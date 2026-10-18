from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from db import backends, system_state
from db.oracle.session import get_session
from routers import db_system_router
from tasks import retention
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


# ---------------------------------------------------------------------------
# 新增端点不得影响既有契约：/status 与 /retention/preview 一字不改
# ---------------------------------------------------------------------------


def test_status_contract_is_unchanged_by_the_progress_endpoint(client, monkeypatch):
    """既有 /status 的字段/形状/语义必须逐字保留（清理车道只加不改）。"""
    http, _ = client
    observation = {
        "status": "ok",
        "storage": {"available": True, "tables": {}, "tablespaces": {}},
        "load": {"available": True, "metrics": [], "connection_pool": {}},
        "warnings": [],
        "observed_at": "2026-10-18T03:40:12+00:00",
        "cache": {"cached": False, "age_seconds": 0.0, "ttl_seconds": 15},
    }

    async def collect(session):
        return observation

    monkeypatch.setattr(db_system_router, "get_database_status", collect)
    body = http.get("/api/system/db/status").json()

    assert body == {
        "status": "ok",
        "code": 200,
        "data": {key: value for key, value in observation.items() if key != "status"},
    }
    assert set(body["data"]) == {
        "storage",
        "load",
        "warnings",
        "observed_at",
        "cache",
    }


def test_preview_contract_keeps_its_keys(client, monkeypatch):
    """preview 只有 ``{cutoff, deletable}``：加了新端点也不能往里塞字段。"""
    http, _ = client

    async def count(cutoff):
        return 0

    async def count_tasks(cutoff):
        return 0

    monkeypatch.setattr(
        backends,
        "get_applogs_repo",
        lambda session=None: SimpleNamespace(count_expired=count),
    )
    monkeypatch.setattr(
        backends,
        "get_tasks_repo",
        lambda session=None: SimpleNamespace(count_expired_finished=count_tasks),
    )
    body = http.post("/api/system/db/retention/preview").json()

    assert body["status"] == "ok" and body["code"] == 200
    assert set(body["data"]) == {"app_logs", "export_tasks"}
    for table in ("app_logs", "export_tasks"):
        assert set(body["data"][table]) == {"cutoff", "deletable"}


# ---------------------------------------------------------------------------
# 新增：GET /retention/progress（设计 §5.5）
# ---------------------------------------------------------------------------

PROGRESS_URL = "/api/system/db/retention/progress"


@pytest.fixture
def state_db(tmp_path, monkeypatch):
    """进度端点会读状态库：隔离到 tmp_path，别碰仓库根下的真实状态库。"""
    path = tmp_path / "system_state.db"
    monkeypatch.setenv("ZVMS_SYSTEM_DB", str(path))
    system_state.invalidate_cache()
    yield path
    system_state.invalidate_cache()


@pytest.fixture(autouse=True)
def _clean_job_state(monkeypatch):
    """进度端点的作业状态是模块级进程内状态（单 worker 前提）：用例之间归零。"""
    monkeypatch.setattr(retention, "_current_job", None)
    monkeypatch.setattr(retention, "_last_run", None)
    monkeypatch.setattr(retention, "_auto_purge_running", False)
    yield


def test_progress_requires_login():
    app = FastAPI()
    app.include_router(db_system_router.router, prefix="/api/system/db")
    assert TestClient(app).get(PROGRESS_URL).status_code == 401


@pytest.mark.parametrize("roles,banned", [(["student"], False), (["admin"], True)])
def test_progress_requires_admin(client, state_db, roles, banned):
    http, state = client
    state.update(roles=roles, banned=banned)

    assert http.get(PROGRESS_URL).status_code == 403


def test_progress_envelope_and_frozen_shape(client, state_db):
    http, _ = client
    response = http.get(PROGRESS_URL)

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["status"] == "ok" and body["code"] == 200
    data = body["data"]
    assert set(data) == {
        "running",
        "job_id",
        "phase",
        "started_at",
        "finished_at",
        "maintenance",
        "progress",
        "result",
        "error",
        "last_run",
    }
    assert data["running"] is False
    assert data["maintenance"] == {
        "enabled": False,
        "since": None,
        "reason": None,
        "auto": False,
    }
    assert data["progress"] == {
        "app_logs": {"deleted": 0, "remaining": 0},
        "export_tasks": {"deleted": 0, "remaining": 0},
        "batches": 0,
        "max_batches": retention.retention_max_batches(),
    }
