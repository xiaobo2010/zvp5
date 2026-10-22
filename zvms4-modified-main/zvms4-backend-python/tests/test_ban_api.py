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


def _rule_row(
    code: str = "foreign_device_login",
    trigger: str = "foreign_device_login",
    builtin: bool = True,
) -> dict:
    return {
        "_id": "0000000000000000000000aa",
        "code": code,
        "name": "非主学海设备登录",
        "trigger": trigger,
        "enabled": True,
        "config": '{"threshold": 3, "window_hours": 24}',
        "penalties": "[1, 2, 7]",
        "builtin": builtin,
        "created_at": UPDATED,
        "updated_at": UPDATED,
    }


@pytest.fixture
def api(monkeypatch):
    rules = {"foreign_device_login": _rule_row()}
    calls = {"updates": [], "inserts": [], "deletes": []}
    seq = {"n": 0}

    async def list_all():
        return list(rules.values())

    async def get_by_code(code):
        return rules.get(code)

    async def get_by_trigger(trigger):
        for rule in rules.values():
            if rule["trigger"] == trigger:
                return rule
        return None

    async def insert(
        code, name, trigger, *, enabled, config, penalties, builtin=False
    ):
        seq["n"] += 1
        oid = f"{seq['n']:024x}"
        rules[code] = {
            "_id": oid,
            "code": code,
            "name": name,
            "trigger": trigger,
            "enabled": enabled,
            "config": config,
            "penalties": penalties,
            "builtin": builtin,
            "created_at": UPDATED,
            "updated_at": UPDATED,
        }
        calls["inserts"].append(
            {
                "code": code,
                "name": name,
                "trigger": trigger,
                "enabled": enabled,
                "config": config,
                "penalties": penalties,
                "builtin": builtin,
            }
        )
        return oid

    async def delete_by_code(code):
        if code not in rules:
            return 0
        del rules[code]
        calls["deletes"].append(code)
        return 1

    async def update_by_code(
        code, *, name=None, enabled=None, config=None, penalties=None
    ):
        rule = rules.get(code)
        if rule is None:
            return 0
        if name is not None:
            rule["name"] = name
        if enabled is not None:
            rule["enabled"] = enabled
        if config is not None:
            rule["config"] = config
        if penalties is not None:
            rule["penalties"] = penalties
        rule["updated_at"] = datetime(2026, 10, 20, 13, 0, 0)
        calls["updates"].append(
            {
                "code": code,
                "name": name,
                "enabled": enabled,
                "config": config,
                "penalties": penalties,
            }
        )
        return 1

    records = [
        {
            "_id": "0000000000000000000000bb",
            "user_oid": "000000000000000000000002",
            "user_name": "李四",
            "user_business_id": "2026002",
            "source": "rule",
            "rule_code": "foreign_device_login",
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
            get_by_trigger=get_by_trigger,
            insert=insert,
            delete_by_code=delete_by_code,
            update_by_code=update_by_code,
        ),
    )
    monkeypatch.setattr(
        backends,
        "get_ban_records_repo",
        lambda *a, **k: SimpleNamespace(list_page=list_page),
    )

    async def get_names(oids):
        return {oid: "管理员" for oid in oids}

    monkeypatch.setattr(
        backends,
        "get_users_repo",
        lambda *a, **k: SimpleNamespace(get_names=get_names),
    )

    # 规则变更审计日志：与 devices_api 同款打桩，避免真去连库写日志。
    log = SimpleNamespace(
        entries=[],
        with_text=lambda text, type="", level="": (
            log.entries.append({"text": text, "type": type, "level": level}) or log
        ),
    )

    async def insert_log():
        return None

    log.insert_log = insert_log
    calls["log"] = log

    app = FastAPI()
    app.include_router(ban_router.router, prefix=PREFIX)
    app.dependency_overrides[get_current_user] = lambda: ADMIN
    app.dependency_overrides[ban_router.inject_log] = lambda: log
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
    response = http.put(f"{PREFIX}/rules/foreign_device_login", json={"enabled": False})
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
            "code": "foreign_device_login",
            "name": "非主学海设备登录",
            "trigger": "foreign_device_login",
            "trigger_name": "非主学海设备登录",
            "enabled": True,
            "builtin": True,
            "config": {"threshold": 3, "window_hours": 24},
            "penalties": [1, 2, 7],
            "updated_at": "2026-10-19T08:30:00",
        }
    ]


def test_broken_rule_json_degrades_to_empty(api) -> None:
    http, rules, _calls = api
    rules["foreign_device_login"]["config"] = "not-json"
    rules["foreign_device_login"]["penalties"] = "not-json"

    data = http.get(f"{PREFIX}/rules").json()["data"][0]
    assert data["config"] == {}
    assert data["penalties"] == []


# ---------------------------------------------------------------------------
# PUT /rules/{code}
# ---------------------------------------------------------------------------


