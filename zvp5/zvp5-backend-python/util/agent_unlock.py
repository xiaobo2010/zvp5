"""agent 的 30 分钟解锁门（Redis 共享，无 Redis 时退回进程内）。

**状态位置**：``settings.REDIS_URI`` 配置了就把解锁窗口、锁定与失败计数写 Redis
（``db.state`` 的 KV 原语），多 worker / 多机看到的是同一份状态。Redis 未配置，
或某次读写不可用时退回改造前的进程内实现 —— 单 worker 下两者等价；多 worker
且没有 Redis 时每个进程各自计时，效果是「窗口命中率下降」而不是「越权放行」。

键（``db.state`` 会再加 ``zvms:state:kv:`` 前缀）：

* ``agent:unlock:ok:<oid>``   —— 值 ``1``，TTL = :func:`unlock_ttl_seconds`；
* ``agent:unlock:lock:<oid>`` —— 值 ``1``，TTL = :func:`unlock_lock_seconds`；
* ``agent:unlock:fail:<oid>`` —— 失败计数（``kv_incr``），TTL 同锁定窗口。

**为什么按用户而不是按会话**：解锁证明的是「坐在键盘前的人知道密码」，不是「某个
浏览器标签页被授权」。换标签页不该重新输一次密码；换人则必须重新证明。

**为什么失败要锁定**：密码门背后是 agent 的写工具。没有失败计数时，它在功能上等
于一个可无限尝试的密码校验接口；5 次 / 10 分钟是给穷举设置成本，而不是防止误触。
"""

from __future__ import annotations

import math
import os
import time
from datetime import datetime, timedelta, timezone

from db import state

AGENT_UNLOCK_TTL_SECONDS = 1800
AGENT_UNLOCK_MAX_ATTEMPTS = 5
AGENT_UNLOCK_LOCK_SECONDS = 600

# Redis 键前缀（``db.state`` 统一加 NS，这里只写逻辑前缀）。
_KEY_PREFIX = "agent:unlock:"
_OK_KEY = f"{_KEY_PREFIX}ok:"
_FAIL_KEY = f"{_KEY_PREFIX}fail:"
_LOCK_KEY = f"{_KEY_PREFIX}lock:"

# user_oid -> 到期时刻（time.monotonic，不受系统时间跳变影响）
_unlocked: dict[str, float] = {}

# user_oid -> 最近连续失败时刻（monotonic）
_failures: dict[str, list[float]] = {}

# user_oid -> 锁定到期时刻（monotonic）
_locked: dict[str, float] = {}


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


async def is_unlocked(user_oid: str, now: float | None = None) -> bool:
    """``user_oid`` 是否仍在解锁窗口内。

    ``now`` 只对进程内实现有意义（Redis 的窗口由键的 TTL 承担）。
    """
    oid = str(user_oid)
    if state.redis_configured():
        if await state.kv_get(f"{_OK_KEY}{oid}") is not None:
            return True
        # 未命中：可能真的没解锁，也可能 Redis 刚降级。继续用进程内状态兜底 ——
        # 那里只会有本进程自己写过的窗口，方向保守。
    return _local_is_unlocked(oid, now)


def _local_is_unlocked(oid: str, now: float | None) -> bool:
    """Redis 未配置/不可用时的进程内实现（语义与改造前一致）。"""
    expires_at = _unlocked.get(oid)
    if expires_at is None:
        return False
    if _now(now) >= expires_at:
        _unlocked.pop(oid, None)
        return False
    return True


async def mark_unlocked(user_oid: str, now: float | None = None) -> datetime:
    """记一次成功解锁，返回**墙上时钟**的到期时刻（给响应体用）。"""
    oid = str(user_oid)
    current = _now(now)
    ttl = unlock_ttl_seconds()
    if state.redis_configured():
        await state.kv_set(f"{_OK_KEY}{oid}", 1, ttl)
        await state.kv_delete(f"{_FAIL_KEY}{oid}", f"{_LOCK_KEY}{oid}")
    # 进程内镜像始终写：Redis 降级窗口里本进程仍能放行，且 TTL 与共享键一致。
    _unlocked[oid] = current + ttl
    _failures.pop(oid, None)
    _locked.pop(oid, None)
    return datetime.now(timezone.utc) + timedelta(seconds=ttl)


async def unlock_retry_after(user_oid: str, now: float | None = None) -> int:
    """锁定剩余秒数（向上取整）；未锁定回 0。``now`` 只对进程内回退生效。"""
    oid = str(user_oid)
    if state.redis_configured():
        remaining = await state.kv_ttl(f"{_LOCK_KEY}{oid}")
        if remaining > 0:
            return int(math.ceil(remaining))
    return _local_retry_after(oid, now)


def _local_retry_after(oid: str, now: float | None) -> int:
    until = _locked.get(oid)
    if until is None:
        return 0
    remaining = until - _now(now)
    if remaining <= 0:
        _locked.pop(oid, None)
        _failures.pop(oid, None)
        return 0
    return int(remaining) + (0 if remaining == int(remaining) else 1)


async def record_unlock_failure(user_oid: str, now: float | None = None) -> bool:
    """记一次解锁失败；返回「本次之后是否已锁定」。

    Redis 可用时计数走 ``kv_incr``（每次自增都重设 TTL：持续尝试会一直留在
    计数窗口里，而不是攒够 5 次后永久锁死）；不可用时回退到进程内计数。
    """
    oid = str(user_oid)
    lock_seconds = unlock_lock_seconds()
    if state.redis_configured():
        count = await state.kv_incr(f"{_FAIL_KEY}{oid}", lock_seconds)
        if count is not None:
            if count >= unlock_max_attempts():
                await state.kv_set(f"{_LOCK_KEY}{oid}", 1, lock_seconds)
                await state.kv_delete(f"{_FAIL_KEY}{oid}")
                return True
            return False
    return _local_record_failure(oid, now)


def _local_record_failure(oid: str, now: float | None) -> bool:
    current = _now(now)
    attempts = _failures.get(oid, [])
    attempts.append(current)
    if len(attempts) >= unlock_max_attempts():
        _failures.pop(oid, None)
        _locked[oid] = current + unlock_lock_seconds()
        return True
    _failures[oid] = attempts
    return False


async def clear_state() -> None:
    """清空全部状态（测试与「立即失效」场景用）。"""
    _unlocked.clear()
    _failures.clear()
    _locked.clear()
    if state.redis_configured():
        await state.kv_delete(*await state.kv_scan(_KEY_PREFIX))


__all__ = [
    "AGENT_UNLOCK_LOCK_SECONDS",
    "AGENT_UNLOCK_MAX_ATTEMPTS",
    "AGENT_UNLOCK_TTL_SECONDS",
    "clear_state",
    "is_unlocked",
    "mark_unlocked",
    "record_unlock_failure",
    "unlock_lock_seconds",
    "unlock_max_attempts",
    "unlock_retry_after",
    "unlock_ttl_seconds",
]
