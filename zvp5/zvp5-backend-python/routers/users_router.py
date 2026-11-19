"""用户域单路由文件（原 users_router + users_v2_router + users_v3_router 合并）。

统一挂载 ``/api/v1/users``。合并时按「前端实际调用」逐端点裁决：

* 未版本化的列表 / 单用户 / 活动 / 时长被 v2/v3 的同路径实现取代（前端已全部
  打到 v1 路径，响应形状与它们对齐）；
* 认证三件套、改密、日志、历史身份、设备自助管理这些 v1 侧没有对应实现的端点
  原样迁入；
* 合并后无人调用的死端点（503 契约桩、users_v3 的设备绑定管理、单用户日志、
  封禁梯度重置）已于 2026-11-19 全部删除，路由表里只剩前端真实调用的端点。

**注册顺序是契约**：静态段（``/auth``、``/me/*``）与不当兜底的集合路由必须先于
参数段 ``/{user_id}`` 注册，否则会被参数段吞掉。
"""

import asyncio
import inspect
import logging
from datetime import datetime, timedelta
from typing import Literal, Optional

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Query,
    Request,
    Response,
)
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError

from db import backends
from db.oracle.session import session_scope
from db.repos.oracle.users import UserGoneError
from db.system_state import is_enabled_fresh
from services import device_guard, log_events, risk_monitor
from typings.log import (
    ZVMSLog,
    client_ip_from,
    inject_log,
    user_agent_from,
    xuehai_id_from,
)
from util import login_throttle
from util.ban import ban_headers, ban_is_active, raise_banned
from util.calculate import calculate_user_time, find_percentile_threshold
from util.cert import (
    generate_initial_password,
    get_current_totp,
    get_hashed_password_by_cert,
    hash_password_async,
    jwt_encode,
    prefetched_user,
    used_otp_credential,
    validate_by_cert,
)
from util.group import get_user_permissions, is_in_a_same_class
from util.object_id import (
    compulsory_temporary_token,
    get_current_user,
    password_change_token,
    require_password_verification,
    string_to_option_object_id,
)
from util.oid import validate_object_id
from util.permission.user import (
    credential_change_forbidden,
    role_change_forbidden,
    validate_read_user_permission,
)
from util.serialization import iso, iso_seconds
from util.user import get_user_display_name, get_user_name, user_profile
from util.validation import validate_number, validate_student_name

router = APIRouter()

_logger = logging.getLogger(__name__)

# 失败登录的证据：同一 oid 60 秒内最多写一条。没有这个节流，攻击者每提交一次
# 错误密码就能往 APP_LOGS 里塞一行，「记录失败」会变成新的日志风暴入口。
_LOGIN_FAILURE_LOG_SECONDS = 60
_LOGIN_FAILURE_LOG_MAX_ENTRIES = 4096
_login_failure_logged: dict[str, float] = {}


def _presented_oid(value: str) -> str:
    """请求里的标识解析成 oid；不是合法 oid（学号也没查到）时返回空串。"""
    parsed = string_to_option_object_id(value)
    return str(parsed) if parsed is not None else ""


async def _note_login_failure(
    request: Request, user_oid: str, xuehai_id: str, summary: str
) -> None:
    """把「有人拿错了凭据」这件事落成审计，**只记摘要**。

    为什么现在要记：登录失败次数是「暴破 / 封禁期重试」两条规则的唯一证据，
    不落库就只剩客户端自述。摘要固定为 ``password-mismatch`` / ``banned``，
    提交的密码、长度、哈希一律不进日志——日志的读者是管理员，不是攻击者。

    节流按 oid：同一个人 60 秒内的连续失败只留一条。窗口由判定层按分钟统计，
    省掉的行不会改变结论，却能挡住日志风暴。

    级别刻意用 ``info``：风控计数只按 ``ltype`` + 时间窗取数，与 ``level`` 无关；
    而保留策略会豁免所有 ``warning`` 级行，取 ``warning`` 就等于让这类行永不
    过期（历史堆积的根因）。``info`` 让它们随 ``APP_LOGS_RETENTION_DAYS`` 自然
    清理，管理端列表也默认隐藏该类型（见 ``routers/logs_router.py``）。
    """
    if not user_oid:
        return
    now = datetime.now().timestamp()
    last = _login_failure_logged.get(user_oid)
    if last is not None and now - last < _LOGIN_FAILURE_LOG_SECONDS:
        return
    if len(_login_failure_logged) > _LOGIN_FAILURE_LOG_MAX_ENTRIES:
        for key, stamp in list(_login_failure_logged.items()):
            if now - stamp >= _LOGIN_FAILURE_LOG_SECONDS:
                _login_failure_logged.pop(key, None)
        if len(_login_failure_logged) > _LOGIN_FAILURE_LOG_MAX_ENTRIES:
            _login_failure_logged.clear()
    _login_failure_logged[user_oid] = now
    try:
        await ZVMSLog(
            str(request.url),
            user_oid,
            f"登录失败（{summary}）",
            now,
            type=log_events.LOGIN_FAIL,
            level="info",
            xuehai_id=xuehai_id,
            client_ip=client_ip_from(request),
            user_agent=user_agent_from(request),
        ).insert_log()
    except Exception:
        _logger.exception("写入 login_fail 日志失败")


