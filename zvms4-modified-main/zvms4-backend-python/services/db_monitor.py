"""Read-only Oracle ADB health, storage and workload observations.

The service deliberately uses Oracle's dictionary views rather than counting
business rows.  ``USER_TABLES.NUM_ROWS`` is an optimizer estimate and is
returned together with ``LAST_ANALYZED`` so callers can judge its freshness.
Each optional view is isolated: a database account without DBA or performance
view privileges receives a degraded component instead of a misleading zero or
a whole-request 500.

Three data sources feed the panel:

* ``V$SYSSTAT`` (cumulative counters, turned into per-second rates) plus
  ``V$ACTIVE_SESSION_HISTORY`` for the load section and the AAS.  The previous
  ``V$SYSMETRIC`` source produced **no rows** on ADB, so the load section was
  permanently empty.
* ``V$SESSION`` for the breakdown/details, behind a short dedicated cache; and
  ``V$SQLAREA`` (filtered by parsing schema) + waits by class for the sampler's
  slow tier.
* Local ``/proc`` reads for host metrics and the in-process endpoint window —
  both cost zero database round trips.
"""

from __future__ import annotations

import asyncio
import logging
import math
import re
import threading
import time
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from services.api_metrics import api_metrics
from services.host_metrics import collect_host_metrics

_logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Own-SQL fingerprint (Top SQL leaderboard hygiene)
# --------------------------------------------------------------------------- #
# The Top SQL list is dominated by *this service's own* monitoring statements:
# production showed the table/tablespace CTE (exec=7, elapsed 15489ms,
# avg 2212ms) and ``SELECT 1 FROM DUAL`` (exec=104273; the pool's pre-ping and
# the health check).  They are real work by the app account, but reporting them
# as "Top SQL" tells the operator nothing and hides business statements.
#
# The rule is a SQL-text fingerprint, applied twice: inside the query (cheap,
# keeps Oracle from ranking them at all) and again in Python (defence in depth,
# and it also hides rows stored by an older build).
#
# Fingerprint (uppercased, whitespace-tolerant):
#   * ``FROM v$…`` / ``FROM gv$…``  — dynamic performance views
#   * ``FROM dba_…``                — dictionary views
#   * ``FROM user_segments|tables|indexes|lobs`` — segment/table inventory
#   * ``WITH table_storage AS``     — the storage-report CTE
#   * ``SELECT 1 FROM DUAL``        — liveness probe (health check + pool pre-ping)
#
# False-positive risk is nil for this application: no business repository reads
# a dictionary/performance view or DUAL (checked across ``db/repos/oracle/**``),
# so anything matching is by construction monitoring SQL.
#
# ``[[:space:]]`` is a POSIX class: Oracle understands it directly, while
# :data:`_MONITORING_SQL_PATTERN` derives the Python pattern from the very same
# string (Python's ``re`` has no POSIX classes), so the two cannot drift apart.
_ORACLE_MONITORING_SQL_REGEX = (
    r"FROM[[:space:]]+(GV[$]|V[$])"
    r"|FROM[[:space:]]+DBA_"
    r"|FROM[[:space:]]+USER_(SEGMENTS|TABLES|INDEXES|LOBS)"
    r"|WITH[[:space:]]+TABLE_STORAGE[[:space:]]+AS"
    r"|SELECT[[:space:]]+1[[:space:]]+FROM[[:space:]]+DUAL"
)
_MONITORING_SQL_PATTERN = re.compile(
    _ORACLE_MONITORING_SQL_REGEX.replace("[[:space:]]", r"\s"), re.IGNORECASE
)


def is_monitoring_sql(sql_text: str | None) -> bool:
    """True when ``sql_text`` is one of this service's own probe/monitoring SQL."""
    if not sql_text:
        return False
    return bool(_MONITORING_SQL_PATTERN.search(str(sql_text)))


_QUERY_TIMEOUT_SECONDS = 3.0
# USER_* dictionary views are cheap once ADB has compiled their plan, but the
# first execution on a cold dictionary cache measured ~9s in production; the
# shared 3s budget cancelled it and left the section degraded after restarts.
_TABLES_TIMEOUT_SECONDS = 20.0
# The merged performance statement measured 1.4s warm / 3.2s on the first
# (cold) execution in production, so the shared 3s budget is too tight.
_LOAD_TIMEOUT_SECONDS = 15.0
_CACHE_TTL_SECONDS = 15.0
# Session details are their own statement with their own short cache: the
# realtime panel refreshes faster than the 15s storage cache without paying a
# database round trip per request.
_SESSIONS_CACHE_TTL_SECONDS = 10.0
_SESSIONS_LIMIT = 50

