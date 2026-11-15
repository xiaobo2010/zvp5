"""路由、熔断与全局并发闸门（纯内存状态 + Redis 共享状态两条路径）。

三个契约：

* ``candidates`` 只按目录顺序过滤冷却项，**不重排**——目录顺序就是「个人 > 共享 >
  服务端」的优先级，重排等于让平台 key 抢走管理员的 personal key。
* 熔断键是 ``entry["key"]``（``scope:oid``），不是 provider 名：同名项在两层同时
  存在时，用名字做键会让一层的失败把另一层也熔掉。
* ``slot()`` 满员时快速失败成 ``AI_BUSY``/429，而不是把请求堆在事件循环里等。

熔断状态有两条路径：配了 Redis 走共享 KV（多 worker 一份真相），未配置或原语
失败时回退进程内 dict。默认没有 Redis，所以用例默认覆盖回退路径。

时钟用 ``routing._now`` 的替身推进，不用 ``sleep``（只影响进程内路径）。
"""

from __future__ import annotations

import asyncio

import pytest

from db import cache
from fake_redis import FakeRedis
from services.ai import config as ai_config
from services.ai import routing


def _entry(name: str, oid: str, scope: str = "shared", models=("m",)) -> dict:
    return {
        "key": f"{scope}:{oid}",
        "scope": scope,
        "oid": oid,
        "name": name,
        "models": list(models),
    }


ALPHA = _entry("alpha", "a" * 24)
BETA = _entry("beta", "b" * 24, scope="personal")
GAMMA = _entry("gamma", "c" * 24, scope="server")


@pytest.fixture(autouse=True)
def _fresh_state():
    """每个用例从空的熔断表与未缓存的信号量开始。

    信号量会绑定创建它的 event loop；跨用例复用会炸在「bound to a different
    event loop」上，所以这里直接把它丢掉（它只是缓存，重建无副作用）。
    """
    routing.reset_state()
    routing._semaphore = None
    routing._semaphore_size = 0
    yield
    routing.reset_state()
    routing._semaphore = None
    routing._semaphore_size = 0


@pytest.fixture
def clock(monkeypatch):
    state = {"now": 1000.0}
    monkeypatch.setattr(routing, "_now", lambda: state["now"])
    return state


@pytest.fixture
def fake_redis():
    """注入 Redis 替身：熔断状态改走 ``db.state`` 的共享 KV。"""
    client = FakeRedis()
    cache._install_client_for_tests(client)
    yield client
    cache._reset_for_tests()


# ---------------------------------------------------------------------------
# candidates
# ---------------------------------------------------------------------------


def test_candidates_keep_catalog_order():
    assert asyncio.run(routing.candidates([ALPHA, BETA, GAMMA])) == [
        ALPHA,
        BETA,
        GAMMA,
    ]


def test_forced_accepts_name_and_oid():
    entries = [ALPHA, BETA, GAMMA]

    assert asyncio.run(routing.candidates(entries, forced="beta")) == [BETA]
    assert asyncio.run(routing.candidates(entries, forced="b" * 24)) == [BETA]


def test_forced_outside_the_catalog_is_rejected():
    """forced 必须真的在可见目录里：否则等于绕过可见性打任意上游。"""
    with pytest.raises(routing.RouteError) as failure:
        asyncio.run(routing.candidates([ALPHA], forced="gamma"))

    assert (failure.value.code, failure.value.status_code) == ("AI_NO_PROVIDER", 503)


def test_empty_catalog_is_no_provider():
    with pytest.raises(routing.RouteError) as failure:
        asyncio.run(routing.candidates([]))

    assert failure.value.code == "AI_NO_PROVIDER"
    assert failure.value.status_code == 503


# ---------------------------------------------------------------------------
# 熔断（默认走进程内实现：没有 Redis）
# ---------------------------------------------------------------------------


def test_three_failures_open_a_sixty_second_cooldown(clock):
    async def main():
        for _ in range(routing.FAILURE_THRESHOLD):
            await routing.mark_failure(ALPHA["key"])
        return await routing.cooldown_state(), await routing.candidates([ALPHA, BETA])

    state, picked = asyncio.run(main())
    assert state == {ALPHA["key"]: 60.0}
    # 冷却期被 candidates 跳过，但没失败的 provider 仍然可用。
    assert picked == [BETA]


def test_all_candidates_in_cooldown_is_no_provider(clock):
    async def main():
        for _ in range(routing.FAILURE_THRESHOLD):
            await routing.mark_failure(ALPHA["key"])
        await routing.candidates([ALPHA])

    with pytest.raises(routing.RouteError) as failure:
        asyncio.run(main())

    assert (failure.value.code, failure.value.status_code) == ("AI_NO_PROVIDER", 503)


def test_two_failures_are_not_enough(clock):
    async def main():
        await routing.mark_failure(ALPHA["key"])
        await routing.mark_failure(ALPHA["key"])
        return await routing.cooldown_state(), await routing.candidates([ALPHA])

    state, picked = asyncio.run(main())
    assert state == {}
    assert picked == [ALPHA]


