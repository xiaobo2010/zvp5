"""采样器的 sqlite 写入不得落在事件循环上。

``sample_host`` 每 30s 由调度器在事件循环里调用；同步 ``sqlite3`` 写会让采样
撞上请求处理。这里钉住：

* ``record_*_async`` 与同步版写入结果完全一致（内容/行数/返回值）；
* ``sample_host`` 的写被挪到工作线程后，慢写期间事件循环仍在推进。
"""

from __future__ import annotations

import asyncio
import sqlite3
import time
from contextlib import closing

import pytest

from services import perf_sampler


@pytest.fixture(autouse=True)
def perf_db(monkeypatch, tmp_path):
    monkeypatch.setenv("ZVP5_PERF_DB", str(tmp_path / "perf_samples.db"))
    perf_sampler._initialized_paths.clear()
    perf_sampler.reset_rate_state()
    yield
    perf_sampler._initialized_paths.clear()


def _rows(table: str, columns: str) -> list[tuple]:
    with closing(sqlite3.connect(perf_sampler._db_path())) as conn:
        return list(conn.execute(f"SELECT {columns} FROM {table} ORDER BY rowid"))


@pytest.mark.asyncio
async def test_async_recorders_store_the_same_rows_as_the_sync_ones():
    ts = 1_700_000_000

    assert perf_sampler.record_series(
        ts, {"host.load.1m": 1.5, "host.mem.total_mb": 100.0}
    ) == 2
    assert await perf_sampler.record_series_async(ts, {"host.load.1m": 2.5}) == 1
    assert _rows("perf_samples", "ts, series, value") == [
        (ts, "host.load.1m", 1.5),
        (ts, "host.mem.total_mb", 100.0),
        (ts, "host.load.1m", 2.5),
    ]

    endpoints = [
        {"method": "GET", "route": "/api/v1/users", "count": 3, "err_count": 0,
         "avg_ms": 1.5, "p95_ms": 2.0, "max_ms": 2.5}
    ]
    assert perf_sampler.record_endpoints(ts, endpoints) == 1
    assert await perf_sampler.record_endpoints_async(ts, iter(endpoints)) == 1
    assert len(_rows("perf_endpoints", "route")) == 2

    waits = [{"wait_class": "User I/O", "sessions": 2}]
    assert perf_sampler.record_waits(ts, waits) == 1
    assert await perf_sampler.record_waits_async(ts, iter(waits)) == 1
    assert _rows("perf_waits", "wait_class, sessions") == [
        ("User I/O", 2),
        ("User I/O", 2),
    ]

    top_sql = [{"sql_id": "abc123", "executions": 7, "elapsed_ms": 10.0}]
    assert perf_sampler.record_top_sql(ts, top_sql) == 1
    assert await perf_sampler.record_top_sql_async(ts, iter(top_sql)) == 1
    assert _rows("perf_top_sql", "sql_id, executions") == [("abc123", 7), ("abc123", 7)]


@pytest.mark.asyncio
async def test_sample_host_does_not_block_the_event_loop(monkeypatch):
    monkeypatch.setattr(
        perf_sampler,
        "collect_host_metrics",
        lambda: {"host": {}, "series": {"host.load.1m": 0.1}},
    )
    monkeypatch.setattr(perf_sampler.db_monitor, "pool_metrics", lambda: {})

    def slow_record(ts, series):
        time.sleep(0.2)
        return len(series)

    monkeypatch.setattr(perf_sampler, "record_series", slow_record)

    ticks = 0

    async def heartbeat():
        nonlocal ticks
        for _ in range(5):
            await asyncio.sleep(0.02)
            ticks += 1

    result, _ = await asyncio.gather(perf_sampler.sample_host(), heartbeat())
    assert result["rows"] == 1
    assert ticks == 5
