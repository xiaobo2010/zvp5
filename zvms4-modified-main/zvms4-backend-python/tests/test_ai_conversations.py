"""会话业务层（``services/ai/conversations.py``）：视图 / 归属 / 游标 / 删除门禁。

这一层最容易出事的不是 CRUD 本身，而是四件「漏了就静默」的事：

* 列表把正文（``content`` / ``summary``）一起投影出来——翻页突然读几 MB，且响应
  里出现本不该给列表看的正文；
* 忘了 ``owner_oid``——跨用户读别人的对话；
* 游标解不开时静默从头翻页——第一页重复给用户；
* 删会话时只删了本体——``proposed`` 提案还留着，30 分钟内仍能被 ``confirm`` 执行。

全部离线：仓储是内存桩，``seq`` 分配与批删语义按真实仓储的行为实现。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from db import backends
from services.agent.tool_impl import AgentError
from services.ai import conversations

OWNER = "0" * 24
OTHER = "1" * 24
ADMIN = {"id": OWNER, "per": ["admin"]}


def _now(offset: float = 0.0) -> datetime:
    return datetime(2026, 10, 1, 12, 0, 0) + timedelta(seconds=offset)


# ---------------------------------------------------------------------------
# 内存仓储
# ---------------------------------------------------------------------------

class FakeConversationsRepo:
    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}
        self.calls: list[str] = []

    def seed(self, oid: str, *, owner_oid: str = OWNER, mode: str = "chat", title=None,
             updated_at: datetime | None = None, summary: str | None = None,
             summary_upto_seq: int = 0, message_count: int = 0, model: str | None = None,
             **extra) -> dict:
        moment = updated_at or _now()
        row = {
            "_id": oid,
            "owner_oid": owner_oid,
            "mode": mode,
            "title": title,
            "model": model,
            "status": "active",
            "summary": summary,
            "summary_upto_seq": summary_upto_seq,
            "tokens_prompt": 0,
            "tokens_completion": 0,
            "tokens_total": 0,
            "context_tokens": 0,
            "message_count": message_count,
            "compaction_degraded_at": None,
            "created_at": moment,
            "updated_at": moment,
        }
        row.update(extra)
        self.rows[oid] = row
        return row

    async def create(self, *, oid, owner_oid, mode, title=None, model=None) -> dict:
        return dict(self.seed(oid, owner_oid=owner_oid, mode=mode, title=title, model=model))

    async def get(self, oid: str, owner_oid: str) -> dict | None:
        row = self.rows.get(str(oid))
        if row is None or str(row["owner_oid"]) != str(owner_oid):
            return None
        return dict(row)

    async def list_page(self, owner_oid: str, *, status=None, cursor=None, limit=50):
        items = [row for row in self.rows.values() if str(row["owner_oid"]) == str(owner_oid)]
        if status:
            items = [row for row in items if row["status"] == status]
        if cursor is not None:
            stamp, oid = cursor
            items = [
                row
                for row in items
                if (row["updated_at"], row["_id"]) < (stamp, oid)
            ]
        items.sort(key=lambda row: (row["updated_at"], row["_id"]), reverse=True)
        page = [dict(row) for row in items[:limit]]
        # 列表投影没有 summary：桩也照这个口径回，测试才有意义。
        for row in page:
            row.pop("summary", None)
        if len(page) < limit:
            return page, None
        return page, (page[-1]["updated_at"], page[-1]["_id"])

    async def update(self, oid, owner_oid, *, title=None, status=None, model=None,
                     clear_model=False) -> bool:
        row = self.rows.get(str(oid))
        if row is None or str(row["owner_oid"]) != str(owner_oid):
            return False
        if title is not None:
            row["title"] = title
        if status is not None:
            row["status"] = status
        if model is not None:
            row["model"] = model
        if clear_model:
            row["model"] = None
        row["updated_at"] = _now(999)
        return True

    async def delete_batched(self, oid, owner_oid, *, batch_size=500) -> int:
        self.calls.append("delete_batched")
        row = self.rows.get(str(oid))
        if row is None or str(row["owner_oid"]) != str(owner_oid):
            return 0
        self.rows.pop(str(oid))
        return 1


class FakeMessagesRepo:
    def __init__(self) -> None:
        self.rows: list[dict] = []
        self.batches: list[dict] = []
        self.calls: list[str] = []

    def seed(self, conversation_oid: str, *, seq: int, role: str, content: str,
             **extra) -> dict:
        row = {
            "_id": uuid4().hex,
            "conversation_oid": conversation_oid,
            "seq": seq,
            "role": role,
            "content": content,
            "tool_calls": None,
            "tool_call_id": None,
            "tool_name": None,
            "round_no": None,
            "finish_reason": None,
            "request_id": None,
            "prompt_tokens": None,
            "completion_tokens": None,
            "total_tokens": None,
            "cache_read_tokens": None,
            "cache_write_tokens": None,
            "created_at": _now(),
        }
        row.update(extra)
        self.rows.append(row)
        return row

    async def append_batch(self, conversation_oid, rows, *, usage=None):
        self.calls.append("append_batch")
        self.batches.append({"conversation_oid": conversation_oid, "rows": [dict(r) for r in rows],
                             "usage": usage})
        base = max([row["seq"] for row in self.rows if row["conversation_oid"] == conversation_oid],
                   default=0)
        inserted = []
        for offset, item in enumerate(rows):
            row = dict(item)
            row["seq"] = base + offset + 1
            row["conversation_oid"] = conversation_oid
            row["created_at"] = _now()
            self.rows.append(row)
            inserted.append(row)
        return inserted

    async def list_after(self, conversation_oid, after_seq=0, *, limit=50):
        rows = [
            row
            for row in self.rows
            if row["conversation_oid"] == conversation_oid and row["seq"] > after_seq
        ]
        rows.sort(key=lambda row: row["seq"])
        return [dict(row) for row in rows[:limit]]

    async def count(self, conversation_oid: str) -> int:
        return len([row for row in self.rows if row["conversation_oid"] == conversation_oid])

    async def purge_by_conversation(self, conversation_oid, *, batch_size=500) -> int:
        self.calls.append("purge_messages")
        keep = [row for row in self.rows if row["conversation_oid"] != conversation_oid]
        removed = len(self.rows) - len(keep)
        self.rows = keep
        return removed


class FakeRunsRepo:
    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}
        self.calls: list[str] = []

    def seed(self, run_id: str, *, conversation_oid: str, owner_oid: str = OWNER,
             status: str = "queued", **extra) -> dict:
        row = {
            "_id": run_id,
            "conversation_oid": conversation_oid,
            "owner_oid": owner_oid,
            "client_msg_id": None,
            "status": status,
            "model": None,
            "stop_reason": None,
            "rounds": None,
            "error_code": None,
            "error_message": None,
            "result_message_oid": None,
            "prompt_tokens": None,
            "completion_tokens": None,
            "total_tokens": None,
            "cancel_requested": False,
            "heartbeat_at": None,
            "created_at": _now(),
            "started_at": None,
            "finished_at": None,
        }
        row.update(extra)
        self.rows[run_id] = row
        return row

    async def get(self, run_id: str, owner_oid: str) -> dict | None:
        row = self.rows.get(str(run_id))
        if row is None or str(row["owner_oid"]) != str(owner_oid):
            return None
        return dict(row)

    async def list_active(self, owner_oid: str) -> list[dict]:
        return [
            dict(row)
            for row in self.rows.values()
            if str(row["owner_oid"]) == str(owner_oid)
            and row["status"] in ("queued", "running")
        ]

    async def purge_by_conversation(self, conversation_oid, *, batch_size=500) -> int:
        self.calls.append("purge_runs")
        doomed = [rid for rid, row in self.rows.items() if row["conversation_oid"] == conversation_oid]
        for rid in doomed:
            self.rows.pop(rid)
        return len(doomed)

    async def interrupt_running(self, **_kwargs) -> int:
        return 0

    async def interrupt_stale(self, _cutoff, **_kwargs) -> int:
        return 0


class FakeEventsRepo:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    def seed(self, *, request_id: str, status: str = "proposed", actor_oid: str = OWNER,
             tool_kind: str = "write", tool_name: str = "fs_write",
             result_summary: str = "等待确认") -> dict:
        row = {
            "_id": uuid4().hex,
            "request_id": request_id,
            "round_no": 1,
            "tool_name": tool_name,
            "tool_kind": tool_kind,
            "args_hash": "h",
            "args_summary": "{}",
            "args_payload": "{}",
            "preview": "{}",
            "result_summary": result_summary,
            "status": status,
            "actor_oid": actor_oid,
            "approver_oid": None,
            "confirmed_at": None,
            "created_at": _now(),
        }
        self.rows.append(row)
        return row

    async def list_by_actor(self, actor_oid: str, *, status=None, limit=20) -> list[dict]:
        rows = [row for row in self.rows if str(row["actor_oid"]) == str(actor_oid)]
        if status:
            rows = [row for row in rows if row["status"] == status]
        return [dict(row) for row in rows[:limit]]

    async def update_status(self, oid, status, *, from_status="proposed",
                            result_summary=None, **extra) -> bool:
        for row in self.rows:
            if row["_id"] == oid and row["status"] == from_status:
                row["status"] = status
                if result_summary is not None:
                    row["result_summary"] = result_summary
                return True
        return False

    async def list_for_request(self, request_id: str) -> list[dict]:
        return [dict(row) for row in self.rows if str(row["request_id"]) == str(request_id)]


@pytest.fixture
def env(monkeypatch) -> SimpleNamespace:
    conv = FakeConversationsRepo()
    msg = FakeMessagesRepo()
    runs = FakeRunsRepo()
    events = FakeEventsRepo()
    monkeypatch.setattr(backends, "get_ai_conversations_repo", lambda session=None: conv)
    monkeypatch.setattr(backends, "get_ai_messages_repo", lambda session=None: msg)
    monkeypatch.setattr(backends, "get_ai_runs_repo", lambda session=None: runs)
    monkeypatch.setattr(backends, "get_ai_agent_events_repo", lambda session=None: events)
    return SimpleNamespace(conv=conv, msg=msg, runs=runs, events=events)


# ---------------------------------------------------------------------------
# 视图
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_returns_the_contract_view(env) -> None:
    view = await conversations.create(user=ADMIN, mode="agent", title="  排障  ")

    assert view["mode"] == "agent"
    assert view["title"] == "排障"
    assert view["status"] == "active"
    assert view["tokens"] == {"prompt": 0, "completion": 0, "total": 0}
    assert view["context"] == {"tokens": 0, "limit": conversations.CONTEXT_LIMIT_DEFAULT, "ratio": 0.0}
    assert view["compaction"] == {"upto_seq": 0, "summarized": False, "degraded_at": None}
    assert view["message_count"] == 0
    assert isinstance(view["created_at"], str)


@pytest.mark.asyncio
async def test_create_rejects_unknown_mode(env) -> None:
    with pytest.raises(AgentError) as caught:
        await conversations.create(user=ADMIN, mode="summary")

    assert caught.value.status_code == 400


@pytest.mark.asyncio
async def test_view_marks_compaction_and_clamps_ratio(env, monkeypatch) -> None:
    # 上限钉死成 1000：这个用例测的是「超限时 ratio 夹在 1.0」，不该随默认窗口
    # 变大（32768 → 200000）而失效。
    monkeypatch.setattr(conversations.settings, "AI_MODEL_CONTEXT_LIMITS", {}, raising=False)
    monkeypatch.setattr(conversations.settings, "AI_DEFAULT_CONTEXT_LIMIT", 1000, raising=False)
    row = env.conv.seed(
        "a" * 32,
        summary="旧内容摘要",
        summary_upto_seq=12,
        context_tokens=99_000,
        message_count=13,
        compaction_degraded_at=_now(),
    )

    view = conversations.conversation_view(row)

    assert view["compaction"]["upto_seq"] == 12
    assert view["compaction"]["summarized"] is True
    assert view["compaction"]["degraded_at"].startswith("2026-10-01")
    # 超限时 ratio 夹在 1.0：进度条不该撑破容器。
    assert view["context"]["limit"] == 1000
    assert view["context"]["ratio"] == 1.0
    assert view["message_count"] == 13


def test_title_from_collapses_whitespace_and_clips() -> None:
    assert conversations.title_from("  第一行\n第二行  ") == "第一行 第二行"
    assert len(conversations.title_from("长" * 400)) == conversations.TITLE_MAX
    assert conversations.title_from(None) == ""


def test_context_limit_reads_settings(monkeypatch) -> None:
    monkeypatch.setattr(conversations.settings, "AI_MODEL_CONTEXT_LIMITS", {"big": 128000}, raising=False)
    monkeypatch.setattr(conversations.settings, "AI_DEFAULT_CONTEXT_LIMIT", 16000, raising=False)

    assert conversations.context_limit("big") == 128000
    assert conversations.context_limit("unknown") == 16000
    assert conversations.context_limit(None) == 16000


# ---------------------------------------------------------------------------
# 列表与归属
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_is_owner_scoped_and_keyset_paginated(env) -> None:
    env.conv.seed("a" * 32, updated_at=_now(30))
    env.conv.seed("b" * 32, updated_at=_now(20))
    env.conv.seed("c" * 32, updated_at=_now(10))
    env.conv.seed("d" * 32, owner_oid=OTHER, updated_at=_now(99))

    first = await conversations.list_page(user=ADMIN, limit=2)

    assert [row["oid"] for row in first["conversations"]] == ["a" * 32, "b" * 32]
    assert first["next_cursor"] is not None

    second = await conversations.list_page(user=ADMIN, limit=2, cursor=first["next_cursor"])

    # 别人的会话一次都没出现；第二页只剩这一条，因此没有下一页游标。
    assert [row["oid"] for row in second["conversations"]] == ["c" * 32]
    assert second["next_cursor"] is None


@pytest.mark.asyncio
async def test_list_cursor_roundtrip_is_stable(env) -> None:
    moment = _now()
    cursor = conversations.encode_cursor(moment, "a" * 32)

    assert conversations.decode_cursor(cursor) == (moment, "a" * 32)


@pytest.mark.asyncio
async def test_list_does_not_carry_bodies(env) -> None:
    env.conv.seed("a" * 32, title="有正文的会话", summary="很长的摘要", message_count=3)
    env.msg.seed("a" * 32, seq=1, role="user", content="机密正文")

    page = await conversations.list_page(user=ADMIN)

    blob = repr(page)
    assert "很长的摘要" not in blob
    assert "机密正文" not in blob
    assert all("summary" not in row for row in page["conversations"])
    # 活跃 run 不在列表里（走 GET /runs?active=1）。
    assert all("active_run" not in row for row in page["conversations"])


@pytest.mark.asyncio
async def test_bad_cursor_is_rejected_not_restarted(env) -> None:
    with pytest.raises(AgentError) as caught:
        await conversations.list_page(user=ADMIN, cursor="not-a-cursor")

    assert caught.value.code == conversations.BAD_CURSOR
    assert caught.value.status_code == 400


@pytest.mark.asyncio
async def test_get_foreign_conversation_is_not_found(env) -> None:
    env.conv.seed("a" * 32, owner_oid=OTHER)

    with pytest.raises(AgentError) as caught:
        await conversations.get(user=ADMIN, oid="a" * 32)

    assert caught.value.code == conversations.NOT_FOUND
    assert caught.value.status_code == 404


@pytest.mark.asyncio
async def test_update_clears_model_only_when_asked(env) -> None:
    env.conv.seed("a" * 32, model="gpt-x", title="旧名")

    kept = await conversations.update(user=ADMIN, oid="a" * 32, title="新名")

    assert kept["title"] == "新名"
    # 不传 model = 不改，不是清空。
    assert kept["model"] == "gpt-x"

    cleared = await conversations.update(user=ADMIN, oid="a" * 32, clear_model=True)

    assert cleared["model"] is None


@pytest.mark.asyncio
async def test_update_rejects_unknown_status(env) -> None:
    env.conv.seed("a" * 32)

    with pytest.raises(AgentError):
        await conversations.update(user=ADMIN, oid="a" * 32, status="deleted")


# ---------------------------------------------------------------------------
# 消息
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_messages_is_incremental_with_usage(env) -> None:
    env.conv.seed("a" * 32, message_count=2)
    env.msg.seed("a" * 32, seq=1, role="user", content="问题")
    env.msg.seed(
        "a" * 32,
        seq=2,
        role="assistant",
        content="回答",
        tool_calls='[{"id": "c1", "type": "function", "function": {"name": "fs_read"}}]',
        round_no=1,
        request_id="r" * 32,
        prompt_tokens=10,
        completion_tokens=5,
        total_tokens=15,
    )

    page = await conversations.list_messages(user=ADMIN, oid="a" * 32, after_seq=1)

    assert page["total"] == 2
    assert [row["seq"] for row in page["messages"]] == [2]
    message = page["messages"][0]
    # tool_calls 原文解析成对象，前端不必再解一次。
    assert message["tool_calls"][0]["id"] == "c1"
    assert message["usage"]["total_tokens"] == 15
    assert page["has_more"] is False


@pytest.mark.asyncio
async def test_message_usage_stays_null_when_upstream_reported_nothing(env) -> None:
    env.conv.seed("a" * 32, message_count=1)
    env.msg.seed("a" * 32, seq=1, role="user", content="问题")

    page = await conversations.list_messages(user=ADMIN, oid="a" * 32)

    # NULL = 上游没报，不是 0（0 是真实观测值）。
    assert page["messages"][0]["usage"] is None


@pytest.mark.asyncio
async def test_list_messages_of_foreign_conversation_is_404(env) -> None:
    env.conv.seed("a" * 32, owner_oid=OTHER)
    env.msg.seed("a" * 32, seq=1, role="user", content="机密")

    with pytest.raises(AgentError) as caught:
        await conversations.list_messages(user=ADMIN, oid="a" * 32)

    assert caught.value.status_code == 404


@pytest.mark.asyncio
async def test_append_round_refuses_a_second_user_message(env) -> None:
    env.conv.seed("a" * 32)

    with pytest.raises(ValueError):
        await conversations.append_round(
            "a" * 32, [{"oid": uuid4().hex, "role": "user", "content": "重跑一次"}]
        )


@pytest.mark.asyncio
async def test_append_round_hands_usage_to_the_batch(env) -> None:
    env.conv.seed("a" * 32)
    usage = {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120, "context_tokens": 100}

    await conversations.append_round(
        "a" * 32,
        [
            {"oid": uuid4().hex, "role": "assistant", "content": "ok", "round_no": 1},
            {"oid": uuid4().hex, "role": "tool", "content": "{}", "tool_call_id": "c1"},
        ],
        usage=usage,
    )

    assert len(env.msg.batches) == 1
    batch = env.msg.batches[0]
    assert [row["role"] for row in batch["rows"]] == ["assistant", "tool"]
    assert batch["usage"] == usage


# ---------------------------------------------------------------------------
# 删除门禁
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_delete_is_blocked_by_an_active_run(env) -> None:
    env.conv.seed("a" * 32, message_count=1)
    env.msg.seed("a" * 32, seq=1, role="user", content="问题")
    env.runs.seed("r" * 32, conversation_oid="a" * 32, status="running")

    with pytest.raises(AgentError) as caught:
        await conversations.delete(user=ADMIN, oid="a" * 32)

    assert caught.value.code == conversations.RUN_CONFLICT
    assert caught.value.status_code == 409
    # 一步都没动：门禁在前，删除在后。
    assert env.msg.calls == []
    assert env.runs.calls == []
    assert "a" * 32 in env.conv.rows


@pytest.mark.asyncio
async def test_delete_invalidates_pending_proposals_only_for_that_conversation(env) -> None:
    env.conv.seed("a" * 32, message_count=1)
    env.conv.seed("b" * 32, message_count=1)
    env.runs.seed("r" * 32, conversation_oid="a" * 32, status="succeeded")
    env.runs.seed("s" * 32, conversation_oid="b" * 32, status="succeeded")
    doomed = env.events.seed(request_id="r" * 32)
    kept = env.events.seed(request_id="s" * 32)
    done = env.events.seed(request_id="r" * 32, status="confirmed")

    result = await conversations.delete(user=ADMIN, oid="a" * 32)

    assert result["deleted"] is True
    assert doomed["status"] == "failed"
    assert doomed["result_summary"] == conversations.DELETED_PROPOSAL_SUMMARY
    # 别的会话的提案、以及已经确认过的提案都不动。
    assert kept["status"] == "proposed"
    assert done["status"] == "confirmed"


@pytest.mark.asyncio
async def test_delete_purges_messages_runs_then_conversation(env) -> None:
    env.conv.seed("a" * 32, message_count=2)
    env.msg.seed("a" * 32, seq=1, role="user", content="一")
    env.msg.seed("a" * 32, seq=2, role="assistant", content="二")
    env.runs.seed("r" * 32, conversation_oid="a" * 32, status="succeeded")

    result = await conversations.delete(user=ADMIN, oid="a" * 32)

    assert result == {"deleted": True, "messages": 2, "runs": 1}
    assert env.msg.calls == ["purge_messages"]
    assert env.runs.calls == ["purge_runs"]
    assert env.conv.calls == ["delete_batched"]
    assert "a" * 32 not in env.conv.rows
    # 顺序：消息与 run 必须早于会话本体（run 先删会丢掉提案归属的唯一线索）。
    assert env.msg.batches == []


@pytest.mark.asyncio
async def test_delete_of_foreign_conversation_is_404(env) -> None:
    env.conv.seed("a" * 32, owner_oid=OTHER)

    with pytest.raises(AgentError) as caught:
        await conversations.delete(user=ADMIN, oid="a" * 32)

    assert caught.value.status_code == 404
    assert env.conv.rows["a" * 32]["owner_oid"] == OTHER
