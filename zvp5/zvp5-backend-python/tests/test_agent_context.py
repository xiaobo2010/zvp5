"""``services.agent.context``：上下文组装、水位判定、压缩切点与降级截断。

这个模块的坑都在「错得很安静」那一侧：切点切在工具链中间会让上游 400；摘要放错
位置会让前缀缓存全站同 key；水位判晚一点就是一次 400 或半截回答。所以这里断言的
是那些不变量本身——顺序、成对、单调、有界——而不是某段文案。
"""

from __future__ import annotations

import json
import math

import pytest

import settings
from services.agent import context
from services.agent.prompt import PROMPT_VERSION, SYSTEM_PROMPT
from services.ai import prompt_cache


def _turn(n: int) -> list[dict]:
    return [
        {"role": "user", "content": f"问题 {n}"},
        {"role": "assistant", "content": f"回答 {n}"},
    ]


def _tool_turn(call_id: str = "call-1") -> list[dict]:
    return [
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": call_id, "type": "function", "function": {"name": "activity_list", "arguments": "{}"}},
        ]},
        {"role": "tool", "content": "结果正文", "tool_call_id": call_id, "tool_name": "activity_list"},
    ]


def _pairing_ok(messages: list[dict]) -> bool:
    """每条 tool 都有紧邻之前的 assistant(tool_calls) 认领；每条调用都有回复。"""
    pending: dict[str, int] = {}
    for message in messages:
        role = message.get("role")
        if role == "assistant" and message.get("tool_calls"):
            pending = {
                call.get("id"): 0
                for call in message["tool_calls"]
                if isinstance(call, dict) and isinstance(call.get("id"), str)
            }
            continue
        if role == "tool":
            call_id = message.get("tool_call_id")
            if call_id not in pending:
                return False
            pending.pop(call_id)
            continue
        pending = {}
    return not pending


# ---------------------------------------------------------------- build_messages


def test_build_messages_order_is_system_summary_history_pending():
    history = _turn(1)
    pending = [{"role": "user", "content": "这轮的问题"}]
    messages = context.build_messages(SYSTEM_PROMPT, "早期摘要", history, pending)

    assert [m["role"] for m in messages] == ["system", "system", "user", "assistant", "user"]
    assert messages[0]["content"] == SYSTEM_PROMPT
    assert PROMPT_VERSION in messages[0]["content"]
    assert "早期摘要" in messages[1]["content"]
    assert messages[2:4] == history
    assert messages[-1]["content"] == "这轮的问题"


def test_build_messages_without_summary_adds_no_second_system():
    messages = context.build_messages(SYSTEM_PROMPT, None, _turn(1), [])
    assert [m["role"] for m in messages] == ["system", "user", "assistant"]

    # 空串 / 纯空白与 None 等价：否则「没压缩」也会多出一条空 system 改掉前缀。
    for empty in ("", "   \n "):
        same = context.build_messages(SYSTEM_PROMPT, empty, _turn(1), [])
        assert same == messages


def test_build_messages_drops_dangling_tool_calls_and_their_tools():
    history = [
        {"role": "user", "content": "查一下"},
        # 缺 tool 回复的 assistant：上游会 400 整条请求。
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "call-a", "type": "function", "function": {"name": "activity_list", "arguments": "{}"}},
        ]},
    ]
    messages = context.build_messages(SYSTEM_PROMPT, None, history, [{"role": "user", "content": "算了"}])
    assert all(m.get("role") != "tool" for m in messages)
    assert all(not m.get("tool_calls") for m in messages)
    assert [m["content"] for m in messages] == [SYSTEM_PROMPT, "查一下", "算了"]


def test_build_messages_keeps_complete_pairs_and_drops_orphan_tool():
    history = [
        {"role": "user", "content": "查一下"},
        *_tool_turn("call-b"),
        # 无主 tool：它的 assistant 不在上下文里（压缩水位切掉了）。
        {"role": "tool", "content": "孤儿结果", "tool_call_id": "call-zzz"},
        {"role": "assistant", "content": "查完了"},
    ]
    messages = context.build_messages(SYSTEM_PROMPT, None, history, [])
    assert _pairing_ok(messages[1:])
    assert [m.get("tool_call_id") for m in messages if m.get("role") == "tool"] == ["call-b"]
    assert all(m.get("content") != "孤儿结果" for m in messages)


