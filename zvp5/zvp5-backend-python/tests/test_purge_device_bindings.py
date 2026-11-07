"""``scripts/purge_legacy_device_bindings.py`` 的离线契约。

钉住四件一旦写错就会**不可逆**的事：

* 删除必须带时间下界：``--apply`` 缺 ``--before`` 直接拒绝执行（不再全表清空）；
* count / preview / 备份导出 / delete 共用同一个谓词，不能各算各的；
* ``--apply`` 先导出备份，导出失败即中止，绝不带着没有备份的 DELETE 继续；
* 表不存在时友好退出，不让 ORA-00942 直接冒泡。

表用同名列的 SQLite ``Table`` 替身（Oracle 的 ``VARCHAR2``/``TIMESTAMP`` 在
SQLite 上编译不了），会话用同步连接包一层最小 async 外壳；不连 Oracle。
"""

from __future__ import annotations

import importlib.util
import json
import sys
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import (
    Column,
    DateTime,
    MetaData,
    String,
    Table,
    create_engine,
)

SCRIPT_PATH = (
    Path(__file__).resolve().parents[1] / "scripts" / "purge_legacy_device_bindings.py"
)
_spec = importlib.util.spec_from_file_location("purge_legacy_device_bindings", SCRIPT_PATH)
purge = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = purge
_spec.loader.exec_module(purge)

BEFORE = datetime(2026, 9, 1, 0, 0, 0)


class _Result:
    def __init__(self, rows, rowcount: int = -1) -> None:
        self._rows = rows
        self.rowcount = rowcount

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None

    def scalar_one(self):
        return self._rows[0][0]


class _Session:
    """最小 async 外壳：脚本只用到 ``execute`` 与 ``commit``。"""

    def __init__(self, conn) -> None:
        self._conn = conn

    async def execute(self, stmt):
        result = self._conn.execute(stmt)
        rows = result.fetchall() if result.returns_rows else []
        return _Result(rows, result.rowcount)

    async def commit(self) -> None:
        self._conn.commit()


def _clone_table() -> Table:
    return Table(
        "user_device_bindings",
        MetaData(),
        Column("oid", String(24), primary_key=True),
        Column("user_oid", String(24), nullable=False),
        Column("xuehai_id", String(32), nullable=False),
        Column("device_id", String(64), nullable=True),
        Column("source", String(16), nullable=False),
        Column("status", String(16), nullable=False),
        Column("note", String(256), nullable=True),
        Column("created_at", DateTime, nullable=False),
        Column("updated_at", DateTime, nullable=False),
    )


@pytest.fixture
def env(monkeypatch, tmp_path):
    engine = create_engine("sqlite://")
    table = _clone_table()
    table.create(engine)
    conn = engine.connect()
    monkeypatch.setattr(purge, "bindings_tbl", table)

    @asynccontextmanager
    async def fake_scope():
        yield _Session(conn)

    monkeypatch.setattr(purge, "session_scope", fake_scope)

    async def table_exists(_session):
        return True

    monkeypatch.setattr(purge, "_table_exists", table_exists)

    state = SimpleNamespace(
        conn=conn,
        table=table,
        backup_dir=tmp_path / "backups",
        insert=_inserter(conn, table),
        count=_counter(conn, table),
    )
    yield state
    conn.close()
    engine.dispose()


def _inserter(conn, table):
    def insert(oid: str, created_at: datetime, status: str = "active") -> None:
        conn.execute(
            table.insert().values(
                oid=oid,
                user_oid="000000000000000000000001",
                xuehai_id="20260001",
                device_id=None,
                source="auto",
                status=status,
                note=None,
                created_at=created_at,
                updated_at=created_at,
            )
        )
        conn.commit()

    return insert


def _counter(conn, table):
    from sqlalchemy import func, select

    def count() -> int:
        return int(conn.execute(select(func.count()).select_from(table)).scalar_one())

    return count


# ---------------------------------------------------------------------------
# 参数解析
# ---------------------------------------------------------------------------


def test_parse_before_is_midnight_of_that_day() -> None:
    assert purge._parse_before("2026-09-01") == datetime(2026, 9, 1, 0, 0, 0)


def test_parse_before_rejects_bad_format() -> None:
    import argparse

    with pytest.raises(argparse.ArgumentTypeError):
        purge._parse_before("2026/09/01")


def test_apply_without_before_is_refused_before_touching_the_db(env) -> None:
    env.insert("0000000000000000000000a1", datetime(2020, 1, 1))

    with pytest.raises(SystemExit) as failure:
        purge.main(["--apply"])

    assert failure.value.code == 2
    assert env.count() == 1


def test_run_itself_refuses_apply_without_before(env, capsys) -> None:
    """双保险：绕过 main()（例如被别的脚本调用）也不能无下界删除。"""
    env.insert("0000000000000000000000a1", datetime(2020, 1, 1))

    import asyncio

    assert asyncio.run(purge._run(True, None, env.backup_dir)) == 2
    assert "拒绝执行" in capsys.readouterr().out
    assert env.count() == 1


# ---------------------------------------------------------------------------
# 谓词
# ---------------------------------------------------------------------------


def test_predicate_is_empty_only_without_before() -> None:
    assert purge._predicate(None) == []
    assert len(purge._predicate(BEFORE)) == 1


def test_delete_stmt_always_carries_the_shared_predicate() -> None:
    """结构保证：delete 与 count 用的是同一组条件，且不是无条件 DELETE。"""
    where = purge._predicate(BEFORE)

    delete_sql = str(purge._delete_stmt(where))
    count_sql = str(purge._count_stmt(where))
    export_sql = str(purge._export_stmt(where))

    for sql in (delete_sql, count_sql, export_sql):
        assert "created_at <" in sql


