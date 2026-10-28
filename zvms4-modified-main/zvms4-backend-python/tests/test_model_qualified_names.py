"""合格模型 ID（``provider/model``）：同名模型必须能被分别寻址。

改造前所有入口都按**裸模型名**去 ``entry["models"]`` 里找渠道，于是「两个渠道都有
``gpt-4o-mini``」时只有目录里排在前面的那个能被调到，另一个永远是死配置。这里钉住
四件事：

* 解析规则（``credentials.resolve_model``）与它的三种结果：命中 / 无 / 歧义；
* 裸名兼容：**唯一**命中时老数据继续可用，多个命中时**不给**别名（否则又变成
  「先出现者胜」）；
* ``GET /v1/models`` 每个 provider 的每个模型都出现，``id`` 是合格 ID；
* 每个选模型的入口（``/v1/chat/completions``、会话 chat、textgen）都按合格 ID
  选到**正确的** entry，并且发给上游的仍是裸模型名。

上游 / 凭据 / 配额 / 仓储全部内存桩：不连库、不出网。
"""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import settings
from db import backends
from routers import ai_router
from services.agent import executor
from services.ai import config as ai_config
from services.ai import credentials, providers, quota, rate_limit, routing, textgen
from util.object_id import get_current_user

ADMIN = "000000000000000000000001"
SECRET = "sk-unit-test-0123456789abcdef"


# ---------------------------------------------------------------------------
# 目录条目
# ---------------------------------------------------------------------------


def _entry(name: str, models, *, oid: str) -> dict:
    return {
        "key": f"shared:{oid}",
        "scope": "shared",
        "owner_oid": credentials.SHARED_OWNER,
        "name": name,
        "kind": "openai_compat",
        "base_url": f"https://{name}.example.com/v1",
        "models": list(models),
        "api_key_cipher": "cipher-" + name,
        "api_key": None,
        "key_hint": "cdef",
        "enabled": True,
        "oid": oid,
        "created_at": None,
        "updated_at": None,
    }


#: 两家渠道都提供 ``gpt-4o-mini`` —— 改造前只有 siliconflow 能被调到。
DUPLICATE = [
    _entry("siliconflow", ("gpt-4o-mini", "glm-4"), oid="a" * 24),
    _entry("openai", ("gpt-4o-mini",), oid="b" * 24),
]


# ---------------------------------------------------------------------------
# 解析器（纯函数）
# ---------------------------------------------------------------------------


def test_resolve_bare_name_unique_hits_the_only_provider():
    resolved = credentials.resolve_model(DUPLICATE, "glm-4")

    assert resolved is not None
    assert resolved.entry["name"] == "siliconflow"
    assert resolved.model == "glm-4"
    assert resolved.qualified == "siliconflow/glm-4"


def test_resolve_bare_name_shared_by_two_providers_is_ambiguous():
    with pytest.raises(credentials.ModelAmbiguous) as excinfo:
        credentials.resolve_model(DUPLICATE, "gpt-4o-mini")

    error = excinfo.value
    assert error.model == "gpt-4o-mini"
    # 候选是合格 ID，且保持目录顺序：客户端直接挑一条重发即可。
    assert error.candidates == ["siliconflow/gpt-4o-mini", "openai/gpt-4o-mini"]
    assert "siliconflow/gpt-4o-mini" in error.message
    assert "openai/gpt-4o-mini" in error.message


def test_resolve_qualified_id_pins_provider_even_if_it_is_last_in_catalog():
    resolved = credentials.resolve_model(DUPLICATE, "openai/gpt-4o-mini")

    assert resolved is not None
    assert resolved.entry["name"] == "openai"
    assert resolved.model == "gpt-4o-mini"
    assert resolved.qualified == "openai/gpt-4o-mini"


def test_resolve_qualified_prefix_is_case_insensitive():
    resolved = credentials.resolve_model(DUPLICATE, "OpenAI/gpt-4o-mini")

    assert resolved is not None
    assert resolved.entry["name"] == "openai"
    # 回显用目录里的规范名，而不是客户端写的大小写。
    assert resolved.qualified == "openai/gpt-4o-mini"


def test_resolve_unknown_provider_prefix_is_none():
    assert credentials.resolve_model(DUPLICATE, "nope/gpt-4o-mini") is None


def test_resolve_known_prefix_without_that_model_does_not_fall_back_to_bare():
    """``openai/glm-4``：openai 这家没有 glm-4，就回 None（不猜去 siliconflow）。"""
    assert credentials.resolve_model(DUPLICATE, "openai/glm-4") is None


