"""Offline tests for the retention job (design §2 / C5).

Pins:

* the config read is **import-safe** — ``config.py`` is a deployment-local file,
  so a plain ``from config import KEY`` would raise ``ImportError`` at import
  time and stop the whole application from starting. Missing / non-integer /
  non-positive values must fall back to the code default;
* the cutoff arithmetic is exact for a fixed ``now``, and the two tables share
  one ``now``;
* the batching loop: a short batch ends the drain, the batch ceiling stops it,
  and each batch is exactly one short session plus one commit;
* the process-wide lock: a second concurrent run is refused
  (:class:`RetentionBusy`) instead of queueing, and the scheduled entry point
  skips quietly;
* the scheduled entry point never raises and writes exactly one
  ``retention_purge`` / ``warning`` audit row attributed to ``scheduler``;
* ``main.startup_event`` registers ``daily_retention_purge`` at 03:30 HKT and
  leaves the existing jobs alone.

Everything is offline. The batching tests drive the **real** repositories with a
faked ``session_scope`` (so no engine is ever created) and assert the commit
count; the audit/lock tests use fake repositories. ``main`` is imported only for
the registration assertions (see ``conftest.py`` for the ``sys.path`` setup).

Run from the repository root::

    ./.venv/bin/python -m pytest tests/golden/retention/ -q
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

import pytest
from sqlalchemy.dialects import oracle

import config
from db import backends
from db.repos.oracle import ai_agent_events as ai_agent_events_repo
from db.repos.oracle import ai_call_logs as ai_call_logs_repo
from db.repos.oracle import ai_prompt_audit as ai_prompt_audit_repo
from db.repos.oracle import app_logs as app_logs_repo
from db.repos.oracle import tasks as tasks_repo
from db.repos.oracle.ai_agent_events import OracleAIAgentEventsRepo
from db.repos.oracle.ai_call_logs import OracleAICallLogsRepo
from db.repos.oracle.ai_prompt_audit import OracleAIPromptAuditRepo
from db.repos.oracle.app_logs import OracleAppLogsRepo
from db.repos.oracle.tasks import OracleTasksRepo
from tasks import retention
from tasks.retention import (
    DEFAULT_AI_CALL_LOGS_RETENTION_DAYS,
    DEFAULT_APP_LOGS_RETENTION_DAYS,
    DEFAULT_EXPORT_TASKS_RETENTION_DAYS,
    DEFAULT_RETENTION_BATCH_SIZE,
    DEFAULT_RETENTION_MAX_BATCHES,
    RetentionBusy,
    ai_call_logs_retention_days,
    app_logs_retention_days,
    export_tasks_retention_days,
    purge_expired_records,
    purge_now,
    retention_batch_size,
    retention_cutoff,
    retention_cutoffs,
    retention_max_batches,
    run_retention,
)

# A fixed instant so cutoff arithmetic is asserted exactly.
NOW = datetime(2026, 5, 1, 3, 30, 0)


# ---------------------------------------------------------------------------
# fakes
# ---------------------------------------------------------------------------


class _Result:
    """``execute`` result: ``rowcount`` for a DELETE, ``scalar_one`` for a COUNT."""

    def __init__(self, rowcount: int | None = None, scalar: int | None = None) -> None:
        self.rowcount = rowcount
        self._scalar = scalar

    def scalar_one(self) -> int | None:
        return self._scalar


class _FakeSession:
    """Fake AsyncSession: replays DELETE rowcounts, counts COUNT calls/commits."""

    def __init__(self, rowcounts: list[int] | None = None, count: int = 0) -> None:
        self.rowcounts = list(rowcounts or [])
        self.count = count
        self.deletes: list = []
        self.counts = 0
        self.commits = 0

    async def execute(self, stmt) -> _Result:
        if getattr(stmt, "is_select", False):
            self.counts += 1
            return _Result(scalar=self.count)
        self.deletes.append(stmt)
        return _Result(rowcount=self.rowcounts.pop(0) if self.rowcounts else 0)

    async def commit(self) -> None:
        self.commits += 1


@asynccontextmanager
async def _scope(session):
    yield session


def _wire(monkeypatch, module, session: _FakeSession) -> None:
    """Replace the module's ``session_scope`` so the repo "owns" the fake session."""
    monkeypatch.setattr(module, "session_scope", lambda: _scope(session))


