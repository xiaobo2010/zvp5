import logging
from typing import Literal, Optional

from bcrypt import hashpw, gensalt
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError

from db import backends
from db.repos.oracle.users import UserGoneError
from typings.log import inject_log
from util.cert import generate_initial_password, get_current_totp
from util.group import get_user_permissions
from util.object_id import compulsory_temporary_token, get_current_user
from util.permission.user import credential_change_forbidden, role_change_forbidden
from util.user import get_user_display_name, user_profile
from utils import validate_object_id
from util.validation import validate_number, validate_student_name

router = APIRouter()

_logger = logging.getLogger(__name__)


async def _get_zvms_new_user(oid: str) -> Optional[dict]:
    validate_object_id(oid)
    return await backends.get_users_repo().get_by_oid(oid)


@router.get("/{user_id}")
async def get_user_v3(
    user_id: str,
    user=Depends(get_current_user),
):
    """
    Read a user's live profile from `zvms_new.users`.

    Permission: any authenticated user.
    """
    validate_object_id(user_id)

    target = await _get_zvms_new_user(user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="用户不存在")

    return {"status": "ok", "code": 200, "data": user_profile(target)}


AllowedRole = Literal["student", "secretary", "department", "admin", "teacher"]


class PutRolesRequest(BaseModel):
    roles: list[AllowedRole]


class PutBannedRequest(BaseModel):
    banned: bool


@router.put("/{user_id}/roles", status_code=204)
async def modify_user_roles_v3(
    user_id: str,
    payload: PutRolesRequest,
    user=Depends(get_current_user),
    log=Depends(inject_log),
):
    """
    修改用户的角色（身份）。

    Permission: admin only。角色白名单由 ``AllowedRole`` Literal 在校验层强制
    （非法角色 → 422），本端点不再重复判定。

    **目标侧保护**（v4.2.4-2 补）：操作者是 admin 不足以动手改任意账号——
    改别人的 admin 角色、改自己的角色都被 :func:`role_change_forbidden` 拦下
    （细节与理由见该函数）。目标账号的 `admin` 级判定用**实时**角色（与
    密码重置端点同源，``get_user_permissions``），不吃 JWT 快照。
    """
    per = user.get("per") or []
    if "admin" not in per:
        raise HTTPException(status_code=403, detail="权限不足")

    oid = validate_object_id(user_id)
    repo = backends.get_users_repo()

    target = await repo.get_by_oid(str(oid))
    if target is None:
        raise HTTPException(status_code=404, detail="用户不存在")

    target_per = await get_user_permissions(target)
    if role_change_forbidden(user["id"], target_per, str(target["_id"])):
        # 两个拒绝原因对应同一条策略，但文案分开：自我变更（提权/自锁）与改别的
        # admin 账号要能被前端分别提示。
        reason = (
            "不能修改自己的角色"
            if str(target["_id"]) == str(user["id"])
            else "不能修改管理员账号的角色"
        )
        raise HTTPException(status_code=403, detail=reason)

    await repo.set_roles(str(oid), list(payload.roles))

    log.with_text(
        f"管理员 {await get_user_display_name(user['id'])} 将用户 {target['name']} 的角色修改为 {list(payload.roles)}",
        type="role_change",
    )
    await log.insert_log()

    return


class PutStudentIdRequest(BaseModel):
    id: str


@router.put("/{user_id}/student-id", status_code=204)
async def modify_user_student_id_v3(
    user_id: str,
    payload: PutStudentIdRequest,
    user=Depends(get_current_user),
    log=Depends(inject_log),
):
    """
    修改用户的学号（``users.business_id``，登录名）。

    Permission: admin only。校验三层：``validate_number`` 格式闸（年级/班号/
    座号区间）+ 唯一性闸（重复 → **409**，与创建用户的既有契约一致）+
    数据库唯一约束 ``uq_users_business_id`` 兜底（并发窗口下的写入被约束拒绝时
    同样转 409，不再冒泡成 500）。

    学号是登录名，改完轮换 ``session_id``（交给 repo 的写语句），旧 token 立即
    失效——目标用户需用新学号重新登录，与改角色/改密码同一行为。
    """
    per = user.get("per") or []
    if "admin" not in per:
        raise HTTPException(status_code=403, detail="权限不足")

    oid = validate_object_id(user_id)
    repo = backends.get_users_repo()

    target = await repo.get_by_oid(str(oid))
    if target is None:
        raise HTTPException(status_code=404, detail="用户不存在")

    valid, message = validate_number(payload.id)
    if not valid:
        raise HTTPException(status_code=400, detail=message)

    existing = await repo.find_by_business_id(payload.id)
    if existing is not None and str(existing["_id"]) != str(oid):
        raise HTTPException(status_code=409, detail="学号已存在")

    try:
        await repo.set_business_id(str(oid), payload.id)
    except IntegrityError:
        # 预检与写入之间的并发抢占：唯一约束是最终裁决者。
        raise HTTPException(status_code=409, detail="学号已存在")

    log.with_text(
        f"管理员 {await get_user_display_name(user['id'])} 将用户 {target['name']} 的学号由 {target['id']} 改为 {payload.id}",
        type="student_id_change",
    )
    await log.insert_log()

    return


