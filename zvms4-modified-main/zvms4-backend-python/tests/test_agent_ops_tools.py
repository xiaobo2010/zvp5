"""运维工具（文件 / 数据库 / SSH）的注册表、预览与权限闸。

离线：没有上游、没有数据库、没有 sshd。``file_ops`` / ``db_content`` /
``session_scope`` / ``ssh_exec.run_once`` / ``ops_auth`` 的依赖全部换成替身，
验的是**工具层自己负责的那部分**：

* 注册表形状（7 读 + 10 写，读工具不带 ``prepare``，写工具必须有预览与双人码）；
* 写操作只出提案——``prepare`` 只读现状、只产预览，不碰任何写入 API；
* 参数上限取服务层常量（``fs_list`` 100 条、``db_rows`` 50 行、命令 1024/60s）；
* 敏感值不进摘要（``redact``）、服务层异常翻译成 ``AgentError``。
"""

from __future__ import annotations

import contextlib
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from services import db_content, file_ops
from services.agent import ops_auth, ops_tools, ssh_exec, tools
from services.agent.tool_impl import AgentError

ADMIN = {"id": "000000000000000000000001", "per": ["admin"]}
DEPARTMENT = {"id": "000000000000000000000002", "per": ["department"]}

EXPECTED_READ = {
    "fs_list",
    "fs_stat",
    "fs_read",
    "db_schema",
    "db_columns",
    "db_rows",
    "db_query",
}
EXPECTED_WRITE = {
    "fs_write",
    "fs_mkdir",
    "fs_rename",
    "fs_delete",
    "fs_chmod",
    "db_insert",
    "db_update",
    "db_delete",
    "db_ddl",
    "ssh_exec",
    # 运维补充（``tools_ops_extra``）的写面：proposal + dual_auth + guard。
    "backup_run",
    "maintenance_mode",
    "scheduler_control",
}

# 平台运维（``services.agent.tools_platform``）：读工具只查不发 SQL，写工具是
# ``direct=True`` 的封禁/解封（过完 permission + guard + 参数收敛直接执行）。
PLATFORM_READ = {
    "platform_logs",
    "db_diagnostics",
    "backup_status",
    "scheduler_status",
    "device_ban_list",
    # 运维补充（``tools_ops_extra``）的只读面：注册期形状与上面 5 个完全一致。
    "settings_snapshot",
    "scheduler_jobs",
    "ai_call_logs",
    "agent_queue_status",
    # 封号前的只读侦察：目标确认 + 权威时间基准。
    "user_lookup",
    "server_time",
}
PLATFORM_WRITE = {"user_ban", "user_unban", "device_unban"}
OPS_TOOLS = EXPECTED_READ | EXPECTED_WRITE | PLATFORM_READ | PLATFORM_WRITE


def _tool(name: str) -> tools.ToolDef:
    return tools.TOOLS[name]


async def _prepare(name: str, **kwargs):
    prepare = _tool(name).prepare
    assert prepare is not None, f"{name} 缺少 prepare"
    return await prepare(ADMIN, **kwargs)


async def _impl(name: str, **kwargs):
    return await _tool(name).impl(ADMIN, **kwargs)


class _FakeSession:
    """db_content 的替身只要求「有个东西传给服务函数」。"""


@pytest.fixture
def sessions(monkeypatch) -> list[_FakeSession]:
    """把 ``ops_tools.session_scope`` 换成一个记录调用的异步上下文。"""
    created: list[_FakeSession] = []

    @contextlib.asynccontextmanager
    async def _scope():
        session = _FakeSession()
        created.append(session)
        yield session

    monkeypatch.setattr(ops_tools, "session_scope", _scope)
    return created


# ---------------------------------------------------------------------------
# 注册表
# ---------------------------------------------------------------------------


def test_ops_tools_are_registered_with_frozen_kinds() -> None:
    """名字集合写死：加一个运维工具是一次要 review 的能力扩张。"""
    assert OPS_TOOLS <= set(tools.TOOLS)
    for name in EXPECTED_READ | PLATFORM_READ:
        assert _tool(name).kind == tools.READ, name
    for name in EXPECTED_WRITE | PLATFORM_WRITE:
        assert _tool(name).kind == tools.WRITE, name


