"""运维补充工具（设置快照 / 调度器 / AI 流水 / run 队列 / 备份 / 维护模式 / 调度开关）。

离线：没有 ADB、没有调度器、没有数据库、没有 systemd。``backends`` / ``main`` /
``system_state`` / ``adb_backup`` / ``session_scope`` 全部换成替身，验的是**工具层
自己负责的那部分**：

* 注册表形状（6 读 + 3 写）：读工具带 guard、无 prepare/双人码；写工具三件套齐；
* 严格 admin：department 连 permission 都过不了；
* 白名单投影：设置快照不含密钥原值、调用流水不含正文；
* 拒绝要早：备份忙、维护模式缺原因、未知 job、非法动作、超窗/超量一律拒。
"""

from __future__ import annotations

import contextlib
import os
import sys
import time
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from db import system_state
from services import adb_backup
from services.agent import tools, tools_ops_extra
from services.agent.tool_impl import AgentError

ADMIN = {"id": "000000000000000000000001", "per": ["admin"]}
DEPARTMENT = {"id": "000000000000000000000002", "per": ["department"]}

EXTRA_READ = {
    "settings_snapshot",
    "scheduler_jobs",
    "ai_call_logs",
    "agent_queue_status",
    "user_lookup",
    "server_time",
}
EXTRA_WRITE = {"backup_run", "maintenance_mode", "scheduler_control"}
EXTRA_TOOLS = EXTRA_READ | EXTRA_WRITE


def _tool(name: str) -> tools.ToolDef:
    return tools.TOOLS[name]


async def _impl(name: str, **kwargs):
    return await _tool(name).impl(ADMIN, **kwargs)


async def _prepare(name: str, **kwargs):
    prepare = _tool(name).prepare
    assert prepare is not None, f"{name} 缺少 prepare"
    return await prepare(ADMIN, **kwargs)


async def _async_none() -> None:
    return None


@pytest.fixture
def no_audit(monkeypatch) -> list[tuple[str, str, str]]:
    """审计是旁路：默认吞掉，用例按需断言（action / detail / log_type）。"""
    seen: list[tuple[str, str, str]] = []

    async def _audit(user, action, detail, *, log_type="ai_ops_extra"):
        seen.append((action, detail, log_type))

    monkeypatch.setattr(tools_ops_extra, "_audit", _audit)
    return seen


# ---------------------------------------------------------------------------
# 注册表
# ---------------------------------------------------------------------------


def test_extra_tools_are_registered_with_frozen_kinds() -> None:
    assert EXTRA_TOOLS <= set(tools.TOOLS)
    for name in EXTRA_READ:
        assert _tool(name).kind == tools.READ, name
    for name in EXTRA_WRITE:
        assert _tool(name).kind == tools.WRITE, name


def test_read_tools_are_guarded_but_never_proposed() -> None:
    for name in EXTRA_READ:
        tool = _tool(name)
        assert tool.prepare is None, name
        assert tool.dual_auth_action is None, name
        assert tool.direct is False, name
        assert tool.guard is not None, name
        assert tool.permission is tools_ops_extra._admin_only, name


def test_write_tools_are_proposals_with_dual_auth_and_guard() -> None:
    """注册期不变量：dual_auth 写必须同时有 prepare + guard，且不得是 direct。"""
    for name in EXTRA_WRITE:
        tool = _tool(name)
        assert tool.impl is not None, name
        assert tool.prepare is not None, name
        assert tool.dual_auth_action == tools_ops_extra.WRITE_AUTH_ACTION, name
        assert tool.guard is not None, name
        assert tool.direct is False, name
        assert name not in tools.DIRECT_WRITE_TOOL_NAMES, name


def test_admin_sees_extra_tools_department_does_not() -> None:
    admin_names = {spec["function"]["name"] for spec in tools.tool_specs(ADMIN)}
    department_names = {
        spec["function"]["name"] for spec in tools.tool_specs(DEPARTMENT)
    }
    assert EXTRA_TOOLS <= admin_names
    assert not (EXTRA_TOOLS & department_names)


