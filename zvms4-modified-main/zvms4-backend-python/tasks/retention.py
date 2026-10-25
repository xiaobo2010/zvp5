"""Data-retention purge for ``app_logs``, ``export_tasks`` and ``ai_call_logs``
(design §2).

Three tables grow without bound and had no cleanup path at all:

* ``zvms_new.app_logs`` — every request writes a row (:mod:`typings.log`);
* ``zvms_new.tasks`` — one row per export request, finished or not;
* ``zvms_new.ai_call_logs`` — one row per AI gateway call (V024).

:func:`run_retention` is the shared coroutine: it drains each table through the
repo's single-batch methods (``OracleAppLogsRepo.purge_batch`` /
``OracleTasksRepo.purge_finished_batch`` /
``OracleAICallLogsRepo.purge_batch``, statements
``db/repos/oracle/app_logs.py::_purge_batch_stmt``,
``db/repos/oracle/tasks.py::_purge_finished_batch_stmt`` and
``db/repos/oracle/ai_call_logs.py::_purge_batch_stmt``) for at most
``RETENTION_MAX_BATCHES`` batches per table, stopping early on a short batch.
Each batch is its own short session and its own commit, so a long purge never
holds one transaction open on ADB.

Two callers share it:

* ``main.py`` schedules :func:`purge_expired_records` daily at 03:30 HKT
  (``id="daily_retention_purge"``);
* ``routers/db_system_router.py`` starts :func:`start_background_purge` for the
  admin-triggered ``POST /api/system/db/retention/run`` (后台任务 + 进度端点;
  :func:`purge_now` 保留给「同步等结果」的调用方/测试).

The retention windows are read *defensively*: ``config.py`` is a
deployment-local file, so a key added to the repository's copy may be absent on
a host that predates it — ``from config import APP_LOGS_RETENTION_DAYS`` would
raise ``ImportError`` at import time and stop the whole application from
starting. Hence ``import config`` + ``getattr`` with a code default.

Deletion is irreversible: the protected set (security/audit log types plus
warning-level rows) is the only guard, and it lives in the SQL predicate — see
``db/repos/oracle/app_logs.py``. Nothing here re-implements it.

数据库清理的维护编排（本模块第二部分，设计 §6）
------------------------------------------------

需求：清理操作要让服务器**自动进入维护状态、结束后自动恢复**，并且「如果进
维护态时有非管理员请求打进来」一律 503（管理员绕过，由 ``main.py`` 的中间件
负责），清理进度要能在后台任务里被轮询。

* 手动入口（``POST /api/system/db/retention/run`` → :func:`start_background_purge`）
  与定时入口（``main.py`` 的 ``daily_retention_purge`` → :func:`purge_expired_records`）
  走**同一段编排** :func:`_orchestrate`，因此两侧都进维护态。
* 进入顺序（重要）：**先置进程内「自动清理进行中」标志，再
  ``system_state.set_state(True, ..., "auto:retention")``**。反过来的话，两个动作
  之间到达的请求会看到「enabled=1 + 自动来源 + 探针 False」而被误自愈，维护态
  刚开就被清掉。
* 恢复：``try/finally`` 保证**任何**异常路径都会
  ``set_state(False, ..., "auto:retention")``，包括清理失败、审计失败、任务被
  取消。进程崩溃时不可能执行 finally —— 那一半由 ``db/system_state.py`` 的
  自动来源探针自愈（重启后探针必然为 False ⇒ 陈旧态被清除），见下面的探针注册。
* 崩溃自愈的**前提是单 worker 进程**：进程内标志即真相。实测生产为单 worker
  uvicorn；多 worker 会破坏该假设，必须改成带租约的外部心跳。
"""

from __future__ import annotations

import asyncio
import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable

import config
import realtime
from db import backends, system_state

# Fallbacks for a host whose ``config.py`` predates these keys (design §1).
DEFAULT_APP_LOGS_RETENTION_DAYS = 90
DEFAULT_EXPORT_TASKS_RETENTION_DAYS = 30
DEFAULT_AI_CALL_LOGS_RETENTION_DAYS = 90
DEFAULT_RETENTION_BATCH_SIZE = 5000
DEFAULT_RETENTION_MAX_BATCHES = 20