def test_read_tools_are_guarded_but_never_proposed() -> None:
    """读工具每次现查权限（``guard``），但不得带 ``prepare``/双人码（契约 §2）。"""
    for name in EXPECTED_READ | PLATFORM_READ:
        tool = _tool(name)
        assert tool.prepare is None, name
        assert tool.dual_auth_action is None, name
        assert tool.direct is False, name
        assert tool.guard is ops_auth.ensure_system_admin, name
        assert tool.permission is ops_tools._admin_only, name


def test_write_tools_are_previewed_proposals_with_dual_auth() -> None:
    """写工具三件套缺一不可：impl、prepare、dual_auth_action。"""
    for name in EXPECTED_WRITE:
        tool = _tool(name)
        assert tool.impl is not None, name
        assert tool.prepare is not None, name
        assert tool.dual_auth_action == ops_tools.WRITE_AUTH_ACTION, name
        assert tool.direct is False, name
        assert tool.guard is ops_auth.ensure_system_admin, name


def test_platform_write_tools_are_direct_but_guarded() -> None:
    """直接执行的写工具：有 guard、无提案预览、无 TOTP，且都在允许清单里。"""
    for name in PLATFORM_WRITE:
        tool = _tool(name)
        assert tool.impl is not None, name
        assert tool.direct is True, name
        assert tool.prepare is None, name
        assert tool.dual_auth_action is None, name
        assert tool.guard is ops_auth.ensure_system_admin, name
        assert name in tools.DIRECT_WRITE_TOOL_NAMES, name


def test_params_are_well_formed_json_schema() -> None:
    for name in OPS_TOOLS:
        params = _tool(name).params
        assert params["type"] == "object", name
        assert params["additionalProperties"] is False, name
        properties = params["properties"]
        assert set(params["required"]) <= set(properties), name
        for key, spec in properties.items():
            assert spec.get("type"), f"{name}.{key} 缺少 type"
        assert _tool(name).description.strip(), name


@pytest.mark.parametrize("name", sorted(OPS_TOOLS))
def test_non_admin_is_denied_by_permission(name: str) -> None:
    """``permission`` 是纯函数闸：department 也进不来（严格 admin）。"""
    assert tools.run_allowed(_tool(name), ADMIN) is True
    assert tools.run_allowed(_tool(name), DEPARTMENT) is False
    assert tools.run_allowed(_tool(name), {"id": "x", "per": []}) is False


def test_prepare_arguments_enforces_bounds() -> None:
    with pytest.raises(AgentError):
        tools.prepare_arguments(_tool("fs_chmod"), {"path": "/x", "mode": 0o777 + 1})
    with pytest.raises(AgentError):
        tools.prepare_arguments(_tool("fs_list"), {"path": "/var/log", "limit": 500})
    with pytest.raises(AgentError):
        tools.prepare_arguments(_tool("db_query"), {"sql": "SELECT 1", "extra": 1})


# ---------------------------------------------------------------------------
# 脱敏
# ---------------------------------------------------------------------------


def test_redact_masks_secrets_and_long_strings() -> None:
    raw = {
        "password": "hunter2",
        "API_KEY": "abc123",
        "nested": {"token": "t-1", "keep": "ok"},
        "rows": [{"secret": "s"}],
        "cmd": "mysql --password=hunter2 -u root",
        "short": "mysql -p s3cret",
        "env": "TOKEN=xyz systemctl restart caddy",
        "long": "x" * 201,
        "count": 3,
    }
    redacted = ops_tools.redact(raw)

    assert redacted["password"] == ops_tools.REDACT_PLACEHOLDER
    assert redacted["API_KEY"] == ops_tools.REDACT_PLACEHOLDER
    assert redacted["nested"] == {"token": ops_tools.REDACT_PLACEHOLDER, "keep": "ok"}
    assert redacted["rows"] == [{"secret": ops_tools.REDACT_PLACEHOLDER}]
    assert redacted["cmd"] == "mysql --password=<redacted> -u root"
    assert redacted["short"] == "mysql -p <redacted>"
    assert redacted["env"] == "TOKEN=<redacted> systemctl restart caddy"
    assert redacted["long"] == "<len 201>"
    assert redacted["count"] == 3
    # 返回副本：调用方（ARGS_PAYLOAD / PREVIEW）还要用原文
    assert raw["password"] == "hunter2"


