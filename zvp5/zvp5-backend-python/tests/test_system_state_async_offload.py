"""维护态读路径的 async 版本：语义与同步版一致，且不再阻塞事件循环。

``maintenance_middleware`` 每请求走一次读路径；同步 sqlite 读落在事件循环线程
上时等待时间 1:1 变成事件循环停顿。这里钉住四件事：

* ``is_enabled_async`` / ``is_enabled_fresh_async`` 与同步版结果完全一致；
* 两级缓存语义不变（缓存命中不碰库，fresh 走自己的 1s 窗口）；
* 状态库故障仍然 fail-open（不抛、不 500）；
* 慢读期间事件循环仍在推进（读被挪到工作线程）；
* 热缓存命中不切线程，只有 miss / 过期才 ``asyncio.to_thread``。
"""

from __future__ import annotations

import asyncio
import sqlite3
import time

import pytest

from db import system_state

ACTOR = "64b7f0c2e1a3b4c5d6e7f901"


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch):
    monkeypatch.setattr(system_state, "_warned_failures", set())
    system_state.invalidate_cache()
    yield
    system_state.invalidate_cache()


@pytest.mark.asyncio
async def test_async_variants_match_the_sync_ones():
    assert await system_state.is_enabled_async() == system_state.is_enabled()
    assert (
        await system_state.is_enabled_fresh_async()
        == system_state.is_enabled_fresh()
    )

    system_state.set_state(True, "维护", "公告", ACTOR)
    assert await system_state.is_enabled_async() is True
    assert await system_state.is_enabled_fresh_async() is True

    system_state.set_state(False, None, None, ACTOR)
    assert await system_state.is_enabled_async() is False
    assert await system_state.is_enabled_fresh_async() is False


@pytest.mark.asyncio
async def test_async_reads_keep_cache_and_fresh_paths_distinct(monkeypatch):
    real_connect = system_state._connect
    calls = {"n": 0}

    def counting(*args, **kwargs):
        calls["n"] += 1
        return real_connect(*args, **kwargs)

    monkeypatch.setattr(system_state, "_connect", counting)
    system_state.invalidate_cache()

    assert await system_state.is_enabled_async() is False
    after_first = calls["n"]
    assert after_first >= 1

    # 2s 缓存命中：不再碰库。
    assert await system_state.is_enabled_async() is False
    assert calls["n"] == after_first

    # fresh 自己的 1s 缓存：第一次读库，第二次命中。
    assert await system_state.is_enabled_fresh_async() is False
    after_fresh = calls["n"]
    assert after_fresh > after_first
    assert await system_state.is_enabled_fresh_async() is False
    assert calls["n"] == after_fresh


@pytest.mark.asyncio
async def test_async_reads_fail_open_when_the_state_db_is_broken(monkeypatch):
    def boom(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(system_state, "_connect", boom)
    system_state.invalidate_cache()

    assert await system_state.is_enabled_async() is False
    assert await system_state.is_enabled_fresh_async() is False


@pytest.mark.asyncio
async def test_async_reads_do_not_block_the_event_loop(monkeypatch):
    def slow_read():
        time.sleep(0.2)
        return True

    monkeypatch.setattr(system_state, "is_enabled", slow_read)

    ticks = 0

    async def heartbeat():
        nonlocal ticks
        for _ in range(5):
            await asyncio.sleep(0.02)
            ticks += 1

    result, _ = await asyncio.gather(system_state.is_enabled_async(), heartbeat())
    assert result is True
    assert ticks == 5


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [False, True])
async def test_hot_cache_never_offloads_to_a_worker_thread(monkeypatch, enabled):
    system_state.set_state(enabled, None, None, ACTOR)
    assert system_state.is_enabled() is enabled
    assert system_state.is_enabled_fresh() is enabled

    calls = {"n": 0}

    async def counting_to_thread(func, /, *args, **kwargs):
        calls["n"] += 1
        return func(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", counting_to_thread)

    for _ in range(10):
        assert await system_state.is_enabled_async() is enabled
        assert await system_state.is_enabled_fresh_async() is enabled

    assert calls["n"] == 0


@pytest.mark.asyncio
async def test_cache_miss_and_expiry_still_offload(monkeypatch):
    system_state.invalidate_cache()

    calls = {"n": 0}

    async def counting_to_thread(func, /, *args, **kwargs):
        calls["n"] += 1
        return func(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", counting_to_thread)

    assert await system_state.is_enabled_async() is False
    assert calls["n"] == 1
    assert await system_state.is_enabled_async() is False
    assert calls["n"] == 1

    assert await system_state.is_enabled_fresh_async() is False
    assert calls["n"] == 2
    assert await system_state.is_enabled_fresh_async() is False
    assert calls["n"] == 2

    monkeypatch.setattr(system_state, "CACHE_TTL", -1.0)
    monkeypatch.setattr(system_state, "FRESH_CACHE_TTL", -1.0)

    assert await system_state.is_enabled_async() is False
    assert await system_state.is_enabled_fresh_async() is False
    assert calls["n"] == 4


PROBE_URL = "/api/__probe_not_exempt__"
MOZ = {"User-Agent": "Mozilla/5.0 (offload harness)"}


def test_middleware_offloads_only_on_cache_miss(monkeypatch):
    from fastapi.testclient import TestClient

    import main

    calls = {"n": 0}

    async def counting_to_thread(func, /, *args, **kwargs):
        calls["n"] += 1
        return func(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", counting_to_thread)
    system_state.invalidate_cache()

    client = TestClient(main.app)
    try:
        assert client.get(PROBE_URL, headers=MOZ).status_code == 404
        cold = calls["n"]
        assert cold > 0

        assert client.get(PROBE_URL, headers=MOZ).status_code == 404
        assert calls["n"] == cold
    finally:
        client.close()
