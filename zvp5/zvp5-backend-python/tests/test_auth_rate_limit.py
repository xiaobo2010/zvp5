"""登录端点频控：按账号 5 次/分钟 + 按 IP 60 次/分钟（Redis 共享，进程内回退）。

钉住的契约：

* 同账号第 6 次尝试在窗口内被挡下，返回 429 + ``AUTH_RATE_LIMITED``；
* 文案只有一句话，**不含**任何阈值/窗口数字（否则等于把爆破预算告诉攻击者）；
* IP 档明显宽于账号档：校园 NAT 下整班同学共用一个出口 IP，账号轮换也拦得住；
* 两个维度互不污染：换账号换个 IP 照常走到底，同一 IP 的其他账号不受累。

离线：users repo 一律查不到人，凭据校验直接判错，日志与风控全部打桩。
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from db import backends
from db import cache
from fake_redis import FakeRedis
from routers import users_router
from util import login_throttle

PREFIX = "/api/users"
OID = "64b7f0c2e1a3b4c5d6e7f801"
IP_HEADER = "X-Forwarded-For"


class _NoSuchUserRepo:
    async def find_by_business_id(self, business_id: str):
        return None

    async def get_by_oid(self, oid: str):
        return None


class _NoRuleRepo:
    async def get_by_trigger(self, trigger: str):
        return None

    async def get_by_code(self, code: str):
        return None


class _NoopLog:
    def __init__(self, *args, **kwargs) -> None:
        pass

    def with_text(self, text: str, type: str = "", level: str = "") -> "_NoopLog":
        return self

    async def insert_log(self) -> None:
        return None


@pytest.fixture()
def client(monkeypatch) -> TestClient:
    async def _reject(id: str, credential: str, mode: str):
        raise HTTPException(status_code=403, detail="Password incorrect")

    async def _no_risk(*args, **kwargs):
        return None

    monkeypatch.setattr(users_router, "ZVMSLog", _NoopLog)
    monkeypatch.setattr(users_router, "validate_by_cert", _reject)
    monkeypatch.setattr(users_router.risk_monitor, "evaluate", _no_risk)
    monkeypatch.setattr(backends, "get_users_repo", lambda *a, **k: _NoSuchUserRepo())
    monkeypatch.setattr(backends, "get_ban_rules_repo", lambda *a, **k: _NoRuleRepo())
    app = FastAPI()
    app.include_router(users_router.router, prefix=PREFIX)
    return TestClient(app)


def _auth(client: TestClient, ident: str, ip: str = "10.0.0.1"):
    return client.post(
        f"{PREFIX}/auth",
        json={"id": ident, "credential": "whatever", "mode": "long"},
        headers={IP_HEADER: ip},
    )


@pytest.fixture()
def fake_redis():
    client = FakeRedis()
    cache._install_client_for_tests(client)
    yield client
    cache._reset_for_tests()


def test_account_window_blocks_one_more_attempt(client: TestClient) -> None:
    for _ in range(login_throttle.ACCOUNT_LIMIT):
        assert _auth(client, OID).status_code == 403

    blocked = _auth(client, OID)
    assert blocked.status_code == 429
    body = blocked.json()
    assert body["detail"]["code"] == "AUTH_RATE_LIMITED"
    assert "Retry-After" in blocked.headers
    assert 1 <= int(blocked.headers["Retry-After"]) <= int(login_throttle.WINDOW_SECONDS)


def test_blocked_message_never_leaks_the_threshold(client: TestClient) -> None:
    for _ in range(login_throttle.ACCOUNT_LIMIT):
        _auth(client, OID)
    message = _auth(client, OID).json()["detail"]["message"]

    assert message == login_throttle.MESSAGE
    assert str(login_throttle.ACCOUNT_LIMIT) not in message
    assert str(login_throttle.IP_LIMIT) not in message
    assert str(int(login_throttle.WINDOW_SECONDS)) not in message


def test_ip_window_is_wide_enough_for_a_shared_campus_nat(client: TestClient) -> None:
    assert login_throttle.IP_LIMIT > login_throttle.ACCOUNT_LIMIT * 5

    for index in range(login_throttle.IP_LIMIT):
        response = _auth(client, f"2024{index:04d}", ip="10.0.0.9")
        assert response.status_code == 403, response.text

    blocked = _auth(client, "20249999", ip="10.0.0.9")
    assert blocked.status_code == 429
    assert blocked.json()["detail"]["code"] == "AUTH_RATE_LIMITED"


def test_other_ip_is_not_affected_by_one_noisy_ip(client: TestClient) -> None:
    for index in range(login_throttle.IP_LIMIT + 1):
        _auth(client, f"2024{index:04d}", ip="10.0.0.9")

    assert _auth(client, "20240001", ip="10.0.1.9").status_code == 403


def test_other_account_is_not_affected_by_one_noisy_account(client: TestClient) -> None:
    for _ in range(login_throttle.ACCOUNT_LIMIT + 1):
        _auth(client, OID, ip="10.0.0.2")

    assert _auth(client, "64b7f0c2e1a3b4c5d6e7f802", ip="10.0.0.3").status_code == 403


@pytest.mark.asyncio
async def test_shared_window_is_used_when_redis_is_configured(fake_redis):
    for _ in range(login_throttle.ACCOUNT_LIMIT):
        await login_throttle.check(OID, "10.0.0.1")

    with pytest.raises(login_throttle.LoginRateLimited) as exc:
        await login_throttle.check(OID, "10.0.0.1")
    assert exc.value.retry_after >= 1

    # 两档都写进了共享窗口（账号档 + IP 档），被拒的那次不计数。
    account_key = f"zvms:state:win:login:acct:{OID}"
    ip_key = "zvms:state:win:login:ip:10.0.0.1"
    assert len(fake_redis.zsets[account_key]) == login_throttle.ACCOUNT_LIMIT
    assert ip_key in fake_redis.zsets
    # 共享状态生效时不双写进程内窗口。
    assert login_throttle._buckets == {}


@pytest.mark.asyncio
async def test_account_key_is_case_insensitive(fake_redis):
    for _ in range(login_throttle.ACCOUNT_LIMIT):
        await login_throttle.check(OID.lower(), "10.0.0.1")

    with pytest.raises(login_throttle.LoginRateLimited):
        await login_throttle.check(OID.upper(), "10.0.0.1")


@pytest.mark.asyncio
async def test_falls_back_to_local_window_when_redis_fails(fake_redis):
    fake_redis.fail = True

    for _ in range(login_throttle.ACCOUNT_LIMIT):
        await login_throttle.check(OID, "10.0.0.1")
    with pytest.raises(login_throttle.LoginRateLimited):
        await login_throttle.check(OID, "10.0.0.1")

    assert fake_redis.zsets == {}
    assert len(login_throttle._buckets[("account", OID.lower())]) == login_throttle.ACCOUNT_LIMIT