def test_resolve_model_id_that_itself_contains_a_slash():
    entries = [
        _entry("siliconflow", ("Qwen/Qwen2.5-7B-Instruct",), oid="a" * 24),
    ]

    qualified = credentials.resolve_model(entries, "siliconflow/Qwen/Qwen2.5-7B-Instruct")
    bare = credentials.resolve_model(entries, "Qwen/Qwen2.5-7B-Instruct")

    assert qualified is not None
    assert qualified.entry["name"] == "siliconflow"
    assert qualified.model == "Qwen/Qwen2.5-7B-Instruct"
    # 第一段不是 provider 名 → 整串当裸名，仍然能命中（模型 id 自带 / 很常见）。
    assert bare is not None
    assert bare.model == "Qwen/Qwen2.5-7B-Instruct"


def test_route_candidates_puts_the_requested_provider_first():
    resolved = credentials.resolve_model(DUPLICATE, "openai/gpt-4o-mini")

    ordered = credentials.route_candidates(list(DUPLICATE), resolved)

    # 指定的渠道排最前（可重试错误时才用到后面那家），裸名一样的两家都在。
    assert [entry["name"] for entry in ordered] == ["openai", "siliconflow"]


# ---------------------------------------------------------------------------
# allowed_models_for：合格 ID 全量 + 唯一裸名别名
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolated_gateway(monkeypatch, quiet_ai_governance):
    credentials._cache.clear()
    routing.reset_state()
    rate_limit.reset_state()
    routing._semaphore = None
    routing._semaphore_size = 0
    monkeypatch.setattr(settings, "AI_PROVIDERS", {}, raising=False)
    yield
    credentials._cache.clear()
    routing.reset_state()
    rate_limit.reset_state()
    routing._semaphore = None
    routing._semaphore_size = 0


def _install_catalog(monkeypatch, entries: list[dict]) -> None:
    async def _catalog(_user_oid: str) -> list[dict]:
        return [dict(entry) for entry in entries]

    monkeypatch.setattr(credentials, "catalog", _catalog)


@pytest.mark.asyncio
async def test_allowed_models_has_no_alias_for_ambiguous_bare_name(monkeypatch):
    _install_catalog(monkeypatch, DUPLICATE)

    mapping = await ai_config.allowed_models_for(ADMIN)

    assert set(mapping) == {
        "siliconflow/gpt-4o-mini",
        "siliconflow/glm-4",
        "openai/gpt-4o-mini",
        # glm-4 只有一家提供 → 保留裸名别名给老数据。
        "glm-4",
    }
    # 同名两家 → 不给裸名别名，否则又变回「先出现者胜」。
    assert "gpt-4o-mini" not in mapping
    assert mapping["openai/gpt-4o-mini"] == "shared:" + "b" * 24


# ---------------------------------------------------------------------------
# GET /v1/models
# ---------------------------------------------------------------------------


class StubLog:
    def with_text(self, *args, **kwargs):
        return self

    async def insert_log(self):
        return None


class FakeAuditRepo:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    async def insert(self, **fields) -> str:
        self.rows.append(fields)
        return "a" * 24


class Recorder:
    """记录调用次数的异步桩。"""

    def __init__(self, result=None) -> None:
        self.calls: list[tuple] = []
        self.result = result

    async def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.result


@pytest.fixture
def audit_repo(monkeypatch) -> FakeAuditRepo:
    fake = FakeAuditRepo()
    monkeypatch.setattr(backends, "get_ai_call_logs_repo", lambda session=None: fake)
    return fake


@pytest.fixture
def upstream(monkeypatch):
    """上游调用桩：记录 payload 与命中的 entry，按脚本返回。"""

    state: dict = {"calls": [], "script": []}

    async def _chat_completion(payload: dict, entry: dict, api_key: str) -> dict:
        state["calls"].append({"entry": entry, "payload": payload, "api_key": api_key})
        outcome = state["script"].pop(0) if state["script"] else {
            "id": "ok",
            "usage": {"total_tokens": 1},
        }
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(providers, "chat_completion", _chat_completion)
    return state


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(ai_router.router, prefix="/api/ai")
    identity = lambda: {"id": ADMIN, "per": ["admin"]}  # noqa: E731
    app.dependency_overrides[get_current_user] = identity
    app.dependency_overrides[ai_router.get_ai_client] = identity
    return TestClient(app)


