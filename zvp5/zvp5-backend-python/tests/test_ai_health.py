"""``/api/ai/health`` 的运维字段（部署形态与进程内状态的作用域）。

三个字段各有一次「看错了不会报错、只会在扩容时静默失真」的机会：

* ``worker_count`` 与实际进程数不一致 → 运维按错误的 N 算全局限额；
* ``rate_limit_per_minute`` 回显配置值而不是**进程内实际阈值** → 加了
  ``--workers 4`` 之后还以为限额没变；
* ``cooldown_scope`` 缺失 → 把某个 worker 的熔断当成全局状态。

``settings.AI_ROUTES`` 是死配置，已从响应里删掉：回显一个不生效的开关比不显示
更糟。离线：凭据目录打桩，不连库、不出网。
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import settings  # noqa: F401  (services.ai.config 读它，先导入保证顺序一致)
from routers import ai_router
from services.ai import config as ai_config
from services.ai import credentials, rate_limit, routing
from typings.log import inject_log
from util.object_id import get_current_user

ADMIN = "000000000000000000000001"


class StubLog:
    def with_text(self, *args, **kwargs):
        return self

    async def insert_log(self):
        return None


@pytest.fixture(autouse=True)
def _stubs(monkeypatch):
    async def _catalog(user_oid: str) -> list[dict]:
        return []

    monkeypatch.setattr(credentials, "catalog", _catalog)
    routing.reset_state()
    rate_limit.reset_state()
    yield
    routing.reset_state()
    rate_limit.reset_state()


def _client(user: dict | None = None) -> TestClient:
    app = FastAPI()
    app.include_router(ai_router.router, prefix="/api/ai")
    app.dependency_overrides[get_current_user] = lambda: user or {
        "id": ADMIN,
        "per": ["admin"],
    }
    app.dependency_overrides[inject_log] = lambda: StubLog()
    return TestClient(app)


def test_health_reports_the_worker_count(monkeypatch):
    monkeypatch.setattr(ai_config, "worker_count", lambda: 4)

    body = _client().get("/api/ai/health").json()["data"]

    assert body["worker_count"] == 4


def test_health_reports_the_per_process_rate_limit(monkeypatch):
    """回显的是**折算后**的阈值：运维拿它乘 worker 数才是全局真实上限。"""
    monkeypatch.setattr(ai_config, "worker_count", lambda: 4)
    monkeypatch.setattr(ai_config, "rate_limit_per_minute", lambda kind: 40)

    body = _client().get("/api/ai/health").json()["data"]

    assert body["rate_limit_per_minute"] == {"ocr": 10, "chat": 10}
    # 与频控层用的是同一个函数，不是另算一遍。
    assert body["rate_limit_per_minute"]["ocr"] == rate_limit.effective_limit("ocr")


def test_health_rate_limit_matches_single_worker_config(monkeypatch):
    monkeypatch.setattr(ai_config, "worker_count", lambda: 1)

    body = _client().get("/api/ai/health").json()["data"]

    # 单 worker 时就是配置值，逐字节不变。
    assert body["rate_limit_per_minute"] == {
        "ocr": ai_config.rate_limit_per_minute("ocr"),
        "chat": ai_config.rate_limit_per_minute("chat"),
    }


def test_health_marks_the_cooldown_scope_as_process():
    body = _client().get("/api/ai/health").json()["data"]

    assert body["cooldown_scope"] == "process"
    assert isinstance(body["cooldown_seconds"], dict)


def test_health_does_not_echo_the_dead_routes_config():
    body = _client().get("/api/ai/health").json()["data"]

    assert "routes" not in body


def test_health_is_admin_only():
    assert _client({"id": ADMIN, "per": []}).get("/api/ai/health").status_code == 403
