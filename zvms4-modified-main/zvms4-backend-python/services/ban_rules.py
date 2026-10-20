"""规则封禁的判定与执行（登录时同步调用）。

为什么判在**登录**：``app_logs`` 只在登录时拿到 ``Xuehai-Id``，其它请求带这个
头也不代表「这台设备在用这个账号」；把窗口计数放在登录路径，证据与动作就是
同一次请求里的因果关系，不需要额外的后台任务去追。

与 ``routers/users_router._observe_binding`` 同款的 fail-open 原则：除了**命中
规则后主动抛出的 403**，任何异常（规则表读不到、JSON 坏、审计写失败）都只
记日志，绝不阻断登录。判封禁是「额外保护」，不能变成登录的单点故障。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta

from db import backends
from typings.log import ZVMSLog
from util.ban import raise_banned

_logger = logging.getLogger(__name__)

RULE_CODE = "multi_device_login"
DEFAULT_CONFIG = {"threshold": 3, "window_hours": 24}
DEFAULT_PENALTIES = [1]


def _json_value(raw, fallback):
    """``CONFIG`` / ``PENALTIES`` 是 DB 只做 ``IS JSON`` 的文本，解析归本层。"""
    if isinstance(raw, (dict, list)):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return fallback
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return fallback


def _positive_int(value) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def rule_config(rule: dict | None) -> dict:
    """规则的 ``{threshold, window_hours}``；坏数据整体回退默认值。"""
    raw = _json_value((rule or {}).get("config"), None)
    if not isinstance(raw, dict):
        if raw is not None:
            _logger.warning("%s 规则的 config 不是对象，回退默认值", RULE_CODE)
        return dict(DEFAULT_CONFIG)
    threshold = _positive_int(raw.get("threshold"))
    window_hours = _positive_int(raw.get("window_hours"))
    if threshold is None or window_hours is None:
        _logger.warning("%s 规则的 config 字段非法，回退默认值", RULE_CODE)
        return dict(DEFAULT_CONFIG)
    return {"threshold": threshold, "window_hours": window_hours}


def rule_penalties(rule: dict | None) -> list[int]:
    """天数梯度；空/非正数的坏数据回退 ``[1]``（永不因为配置坏而不处罚）。"""
    raw = _json_value((rule or {}).get("penalties"), None)
    if not isinstance(raw, list):
        return list(DEFAULT_PENALTIES)
    steps = [step for step in (_positive_int(item) for item in raw) if step is not None]
    return steps or list(DEFAULT_PENALTIES)


def _reason(window_hours: int, devices: int, threshold: int) -> str:
    return (
        f"在 {window_hours} 小时内使用 {devices} 台学海设备登录"
        f"（阈值 {threshold} 台）"
    )


async def evaluate_login(
    user_oid: str,
    roles,
    display_name: str,
    xuehai_id: str,
    request_url: str,
    now: datetime | None = None,
) -> dict | None:
    """判定本次登录是否命中规则封禁；命中则落库并返回事实，否则 ``None``。

    不吞异常：调用方（:func:`enforce_login`）负责 fail-open，这样单测能直接
    看到规则求值里真实的错误。
    """
    # 没有设备号就没有证据：规则的全部依据是「同一账号被多少台设备登录」。
    if not xuehai_id:
        return None
    # 绝不自动封管理员：规则是自动执行的，误封管理员的代价是丢掉管理入口。
    if "admin" in (roles or []):
        return None

    rule = await backends.get_ban_rules_repo().get_by_code(RULE_CODE)
    if rule is None or not rule.get("enabled"):
        return None

    config = rule_config(rule)
    threshold = config["threshold"]
    window_hours = config["window_hours"]
    penalties = rule_penalties(rule)

    at = now or datetime.now()
    since = at.timestamp() - window_hours * 3600
    devices = await backends.get_applogs_repo().count_distinct_xuehai(user_oid, since)
    if devices < threshold:
        return None

    records = backends.get_ban_records_repo()
    # 历史流水条数 = 已发生的处罚次数；本次是第 offense 次。
    offense = await records.count_for_user_rule(user_oid, RULE_CODE) + 1
    days = penalties[min(offense - 1, len(penalties) - 1)]
    until = at + timedelta(days=days)
    reason = _reason(window_hours, devices, threshold)

    # 先改当前状态，再补流水：状态是登录闸门的依据，必须最先落库。
    await backends.get_users_repo().set_ban(
        user_oid,
        reason=reason,
        until=until,
        source="rule",
        rule_code=RULE_CODE,
    )
    await records.insert(
        user_oid,
        source="rule",
        rule_code=RULE_CODE,
        reason=reason,
        permanent=False,
        started_at=at,
        expires_at=until,
        operator_oid=None,
    )

    # 审计写失败不能把「已落库的封禁」变成一次异常冒泡：封禁已经生效，
    # 吞掉写日志的错误并留痕，本次仍然按命中处理（下面照常 403）。
    try:
        await ZVMSLog(
            request_url,
            user_oid,
            f"自动封禁 {display_name}：{reason}，到期 {until:%Y-%m-%d %H:%M}",
            at.timestamp(),
            type="ban_rule",
            level="warning",
            xuehai_id=xuehai_id,
        ).insert_log()
    except Exception:
        _logger.exception("写入 ban_rule 审计日志失败（封禁已生效）")

    return {
        "reason": reason,
        "until": until,
        "days": days,
        "devices": devices,
    }


async def enforce_login(
    user_oid: str,
    roles,
    display_name: str,
    xuehai_id: str,
    request_url: str,
    now: datetime | None = None,
) -> None:
    """登录路径的封禁闸：命中抛 403，其余一切异常只记日志（fail-open）。"""
    try:
        result = await evaluate_login(
            user_oid, roles, display_name, xuehai_id, request_url, now=now
        )
    except Exception:
        _logger.exception("规则封禁判定失败，放行本次登录")
        return

    if not result:
        return

    # 手工构造一个「文档」只为复用 util.ban 的头/文案口径：此时用户行里可能
    # 还没有 ban_reason（写入失败也不会走到这里），以判定结果为准更准确。
    raise_banned(
        {
            "banned": True,
            "ban_reason": result["reason"],
            "ban_until": result["until"],
        }
    )


__all__ = [
    "RULE_CODE",
    "DEFAULT_CONFIG",
    "DEFAULT_PENALTIES",
    "rule_config",
    "rule_penalties",
    "evaluate_login",
    "enforce_login",
]