# The audit row's type/level. 审计行的 type/level 是承重设计：该行落在 app_logs
# 清理保护集内（``log_level='warning'``），所以后续清理不会删掉自己的审计记录。
# 该依赖保护集，勿收窄白名单（``db.repos.oracle.app_logs.PROTECTED_LEVEL``）。
AUDIT_TYPE = "retention_purge"
AUDIT_LEVEL = "warning"

# 409 的文案（既有线上契约，别改）。
BUSY_MESSAGE = "清理任务正在执行中，请稍后重试"

# Who the scheduler-side audit row is attributed to; the manual endpoint uses the
# acting admin instead (``typings.log.inject_log``).
SCHEDULER_USER = "scheduler"

# ---------------------------------------------------------------------------
# 维护编排（设计 §6）：常量
# ---------------------------------------------------------------------------

# 进入维护态时写进 maintenance_state.updated_by 的标记。前缀 ``auto:`` 是自愈的
# 判据（``db/system_state.py``: 自动来源 + 探针 False ⇒ 陈旧态），改不得。
AUTO_MAINTENANCE_ACTOR = "auto:retention"

MAINTENANCE_REASON = "数据库清理"
MAINTENANCE_ANNOUNCEMENT = (
    "系统正在清理数据库历史数据，期间暂时无法访问，清理完成后自动恢复。"
)

# GET /api/system/db/retention/progress 的 phase 取值（设计 §5.5，逐字冻结）。
PHASE_ENTERING = "entering_maintenance"
PHASE_PURGING_APP_LOGS = "purging_app_logs"
PHASE_PURGING_EXPORT_TASKS = "purging_export_tasks"
PHASE_PURGING_AI_CALL_LOGS = "purging_ai_call_logs"
PHASE_LEAVING = "leaving_maintenance"
PHASE_DONE = "done"
PHASE_FAILED = "failed"

_PHASE_FOR_TABLE = {
    "app_logs": PHASE_PURGING_APP_LOGS,
    "export_tasks": PHASE_PURGING_EXPORT_TASKS,
    "ai_call_logs": PHASE_PURGING_AI_CALL_LOGS,
}

JOB_STATE_RUNNING = "running"
JOB_STATE_DONE = "done"
JOB_STATE_FAILED = "failed"

# 进度回调：``(phase, progress)``。progress 是 run_retention 内部那份可变的
# 计数字典，接收方必须自行拷贝（回调可能在下一批就被改写）。
ProgressCallback = Callable[[str, dict], None]
# 每批提交后的回调：``(该表累计删除数, 该表累计批次数)``。纯记账，不得 await。
BatchCallback = Callable[[int, int], None]

# Process-wide mutual exclusion: the purge is expensive and a second concurrent
# run would only duplicate work on the same rows. Never taken across an await
# that outlives one purge.
_lock = asyncio.Lock()

# ---------------------------------------------------------------------------
# 进程内作业状态（handles ``GET /retention/progress``）
#
# **单 worker 进程内即真相**（见模块 docstring）：这些变量就是「当前清理作业」
# 的唯一来源，没有外部存储。``_auto_purge_running`` 同时是自动来源探针。
# ``_job_lock`` 只圈住纯内存的读改写（绝不跨 await），因此不会阻塞事件循环。
# ---------------------------------------------------------------------------
_job_lock = threading.Lock()
_auto_purge_running = False
_current_job: dict | None = None
_last_run: dict | None = None
_background_task: "asyncio.Task | None" = None


class RetentionBusy(RuntimeError):
    """A purge is already running in this process."""

    def __init__(self, message: str = BUSY_MESSAGE, phase: str | None = None) -> None:
        # phase 只用于把「谁在跑、跑到哪一步了」带给 409 的 detail（设计 §5.5）；
        # 没有正在跑的作业时保持既有文案一字不变。
        super().__init__(f"{message}（当前阶段：{phase}）" if phase else message)
        self.phase = phase


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


