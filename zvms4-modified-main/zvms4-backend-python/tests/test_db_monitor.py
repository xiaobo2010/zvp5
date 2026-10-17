from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import DatabaseError

from services import db_monitor


class Row:
    def __init__(self, **values):
        self._mapping = values


class Result:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class Session:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []
        self.rollbacks = 0

    async def execute(self, statement):
        self.calls.append(str(statement))
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return Result(response)

    async def rollback(self):
        self.rollbacks += 1


def denied():
    return DatabaseError(None, None, Exception(SimpleNamespace(code=942)))


@pytest.fixture(autouse=True)
def reset_cache(monkeypatch):
    db_monitor.clear_database_status_cache()
    monkeypatch.setattr(db_monitor, "_pool_status", lambda: {"available": True})


@pytest.mark.asyncio
async def test_partial_privileges_and_cache_are_isolated():
    session = Session([
        [],  # connectivity
        [Row(table_name="USERS", tablespace_name="DATA", num_rows=None, last_analyzed=None,
             table_bytes=100, index_bytes=20, lob_bytes=5, lob_index_bytes=2)],
        denied(), [],  # rollback then recovery ping
        denied(), [],
    ])
    data = await db_monitor.get_database_status(session)
    assert data["status"] == "degraded"
    assert data["storage"]["tables"]["items"][0]["total_bytes"] == 127
    assert data["storage"]["tables"]["items"][0]["estimated_rows"] is None
    assert data["storage"]["tablespaces"]["reason"] == "insufficient_privileges"
    assert not data["load"]["available"]
    assert session.rollbacks == 2
    data["storage"]["tables"]["items"].clear()
    data.pop("status")
    cached = await db_monitor.get_database_status(Session([]))
    assert cached["cache"]["cached"]
    assert cached["status"] == "degraded"
    assert len(cached["storage"]["tables"]["items"]) == 1


@pytest.mark.asyncio
async def test_metrics_keep_units_and_window():
    row = Row(metric_name="Average Active Sessions", value=1.5, metric_unit="Active Sessions",
              begin_time=datetime(2026, 10, 17, 1, 2), end_time=datetime(2026, 10, 17, 1, 3),
              intsize_csec=6000, group_id=2)
    data = await db_monitor.get_database_status(Session([[], [], [], [row]]))
    assert data["status"] == "ok"
    assert data["load"]["metrics"][0]["unit"] == "Active Sessions"
    assert data["load"]["metrics"][0]["interval_centiseconds"] == 6000


@pytest.mark.asyncio
async def test_disconnect_and_sql_errors_are_not_hidden():
    for failure in (ConnectionError("disconnected"), RuntimeError("bad SQL")):
        with pytest.raises(type(failure)):
            await db_monitor.get_database_status(Session([failure]))


@pytest.mark.asyncio
async def test_no_samples_does_not_degrade_storage_status():
    # V$SYSMETRIC yields no rows on some ADB instances; storage stays readable,
    # so the overall verdict must remain ok while the load section self-reports.
    data = await db_monitor.get_database_status(Session([[], [], [], []]))
    assert data["status"] == "ok"
    assert data["load"]["available"] is False
    assert data["load"]["reason"] == "no_samples"


@pytest.mark.asyncio
async def test_operational_tables_hidden_and_tablespaces_flagged():
    session = Session([
        [],  # connectivity
        [
            Row(table_name="USERS", tablespace_name="DATA", num_rows=1, last_analyzed=None,
                table_bytes=10, index_bytes=0, lob_bytes=0, lob_index_bytes=0),
            Row(table_name="KEEPALIVE_HEARTBEAT", tablespace_name="DATA", num_rows=1,
                last_analyzed=None, table_bytes=99, index_bytes=0, lob_bytes=0,
                lob_index_bytes=0),
        ],
        [
            Row(tablespace_name="DATA", used_bytes=1, max_bytes=2, used_percent=50.0),
            Row(tablespace_name="SYSTEM", used_bytes=1, max_bytes=2, used_percent=50.0),
        ],
        [],
    ])
    data = await db_monitor.get_database_status(session)
    tables = data["storage"]["tables"]
    assert [item["name"] for item in tables["items"]] == ["USERS"]
    assert tables["count"] == 1
    assert tables["total_bytes"] == 10
    flags = {
        item["name"]: item["is_application"]
        for item in data["storage"]["tablespaces"]["items"]
    }
    assert flags == {"DATA": True, "SYSTEM": False}
    assert any("KEEPALIVE_HEARTBEAT" in warning for warning in data["warnings"])


