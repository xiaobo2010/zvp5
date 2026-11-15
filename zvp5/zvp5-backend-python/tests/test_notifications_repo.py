"""通知数据层的离线 SQL / 行映射契约（不连库）。

钉住三件最容易写错、且一旦错了会静默破坏客户端语义的事：

* 创建语句必须覆盖所有必填列（尤其不可变发布人与时间）；
* 编辑语句必须 ``version = version + 1``，且绝不覆盖 ``author_*`` /
  ``created_at``；
* 全局版本计数器是单行 ``id = 1`` 的 ``version = version + 1``，与业务写同一
  事务；未知 oid 不触发任何 bump。

Nothing is executed: statements are compiled against the Oracle dialect with
values inlined, no engine, no connection.

Run from the repository root::

    ./.venv/bin/python -m pytest tests/test_notifications_repo.py -q
"""

from __future__ import annotations

import asyncio
from datetime import datetime

import pytest
from sqlalchemy.dialects import oracle

from db import cache
from db.models import notification_state as state_tbl
from db.models import notifications as notifications_tbl
from db.repos.oracle import notifications as notifications_repo

OID = "0d00000000000000000000aa"
AUTHOR_OID = "000000000000000000000001"
NOW = datetime(2026, 10, 21, 9, 30, 0)

DIALECT = oracle.dialect()


def _sql(stmt) -> str:
    """Compile with bound values inlined; whitespace collapsed for matching."""
    return " ".join(
        str(stmt.compile(dialect=DIALECT, compile_kwargs={"literal_binds": True})).split()
    )


# ---------------------------------------------------------------------------
# INSERT
# ---------------------------------------------------------------------------


def test_create_stmt_covers_every_required_column() -> None:
    stmt = notifications_repo._create_stmt(
        OID,
        "活动调整",
        "本周活动改到周六。",
        AUTHOR_OID,
        "张老师",
        NOW,
    )
    provided = {
        (key if isinstance(key, str) else key.name).lower() for key in stmt._values
    }
    required = {
        col.name.lower()
        for col in notifications_tbl.c
        if not col.nullable and col.server_default is None
    }

    assert required - provided == set()
    assert {"oid", "title", "content", "author_oid", "author_name", "version",
            "created_at", "updated_at"} <= provided


def test_create_stmt_starts_at_version_one_and_same_timestamps() -> None:
    sql = _sql(
        notifications_repo._create_stmt(
            OID, "标题", "正文", AUTHOR_OID, "张老师", NOW
        )
    )
    assert sql.startswith("INSERT INTO notifications ")
    assert "'张老师'" in sql
    assert f"'{AUTHOR_OID}'" in sql
    assert "version" in sql
    # 创建时两列同源，不能各取一次 now 而漂移。
    assert sql.count("2026-10-21 09:30:00") == 2


# ---------------------------------------------------------------------------
# UPDATE
# ---------------------------------------------------------------------------


def test_update_stmt_bumps_notice_version_and_keeps_author_immutable() -> None:
    sql = _sql(
        notifications_repo._update_stmt(OID, {"title": "新标题"}, NOW)
    )

    assert sql.startswith("UPDATE notifications SET ")
    assert "version=(notifications.version + 1)" in sql
    assert "updated_at=" in sql
    assert "title=" in sql
    # 发布人与发送时间不属于可编辑字段。
    for column in ("author_oid", "author_name", "created_at"):
        assert f"{column}=" not in sql
    assert f"WHERE notifications.oid = '{OID}'" in sql


def test_update_stmt_patches_only_given_fields() -> None:
    sql = _sql(
        notifications_repo._update_stmt(OID, {"active": 0}, NOW)
    )
    assert "active=0" in sql
    assert "title=" not in sql
    assert "content=" not in sql


