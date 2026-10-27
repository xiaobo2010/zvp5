"""平台运维工具（日志 / DB 诊断 / 备份 / 调度器 / 封禁解封）与「直接执行」写工具。

离线：没有 ADB、没有上游、没有调度器。验的是工具层与 executor 自己负责的部分：

* 注册表形状（5 读 + 3 写，写工具 ``direct=True`` 且带 guard、无预览/TOTP）；
* ``direct`` 的三条注册期约束（只能写工具 / 名字在允许清单 / 必须有 guard）；
* 读工具的有界性（日志 50 行、时间窗 ≤90 天、设备分页 ≤100）；
* 封禁护栏（不能封自己、不能封管理员、目标不存在 404、guard 失败仍然拒）；
* executor：direct 写工具**直接执行**并落 ``executed`` 事件（``args_summary``
  带 ``direct``），既有写工具仍然只出提案（零行为变化）。
"""

from __future__ import annotations

import contextlib
import json
import sys
from dataclasses import replace
from datetime import datetime
from types import SimpleNamespace

import pytest

from db import backends
from services.agent import executor, tools, tools_platform
from services.agent.ops_tools import WRITE_AUTH_ACTION
from services.agent.tool_impl import AgentError

ADMIN = {"id": "000000000000000000000001", "per": ["admin"]}
OTHER_ADMIN = {"id": "000000000000000000000009", "per": ["admin"]}
STUDENT = {"id": "000000000000000000000002", "per": ["student"]}

PLATFORM_TOOLS = {
    "platform_logs": tools.READ,
    "db_diagnostics": tools.READ,
    "backup_status": tools.READ,
    "scheduler_status": tools.READ,
    "device_ban_list": tools.READ,
    "user_ban": tools.WRITE,
    "user_unban": tools.WRITE,
    "device_unban": tools.WRITE,
}


def _tool(name: str) -> tools.ToolDef:
    return tools.TOOLS[name]


async def _impl(name: str, **kwargs):
    return await _tool(name).impl(ADMIN, **kwargs)


# ---------------------------------------------------------------------------
# 替身仓储
# ---------------------------------------------------------------------------


def _user(oid: str, business_id: str, roles: list[str], name: str = "") -> dict:
    return {
        "_id": oid,
        "id": business_id,
        "name": name or business_id,
        "roles": list(roles),
        "password": None,
    }


class FakeUsersRepo:
    def __init__(self, docs: dict[str, dict]) -> None:
        self.docs = docs
        self.bans: list[dict] = []
        self.cleared: list[str] = []

    async def get_by_oid(self, oid: str):
        return self.docs.get(oid)

    async def find_by_business_id(self, business_id: str):
        return next(
            (doc for doc in self.docs.values() if doc.get("id") == business_id), None
        )

    async def set_ban(self, oid, *, reason, until, source, rule_code):
        self.bans.append(
            {
                "oid": oid,
                "reason": reason,
                "until": until,
                "source": source,
                "rule_code": rule_code,
            }
        )
        doc = self.docs[oid]
        doc["banned"] = True
        doc["ban_reason"] = reason
        doc["ban_source"] = source
        if until is not None:
            doc["ban_until"] = until
        else:
            doc.pop("ban_until", None)

    async def clear_ban(self, oid):
        self.cleared.append(oid)
        doc = self.docs[oid]
        for key in ("banned", "ban_reason", "ban_until", "ban_source", "ban_rule"):
            doc.pop(key, None)


class FakeBanRecordsRepo:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    async def insert(
        self,
        user_oid,
        *,
        source,
        rule_code=None,
        reason=None,
        permanent=False,
        started_at=None,
        expires_at=None,
        operator_oid=None,
    ):
        self.rows.append(
            {
                "user_oid": user_oid,
                "source": source,
                "rule_code": rule_code,
                "reason": reason,
                "permanent": permanent,
                "started_at": started_at,
                "expires_at": expires_at,
                "operator_oid": operator_oid,
            }
        )
        return "rec-1"


