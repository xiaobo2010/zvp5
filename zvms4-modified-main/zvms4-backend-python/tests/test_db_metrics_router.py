"""Database metrics router: envelope, auth, validation and error boundaries."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from db import backends
from db.oracle.session import get_session
from routers import db_metrics_router
from util.object_id import get_current_user

PREFIX = "/api/system/db/metrics"


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
    app.include_router(db_metrics_router.router, prefix=PREFIX)
    app.dependency_overrides[get_session] = session
    app.dependency_overrides[get_current_user] = lambda: {"id": "admin", "per": ["admin"]}
    return TestClient(app), state


def test_metrics_requires_login():
    app = FastAPI()
    app.include_router(db_metrics_router.router, prefix=PREFIX)
    assert TestClient(app).get(f"{PREFIX}/realtime").status_code == 401
    assert TestClient(app).get(f"{PREFIX}/history?series=host.load.1m").status_code == 401


@pytest.mark.parametrize(
    "roles,banned", [(["student"], False), (["department"], False), (["admin"], True)]
)
def test_metrics_rechecks_privileges_before_collecting(client, monkeypatch, roles, banned):
    http, state = client
    state.update(roles=roles, banned=banned)

    async def never(session):
        raise AssertionError("must not collect before authorization")

    monkeypatch.setattr(db_metrics_router, "get_realtime_metrics", never)
    assert http.get(f"{PREFIX}/realtime").status_code == 403


def test_realtime_envelope_and_sections(client, monkeypatch):
    http, _ = client
    observation = {
        "status": "ok",
        "observed_at": "2026-10-18T03:40:12+00:00",
        "cache": {"cached": False, "age_seconds": 0.0, "ttl_seconds": 15},
        "storage": {"available": True},
        "load": {"available": True, "connection_pool": {"available": True}},
        "pool": {"size": 5, "checkedout": 1, "overflow": 0, "capacity": 10,
                 "utilization_percent": 10.0, "waited": 0},
        "host": {"cpu": {"user_pct": 1.2}},
        "sessions": {"total": 6, "active": 1, "inactive": 5, "items": []},
        "api": {"window_seconds": 60, "items": []},
        "warnings": [],
    }

    async def collect(session):
        return observation

    monkeypatch.setattr(db_metrics_router, "get_realtime_metrics", collect)
    response = http.get(f"{PREFIX}/realtime")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["status"] == "ok" and body["code"] == 200
    assert set(body["data"]) == {
        "observed_at", "cache", "storage", "load", "pool", "host", "sessions", "api",
        "warnings",
    }
    assert body["data"]["load"]["connection_pool"] == {"available": True}


def test_realtime_collection_failure_is_503_without_internal_details(client, monkeypatch):
    http, _ = client

    async def failed(session):
        raise ConnectionError("do not expose DSN/password")

    monkeypatch.setattr(db_metrics_router, "get_realtime_metrics", failed)
    response = http.get(f"{PREFIX}/realtime")
    assert response.status_code == 503
    assert "DSN" not in response.text
    assert response.headers["cache-control"] == "no-store"


def test_history_passes_query_and_returns_payload(client, monkeypatch):
    http, _ = client
    seen = {}

    def fake_history(keys, range_key, agg):
        seen.update(keys=keys, range=range_key, agg=agg)
        return {
            "from": "2026-10-17T03:40:00+00:00",
            "to": "2026-10-18T03:40:00+00:00",
            "step_seconds": 720,
            "agg": agg,
            "unknown_series": ["foo.bar"],
            "series": [{"key": keys[0], "unit": "%", "points": []}],
        }

    monkeypatch.setattr(db_metrics_router, "query_history", fake_history)
    response = http.get(f"{PREFIX}/history?series=host.cpu.user_pct,foo.bar&range=24h")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["data"]["step_seconds"] == 720
    assert body["data"]["unknown_series"] == ["foo.bar"]
    assert seen == {"keys": ["host.cpu.user_pct", "foo.bar"], "range": "24h", "agg": "avg"}


def test_history_rejects_empty_and_oversized_series_lists(client, monkeypatch):
    http, _ = client

    def never(*args, **kwargs):
        raise AssertionError("must not query with an invalid series list")

    monkeypatch.setattr(db_metrics_router, "query_history", never)
    assert http.get(f"{PREFIX}/history?series=").status_code == 422
    assert http.get(f"{PREFIX}/history?series=,").status_code == 422
    too_many = ",".join(f"host.metric.{index}" for index in range(13))
    assert http.get(f"{PREFIX}/history?series={too_many}").status_code == 422
    assert http.get(f"{PREFIX}/history").status_code == 422


def test_history_maps_value_errors_to_422(client, monkeypatch):
    http, _ = client

    def bad_range(*args, **kwargs):
        raise ValueError("不支持的 range：3h")

    monkeypatch.setattr(db_metrics_router, "query_history", bad_range)
    response = http.get(f"{PREFIX}/history?series=host.load.1m&range=3h")
    assert response.status_code == 422
    assert "3h" in response.text


def test_top_sql_and_endpoints_validate_limits(client, monkeypatch):
    http, _ = client
    seen = {}

    def fake_top(limit):
        seen["limit"] = limit
        return {"observed_at": None, "items": []}

    def fake_endpoints(range_key, limit):
        seen.update(range=range_key, limit=limit)
        return {"window_seconds": 3600, "items": []}

    monkeypatch.setattr(db_metrics_router, "query_top_sql", fake_top)
    monkeypatch.setattr(db_metrics_router, "query_endpoints", fake_endpoints)

    assert http.get(f"{PREFIX}/top-sql?limit=5").status_code == 200
    assert seen["limit"] == 5
    assert http.get(f"{PREFIX}/top-sql?limit=51").status_code == 422

    response = http.get(f"{PREFIX}/endpoints?range=1h&limit=20")
    assert response.status_code == 200
    assert response.json()["data"]["window_seconds"] == 3600
    assert seen == {"limit": 20, "range": "1h"}
    assert http.get(f"{PREFIX}/endpoints?limit=101").status_code == 422


def test_sync_sqlite_collectors_never_run_on_the_event_loop(client, monkeypatch):
    """The four sqlite collectors are synchronous: they must be off-loaded.

    ``asyncio.get_running_loop()`` inside a collector only succeeds on the event
    loop thread, so a collector that observes it is blocking the whole worker.
    """
    http, _ = client
    ran = []

    def off_loop(name, payload):
        def collect(*args):
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                ran.append(name)
                return payload
            raise AssertionError(f"{name} blocked the event loop")

        return collect

    monkeypatch.setattr(
        db_metrics_router,
        "get_local_metrics",
        off_loop("host", {"host": {"cpu": {}}, "pool": {}}),
    )
    monkeypatch.setattr(
        db_metrics_router,
        "query_history",
        off_loop("history", {"from": None, "to": None, "step_seconds": 60, "agg": "avg",
                             "unknown_series": [], "series": []}),
    )
    monkeypatch.setattr(
        db_metrics_router,
        "query_top_sql",
        off_loop("top_sql", {"observed_at": None, "items": []}),
    )
    monkeypatch.setattr(
        db_metrics_router,
        "query_endpoints",
        off_loop("endpoints", {"window_seconds": 3600, "items": []}),
    )

    assert http.get(f"{PREFIX}/host").status_code == 200
    assert http.get(f"{PREFIX}/history?series=host.load.1m").status_code == 200
    assert http.get(f"{PREFIX}/top-sql").status_code == 200
    assert http.get(f"{PREFIX}/endpoints").status_code == 200
    assert ran == ["host", "history", "top_sql", "endpoints"]


def test_wiring_keeps_shield_and_maintenance_outside_api_metrics():
    """Regression guard for main.py: the metrics middleware must stay inside the
    maintenance middleware (so 503s are not counted) and inside the shield."""
    import main

    names = [
        getattr(middleware.cls, "__name__", str(middleware.cls))
        for middleware in main.app.user_middleware
    ]
    assert names.count("ApiMetricsMiddleware") == 1
    metrics_index = names.index("ApiMetricsMiddleware")
    # user_middleware is outermost-first: shield and maintenance are registered last.
    assert names[0] == "BaseHTTPMiddleware"
    assert metrics_index > 1
    assert names.index("CORSMiddleware") > metrics_index

    paths = {getattr(route, "path", None) for route in main.app.routes}
    for path in (
        f"{PREFIX}/realtime",
        f"{PREFIX}/history",
        f"{PREFIX}/top-sql",
        f"{PREFIX}/endpoints",
    ):
        assert path in paths
