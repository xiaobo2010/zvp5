"""「从上游拉取模型元数据」：``POST /api/ai/models/discover`` + ``providers.list_models``。

守四件事：

* **预览不写库**：``apply=False`` 只回上游事实与 ``in_credential``，仓储一次都不碰；
* **回写面严格受限**：``apply=True`` 只改凭据里**已有**模型的 ``context``——不新增
  条目、不改顺序，且只有上游真的给了窗口才算 ``applied``；
* **门禁与文案**：admin + 密码再认证；未知渠道 404、服务端作用域 400、上游失败
  502，detail 一律中文；
* **密钥不外泄**：响应体与日志里都不许出现明文 key（上游报错体常常回显请求头，
  所以 ``/models`` 的失败路径刻意不记报文）。

上游一律用桩：``providers.list_models`` 换成返回值/异常，或直接桩 httpx 的
``MockTransport``（``list_models`` 本体要验时）。不连库、不出网。
"""

from __future__ import annotations

import logging

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import settings
from db import backends
from routers import ai_models_router
from services.ai import credentials, providers
from services.ai.credentials import CredentialError
from services.ai.routing import RouteError
from util.object_id import get_current_user

PREFIX = "/api/ai/models"
ADMIN = {"id": "64b7f0c2e1a3b4c5d6e7fa01", "per": ["admin"]}
STUDENT = {"id": "64b7f0c2e1a3b4c5d6e7fa02", "per": ["student"]}
SECRET = "sk-discover-unit-test-0123456789abcdef"
BASE_URL = "https://api.example.com/v1"


class _FakeRepo:
    """``AICredentialsRepo`` 的内存实现：行形状与 Oracle 版 ``_row`` 一致。"""

    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}
        self.updates: list[tuple[str, dict]] = []

    def seed(self, oid: str, *, name: str, models, scope: str = "shared", **fields):
        row = {
            "_id": oid,
            "scope": scope,
            "owner_oid": credentials.SHARED_OWNER if scope == "shared" else "owner",
            "name": name,
            "kind": "openai_compat",
            "base_url": BASE_URL,
            "models": list(models),
            "headers_json": None,
            "api_path": None,
            "api_key_cipher": "cipher-not-returned",
            "key_hint": "cdef",
            "enabled": True,
            "created_at": None,
            "updated_at": None,
            "expires_at": None,
        }
        row.update(fields)
        self.rows[oid] = row
        return row

    # -- 读 ---------------------------------------------------------------
    async def list_visible(self, user_oid: str) -> list[dict]:
        return [
            dict(row)
            for row in self.rows.values()
            if row["scope"] == "shared" or row["owner_oid"] == user_oid
        ]

    async def list_all(self) -> list[dict]:
        return [dict(row) for row in self.rows.values()]

    async def get(self, oid: str) -> dict | None:
        row = self.rows.get(oid)
        return dict(row) if row is not None else None

    # -- 写 ---------------------------------------------------------------
    async def update(self, oid: str, **fields) -> bool:
        self.updates.append((oid, dict(fields)))
        row = self.rows.get(oid)
        if row is None:
            return False
        row.update(fields)
        return True

    async def create(self, **fields) -> str:  # pragma: no cover - 本文件不走创建
        raise AssertionError("discover 不应创建凭据")

    async def delete(self, oid: str) -> bool:  # pragma: no cover - 本文件不走删除
        return self.rows.pop(oid, None) is not None


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    """默认：没有服务端渠道、测速超时固定、目录缓存清空。"""
    credentials._cache.clear()
    monkeypatch.setattr(settings, "AI_PROVIDERS", {}, raising=False)
    monkeypatch.setattr(settings, "AI_MODEL_PROBE_TIMEOUT_SECONDS", 1.0, raising=False)
    yield
    credentials._cache.clear()


@pytest.fixture
def repo(monkeypatch) -> _FakeRepo:
    fake = _FakeRepo()
    monkeypatch.setattr(
        backends, "get_ai_credentials_repo", lambda *args, **kwargs: fake
    )
    return fake


