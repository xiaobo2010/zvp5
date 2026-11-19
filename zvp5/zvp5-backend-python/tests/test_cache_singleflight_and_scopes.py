"""并发未命中的 singleflight、epoch 原子命令形状、控制台读缓存失效。

三条契约：

* 同 key 的 N 个并发未命中只跑一次 ``loader``，结果/异常对所有等待者一致；
  失败后登记表必须干净，下一个请求能重新回源；
* 不同 key 之间不互相串行；
* ``bump_epoch`` 把 ``INCR`` + ``EXPIRE`` 放进同一个 ``MULTI/EXEC``（不允许
  先 INCR 再断连，留下没有 TTL 的永久 epoch key），Redis 不可用时照旧静默降级；
* DB 控制台改行后 ``conversations`` 也必须失效（``conv`` / ``convlatest``）。
"""

from __future__ import annotations

import asyncio

import pytest

from db import cache
from routers import system_db_content_router as content_router

EPOCH_KEY = "zvms:epoch:users"


class FakeClient:
    """够用的 async Redis 替身；``pipeline`` 记录 MULTI/EXEC 的命令形状。"""

    def __init__(self, *, fail: bool = False, with_pipeline: bool = True) -> None:
        self.data: dict[str, str] = {}
        self.ttls: dict[str, int | None] = {}
        self.fail = fail
        self.calls = {"get": 0, "set": 0, "incr": 0, "expire": 0}
        self.pipelines: list[FakePipeline] = []
        self.transactions = 0
        if with_pipeline:
            self.pipeline = self._pipeline

    def _check(self) -> None:
        if self.fail:
            raise ConnectionError("redis down")

    def _pipeline(self, transaction: bool = True) -> FakePipeline:
        self._check()
        if transaction:
            self.transactions += 1
        pipe = FakePipeline(self)
        self.pipelines.append(pipe)
        return pipe

    async def get(self, key: str):
        self._check()
        self.calls["get"] += 1
        return self.data.get(key)

    async def set(self, key: str, value: str, px: int | None = None):
        self._check()
        self.calls["set"] += 1
        self.data[key] = value
        self.ttls[key] = px
        return True

    async def incr(self, key: str) -> int:
        self._check()
        self.calls["incr"] += 1
        self.data[key] = str(int(self.data.get(key, "0")) + 1)
        return int(self.data[key])

    async def expire(self, key: str, seconds: int) -> bool:
        self._check()
        self.calls["expire"] += 1
        self.ttls[key] = int(seconds)
        return True


class FakePipeline:
    def __init__(self, client: FakeClient) -> None:
        self.client = client
        self.queued: list[tuple] = []
        self.executed = False

    def incr(self, key: str):
        self.queued.append(("incr", key))
        return self

    def expire(self, key: str, seconds: int):
        self.queued.append(("expire", key, int(seconds)))
        return self

    async def execute(self):
        self.executed = True
        for command in self.queued:
            if command[0] == "incr":
                self.client.data[command[1]] = str(
                    int(self.client.data.get(command[1], "0")) + 1
                )
            else:
                self.client.ttls[command[1]] = command[2]
        return [1] * len(self.queued)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info) -> bool:
        return False


@pytest.fixture
def client() -> FakeClient:
    fake = FakeClient()
    cache._install_client_for_tests(fake)
    yield fake
    cache._reset_for_tests()


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# singleflight
# ---------------------------------------------------------------------------


def test_concurrent_misses_share_one_loader(client: FakeClient) -> None:
    calls = {"n": 0}
    n = 8

    async def main():
        gate = asyncio.Event()

        async def loader():
            calls["n"] += 1
            await gate.wait()
            return {"v": 1}

        tasks = [
            asyncio.create_task(cache.remember("k", 30, loader)) for _ in range(n)
        ]
        for _ in range(3):
            await asyncio.sleep(0)
        gate.set()
        return await asyncio.gather(*tasks)

    results = run(main())

    assert calls["n"] == 1
    assert results == [{"v": 1}] * n
    assert client.calls["set"] == 1
    assert client.ttls["k"] == 30000
    assert cache._inflight == {}


