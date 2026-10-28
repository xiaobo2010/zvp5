"""V047 结构优化：索引增删、死表下线、created_at 默认值、只增表保留窗口。

覆盖四块，全部离线（不建引擎、不连库）：

* **索引**：两条新索引存在且列序正确；四条冗余索引在 models / V001 里都不再出现。
  前缀关系的证据写在用例里（被删索引的列必须是被保留索引的完整前缀，或者根本
  没有查询按那一列读）。
* **死表**：``logs`` 从 models 与 V001 消失，V047 里有幂等的 ``DROP TABLE``。
* **默认值**：所有带 ``created_at`` 的表除 ``ai_api_keys``（它早已有）之外都是
  ``DEFAULT SYSTIMESTAMP``，且 V047 逐表补了 ``MODIFY``。
* **保留窗口**：四张只增表的默认天数、防御式读法、``DELETE`` 的 SQL 形状，以及
  ``run_append_only_retention`` 用各自窗口 + 同一批量/批次上限 drain。

跑法：``.venv/bin/python -m pytest tests/test_db_optimization.py -q``
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy.dialects import oracle

import config
from db import ddl_check, models
from tasks import retention

DIALECT = oracle.dialect()
V001_PATH = Path(ddl_check.DDL_PATH)
V047_PATH = V001_PATH.parent / "V047__db_optimization.sql"
TS = datetime(2026, 10, 29, 3, 30, 0)


def _sql(stmt) -> str:
    return " ".join(
        str(
            stmt.compile(dialect=DIALECT, compile_kwargs={"literal_binds": True})
        ).split()
    )


def _index_columns(table: str) -> dict[str, list[str]]:
    return {
        index.name: [col.name for col in index.columns]
        for index in models.metadata.tables[table].indexes
    }


def _unique_columns(table: str) -> list[list[str]]:
    return [
        [col.name for col in constraint.columns]
        for constraint in models.metadata.tables[table].constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    ]


def _v001_sql() -> str:
    return ddl_check.extract_execute_immediate(V001_PATH.read_text(encoding="utf-8"))


def _v047_sql() -> str:
    return ddl_check.extract_execute_immediate(V047_PATH.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# 索引：新增的两条
# ---------------------------------------------------------------------------


def test_ban_violations_gets_a_created_at_index_for_the_admin_page():
    """管理页是「全表 created_at 倒序」，两条复合索引都以 user_oid 打头。"""
    indexes = _index_columns("ban_violations")

    assert indexes["ix_ban_violations_created"] == ["created_at"]
    assert "CREATE INDEX ix_ban_violations_created ON ban_violations (created_at)" in _v001_sql()
    assert "ix_ban_violations_created" in _v047_sql()


def test_ai_call_logs_gets_a_status_created_index():
    """单 status 过滤吃不到 (endpoint, status, created_at) 的首列。"""
    indexes = _index_columns("ai_call_logs")

    assert indexes["ix_ai_call_logs_status_created"] == ["status", "created_at"]
    assert (
        "CREATE INDEX ix_ai_call_logs_status_created ON ai_call_logs (status, created_at)"
        in _v001_sql()
    )
    assert "ix_ai_call_logs_status_created" in _v047_sql()


# ---------------------------------------------------------------------------
# 索引：删掉的四条（逐条给出「为什么可删」的证据）
# ---------------------------------------------------------------------------


def test_ban_violations_user_index_is_a_strict_prefix_of_the_batch_index():
    """(user_oid, trigger_code, created_at) 是四列索引的完整前缀。"""
    indexes = _index_columns("ban_violations")
    kept = indexes["ix_ban_violations_batch"]
    removed = ["user_oid", "trigger_code", "created_at"]

    assert kept[: len(removed)] == removed
    assert "ix_ban_violations_user" not in indexes
    assert "ix_ban_violations_user" in _v047_sql()


def test_time_academic_year_single_column_index_is_a_prefix_of_the_unique_key():
    """(user_oid) 是 uq(user_oid, academic_year) 唯一索引的前缀。"""
    indexes = _index_columns("time_academic_year")
    uniques = _unique_columns("time_academic_year")

    assert ["user_oid", "academic_year"] in uniques
    assert "ix_time_academic_year_user_oid" not in indexes
    assert "ix_time_academic_year_user_oid" in _v047_sql()


def test_ai_messages_created_index_is_unused():
    """只共享首列；而这张表没有任何查询按 created_at 读（只按 seq 读）。"""
    indexes = _index_columns("ai_messages")
    uniques = _unique_columns("ai_messages")

    assert ["conversation_oid", "seq"] in uniques
    assert "ix_ai_messages_conv_created" not in indexes
    assert "ix_ai_messages_conv_created" in _v047_sql()


def test_app_logs_ts_at_index_is_gone_because_purging_moved_to_ts_raw():
    indexes = _index_columns("app_logs")

    assert "ix_app_logs_ts_at" not in indexes
    assert "ix_app_logs_ts_raw" in indexes
    assert "ix_app_logs_ts_at" in _v047_sql()


def test_v001_carries_no_dropped_index():
    ddl = _v001_sql()

    for dropped in (
        "ix_ban_violations_user",
        "ix_time_academic_year_user_oid",
        "ix_ai_messages_conv_created",
        "ix_app_logs_ts_at",
    ):
        assert f"CREATE INDEX {dropped} " not in ddl


# ---------------------------------------------------------------------------
# 死表下线
# ---------------------------------------------------------------------------


def test_logs_table_is_gone_from_models_and_v001():
    assert "logs" not in models.metadata.tables
    assert "CREATE TABLE logs" not in _v001_sql()


def test_v047_drops_the_logs_table_idempotently():
    sql = _v047_sql()

    assert "DROP TABLE logs" in sql
    text = V047_PATH.read_text(encoding="utf-8")
    # -942: 表不存在（重复执行 / 基线里就没有）。
    assert "IF SQLCODE != -942 THEN RAISE; END IF;" in text


# ---------------------------------------------------------------------------
# created_at 默认值
# ---------------------------------------------------------------------------


def _tables_with_created_at() -> list[str]:
    return [
        name
        for name, table in models.metadata.tables.items()
        if "created_at" in table.c
    ]


def test_every_created_at_column_has_systemtimestamp_except_ai_api_keys():
    with_created_at = _tables_with_created_at()

    assert len(with_created_at) == 27
    defaults = {
        name: models.metadata.tables[name].c.created_at.server_default
        for name in with_created_at
    }
    for name, default in defaults.items():
        assert default is not None, name
        assert "SYSTIMESTAMP" in str(default.arg), name


def test_v001_declares_the_default_on_the_same_columns():
    ddl = _v001_sql()

    assert ddl.count("created_at TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL") == 27


def test_v047_modifies_all_twenty_six_tables():
    sql = _v047_sql()
    modified = [
        name
        for name in _tables_with_created_at()
        if name != "ai_api_keys"
    ]

    assert len(modified) == 26
    for name in modified:
        assert f"ALTER TABLE {name} MODIFY (created_at DEFAULT SYSTIMESTAMP)" in sql
    assert "ALTER TABLE ai_api_keys MODIFY" not in sql


# ---------------------------------------------------------------------------
# 保留窗口：四张只增表
# ---------------------------------------------------------------------------


def test_default_windows():
    assert retention.DEFAULT_BAN_RECORDS_RETENTION_DAYS == 730
    assert retention.DEFAULT_BAN_VIOLATIONS_RETENTION_DAYS == 365
    assert (
        retention.DEFAULT_NOTIFICATION_VERSIONS_RETENTION_DAYS == 90
    )
    assert retention.DEFAULT_FILE_EVENTS_RETENTION_DAYS == 180


@pytest.mark.parametrize(
    "getter",
    [
        retention.ban_records_retention_days,
        retention.ban_violations_retention_days,
        retention.notification_versions_retention_days,
        retention.file_events_retention_days,
    ],
)
@pytest.mark.parametrize("bad", ["far", "3.5", None, 0, -5, object()])
def test_windows_fall_back_on_invalid_values(monkeypatch, getter, bad):
    key = {
        retention.ban_records_retention_days: "BAN_RECORDS_RETENTION_DAYS",
        retention.ban_violations_retention_days: "BAN_VIOLATIONS_RETENTION_DAYS",
        retention.notification_versions_retention_days:
            "NOTIFICATION_VERSIONS_RETENTION_DAYS",
        retention.file_events_retention_days: "FILE_EVENTS_RETENTION_DAYS",
    }[getter]
    monkeypatch.setattr(config, key, bad, raising=False)
    defaults = {
        "BAN_RECORDS_RETENTION_DAYS": 730,
        "BAN_VIOLATIONS_RETENTION_DAYS": 365,
        "NOTIFICATION_VERSIONS_RETENTION_DAYS": 90,
        "FILE_EVENTS_RETENTION_DAYS": 180,
    }

    assert getter() == defaults[key]


@pytest.mark.parametrize("key,value", [("BAN_RECORDS_RETENTION_DAYS", 30),
                                       ("FILE_EVENTS_RETENTION_DAYS", "7")])
def test_windows_honour_positive_config(monkeypatch, key, value):
    monkeypatch.setattr(config, key, value, raising=False)

    getter = {
        "BAN_RECORDS_RETENTION_DAYS": retention.ban_records_retention_days,
        "FILE_EVENTS_RETENTION_DAYS": retention.file_events_retention_days,
    }[key]
    assert getter() == int(value)


def test_append_only_windows_cover_the_four_tables():
    names = [name for name, *_ in retention._APPEND_ONLY_WINDOWS]

    assert names == [
        "ban_records",
        "ban_violations",
        "notification_versions",
        "file_events",
    ]


def test_append_only_purge_sql_is_a_bounded_delete_on_created_at():
    table = models.metadata.tables["ban_records"]

    sql = _sql(retention._purge_append_only_stmt(table, TS, 5000))

    assert sql.upper().startswith("DELETE FROM BAN_RECORDS")
    assert "created_at <" in sql
    assert "ROWNUM <= 5000" in sql
    assert "FETCH FIRST" not in sql.upper()


def test_append_only_count_shares_the_predicate_but_is_not_capped():
    table = models.metadata.tables["file_events"]

    sql = _sql(retention._count_append_only_stmt(table, TS))

    assert sql.startswith("SELECT count(*)")
    assert "created_at <" in sql
    assert "ROWNUM" not in sql.upper()


# ---------------------------------------------------------------------------
# run_append_only_retention：窗口、批量、批次上限
# ---------------------------------------------------------------------------


class _Result:
    def __init__(self, rowcount: int | None = None, scalar: int | None = None) -> None:
        self.rowcount = rowcount
        self._scalar = scalar

    def scalar_one(self) -> int | None:
        return self._scalar


class _FakeSession:
    """DELETE 回放 rowcount 序列，COUNT 一律回固定值。"""

    def __init__(self, rowcounts: list[int], remaining: int = 0) -> None:
        self.rowcounts = list(rowcounts)
        self.remaining = remaining
        self.deletes: list = []
        self.commits = 0

    async def execute(self, stmt) -> _Result:
        if getattr(stmt, "is_select", False):
            return _Result(scalar=self.remaining)
        self.deletes.append(stmt)
        return _Result(rowcount=self.rowcounts.pop(0) if self.rowcounts else 0)

    async def commit(self) -> None:
        self.commits += 1


@asynccontextmanager
async def _scope(session):
    yield session


def _wire(monkeypatch, session: _FakeSession) -> None:
    monkeypatch.setattr(retention, "session_scope", lambda: _scope(session))


def test_run_uses_each_table_window_and_one_shared_batch_size(monkeypatch):
    session = _FakeSession(rowcounts=[0, 0, 0, 0], remaining=2)
    _wire(monkeypatch, session)

    result = asyncio.run(retention.run_append_only_retention(now=TS))

    batch = retention.retention_batch_size()
    expected_days = {
        "ban_records": 730,
        "ban_violations": 365,
        "notification_versions": 90,
        "file_events": 180,
    }
    assert set(result) == set(expected_days)
    for name, days in expected_days.items():
        entry = result[name]
        assert datetime.fromisoformat(entry["cutoff"]) == TS - timedelta(days=days)
        assert entry["deleted"] == 0
        assert entry["remaining"] == 2
    assert len(session.deletes) == 4
    for sql in (_sql(stmt) for stmt in session.deletes):
        # ROWNUM 上限是绑定参数，字面量编译后是具体数字。
        assert f"ROWNUM <= {batch}" in sql
    assert session.commits == 4


def test_run_drains_full_batches_until_a_short_one(monkeypatch):
    batch = retention.retention_batch_size()
    # 第一张表两满批 + 短批，其余三张表立即 0 行。
    session = _FakeSession(rowcounts=[batch, batch, 7, 0, 0, 0])
    _wire(monkeypatch, session)
    monkeypatch.setattr(retention, "_positive_int", lambda key, default: default)

    result = asyncio.run(retention.run_append_only_retention(now=TS))

    assert result["ban_records"]["deleted"] == batch * 2 + 7
    assert result["ban_violations"]["deleted"] == 0
    assert len(session.deletes) == 6


def test_run_respects_the_batch_ceiling(monkeypatch):
    batch = retention.retention_batch_size()
    session = _FakeSession(rowcounts=[batch] * 100)
    _wire(monkeypatch, session)
    monkeypatch.setattr(config, "RETENTION_MAX_BATCHES", 2, raising=False)

    result = asyncio.run(retention.run_append_only_retention(now=TS))

    assert result["ban_records"]["deleted"] == batch * 2
    assert len(session.deletes) == 2 * 4


def test_append_only_result_lands_in_the_job_result_and_audit(monkeypatch):
    """审计行要能看出只增表删了多少：一次 run 仍然只有一行审计。"""
    result = {
        "app_logs": {"cutoff": "x", "deleted": 0, "remaining": 0},
        "export_tasks": {"cutoff": "x", "deleted": 0, "remaining": 0},
        "ai_call_logs": {"cutoff": "x", "deleted": 0, "remaining": 0},
        "ai_agent_events": {"cutoff": "x", "deleted": 0, "remaining": 0},
        "ai_prompt_audit": {"cutoff": "x", "deleted": 0, "remaining": 0},
        "batches": 5,
        "append_only": {
            "ban_records": {"cutoff": "x", "deleted": 3, "remaining": 1},
            "file_events": {"cutoff": "x", "deleted": 4, "remaining": 0},
        },
    }

    message = retention.audit_message(result)

    assert "ban_records 删除 3 行（剩余 1 行）" in message
    assert "file_events 删除 4 行（剩余 0 行）" in message
