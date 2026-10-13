import re
from fastapi import APIRouter, Depends, HTTPException, Query
from db import backends
from util.calculate import calculate_user_time, find_percentile_threshold
from util.get_class import get_user_class
from util.object_id import get_current_user
from util.permission.user import validate_read_user_permission
from utils import validate_object_id

router = APIRouter()


@router.get("")
async def get_users_v2(
    page: int = Query(1, ge=1, description="Page number"),
    size: int = Query(10, ge=1, le=1000, description="Page size"),
    query: str = Query("", description="Search keyword"),
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
    search: str = Query("", description="Search keyword"),
    sort: str = Query("_id", description="Sort by field"),
    regex: bool = Query(True, description="Use regex for search"),
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

    if not regex:
        search = re.escape(search) if search else ""

    activities_repo = backends.get_activities_repo()
    members_repo = backends.get_members_repo()

    # Enumerate the activities matching the search, then keep only the
    # requester's memberships pointing at them (mirrors the Mongo pipeline).
    selected_activities, _ = await activities_repo.list_raw(search="", page=None)
    if search:
        pattern = re.compile(search, re.IGNORECASE)
        selected_activities = [
            activity
            for activity in selected_activities
            if pattern.search(activity.get("name") or "")
            or pattern.search(activity.get("description") or "")
        ]
    activity_ids = [str(activity["_id"]) for activity in selected_activities]

    memberships = await members_repo.list_for_member(user_id, None, activity_ids)
    if sort:
        memberships.sort(
            key=lambda membership: (
                membership.get(sort) is None,
                membership.get(sort),
            ),
            reverse=not asc,
        )

    count = len(memberships)

    start = (page - 1) * perpage
    memberships = memberships[start : start + perpage]

    activities = []

    for membership in memberships:
        activity = await activities_repo.get(membership["activity"])
        if activity is None:
            continue
        mine = {key: value for key, value in membership.items() if key != "activity"}
        mine["_id"] = str(mine["_id"])
        activity["mine"] = mine
        activity["_id"] = str(activity["_id"])
        activities.append(activity)

    return {
        "page": page,
        "perpage": perpage,
        "total": count,
        "activities": activities,
    }


@router.get("/{user_id}/time")
async def get_user_time_v2(
    user_id: str, user=Depends(get_current_user), allow_cache: bool = False
):
    """
    Get user time

    :param user_id: User ID
    :param user: Current user
    :param allow_cache: If True, do not use cached data

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
    group_id = await get_user_class(user_id)
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
    user_document = await backends.get_users_repo().get_by_oid(
        str(validate_object_id(user_id))
    )
    grade_indicators = indicators["percentiles"][user_document["id"][:4]]
    user_time = await calculate_user_time(user_id, allow_cache=True)
    group_indicators = group_row["percentiles"]
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
