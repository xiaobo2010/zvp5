"""分钟级频控（``services/ai/rate_limit``）的契约。

三条不变量：

* 窗口内第 ``limit + 1`` 次被拒，且 ``retry_after >= 1``（客户端要能照着退避）；
* 窗口按 ``monotonic`` 滑：被拒的请求**不**计数，否则疯狂重试会把自己越锁越久；
* 不同用户 / 不同能力各自成桶，``limit == 0`` 表示不限。

时钟用 ``rate_limit.time`` 的替身推进，不 sleep 真实窗口。
"""

from __future__ import annotations

import pytest

import settings
from services.ai import config as ai_config
from services.ai import rate_limit

USER = "64b7f0c2e1a3b4c5d6e7fa01"
OTHER = "64b7f0c2e1a3b4c5d6e7fa02"


class FakeTime:
    """``time`` 模块的最小替身：只提供 ``monotonic``。"""

    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def monotonic(self) -> float:
        return self.now


@pytest.fixture(autouse=True)
def _fresh_state():
    rate_limit.reset_state()
    yield
    rate_limit.reset_state()


def _limits(monkeypatch, per_minute: int, window: float = 60.0) -> FakeTime:
    clock = FakeTime()
    monkeypatch.setattr(
        ai_config, "rate_limit_per_minute", lambda kind: per_minute
    )
    monkeypatch.setattr(ai_config, "rate_limit_window_seconds", lambda: window)
    monkeypatch.setattr(rate_limit, "time", clock)
    return clock


@pytest.mark.asyncio
async def test_third_call_is_rejected_with_retry_after(monkeypatch):
    _limits(monkeypatch, 2)

    await rate_limit.check(USER, "ocr")
    await rate_limit.check(USER, "ocr")

    with pytest.raises(rate_limit.RateLimitError) as info:
        await rate_limit.check(USER, "ocr")

    assert info.value.code == "AI_RATE_LIMITED"
    assert info.value.retry_after >= 1


@pytest.mark.asyncio
async def test_window_slides_and_lets_the_user_back_in(monkeypatch):
    clock = _limits(monkeypatch, 1, window=0.05)

    await rate_limit.check(USER, "ocr")
    with pytest.raises(rate_limit.RateLimitError):
        await rate_limit.check(USER, "ocr")

    clock.now += 0.06
    await rate_limit.check(USER, "ocr")  # 窗口滑过去，重新放行


@pytest.mark.asyncio
async def test_rejected_call_does_not_extend_the_window(monkeypatch):
    """连续被拒不该把等待时间越推越长：拒绝不计数。"""
    clock = _limits(monkeypatch, 1, window=10.0)

    await rate_limit.check(USER, "ocr")
    with pytest.raises(rate_limit.RateLimitError) as first:
        await rate_limit.check(USER, "ocr")
    clock.now += 1.0
    with pytest.raises(rate_limit.RateLimitError) as second:
        await rate_limit.check(USER, "ocr")

    assert second.value.retry_after <= first.value.retry_after


@pytest.mark.asyncio
async def test_users_and_kinds_are_independent(monkeypatch):
    _limits(monkeypatch, 1)

    await rate_limit.check(USER, "ocr")
    await rate_limit.check(OTHER, "ocr")
    await rate_limit.check(USER, "chat")

    with pytest.raises(rate_limit.RateLimitError):
        await rate_limit.check(USER, "ocr")
    with pytest.raises(rate_limit.RateLimitError):
        await rate_limit.check(USER, "chat")


@pytest.mark.asyncio
async def test_zero_limit_means_unlimited(monkeypatch):
    _limits(monkeypatch, 0)

    for _ in range(100):
        await rate_limit.check(USER, "chat")

    assert rate_limit._buckets == {}


@pytest.mark.asyncio
async def test_unknown_kind_is_not_limited(monkeypatch):
    """``rate_limit_per_minute`` 对未知 kind 返回 0，频控层不该替它兜底。"""
    monkeypatch.setattr(rate_limit, "time", FakeTime())

    for _ in range(50):
        await rate_limit.check(USER, "embedding")

    assert rate_limit._buckets == {}


@pytest.mark.asyncio
async def test_reset_state_clears_every_bucket(monkeypatch):
    _limits(monkeypatch, 1)

    await rate_limit.check(USER, "ocr")
    with pytest.raises(rate_limit.RateLimitError):
        await rate_limit.check(USER, "ocr")

    rate_limit.reset_state()

    await rate_limit.check(USER, "ocr")  # 清空后重新计数


@pytest.mark.asyncio
async def test_buckets_are_pruned_when_the_table_grows(monkeypatch):
    """key 数触顶时清掉过期桶，不让字典无限长大。"""
    clock = _limits(monkeypatch, 1, window=1.0)

    for index in range(rate_limit.MAX_KEYS):
        await rate_limit.check(f"user-{index}", "ocr")
    assert len(rate_limit._buckets) == rate_limit.MAX_KEYS

    clock.now += 2.0  # 全部过期
    await rate_limit.check("late-user", "ocr")

    assert len(rate_limit._buckets) < rate_limit.MAX_KEYS
    assert ("late-user", "ocr") in rate_limit._buckets


