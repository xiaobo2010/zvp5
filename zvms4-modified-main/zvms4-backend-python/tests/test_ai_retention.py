"""``AI_CALL_LOGS`` 的保留期清理（接进 ``tasks.retention`` 的既有编排）。

覆盖：
* 批循环语义（与另外两张表同一份实现）：满批继续、短批提前停、触顶
  ``RETENTION_MAX_BATCHES`` 停；
* 保留窗口的防御式读法：``config`` 缺键 / 非法值回退 90；
* ``purge_batch`` 的 SQL 形状：``created_at <`` + ``ROWNUM <=``，且**不含**
  ``FETCH FIRST``（Oracle 的 ``DELETE`` 不接受它）；
* ``run_retention()`` 的返回结构里有 ``ai_call_logs`` 的删除数，审计行能看出
  三张表各自的删除数；0 行的表不会多写审计行（一次 run 仍然只有一行审计）。

全部离线：三张表的仓储都是 fake，``backends.get_*_repo`` 被 monkeypatch，
不建引擎、不连库。

跑法：``.venv/bin/python -m pytest tests/test_ai_retention.py -q``
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

import pytest
from sqlalchemy.dialects import oracle

import config
from db import backends
from db.repos.oracle import ai_call_logs as ai_call_logs_repo
from tasks import retention

TS = datetime(2026, 10, 23, 1, 2, 3)
DIALECT = oracle.dialect()
BATCH_SIZE = retention.DEFAULT_RETENTION_BATCH_SIZE


# ---------------------------------------------------------------------------
# fakes
# ---------------------------------------------------------------------------


class _FakeLogsRepo:
    """Fake ``AppLogsRepo``：只做记账 + 审计写入。"""

    def __init__(self, rowcounts: list[int] | None = None, remaining: int = 0) -> None:
        self.rowcounts = list(rowcounts or [])
        self.remaining = remaining
        self.purges: list[tuple[datetime, int]] = []
        self.inserted: list[dict] = []

    async def purge_batch(self, cutoff: datetime, batch_size: int) -> int:
        self.purges.append((cutoff, batch_size))
        return self.rowcounts.pop(0) if self.rowcounts else 0

    async def count_expired(self, cutoff: datetime) -> int:
        return self.remaining

    async def insert(
        self,
        url: str,
        user: str,
        data: str,
        timestamp: float,
        ltype: str,
        level: str,
        xuehai_id: str,
    ) -> None:
        self.inserted.append(
            {
                "url": url,
                "user": user,
                "data": data,
                "timestamp": timestamp,
                "type": ltype,
                "level": level,
                "xuehai_id": xuehai_id,
            }
        )


class _FakeTasksRepo:
    def __init__(self, rowcounts: list[int] | None = None, remaining: int = 0) -> None:
        self.rowcounts = list(rowcounts or [])
        self.remaining = remaining
        self.purges: list[tuple[datetime, int]] = []

    async def purge_finished_batch(self, cutoff: datetime, batch_size: int) -> int:
        self.purges.append((cutoff, batch_size))
        return self.rowcounts.pop(0) if self.rowcounts else 0

    async def count_expired_finished(self, cutoff: datetime) -> int:
        return self.remaining


class _FakeAICallLogsRepo:
    """Fake ``AICallLogsRepo``：只实现保留清理用到的两个方法。"""

    def __init__(self, rowcounts: list[int] | None = None, remaining: int = 0) -> None:
        self.rowcounts = list(rowcounts or [])
        self.remaining = remaining
        self.purges: list[tuple[datetime, int]] = []

    async def purge_batch(self, cutoff: datetime, batch_size: int) -> int:
        self.purges.append((cutoff, batch_size))
        return self.rowcounts.pop(0) if self.rowcounts else 0

    async def count_expired(self, cutoff: datetime) -> int:
        return self.remaining


class _FakeAIAgentEventsRepo(_FakeAICallLogsRepo):
    """Fake ``AIAgentEventsRepo``：与 ``AI_CALL_LOGS`` 同款单批量删除。"""


class _FakeAIPromptAuditRepo:
    """Fake ``AIPromptAuditRepo``：清理走 ``purge_before`` / ``count_expired``。"""

    def __init__(self, rowcounts: list[int] | None = None, remaining: int = 0) -> None:
        self.rowcounts = list(rowcounts or [])
        self.remaining = remaining
        self.purges: list[tuple[datetime, int]] = []

    async def purge_before(self, cutoff: datetime, batch_size: int) -> int:
        self.purges.append((cutoff, batch_size))
        return self.rowcounts.pop(0) if self.rowcounts else 0

    async def count_expired(self, cutoff: datetime) -> int:
        return self.remaining


@pytest.fixture()
def repos(monkeypatch):
    logs = _FakeLogsRepo()
    tasks = _FakeTasksRepo()
    ai = _FakeAICallLogsRepo()
    events = _FakeAIAgentEventsRepo()
    prompts = _FakeAIPromptAuditRepo()
    monkeypatch.setattr(backends, "get_applogs_repo", lambda session=None: logs)
    monkeypatch.setattr(backends, "get_tasks_repo", lambda session=None: tasks)
    monkeypatch.setattr(backends, "get_ai_call_logs_repo", lambda session=None: ai)
    monkeypatch.setattr(backends, "get_ai_agent_events_repo", lambda session=None: events)
    monkeypatch.setattr(backends, "get_ai_prompt_audit_repo", lambda session=None: prompts)
    return logs, tasks, ai, events, prompts


# ---------------------------------------------------------------------------
# 批循环：满批继续 / 短批停 / 触顶停
# ---------------------------------------------------------------------------


def test_full_batches_continue_until_a_short_one(repos):
    _, _, ai, events, prompts = repos
    ai.rowcounts = [BATCH_SIZE, BATCH_SIZE, 7]
    events.rowcounts = [0]
    prompts.rowcounts = [0]

    result = asyncio.run(retention.run_retention())

    assert len(ai.purges) == 3
    assert result["ai_call_logs"]["deleted"] == BATCH_SIZE * 2 + 7
    # 五张表各一批（其余 0 行），共 3 + 1 + 1 + 1 + 1 = 7 批。
    assert result["batches"] == 7


def test_a_short_first_batch_stops_the_drain(repos):
    _, _, ai, events, prompts = repos
    ai.rowcounts = [7]
    events.rowcounts = [0]
    prompts.rowcounts = [0]

    result = asyncio.run(retention.run_retention())

    assert len(ai.purges) == 1
    assert result["ai_call_logs"]["deleted"] == 7


def test_the_batch_ceiling_stops_the_drain(repos, monkeypatch):
    _, _, ai, events, prompts = repos
    monkeypatch.setattr(config, "RETENTION_MAX_BATCHES", 2, raising=False)
    ai.rowcounts = [BATCH_SIZE, BATCH_SIZE, BATCH_SIZE]
    ai.remaining = 11
    events.rowcounts = [0]
    prompts.rowcounts = [0]

    result = asyncio.run(retention.run_retention())

    assert len(ai.purges) == 2
    assert result["ai_call_logs"]["deleted"] == BATCH_SIZE * 2
    # 触顶后仍有剩行：结果里报的是跑完之后的真实计数。
    assert result["ai_call_logs"]["remaining"] == 11


def test_every_batch_uses_the_same_batch_size_and_cutoff(repos):
    _, _, ai, events, prompts = repos
    ai.rowcounts = [BATCH_SIZE, 1]
    events.rowcounts = [0]
    prompts.rowcounts = [0]

    asyncio.run(retention.run_retention())

    assert [batch for _cutoff, batch in ai.purges] == [BATCH_SIZE, BATCH_SIZE]
    assert ai.purges[0][0] == ai.purges[1][0]


# ---------------------------------------------------------------------------
# 保留窗口：防御式读法
# ---------------------------------------------------------------------------


def test_default_window_is_ninety_days():
    assert retention.DEFAULT_AI_CALL_LOGS_RETENTION_DAYS == 90


def test_window_falls_back_when_the_config_key_is_missing(monkeypatch):
    monkeypatch.delattr(config, "AI_CALL_LOGS_RETENTION_DAYS", raising=False)

    assert retention.ai_call_logs_retention_days() == 90


@pytest.mark.parametrize("bad", ["ninety", "3.5", None, 0, -5, object()])
def test_window_falls_back_on_invalid_values(monkeypatch, bad):
    monkeypatch.setattr(config, "AI_CALL_LOGS_RETENTION_DAYS", bad, raising=False)

    assert retention.ai_call_logs_retention_days() == 90


@pytest.mark.parametrize("value,expected", [(30, 30), ("7", 7), (1, 1)])
def test_window_honours_positive_config(monkeypatch, value, expected):
    monkeypatch.setattr(config, "AI_CALL_LOGS_RETENTION_DAYS", value, raising=False)

    assert retention.ai_call_logs_retention_days() == expected


def test_run_uses_the_configured_window_for_the_ai_cutoff(repos, monkeypatch):
    _, _, ai, events, prompts = repos
    monkeypatch.setattr(config, "AI_CALL_LOGS_RETENTION_DAYS", 14, raising=False)
    events.rowcounts = [0]
    prompts.rowcounts = [0]

    before = datetime.now()
    result = asyncio.run(retention.run_retention())
    after = datetime.now()

    cutoff = datetime.fromisoformat(result["ai_call_logs"]["cutoff"])
    assert before - timedelta(days=14) <= cutoff <= after - timedelta(days=14)
    assert ai.purges[0][0] == cutoff


# ---------------------------------------------------------------------------
# purge_batch 的 SQL 形状（离线编译）
# ---------------------------------------------------------------------------


def _sql(stmt) -> str:
    return " ".join(
        str(
            stmt.compile(dialect=DIALECT, compile_kwargs={"literal_binds": True})
        ).split()
    )


def test_purge_batch_sql_is_a_bounded_delete():
    sql = _sql(ai_call_logs_repo._purge_batch_stmt(TS, 500))

    assert sql.upper().startswith("DELETE FROM AI_CALL_LOGS")
    assert "created_at <" in sql
    assert "ROWNUM <= 500" in sql


def test_purge_batch_sql_never_uses_fetch_first():
    """Oracle 的 ``DELETE`` 不接受 ``FETCH FIRST``（app_logs 注释里已写明）。"""
    sql = _sql(ai_call_logs_repo._purge_batch_stmt(TS, 500)).upper()

    assert "FETCH FIRST" not in sql
    assert "ROWNUM" in sql


# ---------------------------------------------------------------------------
# 返回结构 / 进度 / 审计
# ---------------------------------------------------------------------------


def test_result_carries_the_ai_table_counts(repos):
    _, _, ai, events, prompts = repos
    ai.rowcounts = [3]
    ai.remaining = 4
    events.rowcounts = [0]
    prompts.rowcounts = [0]

    result = asyncio.run(retention.run_retention())

    assert set(result) == {
        "app_logs",
        "export_tasks",
        "ai_call_logs",
        "ai_agent_events",
        "ai_prompt_audit",
        "batches",
    }
    assert set(result["ai_call_logs"]) == {"cutoff", "deleted", "remaining"}
    assert result["ai_call_logs"]["deleted"] == 3
    assert result["ai_call_logs"]["remaining"] == 4
    assert datetime.fromisoformat(result["ai_call_logs"]["cutoff"])
    # 工具事件与调用流水同窗；正文留存用自己的窗口（默认 30 天）。
    assert result["ai_agent_events"]["cutoff"] == result["ai_call_logs"]["cutoff"]
    prompt_cutoff = datetime.fromisoformat(result["ai_prompt_audit"]["cutoff"])
    assert abs((datetime.now() - prompt_cutoff) - timedelta(days=30)) < timedelta(minutes=1)


def test_progress_callback_reports_the_ai_phase_and_counters(repos):
    _, _, ai, events, prompts = repos
    ai.rowcounts = [5]
    ai.remaining = 5
    events.rowcounts = [0]
    prompts.rowcounts = [0]

    seen: list[tuple[str, dict]] = []

    def _on_progress(phase: str, progress: dict) -> None:
        seen.append((phase, {k: dict(v) if isinstance(v, dict) else v for k, v in progress.items()}))

    asyncio.run(retention.run_retention(on_progress=_on_progress))

    phases = [phase for phase, _ in seen]
    assert retention.PHASE_PURGING_AI_CALL_LOGS in phases
    # 既有 phase 的取值不变（只是多了一个新表的 phase）。
    assert retention.PHASE_PURGING_APP_LOGS in phases
    assert retention.PHASE_PURGING_EXPORT_TASKS in phases
    final = seen[-1][1]
    assert final["ai_call_logs"] == {"deleted": 5, "remaining": 0}


def test_audit_row_mentions_all_three_tables(repos):
    logs, _, ai, events, prompts = repos
    ai.rowcounts = [2]
    ai.remaining = 0
    events.rowcounts = [4]
    prompts.rowcounts = [6]

    result = asyncio.run(retention.run_retention())
    asyncio.run(retention.write_scheduler_audit(result))

    assert len(logs.inserted) == 1
    row = logs.inserted[0]
    assert row["type"] == retention.AUDIT_TYPE
    assert row["level"] == "warning"
    assert row["user"] == retention.SCHEDULER_USER
    assert "app_logs 删除 0 行" in row["data"]
    assert "export_tasks 删除 0 行" in row["data"]
    assert "ai_call_logs 删除 2 行" in row["data"]
    assert "ai_agent_events 删除 4 行" in row["data"]
    assert "ai_prompt_audit 删除 6 行" in row["data"]


def test_empty_tables_do_not_add_audit_noise(repos):
    """三张表都没得删：仍然只有一行审计（语义不变，只是多了一个 0）。"""
    logs, _, ai, events, prompts = repos
    ai.rowcounts = [0]
    events.rowcounts = [0]
    prompts.rowcounts = [0]

    result = asyncio.run(retention.run_retention())
    asyncio.run(retention.write_scheduler_audit(result))

    assert len(logs.inserted) == 1
    assert "ai_call_logs 删除 0 行" in logs.inserted[0]["data"]


# ---------------------------------------------------------------------------
# 仓储：一次短 session + 一次 commit（仅自有 session 才 commit）
# ---------------------------------------------------------------------------


class _Result:
    def __init__(self, rowcount: int | None) -> None:
        self.rowcount = rowcount


class _FakeSession:
    def __init__(self, rowcount: int | None) -> None:
        self.rowcount = rowcount
        self.executed: list = []
        self.commits = 0

    async def execute(self, stmt):
        self.executed.append(stmt)
        return _Result(self.rowcount)

    async def commit(self) -> None:
        self.commits += 1


@asynccontextmanager
async def _scope(session):
    yield session


def test_purge_batch_commits_once_per_call(monkeypatch):
    session = _FakeSession(rowcount=42)
    monkeypatch.setattr(ai_call_logs_repo, "session_scope", lambda: _scope(session))

    deleted = asyncio.run(
        ai_call_logs_repo.OracleAICallLogsRepo().purge_batch(TS, 100)
    )

    assert deleted == 42
    assert session.commits == 1
    assert len(session.executed) == 1


def test_purge_batch_does_not_commit_an_injected_session():
    session = _FakeSession(rowcount=1)

    deleted = asyncio.run(
        ai_call_logs_repo.OracleAICallLogsRepo(session).purge_batch(TS, 100)
    )

    assert deleted == 1
    assert session.commits == 0


def test_purge_batch_treats_a_missing_rowcount_as_zero(monkeypatch):
    session = _FakeSession(rowcount=None)
    monkeypatch.setattr(ai_call_logs_repo, "session_scope", lambda: _scope(session))

    assert (
        asyncio.run(ai_call_logs_repo.OracleAICallLogsRepo().purge_batch(TS, 100))
        == 0
    )


# ---------------------------------------------------------------------------
# 正文留存 / 工具事件的保留期（V033 的两张表）
# ---------------------------------------------------------------------------


def test_prompt_default_window_is_thirty_days():
    assert retention.DEFAULT_AI_PROMPT_RETENTION_DAYS == 30


def test_prompt_window_falls_back_when_the_config_key_is_missing(monkeypatch):
    monkeypatch.delattr(config, "AI_PROMPT_RETENTION_DAYS", raising=False)

    assert retention.ai_prompt_retention_days() == 30


@pytest.mark.parametrize("bad", ["thirty", "3.5", None, 0, -5, object()])
def test_prompt_window_falls_back_on_invalid_values(monkeypatch, bad):
    monkeypatch.setattr(config, "AI_PROMPT_RETENTION_DAYS", bad, raising=False)

    assert retention.ai_prompt_retention_days() == 30


@pytest.mark.parametrize("value,expected", [(7, 7), ("14", 14), (1, 1)])
def test_prompt_window_honours_positive_config(monkeypatch, value, expected):
    monkeypatch.setattr(config, "AI_PROMPT_RETENTION_DAYS", value, raising=False)

    assert retention.ai_prompt_retention_days() == expected


def test_agent_events_share_the_call_logs_window():
    """工具事件与调用流水同窗：一边删了另一边还在，会让提案历史悬空。"""
    assert (
        retention.ai_agent_events_retention_days()
        == retention.ai_call_logs_retention_days()
    )


def test_run_uses_the_configured_prompt_window(repos, monkeypatch):
    _, _, ai, events, prompts = repos
    monkeypatch.setattr(config, "AI_PROMPT_RETENTION_DAYS", 3, raising=False)
    ai.rowcounts = [0]
    events.rowcounts = [0]
    prompts.rowcounts = [2]

    before = datetime.now()
    result = asyncio.run(retention.run_retention())
    after = datetime.now()

    cutoff = datetime.fromisoformat(result["ai_prompt_audit"]["cutoff"])
    assert before - timedelta(days=3) <= cutoff <= after - timedelta(days=3)
    assert prompts.purges[0][0] == cutoff
    assert result["ai_prompt_audit"]["deleted"] == 2


def test_prompt_purge_sql_is_a_bounded_delete():
    from db.repos.oracle import ai_prompt_audit as prompt_repo

    sql = _sql(prompt_repo._purge_before_stmt(TS, 500))

    assert sql.upper().startswith("DELETE FROM AI_PROMPT_AUDIT")
    assert "created_at <" in sql
    assert "ROWNUM <= 500" in sql
    # Oracle 的 DELETE 不接受 FETCH FIRST（同 app_logs / ai_call_logs）。
    assert "FETCH FIRST" not in sql.upper()


def test_agent_events_purge_sql_is_a_bounded_delete():
    from db.repos.oracle import ai_agent_events as events_repo

    sql = _sql(events_repo._purge_batch_stmt(TS, 500))

    assert sql.upper().startswith("DELETE FROM AI_AGENT_EVENTS")
    assert "created_at <" in sql
    assert "ROWNUM <= 500" in sql
    assert "FETCH FIRST" not in sql.upper()
