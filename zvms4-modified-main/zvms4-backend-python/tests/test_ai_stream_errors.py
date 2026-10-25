"""流式对话的流内错误通道。

``StreamingResponse`` 的 200 + ``text/event-stream`` 头一旦发出就改不了状态码，
而上游 4xx/5xx 恰恰是在 ``client.stream()`` 里、**首个 yield 之前**抛的
（``services/ai/providers.py``）——所以这条分支是所有「非流式正常、流式空返回」
的必经之路。以前它只写一条 warning 就结束生成器，客户端拿到「200 + 空 body」，
前端只能猜成「被截断」；现在必须写一帧 ``{"error": {...}}`` + ``[DONE]`` 收尾。

这里钉四件事：

* 首帧前失败 → 一帧 error + 一帧 [DONE]，HTTP 仍是 200；
* 中途失败 → 已发出的正文保留，后面补 error + [DONE]；
* 码不认识 → 回落到 ``AI_UPSTREAM_ERROR``（前端码表里有文案）；
* 正常流 → 原样透传，不掺 error 帧。

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
        async def _stream(payload: dict, entry: dict, api_key: str):
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


def test_failure_before_first_chunk_still_ends_the_stream_cleanly(
    upstream, audit_repo
):
    """上游 4xx/5xx 在首个 yield 前抛：客户端必须拿到 error 帧 + [DONE]。"""
    upstream(
        routing.RouteError("AI_UPSTREAM_REJECTED", "upstream rejected the request", 502)
    )

    response = _post_stream()

    # 头早就发出去了：状态码只能是 200，失败信息在流里。
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    frames = _frames(response.text)
    assert len(frames) == 2, frames
    payload = json.loads(frames[0])
    assert payload["error"]["code"] == "AI_UPSTREAM_REJECTED"
    assert payload["error"]["message"] == (
        ai_router.STREAM_ERROR_MESSAGES["AI_UPSTREAM_REJECTED"]
    )
    assert frames[1] == "[DONE]"
    # 审计照样要记失败，否则「用户说流是空的」时后台看不出原因。
    assert [row["status"] for row in audit_repo.rows] == ["rejected"]
    assert audit_repo.rows[0]["error_code"] == "AI_UPSTREAM_REJECTED"
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


def test_unmapped_exception_falls_back_to_the_generic_code(upstream, audit_repo):
    """码不认识/根本不是 RouteError：回落到前端码表里有的 AI_UPSTREAM_ERROR。"""
    upstream(RuntimeError("boom"))

    response = _post_stream()

    frames = _frames(response.text)
    assert json.loads(frames[0])["error"]["code"] == "AI_UPSTREAM_ERROR"
    assert frames[-1] == "[DONE]"
    assert [row["error_code"] for row in audit_repo.rows] == ["AI_UPSTREAM_ERROR"]


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
