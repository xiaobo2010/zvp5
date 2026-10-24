"""主学海设备绑定、临时密码登入与设备联动封禁。

**为什么把「设备」当成独立主体**：账号密码正确也可能来自一台不属于本人的
设备（借来的平板、被人抄走的学号+密码）。旧的 observe-only 绑定只记日志不拦，
等于把「设备是身份的一部分」这条事实丢掉。这里的设备闸与账号闸**互不代替**：

* 设备闸（:func:`device_ban_status`）判的是「这台机器还能不能登」，
  命中时**绝不封当前账号**——同一台机器可能马上有别人来登；
* 账号闸仍由 ``util.ban`` / ``services.ban_rules`` 负责。

**为什么不物理同步账号封禁与设备封禁**：只有「临时密码连错 3 次」这一类
证据能确认「有人在这台机器上冒用身份」，才写显式 ``BANNED_DEVICES`` 行；
普通的账号封禁（平台违规使用、管理员封禁）靠 :func:`device_ban_status` 的第二个
条件**实时推导**（主设备=该机器且该用户此刻被封）。账号解封、用户换主设备后
设备封禁自动消失，不需要任何反向同步任务，也就没有「两边不一致」的状态。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import secrets
from datetime import datetime, timedelta
from typing import Any

import bcrypt
from fastapi import HTTPException

from db import backends
from services import ban_rules
from typings.log import ZVMSLog
from util.ban import DEFAULT_BAN_REASON, ban_is_active, ban_reason_of

_logger = logging.getLogger(__name__)

TEMP_PASSWORD_TTL_MINUTES = 30
TEMP_PASSWORD_CODE_LEN = 8
TEMP_SESSION_HOURS = 1
DEVICE_CHALLENGE_TTL_MINUTES = 5
# 产品默认值：真正生效的上限读规则的 ``config["threshold"]``（旧键 max_attempts 兼容），
# 都读不到才回落这里。
MAX_TEMP_ATTEMPTS = 3
# 一次登录最多 bcrypt 比对几条有效临时密码。签发新码时会作废该用户此前的未用码，
# 正常情况下窗口里只有 1 条；上限留宽到 20 是为了应付「作废失败/并发签发」的
# 残留——3 条会把用户手里仍然有效的旧码挡在扫描之外，正确密码被判错。
MAX_TEMP_PASSWORDS_SCANNED = 20
# 跨挑战累计失败上限：换一张挑战就重置 attempts，等于每次都有 3 次机会无限穷举。
# 24 小时窗口内的失败总次数才是「有人在冒用」的证据。
CUMULATIVE_TEMP_FAILURES = 5
CUMULATIVE_TEMP_FAILURE_HOURS = 24
RULE_CODE_FOREIGN_DEVICE = "foreign_device_login"

# 团支书及以上：不受「必须学海设备」与主设备闸约束，可自由换设备登入。
# teacher 也在此列——教师没有主学海设备，按学生口径要求会把教师账号整体锁死。
PRIVILEGED_ROLES = frozenset({"admin", "department", "secretary", "teacher"})

DEVICE_BANNED_MESSAGE = "这台设备由于违规使用，已被限制平台访问。"
DEVICE_NOT_PRIMARY_MESSAGE = (
    "正在尝试登入不属于本机的主学海设备账号；"
    "如确需在本机登入，请让团支书或管理员签发临时密码。"
)
DEVICE_REQUIRED_MESSAGE = (
    "普通用户需使用学海设备登录；"
    "如确需在其它设备登入，请让团支书或管理员签发临时密码。"
)
TEMP_PASSWORD_INVALID_MESSAGE = "临时密码错误或已失效"
CHALLENGE_MISMATCH_MESSAGE = "临时密码只能在一台设备上使用"
DEVICE_BAN_APPLIED_MESSAGE = (
    f"临时密码连续输错 {MAX_TEMP_ATTEMPTS} 次，账号与这台设备已被封禁"
)

Row = dict[str, Any]


class DeviceBanned(Exception):
    """设备封禁事实（``reason`` / ``until`` 供调用方拼 403 契约）。

    刻意不是 ``HTTPException``：服务层只回答「封没封、为什么、到什么时候」，
    状态码与响应头是路由层的表达方式。抛 ``HTTPException`` 会让服务层无法被
    非 HTTP 调用方（脚本、任务）复用。
    """

    def __init__(self, reason: str | None = None, until: datetime | None = None):
        super().__init__(reason or DEVICE_BANNED_MESSAGE)
        self.reason = reason
        self.until = until


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------


def has_privileged_role(roles: Any) -> bool:
    """``roles`` 缺失 / ``None`` / 坏类型一律按学生处理（无豁免）。

    登录路径上不能用异常表达未知角色：一条脏数据只能让本人走学生口径，
    不能把登录打成 500。
    """
    if not isinstance(roles, (list, tuple, set, frozenset)):
        return False
    return bool(PRIVILEGED_ROLES & {role for role in roles if isinstance(role, str)})


def _token_hash(token: str) -> str:
    """challenge 只存 SHA-256：明文 token 泄露等于交出一次登入机会。

    这里不需要慢哈希：token 是 32 字节 urlsafe 随机串（256 bit 熵），
    字典攻击不成立，而每次登录都要算一次，bcrypt 只会拖慢登录。
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _check_code(code: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(code.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError, AttributeError):
        # 坏 hash（空串/非 bcrypt 格式）必须是「校验失败」，不是 500。
        return False


def hash_temp_password(code: str) -> str:
    """临时密码只在创建响应里出现一次，落库的永远是 bcrypt。"""
    return bcrypt.hashpw(code.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


async def hash_temp_password_async(code: str) -> str:
    """``hash_temp_password`` 的线程池版本：cost 12 不能在事件循环里等。"""
    return await asyncio.to_thread(hash_temp_password, code)


def generate_temp_password() -> str:
    """8 位纯数字：人工念得出来，熵由「单次 + 30 分钟 + 3 次尝试」兜底。"""
    return "".join(str(secrets.randbelow(10)) for _ in range(TEMP_PASSWORD_CODE_LEN))


def _as_until(value: Any) -> datetime | None:
    return value if isinstance(value, datetime) else None


def _expired(until: datetime | None, now: datetime) -> bool:
    """``until=None`` 是永久（不算过期）；其余交给 ``ban_is_active`` 比时区口径。"""
    if until is None:
        return False
    return not ban_is_active({"banned": True, "ban_until": until}, now)


def _invalid_temp_password(remaining: int) -> HTTPException:
    return HTTPException(
        status_code=403,
        detail={
            "code": "TEMP_PASSWORD_INVALID",
            "message": TEMP_PASSWORD_INVALID_MESSAGE,
            "remaining": max(remaining, 0),
        },
    )


# ---------------------------------------------------------------------------
# 设备闸
# ---------------------------------------------------------------------------


async def device_ban_status(
    xuehai_id: str, *, now: datetime | None = None
) -> tuple[bool, str | None, datetime | None]:
    """这台设备**此刻**是否被限制登入，以及原因/到期时刻。

    两个来源取并集，顺序即优先级：

    1. 显式 ``BANNED_DEVICES`` 行（``until`` 为空=永久，或尚未到期）；
    2. 主设备 = 本机 且该用户**此刻**处于封禁中（``ban_is_active``；``banned``
       列到期不归零，脏行必须由判定层排除）。

    空 ``xuehai_id`` 一律放行：没有设备号就没有设备证据，老客户端也不带这个头。
    """
    if not xuehai_id:
        return False, None, None

    at = now or datetime.now()

    row = await backends.get_device_bans_repo().get(xuehai_id)
    if row:
        until = _as_until(row.get("until"))
        if not _expired(until, at):
            reason = row.get("reason")
            reason = reason.strip() if isinstance(reason, str) and reason.strip() else None
            return True, reason or DEFAULT_BAN_REASON, until

    for user in await backends.get_users_repo().list_banned_by_xuehai(xuehai_id):
        if ban_is_active(user, at):
            return True, ban_reason_of(user), _as_until(user.get("ban_until"))

    return False, None, None


# ---------------------------------------------------------------------------
# 临时密码登入
# ---------------------------------------------------------------------------


async def issue_challenge(user_oid: str, xuehai_id: str) -> str:
    """为「异设备登录」签发一次性挑战令牌，返回明文（库里只有 SHA-256）。

    挑战绑定 ``(user_oid, xuehai_id)``：拿到令牌的人只能为**这台**设备换一次
    临时密码登入，不能把令牌转给第三台机器。
    """
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now() + timedelta(minutes=DEVICE_CHALLENGE_TTL_MINUTES)
    await backends.get_device_challenges_repo().insert(
        _token_hash(token), user_oid, xuehai_id, expires_at
    )
    return token


async def challenge_device(
    challenge: str, *, now: datetime | None = None
) -> str | None:
    """挑战绑定的设备号；不存在 / 已消费 / 已过期返回 ``None``。

    临时密码是「一台设备的单次许可」，不是可转手的通用凭据。兑换前先回答
    「这张挑战是为哪台机器签的」，路由层才能把换了设备的请求挡在计数之外
    （不消耗 attempts，免得攻击者用别人的挑战把号刷到封禁）。
    """
    at = now or datetime.now()
    try:
        row = await backends.get_device_challenges_repo().get(
            _token_hash(challenge or "")
        )
    except Exception:
        # 读不到就跳过绑定校验：真正的裁决仍在 verify_temp_password，
        # 这里只是「早一步给出更准确的错误」。
        _logger.exception("读取设备挑战失败，跳过设备绑定校验")
        return None

    if row is None or row.get("consumed_at") is not None:
        return None
    expires_at = _as_until(row.get("expires_at"))
    if expires_at is None or _expired(expires_at, at):
        return None
    bound = row.get("xuehai_id")
    return str(bound) if bound else None


def _config_of(rule: Any) -> dict:
    """规则的 ``config`` 解析成 dict；坏数据返回 ``{}``（调用方用产品默认值）。"""
    if not isinstance(rule, dict):
        return {}
    config = rule.get("config")
    if isinstance(config, str):
        try:
            config = json.loads(config)
        except (TypeError, ValueError):
            return {}
    return config if isinstance(config, dict) else {}


def _positive_config(config: dict, *keys: str) -> int | None:
    for key in keys:
        try:
            value = int(config.get(key))
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    return None


def _rule_max_attempts(rule: Any) -> int:
    """规则的尝试上限：``config["threshold"]`` → ``config["max_attempts"]`` → 常量 3。

    ``threshold`` 优先是因为规则配置已统一成 ``{threshold, window_hours}``
    （管理员界面只编辑这两个字段）；``max_attempts`` 是 V014 时代的旧键，留着
    回落是为了「老库里没被 V016 改写的行」也照旧生效，而不是突然放宽成 3 次。
    """
    return (
        _positive_config(_config_of(rule), "threshold", "max_attempts")
        or MAX_TEMP_ATTEMPTS
    )


async def _temp_attempt_policy() -> tuple[int, bool, dict]:
    """``(尝试上限, 规则是否启用, 规则配置)``。

    按**检查项**取规则（``foreign_device_login``）：设备闸关心的是「这台机器
    能不能登」，与管理员给规则起的名字无关；同一检查项最多一条规则，所以
    取到的配置是确定的。

    读规则失败 → ``(默认上限, True, {})``：配置读不到是运维问题，不能顺手把封禁
    也关掉（fail-open 到产品默认值）。规则**存在但停用** → ``(上限, False, cfg)``，
    调用方据此只拒绝本次登入、不封禁。
    """
    try:
        rule = await ban_rules.get_rule_by_trigger(RULE_CODE_FOREIGN_DEVICE)
    except Exception:
        _logger.exception(
            "读取 %s 规则失败，临时密码上限回落到 %s",
            RULE_CODE_FOREIGN_DEVICE,
            MAX_TEMP_ATTEMPTS,
        )
        return MAX_TEMP_ATTEMPTS, True, {}

    enabled = bool(isinstance(rule, dict) and rule.get("enabled"))
    return _rule_max_attempts(rule), enabled, _config_of(rule)


async def count_recent_temp_failures(user_oid: str, since: datetime) -> int:
    """该用户在 ``since`` 之后写入的 ``temp_password_fail`` 审计条数。

    只读查询直接落在 ``APP_LOGS`` 上（``ix_app_logs_user_ts`` 覆盖
    ``app_user`` + ``ts_raw``）：失败证据本来就在那张表里，为一次计数再加一张
    计数表只会多一份会漂移的状态。``ts_raw`` 是时间窗的权威列，与写入路径同源。
    """
    from sqlalchemy import func, select

    from db.models import app_logs as app_logs_tbl
    from db.oracle.session import session_scope

    stmt = (
        select(func.count())
        .select_from(app_logs_tbl)
        .where(
            app_logs_tbl.c.app_user == user_oid,
            app_logs_tbl.c.ltype == "temp_password_fail",
            app_logs_tbl.c.ts_raw >= since.timestamp(),
        )
    )
    async with session_scope() as session:
        return int((await session.execute(stmt)).scalar_one())


async def _recent_temp_failures(user_oid: str, since: datetime) -> int:
    """计数失败一律按 0 处理：读不到审计只是少一道防线，不能让登录 500。"""
    try:
        return await count_recent_temp_failures(user_oid, since)
    except Exception:
        _logger.exception("统计临时密码失败次数失败，本次跳过累计封禁判定")
        return 0


async def _account_ban_active(user_oid: str, now: datetime) -> bool:
    """该账号在 ``now`` 是否处于封禁中（读不到按未封禁处理）。"""
    try:
        doc = await backends.get_users_repo().get_by_oid(user_oid)
    except Exception:
        _logger.exception("读取账号封禁状态失败，按未封禁处理")
        return False
    return ban_is_active(doc, now)


async def verify_temp_password(
    challenge: str,
    code: str,
    *,
    request_url: str = "",
    now: datetime | None = None,
) -> tuple[str, str]:
    """校验「挑战令牌 + 临时密码」，成功返回 ``(user_oid, xuehai_id)``。

    状态机（顺序不可换）：

    1. ``get`` 读挑战行（不加锁，见仓储 docstring）；
    2. 过期/已消费 → 403 ``TEMP_PASSWORD_INVALID``，且**不计数**（令牌已死，
       计数没有意义，也会让人靠重放把别人刷成封禁）；
    3. 每次到达这一步都 ``increment_attempts``（含成功的一次），失败次数因此
       是「已尝试次数」而不是「已失败次数」；自增是语句级原子的，拿到的就是
       本次是第几次，并发请求不会数重；
    4. 遍历目标用户未过期/未使用/未撤销的临时密码，``bcrypt`` 逐个比对；
    5. 本次失败后统计该用户窗口内的失败总次数（跨挑战累计）：换一张挑战就能
       把 ``attempts`` 归零，只有累计条数能识别「一直在穷举」。

    只有 ``attempts == 上限`` 那一次会**同时**动账号与设备，这是能确认「有人
    在这台机器上冒用身份」的证据。``attempts > 上限`` 是同一张挑战被继续提交
    （挑战失败后不消费，5 分钟内仍可用）：再封一次只会把账号梯度一路打到永久
    （1→3→7→14→30→永久），所以那种请求只拒绝本次登入。

    累计路径同样「只在当前没有生效封禁时」落封：否则封禁期内每一次错密码都会
    再推一档梯度，账号会被自己的错误次数打成永久封禁。
    """
    at = now or datetime.now()

    challenges = backends.get_device_challenges_repo()
    row = await challenges.get(_token_hash(challenge or ""))
    if row is None:
        raise _invalid_temp_password(0)

    expires_at = _as_until(row.get("expires_at"))
    if row.get("consumed_at") is not None or expires_at is None or _expired(expires_at, at):
        raise _invalid_temp_password(0)

    challenge_id = row.get("_id")
    user_oid = row.get("user_oid")
    xuehai_id = row.get("xuehai_id")
    if not challenge_id or not user_oid or not xuehai_id:
        raise _invalid_temp_password(0)
    attempts = await challenges.increment_attempts(challenge_id)
    user_oid = str(user_oid)
    xuehai_id = str(xuehai_id)
    limit, rule_enabled, config = await _temp_attempt_policy()

    passwords = backends.get_temp_passwords_repo()
    matched: Row | None = None
    if code:
        matched = await asyncio.to_thread(
            _match_temp_password,
            await passwords.list_active_for_user(
                user_oid, limit=MAX_TEMP_PASSWORDS_SCANNED
            ),
            code,
            at,
        )

    matched_id = matched.get("_id") if matched is not None else None
    if matched is not None and not matched_id:
        raise _invalid_temp_password(0)

    if matched_id is not None and await passwords.mark_used(matched_id):
        # mark_used 返回 0 表示并发下已被别人用掉：这不算成功，落到下面的失败分支。
        await challenges.consume(challenge_id)
        return user_oid, xuehai_id

    await _log_temp_failure(request_url, user_oid, xuehai_id, attempts, limit, at)

    # 正确密码在上面已经放行：累计判定只看真正的失败，避免「手里有有效码却因为
    # 之前手抖几次被锁死」。
    await _reject_on_cumulative_failures(
        request_url, user_oid, xuehai_id, config, rule_enabled, at
    )

    if attempts > limit:
        # 本次挑战已经在上限那一次封过了：不再封、不再写设备日志、不覆盖
        # ``BANNED_DEVICES``（覆盖会让封禁原因/到期时间被打乱）。
        raise _invalid_temp_password(0)

    if attempts == limit:
        if not rule_enabled:
            await _log_rule_skipped(request_url, user_oid, xuehai_id, at)
            raise _invalid_temp_password(0)

        detail: dict[str, Any] = {
            "code": "DEVICE_BAN_APPLIED",
            "message": _ban_applied_message(limit),
            "until": None,
        }
        try:
            ban = await _ban_account_and_device(
                user_oid,
                xuehai_id,
                request_url=request_url,
                now=at,
                limit=limit,
            )
            detail["until"] = (
                int(ban["until"].timestamp()) if ban.get("until") is not None else None
            )
        except Exception:
            # 封禁落库失败也仍然拒绝本次登入：错误次数已经用完，放行等于让人
            # 用穷举继续试。留痕给运维补写即可。
            _logger.exception("临时密码连错后封禁落库失败（本次仍拒绝登入）")
        raise HTTPException(status_code=403, detail=detail)

    raise _invalid_temp_password(limit - attempts)


def _ban_applied_message(limit: int) -> str:
    if limit == MAX_TEMP_ATTEMPTS:
        return DEVICE_BAN_APPLIED_MESSAGE
    return f"临时密码连续输错 {limit} 次，账号与这台设备已被封禁"


def _cumulative_policy(config: dict) -> tuple[int, int]:
    """``(累计失败上限, 窗口小时)``：规则可覆盖，缺项用产品默认值。"""
    limit = (
        _positive_config(config, "cumulative_threshold") or CUMULATIVE_TEMP_FAILURES
    )
    hours = (
        _positive_config(config, "cumulative_window_hours")
        or CUMULATIVE_TEMP_FAILURE_HOURS
    )
    return limit, hours


async def _reject_on_cumulative_failures(
    request_url: str,
    user_oid: str,
    xuehai_id: str,
    config: dict,
    rule_enabled: bool,
    at: datetime,
) -> None:
    """窗口内失败总数到阈值就封账号+设备并拒绝；未到阈值时直接返回。

    这条路径补的是「换挑战重置计数」的洞：``attempts`` 挂在挑战行上，攻击者
    每错 2 次就重新申请一张挑战，就能无限试下去。审计流水里的失败条数跨挑战
    存在，是唯一能累计的证据。
    """
    limit, hours = _cumulative_policy(config)
    since = at - timedelta(hours=hours)
    failures = await _recent_temp_failures(user_oid, since)
    if failures < limit:
        return

    if not rule_enabled:
        await _log_rule_skipped(request_url, user_oid, xuehai_id, at)
        raise _invalid_temp_password(0)

    if await _account_ban_active(user_oid, at):
        # 封禁期内继续错密码：只拒绝本次登入，不再推梯度。
        raise _invalid_temp_password(0)

    reason = (
        f"{hours} 小时内累计 {failures} 次输入错误的临时密码（设备 {xuehai_id}）"
    )
    detail: dict[str, Any] = {
        "code": "DEVICE_BAN_APPLIED",
        "message": (
            f"{hours} 小时内临时密码输错 {failures} 次，账号与这台设备已被封禁"
        ),
        "until": None,
    }
    try:
        ban = await _ban_account_and_device(
            user_oid,
            xuehai_id,
            request_url=request_url,
            now=at,
            limit=limit,
            reason=reason,
        )
        detail["until"] = (
            int(ban["until"].timestamp()) if ban.get("until") is not None else None
        )
    except Exception:
        _logger.exception("临时密码累计连错后封禁落库失败（本次仍拒绝登入）")
    raise HTTPException(status_code=403, detail=detail)


def _record_usable(record: Row, at: datetime) -> bool:
    """仓储已过滤，这里再判一次：判定处不能依赖「调用方一定过滤对了」。"""
    if record.get("used_at") is not None or record.get("revoked_at") is not None:
        return False
    expires_at = _as_until(record.get("expires_at"))
    return expires_at is not None and not _expired(expires_at, at)


def _match_temp_password(records: list[Row], code: str, at: datetime) -> Row | None:
    """整批候选一次跑完：逐个 ``to_thread`` 只会换来一串线程池往返。

    bcrypt cost 12 每次约 250ms，CPU 该花还是要花，但整批只应占用事件
    循环一次切换；扫描顺序与判定口径与原来的 for 循环逐条等价。
    """
    for record in records:
        if not _record_usable(record, at):
            continue
        if _check_code(code, str(record.get("code_hash") or "")):
            return record
    return None


async def _log_temp_failure(
    request_url: str,
    user_oid: str,
    xuehai_id: str,
    attempts: int,
    limit: int,
    at: datetime,
) -> None:
    try:
        await ZVMSLog(
            request_url,
            user_oid,
            f"临时密码输错（第 {attempts} 次，上限 {limit} 次）",
            at.timestamp(),
            type="temp_password_fail",
            level="warning",
            xuehai_id=xuehai_id,
        ).insert_log()
    except Exception:
        _logger.exception("写入 temp_password_fail 日志失败")


async def _log_rule_skipped(
    request_url: str, user_oid: str, xuehai_id: str, at: datetime
) -> None:
    """规则被管理员停用：错误照常计数，但不再自动封禁。"""
    try:
        await ZVMSLog(
            request_url,
            user_oid,
            f"规则 {RULE_CODE_FOREIGN_DEVICE} 已停用，"
            f"临时密码连错不再封禁账号与设备（设备 {xuehai_id}）",
            at.timestamp(),
            type="ban_rule_skipped",
            level="warning",
            xuehai_id=xuehai_id,
        ).insert_log()
    except Exception:
        _logger.exception("写入 ban_rule_skipped 日志失败")


async def _device_owned_by_other(xuehai_id: str, user_oid: str) -> bool:
    """该设备号是否是**别人**的主设备（``xuehai_id`` 由客户端自报，可伪造）。

    判定失败按 ``True`` 处理：宁可少封一台机器，也不能因为查库失败把受害者的
    主设备锁死。
    """
    try:
        return await backends.get_users_repo().primary_owned_by_other(
            xuehai_id, user_oid
        )
    except Exception:
        _logger.exception("查询主设备归属失败，跳过设备封禁")
        return True


async def _ban_account_and_device(
    user_oid: str,
    xuehai_id: str,
    *,
    request_url: str,
    now: datetime,
    limit: int,
    reason: str | None = None,
) -> dict:
    """第 ``limit`` 次失败（或累计到阈值）：账号按梯度封 + 该设备显式永久封 + 一条日志。

    设备封禁前先确认这台机器不是别人的主设备：设备号完全来自请求头，攻击者
    填受害者的设备号就能借 3 次错密码把人永久锁在门外。命中时**只封账号**，
    并留一条 ``device_ban_skipped`` 说明原因。
    """
    reason = reason or f"连续 {limit} 次输入错误的临时密码（设备 {xuehai_id}）"
    ban = await apply_account_ban(user_oid, RULE_CODE_FOREIGN_DEVICE, reason, now=now)

    if await _device_owned_by_other(xuehai_id, user_oid):
        try:
            await ZVMSLog(
                request_url,
                user_oid,
                f"设备 {xuehai_id} 是其他账号的主设备，跳过设备封禁（账号已按规则封禁）：{reason}",
                now.timestamp(),
                type="device_ban_skipped",
                level="warning",
                xuehai_id=xuehai_id,
            ).insert_log()
        except Exception:
            _logger.exception("写入 device_ban_skipped 日志失败（账号封禁已生效）")
        return ban

    # until=None：永久。管理员可在设备封禁列表里解除（设备有换主人的可能）。
    await backends.get_device_bans_repo().upsert(
        xuehai_id,
        reason=reason,
        until=None,
        source="rule",
        rule_code=RULE_CODE_FOREIGN_DEVICE,
        operator_oid=None,
    )

    try:
        await ZVMSLog(
            request_url,
            user_oid,
            f"设备 {xuehai_id} 被封禁：{reason}",
            now.timestamp(),
            type="device_ban",
            level="warning",
            xuehai_id=xuehai_id,
        ).insert_log()
    except Exception:
        _logger.exception("写入 device_ban 日志失败（封禁已生效）")

    return ban


async def apply_account_ban(
    user_oid: str,
    rule_code: str,
    reason: str,
    *,
    now: datetime | None = None,
) -> dict:
    """按规则梯度封账号，返回事实供调用方拼响应。

    与 ``services.violation_guard`` 共用 :func:`services.ban_rules.penalty_expiry`：
    梯度末位 ``None`` → ``until=None``（永久），流水的 ``permanent`` 与
    ``expires_at`` 必须同时表达这件事，否则管理页会把永久封禁显示成「无到期时间」。
    """
    at = now or datetime.now()

    try:
        rule = await backends.get_ban_rules_repo().get_by_code(rule_code)
    except Exception:
        # 规则表读不到不该让封禁凭空消失：按默认梯度封，事后再补查。
        _logger.exception("读取 %s 规则失败，按默认梯度封禁", rule_code)
        rule = None
    penalties = ban_rules.rule_penalties(rule)
    records = backends.get_ban_records_repo()
    offense = await records.count_for_user_rule(user_oid, rule_code) + 1

    until, days = ban_rules.penalty_expiry(penalties, offense, at)

    # 规则封禁只加刑不减刑：管理员已落的永久/更长封禁保留，流水照落。
    effective_until = await ban_rules.set_ban_unless_stronger(
        user_oid,
        reason=reason,
        until=until,
        rule_code=rule_code,
    )
    await records.insert(
        user_oid,
        source="rule",
        rule_code=rule_code,
        reason=reason,
        permanent=effective_until is None,
        started_at=at,
        expires_at=effective_until,
        operator_oid=None,
    )

    return {
        "reason": reason,
        "until": effective_until,
        "days": days,
        "permanent": effective_until is None,
        "offense": offense,
    }


# ---------------------------------------------------------------------------
# 存量数据清理
# ---------------------------------------------------------------------------
# ``USER_DEVICE_BINDINGS`` 的清理**只在** ``scripts/purge_legacy_device_bindings.py``
# 里实现：那是不可逆的 DELETE，运维脚本需要「掩码预览 + 显式 --apply」，服务层
# 再写一份就只能提供更弱的保证，两份实现对不上时没人知道该信哪个。


__all__ = [
    "TEMP_PASSWORD_TTL_MINUTES",
    "TEMP_PASSWORD_CODE_LEN",
    "TEMP_SESSION_HOURS",
    "DEVICE_CHALLENGE_TTL_MINUTES",
    "MAX_TEMP_ATTEMPTS",
    "MAX_TEMP_PASSWORDS_SCANNED",
    "CUMULATIVE_TEMP_FAILURES",
    "CUMULATIVE_TEMP_FAILURE_HOURS",
    "RULE_CODE_FOREIGN_DEVICE",
    "DEVICE_BANNED_MESSAGE",
    "DEVICE_NOT_PRIMARY_MESSAGE",
    "TEMP_PASSWORD_INVALID_MESSAGE",
    "CHALLENGE_MISMATCH_MESSAGE",
    "DEVICE_BAN_APPLIED_MESSAGE",
    "DeviceBanned",
    "device_ban_status",
    "challenge_device",
    "issue_challenge",
    "verify_temp_password",
    "count_recent_temp_failures",
    "apply_account_ban",
    "generate_temp_password",
    "hash_temp_password",
    "hash_temp_password_async",
]
