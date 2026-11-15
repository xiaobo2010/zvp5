"""后台 run 队列（``services/agent/run_queue.py``）：入队 / 领取 / 心跳 / 取消 / 收敛。

这一层出错的代价最高：一个「看起来成功」的假象背后可能是重复计费、僵尸 run 一直
占着唯一索引，或者一条半残的工具链让下次回放被上游 400。所以这里钉的都是**不变量**
而不是实现细节：

* 入队幂等（同一 ``client_msg_id`` 只写一条用户消息、只扣一次费）；
* 活跃 run 的 409 由应用层先判、数据库唯一索引兜底；
* 心跳与上游等待解耦（不是只在轮边界刷）；
* 取消 / watchdog / 崩溃三种收尾都只能 CAS 一次，不能覆盖终态；
* 一轮的 assistant + 全部 tool 消息**一次** ``append_batch``（同一事务）。

executor 是 mock：本车道只验证「怎么调」与「回调怎么写库」，不验证上游协议。
"""

from __future__ import annotations

import asyncio
import inspect
import json
import time
from datetime import datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
import pytest_asyncio

from db import backends
from db.repos.oracle.users import UserGoneError
from db.repos.protocols import ActiveRunConflict
from services.agent import run_queue
from services.agent.tool_impl import AgentError
from util import agent_unlock

OWNER = "0" * 24
OTHER = "1" * 24
ADMIN = {"id": OWNER, "per": ["admin"]}
CONV = "a" * 32


def _now(offset: float = 0.0) -> datetime:
    return datetime(2026, 10, 1, 12, 0, 0) + timedelta(seconds=offset)


# ---------------------------------------------------------------------------
# 内存仓储
# ---------------------------------------------------------------------------

class FakeConversationsRepo:
    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}
        self.summaries: list[tuple[str, str, int]] = []
        self.context_updates: list[tuple[str, int | None]] = []
        self.degraded_marks: list[str] = []

    def seed(self, oid: str = CONV, *, owner_oid: str = OWNER, mode: str = "agent") -> dict:
        row = {
            "_id": oid,
            "owner_oid": owner_oid,
            "mode": mode,
            "title": None,
            "model": None,
            "status": "active",
            "summary": None,
            "summary_upto_seq": 0,
            "tokens_prompt": 0,
            "tokens_completion": 0,
            "tokens_total": 0,
            "context_tokens": 0,
            "message_count": 0,
            "compaction_degraded_at": None,
            "created_at": _now(),
            "updated_at": _now(),
        }
        self.rows[oid] = row
        return row

    async def get(self, oid: str, owner_oid: str) -> dict | None:
        row = self.rows.get(str(oid))
        if row is None or str(row["owner_oid"]) != str(owner_oid):
            return None
        return dict(row)

    async def set_summary(self, oid, summary, upto_seq) -> None:
        self.summaries.append((str(oid), summary, int(upto_seq)))
        row = self.rows.get(str(oid))
        if row is not None:
            row["summary"] = summary
            row["summary_upto_seq"] = int(upto_seq)

    async def set_context_tokens(self, oid, tokens) -> None:
        self.context_updates.append((str(oid), tokens))
        row = self.rows.get(str(oid))
        if row is not None:
            row["context_tokens"] = 0 if tokens is None else int(tokens)

    async def mark_compaction_degraded(self, oid, *, at=None) -> None:
        self.degraded_marks.append(str(oid))
        row = self.rows.get(str(oid))
        if row is not None:
            row["compaction_degraded_at"] = at or _now()

    async def update(self, oid, owner_oid, *, title=None, status=None,
                     thinking=None, clear_thinking=False) -> bool:
        row = self.rows.get(str(oid))
        if row is None:
            return False
        if title is not None:
            row["title"] = title
        if status is not None:
            row["status"] = status
        if thinking is not None:
            row["thinking"] = thinking
        if clear_thinking:
            row["thinking"] = None
        return True


class FakeMessagesRepo:
    def __init__(self) -> None:
        self.rows: list[dict] = []
        self.batches: list[dict] = []
        self.conversations: FakeConversationsRepo | None = None

    async def append_batch(self, conversation_oid, rows, *, usage=None):
        self.batches.append(
            {
                "conversation_oid": conversation_oid,
                "rows": [dict(row) for row in rows],
                "usage": usage,
            }
        )
        base = max(
            [row["seq"] for row in self.rows if row["conversation_oid"] == conversation_oid],
            default=0,
        )
        inserted = []
        for offset, item in enumerate(rows):
            row = dict(item)
            row["seq"] = base + offset + 1
            row["conversation_oid"] = conversation_oid
            row["created_at"] = _now()
            self.rows.append(row)
            inserted.append(row)
        # 真实仓储在同一个事务里更新会话行：这里照做，断言才有意义。
        if self.conversations is not None:
            conversation = self.conversations.rows.get(conversation_oid)
            if conversation is not None:
                conversation["message_count"] += len(rows)
                if usage:
                    conversation["tokens_prompt"] += int(usage.get("prompt_tokens") or 0)
                    conversation["tokens_completion"] += int(usage.get("completion_tokens") or 0)
                    conversation["tokens_total"] += int(usage.get("total_tokens") or 0)
                    if usage.get("context_tokens") is not None:
                        conversation["context_tokens"] = int(usage["context_tokens"])
        return inserted

    def seed(self, *, conversation_oid: str = CONV, seq: int, role: str,
             content: str = "", tool_calls: str | None = None,
             tool_call_id: str | None = None, tool_name: str | None = None,
             request_id: str | None = None) -> dict:
        row = {
            "_id": uuid4().hex,
            "conversation_oid": conversation_oid,
            "seq": seq,
            "role": role,
            "content": content,
            "tool_calls": tool_calls,
            "tool_call_id": tool_call_id,
            "tool_name": tool_name,
            "round_no": None,
            "finish_reason": None,
            "request_id": request_id,
            "prompt_tokens": None,
            "completion_tokens": None,
            "total_tokens": None,
            "cache_read_tokens": None,
            "cache_write_tokens": None,
            "created_at": _now(),
        }
        self.rows.append(row)
        return row

    async def list_after(self, conversation_oid, after_seq=0, *, limit=100):
        rows = [
            row
            for row in self.rows
            if row["conversation_oid"] == conversation_oid and row["seq"] > after_seq
        ]
        rows.sort(key=lambda row: row["seq"])
        return [dict(row) for row in rows[:limit]]


