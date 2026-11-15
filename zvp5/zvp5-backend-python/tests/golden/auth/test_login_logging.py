"""Prove failed-login evidence is written, sanitized and throttled, offline.

Contract under test (policy change: silent failures are no longer acceptable):

* a failed ``POST /api/users/auth`` still returns byte-identical 403
  ``用户名或密码错误``;
* it writes **one** ``login_fail`` row at ``level="info"`` whose text is a
  fixed summary (``password-mismatch``) — never the submitted credential;
  ``info``（而非 ``warning``）是有意的：风控计数只按 ``ltype`` + 时间窗取数，
  不看级别；而保留策略豁免所有 ``warning`` 行，取 ``warning`` 会让这类行永不
  过期（历史堆积的根因）。``info`` 让它们随 ``APP_LOGS_RETENTION_DAYS`` 清理，
  管理端列表默认也不显示该类型；
* repeated failures for the same oid inside the throttle window write nothing
  more (a log storm must not be the side effect of an attack);
* an account that is already banned writes ``banned`` instead, so the
  ``ban_evasion_retry`` rule and the ``login_failure_burst`` rule can share one
  ``ltype`` and still count different facts;
* the unknown-account path stays silent: there is no oid to attach evidence to.

No database, no network, no fixtures beyond fakes: the users repo is a fake that
finds no one, ``routers.users_router.ZVMSLog`` is swapped for a recorder, the
rule repo returns "no rule" (so ``risk_monitor`` never reaches the log table),
and the applogs repo is a canary that fails the test if anything tries to
persist through it — a log write that bypasses the ``ZVMSLog`` name is caught.

Why a throwaway ``FastAPI()`` instead of ``main.app``: importing ``main`` pulls
in APScheduler, socket.io and the ``tasks.compute_time`` graph, none of which
this router contract test needs. The ``/api/users`` prefix is copied from
``main.py``'s ``include_router(users_router.router, prefix="/api/users")`` so
the tested URL is the production one.

Offline compromise: importing ``routers.users_router`` reaches
``util.object_id``, which reads ``aes_key.txt`` from the current directory at
import time. Run pytest from the repository root (as the acceptance command
does); a run from elsewhere fails at collection, not in an assertion.

Run with pytest::

    pytest tests/golden/auth/test_login_logging.py -q
"""

from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import pytest

from db import backends
from routers import users_router

PREFIX = "/api/users"
# A 24-hex string: passes ``string_to_option_object_id``, so the route skips the
# business-id lookup and goes straight to credential validation.
VALID_OID = "64b7f0c2e1a3b4c5d6e7f801"
# Not a 24-hex id: forces the ``find_by_business_id`` branch (the "user not
# found" path).
UNKNOWN_BUSINESS_ID = "12345678"
# Never valid; every assertion below checks it is absent from the log text.
SECRET = "WrongPass1!"

_LOG_CALLS: list[dict] = []


class _RecordingLog:
    """Stand-in for ``typings.log.ZVMSLog`` that records instead of persisting."""

    def __init__(
        self,
        url: str = "",
        user: str = "",
        data: str = "",
        timestamp: float | None = None,
        type: str = "",
        level: str = "info",
        xuehai_id: str = "",
        client_ip: str = "",
        user_agent: str = "",
    ) -> None:
        self.url = url
        self.user = user
        self.data = data
        self.timestamp = timestamp
        self.type = type
        self.level = level
        self.xuehai_id = xuehai_id
        self.client_ip = client_ip
        self.user_agent = user_agent
        _LOG_CALLS.append(
            {
                "kind": "construct",
                "type": type,
                "level": level,
                "data": data,
                "user": user,
                "client_ip": client_ip,
            }
        )

    def with_text(self, text: str, type: str = "", level: str = "") -> "_RecordingLog":
        self.data = text
        if type:
            self.type = type
        if level:
            self.level = level
        return self

    async def insert_log(self) -> None:
        _LOG_CALLS.append(
            {
                "kind": "insert_log",
                "type": self.type,
                "level": self.level,
                "data": self.data,
                "user": self.user,
            }
        )


class _NoSuchUserRepo:
    """Users repo where every lookup misses."""

    async def find_by_business_id(self, business_id: str):
        return None

    async def get_by_oid(self, oid: str):
        return None


class _ForbiddenAppLogsRepo:
    """Canary: any persistence reaching the applogs repo fails the test."""

    async def insert(self, *args, **kwargs):
        raise AssertionError("login evidence must go through ZVMSLog")

    async def count_by_user_ltype(self, *args, **kwargs):
        raise AssertionError("no rule is seeded here: no counting should happen")


class _NoRuleRepo:
    """规则仓储：本文件只测登录失败证据，规则一律「不存在」。"""

    async def get_by_trigger(self, trigger: str):
        return None

    async def get_by_code(self, code: str):
        return None


@pytest.fixture()
def client(monkeypatch) -> TestClient:
    _LOG_CALLS.clear()
    users_router._login_failure_logged.clear()
    monkeypatch.setattr(users_router, "ZVMSLog", _RecordingLog)
    monkeypatch.setattr(backends, "get_users_repo", lambda *a, **k: _NoSuchUserRepo())
    monkeypatch.setattr(
        backends, "get_applogs_repo", lambda *a, **k: _ForbiddenAppLogsRepo()
    )
    monkeypatch.setattr(backends, "get_ban_rules_repo", lambda *a, **k: _NoRuleRepo())
    app = FastAPI()
    app.include_router(users_router.router, prefix=PREFIX)
    return TestClient(app)


