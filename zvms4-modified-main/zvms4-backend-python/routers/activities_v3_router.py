from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Body, Query
from database import db
from typings.log import inject_log
from util.object_id import get_current_user
from util.user import get_user_name
from utils import validate_object_id
from typing import Literal
from asyncio import gather
import re
from pydantic import BaseModel, Field
from bson import ObjectId

router = APIRouter()


def _is_volunteer_manager(user: dict) -> bool:
    per = user.get("per") or []
    return "admin" in per or "department" in per


@router.get('')
async def get_activities_v3(
    page: int = Query(1, ge=1, description="Page number"),
    size: int = Query(10, ge=1, le=1000, description="Page size"),
    query: str = Query('', description="Search keyword"),
    type: Literal['on-campus', 'off-campus', 'social-practice', 'hybrid', ''] = Query('', description="Type of activity to filter"),
    status: Literal['effective', 'refused', 'pending', 'draft', ''] = Query(''),
    user=Depends(get_current_user)
):
    if not _is_volunteer_manager(user):
        raise HTTPException(
            status_code=403,
            detail='权限不足'
        )

    escaped = re.escape(query)

    filters = []

    if query:
        filters.append({
            "name": {
                "$regex": escaped,
                "$options": "i"
            }
        })

    if type:
        filters.append({
            "type": type
        })

    if status:
        filters.append({
            "status": status
        })

    pipeline = [
        {
            "$match": {
                "$and": filters
            } if len(filters) else {}
        },
        {
            "$sort": {
                "_id": -1
            }
        },
        {
            "$skip": (page - 1) * size
        },
        {
            "$limit": size
        },
        {
            "$addFields": {
                "creator_obj_id": {
                    "$convert": {
                        "input": "$creator",
                        "to": "objectId",
                        "onError": None,
                        "onNull": None
                    }
                }
            }
        },
        {
            "$lookup": {
                "from": "users",
                "localField": "creator_obj_id",
                "foreignField": "_id",
                "as": "creator_obj"
            }
        },
        {
            "$unwind": {
                "path": "$creator_obj",
                "preserveNullAndEmptyArrays": True
            }
        },
        {
            "$set": {
                "creator_obj": {
                    "$ifNull": [
                        "$creator_obj",
                        {
                            "name": "未知用户"
                        }
                    ]
                }
            }
        },
        {
            "$project": {
                "_id": {
                    "$toString": "$_id"
                },
                "type": "$type",
                "name": "$name",
                "description": "$description",
                "status": "$status",
                "date": "$date",
                "createdAt": "$createdAt",
                "updatedAt": "$updatedAt",
                "creator": "$creator",
                "creator_name": "$creator_obj.name"
            }
        }
    ]

    count_pipeline = [
        pipeline[0],
        {
            "$count": "total"
        }
    ]

    total, result = await gather(
        db.zvms_new.get_collection("activities").aggregate(count_pipeline).to_list(length=1),
        db.zvms_new.get_collection("activities").aggregate(pipeline).to_list(None)
    )

    return {
        "total": total[0]["total"] if len(total) else 0,
        "activities": result
    }


class CreateActivityRequest(BaseModel):
    name: str
    description: str
    type: Literal['on-campus', 'off-campus', 'social-practice', 'hybrid']


@router.post('', status_code=201)
async def create_activity_v3(
    payload: CreateActivityRequest,
    user=Depends(get_current_user),
    log=Depends(inject_log)
):
    if not _is_volunteer_manager(user):
        raise HTTPException(
            status_code=403,
            detail='权限不足'
        )

    oid = ObjectId()
    now = datetime.now()

    await db.zvms_new.get_collection("activities").insert_one({
        "_id": oid,
        "name": payload.name,
        "description": payload.description,
        "type": payload.type,
        "creator": user["id"],
        "status": "pending",
        "date": now,
        "createdAt": now,
        "updatedAt": now,
        "appointee": user["id"],
        "approver": "",
        "place": "",
        "origin": "activities",
    })

    log.with_text(
        f'用户 {await get_user_name(user["id"])} 创建了义工 {payload.name} ({oid})'
    )
    await log.insert_log()

    return {
        "id": str(oid)
    }