# Operational tables that hold no application data; hidden from the table list
# and its totals so the view reflects the platform's own data only.
_EXCLUDED_TABLES = frozenset({"KEEPALIVE_HEARTBEAT"})

_TABLES_SQL = text(
    """
    WITH
    table_storage AS (
        SELECT t.table_name, SUM(s.bytes) AS table_bytes
        FROM user_tables t
        JOIN user_segments s ON s.segment_name = t.table_name
          AND s.segment_type IN ('TABLE', 'TABLE PARTITION', 'TABLE SUBPARTITION')
        GROUP BY t.table_name
    ),
    index_storage AS (
        SELECT i.table_name, SUM(s.bytes) AS index_bytes
        FROM user_indexes i
        JOIN user_segments s ON s.segment_name = i.index_name
          AND s.segment_type IN ('INDEX', 'INDEX PARTITION', 'INDEX SUBPARTITION')
        WHERE i.index_type <> 'LOB'
        GROUP BY i.table_name
    ),
    lob_storage AS (
        SELECT l.table_name, SUM(s.bytes) AS lob_bytes
        FROM user_lobs l
        JOIN user_segments s ON s.segment_name = l.segment_name
          AND s.segment_type IN ('LOBSEGMENT', 'LOB PARTITION', 'LOB SUBPARTITION')
        GROUP BY l.table_name
    ),
    lob_index_storage AS (
        SELECT l.table_name, SUM(s.bytes) AS lob_index_bytes
        FROM user_lobs l
        JOIN user_segments s ON s.segment_name = l.index_name
          AND s.segment_type IN ('LOBINDEX', 'INDEX', 'INDEX PARTITION', 'INDEX SUBPARTITION')
        GROUP BY l.table_name
    )
    SELECT
        t.table_name,
        t.tablespace_name,
        t.num_rows,
        t.last_analyzed,
        NVL(ts.table_bytes, 0) AS table_bytes,
        NVL(isg.index_bytes, 0) AS index_bytes,
        NVL(ls.lob_bytes, 0) AS lob_bytes,
        NVL(lis.lob_index_bytes, 0) AS lob_index_bytes
    FROM user_tables t
    LEFT JOIN table_storage ts ON ts.table_name = t.table_name
    LEFT JOIN index_storage isg ON isg.table_name = t.table_name
    LEFT JOIN lob_storage ls ON ls.table_name = t.table_name
    LEFT JOIN lob_index_storage lis ON lis.table_name = t.table_name
    ORDER BY t.table_name
    """
)

_TABLESPACES_SQL = text(
    """
    SELECT
        m.tablespace_name,
        m.used_space * t.block_size AS used_bytes,
        m.tablespace_size * t.block_size AS max_bytes,
        m.used_percent
    FROM dba_tablespace_usage_metrics m
    JOIN dba_tablespaces t ON t.tablespace_name = m.tablespace_name
    ORDER BY m.tablespace_name
    """
)

_FAST_SNAPSHOT_SQL = text(
    """
    WITH ash AS (
        SELECT COUNT(*) AS sample_rows,
               COUNT(DISTINCT sample_time) AS sample_seconds
        FROM v$active_session_history
        WHERE sample_time > (SELECT MAX(sample_time) FROM v$active_session_history)
                            - INTERVAL '5' MINUTE
    )
    SELECT
        (SELECT COUNT(*) FROM v$session) AS session_total,
        (SELECT COUNT(*) FROM v$session WHERE status = 'ACTIVE') AS session_active,
        (SELECT COUNT(*) FROM v$session WHERE status = 'INACTIVE') AS session_inactive,
        (SELECT value FROM v$sysstat WHERE name = 'execute count') AS stat_execute_count,
        (SELECT value FROM v$sysstat WHERE name = 'user commits') AS stat_user_commits,
        (SELECT value FROM v$sysstat WHERE name = 'CPU used by this session') AS stat_cpu_used_cs,
        (SELECT value FROM v$sysstat WHERE name = 'session logical reads') AS stat_logical_reads,
        (SELECT value FROM v$sysstat WHERE name = 'physical reads') AS stat_physical_reads,
        (SELECT value FROM v$sysstat WHERE name = 'parse count (total)') AS stat_parse_count,
        ash.sample_rows AS ash_sample_rows,
        ash.sample_seconds AS ash_sample_seconds
    FROM ash
    """
)
# One statement, one round trip: V$SYSSTAT replaced V$SYSMETRIC, which produces
# no samples on ADB (design §2.1). AAS comes from ASH, and the ASH window is
# anchored on ASH's own MAX(sample_time) — comparing against SYSTIMESTAMP
# returned 0 rows because of a timezone mismatch (design §4).
_FAST_SNAPSHOT_COUNTER_COLUMNS = {
    "execute_count": "stat_execute_count",
    "user_commits": "stat_user_commits",
    "cpu_used_cs": "stat_cpu_used_cs",
    "logical_reads": "stat_logical_reads",
    "physical_reads": "stat_physical_reads",
    "parse_count": "stat_parse_count",
}
_FAST_SNAPSHOT_RATE_METRICS = (
    ("execute_count", "Executions Per Sec", "Executions/sec"),
    ("user_commits", "Transactions Per Sec", "Commits/sec"),
    ("cpu_used_cs", "CPU Used Per Sec", "Centiseconds/sec"),
    ("logical_reads", "Logical Reads Per Sec", "Blocks/sec"),
    ("physical_reads", "Physical Reads Per Sec", "Blocks/sec"),
    ("parse_count", "Parse Count Per Sec", "Parses/sec"),
)