def test_build_messages_sanitizes_across_history_pending_boundary():
    # tool 落在 pending、assistant 在 history：跨边界也必须整体判定。
    history = [
        {"role": "user", "content": "查"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "call-x", "type": "function", "function": {"name": "sql_query", "arguments": "{}"}},
        ]},
    ]
    pending = [{"role": "tool", "content": "半条", "tool_call_id": "call-other"}]
    messages = context.build_messages(SYSTEM_PROMPT, None, history, pending)
    assert all(m.get("role") != "tool" for m in messages)
    assert all(not m.get("tool_calls") for m in messages)


def test_build_messages_does_not_mutate_inputs():
    history = _turn(1) + _tool_turn("call-c")
    pending = [{"role": "user", "content": "新问题"}]
    snapshot = json.dumps([history, pending], ensure_ascii=False)

    context.build_messages(SYSTEM_PROMPT, "摘要", history, pending)
    assert json.dumps([history, pending], ensure_ascii=False) == snapshot


def test_prefix_fingerprint_stable_across_uncompressed_calls():
    payload_a = {"model": "gpt-x", "messages": context.build_messages(SYSTEM_PROMPT, None, _turn(1), [{"role": "user", "content": "甲"}])}
    payload_b = {"model": "gpt-x", "messages": context.build_messages(SYSTEM_PROMPT, None, _turn(2) + _turn(3), [{"role": "user", "content": "乙"}])}

    assert prompt_cache.prefix_fingerprint(payload_a)
    assert prompt_cache.prefix_fingerprint(payload_a) == prompt_cache.prefix_fingerprint(payload_b)


def test_prefix_fingerprint_changes_only_at_compaction_point():
    payload_plain = {"model": "gpt-x", "messages": context.build_messages(SYSTEM_PROMPT, None, _turn(1), [])}
    payload_summary = {"model": "gpt-x", "messages": context.build_messages(SYSTEM_PROMPT, "早期摘要", _turn(5), [])}
    payload_summary_same = {"model": "gpt-x", "messages": context.build_messages(SYSTEM_PROMPT, "早期摘要", _turn(9), [])}

    base = prompt_cache.prefix_fingerprint(payload_plain)
    after = prompt_cache.prefix_fingerprint(payload_summary)
    assert after and after != base
    # 摘要在两条 system 里，所以它必然进指纹；摘要不变时指纹也不变。
    assert prompt_cache.prefix_fingerprint(payload_summary_same) == after


# -------------------------------------------------------------- estimate_tokens


def test_estimate_tokens_uses_chars_over_four_ceiling():
    messages = [{"role": "user", "content": "你好，世界"}]
    expected = math.ceil(len(json.dumps(messages, ensure_ascii=False)) / 4)
    assert context.estimate_tokens(messages) == expected
    # ensure_ascii=False 才不算转义：算错这条就说明中文被当成了 6 字符一个。
    escaped = len(json.dumps(messages, ensure_ascii=True))
    assert context.estimate_tokens(messages) < math.ceil(escaped / 4)


def test_estimate_tokens_empty_and_dirty_input():
    assert context.estimate_tokens([]) == 0
    assert context.estimate_tokens(None) == 0  # type: ignore[arg-type]
    assert context.estimate_tokens([{"role": "user", "content": "x"}, "垃圾"]) > 0


# --------------------------------------------------------------- context_limit


@pytest.fixture(autouse=True)
def _no_model_level_context(monkeypatch):
    """默认没有「模型级上下文」索引：别的用例建过目录会把它填上，跨文件串味。"""
    from services.ai import credentials

    monkeypatch.setattr(credentials, "_model_contexts", {})


def test_context_limit_prefers_the_model_level_context(monkeypatch):
    """顺序：模型级（凭据里配的）→ AI_MODEL_CONTEXT_LIMITS → 默认值（R4）。"""
    from services.ai import credentials

    monkeypatch.setattr(credentials, "_model_contexts", {"lab/gpt-x": 64000})
    monkeypatch.setattr(settings, "AI_MODEL_CONTEXT_LIMITS", {"gpt-x": 128000}, raising=False)
    monkeypatch.setattr(settings, "AI_DEFAULT_CONTEXT_LIMIT", 4096, raising=False)

    # 合格 ID 与裸名都要命中：窗口是模型的属性，不是渠道的。
    assert context.context_limit("lab/gpt-x") == 64000
    assert context.context_limit("gpt-x") == 64000
    # 没配的模型继续走配置表 / 默认值。
    assert context.context_limit("lab/gpt-z") == 4096



