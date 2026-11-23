"""``groups_v3.get_group_users_v3``：一个 session_scope 装下全部读取（离线）。

被固化的两条：

1. 权限组判定 / 班级读取 / 成员页三个 repo 调用**共享同一个 session**（一次
   checkout），class 组与权限组两条分支都如此；
2. 响应 JSON 逐字段不变（``group`` / ``password`` 后处理照旧）。

    cd <repo root> && .venv/bin/python -m pytest tests/test_group_users_session.py -q
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.testclient import TestClient

from db import backends
from routers import groups_router as groups_v3
from util.object_id import get_current_user

PREFIX = "/api/v1/groups"
GROUP_ID = "0000000000000000000000ff"
READER = {"id": "000000000000000000000001", "per": ["student"], "class_id": GROUP_ID}
ADMIN = {"id": "000000000000000000000001", "per": ["admin"], "class_id": None}

CLASS_ROW = {
    "_id": "000000000000000000000002",
    "id": "2026002",
    "name": "李四",
    "roles": ["student"],
    "class_id": GROUP_ID,
    "class_name": "高一(1)班",
    "banned": 0,
    "has_password": True,
}


class _GroupsRepo:
    def __init__(self, session, permission_group=None, group=None, rows=()) -> None:
        self.session = session
        self.permission_group = permission_group
        self.group = group
        self.rows = list(rows)
        self.sessions: list = []

    async def get_permission_group(self, oid):
        self.sessions.append(self.session)
        return self.permission_group

    async def get(self, oid):
        self.sessions.append(self.session)
        return self.group

    async def members_page(self, oid, query, page, size):
        self.sessions.append(self.session)
        return [dict(row) for row in self.rows], len(self.rows)


class _UsersRepo:
    def __init__(self, session, members=()) -> None:
        self.session = session
        self.members = list(members)
        self.sessions: list = []
        self.calls: list[tuple] = []

    async def list_page(self, query, role, page, size):
        self.sessions.append(self.session)
        self.calls.append((query, role, page, size))
        return [dict(row) for row in self.members], len(self.members)


def _client(monkeypatch, session, groups, users, reader=READER):
    @asynccontextmanager
    async def scope():
        yield session

    monkeypatch.setattr(groups_v3, "session_scope", scope)
    monkeypatch.setattr(backends, "get_groups_repo", lambda s=None: groups)
    monkeypatch.setattr(backends, "get_users_repo", lambda s=None: users)

    app = FastAPI()
    app.include_router(groups_v3.router, prefix=PREFIX)
    app.dependency_overrides[get_current_user] = lambda: reader
    return TestClient(app)


def test_class_group_shares_one_session_and_keeps_the_payload(monkeypatch) -> None:
    session = object()
    groups = _GroupsRepo(session, group={"_id": GROUP_ID, "name": "高一(1)班"}, rows=[CLASS_ROW])
    users = _UsersRepo(session)
    client = _client(monkeypatch, session, groups, users)

    body = client.get(f"{PREFIX}/{GROUP_ID}/users").json()

    # get_permission_group → get → members_page：三次调用同一个 session
    assert groups.sessions == [session, session, session]
    assert users.sessions == []  # class 分支不查用户表
    assert body["total"] == 1
    assert body["data"] == [{**CLASS_ROW, "group": [GROUP_ID], "password": None}]


def test_permission_group_branch_also_uses_that_one_session(monkeypatch) -> None:
    session = object()
    groups = _GroupsRepo(session, permission_group={"permissions": ["admin"]})
    users = _UsersRepo(session, members=[CLASS_ROW])
    client = _client(monkeypatch, session, groups, users, reader=ADMIN)

    body = client.get(f"{PREFIX}/{GROUP_ID}/users?pwdm=true").json()

    assert groups.sessions == [session]
    assert users.sessions == [session]  # 权限组成员页也在同一个 session 里读
    assert users.calls == [("", "admin", 1, 10)]
    assert body["total"] == 1
    assert body["data"][0]["password"] is True
    assert body["data"][0]["group"] == [GROUP_ID]
    assert body["data"][0]["class_name"] == "高一(1)班"


def test_unknown_group_still_answers_the_empty_page(monkeypatch) -> None:
    session = object()
    groups = _GroupsRepo(session)  # 无权限组、无班级
    client = _client(monkeypatch, session, groups, _UsersRepo(session))

    assert client.get(f"{PREFIX}/{GROUP_ID}/users").json() == {"data": [], "total": 0}
