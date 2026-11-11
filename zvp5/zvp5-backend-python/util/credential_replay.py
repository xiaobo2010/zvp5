"""登录凭据的一次性消费表（Redis 共享，无 Redis 时退回进程内）。

``{password, time}`` 载荷不带 nonce：同一份 RSA 密文在 ±60 秒窗口内可以重复
提交，verify 拿到的密文也能当 login 用。这里在**解密成功后**按原始密文的
sha256 记一次消费，重复出现即拒绝。

**状态位置**：``settings.REDIS_URI`` 配置了就走 ``db.state.kv_set_nx``，多
worker 共享同一份消费表；Redis 未配置或不可用时退回进程内 dict + TTL（单
worker 下等价，多 worker 时每个进程各记一份）。

键里只放摘要，原始密文与密码不落任何存储与日志。
"""

from __future__ import annotations

import hashlib
import time

from db import state

TTL_SECONDS = 120.0
MAX_KEYS = 10000
MESSAGE = "登录凭证已失效，请重新登录"

_KEY_PREFIX = "cred:replay:"


class CredentialReplayed(Exception):
    """同一份密文第二次进入解密后的消费点。"""

    code = "CREDENTIAL_REPLAYED"

    def __init__(self) -> None:
        super().__init__(MESSAGE)
        self.message = MESSAGE


# sha256 -> 到期时刻（time.monotonic，不受系统时间跳变影响）
_used: dict[str, float] = {}


def digest(cert: str) -> str:
    """凭据摘要：只把 sha256 交给存储与日志，原文不外流。"""
    return hashlib.sha256(str(cert).strip().encode("utf-8")).hexdigest()


async def consume(cert: str) -> None:
    """登记一次消费；同一密文第二次调用抛 :class:`CredentialReplayed`。"""
    key = f"{_KEY_PREFIX}{digest(cert)}"
    fresh = await state.kv_set_nx(key, 1, TTL_SECONDS)
    if fresh is not None:
        if fresh:
            return
        raise CredentialReplayed()
    _local_consume(key)


def _local_consume(key: str) -> None:
    """Redis 未配置/不可用时的进程内实现（判与写在同一个同步片段内，无竞态）。"""
    now = time.monotonic()
    expiry = _used.get(key)
    if expiry is not None and expiry > now:
        raise CredentialReplayed()
    if len(_used) >= MAX_KEYS:
        _prune(now)
    _used[key] = now + TTL_SECONDS


def _prune(now: float) -> None:
    stale = [key for key, expiry in _used.items() if expiry <= now]
    for key in stale:
        _used.pop(key, None)


def reset_state() -> None:
    """仅供测试：清空进程内消费表。"""
    _used.clear()


__all__ = [
    "MAX_KEYS",
    "MESSAGE",
    "TTL_SECONDS",
    "CredentialReplayed",
    "consume",
    "digest",
    "reset_state",
]
