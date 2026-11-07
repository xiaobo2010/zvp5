"""``/api/system/db`` 维护端点的 HTTP 契约（鉴权 / envelope / 校验 / 409）。

``tests/test_db_system_router.py`` 里的 ``client`` fixture 是模块内私有的（没有放
进 conftest），所以这里用同样的最小形态重建一次：admin 用户 + 假仓储，不连库。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from db import backends
from db.oracle.session import get_session
from routers import db_system_router
from services import db_maintenance
from util.object_id import get_current_user

BASE = "/api/system/db"


@pytest.fixture
def client(monkeypatch):
    state = {"roles": ["admin"]}

    async def get_by_oid(oid):
        return state

    async def session():
        yield None

    monkeypatch.setattr(
        backends, "get_users_repo", lambda: SimpleNamespace(get_by_oid=get_by_oid)
    )
    app = FastAPI()
    app.include_router(db_system_router.router, prefix=BASE)
    app.dependency_overrides[get_session] = session
    app.dependency_overrides[get_current_user] = lambda: {
        "id": "admin",
        "per": ["admin"],
    }
    return TestClient(app), state


@pytest.fixture(autouse=True)
def reset_jobs():
    db_maintenance.reset_for_tests()
    yield
    db_maintenance.reset_for_tests()


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(db_system_router.router, prefix=BASE)
    return app


# ---------------------------------------------------------------------------
# 鉴权
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "method,path",
    [
        ("get", "/maintenance/overview"),
        ("get", "/maintenance/suggestions"),
        ("post", "/maintenance/plan"),
        ("post", "/maintenance/run"),
        ("get", "/maintenance/progress"),
        ("post", "/cache/flush"),
        ("post", "/cache/refresh"),
    ],
)
def test_new_endpoints_require_login(method, path):
    # 认证依赖在建立会话之前就拒掉请求
    request = TestClient(_app()).request(method.upper(), f"{BASE}{path}", json={})
    assert request.status_code in (401, 403)


@pytest.mark.parametrize(
    "roles,banned", [(["student"], False), (["admin"], True)]
)
def test_new_endpoints_require_admin(client, roles, banned):
    http, state = client
    state.update(roles=roles, banned=banned)

    for path in (
        "/maintenance/overview",
        "/maintenance/suggestions",
        "/maintenance/progress",
    ):
        assert http.get(f"{BASE}{path}").status_code == 403
    assert http.post(f"{BASE}/cache/flush", json={"scopes": ["users"]}).status_code == 403


# ---------------------------------------------------------------------------
# 只读端点：envelope + no-store
# ---------------------------------------------------------------------------


def test_overview_envelope_and_no_store(client, monkeypatch):
    http, _ = client
    observation = {"available": True, "table_count": 0, "tables": []}

    async def fake_overview(session):
        return observation

    monkeypatch.setattr(db_maintenance, "overview", fake_overview)
    response = http.get(f"{BASE}/maintenance/overview")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {"status": "ok", "code": 200, "data": observation}


def test_suggestions_envelope(client, monkeypatch):
    http, _ = client
    observation = {"available": True, "suggestions": [], "counts": {}}

    async def fake_suggestions(session):
        return observation

    monkeypatch.setattr(db_maintenance, "suggestions", fake_suggestions)
    body = http.get(f"{BASE}/maintenance/suggestions").json()

    assert body == {"status": "ok", "code": 200, "data": observation}


# ---------------------------------------------------------------------------
# plan / run 校验
# ---------------------------------------------------------------------------


def test_plan_rejects_unknown_action_with_400_not_503(client):
    http, _ = client
    response = http.post(f"{BASE}/maintenance/plan", json={"action": "drop_all"})

    assert response.status_code == 400
    assert "action" in response.json()["detail"]


def test_plan_rejects_illegal_table_name(client):
    http, _ = client
    response = http.post(
        f"{BASE}/maintenance/plan",
        json={"action": "gather_stats", "tables": ["USERS; DROP TABLE X"]},
    )

    assert response.status_code == 400


def test_run_requires_confirm_token(client):
    http, _ = client
    response = http.post(f"{BASE}/maintenance/run", json={"action": "gather_stats"})

    assert response.status_code == 400
    assert "RUN" in response.json()["detail"]


def test_run_rejects_bad_confirm_token(client):
    http, _ = client
    response = http.post(
        f"{BASE}/maintenance/run",
        json={"action": "gather_stats", "confirm": "yes"},
    )

    assert response.status_code == 400


def test_run_starts_job_with_confirm(client, monkeypatch):
    http, _ = client
    started = {"job_id": "job-1", "started_at": "2026-11-04T00:00:00+00:00", "state": "running"}
    seen = {}

    def fake_start(action, tables, dry_run, actor):
        seen.update(action=action, tables=tables, dry_run=dry_run, actor=actor)
        return started

    monkeypatch.setattr(db_maintenance, "start_job", fake_start)
    response = http.post(
        f"{BASE}/maintenance/run",
        json={"action": "gather_stats", "tables": ["users"], "confirm": "RUN"},
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {"status": "ok", "code": 200, "data": started}
    assert seen == {
        "action": "gather_stats",
        "tables": ["USERS"],
        "dry_run": False,
        "actor": "admin",
    }


def test_run_dry_run_does_not_need_confirm(client, monkeypatch):
    http, _ = client

    monkeypatch.setattr(
        db_maintenance,
        "start_job",
        lambda action, tables, dry_run, actor: {
            "job_id": "job-2",
            "started_at": "2026-11-04T00:00:00+00:00",
            "state": "running",
        },
    )
    response = http.post(
        f"{BASE}/maintenance/run",
        json={"action": "gather_stats", "dry_run": True},
    )

    assert response.status_code == 200


def test_run_busy_returns_409(client, monkeypatch):
    http, _ = client

    def busy(*args, **kwargs):
        raise db_maintenance.JobBusy(phase="running")

    monkeypatch.setattr(db_maintenance, "start_job", busy)
    response = http.post(
        f"{BASE}/maintenance/run",
        json={"action": "gather_stats", "confirm": "RUN"},
    )

    assert response.status_code == 409
    assert "running" in response.json()["detail"]


# ---------------------------------------------------------------------------
# progress
# ---------------------------------------------------------------------------


def test_progress_has_frozen_shape(client):
    http, _ = client
    response = http.get(f"{BASE}/maintenance/progress")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    data = response.json()["data"]
    assert set(data) == {
        "running",
        "job_id",
        "action",
        "dry_run",
        "phase",
        "started_at",
        "finished_at",
        "progress",
        "result",
        "error",
    }
    assert data["running"] is False
    assert data["progress"] == {"total": 0, "done": 0, "failed": 0, "current": None}


# ---------------------------------------------------------------------------
# cache 端点
# ---------------------------------------------------------------------------


def test_cache_flush_rejects_unknown_scope(client):
    http, _ = client
    response = http.post(f"{BASE}/cache/flush", json={"scopes": ["everything"]})

    assert response.status_code == 400
    assert "everything" in response.json()["detail"]


def test_cache_flush_returns_counts(client, monkeypatch):
    http, _ = client
    payload = {
        "scopes": ["users"],
        "flushed": {"users": 4},
        "versions": {"users": 7},
    }

    async def fake_flush(scopes):
        return payload

    monkeypatch.setattr(db_maintenance, "flush_cache", fake_flush)
    response = http.post(f"{BASE}/cache/flush", json={"scopes": ["users"]})

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {"status": "ok", "code": 200, "data": payload}


def test_cache_refresh_returns_both_flags(client, monkeypatch):
    http, _ = client

    async def fake_refresh():
        return {"settings": True, "roles": False}

    monkeypatch.setattr(db_maintenance, "refresh_snapshots", fake_refresh)
    response = http.post(f"{BASE}/cache/refresh", json={})

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "code": 200,
        "data": {"settings": True, "roles": False},
    }
