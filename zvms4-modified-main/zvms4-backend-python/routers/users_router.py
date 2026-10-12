import re
from datetime import datetime
from typing import Optional, Any
from fastapi import APIRouter, HTTPException, Depends, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from bcrypt import hashpw, gensalt
from routers.activities_router import user_activity_signoff
from typings.log import inject_log, ZVMSLog
from typings.user import User
from util.calculate import calculate_user_time
from util.group import is_in_a_same_class
from util.object_id import (
    compulsory_temporary_token,
    get_current_user,
    validate_object_id,
    string_to_option_object_id,
    optional_current_user,
)
from database import db
from util.cert import (
    get_hashed_password_by_cert,
    used_otp_credential,
    validate_by_cert,
)
from util.user import get_user_display_name, get_user_name
from util.validation import (
    validate_number,
    validate_student_name,
)

router = APIRouter()


class AuthUser(BaseModel):
    id: str
    mode: str
    credential: str


@router.post("/auth")
async def auth_user(auth: AuthUser, request: Request):
    id = auth.id
    mode = auth.mode or "long"
    credential = auth.credential

    if string_to_option_object_id(id) is None:
        user_doc = await db.zvms_new.users.find_one({"id": id})
        if user_doc:
            id = str(user_doc["_id"])
        else:
            users = await db.zvms.users.find({"id": id}).to_list(None)
            if len(users) != 1:
                raise HTTPException(
                    status_code=404,
                    detail="User not found",
                )
            id = str(users[0]["_id"])

    result, weak_password = await validate_by_cert(id, credential, mode)

    user_doc = await db.zvms_new.users.find_one({"_id": validate_object_id(id)})
    log_name = user_doc["name"] if user_doc else await get_user_name(id)
    is_otp = used_otp_credential(credential, id)
    if is_otp:
        log_text = f"用户 {log_name} 使用动态码登录了系统"
    elif weak_password and mode == "long":
        log_text = f"用户 {log_name} 登录了系统（密码强度不足，仅发放临时授权）"
    else:
        log_text = f"用户 {log_name} 登录了系统"
    log = ZVMSLog(
        str(request.url),
        id,
        log_text,
        datetime.timestamp(datetime.now()),
        type="user_login",
    )

    await log.insert_log()

    profile = None
    if user_doc:
        profile = {
            "_id": id,
            "id": user_doc["id"],
            "name": user_doc["name"],
            "roles": user_doc.get("roles", []),
            "class_id": str(user_doc["class_id"]) if user_doc.get("class_id") else "",
            "group": [str(user_doc["class_id"])] if user_doc.get("class_id") else [],
        }

    return {
        "token": result,
        "_id": id,
        "profile": profile,
        "weak_password": weak_password,
    }


@router.post("")
async def create_user(
    target_user: User,
    actioner=Depends(compulsory_temporary_token),
):
    raise HTTPException(
        status_code=503,
        detail="仅允许读取：用户创建请使用 POST /api/v3/users",
    )


@router.delete("/{target}")
async def delete_user(target: str):
    raise HTTPException(
        status_code=503,
        detail="仅允许读取：用户删除已停用",
    )


class PutPassword(BaseModel):
    credential: str


@router.put("/{user_oid}/password")
async def change_password(
    user_oid: str,
    credential: PutPassword,
    user=Depends(compulsory_temporary_token),
    log=Depends(inject_log),
):
    # Validate user's permission
    secretary = "secretary" in user["per"] and (
        is_in_a_same_class(user["id"], user_oid)
    )
    modification = user["id"] == user_oid and user["scope"] == "temporary_token"
    if (
        "admin" not in user["per"]
        and (not modification)
        and "department" not in user["per"]
        and "auditor" not in user["per"]
        and (not secretary)
    ):
        raise HTTPException(status_code=403, detail="Permission denied")

    password = await get_hashed_password_by_cert(credential.credential)

    target = await db.zvms_new.users.find_one({"_id": validate_object_id(user_oid)})
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")

    # Change user's password
    await db.zvms_new.users.update_one(
        {"_id": validate_object_id(user_oid)}, {"$set": {"password": str(password)}}
    )

    if modification:
        log.with_text(
            f"用户 {await get_user_display_name(user_oid)} 修改了自己的密码",
            type="passwd_change",
        )
    else:
        log.with_text(
            f"用户 {await get_user_display_name(user['id'])} 重置了用户 {await get_user_display_name(user_oid)} 的密码",
            type="passwd_change",
        )
    await log.insert_log()

    return {
        "status": "ok",
        "code": 200,
    }