async def _run_risk(trigger: str, user_oid: str) -> None:
    """风险规则的挂钩点：评估失败只留日志，绝不影响本次请求的结果。"""
    if not user_oid:
        return
    try:
        await risk_monitor.evaluate(trigger, user_oid)
    except Exception:
        # evaluate 本身已兜底；这里再包一层是因为调用点都在登录/导出主路径上。
        _logger.warning("风险规则 %s 评估失败，忽略", trigger, exc_info=True)


# 审计与风控是「响应不需要等」的副作用：它们既不决定 token，也不改状态码，
# 却是每次登录里 3～5 个跨洋往返（单程实测 138ms）。搬到响应之后跑，客户端
# 少等这一截；任务体里各自开 session（见 ``main.py`` 的 security audit 同款
# 做法），绝不复用请求那个——响应返回时它已经关了。
#
# 这里不学 ``main.py`` 再用 Semaphore 限并发：模块级 Semaphore 会绑定第一个
# 事件循环，而测试里每个 TestClient 都是新循环，跨循环复用直接 RuntimeError；
# 限流带来的收益（任务本身只是一次 insert + 一次评估）不值这个坑。
_background_tasks: set[asyncio.Task] = set()


async def _run_background(coro) -> None:
    try:
        await coro
    except Exception:
        # 后台任务没有调用方等结果：异常只能落日志，否则会变成「响应已发出
        # 之后」的未处理异常。
        _logger.warning("后台审计/风控任务失败（已忽略）", exc_info=True)


def _spawn_background(coro) -> None:
    """派发后台任务，并用集合持有强引用，避免任务跑到一半被 GC 回收。"""
    try:
        task = asyncio.create_task(_run_background(coro))
    except RuntimeError:
        # 没有运行中的事件循环（同步调用/已关闭）：关掉协程，别留
        # "coroutine was never awaited" 警告。
        coro.close()
        _logger.warning("后台审计/风控任务无法调度（无事件循环），已跳过")
        return
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


async def _log_failure_then_evaluate(
    request: Request, user_oid: str, xuehai_id: str, summary: str, trigger: str
) -> None:
    """失败证据 + 风险判定必须合成**一个**后台任务，且先写日志后评估。

    ``login_failure_burst`` / ``ban_evasion_retry`` 的 count 数的就是刚写下的
    那一行 ``login_fail``；拆成两个并发任务会让本次失败偶尔数不进去，阈值
    也就偶尔晚一次触发。
    """
    await _note_login_failure(request, user_oid, xuehai_id, summary)
    await _run_risk(trigger, user_oid)


async def _log_login_then_evaluate(
    log: ZVMSLog, user_oid: str, trigger: Optional[str]
) -> None:
    """成功登录/身份验证的审计，以及紧随其后的风控评估。

    顺序不能换：``login_success_churn`` 数的是刚写下的 ``user_login`` 行，
    先评估后落日志会让本次登录数不进去，阈值永远晚一次触发。
    """
    try:
        await log.insert_log()
    except Exception:
        _logger.exception("写入登录审计日志失败")
    if trigger:
        await _run_risk(trigger, user_oid)


async def _log_device_mismatch_then_evaluate(
    request: Request,
    user_oid: str,
    log_name: str,
    xuehai_id: str,
    primary: object,
) -> None:
    """非主设备登录的审计 + 两条风控，同样先写日志后评估（同上的计数依赖）。"""
    # 审计写失败不得吞掉这次拒绝的语义：拒绝早已同步返回，这里只补证据。
    try:
        await ZVMSLog(
            str(request.url),
            user_oid,
            f"用户 {log_name} 尝试在非主学海设备 {xuehai_id} 上登录"
            f"（主设备 {primary}）",
            datetime.timestamp(datetime.now()),
            type=log_events.DEVICE_MISMATCH,
            level="warning",
            xuehai_id=xuehai_id,
            client_ip=client_ip_from(request),
            user_agent=user_agent_from(request),
        ).insert_log()
    except Exception:
        _logger.exception("写入 device_mismatch 日志失败")
    await _run_risk("challenge_flood", user_oid)
    await _run_risk("device_switch_burst", user_oid)


async def _bind_device_and_log(
    request: Request, user_oid: str, log_name: str, xuehai_id: str
) -> None:
    """首次绑定：upsert 与审计日志都挪到响应之后。

    返回值不参与响应（``binding_mismatch`` 只看「读到的绑定里有没有这台
    设备」），所以这两步写得慢不该拖住登录。
    """
    try:
        await backends.get_bindings_repo().upsert(user_oid, xuehai_id, "auto", None)
        await ZVMSLog(
            str(request.url),
            user_oid,
            f"用户 {log_name} 首次绑定了学海设备 {xuehai_id}",
            datetime.timestamp(datetime.now()),
            type=log_events.DEVICE_BIND,
            level="info",
            xuehai_id=xuehai_id,
            client_ip=client_ip_from(request),
            user_agent=user_agent_from(request),
        ).insert_log()
    except Exception:
        _logger.exception("首次绑定学海设备失败")


async def _log_binding_mismatch(
    request: Request, user_oid: str, log_name: str, xuehai_id: str, bound: str
) -> None:
    """设备不一致的审计：判定（``binding_mismatch``）已经同步做完了，只补日志。"""
    try:
        await ZVMSLog(
            str(request.url),
            user_oid,
            f"用户 {log_name} 登录设备 {xuehai_id} 与已绑定设备 {bound} 不一致",
            datetime.timestamp(datetime.now()),
            type=log_events.BINDING_MISMATCH,
            level="warning",
            xuehai_id=xuehai_id,
            client_ip=client_ip_from(request),
            user_agent=user_agent_from(request),
        ).insert_log()
    except Exception:
        _logger.exception("写入 binding_mismatch 日志失败")


