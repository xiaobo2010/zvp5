import re
from collections import defaultdict
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from config import (
    BASE_OFF_CAMPUS,
    ON_TO_OFF_RATE,
    BASE_ON_CAMPUS,
    OFF_TO_ON_RATE,
    MAX_EXCEED_DISCOUNT,
    BASE_SOCIAL_PRACTICE,
)
from db import backends
from typings.time import UserActivityTime
from util.calculate import calculate_user_time_bulk
from util.get_class import get_user_class
from util.object_id import get_current_user, validate_object_id
from util.permission.user import validate_read_group_permission
from util.cert import check_password

router = APIRouter()


@router.get("")
async def get_groups_v2(
    page: int = Query(1),
    size: int = Query(10),
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
    _, class_total = await repo.list_classes(query, 1, 1)
    class_rows, _ = await repo.list_classes(query, 1, class_total or 1)

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

    start = (page - 1) * size
    result = merged[start : start + size]

    return {
        "data": result,
        "total": total,
    }


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


@router.get("/{group_id}/activities")
async def get_group_activities_v2(
    group_id: str,
    page: int = Query(1, ge=1, description="Page number for pagination"),
    perpage: int = Query(10, ge=1, le=100, description="Number of items per page"),
    search: str = Query("", description="Search keyword"),
    sort: str = Query("_id", description="Sort by field"),
    regex: bool = Query(True, description="Use regex for search"),
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

    await validate_read_group_permission(user, group_id, "volunteer")

    class_oid = validate_object_id(group_id)

    if not regex:
        search = re.escape(search) if search else ""

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
async def read_users(
    group_id: str,
    query: str = Query("", description="Search query for user name or ID"),
    page: int = Query(1, ge=-1, description="Page number for pagination"),
    perpage: int = Query(5, ge=1, le=100, description="Number of items per page"),
    allow_cache: bool = Query(True, description="Allow cached data"),
    sort: str = Query("id", description="Sort by field"),
    asc: bool = Query(True, description="Sort in ascending order"),
    user: Optional[str] = Depends(get_current_user),
):
    """
    Query users
    """
    await validate_read_group_permission(user, group_id, "volunteer")

    sortkey = {
        "on-campus": "on_campus_raw",
        "off-campus": "off_campus_raw",
        "social-practice": "social_practice",
    }
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
        time_struct = UserActivityTime.model_validate(user, strict=False)
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


@router.get("/{group_id}/users")
async def get_group_users_v2(
    group_id: str,
    page: int = Query(1, ge=1, description="Page number for pagination"),
    perpage: int = Query(10, ge=1, le=100, description="Number of items per page"),
    search: str = Query("", description="Search keyword"),
    sort: str = Query("id", description="Sort by field"),
    regex: bool = Query(True, description="Use regex for search"),
    asc: bool = Query(True, description="Sort in ascending order"),
    exceeding: bool = Query(True, description="Include exceeding time"),
    shortage: bool = Query(False, description="Include shortage time"),
    pwdm: bool = Query(False, description="Include password mode information"),
    start: Optional[str] = Query(None, description="Start date filter"),
    end: Optional[str] = Query(None, description="End date filter"),
    user=Depends(get_current_user),
):
    """
    Get group time
    :param group_id: Group ID
    :param page: The page number
    :param perpage: Items per page
    :param search: Search keyword
    :param sort: Sort by field
    :param regex: Use regex for search
    :param asc: Sort in ascending order
    :param user: Current user
    :param exceeding: Exceeding time
    :param shortage: Shortage time
    :param pwdm: Include password mode information
    :param start: Start date
    :param end: End date
    """

    await validate_read_group_permission(user, group_id, "volunteer")

    # Get members from the group
    class_oid = validate_object_id(group_id)
    members = await backends.get_users_repo().list_by_class(str(class_oid))
    if search:
        pattern = re.compile(search, re.IGNORECASE)
        members = [
            member
            for member in members
            if pattern.search(member.get("name") or "")
            or pattern.search(member.get("id") or "")
        ]
    count = len(members)
    if sort:
        members.sort(
            key=lambda member: (member.get(sort) is None, member.get(sort)),
            reverse=not asc,
        )
    start = (page - 1) * perpage
    members = members[start : start + perpage]

    for member in members:
        member["_id"] = str(member["_id"])
        class_id = str(member["class_id"]) if member.get("class_id") else ""
        member["class_id"] = class_id
        member["group"] = [class_id] if class_id else []
        if pwdm:
            stored = member.get("password")
            member["password"] = (
                stored is not None and not check_password(member["id"], stored)
            )
        else:
            member["password"] = None

    return {
        "total": count,
        "page": page,
        "perpage": perpage,
        "members": members,
    }


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

    # Get members from the group
    members = await backends.get_users_repo().list_by_class(
        str(validate_object_id(group_id))
    )

    members = [str(member["_id"]) for member in members]

    user_times = await calculate_user_time_bulk(members, allow_cache=True)

    member_time_counts = []

    on_campus = defaultdict(int)
    off_campus = defaultdict(int)
    social_practice = defaultdict(int)

    # Percentage of compliance, 0%–20%, 20%–40%, 40%–60%, 60%–80%, 80%–100%, completed.

    for member in members:
        user_time = user_times[member]
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

    return {
        "on-campus": sorted_on_campus,
        "off-campus": sorted_off_campus,
        "social-practice": sorted_social_practice,
    }
