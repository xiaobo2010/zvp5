"""登出端点：轮换 ``session_id``，旧 token 立刻失效，并留一条 ``user_logout``。

契约：

* ``POST /api/users/auth/logout`` 需要 Bearer，成功后返回 200；
* 服务端把 ``USERS.SESSION_ID`` 换成新值——每请求实时校验 ``sid`` 的机制让旧
  token 当场作废（用例用「同一张 token 再打一次」证明）；
* 审计落 ``user_logout``，带 client_ip / user_agent；
* 审计写失败不改状态码：登出本身已经生效。

离线：users repo 是内存假实现（含可观测的 ``rotate_session``），权限派生与
日志写入打桩，不连库、不发网络。
"""

from __future__ import annotations

from datetime import datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from db import backends
from routers import users_router
from util import cert
from util import object_id as object_id_module

PREFIX = "/api/users"
OID = "64b7f0c2e1a3b4c5d6e7f801"
OLD_SID = "session-before-logout"
NEW_SID = "session-after-logout"

_LOG_CALLS: list[dict] = []


class _RecordingLog:
    def __init__(
        self,
        url: str = "",
        user: str = "",
        data: str = "",
        timestamp: float | None = None,
        type: str = "",
        level: str = "info",
        xuehai_id: str = "",
        client_ip: str = "",
        user_agent: str = "",
    ) -> None:
        self.url = url
        self.user = user
        self.data = data
        self.timestamp = timestamp
        self.type = type
        self.level = level
        self.xuehai_id = xuehai_id
        self.client_ip = client_ip
        self.user_agent = user_agent

    def with_text(self, text: str, type: str = "", level: str = "") -> "_RecordingLog":
        self.data = text
        if type:
            self.type = type
        if level:
            self.level = level
        return self

    async def insert_log(self) -> None:
        _LOG_CALLS.append(
            {
                "type": self.type,
                "data": self.data,
                "user": self.user,
                "level": self.level,
                "client_ip": self.client_ip,
                "user_agent": self.user_agent,
            }
        )


class _ExplodingLog:
    def __init__(self, *args, **kwargs) -> None:
        pass

    def with_text(self, text: str, type: str = "", level: str = "") -> "_ExplodingLog":
        return self

    async def insert_log(self) -> None:
        raise RuntimeError("app_logs unavailable")


class _MemoryUsersRepo:
    def __init__(self) -> None:
        self.doc = {
            "_id": OID,
            "name": "测试用户",
            "session_id": OLD_SID,
            "banned": 0,
            "roles": ["student"],
        }
        self.rotated: list[str] = []

    async def get_by_oid(self, oid: str):
        return self.doc

    async def get_for_auth(self, oid: str):
        return self.doc

    async def rotate_session(self, oid: str) -> None:
        self.rotated.append(oid)
        self.doc["session_id"] = NEW_SID


async def _permissions(_user):
    return ["student"]


def _token(session_id: str = OLD_SID) -> str:
    return cert.jwt_encode(
        OID, [], ["student"], "long", session_id=session_id, purpose="login"
    )


@pytest.fixture()
def env(monkeypatch):
    _LOG_CALLS.clear()
    repo = _MemoryUsersRepo()
    monkeypatch.setattr(object_id_module, "get_user_permissions", _permissions)
    monkeypatch.setattr(users_router, "ZVMSLog", _RecordingLog)
    monkeypatch.setattr(backends, "get_users_repo", lambda *a, **k: repo)
    app = FastAPI()
    app.include_router(users_router.router, prefix=PREFIX)
    return TestClient(app), repo


def _logout(client: TestClient, token: str | None = None, **headers):
    bearer = {"Authorization": f"Bearer {token if token is not None else _token()}"}
    return client.post(f"{PREFIX}/auth/logout", headers={**bearer, **headers})


def test_logout_rotates_the_session_id(env) -> None:
    client, repo = env

    response = _logout(client)

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert repo.rotated == [OID]
    assert repo.doc["session_id"] == NEW_SID


def test_old_token_is_rejected_right_after_logout(env) -> None:
    client, _repo = env
    token = _token()

    assert _logout(client, token).status_code == 200
    assert _logout(client, token).status_code == 401


def test_a_token_signed_with_the_new_session_id_works(env) -> None:
    client, _repo = env
    _logout(client)

    assert _logout(client, _token(NEW_SID)).status_code == 200


def test_logout_without_bearer_is_rejected(env) -> None:
    client, repo = env

    response = client.post(f"{PREFIX}/auth/logout")

    assert response.status_code == 401
    assert repo.rotated == []


def test_logout_is_audited_with_client_ip_and_user_agent(env) -> None:
    client, _repo = env

    _logout(
        client,
        **{
            "X-Forwarded-For": "203.0.113.7, 10.0.0.1",
            "User-Agent": "Mozilla/5.0 (logout harness)",
        },
    )

    rows = [row for row in _LOG_CALLS if row["type"] == "user_logout"]
    assert len(rows) == 1
    assert rows[0]["user"] == OID
    assert rows[0]["client_ip"] == "203.0.113.7"
    assert rows[0]["user_agent"] == "Mozilla/5.0 (logout harness)"
    assert rows[0]["level"] == "info"


def test_audit_failure_does_not_undo_the_logout(env, monkeypatch) -> None:
    client, repo = env
    monkeypatch.setattr(users_router, "ZVMSLog", _ExplodingLog)

    response = _logout(client)

    assert response.status_code == 200
    assert repo.doc["session_id"] == NEW_SID


def test_logout_response_is_not_cacheable(env) -> None:
    client, _repo = env

    assert _logout(client).headers["cache-control"] == "no-store"


def test_expired_token_cannot_log_out(env) -> None:
    client, repo = env
    expired = cert.jwt_encode(
        OID,
        [],
        ["student"],
        "long",
        session_id=OLD_SID,
        purpose="login",
        expires_in=datetime(2000, 1, 1) - datetime.now(),
    )

    assert _logout(client, expired).status_code == 401
    assert repo.rotated == []
