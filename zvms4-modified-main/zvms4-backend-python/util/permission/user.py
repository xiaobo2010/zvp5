from typing import Literal, Optional

from fastapi import HTTPException

from db import backends
from db.ids import is_valid_oid
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
    user: dict,
    target_group: str,
    applied: Literal["volunteer", "club"] = "volunteer",
    allow_class: bool = False,
):
    """
    Validate that the current user may read the target group's information.

    - admin/department: always allowed (unchanged).
    - ``allow_class=True`` and the target is a **class group** (any group that
      is not a permission group): any authenticated user is allowed. Callers
      must opt in explicitly — the default keeps the read restricted.
    - secretary: allowed only for their own class (unchanged).
    - otherwise: 403.

    A malformed ``target_group`` never raises ``InvalidOid``: the class-group
    rule is skipped and the request falls through to the same 403 as today.
    """
    per = user.get("per") or []
    if "admin" in per or "department" in per:
        return True
    if allow_class and is_valid_oid(target_group):
        # Class groups are exactly the non-permission groups: the class side of
        # the groups table carries no ``type`` marker, and the repo exposes the
        # permission side through ``get_permission_group``.
        if await backends.get_groups_repo().get_permission_group(target_group) is None:
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