async def _observe_binding(
    request: Request, user_oid: str, log_name: str, xuehai_id: str, session=None
) -> bool:
    """Observe-only 学海 device-binding hook; never raises, never blocks login.

    Returns ``True`` only when the presented 学海 ID is not among the user's
    active bindings; every error degrades to ``False`` so a binding problem can
    never fail an otherwise successful login.
    """
    if not xuehai_id:
        return False
    try:
        bindings = backends.get_bindings_repo(session)
        active = await bindings.get_active_for_user(user_oid)
        if not active:
            # 「有没有绑定」决定返回值，必须同步读；首次绑定的写与日志不参与
            # 判定，放到响应之后。
            _spawn_background(_bind_device_and_log(request, user_oid, log_name, xuehai_id))
            return False
        if all(row.get("xuehai_id") != xuehai_id for row in active):
            bound = ",".join(sorted({str(row.get("xuehai_id")) for row in active}))
            _spawn_background(
                _log_binding_mismatch(request, user_oid, log_name, xuehai_id, bound)
            )
            return True
        return False
    except Exception:
        _logger.exception("device-binding observe hook failed")
        return False


def _profile_payload(user_doc, user_id: str) -> Optional[dict]:
    """登录响应的 ``profile``：两个登录端点必须逐字段同形，故抽成一处。

    ``user_id`` 单独传：``/api/users/auth`` 历史上回显的是请求里那个标识
    （学号或 oid），改成库里的 oid 会改变前端已经依赖的字段值。
    """
    if not user_doc:
        return None
    return {
        "_id": user_id,
        "id": user_doc["id"],
        "name": user_doc["name"],
        "roles": user_doc.get("roles", []),
        "class_id": str(user_doc["class_id"]) if user_doc.get("class_id") else "",
        "group": [str(user_doc["class_id"])] if user_doc.get("class_id") else [],
    }


async def _reject_banned_device(xuehai_id: str) -> None:
    """设备闸：这台机器被封就 403，**与当前提交的账号无关**。

    放在密码校验之前是有意的：设备封禁是「机器被拉黑」，先验密码会泄露
    「这个账号存在且密码对不对」的信息给一台已知的违规机器，也让被借用的
    设备多试几次密码。判定失败一律放行（fail-open）：设备表读不到是运维
    问题，不能变成全站无法登录的单点故障。
    """
    if not xuehai_id:
        return
    try:
        banned, reason, until = await device_guard.device_ban_status(xuehai_id)
    except Exception:
        _logger.exception("设备封禁判定失败，放行本次登录")
        return
    if not banned:
        return
    raise HTTPException(
        status_code=403,
        detail={
            "code": "DEVICE_BANNED",
            "message": device_guard.DEVICE_BANNED_MESSAGE,
        },
        # 原因与到期时间仍走 util.ban 的头口径（URL 编码 + epoch 秒），前端只写
        # 一套解析逻辑。
        headers=ban_headers({"banned": True, "ban_reason": reason, "ban_until": until}),
    )