class FakeAppLogsRepo:
    """同时承担「日志查询」与「审计写入」两个角色。"""

    def __init__(self) -> None:
        self.pages: list[dict] = []
        self.inserted: list[dict] = []
        self.rows: list[dict] = []
        self.total = 0

    async def list_page(self, **kwargs):
        self.pages.append(kwargs)
        return list(self.rows), self.total

    async def insert(self, url, user, data, timestamp, ltype, level, xuehai_id):
        self.inserted.append(
            {
                "url": url,
                "user": user,
                "data": data,
                "type": ltype,
                "level": level,
                "xuehai_id": xuehai_id,
            }
        )


class FakeDeviceBansRepo:
    def __init__(self, rows: dict[str, dict] | None = None) -> None:
        self.rows = dict(rows or {})
        self.deleted: list[str] = []

    async def get(self, xuehai_id):
        return self.rows.get(xuehai_id)

    async def delete(self, xuehai_id):
        self.deleted.append(xuehai_id)
        return 1 if self.rows.pop(xuehai_id, None) is not None else 0

    async def list_page(self, page, size, query=""):
        items = list(self.rows.values())[:size]
        return items, len(self.rows)


class FakeDeviceCountersRepo:
    def __init__(self) -> None:
        self.deleted: list[str] = []
        self.counts: dict[str, int] = {}

    async def delete(self, xuehai_id):
        self.deleted.append(xuehai_id)
        return 1

    async def get_counts(self, xuehai_ids):
        return {key: self.counts[key] for key in xuehai_ids if key in self.counts}


class FakeAgentEventsRepo:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    async def insert(self, **kwargs):
        self.rows.append(kwargs)


class FakeSession:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    async def execute(self, stmt):
        return SimpleNamespace(
            all=lambda: [
                SimpleNamespace(_mapping=row) for row in self._rows
            ]
        )


@pytest.fixture
def env(monkeypatch):
    """全部仓储换成替身：单测绝不连 ADB。"""
    users = FakeUsersRepo(
        {
            "000000000000000000000001": _user(
                "000000000000000000000001", "admin001", ["admin"], "管理员甲"
            ),
            "000000000000000000000009": _user(
                "000000000000000000000009", "admin002", ["admin"], "管理员乙"
            ),
            "000000000000000000000002": _user(
                "000000000000000000000002", "20251135", ["student"], "张三"
            ),
        }
    )
    records = FakeBanRecordsRepo()
    logs = FakeAppLogsRepo()
    device_bans = FakeDeviceBansRepo(
        {"123456": {"xuehai_id": "123456", "reason": "刷接口", "until": None}}
    )
    counters = FakeDeviceCountersRepo()
    events = FakeAgentEventsRepo()

    monkeypatch.setattr(backends, "get_users_repo", lambda session=None: users)
    monkeypatch.setattr(backends, "get_ban_records_repo", lambda session=None: records)
    monkeypatch.setattr(backends, "get_applogs_repo", lambda session=None: logs)
    monkeypatch.setattr(
        backends, "get_device_bans_repo", lambda session=None: device_bans
    )
    monkeypatch.setattr(
        backends, "get_device_ban_counters_repo", lambda session=None: counters
    )
    monkeypatch.setattr(
        backends, "get_ai_agent_events_repo", lambda session=None: events
    )
    return SimpleNamespace(
        users=users,
        records=records,
        logs=logs,
        device_bans=device_bans,
        counters=counters,
        events=events,
    )


@pytest.fixture
def fake_session(monkeypatch):
    """``_top_ltypes`` 的会话替身（只回一行聚合结果，并记录语句形状）。"""
    statements: list = []

    @contextlib.asynccontextmanager
    async def _scope():
        session = FakeSession([{"ltype": "ban_change", "total": 2}])
        original = session.execute

        async def _execute(stmt):
            statements.append(stmt)
            return await original(stmt)

        session.execute = _execute  # type: ignore[assignment]
        yield session

    monkeypatch.setattr(tools_platform, "session_scope", _scope)
    return statements


# ---------------------------------------------------------------------------
# 注册表
# ---------------------------------------------------------------------------


