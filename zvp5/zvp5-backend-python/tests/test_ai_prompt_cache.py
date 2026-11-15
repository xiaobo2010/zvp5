"""``services.ai.prompt_cache``：上游 Prompt Cache 的请求体加工。

这个模块有两次「写错了也不报错」的机会：

* **指纹把多轮尾部算进去**：每轮对话的 key 都变，上游路由亲和被打散，缓存永远
  不命中——但请求本身完全正常，线上看不到任何异常。
* **往不该加断点的上游加 ``cache_control``**：直连 OpenAI 官方端点时那是未知
  字段，严格校验的实现直接 400，对话直接挂。

所以这里断言的是**形状**与**恒等**：哪些 host 会改、改的是哪一层、入参有没有被
就地修改。全部离线（monkeypatch 配置，不出网、不连库）。
"""

from __future__ import annotations

import copy

import settings  # noqa: F401  (services.ai.config 读它，先导入保证顺序一致)
from services.ai import config as ai_config
from services.ai import prompt_cache

OPENAI = {"base_url": "https://api.openai.com/v1"}
DEEPSEEK = {"base_url": "https://api.deepseek.com/v1"}
OPENROUTER = {"base_url": "https://openrouter.ai/api/v1"}


def _configure(
    monkeypatch,
    *,
    mode: str,
    key_hosts: list[str] | None = None,
    breakpoint_hosts: list[str] | None = None,
    prefix: str = "zvp",
) -> None:
    """把四项缓存配置钉成固定值；``None`` = 空列表（即不追加任何 host）。"""
    monkeypatch.setattr(settings, "AI_PROMPT_CACHE", mode, raising=False)
    monkeypatch.setattr(
        settings, "AI_PROMPT_CACHE_HOSTS", key_hosts or [], raising=False
    )
    monkeypatch.setattr(
        settings,
        "AI_PROMPT_CACHE_BREAKPOINT_HOSTS",
        breakpoint_hosts or [],
        raising=False,
    )
    monkeypatch.setattr(settings, "AI_PROMPT_CACHE_KEY_PREFIX", prefix, raising=False)


def _payload(**overrides) -> dict:
    body = {
        "model": "gpt-x",
        "messages": [
            {"role": "system", "content": "你是助教。"},
            {"role": "user", "content": "第一问"},
        ],
    }
    body.update(overrides)
    return body


# ---------------------------------------------------------------------------
# 默认配置（真实 settings，不 monkeypatch）
# ---------------------------------------------------------------------------


def test_defaults_are_the_safe_ones() -> None:
    """默认只开「路由提示」这一层：不改请求形状，最坏被上游忽略。"""
    assert ai_config.prompt_cache_mode() == "auto"
    assert ai_config.prompt_cache_key_hosts() == {"api.openai.com"}
    # 断点是**改写 content 形状**的能力，默认必须是空集。
    assert ai_config.prompt_cache_breakpoint_hosts() == frozenset()
    assert ai_config.prompt_cache_key_prefix() == "zvp"


def test_host_of_parses_the_base_url() -> None:
    assert prompt_cache.host_of(OPENAI) == "api.openai.com"
    assert prompt_cache.host_of({"base_url": "https://API.OpenAI.com/v1/"}) == (
        "api.openai.com"
    )
    assert prompt_cache.host_of({"base_url": "not a url"}) == ""
    assert prompt_cache.host_of({}) == ""


# ---------------------------------------------------------------------------
# off / auto：只注入 prompt_cache_key
# ---------------------------------------------------------------------------


def test_off_returns_the_payload_untouched(monkeypatch) -> None:
    _configure(monkeypatch, mode="off")
    payload = _payload()

    body, cache_key = prompt_cache.request_body(payload, OPENAI)

    assert body is payload
    assert cache_key is None


def test_auto_injects_a_prefixed_key_on_the_openai_host(monkeypatch) -> None:
    _configure(monkeypatch, mode="auto")
    payload = _payload()

    body, cache_key = prompt_cache.request_body(payload, OPENAI)

    key = body["prompt_cache_key"]
    assert key.startswith("zvp:")
    assert len(key) <= prompt_cache.KEY_MAX_LEN
    # 审计值必须与真正发出去的值一致，否则事后无法把调用与缓存片段对上。
    assert cache_key == key
    # 只多这一个字段：其它键一个不多、一个不少（不改形状）。
    assert set(body) - set(payload) == {"prompt_cache_key"}
    assert set(payload) - set(body) == set()


