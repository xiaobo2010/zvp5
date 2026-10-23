"""供应商路由、熔断与全局并发闸门。

状态全在进程内存里：部署是单 worker（与 ``services/ssh_bridge`` 的 ticket 同一
前提），所以不引 Redis。多 worker 时每个进程各算各的熔断，最坏结果是多打几次
失败的上游，不会放大成雪崩。
"""

from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager

from . import config

FAILURE_THRESHOLD = 3
COOLDOWN_SECONDS = 60.0
SLOT_TIMEOUT_SECONDS = 2.0

NO_PROVIDER_MESSAGE = "no upstream available"


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


def mark_success(key: str) -> None:
    _failures.pop(key, None)
    _cooldown_until.pop(key, None)


def mark_failure(key: str) -> None:
    count = _failures.get(key, 0) + 1
    if count >= FAILURE_THRESHOLD:
        # 熔断后把计数清零：冷却结束再给满 3 次机会，而不是一失败就立刻再熔断。
        _cooldown_until[key] = _now() + COOLDOWN_SECONDS
        _failures.pop(key, None)
        return
    _failures[key] = count


def _in_cooldown(key: str) -> bool:
    until = _cooldown_until.get(key)
    if until is None:
        return False
    if until <= _now():
        _cooldown_until.pop(key, None)
        _failures.pop(key, None)
        return False
    return True


def cooldown_state() -> dict[str, float]:
    now = _now()
    state: dict[str, float] = {}
    for key, until in list(_cooldown_until.items()):
        remaining = until - now
        if remaining <= 0:
            _cooldown_until.pop(key, None)
            _failures.pop(key, None)
            continue
        state[key] = round(remaining, 1)
    return state


def reset_state() -> None:
    """仅供测试：清熔断状态与并发闸门。

    闸门也要清：``asyncio.Semaphore`` 的等待者 future 会绑在创建/首次等待时的
    event loop 上，``pytest-asyncio`` 每个用例一个新 loop，留着旧的会拿到已关闭
    loop 的 future。
    """
    global _semaphore, _semaphore_size, _semaphore_loop
    _failures.clear()
    _cooldown_until.clear()
    _semaphore = None
    _semaphore_size = 0
    _semaphore_loop = None


def candidates(entries: list[dict], forced: str | None = None) -> list[dict]:
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

    result = [
        entry for entry in ordered if not _in_cooldown(str(entry.get("key") or ""))
    ]
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
