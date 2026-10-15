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
    if not query:
        return {"status": "ok", "code": 200, "data": [], "metadata": {"size": 0}}

    # Narrow the searchable population by role: admin/department see everyone,
    # a secretary only their own class, a student only themselves.
    per = user.get("per") or []
    sort_field = sortkey.get(sort, sort)
    time_repo = backends.get_time_repo()

    if "admin" in per or "department" in per:
        user_times, count = await time_repo.list_by_search(
            query,
            user is not None,
            sort_field,
            not asc,
            page,
            perpage,
        )
    else:
        caller = await backends.get_users_repo().get_by_oid(user["id"])
        if caller is None:
            return {"status": "ok", "code": 200, "data": [], "metadata": {"size": 0}}
        if "secretary" in per and caller.get("class_id") is not None:
            user_times, count = await time_repo.list_by_class(
                str(caller["class_id"]),
                query,
                sort_field,
                not asc,
                page,
                perpage,
            )
        else:
            # Students (and a secretary with no class) are pinned to their own
            # business ID; `logged_in=False` matches `id` exactly.
            user_times, count = await time_repo.list_by_search(
                str(caller["id"]),
                False,
                sort_field,
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