def _sql(stmt) -> str:
    """Compile a recorded statement to Oracle SQL text (no DB)."""
    return str(
        stmt.compile(dialect=oracle.dialect(), compile_kwargs={"literal_binds": True})
    )


def _real_repos(
    monkeypatch,
    logs_rowcounts: list[int] | None = None,
    logs_count: int = 0,
    tasks_rowcounts: list[int] | None = None,
    tasks_count: int = 0,
    ai_rowcounts: list[int] | None = None,
    ai_count: int = 0,
) -> tuple[_FakeSession, _FakeSession, _FakeSession]:
    """Real repositories over fake sessions; returns the three original sessions.

    V033 起 ``run_retention`` 还会 drain ``ai_agent_events`` / ``ai_prompt_audit``，
    所以这两个 repo 也必须接上假 session——否则它们会自己建 engine（bequeath
    在 thin mode 下直接 NotSupportedError）。返回值保持三元组，既有断言不用改。
    """
    logs_session = _FakeSession(logs_rowcounts, logs_count)
    tasks_session = _FakeSession(tasks_rowcounts, tasks_count)
    ai_session = _FakeSession(ai_rowcounts, ai_count)
    events_session = _FakeSession()
    prompt_session = _FakeSession()
    _wire(monkeypatch, app_logs_repo, logs_session)
    _wire(monkeypatch, tasks_repo, tasks_session)
    _wire(monkeypatch, ai_call_logs_repo, ai_session)
    _wire(monkeypatch, ai_agent_events_repo, events_session)
    _wire(monkeypatch, ai_prompt_audit_repo, prompt_session)
    monkeypatch.setattr(backends, "get_applogs_repo", lambda session=None: OracleAppLogsRepo())
    monkeypatch.setattr(backends, "get_tasks_repo", lambda session=None: OracleTasksRepo())
    monkeypatch.setattr(
        backends, "get_ai_call_logs_repo", lambda session=None: OracleAICallLogsRepo()
    )
    monkeypatch.setattr(
        backends,
        "get_ai_agent_events_repo",
        lambda session=None: OracleAIAgentEventsRepo(),
    )
    monkeypatch.setattr(
        backends,
        "get_ai_prompt_audit_repo",
        lambda session=None: OracleAIPromptAuditRepo(),
    )
    return logs_session, tasks_session, ai_session


class _FakeAppLogsRepo:
    """Fake ``AppLogsRepo``: records purge/insert calls, no session at all."""

    def __init__(self, rowcounts: list[int] | None = None, count: int = 0) -> None:
        self.rowcounts = list(rowcounts or [])
        self.count = count
        self.purges: list[tuple[datetime, int]] = []
        self.inserted: list[dict] = []
        self.exc: Exception | None = None

    async def purge_batch(self, cutoff: datetime, batch_size: int) -> int:
        if self.exc is not None:
            raise self.exc
        self.purges.append((cutoff, batch_size))
        return self.rowcounts.pop(0) if self.rowcounts else 0

    async def count_expired(self, cutoff: datetime) -> int:
        return self.count

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
    """Fake ``TasksRepo``: one configurable rowcount."""

    def __init__(self, rowcounts: list[int] | None = None, count: int = 0) -> None:
        self.rowcounts = list(rowcounts or [])
        self.count = count
        self.purges: list[tuple[datetime, int]] = []

    async def purge_finished_batch(self, cutoff: datetime, batch_size: int) -> int:
        self.purges.append((cutoff, batch_size))
        return self.rowcounts.pop(0) if self.rowcounts else 0

    async def count_expired_finished(self, cutoff: datetime) -> int:
        return self.count


