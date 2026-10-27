"""高危操作的「密码再认证」闸（``util.object_id.require_password_verification``）。

这条闸的契约只有一句话：**必须是本人**用密码换来的 ``purpose=verify`` 短令牌。
本文件钉三件事：

* 函数本身：缺令牌 / 他人令牌 / 凭证过期 / 伪造串 / 不是 ``verify`` 用途，统一
  **403**（401 会被前端当成登录过期并清会话，密码框里打错一次就把人踢出去）；
  本人有效令牌放行；
* 每个接入点：拒绝发生在业务写入之前——仓储的写调用一条都不能发生；
* 「首次绑定主设备」不需要令牌：那时没有可迁移的入口，且只能在目标设备上发起。

令牌用 ``util.cert.jwt_encode`` 现签，与线上 ``POST /api/users/auth`` 同一条链路；
用户仓储是内存桩，不连 Oracle、不出网。
"""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from db import backends
from routers import ai_agent_router, attachments_router, users_v3_router
from typings.log import inject_log
from util.cert import jwt_encode
from util.object_id import (
    PASSWORD_VERIFICATION_REQUIRED,
    get_current_user,
    require_password_verification,
)

CURRENT = "000000000000000000000001"
OTHER = "000000000000000000000002"
TARGET = "000000000000000000000003"
PRIMARY = "1234567890"
# 附件写端点的目标行：存在且未裁决，删除/裁决都能走到密码闸。
FILE = "64b7f0c2e1a3b4c5d6e7fa10"

USERS = {
    CURRENT: {
        "_id": CURRENT,
        "id": "2026001",
        "name": "操作者",
        "roles": ["admin"],
        "session_id": "",
        "primary_xuehai_id": PRIMARY,
    },
    OTHER: {
        "_id": OTHER,
        "id": "2026002",
        "name": "别人",
        "roles": ["admin"],
        "session_id": "",
    },
    TARGET: {
        "_id": TARGET,
        "id": "2026003",
        "name": "目标",
        "roles": ["student"],
        "session_id": "",
        "primary_xuehai_id": "777",
    },
}


def _token(
    oid: str,
    *,
    purpose: str = "verify",
    minutes: int = 5,
) -> str:
    """密码复核签发的 5 分钟短令牌（``scope=temporary_token``）。"""
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
    def with_text(self, *args, **kwargs):
        return self

    async def insert_log(self, session=None):
        return None


@pytest.fixture
def world(monkeypatch):
    """用户 / 绑定仓储与日志全部内存化；``writes`` 记录所有业务写调用。"""
    writes: list[tuple] = []

    async def get_by_oid(oid):
        return USERS.get(str(oid))

    async def get_names(oids):
        return {oid: USERS[oid]["name"] for oid in oids if oid in USERS}

    async def set_primary_xuehai(oid, xuehai_id):
        writes.append(("set_primary", str(oid), xuehai_id))
        USERS[str(oid)]["primary_xuehai_id"] = xuehai_id

    async def clear_primary_xuehai(oid):
        writes.append(("clear_primary", str(oid)))
        USERS[str(oid)]["primary_xuehai_id"] = None

    async def list_for_user(oid):
        return [{"xuehai_id": "777", "status": "active"}]

    async def revoke(oid, xuehai_id):
        writes.append(("revoke", str(oid), xuehai_id))

    async def upsert(oid, xuehai_id, source, note):
        writes.append(("upsert", str(oid), xuehai_id, source, note))

    # 附件写端点：行必须存在，且任何一个写调用都要记进 writes（拒绝时它必须为空）。
    row = {"_id": FILE, "owner_oid": TARGET, "status": "stored", "purpose": "volunteer_order"}

    async def get_meta(oid):
        return dict(row) if str(oid) == FILE else None

    async def set_status(oid, status):
        writes.append(("file_set_status", str(oid), status))

    async def set_entity(oid, entity_type, entity_oid):
        writes.append(("file_set_entity", str(oid), entity_type, entity_oid))

    async def soft_delete(oid):
        writes.append(("file_soft_delete", str(oid)))

    async def add_event(file_oid, actor_oid, action, from_status, to_status, detail):
        writes.append(("file_event", str(file_oid), action))

    monkeypatch.setattr(
        backends,
        "get_files_repo",
        lambda *a, **k: SimpleNamespace(
            get_meta=get_meta,
            set_status=set_status,
            set_entity=set_entity,
            soft_delete=soft_delete,
            add_event=add_event,
        ),
        raising=False,
    )

    monkeypatch.setattr(
        backends,
        "get_users_repo",
        lambda *a, **k: SimpleNamespace(
            get_by_oid=get_by_oid,
            get_names=get_names,
            set_primary_xuehai=set_primary_xuehai,
            clear_primary_xuehai=clear_primary_xuehai,
        ),
        raising=False,
    )
    monkeypatch.setattr(
        backends,
        "get_bindings_repo",
        lambda *a, **k: SimpleNamespace(
            list_for_user=list_for_user, revoke=revoke, upsert=upsert
        ),
        raising=False,
    )

    app = FastAPI()
    app.include_router(users_v3_router.router, prefix="/api/v3/users")
    app.include_router(ai_agent_router.router, prefix="/api/ai/agent")
    app.include_router(attachments_router.router, prefix="/api/attachments")
    app.dependency_overrides[get_current_user] = lambda: {
        "id": CURRENT,
        "per": ["admin"],
    }
    app.dependency_overrides[inject_log] = lambda: StubLog()

    return SimpleNamespace(client=TestClient(app), app=app, writes=writes)


