"""运维工具（契约 ``docs/AI_AGENT_OPS_DESIGN.md`` §4）：7 读 + 10 写。

三条不变量，实现里都能找到对应的一行：

1. **能力由封闭注册表决定**。文件名/表的合法性与上限一律取
   ``services/file_ops.py`` / ``services/db_content.py`` 的既有常量（不另立一套），
   工具层只做「形状翻译 + 参数收敛」。
2. **写操作只出提案**。每个写工具都带 ``prepare``（人读摘要 + 结构化预览）与
   ``dual_auth_action="agent_write"``（确认时必须另一位管理员的 6 位动态码），
   ``impl`` 只会在用户确认之后被调用。
3. **权限现查**。``permission=_admin_only`` 只是快速否决；真正的闸是
   ``guard=ops_auth.ensure_system_admin``（每次查库、封禁与权限重算）。

同步服务放在 ``asyncio.to_thread`` 里跑：``file_ops`` 是纯同步 IO，直接调用会卡住
整个事件循环（一次 ``list_dir`` 在十万条目录上就是几百毫秒）。``db_content`` 本身
是异步的，用 ``session_scope()`` 给每次调用一个独立会话。
"""

from __future__ import annotations

import asyncio
import difflib
import functools
import logging
import os
import re
from typing import Any, Awaitable, Callable

from db.oracle.session import session_scope
from services import db_content, file_ops, ssh_bridge
from util.dual_auth import audit_system_op

from . import ops_auth, ssh_exec, tool_impl
from .proposal import ProposalDraft
from .tool_impl import AgentError
from .tools import READ, WRITE, ToolDef, _register

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# 上限（结果给模型看的预算；服务层自己的上限不在这里重定义）
# --------------------------------------------------------------------------- #

# 与 ``executor.MAX_TOOL_RESULT_CHARS`` 同值：工具自己先截断，模型才看得见
# 「被截断了」这件事，而不是拿到一串断在半句上的 JSON。
MAX_TOOL_RESULT_CHARS = 8000

FS_LIST_MAX_ENTRIES = 100  # 契约 §4.1：fs_list 结果上限 100 条
FS_READ_MAX_BYTES = 32768  # fs_read 的 max_bytes 上界
FS_READ_DEFAULT_BYTES = 8000  # 默认读 8KB
DB_SCHEMA_MAX_TABLES = 200
DB_ROWS_MAX_ROWS = 50
DB_ROWS_MAX_REQUEST = 200
DB_QUERY_MAX_ROWS = 100
DB_QUERY_MAX_REQUEST = 500
DIFF_MAX_CHARS = 40000
NEW_FILE_PREVIEW_CHARS = 4000
SUMMARY_MAX_CHARS = 200

# 契约 §4.2：写工具确认时必须 TOTP。字符串会进 ``ToolDef.dual_auth_action``，
# 也是 ``util.dual_auth`` 的 action 名。
WRITE_AUTH_ACTION = "agent_write"


# --------------------------------------------------------------------------- #
# 脱敏（契约 §4.2「敏感值」）
# --------------------------------------------------------------------------- #

REDACT_PLACEHOLDER = "<redacted>"
REDACT_LONG_CHARS = 200

# 键名命中即整值抹掉：``password`` / ``api_key`` / ``token`` / ``secret`` 都算。
_SECRET_KEY_RE = re.compile(r"password|passwd|secret|token|key", re.I)
# 命令行形态：``--password=xxx`` / ``--api-key xxx`` / ``-token=x``。
_CLI_SECRET_RE = re.compile(
    r"(?i)(--?[a-z0-9_-]*(?:password|passwd|secret|token|key)[a-z0-9_-]*)"
    r"(\s*=\s*|\s+)(\S+)"
)
# ``mysql -p s3cret`` 这种短选项：``-p`` 本身不含关键词，只能单独认。
_SHORT_OPTION_SECRET_RE = re.compile(r"(?i)(^|\s)(-p)(\s*=\s*|\s+)(\S+)")
# 环境变量形态：``TOKEN=xyz`` / ``MYSQL_PWD=...``（SSH 命令里最常见的一种泄露）。
_ENV_SECRET_RE = re.compile(
    r"(?i)\b([a-z0-9_]*(?:password|passwd|secret|token|key)[a-z0-9_]*)(\s*=\s*)(\S+)"
)