def ai_call_logs_retention_days() -> int:
    """Retention window for ``ai_call_logs``, in days."""
    return _positive_int(
        "AI_CALL_LOGS_RETENTION_DAYS", DEFAULT_AI_CALL_LOGS_RETENTION_DAYS
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
    on_batch: BatchCallback | None = None,
) -> tuple[int, int]:
    """Run ``purge`` until it reports a short batch; ``(deleted, batches)``.

    A batch shorter than ``batch_size`` means the table ran out of expired rows,
    so there is nothing left to ask for. The loop is bounded by ``max_batches``
    either way: one run must not turn into an unbounded delete.

    ``on_batch(total, batches)``（可选）在**每批提交之后**立刻回调，供后台作业
    更新进程内进度；它必须同步、廉价、不抛异常（这里不做保护，回调抛了就把
    清理带崩，不如让它 500）。
    """
    total = 0
    batches = 0
    for _ in range(max_batches):
        deleted = int(await purge(cutoff, batch_size))
        total += deleted
        batches += 1
        if on_batch is not None:
            on_batch(total, batches)
        if deleted < batch_size:
            break
    return total, batches


async def run_retention(on_progress: ProgressCallback | None = None) -> dict:
    """Purge all three tables in bounded batches and report what happened.

    Returns ``{"app_logs": {"cutoff", "deleted", "remaining"}, "export_tasks":
    {...}, "ai_call_logs": {...}, "batches": int}``; ``batches`` counts the
    ``DELETE`` statements executed across all tables (at most
    ``3 * RETENTION_MAX_BATCHES``), and ``remaining`` is the expired row count
    *after* the run — non-zero only when the batch ceiling was hit.

    ``on_progress(phase, progress)``（可选）让调用方（后台作业）实时看到进度：
    phase 取 :data:`_PHASE_FOR_TABLE` 的值，progress 形状与
    ``GET /retention/progress`` 的 ``data.progress`` 一致。传了它才会**额外**做
    两次 ``COUNT``：展示用的初始 ``remaining``（每表一次）——这只影响进度显示，
    返回结构里的 ``remaining`` 仍是跑完后的真实计数，语义不变。

    三张表共用同一个 ``now``、同一份 ``RETENTION_BATCH_SIZE`` 与
    ``RETENTION_MAX_BATCHES``，逐表串行 drain：一张表删空/触顶后再开始下一张，
    不会并发抢 ADB 的连接。
    """
    batch_size = retention_batch_size()
    max_batches = retention_max_batches()
    moment = datetime.now()
    logs_cutoff, tasks_cutoff = retention_cutoffs(moment)
    ai_cutoff = retention_cutoff(moment, ai_call_logs_retention_days())

    logs_repo = backends.get_applogs_repo()
    tasks_repo = backends.get_tasks_repo()
    ai_repo = backends.get_ai_call_logs_repo()

    progress: dict = {
        "app_logs": {"deleted": 0, "remaining": 0},
        "export_tasks": {"deleted": 0, "remaining": 0},
        "ai_call_logs": {"deleted": 0, "remaining": 0},
        "batches": 0,
        "max_batches": max_batches,
    }

    def emit(phase: str) -> None:
        if on_progress is not None:
            on_progress(phase, progress)

    def recorder(table: str, offset_batches: int, initial: int) -> BatchCallback:
        def _on_batch(total: int, batches: int) -> None:
            progress[table]["deleted"] = total
            progress[table]["remaining"] = max(0, initial - total)
            progress["batches"] = offset_batches + batches
            emit(_PHASE_FOR_TABLE[table])

        return _on_batch

    logs_initial = 0
    if on_progress is not None:
        logs_initial = int(await logs_repo.count_expired(logs_cutoff))
        progress["app_logs"]["remaining"] = max(0, logs_initial)
    emit(PHASE_PURGING_APP_LOGS)
    logs_deleted, logs_batches = await _drain(
        logs_repo.purge_batch,
        logs_cutoff,
        batch_size,
        max_batches,
        recorder("app_logs", 0, logs_initial),
    )

    tasks_initial = 0
    if on_progress is not None:
        tasks_initial = int(await tasks_repo.count_expired_finished(tasks_cutoff))
        progress["export_tasks"]["remaining"] = max(0, tasks_initial)
    emit(PHASE_PURGING_EXPORT_TASKS)
    tasks_deleted, tasks_batches = await _drain(
        tasks_repo.purge_finished_batch,
        tasks_cutoff,
        batch_size,
        max_batches,
        recorder("export_tasks", logs_batches, tasks_initial),
    )

    ai_initial = 0
    if on_progress is not None:
        ai_initial = int(await ai_repo.count_expired(ai_cutoff))
        progress["ai_call_logs"]["remaining"] = max(0, ai_initial)
    emit(PHASE_PURGING_AI_CALL_LOGS)
    ai_deleted, ai_batches = await _drain(
        ai_repo.purge_batch,
        ai_cutoff,
        batch_size,
        max_batches,
        recorder("ai_call_logs", logs_batches + tasks_batches, ai_initial),
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
        "ai_call_logs": {
            "cutoff": ai_cutoff.isoformat(),
            "deleted": ai_deleted,
            "remaining": int(await ai_repo.count_expired(ai_cutoff)),
        },
        "batches": logs_batches + tasks_batches + ai_batches,
    }