@pytest.mark.asyncio
async def test_timeout_recovers_before_next_view(monkeypatch):
    session = Session([[], TimeoutError(), [], [], []])
    data = await db_monitor.get_database_status(session)
    assert data["storage"]["tables"]["reason"] == "query_timeout"
    assert session.rollbacks == 1
    assert session.calls[2] == "SELECT 1 FROM DUAL"


@pytest.mark.asyncio
async def test_concurrent_requests_collect_once(monkeypatch):
    calls = 0

    async def collect(session):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.01)
        return {"storage": {"tables": {"available": True}, "tablespaces": {"available": True}},
                "load": {"available": True}}

    monkeypatch.setattr(db_monitor, "_collect", collect)
    results = await asyncio.gather(*(db_monitor.get_database_status(None) for _ in range(8)))
    assert calls == 1
    assert sum(not result["cache"]["cached"] for result in results) == 1


def test_exact_byte_numbers_and_nonfinite_metrics():
    assert db_monitor._number(2**60 + 1, integer=True) == 2**60 + 1
    assert db_monitor._number(float("nan")) is None
    assert db_monitor._number(float("inf"), integer=True) is None


def test_storage_sql_aggregates_partition_names_and_lob_indexes():
    # Execute the production statement against dictionary-shaped fixtures.
    # Oracle has SEGMENT_NAME=base object and PARTITION_NAME=partition;
    # joining SEGMENT_NAME to partition names would silently lose these bytes.
    db = sqlite3.connect(":memory:")
    db.create_function("NVL", 2, lambda value, fallback: fallback if value is None else value)
    db.executescript("""
        CREATE TABLE user_tables(table_name TEXT, tablespace_name TEXT, num_rows INTEGER, last_analyzed TEXT);
        CREATE TABLE user_segments(segment_name TEXT, partition_name TEXT, segment_type TEXT, bytes INTEGER);
        CREATE TABLE user_indexes(table_name TEXT, index_name TEXT, index_type TEXT);
        CREATE TABLE user_lobs(table_name TEXT, segment_name TEXT, index_name TEXT);
        INSERT INTO user_tables VALUES
          ('PLAIN', 'DATA', NULL, NULL),
          ('PART', 'DATA', 8, NULL),
          ('EMPTY', 'DATA', 0, NULL);
        INSERT INTO user_indexes VALUES ('PART','PART_I','NORMAL'),('PART','LOB_I','LOB');
        INSERT INTO user_lobs VALUES ('PART','LOB_DATA','LOB_I');
        INSERT INTO user_segments VALUES
          ('PLAIN',NULL,'TABLE',10),
          ('PART','P1','TABLE PARTITION',20),
          ('PART','SP2','TABLE SUBPARTITION',30),
          ('PART_I','P1','INDEX PARTITION',4),
          ('PART_I','SP2','INDEX SUBPARTITION',6),
          ('LOB_DATA','LP1','LOB PARTITION',40),
          ('LOB_DATA','LSP2','LOB SUBPARTITION',50),
          ('LOB_I','LIP1','LOBINDEX',2),
          ('LOB_I','LISP2','INDEX SUBPARTITION',3);
    """)
    cursor = db.execute(str(db_monitor._TABLES_SQL))
    rows = {row[0]: dict(zip([c[0] for c in cursor.description], row)) for row in cursor.fetchall()}
    assert rows["PART"]["table_bytes"] == 50
    assert rows["PART"]["index_bytes"] == 10
    assert rows["PART"]["lob_bytes"] == 90
    assert rows["PART"]["lob_index_bytes"] == 5
    assert rows["EMPTY"]["table_bytes"] == 0
    assert rows["PLAIN"]["num_rows"] is None
    db.close()
