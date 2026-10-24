"""认证路径的窄投影：``get_for_auth`` 取数契约（离线单测）。

被固化的三条：

1. 认证投影不含 ``past`` / ``eligibility`` 两个 CLOB，``roles`` 必须保留；
2. ``get_for_auth`` 返回的文档不带 ``password``（认证不比对密码）；
3. ``util.object_id`` 优先走 ``get_for_auth``，仓储没有该方法时退回
   ``get_by_oid``。

离线机制：假 session 只记录 ``execute`` 收到的语句并回放预置行，不建连接、
不连 ADB。
"""

from __future__ import annotations

from datetime import datetime

import pytest

from db.repos.oracle.users import OracleUsersRepo, _auth_columns
from util.object_id import _read_auth_user

OID = "000000000000000000000001"


class _Row:
    def __init__(self, mapping: dict) -> None:
        self._mapping = mapping


class _Result:
    def __init__(self, rows: list[_Row]) -> None:
        self._rows = rows

    def first(self):
        return self._rows[0] if self._rows else None


class _Session:
    def __init__(self, rows: list[_Row] | None = None) -> None:
        self.rows = rows or []
        self.statements: list = []

    async def execute(self, stmt) -> _Result:
        self.statements.append(stmt)
        return _Result(self.rows)


def _auth_row(**overrides) -> _Row:
    mapping = {
        "oid": OID,
        "roles": ["student"],
        "class_oid": "000000000000000000000009",
        "session_id": "sid-1",
        "banned": 0,
        "ban_reason": None,
        "ban_until": None,
    }
    mapping.update(overrides)
    return _Row(mapping)


def test_auth_projection_skips_the_clob_profile_columns() -> None:
    names = {column.name for column in _auth_columns()}
    assert names == {
        "oid",
        "roles",
        "class_oid",
        "session_id",
        "banned",
        "ban_reason",
        "ban_until",
    }
    assert "past" not in names
    assert "eligibility" not in names
    assert "password_hash" not in names


@pytest.mark.asyncio
async def test_get_for_auth_document_carries_the_auth_keys_only() -> None:
    session = _Session([_auth_row()])
    doc = await OracleUsersRepo(session).get_for_auth(OID)
    assert doc == {
        "_id": OID,
        "roles": ["student"],
        "class_id": "000000000000000000000009",
        "session_id": "sid-1",
    }
    assert "password" not in doc
    assert "past" not in doc
    assert "eligibility" not in doc


@pytest.mark.asyncio
async def test_get_for_auth_keeps_the_ban_semantics() -> None:
    until = datetime(2030, 1, 1, 0, 0, 0)
    session = _Session([_auth_row(banned=1, ban_reason="刷屏", ban_until=until)])
    doc = await OracleUsersRepo(session).get_for_auth(OID)
    assert doc["banned"] is True
    assert doc["ban_reason"] == "刷屏"
    assert doc["ban_until"] == until


@pytest.mark.asyncio
async def test_get_for_auth_missing_user_is_none() -> None:
    assert await OracleUsersRepo(_Session([])).get_for_auth(OID) is None


class _NarrowRepo:
    def __init__(self) -> None:
        self.calls = 0

    async def get_for_auth(self, oid: str):
        self.calls += 1
        return {"_id": oid}

    async def get_by_oid(self, oid: str):
        raise AssertionError("认证不应退回整行读")


class _LegacyRepo:
    def __init__(self) -> None:
        self.calls = 0

    async def get_by_oid(self, oid: str):
        self.calls += 1
        return {"_id": oid}


@pytest.mark.asyncio
async def test_auth_read_prefers_the_narrow_repo_method(monkeypatch) -> None:
    repo = _NarrowRepo()
    monkeypatch.setattr("util.object_id.backends.get_users_repo", lambda *a: repo)
    assert await _read_auth_user(OID) == {"_id": OID}
    assert repo.calls == 1


@pytest.mark.asyncio
async def test_auth_read_falls_back_to_get_by_oid(monkeypatch) -> None:
    repo = _LegacyRepo()
    monkeypatch.setattr("util.object_id.backends.get_users_repo", lambda *a: repo)
    assert await _read_auth_user(OID) == {"_id": OID}
    assert repo.calls == 1
