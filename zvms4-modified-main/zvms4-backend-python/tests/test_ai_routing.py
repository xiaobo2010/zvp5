"""路由、熔断与全局并发闸门（纯内存状态，无网络无 DB）。

三个契约：

* ``candidates`` 只按目录顺序过滤冷却项，**不重排**——目录顺序就是「个人 > 共享 >
  服务端」的优先级，重排等于让平台 key 抢走管理员的 personal key。
* 熔断键是 ``entry["key"]``（``scope:oid``），不是 provider 名：同名项在两层同时
  存在时，用名字做键会让一层的失败把另一层也熔掉。
* ``slot()`` 满员时快速失败成 ``AI_BUSY``/429，而不是把请求堆在事件循环里等。

时钟用 ``routing._now`` 的替身推进，不用 ``sleep``。
"""

from __future__ import annotations

import pytest

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


# ---------------------------------------------------------------------------
# candidates
# ---------------------------------------------------------------------------


def test_candidates_keep_catalog_order():
    assert routing.candidates([ALPHA, BETA, GAMMA]) == [ALPHA, BETA, GAMMA]


def test_forced_accepts_name_and_oid():
    entries = [ALPHA, BETA, GAMMA]

    assert routing.candidates(entries, forced="beta") == [BETA]
    assert routing.candidates(entries, forced="b" * 24) == [BETA]


def test_forced_outside_the_catalog_is_rejected():
    """forced 必须真的在可见目录里：否则等于绕过可见性打任意上游。"""
    with pytest.raises(routing.RouteError) as failure:
        routing.candidates([ALPHA], forced="gamma")

    assert (failure.value.code, failure.value.status_code) == ("AI_NO_PROVIDER", 503)


def test_empty_catalog_is_no_provider():
    with pytest.raises(routing.RouteError) as failure:
        routing.candidates([])

    assert failure.value.code == "AI_NO_PROVIDER"
    assert failure.value.status_code == 503


# ---------------------------------------------------------------------------
# 熔断
# ---------------------------------------------------------------------------


def test_three_failures_open_a_sixty_second_cooldown(clock):
    for _ in range(routing.FAILURE_THRESHOLD):
        routing.mark_failure(ALPHA["key"])

    assert routing.cooldown_state() == {ALPHA["key"]: 60.0}
    # 冷却期被 candidates 跳过，但没失败的 provider 仍然可用。
    assert routing.candidates([ALPHA, BETA]) == [BETA]


def test_all_candidates_in_cooldown_is_no_provider(clock):
    for _ in range(routing.FAILURE_THRESHOLD):
        routing.mark_failure(ALPHA["key"])

    with pytest.raises(routing.RouteError) as failure:
        routing.candidates([ALPHA])

    assert (failure.value.code, failure.value.status_code) == ("AI_NO_PROVIDER", 503)


def test_two_failures_are_not_enough(clock):
    routing.mark_failure(ALPHA["key"])
    routing.mark_failure(ALPHA["key"])

    assert routing.cooldown_state() == {}
    assert routing.candidates([ALPHA]) == [ALPHA]


def test_mark_success_clears_failures_and_cooldown(clock):
    routing.mark_failure(ALPHA["key"])
    routing.mark_failure(ALPHA["key"])
    routing.mark_success(ALPHA["key"])

    # 成功清零计数：再来两次失败不该熔断（否则「三次」变成了「三次里不成功」）。
    routing.mark_failure(ALPHA["key"])
    routing.mark_failure(ALPHA["key"])
    assert routing.cooldown_state() == {}

    for _ in range(routing.FAILURE_THRESHOLD):
        routing.mark_failure(BETA["key"])
    assert routing.cooldown_state() == {BETA["key"]: 60.0}

    routing.mark_success(BETA["key"])
    assert routing.cooldown_state() == {}
    assert routing.candidates([BETA]) == [BETA]


def test_cooldown_expires_by_itself(clock):
    for _ in range(routing.FAILURE_THRESHOLD):
        routing.mark_failure(ALPHA["key"])

    clock["now"] += routing.COOLDOWN_SECONDS - 1
    assert routing.candidates([ALPHA, BETA]) == [BETA]

    clock["now"] += 2
    assert routing.cooldown_state() == {}
    assert routing.candidates([ALPHA]) == [ALPHA]


def test_reset_state_clears_everything(clock):
    for _ in range(routing.FAILURE_THRESHOLD):
        routing.mark_failure(ALPHA["key"])
    assert routing.cooldown_state() != {}

    routing.reset_state()

    assert routing.cooldown_state() == {}
    assert routing.candidates([ALPHA]) == [ALPHA]


def test_cooldown_key_is_scope_qualified_not_the_name(clock):
    """同名两层（personal alpha / shared alpha）互不牵连。"""
    personal = _entry("alpha", "d" * 24, scope="personal")
    shared = _entry("alpha", "e" * 24, scope="shared")

    for _ in range(routing.FAILURE_THRESHOLD):
        routing.mark_failure(personal["key"])

    assert routing.candidates([personal, shared]) == [shared]


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