def test_platform_tools_are_registered_with_frozen_kinds() -> None:
    assert set(PLATFORM_TOOLS) <= set(tools.TOOLS)
    for name, kind in PLATFORM_TOOLS.items():
        assert _tool(name).kind == kind, name


def test_platform_tools_are_admin_only_and_guarded() -> None:
    for name in PLATFORM_TOOLS:
        tool = _tool(name)
        assert tool.permission(ADMIN) is True, name
        assert tool.permission(STUDENT) is False, name
        assert tools.run_allowed(tool, STUDENT) is False, name


def test_platform_write_tools_are_direct_without_dual_auth() -> None:
    for name in ("user_ban", "user_unban", "device_unban"):
        tool = _tool(name)
        assert tool.direct is True, name
        assert tool.prepare is None, name
        assert tool.dual_auth_action is None, name
        assert name in tools.DIRECT_WRITE_TOOL_NAMES, name


def test_params_are_well_formed_json_schema() -> None:
    for name in PLATFORM_TOOLS:
        params = _tool(name).params
        assert params["type"] == "object", name
        assert params["additionalProperties"] is False, name
        assert set(params["required"]) <= set(params["properties"]), name
        for key, spec in params["properties"].items():
            assert spec.get("type"), f"{name}.{key} 缺少 type"
        assert _tool(name).description.strip(), name


# ---------------------------------------------------------------------------
# direct 的注册期约束
# ---------------------------------------------------------------------------


def _params() -> dict:
    return {"type": "object", "properties": {}, "required": []}


async def _noop_impl(user, **kwargs):
    return {}


async def _noop_guard(user):
    return None


def test_direct_is_refused_on_read_tools() -> None:
    with pytest.raises(ValueError, match="only write tools can be direct"):
        tools._register(
            tools.ToolDef(
                name="probe_direct_read",
                description="probe",
                kind=tools.READ,
                params=_params(),
                permission=lambda user: True,
                impl=_noop_impl,
                direct=True,
            )
        )
    assert "probe_direct_read" not in tools.TOOLS


def test_direct_is_refused_for_unlisted_names() -> None:
    with pytest.raises(ValueError, match="DIRECT_WRITE_TOOL_NAMES"):
        tools._register(
            tools.ToolDef(
                name="probe_direct_write",
                description="probe",
                kind=tools.WRITE,
                params=_params(),
                permission=lambda user: True,
                impl=_noop_impl,
                guard=_noop_guard,
                direct=True,
            )
        )
    assert "probe_direct_write" not in tools.TOOLS


def test_direct_requires_a_guard_and_an_impl() -> None:
    """允许清单里的名字也不是白名单：没有 guard/impl 一样是定义错误。"""
    original = tools.TOOLS.pop("user_unban")
    try:
        with pytest.raises(ValueError, match="need a guard"):
            tools._register(
                tools.ToolDef(
                    name="user_unban",
                    description="probe",
                    kind=tools.WRITE,
                    params=_params(),
                    permission=lambda user: True,
                    impl=_noop_impl,
                    direct=True,
                )
            )
        with pytest.raises(ValueError, match="must declare impl"):
            tools._register(
                tools.ToolDef(
                    name="user_unban",
                    description="probe",
                    kind=tools.WRITE,
                    params=_params(),
                    permission=lambda user: True,
                    impl=None,  # type: ignore[arg-type]
                    guard=_noop_guard,
                    direct=True,
                )
            )
        with pytest.raises(ValueError, match="cannot declare dual_auth_action"):
            tools._register(
                tools.ToolDef(
                    name="user_unban",
                    description="probe",
                    kind=tools.WRITE,
                    params=_params(),
                    permission=lambda user: True,
                    impl=_noop_impl,
                    guard=_noop_guard,
                    dual_auth_action=WRITE_AUTH_ACTION,
                    direct=True,
                )
            )
    finally:
        tools.TOOLS["user_unban"] = original
    assert tools.TOOLS["user_unban"] is original


