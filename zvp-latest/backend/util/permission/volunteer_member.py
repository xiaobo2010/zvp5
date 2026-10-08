from typings.activity_v2 import Activity
from typings.user import User
from util.get_class import get_user_class
from fastapi import HTTPException


async def validate_create_permission(
    user: dict, target_user: User, target_activity: Activity
):
    """
    Validate if the user has permission to create an activity for the target user.
    """
    if "admin" in user.get("per") or "department" in user.get("per"):
        return True

    raise HTTPException(
        status_code=403,
        detail="权限不足"
    )


async def validate_update_permission(user: dict):
    """
    Validate if the user has permission to update an activity member.
    """
    if "admin" in user.get("per") or "department" in user.get("per"):
        return True
    raise HTTPException(
        status_code=403,
        detail="权限不足"
    )
