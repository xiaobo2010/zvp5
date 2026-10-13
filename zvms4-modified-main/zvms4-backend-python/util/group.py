from db import backends


async def get_user_permissions(user: dict):
    """
    Get user's permissions
    """
    roles = user.get("roles", [])
    if not roles:
        return ["student"]
    return roles


async def is_in_a_same_class(user: str, another_user: str):
    """
    Check if two users are in the same class
    """
    # Get each user's class via `class_id`
    user_doc = await backends.get_users_repo().get_by_oid(user)
    another_doc = await backends.get_users_repo().get_by_oid(another_user)

    user_class = user_doc.get("class_id") if user_doc else None
    another_class = another_doc.get("class_id") if another_doc else None

    if user_class and another_class:
        return user_class == another_class
    return None