# ---------------------------------------------------------------------------
# 权限闸
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ensure_system_admin_maps_ban_to_user_banned(monkeypatch) -> None:
    async def _banned(user):
        raise HTTPException(
            status_code=403,
            detail="USER_BANNED",
            headers={"X-Ban-Reason": "spam"},
        )

    monkeypatch.setattr(ops_auth.system_router, "_require_system_admin", _banned)
    with pytest.raises(AgentError) as caught:
        await ops_auth.ensure_system_admin(ADMIN)

    assert caught.value.code == "USER_BANNED"
    assert caught.value.status_code == 403
    assert caught.value.message == "USER_BANNED"
    assert caught.value.headers["X-Ban-Reason"] == "spam"


@pytest.mark.asyncio
async def test_ensure_system_admin_maps_403_to_ai_forbidden(monkeypatch) -> None:
    async def _denied(user):
        raise HTTPException(status_code=403, detail="权限不足")

    monkeypatch.setattr(ops_auth.system_router, "_require_system_admin", _denied)
    with pytest.raises(AgentError) as caught:
        await ops_auth.ensure_system_admin(DEPARTMENT)

    assert caught.value.code == "AI_FORBIDDEN"
    assert caught.value.status_code == 403


@pytest.mark.asyncio
async def test_ensure_system_admin_fails_closed(monkeypatch) -> None:
    """闸门自己炸了也算拒绝：错误的方向只能是「不放行」。"""

    async def _broken(user):
        raise RuntimeError("repo down")

    monkeypatch.setattr(ops_auth.system_router, "_require_system_admin", _broken)
    with pytest.raises(AgentError) as caught:
        await ops_auth.ensure_system_admin(ADMIN)

    assert caught.value.code == "AI_FORBIDDEN"
    assert caught.value.status_code == 403


@pytest.mark.asyncio
async def test_ensure_system_admin_passes_when_admin(monkeypatch) -> None:
    seen: list[dict] = []

    async def _ok(user):
        seen.append(user)
        return {"id": user["id"]}

    monkeypatch.setattr(ops_auth.system_router, "_require_system_admin", _ok)
    await ops_auth.ensure_system_admin(ADMIN)
    assert seen == [ADMIN]


# ---------------------------------------------------------------------------
# fs_write 预览
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fs_write_prepare_diff_for_existing_file(monkeypatch) -> None:
    monkeypatch.setattr(
        file_ops,
        "preview_file",
        lambda path: {
            "kind": "text",
            "mime": "text/plain",
            "size": 4,
            "content": "a\nb\n",
            "encoding": "utf-8",
            "truncated": False,
        },
    )

    draft = await _prepare("fs_write", path="/etc/x.conf", content="a\nc\n")

    assert draft.preview["kind"] == "diff"
    assert draft.preview["path"] == "/etc/x.conf"
    assert draft.preview["truncated"] is False
    diff = draft.preview["diff"]
    assert "-b" in diff and "+c" in diff
    assert draft.summary.startswith("写入 /etc/x.conf")
    assert len(draft.summary) <= 200


@pytest.mark.asyncio
async def test_fs_write_prepare_new_file(monkeypatch) -> None:
    def _missing(path):
        raise file_ops.FileOpError(404, "路径不存在")

    monkeypatch.setattr(file_ops, "preview_file", _missing)

    draft = await _prepare("fs_write", path="/tmp/new.conf", content="x\ny\n")

    assert draft.preview["kind"] == "new_file"
    assert draft.preview["path"] == "/tmp/new.conf"
    assert draft.preview["lines"] == 2
    assert draft.preview["content"] == "x\ny\n"


@pytest.mark.asyncio
async def test_fs_write_prepare_truncates_huge_diff(monkeypatch) -> None:
    monkeypatch.setattr(
        file_ops,
        "preview_file",
        lambda path: {"kind": "text", "content": "", "truncated": False, "size": 0},
    )

    draft = await _prepare("fs_write", path="/tmp/big.log", content="x\n" * 30000)

    assert draft.preview["kind"] == "diff"
    assert draft.preview["truncated"] is True
    assert len(draft.preview["diff"]) == ops_tools.DIFF_MAX_CHARS


@pytest.mark.asyncio
async def test_fs_write_refuses_binary_target(monkeypatch) -> None:
    monkeypatch.setattr(
        file_ops,
        "preview_file",
        lambda path: {"kind": "binary", "content": None, "truncated": False},
    )

    with pytest.raises(AgentError) as caught:
        await _prepare("fs_write", path="/tmp/a.bin", content="text")

    assert caught.value.code == "AI_AGENT_UNSUPPORTED_FILE"