async def _guard_primary_device(
    request: Request,
    user_doc,
    user_oid: str,
    log_name: str,
    xuehai_id: str,
) -> None:
    """主设备闸（第三步）：凭据正确但设备不是本人的 → 给挑战令牌，不发 token。

    这里**不封账号**也**不封设备**：账号密码已经证明是本人，换设备是常见
    事实（新平板、旧机坏了），按封禁处理会把所有人挡在门外。真正解决路径是
    让团支书/管理员签发一次性临时密码，所以响应里直接带 challenge。

    顺序：豁免角色 → 无设备号 → 主设备比对。
    团支书及以上直接放行（含无设备号、非主设备）；普通用户必须带学海设备号，
    否则 403 ``DEVICE_REQUIRED``，连 challenge 都不发——没有可兑换的目标设备。
    """
    roles = (user_doc or {}).get("roles")
    if device_guard.has_privileged_role(roles):
        return

    primary = (user_doc or {}).get("primary_xuehai_id")
    if not xuehai_id:
        # 先判设备号再判主设备：未绑定 = 没有可比的基准，不等于「本机是主设备」。
        try:
            await ZVMSLog(
                str(request.url),
                user_oid,
                f"用户 {log_name} 未使用学海设备，登录被拒绝（普通用户）",
                datetime.timestamp(datetime.now()),
                type=log_events.DEVICE_REQUIRED,
                level="warning",
                xuehai_id=xuehai_id,
                client_ip=client_ip_from(request),
                user_agent=user_agent_from(request),
            ).insert_log()
        except Exception:
            _logger.exception("写入 device_required 日志失败")

        raise HTTPException(
            status_code=403,
            detail={
                "code": "DEVICE_REQUIRED",
                "message": device_guard.DEVICE_REQUIRED_MESSAGE,
            },
        )

    # 未绑定主设备的账号仍放行：存量账号从没绑过，锁死等于全站登录不可用。
    if not primary or str(primary) == xuehai_id:
        return

    # challenge 必须同步签好：它要出现在 403 的响应体里。旁边的审计与风控
    # 不参与这个响应，放到响应之后跑。
    try:
        challenge = await device_guard.issue_challenge(user_oid, xuehai_id)
    except Exception:
        _logger.exception("签发设备挑战令牌失败")
        challenge = ""

    _spawn_background(
        _log_device_mismatch_then_evaluate(
            request, user_oid, log_name, xuehai_id, primary
        )
    )

    raise HTTPException(
        status_code=403,
        detail={
            "code": "DEVICE_NOT_PRIMARY",
            "message": device_guard.DEVICE_NOT_PRIMARY_MESSAGE,
            "challenge": challenge,
        },
    )


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

    try:
        await login_throttle.check(id, client_ip_from(request))
    except login_throttle.LoginRateLimited as exc:
        raise HTTPException(
            status_code=429,
            detail={"code": exc.code, "message": exc.message},
            headers={"Retry-After": str(exc.retry_after)},
        )

    # 整条认证链共用**一个** session：每次 repo 调用各开一个 session 就是一次
    # checkout（跨洋 138ms），而这里连着读的其实是同一批表。共享 session 只
    # 承载读，退出时不 commit（repo 在「外部传入 session」模式下不提交也不关闭）。
    async with session_scope() as session:
        users_repo = backends.get_users_repo(session)

        # 三步顺序（设计 §5）：设备闸 → 凭据校验 → 主设备闸。
        # 设备闸的库读在 services/device_guard.py 内自开 session，不在这份共享
        # 范围内（改动它超出本次范围）。
        await _reject_banned_device(xuehai_id)

        if string_to_option_object_id(id) is None:
            user_doc = await users_repo.find_by_business_id(id)
            if user_doc:
                id = str(user_doc["_id"])
            else:
                # Indistinguishable from a wrong password: same status and, below,
                # the same body, so a probe cannot tell whether the 学号 exists.
                raise HTTPException(
                    status_code=403,
                    detail="用户名或密码错误",
                )
        else:
            user_doc = None

        user_oid = str(validate_object_id(id))
        if user_doc is None:
            # oid 直达时没有「学号 → 文档」那一次读，这里补上唯一的一次，随后
            # 交给 validate_by_cert 复用（它本来也要读这一行）。
            user_doc = await users_repo.get_by_oid(user_oid)

        # Server-side clamp: a verification request must never issue a session
        # token, whatever ``mode`` says. Routing it through the short-token path
        # keeps ``validate_by_cert`` (and thus the login path) untouched, while
        # TOTP credentials remain accepted and yield a 5-minute temporary token.
        validation_mode = mode if is_login else "short"

        try:
            # 把上面读到的这一行交给 validate_by_cert：封禁判定、密码校验、
            # token 里的 eligibility/session_id 用的都是同一份数据，只是不再
            # 为它多跑一次跨洋往返。
            async with prefetched_user(user_oid, user_doc):
                result, weak_password = await validate_by_cert(
                    id, credential, validation_mode
                )
        except HTTPException as exc:
            # 失败分支同样要留证据（摘要，不含凭据）：这两条规则只认服务端自己
            # 写下的行。封禁态走 ban_evasion_retry，密码错走 login_failure_burst。
            # 证据与判定合成一个后台任务：顺序写反就数不到本次失败。
            if exc.detail == "Password incorrect":
                failed_oid = _presented_oid(id)
                if failed_oid:
                    _spawn_background(
                        _log_failure_then_evaluate(
                            request,
                            failed_oid,
                            xuehai_id,
                            "password-mismatch",
                            "login_failure_burst",
                        )
                    )
                # Normalize the credential failure to the same body as "user not found",
                # keeping the two paths byte-identical.
                raise HTTPException(status_code=403, detail="用户名或密码错误")
            if exc.detail == "USER_BANNED":
                failed_oid = _presented_oid(id)
                if failed_oid:
                    _spawn_background(
                        _log_failure_then_evaluate(
                            request,
                            failed_oid,
                            xuehai_id,
                            "banned",
                            "ban_evasion_retry",
                        )
                    )
            raise

        # user_doc 是上面读过的那一份（可能是 None），不再重复 get_by_oid。
        # None 时保留 get_user_name 兜底：oid 直达且库读不到时仍要有个名字。
        log_name = user_doc["name"] if user_doc else await get_user_name(id)

        # v4.2.4-2 R4（设计 §3.2）：维护期间只放行 admin —— 非 admin 一律拒绝下发
        # token。拒绝必须落在端点内：``/api/users/auth`` 是中间件的维护豁免路径
        # （main.py:136-144），否则管理员无法登录关掉维护。
        # 角色取刚落库读到的这一份 user_doc，不额外查库；读不到文档按「非 admin」
        # 处理（fail-closed，宁拒不放）。
        if is_enabled_fresh() and "admin" not in ((user_doc or {}).get("roles") or []):
            try:
                await ZVMSLog(
                    str(request.url),
                    id,
                    f"维护模式拒绝用户 {log_name} 登录（非管理员）",
                    datetime.timestamp(datetime.now()),
                    type=log_events.AUTH_MAINTENANCE_BLOCKED,
                    level="warning",
                    xuehai_id=xuehai_id,
                    client_ip=client_ip_from(request),
                    user_agent=user_agent_from(request),
                ).insert_log()
            except Exception:
                # 审计写失败不得把 503 变成 500：拒绝本身仍然生效。
                _logger.exception("failed to write auth_maintenance_blocked log")
            # 与维护中间件的 503 体逐字同形（main.py:169-172）：前端
            # plugins/axios.ts:129-140 靠顶层 ``maintenance: true`` 识别维护态，
            # 既不清 token 也不弹「登录已过期」。
            return JSONResponse(
                status_code=503,
                content={"detail": "系统维护中，仅管理员可登录", "maintenance": True},
            )

        is_otp = used_otp_credential(credential, id)
        if is_login:
            await _guard_primary_device(request, user_doc, user_oid, log_name, xuehai_id)
            if is_otp:
                log_text = f"用户 {log_name} 使用动态码登录了系统"
            elif weak_password and mode == "long":
                log_text = f"用户 {log_name} 登录了系统（密码强度不足，仅发放临时授权）"
            else:
                log_text = f"用户 {log_name} 登录了系统"
            log_type = log_events.USER_LOGIN
        else:
            credential_kind = "动态码" if is_otp else "密码"
            log_text = f"用户 {log_name} 完成了身份验证（{credential_kind}）"
            log_type = log_events.USER_VERIFY
        log = ZVMSLog(
            str(request.url),
            id,
            log_text,
            datetime.timestamp(datetime.now()),
            type=log_type,
            xuehai_id=xuehai_id,
            client_ip=client_ip_from(request),
            user_agent=user_agent_from(request),
        )

        # 登录成功的审计与 churn 风控都不决定响应内容，挪到响应之后。
        _spawn_background(
            _log_login_then_evaluate(
                log, user_oid, "login_success_churn" if is_login else None
            )
        )

        # 绑定判定要回填 ``binding_mismatch``，读必须同步；写与日志已挪到后台。
        binding_mismatch = await _observe_binding(
            request, user_oid, log_name, xuehai_id, session
        )

        profile = _profile_payload(user_doc, id)

        response.headers["Cache-Control"] = "no-store"
        return {
            "token": result,
            "_id": id,
            "profile": profile,
            "weak_password": weak_password,
            "binding_mismatch": binding_mismatch,
        }


