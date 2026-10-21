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


def test_penalties_allows_trailing_permanent(api) -> None:
    """末位 ``null`` 表示「此后永久」，是种子梯度 [1,3,7,14,30,null] 的形状。"""
    http, rules, _calls = api
    body = http.put(
        f"{PREFIX}/rules/multi_device_login",
        json={"penalties": [1, 3, 7, 14, 30, None]},
    ).json()

    assert body["code"] == 200
    assert body["data"]["penalties"] == [1, 3, 7, 14, 30, None]
    assert rules["multi_device_login"]["penalties"] == "[1, 3, 7, 14, 30, null]"


def test_penalties_still_accepts_all_int_gradient(api) -> None:
    http, _rules, _calls = api
    body = http.put(
        f"{PREFIX}/rules/multi_device_login",
        json={"penalties": [1, 3, 7, 14, 30]},
    ).json()

    assert body["data"]["penalties"] == [1, 3, 7, 14, 30]


def test_penalties_rejects_null_before_the_end(api) -> None:
    """``null`` 之后的档位永远轮不到，配置就成了死数据，直接在写入口拒绝。"""
    http, _rules, calls = api
    response = http.put(
        f"{PREFIX}/rules/multi_device_login", json={"penalties": [1, None, 7]}
    )

    assert response.status_code == 422
    assert calls["updates"] == []


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


# ---------------------------------------------------------------------------
# GET /users —— 当前被封用户（USERS.BANNED = 1，不是流水）
# ---------------------------------------------------------------------------

BANNED_ROWS = [
    {
        "_id": "000000000000000000000002",
        "id": "2026002",
        "name": "张三",
        "class_name": "高三8班",
        "ban_reason": "管理员封禁",
        "ban_until": None,
        "ban_source": "admin",
        "ban_rule": None,
    },
    {
        "_id": "000000000000000000000003",
        "id": "2026003",
        "name": "李四",
        "class_name": "未知班级",
        "ban_reason": "同学号多设备登录",
        "ban_until": datetime(2026, 11, 1, 0, 0, 0),
        "ban_source": "rule",
        "ban_rule": "multi_device_login",
    },
]


@pytest.fixture
def banned_api(monkeypatch):
    calls: list[tuple] = []

    async def list_banned_page(query, page, size):
        calls.append((query, page, size))
        return BANNED_ROWS, len(BANNED_ROWS)

    monkeypatch.setattr(
        backends,
        "get_users_repo",
        lambda *a, **k: SimpleNamespace(list_banned_page=list_banned_page),
    )

    app = FastAPI()
    app.include_router(ban_router.router, prefix=PREFIX)
    app.dependency_overrides[get_current_user] = lambda: ADMIN
    return TestClient(app), calls


def test_banned_users_shape_matches_frontend_contract(banned_api) -> None:
    http, calls = banned_api
    body = http.get(f"{PREFIX}/users?query=2026&page=1&perpage=20").json()

    assert body["status"] == "ok"
    assert body["code"] == 200
    assert body["metadata"] == {"size": 2, "page": 1, "perpage": 20}
    assert body["data"][0] == {
        "oid": "000000000000000000000002",
        "name": "张三",
        "user_id": "2026002",
        "class_name": "高三8班",
        "reason": "管理员封禁",
        "permanent": True,
        "until": None,
        "source": "admin",
        "rule_code": None,
    }
    # 查询参数原样透传给仓储，顺序不参与契约
    assert calls == [("2026", 1, 20)]


def test_banned_users_marks_timed_rule_ban(banned_api) -> None:
    http, _calls = banned_api
    row = http.get(f"{PREFIX}/users").json()["data"][1]

    assert row["permanent"] is False
    assert row["until"] == "2026-11-01T00:00:00"
    assert row["source"] == "rule"
    assert row["rule_code"] == "multi_device_login"


def test_banned_users_requires_admin(banned_api) -> None:
    http, _calls = banned_api
    http.app.dependency_overrides[get_current_user] = lambda: STUDENT
    response = http.get(f"{PREFIX}/users")

    assert response.status_code == 403
    assert response.json()["detail"] == "权限不足"


