"""白名单脚本工具（``tools_scripts``）：注册形状、目录、提案、argv 与输出处理。

全离线：``asyncio.create_subprocess_exec`` 一律打桩，**测试里不真跑任何脚本**
（脚本会连库、删数据，跑一次就不叫单测了）。守的是「模型能让 agent 跑什么」这
条边界：

* 注册表形状：``script_catalog`` 是纯读，``script_run`` 是提案 + TOTP 写工具；
* 白名单是写死的（路径必须在仓库内、文件真的存在），不做目录扫描；
* ``prepare`` 只算 argv，不执行；``apply=false`` 的 argv 里不出现 ``--apply``；
* 参数校验：日期格式、真实日期、备份目录越界一律 400；未知脚本名 400；
* 输出：stdout/stderr 各截 8000 字符、按行脱敏，非 0 退出码原样回给模型。
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest

from services.agent import executor, tools, tools_scripts
from services.agent.ops_tools import WRITE_AUTH_ACTION
from services.agent.proposal import ProposalDraft
from services.agent.tool_impl import AgentError

ADMIN = {"id": "000000000000000000000001", "per": ["admin"]}
STUDENT = {"id": "000000000000000000000002", "per": ["student"]}

SCRIPT_NAMES = {"clear_device_bans", "purge_legacy_device_bindings"}


# ---------------------------------------------------------------------------
# 子进程替身
# ---------------------------------------------------------------------------


class FakeProcess:
    """``create_subprocess_exec`` 的返回值：够 ``communicate/kill/wait`` 用。"""

    def __init__(
        self,
        stdout: bytes = b"",
        stderr: bytes = b"",
        returncode: int = 0,
        *,
        hang: bool = False,
    ) -> None:
        self._stdout = stdout
        self._stderr = stderr
        self._returncode = returncode
        self._hang = hang
        self.returncode: int | None = None
        self.killed = False

    async def communicate(self):
        if self._hang:
            await asyncio.sleep(60)
        self.returncode = self._returncode
        return self._stdout, self._stderr

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    async def wait(self):
        return self.returncode


def patch_exec(monkeypatch, process: FakeProcess) -> list[dict]:
    """替换 ``asyncio.create_subprocess_exec``，记录每次调用的 argv/关键字。"""
    calls: list[dict] = []

    async def fake(*args, **kwargs):
        calls.append({"args": list(args), "kwargs": dict(kwargs)})
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake)
    return calls


# ---------------------------------------------------------------------------
# 注册表形状
# ---------------------------------------------------------------------------


def test_catalog_is_read_only_and_script_run_is_a_totp_write() -> None:
    catalog = tools.TOOLS["script_catalog"]
    assert catalog.kind == tools.READ
    assert catalog.prepare is None
    assert catalog.dual_auth_action is None
    assert catalog.guard is not None

    runner = tools.TOOLS["script_run"]
    assert runner.kind == tools.WRITE
    assert runner.prepare is not None
    assert runner.dual_auth_action == WRITE_AUTH_ACTION
    assert runner.guard is not None
    assert runner.direct is False


def test_registry_is_a_fixed_whitelist_inside_the_repository() -> None:
    assert set(tools_scripts.SCRIPT_REGISTRY) == SCRIPT_NAMES
    for entry in tools_scripts.SCRIPT_REGISTRY.values():
        path = tools_scripts.REPO_ROOT / entry["path"]
        assert path.is_file(), entry["path"]
        assert path.resolve().is_relative_to(tools_scripts.REPO_ROOT)
        assert entry["requires_confirmation"] is True
        assert entry["params"]["required"] == ["before"]
        assert set(entry["params"]["properties"]) == {"before", "apply", "backup_dir"}
        assert entry["title"] and entry["description"] and entry["usage"]


def test_tool_specs_stay_sorted_with_the_new_tools() -> None:
    names = [spec["function"]["name"] for spec in tools.tool_specs(ADMIN)]
    assert names == sorted(names)
    assert {"script_catalog", "script_run"} <= set(names)


@pytest.mark.asyncio
async def test_missing_backup_dir_still_passes_the_persistent_root(monkeypatch) -> None:
    """``backup_dir`` 缺省也必须显式传：脚本自己的默认值在 PrivateTmp 里会丢。"""
    calls = patch_exec(monkeypatch, FakeProcess(stdout=b"ok\n"))

    await tools_scripts._impl_script_run(
        ADMIN, name="clear_device_bans", before="2026-10-01"
    )

    argv = calls[0]["args"]
    assert "--backup-dir" in argv
    assert argv[argv.index("--backup-dir") + 1] == str(tools_scripts.BACKUP_ROOT)


@pytest.mark.asyncio
async def test_catalog_lists_scripts_without_running_anything(monkeypatch) -> None:
    calls = patch_exec(monkeypatch, FakeProcess())

    result = await tools.TOOLS["script_catalog"].impl(ADMIN)

    assert calls == []
    assert result["kind"] == "scripts"
    names = [item["name"] for item in result["scripts"]]
    assert names == sorted(SCRIPT_NAMES)
    assert str(tools_scripts.BACKUP_ROOT) in result["note"]
    for item in result["scripts"]:
        assert set(item) == {
            "name",
            "title",
            "description",
            "usage",
            "params",
            "dry_run_default",
        }
        assert item["dry_run_default"] is True
        assert item["params"]["required"] == ["before"]
        # usage 是模型唯一会照着抄的字符串：默认落点必须写在里面。
        assert str(tools_scripts.BACKUP_ROOT) in item["usage"]


# ---------------------------------------------------------------------------
# 提案：只算 argv，不执行
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_prepare_does_not_spawn_and_lists_the_argv(monkeypatch) -> None:
    calls = patch_exec(monkeypatch, FakeProcess())

    draft = await tools_scripts._prepare_script_run(
        ADMIN, name="purge_legacy_device_bindings", before="2026-09-01"
    )

    assert calls == []
    assert isinstance(draft, ProposalDraft)
    assert "purge_legacy_device_bindings" in draft.summary
    assert draft.preview["kind"] == "command"
    assert draft.preview["apply"] is False
    command = draft.preview["command"]
    assert "python" in os.path.basename(command[0])
    assert command[1].endswith("scripts/purge_legacy_device_bindings.py")
    assert "--apply" not in command
    # 缺省备份目录必须是后端持久目录（不是脚本的 /tmp 默认值）。
    assert command[command.index("--backup-dir") + 1] == str(tools_scripts.BACKUP_ROOT)


def test_backup_root_is_a_persistent_directory_under_the_backend() -> None:
    """回归：``/tmp`` 在 ``PrivateTmp=true`` 的服务里是私有 tmp，重启即丢。"""
    root = tools_scripts.BACKUP_ROOT
    assert root.is_absolute()
    assert root.is_relative_to(tools_scripts.REPO_ROOT)
    assert root.parts[-2:] == ("data", "backups")
    assert not root.is_relative_to(Path("/tmp"))


@pytest.mark.asyncio
async def test_apply_false_never_passes_the_apply_flag(monkeypatch) -> None:
    calls = patch_exec(monkeypatch, FakeProcess(stdout=b"dry-run ok\n"))

    await tools_scripts._impl_script_run(
        ADMIN, name="clear_device_bans", before="2026-10-01", apply=False
    )

    argv = calls[0]["args"]
    assert "--apply" not in argv
    assert argv[argv.index("--before") + 1] == "2026-10-01-000000"
    assert argv[argv.index("--backup-dir") + 1] == str(tools_scripts.BACKUP_ROOT)
    # 绝不经过 shell：argv 是列表，脚本路径与解释器都是独立参数。
    assert calls[0]["kwargs"]["cwd"] == str(tools_scripts.REPO_ROOT)
    assert argv[1].endswith("scripts/clear_device_bans.py")


@pytest.mark.asyncio
async def test_apply_true_is_only_reachable_with_the_flag_and_backup_dir(
    monkeypatch,
) -> None:
    calls = patch_exec(monkeypatch, FakeProcess(stdout=b"done\n"))
    backup = f"{tools_scripts.BACKUP_ROOT}/agent-test"

    result = await tools_scripts._impl_script_run(
        ADMIN,
        name="purge_legacy_device_bindings",
        before="2026-09-01",
        apply=True,
        backup_dir=backup,
    )

    argv = calls[0]["args"]
    assert "--apply" in argv
    assert argv[argv.index("--before") + 1] == "2026-09-01"
    assert argv[argv.index("--backup-dir") + 1] == backup
    assert result["applied"] is True
    assert result["exit_code"] == 0


@pytest.mark.asyncio
async def test_string_false_is_not_treated_as_apply(monkeypatch) -> None:
    """模型常把布尔值说成字符串：``"false"`` 必须是预演，不能写库。"""
    calls = patch_exec(monkeypatch, FakeProcess())

    draft = await tools_scripts._prepare_script_run(
        ADMIN, name="clear_device_bans", before="2026-10-01", apply="false"
    )

    assert draft.preview["apply"] is False
    assert "--apply" not in draft.preview["command"]
    assert calls == []


# ---------------------------------------------------------------------------
# 参数校验
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("before", ["2026/09/01", "2026-13-01", "20260901", "", " "])
@pytest.mark.asyncio
async def test_bad_before_is_a_400(before: str) -> None:
    with pytest.raises(AgentError) as excinfo:
        await tools_scripts._prepare_script_run(
            ADMIN, name="clear_device_bans", before=before
        )
    assert excinfo.value.status_code == 400


@pytest.mark.parametrize(
    "backup_dir",
    [
        "/tmp/evil",
        "relative/path",
        str(tools_scripts.BACKUP_ROOT / ".." / ".." / "etc"),
        str(tools_scripts.BACKUP_ROOT.parent),  # 上层目录也不许（不是本根的子目录）
        "",
    ],
)
@pytest.mark.asyncio
async def test_backup_dir_must_stay_under_the_allowed_root(backup_dir: str) -> None:
    if backup_dir == "":
        # 空串 = 没给：落到后端持久目录，**不能**回落到脚本的 /tmp 默认值
        # （PrivateTmp 的服务里那是私有 tmp，重启即丢）。
        draft = await tools_scripts._prepare_script_run(
            ADMIN, name="clear_device_bans", before="2026-10-01", backup_dir=backup_dir
        )
        command = draft.preview["command"]
        assert command[command.index("--backup-dir") + 1] == str(
            tools_scripts.BACKUP_ROOT
        )
        return

    with pytest.raises(AgentError) as excinfo:
        await tools_scripts._prepare_script_run(
            ADMIN,
            name="clear_device_bans",
            before="2026-10-01",
            backup_dir=backup_dir,
        )
    assert excinfo.value.status_code == 400
    assert str(tools_scripts.BACKUP_ROOT) in excinfo.value.message


@pytest.mark.asyncio
async def test_unknown_script_name_is_a_400() -> None:
    with pytest.raises(AgentError) as excinfo:
        await tools_scripts._prepare_script_run(ADMIN, name="rm_rf", before="2026-10-01")
    assert excinfo.value.status_code == 400
    assert "unknown script" in excinfo.value.message


def test_tool_schema_rejects_names_outside_the_registry() -> None:
    tool = tools.TOOLS["script_run"]
    with pytest.raises(AgentError) as excinfo:
        tools.prepare_arguments(tool, {"name": "rm_rf", "before": "2026-10-01"})
    assert excinfo.value.status_code == 400

    kwargs = tools.prepare_arguments(
        tool, {"name": "clear_device_bans", "before": "2026-10-01"}
    )
    assert kwargs == {"name": "clear_device_bans", "before": "2026-10-01"}


def test_non_admin_cannot_run_scripts() -> None:
    assert tools.run_allowed(tools.TOOLS["script_run"], ADMIN) is True
    assert tools.run_allowed(tools.TOOLS["script_run"], STUDENT) is False
    assert tools.run_allowed(tools.TOOLS["script_catalog"], STUDENT) is False


# ---------------------------------------------------------------------------
# 输出：截断 / 脱敏 / 退出码
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_long_streams_are_truncated_per_stream(monkeypatch) -> None:
    patch_exec(
        monkeypatch,
        FakeProcess(stdout=b"a" * (tools_scripts.MAX_STREAM_CHARS + 100), stderr=b"b" * 50),
    )

    result = await tools_scripts._impl_script_run(
        ADMIN, name="clear_device_bans", before="2026-10-01"
    )

    assert len(result["stdout"]) == tools_scripts.MAX_STREAM_CHARS
    assert result["stderr"] == "b" * 50
    assert result["truncated"] is True


@pytest.mark.asyncio
async def test_secrets_are_masked_but_output_is_kept(monkeypatch) -> None:
    body = (
        "connected\n"
        "mysql --password=hunter2 --token=abc123\n"
        "TOKEN=supersecret value\n"
        + "x" * 300
        + "\n"
    ).encode()
    patch_exec(monkeypatch, FakeProcess(stdout=body))

    result = await tools_scripts._impl_script_run(
        ADMIN, name="clear_device_bans", before="2026-10-01"
    )

    stdout = result["stdout"]
    assert "hunter2" not in stdout
    assert "abc123" not in stdout
    assert "supersecret" not in stdout
    # 正文不能被整段折成 <len N>（那是审计摘要口径，不是工具结果口径）。
    assert "connected" in stdout
    assert "x" * 300 in stdout


@pytest.mark.asyncio
async def test_nonzero_exit_code_is_a_result_not_an_error(monkeypatch) -> None:
    patch_exec(
        monkeypatch,
        FakeProcess(stdout=b"partial\n", stderr=b"ORA-00942\n", returncode=3),
    )

    result = await tools_scripts._impl_script_run(
        ADMIN, name="clear_device_bans", before="2026-10-01"
    )

    assert result["exit_code"] == 3
    assert "ORA-00942" in result["stderr"]
    assert result["truncated"] is False
    assert result["duration_ms"] >= 0


@pytest.mark.asyncio
async def test_timeout_kills_the_child(monkeypatch) -> None:
    process = FakeProcess(hang=True)
    patch_exec(monkeypatch, process)
    monkeypatch.setattr(tools_scripts, "RUN_TIMEOUT_SECONDS", 0.01)

    with pytest.raises(AgentError) as excinfo:
        await tools_scripts._impl_script_run(
            ADMIN, name="clear_device_bans", before="2026-10-01"
        )

    assert excinfo.value.status_code == 504
    assert process.killed is True


# ---------------------------------------------------------------------------
# executor：未知调用的回包带可用工具清单
# ---------------------------------------------------------------------------


def test_available_tools_are_sorted_and_capped() -> None:
    names = executor._available_tool_names(ADMIN)
    assert names == sorted(names)
    assert len(names) <= executor.MAX_AVAILABLE_TOOLS
    assert "script_catalog" in names or len(names) == executor.MAX_AVAILABLE_TOOLS

    read_only = executor._available_tool_names(ADMIN, scene="terminal")
    assert read_only == sorted(read_only)
    assert "script_run" not in read_only


def test_unknown_tool_message_carries_available_tools(monkeypatch) -> None:
    seen: list[dict] = []

    async def fake_write_event(**kwargs):
        seen.append(kwargs)

    monkeypatch.setattr(executor, "_write_event", fake_write_event)

    call = {
        "id": "call_x",
        "function": {"name": "functions.drop_tables", "arguments": json.dumps({})},
    }
    message, created, status, _note = asyncio.run(
        executor._run_tool(
            call=call, user=ADMIN, request_id="r" * 24, round_no=1, scene=None
        )
    )

    payload = json.loads(message["content"])
    assert status == "denied"
    assert created is None
    assert payload["error"] == "unknown_tool"
    assert payload["tool"] == "functions.drop_tables"
    assert payload["available_tools"] == executor._available_tool_names(ADMIN)
    assert payload["available_tools"] == sorted(payload["available_tools"])
    assert len(payload["available_tools"]) <= executor.MAX_AVAILABLE_TOOLS
    # 审计记录的是模型**说**的名字；白名单外没有注册名可归一。
    assert seen[0]["tool_name"] == "functions.drop_tables"
