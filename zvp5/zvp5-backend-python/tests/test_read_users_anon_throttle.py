"""匿名 ``GET /api/users`` 的按 IP 限流。

未登录且能按学号精确查姓名时，端点就是一条免费的姓名探测通道；现在匿名请求
按来源 IP 走与登录用户同档的读预算（``read_throttle.LIMIT`` / 分钟），登录
用户的路径与额度不变。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from db import backends
from routers import users_router
from util import read_throttle
from util.object_id import optional_current_user

PATH = "/api/users"
LOGGED_IN = {"id": "000000000000000000000001", "per": ["student"]}


@pytest.fixture()
def repo(monkeypatch):
    calls = []

    async def search(query, logged_in, limit=None, offset=0):
        calls.append((query, logged_in, limit, offset))
        return [], 0

    monkeypatch.setattr(
        backends,
        "get_users_repo",
        lambda *a, **k: SimpleNamespace(search=search),
    )
    return calls


@pytest.fixture()
def anonymous(repo) -> TestClient:
    app = FastAPI()
    app.include_router(users_router.router, prefix="/api/users")
    app.dependency_overrides[optional_current_user] = lambda: None
    return TestClient(app)


@pytest.fixture()
def logged_in(repo) -> TestClient:
    app = FastAPI()
    app.include_router(users_router.router, prefix="/api/users")
    app.dependency_overrides[optional_current_user] = lambda: LOGGED_IN
    return TestClient(app)


def test_anonymous_probing_is_429_over_the_budget(anonymous) -> None:
    for index in range(read_throttle.LIMIT):
        assert anonymous.get(PATH, params={"query": f"2024{index:04d}"}).status_code == 200

    response = anonymous.get(PATH, params={"query": "20249999"})
    assert response.status_code == 429
    assert response.json()["detail"] == {
        "code": "READ_RATE_LIMITED",
        "message": "查询过于频繁，请稍后再试",
    }
    assert response.headers["Retry-After"] == "60"


def test_logged_in_users_are_not_throttled_by_the_anonymous_budget(logged_in) -> None:
    for index in range(read_throttle.LIMIT * 2):
        response = logged_in.get(PATH, params={"query": f"2024{index:04d}"})
        assert response.status_code == 200


def test_anonymous_budget_is_a_separate_key_namespace(anonymous) -> None:
    for _ in range(read_throttle.LIMIT):
        anonymous.get(PATH)

    # IP 档只占自己的键；具体用户的读预算键仍然为空。
    assert list(read_throttle._buckets) == ["ip:testclient"]
