"""``/api/v3/ban`` 管理端点的离线契约（admin only）。

覆盖读形状、写校验与写语义三块：

* ``GET /rules``：``config`` / ``penalties`` 从 JSON 文本解析成对象，坏数据降级；
* ``PUT /rules/{code}``：范围/升序校验 422、未知 code 404、局部更新只动传入字段
  （``config`` 缺省项按现值合并，不能把另一项冲掉）；
* ``GET /records``：``user_id`` 取学号、时间 ISO 字符串、``operator_name`` 来自
  操作人 join（规则封禁没有操作人 → ``None``）。

全部离线：两个仓储打桩，不连 Oracle。
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from db import backends
from routers import ban_router
from util.object_id import get_current_user

PREFIX = "/api/v3/ban"
ADMIN = {"id": "000000000000000000000001", "per": ["admin"]}
STUDENT = {"id": "000000000000000000000002", "per": ["student"]}

STARTED = datetime(2026, 10, 20, 12, 0, 0)
UNTIL = datetime(2026, 10, 21, 12, 0, 0)
UPDATED = datetime(2026, 10, 19, 8, 30, 0)


def _rule_row() -> dict:
    return {
        "_id": "0000000000000000000000aa",
        "code": "multi_device_login",
        "name": "多设备登录",
        "enabled": True,
        "config": '{"threshold": 3, "window_hours": 24}',
        "penalties": "[1, 2, 7]",
        "builtin": True,
        "created_at": UPDATED,
        "updated_at": UPDATED,
    }


@pytest.fixture
def api(monkeypatch):
    rules = {"multi_device_login": _rule_row()}
    calls = {"updates": []}

    async def list_all():
        return list(rules.values())

    async def get_by_code(code):
        return rules.get(code)

    async def update_by_code(code, *, enabled=None, config=None, penalties=None):
        rule = rules.get(code)
        if rule is None:
            return 0
        if enabled is not None:
            rule["enabled"] = enabled
        if config is not None:
            rule["config"] = config
        if penalties is not None:
            rule["penalties"] = penalties
        rule["updated_at"] = datetime(2026, 10, 20, 13, 0, 0)
        calls["updates"].append(
            {"code": code, "enabled": enabled, "config": config, "penalties": penalties}
        )
        return 1

    records = [
        {
            "_id": "0000000000000000000000bb",
            "user_oid": "000000000000000000000002",
            "user_name": "李四",
            "user_business_id": "2026002",
            "source": "rule",
            "rule_code": "multi_device_login",
            "reason": "在 24 小时内使用 3 台学海设备登录（阈值 3 台）",
            "permanent": False,
            "started_at": STARTED,
            "expires_at": UNTIL,
            "operator_oid": None,
            "operator_name": None,
            "created_at": STARTED,
        },
        {
            "_id": "0000000000000000000000cc",
            "user_oid": "000000000000000000000003",
            "user_name": "王五",
            "user_business_id": "2026003",
            "source": "admin",
            "rule_code": None,
            "reason": "管理员封禁",
            "permanent": True,
            "started_at": STARTED,
            "expires_at": None,
            "operator_oid": "000000000000000000000001",
            "operator_name": "管理员",
            "created_at": STARTED,
        },
    ]

    async def list_page(page, size, query):
        return records, len(records)

    monkeypatch.setattr(
        backends,
        "get_ban_rules_repo",
        lambda *a, **k: SimpleNamespace(
            list_all=list_all,
            get_by_code=get_by_code,
            update_by_code=update_by_code,
        ),
    )
    monkeypatch.setattr(
        backends,
        "get_ban_records_repo",
        lambda *a, **k: SimpleNamespace(list_page=list_page),
    )

    app = FastAPI()
    app.include_router(ban_router.router, prefix=PREFIX)
    app.dependency_overrides[get_current_user] = lambda: ADMIN
    return TestClient(app), rules, calls


def _as_student(app_hook) -> None:
    app_hook.dependency_overrides[get_current_user] = lambda: STUDENT


# ---------------------------------------------------------------------------
# 权限
# ---------------------------------------------------------------------------


def test_non_admin_is_rejected(api) -> None:
    http, _rules, _calls = api
    _as_student(http.app)
    for path in (f"{PREFIX}/rules", f"{PREFIX}/records"):
        response = http.get(path)
        assert response.status_code == 403
        assert response.json()["detail"] == "权限不足"


def test_non_admin_cannot_write_rules(api) -> None:
    http, _rules, calls = api
    _as_student(http.app)
    response = http.put(f"{PREFIX}/rules/multi_device_login", json={"enabled": False})
    assert response.status_code == 403
    assert calls["updates"] == []


# ---------------------------------------------------------------------------
# GET /rules
# ---------------------------------------------------------------------------


def test_list_rules_parses_json_text(api) -> None:
    http, _rules, _calls = api
    body = http.get(f"{PREFIX}/rules").json()

    assert body["status"] == "ok"
    assert body["code"] == 200
    assert body["data"] == [
        {
            "code": "multi_device_login",
            "name": "多设备登录",
            "enabled": True,
            "builtin": True,
            "config": {"threshold": 3, "window_hours": 24},
            "penalties": [1, 2, 7],
            "updated_at": "2026-10-19T08:30:00",
        }
    ]


def test_broken_rule_json_degrades_to_empty(api) -> None:
    http, rules, _calls = api
    rules["multi_device_login"]["config"] = "not-json"
    rules["multi_device_login"]["penalties"] = "not-json"

    data = http.get(f"{PREFIX}/rules").json()["data"][0]
    assert data["config"] == {}
    assert data["penalties"] == []


# ---------------------------------------------------------------------------
# PUT /rules/{code}
# ---------------------------------------------------------------------------


def test_toggle_enabled_only(api) -> None:
    http, rules, calls = api
    body = http.put(
        f"{PREFIX}/rules/multi_device_login", json={"enabled": False}
    ).json()

    assert body["code"] == 200
    assert body["data"]["enabled"] is False
    assert body["data"]["config"] == {"threshold": 3, "window_hours": 24}
    assert calls["updates"][0] == {
        "code": "multi_device_login",
        "enabled": False,
        "config": None,
        "penalties": None,
    }


def test_config_merges_with_existing_values(api) -> None:
    """只传 window_hours 时 threshold 必须保留现值。"""
    http, rules, _calls = api
    body = http.put(
        f"{PREFIX}/rules/multi_device_login",
        json={"config": {"window_hours": 48}},
    ).json()

    assert body["data"]["config"] == {"threshold": 3, "window_hours": 48}
    assert rules["multi_device_login"]["config"] == '{"threshold": 3, "window_hours": 48}'


def test_penalties_replacement(api) -> None:
    http, rules, _calls = api
    body = http.put(
        f"{PREFIX}/rules/multi_device_login", json={"penalties": [2, 5, 30]}
    ).json()

    assert body["data"]["penalties"] == [2, 5, 30]
    assert rules["multi_device_login"]["penalties"] == "[2, 5, 30]"


@pytest.mark.parametrize(
    "payload",
    [
        {"config": {"threshold": 1}},
        {"config": {"threshold": 21}},
        {"config": {"window_hours": 0}},
        {"config": {"window_hours": 169}},
        {"penalties": []},
        {"penalties": [1, 2, 3, 4, 5, 6, 7]},
        {"penalties": [0]},
        {"penalties": [1, 4000]},
        {"penalties": [3, 2]},
        {"penalties": [1, 1]},
    ],
)
def test_invalid_rule_payload_is_422(api, payload) -> None:
    http, _rules, calls = api
    response = http.put(f"{PREFIX}/rules/multi_device_login", json=payload)

    assert response.status_code == 422
    assert calls["updates"] == []


def test_unknown_rule_is_404(api) -> None:
    http, _rules, calls = api
    response = http.put(f"{PREFIX}/rules/not_a_rule", json={"enabled": False})

    assert response.status_code == 404
    assert response.json()["detail"] == "规则不存在"
    assert calls["updates"] == []


# ---------------------------------------------------------------------------
# GET /records
# ---------------------------------------------------------------------------


def test_records_shape_and_metadata(api) -> None:
    http, _rules, _calls = api
    body = http.get(f"{PREFIX}/records?page=2&perpage=10").json()

    assert body["code"] == 200
    assert body["metadata"] == {"size": 2, "page": 2, "perpage": 10}
    assert body["data"][0] == {
        "_id": "0000000000000000000000bb",
        "user_oid": "000000000000000000000002",
        "user_name": "李四",
        "user_id": "2026002",
        "source": "rule",
        "rule_code": "multi_device_login",
        "reason": "在 24 小时内使用 3 台学海设备登录（阈值 3 台）",
        "permanent": False,
        "started_at": "2026-10-20T12:00:00",
        "expires_at": "2026-10-21T12:00:00",
        "operator_name": None,
    }


def test_admin_record_carries_operator_name_and_permanent_shape(api) -> None:
    http, _rules, _calls = api
    row = http.get(f"{PREFIX}/records").json()["data"][1]

    assert row["source"] == "admin"
    assert row["permanent"] is True
    assert row["expires_at"] is None
    assert row["operator_name"] == "管理员"
