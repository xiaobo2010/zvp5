import re
from collections import defaultdict
from typing import Optional

from bson import ObjectId
from fastapi import APIRouter, Depends, Query, HTTPException

from config import (
    BASE_OFF_CAMPUS,
    ON_TO_OFF_RATE,
    BASE_ON_CAMPUS,
    OFF_TO_ON_RATE,
    MAX_EXCEED_DISCOUNT,
    BASE_SOCIAL_PRACTICE,
)
from database import db
from typings.time import UserActivityTime
from util.calculate import calculate_user_time
from util.get_class import get_user_class
from util.object_id import get_current_user, validate_object_id
from util.permission.user import validate_read_group_permission
from util.cert import check_password
from util.get_user import get_user
from asyncio import gather

router = APIRouter()


@router.get("")
async def get_groups_v2(
    page: int = Query(1),
    size: int = Query(10),
    query: str = Query(''),
    user=Depends(get_user)
):
    if not user.require('admin', 'department'):
        raise HTTPException(
            status_code=403,
            detail='权限不足'
        )
    
    pipeline = [
        {
            "$match": {
                "name": {
                    "$regex": re.escape(query),
                    "$options": "i"
                }
            }
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
            "$project": {
                "_id": {
                    "$toString": "$_id"
                },
                "name": "$name",
                "enrollYear": "$enrollYear"
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
        db.zvms_new.groups.aggregate(count_pipeline).to_list(length=1),
        db.zvms_new.groups.aggregate(pipeline).to_list(None)
    )
    
    return {
        "data": result,
        "total": total[0]["total"] if len(total) else 0
    }


@router.get("/{group_id}")
async def get_group_v2(
    group_id: str,
    user=Depends(get_user)
):
    if group_id != str(user.class_id) and not user.require('admin', 'department'):
        raise HTTPException(
            status_code=403,
            detail="权限不足"
        )
    
    result = await db.zvms_new.groups.find_one({
        "_id": validate_object_id(group_id)
    })
    
    if not result:
        raise HTTPException(
            status_code=404,
            detail='班级不存在'
        )
    
    result["_id"] = str(result["_id"])
    
    return result


@router.get("/{group_id}/activities")
async def get_group_activities_v2(
    group_id: str,
    page: int = Query(1, ge=1, description="Page number for pagination"),
    perpage: int = Query(10, ge=1, le=100, description="Number of items per page"),
    search: str = Query("", description="Search keyword"),
    sort: str = Query("_id", description="Sort by field"),
    regex: bool = Query(True, description="Use regex for search"),
    asc: bool = Query(False, description="Sort in ascending order"),
    user=Depends(get_current_user),
):
    """
    Get group activities
    :param group_id: Group ID
    :param page: The page number
    :param perpage: Items per page
    :param search: Search keyword
    :param sort: Sort by field
    :param regex: Use regex for search
    :param asc: Sort in ascending order
    :param user: Current user
    """

    await validate_read_group_permission(user, group_id, "volunteer")

    # Get members from the group
    members = (
        await db.zvms.get_collection("users")
        .find(
            {
                "group": group_id  # TODO should be `groups` in the new structure
            }
        )
        .to_list(None)
    )

    members = [str(member["_id"]) for member in members]

    activity_members = (
        await db.zvms_new.get_collection("activity_members")
        .find({"member": {"$in": members}})
        .to_list(None)
    )

    activity_members = [
        ObjectId(activity_member["activity"]) for activity_member in activity_members
    ]

    if not regex:
        search = re.escape(search) if search else ""

    activities_filter = (
        {
            "$and": [
                {
                    "$or": [
                        {"name": {"$regex": search, "$options": "i"}},
                        {"description": {"$regex": search, "$options": "i"}},
                    ]
                },
                {"_id": {"$in": activity_members}},
            ]
        }
        if search
        else {"_id": {"$in": activity_members}}
    )

    count = await db.zvms_new.get_collection("activities").count_documents(
        activities_filter
    )

    pipeline = [
        {"$match": activities_filter},
        {"$sort": {sort: 1 if asc else -1}},
        {"$skip": (page - 1) * perpage},
        {"$limit": perpage},
    ]

    activities = (
        await db.zvms_new.get_collection("activities").aggregate(pipeline).to_list(None)
    )

    # Convert ObjectId to string
    for activity in activities:
        activity["_id"] = str(activity["_id"])

    return {
        "total": count,
        "page": page,
        "perpage": perpage,
        "activities": activities,
    }


@router.get("/{group_id}/time")
async def read_users(
    group_id: str,
    query: str = Query("", description="Search query for user name or ID"),
    page: int = Query(1, ge=-1, description="Page number for pagination"),
    perpage: int = Query(5, ge=1, le=100, description="Number of items per page"),
    allow_cache: bool = Query(True, description="Allow cached data"),
    sort: str = Query("id", description="Sort by field"),
    asc: bool = Query(True, description="Sort in ascending order"),
    user: Optional[str] = Depends(get_current_user),
):
    """
    Query users
    """
    sortkey = {
        "on-campus": "on_campus_raw",
        "off-campus": "off_campus_raw",
        "social-practice": "social_practice",
    }
    count = await db.zvms.users.count_documents(
        {
            "$or": [
                {"name": {"$regex": query, "$options": "i"}},
                {"id": {"$regex": query, "$options": "i"}},
                {"past": {"$elemMatch": {"$regex": query, "$options": "i"}}},
            ],
            "group": group_id,
        }
    )
    result = (
        await db.zvms.users
        .find(
            {
                "$or": [
                    {"name": {"$regex": query, "$options": "i"}},
                    {"id": {"$regex": query, "$options": "i"}},
                    {"past": {"$elemMatch": {"$regex": query, "$options": "i"}}},
                ],
                "group": group_id,
            },
            {
                "name": True,
                "id": True,
                "group": True,
            },
        )
        .to_list(None)
    )
    if sort == "id":
        result = sorted(result, key=lambda x: x["id"], reverse=not asc)
    selected_students = [str(user["_id"]) for user in result]

    user_times = (
        await db.zvms_new.get_collection("time")
        .find({"user": {"$in": selected_students}})
        .sort({sortkey.get(sort, sort): -1 if not asc else 1} if sort else None)
        .skip((page - 1) * perpage)
        .limit(perpage)
        .to_list(None)
    )
    results = []

    for user in user_times:
        user_info = await db.zvms.get_collection("users").find_one(
            {"_id": validate_object_id(user["user"])}
        )
        time_struct = UserActivityTime.model_validate(user, strict=False)
        results.append(
            {
                "_id": str(user["user"]),
                "name": user_info["name"],
                "id": user_info["id"],
                "on-campus": time_struct.on_campus,
                "off-campus": time_struct.off_campus,
                "social-practice": time_struct.social_practice,
            }
        )

    return {"status": "ok", "code": 200, "data": results, "metadata": {"size": count}}


@router.get("/{group_id}/users")
async def get_group_users_v2(
    group_id: str,
    page: int = Query(1, ge=1, description="Page number for pagination"),
    perpage: int = Query(10, ge=1, le=100, description="Number of items per page"),
    search: str = Query("", description="Search keyword"),
    sort: str = Query("id", description="Sort by field"),
    regex: bool = Query(True, description="Use regex for search"),
    asc: bool = Query(True, description="Sort in ascending order"),
    exceeding: bool = Query(True, description="Include exceeding time"),
    shortage: bool = Query(False, description="Include shortage time"),
    pwdm: bool = Query(False, description="Include password mode information"),
    start: Optional[str] = Query(None, description="Start date filter"),
    end: Optional[str] = Query(None, description="End date filter"),
    user=Depends(get_current_user),
):
    """
    Get group time
    :param group_id: Group ID
    :param page: The page number
    :param perpage: Items per page
    :param search: Search keyword
    :param sort: Sort by field
    :param regex: Use regex for search
    :param asc: Sort in ascending order
    :param user: Current user
    :param exceeding: Exceeding time
    :param shortage: Shortage time
    :param pwdm: Include password mode information
    :param start: Start date
    :param end: End date
    """

    # Get members from the group
    members = (
        await db.zvms.get_collection("users")
        .aggregate(
            [
                {
                    "$match": {
                        "$or": [
                            {"name": {"$regex": search, "$options": "i"}},
                            {"id": {"$regex": search, "$options": "i"}},
                            {"past": {"$elemMatch": {"$regex": search, "$options": "i"}}},
                        ],
                        "group": group_id  # TODO should be `groups` in the new structure
                    }
                },
                {"$sort": {sort: 1 if asc else -1}},
                {"$skip": (page - 1) * perpage},
                {"$limit": perpage},
            ]
        )
        .to_list(None)
    )

    count = await db.zvms.get_collection("users").count_documents(
        {
            "$or": [
                {"name": {"$regex": search, "$options": "i"}},
                {"id": {"$regex": search, "$options": "i"}},
                {"past": {"$elemMatch": {"$regex": search, "$options": "i"}}},
            ],
            "group": group_id  # TODO should be `groups` in the new structure
        }
    )

    for member in members:
        member["_id"] = str(member["_id"])
        if pwdm:
            member["password"] = not check_password(member["id"], member["password"])
        else:
            member["password"] = None

    return {
        "total": count,
        "page": page,
        "perpage": perpage,
        "members": members,
    }


