"""Route-level tests for ``routers.groups_v2_router.stat_group_compliance``.

Fully offline: the repositories are fake spies injected with
``monkeypatch.setattr(db.backends, ...)`` (the router reaches the factories
through the module object, so patching the attribute is enough) and the current
user comes from ``app.dependency_overrides[get_current_user]``.

Why a throwaway ``FastAPI()`` instead of ``main.app``: importing ``main`` pulls
in APScheduler, socket.io and the ``tasks.compute_time`` graph. The prefix below
is copied from ``main.py``'s
``include_router(groups_v2_router.router, prefix="/api/v1/groups")`` so the
tested URL is the production one.

Two properties are pinned beyond the response contract:

* the class roster and the bulk time lookup run on **the same session object**
  (one checkout / one teardown per request), and
* a secretary whose auth dict carries ``class_id`` (the shape
  ``util.object_id._authenticate`` now returns) is not re-read from the users
  table.

Run from the repository root (``util.cert`` reads ``aes_key.txt`` at import
time)::

    ./.venv/bin/python -m pytest tests/golden/statistics/test_group_compliance_route.py -q
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from db import backends
from routers import groups_v2_router
from util.object_id import get_current_user

PREFIX = "/api/v1/groups"

CLASS_OID = "64b7f0c2e1a3b4c5d6e7f801"
OTHER_CLASS_OID = "64b7f0c2e1a3b4c5d6e7f802"
SECRETARY_OID = "64b7f0c2e1a3b4c5d6e7f805"
MEMBER_OIDS = [
    "64b7f0c2e1a3b4c5d6e7f811",
    "64b7f0c2e1a3b4c5d6e7f812",
]


class _FakeUsersRepo:
    """Users repo stand-in: records the session and the lookups."""

    def __init__(self, members: list[str]) -> None:
        self.members = members
        self.sessions: list[object] = []
        self.by_oid_calls: list[str] = []

    async def list_by_class(self, class_oid: str):
        return [{"_id": oid} for oid in self.members]

    async def get_by_oid(self, oid: str):
        self.by_oid_calls.append(oid)
        return {"_id": oid, "class_id": CLASS_OID}


class _FakeTimeRepo:
    """Time repo stand-in: every member is a cache hit at the full base."""

    def __init__(self) -> None:
        self.sessions: list[object] = []
        self.ids_calls: list[list[str]] = []

    async def list_by_users(self, user_oids, *args, **kwargs):
        self.ids_calls.append(list(user_oids))
        return [
            {
                "user": oid,
                "on_campus_raw": 25.0,
                "off_campus_raw": 15.0,
                "social_practice": 18.0,
            }
            for oid in user_oids
        ]


class _ForbiddenRepo:
    """Fails the test if the endpoint reads a table it should not need."""

    async def list_effective(self, *args, **kwargs):
        raise AssertionError("no cache miss was planted: activities must not be read")

    async def list_by_members(self, *args, **kwargs):
        raise AssertionError("no cache miss was planted: members must not be read")


def _client(user: dict) -> TestClient:
    app = FastAPI()
    app.include_router(groups_v2_router.router, prefix=PREFIX)
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


def _patch(monkeypatch, *, members: list[str] = MEMBER_OIDS):
    """Wire the three repo factories, recording the session each one is handed."""
    users = _FakeUsersRepo(members)
    times = _FakeTimeRepo()
    seen: dict[str, list] = {"users": [], "time": []}

    def users_factory(session=None):
        seen["users"].append(session)
        return users

    def time_factory(session=None):
        seen["time"].append(session)
        return times

    monkeypatch.setattr(backends, "get_users_repo", users_factory)
    monkeypatch.setattr(backends, "get_time_repo", time_factory)
    monkeypatch.setattr(backends, "get_groups_repo", lambda *a, **k: _ForbiddenRepo())
    return users, times, seen


def test_student_is_still_rejected(monkeypatch) -> None:
    _patch(monkeypatch)
    response = _client({"id": SECRETARY_OID, "per": ["student"]}).get(
        f"{PREFIX}/{CLASS_OID}/statistics/compliance"
    )
    assert response.status_code == 403
    assert response.json() == {"detail": "权限不足"}


def test_secretary_reads_their_class_on_a_single_shared_session(monkeypatch) -> None:
    users, times, seen = _patch(monkeypatch)
    response = _client(
        {"id": SECRETARY_OID, "per": ["secretary"], "class_id": CLASS_OID}
    ).get(f"{PREFIX}/{CLASS_OID}/statistics/compliance")

    assert response.status_code == 200
    # 25/25, 15/15 and 18/18 are all 100%: every member sits in the top bucket.
    # ``member_rosters`` is the additive per-bucket roster: emitted on every
    # response, keyed by the very same bucket labels, one entry per member.
    assert response.json() == {
        "on-campus": {"Completed": 2},
        "off-campus": {"Completed": 2},
        "social-practice": {"Completed": 2},
        "member_rosters": {
            mode: {
                "Completed": [
                    {"_id": oid, "id": "", "name": "", "duration": duration}
                    for oid in MEMBER_OIDS
                ]
            }
            for mode, duration in (
                ("on-campus", 25.0),
                ("off-campus", 15.0),
                ("social-practice", 18.0),
            )
        },
    }
    assert times.ids_calls == [MEMBER_OIDS]
    # Auth already carried the caller's class, so the permission check must not
    # re-read the very document authentication loaded.
    assert users.by_oid_calls == []
    # Roster + time share one session: one checkout, one teardown.
    assert seen["users"] == seen["time"]
    assert seen["users"][0] is not None


def test_secretary_without_auth_class_id_falls_back_to_a_fresh_read(monkeypatch) -> None:
    users, times, seen = _patch(monkeypatch)
    # No ``class_id`` key at all: the legacy shape, which must keep working.
    response = _client({"id": SECRETARY_OID, "per": ["secretary"]}).get(
        f"{PREFIX}/{CLASS_OID}/statistics/compliance"
    )

    assert response.status_code == 200
    assert users.by_oid_calls == [SECRETARY_OID]
    # The fallback re-read is sessionless (the repo opens its own); the roster
    # read then shares one session with the bulk time lookup.
    assert len(seen["time"]) == 1
    assert seen["users"] == [None, seen["time"][0]]
    assert seen["time"][0] is not None


def test_secretary_of_another_class_still_gets_403(monkeypatch) -> None:
    users, _, seen = _patch(monkeypatch)
    response = _client(
        {"id": SECRETARY_OID, "per": ["secretary"], "class_id": OTHER_CLASS_OID}
    ).get(f"{PREFIX}/{CLASS_OID}/statistics/compliance")

    assert response.status_code == 403
    assert response.json() == {"detail": "权限不足"}
    assert users.by_oid_calls == []
    # A denied request never opens a session for the roster.
    assert seen["users"] == []


def test_admin_still_passes_without_any_permission_read(monkeypatch) -> None:
    users, _, seen = _patch(monkeypatch)
    response = _client({"id": SECRETARY_OID, "per": ["admin"]}).get(
        f"{PREFIX}/{CLASS_OID}/statistics/compliance"
    )
    assert response.status_code == 200
    assert users.by_oid_calls == []
    assert seen["users"][0] is not None


def test_malformed_class_id_is_still_a_400(monkeypatch) -> None:
    _patch(monkeypatch)
    response = _client({"id": SECRETARY_OID, "per": ["admin"]}).get(
        f"{PREFIX}/not-an-oid/statistics/compliance"
    )
    assert response.status_code == 400
    assert response.json() == {"detail": "Invalid Object ID"}
