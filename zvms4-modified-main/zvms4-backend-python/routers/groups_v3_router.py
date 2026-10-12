import re

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from database import db
from db import backends
from typings.log import inject_log
from util.object_id import get_current_user
from util.user import get_user_display_name
from utils import validate_object_id

router = APIRouter()


def _is_group_manager(user: dict) -> bool:
    per = user.get("per") or []
    return "admin" in per or "department" in per


async def _current_class_id(user: dict):
    validate_object_id(user["id"])
    current = await backends.get_users_repo().get_by_oid(user["id"])
    if current is None or current.get("class_id") is None:
        return None
    return str(current["class_id"])


async def _require_group_read_access(user: dict, group_id: str):
    if _is_group_manager(user):
        return
    per = user.get("per") or []
    if "secretary" in per and await _current_class_id(user) == group_id:
        return
    raise HTTPException(status_code=403, detail="权限不足")


async def _list_permission_group_members(
    permission_group: dict, query: str, page: int, size: int, pwdm: bool
):
    permissions = permission_group.get("permissions") or []
    if not permissions:
        return {"data": [], "total": 0}

    match: dict = {"roles": permissions[0]}
    if query:
        escaped = re.escape(query)
        match["$or"] = [
            {"name": {"$regex": escaped, "$options": "i"}},
            {"id": {"$regex": escaped, "$options": "i"}},
        ]

    total = await db.zvms_new.users.count_documents(match)
    members = (
        await db.zvms_new.users.find(match)
        .sort({"id": 1})
        .skip((page - 1) * size)
        .limit(size)
        .to_list(None)
    )

    groups_repo = backends.get_groups_repo()
    class_name_cache: dict[str, str] = {}
    result = []
    for member in members:
        class_id = str(member["class_id"]) if member.get("class_id") else ""
        class_name = ""
        if class_id:
            if class_id not in class_name_cache:
                class_group = await groups_repo.get(class_id)
                class_name_cache[class_id] = class_group["name"] if class_group else ""
            class_name = class_name_cache[class_id]
        row = {
            "_id": str(member["_id"]),
            "id": member["id"],
            "name": member["name"],
            "roles": member.get("roles", []),
            "class_id": class_id,
            "class_name": class_name,
            "group": [class_id] if class_id else [],
        }
        row["password"] = member.get("password") is not None if pwdm else None
        result.append(row)

    return {"data": result, "total": total}


@router.get("/{group_id}/users")
async def get_group_users_v3(
    group_id: str,
    page: int = Query(1),
    size: int = Query(10),
    query: str = Query(""),
    pwdm: bool = Query(False),
    user=Depends(get_current_user),
):
    """
    List the live members of a class (`zvms_new.users` by `class_id`).

    Permission: admin/department, or secretary for their own class.
    """
    await _require_group_read_access(user, group_id)

    validate_object_id(group_id)
    groups_repo = backends.get_groups_repo()

    permission_group = await db.zvms_new.groups.find_one(
        {"_id": ObjectId(group_id), "type": "permission"}
    )
    if permission_group is not None:
        return await _list_permission_group_members(
            permission_group, query, page, size, pwdm
        )

    group = await groups_repo.get(group_id)
    if group is None:
        return {"data": [], "total": 0}

    rows, total = await groups_repo.members_page(group_id, query, page, size)

    for row in rows:
        row["group"] = [row["class_id"]] if row["class_id"] else []
        row["password"] = row.pop("password", None) is not None if pwdm else None

    return {
        "data": rows,
        "total": total,
    }


class NewGroup(BaseModel):
    name: str = Field(min_length=1)


class RenameGroupRequest(BaseModel):
    name: str = Field(min_length=1)