def test_context_limit_exact_model_wins(monkeypatch):
    monkeypatch.setattr(settings, "AI_MODEL_CONTEXT_LIMITS", {"gpt-x": 128000, "*": 8000}, raising=False)
    monkeypatch.setattr(settings, "AI_DEFAULT_CONTEXT_LIMIT", 4096, raising=False)
    assert context.context_limit("gpt-x") == 128000
    assert context.context_limit("other") == 8000
    assert context.context_limit(None) == 8000


def test_context_limit_falls_back_to_default(monkeypatch):
    monkeypatch.delattr(settings, "AI_MODEL_CONTEXT_LIMITS", raising=False)
    monkeypatch.setattr(settings, "AI_DEFAULT_CONTEXT_LIMIT", 65536, raising=False)
    assert context.context_limit("any") == 65536
    assert context.context_limit(None) == 65536


@pytest.mark.parametrize("bad", [0, -1, True, "8192", None, 1.5])
def test_context_limit_ignores_illegal_values(monkeypatch, bad):
    monkeypatch.setattr(settings, "AI_MODEL_CONTEXT_LIMITS", {"gpt-x": bad, "*": bad}, raising=False)
    monkeypatch.setattr(settings, "AI_DEFAULT_CONTEXT_LIMIT", bad, raising=False)
    # 全都不合法：回退常量默认值，而不是把 0 当成上限。
    assert context.context_limit("gpt-x") == context.DEFAULT_CONTEXT_LIMIT


def test_context_limit_bad_exact_value_falls_through_to_wildcard(monkeypatch):
    monkeypatch.setattr(settings, "AI_MODEL_CONTEXT_LIMITS", {"gpt-x": "big", "*": 32000}, raising=False)
    assert context.context_limit("gpt-x") == 32000


# ------------------------------------------------------------- needs_compaction


def test_compact_ratio_defaults_to_eighty_percent(monkeypatch):
    """默认阈值是 0.80：200K 窗口下 160K 才压（改这个值等于改线上行为）。"""
    monkeypatch.setattr(settings, "AI_CONTEXT_COMPACT_RATIO", context.DEFAULT_COMPACT_RATIO, raising=False)

    assert context.DEFAULT_COMPACT_RATIO == 0.80
    assert context.compact_ratio() == 0.80


def test_compact_ratio_reads_the_runtime_override_first(monkeypatch):
    """运行期设置优先于 settings（在线可调），非法值继续往下找。"""
    from services import settings_runtime

    monkeypatch.setattr(settings, "AI_CONTEXT_COMPACT_RATIO", 0.9, raising=False)
    monkeypatch.setattr(
        settings_runtime, "get_override", lambda key: 0.5 if key == context.RUNTIME_COMPACT_RATIO else None
    )
    assert context.compact_ratio() == 0.5

    for bad in (0, -1, 2, True, "0.5", float("nan")):
        monkeypatch.setattr(
            settings_runtime,
            "get_override",
            lambda key, bad=bad: bad if key == context.RUNTIME_COMPACT_RATIO else None,
        )
        # 配错一个十进制不该把部署里刻意调过的阈值抹掉：继续走 settings。
        assert context.compact_ratio() == 0.9


def test_compact_ratio_is_registered_for_online_editing():
    """键名是管理端契约：注册表里必须有它，默认值来自代码常量。"""
    from services import settings_runtime

    spec = settings_runtime.registry()[context.RUNTIME_COMPACT_RATIO]
    assert spec.type == "float"
    assert spec.default == context.DEFAULT_COMPACT_RATIO
    assert settings_runtime._validate(spec, "0.7") == 0.7
    with pytest.raises(settings_runtime.SettingsValidationError):
        settings_runtime._validate(spec, 1.5)


def test_existing_summary_reads_only_the_leading_system_block(monkeypatch):
    """摘要正文只在前导 system 段里找：正文里出现同样的抬头不算。"""
    built = context.build_messages(
        SYSTEM_PROMPT,
        "很早就问过 A，结论是 B",
        [{"role": "user", "content": "现在的问题"}],
        [],
    )

    assert context.existing_summary(built) == "很早就问过 A，结论是 B"
    assert context.existing_summary([{"role": "user", "content": "没有摘要"}]) == ""
    # 尾部（非前导 system）里的同样文本不认：那是历史消息，不是摘要。
    trailing = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": context.SUMMARY_HEADER + "假摘要"},
    ]
    assert context.existing_summary(trailing) == ""


