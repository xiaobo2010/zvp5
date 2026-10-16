"""Route-level tests for ``routers.groups_router.get_group``, fully offline.

No database, no network: the repositories are fake spies injected with
``monkeypatch.setattr(db.backends, ...)`` (the router reaches the factories
through the module object, so patching the attribute is enough) and the current
user comes from ``app.dependency_overrides[get_current_user]``.

Why a throwaway ``FastAPI()`` instead of ``main.app``: importing ``main`` pulls
in APScheduler, socket.io and the ``tasks.compute_time`` graph. The prefix below
is copied from ``main.py``'s
``include_router(groups_router.router, prefix="/api/groups")`` so the tested URLs
are the production ones.

Run from the repository root (``util.object_id`` reads ``aes_key.txt`` at import
time)::

    ./.venv/bin/python -m pytest tests/golden/permissions/test_group_routes.py -q
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from db import backends
from routers import groups_router
from util.object_id import get_current_user

PREFIX = "/api/groups"

CLASS_OID = "64b7f0c2e1a3b4c5d6e7f801"
PERMISSION_OID = "64b7f0c2e1a3b4c5d6e7f803"
STUDENT_OID = "64b7f0c2e1a3b4c5d6e7f804"


class _FakeGroupsRepo:
    """Groups repo stand-in for both reads the route performs."""

    def __init__(self) -> None:
        self.permission_calls: list[str] = []
        self.get_calls: list[str] = []

    async def get_permission_group(self, oid: str):
        self.permission_calls.append(oid)
        if oid == PERMISSION_OID:
            return {
                "_id": oid,
                "name": "某权限组",
                "type": "permission",
                "permissions": ["activity:read"],
            }
        return None

    async def get(self, oid: str):
        self.get_calls.append(oid)
        if oid == CLASS_OID:
            return {"_id": oid, "name": "高一(1)班", "description": ""}
        return None


def _student() -> dict:
    return {"_id": STUDENT_OID, "id": STUDENT_OID, "per": ["student"]}


def _client(user: dict) -> TestClient:
    app = FastAPI()
    app.include_router(groups_router.router, prefix=PREFIX)
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


def _patch_repo(monkeypatch) -> _FakeGroupsRepo:
    repo = _FakeGroupsRepo()
    monkeypatch.setattr(backends, "get_groups_repo", lambda *a, **k: repo)
    return repo


def test_student_can_read_a_class_group(monkeypatch) -> None:
    repo = _patch_repo(monkeypatch)
    response = _client(_student()).get(f"{PREFIX}/{CLASS_OID}")

    # A plain student has no admin/department/secretary permission, so the only
    # branch inside validate_read_group_permission that can yield a pass is the
    # allow_class opt-in. A 200 here therefore proves the route passes
    # allow_class=True; without it this very request is the 403 pinned below.
    assert response.status_code == 200
    body = response.json()
    assert body["code"] == 200
    assert body["data"]["type"] == "class"
    assert body["data"]["name"] == "高一(1)班"
    assert repo.permission_calls == [CLASS_OID, CLASS_OID]  # permission check + route probe
    assert repo.get_calls == [CLASS_OID]


def test_student_cannot_read_a_permission_group(monkeypatch) -> None:
    _patch_repo(monkeypatch)
    response = _client(_student()).get(f"{PREFIX}/{PERMISSION_OID}")
    assert response.status_code == 403
    assert response.json() == {"detail": "权限不足"}
