"""Performance sample storage and scheduling (design §3 / §4).

Samples live in a local SQLite database (stdlib ``sqlite3``) next to the backend
root, deliberately decoupled from Oracle ADB: an ADB write costs ~0.5-0.95s per
round trip, which would double the sampling budget, and the Oracle DDL in
``db/oracle/ddl/`` needs a human to apply it.  The layout mirrors
:mod:`db.system_state` — WAL, short-lived connections, lazy ``CREATE TABLE IF NOT
EXISTS`` — and accepts a ``ZVMS_PERF_DB`` override (``PERF_DB_PATH`` /
``_db_path()``).

Three tiers, all configurable by environment variable:

* ``PERF_HOST_INTERVAL_SECONDS`` (30) — ``/proc`` gauges, pool gauges and the
  in-process endpoint window.  **Zero database round trips.**
* ``PERF_ORACLE_INTERVAL_SECONDS`` (120) — one merged Oracle statement
  (:func:`services.db_monitor.collect_fast`).
* ``PERF_SLOW_INTERVAL_SECONDS`` (600) — three slow statements (waits by class,
  schema-filtered Top SQL, tablespace usage) via
  :func:`services.db_monitor.collect_slow`.

Rate series (``*_per_sec``) are derived from the previous cumulative reading kept
in this module; the first tick after a restart omits them instead of writing a
fabricated zero.

**Single-worker premise** (design §2.3 / §11): production runs one ``uvicorn``
worker, so the previous-reading state, the endpoint window and "the scheduler
runs once" all hold process-locally. Running several workers would split each
series across processes and double the sampling cost — that assumption is load
bearing, not an optimisation.
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
import sqlite3
import threading
import time
from contextlib import closing
from typing import Any, Iterable, Mapping

from services import db_monitor
from services.api_metrics import api_metrics
from services.host_metrics import collect_host_metrics
from util.serialization import iso_epoch_utc

_logger = logging.getLogger(__name__)

# 后端根目录：本文件位于 services/ 下，向上两级即后端根（同 db/system_state.py）。
_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PERF_DB_PATH = os.environ.get("ZVMS_PERF_DB") or os.path.join(
    _BACKEND_ROOT, "perf_samples.db"
)

_CREATE_TABLES = (
    """
    CREATE TABLE IF NOT EXISTS perf_samples (
      ts INTEGER NOT NULL,
      series TEXT NOT NULL,
      value REAL
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_perf_samples ON perf_samples(series, ts)",
    """
    CREATE TABLE IF NOT EXISTS perf_top_sql (
      ts INTEGER, sql_id TEXT, executions INTEGER, elapsed_ms REAL, cpu_ms REAL,
      avg_elapsed_ms REAL, buffer_gets INTEGER, disk_reads INTEGER, sql_text TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_perf_top_sql_ts ON perf_top_sql(ts)",
    """
    CREATE TABLE IF NOT EXISTS perf_waits (
      ts INTEGER, wait_class TEXT, sessions INTEGER
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS perf_endpoints (
      ts INTEGER, method TEXT, route TEXT, count INTEGER, err_count INTEGER,
      avg_ms REAL, p95_ms REAL, max_ms REAL
    )
    """,
)

_TABLES = ("perf_samples", "perf_top_sql", "perf_waits", "perf_endpoints")

# --------------------------------------------------------------------------- #
# configuration (read per call so tests can monkeypatch the environment)
# --------------------------------------------------------------------------- #

DEFAULT_HOST_INTERVAL_SECONDS = 30.0
DEFAULT_ORACLE_INTERVAL_SECONDS = 120.0
DEFAULT_SLOW_INTERVAL_SECONDS = 600.0
DEFAULT_RETENTION_DAYS = 14


def _positive_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    try:
        value = float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    if not math.isfinite(value) or value <= 0:
        return default
    return value


def _positive_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    try:
        value = int(str(raw))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def host_interval_seconds() -> float:
    return _positive_float("PERF_HOST_INTERVAL_SECONDS", DEFAULT_HOST_INTERVAL_SECONDS)


def oracle_interval_seconds() -> float:
    return _positive_float("PERF_ORACLE_INTERVAL_SECONDS", DEFAULT_ORACLE_INTERVAL_SECONDS)


def slow_interval_seconds() -> float:
    return _positive_float("PERF_SLOW_INTERVAL_SECONDS", DEFAULT_SLOW_INTERVAL_SECONDS)


def retention_days() -> int:
    return _positive_int("PERF_RETENTION_DAYS", DEFAULT_RETENTION_DAYS)


def _db_path() -> str:
    return os.environ.get("ZVMS_PERF_DB") or PERF_DB_PATH


# --------------------------------------------------------------------------- #
# sqlite plumbing
# --------------------------------------------------------------------------- #

_init_lock = threading.Lock()
_initialized_paths: set[str] = set()


def _connect() -> sqlite3.Connection:
    """New short-lived connection; never a global singleton (thread safety)."""
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=3000")
    return conn


def init_db() -> None:
    """Lazy, idempotent table creation (no startup hook required)."""
    path = _db_path()
    if path in _initialized_paths:
        return
    with _init_lock:
        if path in _initialized_paths:
            return
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with closing(_connect()) as conn, conn:
            for statement in _CREATE_TABLES:
                conn.execute(statement)
        _initialized_paths.add(path)


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def record_series(ts: int, series: Mapping[str, Any]) -> int:
    """Append one sample per series key; non-finite values are dropped, not faked."""
    rows = [
        (int(ts), str(key), number)
        for key, value in series.items()
        if (number := _finite(value)) is not None
    ]
    if not rows:
        return 0
    init_db()
    with closing(_connect()) as conn, conn:
        conn.executemany("INSERT INTO perf_samples (ts, series, value) VALUES (?, ?, ?)", rows)
    return len(rows)


def record_endpoints(ts: int, items: Iterable[Mapping[str, Any]]) -> int:
    rows = [
        (
            int(ts),
            str(item.get("method") or ""),
            str(item.get("route") or ""),
            int(item.get("count") or 0),
            int(item.get("err_count") or 0),
            _finite(item.get("avg_ms")),
            _finite(item.get("p95_ms")),
            _finite(item.get("max_ms")),
        )
        for item in items
        if item.get("route")
    ]
    if not rows:
        return 0
    init_db()
    with closing(_connect()) as conn, conn:
        conn.executemany(
            "INSERT INTO perf_endpoints "
            "(ts, method, route, count, err_count, avg_ms, p95_ms, max_ms) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
    return len(rows)


def record_top_sql(ts: int, items: Iterable[Mapping[str, Any]]) -> int:
    rows = [
        (
            int(ts),
            item.get("sql_id"),
            item.get("executions"),
            _finite(item.get("elapsed_ms")),
            _finite(item.get("cpu_ms")),
            _finite(item.get("avg_elapsed_ms")),
            item.get("buffer_gets"),
            item.get("disk_reads"),
            item.get("sql_text"),
        )
        for item in items
        if item.get("sql_id")
    ]
    if not rows:
        return 0
    init_db()
    with closing(_connect()) as conn, conn:
        conn.executemany(
            "INSERT INTO perf_top_sql "
            "(ts, sql_id, executions, elapsed_ms, cpu_ms, avg_elapsed_ms, "
            " buffer_gets, disk_reads, sql_text) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
    return len(rows)


def record_waits(ts: int, items: Iterable[Mapping[str, Any]]) -> int:
    rows = [
        (int(ts), str(item.get("wait_class") or ""), int(item.get("sessions") or 0))
        for item in items
        if item.get("wait_class")
    ]
    if not rows:
        return 0
    init_db()
    with closing(_connect()) as conn, conn:
        conn.executemany(
            "INSERT INTO perf_waits (ts, wait_class, sessions) VALUES (?, ?, ?)", rows
        )
    return len(rows)


async def record_series_async(ts: int, series: Mapping[str, Any]) -> int:
    """``record_series`` 的 async 版本（sqlite 写入在工作线程）。"""
    return await asyncio.to_thread(record_series, ts, series)


async def record_endpoints_async(ts: int, items: Iterable[Mapping[str, Any]]) -> int:
    return await asyncio.to_thread(record_endpoints, ts, list(items))


async def record_top_sql_async(ts: int, items: Iterable[Mapping[str, Any]]) -> int:
    return await asyncio.to_thread(record_top_sql, ts, list(items))


async def record_waits_async(ts: int, items: Iterable[Mapping[str, Any]]) -> int:
    return await asyncio.to_thread(record_waits, ts, list(items))


def purge_expired(days: int | None = None, now: float | None = None) -> dict[str, Any]:
    """Delete samples older than the retention window; returns per-table counts."""
    window = retention_days() if days is None else int(days)
    moment = time.time() if now is None else now
    cutoff = int(moment - window * 86400)
    init_db()
    deleted: dict[str, int] = {}
    with closing(_connect()) as conn, conn:
        for table in _TABLES:
            cursor = conn.execute(f"DELETE FROM {table} WHERE ts < ?", (cutoff,))
            deleted[table] = max(0, cursor.rowcount)
    return {"cutoff": cutoff, "retention_days": window, "deleted": deleted}


# --------------------------------------------------------------------------- #
# series registry (frozen keys, design §4.1)
# --------------------------------------------------------------------------- #

SERIES_UNITS: dict[str, str] = {
    "host.cpu.user_pct": "%",
    "host.cpu.system_pct": "%",
    "host.cpu.idle_pct": "%",
    "host.cpu.iowait_pct": "%",
    "host.mem.total_mb": "MB",
    "host.mem.used_mb": "MB",
    "host.mem.available_mb": "MB",
    "host.load.1m": "load",
    "host.load.5m": "load",
    "host.load.15m": "load",
    "host.disk.used_pct": "%",
    "host.disk.free_gb": "GB",
    "host.net.rx_kbps": "KB/s",
    "host.net.tx_kbps": "KB/s",
    "host.diskio.read_kbps": "KB/s",
    "host.diskio.write_kbps": "KB/s",
    "host.proc.rss_mb": "MB",
    "host.proc.uptime_hours": "h",
    "pool.size": "connections",
    "pool.checkedout": "connections",
    "pool.overflow": "connections",
    "pool.capacity": "connections",
    "pool.utilization_pct": "%",
    "pool.waited": "waits",
    "oracle.session.total": "sessions",
    "oracle.session.active": "sessions",
    "oracle.session.inactive": "sessions",
    "oracle.aas.5m": "sessions",
    "oracle.load.exec_per_sec": "/s",
    "oracle.load.commits_per_sec": "/s",
    "oracle.load.cpu_cs_per_sec": "cs/s",
    "oracle.load.logical_reads_per_sec": "blocks/s",
    "oracle.load.physical_reads_per_sec": "blocks/s",
    "oracle.load.parse_per_sec": "/s",
}

DYNAMIC_SERIES_PREFIXES = ("oracle.tablespace.", "oracle.wait.")


def is_known_series(key: str) -> bool:
    return key in SERIES_UNITS or key.startswith(DYNAMIC_SERIES_PREFIXES)


def series_unit(key: str) -> str:
    if key in SERIES_UNITS:
        return SERIES_UNITS[key]
    if key.startswith("oracle.tablespace."):
        return "%"
    if key.startswith("oracle.wait."):
        return "sessions"
    return ""


# --------------------------------------------------------------------------- #
# queries (design §5.2 - §5.4)
# --------------------------------------------------------------------------- #

RANGE_SECONDS: dict[str, int] = {
    "1h": 3600,
    "6h": 6 * 3600,
    "24h": 24 * 3600,
    "7d": 7 * 24 * 3600,
}
AGG_FUNCTIONS = {"avg": "AVG", "max": "MAX", "min": "MIN"}


def resolve_range(range_key: str) -> int:
    try:
        return RANGE_SECONDS[range_key]
    except KeyError:
        raise ValueError(f"不支持的 range：{range_key}")


def step_seconds(range_seconds: int) -> int:
    """Bucket width: every series gets at most 120 points, multiples of 60s."""
    return math.ceil(range_seconds / 120 / 60) * 60


def query_history(
    keys: Iterable[str],
    range_key: str = "24h",
    agg: str = "avg",
    now: float | None = None,
) -> dict[str, Any]:
    """Aggregated series points for the requested keys (design §5.2)."""
    seconds = resolve_range(range_key)
    if agg not in AGG_FUNCTIONS:
        raise ValueError(f"不支持的 agg：{agg}")
    step = step_seconds(seconds)
    moment = time.time() if now is None else float(now)
    start = moment - seconds

    requested: list[str] = []
    unknown: list[str] = []
    for key in keys:
        if key not in requested:
            requested.append(key)
    known = [key for key in requested if is_known_series(key)]
    unknown = [key for key in requested if not is_known_series(key)]

    points: dict[str, list[dict[str, Any]]] = {key: [] for key in known}
    if known:
        init_db()
        placeholders = ", ".join("?" for _ in known)
        statement = (
            f"SELECT series, (ts / ?) * ? AS bucket, {AGG_FUNCTIONS[agg]}(value) AS value "
            "FROM perf_samples "
            f"WHERE ts >= ? AND ts <= ? AND value IS NOT NULL AND series IN ({placeholders}) "
            "GROUP BY series, bucket ORDER BY series, bucket"
        )
        params: list[Any] = [step, step, int(start), int(moment), *known]
        with closing(_connect()) as conn:
            for row in conn.execute(statement, params):
                bucket = row["bucket"]
                value = _finite(row["value"])
                if value is None:
                    continue
                points[row["series"]].append({"ts": iso_epoch_utc(bucket), "value": value})

    return {
        "from": iso_epoch_utc(start),
        "to": iso_epoch_utc(moment),
        "step_seconds": step,
        "agg": agg,
        "unknown_series": unknown,
        "series": [
            {"key": key, "unit": series_unit(key), "points": points[key]} for key in known
        ],
    }


def query_endpoints(
    range_key: str = "1h", limit: int = 20, now: float | None = None
) -> dict[str, Any]:
    """Aggregate persisted endpoint windows over the requested range (§5.4)."""
    seconds = resolve_range(range_key)
    bounded = max(1, min(int(limit), 100))
    moment = time.time() if now is None else float(now)
    start = moment - seconds

    init_db()
    statement = (
        "SELECT method, route, SUM(count) AS count, SUM(err_count) AS err_count, "
        "SUM(avg_ms * count) AS weighted_ms, MAX(p95_ms) AS p95_ms, MAX(max_ms) AS max_ms "
        "FROM perf_endpoints WHERE ts >= ? AND ts <= ? "
        "GROUP BY method, route ORDER BY count DESC, method, route LIMIT ?"
    )
    with closing(_connect()) as conn:
        rows = list(conn.execute(statement, (int(start), int(moment), bounded)))

    items = []
    for row in rows:
        count = int(row["count"] or 0)
        weighted = _finite(row["weighted_ms"]) or 0.0
        items.append(
            {
                "method": row["method"],
                "route": row["route"],
                "count": count,
                "err_count": int(row["err_count"] or 0),
                "avg_ms": round(weighted / count, 2) if count else 0.0,
                # p95 is not additive: the widest window in the range is the honest
                # worst case for the whole range.
                "p95_ms": round(_finite(row["p95_ms"]) or 0.0, 2),
                "max_ms": round(_finite(row["max_ms"]) or 0.0, 2),
            }
        )
    return {"window_seconds": seconds, "items": items}


def query_top_sql(limit: int = 10) -> dict[str, Any]:
    """Latest persisted Top SQL snapshot (design §5.3).

    Own monitoring SQL is filtered here as well as at collection time: rows
    stored by an older build must not be shown either. The row limit is applied
    *after* filtering, so a clean snapshot still returns ``limit`` statements.
    """
    bounded = max(1, min(int(limit), 50))
    init_db()
    with closing(_connect()) as conn:
        latest = conn.execute("SELECT MAX(ts) AS ts FROM perf_top_sql").fetchone()["ts"]
        if latest is None:
            return {"observed_at": None, "items": []}
        rows = conn.execute(
            "SELECT sql_id, executions, elapsed_ms, cpu_ms, avg_elapsed_ms, "
            "buffer_gets, disk_reads, sql_text FROM perf_top_sql "
            "WHERE ts = ? ORDER BY elapsed_ms DESC",
            (latest,),
        ).fetchall()
    items = [
        {
            "sql_id": row["sql_id"],
            "executions": row["executions"],
            "elapsed_ms": row["elapsed_ms"],
            "cpu_ms": row["cpu_ms"],
            "avg_elapsed_ms": row["avg_elapsed_ms"],
            "buffer_gets": row["buffer_gets"],
            "disk_reads": row["disk_reads"],
            "sql_text": row["sql_text"],
        }
        for row in rows
        if not db_monitor.is_monitoring_sql(row["sql_text"])
    ]
    return {"observed_at": iso_epoch_utc(latest), "items": items[:bounded]}


# --------------------------------------------------------------------------- #
# samplers
# --------------------------------------------------------------------------- #

_COUNTER_SERIES = {
    "execute_count": "oracle.load.exec_per_sec",
    "user_commits": "oracle.load.commits_per_sec",
    "cpu_used_cs": "oracle.load.cpu_cs_per_sec",
    "logical_reads": "oracle.load.logical_reads_per_sec",
    "physical_reads": "oracle.load.physical_reads_per_sec",
    "parse_count": "oracle.load.parse_per_sec",
}

_POOL_SERIES = {
    "pool.size": "size",
    "pool.checkedout": "checkedout",
    "pool.overflow": "overflow",
    "pool.capacity": "capacity",
    "pool.utilization_pct": "utilization_percent",
    "pool.waited": "waited",
}

_previous_lock = threading.Lock()
_previous_counters: tuple[float, dict[str, float]] | None = None

# Replaced in tests to make rate assertions deterministic.
_monotonic = time.monotonic

# Replaced in tests so sample timestamps are deterministic.
_epoch = time.time


def _rate_series(counters: Mapping[str, float]) -> dict[str, float]:
    """``v$sysstat`` cumulative deltas -> per-second rates, from the previous tick."""
    global _previous_counters
    now = _monotonic()
    with _previous_lock:
        previous = _previous_counters
        _previous_counters = (now, dict(counters))
    if previous is None:
        return {}
    elapsed = now - previous[0]
    if elapsed <= 0:
        return {}
    rates: dict[str, float] = {}
    for name, value in counters.items():
        key = _COUNTER_SERIES.get(name)
        old = previous[1].get(name)
        if key is None or old is None or value is None:
            continue
        delta = value - old
        if delta < 0:  # instance restarted between ticks: no rate, not a fake one
            continue
        rates[key] = round(delta / elapsed, 4)
    return rates


def _pool_series(pool: Mapping[str, Any]) -> dict[str, float]:
    if not pool or pool.get("available") is False:
        return {}
    return {
        key: float(pool[field])
        for key, field in _POOL_SERIES.items()
        if pool.get(field) is not None
    }


async def sample_host() -> dict[str, Any]:
    """Tier 1: local-only gauges + drained endpoint window. Zero DB round trips."""
    ts = int(_epoch())
    try:
        series = dict(collect_host_metrics()["series"])
        series.update(_pool_series(db_monitor.pool_metrics()))
        rows = await record_series_async(ts, series)

        window = api_metrics.drain()
        endpoints = await record_endpoints_async(ts, window["items"])
        return {"ts": ts, "series": len(series), "rows": rows, "endpoints": endpoints}
    except Exception as exc:  # a sampler must never take the scheduler down
        _logger.warning("perf sample_host failed: %r", exc)
        return {"ts": ts, "available": False, "reason": repr(exc)}


async def _sample_oracle_once(session, ts: int) -> dict[str, Any]:
    fast = await db_monitor.collect_fast(session)
    if not fast.get("available"):
        return {"ts": ts, "available": False, "reason": fast.get("reason", "unavailable")}

    series: dict[str, float] = {}
    sessions = fast.get("sessions") or {}
    for field, key in (
        ("total", "oracle.session.total"),
        ("active", "oracle.session.active"),
        ("inactive", "oracle.session.inactive"),
    ):
        value = _finite(sessions.get(field))
        if value is not None:
            series[key] = value
    aas = _finite(fast.get("aas"))
    if aas is not None:
        series["oracle.aas.5m"] = round(aas, 4)
    series.update(_rate_series(fast.get("counters") or {}))

    rows = await record_series_async(ts, series)
    return {"ts": ts, "available": True, "series": len(series), "rows": rows}


async def sample_oracle(session=None) -> dict[str, Any]:
    """Tier 2: the single merged Oracle statement (§4)."""
    ts = int(_epoch())
    try:
        if session is not None:
            return await _sample_oracle_once(session, ts)
        from db.oracle.session import session_scope  # local import: no I/O at import time

        async with session_scope() as created:
            return await _sample_oracle_once(created, ts)
    except Exception as exc:
        _logger.warning("perf sample_oracle failed: %r", exc)
        return {"ts": ts, "available": False, "reason": repr(exc)}


async def _sample_slow_once(session, ts: int) -> dict[str, Any]:
    slow = await db_monitor.collect_slow(session)

    series: dict[str, float] = {}
    for item in slow.get("tablespaces") or []:
        name = item.get("name")
        used_pct = _finite(item.get("used_pct"))
        if name and used_pct is not None:
            series[f"oracle.tablespace.{name}.used_pct"] = used_pct
    waits = slow.get("waits") or []
    for item in waits:
        wait_class = item.get("wait_class")
        sessions = _finite(item.get("sessions"))
        if wait_class and sessions is not None:
            series[f"oracle.wait.{wait_class}.sessions"] = sessions

    rows = await record_series_async(ts, series)
    await record_waits_async(ts, waits)
    top = await record_top_sql_async(ts, slow.get("top_sql") or [])
    return {
        "ts": ts,
        "available": slow.get("available", False),
        "series": len(series),
        "rows": rows,
        "top_sql": top,
        "waits": len(waits),
        "errors": slow.get("errors", {}),
    }


async def sample_slow(session=None) -> dict[str, Any]:
    """Tier 3: waits by class + schema-filtered Top SQL + tablespace usage (§4)."""
    ts = int(_epoch())
    try:
        if session is not None:
            return await _sample_slow_once(session, ts)
        from db.oracle.session import session_scope

        async with session_scope() as created:
            return await _sample_slow_once(created, ts)
    except Exception as exc:
        _logger.warning("perf sample_slow failed: %r", exc)
        return {"ts": ts, "available": False, "reason": repr(exc)}


def reset_rate_state() -> None:
    """Drop the previous cumulative counters (tests, and a clean process start)."""
    global _previous_counters
    with _previous_lock:
        _previous_counters = None


__all__ = [
    "host_interval_seconds",
    "init_db",
    "is_known_series",
    "oracle_interval_seconds",
    "purge_expired",
    "query_endpoints",
    "query_history",
    "query_top_sql",
    "record_endpoints",
    "record_endpoints_async",
    "record_series",
    "record_series_async",
    "record_top_sql",
    "record_top_sql_async",
    "record_waits",
    "record_waits_async",
    "reset_rate_state",
    "resolve_range",
    "retention_days",
    "sample_host",
    "sample_oracle",
    "sample_slow",
    "series_unit",
    "slow_interval_seconds",
    "step_seconds",
]
