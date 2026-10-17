from db import backends
from utils import validate_object_id


def public_user(user: dict) -> dict:
    """Explicit API projection; repository-only credentials never cross it."""
    fields = {
        "_id",
        "id",
        "name",
        "roles",
        "class_id",
        "group",
        "sex",
        "banned",
        "password",
    }
    result = {key: value for key, value in user.items() if key in fields}
    # Callers may deliberately expose a password-state boolean. Never let an
    # unprojected repository hash survive this final response boundary.
    if "password" in result and not isinstance(result["password"], bool):
        result["password"] = None
    return result


def user_profile(user: dict) -> dict:
    """用户摘要，与 v1 ``read_user`` 的 ``user_info`` 逐字段同形。

    ``password`` / ``session_id`` 这类仅存储层字段不在白名单里，绝不外泄。
    """
    class_id = str(user["class_id"]) if user.get("class_id") else ""
    return {
        "_id": str(user["_id"]),
        "id": user["id"],
        "name": user["name"],
        "sex": user.get("sex", "unknown"),
        "roles": user.get("roles", []),
        "class_id": class_id,
        "group": [class_id] if class_id else [],
        "past": user.get("past", []),
        "banned": bool(user.get("banned")),
    }


async def get_user_name(id: str):
    try:
        oid = str(validate_object_id(id))
        names = await backends.get_users_repo().get_names([oid])
        return names[oid]
    except Exception:
        return "Unknown"


async def get_user_display_names(ids: list[str]) -> dict[str, str]:
    """
    Resolve display names for a batch of user ObjectIds.

    Looks up `zvms_new.users` (the single source of truth); unknown ids fall
    back to "未知用户".
    """
    result: dict[str, str] = {}
    oids: dict[str, str] = {}
    for uid in ids:
        try:
            oids[uid] = str(validate_object_id(uid))
        except Exception:
            result[uid] = "未知用户"
    if oids:
        found = await backends.get_users_repo().get_names(list(oids.values()))
        for uid, oid in oids.items():
            result[uid] = found.get(oid) or "未知用户"
    return result


async def get_user_display_name(id: str) -> str:
    return (await get_user_display_names([id])).get(id, "未知用户")
