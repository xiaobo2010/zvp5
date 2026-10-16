"""Prove obsolete failed-login logging is gone, fully offline.

Contract under test: a failed ``POST /api/users/auth`` must return the same
response as before (403 ``用户名或密码错误``) but must **write no log row at
all** — the retired ``user_login_failed`` / ``auth_flood`` rows and their
process-local flood guard are gone (user ruling: record nothing on failure).

No database, no network, no fixtures: the users repo is a fake that finds no
one, and ``routers.users_router.ZVMSLog`` is swapped for a recorder. The
applogs repo is swapped for a canary that fails the test if anything tries to
persist through it, so a log write that bypasses the ``ZVMSLog`` name is still
caught.

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

# Every construction / insert_log on the swapped ZVMSLog is appended here; a
# failed login must leave it empty.
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
    ) -> None:
        self.url = url
        self.user = user
        self.data = data
        self.timestamp = timestamp
        self.type = type
        self.level = level
        self.xuehai_id = xuehai_id
        _LOG_CALLS.append(
            {"kind": "construct", "type": type, "level": level, "data": data}
        )

    def with_text(self, text: str, type: str = "", level: str = "") -> "_RecordingLog":
        self.data = text
        if type:
            self.type = type
        if level:
            self.level = level
        return self

    async def insert_log(self) -> None:
        _LOG_CALLS.append({"kind": "insert_log", "type": self.type, "data": self.data})


class _NoSuchUserRepo:
    """Users repo where every lookup misses."""

    async def find_by_business_id(self, business_id: str):
        return None


class _ForbiddenAppLogsRepo:
    """Canary: any persistence reaching the applogs repo fails the test."""

    async def insert(self, *args, **kwargs):
        raise AssertionError("a failed login must not write an applog row")


@pytest.fixture()
def client(monkeypatch) -> TestClient:
    _LOG_CALLS.clear()
    monkeypatch.setattr(users_router, "ZVMSLog", _RecordingLog)
    monkeypatch.setattr(
        backends, "get_users_repo", lambda *a, **k: _NoSuchUserRepo()
    )
    monkeypatch.setattr(
        backends, "get_applogs_repo", lambda *a, **k: _ForbiddenAppLogsRepo()
    )
    app = FastAPI()
    app.include_router(users_router.router, prefix=PREFIX)
    return TestClient(app)


def test_unknown_user_fails_403_and_writes_no_log(client: TestClient) -> None:
    response = client.post(
        f"{PREFIX}/auth",
        json={"id": UNKNOWN_BUSINESS_ID, "mode": "long", "credential": "whatever"},
    )
    # Response is byte-identical to the wrong-password path.
    assert response.status_code == 403
    assert response.json() == {"detail": "用户名或密码错误"}
    assert _LOG_CALLS == []


def test_wrong_password_fails_403_and_writes_no_log(
    client: TestClient, monkeypatch
) -> None:
    async def _reject(id: str, credential: str, mode: str):
        raise HTTPException(status_code=403, detail="Password incorrect")

    monkeypatch.setattr(users_router, "validate_by_cert", _reject)
    response = client.post(
        f"{PREFIX}/auth",
        json={"id": VALID_OID, "mode": "long", "credential": "wrong"},
    )
    assert response.status_code == 403
    assert response.json() == {"detail": "用户名或密码错误"}
    assert _LOG_CALLS == []
