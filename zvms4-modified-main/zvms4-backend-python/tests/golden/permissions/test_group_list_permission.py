"""Route-level tests for ``routers.groups_router.get_groups`` (v1 list), offline.

Pins the permission gate on ``GET /api/groups``: an explicit ``type=permission``
request is refused for unprivileged callers (matching the v2 list endpoint),
``type=all`` degrades silently to the class side, and admin/department keep the
previous behaviour verbatim.

No database, no network: the groups repo is a fake spy injected with
``monkeypatch.setattr(db.backends, ...)`` and the current user comes from
``app.dependency_overrides[get_current_user]``. A throwaway ``FastAPI()`` is used
instead of ``main.app``; the prefix is copied from ``main.py``'s
``include_router(groups_router.router, prefix="/api/groups")``.

Run from the repository root::

    ./.venv/bin/python -m pytest tests/golden/permissions/test_group_list_permission.py -q
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from db import backends
from routers import groups_router
from util.object_id import get_current_user

PREFIX = "/api/groups"

CLASS_ROWS = [
    {"_id": "64b7f0c2e1a3b4c5d6e7f801", "name": "高一(1)班"},
    {"_id": "64b7f0c2e1a3b4c5d6e7f802", "name": "高一(2)班"},
]
PERMISSION_ROWS = [
    {
        "_id": "64b7f0c2e1a3b4c5d6e7f803",
        "name": "志工部",
        "type": "permission",
        "permissions": ["activity:read"],
    }
]

_FORBIDDEN = {"detail": "权限不足"}


class _FakeGroupsRepo:
    """Groups repo stand-in for the two list reads the route performs."""

    def __init__(self) -> None:
        self.class_calls: list[dict] = []
        self.permission_calls: list[dict] = []

    async def list_classes(self, query: str, page: int, size: int):
        self.class_calls.append({"query": query, "page": page, "size": size})
        rows = [row for row in CLASS_ROWS if not query or query in row["name"]]
        start = (page - 1) * size
        return rows[start : start + size], len(rows)

    async def list_permission_groups(self, query: str = "", permissions=None):
        self.permission_calls.append({"query": query, "permissions": permissions})
        return [dict(row) for row in PERMISSION_ROWS]


def _client(user: dict) -> TestClient:
    app = FastAPI()
    app.include_router(groups_router.router, prefix=PREFIX)
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


def _login(per: list[str]) -> dict:
    return {"_id": "64b7f0c2e1a3b4c5d6e7f8ff", "id": "64b7f0c2e1a3b4c5d6e7f8ff", "per": per}


def _patch_repo(monkeypatch) -> _FakeGroupsRepo:
    repo = _FakeGroupsRepo()
    monkeypatch.setattr(backends, "get_groups_repo", lambda *a, **k: repo)
    return repo


def test_student_all_degrades_silently_to_the_class_side(monkeypatch) -> None:
    repo = _patch_repo(monkeypatch)
    response = _client(_login(["student"])).get(f"{PREFIX}?type=all")

    assert response.status_code == 200
    data = response.json()["data"]
    assert [row["name"] for row in data] == ["高一(1)班", "高一(2)班"]
    assert all(row["type"] == "class" for row in data)
    # The permission manifest is gone even though the repo was still consulted
    # for the id-exclusion set (so class rows never shadow a permission group).
    assert repo.permission_calls == [{"query": "", "permissions": None}]


def test_student_permission_is_forbidden(monkeypatch) -> None:
    _patch_repo(monkeypatch)
    response = _client(_login(["student"])).get(f"{PREFIX}?type=permission")
    assert response.status_code == 403
    assert response.json() == _FORBIDDEN


def test_student_class_still_lists_classes(monkeypatch) -> None:
    _patch_repo(monkeypatch)
    response = _client(_login(["student"])).get(f"{PREFIX}?type=class")
    assert response.status_code == 200
    data = response.json()["data"]
    assert [row["name"] for row in data] == ["高一(1)班", "高一(2)班"]
    assert all(row["type"] == "class" for row in data)


def test_secretary_does_not_get_the_permission_manifest(monkeypatch) -> None:
    _patch_repo(monkeypatch)
    response = _client(_login(["secretary"])).get(f"{PREFIX}?type=permission")
    assert response.status_code == 403
    assert response.json() == _FORBIDDEN


def test_admin_all_keeps_class_and_permission(monkeypatch) -> None:
    _patch_repo(monkeypatch)
    response = _client(_login(["admin"])).get(f"{PREFIX}?type=all")

    assert response.status_code == 200
    data = response.json()["data"]
    assert [row["name"] for row in data] == ["志工部", "高一(1)班", "高一(2)班"]
    kinds = {row["type"] for row in data}
    assert kinds == {"class", "permission"}
    permission_row = next(row for row in data if row["type"] == "permission")
    assert permission_row["permissions"] == ["activity:read"]


def test_department_permission_still_works(monkeypatch) -> None:
    _patch_repo(monkeypatch)
    response = _client(_login(["department"])).get(f"{PREFIX}?type=permission")

    assert response.status_code == 200
    data = response.json()["data"]
    assert [row["type"] for row in data] == ["permission"]
    assert data[0]["permissions"] == ["activity:read"]
