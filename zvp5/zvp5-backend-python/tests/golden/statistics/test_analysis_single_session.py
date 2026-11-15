"""Single-session / concurrent-read tests for the activity analysis route.

``GET /api/v1/statistics/activities/{id}/analysis`` reads three things: the
effective memberships, the profiles of the oids those memberships reference and
the class listing. This module pins the shape of those reads:

* the memberships and the profiles share **one** session (one checkout and one
  teardown per request, not two);
* the class listing is unrelated to the other two and therefore runs on a
  **separate** session concurrently — never on the shared one, which an
  ``AsyncSession`` cannot execute against in parallel;
* an activity with no effective member still answers the empty payload without
  reading users or groups at all.

Offline: fake repos via ``monkeypatch``, the user via
``app.dependency_overrides``. Run from the repository root::

    ./.venv/bin/python -m pytest tests/golden/statistics/test_analysis_single_session.py -q
"""

from __future__ import annotations

import asyncio

from fastapi import FastAPI
from fastapi.testclient import TestClient

from db import backends
from routers import activity_statistics_router
from util.activity_statistics import BIN_WIDTH_SMALL
from util.object_id import get_current_user

ACTIVITY_ID = "64b7f0c2e1a3b4c5d6e7f801"
PREFIX = "/api/v1/statistics/activities"
SECRETARY = {"id": "64b7f0c2e1a3b4c5d6e7f8ff", "per": ["secretary"]}


class _FakeMembersRepo:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows

    async def list_by_activity(self, activity_oid: str, status: str | None = None):
        return [
            row for row in self.rows if status is None or row["status"] == status
        ]


class _FakeUsersRepo:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows

    async def get_many_by_oids(self, oids: list[str]):
        wanted = set(oids)
        return {row["_id"]: row for row in self.rows if row["_id"] in wanted}


class _FakeGroupsRepo:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows

    async def list_all(self):
        return list(self.rows)


class _ExplodingRepo:
    def __init__(self, name: str) -> None:
        self.name = name

    async def list_all(self):
        raise AssertionError(f"the route must not read {self.name} here")

    async def get_many_by_oids(self, oids: list[str]):
        raise AssertionError(f"the route must not read {self.name} here")


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(activity_statistics_router.router, prefix=PREFIX)
    app.dependency_overrides[get_current_user] = lambda: dict(SECRETARY)
    return TestClient(app)


def _wire(monkeypatch, *, members, users, classes):
    """Patch the three factories, recording every session each one receives."""
    sessions: dict[str, list] = {"members": [], "users": [], "classes": []}

    def members_factory(session=None):
        sessions["members"].append(session)
        return members

    def users_factory(session=None):
        sessions["users"].append(session)
        return users

    def classes_factory(session=None):
        sessions["classes"].append(session)
        return classes

    monkeypatch.setattr(backends, "get_members_repo", members_factory)
    monkeypatch.setattr(backends, "get_users_repo", users_factory)
    monkeypatch.setattr(backends, "get_groups_repo", classes_factory)
    return sessions


def test_members_and_profiles_share_a_session_and_classes_run_apart(
    monkeypatch,
) -> None:
    members = _FakeMembersRepo(
        [
            {"member": "u1", "status": "effective", "mode": "on-campus", "duration": 1.0},
            {"member": "u2", "status": "effective", "mode": "off-campus", "duration": 2.0},
        ]
    )
    users = _FakeUsersRepo(
        [{"_id": "u1", "class_id": "c1"}, {"_id": "u2", "class_id": "c1"}]
    )
    classes = _FakeGroupsRepo([{"_id": "c1", "name": "高一(1)班"}])
    sessions = _wire(monkeypatch, members=members, users=users, classes=classes)

    response = _client().get(f"{PREFIX}/{ACTIVITY_ID}/analysis")

    assert response.status_code == 200
    assert set(response.json()) == {
        "total",
        "bin_width",
        "description",
        "bins",
        "modes",
        "classes",
        "mode_members",
    }
    assert response.json()["total"] == 2
    assert response.json()["bin_width"] == BIN_WIDTH_SMALL
    assert response.json()["classes"] == [
        {"group_oid": "c1", "group_name": "高一(1)班", "count": 2, "sum": 3.0, "mean": 1.5}
    ]
    # Memberships + profiles: one session, used twice. Sessions are only real
    # when the factory is called without one, so the same object must come back.
    assert sessions["members"] == sessions["users"]
    assert sessions["members"][0] is not None
    # The class listing is a separate read: it must not borrow the shared
    # session (an AsyncSession cannot run two statements at once).
    assert len(sessions["classes"]) == 1
    assert sessions["classes"][0] is not sessions["members"][0]


def test_an_empty_activity_still_reads_nothing_else(monkeypatch) -> None:
    sessions = _wire(
        monkeypatch,
        members=_FakeMembersRepo(
            [{"member": "u1", "status": "pending", "mode": "on-campus", "duration": 1.0}]
        ),
        users=_ExplodingRepo("users"),
        classes=_ExplodingRepo("groups"),
    )

    response = _client().get(f"{PREFIX}/{ACTIVITY_ID}/analysis")

    assert response.status_code == 200
    assert response.json() == {
        "total": 0,
        "bin_width": BIN_WIDTH_SMALL,
        "description": None,
        "bins": [],
        "modes": [],
        "classes": [],
    }
    assert sessions["users"] == []
    assert sessions["classes"] == []


def test_the_two_independent_reads_actually_overlap(monkeypatch) -> None:
    """A slow class listing must not serialise behind the profile read.

    The two fake repos rendezvous through ``asyncio.Event``s: each waits for the
    other to start, so the request only completes if both reads are in flight at
    the same time. If the route ever went back to awaiting them in sequence the
    first one would block forever and ``wait_for`` fails the test.
    """
    started = {"users": asyncio.Event(), "classes": asyncio.Event()}

    class _RendezvousUsers(_FakeUsersRepo):
        async def get_many_by_oids(self, oids: list[str]):
            started["users"].set()
            await asyncio.wait_for(started["classes"].wait(), timeout=5)
            return await super().get_many_by_oids(oids)

    class _RendezvousClasses(_FakeGroupsRepo):
        async def list_all(self):
            started["classes"].set()
            await asyncio.wait_for(started["users"].wait(), timeout=5)
            return await super().list_all()

    _wire(
        monkeypatch,
        members=_FakeMembersRepo(
            [{"member": "u1", "status": "effective", "mode": "on-campus", "duration": 1.0}]
        ),
        users=_RendezvousUsers([{"_id": "u1", "class_id": "c1"}]),
        classes=_RendezvousClasses([{"_id": "c1", "name": "高一(1)班"}]),
    )

    response = _client().get(f"{PREFIX}/{ACTIVITY_ID}/analysis")

    assert response.status_code == 200
    assert response.json()["total"] == 1
