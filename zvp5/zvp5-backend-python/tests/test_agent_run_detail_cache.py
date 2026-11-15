"""run 详情轮询缓存（``services/agent/run_cache.py`` + ``run_detail``）。

守三件事：

* **1 秒内重复轮询不回源**：run 行与事件各只查一次，``after_round`` 在内存里切；
* **消息增量不缓存**：``since_msg_seq`` 每拍都查（它是唯一必须实时的部分）；
* **失效点可靠**：``invalidate`` 之后立刻回源，终态快照活 30 秒。
"""

from __future__ import annotations

from datetime import datetime

import pytest

from db import backends
from services.agent import run_cache, run_queue

OWNER = "0" * 24
OTHER = "1" * 24
ADMIN = {"id": OWNER, "per": ["admin"]}
CONV = "a" * 32
RUN = "b" * 32

TS = datetime(2026, 11, 4, 3, 0, 0)


def _run_row(run_id: str, *, owner_oid: str = OWNER, status: str = "running") -> dict:
    return {
        "_id": run_id,
        "conversation_oid": CONV,
        "owner_oid": owner_oid,
        "client_msg_id": None,
        "status": status,
        "model": "gpt-x",
        "stop_reason": None,
        "rounds": 0,
        "error_code": None,
        "error_message": None,
        "result_message_oid": None,
        "prompt_tokens": None,
        "completion_tokens": None,
        "total_tokens": None,
        "cancel_requested": False,
        "heartbeat_at": None,
        "created_at": TS,
        "started_at": TS,
        "finished_at": None,
    }


def _event_row(
    oid: str,
    *,
    request_id: str = RUN,
    round_no: int,
    tool_kind: str = "read",
    tool_name: str = "fs_read",
    status: str = "executed",
    actor_oid: str = OWNER,
) -> dict:
    return {
        "_id": oid,
        "request_id": request_id,
        "round_no": round_no,
        "tool_name": tool_name,
        "tool_kind": tool_kind,
        "args_hash": "h",
        "args_summary": "{}",
        "args_payload": '{"full": "payload"}',
        "preview": None,
        "result_summary": "ok",
        "status": status,
        "actor_oid": actor_oid,
        "approver_oid": None,
        "confirmed_at": None,
        "created_at": TS,
    }


class FakeRunsRepo:
    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}
        self.get_calls: list[tuple[str, str]] = []

    def seed(self, run_id: str = RUN, **kwargs) -> dict:
        row = _run_row(run_id, **kwargs)
        self.rows[run_id] = row
        return row

    async def get(self, run_id: str, owner_oid: str) -> dict | None:
        self.get_calls.append((str(run_id), str(owner_oid)))
        row = self.rows.get(str(run_id))
        if row is None or str(row["owner_oid"]) != str(owner_oid):
            return None
        return dict(row)


class FakeEventsRepo:
    """只实现轮询用到的两个读口，并分别计数。"""

    def __init__(self) -> None:
        self.rows: list[dict] = []
        self.display_calls = 0
        self.full_calls = 0

    def seed(self, oid: str, **kwargs) -> dict:
        row = _event_row(oid, **kwargs)
        self.rows.append(row)
        return row

    async def list_display_for_request(
        self, request_id: str, *, after_round: int = 0
    ) -> list[dict]:
        self.display_calls += 1
        floor = int(after_round or 0)
        return [
            dict(row)
            for row in self.rows
            if str(row["request_id"]) == str(request_id)
            and (floor <= 0 or int(row["round_no"] or 0) > floor)
        ]

    async def list_for_request(
        self, request_id: str, *, after_round: int = 0
    ) -> list[dict]:
        self.full_calls += 1
        rows = await self.list_display_for_request(
            request_id, after_round=after_round
        )
        for row in rows:
            row["args_payload"] = '{"full": "payload"}'
            row["args_hash"] = "h"
        return rows


@pytest.fixture
def env(monkeypatch) -> dict:
    runs = FakeRunsRepo()
    events = FakeEventsRepo()
    message_calls: list[tuple[str, int]] = []

    async def fake_messages_since(oid, after_seq, *, limit):
        message_calls.append((str(oid), int(after_seq)))
        return []

    monkeypatch.setattr(backends, "get_ai_runs_repo", lambda session=None: runs)
    monkeypatch.setattr(backends, "get_ai_agent_events_repo", lambda session=None: events)
    monkeypatch.setattr(run_queue.conversations, "messages_since", fake_messages_since)
    run_cache.reset()
    yield {"runs": runs, "events": events, "message_calls": message_calls}
    run_cache.reset()


