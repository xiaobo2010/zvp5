"""SSE 解析、进程内 hub 的有界队列、事件流的首帧/终态/心跳。

这里不碰真实网络：provider 的字节流与 run 详情都是桩，测的是「帧契约」而不是
上游实现。契约正文见 ``zvp5/docs/AI_CONVERSATION_DESIGN.md`` §13。
"""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routers import ai_conversations_router
from services.agent import live, run_queue
from services.agent.tool_impl import AgentError
from services.ai import providers
from util.object_id import get_current_user


async def _chunks(*parts: bytes):
    for part in parts:
        yield part


def _payload(frame: bytes) -> dict:
    text = frame.decode("utf-8").strip()
    assert text.startswith("data: ")
    return json.loads(text[len("data: ") :])


# ---------------------------------------------------------------------------
# 上游 SSE → JSON chunk
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sse_parser_survives_frame_splits_comments_and_done(monkeypatch) -> None:
    async def _stream(_payload, _entry, _key):
        async for chunk in _chunks(
            b'data: {"choices":[{"delta":{"content":"he',
            b'llo"}}]}\n\n: keep-alive\n\n',
            b'data: {"choices":[{"delta":{"content":"!"}}]}\n\n',
            b"data: [DONE]\n\n",
        ):
            yield chunk

    monkeypatch.setattr(providers, "chat_completion_stream", _stream)

    seen = [
        chunk
        async for chunk in providers.chat_completion_stream_json({}, {}, "k")
    ]
    text = "".join(
        chunk["choices"][0]["delta"].get("content", "") for chunk in seen
    )
    assert text == "hello!"


@pytest.mark.asyncio
async def test_non_sse_200_is_surfaced_as_full_payload(monkeypatch) -> None:
    async def _stream(_payload, _entry, _key):
        async for chunk in _chunks(
            b'{"choices":[{"message":{"content":"blocking"}}]}'
        ):
            yield chunk

    monkeypatch.setattr(providers, "chat_completion_stream", _stream)

    generator = providers.chat_completion_stream_json({}, {}, "k")
    with pytest.raises(providers.StreamNotSSE) as caught:
        async for _chunk in generator:
            pass
    assert caught.value.payload["choices"][0]["message"]["content"] == "blocking"


# ---------------------------------------------------------------------------
# 进程内 hub
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _fresh_live():
    live.reset()
    yield
    live.reset()


@pytest.mark.asyncio
async def test_assistant_frames_merge_to_latest_cumulative_text() -> None:
    subscription = live.subscribe("run-1", max_pending=4)
    live.publish(
        "run-1", {"type": "assistant", "round_no": 1, "stream_key": "run-1:1", "content": "a"}
    )
    live.publish(
        "run-1",
        {"type": "tool", "tool_call_id": "call_1", "status": "pending", "tool_name": "x"},
    )
    live.publish(
        "run-1", {"type": "assistant", "round_no": 1, "stream_key": "run-1:1", "content": "ab"}
    )

    first = await subscription.get(0.01)
    second = await subscription.get(0.01)
    assert first["type"] == "tool"
    assert second["type"] == "assistant"
    assert second["content"] == "ab"
    assert [first["seq"], second["seq"]] == sorted([first["seq"], second["seq"]])
    assert await subscription.get(0.01) is None


@pytest.mark.asyncio
async def test_overflow_clears_stale_frames_and_resyncs_before_newer_frames() -> None:
    subscription = live.subscribe("run-1", max_pending=1)
    live.publish(
        "run-1",
        {"type": "tool", "tool_call_id": "call_1", "status": "pending", "tool_name": "x"},
    )
    live.publish(
        "run-1",
        {"type": "tool", "tool_call_id": "call_2", "status": "pending", "tool_name": "y"},
    )

    first = await subscription.get(0.01)
    assert first["type"] == "resync"
    assert await subscription.get(0.01) is None
    state = live.live_state("run-1")
    assert "call_2" in [item["tool_call_id"] for item in state["tools"]]

    live.publish(
        "run-1",
        {"type": "tool", "tool_call_id": "call_3", "status": "success", "tool_name": "z"},
    )
    newer = await subscription.get(0.01)
    assert newer["type"] == "tool"
    assert newer["seq"] > first["seq"]


def test_unsubscribe_closes_the_queue() -> None:
    subscription = live.subscribe("run-1")
    live.unsubscribe(subscription)
    assert not live.has_subscribers("run-1")
    live.publish("run-1", {"type": "assistant", "stream_key": "run-1:1", "content": "x"})


