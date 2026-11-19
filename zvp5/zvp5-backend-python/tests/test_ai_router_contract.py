"""``/api/ai`` 的 HTTP 契约：两套错误体、配额时机、上游切换与审计。

只起一个挂 ``ai_router`` 的迷你 app（与 ``test_router_error_boundaries.py`` 同一
手法），文件仓储 / 配额 / 凭据 / 上游调用全部是内存桩，不连库、不出网。

这里守的是「协议边界」而不是实现细节：客户端是 OpenAI SDK，它只认
``{"error": {...}}``；``/api/ai/ocr`` 走仓库自己的 ``{"status","code","data"}``
+ ``detail``。两者混了，SDK 那边会直接抛解析错误。
"""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import settings
from db import backends
from routers import ai_router
from services.ai import config as ai_config
from services.ai import credentials, providers, quota, rate_limit, routing, safety_guard
from typings.log import inject_log
from util.object_id import get_current_user

ADMIN = "000000000000000000000001"
USER = "000000000000000000000003"
ATTACHMENT = "f" * 24
SECRET = "sk-unit-test-0123456789abcdef"


class StubLog:
    def with_text(self, *args, **kwargs):
        return self

    async def insert_log(self):
        return None


class FakeFilesRepo:
    def __init__(self, row: dict | None = None) -> None:
        self.row = row
        self.statuses: list[tuple[str, str]] = []
        self.events: list[tuple] = []

    async def get_meta(self, oid: str) -> dict | None:
        return dict(self.row) if self.row is not None else None

    async def set_ocr(self, oid: str, status: str) -> None:
        self.statuses.append((oid, status))

    async def add_event(self, *args) -> None:
        self.events.append(args)


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