@router.post("/auth/logout")
async def logout_user(
    request: Request,
    response: Response,
    user=Depends(get_current_user),
):
    """退出登录：轮换该用户的 ``session_id``，旧 token 即刻失效。

    轮换而不是「删掉 token」：JWT 无状态，服务端能做的唯一撤销就是让每请求
    实时校验的 ``sid`` 与库里的 ``session_id`` 对不上（见
    ``util.object_id._authenticate``）。代价是同一账号的其他登录会话同时下线，
    这是既定口径——sid 是账号级而非设备级。
    """
    await backends.get_users_repo().rotate_session(user["id"])
    try:
        await ZVMSLog(
            str(request.url),
            user["id"],
            "用户退出登录",
            datetime.timestamp(datetime.now()),
            type=log_events.USER_LOGOUT,
            level="info",
            xuehai_id=xuehai_id_from(request),
            client_ip=client_ip_from(request),
            user_agent=user_agent_from(request),
        ).insert_log()
    except Exception:
        _logger.exception("写入 user_logout 日志失败")
    response.headers["Cache-Control"] = "no-store"
    return {"status": "ok"}


class DeviceAuthUser(BaseModel):
    challenge: str
    temp_password: str


@router.post("/auth/device")
async def auth_user_by_temp_password(
    auth: DeviceAuthUser, request: Request, response: Response
):
    """临时密码登入（异设备登录的唯一正路）。

    挑战令牌与临时密码都是**一次一用**：``verify_temp_password`` 内部
    ``mark_used`` + ``consume`` 同时置位，所以即使两个请求并发拿着同一对凭据，
    也只有一个能拿到 token。签发的 token 只有 1 小时有效期且带 ``tp`` claim——
    这条链路本身是安全事件，会话不该和正常登录一样活 10 天。

    兑换前的两道闸与 ``/auth`` 同源：

    1. 请求头设备必须就是挑战签发时那台机器。挑战是**这台设备**的单次许可，
       换台机器兑换等于把它变成可转手的通用凭据；这里只拒绝，不消耗 attempts
       （否则拿着别人的 challenge 连打几次就能把号刷到封禁）；
    2. 设备封禁闸（头里没带设备号时退回挑战里的设备号）。被拉黑的机器连
       兑换的机会都不该有，也不该拿到 token。
    """
    xuehai_id = xuehai_id_from(request)

    challenge_xuehai = await device_guard.challenge_device(auth.challenge)
    if xuehai_id and challenge_xuehai and xuehai_id != challenge_xuehai:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "DEVICE_CHALLENGE_MISMATCH",
                "message": device_guard.CHALLENGE_MISMATCH_MESSAGE,
            },
        )

    await _reject_banned_device(xuehai_id or challenge_xuehai or "")

    user_oid, challenge_xuehai = await device_guard.verify_temp_password(
        auth.challenge, auth.temp_password, request_url=str(request.url)
    )

    user_doc = await backends.get_users_repo().get_by_oid(user_oid)
    if user_doc is None:
        raise HTTPException(status_code=404, detail="User not found")
    # 连错 3 次会把账号一起封掉；即便还有人拿着有效临时密码，也必须先解封。
    if ban_is_active(user_doc):
        raise_banned(user_doc)

    token = jwt_encode(
        user_oid,
        user_doc.get("eligibility", []),
        await get_user_permissions(user_doc),
        "long",
        session_id=user_doc.get("session_id", ""),
        purpose="login",
        expires_in=timedelta(hours=device_guard.TEMP_SESSION_HOURS),
        tp=True,
    )

    log_name = user_doc.get("name") or user_oid
    try:
        await ZVMSLog(
            str(request.url),
            user_oid,
            f"用户 {log_name} 使用临时密码在设备 {challenge_xuehai} 登入",
            datetime.timestamp(datetime.now()),
            type=log_events.TEMP_PASSWORD_LOGIN,
            level="warning",
            xuehai_id=xuehai_id or challenge_xuehai,
            client_ip=client_ip_from(request),
            user_agent=user_agent_from(request),
        ).insert_log()
    except Exception:
        # 审计写失败不能把「已验证成功的临时密码」变成登录失败：密码是一次性
        # 的，拒绝这次等于让用户回去重新申请一张。
        _logger.exception("写入 temp_password_login 日志失败")

    primary = user_doc.get("primary_xuehai_id")
    response.headers["Cache-Control"] = "no-store"
    return {
        "token": token,
        "_id": user_oid,
        "profile": _profile_payload(user_doc, user_oid),
        "weak_password": False,
        "binding_mismatch": bool(
            primary and (xuehai_id or challenge_xuehai) != str(primary)
        ),
        "temp_session": True,
    }


