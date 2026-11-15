"""Manual ADB backup (prod → adb2): trigger plus read-only progress parsing.

备份真正由服务器上的 systemd oneshot 单元 ``adb-sync.service`` 执行
（``/root/adb-backup/sync_adb.py``，实测约 15 分钟）。本模块只做两件事：

* :func:`trigger_backup` —— 先探 ``systemctl is-active adb-sync`` 排空重复触发，
  再用 ``sudo -n /usr/bin/systemctl start --no-block adb-sync.service`` 提交
  （sudoers 白名单里的**唯一**一条命令，参数顺序必须逐字一致，所以整条命令以
  argv 形式给 ``asyncio.create_subprocess_exec``，绝不经过 shell）；
* :func:`backup_snapshot` —— 只读 ``/var/log/adb-sync.log`` 的**尾部**，解析出
  结构化进度，供 ``GET /api/system/db/backup/progress`` 轮询。

安全红线：日志原文与 DSN 永不外泄。快照只含正则抽出的结构化字段（表名、计数、
耗时、ISO 时间戳）与本模块自写的中文 ``message``；``OK connected src=… dst=…``
这类含 DSN 的行只用来**定位本次 run 的起点**，其内容一个字节都不回传。

依赖的外部事实（按部署现状写成常量，本模块不改动它们）：

* 单元名 ``adb-sync.service``（``is-active`` 用 ``adb-sync``，无需 sudo）；
* 日志 ``/var/log/adb-sync.log``（root:www 0640，``www`` 可读）；
* 单元运行期间 ``systemctl is-active`` 报 ``activating``（oneshot + ``--no-block``），
  故快照里只有 ``activating`` 才判 running —— 若该单元日后改成
  ``RemainAfterExit=yes``，完成后会是 ``active``，本判定不会误报成「一直在跑」。
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from typing import Any, Iterable

_logger = logging.getLogger(__name__)

SYSTEMCTL = "/usr/bin/systemctl"
SYSTEMD_UNIT = "adb-sync.service"
SYSTEMD_UNIT_NAME = "adb-sync"
LOG_PATH = "/var/log/adb-sync.log"

# 手动触发的**首选**通道：后端写一个请求文件，root 的 systemd ``.path`` 单元
# 监听到存在就启动 ``adb-sync.service`` 并删掉它。
#
# 为什么不再直接用 sudo：生产 ``zvms4.service`` 带 ``NoNewPrivileges=true``，
# sudo 在这个进程里一定会失败（"sudo: ... disable the flag"）。而备份脚本必须
# 以 root 读 600 的 wallet1，又不能为了它去掉 NNP。路径单元把「触发」降级成
# 「写一个 www-data 可写的文件」，提权动作由 systemd（root）完成，服务本身
# 仍然没有任何提权能力。
TRIGGER_FILE_ENV = "ZVP5_ADB_SYNC_TRIGGER_FILE"
DEFAULT_TRIGGER_FILE = "/www/zvms4/backend/data/adb-sync.request"
#: 只有该单元存在时才走文件触发；否则保留下面的 sudo 兼容路径（本地/旧部署）。
TRIGGER_PATH_UNIT = "/etc/systemd/system/adb-sync-trigger.path"

_TRIGGER_ARGV = ("sudo", "-n", SYSTEMCTL, "start", "--no-block", SYSTEMD_UNIT)
_TRIGGER_TIMEOUT_SECONDS = 10.0
_PROBE_TIMEOUT_SECONDS = 5.0

_LOG_TAIL_BYTES = 200_000
_LOG_TAIL_LINES = 500

STATE_IDLE = "idle"
STATE_RUNNING = "running"
STATE_OK = "ok"
STATE_FAILED = "failed"

# 内部态：日志里有起点但还没出现终态；由 systemd 的 is-active 在快照层收敛成
# running / idle。它不是对外契约的一部分。
_STATE_UNSETTLED = "unsettled"

BUSY_MESSAGE = "备份任务正在执行中，请稍后重试"
TRIGGER_FAILED_MESSAGE = "启动备份任务失败"
TRIGGER_TIMEOUT_MESSAGE = "启动备份任务超时，请稍后重试"
# 主机迁移（zvp-frontend → zvp-backend）后 systemd 单元没有跟着搬，`systemctl start`
# 回的是 "Unit adb-sync.service could not be found."。把它翻成一句可执行的提示，
# 而不是让管理员对着 "启动备份任务失败" 猜是 AES/密钥还是权限。
NOT_INSTALLED_MESSAGE = "备份服务未安装：目标主机缺少 adb-sync.service"

# 审计行落在 app_logs 清理保护集内（level=warning），后续保留期清理不会删掉它。
AUDIT_TYPE = "adb_backup"
AUDIT_LEVEL = "warning"

# 进日志流的行都是 ISO 时间戳前缀（``2026-10-19T03:30:45``）；空格分隔也容忍。
_ISO_TIMESTAMP = (
    r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?"
)
_LEADING_TIMESTAMP_RE = re.compile(rf"^({_ISO_TIMESTAMP})\s*")

# 只认「结构」，不认「内容」：匹配到的行本身（尤其含 DSN 的那行）绝不回传。
_CONNECTED_RE = re.compile(r"OK connected\b")
_TABLES_TOTAL_RE = re.compile(r"source tables \((\d+)\)")
_ROWS_LOADED_RE = re.compile(r'"([A-Za-z0-9_$#]+)":\s*(\d+)\s+rows loaded\b')
_SYNC_OK_RE = re.compile(
    r"SYNC_OK\b[^\r\n]*?tables=(\d+)\s+rows=(\d+)\s+elapsed=([\d.]+)s"
)
_SYNC_FAIL_RE = re.compile(r"SYNC_FAIL\b")
_INSTANCE_RUNNING_RE = re.compile(r"another sync instance running")
_TABLE_NAME_RE = re.compile(r"^[A-Za-z0-9_$#]+$")

# stderr/stdout 摘要里的 ``src=… / dst=…`` 一律抹掉：那些位置上只可能出现 DSN。
_DSN_RE = re.compile(r"\b(?:src|dst)=[^\s,;]+", re.IGNORECASE)
_MAX_SUMMARY_CHARS = 300

# systemd 找不到单元时的两种输出（不同版本措辞不同）；只用于把错误翻译成
# 「备份服务未安装」，不参与正常路径。
_UNIT_MISSING_RE = re.compile(
    r"Unit\s+\S+\s+(?:could not be found|not found|not loaded)", re.IGNORECASE
)


class BackupBusy(RuntimeError):
    """An ``adb-sync`` backup is already running."""

    def __init__(self, message: str = BUSY_MESSAGE) -> None:
        super().__init__(message)


class BackupTriggerError(RuntimeError):
    """The backup could not be submitted (subprocess timeout or non-zero exit).

    ``message`` 已经是面向前端的中文短句（含脱敏后的 stderr 摘要），路由层直接
    ``str(exc)`` 当 detail，不再拼字符串；本模块不依赖任何 Web 框架，HTTP 语义由
    ``routers/db_system_router.py`` 翻译。
    """

    def __init__(self, message: str = TRIGGER_FAILED_MESSAGE) -> None:
        super().__init__(message)


def _empty_snapshot() -> dict[str, Any]:
    """无日志 / 日志为空时的冻结结构（数值位留 None，不编造计数）。"""
    return {
        "state": STATE_IDLE,
        "started_at": None,
        "finished_at": None,
        "tables_total": None,
        "tables_done": 0,
        "rows_loaded": 0,
        "current_table": None,
        "elapsed_seconds": None,
        "message": "暂无备份任务运行",
        "_contended": False,
    }


def _iso_timestamp(raw: str | None) -> str | None:
    """日志前缀 → ISO8601（原样透传，仅把日期/时间的空格分隔换成 ``T``）。"""
    if not raw:
        return None
    value = raw.strip().replace(" ", "T", 1)
    return value or None


def parse_backup_log(lines: Iterable[str]) -> dict[str, Any]:
    """Pure parser: ``adb-sync`` 日志尾部行 → 快照字段（外加内部 ``_contended``）。

    「本次 run」= **最后一次** ``OK connected`` 行之后的事件（该行本身只用来取
    ``started_at``）。逐行匹配：``source tables (N)`` 给 ``tables_total``；
    ``"TABLE": N rows loaded (S s)`` 累加 ``tables_done`` / ``rows_loaded`` 并更新
    ``current_table``；``SYNC_OK tables=N rows=M elapsed=X`` 收敛成 ok 并取
    ``finished_at`` / ``elapsed_seconds``；``SYNC_FAIL`` 收敛成 failed；
    ``another sync instance running, exit`` 记成 failed 并置 ``_contended``（由
    :func:`backup_snapshot` 结合 is-active 决定是否其实是 running）。

    后出现的终态覆盖先出现的（同一段里正常只有一条终态行，这里只是容错）。
    表名走 :data:`_TABLE_NAME_RE` 白名单，避免把任意文本当表名回传。
    返回值里**不含**任何日志原文。
    """
    events: list[tuple[str | None, str]] = []
    for raw in lines:
        line = str(raw)
        match = _LEADING_TIMESTAMP_RE.match(line)
        if match:
            events.append((_iso_timestamp(match.group(1)), line[match.end():]))
        else:
            events.append((None, line))

    start: int | None = None
    for index, (_, body) in enumerate(events):
        if _CONNECTED_RE.search(body):
            start = index
    if start is None:
        return _empty_snapshot()

    snapshot = _empty_snapshot()
    snapshot["state"] = _STATE_UNSETTLED
    snapshot["started_at"] = events[start][0]
    snapshot["message"] = "备份进行中"

    for timestamp, body in events[start + 1:]:
        total = _TABLES_TOTAL_RE.search(body)
        if total is not None:
            snapshot["tables_total"] = int(total.group(1))
            continue

        loaded = _ROWS_LOADED_RE.search(body)
        if loaded is not None:
            name = loaded.group(1)
            if _TABLE_NAME_RE.match(name):
                snapshot["current_table"] = name
                snapshot["tables_done"] += 1
                snapshot["rows_loaded"] += int(loaded.group(2))
            continue

        ok = _SYNC_OK_RE.search(body)
        if ok is not None:
            snapshot["state"] = STATE_OK
            snapshot["finished_at"] = timestamp
            snapshot["elapsed_seconds"] = float(ok.group(3))
            snapshot["_contended"] = False
            continue

        if _SYNC_FAIL_RE.search(body):
            snapshot["state"] = STATE_FAILED
            snapshot["finished_at"] = timestamp
            snapshot["_contended"] = False
            continue

        if _INSTANCE_RUNNING_RE.search(body):
            snapshot["state"] = STATE_FAILED
            snapshot["finished_at"] = timestamp
            snapshot["_contended"] = True
            continue

    return snapshot


def _is_activating(unit_state: str) -> bool:
    """只有 ``activating`` 才算「正在跑」（见模块 docstring 的 unit 假设）。"""
    return (unit_state or "").strip() == "activating"


def _resolve_state(snapshot: dict[str, Any], unit_state: str) -> None:
    """用 ``systemctl is-active`` 收敛「无终态」与「另一实例占用」两种情形。"""
    if snapshot["state"] == _STATE_UNSETTLED:
        snapshot["state"] = STATE_RUNNING if _is_activating(unit_state) else STATE_IDLE
    elif snapshot.get("_contended") and _is_activating(unit_state):
        snapshot["state"] = STATE_RUNNING


def _message_for(snapshot: dict[str, Any]) -> str:
    """中文状态说明；全部由本模块自写，绝不拼接日志原文。"""
    state = snapshot["state"]
    total = snapshot["tables_total"]
    done = snapshot["tables_done"]

    if state == STATE_OK:
        elapsed = snapshot["elapsed_seconds"]
        detail = (
            f"{done}/{total} 张表、{snapshot['rows_loaded']} 行"
            if total is not None
            else f"{done} 张表、{snapshot['rows_loaded']} 行"
        )
        if elapsed is not None:
            detail = f"{detail}，用时 {elapsed:g} 秒"
        return f"最近一次备份成功（{detail}）"

    if state == STATE_FAILED:
        if snapshot.get("_contended"):
            return "检测到另一个备份实例正在运行，本次触发未执行"
        return "最近一次备份失败，请查看服务器备份日志"

    if state == STATE_RUNNING:
        if snapshot.get("_contended"):
            return "备份进行中（另一个备份实例仍在运行）"
        if total:
            return f"备份进行中：已完成 {done}/{total} 张表"
        if done:
            return f"备份进行中：已完成 {done} 张表"
        return "备份进行中"

    return "暂无备份任务运行"


def _public(snapshot: dict[str, Any]) -> dict[str, Any]:
    """冻结结构：丢内部键、补齐 message，键集与顺序固定。"""
    payload = {key: value for key, value in snapshot.items() if not key.startswith("_")}
    payload["message"] = _message_for(snapshot)
    return payload


def _read_log_tail(
    path: str | os.PathLike[str],
    max_bytes: int = _LOG_TAIL_BYTES,
    max_lines: int = _LOG_TAIL_LINES,
) -> list[str]:
    """只读文件尾部：最多 ``max_bytes`` 字节 / ``max_lines`` 行。

    文件不存在、为空或不可读（权限/轮转竞态）→ 空列表（调用方据此判 idle）。
    """
    try:
        with open(path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            offset = max(0, size - max_bytes)
            handle.seek(offset)
            data = handle.read(max_bytes)
    except OSError as exc:
        _logger.debug("adb-sync log tail unavailable: %s", exc)
        return []

    lines = data.decode("utf-8", errors="replace").splitlines()
    if offset > 0 and lines:
        del lines[0]
    return lines[-max_lines:]


async def _run_command(argv: tuple[str, ...], timeout: float) -> tuple[int, str, str]:
    """argv 直执（无 shell），``(returncode, stdout, stderr)``；超时抛 TimeoutError。"""
    process = await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except (asyncio.TimeoutError, TimeoutError):
        process.kill()
        await process.wait()
        raise
    return (
        process.returncode if process.returncode is not None else -1,
        stdout.decode("utf-8", errors="replace"),
        stderr.decode("utf-8", errors="replace"),
    )


async def _unit_is_active(unit: str = SYSTEMD_UNIT_NAME) -> str:
    """``systemctl is-active <unit>`` 的输出；探不到时返回空串。

    单元 inactive 时 ``is-active`` 以非零退出码结束 —— 那是**正常结果**而不是
    错误，所以这里不看 returncode，只看输出。
    """
    try:
        _, stdout, stderr = await _run_command(
            (SYSTEMCTL, "is-active", unit), _PROBE_TIMEOUT_SECONDS
        )
    except (asyncio.TimeoutError, TimeoutError):
        _logger.warning("systemctl is-active timed out for %s", unit)
        return ""
    except OSError as exc:
        _logger.warning("systemctl is-active unavailable: %s", exc)
        return ""
    return (stdout or stderr).strip()


def _safe_summary(text: str) -> str:
    """命令输出的最后一行有效内容：抹掉 ``src=/dst=`` 并截断。"""
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    if not lines:
        return ""
    return _DSN_RE.sub("<redacted>", lines[-1])[:_MAX_SUMMARY_CHARS]


def trigger_file() -> str | None:
    """可写的请求文件路径；该部署没有 path 单元/目录不可写时返回 ``None``。

    判定只做两件事：path 单元存在（否则写了没人监听）与父目录可写（否则写完
    就是 OSError）。两者任一不满足都回落到 sudo 兼容路径，调用方不必区分。
    """
    if not os.path.exists(TRIGGER_PATH_UNIT):
        return None
    path = os.environ.get(TRIGGER_FILE_ENV) or DEFAULT_TRIGGER_FILE
    parent = os.path.dirname(path) or "."
    try:
        return path if os.path.isdir(parent) and os.access(parent, os.W_OK) else None
    except OSError:
        return None


async def trigger_backup() -> dict[str, Any]:
    """提交一次手动备份；已在跑 → :class:`BackupBusy`，提交失败 → :class:`BackupTriggerError`。

    先探 ``is-active``（unit 运行期是 ``activating``）；空闲则优先写 systemd
    ``.path`` 单元的请求文件（生产），没有该单元时回落到 sudoers 白名单里的
    ``systemctl start --no-block``（本地/旧部署）。

    非零退出码与超时都抛 :class:`BackupTriggerError`（已带脱敏后的简短中文说明）——
    本模块不感知 HTTP：``routers/db_system_router.py`` 必须把它**显式**翻成
    ``HTTPException(500)``，否则会被该 router 的 ``_DatabaseStatusRoute`` 包成 503，
    把「启动失败」伪装成「监控不可用」。

    返回提交后立刻采到的快照（``--no-block`` 的含义：不在这里等 15 分钟）。
    """
    if _is_activating(await _unit_is_active()):
        raise BackupBusy(BUSY_MESSAGE)

    request_path = trigger_file()
    if request_path is not None:
        try:
            # 内容只是给运维看的触发时间；systemd 只看「文件在不在」。
            with open(request_path, "w", encoding="ascii") as handle:
                handle.write(f"{int(time.time())}\n")
        except OSError as exc:
            raise BackupTriggerError(TRIGGER_FAILED_MESSAGE) from exc
        return await backup_snapshot()

    try:
        code, stdout, stderr = await _run_command(
            _TRIGGER_ARGV, _TRIGGER_TIMEOUT_SECONDS
        )
    except (asyncio.TimeoutError, TimeoutError) as exc:
        raise BackupTriggerError(TRIGGER_TIMEOUT_MESSAGE) from exc

    if code != 0:
        summary = _safe_summary(stderr) or _safe_summary(stdout)
        if _UNIT_MISSING_RE.search(stderr or "") or _UNIT_MISSING_RE.search(
            stdout or ""
        ):
            raise BackupTriggerError(NOT_INSTALLED_MESSAGE)
        raise BackupTriggerError(
            TRIGGER_FAILED_MESSAGE + (f"：{summary}" if summary else "")
        )

    return await backup_snapshot()


async def backup_snapshot(
    *,
    log_path: str | os.PathLike[str] | None = None,
    unit_state: str | None = None,
) -> dict[str, Any]:
    """``GET /api/system/db/backup/progress`` 的 ``data``（冻结结构）。

    键集逐字固定：``state`` / ``started_at`` / ``finished_at`` / ``tables_total`` /
    ``tables_done`` / ``rows_loaded`` / ``current_table`` / ``elapsed_seconds`` /
    ``message``。``state`` ∈ ``idle | running | ok | failed``。

    ``log_path`` / ``unit_state`` 是给测试（和轮转排查）的注入点；生产调用两者都不传，
    走 :data:`LOG_PATH` 与真实的 ``systemctl is-active``。只有「无终态」或「另一实例
    占用」时才真的去探 ``is-active``，已跑完的 run 不额外花一次子进程。
    """
    path = LOG_PATH if log_path is None else log_path
    snapshot = parse_backup_log(await asyncio.to_thread(_read_log_tail, path))

    if snapshot["state"] == _STATE_UNSETTLED or snapshot["_contended"]:
        state = unit_state if unit_state is not None else await _unit_is_active()
        _resolve_state(snapshot, state)

    return _public(snapshot)


__all__ = [
    "AUDIT_LEVEL",
    "AUDIT_TYPE",
    "BackupBusy",
    "BackupTriggerError",
    "backup_snapshot",
    "parse_backup_log",
    "trigger_backup",
]