@router.delete("/{user_id}", status_code=204)
async def delete_user_v3(
    user_id: str,
    user=Depends(get_current_user),
    log=Depends(inject_log),
):
    """
    硬删除用户（账号 + 关联行），并把被删数据整体归档进回收站表 ``user_trash``。

    Permission: admin only，照抄 ``groups_v3_router.delete_group_v3`` 的形状。

    * 归档 + 清理 + 删行在**同一个事务**里完成（``users.py``
      ``delete_user_cascade``：一次 checkout、一次 commit）；任一步失败都不提交，
      会话关闭时整体回滚，不留半删状态。
    * 只能删别人：自我硬删除既会自锁管理入口，又会让审计主体消失，按「不能
      封禁自己」（``set_user_banned_v3``）的既有口径直接拒绝。
    * 审计 ltype ``user_hard_delete`` / level ``warning``：破坏性不可逆操作，
      与 ``ban_change`` 同级。
    """
    per = user.get("per") or []
    if "admin" not in per:
        raise HTTPException(status_code=403, detail="权限不足")

    oid = validate_object_id(user_id)
    repo = backends.get_users_repo()

    target = await repo.get_by_oid(str(oid))
    if target is None:
        raise HTTPException(status_code=404, detail="用户不存在")

    if str(oid) == str(user["id"]):
        raise HTTPException(status_code=403, detail="不能删除自己")

    try:
        await repo.delete_user_cascade(str(oid), str(user["id"]))
    except UserGoneError:
        # 前置读与事务读之间的并发删除：目标已经不在库里的，对外就是 404。
        # 此时整个事务未 commit，会话关闭时自动回滚，不留半删状态。
        raise HTTPException(status_code=404, detail="用户不存在")

    log.with_text(
        f"管理员 {await get_user_display_name(user['id'])} 硬删除了用户 {target['name']}（学号 {target['id']}），数据已存入回收站",
        type="user_hard_delete",
        level="warning",
    )
    await log.insert_log()

    return


@router.put("/{user_id}/banned", status_code=204)
async def set_user_banned_v3(
    user_id: str,
    payload: PutBannedRequest,
    user=Depends(get_current_user),
    log=Depends(inject_log),
):
    per = user.get("per") or []
    if "admin" not in per:
        raise HTTPException(status_code=403, detail="权限不足")

    oid = validate_object_id(user_id)
    repo = backends.get_users_repo()

    target = await repo.get_by_oid(str(oid))
    if target is None:
        raise HTTPException(status_code=404, detail="用户不存在")

    # 管理员不得封禁自己：一旦生效，唯一的管理入口会被自己关闭，需要人工介入
    # 才能恢复（无自助解封路径）。仅拦截"封禁"方向，解封自己无风险不额外限制。
    if payload.banned and str(oid) == str(user["id"]):
        raise HTTPException(status_code=403, detail="不能封禁自己")

    await repo.set_banned(str(oid), 1 if payload.banned else 0)

    log.with_text(
        f"管理员 {await get_user_display_name(user['id'])} {'封禁' if payload.banned else '解封'}了用户 {target['name']}",
        type="ban_change",
        level="warning",
    )
    await log.insert_log()

    return


class NewUserRequest(BaseModel):
    id: str
    name: str
    roles: list[str] = Field(default_factory=lambda: ["student"])
    class_id: Optional[str] = None