def test_auto_matches_the_fingerprint_directly(monkeypatch) -> None:
    """注入的 key = 前缀 + 指纹：两处各算一遍就会出现「审计值与实发值不同」。"""
    _configure(monkeypatch, mode="auto")
    payload = _payload()

    body, _ = prompt_cache.request_body(payload, OPENAI)

    assert body["prompt_cache_key"] == (
        f"zvp:{prompt_cache.prefix_fingerprint(payload)}"
    )


def test_auto_leaves_unknown_hosts_alone(monkeypatch) -> None:
    """白名单外的 host 一个字都不改：这个字段对它们没有意义。"""
    _configure(monkeypatch, mode="auto")
    payload = _payload()

    body, cache_key = prompt_cache.request_body(payload, DEEPSEEK)

    assert body is payload
    assert cache_key is None
    assert "prompt_cache_key" not in payload


def test_client_supplied_key_is_kept_and_audited(monkeypatch) -> None:
    """客户端自己算的 key 不覆盖：它可能想让多个会话共享同一段缓存。"""
    _configure(monkeypatch, mode="auto")
    payload = _payload(prompt_cache_key="client-key")

    body, cache_key = prompt_cache.request_body(payload, OPENAI)

    assert body["prompt_cache_key"] == "client-key"
    assert cache_key == "client-key"


def test_client_supplied_key_is_truncated_in_the_audit_value_only(monkeypatch) -> None:
    _configure(monkeypatch, mode="auto")
    long_key = "k" * 200
    payload = _payload(prompt_cache_key=long_key)

    body, cache_key = prompt_cache.request_body(payload, OPENAI)

    assert cache_key == "k" * prompt_cache.KEY_MAX_LEN
    assert body["prompt_cache_key"] == long_key


def test_a_non_dict_entry_is_passed_through(monkeypatch) -> None:
    _configure(monkeypatch, mode="auto")
    payload = _payload()

    assert prompt_cache.request_body(payload, None) == (payload, None)
    assert prompt_cache.request_body("not a dict", OPENAI) == ("not a dict", None)


# ---------------------------------------------------------------------------
# 指纹：只覆盖稳定前缀
# ---------------------------------------------------------------------------


def test_fingerprint_ignores_dict_key_order() -> None:
    """同一个字典换构造路径时键序会变：不排序就会「同一前缀两个 key」。"""
    first = {"model": "gpt-x", "tools": [{"name": "a"}], "temperature": 0.2}
    second = {"temperature": 0.2, "tools": [{"name": "a"}], "model": "gpt-x"}

    assert prompt_cache.prefix_fingerprint(first) == (
        prompt_cache.prefix_fingerprint(second)
    )


def test_fingerprint_ignores_appended_conversation_turns() -> None:
    """多轮推进时 key 必须不变——否则每轮都换一个路由，缓存永远不命中。"""
    base = _payload()
    longer = _payload(
        messages=base["messages"]
        + [
            {"role": "assistant", "content": "第一答"},
            {"role": "user", "content": "第二问"},
        ]
    )

    assert prompt_cache.prefix_fingerprint(base) == (
        prompt_cache.prefix_fingerprint(longer)
    )


def test_fingerprint_changes_with_the_system_text() -> None:
    changed = _payload(
        messages=[
            {"role": "system", "content": "你是另一个助教。"},
            {"role": "user", "content": "第一问"},
        ]
    )

    assert prompt_cache.prefix_fingerprint(_payload()) != (
        prompt_cache.prefix_fingerprint(changed)
    )


def test_fingerprint_changes_with_the_model() -> None:
    assert prompt_cache.prefix_fingerprint(_payload()) != (
        prompt_cache.prefix_fingerprint(_payload(model="gpt-y"))
    )


def test_fingerprint_changes_with_the_tools() -> None:
    tools = [{"type": "function", "function": {"name": "a"}}]
    other_tools = [{"type": "function", "function": {"name": "b"}}]

    assert prompt_cache.prefix_fingerprint(_payload(tools=tools)) != (
        prompt_cache.prefix_fingerprint(_payload(tools=other_tools))
    )


def test_fingerprint_is_empty_without_any_stable_component() -> None:
    """空 payload：连「这段请求长什么样」都描述不了，不注入（随机 key 更糟）。"""
    assert prompt_cache.prefix_fingerprint({}) == ""
    assert prompt_cache.prefix_fingerprint(
        {"messages": [{"role": "user", "content": "只有尾部"}]}
    ) == ""


def test_empty_fingerprint_means_no_injection(monkeypatch) -> None:
    _configure(monkeypatch, mode="auto")
    payload = {}

    body, cache_key = prompt_cache.request_body(payload, OPENAI)

    assert body is payload
    assert cache_key is None


