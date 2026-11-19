"""Route-level tests for ``routers.groups_router.get_group_v2``, fully offline.

Pins ``GET /api/v1/groups/{group_id}``（合并后的唯一实现，原 groups_v2_router）：

* admin / department 读任意班级；
* 普通学生只能读**自己班**（``users.class_id == group_id``）；
* 其他班级一律 403；不存在的班级 404。

No database, no network: the repositories are fake spies injected with
``monkeypatch.setattr(db.backends, ...)`` (the router reaches the factories
through the module object, so patching the attribute is enough) and the current
user comes from ``app.dependency_overrides[get_current_user]``.

Why a throwaway ``FastAPI()`` instead of ``main.app``: importing ``main`` pulls
in APScheduler, socket.io and the ``tasks.compute_time`` graph. The prefix below
is copied from ``main.py``'s
``include_router(groups_router.router, prefix="/api/v1/groups")`` so the tested
URLs are the production ones.

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

PREFIX = "/api/v1/groups"

CLASS_OID = "64b7f0c2e1a3b4c5d6e7f801"
OTHER_CLASS_OID = "64b7f0c2e1a3b4c5d6e7f802"
STUDENT_OID = "64b7f0c2e1a3b4c5d6e7f804"


class _FakeGroupsRepo:
    """Groups repo stand-in for the single read the route performs."""

    def __init__(self) -> None:
        self.get_calls: list[str] = []

    async def get(self, oid: str):
        self.get_calls.append(oid)
        if oid == CLASS_OID:
            return {"_id": oid, "name": "高一(1)班", "description": ""}
        return None


class _FakeUsersRepo:
    def __init__(self, class_id: str | None) -> None:
        self.class_id = class_id
        self.get_calls: list[str] = []

    async def get_by_oid(self, oid: str):
        self.get_calls.append(oid)
        return {"_id": oid, "id": "20250001", "class_id": self.class_id}


def _login(per: list[str]) -> dict:
    return {"_id": STUDENT_OID, "id": STUDENT_OID, "per": per}


def _client(user: dict) -> TestClient:
    app = FastAPI()
    app.include_router(groups_router.router, prefix=PREFIX)
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


def _patch_repo(monkeypatch, class_id: str | None = None) -> _FakeGroupsRepo:
    repo = _FakeGroupsRepo()
    monkeypatch.setattr(backends, "get_groups_repo", lambda *a, **k: repo)
    monkeypatch.setattr(
        backends, "get_users_repo", lambda *a, **k: _FakeUsersRepo(class_id)
    )
    return repo


def test_admin_can_read_any_class(monkeypatch) -> None:
    repo = _patch_repo(monkeypatch)
    response = _client(_login(["admin"])).get(f"{PREFIX}/{CLASS_OID}")

    assert response.status_code == 200
    body = response.json()
    assert body["code"] == 200
    assert body["data"] == {
        "_id": CLASS_OID,
        "name": "高一(1)班",
        "type": "class",
    }
    assert repo.get_calls == [CLASS_OID]


def test_department_can_read_any_class(monkeypatch) -> None:
    _patch_repo(monkeypatch)
    response = _client(_login(["department"])).get(f"{PREFIX}/{CLASS_OID}")

    assert response.status_code == 200
    assert response.json()["data"]["type"] == "class"


def test_student_can_read_their_own_class(monkeypatch) -> None:
    repo = _patch_repo(monkeypatch, class_id=CLASS_OID)
    response = _client(_login(["student"])).get(f"{PREFIX}/{CLASS_OID}")

    # 放行条件只有「自己的 class_id 等于路径参数」——普通学生没有 admin /
    # department，这个 200 直接证明该分支生效。
    assert response.status_code == 200
    assert response.json()["data"]["name"] == "高一(1)班"
    assert repo.get_calls == [CLASS_OID]


def test_student_cannot_read_another_class(monkeypatch) -> None:
    _patch_repo(monkeypatch, class_id=OTHER_CLASS_OID)
    response = _client(_login(["student"])).get(f"{PREFIX}/{CLASS_OID}")

    assert response.status_code == 403
    assert response.json() == {"detail": "权限不足"}


def test_unknown_group_is_404_for_admin(monkeypatch) -> None:
    _patch_repo(monkeypatch)
    response = _client(_login(["admin"])).get(f"{PREFIX}/{OTHER_CLASS_OID}")

    assert response.status_code == 404
    assert response.json() == {"detail": "班级不存在"}
