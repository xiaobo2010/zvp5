"""AI_CALL_LOGS 的可审计粒度：token 列、attempt_no、purpose、拒绝流水、统计。

这一层钉的是「一行流水能不能独立回答四个问题」：**谁花了多少**（token / units
是否同源）、**第几次尝试**（多 provider 重试 / 多引擎回退）、**哪个场景在用**
（purpose）、**被拒的算不算**（拒绝路径也要落行）。

核心不变式（``services/ai/usage.py`` 的模块 docstring 也写着）：

    token 列全 NULL  ⟺  这一行的 units 是估算值

上游、凭据、配额、审计全部内存桩：不连库、不出网、不花真实额度。
"""

from __future__ import annotations

import json
from datetime import datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import settings
from db import backends
from routers import ai_router
from services.ai import config as ai_config
from services.ai import credentials, providers, quota, rate_limit, routing
from typings.log import inject_log
from util.object_id import get_current_user

ADMIN = "000000000000000000000001"
USER = "000000000000000000000003"
SECRET = "sk-unit-test-0123456789abcdef"


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


def _entry(name: str = "team", oid: str = "b" * 24, scope: str = "shared") -> dict:
    return {
        "key": f"{scope}:{oid}",
        "scope": scope,
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


def _client(user: dict | None = None) -> TestClient:
    app = FastAPI()
    app.include_router(ai_router.router, prefix="/api/ai")
    identity = lambda: user or {"id": ADMIN, "per": ["admin"]}  # noqa: E731
    app.dependency_overrides[get_current_user] = identity
    app.dependency_overrides[ai_router.get_ai_client] = identity
    app.dependency_overrides[inject_log] = lambda: StubLog()
    return TestClient(app)


@pytest.fixture(autouse=True)
def _isolated_gateway(monkeypatch, quiet_ai_governance):
    credentials._cache.clear()
    routing.reset_state()
    rate_limit.reset_state()
    routing._semaphore = None
    routing._semaphore_size = 0
    monkeypatch.setattr(settings, "AI_PROVIDERS", {}, raising=False)
    monkeypatch.setattr(ai_config, "is_enabled", lambda: True)
    yield
    credentials._cache.clear()
    routing.reset_state()
    rate_limit.reset_state()
    routing._semaphore = None
    routing._semaphore_size = 0


@pytest.fixture
def audit_repo(monkeypatch) -> FakeAuditRepo:
    fake = FakeAuditRepo()
    monkeypatch.setattr(backends, "get_ai_call_logs_repo", lambda session=None: fake)
    return fake


class CatalogStub:
    """``credentials.catalog`` 的内存替身：``set(...)`` 换掉整个目录。"""

    def __init__(self) -> None:
        self.entries: list[dict] = [_entry()]

    def set(self, entries: list[dict]) -> None:
        self.entries = entries

    async def __call__(self, user_oid: str) -> list[dict]:
        return [dict(entry) for entry in self.entries]


@pytest.fixture
def catalog(monkeypatch) -> CatalogStub:
    stub = CatalogStub()
    monkeypatch.setattr(credentials, "catalog", stub)
    return stub


@pytest.fixture
def credentials_ok(monkeypatch):
    async def _resolve(entry: dict) -> str:
        return SECRET

    monkeypatch.setattr(credentials, "resolve_api_key", _resolve)


@pytest.fixture
def quotas(monkeypatch):
    state = {"peek": Recorder((True, 0, 100)), "consume": Recorder((True, 1, 100))}
    monkeypatch.setattr(quota, "peek", state["peek"])
    monkeypatch.setattr(quota, "consume", state["consume"])
    return state


@pytest.fixture
def upstream(monkeypatch):
    calls: list[dict] = []
    script: list = []

    async def _chat_completion(payload: dict, entry: dict, api_key: str) -> dict:
        calls.append({"entry": entry, "payload": payload})
        outcome = script.pop(0) if script else {"id": "ok"}
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(providers, "chat_completion", _chat_completion)

    def run(*outcomes) -> list[dict]:
        script.extend(outcomes)
        return calls

    return run


@pytest.fixture
def stream_upstream(monkeypatch):
    def install(*items):
        async def _stream(payload: dict, entry: dict, api_key: str):
            for item in items:
                if isinstance(item, BaseException):
                    raise item
                yield item

        monkeypatch.setattr(providers, "chat_completion_stream", _stream)

    return install


def _chat(**extra):
    body = {"model": "gpt-x", "messages": [{"role": "user", "content": "hi"}]}
    body.update(extra)
    return body


def _post(body: dict | None = None, headers: dict | None = None, user=None):
    return _client(user).post(
        "/api/ai/v1/chat/completions",
        json=body if body is not None else _chat(),
        headers=headers or {},
    )


def _post_stream(headers: dict | None = None, body: dict | None = None):
    return _client().post(
        "/api/ai/v1/chat/completions",
        json=body or _chat(stream=True),
        headers=headers or {},
    )


# ---------------------------------------------------------------------------
# 非流式：usage → token 列
# ---------------------------------------------------------------------------


def test_non_stream_usage_lands_in_the_three_token_columns(
    catalog, credentials_ok, quotas, upstream, audit_repo
):
    upstream(
        {
            "id": "ok",
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 20,
                "total_tokens": 120,
            },
        }
    )

    response = _post()

    assert response.status_code == 200
    (row,) = audit_repo.rows
    assert (row["prompt_tokens"], row["completion_tokens"], row["total_tokens"]) == (
        100,
        20,
        120,
    )
    # units 与 token 同源：120 token → 1 单位（每 1000 一个，至少 1）。
    assert row["units"] == 1