@pytest.mark.parametrize("qs", ["page=0", "perpage=0", "perpage=101"])
def test_banned_users_rejects_out_of_range_paging(banned_api, qs) -> None:
    http, _calls = banned_api
    assert http.get(f"{PREFIX}/users?{qs}").status_code == 422


# ---------------------------------------------------------------------------
# GET /devices、DELETE /devices/{xuehai_id} —— 设备封禁
# ---------------------------------------------------------------------------

DEVICE_ROWS = [
    {
        "xuehai_id": "1234567890",
        "reason": "临时密码连续输错 3 次",
        "until": None,
        "source": "rule",
        "rule_code": "foreign_device_login",
        "operator_oid": None,
        "created_at": STARTED,
    },
    {
        "xuehai_id": "99887766",
        "reason": "管理员封禁",
        "until": UNTIL,
        "source": "admin",
        "rule_code": None,
        "operator_oid": ADMIN["id"],
        "created_at": STARTED,
    },
]


@pytest.fixture
def devices_api(monkeypatch):
    calls = {"pages": [], "deleted": []}

    async def list_page(page, size):
        calls["pages"].append((page, size))
        return DEVICE_ROWS, len(DEVICE_ROWS)

    async def delete(xuehai_id):
        calls["deleted"].append(xuehai_id)
        return 1 if xuehai_id == "1234567890" else 0

    async def get_names(oids):
        return {oid: "管理员" for oid in oids}

    monkeypatch.setattr(
        backends,
        "get_users_repo",
        lambda *a, **k: SimpleNamespace(get_names=get_names),
    )
    monkeypatch.setattr(
        backends,
        "get_device_bans_repo",
        lambda *a, **k: SimpleNamespace(list_page=list_page, delete=delete),
        raising=False,
    )
    log = SimpleNamespace(
        entries=[],
        with_text=lambda text, type="", level="": (
            log.entries.append({"text": text, "type": type, "level": level}) or log
        ),
    )

    async def insert_log():
        return None

    log.insert_log = insert_log

    app = FastAPI()
    app.include_router(ban_router.router, prefix=PREFIX)
    app.dependency_overrides[get_current_user] = lambda: ADMIN
    app.dependency_overrides[ban_router.inject_log] = lambda: log
    return TestClient(app), calls, log


def test_banned_devices_shape(devices_api) -> None:
    http, calls, _log = devices_api
    body = http.get(f"{PREFIX}/devices?page=2&perpage=10").json()

    assert body["code"] == 200
    assert body["metadata"] == {"size": 2, "page": 2, "perpage": 10}
    assert calls["pages"] == [(2, 10)]
    assert body["data"][0] == {
        "xuehai_id": "1234567890",
        "reason": "临时密码连续输错 3 次",
        "permanent": True,
        "until": None,
        "source": "rule",
        "rule_code": "foreign_device_login",
        "created_at": "2026-10-20T12:00:00",
    }
    assert body["data"][1]["permanent"] is False
    assert body["data"][1]["until"] == "2026-10-21T12:00:00"


def test_unban_device_writes_ban_change_log(devices_api) -> None:
    http, calls, log = devices_api
    response = http.delete(f"{PREFIX}/devices/1234567890")

    assert response.status_code == 204
    assert calls["deleted"] == ["1234567890"]
    assert log.entries[-1]["type"] == "ban_change"
    assert log.entries[-1]["level"] == "warning"


def test_unban_unknown_device_is_404(devices_api) -> None:
    http, _calls, log = devices_api
    response = http.delete(f"{PREFIX}/devices/99887766")

    assert response.status_code == 404
    assert log.entries == []


def test_device_endpoints_require_admin(devices_api) -> None:
    http, _calls, _log = devices_api
    http.app.dependency_overrides[get_current_user] = lambda: STUDENT

    assert http.get(f"{PREFIX}/devices").status_code == 403
    assert http.delete(f"{PREFIX}/devices/1234567890").status_code == 403