class _FakeAICallLogsRepo:
    """Fake ``AICallLogsRepo``: one configurable rowcount (same style)."""

    def __init__(self, rowcounts: list[int] | None = None, count: int = 0) -> None:
        self.rowcounts = list(rowcounts or [])
        self.count = count
        self.purges: list[tuple[datetime, int]] = []

    async def purge_batch(self, cutoff: datetime, batch_size: int) -> int:
        self.purges.append((cutoff, batch_size))
        return self.rowcounts.pop(0) if self.rowcounts else 0

    async def count_expired(self, cutoff: datetime) -> int:
        return self.count


class _FakeBatchRepo:
    """Fake for the two V033 tables: one rowcount, either purge method name."""

    def __init__(self, rowcounts: list[int] | None = None, count: int = 0) -> None:
        self.rowcounts = list(rowcounts or [])
        self.count = count
        self.purges: list[tuple[datetime, int]] = []

    async def purge_batch(self, cutoff: datetime, batch_size: int) -> int:
        self.purges.append((cutoff, batch_size))
        return self.rowcounts.pop(0) if self.rowcounts else 0

    async def purge_before(self, cutoff: datetime, batch_size: int) -> int:
        return await self.purge_batch(cutoff, batch_size)

    async def count_expired(self, cutoff: datetime) -> int:
        return self.count


def _fake_repos(
    monkeypatch,
    logs: _FakeAppLogsRepo | None = None,
    tasks: _FakeTasksRepo | None = None,
    ai: _FakeAICallLogsRepo | None = None,
) -> tuple[_FakeAppLogsRepo, _FakeTasksRepo, _FakeAICallLogsRepo]:
    logs_repo = logs if logs is not None else _FakeAppLogsRepo()
    tasks_repo = tasks if tasks is not None else _FakeTasksRepo()
    ai_repo = ai if ai is not None else _FakeAICallLogsRepo()
    monkeypatch.setattr(backends, "get_applogs_repo", lambda session=None: logs_repo)
    monkeypatch.setattr(backends, "get_tasks_repo", lambda session=None: tasks_repo)
    monkeypatch.setattr(backends, "get_ai_call_logs_repo", lambda session=None: ai_repo)
    # V033 的两张表同样必须替换，否则会落到真实 repo（要 engine）。
    monkeypatch.setattr(
        backends, "get_ai_agent_events_repo", lambda session=None: _FakeBatchRepo()
    )
    monkeypatch.setattr(
        backends, "get_ai_prompt_audit_repo", lambda session=None: _FakeBatchRepo()
    )
    return logs_repo, tasks_repo, ai_repo


@pytest.fixture(autouse=True)
def _no_append_only_purge(monkeypatch):
    """只增表的清理直连库：本文件只桩了五个仓储，这里把它一并桩掉。"""

    async def _none(now=None):
        return {}

    monkeypatch.setattr(retention, "run_append_only_retention", _none)


@pytest.fixture(autouse=True)
def _no_scene_conversation_purge(monkeypatch):
    """只读场景会话（终端 / 文件编辑）的清理走真实会话仓储：本文件不验证它，
    统一桩成「0 条」，让编排仍走完同一段路径（场景窗口由
    ``tests/test_ai_scene_conversations.py`` 单独钉住）。"""

    async def _none(now=None):
        return {"cutoff": "", "deleted": 0, "scanned": 0, "remaining": 0}

    monkeypatch.setattr(retention, "purge_stale_scene_conversations", _none)


@pytest.fixture(autouse=True)
def _release_lock():
    """Never leave the process-wide lock held across tests."""
    yield
    try:
        retention.release()
    except RuntimeError:  # not held
        pass


# ---------------------------------------------------------------------------
# cutoff arithmetic (pure)
# ---------------------------------------------------------------------------