# Session details; the analytic functions count the whole result set *before*
# the row limit, so one statement yields both the breakdown and the details.
_SESSIONS_SQL = text(
    f"""
    SELECT * FROM (
        SELECT
            sid, username, program, machine, status, event, wait_class,
            last_call_et, sql_id, logon_time,
            COUNT(*) OVER () AS total_count,
            COUNT(CASE WHEN status = 'ACTIVE' THEN 1 END) OVER () AS active_count
        FROM v$session
        ORDER BY CASE WHEN status = 'ACTIVE' THEN 0 ELSE 1 END, last_call_et DESC
    ) WHERE ROWNUM <= {_SESSIONS_LIMIT}
    """
)

_SLOW_WAITS_SQL = text(
    """
    SELECT NVL(wait_class, 'None') AS wait_class, COUNT(*) AS sessions
    FROM v$session
    GROUP BY NVL(wait_class, 'None')
    ORDER BY sessions DESC
    """
)

# Top SQL must be filtered twice over:
#   1. by parsing schema: unfiltered, Oracle's own autotask statements
#      (dbms_autotask_prvt, dbms_auto_index_internal) dominate the list.
#      Measured: 3391 -> 288 rows, all real application SQL (design §5.3).
#   2. by own-SQL fingerprint (see _ORACLE_MONITORING_SQL_REGEX): the leaderboard
#      must not be won by this service's own monitoring/probe statements.
# ``sql_text IS NULL`` rows are kept — an unreadable text is not evidence of
# monitoring SQL, and Oracle's NULL comparison would otherwise drop them.
#
# The pattern is pre-bound (``bindparams``) instead of inlined: ``text()`` reads
# ``:space`` inside the POSIX class ``[[:space:]]`` as a *bind parameter* and
# would corrupt the regex. Pre-binding also keeps the statement parameterless for
# the shared ``_component``/``_execute`` path.
_SLOW_TOP_SQL = text(
    """
    SELECT * FROM (
        SELECT
            sql_id,
            executions,
            elapsed_time,
            cpu_time,
            buffer_gets,
            disk_reads,
            SUBSTR(sql_text, 1, 200) AS sql_text
        FROM v$sqlarea
        WHERE parsing_schema_name = SYS_CONTEXT('USERENV', 'CURRENT_SCHEMA')
          AND (
            sql_text IS NULL
            OR NOT REGEXP_LIKE(UPPER(sql_text), :monitoring_sql_regex)
          )
        ORDER BY elapsed_time DESC
    ) WHERE ROWNUM <= 20
    """
).bindparams(monitoring_sql_regex=_ORACLE_MONITORING_SQL_REGEX)


@dataclass
class _CacheEntry:
    created_at: float
    data: dict[str, Any]


_cache: _CacheEntry | None = None
_cache_lock = asyncio.Lock()

_sessions_cache: _CacheEntry | None = None
_sessions_lock = asyncio.Lock()

# Replaced in tests to make rate assertions deterministic.
_monotonic = time.monotonic

# Previous cumulative V$SYSSTAT readings for the live load section. The perf
# sampler keeps its own state on its own cadence (services/perf_sampler.py).
_load_state_lock = threading.Lock()
_previous_load_counters: tuple[float, dict[str, int | float | None]] | None = None


