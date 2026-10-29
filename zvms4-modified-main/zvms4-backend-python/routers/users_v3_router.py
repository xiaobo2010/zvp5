import logging
from datetime import datetime, timedelta
from typing import Literal, Optional

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
from services import device_guard, risk_monitor
from typings.log import inject_log, xuehai_id_from
from util.cert import (
    generate_initial_password,
    get_current_totp,
    hash_password_async,
)
from util.group import get_user_permissions, is_in_a_same_class
from util.object_id import (
    compulsory_temporary_token,
    get_current_user,
    require_password_verification,
)
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


AllowedRole = Literal["student", "secretary", "department", "admin"]


class PutRolesRequest(BaseModel):
    roles: list[AllowedRole]


class PutBannedRequest(BaseModel):
    banned: bool
    # 原因随请求一起落库（USERS.BAN_REASON + BAN_RECORDS），前端封禁弹窗可填；
    # 512 与 BAN_REASON / BAN_RECORDS.REASON 的列宽一致，超长在这里就 422。
    reason: Optional[str] = Field(default=None, max_length=512)
    # 封禁时长（天）。None = 永久（与历史行为一致）；只收天数、不收客户端时间戳：
    # 时间在服务端按本地时区算，避免浏览器时区把「7 天」折算成别的时刻。
    duration_days: Optional[int] = Field(default=None, ge=1, le=36500)


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


class DeleteUserRequest(BaseModel):
    """硬删除的复核令牌：密码再认证签发的 5 分钟短令牌（缺失即 403）。"""

    verification_token: Optional[str] = None


