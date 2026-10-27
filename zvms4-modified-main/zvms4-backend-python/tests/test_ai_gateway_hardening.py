"""AI 网关的安全/性能硬化：请求体上限、请求时 SSRF 复核、OCR 地址校验、历史查询合并。

四件事，各自钉在「不改行为的那一侧」：

* **体积/条数**：超限回 OpenAI 形状的 413/400（客户端按 ``error.code`` 分支），
  合规请求一个字节都不受影响；``Content-Length`` 撒谎时按实际读取量判。
* **SSRF**：录入时校验过 ``base_url`` 不够（DNS rebinding），发请求前用同一套私网
  黑名单再看一眼；官方域名与服务端作用域走「一次 DNS 都不做」的快路径，命中结果
  按主机缓存 60 秒。全部用桩 DNS，不出网。
* **OCR 引擎地址**：写入口拒绝指向私网的 ``base_url``/``endpoint``。
* **历史回放**：去掉「每页一次归属查询」的重复，并把「翻到 400 条」从固定 4 轮
  往返压到「取不满一页就停」（≤100 条的历史只要 1 次查询）；返回的消息序列必须与
  改造前逐条相同（用改造前的翻页算法在测试里做对照实现）。

仓储与上游全部内存桩：不连库、不出网。
"""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import settings  # noqa: F401  (导入顺序与其它 AI 用例一致)
from db import backends
from routers import ai_chat_conversations_router as chat_router
from routers import ai_router
from services import settings_runtime
from services.ai import config as ai_config
from services.ai import conversations, credentials, ocr_engines, providers
from services.ai.routing import RouteError
from typings.log import inject_log
from util.object_id import get_current_user

ADMIN = "000000000000000000000001"
CONVERSATION = "c" * 32
GROQ_SECRET = {"api_key": "gsk-unit-test-0123456789abcdef"}


class StubLog:
    def with_text(self, *args, **kwargs):
        return self

    async def insert_log(self):
        return None


class FakeEngineRepo:
    """``AIOcrEnginesRepo`` 的最小内存版（本文件的用例只关心「有没有落库」）。"""

    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}
        self.writes: list[tuple] = []

    async def insert(self, **fields) -> str:
        self.writes.append(("insert", fields))
        oid = f"{len(self.rows) + 1:024x}"
        self.rows[oid] = {"_id": oid, **fields}
        return oid

    async def get_by_oid(self, oid: str) -> dict | None:
        row = self.rows.get(oid)
        return dict(row) if row is not None else None

    async def update(self, oid: str, **changes) -> bool:
        self.writes.append(("update", oid, changes))
        row = self.rows.get(oid)
        if row is None:
            return False
        row.update({key: value for key, value in changes.items() if value is not None})
        return True


@pytest.fixture(autouse=True)
def _isolated_state(monkeypatch, quiet_ai_governance):
    credentials._cache.clear()
    credentials.clear_ssrf_cache()
    monkeypatch.setattr(settings, "AI_PROVIDERS", {}, raising=False)
    yield
    credentials._cache.clear()
    credentials.clear_ssrf_cache()


def _client(user: dict | None = None) -> TestClient:
    app = FastAPI()
    app.include_router(ai_router.router, prefix="/api/ai")
    identity = lambda: user or {"id": ADMIN, "per": ["admin"]}  # noqa: E731
    app.dependency_overrides[get_current_user] = identity
    app.dependency_overrides[ai_router.get_ai_client] = identity
    app.dependency_overrides[inject_log] = lambda: StubLog()
    return TestClient(app)


@pytest.fixture
def small_limits(monkeypatch):
    """把三档上限压到「用小报文就能触发」的量级（默认值另有断言）。"""
    monkeypatch.setattr(ai_config, "chat_max_body_bytes", lambda: 4096)
    monkeypatch.setattr(ai_config, "chat_max_messages", lambda: 3)
    monkeypatch.setattr(ai_config, "chat_max_content_chars", lambda: 16)


def _chat(client: TestClient | None = None, **payload):
    body = {"model": "gpt-x", "messages": [{"role": "user", "content": "hi"}]}
    body.update(payload)
    return (client or _client()).post("/api/ai/v1/chat/completions", json=body)


