import re
from fastapi import APIRouter, HTTPException, Depends, Request
from fastapi.responses import RedirectResponse
from typings.log import inject_log
from util.user import get_user_display_name
from util.object_id import (
    get_current_user,
    validate_object_id,
)
from datetime import datetime
from database import db
from db import backends
from pydantic import BaseModel
from typings.activity_v2 import Activity
from util.permission import volunteer
from util.validation import validate_activity_name

router = APIRouter()


@router.post("")
async def create_activity():
    raise HTTPException(
        status_code=410,
        detail="Deprecated, please upgrade your client, or use /api/v2/activities instead.",
    )


class PutDescription(BaseModel):
    description: str


@router.put("/{activity_oid}/description")
async def change_activity_description(
    activity_oid: str,
    payload: PutDescription,
    user=Depends(get_current_user),
    log=Depends(inject_log),
):
    """
    Edit activity description
    """

    description = payload.description
    # Check permission
    if user["id"] != validate_object_id(activity_oid) and "admin" not in user["per"]:
        raise HTTPException(status_code=403, detail="Permission denied")

    # Edit activity description
    await backends.get_activities_repo().update(
        activity_oid,
        {
            "description": description,
            "updatedAt": datetime.now(),
        },
    )

    log.with_text(
        f"用户 {await get_user_display_name(user['id'])} 修改了义工活动 {activity_oid} 的描述",
        type="activity_update",
    )
    await log.insert_log()

    return {
        "status": "ok",
        "code": 200,
    }


class PutActivityName(BaseModel):
    name: str


@router.put("/{activity_oid}/name")
async def change_activity_title(
    activity_oid: str,
    payload: PutActivityName,
    user=Depends(get_current_user),
    log=Depends(inject_log),
):
    """
    Modify Activity Title
    """
    if not validate_activity_name(payload.name)[0]:
        raise HTTPException(
            status_code=400, detail=validate_activity_name(payload.name)[1]
        )

    name = payload.name
    # Check permission
    if user["id"] != validate_object_id(activity_oid) and "admin" not in user["per"]:
        raise HTTPException(status_code=403, detail="Permission denied")

    # Edit activity title
    await backends.get_activities_repo().update(
        activity_oid, {"name": name, "updatedAt": datetime.now()}
    )

    log.with_text(
        f"用户 {await get_user_display_name(user['id'])} 将义工活动 {activity_oid} 的名称修改为 {name}",
        type="activity_update",
    )
    await log.insert_log()

    return {
        "status": "ok",
        "code": 200,
    }


class PutActivityStatus(BaseModel):
    status: str


@router.put("/{activity_oid}/status")
async def change_activity_status(
    activity_oid: str,
    payload: PutActivityStatus,
    user=Depends(get_current_user),
    log=Depends(inject_log),
):
    """
    Modify activity status
    """

    status = payload.status

    target_activity = await backends.get_activities_repo().get(activity_oid)

    if not target_activity:
        raise HTTPException(status_code=404, detail="Activity not found")

    # Check user permission (mirrors /api/v2/activities/{id}/status semantics)
    activity = Activity.model_validate(target_activity, strict=False)
    await volunteer.validate_check_permission(user, activity, strict=True)

    # Update activity status
    await backends.get_activities_repo().update(
        activity_oid, {"status": status, "updatedAt": datetime.now()}
    )

    status_labels = {"effective": "生效", "pending": "待审核", "refused": "已驳回"}
    log.with_text(
        f"用户 {await get_user_display_name(user['id'])} 将义工活动 {activity_oid} 的状态修改为 {status_labels.get(payload.status, payload.status)}",
        type="activity_update",
    )
    await log.insert_log()

    return {
        "status": "ok",
        "code": 200,
    }


@router.get("")
async def read_activities(
    type: str | None,
    mode: str | None,
    page: int = -1,
    perpage: int = 10,
    query: str = "",
    user=Depends(get_current_user),
):
    """
    Return activities
    """
    if query != "" and "admin" not in user["per"]:
        query = re.escape(query)

    # User permission check
    if "admin" not in user["per"] and "department" not in user["per"]:
        raise HTTPException(status_code=403, detail="Permission denied")
    if type is None or type == "all" or type == "":
        target_types = ["specified", "social", "scale", "special"]
    else:
        target_types = type.split(",")
    if len(target_types) == 0:
        target_types = ["specified", "social", "scale", "special"]

    audit = "admin" in user["per"]

    pipeline = [
        {
            "$match": {
                "name": {"$regex": query, "$options": "i"},
                "type": {"$in": target_types},
            }
        },
        {
            "$project": {
                "name": True,
                "status": True,
                "date": True,
                "type": True,
                "special": True,
                "approver": True,
                "members": {
                    "$filter": {
                        "input": "$members",
                        "as": "member",
                        "cond": {
                            "$or": [
                                {
                                    "$eq": [
                                        "$$member.status",
                                        "" if not audit else "pending",
                                    ]
                                },
                            ]
                        },
                    }
                },
            }
        },
        {
            "$project": {
                "name": True,
                "status": True,
                "date": True,
                "type": True,
                "special": True,
                "members._id": True,
                "members.status": True,
            }
        },
        {
            "$addFields": {
                "pendingCount": {
                    "$size": {
                        "$filter": {
                            "input": "$members",
                            "as": "member",
                            "cond": {"$eq": ["$$member.status", "pending"]},
                        }
                    }
                }
            }
        },
        {"$sort": {"pendingCount": -1, "_id": -1}},
        {"$skip": 0 if page == -1 else (page - 1) * perpage},
        {"$limit": 0 if page == -1 else perpage},
    ]

    count = await db.zvms_new.get_collection("activities").count_documents(
        {"name": {"$regex": query, "$options": "i"}, "type": {"$in": target_types}}
    )
    activities = (
        await db.zvms_new.get_collection("activities").aggregate(pipeline).to_list(None)
    )
    for activity in activities:
        activity["_id"] = str(activity["_id"])
    return {
        "status": "ok",
        "code": 200,
        "data": activities,
        "metadata": {"size": count},
    }


