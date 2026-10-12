from typing import Literal, Optional

from fastapi import HTTPException

from db import backends
from util.object_id import validate_object_id


async def get_zvms_new_user(oid: str) -> Optional[dict]:
    """
    Fetch a user document from the live `zvms_new.users` collection.
    """
    return await backends.get_users_repo().get_by_oid(str(validate_object_id(oid)))


async def validate_read_user_permission(
    user: dict, target_user: str, applied: Literal["volunteer", "club"] = "volunteer"
):
    """
    Validate that the current user may read the target user's information.

    Permission vocabulary is the raw JWT `per` list (student/secretary/
    department/admin, issued from `zvms_new.users.roles`):
    - self / admin / department: allowed
    - secretary: allowed only when in the same class as the target
    """
    if user["id"] == target_user:
        return True
    per = user.get("per") or []
    if "admin" in per or "department" in per:
        return True
    if "secretary" in per:
        current = await get_zvms_new_user(user["id"])
        target = await get_zvms_new_user(target_user)
        if (
            current is not None
            and target is not None
            and str(current.get("class_id")) == str(target.get("class_id"))
        ):
            return True
    raise HTTPException(status_code=403, detail="权限不足")


async def validate_read_group_permission(
    user: dict, target_group: str, applied: Literal["volunteer", "club"] = "volunteer"
):
    """
    Validate that the current user may read the target group's information.

    admin/department always; secretary only for their own class.
    """
    per = user.get("per") or []
    if "admin" in per or "department" in per:
        return True
    if "secretary" in per:
        current = await get_zvms_new_user(user["id"])
        if (
            current is not None
            and current.get("class_id") is not None
            and str(current["class_id"]) == target_group
        ):
            return True
    raise HTTPException(status_code=403, detail="权限不足")