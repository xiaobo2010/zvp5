from fastapi import APIRouter, Depends, HTTPException, Query
from db import backends
from util.calculate import calculate_user_time, find_percentile_threshold
from util.object_id import get_current_user
from util.permission.user import validate_read_user_permission
from util.oid import validate_object_id

router = APIRouter()


@router.get("")
async def get_users_v2(
    page: int = Query(1, ge=1, description="Page number"),
    size: int = Query(10, ge=1, le=1000, description="Page size"),
    query: str = Query("", max_length=64, description="Search keyword"),
    role: str = Query(""),
    user=Depends(get_current_user),
):
    """
    List users from the live `zvms_new.users` collection.

    Permission: admin (when filtering by role) or admin/department otherwise.
    """
    per = user.get("per") or []
    if role:
        if "admin" not in per:
            raise HTTPException(status_code=403, detail="权限不足")
    elif not ("admin" in per or "department" in per):
        raise HTTPException(status_code=403, detail="权限不足")

    repo = backends.get_users_repo()
    rows, total = await repo.list_page(query, role, page, size)

    for row in rows:
        row.pop("has_password", None)
        row["group"] = [row["class_id"]] if row["class_id"] else []

    return {
        "data": rows,
        "total": total,
    }


@router.get("/{user_id}/activities")
async def get_user_activities_v2(
    user_id: str,
    page: int = Query(1, ge=1, description="Page number for pagination"),
    perpage: int = Query(10, ge=1, le=100, description="Number of items per page"),
    search: str = Query("", max_length=64, description="Search keyword"),
    sort: str = Query("_id", description="Sort by field"),
    regex: bool = Query(False, description="已废弃：关键字一律按字面量匹配，不再走正则"),
    asc: bool = Query(False, description="Sort in ascending order"),
    user=Depends(get_current_user),
):
    """
    Get user activities
    :param user_id: User ID
    :param page: The page number
    :param perpage: Items per page
    :param search: Search keyword
    :param sort: Sort by field
    :param regex: Use regex for search
    :param asc: Sort in ascending order
    :param user: Current user
    """
    await validate_read_user_permission(user, user_id, "volunteer")

    # Check if the user has permission to view the activities
    # skipped right now

    # 关键字是字面量（仓储侧转义 ``%``/``_``/``\\`` 并 lower 比较）；``regex``
    # 仅为兼容旧客户端保留。
    rows, total = await backends.get_members_repo().list_for_user_activities(
        user_id, search, sort, asc, page, perpage
    )

    activities = []

    for row in rows:
        activity = row["activity"]
        if activity is None:
            continue
        mine = row["member"]
        mine.pop("activity")
        mine["_id"] = str(mine["_id"])
        activity["mine"] = mine
        activity["_id"] = str(activity["_id"])
        activities.append(activity)

    return {
        "page": page,
        "perpage": perpage,
        "total": total,
        "activities": activities,
    }


@router.get("/{user_id}/time")
async def get_user_time_v2(
    user_id: str, user=Depends(get_current_user), allow_cache: bool = True
):
    """
    Get user time

    :param user_id: User ID
    :param user: Current user
    :param allow_cache: If False, recompute and refresh the cache instead of reading it

    :return: User time
    """
    await validate_read_user_permission(user, user_id, "volunteer")

    result = await calculate_user_time(user_id, allow_cache=allow_cache)

    return result


@router.get("/{user_id}/time_statistics")
async def get_user_time_statistics_v2(
    user_id: str, user=Depends(get_current_user)
):
    """
    Get user time statistics, particularly `percentiles`.

    :param user_id: User ID
    :param user: Current user

    :return: User time statistics
    """
    await validate_read_user_permission(user, user_id, "volunteer")

    user_oid = str(validate_object_id(user_id))
    user_document = await backends.get_users_repo().get_by_oid(user_oid)
    if user_document is None:
        raise HTTPException(status_code=404, detail="User not found")
    group_id = user_document.get("class_id")
    if not group_id:
        raise HTTPException(status_code=404, detail="User not in any class")
    if await backends.get_groups_repo().get(group_id) is None:
        raise HTTPException(status_code=404, detail="User not in any class")
    stats_repo = backends.get_stats_repo()
    indicators = await stats_repo.get_indicators()
    group_rows = await stats_repo.list_group_indicators()
    group_row = next(
        (row for row in group_rows if str(row.get("group")) == str(group_id)),
        None,
    )
    if indicators is None or group_row is None:
        raise HTTPException(
            status_code=400, detail="Function temporarily unaccessible."
        )
    percentiles = indicators.get("percentiles")
    grade_indicators = (
        percentiles.get(user_document["id"][:4])
        if isinstance(percentiles, dict)
        else None
    )
    group_indicators = group_row.get("percentiles")
    if not isinstance(grade_indicators, dict) or not isinstance(
        group_indicators, dict
    ):
        raise HTTPException(
            status_code=400, detail="Statistics are not available for this cohort."
        )
    user_time = await calculate_user_time(user_id, allow_cache=True)
    return {
        "on-campus": {
            "value": user_time["on-campus"],
            "group": find_percentile_threshold(
                group_indicators["on-campus"], user_time["on-campus"], "on-campus"
            ),
            "grade": find_percentile_threshold(
                grade_indicators["on-campus"], user_time["on-campus"], "on-campus"
            ),
        },
        "off-campus": {
            "value": user_time["off-campus"],
            "group": find_percentile_threshold(
                group_indicators["off-campus"], user_time["off-campus"], "off-campus"
            ),
            "grade": find_percentile_threshold(
                grade_indicators["off-campus"], user_time["off-campus"], "off-campus"
            ),
        },
        "social-practice": {
            "value": user_time["social-practice"],
            "group": find_percentile_threshold(
                group_indicators["social-practice"],
                user_time["social-practice"],
                "social-practice",
            ),
            "grade": find_percentile_threshold(
                grade_indicators["social-practice"],
                user_time["social-practice"],
                "social-practice",
            ),
        },
    }
