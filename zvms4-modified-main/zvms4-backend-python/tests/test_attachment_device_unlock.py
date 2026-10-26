"""附件媒体库的学海设备闸：密码解锁（5 分钟临时凭证）后放行。

覆盖 ``routers/attachments_router.py`` 的 ``require_xuehai_unlock``：

* 没有 ``Xuehai-Id`` → 一切照旧放行；
* 有 ``Xuehai-Id`` 且带有效 ``X-Device-Unlock`` → 放行；
* 有 ``Xuehai-Id`` 且是带 ``sign`` 的 GET（签名直链） → 放行（端点自行验签）；
* 其余 → 403，detail 固定；
* 凭证本身：过期拒绝、scope 不是 ``temporary_token`` 拒绝、不是同一个用户拒绝。

闸门通过一个只挂依赖的探针路由验证，不碰仓储层；凭证用真实的
``util.cert.jwt_encode`` 签发，和线上同一条链路。
"""

from __future__ import annotations

import datetime
import time

from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient

from routers.attachments_router import (
    DEVICE_LOCKED_DETAIL,
    XUEHAI_UNLOCK_TTL,
    require_xuehai_unlock,
)
from util.cert import jwt_encode

USER_A = "64b7f0c2e1a3b4c5d6e7f901"
USER_B = "64b7f0c2e1a3b4c5d6e7f902"


def _app() -> FastAPI:
    """只挂设备闸的探针应用：依赖是真实对象，端点不触库。"""
    app = FastAPI()
    probe = APIRouter(dependencies=[Depends(require_xuehai_unlock)])

    @probe.get("/")
    async def probe_get():
        return {"ok": True}

    @probe.post("/")
    async def probe_post():
        return {"ok": True}

    app.include_router(probe, prefix="/api/probe")
    return app


def _client() -> TestClient:
    return TestClient(_app())


def _temporary(user: str = USER_A, **kwargs) -> str:
    """5 分钟临时凭证（默认有效期就是 5 分钟，scope=temporary_token）。"""
    return jwt_encode(user, [], ["admin"], "temporary", session_id="s1", **kwargs)


def _access(user: str = USER_A) -> str:
    return jwt_encode(user, [], ["admin"], "long", session_id="s1")


# ---------------------------------------------------------------------------
# 设备闸
# ---------------------------------------------------------------------------


def test_no_device_header_passes() -> None:
    response = _client().get("/api/probe")
    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_device_header_alone_is_blocked() -> None:
    response = _client().get("/api/probe", headers={"Xuehai-Id": "12345"})
    assert response.status_code == 403
    assert response.json() == {"detail": DEVICE_LOCKED_DETAIL}


def test_non_numeric_device_header_passes() -> None:
    """非正整数不是学海设备，保持旧行为放行。"""
    for value in ("", "abc", "0", "-7"):
        response = _client().get("/api/probe", headers={"Xuehai-Id": value})
        assert response.status_code == 200, value


def test_valid_unlock_token_passes() -> None:
    response = _client().get(
        "/api/probe",
        headers={"Xuehai-Id": "12345", "X-Device-Unlock": _temporary()},
    )
    assert response.status_code == 200


def test_unlock_token_with_matching_access_token_passes() -> None:
    response = _client().get(
        "/api/probe",
        headers={
            "Xuehai-Id": "12345",
            "X-Device-Unlock": _temporary(USER_A),
            "Authorization": f"Bearer {_access(USER_A)}",
        },
    )
    assert response.status_code == 200


def test_unlock_token_of_another_user_is_rejected() -> None:
    """拿别人的临时凭证给当前登录用户解锁必须失败。"""
    response = _client().get(
        "/api/probe",
        headers={
            "Xuehai-Id": "12345",
            "X-Device-Unlock": _temporary(USER_B),
            "Authorization": f"Bearer {_access(USER_A)}",
        },
    )
    assert response.status_code == 403
    assert response.json() == {"detail": DEVICE_LOCKED_DETAIL}