def test_retention_cutoff_subtracts_the_window():
    assert retention_cutoff(NOW, 90) == datetime(2026, 1, 31, 3, 30, 0)
    assert retention_cutoff(NOW, 30) == datetime(2026, 4, 1, 3, 30, 0)


def test_retention_cutoff_zero_days_is_now():
    assert retention_cutoff(NOW, 0) == NOW


def test_retention_cutoff_is_exact_for_positive_days():
    assert retention_cutoff(NOW, 7) == NOW - timedelta(days=7)
    assert retention_cutoff(NOW, 1) == datetime(2026, 4, 30, 3, 30, 0)


def test_retention_cutoffs_give_each_table_its_own_window():
    logs_cutoff, tasks_cutoff = retention_cutoffs(NOW)

    assert logs_cutoff == NOW - timedelta(days=DEFAULT_APP_LOGS_RETENTION_DAYS)
    assert tasks_cutoff == NOW - timedelta(days=DEFAULT_EXPORT_TASKS_RETENTION_DAYS)
    assert logs_cutoff < tasks_cutoff  # 90 days reaches further back than 30


# ---------------------------------------------------------------------------
# defensive config read
# ---------------------------------------------------------------------------


def test_defaults_match_the_frozen_parameters():
    assert DEFAULT_APP_LOGS_RETENTION_DAYS == 90
    assert DEFAULT_EXPORT_TASKS_RETENTION_DAYS == 30
    assert DEFAULT_AI_CALL_LOGS_RETENTION_DAYS == 90
    assert DEFAULT_RETENTION_BATCH_SIZE == 5000
    assert DEFAULT_RETENTION_MAX_BATCHES == 20


@pytest.mark.parametrize(
    "reader,key,default",
    [
        (app_logs_retention_days, "APP_LOGS_RETENTION_DAYS", 90),
        (export_tasks_retention_days, "EXPORT_TASKS_RETENTION_DAYS", 30),
        (ai_call_logs_retention_days, "AI_CALL_LOGS_RETENTION_DAYS", 90),
        (retention_batch_size, "RETENTION_BATCH_SIZE", 5000),
        (retention_max_batches, "RETENTION_MAX_BATCHES", 20),
    ],
)
def test_readers_fall_back_when_the_key_is_missing(monkeypatch, reader, key, default):
    monkeypatch.delattr(config, key, raising=False)

    assert reader() == default


@pytest.mark.parametrize("bad", ["ninety", "3.5", None, 0, -5, object()])
def test_readers_fall_back_on_invalid_values(monkeypatch, bad):
    for key in (
        "APP_LOGS_RETENTION_DAYS",
        "EXPORT_TASKS_RETENTION_DAYS",
        "AI_CALL_LOGS_RETENTION_DAYS",
        "RETENTION_BATCH_SIZE",
        "RETENTION_MAX_BATCHES",
    ):
        monkeypatch.setattr(config, key, bad, raising=False)

    assert app_logs_retention_days() == DEFAULT_APP_LOGS_RETENTION_DAYS
    assert export_tasks_retention_days() == DEFAULT_EXPORT_TASKS_RETENTION_DAYS
    assert ai_call_logs_retention_days() == DEFAULT_AI_CALL_LOGS_RETENTION_DAYS
    assert retention_batch_size() == DEFAULT_RETENTION_BATCH_SIZE
    assert retention_max_batches() == DEFAULT_RETENTION_MAX_BATCHES


def test_readers_honour_positive_config(monkeypatch):
    monkeypatch.setattr(config, "APP_LOGS_RETENTION_DAYS", 14, raising=False)
    monkeypatch.setattr(config, "EXPORT_TASKS_RETENTION_DAYS", "2", raising=False)
    monkeypatch.setattr(config, "AI_CALL_LOGS_RETENTION_DAYS", 7, raising=False)
    monkeypatch.setattr(config, "RETENTION_BATCH_SIZE", 100, raising=False)
    monkeypatch.setattr(config, "RETENTION_MAX_BATCHES", 3, raising=False)

    assert app_logs_retention_days() == 14
    assert export_tasks_retention_days() == 2
    assert ai_call_logs_retention_days() == 7
    assert retention_batch_size() == 100
    assert retention_max_batches() == 3


