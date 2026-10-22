"""P0-2：认证链路上的数据库故障必须是 503，不是 500，也不是 401。

``util.object_id._authenticate`` 的认证路径要读两次库（用户 + 当前 roles）。
数据库抖动时这两个调用会抛 SQLAlchemy / 驱动异常，改动前它们直接冒泡成 500；
若误翻成 401，前端会把一次短暂的 DB 故障当成「登录过期」并立刻清会话。

契约：

1. ``OperationalError`` / ``DatabaseError`` / ``InterfaceError`` / ``oracledb.Error``
   → **503** + 固定中文 detail，且 ``WWW-Authenticate`` 语义不变（401 仍是 401）；
2. 其他编程错误（如 ``KeyError``）**不吞**，照旧 500（否则真 bug 会被伪装成故障）；
3. ``is_admin_token`` 保持 total（维护准入层依赖它，「不是 admin」而非异常）。

离线：用户库整个换成替身，不连 Oracle。
"""

from __future__ import annotations

import oracledb
from types import SimpleNamespace

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import DatabaseError, InterfaceError, OperationalError

from db import backends
from util import object_id
from util.cert import jwt_encode
from util.object_id import DB_UNAVAILABLE_DETAIL, get_current_user, is_admin_token

OID = "64b7f0c2e1a3b4c5d6e7f801"
SESSION_ID = "sess-1"


def _token() -> str:
    return jwt_encode(OID, [], ["student"], "long", session_id=SESSION_ID)


def _driver_error(kind: str) -> BaseException:
    if kind == "operational":
        return OperationalError("SELECT 1 FROM users", {}, Exception("ORA-12170"))
    if kind == "database":
        return DatabaseError("SELECT 1 FROM users", {}, Exception("ORA-12541"))
    if kind == "interface":
        return InterfaceError("SELECT 1 FROM users", {}, Exception("closed"))
    return oracledb.DatabaseError("ORA-12170: TNS:Connect timeout occurred")


@pytest.fixture
def client():
    """返回 ``(TestClient, get_by_oid)``：用例自行替换 ``get_by_oid`` 的行为。"""

    async def get_by_oid(oid):
        return {
            "_id": OID,
            "id": "20240001",
            "name": "普通用户",
            "roles": ["student"],
            "class_id": None,
            "session_id": SESSION_ID,
            "banned": False,
        }

    app = FastAPI()

    @app.get("/protected")
    async def protected(user=Depends(get_current_user)):
        return {"id": user["id"]}

    return TestClient(app), get_by_oid


def _patch_users(monkeypatch, get_by_oid) -> None:
    monkeypatch.setattr(
        backends, "get_users_repo", lambda *a, **k: SimpleNamespace(get_by_oid=get_by_oid)
    )


@pytest.mark.parametrize("kind", ["operational", "database", "interface", "oracledb"])
def test_db_failures_become_503(monkeypatch, client, kind):
    http, _ = client
    error = _driver_error(kind)

    async def failing(oid):
        raise error

    _patch_users(monkeypatch, failing)

    response = http.get("/protected", headers={"Authorization": f"Bearer {_token()}"})

    assert response.status_code == 503
    assert response.json()["detail"] == DB_UNAVAILABLE_DETAIL
    # 401 的语义标记不得混进来
    assert "www-authenticate" not in {k.lower() for k in response.headers}
    assert "Could not validate credentials" not in response.text


def test_permission_lookup_failure_also_becomes_503(monkeypatch, client):
    http, get_by_oid = client
    _patch_users(monkeypatch, get_by_oid)

    async def failing_permissions(user):
        raise OperationalError("SELECT roles", {}, Exception("ORA-03113"))

    monkeypatch.setattr(object_id, "get_user_permissions", failing_permissions)

    response = http.get("/protected", headers={"Authorization": f"Bearer {_token()}"})

    assert response.status_code == 503
    assert response.json()["detail"] == DB_UNAVAILABLE_DETAIL


def test_programming_errors_are_not_swallowed(monkeypatch, client):
    http, _ = client

    async def buggy(oid):
        raise KeyError("unexpected field")

    _patch_users(monkeypatch, buggy)

    with pytest.raises(KeyError):
        http.get("/protected", headers={"Authorization": f"Bearer {_token()}"})


def test_missing_token_is_still_401(monkeypatch, client):
    http, get_by_oid = client
    _patch_users(monkeypatch, get_by_oid)

    response = http.get("/protected")

    assert response.status_code == 401
    assert response.json()["detail"] == "Could not validate credentials"


def test_valid_token_still_passes(monkeypatch, client):
    http, get_by_oid = client
    _patch_users(monkeypatch, get_by_oid)

    response = http.get("/protected", headers={"Authorization": f"Bearer {_token()}"})

    assert response.status_code == 200
    assert response.json() == {"id": OID}


@pytest.mark.asyncio
async def test_is_admin_token_stays_total_on_db_failure(monkeypatch):
    """维护准入层按「不是 admin」处理，绝不抛异常（否则中间件每次 500）。"""

    async def failing(oid):
        raise OperationalError("SELECT 1", {}, Exception("ORA-12170"))

    _patch_users(monkeypatch, failing)

    assert await is_admin_token(f"Bearer {_token()}") is False
