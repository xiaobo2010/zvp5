"""管理员封禁端点：不能封自己 + 原因/流水按新契约落库。

封禁会让唯一管理入口自锁（只能人工介入恢复），所以自我封禁必须被拦截；
其余方向要保证 ``set_ban`` / ``clear_ban`` 与 ``BAN_RECORDS`` 流水成对出现——
只有状态没有流水，累犯升级处罚就失去档位依据。
"""

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
    state = {"bans": [], "clears": [], "records": []}
    users = {
        SELF: {"_id": SELF, "name": "self", "roles": ["admin"], "banned": 0},
        OTHER: {"_id": OTHER, "name": "other", "roles": ["student"], "banned": 0},
    }

    async def get_by_oid(oid):
        return users.get(oid)

    async def set_ban(oid, *, reason, until, source, rule_code):
        state["bans"].append(
            {
                "oid": oid,
                "reason": reason,
                "until": until,
                "source": source,
                "rule_code": rule_code,
            }
        )

    async def clear_ban(oid):
        state["clears"].append(oid)

    async def insert(user_oid, **kwargs):
        state["records"].append({"user_oid": user_oid, **kwargs})
        return "rec-1"

    async def display_name(oid):
        return "admin"

    monkeypatch.setattr(
        backends,
        "get_users_repo",
        lambda *a, **k: SimpleNamespace(
            get_by_oid=get_by_oid, set_ban=set_ban, clear_ban=clear_ban
        ),
    )
    monkeypatch.setattr(
        backends,
        "get_ban_records_repo",
        lambda *a, **k: SimpleNamespace(insert=insert),
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
    assert state["bans"] == []
    assert state["records"] == []


def test_admin_can_ban_other_user_with_default_reason(client):
    http, state = client
    assert http.put(f"/api/v3/users/{OTHER}/banned", json={"banned": True}).status_code == 204

    ban = state["bans"][0]
    assert ban["oid"] == OTHER
    assert ban["reason"] == "管理员封禁"
    assert ban["until"] is None
    assert ban["source"] == "admin"
    assert ban["rule_code"] is None

    record = state["records"][0]
    assert record["user_oid"] == OTHER
    assert record["source"] == "admin"
    assert record["reason"] == "管理员封禁"
    assert record["permanent"] is True
    assert record["expires_at"] is None
    assert record["operator_oid"] == SELF


def test_admin_reason_is_trimmed_and_forwarded(client):
    http, state = client
    response = http.put(
        f"/api/v3/users/{OTHER}/banned",
        json={"banned": True, "reason": "  多次刷分  "},
    )
    assert response.status_code == 204
    assert state["bans"][0]["reason"] == "多次刷分"
    assert state["records"][0]["reason"] == "多次刷分"


def test_blank_reason_falls_back_to_default(client):
    http, state = client
    assert (
        http.put(
            f"/api/v3/users/{OTHER}/banned",
            json={"banned": True, "reason": "   "},
        ).status_code
        == 204
    )
    assert state["bans"][0]["reason"] == "管理员封禁"


def test_too_long_reason_is_rejected(client):
    http, state = client
    response = http.put(
        f"/api/v3/users/{OTHER}/banned",
        json={"banned": True, "reason": "x" * 513},
    )
    assert response.status_code == 422
    assert state["bans"] == []


def test_self_unban_is_not_blocked(client):
    http, state = client
    assert http.put(f"/api/v3/users/{SELF}/banned", json={"banned": False}).status_code == 204
    assert state["clears"] == [SELF]
    assert state["bans"] == []
    # 解封不写流水：历史事实已经由封禁时的行承担，解封不能追加「负封禁」。
    assert state["records"] == []
