"""写工具的 HITL：提案 → 确认执行 / 驳回 / 过期 / 越权 / 参数被改。

模型**不能**直接写库。这里的每一条都对应一类真实风险：

* 确认执行 —— 只有用户点头之后 ``members.update_fields`` 才会被调用；
* 驳回 —— 不产生任何写；
* 过期 —— 「10 分钟前看到的那条改动」不能半小时后执行（410）；
* actor 不符 —— 别人不能替你确认你提出的改动（403）；
* args_hash 不符 —— 「批准的那份参数」与「要执行的那份」必须同一份（409）；
* 权限不符 —— **执行层**再判一次，模型自称"已批准"不算凭据（403 + ``denied``）。

全离线：仓储是内存桩，时长重算被替换成空实现（它只影响缓存，不影响本层判定）。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from db import backends
from services.agent import executor, proposal, tool_impl, tools
from services.agent.tool_impl import AgentError
from services.agent.tools import TOOLS, WRITE
from services.ai import config as ai_config
from services.ai import credentials, providers, rate_limit, routing
from util import agent_unlock, dual_auth

ADMIN_ID = "000000000000000000000001"
STUDENT_ID = "000000000000000000000004"
ADMIN = {"id": ADMIN_ID, "per": ["admin"]}
STUDENT = {"id": STUDENT_ID, "per": ["student"]}
ACTIVITY = "64b7f0c2e1a3b4c5d6e7fc01"
MEMBER = "64b7f0c2e1a3b4c5d6e7fc02"
MEMBER_USER = "64b7f0c2e1a3b4c5d6e7fc03"
MODEL = "gpt-x"


@pytest.fixture(autouse=True)
def _agent_unlocked():
    """确认/驳回的 HTTP 面现在有 30 分钟密码门：本文件验证的是提案语义，门禁由
    ``test_agent_unlock`` 守着，所以把两个参与者预置成已解锁。"""
    asyncio.run(agent_unlock.clear_state())
    asyncio.run(agent_unlock.mark_unlocked(ADMIN_ID))
    asyncio.run(agent_unlock.mark_unlocked(STUDENT_ID))
    yield
    asyncio.run(agent_unlock.clear_state())


class FakeAgentEventsRepo:
    """内存版 ``AIAgentEventsRepo``：CAS 语义（``from_status``）逐条实现。"""

    def __init__(self) -> None:
        self.rows: list[dict] = []

    async def insert(self, **fields) -> str:
        row = {
            "request_id": None,
            "round_no": None,
            "tool_name": None,
            "tool_kind": None,
            "args_hash": None,
            "args_summary": None,
            "args_payload": None,
            "preview": None,
            "result_summary": None,
            "status": "executed",
            "actor_oid": None,
            "approver_oid": None,
            "confirmed_at": None,
            "created_at": datetime.now(),
        }
        row.update(fields)
        row["_id"] = row["oid"]
        self.rows.append(row)
        return row["oid"]

    async def get(self, oid: str):
        for row in self.rows:
            if row["oid"] == oid:
                return dict(row)
        return None

    async def update_status(
        self,
        oid,
        status,
        *,
        from_status="proposed",
        result_summary=None,
        approver_oid=None,
        confirmed_at=None,
    ) -> bool:
        for row in self.rows:
            if row["oid"] == oid and row["status"] == from_status:
                row["status"] = status
                if result_summary is not None:
                    row["result_summary"] = result_summary
                if approver_oid is not None:
                    row["approver_oid"] = approver_oid
                if confirmed_at is not None:
                    row["confirmed_at"] = confirmed_at
                return True
        return False

    async def update_status_unconditional(self, oid, status, *, result_summary=None) -> bool:
        for row in self.rows:
            if row["oid"] == oid:
                row["status"] = status
                row["result_summary"] = result_summary
                return True
        return False

    async def list_for_request(self, request_id: str):
        return [row for row in self.rows if row.get("request_id") == request_id]

    async def list_by_actor(self, actor_oid, *, status=None, limit=20):
        rows = [row for row in self.rows if row.get("actor_oid") == actor_oid]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        rows.sort(key=lambda row: row.get("created_at") or datetime.min, reverse=True)
        return [dict(row) for row in rows[:limit]]


class FakeActivitiesRepo:
    async def get(self, oid: str):
        if oid != ACTIVITY:
            return None
        return {"_id": ACTIVITY, "name": "图书馆志愿", "type": "hybrid"}


class FakeMembersRepo:
    def __init__(self) -> None:
        self.updates: list[tuple[str, dict]] = []
        self.row = {
            "_id": MEMBER,
            "activity": ACTIVITY,
            "member": MEMBER_USER,
            "mode": "on-campus",
            "duration": 3.0,
            "status": "pending",
        }

    async def get_by_oid(self, oid: str):
        return dict(self.row) if oid == MEMBER else None

    async def update_fields(self, oid: str, fields: dict) -> None:
        self.updates.append((oid, dict(fields)))
        self.row.update(fields)


class FakeAuditRepo:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    async def insert(self, **fields) -> str:
        self.rows.append(fields)
        return "a" * 24


class FakeQuotaRepo:
    async def get(self, user_oid, period, kind):
        return None

    async def consume(
        self, user_oid, period, kind, amount, default_limit, want_state=True
    ):
        return True, amount, default_limit


def _entry() -> dict:
    return {
        "key": "shared:64b7f0c2e1a3b4c5d6e7fc11",
        "scope": "shared",
        "owner_oid": credentials.SHARED_OWNER,
        "name": "lab",
        "kind": "openai_compat",
        "base_url": "https://lab.example.com/v1",
        "models": [MODEL],
        "api_key_cipher": "cipher",
        "api_key": None,
        "key_hint": "cdef",
        "enabled": True,
        "oid": "64b7f0c2e1a3b4c5d6e7fc11",
        "created_at": None,
        "updated_at": None,
        "expires_at": None,
    }


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, quiet_ai_governance):
    credentials._cache.clear()
    routing.reset_state()
    rate_limit.reset_state()
    routing._semaphore = None
    routing._semaphore_size = 0

    async def noop(*_args, **_kwargs):
        return None

    # 时长重算只影响缓存：替换掉它，本文件就不需要 time 仓储。
    monkeypatch.setattr(tool_impl, "calculate_user_time", noop)
    yield
    credentials._cache.clear()
    routing.reset_state()
    rate_limit.reset_state()
    routing._semaphore = None
    routing._semaphore_size = 0


@pytest.fixture
def env(monkeypatch):
    events = FakeAgentEventsRepo()
    members = FakeMembersRepo()
    monkeypatch.setattr(
        backends, "get_ai_agent_events_repo", lambda session=None: events
    )
    monkeypatch.setattr(backends, "get_activities_repo", lambda session=None: FakeActivitiesRepo())
    monkeypatch.setattr(backends, "get_members_repo", lambda session=None: members)
    return events, members


def _write_tool():
    return TOOLS["set_member_status"]


async def _propose(user=ADMIN, **arguments):
    args = {
        "activity_id": ACTIVITY,
        "member_id": MEMBER,
        "status": "effective",
    }
    args.update(arguments)
    return await proposal.propose(
        user=user,
        tool=_write_tool(),
        arguments=args,
        request_id="req-1",
        round_no=1,
    )


# ---------------------------------------------------------------------------
# 提案
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_proposal_records_a_hash_of_the_stored_arguments(env) -> None:
    events, members = env

    created = await _propose()

    assert members.updates == []  # 提案不执行任何写
    assert created["tool_name"] == "set_member_status"
    assert created["expires_at"]
    assert created["requires_dual_auth"] is False
    row = events.rows[0]
    assert row["status"] == "proposed"
    assert row["tool_kind"] == WRITE
    assert row["actor_oid"] == ADMIN_ID
    assert row["round_no"] == 1
    assert row["request_id"] == "req-1"
    # hash 覆盖的是**完整参数（ARGS_PAYLOAD）**：确认时要能用同一份重算。
    assert row["args_hash"] == hashlib.sha256(
        row["args_payload"].encode("utf-8")
    ).hexdigest()
    assert json.loads(row["args_payload"])["status"] == "effective"
    # ARGS_SUMMARY 只是展示副本，与 payload 同源。
    assert row["args_summary"] == row["args_payload"]
    assert json.loads(row["preview"])["kind"] == "args"
    assert created["preview"]["kind"] == "args"


@pytest.mark.asyncio
async def test_read_tool_cannot_be_proposed(env) -> None:
    with pytest.raises(AgentError) as excinfo:
        await proposal.propose(
            user=ADMIN,
            tool=TOOLS["search_activities"],
            arguments={"query": "x"},
            request_id="req-1",
            round_no=1,
        )
    assert excinfo.value.code == "AI_AGENT_NOT_WRITE_TOOL"


# ---------------------------------------------------------------------------
# 确认
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_confirm_executes_the_write_with_the_users_permission(env) -> None:
    events, members = env
    created = await _propose()

    result = await proposal.confirm(oid=created["oid"], user=ADMIN)

    assert members.updates == [(MEMBER, {"status": "effective"})]
    assert result["status"] == "confirmed"
    assert "effective" in result["result_summary"]
    assert events.rows[0]["status"] == "confirmed"


@pytest.mark.asyncio
async def test_second_confirm_is_a_conflict(env) -> None:
    _events, members = env
    created = await _propose()

    await proposal.confirm(oid=created["oid"], user=ADMIN)

    with pytest.raises(AgentError) as excinfo:
        await proposal.confirm(oid=created["oid"], user=ADMIN)

    assert excinfo.value.status_code == 409
    assert excinfo.value.code == "AI_AGENT_PROPOSAL_CONFLICT"
    # 只执行过一次
    assert len(members.updates) == 1


@pytest.mark.asyncio
async def test_duration_tool_updates_the_duration_and_keeps_the_mode(env) -> None:
    _events, members = env
    created = await proposal.propose(
        user=ADMIN,
        tool=TOOLS["update_member_duration"],
        arguments={"activity_id": ACTIVITY, "member_id": MEMBER, "duration": 6.5},
        request_id="req-1",
        round_no=1,
    )

    await proposal.confirm(oid=created["oid"], user=ADMIN)

    assert members.updates == [(MEMBER, {"duration": 6.5, "mode": "on-campus"})]


# ---------------------------------------------------------------------------
# 拒绝 / 过期 / 越权 / 篡改
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reject_changes_no_data(env) -> None:
    events, members = env
    created = await _propose()

    result = await proposal.reject(oid=created["oid"], user=ADMIN)

    assert result == {"status": "rejected"}
    assert members.updates == []
    assert events.rows[0]["status"] == "rejected"

    with pytest.raises(AgentError) as excinfo:
        await proposal.confirm(oid=created["oid"], user=ADMIN)
    assert excinfo.value.status_code == 409


@pytest.mark.asyncio
async def test_expired_proposal_is_410_and_marked_expired(env) -> None:
    events, members = env
    created = await _propose()
    events.rows[0]["created_at"] = datetime.now() - timedelta(
        seconds=ai_config.agent_proposal_ttl_seconds() + 1
    )

    with pytest.raises(AgentError) as excinfo:
        await proposal.confirm(oid=created["oid"], user=ADMIN)

    assert excinfo.value.status_code == 410
    assert excinfo.value.code == "AI_AGENT_PROPOSAL_EXPIRED"
    assert events.rows[0]["status"] == "expired"
    assert members.updates == []


@pytest.mark.asyncio
async def test_another_user_cannot_confirm_or_reject(env) -> None:
    events, members = env
    created = await _propose()

    for call in (proposal.confirm, proposal.reject):
        with pytest.raises(AgentError) as excinfo:
            await call(oid=created["oid"], user=STUDENT)
        assert excinfo.value.status_code == 403
        assert excinfo.value.code == "AI_AGENT_PROPOSAL_FORBIDDEN"

    assert events.rows[0]["status"] == "proposed"
    assert members.updates == []


@pytest.mark.asyncio
async def test_missing_proposal_is_404(env) -> None:
    with pytest.raises(AgentError) as excinfo:
        await proposal.confirm(oid="0" * 32, user=ADMIN)
    assert excinfo.value.status_code == 404


@pytest.mark.asyncio
async def test_tampered_arguments_are_refused(env) -> None:
    events, members = env
    created = await _propose()
    # 模拟「批准后参数被改」：payload 文本变了但 hash 没变。
    events.rows[0]["args_payload"] = json.dumps(
        {"activity_id": ACTIVITY, "member_id": MEMBER, "status": "refused"},
        sort_keys=True,
        separators=(",", ":"),
    )

    with pytest.raises(AgentError) as excinfo:
        await proposal.confirm(oid=created["oid"], user=ADMIN)

    assert excinfo.value.status_code == 409
    assert excinfo.value.code == "AI_AGENT_PROPOSAL_TAMPERED"
    assert members.updates == []


@pytest.mark.asyncio
async def test_legacy_row_without_payload_falls_back_to_the_summary(env) -> None:
    """V034 之前的老行没有 ``ARGS_PAYLOAD``：回落到 ``ARGS_SUMMARY`` 校验。

    活动域已落库的提案不能因为一次 DDL 就全部失效——那会把「等待确认」变成
    「永远无法确认」。
    """
    events, members = env
    created = await _propose()

    row = events.rows[0]
    legacy_text = row["args_payload"]
    row["args_payload"] = None
    row["args_summary"] = legacy_text
    row["args_hash"] = hashlib.sha256(legacy_text.encode("utf-8")).hexdigest()

    result = await proposal.confirm(oid=created["oid"], user=ADMIN)

    assert result["status"] == "confirmed"
    assert members.updates == [(MEMBER, {"status": "effective"})]


@pytest.mark.asyncio
async def test_missing_arguments_row_is_tampered(env) -> None:
    events, _members = env
    created = await _propose()
    events.rows[0]["args_payload"] = None
    events.rows[0]["args_summary"] = None

    with pytest.raises(AgentError) as excinfo:
        await proposal.confirm(oid=created["oid"], user=ADMIN)
    assert excinfo.value.code == "AI_AGENT_PROPOSAL_TAMPERED"


@pytest.mark.asyncio
async def test_over_long_arguments_are_summarised_not_rejected(monkeypatch, env) -> None:
    """超长参数不再拒绝建提案：完整参数进 CLOB，summary 只留脱敏后的截断。"""
    events, _members = env

    async def impl(_user, note):
        return {"wrote": len(note)}

    tool = tools.ToolDef(
        name="fake_big_write",
        description="x",
        kind=WRITE,
        params={
            "type": "object",
            "properties": {"note": {"type": "string", "description": "x"}},
            "required": ["note"],
        },
        permission=tools._always,
        impl=impl,
    )
    monkeypatch.setitem(tools.TOOLS, "fake_big_write", tool)
    note = "x" * 4000

    created = await proposal.propose(
        user=ADMIN,
        tool=tool,
        arguments={"note": note},
        request_id="req-long",
        round_no=1,
    )

    row = events.rows[0]
    # 展示列不落正文：超过 200 字符的字符串换成长度标注（契约 §4.2）。
    assert note not in row["args_summary"]
    assert "<len 4000>" in row["args_summary"]
    assert len(row["args_summary"]) <= proposal.ARGS_SUMMARY_MAX
    assert note in row["args_payload"]  # 全文可复原
    assert row["args_hash"] == hashlib.sha256(
        row["args_payload"].encode("utf-8")
    ).hexdigest()
    assert created["preview"] == {"kind": "args", "args": {"note": note}}

    # 截断只影响展示：确认时用 payload 复原并执行（不看 summary）。
    result = await proposal.confirm(oid=created["oid"], user=ADMIN)
    assert result["result_summary"] == json.dumps(
        {"wrote": len(note)}, ensure_ascii=False
    )


def test_arguments_summary_is_truncated_not_dropped() -> None:
    """超过 500 的**展示**参数被截断加 ``…``（不再拒绝建提案）。"""
    long_text = proposal._display_summary("x" * 4000)
    assert len(long_text) == proposal.ARGS_SUMMARY_MAX
    assert long_text.endswith("…")
    assert proposal._display_summary("short") == "short"


@pytest.mark.asyncio
async def test_prepare_supplies_the_summary_and_preview(monkeypatch, env) -> None:
    """``prepare`` 存在时：summary / preview 来自它，落库的是 JSON 文本。"""
    events, _members = env

    async def impl(_user, path):
        return {"ok": path}

    async def prepare(_user, path):
        return proposal.ProposalDraft(
            summary=f"写入 1 行到 {path}", preview={"kind": "new_file", "path": path}
        )

    tool = tools.ToolDef(
        name="fake_fs_write",
        description="x",
        kind=WRITE,
        params={
            "type": "object",
            "properties": {"path": {"type": "string", "description": "x"}},
            "required": ["path"],
        },
        permission=tools._always,
        impl=impl,
        prepare=prepare,
    )
    monkeypatch.setitem(tools.TOOLS, "fake_fs_write", tool)

    created = await proposal.propose(
        user=ADMIN,
        tool=tool,
        arguments={"path": "/etc/x.conf"},
        request_id="req-draft",
        round_no=1,
    )

    assert created["summary"] == "写入 1 行到 /etc/x.conf"
    assert created["preview"] == {"kind": "new_file", "path": "/etc/x.conf"}
    assert json.loads(events.rows[0]["preview"]) == created["preview"]


@pytest.mark.asyncio
async def test_permission_is_rechecked_at_execution_time(env) -> None:
    """执行层二次校验：提案是学生生成的，确认时仍然没有写权限 → denied。"""
    events, members = env
    created = await _propose(user={"id": STUDENT_ID, "per": ["student"]})

    with pytest.raises(AgentError) as excinfo:
        await proposal.confirm(oid=created["oid"], user=STUDENT)

    assert excinfo.value.status_code == 403
    assert excinfo.value.code == "AI_AGENT_PROPOSAL_DENIED"
    assert events.rows[0]["status"] == "denied"
    assert members.updates == []


@pytest.mark.asyncio
async def test_unknown_tool_in_a_stored_proposal_is_refused(env) -> None:
    events, _members = env
    created = await _propose()
    events.rows[0]["tool_name"] = "delete_activity"

    with pytest.raises(AgentError) as excinfo:
        await proposal.confirm(oid=created["oid"], user=ADMIN)

    assert excinfo.value.code == "AI_AGENT_PROPOSAL_UNKNOWN_TOOL"
    assert excinfo.value.status_code == 409


@pytest.mark.asyncio
async def test_expiry_cas_never_overwrites_a_confirmed_row(env) -> None:
    """终态不可被覆盖：过期与确认撞车时，后到的一方影响 0 行。"""
    events, _members = env
    created = await _propose()

    await proposal.confirm(oid=created["oid"], user=ADMIN)
    assert events.rows[0]["status"] == "confirmed"
    assert events.rows[0]["confirmed_at"] is not None

    # 模拟「确认之后才跑到的清理」：CAS 从 proposed 出发，影响 0 行。
    await proposal._mark_expired(created["oid"])
    assert events.rows[0]["status"] == "confirmed"


@pytest.mark.asyncio
async def test_confirmed_rows_cannot_be_expired_through_load(env) -> None:
    """即便时间上已经超时，一条 confirmed 的提案也只能回 409，而不是被改写过。"""
    events, _members = env
    created = await _propose()
    await proposal.confirm(oid=created["oid"], user=ADMIN)
    events.rows[0]["created_at"] = datetime.now() - timedelta(
        seconds=ai_config.agent_proposal_ttl_seconds() + 1
    )

    with pytest.raises(AgentError) as excinfo:
        await proposal.confirm(oid=created["oid"], user=ADMIN)
    assert excinfo.value.status_code == 409
    assert events.rows[0]["status"] == "confirmed"


# ---------------------------------------------------------------------------
# 双人授权（运维写工具）
# ---------------------------------------------------------------------------


class FakeDualAuth:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []
        self.error: dual_auth.DualAuthError | None = None
        self.approver = "000000000000000000000009"

    async def __call__(self, operator, code, *, action):
        self.calls.append((str(operator.get("id")), code, action))
        if self.error is not None:
            raise self.error
        return self.approver


@pytest.fixture
def dual_tool(monkeypatch):
    """注册一个「运维写工具」形状的假工具（TOTP + prepare + guard）。"""
    executed: list[dict] = []

    async def impl(_user, path):
        executed.append({"path": path})
        return {"written": path}

    async def prepare(_user, path):
        return proposal.ProposalDraft(
            summary=f"写入 {path}", preview={"kind": "new_file", "path": path}
        )

    guarded: list[str] = []

    async def guard(user):
        guarded.append(str(user.get("id")))

    tool = tools.ToolDef(
        name="fake_fs_write",
        description="x",
        kind=WRITE,
        params={
            "type": "object",
            "properties": {"path": {"type": "string", "description": "x"}},
            "required": ["path"],
        },
        permission=tools._always,
        impl=impl,
        prepare=prepare,
        guard=guard,
        dual_auth_action="fs_write",
    )
    monkeypatch.setitem(tools.TOOLS, "fake_fs_write", tool)
    return tool, executed, guarded


@pytest.mark.asyncio
async def test_dual_auth_tool_requires_a_code(monkeypatch, env, dual_tool) -> None:
    tool, executed, _guarded = dual_tool

    created = await proposal.propose(
        user=ADMIN,
        tool=tool,
        arguments={"path": "/etc/x.conf"},
        request_id="req-totp",
        round_no=1,
    )
    assert created["requires_dual_auth"] is True

    with pytest.raises(AgentError) as excinfo:
        await proposal.confirm(oid=created["oid"], user=ADMIN)

    assert excinfo.value.status_code == 422
    assert excinfo.value.code == "AI_AGENT_TOTP_REQUIRED"
    assert executed == []


@pytest.mark.asyncio
async def test_dual_auth_rejection_is_translated(monkeypatch, env, dual_tool) -> None:
    tool, executed, _guarded = dual_tool
    fake = FakeDualAuth()
    fake.error = dual_auth.DualAuthError(403, "动态码无效或已过期")
    monkeypatch.setattr(dual_auth, "require_dual_auth", fake)

    created = await proposal.propose(
        user=ADMIN,
        tool=tool,
        arguments={"path": "/etc/x.conf"},
        request_id="req-totp",
        round_no=1,
    )

    with pytest.raises(AgentError) as excinfo:
        await proposal.confirm(oid=created["oid"], user=ADMIN, totp_code="000000")

    assert excinfo.value.status_code == 403
    assert excinfo.value.code == "AI_AGENT_TOTP_INVALID"
    assert excinfo.value.message == "动态码无效或已过期"
    assert executed == []
    # 错码不烧提案：用户换一个正确的码还能继续确认。
    assert env[0].rows[0]["status"] == "proposed"


@pytest.mark.asyncio
async def test_dual_auth_success_records_the_approver(monkeypatch, env, dual_tool) -> None:
    tool, executed, guarded = dual_tool
    fake = FakeDualAuth()
    monkeypatch.setattr(dual_auth, "require_dual_auth", fake)

    created = await proposal.propose(
        user=ADMIN,
        tool=tool,
        arguments={"path": "/etc/x.conf"},
        request_id="req-totp",
        round_no=1,
    )

    result = await proposal.confirm(
        oid=created["oid"], user=ADMIN, totp_code="123456"
    )

    assert result["status"] == "confirmed"
    assert result["approver_oid"] == fake.approver
    assert fake.calls == [(ADMIN_ID, "123456", "fs_write")]
    assert executed == [{"path": "/etc/x.conf"}]
    # guard 在确认时现查一次（不是信 proposal 期的快照）。
    assert guarded == [ADMIN_ID]
    row = env[0].rows[0]
    assert row["status"] == "confirmed"
    assert row["approver_oid"] == fake.approver
    assert row["confirmed_at"] is not None


# ---------------------------------------------------------------------------
# 通用 DML 的表级豁免已取消
# ---------------------------------------------------------------------------


def _db_update_tool(monkeypatch):
    """把真实 ``db_update`` 换成只在内存里执行的副本。

    真实工具现在对账号/义工表直接表级拒绝，本组用例只验证提案层的授权语义，
    因此替换 impl/prepare（不绕过真实 ``dual_auth_action``）。
    """
    real = TOOLS["db_update"]
    executed: list[dict] = []

    async def impl(_user, table, key, values):
        executed.append({"table": table, "key": key, "values": values})
        return {"kind": "row_update", "table": table}

    async def prepare(_user, table, key, values):
        return proposal.ProposalDraft(
            summary=f"更新 {table} 1 行",
            preview={"kind": "row_update", "table": table, "key": key, "values": values},
        )

    async def guard(_user):
        return None

    tool = replace(real, impl=impl, prepare=prepare, guard=guard)
    monkeypatch.setitem(TOOLS, "db_update", tool)
    return tool, executed


async def _propose_db_update(tool, table: str) -> dict:
    return await proposal.propose(
        user=ADMIN,
        tool=tool,
        arguments={
            "table": table,
            "key": {"OID": "64b7f0c2e1a3b4c5d6e7fc01"},
            "values": {"NAME": "x"},
        },
        request_id="req-volunteer-db",
        round_no=1,
    )


@pytest.mark.asyncio
async def test_generic_dml_has_no_volunteer_table_exemption(monkeypatch, env) -> None:
    """义工表不再享受“本人确认即可”的豁免：提案一律要求双人动态码。"""
    tool, executed = _db_update_tool(monkeypatch)
    fake = FakeDualAuth()
    monkeypatch.setattr(dual_auth, "require_dual_auth", fake)

    created = await _propose_db_update(tool, "ACTIVITIES")

    assert created["requires_dual_auth"] is True
    with pytest.raises(AgentError) as excinfo:
        await proposal.confirm(oid=created["oid"], user=ADMIN)

    assert excinfo.value.status_code == 422
    assert excinfo.value.code == "AI_AGENT_TOTP_REQUIRED"
    assert executed == []
    assert fake.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("table", ["activities", ' "Activities" ', "GROUPS"])
async def test_dml_table_name_no_longer_changes_authorization(
    monkeypatch, env, table
) -> None:
    tool, _executed = _db_update_tool(monkeypatch)
    created = await _propose_db_update(tool, table)
    assert created["requires_dual_auth"] is True


@pytest.mark.asyncio
async def test_platform_table_dml_still_requires_dual_auth(monkeypatch, env) -> None:
    tool, executed = _db_update_tool(monkeypatch)
    fake = FakeDualAuth()
    monkeypatch.setattr(dual_auth, "require_dual_auth", fake)

    created = await _propose_db_update(tool, "USERS")

    assert created["requires_dual_auth"] is True
    with pytest.raises(AgentError) as excinfo:
        await proposal.confirm(oid=created["oid"], user=ADMIN)

    assert excinfo.value.status_code == 422
    assert excinfo.value.code == "AI_AGENT_TOTP_REQUIRED"
    assert executed == []
    assert fake.calls == []


@pytest.mark.asyncio
async def test_list_fails_closed_when_arguments_are_corrupt(monkeypatch, env) -> None:
    tool, _executed = _db_update_tool(monkeypatch)
    await _propose_db_update(tool, "ACTIVITIES")
    env[0].rows[0]["args_payload"] = "{not-json"

    items = await proposal.list_mine(user=ADMIN, status="proposed", limit=20)

    assert items[0]["requires_dual_auth"] is True


# ---------------------------------------------------------------------------
# 列表 / 时间线
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_mine_only_returns_the_callers_rows(env) -> None:
    _events, _members = env
    mine = await _propose()
    await proposal.propose(
        user={"id": STUDENT_ID, "per": ["student"]},
        tool=_write_tool(),
        arguments={
            "activity_id": ACTIVITY,
            "member_id": MEMBER,
            "status": "refused",
        },
        request_id="req-2",
        round_no=1,
    )

    items = await proposal.list_mine(user=ADMIN, status="proposed", limit=20)

    assert [item["oid"] for item in items] == [mine["oid"]]
    assert items[0]["requires_dual_auth"] is False
    assert items[0]["status"] == "proposed"
    assert items[0]["expires_at"]


@pytest.mark.asyncio
async def test_list_mine_flips_expired_rows(env) -> None:
    events, _members = env
    created = await _propose()
    events.rows[0]["created_at"] = datetime.now() - timedelta(
        seconds=ai_config.agent_proposal_ttl_seconds() + 1
    )

    items = await proposal.list_mine(user=ADMIN, status="proposed", limit=20)

    assert items == []
    assert events.rows[0]["status"] == "expired"


@pytest.mark.asyncio
async def test_run_events_are_scoped_to_the_caller(env) -> None:
    events, _members = env
    await _propose()
    events.rows.append(
        {
            **events.rows[0],
            "oid": "f" * 32,
            "_id": "f" * 32,
            "actor_oid": STUDENT_ID,
        }
    )

    mine = await proposal.run_events(request_id="req-1", user=ADMIN)
    theirs = await proposal.run_events(
        request_id="req-1", user={"id": STUDENT_ID, "per": ["student"]}
    )

    assert [row["oid"] for row in mine] == [events.rows[0]["oid"]]
    assert [row["oid"] for row in theirs] == ["f" * 32]


# ---------------------------------------------------------------------------
# 执行器侧的写工具路径
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_executor_turns_a_write_call_into_a_proposal(monkeypatch, env) -> None:
    events, members = env
    audit = FakeAuditRepo()
    monkeypatch.setattr(backends, "get_ai_call_logs_repo", lambda session=None: audit)
    monkeypatch.setattr(backends, "get_ai_quota_repo", lambda session=None: FakeQuotaRepo())

    async def catalog(_user_oid):
        return [_entry()]

    async def allowed_models(_user_oid):
        return {MODEL: "lab"}

    async def resolve_key(_entry):
        return "sk-unit-test-0123456789abcdef"

    monkeypatch.setattr(credentials, "catalog", catalog)
    monkeypatch.setattr(credentials, "resolve_api_key", resolve_key)
    monkeypatch.setattr(ai_config, "allowed_models_for", allowed_models)

    responses = [
        {
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "call_w",
                                "type": "function",
                                "function": {
                                    "name": "set_member_status",
                                    "arguments": json.dumps(
                                        {
                                            "activity_id": ACTIVITY,
                                            "member_id": MEMBER,
                                            "status": "effective",
                                        }
                                    ),
                                },
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        },
        {
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": "已生成待确认提案。",
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        },
    ]

    async def upstream(_payload, _entry, _key):
        return responses.pop(0)

    monkeypatch.setattr(providers, "chat_completion", upstream)

    result = await executor.run(
        user=ADMIN,
        messages=[{"role": "user", "content": "把这个人审核通过"}],
        model=MODEL,
    )

    assert result["content"] == "已生成待确认提案。"
    assert len(result["proposals"]) == 1
    assert result["proposals"][0]["tool_name"] == "set_member_status"
    assert result["events"][0]["status"] == "proposed"
    # 关键：模型调用写工具**不会**写库
    assert members.updates == []
    assert events.rows[0]["status"] == "proposed"

    # 用户确认之后才真的写
    await proposal.confirm(oid=result["proposals"][0]["oid"], user=ADMIN)
    assert members.updates == [(MEMBER, {"status": "effective"})]


# ---------------------------------------------------------------------------
# HTTP 信封（router 层只做协议翻译，不重复验业务）
# ---------------------------------------------------------------------------


def test_router_uses_the_ai_error_envelope(monkeypatch) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from routers import ai_agent_router
    from util.object_id import get_current_user

    app = FastAPI()
    app.include_router(ai_agent_router.router, prefix="/api/ai/agent")
    app.dependency_overrides[get_current_user] = lambda: STUDENT
    client = TestClient(app)

    response = client.post(f"/api/ai/agent/proposals/{'a' * 32}/confirm")

    assert response.status_code == 403
    body = response.json()
    assert body["error"]["code"] == "AI_FORBIDDEN"
    assert body["error"]["type"] == "invalid_request_error"
    assert response.headers["X-AI-Request-Id"]


def test_router_gate_rejection_writes_an_audit_row(monkeypatch) -> None:
    """门禁 403 也要有行：否则「谁在没权限时反复试探 agent」看不见。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from routers import ai_agent_router
    from util.object_id import get_current_user

    audit = FakeAuditRepo()
    monkeypatch.setattr(backends, "get_ai_call_logs_repo", lambda session=None: audit)

    app = FastAPI()
    app.include_router(ai_agent_router.router, prefix="/api/ai/agent")
    app.dependency_overrides[get_current_user] = lambda: STUDENT
    client = TestClient(app)

    responses = []
    responses.append(client.post(f"/api/ai/agent/proposals/{'a' * 32}/confirm"))
    responses.append(client.post(f"/api/ai/agent/proposals/{'a' * 32}/reject"))
    responses.append(client.get("/api/ai/agent/proposals"))

    for response in responses:
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "AI_FORBIDDEN"

    assert len(audit.rows) == len(responses)
    for row in audit.rows:
        assert row["capability"] == "chat"
        assert row["endpoint"] == "/api/ai/agent/run"
        assert row["purpose"] == "agent"
        assert row["status"] == "rejected"
        assert row["error_code"] == "AI_FORBIDDEN"
        assert row["user_oid"] == STUDENT_ID


