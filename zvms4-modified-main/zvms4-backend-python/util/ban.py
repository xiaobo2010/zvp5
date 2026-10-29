"""封禁状态的唯一判定口径 + 对外错误契约。

**为什么不能只看 ``banned``**：``USERS.BANNED`` 是「有没有被落下封禁」的闸门，
而不是「此刻是否处于封禁中」。到期封禁没有后台任务去把它翻回 0（翻页任务在
多 worker 下必然漏/重），所以 ``banned=1`` + ``ban_until`` 已过期是**正常
存量状态**。任何判定都必须走 :func:`ban_is_active`，只读 ``banned`` 会把已经
刑满的用户永久锁在门外。

对外契约：403 + ``detail="USER_BANNED"``，原因与到期时间放在响应头
（``X-Ban-Reason`` / ``X-Ban-Until``）里。``detail`` 是机器可读标记，前端不得
直接展示；原因走头部是因为它是自由文本（可能含中文/标点），放 detail 会污染
这个稳定契约。
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import NoReturn
from urllib.parse import quote

from fastapi import HTTPException

# 封禁原因缺失时的兜底文案：老数据（V013 之前的封禁）没有原因列，
# 不能让前端展示空白警告。
DEFAULT_BAN_REASON = "违反平台使用规定"

# 审核分类 code → 中文名。写入侧与读侧共用同一份映射：写侧据此生成原因，
# 读侧据此翻译历史数据里残留的英文 code。未登记的 code 原样返回（见
# :func:`humanize_ban_reason`）——宁可露出一个陌生 code，也不要把原因吞成空白。
CATEGORY_LABELS: dict[str, str] = {
    "sexual": "色情内容",
    "violence": "暴力伤害",
    "weapons": "武器或爆炸物制作",
    "substances": "毒品",
    "gambling": "赌博",
    "self_harm": "自伤",
    "jailbreak": "越狱或提示词注入",
    "prompt_injection": "提示词注入",
    "instruction_override": "越权指令",
}

# 原因里只放原文摘要，完整证据在 BAN_VIOLATIONS.
REASON_EXCERPT_CHARS = 120

_BAN_HEADLINE = "AI 安全认证命中"
_FALLBACK_HEADLINE = "AI 安全认证命中违规内容"

# 不能用 ``json.loads`` 解历史原因：写入侧曾把 ``json.dumps(evidence)`` 截到
# 120 字再拼进原因，库里大量是**半截非法 JSON**，``json.loads`` 会直接抛异常
# 而丢掉整条原因。所以按字段名做正则提取，允许缺右引号/缺右括号。
_RULE_RE = re.compile(r'"rule"\s*:\s*"([^"]*)"?')
_INPUT_RE = re.compile(r'"input"\s*:\s*"([^"]{0,%d})' % REASON_EXCERPT_CHARS)
# 证据 JSON 的两个键名：判据是「这段看起来像 JSON 且带证据键」，避免把用户
# 原文里的普通花括号（``详情：{略}``）当成证据。
_EVIDENCE_KEYS = ('"rule"', '"input"')

BAN_REASON_HEADER = "X-Ban-Reason"
BAN_UNTIL_HEADER = "X-Ban-Until"


def _as_until(value) -> datetime | None:
    """``ban_until`` 只认 datetime；其它（缺失/NULL）一律视为永久。"""
    return value if isinstance(value, datetime) else None


def _comparable(until: datetime, now: datetime) -> datetime:
    """把 ``now`` 对齐到 ``until`` 的时区口径再比较。

    仓储返回的 Oracle ``TIMESTAMP`` 是 naive 的，而调用方可能传 aware 的
    ``datetime.now(timezone.utc)``；直接比较会抛 ``TypeError``。naive 一侧按
    本机本地时间理解（与 ``datetime.now()`` 的写法一致）。
    """
    if until.tzinfo is None and now.tzinfo is not None:
        return now.replace(tzinfo=None)
    if until.tzinfo is not None and now.tzinfo is None:
        return now.astimezone(until.tzinfo)
    return now


def ban_is_active(doc: dict | None, now: datetime | None = None) -> bool:
    """用户文档是否**此刻**处于封禁中。

    ``banned`` 为假 → 未封禁；``banned`` 为真且没有 ``ban_until`` → 永久封禁；
    有 ``ban_until`` 时只有未来时刻才算生效（过期即自动失效）。
    """
    if not doc or not doc.get("banned"):
        return False
    until = _as_until(doc.get("ban_until"))
    if until is None:
        return True
    return until > _comparable(until, now or datetime.now())


def _naive_until(value: datetime) -> datetime:
    """比较前统一去掉时区：双方都是同一台机器上的「本地时刻」。"""
    return value.replace(tzinfo=None) if value.tzinfo is not None else value


def ban_outranks(existing: dict | None, until: datetime | None) -> bool:
    """已有封禁是否**不弱于**本次算出的 ``until``（即不该被本次覆写）。

    规则是自动执行的，它只能加刑、不能减刑：管理员落的永久封禁
    （``banned=1`` 且 ``ban_until IS NULL``）被梯度首档改写成 1 天，等于
    用一条自动规则把人工裁定推翻。判定口径：

    * 本次 ``until is None``（永久）→ 本次最强，一律覆写（返回 False）；
    * 已有 ``ban_until IS NULL`` 且处于封禁态 → 已有是永久，保留（True）；
    * 其余按时间比大小，已有更晚则保留。

    ``banned`` 为假或已刑满（``ban_until`` 在过去）都不算「更强」：后者是正常
    存量状态，新封禁理应覆盖它。
    """
    if until is None:
        return False
    if not existing or not existing.get("banned"):
        return False
    current = _as_until(existing.get("ban_until"))
    if current is None:
        return True
    return _naive_until(current) > _naive_until(until)


def _clean_excerpt(value: str) -> str:
    """摘要只留纯文本：换行折叠成空格，引号/反斜杠去掉，超长截断。"""
    text = value.replace("\\n", " ").replace("\\r", " ").replace("\\t", " ")
    text = text.replace('\\"', "").replace('"', "").replace("\\", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text[:REASON_EXCERPT_CHARS]


def _humanize_payload(fragment: str) -> str:
    """从（可能被截断的）证据 JSON 片段生成中文原因；不像证据则返回空串。"""
    if not any(key in fragment for key in _EVIDENCE_KEYS):
        return ""
    rule_match = _RULE_RE.search(fragment)
    input_match = _INPUT_RE.search(fragment)
    rule = _clean_excerpt(rule_match.group(1)) if rule_match else ""
    excerpt = _clean_excerpt(input_match.group(1)) if input_match else ""
    if rule:
        head = f"{_BAN_HEADLINE}{CATEGORY_LABELS.get(rule, rule)}"
    else:
        head = _FALLBACK_HEADLINE
    return f"{head}：{excerpt}" if excerpt else head


def _json_fragment_bounds(text: str) -> tuple[int, int] | None:
    """定位文本里夹带的那段证据 JSON；找不到（或不含证据键）返回 ``None``。"""
    starts = [pos for pos in (text.find("{"), text.find("[")) if pos >= 0]
    if not starts:
        return None
    start = min(starts)
    end = max(text.rfind("}"), text.rfind("]")) + 1
    if end <= start:
        # 半截 JSON：从起始符一直吃到字符串末尾。
        end = len(text)
    if not any(key in text[start:end] for key in _EVIDENCE_KEYS):
        return None
    return start, end


def humanize_ban_reason(text) -> str:
    """把历史封禁原因里的证据 JSON 翻成可读中文。

    三种形态都要能处理：

    * 整段就是合法 JSON：``{"rule": "prompt_injection", "input": "..."}``；
    * 整段是被 120 字截断的非法 JSON（缺右引号/右括号）——这条是主力路径；
    * JSON 片段夹在中文里：``详情：{"rule": ...}``，只替换片段，前后中文保留。

    非 JSON 文本原样返回；空值返回 ``""``，由调用方决定兜底文案（403 头要的是
    :data:`DEFAULT_BAN_REASON`，列表接口要的是「没有原因」）。
    """
    if text is None:
        return ""
    raw = text if isinstance(text, str) else str(text)
    raw = raw.strip()
    if not raw:
        return ""

    if raw[0] in "{[":
        # 整段是 JSON（含被截断的）：能识别成证据就翻译，否则原样保留。
        return _humanize_payload(raw) or raw

    bounds = _json_fragment_bounds(raw)
    if bounds is None:
        return raw
    start, end = bounds
    translated = _humanize_payload(raw[start:end])
    if not translated:
        return raw
    return f"{raw[:start]}{translated}{raw[end:]}".strip()


def ban_reason_of(doc: dict | None) -> str:
    """封禁原因；缺失/空白时回退到 :data:`DEFAULT_BAN_REASON`。"""
    reason = (doc or {}).get("ban_reason")
    if isinstance(reason, str) and reason.strip():
        return humanize_ban_reason(reason) or DEFAULT_BAN_REASON
    return DEFAULT_BAN_REASON


def ban_until_epoch(doc: dict | None) -> int | None:
    """到期时刻的 epoch 秒；永久封禁返回 ``None``。"""
    until = _as_until((doc or {}).get("ban_until"))
    return int(until.timestamp()) if until is not None else None


def ban_headers(doc: dict | None) -> dict[str, str]:
    """403 响应头：原因（URL 编码，UTF-8）与到期 epoch 秒。

    原因必须 ``quote(..., safe="")``：HTTP 头是 latin-1，中文原样写入会直接
    在 ASGI 层炸掉；百分号编码后前端 ``decodeURIComponent`` 即可还原。
    永久封禁时 ``X-Ban-Until`` 是空串——空值比「缺头」更利于前端统一判断
    （``Number(value)`` 的 NaN 也能识别，但不能靠 key 是否存在区分）。
    """
    until = ban_until_epoch(doc)
    return {
        BAN_REASON_HEADER: quote(ban_reason_of(doc), safe="", encoding="utf-8"),
        BAN_UNTIL_HEADER: "" if until is None else str(until),
    }


def raise_banned(doc: dict | None) -> NoReturn:
    """统一的封禁拒绝：403 + ``USER_BANNED`` + 原因/到期头。"""
    raise HTTPException(
        status_code=403,
        detail="USER_BANNED",
        headers=ban_headers(doc),
    )


__all__ = [
    "DEFAULT_BAN_REASON",
    "BAN_REASON_HEADER",
    "BAN_UNTIL_HEADER",
    "CATEGORY_LABELS",
    "REASON_EXCERPT_CHARS",
    "humanize_ban_reason",
    "ban_is_active",
    "ban_outranks",
    "ban_reason_of",
    "ban_until_epoch",
    "ban_headers",
    "raise_banned",
]
