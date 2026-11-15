"""``_invalidate_proposals`` 的集合式路径（删会话时作废待确认提案）。

``db/`` 在本批改动里属于另一条车道，所以「按 request_id 批量查 run + 批量 CAS」
落在服务层；这里用一个记录语句的假 session 钉住三件事：

* run 归属只查一次（一条 ``IN`` 查询），不按 proposal 逐条 ``get``；
* 提案只更新一次，且 ``WHERE status = 'proposed'`` 的 CAS 条件还在；
* 只有属于该会话的提案进入更新语句，返回值为实际命中行数。
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest

import services.agent.tool_impl  # noqa: F401 - 先初始化 agent 包，避免导入环
from services.ai import conversations

OWNER = "000000000000000000000001"
CONVERSATION = "a" * 32
OTHER_CONVERSATION = "b" * 32


class _Result:
    def __init__(self, rows=(), rowcount: int = 0) -> None:
        self._rows = list(rows)
        self.rowcount = rowcount

    def all(self):
        return self._rows


class _Session:
    def __init__(self, runs: dict[str, str], rowcount: int) -> None:
        self.runs = runs
        self.rowcount = rowcount
        self.statements: list = []
        self.commits = 0

    async def execute(self, stmt):
        self.statements.append(stmt)
        if getattr(stmt, "is_select", False):
            return _Result((oid, conv) for oid, conv in self.runs.items())
        return _Result(rowcount=self.rowcount)

    async def commit(self) -> None:
        self.commits += 1


class _Events:
    """真仓储实例有 ``_session``（能力探测依据）；逐条接口一律不许被调用。"""

    _session = None

    def __init__(self, rows) -> None:
        self.rows = rows

    async def list_by_actor(self, actor_oid, *, status=None, limit=20):
        assert str(actor_oid) == OWNER
        assert status == "proposed"
        return [dict(row) for row in self.rows[:limit]]

    async def update_status(self, *_args, **_kwargs):
        raise AssertionError("batch path must not update proposals one by one")


class _Runs:
    _session = None

    async def get(self, *_args, **_kwargs):
        raise AssertionError("batch path must not read runs one by one")


def _proposal(oid: str, request_id: str) -> dict:
    return {"_id": oid, "request_id": request_id, "status": "proposed"}


def _install(monkeypatch, runs: dict[str, str], events, rowcount: int = 1) -> _Session:
    session = _Session(runs, rowcount)

    @asynccontextmanager
    async def scope():
        yield session

    monkeypatch.setattr(conversations, "session_scope", scope)
    monkeypatch.setattr(conversations, "_events", lambda: events)
    monkeypatch.setattr(conversations, "_runs", lambda: _Runs())
    return session


def _params(stmt) -> dict:
    return stmt.compile().params


def _values(stmt) -> set:
    """绑定参数展平：``IN`` 列表在 ``params`` 里是一个 list。"""
    flat: set = set()
    for value in _params(stmt).values():
        if isinstance(value, (list, tuple, set)):
            flat.update(value)
        else:
            flat.add(value)
    return flat


@pytest.mark.asyncio
async def test_runs_are_read_once_and_only_matching_proposals_are_updated(monkeypatch) -> None:
    events = _Events(
        [
            _proposal("e" * 32, "r" * 24),
            _proposal("f" * 32, "s" * 24),
            _proposal("g" * 32, ""),
        ]
    )
    session = _install(
        monkeypatch,
        runs={"r" * 24: CONVERSATION, "s" * 24: OTHER_CONVERSATION},
        events=events,
        rowcount=1,
    )

    invalidated = await conversations._invalidate_proposals(OWNER, CONVERSATION)

    assert invalidated == 1
    selects = [stmt for stmt in session.statements if getattr(stmt, "is_select", False)]
    updates = [stmt for stmt in session.statements if getattr(stmt, "is_update", False)]
    assert len(selects) == 1
    assert len(updates) == 1
    # 一次 SELECT 覆盖全部 request_id：没有逐条 runs.get。
    assert {"r" * 24, "s" * 24} <= _values(selects[0])
    # 只有会话 A 的那条提案被 CAS 成 failed。
    values = _values(updates[0])
    assert "e" * 32 in values
    assert "f" * 32 not in values
    assert {"failed", "proposed", conversations.DELETED_PROPOSAL_SUMMARY} <= values
    assert session.commits == 1


@pytest.mark.asyncio
async def test_no_matching_run_skips_the_update(monkeypatch) -> None:
    events = _Events([_proposal("e" * 32, "r" * 24)])
    session = _install(
        monkeypatch,
        runs={"r" * 24: OTHER_CONVERSATION},
        events=events,
    )

    invalidated = await conversations._invalidate_proposals(OWNER, CONVERSATION)

    assert invalidated == 0
    assert not [stmt for stmt in session.statements if getattr(stmt, "is_update", False)]
    assert session.commits == 0


# ---------------------------------------------------------------------------
# A-M9：单语句下推（仓储有新接口时优先走它）
# ---------------------------------------------------------------------------

class _PushdownEvents(_Events):
    """真仓储的新接口：一条 UPDATE 覆盖该会话全部待确认提案。"""

    def __init__(self, *, rowcount: int = 4) -> None:
        super().__init__(rows=[])
        self.rowcount = rowcount
        self.calls: list[tuple] = []

    async def fail_proposed_for_conversation(self, conversation_oid, *, result_summary=None):
        self.calls.append((str(conversation_oid), result_summary))
        return self.rowcount

    async def list_by_actor(self, *args, **kwargs):  # pragma: no cover - 必须不被调用
        raise AssertionError("pushdown path must not scan a page by actor")

    async def update_status(self, *_args, **_kwargs):  # pragma: no cover
        raise AssertionError("pushdown path must not update proposals one by one")


@pytest.mark.asyncio
async def test_pushdown_is_used_when_the_repo_exposes_it(monkeypatch) -> None:
    """有下推接口时：不扫 500 行、不逐条 CAS，只消费一条 UPDATE 的 rowcount。"""
    events = _PushdownEvents(rowcount=7)
    session = _install(monkeypatch, runs={}, events=events)

    invalidated = await conversations._invalidate_proposals(OWNER, CONVERSATION)

    assert invalidated == 7
    assert events.calls == [(CONVERSATION, conversations.DELETED_PROPOSAL_SUMMARY)]
    # 服务层自己一条语句都没发（连 run 归属查询都不需要）。
    assert session.statements == []


@pytest.mark.asyncio
async def test_pushdown_is_skipped_without_a_shared_session(monkeypatch) -> None:
    """内存替身（无 ``_session``）即使有同名方法也不该走 SQL 下推。"""

    class _MemoryEvents:
        def __init__(self) -> None:
            self.scanned = False

        async def list_by_actor(self, actor_oid, *, status=None, limit=20):
            self.scanned = True
            return []

        async def fail_proposed_for_conversation(self, *_args, **_kwargs):  # pragma: no cover
            raise AssertionError("memory repo has no shared session to push down into")

    events = _MemoryEvents()
    monkeypatch.setattr(conversations, "_events", lambda: events)
    monkeypatch.setattr(conversations, "_runs", lambda: _Runs())

    invalidated = await conversations._invalidate_proposals(OWNER, CONVERSATION)

    assert invalidated == 0
    assert events.scanned is True


@pytest.mark.asyncio
async def test_row_by_row_fallback_still_works(monkeypatch) -> None:
    """既没有下推也没有共享 session：逐条路径保持可用（内存实现的老路）。"""
    updated: list[tuple] = []

    class _PlainEvents:
        async def list_by_actor(self, actor_oid, *, status=None, limit=20):
            return [_proposal("e" * 32, "r" * 24)]

        async def update_status(self, oid, status, *, from_status="proposed", result_summary=None):
            updated.append((oid, status, from_status, result_summary))
            return True

    class _PlainRuns:
        async def get(self, request_id, owner_oid):
            return {"conversation_oid": CONVERSATION}

    monkeypatch.setattr(conversations, "_events", lambda: _PlainEvents())
    monkeypatch.setattr(conversations, "_runs", lambda: _PlainRuns())

    invalidated = await conversations._invalidate_proposals(OWNER, CONVERSATION)

    assert invalidated == 1
    assert updated == [
        ("e" * 32, "failed", "proposed", conversations.DELETED_PROPOSAL_SUMMARY)
    ]