@pytest.mark.parametrize("name", sorted(EXTRA_TOOLS))
def test_non_admin_is_denied(name: str) -> None:
    assert tools.run_allowed(_tool(name), ADMIN) is True
    assert tools.run_allowed(_tool(name), DEPARTMENT) is False


def test_params_and_descriptions_are_well_formed() -> None:
    for name in EXTRA_TOOLS:
        tool = _tool(name)
        assert tool.description.strip(), name
        assert tool.params["type"] == "object", name
        assert tool.params["additionalProperties"] is False, name
        assert set(tool.params["required"]) <= set(tool.params["properties"]), name
        for key, spec in tool.params["properties"].items():
            assert spec.get("type"), f"{name}.{key} 缺少 type"
            assert spec.get("description"), f"{name}.{key} 缺少 description"


# ---------------------------------------------------------------------------
# settings_snapshot
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_settings_snapshot_is_masked_whitelist(monkeypatch) -> None:
    async def _all_settings():
        return {
            "items": [
                {
                    "key": "ai.allowed_roles",
                    "value": ["admin"],
                    "type": "list",
                    "group": "ai",
                    "label": "AI 可用角色",
                    "min": None,
                    "max": None,
                    "updated_at": datetime(2026, 1, 2, 3, 4, 5),
                    "updated_by": ADMIN["id"],
                },
                {
                    "key": "ai.provider_api_key",
                    "value": "sk-live-secret",
                    "type": "str",
                    "group": "ai",
                    "label": "上游 key",
                    "min": None,
                    "max": None,
                    "updated_at": None,
                    "updated_by": None,
                },
                {
                    "key": "ops.log_retention_days",
                    "value": 30,
                    "type": "int",
                    "group": "ops",
                    "label": "日志保留",
                    "min": 1,
                    "max": 3650,
                    "updated_at": None,
                    "updated_by": None,
                },
            ]
        }

    monkeypatch.setattr(tools_ops_extra.settings_runtime, "ensure_fresh", _async_none)
    monkeypatch.setattr(tools_ops_extra.settings_runtime, "all_settings", _all_settings)
    monkeypatch.setattr(
        tools_ops_extra.ai_config,
        "public_provider_view",
        lambda: [
            {
                "name": "deepseek",
                "kind": "openai_compat",
                "base_url": "api.deepseek.com",
                "models": ["deepseek-chat"],
                "configured": True,
                "api_key": "sk-should-not-leak",
            }
        ],
    )

    data = await _impl("settings_snapshot")

    assert data["kind"] == "settings_snapshot"
    rendered = str(tools_ops_extra._cap(data))
    assert "sk-live-secret" not in rendered
    assert "sk-should-not-leak" not in rendered

    by_key = {item["key"]: item for item in data["settings"]}
    assert by_key["ai.provider_api_key"]["value"] == tools_ops_extra.REDACTED
    assert by_key["ai.allowed_roles"]["value"] == ["admin"]
    assert data["masked_keys"] == ["ai.provider_api_key"]
    # provider 视图只留白名单字段
    assert set(data["providers"][0]) == {
        "name",
        "kind",
        "base_url",
        "models",
        "configured",
    }


@pytest.mark.asyncio
async def test_settings_snapshot_filters_by_group(monkeypatch) -> None:
    async def _all_settings():
        return {
            "items": [
                {"key": "ai.a", "value": 1, "group": "ai", "type": "int"},
                {"key": "ops.b", "value": 2, "group": "ops", "type": "int"},
            ]
        }

    monkeypatch.setattr(tools_ops_extra.settings_runtime, "ensure_fresh", _async_none)
    monkeypatch.setattr(tools_ops_extra.settings_runtime, "all_settings", _all_settings)
    monkeypatch.setattr(tools_ops_extra, "_provider_view", lambda: [])

    data = await _impl("settings_snapshot", group="ops")

    assert [item["key"] for item in data["settings"]] == ["ops.b"]
    assert data["group"] == "ops"


