"""``GET /api/v3/users/me/device-bindings`` 的离线契约。

覆盖设备自助读取语义：

* 当前设备 ID 只来自 ``Xuehai-Id`` 请求头，缺失/非法为 ``None``；
* 已登记列表只读当前登录账号的 ``status='active'`` 绑定，按登记顺序返回；
* 被撤销的历史绑定、别的账号的绑定都不会出现在响应里；
* 端点只依赖 ``get_current_user``，不接受调用方传 user_id（无 IDOR 入口）。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from db import backends
from routers import users_v3_router
from util.object_id import get_current_user

PREFIX = "/api/v3/users"
USER_OID = "000000000000000000000002"
OTHER_OID = "000000000000000000000003"


def _binding(oid: str, user_oid: str, xuehai_id: str, status: str) -> dict:
    return {
        "_id": oid,
        "user_oid": user_oid,
        "xuehai_id": xuehai_id,
        "device_id": None,
        "source": "auto",
        "status": status,
        "note": None,
    }


class FakeBindingsRepo:
    """``BindingsRepo`` 替身：只实现本端点用到的 active 查询。"""

    def __init__(self, rows: dict[str, list[dict]], calls: list[str]) -> None:
        self._rows = rows
        self.calls = calls

    async def get_active_for_user(self, user_oid: str) -> list[dict]:
        self.calls.append(str(user_oid))
        return [row for row in self._rows.get(str(user_oid), []) if row["status"] == "active"]


@pytest.fixture
def api(monkeypatch):
    rows = {
        USER_OID: [
            _binding("0000000000000000000000a1", USER_OID, "10001", "active"),
            _binding("0000000000000000000000a2", USER_OID, "20002", "revoked"),
            _binding("0000000000000000000000a3", USER_OID, "30003", "active"),
        ],
        OTHER_OID: [
            _binding("0000000000000000000000b1", OTHER_OID, "99999", "active"),
        ],
    }
    calls: list[str] = []
    monkeypatch.setattr(
        backends,
        "get_bindings_repo",
        lambda *a, **k: FakeBindingsRepo(rows, calls),
    )

    app = FastAPI()
    app.include_router(users_v3_router.router, prefix=PREFIX)
    app.dependency_overrides[get_current_user] = lambda: {"id": USER_OID, "per": []}
    return SimpleNamespace(
        http=TestClient(app),
        app=app,
        rows=rows,
        calls=calls,
    )


def test_requires_authentication(api) -> None:
    api.app.dependency_overrides.pop(get_current_user)

    response = api.http.get(f"{PREFIX}/me/device-bindings")

    assert response.status_code == 401


def test_returns_current_device_from_header_and_active_bindings(api) -> None:
    response = api.http.get(
        f"{PREFIX}/me/device-bindings", headers={"Xuehai-Id": "1234567890"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["code"] == 200
    assert body["data"] == {
        "current_device_id": "1234567890",
        # 20002 已撤销，必须过滤；其余保持登记顺序。
        "registered_device_ids": ["10001", "30003"],
    }
    # 只查当前登录账号，且调用的是 active 查询（不会把 revoked 抖出来）。
    assert api.calls == [USER_OID]


def test_missing_or_invalid_header_returns_null(api) -> None:
    missing = api.http.get(f"{PREFIX}/me/device-bindings").json()["data"]
    assert missing["current_device_id"] is None

    invalid = api.http.get(
        f"{PREFIX}/me/device-bindings", headers={"Xuehai-Id": "abc-123"}
    ).json()["data"]
    assert invalid["current_device_id"] is None


def test_other_account_bindings_are_never_read(api) -> None:
    response = api.http.get(f"{PREFIX}/me/device-bindings")

    assert response.json()["data"]["registered_device_ids"] == ["10001", "30003"]
    assert "99999" not in response.text
    assert api.calls == [USER_OID]


def test_empty_binding_list(api) -> None:
    api.rows[USER_OID] = []

    response = api.http.get(f"{PREFIX}/me/device-bindings")

    assert response.json()["data"]["registered_device_ids"] == []
    assert api.calls == [USER_OID]
