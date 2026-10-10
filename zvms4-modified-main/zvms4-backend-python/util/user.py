from database import db
from utils import validate_object_id


async def get_user_name(id: str):
    try:
        user = await db.zvms.users.find_one(
            {"_id": validate_object_id(id)}, {"name": True}
        )
        return user["name"]
    except Exception:
        return "Unknown"


async def get_user_display_names(ids: list[str]) -> dict[str, str]:
    """
    Resolve display names for a batch of user ObjectIds.

    Lookup order: `zvms_new.users` (live) first, legacy `zvms.users` as
    fallback, then "未知用户".
    """
    result: dict[str, str] = {}
    oids: dict[str, object] = {}
    for uid in ids:
        try:
            oids[uid] = validate_object_id(uid)
        except Exception:
            result[uid] = "未知用户"
    if oids:
        docs = await db.zvms_new.users.find(
            {"_id": {"$in": list(oids.values())}}, {"name": 1}
        ).to_list(None)
        found = {str(doc["_id"]): doc.get("name") for doc in docs}
        missing = [oid for uid, oid in oids.items() if not found.get(uid)]
        legacy: dict[str, str] = {}
        if missing:
            legacy_docs = await db.zvms.users.find(
                {"_id": {"$in": missing}}, {"name": 1}
            ).to_list(None)
            legacy = {str(doc["_id"]): doc.get("name") for doc in legacy_docs}
        for uid in oids:
            result[uid] = found.get(uid) or legacy.get(uid) or "未知用户"
    return result


async def get_user_display_name(id: str) -> str:
    return (await get_user_display_names([id])).get(id, "未知用户")