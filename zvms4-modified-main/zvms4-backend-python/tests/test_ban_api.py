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

from datetime import datetime, timedelta
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


def _compiled(stmt) -> str:
    """把语句按 Oracle 方言编译成 SQL 文本（字面量内联，空白折叠）。"""
    from sqlalchemy.dialects import oracle

    return " ".join(
        str(
            stmt.compile(
                dialect=oracle.dialect(), compile_kwargs={"literal_binds": True}
            )
        ).split()
    )

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
        {"config": {"threshold": 201}},
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
    triggers = [item["trigger"] for item in body["data"]]
    # 两个既有闸门的检查项排在最前（前端下拉的稳定顺序），后面是监控规则。
    assert triggers[:2] == ["foreign_device_login", "platform_misuse"]
    assert set(triggers[2:]) == {
        "login_failure_burst",
        "ban_evasion_retry",
        "challenge_flood",
        "temp_password_issue_burst",
        "export_burst",
        "login_success_churn",
        "device_switch_burst",
        "ai_chat_burst",
        "device_temp_password_abuse",
        "agent_jailbreak",
    }
    for item in body["data"]:
        assert item["name"]
        assert item["evaluation"] in ("login", "report", "monitor", "ai_moderation")
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
        {**NEW_RULE, "config": {"threshold": 201}},
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

    async def list_banned_page(query, page, size, *, include_expired=False):
        calls.append((query, page, size, include_expired))
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
    # 查询参数原样透传给仓储，顺序不参与契约；默认只列有效封禁。
    assert calls == [("2026", 1, 20, False)]


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
    calls = {"pages": [], "deleted": [], "upserts": [], "counter_deletes": []}
    counters = {"1234567890": 3}

    async def list_page(page, size, query="", *, include_expired=False):
        calls["pages"].append((page, size, query, include_expired))
        return DEVICE_ROWS, len(DEVICE_ROWS)

    async def delete(xuehai_id):
        calls["deleted"].append(xuehai_id)
        return 1 if xuehai_id == "1234567890" else 0

    async def upsert(xuehai_id, *, reason, until, source, rule_code, operator_oid):
        calls["upserts"].append(
            {
                "xuehai_id": xuehai_id,
                "reason": reason,
                "until": until,
                "source": source,
                "rule_code": rule_code,
                "operator_oid": operator_oid,
            }
        )

    async def get_counts(xuehai_ids):
        return {key: counters[key] for key in xuehai_ids if key in counters}

    async def counter_delete(xuehai_id):
        calls["counter_deletes"].append(xuehai_id)
        return 1 if counters.pop(xuehai_id, None) is not None else 0

    async def get_names(oids):
        return {oid: "管理员" for oid in oids}

    async def primary_owners(xuehai_ids):
        if "1234567890" in xuehai_ids:
            return {"1234567890": {"user_oid": "000000000000000000000009", "name": "张三"}}
        return {}

    monkeypatch.setattr(
        backends,
        "get_users_repo",
        lambda *a, **k: SimpleNamespace(
            get_names=get_names, primary_owners=primary_owners
        ),
    )
    monkeypatch.setattr(
        backends,
        "get_device_bans_repo",
        lambda *a, **k: SimpleNamespace(
            list_page=list_page, delete=delete, upsert=upsert
        ),
        raising=False,
    )
    monkeypatch.setattr(
        backends,
        "get_device_ban_counters_repo",
        lambda *a, **k: SimpleNamespace(
            get_counts=get_counts, delete=counter_delete
        ),
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
    # P2-b：单一形状 —— 只有 data + metadata，不再镜像 devices/total。
    assert "devices" not in body
    assert "total" not in body
    assert calls["pages"] == [(2, 10, "", False)]
    assert body["data"][0] == {
        "xuehai_id": "1234567890",
        "reason": "临时密码连续输错 3 次",
        "permanent": True,
        "until": None,
        "source": "rule",
        "rule_code": "foreign_device_login",
        "created_at": "2026-10-20T12:00:00",
        "ban_count": 3,
        "owner": {"user_oid": "000000000000000000000009", "name": "张三"},
    }
    assert body["data"][1]["permanent"] is False
    assert body["data"][1]["until"] == "2026-10-21T12:00:00"
    assert body["data"][1]["ban_count"] == 0
    assert body["data"][1]["owner"] is None


def test_banned_devices_query_is_forwarded(devices_api) -> None:
    http, calls, _log = devices_api
    assert http.get(f"{PREFIX}/devices?query=1234").status_code == 200
    assert calls["pages"] == [(1, 20, "1234", False)]


def test_manual_device_ban_upserts_and_audits(devices_api) -> None:
    http, calls, log = devices_api

    response = http.post(
        f"{PREFIX}/devices",
        json={"xuehai_id": "1234567890", "reason": "  多次冒用  ", "until": None},
    )

    assert response.status_code == 200
    assert calls["upserts"] == [
        {
            "xuehai_id": "1234567890",
            "reason": "多次冒用",
            "until": None,
            "source": "admin",
            "rule_code": None,
            "operator_oid": ADMIN["id"],
        }
    ]
    assert response.json()["data"]["ban_count"] == 3
    assert log.entries[-1]["type"] == "ban_change"
    assert "多次冒用" in log.entries[-1]["text"]


@pytest.mark.parametrize(
    "payload",
    [
        {"xuehai_id": ""},
        {"xuehai_id": "abc"},
        {"xuehai_id": "1" * 33},
        {"xuehai_id": "123", "reason": "x" * 513},
    ],
)
def test_manual_device_ban_validates_payload(devices_api, payload) -> None:
    http, calls, log = devices_api
    assert http.post(f"{PREFIX}/devices", json=payload).status_code == 422
    assert calls["upserts"] == []
    assert log.entries == []


def test_reset_gradient_is_idempotent(devices_api) -> None:
    http, calls, log = devices_api

    assert http.post(f"{PREFIX}/devices/1234567890/reset-gradient").status_code == 204
    assert http.post(f"{PREFIX}/devices/99999999/reset-gradient").status_code == 204

    assert calls["counter_deletes"] == ["1234567890", "99999999"]
    assert all(entry["type"] == "ban_change" for entry in log.entries)


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
    assert http.post(f"{PREFIX}/devices", json={"xuehai_id": "123"}).status_code == 403
    assert http.post(f"{PREFIX}/devices/123/reset-gradient").status_code == 403
    assert http.delete(f"{PREFIX}/devices/1234567890").status_code == 403


# ---------------------------------------------------------------------------
# P1-4 过期封禁默认不出现在「当前封禁」列表（include_expired 留口子）
# ---------------------------------------------------------------------------


def test_banned_users_forwards_include_expired(banned_api) -> None:
    http, calls = banned_api

    assert http.get(f"{PREFIX}/users?include_expired=1").status_code == 200

    assert calls == [("", 1, 20, True)]


def test_banned_devices_forwards_include_expired(devices_api) -> None:
    http, calls, _log = devices_api

    assert http.get(f"{PREFIX}/devices?include_expired=1").status_code == 200

    assert calls["pages"] == [(1, 20, "", True)]


def test_banned_users_sql_excludes_expired_by_default() -> None:
    """``banned = 1`` 不等于「此刻还在封」：默认谓词必须看 ``ban_until``。"""
    from db.repos.oracle import users as users_repo

    default_sql = _compiled(users_repo._banned_page_stmt("", 1, 20, now=STARTED))
    assert "users.banned = 1" in default_sql
    assert "users.ban_until IS NULL OR users.ban_until >" in default_sql

    count_sql = _compiled(users_repo._banned_count_stmt("", now=STARTED))
    assert "users.ban_until IS NULL OR users.ban_until >" in count_sql

    all_sql = _compiled(users_repo._banned_page_stmt("", 1, 20, True, STARTED))
    assert "users.banned = 1" in all_sql
    # 不过滤时 WHERE 只剩闸门列（ban_until 仍在投影里，不参与谓词）。
    assert "WHERE users.banned = 1 ORDER BY" in all_sql


def test_device_bans_sql_excludes_expired_by_default() -> None:
    from db.repos.oracle import device_bans

    default_sql = _compiled(device_bans._page_stmt(1, 20, now=STARTED))
    assert "banned_devices.until IS NULL OR banned_devices.until >" in default_sql

    count_sql = _compiled(device_bans._count_stmt(now=STARTED))
    assert "banned_devices.until IS NULL OR banned_devices.until >" in count_sql

    all_sql = _compiled(device_bans._page_stmt(1, 20, include_expired=True))
    assert "banned_devices.until IS NULL" not in all_sql


# ---------------------------------------------------------------------------
# P1-5 阈值上限放宽到 200（ai_chat_burst 种子阈值 80）
# ---------------------------------------------------------------------------


def test_threshold_80_can_be_saved(api) -> None:
    http, rules, _calls = api

    response = http.put(
        f"{PREFIX}/rules/foreign_device_login", json={"config": {"threshold": 80}}
    )

    assert response.status_code == 200
    assert response.json()["data"]["config"]["threshold"] == 80
    assert rules["foreign_device_login"]["config"].find('"threshold": 80') != -1


def test_threshold_200_is_the_new_ceiling(api) -> None:
    http, _rules, _calls = api

    assert (
        http.put(
            f"{PREFIX}/rules/foreign_device_login", json={"config": {"threshold": 200}}
        ).status_code
        == 200
    )
    assert (
        http.put(
            f"{PREFIX}/rules/foreign_device_login", json={"config": {"threshold": 201}}
        ).status_code
        == 422
    )


# ---------------------------------------------------------------------------
# P0-2 手动设备封禁：aware 的 until 折成本地墙钟再落库
# ---------------------------------------------------------------------------


@pytest.fixture
def shanghai_tz():
    import os
    import time as _time

    previous = os.environ.get("TZ")
    os.environ["TZ"] = "Asia/Shanghai"
    _time.tzset()
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = previous
        _time.tzset()


def test_manual_device_ban_converts_utc_offset_to_local_wall_clock(
    devices_api, shanghai_tz
) -> None:
    """``2026-10-28T14:01:17+00:00`` 存成东八区的 22:01:17（naive）。"""
    http, calls, _log = devices_api

    response = http.post(
        f"{PREFIX}/devices",
        json={"xuehai_id": "1234567890", "until": "2026-10-28T14:01:17+00:00"},
    )

    assert response.status_code == 200
    stored = calls["upserts"][0]["until"]
    assert stored == datetime(2026, 10, 28, 22, 1, 17)
    assert stored.tzinfo is None
    assert response.json()["data"]["until"] == "2026-10-28T22:01:17"


def test_manual_device_ban_keeps_naive_until_untouched(devices_api) -> None:
    http, calls, _log = devices_api

    response = http.post(
        f"{PREFIX}/devices",
        json={"xuehai_id": "1234567890", "until": "2026-10-28T14:01:17"},
    )

    assert response.status_code == 200
    assert calls["upserts"][0]["until"] == datetime(2026, 10, 28, 14, 1, 17)
    assert response.json()["data"]["until"] == "2026-10-28T14:01:17"


# ---------------------------------------------------------------------------
# P0-1 目标侧保护：不能封另一个 admin（解封方向不拦）
# ---------------------------------------------------------------------------

USERS_PREFIX = "/api/v3/users"
ACTOR = "000000000000000000000001"
PEER_ADMIN = "000000000000000000000003"
STUDENT_OID = "000000000000000000000002"


@pytest.fixture
def users_api(monkeypatch):
    state = {"bans": [], "clears": [], "records": []}
    docs = {
        PEER_ADMIN: {"_id": PEER_ADMIN, "name": "同级管理员", "roles": ["admin"]},
        STUDENT_OID: {"_id": STUDENT_OID, "name": "学生", "roles": ["student"]},
    }

    async def get_by_oid(oid):
        return docs.get(oid)

    async def get_names(oids):
        return {oid: "管理员" for oid in oids}

    async def set_ban(oid, *, reason, until, source, rule_code):
        state["bans"].append(
            {"oid": oid, "reason": reason, "until": until, "source": source}
        )

    async def clear_ban(oid):
        state["clears"].append(oid)

    async def insert(user_oid, **kwargs):
        state["records"].append({"user_oid": user_oid, **kwargs})
        return "rec"

    monkeypatch.setattr(
        backends,
        "get_users_repo",
        lambda *a, **k: SimpleNamespace(
            get_by_oid=get_by_oid,
            get_names=get_names,
            set_ban=set_ban,
            clear_ban=clear_ban,
        ),
    )
    monkeypatch.setattr(
        backends,
        "get_ban_records_repo",
        lambda *a, **k: SimpleNamespace(insert=insert),
    )

    class StubLog:
        entries: list = []

        def with_text(self, *args, **kwargs):
            self.entries.append((args, kwargs))
            return self

        async def insert_log(self):
            return None

    from routers import users_v3_router
    from typings.log import inject_log

    app = FastAPI()
    app.include_router(users_v3_router.router, prefix=USERS_PREFIX)
    app.dependency_overrides[get_current_user] = lambda: {"id": ACTOR, "per": ["admin"]}
    app.dependency_overrides[inject_log] = lambda: StubLog()
    return TestClient(app), state


def test_cannot_ban_another_admin(users_api) -> None:
    """封禁会轮换 session_id：锁死同级 admin 等于绕过双人授权。"""
    http, state = users_api

    response = http.put(f"{USERS_PREFIX}/{PEER_ADMIN}/banned", json={"banned": True})

    assert response.status_code == 403
    assert response.json()["detail"] == "不能封禁管理员账号"
    assert state["bans"] == []
    assert state["records"] == []


def test_unban_another_admin_is_allowed(users_api) -> None:
    """解封只会恢复对方权限，没有自锁风险，不在保护范围内。"""
    http, state = users_api

    response = http.put(f"{USERS_PREFIX}/{PEER_ADMIN}/banned", json={"banned": False})

    assert response.status_code == 200
    assert state["clears"] == [PEER_ADMIN]
    assert state["bans"] == []


def test_can_ban_regular_user(users_api) -> None:
    http, state = users_api

    response = http.put(f"{USERS_PREFIX}/{STUDENT_OID}/banned", json={"banned": True})

    assert response.status_code == 200
    assert state["bans"][0]["oid"] == STUDENT_OID
    assert state["records"][0]["user_oid"] == STUDENT_OID


# ---------------------------------------------------------------------------
# 管理员封禁时长：客户端只给天数，到期时刻在服务端按本地 naive 算
# ---------------------------------------------------------------------------


def test_ban_with_duration_days_sets_timed_until(users_api) -> None:
    """``duration_days=7`` → ``ban_until ≈ now+7d``（naive），流水同步限时。"""
    http, state = users_api

    response = http.put(
        f"{USERS_PREFIX}/{STUDENT_OID}/banned",
        json={"banned": True, "reason": "限时封禁", "duration_days": 7},
    )

    assert response.status_code == 200
    until = state["bans"][0]["until"]
    assert until is not None
    assert until.tzinfo is None
    assert abs(until - (datetime.now() + timedelta(days=7))) < timedelta(seconds=60)

    record = state["records"][0]
    assert record["permanent"] is False
    assert record["expires_at"] == until
    assert record["started_at"].tzinfo is None

    body = response.json()["data"]
    assert body["banned"] is True
    assert body["permanent"] is False
    assert body["ban_until"] == until.isoformat()


def test_ban_without_duration_stays_permanent(users_api) -> None:
    """不传 ``duration_days`` = 永久：``ban_until IS NULL``、流水 ``permanent=1``。"""
    http, state = users_api

    response = http.put(f"{USERS_PREFIX}/{STUDENT_OID}/banned", json={"banned": True})

    assert response.status_code == 200
    assert state["bans"][0]["until"] is None
    assert state["records"][0]["permanent"] is True
    assert state["records"][0]["expires_at"] is None

    body = response.json()["data"]
    assert body["permanent"] is True
    assert body["ban_until"] is None


@pytest.mark.parametrize("days", [1, 36500])
def test_ban_duration_boundaries_are_accepted(users_api, days) -> None:
    http, state = users_api

    response = http.put(
        f"{USERS_PREFIX}/{STUDENT_OID}/banned",
        json={"banned": True, "duration_days": days},
    )

    assert response.status_code == 200
    assert state["bans"][0]["until"] is not None


@pytest.mark.parametrize("days", [0, 36501])
def test_ban_duration_boundaries_are_rejected(users_api, days) -> None:
    http, state = users_api

    response = http.put(
        f"{USERS_PREFIX}/{STUDENT_OID}/banned",
        json={"banned": True, "duration_days": days},
    )

    assert response.status_code == 422
    assert state["bans"] == []
    assert state["records"] == []


def test_unban_rejects_duration_days(users_api) -> None:
    """解封带时长是语义错误（解封没有到期时间），不许静默忽略。"""
    http, state = users_api

    response = http.put(
        f"{USERS_PREFIX}/{STUDENT_OID}/banned",
        json={"banned": False, "duration_days": 7},
    )

    assert response.status_code == 422
    assert state["clears"] == []
    assert state["bans"] == []


def test_cannot_ban_another_admin_even_with_duration(users_api) -> None:
    """加了时长不能绕过 P0-1 的 admin 目标侧保护。"""
    http, state = users_api

    response = http.put(
        f"{USERS_PREFIX}/{PEER_ADMIN}/banned",
        json={"banned": True, "duration_days": 7},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "不能封禁管理员账号"
    assert state["bans"] == []
    assert state["records"] == []

