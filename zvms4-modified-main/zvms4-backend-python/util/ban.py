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

from datetime import datetime
from typing import NoReturn
from urllib.parse import quote

from fastapi import HTTPException

# 封禁原因缺失时的兜底文案：老数据（V013 之前的封禁）没有原因列，
# 不能让前端展示空白警告。
DEFAULT_BAN_REASON = "违反平台使用规定"

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


def ban_reason_of(doc: dict | None) -> str:
    """封禁原因；缺失/空白时回退到 :data:`DEFAULT_BAN_REASON`。"""
    reason = (doc or {}).get("ban_reason")
    if isinstance(reason, str) and reason.strip():
        return reason.strip()
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
    "ban_is_active",
    "ban_outranks",
    "ban_reason_of",
    "ban_until_epoch",
    "ban_headers",
    "raise_banned",
]
