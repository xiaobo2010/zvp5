"""``pwdm=true`` 页内密码状态：v3 分组用户端点的语义（离线单测）。

被固化的两条：

1. v3（``/api/v1/groups/{id}/users``）的 ``password`` 只表示「该行是否存在
   ``password_hash``」——有哈希即 ``True``，不再区分自定义密码与学号；
2. ``pwdm`` 缺省（``false``）时 ``password`` 恒为 ``None``，不泄露密码状态。

v1（``/api/groups/{id}/user``）仍是「自定义密码」语义（哈希内容不等于学号），
单独固化，避免两条端点口径被顺手改成一样。
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from db import backends
from routers import groups_router, groups_v3_router
from util.cert import hash_password
from util.object_id import get_current_user

GROUP_ID = "000000000000000000000001"
READER = {"id": "000000000000000000000002", "per": ["admin"], "perm": []}

ID_AS_PASSWORD = hash_password("20240101").decode("utf-8")
CUSTOM_PASSWORD = hash_password("OtherPwd1!").decode("utf-8")


class _UsersRepo:
    def __init__(self, rows) -> None:
        self._rows = rows

    async def list_by_class(self, class_oid):
        return [dict(row) for row in self._rows]


class _GroupsRepo:
    def __init__(self, rows) -> None:
        self._rows = rows

    async def get_permission_group(self, oid):
        return None

    async def get(self, oid):
        return {"_id": GROUP_ID, "name": "甲班"}

    async def members_page(self, group_oid, query, page, size):
        return [dict(row) for row in self._rows], len(self._rows)


ROWS = [
    {
        "_id": "000000000000000000000011",
        "id": "20240101",
        "name": "甲",
        "roles": ["student"],
        "class_id": GROUP_ID,
        "class_name": "甲班",
        "banned": False,
        "password": ID_AS_PASSWORD,
    },
    {
        "_id": "000000000000000000000012",
        "id": "20240102",
        "name": "乙",
        "roles": ["student"],
        "class_id": GROUP_ID,
        "class_name": "甲班",
        "banned": False,
        "password": CUSTOM_PASSWORD,
    },
    {
        "_id": "000000000000000000000013",
        "id": "20240103",
        "name": "丙",
        "roles": ["student"],
        "class_id": GROUP_ID,
        "class_name": "甲班",
        "banned": False,
        "password": None,
    },
]


@asynccontextmanager
async def _fake_session_scope():
    yield SimpleNamespace()


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(
        backends, "get_users_repo", lambda session=None: _UsersRepo(ROWS)
    )
    monkeypatch.setattr(
        backends, "get_groups_repo", lambda session=None: _GroupsRepo(ROWS)
    )
    monkeypatch.setattr(groups_v3_router, "session_scope", _fake_session_scope)
    app = FastAPI()
    app.include_router(groups_router.router, prefix="/api/groups")
    app.include_router(groups_v3_router.router, prefix="/api/v1/groups")
    app.dependency_overrides[get_current_user] = lambda: READER
    return TestClient(app)


def test_v1_page_reports_custom_password_per_row(client) -> None:
    body = client.get(f"/api/groups/{GROUP_ID}/user?pwdm=true").json()
    assert [row["password"] for row in body["data"]] == [False, True, False]


def test_v3_page_reports_password_hash_presence_per_row(client) -> None:
    body = client.get(f"/api/v1/groups/{GROUP_ID}/users?pwdm=true").json()
    assert [row["password"] for row in body["data"]] == [True, True, False]


def test_pwdm_off_never_exposes_password_state(client) -> None:
    assert all(
        row["password"] is None
        for row in client.get(f"/api/groups/{GROUP_ID}/user").json()["data"]
    )
    assert all(
        row["password"] is None
        for row in client.get(f"/api/v1/groups/{GROUP_ID}/users").json()["data"]
    )


class _PermissionGroupsRepo:
    async def get_permission_group(self, oid):
        return {"_id": GROUP_ID, "permissions": ["admin"]}


class _PagedUsersRepo:
    async def list_page(self, query, permission, page, size):
        return [
            {
                "_id": "000000000000000000000021",
                "id": "20240201",
                "name": "丁",
                "roles": ["admin"],
                "class_id": None,
                "class_name": None,
                "banned": False,
                "has_password": True,
            },
            {
                "_id": "000000000000000000000022",
                "id": "20240202",
                "name": "戊",
                "roles": ["admin"],
                "class_id": None,
                "class_name": None,
                "banned": False,
                "has_password": False,
            },
        ], 2


def test_permission_group_pwdm_reports_has_password(monkeypatch) -> None:
    monkeypatch.setattr(
        backends, "get_groups_repo", lambda session=None: _PermissionGroupsRepo()
    )
    monkeypatch.setattr(
        backends, "get_users_repo", lambda session=None: _PagedUsersRepo()
    )
    monkeypatch.setattr(groups_v3_router, "session_scope", _fake_session_scope)
    app = FastAPI()
    app.include_router(groups_v3_router.router, prefix="/api/v1/groups")
    app.dependency_overrides[get_current_user] = lambda: READER
    body = TestClient(app).get(f"/api/v1/groups/{GROUP_ID}/users?pwdm=true").json()
    assert [row["password"] for row in body["data"]] == [True, False]
