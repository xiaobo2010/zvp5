from fastapi import HTTPException


async def user_activity_signoff():
    raise HTTPException(
        status_code=410,
        detail="Deprecated, please upgrade your client, or use /api/v2/activities/{activity_oid}/members instead.",
    )
