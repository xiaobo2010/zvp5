"""工具结果的 token 优化：head+tail 截断、重复只读引用、历史回放压缩。

全部是纯函数级断言：这里衡量的是「送进 prompt 的字符数确实下降」，不声称线上
token 百分比——token 取决于模型分词器，字符数是确定性的下界指标。
"""

from __future__ import annotations

import json

from services.agent import executor


def _tool_message(content: str, call_id: str = "call_1") -> dict:
    return {"role": "tool", "tool_call_id": call_id, "content": content}


def test_truncate_keeps_head_and_tail_with_digest_and_original_length() -> None:
    text = "HEAD-" + ("x" * 20000) + "-TAIL"
    out = executor._truncate(text, 1000)

    assert len(out) < len(text)
    assert out.startswith("HEAD-")
    assert out.endswith("-TAIL")
    assert f"original_chars={len(text)}" in out
    assert f"sha256={executor._digest(text)}" in out
    assert "truncated" in out


def test_truncate_never_exceeds_limit_even_for_tiny_limits() -> None:
    text = "HEAD-" + ("x" * 1000000) + "-TAIL"
    for limit in (0, 1, 20, 80, 120, 1000, 1200, 8000):
        assert len(executor._truncate(text, limit)) <= limit


def test_short_error_payload_is_not_truncated() -> None:
    payload = {"error": "permission_denied", "tool": "user_ban", "message": "没有权限"}
    message = executor._tool_message("call_e", payload)

    assert json.loads(message["content"]) == payload


def test_duplicate_read_only_result_becomes_a_compact_ref() -> None:
    content = json.dumps({"items": ["a"] * 500})
    cache: dict = {}
    first_conversation: list[dict] = []
    first_tools: list[dict] = []
    call = {
        "id": "call_1",
        "type": "function",
        "function": {"name": "search_activities", "arguments": '{"query": "a"}'},
    }
    executor._append_tool_outcome(
        call,
        (_tool_message(content, "call_1"), None, "executed", "ok"),
        conversation=first_conversation,
        handled_tools=first_tools,
        events=[],
        proposals=[],
        round_no=1,
        read_cache=cache,
    )

    second_conversation: list[dict] = []
    second_tools: list[dict] = []
    second_call = {
        "id": "call_2",
        "type": "function",
        "function": {"name": "search_activities", "arguments": '{"query": "a"}'},
    }
    executor._append_tool_outcome(
        second_call,
        (_tool_message(content, "call_2"), None, "executed", "ok"),
        conversation=second_conversation,
        handled_tools=second_tools,
        events=[],
        proposals=[],
        round_no=2,
        read_cache=cache,
    )

    assert first_conversation[0]["content"] == content
    compact = json.loads(second_conversation[0]["content"])
    assert compact[executor.DUPLICATE_REF_KEY] == executor._digest(content)
    assert compact["repeat_of"] == "call_1"
    assert len(second_conversation[0]["content"]) < len(content) // 10


def test_write_invalidates_duplicate_cache() -> None:
    cache = {("search_activities", '{"query":"a"}'): {"digest": "x", "call_id": "call_1"}}
    write_call = {
        "id": "call_w",
        "type": "function",
        "function": {"name": "user_ban", "arguments": "{}"},
    }
    executor._append_tool_outcome(
        write_call,
        (
            _tool_message(json.dumps({"error": "permission_denied"}), "call_w"),
            None,
            "denied",
            "denied",
        ),
        conversation=[],
        handled_tools=[],
        events=[],
        proposals=[],
        round_no=1,
        read_cache=cache,
    )

    assert cache == {}


def test_history_compaction_only_touches_older_tool_rounds() -> None:
    def round_messages(round_no: int, body: str) -> list[dict]:
        return [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": f"call_{round_no}",
                        "type": "function",
                        "function": {"name": "search_activities", "arguments": "{}"},
                    }
                ],
            },
            _tool_message(body, f"call_{round_no}"),
        ]

    long_body = "B" * 5000
    conversation = [{"role": "system", "content": "sys"}]
    conversation += round_messages(1, long_body)
    conversation += round_messages(2, long_body)
    conversation += round_messages(3, long_body)

    wired = executor._wire(conversation)
    tools = [item for item in wired if item.get("role") == "tool"]

    assert len(tools[0]["content"]) < len(long_body)
    assert "original_chars=5000" in tools[0]["content"]
    # 最新两轮保持原文（模型正在用）。
    assert tools[1]["content"] == long_body
    assert tools[2]["content"] == long_body

    before = sum(len(item.get("content") or "") for item in conversation)
    after = sum(len(str(item.get("content") or "")) for item in wired)
    assert after < before
