"""Administrative Oracle ADB endpoints: monitoring plus the retention purge."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from sqlalchemy.ext.asyncio import AsyncSession

from db import backends
from db.oracle.session import get_session
from routers.system_router import _require_system_admin
from services.db_monitor import get_database_status
from tasks.retention import (
    AUDIT_LEVEL,
    AUDIT_TYPE,
    RetentionBusy,
    audit_message,
    purge_now,
    retention_cutoffs,
)
from typings.log import inject_log
from util.object_id import get_current_user
from util.user import get_user_display_name

_logger = logging.getLogger(__name__)


class _DatabaseStatusRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def with_availability_errors(request):
            # Authentication and session creation also access Oracle, before
            # the endpoint function runs. Keep their failures in this boundary.
            try:
                return await handler(request)
            except (HTTPException, RequestValidationError):
                raise
            except Exception:
                _logger.exception("Database status request failed")
                raise HTTPException(
                    status_code=503,
                    detail="数据库监控暂时不可用",
                    headers={"Cache-Control": "no-store"},
                )

        return with_availability_errors


router = APIRouter(route_class=_DatabaseStatusRoute)


@router.get("/status")
async def database_status(
    response: Response,
    user=Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Show Oracle schema storage, tablespace usage and recent DB load."""
    await _require_system_admin(user)
    data = await get_database_status(session)
    status = data.get("status", "degraded")
    payload = dict(data)
    payload.pop("status", None)
    response.headers["Cache-Control"] = "no-store"
    return {"status": status, "code": 200, "data": payload}


@router.post("/retention/preview")
async def retention_preview(
    user=Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Show how many rows each table's retention window would delete (read-only).

    Counts only — nothing is deleted here, and the cutoffs are the ones the next
    run would use.
    """
    await _require_system_admin(user)
    logs_cutoff, tasks_cutoff = retention_cutoffs()
    deletable_logs = await backends.get_applogs_repo(session).count_expired(logs_cutoff)
    deletable_tasks = await backends.get_tasks_repo(session).count_expired_finished(
        tasks_cutoff
    )
    return {
        "status": "ok",
        "code": 200,
        "data": {
            "app_logs": {
                "cutoff": logs_cutoff.isoformat(),
                "deletable": deletable_logs,
            },
            "export_tasks": {
                "cutoff": tasks_cutoff.isoformat(),
                "deletable": deletable_tasks,
            },
        },
    }


@router.post("/retention/run")
async def retention_run(
    user=Depends(get_current_user),
    log=Depends(inject_log),
):
    """Run the retention purge now, up to the batch ceiling; returns the totals.

    Synchronous by design: the admin waits for the result, and a run is bounded
    by ``RETENTION_MAX_BATCHES``. A concurrent purge answers 409 instead of
    queueing.
    """
    await _require_system_admin(user)

    try:
        result = await purge_now()
    except RetentionBusy as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    log.with_text(
        f"管理员 {await get_user_display_name(user['id'])} 执行了{audit_message(result)}",
        type=AUDIT_TYPE,
        level=AUDIT_LEVEL,
    )
    await log.insert_log()

    return {"status": "ok", "code": 200, "data": result}


__all__ = ["router"]
