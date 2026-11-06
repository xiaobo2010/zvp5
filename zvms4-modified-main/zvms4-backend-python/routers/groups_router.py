import re
import asyncio
from io import BytesIO
from typing import Optional
import pandas as pd
from fastapi.responses import Response
from typings.export import ExportFormat
from typings.group import Group
from fastapi import APIRouter, HTTPException, Depends, Query
from db import backends
from db.repos.pagination import page_args
from pydantic import BaseModel

from util.calculate import calculate_user_time_bulk
from util.cert import custom_password_flags_async
from util.get_class import get_activities_related_to_user

from util.object_id import (
    compulsory_temporary_token,
    get_current_user,
    validate_object_id,
)
from util.permission.user import validate_read_group_permission
from typings.log import inject_log
from util.user import get_user_display_name, public_user

router = APIRouter()

# 班级列表的硬上限（与 ``db.repos.oracle.groups._CLASS_LIST_CAP`` 同值）：
# 这里把「班级 + 权限组」合并后再排序切片，所以先按上限取一页班级，
# 避免为了分页把整张 ``GROUPS`` 拉进内存。
_CLASS_LIST_CAP = 1000


@router.post("")
async def create_group(
    payload: Group, user=Depends(get_current_user), log=Depends(inject_log)
):
    """
    Create a user group
    """

    if "admin" not in user["per"]:
        raise HTTPException(status_code=403, detail="Permission denied")

    id = await backends.get_groups_repo().create(payload.name)

    log.with_text(
        f"管理员 {await get_user_display_name(user['id'])} 创建了用户组 {payload.name}",
        type="group_create",
    )
    await log.insert_log()

    return {
        "status": "ok",
        "code": 201,
        "data": {"_id": id},
    }


@router.get("")
async def get_groups(
    page: int = Query(1, ge=-1, description="Page number for pagination"),
    perpage: int = Query(10, ge=1, le=100, description="Number of items per page"),
    search: str = Query("", description="Search term for group names"),
    type: str = Query("all", description="Group type filter: all/class/permission"),
    user=Depends(get_current_user),
):
    """
    Get all groups
    """

    per = user.get("per") or []
    privileged = "admin" in per or "department" in per
    if type == "permission" and not privileged:
        raise HTTPException(status_code=403, detail="权限不足")

    # ``all`` degrades silently to the class side for unprivileged callers
    # instead of 403: the class-name lookup stays usable, while the permission
    # manifest is simply omitted (no 403 that would break that use). An explicit
    # ``permission`` request is refused above, matching the v2 list endpoint.
    include_permission = privileged

    rows = []
    groups_repo = backends.get_groups_repo()
    permission_groups = await groups_repo.list_permission_groups(search)
    permission_ids = {str(group["_id"]) for group in permission_groups}

    if type != "permission":
        # 一次往返拿一页班级 + 总数（SQL 侧 OFFSET/FETCH + 窗口计数），
        # 不再「先 COUNT 再按总数把整表拉回来」。
        class_groups, _ = await groups_repo.list_classes(search, 1, _CLASS_LIST_CAP)
        for group in class_groups:
            if str(group["_id"]) in permission_ids:
                continue
            rows.append(
                {
                    "_id": str(group["_id"]),
                    "name": group["name"],
                    "type": "class",
                }
            )

    if type != "class" and include_permission:
        for group in permission_groups:
            rows.append(
                {
                    "_id": str(group["_id"]),
                    "name": group["name"],
                    "type": "permission",
                    "permissions": group.get("permissions", []),
                }
            )

    rows.sort(key=lambda group: group["name"])

    count = len(rows)

    # ``page < 1`` = 「一次全量」，不是第 1 页；哨兵语义特殊，故不走
    # ``db.repos.pagination.page_args``（那里会把页号钳到 1）。
    if page < 1:
        result = rows
    else:
        start = (page - 1) * perpage
        result = rows[start : start + perpage]

    return {
        "status": "ok",
        "code": 200,
        "data": result,
        "metadata": {"size": count},
    }


@router.get("/{group_id}")
async def get_group(group_id: str, user=Depends(get_current_user)):
    """
    Get a group
    """
    await validate_read_group_permission(
        user, group_id, "volunteer", allow_class=True
    )

    result = await backends.get_groups_repo().get_permission_group(group_id)

    if result is not None:
        data = {
            "_id": str(result["_id"]),
            "name": result["name"],
            "type": "permission",
            "permissions": result.get("permissions", []),
        }
    else:
        result = await backends.get_groups_repo().get(group_id)
        if result is None:
            raise HTTPException(status_code=404, detail="Group not found")
        data = {
            "_id": str(result["_id"]),
            "name": result["name"],
            "type": "class",
        }

    return {
        "status": "ok",
        "code": 200,
        "data": data,
    }