@pytest.mark.asyncio
async def test_settings_snapshot_rejects_unknown_group() -> None:
    with pytest.raises(AgentError) as caught:
        await _impl("settings_snapshot", group="root")
    assert caught.value.code == "AI_AGENT_BAD_ARGUMENT"


# ---------------------------------------------------------------------------
# scheduler_jobs / scheduler_control
# ---------------------------------------------------------------------------


class _Job:
    def __init__(self, job_id: str, next_run=None, pending: bool = False) -> None:
        self.id = job_id
        self.name = job_id
        self.pending = pending
        self.trigger = "cron[hour='3']"
        self._next = next_run

    @property
    def next_run_time(self):
        if self._next is None:
            raise ValueError("next_run_time is only available once scheduled")
        return self._next


class _Scheduler:
    running = True
    timezone = None

    def __init__(self, jobs) -> None:
        self.jobs = {job.id: job for job in jobs}
        self.calls: list[tuple] = []

    def get_jobs(self):
        return list(self.jobs.values())

    def get_job(self, job_id):
        return self.jobs.get(job_id)

    def pause_job(self, job_id):
        self.calls.append(("pause", job_id))
        self.jobs[job_id]._next = None

    def resume_job(self, job_id):
        self.calls.append(("resume", job_id))
        self.jobs[job_id]._next = datetime(2026, 1, 1, 3, 0)

    def modify_job(self, job_id, **kwargs):
        self.calls.append(("modify", job_id, kwargs.get("next_run_time")))
        return self.jobs[job_id]


def _use_scheduler(monkeypatch, scheduler) -> None:
    monkeypatch.setitem(sys.modules, "main", SimpleNamespace(scheduler=scheduler))


@pytest.mark.asyncio
async def test_scheduler_jobs_lists_enabled_state(monkeypatch) -> None:
    scheduler = _Scheduler(
        [
            _Job("daily_generate_reports", datetime(2026, 1, 1, 4, 0)),
            _Job("perf_sample_host", None),
        ]
    )
    _use_scheduler(monkeypatch, scheduler)
    monkeypatch.setattr(tools_ops_extra, "_last_result", lambda job_id: None)

    data = await _impl("scheduler_jobs")

    assert data["available"] is True
    assert data["job_count"] == 2
    assert data["enabled_count"] == 1
    by_id = {item["id"]: item for item in data["jobs"]}
    assert by_id["daily_generate_reports"]["enabled"] is True
    assert by_id["perf_sample_host"]["enabled"] is False
    assert by_id["perf_sample_host"]["next_run_time"] is None


@pytest.mark.asyncio
async def test_scheduler_jobs_degrades_when_unavailable(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "main", SimpleNamespace())

    data = await _impl("scheduler_jobs")

    assert data["available"] is False
    assert data["jobs"] == []
    assert "error" in data


@pytest.mark.asyncio
async def test_scheduler_control_pause_targets_registered_job(
    monkeypatch, no_audit
) -> None:
    scheduler = _Scheduler(
        [_Job("daily_retention_purge", datetime(2026, 1, 1, 3, 30))]
    )
    _use_scheduler(monkeypatch, scheduler)

    draft = await _prepare(
        "scheduler_control", job_id="daily_retention_purge", action="pause"
    )
    assert draft.preview["action"] == "pause"
    assert draft.preview["from_next_run_time"] == "2026-01-01T03:30:00"

    data = await _impl(
        "scheduler_control", job_id="daily_retention_purge", action="pause"
    )

    assert scheduler.calls == [("pause", "daily_retention_purge")]
    assert data["enabled"] is False
    assert no_audit and no_audit[0][0] == "agent_scheduler_pause"


