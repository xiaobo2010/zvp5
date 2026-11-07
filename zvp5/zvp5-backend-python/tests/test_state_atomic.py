"""``db.state`` 的原子性与 ``db.cache`` 的故障降级回归测试。

覆盖两类历史缺陷：

* 窗口判定拆成 ``ZREMRANGEBYSCORE → ZCARD → ZADD`` 多次往返，并发下限额超发；
  ``kv_incr`` 的 INCR 与 PEXPIRE 之间断连会留下无 TTL 的计数器。
* ``cache._get_client`` 拿到锁后不复查降级窗口，Redis 宕机瞬间排队请求逐个试连。

真 Redis（含 fakeredis）不在依赖里，这里用 ``FakeRedis`` 记录 ``EVAL`` 调用次数，
断言「一次判定 = 一次往返」以及脚本内容/语义；原子性本身由 Redis 的 EVAL 保证。
"""

from __future__ import annotations

import asyncio
import time

import pytest

from db import cache, state
from fake_redis import FakeRedis


@pytest.fixture()
def fake() -> FakeRedis:
    client = FakeRedis()
    cache._install_client_for_tests(client)
    yield client
    cache._reset_for_tests()


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# window_hit：单次 EVAL
# ---------------------------------------------------------------------------

def test_window_hit_is_one_round_trip(fake: FakeRedis) -> None:
    run(state.window_hit("u:ocr", 2, 60))
    assert fake.calls["eval"] == 1
    # 旧的五步实现留下这些调用，任何一个 >0 都说明判定又被打散了。
    for legacy in ("zremrangebyscore", "zcard", "zadd", "pexpire"):
        assert fake.calls[legacy] == 0, legacy


def test_window_script_contains_the_whole_decision() -> None:
    script = state.WINDOW_HIT_SCRIPT
    for command in ("ZREMRANGEBYSCORE", "ZCARD", "ZRANGE", "ZADD", "PEXPIRE"):
        assert command in script


def test_window_allows_then_rejects_without_writing_on_reject(fake: FakeRedis) -> None:
    async def main():
        return [await state.window_hit("u:ocr", 2, 60) for _ in range(5)]

    results = run(main())
    assert results[:2] == [(True, 0), (True, 0)]
    for allowed, retry in results[2:]:
        assert allowed is False and retry >= 1
    # 拒绝不写入成员：窗口里始终只有放行的那 2 次。
    assert len(fake.zsets["zvms:state:win:u:ocr"]) == 2


def test_window_retry_after_counts_from_oldest_member(fake: FakeRedis) -> None:
    now = time.time()
    # 最旧成员已经 50s，窗口 60s ⇒ 剩余 10s；浮点误差允许 ±1。
    fake.zsets["zvms:state:win:u:ocr"] = {"a": now - 50, "b": now - 1}

    allowed, retry = run(state.window_hit("u:ocr", 2, 60))
    assert allowed is False
    assert 9 <= retry <= 11


def test_window_zero_limit_bypasses_redis(fake: FakeRedis) -> None:
    fake.fail = True  # 若仍去连 Redis 会抛异常并返回 None
    assert run(state.window_hit("u:ocr", 0, 60)) == (True, 0)
    assert fake.calls["eval"] == 0


def test_window_failure_returns_none_and_degrades(fake: FakeRedis) -> None:
    fake.fail = True
    assert run(state.window_hit("u:ocr", 1, 60)) is None
    assert cache._degraded_until > time.monotonic()


def test_window_prunes_expired_members_before_counting(fake: FakeRedis) -> None:
    fake.zsets["zvms:state:win:u:ocr"] = {"old": time.time() - 61}
    assert run(state.window_hit("u:ocr", 1, 60)) == (True, 0)
    assert "old" not in fake.zsets["zvms:state:win:u:ocr"]


# ---------------------------------------------------------------------------
# kv_incr：INCR 与 PEXPIRE 同一次 EVAL
# ---------------------------------------------------------------------------

def test_kv_incr_is_one_round_trip(fake: FakeRedis) -> None:
    async def main():
        return [await state.kv_incr("cnt", 60) for _ in range(2)]

    assert run(main()) == [1, 2]
    assert fake.calls["eval"] == 2
    assert fake.calls["incr"] == 0
    assert fake.calls["pexpire"] == 0
    # TTL 跟着自增一起续期，不会出现「计数器活着但永不过期」。
    assert fake.ttl["zvms:state:kv:cnt"] == 60000


def test_kv_incr_script_sets_ttl_with_incr() -> None:
    assert "INCR" in state.KV_INCR_SCRIPT
    assert "PEXPIRE" in state.KV_INCR_SCRIPT


def test_kv_incr_failure_returns_none_and_degrades(fake: FakeRedis) -> None:
    fake.fail = True
    assert run(state.kv_incr("cnt", 60)) is None
    assert cache._degraded_until > time.monotonic()


# ---------------------------------------------------------------------------
# cache._get_client：锁内复查 + 关闭失败连接
# ---------------------------------------------------------------------------

class _FakeConn:
    def __init__(self, ok: bool) -> None:
        self.ok = ok
        self.closed = False

    async def ping(self) -> bool:
        # 强制让出事件循环：模拟多个请求真的排在锁上（否则 gather 不会交错）。
        await asyncio.sleep(0)
        if not self.ok:
            raise ConnectionError("redis down")
        return True

    async def aclose(self) -> None:
        self.closed = True


class _FakeFactory:
    def __init__(self, ok: bool) -> None:
        self.ok = ok
        self.created: list[_FakeConn] = []

    def from_url(self, *_args, **_kwargs) -> _FakeConn:
        conn = _FakeConn(self.ok)
        self.created.append(conn)
        return conn


def _isolate_client(monkeypatch: pytest.MonkeyPatch, factory: _FakeFactory) -> None:
    monkeypatch.setattr(cache, "_aioredis", factory)
    monkeypatch.setattr(cache, "_uri", lambda: "redis://127.0.0.1:6379/0")
    monkeypatch.setattr(cache, "_client", None)
    monkeypatch.setattr(cache, "_client_uri", None)
    monkeypatch.setattr(cache, "_client_lock", None)


def test_connect_failure_closes_client(monkeypatch) -> None:
    factory = _FakeFactory(ok=False)
    _isolate_client(monkeypatch, factory)

    assert run(cache._get_client()) is None
    assert len(factory.created) == 1
    # 失败分支的 client 不在缓存里，没人会替它 aclose。
    assert factory.created[0].closed is True
    assert cache._degraded_until > time.monotonic()


def test_queued_callers_do_not_retry_connect_after_degrade(monkeypatch) -> None:
    factory = _FakeFactory(ok=False)
    _isolate_client(monkeypatch, factory)

    async def main():
        return await asyncio.gather(cache._get_client(), cache._get_client())

    assert run(main()) == [None, None]
    # 第二个请求进锁后应看到已降级，只允许一次连接尝试。
    assert len(factory.created) == 1


def test_degraded_window_expiry_allows_reconnect(monkeypatch) -> None:
    factory = _FakeFactory(ok=True)
    _isolate_client(monkeypatch, factory)
    monkeypatch.setattr(cache, "_degraded_until", time.monotonic() - 1)

    async def main():
        first = await cache._get_client()
        second = await cache._get_client()
        return first, second

    first, second = run(main())
    assert first is not None and first is second
    assert len(factory.created) == 1
