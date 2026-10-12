import re
from typing import Literal, Optional

from bcrypt import hashpw, gensalt
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from database import db
from typings.log import inject_log
from util.object_id import get_current_user
from util.permission.user import validate_read_user_permission
from utils import validate_object_id
from util.get_user import get_user
from util.validation import validate_number, validate_student_name
from asyncio import gather
from util.log import APILogger

router = APIRouter()

@router.get("/{user_id}/activities")
async def get_user_activities_v3(
    user_id: str,
    page: int = Query(1, ge=1, description="Page number"),
    size: int = Query(1, ge=1, le=100, description="Page size"),
    query: str = Query("", description="Search keyword"),
    user=Depends(get_user),
):
    """
    Get user activities (faster implementation)
    """
    
    validate_object_id(user_id)
    
    if user_id != str(user._id) and not user.require('secretary', 'department', 'admin'):
        raise HTTPException(
            status_code=403, detail='权限不足，禁止访问他人信息'
        )
    
    pipeline = [
        {
            "$match": {
                "member": user_id
            }
        },
        {
            "$addFields": {
                "act_obj_id": {
                    "$convert": {
                        "input": "$activity",
                        "to": "objectId",
                        "onError": None,
                        "onNull": None
                    }
                }
            }
        },
        {
            "$match": {
                "act_obj_id": {
                    "$ne": None
                }
            }
        },
        {
            "$lookup": {
                "from": "activities",
                "localField": "act_obj_id",
                "foreignField": "_id",
                "as": "act"
            }
        },
        {
            "$unwind": "$act"
        },
        {
            "$match": {
                "act.name": {
                    "$regex": re.escape(query),
                    "$options": "i"
                }
            } if query else {}
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
                        "input": "$act.creator",
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
                "type": "$act.type",
                "name": "$act.name",
                "description": "$act.description",
                "date": "$act.date",
                "createdAt": "$act.createdAt",
                "updatedAt": "$act.updatedAt",
                "creator": "$act.creator",
                "creator_name": "$creator_obj.name",
                "mine._id": {
                    "$toString": "$_id"
                },
                "mine.member": "$member",
                "mine.activity": "$activity",
                "mine.status": "$status",
                "mine.mode": "$mode",
                "mine.duration": "$duration",
                "status": "$act.status",
                "_id": "$activity"
            }
        }
    ]
    
    count_pipeline = pipeline[:6] + [
        {
            "$count": "total"
        }
    ]
    
    total, result = await gather(
        db.zvms_new.activity_members.aggregate(count_pipeline).to_list(length=1),
        db.zvms_new.activity_members.aggregate(pipeline).to_list(None)
    )
    
    
    return {
        "total": total[0]["total"] if total else 0,
        "activities": result
    }

@router.get("/{user_id}/time")
async def get_user_time_v3(
    user_id: str,
    user=Depends(get_user),
    logger=Depends(APILogger.with_user),
):
    """
    Get user time (faster implementation)

    :param user_id: User ID
    :param user: Current user

    :return: User time
    """
    
    validate_object_id(user_id)
    
    if user_id != str(user._id) and not user.require('admin', 'department', 'secretary'):
        raise HTTPException(
            status_code=403,
            detail='权限不足，禁止访问他人信息'
        )
    
    pipeline = [
        {
            "$match": {
                "member": user_id,
                "status": "effective"
            }
        },
        {
            "$addFields": {
                "act_obj_id": {
                    "$convert": {
                        "input": "$activity",
                        "to": "objectId",
                        "onError": None,
                        "onNull": None
                    }
                }
            }
        },
        {
            "$match": {
                "act_obj_id": {
                    "$ne": None
                }
            }
        },
        {
            "$lookup": {
                "from": "activities",
                "localField": "act_obj_id",
                "foreignField": "_id",
                "as": "act"
            }
        },
        {
            "$unwind": "$act"
        },
        {
            "$match": {
                "act.status": "effective"
            }
        },
        {
            "$group": {
                "_id": "$mode",
                "time": {
                    "$sum": "$duration"
                }
            }
        }
    ]
    
    result = await db.zvms_new.get_collection("activity_members").aggregate(pipeline).to_list(None)
    
    result_dict = {x["_id"]: x["time"] for x in result}
    
    await logger.log(
        f'用户 {user.name} 读取义工时间',
        level='verbose',
        type='request_log'
    )
    
    return {
        "on-campus": result_dict.get("on-campus", 0),
        "off-campus": result_dict.get("off-campus", 0),
        "social-practice": result_dict.get("social-practice", 0),
        "raw": result_dict
    }


AllowedRole = Literal['student', 'secretary', 'department', 'admin', 'teacher']


class PutRolesRequest(BaseModel):
    roles: list[AllowedRole]


@router.put('/{user_id}/roles', status_code=204)
async def modify_user_roles_v3(
    user_id: str,
    payload: PutRolesRequest,
    user=Depends(get_user),
    log=Depends(inject_log),
):
    if not user.require('admin'):
        raise HTTPException(
            status_code=403, detail='权限不足'
        )

    oid = validate_object_id(user_id)

    target = await db.zvms_new.users.find_one({
        "_id": oid
    })

    if not target:
        raise HTTPException(
            status_code=404, detail='用户不存在'
        )

    await db.zvms_new.users.update_one(
        {
            "_id": oid
        },
        {
            "$set": {
                "roles": payload.roles
            }
        }
    )

    log.with_text(
        f'User {user.name} updated roles of {target["name"]} to {payload.roles}'
    )
    await log.insert_log()

    return


class NewUserRequest(BaseModel):
    id: str
    name: str
    roles: list[str] = Field(default_factory=lambda: ['student'])
    class_id: Optional[str] = None


@router.post('', status_code=201)
async def create_user_v3(
    payload: NewUserRequest,
    user=Depends(get_user),
    log=Depends(inject_log),
):
    if not user.require('admin'):
        raise HTTPException(status_code=403, detail='权限不足')

    allowed_roles = {'student', 'secretary', 'department', 'admin', 'teacher'}
    for role in payload.roles:
        if role not in allowed_roles:
            raise HTTPException(status_code=400, detail=f'无效角色：{role}')

    valid, message = validate_number(payload.id)
    if not valid:
        raise HTTPException(status_code=400, detail=message)

    valid, message = validate_student_name(payload.name)
    if not valid:
        raise HTTPException(status_code=400, detail=message)

    if await db.zvms_new.users.find_one({"id": payload.id}):
        raise HTTPException(status_code=409, detail='学号已存在')

    class_oid = None
    if payload.class_id is not None:
        class_oid = validate_object_id(payload.class_id)
        if not await db.zvms_new.groups.find_one({"_id": class_oid}):
            raise HTTPException(status_code=404, detail='班级不存在')

    password = hashpw(payload.id.encode('utf-8'), gensalt()).decode('utf-8')

    document = {
        "id": payload.id,
        "name": payload.name,
        "password": password,
        "roles": payload.roles,
    }
    if class_oid is not None:
        document["class_id"] = class_oid

    result = await db.zvms_new.users.insert_one(document)

    log.with_text(f'User {user.name} created user {payload.name} ({payload.id})')
    await log.insert_log()

    return {"id": str(result.inserted_id)}