# ---------------------------------------------------------------------------
# ① 同一秒内两次轮询只回源一次
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_two_polls_within_a_second_hit_the_db_once(env) -> None:
    env["runs"].seed(status="running")
    env["events"].seed("e1", round_no=1)
    env["events"].seed("e2", round_no=1)

    first = await run_queue.run_detail(user=ADMIN, run_id=RUN)
    second = await run_queue.run_detail(user=ADMIN, run_id=RUN)

    assert env["runs"].get_calls == [(RUN, OWNER)]
    assert env["events"].display_calls == 1
    assert env["events"].full_calls == 0
    assert [event["oid"] for event in first["events"]] == ["e1", "e2"]
    assert second == first
    # 缓存命中也要算进统计：不然「到底省了多少」只能靠猜。
    assert run_cache.stats()["hits"] == 1


# ---------------------------------------------------------------------------
# ② after_round 在内存里切片
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_after_round_is_sliced_in_memory_not_in_sql(env) -> None:
    env["runs"].seed(status="running")
    env["events"].seed("e1", round_no=1)
    env["events"].seed("e2", round_no=2)
    env["events"].seed("e3", round_no=3)

    full = await run_queue.run_detail(user=ADMIN, run_id=RUN)
    assert [event["round_no"] for event in full["events"]] == [1, 2, 3]

    delta = await run_queue.run_detail(user=ADMIN, run_id=RUN, after_round=2)

    assert [event["oid"] for event in delta["events"]] == ["e3"]
    # 切片没有触发新查询：轮询增量不该再打一次事件表。
    assert env["events"].display_calls == 1
    assert env["runs"].get_calls == [(RUN, OWNER)]
    # 提案不随 after_round 变（轮询增量只管 events）。
    assert delta["proposals"] == full["proposals"]


# ---------------------------------------------------------------------------
# ③ 消息增量每次仍然查库
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_messages_are_still_queried_on_every_poll(env) -> None:
    env["runs"].seed(status="running")

    await run_queue.run_detail(user=ADMIN, run_id=RUN, since_msg_seq=0)
    await run_queue.run_detail(user=ADMIN, run_id=RUN, since_msg_seq=3)

    assert env["message_calls"] == [(CONV, 0), (CONV, 3)]
    assert env["runs"].get_calls == [(RUN, OWNER)]


# ---------------------------------------------------------------------------
# ④ invalidate 之后回源
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_invalidate_forces_a_refetch(env) -> None:
    env["runs"].seed(status="running")
    env["events"].seed("e1", round_no=1)

    await run_queue.run_detail(user=ADMIN, run_id=RUN)
    run_cache.invalidate(RUN)
    await run_queue.run_detail(user=ADMIN, run_id=RUN)

    assert env["runs"].get_calls == [(RUN, OWNER), (RUN, OWNER)]
    assert env["events"].display_calls == 2


# ---------------------------------------------------------------------------
# ⑤ 终态缓存 30 秒（活跃 1 秒）
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_terminal_snapshot_lives_30_seconds(env, monkeypatch) -> None:
    clock = {"now": 1_000.0}
    monkeypatch.setattr(run_cache, "_now", lambda: clock["now"])
    env["runs"].seed(status="succeeded")

    await run_queue.run_detail(user=ADMIN, run_id=RUN)
    clock["now"] += 5.0
    await run_queue.run_detail(user=ADMIN, run_id=RUN)
    assert env["runs"].get_calls == [(RUN, OWNER)]

    clock["now"] += 26.0  # 累计 31s > 30s
    await run_queue.run_detail(user=ADMIN, run_id=RUN)
    assert env["runs"].get_calls == [(RUN, OWNER), (RUN, OWNER)]


@pytest.mark.asyncio
async def test_active_snapshot_expires_after_one_second(env, monkeypatch) -> None:
    clock = {"now": 2_000.0}
    monkeypatch.setattr(run_cache, "_now", lambda: clock["now"])
    env["runs"].seed(status="running")

    await run_queue.run_detail(user=ADMIN, run_id=RUN)
    clock["now"] += 1.1
    await run_queue.run_detail(user=ADMIN, run_id=RUN)

    assert env["runs"].get_calls == [(RUN, OWNER), (RUN, OWNER)]


