"""Top SQL leaderboard must exclude this service's own monitoring statements.

Production evidence: the storage CTE (``WITH table_storage AS ( ... )``,
exec=7 / elapsed 15489ms) and ``SELECT 1 FROM DUAL`` (exec=104273, the pool
pre-ping + health check) were ranking above real business SQL.
"""

from __future__ import annotations

import pytest

from services import db_monitor, perf_sampler


class Row:
    def __init__(self, **values) -> None:
        self._mapping = values


BUSINESS_SQL = [
    "SELECT users.oid, users.business_id, users.name FROM users WHERE users.oid = :oid_1",
    "SELECT activities.oid, activities.name FROM activities WHERE activities.atype = 'normal'",
    "SELECT groups.oid, groups.name FROM groups WHERE groups.oid = :oid_1",
    "SELECT activity_members.oid FROM activity_members WHERE member_oid = :oid_1",
    "UPDATE users SET session_id = :session_id WHERE oid = :oid_1",
    "INSERT INTO app_logs (oid, url) VALUES (:oid, :url)",
    "SELECT daily_exports.data FROM daily_exports WHERE daily_exports.oid = :oid_1",
    # Business-ish text that merely contains monitoring keywords as words.
    "SELECT value FROM valuables WHERE segment_name = :name",
]

MONITORING_SQL = [
    "SELECT 1 FROM DUAL",
    "select 1 from dual",
    "SELECT\n  1\nFROM\n  DUAL",
    "SELECT COUNT(*) FROM v$session",
    "select count(*) from gv$session",
    "SELECT value FROM V$SYSSTAT WHERE name = 'execute count'",
    "SELECT * FROM v$active_session_history",
    "SELECT 1 FROM dba_tablespace_usage_metrics",
    "SELECT COUNT(*) FROM USER_SEGMENTS",
    "SELECT * FROM user_tables",
    "SELECT * FROM user_indexes",
    "SELECT * FROM user_lobs",
    "WITH\n    table_storage AS (\n      SELECT t.table_name FROM user_tables t\n    )\n    SELECT * FROM table_storage",
]


@pytest.fixture
def perf_db(monkeypatch, tmp_path):
    monkeypatch.setenv("ZVMS_PERF_DB", str(tmp_path / "perf_samples.db"))
    perf_sampler._initialized_paths.clear()
    perf_sampler.reset_rate_state()
    return tmp_path / "perf_samples.db"


def test_fingerprint_flags_monitoring_sql_but_never_business_sql():
    for sql in MONITORING_SQL:
        assert db_monitor.is_monitoring_sql(sql) is True, sql
    for sql in BUSINESS_SQL:
        assert db_monitor.is_monitoring_sql(sql) is False, sql
    assert db_monitor.is_monitoring_sql(None) is False
    assert db_monitor.is_monitoring_sql("") is False


def test_fingerprint_covers_every_production_monitoring_statement():
    """The statement constants themselves are the strongest fixtures."""
    statements = [
        ("tables", db_monitor._TABLES_SQL),
        ("tablespaces", db_monitor._TABLESPACES_SQL),
        ("fast", db_monitor._FAST_SNAPSHOT_SQL),
        ("sessions", db_monitor._SESSIONS_SQL),
        ("waits", db_monitor._SLOW_WAITS_SQL),
        ("top", db_monitor._SLOW_TOP_SQL),
        ("dual", "SELECT 1 FROM DUAL"),
    ]
    # v$sqlarea returns SUBSTR(sql_text, 1, 200); the Python guard can only see
    # that prefix. The two statements whose marker lies past it (their projection
    # lists are long) are the ones that never rank anyway — each executes a few
    # times a day at ~0.3-2s, unlike the storage CTE and the DUAL probe that
    # actually won the leaderboard. Both passes therefore matter:
    #   * query side: filters on the full sql_text (authoritative),
    #   * Python side: covers already-stored legacy rows.
    marker_past_200_chars = {"sessions", "top"}
    for name, statement in statements:
        text = str(statement)
        assert db_monitor.is_monitoring_sql(text) is True, name
        truncated = db_monitor.is_monitoring_sql(text[:200])
        if name in marker_past_200_chars:
            assert truncated is False, name
            assert "FROM v$" in text[200:], name
        else:
            assert truncated is True, name


