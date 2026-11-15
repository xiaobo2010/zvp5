"""``/{user_id}/activities`` 的 ``sort="_id"``：按**活动** oid 倒序，而不是成员行序。

原来的映射指向 ``ACTIVITY_MEMBERS.OID``，在 ``WHERE member_oid = :u`` 下这一列
与插入序同向，等于把「按活动倒序」退化成 rowid 序；分页正确性靠的是排序键必须
是页面上的那个 ``_id``（活动 oid）。

离线机制：假 session 按语句声明的 ``OFFSET/FETCH`` 在一个已按活动 oid 倒序、
同活动内按 rowid 升序排好的内存行表上切片，并回放窗口计数；不建连接、不连 ADB。

    cd <repo root> && .venv/bin/python -m pytest tests/test_user_activity_ordering.py -q
"""

from __future__ import annotations

import pytest
from sqlalchemy.dialects import oracle

from db.repos.oracle.members import (
    OracleMembersRepo,
    _list_for_user_activities_stmt,
)

USER_OID = "000000000000000000000001"


def _oid(value: int) -> str:
    return f"{value:024x}"


class _Row:
    def __init__(self, mapping: dict) -> None:
        self._mapping = mapping


class _Result:
    def __init__(self, rows: list[_Row], scalar=None) -> None:
        self._rows = rows
        self._scalar = scalar

    def all(self) -> list[_Row]:
        return list(self._rows)

    def scalar_one(self):
        return self._scalar


def _pagination(stmt) -> tuple[int | None, int]:
    limit = getattr(getattr(stmt, "_limit_clause", None), "value", None)
    offset = getattr(getattr(stmt, "_offset_clause", None), "value", None) or 0
    return limit, offset


def _member_row(activity_oid: str, mode: str, rowid: int) -> dict:
    """一条成员行 + 它 JOIN 回来的活动行（``_joined_activity_row`` 的入参）。"""
    mapping = {
        "oid": f"{activity_oid[-12:]}{rowid:04d}",
        "activity_oid": activity_oid,
        "member_oid": USER_OID,
        "member_status": "effective",
        "member_mode": mode,
        "duration_hours": 1.5,
        "a_oid": activity_oid,
        "a_name": f"活动{activity_oid[-4:]}",
        "a_description": "",
        "a_atype": "on-campus",
        "a_creator_oid": USER_OID,
        "a_status": "effective",
        "a_activity_date": "2026-09-01",
        "a_created_at": None,
        "a_updated_at": None,
        "a_appointee_oid": USER_OID,
        "a_approver": "",
        "a_place": "",
        "a_origin": None,
    }
    return mapping


class _ActivityPagingSession:
    """按 SQL 的排序键（活动 oid 降序、rowid 升序）与 OFFSET/FETCH 切片。"""

    def __init__(self, rows: list[dict]) -> None:
        self.rows = sorted(
            rows, key=lambda row: row["a_oid"], reverse=True
        )
        self.statements: list = []

    async def execute(self, stmt) -> _Result:
        self.statements.append(stmt)
        columns = list(stmt.selected_columns.keys())
        if len(columns) == 1 and columns[0].startswith("count"):
            return _Result([], scalar=len(self.rows))
        limit, offset = _pagination(stmt)
        window = (
            self.rows[offset:] if limit is None else self.rows[offset : offset + limit]
        )
        total = len(self.rows)
        return _Result([_Row({**row, "total_count": total}) for row in window])


def test_activity_page_orders_by_activity_oid():
    sql = str(
        _list_for_user_activities_stmt(USER_OID, "", "_id", False, 1, 2).compile(
            dialect=oracle.dialect(), compile_kwargs={"literal_binds": True}
        )
    )

    assert "ORDER BY activities.oid DESC NULLS LAST" in sql
    assert "ORDER BY activity_members.oid" not in sql
    # 同活动多行仍按 rowid 稳定兜底，分页不会因 ties 重排
    assert "activity_members.rowid ASC" in sql


@pytest.mark.asyncio
async def test_pages_do_not_overlap_and_stay_activity_descending():
    rows = [
        _member_row(_oid(4), "on-campus", 1),
        _member_row(_oid(3), "on-campus", 1),
        _member_row(_oid(3), "off-campus", 2),
        _member_row(_oid(2), "on-campus", 1),
        _member_row(_oid(1), "on-campus", 1),
    ]
    session = _ActivityPagingSession(rows)
    repo = OracleMembersRepo(session)

    keys: list[tuple[str, str]] = []
    for page in (1, 2, 3):
        page_rows, total = await repo.list_for_user_activities(
            USER_OID, "", "_id", False, page, 2
        )
        assert total == len(rows)
        keys.extend(
            (str(row["activity"]["_id"]), row["member"]["mode"]) for row in page_rows
        )

    # 不重不漏：三页拼起来正好是整份列表
    assert len(keys) == len(rows)
    assert set(keys) == {(row["a_oid"], row["member_mode"]) for row in rows}
    # 活动 oid 非递增（同活动的两行相邻）
    activity_ids = [activity_oid for activity_oid, _mode in keys]
    assert activity_ids == [_oid(4), _oid(3), _oid(3), _oid(2), _oid(1)]


@pytest.mark.asyncio
async def test_unknown_sort_still_falls_back_to_member_oid():
    sql = str(
        _list_for_user_activities_stmt(USER_OID, "", "whatever", False, 1, 2).compile(
            dialect=oracle.dialect(), compile_kwargs={"literal_binds": True}
        )
    )

    assert "ORDER BY activity_members.oid DESC" in sql
