"""Administrative, read-only Oracle ADB monitoring endpoints."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from sqlalchemy.ext.asyncio import AsyncSession

from db.oracle.session import get_session
from routers.system_router import _require_system_admin
from services.db_monitor import get_database_status
from util.object_id import get_current_user

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


__all__ = ["router"]
