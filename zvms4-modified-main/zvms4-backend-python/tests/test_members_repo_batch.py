"""``OracleMembersRepo.list_by_activity_and_members``：批量三元组读取的取数契约（离线单测）。

被固化的五条：

1. 语句按 ``activity_oid = ...`` + ``member_oid IN (...)`` 过滤，投影是完整成员列；
2. 空输入**不碰 session**（早退）；
3. oid 归一化去重后只查一次，activity_oid 也归一化；
4. Oracle 单个 ``IN`` 上限 1000 ⇒ 超过则拆多块、**同一个注入 session** 内循环执行、结果合并；
5. ≤1000 仍只发一条语句（与不分块现状逐字节等价）。

离线机制：假 session 只记录 ``execute`` 收到的语句并回放预置行，不建连接、不连 ADB。

    cd <repo root> && .venv/bin/python -m pytest tests/test_members_repo_batch.py -q
"""

from __future__ import annotations

import pytest
from sqlalchemy.dialects import oracle

from db.repos.oracle import members as members_module
from db.repos.oracle.members import (
    OracleMembersRepo,
    _list_by_activity_and_members_stmt,
    _member_columns,
)

ACTIVITY = "000000000000000000000010"
OID_A = "000000000000000000000001"
OID_B = "000000000000000000000002"
OID_C = "000000000000000000000003"


class _Row:
    """``result.all()`` 里的行：本模块只需要 ``_mapping``。"""

    def __init__(self, mapping: dict) -> None:
        self._mapping = mapping


class _Result:
    def __init__(self, rows: list[_Row]) -> None:
        self._rows = rows

    def all(self) -> list[_Row]:
        return self._rows


class _Session:
    """假 AsyncSession：记录语句，回放预置行。"""

    def __init__(self, rows: list[_Row] | None = None) -> None:
        self.rows = rows or []
        self.statements: list = []

    async def execute(self, stmt) -> _Result:
        self.statements.append(stmt)
        return _Result(list(self.rows))


def _row(oid: str, activity: str = ACTIVITY, mode: str = "on-campus") -> _Row:
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


def _bound_list(stmt) -> list[str]:
    """语句里 ``IN`` 绑定的 oid 列表（POSTCOMPILE 展开后的值）。"""
    params = stmt.compile(dialect=oracle.dialect()).params
    return next(value for value in params.values() if isinstance(value, list))


def _bound_activity(stmt) -> str:
    params = stmt.compile(dialect=oracle.dialect()).params
    return next(value for value in params.values() if isinstance(value, str))


def test_stmt_filters_by_activity_and_member_in() -> None:
    stmt = _list_by_activity_and_members_stmt(ACTIVITY, [OID_A, OID_B])
    assert len(stmt.selected_columns) == len(_member_columns())
    assert "IN" in str(stmt.whereclause).upper()
    assert _bound_list(stmt) == [OID_A, OID_B]


@pytest.mark.asyncio
async def test_empty_input_never_touches_the_session(monkeypatch) -> None:
    monkeypatch.setattr(
        members_module, "session_scope", lambda: pytest.fail("不该开新 session")
    )
    session = _Session()
    repo = OracleMembersRepo(session)

    assert await repo.list_by_activity_and_members(ACTIVITY, []) == []
    assert session.statements == []


@pytest.mark.asyncio
async def test_oids_are_normalized_and_deduped_into_one_statement() -> None:
    session = _Session()
    repo = OracleMembersRepo(session)

    rows = await repo.list_by_activity_and_members(
        ACTIVITY.upper(), [OID_A.upper(), OID_A, OID_B]
    )

    assert len(session.statements) == 1
    assert _bound_list(session.statements[0]) == [OID_A, OID_B]
    assert _bound_activity(session.statements[0]) == ACTIVITY  # activity 也归一化
    assert rows == []


@pytest.mark.asyncio
async def test_up_to_1000_oids_stay_in_a_single_statement() -> None:
    oids = [f"{index:024x}" for index in range(1000)]
    session = _Session()
    repo = OracleMembersRepo(session)

    await repo.list_by_activity_and_members(ACTIVITY, oids)

    assert len(session.statements) == 1
    assert len(_bound_list(session.statements[0])) == 1000


@pytest.mark.asyncio
async def test_more_than_1000_oids_are_split_into_chunks_on_one_session(
    monkeypatch,
) -> None:
    oids = [f"{index:024x}" for index in range(1001)]
    session = _Session([_row(OID_A)])
    repo = OracleMembersRepo(session)
    # 分块必须在注入的 session 内循环：不允许重新 checkout。
    monkeypatch.setattr(
        members_module, "session_scope", lambda: pytest.fail("分块不应重开 session")
    )

    rows = await repo.list_by_activity_and_members(ACTIVITY, oids)

    assert len(session.statements) == 2  # 1000 + 1
    assert [len(_bound_list(stmt)) for stmt in session.statements] == [1000, 1]
    assert {_bound_activity(stmt) for stmt in session.statements} == {ACTIVITY}
    # 每块回放行都并入结果（两个块各回放 1 行 ⇒ 2 行）。
    assert [row["_id"] for row in rows] == [f"{OID_A}:on-campus"] * 2


@pytest.mark.asyncio
async def test_rows_are_translated_to_the_mongo_shape() -> None:
    session = _Session([_row(OID_A, mode="off-campus")])
    repo = OracleMembersRepo(session)

    rows = await repo.list_by_activity_and_members(ACTIVITY, [OID_A])

    assert rows == [
        {
            "_id": f"{OID_A}:off-campus",
            "activity": ACTIVITY,
            "member": OID_A,
            "status": "effective",
            "mode": "off-campus",
            "duration": 1.5,
        }
    ]
