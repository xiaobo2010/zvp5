"""会话 / run 入口的角色功能开关（V043 ``ROLE_FEATURES``）闸门。

``routers/ai_conversations_router`` 与 ``routers/ai_chat_conversations_router`` 是
AI 的**会话主入口**：``/v1/chat/completions`` 与 ``/agent/run`` 早就接了开关，但
「建会话 → 发消息 / 提交 run」这条路之前只过角色白名单。结果是管理员在「角色策略」
页关掉 ``ai.chat`` / ``ai.agent`` 之后，用户仍然能在会话里用 AI——开关形同虚设。

口径与 ``test_role_feature_gates_ai.py`` 一致，**只加不删**：

* 开关按**这条会话的 mode** 判，而不是按端点一律取 ``ai.chat``：同一个 router
  同时服务两种语义，混用就会「关掉 ai.agent 仍能在 agent 会话里提交 run」；
* 关掉 → 403 且**零副作用**：不建会话、不写 ``AI_CALL_LOGS``、不入 run 队列、
  不打上游、不扣额度；
* 开着（``department`` 默认全开）→ 行为与改造前逐条一致；只关一个不误伤另一个；
* ``admin`` 在策略层恒真（全部开关关掉也不影响管理员）。

两个 router 的错误体都是平台自有信封（``{"error": {"code", "message"}}``，不是
``detail``），所以这里断言 ``error.code == "AI_FORBIDDEN"`` 与开关层文案。

离线：会话 / 消息 / run / 审计仓储全部内存桩，上游与额度也打桩，不连 ADB、不出网。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import settings  # noqa: F401  (导入顺序与其它 AI 用例一致)
from db import backends
from routers import ai_chat_conversations_router as chat_router
from routers import ai_conversations_router as conv_router
from services.agent import run_queue
from services.ai import credentials, providers, quota, rate_limit, routing, safety_guard
from services.roles import policy
from util.object_id import get_current_user

FEATURE_DENIED = "当前角色无权使用该功能"

ADMIN_OID = "000000000000000000000001"
DEPT_OID = "000000000000000000000002"
MODEL = "gpt-x"
SECRET = "sk-unit-test-0123456789abcdef"
CHAT_OID = "c" * 32
AGENT_OID = "a" * 32
RUN_ID = "r" * 32

ADMIN = {"id": ADMIN_OID, "per": ["admin"]}
DEPT = {"id": DEPT_OID, "per": ["department"]}


# ---------------------------------------------------------------------------
# 桩
# ---------------------------------------------------------------------------


class Recorder:
    """记录调用参数的异步桩。"""

    def __init__(self, result=None) -> None:
        self.calls: list[tuple] = []
        self.result = result

    async def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.result


class FakeAuditRepo:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    async def insert(self, **fields) -> str:
        self.rows.append(fields)
        return "a" * 24


class FakeConversationsRepo:
    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}
        self.creates: list[dict] = []
        self.updates: list[dict] = []

    def seed(self, oid: str, *, owner_oid: str = DEPT_OID, mode: str = "chat", **extra) -> dict:
        row = {
            "_id": oid,
            "owner_oid": owner_oid,
            "mode": mode,
            "title": None,
            "model": MODEL,
            "status": "active",
            "summary": None,
            "summary_upto_seq": 0,
            "tokens_prompt": 0,
            "tokens_completion": 0,
            "tokens_total": 0,
            "context_tokens": 0,
            "message_count": 0,
            "compaction_degraded_at": None,
        }
        row.update(extra)
        self.rows[oid] = row
        return row

    async def create(self, *, oid, owner_oid, mode, title=None, model=None) -> dict:
        self.creates.append({"oid": oid, "mode": mode, "title": title, "model": model})
        return dict(self.seed(oid, owner_oid=owner_oid, mode=mode, title=title, model=model))

    async def get(self, oid: str, owner_oid: str) -> dict | None:
        row = self.rows.get(str(oid))
        if row is None or str(row["owner_oid"]) != str(owner_oid):
            return None
        return dict(row)

    async def list_page(self, owner_oid: str, *, status=None, cursor=None, limit=50):
        items = [row for row in self.rows.values() if str(row["owner_oid"]) == str(owner_oid)]
        if status:
            items = [row for row in items if row["status"] == status]
        return [dict(row) for row in items[:limit]], None

    async def update(self, oid, owner_oid, *, title=None, status=None, model=None,
                     clear_model=False) -> bool:
        self.updates.append({"oid": oid, "title": title})
        row = self.rows.get(str(oid))
        if row is None or str(row["owner_oid"]) != str(owner_oid):
            return False
        if title is not None:
            row["title"] = title
        return True


class FakeMessagesRepo:
    def __init__(self, conv: FakeConversationsRepo) -> None:
        self.conv = conv
        self.rows: list[dict] = []
        self.list_calls: list[dict] = []
        self.batches: list[dict] = []

    async def append_batch(self, conversation_oid, rows, *, usage=None):
        self.batches.append({"conversation_oid": conversation_oid, "rows": [dict(r) for r in rows]})
        base = max(
            [row["seq"] for row in self.rows if row["conversation_oid"] == conversation_oid],
            default=0,
        )
        inserted: list[dict] = []
        for offset, item in enumerate(rows):
            row = dict(item)
            row["seq"] = base + offset + 1
            row["conversation_oid"] = conversation_oid
            self.rows.append(row)
            inserted.append(row)
        record = self.conv.rows.get(str(conversation_oid))
        if record is not None:
            record["message_count"] += len(inserted)
        return inserted

    async def list_after(self, conversation_oid, after_seq=0, *, limit=50):
        self.list_calls.append({"conversation_oid": conversation_oid, "after_seq": after_seq})
        rows = [
            dict(row)
            for row in self.rows
            if row["conversation_oid"] == conversation_oid and row["seq"] > after_seq
        ]
        rows.sort(key=lambda row: row["seq"])
        return rows[:limit]


def _entry() -> dict:
    return {
        "key": "shared:acme",
        "scope": "shared",
        "owner_oid": credentials.SHARED_OWNER,
        "name": "acme",
        "kind": "openai_compat",
        "base_url": "https://acme.example.com/v1",
        "models": [MODEL],
        "api_key_cipher": "cipher-acme",
        "api_key": None,
        "key_hint": "cdef",
        "enabled": True,
        "oid": "b" * 24,
        "created_at": None,
        "updated_at": None,
    }


def _deny(monkeypatch, role: str, key: str) -> None:
    """把库里那一格写成 ``enabled=0``（其余 key 与角色不受影响）。"""
    monkeypatch.setitem(policy._features, (role, key), False)


def _deny_all(monkeypatch) -> None:
    for role in policy.EDITABLE_ROLES:
        for key in policy.FEATURE_KEYS:
            monkeypatch.setitem(policy._features, (role, key), False)


def _client(user: dict) -> TestClient:
    app = FastAPI()
    app.include_router(conv_router.router, prefix="/api/ai")
    app.include_router(chat_router.router, prefix="/api/ai")
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, quiet_ai_governance):
    """策略缓存 / 频控 / 熔断 / 信号量都是进程级状态：每个用例从干净起点开始。"""
    policy.reset_cache()
    credentials._cache.clear()
    routing.reset_state()
    rate_limit.reset_state()
    routing._semaphore = None
    routing._semaphore_size = 0
    monkeypatch.setattr(settings, "AI_PROVIDERS", {}, raising=False)
    yield
    policy.reset_cache()
    credentials._cache.clear()
    routing.reset_state()
    rate_limit.reset_state()
    routing._semaphore = None
    routing._semaphore_size = 0


@pytest.fixture
def env(monkeypatch) -> SimpleNamespace:
    conv = FakeConversationsRepo()
    msgs = FakeMessagesRepo(conv)
    audit = FakeAuditRepo()
    monkeypatch.setattr(backends, "get_ai_conversations_repo", lambda session=None: conv)
    monkeypatch.setattr(backends, "get_ai_messages_repo", lambda session=None: msgs)
    monkeypatch.setattr(backends, "get_ai_call_logs_repo", lambda session=None: audit)

    # run 队列：``submit`` / ``kick`` 只用来证明「有没有走到入队」，不跑真泵。
    submit = Recorder({"run_id": RUN_ID, "message_oid": "m" * 32, "status": "queued"})
    kick = Recorder(None)
    list_active = Recorder([])
    run_detail = Recorder({})
    cancel = Recorder({})
    monkeypatch.setattr(run_queue, "submit", submit)
    monkeypatch.setattr(run_queue, "kick", kick)
    monkeypatch.setattr(run_queue, "list_active", list_active)
    monkeypatch.setattr(run_queue, "run_detail", run_detail)
    monkeypatch.setattr(run_queue, "cancel", cancel)

    return SimpleNamespace(
        conv=conv,
        msgs=msgs,
        audit=audit,
        submit=submit,
        kick=kick,
        list_active=list_active,
        run_detail=run_detail,
        cancel=cancel,
    )


@pytest.fixture
def upstream(monkeypatch):
    """chat 面的最小环境：目录 / 上游 / 额度 / 安全闸全部内存桩。"""
    state = {"streams": []}

    async def _catalog(_user_oid: str) -> list[dict]:
        return [_entry()]

    async def _resolve(_entry_row):
        return SECRET

    async def _allowed(*_args, **_kwargs):
        return safety_guard.ALLOWED

    async def _stream(payload, entry, api_key):
        state["streams"].append(payload)
        yield b'data: {"choices":[{"delta":{"content":"hi"},"finish_reason":null}]}\n\n'
        yield (
            b'data: {"choices":[{"delta":{},"finish_reason":"stop"}],'
            b'"usage":{"prompt_tokens":3,"completion_tokens":1,"total_tokens":4}}\n\n'
        )
        yield b"data: [DONE]\n\n"

    monkeypatch.setattr(credentials, "catalog", _catalog)
    monkeypatch.setattr(credentials, "resolve_api_key", _resolve)
    monkeypatch.setattr(safety_guard, "screen", _allowed)
    monkeypatch.setattr(providers, "chat_completion_stream", _stream)
    monkeypatch.setattr(quota, "peek", Recorder((True, 0, 100)))
    monkeypatch.setattr(quota, "consume", Recorder((True, 1, 100)))
    return state


# ---------------------------------------------------------------------------
# 建会话：按 payload.mode 判
# ---------------------------------------------------------------------------


def test_chat_create_is_denied_without_touching_the_repository(env, monkeypatch):
    _deny(monkeypatch, "department", "ai.chat")

    response = _client(DEPT).post("/api/ai/conversations", json={"mode": "chat"})

    assert response.status_code == 403, response.text
    body = response.json()
    assert body["error"]["code"] == "AI_FORBIDDEN"
    assert body["error"]["message"] == FEATURE_DENIED
    # 零副作用：不建会话、不写 AI_CALL_LOGS。
    assert env.conv.creates == []
    assert env.conv.rows == {}
    assert env.audit.rows == []


def test_agent_create_is_denied_when_ai_agent_is_off(env, monkeypatch):
    _deny(monkeypatch, "department", "ai.agent")

    response = _client(DEPT).post("/api/ai/conversations", json={"mode": "agent"})

    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "AI_FORBIDDEN"
    assert env.conv.creates == []
    assert env.audit.rows == []


def test_each_mode_is_gated_on_its_own_switch(env, monkeypatch):
    _deny(monkeypatch, "department", "ai.agent")

    chat = _client(DEPT).post("/api/ai/conversations", json={"mode": "chat"})

    assert chat.status_code == 201, chat.text
    assert [row["mode"] for row in env.conv.creates] == ["chat"]

    _deny(monkeypatch, "department", "ai.agent")
    monkeypatch.setitem(policy._features, ("department", "ai.chat"), False)

    agent = _client(DEPT).post("/api/ai/conversations", json={"mode": "agent"})

    # ai.chat 关、ai.agent 关 → agent 也不通；把 ai.agent 打开只放行 agent。
    assert agent.status_code == 403, agent.text
    monkeypatch.setitem(policy._features, ("department", "ai.agent"), True)

    reopened = _client(DEPT).post("/api/ai/conversations", json={"mode": "agent"})

    assert reopened.status_code == 201, reopened.text
    assert [row["mode"] for row in env.conv.creates] == ["chat", "agent"]


def test_unknown_mode_is_still_a_400(env, monkeypatch):
    """开关层不接管模式校验：拼错的 mode 还是服务层的 400。"""
    _deny_all(monkeypatch)

    response = _client(ADMIN).post("/api/ai/conversations", json={"mode": "summary"})

    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "AI_BAD_REQUEST"
    assert env.conv.creates == []


# ---------------------------------------------------------------------------
# 列表 / 详情 / 消息：按该条会话的 mode 判
# ---------------------------------------------------------------------------


def test_list_hides_only_the_disabled_mode(env, monkeypatch):
    env.conv.seed(CHAT_OID, mode="chat")
    env.conv.seed(AGENT_OID, mode="agent")

    both = _client(DEPT).get("/api/ai/conversations")

    assert both.status_code == 200, both.text
    assert {row["mode"] for row in both.json()["data"]["conversations"]} == {"chat", "agent"}

    _deny(monkeypatch, "department", "ai.chat")

    filtered = _client(DEPT).get("/api/ai/conversations")

    assert filtered.status_code == 200, filtered.text
    body = filtered.json()
    assert [row["oid"] for row in body["data"]["conversations"]] == [AGENT_OID]
    # metadata.size 跟着过滤后的列表走：前端读它当「本页条数」。
    assert body["metadata"]["size"] == 1
    assert env.audit.rows == []


def test_get_and_messages_of_a_chat_conversation_are_denied(env, monkeypatch):
    env.conv.seed(CHAT_OID, mode="chat")
    env.msgs.rows.append(
        {"conversation_oid": CHAT_OID, "seq": 1, "role": "user", "content": "机密"}
    )
    _deny(monkeypatch, "department", "ai.chat")

    detail = _client(DEPT).get(f"/api/ai/conversations/{CHAT_OID}")
    messages = _client(DEPT).get(f"/api/ai/conversations/{CHAT_OID}/messages")

    assert detail.status_code == 403, detail.text
    assert messages.status_code == 403, messages.text
    assert detail.json()["error"]["code"] == "AI_FORBIDDEN"
    # 正文一次都没读出来。
    assert env.msgs.list_calls == []
    assert env.audit.rows == []


def test_agent_conversation_detail_survives_chat_switch(env, monkeypatch):
    env.conv.seed(AGENT_OID, mode="agent")
    _deny(monkeypatch, "department", "ai.chat")

    response = _client(DEPT).get(f"/api/ai/conversations/{AGENT_OID}")

    assert response.status_code == 200, response.text
    assert response.json()["data"]["mode"] == "agent"


# ---------------------------------------------------------------------------
# run：按会话 mode 判（提交）/ 静态按 ai.agent 判（在飞 run 三个端点）
# ---------------------------------------------------------------------------


def test_agent_run_submit_is_denied_without_queueing_anything(env, monkeypatch):
    env.conv.seed(AGENT_OID, mode="agent")
    _deny(monkeypatch, "department", "ai.agent")

    response = _client(DEPT).post(
        f"/api/ai/conversations/{AGENT_OID}/runs", json={"content": "跑一下"}
    )

    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "AI_FORBIDDEN"
    # 入队一次都没发生，也没有 kick、没有审计行。
    assert env.submit.calls == []
    assert env.kick.calls == []
    assert env.audit.rows == []


def test_agent_run_submit_still_works_when_only_chat_is_off(env, monkeypatch):
    env.conv.seed(AGENT_OID, mode="agent")
    _deny(monkeypatch, "department", "ai.chat")

    response = _client(DEPT).post(
        f"/api/ai/conversations/{AGENT_OID}/runs", json={"content": "跑一下"}
    )

    assert response.status_code == 202, response.text
    assert len(env.submit.calls) == 1
    assert env.submit.calls[0][1]["conversation_oid"] == AGENT_OID
    assert len(env.kick.calls) == 1


def test_run_endpoints_are_gated_on_ai_agent(env, monkeypatch):
    _deny(monkeypatch, "department", "ai.agent")
    client = _client(DEPT)

    listed = client.get("/api/ai/runs?active=1")
    detail = client.get(f"/api/ai/runs/{RUN_ID}")
    cancelled = client.post(f"/api/ai/runs/{RUN_ID}/cancel")

    for response in (listed, detail, cancelled):
        assert response.status_code == 403, response.text
        assert response.json()["error"]["code"] == "AI_FORBIDDEN"
    # run 只有 agent 会话才有，所以这里静态判 ai.agent；三次都没碰队列。
    assert env.list_active.calls == []
    assert env.run_detail.calls == []
    assert env.cancel.calls == []
    assert env.audit.rows == []


def test_run_endpoints_still_work_when_only_chat_is_off(env, monkeypatch):
    _deny(monkeypatch, "department", "ai.chat")
    client = _client(DEPT)

    listed = client.get("/api/ai/runs?active=1")

    assert listed.status_code == 200, listed.text
    assert len(env.list_active.calls) == 1


# ---------------------------------------------------------------------------
# 同步 SSE（chat 面）：开关必须先于上游、留存、审核、扣费
# ---------------------------------------------------------------------------


def test_chat_sse_is_denied_before_the_upstream_call(env, upstream, monkeypatch):
    env.conv.seed(CHAT_OID, mode="chat")
    _deny(monkeypatch, "department", "ai.chat")

    response = _client(DEPT).post(
        f"/api/ai/conversations/{CHAT_OID}/chat", json={"content": "你好"}
    )

    assert response.status_code == 403, response.text
    body = response.json()
    assert body["error"]["code"] == "AI_FORBIDDEN"
    assert body["error"]["message"] == FEATURE_DENIED
    # 零副作用：不上游、不落消息、不写审计。
    assert upstream["streams"] == []
    assert env.msgs.batches == []
    assert env.msgs.rows == []
    assert env.audit.rows == []


def test_chat_sse_still_works_when_only_agent_is_off(env, upstream, monkeypatch):
    env.conv.seed(CHAT_OID, mode="chat")
    _deny(monkeypatch, "department", "ai.agent")

    response = _client(DEPT).post(
        f"/api/ai/conversations/{CHAT_OID}/chat", json={"content": "你好"}
    )

    assert response.status_code == 200, response.text
    assert "hi" in response.text
    assert len(upstream["streams"]) == 1
    # 用户消息 + assistant 各一条（改造前的写入行为不变）。
    assert [row["role"] for row in env.msgs.rows] == ["user", "assistant"]


# ---------------------------------------------------------------------------
# admin 短路：全部开关关掉也照旧
# ---------------------------------------------------------------------------


def test_admin_keeps_every_entry_when_all_switches_are_off(env, upstream, monkeypatch):
    _deny_all(monkeypatch)
    env.conv.seed(AGENT_OID, owner_oid=ADMIN_OID, mode="agent")
    client = _client(ADMIN)

    chat = client.post("/api/ai/conversations", json={"mode": "chat"})
    agent = client.post("/api/ai/conversations", json={"mode": "agent"})
    runs = client.post(f"/api/ai/conversations/{AGENT_OID}/runs", json={"content": "跑"})
    listed = client.get("/api/ai/runs?active=1")

    assert chat.status_code == 201, chat.text
    assert agent.status_code == 201, agent.text
    assert runs.status_code == 202, runs.text
    assert listed.status_code == 200, listed.text
    assert len(env.submit.calls) == 1


def test_department_defaults_are_the_pre_change_behaviour(env):
    """``department`` 默认两个开关都开：这条路必须与改造前完全一致。"""
    department = policy.DEFAULTS["department"]["features"]
    assert department["ai.chat"] is True
    assert department["ai.agent"] is True

    client = _client(DEPT)

    assert client.post("/api/ai/conversations", json={"mode": "chat"}).status_code == 201
    assert client.post("/api/ai/conversations", json={"mode": "agent"}).status_code == 201
    assert client.get("/api/ai/runs?active=1").status_code == 200
    assert env.audit.rows == []
