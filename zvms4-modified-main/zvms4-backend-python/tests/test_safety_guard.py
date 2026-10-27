"""``services.ai.safety_guard`` 的离线契约（角色门、LLM 判定、fail-open）。

为什么这些用例都在服务层：安全认证的**唯一**产品要求是「别误伤正常用户」。
所以每条断言都对应一个「闸门坏掉时会发生什么」——非 department 不触达网络、
无凭据/超时/坏 JSON 一律放行并留痕、只有「无关 + 破坏性」才拒绝。全部外部调用
打桩，不连 Oracle、不发 HTTP。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from db import backends
from services.ai import config as ai_config
from services.ai import credentials, providers, safety_guard
from services.ai.routing import RouteError

USER = {"id": "000000000000000000000002", "per": ["department"]}
ADMIN = {"id": "000000000000000000000003", "per": ["admin", "department"]}
STUDENT = {"id": "000000000000000000000004", "per": ["student"]}

ENTRY = {"key": "personal:abc", "scope": "personal", "models": ["m1"]}

DESTRUCTIVE = (
    '{"related": false, "destructive": true, '
    '"category": "destructive_request", "reason": "rm -rf"}'
)


def _response(content: str) -> dict:
    return {"choices": [{"message": {"content": content}}]}


@pytest.fixture
def env(monkeypatch):
    """安全认证的外部世界：凭据目录、明文 key、上游调用、app_logs。"""

    logs: list[dict] = []
    calls = {"chat": [], "catalog": [], "keys": []}
    state = {"entries": [dict(ENTRY)], "reply": DESTRUCTIVE, "delay": 0.0}

    async def catalog(user_oid):
        calls["catalog"].append(user_oid)
        return [dict(entry) for entry in state["entries"]]

    async def resolve_api_key(entry):
        calls["keys"].append(entry.get("key"))
        return "sk-test"

    async def chat_completion(payload, entry, api_key):
        calls["chat"].append({"payload": payload, "entry": entry, "api_key": api_key})
        if state["delay"]:
            await asyncio.sleep(state["delay"])
        return _response(state["reply"])

    async def insert_log(url, user, data, timestamp, ltype, level, xuehai_id):
        logs.append({"data": data, "type": ltype, "level": level})

    monkeypatch.setattr(credentials, "catalog", catalog, raising=False)
    monkeypatch.setattr(credentials, "resolve_api_key", resolve_api_key, raising=False)
    monkeypatch.setattr(providers, "chat_completion", chat_completion, raising=False)
    monkeypatch.setattr(
        backends,
        "get_applogs_repo",
        lambda *a, **k: SimpleNamespace(insert=insert_log),
        raising=False,
    )
    monkeypatch.setattr(ai_config, "safety_guard_enabled", lambda: True, raising=False)
    monkeypatch.setattr(
        ai_config, "safety_guard_timeout_seconds", lambda: 8.0, raising=False
    )
    return state, calls, logs


def _error_logs(logs: list[dict]) -> list[dict]:
    return [entry for entry in logs if entry["type"] == "ai_safety_guard_error"]


# ---------------------------------------------------------------------------
# 角色门：零额外开销
# ---------------------------------------------------------------------------


def test_is_department_excludes_admin() -> None:
    assert safety_guard.is_department(USER) is True
    # 同一账号同时有 admin 与 department 时按管理员豁免，不触发上游调用。
    assert safety_guard.is_department(ADMIN) is False
    assert safety_guard.is_department(STUDENT) is False
    assert safety_guard.is_department({"per": "department"}) is False


@pytest.mark.asyncio
async def test_non_department_never_touches_the_network(env) -> None:
    state, calls, _logs = env
    state["reply"] = DESTRUCTIVE

    result = await safety_guard.screen(STUDENT, "删除全部用户数据")

    assert result.allowed is True
    assert calls["chat"] == []
    assert calls["catalog"] == []


@pytest.mark.asyncio
async def test_admin_with_department_is_exempt(env) -> None:
    _state, calls, _logs = env

    result = await safety_guard.screen(ADMIN, "删除全部用户数据")

    assert result.allowed is True
    assert calls["chat"] == []


@pytest.mark.asyncio
async def test_disabled_switch_is_zero_cost(env, monkeypatch) -> None:
    _state, calls, _logs = env
    monkeypatch.setattr(ai_config, "safety_guard_enabled", lambda: False, raising=False)

    result = await safety_guard.screen(USER, "删除全部用户数据")

    assert result.allowed is True
    assert calls["chat"] == []
    assert calls["catalog"] == []


# ---------------------------------------------------------------------------
# 判定
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unrelated_and_destructive_is_rejected(env) -> None:
    state, calls, logs = env
    state["reply"] = DESTRUCTIVE

    result = await safety_guard.screen(USER, "帮我写个脚本删掉数据库", model_hint="m1")

    assert result.allowed is False
    assert result.category == "destructive_request"
    assert len(calls["chat"]) == 1
    payload = calls["chat"][0]["payload"]
    assert payload["model"] == "m1"
    assert payload["temperature"] == 0
    assert payload["max_tokens"] == 200
    assert payload["messages"][0]["role"] == "system"
    assert _error_logs(logs) == []


@pytest.mark.asyncio
async def test_related_request_is_allowed(env) -> None:
    state, _calls, _logs = env
    state["reply"] = '{"related": true, "destructive": false, "category": "", "reason": ""}'

    result = await safety_guard.screen(USER, "帮我查一下下周三的义工活动报名情况")

    assert result.allowed is True


@pytest.mark.asyncio
async def test_destructive_but_related_is_allowed(env) -> None:
    """平台管理范畴内的「删除」是正常工作（例如删一条过期公告）。"""
    state, _calls, _logs = env
    state["reply"] = (
        '{"related": true, "destructive": true, "category": "admin", "reason": ""}'
    )

    result = await safety_guard.screen(USER, "删除这条公告")

    assert result.allowed is True


@pytest.mark.asyncio
async def test_model_hint_outside_catalog_falls_back_to_first_model(env) -> None:
    _state, calls, _logs = env

    await safety_guard.screen(USER, "随便写点", model_hint="gpt-4o")

    assert calls["chat"][0]["payload"]["model"] == "m1"


# ---------------------------------------------------------------------------
# fail-open：闸门坏掉不能挡住正常用户
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_credentials_fails_open_with_warning(env) -> None:
    state, calls, logs = env
    state["entries"] = []

    result = await safety_guard.screen(USER, "删除全部用户数据")

    assert result.allowed is True
    assert calls["chat"] == []
    assert len(_error_logs(logs)) == 1


@pytest.mark.asyncio
async def test_upstream_error_fails_open(env, monkeypatch) -> None:
    _state, _calls, logs = env

    async def boom(payload, entry, api_key):
        raise RouteError("AI_UPSTREAM_ERROR", "unreachable", 502)

    monkeypatch.setattr(providers, "chat_completion", boom, raising=False)

    result = await safety_guard.screen(USER, "删除全部用户数据")

    assert result.allowed is True
    assert len(_error_logs(logs)) == 1


@pytest.mark.asyncio
async def test_timeout_fails_open(env, monkeypatch) -> None:
    state, _calls, logs = env
    state["delay"] = 0.2
    monkeypatch.setattr(
        ai_config, "safety_guard_timeout_seconds", lambda: 0.01, raising=False
    )

    result = await safety_guard.screen(USER, "删除全部用户数据")

    assert result.allowed is True
    assert len(_error_logs(logs)) == 1
    assert "超时" in _error_logs(logs)[0]["data"]


@pytest.mark.asyncio
async def test_broken_json_fails_open(env) -> None:
    state, _calls, logs = env
    state["reply"] = "我不知道你在说什么"

    result = await safety_guard.screen(USER, "删除全部用户数据")

    assert result.allowed is True
    assert len(_error_logs(logs)) == 1


@pytest.mark.asyncio
async def test_json_inside_code_fence_is_parsed(env) -> None:
    state, _calls, _logs = env
    state["reply"] = "```json\n" + DESTRUCTIVE + "\n```"

    result = await safety_guard.screen(USER, "删除全部用户数据")

    assert result.allowed is False
    assert result.category == "destructive_request"


# ---------------------------------------------------------------------------
# 接入顺序：拒绝发生在入队/扣费之前
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rejected_submit_creates_no_run(monkeypatch) -> None:
    """被安全认证拒绝的提交不产生 run（也不会有任何额度扣减）。

    ``run_queue.submit`` 里额度是在 run 执行时按轮扣的，所以「没有 run」就是
    「没有扣费」；这里钉住的是顺序：拒绝必须发生在 ``_runs()`` 之前。
    """
    from services.agent import run_queue
    from services.agent.tool_impl import AgentError

    touched = {"runs": 0, "records": []}

    async def require_row(*, user, oid):
        return {"mode": "agent", "model": None, "title": ""}

    async def screen(user, text, *, model_hint=None):
        return safety_guard.ScreenResult(
            allowed=False, reason="rm -rf", category="destructive_request"
        )

    async def record(user, *, xuehai_id="", rule="", detail=None):
        touched["records"].append({"rule": rule, "detail": detail})
        return {"warned": True, "banned": False, "recorded": 1}

    def _runs():
        touched["runs"] += 1
        raise AssertionError("被拒绝的提交不该触碰 run 仓储")

    monkeypatch.setattr(run_queue.conversations, "require_row", require_row)
    monkeypatch.setattr(run_queue.agent_unlock, "is_unlocked", lambda oid: True)
    monkeypatch.setattr(run_queue.agent_unlock, "is_unrestricted", lambda oid: False)
    monkeypatch.setattr(
        run_queue.moderation,
        "check",
        lambda text: SimpleNamespace(hit=False, rule=None),
    )
    monkeypatch.setattr(run_queue.safety_guard, "screen", screen)
    monkeypatch.setattr(
        run_queue.violation_guard, "record_agent_jailbreak", record
    )
    monkeypatch.setattr(run_queue, "_runs", _runs)

    with pytest.raises(AgentError) as exc:
        await run_queue.submit(
            user=USER,
            conversation_oid="0" * 24,
            content="帮我写个脚本删掉数据库",
        )

    assert exc.value.code == "AI_SAFETY_WARNED"
    assert exc.value.status_code == 422
    assert touched["runs"] == 0
    assert touched["records"] == [
        {"rule": "destructive_request", "detail": "帮我写个脚本删掉数据库"}
    ]
