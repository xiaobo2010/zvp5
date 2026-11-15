"""流式对话的流内错误通道。

``StreamingResponse`` 的 200 + ``text/event-stream`` 头一旦发出就改不了状态码，
所以**首帧之后**的失败只能写进流里。首帧**之前**的失败不在此列：路由层会先探首帧
（见 ``_stream_completion``），那时还能回退非流式，或者给一个干净的 4xx/5xx——
那一半的行为由 ``tests/test_ai_stream_fallback.py`` 钉住。

这里钉四件事（都是首帧之后的语义）：

* 中途失败 → 已发出的正文保留，后面补 error + [DONE]；
* 码不认识 → 回落到 ``AI_UPSTREAM_ERROR``（前端码表里有文案）；
* 正常流 → 原样透传，不掺 error 帧；
* 首帧前就不可重试的失败 → 干净的 502（而不是 200 + 空流）。

上游、凭据、配额、审计全部内存桩：不连库、不出网、不花真实额度。
"""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import settings
from db import backends
from routers import ai_router
from services.ai import credentials, providers, quota, rate_limit, routing
from typings.log import inject_log
from util.object_id import get_current_user

ADMIN = "000000000000000000000001"
SECRET = "sk-unit-test-0123456789abcdef"
ENTRY_OID = "b" * 24


class StubLog:
    def with_text(self, *args, **kwargs):
        return self

    async def insert_log(self):
        return None


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


def _entry(name: str = "team") -> dict:
    return {
        "key": f"shared:{ENTRY_OID}",
        "scope": "shared",
        "owner_oid": credentials.SHARED_OWNER,
        "name": name,
        "kind": "openai_compat",
        "base_url": f"https://{name}.example.com/v1",
        "models": ["gpt-x"],
        "api_key_cipher": "cipher-" + name,
        "api_key": None,
        "key_hint": "cdef",
        "enabled": True,
        "oid": ENTRY_OID,
        "created_at": None,
        "updated_at": None,
    }


def _client() -> TestClient:
    """只挂 ai_router 的迷你 app（与 test_ai_router_contract 同一手法）。"""
    app = FastAPI()
    app.include_router(ai_router.router, prefix="/api/ai")
    identity = lambda: {"id": ADMIN, "per": ["admin"]}  # noqa: E731
    app.dependency_overrides[get_current_user] = identity
    app.dependency_overrides[ai_router.get_ai_client] = identity
    app.dependency_overrides[inject_log] = lambda: StubLog()
    return TestClient(app)


def _post_stream():
    return _client().post(
        "/api/ai/v1/chat/completions",
        json={
            "model": "gpt-x",
            "stream": True,
            "messages": [{"role": "user", "content": "hi"}],
        },
    )


def _frames(body: str) -> list[str]:
    """把 SSE body 拆成 data 载荷列表（顺序保留，含 [DONE]）。"""
    frames: list[str] = []
    for block in body.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        assert block.startswith("data: "), block
        frames.append(block[len("data: ") :])
    return frames


@pytest.fixture(autouse=True)
def _isolated_gateway(monkeypatch, quiet_ai_governance):
    credentials._cache.clear()
    routing.reset_state()
    rate_limit.reset_state()

    async def _catalog(user_oid: str) -> list[dict]:
        return [_entry()]

    async def _resolve(entry: dict) -> str:
        return SECRET

    monkeypatch.setattr(settings, "AI_PROVIDERS", {}, raising=False)
    monkeypatch.setattr(credentials, "catalog", _catalog)
    monkeypatch.setattr(credentials, "resolve_api_key", _resolve)
    monkeypatch.setattr(quota, "peek", Recorder((True, 0, 100)))
    monkeypatch.setattr(quota, "consume", Recorder((True, 1, 100)))
    yield
    credentials._cache.clear()
    routing.reset_state()
    rate_limit.reset_state()


@pytest.fixture
def audit_repo(monkeypatch) -> FakeAuditRepo:
    fake = FakeAuditRepo()
    monkeypatch.setattr(backends, "get_ai_call_logs_repo", lambda session=None: fake)
    return fake


@pytest.fixture
def upstream(monkeypatch):
    """上游流桩：``script`` 里 bytes 逐段 yield，异常对象直接抛。"""

    def install(*items):
        async def _stream(payload: dict, entry: dict, api_key: str, *, meta=None):
            for item in items:
                if isinstance(item, BaseException):
                    raise item
                yield item

        monkeypatch.setattr(providers, "chat_completion_stream", _stream)

    return install


DELTA_JSON = 'data: {{"choices":[{{"delta":{{"content":"{}"}}}}]}}\n\n'


# ---------------------------------------------------------------------------
# 流内错误帧
# ---------------------------------------------------------------------------


def test_non_retryable_failure_before_first_chunk_is_a_clean_error(
    upstream, audit_repo
):
    """首帧前、错误码不可重试：响应头还没发，直接给 502，不演「200 + 空流」。"""
    upstream(
        routing.RouteError(
            "AI_UPSTREAM_AUTH", "upstream rejected the credentials", 502
        )
    )

    response = _post_stream()

    assert response.status_code == 502
    assert not response.headers["content-type"].startswith("text/event-stream")
    assert response.json()["error"]["code"] == "AI_UPSTREAM_AUTH"
    # 审计照样要记失败，否则「用户说流是空的」时后台看不出原因。
    assert [row["status"] for row in audit_repo.rows] == ["rejected"]
    assert audit_repo.rows[0]["error_code"] == "AI_UPSTREAM_AUTH"
    assert response.headers["X-AI-Request-Id"] == audit_repo.rows[0]["request_id"]