@pytest.fixture
def api(repo, monkeypatch):
    """迷你 app：只挂 ``ai_models_router``，鉴权与密码再认证都换成桩。"""
    async def _allow(token, user, *, detail=None):
        return user

    monkeypatch.setattr(
        ai_models_router, "require_password_verification", _allow
    )

    async def _resolve(_entry):
        return SECRET

    monkeypatch.setattr(credentials, "resolve_api_key", _resolve)

    app = FastAPI()
    app.include_router(ai_models_router.router, prefix=PREFIX)
    app.dependency_overrides[get_current_user] = lambda: ADMIN
    return TestClient(app)


def _upstream(*rows: dict) -> list[dict]:
    return [{"id": row["id"], "context": row.get("context")} for row in rows]


# ---------------------------------------------------------------------------
# 预览：不写库
# ---------------------------------------------------------------------------


def test_preview_reports_upstream_without_writing(api, repo, monkeypatch) -> None:
    repo.seed("a" * 24, name="team", models=["gpt-x", {"id": "gpt-y", "context": 111}])

    async def _list_models(_entry, _key=None):
        return _upstream(
            {"id": "gpt-x", "context": 8000},
            {"id": "gpt-y", "context": 64000},
            {"id": "extra", "context": 32000},
        )

    monkeypatch.setattr(providers, "list_models", _list_models)

    body = api.post(f"{PREFIX}/discover", json={"provider": "team"}).json()

    assert body["code"] == 200
    assert SECRET not in str(body)
    data = body["data"]
    assert data["provider"] == "team"
    assert data["applied"] == 0
    rows = {row["model"]: row for row in data["models"]}
    assert set(rows) == {"gpt-x", "gpt-y", "extra"}
    assert rows["gpt-x"] == {
        "model": "gpt-x",
        "context_length": 8000,
        "in_credential": True,
        "applied": False,
    }
    # 不在凭据里的上游模型只做展示，不能因此被当成「已在渠道里」。
    assert rows["extra"]["in_credential"] is False
    # 预览不写库：一次 update 都没有。
    assert repo.updates == []
    assert repo.rows["a" * 24]["models"] == ["gpt-x", {"id": "gpt-y", "context": 111}]


# ---------------------------------------------------------------------------
# apply：只回写凭据里已有且上游给了窗口的模型
# ---------------------------------------------------------------------------


def test_apply_only_writes_models_already_in_the_credential(
    api, repo, monkeypatch
) -> None:
    repo.seed("b" * 24, name="team", models=["a", "b"])
    invalidated: list[bool] = []

    async def _list_models(_entry, _key=None):
        return _upstream(
            {"id": "a", "context": 1000},
            {"id": "b", "context": None},
            {"id": "not-configured", "context": 500},
        )

    async def _invalidate(*_args, **_kwargs):
        invalidated.append(True)

    monkeypatch.setattr(providers, "list_models", _list_models)
    monkeypatch.setattr(credentials, "invalidate", _invalidate)

    body = api.post(
        f"{PREFIX}/discover", json={"provider": "team", "apply": True}
    ).json()

    assert body["data"]["applied"] == 1
    rows = {row["model"]: row for row in body["data"]["models"]}
    assert rows["a"]["applied"] is True
    assert rows["b"]["applied"] is False
    assert rows["not-configured"]["applied"] is False

    assert len(repo.updates) == 1
    oid, fields = repo.updates[0]
    assert oid == "b" * 24
    # 只回写 a；b 上游没给窗口所以保持字符串；not-configured 不许被加进来。
    assert fields["models"] == [{"id": "a", "context": 1000}, "b"]
    assert invalidated == [True]