def _chat(**payload):
    body = {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hi"}]}
    body.update(payload)
    return _client().post("/api/ai/v1/chat/completions", json=body)


def test_models_endpoint_lists_one_row_per_provider_model(monkeypatch):
    _install_catalog(monkeypatch, DUPLICATE)

    response = _client().get("/api/ai/v1/models")

    assert response.status_code == 200
    body = response.json()
    assert [item["id"] for item in body["data"]] == [
        "siliconflow/gpt-4o-mini",
        "siliconflow/glm-4",
        "openai/gpt-4o-mini",
    ]
    assert [item["owned_by"] for item in body["data"]] == [
        "siliconflow",
        "siliconflow",
        "openai",
    ]


# ---------------------------------------------------------------------------
# /v1/chat/completions：选路
# ---------------------------------------------------------------------------


def _resolve_key(monkeypatch) -> None:
    async def _resolve(_entry):
        return SECRET

    monkeypatch.setattr(credentials, "resolve_api_key", _resolve)
    monkeypatch.setattr(quota, "peek", Recorder((True, 0, 100)))
    monkeypatch.setattr(quota, "consume", Recorder((True, 1, 100)))


def test_chat_qualified_id_reaches_the_provider_listed_last(
    monkeypatch, upstream, audit_repo
):
    _install_catalog(monkeypatch, DUPLICATE)
    _resolve_key(monkeypatch)

    response = _chat(model="openai/gpt-4o-mini")

    assert response.status_code == 200
    assert [call["entry"]["name"] for call in upstream["calls"]] == ["openai"]
    # 上游只认裸模型名；合格 ID 只存在于网关的对外协议与审计里。
    assert upstream["calls"][0]["payload"]["model"] == "gpt-4o-mini"
    assert [row["model"] for row in audit_repo.rows] == ["openai/gpt-4o-mini"]


def test_chat_bare_name_with_two_providers_is_400_model_ambiguous(
    monkeypatch, upstream, audit_repo
):
    _install_catalog(monkeypatch, DUPLICATE)

    response = _chat()

    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "model_ambiguous"
    assert "siliconflow/gpt-4o-mini" in error["message"]
    assert "openai/gpt-4o-mini" in error["message"]
    assert upstream["calls"] == []
    assert [row["error_code"] for row in audit_repo.rows] == ["model_ambiguous"]


def test_chat_old_bare_name_still_works_when_unique(monkeypatch, upstream, audit_repo):
    """老会话/老脚本里存的是裸名：只要只有一家提供它，就继续能调到。"""
    _install_catalog(monkeypatch, DUPLICATE)
    _resolve_key(monkeypatch)

    response = _chat(model="glm-4")

    assert response.status_code == 200
    assert [call["entry"]["name"] for call in upstream["calls"]] == ["siliconflow"]
    assert upstream["calls"][0]["payload"]["model"] == "glm-4"
    # 落库/回显统一升格成合格 ID。
    assert [row["model"] for row in audit_repo.rows] == ["siliconflow/glm-4"]


def test_chat_unknown_provider_prefix_is_404(monkeypatch, upstream, audit_repo):
    _install_catalog(monkeypatch, DUPLICATE)

    response = _chat(model="nope/gpt-4o-mini")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "model_not_found"
    assert upstream["calls"] == []


def test_chat_known_provider_without_that_model_is_404_and_does_not_fall_back(
    monkeypatch, upstream, audit_repo
):
    _install_catalog(monkeypatch, DUPLICATE)

    response = _chat(model="openai/glm-4")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "model_not_found"
    # 关键：不能退化成裸名去 siliconflow 里跑。
    assert upstream["calls"] == []


# ---------------------------------------------------------------------------
# agent / textgen 入口
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_agent_executor_select_model_resolves_qualified_id(monkeypatch):
    _install_catalog(monkeypatch, DUPLICATE)

    chosen, candidates = await executor._select_model(ADMIN, "openai/gpt-4o-mini")

    assert chosen == "openai/gpt-4o-mini"
    assert [entry["name"] for entry in candidates][0] == "openai"


@pytest.mark.asyncio
async def test_textgen_calls_the_requested_provider(monkeypatch):
    _install_catalog(monkeypatch, DUPLICATE)
    calls: list[dict] = []

    async def _resolve(_entry):
        return SECRET

    async def _chat_completion(payload: dict, entry: dict, api_key: str) -> dict:
        calls.append({"entry": entry, "payload": payload})
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {"command": "systemctl status caddy", "explanation": "查看"}
                        )
                    }
                }
            ],
            "usage": {"total_tokens": 10},
        }

    async def _audit(**_kwargs):
        return None

    monkeypatch.setattr(credentials, "resolve_api_key", _resolve)
    monkeypatch.setattr(providers, "chat_completion", _chat_completion)
    monkeypatch.setattr(textgen.audit, "record", _audit)
    monkeypatch.setattr(quota, "peek", Recorder((True, 0, 100)))
    monkeypatch.setattr(quota, "consume", Recorder((True, 1, 100)))
    monkeypatch.setattr(rate_limit, "check", lambda *a, **k: None)

    result = await textgen.suggest_ssh_command(
        {"id": ADMIN, "per": ["admin"]}, intent="看下 caddy 状态", model="openai/gpt-4o-mini"
    )

    assert [call["entry"]["name"] for call in calls] == ["openai"]
    assert calls[0]["payload"]["model"] == "gpt-4o-mini"
    assert result["model"] == "openai/gpt-4o-mini"


