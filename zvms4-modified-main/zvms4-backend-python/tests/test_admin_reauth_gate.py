"""管理面写操作的「密码再认证」强制化：每个受影响端点两例（缺令牌 / 有效令牌）。

契约：令牌来自 ``POST /api/users/auth``（``purpose='verify'``）的 5 分钟短令牌，
放在请求 body 的 ``verification_token``（``DELETE`` 也走 body）。缺失/无效统一
403，与 ``users_v3_router`` / ``attachments_router`` 的既有行为一致。

本文件用真实令牌（``util.cert.jwt_encode`` 现签）+ 内存用户仓储，端点是线上
router 本体，业务层仓储/服务全部打桩——验证的是闸门位置，不是业务语义。
"""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from db import backends
from routers import (
    ai_api_keys_router,
    ai_router,
    ban_router,
    changelog_router,
    notifications_router,
    system_settings_router,
)
from services import settings_runtime
from services.ai import api_keys, credentials
from typings.log import inject_log
from util.cert import jwt_encode
from util.object_id import get_current_user

ADMIN = "000000000000000000000001"
OTHER = "000000000000000000000002"
OID = "0000000000000000000000bb"

USERS = {
    ADMIN: {"_id": ADMIN, "id": "admin", "name": "管理员", "roles": ["admin"], "session_id": ""},
    OTHER: {"_id": OTHER, "id": "other", "name": "别人", "roles": ["admin"], "session_id": ""},
}


def _token(oid: str = ADMIN, *, purpose: str = "verify", minutes: int = 5) -> str:
    return jwt_encode(
        oid,
        [],
        [],
        "short",
        session_id="",
        purpose=purpose,
        expires_in=timedelta(minutes=minutes),
    )


class StubLog:
    def with_text(self, *_args, **_kwargs):
        return self

    async def insert_log(self, *_args, **_kwargs):
        return None


class FakeUsersRepo:
    async def get_by_oid(self, oid):
        return USERS.get(str(oid))


def _notice() -> dict:
    return {
        "_id": OID,
        "title": "标题",
        "content": "正文",
        "version": 2,
        "active": True,
        "author_oid": ADMIN,
        "author_name": "管理员",
        "created_at": None,
        "updated_at": None,
    }


def _rule() -> dict:
    return {
        "_id": OID,
        "code": "my_rule",
        "name": "规则",
        "trigger": "platform_misuse",
        "enabled": True,
        "builtin": False,
        "config": "{}",
        "penalties": "[1]",
        "created_at": None,
        "updated_at": None,
    }


def _changelog() -> dict:
    return {"_id": OID, "version": "v1", "date": "2026-01-01", "items": ["条目"]}


