"""匿名访问 ``GET /api/v1/users`` 的入口收紧。

未版本化实现曾允许匿名按学号精确查姓名，只能靠按 IP 的读限流
（``read_throttle``）压低枚举成本；合并后列表端点统一为 v2 实现，
``get_current_user`` 直接要求登录——匿名探测连预算都没有，直接被 401 拒掉。
``read_throttle`` 的匿名档随之不再有调用方（本次不改那个模块，避免影响其他
读取路径的未来复用）。

这里钉住三件事：匿名读列表 / 单用户 / 用户活动一律 401，且不会触达仓储。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from db import backends
from routers import users_router

USER_OID = "000000000000000000000001"


@pytest.fixture()
def repo(monkeypatch):
    calls: list[tuple] = []

    async def list_page(*args, **kwargs):
        calls.append(("list_page", args))
        return [], 0

    async def get_by_oid(oid):
        calls.append(("get_by_oid", oid))
        return None

    monkeypatch.setattr(
        backends,
        "get_users_repo",
        lambda *a, **k: SimpleNamespace(list_page=list_page, get_by_oid=get_by_oid),
    )
    return calls


@pytest.fixture()
def anonymous(repo) -> TestClient:
    # 不覆盖 get_current_user：没有 Authorization 头就是真实的未登录请求。
    app = FastAPI()
    app.include_router(users_router.router, prefix="/api/v1/users")
    return TestClient(app)


def test_anonymous_cannot_list_users(anonymous, repo) -> None:
    response = anonymous.get("/api/v1/users", params={"query": "20240001"})

    assert response.status_code == 401
    assert repo == []


def test_anonymous_cannot_read_a_single_user(anonymous, repo) -> None:
    response = anonymous.get(f"/api/v1/users/{USER_OID}")

    assert response.status_code == 401
    assert repo == []


def test_anonymous_cannot_read_user_activities(anonymous, repo) -> None:
    response = anonymous.get(f"/api/v1/users/{USER_OID}/activities")

    assert response.status_code == 401
    assert repo == []