# ---------------------------------------------------------------------------
# 读工具：platform_logs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_platform_logs_defaults_to_a_bounded_window(env, fake_session) -> None:
    env.logs.total = 3
    env.logs.rows = [
        {
            "_id": "log-1",
            "timestamp": 1700000000.0,
            "type": "ban_change",
            "level": "warning",
            "url": "system:agent_ban_change",
            "user": "000000000000000000000001",
            "user_name": "管理员甲",
            "xuehai_id": "",
            "data": "x" * 900,
        }
    ]

    data = await _impl("platform_logs")

    page = env.logs.pages[0]
    assert page["size"] == tools_platform.MAX_LOG_ROWS
    assert page["page"] == 1
    window_days = (page["time_end"] - page["time_start"]) / 86400
    assert 6.9 < window_days < 7.1
    assert data["kind"] == "logs"
    assert data["total"] == 3
    assert data["returned"] == 1
    assert len(data["items"][0]["data"]) == tools_platform.LOG_ROW_DATA_CHARS
    assert data["ltype_counts"] == [{"ltype": "ban_change", "count": 2}]
    assert fake_session, "聚合计数必须走一条有界 SQL"


@pytest.mark.asyncio
async def test_platform_logs_clamps_limit_and_passes_filters(env, fake_session) -> None:
    await _impl(
        "platform_logs",
        keyword="timeout",
        ltype="user_login",
        log_level="warning",
        user_oid="000000000000000000000002",
        xuehai_id="123456",
        time_start="2026-01-01T00:00:00",
        time_end="2026-01-02T00:00:00",
        limit=500,
    )

    page = env.logs.pages[0]
    assert page["size"] == tools_platform.MAX_LOG_ROWS
    assert page["query"] == "timeout"
    assert page["types"] == ["user_login"]
    assert page["levels"] == ["warning"]
    assert page["performer"] == "000000000000000000000002"
    assert page["xuehai_id"] == "123456"
    # 时间是 epoch 秒区间，且按用户时区解释（naive → 本地）
    assert page["time_end"] - page["time_start"] == 86400


@pytest.mark.asyncio
async def test_platform_logs_rejects_inverted_or_huge_windows(env) -> None:
    with pytest.raises(AgentError) as caught:
        await _impl(
            "platform_logs",
            time_start="2026-01-02T00:00:00",
            time_end="2026-01-01T00:00:00",
        )
    assert caught.value.code == "AI_AGENT_BAD_ARGUMENT"

    with pytest.raises(AgentError):
        await _impl(
            "platform_logs",
            time_start="2025-01-01T00:00:00",
            time_end="2026-01-01T00:00:00",
        )
    assert env.logs.pages == []


@pytest.mark.asyncio
async def test_platform_logs_rejects_bad_timestamp(env) -> None:
    with pytest.raises(AgentError) as caught:
        await _impl("platform_logs", time_start="yesterday")
    assert caught.value.code == "AI_AGENT_BAD_ARGUMENT"


@pytest.mark.asyncio
async def test_platform_logs_survives_broken_aggregate(env, monkeypatch) -> None:
    async def _boom(**kwargs):
        raise RuntimeError("aggregate down")

    monkeypatch.setattr(tools_platform, "_top_ltypes", _boom)
    env.logs.rows = []
    env.logs.total = 0

    data = await _impl("platform_logs")

    assert data["items"] == []
    assert data["ltype_counts"] == []