@pytest.mark.asyncio
async def test_scheduler_control_run_now_keeps_trigger(monkeypatch, no_audit) -> None:
    scheduler = _Scheduler([_Job("system_settings_refresh", datetime(2026, 1, 1))])
    _use_scheduler(monkeypatch, scheduler)

    await _impl("scheduler_control", job_id="system_settings_refresh", action="run_now")

    assert scheduler.calls[0][0] == "modify"
    assert isinstance(scheduler.calls[0][2], datetime)


@pytest.mark.asyncio
async def test_scheduler_control_rejects_unknown_job(monkeypatch) -> None:
    _use_scheduler(monkeypatch, _Scheduler([_Job("known", datetime(2026, 1, 1))]))

    with pytest.raises(AgentError) as caught:
        await _prepare("scheduler_control", job_id="drop_tables", action="pause")

    assert caught.value.code == "AI_AGENT_NOT_FOUND"
    assert caught.value.status_code == 404


@pytest.mark.asyncio
async def test_scheduler_control_rejects_unknown_action(monkeypatch) -> None:
    scheduler = _Scheduler([_Job("known", datetime(2026, 1, 1))])
    _use_scheduler(monkeypatch, scheduler)

    with pytest.raises(AgentError) as caught:
        await _prepare("scheduler_control", job_id="known", action="delete")

    assert caught.value.code == "AI_AGENT_BAD_ARGUMENT"
    assert scheduler.calls == []


# ---------------------------------------------------------------------------
# ai_call_logs
# ---------------------------------------------------------------------------


class _FakeCallLogsRepo:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.calls: list[dict] = []

    async def list_page(self, **kwargs):
        self.calls.append(kwargs)
        return self.rows


@pytest.mark.asyncio
async def test_ai_call_logs_projects_metadata_only(monkeypatch) -> None:
    repo = _FakeCallLogsRepo(
        [
            {
                "_id": "a" * 24,
                "created_at": datetime(2026, 1, 2, 3, 4, 5),
                "capability": "chat",
                "status": "error",
                "http_status": 502,
                "error_code": "AI_UPSTREAM_TIMEOUT",
                "model": "deepseek-chat",
                "purpose": "agent",
                "latency_ms": 1200,
                "prompt_tokens": 10,
                "completion_tokens": None,
                "total_tokens": 10,
                "units": 10,
                "key_scope": "server",
                "prompt_text": "SECRET-PROMPT-BODY",
                "completion_text": "SECRET-COMPLETION-BODY",
            }
        ]
    )
    monkeypatch.setattr(tools_ops_extra.backends, "get_ai_call_logs_repo", lambda: repo)

    data = await _impl("ai_call_logs", days=2, capability="chat", limit=5)

    assert repo.calls[0]["capability"] == "chat"
    assert repo.calls[0]["page_size"] == 5
    assert repo.calls[0]["time_start"] < datetime.now()
    item = data["items"][0]
    assert item["tokens"] == {"prompt": 10, "completion": None, "total": 10}
    assert item["units"] == 10
    assert item["key_scope"] == "server"
    rendered = str(data)
    assert "SECRET-PROMPT-BODY" not in rendered
    assert "SECRET-COMPLETION-BODY" not in rendered
    assert "prompt_text" not in item and "completion_text" not in item


@pytest.mark.asyncio
async def test_ai_call_logs_rejects_window_over_a_week() -> None:
    with pytest.raises(AgentError) as caught:
        await _impl("ai_call_logs", days=tools_ops_extra.LOG_WINDOW_MAX_DAYS + 1)
    assert caught.value.code == "AI_AGENT_BAD_ARGUMENT"
    # schema 上限与实现上限必须一致（模型先用 schema 就被挡下）
    assert (
        _tool("ai_call_logs").params["properties"]["days"]["maximum"]
        == tools_ops_extra.LOG_WINDOW_MAX_DAYS
    )


@pytest.mark.asyncio
async def test_ai_call_logs_rejects_limit_over_cap() -> None:
    with pytest.raises(AgentError) as caught:
        await _impl("ai_call_logs", limit=tools_ops_extra.LOG_ROWS_MAX + 1)
    assert caught.value.code == "AI_AGENT_BAD_ARGUMENT"
    assert (
        _tool("ai_call_logs").params["properties"]["limit"]["maximum"]
        == tools_ops_extra.LOG_ROWS_MAX
    )