def test_non_stream_without_usage_leaves_tokens_null_and_units_estimated(
    catalog, credentials_ok, quotas, upstream, audit_repo
):
    """不变式的另一半：没有 usage ⇒ token 列 NULL，units 是估算值（不是 0）。"""
    upstream({"id": "ok"})

    _post()

    (row,) = audit_repo.rows
    assert row["prompt_tokens"] is None
    assert row["completion_tokens"] is None
    assert row["total_tokens"] is None
    assert row["units"] == 1


def test_non_stream_partial_usage_keeps_the_missing_keys_null(
    catalog, credentials_ok, quotas, upstream, audit_repo
):
    upstream({"usage": {"total_tokens": 30}})

    _post()

    (row,) = audit_repo.rows
    assert row["prompt_tokens"] is None
    assert row["completion_tokens"] is None
    assert row["total_tokens"] == 30


# ---------------------------------------------------------------------------
# attempt_no：多 provider 重试的每一行都标序号
# ---------------------------------------------------------------------------


def test_retry_chain_numbers_each_attempt_from_one(
    catalog, credentials_ok, quotas, upstream, audit_repo
):
    """第一次可重试失败、第二次成功 ⇒ 两行，attempt_no = 1 / 2，同一个 request_id。"""
    catalog.set([_entry(), _entry("backup", "c" * 24)])
    upstream(
        routing.RouteError("AI_UPSTREAM_ERROR", "upstream failed", 502),
        {"usage": {"total_tokens": 10}},
    )

    response = _post()

    assert response.status_code == 200
    assert [row["attempt_no"] for row in audit_repo.rows] == [1, 2]
    assert [row["provider"] for row in audit_repo.rows] == ["team", "backup"]
    assert {row["request_id"] for row in audit_repo.rows} == {
        response.headers["X-AI-Request-Id"]
    }


def test_non_retryable_failure_after_a_retry_keeps_its_attempt_number(
    catalog, credentials_ok, quotas, upstream, audit_repo
):
    """重试之后才被拒的那一行也要带序号（否则链上看不出它排第几）。"""
    catalog.set([_entry(), _entry("backup", "c" * 24)])
    upstream(
        routing.RouteError("AI_UPSTREAM_ERROR", "upstream failed", 502),
        routing.RouteError("AI_UPSTREAM_REJECTED", "bad request", 502),
    )

    response = _post()

    assert response.status_code == 502
    assert [row["attempt_no"] for row in audit_repo.rows] == [1, 2]
    assert [row["status"] for row in audit_repo.rows] == ["error", "rejected"]


# ---------------------------------------------------------------------------
# purpose：X-AI-Scene
# ---------------------------------------------------------------------------


def test_purpose_comes_from_the_scene_header(
    catalog, credentials_ok, quotas, upstream, audit_repo
):
    upstream({})

    _post(headers={"X-AI-Scene": "markdown-assistant"})

    assert audit_repo.rows[0]["purpose"] == "markdown-assistant"


def test_purpose_is_null_without_the_header(
    catalog, credentials_ok, quotas, upstream, audit_repo
):
    """第三方 SDK 不会带这个头：NULL 是「未知来源」，不是「chat」。"""
    upstream({})

    _post()

    assert audit_repo.rows[0]["purpose"] is None


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("Chat", "chat"),
        ("  markdown-assistant  ", "markdown-assistant"),
        ("markdown assistant!!", "markdown-assistant"),
        ("a" * 40, "a" * 24),  # 截到 VARCHAR2(24)，否则 INSERT 会 ORA-12899
        ("!!!", None),  # 清洗后为空 = 未知
    ],
)
def test_scene_header_is_normalised_to_fit_the_column(
    catalog, credentials_ok, quotas, upstream, audit_repo, header, expected
):
    upstream({})

    _post(headers={"X-AI-Scene": header})

    assert audit_repo.rows[0]["purpose"] == expected