async def purge_now(actor: str = SCHEDULER_USER) -> dict:
    """Manual (synchronous) entry point: :func:`run_retention` under the
    process-wide lock, wrapped in the maintenance orchestration.

    Kept for callers/tests that want to wait for the result; the HTTP endpoint
    uses :func:`start_background_purge` instead (它不能把请求挂在这儿）。
    Raises :class:`RetentionBusy` instead of queueing behind a running purge —
    the endpoint turns that into a 409 rather than holding the request open;
    **被拒时不会碰维护态**（先抢锁/占位，再进维护）。
    """
    return await _purge_under_maintenance(actor)


def audit_message(result: dict) -> str:
    """One-line summary of a run; shared by the scheduler and the endpoint."""
    logs = result["app_logs"]
    tasks = result["export_tasks"]
    ai = result["ai_call_logs"]
    return (
        f"数据保留期清理：app_logs 删除 {logs['deleted']} 行（剩余 {logs['remaining']} 行），"
        f"export_tasks 删除 {tasks['deleted']} 行（剩余 {tasks['remaining']} 行），"
        f"ai_call_logs 删除 {ai['deleted']} 行（剩余 {ai['remaining']} 行）"
    )


async def write_audit_row(result: dict, user: str) -> None:
    """Write one ``retention_purge`` / ``warning`` audit row for ``user``.

    ``user`` is either the ``scheduler`` sentinel (no authenticated actor) or the
    oid of the admin who started a background run. ``warning`` 是承重取值：该行
    落在 app_logs 清理保护集内，后续清理不会删掉自己的审计记录。
    """
    await backends.get_applogs_repo().insert(
        url="",
        user=user,
        data=audit_message(result),
        timestamp=datetime.now().timestamp(),
        ltype=AUDIT_TYPE,
        level=AUDIT_LEVEL,
        xuehai_id="",
    )


async def write_scheduler_audit(result: dict) -> None:
    """Write the scheduler-side audit row directly (no request, no ``inject_log``).

    ``user`` is :data:`SCHEDULER_USER` because there is no authenticated actor.
    """
    await write_audit_row(result, SCHEDULER_USER)


async def _write_run_audit(result: dict, actor: str) -> None:
    """结果审计。定时侧走 :func:`write_scheduler_audit`（保持既有钩子），手动侧
    按发起管理员归属。"""
    if actor == SCHEDULER_USER:
        await write_scheduler_audit(result)
    else:
        await write_audit_row(result, actor)