@router.delete("/{user_id}", status_code=204)
async def delete_user_v3(
    user_id: str,
    payload: Optional[DeleteUserRequest] = None,
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

    await require_password_verification(
        payload.verification_token if payload is not None else None, user
    )

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


@router.put("/{user_id}/banned")
async def set_user_banned_v3(
    user_id: str,
    payload: PutBannedRequest,
    user=Depends(get_current_user),
    log=Depends(inject_log),
):
    """
    封禁 / 解封一个账号。

    Permission: admin only。两条目标侧保护（与 ``modify_user_roles_v3`` 的
    ``role_change_forbidden`` 同一套「admin 级账号只认本人」逻辑）：

    * 不能封自己——生效后唯一的管理入口被自己关掉，无自助恢复路径；
    * 不能封**另一个** admin——封禁会轮换 ``session_id``，对方立即掉线且
      无法登录，等于绕过双人授权把同级的复核通道掐断。目标是否 admin 用
      **实时**角色（``get_user_permissions``）判定，不吃 JWT 快照。
      解封方向不拦（放出来只会恢复对方权限），但仍需 admin 鉴权。

    封禁时长由 ``duration_days`` 表达：不传 = 永久（历史行为），传了就按
    服务端本地时区算到期时刻（客户端只给天数，避免时区漂移）。解封方向带
    ``duration_days`` 是语义错误，直接 422。
    """
    per = user.get("per") or []
    if "admin" not in per:
        raise HTTPException(status_code=403, detail="权限不足")

    if not payload.banned and payload.duration_days is not None:
        raise HTTPException(status_code=422, detail="解封请求不能指定封禁时长")

    oid = validate_object_id(user_id)
    repo = backends.get_users_repo()

    target = await repo.get_by_oid(str(oid))
    if target is None:
        raise HTTPException(status_code=404, detail="用户不存在")

    ban_until: Optional[datetime] = None
    if payload.banned:
        # 管理员不得封禁自己：一旦生效，唯一的管理入口会被自己关闭，需要人工介入
        # 才能恢复（无自助解封路径）。仅拦截"封禁"方向，解封自己无风险不额外限制。
        if str(oid) == str(user["id"]):
            raise HTTPException(status_code=403, detail="不能封禁自己")

        # 目标侧保护与 ``modify_user_roles_v3`` 的等级闸同源：admin 级账号只认
        # 本人。封禁会轮换 session_id，对方立即掉线，等于绕过双人授权把同级的
        # 复核通道掐断。角色用**实时**读取（不吃 JWT 快照）。
        target_per = await get_user_permissions(target)
        if "admin" in (target_per or []):
            raise HTTPException(status_code=403, detail="不能封禁管理员账号")

        reason = (payload.reason or "").strip() or "管理员封禁"
        now = datetime.now()
        # 不传时长 = 永久（until=None 就是「无到期」）；传了就落本地 naive 墙钟值，
        # 库里所有封禁时间列都是无时区的 TIMESTAMP，判定端用 datetime.now() 比较。
        if payload.duration_days is not None:
            ban_until = device_guard._to_local_naive(
                now + timedelta(days=payload.duration_days)
            )
        # 「写状态 + 写流水」收敛为一个事务（``apply_admin_ban`` → users 仓储
        # 的 ``ban_unit``）：流水写失败时不会留下「已封但无梯度/无审计」。
        await device_guard.apply_admin_ban(
            str(oid),
            reason=reason,
            operator_oid=str(user["id"]),
            at=now,
            until=ban_until,
        )
        suffix = "" if ban_until is None else f"，{payload.duration_days} 天后自动解封"
        action = f"封禁了用户 {target['name']}（原因：{reason}{suffix}）"
    else:
        # 解封只清当前状态；历史事实由 BAN_RECORDS 流水承担（不删不写）。
        # 设备封禁不镜像账号状态：显式设备封禁由设备管理端点单独解除，
        # 账号解封不再连带删设备行（否则管理员解一个号会放走一台被判定违规的机器）。
        await repo.clear_ban(str(oid))
        action = f"解封了用户 {target['name']}"

    log.with_text(
        f"管理员 {await get_user_display_name(user['id'])} {action}",
        type="ban_change",
        level="warning",
    )
    await log.insert_log()

    # 204 没有响应体，前端封禁后只能整页重拉才知道到期时间；这里回 200 带上
    # ``ban_until``（None = 永久），弹窗可以直接提示「将于 … 自动解封」。
    return {
        "status": "ok",
        "code": 200,
        "data": {
            "banned": payload.banned,
            "ban_until": ban_until.isoformat() if ban_until is not None else None,
            "permanent": payload.banned and ban_until is None,
        },
    }


class NewUserRequest(BaseModel):
    id: str
    name: str
    roles: list[str] = Field(default_factory=lambda: ["student"])
    class_id: Optional[str] = None


# users.business_id 是 VARCHAR2(32 CHAR)（DDL ``V001__initial_schema.sql``、镜像
# ``db/models.py``）。宽于列宽的学号在 Oracle 侧会抛 ORA-12899（value too large），
# 那是 500 而不是可读的 400，所以入口先按列宽挡一次。
_MAX_STUDENT_ID_LEN = 32


def _normalize_student_id(raw: str) -> str:
    """建号口对学号的**轻校验**：只保证「存得下、查得回、能当登录名」。

    刻意不再套 ``validate_number``（8 位纯数字 / 年份窗口 / 班号 1-25 / 座号
    1-60）：班级内建号的学号由各班级自定义编号规则，格式限制由管理员承担。
    这里只挡三类会真的炸后端的输入：

    * strip 后为空 —— 空登录名既无法登录也无法被 ``find_by_business_id`` 命中；
    * 超出列宽 —— Oracle 抛 ORA-12899 变成 500；
    * 含空白或控制字符 —— 学号是登录名，前后/中间空白在登录表单里只会表现为
      「密码明明对却登不上」，控制字符则会污染审计日志与导出。

    字符集保持宽松（字母 / 数字 / 常见符号都放行），唯一性仍由下方
    ``find_by_business_id`` + ``uq_users_business_id`` 兜底。

    返回 strip 后的学号，调用方一律使用返回值。
    """
    student_id = raw.strip()
    if not student_id:
        raise HTTPException(status_code=400, detail="学号不能为空")
    if len(student_id) > _MAX_STUDENT_ID_LEN:
        raise HTTPException(
            status_code=400,
            detail=f"学号长度不能超过 {_MAX_STUDENT_ID_LEN} 个字符",
        )
    for char in student_id:
        code = ord(char)
        if char.isspace() or code < 32 or code == 127 or 0x80 <= code <= 0x9F:
            raise HTTPException(
                status_code=400, detail="学号不能包含空白或控制字符"
            )
    return student_id


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
    allowed_roles = {"student", "secretary", "department"}
    for role in payload.roles:
        if role not in allowed_roles:
            raise HTTPException(status_code=400, detail=f"无效角色：{role}")

    valid, message = validate_student_name(payload.name)
    if not valid:
        raise HTTPException(status_code=400, detail=message)

    # 建号不套班级/年级级的学号格式闸（validate_number），只做「能存能登」轻校验。
    # 改学号端点仍走 validate_number——那是纠错入口，格式由平台约定。
    student_id = _normalize_student_id(payload.id)

    repo = backends.get_users_repo()
    if await repo.find_by_business_id(student_id):
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
    # 8 位纯数学号 pwd_strength=1<3，登录仍走「弱密码 → 5 分钟临时令牌 → 强制改密」
    # 闭环；非 8 位学号同样 <3（长度不达 8 或只有单一字符类），见 util.cert.pwd_strength。
    initial_password = student_id
    password = (await hash_password_async(initial_password)).decode("utf-8")

    try:
        new_id = await repo.create(
            student_id,
            payload.name,
            list(payload.roles),
            password,
            str(class_oid) if class_oid is not None else None,
        )
    except IntegrityError:
        raise HTTPException(status_code=409, detail="学号已存在")

    log.with_text(
        f"管理员 {await get_user_display_name(user['id'])} 创建了用户 {payload.name}（学号 {student_id}），初始密码为学号",
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
        (await hash_password_async(initial_password)).decode("utf-8"),
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
    verification_token: Optional[str] = None


class DeleteBindingRequest(BaseModel):
    """解绑的复核令牌（与绑定同一把密码再认证闸）。"""

    verification_token: Optional[str] = None


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

    await require_password_verification(payload.verification_token, user)

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
    payload: Optional[DeleteBindingRequest] = None,
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

    await require_password_verification(
        payload.verification_token if payload is not None else None, user
    )

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
    verification_token: Optional[str] = None


class ClearPrimaryDeviceRequest(BaseModel):
    """解绑主设备的复核令牌：自助与管理员两条路径共用同一形状。"""

    verification_token: Optional[str] = None


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

    换绑是「把账号入口迁到另一台机器」，所以**已绑定**时必须带一次密码再认证
    （``verification_token``）；首次绑定没有可迁的入口，且在目标设备上发起，不额外
    要求——否则新用户永远进不了绑定流程。
    """
    xuehai_id = _validated_device_id(payload.xuehai_id)
    header_xuehai_id = xuehai_id_from(request)

    current = await _get_zvms_new_user(user["id"])
    if current is None:
        raise HTTPException(status_code=404, detail="用户不存在")

    current_primary = current.get("primary_xuehai_id")

    if current_primary is not None:
        await require_password_verification(payload.verification_token, user)

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
    payload: Optional[ClearPrimaryDeviceRequest] = None,
    user=Depends(get_current_user),
    log=Depends(inject_log),
):
    """解绑本人的主学海设备，解绑后回到「未绑定不校验设备」状态。

    只允许当前主设备发起：否则任何令牌持有者都能把受害者踢回未绑定态，再用
    自己手上的平板抢占绑定。没有主设备时返回 **409**——这是状态冲突（本来就没
    绑），不是请求格式错误，前端据此可以只提示而不弹输入框。

    解绑一律要求密码再认证（``verification_token``）：把账号入口摘掉与换绑是同
    一级别的动作，不因为「反正还有设备头」而放宽。
    """
    await require_password_verification(
        payload.verification_token if payload is not None else None, user
    )

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
    payload: Optional[ClearPrimaryDeviceRequest] = None,
    user=Depends(get_current_user),
    log=Depends(inject_log),
):
    """管理员清空任意用户的主设备（用户换机丢了旧平板时的人工出口）。

    Permission: admin only。幂等：目标本来未绑定也返回 204，不写审计。
    替别人摘掉入口是高危动作，一律要求管理员本人的密码再认证。
    """
    per = user.get("per") or []
    if "admin" not in per:
        raise HTTPException(status_code=403, detail="权限不足")

    oid = validate_object_id(user_id)
    target = await backends.get_users_repo().get_by_oid(str(oid))
    if target is None:
        raise HTTPException(status_code=404, detail="用户不存在")

    await require_password_verification(
        payload.verification_token if payload is not None else None, user
    )

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


class CreateTempPasswordRequest(BaseModel):
    """签发人是本端点的操作者：临时密码等价于一次登录凭据，故要求密码再认证。"""

    verification_token: Optional[str] = None


@router.post("/{user_id}/temp-password")
async def create_temp_password_v3(
    user_id: str,
    response: Response,
    request: Request,
    payload: Optional[CreateTempPasswordRequest] = None,
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

    签发人要先过密码再认证（``verification_token``）：这张码能让持有人以目标
    身份登录，等于替别人开门，不能只凭一个长期会话令牌发放。
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

    await require_password_verification(
        payload.verification_token if payload is not None else None, user
    )

    target_per = await get_user_permissions(target)
    if "admin" in target_per and "admin" not in per:
        raise HTTPException(status_code=403, detail="不能为管理员账号创建临时密码")

    from services.device_guard import (
        generate_temp_password,
        hash_temp_password_async,
    )

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
        await hash_temp_password_async(code),
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
