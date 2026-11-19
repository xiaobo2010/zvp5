"""班级/分组域单路由文件（原 groups_router + groups_v2_router + groups_v3_router 合并）。

统一挂载 ``/api/v1/groups``。合并时按「前端实际调用」逐端点裁决：

* 列表、单班详情、班级活动、班级时长、改名、删除、建班都有 v2/v3 的同路径实现，
  前端已全部打到 v1 路径并与之一致，未版本化实现删除；
* 班级模板导出在 v1 侧没有对应实现，原样迁入；单数 ``/user`` 端点（前端零调用）
  已于 2026-11-19 删除，成员列表只剩复数 ``/users``；
* 建班以 v3 为唯一实现（前端 ``createGroupV3``；legacy ``createGroup`` 零调用方）。

**注册顺序是契约**：静态子段（``/users``、``/template`` …）先于任何可能兜底的
参数段注册，``/{group_id}`` 单段路由不会被后注册的静态路由遮蔽。
"""

import asyncio
from collections import defaultdict
from io import BytesIO
from typing import Optional

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field, ValidationError

from config import (
    BASE_OFF_CAMPUS,
    BASE_ON_CAMPUS,
    BASE_SOCIAL_PRACTICE,
)
from db import backends
from db.oracle.session import session_scope
from db.repos.pagination import page_args
from typings.export import ExportFormat
from typings.log import inject_log
from typings.time import UserActivityTime
from util.calculate import calculate_user_time_bulk
from util.object_id import (
    compulsory_temporary_token,
    get_current_user,
    validate_object_id,
)
from util.permission.user import validate_read_group_permission
from util.user import get_user_display_name
from util.validation import validate_group_description, validate_group_name

router = APIRouter()

# 班级列表的硬上限（与 ``db.repos.oracle.groups._CLASS_LIST_CAP`` 同值）：
# 合并「班级 + 权限组」后的排序切片在内存里做，班级一侧先按上限取一页。
_CLASS_LIST_CAP = 1000


