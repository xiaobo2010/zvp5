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

from datetime import datetime

import pytest
from sqlalchemy.dialects import oracle

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
