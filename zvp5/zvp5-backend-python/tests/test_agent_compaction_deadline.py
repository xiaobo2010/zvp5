"""A-H3：压缩的上游调用也要落在 run 的截止时间里。

一次 run 有 wall-clock 上限（``AI_AGENT_RUN_TIMEOUT_SECONDS``），主循环的上游调用
早就有 ``asyncio.wait_for`` 兜着，**压缩调用没有**：run 已经跑了 299 秒、轮收尾发现
越了水位，压缩还能再花一整次跨洋往返，于是「超时」这件事对用户表现为「还要再等」。

这里钉住四条：

* ``deadline`` 已经过期 → 压缩一次上游都不调，直接走既有的降级（硬截断）；
* 压缩调用超时 → 先走既有的重试（换候选），仍失败才是降级；降级结果照旧是
  ``degraded=True`` + 硬截断后的对话；
* 手动压缩（``compact_messages``）没有 run 上下文 → 不传 ``deadline``，行为不变；
* ``run`` → ``_maybe_compact`` → ``compact_history`` → ``_summarize_history`` 这根线
  是通的（预检那一次也能看到同一个 deadline）。

这只是 wall-clock 上界，**不声称**能取消已经在飞的上游请求。
"""

from __future__ import annotations

import asyncio
import time

import pytest

from db import backends
from services.agent import executor
from services.ai import quota
from services.agent.prompt import SYSTEM_PROMPT

USER = "000000000000000000000003"
ADMIN = {"id": USER, "per": ["admin"]}
MODEL = "gpt-x"


def _entry() -> dict:
    return {
        "key": "shared:64b7f0c2e1a3b4c5d6e7fc01",
        "scope": "shared",
        "owner_oid": "shared",
        "name": "lab",
        "kind": "openai_compat",
        "base_url": "https://lab.example.com/v1",
        "models": [MODEL],
        "api_key_cipher": "cipher",
        "api_key": None,
        "key_hint": "cdef",
        "enabled": True,
        "oid": "64b7f0c2e1a3b4c5d6e7fc01",
        "created_at": None,
        "updated_at": None,
        "expires_at": None,
    }


def _conversation(turns: int = 4) -> list[dict]:
    """一段**带 seq** 的历史：压缩切点靠它算 ``upto_seq``。"""
    messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
    seq = 0
    for index in range(turns):
        seq += 1
        messages.append({"role": "user", "content": f"问题 {index}", "seq": seq})
        seq += 1
        messages.append({"role": "assistant", "content": "回答" * 40, "seq": seq})
    return messages


class _CallSpy:
    """替换 ``executor._call_upstream``：记录调用次数，按剧本返回或抛错。"""

    def __init__(self, *, delay: float = 0.0, error: BaseException | None = None) -> None:
        self.calls: list[dict] = []
        self.delay = delay
        self.error = error

    async def __call__(self, payload, candidates, *, thinking=None):
        self.calls.append({"payload": payload, "candidates": candidates})
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error
        return (
            {
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "压缩后的摘要"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            },
            _entry(),
            "cache-key",
        )


class _AuditRepo:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    async def insert(self, **fields) -> str:
        self.rows.append(fields)
        return "a" * 24


class _QuotaRepo:
    async def get(self, user_oid, period, kind):
        return None

    async def consume(
        self, user_oid, period, kind, amount, default_limit, want_state=True
    ):
        return True, amount, default_limit