# ---------------------------------------------------------------------------
# 缓存键只有 run_id：归属检查不能因为命中而被跳过
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cache_hit_does_not_bypass_the_owner_check(env) -> None:
    env["runs"].seed(status="running")
    await run_queue.run_detail(user=ADMIN, run_id=RUN)

    with pytest.raises(run_queue.AgentError) as caught:
        await run_queue.run_detail(user={"id": OTHER, "per": []}, run_id=RUN)

    assert caught.value.code == run_queue.RUN_NOT_FOUND


# ---------------------------------------------------------------------------
# append_event：executor 落库后把同形状事件追加进已缓存的时间线
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_appended_event_is_visible_without_a_refetch(env) -> None:
    env["runs"].seed(status="running")
    env["events"].seed("e1", round_no=1)
    await run_queue.run_detail(user=ADMIN, run_id=RUN)

    run_cache.append_event(
        RUN,
        {
            "oid": "e2",
            "round_no": 2,
            "tool_name": "fs_read",
            "tool_kind": "read",
            "args_summary": "{}",
            "result_summary": "ok",
            "status": "executed",
            "created_at": "2026-11-04 03:00:01",
        },
    )
    delta = await run_queue.run_detail(user=ADMIN, run_id=RUN, after_round=1)

    assert [event["oid"] for event in delta["events"]] == ["e2"]
    assert env["events"].display_calls == 1


@pytest.mark.asyncio
async def test_append_event_normalises_to_the_event_view_shape(env) -> None:
    """executor 的 payload 少一两列时，缓存里仍是一条完整的事件视图。"""
    env["runs"].seed(status="running")
    await run_queue.run_detail(user=ADMIN, run_id=RUN)

    run_cache.append_event(
        RUN,
        {
            "oid": "e9",
            "round_no": 3,
            "tool_name": "fs_read",
            "result_summary": "ok",
        },
    )
    delta = await run_queue.run_detail(user=ADMIN, run_id=RUN, after_round=2)

    assert delta["events"] == [
        {
            "oid": "e9",
            "round_no": 3,
            "tool_name": "fs_read",
            "tool_kind": None,
            "args_summary": None,
            "result_summary": "ok",
            "status": None,
            "created_at": None,
        }
    ]


@pytest.mark.asyncio
async def test_append_event_on_uncached_run_is_a_noop() -> None:
    run_cache.reset()
    run_cache.append_event("nope", {"oid": "x", "round_no": 1})
    assert run_cache.get("nope") is None


# ---------------------------------------------------------------------------
# 读侧不复制：命中返回条目内引用；淘汰是 LRU 而非 FIFO
# ---------------------------------------------------------------------------

def test_cache_hit_returns_the_same_objects_not_copies() -> None:
    """深拷贝一整条 payload 是命中路径最贵的一步，且没有任何用处。

    快照是「只读引用」：``run_detail`` 只切片与序列化，从不原地改。
    """
    run_cache.reset()
    run_cache.put(
        RUN,
        row=_run_row(RUN),
        events=[_event_row("e1", round_no=1)],
        proposals=[{"_id": "p1"}],
    )

    first = run_cache.get(RUN)
    second = run_cache.get(RUN)

    assert first is not None and second is not None
    assert first["row"] is second["row"]
    assert first["events"] is second["events"]
    assert first["events"][0] is second["events"][0]
    assert first["proposals"] is second["proposals"]


def test_eviction_is_lru_not_fifo(monkeypatch) -> None:
    """命中把 key 挪到队尾：最久**没用过**的才被淘汰，而不是最早写进来的。"""
    run_cache.reset()
    monkeypatch.setattr(run_cache, "MAX_ENTRIES", 2)

    run_cache.put("A", row={"status": "running"}, events=[], proposals=[])
    run_cache.put("B", row={"status": "running"}, events=[], proposals=[])
    assert run_cache.get("A") is not None  # A 变成「刚用过」

    run_cache.put("C", row={"status": "running"}, events=[], proposals=[])

    assert run_cache.get("A") is not None
    assert run_cache.get("B") is None
    assert run_cache.get("C") is not None
