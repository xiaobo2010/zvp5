"""分组搜索入参上限：``query`` 超过 64 字符在进仓储之前就 422。

背景：搜索原文会进缓存 key（缓存代理只带摘要，但落摘要之前不能让任意
长度的原文在日志/审计/仓储层自由流动），三个列表端点统一 ``max_length=64``。

离线：仓储全打桩，超长请求在 FastAPI 校验层就被挡下，桩里的
``AssertionError`` 保证「不该走到仓储」这条契约。
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from db import backends
from routers import groups_router
from util.object_id import get_current_user

PREFIX = "/api/v1/groups"
GROUP = "000000000000000000000001"
ADMIN_OID = "000000000000000000000002"


class _Repo:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def list_classes(self, query: str = "", page: int = 1, size: int = 1000):
        self.calls.append(("class", query))
        return [], 0

    async def list_permission_groups(self, query: str = "", permissions=None):
        self.calls.append(("permission", query))
        return []

    async def members_page(self, _group_id: str, _query: str, _page: int, _size: int):
        raise AssertionError("搜索词超长时不该走到仓储")

    async def activities_page(self, *_args, **_kwargs):
        raise AssertionError("搜索词超长时不该走到仓储")


@pytest.fixture()
def api(monkeypatch):
    repo = _Repo()
    monkeypatch.setattr(backends, "get_groups_repo", lambda *a, **k: repo)
    app = FastAPI()
    app.include_router(groups_router.router, prefix=PREFIX)
    app.dependency_overrides[get_current_user] = lambda: {
        "id": ADMIN_OID,
        "per": ["admin"],
    }
    return TestClient(app), repo


def test_group_list_rejects_overlong_query(api) -> None:
    client, repo = api

    response = client.get(PREFIX, params={"query": "x" * 65})

    assert response.status_code == 422
    assert repo.calls == []


def test_group_list_accepts_query_at_the_cap(api) -> None:
    client, repo = api

    assert client.get(PREFIX, params={"query": "x" * 64}).status_code == 200
    assert ("class", "x" * 64) in repo.calls
    assert ("permission", "x" * 64) in repo.calls


def test_group_member_query_rejects_overlong_term(api) -> None:
    client, repo = api

    response = client.get(
        f"{PREFIX}/{GROUP}/users", params={"query": "x" * 65}
    )

    assert response.status_code == 422
    assert repo.calls == []


def test_group_time_rejects_overlong_query_and_legacy_alias(api) -> None:
    """``query`` 与旧前端别名 ``search`` 两条入口都吃同一个上限。"""
    client, repo = api

    assert (
        client.get(f"{PREFIX}/{GROUP}/time", params={"query": "x" * 65}).status_code
        == 422
    )
    assert (
        client.get(f"{PREFIX}/{GROUP}/time", params={"search": "x" * 65}).status_code
        == 422
    )
    assert repo.calls == []