# ---------------------------------------------------------------------------
# 其余写工具预览
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fs_delete_prepare_counts_entries(monkeypatch) -> None:
    monkeypatch.setattr(
        ops_tools, "_count_delete_entries", lambda path, recursive: (3, False)
    )
    draft = await _prepare("fs_delete", path="/tmp/cache", recursive=True)

    assert draft.preview == {
        "kind": "delete",
        "path": "/tmp/cache",
        "entries": 3,
        "entries_truncated": False,
    }


@pytest.mark.asyncio
async def test_fs_delete_prepare_does_not_reject_huge_tree(monkeypatch) -> None:
    """条目 >10000 由服务层拒绝：预览只标记，不提前失败（契约 §4.2）。"""
    monkeypatch.setattr(
        ops_tools, "_count_delete_entries", lambda path, recursive: (10001, True)
    )
    draft = await _prepare("fs_delete", path="/tmp/huge", recursive=True)

    assert draft.preview["entries"] == 10001
    assert draft.preview["entries_truncated"] is True


def test_count_delete_entries_real_files(tmp_path) -> None:
    """真文件小样本：目录递归计数、单文件算 1、不存在算 0。"""
    root = tmp_path / "tree"
    (root / "nested").mkdir(parents=True)
    (root / "a.txt").write_text("a", encoding="utf-8")
    (root / "nested" / "b.txt").write_text("b", encoding="utf-8")

    assert ops_tools._count_delete_entries(str(root), True) == (3, False)
    assert ops_tools._count_delete_entries(str(root), False) == (1, False)
    assert ops_tools._count_delete_entries(str(tmp_path / "nope"), False) == (0, False)


@pytest.mark.asyncio
async def test_fs_chmod_prepare_reports_octal_transition(monkeypatch) -> None:
    monkeypatch.setattr(
        file_ops, "stat_entry", lambda path: {"path": "/etc/x", "mode": 0o644}
    )

    draft = await _prepare("fs_chmod", path="/etc/x", mode=0o755)

    assert draft.preview == {
        "kind": "chmod",
        "path": "/etc/x",
        "from": "0o644",
        "to": "0o755",
    }


@pytest.mark.asyncio
async def test_db_insert_prepare_lists_columns() -> None:
    draft = await _prepare("db_insert", table="activities", values={"B": 2, "A": 1})

    assert draft.preview["kind"] == "row_insert"
    assert draft.preview["columns"] == ["A", "B"]
    assert draft.preview["values"] == {"B": 2, "A": 1}


@pytest.mark.asyncio
async def test_db_row_write_prepare_requires_key_object() -> None:
    with pytest.raises(AgentError) as caught:
        await _prepare("db_update", table="t", key={}, values={"A": 1})
    assert caught.value.code == "AI_AGENT_BAD_ARGUMENT"


@pytest.mark.asyncio
async def test_db_ddl_prepare_rejects_illegal_sql() -> None:
    """静态闸前置：非法 SQL 连提案都不该建。"""
    with pytest.raises(AgentError) as caught:
        await _prepare("db_ddl", sql="SELECT * FROM USERS")

    assert caught.value.status_code == 400
    assert "DDL" in caught.value.message


@pytest.mark.asyncio
async def test_db_ddl_prepare_requires_confirm_for_destructive() -> None:
    with pytest.raises(AgentError) as caught:
        await _prepare("db_ddl", sql="DELETE FROM ACTIVITIES")

    assert caught.value.status_code == 400
    assert "confirm" in caught.value.message


@pytest.mark.asyncio
async def test_db_ddl_prepare_preview_shape() -> None:
    draft = await _prepare(
        "db_ddl", sql="DELETE FROM ACTIVITIES WHERE ID = 1", confirm="ACTIVITIES"
    )

    preview = draft.preview
    assert preview["kind"] == "sql"
    assert preview["verb"] == "DELETE"
    assert preview["target"] == "ACTIVITIES"
    assert preview["destructive"] is True
    assert preview["sql"].startswith("DELETE FROM ACTIVITIES")
    assert draft.summary