# ---------------------------------------------------------------------------
# 读工具：db_diagnostics / backup_status / scheduler_status / device_ban_list
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_db_diagnostics_trims_to_key_fields(env, monkeypatch) -> None:
    @contextlib.asynccontextmanager
    async def _scope():
        yield SimpleNamespace()

    async def _fast(session):
        return {
            "available": True,
            "sessions": 7,
            "aas": 0.5,
            "counters": {"parse": 1},
            "ash_seconds": 12,
        }

    async def _slow(session):
        return {
            "available": True,
            "top_sql": [{"sql_id": f"s{index}"} for index in range(15)],
            "waits": [{"wait_class": f"w{index}"} for index in range(15)],
            "tablespaces": [
                {"name": "USERS", "used_percent": 91},
                {"name": "SYSTEM", "used_percent": 40},
            ],
            "errors": {},
        }

    async def _sessions(session):
        return {"total": 9, "active": 2, "inactive": 7, "items": [{"sid": 1}]}

    monkeypatch.setattr(tools_platform, "session_scope", _scope)
    monkeypatch.setattr(tools_platform.db_monitor, "collect_fast", _fast)
    monkeypatch.setattr(tools_platform.db_monitor, "collect_slow", _slow)
    monkeypatch.setattr(tools_platform.db_monitor, "collect_sessions", _sessions)
    monkeypatch.setattr(
        tools_platform.db_monitor,
        "_pool_status",
        lambda: {"available": True, "size": 5},
    )

    data = await _impl("db_diagnostics")

    assert data["load"] == {
        "available": True,
        "sessions": 7,
        "aas": 0.5,
        "ash_seconds": 12,
    }
    assert len(data["slow"]["top_sql"]) == tools_platform.DIAG_TOP_N
    assert len(data["slow"]["waits"]) == tools_platform.DIAG_TOP_N
    assert data["slow"]["tablespaces"][0]["name"] == "USERS"
    assert data["sessions"] == {"total": 9, "active": 2, "inactive": 7}
    assert data["pool"]["size"] == 5


@pytest.mark.asyncio
async def test_db_diagnostics_turns_collector_failure_into_agent_error(
    env, monkeypatch
) -> None:
    @contextlib.asynccontextmanager
    async def _scope():
        raise RuntimeError("no dsn")
        yield  # pragma: no cover

    monkeypatch.setattr(tools_platform, "session_scope", _scope)

    with pytest.raises(AgentError) as caught:
        await _impl("db_diagnostics")
    assert caught.value.status_code == 503
    assert caught.value.code == "AI_AGENT_TOOL_FAILED"


@pytest.mark.asyncio
async def test_backup_status_flags_failure(env, monkeypatch) -> None:
    async def _snapshot():
        return {
            "state": "failed",
            "started_at": "2026-01-01T00:00:00",
            "finished_at": "2026-01-01T00:20:00",
            "tables_total": 40,
            "tables_done": 12,
            "rows_loaded": 900,
            "current_table": "APP_LOGS",
            "elapsed_seconds": 1200,
            "message": "ORA-01653",
        }

    monkeypatch.setattr(tools_platform.adb_backup, "backup_snapshot", _snapshot)

    data = await _impl("backup_status")

    assert data["state"] == "failed"
    assert data["tables_done"] == 12
    assert [alert["code"] for alert in data["alerts"]] == ["backup_failed"]


@pytest.mark.asyncio
async def test_backup_status_maps_probe_failure_to_agent_error(env, monkeypatch) -> None:
    async def _boom():
        raise OSError("systemctl missing")

    monkeypatch.setattr(tools_platform.adb_backup, "backup_snapshot", _boom)

    with pytest.raises(AgentError) as caught:
        await _impl("backup_status")
    assert caught.value.status_code == 503


@pytest.mark.asyncio
async def test_scheduler_status_lists_jobs(env, monkeypatch) -> None:
    class _Job:
        def __init__(self, job_id: str, next_run) -> None:
            self.id = job_id
            self.name = job_id
            self.pending = False
            self.trigger = "cron[hour='1']"
            self._next = next_run

        @property
        def next_run_time(self):
            if self._next is None:
                raise ValueError("next_run_time is only available once scheduled")
            return self._next

    class _Scheduler:
        running = True

        def get_jobs(self):
            return [
                _Job("daily_generate_reports", datetime(2026, 1, 1, 4, 0)),
                _Job("perf_sample_host", None),
            ]

    monkeypatch.setitem(sys.modules, "main", SimpleNamespace(scheduler=_Scheduler()))

    data = await _impl("scheduler_status")

    assert data["available"] is True
    assert data["running"] is True
    assert data["job_count"] == 2
    assert [job["id"] for job in data["jobs"]] == [
        "daily_generate_reports",
        "perf_sample_host",
    ]
    assert data["jobs"][0]["next_run_time"] == "2026-01-01T04:00:00"
    assert data["jobs"][1]["next_run_time"] is None


