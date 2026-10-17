"""维护模式（maintenance mode）管理端点。

前缀 ``/api/system/maintenance``（main.py 挂载；每个子系统一个资源段，
后续 ``/api/system/otp`` 同形）。维护中间件放行整个 ``/api/system`` 前缀，
所以维护模式开启期间管理员仍能开关维护模式。

权限：开关仅需**单个 admin**（严格 ``admin``，不含 department）。未来双人
授权的唯一挂点见 ``_require_system_admin``。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from db import backends
from db.system_state import get_state, set_state
from realtime import disconnect_all_clients
from typings.log import inject_log
from util.group import get_user_permissions
from util.object_id import get_current_user
from util.user import get_user_display_name

router = APIRouter()


class EnableRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=200)
    announcement: str | None = Field(default=None, max_length=300)


class DisableRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=200)


async def _require_system_admin(user: dict) -> dict:
    """严格 admin + 即时查库复核（不信 token 里的 per 快照）。"""
    if "admin" not in (user.get("per") or []):
        raise HTTPException(status_code=403, detail="权限不足")

    me = await backends.get_users_repo().get_by_oid(user["id"])
    if me is None:
        raise HTTPException(status_code=403, detail="权限不足")
    if me.get("banned"):
        raise HTTPException(status_code=403, detail="用户已被封禁")
    if "admin" not in (await get_user_permissions(me)):
        raise HTTPException(status_code=403, detail="权限不足")

    # 未来双人授权校验（grant）插入此层。
    return me


def _public_state() -> dict:
    """对外只暴露公开字段：reason / updated_by 属内部信息，不外泄。"""
    state = get_state()
    return {
        "enabled": state["enabled"],
        "since": state["since"],
        "announcement": state["announcement"],
    }


@router.get("")
async def read_maintenance_state():
    """公开端点：无需登录，维护模式开启期间仍可用。"""
    return {"status": "ok", "code": 200, "data": _public_state()}


@router.post("/enable")
async def enable_maintenance(
    payload: EnableRequest,
    user=Depends(get_current_user),
    log=Depends(inject_log),
):
    await _require_system_admin(user)

    state = set_state(True, payload.reason, payload.announcement, user["id"])

    # 状态已落库 ⇒ 新连接由 realtime.MaintenanceSocketGuard 在握手阶段挡掉；
    # 已建立的连接必须在这里立刻断开（前端不会自己掉线）。断开失败不能影响
    # enable 的返回，所以逐个 try/except 由 disconnect_all_clients 内部兜住。
    disconnected = await disconnect_all_clients()
    print(f"Maintenance enabled: disconnected {disconnected} socket.io client(s)")

    log.with_text(
        f"管理员 {await get_user_display_name(user['id'])} 开启了系统维护模式：{payload.reason}",
        type="system_maintenance_enable",
        level="warning",
    )
    await log.insert_log()

    data = _public_state()
    data["reason"] = state["reason"]
    return {"status": "ok", "code": 200, "data": data}


@router.post("/disable")
async def disable_maintenance(
    payload: DisableRequest | None = None,
    user=Depends(get_current_user),
    log=Depends(inject_log),
):
    await _require_system_admin(user)

    reason = payload.reason if payload is not None else None
    state = set_state(False, reason, None, user["id"])

    log.with_text(
        f"管理员 {await get_user_display_name(user['id'])} 关闭了系统维护模式",
        type="system_maintenance_disable",
        level="warning",
    )
    await log.insert_log()

    data = _public_state()
    data["reason"] = state["reason"]
    return {"status": "ok", "code": 200, "data": data}
