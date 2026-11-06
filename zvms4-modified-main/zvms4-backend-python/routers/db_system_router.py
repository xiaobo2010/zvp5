"""Administrative Oracle ADB endpoints: monitoring, retention purge and backup."""

from __future__ import annotations

import logging
import re
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from db import backends
from db.oracle.session import get_session
from routers.system_router import _require_system_admin
from services import db_maintenance
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
    ai_agent_events_retention_days,
    ai_call_logs_retention_days,
    ai_prompt_retention_days,
    progress_snapshot,
    retention_cutoff,
    retention_cutoffs,
    start_background_purge,
)
from typings.log import inject_log
from util.object_id import get_current_user
from util.route_errors import DatabaseAvailabilityRoute
from util.user import get_user_display_name


_logger = logging.getLogger(__name__)

# 提交失败对客户端的固定文案：systemd/sudo 的 stderr 里可能有主机名、路径与
# 凭据片段，只留在审计日志里，不进 HTTP 响应。
BACKUP_FAILED_DETAIL = "备份任务执行失败"

_CREDENTIAL_RE = re.compile(
    r"\b((?:password|passwd|pwd|secret|token|user|userid|username)=)[^\s,;]+",
    re.IGNORECASE,
)


def _audit_summary(text: str) -> str:
    """审计用的一句话摘要：抹掉凭据型键值并压平空白（长度与 stderr 同量级）。"""
    scrubbed = _CREDENTIAL_RE.sub(r"\1<redacted>", text or "")
    return " ".join(scrubbed.split())[:300]


class _DatabaseStatusRoute(DatabaseAvailabilityRoute):
    """Map unexpected failures to 503 without leaking internals."""

    log_message = "Database status request failed"
    unavailable_detail = "数据库监控暂时不可用"


router = APIRouter(route_class=_DatabaseStatusRoute)


# ---------------------------------------------------------------------------
# 维护端点（services/db_maintenance.py）
#
# 所有校验都翻成 HTTPException：本 router 的 route_class 会把**非 HTTP** 异常
# 统一变成 503，参数错误若走 ValueError 就会被伪装成「数据库不可用」。
# ---------------------------------------------------------------------------


class MaintenancePlanRequest(BaseModel):
    action: str
    tables: list[str] | None = None
    dry_run: bool = False


class MaintenanceRunRequest(BaseModel):
    action: str
    tables: list[str] | None = None
    dry_run: bool = False
    confirm: str | None = None


class CacheFlushRequest(BaseModel):
    scopes: list[str]


class CacheRefreshRequest(BaseModel):
    scopes: list[str] | None = None


def _maintenance_http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, db_maintenance.MaintenanceError):
        return HTTPException(status_code=exc.status_code, detail=exc.detail)
    return HTTPException(status_code=400, detail="维护请求不合法")


def _validated_action(action: str) -> str:
    value = (action or "").strip().lower()
    if value not in db_maintenance.ACTIONS:
        raise HTTPException(
            status_code=400,
            detail="action 不合法，可选：" + ", ".join(db_maintenance.ACTIONS),
        )
    return value


def _validated_tables(tables: list[str] | None) -> list[str]:
    try:
        return db_maintenance._normalize_tables(tables)
    except db_maintenance.MaintenanceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None


def _validated_scopes(scopes: list[str] | None) -> list[str]:
    try:
        return db_maintenance._normalize_scopes(scopes)
    except db_maintenance.MaintenanceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None



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
    run would use (``tasks.retention`` 的同一套函数与窗口配置)。

    三张 ``ai_*`` 表是只读诊断项：查不了（表未迁移 / 权限不足）时只把
    ``deletable`` 置 ``null`` 并记一条 warning，绝不让整个预览 500/503。
    """
    await _require_system_admin(user)
    logs_cutoff, tasks_cutoff = retention_cutoffs()
    ai_cutoff = retention_cutoff(datetime.now(), ai_call_logs_retention_days())
    deletable_logs = await backends.get_applogs_repo(session).count_expired(logs_cutoff)
    deletable_tasks = await backends.get_tasks_repo(session).count_expired_finished(
        tasks_cutoff
    )
    try:
        deletable_ai = await backends.get_ai_call_logs_repo(session).count_expired(
            ai_cutoff
        )
    except Exception:
        _logger.warning("retention preview: ai_call_logs count failed", exc_info=True)
        deletable_ai = None
    prompt_cutoff = retention_cutoff(datetime.now(), ai_prompt_retention_days())
    events_cutoff = retention_cutoff(datetime.now(), ai_agent_events_retention_days())
    try:
        deletable_prompts = await backends.get_ai_prompt_audit_repo(
            session
        ).count_expired(prompt_cutoff)
    except Exception:
        _logger.warning(
            "retention preview: ai_prompt_audit count failed", exc_info=True
        )
        deletable_prompts = None
    try:
        deletable_events = await backends.get_ai_agent_events_repo(
            session
        ).count_expired(events_cutoff)
    except Exception:
        _logger.warning(
            "retention preview: ai_agent_events count failed", exc_info=True
        )
        deletable_events = None
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
            "ai_call_logs": {
                "cutoff": ai_cutoff.isoformat(),
                "deletable": deletable_ai,
            },
            "ai_prompt_audit": {
                "cutoff": prompt_cutoff.isoformat(),
                "deletable": deletable_prompts,
            },
            "ai_agent_events": {
                "cutoff": events_cutoff.isoformat(),
                "deletable": deletable_events,
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
    约定）；提交失败 → 500 + 固定中文文案：``services.adb_backup`` 只抛领域异常，
    必须在这里**显式**翻成 HTTPException，否则会被本 router 的 route_class 吞成 503
    丢掉语义。失败原因（已脱敏的 stderr 摘要）只写审计日志，不回给客户端。
    """
    await _require_system_admin(user)

    failure: str | None = None
    snapshot = None
    try:
        snapshot = await trigger_backup()
    except BackupBusy as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except BackupTriggerError as exc:
        failure = str(exc)

    if failure is None:
        log.with_text(
            f"管理员 {await get_user_display_name(user['id'])} 启动了 ADB 手动备份（prod → adb2）",
            type=BACKUP_AUDIT_TYPE,
            level=BACKUP_AUDIT_LEVEL,
        )
        await log.insert_log()
        return {"status": "ok", "code": 200, "data": snapshot}

    # 审计不能盖掉真正的失败：查显示名 / 写审计出错时，客户端仍拿到固定的 500。
    try:
        log.with_text(
            f"管理员 {await get_user_display_name(user['id'])} 启动 ADB 手动备份失败："
            f"{_audit_summary(failure)}",
            type=BACKUP_AUDIT_TYPE,
            level=BACKUP_AUDIT_LEVEL,
        )
        await log.insert_log()
    except Exception:
        _logger.exception("failed to audit the backup trigger failure")
    raise HTTPException(status_code=500, detail=BACKUP_FAILED_DETAIL)


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