def test_needs_compaction_prefers_measured_prompt_tokens(monkeypatch):
    monkeypatch.setattr(settings, "AI_CONTEXT_COMPACT_RATIO", 0.5, raising=False)
    monkeypatch.setattr(settings, "AI_MODEL_CONTEXT_LIMITS", {}, raising=False)
    monkeypatch.setattr(settings, "AI_DEFAULT_CONTEXT_LIMIT", 1000, raising=False)

    tiny = [{"role": "user", "content": "短"}]
    assert context.needs_compaction(500, tiny, 1000) is True   # 实测触顶，估算不触
    assert context.needs_compaction(499, tiny, 1000) is False  # 未到 ceil(1000*0.5)


def test_needs_compaction_estimates_when_no_measurement(monkeypatch):
    monkeypatch.setattr(settings, "AI_CONTEXT_COMPACT_RATIO", 0.75, raising=False)
    messages = [{"role": "user", "content": "字" * 4000}]
    estimated = context.estimate_tokens(messages)
    assert estimated > 750

    for absent in (None, -1, True, "900"):
        assert context.needs_compaction(absent, messages, 1000) is True  # type: ignore[arg-type]


def test_needs_compaction_ratio_and_limit_fallback(monkeypatch):
    monkeypatch.setattr(settings, "AI_CONTEXT_COMPACT_RATIO", 2, raising=False)
    monkeypatch.setattr(settings, "AI_DEFAULT_CONTEXT_LIMIT", 1000, raising=False)
    messages = [{"role": "user", "content": "字" * 5000}]
    # 非法 ratio → 默认 0.80；5000 字 ≈ 1250，估算已越过 800，这里靠 limit 回退生效。
    assert context.needs_compaction(None, messages, 0) is True
    assert context.needs_compaction(800, [], 1000) is True
    assert context.needs_compaction(799, [], 1000) is False


# -------------------------------------------------------------- plan_compaction


def test_plan_compaction_cuts_before_user_turn():
    messages = _turn(1) + _turn(2) + _turn(3)
    older, recent = context.plan_compaction(messages, keep_turns=2)

    assert recent[0]["role"] == "user"
    assert len(recent) == 4
    assert len(older) == 2
    assert older + recent == messages


def test_plan_compaction_never_splits_tool_chain():
    messages = _turn(1) + _tool_turn("call-1") + [{"role": "assistant", "content": "收尾"}] + _turn(2)
    older, recent = context.plan_compaction(messages, keep_turns=1)

    assert recent[0]["role"] == "user"
    assert _pairing_ok(older)
    # assistant(tool_calls) 与它的 tool 必须同侧，且落在被摘要的旧段里。
    assert [m["role"] for m in older] == ["user", "assistant", "assistant", "tool", "assistant"]
    assert older[2]["tool_calls"][0]["id"] == older[3]["tool_call_id"]


def test_plan_compaction_cut_directly_after_tool_result():
    messages = _turn(1) + _tool_turn("call-2") + [{"role": "user", "content": "继续"}]
    older, recent = context.plan_compaction(messages, keep_turns=1)

    assert recent == [{"role": "user", "content": "继续"}]
    assert _pairing_ok(older)
    assert older[-1]["role"] == "tool"


def test_plan_compaction_without_user_message_is_noop():
    messages = [{"role": "system", "content": "提示"}, {"role": "assistant", "content": "孤零零"}]
    older, recent = context.plan_compaction(messages, keep_turns=6)
    assert older == []
    assert recent == messages


def test_plan_compaction_not_enough_turns_is_noop():
    messages = _turn(1)
    older, recent = context.plan_compaction(messages, keep_turns=6)
    assert older == []
    assert recent == messages


def test_plan_compaction_rejects_dangling_older():
    # 旧段本身是残链：与其把残骸写进摘要，不如这一轮不压缩。
    messages = [
        {"role": "user", "content": "查"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "call-d", "type": "function", "function": {"name": "sql_query", "arguments": "{}"}},
        ]},
        {"role": "user", "content": "还在吗"},
    ]
    older, recent = context.plan_compaction(messages, keep_turns=1)
    assert older == []
    assert recent == messages


def test_plan_compaction_invalid_keep_turns_uses_configured_default(monkeypatch):
    monkeypatch.setattr(settings, "AI_CONTEXT_KEEP_TURNS", 2, raising=False)
    messages = _turn(1) + _turn(2) + _turn(3)

    for bad in (0, -3, True, "2"):
        older, recent = context.plan_compaction(messages, keep_turns=bad)  # type: ignore[arg-type]
        assert len(recent) == 4
        assert len(older) == 2