@pytest.fixture
def env(monkeypatch):
    notifications = SimpleNamespace(
        create=lambda **_kwargs: _async((_notice(), 2)),
        update=lambda *_args, **_kwargs: _async((_notice(), 3)),
        delete=lambda *_args, **_kwargs: _async((_notice(), 4)),
        rollback=lambda *_args, **_kwargs: _async(
            (_notice(), 5, {"content_hash": "x" * 64})
        ),
    )
    created = {"done": False}

    async def _get_rule(code):
        if code == "my_rule":
            return _rule()
        return _rule() if created["done"] and code == "new_rule" else None

    async def _insert(*_args, **_kwargs):
        created["done"] = True

    rules = SimpleNamespace(
        get_by_code=_get_rule,
        list_all=lambda: _async([_rule()]),
        get_by_trigger=lambda trigger: _async(None),
        insert=_insert,
        update_by_code=lambda *_args, **_kwargs: _async(1),
        delete_by_code=lambda code: _async(1),
    )
    devices = SimpleNamespace(
        upsert=lambda *_args, **_kwargs: _async(None),
        delete=lambda xuehai_id: _async(1),
    )
    counters = SimpleNamespace(
        get_counts=lambda ids: _async({}),
        delete=lambda xuehai_id: _async(1),
    )
    changelogs = SimpleNamespace(
        create=lambda *_args, **_kwargs: _async(OID),
        get_by_oid=lambda oid: _async(_changelog()),
        update=lambda *_args, **_kwargs: _async(None),
        delete=lambda oid: _async(None),
    )

    monkeypatch.setattr(backends, "get_users_repo", lambda *a, **k: FakeUsersRepo())
    monkeypatch.setattr(backends, "get_notifications_repo", lambda *a, **k: notifications)
    monkeypatch.setattr(backends, "get_ban_rules_repo", lambda *a, **k: rules)
    monkeypatch.setattr(backends, "get_device_bans_repo", lambda *a, **k: devices)
    monkeypatch.setattr(backends, "get_device_ban_counters_repo", lambda *a, **k: counters)
    monkeypatch.setattr(backends, "get_changelog_repo", lambda *a, **k: changelogs)

    async def _name(_oid):
        return "管理员"

    for module in (ban_router, notifications_router, changelog_router, system_settings_router):
        monkeypatch.setattr(module, "get_user_display_name", _name)

    monkeypatch.setattr(
        changelog_router, "sync_changelog_notification", lambda entry: _async(True)
    )
    monkeypatch.setattr(
        changelog_router, "deactivate_changelog_notification", lambda oid: _async(True)
    )

    async def _create_entry(**_kwargs):
        return {"oid": OID, "scope": "shared", "name": "k"}

    async def _update_entry(**_kwargs):
        return {"oid": OID, "scope": "shared", "name": "k"}

    async def _delete_entry(**_kwargs):
        return None

    monkeypatch.setattr(credentials, "create_entry", _create_entry)
    monkeypatch.setattr(credentials, "update_entry", _update_entry)
    monkeypatch.setattr(credentials, "delete_entry", _delete_entry)

    async def _key_view(**_kwargs):
        return {"oid": OID, "name": "ci", "key_hint": "zvms_***"}

    monkeypatch.setattr(api_keys, "list_for", lambda *a, **k: _async([]))
    monkeypatch.setattr(api_keys, "create", lambda *a, **k: _key_view())
    monkeypatch.setattr(api_keys, "update", lambda *a, **k: _key_view())
    monkeypatch.setattr(api_keys, "delete", lambda *a, **k: _async(None))

    monkeypatch.setattr(system_settings_router, "_require_system_admin", lambda user: _async(user))
    monkeypatch.setattr(settings_runtime, "get_sync", lambda key: "before")
    monkeypatch.setattr(
        settings_runtime, "set_many", lambda updates, actor_oid=None: _async([])
    )

    app = FastAPI()
    app.include_router(ai_router.router, prefix="/api/ai")
    app.include_router(ai_api_keys_router.router, prefix="/api/ai/api-keys")
    app.include_router(ban_router.router, prefix="/api/v3/ban")
    app.include_router(notifications_router.router, prefix="/api/v3/notifications")
    app.include_router(changelog_router.router, prefix="/api/changelog")
    app.include_router(system_settings_router.router, prefix="/api/system")
    app.dependency_overrides[get_current_user] = lambda: {
        "id": ADMIN,
        "per": ["admin"],
        "_id": ADMIN,
    }
    app.dependency_overrides[inject_log] = lambda: StubLog()
    return TestClient(app)


def _async(value):
    async def _run():
        return value

    return _run()


CREDENTIAL = {
    "scope": "shared",
    "name": "ci",
    "base_url": "https://api.example.com/v1",
    "models": ["m"],
    "api_key": "sk-abcdefgh",
}
NOTICE = {"title": "标题", "content": "正文"}
SETTINGS = {"items": [{"key": "ops.max_query_count", "value": 200}]}