def test_fingerprint_is_16_hex_chars() -> None:
    fingerprint = prompt_cache.prefix_fingerprint(_payload())

    assert len(fingerprint) == 16
    assert all(ch in "0123456789abcdef" for ch in fingerprint)


# ---------------------------------------------------------------------------
# explicit：额外打 cache_control 断点
# ---------------------------------------------------------------------------


def test_explicit_marks_the_system_string_as_one_text_block(monkeypatch) -> None:
    _configure(monkeypatch, mode="explicit", breakpoint_hosts=["openrouter.ai"])

    body, _ = prompt_cache.request_body(_payload(), OPENROUTER)

    assert body["messages"][0]["content"] == [
        {
            "type": "text",
            "text": "你是助教。",
            "cache_control": {"type": "ephemeral"},
        }
    ]


def test_explicit_marks_only_the_last_text_block_of_a_list(monkeypatch) -> None:
    _configure(monkeypatch, mode="explicit", breakpoint_hosts=["openrouter.ai"])
    payload = _payload(
        messages=[
            {
                "role": "system",
                "content": [
                    {"type": "text", "text": "片段一"},
                    {"type": "text", "text": "片段二"},
                ],
            },
            {"role": "user", "content": "问"},
        ]
    )

    body, _ = prompt_cache.request_body(payload, OPENROUTER)

    content = body["messages"][0]["content"]
    assert "cache_control" not in content[0]
    assert content[1]["cache_control"] == {"type": "ephemeral"}
    assert content[1]["text"] == "片段二"


def test_explicit_marks_the_last_tool(monkeypatch) -> None:
    _configure(monkeypatch, mode="explicit", breakpoint_hosts=["openrouter.ai"])
    payload = _payload(
        tools=[
            {"type": "function", "function": {"name": "a"}},
            {"type": "function", "function": {"name": "b"}},
        ]
    )

    body, _ = prompt_cache.request_body(payload, OPENROUTER)

    assert "cache_control" not in body["tools"][0]
    assert body["tools"][1]["cache_control"] == {"type": "ephemeral"}


def test_explicit_never_mutates_the_input_payload(monkeypatch) -> None:
    """调用方还要用原 payload 算字符数、记审计：任何就地修改都会污染审计。"""
    _configure(monkeypatch, mode="explicit", breakpoint_hosts=["openrouter.ai"])
    payload = _payload(
        tools=[{"type": "function", "function": {"name": "a"}}],
    )
    snapshot = copy.deepcopy(payload)

    prompt_cache.request_body(payload, OPENROUTER)

    assert payload == snapshot


def test_explicit_only_applies_to_whitelisted_breakpoint_hosts(monkeypatch) -> None:
    """不在断点白名单的 host：不加任何 cache_control（等价于 auto）。"""
    _configure(monkeypatch, mode="explicit", breakpoint_hosts=["openrouter.ai"])

    body, _ = prompt_cache.request_body(_payload(), DEEPSEEK)

    assert "cache_control" not in str(body)


def test_explicit_does_not_duplicate_an_existing_breakpoint(monkeypatch) -> None:
    """调用方比我们更清楚该断在哪：已有 cache_control 的块不动。"""
    _configure(monkeypatch, mode="explicit", breakpoint_hosts=["openrouter.ai"])
    marked = {"type": "text", "text": "已标记", "cache_control": {"type": "ephemeral"}}
    payload = _payload(
        messages=[
            {"role": "system", "content": [marked]},
            {"role": "user", "content": "问"},
        ]
    )

    body, _ = prompt_cache.request_body(payload, OPENROUTER)

    assert body["messages"][0]["content"] == [marked]


def test_explicit_skips_an_empty_system_content(monkeypatch) -> None:
    """空串换成 text 块会被「text 必须非空」的实现拒绝，而它本来也没内容可缓存。"""
    _configure(monkeypatch, mode="explicit", breakpoint_hosts=["openrouter.ai"])
    payload = _payload(
        messages=[
            {"role": "system", "content": ""},
            {"role": "user", "content": "问"},
        ]
    )

    body, _ = prompt_cache.request_body(payload, OPENROUTER)

    assert body["messages"][0]["content"] == ""


def test_explicit_without_a_system_message_is_a_noop(monkeypatch) -> None:
    _configure(monkeypatch, mode="explicit", breakpoint_hosts=["openrouter.ai"])
    payload = {"model": "gpt-x", "messages": [{"role": "user", "content": "问"}]}

    body, _ = prompt_cache.request_body(payload, OPENROUTER)

    assert body == payload