@router.post("", status_code=201)
async def create_user_v3(
    payload: NewUserRequest,
    response: Response,
    user=Depends(get_current_user),
    log=Depends(inject_log),
):
    per = user.get("per") or []
    if "admin" not in per:
        raise HTTPException(status_code=403, detail="权限不足")

    allowed_roles = {"student", "secretary", "department", "admin", "teacher"}
    for role in payload.roles:
        if role not in allowed_roles:
            raise HTTPException(status_code=400, detail=f"无效角色：{role}")

    valid, message = validate_number(payload.id)
    if not valid:
        raise HTTPException(status_code=400, detail=message)

    valid, message = validate_student_name(payload.name)
    if not valid:
        raise HTTPException(status_code=400, detail=message)

    repo = backends.get_users_repo()
    if await repo.find_by_business_id(payload.id):
        raise HTTPException(status_code=409, detail="学号已存在")

    class_oid = None
    if payload.class_id:
        class_oid = validate_object_id(payload.class_id)
        groups_repo = backends.get_groups_repo()
        if (
            await groups_repo.get_permission_group(str(class_oid)) is not None
            or await groups_repo.get(str(class_oid)) is None
        ):
            raise HTTPException(status_code=404, detail="班级不存在")

    # 建号初始密码 = 学号（平台运营要求新生可用学号直接登录；可枚举风险已确认接受）。
    # 学号 pwd_strength=1<3，登录仍走「弱密码 → 5 分钟临时令牌 → 强制改密」闭环。
    initial_password = payload.id
    password = hashpw(initial_password.encode("utf-8"), gensalt()).decode("utf-8")

    new_id = await repo.create(
        payload.id,
        payload.name,
        list(payload.roles),
        password,
        str(class_oid) if class_oid is not None else None,
    )

    log.with_text(
        f"管理员 {await get_user_display_name(user['id'])} 创建了用户 {payload.name}（学号 {payload.id}），初始密码为学号",
        type="user_create",
    )
    await log.insert_log()

    response.headers["Cache-Control"] = "no-store"
    return {"id": new_id, "initial_password": initial_password}


async def _is_same_class(a_id: str, b_id: str) -> bool:
    current = await _get_zvms_new_user(a_id)
    target = await _get_zvms_new_user(b_id)
    return (
        current is not None
        and target is not None
        and current.get("class_id") is not None
        and str(current.get("class_id")) == str(target.get("class_id"))
    )


@router.delete("/{user_id}/password")
async def reset_user_password_v3(
    user_id: str,
    response: Response,
    user=Depends(compulsory_temporary_token),
    log=Depends(inject_log),
):
    """
    为用户生成一个新的**一次性初始密码**（重置凭据）。

    Permission: admin/department, or secretary in the same class.
    Requires a temporary token (re-verification), mirroring zvp's
    `get_authz_user` posture for credential-affecting operations.

    另外：目标账号是 admin 级时，只有本人能重置其凭据（见
    ``credential_change_forbidden``）。明文只在本次响应里回一次，日志里
    绝不出现。
    """
    per = user.get("per") or []
    target = await _get_zvms_new_user(user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="用户不存在")

    if "admin" not in per and "department" not in per:
        if "secretary" not in per or not await _is_same_class(user["id"], user_id):
            raise HTTPException(status_code=403, detail="权限不足")

    target_per = await get_user_permissions(target)
    if credential_change_forbidden(user["id"], target_per, str(target["_id"])):
        raise HTTPException(status_code=403, detail="不能修改管理员账号的凭据")

    initial_password = generate_initial_password()
    target_oid = str(target["_id"])
    affected = await backends.get_users_repo().set_password_guarded(
        target_oid,
        hashpw(initial_password.encode("utf-8"), gensalt()).decode("utf-8"),
        user["id"],
    )
    if affected == 0:
        # 纵深：前置判定被绕过，或目标在两次读写之间被并发改成 admin 时，
        # 条件写会拒绝（0 行），这里按同样的文案报错。
        raise HTTPException(status_code=403, detail="不能修改管理员账号的凭据")

    log.with_text(
        f"用户 {await get_user_display_name(user['id'])} 将用户 {target['name']} 的密码重置为一次性初始密码",
        type="passwd_change",
    )
    await log.insert_log()

    response.headers["Cache-Control"] = "no-store"
    return {"status": "ok", "code": 200, "data": {"initial_password": initial_password}}


async def _persist_totp_view_log(log) -> None:
    """把「查看动态码」审计日志落库。

    作为 BackgroundTasks 在响应发出后执行，避免审计写库拖慢响应；失败不能静默
    丢日志，因此这里统一捕获并记录异常。
    """
    try:
        await log.insert_log()
    except Exception:
        _logger.exception("TOTP view audit log write failed (user=%s)", log.user)