# ---------------------------------------------------------------------------
# 事件流：首帧 snapshot / 终态顺序 / 心跳
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_first_frame_is_a_full_snapshot_and_terminal_closes_the_stream(
    monkeypatch,
) -> None:
    initial = {"run": {"status": "running"}, "events": [], "proposals": []}
    final = {
        "run": {
            "status": "succeeded",
            "stop_reason": "completed",
            "error_code": None,
        },
        "events": [],
        "proposals": [],
    }

    async def _detail(**_kwargs):
        return final

    monkeypatch.setattr(run_queue, "run_detail", _detail)

    subscription = live.subscribe("run-1")
    snapshot_seq = live.current_seq("run-1")
    frames = live.event_frames(
        subscription,
        run_id="run-1",
        initial=initial,
        snapshot_seq=snapshot_seq,
        user={"id": "u"},
        heartbeat=0.01,
    )

    first = _payload(await anext(frames))
    assert first["type"] == "snapshot"
    assert first["data"] == initial
    assert first["seq"] == snapshot_seq
    assert "live" in first

    live.publish_resync("run-1")
    snapshot = _payload(await anext(frames))
    assert snapshot["type"] == "snapshot"
    assert snapshot["data"] == final

    terminal = _payload(await anext(frames))
    assert terminal["type"] == "terminal"
    assert terminal["status"] == "succeeded"
    assert terminal["seq"] > snapshot["seq"]

    assert await anext(frames) == live.DONE_FRAME

    with pytest.raises(StopAsyncIteration):
        await anext(frames)


@pytest.mark.asyncio
async def test_idle_stream_emits_sse_comments_as_heartbeat() -> None:
    subscription = live.subscribe("run-1")
    frames = live.event_frames(
        subscription,
        run_id="run-1",
        initial={"run": {"status": "running"}},
        snapshot_seq=live.current_seq("run-1"),
        user={"id": "u"},
        heartbeat=0.001,
    )
    await anext(frames)  # snapshot
    assert await anext(frames) == live.HEARTBEAT_FRAME


def test_multi_worker_flag_reads_common_env(monkeypatch) -> None:
    monkeypatch.delenv("AI_RUN_EVENTS_MULTI_WORKER", raising=False)
    monkeypatch.setenv("WEB_CONCURRENCY", "2")
    assert live.multi_worker() is True
    monkeypatch.setenv("WEB_CONCURRENCY", "1")
    assert live.multi_worker() is False


# ---------------------------------------------------------------------------
# 路由：单 worker 限制与归属校验仍走普通 JSON 错误
# ---------------------------------------------------------------------------


def _client(user: dict | None = None) -> TestClient:
    app = FastAPI()
    app.include_router(ai_conversations_router.router, prefix="/api/ai")
    app.dependency_overrides[get_current_user] = lambda: user or {
        "id": "0" * 24,
        "per": ["admin"],
    }
    return TestClient(app)


def test_events_route_rejects_multi_worker_deployments(monkeypatch) -> None:
    monkeypatch.setattr(
        ai_conversations_router, "_require_agent_feature", lambda _user: None
    )
    monkeypatch.setattr(live, "multi_worker", lambda: True)

    response = _client().get("/api/ai/runs/" + "a" * 32 + "/events")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "AI_RUN_EVENTS_UNSUPPORTED"


def test_events_route_keeps_ownership_404_as_json(monkeypatch) -> None:
    monkeypatch.setattr(
        ai_conversations_router, "_require_agent_feature", lambda _user: None
    )
    monkeypatch.setattr(live, "multi_worker", lambda: False)

    async def _missing(**_kwargs):
        raise AgentError("AI_RUN_NOT_FOUND", "run not found", 404)

    monkeypatch.setattr(run_queue, "run_detail", _missing)

    response = _client().get("/api/ai/runs/" + "a" * 32 + "/events")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "AI_RUN_NOT_FOUND"


@pytest.mark.asyncio
async def test_reconnect_snapshot_carries_current_live_text() -> None:
    """断线重连：首帧 snapshot 直接带当前累计文本，无需回放增量帧。"""
    live.publish(
        "run-1",
        {
            "type": "assistant",
            "round_no": 1,
            "stream_key": "run-1:1",
            "content": "已经写了一半",
        },
    )
    subscription = live.subscribe("run-1")
    frames = live.event_frames(
        subscription,
        run_id="run-1",
        initial={"run": {"status": "running"}},
        snapshot_seq=live.current_seq("run-1"),
        user={"id": "u"},
        heartbeat=0.01,
    )

    first = _payload(await anext(frames))
    assert first["type"] == "snapshot"
    assistant = first["live"]["assistant"]
    assert assistant and assistant[0]["content"] == "已经写了一半"


