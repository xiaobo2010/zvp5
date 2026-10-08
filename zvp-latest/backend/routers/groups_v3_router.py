import re
from collections import defaultdict
from typing import Optional

from bson import ObjectId
from fastapi import APIRouter, Depends, Query

from database import db
from util.get_class import get_user_class
from util.object_id import get_current_user, validate_object_id
from util.permission.user import validate_read_group_permission
from util.cert import check_password
from util.get_user import get_user

from asyncio import gather

router = APIRouter()

@router.get('/{group_id}/activities')
async def get_group_activities_v3(
    group_id: str,
    page: int = Query(1),
    size: int = Query(10),
    query: str = Query(''),
    user=Depends(get_user)
):
    if not user.require('department', 'admin') and not (
        'secretary' in user.roles
        and str(user.class_id) == group_id
    ):
        raise HTTPException(status_code=403, detail="权限不足")
    
    pipeline = [
        {
            "$match": {
                "class_id": validate_object_id(group_id)
            }
        },
        {
            "$project": {
                "user": {
                    "$toString": "$_id"
                }
            }
        },
        {
            "$lookup":{
                "from": "activity_members",
                "localField": "user",
                "foreignField": "member",
                "as": "member"
            }
        },
        {
            "$unwind": "$member"
        },
        {
            "$group": {
                "_id": "$member.activity",
                "member": {
                    "$first": "$member"
                }
            }
        },
        {
            "$project": {
                "activity": {
                    "$convert": {
                        "input": "$member.activity",
                        "to": "objectId",
                        "onError": None,
                        "onNull": None
                    }
                }
            }
        },
        {
            "$match": {
                "activity": {
                    "$ne": None
                }
            }
        },
        {
            "$lookup": {
                "from": "activities",
                "localField": "activity",
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
                "_id": {
                    "$toString": "$act._id"
                },
                "type": "$act.type",
                "name": "$act.name",
                "description": "$act.description",
                "status": "$act.status",
                "date": "$act.date",
                "createdAt": "$act.createdAt",
                "updatedAt": "$act.updatedAt",
                "creator": "$act.creator",
                "creator_name": "$creator_obj.name"
            }
        }
    ]
    
    count_pipeline = pipeline[:10] + [
        {
            "$count": "total"
        }
    ]
    
    total, result = await gather(
        db.zvms_new.users.aggregate(count_pipeline).to_list(length=1),
        db.zvms_new.users.aggregate(pipeline).to_list(None)
    )
    
    return {
        "total": total[0]["total"] if len(total) else 0,
        "activities": result
    }


@router.get('/{group_id}/users')
async def get_group_users_v3(
    group_id: str,
    page: int = Query(1),
    size: int = Query(10),
    query: str = Query(''),
    user=Depends(get_user)
):
    if not user.require('department', 'admin') and not (
        'secretary' in user.roles
        and str(user.class_id) == group_id
    ):
        raise HTTPException(
            status_code=403,
            detail='权限不足'
        )
    
    escaped = re.escape(query)
    
    pipeline = [
        {
            "$match": {
                "class_id": validate_object_id(group_id),
                "$or": [
                    {
                        "name": {
                            "$regex": escaped,
                            "$options": "i"
                        }
                    },
                    {
                        "id": {
                            "$regex": escaped,
                            "$options": "i"
                        }
                    }
                ]
            }
        },
        {
            "$sort": {
                "id": 1
            }
        },
        {
            "$skip": (page - 1) * size
        },
        {
            "$limit": size
        },
        {
            "$lookup": {
                "from": "groups",
                "localField": "class_id",
                "foreignField": "_id",
                "as": "class_obj"
            }
        },
        {
            "$unwind": {
                "path": "$class_obj",
                "preserveNullAndEmptyArrays": True
            }
        },
        {
            "$set": {
                "class_obj": {
                    "$ifNull": [
                        "$class_obj",
                        {
                            "name": "未知班级"
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
                "id": "$id",
                "name": "$name",
                "roles": "$roles",
                "class_id": {
                    "$convert": {
                        "input": "$class_id",
                        "to": "string",
                        "onError": "",
                        "onNull": ""
                    }
                },
                "class_name": "$class_obj.name"
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
        db.zvms_new.users.aggregate(count_pipeline).to_list(length=1),
        db.zvms_new.users.aggregate(pipeline).to_list(None)
    )
    
    return {
        "data": result,
        "total": total[0]["total"] if len(total) else 0
    }