def test_toggle_enabled_only(api) -> None:
    http, rules, calls = api
    body = http.put(
        f"{PREFIX}/rules/foreign_device_login", json={"enabled": False}
    ).json()

    assert body["code"] == 200
    assert body["data"]["enabled"] is False
    assert body["data"]["config"] == {"threshold": 3, "window_hours": 24}
    assert calls["updates"][0] == {
        "code": "foreign_device_login",
        "name": None,
        "enabled": False,
        "config": None,
        "penalties": None,
    }


def test_config_merges_with_existing_values(api) -> None:
    """只传 window_hours 时 threshold 必须保留现值。"""
    http, rules, _calls = api
    body = http.put(
        f"{PREFIX}/rules/foreign_device_login",
        json={"config": {"window_hours": 48}},
    ).json()

    assert body["data"]["config"] == {"threshold": 3, "window_hours": 48}
    assert rules["foreign_device_login"]["config"] == '{"threshold": 3, "window_hours": 48}'


def test_penalties_replacement(api) -> None:
    http, rules, _calls = api
    body = http.put(
        f"{PREFIX}/rules/foreign_device_login", json={"penalties": [2, 5, 30]}
    ).json()

    assert body["data"]["penalties"] == [2, 5, 30]
    assert rules["foreign_device_login"]["penalties"] == "[2, 5, 30]"


def test_penalties_allows_trailing_permanent(api) -> None:
    """末位 ``null`` 表示「此后永久」，是种子梯度 [1,3,7,14,30,null] 的形状。"""
    http, rules, _calls = api
    body = http.put(
        f"{PREFIX}/rules/foreign_device_login",
        json={"penalties": [1, 3, 7, 14, 30, None]},
    ).json()

    assert body["code"] == 200
    assert body["data"]["penalties"] == [1, 3, 7, 14, 30, None]
    assert rules["foreign_device_login"]["penalties"] == "[1, 3, 7, 14, 30, null]"


def test_penalties_still_accepts_all_int_gradient(api) -> None:
    http, _rules, _calls = api
    body = http.put(
        f"{PREFIX}/rules/foreign_device_login",
        json={"penalties": [1, 3, 7, 14, 30]},
    ).json()

    assert body["data"]["penalties"] == [1, 3, 7, 14, 30]