def _entry(name: str, oid: str, scope: str = "shared", models=("gpt-x",)) -> dict:
    return {
        "key": f"{scope}:{oid}",
        "scope": scope,
        "owner_oid": ADMIN if scope == "personal" else credentials.SHARED_OWNER,
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


def _client(user: dict | None = None) -> TestClient:
    app = FastAPI()
    app.include_router(ai_router.router, prefix="/api/ai")
    identity = lambda: user or {"id": ADMIN, "per": ["admin"]}  # noqa: E731
    app.dependency_overrides[get_current_user] = identity
    # ``/v1/*`` 的鉴权入口是 get_ai_client（API key 或 JWT）；这里把两条路径都
    # 固定成同一个身份，鉴权分支本身由 tests/test_ai_api_keys.py 覆盖。
    app.dependency_overrides[ai_router.get_ai_client] = identity
    app.dependency_overrides[inject_log] = lambda: StubLog()
    return TestClient(app)


@pytest.fixture(autouse=True)
def _isolated_gateway(monkeypatch, quiet_ai_governance):
    credentials._cache.clear()
    routing.reset_state()
    # 频控是进程级滑动窗口：同一 oid 的 OCR 请求数会跨用例累积，也会被同进程的
    # 其它测试文件（如 test_ai_ocr_wiring.py）提前消耗。每个用例从空窗口开始。
    rate_limit.reset_state()
    # 信号量会绑定 event loop；TestClient 每个请求一个新 loop，跨用例复用会炸。
    routing._semaphore = None
    routing._semaphore_size = 0
    monkeypatch.setattr(settings, "AI_PROVIDERS", {}, raising=False)
    yield
    credentials._cache.clear()
    routing.reset_state()
    rate_limit.reset_state()
    routing._semaphore = None
    routing._semaphore_size = 0


@pytest.fixture
def audit_repo(monkeypatch) -> FakeAuditRepo:
    fake = FakeAuditRepo()
    monkeypatch.setattr(backends, "get_ai_call_logs_repo", lambda session=None: fake)
    return fake


@pytest.fixture
def files_repo(monkeypatch) -> FakeFilesRepo:
    fake = FakeFilesRepo()
    monkeypatch.setattr(backends, "get_files_repo", lambda session=None: fake)
    return fake


@pytest.fixture
def catalog(monkeypatch):
    """按用例替换凭据目录：``catalog(entries)`` 装上，返回该列表。"""
    state = {"entries": []}

    async def _catalog(user_oid: str) -> list[dict]:
        return [dict(entry) for entry in state["entries"]]

    monkeypatch.setattr(credentials, "catalog", _catalog)

    def install(entries: list[dict]) -> None:
        state["entries"] = entries

    install([])
    return install


@pytest.fixture
def upstream(monkeypatch):
    """上游调用桩：``script(...)`` 按调用顺序决定返回值/异常。"""
    calls: list[dict] = []
    script: list = []

    async def _chat_completion(payload: dict, entry: dict, api_key: str) -> dict:
        calls.append({"entry": entry, "api_key": api_key, "payload": payload})
        outcome = script.pop(0) if script else {"id": "ok", "usage": {"total_tokens": 1}}
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(providers, "chat_completion", _chat_completion)

    def run(*outcomes) -> list[dict]:
        script.extend(outcomes)
        return calls

    return run


# ---------------------------------------------------------------------------
# POST /api/ai/ocr
# ---------------------------------------------------------------------------


def _ocr(files_repo, mime="image/png", ocr_status=None):
    files_repo.row = {
        "mime": mime,
        "owner_oid": USER,
        "ocr_status": ocr_status,
        "size_bytes": 1024,
    }
    return files_repo


@pytest.fixture
def ocr_deps(monkeypatch, files_repo):
    """OCR 入队的依赖：读权限、用户额度（peek/consume）、站点额度、引擎表、后台任务。

    ``engines`` 默认给一行（后面车道 3a 起的真实现要求「没有 enabled 引擎就 503」，
    这里给了才能覆盖到正常路径）；``site_results`` 是可用站点额度的队列，空队列时
    按「还够」处理。
    """
    state: dict = {
        "readable": True,
        "consume_result": (True, 1, 100),
        "peek_result": (True, 0, 100),
        "site_results": [],
        "background": [],
        "engines": [
            {
                "_id": "e" * 24,
                "name": "primary",
                "kind": "tencent",
                "config": {},
                "secret_cipher": None,
                "key_hint": None,
                "enabled": True,
                "priority": 100,
                "created_at": None,
                "updated_at": None,
            }
        ],
        "selected": [],
    }
    consume = Recorder()
    peek = Recorder()
    site = Recorder()
    refund = Recorder()

    async def _can_read(user, row, class_id):
        return state["readable"]

    async def _peek(user_oid, kind, amount, **_kwargs):
        peek.calls.append((user_oid, kind, amount))
        return state["peek_result"]

    async def _consume(user_oid, kind, amount, **_kwargs):
        consume.calls.append((user_oid, kind, amount))
        return state["consume_result"]

    async def _site_consume(engine_kind, amount=1):
        site.calls.append((engine_kind, amount))
        return state["site_results"].pop(0) if state["site_results"] else (True, 1, 100)

    async def _site_refund(engine_kind, units=1):
        refund.calls.append((engine_kind, units))
        return 1

    class _EngineRepo:
        async def list_all(self, *, enabled_only: bool = False) -> list[dict]:
            return [dict(row) for row in state["engines"]]

    def _process(oid, engine_kind=None, engine_name=None, user_oid=None):
        state["background"].append(oid)
        state["selected"].append((engine_kind, engine_name))

    monkeypatch.setattr(ai_router.ai_ocr, "can_read_attachment", _can_read)
    monkeypatch.setattr(quota, "peek", _peek)
    monkeypatch.setattr(quota, "consume", _consume)
    monkeypatch.setattr(quota, "site_consume", _site_consume)
    monkeypatch.setattr(quota, "site_refund", _site_refund)
    monkeypatch.setattr(
        backends, "get_ai_ocr_engines_repo", lambda session=None: _EngineRepo()
    )
    monkeypatch.setattr("routers.ai.ocr.process_ocr", _process)
    state["consume"] = consume
    state["peek"] = peek
    state["site"] = site
    state["refund"] = refund
    return state


def test_ocr_rejects_non_image_mime(files_repo, ocr_deps, audit_repo):
    _ocr(files_repo, mime="application/pdf")

    response = _client().post("/api/ai/ocr", json={"attachment_oid": ATTACHMENT})

    assert response.status_code == 400
    assert response.json()["detail"] == "AI_UNSUPPORTED_MEDIA"
    assert ocr_deps["consume"].calls == []


def test_ocr_missing_attachment_is_404(files_repo, ocr_deps, audit_repo):
    files_repo.row = None

    response = _client().post("/api/ai/ocr", json={"attachment_oid": ATTACHMENT})

    assert response.status_code == 404
    assert ocr_deps["consume"].calls == []


def test_ocr_without_read_permission_is_403(files_repo, ocr_deps, audit_repo):
    _ocr(files_repo)
    ocr_deps["readable"] = False

    response = _client().post("/api/ai/ocr", json={"attachment_oid": ATTACHMENT})

    assert response.status_code == 403
    assert ocr_deps["consume"].calls == []


def test_ocr_exhausted_quota_is_429_with_headers(files_repo, ocr_deps, audit_repo):
    _ocr(files_repo)
    ocr_deps["consume_result"] = (False, 7, 7)

    response = _client().post("/api/ai/ocr", json={"attachment_oid": ATTACHMENT})

    assert response.status_code == 429
    assert response.json()["detail"] == "AI_QUOTA_EXCEEDED"
    assert response.headers["X-AI-Quota-Used"] == "7"
    assert response.headers["X-AI-Quota-Limit"] == "7"
    # 用户额度竞态失败时把已占的站点额度退回去。
    assert ocr_deps["refund"].calls == [("tencent", 1)]
    # 配额被拒就不能把附件挂进队列（否则前端看到 pending 却永远等不到结果）。
    assert files_repo.statuses == []
    assert ocr_deps["background"] == []


def test_ocr_happy_path_charges_exactly_once(files_repo, ocr_deps, audit_repo):
    _ocr(files_repo)

    response = _client().post("/api/ai/ocr", json={"attachment_oid": ATTACHMENT})

    assert response.status_code == 202
    assert response.json()["data"]["status"] == "pending"
    assert ocr_deps["consume"].calls == [(ADMIN, "af", ai_config.AI_OCR_AF_COST)]
    assert files_repo.statuses == [(ATTACHMENT, "pending")]
    assert ocr_deps["background"] == [ATTACHMENT]
    # 引擎凭证是站点级的（ai_ocr_engines 全站生效）：审计必须显式记 server。
    assert [row["key_scope"] for row in audit_repo.rows] == ["server"]
    assert [row["capability"] for row in audit_repo.rows] == ["ocr"]


def test_ocr_already_processing_does_not_charge_again(files_repo, ocr_deps, audit_repo):
    _ocr(files_repo, ocr_status="processing")

    response = _client().post("/api/ai/ocr", json={"attachment_oid": ATTACHMENT})

    # 「处理中」是幂等分支：本次没有新受理、也没扣配额，用 200 表示已经是这个状态。
    assert response.status_code == 200
    assert response.json()["data"]["status"] == "processing"
    assert ocr_deps["consume"].calls == []
    assert files_repo.statuses == []
    assert ocr_deps["background"] == []


# ---------------------------------------------------------------------------
# GET /api/ai/health
# ---------------------------------------------------------------------------


def test_health_is_admin_only(catalog):
    response = _client({"id": USER, "per": []}).get("/api/ai/health")

    assert response.status_code == 403


def test_health_never_echoes_keys(catalog):
    # 故意在 entry 里塞明文 api_key：即使目录被污染，health 也只投影固定字段。
    catalog(
        [
            {**_entry("mine", "a" * 24, scope="personal"), "api_key": SECRET},
            {
                **_entry("team", "b" * 24, scope="shared", models=("gpt-x", "gpt-y")),
                "api_key": SECRET,
            },
        ]
    )

    response = _client().get("/api/ai/health")

    assert response.status_code == 200
    body = response.json()["data"]
    assert body["credentials"]["personal"] == 1
    assert body["credentials"]["shared"] == 1
    assert body["models"] == ["gpt-x", "gpt-y"]
    assert SECRET not in response.text
    assert "cipher-" not in response.text
    assert "api_key" not in response.text


def test_health_does_not_echo_the_dead_routes_config(catalog):
    """``settings.AI_ROUTES`` 配了不生效：既不能当生效项回显，就该从响应里消失。"""
    catalog([])

    body = _client().get("/api/ai/health").json()["data"]

    assert "routes" not in body
    # 其余运维字段还在（删的是死配置，不是整个视图）。
    assert set(body) >= {
        "enabled",
        "max_concurrency",
        "providers",
        "cooldown_seconds",
        "models",
        "quota_defaults",
        "credentials",
    }


# ---------------------------------------------------------------------------
# GET /api/ai/v1/models
# ---------------------------------------------------------------------------


def test_models_endpoint_lists_every_provider_model(catalog):
    """同名模型不再去重：两个渠道的 ``m2`` 都要出现，各自带自己的合格 ID。"""
    catalog(
        [
            _entry("mine", "a" * 24, scope="personal", models=("m1", "m2")),
            _entry("team", "b" * 24, scope="shared", models=("m2", "m3")),
            _entry("empty", "c" * 24, scope="shared", models=()),
        ]
    )

    response = _client().get("/api/ai/v1/models")

    assert response.status_code == 200
    body = response.json()
    assert body["object"] == "list"
    assert [item["id"] for item in body["data"]] == [
        "mine/m1",
        "mine/m2",
        "team/m2",
        "team/m3",
    ]
    # owned_by 是 provider 名：客户端据此知道该 ID 的前缀写什么。
    assert [item["owned_by"] for item in body["data"]] == [
        "mine",
        "mine",
        "team",
        "team",
    ]
    assert all(item["object"] == "model" for item in body["data"])


# ---------------------------------------------------------------------------
# POST /api/ai/v1/chat/completions
# ---------------------------------------------------------------------------


def _chat(client=None, **payload):
    body = {"model": "gpt-x", "messages": [{"role": "user", "content": "hi"}]}
    body.update(payload)
    return (client or _client()).post("/api/ai/v1/chat/completions", json=body)


def test_chat_without_any_provider_is_503_in_openai_shape(catalog, audit_repo):
    catalog([])

    response = _chat()

    assert response.status_code == 503
    error = response.json()["error"]
    assert error["code"] == "AI_NO_PROVIDER"
    assert error["type"] == "server_error"
    assert "detail" not in response.json()  # 不能混进仓库自己的错误体
    assert [row["status"] for row in audit_repo.rows] == ["rejected"]


def test_chat_unknown_model_is_404_model_not_found(catalog, upstream, audit_repo):
    catalog([_entry("team", "b" * 24, models=("gpt-x",))])
    calls = upstream()

    response = _chat(model="gpt-nope")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "model_not_found"
    assert calls == []  # 选错模型不该打上游


def test_chat_personal_key_is_not_billed(catalog, upstream, monkeypatch, audit_repo):
    catalog([_entry("mine", "a" * 24, scope="personal")])
    upstream({"id": "ok", "usage": {"total_tokens": 10}})

    async def _resolve(entry):
        return SECRET

    peek = Recorder((True, 0, 100))
    consume = Recorder((True, 1, 100))
    monkeypatch.setattr(credentials, "resolve_api_key", _resolve)
    monkeypatch.setattr(quota, "peek", peek)
    monkeypatch.setattr(quota, "consume", consume)

    response = _chat()

    assert response.status_code == 200
    assert response.json()["id"] == "ok"
    # 个人 key 是管理员自己付钱：预检与扣减都不该发生。
    assert peek.calls == []
    assert consume.calls == []
    assert [row["key_scope"] for row in audit_repo.rows] == ["personal"]


def test_chat_shared_key_is_billed_once(catalog, upstream, monkeypatch, audit_repo):
    catalog([_entry("team", "b" * 24, scope="shared")])
    upstream({"id": "ok", "usage": {"total_tokens": 10}})

    async def _resolve(entry):
        return SECRET

    peek = Recorder((True, 3, 100))
    consume = Recorder((True, 4, 100))
    monkeypatch.setattr(credentials, "resolve_api_key", _resolve)
    monkeypatch.setattr(quota, "peek", peek)
    monkeypatch.setattr(quota, "consume", consume)

    response = _chat()

    assert response.status_code == 200
    assert len(peek.calls) == 1
    assert len(consume.calls) == 1
    assert consume.calls[0][0] == (ADMIN, "af", 1)  # 10 token -> 1 个单位
    assert [row["key_scope"] for row in audit_repo.rows] == ["shared"]


def test_chat_quota_exhausted_is_429_with_headers(catalog, upstream, monkeypatch):
    catalog([_entry("team", "b" * 24, scope="shared")])
    calls = upstream()
    peek = Recorder((False, 100, 100))
    monkeypatch.setattr(quota, "peek", peek)

    response = _chat()

    assert response.status_code == 429
    assert response.json()["error"]["code"] == "insufficient_quota"
    assert response.headers["X-AI-Quota-Used"] == "100"
    assert response.headers["X-AI-Quota-Limit"] == "100"
    assert calls == []  # 先预检再花钱


def test_chat_fails_over_on_upstream_5xx(catalog, upstream, monkeypatch, audit_repo):
    """合格 ID 指定 first 时它是首选，可重试错误仍会兜底到同一模型名的第二家。

    裸名在这里是**不可用**的：两家都叫 ``gpt-x``，按裸名选等于让目录顺序决定渠道
    （另一个渠道永远调不到），所以客户端要用 ``first/gpt-x``。
    """
    catalog(
        [
            _entry("first", "a" * 24, scope="shared"),
            _entry("second", "b" * 24, scope="shared"),
        ]
    )

    async def _resolve(entry):
        return SECRET

    monkeypatch.setattr(credentials, "resolve_api_key", _resolve)
    monkeypatch.setattr(quota, "peek", Recorder((True, 0, 100)))
    monkeypatch.setattr(quota, "consume", Recorder((True, 1, 100)))
    calls = upstream(
        routing.RouteError("AI_UPSTREAM_ERROR", "upstream failed", 502),
        {"id": "second-ok", "usage": {"total_tokens": 1}},
    )

    response = _chat(model="first/gpt-x")

    assert response.status_code == 200
    assert response.json()["id"] == "second-ok"
    assert [call["entry"]["name"] for call in calls] == ["first", "second"]
    # 上游请求体里永远是裸模型名：合格 ID 是我们自己的命名空间。
    assert [call["payload"]["model"] for call in calls] == ["gpt-x", "gpt-x"]
    # 第一次失败也要留审计（否则「用户说慢」时看不到中间那次 502）。
    assert [(row["provider"], row["status"]) for row in audit_repo.rows] == [
        ("first", "error"),
        ("second", "success"),
    ]


def test_chat_bare_name_shared_by_two_providers_is_400_model_ambiguous(
    catalog, upstream, audit_repo
):
    """裸名命中多家 → 400 并列出候选；不允许「先出现者胜」再决定渠道。"""
    catalog(
        [
            _entry("first", "a" * 24, scope="shared"),
            _entry("second", "b" * 24, scope="shared"),
        ]
    )
    calls = upstream()

    response = _chat()

    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "model_ambiguous"
    assert "first/gpt-x" in error["message"]
    assert "second/gpt-x" in error["message"]
    assert calls == []  # 歧义不该让任何一家先花钱
    assert [row["error_code"] for row in audit_repo.rows] == ["model_ambiguous"]


def test_chat_does_not_fail_over_on_401(catalog, upstream, monkeypatch, audit_repo):
    catalog(
        [
            _entry("first", "a" * 24, scope="shared"),
            _entry("second", "b" * 24, scope="shared"),
        ]
    )

    async def _resolve(entry):
        return SECRET

    monkeypatch.setattr(credentials, "resolve_api_key", _resolve)
    monkeypatch.setattr(quota, "peek", Recorder((True, 0, 100)))
    monkeypatch.setattr(quota, "consume", Recorder((True, 1, 100)))
    calls = upstream(routing.RouteError("AI_UPSTREAM_AUTH", "upstream rejected", 502))

    response = _chat(model="first/gpt-x")

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "AI_UPSTREAM_AUTH"
    # 同一把 key 到哪都是错：换 provider 只是多花一次往返。
    assert len(calls) == 1
    assert [row["key_scope"] for row in audit_repo.rows] == ["shared"]


def test_chat_success_carries_request_id(catalog, upstream, monkeypatch, audit_repo):
    catalog([_entry("mine", "a" * 24, scope="personal")])
    upstream({"id": "ok", "usage": {"total_tokens": 1}})

    async def _resolve(entry):
        return SECRET

    monkeypatch.setattr(credentials, "resolve_api_key", _resolve)

    response = _chat()

    assert response.status_code == 200
    assert response.headers["X-AI-Request-Id"]
    assert response.headers["X-AI-Request-Id"] == audit_repo.rows[0]["request_id"]


def test_chat_error_also_carries_request_id(catalog, audit_repo):
    catalog([])

    response = _chat()

    assert response.headers["X-AI-Request-Id"]
    assert response.headers["X-AI-Request-Id"] == audit_repo.rows[0]["request_id"]


def test_chat_disabled_gateway_is_503(catalog, monkeypatch, audit_repo):
    monkeypatch.setattr(ai_config, "is_enabled", lambda: False)

    response = _chat()

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "AI_DISABLED"


def test_chat_audit_never_records_the_api_key(catalog, upstream, monkeypatch, audit_repo):
    catalog([_entry("team", "b" * 24, scope="shared")])
    upstream({"id": "ok", "usage": {"total_tokens": 1}})

    async def _resolve(entry):
        return SECRET

    monkeypatch.setattr(credentials, "resolve_api_key", _resolve)
    monkeypatch.setattr(quota, "peek", Recorder((True, 0, 100)))
    monkeypatch.setattr(quota, "consume", Recorder((True, 1, 100)))

    assert _chat().status_code == 200

    dumped = json.dumps(audit_repo.rows, default=str, ensure_ascii=False)
    assert SECRET not in dumped
    assert "cipher-" not in dumped
    assert "Bearer" not in dumped


# ---------------------------------------------------------------------------
# 角色门禁（AI_ALLOWED_ROLES）
# ---------------------------------------------------------------------------
#
# 门禁是「谁能花平台的 AI 额度」的唯一闸门：放进来就等于允许他消耗上游费用。
# 两个协议面的拒绝体**故意不同**——`/ocr` 走仓库的 `detail`，`/v1/*` 走 OpenAI
# SDK 认得的 `{"error": {...}}`；混用会让 SDK 抛解析错误而不是显示 403。


def _role_client(*roles: str) -> TestClient:
    return _client({"id": USER, "per": list(roles)})


def test_ocr_allows_admin(files_repo, ocr_deps, audit_repo):
    _ocr(files_repo)

    response = _client({"id": ADMIN, "per": ["admin"]}).post(
        "/api/ai/ocr", json={"attachment_oid": ATTACHMENT}
    )

    assert response.status_code in (200, 202)
    assert ocr_deps["consume"].calls == [(ADMIN, "af", ai_config.AI_OCR_AF_COST)]


def test_ocr_allows_department(files_repo, ocr_deps, audit_repo):
    """系管也在白名单里：OCR 是日常教学/办公用途，不是管理员专属。"""
    _ocr(files_repo)

    response = _role_client("department").post(
        "/api/ai/ocr", json={"attachment_oid": ATTACHMENT}
    )

    assert response.status_code in (200, 202)
    assert ocr_deps["consume"].calls == [(USER, "af", ai_config.AI_OCR_AF_COST)]


def test_ocr_denies_student_in_zvp_error_shape(files_repo, ocr_deps, audit_repo):
    _ocr(files_repo)

    response = _role_client("student").post(
        "/api/ai/ocr", json={"attachment_oid": ATTACHMENT}
    )

    assert response.status_code == 403
    body = response.json()
    assert body["detail"] == "AI_FORBIDDEN"
    # 仓库自己的错误体：不能混进 OpenAI 的 error 壳。
    assert "error" not in body


def test_ocr_denies_secretary(files_repo, ocr_deps, audit_repo):
    """团支书不在白名单：能读附件不等于能花 AI 额度。"""
    _ocr(files_repo)

    response = _role_client("secretary").post(
        "/api/ai/ocr", json={"attachment_oid": ATTACHMENT}
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "AI_FORBIDDEN"


def test_models_denies_student_in_openai_error_shape(catalog):
    catalog([_entry("team", "b" * 24, scope="shared", models=("gpt-x",))])

    response = _role_client("student").get("/api/ai/v1/models")

    assert response.status_code == 403
    body = response.json()
    assert body["error"]["code"] == "AI_FORBIDDEN"
    # SDK 只认 error 壳：带上 detail 会让它把 403 当成解析失败。
    assert "detail" not in body


def test_models_allows_department(catalog):
    catalog([_entry("team", "b" * 24, scope="shared", models=("gpt-x",))])

    response = _role_client("department").get("/api/ai/v1/models")

    assert response.status_code == 200
    # 对外 id 是合格 ID（provider/model）：同名模型分渠道时只有它不歧义。
    assert [item["id"] for item in response.json()["data"]] == ["team/gpt-x"]


def test_gate_roles_are_configurable(catalog, monkeypatch):
    """白名单可被 settings 覆盖：只留 admin 时 department 立刻被拒。"""
    catalog([_entry("team", "b" * 24, scope="shared", models=("gpt-x",))])
    monkeypatch.setattr(ai_config, "allowed_roles", lambda: frozenset({"admin"}))

    assert _role_client("department").get("/api/ai/v1/models").status_code == 403
    assert _role_client("admin").get("/api/ai/v1/models").status_code == 200


# ---------------------------------------------------------------------------
# chat 面的角色门：只对学生会及以上开放（用户拍板）
# ---------------------------------------------------------------------------
#
# 「chat 也是 AI 功能」：学生 / 团支书一律进不来（403 ``AI_FORBIDDEN``，OpenAI
# 错误壳），学生会（department）进来之后走一次 LLM 前置判定，管理员天然旁路。
# 角色口径只有 ``AI_ALLOWED_ROLES`` 一处，不另抄一份表。


def _key_client() -> TestClient:
    """不覆盖 ``get_ai_client`` 的客户端：``install_api_key`` 走真实 key 分支。"""
    app = FastAPI()
    app.include_router(ai_router.router, prefix="/api/ai")
    # 管理面（非 ``/v1``）仍需要 JWT 身份；本组用例只打 ``/v1/chat/completions``。
    app.dependency_overrides[get_current_user] = lambda: {"id": USER, "per": []}
    app.dependency_overrides[inject_log] = lambda: StubLog()
    return TestClient(app)


def _key_chat(client: TestClient, token: str):
    return client.post(
        "/api/ai/v1/chat/completions",
        json={"model": "gpt-x", "messages": [{"role": "user", "content": "hi"}]},
        headers={"Authorization": f"Bearer {token}"},
    )


def test_chat_denies_secretary_in_openai_error_shape(catalog, upstream, audit_repo):
    """团支书不是「学生会及以上」：能读附件（ai.ocr）不等于能用 chat。"""
    catalog([_entry("team", "b" * 24, models=("gpt-x",))])
    calls = upstream()

    response = _role_client("secretary").post(
        "/api/ai/v1/chat/completions",
        json={"model": "gpt-x", "messages": [{"role": "user", "content": "hi"}]},
    )

    assert response.status_code == 403
    body = response.json()
    assert body["error"]["code"] == "AI_FORBIDDEN"
    assert "detail" not in body  # SDK 只认 error 壳
    assert calls == []
    assert [row["status"] for row in audit_repo.rows] == ["rejected"]


def test_chat_denies_student_api_key_owner(
    catalog, upstream, monkeypatch, audit_repo
):
    """``zvk_`` key 用 owner 的 ``per`` 判定：student 的 key 同样进不来。"""
    catalog([_entry("team", "b" * 24, models=("gpt-x",))])
    calls = upstream()

    async def _authenticate(_token):
        return {
            "id": USER,
            "per": ["student"],
            "perm": [],
            "class_id": None,
            "api_key_oid": "k" * 24,
            "api_key_name": "cli",
        }

    monkeypatch.setattr(ai_router.api_keys, "authenticate", _authenticate)

    response = _key_chat(_key_client(), "zvk_" + "a" * 43)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "AI_FORBIDDEN"
    assert calls == []
    assert [row["status"] for row in audit_repo.rows] == ["rejected"]


def test_chat_department_is_screened_by_the_llm(
    catalog, upstream, monkeypatch, audit_repo
):
    """学生会进来之后走一次 LLM 前置判定：无关即 403 + 记证据，不打上游。"""
    catalog([_entry("team", "b" * 24, models=("gpt-x",))])
    calls = upstream()
    seen: list[dict] = []

    async def _screen(user, text, *, model_hint=None):
        seen.append({"user": dict(user), "text": text, "model_hint": model_hint})
        # 判定成立：这是「真的无关」，不是「判定跑不起来」，所以会写违规证据。
        return safety_guard.ScreenResult(
            allowed=False, category="unrelated_request", reason="与平台无关"
        )

    recorded = Recorder({"recorded": 1, "warned": True, "banned": True})
    monkeypatch.setattr(ai_router.safety_guard, "screen", _screen)
    monkeypatch.setattr(ai_router.violation_guard, "record_agent_jailbreak", recorded)

    response = _role_client("department").post(
        "/api/ai/v1/chat/completions",
        json={"model": "gpt-x", "messages": [{"role": "user", "content": "陪我聊聊电影"}]},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == safety_guard.REJECTED_CODE
    assert seen == [
        {
            "user": {"id": USER, "per": ["department"]},
            "text": "陪我聊聊电影",
            "model_hint": "gpt-x",
        }
    ]
    assert [call[1]["rule"] for call in recorded.calls] == ["unrelated_request"]
    assert calls == []


def test_chat_admin_bypasses_the_llm_screen(catalog, upstream, monkeypatch):
    """管理员天然旁路：不跑判定，正常调用照旧。"""

    async def boom(*_args, **_kwargs):
        raise AssertionError("admin 不该触发安全判定")

    catalog([_entry("team", "b" * 24, models=("gpt-x",))])
    upstream({"id": "ok", "usage": {"total_tokens": 10}})

    async def _resolve(entry):
        return SECRET

    monkeypatch.setattr(ai_router.safety_guard, "screen", boom)
    monkeypatch.setattr(credentials, "resolve_api_key", _resolve)
    monkeypatch.setattr(quota, "peek", Recorder((True, 0, 100)))
    monkeypatch.setattr(quota, "consume", Recorder((True, 1, 100)))

    response = _chat(_client({"id": ADMIN, "per": ["admin"]}))

    assert response.status_code == 200
    assert response.json()["id"] == "ok"


def test_denied_chat_does_not_charge_or_call_upstream_but_leaves_an_audit_row(
    catalog, upstream, monkeypatch, audit_repo
):
    """被门禁挡下的请求不扣额度、不打上游，但**要留一行 rejected 审计**。

    审计从「只记成功的调用」改成「记每一次尝试」是刻意的：没有这一行，
    「某个角色一直在试但一直被拒」在后台完全看不见（这正是要审计的滥用信号）。
    """
    catalog([_entry("team", "b" * 24, scope="shared", models=("gpt-x",))])
    calls = upstream({"id": "ok", "usage": {"total_tokens": 1}})

    async def _resolve(entry):
        return SECRET

    peek = Recorder((True, 0, 100))
    consume = Recorder((True, 1, 100))
    monkeypatch.setattr(credentials, "resolve_api_key", _resolve)
    monkeypatch.setattr(quota, "peek", peek)
    monkeypatch.setattr(quota, "consume", consume)

    response = _role_client("student").post(
        "/api/ai/v1/chat/completions",
        json={"model": "gpt-x", "messages": [{"role": "user", "content": "hi"}]},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "AI_FORBIDDEN"
    assert calls == []
    assert peek.calls == []
    assert consume.calls == []
    assert [(row["status"], row["error_code"], row["http_status"]) for row in audit_repo.rows] == [
        ("rejected", "AI_FORBIDDEN", 403)
    ]
    # 拒绝行不带上游痕迹：没有 provider / attempt_no（一次都没打过上游）。
    assert audit_repo.rows[0]["provider"] is None
    assert audit_repo.rows[0]["attempt_no"] is None
    assert audit_repo.rows[0]["units"] == 0