async def purge_expired_records() -> int:
    """Scheduled entry point (``daily_retention_purge``); returns rows deleted.

    Returns ``0`` when nothing matched, when another purge is running **or** when
    the purge failed: failures are printed, never raised, because a bad row or a
    transient DB error must not kill the scheduler or the process. 与手动入口
    同源：走同一段编排，因此定时清理同样会进维护态（并在结束时恢复）。
    """
    try:
        result = await _purge_under_maintenance(SCHEDULER_USER)
    except RetentionBusy:
        print("purge_expired_records: skipped, a purge is already running")
        return 0
    except Exception as exc:
        print(f"purge_expired_records failed: {exc!r}")
        return 0

    deleted = (
        result["app_logs"]["deleted"]
        + result["export_tasks"]["deleted"]
        + result["ai_call_logs"]["deleted"]
    )
    print(
        f"purge_expired_records: deleted {deleted} row(s); "
        f"app_logs={result['app_logs']}; export_tasks={result['export_tasks']}; "
        f"ai_call_logs={result['ai_call_logs']}"
    )
    return deleted


# ---------------------------------------------------------------------------
# 维护编排（设计 §6）
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    """UTC ISO8601（带 ``+00:00``），与 §5.5 的进度契约一致。"""
    return datetime.now(timezone.utc).isoformat()


def _iso_from_epoch(value: float | None) -> str | None:
    """``maintenance_state.since``（epoch 秒）→ UTC ISO8601。"""
    if value is None:
        return None
    return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat()


def _auto_purge_probe() -> bool:
    """自动来源探针：本进程内是否真有一次自动清理在跑（设计 §6.4）。

    模块级注册给 ``db/system_state`` 用 —— 崩溃/重启后它必然是 ``False``，于是
    上一次进程留下的 ``updated_by='auto:retention'`` 维护态会被自愈清除。
    """
    with _job_lock:
        return _auto_purge_running


# 模块级注册：``main.py`` 已 import 本模块（调度 ``purge_expired_records``），
# 所以探针在服务开始受理请求之前就已注册，**不需要改 main.py**（设计 §6.4）。
system_state.set_auto_source_probe(_auto_purge_probe)


def _reserve_job(actor: str) -> dict:
    """占位：置进程内「自动清理进行中」标志并建作业记录；已在跑则 409。

    顺序（设计 §6.4，关键）：**先置标志，再 set_state(True)**。见
    :func:`_orchestrate`。
    """
    global _auto_purge_running, _current_job, _last_run
    with _job_lock:
        if _auto_purge_running or _lock.locked():
            raise RetentionBusy(
                phase=_current_job.get("phase") if _current_job else None
            )
        # 上一个跑完的作业降级成 last_run：契约里 ``running=true`` 时
        # ``last_run`` 是「再上一次」，跑完的作业则留在顶层字段上（phase 会是
        # done/failed —— §5.5 的 phase 取值里有它们，所以必须可观测）。
        if _current_job is not None:
            _last_run = _job_summary(_current_job)
        job = {
            "job_id": str(uuid.uuid4()),
            "actor": actor,
            "state": JOB_STATE_RUNNING,
            "phase": PHASE_ENTERING,
            "started_at": _now_iso(),
            "finished_at": None,
            "progress": {
                "app_logs": {"deleted": 0, "remaining": 0},
                "export_tasks": {"deleted": 0, "remaining": 0},
                "ai_call_logs": {"deleted": 0, "remaining": 0},
                "batches": 0,
                "max_batches": retention_max_batches(),
            },
            "result": None,
            "error": None,
        }
        _current_job = job
        _auto_purge_running = True
        return job


def _job_summary(job: dict) -> dict:
    """作业记录 → ``last_run`` 的形状（设计 §5.5，键逐字）。"""
    state = job["state"] if job["state"] != JOB_STATE_RUNNING else JOB_STATE_FAILED
    return {
        "job_id": job["job_id"],
        "started_at": job["started_at"],
        "finished_at": job["finished_at"] or _now_iso(),
        "state": state,
        "result": job["result"],
    }


