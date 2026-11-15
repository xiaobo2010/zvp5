from fastapi import HTTPException
from db import backends
from util.oid import validate_object_id


async def get_user_class(user_oid: str) -> str:
    validate_object_id(user_oid)
    user = await backends.get_users_repo().get_by_oid(user_oid)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    class_id = user.get("class_id")
    if not class_id:
        raise HTTPException(status_code=404, detail="User not in any class")

    grouping = await backends.get_groups_repo().get(class_id)
    if grouping is None:
        raise HTTPException(status_code=404, detail="User not in any class")

    return class_id


async def get_activities_related_to_user(
    user_oid: str, page: int = -1, perpage: int = 10, query: str = "", target: str = ""
):
    if not target:
        target = await get_user_class(user_oid)
    class_oid = validate_object_id(str(target))

    members = await backends.get_users_repo().list_by_class(class_oid)
    member_ids = [str(member["_id"]) for member in members]

    memberships = await backends.get_members_repo().list_by_members(member_ids)

    activity_ids = list({membership["activity"] for membership in memberships})

    activities, count = await backends.get_activities_repo().list_by_oids(
        activity_ids, query, page, perpage
    )

    members_by_activity: dict[str, list] = {}
    for membership in memberships:
        members_by_activity.setdefault(str(membership["activity"]), []).append(
            {
                "_id": str(membership["member"]),
                "status": membership.get("status"),
                "mode": membership.get("mode"),
            }
        )

    for activity in activities:
        activity["_id"] = str(activity["_id"])
        activity["members"] = members_by_activity.get(activity["_id"], [])

    return activities, count


async def get_user_classname(user_id: str):
    validate_object_id(user_id)
    user = await backends.get_users_repo().get_by_oid(user_id)
    if not user:
        return HTTPException(status_code=404, detail="User not found")

    class_id = user.get("class_id")
    if not class_id:
        return HTTPException(status_code=404, detail="User not in any class")

    class_name = await backends.get_groups_repo().get(class_id)
    if not class_name:
        return HTTPException(status_code=404, detail="User not in any class")

    return class_name["name"]


async def get_classname(user: dict, groups: list[dict]):
    if user is None:
        return None

    class_id = user.get("class_id")
    if not class_id:
        return None

    if groups is None:
        groups = await backends.get_groups_repo().list_all()

    class_id = str(class_id)
    for group in groups:
        if str(group["_id"]) == class_id:
            return group["name"]

    return None
