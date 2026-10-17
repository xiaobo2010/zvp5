"""管理员不得封禁自己——封禁会让唯一管理入口自锁，只能人工介入恢复。"""

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from db import backends
from routers import users_v3_router
from typings.log import inject_log
from util.object_id import get_current_user

SELF = "000000000000000000000001"
OTHER = "000000000000000000000002"


class StubLog:
    def __init__(self):
        self.calls = []

    def with_text(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self

    async def insert_log(self):
        return None


@pytest.fixture
def client(monkeypatch):
    state = {"banned_calls": []}
    users = {
        SELF: {"_id": SELF, "name": "self", "roles": ["admin"], "banned": 0},
        OTHER: {"_id": OTHER, "name": "other", "roles": ["student"], "banned": 0},
    }

    async def get_by_oid(oid):
        return users.get(oid)

    async def set_banned(oid, value):
        state["banned_calls"].append((oid, value))

    async def display_name(oid):
        return "admin"

    monkeypatch.setattr(
        backends,
        "get_users_repo",
        lambda: SimpleNamespace(get_by_oid=get_by_oid, set_banned=set_banned),
    )
    monkeypatch.setattr(users_v3_router, "get_user_display_name", display_name)

    app = FastAPI()
    app.include_router(users_v3_router.router, prefix="/api/v3/users")
    app.dependency_overrides[get_current_user] = lambda: {"id": SELF, "per": ["admin"]}
    app.dependency_overrides[inject_log] = lambda: StubLog()
    return TestClient(app), state


def test_admin_cannot_ban_self(client):
    http, state = client
    response = http.put(f"/api/v3/users/{SELF}/banned", json={"banned": True})
    assert response.status_code == 403
    assert response.json()["detail"] == "不能封禁自己"
    # 未落库，也未写审计日志
    assert state["banned_calls"] == []


def test_admin_can_ban_other_user(client):
    http, state = client
    assert http.put(f"/api/v3/users/{OTHER}/banned", json={"banned": True}).status_code == 204
    assert state["banned_calls"] == [(OTHER, 1)]


def test_self_unban_is_not_blocked(client):
    http, state = client
    assert http.put(f"/api/v3/users/{SELF}/banned", json={"banned": False}).status_code == 204
    assert state["banned_calls"] == [(SELF, 0)]
