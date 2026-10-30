"""流式的「首字节前回退」与计费估算口径。

契约（这一版冻结的三条）：

* **首帧前**失败可回退：连接错误/首帧超时 → 同一候选改走非流式一次；非流式仍失败
  且错误码可重试 → 换下一候选（复用 ``_blocking_completion``，不另写一套重试）。
* 「上游不支持流式」= HTTP 405，或 400/404/422 且错误体命中 ``stream|unsupported|
  not support``（大小写不敏感）→ 记进进程内负缓存（TTL 3600s），后续同键请求直接
  非流式起步。**超时不写负缓存**（那时没有状态码，判不出来）。
* 200 但 ``Content-Type`` 不是 ``text/event-stream`` → 把响应体当普通 JSON，包成
  一帧 SSE 回给客户端，不报错。

外加一条计费口径：没有 usage 时，完成侧字符数只数解析出的正文
（``delta.content`` / ``delta.reasoning_content``），不数 SSE 原始字节——帧壳不是
回答，按字节估会把额度放大一到两个数量级。

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
from services.ai import usage as ai_usage
from typings.log import inject_log
from util.object_id import get_current_user

ADMIN = "000000000000000000000001"
SECRET = "sk-unit-test-0123456789abcdef"
ENTRY_OID = "b" * 24
SECOND_OID = "c" * 24


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
    def __init__(self, result=None) -> None:
        self.calls: list[tuple] = []
        self.result = result

    async def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.result


def _entry(name: str = "team", oid: str = ENTRY_OID) -> dict:
    return {
        "key": f"shared:{oid}",
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
        "oid": oid,
        "created_at": None,
        "updated_at": None,
    }


def _qualified(entry: dict, model: str = "gpt-x") -> str:
    return credentials.qualified_model_id(entry, model)


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(ai_router.router, prefix="/api/ai")
    identity = lambda: {"id": ADMIN, "per": ["admin"]}  # noqa: E731
    app.dependency_overrides[get_current_user] = identity
    app.dependency_overrides[ai_router.get_ai_client] = identity
    app.dependency_overrides[inject_log] = lambda: StubLog()
    return TestClient(app)


def _post_stream(model: str = "gpt-x"):
    return _client().post(
        "/api/ai/v1/chat/completions",
        json={
            "model": model,
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
    ai_router.reset_stream_unsupported_cache()

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


@pytest.fixture
def audit_repo(monkeypatch) -> FakeAuditRepo:
    fake = FakeAuditRepo()
    monkeypatch.setattr(backends, "get_ai_call_logs_repo", lambda session=None: fake)
    return fake


@pytest.fixture
def catalog(monkeypatch):
    """``catalog(entries)`` 装上凭据目录（返回副本，与真实目录同语义）。"""
    state = {"entries": []}

    async def _catalog(user_oid: str) -> list[dict]:
        return [dict(entry) for entry in state["entries"]]

    monkeypatch.setattr(credentials, "catalog", _catalog)

    def install(entries: list[dict]) -> None:
        state["entries"] = entries

    install([])
    return install


@pytest.fixture
def blocking(monkeypatch):
    """非流式上游桩：``script(*outcomes)`` 按调用顺序决定返回值/异常。"""
    calls: dict[str, list[dict]] = {"payloads": []}
    script: list = []

    async def _chat_completion(payload: dict, entry: dict, api_key: str) -> dict:
        calls["payloads"].append(payload)
        outcome = script.pop(0) if script else _ok_body()
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(providers, "chat_completion", _chat_completion)

    def run(*outcomes) -> dict[str, list[dict]]:
        script.extend(outcomes)
        return calls

    return run


@pytest.fixture
def stream(monkeypatch):
    """流式上游桩。

    ``install(...)`` 的参数是逐段 yield 的内容（异常对象直接抛）。``meta`` 由桩
    自己填：路由层靠它判「不支持流式」和「200 但不是 SSE」，真实实现里由
    ``providers.chat_completion_stream`` 填。
    """
    calls: dict[str, list[dict]] = {"payloads": []}

    def install(
        *items,
        status_code: int = 200,
        content_type: str = "text/event-stream",
        error_body: str = "",
    ):
        async def _stream(payload: dict, entry: dict, api_key: str, *, meta=None):
            calls["payloads"].append(payload)
            if meta is not None:
                meta["status_code"] = status_code
                meta["content_type"] = content_type
                if status_code >= 300:
                    meta["error_body"] = error_body
            if status_code >= 300:
                # 真实 provider 在首个 yield 之前就把非 2xx 翻成 RouteError。
                raise routing.RouteError(
                    "AI_UPSTREAM_REJECTED", "upstream rejected the request", 502
                )
            for item in items:
                if isinstance(item, BaseException):
                    raise item
                yield item

        monkeypatch.setattr(providers, "chat_completion_stream", _stream)
        return calls

    return install


def _ok_body(text: str = "你好", total_tokens: int | None = 3) -> dict:
    body = {
        "id": "ok",
        "choices": [{"message": {"content": text}}],
    }
    if total_tokens is not None:
        body["usage"] = {"total_tokens": total_tokens}
    return body


DELTA = 'data: {{"choices":[{{"delta":{{"content":"{}"}}}}]}}\n\n'


# ---------------------------------------------------------------------------
# 纯函数：不支持流式的判定
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (405, "", True),  # Method Not Allowed 不用看 body
        (405, None, True),
        (400, "stream is not supported", True),
        (400, "Unsupported parameter: stream", True),
        (404, "does not support streaming", True),
        (422, "STREAM option rejected", True),  # 大小写不敏感
        (400, "messages[0].content is required", False),  # 参数错，不是不支持流式
        (404, "model not found", False),
        (422, "", False),
        (500, "stream blew up", False),  # 5xx 一律不当「不支持流式」
        (429, "stream rate limited", False),
        (None, "unsupported", False),  # 超时：没有状态码，判不出来
        ("400", "unsupported", False),  # 状态码必须是 int
        (True, "unsupported", False),
    ],
)
def test_stream_unsupported_decision(status, body, expected) -> None:
    assert ai_router._stream_unsupported(status, body) is expected


def test_stream_unsupported_only_scans_the_head_of_the_body(monkeypatch) -> None:
    """错误体只留前面一段做判定：超长 body 后面出现的字样不算（防回显误判）。"""
    tail = "x" * (ai_router.STREAM_UNSUPPORTED_BODY_MAX + 10) + " unsupported"

    assert ai_router._stream_unsupported(400, tail) is False


# ---------------------------------------------------------------------------
# 进程内负缓存
# ---------------------------------------------------------------------------


def test_negative_cache_expires_after_the_ttl(monkeypatch) -> None:
    now = {"value": 1000.0}
    monkeypatch.setattr(ai_router, "_stream_clock", lambda: now["value"])

    ai_router._mark_stream_unsupported("shared:b", "team/gpt-x")

    assert ai_router._stream_unsupported_cached("shared:b", "team/gpt-x") is True
    now["value"] += ai_router.STREAM_UNSUPPORTED_TTL_SECONDS - 1
    assert ai_router._stream_unsupported_cached("shared:b", "team/gpt-x") is True
    now["value"] += 2
    assert ai_router._stream_unsupported_cached("shared:b", "team/gpt-x") is False


def test_negative_cache_is_keyed_by_credential_and_model() -> None:
    """同一个 key 换模型、或同一个模型换 key，都不能串味。"""
    ai_router._mark_stream_unsupported("shared:b", "team/gpt-x")

    assert ai_router._stream_unsupported_cached("shared:b", "team/other") is False
    assert ai_router._stream_unsupported_cached("shared:c", "team/gpt-x") is False


# ---------------------------------------------------------------------------
# 端到端：不支持流式 → 回退非流式 + 负缓存
# ---------------------------------------------------------------------------


def test_unsupported_stream_falls_back_to_blocking_and_remembers_the_key(
    catalog, stream, blocking, audit_repo
):
    entry = _entry()
    catalog([entry])
    stream(status_code=405, error_body="Method Not Allowed")
    calls = blocking()

    response = _post_stream()

    # 客户端要的是流：回退非流式的结果也包成一帧 SSE（前端解析器只认帧）。
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    frames = _frames(response.text)
    assert len(frames) == 2, frames
    assert json.loads(frames[0])["choices"][0]["message"]["content"] == "你好"
    assert frames[1] == "[DONE]"

    # 回退用的 body 里不能再带 stream：否则上游照样走流式语义。
    assert "stream" not in calls["payloads"][0]
    assert "stream_options" not in calls["payloads"][0]

    # 负缓存：这个 (凭据, 模型) 一小时内不必再试流式。
    assert ai_router._stream_unsupported_cached(entry["key"], _qualified(entry))
    # 两次上游尝试都留了流水：流式那一跳 rejected，非流式那一跳 success。
    assert [(row["provider"], row["status"]) for row in audit_repo.rows] == [
        ("team", "rejected"),
        ("team", "success"),
    ]


def test_negative_cache_hit_starts_with_the_blocking_call(
    catalog, blocking, audit_repo, monkeypatch
):
    """负缓存命中：一次流式调用都不该发生（上游明确说过不支持）。"""
    entry = _entry()
    catalog([entry])
    ai_router._mark_stream_unsupported(entry["key"], _qualified(entry))

    async def _boom(payload, entry, api_key, *, meta=None):
        raise AssertionError("负缓存命中后不得再尝试流式")
        yield b""  # pragma: no cover - 只是为了保持异步生成器形状

    monkeypatch.setattr(providers, "chat_completion_stream", _boom)
    calls = blocking()

    response = _post_stream()

    assert response.status_code == 200
    frames = _frames(response.text)
    assert json.loads(frames[0])["id"] == "ok"
    assert frames[-1] == "[DONE]"
    assert len(calls["payloads"]) == 1
    assert [row["status"] for row in audit_repo.rows] == ["success"]


def test_timeout_before_first_chunk_falls_back_without_caching(
    catalog, stream, blocking
):
    """首帧超时 → 同一候选改走非流式；**不写负缓存**（超时判不出支持性）。"""
    entry = _entry()
    catalog([entry])
    stream(routing.RouteError("AI_TIMEOUT", "upstream timed out", 504))
    blocking()

    response = _post_stream()

    assert response.status_code == 200
    assert _frames(response.text)[-1] == "[DONE]"
    assert ai_router._stream_unsupported_cached(entry["key"], _qualified(entry)) is False


def test_blocking_fallback_that_is_still_retryable_moves_to_the_next_candidate(
    catalog, stream, blocking, audit_repo
):
    """流式不支持 + 非流式也失败（可重试）→ 换下一候选（复用非流式重试语义）。"""
    first = _entry("first", ENTRY_OID)
    second = _entry("second", SECOND_OID)
    catalog([first, second])
    stream(status_code=405, error_body="not supported")
    calls = blocking(
        routing.RouteError("AI_UPSTREAM_ERROR", "upstream failed", 502),
        _ok_body("second 回答"),
    )

    response = _post_stream(model=f"first/gpt-x")

    assert response.status_code == 200
    frames = _frames(response.text)
    assert json.loads(frames[0])["choices"][0]["message"]["content"] == "second 回答"
    assert [payload["model"] for payload in calls["payloads"]] == ["gpt-x", "gpt-x"]
    assert [(row["provider"], row["status"]) for row in audit_repo.rows] == [
        ("first", "rejected"),  # 流式那一跳
        ("first", "error"),  # 非流式回退那一跳
        ("second", "success"),
    ]


# ---------------------------------------------------------------------------
# 200 但不是 SSE
# ---------------------------------------------------------------------------


def test_200_with_a_json_body_is_wrapped_into_one_sse_frame(
    catalog, stream, blocking, audit_repo
):
    """上游把 stream 开关当没看见：整包 JSON 一帧回给客户端，不报错。"""
    entry = _entry()
    catalog([entry])
    body = json.dumps(
        {
            "id": "json-ok",
            "choices": [{"message": {"content": "整包回答"}}],
            "usage": {"prompt_tokens": 7, "completion_tokens": 5, "total_tokens": 12},
        }
    ).encode()
    # 故意切成两段：真上游的 body 也是按 TCP 分片到的。
    stream(body[:40], body[40:], content_type="application/json")
    calls = blocking()

    response = _post_stream()

    assert response.status_code == 200
    frames = _frames(response.text)
    assert len(frames) == 2, frames
    assert json.loads(frames[0])["id"] == "json-ok"
    assert frames[1] == "[DONE]"
    assert calls["payloads"] == []  # 没走非流式：上游本来就回了内容
    # usage 直接来自响应体：token 列落值，units 走实测口径。
    row = audit_repo.rows[0]
    assert row["status"] == "success"
    assert (row["prompt_tokens"], row["total_tokens"]) == (7, 12)
    assert row["units"] == ai_usage.af_units(12, 1.0)


def test_json_body_without_usage_falls_back_to_the_content_estimate(
    catalog, stream, blocking, audit_repo
):
    """200 + JSON 但没 usage：按 message.content 的字符数估算（不是整包字节数）。"""
    entry = _entry()
    catalog([entry])
    stream(json.dumps(_ok_body("四" * 40, total_tokens=None)).encode())

    response = _post_stream()

    assert response.status_code == 200
    row = audit_repo.rows[0]
    assert row["total_tokens"] is None
    assert row["completion_tokens"] is None
    # prompt 侧来自客户端原文，完成侧 = 40 个字符。
    expected = ai_usage.estimate_af(
        ai_usage.prompt_chars(
            {"messages": [{"role": "user", "content": "hi"}]}
        ),
        40,
        1.0,
    )
    assert row["units"] == expected


# ---------------------------------------------------------------------------
# 首帧之后：不换候选、不回退
# ---------------------------------------------------------------------------


def test_failure_after_the_first_frame_does_not_switch_candidates(
    catalog, stream, blocking, audit_repo
):
    """半句 A 半句 B 比报错更难查：首帧之后只写 error 帧，绝不换候选。"""
    first = _entry("first", ENTRY_OID)
    second = _entry("second", SECOND_OID)
    catalog([first, second])
    stream(
        DELTA.format("半句").encode(),
        routing.RouteError("AI_UPSTREAM_ERROR", "upstream blew up", 502),
    )
    calls = blocking()

    response = _post_stream(model="first/gpt-x")

    assert response.status_code == 200
    frames = _frames(response.text)
    assert frames[0] == DELTA.format("半句").strip()[len("data: ") :]
    assert json.loads(frames[-2])["error"]["code"] == "AI_UPSTREAM_ERROR"
    assert frames[-1] == "[DONE]"
    assert calls["payloads"] == []  # 没回退非流式
    assert [row["provider"] for row in audit_repo.rows] == ["first"]


# ---------------------------------------------------------------------------
# 计费估算口径：只数正文
# ---------------------------------------------------------------------------


def test_estimate_counts_only_content_not_sse_frame_shells(
    catalog, stream, blocking, audit_repo
):
    """50 帧、每帧一个字：估算应按 50 个字符算，而不是按 ~6KB 帧壳算。"""
    entry = _entry()
    catalog([entry])
    fat = (
        'data: {"id":"chunk","object":"chat.completion.chunk","created":1,'
        '"model":"gpt-x","system_fingerprint":"fp","choices":[{"index":0,'
        '"delta":{"role":"assistant","content":"__CONTENT__"},"finish_reason":null}]}\n\n'
    )
    pieces = [fat.replace("__CONTENT__", "字").encode() for _ in range(50)]
    stream(*pieces, b"data: [DONE]\n\n")
    blocking()

    response = _post_stream()

    assert response.status_code == 200
    raw_bytes = sum(len(piece) for piece in pieces)
    # 口径对照：按原始字节估会是多少（改造前的算法）。
    byte_based = ai_usage.estimate_af(
        ai_usage.prompt_chars({"messages": [{"role": "user", "content": "hi"}]}),
        raw_bytes,
        1.0,
    )
    content_based = ai_usage.estimate_af(
        ai_usage.prompt_chars({"messages": [{"role": "user", "content": "hi"}]}),
        50,
        1.0,
    )

    row = audit_repo.rows[0]
    assert row["units"] == content_based
    assert row["units"] < byte_based, (row["units"], byte_based)
    assert byte_based >= 3 * content_based
    # 没 usage：token 列留 NULL（不变式「NULL ⟺ 估算」）。
    assert row["total_tokens"] is None


def test_usage_frame_still_wins_over_the_estimate(catalog, stream, blocking, audit_repo):
    """上游报了 usage：估算数字一律让位（实测与估算不能混在同一列）。"""
    entry = _entry()
    catalog([entry])
    stream(
        DELTA.format("很长的正文" * 100).encode(),
        'data: {"choices":[],"usage":{"prompt_tokens":9,"completion_tokens":4,"total_tokens":13}}\n\n'.encode(),
        b"data: [DONE]\n\n",
    )

    response = _post_stream()

    assert response.status_code == 200
    row = audit_repo.rows[0]
    assert row["total_tokens"] == 13
    assert row["units"] == ai_usage.af_units(13, 1.0)
