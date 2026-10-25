"""数据库清理后台任务化 + 维护自动编排 + 进度端点（设计 §5.5 / §6）。

覆盖：
* 手动入口 ``POST /api/system/db/retention/run`` 立刻返回作业句柄
  （``{job_id, started_at, state: running}``），不再同步等结果；
* 已在清理 → **409**（沿用既有约定，detail 里带当前 phase）；
* ``GET /api/system/db/retention/progress`` 的形状（冻结键集）与生命周期
  （running → phase done/failed + result/error）；
* **手动 / 定时同源**：两者都进维护态（``updated_by='auto:retention'``）、都在
  结束时恢复（成功、失败、以及「进不去维护态就不清理」）；
* 失败路径：清理抛异常时维护态照样恢复、进度记 failed、且对外不泄露内部细节
  （detail/JSON 里不得出现 DSN / 密码）；
* 审计：发起行（管理员）+ 结果行（``retention_purge`` / ``warning``）。

离线：业务库仓库是 fake、session 依赖 yield None、socket 断连被替身接管；
维护态库用 ``ZVMS_SYSTEM_DB`` 隔离到 ``tmp_path``，绝不碰仓库根下的真实状态库。

跑法：``.venv/bin/python -m pytest tests/test_retention_async.py -q``
"""

from __future__ import annotations

import asyncio
import sqlite3
import time
import uuid
from contextlib import closing
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from db import backends, system_state
from db.oracle.session import get_session
from routers import db_system_router
from tasks import retention
from util.object_id import authenticated_log_user, get_current_user

ADMIN_OID = "64b7f0c2e1a3b4c5d6e7f801"
RUN_URL = "/api/system/db/retention/run"
PROGRESS_URL = "/api/system/db/retention/progress"
BUSY_MESSAGE = retention.BUSY_MESSAGE
AUTO_ACTOR = retention.AUTO_MAINTENANCE_ACTOR

# §5.5 冻结的形状。
PROGRESS_KEYS = {
    "running",
    "job_id",
    "phase",
    "started_at",
    "finished_at",
    "maintenance",
    "progress",
    "result",
    "error",
    "last_run",
}
MAINTENANCE_KEYS = {"enabled", "since", "reason", "auto"}
PROGRESS_COUNTER_KEYS = {"deleted", "remaining"}
PHASES = {
    "entering_maintenance",
    "purging_app_logs",
    "purging_export_tasks",
    "purging_ai_call_logs",
    "purging_ai_agent_events",
    "purging_ai_prompt_audit",
    "leaving_maintenance",
    "done",
    "failed",
}
RUN_KEYS = {"job_id", "started_at", "state"}


# ---------------------------------------------------------------------------
# fakes
# ---------------------------------------------------------------------------


class _FakeLogsRepo:
    """Fake ``AppLogsRepo``：记录批量删除 / 计数 / 审计写入。"""

    def __init__(
        self,
        deleted: list[int] | None = None,
        deletable: int = 0,
        delay: float = 0.0,
    ) -> None:
        self.rowcounts = list(deleted or [])
        self.deletable = deletable
        self.delay = delay
        self.purge_calls: list[tuple[datetime, int]] = []
        self.count_calls: list[datetime] = []
        self.inserted: list[dict] = []
        self.fail: Exception | None = None

    def _guard(self) -> None:
        if self.fail is not None:
            raise self.fail

    async def count_expired(self, cutoff: datetime) -> int:
        self._guard()
        self.count_calls.append(cutoff)
        return self.deletable

    async def purge_batch(self, cutoff: datetime, batch_size: int) -> int:
        self._guard()
        if self.delay:
            # 让「作业在飞」有一个可观测的窗口（409 / 进度用）。
            await asyncio.sleep(self.delay)
        self.purge_calls.append((cutoff, batch_size))
        return self.rowcounts.pop(0) if self.rowcounts else 0

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
    def __init__(self, deleted: list[int] | None = None, deletable: int = 0) -> None:
        self.rowcounts = list(deleted or [])
        self.deletable = deletable
        self.purge_calls: list[tuple[datetime, int]] = []
        self.count_calls: list[datetime] = []

    async def count_expired_finished(self, cutoff: datetime) -> int:
        self.count_calls.append(cutoff)
        return self.deletable

    async def purge_finished_batch(self, cutoff: datetime, batch_size: int) -> int:
        self.purge_calls.append((cutoff, batch_size))
        return self.rowcounts.pop(0) if self.rowcounts else 0


