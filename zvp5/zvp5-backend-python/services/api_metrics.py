"""In-process per-endpoint latency statistics (design §5.1 / §5.4).

Two hard requirements shape this module:

* **Route templates only.**  The middleware reads ``scope["route"].path`` (the
  FastAPI template, e.g. ``/api/v1/activities/{aid}/members``), never the raw
  URL.  Raw URLs would blow up the key space with every path parameter value.
* **Bounded memory.**  The recorder keeps at most ``MAX_ROUTES`` (method, route)
  buckets; anything beyond that collapses into ``(other)``.  Latency
  percentiles come from a fixed-boundary histogram per bucket, so a slow
  request never allocates per-request memory.

The window is drained by the background sampler (every host tick) and written to
``perf_endpoints``; the realtime endpoint reads it non-destructively.
``window_seconds`` is the **real span** the current window covers (seconds since
the last drain, or since process start). It is not the nominal 60s: in
production the sampler drains every 30s, so the honest value is at most ~30s,
and reporting a nominal figure would overstate how much traffic the numbers
describe.
"""

from __future__ import annotations

import bisect
import math
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

# Histogram boundaries in milliseconds; the last bucket is ">= 60000 ms" and is
# reported as the observed maximum.
BOUNDS_MS: tuple[float, ...] = (
    1.0, 2.0, 3.0, 5.0, 8.0, 13.0, 21.0, 34.0, 55.0, 89.0, 144.0, 233.0, 377.0,
    610.0, 987.0, 1597.0, 2584.0, 4181.0, 6765.0, 10946.0, 17711.0, 28657.0, 60000.0,
)

MAX_ROUTES = 300
OVERFLOW_ROUTE = "(other)"

EXCLUDED_PATHS = frozenset({"/api/health", "/api/version", "/favicon.ico"})
EXCLUDED_PREFIXES = ("/api/system/db/metrics", "/assets", "/static", "/socket.io")
STATIC_SUFFIXES = (
    ".js", ".mjs", ".css", ".map", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico",
    ".webp", ".woff", ".woff2", ".ttf", ".eot", ".txt", ".html", ".json",
)


def is_excluded_path(path: str) -> bool:
    """True for paths the recorder must ignore (§5.4 exclusion list)."""
    if not path:
        return True
    if path in EXCLUDED_PATHS or path.startswith(EXCLUDED_PREFIXES):
        return True
    lowered = path.lower()
    return any(lowered.endswith(suffix) for suffix in STATIC_SUFFIXES)


@dataclass
class _RouteStats:
    count: int = 0
    err_count: int = 0
    total_ms: float = 0.0
    max_ms: float = 0.0
    buckets: list[int] = field(default_factory=lambda: [0] * (len(BOUNDS_MS) + 1))

    def add(self, duration_ms: float, is_error: bool) -> None:
        self.count += 1
        self.total_ms += duration_ms
        if duration_ms > self.max_ms:
            self.max_ms = duration_ms
        if is_error:
            self.err_count += 1
        self.buckets[bisect.bisect_left(BOUNDS_MS, duration_ms)] += 1

    def percentile(self, fraction: float) -> float:
        """Upper bound of the bucket containing the requested percentile."""
        if self.count == 0:
            return 0.0
        target = math.ceil(self.count * fraction)
        cumulative = 0
        for index, bucket in enumerate(self.buckets):
            cumulative += bucket
            if cumulative >= target:
                return BOUNDS_MS[index] if index < len(BOUNDS_MS) else self.max_ms
        return self.max_ms

    def item(self, method: str, route: str) -> dict[str, Any]:
        return {
            "method": method,
            "route": route,
            "count": self.count,
            "err_count": self.err_count,
            "avg_ms": round(self.total_ms / self.count, 2) if self.count else 0.0,
            "p95_ms": round(self.percentile(0.95), 2),
            "max_ms": round(self.max_ms, 2),
        }


