"""Redis 共享运行态原语：滑动窗口与短 TTL KV。

背景：``services/ai/rate_limit`` / ``util/login_throttle`` / ``util/read_throttle``
的窗口、``services/ai/routing`` 的熔断、``util/agent_unlock`` 的解锁门、以及
``services/ai/api_keys`` / ``credentials`` 的目录缓存原先都在进程内存里。单
worker 部署时它们恰好等于全局状态；一旦多进程/多机，各算各的，限额与熔断都会
按进程数放大。

本模块只提供**原语**，不替调用方决定语义：

* :func:`window_hit` —— 滑动窗口计数，Redis 用 ZSET 实现（清理过期成员 →
  计数 → 命中则追加）。返回 ``None`` 表示「Redis 不可用/未配置」，调用方应回退
  到自己的进程内实现（各模块原本的那套代码原样保留，行为不变）。
* :func:`kv_get` / :func:`kv_set` / :func:`kv_delete` / :func:`kv_ttl` —— 带 TTL
  的 JSON KV，值与 :mod:`db.cache` 共用类型标签编解码。

为什么不做「Redis 不可用时自动落本地」：每个调用方的本地实现语义不同（登录按
账号+IP 两档、熔断要区分成功/失败、解锁门还有失败锁定），在公共层重写一遍只会
制造第二份真相。原语失败就返回 ``None``，调用方用自己那套。
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

from db import cache

NS = "zvms:state"

#: 滑动窗口判定与写入必须在一个原子操作里完成：拆成 ZREMRANGEBYSCORE →
#: ZCARD → ZADD 时，并发请求会同时读到 ``count < limit`` 再各自 ZADD，
#: 限额按并发数放大。脚本内的时间基准仍由调用方传入（``now``），
#: 保持「窗口边界 = 调用方此刻 - window」的原有语义。
WINDOW_HIT_SCRIPT = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])

redis.call('ZREMRANGEBYSCORE', key, '-inf', now - window)
local count = redis.call('ZCARD', key)
if count >= limit then
  local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
  local retry = window
  if oldest[2] then
    retry = window - (now - tonumber(oldest[2]))
  end
  retry = math.ceil(retry)
  if retry < 1 then retry = 1 end
  return {0, retry}
end
redis.call('ZADD', key, now, ARGV[4])
redis.call('PEXPIRE', key, math.max(1, math.floor(window * 1000)))
return {1, 0}
"""

#: INCR 与 PEXPIRE 拆两次往返时，中间的断连/崩溃会留下**没有 TTL** 的计数器
#: （永久占用键空间且计数只增不减）。Lua 保证两者要么都生效要么都不生效。
KV_INCR_SCRIPT = """
local value = redis.call('INCR', KEYS[1])
redis.call('PEXPIRE', KEYS[1], ARGV[1])
return value
"""


def redis_configured() -> bool:
    """是否配置了 Redis（用于调用方决定「按全局阈值」还是「按进程折算」）。"""
    return cache.redis_configured()


async def window_hit(key: str, limit: int, window: float) -> tuple[bool, int] | None:
    """记一次调用并判定是否放行；``(allowed, retry_after_seconds)``。

    ``retry_after`` 只在拒绝时有意义（≥1 秒，直接进 ``Retry-After``）。
    被拒时**不**写入成员：与各模块原有实现一致，疯狂重试不该把自己越锁越久。
    """
    if limit <= 0:
        return True, 0
    client = await cache._get_client()
    if client is None:
        return None

    now = time.time()
    zkey = f"{NS}:win:{key}"
    member = f"{now:.6f}:{uuid.uuid4().hex[:8]}"
    try:
        allowed, retry = await client.eval(
            WINDOW_HIT_SCRIPT,
            1,
            zkey,
            f"{now:.6f}",
            window,
            limit,
            member,
        )
    except Exception:  # noqa: BLE001 - 降级由调用方的本地实现接管
        cache.note_failure()
        return None
    if int(allowed) == 1:
        return True, 0
    return False, max(1, int(retry))


async def kv_get(key: str) -> Any | None:
    client = await cache._get_client()
    if client is None:
        return None
    try:
        raw = await client.get(f"{NS}:kv:{key}")
    except Exception:  # noqa: BLE001
        cache.note_failure()
        return None
    if raw is None:
        return None
    try:
        return cache._decode(json.loads(raw))
    except Exception:  # noqa: BLE001 - 脏值按未命中处理
        return None


async def kv_set(key: str, value: Any, ttl: float) -> bool:
    client = await cache._get_client()
    if client is None:
        return False
    try:
        payload = json.dumps(
            cache._encode(value), ensure_ascii=False, separators=(",", ":"), default=str
        )
        await client.set(f"{NS}:kv:{key}", payload, px=max(1, int(ttl * 1000)))
        return True
    except Exception:  # noqa: BLE001
        cache.note_failure()
        return False


async def kv_delete(*keys: str) -> None:
    client = await cache._get_client()
    if client is None or not keys:
        return
    try:
        await client.delete(*[f"{NS}:kv:{key}" for key in keys])
    except Exception:  # noqa: BLE001
        cache.note_failure()


async def kv_ttl(key: str) -> float:
    """剩余 TTL（秒）；不存在/不可用回 0。"""
    client = await cache._get_client()
    if client is None:
        return 0.0
    try:
        raw = await client.pttl(f"{NS}:kv:{key}")
    except Exception:  # noqa: BLE001
        cache.note_failure()
        return 0.0
    if raw is None or int(raw) < 0:
        return 0.0
    return int(raw) / 1000.0


async def kv_incr(key: str, ttl: float) -> int | None:
    """原子自增并（重新）设置 TTL；返回新值，不可用返回 ``None``。"""
    client = await cache._get_client()
    if client is None:
        return None
    try:
        value = await client.eval(
            KV_INCR_SCRIPT, 1, f"{NS}:kv:{key}", max(1, int(ttl * 1000))
        )
        return int(value)
    except Exception:  # noqa: BLE001
        cache.note_failure()
        return None


async def kv_set_nx(key: str, value: Any, ttl: float) -> bool | None:
    """仅当 key 不存在时写入（``SET NX PX``）；返回是否写入，不可用返回 ``None``。"""
    client = await cache._get_client()
    if client is None:
        return None
    try:
        payload = json.dumps(
            cache._encode(value), ensure_ascii=False, separators=(",", ":"), default=str
        )
        return bool(
            await client.set(
                f"{NS}:kv:{key}", payload, px=max(1, int(ttl * 1000)), nx=True
            )
        )
    except Exception:  # noqa: BLE001
        cache.note_failure()
        return None


async def kv_scan(prefix: str) -> list[str]:
    """按前缀列出 key（不含 ``NS`` 前缀）；不可用返回空表。"""
    client = await cache._get_client()
    if client is None:
        return []
    pattern = f"{NS}:kv:{prefix}*"
    found: list[str] = []
    try:
        async for key in client.scan_iter(match=pattern, count=200):
            found.append(str(key).removeprefix(f"{NS}:kv:"))
    except Exception:  # noqa: BLE001
        cache.note_failure()
        return []
    return found