class FakeRunsRepo:
    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}
        self.finishes: list[dict] = []
        self.heartbeats = 0
        self.interrupt_running_calls = 0
        self.interrupt_stale_calls: list[datetime] = []
        self.stale_hits = 0
        self.messages: FakeMessagesRepo | None = None
        self.conversations: FakeConversationsRepo | None = None

    # -- 基本查询 ---------------------------------------------------------
    def seed(self, *, conversation_oid: str = CONV, owner_oid: str = OWNER,
             status: str = "queued", client_msg_id: str | None = None, **extra) -> dict:
        run_id = uuid4().hex
        row = {
            "_id": run_id,
            "conversation_oid": conversation_oid,
            "owner_oid": owner_oid,
            "client_msg_id": client_msg_id,
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

    # -- 入队 -------------------------------------------------------------
    async def insert_queued(self, *, run_id, conversation_oid, owner_oid, message_oid,
                            content, client_msg_id=None, model=None):
        if client_msg_id:
            for row in self.rows.values():
                if row["conversation_oid"] == conversation_oid and row["client_msg_id"] == client_msg_id:
                    existing = dict(row)
                    existing["message_oid"] = next(
                        (
                            item["_id"]
                            for item in (self.messages.rows if self.messages else [])
                            if item["request_id"] == row["_id"] and item["role"] == "user"
                        ),
                        None,
                    )
                    return existing, False
        active = [
            row
            for row in self.rows.values()
            if row["status"] in ("queued", "running")
            and (row["conversation_oid"] == conversation_oid or row["owner_oid"] == owner_oid)
        ]
        if active:
            # 真实仓储靠两个函数唯一索引；这里显式拒绝，语义相同。
            raise ActiveRunConflict("active run exists")
        row = {
            "_id": run_id,
            "conversation_oid": conversation_oid,
            "owner_oid": owner_oid,
            "client_msg_id": client_msg_id,
            "status": "queued",
            "model": model,
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
        self.rows[run_id] = row
        if self.messages is not None:
            self.messages.rows.append(
                {
                    "_id": message_oid,
                    "conversation_oid": conversation_oid,
                    "seq": 1 + len(self.messages.rows),
                    "role": "user",
                    "content": content,
                    "tool_calls": None,
                    "tool_call_id": None,
                    "tool_name": None,
                    "round_no": None,
                    "finish_reason": None,
                    "request_id": run_id,
                    "prompt_tokens": None,
                    "completion_tokens": None,
                    "total_tokens": None,
                    "cache_read_tokens": None,
                    "cache_write_tokens": None,
                    "created_at": _now(),
                }
            )
        if self.conversations is not None:
            conversation = self.conversations.rows.get(conversation_oid)
            if conversation is not None:
                conversation["message_count"] += 1
        return dict(row, message_oid=message_oid), True

    # -- 领取 / 心跳 / 收尾 -----------------------------------------------
    async def claim_next(self) -> dict | None:
        candidates = [
            row
            for row in self.rows.values()
            if row["status"] == "queued" and not row["cancel_requested"]
        ]
        if not candidates:
            return None
        row = sorted(candidates, key=lambda item: (item["created_at"], item["_id"]))[0]
        row["status"] = "running"
        row["started_at"] = _now()
        row["heartbeat_at"] = _now()
        return dict(row)

    async def heartbeat(self, run_id: str, *, at=None) -> bool:
        row = self.rows.get(str(run_id))
        if row is None or row["status"] != "running":
            return False
        self.heartbeats += 1
        row["heartbeat_at"] = at or _now()
        return True

    async def finish(self, run_id, *, status, stop_reason=None, rounds=None,
                     error_code=None, error_message=None, result_message_oid=None,
                     prompt_tokens=None, completion_tokens=None, total_tokens=None,
                     finished_at=None) -> bool:
        row = self.rows.get(str(run_id))
        if row is None or row["status"] != "running":
            return False
        row.update(
            {
                "status": status,
                "stop_reason": stop_reason,
                "rounds": rounds,
                "error_code": error_code,
                "error_message": error_message,
                "result_message_oid": result_message_oid,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens,
                "finished_at": finished_at or _now(),
            }
        )
        self.finishes.append(dict(row))
        return True

    # -- 取消 / 收敛 -------------------------------------------------------
    async def request_cancel(self, run_id, owner_oid) -> bool:
        row = self.rows.get(str(run_id))
        if row is None or row["owner_oid"] != owner_oid or row["status"] not in ("queued", "running"):
            return False
        row["cancel_requested"] = True
        return True

    async def mark_cancelled_if_queued(self, run_id) -> bool:
        row = self.rows.get(str(run_id))
        if row is None or row["status"] != "queued":
            return False
        row["status"] = "cancelled"
        row["finished_at"] = _now()
        return True

    async def interrupt_running(self, **_kwargs) -> int:
        self.interrupt_running_calls += 1
        hits = 0
        for row in self.rows.values():
            if row["status"] == "running":
                row["status"] = "interrupted"
                row["error_code"] = "AI_RUN_INTERRUPTED"
                hits += 1
        return hits

    async def interrupt_stale(self, cutoff, **_kwargs) -> int:
        self.interrupt_stale_calls.append(cutoff)
        hits = 0
        for row in self.rows.values():
            if row["status"] == "running" and (row["heartbeat_at"] or _now(0)) < cutoff:
                row["status"] = "interrupted"
                hits += 1
        return hits or self.stale_hits


class FakePromptAuditRepo:
    """``AI_PROMPT_AUDIT`` 的内存实现（只做本轮用到的 insert / update_outcome）。"""

    def __init__(self) -> None:
        self.rows: list[dict] = []

    async def insert(self, **fields) -> str:
        oid = uuid4().hex[:24]
        row = {
            "oid": oid,
            "refused": 0,
            "warned": 0,
            "answer_text": None,
            "tool_calls": None,
        }
        row.update(fields)
        self.rows.append(row)
        return oid

    async def update_outcome(self, oid, *, answer_text=None, tool_calls=None,
                             refused=None, warned=None) -> bool:
        for row in self.rows:
            if row["oid"] != str(oid):
                continue
            if answer_text is not None:
                row["answer_text"] = answer_text
            if tool_calls is not None:
                row["tool_calls"] = tool_calls
            if refused is not None:
                row["refused"] = 1 if refused else 0
            if warned is not None:
                row["warned"] = 1 if warned else 0
            return True
        return False


class FakeUsersRepo:
    def __init__(self) -> None:
        self.roles = ["admin"]

    async def get_for_auth(self, oid: str) -> dict | None:
        if str(oid) != OWNER:
            return None
        return {"_id": oid, "roles": list(self.roles)}


class FakeEventsRepo:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    def seed(self, *, request_id: str, round_no: int = 1, tool_kind: str = "write",
             tool_name: str = "fs_write", status: str = "proposed",
             actor_oid: str = OWNER, oid: str | None = None) -> dict:
        row = {
            "_id": oid or uuid4().hex,
            "request_id": request_id,
            "round_no": round_no,
            "tool_name": tool_name,
            "tool_kind": tool_kind,
            "args_hash": "h",
            "args_summary": "{}",
            "args_payload": "{}",
            "preview": "{}",
            "result_summary": "等待确认",
            "status": status,
            "actor_oid": actor_oid,
            "approver_oid": None,
            "confirmed_at": None,
            "created_at": _now(),
        }
        self.rows.append(row)
        return row

    async def list_for_request(self, request_id: str) -> list[dict]:
        return [dict(row) for row in self.rows if str(row["request_id"]) == str(request_id)]


@pytest.fixture
def env(monkeypatch) -> SimpleNamespace:
    conv = FakeConversationsRepo()
    msg = FakeMessagesRepo()
    runs = FakeRunsRepo()
    events = FakeEventsRepo()
    users = FakeUsersRepo()
    prompt_audit = FakePromptAuditRepo()
    msg.conversations = conv
    runs.messages = msg
    runs.conversations = conv
    monkeypatch.setattr(backends, "get_ai_conversations_repo", lambda session=None: conv)
    monkeypatch.setattr(backends, "get_ai_messages_repo", lambda session=None: msg)
    monkeypatch.setattr(backends, "get_ai_runs_repo", lambda session=None: runs)
    monkeypatch.setattr(backends, "get_ai_agent_events_repo", lambda session=None: events)
    monkeypatch.setattr(backends, "get_users_repo", lambda session=None: users)
    monkeypatch.setattr(
        backends, "get_ai_prompt_audit_repo", lambda session=None: prompt_audit
    )
    monkeypatch.setattr(run_queue, "DEFAULT_IDLE_SECONDS", 0.01, raising=False)
    conv.seed(CONV)
    return SimpleNamespace(
        conv=conv, msg=msg, runs=runs, events=events, users=users,
        prompt_audit=prompt_audit,
    )


@pytest_asyncio.fixture(autouse=True)
async def _fresh_queue():
    run_queue.reset()
    # agent 提交现在自带 30 分钟密码门：本文件验证的是队列机制，不是门禁，
    # 所以把两个参与者预置成已解锁（门禁本身由 test_agent_unlock 守着）。
    await agent_unlock.clear_state()
    await agent_unlock.mark_unlocked(OWNER)
    await agent_unlock.mark_unlocked(OTHER)
    yield
    run_queue.reset()
    await agent_unlock.clear_state()
    await asyncio.sleep(0)  # 让被 cancel 的泵真的结束，免得 loop 关闭时报 pending


def install_executor(monkeypatch, handler):
    """把 ``executor.run`` 换成可控实现，返回调用记录。"""
    calls: list[dict] = []

    async def run(*, user, messages, model=None, thinking=None, request_id=None,
                  summary=None, on_round=None, should_stop=None, on_summary=None,
                  scene=None, reference=None, context_tokens=None,
                  on_compaction=None, on_delta=None, on_tool=None):
        record = {
            "user": user,
            "messages": messages,
            "model": model,
            "thinking": thinking,
            "request_id": request_id,
            "summary": summary,
            "scene": scene,
            "reference": reference,
            "context_tokens": context_tokens,
            "on_compaction": on_compaction,
            "on_delta": on_delta,
            "on_tool": on_tool,
        }
        calls.append(record)
        forwarded = {
            "on_round": on_round,
            "should_stop": should_stop,
            "on_summary": on_summary,
            "record": record,
        }
        # 老用例的 handler 不收 ``on_compaction``：只在它愿意接的时候透传，避免
        # 「给所有 handler 加一个参数」的机械改动把测试意图冲淡。
        if "on_compaction" in inspect.signature(handler).parameters:
            forwarded["on_compaction"] = on_compaction
        return await handler(**forwarded)

    monkeypatch.setattr(run_queue.executor, "run", run)
    return calls


async def wait_idle(limit: float = 5.0) -> None:
    # 先让泵走一拍：kick() 只建协程，_in_flight 要等 claim 之后才非空。
    await asyncio.sleep(0.02)
    deadline = time.monotonic() + limit
    while time.monotonic() < deadline:
        if not run_queue._in_flight:
            return
        await asyncio.sleep(0.005)
    raise AssertionError("run queue did not go idle")


# ---------------------------------------------------------------------------
# 入队
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_submit_writes_user_message_and_queued_run(env) -> None:
    result = await run_queue.submit(
        user=ADMIN, conversation_oid=CONV, content="  帮我看看磁盘  "
    )

    assert result["status"] == "queued"
    assert "deduped" not in result
    row = env.runs.rows[result["run_id"]]
    assert row["status"] == "queued"
    assert row["owner_oid"] == OWNER
    user_messages = [item for item in env.msg.rows if item["role"] == "user"]
    assert [item["content"] for item in user_messages] == ["帮我看看磁盘"]
    assert user_messages[0]["_id"] == result["message_oid"]
    # 首条用户消息定标题（§2.1）。
    assert env.conv.rows[CONV]["title"] == "帮我看看磁盘"


@pytest.mark.asyncio
async def test_submit_is_idempotent_on_client_msg_id(env) -> None:
    first = await run_queue.submit(
        user=ADMIN, conversation_oid=CONV, content="查一下", client_msg_id="c-1"
    )
    second = await run_queue.submit(
        user=ADMIN, conversation_oid=CONV, content="查一下", client_msg_id="c-1"
    )

    assert second["run_id"] == first["run_id"]
    assert second["deduped"] is True
    assert second["message_oid"] == first["message_oid"]
    # 只写了一条用户消息：重试不重复计费。
    assert len(env.msg.rows) == 1


@pytest.mark.asyncio
async def test_submit_conflicts_with_an_active_run(env) -> None:
    env.runs.seed(conversation_oid=CONV, status="running")

    with pytest.raises(AgentError) as caught:
        await run_queue.submit(user=ADMIN, conversation_oid=CONV, content="再来一次")

    assert caught.value.code == run_queue.RUN_CONFLICT
    assert caught.value.status_code == 409
    assert env.msg.rows == []


@pytest.mark.asyncio
async def test_submit_rejects_a_chat_conversation(env) -> None:
    env.conv.seed("b" * 32, mode="chat")

    with pytest.raises(AgentError) as caught:
        await run_queue.submit(user=ADMIN, conversation_oid="b" * 32, content="跑一下")

    assert caught.value.code == "AI_CONVERSATION_MODE_MISMATCH"
    assert caught.value.status_code == 409


@pytest.mark.asyncio
async def test_submit_of_foreign_conversation_is_404(env) -> None:
    env.conv.seed("c" * 32, owner_oid=OTHER)

    with pytest.raises(AgentError) as caught:
        await run_queue.submit(user=ADMIN, conversation_oid="c" * 32, content="跑一下")

    assert caught.value.code == "AI_CONVERSATION_NOT_FOUND"


@pytest.mark.asyncio
async def test_submit_of_a_conversation_deleted_mid_flight_is_404(env, monkeypatch) -> None:
    """会话在 require_row 之后、插入之前被删：仓储拒绝写入，路由回 404。"""

    async def gone(**_kwargs):
        raise UserGoneError("session row vanished")

    monkeypatch.setattr(env.runs, "insert_queued", gone)

    with pytest.raises(AgentError) as caught:
        await run_queue.submit(user=ADMIN, conversation_oid=CONV, content="跑一下")

    assert caught.value.code == "AI_CONVERSATION_NOT_FOUND"
    assert caught.value.status_code == 404
    # 仓储在事务里就停了：既没有 run 行，也没有那条孤儿用户消息。
    assert env.runs.rows == {}
    assert env.msg.rows == []


# ---------------------------------------------------------------------------
# 领取与执行
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pump_claims_and_finishes_the_queued_run(env, monkeypatch) -> None:
    calls = install_executor(
        monkeypatch,
        lambda **_: _async({"stop_reason": None, "rounds": 1, "content": "done"}),
    )
    submitted = await run_queue.submit(user=ADMIN, conversation_oid=CONV, content="跑")

    await run_queue.kick()
    await wait_idle()

    row = env.runs.rows[submitted["run_id"]]
    assert row["status"] == "succeeded"
    assert row["stop_reason"] == "completed"
    assert row["finished_at"] is not None
    # 执行身份是**执行时刻**的角色，不是提交时的 token 快照；``banned`` 是这次
    # 新增的执行期封禁事实（提交到执行之间可能已经落封）。
    assert calls[0]["user"] == {"id": OWNER, "per": ["admin"], "banned": False}
    assert calls[0]["request_id"] == submitted["run_id"]
    # 历史里带着刚写进去的那条用户消息。
    assert calls[0]["messages"][-1]["role"] == "user"


@pytest.mark.asyncio
async def test_kick_is_idempotent(env, monkeypatch) -> None:
    install_executor(monkeypatch, lambda **_: _async({"stop_reason": None, "rounds": 0}))

    await run_queue.kick()
    first = run_queue._pump_task
    await run_queue.kick()

    assert run_queue._pump_task is first
    assert first is not None and not first.done()


@pytest.mark.asyncio
async def test_on_round_legacy_shape_still_writes_one_batch(env, monkeypatch) -> None:
    """旧形状 ``(round_no, messages, usage)`` 仍能落库（兼容入口，不是主路径）。"""
    tools = {"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110}
    final = {"prompt_tokens": 130, "completion_tokens": 7, "total_tokens": 137}

    async def handler(*, on_round, should_stop, on_summary=None, record):
        assert await should_stop() is False
        await on_round(
            1,
            [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {"id": "c1", "type": "function", "function": {"name": "fs_read", "arguments": "{}"}},
                        {"id": "c2", "type": "function", "function": {"name": "db_query", "arguments": "{}"}},
                    ],
                },
                {"role": "tool", "tool_call_id": "c1", "content": "文件内容"},
                {"role": "tool", "tool_call_id": "c2", "content": "查询结果"},
            ],
            tools,
        )
        await on_round(2, [{"role": "assistant", "content": "结论"}], final)
        return {"stop_reason": None, "rounds": 2, "content": "结论"}

    install_executor(monkeypatch, handler)
    submitted = await run_queue.submit(user=ADMIN, conversation_oid=CONV, content="查")

    await run_queue.kick()
    await wait_idle()

    # 一轮一批：assistant + 全部 tool 在同一个 append_batch（同一事务）。
    assert len(env.msg.batches) == 2
    first = env.msg.batches[0]
    assert [row["role"] for row in first["rows"]] == ["assistant", "tool", "tool"]
    assert [row["tool_name"] for row in first["rows"][1:]] == ["fs_read", "db_query"]
    assert first["rows"][0]["tool_calls"].startswith('[{"id": "c1"')
    assert first["usage"]["context_tokens"] == 100

    conversation = env.conv.rows[CONV]
    assert conversation["tokens_total"] == 110 + 137
    # context_tokens 是**覆盖**语义：最近一次请求的 prompt token，不是第二个总量。
    assert conversation["context_tokens"] == 130
    assert conversation["message_count"] == 1 + 3 + 1

    row = env.runs.rows[submitted["run_id"]]
    assert row["rounds"] == 2
    assert row["total_tokens"] == 247
    last_assistant = env.msg.batches[1]["rows"][0]["oid"]
    assert row["result_message_oid"] == last_assistant


@pytest.mark.asyncio
async def test_on_round_payload_writes_assistant_and_every_tool(env, monkeypatch) -> None:
    """executor 的真实形状 ``on_round(payload)``：assistant + 全部 tool 同一批。

    usage 用的是 payload 的**短键**（prompt/completion/total/cache_read/cache_write）
    ——把它们漏认会让每一轮的 token 都记成 NULL，会话累计永远是 0。
    """

    async def handler(*, on_round, should_stop, on_summary=None, record):
        await on_round(
            {
                "round_no": 1,
                "assistant": {
                    "content": "",
                    "reasoning": "先查活动再回答",
                    "finish_reason": "stop",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "search_activities", "arguments": "{}"},
                        },
                        {
                            "id": "call_2",
                            "type": "function",
                            "function": {"name": "fs_read", "arguments": "{}"},
                        },
                    ],
                },
                "tools": [
                    {
                        "tool_call_id": "call_1",
                        "tool_name": "search_activities",
                        "content": "找到 1 个活动",
                    },
                    {"tool_call_id": "call_2", "tool_name": "fs_read", "content": "文件内容"},
                ],
                "usage": {
                    "prompt": 100,
                    "completion": 20,
                    "total": 120,
                    "cache_read": 64,
                    "cache_write": None,
                },
                "units": 1,
            }
        )
        await on_round(
            {
                "round_no": 2,
                "assistant": {"content": "结论", "tool_calls": None, "finish_reason": "stop"},
                "tools": [],
                "usage": {"prompt": 130, "completion": 7, "total": 137},
                "units": 1,
            }
        )
        return {"stop_reason": None, "rounds": 2, "content": "结论"}

    install_executor(monkeypatch, handler)
    submitted = await run_queue.submit(user=ADMIN, conversation_oid=CONV, content="查")

    await run_queue.kick()
    await wait_idle()

    assert len(env.msg.batches) == 2
    first = env.msg.batches[0]
    assert [row["role"] for row in first["rows"]] == ["assistant", "tool", "tool"]
    assistant = first["rows"][0]
    # 有工具调用就记 tool_calls（上游可能把这一轮报成 stop，照抄会让 UI 判错）。
    assert assistant["finish_reason"] == "tool_calls"
    assert assistant["prompt_tokens"] == 100
    assert assistant["completion_tokens"] == 20
    assert assistant["cache_read_tokens"] == 64
    assert assistant["cache_write_tokens"] is None
    assert assistant["round_no"] == 1
    assert assistant["request_id"] == submitted["run_id"]
    # 思考过程落在 assistant 行上（tool 行恒为 NULL）：刷新页面后仍能看到。
    assert assistant["reasoning"] == "先查活动再回答"
    assert all(row.get("reasoning") is None for row in first["rows"][1:])
    # tool 行的身份直接来自 payload，不再反查 tool_calls。
    assert [row["tool_call_id"] for row in first["rows"][1:]] == ["call_1", "call_2"]
    assert [row["tool_name"] for row in first["rows"][1:]] == [
        "search_activities",
        "fs_read",
    ]
    assert [row["content"] for row in first["rows"][1:]] == ["找到 1 个活动", "文件内容"]
    # tool 行不带 usage（那一轮的量属于 assistant）——字段缺席即 NULL，不是 0。
    assert all(row.get("prompt_tokens") is None for row in first["rows"][1:])
    assert first["usage"]["context_tokens"] == 100
    # 最后的 assistant（无工具）就是 run 的最终回答。
    final = env.msg.batches[1]["rows"][0]
    assert final["finish_reason"] == "stop"
    # 这一轮的 payload 没带 reasoning → NULL（不是空串：两者的 UI 含义不同）。
    assert final["reasoning"] is None
    assert env.runs.rows[submitted["run_id"]]["result_message_oid"] == final["oid"]
    assert env.conv.rows[CONV]["tokens_total"] == 120 + 137
    assert env.conv.rows[CONV]["context_tokens"] == 130


