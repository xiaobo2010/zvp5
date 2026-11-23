"""主题偏好端点（``/api/v1/themes``）：注册表 + 读取/修改/统计，离线。

被固化的契约：

1. 注册表是唯一事实源（``canary`` / ``canary-pro`` 必须在，``THEMES`` 有序）；
2. ``GET /me`` 对 NULL / 未知历史值回默认主题，绝不 500；
3. ``PUT /me`` 先 strip+lower 再校验，未知主题 400 且**不写库**；
4. ``GET /stats`` 只给管理员，``items`` 按注册表顺序、包含 0 人的主题，
   未知历史值只计入 ``other``。

离线机制：``SimpleNamespace`` 假仓储 monkeypatch ``backends.get_users_repo``，
TestClient 只挂 ``theme_router``，认证依赖用 ``app.dependency_overrides`` 注入。
不连 ADB、不启中间件。

    cd <repo root> && .venv/bin/python -m pytest tests/test_theme_router.py -q
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from db import backends
from routers import theme_router
from services import theme_registry

PREFIX = "/api/v1/themes"
USER = {"id": "000000000000000000000001", "per": ["student"]}


class _FakeUsersRepo:
    """只实现主题相关的三个方法；其它方法由具体用例按需补。"""

    def __init__(self, theme: str | None = None, counts: dict | None = None) -> None:
        self.theme = theme
        self.counts = counts or {}
        self.set_calls: list[tuple[str, str]] = []

    async def get_theme(self, oid: str) -> str | None:
        return self.theme

    async def set_theme(self, oid: str, theme: str) -> None:
        self.set_calls.append((oid, theme))
        self.theme = theme

    async def count_by_theme(self) -> dict[str, int]:
        return dict(self.counts)


@pytest.fixture()
def make_client(monkeypatch):
    """返回 ``(repo) -> TestClient`` 的工厂；仓储由用例自己准备。"""

    def _factory(repo, user: dict | None = None) -> TestClient:
        monkeypatch.setattr(backends, "get_users_repo", lambda *a, **k: repo)
        app = FastAPI()
        app.include_router(theme_router.router, prefix=PREFIX)
        app.dependency_overrides[theme_router.get_current_user] = (
            lambda: user if user is not None else USER
        )
        return TestClient(app)

    return _factory


def test_registry_contains_the_shipped_themes() -> None:
    assert theme_registry.DEFAULT_THEME == "canary"
    assert {"canary", "canary-pro"} <= set(theme_registry.THEME_IDS)
    assert theme_registry.THEME_IDS == {theme.id for theme in theme_registry.THEMES}

    items = theme_registry.theme_items()
    assert [item["id"] for item in items[: len(theme_registry.THEMES)]] == [
        theme.id for theme in theme_registry.THEMES
    ]
    canary = next(item for item in items if item["id"] == "canary")
    assert canary["label"] == "Canary"
    assert canary["status"] == "stable"
    assert canary["description"]


def test_list_themes_returns_registry_and_default(make_client) -> None:
    client = make_client(_FakeUsersRepo())

    body = client.get(PREFIX).json()

    assert body["status"] == "ok"
    assert body["data"]["default"] == "canary"
    assert [item["id"] for item in body["data"]["items"]] == [
        theme.id for theme in theme_registry.THEMES
    ]


def test_get_me_falls_back_to_default_when_stored_value_is_null(make_client) -> None:
    client = make_client(_FakeUsersRepo(theme=None))

    body = client.get(f"{PREFIX}/me").json()

    assert body == {"status": "ok", "data": {"theme": "canary"}}


def test_get_me_falls_back_to_default_for_unknown_historical_value(make_client) -> None:
    client = make_client(_FakeUsersRepo(theme="retired-theme"))

    body = client.get(f"{PREFIX}/me").json()

    assert body["data"]["theme"] == "canary"


def test_put_me_persists_a_registered_theme(make_client) -> None:
    repo = _FakeUsersRepo()
    client = make_client(repo)

    body = client.put(f"{PREFIX}/me", json={"theme": "canary-pro"}).json()

    assert body == {"status": "ok", "data": {"theme": "canary-pro"}}
    assert repo.set_calls == [(USER["id"], "canary-pro")]
    assert client.get(f"{PREFIX}/me").json()["data"]["theme"] == "canary-pro"


def test_put_me_rejects_unknown_theme_without_writing(make_client) -> None:
    repo = _FakeUsersRepo()
    client = make_client(repo)

    response = client.put(f"{PREFIX}/me", json={"theme": "neon-cyber"})

    assert response.status_code == 400
    assert response.json()["detail"] == "未知主题"
    assert repo.set_calls == []
    assert repo.theme is None


def test_put_me_normalizes_case_and_whitespace(make_client) -> None:
    repo = _FakeUsersRepo()
    client = make_client(repo)

    body = client.put(f"{PREFIX}/me", json={"theme": "  Canary-Pro  "}).json()

    assert body["data"]["theme"] == "canary-pro"
    assert repo.set_calls == [(USER["id"], "canary-pro")]


def test_stats_requires_admin(make_client) -> None:
    repo = _FakeUsersRepo(counts={"canary": 1})
    client = make_client(repo, user={"id": USER["id"], "per": []})

    response = client.get(f"{PREFIX}/stats")

    # 真实 ``_require_system_admin``：没有 admin 角色，在查库之前就 403。
    assert response.status_code == 403


def test_stats_reports_counts_percent_and_other(
    make_client, monkeypatch
) -> None:
    async def _allow(_user: dict) -> dict:
        return {"_id": USER["id"]}

    monkeypatch.setattr(theme_router, "_require_system_admin", _allow)

    repo = _FakeUsersRepo(
        counts={"canary": 12, "canary-pro": 6, "retired-theme": 2}
    )
    client = make_client(repo)

    body = client.get(f"{PREFIX}/stats").json()

    assert body["status"] == "ok"
    data = body["data"]
    assert data["total"] == 20
    # 注册表顺序 + 0 人的主题也必须在。
    assert [item["id"] for item in data["items"]] == [
        "canary",
        "canary-pro",
        "canary-anthropic",
    ]
    by_id = {item["id"]: item for item in data["items"]}
    assert by_id["canary"] == {
        "id": "canary",
        "label": "Canary",
        "count": 12,
        "percent": 60.0,
    }
    assert by_id["canary-pro"]["count"] == 6
    assert by_id["canary-pro"]["percent"] == 30.0
    # 未知历史值只计入 other，不伪造 item。
    assert data["other"] == 2
    assert "retired-theme" not in by_id


def test_stats_handles_empty_user_table(make_client, monkeypatch) -> None:
    async def _allow(_user: dict) -> dict:
        return {"_id": USER["id"]}

    monkeypatch.setattr(theme_router, "_require_system_admin", _allow)
    client = make_client(_FakeUsersRepo(counts={}))

    data = client.get(f"{PREFIX}/stats").json()["data"]

    assert data["total"] == 0
    assert data["other"] == 0
    assert all(item["count"] == 0 and item["percent"] == 0.0 for item in data["items"])


def test_stats_admin_guard_is_the_system_one(make_client) -> None:
    """``stats`` 复用的就是 ``system_router._require_system_admin``（同一对象）。"""
    from routers.system_router import _require_system_admin

    assert theme_router._require_system_admin is _require_system_admin