class _FakeAICallLogsRepo:
    """Fake ``AICallLogsRepo``：与 tasks fake 同风格（单批量 + 过期计数）。"""

    def __init__(self, deleted: list[int] | None = None, deletable: int = 0) -> None:
        self.rowcounts = list(deleted or [])
        self.deletable = deletable
        self.purge_calls: list[tuple[datetime, int]] = []
        self.count_calls: list[datetime] = []

    async def count_expired(self, cutoff: datetime) -> int:
        self.count_calls.append(cutoff)
        return self.deletable

    async def purge_batch(self, cutoff: datetime, batch_size: int) -> int:
        self.purge_calls.append((cutoff, batch_size))
        return self.rowcounts.pop(0) if self.rowcounts else 0


class _FakeAIAgentEventsRepo(_FakeAICallLogsRepo):
    """Fake ``AIAgentEventsRepo``：工具事件与调用流水同款单批量删除。"""


class _FakeAIPromptAuditRepo(_FakeAICallLogsRepo):
    """Fake ``AIPromptAuditRepo``：正文留存走 ``purge_before``（同签名）。"""

    async def purge_before(self, cutoff: datetime, batch_size: int) -> int:
        self.purge_calls.append((cutoff, batch_size))
        return self.rowcounts.pop(0) if self.rowcounts else 0


@pytest.fixture(autouse=True)
def _clean_job_state(monkeypatch):
    """进程内作业状态是模块级的（单 worker 前提），用例之间必须归零。"""
    monkeypatch.setattr(retention, "_current_job", None)
    monkeypatch.setattr(retention, "_last_run", None)
    monkeypatch.setattr(retention, "_auto_purge_running", False)
    yield
    assert retention._auto_purge_running is False, "用例结束时仍有清理标志残留"
    try:
        retention.release()
    except RuntimeError:  # 锁没被持有
        pass


@pytest.fixture()
def state_db(tmp_path, monkeypatch) -> Path:
    """维护态库隔离到 tmp_path（真实状态库不可被测试污染）。"""
    path = tmp_path / "system_state.db"
    monkeypatch.setenv("ZVMS_SYSTEM_DB", str(path))
    system_state.invalidate_cache()
    yield path
    system_state.invalidate_cache()


@pytest.fixture()
def repos(monkeypatch):
    """三张既有表的 fake（返回三元组，既有用例的拆包方式不变）。"""
    logs = _FakeLogsRepo(deleted=[3], deletable=0)
    tasks = _FakeTasksRepo(deleted=[0], deletable=0)
    ai = _FakeAICallLogsRepo(deleted=[0], deletable=0)
    monkeypatch.setattr(backends, "get_applogs_repo", lambda session=None: logs)
    monkeypatch.setattr(backends, "get_tasks_repo", lambda session=None: tasks)
    monkeypatch.setattr(backends, "get_ai_call_logs_repo", lambda session=None: ai)
    return logs, tasks, ai


@pytest.fixture(autouse=True)
def governance_repos(monkeypatch):
    """V033 的两张表：每条用例都要有桩，否则清理会真的去连 ADB。"""
    events = _FakeAIAgentEventsRepo(deleted=[0], deletable=0)
    prompts = _FakeAIPromptAuditRepo(deleted=[0], deletable=0)
    monkeypatch.setattr(backends, "get_ai_agent_events_repo", lambda session=None: events)
    monkeypatch.setattr(backends, "get_ai_prompt_audit_repo", lambda session=None: prompts)
    return events, prompts


@pytest.fixture()
def disconnects(monkeypatch):
    calls: list[int] = []

    async def _disconnect() -> int:
        calls.append(len(calls) + 1)
        return 0

    monkeypatch.setattr(retention.realtime, "disconnect_all_clients", _disconnect)
    return calls


@pytest.fixture()
def client(monkeypatch):
    state = {"_id": ADMIN_OID, "roles": ["admin"], "banned": False}

    async def get_by_oid(oid):
        return state

    async def get_names(oids):
        return {oid: "管理员" for oid in oids}

    async def session():
        yield None

    monkeypatch.setattr(
        backends,
        "get_users_repo",
        lambda session=None: SimpleNamespace(
            get_by_oid=get_by_oid, get_names=get_names
        ),
    )
    app = FastAPI()
    app.include_router(db_system_router.router, prefix="/api/system/db")
    app.dependency_overrides[get_session] = session
    app.dependency_overrides[get_current_user] = lambda: {
        "id": state["_id"],
        "per": ["admin"],
    }
    app.dependency_overrides[authenticated_log_user] = lambda: {"id": state["_id"]}
    with TestClient(app) as http:
        yield http, state


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _query(path: Path, sql: str) -> list[tuple]:
    """裸读状态库；表还没建（作业一次都没跑过）时返回空。"""
    try:
        with closing(sqlite3.connect(path)) as conn:
            return conn.execute(sql).fetchall()
    except sqlite3.OperationalError:  # no such table
        return []


