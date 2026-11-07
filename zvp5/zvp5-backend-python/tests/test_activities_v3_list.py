"""``GET /api/v1/activities``（原 v3 列表）的排序白名单与响应投影。

离线：假 repo 只记录 ``list_page`` 的入参，断言

1. 不传 ``sort`` ⇒ 交给 repo 的 ``sort=None``（沿用 repo 的 oid DESC）；
2. 白名单值映射到实际列（``_id`` → ``oid``、``date`` → ``activity_date``）；
3. 白名单外的值 400，且不触达 repo；
4. 响应透传 ``place`` / ``origin`` / ``appointee`` / ``approver`` 四个字段。
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from db import backends
from routers import activities_v3_router
from util.object_id import get_current_user

READER = {"id": "000000000000000000000002", "per": ["admin"]}


class _ActivitiesRepo:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def list_page(
        self, query, atypes, status, page, size, sort=None, asc=False
    ):
        self.calls.append(
            {
                "query": query,
                "atypes": atypes,
                "status": status,
                "page": page,
                "size": size,
                "sort": sort,
                "asc": asc,
            }
        )
        return [
            {
                "_id": "000000000000000000000010",
                "type": "on-campus",
                "name": "活动甲",
                "description": "",
                "status": "effective",
                "date": None,
                "createdAt": None,
                "updatedAt": None,
                "creator": "000000000000000000000002",
                "creator_name": "乙",
                "place": "图书馆",
                "origin": "activities",
                "appointee": "000000000000000000000002",
                "approver": "丙",
            }
        ], 1


@pytest.fixture
def client(monkeypatch):
    repo = _ActivitiesRepo()
    monkeypatch.setattr(backends, "get_activities_repo", lambda session=None: repo)
    app = FastAPI()
    app.include_router(activities_v3_router.router, prefix="/api/v1/activities")
    app.dependency_overrides[get_current_user] = lambda: READER
    return TestClient(app), repo


def test_default_sort_keeps_repo_order(client) -> None:
    http, repo = client
    response = http.get("/api/v1/activities")
    assert response.status_code == 200
    assert repo.calls[-1]["sort"] is None
    assert repo.calls[-1]["asc"] is False


def test_projection_passes_through_place_origin_appointee_approver(client) -> None:
    http, _ = client
    activity = http.get("/api/v1/activities").json()["activities"][0]
    assert activity["place"] == "图书馆"
    assert activity["origin"] == "activities"
    assert activity["appointee"] == "000000000000000000000002"
    assert activity["approver"] == "丙"


@pytest.mark.parametrize(
    ("sort", "column"),
    [("date", "activity_date"), ("_id", "oid"), ("type", "atype")],
)
def test_sort_whitelist_maps_to_columns(client, sort, column) -> None:
    http, repo = client
    response = http.get(f"/api/v1/activities?sort={sort}&asc=true")
    assert response.status_code == 200
    assert repo.calls[-1]["sort"] == column
    assert repo.calls[-1]["asc"] is True


def test_sort_outside_whitelist_is_client_error(client) -> None:
    http, repo = client
    response = http.get("/api/v1/activities?sort=password")
    assert response.status_code == 400
    assert repo.calls == []