@pytest.mark.asyncio
async def test_ai_call_logs_rejects_unknown_capability() -> None:
    with pytest.raises(AgentError):
        await _impl("ai_call_logs", capability="tts")


# ---------------------------------------------------------------------------
# backup_run
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_backup_run_prepare_refuses_when_running(monkeypatch) -> None:
    async def _snapshot():
        return {"state": adb_backup.STATE_RUNNING, "message": "备份进行中"}

    monkeypatch.setattr(adb_backup, "backup_snapshot", _snapshot)

    with pytest.raises(AgentError) as caught:
        await _prepare("backup_run")

    assert caught.value.code == "AI_AGENT_TOOL_FAILED"
    assert caught.value.status_code == 409


@pytest.mark.asyncio
async def test_backup_run_maps_busy_to_tool_failure(monkeypatch, no_audit) -> None:
    async def _trigger():
        raise adb_backup.BackupBusy()

    monkeypatch.setattr(adb_backup, "trigger_backup", _trigger)

    with pytest.raises(AgentError) as caught:
        await _impl("backup_run")

    assert caught.value.code == "AI_AGENT_TOOL_FAILED"
    assert caught.value.status_code == 409
    assert caught.value.message == adb_backup.BUSY_MESSAGE
    assert no_audit == []  # 没执行就不该留「已触发」审计


@pytest.mark.asyncio
async def test_backup_run_maps_trigger_error_to_503(monkeypatch, no_audit) -> None:
    async def _trigger():
        raise adb_backup.BackupTriggerError("启动备份任务失败")

    monkeypatch.setattr(adb_backup, "trigger_backup", _trigger)

    with pytest.raises(AgentError) as caught:
        await _impl("backup_run")

    assert caught.value.status_code == 503
    assert caught.value.message == "启动备份任务失败"


@pytest.mark.asyncio
async def test_backup_run_returns_snapshot_on_success(monkeypatch, no_audit) -> None:
    async def _snapshot():
        return {"state": adb_backup.STATE_IDLE, "message": "暂无备份任务运行"}

    async def _trigger():
        return {"state": adb_backup.STATE_OK, "message": "最近一次备份成功"}

    monkeypatch.setattr(adb_backup, "backup_snapshot", _snapshot)
    monkeypatch.setattr(adb_backup, "trigger_backup", _trigger)

    draft = await _prepare("backup_run")
    assert draft.preview["kind"] == "backup_run"

    data = await _impl("backup_run")

    assert data["accepted"] is True
    assert data["state"] == adb_backup.STATE_OK
    assert no_audit and no_audit[0][0] == "agent_backup_run"


# ---------------------------------------------------------------------------
# maintenance_mode
# ---------------------------------------------------------------------------


@pytest.fixture
def maintenance(monkeypatch) -> list[tuple]:
    """``system_state`` 的替身：记录写入，不碰真 SQLite。"""
    current = {"state": {"enabled": False, "since": None, "reason": None}}
    writes: list[tuple] = []

    def _get_state():
        return dict(current["state"])

    def _set_state(enabled, reason, announcement, actor):
        writes.append((enabled, reason, announcement, actor))
        current["state"] = {
            "enabled": enabled,
            "since": 1_700_000_000.0 if enabled else None,
            "reason": reason,
            "announcement": announcement,
            "updated_by": actor,
        }
        return dict(current["state"])

    monkeypatch.setattr(system_state, "get_state", _get_state)
    monkeypatch.setattr(system_state, "set_state", _set_state)
    return writes


@pytest.mark.asyncio
async def test_maintenance_mode_requires_reason_on_enable(maintenance) -> None:
    with pytest.raises(AgentError) as caught:
        await _prepare("maintenance_mode", action="enable")

    assert caught.value.code == "AI_AGENT_BAD_ARGUMENT"
    assert maintenance == []  # 一个字节都没写


