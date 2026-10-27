"""AI 定时任务的 HTTP 层（``/api/ai/scheduled-tasks``）。

与 ``/api/ai/api-keys`` 同一套外壳：成功 ``{"status","code","data"}``（列表再带
``metadata.size``），错误 ``detail`` 用**错误码**而不是长句——前端按 code 分支。
错误码前缀统一 ``AI_SCHEDULE_``。

权限：

* ``GET/PATCH/DELETE/run-now`` —— owner 或 admin；
* ``POST /{oid}/review`` —— 仅 admin；
* ``POST`` —— admin（不限条数）或 department（学生会，条数取
  ``ai.schedule_quota_department``），其他角色 403。

**创建即待审**：新建任务的 ``review_status`` 是 ``pending`` 而不是 ``draft``
（表默认值仍是 draft，那是给迁移/脚本用的）。理由：用户点「保存」的语义就是
「提交审核」；让他再点一次「申请审核」只是多一个没人会漏掉、但会让任务永远不跑
的按钮。``draft`` 只由「改了 prompt」产生——那时审核结论确实已失效。

挂载点由主会话接线（``app.include_router(..., prefix="/api/ai/scheduled-tasks")``），
本模块不改 ``main.py``。
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Mapping

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from services.ai import config as ai_config
from services.ai import scheduled_runner as runner
from services.ai.scheduled_runner import ScheduledError
from util.object_id import get_current_user

router = APIRouter()

# 任务 oid 是 24 位 hex（业务 oid 空间）。形状不对直接当「不存在」，不查库。
OID_PATTERN = re.compile(r"^[0-9a-f]{24}$")

DECISIONS = ("approved", "rejected")
SCHEDULE_KINDS = ("interval", "cron")
REVIEW_STATUSES = ("draft", "pending", "approved", "rejected")


# ---------------------------------------------------------------------------
# 入参
# ---------------------------------------------------------------------------

class TaskCreate(BaseModel):
    """建任务。审核状态不由客户端决定（创建即 pending）。"""

    name: str = Field(min_length=1, max_length=128)
    prompt_text: str = Field(min_length=1, max_length=20000)
    schedule_kind: str = Field(default="interval", max_length=8)
    interval_minutes: int = Field(default=runner.DEFAULT_INTERVAL_MINUTES)
    cron_expr: str | None = Field(default=None, max_length=64)
    model: str | None = Field(default=None, max_length=64)


class TaskUpdate(BaseModel):
    """部分更新：只改传了的字段（``exclude_unset``）。

    改 ``prompt_text`` 会把 ``review_status`` 打回 ``draft``（审核结论已失效）；
    改排程会重算 ``next_run_at``。
    """

    name: str | None = Field(default=None, min_length=1, max_length=128)
    prompt_text: str | None = Field(default=None, min_length=1, max_length=20000)
    schedule_kind: str | None = Field(default=None, max_length=8)
    interval_minutes: int | None = None
    cron_expr: str | None = Field(default=None, max_length=64)
    model: str | None = Field(default=None, max_length=64)
    enabled: bool | None = None


class ReviewRequest(BaseModel):
    """审核结论。``note`` 只在驳回时真正有用，但通过时留一句也允许。"""

    decision: str = Field(max_length=16)
    note: str | None = Field(default=None, max_length=512)


# ---------------------------------------------------------------------------
# 协议外壳
# ---------------------------------------------------------------------------

def _is_admin(user: Mapping[str, Any]) -> bool:
    return "admin" in (user.get("per") or [])


def _roles(user: Mapping[str, Any]) -> list[str]:
    return list(user.get("per") or [])


def _http(exc: ScheduledError) -> HTTPException:
    """服务层错误码 → HTTP；``detail`` 用 code（客户端按 code 分支）。"""
    return HTTPException(status_code=exc.status_code, detail=exc.code)


def _oid(value: str) -> str:
    text = str(value or "").strip().lower()
    if not OID_PATTERN.match(text):
        raise HTTPException(status_code=404, detail="AI_SCHEDULE_NOT_FOUND")
    return text


def _iso(value: object) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else None


def _view(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "oid": row.get("_id"),
        "owner_oid": row.get("owner_oid"),
        "name": row.get("name"),
        "prompt_text": row.get("prompt_text"),
        "review_status": row.get("review_status"),
        "reviewed_by_oid": row.get("reviewed_by_oid"),
        "reviewed_at": _iso(row.get("reviewed_at")),
        "review_note": row.get("review_note"),
        "schedule_kind": row.get("schedule_kind"),
        "interval_minutes": row.get("interval_minutes"),
        "cron_expr": row.get("cron_expr"),
        "enabled": bool(row.get("enabled")),
        "model": row.get("model"),
        "last_run_at": _iso(row.get("last_run_at")),
        "next_run_at": _iso(row.get("next_run_at")),
        "run_count": row.get("run_count") or 0,
        "last_status": row.get("last_status"),
        "last_error": row.get("last_error"),
        "created_at": _iso(row.get("created_at")),
        "updated_at": _iso(row.get("updated_at")),
    }


def _run_view(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "run_id": row.get("_id"),
        "status": row.get("status"),
        "model": row.get("model"),
        "stop_reason": row.get("stop_reason"),
        "error_code": row.get("error_code"),
        "error_message": row.get("error_message"),
        "created_at": _iso(row.get("created_at")),
        "started_at": _iso(row.get("started_at")),
        "finished_at": _iso(row.get("finished_at")),
    }


async def _require_task(repo: Any, oid: str) -> dict[str, Any]:
    row = await repo.get(oid)
    if row is None:
        raise HTTPException(status_code=404, detail="AI_SCHEDULE_NOT_FOUND")
    return row


def _require_owner_or_admin(user: Mapping[str, Any], task: Mapping[str, Any]) -> None:
    """404 与 403 的区别：oid 是 24 位 hex、行真的存在，所以回 403 不泄露任何东西。"""
    if _is_admin(user):
        return
    if str(task.get("owner_oid")) != str(user.get("id")):
        raise HTTPException(status_code=403, detail="AI_SCHEDULE_FORBIDDEN")


def _require_creator(user: Mapping[str, Any]) -> None:
    roles = _roles(user)
    if "admin" in roles or "department" in roles:
        return
    raise HTTPException(status_code=403, detail="AI_SCHEDULE_FORBIDDEN")


def _schedule_values(
    *, schedule_kind: str, interval_minutes: int, cron_expr: str | None
) -> dict[str, Any]:
    """校验排程三元组并返回可写库的字段（非法值在这里就 422）。

    ``interval`` 时 ``cron_expr`` 保留原值（切换排程方式不丢原文）；``cron`` 时
    ``interval_minutes`` 同样保留，只由 ``schedule_kind`` 决定读哪个。
    """
    kind = str(schedule_kind or "").strip().lower()
    if kind not in SCHEDULE_KINDS:
        raise HTTPException(status_code=422, detail="AI_SCHEDULE_BAD_KIND")
    try:
        runner.validate_interval(interval_minutes)
    except ScheduledError as exc:
        raise _http(exc) from None
    if kind == "cron":
        try:
            runner.validate_cron(cron_expr)
        except ScheduledError as exc:
            raise _http(exc) from None
    return {
        "schedule_kind": kind,
        "interval_minutes": interval_minutes,
        "cron_expr": (str(cron_expr).strip() if cron_expr else None),
    }


# ---------------------------------------------------------------------------
# 端点
# ---------------------------------------------------------------------------

@router.get("")
async def list_tasks(
    owner_oid: str | None = Query(None, description="admin only: another user's tasks"),
    all_tasks: bool = Query(False, description="admin only: every user's tasks"),
    review_status: str | None = Query(
        None, description="admin only: draft/pending/approved/rejected"
    ),
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0),
    user=Depends(get_current_user),
):
    """本人的任务；admin 可带 ``?owner_oid=`` 看别人的，或用 ``?all_tasks=1``
    看审核队列（可再叠 ``?review_status=pending``）。"""
    repo = runner.repo()

    wants_admin_view = all_tasks or review_status is not None
    if wants_admin_view and not _is_admin(user):
        raise HTTPException(status_code=403, detail="AI_SCHEDULE_FORBIDDEN")

    target: str | None = None
    if owner_oid is not None:
        target = str(owner_oid).strip().lower()
        if not OID_PATTERN.match(target):
            raise HTTPException(status_code=400, detail="AI_SCHEDULE_BAD_OID")
        if target != str(user.get("id")) and not _is_admin(user):
            raise HTTPException(status_code=403, detail="AI_SCHEDULE_FORBIDDEN")

    if review_status is not None:
        status = str(review_status).strip().lower()
        if status not in REVIEW_STATUSES:
            raise HTTPException(status_code=422, detail="AI_SCHEDULE_BAD_STATUS")
    else:
        status = None

    if wants_admin_view:
        rows = await repo.list_all(
            owner_oid=target, review_status=status, limit=limit, offset=offset
        )
    elif target is None:
        rows = await repo.list_for_owner(str(user["id"]))
    else:
        rows = await repo.list_for_owner(target)

    data = [_view(row) for row in rows]
    return {"status": "ok", "code": 200, "data": data, "metadata": {"size": len(data)}}


@router.post("", status_code=201)
async def create_task(payload: TaskCreate, user=Depends(get_current_user)):
    """建任务（``review_status='pending'``，等 admin 审核后才可能执行）。"""
    _require_creator(user)
    repo = runner.repo()

    if not _is_admin(user):
        used = await repo.count_active_for_owner(str(user["id"]))
        limit = ai_config.schedule_quota_department()
        if used >= limit:
            raise HTTPException(status_code=409, detail="AI_SCHEDULE_QUOTA_EXCEEDED")

    schedule = _schedule_values(
        schedule_kind=payload.schedule_kind,
        interval_minutes=payload.interval_minutes,
        cron_expr=payload.cron_expr,
    )
    now = datetime.now()
    next_at = runner.first_run_at(
        schedule_kind=schedule["schedule_kind"],
        interval_minutes=schedule["interval_minutes"],
        cron_expr=schedule["cron_expr"],
        now=now,
    )
    row = await repo.create(
        owner_oid=str(user["id"]),
        name=payload.name.strip(),
        prompt_text=payload.prompt_text,
        schedule_kind=schedule["schedule_kind"],
        interval_minutes=schedule["interval_minutes"],
        cron_expr=schedule["cron_expr"],
        next_run_at=next_at,
        run_at=now,
        review_status="pending",
        model=(payload.model.strip() or None) if payload.model else None,
    )
    return {"status": "ok", "code": 201, "data": _view(row)}


@router.patch("/{oid}")
async def update_task(oid: str, payload: TaskUpdate, user=Depends(get_current_user)):
    """owner 或 admin 的部分更新。

    改 prompt → 打回 ``draft`` 并清空上一位审核人的结论（留着的「已通过」是
    对旧文案的判断，比没有结论更危险）；改排程 → 从此刻重算 ``next_run_at``。
    """
    repo = runner.repo()
    task = await _require_task(repo, _oid(oid))
    _require_owner_or_admin(user, task)

    values: dict[str, Any] = {}
    fields = payload.model_dump(exclude_unset=True)

    if "name" in fields and fields["name"] is not None:
        values["name"] = str(fields["name"]).strip()
    if "prompt_text" in fields and fields["prompt_text"] is not None:
        values["prompt_text"] = fields["prompt_text"]
        values["review_status"] = "draft"
        values["reviewed_by_oid"] = None
        values["reviewed_at"] = None
        values["review_note"] = None
    if "model" in fields:
        raw = fields["model"]
        values["model"] = (str(raw).strip() or None) if raw else None

    schedule_changed = any(
        key in fields for key in ("schedule_kind", "interval_minutes", "cron_expr")
    )
    if schedule_changed:
        schedule = _schedule_values(
            schedule_kind=fields.get("schedule_kind", task.get("schedule_kind")),
            interval_minutes=fields.get(
                "interval_minutes", task.get("interval_minutes")
            ),
            cron_expr=(
                fields["cron_expr"]
                if "cron_expr" in fields
                else task.get("cron_expr")
            ),
        )
        values.update(schedule)

    enabled_changed = "enabled" in fields and fields["enabled"] is not None
    if enabled_changed:
        values["enabled"] = 1 if fields["enabled"] else 0

    if schedule_changed or (enabled_changed and fields["enabled"]):
        values["next_run_at"] = runner.first_run_at(
            schedule_kind=values.get("schedule_kind") or task.get("schedule_kind"),
            interval_minutes=values.get(
                "interval_minutes", task.get("interval_minutes")
            ),
            cron_expr=(
                values["cron_expr"] if "cron_expr" in values else task.get("cron_expr")
            ),
        )

    if not values:
        return {"status": "ok", "code": 200, "data": _view(task)}

    await repo.update(task["_id"], values)
    updated = await repo.get(task["_id"])
    if updated is None:  # 并发删除
        raise HTTPException(status_code=404, detail="AI_SCHEDULE_NOT_FOUND")
    return {"status": "ok", "code": 200, "data": _view(updated)}


@router.delete("/{oid}", status_code=204)
async def delete_task(oid: str, user=Depends(get_current_user)):
    """owner 或 admin。已产生的 run 历史留在 ``ai_runs``（按 task_oid 仍可查）。"""
    repo = runner.repo()
    task = await _require_task(repo, _oid(oid))
    _require_owner_or_admin(user, task)
    await repo.delete(task["_id"])
    return None


@router.post("/{oid}/review")
async def review_task(oid: str, payload: ReviewRequest, user=Depends(get_current_user)):
    """审核（仅 admin）。通过/驳回都重算 ``next_run_at``。

    驳回后 ``next_run_at`` 仍然推进：任务留在表里让用户改文案，调度器由
    ``review_status`` 拦住；把它置空会让「驳回」变成另一种「停用」。
    """
    if not _is_admin(user):
        raise HTTPException(status_code=403, detail="AI_SCHEDULE_FORBIDDEN")

    decision = str(payload.decision or "").strip().lower()
    if decision not in DECISIONS:
        raise HTTPException(status_code=422, detail="AI_SCHEDULE_BAD_DECISION")

    repo = runner.repo()
    task = await _require_task(repo, _oid(oid))
    now = datetime.now()
    values = {
        "review_status": decision,
        "reviewed_by_oid": str(user["id"]),
        "reviewed_at": now,
        "review_note": (payload.note.strip() or None) if payload.note else None,
        "next_run_at": runner.first_run_at(
            schedule_kind=task.get("schedule_kind"),
            interval_minutes=task.get("interval_minutes"),
            cron_expr=task.get("cron_expr"),
            now=now,
        ),
    }
    await repo.update(task["_id"], values)
    updated = await repo.get(task["_id"])
    if updated is None:
        raise HTTPException(status_code=404, detail="AI_SCHEDULE_NOT_FOUND")
    return {"status": "ok", "code": 200, "data": _view(updated)}


@router.post("/{oid}/run-now", status_code=202)
async def run_task_now(oid: str, user=Depends(get_current_user)):
    """立即执行一次（owner 或 admin，且必须已审核通过）。"""
    repo = runner.repo()
    task = await _require_task(repo, _oid(oid))
    _require_owner_or_admin(user, task)
    try:
        run_id = await runner.run_now(task)
    except ScheduledError as exc:
        raise _http(exc) from None
    # 立即刷新 next_run_at 的语义只属于「计划执行」：手动触发不动排程，所以这里
    # 只记录一次队列入账，不调 mark_result。
    await repo.mark_result(
        task["_id"],
        last_status=runner.STATUS_QUEUED,
        next_run_at=(
            task.get("next_run_at")
            or runner.first_run_at(
                schedule_kind=task.get("schedule_kind"),
                interval_minutes=task.get("interval_minutes"),
                cron_expr=task.get("cron_expr"),
            )
        ),
        counted=True,
    )
    return {"status": "ok", "code": 202, "data": {"run_id": run_id, "status": "queued"}}


@router.get("/{oid}/runs")
async def list_task_runs(
    oid: str,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    user=Depends(get_current_user),
):
    """该任务的执行历史（``ai_runs.task_oid``），新的在前。"""
    repo = runner.repo()
    task = await _require_task(repo, _oid(oid))
    _require_owner_or_admin(user, task)
    rows, total = await repo.list_runs(task["_id"], page=page, size=size)
    data = [_run_view(row) for row in rows]
    return {
        "status": "ok",
        "code": 200,
        "data": data,
        "metadata": {"size": total, "page": page, "perpage": size},
    }