def test_penalties_rejects_null_before_the_end(api) -> None:
    """``null`` 之后的档位永远轮不到，配置就成了死数据，直接在写入口拒绝。"""
    http, _rules, calls = api
    response = http.put(
        f"{PREFIX}/rules/foreign_device_login", json={"penalties": [1, None, 7]}
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
    response = http.put(f"{PREFIX}/rules/foreign_device_login", json=payload)

    assert response.status_code == 422
    assert calls["updates"] == []


def test_unknown_rule_is_404(api) -> None:
    http, _rules, calls = api
    response = http.put(f"{PREFIX}/rules/not_a_rule", json={"enabled": False})

    assert response.status_code == 404
    assert response.json()["detail"] == "规则不存在"
    assert calls["updates"] == []


# ---------------------------------------------------------------------------
# PUT /rules/{code} 的审计日志
# ---------------------------------------------------------------------------


def test_rule_change_writes_audit_log(api) -> None:
    """改的是登录闸门阈值/梯度，必须留下「谁把什么从多少改成多少」。"""
    http, _rules, calls = api
    body = http.put(
        f"{PREFIX}/rules/foreign_device_login",
        json={
            "enabled": False,
            "config": {"threshold": 5},
            "penalties": [1, 3],
        },
    ).json()

    assert body["code"] == 200
    entries = calls["log"].entries
    assert len(entries) == 1
    entry = entries[0]
    assert entry["type"] == "ban_change"
    assert entry["level"] == "warning"
    text = entry["text"]
    assert "foreign_device_login" in text
    assert "threshold 3→5" in text
    assert "window_hours 24→24" in text
    assert "penalties [1, 2, 7]→[1, 3]" in text
    assert "enabled True→False" in text


def test_empty_patch_writes_no_audit_log(api) -> None:
    """空 patch 是一次「读回」，没改动就不该产生审计行。"""
    http, _rules, calls = api
    http.put(f"{PREFIX}/rules/foreign_device_login", json={})

    assert calls["updates"] == []
    assert calls["log"].entries == []


def test_rejected_rule_patch_writes_no_audit_log(api) -> None:
    http, _rules, calls = api
    http.put(f"{PREFIX}/rules/foreign_device_login", json={"penalties": [3, 2]})
    http.put(f"{PREFIX}/rules/not_a_rule", json={"enabled": False})

    assert calls["log"].entries == []


# ---------------------------------------------------------------------------
# GET /triggers —— 平台注册的检查项（新增规则的下拉来源）
# ---------------------------------------------------------------------------


def test_triggers_lists_registered_checks(api) -> None:
    http, _rules, _calls = api
    body = http.get(f"{PREFIX}/triggers").json()

    assert body["status"] == "ok"
    assert [item["trigger"] for item in body["data"]] == [
        "foreign_device_login",
        "platform_misuse",
    ]
    for item in body["data"]:
        assert item["name"]
        assert item["evaluation"] in ("login", "report")
        assert item["description"]
        # 新建规则的默认参数：阈值/窗口都必须是合法范围内的整数
        assert isinstance(item["config"]["threshold"], int)
        assert isinstance(item["config"]["window_hours"], int)


def test_triggers_requires_admin(api) -> None:
    http, _rules, _calls = api
    _as_student(http.app)
    assert http.get(f"{PREFIX}/triggers").status_code == 403


# ---------------------------------------------------------------------------
# POST /rules —— 新增规则
# ---------------------------------------------------------------------------

NEW_RULE = {
    "code": "my_rule",
    "name": "我的规则",
    "trigger": "platform_misuse",
    "config": {"threshold": 3, "window_hours": 24},
    "penalties": [1, 3, 7],
    "enabled": True,
}


def test_create_rule_returns_201_and_the_same_shape_as_list(api) -> None:
    http, rules, calls = api
    response = http.post(f"{PREFIX}/rules", json=NEW_RULE)

    assert response.status_code == 201
    body = response.json()
    assert body["code"] == 201
    assert body["data"] == {
        "code": "my_rule",
        "name": "我的规则",
        "trigger": "platform_misuse",
        "trigger_name": "平台违规使用",
        "enabled": True,
        "builtin": False,
        "config": {"threshold": 3, "window_hours": 24},
        "penalties": [1, 3, 7],
        "updated_at": "2026-10-19T08:30:00",
    }
    assert "my_rule" in rules
    insert = calls["inserts"][0]
    assert insert["trigger"] == "platform_misuse"
    assert insert["config"] == '{"threshold": 3, "window_hours": 24}'
    assert insert["penalties"] == "[1, 3, 7]"
    # 运行时新建的一律不是内置规则
    assert insert["builtin"] is False


def test_create_rule_fills_missing_config_from_registry(api) -> None:
    http, rules, calls = api
    payload = {**NEW_RULE, "config": {"threshold": 5}}

    http.post(f"{PREFIX}/rules", json=payload)

    # 只传 threshold 时窗口取注册表默认值，配置形状始终完整
    assert rules["my_rule"]["config"] == '{"threshold": 5, "window_hours": 24}'
    assert calls["inserts"][0]["config"] == '{"threshold": 5, "window_hours": 24}'


def test_create_rule_writes_ban_change_audit(api) -> None:
    http, _rules, calls = api
    http.post(f"{PREFIX}/rules", json=NEW_RULE)

    entry = calls["log"].entries[0]
    assert entry["type"] == "ban_change"
    assert entry["level"] == "warning"
    assert "my_rule" in entry["text"]
    assert "平台违规使用" in entry["text"]
    assert "threshold 3" in entry["text"]


def test_create_rule_rejects_duplicate_code(api) -> None:
    http, _rules, calls = api
    response = http.post(
        f"{PREFIX}/rules", json={**NEW_RULE, "code": "foreign_device_login"}
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "规则 code 已存在"
    assert calls["inserts"] == []


def test_create_rule_rejects_occupied_trigger(api) -> None:
    """一个检查项最多一条规则：否则「按哪条梯度封」取决于查询顺序。"""
    http, _rules, calls = api
    response = http.post(
        f"{PREFIX}/rules", json={**NEW_RULE, "trigger": "foreign_device_login"}
    )

    assert response.status_code == 409
    assert "已被规则 foreign_device_login 使用" in response.json()["detail"]
    assert calls["inserts"] == []


@pytest.mark.parametrize(
    "code",
    ["MyRule", "1my_rule", "my-rule", "ab", "a" * 65, "", "我的规则"],
)
def test_create_rule_rejects_bad_code(api, code) -> None:
    http, _rules, calls = api
    response = http.post(f"{PREFIX}/rules", json={**NEW_RULE, "code": code})

    assert response.status_code == 422
    assert calls["inserts"] == []


def test_create_rule_rejects_unknown_trigger(api) -> None:
    http, _rules, calls = api
    response = http.post(f"{PREFIX}/rules", json={**NEW_RULE, "trigger": "no_such"})

    assert response.status_code == 422
    assert "未知的检查项" in response.json()["detail"]
    assert calls["inserts"] == []


@pytest.mark.parametrize(
    "payload",
    [
        {**NEW_RULE, "name": ""},
        {**NEW_RULE, "name": "   "},
        {**NEW_RULE, "name": "x" * 129},
        {**NEW_RULE, "config": {"threshold": 1}},
        {**NEW_RULE, "config": {"threshold": 21}},
        {**NEW_RULE, "config": {"window_hours": 0}},
        {**NEW_RULE, "config": {"window_hours": 169}},
        {**NEW_RULE, "penalties": []},
        {**NEW_RULE, "penalties": [1, 2, 3, 4, 5, 6, 7]},
        {**NEW_RULE, "penalties": [1, None, 3]},
        {**NEW_RULE, "penalties": [3, 2]},
    ],
)
def test_create_rule_rejects_invalid_payload(api, payload) -> None:
    http, _rules, calls = api
    response = http.post(f"{PREFIX}/rules", json=payload)

    assert response.status_code == 422
    assert calls["inserts"] == []


def test_create_rule_requires_admin(api) -> None:
    http, _rules, calls = api
    _as_student(http.app)
    response = http.post(f"{PREFIX}/rules", json=NEW_RULE)

    assert response.status_code == 403
    assert calls["inserts"] == []


# ---------------------------------------------------------------------------
# DELETE /rules/{code}
# ---------------------------------------------------------------------------


def test_delete_custom_rule_writes_ban_change(api) -> None:
    http, rules, calls = api
    rules["my_rule"] = _rule_row("my_rule", "platform_misuse", builtin=False)

    response = http.delete(f"{PREFIX}/rules/my_rule")

    assert response.status_code == 204
    assert calls["deletes"] == ["my_rule"]
    assert "my_rule" not in rules
    entry = calls["log"].entries[-1]
    assert entry["type"] == "ban_change"
    assert entry["level"] == "warning"
    assert "my_rule" in entry["text"]


def test_delete_builtin_rule_is_409(api) -> None:
    """内置规则对应一条求值链路：删掉会让策略静默失效，只能停用。"""
    http, rules, calls = api
    response = http.delete(f"{PREFIX}/rules/foreign_device_login")

    assert response.status_code == 409
    assert response.json()["detail"] == "内置规则不能删除，只能停用"
    assert calls["deletes"] == []
    assert "foreign_device_login" in rules
    assert calls["log"].entries == []


def test_delete_unknown_rule_is_404(api) -> None:
    http, _rules, calls = api
    response = http.delete(f"{PREFIX}/rules/not_a_rule")

    assert response.status_code == 404
    assert response.json()["detail"] == "规则不存在"
    assert calls["deletes"] == []


def test_delete_rule_requires_admin(api) -> None:
    http, rules, calls = api
    rules["my_rule"] = _rule_row("my_rule", "platform_misuse", builtin=False)
    _as_student(http.app)

    assert http.delete(f"{PREFIX}/rules/my_rule").status_code == 403
    assert calls["deletes"] == []


# ---------------------------------------------------------------------------
# PUT /rules/{code}：name 可改，trigger 不可改
# ---------------------------------------------------------------------------


def test_rename_rule(api) -> None:
    http, rules, calls = api
    body = http.put(
        f"{PREFIX}/rules/foreign_device_login", json={"name": "换机登录"}
    ).json()

    assert body["code"] == 200
    assert body["data"]["name"] == "换机登录"
    assert rules["foreign_device_login"]["name"] == "换机登录"
    assert calls["updates"][0]["name"] == "换机登录"


@pytest.mark.parametrize("name", ["", "   ", "x" * 129])
def test_rename_rejects_bad_length(api, name) -> None:
    http, _rules, calls = api
    response = http.put(f"{PREFIX}/rules/foreign_device_login", json={"name": name})

    assert response.status_code == 422
    assert calls["updates"] == []


def test_changing_trigger_is_rejected(api) -> None:
    """换 trigger 等于换一条规则（求值链路全变），静默忽略又会骗调用方。"""
    http, rules, calls = api
    response = http.put(
        f"{PREFIX}/rules/foreign_device_login",
        json={"trigger": "platform_misuse", "enabled": False},
    )

    assert response.status_code == 422
    assert "不可修改" in response.json()["detail"]
    assert calls["updates"] == []
    assert rules["foreign_device_login"]["enabled"] is True


def test_sending_the_same_trigger_is_allowed(api) -> None:
    """前端整表单提交时会把现值一起带上，这种「没改」不能报错。"""
    http, _rules, calls = api
    response = http.put(
        f"{PREFIX}/rules/foreign_device_login",
        json={"trigger": "foreign_device_login", "enabled": False},
    )

    assert response.status_code == 200
    assert calls["updates"][0]["enabled"] is False


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
        "rule_code": "foreign_device_login",
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
        "ban_reason": "非主学海设备登录",
        "ban_until": datetime(2026, 11, 1, 0, 0, 0),
        "ban_source": "rule",
        "ban_rule": "foreign_device_login",
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
    assert row["rule_code"] == "foreign_device_login"


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