@pytest.mark.asyncio
async def test_summary_never_goes_into_messages(env, monkeypatch) -> None:
    """摘要走 ``summary=``，不进 messages（executor 只收 user/assistant，system 会 400）。"""
    env.conv.seed(CONV)
    env.conv.rows[CONV]["summary"] = "更早内容的摘要"
    env.conv.rows[CONV]["summary_upto_seq"] = 2
    env.msg.seed(seq=1, role="user", content="早期问题")
    env.msg.seed(seq=2, role="assistant", content="早期回答")
    calls = install_executor(
        monkeypatch, lambda **_: _async({"stop_reason": None, "rounds": 0})
    )

    await run_queue.submit(user=ADMIN, conversation_oid=CONV, content="现在的问题")
    await run_queue.kick()
    await wait_idle()

    sent = calls[0]
    assert sent["summary"] == "更早内容的摘要"
    assert [message["role"] for message in sent["messages"]] == ["user"]
    # 水位以内（seq <= 2）的原文不再进上下文：那是摘要覆盖的部分。
    assert sent["messages"][0]["content"] == "现在的问题"


@pytest.mark.asyncio
async def test_replay_carries_seq_and_tool_chain(env, monkeypatch) -> None:
    """回放带 ``seq``（压缩算切点）与完整工具链（否则上游 400）。"""
    env.conv.seed(CONV)
    env.msg.seed(seq=1, role="user", content="读一下文件")
    env.msg.seed(
        seq=2,
        role="assistant",
        content="",
        tool_calls=json.dumps(
            [{"id": "call_9", "type": "function", "function": {"name": "fs_read"}}]
        ),
    )
    env.msg.seed(
        seq=3, role="tool", content="文件内容", tool_call_id="call_9", tool_name="fs_read"
    )
    calls = install_executor(
        monkeypatch, lambda **_: _async({"stop_reason": None, "rounds": 0})
    )

    await run_queue.submit(user=ADMIN, conversation_oid=CONV, content="继续")
    await run_queue.kick()
    await wait_idle()

    messages = calls[0]["messages"]
    # 前三条是历史，最后一条是本次提交写入的用户消息（§4.5：run 创建时落库）。
    assert [message["role"] for message in messages] == [
        "user",
        "assistant",
        "tool",
        "user",
    ]
    # seq 原样透传：压缩的切点只能落在已落库的行上（executor 送上游前会剥掉）。
    assert [message["seq"] for message in messages] == [1, 2, 3, 4]
    assert messages[1]["tool_calls"][0]["id"] == "call_9"
    assert messages[2]["tool_call_id"] == "call_9"
    assert messages[2]["name"] == "fs_read"
    # 摘要为空时不额外塞任何 system 行。
    assert calls[0]["summary"] is None


