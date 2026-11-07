"""Offline tests for the retention endpoints (design §2 / C7 / §5.5).

Pins the admin-facing contract of ``routers/db_system_router.py``:

* ``POST /api/system/db/retention/preview`` → per-table ``{cutoff, deletable}``
  and **nothing is deleted** (only counts reach the repositories);
* ``POST /api/system/db/retention/run`` → **后台任务**：立刻返回
  ``{job_id, started_at, state}``（HTTP 200 / ``status=ok``），结果是异步的，从
  ``GET /api/system/db/retention/progress`` 取（``phase`` + ``result``）；
* run 的审计：一条发起行（管理员）+ 一条结果行，都是
  ``retention_purge`` / ``warning``；
* authorization is re-checked against the database before any work happens
  (403 for a non-admin or a banned admin, without touching a repository);
* a concurrent purge is 409 with the frozen detail prefix (plus the current
  phase), and a failing database surfaces in the progress payload instead of a
  503 — 且不泄露内部细节（\"DSN\"/\"password\" 不出现）。

Offline: the users / app-logs / tasks repositories are fakes and the session
dependency yields ``None``; no engine is created. 维护态库用 ``ZVMS_SYSTEM_DB``
隔离到 ``tmp_path``（清理会进维护态）。

Run from the repository root::

    ./.venv/bin/python -m pytest tests/test_db_retention_router.py -q
"""

from __future__ import annotations

import asyncio
import time
import uuid
from datetime import datetime, timedelta
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
BUSY_DETAIL = "清理任务正在执行中，请稍后重试"

PREVIEW_URL = "/api/system/db/retention/preview"
RUN_URL = "/api/system/db/retention/run"
PROGRESS_URL = "/api/system/db/retention/progress"


class _FakeLogsRepo:
    """Fake ``AppLogsRepo``: counts, purges and records audit inserts."""

    def __init__(
        self, deletable: int = 0, deleted: list[int] | None = None, delay: float = 0.0
    ) -> None:
        self.deletable = deletable
        self.rowcounts = list(deleted or [])
        self.delay = delay
        self.count_calls: list[datetime] = []
        self.purge_calls: list[tuple[datetime, int]] = []
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
            await asyncio.sleep(self.delay)  # 让「作业在飞」有可观测窗口
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
        client_ip: str = "",
        user_agent: str = "",
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
    def __init__(self, deletable: int = 0, deleted: list[int] | None = None) -> None:
        self.deletable = deletable
        self.rowcounts = list(deleted or [])
        self.count_calls: list[datetime] = []
        self.purge_calls: list[tuple[datetime, int]] = []

    async def count_expired_finished(self, cutoff: datetime) -> int:
        self.count_calls.append(cutoff)
        return self.deletable

    async def purge_finished_batch(self, cutoff: datetime, batch_size: int) -> int:
        self.purge_calls.append((cutoff, batch_size))
        return self.rowcounts.pop(0) if self.rowcounts else 0


class _FakeAICallLogsRepo:
    """Fake ``AICallLogsRepo``：与 tasks fake 同风格（过期计数 + 单批量删除）。"""

    def __init__(self, deletable: int = 0, deleted: list[int] | None = None) -> None:
        self.deletable = deletable
        self.rowcounts = list(deleted or [])
        self.count_calls: list[datetime] = []
        self.purge_calls: list[tuple[datetime, int]] = []
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
        self.purge_calls.append((cutoff, batch_size))
        return self.rowcounts.pop(0) if self.rowcounts else 0


@pytest.fixture
def repos(monkeypatch):
    """Wire fake repositories through ``backends`` (既有三张表)."""
    logs = _FakeLogsRepo()
    tasks = _FakeTasksRepo()
    ai = _FakeAICallLogsRepo()
    monkeypatch.setattr(backends, "get_applogs_repo", lambda session=None: logs)
    monkeypatch.setattr(backends, "get_tasks_repo", lambda session=None: tasks)
    monkeypatch.setattr(backends, "get_ai_call_logs_repo", lambda session=None: ai)
    return logs, tasks, ai


@pytest.fixture(autouse=True)
def _governance_repos(monkeypatch):
    """V033 的两张表：没有桩就会真的去连 ADB。"""
    events = _FakeAICallLogsRepo()
    prompts = _FakeAICallLogsRepo()

    async def _purge_before(cutoff: datetime, batch_size: int) -> int:
        prompts.purge_calls.append((cutoff, batch_size))
        return prompts.rowcounts.pop(0) if prompts.rowcounts else 0

    prompts.purge_before = _purge_before  # type: ignore[attr-defined]
    monkeypatch.setattr(backends, "get_ai_agent_events_repo", lambda session=None: events)
    monkeypatch.setattr(backends, "get_ai_prompt_audit_repo", lambda session=None: prompts)
    return events, prompts