class PutGroupName(BaseModel):
    name: str


@router.put("/{group_id}/name")
async def update_group_name(
    group_id: str,
    payload: PutGroupName,
    user=Depends(get_current_user),
    log=Depends(inject_log),
):
    """
    Update group name
    """

    if "admin" not in user["per"]:
        raise HTTPException(status_code=403, detail="Permission denied")

    oid = str(validate_object_id(group_id))
    groups_repo = backends.get_groups_repo()

    target = await groups_repo.get(oid)
    if target is None:
        raise HTTPException(status_code=404, detail="Group not found")

    await groups_repo.rename(oid, payload.name)

    log.with_text(
        f"管理员 {await get_user_display_name(user['id'])} 将用户组 {target['name']} 改名为 {payload.name}",
        type="group_update",
    )
    await log.insert_log()

    return {
        "status": "ok",
        "code": 200,
    }


@router.get("/{group_id}/activities")
async def get_class_activities(
    group_id: str,
    page: int = Query(1, ge=1, description="Page number for pagination"),
    perpage: int = Query(10, ge=1, le=100, description="Number of items per page"),
    query: str = Query("", description="Search query for activities"),
    user=Depends(get_current_user),
):
    """
    Get activities related to a group
    """
    if query != "" and "admin" not in user["per"]:
        query = re.escape(query)

    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    result, count = await get_activities_related_to_user(
        user["id"], page, perpage, query, group_id
    )
    return {
        "status": "ok",
        "code": 200,
        "data": result,
        "metadata": {"size": count},
    }


@router.get("/{group_id}/user")
async def get_users_in_class(
    group_id: str,
    page: int = Query(1, ge=1, description="Page number for pagination"),
    perpage: int = Query(10, ge=1, le=100, description="Number of items per page"),
    search: str = Query("", description="Search term for user names"),
    pwdm: bool = Query(False, description="Include password mode information"),
    user=Depends(get_current_user),
):
    """
    Get users in a class
    """
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    class_oid = validate_object_id(group_id)
    result = await backends.get_users_repo().list_by_class(str(class_oid))
    if search:
        pattern = re.compile(re.escape(search), re.IGNORECASE)
        result = [user for user in result if pattern.search(user.get("name") or "")]
    count = len(result)
    result.sort(key=lambda user: user.get("id") or "")
    offset, limit = page_args(page, perpage)
    result = result[offset : offset + limit]
    custom_flags = (
        await custom_password_flags_async(result) if pwdm else None
    )
    for index, user in enumerate(result):
        user["_id"] = str(user["_id"])
        class_id = str(user["class_id"]) if user.get("class_id") else ""
        user["class_id"] = class_id
        user["group"] = [class_id] if class_id else []
        if pwdm:
            user["password"] = custom_flags[index]
        else:
            user["password"] = None
    return {"status": "ok", "code": 200, "data": [public_user(row) for row in result], "metadata": {"size": count}}


@router.get("/{group_id}/time")
async def get_user_times_in_class(
    group_id: str,
    page: int = Query(1, ge=1, description="Page number for pagination"),
    perpage: int = Query(10, ge=1, le=100, description="Number of items per page"),
    exceeding: bool = Query(True, description="Include exceeding time calculations"),
    shortage: bool = Query(False, description="Include shortage calculations"),
    start: Optional[str] = Query(None, description="Start date filter"),
    end: Optional[str] = Query(None, description="End date filter"),
    search: str = Query("", description="Search term for user names"),
    allow_cache: bool = Query(True, description="Allow cached time calculations"),
    user=Depends(get_current_user),
):
    """
    Get users in a class
    """
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    class_oid = validate_object_id(group_id)
    result = await backends.get_users_repo().list_by_class(str(class_oid))
    if search:
        pattern = re.compile(re.escape(search), re.IGNORECASE)
        result = [user for user in result if pattern.search(user.get("name") or "")]
    count = len(result)
    result.sort(key=lambda user: user.get("id") or "")
    offset, limit = page_args(page, perpage)
    result = result[offset : offset + limit]
    selected_students = [str(user["_id"]) for user in result]
    user_times = await calculate_user_time_bulk(
        selected_students, start, end, allow_cache=allow_cache
    )
    if not allow_cache and start is None and end is None:
        # 整批一个事务重算缓存：逐学生 upsert 是每人 2 次往返 + 1 次 commit，
        # 100 人最坏 ~41s，且中途失败会留下半新半旧的缓存。
        await backends.get_time_repo().upsert_many_for_users(
            [
                (
                    student,
                    user_times[student]["on-campus"],
                    user_times[student]["off-campus"],
                    user_times[student]["social-practice"],
                )
                for student in selected_students
            ]
        )
    time = []
    class_groups: dict[str, Optional[dict]] = {}
    for user in result:
        user_time = user_times.get(str(user["_id"])) or {
            "on-campus": 0.0,
            "off-campus": 0.0,
            "social-practice": 0.0,
        }
        if exceeding or shortage:
            more_on_campus = min(
                round(max(user_time["off-campus"] - 15, 1) / 2, 0), 6.0
            )
            more_off_campus = min(
                round(max(user_time["on-campus"] - 25, 1) / 3, 0), 6.0
            )
            user_time["on-campus"] += more_on_campus
            user_time["off-campus"] += more_off_campus
        if shortage:
            user_time["on-campus"] = max(25 - user_time["on-campus"], 0)
            user_time["off-campus"] = max(15 - user_time["off-campus"], 0)
            user_time["social-practice"] = max(18 - user_time["social-practice"], 0)
        class_id = user.get("class_id")
        if class_id is None:
            continue
        class_key = str(class_id)
        if class_key not in class_groups:
            class_groups[class_key] = await backends.get_groups_repo().get(class_key)
        class_group = class_groups[class_key]
        if class_group is None:
            continue
        doc = {
            "_id": str(user["_id"]),
            "name": user["name"],
            "id": str(user["id"]),
            "group": class_group["name"],
            "on-campus": user_time["on-campus"],
            "off-campus": user_time["off-campus"],
            "social-practice": user_time["social-practice"],
        }
        time.append(doc)
    return {"status": "ok", "code": 200, "data": time, "metadata": {"size": count}}


