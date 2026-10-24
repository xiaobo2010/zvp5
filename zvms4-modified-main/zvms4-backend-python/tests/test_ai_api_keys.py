"""独立 API key（``/api/ai/api-keys`` + ``/v1/*`` 鉴权）的离线契约。

三块各守一件事，都是「写错了不会报错、只会在生产上静默出事」的形状：

* **明文只出现一次**：创建响应里有 ``token``，列表/更新/删除的响应里既没有
  ``token`` 也没有 ``token_hash``。落库的是 sha256，读路径没有解密分支。
* **鉴权失败不区分原因**：不存在 / 已过期 / 已禁用 / token 形状不对都是 401
  ``AI_API_KEY_INVALID``（区分它们等于给攻击者一个探测存在性的接口）；只有**封禁**
  是 403 ``USER_BANNED``——那是「你还是你，但账号被停用」，客户端要能分辨。
* **key 只对 OpenAI 面生效**：管理面（``/api/ai/keys``）仍然只认 JWT，API key
  不能变成平台万能凭证。

全部离线：key 仓储 / 用户仓储 / 凭据目录 / 上游调用都是内存桩，不连库、不出网。
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import settings  # noqa: F401  (services.ai.config 读它，先导入保证顺序一致)
from db import backends
from db.repos.protocols import UNSET, DuplicateApiKey
from routers import ai_api_keys_router, ai_router
from services.ai import api_keys, credentials, providers, quota, rate_limit, routing
from typings.log import inject_log
from util.object_id import get_current_user

ADMIN = "000000000000000000000001"
OWNER = "000000000000000000000003"
OTHER = "000000000000000000000004"
SECRET = "sk-unit-test-0123456789abcdef"


# ---------------------------------------------------------------------------
# 桩
# ---------------------------------------------------------------------------


class StubLog:
    def with_text(self, *args, **kwargs):
        return self

    async def insert_log(self):
        return None


class FakeApiKeysRepo:
    """``AIApiKeysRepo`` 的内存实现：行形状与 Oracle 版一致。"""

    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}
        self.touches: list[str] = []
        self._seq = 0

    def _new_oid(self) -> str:
        self._seq += 1
        return f"{self._seq:024x}"

    async def list_for(self, user_oid: str) -> list[dict]:
        rows = [row for row in self.rows.values() if row["user_oid"] == user_oid]
        rows.sort(key=lambda row: (row["created_at"], row["_id"]), reverse=True)
        return [dict(row) for row in rows]

    async def count_for(self, user_oid: str) -> int:
        return sum(1 for row in self.rows.values() if row["user_oid"] == user_oid)

    async def get_by_oid(self, oid: str) -> dict | None:
        row = self.rows.get(oid)
        return dict(row) if row is not None else None

    async def get_by_hash(self, token_hash: str) -> dict | None:
        for row in self.rows.values():
            if row["token_hash"] == token_hash:
                return dict(row)
        return None

    async def insert(
        self,
        *,
        user_oid: str,
        name: str,
        token_hash: str,
        key_hint: str,
        enabled: bool = True,
        expires_at=None,
    ) -> str:
        self._assert_unique(user_oid, name, token_hash, ignore_oid=None)
        oid = self._new_oid()
        self.rows[oid] = {
            "_id": oid,
            "user_oid": user_oid,
            "name": name,
            "token_hash": token_hash,
            "key_hint": key_hint,
            "enabled": bool(enabled),
            "expires_at": expires_at,
            "last_used_at": None,
            "created_at": datetime.now(),
        }
        return oid

    async def update(
        self,
        oid: str,
        *,
        name: str | None = None,
        enabled: bool | None = None,
        expires_at=UNSET,
    ) -> bool:
        row = self.rows.get(oid)
        if row is None:
            return False
        if name is not None:
            self._assert_unique(row["user_oid"], name, row["token_hash"], ignore_oid=oid)
            row["name"] = name
        if enabled is not None:
            row["enabled"] = bool(enabled)
        if expires_at is not UNSET:
            row["expires_at"] = expires_at
        return True

    async def delete(self, oid: str) -> bool:
        return self.rows.pop(oid, None) is not None

    async def touch(self, oid: str) -> None:
        self.touches.append(oid)
        row = self.rows.get(oid)
        if row is not None:
            row["last_used_at"] = datetime.now()

    def _assert_unique(
        self, user_oid: str, name: str, token_hash: str, *, ignore_oid: str | None
    ) -> None:
        for oid, row in self.rows.items():
            if oid == ignore_oid:
                continue
            if row["user_oid"] == user_oid and row["name"] == name:
                raise DuplicateApiKey(name=name)
            if row["token_hash"] == token_hash:
                raise DuplicateApiKey(name=name)


class FakeUsersRepo:
    def __init__(self, rows: dict[str, dict] | None = None) -> None:
        self.rows = rows or {}
        self.reads: list[str] = []

    async def get_by_oid(self, oid: str) -> dict | None:
        self.reads.append(oid)
        row = self.rows.get(oid)
        return dict(row) if row is not None else None


class Recorder:
    def __init__(self, result=None) -> None:
        self.calls: list[tuple] = []
        self.result = result

    async def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.result


def _user_row(oid: str, *, roles: list[str] | None = None, banned: bool = False, **extra):
    row = {
        "_id": oid,
        "roles": ["admin"] if roles is None else roles,
        "class_id": "c" * 24,
    }
    if banned:
        row["banned"] = True
        row["ban_reason"] = "违规"
    row.update(extra)
    return row


# ---------------------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    """每个用例从干净状态开始：缓存/频控/熔断/信号量都不跨用例。"""
    _reset()
    yield
    _reset()


def _reset() -> None:
    import asyncio

    asyncio.run(api_keys.invalidate())
    rate_limit.reset_state()
    routing.reset_state()
    routing._semaphore = None
    routing._semaphore_size = 0
    credentials._cache.clear()


@pytest.fixture
def repo(monkeypatch) -> FakeApiKeysRepo:
    fake = FakeApiKeysRepo()
    monkeypatch.setattr(backends, "get_ai_api_keys_repo", lambda session=None: fake)
    return fake


@pytest.fixture
def users(monkeypatch) -> FakeUsersRepo:
    fake = FakeUsersRepo({OWNER: _user_row(OWNER), ADMIN: _user_row(ADMIN)})
    monkeypatch.setattr(backends, "get_users_repo", lambda session=None: fake)
    return fake


def _client(user: dict | None = None, *, include_gateway: bool = False) -> TestClient:
    app = FastAPI()
    app.include_router(ai_router.router, prefix="/api/ai")
    app.include_router(ai_api_keys_router.router, prefix="/api/ai/api-keys")
    identity = lambda: user or {"id": OWNER, "per": ["department"]}  # noqa: E731
    app.dependency_overrides[get_current_user] = identity
    app.dependency_overrides[ai_router.get_ai_client] = identity
    app.dependency_overrides[inject_log] = lambda: StubLog()
    return TestClient(app)


def _raw_client() -> TestClient:
    """不覆盖任何鉴权依赖：用来验证「key 不能当 JWT 用」。"""
    app = FastAPI()
    app.include_router(ai_router.router, prefix="/api/ai")
    app.include_router(ai_api_keys_router.router, prefix="/api/ai/api-keys")
    app.dependency_overrides[inject_log] = lambda: StubLog()
    return TestClient(app)


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


def test_create_returns_plaintext_token_once(repo, users):
    client = _client()

    response = client.post("/api/ai/api-keys", json={"name": "ci"})

    assert response.status_code == 201
    data = response.json()["data"]
    token = data["token"]
    assert token.startswith(api_keys.TOKEN_PREFIX)
    assert len(token) > 40
    assert set(data) == {
        "oid",
        "name",
        "key_hint",
        "enabled",
        "expires_at",
        "last_used_at",
        "created_at",
        "token",
    }
    # 落库的是哈希，不是明文。
    stored = repo.rows[data["oid"]]
    assert stored["token_hash"] == api_keys.hash_token(token)
    assert token not in repr(stored)
    # hint = 前 8 + 后 4。
    assert stored["key_hint"] == token[:8] + token[-4:]


def test_list_never_returns_token_or_hash(repo, users):
    client = _client()
    created = client.post("/api/ai/api-keys", json={"name": "ci"}).json()["data"]

    response = client.get("/api/ai/api-keys")

    assert response.status_code == 200
    rows = response.json()["data"]
    assert [row["oid"] for row in rows] == [created["oid"]]
    assert "token" not in rows[0]
    assert "token_hash" not in rows[0]
    assert created["token"] not in response.text
    assert repo.rows[created["oid"]]["token_hash"] not in response.text


def test_duplicate_name_is_409(repo, users):
    client = _client()
    assert client.post("/api/ai/api-keys", json={"name": "ci"}).status_code == 201

    response = client.post("/api/ai/api-keys", json={"name": "ci"})

    assert response.status_code == 409
    assert response.json()["detail"] == "AI_API_KEY_DUPLICATE"


def test_same_name_for_different_users_is_fine(repo, users):
    users.rows[OTHER] = _user_row(OTHER)

    assert _client().post("/api/ai/api-keys", json={"name": "ci"}).status_code == 201
    response = _client({"id": OTHER, "per": ["student"]}).post(
        "/api/ai/api-keys", json={"name": "ci"}
    )

    assert response.status_code == 201


def test_eleventh_key_is_409_limit(repo, users):
    client = _client()
    for index in range(api_keys.MAX_KEYS_PER_USER):
        assert (
            client.post("/api/ai/api-keys", json={"name": f"k{index}"}).status_code
            == 201
        )

    response = client.post("/api/ai/api-keys", json={"name": "overflow"})

    assert response.status_code == 409
    assert response.json()["detail"] == "AI_API_KEY_LIMIT"
    assert len(repo.rows) == api_keys.MAX_KEYS_PER_USER


def test_blank_or_control_char_names_are_400(repo, users):
    client = _client()

    for name in ("", "   ", "a\x00b", "x" * 65):
        response = client.post("/api/ai/api-keys", json={"name": name})
        assert response.status_code == 400
        assert response.json()["detail"] == "AI_API_KEY_BAD_NAME"

    assert repo.rows == {}


def test_invalid_expiry_is_400(repo, users):
    client = _client()

    for value in ("not-a-date", ""):
        response = client.post(
            "/api/ai/api-keys", json={"name": "ci", "expires_at": value}
        )
        assert response.status_code == 400
        assert response.json()["detail"] == "AI_API_KEY_BAD_EXPIRY"

    past = (datetime.now() - timedelta(days=1)).isoformat()
    response = client.post("/api/ai/api-keys", json={"name": "ci", "expires_at": past})
    assert response.status_code == 400
    assert response.json()["detail"] == "AI_API_KEY_BAD_EXPIRY"

    future = (datetime.now() + timedelta(days=1)).isoformat()
    assert (
        client.post(
            "/api/ai/api-keys", json={"name": "ci", "expires_at": future}
        ).status_code
        == 201
    )


def test_owner_can_disable_and_rename_other_users_cannot(repo, users):
    created = _client().post("/api/ai/api-keys", json={"name": "ci"}).json()["data"]
    users.rows[OTHER] = _user_row(OTHER, roles=["student"])

    stranger = _client({"id": OTHER, "per": ["student"]})
    denied = stranger.patch(f"/api/ai/api-keys/{created['oid']}", json={"enabled": False})
    assert denied.status_code == 403
    assert denied.json()["detail"] == "AI_FORBIDDEN"
    assert repo.rows[created["oid"]]["enabled"] is True

    ok = _client().patch(
        f"/api/ai/api-keys/{created['oid']}", json={"name": "ci-2", "enabled": False}
    )
    assert ok.status_code == 200
    assert ok.json()["data"]["name"] == "ci-2"
    assert repo.rows[created["oid"]]["enabled"] is False


def test_admin_sees_and_manages_other_users_keys(repo, users):
    users.rows[OTHER] = _user_row(OTHER, roles=["student"])
    created = (
        _client({"id": OTHER, "per": ["student"]})
        .post("/api/ai/api-keys", json={"name": "student-key"})
        .json()["data"]
    )

    admin = _client({"id": ADMIN, "per": ["admin"]})
    listed = admin.get(f"/api/ai/api-keys?user_oid={OTHER}")

    assert listed.status_code == 200
    assert [row["oid"] for row in listed.json()["data"]] == [created["oid"]]
    assert admin.delete(f"/api/ai/api-keys/{created['oid']}").status_code == 204
    assert repo.rows == {}


def test_non_admin_cannot_list_other_users_keys(repo, users):
    users.rows[OTHER] = _user_row(OTHER, roles=["student"])

    response = _client().get(f"/api/ai/api-keys?user_oid={OTHER}")

    assert response.status_code == 403
    assert response.json()["detail"] == "AI_FORBIDDEN"


def test_patch_unknown_oid_is_404(repo, users):
    response = _client().patch("/api/ai/api-keys/" + "a" * 24, json={"enabled": False})

    assert response.status_code == 404
    assert response.json()["detail"] == "AI_API_KEY_NOT_FOUND"


def test_patch_explicit_null_clears_expiry(repo, users):
    future = (datetime.now() + timedelta(days=1)).isoformat()
    created = (
        _client()
        .post("/api/ai/api-keys", json={"name": "ci", "expires_at": future})
        .json()["data"]
    )

    response = _client().patch(f"/api/ai/api-keys/{created['oid']}", json={"expires_at": None})

    assert response.status_code == 200
    assert response.json()["data"]["expires_at"] is None
    assert repo.rows[created["oid"]]["expires_at"] is None


# ---------------------------------------------------------------------------
# 鉴权
# ---------------------------------------------------------------------------


def _issue(repo, users, *, user_oid: str = OWNER, name: str = "ci", **kwargs) -> tuple[str, str]:
    """直接走服务层签一把 key，返回 ``(token, oid)``。"""
    view = __import__("asyncio").run(api_keys.create(user_oid, name, kwargs.get("expires_at")))
    return view["token"], view["oid"]


def test_authenticate_builds_the_same_user_shape(repo, users):
    token, oid = _issue(repo, users)

    user = __import__("asyncio").run(api_keys.authenticate(token))

    assert user["id"] == OWNER
    assert user["per"] == ["admin"]  # roles 来自用户文档，不是 key
    assert isinstance(user["perm"], list)
    assert user["class_id"] == "c" * 24
    assert user["api_key_oid"] == oid


def test_authenticate_unknown_token_is_401(repo, users):
    with pytest.raises(api_keys.ApiKeyError) as info:
        __import__("asyncio").run(api_keys.authenticate("zvk_" + "x" * 40))

    assert info.value.code == "AI_API_KEY_INVALID"
    assert info.value.status_code == 401


def test_non_api_key_token_is_not_even_looked_up(repo, users):
    """JWT 形状的 token 不该被拿去查 key 表（那是另一套鉴权）。"""
    with pytest.raises(api_keys.ApiKeyError):
        __import__("asyncio").run(api_keys.authenticate("not-a-zvk-token"))

    assert repo.rows == {}


def test_disabled_key_is_401(repo, users):
    token, oid = _issue(repo, users)
    repo.rows[oid]["enabled"] = False

    with pytest.raises(api_keys.ApiKeyError) as info:
        __import__("asyncio").run(api_keys.authenticate(token))

    assert info.value.status_code == 401
    assert info.value.code == "AI_API_KEY_INVALID"


def test_expired_key_is_401(repo, users):
    token, oid = _issue(repo, users)
    repo.rows[oid]["expires_at"] = datetime.now() - timedelta(seconds=1)

    with pytest.raises(api_keys.ApiKeyError) as info:
        __import__("asyncio").run(api_keys.authenticate(token))

    assert info.value.status_code == 401


def test_deleted_user_makes_the_key_invalid(repo, users):
    token, _oid = _issue(repo, users)
    users.rows.pop(OWNER)

    with pytest.raises(api_keys.ApiKeyError) as info:
        __import__("asyncio").run(api_keys.authenticate(token))

    assert info.value.status_code == 401


def test_banned_user_gets_403_user_banned(repo, users):
    """封禁是「账号被停用」而不是「凭据无效」：必须是 403 + USER_BANNED。"""
    from fastapi import HTTPException

    token, _oid = _issue(repo, users)
    users.rows[OWNER] = _user_row(OWNER, banned=True)

    with pytest.raises(HTTPException) as info:
        __import__("asyncio").run(api_keys.authenticate(token))

    assert info.value.status_code == 403
    assert info.value.detail == "USER_BANNED"


def test_revocation_takes_effect_immediately(repo, users):
    """吊销后同进程立刻失效：缓存必须被 CRUD 主动清掉。"""
    import asyncio

    token, oid = _issue(repo, users)
    assert asyncio.run(api_keys.authenticate(token))["id"] == OWNER

    asyncio.run(api_keys.delete(oid, {"id": OWNER, "per": ["department"]}))

    with pytest.raises(api_keys.ApiKeyError):
        asyncio.run(api_keys.authenticate(token))


def test_cache_hit_skips_the_database(repo, users, monkeypatch):
    import asyncio

    token, _oid = _issue(repo, users)
    calls = {"n": 0}
    original = repo.get_by_hash

    async def _counting(token_hash: str):
        calls["n"] += 1
        return await original(token_hash)

    monkeypatch.setattr(repo, "get_by_hash", _counting)

    asyncio.run(api_keys.authenticate(token))
    asyncio.run(api_keys.authenticate(token))

    assert calls["n"] == 1


def test_last_used_is_sampled_not_written_per_request(repo, users):
    import asyncio

    token, oid = _issue(repo, users)

    asyncio.run(api_keys.authenticate(token))
    asyncio.run(api_keys.authenticate(token))
    asyncio.run(api_keys.authenticate(token))

    assert repo.touches == [oid]


# ---------------------------------------------------------------------------
# /v1/* 接线
# ---------------------------------------------------------------------------


def _entry(models=("gpt-x",)) -> dict:
    return {
        "key": "shared:" + "a" * 24,
        "scope": "shared",
        "owner_oid": "0" * 24,
        "name": "team",
        "kind": "openai_compat",
        "base_url": "https://team.example.com/v1",
        "models": list(models),
        "api_key_cipher": "cipher-team",
        "api_key": None,
        "key_hint": "cdef",
        "enabled": True,
        "oid": "a" * 24,
        "created_at": None,
        "updated_at": None,
        "expires_at": None,
    }


@pytest.fixture
def gateway(monkeypatch):
    """上游 / 目录 / 额度的桩：让 ``/v1/*`` 能跑到 200。"""
    async def _catalog(user_oid: str) -> list[dict]:
        return [_entry()]

    async def _resolve(entry: dict) -> str:
        return SECRET

    async def _chat(payload: dict, entry: dict, api_key: str) -> dict:
        return {"id": "ok", "usage": {"total_tokens": 1}}

    async def _peek(*args, **kwargs):
        return (True, 0, 100)

    async def _consume(*args, **kwargs):
        return (True, 1, 100)

    monkeypatch.setattr(credentials, "catalog", _catalog)
    monkeypatch.setattr(credentials, "resolve_api_key", _resolve)
    monkeypatch.setattr(providers, "chat_completion", _chat)
    monkeypatch.setattr(quota, "peek", _peek)
    monkeypatch.setattr(quota, "consume", _consume)


def test_chat_completions_accepts_an_api_key(repo, users, gateway):
    token, _oid = _issue(repo, users)
    client = _raw_client()

    response = client.post(
        "/api/ai/v1/chat/completions",
        json={"model": "gpt-x", "messages": [{"role": "user", "content": "hi"}]},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.json()["id"] == "ok"


def test_models_endpoint_accepts_an_api_key(repo, users, gateway):
    token, _oid = _issue(repo, users)
    client = _raw_client()

    response = client.get(
        "/api/ai/v1/models", headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 200
    assert [item["id"] for item in response.json()["data"]] == ["gpt-x"]


def test_expired_key_is_rejected_by_the_v1_endpoint(repo, users, gateway):
    token, oid = _issue(repo, users)
    repo.rows[oid]["expires_at"] = datetime.now() - timedelta(seconds=1)

    response = _raw_client().get(
        "/api/ai/v1/models", headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "AI_API_KEY_INVALID"


def test_api_key_cannot_open_the_management_face(repo, users, gateway):
    """key 只能在 ``/v1/*`` 用：管理面只认 JWT，否则泄漏一把 key = 全平台。"""
    token, _oid = _issue(repo, users)

    response = _raw_client().get(
        "/api/ai/keys", headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 401


def test_jwt_path_still_uses_the_platform_authenticator(repo, users, gateway, monkeypatch):
    """非 ``zvk_`` 的 token 原样交给 ``_authenticate``：JWT 行为不变。"""
    import asyncio

    seen: list[tuple] = []

    async def _fake_authenticate(token, scope, exception=True, request=None):
        seen.append((token, scope, exception))
        return {"id": OWNER, "per": ["department"], "perm": [], "class_id": None}

    monkeypatch.setattr(ai_router, "_authenticate", _fake_authenticate)

    user = asyncio.run(ai_router.get_ai_client("jwt-looking-token", None, None))

    assert user["id"] == OWNER
    assert seen == [("jwt-looking-token", "long", True)]


def test_key_dimension_rate_limit_does_not_touch_the_user_bucket(repo, users, gateway, monkeypatch):
    """每把 key 有自己的窗口：一把失控不拖累同一个人的另一把。"""
    from services.ai import config as ai_config

    first, _oid = _issue(repo, users, name="ci-1")
    second, _oid2 = _issue(repo, users, name="ci-2")
    # 只留 key 维度：用户维度设成 0（不限），否则它先挡住第二次调用。
    monkeypatch.setattr(ai_config, "rate_limit_per_minute", lambda kind: 0)
    monkeypatch.setattr(ai_config, "api_key_rate_limit_per_minute", lambda: 1)
    client = _raw_client()

    def _call(token: str):
        return client.post(
            "/api/ai/v1/chat/completions",
            json={"model": "gpt-x", "messages": [{"role": "user", "content": "hi"}]},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert _call(first).status_code == 200
    limited = _call(first)
    assert limited.status_code == 429
    assert limited.json()["error"]["code"] == "AI_RATE_LIMITED"
    # 同一用户的另一把 key 不受影响。
    assert _call(second).status_code == 200


def test_api_key_identity_carries_the_user_quota(repo, users, gateway, monkeypatch):
    """额度仍按 user_oid 计：同一用户的两把 key 共享月额度。"""
    first, _oid = _issue(repo, users, name="ci-1")
    consume = Recorder((True, 1, 100))

    async def _peek(*args, **kwargs):
        return (True, 0, 100)

    monkeypatch.setattr(quota, "peek", _peek)
    monkeypatch.setattr(quota, "consume", consume)

    client = _raw_client()
    response = client.post(
        "/api/ai/v1/chat/completions",
        json={"model": "gpt-x", "messages": [{"role": "user", "content": "hi"}]},
        headers={"Authorization": f"Bearer {first}"},
    )

    assert response.status_code == 200
    assert consume.calls[0][0] == (OWNER, "chat", 1)
