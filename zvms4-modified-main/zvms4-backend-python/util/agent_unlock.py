"""agent 的 30 分钟解锁门（进程内），以及管理员的「无限制模式」开关。

**为什么是进程内**：部署是单 worker（``config/supervisor.conf`` 没有 ``--workers``），
与 ``util.dual_auth`` 的授权缓存同一个前提。多 worker 时每个进程各自计时，效果是
「窗口命中率下降」而不是「越权放行」——保守方向，可以接受；真要横向扩容就把这里
换成共享存储。

**为什么按用户而不是按会话**：解锁证明的是「坐在键盘前的人知道密码」，不是「某个
浏览器标签页被授权」。换标签页不该重新输一次密码；换人则必须重新证明。

**为什么失败要锁定**：密码门背后是 agent 的写工具。没有失败计数时，它在功能上等
于一个可无限尝试的密码校验接口；5 次 / 10 分钟是给穷举设置成本，而不是防止误触。
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone

AGENT_UNLOCK_TTL_SECONDS = 1800
AGENT_UNLOCK_MAX_ATTEMPTS = 5
AGENT_UNLOCK_LOCK_SECONDS = 600

# user_oid -> 到期时刻（time.monotonic，不受系统时间跳变影响）
_unlocked: dict[str, float] = {}

# user_oid -> 最近连续失败时刻（monotonic）
_failures: dict[str, list[float]] = {}

# user_oid -> 锁定到期时刻（monotonic）
_locked: dict[str, float] = {}

# user_oid -> True；只由管理员端点写入
_unrestricted: dict[str, bool] = {}


def _env_number(name: str, fallback: float) -> float:
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return float(fallback)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return float(fallback)
    return value if value > 0 else float(fallback)


def unlock_ttl_seconds() -> float:
    """解锁窗口（秒）；``AGENT_UNLOCK_TTL_SECONDS`` 可覆盖，0/坏值回默认。"""
    return _env_number("AGENT_UNLOCK_TTL_SECONDS", AGENT_UNLOCK_TTL_SECONDS)


def unlock_max_attempts() -> int:
    return max(1, int(_env_number("AGENT_UNLOCK_MAX_ATTEMPTS", AGENT_UNLOCK_MAX_ATTEMPTS)))


def unlock_lock_seconds() -> float:
    return _env_number("AGENT_UNLOCK_LOCK_SECONDS", AGENT_UNLOCK_LOCK_SECONDS)


def _now(now: float | None) -> float:
    return time.monotonic() if now is None else float(now)


def is_unlocked(user_oid: str, now: float | None = None) -> bool:
    """``user_oid`` 是否仍在解锁窗口内；过期的记录顺手清掉。"""
    oid = str(user_oid)
    expires_at = _unlocked.get(oid)
    if expires_at is None:
        return False
    if _now(now) >= expires_at:
        _unlocked.pop(oid, None)
        return False
    return True


def mark_unlocked(user_oid: str, now: float | None = None) -> datetime:
    """记一次成功解锁，返回**墙上时钟**的到期时刻（给响应体用）。"""
    oid = str(user_oid)
    current = _now(now)
    ttl = unlock_ttl_seconds()
    _unlocked[oid] = current + ttl
    _failures.pop(oid, None)
    _locked.pop(oid, None)
    return datetime.now(timezone.utc) + timedelta(seconds=ttl)


def unlock_retry_after(user_oid: str, now: float | None = None) -> int:
    """锁定剩余秒数（向上取整）；未锁定回 0。"""
    oid = str(user_oid)
    until = _locked.get(oid)
    if until is None:
        return 0
    remaining = until - _now(now)
    if remaining <= 0:
        _locked.pop(oid, None)
        _failures.pop(oid, None)
        return 0
    return int(remaining) + (0 if remaining == int(remaining) else 1)


def record_unlock_failure(user_oid: str, now: float | None = None) -> bool:
    """记一次解锁失败；返回「本次之后是否已锁定」。"""
    oid = str(user_oid)
    current = _now(now)
    attempts = _failures.get(oid, [])
    attempts.append(current)
    if len(attempts) >= unlock_max_attempts():
        _failures.pop(oid, None)
        _locked[oid] = current + unlock_lock_seconds()
        return True
    _failures[oid] = attempts
    return False


def is_unrestricted(user_oid: str) -> bool:
    return bool(_unrestricted.get(str(user_oid)))


def set_unrestricted(user_oid: str, enabled: bool) -> None:
    oid = str(user_oid)
    if enabled:
        _unrestricted[oid] = True
    else:
        _unrestricted.pop(oid, None)


def clear_state() -> None:
    """清空全部进程内状态（测试与「立即失效」场景用）。"""
    _unlocked.clear()
    _failures.clear()
    _locked.clear()
    _unrestricted.clear()


__all__ = [
    "AGENT_UNLOCK_LOCK_SECONDS",
    "AGENT_UNLOCK_MAX_ATTEMPTS",
    "AGENT_UNLOCK_TTL_SECONDS",
    "clear_state",
    "is_unlocked",
    "is_unrestricted",
    "mark_unlocked",
    "record_unlock_failure",
    "set_unrestricted",
    "unlock_lock_seconds",
    "unlock_max_attempts",
    "unlock_retry_after",
    "unlock_ttl_seconds",
]
