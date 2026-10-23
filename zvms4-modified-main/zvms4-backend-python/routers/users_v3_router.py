import logging
from datetime import datetime, timedelta
from typing import Literal, Optional

from bcrypt import hashpw, gensalt
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Request,
    Response,
)
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError

from db import backends
from db.oracle.session import session_scope
from db.repos.oracle.users import UserGoneError
from services import risk_monitor
from typings.log import inject_log, xuehai_id_from
from util.cert import generate_initial_password, get_current_totp
from util.group import get_user_permissions, is_in_a_same_class
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
    # 原因随请求一起落库（USERS.BAN_REASON + BAN_RECORDS），前端封禁弹窗可填；
    # 512 与 BAN_REASON / BAN_RECORDS.REASON 的列宽一致，超长在这里就 422。
    reason: Optional[str] = Field(default=None, max_length=512)


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


async def _release_primary_device_ban(user_oid: str, target: dict) -> str:
    """解封账号时连带解除其主设备的显式设备封禁，返回写进审计日志的说明。

    「临时密码连错」落的是永久设备封，不随账号解封自动消失（``device_ban_status``
    只推导账号联动那一支）。管理员解封账号的意图就是「这个人恢复使用」，把他
    自己的平板继续锁在门外不是意图的一部分。没有主设备时无事可做。

    解除失败不影响账号解封结果：账号状态是主事实，设备行可以事后单独处理，
    但必须在同一条审计里说明失败，否则「为什么这台机器还登不进」无从追查。
    """
    primary = target.get("primary_xuehai_id")
    if not primary:
        return ""
    masked = f"****{str(primary)[-4:]}"
    try:
        removed = await backends.get_device_bans_repo().delete(str(primary))
    except Exception as exc:
        _logger.exception("解封账号时解除设备封禁失败（账号已解封）")
        return f"，但解除主学海设备 {masked} 的设备封禁失败（{type(exc).__name__}）"
    if not removed:
        return ""
    return f"，并解除了主学海设备 {masked} 的设备封禁（{removed} 条）"


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

    if payload.banned:
        reason = (payload.reason or "").strip() or "管理员封禁"
        now = datetime.now()
        # 管理员封禁一律永久（until=None 就是「无到期」），与规则封禁的
        # 限时处罚区分开：人工判定不需要自动解封。
        await repo.set_ban(
            str(oid), reason=reason, until=None, source="admin", rule_code=None
        )
        await backends.get_ban_records_repo().insert(
            str(oid),
            source="admin",
            reason=reason,
            permanent=True,
            started_at=now,
            expires_at=None,
            operator_oid=str(user["id"]),
        )
        action = f"封禁了用户 {target['name']}（原因：{reason}）"
    else:
        # 解封只清当前状态；历史事实由 BAN_RECORDS 流水承担（不删不写）。
        await repo.clear_ban(str(oid))
        # 设备封禁不镜像账号状态，但「临时密码连错」写下的显式设备封是永久行：
        # 只解账号会留下「账号能登、这台机器永远登不进」的死角。
        device_note = await _release_primary_device_ban(str(oid), target)
        action = f"解封了用户 {target['name']}{device_note}"

    log.with_text(
        f"管理员 {await get_user_display_name(user['id'])} {action}",
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

    # 创建账号不是提权入口：admin 只能由现存的另一个 admin 通过受控流程
    # 授予，不能在一次普通建号里直接生成。否则一个被窃取的 admin token 可以
    # 造一个已知密码（学号）的 admin，再用它的 TOTP 给自己做双人审批。
    if "admin" in payload.roles:
        raise HTTPException(status_code=403, detail="不能通过创建账号直接授予管理员角色")
    allowed_roles = {"student", "secretary", "department", "teacher"}
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

    try:
        new_id = await repo.create(
            payload.id,
            payload.name,
            list(payload.roles),
            password,
            str(class_oid) if class_oid is not None else None,
        )
    except IntegrityError:
        raise HTTPException(status_code=409, detail="学号已存在")

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


# ---------------------------------------------------------------------------
# 主学海设备 / 一次性临时密码
#
# 设备身份取自请求头 ``Xuehai-Id``，不是请求体：请求体里的值只能说明「客户端
# 声称自己是谁」，请求头也是，但请求头是整条登录链路（登录、日志、封禁）唯一
# 认可的来源，自助改绑必须与之一致，否则等于允许任何令牌持有者替任意平板注册。
# ---------------------------------------------------------------------------

_DEVICE_ID_MAX_LEN = 32


async def _device_ban_status(
    xuehai_id: str,
) -> tuple[bool, Optional[str], Optional[datetime]]:
    """设备封禁状态统一问 :mod:`services.device_guard`。

    服务层在并行车道落地，这里延迟导入：路由模块不因服务层尚未就绪而在导入期
    失败，同时保证常量与判定逻辑只有一份实现（本层不重复推导账号封禁联动）。
    """
    from services.device_guard import device_ban_status

    return await device_ban_status(xuehai_id)


def _temp_password_ttl_minutes() -> int:
    """TTL 的唯一来源是 :mod:`services.device_guard` 的常量。"""
    from services.device_guard import TEMP_PASSWORD_TTL_MINUTES

    return TEMP_PASSWORD_TTL_MINUTES


def _validated_device_id(raw: str) -> str:
    """学海设备 ID：纯数字、1–32 位（与 ``users.primary_xuehai_id`` 列宽一致）。

    ``isascii`` 必须先判：``str.isdecimal`` 对全角/阿拉伯-印度数字同样为真，
    放到 Oracle 列里会变成无法与请求头对齐的异体字符。
    """
    value = (raw or "").strip()
    if not value.isascii() or not value.isdecimal() or not 1 <= len(value) <= _DEVICE_ID_MAX_LEN:
        raise HTTPException(status_code=400, detail="学海设备 ID 必须是 1–32 位数字")
    return value


def _iso_or_none(value) -> Optional[str]:
    return value.isoformat(timespec="seconds") if isinstance(value, datetime) else None


@router.get("/me/device")
async def get_my_device(request: Request, user=Depends(get_current_user)):
    """当前请求所携带的设备身份 + 本人主设备绑定/封禁状态。

    ``xuehai_id`` 是请求头原值（经 ``xuehai_id_from`` 规整，缺失/非法即空串），
    前端用它区分「没带设备头」和「带了但不是主设备」，因此不做 400。
    """
    xuehai_id = xuehai_id_from(request)
    current = await _get_zvms_new_user(user["id"])

    device_banned, ban_reason, ban_until = False, None, None
    if xuehai_id:
        # 空设备号不代表任何设备，拿它去查只会命中脏数据行。
        device_banned, ban_reason, ban_until = await _device_ban_status(xuehai_id)

    return {
        "status": "ok",
        "code": 200,
        "data": {
            "xuehai_id": xuehai_id,
            "primary_xuehai_id": current.get("primary_xuehai_id") if current else None,
            "device_banned": device_banned,
            "device_ban_reason": ban_reason,
            "device_ban_until": _iso_or_none(ban_until),
        },
    }


@router.get("/me/device-bindings")
async def get_my_device_bindings(
    request: Request,
    user=Depends(get_current_user),
):
    """当前请求设备 ID + 本账号在 ``USER_DEVICE_BINDINGS`` 中登记的设备 ID。

    * ``current_device_id`` 取自请求头 ``Xuehai-Id``（与登录/日志/封禁同源，
      经 :func:`xuehai_id_from` 规整）；缺失或非法时为 ``None``。设备号来自
      请求头而不是查询参数/请求体，调用方无法用别人的 OID 读取他人绑定。
    * ``registered_device_ids`` 只含 ``status='active'`` 的绑定，按仓储返回的
      登记时间升序排列；已撤销（revoked）的历史行不返回。取的是绑定行的
      ``xuehai_id``——它才是学海定制设备号；``device_id`` 列是 observe-only
      阶段留空的协议字段，绑定行 OID 也不是设备 ID。
    * ``USERS.PRIMARY_XUEHAI_ID``（V014）是账号选定的主设备，不属于
      ``USER_DEVICE_BINDINGS``，因此不混入这个列表；需要时由
      ``GET /me/device`` 单独返回。

    Permission: 任意已登录用户，且只能读自己。
    """
    current_device_id = xuehai_id_from(request) or None
    rows = await backends.get_bindings_repo().get_active_for_user(user["id"])
    return {
        "status": "ok",
        "code": 200,
        "data": {
            "current_device_id": current_device_id,
            "registered_device_ids": [row["xuehai_id"] for row in rows],
        },
    }


class PutPrimaryDeviceRequest(BaseModel):
    xuehai_id: str


@router.put("/me/primary-device", status_code=204)
async def set_my_primary_device(
    payload: PutPrimaryDeviceRequest,
    request: Request,
    user=Depends(get_current_user),
    log=Depends(inject_log),
):
    """把某台学海设备登记为本人主设备（可换成新平板）。

    两条互斥规则，对应「首次绑定」与「换绑」两种风险：

    * 未绑定（``primary_xuehai_id`` 为 NULL）：目标必须**就是当前这台设备**
      （请求头等于请求体）。否则任何拿到令牌的人都能把别人手里的平板登记成
      自己账号的合法入口，反向冒用。
    * 已绑定：**发起请求的必须是当前主设备**，目标可以是别的学海 ID（换新机）。
      这条是把「谁能决定下一台主设备」锁在已受信任的设备上。

    写操作不轮换 ``session_id``（主设备不是身份凭证，换机不该把其他会话踢下线）。
    """
    xuehai_id = _validated_device_id(payload.xuehai_id)
    header_xuehai_id = xuehai_id_from(request)

    current = await _get_zvms_new_user(user["id"])
    if current is None:
        raise HTTPException(status_code=404, detail="用户不存在")

    current_primary = current.get("primary_xuehai_id")

    if current_primary is None:
        if header_xuehai_id != xuehai_id:
            raise HTTPException(
                status_code=400,
                detail="首次绑定只能在目标设备上发起（请求头 Xuehai-Id 与目标设备不一致）",
            )
    elif header_xuehai_id != str(current_primary):
        raise HTTPException(
            status_code=403, detail="只能在主学海设备上更换主学海设备"
        )

    if current_primary is not None and str(current_primary) == xuehai_id:
        # 幂等：目标与现值相同，不产生写、不产生审计噪声。
        return

    await backends.get_users_repo().set_primary_xuehai(str(current["_id"]), xuehai_id)

    log.with_text(
        f"用户 {current.get('name') or '未知用户'} 将主学海设备由 {current_primary or '未绑定'} 改为 {xuehai_id}",
        type="device_bind",
        level="info",
    )
    await log.insert_log()


@router.delete("/me/primary-device", status_code=204)
async def clear_my_primary_device(
    request: Request,
    user=Depends(get_current_user),
    log=Depends(inject_log),
):
    """解绑本人的主学海设备，解绑后回到「未绑定不校验设备」状态。

    只允许当前主设备发起：否则任何令牌持有者都能把受害者踢回未绑定态，再用
    自己手上的平板抢占绑定。没有主设备时返回 **409**——这是状态冲突（本来就没
    绑），不是请求格式错误，前端据此可以只提示而不弹输入框。
    """
    current = await _get_zvms_new_user(user["id"])
    if current is None:
        raise HTTPException(status_code=404, detail="用户不存在")

    current_primary = current.get("primary_xuehai_id")
    if current_primary is None:
        raise HTTPException(status_code=409, detail="尚未绑定主学海设备")

    if xuehai_id_from(request) != str(current_primary):
        raise HTTPException(
            status_code=403, detail="只能在主学海设备上更换主学海设备"
        )

    await backends.get_users_repo().clear_primary_xuehai(str(current["_id"]))

    log.with_text(
        f"用户 {current.get('name') or '未知用户'} 解绑了主学海设备 {current_primary}",
        type="device_unbind",
        level="info",
    )
    await log.insert_log()


@router.delete("/{user_id}/primary-device", status_code=204)
async def admin_clear_user_primary_device(
    user_id: str,
    user=Depends(get_current_user),
    log=Depends(inject_log),
):
    """管理员清空任意用户的主设备（用户换机丢了旧平板时的人工出口）。

    Permission: admin only。幂等：目标本来未绑定也返回 204，不写审计。
    """
    per = user.get("per") or []
    if "admin" not in per:
        raise HTTPException(status_code=403, detail="权限不足")

    oid = validate_object_id(user_id)
    target = await backends.get_users_repo().get_by_oid(str(oid))
    if target is None:
        raise HTTPException(status_code=404, detail="用户不存在")

    previous = target.get("primary_xuehai_id")
    if previous is None:
        return

    await backends.get_users_repo().clear_primary_xuehai(str(oid))

    log.with_text(
        f"管理员 {await get_user_display_name(user['id'])} 清除了用户 {target['name']} 的主学海设备 {previous}",
        type="device_unbind",
        level="info",
    )
    await log.insert_log()


class ResetBanRecordsRequest(BaseModel):
    # 64 = BAN_RECORDS.RULE_CODE 列宽；空串在下游按「不限规则」处理。
    rule_code: Optional[str] = Field(default=None, max_length=64)


@router.post("/{user_id}/ban-records/reset")
async def reset_user_ban_records_v3(
    user_id: str,
    payload: Optional[ResetBanRecordsRequest] = None,
    user=Depends(get_current_user),
    log=Depends(inject_log),
):
    """清空某用户的封禁流水（可只清某条规则），把处罚梯度打回第一档。

    Permission: admin only。只删审计流水、不动 ``USERS.BANNED``：当前封禁状态
    由「解封」端点单独负责，混在一起会让「仍在封禁中但梯度已清零」变得无法表达。
    """
    per = user.get("per") or []
    if "admin" not in per:
        raise HTTPException(status_code=403, detail="权限不足")

    oid = validate_object_id(user_id)
    target = await backends.get_users_repo().get_by_oid(str(oid))
    if target is None:
        raise HTTPException(status_code=404, detail="用户不存在")

    rule_code = None
    if payload is not None:
        rule_code = (payload.rule_code or "").strip() or None
    # The delete is evidence-destroying: the audit row must commit with it.
    # A separate app-log transaction could fail after the rows are gone,
    # leaving no trace of who reset the escalation ladder.
    async with session_scope() as session:
        deleted = await backends.get_ban_records_repo(
            session
        ).delete_for_user_rule(str(oid), rule_code)

        scope = f"规则 {rule_code} 的" if rule_code else "全部"
        log.with_text(
            f"管理员 {await get_user_display_name(user['id'])} 重置了用户 {target['name']} {scope}封禁梯度（删除 {deleted} 条流水）",
            type="ban_change",
            level="warning",
        )
        await log.insert_log(session=session)
        await session.commit()

    return {"status": "ok", "code": 200, "data": {"deleted": deleted}}


@router.post("/{user_id}/temp-password")
async def create_temp_password_v3(
    user_id: str,
    response: Response,
    request: Request,
    user=Depends(get_current_user),
    log=Depends(inject_log),
):
    """为指定用户签发一个「一次性临时密码」，供其在**非主设备**上完成登录验证。

    Permission: admin / department（全校），或 secretary 且**同班**。同班判定用
    :func:`util.group.is_in_a_same_class`，它对「任一方没有班级」返回 ``None``——
    这里是放行条件而不是拒绝条件，所以必须写 ``is True`` 的等价判断（``not ...``
    会把 None 当成 False 拒绝，正好符合预期；显式判 None 则更易读）。

    目标侧保护：不能给别人手上的 ``admin`` 账号建临时密码——那等于绕开管理员
    凭据。只有请求者自己也是 admin 时才允许（对齐 ``credential_change_forbidden``
    的「管理员凭据只能由管理员处置」口径）。

    明文只在本响应里出现一次；库里只留 bcrypt 哈希。
    """
    per = user.get("per") or []
    target = await _get_zvms_new_user(user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="用户不存在")

    if "admin" not in per and "department" not in per:
        same_class = False
        if "secretary" in per:
            same_class = await is_in_a_same_class(user["id"], user_id)
        if same_class is not True:
            raise HTTPException(status_code=403, detail="权限不足")

    target_per = await get_user_permissions(target)
    if "admin" in target_per and "admin" not in per:
        raise HTTPException(status_code=403, detail="不能为管理员账号创建临时密码")

    from services.device_guard import generate_temp_password, hash_temp_password

    ttl_minutes = _temp_password_ttl_minutes()
    code = generate_temp_password()
    now = datetime.now()
    expires_at = now + timedelta(minutes=ttl_minutes)

    passwords = backends.get_temp_passwords_repo()
    # 作废旧码 + 落新码必须在一个事务里：拆成两次提交时，中间失败会让用户
    # 一个有效码都没有，并发签发则可能留下两个有效码，最终都被登录失败计数
    # 推向封禁。仓储内部的 issue_for_user 用同一 session 完成两件事。
    await passwords.issue_for_user(
        str(target["_id"]),
        str(user["id"]),
        hash_temp_password(code),
        xuehai_id_from(request) or None,
        expires_at,
    )

    log.with_text(
        f"用户 {await get_user_display_name(user['id'])} 为用户 {target['name']} 创建了一次性临时密码（{ttl_minutes} 分钟内有效）",
        type="temp_password_create",
        level="info",
    )
    await log.insert_log()

    # 签发人是**请求者**（app_user 即其 oid）：批量替别人开门才是要观察的行为。
    try:
        await risk_monitor.evaluate("temp_password_issue_burst", str(user["id"]))
    except Exception:
        _logger.warning("风险规则 temp_password_issue_burst 评估失败", exc_info=True)

    # 明文密码等价于一次登录凭据，任何缓存都可能把它留下。
    response.headers["Cache-Control"] = "no-store"
    return {
        "status": "ok",
        "code": 200,
        "data": {
            "code": code,
            "expires_at": _iso_or_none(expires_at),
            "target": {
                "_id": str(target["_id"]),
                "id": target.get("id"),
                "name": target.get("name"),
            },
        },
    }