@router.get("/{activity_oid}")
async def read_activity(activity_oid: str, user=Depends(get_current_user)):
    """
    Return activity
    """
    # Read activity
    pipeline = [
        {
            "$match": {
                "_id": validate_object_id(activity_oid),
            }
        },
        {
            "$addFields": {
                "members": {
                    "$map": {
                        "input": "$members",
                        "as": "member",
                        "in": {
                            "$mergeObjects": [
                                "$$member",
                                {
                                    "sortKey": {
                                        "$switch": {
                                            "branches": [
                                                {
                                                    "case": {
                                                        "$eq": [
                                                            "$$member.status",
                                                            "pending",
                                                        ]
                                                    },
                                                    "then": 0,
                                                },
                                                {
                                                    "case": {
                                                        "$eq": [
                                                            "$$member.status",
                                                            "rejected",
                                                        ]
                                                    },
                                                    "then": 1,
                                                },
                                                {
                                                    "case": {
                                                        "$eq": [
                                                            "$$member.status",
                                                            "refused",
                                                        ]
                                                    },
                                                    "then": 2,
                                                },
                                                {
                                                    "case": {
                                                        "$eq": [
                                                            "$$member.status",
                                                            "effective",
                                                        ]
                                                    },
                                                    "then": 3,
                                                },
                                                {
                                                    "case": {
                                                        "$eq": [
                                                            "$$member.status",
                                                            "draft",
                                                        ]
                                                    },
                                                    "then": 4,
                                                },
                                            ],
                                            "default": 5,  # handle unexpected statuses
                                        }
                                    }
                                },
                            ]
                        },
                    }
                }
            }
        },
        {
            "$set": {
                "members": {
                    "$sortArray": {"input": "$members", "sortBy": {"sortKey": 1}}
                }
            }
        },
        {
            "$project": {
                "members": {
                    "$map": {
                        "input": "$members",
                        "as": "member",
                        "in": {
                            "_id": "$$member._id",
                            "status": "$$member.status",
                            "duration": "$$member.duration",
                            "mode": "$$member.mode",
                        },
                    }
                },
                "name": True,
                "description": True,
                "status": True,
                "date": True,
                "type": True,
                "special": True,
                "creator": True,
                "createdAt": True,
                "updatedAt": True,
                "registration": True,
            }
        },
    ]
    activity = (
        await db.zvms_new.get_collection("activities").aggregate(pipeline).to_list(None)
    )
    activity = activity[0]

    if not activity:
        raise HTTPException(status_code=404, detail="Activity not found")

    activity["_id"] = str(activity["_id"])
    return {"status": "ok", "code": 200, "data": activity}


class PutActivityDuration(BaseModel):
    duration: float


@router.put("/{activity_oid}/members/{uid}/duration")
async def update_activity_member_duration(
    activity_oid: str,
    uid: str,
    payload: PutActivityDuration,
    user=Depends(get_current_user),
    log=Depends(inject_log),
):
    """
    Update activity member duration
    """

    activity = await backends.get_activities_repo().get(activity_oid)

    if not activity:
        raise HTTPException(status_code=404, detail="Activity not found")

    if "admin" not in user["per"] and "department" not in user["per"]:
        raise HTTPException(status_code=403, detail="Permission denied")

    await backends.get_members_repo().set_duration_for_member(
        activity_oid, uid, payload.duration
    )

    log.with_text(
        f"用户 {await get_user_display_name(user['id'])} 修改了义工活动 {activity_oid} 中成员 {await get_user_display_name(uid)} 的时长为 {payload.duration}",
        type="activity_member_update",
    )
    await log.insert_log()

    return {
        "status": "ok",
        "code": 200,
    }


# Redirect


@router.post("/{activity_oid}/member")
async def user_activity_signup():
    raise HTTPException(
        status_code=410,
        detail="Deprecated, please upgrade your client, or use /api/v2/activities/{activity_oid}/members instead.",
    )


@router.get("/{activity_oid}/members/{uid}")
async def read_activity_user(
    activity_oid: str, uid: str, user=Depends(get_current_user)
):
    raise HTTPException(
        status_code=410,
        detail="Deprecated, please upgrade your client, or use /api/v2/activities/{activity_oid}/members instead.",
    )


@router.delete("/{activity_oid}/members/{uid}")
async def user_activity_signoff():
    raise HTTPException(
        status_code=410,
        detail="Deprecated, please upgrade your client, or use /api/v2/activities/{activity_oid}/members instead.",
    )


@router.delete("/{activity_oid}")
async def delete_activity():
    raise HTTPException(
        status_code=410,
        detail="Deprecated, please upgrade your client, or use /api/v2/activities/{activity_oid} instead.",
    )


@router.api_route(
    "/{activity_oid}/member/{path:path}", methods=["GET", "POST", "PUT", "DELETE"]
)
async def redirect_activity_member(request: Request, activity_oid: str, path: str):
    new_url = request.url.replace(
        path=f"/api/activities/{activity_oid}/members/" + path
    )
    return RedirectResponse(url=new_url)