@pytest.mark.asyncio
async def test_on_summary_persists_monotonically(env, monkeypatch) -> None:
    """``on_summary`` 落库 summary + upto_seq，水位只许向前（单 run 可压多次）。"""

    async def handler(*, on_round, should_stop, on_summary=None, record):
        await on_summary("压缩后的摘要", 6)
        # 同一水位重放：忽略（同一份摘要写两遍没有意义）。
        await on_summary("压缩后的摘要", 6)
        # 水位倒退：同样忽略。往回写会让已摘要过的原文重新进上下文。
        await on_summary("更旧的摘要", 4)
        # 长 run 再压一次：水位推进，摘要覆盖到更后面（累积，不是替换成片段）。
        await on_summary("更完整的摘要", 9)
        return {"stop_reason": None, "rounds": 1}

    install_executor(monkeypatch, handler)
    await run_queue.submit(user=ADMIN, conversation_oid=CONV, content="长会话")

    await run_queue.kick()
    await wait_idle()

    assert env.conv.summaries == [(CONV, "压缩后的摘要", 6), (CONV, "更完整的摘要", 9)]
    assert env.conv.rows[CONV]["summary"] == "更完整的摘要"
    assert env.conv.rows[CONV]["summary_upto_seq"] == 9


@pytest.mark.asyncio
async def test_on_compaction_refreshes_context_tokens_and_marks_degraded(
    env, monkeypatch
) -> None:
    """压缩回调负责把会话水位拉下来，并在降级时补一条事实。"""
    env.conv.seed(CONV)
    env.conv.rows[CONV]["context_tokens"] = 180_000

    async def handler(*, on_round, should_stop, on_summary=None, on_compaction=None,
                      record):
        await on_compaction(
            {
                "compacted": True,
                "upto_seq": 6,
                "tokens_before": 180_000,
                "tokens_after": 24_000,
                "degraded": True,
                "reason": "degraded",
                "summary": None,
            }
        )
        return {"stop_reason": None, "rounds": 1}

    install_executor(monkeypatch, handler)
    submitted = await run_queue.submit(user=ADMIN, conversation_oid=CONV, content="长会话")

    await run_queue.kick()
    await wait_idle()

    # 前端占用条据此回落：压缩后写回的是压缩后的规模，不是压缩前的。
    assert env.conv.context_updates == [(CONV, 24_000)]
    assert env.conv.rows[CONV]["context_tokens"] == 24_000
    assert env.conv.degraded_marks == [CONV]
    assert env.runs.rows[submitted["run_id"]]["status"] == "succeeded"


