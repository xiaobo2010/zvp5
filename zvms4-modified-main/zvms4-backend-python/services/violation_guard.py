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

**agent 越狱是另一条语义：先警告、再梯度封禁**。证据层与 ``platform_misuse``
共用（白名单/去重/批次号），但处罚多一层 ``USER_WARNINGS``：自然月内首次命中
只落警告，同月第二次才交给 ``device_guard`` 走梯度。月度归零靠唯一索引，没有
cron；同一次违规的重试（证据被去重）绝不升级处罚。
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
from util.period import period_now

_logger = logging.getLogger(__name__)

TRIGGER = "platform_misuse"

# agent 越狱的检查项/规则 code：与 V037 种子的 ``code`` 一致。梯度由
# ``device_guard.apply_account_ban`` 按 ``ban_records`` 的累计次数决定。
AGENT_JAILBREAK_TRIGGER = "agent_jailbreak"

# 信号白名单：客户端只能报「观察到了什么形态」，不能自己发明类别。
# ``agent_jailbreak`` 由服务端在 agent 输入审核命中时上报，客户端仍不可传。
SIGNALS = ("iframe", "bridge", "devtools", "agent_jailbreak")

# 信号 → 检查项（BAN_RULES.TRIGGER_CODE）。没列出的都归属 ``TRIGGER``；agent 越狱
# 单独一条规则，因为它的梯度与「平台违规使用」不是同一件事。
SIGNAL_TRIGGERS = {"agent_jailbreak": "agent_jailbreak"}

# 去重窗口：同 (user, signal) 窗口内只记一条。``devtools`` 给更长窗口，因为
# 它按采样上报（窗口尺寸差/计时器抖动），短时间内天然会连续命中。
DEDUP_WINDOW_SECONDS = 300
DEVTOOLS_DEDUP_WINDOW_SECONDS = 1800

# 规则缺失/坏数据时的产品默认值（与 V001/V019 种子、ban_rules.DEFAULT_CONFIG 一致）。
DEFAULT_CONFIG = {"threshold": 3, "window_hours": 24}

# DB 列宽即契约：超长详情在服务端截断，不让一次上报因为一个长字符串丢整批。
MAX_DETAIL_CHARS = 500
# BAN_RECORDS.REASON / USERS.BAN_REASON 都是 VARCHAR2(512)，留一点余量。
MAX_REASON_CHARS = 500
# 封禁理由里只放 detail 的摘要：整段详情已经在 ban_violations 里。
REASON_DETAIL_CHARS = 120

# 角色豁免：这些角色即使在别的账号视角下「违规」，也可能是工作行为。
# 与 ``services.device_guard.PRIVILEGED_ROLES`` 对齐（团支书也在其中）。
NO_AUTO_BAN_ROLES = ("admin", "department", "secretary")

# agent 越狱单独豁免口径：AI 门禁默认只放行 admin/department，若沿用上面的豁免，
# 学生会的越狱证据永远只留审计、封禁规则形同虚设。admin 保留豁免——有无限制模式
# 兜底，且自动封管理员可能把平台锁死。
AGENT_JAILBREAK_EXEMPT_ROLES = ("admin",)

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


def _clip_reason(text: str) -> str:
    if len(text) <= MAX_REASON_CHARS:
        return text
    return text[: MAX_REASON_CHARS - 1] + "…"


def _detail_excerpt(details: list[str]) -> str:
    """多信号合并后的详情摘要：去重、前 ``REASON_DETAIL_CHARS`` 字、超长加省略号。"""
    joined = " | ".join(dict.fromkeys(details))
    if len(joined) <= REASON_DETAIL_CHARS:
        return joined
    return joined[: REASON_DETAIL_CHARS - 1] + "…"


