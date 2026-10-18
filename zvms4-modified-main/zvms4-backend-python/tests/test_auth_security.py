import json
import time
from types import SimpleNamespace

import pytest
from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient

from db import backends
from db.repos.oracle.users import _set_banned_stmt, _set_password_stmt, _set_roles_stmt
from util import cert, object_id

OID = "000000000000000000000001"


@pytest.fixture
def account(monkeypatch):
    user = {"_id": OID, "roles": ["student"], "password": None, "session_id": "v1"}

    async def get_by_oid(oid):
        return user if oid == OID else None

    monkeypatch.setattr(backends, "get_users_repo", lambda: SimpleNamespace(get_by_oid=get_by_oid))
    return user


def token(kind="long", **kwargs):
    return cert.jwt_encode(OID, [], ["admin"], kind, session_id="v1", **kwargs)


@pytest.mark.asyncio
async def test_auth_uses_current_roles_and_rejects_revoked_and_banned(account):
    signed = token()
    assert (await object_id.get_current_user(signed))["per"] == ["student"]
    account["session_id"] = "v2"
    with pytest.raises(HTTPException) as failure:
        await object_id.get_current_user(signed)
    assert failure.value.status_code == 401
    account["session_id"] = "v1"
    account["banned"] = True
    assert await object_id.optional_current_user(signed) is None


@pytest.mark.asyncio
async def test_banned_account_is_403_user_banned_not_401(account):
    """封禁必须与「凭据过期/被撤销」可分辨。

    封禁会把 session_id 轮换掉，若这里统一返回 401，前端只能当成登录过期并
    立即退到登录页，用户永远看不到封禁警告。故封禁走 403 + 稳定标记
    ``USER_BANNED``，且必须在 sid 校验之前命中（此处故意同时把 sid 改成 v2
    模拟真实封禁现场）。
    """
    signed = token()
    account["session_id"] = "v2"
    account["banned"] = True

    with pytest.raises(HTTPException) as failure:
        await object_id.get_current_user(signed)
    assert failure.value.status_code == 403
    assert failure.value.detail == "USER_BANNED"

    # exception=False 的探测路径必须保持「不抛错、只返回 None」，
    # 否则维护模式中间件 is_admin_token 会炸。
    assert await object_id.optional_current_user(signed) is None
    assert await object_id.is_admin_token(f"Bearer {signed}") is False


def test_query_parameters_cannot_bypass_access_scope(account):
    app = FastAPI()

    @app.get("/")
    def endpoint(user=Depends(object_id.get_current_user)):
        return user

    response = TestClient(app).get("/?scope=any&exception=false", headers={"Authorization": f"Bearer {token('short')}"})
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_initial_password_token_only_allows_self_password_flow(account):
    signed = token("short", purpose="password_change")
    with pytest.raises(HTTPException):
        await object_id.compulsory_temporary_token(signed)
    assert (await object_id.password_change_token(signed))["purpose"] == "password_change"
    assert (await object_id.authenticated_log_user(signed))["id"] == OID


@pytest.mark.asyncio
async def test_temporary_verification_works_for_log_but_not_business(account):
    signed = token("short")
    assert (await object_id.compulsory_temporary_token(signed))["id"] == OID
    assert (await object_id.authenticated_log_user(signed))["id"] == OID
    with pytest.raises(HTTPException):
        await object_id.get_current_user(signed)


@pytest.mark.asyncio
async def test_current_user_carries_the_class_id_for_downstream_checks(account):
    """认证结果带上 class_id（附加字段，旧键不动）。

    这是纯附加：下游需要班级的权限校验（如
    ``validate_read_group_permission`` 的团支书分支）可以直接复用认证时刚读到的
    那份文档，不必再读一次同一个用户。
    """
    account["class_id"] = "000000000000000000000002"
    user = await object_id.get_current_user(token())
    assert user["id"] == OID
    assert user["per"] == ["student"]
    assert user["class_id"] == "000000000000000000000002"


@pytest.mark.asyncio
async def test_current_user_reports_a_missing_class_as_none_not_a_missing_key(
    account,
):
    # 键必须始终存在：None 表示「本人没有班级」，与「字典根本没带这个键」可
    # 区分——后者是历史调用形态，会退回再读一次库。
    user = await object_id.get_current_user(token())
    assert "class_id" in user
    assert user["class_id"] is None


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf"), True, None, "oops"])
def test_invalid_timestamps_fail(value):
    with pytest.raises(HTTPException):
        cert._credential_timestamp(value)


def test_seconds_and_javascript_milliseconds_both_work():
    now = time.time()
    assert cert._credential_timestamp(now) == now
    assert abs(cert._credential_timestamp(now * 1000) - now) < .001
    with pytest.raises(HTTPException):
        cert._credential_timestamp(now + 3600)


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", ["not hex", cert.rsa_encrypt("[]"), cert.rsa_encrypt('{"password":null}')])
async def test_malformed_credentials_are_client_errors(raw):
    with pytest.raises(HTTPException) as failure:
        await cert.validate_by_cert(OID, raw)
    assert failure.value.status_code == 400


def test_overlong_bcrypt_input_is_failed_authentication():
    assert not cert.check_password("a" * 73, cert.hash_password("StrongPwd1!").decode())


def test_sensitive_updates_rotate_session_in_same_statement():
    for statement in (_set_password_stmt(OID, "hash"), _set_roles_stmt(OID, ["admin"]), _set_banned_stmt(OID, 1)):
        assert statement.compile().params["session_id"]