# ---------------------------------------------------------------------------
# 主学海设备 / 设备绑定
#
# 设备身份取自请求头 ``Xuehai-Id``，不是请求体：请求体里的值只能说明「客户端
# 声称自己是谁」，请求头也是，但请求头是整条登录链路（登录、日志、封禁）唯一
# 认可的来源，自助改绑必须与之一致，否则等于允许任何令牌持有者替任意平板注册。
# ---------------------------------------------------------------------------

_DEVICE_ID_MAX_LEN = 32


async def _get_zvms_new_user(oid: str, session=None) -> Optional[dict]:
    validate_object_id(oid)
    return await backends.get_users_repo(session).get_by_oid(oid)


async def _device_ban_status(
    xuehai_id: str,
    session=None,
) -> tuple[bool, Optional[str], Optional[datetime]]:
    """设备封禁状态统一问 :mod:`services.device_guard`。

    服务层在并行车道落地，这里延迟导入：路由模块不因服务层尚未就绪而在导入期
    失败，同时保证常量与判定逻辑只有一份实现（本层不重复推导账号封禁联动）。

    ``session`` 交给服务层复用本请求的事务，省掉「一次请求开三条连接」；替身
    （离线单测）只实现单参形态，按能力探测回落到原调用，行为不变。
    """
    from services.device_guard import device_ban_status

    if session is None:
        return await device_ban_status(xuehai_id)
    if "session" in inspect.signature(device_ban_status).parameters:
        return await device_ban_status(xuehai_id, session=session)
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