# ---------------------------------------------------------------------------
# dry-run
# ---------------------------------------------------------------------------


def test_dry_run_without_before_previews_whole_table(env, capsys) -> None:
    env.insert("0000000000000000000000a1", datetime(2020, 1, 1))
    env.insert("0000000000000000000000a2", datetime(2026, 10, 1))

    import asyncio

    assert asyncio.run(purge._run(False, None, env.backup_dir)) == 0

    out = capsys.readouterr().out
    assert "未提供 --before，仅预览全表" in out
    assert "待删除 2 行" in out
    assert env.count() == 2
    assert not env.backup_dir.exists()


def test_dry_run_with_before_only_counts_older_rows(env, capsys) -> None:
    env.insert("0000000000000000000000a1", datetime(2020, 1, 1))
    env.insert("0000000000000000000000a2", datetime(2026, 9, 1))  # 边界当天
    env.insert("0000000000000000000000a3", datetime(2026, 10, 1))

    import asyncio

    assert asyncio.run(purge._run(False, BEFORE, env.backup_dir)) == 0

    out = capsys.readouterr().out
    assert "删除条件：created_at < 2026-09-01 00:00:00" in out
    assert "待删除 1 行" in out
    assert env.count() == 3


def test_masked_preview_does_not_print_full_identifiers(env, capsys) -> None:
    env.insert("0000000000000000000000a1", datetime(2020, 1, 1))

    import asyncio

    asyncio.run(purge._run(False, BEFORE, env.backup_dir))
    out = capsys.readouterr().out
    assert "0000000000000000000000a1" not in out
    assert "00a1" in out


# ---------------------------------------------------------------------------
# apply + 备份
# ---------------------------------------------------------------------------


def test_apply_deletes_only_rows_before_cutoff_and_backs_them_up(env, capsys) -> None:
    old = ["0000000000000000000000a1", "0000000000000000000000a2"]
    env.insert(old[0], datetime(2020, 1, 1), status="revoked")
    env.insert(old[1], datetime(2026, 8, 31, 23, 59, 59))
    env.insert("0000000000000000000000a3", datetime(2026, 9, 1))
    env.insert("0000000000000000000000a4", datetime(2026, 10, 1))

    import asyncio

    assert asyncio.run(purge._run(True, BEFORE, env.backup_dir)) == 0

    out = capsys.readouterr().out
    assert "待删除 2 行" in out
    assert "已删除 2 行" in out
    assert env.count() == 2  # 边界当天与之后的行都留着

    backups = list(env.backup_dir.glob("purge_device_bindings_*.json"))
    assert len(backups) == 1
    assert str(backups[0]) in out

    payload = json.loads(backups[0].read_text(encoding="utf-8"))
    assert payload["table"] == "USER_DEVICE_BINDINGS"
    assert payload["before"] == "2026-09-01T00:00:00"
    assert payload["predicate"] == "created_at < 2026-09-01T00:00:00"
    assert payload["row_count"] == 2
    assert {row["oid"] for row in payload["rows"]} == set(old)
    # 全列快照：删掉的 revoked 记录必须还能从备份里看到。
    assert {row["status"] for row in payload["rows"]} == {"active", "revoked"}


def test_backup_dir_override_is_honoured(env) -> None:
    env.insert("0000000000000000000000a1", datetime(2020, 1, 1))
    target = env.backup_dir.parent / "custom_backups"

    import asyncio

    assert asyncio.run(purge._run(True, BEFORE, target)) == 0
    assert len(list(target.glob("*.json"))) == 1


def test_backup_failure_aborts_delete(env, monkeypatch, capsys) -> None:
    env.insert("0000000000000000000000a1", datetime(2020, 1, 1))

    def boom(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(purge, "_write_backup", boom)

    import asyncio

    assert asyncio.run(purge._run(True, BEFORE, env.backup_dir)) == 1

    captured = capsys.readouterr()
    assert "备份失败，已中止删除" in captured.err
    assert "disk full" in captured.err
    assert env.count() == 1  # 一行都没删


def test_nothing_to_delete_skips_backup(env, capsys) -> None:
    env.insert("0000000000000000000000a1", datetime(2026, 10, 1))

    import asyncio

    assert asyncio.run(purge._run(True, BEFORE, env.backup_dir)) == 0
    assert "没有可删除的行" in capsys.readouterr().out
    assert not env.backup_dir.exists()
    assert env.count() == 1


# ---------------------------------------------------------------------------
# 表存在性
# ---------------------------------------------------------------------------


class _ScalarSession:
    def __init__(self, value: int) -> None:
        self._value = value
        self.statements: list = []

    async def execute(self, stmt):
        self.statements.append(stmt)
        return _Result([(self._value,)])


@pytest.mark.asyncio
async def test_table_exists_reads_the_data_dictionary() -> None:
    session = _ScalarSession(1)
    assert await purge._table_exists(session) is True
    assert session.statements  # 发的是 user_tables 查询，不是直查业务表


@pytest.mark.asyncio
async def test_missing_table_exits_friendly(monkeypatch, capsys) -> None:
    async def table_exists(_session):
        return False

    monkeypatch.setattr(purge, "_table_exists", table_exists)

    assert await purge._run(True, BEFORE, Path("/tmp/unused")) == 0
    out = capsys.readouterr().out
    assert "不在当前 schema 里" in out
