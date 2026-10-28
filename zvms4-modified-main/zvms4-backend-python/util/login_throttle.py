"""登录端点的进程内滑动窗口频控。

按「账号 + 来源 IP」两条维度同时计数：账号档挡单账号暴破，IP 档挡同段
轮换账号的撞库。校园网整班共享出口 IP，IP 档因此明显宽松。

进程内状态与 ``services.ai.rate_limit`` 同一前提：单 worker 部署时它就是全局
限制；多 worker 时每进程各算各的窗口。时间基准用 ``time.monotonic()``，
避免 NTP 跳变把窗口拉成负长度。
"""

from __future__ import annotations

import math
import time
from collections import deque

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


def check(account: str, client_ip: str) -> None:
    """记一次登录尝试；超限抛 :class:`LoginRateLimited`（此时不计数）。"""
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