@pytest.mark.asyncio
async def test_scheduler_status_returns_structured_error(env, monkeypatch) -> None:
    class _Broken:
        running = False

        def get_jobs(self):
            raise RuntimeError("scheduler not started")

    monkeypatch.setitem(sys.modules, "main", SimpleNamespace(scheduler=_Broken()))

    data = await _impl("scheduler_status")

    assert data["available"] is False
    assert "RuntimeError" in data["error"]
    assert data["jobs"] == []


@pytest.mark.asyncio
async def test_device_ban_list_includes_penalty_counts(env) -> None:
    env.counters.counts = {"123456": 3}

    data = await _impl("device_ban_list", size=500)

    assert data["size"] == tools_platform.DEVICE_BAN_PAGE_MAX
    assert data["total"] == 1
    assert data["items"][0]["xuehai_id"] == "123456"
    assert data["items"][0]["ban_count"] == 3


@pytest.mark.asyncio
async def test_device_ban_list_survives_missing_counters(env, monkeypatch) -> None:
    async def _boom(ids):
        raise RuntimeError("counters down")

    monkeypatch.setattr(env.counters, "get_counts", _boom)

    data = await _impl("device_ban_list")

    assert data["items"][0]["ban_count"] == 0


# ---------------------------------------------------------------------------
# 写工具：user_ban
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_user_ban_writes_state_record_and_audit(env) -> None:
    data = await _impl(
        "user_ban", user_oid="000000000000000000000002", reason="灌接口", duration_days=3
    )

    assert data["kind"] == "user_ban"
    assert data["business_id"] == "20251135"
    assert data["permanent"] is False
    assert data["before"] == {
        "banned": False,
        "active": False,
        "reason": None,
        "until": None,
        "source": None,
        "rule": None,
    }
    assert data["after"]["banned"] is True
    assert data["after"]["active"] is True

    assert env.users.bans == [
        {
            "oid": "000000000000000000000002",
            "reason": "灌接口",
            "until": data["until"],
            "source": "agent",
            "rule_code": None,
        }
    ]
    assert env.records.rows[0]["source"] == "agent"
    assert env.records.rows[0]["permanent"] is False
    assert env.records.rows[0]["operator_oid"] == ADMIN["id"]
    assert env.logs.inserted[0]["type"] == "ban_change"
    assert env.logs.inserted[0]["level"] == "warning"
    assert "封禁了用户" in env.logs.inserted[0]["data"]


@pytest.mark.asyncio
async def test_user_ban_resolves_by_business_id_and_supports_permanent(env) -> None:
    data = await _impl("user_ban", business_id="20251135", reason="永久禁入")

    assert data["permanent"] is True
    assert data["until"] is None
    assert env.records.rows[0]["permanent"] is True
    assert env.records.rows[0]["expires_at"] is None


@pytest.mark.asyncio
async def test_user_ban_refuses_self_and_admins(env) -> None:
    with pytest.raises(AgentError) as self_ban:
        await _impl("user_ban", user_oid=ADMIN["id"], reason="手滑")
    assert self_ban.value.code == "AI_AGENT_FORBIDDEN"
    assert self_ban.value.status_code == 403

    with pytest.raises(AgentError) as admin_ban:
        await _impl("user_ban", user_oid=OTHER_ADMIN["id"], reason="内斗")
    assert admin_ban.value.code == "AI_AGENT_FORBIDDEN"

    # 管理员保护必须发生在任何写入之前
    assert env.users.bans == []
    assert env.records.rows == []
    assert env.logs.inserted == []


@pytest.mark.asyncio
async def test_user_ban_404s_and_requires_a_target(env) -> None:
    with pytest.raises(AgentError) as missing:
        await _impl("user_ban", user_oid="0000000000000000000000ff", reason="x")
    assert missing.value.code == "AI_AGENT_NOT_FOUND"
    assert missing.value.status_code == 404

    with pytest.raises(AgentError) as no_target:
        await _impl("user_ban", reason="x")
    assert no_target.value.code == "AI_AGENT_BAD_ARGUMENT"