@router.post("", status_code=201)
async def create_group_v3(
    payload: NewGroup,
    user=Depends(get_current_user),
    log=Depends(inject_log),
):
    per = user.get("per") or []
    if "admin" not in per:
        raise HTTPException(status_code=403, detail="权限不足")

    new_id = await backends.get_groups_repo().create(payload.name)

    log.with_text(
        f"管理员 {await get_user_display_name(user['id'])} 创建了班级 {payload.name}",
        type="group_create",
    )
    await log.insert_log()

    return {"id": new_id}


@router.put("/{group_id}/name", status_code=204)
async def rename_group_v3(
    group_id: str,
    payload: RenameGroupRequest,
    user=Depends(get_current_user),
    log=Depends(inject_log),
):
    per = user.get("per") or []
    if "admin" not in per:
        raise HTTPException(status_code=403, detail="权限不足")

    oid = validate_object_id(group_id)
    repo = backends.get_groups_repo()

    target = await repo.get(str(oid))
    if target is None:
        raise HTTPException(status_code=404, detail="班级不存在")

    await repo.rename(str(oid), payload.name)

    log.with_text(
        f"管理员 {await get_user_display_name(user['id'])} 将班级 {target['name']} 改名为 {payload.name}",
        type="group_update",
    )
    await log.insert_log()

    return


@router.delete("/{group_id}", status_code=204)
async def delete_group_v3(
    group_id: str,
    user=Depends(get_current_user),
    log=Depends(inject_log),
):
    per = user.get("per") or []
    if "admin" not in per:
        raise HTTPException(status_code=403, detail="权限不足")

    oid = validate_object_id(group_id)
    repo = backends.get_groups_repo()

    target = await repo.get(str(oid))
    if target is None:
        raise HTTPException(status_code=404, detail="班级不存在")

    await backends.get_users_repo().detach_class(str(oid))
    await repo.delete(str(oid))

    log.with_text(
        f"管理员 {await get_user_display_name(user['id'])} 删除了班级 {target['name']}",
        type="group_delete",
    )
    await log.insert_log()

    return


@router.post("/{group_id}/users/{user_id}", status_code=204)
async def add_group_user_v3(
    group_id: str,
    user_id: str,
    user=Depends(get_current_user),
    log=Depends(inject_log),
):
    if not _is_group_manager(user):
        raise HTTPException(status_code=403, detail="权限不足")

    goid = validate_object_id(group_id)
    uoid = validate_object_id(user_id)
    groups_repo = backends.get_groups_repo()
    users_repo = backends.get_users_repo()

    group = await groups_repo.get(str(goid))
    if group is None:
        raise HTTPException(status_code=404, detail="班级不存在")

    target = await users_repo.get_by_oid(str(uoid))
    if target is None:
        raise HTTPException(status_code=404, detail="用户不存在")

    await users_repo.set_class(str(uoid), str(goid))

    log.with_text(
        f"管理员 {await get_user_display_name(user['id'])} 将用户 {target['name']} 加入班级 {group['name']}",
        type="group_member_add",
    )
    await log.insert_log()

    return


@router.delete("/{group_id}/users/{user_id}", status_code=204)
async def remove_group_user_v3(
    group_id: str,
    user_id: str,
    user=Depends(get_current_user),
    log=Depends(inject_log),
):
    if not _is_group_manager(user):
        raise HTTPException(status_code=403, detail="权限不足")

    goid = validate_object_id(group_id)
    uoid = validate_object_id(user_id)
    groups_repo = backends.get_groups_repo()
    users_repo = backends.get_users_repo()

    group = await groups_repo.get(str(goid))
    if group is None:
        raise HTTPException(status_code=404, detail="班级不存在")

    target = await users_repo.get_by_oid(str(uoid))
    if target is None:
        raise HTTPException(status_code=404, detail="用户不存在")

    if target.get("class_id") != str(goid):
        raise HTTPException(status_code=400, detail="用户不在此班级")

    await users_repo.set_class(str(uoid), None)

    log.with_text(
        f"管理员 {await get_user_display_name(user['id'])} 将用户 {target['name']} 移出班级 {group['name']}",
        type="group_member_remove",
    )
    await log.insert_log()

    return