def _as_float(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _user_activity_time(row) -> UserActivityTime:
    try:
        return UserActivityTime.model_validate(row, strict=False)
    except ValidationError:
        return UserActivityTime.model_construct(
            user=str(row.get("user") or ""),
            on_campus_raw=_as_float(row.get("on_campus_raw")),
            off_campus_raw=_as_float(row.get("off_campus_raw")),
            social_practice=_as_float(row.get("social_practice")),
            updated_at=row.get("updated_at"),
        )


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


# ---------------------------------------------------------------------------
# 集合：列表 / 建班
# ---------------------------------------------------------------------------


@router.get("")
async def get_groups_v2(
    page: int = Query(1, ge=1),
    size: int = Query(10, ge=1, le=100),
    query: str = Query(""),
    user=Depends(get_current_user),
):
    """
    List the live classes (`zvms_new.groups`) plus permission groups.

    Permission: admin/department.
    """
    per = user.get("per") or []
    if not ("admin" in per or "department" in per):
        raise HTTPException(status_code=403, detail="权限不足")

    repo = backends.get_groups_repo()
    # 一次往返：一页班级 + 总数（SQL 侧 OFFSET/FETCH + COUNT(*) OVER()）。
    class_rows, _ = await repo.list_classes(query, 1, _CLASS_LIST_CAP)

    permission_rows = await repo.list_permission_groups(query)

    permission_ids = {str(group["_id"]) for group in permission_rows}
    class_rows = [row for row in class_rows if str(row["_id"]) not in permission_ids]

    permission_rows = [
        {
            "_id": str(group["_id"]),
            "name": group["name"],
            "type": "permission",
            "permissions": group.get("permissions", []),
        }
        for group in permission_rows
    ]
    permission_rows.sort(key=lambda group: group["name"])

    merged = class_rows + permission_rows
    total = len(merged)

    offset, limit = page_args(page, size)
    result = merged[offset : offset + limit]

    return {
        "data": result,
        "total": total,
    }


class NewGroup(BaseModel):
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

    validated, reason = validate_group_name(payload.name)
    if not validated:
        raise HTTPException(status_code=400, detail=reason)

    new_id = await backends.get_groups_repo().create(payload.name)

    log.with_text(
        f"管理员 {await get_user_display_name(user['id'])} 创建了班级 {payload.name}",
        type="group_create",
    )
    await log.insert_log()

    return {"id": new_id}


# ---------------------------------------------------------------------------
# 单个班级
# ---------------------------------------------------------------------------


@router.get("/{group_id}")
async def get_group_v2(
    group_id: str,
    user=Depends(get_current_user),
):
    """
    Read a single live class (`zvms_new.groups`).

    Permission: admin/department, or the requester's own class.
    """
    per = user.get("per") or []
    if not ("admin" in per or "department" in per):
        validate_object_id(user["id"])
        current = await backends.get_users_repo().get_by_oid(user["id"])
        if not (
            current is not None
            and current.get("class_id") is not None
            and str(current["class_id"]) == group_id
        ):
            raise HTTPException(status_code=403, detail="权限不足")

    result = await backends.get_groups_repo().get(str(validate_object_id(group_id)))
    if result is None:
        raise HTTPException(status_code=404, detail="班级不存在")

    return {
        "status": "ok",
        "code": 200,
        "data": {
            "_id": result["_id"],
            "name": result["name"],
            "type": "class",
        },
    }


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


@router.get("/{group_id}/activities")
async def get_group_activities_v2(
    group_id: str,
    page: int = Query(1, ge=1, description="Page number for pagination"),
    perpage: int = Query(10, ge=1, le=100, description="Number of items per page"),
    search: str = Query("", max_length=64, description="Search keyword"),
    sort: str = Query("_id", description="Sort by field"),
    regex: bool = Query(False, description="已废弃：关键字一律按字面量匹配，不再走正则"),
    asc: bool = Query(False, description="Sort in ascending order"),
    user=Depends(get_current_user),
):
    """
    Get group activities
    :param group_id: Group ID
    :param page: The page number
    :param perpage: Items per page
    :param search: Search keyword
    :param sort: Sort by field
    :param regex: Use regex for search
    :param asc: Sort in ascending order
    :param user: Current user
    """

    class_oid = validate_object_id(group_id)

    # ``search`` 按字面量关键字处理（仓储侧转义 LIKE 通配符）；``regex`` 保留
    # 只是为了不破坏旧客户端的 query 参数。
    activities, count = await backends.get_groups_repo().activities_page(
        str(class_oid), search, sort, asc, page, perpage
    )

    for activity in activities:
        activity["_id"] = str(activity["_id"])

    return {
        "total": count,
        "page": page,
        "perpage": perpage,
        "activities": activities,
    }


@router.get("/{group_id}/time")
async def read_group_time(
    group_id: str,
    query: str = Query("", description="Search query for user name or ID"),
    search: str = Query(
        "", description="Alias of query (legacy frontend parameter)"
    ),
    page: int = Query(1, ge=1, description="Page number for pagination"),
    perpage: int = Query(5, ge=1, le=100, description="Number of items per page"),
    allow_cache: bool = Query(True, description="Allow cached data"),
    sort: str = Query("id", description="Sort by field"),
    asc: bool = Query(True, description="Sort in ascending order"),
    user: Optional[str] = Depends(get_current_user),
):
    """
    Query the activity time of the users in a group
    """
    sortkey = {
        "on-campus": "on_campus_raw",
        "off-campus": "off_campus_raw",
        "social-practice": "social_practice",
    }
    # 前端历史版本传 ``search``，后端签名是 ``query``：两个名字都收，``query``
    # 优先，避免班级时长搜索被静默忽略（q 为空时再用 search）。
    query = query or search
    class_oid = validate_object_id(group_id)
    user_times, count = await backends.get_time_repo().list_by_class(
        str(class_oid),
        query,
        sortkey.get(sort, sort) if sort else None,
        not asc,
        page,
        perpage,
    )
    results = []

    for user in user_times:
        time_struct = _user_activity_time(user)
        results.append(
            {
                "_id": str(user["user"]),
                "name": user["name"],
                "id": user["id"],
                "on-campus": time_struct.on_campus,
                "off-campus": time_struct.off_campus,
                "social-practice": time_struct.social_practice,
            }
        )

    return {"status": "ok", "code": 200, "data": results, "metadata": {"size": count}}


@router.get("/{group_id}/statistics/compliance")
async def stat_group_compliance(
    group_id: str,
    user=Depends(get_current_user),
):
    """
    Get group compliance statistics
    :param group_id: Group ID
    :param user: Current user
    """

    await validate_read_group_permission(user, group_id, "volunteer")

    # One session for the whole read: the class roster and the (cached) bulk
    # time lookup then share a single connection, so the request pays one
    # checkout and one teardown instead of two.
    class_oid = str(validate_object_id(group_id))
    async with session_scope() as session:
        roster_rows = await backends.get_users_repo(session).list_by_class(class_oid)

        members = [str(member["_id"]) for member in roster_rows]

        user_times = await calculate_user_time_bulk(
            members, allow_cache=True, session=session
        )

    member_time_counts = []

    on_campus = defaultdict(int)
    off_campus = defaultdict(int)
    social_practice = defaultdict(int)
    # The member roster behind every bucket, filled in the very same loop as the
    # counts: no second pass, and no member is ever counted twice in one mode.
    on_campus_members = defaultdict(list)
    off_campus_members = defaultdict(list)
    social_practice_members = defaultdict(list)

    # Percentage of compliance, 0%–20%, 20%–40%, 40%–60%, 60%–80%, 80%–100%, completed.

    for member in roster_rows:
        member_oid = str(member["_id"])
        user_time = user_times[member_oid]
        on_campus_range = min(
            int(user_time["on-campus"] / BASE_ON_CAMPUS * 100 // 20), 5
        )
        off_campus_range = min(
            int(user_time["off-campus"] / BASE_OFF_CAMPUS * 100 // 20), 5
        )
        social_practice_range = min(
            int(user_time["social-practice"] / BASE_SOCIAL_PRACTICE * 100 // 20), 5
        )
        on_campus[on_campus_range] += 1
        off_campus[off_campus_range] += 1
        social_practice[social_practice_range] += 1
        # Explicit projection: only the whitelisted identity fields leave the
        # repository row, so a password hash can never ride along in a roster.
        identity = {
            "_id": member_oid,
            "id": str(member["id"]) if member.get("id") else "",
            "name": member.get("name") or "",
        }
        on_campus_members[on_campus_range].append(
            {**identity, "duration": float(user_time["on-campus"])}
        )
        off_campus_members[off_campus_range].append(
            {**identity, "duration": float(user_time["off-campus"])}
        )
        social_practice_members[social_practice_range].append(
            {**identity, "duration": float(user_time["social-practice"])}
        )

    def convert_key_name(key: int) -> str:
        if key == 5:
            return "Completed"
        return f"{key * 20}%–{(key + 1) * 20}%"

    # Sort each category by its values (member counts)
    sorted_on_campus = dict(
        sorted(
            {convert_key_name(k): v for k, v in on_campus.items()}.items(),
            key=lambda item: item[0],
        )
    )
    sorted_off_campus = dict(
        sorted(
            {convert_key_name(k): v for k, v in off_campus.items()}.items(),
            key=lambda item: item[0],
        )
    )
    sorted_social_practice = dict(
        sorted(
            {convert_key_name(k): v for k, v in social_practice.items()}.items(),
            key=lambda item: item[0],
        )
    )

    def bucket_rosters(counts, members_by_range):
        """Mirror the bucket keys of one mode onto its member lists.

        Iterating ``sorted(counts)`` guarantees an exact, per-character key match
        with the aggregate dict built just above (both go through
        ``convert_key_name``), so the frontend can join the two by label.
        """
        return {
            convert_key_name(key): members_by_range[key] for key in sorted(counts)
        }

    member_rosters = {
        "on-campus": bucket_rosters(on_campus, on_campus_members),
        "off-campus": bucket_rosters(off_campus, off_campus_members),
        "social-practice": bucket_rosters(
            social_practice, social_practice_members
        ),
    }

    response = {
        "on-campus": sorted_on_campus,
        "off-campus": sorted_off_campus,
        "social-practice": sorted_social_practice,
        # Additive field: the per-bucket member rosters assembled from the rows
        # the handler already read (zero extra queries). Emitted unconditionally
        # so the response shape never depends on the data it carries.
        "member_rosters": member_rosters,
    }
    return response


# ---------------------------------------------------------------------------
# 班级模板导出（v1 侧无对应实现）
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# 改名 / 描述 / 删除
# ---------------------------------------------------------------------------


class RenameGroupRequest(BaseModel):
    name: str = Field(min_length=1)


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

    validated, reason = validate_group_name(payload.name)
    if not validated:
        raise HTTPException(status_code=400, detail=reason)

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

    validated, reason = validate_group_description(payload.description)
    if not validated:
        raise HTTPException(status_code=400, detail=reason)

    oid = str(validate_object_id(group_id))
    groups_repo = backends.get_groups_repo()
    if await groups_repo.get(oid) is None:
        raise HTTPException(status_code=404, detail="Group not found")

    await groups_repo.set_description(oid, payload.description)

    log.with_text(
        f"管理员 {await get_user_display_name(user['id'])} 修改了用户组描述（ID: {group_id}）",
        type="group_update",
    )
    await log.insert_log()

    return {
        "status": "ok",
        "code": 200,
    }


@router.delete("/{group_id}", status_code=204)
async def delete_group_v3(
    group_id: str,
    user=Depends(compulsory_temporary_token),
    log=Depends(inject_log),
):
    per = user.get("per") or []
    if "admin" not in per:
        raise HTTPException(status_code=403, detail="权限不足")

    oid = validate_object_id(group_id)
    deleted = await backends.get_groups_repo().delete_cascade(str(oid))
    if deleted is None:
        raise HTTPException(status_code=404, detail="班级不存在")

    log.with_text(
        f"管理员 {await get_user_display_name(user['id'])} 删除了班级 {deleted['name']}",
        type="group_delete",
    )
    await log.insert_log()

    return


# ---------------------------------------------------------------------------
# 班级成员增删
# ---------------------------------------------------------------------------


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