def test_expired_unlock_token_is_rejected() -> None:
    expired = _temporary(expires_in=datetime.timedelta(minutes=-6))
    response = _client().get(
        "/api/probe", headers={"Xuehai-Id": "12345", "X-Device-Unlock": expired}
    )
    assert response.status_code == 403
    assert response.json() == {"detail": DEVICE_LOCKED_DETAIL}


def test_access_token_is_not_a_temporary_token() -> None:
    """正式会话凭证不是「刚输过密码」的证明，scope 不对就得拒。"""
    response = _client().get(
        "/api/probe",
        headers={"Xuehai-Id": "12345", "X-Device-Unlock": _access(USER_A)},
    )
    assert response.status_code == 403


def test_garbage_unlock_token_is_rejected() -> None:
    response = _client().get(
        "/api/probe",
        headers={"Xuehai-Id": "12345", "X-Device-Unlock": "not-a-jwt"},
    )
    assert response.status_code == 403
    assert response.json() == {"detail": DEVICE_LOCKED_DETAIL}


def test_signed_get_passes_without_unlock() -> None:
    """``<img>`` 带不了请求头：带 sign 的 GET 交给端点验签。"""
    response = _client().get("/api/probe", params={"sign": "x"}, headers={"Xuehai-Id": "12345"})
    assert response.status_code == 200


def test_signed_post_still_needs_unlock() -> None:
    """签名只对 GET 直链放行，写操作不能靠 query 绕闸。"""
    response = _client().post(
        "/api/probe", params={"sign": "x"}, headers={"Xuehai-Id": "12345"}
    )
    assert response.status_code == 403
    assert response.json() == {"detail": DEVICE_LOCKED_DETAIL}


def test_unlock_window_matches_temporary_token_lifetime() -> None:
    """直链时效钳制常量与临时凭证的 5 分钟寿命对齐。"""
    assert XUEHAI_UNLOCK_TTL == 300


# ---------------------------------------------------------------------------
# 签名直链：学海设备不能拿到活过解锁窗口的 URL
# ---------------------------------------------------------------------------


class _FakeLog:
    def with_text(self, text):
        return self

    async def insert_log(self):
        return None


class _FakeFilesRepo:
    async def get_meta(self, oid):
        return {"_id": oid, "owner_oid": USER_A, "status": "stored"}

    async def add_event(self, *args):
        return None


def _link_app(monkeypatch) -> FastAPI:
    from routers import attachments_router
    from typings.log import inject_log
    from util.object_id import get_current_user

    monkeypatch.setattr(attachments_router.backends, "get_files_repo", lambda *a, **k: _FakeFilesRepo())
    app = FastAPI()
    app.include_router(attachments_router.router, prefix="/api/attachments")
    app.dependency_overrides[get_current_user] = lambda: {"id": USER_A, "per": ["admin"]}
    app.dependency_overrides[inject_log] = lambda: _FakeLog()
    return app


def test_xuehai_signed_link_ttl_is_clamped(monkeypatch) -> None:
    client = TestClient(_link_app(monkeypatch))
    response = client.post(
        f"/api/attachments/{USER_A}/link",
        params={"ttl": 3600},
        headers={"Xuehai-Id": "12345", "X-Device-Unlock": _temporary()},
    )
    assert response.status_code == 200, response.text
    expires_at = response.json()["data"]["expires_at"]
    assert expires_at - int(time.time()) <= XUEHAI_UNLOCK_TTL


def test_non_device_signed_link_keeps_requested_ttl(monkeypatch) -> None:
    client = TestClient(_link_app(monkeypatch))
    response = client.post(f"/api/attachments/{USER_A}/link", params={"ttl": 3600})
    assert response.status_code == 200, response.text
    expires_at = response.json()["data"]["expires_at"]
    assert expires_at - int(time.time()) > 3000
