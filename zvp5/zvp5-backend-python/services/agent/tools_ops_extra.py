"""运维补充工具（7 读 + 3 写）：设置快照 / 调度器 / AI 流水 / run 队列 /
用户查找 / 封禁历史 / 服务器时间 + 备份 / 维护模式 / 调度开关。

注册表语义与 ``ops_tools`` / ``tools_platform`` / ``tools_governance`` 完全一致，
本模块不另立一套：

* **读工具直接执行**，``guard=ops_auth.ensure_system_admin`` 每次现查库
  ——token 里的 ``per`` 只是快照，撤权与封禁必须立刻生效；
* **写工具只出提案**：``prepare`` 只读、只产人读摘要与结构化预览；``impl`` 在
  另一位管理员提供 6 位动态码之后才被调用；``dual_auth_action`` 一填，
  ``proposal`` 就强制要求 ``prepare`` + ``guard`` 同时在位（``tools._register``）。

三条本模块自己的铁律 —— 都是「别把运维工具做成泄露面 / 单点故障」：

1. **白名单投影，不是脱敏**。``settings_snapshot`` 只回注册表里的显式字段，值
   按键名命中密钥特征时替换成 ``***``（与 ``db_content._is_sensitive_column``
   同一套判据）；``ai_call_logs`` 只回元数据，正文列（``AI_PROMPT_AUDIT``）
   根本不在查询里。两个工具都不透传整行/整字典。
2. **数据来源只有一份**。调度器一律读 ``main.scheduler`` 与
   ``tools_platform._job_summary``（``scheduler_status`` 的同一实现）；run 队列
   读 ``run_queue`` 的既有阈值（``stale_seconds`` / ``concurrency``）与
   ``ai_runs`` 表，僵尸判定与 ``watchdog_tick`` 同一谓词，不复制第二套口径。
3. **拒绝要早、要可读**。备份已在跑、job 名不存在、维护模式缺原因一律在
   ``prepare`` 就拒（不为注定失败的提案让人输一次动态码），``impl`` 侧再判一次
   ——中间那几分钟状态会变。
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import func, select

from db import backends, system_state
from db.models import ai_runs as ai_runs_table
from db.models import ban_records as ban_records_table
from db.models import ban_rules as ban_rules_table
from db.models import ban_violations as ban_violations_table
from db.models import users as users_table
from db.oracle.session import session_scope
from services import adb_backup, db_content, settings_runtime
from services.ai import config as ai_config
from util.dual_auth import audit_system_op
from util.serialization import iso_str_nonempty

from . import ops_auth, run_queue
from .ops_tools import WRITE_AUTH_ACTION, _admin_only, _cap, _draft, _params
from .tool_impl import AgentError
from .tools import READ, WRITE, ToolDef, _register

# 复用 ``scheduler_status`` 的 job 视图：两个工具看到同一份 id/name/next_run_time，
# 否则「调度器面板」与「调度器控制」对同一个 job 会给出不一致的描述。
from .tools_platform import _job_summary

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# 上限 / 枚举（schema 里出现一次，服务端再判一次：schema 只是给模型的建议）
# --------------------------------------------------------------------------- #

# AI 流水的时间窗：7 天足够回答「昨天/今早谁在报错」，再长就该走统计面板聚合，
# 而不是把几千行流水塞进 8000 字符的工具结果。
LOG_WINDOW_MAX_DAYS = 7
LOG_WINDOW_DEFAULT_DAYS = 1
LOG_ROWS_MAX = 50
LOG_ROWS_DEFAULT = 20
CALL_LOG_STATUSES = ("success", "error", "timeout", "rejected", "busy", "warning")
AI_CAPABILITIES = ("chat", "ocr")

SETTINGS_GROUPS = ("ai", "ops", "all")
SETTINGS_GROUP_DEFAULT = "all"
REDACTED = "***"

MAINTENANCE_ACTIONS = ("enable", "disable")
MAINTENANCE_REASON_MAX = 200  # 与 ``routers/system_router.EnableRequest`` 同宽
MAINTENANCE_ANNOUNCEMENT_MAX = 300

SCHEDULER_ACTIONS = ("pause", "resume", "run_now")
# 唯一有进程内「上次运行」记录的作业：``tasks.retention`` 的手动/定时清理。
RETENTION_JOB_ID = "daily_retention_purge"

AUDIT_LOG_TYPE = "ai_ops_extra"


# --------------------------------------------------------------------------- #
# 小工具
# --------------------------------------------------------------------------- #


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _int_or_none(value: Any) -> int | None:
    """``None`` 保留（= 上游没报），不折算成 0。"""
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _seconds_since(moment: Any, now: datetime) -> int | None:
    if not isinstance(moment, datetime):
        return None
    return max(0, int((now - moment).total_seconds()))


def _epoch_iso(value: Any) -> str | None:
    """epoch 秒 → ISO 字符串（``maintenance_state.since`` 是 ``time.time()``）。"""
    if isinstance(value, datetime):
        return iso_str_nonempty(value)
    try:
        return datetime.fromtimestamp(float(value)).isoformat(sep=" ", timespec="seconds")
    except (TypeError, ValueError, OverflowError, OSError):
        return None


_SECRET_NAME_RE = re.compile(
    r"password|passwd|secret|token|credential|api[_-]?key|private[_-]?key|dsn",
    re.I,
)

# 本模块自己投影的**计数字段**叫 ``tokens``（token 数量，不是凭据）。豁免它，
# 否则「返回 token 用量」这条需求会被自己的脱敏规则抹掉。
_COUNT_FIELDS = frozenset({"tokens"})


def _is_secret_name(name: Any) -> bool:
    """键名是否指代密钥材料：本模块的判据 + ``db_content`` 的敏感列判据。"""
    text = str(name or "")
    if text in _COUNT_FIELDS:
        return False
    return bool(_SECRET_NAME_RE.search(text)) or db_content._is_sensitive_column(text)


def _mask(name: Any, value: Any) -> Any:
    """按键名脱敏并递归容器；命中密钥特征的**值**一律换成 ``***``。

    这里不是「隐藏一部分」，而是「不给」：``***`` 的长度、前缀都不携带原值信息
    （``db_content._redact_cell`` 同一口径）。容器按键逐个判：``{"api_key": …}``
    里的键名照样命中，``{"tokens": {...}}`` 这种计数器容器则原样保留。
    """
    if isinstance(value, dict):
        return {str(key): _mask(key, item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_mask(name, item) for item in value]
    if _is_secret_name(name):
        return REDACTED
    return value


async def _audit(
    user: dict, action: str, detail: str, *, log_type: str = AUDIT_LOG_TYPE
) -> None:
    """审计是旁路：写失败只记 warning，绝不把「已经做完的事」报成失败。"""
    try:
        await audit_system_op(
            str(user.get("id") or ""),
            action=action,
            detail=detail[:500],
            log_type=log_type,
        )
    except Exception:  # noqa: BLE001 - 审计写失败不改变业务结论
        logger.warning("agent %s audit write failed", action, exc_info=True)


def _scheduler():
    """``main.scheduler``（延迟 import：``main`` 会 import 全部 router，成环）。

    与 ``tools_platform._impl_scheduler_status`` 取的是同一个对象：一处 ``pause``
    另一处立刻看得见，不存在「第二个调度器」。
    """
    import main

    return main.scheduler


def _scheduler_failure(exc: Exception, what: str) -> AgentError:
    return AgentError(
        "AI_AGENT_TOOL_FAILED", f"{what} unavailable: {type(exc).__name__}", 503
    )


# --------------------------------------------------------------------------- #
# 读工具 1：settings_snapshot
# --------------------------------------------------------------------------- #


def _settings_group(value: Any) -> str:
    target = str(value or "").strip().lower() or SETTINGS_GROUP_DEFAULT
    if target not in SETTINGS_GROUPS:
        raise AgentError(
            "AI_AGENT_BAD_ARGUMENT",
            f"group must be one of: {', '.join(SETTINGS_GROUPS)}",
            400,
        )
    return target


def _settings_item(item: dict, masked: list[str]) -> dict:
    """注册表视图 → 白名单字段（``value`` 已按键名脱敏）。

    ``all_settings`` 的 ``_view`` 里还有 ``description``/``options``；这里保留
    描述类字段（它们不含密钥），只把**值**收紧：任何键名像凭据的一律 ``***``。
    """
    key = str(item.get("key") or "")
    raw_value = item.get("value")
    value = _mask(key, raw_value)
    if value == REDACTED and raw_value != REDACTED:
        masked.append(key)
    return {
        "key": key,
        "value": value,
        "type": item.get("type"),
        "group": item.get("group"),
        "label": item.get("label"),
        "min": item.get("min"),
        "max": item.get("max"),
        "updated_at": iso_str_nonempty(item.get("updated_at")),
        "updated_by": item.get("updated_by"),
    }


def _provider_view() -> list[dict]:
    """上游 provider 的脱敏视图（``services.ai.config.public_provider_view``）。

    那个函数已经是「只留 host + 模型名 + 是否已配」，这里再显式列一次字段：
    白名单写在**消费方**，上游哪天多加一个键也不会自动漏进来。
    """
    try:
        rows = ai_config.public_provider_view()
    except Exception:  # noqa: BLE001 - provider 读不出来不该让设置快照整体失败
        logger.warning("agent settings_snapshot provider view failed", exc_info=True)
        return []
    return [
        {
            "name": row.get("name"),
            "kind": row.get("kind"),
            "base_url": row.get("base_url"),
            "models": list(row.get("models") or []),
            "configured": bool(row.get("configured")),
        }
        for row in rows
        if isinstance(row, dict)
    ]


async def _impl_settings_snapshot(user: dict, group: str = SETTINGS_GROUP_DEFAULT) -> dict:
    target = _settings_group(group)
    # 先刷一次快照：管理员刚在设置中心保存，这里就该看到新值（TTL 5s 只是
    # 跨进程的兜底，不是「看到旧值」的许可）。
    await settings_runtime.ensure_fresh()
    data = await settings_runtime.all_settings()

    masked: list[str] = []
    items = [
        _settings_item(item, masked)
        for item in (data.get("items") or [])
        if isinstance(item, dict) and target in ("all", item.get("group"))
    ]
    items.sort(key=lambda item: item.get("key") or "")

    return _cap(
        {
            "kind": "settings_snapshot",
            "group": target,
            "observed_at": iso_str_nonempty(datetime.now()),
            "count": len(items),
            "settings": items,
            "masked_keys": masked,
            "providers": _provider_view(),
            "fields": [
                "key",
                "value",
                "type",
                "group",
                "label",
                "min",
                "max",
                "updated_at",
                "updated_by",
            ],
            "note": (
                "值白名单投影；键名命中密钥特征（key/secret/token/...）时一律 ***，"
                "上游只回 host 与模型名，不含 API key"
            ),
        }
    )


# --------------------------------------------------------------------------- #
# 读工具 2：scheduler_jobs
# --------------------------------------------------------------------------- #


def _last_result(job_id: Any) -> dict | None:
    """job 的「上次结果」。

    绝大多数 job 没有：``AsyncIOScheduler`` 用的是内存 jobstore，只记下次触发，
    不存上次执行结果。唯一有进程内记录的是留存清理（``tasks.retention``），
    有就如实给，没有就 ``None`` —— 编一个 ``ok`` 比什么都不给更糟。
    """
    if str(job_id or "") != RETENTION_JOB_ID:
        return None
    try:
        from tasks import retention

        snapshot = retention.progress_snapshot()
    except Exception:  # noqa: BLE001 - 进度快照读不到就是「没有上次结果」
        logger.warning("agent scheduler_jobs last_result failed", exc_info=True)
        return None
    return {
        "running": bool(snapshot.get("running")),
        "phase": snapshot.get("phase"),
        "started_at": snapshot.get("started_at"),
        "finished_at": snapshot.get("finished_at"),
        "error": snapshot.get("error"),
    }


def _job_item(job: Any) -> dict:
    """``_job_summary`` + 是否启用 + 上次结果（前两项与 ``scheduler_status`` 同源）。"""
    summary = _job_summary(job)
    next_run = summary.get("next_run_time")
    return {
        **summary,
        # 未排程（pending）或已暂停（next_run_time 为 None）都不在自动触发之列。
        "enabled": bool(next_run) and not summary.get("pending"),
        "last_result": _last_result(summary.get("id")),
    }


async def _impl_scheduler_jobs(user: dict) -> dict:
    try:
        scheduler = _scheduler()
        jobs = scheduler.get_jobs()
    except Exception as exc:  # noqa: BLE001 - 读工具挂掉时巡检应当继续
        return {
            "kind": "scheduler_jobs",
            "available": False,
            "error": f"{type(exc).__name__}: {exc}",
            "jobs": [],
        }

    items = [_job_item(job) for job in jobs]
    items.sort(key=lambda item: item.get("id") or "")
    return _cap(
        {
            "kind": "scheduler_jobs",
            "available": True,
            "running": bool(getattr(scheduler, "running", False)),
            "job_count": len(items),
            "enabled_count": sum(1 for item in items if item.get("enabled")),
            "jobs": items,
            "note": (
                "job 视图与 scheduler_status 同源（main.scheduler）；"
                "last_result 只有留存清理作业有进程内记录，其余 jobstore 不存"
            ),
        }
    )


# --------------------------------------------------------------------------- #
# 读工具 3：ai_call_logs
# --------------------------------------------------------------------------- #


def _window_days(value: Any) -> int:
    days = _int(value, LOG_WINDOW_DEFAULT_DAYS)
    if days < 1 or days > LOG_WINDOW_MAX_DAYS:
        raise AgentError(
            "AI_AGENT_BAD_ARGUMENT",
            f"days must be between 1 and {LOG_WINDOW_MAX_DAYS}",
            400,
        )
    return days


def _log_limit(value: Any) -> int:
    limit = _int(value, LOG_ROWS_DEFAULT)
    if limit < 1 or limit > LOG_ROWS_MAX:
        raise AgentError(
            "AI_AGENT_BAD_ARGUMENT",
            f"limit must be between 1 and {LOG_ROWS_MAX}",
            400,
        )
    return limit


def _optional_choice(value: Any, allowed: tuple[str, ...], name: str) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text not in allowed:
        raise AgentError(
            "AI_AGENT_BAD_ARGUMENT",
            f"{name} must be one of: {', '.join(allowed)}",
            400,
        )
    return text


def _call_log_item(row: dict) -> dict:
    """一行流水 → 白名单元数据。

    ``AI_CALL_LOGS`` 本身没有正文列（正文在 ``AI_PROMPT_AUDIT``，另一个工具、
    另一套访问审计），所以这里连「正文」都不选；``tokens`` 为 ``None`` 是
    「上游没报」，不是 0。
    """
    payload = {
        "oid": str(row.get("_id") or ""),
        "ts": iso_str_nonempty(row.get("created_at")),
        "capability": row.get("capability"),
        "status": row.get("status"),
        "http_status": _int_or_none(row.get("http_status")),
        "error_code": row.get("error_code"),
        "model": row.get("model"),
        "purpose": row.get("purpose"),
        "latency_ms": _int_or_none(row.get("latency_ms")),
        "tokens": {
            "prompt": _int_or_none(row.get("prompt_tokens")),
            "completion": _int_or_none(row.get("completion_tokens")),
            "total": _int_or_none(row.get("total_tokens")),
        },
        "units": _int(row.get("units")),
        "key_scope": row.get("key_scope"),
    }
    # 列名命中敏感特征的整格抹掉（与 db_content 的控制台同一套判据）：这张表
    # 目前没有敏感列，但投影逻辑不该依赖「今天没有」这个事实。
    return {key: _mask(key, value) for key, value in payload.items()}


async def _impl_ai_call_logs(
    user: dict,
    days: int = LOG_WINDOW_DEFAULT_DAYS,
    capability: str | None = None,
    status: str | None = None,
    user_oid: str | None = None,
    limit: int = LOG_ROWS_DEFAULT,
) -> dict:
    window = _window_days(days)
    size = _log_limit(limit)
    capability = _optional_choice(capability, AI_CAPABILITIES, "capability")
    status = _optional_choice(status, CALL_LOG_STATUSES, "status")
    target_user = str(user_oid or "").strip() or None
    since = datetime.now() - timedelta(days=window)

    rows = await backends.get_ai_call_logs_repo().list_page(
        user_oid=target_user,
        capability=capability,
        status=status,
        time_start=since,
        page=1,
        page_size=size,
    )
    items = [_call_log_item(row) for row in rows]
    return _cap(
        {
            "kind": "ai_call_logs",
            "window": {"days": window, "since": iso_str_nonempty(since)},
            "filters": {
                "capability": capability,
                "status": status,
                "user_oid": target_user,
            },
            "limit": size,
            "returned": len(items),
            "capped": len(items) >= size,
            "items": items,
            "fields": [
                "oid",
                "ts",
                "capability",
                "status",
                "http_status",
                "error_code",
                "model",
                "purpose",
                "latency_ms",
                "tokens",
                "units",
                "key_scope",
            ],
            "note": "只回元数据：prompt/completion 正文不在本表，也不在本工具的查询里",
        }
    )


# --------------------------------------------------------------------------- #
# 读工具 4：agent_queue_status
# --------------------------------------------------------------------------- #


def _pump_alive() -> bool:
    """泵协程是否还活着（进程内唯一真相，见 ``run_queue`` docstring）。"""
    task = getattr(run_queue, "_pump_task", None)  # noqa: SLF001 - 同一模块族
    return bool(task is not None and not task.done())


async def _impl_agent_queue_status(user: dict) -> dict:
    now = datetime.now()
    stale_after = run_queue.stale_seconds()
    cutoff = now - timedelta(seconds=stale_after)

    async with session_scope() as session:
        grouped = (
            await session.execute(
                select(
                    ai_runs_table.c.status,
                    func.count().label("total"),
                    func.min(ai_runs_table.c.created_at).label("oldest_created"),
                    func.min(ai_runs_table.c.started_at).label("oldest_started"),
                    func.min(ai_runs_table.c.heartbeat_at).label("oldest_heartbeat"),
                )
                .select_from(ai_runs_table)
                .where(ai_runs_table.c.status.in_(run_queue.ACTIVE_STATUSES))
                .group_by(ai_runs_table.c.status)
            )
        ).all()
        # 僵尸谓词与 ``watchdog_tick`` 逐字一致：running 且心跳早于阈值。
        stale = (
            await session.execute(
                select(func.count())
                .select_from(ai_runs_table)
                .where(
                    ai_runs_table.c.status == "running",
                    ai_runs_table.c.heartbeat_at < cutoff,
                )
            )
        ).scalar_one()
        # 心跳为 NULL 的 running 行不会被 watchdog 命中（SQL 里 NULL < x 为 UNKNOWN）
        # ——单列出来，避免「看着没僵尸，任务其实卡住了」。
        no_heartbeat = (
            await session.execute(
                select(func.count())
                .select_from(ai_runs_table)
                .where(
                    ai_runs_table.c.status == "running",
                    ai_runs_table.c.heartbeat_at.is_(None),
                )
            )
        ).scalar_one()

    by_status = {str(row._mapping["status"]): row._mapping for row in grouped}
    queued = by_status.get("queued")
    running = by_status.get("running")

    return _cap(
        {
            "kind": "agent_queue_status",
            "observed_at": iso_str_nonempty(now),
            "queued": {
                "count": _int(queued["total"]) if queued else 0,
                "oldest_created_at": iso_str_nonempty(queued["oldest_created"]) if queued else None,
                "oldest_wait_seconds": (
                    _seconds_since(queued["oldest_created"], now) if queued else None
                ),
            },
            "running": {
                "count": _int(running["total"]) if running else 0,
                "oldest_started_at": iso_str_nonempty(running["oldest_started"]) if running else None,
                "oldest_running_seconds": (
                    _seconds_since(running["oldest_started"], now) if running else None
                ),
                "oldest_heartbeat_at": (
                    iso_str_nonempty(running["oldest_heartbeat"]) if running else None
                ),
                "stale": _int(stale),
                "without_heartbeat": _int(no_heartbeat),
            },
            "active_total": (
                (_int(queued["total"]) if queued else 0)
                + (_int(running["total"]) if running else 0)
            ),
            "limits": {
                "concurrency": run_queue.concurrency(),
                "max_runs_per_user": run_queue.max_runs_per_user(),
                "heartbeat_seconds": int(run_queue.heartbeat_seconds()),
                "stale_seconds": stale_after,
                "stale_cutoff": iso_str_nonempty(cutoff),
            },
            "pump_alive": _pump_alive(),
            "zombie_basis": (
                "status='running' 且 heartbeat_at < stale_cutoff（与 watchdog_tick "
                "同一谓词）；without_heartbeat 是 NULL 心跳的 running 行，"
                "watchdog 不会命中它们"
            ),
        }
    )


# --------------------------------------------------------------------------- #
# 写工具 1：backup_run
# --------------------------------------------------------------------------- #


async def _backup_snapshot() -> dict:
    try:
        return await adb_backup.backup_snapshot()
    except Exception as exc:  # noqa: BLE001 - 探测失败要可读，不要 500
        raise AgentError(
            "AI_AGENT_TOOL_FAILED",
            f"backup status unavailable: {type(exc).__name__}",
            503,
        ) from None


def _backup_busy(snapshot: dict) -> bool:
    return snapshot.get("state") == adb_backup.STATE_RUNNING


async def _prepare_backup_run(user: dict) -> Any:
    snapshot = await _backup_snapshot()
    if _backup_busy(snapshot):
        # 已在跑时连提案都不该建：这次触发注定被 systemd 侧拒掉（BackupBusy）。
        raise AgentError(
            "AI_AGENT_TOOL_FAILED", adb_backup.BUSY_MESSAGE, 409
        )
    return _draft(
        f"触发一次 ADB 备份（当前 {snapshot.get('state')}）",
        {
            "kind": "backup_run",
            "from_state": snapshot.get("state"),
            "last_started_at": snapshot.get("started_at"),
            "last_finished_at": snapshot.get("finished_at"),
            "last_message": snapshot.get("message"),
            "action": "systemctl start --no-block adb-sync.service（sudoers 白名单）",
            "effect": (
                "异步触发一次 prod→adb2 全量备份（约 15 分钟），不等待完成；"
                "已在运行时会被拒绝，不会排队"
            ),
        },
    )


async def _impl_backup_run(user: dict) -> dict:
    try:
        snapshot = await adb_backup.trigger_backup()
    except adb_backup.BackupBusy as exc:
        # 并发窗口：prepare 时还空闲，确认时已经在跑。这是「明确的工具失败」，
        # 不是 500 —— 模型读到 409 会去查进度，而不是把整轮 run 崩掉。
        raise AgentError(
            "AI_AGENT_TOOL_FAILED", str(exc) or adb_backup.BUSY_MESSAGE, 409
        ) from None
    except adb_backup.BackupTriggerError as exc:
        raise AgentError("AI_AGENT_TOOL_FAILED", str(exc), 503) from None
    except Exception as exc:  # noqa: BLE001 - 未预期异常也只回类型名（不带 DSN）
        raise AgentError(
            "AI_AGENT_TOOL_FAILED",
            f"backup trigger failed: {type(exc).__name__}",
            503,
        ) from None

    await _audit(
        user,
        "agent_backup_run",
        f"state={snapshot.get('state')} started_at={snapshot.get('started_at')}",
    )
    return _cap(
        {
            "kind": "backup_run",
            "accepted": True,
            # ``trigger_backup`` 的返回值就是提交后立刻采到的快照（--no-block）。
            **snapshot,
            "note": "已提交，备份在服务器上异步执行；用 backup_status 轮询进度",
        }
    )


# --------------------------------------------------------------------------- #
# 写工具 2：maintenance_mode
# --------------------------------------------------------------------------- #


def _maintenance_args(
    action: Any, reason: str | None, announcement: str | None
) -> tuple[str, str | None, str | None]:
    """参数校验：开必须带原因，关一律清空（reason/announcement 都不留）。

    校验在 ``prepare`` 与 ``impl`` 各跑一次：中间隔着一个动态码的输入时间，
    状态可能已经变了，参数却不会——所以这里只做纯函数校验（不查库）。
    """
    target = str(action or "").strip().lower()
    if target not in MAINTENANCE_ACTIONS:
        raise AgentError(
            "AI_AGENT_BAD_ARGUMENT",
            f"action must be one of: {', '.join(MAINTENANCE_ACTIONS)}",
            400,
        )
    if target == "disable":
        # 关闭即清空：维护态里的 reason 是「为什么现在在维护」，关掉之后它只会
        # 误导后来的人（历史原因在 maintenance_events 里）。
        return target, None, None

    text = str(reason or "").strip()
    if not text:
        raise AgentError(
            "AI_AGENT_BAD_ARGUMENT", "开启维护模式必须给出 reason", 400
        )
    if len(text) > MAINTENANCE_REASON_MAX:
        raise AgentError(
            "AI_AGENT_BAD_ARGUMENT",
            f"reason must be at most {MAINTENANCE_REASON_MAX} chars",
            400,
        )
    note = str(announcement or "").strip() or None
    if note is not None and len(note) > MAINTENANCE_ANNOUNCEMENT_MAX:
        raise AgentError(
            "AI_AGENT_BAD_ARGUMENT",
            f"announcement must be at most {MAINTENANCE_ANNOUNCEMENT_MAX} chars",
            400,
        )
    return target, text, note


def _maintenance_view(state: dict) -> dict:
    return {
        "enabled": bool(state.get("enabled")),
        "since": _epoch_iso(state.get("since")),
        "reason": state.get("reason"),
        "announcement": state.get("announcement"),
        "updated_by": state.get("updated_by"),
    }


async def _prepare_maintenance_mode(
    user: dict,
    action: str,
    reason: str | None = None,
    announcement: str | None = None,
) -> Any:
    target, text, note = _maintenance_args(action, reason, announcement)
    current = _maintenance_view(await asyncio.to_thread(system_state.get_state))
    if target == "enable":
        summary = f"开启系统维护模式：{text}"
    else:
        summary = "关闭系统维护模式（清空原因与公告）"
    return _draft(
        summary,
        {
            "kind": "maintenance_mode",
            "action": target,
            "from": current,
            "to": {
                "enabled": target == "enable",
                "reason": text,
                "announcement": note,
            },
            "effect": (
                "写本地 SQLite 维护态（maintenance_state + maintenance_events）；"
                "开启时全站请求 503 并断开已建立的实时连接，管理员入口仍可开关"
            ),
        },
    )


async def _impl_maintenance_mode(
    user: dict,
    action: str,
    reason: str | None = None,
    announcement: str | None = None,
) -> dict:
    target, text, note = _maintenance_args(action, reason, announcement)
    actor = str(user.get("id") or "")
    enabled = target == "enable"

    try:
        state = await asyncio.to_thread(
            system_state.set_state, enabled, text, note, actor
        )
    except Exception as exc:  # noqa: BLE001 - 本地状态库失败翻成 503，不要 500
        # 维护态没变，如实报错（不要谎报「已开启」）。
        raise AgentError(
            "AI_AGENT_TOOL_FAILED",
            f"维护态写入失败：{type(exc).__name__}",
            503,
        ) from None

    if enabled:
        # 已建立的连接不会自己掉线；断开失败不影响已经落库的状态。
        try:
            from realtime import disconnect_all_clients

            await disconnect_all_clients()
        except Exception:  # noqa: BLE001 - 断连是旁路
            logger.warning("agent maintenance_mode disconnect failed", exc_info=True)

    await _audit(
        user,
        "agent_maintenance_enable" if enabled else "agent_maintenance_disable",
        f"enabled={enabled} reason={text or ''}",
        # 与 ``routers/system_router`` 同一 log_type：维护模式的开/关在日志页里
        # 应该连成一条链，而不是分在两个类型下各查一遍。
        log_type=(
            "system_maintenance_enable" if enabled else "system_maintenance_disable"
        ),
    )
    return _cap(
        {
            "kind": "maintenance_mode",
            "action": target,
            "state": _maintenance_view(state),
        }
    )


# --------------------------------------------------------------------------- #
# 写工具 3：scheduler_control
# --------------------------------------------------------------------------- #


def _action_text(action: str) -> str:
    return {"pause": "暂停", "resume": "恢复", "run_now": "立即触发"}[action]


def _check_action(action: Any) -> str:
    target = str(action or "").strip().lower()
    if target not in SCHEDULER_ACTIONS:
        raise AgentError(
            "AI_AGENT_BAD_ARGUMENT",
            f"action must be one of: {', '.join(SCHEDULER_ACTIONS)}",
            400,
        )
    return target


def _load_job(job_id: Any) -> tuple[Any, Any]:
    """job 名 → (scheduler, job)。未知 job 一律 404（不做模糊匹配）。"""
    target = str(job_id or "").strip()
    if not target:
        raise AgentError("AI_AGENT_BAD_ARGUMENT", "job_id must not be empty", 400)
    try:
        scheduler = _scheduler()
        job = scheduler.get_job(target)
    except Exception as exc:  # noqa: BLE001 - 调度器不可用是可读的 503
        raise _scheduler_failure(exc, "scheduler") from None
    if job is None:
        raise AgentError(
            "AI_AGENT_NOT_FOUND", f"unknown scheduler job: {target}", 404
        )
    return scheduler, job


async def _prepare_scheduler_control(user: dict, job_id: str, action: str) -> Any:
    target = _check_action(action)
    scheduler, job = _load_job(job_id)
    summary = _job_summary(job)
    return _draft(
        f"{_action_text(target)}调度器 job「{summary.get('id')}」",
        {
            "kind": "scheduler_control",
            "job_id": summary.get("id"),
            "job_name": summary.get("name"),
            "action": target,
            "trigger": summary.get("trigger"),
            "from_next_run_time": summary.get("next_run_time"),
            "scheduler_running": bool(getattr(scheduler, "running", False)),
            "effect": {
                "pause": "暂停自动触发（手动 run_now 仍可用）",
                "resume": "恢复触发器排程",
                "run_now": "把下次触发时间改成现在（不改变原有 cron/interval）",
            }[target],
        },
    )


async def _impl_scheduler_control(user: dict, job_id: str, action: str) -> dict:
    target = _check_action(action)
    scheduler, job = _load_job(job_id)
    summary = _job_summary(job)
    job_key = str(summary.get("id") or "")

    try:
        if target == "pause":
            scheduler.pause_job(job_key)
        elif target == "resume":
            scheduler.resume_job(job_key)
        else:
            tz = getattr(scheduler, "timezone", None)
            # run-now 只挪 next_run_time，不动触发器：一次手动补跑不该把
            # cron 改成「每天这个点」。
            scheduler.modify_job(
                job_key, next_run_time=datetime.now(tz) if tz else datetime.now()
            )
    except Exception as exc:  # noqa: BLE001 - 未预期异常翻成工具失败
        raise AgentError(
            "AI_AGENT_TOOL_FAILED",
            f"scheduler {target} failed: {type(exc).__name__}",
            503,
        ) from None

    after = _job_summary(scheduler.get_job(job_key) or job)
    await _audit(
        user,
        f"agent_scheduler_{target}",
        f"job={job_key} next_run_time={after.get('next_run_time')}",
    )
    return _cap(
        {
            "kind": "scheduler_control",
            "job_id": job_key,
            "job_name": summary.get("name"),
            "action": target,
            "before_next_run_time": summary.get("next_run_time"),
            "after_next_run_time": after.get("next_run_time"),
            "enabled": bool(after.get("next_run_time")) and not after.get("pending"),
        }
    )


# --------------------------------------------------------------------------- #
# 读工具 5：user_lookup
# --------------------------------------------------------------------------- #

# 一次最多回 20 个目标：封号决策要的是「确认是谁」，不是导全班通讯录。
LOOKUP_LIMIT = 20
LOOKUP_NAME_MAX = 32
LOOKUP_FIELDS = (
    "oid",
    "name",
    "business_id",
    "class_id",
    "class_name",
    "roles",
    "banned",
    "ban_until",
    "device_bound",
)

_OID_RE = re.compile(r"^[0-9a-fA-F]{24}$")


def _lookup_mode(
    name: Any, business_id: Any, oid: Any
) -> tuple[str, str]:
    """三选一：恰好给一个查询键，回 ``(mode, value)``。

    多给不是「更精确」，而是**口径不明**：``name`` 模糊命中 20 个、``business_id``
    精确命中 1 个时该以谁为准？让模型二选一，比替它猜一个更安全。
    """
    raw = {"name": name, "business_id": business_id, "oid": oid}
    given = [
        key for key, value in raw.items() if isinstance(value, str) and value.strip()
    ]
    if not given:
        raise AgentError(
            "AI_AGENT_BAD_ARGUMENT",
            "provide exactly one of: name / business_id / oid",
            400,
        )
    if len(given) > 1:
        raise AgentError(
            "AI_AGENT_BAD_ARGUMENT",
            f"provide only one of name / business_id / oid (got {', '.join(given)})",
            400,
        )
    mode = given[0]
    value = str(raw[mode]).strip()
    if mode == "name" and len(value) > LOOKUP_NAME_MAX:
        raise AgentError(
            "AI_AGENT_BAD_ARGUMENT",
            f"name must be at most {LOOKUP_NAME_MAX} characters",
            400,
        )
    if mode == "oid" and not _OID_RE.match(value):
        raise AgentError(
            "AI_AGENT_BAD_ARGUMENT", "oid must be a 24-char hex id", 400
        )
    return mode, value


async def _class_names(class_ids: list[str]) -> dict[str, str]:
    """``{class_oid: name}``；单个班级查不到只影响 ``class_name``，不失败。

    ``GROUPS`` 只有班级行，所以优先一次 ``list_all`` 建映射，而不是对每个
    ``class_id`` 单查（N+1）。仓储替身只有单查接口，按能力探测回落旧路径。
    """
    unique = list(dict.fromkeys(str(cid) for cid in class_ids if cid))
    if not unique:
        return {}
    repo = backends.get_groups_repo()
    list_all = getattr(repo, "list_all", None)
    names: dict[str, str] = {}
    if list_all is not None:
        try:
            by_oid = {str(group.get("_id")): group for group in await list_all()}
        except Exception:  # noqa: BLE001 - 班级名是加分项，不是查询本身
            logger.warning("agent user_lookup class lookup failed", exc_info=True)
            return {}
        for class_id in unique:
            group = by_oid.get(class_id)
            if isinstance(group, dict) and group.get("name"):
                names[class_id] = str(group["name"])
        return names
    for class_id in unique:
        try:
            group = await repo.get(class_id)
        except Exception:  # noqa: BLE001 - 班级名是加分项，不是查询本身
            logger.warning("agent user_lookup class lookup failed", exc_info=True)
            continue
        if isinstance(group, dict) and group.get("name"):
            names[class_id] = str(group["name"])
    return names


def _lookup_view(row: Any, class_names: dict[str, str]) -> dict:
    """**白名单投影**：只挑这 9 个键，仓储回什么都进不来。

    ``password`` / ``session_id`` / ``eligibility`` / ``past`` 等一律不读；
    主设备只回「有没有绑」的布尔，原值（设备指纹）不外带。
    """
    class_id = row.get("class_id")
    class_id = str(class_id) if class_id else None
    ban_until = row.get("ban_until")
    return {
        "oid": str(row.get("_id") or ""),
        "name": row.get("name"),
        "business_id": row.get("id"),
        "class_id": class_id,
        "class_name": class_names.get(class_id) if class_id else None,
        "roles": [str(role) for role in (row.get("roles") or [])],
        "banned": bool(row.get("banned")),
        # 永久封禁在仓储里是「没有 ban_until 这个键」；这里显式给 null，
        # 让模型看到 ``banned=true, ban_until=null`` 这一对。
        "ban_until": iso_str_nonempty(ban_until),
        "device_bound": "primary_xuehai_id" in row,
    }


async def _impl_user_lookup(
    user: dict,
    name: str | None = None,
    business_id: str | None = None,
    oid: str | None = None,
) -> dict:
    mode, value = _lookup_mode(name, business_id, oid)
    repo = backends.get_users_repo()

    if mode == "oid":
        row = await repo.get_by_oid(value)
        rows = [row] if row else []
    elif mode == "business_id":
        row = await repo.find_by_business_id(value)
        rows = [row] if row else []
    else:
        # 仓储的模糊查同时匹配 name/id；这里再按姓名收一次，免得用纯数字
        # 姓名查到一串学号。服务端已经筛过一遍，多这一层不增加往返。
        pattern = re.escape(value)
        needle = value.lower()
        rows = [
            row
            for row in await repo.list_by_name_or_id(pattern)
            if needle in str(row.get("name") or "").lower()
        ]

    truncated = len(rows) > LOOKUP_LIMIT
    picked = rows[:LOOKUP_LIMIT]
    class_names = await _class_names(
        [str(row["class_id"]) for row in picked if row.get("class_id")]
    )
    users = [_lookup_view(row, class_names) for row in picked]

    return _cap(
        {
            "kind": "user_lookup",
            "query": {mode: value},
            "count": len(users),
            "truncated": truncated,
            "users": users,
            "fields": list(LOOKUP_FIELDS),
            "note": (
                "白名单投影：不含密码/会话/totp/设备指纹原值。banned 是登录闸的"
                "原始值（到期未解封仍为 true），ban_until=null 且 banned=true 表示"
                "永久封禁；时长判断请用 server_time 的时间基准。"
            ),
        }
    )


# --------------------------------------------------------------------------- #
# 读工具 6：ban_history
# --------------------------------------------------------------------------- #

# 封禁流水一次最多回 20 条：档位判断看的是「最近几次 + 按规则汇总」，不是全量。
BAN_HISTORY_LIMIT_MAX = 20
BAN_HISTORY_LIMIT_DEFAULT = 10
# 违规信号只看最近 30 天：与规则里的窗口档位同量级，更早的信号已不该再抬档。
VIOLATION_WINDOW_DAYS = 30

BAN_HISTORY_FIELDS = (
    "kind",
    "user",
    "records",
    "tiers",
    "violations",
    "rules",
    "device",
    "note",
)
USER_FIELDS = ("oid", "name", "business_id", "banned", "ban_until")
RECORD_FIELDS = (
    "oid",
    "source",
    "rule_code",
    "rule_name",
    "reason",
    "permanent",
    "started_at",
    "expires_at",
    "operator_oid",
    "operator_name",
    "created_at",
)
TIER_FIELDS = ("rule_code", "count", "last_at")
VIOLATION_FIELDS = ("trigger_code", "batches", "last_at")
RULE_FIELDS = ("code", "name", "trigger_code", "penalties", "enabled")
DEVICE_FIELDS = ("xuehai_id_masked", "ban_count", "last_banned_at")

BAN_HISTORY_NOTE = (
    "tiers 是「该用户在该 rule_code 下已被封过的次数」：在 rules.penalties "
    "（JSON 数组，原样透出）里取第 count+1 个元素即本次应执行的档位；"
    "超出数组长度时按最后一档处理。violations 的计数单位是批次"
    "（同一 batch 的多行信号只算一次），用于确认 30 天内违规是否仍在累积。"
)


def _history_args(
    user_oid: Any, business_id: Any, limit: Any
) -> tuple[str, str, int]:
    """``user_oid`` / ``business_id`` 恰好一个；``limit`` 收敛到 1..20。"""
    raw = {"user_oid": user_oid, "business_id": business_id}
    given = [
        key for key, value in raw.items() if isinstance(value, str) and value.strip()
    ]
    if not given:
        raise AgentError(
            "AI_AGENT_BAD_ARGUMENT",
            "provide exactly one of: user_oid / business_id",
            400,
        )
    if len(given) > 1:
        raise AgentError(
            "AI_AGENT_BAD_ARGUMENT",
            f"provide only one of user_oid / business_id (got {', '.join(given)})",
            400,
        )
    mode = given[0]
    value = str(raw[mode]).strip()
    if mode == "user_oid" and not _OID_RE.match(value):
        raise AgentError(
            "AI_AGENT_BAD_ARGUMENT", "user_oid must be a 24-char hex id", 400
        )
    size = _int(limit, BAN_HISTORY_LIMIT_DEFAULT)
    if size < 1 or size > BAN_HISTORY_LIMIT_MAX:
        raise AgentError(
            "AI_AGENT_BAD_ARGUMENT",
            f"limit must be between 1 and {BAN_HISTORY_LIMIT_MAX}",
            400,
        )
    return mode, value, size


def _mask_xuehai(value: Any) -> str | None:
    """学海 ID 打码到「前 3 + 后 2」，中间固定为 ``*``；太短就全遮。

    打码后的长度不携带原值长度以外的信息，且短 ID 不做「前 3 后 2」——
    那等于把 5 位以内的原值整段交出去。
    """
    text = str(value or "").strip()
    if not text:
        return None
    if len(text) <= 5:
        return "*" * max(3, len(text))
    return f"{text[:3]}{'*' * (len(text) - 5)}{text[-2:]}"


def _record_item(row: Any, rule_names: dict[str, str]) -> dict:
    """白名单投影：只挑 ``RECORD_FIELDS``，行里别的东西进不来。"""
    mapping = row._mapping
    rule_code = mapping.get("rule_code")
    return {
        "oid": str(mapping.get("oid") or ""),
        "source": mapping.get("source"),
        "rule_code": rule_code,
        "rule_name": mapping.get("rule_name") or rule_names.get(rule_code),
        "reason": mapping.get("reason"),
        "permanent": bool(mapping.get("permanent")),
        "started_at": iso_str_nonempty(mapping.get("started_at")),
        "expires_at": iso_str_nonempty(mapping.get("expires_at")),
        "operator_oid": mapping.get("operator_oid"),
        "operator_name": mapping.get("operator_name"),
        "created_at": iso_str_nonempty(mapping.get("created_at")),
    }


def _records_stmt(user_oid: str, size: int):
    """最近 ``size`` 条封禁流水，``started_at`` 倒序（同一时刻按 oid 稳定）。

    ``rule_name`` 用 ``BAN_RULES.code`` 左连（规则被删后仍显示流水，名字退回
    null）；``operator_name`` 用 ``USERS`` 别名 ``op`` 左连——规则封禁没有
    ``operator_oid``，不能因此丢掉整行。
    """
    operator = users_table.alias("op")
    return (
        select(
            ban_records_table.c.oid,
            ban_records_table.c.source,
            ban_records_table.c.rule_code,
            ban_records_table.c.reason,
            ban_records_table.c.permanent,
            ban_records_table.c.started_at,
            ban_records_table.c.expires_at,
            ban_records_table.c.operator_oid,
            ban_records_table.c.created_at,
            ban_rules_table.c.name.label("rule_name"),
            operator.c.name.label("operator_name"),
        )
        .select_from(
            ban_records_table.outerjoin(
                ban_rules_table, ban_records_table.c.rule_code == ban_rules_table.c.code
            ).outerjoin(
                operator, ban_records_table.c.operator_oid == operator.c.oid
            )
        )
        .where(ban_records_table.c.user_oid == user_oid)
        .order_by(
            ban_records_table.c.started_at.desc(), ban_records_table.c.oid.desc()
        )
        .limit(size)
    )


def _tiers_stmt(user_oid: str):
    """按 ``rule_code`` 汇总历史封禁次数与最近一次时间（累犯档位的直接依据）。

    管理员手动封禁的 ``rule_code IS NULL``，不参与梯度计数，直接过滤掉。
    """
    last_at = func.max(ban_records_table.c.started_at)
    return (
        select(
            ban_records_table.c.rule_code,
            func.count().label("ban_count"),
            last_at.label("last_at"),
        )
        .where(
            ban_records_table.c.user_oid == user_oid,
            ban_records_table.c.rule_code.is_not(None),
        )
        .group_by(ban_records_table.c.rule_code)
        .order_by(func.count().desc(), last_at.desc())
    )


def _violations_stmt(user_oid: str, since: datetime):
    """30 天内按 trigger 聚合的**批次**数：``COALESCE(batch_id, oid)`` 去重。

    与 ``violations.count_batches_since`` 同一口径：一次上报的多个信号只算
    一次违规；V019 之前的存量行没有批次，用主键顶替（每行一批）。
    """
    batch = func.coalesce(ban_violations_table.c.batch_id, ban_violations_table.c.oid)
    return (
        select(
            ban_violations_table.c.trigger_code,
            func.count(func.distinct(batch)).label("batches"),
            func.max(ban_violations_table.c.created_at).label("last_at"),
        )
        .where(
            ban_violations_table.c.user_oid == user_oid,
            ban_violations_table.c.created_at >= since,
        )
        .group_by(ban_violations_table.c.trigger_code)
        .order_by(func.max(ban_violations_table.c.created_at).desc())
    )


async def _device_history(user_oid: str) -> dict | None:
    """绑定设备的处罚梯度；没有绑定或查不到计数一律 ``None``（不是 0）。

    只回打码后的学海 ID：调用方要判断的是「这台机器被封过几次」，原值对
    推断档位没有增量信息，却会扩大泄露面。
    """
    try:
        bindings = await backends.get_bindings_repo().get_active_for_user(user_oid)
    except Exception:  # noqa: BLE001 - 设备是加分项，不是查询本身
        logger.warning("agent ban_history binding lookup failed", exc_info=True)
        return None
    xuehai_id = next(
        (str(row.get("xuehai_id")) for row in bindings if row.get("xuehai_id")), ""
    )
    if not xuehai_id:
        return None
    counter = await backends.get_device_ban_counters_repo().get(xuehai_id)
    return {
        "xuehai_id_masked": _mask_xuehai(xuehai_id),
        "ban_count": _int(counter.get("ban_count")) if counter else 0,
        "last_banned_at": iso_str_nonempty(counter.get("last_banned_at")) if counter else None,
    }


async def _impl_ban_history(
    user: dict,
    user_oid: str | None = None,
    business_id: str | None = None,
    limit: int = BAN_HISTORY_LIMIT_DEFAULT,
) -> dict:
    mode, value, size = _history_args(user_oid, business_id, limit)
    repo = backends.get_users_repo()
    if mode == "user_oid":
        row = await repo.get_by_oid(value)
    else:
        row = await repo.find_by_business_id(value)
    if not row:
        raise AgentError("AI_AGENT_NOT_FOUND", "user not found", 404)

    target_oid = str(row.get("_id") or "")
    if not target_oid:
        raise AgentError("AI_AGENT_NOT_FOUND", "user not found", 404)

    all_rules = await backends.get_ban_rules_repo().list_all()
    rule_names = {
        str(item.get("code")): str(item.get("name"))
        for item in all_rules
        if item.get("code") and item.get("name")
    }
    rules = [
        {
            "code": item.get("code"),
            "name": item.get("name"),
            "trigger_code": item.get("trigger"),
            "penalties": item.get("penalties"),
            "enabled": bool(item.get("enabled")),
        }
        for item in all_rules
        if item.get("enabled")
    ]

    since = datetime.now() - timedelta(days=VIOLATION_WINDOW_DAYS)
    async with session_scope() as session:
        record_rows = (await session.execute(_records_stmt(target_oid, size))).all()
        tier_rows = (await session.execute(_tiers_stmt(target_oid))).all()
        violation_rows = (
            await session.execute(_violations_stmt(target_oid, since))
        ).all()

    records = [_record_item(item, rule_names) for item in record_rows]
    tiers = [
        {
            "rule_code": item._mapping.get("rule_code"),
            "count": _int(item._mapping.get("ban_count")),
            "last_at": iso_str_nonempty(item._mapping.get("last_at")),
        }
        for item in tier_rows
    ]
    violations = [
        {
            "trigger_code": item._mapping.get("trigger_code"),
            "batches": _int(item._mapping.get("batches")),
            "last_at": iso_str_nonempty(item._mapping.get("last_at")),
        }
        for item in violation_rows
    ]

    return _cap(
        {
            "kind": "ban_history",
            "user": {
                "oid": target_oid,
                "name": row.get("name"),
                "business_id": row.get("id"),
                "banned": bool(row.get("banned")),
                "ban_until": iso_str_nonempty(row.get("ban_until")),
            },
            "records": records,
            "tiers": tiers,
            "violations": violations,
            "rules": rules,
            "device": await _device_history(target_oid),
            "note": BAN_HISTORY_NOTE,
        }
    )


# --------------------------------------------------------------------------- #
# 读工具 7：server_time
# --------------------------------------------------------------------------- #

_WEEKDAYS = (
    "星期一",
    "星期二",
    "星期三",
    "星期四",
    "星期五",
    "星期六",
    "星期日",
)


def _zone_name(aware: datetime) -> str:
    """进程当前时区名（``Asia/Shanghai`` 这种），拿不到就退 ``tzname()``。

    顺序：``TZ`` 环境变量 → ``/etc/timezone`` → ``/etc/localtime`` 软链 →
    ``tzname()``。前三个给的是 IANA 名，告诉模型「这是东八区」比 ``CST`` 这种
    可能被误读成北美中部的缩写可靠。
    """
    env = os.environ.get("TZ", "").strip().lstrip(":")
    if env:
        return env
    try:
        text = Path("/etc/timezone").read_text(encoding="utf-8").strip()
    except OSError:
        text = ""
    if text:
        return text
    try:
        target = str(Path("/etc/localtime").resolve())
    except OSError:
        target = ""
    if "zoneinfo/" in target:
        return target.split("zoneinfo/", 1)[1]
    return aware.tzname() or "UTC"


async def _impl_server_time(user: dict) -> dict:
    """权威时间基准。**naive 侧走 ``device_guard._to_local_naive`` 同一口径**：
    封禁落库的 ``USERS.BAN_UNTIL`` 是本地墙钟 naive，直接喂 aware UTC 会差出一个
    时区偏移（东八区「封 24 小时」实际只封 16 小时）。
    """
    from services.device_guard import _to_local_naive

    aware = datetime.now().astimezone()
    naive = _to_local_naive(aware) or datetime.now()
    return _cap(
        {
            "kind": "server_time",
            "now_iso": aware.isoformat(timespec="seconds"),
            "timezone": _zone_name(aware),
            "epoch_seconds": int(aware.timestamp()),
            "weekday": _WEEKDAYS[naive.weekday()],
            # 与封禁落库同口径的墙钟值：给「封到几点」时直接用它加时长。
            "local_naive": naive.isoformat(sep=" ", timespec="seconds"),
            "note": (
                "local_naive 是本地墙钟 naive（与 BAN_UNTIL 落库口径一致）；"
                "now_iso 带时区偏移。两者指向同一时刻。"
            ),
        }
    )


# --------------------------------------------------------------------------- #
# 注册（7 读 + 3 写）
# --------------------------------------------------------------------------- #

_STR = {"type": "string"}
_INT = {"type": "integer"}


_register(
    ToolDef(
        name="settings_snapshot",
        description=(
            "读取运行期设置快照（AI 角色/配额/开关、运维上限）与上游 provider 的脱敏视图；"
            "不回任何密钥原值。只读。"
        ),
        kind=READ,
        params=_params(
            {
                "group": {
                    **_STR,
                    "enum": list(SETTINGS_GROUPS),
                    "description": "设置分组（ai/ops/all，默认 all）",
                }
            },
            (),
        ),
        permission=_admin_only,
        impl=_impl_settings_snapshot,
        guard=ops_auth.ensure_system_admin,
    )
)


_register(
    ToolDef(
        name="scheduler_jobs",
        description=(
            "列出后台调度器的全部 job（下次触发时间、触发器、是否启用、上次结果）；"
            "与 scheduler_status 同源，只读。"
        ),
        kind=READ,
        params=_params({}, ()),
        permission=_admin_only,
        impl=_impl_scheduler_jobs,
        guard=ops_auth.ensure_system_admin,
    )
)


_register(
    ToolDef(
        name="ai_call_logs",
        description=(
            "按时间窗（≤7 天）/能力/状态检索 AI 调用流水的元数据（tokens、units、"
            "key_scope、错误码），不含任何正文。只读。"
        ),
        kind=READ,
        params=_params(
            {
                "days": {
                    **_INT,
                    "minimum": 1,
                    "maximum": LOG_WINDOW_MAX_DAYS,
                    "description": (
                        f"时间窗天数（1-{LOG_WINDOW_MAX_DAYS}，默认 "
                        f"{LOG_WINDOW_DEFAULT_DAYS}）"
                    ),
                },
                "capability": {
                    **_STR,
                    "enum": list(AI_CAPABILITIES),
                    "description": "能力（chat/ocr），省略=全部",
                },
                "status": {
                    **_STR,
                    "enum": list(CALL_LOG_STATUSES),
                    "description": "调用状态（success/error/timeout/rejected/busy/warning）",
                },
                "user_oid": {**_STR, "description": "可选：按用户 oid 过滤"},
                "limit": {
                    **_INT,
                    "minimum": 1,
                    "maximum": LOG_ROWS_MAX,
                    "description": f"最多返回多少条（1-{LOG_ROWS_MAX}，默认 {LOG_ROWS_DEFAULT}）",
                },
            },
            (),
        ),
        permission=_admin_only,
        impl=_impl_ai_call_logs,
        guard=ops_auth.ensure_system_admin,
    )
)


_register(
    ToolDef(
        name="agent_queue_status",
        description=(
            "查看 ZVAgent run 队列快照（排队数、运行中、最久等待、僵尸候选）与并发/心跳阈值。只读。"
        ),
        kind=READ,
        params=_params({}, ()),
        permission=_admin_only,
        impl=_impl_agent_queue_status,
        guard=ops_auth.ensure_system_admin,
    )
)


_register(
    ToolDef(
        name="backup_run",
        description=(
            "触发一次 ADB 全量备份（异步提交，已在运行时拒绝）；不等待完成。"
            "写操作：先生成提案，另一位管理员确认后执行。"
        ),
        kind=WRITE,
        params=_params({}, ()),
        permission=_admin_only,
        impl=_impl_backup_run,
        prepare=_prepare_backup_run,
        guard=ops_auth.ensure_system_admin,
        dual_auth_action=WRITE_AUTH_ACTION,
    )
)


_register(
    ToolDef(
        name="maintenance_mode",
        description=(
            "开启/关闭系统维护模式（开启必须给原因，关闭清空原因与公告）。"
            "写操作：先生成提案，另一位管理员确认后执行。"
        ),
        kind=WRITE,
        params=_params(
            {
                "action": {
                    **_STR,
                    "enum": list(MAINTENANCE_ACTIONS),
                    "description": "enable=开启 / disable=关闭",
                },
                "reason": {
                    **_STR,
                    "maxLength": MAINTENANCE_REASON_MAX,
                    "description": (
                        f"开启原因（≤{MAINTENANCE_REASON_MAX} 字，开启必填；"
                        "关闭时忽略并清空）"
                    ),
                },
                "announcement": {
                    **_STR,
                    "maxLength": MAINTENANCE_ANNOUNCEMENT_MAX,
                    "description": (
                        f"可选：维护公告（≤{MAINTENANCE_ANNOUNCEMENT_MAX} 字）"
                    ),
                },
            },
            ("action",),
        ),
        permission=_admin_only,
        impl=_impl_maintenance_mode,
        prepare=_prepare_maintenance_mode,
        guard=ops_auth.ensure_system_admin,
        dual_auth_action=WRITE_AUTH_ACTION,
    )
)


_register(
    ToolDef(
        name="scheduler_control",
        description=(
            "暂停/恢复/立即触发单个调度器 job（job 名必须已注册，只允许这三个动作）。"
            "写操作：先生成提案，另一位管理员确认后执行。"
        ),
        kind=WRITE,
        params=_params(
            {
                "job_id": {**_STR, "description": "调度器 job id（如 daily_retention_purge）"},
                "action": {
                    **_STR,
                    "enum": list(SCHEDULER_ACTIONS),
                    "description": "pause=暂停 / resume=恢复 / run_now=立即触发一次",
                },
            },
            ("job_id", "action"),
        ),
        permission=_admin_only,
        impl=_impl_scheduler_control,
        prepare=_prepare_scheduler_control,
        guard=ops_auth.ensure_system_admin,
        dual_auth_action=WRITE_AUTH_ACTION,
    )
)


_register(
    ToolDef(
        name="user_lookup",
        description=(
            "按姓名（模糊）/学号（精确）/oid（精确）查用户，用于确认封号目标；"
            "只回白名单字段（oid/姓名/学号/班级/角色/是否封禁/到期/是否绑主设备），"
            "不含任何凭据。只读。"
        ),
        kind=READ,
        params=_params(
            {
                "name": {
                    **_STR,
                    "maxLength": LOOKUP_NAME_MAX,
                    "description": (
                        f"姓名（部分匹配，≤{LOOKUP_NAME_MAX} 字）；与 business_id/oid 三选一"
                    ),
                },
                "business_id": {
                    **_STR,
                    "description": "学号（精确匹配）；与 name/oid 三选一",
                },
                "oid": {
                    **_STR,
                    "description": "用户 oid（24 位 hex，精确匹配）；与 name/business_id 三选一",
                },
            },
            (),
        ),
        permission=_admin_only,
        impl=_impl_user_lookup,
        guard=ops_auth.ensure_system_admin,
    )
)


_register(
    ToolDef(
        name="ban_history",
        description=(
            "封号前查历史：最近封禁流水（含规则名/操作人）、按 rule_code 的历史"
            "封禁次数与时间、30 天内按批次的违规信号、启用规则及其 penalties 梯度、"
            "绑定设备的封禁计数（学海 ID 打码）。用于推断本次该用第几档。只读。"
        ),
        kind=READ,
        params=_params(
            {
                "user_oid": {
                    **_STR,
                    "description": "用户 oid（24 位 hex）；与 business_id 二选一",
                },
                "business_id": {
                    **_STR,
                    "description": "学号（精确匹配）；与 user_oid 二选一",
                },
                "limit": {
                    **_INT,
                    "minimum": 1,
                    "maximum": BAN_HISTORY_LIMIT_MAX,
                    "description": (
                        f"最近封禁流水条数（1-{BAN_HISTORY_LIMIT_MAX}，默认 "
                        f"{BAN_HISTORY_LIMIT_DEFAULT}）"
                    ),
                },
            },
            (),
        ),
        permission=_admin_only,
        impl=_impl_ban_history,
        guard=ops_auth.ensure_system_admin,
    )
)


_register(
    ToolDef(
        name="server_time",
        description=(
            "读取服务器权威时间（带偏移的 ISO / 时区名 / epoch / 星期，以及封禁落库"
            "同口径的本地墙钟 naive），用于换算封禁时长与时区。只读。"
        ),
        kind=READ,
        params=_params({}, ()),
        permission=_admin_only,
        impl=_impl_server_time,
        guard=ops_auth.ensure_system_admin,
    )
)


__all__ = [
    "BAN_HISTORY_FIELDS",
    "BAN_HISTORY_LIMIT_DEFAULT",
    "BAN_HISTORY_LIMIT_MAX",
    "CALL_LOG_STATUSES",
    "LOG_ROWS_MAX",
    "LOG_WINDOW_MAX_DAYS",
    "LOOKUP_FIELDS",
    "LOOKUP_LIMIT",
    "LOOKUP_NAME_MAX",
    "MAINTENANCE_ACTIONS",
    "MAINTENANCE_REASON_MAX",
    "SCHEDULER_ACTIONS",
    "SETTINGS_GROUPS",
]
