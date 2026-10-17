import logging
import re
from datetime import datetime
from typing import Literal, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from bcrypt import hashpw, gensalt
from util.activity_signoff import user_activity_signoff
from typings.log import inject_log, ZVMSLog, xuehai_id_from
from typings.user import User
from util.calculate import calculate_user_time
from util.group import get_user_permissions, is_in_a_same_class
from util.object_id import (
    compulsory_temporary_token,
    get_current_user,
    validate_object_id,
    string_to_option_object_id,
    optional_current_user,
    password_change_token,
)
from util.permission.user import (
    credential_change_forbidden,
    validate_read_user_permission,
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

_logger = logging.getLogger(__name__)


async def _observe_binding(
    request: Request, user_oid: str, log_name: str, xuehai_id: str
) -> bool:
    """Observe-only 学海 device-binding hook; never raises, never blocks login.

    Returns ``True`` only when the presented 学海 ID is not among the user's
    active bindings; every error degrades to ``False`` so a binding problem can
    never fail an otherwise successful login.
    """
    if not xuehai_id:
        return False
    try:
        bindings = backends.get_bindings_repo()
        active = await bindings.get_active_for_user(user_oid)
        if not active:
            await bindings.upsert(user_oid, xuehai_id, "auto", None)
            await ZVMSLog(
                str(request.url),
                user_oid,
                f"用户 {log_name} 首次绑定了学海设备 {xuehai_id}",
                datetime.timestamp(datetime.now()),
                type="device_bind",
                level="info",
                xuehai_id=xuehai_id,
            ).insert_log()
            return False
        if all(row.get("xuehai_id") != xuehai_id for row in active):
            bound = ",".join(sorted({str(row.get("xuehai_id")) for row in active}))
            await ZVMSLog(
                str(request.url),
                user_oid,
                f"用户 {log_name} 登录设备 {xuehai_id} 与已绑定设备 {bound} 不一致",
                datetime.timestamp(datetime.now()),
                type="binding_mismatch",
                level="warning",
                xuehai_id=xuehai_id,
            ).insert_log()
            return True
        return False
    except Exception:
        _logger.exception("device-binding observe hook failed")
        return False


class AuthUser(BaseModel):
    id: str
    mode: Literal["long", "short"] = "long"
    credential: str
    purpose: str = "login"


@router.post("/auth")
async def auth_user(auth: AuthUser, request: Request, response: Response):
    id = auth.id
    mode = auth.mode or "long"
    credential = auth.credential
    # Fail-safe: only an exact "login" gets login semantics. Any other value
    # (empty, misspelled, unknown) is treated as identity verification, so a
    # typo can never mint a long-lived session token.
    is_login = auth.purpose == "login"
    xuehai_id = xuehai_id_from(request)

    if string_to_option_object_id(id) is None:
        user_doc = await backends.get_users_repo().find_by_business_id(id)
        if user_doc:
            id = str(user_doc["_id"])
        else:
            # Indistinguishable from a wrong password: same status and, below,
            # the same body, so a probe cannot tell whether the 学号 exists.
            raise HTTPException(
                status_code=403,
                detail="用户名或密码错误",
            )

    # Server-side clamp: a verification request must never issue a session
    # token, whatever ``mode`` says. Routing it through the short-token path
    # keeps ``validate_by_cert`` (and thus the login path) untouched, while
    # TOTP credentials remain accepted and yield a 5-minute temporary token.
    validation_mode = mode if is_login else "short"

    try:
        result, weak_password = await validate_by_cert(
            id, credential, validation_mode
        )
    except HTTPException as exc:
        # Normalize the credential failure to the same body as "user not found",
        # keeping the two paths byte-identical.
        if exc.detail == "Password incorrect":
            raise HTTPException(status_code=403, detail="用户名或密码错误")
        raise

    user_doc = await backends.get_users_repo().get_by_oid(
        str(validate_object_id(id))
    )
    log_name = user_doc["name"] if user_doc else await get_user_name(id)
    is_otp = used_otp_credential(credential, id)
    if is_login:
        if is_otp:
            log_text = f"用户 {log_name} 使用动态码登录了系统"
        elif weak_password and mode == "long":
            log_text = f"用户 {log_name} 登录了系统（密码强度不足，仅发放临时授权）"
        else:
            log_text = f"用户 {log_name} 登录了系统"
        log_type = "user_login"
    else:
        credential_kind = "动态码" if is_otp else "密码"
        log_text = f"用户 {log_name} 完成了身份验证（{credential_kind}）"
        log_type = "user_verify"
    log = ZVMSLog(
        str(request.url),
        id,
        log_text,
        datetime.timestamp(datetime.now()),
        type=log_type,
        xuehai_id=xuehai_id,
    )

    await log.insert_log()

    user_oid = str(validate_object_id(id))
    binding_mismatch = await _observe_binding(request, user_oid, log_name, xuehai_id)

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

    response.headers["Cache-Control"] = "no-store"
    return {
        "token": result,
        "_id": id,
        "profile": profile,
        "weak_password": weak_password,
        "binding_mismatch": binding_mismatch,
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
    user=Depends(password_change_token),
    log=Depends(inject_log),
):
    # Validate user's permission
    if user.get("purpose") == "password_change" and user["id"] != user_oid:
        raise HTTPException(status_code=403, detail="初始密码授权仅限修改本人密码")
    secretary = "secretary" in user["per"] and await is_in_a_same_class(
        user["id"], user_oid
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

    # admin 级账号的凭据只有本人能动：否则某管理员可改掉同级账号的密码，
    # 再以对方身份登录「批准」自己的危险操作，双人授权即被冒充击穿。本人
    # 自助改密（actor oid == target oid）不受影响。
    target_per = await get_user_permissions(target)
    if credential_change_forbidden(user["id"], target_per, oid):
        raise HTTPException(status_code=403, detail="不能修改管理员账号的凭据")

    # Change user's password（条件写：前置判定放行后，若目标在此期间被并发
    # 改成 admin，数据库层的守卫仍会拒绝，返回 0 行）。
    affected = await repo.set_password_guarded(oid, str(password), user["id"])
    if affected == 0:
        raise HTTPException(status_code=403, detail="不能修改管理员账号的凭据")

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
    query: str = Query("", max_length=128),
    page: int = Query(1, ge=-1),
    perpage: int = Query(5, ge=1, le=100),
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
    # Check user's permission
    await validate_read_user_permission(user, user_oid, "volunteer")

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
        "banned": bool(new_user.get("banned")),
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
    page: int = Query(-1, ge=-1),
    perpage: int = Query(10, ge=1, le=100),
    query: str = Query("", max_length=128),
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
    page: int = Query(-1, ge=-1),
    perpage: int = Query(10, ge=1, le=100),
    query: str = Query("", max_length=128),
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

