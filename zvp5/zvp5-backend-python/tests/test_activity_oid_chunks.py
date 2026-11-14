"""``IN`` 超过 1000 项时的 SQL 形状（离线单测，不连 ADB）。

Oracle 对单条 ``IN`` 的元素数上限是 1000（ORA-01795）。调用方（``util/calculate.py``
把「全部有效活动」一次传进来、班委导出把整班成员一次传进来）轻松越线，所以
每条带 ``member_oid/activity_oid IN (...)`` 的语句都要把同一列的比较拆成
``OR c IN (...) OR c IN (...)``。

钉住四条：

1. 仍是**一条**语句（``execute`` 一次），不是分块多次执行；
2. 每段 ``IN`` 至多 1000 个绑定值，拼起来正好是全部输入且顺序不变；
3. ``ORDER BY`` / ``OFFSET`` / ``FETCH`` 还在语句里（分页与排序留在 SQL 侧）；
4. ``None`` = 不过滤、``[]`` = 匹配不到任何行——与改造前逐字节同义。

    cd <repo root> && .venv/bin/python -m pytest tests/test_activity_oid_chunks.py -q
"""

from __future__ import annotations

import warnings

import pytest
from sqlalchemy.dialects import oracle

from db.repos.oracle import activities as activities_module
from db.repos.oracle.activities import OracleActivitiesRepo
from db.repos.oracle.members import OracleMembersRepo

ACTIVITY = "000000000000000000000010"
USER = "000000000000000000000001"


class _Row:
    def __init__(self, mapping: dict) -> None:
        self._mapping = mapping


