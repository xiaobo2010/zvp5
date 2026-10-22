"""平台违规使用的上报、去重、累计计数与自动封禁。

**为什么检测放在客户端、判定放在服务端**：嵌套 ``iframe``、``devtools``、
学海客户端桥缺失这三类事实只有浏览器里看得见，服务端拿不到。但「这次上报
该不该封号」是政策问题，必须由服务端按同一份计数口径裁决——客户端只提交
观察到的信号，不提交结论。

**为什么先入库、再判封禁**：证据与处罚是两份事实。规则被停用、角色豁免、
计数还没到阈值时都应留下证据；反过来，先判后记会让「差一次到阈值」的那批
上报彻底消失，管理员在违规列表里看不到「差一点就被封」的人。

**为什么用「计数等于阈值」而不是 `>=`**：与临时密码同一思路（见
``services.device_guard.verify_temp_password``）。计数只会随上报递增，若用
``>=``，阈值之后每一次上报都会再封一次，梯度会被一路打到永久档；用等号则
只在跨过阈值的那一次落封禁。管理员事后调低阈值导致 ``count > threshold``
时不再补封——那是一次配置变更，不该由下一条无关上报来触发历史追罚。

**为什么 privileged 角色只记录不封**：``iframe``/``devtools`` 在管理端调试、
投屏、嵌入场景里误报代价极高，自动封掉管理员等于丢掉管理入口（与
``services.ban_rules`` 的「绝不自动封 admin」同一条原则，这里扩到学生会与
教师）。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any

from db import backends
from services import ban_rules, device_guard
from typings.log import ZVMSLog

_logger = logging.getLogger(__name__)

TRIGGER = "platform_misuse"

# 信号白名单：客户端只能报「观察到了什么形态」，不能自己发明类别。
SIGNALS = ("iframe", "bridge", "devtools")

# 去重窗口：同 (user, signal) 窗口内只记一条。``devtools`` 给更长窗口，因为
# 它按采样上报（窗口尺寸差/计时器抖动），短时间内天然会连续命中。
DEDUP_WINDOW_SECONDS = 300
DEVTOOLS_DEDUP_WINDOW_SECONDS = 1800

# 规则缺失/坏数据时的产品默认值（与 V016 种子一致）。
DEFAULT_CONFIG = {"threshold": 3, "window_hours": 24}

# DB 列宽即契约：超长详情在服务端截断，不让一次上报因为一个长字符串丢整批。
MAX_DETAIL_CHARS = 500

# 角色豁免：这些角色即使在别的账号视角下「违规」，也可能是工作行为。
NO_AUTO_BAN_ROLES = ("admin", "department", "teacher")

Row = dict[str, Any]


def dedup_window_seconds(signal: str) -> int:
    """该信号的回溯窗口秒数；未知信号按普通窗口处理（调用方已先过滤）。"""
    if signal == "devtools":
        return DEVTOOLS_DEDUP_WINDOW_SECONDS
    return DEDUP_WINDOW_SECONDS


def _clean_detail(detail: Any) -> str | None:
    """详情是自由文本，只做两件事：转字符串、截断。"""
    if detail is None:
        return None
    text = detail if isinstance(detail, str) else str(detail)
    text = text.strip()
    if not text:
        return None
    return text[:MAX_DETAIL_CHARS]


def _config(rule: dict | None) -> dict:
    """规则 ``{threshold, window_hours}``；坏数据整体回退默认值。

    与 ``ban_rules.rule_config`` 同口径，但这里不复用它：违规计数窗口与登录
    判定窗口是两条独立策略，将来任一侧改语义时不该互相牵连。
    """
    raw = (rule or {}).get("config")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            raw = None
    if not isinstance(raw, dict):
        return dict(DEFAULT_CONFIG)

    def positive(value: Any) -> int | None:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return None
        return parsed if parsed > 0 else None

    threshold = positive(raw.get("threshold"))
    window_hours = positive(raw.get("window_hours"))
    if threshold is None or window_hours is None:
        return dict(DEFAULT_CONFIG)
    return {"threshold": threshold, "window_hours": window_hours}


async def _rule() -> dict | None:
    """``platform_misuse`` 规则；读表失败按「只记录」处理（fail-open）。

    证据已经入库，规则表读不到是运维问题：让整批上报 500 只会丢掉客户端手里的
    事实，而重试还会被去重窗口挡掉，等于永久失去这条证据。
    """
    try:
        return await ban_rules.get_rule_by_trigger(TRIGGER)
    except Exception:
        _logger.exception("读取 %s 规则失败，本次只记录违规不判定封禁", TRIGGER)
        return None


async def _log_violation(
    user_oid: str,
    signal: str,
    detail: str | None,
    xuehai_id: str,
    at: datetime,
) -> None:
    """每条入库的违规都留审计；写日志失败不能影响「已入库」这件事。"""
    try:
        await ZVMSLog(
            "",
            user_oid,
            f"平台违规使用：{signal}"
            f"（详情：{detail or '无'}，设备 {xuehai_id or '未知'}）",
            at.timestamp(),
            type="platform_misuse_violation",
            level="warning",
            xuehai_id=xuehai_id,
        ).insert_log()
    except Exception:
        _logger.exception("写入 platform_misuse_violation 日志失败（违规已入库）")


async def _log_ban(user_oid: str, reason: str, xuehai_id: str, at: datetime) -> None:
    """封禁已生效后的审计；同样吞掉失败，封禁状态才是事实。"""
    try:
        await ZVMSLog(
            "",
            user_oid,
            f"自动封禁：{reason}",
            at.timestamp(),
            type="ban_rule",
            level="warning",
            xuehai_id=xuehai_id,
        ).insert_log()
    except Exception:
        _logger.exception("写入 ban_rule 日志失败（封禁已生效）")


async def record(user: dict, signals: list[dict], xuehai_id: str = "") -> dict:
    """记录一批平台违规信号，命中阈值当次按规则梯度封禁。

    流程（顺序即语义）：白名单过滤 → 逐条窗口去重 → 入库 + 审计 → 取规则 →
    窗口计数 → ``count == threshold`` 且角色可封时落封禁。

    返回 ``{"recorded", "duplicates", "banned", "rule_code"}``：``recorded`` 是
    真正新增的证据条数（客户端可据此判断是否被去重），``duplicates`` 是被窗口
    挡掉的条数，两者之和只统计合法信号。
    """
    user_oid = str(user["id"])
    now = datetime.now()

    violations = backends.get_violations_repo()
    recorded = 0
    duplicates = 0
    for item in signals or []:
        signal = (item or {}).get("signal")
        if not isinstance(signal, str) or signal not in SIGNALS:
            # 客户端拼错信号名不该让整批 422：忽略这一条，其余照记。
            _logger.warning("忽略未知的平台违规信号 %r", signal)
            continue

        detail = _clean_detail((item or {}).get("detail"))
        since = now - timedelta(seconds=dedup_window_seconds(signal))
        if await violations.exists_recent(user_oid, signal, since):
            duplicates += 1
            continue

        await violations.insert(
            user_oid, TRIGGER, signal, xuehai_id=xuehai_id or None, detail=detail
        )
        recorded += 1
        await _log_violation(user_oid, signal, detail, xuehai_id, now)

    result = {
        "recorded": recorded,
        "duplicates": duplicates,
        "banned": False,
        "rule_code": TRIGGER,
    }
    if recorded == 0:
        # 全是重复或非法信号：计数没有变化，没必要再读规则表。
        return result

    rule = await _rule()
    if rule is None or not rule.get("enabled"):
        return result

    # 梯度与流水必须按规则自己的 code 记：管理员新建的规则 code 可能不是
    # ``platform_misuse``，写死 trigger 会让 count_for_user_rule 与取梯度的
    # ``get_by_code`` 对不上，处罚回落成默认 [1]。
    rule_code = str(rule.get("code") or TRIGGER)
    result["rule_code"] = rule_code

    config = _config(rule)
    threshold = config["threshold"]
    window_hours = config["window_hours"]
    since = now - timedelta(hours=window_hours)
    count = await violations.count_since(user_oid, TRIGGER, since)
    if count != threshold:
        return result

    if any(role in NO_AUTO_BAN_ROLES for role in (user.get("per") or [])):
        # 只记录不封：管理端的调试/嵌入/投屏会稳定触发这些信号。
        return result

    reason = (
        f"{window_hours} 小时内累计 {count} 次平台违规使用"
        f"（阈值 {threshold} 次）"
    )
    try:
        await device_guard.apply_account_ban(user_oid, rule_code, reason)
    except Exception:
        # 封禁落库失败不回滚已入库的证据：下一条上报的 count 已越过阈值，
        # 不会再走等号分支，这里只能留痕给运维补封。
        _logger.exception("平台违规封禁落库失败（证据已入库）")
        return result

    result["banned"] = True
    await _log_ban(user_oid, reason, xuehai_id, now)
    return result


__all__ = [
    "TRIGGER",
    "SIGNALS",
    "DEDUP_WINDOW_SECONDS",
    "DEVTOOLS_DEDUP_WINDOW_SECONDS",
    "DEFAULT_CONFIG",
    "MAX_DETAIL_CHARS",
    "NO_AUTO_BAN_ROLES",
    "dedup_window_seconds",
    "record",
]
