from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from db import backends
from db.oracle.session import session_scope
from typings.log import inject_log
from util.object_id import get_current_user
from util.user import get_user_display_name
from util.oid import validate_object_id

router = APIRouter()


def _is_group_manager(user: dict) -> bool:
    per = user.get("per") or []
    return "admin" in per or "department" in per


async def _list_permission_group_members(
    permission_group: dict, query: str, page: int, size: int, pwdm: bool, session
):
    permissions = permission_group.get("permissions") or []
    if not permissions:
        return {"data": [], "total": 0}

    members, total = await backends.get_users_repo(session).list_page(
        query, permissions[0], page, size
    )

    result = []
    for member in members:
        class_id = str(member["class_id"]) if member.get("class_id") else ""
        result.append(
            {
                "_id": str(member["_id"]),
                "id": member["id"],
                "name": member["name"],
                "roles": member.get("roles", []),
                "class_id": class_id,
                "class_name": member["class_name"] if class_id else "",
                "group": [class_id] if class_id else [],
                "banned": bool(member.get("banned")),
                "password": bool(member["has_password"]) if pwdm else None,
            }
        )

    return {"data": result, "total": total}


@router.get("/{group_id}/users")
async def get_group_users_v3(
    group_id: str,
    page: int = Query(1, ge=1),
    size: int = Query(10, ge=1, le=100),
    query: str = Query(""),
    pwdm: bool = Query(False),
    user=Depends(get_current_user),
):
    """
    List the live members of a class (`zvms_new.users` by `class_id`).

    Permission: any authenticated user (class and permission groups alike);
    ``get_current_user`` already enforces the login.
    """

    validate_object_id(group_id)

    # 一个 session_scope 装下全部读取：权限组判定 / 班级 / 成员页共享一次 checkout。
    async with session_scope() as session:
        groups_repo = backends.get_groups_repo(session)

        permission_group = await groups_repo.get_permission_group(group_id)
        if permission_group is not None:
            return await _list_permission_group_members(
                permission_group, query, page, size, pwdm, session
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