@router.get("")
async def read_users(
    query: str = "",
    page: int = 1,
    perpage: int = 5,
    privilege: bool = False,
    user: Optional[str] = Depends(optional_current_user),
):
    """
    Query users
    """
    if privilege:
        groups = await db.zvms["groups"].find(
            {"permissions": {"$in": ["admin", "department"]}}
        )
        selected = []
        for group in groups:
            selected.append(str(group["_id"]))

    if user is not None:
        legacy_filter = {
            "$or": [
                {"name": {"$regex": query, "$options": "i"}},
                {"id": {"$regex": query, "$options": "i"}},
                {"past": {"$elemMatch": {"$regex": query, "$options": "i"}}},
            ]
        }
        new_filter = {
            "$or": [
                {"name": {"$regex": query, "$options": "i"}},
                {"id": {"$regex": query, "$options": "i"}},
            ]
        }
    else:
        legacy_filter = {"id": query}
        new_filter = {"id": query}

    legacy_users = (
        await db.zvms.users.find(
            legacy_filter,
            {
                "name": True,
                "id": True,
                "group": True,
            },
        )
        .sort({"id": 1})
        .to_list(None)
    )

    new_users = (
        await db.zvms_new.users.find(
            new_filter,
            {
                "name": True,
                "id": True,
                "class_id": True,
            },
        )
        .sort({"id": 1})
        .to_list(None)
    )

    merged: dict[str, dict] = {}
    for doc in legacy_users:
        merged[doc["id"]] = {
            "_id": str(doc["_id"]),
            "id": doc["id"],
            "name": doc["name"],
            "group": doc.get("group", []),
        }
    for doc in new_users:
        class_id = doc.get("class_id")
        merged[doc["id"]] = {
            "_id": str(doc["_id"]),
            "id": doc["id"],
            "name": doc["name"],
            "group": [str(class_id)] if class_id else [],
        }

    rows = sorted(merged.values(), key=lambda row: row["id"])
    count = len(rows)

    if page == -1:
        result = rows
    else:
        start = (page - 1) * perpage
        result = rows[start : start + perpage]

    return {"status": "ok", "code": 200, "data": result, "metadata": {"size": count}}


@router.get("/{user_oid}")
async def read_user(user_oid: str, user=Depends(get_current_user)):
    """
    Return user's information
    """
    # Read user's information
    user_info = await db.zvms.users.find_one({"_id": validate_object_id(user_oid)})

    if user_info is None:
        new_user = await db.zvms_new.users.find_one({"_id": validate_object_id(user_oid)})
        if new_user is not None:
            class_id = str(new_user["class_id"]) if new_user.get("class_id") else ""
            user_info = {
                "_id": new_user["_id"],
                "id": new_user["id"],
                "name": new_user["name"],
                "sex": new_user.get("sex", "unknown"),
                "roles": new_user.get("roles", []),
                "class_id": class_id,
                "group": [class_id] if class_id else [],
                "past": new_user.get("past", []),
            }
    else:
        new_user = await db.zvms_new.users.find_one({"id": user_info["id"]})
        if new_user is not None:
            user_info["roles"] = new_user.get("roles", [])

    if user_info is None:
        raise HTTPException(status_code=404, detail="User not found")

    user_info["_id"] = str(user_info["_id"])
    user_info.pop("password", None)
    return {
        "status": "ok",
        "code": 200,
        "data": user_info,
    }


class PutUser(BaseModel):
    name: str
    id: str
    group: list[str]