@router.get("/{user_id}/totp")
async def get_user_totp_v3(
    user_id: str,
    response: Response,
    background_tasks: BackgroundTasks,
    user=Depends(get_current_user),
    log=Depends(inject_log),
):
    """
    Read the caller's own current login dynamic code (TOTP).

    Permission: any authenticated user, but **only their own** code. A dynamic
    code can be exchanged for a login token, so reading someone else's code is
    equivalent to impersonating them——this endpoint is therefore narrowed to
    self only.
    """
    target = await _get_zvms_new_user(user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="用户不存在")

    if str(target["_id"]) != str(user["id"]):
        raise HTTPException(status_code=403, detail="只能查看自己的动态码")

    # Compute with the caller's own oid: login derives the code from the oid
    # (routers/users_router.py), so feeding the path parameter through would
    # produce a different code whenever a business id (student number) is used.
    code, exp = get_current_totp(user["id"])

    # `target` is already the caller's full user document (the self-only check
    # above guarantees it), so its `name` is exactly what
    # get_user_display_name() would fetch again——reuse it instead of a 2nd query.
    log.with_text(
        f"用户 {target.get('name') or '未知用户'} 查看了自己的登录动态码",
        type="totp_view",
        level="info",
    )
    # Audit is a security control: it must still be written, just not on the
    # response path. Failures are logged inside the task, never swallowed.
    background_tasks.add_task(_persist_totp_view_log, log)

    response.headers["Cache-Control"] = "no-store"
    return {"status": "ok", "code": 200, "data": {"otp": code, "exp": exp}}


class PutBindingRequest(BaseModel):
    xuehai_id: str
    note: Optional[str] = None


@router.get("/{user_id}/bindings")
async def list_user_bindings_v3(
    user_id: str,
    user=Depends(get_current_user),
):
    """
    List a user's 学海 device bindings, including revoked ones.

    Permission: admin only.
    """
    per = user.get("per") or []
    if "admin" not in per:
        raise HTTPException(status_code=403, detail="权限不足")

    oid = validate_object_id(user_id)
    target = await backends.get_users_repo().get_by_oid(str(oid))
    if target is None:
        raise HTTPException(status_code=404, detail="用户不存在")

    rows = await backends.get_bindings_repo().list_for_user(str(oid))
    data = [
        {
            "xuehai_id": row["xuehai_id"],
            "source": row["source"],
            "status": row["status"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "note": row["note"],
        }
        for row in rows
    ]
    return {"data": data}


@router.put("/{user_id}/bindings", status_code=204)
async def set_user_binding_v3(
    user_id: str,
    payload: PutBindingRequest,
    user=Depends(get_current_user),
    log=Depends(inject_log),
):
    """
    Bind a user to a 学海 device id. Re-binding revokes the previous active
    binding(s) and activates the new one.

    Permission: admin only.
    """
    per = user.get("per") or []
    if "admin" not in per:
        raise HTTPException(status_code=403, detail="权限不足")

    oid = validate_object_id(user_id)
    target = await backends.get_users_repo().get_by_oid(str(oid))
    if target is None:
        raise HTTPException(status_code=404, detail="用户不存在")

    bindings = backends.get_bindings_repo()
    for row in await bindings.list_for_user(str(oid)):
        if row["status"] == "active" and row["xuehai_id"] != payload.xuehai_id:
            await bindings.revoke(str(oid), row["xuehai_id"])
    await bindings.upsert(str(oid), payload.xuehai_id, "admin", payload.note)

    log.with_text(
        f"管理员 {await get_user_display_name(user['id'])} 为用户 {target['name']} 绑定了学海设备 {payload.xuehai_id}",
        type="device_bind",
    )
    await log.insert_log()

    return


@router.delete("/{user_id}/bindings/{xuehai_id}", status_code=204)
async def delete_user_binding_v3(
    user_id: str,
    xuehai_id: str,
    user=Depends(get_current_user),
    log=Depends(inject_log),
):
    """
    Revoke a user's 学海 device binding. The row is kept (status='revoked').

    Permission: admin only.
    """
    per = user.get("per") or []
    if "admin" not in per:
        raise HTTPException(status_code=403, detail="权限不足")

    oid = validate_object_id(user_id)
    target = await backends.get_users_repo().get_by_oid(str(oid))
    if target is None:
        raise HTTPException(status_code=404, detail="用户不存在")

    await backends.get_bindings_repo().revoke(str(oid), xuehai_id)

    log.with_text(
        f"管理员 {await get_user_display_name(user['id'])} 为用户 {target['name']} 解绑了学海设备 {xuehai_id}",
        type="device_unbind",
    )
    await log.insert_log()

    return
