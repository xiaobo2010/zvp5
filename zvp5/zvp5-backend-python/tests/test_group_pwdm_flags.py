"""``pwdm=true`` 页内密码状态：两个分组用户端点语义一致（离线单测）。

被固化的两条：

1. ``password`` 为 ``True`` 表示「库里存的是自定义密码」（不等于学号），
   密码仍是学号或为 NULL 时为 ``False``；
2. v1（``/api/groups/{id}/user``）与 v2（``/api/v2/groups/{id}/users``）
   对同一批行的输出完全一致——判定改到线程池后不许出现分叉。
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from db import backends
from routers import groups_router, groups_v2_router
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


ROWS = [
    {
        "_id": "000000000000000000000011",
        "id": "20240101",
        "name": "甲",
        "class_id": GROUP_ID,
        "password": ID_AS_PASSWORD,
    },
    {
        "_id": "000000000000000000000012",
        "id": "20240102",
        "name": "乙",
        "class_id": GROUP_ID,
        "password": CUSTOM_PASSWORD,
    },
    {
        "_id": "000000000000000000000013",
        "id": "20240103",
        "name": "丙",
        "class_id": GROUP_ID,
        "password": None,
    },
]


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(
        backends, "get_users_repo", lambda session=None: _UsersRepo(ROWS)
    )
    app = FastAPI()
    app.include_router(groups_router.router, prefix="/api/groups")
    app.include_router(groups_v2_router.router, prefix="/api/v2/groups")
    app.dependency_overrides[get_current_user] = lambda: READER
    return TestClient(app)


def test_v1_page_reports_custom_password_per_row(client) -> None:
    body = client.get(f"/api/groups/{GROUP_ID}/user?pwdm=true").json()
    assert [row["password"] for row in body["data"]] == [False, True, False]


def test_v2_page_reports_custom_password_per_row(client) -> None:
    body = client.get(f"/api/v2/groups/{GROUP_ID}/users?pwdm=true").json()
    assert [row["password"] for row in body["members"]] == [False, True, False]


def test_pwdm_off_never_exposes_password_state(client) -> None:
    assert all(
        row["password"] is None
        for row in client.get(f"/api/groups/{GROUP_ID}/user").json()["data"]
    )