def test_unknown_oid_update_does_not_bump_state() -> None:
    class EmptyResult:
        rowcount = 0

        def first(self):
            return None

    class FakeSession:
        def __init__(self):
            self.calls: list[str] = []

        async def execute(self, stmt):
            self.calls.append(_sql(stmt))
            return EmptyResult()

        async def commit(self):
            self.calls.append("COMMIT")

    import asyncio

    session = FakeSession()
    repo = notifications_repo.OracleNotificationsRepo(session=session)
    row, version = asyncio.run(repo.update(OID, title="新标题"))

    assert row is None
    assert version == 0
    assert not any(
        "UPDATE notification_state SET" in call for call in session.calls
    )
    assert "COMMIT" not in session.calls  # injected session is caller-owned


# ---------------------------------------------------------------------------
# GLOBAL VERSION
# ---------------------------------------------------------------------------


def test_state_bump_is_a_single_row_increment() -> None:
    sql = _sql(notifications_repo._bump_state_stmt(NOW))

    assert sql.startswith("UPDATE notification_state SET ")
    assert "version=(notification_state.version + 1)" in sql
    assert "updated_at=" in sql
    assert "WHERE notification_state.id = 1" in sql


def test_state_select_targets_id_one() -> None:
    sql = _sql(notifications_repo._state_select_stmt())
    assert "FROM notification_state" in sql
    assert "notification_state.id = 1" in sql


def test_state_columns_match_singleton_design() -> None:
    assert [col.name for col in state_tbl.c] == ["id", "version", "updated_at"]
    assert state_tbl.c.id.primary_key


# ---------------------------------------------------------------------------
# LIST / GET / DELETE
# ---------------------------------------------------------------------------


def test_active_list_filters_and_orders_newest_first() -> None:
    sql = _sql(notifications_repo._list_active_stmt())

    assert "FROM notifications" in sql
    assert "notifications.active = 1" in sql
    assert "ORDER BY notifications.created_at DESC, notifications.oid DESC" in sql


def test_active_list_hides_changelog_synced_notices() -> None:
    """CHANGELOG 同步出来的公告不进用户侧列表（否则每发一版多一条，淹掉真公告）。"""
    active = _sql(notifications_repo._list_active_stmt())
    admin = _sql(notifications_repo._list_all_stmt())

    assert "notifications.source_ref IS NULL" in active
    assert "notifications.source_ref IS NULL" not in admin


def test_active_list_is_bounded_to_the_latest_100() -> None:
    """历史公告只增不减；用户侧列表必须有行数上限，且截断的是最新一批。"""
    sql = _sql(notifications_repo._list_active_stmt())

    assert "FETCH FIRST 100 ROWS ONLY" in sql
    assert sql.index("ORDER BY notifications.created_at DESC") < sql.index(
        "FETCH FIRST 100 ROWS ONLY"
    )


def test_admin_list_has_no_active_filter() -> None:
    sql = _sql(notifications_repo._list_all_stmt())
    assert "FROM notifications" in sql
    assert "notifications.active = 1" not in sql
    assert "ORDER BY notifications.created_at DESC, notifications.oid DESC" in sql


def test_delete_selects_the_row_for_update_before_removing() -> None:
    select_sql = _sql(notifications_repo._get_for_update_stmt(OID))
    delete_sql = _sql(notifications_repo._delete_stmt(OID))

    assert "FOR UPDATE" in select_sql
    assert f"notifications.oid = '{OID}'" in select_sql
    assert delete_sql.startswith("DELETE FROM notifications ")
    assert f"notifications.oid = '{OID}'" in delete_sql


# ---------------------------------------------------------------------------
# ROW MAPPING
# ---------------------------------------------------------------------------


def test_row_maps_flags_and_numeric_versions() -> None:
    row = notifications_repo._row(
        {
            "oid": OID,
            "title": "标题",
            "content": "正文",
            "active": 1,
            "author_oid": AUTHOR_OID,
            "author_name": "张老师",
            "version": 3,
            "created_at": NOW,
            "updated_at": NOW,
        }
    )

    assert row["_id"] == OID
    assert row["active"] is True
    assert row["version"] == 3
    assert row["author_oid"] == AUTHOR_OID
    assert row["author_name"] == "张老师"


@pytest.mark.parametrize("active", [0, 1])
def test_row_maps_active_number_to_bool(active: int) -> None:
    row = notifications_repo._row(
        {
            "oid": OID,
            "title": "标题",
            "content": "正文",
            "active": active,
            "author_oid": AUTHOR_OID,
            "author_name": "张老师",
            "version": 1,
            "created_at": NOW,
            "updated_at": NOW,
        }
    )
    assert row["active"] is bool(active)


