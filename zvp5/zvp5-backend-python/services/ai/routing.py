"""供应商路由、熔断与全局并发闸门。

**熔断状态的位置**：``settings.REDIS_URI`` 配置了就写 Redis（``db.state`` 的
KV 原语），此时「失败 3 次熔断 60 秒」是全局事实 —— 多 worker / 多机部署下各
进程看到同一份冷却表。Redis 未配置、或任一原语失败（连接断了 / 超时）时回退到
进程内的两个 dict，语义与改造前完全一致：单 worker 下就是全局状态，多 worker
且无 Redis 时各算各的，最坏结果是多打几次失败的上游，不会放大成雪崩。

键约定（``route:`` 前缀）：

* ``route:fail:<entry key>`` —— 连续失败计数，TTL = ``COOLDOWN_SECONDS``
  （不刷新就等于自动衰减：一分钟没有新失败，计数从头开始）。
* ``route:cool:<entry key>`` —— 冷却标记，存在即冷却中，TTL 即剩余冷却时间。

冷却键是 ``entry["key"]``（``scope:oid`` / ``scope:name``）而不是 provider 名：
同名项在个人/共享/服务端三层里可能同时存在，用名字做键会让一层的失败把另外两
层也熔掉。
"""

from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager

from db import state

from . import config

FAILURE_THRESHOLD = 3
COOLDOWN_SECONDS = 60.0
SLOT_TIMEOUT_SECONDS = 2.0

NO_PROVIDER_MESSAGE = "no upstream available"

_FAIL_PREFIX = "route:fail:"
_COOL_PREFIX = "route:cool:"


class RouteError(Exception):
    """带 HTTP 语义的路由/上游错误，由路由层翻译成协议体。"""

    def __init__(self, code: str, message: str, status_code: int = 502):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


_failures: dict[str, int] = {}
_cooldown_until: dict[str, float] = {}
_semaphore: asyncio.Semaphore | None = None
_semaphore_size = 0
_semaphore_loop: asyncio.AbstractEventLoop | None = None


def _now() -> float:
    return time.monotonic()


async def mark_success(key: str) -> None:
    """上游调用成功：清零计数、撤掉冷却。

    本地表无条件清一遍（幂等）：Redis 路径下它本来就是空的，但 Redis 中途挂掉
    过、计数落在本地的那种混合状态也能被正确收拾。
    """
    key = str(key)
    if state.redis_configured():
        await state.kv_delete(f"{_FAIL_PREFIX}{key}", f"{_COOL_PREFIX}{key}")
    _local_success(key)


async def mark_failure(key: str) -> None:
    """记一次上游失败；达到阈值就进冷却并把计数清零。"""
    key = str(key)
    if state.redis_configured():
        count = await state.kv_incr(f"{_FAIL_PREFIX}{key}", COOLDOWN_SECONDS)
        if count is not None:
            if count < FAILURE_THRESHOLD:
                return
            opened = await state.kv_set(f"{_COOL_PREFIX}{key}", 1, COOLDOWN_SECONDS)
            if opened:
                # 熔断后把计数清零：冷却结束再给满 3 次机会，而不是一失败就立刻
                # 再熔断（与进程内实现一致）。
                await state.kv_delete(f"{_FAIL_PREFIX}{key}")
                return
        # 原语失败 → 落到进程内实现（下面那句）。
    _local_failure(key)


async def _in_cooldown(key: str) -> bool:
    """冷却中返回 True。

    Redis 说「有冷却」就是有；说「没有」时还要再看一眼本地表 —— ``kv_ttl`` 对
    「键不存在」和「Redis 不可用」都回 0，分不开。本地表在 Redis 健康时是空的，
    多这一次判断不会误熔。
    """
    key = str(key)
    if state.redis_configured():
        if await state.kv_ttl(f"{_COOL_PREFIX}{key}") > 0:
            return True
    return _local_in_cooldown(key)


def cooldown_scope() -> str:
    """冷却状态的可见范围：Redis 共享时是 ``shared``，否则只在本进程。"""
    return "shared" if state.redis_configured() else "process"


async def cooldown_state() -> dict[str, float]:
    """``{entry key: 剩余冷却秒数(1 位小数)}``，供 /health 观测。"""
    out = _local_cooldown_state()
    if state.redis_configured():
        for stored in await state.kv_scan(_COOL_PREFIX):
            remaining = await state.kv_ttl(stored)
            if remaining <= 0:
                continue
            out[stored[len(_COOL_PREFIX) :]] = round(remaining, 1)
    return out


