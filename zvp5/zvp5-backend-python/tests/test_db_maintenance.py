"""``services/db_maintenance.py`` 的离线用例（不建引擎、不连库）。

覆盖合同要求的六件事：overview 组件级降级、suggestions 五类建议、plan 的 SQL
形状、dry_run 不执行、shrink 被 ADB 拒绝后继续并记 failed、作业完成后调用
``cache.flush_scopes``。

替身形态照 ``tests/test_db_monitor.py``：一个按 SQL 文本分派的假会话，行用普通
元组（``services.db_maintenance._get`` 同时支持元组和 ``Row._mapping``）。
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import DatabaseError

from db import cache
from services import db_maintenance

NOW = datetime(2026, 11, 4, 12, 0, 0)

TABLES_ROWS = [
    ("USERS", 12000, 200, 120, NOW),
    ("OLD_TABLE", 3000, 40, 80, NOW - timedelta(days=90)),
    ("NO_STATS", 100, 2, 50, None),
    ("BIG_NO_INDEX", 20000, 500, 200, NOW),
]
STATS_ROWS = [("USERS", 12000, 200, 120, NOW)]
INDEX_ROWS = [
    ("USERS", "IDX_USERS_NAME", "NORMAL", "NONUNIQUE", "VALID", 2, 12000),
    ("USERS", "IDX_USERS_NAME_COPY", "NORMAL", "NONUNIQUE", "VALID", 2, 12000),
    ("USERS", "IDX_USERS_BAD", "NORMAL", "NONUNIQUE", "UNUSABLE", 1, 12000),
    ("OLD_TABLE", "IDX_OLD_HIGH", "NORMAL", "NONUNIQUE", "VALID", 4, 3000),
]
COLUMN_ROWS = [
    ("IDX_USERS_NAME", 1, "NAME"),
    ("IDX_USERS_NAME_COPY", 1, "NAME"),
    ("IDX_USERS_BAD", 1, "CLASS"),
    ("IDX_OLD_HIGH", 1, "CREATED_AT"),
]
SEGMENT_ROWS = [
    ("USERS", "TABLE", 65536),
    ("IDX_USERS_NAME", "INDEX", 8192),
    ("OLD_TABLE", "TABLE", 4096),
]


class Result:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return list(self._rows)


def denied(code: int = 942):
    return DatabaseError(None, None, Exception(SimpleNamespace(code=code)))


class FakeSession:
    """按 SQL 关键字分派；``fail`` 里的关键字命中就抛权限错误。"""

    def __init__(self, *, fail: tuple[str, ...] = ()) -> None:
        self.fail = fail
        self.calls: list[str] = []
        self.rollbacks = 0

    async def execute(self, statement):
        sql = " ".join(str(statement).split()).lower()
        self.calls.append(sql)
        for needle in self.fail:
            if needle in sql:
                raise denied()
        if "user_tab_statistics" in sql:
            return Result(STATS_ROWS)
        if "user_ind_columns" in sql:
            return Result(COLUMN_ROWS)
        if "user_indexes" in sql:
            return Result(INDEX_ROWS)
        if "user_segments" in sql:
            return Result(SEGMENT_ROWS)
        if "user_tables" in sql:
            return Result(TABLES_ROWS)
        raise AssertionError(f"unexpected SQL: {sql}")

    async def rollback(self):
        self.rollbacks += 1


def scope_factory(session):
    @asynccontextmanager
    async def _scope():
        yield session

    return _scope


@pytest.fixture(autouse=True)
def reset_jobs():
    db_maintenance.reset_for_tests()
    yield
    db_maintenance.reset_for_tests()


@pytest.fixture
def audit_rows(monkeypatch):
    rows: list[dict] = []

    async def insert(**kwargs):
        rows.append(kwargs)

    monkeypatch.setattr(
        db_maintenance.backends,
        "get_applogs_repo",
        lambda session=None: SimpleNamespace(insert=insert),
    )
    return rows


async def _finish_job():
    task = db_maintenance._background_task
    assert task is not None
    await task


# ---------------------------------------------------------------------------
# overview
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_overview_reports_table_facts():
    data = await db_maintenance.overview(FakeSession())

    assert data["available"] is True
    assert data["table_count"] == 4
    by_name = {table["table_name"]: table for table in data["tables"]}
    users = by_name["USERS"]
    assert users["num_rows"] == 12000
    assert users["blocks"] == 200
    assert users["avg_row_len"] == 120
    assert users["last_analyzed"] == NOW.isoformat()
    assert users["index_count"] == 3
    assert users["table_bytes"] == 65536
    assert users["index_bytes"] == 8192
    assert users["total_bytes"] == 73728
    assert set(users) == set(db_maintenance._OVERVIEW_FIELDS)
    assert data["components"]["segments"]["available"] is True


@pytest.mark.asyncio
async def test_overview_degrades_per_component_instead_of_raising():
    session = FakeSession(fail=("user_segments",))
    data = await db_maintenance.overview(session)

    assert data["available"] is True
    assert data["components"]["segments"]["available"] is False
    assert data["components"]["segments"]["reason"] == "insufficient_privileges"
    assert data["warnings"]
    for table in data["tables"]:
        assert table["table_bytes"] is None
        assert table["index_bytes"] is None
        assert table["total_bytes"] is None
        # 其它组件仍然有值：降级是组件级，不是整页
        assert table["index_count"] is not None
    assert session.rollbacks == 1


@pytest.mark.asyncio
async def test_overview_keeps_going_when_user_tables_is_unreadable():
    data = await db_maintenance.overview(FakeSession(fail=("user_tables",)))

    assert data["available"] is False
    assert data["reason"] == "insufficient_privileges"
    # 索引/统计里出现的表仍然能列出来，只是基数未知
    assert {table["table_name"] for table in data["tables"]} >= {"USERS", "OLD_TABLE"}


# ---------------------------------------------------------------------------
# suggestions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_suggestions_cover_all_five_codes_and_sort_by_severity():
    data = await db_maintenance.suggestions(FakeSession())

    codes = {item["code"] for item in data["suggestions"]}
    assert codes == {
        "stats_stale",
        "index_unusable",
        "index_high_blevel",
        "table_large_no_index",
        "duplicate_index",
    }
    assert data["counts"]["high"] >= 2
    severities = [item["severity"] for item in data["suggestions"]]
    assert severities == sorted(
        severities, key=lambda level: db_maintenance._SEVERITY_RANK[level]
    )
    assert all(
        set(item) == {"code", "severity", "table", "message", "action"}
        for item in data["suggestions"]
    )

    stale_tables = {
        item["table"] for item in data["suggestions"] if item["code"] == "stats_stale"
    }
    assert stale_tables == {"OLD_TABLE", "NO_STATS"}
    unusable = [
        item for item in data["suggestions"] if item["code"] == "index_unusable"
    ]
    assert unusable[0]["table"] == "USERS"
    assert "IDX_USERS_BAD" in unusable[0]["message"]
    duplicate = [
        item for item in data["suggestions"] if item["code"] == "duplicate_index"
    ]
    assert duplicate[0]["table"] == "USERS"
    assert "IDX_USERS_NAME_COPY" in duplicate[0]["message"]
    large = [
        item for item in data["suggestions"] if item["code"] == "table_large_no_index"
    ]
    assert large[0]["table"] == "BIG_NO_INDEX"


@pytest.mark.asyncio
async def test_suggestions_skip_duplicates_when_index_columns_unavailable():
    data = await db_maintenance.suggestions(FakeSession(fail=("user_ind_columns",)))

    assert "duplicate_index" not in {item["code"] for item in data["suggestions"]}


# ---------------------------------------------------------------------------
# build_plan
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action,tables,expected",
    [
        (
            "gather_stats",
            ["users"],
            "BEGIN DBMS_STATS.GATHER_TABLE_STATS(ownname=>USER, tabname=>'USERS', "
            "cascade=>TRUE, degree=>4); END;",
        ),
        (
            "shrink_space",
            ["users"],
            'ALTER TABLE "USERS" SHRINK SPACE CASCADE',
        ),
    ],
)
async def test_build_plan_sql_shapes(action, tables, expected):
    plan = await db_maintenance.build_plan(FakeSession(), action, tables)

    assert plan["action"] == action
    assert [item["sql"] for item in plan["items"]] == [expected]
    assert plan["items"][0]["table"] == "USERS"
    assert plan["items"][0]["reason"]


@pytest.mark.asyncio
async def test_build_plan_rebuilds_indexes_quoted_and_online():
    plan = await db_maintenance.build_plan(FakeSession(), "rebuild_indexes", ["users"])

    sqls = [item["sql"] for item in plan["items"]]
    assert 'ALTER INDEX "IDX_USERS_NAME" REBUILD ONLINE' in sqls
    assert all("REBUILD ONLINE" in sql for sql in sqls)
    # 显式点名表 → 该表全部索引；无 blevel/status 过滤
    assert len(sqls) == 3


@pytest.mark.asyncio
async def test_rebuild_plan_without_tables_targets_flagged_indexes_only():
    plan = await db_maintenance.build_plan(FakeSession(), "rebuild_indexes")

    sqls = sorted(item["sql"] for item in plan["items"])
    assert sqls == [
        'ALTER INDEX "IDX_OLD_HIGH" REBUILD ONLINE',
        'ALTER INDEX "IDX_USERS_BAD" REBUILD ONLINE',
    ]


@pytest.mark.asyncio
async def test_gather_stats_without_tables_targets_stale_ones():
    plan = await db_maintenance.build_plan(FakeSession(), "gather_stats")

    assert sorted(item["table"] for item in plan["items"]) == ["NO_STATS", "OLD_TABLE"]


@pytest.mark.asyncio
async def test_purge_and_cache_actions_produce_their_own_plan():
    purge = await db_maintenance.build_plan(None, "purge_recyclebin")
    assert len(purge["items"]) == 1
    assert purge["items"][0]["sql"] == "PURGE RECYCLEBIN"
    assert purge["items"][0]["table"] is None

    for action in ("flush_cache", "warm_cache"):
        plan = await db_maintenance.build_plan(None, action)
        assert plan["items"] == []
        assert plan["note"]


@pytest.mark.asyncio
async def test_build_plan_rejects_bad_input():
    with pytest.raises(db_maintenance.MaintenanceError) as unknown_action:
        await db_maintenance.build_plan(FakeSession(), "drop_everything")
    assert unknown_action.value.status_code == 400

    with pytest.raises(db_maintenance.MaintenanceError) as bad_table:
        await db_maintenance.build_plan(FakeSession(), "gather_stats", ["1bad"])
    assert bad_table.value.status_code == 400

    with pytest.raises(db_maintenance.MaintenanceError) as missing_table:
        await db_maintenance.build_plan(FakeSession(), "gather_stats", ["GHOST"])
    assert missing_table.value.status_code == 400

    with pytest.raises(db_maintenance.MaintenanceError) as too_many:
        await db_maintenance.build_plan(
            FakeSession(), "gather_stats", [f"T{index}" for index in range(51)]
        )
    assert too_many.value.status_code == 400


@pytest.mark.asyncio
async def test_shrink_requires_explicit_tables():
    with pytest.raises(db_maintenance.MaintenanceError) as excinfo:
        await db_maintenance.build_plan(FakeSession(), "shrink_space", [])
    assert excinfo.value.status_code == 400
    assert "tables" in excinfo.value.detail


# ---------------------------------------------------------------------------
# 后台作业
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dry_run_plans_without_executing(monkeypatch, audit_rows):
    session = FakeSession()
    monkeypatch.setattr(db_maintenance, "session_scope", scope_factory(session))
    executed: list[str] = []

    async def should_not_run(sql):
        executed.append(sql)

    monkeypatch.setattr(db_maintenance, "_execute_statement", should_not_run)

    started = db_maintenance.start_job("gather_stats", ["USERS"], True, "admin")
    assert set(started) == {"job_id", "started_at", "state"}
    assert started["state"] == "running"
    await _finish_job()

    snapshot = db_maintenance.progress_snapshot()
    assert snapshot["running"] is False
    assert snapshot["phase"] == "done"
    assert snapshot["result"]["dry_run"] is True
    assert snapshot["result"]["items"]
    assert executed == []
    assert snapshot["result"]["executed"] == 0


@pytest.mark.asyncio
async def test_dry_run_does_not_invalidate_cache(monkeypatch, audit_rows):
    monkeypatch.setattr(db_maintenance, "session_scope", scope_factory(FakeSession()))
    flushes: list[list[str]] = []

    async def flush_scopes(scopes):
        flushes.append(list(scopes))
        return {scope: 1 for scope in scopes}

    monkeypatch.setattr(
        db_maintenance.cache, "flush_scopes", flush_scopes, raising=False
    )

    db_maintenance.start_job("gather_stats", ["USERS"], True, "admin")
    await _finish_job()

    snapshot = db_maintenance.progress_snapshot()
    assert flushes == []
    assert snapshot["result"]["cache"] == {
        "scopes": [],
        "flushed": {},
        "versions": {},
    }
    assert "预演未失效缓存" in snapshot["result"]["note"]


@pytest.mark.asyncio
async def test_shrink_rejected_by_adb_continues_and_records_failed(
    monkeypatch, audit_rows
):
    session = FakeSession()
    monkeypatch.setattr(db_maintenance, "session_scope", scope_factory(session))
    attempts: list[str] = []

    async def execute(sql):
        attempts.append(sql)
        if '"USERS"' in sql:
            # ADB 对 SHRINK SPACE 的典型拒绝
            raise DatabaseError(
                None, None, Exception(SimpleNamespace(code=65040))
            )

    monkeypatch.setattr(db_maintenance, "_execute_statement", execute)

    db_maintenance.start_job(
        "shrink_space", ["USERS", "OLD_TABLE"], False, "admin"
    )
    await _finish_job()

    snapshot = db_maintenance.progress_snapshot()
    assert len(attempts) == 2  # 第一张失败后仍然执行第二张
    assert snapshot["progress"] == {
        "total": 2,
        "done": 1,
        "failed": 1,
        "current": None,
    }
    assert snapshot["result"]["executed"] == 1
    assert snapshot["result"]["failed"] == 1
    assert snapshot["result"]["errors"] == [{"table": "USERS", "error": "ORA-65040"}]
    assert snapshot["phase"] == "done"


@pytest.mark.asyncio
async def test_job_marks_failed_when_every_item_fails(monkeypatch, audit_rows):
    monkeypatch.setattr(db_maintenance, "session_scope", scope_factory(FakeSession()))

    async def always_fail(sql):
        raise DatabaseError(None, None, Exception(SimpleNamespace(code=65040)))

    monkeypatch.setattr(db_maintenance, "_execute_statement", always_fail)

    db_maintenance.start_job("shrink_space", ["USERS"], False, "admin")
    await _finish_job()

    snapshot = db_maintenance.progress_snapshot()
    assert snapshot["phase"] == "failed"
    assert snapshot["progress"]["failed"] == 1
    assert snapshot["result"]["errors"]


@pytest.mark.asyncio
async def test_job_flushes_cache_scopes_and_writes_warning_audit(
    monkeypatch, audit_rows
):
    monkeypatch.setattr(db_maintenance, "session_scope", scope_factory(FakeSession()))
    executed: list[str] = []

    async def execute(sql):
        executed.append(sql)

    monkeypatch.setattr(db_maintenance, "_execute_statement", execute)

    flushes: list[list[str]] = []

    async def flush_scopes(scopes):
        flushes.append(list(scopes))
        return {scope: 3 for scope in scopes}

    monkeypatch.setattr(
        db_maintenance.cache, "flush_scopes", flush_scopes, raising=False
    )

    db_maintenance.start_job("gather_stats", ["USERS"], False, "admin")
    await _finish_job()

    assert len(executed) == 1
    assert flushes == [["users", "groups", "activities", "conversations"]]
    snapshot = db_maintenance.progress_snapshot()
    assert snapshot["result"]["cache"]["scopes"] == list(
        db_maintenance.DEFAULT_FLUSH_SCOPES
    )
    assert audit_rows and audit_rows[0]["ltype"] == db_maintenance.AUDIT_TYPE
    assert audit_rows[0]["level"] == db_maintenance.AUDIT_LEVEL
    assert audit_rows[0]["user"] == "admin"


@pytest.mark.asyncio
async def test_busy_job_is_rejected(monkeypatch, audit_rows):
    monkeypatch.setattr(db_maintenance, "session_scope", scope_factory(FakeSession()))
    gate = asyncio.Event()

    async def slow(sql):
        await gate.wait()

    monkeypatch.setattr(db_maintenance, "_execute_statement", slow)

    db_maintenance.start_job("gather_stats", ["USERS"], False, "admin")
    # 让作业推进到执行阶段（planning 里有 await）
    for _ in range(5):
        await asyncio.sleep(0)

    with pytest.raises(db_maintenance.JobBusy):
        db_maintenance.start_job("gather_stats", ["USERS"], False, "admin")

    gate.set()
    await _finish_job()
    assert db_maintenance.progress_snapshot()["running"] is False


@pytest.mark.asyncio
async def test_warm_cache_refreshes_snapshots_after_flush(monkeypatch, audit_rows):
    calls: list[str] = []

    async def flush_scopes(scopes):
        calls.append("flush")
        return {}

    async def refresh_settings(**kwargs):
        calls.append(("settings", kwargs.get("force")))

    async def refresh_roles(**kwargs):
        calls.append(("roles", kwargs.get("force")))

    monkeypatch.setattr(
        db_maintenance.cache, "flush_scopes", flush_scopes, raising=False
    )
    monkeypatch.setattr(
        db_maintenance.settings_runtime, "refresh", refresh_settings
    )
    monkeypatch.setattr(db_maintenance.roles_policy, "refresh", refresh_roles)

    db_maintenance.start_job("warm_cache", None, False, "admin")
    await _finish_job()

    assert calls == ["flush", ("settings", True), ("roles", True)]
    snapshot = db_maintenance.progress_snapshot()
    assert snapshot["result"]["warm"] == {"settings": True, "roles": True}


@pytest.mark.asyncio
async def test_refresh_snapshots_always_forces_reload(monkeypatch):
    """版本闸在版本号没变时会跳过；显式刷新必须 force=True 才真的回源。"""
    calls: list[tuple[str, bool | None]] = []

    async def fake_refresh(*, force: bool = False):
        calls.append(("refresh", force))

    monkeypatch.setattr(db_maintenance.settings_runtime, "refresh", fake_refresh)
    monkeypatch.setattr(db_maintenance.roles_policy, "refresh", fake_refresh)

    result = await db_maintenance.refresh_snapshots()

    assert result == {"settings": True, "roles": True}
    assert calls == [("refresh", True), ("refresh", True)]


# ---------------------------------------------------------------------------
# 缓存域集合：维护面与 db.cache 的键空间必须一一对应
# ---------------------------------------------------------------------------


def test_cache_scopes_stay_in_sync_with_the_prefix_table():
    """``CACHE_SCOPES`` 漏一个域 = 维护动作永远刷不到那类 key。"""
    assert tuple(db_maintenance.CACHE_SCOPES) == tuple(cache.SCOPE_PREFIXES)
    assert set(db_maintenance.DEFAULT_FLUSH_SCOPES) <= set(db_maintenance.CACHE_SCOPES)
    assert "notifications" in db_maintenance.CACHE_SCOPES
    assert "changelog" in db_maintenance.CACHE_SCOPES


def test_scope_prefixes_cover_the_notification_and_changelog_keys():
    notifications = cache.SCOPE_PREFIXES["notifications"]
    changelog = cache.SCOPE_PREFIXES["changelog"]

    assert "zvms:notif:0:active".startswith(f"{cache.NS}:{notifications[0]}")
    assert "zvms:changelog:0:all".startswith(f"{cache.NS}:{changelog[0]}")


@pytest.mark.asyncio
async def test_flush_cache_accepts_notification_and_changelog_scopes(monkeypatch):
    seen: list[list[str]] = []

    async def fake_flush(scopes):
        seen.append(list(scopes))
        return {scope: 0 for scope in scopes}

    monkeypatch.setattr(db_maintenance.cache, "flush_scopes", fake_flush, raising=False)

    result = await db_maintenance.flush_cache(["notifications", "changelog"])

    assert seen == [["notifications", "changelog"]]
    assert result["scopes"] == ["notifications", "changelog"]


@pytest.mark.asyncio
async def test_flush_cache_job_covers_every_scope(monkeypatch, audit_rows):
    seen: list[list[str]] = []

    async def fake_flush(scopes):
        seen.append(list(scopes))
        return {scope: 0 for scope in scopes}

    monkeypatch.setattr(db_maintenance.cache, "flush_scopes", fake_flush, raising=False)

    db_maintenance.start_job("flush_cache", None, False, "admin")
    await _finish_job()

    assert seen == [list(db_maintenance.CACHE_SCOPES)]