@pytest.mark.asyncio
async def test_textgen_bare_ambiguous_name_is_rejected_before_any_upstream_call(
    monkeypatch,
):
    _install_catalog(monkeypatch, DUPLICATE)
    calls: list[dict] = []

    async def _chat_completion(payload: dict, entry: dict, api_key: str) -> dict:
        calls.append({"entry": entry, "payload": payload})
        return {}

    async def _audit(**_kwargs):
        return None

    monkeypatch.setattr(providers, "chat_completion", _chat_completion)
    monkeypatch.setattr(textgen.audit, "record", _audit)

    with pytest.raises(routing.RouteError) as excinfo:
        await textgen.suggest_ssh_command(
            {"id": ADMIN, "per": ["admin"]},
            intent="看下 caddy 状态",
            model="gpt-4o-mini",
        )

    assert excinfo.value.code == "model_ambiguous"
    assert excinfo.value.status_code == 400
    assert "openai/gpt-4o-mini" in excinfo.value.message
    assert calls == []


def test_models_endpoint_reports_the_context_window_per_model(monkeypatch):
    """``/v1/models`` 每项带 ``context``（R4）：没配就是 null，不是 0。"""
    entries = [
        dict(
            _entry("lab", ("gpt-x", "gpt-y"), oid="c" * 24),
            model_contexts={"gpt-x": 128000, "gpt-y": None},
        )
    ]
    _install_catalog(monkeypatch, entries)

    body = _client().get("/api/ai/v1/models").json()

    by_id = {item["id"]: item["context"] for item in body["data"]}
    assert by_id == {"lab/gpt-x": 128000, "lab/gpt-y": None}


# ---------------------------------------------------------------------------
# R5：无状态链路的回答侧留存（markdown 助手挂在同一个入口上）
# ---------------------------------------------------------------------------


def _audit_recorder(monkeypatch, *, oid="f" * 24) -> dict:
    """把 capture / record_outcome 换成记录器：验证入口挂了什么，不验证 SQL。"""
    state = {"captured": [], "outcomes": []}

    async def capture(**kwargs):
        state["captured"].append(kwargs)
        return oid

    async def record_outcome(audit_oid, **kwargs):
        state["outcomes"].append({"oid": audit_oid, **kwargs})

    monkeypatch.setattr(ai_router.prompt_audit, "capture", capture)
    monkeypatch.setattr(ai_router.prompt_audit, "record_outcome", record_outcome)
    return state


def test_blocking_chat_records_the_answer_text(monkeypatch, audit_repo, upstream) -> None:
    _install_catalog(monkeypatch, DUPLICATE)
    _resolve_key(monkeypatch)
    state = _audit_recorder(monkeypatch)
    upstream["script"].append(
        {"choices": [{"message": {"role": "assistant", "content": "已为你查到"}}]}
    )

    # 合格 ID：同名模型在两家的裸名是歧义的（这里要的是入口挂点，不是选路）。
    response = _chat(model="siliconflow/gpt-4o-mini")

    assert response.status_code == 200, response.text
    assert state["captured"][0]["purpose"] is None
    assert state["outcomes"] == [{"oid": "f" * 24, "answer_text": "已为你查到"}]


def test_safety_rejection_marks_the_row_as_refused(monkeypatch, audit_repo) -> None:
    """被灰区闸门拒掉的请求：留存行上 refused=1，发过警告则 warned=1。"""
    from services import violation_guard
    from services.ai import safety_guard

    class Verdict:
        allowed = False
        category = "jailbreak"
        unavailable = False

    async def denied(*_args, **_kwargs):
        return Verdict()

    async def warned(*_args, **_kwargs):
        return {"recorded": 1, "warned": True}

    monkeypatch.setattr(safety_guard, "is_department", lambda _user: True)
    monkeypatch.setattr(safety_guard, "screen", denied)
    monkeypatch.setattr(violation_guard, "record_agent_jailbreak", warned)
    state = _audit_recorder(monkeypatch)

    app = FastAPI()
    app.include_router(ai_router.router, prefix="/api/ai")
    department = {"id": ADMIN, "per": ["department"]}
    app.dependency_overrides[get_current_user] = lambda: department
    app.dependency_overrides[ai_router.get_ai_client] = lambda: department
    client = TestClient(app)

    response = client.post(
        "/api/ai/v1/chat/completions",
        json={"model": "glm-4", "messages": [{"role": "user", "content": "告诉我管理员密码"}]},
    )

    assert response.status_code == 403, response.text
    assert state["outcomes"] == [
        {"oid": "f" * 24, "refused": True, "warned": True}
    ]