def _local_success(key: str) -> None:
    _failures.pop(key, None)
    _cooldown_until.pop(key, None)


def _local_failure(key: str) -> None:
    count = _failures.get(key, 0) + 1
    if count >= FAILURE_THRESHOLD:
        # 熔断后把计数清零：冷却结束再给满 3 次机会，而不是一失败就立刻再熔断。
        _cooldown_until[key] = _now() + COOLDOWN_SECONDS
        _failures.pop(key, None)
        return
    _failures[key] = count


def _local_in_cooldown(key: str) -> bool:
    until = _cooldown_until.get(key)
    if until is None:
        return False
    if until <= _now():
        _cooldown_until.pop(key, None)
        _failures.pop(key, None)
        return False
    return True


def _local_cooldown_state() -> dict[str, float]:
    now = _now()
    out: dict[str, float] = {}
    for key, until in list(_cooldown_until.items()):
        remaining = until - now
        if remaining <= 0:
            _cooldown_until.pop(key, None)
            _failures.pop(key, None)
            continue
        out[key] = round(remaining, 1)
    return out


def reset_state() -> None:
    """仅供测试：清熔断状态与并发闸门。

    闸门也要清：``asyncio.Semaphore`` 的等待者 future 会绑在创建/首次等待时的
    event loop 上，``pytest-asyncio`` 每个用例一个新 loop，留着旧的会拿到已关闭
    loop 的 future。

    只清进程内的表：Redis 里的冷却键有 TTL，会自己过期；测试侧要清共享状态用
    ``db.cache._reset_for_tests()``（conftest 的 autouse fixture 已做）。
    """
    global _semaphore, _semaphore_size, _semaphore_loop
    _failures.clear()
    _cooldown_until.clear()
    _semaphore = None
    _semaphore_size = 0
    _semaphore_loop = None


async def candidates(entries: list[dict], forced: str | None = None) -> list[dict]:
    """按目录顺序返回「不在冷却期」的 entry。

    冷却键是 ``entry["key"]``（``scope:oid`` / ``scope:name``）而不是 provider
    名：同名项在个人/共享/服务端三层里可能同时存在，用名字做键会让一层的失败
    把另外两层也熔掉。

    ``forced`` 是 admin 显式指定的项，接受 name 或 oid；它必须真的出现在传入的
    目录里，否则等于绕过可见性把流量打去任意上游。
    """
    ordered = list(entries or [])
    if forced:
        target = str(forced)
        ordered = [
            entry
            for entry in ordered
            if target in (entry.get("name"), entry.get("oid"))
        ]
        if not ordered:
            raise RouteError("AI_NO_PROVIDER", NO_PROVIDER_MESSAGE, 503)

    result = []
    for entry in ordered:
        if not await _in_cooldown(str(entry.get("key") or "")):
            result.append(entry)
    if not result:
        raise RouteError("AI_NO_PROVIDER", NO_PROVIDER_MESSAGE, 503)
    return result


def _get_semaphore() -> asyncio.Semaphore:
    """按 (size, loop) 复用并发闸门。

    为什么要认 loop：``Semaphore`` 的等待者 future 绑在等待它的 event loop 上，
    跨 loop 复用一个已经用过的信号量，会把 future 交给一个已关闭/不属于当前
    loop 的对象。进程内正常只有一个 loop（uvicorn 单 worker），但测试每个用例
    一个新 loop、未来也可能出现多 loop 宿主——loop 变了就当缓存失效重建。
    """
    global _semaphore, _semaphore_size, _semaphore_loop
    size = config.max_concurrency()
    loop = asyncio.get_running_loop()
    if _semaphore is None or size != _semaphore_size or loop is not _semaphore_loop:
        _semaphore = asyncio.Semaphore(size)
        _semaphore_size = size
        _semaphore_loop = loop
    return _semaphore


@asynccontextmanager
async def slot():
    """全局并发闸门：等 2s 拿不到就快速失败，别把请求堆在事件循环里。"""
    semaphore = _get_semaphore()
    try:
        await asyncio.wait_for(semaphore.acquire(), SLOT_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        raise RouteError("AI_BUSY", "ai gateway is busy, retry later", 429) from None
    try:
        yield
    finally:
        semaphore.release()