@pytest.fixture(autouse=True)
def _no_append_only_purge(monkeypatch):
    """只增表（ban_records/ban_violations/...）的清理直连库，本文件没有桩：统一
    归零，让编排仍走完同一条路径。"""

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
    yield
    try:
        retention.release()
    except RuntimeError:  # not held
        pass


@pytest.fixture(autouse=True)
def _clean_job_state(monkeypatch):
    """进程内作业状态是模块级的（单 worker 前提）；用例之间必须归零。"""
    monkeypatch.setattr(retention, "_current_job", None)
    monkeypatch.setattr(retention, "_last_run", None)
    monkeypatch.setattr(retention, "_auto_purge_running", False)
    yield
    assert retention._auto_purge_running is False


@pytest.fixture(autouse=True)
def _isolated_state_db(tmp_path, monkeypatch) -> Path:
    """清理会进维护态：状态库隔离到 tmp_path，别碰仓库根下的真实状态库。"""
    path = tmp_path / "system_state.db"
    monkeypatch.setenv("ZVMS_SYSTEM_DB", str(path))
    system_state.invalidate_cache()
    yield path
    system_state.invalidate_cache()


@pytest.fixture
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
    # ``inject_log`` authenticates through its own dependency, not through
    # ``get_current_user`` — overriding the latter alone leaves the audit path
    # at 401.
    app.dependency_overrides[authenticated_log_user] = lambda: {"id": state["_id"]}
    with TestClient(app) as http:
        yield http, state


def _progress(http) -> dict:
    response = http.get(PROGRESS_URL)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "ok" and body["code"] == 200
    return body["data"]


def _wait_until(http, predicate, timeout: float = 10.0) -> dict:
    deadline = time.monotonic() + timeout
    data = _progress(http)
    while time.monotonic() < deadline:
        if predicate(data):
            return data
        time.sleep(0.01)
        data = _progress(http)
    raise AssertionError(f"progress never matched within {timeout}s: {data}")


def _wait_terminal(http, timeout: float = 10.0) -> dict:
    return _wait_until(
        http,
        lambda data: not data["running"] and data["phase"] in {"done", "failed"},
        timeout,
    )


# ---------------------------------------------------------------------------
# authorization
# ---------------------------------------------------------------------------


def test_retention_requires_login():
    app = FastAPI()
    app.include_router(db_system_router.router, prefix="/api/system/db")
    # Auth runs before any connection/session is created.
    assert TestClient(app).post(PREVIEW_URL).status_code == 401
    assert TestClient(app).post(RUN_URL).status_code == 401


@pytest.mark.parametrize(
    "roles,banned", [(["student"], False), (["department"], False), (["admin"], True)]
)
@pytest.mark.parametrize("url", [PREVIEW_URL, RUN_URL])
def test_retention_rechecks_privileges_before_touching_any_repository(
    client, repos, roles, banned, url
):
    """403 for a non-admin / banned admin, with no repository call at all.

    The fake repositories record every call, so "nothing happened" is asserted
    rather than assumed.
    """
    http, state = client
    state.update(roles=roles, banned=banned)
    logs, tasks, ai = repos

    assert http.post(url).status_code == 403
    assert logs.count_calls == [] and logs.purge_calls == []
    assert tasks.count_calls == [] and tasks.purge_calls == []
    assert logs.inserted == []


# ---------------------------------------------------------------------------
# preview
# ---------------------------------------------------------------------------