@pytest.mark.asyncio
async def test_on_compaction_failure_does_not_fail_the_run(env, monkeypatch) -> None:
    """水位刷新失败只记日志：压缩已经完成，run 的结果不该因此变成 failed。"""

    async def boom(oid, tokens):
        raise RuntimeError("db down")

    monkeypatch.setattr(env.conv, "set_context_tokens", boom)

    async def handler(*, on_round, should_stop, on_summary=None, on_compaction=None,
                      record):
        await on_compaction(
            {"compacted": True, "upto_seq": 3, "tokens_after": 900_000,
             "tokens_before": 5, "degraded": False, "reason": None}
        )
        return {"stop_reason": None, "rounds": 1}

    install_executor(monkeypatch, handler)
    submitted = await run_queue.submit(user=ADMIN, conversation_oid=CONV, content="长会话")

    await run_queue.kick()
    await wait_idle()

    assert env.runs.rows[submitted["run_id"]]["status"] == "succeeded"


@pytest.mark.asyncio
async def test_on_summary_failure_does_not_fail_the_run(env, monkeypatch) -> None:
    """摘要落库失败只记日志：压缩已经完成，run 的结果不该因此变成 failed。"""

    async def boom(oid, summary, upto_seq):
        raise RuntimeError("db down")

    monkeypatch.setattr(env.conv, "set_summary", boom)

    async def handler(*, on_round, should_stop, on_summary=None, record):
        await on_summary("摘要", 3)
        return {"stop_reason": None, "rounds": 1}

    install_executor(monkeypatch, handler)
    submitted = await run_queue.submit(user=ADMIN, conversation_oid=CONV, content="长会话")

    await run_queue.kick()
    await wait_idle()

    assert env.runs.rows[submitted["run_id"]]["status"] == "succeeded"


# ---------------------------------------------------------------------------
# 手动压缩（POST /conversations/{oid}/compact 的服务层）
# ---------------------------------------------------------------------------


def _install_compact(monkeypatch, result: dict, *, rebuilt=None):
    """把 ``executor.compact_messages`` 换成可控实现，返回调用记录。"""
    calls: list[dict] = []

    async def compact_messages(**kwargs):
        calls.append(kwargs)
        return (rebuilt or [], dict(result))

    monkeypatch.setattr(run_queue.executor, "compact_messages", compact_messages)
    return calls


def _seed_history(env, turns: int = 4) -> None:
    """``turns`` 轮 user/assistant 历史（seq 1..2*turns）。"""
    for index in range(turns):
        env.msg.seed(seq=2 * index + 1, role="user", content=f"问题 {index + 1}")
        env.msg.seed(seq=2 * index + 2, role="assistant", content=f"回答 {index + 1}")
    env.conv.rows[CONV]["message_count"] = 2 * turns


@pytest.mark.asyncio
async def test_manual_compact_forces_preflight_and_refreshes_the_water_line(
    env, monkeypatch
) -> None:
    """手动压缩：绕过阈值（force=True），落摘要 + 把会话水位改成压缩后的规模。"""
    _seed_history(env)
    env.conv.rows[CONV]["model"] = "gpt-x"
    env.conv.rows[CONV]["context_tokens"] = 180_000
    calls = _install_compact(
        monkeypatch,
        {
            "compacted": True,
            "upto_seq": 6,
            "tokens_before": 180_000,
            "tokens_after": 21_000,
            "degraded": False,
            "reason": None,
            "summary": "早期结论摘要",
        },
    )

    data = await run_queue.compact_conversation(user=ADMIN, conversation_oid=CONV)

    assert data == {
        "compacted": True,
        "upto_seq": 6,
        "tokens_before": 180_000,
        "tokens_after": 21_000,
        "degraded": False,
        "reason": None,
    }
    # 与自动路径共用实现：force=True + 会话行水位当作已知 prompt 规模。
    assert calls[0]["force"] is True
    assert calls[0]["prompt_tokens"] == 180_000
    assert calls[0]["model"] == "gpt-x"
    assert [message["seq"] for message in calls[0]["messages"]] == [1, 2, 3, 4, 5, 6, 7, 8]
    assert env.conv.summaries == [(CONV, "早期结论摘要", 6)]
    # 前端占用条的依据：压缩后写回的是压缩后的规模。
    assert env.conv.context_updates == [(CONV, 21_000)]
    assert env.conv.degraded_marks == []


@pytest.mark.asyncio
async def test_manual_compact_rejects_an_active_run(env, monkeypatch) -> None:
    """同会话还有在飞 run → 409，且不花一次摘要调用（worker 手里的上下文会打架）。"""
    _seed_history(env)
    env.runs.seed(status="running")
    calls = _install_compact(monkeypatch, {"compacted": True})

    with pytest.raises(AgentError) as caught:
        await run_queue.compact_conversation(user=ADMIN, conversation_oid=CONV)

    assert caught.value.status_code == 409
    assert caught.value.code == "AI_RUN_CONFLICT"
    assert calls == []
    assert env.conv.summaries == []


@pytest.mark.asyncio
async def test_manual_compact_reports_nothing_to_compact_without_writes(
    env, monkeypatch
) -> None:
    """轮数不够（user 轮 ≤ keep_turns）→ 200 + compacted=false，不写任何东西。"""
    _seed_history(env)
    calls = _install_compact(
        monkeypatch,
        {
            "compacted": False,
            "upto_seq": None,
            "tokens_before": 900,
            "tokens_after": 900,
            "degraded": False,
            "reason": "nothing_to_compact",
            "summary": None,
        },
    )

    data = await run_queue.compact_conversation(user=ADMIN, conversation_oid=CONV)

    assert data["compacted"] is False
    assert data["reason"] == "nothing_to_compact"
    assert calls and calls[0]["force"] is True
    assert env.conv.summaries == []
    assert env.conv.context_updates == []


@pytest.mark.asyncio
async def test_manual_compact_of_an_empty_conversation_is_not_an_error(
    env, monkeypatch
) -> None:
    """空会话没有可压的历史：直接回 nothing_to_compact，不调上游。"""
    calls = _install_compact(monkeypatch, {"compacted": True})

    data = await run_queue.compact_conversation(user=ADMIN, conversation_oid=CONV)

    assert data["compacted"] is False
    assert data["reason"] == "nothing_to_compact"
    assert calls == []


@pytest.mark.asyncio
async def test_manual_compact_marks_degraded_and_still_refreshes_tokens(
    env, monkeypatch
) -> None:
    """降级（硬截断）也要把水位拉下来，并补一条降级事实；返回 degraded=True。"""
    _seed_history(env)
    env.conv.rows[CONV]["context_tokens"] = 190_000
    _install_compact(
        monkeypatch,
        {
            "compacted": True,
            "upto_seq": 8,
            "tokens_before": 190_000,
            "tokens_after": 12_000,
            "degraded": True,
            "reason": "degraded",
            "summary": None,
        },
    )

    data = await run_queue.compact_conversation(user=ADMIN, conversation_oid=CONV)

    assert (data["degraded"], data["reason"]) == (True, "degraded")
    assert env.conv.summaries == []  # 没有摘要可写
    assert env.conv.context_updates == [(CONV, 12_000)]
    assert env.conv.degraded_marks == [CONV]