def test_mark_success_clears_failures_and_cooldown(clock):
    async def main():
        await routing.mark_failure(ALPHA["key"])
        await routing.mark_failure(ALPHA["key"])
        await routing.mark_success(ALPHA["key"])

        # 成功清零计数：再来两次失败不该熔断（否则「三次」变成了「三次里不成功」）。
        await routing.mark_failure(ALPHA["key"])
        await routing.mark_failure(ALPHA["key"])
        after_success = await routing.cooldown_state()

        for _ in range(routing.FAILURE_THRESHOLD):
            await routing.mark_failure(BETA["key"])
        opened = await routing.cooldown_state()

        await routing.mark_success(BETA["key"])
        return after_success, opened, await routing.cooldown_state(), await routing.candidates([BETA])

    after_success, opened, cleared, picked = asyncio.run(main())
    assert after_success == {}
    assert opened == {BETA["key"]: 60.0}
    assert cleared == {}
    assert picked == [BETA]


def test_cooldown_expires_by_itself(clock):
    async def main():
        for _ in range(routing.FAILURE_THRESHOLD):
            await routing.mark_failure(ALPHA["key"])

        clock["now"] += routing.COOLDOWN_SECONDS - 1
        still_cooling = await routing.candidates([ALPHA, BETA])

        clock["now"] += 2
        return still_cooling, await routing.cooldown_state(), await routing.candidates([ALPHA])

    still_cooling, state, picked = asyncio.run(main())
    assert still_cooling == [BETA]
    assert state == {}
    assert picked == [ALPHA]


def test_reset_state_clears_everything(clock):
    async def main():
        for _ in range(routing.FAILURE_THRESHOLD):
            await routing.mark_failure(ALPHA["key"])
        opened = await routing.cooldown_state()

        routing.reset_state()

        return opened, await routing.cooldown_state(), await routing.candidates([ALPHA])

    opened, state, picked = asyncio.run(main())
    assert opened != {}
    assert state == {}
    assert picked == [ALPHA]


def test_cooldown_key_is_scope_qualified_not_the_name(clock):
    """同名两层（personal alpha / shared alpha）互不牵连。"""
    personal = _entry("alpha", "d" * 24, scope="personal")
    shared = _entry("alpha", "e" * 24, scope="shared")

    async def main():
        for _ in range(routing.FAILURE_THRESHOLD):
            await routing.mark_failure(personal["key"])
        return await routing.candidates([personal, shared])

    assert asyncio.run(main()) == [shared]


# ---------------------------------------------------------------------------
# 熔断（配了 Redis：状态在共享 KV 里）
# ---------------------------------------------------------------------------


def test_cooldown_is_shared_through_redis(fake_redis):
    """3 次失败 → 该项被排除 → 成功 → 立即恢复。"""

    async def main():
        for _ in range(routing.FAILURE_THRESHOLD):
            await routing.mark_failure(ALPHA["key"])
        opened = await routing.cooldown_state()
        picked = await routing.candidates([ALPHA, BETA])
        after_open = dict(fake_redis.kv)

        await routing.mark_success(ALPHA["key"])
        return (
            opened,
            picked,
            after_open,
            await routing.cooldown_state(),
            await routing.candidates([ALPHA]),
        )

    opened, picked, after_open, cleared, recovered = asyncio.run(main())

    fail_key = f"zvms:state:kv:route:fail:{ALPHA['key']}"
    cool_key = f"zvms:state:kv:route:cool:{ALPHA['key']}"
    assert cool_key in after_open
    # 熔断后计数清零：冷却结束再给满 3 次机会。
    assert fail_key not in after_open
    assert opened == {ALPHA["key"]: 60.0}
    assert picked == [BETA]
    assert cleared == {}
    assert recovered == [ALPHA]
    assert cool_key not in fake_redis.kv


def test_cooldown_falls_back_when_redis_fails(clock, fake_redis):
    """Redis 报错时回退到进程内 dict，语义不变。"""
    fake_redis.fail = True

    async def main():
        for _ in range(routing.FAILURE_THRESHOLD):
            await routing.mark_failure(ALPHA["key"])
        return await routing.cooldown_state(), await routing.candidates([ALPHA, BETA])

    state, picked = asyncio.run(main())
    assert ALPHA["key"] in routing._cooldown_until
    assert state == {ALPHA["key"]: 60.0}
    assert picked == [BETA]


# ---------------------------------------------------------------------------
# 并发闸门
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_slot_fails_fast_when_saturated(monkeypatch):
    monkeypatch.setattr(ai_config, "max_concurrency", lambda: 1)
    # 别让用例真的等 2 秒：闸门的超时是模块级常量，替换即可。
    monkeypatch.setattr(routing, "SLOT_TIMEOUT_SECONDS", 0.01)

    async with routing.slot():
        with pytest.raises(routing.RouteError) as failure:
            async with routing.slot():
                pytest.fail("saturated gateway must not hand out a second slot")

    assert (failure.value.code, failure.value.status_code) == ("AI_BUSY", 429)


@pytest.mark.asyncio
async def test_slot_is_released_after_use(monkeypatch):
    monkeypatch.setattr(ai_config, "max_concurrency", lambda: 1)
    monkeypatch.setattr(routing, "SLOT_TIMEOUT_SECONDS", 0.01)

    async with routing.slot():
        pass

    # 释放干净：下一次能立刻拿到，不会累积成 429。
    async with routing.slot():
        pass


@pytest.mark.asyncio
async def test_slot_is_released_even_when_the_body_raises(monkeypatch):
    monkeypatch.setattr(ai_config, "max_concurrency", lambda: 1)
    monkeypatch.setattr(routing, "SLOT_TIMEOUT_SECONDS", 0.01)

    with pytest.raises(RuntimeError):
        async with routing.slot():
            raise RuntimeError("upstream blew up")

    async with routing.slot():
        pass