def test_preview_returns_both_cutoffs_and_counts(client, repos, _governance_repos):
    http, _ = client
    logs, tasks, ai = repos
    events, prompts = _governance_repos
    logs.deletable = 7
    tasks.deletable = 2
    ai.deletable = 4
    prompts.deletable = 5
    events.deletable = 3

    before = datetime.now()
    response = http.post(PREVIEW_URL)
    after = datetime.now()

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok" and body["code"] == 200
    assert set(body["data"]) == {
        "app_logs",
        "export_tasks",
        "ai_call_logs",
        "ai_prompt_audit",
        "ai_agent_events",
    }
    for table in (
        "app_logs",
        "export_tasks",
        "ai_call_logs",
        "ai_prompt_audit",
        "ai_agent_events",
    ):
        assert set(body["data"][table]) == {"cutoff", "deletable"}
    assert body["data"]["app_logs"]["deletable"] == 7
    assert body["data"]["export_tasks"]["deletable"] == 2
    assert body["data"]["ai_call_logs"]["deletable"] == 4
    assert body["data"]["ai_prompt_audit"]["deletable"] == 5
    assert body["data"]["ai_agent_events"]["deletable"] == 3

    logs_cutoff = datetime.fromisoformat(body["data"]["app_logs"]["cutoff"])
    tasks_cutoff = datetime.fromisoformat(body["data"]["export_tasks"]["cutoff"])
    ai_cutoff = datetime.fromisoformat(body["data"]["ai_call_logs"]["cutoff"])
    prompts_cutoff = datetime.fromisoformat(body["data"]["ai_prompt_audit"]["cutoff"])
    events_cutoff = datetime.fromisoformat(body["data"]["ai_agent_events"]["cutoff"])
    assert before - timedelta(days=90) <= logs_cutoff <= after - timedelta(days=90)
    assert before - timedelta(days=30) <= tasks_cutoff <= after - timedelta(days=30)
    assert before - timedelta(days=90) <= ai_cutoff <= after - timedelta(days=90)
    assert before - timedelta(days=30) <= prompts_cutoff <= after - timedelta(days=30)
    assert before - timedelta(days=90) <= events_cutoff <= after - timedelta(days=90)

    # The counts were asked for with the very cutoffs the response reports.
    assert logs.count_calls == [logs_cutoff]
    assert tasks.count_calls == [tasks_cutoff]
    assert ai.count_calls == [ai_cutoff]
    assert prompts.count_calls == [prompts_cutoff]
    assert events.count_calls == [events_cutoff]


def test_preview_deletes_nothing(client, repos):
    http, _ = client
    logs, tasks, ai = repos

    assert http.post(PREVIEW_URL).status_code == 200

    assert logs.purge_calls == []
    assert tasks.purge_calls == []
    assert ai.purge_calls == []
    assert logs.inserted == []


def test_preview_ai_count_failure_degrades_to_null(client, repos, caplog):
    """AI 表查不了只降级成 ``deletable: null``（只读诊断不能整页挂）。"""
    http, _ = client
    logs, tasks, ai = repos
    logs.deletable = 7
    tasks.deletable = 2
    ai.fail = RuntimeError("AI_CALL_LOGS is not migrated")

    response = http.post(PREVIEW_URL)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok" and body["code"] == 200
    assert body["data"]["app_logs"]["deletable"] == 7
    assert body["data"]["export_tasks"]["deletable"] == 2
    assert body["data"]["ai_call_logs"]["deletable"] is None
    assert datetime.fromisoformat(body["data"]["ai_call_logs"]["cutoff"])
    assert "ai_call_logs count failed" in caplog.text
    # 内部细节（表名/异常文案）不进响应。
    assert "not migrated" not in response.text


# ---------------------------------------------------------------------------
# run: 后台任务化（设计 §5.5）
# ---------------------------------------------------------------------------


def test_run_starts_a_background_job_and_audits_the_actor(client, repos, monkeypatch):
    """POST /retention/run 立刻给作业句柄；结果通过进度端点取（不再同步返回）。"""
    http, _ = client
    logs, tasks, ai = repos
    logs.delay = 0.2  # 响应返回时作业仍在跑
    logs.rowcounts = [3]  # one short batch
    tasks.rowcounts = [0]  # nothing expired

    response = http.post(RUN_URL)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok" and body["code"] == 200
    assert set(body["data"]) == {"job_id", "started_at", "state"}
    assert body["data"]["state"] == "running"
    assert uuid.UUID(body["data"]["job_id"])
    assert datetime.fromisoformat(body["data"]["started_at"])

    # 发起审计：管理员 + 后台任务 id。
    assert len(logs.inserted) == 1
    start = logs.inserted[0]
    assert start["type"] == "retention_purge"
    assert start["level"] == "warning"
    assert start["user"] == ADMIN_OID
    assert "管理员" in start["data"]
    assert body["data"]["job_id"] in start["data"]

    terminal = _wait_terminal(http)
    assert terminal["job_id"] == body["data"]["job_id"]
    assert terminal["phase"] == "done"
    assert terminal["running"] is False
    result = terminal["result"]
    assert result["app_logs"]["deleted"] == 3
    assert result["app_logs"]["remaining"] == 0
    assert result["export_tasks"]["deleted"] == 0
    assert result["export_tasks"]["remaining"] == 0
    assert result["ai_call_logs"]["deleted"] == 0
    assert result["ai_call_logs"]["remaining"] == 0
    assert result["batches"] == 5
    for table, days in (
        ("app_logs", 90),
        ("export_tasks", 30),
        ("ai_call_logs", 90),
        ("ai_agent_events", 90),
        ("ai_prompt_audit", 30),
    ):
        assert set(result[table]) == {"cutoff", "deleted", "remaining"}
        cutoff = datetime.fromisoformat(result[table]["cutoff"])
        assert datetime.now() - timedelta(days=days + 1) < cutoff < datetime.now()

    # 结果审计：按发起管理员归属，内容仍是既有的一行摘要。
    assert [row["user"] for row in logs.inserted] == [ADMIN_OID, ADMIN_OID]
    audit = logs.inserted[1]
    assert audit["type"] == "retention_purge"
    assert audit["level"] == "warning"
    assert "app_logs 删除 3 行" in audit["data"]

    # 结束后恢复维护态（updated_by 是自动来源标记）。
    assert terminal["maintenance"]["enabled"] is False
    assert system_state.is_enabled() is False