def test_sql_side_filter_is_embedded_in_the_top_sql_statement():
    statement = str(db_monitor._SLOW_TOP_SQL)
    assert "parsing_schema_name = SYS_CONTEXT('USERENV', 'CURRENT_SCHEMA')" in statement
    assert "REGEXP_LIKE(UPPER(sql_text)" in statement
    # Keep rows whose text is unreadable: not evidence of monitoring SQL.
    assert "sql_text IS NULL" in statement
    # The pattern is pre-bound, not inlined: text() would read ":space" inside the
    # POSIX class "[[:space:]]" as a bind parameter and corrupt the regex.
    assert "NOT REGEXP_LIKE(UPPER(sql_text), :monitoring_sql_regex)" in statement
    bound = db_monitor._SLOW_TOP_SQL._bindparams["monitoring_sql_regex"]
    assert bound.value == db_monitor._ORACLE_MONITORING_SQL_REGEX
    assert bound.value is not None


def test_no_production_statement_has_unresolved_bind_parameters():
    """A stray ``:name`` in a SQL literal silently becomes a bind parameter."""
    statements = {
        "_TABLES_SQL": db_monitor._TABLES_SQL,
        "_TABLESPACES_SQL": db_monitor._TABLESPACES_SQL,
        "_FAST_SNAPSHOT_SQL": db_monitor._FAST_SNAPSHOT_SQL,
        "_SESSIONS_SQL": db_monitor._SESSIONS_SQL,
        "_SLOW_WAITS_SQL": db_monitor._SLOW_WAITS_SQL,
        "_SLOW_TOP_SQL": db_monitor._SLOW_TOP_SQL,
    }
    for name, statement in statements.items():
        unresolved = [
            key for key, param in statement._bindparams.items() if param.value is None
        ]
        assert unresolved == [], f"{name} needs execution-time bind values: {unresolved}"


def test_item_builder_drops_monitoring_rows(perf_db):
    rows = [
        Row(sql_id="mon1", executions=104273, elapsed_time=4187525, cpu_time=100,
            buffer_gets=1, disk_reads=0, sql_text="SELECT 1 FROM DUAL"),
        Row(sql_id="biz1", executions=29344, elapsed_time=170842188, cpu_time=100,
            buffer_gets=1, disk_reads=0, sql_text=BUSINESS_SQL[0]),
        Row(sql_id="mon2", executions=7, elapsed_time=15489339, cpu_time=100,
            buffer_gets=1, disk_reads=0, sql_text=str(db_monitor._TABLES_SQL)[:200]),
        Row(sql_id="biz2", executions=9764, elapsed_time=50074618, cpu_time=100,
            buffer_gets=1, disk_reads=0, sql_text=BUSINESS_SQL[1]),
        # Unreadable text must be kept, not silently dropped.
        Row(sql_id="biz3", executions=1, elapsed_time=1000, cpu_time=1,
            buffer_gets=1, disk_reads=0, sql_text=None),
    ]
    items = db_monitor._top_sql_items(rows)
    assert [item["sql_id"] for item in items] == ["biz1", "biz2", "biz3"]


def test_stored_monitoring_rows_never_reach_the_leaderboard(perf_db):
    snapshot = []
    for index, sql in enumerate(BUSINESS_SQL * 1):
        snapshot.append(
            {"sql_id": f"biz{index}", "executions": 10, "elapsed_ms": 1000.0 - index,
             "cpu_ms": 1.0, "avg_elapsed_ms": 10.0, "buffer_gets": 1, "disk_reads": 0,
             "sql_text": sql}
        )
    snapshot.append(
        {"sql_id": "mon1", "executions": 104273, "elapsed_ms": 900000.0, "cpu_ms": 1.0,
         "avg_elapsed_ms": 1.0, "buffer_gets": 1, "disk_reads": 0,
         "sql_text": "SELECT 1 FROM DUAL"}
    )
    snapshot.append(
        {"sql_id": "mon2", "executions": 7, "elapsed_ms": 800000.0, "cpu_ms": 1.0,
         "avg_elapsed_ms": 1.0, "buffer_gets": 1, "disk_reads": 0,
         "sql_text": str(db_monitor._TABLES_SQL)[:200]}
    )
    perf_sampler.record_top_sql(1000, snapshot)

    everything = perf_sampler.query_top_sql(50)["items"]
    ids = [item["sql_id"] for item in everything]
    assert "mon1" not in ids and "mon2" not in ids
    assert ids == [f"biz{index}" for index in range(len(BUSINESS_SQL))]
    # The huge monitoring statements are gone, so the real top statement leads.
    assert everything[0]["sql_id"] == "biz0"

    # limit still bounds the leaderboard after filtering.
    assert len(perf_sampler.query_top_sql(3)["items"]) == 3
    assert len(perf_sampler.query_top_sql(1)["items"]) == 1