@pytest.mark.asyncio
async def test_maintenance_mode_rejects_overlong_reason(maintenance) -> None:
    with pytest.raises(AgentError):
        await _prepare("maintenance_mode", action="enable", reason="x" * 201)
    assert maintenance == []


@pytest.mark.asyncio
async def test_maintenance_mode_enable_writes_reason(maintenance, no_audit) -> None:
    draft = await _prepare(
        "maintenance_mode",
        action="enable",
        reason="数据库迁移",
        announcement="预计 30 分钟",
    )
    assert draft.preview["to"]["reason"] == "数据库迁移"

    data = await _impl(
        "maintenance_mode",
        action="enable",
        reason="数据库迁移",
        announcement="预计 30 分钟",
    )

    assert maintenance[0] == (True, "数据库迁移", "预计 30 分钟", ADMIN["id"])
    assert data["state"]["enabled"] is True
    assert data["state"]["since"] is not None
    assert no_audit[0][0] == "agent_maintenance_enable"
    assert no_audit[0][2] == "system_maintenance_enable"


@pytest.mark.asyncio
async def test_maintenance_mode_disable_clears_reason(maintenance, no_audit) -> None:
    await _impl("maintenance_mode", action="enable", reason="迁移")
    await _impl("maintenance_mode", action="disable", reason="随便填的")

    assert maintenance[-1] == (False, None, None, ADMIN["id"])
    assert no_audit[-1][0] == "agent_maintenance_disable"


@pytest.mark.asyncio
async def test_maintenance_mode_rejects_unknown_action(maintenance) -> None:
    with pytest.raises(AgentError) as caught:
        await _prepare("maintenance_mode", action="reboot")
    assert caught.value.code == "AI_AGENT_BAD_ARGUMENT"


# ---------------------------------------------------------------------------
# user_lookup
# ---------------------------------------------------------------------------

# 仓储 `_user_doc` 真实回的形状：凭据类字段**都在**，工具必须自己投影掉。
_FULL_ROW = {
    "_id": "a" * 24,
    "id": "2023010101",
    "name": "张三",
    "password": "$2b$12$hash-should-not-leak",
    "session_id": "sid-should-not-leak",
    "totp_secret": "totp-should-not-leak",
    "roles": ["student"],
    "class_id": "c" * 24,
    "banned": True,
    "ban_until": datetime(2026, 1, 2, 3, 4, 5),
    "primary_xuehai_id": "DEVICE-FINGERPRINT-RAW",
}


class _FakeLookupUsersRepo:
    def __init__(self, rows: list[dict] | None = None, one: dict | None = None) -> None:
        self.rows = rows or []
        self.one = one
        self.calls: list[tuple] = []

    async def get_by_oid(self, oid: str):
        self.calls.append(("get_by_oid", oid))
        return self.one

    async def find_by_business_id(self, business_id: str):
        self.calls.append(("find_by_business_id", business_id))
        return self.one

    async def list_by_name_or_id(self, pattern: str):
        self.calls.append(("list_by_name_or_id", pattern))
        return self.rows


class _FakeLookupGroupsRepo:
    def __init__(self, names: dict[str, str] | None = None) -> None:
        self.names = names or {}

    async def get(self, oid: str):
        name = self.names.get(oid)
        return {"_id": oid, "name": name, "description": ""} if name else None


async def _impl_lookup(**kwargs):
    """``_impl`` 的 ``name`` 形参与工具入参同名，直接绕开它调 impl。"""
    return await _tool("user_lookup").impl(ADMIN, **kwargs)


def _use_lookup_repos(monkeypatch, users, groups=None) -> None:
    monkeypatch.setattr(
        tools_ops_extra.backends, "get_users_repo", lambda session=None: users
    )
    monkeypatch.setattr(
        tools_ops_extra.backends,
        "get_groups_repo",
        lambda session=None: groups or _FakeLookupGroupsRepo(),
    )