def test_apply_keeps_the_model_order_and_never_adds_entries(
    api, repo, monkeypatch
) -> None:
    repo.seed("c" * 24, name="team", models=["zeta", "alpha", "mid"])

    async def _list_models(_entry, _key=None):
        # 上游顺序故意打乱：回写顺序必须跟凭据走，不跟上游走。
        return _upstream(
            {"id": "mid", "context": 3000},
            {"id": "zeta", "context": 1000},
            {"id": "alpha", "context": 2000},
            {"id": "brand-new", "context": 9000},
        )

    monkeypatch.setattr(providers, "list_models", _list_models)
    monkeypatch.setattr(credentials, "invalidate", _noop_invalidate)

    api.post(f"{PREFIX}/discover", json={"provider": "team", "apply": True})

    stored = repo.rows["c" * 24]["models"]
    assert [item["id"] if isinstance(item, dict) else item for item in stored] == [
        "zeta",
        "alpha",
        "mid",
    ]
    assert "brand-new" not in [
        item["id"] if isinstance(item, dict) else item for item in stored
    ]
    assert len(stored) <= credentials._MODELS_MAX


def test_apply_overwrites_a_hand_filled_context(api, repo, monkeypatch) -> None:
    """显式 apply 的语义是「以上游为准」：预览已经让管理员看见差异。"""
    repo.seed("d" * 24, name="team", models=[{"id": "a", "context": 999}])

    async def _list_models(_entry, _key=None):
        return _upstream({"id": "a", "context": 128000})

    monkeypatch.setattr(providers, "list_models", _list_models)
    monkeypatch.setattr(credentials, "invalidate", _noop_invalidate)

    body = api.post(
        f"{PREFIX}/discover", json={"provider": "team", "apply": True}
    ).json()

    assert body["data"]["applied"] == 1
    assert repo.rows["d" * 24]["models"] == [{"id": "a", "context": 128000}]


def test_apply_without_any_change_still_invalidates_nothing(
    api, repo, monkeypatch
) -> None:
    repo.seed("e" * 24, name="team", models=[{"id": "a", "context": 128000}])
    invalidated: list[bool] = []

    async def _list_models(_entry, _key=None):
        return _upstream({"id": "a", "context": 128000})

    async def _invalidate(*_args, **_kwargs):
        invalidated.append(True)

    monkeypatch.setattr(providers, "list_models", _list_models)
    monkeypatch.setattr(credentials, "invalidate", _invalidate)

    body = api.post(
        f"{PREFIX}/discover", json={"provider": "team", "apply": True}
    ).json()

    assert body["data"]["applied"] == 0
    assert repo.updates == []
    assert invalidated == []


# ---------------------------------------------------------------------------
# 门禁与错误码
# ---------------------------------------------------------------------------


def test_unknown_provider_is_404(api) -> None:
    response = api.post(f"{PREFIX}/discover", json={"provider": "nope"})

    assert response.status_code == 404
    assert response.json()["detail"] == "渠道不存在"


def test_server_scoped_provider_is_400(api, monkeypatch) -> None:
    monkeypatch.setattr(
        settings,
        "AI_PROVIDERS",
        {"team": {"base_url": BASE_URL, "api_key": SECRET}},
        raising=False,
    )

    response = api.post(f"{PREFIX}/discover", json={"provider": "team"})

    assert response.status_code == 400
    assert response.json()["detail"] == "服务端作用域的渠道不支持回写"
    assert SECRET not in response.text


def test_upstream_failure_is_502_without_leaking_the_key(
    api, repo, monkeypatch
) -> None:
    repo.seed("f" * 24, name="team", models=["a"])

    async def _list_models(_entry, _key=None):
        raise RouteError("AI_UPSTREAM_AUTH", "upstream rejected the credentials", 502)

    monkeypatch.setattr(providers, "list_models", _list_models)

    response = api.post(f"{PREFIX}/discover", json={"provider": "team"})

    assert response.status_code == 502
    detail = response.json()["detail"]
    assert "上游模型列表获取失败" in detail
    assert SECRET not in response.text


def test_unreadable_credential_is_502(api, repo, monkeypatch) -> None:
    repo.seed("a1" * 12, name="team", models=["a"])

    async def _resolve(_entry):
        raise CredentialError("AI_CREDENTIAL_UNREADABLE", "no secret", 502)

    monkeypatch.setattr(credentials, "resolve_api_key", _resolve)

    response = api.post(f"{PREFIX}/discover", json={"provider": "team"})

    assert response.status_code == 502
    assert "凭据不可用" in response.json()["detail"]


