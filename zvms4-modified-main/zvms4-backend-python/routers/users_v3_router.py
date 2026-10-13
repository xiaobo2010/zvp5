from typing import Literal, Optional

from bcrypt import hashpw, gensalt
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from db import backends
from typings.log import inject_log
from util.cert import get_current_totp
from util.object_id import compulsory_temporary_token, get_current_user
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

    Permission: self, or admin/department, or secretary in the same class.
    """
    validate_object_id(user_id)

    if user_id != user["id"]:
        per = user.get("per") or []
        if "admin" in per or "department" in per:
            pass
        elif "secretary" in per:
            current = await _get_zvms_new_user(user["id"])
            target = await _get_zvms_new_user(user_id)
            if not (
                current is not None
                and target is not None
                and str(current.get("class_id")) == str(target.get("class_id"))
            ):
                raise HTTPException(status_code=403, detail="权限不足")
        else:
            raise HTTPException(status_code=403, detail="权限不足")

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
    }

    return {"status": "ok", "code": 200, "data": profile}


AllowedRole = Literal["student", "secretary", "department", "admin", "teacher"]


class PutRolesRequest(BaseModel):
    roles: list[AllowedRole]


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


class NewUserRequest(BaseModel):
    id: str
    name: str
    roles: list[str] = Field(default_factory=lambda: ["student"])
    class_id: Optional[str] = None


@router.post("", status_code=201)
async def create_user_v3(
    payload: NewUserRequest,
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

    password = hashpw(payload.id.encode("utf-8"), gensalt()).decode("utf-8")

    new_id = await repo.create(
        payload.id,
        payload.name,
        list(payload.roles),
        password,
        str(class_oid) if class_oid is not None else None,
    )

    log.with_text(
        f"管理员 {await get_user_display_name(user['id'])} 创建了用户 {payload.name}（学号 {payload.id}）",
        type="user_create",
    )
    await log.insert_log()

    return {"id": new_id}


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
    user=Depends(compulsory_temporary_token),
    log=Depends(inject_log),
):
    """
    Reset a user's password to the initial one (the student number).

    Permission: admin/department, or secretary in the same class.
    Requires a temporary token (re-verification), mirroring zvp's
    `get_authz_user` posture for credential-affecting operations.
    """
    per = user.get("per") or []
    target = await _get_zvms_new_user(user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="用户不存在")

    if "admin" not in per and "department" not in per:
        if "secretary" not in per or not await _is_same_class(user["id"], user_id):
            raise HTTPException(status_code=403, detail="权限不足")

    await backends.get_users_repo().set_password(user_id, None)

    log.with_text(
        f"用户 {await get_user_display_name(user['id'])} 将用户 {target['name']} 的密码重置为初始密码（学号）",
        type="passwd_change",
    )
    await log.insert_log()

    return {"status": "ok", "code": 200}


@router.get("/{user_id}/totp")
async def get_user_totp_v3(
    user_id: str,
    user=Depends(get_current_user),
    log=Depends(inject_log),
):
    """
    Read the current login dynamic code (TOTP) of a user.

    Permission: admin only. The code is computed locally per user and is
    never delivered through any external channel.
    """
    per = user.get("per") or []
    if "admin" not in per:
        raise HTTPException(status_code=403, detail="权限不足")

    target = await _get_zvms_new_user(user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="用户不存在")

    code, exp = get_current_totp(user_id)

    log.with_text(
        f"管理员 {await get_user_display_name(user['id'])} 查看了用户 {target['name']} 的登录动态码",
        type="totp_view",
        level="warning",
    )
    await log.insert_log()

    return {"status": "ok", "code": 200, "data": {"otp": code, "exp": exp}}