"""``services.ai.usage``：usage 解析与 token → 额度的折算。

这层守的是**一个不变式**：token 列全 NULL ⟺ 该行的 units 是估算值。它一旦破了，
报表里「实扣」与「估算」就混在一列里，对账时没人分得清差在哪。

流式那一侧还要能处理真实的字节形态：一帧可能被 TCP 切成两段、多帧可能挤在一段、
最后一帧可能没有空行收尾。这些都用字节级用例钉住——它们不会报错，只会在生产上
悄悄丢掉 usage。
"""

from __future__ import annotations

import math

import pytest

from services.ai import usage


# ---------------------------------------------------------------------------
# usage dict → 三个 token 列
# ---------------------------------------------------------------------------


def test_token_fields_reads_the_three_upstream_numbers() -> None:
    fields = usage.token_fields(
        {"prompt_tokens": 11, "completion_tokens": 22, "total_tokens": 33}
    )

    assert fields == {
        "prompt_tokens": 11,
        "completion_tokens": 22,
        "total_tokens": 33,
    }


def test_token_fields_keeps_missing_keys_as_none() -> None:
    """上游只报一部分就只落一部分：缺的那个是「没报」，不是 0。"""
    fields = usage.token_fields({"prompt_tokens": 5})

    assert fields == {
        "prompt_tokens": 5,
        "completion_tokens": None,
        "total_tokens": None,
    }


def test_token_fields_never_invents_total_tokens() -> None:
    """不拿 prompt+completion 推算合计：上游合计可能含缓存/推理 token。"""
    fields = usage.token_fields({"prompt_tokens": 5, "completion_tokens": 7})

    assert fields["total_tokens"] is None


@pytest.mark.parametrize("usage_obj", [None, [], "usage", 42])
def test_token_fields_tolerates_non_dict(usage_obj) -> None:
    assert usage.token_fields(usage_obj) == {
        "prompt_tokens": None,
        "completion_tokens": None,
        "total_tokens": None,
    }


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("12", 12),  # 数字字符串认
        (12.0, 12),  # float 认（上游偶尔给 12.0）
        (True, None),  # bool 不认（它是 int 的子类）
        (-1, None),  # 负数不认
        ("abc", None),
        (None, None),
    ],
)
def test_token_fields_coerces_only_real_numbers(raw, expected) -> None:
    assert usage.token_fields({"total_tokens": raw})["total_tokens"] == expected


def test_has_tokens_requires_at_least_one_number() -> None:
    assert usage.has_tokens({"prompt_tokens": None, "completion_tokens": 0}) is True
    assert usage.has_tokens(
        {"prompt_tokens": None, "completion_tokens": None, "total_tokens": None}
    ) is False


# ---------------------------------------------------------------------------
# 折算
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("total", "units"),
    [
        (0, 1),  # 至少 1：调用发生过
        (None, 1),
        (1, 1),
        (1000, 1),
        (1001, 2),
        (2500, 3),
    ],
)
def test_units_from_total_tokens_keeps_the_historical_formula(total, units) -> None:
    assert usage.units_from_total_tokens(total) == units


def test_estimate_units_counts_chars_without_the_thousand_divisor() -> None:
    """**现行**估算口径：``ceil(prompt_chars/4 + completion_chars/4)``。

    注意它与实测口径的量纲不同（估算 = token 量级，实测 = 每 1000 token 一个
    单位）。这是本轮刻意不改的历史行为（``units`` 语义不动），已在
    ``docs/AI_GATEWAY_API.md`` §6 记为已知差异：回填/比较时不能把两种行直接相加。
    """
    assert usage.estimate_units(0, 0) == 1
    assert usage.estimate_units(4, 0) == 1
    assert usage.estimate_units(4000, 4000) == 2000


def test_estimate_units_stays_identical_to_the_previous_inline_formula() -> None:
    """逐字节复刻改造前 router 里的那行 ``max(1, ceil(p/4 + c/4))``。"""
    for prompt_chars in (0, 1, 3, 999, 12345):
        for completion_chars in (0, 7, 4000):
            expected = max(
                1, math.ceil(prompt_chars / 4 + completion_chars / 4)
            )
            assert usage.estimate_units(prompt_chars, completion_chars) == expected


def test_prompt_chars_serialises_messages_and_never_stores_them() -> None:
    payload = {"messages": [{"role": "user", "content": "你好"}]}

    assert usage.prompt_chars(payload) == len(
        '[{"role": "user", "content": "你好"}]'
    )


def test_prompt_chars_tolerates_junk() -> None:
    assert usage.prompt_chars({}) == 2  # "[]"
    assert usage.prompt_chars(None) == 0
    assert usage.prompt_chars({"messages": object()}) == 0


