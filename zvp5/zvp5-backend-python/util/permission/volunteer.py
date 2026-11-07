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


async def validate_delete_permission(user: dict, activity: Activity) -> bool:
    """删除活动的口径：创建人 OR admin OR department。

    与 `validate_update_permission` 分离，避免放宽读取/更新的既有口径；
    前端 `ZActivityDetails.vue` 允许创建人删除，此处对齐后端。
    """
    if "admin" in user.get("per", []) or "department" in user.get("per", []):
        return True

    creator = activity.creator
    if creator is not None and str(creator) == str(user.get("id", "")):
        return True

    raise HTTPException(status_code=403, detail="Permission denied")


async def validate_check_permission(
    user: dict, activity: Activity, strict: bool = False
) -> bool:
    if "admin" in user.get("per", []) or "department" in user.get("per", []):
        return True
    raise HTTPException(status_code=403, detail="Permission denied")
