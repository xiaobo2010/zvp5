"""重查询端点的进程内读预算（每用户 60 次/分钟滑动窗口）。

与 ``util.login_throttle`` 同一前提：单 worker 部署时它就是全局限制，多 worker
时每进程各算各的窗口；时间基准用 ``time.monotonic()``。管理员与普通用户同一
档位——读预算挡的是「一个账号把库拖死」，与权限高低无关。
"""

from __future__ import annotations

import math
import time
from collections import deque

from fastapi import Depends, HTTPException

from util.object_id import get_current_user

LIMIT = 60
WINDOW_SECONDS = 60.0
MAX_KEYS = 10000

CODE = "READ_RATE_LIMITED"
MESSAGE = "查询过于频繁，请稍后再试"


class ReadRateLimited(Exception):
    """超频。``retry_after`` 秒后最早的那次查询才滑出窗口。"""

    code = CODE

    def __init__(self, retry_after: int) -> None:
        super().__init__(MESSAGE)
        self.retry_after = max(1, int(retry_after))
        self.message = MESSAGE


_buckets: dict[str, deque[float]] = {}


def check(user_oid: str) -> None:
    """记一次重查询；超限抛 :class:`ReadRateLimited`（此时不计数）。"""
    key = str(user_oid).strip()
    if not key:
        return
    now = time.monotonic()
    bucket = _buckets.get(key)
    if bucket is None:
        if len(_buckets) >= MAX_KEYS:
            _prune(now)
        bucket = _buckets[key] = deque()
    cutoff = now - WINDOW_SECONDS
    while bucket and bucket[0] <= cutoff:
        bucket.popleft()
    if len(bucket) >= LIMIT:
        raise ReadRateLimited(math.ceil(WINDOW_SECONDS - (now - bucket[0])))
    bucket.append(now)


def _prune(now: float) -> None:
    cutoff = now - WINDOW_SECONDS
    stale = [key for key, bucket in _buckets.items() if not bucket or bucket[-1] <= cutoff]
    for key in stale:
        _buckets.pop(key, None)


def reset_state() -> None:
    """仅供测试：清空所有窗口。"""
    _buckets.clear()


async def read_budget(user=Depends(get_current_user)) -> None:
    """重查询端点的依赖闸：超限 429 + ``Retry-After``。"""
    try:
        check(str(user.get("id") or ""))
    except ReadRateLimited as exc:
        raise HTTPException(
            status_code=429,
            detail={"code": exc.code, "message": exc.message},
            headers={"Retry-After": str(exc.retry_after)},
        ) from None


__all__ = [
    "CODE",
    "LIMIT",
    "MESSAGE",
    "MAX_KEYS",
    "WINDOW_SECONDS",
    "ReadRateLimited",
    "check",
    "read_budget",
    "reset_state",
]