@pytest.mark.asyncio
async def test_user_lookup_by_name_projects_whitelist(monkeypatch) -> None:
    users = _FakeLookupUsersRepo(rows=[dict(_FULL_ROW)])
    _use_lookup_repos(
        monkeypatch, users, _FakeLookupGroupsRepo({"c" * 24: "软件工程 2301"})
    )

    data = await _impl_lookup(name="张")

    assert users.calls == [("list_by_name_or_id", "张")]
    item = data["users"][0]
    assert set(item) == set(tools_ops_extra.LOOKUP_FIELDS)
    assert item["oid"] == "a" * 24
    assert item["business_id"] == "2023010101"
    assert item["class_name"] == "软件工程 2301"
    assert item["banned"] is True
    assert item["ban_until"] == "2026-01-02 03:04:05"
    # 已绑主设备只回布尔，原值不外带
    assert item["device_bound"] is True
    rendered = str(data)
    for leak in ("hash-should-not-leak", "sid-should-not-leak",
                 "totp-should-not-leak", "DEVICE-FINGERPRINT-RAW"):
        assert leak not in rendered


@pytest.mark.asyncio
async def test_user_lookup_by_business_id_is_exact(monkeypatch) -> None:
    users = _FakeLookupUsersRepo(one=dict(_FULL_ROW))
    _use_lookup_repos(monkeypatch, users)

    data = await _impl_lookup(business_id="2023010101")

    assert users.calls == [("find_by_business_id", "2023010101")]
    assert data["query"] == {"business_id": "2023010101"}
    assert data["count"] == 1
    assert data["users"][0]["device_bound"] is True
    assert data["users"][0]["class_name"] is None  # 班级名取不到不失败


@pytest.mark.asyncio
async def test_user_lookup_by_oid_is_exact(monkeypatch) -> None:
    users = _FakeLookupUsersRepo(one=dict(_FULL_ROW))
    _use_lookup_repos(monkeypatch, users)

    data = await _impl_lookup(oid="A" * 24)

    assert users.calls == [("get_by_oid", "A" * 24)]
    assert data["count"] == 1
    assert data["truncated"] is False


@pytest.mark.asyncio
async def test_user_lookup_rejects_missing_or_ambiguous_arguments(monkeypatch) -> None:
    users = _FakeLookupUsersRepo(rows=[dict(_FULL_ROW)])
    _use_lookup_repos(monkeypatch, users)

    with pytest.raises(AgentError) as caught:
        await _impl_lookup()
    assert caught.value.code == "AI_AGENT_BAD_ARGUMENT"
    assert caught.value.status_code == 400

    with pytest.raises(AgentError) as caught:
        await _impl_lookup(name="张", business_id="2023010101")
    assert caught.value.code == "AI_AGENT_BAD_ARGUMENT"

    with pytest.raises(AgentError) as caught:
        await _impl_lookup(oid="not-an-oid")
    assert caught.value.code == "AI_AGENT_BAD_ARGUMENT"
    assert users.calls == []  # 一个查询都没发出去


@pytest.mark.asyncio
async def test_user_lookup_truncates_at_twenty(monkeypatch) -> None:
    rows = [
        {**_FULL_ROW, "_id": f"{index:024x}", "name": f"张{index}"} for index in range(25)
    ]
    _use_lookup_repos(monkeypatch, _FakeLookupUsersRepo(rows=rows))

    data = await _impl_lookup(name="张")

    assert data["count"] == tools_ops_extra.LOOKUP_LIMIT == 20
    assert data["truncated"] is True
    assert len(data["users"]) == 20


# ---------------------------------------------------------------------------
# server_time
# ---------------------------------------------------------------------------


@pytest.fixture
def shanghai_tz(monkeypatch):
    """固定进程时区（``datetime.now().astimezone()`` 与 ``_to_local_naive`` 都读它）。"""
    original = os.environ.get("TZ")
    monkeypatch.setenv("TZ", "Asia/Shanghai")
    time.tzset()
    try:
        yield "Asia/Shanghai"
    finally:
        if original is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = original
        time.tzset()


