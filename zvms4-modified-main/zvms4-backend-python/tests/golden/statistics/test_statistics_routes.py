"""Route-level tests for ``routers.activity_statistics_router``, fully offline.

No database, no network, no fixtures directory: the repositories are fake spies
injected with ``monkeypatch.setattr(db.backends, ...)`` (the router calls the
factories through the module object, so patching the module attribute is enough)
and the current user comes from ``app.dependency_overrides[get_current_user]``.

Why a throwaway ``FastAPI()`` instead of ``main.app``: importing ``main`` pulls in
the APScheduler instance, socket.io and the ``tasks.compute_time`` graph, none of
which this test needs — and it would drag the whole application's import chain
(and its startup hooks) into what is meant to be a router contract test. The
prefix below is copied from ``main.py``'s
``include_router(activity_statistics_router.router, prefix="/api/v2/statistics/activities")``
so the tested URLs are the production ones.

Offline compromise: importing ``routers.activity_statistics_router`` reaches
``util.object_id``, which reads ``aes_key.txt`` from the current directory at
import time. Run pytest from the repository root (as the acceptance command
does); a run from elsewhere fails at collection, not in an assertion.

Run with pytest::

    pytest tests/golden/statistics/test_statistics_routes.py -q
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from db import backends
from routers import activity_statistics_router
from util.activity_statistics import BIN_WIDTH_SMALL
from util.object_id import get_current_user

ACTIVITY_ID = "64b7f0c2e1a3b4c5d6e7f801"
PREFIX = "/api/v2/statistics/activities"

_USERS = [{"_id": "u1", "class_id": "c1"}, {"_id": "u2", "class_id": "c1"}]
_CLASSES = [{"_id": "c1", "name": "高一(1)班"}]

_ANALYSIS_KEYS = {"total", "bin_width", "description", "bins", "modes", "classes"}
_EMPTY_ANALYSIS = {
    "total": 0,
    "bin_width": BIN_WIDTH_SMALL,
    "description": None,
    "bins": [],
    "modes": [],
    "classes": [],
}


class _SpyMembersRepo:
    """Members repo stand-in: records every call and honours ``status``."""

    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.calls: list[dict] = []

    async def list_by_activity(self, activity_oid: str, status: str | None = None):
        self.calls.append({"activity_oid": activity_oid, "status": status})
        return [
            row for row in self.rows if status is None or row["status"] == status
        ]


class _ListRepo:
    """Users / groups stand-in returning a fixed list.

    ``get_many_by_oids`` mirrors the batch users read: only the referenced oids
    come back (the route no longer pulls the whole table).
    """

    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows

    async def list_all(self):
        return list(self.rows)

    async def get_many_by_oids(self, oids: list[str]):
        wanted = list(dict.fromkeys(oids))
        return {
            row["_id"]: row for row in self.rows if row["_id"] in set(wanted)
        }


class _ForbiddenRepo:
    """Fails the test if the route reads a table it is supposed to skip."""

    def __init__(self, name: str) -> None:
        self.name = name

    async def list_all(self):
        raise AssertionError(f"the route must not read {self.name} here")

    async def get_many_by_oids(self, oids: list[str]):
        raise AssertionError(f"the route must not read {self.name} here")


def _client(user: dict) -> TestClient:
    app = FastAPI()
    app.include_router(activity_statistics_router.router, prefix=PREFIX)
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


def _login(per: list[str]) -> dict:
    return {"_id": "64b7f0c2e1a3b4c5d6e7f8ff", "per": per}


def _patch_repos(
    monkeypatch,
    members: _SpyMembersRepo,
    users=None,
    classes=None,
) -> _SpyMembersRepo:
    monkeypatch.setattr(backends, "get_members_repo", lambda *a, **k: members)
    monkeypatch.setattr(
        backends, "get_users_repo", lambda *a, **k: users or _ListRepo(_USERS)
    )
    monkeypatch.setattr(
        backends, "get_groups_repo", lambda *a, **k: classes or _ListRepo(_CLASSES)
    )
    return members


@pytest.fixture()
def spy(monkeypatch) -> _SpyMembersRepo:
    rows = [
        {"member": "u1", "status": "effective", "mode": "on-campus", "duration": 1.0},
        {"member": "u2", "status": "effective", "mode": "off-campus", "duration": 2.0},
        {"member": "u3", "status": "pending", "mode": "on-campus", "duration": 99.0},
        {"member": "u4", "status": "refused", "mode": "on-campus", "duration": 50.0},
    ]
    return _patch_repos(monkeypatch, _SpyMembersRepo(rows))


def test_analysis_rejects_a_student() -> None:
    response = _client(_login(["student"])).get(f"{PREFIX}/{ACTIVITY_ID}/analysis")
    assert response.status_code == 403
    assert response.json() == {"detail": "权限不足"}


def test_analysis_allows_a_secretary_and_only_reads_effective_members(spy) -> None:
    response = _client(_login(["secretary"])).get(f"{PREFIX}/{ACTIVITY_ID}/analysis")
    assert response.status_code == 200
    assert spy.calls == [{"activity_oid": ACTIVITY_ID, "status": "effective"}]
    body = response.json()
    assert set(body) == _ANALYSIS_KEYS
    # The two non-effective rows (99 h / 50 h) are gone: the fake repo filters on
    # the status it was handed, so this only holds if the router asked for it.
    assert body["total"] == 2
    assert body["bin_width"] == BIN_WIDTH_SMALL
    assert body["description"]["total"] == 2
    assert body["description"]["mean"] == pytest.approx(1.5)
    assert body["description"]["std"] == pytest.approx(0.5)
    assert body["description"]["sum"] == pytest.approx(3.0)
    assert body["description"]["25_percentile"] == pytest.approx(1.25)
    assert body["description"]["75_percentile"] == pytest.approx(1.75)
    assert body["bins"] == [
        {"from": 0.0, "to": 0.5, "count": 0},
        {"from": 0.5, "to": 1.0, "count": 0},
        {"from": 1.0, "to": 1.5, "count": 1},
        {"from": 1.5, "to": 2.0, "count": 0},
        {"from": 2.0, "to": 2.5, "count": 1},
    ]
    assert body["modes"] == [
        {"mode": "on-campus", "count": 1, "sum": 1.0, "mean": 1.0},
        {"mode": "off-campus", "count": 1, "sum": 2.0, "mean": 2.0},
    ]
    assert body["classes"] == [
        {"group_oid": "c1", "group_name": "高一(1)班", "count": 2, "sum": 3.0, "mean": 1.5}
    ]


def test_analysis_without_effective_members_returns_the_empty_payload(
    monkeypatch,
) -> None:
    rows = [
        {
            "member": "u1",
            "status": "pending",
            "mode": "on-campus",
            "duration": 1.0,
        }
    ]
    spy = _patch_repos(
        monkeypatch,
        _SpyMembersRepo(rows),
        users=_ForbiddenRepo("users"),
        classes=_ForbiddenRepo("groups"),
    )
    response = _client(_login(["secretary"])).get(f"{PREFIX}/{ACTIVITY_ID}/analysis")
    assert response.status_code == 200
    assert response.json() == _EMPTY_ANALYSIS
    assert spy.calls == [{"activity_oid": ACTIVITY_ID, "status": "effective"}]


def test_description_keeps_its_in_band_404_for_an_empty_activity(
    monkeypatch,
) -> None:
    _patch_repos(monkeypatch, _SpyMembersRepo([]))
    response = _client(_login(["secretary"])).get(
        f"{PREFIX}/{ACTIVITY_ID}/description"
    )
    # In-band not-found: HTTP 200, the 404 lives in the body.
    assert response.status_code == 200
    assert response.json() == {"code": 404, "status": "not found", "data": None}


def test_description_and_layers_also_only_read_effective_members(spy) -> None:
    client = _client(_login(["secretary"]))
    description = client.get(f"{PREFIX}/{ACTIVITY_ID}/description")
    layers = client.get(f"{PREFIX}/{ACTIVITY_ID}/layers")
    assert description.status_code == 200
    assert description.json()["total"] == 2
    assert layers.status_code == 200
    assert layers.json() == [
        {"value": 2.0, "count": 1},
        {"value": 1.0, "count": 1},
    ]
    assert spy.calls == [
        {"activity_oid": ACTIVITY_ID, "status": "effective"},
        {"activity_oid": ACTIVITY_ID, "status": "effective"},
    ]


def test_description_rejects_a_student() -> None:
    response = _client(_login(["student"])).get(
        f"{PREFIX}/{ACTIVITY_ID}/description"
    )
    assert response.status_code == 403
