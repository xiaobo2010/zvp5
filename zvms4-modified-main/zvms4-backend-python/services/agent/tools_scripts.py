"""运维脚本工具：把 ``scripts/`` 下**白名单内**的一次性脚本交给 agent。

与 ``ops_tools``（文件/数据库/SSH，任意命令）的区别只有一条：**脚本是固定的**。
``SCRIPT_REGISTRY`` 是写死的清单，不做目录扫描——「模型能让 agent 跑什么」应该是
一份需要 review 的常量，而不是「此刻磁盘上恰好有什么」。目录扫描会把任何一次
误放进 ``scripts/`` 的调试脚本变成模型可执行的远程代码。

两个工具：

* ``script_catalog``（读）：列出脚本名、用途、用法与参数 schema，**不执行任何
  东西**。模型先看目录再调用，避免编脚本名。
* ``script_run``（写）：``prepare`` 生成 ``kind=command`` 的提案（argv 逐项列出，
  不是拼成一行 shell），``guard`` 现查库确认管理员，``dual_auth_action`` 要求
  另一位管理员的 6 位动态码。**即使 ``apply=false`` 也走提案**：写工具只有一种
  语义（用户确认后才执行），「预演」不是绕过确认的理由。

执行侧的三条硬约束：

1. ``asyncio.create_subprocess_exec`` + argv 列表，**绝不经过 shell**——参数里的
   空格、引号、``;`` 都只是参数，不是命令分隔符；
2. ``cwd`` 固定仓库根（脚本自己会往 ``sys.path`` 里补仓库根），``env`` 继承
   当前进程并补 ``PYTHONPATH``；
3. stdout/stderr 各截 8000 字符、抹掉密钥形态的片段；非 0 退出码是**结果**而不是
   异常，模型要看 stderr 才能纠偏。
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from . import ops_auth
from .ops_tools import (
    WRITE_AUTH_ACTION,
    _admin_only,
    _as_bool,
    _cap,
    _draft,
    _mask_secrets,
    _params,
)
from .proposal import ProposalDraft
from .tool_impl import AgentError
from .tools import READ, WRITE, ToolDef, _register

# ``services/agent/tools_scripts.py`` → parents[0]=agent, [1]=services,
# [2]=后端根（生产即 ``/www/zvms4/backend``）。下面所有相对路径都相对它。
REPO_ROOT = Path(__file__).resolve().parents[2]

# 备份目录白名单：脚本只允许把「删前备份」写到这里（生产即
# ``/www/zvms4/backend/data/backups``，``data/`` 已在 .gitignore）。放开成任意路径
# 等于让一次误调用把 JSON 落进仓库/配置目录，既不安全也不好清。
#
# 为什么不是 ``/tmp``：后端 service 开了 ``PrivateTmp=true``，``/tmp`` 是服务私有的
# 挂载，备份在服务重启后就没了，管理员在宿主机上也看不到——而「删前备份」唯一
# 的价值就是事后能找回。``data/`` 与进程生命周期无关，且和库、日志同属持久目录。
BACKUP_ROOT = (REPO_ROOT / "data" / "backups").resolve()

# 单次运行的输出预算与超时（与 ``ops_tools.MAX_TOOL_RESULT_CHARS`` 同量级：
# 大于这个数对模型没用，只会挤掉上下文）。
MAX_STREAM_CHARS = 8000
RUN_TIMEOUT_SECONDS = 120.0

# agent 侧的统一日期口径：``YYYY-MM-DD``。个别脚本历史上要 ``YYYY-MM-DD-HHMMSS``
# （``clear_device_bans`` 的 ``--before`` 是写进备份文件的授权戳），由
# :func:`_before_argument` 补齐时分秒，对外只暴露一种格式。
BEFORE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_BEFORE_STYLE_DATE = "date"
_BEFORE_STYLE_DATETIME = "datetime"


def _script_params() -> dict:
    """两个脚本共用的参数 schema（脚本之间的差别只在 ``before`` 的传递格式）。"""
    return _params(
        {
            "before": {
                "type": "string",
                "description": "日期下界，格式 YYYY-MM-DD（作为删除边界与执行授权戳）",
            },
            "apply": {
                "type": "boolean",
                "description": "true = 真正写库（需另一位管理员确认）；默认 false 只预演",
            },
            "backup_dir": {
                "type": "string",
                "description": (
                    f"删前备份目录，必须在 {BACKUP_ROOT} 下（缺省就写到这里）"
                ),
            },
        },
        ("before",),
    )


SCRIPT_REGISTRY: dict[str, dict] = {
    "clear_device_bans": {
        "name": "clear_device_bans",
        "path": "scripts/clear_device_bans.py",
        "title": "清空显式设备封禁",
        "description": (
            "清空 BANNED_DEVICES 的显式设备封禁行与 DEVICE_BAN_COUNTERS 的梯度计数"
            "（删前先导出 JSON 备份）。账号推导型受限不在范围内。"
        ),
        "usage": (
            ".venv/bin/python scripts/clear_device_bans.py "
            "--before YYYY-MM-DD-HHMMSS [--apply] [--backup-dir DIR]"
            f"（不传 --backup-dir 时备份默认写到 {BACKUP_ROOT}）"
        ),
        "params": _script_params(),
        "requires_confirmation": True,
        "before_style": _BEFORE_STYLE_DATETIME,
    },
    "purge_legacy_device_bindings": {
        "name": "purge_legacy_device_bindings",
        "path": "scripts/purge_legacy_device_bindings.py",
        "title": "清理历史设备绑定",
        "description": (
            "删除 USER_DEVICE_BINDINGS 中早于 before 的历史观测行（主设备真相在 "
            "USERS.PRIMARY_XUEHAI_ID，本脚本不碰）。删前先导出 JSON 备份。"
        ),
        "usage": (
            ".venv/bin/python scripts/purge_legacy_device_bindings.py "
            "--before YYYY-MM-DD [--apply] [--backup-dir DIR]"
            f"（不传 --backup-dir 时备份默认写到 {BACKUP_ROOT}）"
        ),
        "params": _script_params(),
        "requires_confirmation": True,
        "before_style": _BEFORE_STYLE_DATE,
    },
}


# --------------------------------------------------------------------------- #
# 参数校验（prepare 与 impl 共用同一份，确认执行时不会因为「预演时校验过」就跳过）
# --------------------------------------------------------------------------- #


def _script_of(name: object) -> dict:
    key = name.strip() if isinstance(name, str) else ""
    entry = SCRIPT_REGISTRY.get(key)
    if entry is None:
        available = ", ".join(sorted(SCRIPT_REGISTRY))
        raise AgentError(
            "AI_AGENT_BAD_ARGUMENT",
            f"unknown script: {key or name!r}（可用：{available}）",
            400,
        )
    return entry


def _parse_before(value: object) -> str:
    text = value.strip() if isinstance(value, str) else ""
    if not BEFORE_RE.fullmatch(text):
        raise AgentError(
            "AI_AGENT_BAD_ARGUMENT",
            "before must be a date in YYYY-MM-DD format",
            400,
        )
    try:
        datetime.strptime(text, "%Y-%m-%d")
    except ValueError:
        raise AgentError(
            "AI_AGENT_BAD_ARGUMENT", f"before is not a real date: {text}", 400
        ) from None
    return text


def _backup_dir(value: Any) -> Path | None:
    """备份目录：绝对路径且必须落在 :data:`BACKUP_ROOT` 内（未给返回 ``None``）。

    ``resolve()`` 后再比较，``<BACKUP_ROOT>/../../etc`` 与指向外面的符号链接都会
    被挡掉；不存在的目录也照算（脚本自己 ``mkdir(parents=True)``）。
    """
    if value is None:
        return None
    text = value.strip() if isinstance(value, str) else ""
    if not text:
        return None
    path = Path(text)
    if not path.is_absolute():
        raise AgentError(
            "AI_AGENT_BAD_ARGUMENT",
            f"backup_dir must be an absolute path under {BACKUP_ROOT}",
            400,
        )
    resolved = path.resolve()
    if not resolved.is_relative_to(BACKUP_ROOT):
        raise AgentError(
            "AI_AGENT_BAD_ARGUMENT",
            f"backup_dir must stay under {BACKUP_ROOT}（后端 data 目录，持久且可读）",
            400,
        )
    return resolved


def _backup_target(value: Any) -> Path:
    """显式 ``backup_dir``，否则默认 :data:`BACKUP_ROOT`。

    缺省也要传 ``--backup-dir``：脚本自己的默认值是 ``/tmp/opencode/backups``，
    在 ``PrivateTmp=true`` 的服务里那是私有 tmp，服务重启即丢——「没指定」不能
    变成「备份写到一个管理员看不到的地方」。
    """
    return _backup_dir(value) or BACKUP_ROOT


def _before_argument(entry: dict, before: str) -> str:
    """``before`` → 脚本命令行要的格式。

    ``clear_device_bans`` 的 ``--before`` 是「执行授权戳」（会被写进备份文件），
    格式是 ``YYYY-MM-DD-HHMMSS``；agent 侧只暴露日期，所以补 ``-000000`` 表示
    当天零点。语义没有漂移：它本来就只做授权记录，不过滤行。
    """
    if entry.get("before_style") == _BEFORE_STYLE_DATETIME:
        return f"{before}-000000"
    return before


def _argv(
    entry: dict, *, before: str, apply: bool, backup_dir: Path
) -> list[str]:
    """完整 argv（第 0 项是解释器，第 1 项是脚本绝对路径）。

    ``--apply`` 只在 ``apply=true`` 时出现：dry-run 分支的 argv 里没有这个开关，
    即使脚本自己的默认值将来变了也不会误写。``--backup-dir`` 总是显式给出——
    备份必须落在后端持久目录，不能交给脚本的 ``/tmp`` 默认值。
    """
    argv = [
        sys.executable,
        str(REPO_ROOT / entry["path"]),
        "--before",
        _before_argument(entry, before),
    ]
    if apply:
        argv.append("--apply")
    argv.extend(["--backup-dir", str(backup_dir)])
    return argv


# --------------------------------------------------------------------------- #
# 读工具：script_catalog
# --------------------------------------------------------------------------- #


async def _impl_script_catalog(user: dict) -> dict:
    scripts = [
        {
            "name": entry["name"],
            "title": entry["title"],
            "description": entry["description"],
            "usage": entry["usage"],
            "params": entry["params"],
            "dry_run_default": True,
        }
        for entry in sorted(SCRIPT_REGISTRY.values(), key=lambda item: item["name"])
    ]
    return _cap(
        {
            "kind": "scripts",
            "scripts": scripts,
            "note": (
                "白名单固定，不接受任意脚本路径。运行走 script_run：先提案，"
                "另一位管理员确认（含动态码）后才执行；apply=false 只预演。"
                f"不传 backup_dir 时备份默认写到 {BACKUP_ROOT}"
                "（后端持久目录，重启不丢；脚本自己 mkdir）。"
            ),
        }
    )


# --------------------------------------------------------------------------- #
# 写工具：script_run（提案 → TOTP → 子进程）
# --------------------------------------------------------------------------- #


async def _prepare_script_run(
    user: dict,
    name: str,
    before: str,
    apply: bool = False,
    backup_dir: str | None = None,
) -> ProposalDraft:
    entry = _script_of(name)
    when = _parse_before(before)
    target = _backup_target(backup_dir)
    run = _as_bool(apply)
    argv = _argv(entry, before=when, apply=run, backup_dir=target)
    action = "执行" if run else "预演"
    return _draft(
        f"{action}脚本 {entry['name']}（before={when}）",
        {"kind": "command", "command": argv, "apply": run},
    )


def _environment() -> dict[str, str]:
    """子进程环境：继承 + 保证仓库根在 ``PYTHONPATH`` 上（脚本 import 得了 ``db``）。

    脚本自己会补 ``sys.path``，这里再补一层是为了「脚本被改动/被包了一层入口」时
    仍然能起来——环境变量是脚本与调用方唯一共享的约定，不依赖对方的实现细节。
    """
    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    parts = [str(REPO_ROOT)]
    parts.extend(item for item in env.get("PYTHONPATH", "").split(os.pathsep) if item)
    env["PYTHONPATH"] = os.pathsep.join(parts)
    return env


def _decode(raw: Any) -> str:
    if raw is None:
        return ""
    if isinstance(raw, bytes):
        return raw.decode("utf-8", "replace")
    return str(raw)


def _clip(raw: Any) -> tuple[str, bool]:
    text = _decode(raw)
    if len(text) <= MAX_STREAM_CHARS:
        return text, False
    return text[:MAX_STREAM_CHARS], True


def _redact_block(text: str) -> str:
    """脱敏命令行/环境变量形态的敏感值，正文原样保留。

    这里**不能**直接整段丢给 :func:`ops_tools.redact`：它把任何 >200 字符的字符串
    折成 ``<len N>``（审计摘要的口径）。脚本的 stdout 是模型要读的结果，折掉长度
    等于把工具变成「只会说输出有多长」。所以用同一套正则的脱敏实现
    （``_mask_secrets``，与 ``redact`` 内部同一个函数），只抹密钥形态的片段。
    """
    return _mask_secrets(text)


async def _impl_script_run(
    user: dict,
    name: str,
    before: str,
    apply: bool = False,
    backup_dir: str | None = None,
) -> dict:
    entry = _script_of(name)
    when = _parse_before(before)
    target = _backup_target(backup_dir)
    run = _as_bool(apply)
    argv = _argv(entry, before=when, apply=run, backup_dir=target)

    started = time.monotonic()
    try:
        process = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(REPO_ROOT),
            env=_environment(),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        raise AgentError(
            "AI_AGENT_TOOL_FAILED", f"script not found: {entry['path']}", 503
        ) from None

    try:
        raw_out, raw_err = await asyncio.wait_for(
            process.communicate(), timeout=RUN_TIMEOUT_SECONDS
        )
    except asyncio.TimeoutError:
        # 超时必须有终态：留着子进程会让一次 run 结束后还有脚本在写库。
        process.kill()
        await process.wait()
        raise AgentError(
            "AI_AGENT_TOOL_TIMEOUT",
            f"script {entry['name']} exceeded {int(RUN_TIMEOUT_SECONDS)}s",
            504,
        ) from None

    duration_ms = max(0, int((time.monotonic() - started) * 1000))
    stdout, stdout_cut = _clip(raw_out)
    stderr, stderr_cut = _clip(raw_err)
    exit_code = process.returncode if process.returncode is not None else -1

    return {
        "kind": "script",
        "script": entry["name"],
        "applied": run,
        "exit_code": int(exit_code),
        "stdout": _redact_block(stdout),
        "stderr": _redact_block(stderr),
        "truncated": stdout_cut or stderr_cut,
        "duration_ms": duration_ms,
    }


# --------------------------------------------------------------------------- #
# 注册（1 读 + 1 写）
# --------------------------------------------------------------------------- #

_register(
    ToolDef(
        name="script_catalog",
        description=(
            "列出可被 agent 调用的运维脚本（名称、用途、用法、参数 schema、默认 "
            "dry-run）。只读，不执行任何脚本；调用 script_run 前先看这里。"
        ),
        kind=READ,
        params=_params({}, ()),
        permission=_admin_only,
        impl=_impl_script_catalog,
        guard=ops_auth.ensure_system_admin,
    )
)

_register(
    ToolDef(
        name="script_run",
        description=(
            "运行白名单内的运维脚本（默认 dry-run，只预演不写库）。写操作：先生成"
            "提案，另一位管理员确认后才执行；apply=true 才会真正写库，且脚本自己"
            "还会先导出 JSON 备份。"
        ),
        kind=WRITE,
        params=_params(
            {
                "name": {
                    "type": "string",
                    "enum": sorted(SCRIPT_REGISTRY),
                    "description": "脚本名（见 script_catalog）",
                },
                "before": {
                    "type": "string",
                    "description": "日期下界，格式 YYYY-MM-DD（删除边界与授权戳）",
                },
                "apply": {
                    "type": "boolean",
                    "description": "true = 真正写库；默认 false 只预演",
                },
                "backup_dir": {
                    "type": "string",
                    "description": f"删前备份目录，必须在 {BACKUP_ROOT} 下",
                },
            },
            ("name", "before"),
        ),
        permission=_admin_only,
        impl=_impl_script_run,
        prepare=_prepare_script_run,
        guard=ops_auth.ensure_system_admin,
        dual_auth_action=WRITE_AUTH_ACTION,
    )
)


__all__ = [
    "BACKUP_ROOT",
    "MAX_STREAM_CHARS",
    "REPO_ROOT",
    "RUN_TIMEOUT_SECONDS",
    "SCRIPT_REGISTRY",
]