def test_module_reads_config_by_attribute_not_by_import():
    """``import config`` + ``getattr`` — a missing key must not break the import."""
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(retention))
    assert not [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "config"
    ], "config must be imported as a module, never `from config import KEY`"
    assert [
        alias
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
        if alias.name == "config"
    ], "expected `import config`"


# ---------------------------------------------------------------------------
# batching loop (real repos, faked session_scope)
# ---------------------------------------------------------------------------


def test_run_drains_until_a_short_batch(monkeypatch):
    """``[5000, 5000, 3]`` ⇒ exactly three batches, three commits, 10003 rows."""
    logs_session, tasks_session, ai_session = _real_repos(
        monkeypatch,
        logs_rowcounts=[5000, 5000, 3],
        logs_count=3,
        tasks_rowcounts=[0],
        tasks_count=0,
    )

    result = asyncio.run(run_retention())

    assert len(logs_session.deletes) == 3
    assert logs_session.commits == 3  # one short session + one commit per batch
    assert result["app_logs"]["deleted"] == 10003
    assert result["app_logs"]["remaining"] == 3
    assert len(tasks_session.deletes) == 1
    assert tasks_session.commits == 1
    assert result["export_tasks"]["deleted"] == 0
    assert len(ai_session.deletes) == 1
    assert ai_session.commits == 1
    assert set(result["ai_call_logs"]) == {"cutoff", "deleted", "remaining"}
    assert result["ai_call_logs"]["deleted"] == 0
    assert result["batches"] == 7


def test_run_stops_on_the_first_empty_batch(monkeypatch):
    """A first batch of ``0`` means nothing is expired: one call, one commit."""
    logs_session, tasks_session, ai_session = _real_repos(
        monkeypatch, logs_rowcounts=[0], tasks_rowcounts=[0]
    )

    result = asyncio.run(run_retention())

    assert len(logs_session.deletes) == 1
    assert logs_session.commits == 1
    assert len(tasks_session.deletes) == 1
    assert len(ai_session.deletes) == 1
    assert result["app_logs"]["deleted"] == 0
    assert result["batches"] == 5


def test_run_stops_at_the_batch_ceiling(monkeypatch):
    """A full batch every time must not loop forever: ``RETENTION_MAX_BATCHES``."""
    monkeypatch.setattr(config, "RETENTION_MAX_BATCHES", 2, raising=False)
    logs_session, _, _ = _real_repos(
        monkeypatch,
        logs_rowcounts=[5000, 5000, 5000],
        logs_count=7,
        tasks_rowcounts=[0],
    )

    result = asyncio.run(run_retention())

    assert len(logs_session.deletes) == 2
    assert logs_session.commits == 2
    assert result["app_logs"]["deleted"] == 10000
    # The ceiling was hit, so the remaining expired rows are reported.
    assert result["app_logs"]["remaining"] == 7
    assert result["batches"] == 6


def test_run_uses_the_configured_windows_and_batch_size(monkeypatch):
    monkeypatch.setattr(config, "APP_LOGS_RETENTION_DAYS", 10, raising=False)
    monkeypatch.setattr(config, "EXPORT_TASKS_RETENTION_DAYS", 2, raising=False)
    monkeypatch.setattr(config, "AI_CALL_LOGS_RETENTION_DAYS", 5, raising=False)
    monkeypatch.setattr(config, "RETENTION_BATCH_SIZE", 100, raising=False)
    logs_session, tasks_session, ai_session = _real_repos(
        monkeypatch, logs_rowcounts=[0], tasks_rowcounts=[0]
    )

    before = datetime.now()
    result = asyncio.run(run_retention())
    after = datetime.now()

    assert "ROWNUM <= 100" in _sql(logs_session.deletes[0])
    assert "ROWNUM <= 100" in _sql(tasks_session.deletes[0])
    assert "ROWNUM <= 100" in _sql(ai_session.deletes[0])
    logs_cutoff = datetime.fromisoformat(result["app_logs"]["cutoff"])
    tasks_cutoff = datetime.fromisoformat(result["export_tasks"]["cutoff"])
    ai_cutoff = datetime.fromisoformat(result["ai_call_logs"]["cutoff"])
    assert before - timedelta(days=10) <= logs_cutoff <= after - timedelta(days=10)
    assert before - timedelta(days=2) <= tasks_cutoff <= after - timedelta(days=2)
    assert before - timedelta(days=5) <= ai_cutoff <= after - timedelta(days=5)
    assert before - timedelta(days=10) <= logs_cutoff < tasks_cutoff


