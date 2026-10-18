"""L4 (R6-1): ``GET /api/system/db/metrics/host`` — local-only server panel.

The endpoint exists so the "本地服务器" tab renders without waiting for Oracle.
The tests therefore guard three things, in this order of importance:

1. **Zero Oracle round trips on the data path.**  Every database collector is
   replaced by a counting stub (and the session dependency by one that raises
   if it is ever resolved); the endpoint must still answer 200 with a full
   payload.  A static check asserts the route declares no ``get_session``
   dependency at all, so this cannot regress silently.
2. **Shape parity with ``/realtime``.**  ``host``/``pool`` must be built by the
   same builders ``/realtime`` embeds, so the frontend reuses one renderer.
3. **Admin-only**, re-checked in the database exactly like its siblings.
"""

from __future__ import annotations

import inspect
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from db import backends
from db.oracle.session import get_session
from routers import db_metrics_router
from services import db_monitor
from util.object_id import get_current_user

PREFIX = "/api/system/db/metrics"
HOST_URL = f"{PREFIX}/host"
REALTIME_URL = f"{PREFIX}/realtime"

#: The ``host`` section exactly as ``collect_host_metrics()["host"]`` builds it.
FIXED_HOST = {
    "cpu": {"user_pct": 1.0, "system_pct": 2.0, "idle_pct": 96.0, "iowait_pct": 1.0},
    "mem": {"total_mb": 16000.0, "used_mb": 8000.0, "available_mb": 7000.0},
    "load": {"1m": 0.5, "5m": 0.4, "15m": 0.3},
    "disk": {"mount": "/", "used_pct": 40.0, "free_gb": 100.0},
    "net": {"rx_kbps": 12.0, "tx_kbps": 3.0},
    "diskio": {"read_kbps": 1.0, "write_kbps": 2.0},
    "proc": {"rss_mb": 120.0, "uptime_hours": 5.5},
}

#: The ``pool`` section on success (``pool_metrics()``, db_monitor.py:430).
FIXED_POOL = {
    "size": 5,
    "checkedout": 1,
    "overflow": 0,
    "capacity": 10,
    "utilization_percent": 10.0,
    "waited": 0,
}

#: The degraded ``pool`` shape when the engine/pool cannot be inspected.
POOL_DEGRADED_KEYS = {"available", "reason"}

#: Oracle-derived sections of ``/realtime`` that must never appear here.
ORACLE_SECTIONS = {"storage", "load", "sessions", "warnings", "observed_at"}


@pytest.fixture
def admin_client(monkeypatch):
    """Router-only app plus an admin whose DB re-check succeeds."""
    state = {"roles": ["admin"]}

    async def get_by_oid(oid):
        return state

    monkeypatch.setattr(
        backends, "get_users_repo", lambda: SimpleNamespace(get_by_oid=get_by_oid)
    )
    app = FastAPI()
    app.include_router(db_metrics_router.router, prefix=PREFIX)
    app.dependency_overrides[get_current_user] = lambda: {
        "id": "admin",
        "per": ["admin"],
    }
    return TestClient(app), state


@pytest.fixture
def stub_local_sections(monkeypatch):
    """Pin the two local builders so both endpoints can be compared verbatim."""
    monkeypatch.setattr(
        db_monitor,
        "collect_host_metrics",
        lambda: {"host": FIXED_HOST, "series": {"host.load.1m": 0.5}},
    )
    monkeypatch.setattr(db_monitor, "pool_metrics", lambda: FIXED_POOL)


def test_host_endpoint_requires_login():
    app = FastAPI()
    app.include_router(db_metrics_router.router, prefix=PREFIX)
    assert TestClient(app).get(HOST_URL).status_code == 401


def test_host_endpoint_returns_envelope_and_local_sections_only(admin_client):
    http, _ = admin_client
    response = http.get(HOST_URL)

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"

    body = response.json()
    assert body["status"] == "ok"
    assert body["code"] == 200
    assert set(body["data"]) == {"host", "pool", "api"}
    assert ORACLE_SECTIONS.isdisjoint(body["data"])


