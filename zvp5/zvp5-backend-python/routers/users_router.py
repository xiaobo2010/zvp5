import logging
import re
from datetime import datetime, timedelta
from typing import Literal, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel
import asyncio
from typings.log import (
    inject_log,
    ZVMSLog,
    client_ip_from,
    user_agent_from,
    xuehai_id_from,
)
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
from db.oracle.session import session_scope
from db.system_state import is_enabled_fresh
from services import device_guard, log_events, risk_monitor
from util.ban import ban_headers, ban_is_active, raise_banned
from util import login_throttle, read_throttle
from util.cert import (
    get_hashed_password_by_cert,
    jwt_encode,
    prefetched_user,
    used_otp_credential,
    validate_by_cert,
)
from util.user import get_user_display_name, get_user_name
from util.validation import validate_number

router = APIRouter()

_logger = logging.getLogger(__name__)

# ``page=-1``（不分页）的硬上限：保留「一次拿完」的语义，但不允许无界返回。
_USER_LIST_CAP = 1000

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


def _query_datetime(value: str, field: str) -> datetime:
    try:
        return datetime.fromisoformat(value)
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail=f"无效的时间参数：{field}")


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


class AuthUser(BaseModel):
    id: str
    mode: Literal["long", "short"] = "long"
    credential: str
    purpose: str = "login"


class DeviceAuthUser(BaseModel):
    challenge: str
    temp_password: str


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


@router.post("")
async def create_user(
    target_user: User,
    actioner=Depends(compulsory_temporary_token),
):
    raise HTTPException(
        status_code=503,
        detail="仅允许读取：用户创建请使用 POST /api/v1/users",
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
    request: Request,
    query: str = Query("", max_length=128),
    page: int = Query(1, ge=-1),
    perpage: int = Query(5, ge=1, le=100),
    privilege: bool = False,
    user: Optional[str] = Depends(optional_current_user),
):
    """
    Query users
    """
    # 匿名分支按 IP 记账：未登录也能按学号精确查姓名，不限速就是一条无成本
    # 的学号枚举通道。登录用户的路径与额度不受影响。
    if user is None:
        try:
            await read_throttle.check_anonymous(client_ip_from(request))
        except read_throttle.ReadRateLimited as exc:
            raise HTTPException(
                status_code=429,
                detail={"code": exc.code, "message": exc.message},
                headers={"Retry-After": str(exc.retry_after)},
            )
    if privilege:
        groups = await backends.get_groups_repo().list_permission_groups(
            permissions=["admin", "department"]
        )
        selected = []
        for group in groups:
            selected.append(str(group["_id"]))

    # 分页下推到 SQL：``page == -1`` 仍表示「不分页」，但必须给硬上限——
    # 不限量返回才是这条路径原来的问题。``perpage`` 本身已由 Query(le=100)
    # 夹住，所以只有 -1 分支需要这个天花板。
    # 这个 ``-1`` 哨兵刻意不交给 ``db.repos.pagination.page_args``：它会把页
    # 号钳到 1，而这里要的是「一次全量（截断到 CAP）」的另一种语义。
    limit = _USER_LIST_CAP if page == -1 else perpage
    offset = 0 if page == -1 else (page - 1) * perpage
    users, count = await backends.get_users_repo().search(
        query, user is not None, limit=limit, offset=offset
    )

    # 行序由 SQL 的 ``ORDER BY business_id, oid`` 决定（与这里原来的
    # ``rows.sort(key=row["id"])`` 同序），因此不再整页重排、也不再切片。
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

    return {"status": "ok", "code": 200, "data": rows, "metadata": {"size": count}}


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
        # 与 v3 profile 同一口径：到期封禁列仍是 1，但不该显示为封禁中。
        "banned": ban_is_active(new_user),
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
        detail="仅允许读取：用户分组请使用 POST /api/v1/groups/{group_id}/users/{user_id}",
    )


@router.delete("/{user_oid}/group/{group_id}")
async def remove_user_from_group(user_oid: str, group_id: str):
    raise HTTPException(
        status_code=503,
        detail="仅允许读取：用户移出分组请使用 DELETE /api/v1/groups/{group_id}/users/{user_id}",
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

    # 分页、过滤、排序全部下推到 SQL：与 ``users_v2`` 的 ``/{user_id}/activities``
    # 用同一个孪生体（``COUNT(*) OVER()`` 随页带回总数），不再「拉该人全部成员行
    # + 按 oid 拉全部活动 + Python 正则过滤/排序/切片」。
    # ``page == -1`` 是老客户端的「一次全量」哨兵：交给同一条查询但不分页
    # （``size=None``），不能用 ``page_args`` 归一。
    rows, total = await backends.get_members_repo().list_for_user_activities(
        user_key,
        query,
        "_id",
        False,
        page,
        None if page == -1 else perpage,
    )

    # 同一活动的多条（多 mode）成员行合成一条：对外仍是「一个活动 + 该人在该
    # 活动的全部成员记录」。行序即 SQL 的排序（活动 oid 倒序），同一活动的行相邻。
    members_by_activity: dict[str, list] = {}
    activities: list = []
    for row in rows:
        activity = row["activity"]
        if activity is None:
            continue
        activity_id = str(activity["_id"])
        if activity_id not in members_by_activity:
            members_by_activity[activity_id] = []
            activities.append(activity)
        membership = row["member"]
        members_by_activity[activity_id].append(
            {
                "_id": str(membership["member"]),
                "status": membership.get("status"),
                "duration": membership.get("duration"),
                "mode": membership.get("mode"),
            }
        )

    # `metadata.size` 与 v2 孪生体同口径：`COUNT(*) OVER()` 统计的是**成员行**
    # （同一活动的多 mode 行各计一次），且随关键字谓词变化。与旧实现的
    # 「distinct 活动数、忽略过滤」略有差异；legacy v1 无前端调用方，统一到 v2。
    count = total

    data = []
    for activity in activities:
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
            user_oid,
            _query_datetime(start, "start"),
            _query_datetime(end, "end"),
        )
    else:
        result = await calculate_user_time(user_oid)
    # ``calculate_user_time`` 只回三档时长（on-campus / off-campus /
    # social-practice），从不带 ``total``：这里求和补齐。响应字段与其它时长端点
    # （``/api/exports/time``、``/api/activities/{oid}/time``）保持一致，不再因为
    # 直接读 ``result["total"]`` 把 GET /api/users/{oid}/time 打成 500。
    on_campus = result["on-campus"]
    off_campus = result["off-campus"]
    social_practice = result["social-practice"]
    return {
        "status": "ok",
        "code": 200,
        "data": {
            "onCampus": on_campus,
            "offCampus": off_campus,
            "socialPractice": social_practice,
            # 走 USER_TIME 缓存时三个值是 NUMBER（Decimal），现算时是 float；
            # 统一 float() 后相加，避免 Decimal + float 的 TypeError。
            "total": float(on_campus) + float(off_campus) + float(social_practice),
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
    try:
        idx = int(past_identity_idx)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="无效的历史身份序号")
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