@pytest.mark.asyncio
async def test_manual_compact_surfaces_insufficient_quota(env, monkeypatch) -> None:
    """额度不足不是瞬态故障：手动压缩如实抛 429，而不是偷偷硬截断。"""
    _seed_history(env)

    async def boom(**_kwargs):
        raise AgentError("insufficient_quota", "monthly chat quota is exhausted", 429)

    monkeypatch.setattr(run_queue.executor, "compact_messages", boom)

    with pytest.raises(AgentError) as caught:
        await run_queue.compact_conversation(user=ADMIN, conversation_oid=CONV)

    assert caught.value.code == "insufficient_quota"
    assert caught.value.status_code == 429
    assert env.conv.context_updates == []


@pytest.mark.asyncio
async def test_manual_compact_is_owner_scoped(env, monkeypatch) -> None:
    """别人的会话一律 404（不区分「没有」与「不是你的」）。"""
    _seed_history(env)
    calls = _install_compact(monkeypatch, {"compacted": True})

    with pytest.raises(AgentError) as caught:
        await run_queue.compact_conversation(user={"id": OTHER, "per": ["admin"]}, conversation_oid=CONV)

    assert caught.value.status_code == 404
    assert calls == []


@pytest.mark.asyncio
async def test_executor_exception_still_converges_to_failed(env, monkeypatch) -> None:
    """executor 抛错（含 ``on_round`` 里落库失败）一律收敛成 failed，泵不死。"""

    async def handler(*, on_round, should_stop, on_summary=None, record):
        raise RuntimeError("upstream exploded")

    install_executor(monkeypatch, handler)
    submitted = await run_queue.submit(user=ADMIN, conversation_oid=CONV, content="跑")
    await run_queue.kick()
    await wait_idle()

    row = env.runs.rows[submitted["run_id"]]
    assert row["status"] == "failed"
    assert row["stop_reason"] == "error"
    assert row["error_code"] == "AI_AGENT_INTERNAL_ERROR"
    # 只留类型名：异常正文可能带 DSN / 内部路径，而这一列直接进响应。
    assert row["error_message"] == "RuntimeError"


@pytest.mark.asyncio
async def test_heartbeat_ticks_between_rounds(env, monkeypatch) -> None:
    monkeypatch.setattr(run_queue, "heartbeat_seconds", lambda: 0.01)

    async def handler(*, on_round, should_stop, on_summary=None, record):
        # 一次「很长的上游调用」：期间没有任何轮边界，心跳仍要跳。
        await asyncio.sleep(0.08)
        return {"stop_reason": None, "rounds": 0}

    install_executor(monkeypatch, handler)
    await run_queue.submit(user=ADMIN, conversation_oid=CONV, content="慢查询")

    await run_queue.kick()
    await wait_idle()

    assert env.runs.heartbeats >= 2
    assert env.msg.batches == []  # 全程没有轮收尾，心跳与轮次无关


@pytest.mark.asyncio
async def test_cancel_stops_at_the_next_round_boundary(env, monkeypatch) -> None:
    submitted: dict = {}

    async def handler(*, on_round, should_stop, on_summary=None, record):
        # 用户点了取消：下一处 should_stop 必须看见。
        env.runs.rows[submitted["run_id"]]["cancel_requested"] = True
        assert await should_stop() is True
        return {"stop_reason": None, "rounds": 1}

    install_executor(monkeypatch, handler)
    submitted.update(
        await run_queue.submit(user=ADMIN, conversation_oid=CONV, content="跑")
    )

    await run_queue.kick()
    await wait_idle()

    row = env.runs.rows[submitted["run_id"]]
    assert row["status"] == "cancelled"
    assert row["stop_reason"] == "cancelled"


@pytest.mark.asyncio
async def test_executor_crash_becomes_failed_and_pump_keeps_running(env, monkeypatch) -> None:
    attempts = {"count": 0}

    async def handler(*, on_round, should_stop, on_summary=None, record):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise RuntimeError("boom")
        return {"stop_reason": None, "rounds": 1}

    install_executor(monkeypatch, handler)
    first = await run_queue.submit(user=ADMIN, conversation_oid=CONV, content="一")
    await run_queue.kick()
    await wait_idle()

    assert env.runs.rows[first["run_id"]]["status"] == "failed"
    assert env.runs.rows[first["run_id"]]["error_code"] == "AI_AGENT_INTERNAL_ERROR"
    # 异常正文不外泄：只留类型名。
    assert env.runs.rows[first["run_id"]]["error_message"] == "RuntimeError"

    second = await run_queue.submit(user=ADMIN, conversation_oid=CONV, content="二")
    await run_queue.kick()
    await wait_idle()

    assert env.runs.rows[second["run_id"]]["status"] == "succeeded"


@pytest.mark.asyncio
async def test_on_round_is_dropped_when_the_row_is_already_terminal(env, monkeypatch) -> None:
    submitted: dict = {}

    async def handler(*, on_round, should_stop, on_summary=None, record):
        # watchdog 抢先把它判成 interrupted。
        env.runs.rows[submitted["run_id"]]["status"] = "interrupted"
        await on_round(1, [{"role": "assistant", "content": "晚到的答案"}], None)
        return {"stop_reason": None, "rounds": 1}

    install_executor(monkeypatch, handler)
    submitted.update(
        await run_queue.submit(user=ADMIN, conversation_oid=CONV, content="跑")
    )

    await run_queue.kick()
    await wait_idle()

    # 终态行上不再落消息：已扣费的轮次与 run 记录必须对得上。
    assert [row for row in env.msg.rows if row["role"] == "assistant"] == []


# ---------------------------------------------------------------------------
# 取消 / 收敛 / watchdog
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cancel_a_queued_run_goes_terminal_immediately(env) -> None:
    submitted = await run_queue.submit(user=ADMIN, conversation_oid=CONV, content="跑")

    result = await run_queue.cancel(user=ADMIN, run_id=submitted["run_id"])

    assert result["status"] == "cancelled"
    assert env.runs.rows[submitted["run_id"]]["cancel_requested"] is True


@pytest.mark.asyncio
async def test_cancel_a_finished_run_is_409(env) -> None:
    row = env.runs.seed(conversation_oid=CONV, status="succeeded")

    with pytest.raises(AgentError) as caught:
        await run_queue.cancel(user=ADMIN, run_id=row["_id"])

    assert caught.value.code == run_queue.RUN_NOT_CANCELLABLE
    assert caught.value.status_code == 409


@pytest.mark.asyncio
async def test_cancel_of_foreign_run_is_404(env) -> None:
    row = env.runs.seed(conversation_oid=CONV, owner_oid=OTHER, status="running")

    with pytest.raises(AgentError) as caught:
        await run_queue.cancel(user=ADMIN, run_id=row["_id"])

    assert caught.value.code == run_queue.RUN_NOT_FOUND


@pytest.mark.asyncio
async def test_startup_recovery_interrupts_running_but_keeps_queued(env) -> None:
    running = env.runs.seed(conversation_oid="aa" * 16, status="running")
    queued = env.runs.seed(conversation_oid="bb" * 16, status="queued")

    recovered = await run_queue.recover_on_startup()

    assert recovered == 1
    assert env.runs.rows[running["_id"]]["status"] == "interrupted"
    # queued 保留：起泵后自然会续跑（它只有一个持久化副作用：那条用户消息）。
    assert env.runs.rows[queued["_id"]]["status"] == "queued"


@pytest.mark.asyncio
async def test_watchdog_cancels_the_coroutine_of_a_stale_run(env, monkeypatch) -> None:
    started = asyncio.Event()

    async def handler(*, on_round, should_stop, on_summary=None, record):
        started.set()
        await asyncio.sleep(30)  # 永远不自己结束：只能被 watchdog 掐掉
        return {"stop_reason": None, "rounds": 0}

    install_executor(monkeypatch, handler)
    submitted = await run_queue.submit(user=ADMIN, conversation_oid=CONV, content="卡住")
    await run_queue.kick()
    await asyncio.wait_for(started.wait(), timeout=2)

    # 本地心跳退到阈值之前（真实场景里是 ticker 停了）。
    run_queue._in_flight[submitted["run_id"]]["heartbeat"] = datetime.now() - timedelta(
        seconds=run_queue.stale_seconds() + 10
    )
    env.runs.stale_hits = 1

    hit = await run_queue.watchdog_tick()
    await wait_idle()

    assert hit == 1
    assert env.runs.interrupt_stale_calls  # cutoff 落在「阈值之前」
    assert env.runs.rows[submitted["run_id"]]["status"] == "interrupted"


