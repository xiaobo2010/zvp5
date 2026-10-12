from fastapi import HTTPException
from database import db
from utils import validate_object_id


async def get_user_class(user_oid: str) -> str:
    user = await db.zvms_new.users.find_one({"_id": validate_object_id(user_oid)})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    class_id = user.get("class_id")
    if not class_id:
        raise HTTPException(status_code=404, detail="User not in any class")

    grouping = await db.zvms_new.groups.find_one({"_id": class_id})
    if grouping is None:
        raise HTTPException(status_code=404, detail="User not in any class")

    return class_id


async def get_activities_related_to_user(
    user_oid: str, page: int = -1, perpage: int = 10, query: str = "", target: str = ""
):
    if not target:
        target = await get_user_class(user_oid)
    class_oid = validate_object_id(str(target))

    members = (
        await db.zvms_new.users.find({"class_id": class_oid}, {"_id": 1}).to_list(None)
    )
    member_ids = [str(member["_id"]) for member in members]

    memberships = (
        await db.zvms_new.activity_members.find(
            {"member": {"$in": member_ids}},
            {"activity": 1, "member": 1, "status": 1, "mode": 1},
        ).to_list(None)
    )

    activity_ids = list({membership["activity"] for membership in memberships})
    activity_oids = [validate_object_id(activity_id) for activity_id in activity_ids]

    activity_filter = {
        "_id": {"$in": activity_oids},
        "name": {"$regex": query, "$options": "i"},
    }

    count = await db.zvms_new.activities.count_documents(activity_filter)

    cursor = db.zvms_new.activities.find(activity_filter).sort("_id", -1)
    if page != -1:
        cursor = cursor.skip((page - 1) * perpage).limit(perpage)

    activities = await cursor.to_list(None)

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
    user = await db.zvms_new.users.find_one({"_id": validate_object_id(user_id)})
    if not user:
        return HTTPException(status_code=404, detail="User not found")

    class_id = user.get("class_id")
    if not class_id:
        return HTTPException(status_code=404, detail="User not in any class")

    class_name = await db.zvms_new.groups.find_one({"_id": class_id})
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
        groups = await db.zvms_new.groups.find().to_list(None)

    class_id = str(class_id)
    for group in groups:
        if str(group["_id"]) == class_id:
            return group["name"]

    return None
