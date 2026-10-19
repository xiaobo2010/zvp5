"""Administrative Oracle ADB endpoints: monitoring, retention purge and backup."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from sqlalchemy.ext.asyncio import AsyncSession

from db import backends
from db.oracle.session import get_session
from routers.system_router import _require_system_admin
from services.adb_backup import (
    AUDIT_LEVEL as BACKUP_AUDIT_LEVEL,
    AUDIT_TYPE as BACKUP_AUDIT_TYPE,
    BackupBusy,
    BackupTriggerError,
    backup_snapshot,
    trigger_backup,
)
from services.db_monitor import get_database_status
from tasks.retention import (
    AUDIT_LEVEL,
    AUDIT_TYPE,
    RetentionBusy,
    progress_snapshot,
    retention_cutoffs,
    start_background_purge,
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
    """Start the retention purge as a **background job**; returns its handle.

    立即返回 ``{job_id, started_at, state: "running"}``（HTTP 200，envelope
    ``status=ok``）。真正干活的是 ``tasks.retention.start_background_purge`` 里的
    后台任务：它先让全站进维护态（``updated_by='auto:retention'``），逐个批量
    清理，然后**无论成功失败**都恢复；进度看
    ``GET /api/system/db/retention/progress``。已经在清理 → 409（沿用既有约定，
    detail 里带当前 phase）而不是排队。
    """
    await _require_system_admin(user)

    try:
        started = start_background_purge(str(user["id"]))
    except RetentionBusy as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    # 审计「谁发起了这次清理」：结果行由后台任务在收尾时写（同一 type/level）。
    log.with_text(
        f"管理员 {await get_user_display_name(user['id'])} 启动了数据库历史数据清理"
        f"（后台任务 {started['job_id']}，期间系统进入维护状态）",
        type=AUDIT_TYPE,
        level=AUDIT_LEVEL,
    )
    await log.insert_log()

    return {"status": "ok", "code": 200, "data": started}


@router.get("/retention/progress")
async def retention_progress(
    response: Response,
    user=Depends(get_current_user),
):
    """Database-cleanup job progress (frozen shape, design §5.5).

    管理员专用（维护页不消费本端点）：清理期间维护中间件放行整个 ``/api/system``
    前缀，所以管理员在维护态里照样能轮询它。
    """
    await _require_system_admin(user)
    # 进度是瞬态的：别让任何一层（浏览器/反代）缓存它。
    response.headers["Cache-Control"] = "no-store"
    return {"status": "ok", "code": 200, "data": progress_snapshot()}


@router.post("/backup/run")
async def backup_run(
    user=Depends(get_current_user),
    log=Depends(inject_log),
):
    """Start one manual ADB backup (prod → adb2); returns the current snapshot.

    真正干活的是服务器上的 ``adb-sync.service``（oneshot，实测约 15 分钟），本端点
    只**提交**任务并立刻返回快照 —— 不能把请求挂 15 分钟；进度轮询
    ``GET /api/system/db/backup/progress``。已有备份在跑 → 409（沿用清理那边的既有
    约定）；提交失败 → 500：``services.adb_backup`` 只抛领域异常，必须在这里
    **显式**翻成 HTTPException，否则会被本 router 的 route_class 吞成 503 丢掉语义。
    """
    await _require_system_admin(user)

    try:
        snapshot = await trigger_backup()
    except BackupBusy as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except BackupTriggerError as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    log.with_text(
        f"管理员 {await get_user_display_name(user['id'])} 启动了 ADB 手动备份（prod → adb2）",
        type=BACKUP_AUDIT_TYPE,
        level=BACKUP_AUDIT_LEVEL,
    )
    await log.insert_log()

    return {"status": "ok", "code": 200, "data": snapshot}


@router.get("/backup/progress")
async def backup_progress(
    response: Response,
    user=Depends(get_current_user),
):
    """ADB manual-backup progress (frozen shape; ``state`` ∈ idle/running/ok/failed).

    只回结构化字段 + 中文 ``message``：备份日志原文与其中的 DSN 永不外泄
    （见 ``services/adb_backup``）。
    """
    await _require_system_admin(user)
    response.headers["Cache-Control"] = "no-store"
    return {"status": "ok", "code": 200, "data": await backup_snapshot()}


__all__ = ["router"]

