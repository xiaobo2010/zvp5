"""Data-retention purge for ``app_logs`` and ``export_tasks`` (design §2).

Two tables grow without bound and had no cleanup path at all:

* ``zvms_new.app_logs`` — every request writes a row (:mod:`typings.log`);
* ``zvms_new.tasks`` — one row per export request, finished or not.

:func:`run_retention` is the shared coroutine: it drains each table through the
repo's single-batch methods (``OracleAppLogsRepo.purge_batch`` /
``OracleTasksRepo.purge_finished_batch``, statements
``db/repos/oracle/app_logs.py::_purge_batch_stmt`` and
``db/repos/oracle/tasks.py::_purge_finished_batch_stmt``) for at most
``RETENTION_MAX_BATCHES`` batches per table, stopping early on a short batch.
Each batch is its own short session and its own commit, so a long purge never
holds one transaction open on ADB.

Two callers share it:

* ``main.py`` schedules :func:`purge_expired_records` daily at 03:30 HKT
  (``id="daily_retention_purge"``);
* ``routers/db_system_router.py`` calls :func:`purge_now` for the admin-triggered
  ``POST /api/system/db/retention/run``.

The retention windows are read *defensively*: ``config.py`` is a
deployment-local file, so a key added to the repository's copy may be absent on
a host that predates it — ``from config import APP_LOGS_RETENTION_DAYS`` would
raise ``ImportError`` at import time and stop the whole application from
starting. Hence ``import config`` + ``getattr`` with a code default.

Deletion is irreversible: the protected set (security/audit log types plus
warning-level rows) is the only guard, and it lives in the SQL predicate — see
``db/repos/oracle/app_logs.py``. Nothing here re-implements it.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Awaitable, Callable

import config
from db import backends

# Fallbacks for a host whose ``config.py`` predates these keys (design §1).
DEFAULT_APP_LOGS_RETENTION_DAYS = 90
DEFAULT_EXPORT_TASKS_RETENTION_DAYS = 30
DEFAULT_RETENTION_BATCH_SIZE = 5000
DEFAULT_RETENTION_MAX_BATCHES = 20

# The audit row's type/level. 审计行的 type/level 是承重设计：该行落在 app_logs
# 清理保护集内（``log_level='warning'``），所以后续清理不会删掉自己的审计记录。
# 该依赖保护集，勿收窄白名单（``db.repos.oracle.app_logs.PROTECTED_LEVEL``）。
AUDIT_TYPE = "retention_purge"
AUDIT_LEVEL = "warning"

# Who the scheduler-side audit row is attributed to; the manual endpoint uses the
# acting admin instead (``typings.log.inject_log``).
SCHEDULER_USER = "scheduler"

# Process-wide mutual exclusion: the purge is expensive and a second concurrent
# run would only duplicate work on the same rows. Never taken across an await
# that outlives one purge.
_lock = asyncio.Lock()


class RetentionBusy(RuntimeError):
    """A purge is already running in this process."""

    def __init__(self, message: str = "清理任务正在执行中，请稍后重试") -> None:
        super().__init__(message)


def _positive_int(name: str, default: int) -> int:
    """Read a positive integer from ``config``, never raising on a bad value.

    Missing key, non-integer value and non-positive values all fall back to
    ``default`` — a broken deployment config must not take the app down.
    """
    raw = getattr(config, name, default)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def app_logs_retention_days() -> int:
    """Retention window for ``app_logs``, in days."""
    return _positive_int("APP_LOGS_RETENTION_DAYS", DEFAULT_APP_LOGS_RETENTION_DAYS)


def export_tasks_retention_days() -> int:
    """Retention window for finished ``export_tasks``, in days."""
    return _positive_int(
        "EXPORT_TASKS_RETENTION_DAYS", DEFAULT_EXPORT_TASKS_RETENTION_DAYS
    )


def retention_batch_size() -> int:
    """Rows per ``DELETE`` batch."""
    return _positive_int("RETENTION_BATCH_SIZE", DEFAULT_RETENTION_BATCH_SIZE)


def retention_max_batches() -> int:
    """Batches per table and per run (the manual endpoint stops here too)."""
    return _positive_int("RETENTION_MAX_BATCHES", DEFAULT_RETENTION_MAX_BATCHES)


def retention_cutoff(now: datetime, days: int) -> datetime:
    """Pure helper: the instant a row must precede to be deletable."""
    return now - timedelta(days=days)


def retention_cutoffs(now: datetime | None = None) -> tuple[datetime, datetime]:
    """``(app_logs_cutoff, export_tasks_cutoff)`` for one run.

    Naive ``datetime.now()``, matching the schema convention for ``TIMESTAMP``
    columns (``db/repos/oracle/files.py::_now``). Both tables share one ``now``
    so a run reports consistent cutoffs.
    """
    moment = datetime.now() if now is None else now
    return (
        retention_cutoff(moment, app_logs_retention_days()),
        retention_cutoff(moment, export_tasks_retention_days()),
    )


async def acquire_nowait() -> bool:
    """Take the purge lock without waiting; ``False`` when it is already held.

    ``asyncio.Lock`` has no ``acquire_nowait``: the ``locked()`` check and the
    acquire below are both free of suspension points on an idle lock, so the
    pair is atomic on a single event loop.
    """
    if _lock.locked():
        return False
    await _lock.acquire()
    return True


def release() -> None:
    """Release the purge lock. Whoever took it must always call this."""
    _lock.release()


async def _drain(
    purge: Callable[[datetime, int], Awaitable[int]],
    cutoff: datetime,
    batch_size: int,
    max_batches: int,
) -> tuple[int, int]:
    """Run ``purge`` until it reports a short batch; ``(deleted, batches)``.

    A batch shorter than ``batch_size`` means the table ran out of expired rows,
    so there is nothing left to ask for. The loop is bounded by ``max_batches``
    either way: one run must not turn into an unbounded delete.
    """
    total = 0
    batches = 0
    for _ in range(max_batches):
        deleted = int(await purge(cutoff, batch_size))
        total += deleted
        batches += 1
        if deleted < batch_size:
            break
    return total, batches


async def run_retention() -> dict:
    """Purge both tables in bounded batches and report what happened.

    Returns ``{"app_logs": {"cutoff", "deleted", "remaining"}, "export_tasks":
    {...}, "batches": int}``; ``batches`` counts the ``DELETE`` statements
    executed across both tables (at most ``2 * RETENTION_MAX_BATCHES``), and
    ``remaining`` is the expired row count *after* the run — non-zero only when
    the batch ceiling was hit.
    """
    batch_size = retention_batch_size()
    max_batches = retention_max_batches()
    logs_cutoff, tasks_cutoff = retention_cutoffs()

    logs_repo = backends.get_applogs_repo()
    tasks_repo = backends.get_tasks_repo()

    logs_deleted, logs_batches = await _drain(
        logs_repo.purge_batch, logs_cutoff, batch_size, max_batches
    )
    tasks_deleted, tasks_batches = await _drain(
        tasks_repo.purge_finished_batch, tasks_cutoff, batch_size, max_batches
    )

    return {
        "app_logs": {
            "cutoff": logs_cutoff.isoformat(),
            "deleted": logs_deleted,
            "remaining": int(await logs_repo.count_expired(logs_cutoff)),
        },
        "export_tasks": {
            "cutoff": tasks_cutoff.isoformat(),
            "deleted": tasks_deleted,
            "remaining": int(await tasks_repo.count_expired_finished(tasks_cutoff)),
        },
        "batches": logs_batches + tasks_batches,
    }


async def purge_now() -> dict:
    """Manual entry point: :func:`run_retention` under the process-wide lock.

    Raises :class:`RetentionBusy` instead of queueing behind a running purge —
    the endpoint turns that into a 409 rather than holding the request open.
    """
    if not await acquire_nowait():
        raise RetentionBusy()
    try:
        return await run_retention()
    finally:
        release()


def audit_message(result: dict) -> str:
    """One-line summary of a run; shared by the scheduler and the endpoint."""
    logs = result["app_logs"]
    tasks = result["export_tasks"]
    return (
        f"数据保留期清理：app_logs 删除 {logs['deleted']} 行（剩余 {logs['remaining']} 行），"
        f"export_tasks 删除 {tasks['deleted']} 行（剩余 {tasks['remaining']} 行）"
    )


async def write_scheduler_audit(result: dict) -> None:
    """Write the scheduler-side audit row directly (no request, no ``inject_log``).

    ``user`` is :data:`SCHEDULER_USER` because there is no authenticated actor.
    """
    await backends.get_applogs_repo().insert(
        url="",
        user=SCHEDULER_USER,
        data=audit_message(result),
        timestamp=datetime.now().timestamp(),
        ltype=AUDIT_TYPE,
        level=AUDIT_LEVEL,
        xuehai_id="",
    )


async def purge_expired_records() -> int:
    """Scheduled entry point (``daily_retention_purge``); returns rows deleted.

    Returns ``0`` when nothing matched **or** when the purge failed: failures are
    printed, never raised, because a bad row or a transient DB error must not
    kill the scheduler or the process.
    """
    try:
        if not await acquire_nowait():
            print("purge_expired_records: skipped, a purge is already running")
            return 0
        try:
            result = await run_retention()
        finally:
            release()

        try:
            await write_scheduler_audit(result)
        except Exception as exc:
            # The purge already happened; a failing audit must not hide it.
            print(f"purge_expired_records: audit write failed: {exc!r}")

        deleted = result["app_logs"]["deleted"] + result["export_tasks"]["deleted"]
        print(
            f"purge_expired_records: deleted {deleted} row(s); "
            f"app_logs={result['app_logs']}; export_tasks={result['export_tasks']}"
        )
        return deleted
    except Exception as exc:
        print(f"purge_expired_records failed: {exc!r}")
        return 0