@router.put("/{user_oid}")
async def update_user(
    user_oid: str,
    user_struct: PutUser,
):
    raise HTTPException(
        status_code=503,
        detail="仅允许读取：用户信息修改已停用",
    )


@router.post("/{user_oid}/group")
async def add_user_to_group(user_oid: str, group_id: str):
    raise HTTPException(
        status_code=503,
        detail="仅允许读取：用户分组请使用 POST /api/v3/groups/{group_id}/users/{user_id}",
    )


@router.delete("/{user_oid}/group/{group_id}")
async def remove_user_from_group(user_oid: str, group_id: str):
    raise HTTPException(
        status_code=503,
        detail="仅允许读取：用户移出分组请使用 DELETE /api/v3/groups/{group_id}/users/{user_id}",
    )


@router.get("/{user_oid}/activities")
async def read_user_activity(
    user_oid: str,
    user=Depends(get_current_user),
    page: int = -1,
    perpage: int = 10,
    query: str = "",
):
    """
    Return user's activities
    """
    # Check user's permission

    if (
        "admin" not in user["per"]
        and "department" not in user["per"]
        and user["id"] != str(validate_object_id(user_oid))
    ):
        raise HTTPException(status_code=403, detail="Permission denied")

    if query != "" and "admin" not in user["per"]:
        query = re.escape(query)

    count = await db.zvms.activities.count_documents(
        {"members._id": str(validate_object_id(user_oid))},
        # {"name": {"$regex": query}},
    )
    # Read user's activities
    pipeline = [
        {
            "$match": {
                "members._id": user_oid,
                "name": {"$regex": query, "$options": "i"},
            }
        },
        {
            "$project": {
                "name": 1,
                "date": 1,
                "_id": 1,
                "status": 1,
                "type": 1,
                "special": 1,
                "members": {
                    "$filter": {
                        "input": "$members",
                        "as": "member",
                        "cond": {"$eq": ["$$member._id", user_oid]},
                    }
                },
            }
        },
        {"$sort": {"_id": -1}},
        {"$skip": 0 if page == -1 else (page - 1) * perpage},
    ]
    if page != -1:
        pipeline.append({"$limit": perpage})

    all_activities = await db.zvms.activities.aggregate(pipeline).to_list(
        None if page == -1 else perpage
    )

    for activity in all_activities:
        activity["_id"] = str(activity["_id"])

    return {
        "status": "ok",
        "code": 200,
        "data": all_activities,
        "metadata": {
            "size": count,
        },
    }


# Redirect
@router.api_route("/{user_oid}/activity/{path:path}", methods=["GET"])
async def redirect_activity(request: Request, user_oid: str, path: str):
    new_url = request.url.replace(path="/api/users/" + user_oid + "/activities/" + path)
    return RedirectResponse(url=new_url)


@router.get("/{user_oid}/time")
async def read_user_time(
    user_oid: str,
    start: Optional[str] = None,
    end: Optional[str] = None,
    user=Depends(get_current_user),
):
    """
    Return user's time
    """
    # Check user's permission
    if (
        "admin" not in user["per"]
        and "department" not in user["per"]
        and user["id"] != str(validate_object_id(user_oid))
    ):
        raise HTTPException(status_code=403, detail="Permission denied")

    if (start is not None and end is None) or (start is None and end is not None):
        raise HTTPException(status_code=400, detail="Invalid query")

    if start is not None and end is not None:
        result = await calculate_user_time(
            user_oid, datetime.fromisoformat(start), datetime.fromisoformat(end)
        )
    else:
        result = await calculate_user_time(user_oid)
    return {
        "status": "ok",
        "code": 200,
        "data": {
            "onCampus": result["on-campus"],
            "offCampus": result["off-campus"],
            "socialPractice": result["social-practice"],
            "total": result["total"],
        },
    }


