import re
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, HTTPException, Depends, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from bcrypt import hashpw, gensalt
from util.activity_signoff import user_activity_signoff
from typings.log import inject_log, ZVMSLog, xuehai_id_from
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
from db import backends
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
        user_doc = await backends.get_users_repo().find_by_business_id(id)
        if user_doc:
            id = str(user_doc["_id"])
        else:
            raise HTTPException(
                status_code=404,
                detail="User not found",
            )

    result, weak_password = await validate_by_cert(id, credential, mode)

    user_doc = await backends.get_users_repo().get_by_oid(
        str(validate_object_id(id))
    )
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
        xuehai_id=xuehai_id_from(request),
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

    repo = backends.get_users_repo()
    oid = str(validate_object_id(user_oid))
    target = await repo.get_by_oid(oid)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")

    # Change user's password
    await repo.set_password(oid, str(password))

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
        groups = await backends.get_groups_repo().list_permission_groups(
            permissions=["admin", "department"]
        )
        selected = []
        for group in groups:
            selected.append(str(group["_id"]))

    users, _ = await backends.get_users_repo().search(query, user is not None)

    rows = []
    for doc in users:
        class_id = doc.get("class_id")
        rows.append(
            {
                "_id": str(doc["_id"]),
                "id": doc["id"],
                "name": doc["name"],
                "group": [str(class_id)] if class_id else [],
            }
        )

    rows.sort(key=lambda row: row["id"])
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
    new_user = await backends.get_users_repo().get_by_oid(
        str(validate_object_id(user_oid))
    )
    if new_user is None:
        raise HTTPException(status_code=404, detail="User not found")

    class_id = str(new_user["class_id"]) if new_user.get("class_id") else ""
    user_info = {
        "_id": str(new_user["_id"]),
        "id": new_user["id"],
        "name": new_user["name"],
        "sex": new_user.get("sex", "unknown"),
        "roles": new_user.get("roles", []),
        "class_id": class_id,
        "group": [class_id] if class_id else [],
        "past": new_user.get("past", []),
    }
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

    user_key = str(validate_object_id(user_oid))

    # The legacy embedded `activities.members` array does not exist in the
    # normalized schema; rebuild the same view from ACTIVITY_MEMBERS.
    memberships = await backends.get_members_repo().list_for_member(user_key)

    members_by_activity: dict[str, list] = {}
    for membership in memberships:
        members_by_activity.setdefault(str(membership["activity"]), []).append(
            {
                "_id": str(membership["member"]),
                "status": membership.get("status"),
                "duration": membership.get("duration"),
                "mode": membership.get("mode"),
            }
        )

    activities, _ = await backends.get_activities_repo().list_by_oids(
        list(members_by_activity), "", -1
    )

    # `metadata.size` counts the member's activities regardless of the name
    # query (the original count omitted the name filter, cf. the commented-out
    # `# {"name": {"$regex": query}}`).
    count = len(activities)

    if query:
        pattern = re.compile(query, re.IGNORECASE)
        activities = [
            activity
            for activity in activities
            if pattern.search(activity.get("name") or "")
        ]

    activities.sort(key=lambda activity: activity["_id"], reverse=True)

    if page == -1:
        page_activities = activities
    else:
        start = (page - 1) * perpage
        page_activities = activities[start : start + perpage]

    data = []
    for activity in page_activities:
        activity_id = str(activity["_id"])
        data.append(
            {
                "_id": activity_id,
                "name": activity.get("name"),
                "date": activity.get("date"),
                "status": activity.get("status"),
                "type": activity.get("type"),
                "special": activity.get("special"),
                "members": members_by_activity.get(activity_id, []),
            }
        )

    return {
        "status": "ok",
        "code": 200,
        "data": data,
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

    logs, count = await backends.get_applogs_repo().list_for_user(
        user_oid, query, page, perpage
    )

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
    users_repo = backends.get_users_repo()
    oid = str(validate_object_id(user_oid))
    # Remove by index
    user_doc = await users_repo.get_by_oid(oid)
    name = (user_doc or {}).get("past", [])
    idx = int(past_identity_idx)
    if idx < 0 or idx >= len(name):
        raise HTTPException(status_code=404, detail="Past identity not found.")
    await users_repo.remove_past(oid, name[idx])
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
    users_repo = backends.get_users_repo()
    if await users_repo.find_by_business_id(past.past) is not None:
        raise HTTPException(status_code=409, detail="The past identity already exists.")
    await users_repo.add_past(str(validate_object_id(user_oid)), past.past)
    log.with_text(
        f"管理员 {await get_user_display_name(user['id'])} 添加了用户 {await get_user_display_name(user_oid)} 的历史身份 {past.past}",
        type="past_change",
    )
    await log.insert_log()
    return {"code": 200, "status": "ok"}