# ---------------------------------------------------------------- hard_truncate


def test_hard_truncate_drops_oldest_first_and_keeps_system():
    messages = [{"role": "system", "content": "提示"}] + _turn(1) + _turn(2) + _turn(3)
    trimmed = context.hard_truncate(messages, limit_tokens=60)

    assert trimmed[0] == messages[0]
    assert context.estimate_tokens(trimmed) <= 60
    # 留下的是「连续的最近一段」，不是东拼西凑的补丁。
    assert trimmed[1:] == messages[len(messages) - (len(trimmed) - 1) :]
    assert "问题 1" not in [m.get("content") for m in trimmed]


def test_hard_truncate_never_drops_system_even_over_budget():
    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + _turn(1) + _turn(2)
    trimmed = context.hard_truncate(messages, limit_tokens=10)

    # system 是行为护栏 + 缓存前缀：超预算也留着，只丢历史。
    assert trimmed[0] == messages[0]
    assert len(trimmed) == 2
    assert trimmed[-1] == messages[-1]


def test_hard_truncate_never_leaves_dangling_pair():
    messages = [{"role": "system", "content": "提示"}]
    for index in range(6):
        messages += [{"role": "user", "content": f"问题 {index}"}]
        messages += _tool_turn(f"call-{index}")
    messages += [{"role": "assistant", "content": "收尾"}]

    trimmed = context.hard_truncate(messages, limit_tokens=120)
    assert _pairing_ok(trimmed[1:])
    assert all(m.get("role") != "tool" or m.get("tool_call_id") != "call-0" for m in trimmed)


def test_hard_truncate_is_noop_under_limit():
    messages = _turn(1) + _turn(2)
    assert context.hard_truncate(messages, limit_tokens=10000) == messages


def test_hard_truncate_keeps_last_message_even_if_alone_over_limit():
    messages = [{"role": "user", "content": "字" * 5000}]
    assert context.hard_truncate(messages, limit_tokens=10) == messages


def _hard_truncate_reference(messages: list[dict], limit_tokens: int) -> list[dict]:
    """改造前的实现（逐条 ``pop(0)`` + 每次重算整段）：等价性基准，别优化它。"""
    budget = context._positive_int(limit_tokens, context.DEFAULT_CONTEXT_LIMIT)
    items = [dict(item) for item in context._messages_of(messages)]
    head_end = 0
    while head_end < len(items) and items[head_end].get("role") in context._SYSTEM_ROLES:
        head_end += 1
    head, rest = items[:head_end], items[head_end:]

    def drop_orphans(target: list[dict]) -> None:
        while target and target[0].get("role") == "tool":
            target.pop(0)

    drop_orphans(rest)
    while len(rest) > 1 and context.estimate_tokens(head) + context.estimate_tokens(rest) > budget:
        rest.pop(0)
        drop_orphans(rest)
    drop_orphans(rest)
    return head + rest


def test_hard_truncate_matches_the_quadratic_reference_on_random_inputs():
    """线性实现必须与旧实现**逐字节**等价（随机 300 组：角色混排 + 各种水位）。

    旧写法每轮都对「剩下的整段」``json.dumps`` 两次，长上下文里一次降级就是上万
    次整段编码；这次换成一次预算 + 区间和。但「更快」不能换来切点漂移：少留一条
    就少了一段工具结果，多留一条就可能又超限（下一次调用直接 400）。所以这里用
    旧实现当 oracle。
    """
    import random

    rng = random.Random(20261115)
    roles = ("system", "developer", "user", "assistant", "tool")
    for _ in range(300):
        messages: list[dict] = []
        for index in range(rng.randint(0, 14)):
            role = rng.choice(roles)
            item: dict = {
                "role": role,
                "content": "中" * rng.randint(0, 60) + "x" * rng.randint(0, 20),
            }
            if role == "assistant" and rng.random() < 0.5:
                item["tool_calls"] = [
                    {
                        "id": f"call-{index}",
                        "type": "function",
                        "function": {"name": "activity_list", "arguments": "{}"},
                    }
                ]
            if role == "tool":
                item["tool_call_id"] = f"call-{index}"
            if rng.random() < 0.3:
                item["seq"] = index
            messages.append(item)
        budget = rng.choice([1, 5, 40, 200, 900, 4000])
        assert context.hard_truncate(messages, budget) == _hard_truncate_reference(
            messages, budget
        ), (messages, budget)