def _pool_wait_count() -> int:
    """Blocking pool checkouts that waited (see ``db.oracle.session``).

    Read lazily: this module must stay importable without an engine, and the
    counter only exists once a sessionmaker has been built.
    """
    try:
        from db.oracle.session import pool_wait_count

        return int(pool_wait_count())
    except Exception as exc:  # pragma: no cover - no engine in tooling/tests
        _logger.debug("pool wait counter unavailable: %s", exc)
        return 0


def _number(value: Any, *, integer: bool = False) -> int | float | None:
    if value is None:
        return None
    try:
        # Preserve exact byte counters even above IEEE-754's integer range.
        if integer:
            return int(value)
        converted = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return converted if math.isfinite(converted) else None


def _timestamp(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _utc_timestamp(value: Any) -> str | None:
    """ISO-8601 instant for Oracle timestamps.

    Oracle ``TIMESTAMP``/``DATE`` columns carry no offset; the ADB session time
    zone is UTC (verified in production), so a naive value is labelled UTC
    instead of being left ambiguous — the browser would otherwise read it as
    local time. Storage fields keep using :func:`_timestamp` (frozen shape).
    """
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    return _timestamp(value)


def _row_value(row: Any, key: str) -> Any:
    return row._mapping[key]


async def _execute(
    session: AsyncSession, statement, timeout: float = _QUERY_TIMEOUT_SECONDS
) -> list[Any]:
    result = await asyncio.wait_for(session.execute(statement), timeout=timeout)
    return list(result.fetchall())


async def _component(
    session: AsyncSession,
    name: str,
    statement,
    timeout: float = _QUERY_TIMEOUT_SECONDS,
) -> tuple[list[Any] | None, dict[str, Any] | None]:
    try:
        return await _execute(session, statement, timeout), None
    except Exception as exc:  # dictionary/performance privileges vary by ADB user
        code = None
        if isinstance(exc, DBAPIError) and exc.orig.args:
            code = getattr(exc.orig.args[0], "code", None)
        if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
            reason = "query_timeout"
        elif code in (942, 1031):
            reason = "insufficient_privileges"
        else:
            # Connectivity and programming errors must surface as HTTP 503,
            # rather than looking like a healthy database with missing views.
            raise
        _logger.warning("DB monitor component %s unavailable (%s)", name, reason)
        await asyncio.wait_for(session.rollback(), timeout=_QUERY_TIMEOUT_SECONDS)
        # Cancellation may invalidate a connection. Verify recovery before
        # reusing the session for the next independent observation.
        await _execute(session, text("SELECT 1 FROM DUAL"))
        return None, {
            "available": False,
            "reason": reason,
            "message": "当前数据库账号无法读取该监控项",
        }


def _pool_status() -> dict[str, Any]:
    """Report pressure on this API process's pool, not the whole database."""
    try:
        from db.oracle.engine import get_engine

        pool = get_engine().pool
        size = pool.size()
        checked_out = pool.checkedout()
        # SQLAlchemy's internal ``_overflow`` counter goes negative once an
        # overflowed connection is returned to the pool (observed -4 in
        # production); a negative overflow is physically meaningless.
        overflow = max(0, pool.overflow())
        capacity = size + max(pool._max_overflow, 0)  # SQLAlchemy public pool API lacks this value.
        return {
            "available": True,
            "scope": "this_api_process",
            "size": size,
            "checked_out": checked_out,
            "overflow": overflow,
            "capacity": capacity,
            "utilization_percent": round(checked_out / capacity * 100, 2)
            if capacity
            else None,
            # Real count of blocking checkouts (db.oracle.session probe); 0 means
            # "nobody had to wait", not "not measured".
            "waited": _pool_wait_count(),
        }
    except Exception as exc:
        _logger.debug("DB monitor connection-pool metrics unavailable: %s", exc)
        return {
            "available": False,
            "scope": "this_api_process",
            "reason": "pool_metrics_unavailable",
        }


def pool_metrics() -> dict[str, Any]:
    """The realtime endpoint's flat ``pool`` section (design §5.1).

    Same pool as :func:`_pool_status` (which keeps the legacy key names used by
    ``load.connection_pool``); a failure degrades to ``{"available": false}``
    instead of taking the whole panel down.
    """
    try:
        from db.oracle.engine import get_engine

        pool = get_engine().pool
        size = int(pool.size())
        checked_out = int(pool.checkedout())
        overflow = max(0, int(pool.overflow()))
        capacity = size + max(int(pool._max_overflow), 0)
        return {
            "size": size,
            "checkedout": checked_out,
            "overflow": overflow,
            "capacity": capacity,
            "utilization_percent": round(checked_out / capacity * 100, 2)
            if capacity
            else None,
            "waited": _pool_wait_count(),
        }
    except Exception as exc:
        _logger.debug("DB monitor connection-pool metrics unavailable: %s", exc)
        return {"available": False, "reason": "pool_metrics_unavailable"}


def _mapping_get(row: Any, key: str) -> Any:
    """Tolerant column read: a missing column degrades instead of raising."""
    mapping = row._mapping
    return mapping.get(key) if hasattr(mapping, "get") else None


def _fast_snapshot_values(rows: list[Any]) -> dict[str, Any] | None:
    """Parse the merged V$SYSSTAT + ASH statement; ``None`` for other shapes."""
    if not rows:
        return None
    mapping = rows[0]._mapping
    if "session_total" not in mapping:
        return None

    total = _number(mapping.get("session_total"), integer=True)
    active = _number(mapping.get("session_active"), integer=True)
    inactive = _number(mapping.get("session_inactive"), integer=True)
    if inactive is None and total is not None and active is not None:
        inactive = total - active

    sample_rows = _number(mapping.get("ash_sample_rows"), integer=True)
    sample_seconds = _number(mapping.get("ash_sample_seconds"), integer=True)
    if sample_rows is None:
        aas: float | None = None
    elif sample_seconds:
        # ASH writes one row per active session per sample; dividing by the number
        # of distinct sample instants is independent of the sampling frequency.
        aas = sample_rows / sample_seconds
    else:
        aas = 0.0  # no ASH rows in the window == no activity recorded

    counters = {
        name: _number(mapping.get(column), integer=True)
        for name, column in _FAST_SNAPSHOT_COUNTER_COLUMNS.items()
    }
    return {
        "sessions": {"total": total, "active": active, "inactive": inactive},
        "aas": aas,
        "counters": counters,
        "ash_seconds": sample_seconds,
    }


def _fast_load_metrics(rows: list[Any]) -> list[dict[str, Any]]:
    """Build the frozen ``load.metrics[]`` items from the merged statement.

    Element field names are unchanged (``name/value/unit/begin_time/end_time/
    interval_centiseconds/group_id``); only the source changed. Gauges are
    emitted on the first tick, rates only once a previous reading exists — a
    restart must not produce a zero-rate artefact.
    """
    global _previous_load_counters

    values = _fast_snapshot_values(rows)
    if values is None:
        return []

    now = datetime.now(timezone.utc)
    now_monotonic = _monotonic()
    with _load_state_lock:
        previous = _previous_load_counters
        _previous_load_counters = (now_monotonic, dict(values["counters"]))
    elapsed = None if previous is None else now_monotonic - previous[0]

    items: list[dict[str, Any]] = []

    def add(name: str, value: Any, unit: str, interval_centiseconds: int,
            begin: Any = None, end: Any = None) -> None:
        items.append(
            {
                "name": name,
                "value": value,
                "unit": unit,
                "begin_time": _timestamp(begin if begin is not None else now),
                "end_time": _timestamp(end if end is not None else now),
                "interval_centiseconds": interval_centiseconds,
                "group_id": 2,
            }
        )

    if values["sessions"]["total"] is not None:
        add("Session Count", values["sessions"]["total"], "Sessions", 0)
    if values["aas"] is not None:
        add("Average Active Sessions", round(values["aas"], 4), "Active Sessions", 0)

    if previous is not None and elapsed and elapsed > 0:
        interval_centiseconds = int(round(elapsed * 100))
        for name, metric_name, unit in _FAST_SNAPSHOT_RATE_METRICS:
            current = values["counters"].get(name)
            old = previous[1].get(name)
            if current is None or old is None:
                continue
            delta = current - old
            if delta < 0:  # instance restarted between readings
                continue
            add(metric_name, round(delta / elapsed, 4), unit, interval_centiseconds)
    return items


def _legacy_load_metrics(rows: list[Any]) -> list[dict[str, Any]]:
    """V$SYSMETRIC-shaped rows.

    Retained as a tolerant reader (a stale cache or a mocked session can still
    hand this module the previous row shape); production rows always come from
    the merged statement, which has no ``metric_name`` column.
    """
    items: list[dict[str, Any]] = []
    for row in rows:
        items.append(
            {
                "name": _row_value(row, "metric_name"),
                "value": _number(_row_value(row, "value")),
                "unit": _row_value(row, "metric_unit"),
                "begin_time": _timestamp(_row_value(row, "begin_time")),
                "end_time": _timestamp(_row_value(row, "end_time")),
                "interval_centiseconds": _number(
                    _row_value(row, "intsize_csec"), integer=True
                ),
                "group_id": _number(_row_value(row, "group_id"), integer=True),
            }
        )
    return items


async def _collect(session: AsyncSession) -> dict[str, Any]:
    await _execute(session, text("SELECT 1 FROM DUAL"))
    tables, tables_error = await _component(
        session, "tables", _TABLES_SQL, _TABLES_TIMEOUT_SECONDS
    )
    tablespaces, tablespaces_error = await _component(
        session, "tablespaces", _TABLESPACES_SQL
    )
    metrics, metrics_error = await _component(
        session, "load", _FAST_SNAPSHOT_SQL, _LOAD_TIMEOUT_SECONDS
    )

    application_tablespaces: set[str] = set()
    excluded_seen: set[str] = set()
    table_items: list[dict[str, Any]] = []
    if tables is not None:
        for row in tables:
            tablespace_name = _row_value(row, "tablespace_name")
            if tablespace_name:
                application_tablespaces.add(tablespace_name)
            name = _row_value(row, "table_name")
            if name in _EXCLUDED_TABLES:
                excluded_seen.add(name)
                continue
            table_bytes = _number(_row_value(row, "table_bytes"), integer=True) or 0
            index_bytes = _number(_row_value(row, "index_bytes"), integer=True) or 0
            lob_bytes = _number(_row_value(row, "lob_bytes"), integer=True) or 0
            lob_index_bytes = _number(_row_value(row, "lob_index_bytes"), integer=True) or 0
            table_items.append(
                {
                    "name": name,
                    "estimated_rows": _number(
                        _row_value(row, "num_rows"), integer=True
                    ),
                    "last_analyzed": _timestamp(_row_value(row, "last_analyzed")),
                    "table_bytes": table_bytes,
                    "index_bytes": index_bytes,
                    "lob_bytes": lob_bytes,
                    "lob_index_bytes": lob_index_bytes,
                    "total_bytes": table_bytes + index_bytes + lob_bytes + lob_index_bytes,
                }
            )

    tablespace_items: list[dict[str, Any]] = []
    if tablespaces is not None:
        for row in tablespaces:
            tablespace_items.append(
                {
                    "name": _row_value(row, "tablespace_name"),
                    "used_bytes": _number(_row_value(row, "used_bytes"), integer=True),
                    "max_bytes": _number(_row_value(row, "max_bytes"), integer=True),
                    "used_percent": _number(_row_value(row, "used_percent")),
                    # Tablespaces that actually host the platform's tables, so
                    # callers can show our data without Oracle's SYSTEM/SYSAUX/
                    # TEMP/UNDO internal tablespaces.
                    "is_application": _row_value(row, "tablespace_name")
                    in application_tablespaces,
                }
            )

    load_items: list[dict[str, Any]] = []
    if metrics is not None:
        if metrics and "metric_name" in metrics[0]._mapping:
            load_items = _legacy_load_metrics(metrics)
        else:
            load_items = _fast_load_metrics(metrics)

    storage_available = tables is not None or tablespaces is not None
    load_available = bool(load_items)
    if metrics == [] or (metrics is not None and not load_items):
        metrics_error = {"available": False, "reason": "no_samples"}
    warnings = [
        "estimated_rows 来源于 USER_TABLES.NUM_ROWS，需结合 last_analyzed 判断新鲜度；本接口不会执行逐表 COUNT(*)。"
    ]
    if tablespaces is not None:
        warnings.append(
            "tablespace max_bytes 是 Oracle 根据数据文件自动扩展、底层空间及 PDB 限额计算的可用上限，不等于 OCI 套餐总容量。"
        )
    if load_items:
        warnings.append(
            "DB 负载取自 V$SYSSTAT 累计值差值（速率）与 V$ACTIVE_SESSION_HISTORY 的 AAS；connection_pool 仅代表当前 API 进程。"
        )
    elif metrics_error is not None:
        warnings.append(
            "负载指标取自 V$SYSSTAT 与 V$ACTIVE_SESSION_HISTORY；该项不可读或无采样时降级为空，不影响存储数据的可用性。"
        )
    if excluded_seen:
        warnings.append(
            "运维表（" + "、".join(sorted(excluded_seen)) + "）不计入表清单与合计。"
        )

    return {
        "storage": {
            "available": storage_available,
            "tables": {
                "available": tables is not None,
                "count": len(table_items) if tables is not None else None,
                "total_bytes": sum(item["total_bytes"] for item in table_items)
                if tables is not None
                else None,
                "items": table_items,
                **(tables_error or {}),
            },
            "tablespaces": {
                "available": tablespaces is not None,
                "items": tablespace_items,
                **(tablespaces_error or {}),
            },
        },
        "load": {
            "available": load_available,
            "source": "V$SYSSTAT+V$ACTIVE_SESSION_HISTORY",
            "metrics": load_items,
            "assessment": "insufficient_capacity_context",
            **(metrics_error or {}),
        },
        "warnings": warnings,
    }


def _response(entry: _CacheEntry, *, cached: bool) -> dict[str, Any]:
    data = deepcopy(entry.data)
    data["cache"] = {
        "cached": cached,
        "age_seconds": round(time.monotonic() - entry.created_at, 3),
        "ttl_seconds": _CACHE_TTL_SECONDS,
    }
    data["load"]["connection_pool"] = {
        **_pool_status(), "observed_at": datetime.now(timezone.utc).isoformat()
    }
    return data


async def get_database_status(session: AsyncSession) -> dict[str, Any]:
    """Return a short-lived, read-only database observation."""
    global _cache
    now = time.monotonic()
    if _cache is not None and now - _cache.created_at < _CACHE_TTL_SECONDS:
        return _response(_cache, cached=True)

    async with _cache_lock:
        now = time.monotonic()
        if _cache is not None and now - _cache.created_at < _CACHE_TTL_SECONDS:
            return _response(_cache, cached=True)

        data = await _collect(session)
        data["observed_at"] = datetime.now(timezone.utc).isoformat()
        # Storage drives the overall verdict: a load view that yields no samples
        # (V$SYSSTAT/ASH can be unreadable or empty) must not keep the whole page
        # reading "degraded" while capacity and tables are fully readable.
        # The load section still reports its own availability and reason.
        data["status"] = (
            "ok"
            if (data["storage"]["tables"]["available"]
                and data["storage"]["tablespaces"]["available"])
            else "degraded"
        )
        _cache = _CacheEntry(time.monotonic(), data)
        return _response(_cache, cached=False)


async def collect_fast(session: AsyncSession) -> dict[str, Any]:
    """The merged fast tier, raw values only (used by the perf sampler).

    Returns the *cumulative* ``v$sysstat`` counters so each caller can keep its
    own previous reading (the realtime section and the sampler tick at different
    cadences, so a shared delta state would corrupt both).
    """
    rows, error = await _component(
        session, "load", _FAST_SNAPSHOT_SQL, _LOAD_TIMEOUT_SECONDS
    )
    if error is not None:
        return {"available": False, **error}
    values = _fast_snapshot_values(rows)
    if values is None:
        return {"available": False, "reason": "no_samples"}
    return {
        "available": True,
        "sessions": values["sessions"],
        "aas": values["aas"],
        "counters": {k: v for k, v in values["counters"].items() if v is not None},
        "ash_seconds": values["ash_seconds"],
    }


def _top_sql_items(rows: list[Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for row in rows:
        mapping = row._mapping
        sql_text = mapping.get("sql_text")
        # Defence in depth: the statement already excludes own monitoring SQL,
        # but rows stored by an older build (or a partially readable text) must
        # not resurface in the leaderboard.
        if is_monitoring_sql(sql_text):
            continue
        elapsed_us = _number(mapping.get("elapsed_time"), integer=True)
        cpu_us = _number(mapping.get("cpu_time"), integer=True)
        executions = _number(mapping.get("executions"), integer=True)
        elapsed_ms = None if elapsed_us is None else round(elapsed_us / 1000, 1)
        avg_elapsed_ms = (
            None
            if elapsed_us is None or not executions
            else round(elapsed_us / executions / 1000, 1)
        )
        items.append(
            {
                "sql_id": mapping.get("sql_id"),
                "executions": executions,
                "elapsed_ms": elapsed_ms,
                "cpu_ms": None if cpu_us is None else round(cpu_us / 1000, 1),
                "avg_elapsed_ms": avg_elapsed_ms,
                "buffer_gets": _number(mapping.get("buffer_gets"), integer=True),
                "disk_reads": _number(mapping.get("disk_reads"), integer=True),
                "sql_text": sql_text,
            }
        )
    return items


async def collect_slow(session: AsyncSession) -> dict[str, Any]:
    """The slow tier: waits by class + schema-filtered Top SQL + tablespaces.

    Each part degrades on its own (privileges / timeout) so one unreadable view
    never hides the other two.
    """
    waits, waits_error = await _component(session, "waits", _SLOW_WAITS_SQL)
    top, top_error = await _component(session, "top_sql", _SLOW_TOP_SQL)
    tablespaces, tablespaces_error = await _component(
        session, "tablespaces", _TABLESPACES_SQL
    )

    errors = {
        name: error
        for name, error in (
            ("waits", waits_error),
            ("top_sql", top_error),
            ("tablespaces", tablespaces_error),
        )
        if error is not None
    }
    wait_items = [
        {
            "wait_class": _row_value(row, "wait_class"),
            "sessions": _number(_row_value(row, "sessions"), integer=True),
        }
        for row in (waits or [])
    ]
    tablespace_items = [
        {
            "name": _row_value(row, "tablespace_name"),
            "used_pct": _number(_row_value(row, "used_percent")),
        }
        for row in (tablespaces or [])
    ]
    return {
        "available": any(part is not None for part in (waits, top, tablespaces)),
        "waits": wait_items,
        "top_sql": _top_sql_items(top or []),
        "tablespaces": tablespace_items,
        "errors": errors,
    }


def _sessions_payload(rows: list[Any]) -> dict[str, Any]:
    if not rows:
        return {"total": 0, "active": 0, "inactive": 0, "items": []}
    total = _number(_mapping_get(rows[0], "total_count"), integer=True) or 0
    active = _number(_mapping_get(rows[0], "active_count"), integer=True) or 0
    items = [
        {
            "sid": _number(_row_value(row, "sid"), integer=True),
            "username": _mapping_get(row, "username"),
            "program": _mapping_get(row, "program"),
            "machine": _mapping_get(row, "machine"),
            "status": _mapping_get(row, "status"),
            "event": _mapping_get(row, "event"),
            "wait_class": _mapping_get(row, "wait_class"),
            "last_call_et": _number(_mapping_get(row, "last_call_et"), integer=True),
            "sql_id": _mapping_get(row, "sql_id"),
            "logon_time": _utc_timestamp(_mapping_get(row, "logon_time")),
        }
        for row in rows
    ]
    return {
        "total": total,
        "active": active,
        "inactive": max(0, total - active),
        "items": items,
    }


async def collect_sessions(session: AsyncSession) -> dict[str, Any]:
    """Session breakdown + details, behind its own short cache (§5.1)."""
    global _sessions_cache
    now = time.monotonic()
    if (
        _sessions_cache is not None
        and now - _sessions_cache.created_at < _SESSIONS_CACHE_TTL_SECONDS
    ):
        return deepcopy(_sessions_cache.data)

    async with _sessions_lock:
        now = time.monotonic()
        if (
            _sessions_cache is not None
            and now - _sessions_cache.created_at < _SESSIONS_CACHE_TTL_SECONDS
        ):
            return deepcopy(_sessions_cache.data)

        rows, error = await _component(session, "sessions", _SESSIONS_SQL)
        data = dict(error) if error is not None else _sessions_payload(rows or [])
        _sessions_cache = _CacheEntry(time.monotonic(), data)
        return deepcopy(data)


def get_local_metrics() -> dict[str, Any]:
    """Host-only snapshot for the realtime panel's sibling endpoint (R6).

    Every section here is produced **inside this process**: the ``/proc``
    reading, the SQLAlchemy connection pool counters and the in-process API
    window.  The function takes no session and calls none of the Oracle
    collectors above, so it cannot cost a database round trip — the caller (and
    the test suite) can rely on that by construction, not by measuring.

    ``host``/``pool``/``api`` are built by the very same builders
    :func:`get_realtime_metrics` uses, which keeps the two endpoints' shapes
    byte-for-byte identical for the frontend renderer.
    """
    return {
        "host": collect_host_metrics()["host"],
        "pool": pool_metrics(),
        "api": api_metrics.snapshot(),
    }


async def get_realtime_metrics(session: AsyncSession) -> dict[str, Any]:
    """Everything the realtime panel needs (design §5.1).

    ``storage``/``load``/``warnings``/``cache`` come from the shared 15s
    observation; ``host`` is a live ``/proc`` read (zero DB cost), ``api`` is the
    in-process endpoint window and ``sessions`` has its own 10s cache.
    """
    data = await get_database_status(session)
    data["pool"] = pool_metrics()
    data["host"] = collect_host_metrics()["host"]
    data["api"] = api_metrics.snapshot()
    data["sessions"] = await collect_sessions(session)
    return data


def clear_database_status_cache() -> None:
    """Test/admin hook; does not touch the database."""
    global _cache, _sessions_cache, _previous_load_counters
    _cache = None
    _sessions_cache = None
    with _load_state_lock:
        _previous_load_counters = None


__all__ = [
    "clear_database_status_cache",
    "collect_fast",
    "collect_sessions",
    "collect_slow",
    "get_database_status",
    "get_local_metrics",
    "get_realtime_metrics",
    "pool_metrics",
]
