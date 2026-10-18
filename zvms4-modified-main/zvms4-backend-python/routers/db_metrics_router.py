"""Read-only database performance endpoints (design §5.1 - §5.4).

Prefix ``/api/system/db/metrics`` (mounted in ``main.py``).  Admin-only, same
envelope as ``/api/system/db/*``: ``{"status", "code", "data"}``.

The route class is a deliberate *copy* of Lane 2's ``_DatabaseStatusRoute``
rather than an import: the two lanes own separate files and must not couple.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from sqlalchemy.ext.asyncio import AsyncSession

from db.oracle.session import get_session
from routers.system_router import _require_system_admin
from services.db_monitor import get_realtime_metrics
from services.perf_sampler import query_endpoints, query_history, query_top_sql
from util.object_id import get_current_user

_logger = logging.getLogger(__name__)

_MAX_SERIES = 12


class _MetricsStatusRoute(APIRoute):
    """Map unexpected failures to 503 without leaking internals."""

    def get_route_handler(self):
        handler = super().get_route_handler()

        async def with_availability_errors(request):
            # Authentication also accesses Oracle, before the endpoint function
            # runs. Keep its failures inside this boundary.
            try:
                return await handler(request)
            except (HTTPException, RequestValidationError):
                raise
            except Exception:
                _logger.exception("Database metrics request failed")
                raise HTTPException(
                    status_code=503,
                    detail="数据库性能监控暂时不可用",
                    headers={"Cache-Control": "no-store"},
                )

        return with_availability_errors


router = APIRouter(route_class=_MetricsStatusRoute)


@router.get("/realtime")
async def metrics_realtime(
    response: Response,
    user=Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Live panel: storage/load (15s cache) + pool, host, sessions and api window."""
    await _require_system_admin(user)
    data = await get_realtime_metrics(session)
    status = data.pop("status", "degraded")
    response.headers["Cache-Control"] = "no-store"
    return {"status": status, "code": 200, "data": data}


@router.get("/history")
async def metrics_history(
    series: str = Query(..., description="逗号分隔的 series 键，最多 12 个"),
    range_: str = Query("24h", alias="range"),
    agg: str = Query("avg"),
    user=Depends(get_current_user),
):
    """Aggregated time series for the trend charts."""
    await _require_system_admin(user)

    keys = [key.strip() for key in series.split(",") if key.strip()]
    if not keys:
        raise HTTPException(status_code=422, detail="series 不能为空")
    if len(keys) > _MAX_SERIES:
        raise HTTPException(status_code=422, detail=f"series 最多 {_MAX_SERIES} 个")
    try:
        data = query_history(keys, range_, agg)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {"status": "ok", "code": 200, "data": data}


@router.get("/top-sql")
async def metrics_top_sql(
    limit: int = Query(10, ge=1, le=50),
    user=Depends(get_current_user),
):
    """Latest schema-filtered Top SQL snapshot."""
    await _require_system_admin(user)
    return {"status": "ok", "code": 200, "data": query_top_sql(limit)}


@router.get("/endpoints")
async def metrics_endpoints(
    range_: str = Query("1h", alias="range"),
    limit: int = Query(20, ge=1, le=100),
    user=Depends(get_current_user),
):
    """Persisted per-route latency aggregates over the requested range."""
    await _require_system_admin(user)
    try:
        data = query_endpoints(range_, limit)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {"status": "ok", "code": 200, "data": data}


__all__ = ["router"]
