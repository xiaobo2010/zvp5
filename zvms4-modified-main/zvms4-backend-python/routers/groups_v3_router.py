import re

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from database import db
from typings.log import inject_log
from util.object_id import get_current_user
from util.user import get_user_display_name
from utils import validate_object_id

from asyncio import gather

router = APIRouter()


def _is_group_manager(user: dict) -> bool:
    per = user.get("per") or []
    return "admin" in per or "department" in per


async def _current_class_id(user: dict):
    current = await db.zvms_new.users.find_one(
        {"_id": validate_object_id(user["id"])}, {"class_id": 1}
    )
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

    escaped = re.escape(query)

    pipeline = [
        {
            "$match": {
                "class_id": validate_object_id(group_id),
                "$or": [
                    {"name": {"$regex": escaped, "$options": "i"}},
                    {"id": {"$regex": escaped, "$options": "i"}},
                ],
            }
        },
        {"$sort": {"id": 1}},
        {"$skip": (page - 1) * size},
        {"$limit": size},
        {
            "$lookup": {
                "from": "groups",
                "localField": "class_id",
                "foreignField": "_id",
                "as": "class_obj",
            }
        },
        {"$unwind": {"path": "$class_obj", "preserveNullAndEmptyArrays": True}},
        {
            "$set": {
                "class_obj": {
                    "$ifNull": ["$class_obj", {"name": "未知班级"}]
                }
            }
        },
        {
            "$project": {
                "_id": {"$toString": "$_id"},
                "id": "$id",
                "name": "$name",
                "roles": "$roles",
                "class_id": {
                    "$convert": {
                        "input": "$class_id",
                        "to": "string",
                        "onError": "",
                        "onNull": "",
                    }
                },
                "class_name": "$class_obj.name",
                "password": "$password",
            }
        },
    ]

    count_pipeline = [
        pipeline[0],
        {"$count": "total"},
    ]

    total, result = await gather(
        db.zvms_new.users.aggregate(count_pipeline).to_list(length=1),
        db.zvms_new.users.aggregate(pipeline).to_list(None),
    )

    for row in result:
        row["group"] = [row["class_id"]] if row["class_id"] else []
        row["password"] = row.pop("password", None) is not None if pwdm else None

    return {
        "data": result,
        "total": total[0]["total"] if len(total) else 0,
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

    result = await db.zvms_new.groups.insert_one({"name": payload.name})

    log.with_text(
        f"管理员 {await get_user_display_name(user['id'])} 创建了班级 {payload.name}",
        type="group_create",
    )
    await log.insert_log()

    return {"id": str(result.inserted_id)}


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

    target = await db.zvms_new.groups.find_one({"_id": oid})
    if target is None:
        raise HTTPException(status_code=404, detail="班级不存在")

    await db.zvms_new.groups.update_one({"_id": oid}, {"$set": {"name": payload.name}})

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

    target = await db.zvms_new.groups.find_one({"_id": oid})
    if target is None:
        raise HTTPException(status_code=404, detail="班级不存在")

    await db.zvms_new.users.update_many({"class_id": oid}, {"$set": {"class_id": None}})
    await db.zvms_new.groups.delete_one({"_id": oid})

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

    group = await db.zvms_new.groups.find_one({"_id": goid})
    if group is None:
        raise HTTPException(status_code=404, detail="班级不存在")

    target = await db.zvms_new.users.find_one({"_id": uoid})
    if target is None:
        raise HTTPException(status_code=404, detail="用户不存在")

    await db.zvms_new.users.update_one({"_id": uoid}, {"$set": {"class_id": goid}})

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

    group = await db.zvms_new.groups.find_one({"_id": goid})
    if group is None:
        raise HTTPException(status_code=404, detail="班级不存在")

    target = await db.zvms_new.users.find_one({"_id": uoid})
    if target is None:
        raise HTTPException(status_code=404, detail="用户不存在")

    if target.get("class_id") != goid:
        raise HTTPException(status_code=400, detail="用户不在此班级")

    await db.zvms_new.users.update_one({"_id": uoid}, {"$set": {"class_id": None}})

    log.with_text(
        f"管理员 {await get_user_display_name(user['id'])} 将用户 {target['name']} 移出班级 {group['name']}",
        type="group_member_remove",
    )
    await log.insert_log()

    return