def _ban_reason(
    *,
    window_hours: int,
    count: int,
    threshold: int,
    rule_code: str,
    signals: list[str],
    details: list[str],
) -> str:
    """管理员在封禁列表里要能直接读出「哪条规则、哪个信号、什么内容」。"""
    signal_text = "/".join(dict.fromkeys(signals)) or "unknown"
    reason = (
        f"{window_hours} 小时内累计 {count} 次平台违规使用"
        f"（阈值 {threshold} 次，规则 {rule_code}，信号 {signal_text}"
    )
    excerpt = _detail_excerpt(details)
    if excerpt:
        reason += f"，详情：{excerpt}"
    reason += "）"
    return _clip_reason(reason)


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


def _exempt_roles(
    roles: tuple[str, ...], allowed: tuple[str, ...] | None = None
) -> tuple[str, ...]:
    baseline = allowed if allowed is not None else NO_AUTO_BAN_ROLES
    return tuple(role for role in roles if role in baseline)


async def _rule(trigger: str = TRIGGER) -> dict | None:
    """按检查项取规则；读表失败按「只记录」处理（fail-open）。

    证据已经入库，规则表读不到是运维问题：让整批上报 500 只会丢掉客户端手里的
    事实，而重试还会被去重窗口挡掉，等于永久失去这条证据。
    """
    try:
        return await ban_rules.get_rule_by_trigger(trigger)
    except Exception:
        _logger.exception("读取 %s 规则失败，本次只记录违规不判定封禁", trigger)
        return None


def _rule_trigger(triggers: set[str]) -> str:
    """一批证据归属哪条规则：单一种类的非默认信号才换检查项。

    混合批次沿用 ``platform_misuse``：一次请求同时报「平台违规」与「越狱」时，
    用默认规则比按先到先得挑一条更可预测。
    """
    if len(triggers) == 1:
        only = next(iter(triggers))
        if only != TRIGGER:
            return only
    return TRIGGER


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


async def _log_warning(
    user_oid: str, period: str, detail: str | None, xuehai_id: str, at: datetime
) -> None:
    """月度首次警告的审计：警告账本写入成功后才记，失败不影响结论。"""
    try:
        await ZVMSLog(
            "",
            user_oid,
            f"AI 越狱/提示注入首次警告（月份 {period}，详情：{detail or '无'}，"
            f"设备 {xuehai_id or '未知'}）",
            at.timestamp(),
            type="agent_jailbreak_warning",
            level="warning",
            xuehai_id=xuehai_id,
        ).insert_log()
    except Exception:
        _logger.exception("写入 agent_jailbreak_warning 日志失败（警告已生效）")


async def _ingest_signals(
    user_oid: str,
    signals: list[dict] | None,
    xuehai_id: str,
    now: datetime,
) -> dict:
    """白名单过滤 → 逐条窗口去重 → 入库 + 审计。

    抽成函数是为了让「平台违规」与「agent 越狱」共用同一份证据口径：两处各写
    一遍，去重窗口与批次号迟早会漂。返回本批事实，``recorded == 0`` 表示整批都
    被窗口挡掉（或没有合法信号）。
    """
    violations = backends.get_violations_repo()
    batch_id = new_oid()
    recorded = 0
    duplicates = 0
    triggers: set[str] = set()
    names: list[str] = []
    details: list[str] = []
    for item in signals or []:
        signal = (item or {}).get("signal")
        if not isinstance(signal, str) or signal not in SIGNALS:
            # 客户端拼错信号名不该让整批 422：忽略这一条，其余照记。
            _logger.warning("忽略未知的平台违规信号 %r", signal)
            continue

        trigger = SIGNAL_TRIGGERS.get(signal, TRIGGER)
        detail = _clean_detail((item or {}).get("detail"))
        since = now - timedelta(seconds=dedup_window_seconds(signal))
        if await violations.exists_recent(user_oid, signal, since):
            duplicates += 1
            continue

        await violations.insert(
            user_oid,
            trigger,
            signal,
            xuehai_id=xuehai_id or None,
            detail=detail,
            batch_id=batch_id,
        )
        recorded += 1
        triggers.add(trigger)
        names.append(signal)
        if detail:
            details.append(detail)
        await _log_violation(user_oid, signal, detail, xuehai_id, now)

    return {
        "recorded": recorded,
        "duplicates": duplicates,
        "triggers": triggers,
        "signals": names,
        "details": details,
    }