def test_failure_after_partial_output_keeps_the_text_and_appends_the_error(
    upstream, audit_repo
):
    """已经吐了一部分：正文保留，error 帧跟在后面收尾。"""
    first, second = DELTA_JSON.format("你"), DELTA_JSON.format("好")
    upstream(
        first.encode(),
        second.encode(),
        routing.RouteError("AI_TIMEOUT", "upstream timed out", 504),
    )

    response = _post_stream()

    assert response.status_code == 200
    frames = _frames(response.text)
    assert frames[:2] == [first[len("data: ") :].strip(), second[len("data: ") :].strip()]
    payload = json.loads(frames[2])
    assert payload["error"]["code"] == "AI_TIMEOUT"
    assert frames[3] == "[DONE]"
    assert [row["status"] for row in audit_repo.rows] == ["timeout"]


def test_unmapped_exception_before_first_chunk_falls_back_to_blocking(
    upstream, audit_repo, monkeypatch
):
    """码都不认识的异常落在首帧前 → 回退非流式，客户端拿到的仍是一条完整 SSE。"""

    async def _blocking(payload: dict, entry: dict, api_key: str) -> dict:
        assert "stream" not in payload
        return {
            "id": "ok",
            "choices": [{"message": {"content": "你好"}}],
            "usage": {"total_tokens": 3},
        }

    monkeypatch.setattr(providers, "chat_completion", _blocking)
    upstream(RuntimeError("boom"))

    response = _post_stream()

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    frames = _frames(response.text)
    assert len(frames) == 2, frames
    assert json.loads(frames[0])["choices"][0]["message"]["content"] == "你好"
    assert frames[1] == "[DONE]"
    assert [row["status"] for row in audit_repo.rows][-1] == "success"


def test_healthy_stream_is_passed_through_untouched(upstream, audit_repo):
    """正常流不许被改动：字节级透传，不掺 error 帧。"""
    chunks = [DELTA_JSON.format("你").encode(), b"data: [DONE]\n\n"]
    upstream(*chunks)

    response = _post_stream()

    assert response.status_code == 200
    assert response.text == b"".join(chunks).decode()
    assert '"error"' not in response.text
    assert [row["status"] for row in audit_repo.rows] == ["success"]


# ---------------------------------------------------------------------------
# providers：不透明压缩字节不发出去
# ---------------------------------------------------------------------------


class _FakeStreamResponse:
    status_code = 200

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info) -> bool:
        return False

    async def aiter_bytes(self):
        # 真实 httpx 在这个方法里按 Content-Encoding 解压；上游就算硬压，
        # 我们转发出去的也必须是明文 SSE。
        yield b'data: {"ok": true}\n\n'

    async def aiter_raw(self):
        raise AssertionError("必须用 aiter_bytes()：aiter_raw 会透传 gzip 字节")


class _FakeClient:
    def __init__(self) -> None:
        self.requests: list[dict] = []

    def stream(self, method: str, url: str, *, json=None, headers=None, timeout=None):
        self.requests.append({"url": url, "headers": headers})
        return _FakeStreamResponse()


@pytest.mark.asyncio
async def test_stream_requests_identity_encoding_and_decodes_the_body(monkeypatch):
    client = _FakeClient()
    monkeypatch.setattr(providers, "_client_instance", lambda: client)

    chunks = [
        chunk
        async for chunk in providers.chat_completion_stream(
            {"stream": True}, _entry(), SECRET
        )
    ]

    assert chunks == [b'data: {"ok": true}\n\n']
    assert client.requests[0]["headers"]["Accept-Encoding"] == "identity"


class _MetaStreamResponse:
    """带响应头的流桩：``meta`` 回填的就是这两个值。"""

    def __init__(self, status_code: int, content_type: str, body: bytes = b"") -> None:
        self.status_code = status_code
        self.headers = {"content-type": content_type}
        self._body = body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info) -> bool:
        return False

    async def aread(self) -> bytes:
        return self._body

    async def aiter_bytes(self):
        if self._body:
            yield self._body


class _MetaClient:
    def __init__(self, response) -> None:
        self.response = response

    def stream(self, method: str, url: str, *, json=None, headers=None, timeout=None):
        return self.response


@pytest.mark.asyncio
async def test_stream_meta_carries_status_and_content_type(monkeypatch):
    """路由层靠 ``meta`` 判「不支持流式」与「200 但不是 SSE」。"""
    response = _MetaStreamResponse(200, "application/json", b'{"ok": true}')
    monkeypatch.setattr(providers, "_client_instance", lambda: _MetaClient(response))
    meta: dict = {}

    chunks = [
        chunk
        async for chunk in providers.chat_completion_stream(
            {"stream": True}, _entry(), SECRET, meta=meta
        )
    ]

    assert chunks == [b'{"ok": true}']
    assert meta["status_code"] == 200
    assert meta["content_type"] == "application/json"


@pytest.mark.asyncio
async def test_stream_meta_carries_a_truncated_error_body(monkeypatch):
    """非 2xx：错误体截断后交给路由层判定（不进日志、不进响应）。"""
    long_body = b"stream is not supported " + b"x" * (providers.STREAM_META_BODY_LIMIT * 2)
    response = _MetaStreamResponse(405, "text/plain", long_body)
    monkeypatch.setattr(providers, "_client_instance", lambda: _MetaClient(response))
    meta: dict = {}

    with pytest.raises(routing.RouteError):
        async for _ in providers.chat_completion_stream(
            {"stream": True}, _entry(), SECRET, meta=meta
        ):
            pass  # pragma: no cover - 非 2xx 不会 yield

    assert meta["status_code"] == 405
    assert len(meta["error_body"]) == providers.STREAM_META_BODY_LIMIT
    assert meta["error_body"].startswith("stream is not supported")