def test_run_reports_the_cutoff_it_used_to_the_repo(monkeypatch):
    """The cutoff handed to the repo is the one echoed in the response."""
    logs_repo, tasks_repo, ai_repo = _fake_repos(monkeypatch)

    result = asyncio.run(run_retention())

    logs_cutoff, batch_size = logs_repo.purges[0]
    assert logs_cutoff.isoformat() == result["app_logs"]["cutoff"]
    assert batch_size == DEFAULT_RETENTION_BATCH_SIZE
    tasks_cutoff, _ = tasks_repo.purges[0]
    assert tasks_cutoff.isoformat() == result["export_tasks"]["cutoff"]
    assert tasks_cutoff - logs_cutoff == timedelta(
        days=DEFAULT_APP_LOGS_RETENTION_DAYS - DEFAULT_EXPORT_TASKS_RETENTION_DAYS
    )
    ai_cutoff, ai_batch_size = ai_repo.purges[0]
    assert ai_cutoff.isoformat() == result["ai_call_logs"]["cutoff"]
    assert ai_batch_size == DEFAULT_RETENTION_BATCH_SIZE
    assert tasks_cutoff - ai_cutoff == timedelta(
        days=DEFAULT_AI_CALL_LOGS_RETENTION_DAYS - DEFAULT_EXPORT_TASKS_RETENTION_DAYS
    )


# ---------------------------------------------------------------------------
# process-wide lock
# ---------------------------------------------------------------------------


def test_manual_run_is_refused_while_a_purge_is_running(monkeypatch):
    """A second run is refused, not queued behind the first."""
    _fake_repos(monkeypatch)

    async def _scenario():
        assert await retention.acquire_nowait() is True
        try:
            with pytest.raises(RetentionBusy) as excinfo:
                await purge_now()
            return str(excinfo.value)
        finally:
            retention.release()

    assert asyncio.run(_scenario()) == "清理任务正在执行中，请稍后重试"


def test_lock_is_released_after_a_manual_run(monkeypatch):
    _fake_repos(monkeypatch)

    async def _scenario():
        first = await purge_now()
        # A second run would raise RetentionBusy if the first still held the lock.
        second = await purge_now()
        return first["batches"], second["batches"]

    assert asyncio.run(_scenario()) == (5, 5)


def test_scheduled_run_skips_quietly_while_a_purge_is_running(monkeypatch, capsys):
    logs_repo, _, _ = _fake_repos(monkeypatch)

    async def _scenario():
        assert await retention.acquire_nowait() is True
        try:
            return await purge_expired_records()
        finally:
            retention.release()

    assert asyncio.run(_scenario()) == 0
    assert logs_repo.purges == []
    assert "skipped" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# scheduled entry point: never raises, always audits
# ---------------------------------------------------------------------------