# ---------------------------------------------------------------------------
# 拒绝路径：每一道闸门都留一行 rejected
# ---------------------------------------------------------------------------


def test_forbidden_role_leaves_a_rejected_row(catalog, audit_repo, quotas):
    response = _post(user={"id": USER, "per": ["student"]})

    assert response.status_code == 403
    (row,) = audit_repo.rows
    assert (row["status"], row["error_code"], row["http_status"]) == (
        "rejected",
        "AI_FORBIDDEN",
        403,
    )
    assert row["units"] == 0
    assert row["attempt_no"] is None


def test_disabled_gateway_leaves_a_rejected_row(
    catalog, audit_repo, quotas, monkeypatch
):
    monkeypatch.setattr(ai_config, "is_enabled", lambda: False)

    response = _post()

    assert response.status_code == 503
    (row,) = audit_repo.rows
    assert (row["status"], row["error_code"], row["http_status"]) == (
        "rejected",
        "AI_DISABLED",
        503,
    )


def test_unknown_model_leaves_a_rejected_row(catalog, audit_repo, quotas):
    response = _post(_chat(model="nope"))

    assert response.status_code == 404
    (row,) = audit_repo.rows
    assert (row["error_code"], row["model"], row["http_status"]) == (
        "model_not_found",
        "nope",
        404,
    )


def test_exhausted_quota_leaves_a_rejected_row(
    catalog, credentials_ok, upstream, audit_repo, monkeypatch
):
    peek = Recorder((False, 200000, 200000))
    monkeypatch.setattr(quota, "peek", peek)
    monkeypatch.setattr(quota, "consume", Recorder((True, 1, 100)))

    response = _post()

    assert response.status_code == 429
    (row,) = audit_repo.rows
    assert (row["status"], row["error_code"], row["http_status"]) == (
        "rejected",
        "insufficient_quota",
        429,
    )
    # 拒绝是「我们挡的」，不是上游限流：busy 只留给上游 429。
    assert row["status"] != "busy"


def test_local_rate_limit_leaves_a_rejected_row(
    catalog, credentials_ok, upstream, audit_repo, quotas, monkeypatch
):
    monkeypatch.setattr(ai_config, "rate_limit_per_minute", lambda kind: 1)
    upstream({}, {})

    first = _post()
    second = _post()

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.json()["error"]["code"] == "AI_RATE_LIMITED"
    assert second.headers["Retry-After"]
    assert [(row["status"], row["error_code"], row["http_status"]) for row in audit_repo.rows] == [
        ("success", None, 200),
        ("rejected", "AI_RATE_LIMITED", 429),
    ]


def test_rejections_are_audited_with_the_scene(
    catalog, audit_repo, quotas
):
    """拒绝行也带 purpose：否则「哪个页面一直在被拒」查不出来。"""
    _post(_chat(model="nope"), headers={"X-AI-Scene": "chat"})

    assert audit_repo.rows[0]["purpose"] == "chat"


# ---------------------------------------------------------------------------
# 流式：usage 帧 / first_token_ms / 不变式
# ---------------------------------------------------------------------------

DELTA = b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n'
USAGE = (
    b'data: {"choices":[],"usage":'
    b'{"prompt_tokens":30,"completion_tokens":6,"total_tokens":36}}\n\n'
)
DONE = b"data: [DONE]\n\n"


def test_stream_usage_frame_fills_tokens_and_first_token_ms(
    catalog, credentials_ok, quotas, stream_upstream, audit_repo
):
    stream_upstream(DELTA, USAGE, DONE)

    response = _post_stream()

    assert response.status_code == 200
    (row,) = audit_repo.rows
    assert (row["prompt_tokens"], row["completion_tokens"], row["total_tokens"]) == (
        30,
        6,
        36,
    )
    assert row["units"] == 1
    assert row["first_token_ms"] is not None and row["first_token_ms"] >= 0
    assert row["attempt_no"] == 1