def test_loader_exception_reaches_every_waiter_and_cleans_up(
    client: FakeClient,
) -> None:
    calls = {"n": 0}
    n = 5

    async def main():
        gate = asyncio.Event()

        async def loader():
            calls["n"] += 1
            await gate.wait()
            raise RuntimeError("boom")

        tasks = [
            asyncio.create_task(cache.remember("k", 30, loader)) for _ in range(n)
        ]
        for _ in range(3):
            await asyncio.sleep(0)
        gate.set()
        return await asyncio.gather(*tasks, return_exceptions=True)

    results = run(main())

    assert calls["n"] == 1
    assert len(results) == n
    assert all(isinstance(item, RuntimeError) for item in results)
    assert str(results[0]) == "boom"
    assert "k" not in client.data
    assert cache._inflight == {}

    async def retry():
        return await cache.remember("k", 30, _const("ok"))

    assert run(retry()) == "ok"


def test_different_keys_are_not_serialized(client: FakeClient) -> None:
    started = {"n": 0}

    async def main():
        both_running = asyncio.Event()
        release = asyncio.Event()

        def loader(name: str):
            async def load():
                started["n"] += 1
                if started["n"] == 2:
                    both_running.set()
                await release.wait()
                return name

            return load

        tasks = [
            asyncio.create_task(cache.remember("a", 30, loader("a"))),
            asyncio.create_task(cache.remember("b", 30, loader("b"))),
        ]
        await asyncio.wait_for(both_running.wait(), timeout=2)
        release.set()
        return await asyncio.gather(*tasks)

    assert run(main()) == ["a", "b"]
    assert started["n"] == 2


def test_foreign_loop_entry_is_not_awaited() -> None:
    """登记项属于别的循环时，调用方直接回源，不去等一个挂不上的 Future。"""
    cache._reset_for_tests()
    other_loop = asyncio.new_event_loop()
    calls = {"n": 0}
    try:
        cache._inflight["k"] = (other_loop, other_loop.create_future())

        async def loader():
            calls["n"] += 1
            return {"v": 2}

        assert run(cache._load_once("k", loader)) == ({"v": 2}, True)
        assert calls["n"] == 1
        assert cache._inflight == {}
    finally:
        other_loop.close()


def _const(value):
    async def loader():
        return value

    return loader


# ---------------------------------------------------------------------------
# bump_epoch：INCR + EXPIRE 同事务
# ---------------------------------------------------------------------------


def test_bump_epoch_queues_incr_and_expire_in_one_transaction(
    client: FakeClient,
) -> None:
    run(cache.bump_epoch("users"))

    assert client.transactions == 1
    assert len(client.pipelines) == 1
    pipe = client.pipelines[0]
    assert pipe.executed is True
    assert pipe.queued == [
        ("incr", EPOCH_KEY),
        ("expire", EPOCH_KEY, cache.EPOCH_TTL_SECONDS),
    ]
    # 两条命令都走事务，客户端层不该再单独发一次。
    assert client.calls["incr"] == 0
    assert client.calls["expire"] == 0
    assert client.ttls[EPOCH_KEY] == 30 * 24 * 3600
    assert run(cache.epoch("users")) == 1


def test_bump_epoch_falls_back_without_pipeline() -> None:
    fake = FakeClient(with_pipeline=False)
    cache._install_client_for_tests(fake)
    try:
        run(cache.bump_epoch("users"))
        assert fake.calls["incr"] == 1
        assert fake.calls["expire"] == 1
        assert fake.ttls[EPOCH_KEY] == cache.EPOCH_TTL_SECONDS
        assert run(cache.epoch("users")) == 1
    finally:
        cache._reset_for_tests()


def test_bump_epoch_degrades_when_redis_fails() -> None:
    fake = FakeClient(fail=True)
    cache._install_client_for_tests(fake)
    try:
        run(cache.bump_epoch("users"))
        assert cache._degraded_until > 0
        assert run(cache.epoch("users")) == 0
    finally:
        cache._reset_for_tests()


# ---------------------------------------------------------------------------
# DB 控制台的读缓存失效
# ---------------------------------------------------------------------------


def test_content_console_invalidates_conversations(monkeypatch) -> None:
    bumped: list[str] = []

    async def fake_bump(scope: str) -> None:
        bumped.append(scope)

    monkeypatch.setattr(cache, "bump_epoch", fake_bump)

    run(content_router._invalidate_read_caches())

    assert "conversations" in bumped
    assert "notifications" in bumped
    assert "changelog" in bumped
    # 与 db.cache 的键空间对齐，且顺序固定（新增域不许悄悄漏掉）。
    assert set(bumped) == set(cache.SCOPE_PREFIXES)
    assert bumped == [
        "users",
        "groups",
        "activities",
        "conversations",
        "notifications",
        "changelog",
        "roles",
        "settings",
    ]
