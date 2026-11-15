"""In-process endpoint statistics: histogram percentiles, bounded keys, middleware."""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from services.api_metrics import (
    MAX_ROUTES,
    ApiMetricsMiddleware,
    ApiMetricsRecorder,
    is_excluded_path,
)


class Clock:
    """Settable monotonic clock: tests control elapsed time explicitly."""

    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def test_counts_errors_average_max_and_percentile():
    recorder = ApiMetricsRecorder()
    for _ in range(99):
        recorder.record("GET", "/api/v1/activities/{aid}/members", 200, 10.0)
    recorder.record("GET", "/api/v1/activities/{aid}/members", 500, 500.0)

    items = recorder.snapshot()["items"]
    assert len(items) == 1
    item = items[0]
    assert item["route"] == "/api/v1/activities/{aid}/members"
    assert item["count"] == 100
    assert item["err_count"] == 1
    assert item["avg_ms"] == 14.9
    assert item["max_ms"] == 500.0
    # 95th percentile lands in the bucket that contains the 95th sample.
    assert 10.0 <= item["p95_ms"] <= 13.0


def test_window_seconds_is_the_real_span_not_a_nominal_value():
    clock = Clock(0.0)
    recorder = ApiMetricsRecorder(clock=clock)
    recorder.record("POST", "/api/logs", 201, 25.0)

    # 12s of accumulation: that — not a nominal 60s — is what the numbers cover.
    clock.now = 12.0
    assert recorder.snapshot()["window_seconds"] == 12
    assert recorder.snapshot()["items"][0]["count"] == 1

    drained = recorder.drain()
    assert drained["window_seconds"] == 12
    assert drained["elapsed_seconds"] == 12.0
    assert drained["items"][0]["count"] == 1
    # Drained window starts empty; snapshot is non-destructive.
    assert recorder.snapshot()["items"] == []

    # The next window reports its own span (35s since the drain).
    clock.now = 47.0
    assert recorder.snapshot()["window_seconds"] == 35


def test_route_cardinality_is_bounded_by_an_other_bucket():
    recorder = ApiMetricsRecorder(max_routes=3)
    for index in range(50):
        recorder.record("GET", f"/api/v1/users/{index}", 200, 1.0)
    items = recorder.snapshot()["items"]
    assert len(items) == 4  # max_routes + the overflow bucket
    assert sum(item["count"] for item in items) == 50
    assert "(other)" in {item["route"] for item in items}
    assert MAX_ROUTES >= 3


def test_histogram_memory_per_route_is_fixed():
    from services.api_metrics import BOUNDS_MS

    recorder = ApiMetricsRecorder()
    for index in range(5000):
        recorder.record("GET", "/api/v1/activities", 200, float(index % 1000))
    stats = recorder._routes[("GET", "/api/v1/activities")]
    assert stats.count == 5000
    # One fixed-size bucket array per route, independent of the sample count.
    assert len(stats.buckets) == len(BOUNDS_MS) + 1
    assert recorder.snapshot()["items"][0]["max_ms"] == 999.0


def test_excluded_paths_cover_health_version_static_and_self():
    assert is_excluded_path("/api/health")
    assert is_excluded_path("/api/version")
    assert is_excluded_path("/api/system/db/metrics/realtime")
    assert is_excluded_path("/api/system/db/metrics/history")
    assert is_excluded_path("/assets/index-abc.js")
    assert is_excluded_path("/socket.io/")
    assert is_excluded_path("/favicon.ico")
    assert not is_excluded_path("/api/v1/activities/123/members")


def _client(recorder):
    app = FastAPI()
    app.add_middleware(ApiMetricsMiddleware, recorder=recorder)

    @app.get("/items/{item_id}")
    async def item(item_id: str):
        return {"item_id": item_id}

    @app.get("/boom")
    async def boom():
        raise HTTPException(status_code=503, detail="nope")

    @app.get("/api/health")
    async def health():
        return {"ok": True}

    @app.get("/api/system/db/metrics/realtime")
    async def realtime():
        return {"ok": True}

    return TestClient(app)


def test_middleware_records_route_templates_not_urls():
    recorder = ApiMetricsRecorder()
    client = _client(recorder)

    assert client.get("/items/42").status_code == 200
    assert client.get("/items/43").status_code == 200
    client.get("/boom")
    client.get("/api/health")
    client.get("/api/system/db/metrics/realtime")
    client.get("/does-not-exist")

    items = recorder.snapshot()["items"]
    by_route = {item["route"]: item for item in items}
    assert set(by_route) == {"/items/{item_id}", "/boom"}
    # Two different path values, one template key.
    assert by_route["/items/{item_id}"]["count"] == 2
    assert by_route["/boom"]["err_count"] == 1
    assert by_route["/boom"]["count"] == 1


def test_include_router_prefix_is_preserved_in_recorded_template():
    """FastAPI >=0.16x 懒挂载：scope["route"].path 丢 include 前缀，须还原。

    回归背景：升级到 FastAPI 0.162 后，perf_endpoints 里出现无前缀模板
    （``/{user_id}/time``），把 ``/api/v1/users/{user_id}/time`` 记错并与其他
    前缀的同名模板撞车。完整模板在 ``_IncludedRouter._effective_candidates``。
    """
    from fastapi import APIRouter

    recorder = ApiMetricsRecorder()
    app = FastAPI()
    app.add_middleware(ApiMetricsMiddleware, recorder=recorder)
    router = APIRouter()

    @router.get("/{user_id}/time")
    async def user_time(user_id: str):
        return {"ok": user_id}

    app.include_router(router, prefix="/api/v1/users")
    client = TestClient(app)

    assert client.get("/api/v1/users/123/time").status_code == 200
    routes = {item["route"] for item in recorder.snapshot()["items"]}
    assert routes == {"/api/v1/users/{user_id}/time"}