def test_router_maps_proposal_errors_and_adds_the_request_id(monkeypatch) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from routers import ai_agent_router
    from util.object_id import get_current_user

    async def _expired(*, oid, user, totp_code=None):
        raise AgentError("AI_AGENT_PROPOSAL_EXPIRED", "proposal has expired", 410)

    monkeypatch.setattr(ai_agent_router.proposal, "confirm", _expired)

    app = FastAPI()
    app.include_router(ai_agent_router.router, prefix="/api/ai/agent")
    app.dependency_overrides[get_current_user] = lambda: ADMIN
    client = TestClient(app)

    response = client.post(f"/api/ai/agent/proposals/{'a' * 32}/confirm")

    assert response.status_code == 410
    assert response.json()["error"]["code"] == "AI_AGENT_PROPOSAL_EXPIRED"
    assert response.headers["X-AI-Request-Id"]

    # 形状不对的 oid 直接 404，不查库
    bad = client.post("/api/ai/agent/proposals/not-an-oid/confirm")
    assert bad.status_code == 404
    assert bad.json()["error"]["code"] == "AI_AGENT_PROPOSAL_NOT_FOUND"


def test_router_passes_the_totp_code_through(monkeypatch) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from routers import ai_agent_router
    from util.object_id import get_current_user

    seen: dict = {}

    async def _confirm(*, oid, user, totp_code=None):
        seen["totp_code"] = totp_code
        return {"status": "confirmed", "result_summary": "{}", "approver_oid": "x"}

    monkeypatch.setattr(ai_agent_router.proposal, "confirm", _confirm)

    app = FastAPI()
    app.include_router(ai_agent_router.router, prefix="/api/ai/agent")
    app.dependency_overrides[get_current_user] = lambda: ADMIN
    client = TestClient(app)

    assert client.post(f"/api/ai/agent/proposals/{'a' * 32}/confirm").status_code == 200
    assert seen["totp_code"] is None
    client.post(
        f"/api/ai/agent/proposals/{'a' * 32}/confirm", json={"totp_code": "123456"}
    )
    assert seen["totp_code"] == "123456"


