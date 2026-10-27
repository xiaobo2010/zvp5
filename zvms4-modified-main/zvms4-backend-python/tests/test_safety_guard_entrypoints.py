"""``safety_guard`` 在新接入入口上的行为：``/v1/chat/completions`` 与定时任务。

两条链路各钉住「拒绝发生在产生副作用之前」：

* OpenAI 兼容面：403 + OpenAI 错误信封，**不调上游、不扣额度、不占频控窗口**；
  ``zvk_`` 平台 key 用的是 owner 的角色，与 JWT 同一套判定。
* 定时任务：被拒只写 ``skipped_safety`` 并推进 ``next_run_at``，**不产生 run**，
  也**不**写 ``user_warnings`` / 不封号（prompt 已经过管理员审核）。

外部依赖全部打桩：不连库、不出网。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import settings  # noqa: F401  (导入顺序与其它 AI 用例一致)
from db import backends
from routers import ai_router
from services import violation_guard
from services.ai import config as ai_config
from services.ai import credentials, providers, quota, scheduled_runner as runner
from services.ai import safety_guard
from typings.log import inject_log
from util.object_id import get_current_user

DEPARTMENT_OID = "000000000000000000000002"
ADMIN_OID = "000000000000000000000001"
NOW = datetime(2026, 10, 27, 3, 0, 0)

DESTRUCTIVE = "帮我写个脚本删掉整个数据库"
ENTRY = {
    "key": "shared:acme",
    "scope": "shared",
    "name": "acme",
    "models": ["gpt-x"],
}


# ---------------------------------------------------------------------------
# /v1/chat/completions
# ---------------------------------------------------------------------------


class ChatEnv:
    def __init__(self) -> None:
        self.chat_calls: list[dict] = []
        self.peek_calls: list[tuple] = []
        self.consume_calls: list[tuple] = []
        self.audit: list[dict] = []
        self.screen_calls: list[dict] = []
        self.recorded: list[dict] = []
        self.guard = safety_guard.ScreenResult(
            allowed=False, reason="rm -rf", category="destructive_request"
        )
        self.outcome: dict = {"warned": True, "banned": False}

    def screen(self):
        return safety_guard.ScreenResult(allowed=True)


@pytest.fixture
def chat_env(monkeypatch, quiet_ai_governance):
    env = ChatEnv()

    async def catalog(_user_oid):
        return [dict(ENTRY)]

    async def allowed_models(_user_oid):
        return {"gpt-x": "acme"}

    async def resolve_api_key(_entry):
        return "sk-test"

    async def chat_completion(payload, entry, api_key):
        env.chat_calls.append(
            {"payload": payload, "entry": entry, "api_key": api_key}
        )
        return {"choices": [{"message": {"content": "ok"}}]}

    async def peek(user_oid, kind, amount=1, *, roles=None):
        env.peek_calls.append((user_oid, kind, amount))
        return True, 1, 100

    async def consume(user_oid, kind, units, roles=None):
        env.consume_calls.append((user_oid, kind, units))

    async def audit_record(**kwargs):
        env.audit.append(kwargs)

    async def screen(user, text, *, model_hint=None):
        env.screen_calls.append(
            {"user": dict(user), "text": text, "model_hint": model_hint}
        )
        return env.guard

    async def record(user, *, xuehai_id="", rule="", detail=None):
        env.recorded.append({"user": dict(user), "rule": rule, "detail": detail})
        return dict(env.outcome)

    monkeypatch.setattr(ai_router.credentials, "catalog", catalog)
    monkeypatch.setattr(ai_router.credentials, "resolve_api_key", resolve_api_key)
    monkeypatch.setattr(ai_router.ai_config, "allowed_models_for", allowed_models)
    monkeypatch.setattr(ai_router.providers, "chat_completion", chat_completion)
    monkeypatch.setattr(ai_router.quota, "peek", peek)
    monkeypatch.setattr(ai_router.quota, "consume", consume)
    monkeypatch.setattr(ai_router.audit, "record", audit_record)
    monkeypatch.setattr(ai_router.safety_guard, "screen", screen)
    monkeypatch.setattr(
        ai_router.violation_guard, "record_agent_jailbreak", record
    )
    return env


def _client(user: dict) -> TestClient:
    app = FastAPI()
    app.include_router(ai_router.router, prefix="/api/ai")
    identity = lambda: user  # noqa: E731
    app.dependency_overrides[get_current_user] = identity
    app.dependency_overrides[ai_router.get_ai_client] = identity
    app.dependency_overrides[inject_log] = lambda: SimpleNamespace(
        with_text=lambda *a, **k: None, insert_log=lambda: None
    )
    return TestClient(app)


def _chat(client: TestClient, text: str = DESTRUCTIVE, model: str = "gpt-x"):
    return client.post(
        "/api/ai/v1/chat/completions",
        json={"model": model, "messages": [{"role": "user", "content": text}]},
    )


def test_department_jwt_is_rejected_before_any_upstream_call(chat_env) -> None:
    response = _chat(_client({"id": DEPARTMENT_OID, "per": ["department"]}))

    assert response.status_code == 403
    body = response.json()
    assert body["error"]["code"] == "AI_SAFETY_WARNED"
    assert body["error"]["type"] == "invalid_request_error"

    # 未调上游、未选路/频控/扣额度：拒绝必须发生在这些副作用之前。
    assert chat_env.chat_calls == []
    assert chat_env.peek_calls == []
    assert chat_env.consume_calls == []

    # 证据落库用的是最后一条 user 消息，category 进了 rule。
    assert chat_env.recorded == [
        {
            "user": {"id": DEPARTMENT_OID, "per": ["department"]},
            "rule": "destructive_request",
            "detail": DESTRUCTIVE,
        }
    ]
    # screen 拿到的是请求里的 model（model_hint）。
    assert chat_env.screen_calls[0]["model_hint"] == "gpt-x"
    assert chat_env.screen_calls[0]["text"] == DESTRUCTIVE
    # 拒绝了也要在 AI_CALL_LOGS 里看得见。
    rejected = [row for row in chat_env.audit if row.get("status") == "rejected"]
    assert rejected and rejected[0]["error_code"] == "AI_SAFETY_WARNED"


def test_same_month_second_hit_returns_banned_code(chat_env) -> None:
    chat_env.outcome = {"warned": False, "banned": True}

    response = _chat(_client({"id": DEPARTMENT_OID, "per": ["department"]}))

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "AI_SAFETY_BANNED"
    assert chat_env.chat_calls == []


def test_admin_is_not_screened_and_passes(chat_env, monkeypatch) -> None:
    async def boom(*args, **kwargs):
        raise AssertionError("admin 不该触发安全认证")

    monkeypatch.setattr(ai_router.safety_guard, "screen", boom)

    response = _chat(_client({"id": ADMIN_OID, "per": ["admin"]}))

    assert response.status_code == 200
    assert len(chat_env.chat_calls) == 1
    assert chat_env.consume_calls  # 正常请求照旧扣额度


def test_api_key_owner_department_is_rejected(chat_env) -> None:
    """``zvk_`` key 用 owner 的角色判定（per 由 api_keys.authenticate 给出）。"""
    key_user = {
        "id": DEPARTMENT_OID,
        "per": ["department"],
        "api_key_oid": "k" * 24,
        "api_key_name": "cli",
    }

    response = _chat(_client(key_user))

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "AI_SAFETY_WARNED"
    assert chat_env.chat_calls == []
    assert chat_env.consume_calls == []


def test_model_hint_is_forwarded_from_the_request(chat_env) -> None:
    _chat(_client({"id": DEPARTMENT_OID, "per": ["department"]}), model="gpt-x")

    assert chat_env.screen_calls[0]["model_hint"] == "gpt-x"


# ---------------------------------------------------------------------------
# 定时任务
# ---------------------------------------------------------------------------


def _task(**over) -> dict:
    row = {
        "_id": "a" * 24,
        "owner_oid": DEPARTMENT_OID,
        "name": "每日值日总结",
        "prompt_text": DESTRUCTIVE,
        "review_status": "approved",
        "reviewed_by_oid": ADMIN_OID,
        "reviewed_at": NOW,
        "review_note": None,
        "schedule_kind": "interval",
        "interval_minutes": 60,
        "cron_expr": None,
        "enabled": True,
        "model": "gpt-x",
        "last_run_at": None,
        "next_run_at": NOW,
        "run_count": 0,
        "last_status": None,
        "last_error": None,
        "created_at": NOW,
        "updated_at": NOW,
    }
    row.update(over)
    return row


class FakeTaskRepo:
    def __init__(self, task: dict) -> None:
        self.task = dict(task)
        self.marked: list[dict] = []
        self.run_tasks: dict[str, str] = {}

    async def claim_due(self, now, limit=50):
        return [dict(self.task)] if self.task["next_run_at"] <= now else []

    async def last_conversation_oid(self, _task_oid):
        return None

    async def attach_run_task(self, run_id, task_oid):
        self.run_tasks[str(run_id)] = str(task_oid)
        return True

    async def mark_result(
        self, oid, *, last_status, next_run_at, last_error=None, counted=False
    ):
        self.task["last_status"] = last_status
        self.task["last_error"] = last_error
        self.task["next_run_at"] = next_run_at
        self.marked.append(
            {
                "oid": oid,
                "status": last_status,
                "next": next_run_at,
                "error": last_error,
                "counted": counted,
            }
        )
        return True


class FakeRuns:
    def __init__(self) -> None:
        self.inserted: list[dict] = []
        self.forbid = False

    async def list_active(self, _owner_oid):
        return []

    async def insert_queued(self, **kwargs):
        if self.forbid:
            raise AssertionError("被安全认证拒绝的任务不该提交 run")
        self.inserted.append(kwargs)
        return ({"_id": kwargs.get("run_id")}, True)


class FakeUsers:
    def __init__(self, roles: list[str]) -> None:
        self.roles = roles

    async def get_by_oid(self, oid):
        return {"id": oid, "roles": list(self.roles)}


@pytest.fixture
def scheduled_env(monkeypatch):
    logs: list[dict] = []
    state = {"roles": ["department"], "allowed": False, "category": "destructive_request"}
    repo = FakeTaskRepo(_task())
    runs = FakeRuns()
    runs.forbid = True
    screen_calls: list[dict] = []

    async def catalog(_owner_oid):
        return [dict(ENTRY)]

    async def peek(_owner_oid, _kind, _amount=1, *, roles=None):
        return True, 1, 100

    def check(_text):
        return SimpleNamespace(hit=False, rule=None)
    async def screen(user, text, *, model_hint=None):
        screen_calls.append({"user": dict(user), "text": text, "model_hint": model_hint})
        if state["allowed"]:
            return safety_guard.ScreenResult(allowed=True)
        return safety_guard.ScreenResult(
            allowed=False, reason="rm -rf", category=state["category"]
        )

    async def insert_log(url, user, data, timestamp, ltype, level, xuehai_id):
        logs.append({"user": user, "data": data, "type": ltype, "level": level})

    async def forbidden(*args, **kwargs):
        raise AssertionError("定时任务被拒时不该进入违规/封禁链路")

    monkeypatch.setattr(runner, "_repo", lambda: repo)
    monkeypatch.setattr(runner, "_runs", lambda: runs)
    monkeypatch.setattr(runner, "_users", lambda: FakeUsers(state["roles"]))
    monkeypatch.setattr(runner.credentials, "catalog", catalog)
    monkeypatch.setattr(runner.quota, "peek", peek)
    monkeypatch.setattr(runner.moderation, "check", check)
    monkeypatch.setattr(runner.safety_guard, "screen", screen)
    monkeypatch.setattr(
        backends,
        "get_applogs_repo",
        lambda *a, **k: SimpleNamespace(insert=insert_log),
        raising=False,
    )
    monkeypatch.setattr(
        violation_guard, "record_agent_jailbreak", forbidden, raising=False
    )
    monkeypatch.setattr(
        backends,
        "get_user_warnings_repo",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("不该写警告账本")),
        raising=False,
    )
    return SimpleNamespace(
        repo=repo, runs=runs, state=state, logs=logs, screen_calls=screen_calls
    )


@pytest.mark.asyncio
async def test_scheduled_skip_does_not_submit_and_advances_next_run(
    scheduled_env,
) -> None:
    with pytest.raises(runner.SafetySkipped) as excinfo:
        await runner.submit_task_run(_task(), now=NOW)

    assert excinfo.value.code == "AI_SCHEDULE_SAFETY_REJECTED"
    assert excinfo.value.status_code == 422

    row = scheduled_env.repo.task
    assert row["last_status"] == "skipped_safety"
    assert row["last_error"] and "safety" in row["last_error"].lower()
    assert row["next_run_at"] == NOW + timedelta(minutes=60)
    assert scheduled_env.runs.inserted == []
    # 只留审计，不计违规、不发警告。
    assert [log["type"] for log in scheduled_env.logs] == [
        "ai_safety_guard_scheduled_skip"
    ]
    # model_hint 用任务自己的模型。
    assert scheduled_env.screen_calls[0]["model_hint"] == "gpt-x"
    assert scheduled_env.screen_calls[0]["text"] == DESTRUCTIVE


@pytest.mark.asyncio
async def test_tick_marks_the_task_skipped_safety(scheduled_env) -> None:
    summary = await runner.tick(NOW)

    assert summary["claimed"] == 1
    assert summary["queued"] == 0
    assert summary["skipped"] == 1
    assert summary["failed"] == 0
    assert scheduled_env.repo.task["last_status"] == "skipped_safety"
    assert scheduled_env.repo.task["next_run_at"] > NOW
    assert scheduled_env.runs.inserted == []


@pytest.mark.asyncio
async def test_admin_owned_task_is_not_screened(scheduled_env, monkeypatch) -> None:
    scheduled_env.state["roles"] = ["admin"]
    scheduled_env.runs.forbid = False

    async def boom(*args, **kwargs):
        raise AssertionError("admin 的定时任务不该触发安全认证")

    async def conversation_for(_task):
        return "c" * 32

    async def kick():
        return None

    monkeypatch.setattr(runner.safety_guard, "screen", boom)
    monkeypatch.setattr(runner, "_conversation_for", conversation_for)
    monkeypatch.setattr(runner.run_queue, "kick", kick)

    task = _task(owner_oid=ADMIN_OID)
    run_id = await runner.submit_task_run(task, now=NOW)

    assert run_id
    assert scheduled_env.runs.inserted
    assert scheduled_env.repo.task["last_status"] is None