class _Result:
    def __init__(self, rows: list[_Row], scalar=None) -> None:
        self._rows = rows
        self._scalar = scalar

    def all(self) -> list[_Row]:
        return self._rows

    def scalar_one(self):
        return self._scalar if self._scalar is not None else len(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None


class _Session:
    """记录语句并按队列回放结果（不建连接、不连 ADB）。"""

    def __init__(self, results: list[_Result] | None = None) -> None:
        self.results = list(results or [])
        self.statements: list = []

    async def execute(self, stmt) -> _Result:
        self.statements.append(stmt)
        return self.results.pop(0) if self.results else _Result([])


def _member_row(oid: str, activity: str = ACTIVITY, mode: str = "on-campus") -> _Row:
    return _Row(
        {
            "oid": f"{oid}:{mode}",
            "activity_oid": activity,
            "member_oid": oid,
            "member_status": "effective",
            "member_mode": mode,
            "duration_hours": 1.5,
        }
    )


def _activity_row(oid: str) -> _Row:
    return _Row(
        {
            "oid": oid,
            "name": "活动",
            "description": "",
            "atype": "on-campus",
            "creator_oid": USER,
            "status": "effective",
            "activity_date": None,
            "created_at": None,
            "updated_at": None,
            "appointee_oid": USER,
            "approver": "",
            "place": "",
            "origin": "labor",
        }
    )


def _oid(index: int) -> str:
    return f"{index:024x}"


def _in_lists(stmt) -> list[list[str]]:
    """语句里每段 ``IN`` 的绑定值（按出现顺序）。"""
    return [
        value
        for value in stmt.compile(dialect=oracle.dialect()).params.values()
        if isinstance(value, list)
    ]


def _literal(stmt) -> str:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return str(
            stmt.compile(
                dialect=oracle.dialect(), compile_kwargs={"literal_binds": True}
            )
        )


# --- members: 每条带 IN 的语句 -------------------------------------------


@pytest.mark.asyncio
async def test_list_for_member_chunks_activity_oids_into_one_statement() -> None:
    oids = [_oid(index) for index in range(1500)]
    session = _Session([_Result([_member_row(USER)])])

    rows = await OracleMembersRepo(session).list_for_member(
        USER, status="effective", activity_oids=oids
    )

    assert len(session.statements) == 1
    lists = _in_lists(session.statements[0])
    assert [len(values) for values in lists] == [1000, 500]
    assert lists[0] + lists[1] == oids
    assert rows == [
        {
            "_id": f"{USER}:on-campus",
            "activity": ACTIVITY,
            "member": USER,
            "status": "effective",
            "mode": "on-campus",
            "duration": 1.5,
        }
    ]


@pytest.mark.asyncio
async def test_list_for_member_without_activity_filter_keeps_no_in_clause() -> None:
    session = _Session([_Result([])])

    await OracleMembersRepo(session).list_for_member(USER, status="effective")

    assert _in_lists(session.statements[0]) == []


@pytest.mark.asyncio
async def test_list_raw_pages_once_and_chunks_the_member_filter() -> None:
    oids = [_oid(index) for index in range(1201)]
    page = _Result([_member_row(USER)])
    count = _Result([], scalar=1201)
    session = _Session([count, page])

    rows, total = await OracleMembersRepo(session).list_raw(
        ACTIVITY, member_oids=oids, sort="_id", asc=True, page=2, size=10
    )

    assert total == 1201
    assert len(rows) == 1
    # 一次 count + 一次分页，各自都是单条语句：分块没有变成「每块各查一次」。
    assert len(session.statements) == 2
    count_stmt, page_stmt = session.statements
    assert [len(values) for values in _in_lists(count_stmt)] == [1000, 201]
    assert [len(values) for values in _in_lists(page_stmt)] == [1000, 201]
    # 排序与分页仍由 SQL 负责（分块若改成多次执行，这里会变成每块各取第一页）。
    sql = _literal(page_stmt)
    assert "ORDER BY activity_members.oid ASC" in sql
    assert "OFFSET" in sql and "FETCH FIRST" in sql


@pytest.mark.asyncio
async def test_list_by_members_and_activity_members_use_one_statement() -> None:
    oids = [_oid(index) for index in range(1001)]
    session = _Session([_Result([_member_row(USER)])])

    repo = OracleMembersRepo(session)
    await repo.list_by_members(oids)
    await repo.list_by_activity_and_members(ACTIVITY, oids)

    assert len(session.statements) == 2
    for stmt in session.statements:
        assert [len(values) for values in _in_lists(stmt)] == [1000, 1]
        assert str(stmt).count("IN (") == 2


@pytest.mark.asyncio
async def test_count_for_activity_chunks_the_member_filter() -> None:
    oids = [_oid(index) for index in range(1500)]
    session = _Session([_Result([], scalar=0)])

    total = await OracleMembersRepo(session).count_for_activity(
        ACTIVITY, member_oids=oids
    )

    assert total == 0
    assert len(session.statements) == 1
    assert [len(values) for values in _in_lists(session.statements[0])] == [1000, 500]


@pytest.mark.asyncio
async def test_empty_member_list_still_matches_nothing() -> None:
    session = _Session([_Result([]), _Result([], scalar=0)])

    rows, total = await OracleMembersRepo(session).list_raw(ACTIVITY, member_oids=[])

    assert (rows, total) == ([], 0)
    for stmt in session.statements:
        assert _in_lists(stmt) == [[]]
        assert "IN (NULL)" in _literal(stmt)
        assert "1 != 1" in _literal(stmt)


# --- activities: 有效活动全集超 1000 -------------------------------------


@pytest.mark.asyncio
async def test_activities_list_by_oids_chunks_into_one_statement() -> None:
    oids = [_oid(index) for index in range(1201)]
    page = _Result([_activity_row(oids[0])])
    count = _Result([], scalar=1201)
    session = _Session([count, page])

    rows, total = await OracleActivitiesRepo(session).list_by_oids(
        oids, "", page=2, size=10
    )

    assert total == 1201
    assert len(rows) == 1
    assert len(session.statements) == 2
    count_stmt, page_stmt = session.statements
    for stmt in (count_stmt, page_stmt):
        assert [len(values) for values in _in_lists(stmt)] == [1000, 201]
    # 排序/分页留在 SQL 里。
    sql = _literal(page_stmt)
    assert "ORDER BY activities.oid DESC" in sql
    assert "OFFSET" in sql and "FETCH FIRST" in sql


def test_activities_statement_builder_is_chunked_and_ordered() -> None:
    oids = [_oid(index) for index in range(2500)]

    stmt = activities_module._list_by_oids_stmt(oids, "", -1, 10)

    assert [len(values) for values in _in_lists(stmt)] == [1000, 1000, 500]
    assert str(stmt).count("IN (") == 3