def test_host_and_pool_are_verbatim_identical_to_realtime(
    admin_client, stub_local_sections, monkeypatch
):
    """``/host`` must embed the very same two sections ``/realtime`` embeds."""
    http, _ = admin_client

    async def get_database_status(session):
        return {"storage": {"available": True}, "load": {"available": True}}

    async def collect_sessions(session):
        return {"total": 0, "active": 0, "inactive": 0, "items": []}

    monkeypatch.setattr(db_monitor, "get_database_status", get_database_status)
    monkeypatch.setattr(db_monitor, "collect_sessions", collect_sessions)

    async def session():
        yield None

    http.app.dependency_overrides[get_session] = session

    local = http.get(HOST_URL).json()["data"]
    realtime = http.get(REALTIME_URL).json()["data"]

    assert local["host"] == realtime["host"] == FIXED_HOST
    assert local["pool"] == realtime["pool"] == FIXED_POOL
    # /realtime hands back the collector's nested host dict unchanged: no extra
    # key is mixed into the host section by either endpoint.
    assert set(local["host"]) == set(FIXED_HOST)


def test_real_collector_locks_the_frontend_key_contract(admin_client):
    """No stubs: the shipped collector's key names are the frontend contract."""
    http, _ = admin_client
    body = http.get(HOST_URL).json()["data"]

    host = body["host"]
    assert set(host) == {"cpu", "mem", "load", "disk", "net", "diskio", "proc"}
    assert set(host["cpu"]) == {"user_pct", "system_pct", "idle_pct", "iowait_pct"}
    assert set(host["mem"]) == {"total_mb", "used_mb", "available_mb"}
    assert set(host["load"]) == {"1m", "5m", "15m"}
    assert set(host["disk"]) == {"mount", "used_pct", "free_gb"}
    assert set(host["net"]) == {"rx_kbps", "tx_kbps"}
    assert set(host["diskio"]) == {"read_kbps", "write_kbps"}
    assert set(host["proc"]) == {"rss_mb", "uptime_hours"}

    assert set(body["pool"]) in (set(FIXED_POOL), POOL_DEGRADED_KEYS)
    assert set(body["api"]) == {"window_seconds", "items"}


def test_host_endpoint_makes_zero_oracle_round_trips(admin_client, monkeypatch):
    """Every db collector counts calls; the session dependency must be unused."""
    http, _ = admin_client
    calls: list[str] = []

    def async_stub(name, value):
        async def stub(*args, **kwargs):
            calls.append(name)
            return value

        return stub

    monkeypatch.setattr(db_monitor, "get_database_status",
                        async_stub("get_database_status", {}))
    monkeypatch.setattr(db_monitor, "collect_sessions",
                        async_stub("collect_sessions", {}))
    monkeypatch.setattr(db_monitor, "collect_fast", async_stub("collect_fast", {}))
    monkeypatch.setattr(db_monitor, "collect_slow", async_stub("collect_slow", {}))
    monkeypatch.setattr(db_metrics_router, "get_realtime_metrics",
                        async_stub("get_realtime_metrics", {}))

    async def session_must_not_be_used():
        calls.append("get_session")
        raise AssertionError("the host panel must not open a database session")

    http.app.dependency_overrides[get_session] = session_must_not_be_used

    response = http.get(HOST_URL)

    assert response.status_code == 200
    assert set(response.json()["data"]) == {"host", "pool", "api"}
    assert calls == [], f"Oracle collectors were invoked: {calls}"


def test_host_route_declares_no_database_session_dependency():
    route = next(
        route
        for route in db_metrics_router.router.routes
        if getattr(route, "path", None) == "/host"
    )
    dependency_names = {
        getattr(dependency.call, "__name__", "") for dependency in route.dependant.dependencies
    }
    assert "get_session" not in dependency_names
    assert "get_current_user" in dependency_names
    assert "session" not in inspect.signature(route.endpoint).parameters


@pytest.mark.parametrize(
    "roles,banned",
    [(["student"], False), (["department"], False), (["admin"], True)],
)
def test_host_endpoint_rechecks_admin_before_collecting(
    admin_client, monkeypatch, roles, banned
):
    http, state = admin_client
    state.update(roles=roles, banned=banned)

    def never():
        raise AssertionError("must not collect before authorization")

    monkeypatch.setattr(db_metrics_router, "get_local_metrics", never)
    assert http.get(HOST_URL).status_code == 403


def test_host_endpoint_rejects_token_without_admin_permission(admin_client, monkeypatch):
    http, _ = admin_client

    def never():
        raise AssertionError("must not collect before authorization")

    monkeypatch.setattr(db_metrics_router, "get_local_metrics", never)
    http.app.dependency_overrides[get_current_user] = lambda: {"id": "u", "per": []}
    assert http.get(HOST_URL).status_code == 403


def test_host_endpoint_is_mounted_by_main_with_the_metrics_prefix():
    import main

    paths = {getattr(route, "path", None) for route in main.app.routes}
    assert HOST_URL in paths
    assert REALTIME_URL in paths
