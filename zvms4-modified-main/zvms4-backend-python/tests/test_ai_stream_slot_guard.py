"""流式并发槽的归还保证：断开/取消也不能漏槽。

漏一格就是一个永久被占的并发名额：漏满 ``AI_MAX_CONCURRENCY``（默认 8）次之后
所有 AI 请求都 429，直到重启。回归的正是 ``_stream_completion`` 的收尾顺序——
``slot()`` 是手工 ``__aenter__`` 进去的上下文管理器，没有兜底，所以：

* 槽必须在生成器 finally 里**第一个**还：客户端断开时 anyio 会把 CancelledError
  注入这个 finally，之后第一个 await 就再抛一次（``stream_iter.aclose()`` 首当
  其冲），排在它后面的还槽语句永远跑不到；
* 首帧前的取消是 ``BaseException``，``except Exception`` 抓不到，同样必须显式还。

这里不起上游 HTTP：凭据/配额/审计/上游全是内存桩，槽直接看
``routing._get_semaphore()._value`` 是否回到基线。
"""

from __future__ import annotations

import asyncio

import pytest

import settings
from db import backends
from routers import ai_router
from services.ai import credentials, providers, quota, rate_limit, routing

ADMIN = "000000000000000000000001"
SECRET = "sk-unit-test-0123456789abcdef"
ENTRY_OID = "b" * 24

DELTA = b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n'


class FakeAuditRepo:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    async def insert(self, **fields) -> str:
        self.rows.append(fields)
        return "a" * 24


class Recorder:
    """记录调用次数的异步桩。"""

    def __init__(self, result=None) -> None:
        self.calls: list[tuple] = []
        self.result = result

    async def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.result


class Upstream:
    """``providers.chat_completion_stream`` 的最小替身。

    ``frames`` 里放字节或异常对象；``aclose_error`` 模拟「收尾时被取消」——真实
    环境里 anyio 的取消作用域会让 finally 里的第一个 await（就是 aclose）直接
    抛 CancelledError。
    """

    def __init__(self, frames, *, aclose_error: BaseException | None = None) -> None:
        self._frames = list(frames)
        self._aclose_error = aclose_error
        self.aclose_calls = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._frames:
            raise StopAsyncIteration
        item = self._frames.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    async def aclose(self) -> None:
        self.aclose_calls += 1
        if self._aclose_error is not None:
            raise self._aclose_error


def _entry() -> dict:
    return {
        "key": f"shared:{ENTRY_OID}",
        "scope": "shared",
        "owner_oid": credentials.SHARED_OWNER,
        "name": "team",
        "kind": "openai_compat",
        "base_url": "https://team.example.com/v1",
        "models": ["gpt-x"],
        "api_key_cipher": "cipher-team",
        "api_key": None,
        "key_hint": "cdef",
        "enabled": True,
        "oid": ENTRY_OID,
        "created_at": None,
        "updated_at": None,
    }


def _payload() -> dict:
    return {
        "model": "gpt-x",
        "stream": True,
        "messages": [{"role": "user", "content": "hi"}],
    }


@pytest.fixture(autouse=True)
def _isolated_gateway(monkeypatch, quiet_ai_governance):
    credentials._cache.clear()
    routing.reset_state()
    rate_limit.reset_state()
    ai_router.reset_stream_unsupported_cache()
    # 信号量绑 event loop：每个用例一个 asyncio.run，必须从空缓存开始。
    routing._semaphore = None
    routing._semaphore_size = 0

    async def _resolve(entry: dict) -> str:
        return SECRET

    monkeypatch.setattr(settings, "AI_PROVIDERS", {}, raising=False)
    monkeypatch.setattr(credentials, "resolve_api_key", _resolve)
    monkeypatch.setattr(quota, "peek", Recorder((True, 0, 100)))
    monkeypatch.setattr(quota, "consume", Recorder((True, 1, 100)))
    yield
    credentials._cache.clear()
    routing.reset_state()
    rate_limit.reset_state()
    ai_router.reset_stream_unsupported_cache()
    routing._semaphore = None
    routing._semaphore_size = 0


