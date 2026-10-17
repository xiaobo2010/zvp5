from typing import Literal, Optional

from bcrypt import hashpw, gensalt
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from db import backends
from typings.log import inject_log
from util.cert import generate_initial_password, get_current_totp
from util.group import get_user_permissions
from util.object_id import compulsory_temporary_token, get_current_user
from util.permission.user import credential_change_forbidden
from util.user import get_user_display_name
from utils import validate_object_id
from util.validation import validate_number, validate_student_name

router = APIRouter()


async def _get_zvms_new_user(oid: str) -> Optional[dict]:
    validate_object_id(oid)
    return await backends.get_users_repo().get_by_oid(oid)


@router.get("/{user_id}")
async def get_user_v3(
    user_id: str,
    user=Depends(get_current_user),
):
    """
    Read a user's live profile from `zvms_new.users`.

    Permission: any authenticated user.
    """
    validate_object_id(user_id)

    target = await _get_zvms_new_user(user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="用户不存在")

    class_id = str(target["class_id"]) if target.get("class_id") else ""
    profile = {
        "_id": str(target["_id"]),
        "id": target["id"],
        "name": target["name"],
        "roles": target.get("roles", []),
        "class_id": class_id,
        "group": [class_id] if class_id else [],
        "banned": bool(target.get("banned")),
    }

    return {"status": "ok", "code": 200, "data": profile}


AllowedRole = Literal["student", "secretary", "department", "admin", "teacher"]


class PutRolesRequest(BaseModel):
    roles: list[AllowedRole]


class PutBannedRequest(BaseModel):
    banned: bool


@router.put("/{user_id}/roles", status_code=204)
async def modify_user_roles_v3(
    user_id: str,
    payload: PutRolesRequest,
    user=Depends(get_current_user),
    log=Depends(inject_log),
):
    per = user.get("per") or []
    if "admin" not in per:
        raise HTTPException(status_code=403, detail="权限不足")

    oid = validate_object_id(user_id)
    repo = backends.get_users_repo()

    target = await repo.get_by_oid(str(oid))
    if target is None:
        raise HTTPException(status_code=404, detail="用户不存在")

    await repo.set_roles(str(oid), list(payload.roles))

    log.with_text(
        f"管理员 {await get_user_display_name(user['id'])} 将用户 {target['name']} 的角色修改为 {list(payload.roles)}",
        type="role_change",
    )
    await log.insert_log()

    return


@router.put("/{user_id}/banned", status_code=204)
async def set_user_banned_v3(
    user_id: str,
    payload: PutBannedRequest,
    user=Depends(get_current_user),
    log=Depends(inject_log),
):
    per = user.get("per") or []
    if "admin" not in per:
        raise HTTPException(status_code=403, detail="权限不足")

    oid = validate_object_id(user_id)
    repo = backends.get_users_repo()

    target = await repo.get_by_oid(str(oid))
    if target is None:
        raise HTTPException(status_code=404, detail="用户不存在")

    await repo.set_banned(str(oid), 1 if payload.banned else 0)

    log.with_text(
        f"管理员 {await get_user_display_name(user['id'])} {'封禁' if payload.banned else '解封'}了用户 {target['name']}",
        type="ban_change",
        level="warning",
    )
    await log.insert_log()

    return


class NewUserRequest(BaseModel):
    id: str
    name: str
    roles: list[str] = Field(default_factory=lambda: ["student"])
    class_id: Optional[str] = None