@pytest.mark.asyncio
async def test_watchdog_leaves_fresh_runs_alone(env, monkeypatch) -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    async def handler(*, on_round, should_stop, on_summary=None, record):
        started.set()
        await release.wait()
        return {"stop_reason": None, "rounds": 0}

    install_executor(monkeypatch, handler)
    submitted = await run_queue.submit(user=ADMIN, conversation_oid=CONV, content="正常")
    await run_queue.kick()
    await asyncio.wait_for(started.wait(), timeout=2)

    await run_queue.watchdog_tick()

    assert submitted["run_id"] in run_queue._in_flight  # 心脏还在跳，不掐
    release.set()
    await wait_idle()


def test_stale_threshold_never_drops_below_the_floor(monkeypatch) -> None:
    monkeypatch.setattr(run_queue.settings, "AI_RUN_STALE_SECONDS", 10, raising=False)
    monkeypatch.setattr(run_queue.executor, "run_timeout_seconds", lambda: 300)

    # 阈值必须大于单次上游调用的最坏耗时，否则正常长轮次会被误杀。
    assert run_queue.stale_seconds() == 360

    monkeypatch.setattr(run_queue.settings, "AI_RUN_STALE_SECONDS", 900, raising=False)
    assert run_queue.stale_seconds() == 900

    monkeypatch.setattr(run_queue.executor, "run_timeout_seconds", lambda: 1200)
    monkeypatch.setattr(run_queue.settings, "AI_RUN_STALE_SECONDS", 900, raising=False)
    assert run_queue.stale_seconds() == 1260


# ---------------------------------------------------------------------------
# 读侧
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_detail_returns_only_newer_rounds(env) -> None:
    row = env.runs.seed(conversation_oid=CONV, status="running")
    keep = env.events.seed(request_id=row["_id"], round_no=1)
    newer = env.events.seed(request_id=row["_id"], round_no=2)
    read_event = env.events.seed(
        request_id=row["_id"], round_no=2, tool_kind="read", tool_name="fs_read"
    )
    # 同一条事件重复出现（同轮重发）：按 oid 去重。
    env.events.rows.append(dict(newer))

    detail = await run_queue.run_detail(user=ADMIN, run_id=row["_id"], after_round=1)

    assert detail["run"]["oid"] == row["_id"]
    assert [event["round_no"] for event in detail["events"]] == [2, 2]
    assert [event["oid"] for event in detail["events"]].count(newer["_id"]) == 1
    # 提案是「这次 run 的全部写工具提案」（不随 after_round 增减）：轮询增量只管
    # events，提案卡不该在中途消失。
    proposals = [item["oid"] for item in detail["proposals"]]
    assert proposals == [keep["_id"], newer["_id"]]
    assert read_event["_id"] not in proposals


@pytest.mark.asyncio
async def test_run_detail_of_foreign_run_is_404(env) -> None:
    row = env.runs.seed(conversation_oid=CONV, owner_oid=OTHER, status="running")

    with pytest.raises(AgentError) as caught:
        await run_queue.run_detail(user=ADMIN, run_id=row["_id"])

    assert caught.value.code == run_queue.RUN_NOT_FOUND


async def _async(value):
    return value


# ---------------------------------------------------------------------------
# R1：轮级增量消息（GET /runs/{id}?since_msg_seq=N）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_detail_returns_messages_after_seq_when_asked(env) -> None:
    """给了 ``since_msg_seq`` 才回 ``messages``：seq 升序、形状与 /messages 一致。"""
    from services.ai import conversations

    row = env.runs.seed(conversation_oid=CONV, status="running")
    env.msg.seed(conversation_oid=CONV, seq=1, role="user", content="查一下")
    newer = env.msg.seed(conversation_oid=CONV, seq=2, role="assistant", content="好了")

    detail = await run_queue.run_detail(
        user=ADMIN, run_id=row["_id"], since_msg_seq=1
    )

    assert [item["seq"] for item in detail["messages"]] == [2]
    assert detail["messages"][0]["content"] == "好了"
    # 形状必须与 GET /conversations/{oid}/messages 完全一致（同一份 view）。
    assert detail["messages"][0] == conversations.message_view(newer)


@pytest.mark.asyncio
async def test_run_detail_without_since_msg_seq_has_no_messages_key(env) -> None:
    """不传参数 = 老响应逐字不变（老客户端不该看到一个多出来的键）。"""
    row = env.runs.seed(conversation_oid=CONV, status="running")
    env.msg.seed(conversation_oid=CONV, seq=1, role="user", content="问")

    detail = await run_queue.run_detail(user=ADMIN, run_id=row["_id"])

    assert "messages" not in detail
    assert set(detail) == {"run", "events", "proposals"}


@pytest.mark.asyncio
async def test_run_detail_reads_the_event_table_only_once(env, monkeypatch) -> None:
    """事件与提案是同一张表的两种视图：一次查询喂两处（别再各查一遍）。"""
    row = env.runs.seed(conversation_oid=CONV, status="running")
    env.events.seed(request_id=row["_id"], round_no=1)
    calls: list[str] = []
    original = env.events.list_for_request

    async def counting(request_id: str):
        calls.append(str(request_id))
        return await original(request_id)

    monkeypatch.setattr(env.events, "list_for_request", counting)

    await run_queue.run_detail(user=ADMIN, run_id=row["_id"])

    assert calls == [row["_id"]]


# ---------------------------------------------------------------------------
# R2：模型由会话决定
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_executor_runs_the_conversation_model_not_the_submitted_one(
    env, monkeypatch
) -> None:
    """``AI_RUNS.model`` 只是提交记录；执行一律用 ``conversation.model``（R2）。"""
    env.conv.rows[CONV]["model"] = "lab/gpt-conv"
    calls = install_executor(
        monkeypatch,
        lambda **_: _async({"stop_reason": None, "rounds": 1, "content": "done"}),
    )

    submitted = await run_queue.submit(
        user=ADMIN, conversation_oid=CONV, content="跑", model="lab/other"
    )
    await run_queue.kick()
    await wait_idle()

    assert calls[0]["model"] == "lab/gpt-conv"
    # 提交时的 model 只留痕，不参与执行。
    assert env.runs.rows[submitted["run_id"]]["model"] == "lab/other"


# ---------------------------------------------------------------------------
# R3：思考强度随会话下行
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_executor_receives_the_conversation_thinking(env, monkeypatch) -> None:
    env.conv.rows[CONV]["thinking"] = "high"
    calls = install_executor(
        monkeypatch,
        lambda **_: _async({"stop_reason": None, "rounds": 1, "content": "done"}),
    )

    await run_queue.submit(user=ADMIN, conversation_oid=CONV, content="跑")
    await run_queue.kick()
    await wait_idle()

    assert calls[0]["thinking"] == "high"


# ---------------------------------------------------------------------------
# R3+：只读场景的**按次覆盖**（覆盖一次 run，不改会话行）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_overrides_win_over_the_conversation_values(env, monkeypatch) -> None:
    """``overrides`` 只作用于这一次 run：模型与档位都换掉，会话行原样不动。"""
    env.conv.rows[CONV]["model"] = "lab/gpt-conv"
    env.conv.rows[CONV]["thinking"] = "low"
    calls = install_executor(
        monkeypatch,
        lambda **_: _async({"stop_reason": None, "rounds": 1, "content": "done"}),
    )

    submitted = await run_queue.submit(
        user=ADMIN,
        conversation_oid=CONV,
        content="看看磁盘",
        overrides={"model": "lab/gpt-picked", "thinking": "high"},
    )
    await run_queue.kick()
    await wait_idle()

    assert calls[0]["model"] == "lab/gpt-picked"
    assert calls[0]["thinking"] == "high"
    # 会话行是「固定值」，按次覆盖不许回落成持久选择。
    assert env.conv.rows[CONV]["model"] == "lab/gpt-conv"
    assert env.conv.rows[CONV]["thinking"] == "low"
    # 覆盖随 run 一次性取走：进程内不留残留（下一个 run 不会莫名沿用）。
    assert run_queue._OVERRIDES == {}
    # 审计的 model 列与执行无关（提交记录口径不变）。
    assert env.runs.rows[submitted["run_id"]]["model"] is None