@pytest.fixture
def audit_repo(monkeypatch) -> FakeAuditRepo:
    fake = FakeAuditRepo()
    monkeypatch.setattr(backends, "get_ai_call_logs_repo", lambda session=None: fake)
    return fake


@pytest.fixture
def upstream(monkeypatch):
    """``install(Upstream(...))`` 装上流式上游桩。"""

    def install(fake: Upstream) -> Upstream:
        monkeypatch.setattr(
            providers, "chat_completion_stream", lambda *args, **kwargs: fake
        )
        return fake

    return install


async def _open_stream():
    return await ai_router._stream_completion(
        _payload(),
        "gpt-x",
        [_entry()],
        ADMIN,
        "req-slot",
        ["admin"],
        purpose="chat",
        audit_oid=None,
    )


def _slot_value() -> int:
    return routing._get_semaphore()._value


def test_slot_is_held_while_streaming_and_returned_on_close(upstream, audit_repo):
    """正常路径的基线：流没结束前占着槽，``aclose()`` 之后还回去。"""

    upstream(Upstream([DELTA]))

    async def scenario() -> None:
        baseline = _slot_value()
        response = await _open_stream()
        gen = response.body_iterator

        assert await gen.__anext__() == DELTA
        assert _slot_value() == baseline - 1, "流式期间必须占着一格"

        await gen.aclose()
        assert _slot_value() == baseline, "首帧后断开也必须把槽还回去"

    asyncio.run(scenario())
    assert [row["status"] for row in audit_repo.rows] == ["success"]


def test_slot_is_returned_when_cancellation_hits_the_first_teardown_await(upstream):
    """断开时 finally 的第一个 await 被取消：槽仍要还，且只还一次。

    这是线上泄漏的原始形态（anyio 取消作用域）+ 以前的重释放风险（手工还槽
    分散在多处，容易多还一格）。
    """

    upstream(Upstream([DELTA], aclose_error=asyncio.CancelledError()))

    async def scenario() -> None:
        baseline = _slot_value()
        response = await _open_stream()
        gen = response.body_iterator
        assert await gen.__anext__() == DELTA

        with pytest.raises(asyncio.CancelledError):
            await gen.aclose()

        assert _slot_value() == baseline, "取消把收尾打断了，但槽不能跟着丢"

    asyncio.run(scenario())


def test_slot_is_released_when_a_teardown_step_raises(upstream):
    """收尾的某一步抛普通异常：槽已还，后面的记账步骤继续跑。"""

    upstream(Upstream([DELTA], aclose_error=RuntimeError("upstream gone")))

    async def scenario() -> None:
        baseline = _slot_value()
        response = await _open_stream()
        gen = response.body_iterator
        assert await gen.__anext__() == DELTA

        await gen.aclose()
        assert _slot_value() == baseline

    asyncio.run(scenario())


def test_slot_is_released_when_the_first_frame_fails(upstream, audit_repo):
    """首帧前上游报错：没有生成器兜底，这条出口必须自己还槽。"""

    upstream(
        Upstream([routing.RouteError("AI_UPSTREAM_AUTH", "upstream rejected", 502)])
    )

    async def scenario() -> None:
        baseline = _slot_value()
        response = await _open_stream()

        assert response.status_code == 502
        assert _slot_value() == baseline

    asyncio.run(scenario())
    assert [row["status"] for row in audit_repo.rows] == ["rejected"]


def test_slot_is_released_when_the_first_frame_is_cancelled(upstream):
    """首帧前被取消（BaseException）：``except Exception`` 抓不到，也得还槽。"""

    upstream(Upstream([asyncio.CancelledError()]))

    async def scenario() -> None:
        baseline = _slot_value()

        with pytest.raises(asyncio.CancelledError):
            await _open_stream()

        assert _slot_value() == baseline

    asyncio.run(scenario())
