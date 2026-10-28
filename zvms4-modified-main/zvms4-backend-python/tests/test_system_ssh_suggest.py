"""``POST /api/system/ssh/suggest``：AI 起草 SSH 命令（生成 ≠ 执行）。

不连库、不出网、不建 SSH：AI 组件在 ``services.ai`` 命名空间打桩，用户库与
``ZVMSLog`` 在路由命名空间打桩。两组断言：

* 错误码与计费口径必须和 ``/api/ai/v1/chat/completions`` 一致（404/429/504/400）；
* 这个端点**从不执行**：``ssh_exec.run_once``、``SshSession.open``、``issue_ticket``
  一次都不能被调用（fixture 退出时统一复核）。

跑法：``.venv/bin/python -m pytest tests/test_system_ssh_suggest.py -q``
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from db import backends
from routers import system_ssh_router
from services import ssh_bridge
from services.agent import ssh_exec
from services.ai import config as ai_config
from services.ai import credentials, providers, quota, rate_limit, routing, textgen
from util.object_id import get_current_user

OPERATOR = "000000000000000000000001"
SUGGEST = "/api/system/ssh/suggest"
GOOD_COMMAND = "systemctl status caddy --no-pager"


def _entry(
    scope: str = "shared",
    oid: str = "b" * 24,
    name: str = "team",
    models: tuple[str, ...] = ("gpt-x",),
) -> dict:
    return {
        "key": f"{scope}:{oid}",
        "scope": scope,
        "owner_oid": credentials.SHARED_OWNER,
        "name": name,
        "kind": "openai_compat",
        "base_url": f"https://{name}.example.com/v1",
        "models": list(models),
        "api_key_cipher": "cipher-" + name,
        "key_hint": "cdef",
        "enabled": True,
        "oid": oid,
    }


def _completion(
    command: str, explanation: str = "查看服务状态", total_tokens: int = 120
) -> dict:
    return {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {"command": command, "explanation": explanation},
                        ensure_ascii=False,
                    )
                }
            }
        ],
        "usage": {"total_tokens": total_tokens},
    }


class SyncRecorder:
    """同步调用记录器（``rate_limit.check`` 是同步函数）。"""

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.error: BaseException | None = None

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if self.error is not None:
            raise self.error
        return None


class Recorder:
    """异步调用记录器（``quota.peek`` / ``quota.consume``）。"""

    def __init__(self, result=None) -> None:
        self.calls: list[tuple] = []
        self.result = result
        self.error: BaseException | None = None

    async def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if self.error is not None:
            raise self.error
        return self.result


@pytest.fixture
def env(monkeypatch, quiet_ai_governance):
    logs: list[dict] = []
    audits: list[dict] = []
    shell_calls: list[str] = []
    ticket_calls: list[tuple] = []

    class StubLog:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

        async def insert_log(self):
            logs.append(self.kwargs)

    monkeypatch.setattr(system_ssh_router, "ZVMSLog", StubLog)

    class StubUsersRepo:
        async def get_by_oid(self, oid):
            return {"_id": OPERATOR, "id": "20240101", "roles": ["admin"]}

    monkeypatch.setattr(
        backends, "get_users_repo", lambda session=None: StubUsersRepo()
    )

    async def permissions(user):
        return list(user.get("roles") or [])

    monkeypatch.setattr(system_ssh_router, "get_user_permissions", permissions)

    class FakeAuditRepo:
        async def insert(self, **fields):
            audits.append(fields)
            return "a" * 24

    monkeypatch.setattr(
        backends, "get_ai_call_logs_repo", lambda session=None: FakeAuditRepo()
    )
    monkeypatch.setattr(ai_config, "is_enabled", lambda: True)

    catalog_state = {"entries": [_entry()]}

    async def catalog(user_oid):
        return [dict(entry) for entry in catalog_state["entries"]]

    monkeypatch.setattr(credentials, "catalog", catalog)

    async def allowed_models(user_oid):
        return {
            model: entry["key"]
            for entry in catalog_state["entries"]
            for model in entry.get("models") or []
        }

    monkeypatch.setattr(ai_config, "allowed_models_for", allowed_models)

    async def resolve(entry):
        return "sk-unit-test-0123456789abcdef"

    monkeypatch.setattr(credentials, "resolve_api_key", resolve)

    rate = SyncRecorder()
    monkeypatch.setattr(rate_limit, "check", rate)

    peek = Recorder((True, 0, 100))
    consume = Recorder((True, 1, 100))
    monkeypatch.setattr(quota, "peek", peek)
    monkeypatch.setattr(quota, "consume", consume)

    script: list = []
    upstream: list[dict] = []

    async def chat_completion(payload, entry, api_key):
        upstream.append({"payload": payload, "entry": entry, "api_key": api_key})
        outcome = script.pop(0) if script else _completion(GOOD_COMMAND)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(providers, "chat_completion", chat_completion)

    # 两条执行原语都换成「一碰就炸」的记录器：本端点必须一条都碰不到。
    async def _open(cls, *args, **kwargs):
        shell_calls.append("SshSession.open")
        raise AssertionError("suggest 不得打开 SSH 会话")

    monkeypatch.setattr(ssh_bridge.SshSession, "open", classmethod(_open))

    async def _run_once(command, *, timeout=30.0):
        shell_calls.append("run_once")
        raise AssertionError("suggest 不得执行命令")

    monkeypatch.setattr(ssh_exec, "run_once", _run_once)

    def _issue(operator_oid, approver_oid, ttl_seconds=None):
        ticket_calls.append((operator_oid, approver_oid))
        return "t" * 40, 120

    monkeypatch.setattr(ssh_bridge, "issue_ticket", _issue)

    routing.reset_state()
    rate_limit.reset_state()

    app = FastAPI()
    app.include_router(system_ssh_router.router, prefix="/api/system/ssh")
    app.dependency_overrides[get_current_user] = lambda: {
        "id": OPERATOR,
        "per": ["admin"],
    }

    yield SimpleNamespace(
        app=app,
        client=TestClient(app),
        logs=logs,
        audits=audits,
        upstream=upstream,
        script=script,
        shell_calls=shell_calls,
        ticket_calls=ticket_calls,
        catalog_state=catalog_state,
        rate=rate,
        peek=peek,
        consume=consume,
    )

    # 无论用例走哪条分支，都复核「没有执行、没有发 ticket」。
    assert shell_calls == [], f"SSH 执行原语被调用了: {shell_calls}"
    assert ticket_calls == [], f"不应发放终端 ticket: {ticket_calls}"


def _post(env, **body):
    payload = {"intent": "看一下 caddy 的状态"}
    payload.update(body)
    return env.client.post(SUGGEST, json=payload)


# ---------------------------------------------------------------------------
# 鉴权
# ---------------------------------------------------------------------------


def test_suggest_requires_login():
    """未带凭据：认证依赖直接 401（端点存在，不是 404）。"""
    app = FastAPI()
    app.include_router(system_ssh_router.router, prefix="/api/system/ssh")

    response = TestClient(app).post(SUGGEST, json={"intent": "看一下磁盘"})

    assert response.status_code == 401


def test_suggest_rejects_non_admin(env):
    env.app.dependency_overrides[get_current_user] = lambda: {
        "id": OPERATOR,
        "per": ["student"],
    }

    response = _post(env)

    assert response.status_code == 403
    assert response.json()["detail"] == "权限不足"
    assert env.upstream == []


def test_suggest_rejects_empty_intent(env):
    response = env.client.post(SUGGEST, json={"intent": ""})

    assert response.status_code == 422
    assert env.upstream == []


# ---------------------------------------------------------------------------
# 成功路径
# ---------------------------------------------------------------------------


def test_suggest_returns_command_explanation_model(env):
    response = _post(env)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok" and body["code"] == 200
    data = body["data"]
    assert data["command"] == GOOD_COMMAND
    assert data["explanation"] == "查看服务状态"
    # 对外回显的是合格 ID（provider/model），不再是裸名。
    assert data["model"] == "team/gpt-x"
    assert isinstance(data["request_id"], str) and len(data["request_id"]) == 32


def test_suggest_uses_server_side_system_prompt(env):
    """前端能改的只有 intent/context/model：system prompt 是服务端常量。"""
    _post(env, context="caddy 在 docker 里，宿主是 ubuntu 22.04")

    payload = env.upstream[0]["payload"]
    # 上游请求体只要裸模型名：合格 ID 是网关自己的命名空间。
    assert payload["model"] == "gpt-x"
    assert payload["stream"] is False
    assert payload["messages"][0] == {
        "role": "system",
        "content": textgen.SYSTEM_PROMPT,
    }
    assert "caddy 在 docker 里" in payload["messages"][1]["content"]


def test_suggest_accepts_explicit_model(env):
    env.catalog_state["entries"] = [_entry(models=("gpt-x", "gpt-y"))]

    response = _post(env, model="gpt-y")

    assert response.status_code == 200
    # 传裸名也接受（唯一命中），回显统一升格成合格 ID。
    assert response.json()["data"]["model"] == "team/gpt-y"


def test_suggest_writes_suggest_audit_log(env):
    _post(env)

    (log,) = env.logs
    assert log["type"] == "system_ssh_suggest"
    assert log["level"] == "info"
    data = json.loads(log["data"])
    assert data["intent_len"] == len("看一下 caddy 的状态")
    # 审计里的 model 与接口回显同口径：合格 ID。
    assert data["model"] == "team/gpt-x"
    assert data["command_head"] == GOOD_COMMAND


def test_suggest_records_ai_audit_with_purpose(env):
    response = _post(env)

    (row,) = env.audits
    assert row["capability"] == "chat"
    assert row["purpose"] == "system-ssh-suggest"
    assert row["endpoint"] == "/api/ai/v1/chat/completions"
    assert row["status"] == "success"
    assert row["request_id"] == response.json()["data"]["request_id"]
    assert row["units"] == 1


# ---------------------------------------------------------------------------
# 输出解析
# ---------------------------------------------------------------------------


def test_suggest_strips_markdown_fence(env):
    env.script.append(
        {
            "choices": [
                {
                    "message": {
                        "content": '```json\n{"command": "ls -l /etc/caddy"}\n```'
                    }
                }
            ]
        }
    )

    response = _post(env)

    assert response.status_code == 200
    assert response.json()["data"]["command"] == "ls -l /etc/caddy"
    assert response.json()["data"]["explanation"] == ""


def test_suggest_flattens_multiline_command(env):
    """换行粘进终端 = 隐式回车：模型给多行时压成一行，内容不丢。"""
    env.script.append(
        _completion("systemctl restart caddy\nsystemctl status caddy")
    )

    command = _post(env).json()["data"]["command"]

    assert "\n" not in command
    assert command == "systemctl restart caddy systemctl status caddy"


def test_suggest_falls_back_to_raw_text_when_not_json(env):
    env.script.append({"choices": [{"message": {"content": "df -h /"}}]})

    data = _post(env).json()["data"]

    assert data["command"] == "df -h /"
    assert data["explanation"] == ""


def test_suggest_rejects_overlong_command(env):
    env.script.append(_completion("echo " + "x" * 1100))

    response = _post(env)

    assert response.status_code == 400
    assert response.json()["detail"] == "AI_AGENT_BAD_ARGUMENT"
    assert env.consume.calls == []


# ---------------------------------------------------------------------------
# 上游 / 门禁错误
# ---------------------------------------------------------------------------


def test_suggest_unknown_model_returns_404(env):
    response = _post(env, model="gpt-not-listed")

    assert response.status_code == 404
    assert response.json()["detail"] == "model_not_found"
    assert env.upstream == []
    # 门禁拒绝也留流水（同 chat 面），但一次上游都没打。
    (row,) = env.audits
    assert (row["status"], row["error_code"]) == ("rejected", "model_not_found")


def test_suggest_without_provider_returns_503(env):
    env.catalog_state["entries"] = []

    response = _post(env)

    assert response.status_code == 503
    assert response.json()["detail"] == "AI_NO_PROVIDER"


def test_suggest_gateway_disabled_returns_503(env, monkeypatch):
    monkeypatch.setattr(ai_config, "is_enabled", lambda: False)

    response = _post(env)

    assert response.status_code == 503
    assert response.json()["detail"] == "AI_DISABLED"
    assert env.upstream == []


def test_suggest_upstream_timeout_returns_504(env):
    env.script.append(routing.RouteError("AI_TIMEOUT", "upstream timed out", 504))

    response = _post(env)

    assert response.status_code == 504
    assert response.json()["detail"] == "AI_TIMEOUT"
    (row,) = env.audits
    assert row["status"] == "timeout"
    assert row["error_code"] == "AI_TIMEOUT"
    # 失败不扣额度、不写生成审计。
    assert env.consume.calls == []
    assert env.logs == []


def test_suggest_busy_returns_429(env):
    env.script.append(routing.RouteError("AI_BUSY", "ai gateway is busy", 429))

    response = _post(env)

    assert response.status_code == 429
    assert response.json()["detail"] == "AI_BUSY"
    assert env.audits[0]["status"] == "busy"


def test_suggest_local_rate_limit_returns_429(env):
    env.rate.error = rate_limit.RateLimitError(30)

    response = _post(env)

    assert response.status_code == 429
    assert response.json()["detail"] == "AI_RATE_LIMITED"
    assert env.upstream == []


def test_suggest_quota_exhausted_returns_429(env):
    env.peek.result = (False, 100, 100)

    response = _post(env)

    assert response.status_code == 429
    assert response.json()["detail"] == "insufficient_quota"
    assert env.upstream == []


# ---------------------------------------------------------------------------
# 计费：个人 key 不扣，共享 / 服务端扣
# ---------------------------------------------------------------------------


def test_personal_key_is_not_charged(env):
    env.catalog_state["entries"] = [_entry(scope="personal")]

    response = _post(env)

    assert response.status_code == 200
    assert env.peek.calls == []
    assert env.consume.calls == []
    assert env.audits[0]["key_scope"] == "personal"


@pytest.mark.parametrize("scope", ["shared", "server"])
def test_platform_key_is_charged(env, scope):
    env.catalog_state["entries"] = [_entry(scope=scope)]

    response = _post(env)

    assert response.status_code == 200
    # 120 token → 1 单位（与 chat 面同一把尺子）。
    assert env.consume.calls == [((OPERATOR, "chat", 1), {"roles": ["admin"]})]
    assert env.audits[0]["key_scope"] == scope


# ---------------------------------------------------------------------------
# 安全：脱敏 + 从不执行
# ---------------------------------------------------------------------------


def test_audit_log_masks_secret_in_command(env):
    env.script.append(
        _completion("mysql -u root --password=secret -e 'select 1'")
    )

    response = _post(env, intent="用 --password=secret 这个口令连一下 mysql")

    assert response.status_code == 200
    (log,) = env.logs
    assert "secret" not in json.dumps(log, ensure_ascii=False)
    assert "<redacted>" in json.loads(log["data"])["command_head"]
    # 命令原文只为「给人看」存在，不落进 AI 审计（那张表只存元数据）。
    assert "mysql" not in json.dumps(env.audits, ensure_ascii=False)


def test_suggest_never_executes_or_opens_a_session(env):
    """成功路径同样不得碰执行原语（fixture 退出时还会再复核一次）。"""
    assert _post(env).status_code == 200

    assert env.shell_calls == []
    assert env.ticket_calls == []


def test_suggest_log_type_is_protected_from_purge():
    """生成记录与执行记录同享保留期豁免（``db/repos/oracle/app_logs.py``）。"""
    from db.repos.oracle import app_logs

    assert "system_ssh_suggest" in app_logs.PROTECTED_LTYPES
