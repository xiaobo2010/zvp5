"""Route-level tests for ``routers.groups_router.get_groups_v2``, offline.

Pins ``GET /api/v1/groups``（合并后的唯一实现，原 groups_v2_router）：只有
admin / department 能列；普通学生与团支书一律 403。合并前的未版本化
``GET /api/groups``（含 ``type`` 过滤与「学生静默降级到班级侧」）没有前端调用方
（前端统一打 ``/v1/groups``，见 ``src/api/group/crud.ts``），本次收口时删除，
这里不再为其保留断言。

No database, no network: the groups repo is a fake spy injected with
``monkeypatch.setattr(db.backends, ...)`` and the current user comes from
``app.dependency_overrides[get_current_user]``. A throwaway ``FastAPI()`` is used
instead of ``main.app``; the prefix is copied from ``main.py``'s
``include_router(groups_router.router, prefix="/api/v1/groups")``.

Run from the repository root::

    ./.venv/bin/python -m pytest tests/golden/permissions/test_group_list_permission.py -q
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from db import backends
from routers import groups_router
from util.object_id import get_current_user

PREFIX = "/api/v1/groups"

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
        self.permission_calls: list[str] = []

    async def list_classes(self, query: str, page: int, size: int):
        self.class_calls.append({"query": query, "page": page, "size": size})
        rows = [row for row in CLASS_ROWS if not query or query in row["name"]]
        start = (page - 1) * size
        return rows[start : start + size], len(rows)

    async def list_permission_groups(self, query: str = "", permissions=None):
        self.permission_calls.append(query)
        return [
            dict(row) for row in PERMISSION_ROWS if not query or query in row["name"]
        ]


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


def test_student_is_forbidden(monkeypatch) -> None:
    _patch_repo(monkeypatch)
    response = _client(_login(["student"])).get(PREFIX)

    assert response.status_code == 403
    assert response.json() == _FORBIDDEN


def test_secretary_is_forbidden(monkeypatch) -> None:
    _patch_repo(monkeypatch)
    response = _client(_login(["secretary"])).get(PREFIX)

    assert response.status_code == 403
    assert response.json() == _FORBIDDEN


def test_admin_gets_classes_and_permission_groups(monkeypatch) -> None:
    repo = _patch_repo(monkeypatch)
    response = _client(_login(["admin"])).get(PREFIX)

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 3
    assert [row["name"] for row in body["data"]] == ["高一(1)班", "高一(2)班", "志工部"]
    # 班级行是仓储原样行（没有 ``type``），权限行才带 ``type`` 与
    # ``permissions``——口径沿用 v2 实现，本次不动。
    assert all("type" not in row for row in body["data"] if row["name"] != "志工部")
    permission_row = next(row for row in body["data"] if row["name"] == "志工部")
    assert permission_row["type"] == "permission"
    assert permission_row["permissions"] == ["activity:read"]
    # 班级一侧只问一次、且按硬上限取一页。
    assert repo.class_calls == [
        {"query": "", "page": 1, "size": groups_router._CLASS_LIST_CAP}
    ]


def test_department_gets_both_sides(monkeypatch) -> None:
    _patch_repo(monkeypatch)
    response = _client(_login(["department"])).get(PREFIX)

    assert response.status_code == 200
    assert response.json()["total"] == 3


def test_pagination_slices_the_merged_list(monkeypatch) -> None:
    _patch_repo(monkeypatch)
    response = _client(_login(["admin"])).get(
        f"{PREFIX}?page=2&size=2"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 3
    assert [row["name"] for row in body["data"]] == ["志工部"]


def test_query_filters_both_sides(monkeypatch) -> None:
    repo = _patch_repo(monkeypatch)
    response = _client(_login(["admin"])).get(f"{PREFIX}?query=高一")

    assert response.status_code == 200
    body = response.json()
    assert [row["name"] for row in body["data"]] == ["高一(1)班", "高一(2)班"]
    assert repo.class_calls[0]["query"] == "高一"
    assert repo.permission_calls == ["高一"]