@pytest.mark.asyncio
async def test_sse_parser_handles_multibyte_split_across_chunks(monkeypatch) -> None:
    frame = 'data: {"choices":[{"delta":{"content":"中文"}}]}\n\n'.encode("utf-8")
    cut = frame.index("中".encode("utf-8")) + 1  # 故意切在多字节字符中间

    async def _stream(_payload, _entry, _key):
        async for chunk in _chunks(frame[:cut], frame[cut:]):
            yield chunk

    monkeypatch.setattr(providers, "chat_completion_stream", _stream)

    seen = [
        chunk
        async for chunk in providers.chat_completion_stream_json({}, {}, "k")
    ]
    assert seen[0]["choices"][0]["delta"]["content"] == "中文"


@pytest.mark.parametrize("prefix", [b"d", b"da", b"dat", b"data", b"data:"])
@pytest.mark.asyncio
async def test_sse_prefix_split_is_not_mistaken_for_json(monkeypatch, prefix) -> None:
    full = b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\n'
    rest = full[len(prefix):]

    async def _stream(_payload, _entry, _key):
        async for chunk in _chunks(prefix, rest):
            yield chunk

    monkeypatch.setattr(providers, "chat_completion_stream", _stream)

    seen = [
        chunk
        async for chunk in providers.chat_completion_stream_json({}, {}, "k")
    ]
    assert seen[0]["choices"][0]["delta"]["content"] == "ok"


@pytest.mark.asyncio
async def test_trailing_malformed_frame_fails_loud(monkeypatch) -> None:
    async def _stream(_payload, _entry, _key):
        async for chunk in _chunks(b'data: {"choices": ['):
            yield chunk

    monkeypatch.setattr(providers, "chat_completion_stream", _stream)

    generator = providers.chat_completion_stream_json({}, {}, "k")
    with pytest.raises(providers.RouteError) as caught:
        async for _chunk in generator:
            pass
    assert caught.value.code == "AI_UPSTREAM_ERROR"


@pytest.mark.asyncio
async def test_event_frames_seq_is_strictly_increasing_for_lastseq_dedupe(
    monkeypatch,
) -> None:
    subscription = live.subscribe("run-1")
    frames = live.event_frames(
        subscription,
        run_id="run-1",
        initial={"run": {"status": "running"}},
        snapshot_seq=live.current_seq("run-1"),
        user={"id": "u"},
        heartbeat=0.01,
    )

    snapshot = _payload(await anext(frames))
    live.publish(
        "run-1",
        {"type": "tool", "tool_call_id": "call_1", "status": "pending", "tool_name": "x"},
    )
    live.publish(
        "run-1",
        {"type": "assistant", "round_no": 1, "stream_key": "run-1:1", "content": "a"},
    )
    live.publish(
        "run-1",
        {"type": "assistant", "round_no": 1, "stream_key": "run-1:1", "content": "ab"},
    )

    tool_frame = _payload(await anext(frames))
    assistant_frame = _payload(await anext(frames))
    seqs = [snapshot["seq"], tool_frame["seq"], assistant_frame["seq"]]
    assert seqs == sorted(seqs)
    assert len(set(seqs)) == 3


@pytest.mark.asyncio
async def test_reconnect_snapshot_seq_is_newer_than_previous_frames() -> None:
    live.publish(
        "run-1",
        {"type": "tool", "tool_call_id": "call_1", "status": "pending", "tool_name": "x"},
    )
    previous = live.current_seq("run-1")
    snapshot_seq = live.next_seq("run-1")
    subscription = live.subscribe("run-1")
    frames = live.event_frames(
        subscription,
        run_id="run-1",
        initial={"run": {"status": "running"}},
        snapshot_seq=snapshot_seq,
        user={"id": "u"},
        heartbeat=0.01,
    )

    first = _payload(await anext(frames))
    assert first["seq"] > previous
    live.publish(
        "run-1",
        {"type": "tool", "tool_call_id": "call_2", "status": "success", "tool_name": "y"},
    )
    next_frame = _payload(await anext(frames))
    assert next_frame["seq"] > first["seq"]