# ---------------------------------------------------------------------------
# CONTENT HASH / VERSION SNAPSHOTS
# ---------------------------------------------------------------------------


def test_content_hash_is_stable_sha256_hex() -> None:
    first = notifications_repo.notification_content_hash("标题", "正文", True)
    second = notifications_repo.notification_content_hash("标题", "正文", True)

    assert first == second
    assert len(first) == 64
    assert first == first.lower()
    assert all(ch in "0123456789abcdef" for ch in first)


def test_content_hash_covers_every_field_including_active() -> None:
    base = notifications_repo.notification_content_hash("标题", "正文", True)

    assert notifications_repo.notification_content_hash("新标题", "正文", True) != base
    assert notifications_repo.notification_content_hash("标题", "新正文", True) != base
    assert notifications_repo.notification_content_hash("标题", "正文", False) != base


def test_content_hash_matches_contract_algorithm() -> None:
    import hashlib

    expected = hashlib.sha256("标题\n正文\n0".encode("utf-8")).hexdigest()
    assert (
        notifications_repo.notification_content_hash("标题", "正文", False) == expected
    )


def test_snapshot_stmt_carries_full_content_and_hash() -> None:
    snapshot = notifications_repo._snapshot_values(
        notification_oid=OID,
        version=2,
        title="新标题",
        content="新正文",
        active=False,
        op="update",
        author_oid=AUTHOR_OID,
        author_name="张老师",
        created_at=NOW,
    )
    sql = _sql(notifications_repo._snapshot_insert_stmt(snapshot))

    assert sql.startswith("INSERT INTO notification_versions ")
    assert f"'{OID}'" in sql
    assert "'update'" in sql
    assert "'新正文', 0, 'update'" in sql
    assert snapshot["content_hash"] in sql
    assert snapshot["content_hash"] == notifications_repo.notification_content_hash(
        "新标题", "新正文", False
    )
    assert ", 'update', NULL," in sql


def test_snapshot_stmt_records_rollback_target() -> None:
    snapshot = notifications_repo._snapshot_values(
        notification_oid=OID,
        version=3,
        title="标题",
        content="正文",
        active=True,
        op="rollback",
        author_oid=AUTHOR_OID,
        author_name="张老师",
        created_at=NOW,
        source_version=1,
    )
    sql = _sql(notifications_repo._snapshot_insert_stmt(snapshot))

    assert "1, 'rollback', 1," in sql


def test_version_history_reads_newest_first() -> None:
    sql = _sql(notifications_repo._list_versions_stmt(OID))

    assert "FROM notification_versions" in sql
    assert f"notification_versions.notification_oid = '{OID}'" in sql
    assert "ORDER BY notification_versions.version DESC" in sql


def test_single_version_lookup_is_scoped_to_the_notice() -> None:
    sql = _sql(notifications_repo._get_version_stmt(OID, 2))

    assert "FROM notification_versions" in sql
    assert f"notification_versions.notification_oid = '{OID}'" in sql
    assert "notification_versions.version = 2" in sql


def test_max_version_is_per_notice() -> None:
    sql = _sql(notifications_repo._max_version_stmt(OID))

    assert "max(notification_versions.version)" in sql
    assert f"notification_versions.notification_oid = '{OID}'" in sql


def test_version_row_maps_flags_and_numeric_versions() -> None:
    row = notifications_repo._version_row(
        {
            "oid": "0e00000000000000000000aa",
            "notification_oid": OID,
            "version": 2,
            "title": "标题",
            "content": "正文",
            "active": 0,
            "op": "rollback",
            "source_version": 1,
            "content_hash": "a" * 64,
            "author_oid": AUTHOR_OID,
            "author_name": "张老师",
            "created_at": NOW,
        }
    )

    assert row["_id"] == "0e00000000000000000000aa"
    assert row["notification_oid"] == OID
    assert row["version"] == 2
    assert row["active"] is False
    assert row["source_version"] == 1
    assert row["content_hash"] == "a" * 64
    assert row["created_at"] == NOW