def _failed_inserts() -> list[dict]:
    return [
        call
        for call in _LOG_CALLS
        if call["kind"] == "insert_log" and call["type"] == "login_fail"
    ]


def _reject_password(monkeypatch) -> None:
    async def _reject(id: str, credential: str, mode: str):
        raise HTTPException(status_code=403, detail="Password incorrect")

    monkeypatch.setattr(users_router, "validate_by_cert", _reject)


def test_unknown_user_fails_403_and_writes_no_log(client: TestClient) -> None:
    response = client.post(
        f"{PREFIX}/auth",
        json={"id": UNKNOWN_BUSINESS_ID, "mode": "long", "credential": SECRET},
    )
    # Response is byte-identical to the wrong-password path.
    assert response.status_code == 403
    assert response.json() == {"detail": "用户名或密码错误"}
    # 没有 oid 就没有可挂证据的主体。
    assert _LOG_CALLS == []


def test_wrong_password_fails_403_and_writes_one_sanitized_log(
    client: TestClient, monkeypatch
) -> None:
    _reject_password(monkeypatch)
    response = client.post(
        f"{PREFIX}/auth",
        json={"id": VALID_OID, "mode": "long", "credential": SECRET},
    )
    assert response.status_code == 403
    assert response.json() == {"detail": "用户名或密码错误"}

    inserts = _failed_inserts()
    assert len(inserts) == 1
    # info 级 = 可被保留策略清理；风控按 ltype+时间窗计数，不看级别。
    assert inserts[0]["level"] == "info"
    assert inserts[0]["user"] == VALID_OID
    # 摘要是固定串：规则靠它区分「密码错」与「封禁期重试」。
    assert "password-mismatch" in inserts[0]["data"]
    # 凭据原文、长度、哈希都不得出现。
    assert SECRET not in inserts[0]["data"]
    assert str(len(SECRET)) not in inserts[0]["data"]


def test_repeated_failures_are_throttled_to_one_log(
    client: TestClient, monkeypatch
) -> None:
    _reject_password(monkeypatch)
    for _ in range(5):
        response = client.post(
            f"{PREFIX}/auth",
            json={"id": VALID_OID, "mode": "long", "credential": SECRET},
        )
        assert response.status_code == 403

    assert len(_failed_inserts()) == 1


def test_banned_account_logs_banned_marker(
    client: TestClient, monkeypatch
) -> None:
    """封禁期的尝试走同一 ltype，但摘要不同——两条规则据此分工。"""

    async def _banned(id: str, credential: str, mode: str):
        raise HTTPException(status_code=403, detail="USER_BANNED")

    monkeypatch.setattr(users_router, "validate_by_cert", _banned)
    response = client.post(
        f"{PREFIX}/auth",
        json={"id": VALID_OID, "mode": "long", "credential": SECRET},
    )
    assert response.status_code == 403
    assert response.json() == {"detail": "USER_BANNED"}

    inserts = _failed_inserts()
    assert len(inserts) == 1
    assert "banned" in inserts[0]["data"]
    assert SECRET not in inserts[0]["data"]


def test_throttle_is_per_oid_not_global(client: TestClient, monkeypatch) -> None:
    """节流按账号：一个人被节流不该让另一个人的失败证据消失。"""
    _reject_password(monkeypatch)
    other_oid = "64b7f0c2e1a3b4c5d6e7f802"

    for oid in (VALID_OID, VALID_OID, other_oid):
        client.post(
            f"{PREFIX}/auth",
            json={"id": oid, "mode": "long", "credential": SECRET},
        )

    assert [row["user"] for row in _failed_inserts()] == [VALID_OID, other_oid]


def test_risk_hook_failure_does_not_break_the_403(
    client: TestClient, monkeypatch
) -> None:
    """规则引擎炸了也只能是「少一次判定」，不能把登录失败变成 500。"""
    _reject_password(monkeypatch)

    async def _boom(*args, **kwargs):
        raise RuntimeError("risk engine down")

    monkeypatch.setattr(users_router.risk_monitor, "evaluate", _boom)
    response = client.post(
        f"{PREFIX}/auth",
        json={"id": VALID_OID, "mode": "long", "credential": SECRET},
    )
    assert response.status_code == 403
    assert response.json() == {"detail": "用户名或密码错误"}


def test_log_repo_failure_does_not_break_the_403(
    client: TestClient, monkeypatch
) -> None:
    """审计写失败同样只记 warning：403 是主行为，日志是附带证据。"""
    _reject_password(monkeypatch)

    class _Exploding:
        def __init__(self, *a, **k):
            raise RuntimeError("log repo down")

    monkeypatch.setattr(users_router, "ZVMSLog", _Exploding)
    response = client.post(
        f"{PREFIX}/auth",
        json={"id": VALID_OID, "mode": "long", "credential": SECRET},
    )
    assert response.status_code == 403
    assert response.json() == {"detail": "用户名或密码错误"}