async def record(
    user: dict,
    signals: list[dict],
    xuehai_id: str = "",
    exempt_roles: tuple[str, ...] | None = None,
) -> dict:
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

    exempt = _exempt_roles(_roles(user.get("per")), exempt_roles)
    if exempt:
        # 豁免必须在计数之前：特权角色的记录留在窗口里，换届/降级后会被一条
        # 无关上报当成「累计违规」触发封禁。
        await _log_exempt(
            user_oid, exempt, len(signals or []), xuehai_id, now
        )
        return result

    ingested = await _ingest_signals(user_oid, signals, xuehai_id, now)
    recorded = ingested["recorded"]
    recorded_triggers = ingested["triggers"]
    recorded_signals = ingested["signals"]
    recorded_details = ingested["details"]

    result["recorded"] = recorded
    result["duplicates"] = ingested["duplicates"]
    if recorded == 0:
        # 全是重复或非法信号：计数没有变化，没必要再读规则表。
        return result

    rule_trigger = _rule_trigger(recorded_triggers)
    rule = await _rule(rule_trigger)
    if rule is None or not rule.get("enabled"):
        return result

    # 梯度与流水必须按规则自己的 code 记：管理员新建的规则 code 可能不是
    # ``platform_misuse``，写死 trigger 会让 count_for_user_rule 与取梯度的
    # ``get_by_code`` 对不上，处罚回落成默认 [1]。
    rule_code = str(rule.get("code") or rule_trigger)
    result["rule_code"] = rule_code

    config = _config(rule)
    threshold = config["threshold"]
    window_hours = config["window_hours"]
    since = now - timedelta(hours=window_hours)
    violations = backends.get_violations_repo()
    count = await violations.count_batches_since(user_oid, rule_trigger, since)
    if count < threshold:
        return result

    if await _ban_active(user_oid):
        # 已经封着：本批只是新证据，不该再推一档梯度。
        return result

    reason = _ban_reason(
        window_hours=window_hours,
        count=count,
        threshold=threshold,
        rule_code=rule_code,
        signals=recorded_signals,
        details=recorded_details,
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


def _warning_reason(category: str) -> str:
    text = (
        f"AI 安全认证命中（{category or 'destructive_request'}）："
        "本月首次警告，再犯将按梯度封禁"
    )
    return _clip_reason(text)


def _jailbreak_ban_reason(category: str, detail: str | None) -> str:
    text = f"AI 安全认证命中越狱/提示词注入（{category or 'destructive_request'}）"
    excerpt = (detail or "").strip()[:REASON_DETAIL_CHARS]
    if excerpt:
        text += f"：{excerpt}"
    return _clip_reason(text)


async def record_agent_jailbreak(
    user: dict,
    *,
    xuehai_id: str = "",
    rule: str = "",
    detail: str | None = None,
) -> dict:
    """agent 越狱/提示注入命中：**每月首次只警告，同月再犯按梯度封号**。

    为什么不是命中即封：LLM 审核有灰区，一次判定直接封号会把「正常提问被判错」
    的成本抬到账号级别。第一次落警告账本（``USER_WARNINGS``，按自然月唯一），
    同月第二次才交给 ``device_guard`` 走 ``ban_rules`` 的梯度。

    为什么重试不能升级处罚：请求被拒后客户端可能原样重发，而证据层有 5 分钟
    去重窗口。``recorded == 0``（本批全是重复）时绝不落封禁——否则一次网络
    重试就能把用户从「警告」推到「封号」。

    ``xuehai_id`` 来自请求头（真实设备号），不再拿 ``users.id``（学号）冒充：
    违规证据要能回答「哪台机器在试图越狱」，学号是账号不是设备。
    ``detail`` 以 JSON 落库，含命中的规则名与用户输入摘要（前 200 字符）——
    越狱原文受角色白名单约束另存 ``AI_PROMPT_AUDIT``，这里只留复核所需的片段。
    """
    user_oid = str(user["id"])
    now = datetime.now()

    result = {
        "recorded": 0,
        "duplicates": 0,
        "warned": False,
        "banned": False,
        "rule_code": AGENT_JAILBREAK_TRIGGER,
    }

    exempt = _exempt_roles(_roles(user.get("per")), AGENT_JAILBREAK_EXEMPT_ROLES)
    if exempt:
        # admin 豁免：自动封管理员可能把平台锁死，且无限制模式已覆盖该场景。
        await _log_exempt(user_oid, exempt, 1, xuehai_id, now)
        result["exempt"] = True
        return result

    evidence: dict[str, str] = {}
    if rule:
        evidence["rule"] = str(rule)
    excerpt = (detail or "").strip()[:200]
    if excerpt:
        evidence["input"] = excerpt
    payload = json.dumps(evidence, ensure_ascii=False) if evidence else None

    ingested = await _ingest_signals(
        user_oid,
        [{"signal": "agent_jailbreak", "detail": payload}],
        xuehai_id,
        now,
    )
    result["recorded"] = ingested["recorded"]
    result["duplicates"] = ingested["duplicates"]

    if await _ban_active(user_oid):
        # 已经封着：本次只是新证据，不再重复落封、也不再发警告。
        result["banned"] = True
        result["skipped"] = "already_banned"
        return result

    category = str(rule or AGENT_JAILBREAK_TRIGGER)
    period = period_now(now)
    repo = backends.get_user_warnings_repo()

    if result["recorded"] == 0:
        # 证据没新增 = 同一次违规的重试（窗口内去重）。已有本月警告就直接回
        # 「已警告」，绝不升级；账本还没有时才补一条（兼容旧调用路径）。
        existing = await repo.get(user_oid, AGENT_JAILBREAK_TRIGGER, period)
        if existing is not None:
            result["warned"] = True
            result["duplicate"] = True
            return result

    try:
        fresh = await repo.try_record(
            user_oid,
            AGENT_JAILBREAK_TRIGGER,
            period,
            _warning_reason(category),
            payload,
        )
    except Exception:
        # 账本写不进去时**不封号**：警告是封禁的前置事实，读不到它就没有
        # 「同月第二次」这个结论，按 fail-open 放行这一次。
        _logger.exception("写入 agent 越狱警告失败，本次不升级为封禁")
        result["error"] = "warning_store_failed"
        return result

    if fresh:
        result["warned"] = True
        await _log_warning(user_oid, period, payload, xuehai_id, now)
        return result

    reason = _jailbreak_ban_reason(category, payload)
    try:
        ban = await device_guard.apply_account_ban(
            user_oid, AGENT_JAILBREAK_TRIGGER, reason
        )
    except Exception:
        # 封禁落库失败不回滚已入库的证据与警告：留痕给运维补封。
        _logger.exception("agent 越狱封禁落库失败（证据与警告已入库）")
        return result

    result["banned"] = True
    result["until"] = ban.get("until")
    result["days"] = ban.get("days")
    result["offense"] = ban.get("offense")
    await _log_ban(user_oid, reason, xuehai_id, now)
    return result


__all__ = [
    "TRIGGER",
    "AGENT_JAILBREAK_TRIGGER",
    "SIGNALS",
    "SIGNAL_TRIGGERS",
    "DEDUP_WINDOW_SECONDS",
    "DEVTOOLS_DEDUP_WINDOW_SECONDS",
    "DEFAULT_CONFIG",
    "MAX_DETAIL_CHARS",
    "NO_AUTO_BAN_ROLES",
    "dedup_window_seconds",
    "record",
    "record_agent_jailbreak",
]
