"""平台运维工具：日志 / DB 诊断 / 备份 / 调度器 / 封禁解封。

与 ``ops_tools``（文件 / 数据库内容 / SSH，全部走提案 + TOTP）不同的一层：

* **读**（``platform_logs`` / ``db_diagnostics`` / ``backup_status`` /
  ``scheduler_status`` / ``device_ban_list``）全部只读、有界、不发任意 SQL。
  它们是「巡检 agent 的眼睛」——没有这些，模型只能靠猜或让管理员贴截图。
* **写**（``user_ban`` / ``user_unban`` / ``device_unban``）是 ``direct=True``：
  过完 ``permission`` + ``guard`` + 参数收敛就直接执行，不建提案、不要 TOTP。
  这是唯一被允许绕过人工确认的一类副作用，理由与护栏：

  1. 只有 :data:`services.agent.tools.DIRECT_WRITE_TOOL_NAMES` 里的三个名字能
     这样声明（注册期校验，import 就跑）；
  2. ``guard=ops_auth.ensure_system_admin`` 每次现查库：封禁/撤权/降级立刻生效，
     不看 token 里的 ``per`` 快照；
  3. 目标护栏写在实现里而不是 prompt 里：不能是操作者自己、重算权限含 ``admin``
     的一律 403（模型无法通过话术绕过），目标不存在 404；
  4. 每次执行都落 ``app_logs``（``ban_change`` / ``device_ban`` + ``warning``，
     属于受保留策略保护的证据行）与 ``ban_records`` 流水。

封禁类写操作**必须**是可逆的：``user_unban`` / ``device_unban`` 与封禁同级
（同为 direct），否则自动封禁就会变成「只能人工解」的单向操作。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from db import backends
from db.models import app_logs as app_logs_table
from db.oracle.session import session_scope
from services import adb_backup, db_monitor
from typings.log import ZVMSLog
from util.ban import ban_is_active, humanize_ban_reason
from util.group import get_user_permissions
from util.user import get_user_display_name

from . import ops_auth
from .ops_tools import _admin_only, _cap, _params
from .tool_impl import AgentError
from .tools import READ, WRITE, ToolDef, _register

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# 上限：所有查询都必须是「有界」的（巡检 agent 会反复调用，不能每次都全表扫）
# --------------------------------------------------------------------------- #

MAX_LOG_ROWS = 50
LOG_DEFAULT_WINDOW_DAYS = 7
LOG_MAX_WINDOW_DAYS = 90
LOG_TOP_LTYPES = 20
LOG_ROW_DATA_CHARS = 300
DIAG_TOP_N = 10
DEVICE_BAN_PAGE_MAX = 100
BAN_DURATION_MAX_DAYS = 3650
BAN_REASON_MAX_CHARS = 500

# 审计用的 log_type：与 ``routers/users_router.set_user_banned_v3`` 同值
# （``ban_change`` 在 ``db.repos.oracle.app_logs.PROTECTED_LTYPES`` 里，不会被
# 保留期清掉）。设备封禁沿用 ``device_ban``（同样受保护）。
AUDIT_TYPE_BAN = "ban_change"
AUDIT_TYPE_DEVICE = "device_ban"
AUDIT_LEVEL = "warning"


# --------------------------------------------------------------------------- #
# 通用小工具
# --------------------------------------------------------------------------- #


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _parse_iso(value: Any, name: str) -> float | None:
    """ISO-8601 → epoch 秒；空值返回 ``None``，格式不对报 400。

    naive 时间按本地时区解释——与 ``app_logs.ts_at`` 的派生方式（写入时
    ``datetime.fromtimestamp(ts_raw)``）同源，否则「我看到的 12:00」和
    「数据库里的 12:00」会差一个时区。
    """
    text = _text(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise AgentError(
            "AI_AGENT_BAD_ARGUMENT",
            f"{name} must be an ISO-8601 timestamp (e.g. 2026-01-31T12:00:00)",
            400,
        ) from None
    return parsed.timestamp()


def _window(time_start: Any, time_end: Any) -> tuple[float, float]:
    """日志查询的时间窗（epoch 秒），默认最近 7 天，最多 90 天。

    默认窗口是**承重**的：不传时间就等于全表倒序取 50 条，``COUNT(*) OVER()``
    会把整张 ``app_logs`` 数一遍。有了窗口，``ts_raw`` 上的索引才是可用形状。
    """
    end = _parse_iso(time_end, "time_end")
    start = _parse_iso(time_start, "time_start")
    if end is None:
        end = datetime.now().timestamp()
    if start is None:
        start = end - timedelta(days=LOG_DEFAULT_WINDOW_DAYS).total_seconds()
    if start >= end:
        raise AgentError(
            "AI_AGENT_BAD_ARGUMENT", "time_start must be earlier than time_end", 400
        )
    if end - start > LOG_MAX_WINDOW_DAYS * 86400:
        raise AgentError(
            "AI_AGENT_BAD_ARGUMENT",
            f"time window must not exceed {LOG_MAX_WINDOW_DAYS} days",
            400,
        )
    return float(start), float(end)


def _iso_moment(epoch: float) -> str:
    return datetime.fromtimestamp(epoch).isoformat()


async def _display_name(oid: str) -> str:
    """操作者显示名；查不到就用 oid（审计宁可难看，也不能因为改名失败丢一行）。"""
    try:
        name = await get_user_display_name(oid)
    except Exception:  # noqa: BLE001 - 展示信息不该阻断封禁
        return oid
    return name or oid


async def _audit(user: dict, text: str, *, log_type: str) -> None:
    """封禁/解封审计：``app_logs`` + ``warning``。

    审计是旁路：业务结论（封禁已落库）已经定了，写日志失败只该留一条进程内
    warning，而不是把一次成功的封禁变成 500（``util.dual_auth.audit_system_op``
    同一口径）。
    """
    try:
        await ZVMSLog(
            url=f"system:agent_{log_type}",
            user=str(user.get("id") or ""),
            data=text[:2000],
            type=log_type,
            level=AUDIT_LEVEL,
        ).insert_log()
    except Exception:  # noqa: BLE001 - 审计旁路
        logger.warning("agent %s audit write failed", log_type, exc_info=True)


# --------------------------------------------------------------------------- #
# 读工具 1：platform_logs
# --------------------------------------------------------------------------- #


async def _top_ltypes(
    *,
    start: float,
    end: float,
    user_oid: str = "",
    xuehai_id: str = "",
    levels: list[str] | None = None,
) -> list[dict]:
    """时间窗内按 ``ltype`` 的计数（Top N），一条聚合语句，不做任意 SQL。

    走 ``ix_app_logs_type_level_ts`` / ``ix_app_logs_user_ts`` 的前缀；结果行数由
    ``GROUP BY ltype`` 天然有界，再按计数倒序截 Top N。
    """
    stmt = select(
        app_logs_table.c.ltype, func.count().label("total")
    ).select_from(app_logs_table)
    conds = [
        app_logs_table.c.ts_raw >= start,
        app_logs_table.c.ts_raw <= end,
    ]
    if user_oid:
        conds.append(app_logs_table.c.app_user == user_oid)
    if xuehai_id:
        conds.append(app_logs_table.c.xuehai_id == xuehai_id)
    if levels:
        conds.append(app_logs_table.c.log_level.in_(list(levels)))
    stmt = (
        stmt.where(*conds)
        .group_by(app_logs_table.c.ltype)
        .order_by(func.count().desc())
        .limit(LOG_TOP_LTYPES)
    )
    async with session_scope() as session:
        rows = (await session.execute(stmt)).all()
    return [
        {"ltype": row._mapping["ltype"], "count": int(row._mapping["total"] or 0)}
        for row in rows
    ]


async def _impl_platform_logs(
    user: dict,
    keyword: str = "",
    ltype: str = "",
    log_level: str = "",
    user_oid: str = "",
    xuehai_id: str = "",
    time_start: str = "",
    time_end: str = "",
    limit: int = MAX_LOG_ROWS,
) -> dict:
    start, end = _window(time_start, time_end)
    limit = max(1, min(int(limit), MAX_LOG_ROWS))
    types = [ltype] if _text(ltype) else None
    levels = [log_level] if _text(log_level) else None
    performer = _text(user_oid)
    device = _text(xuehai_id)

    rows, total = await backends.get_applogs_repo().list_page(
        query=_text(keyword),
        performer=performer,
        types=types,
        levels=levels,
        time_start=start,
        time_end=end,
        xuehai_id=device,
        page=1,
        size=limit,
    )

    try:
        counts = await _top_ltypes(
            start=start, end=end, user_oid=performer, xuehai_id=device, levels=levels
        )
    except Exception:  # noqa: BLE001 - 聚合是补充信息，不该让日志正文一起丢
        logger.warning("agent platform_logs ltype counts failed", exc_info=True)
        counts = []

    items = [
        {
            "id": row.get("_id"),
            "ts": row.get("timestamp"),
            "ltype": row.get("type"),
            "level": row.get("level"),
            "url": row.get("url"),
            "user": row.get("user"),
            "user_name": row.get("user_name"),
            "xuehai_id": row.get("xuehai_id"),
            "data": str(row.get("data") or "")[:LOG_ROW_DATA_CHARS],
        }
        for row in rows
    ]
    return _cap(
        {
            "kind": "logs",
            "window": {"start": _iso_moment(start), "end": _iso_moment(end)},
            "filters": {
                "keyword": _text(keyword),
                "ltype": types[0] if types else None,
                "log_level": levels[0] if levels else None,
                "user_oid": performer or None,
                "xuehai_id": device or None,
            },
            "total": int(total),
            "limit": limit,
            "returned": len(items),
            "ltype_counts": counts,
            "items": items,
        }
    )


# --------------------------------------------------------------------------- #
# 读工具 2：db_diagnostics
# --------------------------------------------------------------------------- #


def _pool() -> dict:
    """本进程连接池压力（``db_monitor._pool_status``，与监控面板同一实现）。

    这是服务层内部的私有函数：对外只有 ``get_database_status`` 带出池信息，而
    那一条要顺带跑完整的 storage 采集（重得多）。这里只读池，故意直连。
    """
    try:
        return db_monitor._pool_status()
    except Exception:  # noqa: BLE001 - 池不可读时其余诊断仍然有用
        logger.warning("agent db_diagnostics pool status failed", exc_info=True)
        return {"available": False, "reason": "pool_unavailable"}


def _tablespaces(items: list[dict]) -> list[dict]:
    ordered = sorted(items, key=lambda item: item.get("used_percent") or 0, reverse=True)
    return ordered[:DIAG_TOP_N]


async def _impl_db_diagnostics(user: dict) -> dict:
    try:
        async with session_scope() as session:
            fast = await db_monitor.collect_fast(session)
            slow = await db_monitor.collect_slow(session)
            sessions = await db_monitor.collect_sessions(session)
    except Exception as exc:  # noqa: BLE001 - 采集中断要给模型一条可读原因
        raise AgentError(
            "AI_AGENT_TOOL_FAILED",
            f"database diagnostics unavailable: {type(exc).__name__}",
            503,
        ) from None

    return _cap(
        {
            "kind": "db_diagnostics",
            "observed_at": datetime.now().isoformat(),
            # 关键字段白名单：``collect_fast`` 还有累计计数器等原始值，对模型
            # 判断「现在慢不慢」没用，只会占满 8000 字符的预算。
            "load": {
                key: fast.get(key)
                for key in ("available", "sessions", "aas", "ash_seconds", "reason")
                if key in fast
            },
            "slow": {
                "available": bool(slow.get("available")),
                "top_sql": list(slow.get("top_sql") or [])[:DIAG_TOP_N],
                "waits": list(slow.get("waits") or [])[:DIAG_TOP_N],
                "tablespaces": _tablespaces(list(slow.get("tablespaces") or [])),
                "errors": slow.get("errors") or {},
            },
            "sessions": {
                key: sessions.get(key) for key in ("total", "active", "inactive")
            },
            "pool": _pool(),
        }
    )


# --------------------------------------------------------------------------- #
# 读工具 3：backup_status
# --------------------------------------------------------------------------- #


def _backup_alerts(snapshot: dict) -> list[dict]:
    """快照上的异常标记：``state`` 是冻结枚举，判定逻辑只读不改。"""
    state = snapshot.get("state")
    alerts: list[dict] = []
    if state == "failed":
        alerts.append(
            {
                "level": "error",
                "code": "backup_failed",
                "message": snapshot.get("message") or "最近一次备份失败",
            }
        )
    elif state in (None, "", "idle"):
        alerts.append(
            {
                "level": "info",
                "code": "backup_no_recent_run",
                "message": "备份日志里没有可判定的最近一次运行",
            }
        )
    elapsed = snapshot.get("elapsed_seconds")
    if state == "running" and isinstance(elapsed, (int, float)) and elapsed > 3600:
        alerts.append(
            {
                "level": "warning",
                "code": "backup_running_long",
                "message": f"备份已运行 {int(elapsed)} 秒，超过一小时",
            }
        )
    return alerts


async def _impl_backup_status(user: dict) -> dict:
    try:
        snapshot = await adb_backup.backup_snapshot()
    except Exception as exc:  # noqa: BLE001 - 探测失败要可读，不要 500
        raise AgentError(
            "AI_AGENT_TOOL_FAILED",
            f"backup status unavailable: {type(exc).__name__}",
            503,
        ) from None
    return _cap(
        {
            "kind": "backup",
            # ``backup_snapshot`` 的键集是冻结结构（契约 §），这里原样透传，
            # 只在外面加 ``alerts``，不改服务层返回。
            **snapshot,
            "alerts": _backup_alerts(snapshot),
        }
    )


# --------------------------------------------------------------------------- #
# 读工具 4：scheduler_status
# --------------------------------------------------------------------------- #


def _next_run(job: Any) -> str | None:
    """``job.next_run_time``：未排程的 job 读它会抛 ``ValueError``（APScheduler 3）。"""
    try:
        value = job.next_run_time
    except Exception:  # noqa: BLE001 - pending job 没有下次运行时间
        return None
    if value is None:
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _job_summary(job: Any) -> dict:
    trigger = getattr(job, "trigger", None)
    return {
        "id": getattr(job, "id", None),
        "name": getattr(job, "name", None),
        "pending": bool(getattr(job, "pending", False)),
        "next_run_time": _next_run(job),
        "trigger": str(trigger) if trigger is not None else None,
    }


async def _impl_scheduler_status(user: dict) -> dict:
    """调度器 job 一览。

    ``import main`` 放在函数体里：``main`` 会 import 全部 router，而 router 的
    依赖链又会回到 ``services.agent``——模块级导入就是一条环。失败一律转成结构化
    ``error`` 而不是抛：读工具挂掉时巡检应当继续，而不是整轮 run 崩。
    """
    try:
        import main

        scheduler = main.scheduler
        jobs = scheduler.get_jobs()
    except Exception as exc:  # noqa: BLE001 - 结构化失败
        return {
            "kind": "scheduler",
            "available": False,
            "error": f"{type(exc).__name__}: {exc}",
            "jobs": [],
        }

    summaries = [_job_summary(job) for job in jobs]
    summaries.sort(key=lambda item: item.get("id") or "")
    return _cap(
        {
            "kind": "scheduler",
            "available": True,
            "running": bool(getattr(scheduler, "running", False)),
            "job_count": len(summaries),
            "jobs": summaries,
        }
    )


# --------------------------------------------------------------------------- #
# 读工具 5：device_ban_list
# --------------------------------------------------------------------------- #


async def _impl_device_ban_list(
    user: dict, query: str = "", page: int = 1, size: int = 20
) -> dict:
    page = max(1, int(page))
    size = max(1, min(int(size), DEVICE_BAN_PAGE_MAX))
    rows, total = await backends.get_device_bans_repo().list_page(
        page, size, _text(query)
    )

    ids = [row["xuehai_id"] for row in rows if row.get("xuehai_id")]
    try:
        counts = await backends.get_device_ban_counters_repo().get_counts(ids)
    except Exception:  # noqa: BLE001 - 处罚次数是补充信息
        logger.warning("agent device_ban_list counters failed", exc_info=True)
        counts = {}

    items = [
        {
            "xuehai_id": row.get("xuehai_id"),
            "reason": row.get("reason"),
            "until": row.get("until"),
            "source": row.get("source"),
            "rule_code": row.get("rule_code"),
            "operator_oid": row.get("operator_oid"),
            "created_at": row.get("created_at"),
            "ban_count": int(counts.get(row.get("xuehai_id"), 0) or 0),
        }
        for row in rows
    ]
    return _cap(
        {
            "kind": "device_bans",
            "page": page,
            "size": size,
            "total": int(total),
            "returned": len(items),
            "items": items,
        }
    )


# --------------------------------------------------------------------------- #
# 写工具：目标解析与硬保护（guard 之外的第二层，按目标而非按操作者）
# --------------------------------------------------------------------------- #


async def _resolve_target(user_oid: Any, business_id: Any) -> dict:
    """``user_oid`` 或学号 → 用户文档；都不给 400，查不到 404。"""
    oid = _text(user_oid)
    sid = _text(business_id)
    repo = backends.get_users_repo()
    if oid:
        target = await repo.get_by_oid(oid)
    elif sid:
        target = await repo.find_by_business_id(sid)
    else:
        raise AgentError(
            "AI_AGENT_BAD_ARGUMENT", "user_oid or business_id is required", 400
        )
    if target is None:
        raise AgentError("AI_AGENT_NOT_FOUND", "user not found", 404)
    return target


async def _assert_target_allowed(user: dict, target: dict) -> str:
    """封禁/解封的目标护栏，返回目标 oid。

    三条拒绝理由，都写在代码里而不是 system prompt 里：

    * 目标就是操作者自己 → 403（自锁管理入口，与
      ``set_user_banned_v3`` 的「不能封禁自己」同一口径）；
    * 目标重算权限含 ``admin`` → 403（``get_user_permissions`` 用**库里**的
      ``roles``，不是调用方传进来的 token）；
    * 重算失败 → 403（fail closed：判断不了就别动）。

    没有这一层，模型只要把 ``user_oid`` 换成一个管理员的 oid，就能用「自动封禁
    灌水账号」的名义把整个管理组关掉——而 prompt 只能劝阻，不能阻止。
    """
    oid = str(target.get("_id") or "")
    if not oid:
        raise AgentError("AI_AGENT_NOT_FOUND", "user not found", 404)
    if oid == str(user.get("id") or ""):
        raise AgentError(
            "AI_AGENT_FORBIDDEN", "不能对当前操作者自己执行该操作", 403
        )
    try:
        perms = set(await get_user_permissions(target) or [])
    except Exception:  # noqa: BLE001 - 目标权限不可判定时按拒绝处理
        raise AgentError(
            "AI_AGENT_FORBIDDEN", "无法确认目标账号权限，已拒绝", 403
        ) from None
    if "admin" in perms:
        raise AgentError(
            "AI_AGENT_FORBIDDEN", "不能封禁或解封管理员账号", 403
        )
    return oid


def _ban_state(doc: dict | None) -> dict:
    """封禁状态快照（``banned`` 列到期不归零，``active`` 才是此刻是否生效）。"""
    if not doc:
        return {}
    return {
        "banned": bool(doc.get("banned")),
        "active": ban_is_active(doc),
        "reason": humanize_ban_reason(doc.get("ban_reason")) or None,
        "until": doc.get("ban_until"),
        "source": doc.get("ban_source"),
        "rule": doc.get("ban_rule"),
    }


def _target_facts(target: dict) -> dict:
    return {
        "oid": str(target.get("_id") or ""),
        "business_id": target.get("id"),
        "name": target.get("name"),
    }


async def _insert_record(
    oid: str,
    *,
    user: dict,
    reason: str,
    started_at: datetime,
    expires_at: datetime | None,
) -> None:
    """写 ``ban_records`` 流水（``source=agent``，操作者 = 当前管理员）。

    ``source=agent`` 由 V042 显式加进 ``ck_ban_records_source`` 白名单
    （``('admin','rule','agent')``，见 ``db/models.py`` 与 V042 DDL），生产已应用；
    未应用该迁移的实例仍会被数据库拒绝，那种情况照旧 503。

    流水写在**状态之前**：被约束拒绝时用户行的 ban 字段一个字节都没改，调用方
    只需把 503 原样抛给模型。
    """
    try:
        await backends.get_ban_records_repo().insert(
            oid,
            source="agent",
            rule_code=None,
            reason=reason,
            permanent=expires_at is None,
            started_at=started_at,
            expires_at=expires_at,
            operator_oid=str(user.get("id") or ""),
        )
    except IntegrityError as exc:
        if _checks_source(exc):
            raise AgentError(
                "AI_AGENT_TOOL_FAILED",
                "ban_records.source 尚未支持 agent（需先落地对应迁移）",
                503,
            ) from None
        raise


def _checks_source(exc: Exception) -> bool:
    """是不是 ``CK_BAN_RECORDS_SOURCE`` 触发的（别的约束照常外抛）。"""
    return "BAN_RECORDS_SOURCE" in str(exc).upper()


# --------------------------------------------------------------------------- #
# 写工具 1：user_ban（direct）
# --------------------------------------------------------------------------- #


async def _impl_user_ban(
    user: dict,
    reason: str,
    user_oid: str = "",
    business_id: str = "",
    duration_days: int | None = None,
) -> dict:
    reason_text = _text(reason)
    if not reason_text:
        raise AgentError(
            "AI_AGENT_BAD_ARGUMENT", "reason is required", 400
        )
    reason_text = reason_text[:BAN_REASON_MAX_CHARS]

    until: datetime | None = None
    if duration_days is not None:
        days = int(duration_days)
        if days < 1 or days > BAN_DURATION_MAX_DAYS:
            raise AgentError(
                "AI_AGENT_BAD_ARGUMENT",
                f"duration_days must be between 1 and {BAN_DURATION_MAX_DAYS}",
                400,
            )
        until = datetime.now() + timedelta(days=days)

    target = await _resolve_target(user_oid, business_id)
    oid = await _assert_target_allowed(user, target)

    before = _ban_state(target)
    now = datetime.now()
    # 顺序：流水 → 状态 → 审计日志。流水在前是为了让「数据库不认 agent 来源」
    # 这类拒绝发生在**任何状态被改之前**（见 _insert_record 的说明）。
    await _insert_record(
        oid, user=user, reason=reason_text, started_at=now, expires_at=until
    )
    await backends.get_users_repo().set_ban(
        oid, reason=reason_text, until=until, source="agent", rule_code=None
    )

    sentence = "永久" if until is None else f"至 {until.isoformat()}"
    operator = await _display_name(str(user.get("id") or ""))
    await _audit(
        user,
        (
            f"ZVAgent（操作者 {operator}）封禁了用户 {target.get('name')}"
            f"（学号 {target.get('id')}，原因：{reason_text}，{sentence}）"
        ),
        log_type=AUDIT_TYPE_BAN,
    )

    after = await backends.get_users_repo().get_by_oid(oid)
    return _cap(
        {
            "kind": "user_ban",
            **_target_facts(target),
            "reason": reason_text,
            "duration_days": duration_days,
            "permanent": until is None,
            "until": until,
            "before": before,
            "after": _ban_state(after),
        }
    )


# --------------------------------------------------------------------------- #
# 写工具 2：user_unban（direct）
# --------------------------------------------------------------------------- #


async def _impl_user_unban(
    user: dict, reason: str, user_oid: str = "", business_id: str = ""
) -> dict:
    reason_text = _text(reason)
    if not reason_text:
        raise AgentError("AI_AGENT_BAD_ARGUMENT", "reason is required", 400)
    reason_text = reason_text[:BAN_REASON_MAX_CHARS]

    target = await _resolve_target(user_oid, business_id)
    oid = await _assert_target_allowed(user, target)

    before = _ban_state(target)
    if not before.get("active"):
        # 幂等：本来就没封就不该写流水，否则反复巡检会把 BAN_RECORDS 灌满垃圾行。
        return _cap(
            {
                "kind": "user_unban",
                **_target_facts(target),
                "changed": False,
                "note": "目标当前未被封禁（或封禁已到期），未做修改",
                "before": before,
                "after": before,
            }
        )

    now = datetime.now()
    await _insert_record(
        oid, user=user, reason=reason_text, started_at=now, expires_at=now
    )
    await backends.get_users_repo().clear_ban(oid)

    operator = await _display_name(str(user.get("id") or ""))
    await _audit(
        user,
        (
            f"ZVAgent（操作者 {operator}）解封了用户 {target.get('name')}"
            f"（学号 {target.get('id')}，原因：{reason_text}）"
        ),
        log_type=AUDIT_TYPE_BAN,
    )

    after = await backends.get_users_repo().get_by_oid(oid)
    return _cap(
        {
            "kind": "user_unban",
            **_target_facts(target),
            "changed": True,
            "reason": reason_text,
            "before": before,
            "after": _ban_state(after),
        }
    )


# --------------------------------------------------------------------------- #
# 写工具 3：device_unban（direct）
# --------------------------------------------------------------------------- #


async def _impl_device_unban(
    user: dict, xuehai_id: str, reason: str
) -> dict:
    xid = _text(xuehai_id)
    if not xid:
        raise AgentError("AI_AGENT_BAD_ARGUMENT", "xuehai_id is required", 400)
    reason_text = _text(reason)
    if not reason_text:
        raise AgentError("AI_AGENT_BAD_ARGUMENT", "reason is required", 400)
    reason_text = reason_text[:BAN_REASON_MAX_CHARS]

    repo = backends.get_device_bans_repo()
    before = await repo.get(xid)
    if before is None:
        return _cap(
            {
                "kind": "device_unban",
                "xuehai_id": xid,
                "changed": False,
                "note": "该设备当前没有显式封禁行",
                "counters_removed": 0,
            }
        )

    removed = await repo.delete(xid)
    counters_removed = 0
    if removed:
        # 处罚梯度计数一并清掉：留着会让这台机器下一次违规直接跳到更重的档位。
        try:
            counters_removed = await backends.get_device_ban_counters_repo().delete(
                xid
            )
        except Exception:  # noqa: BLE001 - 计数清理失败不该把解封回滚
            logger.warning("agent device_unban counters delete failed", exc_info=True)

    operator = await _display_name(str(user.get("id") or ""))
    await _audit(
        user,
        (
            f"ZVAgent（操作者 {operator}）解除了设备 {xid} 的封禁"
            f"（原因：{reason_text}）"
        ),
        log_type=AUDIT_TYPE_DEVICE,
    )
    return _cap(
        {
            "kind": "device_unban",
            "xuehai_id": xid,
            "changed": bool(removed),
            "removed": int(removed),
            "counters_removed": int(counters_removed),
            "reason": reason_text,
            "before": before,
            "note": "账号级封禁不在此列：目标是账号时仍会被 device_ban_status 推导为受限",
        }
    )


# --------------------------------------------------------------------------- #
# 注册（5 读 + 3 写）
# --------------------------------------------------------------------------- #

_STR = {"type": "string"}
_INT = {"type": "integer"}

_TIME = {
    **_STR,
    "description": "ISO-8601 时间（默认最近 7 天，窗口最多 90 天）",
}


_register(
    ToolDef(
        name="platform_logs",
        description=(
            "查询平台运行日志（app_logs）：按类型/级别/用户/设备/时间窗/关键词过滤，"
            "返回时间倒序最多 50 条、命中总数与窗口内按类型的计数。只读。"
        ),
        kind=READ,
        params=_params(
            {
                "keyword": {**_STR, "description": "url 或 data 的关键词（大小写不敏感）"},
                "ltype": {**_STR, "description": "日志类型，如 user_login / ban_change"},
                "log_level": {
                    **_STR,
                    "description": "级别，如 info / warning / error",
                },
                "user_oid": {**_STR, "description": "用户 oid（app_user）"},
                "xuehai_id": {**_STR, "description": "学海设备号"},
                "time_start": _TIME,
                "time_end": _TIME,
                "limit": {
                    **_INT,
                    "minimum": 1,
                    "maximum": MAX_LOG_ROWS,
                    "description": f"最多返回多少条（1-{MAX_LOG_ROWS}，默认 50）",
                },
            },
            (),
        ),
        permission=_admin_only,
        impl=_impl_platform_logs,
        guard=ops_auth.ensure_system_admin,
    )
)


_register(
    ToolDef(
        name="db_diagnostics",
        description=(
            "数据库诊断：负载与会话数、Top 慢 SQL（≤10）、等待类（≤10）、"
            "表空间使用率、本进程连接池状态。只读，不接受任意 SQL。"
        ),
        kind=READ,
        params=_params({}, ()),
        permission=_admin_only,
        impl=_impl_db_diagnostics,
        guard=ops_auth.ensure_system_admin,
    )
)


_register(
    ToolDef(
        name="backup_status",
        description=(
            "ADB 备份状态：最近一次备份的开始/结束时间、已处理表数与行数、"
            "服务状态（idle/running/ok/failed）与异常标记。只读。"
        ),
        kind=READ,
        params=_params({}, ()),
        permission=_admin_only,
        impl=_impl_backup_status,
        guard=ops_auth.ensure_system_admin,
    )
)


_register(
    ToolDef(
        name="scheduler_status",
        description="列出后台调度器的全部 job（id、下次运行时间、触发器描述、是否在运行）。只读。",
        kind=READ,
        params=_params({}, ()),
        permission=_admin_only,
        impl=_impl_scheduler_status,
        guard=ops_auth.ensure_system_admin,
    )
)


_register(
    ToolDef(
        name="device_ban_list",
        description=(
            "分页列出设备封禁（学海设备号、原因、来源、到期、处罚次数），"
            "可按设备号搜索。只读。"
        ),
        kind=READ,
        params=_params(
            {
                "query": {**_STR, "description": "设备号关键词"},
                "page": {**_INT, "minimum": 1, "description": "页码（从 1 起）"},
                "size": {
                    **_INT,
                    "minimum": 1,
                    "maximum": DEVICE_BAN_PAGE_MAX,
                    "description": f"每页条数（1-{DEVICE_BAN_PAGE_MAX}，默认 20）",
                },
            },
            (),
        ),
        permission=_admin_only,
        impl=_impl_device_ban_list,
        guard=ops_auth.ensure_system_admin,
    )
)


_register(
    ToolDef(
        name="user_ban",
        description=(
            "封禁非管理员账号（巡检用，直接执行，无需二次确认）：可给 user_oid 或学号，"
            "duration_days 省略=永久。不能封自己、不能封管理员。会写封禁流水与审计日志。"
        ),
        kind=WRITE,
        params=_params(
            {
                "user_oid": {**_STR, "description": "用户 oid（与 business_id 二选一）"},
                "business_id": {**_STR, "description": "学号（与 user_oid 二选一）"},
                "reason": {
                    **_STR,
                    "maxLength": 200,
                    "description": "封禁原因（会进审计与用户可见的封禁提示）",
                },
                "duration_days": {
                    **_INT,
                    "minimum": 1,
                    "maximum": BAN_DURATION_MAX_DAYS,
                    "description": "封禁天数；省略=永久",
                },
            },
            ("reason",),
        ),
        permission=_admin_only,
        impl=_impl_user_ban,
        guard=ops_auth.ensure_system_admin,
        direct=True,
    )
)


_register(
    ToolDef(
        name="user_unban",
        description=(
            "解除非管理员账号的封禁（直接执行）：可给 user_oid 或学号，reason 必填。"
            "不能操作管理员；本来未封禁时不做修改。会写解封流水与审计日志。"
        ),
        kind=WRITE,
        params=_params(
            {
                "user_oid": {**_STR, "description": "用户 oid（与 business_id 二选一）"},
                "business_id": {**_STR, "description": "学号（与 user_oid 二选一）"},
                "reason": {**_STR, "maxLength": 200, "description": "解封原因（必填）"},
            },
            ("reason",),
        ),
        permission=_admin_only,
        impl=_impl_user_unban,
        guard=ops_auth.ensure_system_admin,
        direct=True,
    )
)


_register(
    ToolDef(
        name="device_unban",
        description=(
            "解除某台学海设备的显式封禁并清理其处罚计数（直接执行）。"
            "只解除设备行；账号级封禁需用 user_unban。"
        ),
        kind=WRITE,
        params=_params(
            {
                "xuehai_id": {**_STR, "description": "学海设备号"},
                "reason": {**_STR, "maxLength": 200, "description": "解封原因（必填）"},
            },
            ("xuehai_id", "reason"),
        ),
        permission=_admin_only,
        impl=_impl_device_unban,
        guard=ops_auth.ensure_system_admin,
        direct=True,
    )
)


__all__ = [
    "BAN_DURATION_MAX_DAYS",
    "DEVICE_BAN_PAGE_MAX",
    "LOG_MAX_WINDOW_DAYS",
    "MAX_LOG_ROWS",
]
