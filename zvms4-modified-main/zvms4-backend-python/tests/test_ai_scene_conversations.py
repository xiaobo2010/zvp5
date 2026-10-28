"""只读场景（终端 / 文件编辑）：会话复用、只读工具子集、密码门豁免、
列表隐藏、14 天窗口、参考内容脱敏且不落消息表。

三条 oracle 条件的落点都能在这里找到：

* **条件①（保留窗口）**：场景会话 14 天后走既有级联删除，接在 retention 作业的
  ``scene_conversations`` 键上；
* **条件②（参考内容先脱敏再入库）**：``scene_reference`` 的三道处理（凭据 → PII →
  截断），且参考内容**不进** ``AI_MESSAGES``，只随 ``AI_PROMPT_AUDIT`` 留档；
* **条件③（镜像）**：无需代码——``ai_messages`` / ``ai_runs`` 的镜像关系不变，
  脱敏后的内容进镜像仍然合规。

工具声明与执行两道闸都钉住：声明层不给写工具，执行层就算模型编出一个写工具名也
只得到 ``denied``。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient

from db import backends
from db.repos.oracle import ai_conversations as conversations_repo
from services.agent import executor, run_queue
from services.agent.tool_impl import AgentError
from services.agent.tools import READ, TOOLS, tool_specs
from services.ai import conversations, prompt_audit
from typings.log import inject_log
from util import agent_unlock
from util.object_id import get_current_user

OWNER = "0" * 24
OTHER = "1" * 24
DEPARTMENT = "2" * 24
ADMIN = {"id": OWNER, "per": ["admin"]}
CONV = "a" * 32


def _now(offset: float = 0.0) -> datetime:
    return datetime(2026, 10, 1, 12, 0, 0) + timedelta(seconds=offset)


# ---------------------------------------------------------------------------
# 内存仓储
# ---------------------------------------------------------------------------


class FakeConversationsRepo:
    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}
        self.creates: list[dict] = []
        self.deleted: list[str] = []

    def seed(
        self,
        oid: str = CONV,
        *,
        owner_oid: str = OWNER,
        scene: str | None = "terminal",
        title: str | None = "终端智能",
        updated_at: datetime | None = None,
    ) -> dict:
        moment = updated_at or _now()
        row = {
            "_id": oid,
            "owner_oid": owner_oid,
            "mode": "agent",
            "title": title,
            "model": "vendor/model",
            "thinking": None,
            "scene": scene,
            "status": "active",
            "summary": None,
            "summary_upto_seq": 0,
            "tokens_prompt": 0,
            "tokens_completion": 0,
            "tokens_total": 0,
            "context_tokens": 0,
            "message_count": 0,
            "compaction_degraded_at": None,
            "created_at": moment,
            "updated_at": moment,
        }
        self.rows[oid] = row
        return row

    async def latest_by_scene(self, owner_oid: str, scene: str) -> dict | None:
        hits = [
            row
            for row in self.rows.values()
            if str(row["owner_oid"]) == str(owner_oid) and row.get("scene") == scene
        ]
        if not hits:
            return None
        return dict(max(hits, key=lambda row: row["updated_at"]))

    async def create(self, *, oid, owner_oid, mode, title=None, model=None,
                     thinking=None, scene=None) -> dict:
        self.creates.append(
            {
                "oid": oid,
                "owner_oid": owner_oid,
                "mode": mode,
                "title": title,
                "model": model,
                "thinking": thinking,
                "scene": scene,
            }
        )
        return dict(self.seed(oid, owner_oid=owner_oid, scene=scene, title=title))

    async def get(self, oid: str, owner_oid: str) -> dict | None:
        row = self.rows.get(str(oid))
        if row is None or str(row["owner_oid"]) != str(owner_oid):
            return None
        return dict(row)

    async def list_stale_scenes(self, cutoff, *, limit=50) -> list[dict]:
        self.last_cutoff = cutoff
        hits = [
            dict(row)
            for row in self.rows.values()
            if row.get("scene") and row["updated_at"] < cutoff
        ]
        return sorted(hits, key=lambda row: row["updated_at"])[:limit]

    async def count_stale_scenes(self, cutoff) -> int:
        return len(
            [
                row
                for row in self.rows.values()
                if row.get("scene") and row["updated_at"] < cutoff
            ]
        )

    async def delete_batched(self, oid, owner_oid, *, batch_size=500) -> int:
        row = self.rows.pop(str(oid), None)
        if row is None or str(row["owner_oid"]) != str(owner_oid):
            return 0
        self.deleted.append(str(oid))
        return 1

    async def update(self, oid, owner_oid, *, title=None, status=None,
                     thinking=None, clear_thinking=False) -> bool:
        return True


class FakeMessagesRepo:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    async def append_batch(self, conversation_oid, rows, *, usage=None):
        for row in rows:
            self.rows.append({**row, "conversation_oid": conversation_oid})
        return [dict(row) for row in rows]

    async def purge_by_conversation(self, conversation_oid, *, batch_size=500) -> int:
        keep = [
            row for row in self.rows if str(row["conversation_oid"]) != str(conversation_oid)
        ]
        removed = len(self.rows) - len(keep)
        self.rows = keep
        return removed


class FakeRunsRepo:
    def __init__(self) -> None:
        self.active: dict[str, list[dict]] = {}
        self.purged: list[str] = []

    async def list_active(self, owner_oid: str) -> list[dict]:
        return list(self.active.get(str(owner_oid), []))

    def seed_active(self, conversation_oid: str, owner_oid: str = OWNER) -> None:
        self.active.setdefault(str(owner_oid), []).append(
            {"conversation_oid": conversation_oid}
        )

    async def purge_by_conversation(self, conversation_oid, *, batch_size=500) -> int:
        self.purged.append(str(conversation_oid))
        return 1

    def __getattr__(self, name):  # submit 会用到 insert_queued / get / etc.
        async def _missing(*args, **kwargs):
            raise AssertionError(f"unexpected runs repo call: {name}")

        return _missing


class FakeEventsRepo:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    async def insert(self, **fields) -> str:
        self.rows.append(fields)
        return "e" * 24

    async def list_by_actor(self, owner_oid, *, status=None, limit=500):
        return []

    async def update_status(self, *args, **kwargs) -> bool:
        return False


class FakePromptAuditRepo:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    async def insert(self, **fields) -> str:
        oid = "p" * 24
        self.rows.append({"oid": oid, **fields})
        return oid

    async def update_outcome(self, oid, **fields) -> bool:
        return True

    async def purge_before(self, cutoff, **kwargs) -> int:
        return 0

    async def count_expired(self, cutoff) -> int:
        return 0


@pytest.fixture
def repos(monkeypatch):
    conv = FakeConversationsRepo()
    msgs = FakeMessagesRepo()
    runs = FakeRunsRepo()
    events = FakeEventsRepo()
    audits = FakePromptAuditRepo()
    monkeypatch.setattr(backends, "get_ai_conversations_repo", lambda session=None: conv)
    monkeypatch.setattr(backends, "get_ai_messages_repo", lambda session=None: msgs)
    monkeypatch.setattr(backends, "get_ai_runs_repo", lambda session=None: runs)
    monkeypatch.setattr(backends, "get_ai_agent_events_repo", lambda session=None: events)
    monkeypatch.setattr(
        backends, "get_ai_prompt_audit_repo", lambda session=None: audits
    )
    return SimpleNamespace(conv=conv, msgs=msgs, runs=runs, events=events, audits=audits)


@pytest_asyncio.fixture(autouse=True)
async def _clean_queue():
    run_queue.reset()
    run_queue._REFERENCES.clear()
    agent_unlock.clear_state()
    yield
    run_queue.reset()
    run_queue._REFERENCES.clear()
    agent_unlock.clear_state()


# ---------------------------------------------------------------------------
# 会话复用
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ensure_scene_creates_once_then_reuses(repos) -> None:
    created = await conversations.ensure_scene(user=ADMIN, scene="terminal")

    assert created["scene"] == "terminal"
    assert created["title"] == "终端智能"
    assert created["mode"] == "agent"
    assert [row["scene"] for row in repos.conv.creates] == ["terminal"]

    again = await conversations.ensure_scene(user=ADMIN, scene="terminal")

    assert again["_id"] == created["_id"]
    assert len(repos.conv.creates) == 1


def test_scene_titles_are_the_frozen_copy() -> None:
    assert conversations.SCENE_TITLES == {
        "terminal": "终端智能",
        "file_edit": "文件编辑智能",
    }


@pytest.mark.asyncio
async def test_ensure_scene_rejects_unknown_scenes(repos) -> None:
    with pytest.raises(AgentError) as caught:
        await conversations.ensure_scene(user=ADMIN, scene="terminal2")
    assert caught.value.code == conversations.SCENE_BAD
    assert caught.value.status_code == 400
    assert repos.conv.creates == []


def test_scene_conversations_are_hidden_from_the_normal_list() -> None:
    """列表查询固定带 ``scene IS NULL``：场景会话不污染 ZAiChat 的左栏。"""
    from sqlalchemy.dialects import oracle

    stmt = conversations_repo._list_page_stmt(
        OWNER, status=None, cursor=None, limit=10
    )
    sql = str(
        stmt.compile(
            dialect=oracle.dialect(), compile_kwargs={"literal_binds": True}
        )
    )
    assert "scene IS NULL" in sql


# ---------------------------------------------------------------------------
# 参考内容：先脱敏，再进 prompt；不落消息表
# ---------------------------------------------------------------------------


def test_reference_masks_credentials_before_truncation() -> None:
    raw = (
        "export DB_PASSWORD=hunter2\n"
        "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.payload.signature\n"
        "row hash 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef\n"
        "contact 13812345678 id 110101199003071234"
    )

    masked = conversations.scene_reference(raw)

    assert "hunter2" not in masked
    assert "eyJhbGciOiJIUzI1NiJ9" not in masked
    assert "0123456789abcdef0123456789abcdef" not in masked
    assert "13812345678" not in masked
    assert "110101199003071234" not in masked
    # 键名与行结构保留：读的人仍知道「这里本来有个密码/哈希」。
    assert "DB_PASSWORD" in masked
    assert "***" in masked


def test_reference_is_capped_and_empty_is_none() -> None:
    assert conversations.scene_reference("") is None
    assert conversations.scene_reference(None) is None
    assert conversations.scene_reference("   ") is None
    assert len(conversations.scene_reference("x" * 20000)) == (
        conversations.SCENE_CONTEXT_MAX
    )


@pytest.mark.asyncio
async def test_capture_keeps_the_reference_next_to_the_question(repos) -> None:
    await prompt_audit.capture(
        user=ADMIN,
        purpose=executor.PURPOSE_AGENT,
        messages=[{"role": "user", "content": "这个报错什么意思"}],
        request_id="r" * 32,
        reference="line 1: boom",
    )

    row = repos.audits.rows[0]
    assert row["prompt_text"].startswith("这个报错什么意思")
    assert "[参考内容]" in row["prompt_text"]
    assert "line 1: boom" in row["prompt_text"]


# ---------------------------------------------------------------------------
# 提交：会话复用 / 密码门豁免
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_submit_scene_reuses_the_conversation_and_skips_the_password_gate(
    repos, monkeypatch
) -> None:
    """admin + 只读场景：没解锁也能提交（0 次解锁调用），消息只存提问本身。"""
    repos.conv.seed(CONV, scene="file_edit", title="文件编辑智能")
    agent_unlock.clear_state()

    async def _insert_queued(**kwargs):
        # 真实仓储在一个事务里写「用户消息 + queued run」；这里只复刻可见结果。
        repos.msgs.rows.append(
            {"role": "user", "content": kwargs["content"], "request_id": kwargs["run_id"]}
        )
        return ({"_id": kwargs["run_id"], "message_oid": kwargs["message_oid"]}, True)

    monkeypatch.setattr(repos.runs, "insert_queued", _insert_queued)

    data = await run_queue.submit_scene(
        user=ADMIN,
        scene="file_edit",
        content="帮我看这个文件为什么报错",
        context="token=abc123",
    )

    assert set(data) == {"run_id", "conversation_oid"}
    assert data["conversation_oid"] == CONV
    assert repos.conv.creates == []  # 已有场景会话：复用，不新建
    assert repos.msgs.rows[0]["content"] == "帮我看这个文件为什么报错"
    # 参考内容不落消息表（只有提问），但会交给执行器（进程内，随 run_id）。
    assert "abc123" not in json_dumps(repos.msgs.rows)
    assert "abc123" not in run_queue._REFERENCES[data["run_id"]]


def json_dumps(value) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, default=str)


@pytest.mark.asyncio
async def test_plain_submit_still_needs_the_password_gate(repos, monkeypatch) -> None:
    """同一个管理员、普通会话：门禁照旧（豁免只对只读场景生效）。"""
    repos.conv.seed(CONV, scene=None)
    agent_unlock.clear_state()

    with pytest.raises(AgentError) as caught:
        await run_queue.submit(user=ADMIN, conversation_oid=CONV, content="查一下")

    assert caught.value.code == "AI_AGENT_UNLOCK_REQUIRED"
    assert caught.value.status_code == 422


@pytest.mark.asyncio
async def test_read_only_scene_does_not_exempt_non_admins(repos, monkeypatch) -> None:
    """非 admin 即使落在场景会话上也要解锁：豁免条件是「只读场景 **且** admin」。"""
    user = {"id": DEPARTMENT, "per": ["department"]}
    repos.conv.seed(CONV, owner_oid=DEPARTMENT, scene="terminal")
    agent_unlock.clear_state()

    with pytest.raises(AgentError) as caught:
        await run_queue.submit(user=user, conversation_oid=CONV, content="看看")

    assert caught.value.code == "AI_AGENT_UNLOCK_REQUIRED"


# ---------------------------------------------------------------------------
# 只读工具子集
# ---------------------------------------------------------------------------


def test_scene_specs_only_declare_read_tools() -> None:
    names = {spec["function"]["name"] for spec in tool_specs(ADMIN)}

    scene_names = {spec["function"]["name"] for spec in tool_specs(ADMIN, scene="terminal")}

    assert scene_names < names
    assert {"fs_write", "fs_delete", "fs_chmod", "ssh_exec", "db_dml", "db_ddl"} & (
        scene_names
    ) == set()
    for name in scene_names:
        assert TOOLS[name].kind == READ, name
    # 读工具仍然在（场景的用途就是「读」）。
    assert {"fs_read", "fs_list", "db_query"} <= scene_names


@pytest.mark.asyncio
async def test_a_write_tool_call_is_denied_even_if_the_model_invents_it(repos) -> None:
    call = {
        "id": "call-1",
        "function": {"name": "fs_write", "arguments": '{"path": "/www/zvms4/x", "content": "y"}'},
    }

    message, proposal, status, note = await executor._run_tool(
        call=call,
        user={"id": OWNER, "per": ["admin"]},
        request_id="r" * 32,
        round_no=1,
        scene="terminal",
    )

    assert proposal is None
    assert status == "denied"
    assert "只读场景" in note
    assert "read_only_scene" in message["content"]
    assert repos.msgs.rows == []


# ---------------------------------------------------------------------------
# 14 天保留窗口：走既有级联删除
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stale_scene_conversations_are_purged_after_fourteen_days(repos) -> None:
    old = repos.conv.seed("b" * 32, scene="terminal", updated_at=_now(-15 * 86400))
    fresh = repos.conv.seed("c" * 32, scene="file_edit", updated_at=_now())
    normal = repos.conv.seed("d" * 32, scene=None, updated_at=_now(-30 * 86400))

    result = await conversations.purge_stale_scenes(now=_now())

    assert result["deleted"] == 1
    assert repos.conv.deleted == [old["_id"]]
    # 普通会话再老也不归这条规则管。
    assert normal["_id"] in repos.conv.rows
    assert fresh["_id"] in repos.conv.rows
    assert repos.runs.purged == [old["_id"]]
    assert repos.conv.last_cutoff == _now() - timedelta(days=14)


@pytest.mark.asyncio
async def test_purge_skips_conversations_with_an_active_run(repos) -> None:
    busy = repos.conv.seed("e" * 32, scene="terminal", updated_at=_now(-20 * 86400))
    repos.runs.seed_active(busy["_id"])

    result = await conversations.purge_stale_scenes(now=_now())

    assert result["deleted"] == 0
    assert busy["_id"] in repos.conv.rows
    assert result["remaining"] == 1


@pytest.mark.asyncio
async def test_orchestration_reports_the_scene_key(monkeypatch) -> None:
    """retention 作业的结果里要有 ``scene_conversations``（与 append_only 并列）。"""
    from tasks import retention

    calls: list[str] = []

    async def _run_retention(*args, **kwargs):
        calls.append("main")
        return {"app_logs": {"deleted": 0}, "birth": 0}

    async def _append_only(now=None):
        calls.append("append_only")
        return {}

    async def _scenes(now=None):
        calls.append("scenes")
        return {"cutoff": "", "deleted": 3, "scanned": 3, "remaining": 0}

    monkeypatch.setattr(retention, "run_retention", _run_retention)
    monkeypatch.setattr(retention, "run_append_only_retention", _append_only)
    monkeypatch.setattr(retention, "purge_stale_scene_conversations", _scenes)
    monkeypatch.setattr(retention.realtime, "disconnect_all_clients", _noop)
    monkeypatch.setattr(retention.system_state, "set_state", lambda *a, **k: None)

    job: dict = {}
    result = await retention._orchestrate(job)

    assert calls == ["main", "append_only", "scenes"]
    assert result["scene_conversations"]["deleted"] == 3
    assert "只读场景会话删除 3 条" in retention.audit_message(
        {
            **result,
            "app_logs": {"deleted": 0, "remaining": 0},
            "export_tasks": {"deleted": 0, "remaining": 0},
            "ai_call_logs": {"deleted": 0, "remaining": 0},
            "ai_agent_events": {"deleted": 0, "remaining": 0},
            "ai_prompt_audit": {"deleted": 0, "remaining": 0},
        }
    )


async def _noop(*args, **kwargs) -> None:
    return None


# ---------------------------------------------------------------------------
# HTTP 面
# ---------------------------------------------------------------------------


class _StubLog:
    def with_text(self, *args, **kwargs):
        return self

    async def insert_log(self):
        return None


def _client(user: dict) -> TestClient:
    from routers import ai_scenes_router

    app = FastAPI()
    app.include_router(ai_scenes_router.router, prefix="/api/ai")
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[inject_log] = lambda: _StubLog()
    return TestClient(app)


@pytest.fixture
def submitted(monkeypatch) -> SimpleNamespace:
    calls: list[dict] = []
    kicks: list[int] = []

    async def _submit_scene(**kwargs):
        calls.append(kwargs)
        return {"run_id": "f" * 32, "conversation_oid": CONV}

    async def _kick():
        kicks.append(1)

    monkeypatch.setattr(run_queue, "submit_scene", _submit_scene)
    monkeypatch.setattr(run_queue, "kick", _kick)
    return SimpleNamespace(calls=calls, kicks=kicks)


def test_scene_endpoint_returns_the_run_handle(submitted) -> None:
    response = _client(ADMIN).post(
        "/api/ai/scenes/terminal/runs",
        json={"content": "看看磁盘", "context": "df -h 输出"},
    )

    assert response.status_code == 202, response.text
    assert response.json()["data"] == {
        "run_id": "f" * 32,
        "conversation_oid": CONV,
    }
    assert submitted.calls[0]["scene"] == "terminal"
    assert submitted.calls[0]["context"] == "df -h 输出"
    assert submitted.kicks == [1]


def test_scene_endpoint_is_admin_only(submitted) -> None:
    response = _client({"id": DEPARTMENT, "per": ["department"]}).post(
        "/api/ai/scenes/terminal/runs", json={"content": "看看"}
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "AI_FORBIDDEN"
    assert submitted.calls == []


def test_unknown_scene_is_a_400(submitted) -> None:
    response = _client(ADMIN).post(
        "/api/ai/scenes/terminal2/runs", json={"content": "看看"}
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == conversations.SCENE_BAD
    assert submitted.calls == []


def test_payload_limits_are_enforced(submitted) -> None:
    response = _client(ADMIN).post(
        "/api/ai/scenes/terminal/runs", json={"content": "x" * 4001}
    )

    assert response.status_code == 422
    assert submitted.calls == []
