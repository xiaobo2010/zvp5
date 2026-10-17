from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from db import backends
from db.oracle.session import get_session
from routers import db_system_router
from util.object_id import get_current_user


@pytest.fixture
def client(monkeypatch):
    state = {"roles": ["admin"]}

    async def get_by_oid(oid):
        return state

    async def session():
        yield None

    monkeypatch.setattr(backends, "get_users_repo", lambda: SimpleNamespace(get_by_oid=get_by_oid))
    app = FastAPI()
    app.include_router(db_system_router.router, prefix="/api/system/db")
    app.dependency_overrides[get_session] = session
    app.dependency_overrides[get_current_user] = lambda: {"id": "admin", "per": ["admin"]}
    return TestClient(app), state


def test_monitor_requires_login():
    app = FastAPI()
    app.include_router(db_system_router.router, prefix="/api/system/db")
    # Auth runs before any connection/session is created.
    assert TestClient(app).get("/api/system/db/status").status_code == 401


@pytest.mark.parametrize("roles,banned", [(["student"], False), (["department"], False), (["admin"], True)])
def test_monitor_rechecks_privileges_before_collecting(client, monkeypatch, roles, banned):
    http, state = client
    state.update(roles=roles, banned=banned)

    async def never(session):
        raise AssertionError("must not collect before authorization")

    monkeypatch.setattr(db_system_router, "get_database_status", never)
    assert http.get("/api/system/db/status").status_code == 403


def test_admin_get_has_envelope_no_store_and_preserves_cached_status(client, monkeypatch):
    http, _ = client
    observation = {"status": "degraded", "storage": {}, "load": {}}

    async def collect(session):
        return observation

    monkeypatch.setattr(db_system_router, "get_database_status", collect)
    response = http.get("/api/system/db/status")
    assert response.json() == {"status": "degraded", "code": 200, "data": {"storage": {}, "load": {}}}
    assert response.headers["cache-control"] == "no-store"
    assert observation["status"] == "degraded"


def test_collection_failure_is_503_without_internal_details(client, monkeypatch):
    http, _ = client

    async def failed(session):
        raise ConnectionError("do not expose DSN/password")

    monkeypatch.setattr(db_system_router, "get_database_status", failed)
    response = http.get("/api/system/db/status")
    assert response.status_code == 503
    assert "DSN" not in response.text


@pytest.mark.parametrize("dependency", [get_current_user, get_session])
def test_database_failure_in_dependencies_is_also_503(client, dependency):
    http, _ = client

    async def unavailable():
        raise ConnectionError("private DSN and password")

    http.app.dependency_overrides[dependency] = unavailable
    response = http.get("/api/system/db/status")
    assert response.status_code == 503
    assert response.headers["cache-control"] == "no-store"
    assert "private" not in response.text