# ---------------------------------------------------------------------------
# 校验函数本身
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_valid_token_of_the_same_user_passes(world) -> None:
    verified = await require_password_verification(
        _token(CURRENT), {"id": CURRENT, "per": ["admin"]}
    )

    assert verified["id"] == CURRENT
    assert verified["purpose"] == "verify"


@pytest.mark.parametrize(
    ("label", "token"),
    [
        ("缺令牌", None),
        ("他人令牌", _token(OTHER)),
        ("过期令牌", _token(CURRENT, minutes=-1)),
        ("伪造串", "not-a-real-token"),
        ("登录态 access token", jwt_encode(CURRENT, [], ["admin"], "long", session_id="")),
        ("不是 verify 用途", _token(CURRENT, purpose="login")),
    ],
)
@pytest.mark.asyncio
async def test_rejections_are_403_never_401(world, label, token) -> None:
    """401 会被前端当登录过期并清会话；这里只能 403。"""
    with pytest.raises(HTTPException) as failure:
        await require_password_verification(token, {"id": CURRENT, "per": ["admin"]})

    assert failure.value.status_code == 403, label
    assert failure.value.detail == PASSWORD_VERIFICATION_REQUIRED


# ---------------------------------------------------------------------------
# 每个接入点：拒绝先于任何业务写入
# ---------------------------------------------------------------------------

HIGH_RISK_ENDPOINTS = [
    (
        "PUT",
        f"/api/v3/users/{TARGET}/bindings",
        {"xuehai_id": PRIMARY},
        {},
    ),
    (
        "DELETE",
        f"/api/v3/users/{TARGET}/bindings/777",
        {},
        {},
    ),
    (
        "DELETE",
        f"/api/v3/users/{TARGET}/primary-device",
        {},
        {},
    ),
    (
        "POST",
        f"/api/v3/users/{TARGET}/temp-password",
        {},
        {},
    ),
    (
        "DELETE",
        f"/api/v3/users/{TARGET}",
        {},
        {},
    ),
    (
        "PUT",
        "/api/v3/users/me/primary-device",
        {"xuehai_id": "555"},
        {"Xuehai-Id": PRIMARY},
    ),
    (
        "DELETE",
        "/api/v3/users/me/primary-device",
        {},
        {"Xuehai-Id": PRIMARY},
    ),
    (
        "PUT",
        "/api/ai/agent/moderation/unrestricted",
        {"enabled": True},
        {},
    ),
    (
        "PATCH",
        f"/api/attachments/{FILE}/status",
        {"status": "verified"},
        {},
    ),
    (
        "DELETE",
        f"/api/attachments/{FILE}",
        {},
        {},
    ),
    (
        "POST",
        f"/api/attachments/{FILE}/verify",
        {"decision": "verified"},
        {},
    ),
    (
        "POST",
        f"/api/attachments/{FILE}/bind",
        {"entity_type": "activity", "entity_oid": TARGET},
        {},
    ),
]


@pytest.mark.parametrize(("method", "path", "body", "headers"), HIGH_RISK_ENDPOINTS)
def test_endpoint_rejects_a_missing_token(world, method, path, body, headers) -> None:
    response = world.client.request(method, path, json=body or None, headers=headers)

    assert response.status_code == 403
    assert world.writes == []


@pytest.mark.parametrize(("method", "path", "body", "headers"), HIGH_RISK_ENDPOINTS)
def test_endpoint_rejects_another_users_token(world, method, path, body, headers) -> None:
    response = world.client.request(
        method,
        path,
        json={**body, "verification_token": _token(OTHER)},
        headers=headers,
    )

    assert response.status_code == 403
    assert world.writes == []


@pytest.mark.parametrize(("method", "path", "body", "headers"), HIGH_RISK_ENDPOINTS)
def test_endpoint_rejects_an_expired_token(world, method, path, body, headers) -> None:
    response = world.client.request(
        method,
        path,
        json={**body, "verification_token": _token(CURRENT, minutes=-1)},
        headers=headers,
    )

    assert response.status_code == 403
    assert world.writes == []


@pytest.mark.parametrize(("method", "path", "body", "headers"), HIGH_RISK_ENDPOINTS)
def test_endpoint_rejects_a_forged_token(world, method, path, body, headers) -> None:
    response = world.client.request(
        method,
        path,
        json={**body, "verification_token": "forged.token.value"},
        headers=headers,
    )

    assert response.status_code == 403
    assert world.writes == []


# ---------------------------------------------------------------------------
# 本人有效令牌：管理端绑定 / 解绑正常走通
# ---------------------------------------------------------------------------


def test_binding_endpoints_pass_with_the_operators_own_token(world) -> None:
    bound = world.client.put(
        f"/api/v3/users/{TARGET}/bindings",
        json={"xuehai_id": PRIMARY, "verification_token": _token(CURRENT)},
    )
    assert bound.status_code == 204
    assert ("upsert", TARGET, PRIMARY, "admin", None) in world.writes

    revoked = world.client.request(
        "DELETE",
        f"/api/v3/users/{TARGET}/bindings/777",
        json={"verification_token": _token(CURRENT)},
    )
    assert revoked.status_code == 204
    assert ("revoke", TARGET, "777") in world.writes


def test_first_binding_needs_no_token(world) -> None:
    """没有 primary 时不存在「把入口迁走」的风险，首次绑定免验。"""
    USERS[CURRENT]["primary_xuehai_id"] = None

    response = world.client.put(
        "/api/v3/users/me/primary-device",
        json={"xuehai_id": "555"},
        headers={"Xuehai-Id": "555"},
    )

    assert response.status_code == 204
    assert ("set_primary", CURRENT, "555") in world.writes
