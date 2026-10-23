"""按用户 + 能力的分钟级频控（滑动窗口，进程内）。

月额度防长期滥用，这一层防的是「单个用户在一分钟内把单 worker 打满」：OCR 走
进程内引擎、chat 走外部上游，但两者都会占事件循环与并发槽，光有月额度挡不住
突发。

**单 worker 前提**：状态全在进程内存里（与 ``routing`` 的熔断、``credentials``
的目录缓存同一前提）。多 worker 部署时这里会退化成「每 worker 各自限流」，实际
上限是 N 倍；真要横向扩容，这层必须换成 Redis 的滑动窗口/令牌桶。

时间基准用 ``time.monotonic()`` 而不是 wall clock：NTP 校时/手工改时间会让
``time.time()`` 跳变，窗口可能瞬间变成负长度或几十年，用户要么被无故放行要么
被永久锁死。monotonic 只保证单调，正好是窗口需要的性质。
"""

from __future__ import annotations

import math
import time
from collections import deque

from . import config

# 超过这个 key 数就顺手清一次：偶发 O(n) 远好过让字典跟着「用户数 × 能力数」
# 无限长大（每个空 deque 也要占内存）。不引后台线程——单 worker 里多一个线程
# 只是多一处要关的东西。
MAX_KEYS = 5000


class RateLimitError(Exception):
    """超频。``retry_after`` 直接进 ``Retry-After`` 头，至少 1 秒。"""

    code = "AI_RATE_LIMITED"

    def __init__(self, retry_after: int, message: str = "too many requests, retry later") -> None:
        super().__init__(message)
        self.retry_after = max(1, int(retry_after))
        self.message = message


_buckets: dict[tuple[str, str], deque[float]] = {}


def check(user_oid: str, kind: str) -> None:
    """记一次调用；超限抛 :class:`RateLimitError`（此时**不**计数）。

    「超限不计数」是刻意的：被拒的请求不该把自己的窗口越推越长，否则一个客户端
    疯狂重试就能把自己永久锁在 429 上，而它其实只成功调用过 limit 次。
    """
    limit = config.rate_limit_per_minute(kind)
    if limit <= 0:
        return

    window = config.rate_limit_window_seconds()
    now = time.monotonic()
    key = (str(user_oid), str(kind))

    bucket = _buckets.get(key)
    if bucket is None:
        if len(_buckets) >= MAX_KEYS:
            _prune(now, window)
        bucket = _buckets[key] = deque()

    cutoff = now - window
    while bucket and bucket[0] <= cutoff:
        bucket.popleft()

    if len(bucket) >= limit:
        # 窗口要等到最早那次滑出去为止；向上取整避免 Retry-After: 0 让客户端
        # 立刻重试又立刻被拒。
        raise RateLimitError(math.ceil(window - (now - bucket[0])))

    bucket.append(now)


def _prune(now: float, window: float) -> None:
    """清掉空桶与「最后一次调用也已经过期」的桶（O(n)，只在 key 数触顶时跑）。"""
    cutoff = now - window
    stale = [
        key
        for key, bucket in _buckets.items()
        if not bucket or bucket[-1] <= cutoff
    ]
    for key in stale:
        _buckets.pop(key, None)


def reset_state() -> None:
    """仅供测试：清空所有窗口。"""
    _buckets.clear()


__all__ = ["MAX_KEYS", "RateLimitError", "check", "reset_state"]