@pytest.mark.asyncio
async def test_ssh_exec_prepare_names_configured_target() -> None:
    draft = await _prepare("ssh_exec", command="systemctl status caddy")

    assert draft.preview["kind"] == "command"
    assert draft.preview["command"] == "systemctl status caddy"
    assert draft.preview["target"].count(":") == 1  # user@host:port
    assert "systemctl" not in draft.summary  # summary 只说明「在哪执行」


# ---------------------------------------------------------------------------
# ssh_exec：参数上限与输出截断
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ssh_exec_rejects_overlong_command() -> None:
    with pytest.raises(AgentError) as caught:
        await ssh_exec.run_once("x" * (ssh_exec.MAX_COMMAND_CHARS + 1))

    assert caught.value.code == "AI_AGENT_BAD_ARGUMENT"
    assert caught.value.status_code == 400


@pytest.mark.asyncio
async def test_ssh_exec_rejects_overlong_timeout() -> None:
    with pytest.raises(AgentError) as caught:
        await ssh_exec.run_once("ls", timeout=ssh_exec.MAX_TIMEOUT_SECONDS + 1)

    assert caught.value.code == "AI_AGENT_BAD_ARGUMENT"


@pytest.mark.asyncio
async def test_ssh_exec_rejects_empty_command() -> None:
    with pytest.raises(AgentError):
        await ssh_exec.run_once("   ")


@pytest.mark.asyncio
async def test_ssh_exec_clips_streams_and_closes(monkeypatch) -> None:
    class _FakeConnection:
        def __init__(self) -> None:
            self.calls: list[tuple] = []
            self.closed = False

        async def run(self, command, *, check, timeout):
            self.calls.append((command, check, timeout))
            return SimpleNamespace(
                exit_status=2, stdout=b"a" * 9000, stderr=b"boom"
            )

        def close(self) -> None:
            self.closed = True

    connection = _FakeConnection()

    async def _connect():
        return connection

    monkeypatch.setattr(ssh_exec.ssh_bridge, "connect", _connect)

    result = await ssh_exec.run_once("false", timeout=5)

    assert connection.calls == [("false", False, 5.0)]
    assert connection.closed is True
    assert result["exit_status"] == 2
    assert len(result["stdout"]) == ssh_exec.MAX_STREAM_CHARS
    assert result["stderr"] == "boom"
    assert result["truncated"] == {"stdout": True, "stderr": False}
    assert result["target"].startswith("root@")


@pytest.mark.asyncio
async def test_ssh_exec_translates_channel_failure(monkeypatch) -> None:
    class _Broken:
        async def run(self, *args, **kwargs):
            raise OSError("connection refused")

        def close(self) -> None:
            pass

    async def _connect():
        return _Broken()

    monkeypatch.setattr(ssh_exec.ssh_bridge, "connect", _connect)

    with pytest.raises(AgentError) as caught:
        await ssh_exec.run_once("ls")

    assert caught.value.code == "AI_AGENT_SSH_FAILED"
    assert caught.value.status_code == 502


# ---------------------------------------------------------------------------
# 读工具：正常路径 + 结构上限
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fs_list_caps_entries_per_contract(monkeypatch) -> None:
    calls: list[dict] = []

    def _list_dir(path, *, offset=0, limit=500):
        calls.append({"path": path, "offset": offset, "limit": limit})
        return {
            "path": path,
            "offset": offset,
            "limit": limit,
            "total": 1,
            "entries": [{"name": "a.log", "type": "file"}],
        }

    monkeypatch.setattr(file_ops, "list_dir", _list_dir)

    data = await _impl("fs_list", path="/var/log", limit=500)

    assert calls == [{"path": "/var/log", "offset": 0, "limit": 100}]
    assert data["kind"] == "list"
    assert data["entries"] == [{"name": "a.log", "type": "file"}]


@pytest.mark.asyncio
async def test_fs_read_clips_text_and_flags_truncation(monkeypatch) -> None:
    monkeypatch.setattr(
        file_ops,
        "preview_file",
        lambda path: {
            "kind": "text",
            "mime": "text/plain",
            "size": 20000,
            "encoding": "utf-8",
            "content": "z" * 20000,
            "truncated": True,
        },
    )

    data = await _impl("fs_read", path="/var/log/big.log", max_bytes=9000)

    assert data["kind"] == "text"
    assert data["truncated"] is True
    assert len(data["content"]) < ops_tools.MAX_TOOL_RESULT_CHARS