def test_discover_is_admin_only(api) -> None:
    api.app.dependency_overrides[get_current_user] = lambda: STUDENT

    response = api.post(f"{PREFIX}/discover", json={"provider": "team"})

    assert response.status_code == 403
    assert response.json()["detail"] == "Permission denied"


def test_discover_requires_password_verification(api, monkeypatch) -> None:
    """端点必须把前端给的 verify 令牌透传给再认证闸；缺令牌 = 403。"""
    calls: list[tuple[str | None, str]] = []

    async def _verify(token, user, *, detail=None):
        calls.append((token, detail))
        if token != "verify-token":
            raise HTTPException(status_code=403, detail=detail)
        return user

    monkeypatch.setattr(ai_models_router, "require_password_verification", _verify)

    denied = api.post(f"{PREFIX}/discover", json={"provider": "team"})
    assert denied.status_code == 403
    assert denied.json()["detail"] == "AI_PASSWORD_VERIFICATION_REQUIRED"

    # 有令牌时闸被放行，后面才轮到「渠道不存在」（说明门禁在最前面）。
    passed = api.post(
        f"{PREFIX}/discover",
        json={"provider": "team", "verification_token": "verify-token"},
    )
    assert passed.status_code == 404
    assert calls[0][1] == "AI_PASSWORD_VERIFICATION_REQUIRED"
    assert calls[1][0] == "verify-token"


# ---------------------------------------------------------------------------
# providers.list_models 本体
# ---------------------------------------------------------------------------


def test_context_window_takes_the_first_positive_int() -> None:
    assert (
        providers.context_window(
            {"context_length": "abc", "context_window": 0, "max_model_len": 50000}
        )
        == 50000
    )
    assert providers.context_window({"context_length": 100, "context_window": 200}) == 100
    # bool 是 int 子类：True 不能被当成 1。
    assert providers.context_window({"context_length": True, "context_window": 123}) == 123
    assert providers.context_window({"context_length": -5}) is None
    assert providers.context_window({"context_length": 0}) is None
    assert providers.context_window("not-a-mapping") is None


def _mock_transport(monkeypatch, handler) -> httpx.AsyncClient:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(providers, "_client_instance", lambda: client)
    return client


@pytest.mark.asyncio
async def test_list_models_parses_data_and_sends_the_key(monkeypatch) -> None:
    seen: dict = {}

    async def _guard(_entry):
        seen["guarded"] = True

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(
            200,
            json={
                "data": [
                    {"id": "a", "context_length": 8000},
                    {"id": "b"},
                    {"id": "a", "context_length": 1},
                    {"id": None},
                ]
            },
        )

    monkeypatch.setattr(providers, "guard_endpoint", _guard)
    client = _mock_transport(monkeypatch, handler)
    try:
        rows = await providers.list_models({"base_url": BASE_URL}, SECRET)
    finally:
        await client.aclose()

    assert seen["guarded"] is True
    assert seen["url"] == f"{BASE_URL}/models"
    assert seen["auth"] == f"Bearer {SECRET}"
    assert rows == [{"id": "a", "context": 8000}, {"id": "b", "context": None}]


@pytest.mark.asyncio
async def test_list_models_runs_guard_before_sending_anything(monkeypatch) -> None:
    sent: list[bool] = []

    async def _guard(_entry):
        raise RouteError("AI_UPSTREAM_UNREACHABLE", "blocked", 502)

    def handler(_request):  # pragma: no cover - 绝不该被调用
        sent.append(True)
        return httpx.Response(200, json={"data": []})

    monkeypatch.setattr(providers, "guard_endpoint", _guard)
    client = _mock_transport(monkeypatch, handler)
    try:
        with pytest.raises(RouteError):
            await providers.list_models({"base_url": BASE_URL}, SECRET)
    finally:
        await client.aclose()

    assert sent == []


