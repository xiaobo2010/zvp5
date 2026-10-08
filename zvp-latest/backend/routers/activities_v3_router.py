from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Body, Query
from database import db
from typings.log import inject_log
from typings.user import User as UserV1
from util.object_id import get_current_user
from util.permission import volunteer_member
from util.user import get_user_name
from util.get_user import get_user
from util.log import APILogger
from utils import validate_object_id
from typing import Literal
from asyncio import gather
import re
from pydantic import BaseModel, Field
from bson import ObjectId

router = APIRouter()

type ActivityType = Literal['on-campus', 'off-campus', 'social-practice', 'hybrid', '']
type ActivityStatus = Literal['effective', 'refused', 'pending', 'draft', '']

@router.get('')
async def get_activities_v3(
    page: int = Query(1, ge=1, description="Page number"),
    size: int = Query(10, ge=1, le=1000, description="Page size"),
    query: str = Query('', description="Search keyword"),
    type: ActivityType = Query('', description="Type of activity to filter"),
    status: ActivityStatus = Query(''),
    user=Depends(get_user)
):
    if not user.require('department', 'admin'):
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
        db.zvms_new.activities.aggregate(count_pipeline).to_list(length=1),
        db.zvms_new.activities.aggregate(pipeline).to_list(None)
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
    user=Depends(get_user),
    logger=Depends(APILogger.with_user)
):
    if not user.require('admin', 'department'):
        raise HTTPException(
            status_code=403,
            detail='权限不足'
        )
    
    oid = ObjectId()
    timestamp = int(datetime.now().timestamp() * 1000)
    
    await db.zvms_new.activities.insert_one({
        "_id": oid,
        **payload.model_dump(),
        "creator": str(user._id),
        "status": "pending",
        "date": timestamp,
        "createdAt": timestamp,
        "updatedAt": timestamp,
    })
    
    await logger.log(
        f'用户 {user.name} 创建了义工 {payload.name} ({oid})',
        level='warning' if payload.type == 'hybrid' else 'info',
        type='activity_create'
    )
    
    return {
        "id": str(oid)
    }


@router.get('/{activity_id}/members')
async def get_activity_members_v3(
    activity_id: str,
    page: int = Query(1),
    size: int = Query(10),
    query: str = Query(''),
    user=Depends(get_user)
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
        db.zvms_new.activity_members.aggregate(count_pipeline).to_list(length=1),
        db.zvms_new.activity_members.aggregate(pipeline).to_list(None)
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
    user=Depends(get_user),
    log=Depends(inject_log),
):
    """
    :param activity_id: ID of the activity to be retrieved
    :param user: Current user
    :param member: ActivityMember object to be added
    :param log: Logger object
    :return: Activity object
    """
    
    if not user.require('admin', 'department'):
        raise HTTPException(status_code=403,detail="权限不足")

    target_activity = await db.zvms_new.activities.find_one(
        {"_id": validate_object_id(activity_id)}
    )

    results = []

    log_text = ''

    for member in members:

        if target_activity["type"] != "hybrid" and target_activity["type"] != member.mode:
            raise HTTPException(
                status_code=400, detail="义工类型不匹配"
            )
    
    for member in members:
        
        existing: dict = await db.zvms_new.activity_members.find_one(
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
        
        log_text += f'User {user.name} added member {await get_user_name(member.member)} to activity {target_activity["name"]}\n'

    if len(log_text):
        log.with_text(log_text[:-1])
        await log.insert_log()
    
    result = await db.zvms_new.get_collection("activities").update_one(
        {"_id": validate_object_id(activity_id)},
        {
            "$set": {
                "status": "pending",
                "updatedAt": int(datetime.now().timestamp() * 1000),
            }
        },
    )