def test_hard_truncate_serializes_each_message_once(monkeypatch):
    """序列化次数必须是 O(n)：旧实现是 O(n²)（每条丢一次、整段重算两次）。"""
    calls = {"n": 0}
    real = context.json.dumps

    def counting(*args, **kwargs):
        calls["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(context.json, "dumps", counting)

    messages = [{"role": "system", "content": "提示"}]
    for index in range(40):
        messages += _turn(index)
    context.hard_truncate(messages, limit_tokens=400)

    # 上界 = 每条一次（+ head 的估算一次）再留一点余量；O(n²) 在这个规模上是
    # 数百次，一眼能分开。故意用一个宽松的常数，避免钉死实现细节。
    assert calls["n"] <= len(messages) + 5



# ---------------------------------------------------------------------- compact


@pytest.mark.asyncio
async def test_compact_calls_injected_complete_and_truncates():
    seen: dict[str, object] = {}

    async def complete(messages: list[dict], max_tokens: int) -> str:
        seen["messages"] = messages
        seen["max_tokens"] = max_tokens
        return "  摘" + "要" * 100 + "  "

    text = await context.compact(_turn(1) + _turn(2), _turn(3), complete=complete, max_tokens=10)

    assert seen["max_tokens"] == 10
    assert [m["role"] for m in seen["messages"]] == ["system", "user"]  # type: ignore[index]
    assert "问题 1" in seen["messages"][-1]["content"]  # type: ignore[index]
    assert text.startswith("摘")
    assert len(text) <= 10 * 4


@pytest.mark.asyncio
async def test_compact_accepts_sync_complete():
    def complete(messages: list[dict], max_tokens: int) -> str:
        return "同步摘要"

    text = await context.compact(_turn(1), [], complete=complete)  # type: ignore[arg-type]
    assert text == "同步摘要"


@pytest.mark.asyncio
async def test_compact_empty_older_skips_upstream():
    called = False

    async def complete(messages: list[dict], max_tokens: int) -> str:
        nonlocal called
        called = True
        return "不该被调用"

    assert await context.compact([], _turn(1), complete=complete) == ""
    assert called is False


@pytest.mark.asyncio
async def test_compact_propagates_upstream_failure():
    async def complete(messages: list[dict], max_tokens: int) -> str:
        raise RuntimeError("upstream down")

    # 降级（换候选 / 硬截断）是调用方的决定，这里不许吞。
    with pytest.raises(RuntimeError, match="upstream down"):
        await context.compact(_turn(1), [], complete=complete)


@pytest.mark.asyncio
async def test_compact_handles_non_str_response():
    async def complete(messages: list[dict], max_tokens: int) -> str:
        return None  # type: ignore[return-value]

    assert await context.compact(_turn(1), [], complete=complete) == ""


# ----------------------------------------------------- summary prompt / parsing


def test_parse_summary_strips_and_truncates():
    assert context.parse_summary("  正文  ", 100) == "正文"
    assert context.parse_summary("a" * 40, 10) == "a" * 10
    assert context.parse_summary(None, 10) == ""  # type: ignore[arg-type]
    # 非法上限不抛：回退到 max_tokens*4 的量级。
    assert context.parse_summary("b" * 100, 0) == "b" * 100


def test_compose_summary_prompt_renders_roles_and_clips_tool_output():
    older = [
        {"role": "user", "content": "查活动"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "activity_list", "arguments": "{}"}},
        ]},
        {"role": "tool", "content": "Z" * 5000, "tool_call_id": "c1", "tool_name": "activity_list"},
    ]
    prompt = context.compose_summary_prompt(older)

    assert [m["role"] for m in prompt] == ["system", "user"]
    body = prompt[-1]["content"]
    assert "查活动" in body
    assert "activity_list" in body
    assert len(body) < 2000  # 工具全文 5000 字符没有原样进摘要输入
    assert "Z" * 5000 not in body


def test_compose_summary_prompt_caps_input_keeping_recent_tail():
    older = [{"role": "user", "content": f"第{index}条：" + "x" * 400} for index in range(200)]
    prompt = context.compose_summary_prompt(older)
    body = prompt[-1]["content"]

    assert len(body) <= context.SUMMARY_INPUT_MAX_CHARS + 200
    assert "第199条" in body      # 靠近切点的内容优先保留
    assert "第0条" not in body