@router.get('/{activity_id}/members')
async def get_activity_members_v3(
    activity_id: str,
    page: int = Query(1),
    size: int = Query(10),
    query: str = Query(''),
    user=Depends(get_current_user)
):
    """
    Get activity members (ULTRA FAST impl)
    """

    escaped = re.escape(query)

    validate_object_id(activity_id)

    pipeline = [
        {
            "$match": {
                "activity": activity_id
            }
        },
        {
            "$addFields": {
                "member_id": {
                    "$convert": {
                        "input": "$member",
                        "to": "objectId",
                        "onError": None,
                        "onNull": None
                    }
                }
            }
        },
        {
            "$lookup": {
                "from": "users",
                "localField": "member_id",
                "foreignField": "_id",
                "as": "member_obj"
            }
        },
        {
            "$unwind": {
                "path": "$member_obj",
                "preserveNullAndEmptyArrays": True
            }
        },
        {
            "$match": {
                "$and": [
                    {
                        "member_obj": {
                            "$ne": None
                        }
                    },
                    {
                        "member_obj.name": {
                            "$regex": escaped,
                            "$options": "i"
                        }
                    }
                ]
            } if query else {}
        },
        {
            "$sort": {
                "_id": 1
            }
        },
        {
            "$skip": (page - 1) * size
        },
        {
            "$limit": size
        },
        {
            "$set": {
                "member_obj": {
                    "$ifNull": [
                        "$member_obj",
                        {
                            "name": "未知用户"
                        }
                    ]
                }
            }
        },
        {
            "$project": {
                "_id": {
                    "$toString": "$_id"
                },
                "activity": "$activity",
                "member": "$member",
                "member_name": "$member_obj.name",
                "mode": "$mode",
                "status": "$status",
                "duration": "$duration"
            }
        }
    ]

    count_pipeline = pipeline[:5] + [
        {
            "$count": "total"
        }
    ]

    total, result = await gather(
        db.zvms_new.get_collection("activity_members").aggregate(count_pipeline).to_list(length=1),
        db.zvms_new.get_collection("activity_members").aggregate(pipeline).to_list(None)
    )

    return {
        "members": result,
        "total": total[0]["total"] if len(total) else 0
    }


class NewActivityMember(BaseModel):
    member: str
    mode: Literal['on-campus', 'off-campus', 'social-practice']
    duration: float = Field(gt=0)


@router.post("/{activity_id}/members", status_code=204)
async def add_activity_member_v3(
    activity_id: str,
    members: list[NewActivityMember] = Body(),
    user=Depends(get_current_user),
    log=Depends(inject_log),
):
    """
    :param activity_id: ID of the activity to be retrieved
    :param user: Current user
    :param member: ActivityMember object to be added
    :param log: Logger object
    :return: Activity object
    """

    if not _is_volunteer_manager(user):
        raise HTTPException(status_code=403, detail="权限不足")

    target_activity = await db.zvms_new.get_collection("activities").find_one(
        {"_id": validate_object_id(activity_id)}
    )

    if not target_activity:
        raise HTTPException(status_code=404, detail="Activity not found")

    results = []

    log_text = ''

    for member in members:
        if target_activity["type"] != "hybrid" and target_activity["type"] != member.mode:
            raise HTTPException(
                status_code=400, detail="义工类型不匹配"
            )

    for member in members:

        existing: dict = await db.zvms_new.get_collection("activity_members").find_one(
            {
                "member": str(member.member),
                "activity": str(activity_id),
                "mode": member.mode,
            }
        )

        if existing:

            updated_data = {
                "duration": existing["duration"] + member.duration
            }

            result = await db.zvms_new.get_collection("activity_members").update_one(
                {"_id": existing["_id"]},
                {"$set": updated_data},
            )

            log_text += f'User {await get_user_name(user["id"])} updated member {await get_user_name(member.member)} in activity {target_activity["name"]}\n'

            continue

        member_ = member.model_dump()

        result = await db.zvms_new.get_collection("activity_members").insert_one(member_ | {
            "activity": activity_id,
            "status": "effective",
        })

        log_text += f'User {await get_user_name(user["id"])} added member {await get_user_name(member.member)} to activity {target_activity["name"]}\n'

    if len(log_text):
        log.with_text(log_text[:-1])
        await log.insert_log()

    result = await db.zvms_new.get_collection("activities").update_one(
        {"_id": validate_object_id(activity_id)},
        {
            "$set": {
                "status": "pending",
                "updatedAt": datetime.now(),
            }
        },
    )