def _set_phase(job: dict, phase: str) -> None:
    with _job_lock:
        job["phase"] = phase


def _progress_recorder(job: dict) -> ProgressCallback:
    """把 :func:`run_retention` 的进度回调接到作业记录上（只做浅拷贝记账）。"""

    def _record(phase: str, progress: dict) -> None:
        with _job_lock:
            job["phase"] = phase
            job["progress"] = {
                "app_logs": dict(progress["app_logs"]),
                "export_tasks": dict(progress["export_tasks"]),
                "ai_call_logs": dict(progress["ai_call_logs"]),
                "batches": progress["batches"],
                "max_batches": progress["max_batches"],
            }

    return _record


def _finish_job(
    job: dict, state: str, result: dict | None = None, error: str | None = None
) -> None:
    with _job_lock:
        job["state"] = state
        job["phase"] = PHASE_DONE if state == JOB_STATE_DONE else PHASE_FAILED
        job["finished_at"] = _now_iso()
        job["result"] = result
        job["error"] = error


def _release_reservation() -> None:
    """收尾：清掉「自动清理进行中」标志（作业记录留着给进度端点读结果）。

    标志复位发生在 :func:`_orchestrate` 的 finally 恢复维护态**之后**：反过来
    的话，两者之间到达的请求会看到「enabled=1 + 自动来源 + 探针 False」而触发
    自愈，多写一条无意义的自愈审计（不致命，但会污染审计与 ``last_run``）。
    """
    global _auto_purge_running
    with _job_lock:
        _auto_purge_running = False


async def _orchestrate(job: dict) -> dict:
    """进维护 → 清理 → 恢复。**任何**异常路径都在 finally 里恢复（§6.1/§6.2）。

    调用方已持有进程内占位与清理锁。
    """
    try:
        try:
            # updated_by 用 ``auto:retention``：这就是崩溃自愈的判据（自动来源）。
            system_state.set_state(
                True,
                MAINTENANCE_REASON,
                MAINTENANCE_ANNOUNCEMENT,
                AUTO_MAINTENANCE_ACTOR,
            )
        except Exception as exc:
            # 进不去维护态就**不清理**：宁可这一轮不清理，也不能出现「没有维护态
            # 的清理」——那正是本需求要防的（清理期间用户仍在读写）。
            raise RuntimeError(f"进入维护模式失败，已放弃本次清理：{exc!r}") from exc

        try:
            # 状态已落库：新连接由 realtime.MaintenanceSocketGuard 在握手阶段挡掉；
            # 已建立的连接必须立刻断开（前端不会自己掉线）。断连失败不阻断清理。
            await realtime.disconnect_all_clients()
        except Exception as exc:
            print(f"retention: disconnect_all_clients failed: {exc!r}")

        result = await run_retention(on_progress=_progress_recorder(job))
        with _job_lock:
            job["result"] = result
        return result
    finally:
        _set_phase(job, PHASE_LEAVING)
        try:
            system_state.set_state(False, None, None, AUTO_MAINTENANCE_ACTOR)
        except Exception as exc:
            # 恢复失败要大声喊出来：这是「全站 503 卡死」的唯一来源。进程内的
            # 标志随后会复位，于是下一次 is_enabled() 的自动来源探针会把它自愈
            # 掉（单 worker 前提，见模块 docstring）。
            print(f"retention: failed to leave maintenance: {exc!r}")


async def _run_job(job: dict) -> dict:
    """作业执行体：抢锁 → 编排 → 结果审计 → 收尾。异常一律往上抛，标志与
    维护态由 finally 保证复位。"""
    try:
        if not await acquire_nowait():
            raise RetentionBusy(phase=job["phase"])
        try:
            result = await _orchestrate(job)
        finally:
            release()

        try:
            await _write_run_audit(result, job["actor"])
        except Exception as exc:
            # 清理已经发生；审计失败不能把结果吞掉（与定时侧既有行为一致）。
            print(f"retention: audit write failed: {exc!r}")

        _finish_job(job, JOB_STATE_DONE, result=result)
        return result
    except BaseException as exc:
        # BaseException 包含 CancelledError：进程退出时也要留下「失败」痕迹，
        # 维护态本身已由 _orchestrate 的 finally 恢复。
        # 对外只暴露异常类名（既有约定：不外泄 DSN / 内部细节），细节打日志。
        print(f"retention: purge job {job['job_id']} failed: {exc!r}")
        _finish_job(job, JOB_STATE_FAILED, error=type(exc).__name__)
        raise
    finally:
        _release_reservation()