@pytest.mark.asyncio
async def test_fs_stats_result_is_capped(monkeypatch) -> None:
    monkeypatch.setattr(
        file_ops, "stat_entry", lambda path: {"path": path, "blob": "a" * 9000}
    )

    data = await _impl("fs_stat", path="/var/log")

    assert data["truncated"] is True
    assert len(data["content"]) == ops_tools.MAX_TOOL_RESULT_CHARS


@pytest.mark.asyncio
async def test_db_query_happy_path_uses_one_session(sessions, monkeypatch) -> None:
    async def _run_query(session, sql, *, limit=100):
        assert isinstance(session, _FakeSession)
        return {
            "columns": ["NAME"],
            "rows": [{"NAME": "x"}],
            "truncated": False,
            "elapsed_ms": 3,
        }

    monkeypatch.setattr(db_content, "run_query", _run_query)

    data = await _impl("db_query", sql="SELECT NAME FROM GROUPS", limit=500)

    assert len(sessions) == 1
    assert data["kind"] == "query"
    assert data["rows"] == [{"NAME": "x"}]


@pytest.mark.asyncio
async def test_db_query_rejects_writes_before_opening_session(sessions) -> None:
    with pytest.raises(AgentError) as caught:
        await _impl("db_query", sql="UPDATE USERS SET NAME = 'x'")

    assert caught.value.status_code == 400
    assert sessions == []  # 早拒绝：不建会话


@pytest.mark.asyncio
async def test_db_rows_clamps_request_limit(sessions, monkeypatch) -> None:
    seen: list[int] = []

    async def _read_rows(session, table, *, offset=0, limit=100, order_by=None, desc=False):
        seen.append(limit)
        return {
            "table": table,
            "columns": ["ID"],
            "rows": [],
            "primary_key": ["ID"],
            "offset": offset,
            "limit": limit,
            "has_more": False,
            "total_estimate": 0,
        }

    monkeypatch.setattr(db_content, "read_rows", _read_rows)

    await _impl("db_rows", table="USERS", limit=200, desc="true")

    assert seen == [ops_tools.DB_ROWS_MAX_ROWS]


# ---------------------------------------------------------------------------
# 写工具执行 + 服务层异常翻译
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_db_insert_impl_reports_rowcount(sessions, monkeypatch) -> None:
    async def _insert(session, table, values):
        assert isinstance(session, _FakeSession)
        return {"rowcount": 1}

    monkeypatch.setattr(db_content, "insert_row", _insert)

    data = await _impl("db_insert", table="ACTIVITIES", values={"NAME": "x"})

    assert data == {"kind": "row_insert", "table": "ACTIVITIES", "rowcount": 1}


@pytest.mark.asyncio
async def test_service_errors_become_agent_errors(monkeypatch) -> None:
    def _exists(path):
        raise file_ops.FileOpError(409, "目标已存在")

    monkeypatch.setattr(file_ops, "make_dir", _exists)

    with pytest.raises(AgentError) as caught:
        await _impl("fs_mkdir", path="/etc")

    assert caught.value.code == "AI_AGENT_TOOL_FAILED"
    assert caught.value.status_code == 409
    assert caught.value.message == "目标已存在"


@pytest.mark.asyncio
async def test_ssh_exec_impl_audits_and_caps(monkeypatch) -> None:
    """SSH 执行必须留审计，且 detail 只含命令与退出码。"""
    audited: list[dict] = []

    async def _run_once(command, *, timeout=30.0):
        assert timeout == 20.0
        return {
            "exit_status": 0,
            "stdout": "ok",
            "stderr": "",
            "truncated": {"stdout": False, "stderr": False},
            "target": "root@127.0.0.1:22",
        }

    async def _audit(operator, *, action, detail, log_type):
        audited.append(
            {"operator": operator, "action": action, "detail": detail, "type": log_type}
        )

    monkeypatch.setattr(ops_tools.ssh_exec, "run_once", _run_once)
    monkeypatch.setattr(ops_tools, "audit_system_op", _audit)

    data = await _impl(
        "ssh_exec", command="systemctl restart caddy --password=hunter2", timeout=20
    )

    assert data["exit_status"] == 0
    assert audited == [
        {
            "operator": ADMIN["id"],
            "action": "agent_ssh_exec",
            "detail": "exit=0 cmd=systemctl restart caddy --password=<redacted>",
            "type": "system_ssh_command",
        }
    ]