def _mask_secrets(text: str) -> str:
    """抹掉命令行/环境变量形态的敏感值（键值对与长字符串另由调用方处理）。"""
    masked = _SHORT_OPTION_SECRET_RE.sub(
        lambda match: (
            f"{match.group(1)}{match.group(2)}{match.group(3)}{REDACT_PLACEHOLDER}"
        ),
        text,
    )
    masked = _CLI_SECRET_RE.sub(
        lambda match: f"{match.group(1)}{match.group(2)}{REDACT_PLACEHOLDER}",
        masked,
    )
    return _ENV_SECRET_RE.sub(
        lambda match: f"{match.group(1)}{match.group(2)}{REDACT_PLACEHOLDER}",
        masked,
    )


def _redact_text(text: str) -> str:
    masked = _mask_secrets(text)
    if len(masked) > REDACT_LONG_CHARS:
        # 长字符串本身可能就是正文/密钥材料：只留长度，内容一概不出摘要。
        return f"<len {len(masked)}>"
    return masked


def redact(value: Any) -> Any:
    """把敏感值换成占位符/长度标注，返回**副本**（不改入参）。

    用于 ``ARGS_SUMMARY`` 与事件摘要；``PREVIEW`` / ``ARGS_PAYLOAD`` 需要原文，
    不走这里。
    """
    if isinstance(value, dict):
        return {
            str(key): (
                REDACT_PLACEHOLDER
                if _SECRET_KEY_RE.search(str(key))
                else redact(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return _redact_text(value)
    return value


# --------------------------------------------------------------------------- #
# 通用小工具
# --------------------------------------------------------------------------- #


def _summary(text: str) -> str:
    """``ProposalDraft.summary``：人读一句话，≤200 字符，且**已脱敏**。

    与 :func:`redact` 的差别：这里长文本按**截断**处理而不是替换成 ``<len N>``
    ——摘要本来就是一句话，长路径/长命令截断后仍然可读，而 ``<len 250>`` 等于
    什么也没说。
    """
    return _mask_secrets(text)[:SUMMARY_MAX_CHARS]


def _service_error(exc: Exception) -> AgentError:
    """服务层异常 → 协议错误。状态码原样带过去（403/404/409/413 都有语义）。"""
    status = getattr(exc, "status_code", 400)
    try:
        status_code = int(status)
    except (TypeError, ValueError):
        status_code = 400
    detail = getattr(exc, "detail", None) or str(exc)
    return AgentError("AI_AGENT_TOOL_FAILED", str(detail), status_code)


def _translates_service_errors(
    func: Callable[..., Awaitable[Any]],
) -> Callable[..., Awaitable[Any]]:
    """``FileOpError`` / ``DbContentError`` → ``AgentError``。

    executor 只把 ``AgentError`` 的消息回给模型；不翻译的话模型看到的是
    「内部错误」，参数错了也不知道错在哪。
    """

    @functools.wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return await func(*args, **kwargs)
        except AgentError:
            raise
        except (file_ops.FileOpError, db_content.DbContentError) as exc:
            raise _service_error(exc) from None

    return wrapper


def _cap(payload: dict, limit: int = MAX_TOOL_RESULT_CHARS) -> dict:
    """结果兜底截断：结构不重要时降级成「前缀文本 + 截断标记」。"""
    text = tool_impl.to_json_text(payload)
    if len(text) <= limit:
        return payload
    return {
        "kind": payload.get("kind", "result"),
        "truncated": True,
        "note": f"结果超过 {limit} 字符，已截断",
        "content": text[:limit],
    }


def _as_bool(value: Any) -> bool:
    """``bool("false")`` 是 True——模型给的布尔参数必须显式翻译。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return False


def _require_object(name: str, value: Any) -> dict:
    if not isinstance(value, dict):
        raise AgentError("AI_AGENT_BAD_ARGUMENT", f"{name} must be a json object", 400)
    if not value:
        raise AgentError("AI_AGENT_BAD_ARGUMENT", f"{name} must not be empty", 400)
    return value


# --------------------------------------------------------------------------- #
# 提案草稿
# --------------------------------------------------------------------------- #


def _draft(summary: str, preview: dict) -> ProposalDraft:
    """``prepare`` 的返回值：一句人读摘要（已脱敏）+ 结构化预览。

    ``summary`` 是**展示**用的，所以先过 :func:`redact`；``preview`` 需要原文
    （确认界面和重放都靠它），不脱敏——它落 ``PREVIEW`` 列，不进事件摘要。
    """
    return ProposalDraft(summary=_summary(summary), preview=preview)


def _admin_only(user: dict) -> bool:
    """快速否决：``per`` 里没有 admin 的直接拒（真正的闸是 ``guard``）。"""
    return "admin" in (user.get("per") or [])


# --------------------------------------------------------------------------- #
# 读工具：文件
# --------------------------------------------------------------------------- #


@_translates_service_errors
async def _impl_fs_list(
    user: dict, path: str, limit: int = FS_LIST_MAX_ENTRIES, offset: int = 0
) -> dict:
    limit = max(1, min(int(limit), FS_LIST_MAX_ENTRIES))
    data = await asyncio.to_thread(
        file_ops.list_dir, path, offset=max(0, int(offset)), limit=limit
    )
    return _cap({"kind": "list", **data})


@_translates_service_errors
async def _impl_fs_stat(user: dict, path: str) -> dict:
    data = await asyncio.to_thread(file_ops.stat_entry, path)
    return _cap({"kind": "stat", **data})


@_translates_service_errors
async def _impl_fs_read(
    user: dict, path: str, max_bytes: int = FS_READ_DEFAULT_BYTES
) -> dict:
    """只读文本：二进制/图片一律拒绝（模型拿到 base64 只会胡说八道）。"""
    limit = max(1, min(int(max_bytes), FS_READ_MAX_BYTES))
    # 留 JSON 元数据的余量，正文尽量完整地留在结构化字段里。
    budget = max(1, MAX_TOOL_RESULT_CHARS - 400)
    limit = min(limit, budget)

    preview = await asyncio.to_thread(_preview_text, path)
    content = preview.get("content") or ""
    truncated = bool(preview.get("truncated")) or len(content) > limit
    return _cap(
        {
            "kind": "text",
            "path": path,
            "encoding": preview.get("encoding"),
            "size": preview.get("size"),
            "mime": preview.get("mime"),
            "content": content[:limit],
            "truncated": truncated,
        }
    )


def _preview_text(path: str) -> dict:
    preview = file_ops.preview_file(path)
    if preview.get("kind") != "text":
        raise AgentError(
            "AI_AGENT_UNSUPPORTED_FILE", "仅支持读取文本文件（二进制/图片不支持）", 400
        )
    return preview


# --------------------------------------------------------------------------- #
# 读工具：数据库
# --------------------------------------------------------------------------- #


@_translates_service_errors
async def _impl_db_schema(user: dict) -> dict:
    async with session_scope() as session:
        data = await db_content.list_schema(session)
    tables = list(data.get("tables") or [])
    return _cap(
        {
            "kind": "schema",
            "total": data.get("total", len(tables)),
            "tables": tables[:DB_SCHEMA_MAX_TABLES],
            "truncated": len(tables) > DB_SCHEMA_MAX_TABLES,
        }
    )


@_translates_service_errors
async def _impl_db_columns(user: dict, table: str) -> dict:
    async with session_scope() as session:
        data = await db_content.get_columns(session, table)
    return _cap({"kind": "columns", **data})


@_translates_service_errors
async def _impl_db_rows(
    user: dict,
    table: str,
    limit: int = DB_ROWS_MAX_ROWS,
    offset: int = 0,
    order_by: str | None = None,
    desc: bool = False,
) -> dict:
    # 契约 §4.1 的「结果上限 50 行」落在请求上：``has_more`` 才对得上真实页。
    limit = max(1, min(int(limit), DB_ROWS_MAX_ROWS))
    column = (order_by or "").strip() or None
    async with session_scope() as session:
        data = await db_content.read_rows(
            session,
            table,
            offset=max(0, int(offset)),
            limit=limit,
            order_by=column,
            desc=_as_bool(desc),
        )
    return _cap({"kind": "rows", **data})


@_translates_service_errors
async def _impl_db_query(user: dict, sql: str, limit: int = DB_QUERY_MAX_ROWS) -> dict:
    # 先做只读闸：不合格的 SQL 不该浪费时间建会话（也避免半途事务）。
    db_content.validate_readonly_sql(sql)
    limit = max(1, min(int(limit), DB_QUERY_MAX_ROWS))
    async with session_scope() as session:
        data = await db_content.run_query(session, sql, limit=limit)
    return _cap({"kind": "query", **data})


# --------------------------------------------------------------------------- #
# 写工具：文件（prepare 只读、只算预览；impl 只在确认后执行）
# --------------------------------------------------------------------------- #


def _current_text(path: str) -> tuple[str, str]:
    """现有文件正文 + 状态（``text`` / ``new_file``）。"""
    try:
        preview = file_ops.preview_file(path)
    except file_ops.FileOpError as exc:
        if exc.status_code == 404:
            # 父目录不存在也走这里：创建失败留给 apply 阶段报，预览不该卡住提案。
            return "", "new_file"
        raise
    if preview.get("kind") != "text":
        raise AgentError(
            "AI_AGENT_UNSUPPORTED_FILE", "目标不是文本文件，拒绝以文本内容覆盖", 400
        )
    return preview.get("content") or "", "text"


def _unified_diff(path: str, old: str, new: str) -> tuple[str, bool]:
    diff = "".join(
        difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile=path,
            tofile=path,
            n=3,
        )
    )
    if len(diff) > DIFF_MAX_CHARS:
        return diff[:DIFF_MAX_CHARS], True
    return diff, False


@_translates_service_errors
async def _prepare_fs_write(
    user: dict, path: str, content: str, encoding: str = "utf-8"
) -> Any:
    old, state = await asyncio.to_thread(_current_text, path)
    if state == "new_file":
        lines = len(content.splitlines())
        return _draft(
            f"新建文件 {path}（{lines} 行）",
            {
                "kind": "new_file",
                "path": path,
                "encoding": encoding,
                "lines": lines,
                "chars": len(content),
                "truncated": len(content) > NEW_FILE_PREVIEW_CHARS,
                "content": content[:NEW_FILE_PREVIEW_CHARS],
            },
        )

    diff, truncated = _unified_diff(path, old, content)
    added = sum(1 for line in diff.splitlines() if line.startswith("+"))
    removed = sum(1 for line in diff.splitlines() if line.startswith("-"))
    return _draft(
        f"写入 {path}（+{added}/-{removed} 行，{len(content)} 字符）",
        {"kind": "diff", "path": path, "diff": diff, "truncated": truncated},
    )


@_translates_service_errors
async def _impl_fs_write(
    user: dict, path: str, content: str, encoding: str = "utf-8"
) -> dict:
    data = await asyncio.to_thread(
        file_ops.write_text, path, content, encoding, create=True
    )
    return _cap({"kind": "written", **data})


@_translates_service_errors
async def _prepare_fs_mkdir(user: dict, path: str) -> Any:
    return _draft(f"创建目录 {path}", {"kind": "mkdir", "path": path})


@_translates_service_errors
async def _impl_fs_mkdir(user: dict, path: str) -> dict:
    data = await asyncio.to_thread(file_ops.make_dir, path)
    return _cap({"kind": "mkdir", **data})


@_translates_service_errors
async def _prepare_fs_rename(user: dict, source: str, target: str) -> Any:
    return _draft(
        f"重命名 {source} → {target}",
        {"kind": "rename", "from": source, "to": target},
    )


@_translates_service_errors
async def _impl_fs_rename(user: dict, source: str, target: str) -> dict:
    data = await asyncio.to_thread(file_ops.rename_path, source, target)
    return _cap({"kind": "rename", **data})


def _count_delete_entries(path: str, recursive: bool) -> tuple[int, bool]:
    """预览用条目数；超过 ``MAX_RECURSIVE_ENTRIES`` 只标记，不在这里拒绝。"""
    entry, _ = file_ops.resolve_entry_path(path)
    if not os.path.lexists(entry):
        return 0, False  # 不存在交给 apply 报 404
    if not recursive or os.path.islink(entry) or not os.path.isdir(entry):
        return 1, False
    count = file_ops._count_entries(entry)
    return count, count > file_ops.MAX_RECURSIVE_ENTRIES


@_translates_service_errors
async def _prepare_fs_delete(user: dict, path: str, recursive: bool = False) -> Any:
    flag = _as_bool(recursive)
    entries, truncated = await asyncio.to_thread(_count_delete_entries, path, flag)
    suffix = "（递归）" if flag else ""
    return _draft(
        f"删除 {path}{suffix}，条目 {entries}",
        {
            "kind": "delete",
            "path": path,
            "entries": entries,
            "entries_truncated": truncated,
        },
    )


@_translates_service_errors
async def _impl_fs_delete(user: dict, path: str, recursive: bool = False) -> dict:
    flag = _as_bool(recursive)
    data = await asyncio.to_thread(
        functools.partial(file_ops.delete_path, path, recursive=flag)
    )
    return _cap({"kind": "delete", **data})


def _octal(mode: Any) -> str:
    try:
        return f"0o{int(mode):03o}"
    except (TypeError, ValueError):
        return str(mode)


@_translates_service_errors
async def _prepare_fs_chmod(user: dict, path: str, mode: int) -> Any:
    info = await asyncio.to_thread(file_ops.stat_entry, path)
    before = _octal(info.get("mode"))
    after = _octal(mode)
    return _draft(
        f"修改 {path} 权限 {before} → {after}",
        {"kind": "chmod", "path": path, "from": before, "to": after},
    )


@_translates_service_errors
async def _impl_fs_chmod(user: dict, path: str, mode: int) -> dict:
    data = await asyncio.to_thread(file_ops.chmod_path, path, int(mode))
    return _cap({"kind": "chmod", **data, "mode": _octal(data.get("mode"))})


# --------------------------------------------------------------------------- #
# 写工具：数据库
# --------------------------------------------------------------------------- #


@_translates_service_errors
async def _prepare_db_insert(user: dict, table: str, values: dict) -> Any:
    payload = _require_object("values", values)
    columns = sorted(str(key) for key in payload)
    return _draft(
        f"向 {table} 插入 1 行（{', '.join(columns)}）",
        {
            "kind": "row_insert",
            "table": table,
            "columns": columns,
            "values": payload,
        },
    )


@_translates_service_errors
async def _impl_db_insert(user: dict, table: str, values: dict) -> dict:
    payload = _require_object("values", values)
    async with session_scope() as session:
        data = await db_content.insert_row(session, table, payload)
    return _cap({"kind": "row_insert", "table": table, **data})


@_translates_service_errors
async def _prepare_db_update(
    user: dict, table: str, key: dict, values: dict
) -> Any:
    key_payload = _require_object("key", key)
    value_payload = _require_object("values", values)
    return _draft(
        f"更新 {table} 1 行（{', '.join(sorted(str(item) for item in key_payload))}）",
        {
            "kind": "row_update",
            "table": table,
            "key": key_payload,
            "values": value_payload,
        },
    )


@_translates_service_errors
async def _impl_db_update(
    user: dict, table: str, key: dict, values: dict
) -> dict:
    key_payload = _require_object("key", key)
    value_payload = _require_object("values", values)
    async with session_scope() as session:
        data = await db_content.update_row(session, table, key_payload, value_payload)
    return _cap({"kind": "row_update", "table": table, **data})


@_translates_service_errors
async def _prepare_db_delete(user: dict, table: str, key: dict) -> Any:
    key_payload = _require_object("key", key)
    return _draft(
        f"从 {table} 删除 1 行（{', '.join(sorted(str(item) for item in key_payload))}）",
        {"kind": "row_delete", "table": table, "key": key_payload},
    )


@_translates_service_errors
async def _impl_db_delete(user: dict, table: str, key: dict) -> dict:
    key_payload = _require_object("key", key)
    async with session_scope() as session:
        data = await db_content.delete_row(session, table, key_payload)
    return _cap({"kind": "row_delete", "table": table, **data})


@_translates_service_errors
async def _prepare_db_ddl(user: dict, sql: str, confirm: str | None = None) -> Any:
    # 静态闸先跑：非法 SQL 连提案都不该建（否则用户要为一条注定失败的语句输码）。
    plan = db_content.validate_ddl_sql(sql, confirm)
    suffix = "（破坏性语句）" if plan["destructive"] else ""
    return _draft(
        f"{plan['summary']}{suffix}",
        {
            "kind": "sql",
            "sql": plan["sql"],
            "summary": plan["summary"],
            "verb": plan["verb"],
            "target": plan["target"],
            "destructive": plan["destructive"],
        },
    )


@_translates_service_errors
async def _impl_db_ddl(user: dict, sql: str, confirm: str | None = None) -> dict:
    async with session_scope() as session:
        data = await db_content.run_ddl(session, sql, confirm)
    return _cap({"kind": "sql", **data})


# --------------------------------------------------------------------------- #
# 写工具：SSH
# --------------------------------------------------------------------------- #


def _ssh_target() -> str:
    target = ssh_bridge.load_target()
    return f"{target.user}@{target.host}:{target.port}"


@_translates_service_errors
async def _prepare_ssh_exec(user: dict, command: str, timeout: int = 30) -> Any:
    return _draft(
        f"在 {_ssh_target()} 执行命令",
        {"kind": "command", "target": _ssh_target(), "command": command},
    )


async def _audit_ssh(user: dict, command: str, result: dict) -> None:
    """审计只写「命令 + 退出码」，输出正文一概不进日志（契约 §4.2）。"""
    detail = f"exit={result.get('exit_status')} cmd={redact(command)}"
    try:
        await audit_system_op(
            user.get("id") or "",
            action="agent_ssh_exec",
            detail=detail[:500],
            log_type="system_ssh_command",
        )
    except Exception:  # noqa: BLE001 - 审计是旁路，写失败不改变业务结论
        logger.warning("agent_ssh_exec audit write failed", exc_info=True)


def _fit_command(result: dict) -> dict:
    """命令结果超预算时按流截断，保住退出码与 target（这两个才是判断依据）。"""
    payload = {"kind": "command", **result}
    if len(tool_impl.to_json_text(payload)) <= MAX_TOOL_RESULT_CHARS:
        return payload

    frames = dict(payload)
    frames["stdout"], frames["stderr"] = "", ""
    overhead = len(tool_impl.to_json_text(frames))
    share = max(1, (MAX_TOOL_RESULT_CHARS - overhead) // 2)

    truncated = dict(result.get("truncated") or {})
    for stream in ("stdout", "stderr"):
        value = str(payload.get(stream) or "")
        if len(value) > share:
            payload[stream] = value[:share]
            truncated[stream] = True
    payload["truncated"] = truncated
    return payload


@_translates_service_errors
async def _impl_ssh_exec(user: dict, command: str, timeout: int = 30) -> dict:
    result = await ssh_exec.run_once(command, timeout=float(timeout))
    await _audit_ssh(user, command, result)
    return _cap(_fit_command(result))


# --------------------------------------------------------------------------- #
# 注册（17 个：7 读 + 10 写）
# --------------------------------------------------------------------------- #


def _params(properties: dict, required: tuple[str, ...]) -> dict:
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


_STR = {"type": "string"}
_INT = {"type": "integer"}
_BOOL = {"type": "boolean"}
_OBJ = {"type": "object"}

_PATH = {**_STR, "description": "绝对路径"}

_register(
    ToolDef(
        name="fs_list",
        description="列出目录内容（目录在前、按名排序，含大小/权限/修改时间）。只读。",
        kind=READ,
        params=_params(
            {
                "path": _PATH,
                "limit": {
                    **_INT,
                    "minimum": 1,
                    "maximum": FS_LIST_MAX_ENTRIES,
                    "description": f"最多返回多少条（1-{FS_LIST_MAX_ENTRIES}，默认 100）",
                },
                "offset": {**_INT, "minimum": 0, "description": "跳过多少条（默认 0）"},
            },
            ("path",),
        ),
        permission=_admin_only,
        impl=_impl_fs_list,
        guard=ops_auth.ensure_system_admin,
    )
)

_register(
    ToolDef(
        name="fs_stat",
        description="读取单个路径的元信息（类型、大小、权限、mtime、符号链接目标）。只读。",
        kind=READ,
        params=_params({"path": _PATH}, ("path",)),
        permission=_admin_only,
        impl=_impl_fs_stat,
        guard=ops_auth.ensure_system_admin,
    )
)

_register(
    ToolDef(
        name="fs_read",
        description="读取文本文件正文（默认 8KB，最多 32KB）。二进制文件会被拒绝。只读。",
        kind=READ,
        params=_params(
            {
                "path": _PATH,
                "max_bytes": {
                    **_INT,
                    "minimum": 1,
                    "maximum": FS_READ_MAX_BYTES,
                    "description": f"最多读取多少字节（≤{FS_READ_MAX_BYTES}，默认 8000）",
                },
            },
            ("path",),
        ),
        permission=_admin_only,
        impl=_impl_fs_read,
        guard=ops_auth.ensure_system_admin,
    )
)

_register(
    ToolDef(
        name="db_schema",
        description="列出当前 schema 的表与视图（名称、类型、行数估计）。只读。",
        kind=READ,
        params=_params({}, ()),
        permission=_admin_only,
        impl=_impl_db_schema,
        guard=ops_auth.ensure_system_admin,
    )
)

_register(
    ToolDef(
        name="db_columns",
        description="读取某表的列定义与主键。只读。",
        kind=READ,
        params=_params({"table": {**_STR, "description": "表名"}}, ("table",)),
        permission=_admin_only,
        impl=_impl_db_columns,
        guard=ops_auth.ensure_system_admin,
    )
)

_register(
    ToolDef(
        name="db_rows",
        description="分页读取整表数据（默认 50 行，最多 50 行/页；可用 offset 翻页）。只读。",
        kind=READ,
        params=_params(
            {
                "table": {**_STR, "description": "表名"},
                "limit": {
                    **_INT,
                    "minimum": 1,
                    "maximum": DB_ROWS_MAX_REQUEST,
                    "description": f"每页行数（1-{DB_ROWS_MAX_REQUEST}，实际按 {DB_ROWS_MAX_ROWS} 封顶）",
                },
                "offset": {**_INT, "minimum": 0, "description": "跳过多少行（默认 0）"},
                "order_by": {**_STR, "description": "排序列（缺省按主键）"},
                "desc": {**_BOOL, "description": "是否倒序（默认 false）"},
            },
            ("table",),
        ),
        permission=_admin_only,
        impl=_impl_db_rows,
        guard=ops_auth.ensure_system_admin,
    )
)

_register(
    ToolDef(
        name="db_query",
        description="执行只读 SQL（仅 SELECT/WITH，最多 100 行、30 秒超时）。只读。",
        kind=READ,
        params=_params(
            {
                "sql": {**_STR, "description": "单条只读 SQL"},
                "limit": {
                    **_INT,
                    "minimum": 1,
                    "maximum": DB_QUERY_MAX_REQUEST,
                    "description": f"最多返回多少行（1-{DB_QUERY_MAX_REQUEST}，实际按 {DB_QUERY_MAX_ROWS} 封顶）",
                },
            },
            ("sql",),
        ),
        permission=_admin_only,
        impl=_impl_db_query,
        guard=ops_auth.ensure_system_admin,
    )
)

_register(
    ToolDef(
        name="fs_write",
        description="写入文本文件（原子替换）。写操作：先生成 diff 提案，另一位管理员确认后执行。",
        kind=WRITE,
        params=_params(
            {
                "path": _PATH,
                "content": {**_STR, "description": "完整的新文件内容（不是补丁）"},
                "encoding": {
                    **_STR,
                    "enum": ["utf-8", "gbk"],
                    "description": "编码（默认 utf-8）",
                },
            },
            ("path", "content"),
        ),
        permission=_admin_only,
        impl=_impl_fs_write,
        prepare=_prepare_fs_write,
        guard=ops_auth.ensure_system_admin,
        dual_auth_action=WRITE_AUTH_ACTION,
    )
)

_register(
    ToolDef(
        name="fs_mkdir",
        description="创建目录（父目录必须存在）。写操作：需另一位管理员确认。",
        kind=WRITE,
        params=_params({"path": _PATH}, ("path",)),
        permission=_admin_only,
        impl=_impl_fs_mkdir,
        prepare=_prepare_fs_mkdir,
        guard=ops_auth.ensure_system_admin,
        dual_auth_action=WRITE_AUTH_ACTION,
    )
)

_register(
    ToolDef(
        name="fs_rename",
        description="重命名/移动路径（同文件系统，目标不能已存在）。写操作：需另一位管理员确认。",
        kind=WRITE,
        params=_params(
            {
                "source": {**_STR, "description": "源绝对路径"},
                "target": {**_STR, "description": "目标绝对路径"},
            },
            ("source", "target"),
        ),
        permission=_admin_only,
        impl=_impl_fs_rename,
        prepare=_prepare_fs_rename,
        guard=ops_auth.ensure_system_admin,
        dual_auth_action=WRITE_AUTH_ACTION,
    )
)

_register(
    ToolDef(
        name="fs_delete",
        description="删除文件或目录（目录需 recursive=true，条目上限 10000）。写操作：需另一位管理员确认。",
        kind=WRITE,
        params=_params(
            {
                "path": _PATH,
                "recursive": {**_BOOL, "description": "是否递归删除目录（默认 false）"},
            },
            ("path",),
        ),
        permission=_admin_only,
        impl=_impl_fs_delete,
        prepare=_prepare_fs_delete,
        guard=ops_auth.ensure_system_admin,
        dual_auth_action=WRITE_AUTH_ACTION,
    )
)

_register(
    ToolDef(
        name="fs_chmod",
        description="修改权限位（0o000-0o777，即 0-511）。写操作：需另一位管理员确认。",
        kind=WRITE,
        params=_params(
            {
                "path": _PATH,
                "mode": {
                    **_INT,
                    "minimum": 0,
                    "maximum": 0o777,
                    "description": "十进制权限位（例如 420 即 0o644）",
                },
            },
            ("path", "mode"),
        ),
        permission=_admin_only,
        impl=_impl_fs_chmod,
        prepare=_prepare_fs_chmod,
        guard=ops_auth.ensure_system_admin,
        dual_auth_action=WRITE_AUTH_ACTION,
    )
)

_register(
    ToolDef(
        name="db_insert",
        description="向表中插入一行（列名→值）。写操作：需另一位管理员确认。",
        kind=WRITE,
        params=_params(
            {
                "table": {**_STR, "description": "表名"},
                "values": {**_OBJ, "description": "列名→值"},
            },
            ("table", "values"),
        ),
        permission=_admin_only,
        impl=_impl_db_insert,
        prepare=_prepare_db_insert,
        guard=ops_auth.ensure_system_admin,
        dual_auth_action=WRITE_AUTH_ACTION,
    )
)

_register(
    ToolDef(
        name="db_update",
        description="按主键更新一行（key=主键列，values=要改的列）。写操作：需另一位管理员确认。",
        kind=WRITE,
        params=_params(
            {
                "table": {**_STR, "description": "表名"},
                "key": {**_OBJ, "description": "主键列→值"},
                "values": {**_OBJ, "description": "待更新列→新值"},
            },
            ("table", "key", "values"),
        ),
        permission=_admin_only,
        impl=_impl_db_update,
        prepare=_prepare_db_update,
        guard=ops_auth.ensure_system_admin,
        dual_auth_action=WRITE_AUTH_ACTION,
    )
)

_register(
    ToolDef(
        name="db_delete",
        description="按主键删除一行（key=主键列）。写操作：需另一位管理员确认。",
        kind=WRITE,
        params=_params(
            {
                "table": {**_STR, "description": "表名"},
                "key": {**_OBJ, "description": "主键列→值"},
            },
            ("table", "key"),
        ),
        permission=_admin_only,
        impl=_impl_db_delete,
        prepare=_prepare_db_delete,
        guard=ops_auth.ensure_system_admin,
        dual_auth_action=WRITE_AUTH_ACTION,
    )
)

_register(
    ToolDef(
        name="db_ddl",
        description="执行一条 DDL/DML（破坏性语句需 confirm=目标表名）。写操作：需另一位管理员确认。",
        kind=WRITE,
        params=_params(
            {
                "sql": {**_STR, "description": "单条 DDL/DML 语句"},
                "confirm": {
                    **_STR,
                    "description": "破坏性语句（DROP/TRUNCATE/UPDATE/DELETE/MERGE 等）必须等于目标表名",
                },
            },
            ("sql",),
        ),
        permission=_admin_only,
        impl=_impl_db_ddl,
        prepare=_prepare_db_ddl,
        guard=ops_auth.ensure_system_admin,
        dual_auth_action=WRITE_AUTH_ACTION,
    )
)

_register(
    ToolDef(
        name="ssh_exec",
        description="在本机 SSH 目标上执行一条命令（≤1024 字符、≤60 秒、无 PTY）。写操作：需另一位管理员确认。",
        kind=WRITE,
        params=_params(
            {
                "command": {
                    **_STR,
                    "maxLength": ssh_exec.MAX_COMMAND_CHARS,
                    "description": "要执行的命令（单条 shell 命令行）",
                },
                "timeout": {
                    **_INT,
                    "minimum": 1,
                    "maximum": int(ssh_exec.MAX_TIMEOUT_SECONDS),
                    "description": "超时秒数（默认 30，最多 60）",
                },
            },
            ("command",),
        ),
        permission=_admin_only,
        impl=_impl_ssh_exec,
        prepare=_prepare_ssh_exec,
        guard=ops_auth.ensure_system_admin,
        dual_auth_action=WRITE_AUTH_ACTION,
    )
)


__all__ = [
    "DB_QUERY_MAX_ROWS",
    "DB_ROWS_MAX_ROWS",
    "FS_LIST_MAX_ENTRIES",
    "FS_READ_MAX_BYTES",
    "MAX_TOOL_RESULT_CHARS",
    "REDACT_PLACEHOLDER",
    "WRITE_AUTH_ACTION",
    "redact",
]