def test_router_proposals_endpoint_is_scoped_and_bounded(monkeypatch) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from routers import ai_agent_router
    from util.object_id import get_current_user

    seen: dict = {}

    async def _list_mine(*, user, status=None, limit=20):
        seen.update({"user": user, "status": status, "limit": limit})
        return [{"oid": "a" * 32, "tool_name": "x", "status": "proposed"}]

    monkeypatch.setattr(ai_agent_router.proposal, "list_mine", _list_mine)

    app = FastAPI()
    app.include_router(ai_agent_router.router, prefix="/api/ai/agent")
    app.dependency_overrides[get_current_user] = lambda: ADMIN
    client = TestClient(app)

    response = client.get("/api/ai/agent/proposals")
    assert response.status_code == 200
    assert response.json()["proposals"][0]["oid"] == "a" * 32
    assert seen == {"user": ADMIN, "status": "proposed", "limit": 20}

    # limit > 50 由 Query 校验挡掉（契约：limit ≤ 50）
    assert client.get("/api/ai/agent/proposals?limit=500").status_code == 422


def test_router_catches_unexpected_errors_as_openai_shaped_500(monkeypatch) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from routers import ai_agent_router
    from util.object_id import get_current_user

    async def _boom(*, oid, user, totp_code=None):
        raise RuntimeError("boom")

    monkeypatch.setattr(ai_agent_router.proposal, "confirm", _boom)

    audit = FakeAuditRepo()
    monkeypatch.setattr(backends, "get_ai_call_logs_repo", lambda session=None: audit)

    app = FastAPI()
    app.include_router(ai_agent_router.router, prefix="/api/ai/agent")
    app.dependency_overrides[get_current_user] = lambda: ADMIN
    client = TestClient(app)

    response = client.post(f"/api/ai/agent/proposals/{'a' * 32}/confirm")

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "AI_AGENT_INTERNAL_ERROR"
    assert response.headers["X-AI-Request-Id"]
    assert audit.rows[0]["status"] == "error"