ENDPOINTS = [
    ("ai_credential_create", "post", "/api/ai/keys", CREDENTIAL, 201),
    ("ai_credential_update", "patch", f"/api/ai/keys/{OID}", {"enabled": False}, 200),
    ("ai_credential_delete", "delete", f"/api/ai/keys/{OID}", None, 204),
    ("ai_api_key_create", "post", "/api/ai/api-keys", {"name": "ci"}, 201),
    ("ai_api_key_update", "patch", f"/api/ai/api-keys/{OID}", {"enabled": False}, 200),
    ("ai_api_key_delete", "delete", f"/api/ai/api-keys/{OID}", None, 204),
    (
        "ban_rule_create",
        "post",
        "/api/v3/ban/rules",
        {
            "code": "new_rule",
            "name": "规则",
            "trigger": "platform_misuse",
            "penalties": [1, 7],
        },
        201,
    ),
    ("ban_rule_update", "put", "/api/v3/ban/rules/my_rule", {"enabled": False}, 200),
    ("ban_rule_delete", "delete", "/api/v3/ban/rules/my_rule", None, 204),
    ("ban_device_ban", "post", "/api/v3/ban/devices", {"xuehai_id": "1234567890"}, 200),
    ("ban_device_unban", "delete", "/api/v3/ban/devices/1234567890", None, 204),
    (
        "ban_device_reset_gradient",
        "post",
        "/api/v3/ban/devices/1234567890/reset-gradient",
        None,
        204,
    ),
    ("notification_create", "post", "/api/v3/notifications", NOTICE, 201),
    ("notification_update", "put", f"/api/v3/notifications/{OID}", {"title": "改"}, 200),
    ("notification_delete", "delete", f"/api/v3/notifications/{OID}", None, 204),
    (
        "notification_rollback",
        "post",
        f"/api/v3/notifications/{OID}/rollback",
        {"version": 1},
        200,
    ),
    (
        "changelog_create",
        "post",
        "/api/changelog",
        {"version": "v2", "date": "2026-02-01", "items": ["条目"]},
        201,
    ),
    (
        "changelog_update",
        "put",
        f"/api/changelog/{OID}",
        {"version": "v1", "date": "2026-01-01", "items": ["条目"]},
        204,
    ),
    ("changelog_delete", "delete", f"/api/changelog/{OID}", None, 204),
    ("system_settings_update", "put", "/api/system/settings", SETTINGS, 200),
]


def _call(client, method: str, path: str, body):
    return client.request(method.upper(), path, json=body)


@pytest.mark.parametrize("name,method,path,body,ok_status", ENDPOINTS)
def test_missing_token_is_rejected(env, name, method, path, body, ok_status):
    response = _call(env, method, path, body)
    assert response.status_code == 403, (name, response.text)
    assert response.json()["detail"] == "需要密码验证后才能继续" or (
        response.json()["detail"] == "AI_PASSWORD_VERIFICATION_REQUIRED"
    )


@pytest.mark.parametrize("name,method,path,body,ok_status", ENDPOINTS)
def test_valid_token_passes(env, name, method, path, body, ok_status):
    payload = {**(body or {}), "verification_token": _token()}
    response = _call(env, method, path, payload)
    assert response.status_code == ok_status, (name, response.text)


@pytest.mark.parametrize("name,method,path,body,ok_status", ENDPOINTS)
def test_foreign_token_is_rejected(env, name, method, path, body, ok_status):
    payload = {**(body or {}), "verification_token": _token(OTHER)}
    assert _call(env, method, path, payload).status_code == 403


@pytest.mark.parametrize(
    "name,method,path,body,ok_status",
    [
        ("ai_credential_delete", "delete", f"/api/ai/keys/{OID}", None, 204),
        ("notification_create", "post", "/api/v3/notifications", NOTICE, 201),
        ("system_settings_update", "put", "/api/system/settings", SETTINGS, 200),
    ],
)
def test_expired_or_wrong_purpose_token_is_rejected(
    env, name, method, path, body, ok_status
):
    expired = {**(body or {}), "verification_token": _token(minutes=-1)}
    assert _call(env, method, path, expired).status_code == 403
    login_scope = {**(body or {}), "verification_token": _token(purpose="login")}
    assert _call(env, method, path, login_scope).status_code == 403


def test_token_does_not_leak_into_service_fields(env, monkeypatch):
    seen: list[dict] = []

    async def _update_entry(oid, actor, fields):
        seen.append(fields)
        return {"oid": OID, "scope": "shared", "name": "k"}

    monkeypatch.setattr(credentials, "update_entry", _update_entry)
    response = env.patch(
        f"/api/ai/keys/{OID}",
        json={"enabled": False, "verification_token": _token()},
    )
    assert response.status_code == 200
    assert seen == [{"enabled": False}]


def test_read_endpoints_need_no_token(env):
    assert env.get("/api/ai/api-keys").status_code == 200
    assert env.get("/api/v3/ban/rules").status_code == 200
