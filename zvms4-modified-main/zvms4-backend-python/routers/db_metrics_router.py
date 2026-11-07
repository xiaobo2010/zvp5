"""Read-only database performance endpoints (design §5.1 - §5.4).

Prefix ``/api/system/db/metrics`` (mounted in ``main.py``).  Admin-only, same
envelope as ``/api/system/db/*``: ``{"status", "code", "data"}``.

「不可用 → 503」的错误边界与 ``db_system_router`` 共用
:class:`util.route_errors.DatabaseAvailabilityRoute`；本模块只覆盖日志前缀与
detail 文案，响应形状与 ``/api/system/db/*`` 保持一致。
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from db.oracle.session import get_session
from routers.system_router import _require_system_admin
from services.db_monitor import get_local_metrics, get_realtime_metrics
from services.perf_sampler import query_endpoints, query_history, query_top_sql
from util.object_id import get_current_user
from util.route_errors import DatabaseAvailabilityRoute

_MAX_SERIES = 12


class _MetricsStatusRoute(DatabaseAvailabilityRoute):
    """Map unexpected failures to 503 without leaking internals."""

    log_message = "Database metrics request failed"
    unavailable_detail = "数据库性能监控暂时不可用"


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


@router.get("/host")
async def metrics_host(
    response: Response,
    user=Depends(get_current_user),
):
    """Local server panel: ``/proc`` host metrics + this process' pool/api.

    Deliberately **stateless with respect to the database**: the endpoint does
    not depend on ``get_session`` and calls no Oracle collector, so the panel
    renders even while the database is unreachable or still warming up (the
    realtime panel waits for Oracle; this one does not).  ``host``/``pool``
    keep the exact shapes ``/realtime`` returns, so the frontend reuses one
    renderer for both.
    """
    await _require_system_admin(user)
    response.headers["Cache-Control"] = "no-store"
    return {"status": "ok", "code": 200, "data": await asyncio.to_thread(get_local_metrics)}


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
        data = await asyncio.to_thread(query_history, keys, range_, agg)
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
    return {"status": "ok", "code": 200, "data": await asyncio.to_thread(query_top_sql, limit)}


@router.get("/endpoints")
async def metrics_endpoints(
    range_: str = Query("1h", alias="range"),
    limit: int = Query(20, ge=1, le=100),
    user=Depends(get_current_user),
):
    """Persisted per-route latency aggregates over the requested range."""
    await _require_system_admin(user)
    try:
        data = await asyncio.to_thread(query_endpoints, range_, limit)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {"status": "ok", "code": 200, "data": data}


__all__ = ["router"]