class ApiMetricsRecorder:
    """Bounded, lock-guarded endpoint statistics for one process window."""

    def __init__(
        self,
        max_routes: int = MAX_ROUTES,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._max_routes = max_routes
        self._clock = clock
        self._lock = threading.Lock()
        self._routes: dict[tuple[str, str], _RouteStats] = {}
        self._window_started_at = clock()

    def record(
        self, method: str, route: str, status_code: int, duration_ms: float
    ) -> None:
        if not route:
            return
        key = (str(method).upper(), route)
        is_error = int(status_code) >= 400
        with self._lock:
            stats = self._routes.get(key)
            if stats is None:
                if len(self._routes) >= self._max_routes:
                    key = (key[0], OVERFLOW_ROUTE)
                    stats = self._routes.get(key)
                if stats is None:
                    stats = _RouteStats()
                    self._routes[key] = stats
            stats.add(duration_ms, is_error)

    def _elapsed_seconds_locked(self) -> float:
        return max(0.0, self._clock() - self._window_started_at)

    def _snapshot_locked(self) -> dict[str, Any]:
        items = [
            stats.item(method, route)
            for (method, route), stats in self._routes.items()
            if stats.count
        ]
        items.sort(key=lambda item: (-item["count"], item["method"], item["route"]))
        # The real covered span, rounded to whole seconds: the numbers describe
        # whatever was recorded since the last drain, nothing more.
        return {
            "window_seconds": int(round(self._elapsed_seconds_locked())),
            "items": items,
        }

    def snapshot(self) -> dict[str, Any]:
        """Current window without resetting it (realtime endpoint)."""
        with self._lock:
            return self._snapshot_locked()

    def drain(self) -> dict[str, Any]:
        """Export the window and start a new one (sampler → ``perf_endpoints``)."""
        with self._lock:
            data = self._snapshot_locked()
            elapsed = self._elapsed_seconds_locked()
            self._routes = {}
            self._window_started_at = self._clock()
        data["elapsed_seconds"] = round(elapsed, 3)
        return data

    def reset(self) -> None:
        with self._lock:
            self._routes = {}
            self._window_started_at = self._clock()


# The process-wide recorder: the middleware writes it, the sampler drains it.
api_metrics = ApiMetricsRecorder()


class ApiMetricsMiddleware:
    """Pure-ASGI middleware timing every routed request.

    Registered *inside* the maintenance middleware (a blocked request never
    reaches a route), so only requests that actually executed a handler are
    counted.  ``recorder`` is injectable for tests.
    """

    def __init__(self, app, recorder: ApiMetricsRecorder | None = None) -> None:
        self.app = app
        self.recorder = recorder if recorder is not None else api_metrics
        # FastAPI 0.16x 起 ``include_router`` 变成 ``_IncludedRouter`` 懒解析：命中后
        # ``scope["route"]`` 仍是内层 ``APIRoute``，其 ``.path`` 只是路由器内的局部
        # 模板（丢了 include 前缀），真正的完整模板在该 wrapper 的
        # ``_effective_candidates[*].path_format``。按 ``id(route)`` 缓存
        # ``[(path_regex, path_format)]``，避免每请求扫路由表。
        self._display_cache: dict[int, list[tuple[object, str]]] = {}

    def _display_path(self, scope, route) -> str | None:
        """还原 include 前缀后的完整路由模板（缺失时回退到局部 ``route.path``）。"""
        key = id(route)
        candidates = self._display_cache.get(key)
        if candidates is None:
            candidates = []
            router = getattr(scope.get("app"), "router", None)
            for entry in getattr(router, "routes", ()) or ():
                for cand in getattr(entry, "_effective_candidates", None) or ():
                    if getattr(cand, "original_route", None) is route:
                        regex = getattr(cand, "path_regex", None)
                        fmt = getattr(cand, "path_format", None) or getattr(
                            cand, "path", None
                        )
                        if regex is not None and fmt:
                            candidates.append((regex, fmt))
            # 只有真找到 include 包装才缓存；否则留空、下次重试（懒挂载可能尚未填充），
            # 直接注册的路由/旧版 FastAPI 永远为空，稳定回退。
            if candidates:
                self._display_cache[key] = candidates
        real = scope.get("path", "")
        for regex, fmt in candidates or ():
            if regex.match(real):
                return fmt
        return getattr(route, "path", None)

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        if str(scope.get("method", "")).upper() == "OPTIONS":
            await self.app(scope, receive, send)
            return
        if is_excluded_path(scope.get("path", "")):
            await self.app(scope, receive, send)
            return

        recorder = self.recorder
        method = scope.get("method", "")
        started = time.perf_counter()
        recorded = False

        def route_template() -> str | None:
            route = scope.get("route")
            if route is None:
                return None
            return self._display_path(scope, route)

        async def send_wrapper(message):
            nonlocal recorded
            if message["type"] == "http.response.start" and not recorded:
                recorded = True
                route = route_template()
                # Unmatched requests have no route: skipping them is what keeps the
                # cardinality bounded (§5.4).
                if route:
                    recorder.record(
                        method, route, message["status"], (time.perf_counter() - started) * 1000
                    )
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            if not recorded:
                route = route_template()
                if route:
                    recorder.record(method, route, 500, (time.perf_counter() - started) * 1000)
            raise


__all__ = [
    "ApiMetricsMiddleware",
    "ApiMetricsRecorder",
    "api_metrics",
    "is_excluded_path",
]