class PutGroupDescription(BaseModel):
    description: str


@router.put("/{group_id}/description")
async def update_group_description(
    group_id: str,
    payload: PutGroupDescription,
    user=Depends(get_current_user),
    log=Depends(inject_log),
):
    """
    Update group description
    """

    if "admin" not in user["per"]:
        raise HTTPException(status_code=403, detail="Permission denied")

    await backends.get_groups_repo().set_description(group_id, payload.description)

    log.with_text(
        f"管理员 {await get_user_display_name(user['id'])} 修改了用户组描述（ID: {group_id}）",
        type="group_update",
    )
    await log.insert_log()

    return {
        "status": "ok",
        "code": 200,
    }


@router.delete("/{group_id}")
async def delete_group(
    group_id: str, user=Depends(compulsory_temporary_token), log=Depends(inject_log)
):
    """
    Remove group
    """

    if "admin" not in user["per"]:
        raise HTTPException(status_code=403, detail="Permission denied")

    groups_repo = backends.get_groups_repo()
    target = await groups_repo.get(group_id)
    if target is None:
        raise HTTPException(status_code=404, detail="Group not found")

    await groups_repo.delete(group_id)

    log.with_text(
        f"管理员 {await get_user_display_name(user['id'])} 删除了用户组 {target['name']}",
        type="group_delete",
    )
    await log.insert_log()

    return {
        "status": "ok",
        "code": 200,
    }


@router.get("/{group_id}/template")
async def get_group_template(
    group_id: str, export_format: ExportFormat, user=Depends(get_current_user)
):
    same_class = False
    if "secretary" in user["per"]:
        target = await backends.get_users_repo().get_by_oid(user["id"])
        if target is None:
            raise HTTPException(status_code=404, detail="User not found")
        classid = str(target["class_id"]) if target.get("class_id") else None
        if classid == group_id:
            same_class = True
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    if (
        "admin" not in user["per"]
        and "auditor" not in user["per"]
        and "department" not in user["per"]
        and ("secretary" not in user["per"] or not same_class)
    ):
        raise HTTPException(status_code=403, detail="Permission denied")

    class_oid = validate_object_id(group_id)
    db_users = await backends.get_users_repo().list_by_class(str(class_oid))

    class_group = await backends.get_groups_repo().get(str(class_oid))
    if class_group is None:
        raise HTTPException(status_code=404, detail="Group not found")
    group_name = class_group["name"]

    users = []

    for user in db_users:
        users.append(
            {
                "_id": str(user["_id"]),
                "Name": user["name"],
                "ID": user["id"],
                "Group": group_name,
                "On Campus": None,
                "Off Campus": None,
                "Social Practice": None,
            }
        )

    table = pd.DataFrame(users, columns=["_id", "Name", "ID", "Group", "On Campus", "Off Campus", "Social Practice"]).sort_values("ID")

    def render():
        buffer = BytesIO()
        if export_format == ExportFormat.excel:
            table.to_excel(buffer, index=False)
        elif export_format == ExportFormat.csv:
            buffer.write(table.to_csv(index=False).encode("utf-8-sig"))
        else:
            raise HTTPException(status_code=400, detail="Unsupported format")
        return buffer.getvalue()

    return Response(await asyncio.to_thread(render), media_type=export_format.mime(),
                    headers={"Content-Disposition": f'attachment; filename="template.{export_format.suffix()}"'})