# ---------------------------------------------------------------------------
# 已读状态（V053）
# ---------------------------------------------------------------------------


class _Rows:
    """最小结果集桩：``first()`` / ``all()`` / ``rowcount``，不连库。"""

    rowcount = 1

    def __init__(self, rows) -> None:
        self._rows = rows

    def first(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return self._rows


class _MappingRow:
    def __init__(self, mapping) -> None:
        self._mapping = mapping


#: 一条完整的通知行映射：``_row()`` 会读取除 ``source_ref`` 外的全部列。
_NOTICE_ROW = {
    "oid": OID,
    "title": "标题",
    "content": "正文",
    "active": 1,
    "author_oid": AUTHOR_OID,
    "author_name": "张老师",
    "version": 3,
    "source_ref": None,
    "created_at": NOW,
    "updated_at": NOW,
}


class _ReadSession:
    """按语句形状返回结果的会话桩，并记录每条 SQL 与绑定参数。"""

    def __init__(self, *, notice_version=None, read_rows=()) -> None:
        self.notice_version = notice_version
        self.read_rows = list(read_rows)
        self.calls: list[tuple[str, dict]] = []
        self.commits = 0

    async def execute(self, stmt, params=None):
        sql = _sql(stmt) if params is None else " ".join(str(stmt).split())
        self.calls.append((sql, dict(params or {})))
        if params is not None:
            return _Rows([])
        if "FROM notifications" in sql:
            if self.notice_version is None:
                return _Rows([])
            row = dict(_NOTICE_ROW, version=self.notice_version)
            return _Rows([_MappingRow(row)])
        return _Rows(self.read_rows)

    async def commit(self) -> None:
        self.commits += 1


def test_list_read_stmt_is_scoped_to_one_user() -> None:
    sql = _sql(notifications_repo._list_read_stmt(AUTHOR_OID))

    assert "FROM notification_reads" in sql
    assert f"notification_reads.user_oid = '{AUTHOR_OID}'" in sql
    # 只取两列：客户端要的是 {oid: version}，读正文/时间都是浪费。
    assert sql.startswith(
        "SELECT notification_reads.notification_oid, notification_reads.version"
    )


def test_list_read_maps_oid_to_int_version() -> None:
    import asyncio

    session = _ReadSession(read_rows=[("a" * 24, 3), ("b" * 24, 1)])
    repo = notifications_repo.OracleNotificationsRepo(session=session)

    assert asyncio.run(repo.list_read(AUTHOR_OID)) == {"a" * 24: 3, "b" * 24: 1}


def test_mark_read_upsert_is_keyed_on_the_full_primary_key() -> None:
    sql = " ".join(str(notifications_repo._mark_read_stmt()).split())

    assert sql.startswith("MERGE INTO notification_reads target")
    # 冲突键必须是完整 PK：只按通知去重会把别人的已读行覆盖掉。
    assert "target.user_oid = source.user_oid" in sql
    assert "target.notification_oid = source.notification_oid" in sql
    # 存在则覆盖 version/read_at（不是「已存在就跳过」：重复标记要能刷新）。
    assert "UPDATE SET target.version = :version, target.read_at = :at" in sql
    assert (
        "INSERT (user_oid, notification_oid, version, read_at)" in sql
    )


def test_mark_read_reads_the_notice_version_and_upserts() -> None:
    import asyncio

    session = _ReadSession(notice_version=4)
    repo = notifications_repo.OracleNotificationsRepo(session=session)

    result = asyncio.run(repo.mark_read(AUTHOR_OID, OID, NOW))

    assert result == (OID, 4)
    merge_calls = [
        (sql, params) for sql, params in session.calls if "notification_reads" in sql
    ]
    assert len(merge_calls) == 1
    _sql_text, params = merge_calls[0]
    # 版本来自 NOTIFICATIONS 的当前值，客户端不传。
    assert params == {
        "user_oid": AUTHOR_OID,
        "notification_oid": OID,
        "version": 4,
        "at": NOW,
    }
    # 调用方自带事务：repo 不提交（unit of work 归调用方）。
    assert session.commits == 0


def test_second_mark_read_overwrites_version_and_read_at() -> None:
    """同 user 同通知二次标记：还是同一条 MERGE，绑定新的 version / 时间。"""
    import asyncio

    session = _ReadSession(notice_version=2)
    repo = notifications_repo.OracleNotificationsRepo(session=session)
    later = datetime(2026, 10, 22, 8, 0, 0)

    asyncio.run(repo.mark_read(AUTHOR_OID, OID, NOW))
    asyncio.run(repo.mark_read(AUTHOR_OID, OID, later))

    versions = [
        params["version"]
        for sql, params in session.calls
        if "notification_reads" in sql
    ]
    timestamps = [
        params["at"] for sql, params in session.calls if "notification_reads" in sql
    ]
    assert versions == [2, 2]
    assert timestamps == [NOW, later]


def test_mark_read_unknown_notice_returns_none_without_writing() -> None:
    import asyncio

    session = _ReadSession(notice_version=None)
    repo = notifications_repo.OracleNotificationsRepo(session=session)

    assert asyncio.run(repo.mark_read(AUTHOR_OID, OID, NOW)) is None
    assert not any("notification_reads" in sql for sql, _ in session.calls)
    assert session.commits == 0


def test_mark_read_normalizes_oid_case() -> None:
    import asyncio

    session = _ReadSession(notice_version=1)
    repo = notifications_repo.OracleNotificationsRepo(session=session)

    assert asyncio.run(repo.mark_read(AUTHOR_OID, OID.upper(), NOW)) == (OID, 1)


def test_delete_cleans_read_rows_in_the_same_transaction() -> None:
    import asyncio

    session = _ReadSession(notice_version=3)
    repo = notifications_repo.OracleNotificationsRepo(session=session)

    asyncio.run(repo.delete(OID))

    sqls = [sql for sql, _ in session.calls]
    delete_reads = [
        sql for sql in sqls if sql.startswith("DELETE FROM notification_reads")
    ]
    assert len(delete_reads) == 1
    assert f"notification_reads.notification_oid = '{OID}'" in delete_reads[0]
    # 先删通知行、再清已读行：两条 DELETE 之间没有被 commit 切开。
    assert sqls.index(delete_reads[0]) > next(
        index
        for index, sql in enumerate(sqls)
        if sql.startswith("DELETE FROM notifications ")
    )


def test_edit_never_touches_read_rows() -> None:
    """编辑只 bump 通知版本；已读行原样留着，由客户端比较判未读。"""
    import asyncio

    session = _ReadSession(notice_version=1)
    repo = notifications_repo.OracleNotificationsRepo(session=session)

    asyncio.run(repo.update(OID, title="新标题"))

    assert not any("notification_reads" in sql for sql, _ in session.calls)


# ---------------------------------------------------------------------------
# 迁移镜像（ddl_check 只比对 V001；这里额外钉住 V053 与 V001/models 一致）
# ---------------------------------------------------------------------------


def test_v053_migration_mirrors_models_and_v001() -> None:
    from db import ddl_check

    path = ddl_check.DDL_PATH.parent / "V053__notification_reads.sql"
    v053 = ddl_check.parse_schema(
        ddl_check.extract_execute_immediate(path.read_text(encoding="utf-8"))
    )
    modeled = ddl_check.parse_models()
    v001 = ddl_check.parse_v001()

    expected = modeled["tables"]["notification_reads"]
    assert v053["tables"]["notification_reads"] == expected
    assert v001["tables"]["notification_reads"] == expected

    # 形状定稿：复合主键、无 OID 列、无外键、无额外索引。
    assert expected["pk"] == ("user_oid", "notification_oid")
    assert set(expected["columns"]) == {
        "user_oid",
        "notification_oid",
        "version",
        "read_at",
    }
    assert expected["fk"] == set()
    assert v053["indexes"] == set()


def test_v053_is_idempotent() -> None:
    from db import ddl_check

    text = (
        ddl_check.DDL_PATH.parent / "V053__notification_reads.sql"
    ).read_text(encoding="utf-8")

    # 重跑时表已存在 -> ORA-00955 吞掉；其它错误必须继续 RAISE。
    assert "EXECUTE IMMEDIATE" in text
    assert "-955" in text
    assert "RAISE" in text


# ---------------------------------------------------------------------------
# 列表缓存：单值上限必须容得下一整页正文
# ---------------------------------------------------------------------------


class _FakeCacheClient:
    """够用的 async Redis 替身（字符串 value + px）。"""

    def __init__(self) -> None:
        self.data: dict[str, str] = {}
        self.calls = {"get": 0, "set": 0}

    async def get(self, key: str):
        self.calls["get"] += 1
        return self.data.get(key)

    async def set(self, key: str, value: str, px: int | None = None):
        self.calls["set"] += 1
        self.data[key] = value
        return True

    async def incr(self, key: str) -> int:
        value = int(self.data.get(key, "0")) + 1
        self.data[key] = str(value)
        return value

    async def aclose(self) -> None:
        return None


@pytest.fixture
def fake_cache():
    client = _FakeCacheClient()
    cache._install_client_for_tests(client)
    yield client
    cache._reset_for_tests()


#: 300KB（UTF-8）：超过 ``cache.MAX_VALUE_BYTES``(256KB)，低于公告列表的 2MB。
BIG_BODY = "正" * 100_000


class _BigListRepo(notifications_repo.OracleNotificationsRepo):
    """只替换「回源」一步，其余（key/TTL/max_bytes）走真实 ``list_active``。"""

    def __init__(self, rows: list[dict]) -> None:
        super().__init__()
        self.calls = 0
        self._rows = rows

    async def _list_active_uncached(self):
        self.calls += 1
        return [dict(row) for row in self._rows], 7


def test_remember_default_ceiling_still_rejects_payloads_over_256kb(fake_cache):
    loads = {"n": 0}

    async def loader():
        loads["n"] += 1
        return {"content": BIG_BODY}

    async def main():
        await cache.remember("k", 30, loader)
        await cache.remember("k", 30, loader)

    assert asyncio.run(main()) is None
    assert loads["n"] == 2, "超过默认上限就不缓存，每次都回源"
    assert fake_cache.calls["set"] == 0
    assert "k" not in fake_cache.data


def test_remember_explicit_max_bytes_caches_and_hits(fake_cache):
    loads = {"n": 0}

    async def loader():
        loads["n"] += 1
        return {"content": BIG_BODY}

    async def main():
        first = await cache.remember("k", 30, loader, max_bytes=2 * 1024 * 1024)
        second = await cache.remember("k", 30, loader, max_bytes=2 * 1024 * 1024)
        return first, second

    assert asyncio.run(main()) == ({"content": BIG_BODY}, {"content": BIG_BODY})
    assert loads["n"] == 1
    assert fake_cache.calls["set"] == 1


def test_list_active_caches_a_page_larger_than_the_default_ceiling(fake_cache):
    """100 条 × 4000 字正文的整页必须进缓存，否则读穿永久失效。"""
    repo = _BigListRepo([{"_id": OID, "content": BIG_BODY}])

    async def main():
        first_version = (await repo.list_active())[1]
        second_version = (await repo.list_active())[1]
        await asyncio.sleep(0)  # 让 async_backfill 真正写进 Redis
        return first_version, second_version

    assert asyncio.run(main()) == (7, 7)
    assert repo.calls == 1, "第二次读必须命中缓存"
    assert any(key.startswith(f"{cache.NS}:notif:") for key in fake_cache.data)


def test_active_cache_ttl_and_ceiling_match_their_documentation():
    # 模块 docstring 曾写「~20s」，常量实际是 120s；这里把两者钉在一起。
    assert notifications_repo._ACTIVE_TTL_SECONDS == 120.0
    assert notifications_repo._ACTIVE_CACHE_MAX_BYTES == 2 * 1024 * 1024
    assert notifications_repo._ACTIVE_CACHE_MAX_BYTES > cache.MAX_VALUE_BYTES
    assert len(BIG_BODY.encode("utf-8")) > cache.MAX_VALUE_BYTES
