"""服务端可观测数据的封禁规则（登录失败、挑战刷取、导出频率等）。

**为什么单独一个模块**：``ban_rules`` 只留积木（配置解析/梯度/落封禁），设备闸与
上报闸各自持有一条证据链。这里的七条规则证据全部来自**服务端自己写下的行**
（``APP_LOGS`` / ``DEVICE_CHALLENGES``），既不需要客户端上报，也不该塞进任何一个
既有闸门——它们的共同点只有一个：**先在登录/导出等入口埋点，再按窗口计数裁决**。

**为什么计数直接读日志表**：日志是已经落库的事实，再加一张计数表只会多一份会
漂移的状态（补记历史、并发自增、清理任务对不上）。窗口计数是一条走索引的
``COUNT(*)``，成本远低于维护第二份真相。

**为什么所有失败都吞掉**：这里的判定是「额外保护」，调用点都在登录、导出这类
主流程上。读规则失败、日志表不可用都只能是少封一次，绝不能把登录打成 500——
``evaluate`` 因此只有一个宽 except，失败只有一条 warning。

**为什么要 ``mode="alert"``**：新规则的口径（比如「8 次设备挑战/小时」）在真实
流量里可能是正常行为。先只写证据与告警，管理员看过误报率再切 ``ban``，比上线
即封人再回滚便宜得多。

**为什么要节流**：调用点在每一次失败登录上，而窗口计数是 60 秒粒度的事实。
同一 ``(trigger, user)`` 60 秒内只评估一次，既保证「刷得再快也只多一条证据」，
也把「被刷爆时把库打爆」这条路堵死。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from db import backends
from db.ids import new_oid
from services import ban_rules, device_guard
from typings.log import ZVMSLog
from util.ban import ban_is_active

_logger = logging.getLogger(__name__)

EVALUATION = "monitor"

THROTTLE_SECONDS = 60
THROTTLE_MAX_ENTRIES = 4096

#: ``BAN_VIOLATIONS.DETAIL`` 的列宽即契约：超长细节在服务端截断。
MAX_DETAIL_CHARS = 500

#: 自动封禁的角色豁免。与 ``services.device_guard.PRIVILEGED_ROLES`` /
#: ``services.violation_guard.NO_AUTO_BAN_ROLES`` 同一口径：这些角色的「高频」
#: 多半是工作行为（批量导出、给全班签临时密码），可记录不可自动封。
NO_AUTO_BAN_ROLES = ("admin", "department", "secretary", "teacher")

#: 检查项 → 证据来源 + 产品默认 config。
#: ``ltype`` 为 ``APP_LOGS.LTYPE``；``contains`` 在 ``DATA`` 摘要上做补充过滤
#: （登录失败与封禁期重试共享 ``login_fail``，靠 ``banned`` 标记区分）。
#: ``source`` 为 ``challenges`` 时按 ``DEVICE_CHALLENGES`` 计数——挑战本身就是
#: 「有人在换设备登录」的事实，不必再借日志。
TRIGGERS: dict[str, dict[str, Any]] = {
    "login_failure_burst": {
        "name": "登录失败暴破",
        "source": "app_logs",
        "ltype": "login_fail",
        "contains": None,
        "config": {"threshold": 10, "window_hours": 1, "mode": "ban"},
    },
    "ban_evasion_retry": {
        "name": "封禁期内反复尝试登录",
        "source": "app_logs",
        "ltype": "login_fail",
        "contains": "banned",
        "config": {"threshold": 5, "window_hours": 1, "mode": "ban"},
    },
    "challenge_flood": {
        "name": "设备挑战刷取",
        "source": "challenges",
        "ltype": None,
        "contains": None,
        "config": {"threshold": 8, "window_hours": 1, "mode": "alert"},
    },
    "temp_password_issue_burst": {
        "name": "临时密码签发过频",
        "source": "app_logs",
        "ltype": "temp_password_create",
        "contains": None,
        "config": {"threshold": 20, "window_hours": 1, "mode": "alert"},
    },
    "export_burst": {
        "name": "导出任务过频",
        "source": "app_logs",
        "ltype": "export",
        "contains": None,
        "config": {"threshold": 10, "window_hours": 1, "mode": "alert"},
    },
    "login_success_churn": {
        "name": "短时间反复登录",
        "source": "app_logs",
        "ltype": "user_login",
        "contains": None,
        "config": {"threshold": 15, "window_hours": 1, "mode": "alert"},
    },
    "device_switch_burst": {
        "name": "频繁更换设备",
        "source": "app_logs",
        "ltype": "device_mismatch",
        "contains": None,
        "config": {"threshold": 5, "window_hours": 24, "mode": "alert"},
    },
}

#: ``(trigger, user_oid)`` → 上次评估时刻（epoch 秒）。进程内即可：多 worker 下
#: 每个进程各节流一次，最坏情况是多写几条证据，不会变成状态不一致。
_THROTTLE: dict[tuple[str, str], float] = {}


def _prune(now_ts: float) -> None:
    """清掉过期项；仍然过多就整表清空（节流是优化，不是正确性依赖）。"""
    expired = [
        key
        for key, stamp in _THROTTLE.items()
        if now_ts - stamp >= THROTTLE_SECONDS
    ]
    for key in expired:
        _THROTTLE.pop(key, None)
    if len(_THROTTLE) > THROTTLE_MAX_ENTRIES:
        _THROTTLE.clear()


def _throttled(trigger_code: str, user_oid: str, now_ts: float) -> bool:
    """记录本次评估时刻并回答「是否还在窗口内」。"""
    if len(_THROTTLE) > THROTTLE_MAX_ENTRIES:
        _prune(now_ts)
    key = (trigger_code, user_oid)
    previous = _THROTTLE.get(key)
    _THROTTLE[key] = now_ts
    return previous is not None and now_ts - previous < THROTTLE_SECONDS


async def _count_challenges(user_oid: str, since: datetime) -> int:
    """``DEVICE_CHALLENGES`` 窗口内签发条数（挑战即「换设备登录」的事实）。

    直接走 SQLAlchemy：仓储只提供单行读写，为一次计数给它加方法会把「挑战」
    的读写语义和统计口径搅在一起，而这里只需要一条 ``COUNT(*)``。
    """
    from sqlalchemy import func, select

    from db.models import device_challenges as challenges_tbl
    from db.oracle.session import session_scope

    stmt = (
        select(func.count())
        .select_from(challenges_tbl)
        .where(
            challenges_tbl.c.user_oid == user_oid,
            challenges_tbl.c.created_at >= since,
        )
    )
    async with session_scope() as session:
        return int((await session.execute(stmt)).scalar_one())


async def _count(spec: dict, user_oid: str, since: datetime) -> int:
    if spec["source"] == "challenges":
        return await _count_challenges(user_oid, since)
    return await backends.get_applogs_repo().count_by_user_ltype(
        user_oid, spec["ltype"], since.timestamp(), spec["contains"]
    )


async def _user_doc(user_oid: str) -> dict | None:
    try:
        return await backends.get_users_repo().get_by_oid(user_oid)
    except Exception:
        # 读不到用户按「无豁免、未封禁」处理：少一次豁免最多是记录一条告警，
        # 而多判一次封禁会真的把一个特权账号锁在门外。
        _logger.warning("读取用户 %s 失败，风险规则按无豁免处理", user_oid, exc_info=True)
        return None


def _privileged(doc: dict | None) -> bool:
    roles = (doc or {}).get("roles")
    if not isinstance(roles, (list, tuple, set, frozenset)):
        return False
    return any(role in NO_AUTO_BAN_ROLES for role in roles if isinstance(role, str))


def _detail(count: int, threshold: int, window_hours: int) -> str:
    return (
        f"count={count} threshold={threshold} window_hours={window_hours}"
    )[:MAX_DETAIL_CHARS]


async def _record_violation(
    user_oid: str, trigger_code: str, detail: str
) -> None:
    """写一条违规证据；写失败不阻止告警/封禁（证据是附带材料）。"""
    try:
        await backends.get_violations_repo().insert(
            user_oid,
            trigger_code,
            trigger_code,
            batch_id=new_oid(),
            detail=detail,
        )
    except Exception:
        _logger.warning(
            "写入 %s 违规证据失败（继续判定与告警）", trigger_code, exc_info=True
        )


async def _log(
    user_oid: str,
    log_type: str,
    text: str,
    at: datetime,
    xuehai_id: str = "",
) -> None:
    try:
        await ZVMSLog(
            "",
            user_oid,
            text,
            at.timestamp(),
            type=log_type,
            level="warning",
            xuehai_id=xuehai_id,
        ).insert_log()
    except Exception:
        _logger.warning("写入 %s 审计日志失败", log_type, exc_info=True)


async def evaluate(
    trigger_code: str, user_oid: str, *, now: datetime | None = None
) -> dict | None:
    """按 ``trigger_code`` 统计窗口事件，达到阈值时告警或封禁。

    返回 ``{"trigger", "count", "threshold", "mode", "banned"}``；任何一步不该
    触发（规则缺失/停用、未达阈值、已在封禁中、被节流、任何异常）都返回
    ``None``——调用方只关心「这一次有没有做动作」。
    """
    try:
        return await _evaluate(trigger_code, str(user_oid), now or datetime.now())
    except Exception:
        # 唯一的兜底：本模块绝不把登录/导出打成 500。
        _logger.warning(
            "风险规则评估失败（trigger=%s user=%s），本次跳过",
            trigger_code,
            user_oid,
            exc_info=True,
        )
        return None


async def _evaluate(
    trigger_code: str, user_oid: str, at: datetime
) -> dict | None:
    spec = TRIGGERS.get(trigger_code)
    if spec is None:
        _logger.debug("未注册的风险检查项 %r，跳过", trigger_code)
        return None

    if _throttled(trigger_code, user_oid, at.timestamp()):
        return None

    rule = await ban_rules.get_rule_by_trigger(trigger_code)
    if rule is None or not rule.get("enabled"):
        _logger.debug("风险规则 %s 不存在或已停用，跳过", trigger_code)
        return None

    config = ban_rules.normalize_config(rule.get("config"), defaults=spec["config"])
    threshold = config["threshold"]
    window_hours = config["window_hours"]
    mode = config["mode"]

    count = await _count(spec, user_oid, at - timedelta(hours=window_hours))
    if count < threshold:
        return None

    doc = await _user_doc(user_oid)
    if ban_is_active(doc, at):
        # 已经在封禁中：新证据照记，但不再推一档梯度（否则会一路打到永久）。
        _logger.debug("用户 %s 已在封禁中，%s 不再重复处罚", user_oid, trigger_code)
        return None

    rule_code = str(rule.get("code") or trigger_code)
    detail = _detail(count, threshold, window_hours)
    await _record_violation(user_oid, trigger_code, detail)

    summary = (
        f"{spec['name']}：{window_hours} 小时内 {count} 次"
        f"（阈值 {threshold} 次，规则 {rule_code}）"
    )
    await _log(user_oid, "risk_alert", f"风险告警（{mode}）：{summary}", at)

    if mode != ban_rules.MODE_BAN:
        return {
            "trigger": trigger_code,
            "count": count,
            "threshold": threshold,
            "mode": mode,
            "banned": False,
        }

    if _privileged(doc):
        await _log(
            user_oid,
            "risk_alert",
            f"特权角色命中 {summary}，按策略只告警不封禁",
            at,
        )
        return {
            "trigger": trigger_code,
            "count": count,
            "threshold": threshold,
            "mode": mode,
            "banned": False,
        }

    reason = f"{window_hours} 小时内 {count} 次{spec['name']}（阈值 {threshold} 次）"
    await device_guard.apply_account_ban(user_oid, rule_code, reason, now=at)
    await _log(user_oid, "risk_ban", f"自动封禁：{reason}", at)

    return {
        "trigger": trigger_code,
        "count": count,
        "threshold": threshold,
        "mode": mode,
        "banned": True,
    }


__all__ = [
    "EVALUATION",
    "THROTTLE_SECONDS",
    "THROTTLE_MAX_ENTRIES",
    "MAX_DETAIL_CHARS",
    "NO_AUTO_BAN_ROLES",
    "TRIGGERS",
    "evaluate",
]