@pytest.mark.asyncio
async def test_server_time_uses_local_naive_ban_basis(shanghai_tz) -> None:
    from services.device_guard import _to_local_naive

    data = await _impl("server_time")

    now_iso = datetime.fromisoformat(data["now_iso"])
    assert now_iso.utcoffset() == timedelta(hours=8)
    assert data["timezone"] == "Asia/Shanghai"
    assert data["epoch_seconds"] == int(now_iso.timestamp())
    assert data["weekday"] == "星期" + "一二三四五六日"[now_iso.weekday()]

    # 本地墙钟 naive 必须与封禁落库同口径（±2s 容忍取整）
    naive = datetime.fromisoformat(data["local_naive"])
    assert naive.tzinfo is None
    expected = _to_local_naive(datetime.now().astimezone())
    assert abs((naive - expected).total_seconds()) <= 2
    assert naive.date() == now_iso.date()


# ---------------------------------------------------------------------------
# 参数收敛（schema 层）
# ---------------------------------------------------------------------------


def test_prepare_arguments_enforces_schema_bounds() -> None:
    with pytest.raises(AgentError):
        tools.prepare_arguments(
            _tool("ai_call_logs"), {"days": tools_ops_extra.LOG_WINDOW_MAX_DAYS + 1}
        )
    with pytest.raises(AgentError):
        tools.prepare_arguments(
            _tool("ai_call_logs"), {"limit": tools_ops_extra.LOG_ROWS_MAX + 1}
        )
    with pytest.raises(AgentError):
        tools.prepare_arguments(
            _tool("scheduler_control"), {"job_id": "x", "action": "delete"}
        )
    with pytest.raises(AgentError):
        tools.prepare_arguments(_tool("settings_snapshot"), {"group": "root"})


# ---------------------------------------------------------------------------
# agent_queue_status
# ---------------------------------------------------------------------------


class _Row:
    def __init__(self, mapping: dict) -> None:
        self._mapping = mapping


class _Result:
    def __init__(self, rows=None, scalar=None) -> None:
        self._rows = rows or []
        self._scalar = scalar

    def all(self):
        return self._rows

    def scalar_one(self):
        return self._scalar


class _FakeSession:
    def __init__(self, results) -> None:
        self.results = list(results)

    async def execute(self, stmt):
        return self.results.pop(0)


@pytest.mark.asyncio
async def test_agent_queue_status_reports_counts_and_stale(monkeypatch) -> None:
    now = datetime.now()
    grouped = [
        _Row(
            {
                "status": "queued",
                "total": 2,
                "oldest_created": now - timedelta(seconds=90),
                "oldest_started": None,
                "oldest_heartbeat": None,
            }
        ),
        _Row(
            {
                "status": "running",
                "total": 1,
                "oldest_created": now - timedelta(seconds=400),
                "oldest_started": now - timedelta(seconds=300),
                "oldest_heartbeat": now - timedelta(seconds=120),
            }
        ),
    ]

    @contextlib.asynccontextmanager
    async def _scope():
        yield _FakeSession([_Result(grouped), _Result(scalar=1), _Result(scalar=0)])

    monkeypatch.setattr(tools_ops_extra, "session_scope", _scope)
    monkeypatch.setattr(tools_ops_extra.run_queue, "stale_seconds", lambda: 360)

    data = await _impl("agent_queue_status")

    assert data["queued"]["count"] == 2
    assert data["queued"]["oldest_wait_seconds"] >= 89
    assert data["running"]["count"] == 1
    assert data["running"]["stale"] == 1
    assert data["running"]["without_heartbeat"] == 0
    assert data["active_total"] == 3
    assert data["limits"]["stale_seconds"] == 360
    assert data["limits"]["concurrency"] == tools_ops_extra.run_queue.concurrency()
    assert isinstance(data["pump_alive"], bool)