def test_run_reports_the_remaining_rows_when_the_ceiling_is_hit(
    client, repos, monkeypatch
):
    http, _ = client
    logs, tasks, ai = repos
    monkeypatch.setattr(retention, "retention_max_batches", lambda: 1)
    logs.rowcounts = [5000]
    logs.deletable = 42  # what count_expired answers
    logs.delay = 0.2

    body = http.post(RUN_URL).json()
    assert body["data"]["state"] == "running"

    result = _wait_terminal(http)["result"]
    assert result["app_logs"]["deleted"] == 5000
    assert result["app_logs"]["remaining"] == 42
    assert result["batches"] == 5


def test_run_is_409_when_a_purge_is_already_running(client, repos):
    """第二个请求 409（沿用既有文案），detail 里带当前 phase，且什么都不做。"""
    http, _ = client
    logs, tasks, ai = repos
    logs.delay = 1.0  # 足够宽的窗口

    first = http.post(RUN_URL)
    assert first.status_code == 200
    _wait_until(http, lambda data: data["running"] and data["phase"] == "purging_app_logs")

    before = (len(logs.purge_calls), len(tasks.purge_calls), len(logs.inserted))
    response = http.post(RUN_URL)

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail.startswith(BUSY_DETAIL)
    assert "purging_app_logs" in detail  # 携带当前 phase
    # Refused before any work: no extra delete, no extra audit row.
    assert (len(logs.purge_calls), len(tasks.purge_calls), len(logs.inserted)) == before
    assert len(logs.inserted) == 1  # 只有第一个请求的发起行

    assert _wait_terminal(http)["phase"] == "done"


def test_run_failure_is_surfaced_in_progress_without_internal_details(client, repos):
    """后台任务化后失败不再走 503：请求 200，失败在进度里（且不泄露内部细节）。"""
    http, _ = client
    logs, _, _ = repos
    logs.fail = ConnectionError("private DSN and password")

    response = http.post(RUN_URL)

    assert response.status_code == 200
    assert "private" not in response.text
    assert "DSN" not in response.text

    terminal = _wait_terminal(http)
    assert terminal["phase"] == "failed"
    assert terminal["error"] == "ConnectionError"  # 只暴露异常类名
    assert terminal["result"] is None
    assert terminal["maintenance"]["enabled"] is False
    assert system_state.get_state()["enabled"] is False
    # 只有发起行；没跑成功就没有结果审计行。
    assert len(logs.inserted) == 1
    assert "启动" in logs.inserted[0]["data"]


@pytest.mark.parametrize("url", [PREVIEW_URL, RUN_URL])
def test_auth_dependency_failure_is_503(client, url):
    http, _ = client

    async def unavailable():
        raise ConnectionError("private DSN and password")

    http.app.dependency_overrides[get_current_user] = unavailable
    response = http.post(url)

    assert response.status_code == 503
    assert response.headers["cache-control"] == "no-store"
    assert "private" not in response.text


def test_preview_session_dependency_failure_is_503(client):
    """Only the preview takes the request session, so only it can fail here."""
    http, _ = client

    async def unavailable():
        raise ConnectionError("private DSN and password")

    http.app.dependency_overrides[get_session] = unavailable
    response = http.post(PREVIEW_URL)

    assert response.status_code == 503
    assert response.headers["cache-control"] == "no-store"
    assert "private" not in response.text
