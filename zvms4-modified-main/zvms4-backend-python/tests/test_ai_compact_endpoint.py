"""``POST /api/ai/conversations/{oid}/compact``：手动压缩上下文的协议面。

这个端点值得单独守，因为它的**契约字段**是前端进度条与提示文案的唯一依据，而且
四条分支长得都很像「200」：

* 成功：``compacted=true`` + 水位前后 + ``upto_seq``；
* 无可压缩旧段：``compacted=false`` + ``reason="nothing_to_compact"``（**不是**错误）；
* 降级硬截断：``compacted=true`` + ``degraded=true`` + ``reason="degraded"``；
* 在飞 run：409 ``AI_RUN_CONFLICT``（沿用「同会话已有 run」的同一个码）。

额度不足必须走既有的 429 映射（``insufficient_quota``），不能冒成 500；协议层崩溃
则必须是可解析的信封，而不是裸栈。全部离线：服务层是桩，HTTP 面用 ``TestClient``。
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from db import backends
from routers import ai_conversations_router as conv_router
from services.agent.run_queue import RUN_CONFLICT
from services.agent.tool_impl import AgentError
from services.roles import policy
from util.object_id import get_current_user

PREFIX = "/api/ai"
OWNER = "0" * 24
OTHER = "1" * 24
ADMIN = {"id": OWNER, "per": ["admin"]}
DEPT = {"id": OTHER, "per": ["department"]}
CONV = "a" * 32

SUCCESS = {
    "compacted": True,
    "upto_seq": 6,
    "tokens_before": 180_000,
    "tokens_after": 21_000,
    "degraded": False,
    "reason": None,
}


class FakeCallLogsRepo:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    async def insert(self, **fields) -> None:
        self.rows.append(fields)


@pytest.fixture
def call_logs(monkeypatch) -> FakeCallLogsRepo:
    logs = FakeCallLogsRepo()
    monkeypatch.setattr(
        backends, "get_ai_call_logs_repo", lambda session=None: logs
    )
    return logs


@pytest.fixture(autouse=True)
def _isolated_policy(monkeypatch):
    """功能开关是进程级缓存：每个用例从默认状态开始。"""
    policy.reset_cache()
    yield
    policy.reset_cache()


def _client(user: dict = ADMIN) -> TestClient:
    app = FastAPI()
    app.include_router(conv_router.router, prefix=PREFIX)
    app.dependency_overrides[get_current_user] = lambda: dict(user)
    return TestClient(app)


def _install(monkeypatch, result=None, *, error: Exception | None = None):
    calls: list[dict] = []

    async def compact_conversation(*, user, conversation_oid):
        calls.append({"user": user, "conversation_oid": conversation_oid})
        if error is not None:
            raise error
        return dict(result or SUCCESS)

    monkeypatch.setattr(conv_router.run_queue, "compact_conversation", compact_conversation)
    return calls


def test_success_returns_the_frozen_contract(call_logs, monkeypatch) -> None:
    calls = _install(monkeypatch)

    response = _client().post(f"{PREFIX}/conversations/{CONV}/compact")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "ok"
    assert body["code"] == 200
    # 字段名是契约：前端按这四个数字画「压缩前 → 压缩后」，改名等于静默失效。
    assert body["data"] == {
        "compacted": True,
        "upto_seq": 6,
        "tokens_before": 180_000,
        "tokens_after": 21_000,
        "degraded": False,
        "reason": None,
    }
    assert "X-AI-Request-Id" in response.headers
    # 归属校验在服务层：路由只做 oid 形状归一（小写）后原样交下去。
    assert calls[0]["conversation_oid"] == CONV


def test_nothing_to_compact_is_still_200(call_logs, monkeypatch) -> None:
    calls = _install(
        monkeypatch,
        {
            "compacted": False,
            "upto_seq": None,
            "tokens_before": 900,
            "tokens_after": 900,
            "degraded": False,
            "reason": "nothing_to_compact",
        },
    )

    response = _client().post(f"{PREFIX}/conversations/{CONV}/compact")

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["compacted"] is False
    assert data["reason"] == "nothing_to_compact"
    assert calls and calls[0]["conversation_oid"] == CONV


def test_degraded_compaction_is_reported(call_logs, monkeypatch) -> None:
    _install(
        monkeypatch,
        {
            "compacted": True,
            "upto_seq": 8,
            "tokens_before": 190_000,
            "tokens_after": 12_000,
            "degraded": True,
            "reason": "degraded",
        },
    )

    response = _client().post(f"{PREFIX}/conversations/{CONV}/compact")

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert (data["compacted"], data["degraded"], data["reason"]) == (
        True,
        True,
        "degraded",
    )


def test_active_run_is_a_conflict(call_logs, monkeypatch) -> None:
    _install(
        monkeypatch,
        error=AgentError(
            RUN_CONFLICT, "this conversation already has an active run", 409
        ),
    )

    response = _client().post(f"{PREFIX}/conversations/{CONV}/compact")

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "AI_RUN_CONFLICT"


def test_insufficient_quota_is_mapped_not_a_500(call_logs, monkeypatch) -> None:
    _install(
        monkeypatch,
        error=AgentError(
            "insufficient_quota", "monthly chat quota is exhausted", 429
        ),
    )

    response = _client().post(f"{PREFIX}/conversations/{CONV}/compact")

    assert response.status_code == 429, response.text
    assert response.json()["error"]["code"] == "insufficient_quota"


def test_malformed_oid_is_404_without_touching_the_service(
    call_logs, monkeypatch
) -> None:
    calls = _install(monkeypatch)

    response = _client().post(f"{PREFIX}/conversations/not-an-oid/compact")

    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "AI_CONVERSATION_NOT_FOUND"
    assert calls == []


def test_feature_gate_denies_before_any_work(call_logs, monkeypatch) -> None:
    """关掉 ``ai.agent`` → 403 且不进入服务层（不花一次上游调用）。"""
    monkeypatch.setitem(policy._features, (DEPT["per"][0], conv_router.AGENT_FEATURE), False)
    calls = _install(monkeypatch)

    response = _client(DEPT).post(f"{PREFIX}/conversations/{CONV}/compact")

    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "AI_FORBIDDEN"
    assert calls == []


def test_unexpected_failure_is_a_parseable_envelope(call_logs, monkeypatch) -> None:
    _install(monkeypatch, error=RuntimeError("db down"))

    response = _client().post(f"{PREFIX}/conversations/{CONV}/compact")

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "AI_AGENT_INTERNAL_ERROR"
