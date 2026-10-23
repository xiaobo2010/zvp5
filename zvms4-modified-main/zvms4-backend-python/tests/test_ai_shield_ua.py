"""盾牌中间件对 OpenAI SDK 的放行边界（走真实 ``main.app``）。

OpenAI SDK 的 UA 是 ``OpenAI/Python x.y``，不含 ``Mozilla``——不放行的话 SDK 一个
请求都发不出去。放行面必须**只有** ``/api/ai/v1/`` 前缀，且只放开 UA 判断：
``SEC_PATTERN``（``.sql`` / ``.env`` / ``swagger`` ...）与 method 白名单照旧生效，
否则这条豁免就成了绕过盾牌的通用后门。

鉴权仍走 ``Authorization: Bearer``：没有 token 时 401 就是「已经进到路由层」的
证据，也是这里断言「不是 418」的方式（401/403 都算放行）。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import main

MOZ = {"User-Agent": "Mozilla/5.0 (lane2 harness)"}
OPENAI = {"User-Agent": "OpenAI/Python 1.30"}
ATTACHMENT = "f" * 24


class _NullLog:
    """418 分支的审计是 fire-and-forget 的 DB 写：桩掉它，用例不连库。"""

    def __init__(self, **_kwargs) -> None:
        pass

    async def insert_log(self) -> None:
        return None


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(main, "ZVMSLog", _NullLog)
    http = TestClient(main.app)
    try:
        yield http
    finally:
        http.close()


def test_openai_ua_is_not_shielded_on_the_v1_prefix(client):
    response = client.get("/api/ai/v1/models", headers=OPENAI)

    assert response.status_code != 418
    # 401 = 盾牌放行、路由层的 Bearer 鉴权接管；这是「进来了」而不是「被拦了」。
    assert response.status_code in (401, 403)


def test_openai_ua_is_not_shielded_on_chat_completions(client):
    response = client.post(
        "/api/ai/v1/chat/completions", headers=OPENAI, json={"model": "gpt-x"}
    )

    assert response.status_code != 418
    assert response.status_code in (401, 403)


def test_openai_ua_is_still_shielded_outside_the_v1_prefix(client):
    """/api/ai/ocr 不在豁免前缀里：同一个 UA 仍然吃 418。"""
    response = client.post(
        "/api/ai/ocr", headers=OPENAI, json={"attachment_oid": ATTACHMENT}
    )

    assert response.status_code == 418
    assert response.json() == "请求已被拦截"


def test_openai_ua_does_not_bypass_the_suspicious_path_pattern(client):
    """豁免只放开 UA，不放开 SEC_PATTERN：``.sql`` 结尾照样 418。"""
    response = client.get("/api/ai/v1/models.sql", headers=OPENAI)

    assert response.status_code == 418


def test_mozilla_ua_reaches_the_gateway(client):
    response = client.get("/api/ai/v1/models", headers=MOZ)

    assert response.status_code != 418
    assert response.status_code in (401, 403)