@pytest.mark.asyncio
async def test_run_overrides_do_not_leak_into_the_next_run(env, monkeypatch) -> None:
    env.conv.rows[CONV]["model"] = "lab/gpt-conv"
    calls = install_executor(
        monkeypatch,
        lambda **_: _async({"stop_reason": None, "rounds": 1, "content": "done"}),
    )

    await run_queue.submit(
        user=ADMIN,
        conversation_oid=CONV,
        content="第一次",
        overrides={"model": "lab/gpt-picked", "thinking": "high"},
    )
    await run_queue.kick()
    await wait_idle()

    await run_queue.submit(user=ADMIN, conversation_oid=CONV, content="第二次")
    await run_queue.kick()
    await wait_idle()

    assert calls[0]["model"] == "lab/gpt-picked"
    # 不传覆盖 = 与今天行为一致（沿用会话值），不是「沿用上一次覆盖」。
    assert calls[1]["model"] == "lab/gpt-conv"
    assert calls[1]["thinking"] is None


@pytest.mark.asyncio
async def test_empty_overrides_are_not_registered(env, monkeypatch) -> None:
    """空串/None 的覆盖 = 不覆盖（等价于不传），不占进程内表。"""
    calls = install_executor(
        monkeypatch,
        lambda **_: _async({"stop_reason": None, "rounds": 1, "content": "done"}),
    )

    await run_queue.submit(
        user=ADMIN,
        conversation_oid=CONV,
        content="跑",
        overrides={"model": "  ", "thinking": None},
    )
    await run_queue.kick()
    await wait_idle()

    assert calls[0]["model"] is None
    assert run_queue._OVERRIDES == {}


@pytest.mark.asyncio
async def test_a_deduped_submit_drops_the_override_it_registered(env, monkeypatch) -> None:
    """幂等命中的是**已有** run：新生成的 run_id 上的覆盖没人会取，必须清掉。"""
    install_executor(
        monkeypatch,
        lambda **_: _async({"stop_reason": None, "rounds": 1, "content": "done"}),
    )
    await run_queue.submit(
        user=ADMIN, conversation_oid=CONV, content="查一下", client_msg_id="c-1"
    )
    await run_queue.kick()
    await wait_idle()

    again = await run_queue.submit(
        user=ADMIN,
        conversation_oid=CONV,
        content="查一下",
        client_msg_id="c-1",
        overrides={"thinking": "high"},
    )

    assert again.get("deduped") is True
    assert run_queue._OVERRIDES == {}


# ---------------------------------------------------------------------------
# R5：被拒的问题在留存行上留痕
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_safety_rejection_marks_the_capture_as_refused(env, monkeypatch) -> None:
    class Verdict:
        allowed = False
        category = "jailbreak"
        unavailable = True

    async def denied(*_args, **_kwargs):
        return Verdict()

    monkeypatch.setattr(run_queue.safety_guard, "screen", denied)
    user = {"id": OWNER, "per": ["department"]}

    with pytest.raises(AgentError):
        await run_queue.submit(user=user, conversation_oid=CONV, content="帮我查一下期末成绩")

    assert env.prompt_audit.rows[0]["refused"] == 1
    # 判定没跑起来：不记违规，所以 warned 保持 0。
    assert env.prompt_audit.rows[0]["warned"] == 0


# ---------------------------------------------------------------------------
# 性能：每轮只查一次 run 状态（取消/中断仍然每轮可见）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_check_hits_the_database_once_per_round(env, monkeypatch) -> None:
    """同一轮的多个工具复用一份 run 行；轮收尾把它清掉（下轮必然重读）。

    5 个工具 = 过去 5 次 ``_runs().get``，现在 1 次；而「每轮可见」由轮边界的
    重新读取保证（下一个用例钉住它真的会重读）。
    """
    reads: list[str] = []
    original = env.runs.get

    async def counting(run_id: str, owner_oid: str):
        reads.append(str(run_id))
        return await original(run_id, owner_oid)

    monkeypatch.setattr(env.runs, "get", counting)

    async def handler(*, on_round, should_stop, on_summary=None, record):
        # 轮入口 1 次 + 5 个工具各 1 次 = 过去 6 次查询。
        assert await should_stop() is False
        for _ in range(5):
            assert await should_stop() is False
        await on_round(
            {
                "round_no": 1,
                "assistant": {"content": "好了", "tool_calls": [], "finish_reason": "stop"},
                "tools": [],
                "usage": {},
            }
        )
        return {"stop_reason": None, "rounds": 1, "content": "好了"}

    install_executor(monkeypatch, handler)
    await run_queue.submit(user=ADMIN, conversation_oid=CONV, content="跑")

    await run_queue.kick()
    await wait_idle()

    # 5 个工具的取消检查 + 轮收尾的消息写入前复核 = 2 次；改造前是 5 + 1 = 6 次。
    # （``submit`` 的活跃预检走 ``list_active``，不计在这里。）
    assert len(reads) == 2


@pytest.mark.asyncio
async def test_cancel_requested_between_rounds_is_seen(env, monkeypatch) -> None:
    """缓存只在轮内有效：下一轮的第一次检查必须看到新的取消标记。"""
    state: dict = {}

    async def handler(*, on_round, should_stop, on_summary=None, record):
        assert await should_stop() is False
        await on_round(
            {
                "round_no": 1,
                "assistant": {"content": "", "tool_calls": [], "finish_reason": "stop"},
                "tools": [],
                "usage": {},
            }
        )
        # 用户在第二轮开始前点了取消。
        env.runs.rows[state["run_id"]]["cancel_requested"] = True
        assert await should_stop() is True
        return {"stop_reason": None, "rounds": 2}

    install_executor(monkeypatch, handler)
    submitted = await run_queue.submit(user=ADMIN, conversation_oid=CONV, content="跑")
    state["run_id"] = submitted["run_id"]

    await run_queue.kick()
    await wait_idle()

    assert env.runs.rows[submitted["run_id"]]["status"] == "cancelled"


@pytest.mark.asyncio
async def test_submit_reuses_the_conversation_row_it_was_given(env, monkeypatch) -> None:
    """路由层刚读过的会话行传进来就不再查一遍（同一行、同一归属）。"""

    async def boom(*_args, **_kwargs):
        raise AssertionError("submit must not re-read the conversation")

    monkeypatch.setattr(run_queue.conversations, "require_row", boom)
    conversation = dict(env.conv.rows[CONV])

    result = await run_queue.submit(
        user=ADMIN, conversation_oid=CONV, content="跑", conversation=conversation
    )

    assert result["status"] == "queued"


@pytest.mark.asyncio
async def test_rejection_marks_warned_when_a_warning_was_issued(env, monkeypatch) -> None:
    """违规层发了警告 → 留存行 ``warned=1``（refused 由拒绝本身置位）。

    内容判定的唯一判据是 ``safety_guard.screen``（当前会话的模型判定），所以这里
    打桩的是它，而不是已经把门禁摘掉的关键词层。
    """
    from services.ai import safety_guard

    async def denied(*_args, **_kwargs):
        return safety_guard.ScreenResult(
            allowed=False, category="unrelated_request", reason="与平台无关"
        )

    async def warned(*_args, **_kwargs):
        return {"recorded": 1, "warned": True, "banned": True}

    monkeypatch.setattr(run_queue.safety_guard, "screen", denied)
    monkeypatch.setattr(run_queue.violation_guard, "record_agent_jailbreak", warned)

    with pytest.raises(AgentError):
        await run_queue.submit(
            user={"id": OWNER, "per": ["department"]},
            conversation_oid=CONV,
            content="陪我聊聊昨天看的电影",
        )

    assert env.prompt_audit.rows[0]["refused"] == 1
    assert env.prompt_audit.rows[0]["warned"] == 1
