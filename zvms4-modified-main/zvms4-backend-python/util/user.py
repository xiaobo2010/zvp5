from datetime import datetime

from db import backends
from util.ban import ban_is_active, ban_reason_of, ban_until_epoch
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

    ``banned`` 是**有效封禁**（走 :func:`util.ban.ban_is_active`），不是列值：
    到期封禁的 ``banned`` 列仍是 1，直接透传会让管理页永远显示「封禁中」。
    封禁详情只在有效时出现——未封禁账号的字段集合保持与历史响应逐字同形，
    不给所有读用户端点引入无意义的 null。
    """
    class_id = str(user["class_id"]) if user.get("class_id") else ""
    banned = ban_is_active(user)
    profile = {
        "_id": str(user["_id"]),
        "id": user["id"],
        "name": user["name"],
        "sex": user.get("sex", "unknown"),
        "roles": user.get("roles", []),
        "class_id": class_id,
        "group": [class_id] if class_id else [],
        "past": user.get("past", []),
        "banned": banned,
    }
    if banned:
        until = user.get("ban_until")
        profile["ban_reason"] = ban_reason_of(user)
        profile["ban_until"] = (
            until.isoformat() if isinstance(until, datetime) else None
        )
        profile["ban_source"] = user.get("ban_source")
        profile["ban_rule"] = user.get("ban_rule")
        # 永久封禁 = 没有到期时间；前端靠这一位决定显示「永久」而不是空白。
        profile["ban_permanent"] = ban_until_epoch(user) is None
    return profile


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