# ---------------------------------------------------------------------------
# 流式：增量解析 usage 帧
# ---------------------------------------------------------------------------

USAGE_FRAME = b'data: {"choices":[],"usage":{"prompt_tokens":7,"completion_tokens":5,"total_tokens":12}}\n\n'
DELTA_FRAME = b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n'
DONE_FRAME = b"data: [DONE]\n\n"


def test_tracker_ignores_delta_frames_without_usage() -> None:
    tracker = usage.StreamUsageTracker()
    tracker.feed(DELTA_FRAME)
    tracker.feed(DONE_FRAME)
    tracker.close()

    assert tracker.captured is False
    assert tracker.total_tokens is None


def test_tracker_reads_the_usage_frame() -> None:
    tracker = usage.StreamUsageTracker()
    tracker.feed(DELTA_FRAME)
    tracker.feed(USAGE_FRAME)
    tracker.feed(DONE_FRAME)
    tracker.close()

    assert tracker.captured is True
    assert (tracker.prompt_tokens, tracker.completion_tokens) == (7, 5)
    assert tracker.total_tokens == 12


def test_tracker_survives_a_frame_split_across_chunks() -> None:
    """TCP 切片是常态：一帧被切成三段也必须能拼出来。"""
    tracker = usage.StreamUsageTracker()
    for piece in (USAGE_FRAME[:20], USAGE_FRAME[20:50], USAGE_FRAME[50:]):
        tracker.feed(piece)
    tracker.close()

    assert tracker.total_tokens == 12


def test_tracker_handles_several_frames_in_one_chunk() -> None:
    tracker = usage.StreamUsageTracker()
    tracker.feed(DELTA_FRAME + USAGE_FRAME + DONE_FRAME)
    tracker.close()

    assert tracker.total_tokens == 12


def test_tracker_parses_the_last_frame_without_a_trailing_blank_line() -> None:
    """上游可能在最后一帧后直接关连接：没有空行收尾也要读出来。"""
    tracker = usage.StreamUsageTracker()
    tracker.feed(b'data: {"usage":{"total_tokens":9}}')
    tracker.close()

    assert tracker.total_tokens == 9


def test_tracker_handles_crlf_frames() -> None:
    tracker = usage.StreamUsageTracker()
    tracker.feed(
        b'data: {"usage":{"total_tokens":3}}\r\n\r\n'
    )
    tracker.close()

    assert tracker.total_tokens == 3


def test_tracker_keeps_the_last_usage_when_upstream_repeats_it() -> None:
    """有的实现逐帧带累计 usage：取最后一份（最完整）。"""
    tracker = usage.StreamUsageTracker()
    tracker.feed(b'data: {"usage":{"total_tokens":1}}\n\n')
    tracker.feed(b'data: {"usage":{"total_tokens":2}}\n\n')
    tracker.close()

    assert tracker.total_tokens == 2


def test_tracker_tolerates_malformed_json() -> None:
    tracker = usage.StreamUsageTracker()
    tracker.feed(b"data: {not json}\n\n")
    tracker.feed(USAGE_FRAME)
    tracker.close()

    assert tracker.total_tokens == 12


def test_tracker_tolerates_non_sse_bytes_and_does_not_grow_without_bound() -> None:
    """上游吐的不是 SSE：缓冲区超限即丢弃，绝不吃穿内存。"""
    tracker = usage.StreamUsageTracker()
    tracker.feed(b"x" * (usage.MAX_BUFFER_BYTES + 1))
    tracker.close()

    assert tracker.captured is False


def test_tracker_ignores_empty_and_non_bytes_chunks() -> None:
    tracker = usage.StreamUsageTracker()
    tracker.feed(b"")
    tracker.feed("not bytes")  # type: ignore[arg-type]
    tracker.close()

    assert tracker.captured is False


def test_tracker_usage_frame_with_empty_usage_is_not_captured() -> None:
    """``"usage": null`` 是「这次没报」，不是「0 个 token」。"""
    tracker = usage.StreamUsageTracker()
    tracker.feed(b'data: {"choices":[],"usage":null}\n\n')
    tracker.close()

    assert tracker.captured is False


def test_end_to_end_units_match_the_formula() -> None:
    """流式拿到 usage 时，units 与 token 列同源（同一把尺子）。"""
    tracker = usage.StreamUsageTracker()
    tracker.feed(USAGE_FRAME)
    tracker.close()

    assert usage.units_from_total_tokens(tracker.total_tokens) == 1
    assert math.ceil(12 / 1000) == 1
