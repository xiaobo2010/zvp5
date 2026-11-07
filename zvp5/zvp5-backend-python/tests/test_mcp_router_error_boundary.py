"""``/api/v1/mcp/activities/{id}`` 的异常收窄（审计 P2）。

裸 ``except:`` 会把 ``KeyboardInterrupt`` / ``SystemExit`` / ``TypeError`` 全部
翻译成 400，既掩盖真正的服务端 bug，也让基类异常无法穿过 ASGI 栈。

钉死：

* 合法 oid → 200，投影与成员数照旧；
* 非法 oid（``validate_object_id`` 抛 400）→ 仍是 400 + 原文案；
* 意料之外的异常（这里用 ``TypeError`` 代表）**不再被吞**，直接冒泡。

离线：activities / members 仓储打桩。

    cd <repo root> && .venv/bin/python -m pytest tests/test_mcp_router_error_boundary.py -q
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from db import backends
from routers import mcp_router
from util.object_id import get_current_user

PREFIX = "/api/v1/mcp"
ADMIN = {"id": "64b7f0c2e1a3b4c5d6e7fc00", "per": ["admin"]}
VALID_OID = "64b7f0c2e1a3b4c5d6e7fc01"

ACTIVITY = {
    "_id": VALID_OID,
    "name": "志愿活动",
    "description": "desc",
    "type": "on-campus",
    "date": "2026-10-01",
    "location": "主楼",
    "duration": 3.5,
    "status": "approved",
    "creator": "64b7f0c2e1a3b4c5d6e7fc02",
    "createdAt": "2026-09-01T10:00:00",
    "updatedAt": "2026-09-01T10:00:00",
}


class _SpyActivitiesRepo:
    def __init__(self) -> None:
        self.gets: list[str] = []

    async def get(self, oid: str):
        self.gets.append(oid)
        return dict(ACTIVITY) if oid == VALID_OID else None


class _FakeMembersRepo:
    async def count_for_activity(self, oid: str) -> int:
        return 7

    async def list_by_activity(self, oid: str, status: str | None = None):
        return []


@pytest.fixture
def api(monkeypatch):
    repo = _SpyActivitiesRepo()
    monkeypatch.setattr(backends, "get_activities_repo", lambda *a, **k: repo)
    monkeypatch.setattr(backends, "get_members_repo", lambda *a, **k: _FakeMembersRepo())

    app = FastAPI()
    app.include_router(mcp_router.router, prefix=PREFIX)
    app.dependency_overrides[get_current_user] = lambda: ADMIN
    return TestClient(app), repo


def test_valid_id_still_answers_the_projection(api) -> None:
    http, repo = api

    response = http.get(f"{PREFIX}/activities/{VALID_OID}")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["success"] is True
    assert body["data"]["id"] == VALID_OID
    assert body["data"]["members_count"] == 7
    assert repo.gets == [VALID_OID]


def test_invalid_id_is_still_400(api) -> None:
    http, repo = api

    response = http.get(f"{PREFIX}/activities/not-an-oid")

    assert response.status_code == 400, response.text
    assert response.json() == {"detail": "Invalid activity ID format"}
    assert repo.gets == []


def test_invalid_id_is_still_400_on_statistics(api) -> None:
    http, repo = api

    response = http.get(f"{PREFIX}/activities/not-an-oid/statistics")

    assert response.status_code == 400, response.text
    assert response.json() == {"detail": "Invalid activity ID format"}
    assert repo.gets == []


def test_unexpected_error_is_no_longer_swallowed(api, monkeypatch) -> None:
    """``TypeError`` 不是「ID 格式错」：必须冒泡，而不是伪装成 400。"""
    http, _repo = api

    def _boom(_oid):
        raise TypeError("boom")

    monkeypatch.setattr(mcp_router, "validate_object_id", _boom)

    with pytest.raises(TypeError):
        http.get(f"{PREFIX}/activities/{VALID_OID}")


def test_unexpected_error_is_no_longer_swallowed_on_statistics(api, monkeypatch) -> None:
    http, _repo = api

    def _boom(_oid):
        raise TypeError("boom")

    monkeypatch.setattr(mcp_router, "validate_object_id", _boom)

    with pytest.raises(TypeError):
        http.get(f"{PREFIX}/activities/{VALID_OID}/statistics")


def test_non_admin_still_403(api) -> None:
    http, repo = api
    http.app.dependency_overrides[get_current_user] = lambda: {
        "id": "64b7f0c2e1a3b4c5d6e7fc03",
        "per": ["student"],
    }

    response = http.get(f"{PREFIX}/activities/{VALID_OID}")

    assert response.status_code == 403
    assert response.json() == {"detail": "权限不足"}
    assert repo.gets == []
