"""按用户 + 能力的分钟级频控（滑动窗口，进程内）。

月额度防长期滥用，这一层防的是「单个用户在一分钟内把单 worker 打满」：OCR 走
进程内引擎、chat 走外部上游，但两者都会占事件循环与并发槽，光有月额度挡不住
突发。

**状态位置**：``settings.REDIS_URI`` 配置了就写 Redis 的滑动窗口
（``db.state.window_hit``），此时限额就是全局限额，不再按 worker 数折算。
Redis 未配置或读取失败时回退到进程内 deque —— 单 worker 下即为全局限额；
多 worker 且没有 Redis 时，:func:`effective_limit` 仍按
:func:`services.ai.config.worker_count` 把每进程阈值折算成
``ceil(limit / N)``，让**全局**上限尽量贴近配置值。

时间基准用 ``time.monotonic()`` 而不是 wall clock：NTP 校时/手工改时间会让
``time.time()`` 跳变，窗口可能瞬间变成负长度或几十年，用户要么被无故放行要么
被永久锁死。monotonic 只保证单调，正好是窗口需要的性质。
"""

from __future__ import annotations

import logging
import math
import time
from collections import deque

from db import state

from . import config

_logger = logging.getLogger(__name__)

# 超过这个 key 数就顺手清一次：偶发 O(n) 远好过让字典跟着「用户数 × 能力数」
# 无限长大（每个空 deque 也要占内存）。不引后台线程——单 worker 里多一个线程
# 只是多一处要关的东西。
MAX_KEYS = 5000

_PROCESS_WORKERS = config.worker_count()
if _PROCESS_WORKERS > 1:
    # 只打一次（模块导入一次）。写清楚「哪些状态是进程内的」，否则运维看到
    # /health 里的 cooldown 只会以为它也是全局的。
    _logger.warning(
        "ai gateway is configured for %s worker processes: rate limit, provider "
        "cooldown and credential caches are PER-PROCESS, so the effective global "
        "limit is the per-process threshold (%s) times %s",
        _PROCESS_WORKERS,
        "rate limit / N",
        _PROCESS_WORKERS,
    )


class RateLimitError(Exception):
    """超频。``retry_after`` 直接进 ``Retry-After`` 头，至少 1 秒。"""

    code = "AI_RATE_LIMITED"

    def __init__(self, retry_after: int, message: str = "too many requests, retry later") -> None:
        super().__init__(message)
        self.retry_after = max(1, int(retry_after))
        self.message = message


_buckets: dict[tuple[str, str], deque[float]] = {}


def effective_limit(kind: str) -> int:
    """实际阈值：Redis 共享时即全局阈值；否则按 worker 数折算。

    无 Redis 的多 worker 部署下，本进程阈值 = ``ceil(全局阈值 / worker 数)``，
    至少 1；0 = 不限。向上取整而不是向下：向下会把「全局 10 次 / 3 worker」
    压成每进程 3 次（全局 9），比配置更紧；取整后最坏是全局略超一点，但不会
    因为除不尽而悄悄收紧额度。
    """
    limit = config.rate_limit_per_minute(kind)
    if limit <= 0:
        return 0
    if state.redis_configured():
        return limit
    workers = config.worker_count()
    if workers <= 1:
        return limit
    return max(1, math.ceil(limit / workers))


async def check(user_oid: str, kind: str, *, limit: int | None = None) -> None:
    """记一次调用；超限抛 :class:`RateLimitError`（此时**不**计数）。

    「超限不计数」是刻意的：被拒的请求不该把自己的窗口越推越长，否则一个客户端
    疯狂重试就能把自己永久锁在 429 上，而它其实只成功调用过 limit 次。

    ``limit`` 非空时覆盖配置阈值（无 Redis 时仍按 worker 数折算）：独立 API key
    的「60 次/分钟」不是全局配置里那种按能力划分的额度，而是另一条维度。
    ``user_oid`` 只当作**桶名**，调用方可以把复合键（``user:key_oid``）传进来。
    """
    limit = effective_limit(kind) if limit is None else _per_process(limit)
    if limit <= 0:
        return

    window = config.rate_limit_window_seconds()
    if state.redis_configured():
        result = await state.window_hit(f"ai:{user_oid}:{kind}", limit, window)
        if result is not None:
            allowed, retry_after = result
            if not allowed:
                raise RateLimitError(retry_after)
            return
    _local_check(str(user_oid), str(kind), limit, window)


def _local_check(user_oid: str, kind: str, limit: int, window: float) -> None:
    """Redis 未配置/不可用时的进程内滑动窗口（改造前的实现，语义不变）。"""
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


def _per_process(limit: int) -> int:
    """调用方给的绝对阈值也按 worker 数折算（非正数原样返回 = 不限）。"""
    if limit <= 0:
        return 0
    workers = config.worker_count()
    if workers <= 1:
        return limit
    return max(1, math.ceil(limit / workers))


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


__all__ = ["MAX_KEYS", "RateLimitError", "check", "effective_limit", "reset_state"]
