"""AI 配额申请端点（``/api/ai/quota``）。

三个视角，权限口径各不相同：

* **提交 / 查自己的**：任意登录人。学生会在额度用尽时自己申请，不需要额外能力。
* **审批队列 / 通过 / 拒绝**：``admin`` 或具备 ``quota.manage`` 的角色（默认只有
  admin，学生会由管理员在「角色策略」页下放）。判据与
  ``routers/ai_router.py`` 的 ``ensure_feature(user, "quota.manage")`` 同源
  （``util.permission.features``），只是这里把 admin 显式短路，拒绝体统一成
  ``403 Permission denied``，前端不必区分两套文案。
* **用户额度表格**：**仅 admin**。整页用户的额度分布属于管理视野，``quota.manage``
  只授权「审批」，不授权「看全站用量」。

**注册顺序有约束**：本 router 必须排在 ``ai_router`` 之前 include。``ai_router``
有 ``GET /quota/{user_oid}``，若它先注册，``/api/ai/quota/users`` 与
``/api/ai/quota/requests`` 会被当成 ``user_oid="users"`` / ``"requests"`` 匹配过去，
回一个 400 Invalid Object ID。见 ``main.py`` 的注册处。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from db import backends
from services.ai import quota as quota_service
from services.ai import quota_requests as requests_service
from util.object_id import get_current_user, validate_object_id
from util.permission.features import has_feature

router = APIRouter()

#: 审批权的能力 key。与 ``services.roles.policy.FEATURE_KEYS`` 一致。
APPROVER_FEATURE = "quota.manage"


class QuotaRequestCreate(BaseModel):
    """提交申请。字段不做枚举/区间约束：非法值由服务层判成稳定的 400 + 错误码，
    而不是 pydantic 的 422 报文（客户端按 ``code`` 分支，不解析 detail 数组）。
    """

    kind: str
    reason: str | None = None
    requested_limit: int | None = None


class QuotaApproveRequest(BaseModel):
    """通过申请：默认重置已用量，可选顺手把上限改成 ``limit``。"""

    reset: bool = True
    limit: int | None = None


class QuotaRejectRequest(BaseModel):
    note: str | None = None


# ---------------------------------------------------------------------------
# guards
# ---------------------------------------------------------------------------


def _is_admin(user: dict) -> bool:
    """与 ``routers/ai_router._require_admin`` 同口径：token 里的原始角色。"""
    return "admin" in (user.get("per") or [])


def _require_approver(user: dict) -> None:
    """审批人：admin 短路，或角色策略表授予了 ``quota.manage``。"""
    if _is_admin(user) or has_feature(user, APPROVER_FEATURE):
        return
    raise HTTPException(status_code=403, detail="Permission denied")


def _require_admin(user: dict) -> None:
    if not _is_admin(user):
        raise HTTPException(status_code=403, detail="Permission denied")


def _request_error(exc: requests_service.QuotaRequestError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.code)


def _ok(data: Any, code: int = 200) -> dict[str, Any]:
    return {"status": "ok", "code": code, "data": data}


# ---------------------------------------------------------------------------
# 自助
# ---------------------------------------------------------------------------


@router.post("/requests", status_code=201)
async def submit_quota_request(
    payload: QuotaRequestCreate,
    user=Depends(get_current_user),
):
    """提交一条配额申请；本月该能力已有 pending → 409。"""
    try:
        row = await requests_service.submit(
            user["id"],
            payload.kind,
            payload.reason,
            payload.requested_limit,
        )
    except requests_service.QuotaRequestError as exc:
        raise _request_error(exc) from None
    return _ok(row, 201)


@router.get("/requests/mine")
async def list_my_quota_requests(user=Depends(get_current_user)):
    """本人最近 10 条申请（新的在前）。"""
    return _ok(await requests_service.list_mine(user["id"]))


# ---------------------------------------------------------------------------
# 审批
# ---------------------------------------------------------------------------


@router.get("/requests")
async def list_quota_requests(
    status: str = Query("pending"),
    page: int = Query(1),
    size: int = Query(20),
    user=Depends(get_current_user),
):
    """审批队列：按状态过滤、按创建时间倒序。``admin`` 或 ``quota.manage``。"""
    _require_approver(user)
    try:
        data = await requests_service.list_for_approver(status, page, size)
    except requests_service.QuotaRequestError as exc:
        raise _request_error(exc) from None
    return _ok(data)


@router.post("/requests/{oid}/approve")
async def approve_quota_request(
    oid: str,
    payload: QuotaApproveRequest,
    user=Depends(get_current_user),
):
    """通过申请：重置已用量（可选改上限），返回申请行 + 该用户当前额度快照。"""
    _require_approver(user)
    target = validate_object_id(oid)
    try:
        row = await requests_service.decide(
            target,
            user["id"],
            True,
            reset=payload.reset,
            limit=payload.limit,
        )
    except requests_service.QuotaRequestError as exc:
        raise _request_error(exc) from None

    # 审批完立刻回一份快照：前端表格要就地更新，不该再发一次 GET。
    target_roles = await _target_roles(str(row["user_oid"]))
    return _ok(
        {
            "request": row,
            "quota": await quota_service.snapshot(
                str(row["user_oid"]), roles=target_roles
            ),
        }
    )


@router.post("/requests/{oid}/reject")
async def reject_quota_request(
    oid: str,
    payload: QuotaRejectRequest,
    user=Depends(get_current_user),
):
    """拒绝申请：只置状态与备注，**不动**额度。"""
    _require_approver(user)
    target = validate_object_id(oid)
    try:
        row = await requests_service.decide(
            target, user["id"], False, note=payload.note
        )
    except requests_service.QuotaRequestError as exc:
        raise _request_error(exc) from None
    return _ok(row)


# ---------------------------------------------------------------------------
# 管理表格
# ---------------------------------------------------------------------------


@router.get("/users")
async def list_quota_users(
    query: str = Query(""),
    page: int = Query(1),
    size: int = Query(20),
    user=Depends(get_current_user),
):
    """整页用户 + 当月 chat/ocr 额度 + 是否有待审申请（**仅 admin**）。"""
    _require_admin(user)
    return _ok(await requests_service.list_users_with_quota(query, page, size))


async def _target_roles(user_oid: str) -> list[str] | None:
    """目标用户的原始角色（``per`` 口径），用于展示正确的默认额度。

    查不到用户回 ``None``：``quota.snapshot`` 会按全局默认展示并标
    ``global_default``——不编造角色，也不把管理员的角色套到别人身上。
    """
    row = await backends.get_users_repo().get_for_auth(user_oid)
    if not row:
        return None
    roles = row.get("roles")
    return roles if isinstance(roles, list) else None


__all__ = ["router"]
