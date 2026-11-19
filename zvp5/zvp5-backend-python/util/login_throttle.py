"""登录端点的滑动窗口频控（Redis 共享，进程内回退）。

按「账号 + 来源 IP」两条维度同时计数：账号档挡单账号暴破，IP 档挡同段
轮换账号的撞库。校园网整班共享出口 IP，IP 档因此明显宽松。

**状态位置**：``settings.REDIS_URI`` 配置了就写 Redis 的滑动窗口
（``db.state.window_hit``，键 ``login:acct:<account>`` / ``login:ip:<ip>``），
此时两档限额就是**全局**限额，多 worker 共享同一份计数。Redis 未配置或命令
失败时回退到进程内 deque（:func:`_local_check`，改造前的实现，语义不变）——
单 worker 下它就是全局限制；多 worker 且没有 Redis 时每进程各算各的窗口。

**两档的一致性**：本地实现先判账号档、再判 IP 档，两档都过了才一起写入，所以
任一方被拒时两档都不计数。Redis 路径用只读的 IP 档预检复现这个顺序
（:func:`_peek`：先看 IP 档是否已满，满了就直接拒绝，账号档一次都不写），
再按「账号 → IP」写入。剩下的竞态是：预检通过后、写入之前如果别的 worker
把 IP 档填满，那次请求会消耗一个账号档名额——这不是漏拦（该请求仍被 429），
只是计数比「两档判定与写入原子完成」多算了一次账号档。彻底消除它需要在
``db.state`` 增一个「双档 Lua 原子判定」原语，超出本次改动范围。

时间基准用 ``time.monotonic()``，避免 NTP 跳变把窗口拉成负长度（Redis 路径的
时间基准由 ``db.state`` 统一，同样是拒绝不计数）。
"""

from __future__ import annotations

import math
import time
from collections import deque

from db import cache, state

ACCOUNT_LIMIT = 5
IP_LIMIT = 60
WINDOW_SECONDS = 60.0
MAX_KEYS = 10000

MESSAGE = "登录尝试过于频繁，请稍后再试"


class LoginRateLimited(Exception):
    """超频。``retry_after`` 秒后最早的那次尝试才滑出窗口。"""

    code = "AUTH_RATE_LIMITED"

    def __init__(self, retry_after: int) -> None:
        super().__init__(MESSAGE)
        self.retry_after = max(1, int(retry_after))
        self.message = MESSAGE


_buckets: dict[tuple[str, str], deque[float]] = {}


def _key(scope: str, value: str) -> tuple[str, str]:
    return (scope, value.strip().lower())


async def check(account: str, client_ip: str) -> None:
    """记一次登录尝试；超限抛 :class:`LoginRateLimited`（此时不计数）。

    两档都要过：账号档先判（被拒时只有已经拦下的账号档被消耗，不会连累同出口
    IP 的其他同学），IP 档后判。任一档超限即整次拒绝。
    """
    if state.redis_configured():
        result = await _shared_check(account, client_ip)
        if result is not None:
            allowed, retry_after = result
            if not allowed:
                raise LoginRateLimited(retry_after)
            return
    _local_check(account, client_ip)


async def _shared_check(account: str, client_ip: str) -> tuple[bool, int] | None:
    """走 ``db.state.window_hit`` 判两档；任一档不可用返回 ``None``（交给本地实现）。"""
    ip_key = f"login:ip:{client_ip.strip().lower()}"
    if client_ip.strip():
        retry_after = await _peek(ip_key, IP_LIMIT)
        if retry_after is None:
            return None
        if retry_after > 0:
            return False, retry_after
    tiers = (
        (
            f"login:acct:{account.strip().lower()}",
            ACCOUNT_LIMIT,
            bool(account.strip()),
        ),
        (ip_key, IP_LIMIT, bool(client_ip.strip())),
    )
    for key, limit, present in tiers:
        if not present:
            continue
        result = await state.window_hit(key, limit, WINDOW_SECONDS)
        if result is None:
            return None
        allowed, retry_after = result
        if not allowed:
            return False, retry_after
    return True, 0


async def _peek(key: str, limit: int) -> int | None:
    """只读判定一档：未满返回 ``0``，已满返回 ``retry_after``，不可用返回 ``None``。

    不写成员（清理过期成员除外），所以「IP 档已满」的请求不会连累账号档。
    """
    client = await cache._get_client()
    if client is None:
        return None
    now = time.time()
    zkey = f"{state.NS}:win:{key}"
    try:
        await client.zremrangebyscore(zkey, "-inf", now - WINDOW_SECONDS)
        count = int(await client.zcard(zkey))
        if count < limit:
            return 0
        oldest = await client.zrange(zkey, 0, 0, withscores=True)
    except Exception:  # noqa: BLE001 - 降级由本地实现接管
        cache.note_failure()
        return None
    if not oldest:
        return 0
    return max(1, math.ceil(WINDOW_SECONDS - (now - float(oldest[0][1]))))


def _local_check(account: str, client_ip: str) -> None:
    """Redis 未配置/不可用时的进程内滑动窗口（改造前的实现，语义不变）。"""
    now = time.monotonic()
    limits = (
        (_key("account", account), ACCOUNT_LIMIT, bool(account.strip())),
        (_key("ip", client_ip), IP_LIMIT, bool(client_ip.strip())),
    )
    pending: list[tuple[deque[float], float]] = []
    for key, limit, present in limits:
        if not present:
            continue
        bucket = _buckets.get(key)
        if bucket is None:
            if len(_buckets) >= MAX_KEYS:
                _prune(now)
            bucket = _buckets[key] = deque()
        cutoff = now - WINDOW_SECONDS
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()
        if len(bucket) >= limit:
            raise LoginRateLimited(math.ceil(WINDOW_SECONDS - (now - bucket[0])))
        pending.append((bucket, now))
    for bucket, stamp in pending:
        bucket.append(stamp)


def _prune(now: float) -> None:
    cutoff = now - WINDOW_SECONDS
    stale = [key for key, bucket in _buckets.items() if not bucket or bucket[-1] <= cutoff]
    for key in stale:
        _buckets.pop(key, None)


def reset_state() -> None:
    """仅供测试：清空所有窗口。"""
    _buckets.clear()


__all__ = [
    "ACCOUNT_LIMIT",
    "IP_LIMIT",
    "WINDOW_SECONDS",
    "LoginRateLimited",
    "MESSAGE",
    "check",
    "reset_state",
]
