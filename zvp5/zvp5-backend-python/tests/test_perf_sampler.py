"""Perf sampler: SQLite storage, history/endpoint queries, retention, samplers."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from services import perf_sampler
from services.api_metrics import ApiMetricsRecorder


class Clock:
    def __init__(self, *values: float) -> None:
        self._values = iter(values)

    def __call__(self) -> float:
        return next(self._values)


class Row:
    def __init__(self, **values) -> None:
        self._mapping = values


class Result:
    def __init__(self, rows) -> None:
        self._rows = rows

    def fetchall(self):
        return self._rows


class Session:
    """Returns the same rows for every statement (fast tier: one statement)."""

    def __init__(self, rows) -> None:
        self.rows = rows
        self.statements: list[str] = []

    async def execute(self, statement):
        self.statements.append(str(statement))
        return Result(self.rows)


class QueuedSession:
    """Returns one prepared result per statement, in order (slow tier: three)."""

    def __init__(self, responses) -> None:
        self._responses = list(responses)
        self.statements: list[str] = []

    async def execute(self, statement):
        self.statements.append(str(statement))
        return Result(self._responses.pop(0))


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()


@pytest.fixture
def perf_db(monkeypatch, tmp_path):
    path = tmp_path / "perf_samples.db"
    monkeypatch.setenv("ZVP5_PERF_DB", str(path))
    perf_sampler._initialized_paths.clear()
    perf_sampler.reset_rate_state()
    return path


def test_intervals_are_configurable_and_defensive(perf_db, monkeypatch):
    assert perf_sampler.host_interval_seconds() == 30.0
    assert perf_sampler.oracle_interval_seconds() == 120.0
    assert perf_sampler.slow_interval_seconds() == 600.0
    assert perf_sampler.retention_days() == 14

    monkeypatch.setenv("PERF_HOST_INTERVAL_SECONDS", "15")
    monkeypatch.setenv("PERF_ORACLE_INTERVAL_SECONDS", "60")
    monkeypatch.setenv("PERF_SLOW_INTERVAL_SECONDS", "300")
    monkeypatch.setenv("PERF_RETENTION_DAYS", "7")
    assert perf_sampler.host_interval_seconds() == 15.0
    assert perf_sampler.oracle_interval_seconds() == 60.0
    assert perf_sampler.slow_interval_seconds() == 300.0
    assert perf_sampler.retention_days() == 7

    monkeypatch.setenv("PERF_HOST_INTERVAL_SECONDS", "not-a-number")
    monkeypatch.setenv("PERF_RETENTION_DAYS", "-3")
    assert perf_sampler.host_interval_seconds() == 30.0
    assert perf_sampler.retention_days() == 14


def test_step_seconds_keeps_every_series_under_120_points():
    assert perf_sampler.step_seconds(3600) == 60
    assert perf_sampler.step_seconds(6 * 3600) == 180
    assert perf_sampler.step_seconds(24 * 3600) == 720
    assert perf_sampler.step_seconds(7 * 24 * 3600) == 5040


def test_history_buckets_uses_agg_and_reports_unknown_series(perf_db):
    now = 1_700_000_040  # divisible by 60: buckets land exactly on sample times
    perf_sampler.record_series(now - 1800, {"host.cpu.user_pct": 10.0})
    perf_sampler.record_series(now - 1800, {"host.cpu.user_pct": 30.0})
    perf_sampler.record_series(now - 7200, {"host.cpu.user_pct": 99.0})  # outside 1h

    data = perf_sampler.query_history(
        ["host.cpu.user_pct", "foo.bar"], "1h", "avg", now=now
    )
    assert data["from"] == _iso(now - 3600)
    assert data["to"] == _iso(now)
    assert data["step_seconds"] == 60
    assert data["agg"] == "avg"
    assert data["unknown_series"] == ["foo.bar"]
    assert [item["key"] for item in data["series"]] == ["host.cpu.user_pct"]
    assert data["series"][0]["unit"] == "%"
    assert data["series"][0]["points"] == [{"ts": _iso(now - 1800), "value": 20.0}]

    peak = perf_sampler.query_history(["host.cpu.user_pct"], "1h", "max", now=now)
    assert peak["series"][0]["points"] == [{"ts": _iso(now - 1800), "value": 30.0}]
    lowest = perf_sampler.query_history(["host.cpu.user_pct"], "1h", "min", now=now)
    assert lowest["series"][0]["points"] == [{"ts": _iso(now - 1800), "value": 10.0}]


def test_history_rejects_unknown_range_and_agg(perf_db):
    with pytest.raises(ValueError):
        perf_sampler.query_history(["host.cpu.user_pct"], "3h")
    with pytest.raises(ValueError):
        perf_sampler.query_history(["host.cpu.user_pct"], "1h", "median")


def test_history_keeps_known_series_without_data_as_empty_points(perf_db):
    data = perf_sampler.query_history(["oracle.tablespace.SYSTEM.used_pct"], "1h")
    assert data["unknown_series"] == []
    assert data["series"][0]["points"] == []
    assert data["series"][0]["unit"] == "%"


def test_top_sql_snapshot_is_the_latest_by_elapsed_time(perf_db):
    perf_sampler.record_top_sql(
        1000,
        [
            {"sql_id": "aaa", "executions": 1, "elapsed_ms": 10.0, "cpu_ms": 1.0,
             "avg_elapsed_ms": 10.0, "buffer_gets": 5, "disk_reads": 1, "sql_text": "SELECT 1"},
            {"sql_id": "bbb", "executions": 2, "elapsed_ms": 200.0, "cpu_ms": 2.0,
             "avg_elapsed_ms": 100.0, "buffer_gets": 6, "disk_reads": 2, "sql_text": None},
        ],
    )
    perf_sampler.record_top_sql(
        2000,
        [{"sql_id": "ccc", "executions": 3, "elapsed_ms": 5.0, "cpu_ms": 0.5,
          "avg_elapsed_ms": 1.6, "buffer_gets": 1, "disk_reads": 0, "sql_text": "SELECT 2"}],
    )

    data = perf_sampler.query_top_sql(10)
    assert data["observed_at"] == _iso(2000)
    assert [item["sql_id"] for item in data["items"]] == ["ccc"]

    # Within one snapshot the longest statement comes first.
    perf_sampler.record_top_sql(
        3000,
        [
            {"sql_id": "ddd", "executions": 1, "elapsed_ms": 1.0, "cpu_ms": 1.0,
             "avg_elapsed_ms": 1.0, "buffer_gets": 1, "disk_reads": 0, "sql_text": "a"},
            {"sql_id": "eee", "executions": 1, "elapsed_ms": 900.0, "cpu_ms": 1.0,
             "avg_elapsed_ms": 900.0, "buffer_gets": 1, "disk_reads": 0, "sql_text": "b"},
        ],
    )
    assert [item["sql_id"] for item in perf_sampler.query_top_sql(1)["items"]] == ["eee"]


def test_top_sql_is_empty_before_any_sample(perf_db):
    assert perf_sampler.query_top_sql() == {"observed_at": None, "items": []}


def test_endpoints_aggregate_across_windows(perf_db):
    now = 1_700_000_000
    perf_sampler.record_endpoints(
        now - 600,
        [{"method": "GET", "route": "/a", "count": 10, "err_count": 1,
          "avg_ms": 100.0, "p95_ms": 200.0, "max_ms": 300.0}],
    )
    perf_sampler.record_endpoints(
        now - 60,
        [{"method": "GET", "route": "/a", "count": 30, "err_count": 0,
          "avg_ms": 200.0, "p95_ms": 400.0, "max_ms": 500.0},
         {"method": "POST", "route": "/b", "count": 5, "err_count": 0,
          "avg_ms": 1.0, "p95_ms": 2.0, "max_ms": 3.0}],
    )
    perf_sampler.record_endpoints(
        now - 4 * 3600,
        [{"method": "GET", "route": "/old", "count": 99, "err_count": 0,
          "avg_ms": 1.0, "p95_ms": 1.0, "max_ms": 1.0}],
    )

    data = perf_sampler.query_endpoints("1h", 20, now=now)
    assert data["window_seconds"] == 3600
    items = {item["route"]: item for item in data["items"]}
    assert set(items) == {"/a", "/b"}
    assert items["/a"]["count"] == 40
    assert items["/a"]["err_count"] == 1
    assert items["/a"]["avg_ms"] == 175.0  # weighted by count
    assert items["/a"]["p95_ms"] == 400.0
    assert items["/a"]["max_ms"] == 500.0

    limited = perf_sampler.query_endpoints("1h", 1, now=now)
    assert [item["route"] for item in limited["items"]] == ["/a"]


def test_retention_deletes_only_expired_samples(perf_db):
    now = 1_700_000_000
    perf_sampler.record_series(now - 20 * 86400, {"host.load.1m": 1.0})
    perf_sampler.record_series(now, {"host.load.1m": 2.0})
    perf_sampler.record_endpoints(now - 20 * 86400, [
        {"method": "GET", "route": "/a", "count": 1, "err_count": 0,
         "avg_ms": 1.0, "p95_ms": 1.0, "max_ms": 1.0},
    ])
    perf_sampler.record_top_sql(now - 20 * 86400, [
        {"sql_id": "old", "executions": 1, "elapsed_ms": 1.0, "cpu_ms": 1.0,
         "avg_elapsed_ms": 1.0, "buffer_gets": 1, "disk_reads": 0, "sql_text": "x"},
    ])
    perf_sampler.record_waits(now - 20 * 86400, [{"wait_class": "Idle", "sessions": 1}])

    result = perf_sampler.purge_expired(14, now=now)
    assert result["retention_days"] == 14
    assert result["cutoff"] == int(now - 14 * 86400)
    assert result["deleted"]["perf_samples"] == 1
    assert result["deleted"]["perf_endpoints"] == 1
    assert result["deleted"]["perf_top_sql"] == 1
    assert result["deleted"]["perf_waits"] == 1

    remaining = perf_sampler.query_history(["host.load.1m"], "24h", "max", now=now)
    assert remaining["series"][0]["points"]


@pytest.mark.asyncio
async def test_sample_host_writes_local_series_and_drains_endpoints(perf_db, monkeypatch):
    monkeypatch.setattr(
        perf_sampler,
        "collect_host_metrics",
        lambda: {"host": {}, "series": {"host.mem.total_mb": 1919.8, "host.load.1m": 0.01}},
    )
    monkeypatch.setattr(
        perf_sampler.db_monitor,
        "pool_metrics",
        lambda: {"size": 5, "checkedout": 1, "overflow": 0, "capacity": 10,
                 "utilization_percent": 10.0, "waited": 0},
    )
    recorder = ApiMetricsRecorder()
    recorder.record("GET", "/api/v1/users", 200, 12.0)
    monkeypatch.setattr(perf_sampler, "api_metrics", recorder)

    result = await perf_sampler.sample_host()
    assert result["series"] == 8  # 2 host + 6 pool
    assert result["endpoints"] == 1

    series = perf_sampler.query_history(["oracle.session.total"], "1h")
    assert series["series"][0]["points"] == []
    stored = perf_sampler.query_history(["pool.utilization_pct"], "1h", "max")
    assert stored["series"][0]["points"][-1]["value"] == 10.0

    # The endpoint window was drained into perf_endpoints.
    endpoints = perf_sampler.query_endpoints("1h")
    assert endpoints["items"][0]["route"] == "/api/v1/users"
    assert recorder.snapshot()["items"] == []


@pytest.mark.asyncio
async def test_sample_oracle_computes_rates_from_the_previous_tick(perf_db, monkeypatch):
    # 0s/100s between the ticks; sample times 100s apart so the buckets differ.
    monkeypatch.setattr(perf_sampler, "_monotonic", Clock(0.0, 100.0))
    monkeypatch.setattr(perf_sampler, "_epoch", Clock(1_700_000_040, 1_700_000_140))
    session = Session(
        [
            Row(
                session_total=6, session_active=1, session_inactive=5,
                stat_execute_count=1000, stat_user_commits=500, stat_cpu_used_cs=200,
                stat_logical_reads=10000, stat_physical_reads=100, stat_parse_count=50,
                ash_sample_rows=30, ash_sample_seconds=30,
            )
        ]
    )
    first = await perf_sampler.sample_oracle(session=session)
    assert first["available"] is True
    # Gauges only: no previous reading, so no rate series (no fake values).
    assert first["series"] == 4
    assert len(session.statements) == 1  # exactly one merged statement per tick

    session.rows = [
        Row(
            session_total=7, session_active=2, session_inactive=5,
            stat_execute_count=1300, stat_user_commits=500, stat_cpu_used_cs=400,
            stat_logical_reads=10000, stat_physical_reads=None, stat_parse_count=150,
            ash_sample_rows=60, ash_sample_seconds=30,
        )
    ]
    second = await perf_sampler.sample_oracle(session=session)
    assert second["available"] is True

    now = 1_700_000_300
    result = perf_sampler.query_history(
        [
            "oracle.session.total",
            "oracle.aas.5m",
            "oracle.load.exec_per_sec",
            "oracle.load.commits_per_sec",
        ],
        "1h",
        "avg",
        now=now,
    )
    history = {item["key"]: item["points"][-1]["value"] for item in result["series"]}
    assert history["oracle.session.total"] == 7
    assert history["oracle.aas.5m"] == 2.0  # 60 rows over 30 sample instants
    assert history["oracle.load.exec_per_sec"] == 3.0  # (1300-1000)/100
    assert history["oracle.load.commits_per_sec"] == 0.0
    # NULL counter degrades to "no series", not to a fake zero.
    null_rate = perf_sampler.query_history(
        ["oracle.load.physical_reads_per_sec"], "1h", "avg", now=now
    )
    assert null_rate["series"][0]["points"] == []


@pytest.mark.asyncio
async def test_sample_oracle_degrades_without_samples(perf_db):
    session = Session([])
    result = await perf_sampler.sample_oracle(session=session)
    assert result["available"] is False
    assert result["reason"] == "no_samples"


@pytest.mark.asyncio
async def test_sample_slow_persists_waits_top_sql_and_tablespaces(perf_db, monkeypatch):
    monkeypatch.setattr(perf_sampler, "_epoch", lambda: 1_700_000_040)
    session = QueuedSession(
        [
            [Row(wait_class="Idle", sessions=4), Row(wait_class="User I/O", sessions=1)],
            [Row(sql_id="b39m8n96gxk7c", executions=298, elapsed_time=728319300,
                 cpu_time=697343800, buffer_gets=26087739, disk_reads=494671,
                 sql_text="SELECT ...")],
            [Row(tablespace_name="SYSTEM", used_percent=42.5),
             Row(tablespace_name="DATA", used_percent=10.0)],
        ]
    )
    result = await perf_sampler.sample_slow(session=session)
    assert result["available"] is True
    assert result["top_sql"] == 1
    assert result["waits"] == 2
    assert len(session.statements) == 3  # slow tier is exactly three statements

    now = 1_700_000_700
    history = {
        item["key"]: item["points"][-1]["value"]
        for item in perf_sampler.query_history(
            ["oracle.tablespace.SYSTEM.used_pct", "oracle.wait.Idle.sessions"],
            "1h",
            "avg",
            now=now,
        )["series"]
    }
    assert history["oracle.tablespace.SYSTEM.used_pct"] == 42.5
    assert history["oracle.wait.Idle.sessions"] == 4.0

    top = perf_sampler.query_top_sql(10)["items"][0]
    assert top["sql_id"] == "b39m8n96gxk7c"
    assert top["executions"] == 298
    assert top["elapsed_ms"] == 728319.3
    assert top["cpu_ms"] == 697343.8
    assert top["avg_elapsed_ms"] == 2444.0
    assert top["buffer_gets"] == 26087739


def test_fast_load_metrics_are_rates_after_the_first_reading(perf_db, monkeypatch):
    """The live load section switches from gauges to V$SYSSTAT deltas."""
    from services import db_monitor

    db_monitor.clear_database_status_cache()
    monkeypatch.setattr(db_monitor, "_monotonic", Clock(0.0, 100.0))

    first = db_monitor._fast_load_metrics(
        [Row(session_total=6, session_active=1, session_inactive=5,
             stat_execute_count=1000, ash_sample_rows=30, ash_sample_seconds=30)]
    )
    assert [item["name"] for item in first] == ["Session Count", "Average Active Sessions"]
    assert first[0]["value"] == 6
    assert first[0]["unit"] == "Sessions"
    assert first[0]["interval_centiseconds"] == 0
    assert first[1]["value"] == 1.0
    # Frozen element shape: field names unchanged by the source swap.
    assert set(first[0]) == {
        "name", "value", "unit", "begin_time", "end_time",
        "interval_centiseconds", "group_id",
    }

    second = db_monitor._fast_load_metrics(
        [Row(session_total=7, session_active=2, session_inactive=5,
             stat_execute_count=1300, ash_sample_rows=60, ash_sample_seconds=30)]
    )
    by_name = {item["name"]: item for item in second}
    assert by_name["Session Count"]["value"] == 7
    assert by_name["Average Active Sessions"]["value"] == 2.0
    assert by_name["Executions Per Sec"]["value"] == 3.0  # (1300-1000)/100s
    assert by_name["Executions Per Sec"]["interval_centiseconds"] == 10000
    assert by_name["Executions Per Sec"]["unit"] == "Executions/sec"