def test_stream_without_usage_keeps_tokens_null_and_estimates_units(
    catalog, credentials_ok, quotas, stream_upstream, audit_repo
):
    """不变式：没有 usage ⇒ token 全 NULL，units 走字符估算（至少 1）。"""
    stream_upstream(DELTA, b'data: {"choices":[{"delta":{"content":"there"}}]}\n\n', DONE)

    _post_stream()

    (row,) = audit_repo.rows
    assert row["prompt_tokens"] is None
    assert row["completion_tokens"] is None
    assert row["total_tokens"] is None
    assert row["units"] >= 1
    assert row["first_token_ms"] is not None


def test_stream_usage_frame_split_across_chunks_is_still_parsed(
    catalog, credentials_ok, quotas, stream_upstream, audit_repo
):
    """真实 SSE 会被 TCP 切片：usage 帧分两段到达也必须读出来。"""
    stream_upstream(DELTA, USAGE[:25], USAGE[25:], DONE)

    _post_stream()

    assert audit_repo.rows[0]["total_tokens"] == 36


def test_stream_failure_keeps_tokens_null_and_records_the_error(
    catalog, credentials_ok, quotas, stream_upstream, audit_repo
):
    """流中途失败：没有 usage，不该编一个 token 数出来。"""
    stream_upstream(DELTA, routing.RouteError("AI_TIMEOUT", "timed out", 504))

    response = _post_stream()

    assert response.status_code == 200
    (row,) = audit_repo.rows
    assert row["status"] == "timeout"
    assert row["total_tokens"] is None
    assert row["first_token_ms"] is not None


def test_stream_purpose_is_recorded(
    catalog, credentials_ok, quotas, stream_upstream, audit_repo
):
    stream_upstream(DELTA, DONE)

    _post_stream(headers={"X-AI-Scene": "markdown-assistant"})

    assert audit_repo.rows[0]["purpose"] == "markdown-assistant"


def test_stream_request_body_is_left_alone_for_unknown_hosts() -> None:
    """非白名单 host：请求体逐字节不变（宁缺勿坏）。"""
    body = providers.stream_request_body(
        {"model": "m", "stream": True}, _entry()
    )

    assert "stream_options" not in body


def test_stream_request_body_opts_in_for_allowlisted_hosts() -> None:
    """白名单 host 才带 ``stream_options.include_usage``。"""
    entry = _entry()
    entry["base_url"] = "https://api.openai.com/v1"

    body = providers.stream_request_body({"model": "m", "stream": True}, entry)

    assert body["stream_options"] == {"include_usage": True}


def test_stream_request_body_keeps_client_stream_options(monkeypatch) -> None:
    """客户端自己传的 ``stream_options`` 里我们不认识的键原样保留。"""
    monkeypatch.setattr(ai_config, "stream_usage_hosts", lambda: frozenset({"*"}))
    entry = _entry()

    body = providers.stream_request_body(
        {"stream": True, "stream_options": {"foo": 1, "include_usage": False}}, entry
    )

    assert body["stream_options"] == {"foo": 1, "include_usage": True}


def test_stream_request_body_does_not_mutate_the_caller_payload(monkeypatch) -> None:
    """路由层还要用原始 payload 估算字符数，不能被就地改。"""
    monkeypatch.setattr(ai_config, "stream_usage_hosts", lambda: frozenset({"*"}))
    payload = {"stream": True, "messages": []}

    providers.stream_request_body(payload, _entry())

    assert payload == {"stream": True, "messages": []}


def test_stream_request_body_settings_extension(monkeypatch) -> None:
    """自建网关确认支持时，用 settings 追加自己的 host。"""
    monkeypatch.setattr(
        settings, "AI_STREAM_USAGE_HOSTS", ["vllm.internal"], raising=False
    )
    entry = _entry()
    entry["base_url"] = "https://vllm.internal/v1"

    assert providers.wants_stream_usage(entry) is True


def test_wants_stream_usage_rejects_unparsable_base_url() -> None:
    for base in ("", None, "not a url", "https://"):
        assert providers.wants_stream_usage({"base_url": base}) is False


def test_stream_options_never_reach_a_provider_that_did_not_opt_in(
    catalog, credentials_ok, quotas, audit_repo, monkeypatch
):
    """端到端：白名单外的上游收到的请求体里没有 ``stream_options``。"""
    captured: list[dict] = []

    async def _stream(payload: dict, entry: dict, api_key: str):
        captured.append(providers.stream_request_body(payload, entry))
        yield DELTA

    monkeypatch.setattr(providers, "chat_completion_stream", _stream)

    _post_stream()

    assert "stream_options" not in captured[0]
    assert audit_repo.rows[0]["total_tokens"] is None