@pytest.mark.asyncio
async def test_user_ban_rejects_bad_duration_and_blank_reason(env) -> None:
    with pytest.raises(AgentError):
        await _impl(
            "user_ban", user_oid="000000000000000000000002", reason="x", duration_days=0
        )
    with pytest.raises(AgentError):
        await _impl(
            "user_ban",
            user_oid="000000000000000000000002",
            reason="x",
            duration_days=tools_platform.BAN_DURATION_MAX_DAYS + 1,
        )
    with pytest.raises(AgentError):
        await _impl("user_ban", user_oid="000000000000000000000002", reason="   ")


# ---------------------------------------------------------------------------
# 写工具：user_unban
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_user_unban_clears_state_and_records(env) -> None:
    await _impl("user_ban", user_oid="000000000000000000000002", reason="灌接口")
    env.logs.inserted.clear()

    data = await _impl(
        "user_unban", user_oid="000000000000000000000002", reason="误封解除"
    )

    assert data["changed"] is True
    assert data["after"]["banned"] is False
    assert env.users.cleared == ["000000000000000000000002"]
    assert env.records.rows[-1]["source"] == "agent"
    assert env.records.rows[-1]["reason"] == "误封解除"
    assert env.records.rows[-1]["permanent"] is False
    assert env.logs.inserted[-1]["type"] == "ban_change"


@pytest.mark.asyncio
async def test_user_unban_is_idempotent_when_not_banned(env) -> None:
    data = await _impl(
        "user_unban", user_oid="000000000000000000000002", reason="顺手解一下"
    )

    assert data["changed"] is False
    assert env.users.cleared == []
    assert env.records.rows == []
    assert env.logs.inserted == []


@pytest.mark.asyncio
async def test_user_unban_refuses_admin_targets(env) -> None:
    with pytest.raises(AgentError) as caught:
        await _impl("user_unban", user_oid=OTHER_ADMIN["id"], reason="放开")
    assert caught.value.code == "AI_AGENT_FORBIDDEN"
    assert env.users.cleared == []


# ---------------------------------------------------------------------------
# 写工具：device_unban
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_device_unban_deletes_row_and_counters(env) -> None:
    data = await _impl("device_unban", xuehai_id="123456", reason="误判")

    assert data["changed"] is True
    assert data["before"]["reason"] == "刷接口"
    assert env.device_bans.deleted == ["123456"]
    assert env.counters.deleted == ["123456"]
    assert env.logs.inserted[-1]["type"] == "device_ban"
    assert env.logs.inserted[-1]["level"] == "warning"


@pytest.mark.asyncio
async def test_device_unban_is_idempotent(env) -> None:
    data = await _impl("device_unban", xuehai_id="999999", reason="误判")

    assert data["changed"] is False
    assert env.device_bans.deleted == []
    assert env.logs.inserted == []


@pytest.mark.asyncio
async def test_device_unban_keeps_the_unban_when_counters_fail(env, monkeypatch) -> None:
    async def _boom(xuehai_id):
        raise RuntimeError("counters down")

    monkeypatch.setattr(env.counters, "delete", _boom)

    data = await _impl("device_unban", xuehai_id="123456", reason="误判")

    assert data["changed"] is True
    assert data["counters_removed"] == 0


# ---------------------------------------------------------------------------
# executor：direct 写工具直接执行；既有写工具仍然只出提案
# ---------------------------------------------------------------------------


def _call(name: str, arguments: dict) -> dict:
    return {
        "id": "call_1",
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments)},
    }


async def _run_tool(name: str, arguments: dict):
    return await executor._run_tool(
        call=_call(name, arguments),
        user=ADMIN,
        request_id="req-1",
        round_no=1,
    )


