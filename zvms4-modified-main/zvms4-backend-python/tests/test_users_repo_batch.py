"""``OracleUsersRepo.get_many_by_oids``：批量用户读取的取数契约（离线单测）。

被固化的四条：

1. 语句是完整用户投影（与 ``get_by_oid`` 同列）且按 ``oid IN (...)`` 过滤；
2. 空输入**不碰 session**（早退，与 ``get_names`` 同形）；
3. oid 去重后只查一次；
4. Oracle 单个 ``IN`` 上限 1000 ⇒ 超过则拆多块执行、结果合并。

离线机制：假 session 只记录 ``execute`` 收到的语句并回放预置行，不建连接、不连 ADB。

    cd <repo root> && .venv/bin/python -m pytest tests/test_users_repo_batch.py -q
"""

from __future__ import annotations

import pytest
from sqlalchemy.dialects import oracle

from db.repos.oracle.users import (
    OracleUsersRepo,
    _get_many_by_oids_stmt,
    _user_columns,
)

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
        return _Result(self.rows)


def _row(oid: str, **overrides) -> _Row:
    mapping = {
        "oid": oid,
        "business_id": "2026001",
        "name": "张三",
        "roles": ["student"],
        "class_oid": "0000000000000000000000ff",
        "password_hash": None,
        "session_id": None,
        "sex": None,
        "eligibility": None,
        "past": None,
        "banned": 0,
        # V013: ``_user_columns`` 的封禁元数据四列（NULL = 未封禁 / 永久封禁）
        "ban_reason": None,
        "ban_until": None,
        "ban_source": None,
        "ban_rule": None,
    }
    mapping.update(overrides)
    return _Row(mapping)


def _bound_oids(stmt) -> list[str]:
    """语句里 ``IN`` 绑定的 oid 列表（POSTCOMPILE 展开后的值）。"""
    return next(iter(stmt.compile(dialect=oracle.dialect()).params.values()))


def test_stmt_selects_the_full_user_columns_and_filters_by_in() -> None:
    stmt = _get_many_by_oids_stmt([OID_A, OID_B])
    # 11 个原有列 + V013 的 4 个封禁元数据列
    assert len(stmt.selected_columns) == len(_user_columns()) == 15
    assert "IN" in str(stmt.whereclause).upper()


@pytest.mark.asyncio
async def test_empty_input_never_touches_the_session() -> None:
    session = _Session()
    repo = OracleUsersRepo(session)

    assert await repo.get_many_by_oids([]) == {}
    assert session.statements == []


@pytest.mark.asyncio
async def test_duplicate_oids_are_queried_once() -> None:
    session = _Session()
    repo = OracleUsersRepo(session)

    assert await repo.get_many_by_oids([OID_A, OID_A, OID_B]) == {}
    assert len(session.statements) == 1
    assert _bound_oids(session.statements[0]) == [OID_A, OID_B]


@pytest.mark.asyncio
async def test_more_than_1000_oids_are_split_into_chunks() -> None:
    oids = [f"{index:024x}" for index in range(1001)]
    session = _Session()
    repo = OracleUsersRepo(session)

    assert await repo.get_many_by_oids(oids) == {}
    assert len(session.statements) == 2  # 1000 + 1
    assert [len(_bound_oids(stmt)) for stmt in session.statements] == [1000, 1]


@pytest.mark.asyncio
async def test_rows_come_back_as_full_documents_keyed_by_oid() -> None:
    session = _Session(
        [
            _row(
                OID_A,
                roles=["admin"],
                banned=1,
                sex="M",
                past=["旧班级"],
                ban_reason="多设备登录",
                ban_source="rule",
                ban_rule="multi_device_login",
            ),
            _row(OID_B, class_oid=None),
        ]
    )
    repo = OracleUsersRepo(session)

    found = await repo.get_many_by_oids([OID_A, OID_B, OID_C])

    assert set(found) == {OID_A, OID_B}  # 查不到的 oid 直接缺席
    assert found[OID_A]["roles"] == ["admin"]
    assert found[OID_A]["banned"] is True
    assert found[OID_A]["sex"] == "M"
    assert found[OID_A]["past"] == ["旧班级"]
    assert found[OID_A]["ban_source"] == "rule"
    assert found[OID_A]["ban_rule"] == "multi_device_login"
    assert "ban_until" not in found[OID_A]  # NULL = 永久封禁，字段不出现
    assert "class_id" not in found[OID_B]  # NULL 可选列不进文档（同 get_by_oid）
    assert found[OID_A]["password"] is None
