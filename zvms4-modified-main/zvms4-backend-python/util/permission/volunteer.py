from fastapi import HTTPException
from typings.activity_v2 import Activity


async def validate_create_permission(user: dict, hybrid: bool):
    if "admin" in user.get("per", []) or "department" in user.get("per", []):
        return True

    raise HTTPException(status_code=403, detail="Permission denied")


async def validate_update_permission(user: dict, activity: Activity) -> bool:
    if "admin" in user.get("per", []) or "department" in user.get("per", []):
        return True
    
    raise HTTPException(status_code=403, detail="Permission denied")


async def validate_check_permission(
    user: dict, activity: Activity, strict: bool = False
) -> bool:
    if "admin" in user.get("per", []) or "department" in user.get("per", []):
        return True
    raise HTTPException(status_code=403, detail="Permission denied")
