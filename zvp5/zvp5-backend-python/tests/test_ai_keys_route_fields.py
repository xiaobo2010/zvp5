"""``/api/ai/keys`` 路由层的 ``headers`` / ``api_path`` 透传（车道 2b）。

服务层的校验与脱敏已经在 ``tests/test_ai_llm_headers.py`` 钉住了；这里只钉**路由
有没有把这两个字段送下去**——漏传不会报错，只会让管理员配好的自定义头静默失效，
是最难从响应里发现的一类问题。
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import settings  # noqa: F401  (services.ai.config 读它，先导入保证顺序一致)
from routers import ai_router
from services.ai import credentials
from typings.log import inject_log
from util.object_id import get_current_user

ADMIN = "000000000000000000000001"
OID = "a" * 24
API_KEY = "sk-unit-test-0123456789abcdef"


class StubLog:
    def with_text(self, *args, **kwargs):
        return self

    async def insert_log(self):
        return None


async def _allow_reauth(*_args, **_kwargs):
    """本文件只验业务语义；再认证本身由 ``test_admin_reauth_gate`` 专测。"""
    return None


@pytest.fixture(autouse=True)
def _no_reauth(monkeypatch):
    monkeypatch.setattr("routers.ai.keys.require_password_verification", _allow_reauth)


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(ai_router.router, prefix="/api/ai")
    app.dependency_overrides[get_current_user] = lambda: {
        "id": ADMIN,
        "per": ["admin"],
    }
    app.dependency_overrides[inject_log] = lambda: StubLog()
    return TestClient(app)


class Recorder:
    def __init__(self, result: dict) -> None:
        self.calls: list[dict] = []
        self.result = result

    async def __call__(self, **kwargs) -> dict:
        self.calls.append(kwargs)
        return dict(self.result)


@pytest.fixture
def create_entry(monkeypatch) -> Recorder:
    recorder = Recorder(
        {
            "oid": OID,
            "scope": "shared",
            "name": "team",
            "headers": {"X-Api-Key": "****"},
            "api_path": "/v1/messages",
        }
    )
    monkeypatch.setattr(credentials, "create_entry", recorder)
    return recorder


@pytest.fixture
def update_entry(monkeypatch) -> Recorder:
    recorder = Recorder(
        {"oid": OID, "scope": "shared", "name": "team", "headers": {}, "api_path": None}
    )
    monkeypatch.setattr(credentials, "update_entry", recorder)
    return recorder


def _body(**extra) -> dict:
    body = {
        "scope": "shared",
        "name": "team",
        "base_url": "https://api.example.com/v1",
        "models": ["claude-x"],
        "api_key": API_KEY,
    }
    body.update(extra)
    return body


def test_create_passes_headers_and_api_path(create_entry):
    response = _client().post(
        "/api/ai/keys",
        json=_body(
            headers={"X-Api-Key": API_KEY},
            api_path="/v1/messages",
        ),
    )

    assert response.status_code == 201
    sent = create_entry.calls[0]
    assert sent["headers"] == {"X-Api-Key": API_KEY}
    assert sent["api_path"] == "/v1/messages"
    # 响应里不回明文头值（只回 ****）。
    assert API_KEY not in response.text


def test_create_without_them_keeps_previous_behaviour(create_entry):
    response = _client().post("/api/ai/keys", json=_body())

    assert response.status_code == 201
    sent = create_entry.calls[0]
    # 不传 = None（服务层按「没有自定义头/路径」处理），不是空 dict/空串。
    assert sent["headers"] is None
    assert sent["api_path"] is None


def test_patch_passes_them_through_fields(update_entry):
    response = _client().patch(
        f"/api/ai/keys/{OID}",
        json={"headers": {"X-Api-Key": API_KEY}, "api_path": "/v1/messages"},
    )

    assert response.status_code == 200
    sent = update_entry.calls[0]
    # 路由层不重新校验：错误码口径全在 credentials，重复一遍只会漂移。
    assert sent["fields"]["headers"] == {"X-Api-Key": API_KEY}
    assert sent["fields"]["api_path"] == "/v1/messages"
    assert sent["oid"] == OID


def test_patch_without_them_does_not_touch_existing_values(update_entry):
    response = _client().patch(f"/api/ai/keys/{OID}", json={"enabled": False})

    assert response.status_code == 200
    sent = update_entry.calls[0]
    # exclude_unset：没传的字段根本不出现在 payload 里（= 不改）。
    assert "headers" not in sent["fields"]
    assert "api_path" not in sent["fields"]