@pytest.mark.asyncio
async def test_executor_executes_direct_write_without_a_proposal(env, monkeypatch):
    hits: list[dict] = []

    async def _impl(user, **kwargs):
        hits.append(kwargs)
        return {"kind": "user_ban", "changed": True}

    async def _guard(user):
        return None

    async def _no_proposal(**kwargs):  # pragma: no cover - 被调用就是 bug
        raise AssertionError("direct 写工具不得创建提案")

    monkeypatch.setitem(
        tools.TOOLS,
        "user_ban",
        replace(_tool("user_ban"), impl=_impl, guard=_guard),
    )
    monkeypatch.setattr(executor.proposal, "propose", _no_proposal)

    message, created, status, note = await _run_tool(
        "user_ban", {"user_oid": "000000000000000000000002", "reason": "灌接口"}
    )

    assert created is None
    assert status == "executed"
    assert hits == [
        {"user_oid": "000000000000000000000002", "reason": "灌接口"}
    ]

    payload = json.loads(message["content"])
    assert payload["status"] == "executed"
    assert payload["direct"] is True
    assert payload["result"] == {"kind": "user_ban", "changed": True}

    event = env.events.rows[-1]
    assert event["status"] == "executed"
    assert event["tool_kind"] == tools.WRITE
    assert json.loads(event["args_summary"])["direct"] is True
    assert "user_ban" in note


@pytest.mark.asyncio
async def test_executor_still_proposes_existing_write_tools(env, monkeypatch):
    seen: list[dict] = []

    async def _propose(**kwargs):
        seen.append(kwargs)
        return {
            "oid": "proposal-1",
            "summary": "修改时长",
            "expires_at": "2026-01-01T00:10:00",
            "requires_dual_auth": False,
        }

    monkeypatch.setattr(executor.proposal, "propose", _propose)

    message, created, status, _note = await _run_tool(
        "update_member_duration",
        {"activity_id": "act-1", "member_id": "member-1", "duration": 2.5},
    )

    assert status == "proposed"
    assert created["oid"] == "proposal-1"
    assert seen and seen[0]["tool"].name == "update_member_duration"
    # 提案路径本就不写工具事件（流水在 proposals 表里），这里顺带把「行为没变」
    # 钉住：direct 分支才新增事件留痕，既有写工具一个字段都没动。
    assert env.events.rows == []
    assert json.loads(message["content"])["status"] == "proposed"


@pytest.mark.asyncio
async def test_executor_direct_write_still_runs_the_guard(env, monkeypatch):
    guard_hits: list[dict] = []

    async def _deny(user):
        guard_hits.append(user)
        raise AgentError("AI_FORBIDDEN", "权限不足", 403)

    async def _impl(user, **kwargs):  # pragma: no cover - 被调用就是 bug
        raise AssertionError("guard 拒绝后不得执行 impl")

    monkeypatch.setitem(
        tools.TOOLS,
        "user_ban",
        replace(_tool("user_ban"), impl=_impl, guard=_deny),
    )

    _message, created, status, note = await _run_tool(
        "user_ban", {"user_oid": "000000000000000000000002", "reason": "灌接口"}
    )

    assert status == "denied"
    assert created is None
    assert guard_hits == [ADMIN]
    assert env.events.rows[-1]["status"] == "denied"
    assert "权限复核未通过" in note


@pytest.mark.asyncio
async def test_executor_direct_write_reports_tool_failure(env, monkeypatch):
    async def _impl(user, **kwargs):
        raise AgentError("AI_AGENT_FORBIDDEN", "不能封禁或解封管理员账号", 403)

    async def _guard(user):
        return None

    monkeypatch.setitem(
        tools.TOOLS,
        "user_ban",
        replace(_tool("user_ban"), impl=_impl, guard=_guard),
    )

    message, _created, status, _note = await _run_tool(
        "user_ban", {"user_oid": "000000000000000000000009", "reason": "内斗"}
    )

    assert status == "failed"
    payload = json.loads(message["content"])
    assert payload["error"] == "tool_failed"
    assert payload["code"] == "AI_AGENT_FORBIDDEN"
    assert env.events.rows[-1]["status"] == "failed"


# ---------------------------------------------------------------------------
# 直执行的事件摘要
# ---------------------------------------------------------------------------


def test_direct_args_summary_is_marked_and_still_redacted() -> None:
    text = executor._direct_args_text(
        json.dumps({"user_oid": "abc", "reason": "x", "password": "hunter2"})
    )
    assert text is not None
    parsed = json.loads(text)
    assert parsed["direct"] is True
    assert parsed["password"] != "hunter2"


def test_direct_args_summary_handles_missing_arguments() -> None:
    assert json.loads(executor._direct_args_text(None))["direct"] is True
