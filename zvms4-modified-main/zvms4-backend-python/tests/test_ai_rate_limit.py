"""分钟级频控（``services/ai/rate_limit``）的契约。

三条不变量：

* 窗口内第 ``limit + 1`` 次被拒，且 ``retry_after >= 1``（客户端要能照着退避）；
* 窗口按 ``monotonic`` 滑：被拒的请求**不**计数，否则疯狂重试会把自己越锁越久；
* 不同用户 / 不同能力各自成桶，``limit == 0`` 表示不限。

时钟用 ``rate_limit.time`` 的替身推进，不 sleep 真实窗口。
"""

from __future__ import annotations

import pytest

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


def test_third_call_is_rejected_with_retry_after(monkeypatch):
    _limits(monkeypatch, 2)

    rate_limit.check(USER, "ocr")
    rate_limit.check(USER, "ocr")

    with pytest.raises(rate_limit.RateLimitError) as info:
        rate_limit.check(USER, "ocr")

    assert info.value.code == "AI_RATE_LIMITED"
    assert info.value.retry_after >= 1


def test_window_slides_and_lets_the_user_back_in(monkeypatch):
    clock = _limits(monkeypatch, 1, window=0.05)

    rate_limit.check(USER, "ocr")
    with pytest.raises(rate_limit.RateLimitError):
        rate_limit.check(USER, "ocr")

    clock.now += 0.06
    rate_limit.check(USER, "ocr")  # 窗口滑过去，重新放行


def test_rejected_call_does_not_extend_the_window(monkeypatch):
    """连续被拒不该把等待时间越推越长：拒绝不计数。"""
    clock = _limits(monkeypatch, 1, window=10.0)

    rate_limit.check(USER, "ocr")
    with pytest.raises(rate_limit.RateLimitError) as first:
        rate_limit.check(USER, "ocr")
    clock.now += 1.0
    with pytest.raises(rate_limit.RateLimitError) as second:
        rate_limit.check(USER, "ocr")

    assert second.value.retry_after <= first.value.retry_after


def test_users_and_kinds_are_independent(monkeypatch):
    _limits(monkeypatch, 1)

    rate_limit.check(USER, "ocr")
    rate_limit.check(OTHER, "ocr")
    rate_limit.check(USER, "chat")

    with pytest.raises(rate_limit.RateLimitError):
        rate_limit.check(USER, "ocr")
    with pytest.raises(rate_limit.RateLimitError):
        rate_limit.check(USER, "chat")


def test_zero_limit_means_unlimited(monkeypatch):
    _limits(monkeypatch, 0)

    for _ in range(100):
        rate_limit.check(USER, "chat")

    assert rate_limit._buckets == {}


def test_unknown_kind_is_not_limited(monkeypatch):
    """``rate_limit_per_minute`` 对未知 kind 返回 0，频控层不该替它兜底。"""
    monkeypatch.setattr(rate_limit, "time", FakeTime())

    for _ in range(50):
        rate_limit.check(USER, "embedding")

    assert rate_limit._buckets == {}


def test_reset_state_clears_every_bucket(monkeypatch):
    _limits(monkeypatch, 1)

    rate_limit.check(USER, "ocr")
    with pytest.raises(rate_limit.RateLimitError):
        rate_limit.check(USER, "ocr")

    rate_limit.reset_state()

    rate_limit.check(USER, "ocr")  # 清空后重新计数


def test_buckets_are_pruned_when_the_table_grows(monkeypatch):
    """key 数触顶时清掉过期桶，不让字典无限长大。"""
    clock = _limits(monkeypatch, 1, window=1.0)

    for index in range(rate_limit.MAX_KEYS):
        rate_limit.check(f"user-{index}", "ocr")
    assert len(rate_limit._buckets) == rate_limit.MAX_KEYS

    clock.now += 2.0  # 全部过期
    rate_limit.check("late-user", "ocr")

    assert len(rate_limit._buckets) < rate_limit.MAX_KEYS
    assert ("late-user", "ocr") in rate_limit._buckets
