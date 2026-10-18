"""Offline tests for the retention endpoints (design §2 / C7).

Pins the admin-facing contract of ``routers/db_system_router.py``:

* ``POST /api/system/db/retention/preview`` → per-table ``{cutoff, deletable}``
  and **nothing is deleted** (only counts reach the repositories);
* ``POST /api/system/db/retention/run`` → per-table
  ``{cutoff, deleted, remaining}`` plus ``batches``, and exactly one
  ``retention_purge`` / ``warning`` audit row naming the acting admin;
* authorization is re-checked against the database before any work happens
  (403 for a non-admin or a banned admin, without touching a repository);
* a concurrent purge is 409 with the frozen detail, and a failing database is
  503 without leaking internals.

Offline: the users / app-logs / tasks repositories are fakes and the session
dependency yields ``None``; no engine is created.

Run from the repository root::

    ./.venv/bin/python -m pytest tests/test_db_retention_router.py -q
"""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from db import backends
from db.oracle.session import get_session
from routers import db_system_router
from tasks import retention
from util.object_id import authenticated_log_user, get_current_user

ADMIN_OID = "64b7f0c2e1a3b4c5d6e7f801"
BUSY_DETAIL = "清理任务正在执行中，请稍后重试"

PREVIEW_URL = "/api/system/db/retention/preview"
RUN_URL = "/api/system/db/retention/run"


class _FakeLogsRepo:
    """Fake ``AppLogsRepo``: counts, purges and records audit inserts."""

    def __init__(self, deletable: int = 0, deleted: list[int] | None = None) -> None:
        self.deletable = deletable
        self.rowcounts = list(deleted or [])
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


@pytest.fixture
def repos(monkeypatch):
    """Wire fake repositories through ``backends`` (both sides)."""
    logs = _FakeLogsRepo()
    tasks = _FakeTasksRepo()
    monkeypatch.setattr(backends, "get_applogs_repo", lambda session=None: logs)
    monkeypatch.setattr(backends, "get_tasks_repo", lambda session=None: tasks)
    return logs, tasks


@pytest.fixture(autouse=True)
def _release_lock():
    yield
    try:
        retention.release()
    except RuntimeError:  # not held
        pass


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
    return TestClient(app), state


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
    logs, tasks = repos

    assert http.post(url).status_code == 403
    assert logs.count_calls == [] and logs.purge_calls == []
    assert tasks.count_calls == [] and tasks.purge_calls == []
    assert logs.inserted == []


# ---------------------------------------------------------------------------
# preview
# ---------------------------------------------------------------------------


def test_preview_returns_both_cutoffs_and_counts(client, repos):
    http, _ = client
    logs, tasks = repos
    logs.deletable = 7
    tasks.deletable = 2

    before = datetime.now()
    response = http.post(PREVIEW_URL)
    after = datetime.now()

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok" and body["code"] == 200
    assert set(body["data"]) == {"app_logs", "export_tasks"}
    for table in ("app_logs", "export_tasks"):
        assert set(body["data"][table]) == {"cutoff", "deletable"}
    assert body["data"]["app_logs"]["deletable"] == 7
    assert body["data"]["export_tasks"]["deletable"] == 2

    logs_cutoff = datetime.fromisoformat(body["data"]["app_logs"]["cutoff"])
    tasks_cutoff = datetime.fromisoformat(body["data"]["export_tasks"]["cutoff"])
    assert before - timedelta(days=90) <= logs_cutoff <= after - timedelta(days=90)
    assert before - timedelta(days=30) <= tasks_cutoff <= after - timedelta(days=30)

    # The counts were asked for with the very cutoffs the response reports.
    assert logs.count_calls == [logs_cutoff]
    assert tasks.count_calls == [tasks_cutoff]


def test_preview_deletes_nothing(client, repos):
    http, _ = client
    logs, tasks = repos

    assert http.post(PREVIEW_URL).status_code == 200

    assert logs.purge_calls == []
    assert tasks.purge_calls == []
    assert logs.inserted == []


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------


def test_run_returns_totals_and_audits_once(client, repos):
    http, _ = client
    logs, tasks = repos
    logs.rowcounts = [3]  # one short batch
    tasks.rowcounts = [0]  # nothing expired

    response = http.post(RUN_URL)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok" and body["code"] == 200
    assert body["data"]["app_logs"]["deleted"] == 3
    assert body["data"]["app_logs"]["remaining"] == 0
    assert body["data"]["export_tasks"]["deleted"] == 0
    assert body["data"]["export_tasks"]["remaining"] == 0
    assert body["data"]["batches"] == 2
    for table, days in (("app_logs", 90), ("export_tasks", 30)):
        assert set(body["data"][table]) == {"cutoff", "deleted", "remaining"}
        cutoff = datetime.fromisoformat(body["data"][table]["cutoff"])
        assert datetime.now() - timedelta(days=days + 1) < cutoff < datetime.now()

    assert len(logs.inserted) == 1
    audit = logs.inserted[0]
    assert audit["type"] == "retention_purge"
    assert audit["level"] == "warning"
    assert audit["user"] == ADMIN_OID
    assert "管理员" in audit["data"]
    assert "app_logs 删除 3 行" in audit["data"]


def test_run_reports_the_remaining_rows_when_the_ceiling_is_hit(client, repos, monkeypatch):
    http, _ = client
    logs, tasks = repos
    monkeypatch.setattr(retention, "retention_max_batches", lambda: 1)
    logs.rowcounts = [5000]
    logs.deletable = 42  # what count_expired answers afterwards

    body = http.post(RUN_URL).json()

    assert body["data"]["app_logs"]["deleted"] == 5000
    assert body["data"]["app_logs"]["remaining"] == 42
    assert body["data"]["batches"] == 2


def test_run_is_409_when_a_purge_is_already_running(client, repos, monkeypatch):
    http, _ = client
    logs, tasks = repos

    async def _busy() -> bool:
        return False

    monkeypatch.setattr(retention, "acquire_nowait", _busy)

    response = http.post(RUN_URL)

    assert response.status_code == 409
    assert response.json()["detail"] == BUSY_DETAIL
    # Refused before any work: no delete, no count, no audit row.
    assert logs.purge_calls == [] and tasks.purge_calls == []
    assert logs.inserted == []


def test_run_failure_is_503_without_internal_details(client, repos):
    http, _ = client
    logs, _ = repos
    logs.fail = ConnectionError("private DSN and password")

    response = http.post(RUN_URL)

    assert response.status_code == 503
    assert "private" not in response.text
    assert "DSN" not in response.text
    assert logs.inserted == []


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
