from typing import Optional
from fastapi import Depends, APIRouter, Query

from typings.time import UserActivityTime
from util.calculate import calculate_user_time
from util.object_id import (
    optional_current_user,
    validate_object_id,
    get_current_user,
)
from db import backends

router = APIRouter()


@router.get("")
async def read_times(
    query: str = Query("", description="Search query for user name or ID"),
    page: int = Query(1, ge=1, description="Page number for pagination"),
    perpage: int = Query(5, ge=1, le=100, description="Number of items per page"),
    sort: str = Query("id", description="Sort by field"),
    asc: bool = Query(True, description="Sort in ascending order"),
    user=Depends(get_current_user),
):
    """
    Query users
    """
    sortkey = {
        "on-campus": "on_campus_raw",
        "off-campus": "off_campus_raw",
        "social-practice": "social_practice",
    }
    result, count = await backends.get_users_repo().search(query, user is not None)
    if sort == "id":
        result = sorted(result, key=lambda x: x["id"], reverse=not asc)
    selected_students = [str(user["_id"]) for user in result]

    user_times = await backends.get_time_repo().list_by_users(
        selected_students,
        sort_field=sortkey.get(sort, sort),
        descending=not asc,
        page=page,
        size=perpage,
    )
    results = []

    for user in user_times:
        user_info = await backends.get_users_repo().get_by_oid(
            validate_object_id(user["user"])
        )
        time_struct = UserActivityTime.model_validate(user, strict=False)
        results.append(
            {
                "_id": str(user["user"]),
                "name": user_info["name"],
                "id": user_info["id"],
                "on-campus": time_struct.on_campus,
                "off-campus": time_struct.off_campus,
                "social-practice": time_struct.social_practice,
            }
        )

    return {"status": "ok", "code": 200, "data": results, "metadata": {"size": count}}
