"""A-H1：一轮的工具事件攒起来，与消息**同一个事务**落库。

背景：ADB 上一次 ``commit`` 是 0.5~0.95s。原来每个工具事件都是自己的写事务，
一个「3 轮 × 2 工具」的 run 就是 ~18 次提交，光等 commit 就把 run 拖成秒级。
事件是审计旁路，没有「必须先于消息可见」的业务要求，所以：

* 有 ``on_round``（会话路径）→ 本轮事件先进内存，轮收尾随 ``append_round`` 一起写；
* 没有 ``on_round``（裸执行器）→ 保持**逐条即时写**，否则没人负责落库，审计会整段
  消失；
* ``proposed`` 行不进缓冲：它由 ``proposal.propose`` 立刻写（前端要拿 oid 轮询），
  而且必须单行可见；
* 事件时间戳是**工具完成时**，不是轮收尾刷盘时——否则一轮里 5 个工具会被压成同一
  个瞬间；
* 轮询快照（``run_cache``）仍然即时追加：亚秒级的时间线不受影响；
* 事件写失败只 warning，消息与会话累加照旧提交（嵌套 savepoint 兜住）。

离线：上游 ``providers.chat_completion`` 脚本化，仓储全打桩。
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime

import pytest

from db import backends
from services.agent import executor, proposal, run_cache
from services.ai import config as ai_config
from services.ai import credentials, rate_limit, routing

USER = "000000000000000000000003"
ADMIN = {"id": USER, "per": ["admin"]}
MODEL = "gpt-x"

CAPTURED_AT = datetime(2026, 11, 15, 10, 20, 30)


def _entry() -> dict:
    return {
        "key": "shared:64b7f0c2e1a3b4c5d6e7fc01",
        "scope": "shared",
        "owner_oid": credentials.SHARED_OWNER,
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


class FakeEventsRepo:
    """只实现本车道用到的三个写口（逐条 / 批量 / 作废）。"""

    def __init__(self) -> None:
        self.rows: list[dict] = []
        self.inserts = 0

    async def insert(self, **fields) -> str:
        self.inserts += 1
        self.rows.append(dict(fields))
        return fields["oid"]

    async def insert_many(self, rows) -> int:
        self.inserts += 1
        for row in rows:
            self.rows.append(dict(row))
        return len(rows)


class FakeAuditRepo:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    async def insert(self, **fields) -> str:
        self.rows.append(fields)
        return "a" * 24


class FakeQuotaRepo:
    async def get(self, user_oid, period, kind):
        return None

    async def consume(self, user_oid, period, kind, amount, default_limit):
        return True, amount, default_limit


class FakeActivitiesRepo:
    async def list_raw(self, **kwargs):
        return [], 0


class FakeMembersRepo:
    async def count_for_activity(self, oid: str) -> int:
        return 0

    async def list_by_activity(self, oid: str, status=None):
        return []


class ScriptedUpstream:
    def __init__(self, responses: list[dict]) -> None:
        self.responses = list(responses)

    async def __call__(self, payload, entry, api_key):
        if not self.responses:
            raise AssertionError("upstream called more times than scripted")
        return self.responses.pop(0)


def _completion(content: str = "", *, tool_calls=None, total_tokens: int = 120) -> dict:
    message: dict = {"role": "assistant", "content": content}
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
    return {
        "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
        "usage": {
            "prompt_tokens": total_tokens - 20,
            "completion_tokens": 20,
            "total_tokens": total_tokens,
        },
    }


def _tool_call(name: str, arguments: dict | str, call_id: str = "call_1") -> dict:
    if isinstance(arguments, dict):
        import json

        arguments = json.dumps(arguments)
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": arguments},
    }


@pytest.fixture(autouse=True)
def _isolated_gateway(monkeypatch, quiet_ai_governance):
    credentials._cache.clear()
    routing.reset_state()
    rate_limit.reset_state()
    routing._semaphore = None
    routing._semaphore_size = 0
    run_cache.reset()

    async def catalog(_user_oid):
        return [_entry()]

    async def allowed_models(_user_oid):
        return {MODEL: "lab"}

    async def resolve_key(_entry):
        return "sk-unit-test-0123456789abcdef"

    monkeypatch.setattr(credentials, "catalog", catalog)
    monkeypatch.setattr(credentials, "resolve_api_key", resolve_key)
    monkeypatch.setattr(ai_config, "allowed_models_for", allowed_models)
    yield
    credentials._cache.clear()
    routing.reset_state()
    rate_limit.reset_state()
    routing._semaphore = None
    routing._semaphore_size = 0
    run_cache.reset()


@pytest.fixture
def repos(monkeypatch):
    events = FakeEventsRepo()
    monkeypatch.setattr(
        backends, "get_ai_agent_events_repo", lambda session=None: events
    )
    monkeypatch.setattr(backends, "get_ai_call_logs_repo", lambda session=None: FakeAuditRepo())
    monkeypatch.setattr(backends, "get_ai_quota_repo", lambda session=None: FakeQuotaRepo())
    monkeypatch.setattr(backends, "get_ai_prompt_audit_repo", lambda session=None: object())
    monkeypatch.setattr(
        backends, "get_activities_repo", lambda session=None: FakeActivitiesRepo()
    )
    monkeypatch.setattr(backends, "get_members_repo", lambda session=None: FakeMembersRepo())
    return events


def _two_tool_run(monkeypatch) -> ScriptedUpstream:
    """一轮 2 个只读工具 + 一轮回答：足够看「同轮事件进同一个缓冲」。"""
    upstream = ScriptedUpstream(
        [
            _completion(
                "",
                tool_calls=[
                    _tool_call("search_activities", {"query": "a"}, "call_1"),
                    _tool_call("search_activities", {"query": "b"}, "call_2"),
                ],
            ),
            _completion("查完了"),
        ]
    )
    monkeypatch.setattr("services.ai.providers.chat_completion", upstream)
    return upstream


async def _run(**kwargs):
    return await executor.run(
        user=ADMIN, messages=[{"role": "user", "content": "查活动"}], **kwargs
    )


# ---------------------------------------------------------------------------
# 缓冲 vs 即时
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_events_are_buffered_into_the_round_payload(monkeypatch, repos) -> None:
    events = repos
    _two_tool_run(monkeypatch)
    rounds: list[dict] = []

    result = await _run(model=MODEL, on_round=rounds.append)

    # 会话路径：executor 一条事件都不写（落库是 on_round 的事）。
    assert events.rows == []
    assert events.inserts == 0
    # 但事件照常出现在这一轮的载荷里，顺序 = 工具调用顺序。
    buffered = rounds[0]["events"]
    assert [item["status"] for item in buffered] == ["executed", "executed"]
    assert [item["tool_name"] for item in buffered] == [
        "search_activities",
        "search_activities",
    ]
    assert all(item["request_id"] == result["request_id"] for item in buffered)
    assert all(item["round_no"] == 1 for item in buffered)
    # 第二轮（无工具）没有事件。
    assert rounds[1]["events"] == []


@pytest.mark.asyncio
async def test_events_keep_the_tool_completion_timestamp(monkeypatch, repos) -> None:
    """时间戳在工具完成时定格：一轮多个工具不能被轮收尾压成同一瞬间。"""
    _two_tool_run(monkeypatch)
    rounds: list[dict] = []
    stamps: list[datetime] = []
    real = executor._write_event

    async def spy(**kwargs):
        before = len(kwargs.get("buffer") or ())
        await real(**kwargs)
        buffer = kwargs.get("buffer") or ()
        if len(buffer) > before:
            stamps.append(buffer[-1]["created_at"])

    monkeypatch.setattr(executor, "_write_event", spy)

    await _run(model=MODEL, on_round=rounds.append)

    assert len(stamps) == 2
    buffered = rounds[0]["events"]
    # 载荷里的时间戳就是完成时的那个对象（不是轮收尾的 ``now()``）。
    assert [item["created_at"] for item in buffered] == stamps
    assert all(isinstance(item["created_at"], datetime) for item in buffered)
    assert stamps[0] <= stamps[1]


@pytest.mark.asyncio
async def test_without_on_round_events_are_written_immediately(monkeypatch, repos) -> None:
    """裸执行器没有落库方：必须逐条即时写，审计不能因为少接一根线就消失。"""
    events = repos
    _two_tool_run(monkeypatch)

    result = await _run(model=MODEL)

    assert events.inserts == 2
    assert [row["status"] for row in events.rows] == ["executed", "executed"]
    # 返回值里的 events 形状不变（prompt 留存仍在用它）。
    assert [item["tool_name"] for item in result["events"]] == [
        "search_activities",
        "search_activities",
    ]


@pytest.mark.asyncio
async def test_proposed_rows_never_enter_the_buffer(monkeypatch, repos) -> None:
    """``proposed`` 行必须单行立刻可见（前端拿 oid 轮询提案状态）。"""
    events = repos
    buffer: list[dict] = []

    await executor._write_event(
        request_id="run-1",
        round_no=1,
        tool_name="update_activity",
        tool_kind="write",
        args_summary="{}",
        result_summary="已生成待确认提案",
        status="proposed",
        user_oid=USER,
        buffer=buffer,
    )
    assert buffer == []
    assert events.inserts == 1
    assert events.rows[0]["status"] == "proposed"

    # 对照：非 proposed 的同一份参数会被缓冲，而不是立刻写。
    await executor._write_event(
        request_id="run-1",
        round_no=1,
        tool_name="update_activity",
        tool_kind="write",
        args_summary="{}",
        result_summary="执行完成",
        status="executed",
        user_oid=USER,
        buffer=buffer,
    )
    assert [item["status"] for item in buffer] == ["executed"]
    assert events.inserts == 1


@pytest.mark.asyncio
async def test_proposal_rows_stay_out_of_the_buffer_in_a_real_write_round(
    monkeypatch, repos
) -> None:
    """真实写工具轮：提案行由 proposal.propose 立刻写，缓冲里只有旁路事件。"""
    from services.agent import tools

    events = repos
    written: list[dict] = []

    async def impl(_user, **_kwargs):
        raise AssertionError("写工具不该被直接执行")

    monkeypatch.setitem(
        tools.TOOLS,
        "fake_probe_write",
        tools.ToolDef(
            name="fake_probe_write",
            description="x",
            kind=tools.WRITE,
            params={
                "type": "object",
                "properties": {"path": {"type": "string", "description": "x"}},
                "required": ["path"],
            },
            permission=tools._always,
            impl=impl,
        ),
    )

    async def fake_propose(**kwargs):
        written.append(kwargs)
        # 真实实现自己插一行 ``status='proposed'``（同一个仓储）。
        oid = "p" * 32
        await events.insert(oid=oid, status="proposed", actor_oid=USER)
        return {
            "oid": oid,
            "summary": "把活动 1 改名",
            "expires_at": "2026-11-15 11:00:00",
            "requires_dual_auth": False,
        }

    monkeypatch.setattr(proposal, "propose", fake_propose)
    upstream = ScriptedUpstream(
        [
            _completion(
                "",
                tool_calls=[_tool_call("fake_probe_write", {"path": "/x"})],
            ),
            _completion("已提交提案"),
        ]
    )
    monkeypatch.setattr("services.ai.providers.chat_completion", upstream)
    rounds: list[dict] = []

    await _run(model=MODEL, on_round=rounds.append)

    assert written, "写工具必须走提案"
    assert [row["status"] for row in events.rows] == ["proposed"]
    # 缓冲里没有任何 proposed 行：提案不参与轮收尾的批量写。
    assert [item["status"] for item in rounds[0]["events"]] == []


@pytest.mark.asyncio
async def test_run_cache_still_sees_the_event_immediately(monkeypatch, repos) -> None:
    """轮询快照即时追加：缓冲只改落库时机，不改「亚秒内可见」。"""
    _two_tool_run(monkeypatch)
    run_cache.put("run-1", row={"status": "running"}, events=[], proposals=[])
    monkeypatch.setattr(executor, "uuid4", lambda: type("U", (), {"hex": "run-1"})())

    rounds: list[dict] = []
    await executor.run(
        user=ADMIN,
        messages=[{"role": "user", "content": "查活动"}],
        model=MODEL,
        request_id="run-1",
        on_round=rounds.append,
    )

    cached = run_cache.get("run-1")
    assert cached is not None
    assert [item["tool_name"] for item in cached["events"]] == [
        "search_activities",
        "search_activities",
    ]


# ---------------------------------------------------------------------------
# 轮事务：消息 + 事件一次提交，事件失败不带走消息
# ---------------------------------------------------------------------------


class _FakeSession:
    def __init__(self, *, fail_events: bool = False) -> None:
        self.commits = 0
        self.rollbacks = 0
        self.nested_entered = 0
        self.fail_events = fail_events

    def begin_nested(self):
        session = self

        @asynccontextmanager
        async def savepoint():
            session.nested_entered += 1
            try:
                yield session
            except Exception:
                session.rollbacks += 1
                raise

        return savepoint()

    async def commit(self) -> None:
        self.commits += 1


class _MessagesRepo:
    def __init__(self) -> None:
        self.batches: list[dict] = []

    async def append_batch(self, conversation_oid, rows, *, usage=None):
        self.batches.append({"conversation_oid": conversation_oid, "rows": rows})
        return [dict(row, seq=index + 1) for index, row in enumerate(rows)]


class _BoomEventsRepo:
    async def insert_many(self, rows):
        raise RuntimeError("event insert exploded")


def _install_round_env(monkeypatch, session, events_repo):
    from services.ai import conversations

    @asynccontextmanager
    async def scope():
        yield session

    monkeypatch.setattr(conversations, "session_scope", scope)
    messages = _MessagesRepo()
    monkeypatch.setattr(backends, "get_ai_messages_repo", lambda s=None: messages)
    monkeypatch.setattr(backends, "get_ai_agent_events_repo", lambda s=None: events_repo)
    return conversations, messages


@pytest.mark.asyncio
async def test_append_round_writes_messages_and_events_in_one_commit(monkeypatch) -> None:
    events = FakeEventsRepo()
    session = _FakeSession()
    conversations, messages = _install_round_env(monkeypatch, session, events)

    rows = [
        {"oid": "m1", "role": "assistant", "content": ""},
        {"oid": "m2", "role": "tool", "content": "结果"},
    ]
    buffered = [
        {
            "oid": "e1",
            "request_id": "run-1",
            "round_no": 1,
            "tool_name": "search_activities",
            "result_summary": "ok",
            "status": "executed",
            "actor_oid": USER,
            "created_at": CAPTURED_AT,
        }
    ]

    inserted = await conversations.append_round(
        "c" * 32, rows, usage={"total_tokens": 10}, events=buffered
    )

    assert [row["oid"] for row in inserted] == ["m1", "m2"]
    assert len(messages.batches) == 1
    # 一次提交：消息 + 事件 + 会话累加都在它之前。
    assert session.commits == 1
    assert [row["oid"] for row in events.rows] == ["e1"]
    # 时间戳原样带下去（不是落库时刻）。
    assert events.rows[0]["created_at"] == CAPTURED_AT


@pytest.mark.asyncio
async def test_event_failure_leaves_the_message_write_intact(
    monkeypatch, caplog
) -> None:
    session = _FakeSession(fail_events=True)
    conversations, messages = _install_round_env(monkeypatch, session, _BoomEventsRepo())
    caplog.set_level(logging.WARNING, logger="services.ai.conversations")

    rows = [{"oid": "m1", "role": "assistant", "content": "答案"}]

    inserted = await conversations.append_round(
        "c" * 32,
        rows,
        events=[{"oid": "e1", "status": "executed", "created_at": CAPTURED_AT}],
    )

    # 消息照常落库、照常提交：审计是旁路，不能决定业务数据可不可用。
    assert [row["oid"] for row in inserted] == ["m1"]
    assert len(messages.batches) == 1
    assert session.commits == 1
    # savepoint 真的用了（回滚到它，而不是把整个事务标脏）。
    assert session.nested_entered == 1
    assert session.rollbacks == 1
    assert any("round events write failed" in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_append_round_without_events_keeps_the_fast_path(monkeypatch) -> None:
    """无事件的轮次不额外开 session、不碰事件仓储（热路径保持原样）。"""
    called: list[str] = []

    class _Messages:
        async def append_batch(self, conversation_oid, rows, *, usage=None):
            called.append("messages")
            return [dict(row, seq=1) for row in rows]

    @asynccontextmanager
    async def boom():
        raise AssertionError("no events → no composite session")
        yield  # pragma: no cover

    from services.ai import conversations

    monkeypatch.setattr(conversations, "session_scope", boom)
    monkeypatch.setattr(backends, "get_ai_messages_repo", lambda s=None: _Messages())

    inserted = await conversations.append_round(
        "c" * 32, [{"oid": "m1", "role": "assistant", "content": "答案"}]
    )

    assert called == ["messages"]
    assert inserted[0]["seq"] == 1


# ---------------------------------------------------------------------------
# run_queue：载荷 → append_round 的搬运
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_on_round_hands_buffered_events_to_append_round(monkeypatch) -> None:
    from services.agent import run_queue

    seen: list[dict] = []

    async def fake_append(conversation_oid, rows, *, usage=None, events=None):
        seen.append({"rows": rows, "events": events})
        return rows

    async def still_running(_run_id, _owner):
        return True

    monkeypatch.setattr(run_queue.conversations, "append_round", fake_append)
    monkeypatch.setattr(run_queue, "_still_running", still_running)

    callback = run_queue._on_round("run-1", "c" * 32, USER, {})
    buffered = [
        {
            "oid": "e1",
            "round_no": 1,
            "tool_name": "search_activities",
            "result_summary": "ok",
            "status": "executed",
            "actor_oid": USER,
            "created_at": CAPTURED_AT,
        }
    ]
    await callback(
        {
            "round_no": 1,
            "assistant": {"content": "", "tool_calls": [{"id": "c1"}]},
            "tools": [
                {"tool_call_id": "c1", "tool_name": "search_activities", "content": "{}"}
            ],
            "usage": {"prompt": 10, "completion": 5, "total": 15},
            "events": buffered,
        }
    )

    assert seen and seen[0]["events"] == buffered


@pytest.mark.asyncio
async def test_on_round_without_an_events_key_still_works(monkeypatch) -> None:
    """手写载荷（测试替身 / 旧调用方）没有 ``events``：按空列表处理，不 500。"""
    from services.agent import run_queue

    seen: list = []

    async def fake_append(conversation_oid, rows, *, usage=None, events=None):
        seen.append(events)
        return rows

    async def still_running(_run_id, _owner):
        return True

    monkeypatch.setattr(run_queue.conversations, "append_round", fake_append)
    monkeypatch.setattr(run_queue, "_still_running", still_running)

    callback = run_queue._on_round("run-1", "c" * 32, USER, {})
    await callback(
        {
            "round_no": 1,
            "assistant": {"content": "答案", "tool_calls": None},
            "tools": [],
            "usage": {"prompt": 1, "completion": 1, "total": 2},
        }
    )

    assert seen == [[]]


def test_round_payload_defaults_events_to_an_empty_list() -> None:
    payload = executor._round_payload(
        round_no=1,
        content="",
        reasoning=None,
        finish_reason="stop",
        tool_calls=[],
        tools=[],
        tokens={},
        units=0,
    )
    assert payload["events"] == []