# ---------------------------------------------------------------------------
# 请求体：体积 / 条数 / 单条长度
# ---------------------------------------------------------------------------


def test_defaults_match_the_documented_numbers():
    assert ai_config._CHAT_MAX_MESSAGES == 200
    assert ai_config._CHAT_MAX_CONTENT_CHARS == 120_000
    assert ai_config._CHAT_MAX_BODY_BYTES >= 1024 * 1024
    # 注册到 settings_runtime 才能在线调（键名是管理端的契约）。
    specs = settings_runtime.registry()
    assert specs["ai.chat_max_messages"].default == 200
    assert specs["ai.chat_max_content_chars"].default == 120_000
    assert specs["ai.chat_max_body_bytes"].default == ai_config._CHAT_MAX_BODY_BYTES


def test_runtime_override_beats_the_constant(monkeypatch):
    monkeypatch.setattr(
        settings_runtime, "get_override", lambda key: 7 if key.startswith("ai.chat_") else None
    )

    assert ai_config.chat_max_body_bytes() == 7
    assert ai_config.chat_max_messages() == 7
    assert ai_config.chat_max_content_chars() == 7


def test_chunked_body_over_limit_is_413_without_content_length(small_limits):
    """声明超限就立刻回 413：连 body 都不读（省内存，也是最快的一条路）。"""
    body = {"model": "gpt-x", "messages": [{"role": "user", "content": "x" * 8192}]}

    response = _client().post("/api/ai/v1/chat/completions", json=body)

    assert response.status_code == 413
    error = response.json()["error"]
    assert error["code"] == "request_too_large"
    assert error["type"] == "invalid_request_error"
    assert response.headers["X-AI-Request-Id"]


def test_content_length_over_limit_is_413_before_reading_the_body(small_limits):
    """声明超限就立刻回 413：连 body 都不读（省内存，也是最快的一条路）。

    直接构造一个「一读 body 就抛」的请求，把「先看 Content-Length」这条顺序钉住
    ——只断言状态码的话，读完之后再判也能得到 413，顺序退化了也看不出来。
    """
    from starlette.requests import Request as StarletteRequest

    async def _receive():  # pragma: no cover - 被调用就是回归
        raise AssertionError("超限的请求不该被读取")

    request = StarletteRequest(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/ai/v1/chat/completions",
            "headers": [(b"content-length", b"999999")],
        },
        _receive,
    )

    payload, error = asyncio.run(ai_router._read_chat_payload(request, "rid"))

    assert payload is None
    assert error.status_code == 413
    assert json.loads(error.body)["error"]["code"] == "request_too_large"


def test_content_length_over_limit_is_413_on_the_wire(small_limits):
    body = {"model": "gpt-x", "messages": [{"role": "user", "content": "x" * 8192}]}

    response = _client().post("/api/ai/v1/chat/completions", json=body)

    assert response.status_code == 413
    error = response.json()["error"]
    assert error["code"] == "request_too_large"
    assert error["type"] == "invalid_request_error"
    assert response.headers["X-AI-Request-Id"]


def test_chunked_body_over_limit_is_413_without_content_length(small_limits):
    """``Content-Length`` 不可信：分块传输时只能按实际读取的字节数判。"""
    chunk = json.dumps(
        {"model": "gpt-x", "messages": [{"role": "user", "content": "x" * 8192}]}
    ).encode()

    response = _client().post(
        "/api/ai/v1/chat/completions",
        content=iter([chunk]),
        headers={"Content-Type": "application/json"},
    )

    assert "content-length" not in {key.lower() for key in response.request.headers}
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "request_too_large"


def test_too_many_messages_is_400(small_limits):
    messages = [{"role": "user", "content": "hi"} for _ in range(4)]

    response = _chat(messages=messages)

    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "too_many_messages"
    assert error["type"] == "invalid_request_error"


def test_single_message_too_long_is_400(small_limits):
    response = _chat(messages=[{"role": "user", "content": "x" * 17}])

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "message_too_long"


