"""登录凭据的一次性消费（服务端单侧防重放）。

``{password, time}`` 载荷里没有 nonce：同一份 RSA 密文在 ±60 秒窗口内可以重复
提交；verify 拿到的密文也能当 login 用换 10 天会话。这里钉住：解密成功后按
**原始密文摘要**消费一次，第二次进来即 401；不同密文（哪怕同一密码）互不影响；
verify → login 复用同一密文同样被拒。

全离线：真跑 ``util.cert`` 的 RSA/bcrypt/JWT，只把 users repo 与权限派生换成
内存假实现。
"""

from __future__ import annotations

import json
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from db import backends
from db import cache
from fake_redis import FakeRedis
from util import cert, credential_replay

OID = "64b7f0c2e1a3b4c5d6e7f901"
PASSWORD = "StrongPass1!"


def _credential(password: str = PASSWORD) -> str:
    return cert.rsa_encrypt(json.dumps({"password": password, "time": datetime.now().timestamp()}))


@pytest.fixture()
def account(monkeypatch):
    async def get_by_oid(oid):
        if oid != OID:
            return None
        return {
            "_id": OID,
            "id": "2023001",
            "name": "回归用户",
            "password": cert.hash_password(PASSWORD).decode(),
            "roles": ["student"],
            "class_id": None,
            "session_id": "v1",
        }

    async def permissions(_user):
        return ["student"]

    monkeypatch.setattr(backends, "get_users_repo", lambda *a, **k: SimpleNamespace(get_by_oid=get_by_oid))
    monkeypatch.setattr(cert, "get_user_permissions", permissions)


@pytest.mark.asyncio
async def test_same_credential_cannot_be_replayed(account):
    credential = _credential()

    await cert.validate_by_cert(OID, credential)

    with pytest.raises(HTTPException) as failure:
        await cert.validate_by_cert(OID, credential)
    assert failure.value.status_code == 401
    assert failure.value.detail == "登录凭证已失效，请重新登录"


@pytest.mark.asyncio
async def test_distinct_credentials_with_the_same_password_both_work(account):
    first = _credential()
    second = _credential()

    assert await cert.validate_by_cert(OID, first)
    assert await cert.validate_by_cert(OID, second)


@pytest.mark.asyncio
async def test_verify_credential_cannot_be_reused_for_login(account):
    credential = _credential()

    # verify 走临时令牌，login 走正式会话；同一份密文的第二次使用必须被拒。
    await cert.validate_by_cert(OID, credential, "short")
    with pytest.raises(HTTPException) as failure:
        await cert.validate_by_cert(OID, credential, "long")
    assert failure.value.status_code == 401


@pytest.mark.asyncio
async def test_shared_redis_marks_the_ciphertext_used_once(account):
    client = FakeRedis()
    cache._install_client_for_tests(client)
    try:
        credential = _credential()
        await cert.validate_by_cert(OID, credential)
        with pytest.raises(HTTPException) as failure:
            await cert.validate_by_cert(OID, credential)
        assert failure.value.status_code == 401
        assert credential_replay.digest(credential) in json.dumps(list(client.kv))
    finally:
        cache._reset_for_tests()


@pytest.mark.asyncio
async def test_replay_store_only_keeps_a_digest(account):
    credential = _credential()
    await cert.validate_by_cert(OID, credential)

    key = f"{credential_replay._KEY_PREFIX}{credential_replay.digest(credential)}"
    assert credential not in key
    assert PASSWORD not in key
    assert len(credential_replay.digest(credential)) == 64