@router.get("/{user_oid}/logs")
async def read_logs(
    user_oid: str,
    page: int = -1,
    perpage: int = 10,
    query: str = "",
    user=Depends(get_current_user),
):
    if "admin" not in user["per"]:
        raise HTTPException(status_code=403, detail="Permission denied")

    query: dict[str, Any] = (
        {"user": user_oid}
        if query == ""
        else {
            "$or": [{"url": {"$regex": query}}, {"data": {"$regex": query}}],
            "user": user_oid,
        }
    )

    count = await db.zvms.logs.count_documents(query)

    pipeline = [
        {"$match": query},
        {"$sort": {"timestamp": -1}},
        {"$skip": 0 if page == -1 else (page - 1) * perpage},
        {"$limit": perpage},
    ]

    logs = await db.zvms.logs.aggregate(pipeline).to_list(None)

    for log in logs:
        log["_id"] = str(log["_id"])

    return {
        "code": 200,
        "status": "ok",
        "data": logs,
        "metadata": {
            "size": count,
        },
    }


@router.delete("/{user_oid}/past/{past_identity_idx}")
async def delete_past(
    user_oid: str,
    past_identity_idx: str,
    user=Depends(get_current_user),
    log=Depends(inject_log),
):
    if "admin" not in user["per"]:
        raise HTTPException(status_code=403, detail="Permission denied")
    # Remove by index
    name = (await db.zvms.users.find_one({"_id": validate_object_id(user_oid)}))["past"]
    idx = int(past_identity_idx)
    if idx < 0 or idx >= len(name):
        raise HTTPException(status_code=404, detail="Past identity not found.")
    await db.zvms.users.update_one(
        {"_id": validate_object_id(user_oid)}, {"$pull": {"past": name[idx]}}
    )
    log.with_text(
        f"管理员 {await get_user_display_name(user['id'])} 删除了用户 {await get_user_display_name(user_oid)} 的历史身份 {name[idx]}",
        type="past_change",
    )
    await log.insert_log()
    return {"code": 200, "status": "ok"}


class PostPast(BaseModel):
    past: str


@router.post("/{user_oid}/past")
async def add_past(
    user_oid: str,
    past: PostPast,
    user=Depends(get_current_user),
    log=Depends(inject_log),
):
    if not validate_number(past.past)[0]:
        raise HTTPException(status_code=400, detail="Invalid past identity.")
    if "admin" not in user["per"]:
        raise HTTPException(status_code=403, detail="Permission denied")
    if (
        await db.zvms.users.count_documents(
            {"$or": [{"id": past.past}, {"past": {"$elemMatch": {"$eq": past.past}}}]}
        )
    ) > 0:
        raise HTTPException(status_code=409, detail="The past identity already exists.")
    await db.zvms.users.update_one(
        {"_id": validate_object_id(user_oid)}, {"$push": {"past": past.past}}
    )
    log.with_text(
        f"管理员 {await get_user_display_name(user['id'])} 添加了用户 {await get_user_display_name(user_oid)} 的历史身份 {past.past}",
        type="past_change",
    )
    await log.insert_log()
    return {"code": 200, "status": "ok"}


@router.get("/{user_oid}/notification")
async def read_notifications(
    user_oid: str, page: int = 1, perpage: int = 10, user=Depends(get_current_user)
):
    """
    Get Notifications
    """
    # Get a notification list
    count = await db.zvms.notifications.count_documents(
        {
            "$or": [
                {"receivers": str(user_oid)},
                {"global": True},
            ],
        }
    )
    notifications = (
        await db.zvms.notifications.find(
            {
                "$or": [
                    {"receivers": str(user_oid)},
                    {"global": True},
                    {"publisher": str(user_oid)},
                ],
            }
        )
        .sort("_id", -1)
        .skip(0 if page == -1 else (page - 1) * perpage)
        .limit(0 if page == -1 else perpage)
        .to_list(None if page == -1 else perpage)
    )

    if user_oid != user["id"]:
        raise HTTPException(status_code=403, detail="Permission denied")

    for notification in notifications:
        notification["_id"] = str(notification["_id"])
    return {
        "status": "ok",
        "code": 200,
        "data": notifications,
        "metadata": {
            "size": count,
        },
    }
