"""平台违规使用的上报、去重、累计计数与自动封禁。

**为什么检测放在客户端、判定放在服务端**：嵌套 ``iframe``、``devtools``、
学海客户端桥缺失这三类事实只有浏览器里看得见，服务端拿不到。但「这次上报
该不该封号」是政策问题，必须由服务端按同一份计数口径裁决——客户端只提交
观察到的信号，不提交结论。

**为什么先入库、再判封禁**：证据与处罚是两份事实。规则被停用、计数还没到
阈值时都应留下证据；反过来，先判后记会让「差一次到阈值」的那批上报彻底消失，
管理员在违规列表里看不到「差一点就被封」的人。只有角色豁免是例外：特权角色
的记录留在窗口里会在换届后变成别人的处罚依据，所以那一类只留审计日志。

**为什么计数单位是「上报批次」而不是「一条信号」**：一次页面加载可能同时
命中 iframe + bridge + devtools，按行计数会让阈值 2 的规则在第一次加载就封号。
``BATCH_ID`` 由服务端为每次请求生成（客户端不能传），判定统计窗口内的
``COUNT(DISTINCT batch_id)``；``COALESCE(batch_id, oid)`` 让旧行保持「每行一批」。

**为什么用 ``>=`` 且要求「当前没有生效封禁」**：计数只在批次上递增，用等号会
漏掉「一次上报跨过阈值」的情形（3 条批次 > 阈值 2）。而 ``>=`` 本身会让阈值
之后的每一批都再封一次，梯度被一路打到永久档——所以落封前先看当前是否已经
有生效封禁，有就只记账不重复处罚。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any

from db import backends
from db.ids import new_oid
from services import ban_rules, device_guard
from typings.log import ZVMSLog
from util.ban import ban_is_active

_logger = logging.getLogger(__name__)

TRIGGER = "platform_misuse"

# 信号白名单：客户端只能报「观察到了什么形态」，不能自己发明类别。
SIGNALS = ("iframe", "bridge", "devtools")

# 去重窗口：同 (user, signal) 窗口内只记一条。``devtools`` 给更长窗口，因为
# 它按采样上报（窗口尺寸差/计时器抖动），短时间内天然会连续命中。
DEDUP_WINDOW_SECONDS = 300
DEVTOOLS_DEDUP_WINDOW_SECONDS = 1800

# 规则缺失/坏数据时的产品默认值（与 V001/V019 种子、ban_rules.DEFAULT_CONFIG 一致）。
DEFAULT_CONFIG = {"threshold": 3, "window_hours": 24}

# DB 列宽即契约：超长详情在服务端截断，不让一次上报因为一个长字符串丢整批。
MAX_DETAIL_CHARS = 500

# 角色豁免：这些角色即使在别的账号视角下「违规」，也可能是工作行为。
# 与 ``services.device_guard.PRIVILEGED_ROLES`` 对齐（团支书也在其中）。
NO_AUTO_BAN_ROLES = ("admin", "department", "secretary", "teacher")

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


def _positive(value: Any, fallback: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return fallback
    return parsed if parsed > 0 else fallback


def _config(rule: dict | None) -> dict:
    """规则 ``{threshold, window_hours}``，缺项/坏数据**逐项**回退默认值。

    逐项而不是整体回退：管理员只写了一个键（``{"threshold":5}``）时，另一个键
    该拿产品默认值，而不是把整份配置丢掉——否则「调高阈值」会被静默改回默认。
    """
    raw = (rule or {}).get("config")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            raw = None
    if not isinstance(raw, dict):
        return dict(DEFAULT_CONFIG)
    return {
        "threshold": _positive(raw.get("threshold"), DEFAULT_CONFIG["threshold"]),
        "window_hours": _positive(
            raw.get("window_hours"), DEFAULT_CONFIG["window_hours"]
        ),
    }


def _roles(value: Any) -> tuple[str, ...]:
    """角色列表；缺失/坏类型按学生处理，但要留一条 warning。

    一条脏数据只能让本人走学生口径，不能把上报打成 500；也不能因为读不懂就
    当他是管理员（那等于给一条坏数据发豁免）。
    """
    if value is None:
        return ()
    if not isinstance(value, (list, tuple, set, frozenset)):
        _logger.warning("用户角色字段类型异常（%r），按学生处理", type(value).__name__)
        return ()
    return tuple(role for role in value if isinstance(role, str))


def _exempt_roles(roles: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(role for role in roles if role in NO_AUTO_BAN_ROLES)


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


async def _ban_active(user_oid: str) -> bool:
    """该账号此刻是否已在封禁中（``ban_until`` 到期即视为解除）。

    读不到按「未封禁」处理：少判一次只会让本次处罚照旧，而多判一次会让真正
    该封的人一直被封不掉（``>=`` 分支每次都以为「已经封过了」）。
    """
    try:
        doc = await backends.get_users_repo().get_by_oid(user_oid)
    except Exception:
        _logger.exception("读取用户封禁状态失败，按未封禁处理")
        return False
    return ban_is_active(doc)


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


async def _log_exempt(
    user_oid: str,
    roles: tuple[str, ...],
    count: int,
    xuehai_id: str,
    at: datetime,
) -> None:
    """特权角色被豁免的审计：**没有**违规入库，这条日志是唯一痕迹。"""
    try:
        await ZVMSLog(
            "",
            user_oid,
            f"特权角色（{'/'.join(roles)}）上报 {count} 条平台违规信号，"
            f"按策略只记录不封禁（设备 {xuehai_id or '未知'}）",
            at.timestamp(),
            type="platform_misuse_exempt",
            level="warning",
            xuehai_id=xuehai_id,
        ).insert_log()
    except Exception:
        _logger.exception("写入 platform_misuse_exempt 日志失败")


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

    流程（顺序即语义）：角色豁免 → 白名单过滤 → 逐条窗口去重 → 入库 + 审计 →
    取规则 → 窗口内**批次**计数 → ``count >= threshold`` 且当前无生效封禁时落封禁。

    返回 ``{"recorded", "duplicates", "banned", "rule_code"}``：``recorded`` 是
    真正新增的证据条数（客户端可据此判断是否被去重），``duplicates`` 是被窗口
    挡掉的条数，两者之和只统计合法信号。特权角色只写审计不写证据，因此两项
    都是 0。
    """
    user_oid = str(user["id"])
    now = datetime.now()

    result = {
        "recorded": 0,
        "duplicates": 0,
        "banned": False,
        "rule_code": TRIGGER,
    }

    exempt = _exempt_roles(_roles(user.get("per")))
    if exempt:
        # 豁免必须在计数之前：特权角色的记录留在窗口里，换届/降级后会被一条
        # 无关上报当成「累计违规」触发封禁。
        await _log_exempt(
            user_oid, exempt, len(signals or []), xuehai_id, now
        )
        return result

    violations = backends.get_violations_repo()
    batch_id = new_oid()
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
            user_oid,
            TRIGGER,
            signal,
            xuehai_id=xuehai_id or None,
            detail=detail,
            batch_id=batch_id,
        )
        recorded += 1
        await _log_violation(user_oid, signal, detail, xuehai_id, now)

    result["recorded"] = recorded
    result["duplicates"] = duplicates
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
    count = await violations.count_batches_since(user_oid, TRIGGER, since)
    if count < threshold:
        return result

    if await _ban_active(user_oid):
        # 已经封着：本批只是新证据，不该再推一档梯度。
        return result

    reason = (
        f"{window_hours} 小时内累计 {count} 次平台违规使用"
        f"（阈值 {threshold} 次）"
    )
    try:
        await device_guard.apply_account_ban(user_oid, rule_code, reason)
    except Exception:
        # 封禁落库失败不回滚已入库的证据：留痕给运维补封。
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