@router.post("", status_code=201)
async def create_user_v3(
    payload: NewUserRequest,
    response: Response,
    user=Depends(get_current_user),
    log=Depends(inject_log),
):
    per = user.get("per") or []
    if "admin" not in per:
        raise HTTPException(status_code=403, detail="权限不足")

    allowed_roles = {"student", "secretary", "department", "admin", "teacher"}
    for role in payload.roles:
        if role not in allowed_roles:
            raise HTTPException(status_code=400, detail=f"无效角色：{role}")

    valid, message = validate_number(payload.id)
    if not valid:
        raise HTTPException(status_code=400, detail=message)

    valid, message = validate_student_name(payload.name)
    if not valid:
        raise HTTPException(status_code=400, detail=message)

    repo = backends.get_users_repo()
    if await repo.find_by_business_id(payload.id):
        raise HTTPException(status_code=409, detail="学号已存在")

    class_oid = None
    if payload.class_id:
        class_oid = validate_object_id(payload.class_id)
        groups_repo = backends.get_groups_repo()
        if (
            await groups_repo.get_permission_group(str(class_oid)) is not None
            or await groups_repo.get(str(class_oid)) is None
        ):
            raise HTTPException(status_code=404, detail="班级不存在")

    initial_password = generate_initial_password()
    password = hashpw(initial_password.encode("utf-8"), gensalt()).decode("utf-8")

    new_id = await repo.create(
        payload.id,
        payload.name,
        list(payload.roles),
        password,
        str(class_oid) if class_oid is not None else None,
    )

    log.with_text(
        f"管理员 {await get_user_display_name(user['id'])} 创建了用户 {payload.name}（学号 {payload.id}），已生成一次性初始密码",
        type="user_create",
    )
    await log.insert_log()

    response.headers["Cache-Control"] = "no-store"
    return {"id": new_id, "initial_password": initial_password}


async def _is_same_class(a_id: str, b_id: str) -> bool:
    current = await _get_zvms_new_user(a_id)
    target = await _get_zvms_new_user(b_id)
    return (
        current is not None
        and target is not None
        and current.get("class_id") is not None
        and str(current.get("class_id")) == str(target.get("class_id"))
    )


@router.delete("/{user_id}/password")
async def reset_user_password_v3(
    user_id: str,
    response: Response,
    user=Depends(compulsory_temporary_token),
    log=Depends(inject_log),
):
    """
    为用户生成一个新的**一次性初始密码**（重置凭据）。

    Permission: admin/department, or secretary in the same class.
    Requires a temporary token (re-verification), mirroring zvp's
    `get_authz_user` posture for credential-affecting operations.

    另外：目标账号是 admin 级时，只有本人能重置其凭据（见
    ``credential_change_forbidden``）。明文只在本次响应里回一次，日志里
    绝不出现。
    """
    per = user.get("per") or []
    target = await _get_zvms_new_user(user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="用户不存在")

    if "admin" not in per and "department" not in per:
        if "secretary" not in per or not await _is_same_class(user["id"], user_id):
            raise HTTPException(status_code=403, detail="权限不足")

    target_per = await get_user_permissions(target)
    if credential_change_forbidden(user["id"], target_per, str(target["_id"])):
        raise HTTPException(status_code=403, detail="不能修改管理员账号的凭据")

    initial_password = generate_initial_password()
    target_oid = str(target["_id"])
    affected = await backends.get_users_repo().set_password_guarded(
        target_oid,
        hashpw(initial_password.encode("utf-8"), gensalt()).decode("utf-8"),
        user["id"],
    )
    if affected == 0:
        # 纵深：前置判定被绕过，或目标在两次读写之间被并发改成 admin 时，
        # 条件写会拒绝（0 行），这里按同样的文案报错。
        raise HTTPException(status_code=403, detail="不能修改管理员账号的凭据")

    log.with_text(
        f"用户 {await get_user_display_name(user['id'])} 将用户 {target['name']} 的密码重置为一次性初始密码",
        type="passwd_change",
    )
    await log.insert_log()

    response.headers["Cache-Control"] = "no-store"
    return {"status": "ok", "code": 200, "data": {"initial_password": initial_password}}