def test_multimodal_text_parts_count_towards_the_limit(small_limits):
    """``content`` 是数组时只算 text 段：图片按字节由体积上限兜。"""
    content = [
        {"type": "text", "text": "x" * 20},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
    ]

    response = _chat(messages=[{"role": "user", "content": content}])

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "message_too_long"


def test_valid_payload_passes_the_gate(small_limits, monkeypatch):
    """闸门对合规请求零影响：正常走到「没有可用 provider」的 503。"""

    async def _catalog(user_oid: str) -> list[dict]:
        return []

    monkeypatch.setattr(credentials, "catalog", _catalog)

    response = _chat(messages=[{"role": "user", "content": "hi"}])

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "AI_NO_PROVIDER"


def test_malformed_json_is_400_in_openai_shape(small_limits):
    response = _client().post(
        "/api/ai/v1/chat/completions",
        content=b"{not json",
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "invalid_request_body"
    assert "detail" not in response.json()


# ---------------------------------------------------------------------------
# 请求时 SSRF 复核
# ---------------------------------------------------------------------------

def _entry(base_url: str, *, scope: str = "shared") -> dict:
    return {
        "key": f"{scope}:entry",
        "scope": scope,
        "owner_oid": credentials.SHARED_OWNER,
        "name": "lab",
        "kind": "openai_compat",
        "base_url": base_url,
        "models": ["gpt-x"],
        "api_key_cipher": "cipher",
        "api_key": None,
        "key_hint": "cdef",
        "enabled": True,
        "oid": "b" * 24,
        "created_at": None,
        "updated_at": None,
    }


class FakeResponse:
    status_code = 200
    text = '{"id": "ok"}'

    def json(self) -> dict:
        return {"id": "ok"}

    async def aread(self) -> bytes:
        return self.text.encode()


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def post(self, url, *, json=None, headers=None, timeout=None):
        self.calls.append(url)
        return FakeResponse()


@pytest.fixture
def upstream(monkeypatch) -> FakeClient:
    client = FakeClient()
    monkeypatch.setattr(providers, "_client_instance", lambda: client)
    return client


@pytest.fixture
def dns(monkeypatch):
    """桩 DNS：``hosts`` 决定每个主机解析到什么；返回调用计数。"""

    state = {"hosts": {}, "calls": []}

    def _lookup(host: str):
        state["calls"].append(host)
        import ipaddress

        return [ipaddress.ip_address(address) for address in state["hosts"].get(host, [])] or None

    monkeypatch.setattr(credentials, "_lookup_addresses", _lookup)
    return state


def test_custom_host_resolving_private_is_blocked(upstream, dns):
    dns["hosts"] = {"rebind-block.example.net": ["127.0.0.1"]}

    with pytest.raises(RouteError) as excinfo:
        asyncio.run(providers.chat_completion({}, _entry("https://rebind-block.example.net/v1"), "sk"))

    assert excinfo.value.code == "AI_UPSTREAM_UNREACHABLE"
    assert excinfo.value.status_code == 502
    # 被拦的请求一次都不该打到 httpx。
    assert upstream.calls == []


def test_private_ip_literal_is_blocked_without_dns(upstream, monkeypatch):
    def _boom(host):  # pragma: no cover - 被调用就是失败
        raise AssertionError("IP 字面量不该触发 DNS")

    monkeypatch.setattr(credentials, "_lookup_addresses", _boom)

    with pytest.raises(RouteError) as excinfo:
        asyncio.run(
            providers.chat_completion({}, _entry("http://169.254.169.254/latest"), "sk")
        )

    assert excinfo.value.code == "AI_UPSTREAM_UNREACHABLE"
    assert upstream.calls == []


def test_resolution_is_cached_for_the_same_host(upstream, dns):
    dns["hosts"] = {"rebind-cache.example.net": ["127.0.0.1"]}
    entry = _entry("https://rebind-cache.example.net/v1")

    for _ in range(3):
        with pytest.raises(RouteError):
            asyncio.run(providers.chat_completion({}, entry, "sk"))

    assert dns["calls"] == ["rebind-cache.example.net"]


def test_official_host_takes_the_no_dns_fast_path(upstream, monkeypatch):
    def _boom(host):  # pragma: no cover - 被调用就是回归
        raise AssertionError("官方域名不该做 DNS 复核")

    monkeypatch.setattr(credentials, "_lookup_addresses", _boom)

    result = asyncio.run(
        providers.chat_completion({}, _entry("https://api.openai.com/v1"), "sk")
    )

    assert result["id"] == "ok"
    assert upstream.calls == ["https://api.openai.com/v1/chat/completions"]


def test_server_scope_is_trusted_and_never_resolved(upstream, monkeypatch):
    """``settings.AI_PROVIDERS`` 只由运维改，且可能是内网自建网关。"""

    def _boom(host):  # pragma: no cover
        raise AssertionError("server 作用域不该做 DNS 复核")

    monkeypatch.setattr(credentials, "_lookup_addresses", _boom)

    result = asyncio.run(
        providers.chat_completion(
            {}, _entry("http://10.0.0.9:8000/v1", scope="server"), "sk"
        )
    )

    assert result["id"] == "ok"


def test_unresolvable_host_keeps_the_old_error_path(upstream, dns):
    """解析失败不是「私网」：交给 httpx 报 AI_UPSTREAM_ERROR，别误判成 SSRF。"""
    dns["hosts"] = {}

    result = asyncio.run(
        providers.chat_completion({}, _entry("https://nxdomain.example.net/v1"), "sk")
    )

    assert result["id"] == "ok"
    assert upstream.calls == ["https://nxdomain.example.net/v1/chat/completions"]


# ---------------------------------------------------------------------------
# OCR 引擎地址校验（写入口）
# ---------------------------------------------------------------------------


def test_ocr_engine_endpoint_url_forms_are_parsed():
    """阿里 endpoint 常常没有 scheme，裸 ``host:port`` 也必须认出来。"""
    assert credentials.host_of_url("ocr-api.cn-hangzhou.aliyuncs.com") == (
        "ocr-api.cn-hangzhou.aliyuncs.com"
    )
    assert credentials.host_of_url("http://127.0.0.1:8080/v1") == "127.0.0.1"
    assert credentials.host_of_url("//10.0.0.5:9000") == "10.0.0.5"
    assert credentials.host_of_url(None) == ""


def test_ocr_engine_private_base_url_is_rejected():
    response = _client().post(
        "/api/ai/ocr-engines",
        json={
            "name": "lab",
            "kind": "groq",
            "config": {"base_url": "http://127.0.0.1:11434/v1"},
            "secret": dict(GROQ_SECRET),
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "AI_OCR_ENDPOINT_BLOCKED"


def test_ocr_engine_private_endpoint_on_update_is_rejected(monkeypatch):
    repo = FakeEngineRepo()
    monkeypatch.setattr(backends, "get_ai_ocr_engines_repo", lambda session=None: repo)
    oid = asyncio.run(
        repo.insert(
            name="primary",
            kind="baidu",
            config={},
            secret_cipher="cipher",
            key_hint="cdef",
            enabled=True,
            priority=10,
        )
    )

    response = _client().patch(
        f"/api/ai/ocr-engines/{oid}",
        json={"config": {"endpoint": "10.1.2.3:8080"}},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "AI_OCR_ENDPOINT_BLOCKED"
    assert repo.writes == [("insert", repo.writes[0][1])]  # 只有建行那次写


def test_ocr_engine_public_endpoint_is_accepted(monkeypatch):
    repo = FakeEngineRepo()
    monkeypatch.setattr(backends, "get_ai_ocr_engines_repo", lambda session=None: repo)

    response = _client().post(
        "/api/ai/ocr-engines",
        json={
            "name": "lab",
            "kind": "groq",
            "config": {"base_url": "https://api.groq.com/openai/v1"},
            "secret": dict(GROQ_SECRET),
        },
    )

    assert response.status_code == 201
    assert repo.writes and repo.writes[0][0] == "insert"


def test_ocr_endpoint_check_ignores_engines_without_urls():
    assert asyncio.run(ocr_engines.blocked_endpoint({"model": "x"}, None)) is None
    assert asyncio.run(ocr_engines.blocked_endpoint(None, None)) is None


# ---------------------------------------------------------------------------
# 历史回放：1 次查询 + 与改造前等价
# ---------------------------------------------------------------------------


class FakeMessagesRepo:
    """``list_after`` 的内存版：行数封顶与 Oracle 仓储一致（``LIST_LIMIT_MAX=100``）。"""

    def __init__(self, rows: list[dict] | None = None) -> None:
        self.rows = list(rows or [])
        self.queries: list[tuple[int, int]] = []

    async def list_after(self, conversation_oid, after_seq=0, *, limit=50):
        self.queries.append((after_seq, limit))
        size = max(1, min(int(limit), conversations.MESSAGE_LIMIT_MAX))
        rows = [
            dict(row)
            for row in self.rows
            if row["conversation_oid"] == conversation_oid and row["seq"] > after_seq
        ]
        rows.sort(key=lambda row: row["seq"])
        return rows[:size]


def _message(seq: int, *, role: str = "user", content: str = "hi") -> dict:
    return {
        "_id": f"m{seq:031d}",
        "conversation_oid": CONVERSATION,
        "seq": seq,
        "role": role,
        "content": f"{content}-{seq}",
        "tool_calls": None,
        "tool_call_id": None,
        "tool_name": None,
        "round_no": None,
        "finish_reason": None,
        "request_id": None,
        "prompt_tokens": None,
        "completion_tokens": None,
        "total_tokens": None,
        "cache_read_tokens": None,
        "cache_write_tokens": None,
        "created_at": None,
    }


async def _paged_reference(repo, conversation, *, page: int, maximum: int):
    """改造前的算法（每页 ``page`` 条、翻到 ``maximum``）：作为等价性对照。"""
    after = max(0, int(conversation.get("summary_upto_seq") or 0))
    rows: list[dict] = []
    while len(rows) < maximum:
        items = await repo.list_after(
            str(conversation.get("_id") or ""), after, limit=page
        )
        if not items:
            break
        rows.extend(items)
        after = max(after, int(items[-1].get("seq") or 0))
        if len(items) < page:
            break
    return [
        chat_router._replay(conversations.message_view(row))
        for row in rows[:maximum]
    ]


def test_history_matches_the_old_paging_result(monkeypatch):
    rows = [
        _message(seq, role="user" if seq % 2 else "assistant") for seq in range(1, 251)
    ]
    repo = FakeMessagesRepo(rows)
    monkeypatch.setattr(backends, "get_ai_messages_repo", lambda session=None: repo)
    conversation = {"_id": CONVERSATION, "summary_upto_seq": 0}

    history = asyncio.run(chat_router._history(conversation))
    # 250 条 = 100 + 100 + 50 三页（仓库单页上限 100，不能靠「一次要 400」绕过），
    # 改造前的同一场景是 6 次查询（每页多一次 require_row）。
    assert repo.queries == [(0, 100), (100, 100), (200, 100)]

    reference = asyncio.run(_paged_reference(repo, conversation, page=100, maximum=400))

    assert history == reference
    assert len(history) == 250


def test_short_history_costs_a_single_query(monkeypatch):
    """常见情况（≤100 条）一次查询搞定：这是「每请求 4 次往返」真正被砍掉的部分。"""
    rows = [_message(seq) for seq in range(1, 31)]
    repo = FakeMessagesRepo(rows)
    monkeypatch.setattr(backends, "get_ai_messages_repo", lambda session=None: repo)

    history = asyncio.run(chat_router._history({"_id": CONVERSATION}))

    assert len(history) == 30
    assert repo.queries == [(0, 100)]


def test_history_respects_summary_cut_and_cap(monkeypatch):
    rows = [_message(seq) for seq in range(1, 501)]
    repo = FakeMessagesRepo(rows)
    monkeypatch.setattr(backends, "get_ai_messages_repo", lambda session=None: repo)

    history = asyncio.run(
        chat_router._history({"_id": CONVERSATION, "summary_upto_seq": 100})
    )

    assert history[0]["content"] == "hi-101"
    assert len(history) == chat_router.HISTORY_MAX == 400
    # 400 条 = 4 页，正好在封顶处停下，不多打一次空查询。
    assert repo.queries == [(100, 100), (200, 100), (300, 100), (400, 100)]