@router.get("/maintenance/overview")
async def maintenance_overview(
    response: Response,
    user=Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """表级体量/统计新鲜度体检（只读；单个字典视图不可用只降级该组件）。"""
    await _require_system_admin(user)
    data = await db_maintenance.overview(session)
    response.headers["Cache-Control"] = "no-store"
    # 体检本身有 ``data.available``：即使字典全不可读也返回 200 + 降级说明，
    # 否则监控页会因为连接抖动整页 503。
    return {"status": "ok", "code": 200, "data": data}


@router.get("/maintenance/suggestions")
async def maintenance_suggestions(
    response: Response,
    user=Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """优化建议（统计过期 / 索引不可用 / 层级过高 / 大表无索引 / 重复索引）。"""
    await _require_system_admin(user)
    data = await db_maintenance.suggestions(session)
    response.headers["Cache-Control"] = "no-store"
    return {"status": "ok", "code": 200, "data": data}


@router.post("/maintenance/plan")
async def maintenance_plan(
    payload: MaintenancePlanRequest,
    response: Response,
    user=Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """只生成 SQL 计划，不执行（dry-run 的前半段，可在前端先审阅）。"""
    await _require_system_admin(user)
    action = _validated_action(payload.action)
    tables = _validated_tables(payload.tables)
    try:
        data = await db_maintenance.build_plan(session, action, tables)
    except Exception as exc:  # noqa: BLE001 - 领域错误转 4xx，其余交给 route_class
        if isinstance(exc, db_maintenance.MaintenanceError):
            raise _maintenance_http_error(exc) from None
        raise
    response.headers["Cache-Control"] = "no-store"
    return {"status": "ok", "code": 200, "data": data}


@router.post("/maintenance/run")
async def maintenance_run(
    payload: MaintenanceRunRequest,
    response: Response,
    user=Depends(get_current_user),
):
    """启动维护作业（后台任务），立刻返回 ``{job_id, started_at, state}``。

    ``dry_run=true`` 时只生成计划、不执行 SQL，可以省略 ``confirm``；真正执行
    必须带 ``confirm="RUN"``（防误触）。已有作业在跑 → 409。
    """
    await _require_system_admin(user)
    action = _validated_action(payload.action)
    tables = _validated_tables(payload.tables)
    if not payload.dry_run and (payload.confirm or "").strip() != "RUN":
        raise HTTPException(status_code=400, detail='危险操作需要 confirm="RUN"')

    try:
        started = db_maintenance.start_job(
            action, tables, bool(payload.dry_run), str(user["id"])
        )
    except db_maintenance.JobBusy as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except db_maintenance.MaintenanceError as exc:
        raise _maintenance_http_error(exc) from None

    response.headers["Cache-Control"] = "no-store"
    return {"status": "ok", "code": 200, "data": started}


@router.get("/maintenance/progress")
async def maintenance_progress(
    response: Response,
    user=Depends(get_current_user),
):
    """维护作业进度（进程内状态，单 worker 前提与清理作业一致）。"""
    await _require_system_admin(user)
    response.headers["Cache-Control"] = "no-store"
    return {"status": "ok", "code": 200, "data": db_maintenance.progress_snapshot()}


@router.post("/cache/flush")
async def cache_flush(
    payload: CacheFlushRequest,
    response: Response,
    user=Depends(get_current_user),
):
    """按逻辑域失效 Redis 缓存（下一次读请求自动重建）。"""
    await _require_system_admin(user)
    scopes = _validated_scopes(payload.scopes)
    data = await db_maintenance.flush_cache(scopes)
    response.headers["Cache-Control"] = "no-store"
    return {"status": "ok", "code": 200, "data": data}


@router.post("/cache/refresh")
async def cache_refresh(
    payload: CacheRefreshRequest,
    response: Response,
    user=Depends(get_current_user),
):
    """重新生成设置中心 / 角色策略的进程内快照（失效后再生成）。"""
    await _require_system_admin(user)
    if payload.scopes is not None:
        # scopes 只用于校验（前端可以原样回传）；快照刷新覆盖两处全部内容。
        _validated_scopes(payload.scopes)
    data = await db_maintenance.refresh_snapshots()
    response.headers["Cache-Control"] = "no-store"
    return {"status": "ok", "code": 200, "data": data}


__all__ = ["router"]

