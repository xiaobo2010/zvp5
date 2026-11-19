"""数据库维护：表结构体检、优化建议、SQL 计划与后台作业（ADB）。

清理/维护端点（``routers/db_system_router.py``）之前只做「删行 + 备份」，但
``app_logs`` 之外的字典统计、索引碎片、回收站占用都没有入口；更关键的是清理
删的是 ADB 行，Redis 里 ``zvms:user:*`` / ``zvms:names:*`` / ``zvms:activity:*``
不会自己失效，清理后最多 10 分钟仍可能读到已删数据。本模块补上两件事：

* **只读体检**：``overview`` / ``suggestions`` 只查 ``USER_*`` 字典视图，单个
  组件查不了（权限/超时）就降级成 ``{"available": false, "reason": ...}``，
  绝不让整页 503。
* **可审阅的维护作业**：``build_plan`` 只生成 SQL（不执行），``start_job`` 把
  执行丢进后台任务并**在收尾时统一失效 Redis**（``cache.flush_scopes``），
  于是「维护完成 → 缓存自动刷新」是一条链，而不是靠 TTL 碰运气。

三条不可让步的边界：

* 标识符只走 ``services.db_content.validate_identifier``，SQL 文本里只出现
  已校验（大写 + 正则）的表名/索引名；请求里的值永不出现在 SQL 里。
* ADB 对 ``ALTER TABLE ... SHRINK SPACE`` 的支持不稳定（实测可能 ORA-00439/
  ORA-65040），所以**逐表独立执行、独立提交**，一张失败只记进 ``errors``，
  继续下一张；整体作业不会因此中断。
* 异常对外只暴露 ``ORA-nnnnn`` 或异常类名：Oracle 报错里可能夹带 SQL 片段与
  连接信息，这些只留在日志里。

单 worker 前提与 :mod:`tasks.retention` 一致：作业状态是进程内变量，没有外部
存储。多 worker 部署时进度端点只会看到自己进程里的作业。
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Sequence

from sqlalchemy import text

from db import backends, cache
from db.oracle.session import session_scope
from services import db_content, settings_runtime
from services.roles import policy as roles_policy
from util.serialization import iso

_logger = logging.getLogger(__name__)

#: ``/run`` / ``/plan`` 接受的维护动作（顺序即文档顺序）。
ACTIONS = (
    "gather_stats",
    "rebuild_indexes",
    "shrink_space",
    "purge_recyclebin",
    "flush_cache",
    "warm_cache",
)

#: 会产生 SQL 的动作：执行完统一失效这四类业务缓存。
SQL_ACTIONS = ("gather_stats", "rebuild_indexes", "shrink_space", "purge_recyclebin")
DEFAULT_FLUSH_SCOPES = ("users", "groups", "activities", "conversations")
#: ``/cache/flush`` 与 ``flush_cache`` 动作可以显式失效的全部逻辑域。
CACHE_SCOPES = (
    "users",
    "groups",
    "activities",
    "conversations",
    "notifications",
    "changelog",
    "settings",
    "roles",
)

MAX_TABLES = 50
#: 计划条目上限：给「重建某表全部索引」这类请求一个硬边界。
MAX_PLAN_ITEMS = 200
STATS_STALE_DAYS = 30
HIGH_BLEVEL = 3
LARGE_TABLE_ROWS = 5000
LARGE_TABLE_BYTES = 50 * 1024 * 1024
MAX_SUGGESTIONS = 50

#: 单条字典/DDL 语句的超时。ADB 上 ``GATHER_TABLE_STATS`` / ``SHRINK SPACE``
#: 可能跑几十秒，但一条语句不该无限挂住后台作业。
STATEMENT_TIMEOUT_SECONDS = 60.0

AUDIT_TYPE = "db_maintenance"
#: warning 是承重取值：该行落在 app_logs 清理保护集内（与 tasks.retention 同）。
AUDIT_LEVEL = "warning"

BUSY_MESSAGE = "数据库维护任务正在执行中，请稍后重试"

PHASE_PLANNING = "planning"
PHASE_RUNNING = "running"
PHASE_FLUSHING_CACHE = "flushing_cache"
PHASE_WARMING_CACHE = "warming_cache"
PHASE_DONE = "done"
PHASE_FAILED = "failed"

STATE_RUNNING = "running"
STATE_DONE = "done"
STATE_FAILED = "failed"

# ---------------------------------------------------------------------------
# 只读字典查询
# ---------------------------------------------------------------------------
#
# 全部是 USER_ 前缀的只读字典视图：``USER_TABLES`` 给基数，``USER_TAB_STATISTICS``
# 给**收集时刻的真实值**（``USER_TABLES`` 里的统计列可能停在旧值），
# ``USER_INDEXES`` 给索引状态/层级，``USER_SEGMENTS`` 给真实占用字节。
# ``USER_IND_COLUMNS`` 不在最初的四张清单里，但 ``duplicate_index`` 需要「同列
# 集合」——``USER_INDEXES`` 不含列信息，没有它这条规则无法实现；它同样是当前
# schema 的只读视图，不会跨 schema 读数据。

_TABLES_SQL = text(
    "SELECT table_name, num_rows, blocks, avg_row_len, last_analyzed "
    "FROM user_tables ORDER BY table_name"
)
_STATS_SQL = text(
    "SELECT table_name, num_rows, blocks, avg_row_len, last_analyzed "
    "FROM user_tab_statistics WHERE object_type = 'TABLE'"
)
_INDEXES_SQL = text(
    "SELECT table_name, index_name, index_type, uniqueness, status, blevel, num_rows "
    "FROM user_indexes ORDER BY table_name, index_name"
)
_INDEX_COLUMNS_SQL = text(
    "SELECT index_name, column_position, column_name FROM user_ind_columns "
    "ORDER BY index_name, column_position"
)
_SEGMENTS_SQL = text("SELECT segment_name, segment_type, bytes FROM user_segments")

_SEVERITY_RANK = {"high": 0, "medium": 1, "low": 2}


class MaintenanceError(Exception):
    """维护动作被拒；路由层转 ``HTTPException(status_code, detail)``。"""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class JobBusy(RuntimeError):
    """本进程内已有一个维护作业在跑。"""

    def __init__(self, message: str = BUSY_MESSAGE, phase: str | None = None) -> None:
        super().__init__(f"{message}（当前阶段：{phase}）" if phase else message)
        self.phase = phase


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------


def _int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_datetime(value: Any) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is not None:
        # 字典里的 last_analyzed 通常是 naive（ADB 会话时区 UTC）；带时区时统一
        # 换成本地 naive，避免和 ``datetime.now()`` 比较时抛 TypeError。
        return value.astimezone().replace(tzinfo=None)
    return value


def _iso(value: Any) -> str | None:
    """先按运维面的口径归一（带时区换本地 naive），再统一序列化。"""
    return iso(_as_datetime(value))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get(row: Any, index: int, key: str) -> Any:
    """``Row``（带 ``_mapping``）或普通元组都能按位置取值。"""
    mapping = getattr(row, "_mapping", None)
    if mapping is not None and key in mapping:
        return mapping[key]
    return row[index]


def _ora_code(exc: BaseException) -> int | None:
    """从 SQLAlchemy/驱动异常里挖出 ORA 错误号（挖不到返回 ``None``）。"""
    for candidate in (getattr(exc, "orig", None), exc):
        for arg in getattr(candidate, "args", ()) or ():
            code = getattr(arg, "code", None)
            if isinstance(code, int):
                return code
    return None


def _reason_for(exc: BaseException) -> str:
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
        return "query_timeout"
    if _ora_code(exc) in (942, 1031):
        return "insufficient_privileges"
    return "unavailable"


def _sanitize_error(exc: BaseException) -> str:
    """对外错误标识：``ORA-nnnnn`` 或异常类名，绝不含 SQL 文本/DSN。"""
    code = _ora_code(exc)
    if code is not None:
        return f"ORA-{code:05d}"
    return type(exc).__name__


def _validate_action(action: Any) -> str:
    value = str(action or "").strip().lower()
    if value not in ACTIONS:
        raise MaintenanceError(400, f"不支持的维护动作：{action!r}")
    return value


def _normalize_tables(tables: Sequence[str] | None) -> list[str]:
    """校验并去重表名（大写形态）；非法/超量一律 400。"""
    if tables is None:
        return []
    if isinstance(tables, str) or not isinstance(tables, Sequence):
        raise MaintenanceError(400, "tables 必须是表名数组")
    if len(tables) > MAX_TABLES:
        raise MaintenanceError(400, f"tables 最多 {MAX_TABLES} 张表")
    normalized: list[str] = []
    for raw in tables:
        try:
            name = db_content.validate_identifier(str(raw), what="表名")
        except db_content.DbContentError as exc:
            raise MaintenanceError(exc.status_code, exc.detail) from None
        if name not in normalized:
            normalized.append(name)
    return normalized


def _normalize_scopes(scopes: Sequence[str] | None) -> list[str]:
    if not scopes:
        raise MaintenanceError(400, "scopes 不能为空")
    normalized: list[str] = []
    for raw in scopes:
        scope = str(raw or "").strip().lower()
        if scope not in CACHE_SCOPES:
            raise MaintenanceError(
                400, f"不支持的缓存域：{raw!r}（可选：{', '.join(CACHE_SCOPES)}）"
            )
        if scope not in normalized:
            normalized.append(scope)
    return normalized


# ---------------------------------------------------------------------------
# 只读体检
# ---------------------------------------------------------------------------


async def _component(
    session: Any, name: str, statement: Any
) -> tuple[list[Any] | None, dict[str, Any]]:
    """跑一条字典查询；失败降级成组件级 ``available=False``（不抛给上层）。"""
    try:
        result = await asyncio.wait_for(
            session.execute(statement), timeout=STATEMENT_TIMEOUT_SECONDS
        )
        return list(result.fetchall()), {"available": True}
    except Exception as exc:  # noqa: BLE001 - 字典权限/超时按组件降级
        reason = _reason_for(exc)
        _logger.warning("db maintenance component %s unavailable (%s)", name, reason)
        try:
            await asyncio.wait_for(
                session.rollback(), timeout=STATEMENT_TIMEOUT_SECONDS
            )
        except Exception:  # noqa: BLE001 - 回收失败不影响降级结果
            pass
        return None, {"available": False, "reason": reason}


def _empty_table(name: str) -> dict[str, Any]:
    return {
        "table_name": name,
        "num_rows": None,
        "blocks": None,
        "avg_row_len": None,
        "last_analyzed": None,
        "last_analyzed_at": None,
        "index_count": None,
        "index_bytes": None,
        "table_bytes": None,
        "total_bytes": None,
    }


async def _snapshot(session: Any) -> dict[str, Any]:
    """一次采集：组件状态 + 表级事实 + 索引明细（``overview``/``suggestions`` 共用）。"""
    components: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []

    table_rows, components["tables"] = await _component(session, "tables", _TABLES_SQL)
    stats_rows, components["statistics"] = await _component(
        session, "statistics", _STATS_SQL
    )
    index_rows, components["indexes"] = await _component(
        session, "indexes", _INDEXES_SQL
    )
    column_rows, components["index_columns"] = await _component(
        session, "index_columns", _INDEX_COLUMNS_SQL
    )
    segment_rows, components["segments"] = await _component(
        session, "segments", _SEGMENTS_SQL
    )
    for name, component in components.items():
        if not component["available"]:
            warnings.append(
                f"数据字典 {name} 不可用（{component['reason']}），相关字段为空"
            )

    # 索引列（用于 duplicate_index）
    columns_by_index: dict[str, list[tuple[int, str]]] = {}
    for row in column_rows or ():
        index_name = str(_get(row, 0, "index_name")).upper()
        position = _int_or_none(_get(row, 1, "column_position")) or 0
        column = str(_get(row, 2, "column_name")).upper()
        columns_by_index.setdefault(index_name, []).append((position, column))
    ordered_columns = {
        name: [column for _, column in sorted(pairs)]
        for name, pairs in columns_by_index.items()
    }

    indexes: list[dict[str, Any]] = []
    for row in index_rows or ():
        index_name = str(_get(row, 1, "index_name")).upper()
        indexes.append(
            {
                "table_name": str(_get(row, 0, "table_name")).upper(),
                "index_name": index_name,
                "index_type": str(_get(row, 2, "index_type") or "").upper(),
                "uniqueness": str(_get(row, 3, "uniqueness") or "").upper(),
                "status": str(_get(row, 4, "status") or "").upper(),
                "blevel": _int_or_none(_get(row, 5, "blevel")),
                "num_rows": _int_or_none(_get(row, 6, "num_rows")),
                "columns": ordered_columns.get(index_name, []),
            }
        )

    tables: dict[str, dict[str, Any]] = {}
    for row in table_rows or ():
        name = str(_get(row, 0, "table_name")).upper()
        entry = _empty_table(name)
        entry.update(
            num_rows=_int_or_none(_get(row, 1, "num_rows")),
            blocks=_int_or_none(_get(row, 2, "blocks")),
            avg_row_len=_int_or_none(_get(row, 3, "avg_row_len")),
            last_analyzed=_iso(_get(row, 4, "last_analyzed")),
            last_analyzed_at=_as_datetime(_get(row, 4, "last_analyzed")),
        )
        tables[name] = entry

    # USER_TAB_STATISTICS 是「最近一次 GATHER 的结果」，比 USER_TABLES 的缓存列更准。
    for row in stats_rows or ():
        name = str(_get(row, 0, "table_name")).upper()
        entry = tables.setdefault(name, _empty_table(name))
        for key, index in (
            ("num_rows", 1),
            ("blocks", 2),
            ("avg_row_len", 3),
        ):
            value = _int_or_none(_get(row, index, key))
            if value is not None:
                entry[key] = value
        analyzed = _get(row, 4, "last_analyzed")
        if analyzed is not None:
            entry["last_analyzed"] = _iso(analyzed)
            entry["last_analyzed_at"] = _as_datetime(analyzed)

    indexes_by_table: dict[str, list[dict[str, Any]]] = {}
    for index in indexes:
        indexes_by_table.setdefault(index["table_name"], []).append(index)
        tables.setdefault(index["table_name"], _empty_table(index["table_name"]))

    table_bytes: dict[str, int] = {}
    index_bytes: dict[str, int] = {}
    for row in segment_rows or ():
        segment_name = str(_get(row, 0, "segment_name")).upper()
        segment_type = str(_get(row, 1, "segment_type") or "").upper()
        size = _int_or_none(_get(row, 2, "bytes")) or 0
        if segment_type.startswith("TABLE"):
            table_bytes[segment_name] = table_bytes.get(segment_name, 0) + size
        elif segment_type.startswith("INDEX"):
            index_bytes[segment_name] = index_bytes.get(segment_name, 0) + size

    indexes_available = components["indexes"]["available"]
    segments_available = components["segments"]["available"]
    for name, entry in tables.items():
        own_indexes = indexes_by_table.get(name, [])
        entry["index_count"] = len(own_indexes) if indexes_available else None
        if segments_available:
            entry["table_bytes"] = table_bytes.get(name, 0)
            entry["index_bytes"] = sum(
                index_bytes.get(index["index_name"], 0) for index in own_indexes
            )
            entry["total_bytes"] = entry["table_bytes"] + entry["index_bytes"]
        else:
            entry["table_bytes"] = None
            entry["index_bytes"] = None
            entry["total_bytes"] = None

    ordered = [tables[name] for name in sorted(tables)]
    return {
        "available": components["tables"]["available"],
        "components": components,
        "warnings": warnings,
        "tables": ordered,
        "indexes": indexes,
        "observed_at": _now_iso(),
    }


_OVERVIEW_FIELDS = (
    "table_name",
    "num_rows",
    "blocks",
    "avg_row_len",
    "last_analyzed",
    "index_count",
    "index_bytes",
    "table_bytes",
    "total_bytes",
)


async def overview(session: Any) -> dict[str, Any]:
    """当前 schema 每张表的体量与统计新鲜度（组件级降级，不抛 503）。"""
    snapshot = await _snapshot(session)
    tables = [
        {field: table[field] for field in _OVERVIEW_FIELDS}
        for table in snapshot["tables"]
    ]
    payload: dict[str, Any] = {
        "available": snapshot["available"],
        "table_count": len(tables),
        "tables": tables,
        "components": snapshot["components"],
        "warnings": snapshot["warnings"],
        "observed_at": snapshot["observed_at"],
    }
    if not snapshot["available"]:
        payload["reason"] = snapshot["components"]["tables"].get("reason")
    return payload


def _suggestion(
    code: str, severity: str, table: str | None, message: str, action: str
) -> dict[str, Any]:
    return {
        "code": code,
        "severity": severity,
        "table": table,
        "message": message,
        "action": action,
    }


async def suggestions(session: Any) -> dict[str, Any]:
    """基于体检结果产出可执行的优化建议（按 severity 排序，最多 50 条）。"""
    snapshot = await _snapshot(session)
    stale_before = datetime.now() - timedelta(days=STATS_STALE_DAYS)
    items: list[dict[str, Any]] = []

    for table in snapshot["tables"]:
        name = table["table_name"]
        analyzed_at = table["last_analyzed_at"]
        if analyzed_at is None:
            items.append(
                _suggestion(
                    "stats_stale",
                    "medium",
                    name,
                    "从未收集过优化器统计信息，执行计划可能严重偏离实际",
                    "gather_stats",
                )
            )
        elif analyzed_at < stale_before:
            items.append(
                _suggestion(
                    "stats_stale",
                    "medium",
                    name,
                    f"统计信息已超过 {STATS_STALE_DAYS} 天未更新"
                    f"（最后收集：{analyzed_at.date().isoformat()}）",
                    "gather_stats",
                )
            )
        if (
            table["index_count"] == 0
            and (
                (table["num_rows"] or 0) >= LARGE_TABLE_ROWS
                or (table["total_bytes"] or 0) >= LARGE_TABLE_BYTES
            )
        ):
            items.append(
                _suggestion(
                    "table_large_no_index",
                    "high",
                    name,
                    "大表没有任何索引，按条件查询会退化成全表扫描",
                    "manual_review",
                )
            )

    index_columns_available = snapshot["components"]["index_columns"]["available"]
    for index in snapshot["indexes"]:
        name = index["table_name"]
        if index["status"] != "VALID":
            items.append(
                _suggestion(
                    "index_unusable",
                    "high",
                    name,
                    f"索引 {index['index_name']} 状态为 {index['status'] or 'UNKNOWN'}，"
                    "优化器不会使用它",
                    "rebuild_indexes",
                )
            )
        blevel = index["blevel"]
        if blevel is not None and blevel >= HIGH_BLEVEL:
            items.append(
                _suggestion(
                    "index_high_blevel",
                    "medium",
                    name,
                    f"索引 {index['index_name']} 层级为 {blevel}（≥{HIGH_BLEVEL}），"
                    "索引树偏高，每次等值查询要多读块",
                    "rebuild_indexes",
                )
            )

    if index_columns_available:
        groups: dict[tuple[str, tuple[str, ...]], list[str]] = {}
        for index in snapshot["indexes"]:
            if not index["columns"]:
                continue
            key = (index["table_name"], tuple(index["columns"]))
            groups.setdefault(key, []).append(index["index_name"])
        for (table_name, columns), names in groups.items():
            if len(names) < 2:
                continue
            items.append(
                _suggestion(
                    "duplicate_index",
                    "low",
                    table_name,
                    "同列集合存在多个索引：" + ", ".join(sorted(names))
                    + f"（列：{', '.join(columns)}），写放大且互相争抢空间",
                    "manual_review",
                )
            )

    items.sort(key=lambda item: (_SEVERITY_RANK[item["severity"]], item["table"] or ""))
    counts = {level: 0 for level in _SEVERITY_RANK}
    for item in items:
        counts[item["severity"]] += 1
    truncated = len(items) > MAX_SUGGESTIONS
    return {
        "available": snapshot["available"],
        "suggestions": items[:MAX_SUGGESTIONS],
        "counts": counts,
        "truncated": truncated,
        "components": snapshot["components"],
        "generated_at": _now_iso(),
    }


# ---------------------------------------------------------------------------
# SQL 计划（只生成，不执行）
# ---------------------------------------------------------------------------


def _gather_stats_sql(table: str) -> str:
    # tabname 是单引号字符串字面量；表名已过 validate_identifier（不含引号）。
    return (
        "BEGIN DBMS_STATS.GATHER_TABLE_STATS("
        f"ownname=>USER, tabname=>'{table}', cascade=>TRUE, degree=>4); END;"
    )


def _rebuild_index_sql(index_name: str) -> str:
    return f"ALTER INDEX {db_content.quote_identifier(index_name)} REBUILD ONLINE"


def _shrink_sql(table: str) -> str:
    return f"ALTER TABLE {db_content.quote_identifier(table)} SHRINK SPACE CASCADE"


CACHE_ONLY_NOTE = (
    "缓存动作不产生 SQL：直接失效 Redis 逻辑域，由下一次读请求重新生成快照"
)


async def build_plan(
    session: Any, action: str, tables: Sequence[str] | None = None
) -> dict[str, Any]:
    """生成维护计划；**只返回 SQL**，不执行任何 DDL。

    返回 ``{"action", "items": [{"table", "sql", "reason"}], "note"}``。
    ``flush_cache`` / ``warm_cache`` 的 ``items`` 恒为空。
    """
    action = _validate_action(action)
    wanted = _normalize_tables(tables)

    if action in ("flush_cache", "warm_cache"):
        return {"action": action, "items": [], "note": CACHE_ONLY_NOTE}
    if action == "purge_recyclebin":
        # 回收站是 schema 级对象，与表清单无关：不查字典，也不接受 tables。
        return {
            "action": action,
            "items": [
                {
                    "table": None,
                    "sql": "PURGE RECYCLEBIN",
                    "reason": "清空当前 schema 的回收站，释放被 DROP 对象占用的空间",
                }
            ],
            "note": "",
        }

    snapshot = await _snapshot(session)
    if wanted and snapshot["components"]["tables"]["available"]:
        known = {table["table_name"] for table in snapshot["tables"]}
        missing = [name for name in wanted if name not in known]
        if missing:
            raise MaintenanceError(400, "表不存在或无权访问：" + ", ".join(missing))

    items: list[dict[str, Any]] = []
    note = ""

    if action == "gather_stats":
        targets = wanted
        if not targets:
            stale_before = datetime.now() - timedelta(days=STATS_STALE_DAYS)
            targets = [
                table["table_name"]
                for table in snapshot["tables"]
                if table["last_analyzed_at"] is None
                or table["last_analyzed_at"] < stale_before
            ]
        for name in targets:
            items.append(
                {
                    "table": name,
                    "sql": _gather_stats_sql(name),
                    "reason": f"重建 {name} 的优化器统计信息（含索引级 cascade）",
                }
            )
        if not items:
            note = "没有统计信息过期的表；如需强制收集请显式传 tables"

    elif action == "rebuild_indexes":
        flagged: list[dict[str, Any]] = []
        if wanted:
            for index in snapshot["indexes"]:
                if index["table_name"] in wanted:
                    flagged.append(
                        {
                            **index,
                            "why": "按请求重建该表上的索引",
                        }
                    )
        else:
            for index in snapshot["indexes"]:
                reasons = []
                if index["status"] != "VALID":
                    reasons.append(f"状态 {index['status'] or 'UNKNOWN'}")
                if (index["blevel"] or 0) >= HIGH_BLEVEL:
                    reasons.append(f"blevel={index['blevel']}")
                if reasons:
                    flagged.append({**index, "why": "、".join(reasons)})
        for index in flagged:
            items.append(
                {
                    "table": index["table_name"],
                    "sql": _rebuild_index_sql(index["index_name"]),
                    "reason": f"重建索引 {index['index_name']}（{index['why']}）",
                }
            )
        if not items:
            note = "没有不可用或层级过高的索引"

    elif action == "shrink_space":
        if not wanted:
            # 全库 shrink 会把整个 schema 的段锁一遍，代价与风险都不该由一个
            # 开关触发；必须显式点名。
            raise MaintenanceError(400, "shrink_space 需要显式指定 tables（逐表执行）")
        for name in wanted:
            items.append(
                {
                    "table": name,
                    "sql": _shrink_sql(name),
                    "reason": "回收表内高水位以下的空闲空间（ADB 可能拒绝，失败不影响数据）",
                }
            )

    elif action == "purge_recyclebin":
        pass  # 已在上面提前返回

    if len(items) > MAX_PLAN_ITEMS:
        note = (
            (note + "；" if note else "")
            + f"计划条目超过 {MAX_PLAN_ITEMS} 条，已截断（请缩小 tables 范围）"
        )
        items = items[:MAX_PLAN_ITEMS]

    return {"action": action, "items": items, "note": note}


# ---------------------------------------------------------------------------
# 缓存刷新（消费 db.cache 的共享接口；对方未落地时退化）
# ---------------------------------------------------------------------------


async def _flush_scopes(scopes: Sequence[str]) -> dict[str, int]:
    """按逻辑域失效缓存。

    优先走 ``db.cache.flush_scopes``（SCAN+UNLINK 精确删除 + epoch 自增）。对方
    车道尚未落地时退回 ``bump_epoch``：缓存 key 里带 epoch（``zvms:names:<v>:…``
    这类），INCR 后旧 key 立即不可达，等价于整域失效；``user``/``auth`` 这类不带
    epoch 的精确 key 只能等各自的 60s TTL（比现状的 10 分钟窗口已收窄一个量级）。
    这里**不做** ``cache.forget()`` 空调用——没有可枚举的 key 清单，删不了任何东西。
    """
    flush = getattr(cache, "flush_scopes", None)
    if callable(flush):
        result = await flush(list(scopes))
        return {str(scope): int(count) for scope, count in (result or {}).items()}

    counts: dict[str, int] = {}
    for scope in scopes:
        await cache.bump_epoch(scope)
        counts[str(scope)] = 0
    return counts


async def _versions(scopes: Sequence[str]) -> dict[str, int]:
    reader = getattr(cache, "version", None)
    versions: dict[str, int] = {}
    for scope in scopes:
        if callable(reader):
            versions[scope] = int(await reader(scope))
        else:
            versions[scope] = int(await cache.epoch(scope))
    return versions


async def flush_cache(scopes: Sequence[str] | None = None) -> dict[str, Any]:
    """显式失效缓存域（``POST /cache/flush`` 与维护作业共用）。"""
    normalized = _normalize_scopes(scopes)
    flushed = await _flush_scopes(normalized)
    return {
        "scopes": normalized,
        "flushed": flushed,
        "versions": await _versions(normalized),
    }


async def refresh_snapshots() -> dict[str, bool]:
    """强制回源重建进程内快照（设置中心 / 角色策略）。

    两处 ``refresh()`` 都带版本闸（版本没变 + 未到 5 分钟 → 直接返回），而
    ``POST /cache/refresh`` 的语义是「我现在就要库里那份」，所以必须
    ``force=True`` 绕过闸门；否则「点了刷新但没回源」。

    两处内部都已「失败回落默认值」，这里再兜一层只是保证一个坏掉的刷新不会
    把另一个也带下去；返回的 bool 就是「这次是否刷新成功」。
    """
    result = {"settings": False, "roles": False}
    try:
        await settings_runtime.refresh(force=True)
        result["settings"] = True
    except Exception:  # noqa: BLE001 - 刷新失败不该让维护作业失败
        _logger.exception("db maintenance: settings refresh failed")
    try:
        await roles_policy.refresh(force=True)
        result["roles"] = True
    except Exception:  # noqa: BLE001
        _logger.exception("db maintenance: role policy refresh failed")
    return result


# ---------------------------------------------------------------------------
# 后台作业（与 tasks.retention 同范式：单 worker 进程内状态）
# ---------------------------------------------------------------------------

_job_lock = threading.Lock()
_current_job: dict[str, Any] | None = None
_background_task: "asyncio.Task | None" = None


def _empty_progress() -> dict[str, Any]:
    return {"total": 0, "done": 0, "failed": 0, "current": None}


def _reserve_job(
    action: str, tables: list[str], dry_run: bool, actor: str
) -> dict[str, Any]:
    global _current_job
    with _job_lock:
        if _current_job is not None and _current_job["state"] == STATE_RUNNING:
            raise JobBusy(phase=_current_job.get("phase"))
        job = {
            "job_id": str(uuid.uuid4()),
            "action": action,
            "tables": list(tables),
            "dry_run": bool(dry_run),
            "actor": actor,
            "state": STATE_RUNNING,
            "phase": PHASE_PLANNING,
            "started_at": _now_iso(),
            "finished_at": None,
            "progress": _empty_progress(),
            "result": None,
            "error": None,
        }
        _current_job = job
        return job


def _update_job(job: dict[str, Any], **changes: Any) -> None:
    with _job_lock:
        job.update(changes)


def _set_phase(job: dict[str, Any], phase: str) -> None:
    _update_job(job, phase=phase)


def _update_progress(job: dict[str, Any], **changes: Any) -> None:
    with _job_lock:
        job["progress"].update(changes)


def _finish_job(
    job: dict[str, Any],
    state: str,
    result: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    _update_job(
        job,
        state=state,
        phase=PHASE_DONE if state == STATE_DONE else PHASE_FAILED,
        finished_at=_now_iso(),
        result=result,
        error=error,
    )


async def _execute_statement(sql: str) -> None:
    """执行一条维护 SQL：独立会话、独立提交（一表失败不影响下一张）。"""
    async with session_scope() as session:
        connection = await session.connection()
        await connection.exec_driver_sql(sql)
        await session.commit()


async def _write_audit(job: dict[str, Any]) -> None:
    """收尾审计：``db_maintenance`` / ``warning``（落在清理保护集内）。"""
    progress = job["progress"]
    result = job.get("result") or {}
    mode = "预演" if job.get("dry_run") else "执行"
    lines = [
        f"数据库维护 {job['action']}（{mode}）：成功 {progress['done']}，"
        f"失败 {progress['failed']}，作业 {job['job_id']}"
    ]
    cache_info = result.get("cache") or {}
    if cache_info.get("scopes"):
        lines.append("已失效缓存域：" + ", ".join(cache_info["scopes"]))
    if job.get("error"):
        lines.append(f"作业异常：{job['error']}")
    if result.get("errors"):
        lines.append(
            "失败项："
            + "，".join(
                f"{item.get('table') or '-'}({item.get('error')})"
                for item in result["errors"][:10]
            )
        )
    try:
        await backends.get_applogs_repo().insert(
            url="",
            user=job.get("actor") or "",
            data="；".join(lines)[:4000],
            timestamp=time.time(),
            ltype=AUDIT_TYPE,
            level=AUDIT_LEVEL,
            xuehai_id="",
        )
    except Exception:  # noqa: BLE001 - 维护已发生，审计失败不能盖掉结果
        _logger.exception("db maintenance: audit write failed")


async def _execute_job(job: dict[str, Any]) -> dict[str, Any]:
    """作业主体：planning → running → flushing/warming → done。"""
    action = job["action"]
    _set_phase(job, PHASE_PLANNING)

    if action in ("flush_cache", "warm_cache"):
        plan = {"action": action, "items": [], "note": CACHE_ONLY_NOTE}
    else:
        async with session_scope() as session:
            plan = await build_plan(session, action, job["tables"])

    items = plan["items"]
    _update_progress(job, total=len(items), done=0, failed=0, current=None)

    done = 0
    failed = 0
    errors: list[dict[str, Any]] = []

    if not job["dry_run"]:
        _set_phase(job, PHASE_RUNNING)
        for item in items:
            label = item.get("table") or action
            _update_progress(job, current=label)
            try:
                await asyncio.wait_for(
                    _execute_statement(item["sql"]),
                    timeout=STATEMENT_TIMEOUT_SECONDS,
                )
            except Exception as exc:  # noqa: BLE001 - 逐表 catch（ADB 可能拒绝 shrink）
                failed += 1
                errors.append({"table": item.get("table"), "error": _sanitize_error(exc)})
                _logger.warning(
                    "db maintenance %s failed on %s: %r", action, label, exc
                )
            else:
                done += 1
            _update_progress(job, done=done, failed=failed)
        _update_progress(job, current=None)

    cache_info: dict[str, Any] = {"scopes": [], "flushed": {}, "versions": {}}
    warm: dict[str, bool] | None = None
    note = plan.get("note") or ""
    if job["dry_run"]:
        # 预演只出计划：缓存失效 / 快照重建同样是副作用，一并跳过。
        note = (note + "；" if note else "") + "预演未失效缓存"
    elif action in SQL_ACTIONS:
        _set_phase(job, PHASE_FLUSHING_CACHE)
        cache_info = await flush_cache(list(DEFAULT_FLUSH_SCOPES))
    elif action == "flush_cache":
        _set_phase(job, PHASE_FLUSHING_CACHE)
        cache_info = await flush_cache(list(CACHE_SCOPES))
    elif action == "warm_cache":
        _set_phase(job, PHASE_FLUSHING_CACHE)
        cache_info = await flush_cache(list(CACHE_SCOPES))
        _set_phase(job, PHASE_WARMING_CACHE)
        warm = await refresh_snapshots()

    payload: dict[str, Any] = {
        "action": action,
        "dry_run": bool(job["dry_run"]),
        "items": items,
        "note": note,
        "executed": done,
        "failed": failed,
        "errors": errors,
        "cache": cache_info,
    }
    if warm is not None:
        payload["warm"] = warm
    return payload


async def _run_job(job: dict[str, Any]) -> None:
    """跑完一个作业；**任何**异常都收尾成 failed（不向外抛）。"""
    try:
        result = await _execute_job(job)
        state = STATE_DONE
        if result["executed"] == 0 and result["failed"] > 0:
            state = STATE_FAILED
        _update_job(job, result=result)
        # 先落作业状态（客户端能立刻看到 done/failed），审计写入放在最后：
        # 审计失败不能把已完成的作业显示成还在跑。
        _finish_job(job, state, result=result)
        await _write_audit(job)
    except BaseException as exc:  # noqa: BLE001 - CancelledError 也要留下失败痕迹
        _logger.exception("db maintenance job %s failed", job.get("job_id"))
        # MaintenanceError 的 detail 是给管理员看的中文原因（不含 SQL/DSN），
        # 其余异常只暴露 ORA-nnnnn / 类名。
        detail = (
            exc.detail
            if isinstance(exc, MaintenanceError)
            else _sanitize_error(exc)
        )
        _finish_job(job, STATE_FAILED, result=job.get("result"), error=detail)
        try:
            await _write_audit(job)
        except Exception:  # noqa: BLE001
            pass


def start_job(
    action: str,
    tables: Sequence[str] | None,
    dry_run: bool,
    actor_oid: str,
) -> dict[str, Any]:
    """把维护动作丢进后台任务，立刻返回作业句柄（已有作业 → :class:`JobBusy`）。"""
    global _background_task
    action = _validate_action(action)
    wanted = _normalize_tables(tables)
    if action == "shrink_space" and not wanted and not dry_run:
        raise MaintenanceError(400, "shrink_space 需要显式指定 tables（逐表执行）")

    job = _reserve_job(action, wanted, dry_run, actor_oid)
    try:
        _background_task = asyncio.create_task(_run_job(job))
    except BaseException as exc:
        _finish_job(job, STATE_FAILED, error=_sanitize_error(exc))
        raise
    return {
        "job_id": job["job_id"],
        "started_at": job["started_at"],
        "state": STATE_RUNNING,
    }


def progress_snapshot() -> dict[str, Any]:
    """``GET /maintenance/progress`` 的 ``data``：进程内最近一个作业的状态。"""
    with _job_lock:
        job = _current_job
        if job is None:
            return {
                "running": False,
                "job_id": None,
                "action": None,
                "dry_run": False,
                "phase": None,
                "started_at": None,
                "finished_at": None,
                "progress": _empty_progress(),
                "result": None,
                "error": None,
            }
        return {
            "running": job["state"] == STATE_RUNNING,
            "job_id": job["job_id"],
            "action": job["action"],
            "dry_run": job["dry_run"],
            "phase": job["phase"],
            "started_at": job["started_at"],
            "finished_at": job["finished_at"],
            "progress": dict(job["progress"]),
            "result": job["result"],
            "error": job["error"],
        }


def reset_for_tests() -> None:
    """清空进程内作业状态（用例之间不串味）。"""
    global _current_job, _background_task
    with _job_lock:
        _current_job = None
        _background_task = None


__all__ = [
    "ACTIONS",
    "AUDIT_LEVEL",
    "AUDIT_TYPE",
    "BUSY_MESSAGE",
    "CACHE_SCOPES",
    "DEFAULT_FLUSH_SCOPES",
    "JobBusy",
    "MaintenanceError",
    "SQL_ACTIONS",
    "build_plan",
    "flush_cache",
    "overview",
    "progress_snapshot",
    "refresh_snapshots",
    "reset_for_tests",
    "start_job",
    "suggestions",
]
