"""重查询端点的读预算（60 次/分钟/用户，进程内滑动窗口）。

钉四件事：超限 429 + 稳定错误码；管理员不豁免；不同用户各算一份；没挂预算的
端点不受影响（打字节奏的轮询/详情不在预算口径内）。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from db import backends
from routers import logs_router
from typings.log import inject_log
from util import read_throttle
from util.object_id import get_current_user

ADMIN = "000000000000000000000001"
PEER = "000000000000000000000002"


class StubLog:
    def with_text(self, *_args, **_kwargs):
        return self

    async def insert_log(self, *_args, **_kwargs):
        return None


@pytest.fixture
def api(monkeypatch):
    async def list_page(**_kwargs):
        return [], 0

    async def get_names(_oids):
        return {}

    monkeypatch.setattr(
        backends,
        "get_applogs_repo",
        lambda *a, **k: SimpleNamespace(list_page=list_page),
    )
    monkeypatch.setattr(logs_router, "get_user_display_names", get_names)

    app = FastAPI()
    app.include_router(logs_router.router, prefix="/api/logs")

    @app.get("/api/ping")
    async def _ping():
        return {"status": "ok"}

    app.dependency_overrides[get_current_user] = lambda: {
        "id": ADMIN,
        "per": ["admin"],
    }
    app.dependency_overrides[inject_log] = lambda: StubLog()
    return TestClient(app)


def test_within_budget_is_allowed(api):
    for _ in range(read_throttle.LIMIT):
        assert api.get("/api/logs").status_code == 200


def test_over_budget_is_429_with_stable_code(api):
    for _ in range(read_throttle.LIMIT):
        api.get("/api/logs")

    response = api.get("/api/logs")
    assert response.status_code == 429
    assert response.json()["detail"] == {
        "code": "READ_RATE_LIMITED",
        "message": "查询过于频繁，请稍后再试",
    }
    assert response.headers["Retry-After"] == "60"


def test_admin_is_throttled_too(api):
    for _ in range(read_throttle.LIMIT):
        api.get("/api/logs")
    assert api.get("/api/logs").status_code == 429


def test_budget_is_per_user():
    for _ in range(read_throttle.LIMIT):
        read_throttle.check(ADMIN)
    with pytest.raises(read_throttle.ReadRateLimited):
        read_throttle.check(ADMIN)
    read_throttle.check(PEER)


def test_window_slides(monkeypatch):
    clock = {"now": 1000.0}
    monkeypatch.setattr(read_throttle.time, "monotonic", lambda: clock["now"])
    for _ in range(read_throttle.LIMIT):
        read_throttle.check(ADMIN)
    clock["now"] += read_throttle.WINDOW_SECONDS + 0.5
    read_throttle.check(ADMIN)


def test_uncovered_endpoints_are_unaffected(api):
    for _ in range(read_throttle.LIMIT):
        assert api.get("/api/logs").status_code == 200
    assert api.get("/api/logs").status_code == 429
    assert api.get("/api/ping").status_code == 200


def test_retry_after_shrinks_with_the_window(monkeypatch):
    clock = {"now": 1000.0}
    monkeypatch.setattr(read_throttle.time, "monotonic", lambda: clock["now"])
    for _ in range(read_throttle.LIMIT):
        read_throttle.check(ADMIN)
    clock["now"] += 50.0
    with pytest.raises(read_throttle.ReadRateLimited) as exc:
        read_throttle.check(ADMIN)
    assert exc.value.retry_after == 10