@router.get("/me/device")
async def get_my_device(request: Request, user=Depends(get_current_user)):
    """当前请求所携带的设备身份 + 本人主设备绑定/封禁状态。

    ``xuehai_id`` 是请求头原值（经 ``xuehai_id_from`` 规整，缺失/非法即空串），
    前端用它区分「没带设备头」和「带了但不是主设备」，因此不做 400。
    """
    xuehai_id = xuehai_id_from(request)

    device_banned, ban_reason, ban_until = False, None, None
    # 本人的主设备与设备封禁判定共用一个 session：只读，不提交（close 即回滚）。
    async with session_scope() as session:
        current = await _get_zvms_new_user(user["id"], session)
        if xuehai_id:
            # 空设备号不代表任何设备，拿它去查只会命中脏数据行。
            device_banned, ban_reason, ban_until = await _device_ban_status(
                xuehai_id, session
            )

    return {
        "status": "ok",
        "code": 200,
        "data": {
            "xuehai_id": xuehai_id,
            "primary_xuehai_id": current.get("primary_xuehai_id") if current else None,
            "device_banned": device_banned,
            "device_ban_reason": ban_reason,
            "device_ban_until": iso_seconds(ban_until),
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


# ---------------------------------------------------------------------------
# 集合：列表 / 创建
# ---------------------------------------------------------------------------


@router.get("")
async def get_users_v2(
    page: int = Query(1, ge=1, description="Page number"),
    size: int = Query(10, ge=1, le=1000, description="Page size"),
    query: str = Query("", max_length=64, description="Search keyword"),
    role: str = Query(""),
    user=Depends(get_current_user),
):
    """
    List users from the live `zvms_new.users` collection.

    Permission: admin (when filtering by role) or admin/department otherwise.
    """
    per = user.get("per") or []
    if role:
        if "admin" not in per:
            raise HTTPException(status_code=403, detail="权限不足")
    elif not ("admin" in per or "department" in per):
        raise HTTPException(status_code=403, detail="权限不足")

    repo = backends.get_users_repo()
    rows, total = await repo.list_page(query, role, page, size)

    for row in rows:
        row.pop("has_password", None)
        row["group"] = [row["class_id"]] if row["class_id"] else []

    return {
        "data": rows,
        "total": total,
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
    """建号 / 改学号两处共用的**轻校验**：只保证「存得下、查得回、能当登录名」。

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
    # 改学号端点用的是同一个 _normalize_student_id——它是纠错入口，口径必须与
    # 建号一致，否则「建得出来却改不回去」。
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


# ---------------------------------------------------------------------------
# 单个用户
# ---------------------------------------------------------------------------


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

    Permission: admin only，照抄 ``groups_router.delete_group_v3`` 的形状。

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


# ---------------------------------------------------------------------------
# 凭据
# ---------------------------------------------------------------------------


class PutPassword(BaseModel):
    credential: str


@router.put("/{user_id}/password")
async def change_password(
    user_id: str,
    credential: PutPassword,
    user=Depends(password_change_token),
    log=Depends(inject_log),
):
    # Validate user's permission
    if user.get("purpose") == "password_change" and user["id"] != user_id:
        raise HTTPException(status_code=403, detail="初始密码授权仅限修改本人密码")
    secretary = "secretary" in user["per"] and await is_in_a_same_class(
        user["id"], user_id
    )
    modification = user["id"] == user_id and user["scope"] == "temporary_token"
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
    oid = str(validate_object_id(user_id))
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
            f"用户 {await get_user_display_name(user_id)} 修改了自己的密码",
            type="passwd_change",
        )
    else:
        log.with_text(
            f"用户 {await get_user_display_name(user['id'])} 重置了用户 {await get_user_display_name(user_id)} 的密码",
            type="passwd_change",
        )
    await log.insert_log()

    return {
        "status": "ok",
        "code": 200,
    }


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


# ---------------------------------------------------------------------------
# 活动 / 时长 / 日志
# ---------------------------------------------------------------------------


@router.get("/{user_id}/activities")
async def get_user_activities_v2(
    user_id: str,
    page: int = Query(1, ge=1, description="Page number for pagination"),
    perpage: int = Query(10, ge=1, le=100, description="Number of items per page"),
    search: str = Query("", max_length=64, description="Search keyword"),
    sort: str = Query("_id", description="Sort by field"),
    regex: bool = Query(False, description="已废弃：关键字一律按字面量匹配，不再走正则"),
    asc: bool = Query(False, description="Sort in ascending order"),
    user=Depends(get_current_user),
):
    """
    Get user activities
    :param user_id: User ID
    :param page: The page number
    :param perpage: Items per page
    :param search: Search keyword
    :param sort: Sort by field
    :param regex: Use regex for search
    :param asc: Sort in ascending order
    :param user: Current user
    """
    await validate_read_user_permission(user, user_id, "volunteer")

    # Check if the user has permission to view the activities
    # skipped right now

    # 关键字是字面量（仓储侧转义 ``%``/``_``/``\\`` 并 lower 比较）；``regex``
    # 仅为兼容旧客户端保留。
    rows, total = await backends.get_members_repo().list_for_user_activities(
        user_id, search, sort, asc, page, perpage
    )

    activities = []

    for row in rows:
        activity = row["activity"]
        if activity is None:
            continue
        mine = row["member"]
        mine.pop("activity")
        mine["_id"] = str(mine["_id"])
        activity["mine"] = mine
        activity["_id"] = str(activity["_id"])
        activities.append(activity)

    return {
        "page": page,
        "perpage": perpage,
        "total": total,
        "activities": activities,
    }


@router.get("/{user_id}/time")
async def get_user_time_v2(
    user_id: str, user=Depends(get_current_user), allow_cache: bool = True
):
    """
    Get user time

    :param user_id: User ID
    :param user: Current user
    :param allow_cache: If False, recompute and refresh the cache instead of reading it

    :return: User time
    """
    await validate_read_user_permission(user, user_id, "volunteer")

    result = await calculate_user_time(user_id, allow_cache=allow_cache)

    return result


@router.get("/{user_id}/time_statistics")
async def get_user_time_statistics_v2(
    user_id: str, user=Depends(get_current_user)
):
    """
    Get user time statistics, particularly `percentiles`.

    :param user_id: User ID
    :param user: Current user

    :return: User time statistics
    """
    await validate_read_user_permission(user, user_id, "volunteer")

    user_oid = str(validate_object_id(user_id))
    user_document = await backends.get_users_repo().get_by_oid(user_oid)
    if user_document is None:
        raise HTTPException(status_code=404, detail="User not found")
    group_id = user_document.get("class_id")
    if not group_id:
        raise HTTPException(status_code=404, detail="User not in any class")
    if await backends.get_groups_repo().get(group_id) is None:
        raise HTTPException(status_code=404, detail="User not in any class")
    stats_repo = backends.get_stats_repo()
    indicators = await stats_repo.get_indicators()
    group_rows = await stats_repo.list_group_indicators()
    group_row = next(
        (row for row in group_rows if str(row.get("group")) == str(group_id)),
        None,
    )
    if indicators is None or group_row is None:
        raise HTTPException(
            status_code=400, detail="Function temporarily unaccessible."
        )
    percentiles = indicators.get("percentiles")
    grade_indicators = (
        percentiles.get(user_document["id"][:4])
        if isinstance(percentiles, dict)
        else None
    )
    group_indicators = group_row.get("percentiles")
    if not isinstance(grade_indicators, dict) or not isinstance(
        group_indicators, dict
    ):
        raise HTTPException(
            status_code=400, detail="Statistics are not available for this cohort."
        )
    user_time = await calculate_user_time(user_id, allow_cache=True)
    return {
        "on-campus": {
            "value": user_time["on-campus"],
            "group": find_percentile_threshold(
                group_indicators["on-campus"], user_time["on-campus"], "on-campus"
            ),
            "grade": find_percentile_threshold(
                grade_indicators["on-campus"], user_time["on-campus"], "on-campus"
            ),
        },
        "off-campus": {
            "value": user_time["off-campus"],
            "group": find_percentile_threshold(
                group_indicators["off-campus"], user_time["off-campus"], "off-campus"
            ),
            "grade": find_percentile_threshold(
                grade_indicators["off-campus"], user_time["off-campus"], "off-campus"
            ),
        },
        "social-practice": {
            "value": user_time["social-practice"],
            "group": find_percentile_threshold(
                group_indicators["social-practice"],
                user_time["social-practice"],
                "social-practice",
            ),
            "grade": find_percentile_threshold(
                grade_indicators["social-practice"],
                user_time["social-practice"],
                "social-practice",
            ),
        },
    }


# ---------------------------------------------------------------------------
# 动态码（管理员侧）
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# 角色 / 学号 / 封禁
# ---------------------------------------------------------------------------

AllowedRole = Literal["student", "secretary", "department", "admin"]


class PutRolesRequest(BaseModel):
    roles: list[AllowedRole]


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

    Permission: admin only。**不校验学号格式**，只保三件事：非空 / 长度不超过
    列宽 / 唯一。格式校验与建号口共用同一套轻校验
    :func:`_normalize_student_id`（strip、≤32 字符、禁空白与控制字符）——班级
    自定的编号规则不该被平台的 8 位数字约定拦住。

    校验三层：轻校验（失败 → **400**，文案见 :func:`_normalize_student_id`）+
    唯一性闸（重复 → **409**，与创建用户的既有契约一致）+ 数据库唯一约束
    ``uq_users_business_id`` 兜底（并发窗口下的写入被约束拒绝时同样转 409，
    不再冒泡成 500）。

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

    student_id = _normalize_student_id(payload.id)

    existing = await repo.find_by_business_id(student_id)
    if existing is not None and str(existing["_id"]) != str(oid):
        raise HTTPException(status_code=409, detail="学号已存在")

    try:
        await repo.set_business_id(str(oid), student_id)
    except IntegrityError:
        # 预检与写入之间的并发抢占：唯一约束是最终裁决者。
        raise HTTPException(status_code=409, detail="学号已存在")

    log.with_text(
        f"管理员 {await get_user_display_name(user['id'])} 将用户 {target['name']} 的学号由 {target['id']} 改为 {student_id}",
        type="student_id_change",
    )
    await log.insert_log()

    return


class PutBannedRequest(BaseModel):
    banned: bool
    # 原因随请求一起落库（USERS.BAN_REASON + BAN_RECORDS），前端封禁弹窗可填；
    # 512 与 BAN_REASON / BAN_RECORDS.REASON 的列宽一致，超长在这里就 422。
    reason: Optional[str] = Field(default=None, max_length=512)
    # 封禁时长（天）。None = 永久（与历史行为一致）；只收天数、不收客户端时间戳：
    # 时间在服务端按本地时区算，避免浏览器时区把「7 天」折算成别的时刻。
    duration_days: Optional[int] = Field(default=None, ge=1, le=36500)


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
            "ban_until": iso(ban_until),
            "permanent": payload.banned and ban_until is None,
        },
    }


# ---------------------------------------------------------------------------
# 历史身份（past）
# ---------------------------------------------------------------------------


class PostPast(BaseModel):
    past: str


@router.post("/{user_id}/past")
async def add_past(
    user_id: str,
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
    await users_repo.add_past(str(validate_object_id(user_id)), past.past)
    log.with_text(
        f"管理员 {await get_user_display_name(user['id'])} 添加了用户 {await get_user_display_name(user_id)} 的历史身份 {past.past}",
        type="past_change",
    )
    await log.insert_log()
    return {"code": 200, "status": "ok"}


@router.delete("/{user_id}/past/{past_identity_idx}")
async def delete_past(
    user_id: str,
    past_identity_idx: str,
    user=Depends(get_current_user),
    log=Depends(inject_log),
):
    if "admin" not in user["per"]:
        raise HTTPException(status_code=403, detail="Permission denied")
    users_repo = backends.get_users_repo()
    oid = str(validate_object_id(user_id))
    # Remove by index
    user_doc = await users_repo.get_by_oid(oid)
    name = (user_doc or {}).get("past", [])
    try:
        idx = int(past_identity_idx)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="无效的历史身份序号")
    if idx < 0 or idx >= len(name):
        raise HTTPException(status_code=404, detail="Past identity not found.")
    await users_repo.remove_past(oid, name[idx])
    log.with_text(
        f"管理员 {await get_user_display_name(user['id'])} 删除了用户 {await get_user_display_name(user_id)} 的历史身份 {name[idx]}",
        type="past_change",
    )
    await log.insert_log()
    return {"code": 200, "status": "ok"}


# ---------------------------------------------------------------------------
# 主设备 / 临时密码（管理员侧）
# ---------------------------------------------------------------------------


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
            "expires_at": iso_seconds(expires_at),
            "target": {
                "_id": str(target["_id"]),
                "id": target.get("id"),
                "name": target.get("name"),
            },
        },
    }