async def _purge_under_maintenance(actor: str) -> dict:
    """同步等待式的编排入口（:func:`purge_now` / 定时任务共用）。"""
    job = _reserve_job(actor)
    return await _run_job(job)


async def _background_purge(job: dict) -> None:
    """后台任务体：把作业跑完，异常吞掉（不能变成 "never retrieved" 噪音）。"""
    try:
        await _run_job(job)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        print(f"retention: background purge aborted: {exc!r}")


def start_background_purge(actor: str) -> dict:
    """手动入口（HTTP）：把编排丢进后台任务，**立刻**返回作业句柄（§5.5）。

    已在清理中 → :class:`RetentionBusy`（端点转 409，detail 带当前 phase）。
    若连任务都调度不起来（没有运行中的事件循环），占位会被回滚后重新抛出，
    绝不留下「标志置着但没人清」的残留。
    """
    global _background_task
    job = _reserve_job(actor)
    try:
        _background_task = asyncio.create_task(_background_purge(job))
    except BaseException as exc:
        print(f"retention: could not schedule the purge job: {exc!r}")
        _finish_job(job, JOB_STATE_FAILED, error=type(exc).__name__)
        _release_reservation()
        raise
    return {
        "job_id": job["job_id"],
        "started_at": job["started_at"],
        "state": JOB_STATE_RUNNING,
    }


def progress_snapshot() -> dict:
    """``GET /api/system/db/retention/progress`` 的 ``data``（设计 §5.5，逐字）。

    进程内作业状态 + 持久化维护态：作业跑完（或从未跑过）也返回同一套键，
    形状可预期。``running`` 只在作业真的在飞时为 True；跑完的作业留在顶层字段
    上（``phase`` = done/failed，结果在 ``result``），``last_run`` 是再上一次。
    """
    with _job_lock:
        job = _current_job
        last = dict(_last_run) if _last_run is not None else None
        if job is None:
            payload: dict = {
                "running": False,
                "job_id": None,
                "phase": None,
                "started_at": None,
                "finished_at": None,
                "progress": {
                    "app_logs": {"deleted": 0, "remaining": 0},
                    "export_tasks": {"deleted": 0, "remaining": 0},
                    "ai_call_logs": {"deleted": 0, "remaining": 0},
                    "batches": 0,
                    "max_batches": retention_max_batches(),
                },
                "result": None,
                "error": None,
            }
        else:
            payload = {
                "running": job["state"] == JOB_STATE_RUNNING,
                "job_id": job["job_id"],
                "phase": job["phase"],
                "started_at": job["started_at"],
                "finished_at": job["finished_at"],
                "progress": {
                    "app_logs": dict(job["progress"]["app_logs"]),
                    "export_tasks": dict(job["progress"]["export_tasks"]),
                    "ai_call_logs": dict(job["progress"]["ai_call_logs"]),
                    "batches": job["progress"]["batches"],
                    "max_batches": job["progress"]["max_batches"],
                },
                "result": job["result"],
                "error": job["error"],
            }


    # 维护态直读 SQLite（不走 is_enabled 的缓存，也刻意不触发自愈）：这是给
    # 管理员看的现状，不能因为一次读而改变状态。
    state = system_state.get_state()
    payload["maintenance"] = {
        "enabled": state["enabled"],
        "since": _iso_from_epoch(state["since"]),
        "reason": state["reason"],
        "auto": system_state.is_auto_actor(state["updated_by"]),
    }
    payload["last_run"] = last
    return payload


