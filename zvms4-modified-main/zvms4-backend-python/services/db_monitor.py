"""Read-only Oracle ADB health, storage and workload observations.

The service deliberately uses Oracle's dictionary views rather than counting
business rows.  ``USER_TABLES.NUM_ROWS`` is an optimizer estimate and is
returned together with ``LAST_ANALYZED`` so callers can judge its freshness.
Each optional view is isolated: a database account without DBA or performance
view privileges receives a degraded component instead of a misleading zero or
a whole-request 500.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

_logger = logging.getLogger(__name__)

_QUERY_TIMEOUT_SECONDS = 3.0
_CACHE_TTL_SECONDS = 15.0

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

_LOAD_SQL = text(
    """
    SELECT metric_name, value, metric_unit, begin_time, end_time, intsize_csec, group_id
    FROM v$sysmetric
    WHERE group_id = 2
      AND metric_name IN (
        'Database CPU Time Ratio',
        'Average Active Sessions',
        'Executions Per Sec',
        'Transactions Per Sec',
        'User Calls Per Sec',
        'Session Count',
        'Physical Reads Per Sec',
        'Physical Writes Per Sec',
        'Logical Reads Per Sec',
        'SQL Service Response Time'
      )
    ORDER BY metric_name
    """
)


@dataclass
class _CacheEntry:
    created_at: float
    data: dict[str, Any]


_cache: _CacheEntry | None = None
_cache_lock = asyncio.Lock()


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


def _row_value(row: Any, key: str) -> Any:
    return row._mapping[key]


async def _execute(session: AsyncSession, statement) -> list[Any]:
    result = await asyncio.wait_for(
        session.execute(statement), timeout=_QUERY_TIMEOUT_SECONDS
    )
    return list(result.fetchall())


async def _component(
    session: AsyncSession, name: str, statement
) -> tuple[list[Any] | None, dict[str, Any] | None]:
    try:
        return await _execute(session, statement), None
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
        overflow = pool.overflow()
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
        }
    except Exception as exc:
        _logger.debug("DB monitor connection-pool metrics unavailable: %s", exc)
        return {
            "available": False,
            "scope": "this_api_process",
            "reason": "pool_metrics_unavailable",
        }


async def _collect(session: AsyncSession) -> dict[str, Any]:
    await _execute(session, text("SELECT 1 FROM DUAL"))
    tables, tables_error = await _component(session, "tables", _TABLES_SQL)
    tablespaces, tablespaces_error = await _component(
        session, "tablespaces", _TABLESPACES_SQL
    )
    metrics, metrics_error = await _component(session, "load", _LOAD_SQL)

    table_items: list[dict[str, Any]] = []
    if tables is not None:
        for row in tables:
            table_bytes = _number(_row_value(row, "table_bytes"), integer=True) or 0
            index_bytes = _number(_row_value(row, "index_bytes"), integer=True) or 0
            lob_bytes = _number(_row_value(row, "lob_bytes"), integer=True) or 0
            lob_index_bytes = _number(_row_value(row, "lob_index_bytes"), integer=True) or 0
            table_items.append(
                {
                    "name": _row_value(row, "table_name"),
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
                }
            )

    load_items: list[dict[str, Any]] = []
    if metrics is not None:
        for row in metrics:
            load_items.append(
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

    storage_available = tables is not None or tablespaces is not None
    load_available = bool(load_items)
    if metrics == []:
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
            "V$SYSMETRIC 负载为当前连接可见的实例/容器最近约 60 秒采样；connection_pool 仅代表当前 API 进程。"
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
            "source": "V$SYSMETRIC",
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
        data["status"] = (
            "ok"
            if (data["storage"]["tables"]["available"]
                and data["storage"]["tablespaces"]["available"]
                and data["load"]["available"])
            else "degraded"
        )
        _cache = _CacheEntry(time.monotonic(), data)
        return _response(_cache, cached=False)


def clear_database_status_cache() -> None:
    """Test/admin hook; does not touch the database."""
    global _cache
    _cache = None


__all__ = ["clear_database_status_cache", "get_database_status"]
