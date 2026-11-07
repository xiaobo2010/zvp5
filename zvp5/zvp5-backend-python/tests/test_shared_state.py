"""``db.state``（Redis 共享运行态原语）的单元测试。"""

from __future__ import annotations

import asyncio
import time

import pytest

from db import cache, state
from fake_redis import FakeRedis
from services.ai import rate_limit

OID = "64b7f0c2e1a3b4c5d6e7fa01"


@pytest.fixture()
def fake() -> FakeRedis:
    client = FakeRedis()
    cache._install_client_for_tests(client)
    yield client
    cache._reset_for_tests()


def run(coro):
    return asyncio.run(coro)


def test_window_allows_until_limit_then_rejects(fake: FakeRedis) -> None:
    async def main():
        first = await state.window_hit("u:ocr", 2, 60)
        second = await state.window_hit("u:ocr", 2, 60)
        third = await state.window_hit("u:ocr", 2, 60)
        return first, second, third

    first, second, third = run(main())
    assert first == (True, 0) and second == (True, 0)
    allowed, retry = third
    assert allowed is False and retry >= 1
    # 拒绝不写成员：窗口长度不因重试变长。
    assert len(fake.zsets["zvms:state:win:u:ocr"]) == 2


def test_window_expires_old_members(fake: FakeRedis) -> None:
    fake.zsets["zvms:state:win:u:ocr"] = {"old": time.time() - 61}

    async def main():
        return await state.window_hit("u:ocr", 1, 60)

    assert run(main()) == (True, 0)


def test_window_returns_none_without_redis() -> None:
    cache._reset_for_tests()

    async def main():
        return await state.window_hit("u:ocr", 1, 60)

    assert run(main()) is None


def test_kv_roundtrip_and_ttl(fake: FakeRedis) -> None:
    async def main():
        await state.kv_set("conv:1", {"ok": True}, 30)
        got = await state.kv_get("conv:1")
        ttl = await state.kv_ttl("conv:1")
        await state.kv_delete("conv:1")
        gone = await state.kv_get("conv:1")
        return got, ttl, gone

    got, ttl, gone = run(main())
    assert got == {"ok": True}
    assert 29 <= ttl <= 30
    assert gone is None


def test_rate_limit_uses_shared_window(fake: FakeRedis) -> None:
    async def main():
        await rate_limit.check(OID, "chat", limit=1)
        try:
            await rate_limit.check(OID, "chat", limit=1)
        except rate_limit.RateLimitError as exc:
            return exc.retry_after
        return None

    assert run(main()) >= 1
    assert any(k.startswith("zvms:state:win:ai:") for k in fake.zsets)


def test_rate_limit_falls_back_when_redis_fails(monkeypatch, fake: FakeRedis) -> None:
    fake.fail = True
    monkeypatch.setattr("services.ai.config.rate_limit_per_minute", lambda kind: 1)

    async def main():
        await rate_limit.check(OID, "chat")
        try:
            await rate_limit.check(OID, "chat")
        except rate_limit.RateLimitError:
            return True
        return False

    assert run(main()) is True


def test_incr_and_set_nx(fake: FakeRedis) -> None:
    async def main():
        first = await state.kv_incr("cnt", 60)
        second = await state.kv_incr("cnt", 60)
        nx_first = await state.kv_set_nx("lock", 1, 60)
        nx_second = await state.kv_set_nx("lock", 1, 60)
        return first, second, nx_first, nx_second

    assert run(main()) == (1, 2, True, False)


def test_scan_lists_prefix(fake: FakeRedis) -> None:
    async def main():
        await state.kv_set("conv:a", 1, 30)
        await state.kv_set("conv:b", 2, 30)
        await state.kv_set("other", 3, 30)
        return sorted(await state.kv_scan("conv:"))

    assert run(main()) == ["conv:a", "conv:b"]