@pytest.mark.asyncio
async def test_list_models_never_logs_or_returns_the_key(monkeypatch, caplog) -> None:
    """上游报错体常常回显请求头：失败路径不许把报文写进日志。"""

    def handler(_request):
        return httpx.Response(
            401,
            json={"error": "bad key", "echo": f"Bearer {SECRET}"},
        )

    monkeypatch.setattr(providers, "guard_endpoint", _guard_noop)
    client = _mock_transport(monkeypatch, handler)
    caplog.set_level(logging.WARNING, logger="services.ai.providers")
    try:
        with pytest.raises(RouteError) as failure:
            await providers.list_models({"base_url": BASE_URL}, SECRET)
    finally:
        await client.aclose()

    assert failure.value.code == "AI_UPSTREAM_AUTH"
    assert SECRET not in str(failure.value)
    assert SECRET not in caplog.text
    # 只记 host 与状态码。
    assert "api.example.com" in caplog.text


@pytest.mark.asyncio
async def test_list_models_honours_the_probe_timeout_switch(monkeypatch) -> None:
    """``AI_MODEL_PROBE_TIMEOUT_SECONDS <= 0`` 是「关掉探测」，显式拉取也不许发包。"""
    monkeypatch.setattr(settings, "AI_MODEL_PROBE_TIMEOUT_SECONDS", 0, raising=False)
    sent: list[bool] = []

    def handler(_request):  # pragma: no cover - 绝不该被调用
        sent.append(True)
        return httpx.Response(200, json={"data": []})

    monkeypatch.setattr(providers, "guard_endpoint", _guard_noop)
    client = _mock_transport(monkeypatch, handler)
    try:
        with pytest.raises(RouteError):
            await providers.list_models({"base_url": BASE_URL}, SECRET)
    finally:
        await client.aclose()

    assert sent == []


@pytest.mark.asyncio
async def test_list_models_takes_the_timeout_from_settings(monkeypatch) -> None:
    monkeypatch.setattr(
        settings, "AI_MODEL_PROBE_TIMEOUT_SECONDS", 3.5, raising=False
    )
    captured: dict = {}

    class _Client:
        is_closed = False

        async def get(self, _url, **kwargs):
            captured.update(kwargs)
            return httpx.Response(200, json={"data": []})

    monkeypatch.setattr(providers, "guard_endpoint", _guard_noop)
    monkeypatch.setattr(providers, "_client_instance", lambda: _Client())

    await providers.list_models({"base_url": BASE_URL}, SECRET)

    timeout = captured["timeout"]
    assert timeout.read == 3.5
    assert timeout.connect <= 3.5


@pytest.mark.asyncio
async def test_list_models_rejects_malformed_json(monkeypatch) -> None:
    def handler(_request):
        return httpx.Response(200, text="not json")

    monkeypatch.setattr(providers, "guard_endpoint", _guard_noop)
    client = _mock_transport(monkeypatch, handler)
    try:
        with pytest.raises(RouteError) as failure:
            await providers.list_models({"base_url": BASE_URL}, SECRET)
    finally:
        await client.aclose()

    assert failure.value.status_code == 502


# ---------------------------------------------------------------------------
# 保存路径：探测委托 list_models，失败静默
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_save_path_discovery_delegates_to_list_models(monkeypatch) -> None:
    calls: list[tuple[dict, str]] = []

    async def _list_models(entry, api_key=None):
        calls.append((dict(entry), api_key))
        return [
            {"id": "a", "context": 4096},
            {"id": "b", "context": None},
        ]

    monkeypatch.setattr(providers, "list_models", _list_models)

    found = await credentials._discover_model_contexts(
        base_url=BASE_URL, api_key=SECRET
    )

    assert found == {"a": 4096}
    assert calls[0][0]["base_url"] == BASE_URL
    assert calls[0][1] == SECRET


@pytest.mark.asyncio
async def test_save_path_discovery_swallows_upstream_failures(monkeypatch) -> None:
    async def _list_models(_entry, _api_key=None):
        raise RouteError("AI_TIMEOUT", "upstream timed out", 504)

    monkeypatch.setattr(providers, "list_models", _list_models)

    assert (
        await credentials._discover_model_contexts(base_url=BASE_URL, api_key=SECRET)
        == {}
    )


async def _guard_noop(_entry) -> None:
    return None


async def _noop_invalidate(*_args, **_kwargs) -> None:
    return None