# ---------------------------------------------------------------------------
# 查询层：新过滤参数
# ---------------------------------------------------------------------------


class FakeQueryRepo:
    def __init__(self) -> None:
        self.page_calls: list[dict] = []
        self.count_calls: list[dict] = []
        self.stats_calls: list[dict] = []

    async def list_page(self, **kwargs):
        self.page_calls.append(kwargs)
        return []

    async def total_count(self, **kwargs):
        self.count_calls.append(kwargs)
        return 0

    async def stats(self, **kwargs):
        self.stats_calls.append(kwargs)
        return {
            "daily": [],
            "by_provider": [],
            "by_model": [],
            "by_purpose": [],
        }


@pytest.fixture
def query_repo(monkeypatch) -> FakeQueryRepo:
    fake = FakeQueryRepo()
    monkeypatch.setattr(backends, "get_ai_call_logs_repo", lambda session=None: fake)
    return fake


@pytest.fixture
def users(monkeypatch):
    class _Users:
        async def get_names(self, oids: list[str]) -> dict[str, str]:
            return {}

    monkeypatch.setattr(backends, "get_users_repo", lambda session=None: _Users())


def test_logs_filters_are_passed_through(query_repo, users):
    _client().get(
        "/api/ai/logs",
        params={
            "request_id": "req-1",
            "provider": "team",
            "model": "gpt-x",
            "purpose": "chat",
        },
    )

    sent = query_repo.page_calls[0]
    assert sent["request_id"] == "req-1"
    assert sent["provider"] == "team"
    assert sent["model"] == "gpt-x"
    assert sent["purpose"] == "chat"
    # 总数用同一组条件：两处不一致会让「页内条数」与「总数」永远对不上。
    assert query_repo.count_calls[0]["request_id"] == "req-1"


def test_logs_blank_filters_mean_no_filter(query_repo, users):
    _client().get(
        "/api/ai/logs",
        params={"request_id": "", "provider": "", "model": "", "purpose": ""},
    )

    sent = query_repo.page_calls[0]
    assert sent["request_id"] is None
    assert sent["provider"] is None
    assert sent["model"] is None
    assert sent["purpose"] is None


# ---------------------------------------------------------------------------
# /api/ai/logs/stats
# ---------------------------------------------------------------------------


def test_stats_is_admin_only(query_repo):
    response = _client({"id": USER, "per": []}).get("/api/ai/logs/stats")

    assert response.status_code == 403
    assert query_repo.stats_calls == []


def test_stats_defaults_to_seven_days_starting_at_local_midnight(query_repo, users):
    body = _client().get("/api/ai/logs/stats").json()

    assert body["data"]["days"] == 7
    since = query_repo.stats_calls[0]["since"]
    assert since.hour == 0 and since.minute == 0 and since.second == 0
    # 窗口是「今天 + 前 6 天」，共 7 个自然日。
    expected_day = datetime.now().replace(
        hour=0, minute=0, second=0, microsecond=0
    ).date()
    assert (expected_day - since.date()).days == 6
    assert body["data"]["since"].startswith(str(since.date()))


def test_stats_days_is_bounded_by_the_retention_window(query_repo):
    assert _client().get("/api/ai/logs/stats", params={"days": 0}).status_code == 422
    assert _client().get("/api/ai/logs/stats", params={"days": 91}).status_code == 422
    assert (
        _client().get("/api/ai/logs/stats", params={"days": 90}).status_code == 200
    )


def test_stats_returns_the_four_sections(query_repo, users, monkeypatch):
    async def _stats(since, until=None):
        return {
            "daily": [
                {
                    "date": "2026-10-20",
                    "calls": 3,
                    "errors": 1,
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "total_tokens": 15,
                    "avg_latency_ms": 120,
                }
            ],
            "by_provider": [
                {"provider": "team", "calls": 3, "errors": 1, "units": 3, "total_tokens": 15}
            ],
            "by_model": [
                {"model": "gpt-x", "calls": 3, "errors": 1, "units": 3, "total_tokens": 15}
            ],
            "by_purpose": [
                {"purpose": "chat", "calls": 2, "errors": 0, "units": 2, "total_tokens": 10}
            ],
        }

    monkeypatch.setattr(query_repo, "stats", _stats)

    body = _client().get("/api/ai/logs/stats").json()["data"]

    assert body["daily"][0]["calls"] == 3
    assert body["by_provider"][0]["provider"] == "team"
    assert body["by_model"][0]["model"] == "gpt-x"
    assert body["by_purpose"][0]["purpose"] == "chat"
