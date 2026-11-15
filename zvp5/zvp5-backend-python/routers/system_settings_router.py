"""在线设置中心（``/api/system/settings``、``/api/system/user-quotas``）。

为什么单独一个 router：``system_router`` 只负责维护模式（挂载在
``/api/system/maintenance`` 下），设置中心是两个独立资源段，混进去会让前缀与
权限语义都不清晰。两者共用同一套守卫 :func:`routers.system_router._require_system_admin`
（严格 ``admin`` + 实时查库复核，不信 token 快照），未来双人授权的挂点也只有
那一处。唯一例外是用户配额的两个端点：``quota.manage`` 开关打开时非 admin 也能
操作**本班**用户的配额，见 :func:`_require_quota_manager`。

写操作全部走 ``inject_log`` 留痕（谁、改了哪个键、从什么改成什么）：
设置中心能改 AI 频控/额度与上传配额，属于「事后必须能回答是谁改的」的操作。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from db import backends
from db.ids import InvalidOid, normalize_oid
from routers.system_router import _require_system_admin
from services import settings_runtime
from typings.log import inject_log
from util.object_id import get_current_user, require_password_verification
from util.permission.features import has_feature
from util.user import get_user_display_name

router = APIRouter()

# 用户级覆盖的数值边界与全局设置一致（见 services.settings_runtime 的注册表）。
_QUOTA_FILES_MAX = 100_000
_QUOTA_BYTES_MAX = 100 * 1024 * 1024 * 1024


class SettingItem(BaseModel):
    key: str = Field(min_length=1, max_length=64)
    value: Any


class SettingsUpdateRequest(BaseModel):
    items: list[SettingItem]
    verification_token: str | None = None


class UserQuotaUpdateRequest(BaseModel):
    daily_max_files: int | None = Field(default=None, ge=0, le=_QUOTA_FILES_MAX)
    daily_max_bytes: int | None = Field(default=None, ge=0, le=_QUOTA_BYTES_MAX)


def _brief(value: Any, limit: int = 200) -> str:
    """日志里的值摘要：对象可能很大，截断后仍能看出「改成了什么」。"""
    text = repr(value)
    return text if len(text) <= limit else text[:limit] + "…"


def _bad_request(key: str, reason: str) -> HTTPException:
    return HTTPException(status_code=400, detail=f"{key}: {reason}")


def _valid_oid(oid: str) -> str:
    """把路径里的 oid 规范成小写 24 位 hex；非法一律 400（不是 500）。"""
    try:
        return normalize_oid(oid)
    except InvalidOid:
        raise HTTPException(status_code=400, detail="无效的用户 ID")


def _same_class(actor: dict | None, target: dict | None) -> bool:
    """同班判定：任一方班级为空都算「不同班」（fail-closed）。"""
    actor_class = (actor or {}).get("class_id")
    target_class = (target or {}).get("class_id")
    return (
        bool(actor_class)
        and bool(target_class)
        and str(actor_class) == str(target_class)
    )


async def _require_quota_manager(user: dict, target_oid: str) -> None:
    """用户配额两个端点的守卫（``quota.manage`` 下放后的口径）。

    * ``admin``：仍走 :func:`_require_system_admin`（实时查库复核，不信 token
      快照，未来双人授权也只挂在那里）；
    * 其余角色：开关打开后只能管**本班**用户。配额是资源分配，跨班调额度等于
      跨班限流；目标用户不存在或没分班时取不到 ``class_id``，一律拒绝——不能
      因为「查不到」就放行。
    """
    if "admin" in (user.get("per") or []):
        await _require_system_admin(user)
        return
    if not has_feature(user, "quota.manage"):
        raise HTTPException(status_code=403, detail="权限不足")
    users = backends.get_users_repo()
    target = await users.get_by_oid(target_oid)
    if target is not None and "admin" in (target.get("roles") or []):
        # 同班也不许：给管理员设一个极小配额，就是一次针对管理员的定向限流。
        raise HTTPException(status_code=403, detail="权限不足")
    if not _same_class(await users.get_by_oid(user["id"]), target):
        raise HTTPException(status_code=403, detail="权限不足")


@router.get("/settings")
async def read_settings(user=Depends(get_current_user)):
    """全部可调项 + 各自默认值。

    ``force=True``：管理员打开设置页看到的必须是库里的当前值，不能是别的 worker
    5 秒前的快照。
    """
    await _require_system_admin(user)
    return {"status": "ok", "data": await settings_runtime.all_settings(force=True)}


@router.put("/settings")
async def update_settings(
    payload: SettingsUpdateRequest,
    user=Depends(get_current_user),
    log=Depends(inject_log),
):
    """批量修改：先全量校验，全部通过才落库；返回**本次改的**键。"""
    await _require_system_admin(user)
    await require_password_verification(payload.verification_token, user)

    if not payload.items:
        raise HTTPException(status_code=400, detail="items 不能为空")

    updates: dict[str, Any] = {}
    for item in payload.items:
        if item.key in updates:
            raise _bad_request(item.key, "同一请求里重复出现")
        updates[item.key] = item.value

    # 只有注册表里的键才会被 get_sync 读到；未知键交给 set_many 抛校验异常。
    before = {
        key: (
            settings_runtime.get_sync(key)
            if key in settings_runtime.registry()
            else None
        )
        for key in updates
    }

    try:
        changed = await settings_runtime.set_many(updates, user["id"])
    except settings_runtime.SettingsValidationError as exc:
        raise _bad_request(exc.key, exc.reason)

    detail = "；".join(
        f"{key}: {_brief(before.get(key))} -> {_brief(updates[key])}"
        for key in updates
    )
    log.with_text(
        f"管理员 {await get_user_display_name(user['id'])} 修改系统设置：{detail}",
        type="system_settings_update",
        level="warning",
    )
    await log.insert_log()

    return {"status": "ok", "data": {"items": changed}}


@router.get("/user-quotas/{oid}")
async def read_user_quota(oid: str, user=Depends(get_current_user)):
    """某用户的配额覆盖与生效值（没有覆盖时 ``daily_max_*`` 为 null）。"""
    target = _valid_oid(oid)
    await _require_quota_manager(user, target)
    # 用户不存在也不拒绝（对 admin）：设置页可以先设上限，账号随后才建；查库
    # 只为给前端一个明确的 404 语义时才有必要，这里保持「配额是用户的附属
    # 数据」的宽松口径。非 admin 的同班判定则必须能查到目标，见守卫。
    return {"status": "ok", "data": await settings_runtime.get_user_quota(target)}


@router.put("/user-quotas/{oid}")
async def update_user_quota(
    oid: str,
    payload: UserQuotaUpdateRequest,
    user=Depends(get_current_user),
    log=Depends(inject_log),
):
    """写用户级覆盖；两列都传 null = 清除覆盖、继承全局设置。"""
    target = _valid_oid(oid)
    await _require_quota_manager(user, target)

    # 非 admin 下放额度管理后的上限钳制：否则同班可以把同学的额度抬到模型上限
    # （十万文件 / 百 GiB），比平台默认高两个数量级，等于一次资源滥用。
    if "admin" not in (user.get("per") or []):
        cap_files = int(settings_runtime.get_sync("ops.upload_daily_max_files"))
        cap_bytes = int(settings_runtime.get_sync("ops.upload_daily_max_bytes"))
        if payload.daily_max_files is not None and int(payload.daily_max_files) > cap_files:
            raise HTTPException(
                status_code=400, detail=f"每日文件数不得超过平台默认上限 {cap_files}"
            )
        if payload.daily_max_bytes is not None and int(payload.daily_max_bytes) > cap_bytes:
            raise HTTPException(
                status_code=400, detail=f"每日字节数不得超过平台默认上限 {cap_bytes}"
            )

    # 先确认目标账号存在：给一个不存在的 oid 配额，事后无法从界面上发现写错了。
    if await backends.get_users_repo().get_by_oid(target) is None:
        raise HTTPException(status_code=404, detail="用户不存在")

    before = settings_runtime.user_quota_override(target) or {}
    data = await settings_runtime.set_user_quota(
        target,
        payload.daily_max_files,
        payload.daily_max_bytes,
        user["id"],
    )

    detail = (
        f"每日文件数 {_brief(before.get('daily_max_files'))} -> "
        f"{_brief(payload.daily_max_files)}，"
        f"每日字节数 {_brief(before.get('daily_max_bytes'))} -> "
        f"{_brief(payload.daily_max_bytes)}"
    )
    log.with_text(
        f"管理员 {await get_user_display_name(user['id'])} 修改用户 {target} 的上传配额：{detail}",
        type="system_user_quota_update",
        level="warning",
    )
    await log.insert_log()

    return {"status": "ok", "data": data}


__all__ = ["router"]