@pytest.fixture(autouse=True)
def _offline(monkeypatch, quiet_ai_governance):
    """额度/流水/配置全部进程内：这个文件只关心 deadline 的传导与分支。"""
    monkeypatch.setattr(backends, "get_ai_call_logs_repo", lambda session=None: _AuditRepo())
    monkeypatch.setattr(backends, "get_ai_quota_repo", lambda session=None: _QuotaRepo())
    async def peek(_user, _kind, _amount=1, *, roles=None):
        return True, 0, 1000

    async def consume(_user, _kind, _amount, *, roles=None, want_state=True):
        return True, 0, 1000

    monkeypatch.setattr(quota, "peek", peek)
    monkeypatch.setattr(quota, "consume", consume)

    import settings

    monkeypatch.setattr(settings, "AI_MODEL_CONTEXT_LIMITS", {}, raising=False)
    monkeypatch.setattr(settings, "AI_DEFAULT_CONTEXT_LIMIT", 2000, raising=False)
    monkeypatch.setattr(settings, "AI_CONTEXT_KEEP_TURNS", 1, raising=False)
    monkeypatch.setattr(settings, "AI_CONTEXT_COMPACT_RATIO", 0.8, raising=False)
    yield


async def _compact(deadline: float | None, *, force: bool = True):
    return await executor.compact_history(
        conversation=_conversation(),
        model=MODEL,
        candidates=[_entry()],
        user_oid=USER,
        roles=["admin"],
        request_id="run-1",
        snapshot=None,
        force=force,
        deadline=deadline,
    )


# ---------------------------------------------------------------------------
# 已过期：一次都不发
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_expired_deadline_skips_the_upstream_call_and_degrades(monkeypatch) -> None:
    spy = _CallSpy()
    monkeypatch.setattr(executor, "_call_upstream", spy)

    rebuilt, result = await _compact(deadline=time.monotonic() - 1)

    # 上游零调用：已经过期的 run 不该再买一次跨洋往返。
    assert spy.calls == []
    # 走的是既有降级路径（不是异常外溢、也不是「没压成」）。
    assert result["compacted"] is True
    assert result["degraded"] is True
    assert result["reason"] == "degraded"
    assert result["summary"] is None
    # 硬截断保留了 system 段（行为护栏 + 缓存前缀），且水位只降不升。
    assert rebuilt[0]["role"] == "system"
    assert result["tokens_after"] <= result["tokens_before"]


@pytest.mark.asyncio
async def test_expired_deadline_still_attempts_both_candidates(monkeypatch) -> None:
    """降级是**重试之后**的结论：两次尝试都过不了 deadline 闸，才截断。"""
    attempts: list[str] = []
    original = executor._summarize_history

    async def spy(**kwargs):
        attempts.append("attempt")
        return await original(**kwargs)

    monkeypatch.setattr(executor, "_summarize_history", spy)
    monkeypatch.setattr(executor, "_call_upstream", _CallSpy())

    _rebuilt, result = await _compact(deadline=time.monotonic() - 1)

    assert len(attempts) == executor.COMPACT_ATTEMPTS
    assert result["degraded"] is True


# ---------------------------------------------------------------------------
# 超时：仍然走降级
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upstream_timeout_goes_through_the_degraded_path(monkeypatch) -> None:
    spy = _CallSpy(error=asyncio.TimeoutError())
    monkeypatch.setattr(executor, "_call_upstream", spy)

    rebuilt, result = await _compact(deadline=time.monotonic() + 60)

    # 换候选重试 1 次 = 共 2 次机会（既有策略，不因为加了 deadline 就改）。
    assert len(spy.calls) == executor.COMPACT_ATTEMPTS
    assert result["compacted"] is True
    assert result["degraded"] is True
    assert result["upto_seq"] is not None
    assert rebuilt[0]["role"] == "system"


@pytest.mark.asyncio
async def test_a_hanging_upstream_is_cut_by_the_remaining_budget(monkeypatch) -> None:
    """上游不返回：deadline 到点就放弃等待（wall-clock 有上界）。"""
    spy = _CallSpy(delay=5.0)
    monkeypatch.setattr(executor, "_call_upstream", spy)

    started = time.monotonic()
    _rebuilt, result = await _compact(deadline=time.monotonic() + 0.2)
    elapsed = time.monotonic() - started

    assert result["degraded"] is True
    assert elapsed < 2.0, "压缩必须被 run 的剩余预算掐断，而不是等满上游"