# ---------------------------------------------------------------------------
# 多 worker 折算
# ---------------------------------------------------------------------------
#
# 频控状态在进程内存里，所以 ``--workers N`` 会让全局限额变成 N 倍。进程内阈值
# 折算成 ``ceil(limit / N)`` 是对这个事实的补偿；单 worker 时必须**逐字节不变**
# （上面的用例就是这条基线）。配置来源与非法值回退也在这里钉住。


def _workers(monkeypatch, count: int) -> None:
    monkeypatch.setattr(ai_config, "worker_count", lambda: count)


def test_worker_count_defaults_to_one(monkeypatch):
    monkeypatch.delenv("WEB_CONCURRENCY", raising=False)
    monkeypatch.delenv("UVICORN_WORKERS", raising=False)
    monkeypatch.setattr(settings, "AI_WORKER_COUNT", None, raising=False)
    monkeypatch.setattr(ai_config.config, "AI_WORKER_COUNT", None)

    assert ai_config.worker_count() == 1


def test_worker_count_reads_settings_before_env(monkeypatch):
    monkeypatch.setattr(settings, "AI_WORKER_COUNT", 3, raising=False)
    monkeypatch.setenv("WEB_CONCURRENCY", "4")

    assert ai_config.worker_count() == 3


def test_worker_count_reads_the_env_knobs_in_order(monkeypatch):
    monkeypatch.setattr(settings, "AI_WORKER_COUNT", None, raising=False)
    monkeypatch.setattr(ai_config.config, "AI_WORKER_COUNT", None)
    monkeypatch.setenv("WEB_CONCURRENCY", "4")
    monkeypatch.setenv("UVICORN_WORKERS", "5")

    assert ai_config.worker_count() == 4

    monkeypatch.delenv("WEB_CONCURRENCY")
    assert ai_config.worker_count() == 5


@pytest.mark.parametrize("value", ["abc", "0", "-2", "", "1.5", "true"])
def test_worker_count_falls_back_to_one_on_garbage(monkeypatch, value):
    monkeypatch.setattr(settings, "AI_WORKER_COUNT", None, raising=False)
    monkeypatch.setattr(ai_config.config, "AI_WORKER_COUNT", None)
    monkeypatch.setenv("WEB_CONCURRENCY", value)
    monkeypatch.delenv("UVICORN_WORKERS", raising=False)

    assert ai_config.worker_count() == 1


@pytest.mark.asyncio
async def test_effective_limit_is_unchanged_for_one_worker(monkeypatch):
    _limits(monkeypatch, 10)
    _workers(monkeypatch, 1)
    monkeypatch.setattr(rate_limit, "time", FakeTime())

    assert rate_limit.effective_limit("ocr") == 10

    for _ in range(10):
        await rate_limit.check(USER, "ocr")
    with pytest.raises(rate_limit.RateLimitError):
        await rate_limit.check(USER, "ocr")


def test_effective_limit_rounds_up_per_worker(monkeypatch):
    _limits(monkeypatch, 10)
    _workers(monkeypatch, 3)

    # ceil(10 / 3) = 4：取整不是 floor（floor 会让全局变成 9，比配置更紧）。
    assert rate_limit.effective_limit("ocr") == 4


def test_effective_limit_never_drops_below_one(monkeypatch):
    _limits(monkeypatch, 2)
    _workers(monkeypatch, 8)

    assert rate_limit.effective_limit("ocr") == 1


def test_zero_limit_stays_unlimited_regardless_of_workers(monkeypatch):
    _limits(monkeypatch, 0)
    _workers(monkeypatch, 4)

    assert rate_limit.effective_limit("chat") == 0


@pytest.mark.asyncio
async def test_per_worker_threshold_is_what_check_enforces(monkeypatch):
    clock = _limits(monkeypatch, 6)
    _workers(monkeypatch, 3)
    monkeypatch.setattr(rate_limit, "time", clock)

    # ceil(6 / 3) = 2 次/进程。
    await rate_limit.check(USER, "chat")
    await rate_limit.check(USER, "chat")
    with pytest.raises(rate_limit.RateLimitError):
        await rate_limit.check(USER, "chat")


@pytest.mark.asyncio
async def test_explicit_limit_is_scaled_too(monkeypatch):
    """独立 API key 的绝对阈值（60/min）同样按 worker 数折算。"""
    _limits(monkeypatch, 0)
    _workers(monkeypatch, 4)
    monkeypatch.setattr(rate_limit, "time", FakeTime())

    assert rate_limit.effective_limit("chat") == 0
    for _ in range(15):  # ceil(60 / 4) = 15
        await rate_limit.check("user:key", "chat", limit=60)
    with pytest.raises(rate_limit.RateLimitError):
        await rate_limit.check("user:key", "chat", limit=60)