def _events(path: Path) -> list[tuple]:
    return _query(
        path,
        "SELECT enabled, actor_oid, reason FROM maintenance_events ORDER BY id",
    )


def _state_row(path: Path) -> tuple:
    rows = _query(
        path, "SELECT enabled, updated_by FROM maintenance_state WHERE id = 1"
    )
    return rows[0] if rows else (None, None)


def _progress(http) -> dict:
    response = http.get(PROGRESS_URL)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "ok" and body["code"] == 200
    return body["data"]


def _wait_until(http, predicate, timeout: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout
    data = _progress(http)
    while time.monotonic() < deadline:
        if predicate(data):
            return data
        time.sleep(0.01)
        data = _progress(http)
    raise AssertionError(f"progress never matched within {timeout}s: {data}")


def _wait_terminal(http, timeout: float = 5.0) -> dict:
    return _wait_until(
        http, lambda data: not data["running"] and data["phase"] in {"done", "failed"}, timeout
    )


def _assert_frozen_shape(data: dict) -> None:
    assert set(data) == PROGRESS_KEYS
    assert set(data["maintenance"]) == MAINTENANCE_KEYS
    assert set(data["progress"]) == {
        "app_logs",
        "export_tasks",
        "ai_call_logs",
        "ai_agent_events",
        "ai_prompt_audit",
        "batches",
        "max_batches",
    }
    for table in (
        "app_logs",
        "export_tasks",
        "ai_call_logs",
        "ai_agent_events",
        "ai_prompt_audit",
    ):
        assert set(data["progress"][table]) == PROGRESS_COUNTER_KEYS
    assert isinstance(data["progress"]["batches"], int)
    assert isinstance(data["progress"]["max_batches"], int)
    if data["job_id"] is not None:
        uuid.UUID(data["job_id"])  # 必须是 uuid
    if data["phase"] is not None:
        assert data["phase"] in PHASES
    if data["last_run"] is not None:
        assert set(data["last_run"]) == {
            "job_id",
            "started_at",
            "finished_at",
            "state",
            "result",
        }
        assert data["last_run"]["state"] in {"done", "failed"}


# ---------------------------------------------------------------------------
# 同步编排：手动入口（purge_now）
# ---------------------------------------------------------------------------


def test_manual_entry_enters_maintenance_and_restores_it(state_db, repos, disconnects):
    result = asyncio.run(retention.purge_now(actor=ADMIN_OID))

    assert result["app_logs"]["deleted"] == 3
    assert result["export_tasks"]["deleted"] == 0
    assert disconnects == [1]  # 复用 realtime.disconnect_all_clients()
    assert _events(state_db) == [
        (1, AUTO_ACTOR, retention.MAINTENANCE_REASON),
        (0, AUTO_ACTOR, None),
    ]
    assert _state_row(state_db) == (0, AUTO_ACTOR)
    # 结束时恢复：探测（探针）读到 False。
    assert system_state.is_enabled() is False


def test_announcement_matches_the_frozen_text(state_db, repos, disconnects):
    asyncio.run(retention.purge_now(actor=ADMIN_OID))

    with closing(sqlite3.connect(state_db)) as conn:
        rows = conn.execute(
            "SELECT announcement FROM maintenance_events WHERE enabled = 1"
        ).fetchall()
    assert rows == [(retention.MAINTENANCE_ANNOUNCEMENT,)]
    assert len(retention.MAINTENANCE_ANNOUNCEMENT) <= 300
    assert retention.MAINTENANCE_REASON == "数据库清理"


def test_manual_entry_writes_one_result_audit_row(state_db, repos, disconnects):
    asyncio.run(retention.purge_now(actor=ADMIN_OID))

    logs, _, _ = repos
    assert len(logs.inserted) == 1
    row = logs.inserted[0]
    assert row["type"] == "retention_purge"
    assert row["level"] == "warning"
    assert row["user"] == ADMIN_OID
    assert "app_logs 删除 3 行" in row["data"]


# ---------------------------------------------------------------------------
# 同步编排：定时入口（purge_expired_records）
# ---------------------------------------------------------------------------


def test_scheduled_entry_uses_the_same_orchestration(state_db, repos, disconnects):
    logs, _, _ = repos
    logs.rowcounts = [5000, 3]
    logs.deletable = 4

    deleted = asyncio.run(retention.purge_expired_records())

    assert deleted == 5003
    assert disconnects == [1]
    assert [event[0] for event in _events(state_db)] == [1, 0]
    assert {event[1] for event in _events(state_db)} == {AUTO_ACTOR}
    assert system_state.is_enabled() is False

    # 定时侧审计沿用既有的「scheduler」归属。
    assert len(logs.inserted) == 1
    assert logs.inserted[0]["user"] == retention.SCHEDULER_USER


def test_scheduled_entry_never_raises_and_still_restores(
    state_db, repos, disconnects, capsys
):
    logs, _, _ = repos
    logs.fail = RuntimeError("boom")

    assert asyncio.run(retention.purge_expired_records()) == 0

    out = capsys.readouterr().out
    assert "purge_expired_records failed" in out
    assert "boom" in out
    assert logs.inserted == []
    assert _state_row(state_db) == (0, AUTO_ACTOR)
    assert system_state.is_enabled() is False


def test_scheduled_entry_skips_without_touching_maintenance(
    state_db, repos, disconnects, capsys
):
    """已经在清理：定时侧安静跳过，绝不二次进维护（也不二次恢复）。"""
    logs, _, _ = repos

    async def _scenario() -> int:
        assert await retention.acquire_nowait() is True
        try:
            return await retention.purge_expired_records()
        finally:
            retention.release()

    assert asyncio.run(_scenario()) == 0
    assert logs.purge_calls == []
    assert "skipped" in capsys.readouterr().out
    assert _events(state_db) == []  # 一行都没写


# ---------------------------------------------------------------------------
# 失败 / 异常路径（最高风险：维护态残留 ⇒ 全站 503）
# ---------------------------------------------------------------------------


def test_purge_failure_restores_maintenance(state_db, repos, disconnects):
    logs, _, _ = repos
    logs.fail = ConnectionError("private DSN and password")

    with pytest.raises(ConnectionError):
        asyncio.run(retention.purge_now(actor=ADMIN_OID))

    assert [event[0] for event in _events(state_db)] == [1, 0]
    assert _state_row(state_db) == (0, AUTO_ACTOR)
    assert system_state.is_enabled() is False
    assert system_state.is_enabled_fresh() is False

    snapshot = retention.progress_snapshot()
    assert snapshot["running"] is False
    assert snapshot["phase"] == "failed"
    assert snapshot["error"] == "ConnectionError"  # 只暴露异常类名
    assert "private" not in str(snapshot) and "DSN" not in str(snapshot)


def test_maintenance_entry_failure_aborts_the_purge(state_db, repos, disconnects):
    """进不去维护态 ⇒ 不清理（绝不允许「没有维护态的清理」）。"""
    logs, _, _ = repos
    real_set_state = system_state.set_state

    def _set_state(enabled, reason, announcement, actor_oid):
        if enabled:
            raise RuntimeError("sqlite is down")
        return real_set_state(enabled, reason, announcement, actor_oid)

    system_state.set_state = _set_state  # type: ignore[assignment]
    try:
        with pytest.raises(RuntimeError):
            asyncio.run(retention.purge_now(actor=ADMIN_OID))
    finally:
        system_state.set_state = real_set_state  # type: ignore[assignment]

    assert logs.purge_calls == [] and logs.count_calls == []
    # 一行都没「开启」过：写进去的只有收尾那次恢复（enabled=0）。
    assert _state_row(state_db)[0] == 0
    assert system_state.get_state()["enabled"] is False
    assert retention.progress_snapshot()["phase"] == "failed"


def test_purge_cancellation_still_restores_maintenance(state_db, repos, disconnects):
    """进程退出（任务被取消）也要恢复维护态。"""
    logs, _, _ = repos
    logs.delay = 0.05  # 保证 cancel 时任务确实还挂在 await 上

    async def _scenario() -> str:
        task = asyncio.ensure_future(retention.purge_now(actor=ADMIN_OID))
        await asyncio.sleep(0)  # 让作业跑到第一个 await
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return "cancelled"

    assert asyncio.run(_scenario()) == "cancelled"
    assert [event[0] for event in _events(state_db)] == [1, 0]
    assert system_state.is_enabled() is False


# ---------------------------------------------------------------------------
# HTTP：POST /retention/run 后台任务化
# ---------------------------------------------------------------------------


def test_run_returns_the_job_handle_immediately(client, repos, disconnects, state_db):
    http, _ = client
    logs, _, _ = repos
    logs.delay = 0.2  # 请求返回时作业仍在跑

    response = http.post(RUN_URL)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok" and body["code"] == 200
    assert set(body["data"]) == RUN_KEYS
    assert body["data"]["state"] == "running"
    uuid.UUID(body["data"]["job_id"])
    assert datetime.fromisoformat(body["data"]["started_at"])

    # 发起行：管理员 + 后台任务 id（结果行由后台任务收尾时写）。
    assert len(logs.inserted) == 1
    assert logs.inserted[0]["user"] == ADMIN_OID
    assert body["data"]["job_id"] in logs.inserted[0]["data"]

    data = _wait_terminal(http)
    assert data["job_id"] == body["data"]["job_id"]
    assert data["phase"] == "done"
    assert data["result"]["app_logs"]["deleted"] == 3
    # 结果行按发起管理员归属。
    assert [row["user"] for row in logs.inserted] == [ADMIN_OID, ADMIN_OID]
    assert "app_logs 删除 3 行" in logs.inserted[1]["data"]
    assert system_state.is_enabled() is False


def test_progress_idle_shape_is_frozen(client, state_db):
    http, _ = client

    data = _progress(http)

    _assert_frozen_shape(data)
    assert data["running"] is False
    assert data["job_id"] is None and data["phase"] is None
    assert data["result"] is None and data["error"] is None
    assert data["last_run"] is None
    assert data["maintenance"] == {
        "enabled": False,
        "since": None,
        "reason": None,
        "auto": False,
    }
    assert data["progress"]["app_logs"] == {"deleted": 0, "remaining": 0}
    assert data["progress"]["ai_call_logs"] == {"deleted": 0, "remaining": 0}
    assert data["progress"]["max_batches"] == retention.retention_max_batches()


def test_progress_while_running_reports_phase_counts_and_maintenance(
    client, repos, state_db
):
    http, _ = client
    logs, _, _ = repos
    logs.delay = 0.3
    logs.deletable = 10  # 进度里的剩余量（展示用初始计数）

    http.post(RUN_URL)
    data = _wait_until(
        http, lambda d: d["running"] and d["phase"] == "purging_app_logs"
    )

    _assert_frozen_shape(data)
    assert data["running"] is True
    assert data["phase"] == "purging_app_logs"
    assert data["finished_at"] is None
    assert data["progress"]["max_batches"] == retention.retention_max_batches()
    # 维护态：自动来源、理由与公告都按契约。
    assert data["maintenance"]["enabled"] is True
    assert data["maintenance"]["auto"] is True
    assert data["maintenance"]["reason"] == "数据库清理"
    assert datetime.fromisoformat(data["maintenance"]["since"])

    terminal = _wait_terminal(http)
    assert terminal["phase"] == "done"
    assert terminal["running"] is False
    assert terminal["result"]["app_logs"]["deleted"] == 3
    assert terminal["maintenance"]["enabled"] is False


def test_run_is_409_while_a_purge_is_running(client, repos, state_db):
    http, _ = client
    logs, _, _ = repos
    logs.delay = 1.0  # 足够宽的窗口，让 409 与其断言都在「作业在飞」时发生

    first = http.post(RUN_URL)
    assert first.status_code == 200
    _wait_until(http, lambda d: d["running"] and d["phase"] == "purging_app_logs")

    before = (len(logs.purge_calls), len(logs.inserted))
    second = http.post(RUN_URL)

    assert second.status_code == 409
    detail = second.json()["detail"]
    assert detail.startswith(BUSY_MESSAGE)
    assert "purging_app_logs" in detail  # 携带当前 phase
    # 被拒的请求什么都没做（没有新的删除调用，也没有新的审计行）。
    assert (len(logs.purge_calls), len(logs.inserted)) == before
    assert len(logs.inserted) == 1  # 只有发起行；结果行要等作业收尾

    terminal = _wait_terminal(http, timeout=10.0)
    assert terminal["job_id"] == first.json()["data"]["job_id"]
    assert terminal["phase"] == "done"


def test_run_failure_is_surfaced_in_progress_without_leaking_internals(
    client, repos, state_db
):
    http, _ = client
    logs, _, _ = repos
    logs.fail = ConnectionError("private DSN and password")

    response = http.post(RUN_URL)
    assert response.status_code == 200  # 后台任务化：请求本身成功
    assert "DSN" not in response.text and "private" not in response.text

    terminal = _wait_terminal(http)
    assert terminal["phase"] == "failed"
    assert terminal["error"] == "ConnectionError"
    assert terminal["result"] is None
    assert system_state.get_state()["enabled"] is False

    body = http.get(PROGRESS_URL).text
    assert "DSN" not in body and "private" not in body


def test_run_then_progress_exposes_last_run_after_the_next_job(
    client, repos, state_db
):
    """契约示例：``running=true`` 时 ``last_run`` 是上一次跑完的作业。"""
    http, _ = client

    first = http.post(RUN_URL).json()["data"]
    assert _wait_terminal(http)["phase"] == "done"

    repos[0].delay = 0.3
    http.post(RUN_URL)
    data = _wait_until(http, lambda d: d["running"])
    assert data["last_run"] is not None
    assert data["last_run"]["job_id"] == first["job_id"]
    assert data["last_run"]["state"] == "done"
    assert data["last_run"]["result"]["app_logs"]["deleted"] == 3

    assert _wait_terminal(http)["phase"] == "done"


def test_progress_requires_system_admin(client, state_db):
    http, state = client
    assert http.get(PROGRESS_URL).status_code == 200  # admin ok

    state.update(roles=["student"])
    assert http.get(PROGRESS_URL).status_code == 403
    assert http.post(RUN_URL).status_code == 403

    state.update(roles=["admin"], banned=True)
    assert http.get(PROGRESS_URL).status_code == 403


def test_progress_without_login_is_401(state_db):
    app = FastAPI()
    app.include_router(db_system_router.router, prefix="/api/system/db")
    http = TestClient(app)

    assert http.get(PROGRESS_URL).status_code == 401
    assert http.post(RUN_URL).status_code == 401


# ---------------------------------------------------------------------------
# run_retention 的进度回调（不改既有返回语义）
# ---------------------------------------------------------------------------


def test_run_retention_progress_callback_is_optional(
    state_db, repos, governance_repos
):
    logs, tasks, ai = repos
    events, prompts = governance_repos

    seen: list[tuple[str, dict]] = []

    def _on_progress(phase: str, progress: dict) -> None:
        seen.append((phase, {k: dict(v) if isinstance(v, dict) else v for k, v in progress.items()}))

    result = asyncio.run(retention.run_retention(on_progress=_on_progress))

    phases = [phase for phase, _ in seen]
    assert retention.PHASE_PURGING_APP_LOGS in phases
    assert retention.PHASE_PURGING_EXPORT_TASKS in phases
    assert retention.PHASE_PURGING_AI_CALL_LOGS in phases
    final = seen[-1][1]
    assert final["app_logs"] == {"deleted": 3, "remaining": 0}
    assert final["export_tasks"] == {"deleted": 0, "remaining": 0}
    assert final["ai_call_logs"] == {"deleted": 0, "remaining": 0}
    assert final["ai_agent_events"] == {"deleted": 0, "remaining": 0}
    assert final["ai_prompt_audit"] == {"deleted": 0, "remaining": 0}
    assert final["batches"] == 5
    assert final["max_batches"] == retention.retention_max_batches()
    assert final["batches"] == 5
    # 返回值语义不变（进度里的 remaining 只是展示口径），只是多了两张表。
    assert set(result) == {
        "app_logs",
        "export_tasks",
        "ai_call_logs",
        "ai_agent_events",
        "ai_prompt_audit",
        "batches",
    }
    for table in (
        "app_logs",
        "export_tasks",
        "ai_call_logs",
        "ai_agent_events",
        "ai_prompt_audit",
    ):
        assert set(result[table]) == {"cutoff", "deleted", "remaining"}
    assert result["app_logs"]["deleted"] == 3
    assert logs.purge_calls and tasks.purge_calls and ai.purge_calls
    assert events.purge_calls and prompts.purge_calls


def test_retention_module_registers_the_auto_source_probe():
    """探针由 tasks.retention 在 **import 时**注册（因此不需要改 main.py）。"""
    assert system_state._auto_source_probe is retention._auto_purge_probe
    assert retention._auto_purge_probe() is False  # 没有作业在跑