# ---------------------------------------------------------------------------
# 无 deadline：手动压缩逐字不变
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_without_a_deadline_the_upstream_call_still_happens(monkeypatch) -> None:
    spy = _CallSpy()
    monkeypatch.setattr(executor, "_call_upstream", spy)

    _rebuilt, result = await _compact(deadline=None)

    assert len(spy.calls) == 1
    assert result["degraded"] is False
    assert result["summary"] == "压缩后的摘要"


@pytest.mark.asyncio
async def test_manual_compaction_passes_no_deadline(monkeypatch) -> None:
    """``compact_messages`` 没有 run 上下文：绝不替用户编一个截止时间出来。"""
    seen: list[dict] = []

    async def fake_compact_history(**kwargs):
        seen.append(kwargs)
        return [], {"compacted": False, "reason": "below_threshold"}

    async def fake_select(_user_oid, _model):
        return MODEL, [_entry()]

    monkeypatch.setattr(executor, "compact_history", fake_compact_history)
    monkeypatch.setattr(executor, "_select_model", fake_select)

    await executor.compact_messages(
        user=ADMIN,
        messages=[{"role": "user", "content": "hi"}],
        model=MODEL,
        force=True,
    )

    # 不传键 = 用默认 ``None``：手动压缩没有 run 上下文，也不该被编一个预算。
    assert seen and seen[0].get("deadline") is None


# ---------------------------------------------------------------------------
# run：deadline 一路传下去
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_threads_its_deadline_into_the_preflight_compaction(monkeypatch) -> None:
    """run 的 wall-clock 预算必须传到压缩：预检那一次也不能例外。

    截断到「预算 = 0」：主循环一次上游都不会发（0a 直接 timeout），而**预检**压缩
    排在那之前——它拿到的 deadline 已经过期，所以压缩也不该发。
    """
    from services.ai import config as ai_config
    from services.ai import credentials, providers, rate_limit, routing

    credentials._cache.clear()
    routing.reset_state()
    rate_limit.reset_state()
    routing._semaphore = None
    routing._semaphore_size = 0

    async def catalog(_user_oid):
        return [_entry()]

    async def allowed_models(_user_oid):
        return {MODEL: "lab"}

    async def resolve_key(_entry):
        return "sk-unit-test-0123456789abcdef"

    monkeypatch.setattr(credentials, "catalog", catalog)
    monkeypatch.setattr(credentials, "resolve_api_key", resolve_key)
    monkeypatch.setattr(ai_config, "allowed_models_for", allowed_models)

    monkeypatch.setattr(executor, "run_timeout_seconds", lambda: 0)

    class _Boom:
        async def __call__(self, payload, entry, api_key):
            raise AssertionError("no upstream call should happen with an expired run")

    monkeypatch.setattr(providers, "chat_completion", _Boom())
    spy = _CallSpy()
    monkeypatch.setattr(executor, "_call_upstream", spy)

    compacted: list[dict] = []

    async def on_summary(_summary, _upto):
        compacted.append({"summary": _summary})

    async def on_compaction(result):
        compacted.append(result)

    result = await executor.run(
        user=ADMIN,
        # 两轮 user：压缩切点只落在 user 之前，只有一轮时切不出旧段。
        messages=[
            {"role": "user", "content": "第一问", "seq": 1},
            {"role": "assistant", "content": "第一答", "seq": 2},
            {"role": "user", "content": "继续", "seq": 3},
        ],
        model=MODEL,
        summary="早前的摘要",
        context_tokens=10_000,
        on_round=lambda _payload: None,
        on_summary=on_summary,
        on_compaction=on_compaction,
    )

    assert spy.calls == []
    assert result["stop_reason"] == "timeout"
    # 预检压缩：降级而不是「没压成」，且没有摘要落库。
    assert any(item.get("degraded") is True for item in compacted)
    assert not any("summary" in item and item["summary"] for item in compacted)


def test_deadline_docstring_promises_no_mid_call_cancellation() -> None:
    """文档必须说清边界：``wait_for`` 只是停止等待，不是取消上游。"""
    doc = executor._summarize_history.__doc__ or ""
    assert "不是" in doc and "取消" in doc