def test_scheduled_run_writes_one_audit_row_for_the_scheduler(monkeypatch, capsys):
    logs_repo, _, _ = _fake_repos(
        monkeypatch,
        logs=_FakeAppLogsRepo([5000, 3], count=0),
        tasks=_FakeTasksRepo([2], count=0),
        ai=_FakeAICallLogsRepo([0], count=0),
    )

    assert asyncio.run(purge_expired_records()) == 5005

    assert len(logs_repo.inserted) == 1
    row = logs_repo.inserted[0]
    assert row["type"] == "retention_purge"
    assert row["level"] == "warning"
    assert row["user"] == "scheduler"
    assert "数据保留期清理" in row["data"]
    assert "app_logs 删除 5003 行" in row["data"]
    assert "export_tasks 删除 2 行" in row["data"]
    assert "ai_call_logs 删除 0 行" in row["data"]
    assert "deleted 5005 row(s)" in capsys.readouterr().out


def test_audit_row_level_is_inside_the_protected_set(monkeypatch):
    """The audit row must survive its own purge (``level='warning'``).

    ``db.repos.oracle.app_logs._expired_conds`` excludes warning-level rows, so
    the two constants below are a dependency, not a cosmetic choice.
    """
    from db.repos.oracle import app_logs as app_logs_module

    assert retention.AUDIT_LEVEL == app_logs_module.PROTECTED_LEVEL
    assert retention.AUDIT_LEVEL == "warning"
    assert retention.AUDIT_TYPE not in app_logs_module.PROTECTED_LTYPES


def test_scheduled_run_swallows_purge_failures(monkeypatch, capsys):
    logs_repo, _, _ = _fake_repos(
        monkeypatch,
        logs=_FakeAppLogsRepo([], count=0),
        tasks=_FakeTasksRepo([0]),
        ai=_FakeAICallLogsRepo([0]),
    )
    logs_repo.exc = RuntimeError("boom")

    # Must not raise: a failure here would surface inside the scheduler.
    assert asyncio.run(purge_expired_records()) == 0
    out = capsys.readouterr().out
    assert "purge_expired_records failed" in out
    assert "boom" in out
    assert logs_repo.inserted == []


def test_scheduled_run_swallows_repo_lookup_failures(monkeypatch, capsys):
    def _boom(session=None):
        raise RuntimeError("no engine")

    monkeypatch.setattr(backends, "get_applogs_repo", _boom)

    assert asyncio.run(purge_expired_records()) == 0
    assert "no engine" in capsys.readouterr().out


def test_audit_failure_does_not_hide_a_completed_purge(monkeypatch, capsys):
    """The rows are already gone; a failing audit must not turn the run into 0."""
    logs_repo, _, _ = _fake_repos(
        monkeypatch,
        logs=_FakeAppLogsRepo([7], count=0),
        tasks=_FakeTasksRepo([0]),
        ai=_FakeAICallLogsRepo([0]),
    )

    async def _boom(result):
        raise RuntimeError("audit down")

    monkeypatch.setattr(retention, "write_scheduler_audit", _boom)

    assert asyncio.run(purge_expired_records()) == 7
    out = capsys.readouterr().out
    assert "audit write failed" in out
    assert "audit down" in out
    assert logs_repo.purges != []


# ---------------------------------------------------------------------------
# scheduler registration
# ---------------------------------------------------------------------------


def test_startup_registers_the_daily_retention_job():
    import main

    async def _boot_and_inspect():
        if main.scheduler.running:
            main.scheduler.shutdown(wait=False)
        await main.startup_event()
        try:
            return {job.id: job for job in main.scheduler.get_jobs()}
        finally:
            main.scheduler.shutdown(wait=False)

    jobs = asyncio.run(_boot_and_inspect())

    assert "daily_retention_purge" in jobs
    trigger = jobs["daily_retention_purge"].trigger
    fields = {field.name: str(field) for field in trigger.fields}
    assert fields["hour"] == "3"
    assert fields["minute"] == "30"  # hour=3 minute=0 belongs to daily_purge_files
    assert fields["second"] == "0"
    assert str(trigger.timezone) == "Asia/Hong_Kong"

    # Pre-existing jobs must survive the new registration.
    assert "daily_purge_files" in jobs
    assert "daily_compute_time" in jobs
    assert "daily_generate_reports" in jobs