@router.get("/{user_id}/totp")
async def get_user_totp_v3(
    user_id: str,
    response: Response,
    user=Depends(get_current_user),
    log=Depends(inject_log),
):
    """
    Read the caller's own current login dynamic code (TOTP).

    Permission: any authenticated user, but **only their own** code. A dynamic
    code can be exchanged for a login token, so reading someone else's code is
    equivalent to impersonating them——this endpoint is therefore narrowed to
    self only.
    """
    target = await _get_zvms_new_user(user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="用户不存在")

    if str(target["_id"]) != str(user["id"]):
        raise HTTPException(status_code=403, detail="只能查看自己的动态码")

    # Compute with the caller's own oid: login derives the code from the oid
    # (routers/users_router.py), so feeding the path parameter through would
    # produce a different code whenever a business id (student number) is used.
    code, exp = get_current_totp(user["id"])

    log.with_text(
        f"用户 {await get_user_display_name(user['id'])} 查看了自己的登录动态码",
        type="totp_view",
        level="info",
    )
    await log.insert_log()

    response.headers["Cache-Control"] = "no-store"
    return {"status": "ok", "code": 200, "data": {"otp": code, "exp": exp}}


class PutBindingRequest(BaseModel):
    xuehai_id: str
    note: Optional[str] = None


@router.get("/{user_id}/bindings")
async def list_user_bindings_v3(
    user_id: str,
    user=Depends(get_current_user),
):
    """
    List a user's 学海 device bindings, including revoked ones.

    Permission: admin only.
    """
    per = user.get("per") or []
    if "admin" not in per:
        raise HTTPException(status_code=403, detail="权限不足")

    oid = validate_object_id(user_id)
    target = await backends.get_users_repo().get_by_oid(str(oid))
    if target is None:
        raise HTTPException(status_code=404, detail="用户不存在")

    rows = await backends.get_bindings_repo().list_for_user(str(oid))
    data = [
        {
            "xuehai_id": row["xuehai_id"],
            "source": row["source"],
            "status": row["status"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "note": row["note"],
        }
        for row in rows
    ]
    return {"data": data}


@router.put("/{user_id}/bindings", status_code=204)
async def set_user_binding_v3(
    user_id: str,
    payload: PutBindingRequest,
    user=Depends(get_current_user),
    log=Depends(inject_log),
):
    """
    Bind a user to a 学海 device id. Re-binding revokes the previous active
    binding(s) and activates the new one.

    Permission: admin only.
    """
    per = user.get("per") or []
    if "admin" not in per:
        raise HTTPException(status_code=403, detail="权限不足")

    oid = validate_object_id(user_id)
    target = await backends.get_users_repo().get_by_oid(str(oid))
    if target is None:
        raise HTTPException(status_code=404, detail="用户不存在")

    bindings = backends.get_bindings_repo()
    for row in await bindings.list_for_user(str(oid)):
        if row["status"] == "active" and row["xuehai_id"] != payload.xuehai_id:
            await bindings.revoke(str(oid), row["xuehai_id"])
    await bindings.upsert(str(oid), payload.xuehai_id, "admin", payload.note)

    log.with_text(
        f"管理员 {await get_user_display_name(user['id'])} 为用户 {target['name']} 绑定了学海设备 {payload.xuehai_id}",
        type="device_bind",
    )
    await log.insert_log()

    return


@router.delete("/{user_id}/bindings/{xuehai_id}", status_code=204)
async def delete_user_binding_v3(
    user_id: str,
    xuehai_id: str,
    user=Depends(get_current_user),
    log=Depends(inject_log),
):
    """
    Revoke a user's 学海 device binding. The row is kept (status='revoked').

    Permission: admin only.
    """
    per = user.get("per") or []
    if "admin" not in per:
        raise HTTPException(status_code=403, detail="权限不足")

    oid = validate_object_id(user_id)
    target = await backends.get_users_repo().get_by_oid(str(oid))
    if target is None:
        raise HTTPException(status_code=404, detail="用户不存在")

    await backends.get_bindings_repo().revoke(str(oid), xuehai_id)

    log.with_text(
        f"管理员 {await get_user_display_name(user['id'])} 为用户 {target['name']} 解绑了学海设备 {xuehai_id}",
        type="device_unbind",
    )
    await log.insert_log()

    return
