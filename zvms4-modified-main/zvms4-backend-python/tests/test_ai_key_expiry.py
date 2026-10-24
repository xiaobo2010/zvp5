"""``AI_CREDENTIALS.EXPIRES_AT``：写入校验、部分更新的三态、目录遮蔽。

契约（V028）：

* 创建/更新都收 ISO 8601 字符串；``None`` = 永不过期；
* 更新是**三态**：不传 = 不改，字符串 = 设置，显式 ``null`` = 清除；
* 过去的时间与坏格式一律 400 ``AI_KEY_BAD_EXPIRY``（静默接受等于存一条立刻
  过期的凭据，管理员只会看到查不出原因的 401）；
* 过期条目**不进生效目录**：与 disabled 走同一条路径，于是 ``/v1/models`` 自然
  不再列出它的模型，路由也不会把它当候选。

仓储是内存桩（不连库、不出网）；SQL 形状由 ``test_ai_repo_sql`` 与
``test_ai_ddl_mirror`` 守。
"""

from __future__ import annotations

import ipaddress
import time
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import settings
from db import backends
from routers import ai_router
from services.ai import config as ai_config
from services.ai import credentials, crypto
from typings.log import inject_log
from util.object_id import get_current_user

ADMIN = "000000000000000000000001"
USER = "000000000000000000000003"
SHARED_OWNER = credentials.SHARED_OWNER
SECRET = "sk-unit-test-0123456789abcdef"


class FakeRepo:
    """内存 ``AICredentialsRepo``：``update`` 按「键存在即写」语义（含 None）。"""

    def __init__(self, rows: list[dict] | None = None) -> None:
        self.rows: dict[str, dict] = {row["_id"]: dict(row) for row in rows or []}
        self.calls: list[str] = []

    async def list_visible(self, user_oid: str) -> list[dict]:
        self.calls.append("list_visible")
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

    async def create(self, **fields) -> str:
        oid = f"{len(self.rows) + 100:024x}"
        owner = SHARED_OWNER if fields["scope"] == "shared" else fields["owner_oid"]
        self.rows[oid] = {
            "_id": oid,
            "scope": fields["scope"],
            "owner_oid": owner,
            "name": fields["name"],
            "kind": fields["kind"],
            "base_url": fields["base_url"],
            "models": list(fields["models"]),
            "headers_json": None,
            "api_path": None,
            "api_key_cipher": fields["api_key_cipher"],
            "key_hint": fields.get("key_hint"),
            "enabled": fields["enabled"],
            "created_at": datetime.now(),
            "updated_at": datetime.now(),
            "expires_at": fields.get("expires_at"),
        }
        return oid

    async def update(self, oid: str, **fields) -> bool:
        row = self.rows.get(oid)
        if row is None:
            return False
        row.update(fields)  # expires_at=None 就是「置 NULL」
        return True

    async def delete(self, oid: str) -> bool:
        return self.rows.pop(oid, None) is not None


def _row(
    *,
    oid: str,
    name: str = "team",
    scope: str = "shared",
    models: tuple[str, ...] = ("gpt-x",),
    enabled: bool = True,
    expires_at: datetime | None = None,
) -> dict:
    return {
        "_id": oid,
        "scope": scope,
        "owner_oid": SHARED_OWNER if scope == "shared" else ADMIN,
        "name": name,
        "kind": "openai_compat",
        "base_url": "https://api.example.com/v1",
        "models": list(models),
        "headers_json": None,
        "api_path": None,
        "api_key_cipher": crypto.encrypt_secret(SECRET),
        "key_hint": SECRET[-4:],
        "enabled": enabled,
        "created_at": datetime.now(),
        "updated_at": datetime.now(),
        "expires_at": expires_at,
    }


def _offline_resolver(monkeypatch) -> None:
    def _resolve(host: str):
        try:
            return [ipaddress.ip_address(host)]
        except ValueError:
            return [ipaddress.ip_address("93.184.216.34")]

    monkeypatch.setattr(credentials, "_resolve_host", _resolve)


@pytest.fixture(autouse=True)
def _isolated_gateway(monkeypatch):
    credentials._cache.clear()
    monkeypatch.setenv(crypto._SECRET_ENV, "unit-test-master-secret")
    monkeypatch.setattr(crypto, "_master_key", None)
    monkeypatch.setattr(settings, "AI_PROVIDERS", {}, raising=False)
    yield
    credentials._cache.clear()
    crypto._master_key = None


@pytest.fixture
def repo(monkeypatch) -> FakeRepo:
    fake = FakeRepo()
    monkeypatch.setattr(backends, "get_ai_credentials_repo", lambda session=None: fake)
    return fake


def _create(**kwargs) -> dict:
    payload = {
        "user_oid": ADMIN,
        "scope": "shared",
        "name": "team",
        "base_url": "http://8.8.8.8/v1",  # IP 字面量：不触发 DNS
        "models": ["gpt-x"],
        "api_key": SECRET,
    }
    payload.update(kwargs)
    return payload


# ---------------------------------------------------------------------------
# 创建
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_stores_the_expiry(repo):
    expiry = datetime.now() + timedelta(days=30)

    view = await credentials.create_entry(**_create(expires_at=expiry.isoformat()))

    # 视图原样回显（与 created_at / updated_at 同一处理方式），不额外格式化。
    assert view["expires_at"] == expiry
    stored = repo.rows[view["oid"]]
    assert stored["expires_at"] == expiry


@pytest.mark.asyncio
async def test_create_without_expiry_is_never_expiring(repo):
    view = await credentials.create_entry(**_create())

    assert view["expires_at"] is None
    assert repo.rows[view["oid"]]["expires_at"] is None


@pytest.mark.asyncio
async def test_create_accepts_a_timezone_aware_string(repo):
    """带时区的入参先换算到服务器本地再落库（naive），与时间列同口径。"""
    expiry = datetime.now() + timedelta(days=1)

    view = await credentials.create_entry(**_create(expires_at=expiry.isoformat() + "Z"))

    expected = expiry.replace(tzinfo=timezone.utc).astimezone().replace(tzinfo=None)
    stored = repo.rows[view["oid"]]["expires_at"]
    assert stored.tzinfo is None
    assert stored == expected


@pytest.mark.asyncio
async def test_list_entries_echoes_the_expiry(repo):
    expiry = datetime.now() + timedelta(days=7)
    repo.rows = {"c" * 24: _row(oid="c" * 24, expires_at=expiry)}

    views = await credentials.list_entries({"id": ADMIN, "per": ["admin"]})

    assert views[0]["expires_at"] == expiry


# ---------------------------------------------------------------------------
# 更新：三态
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_sets_the_expiry(repo):
    repo.rows = {"c" * 24: _row(oid="c" * 24)}
    expiry = datetime.now() + timedelta(days=3)

    view = await credentials.update_entry(
        oid="c" * 24,
        actor={"id": ADMIN, "per": ["admin"]},
        fields={"expires_at": expiry.isoformat()},
    )

    assert view["expires_at"] == expiry
    assert repo.rows["c" * 24]["expires_at"] == expiry


@pytest.mark.asyncio
async def test_update_with_explicit_null_clears_the_expiry(repo):
    """``null`` 是「清除到期」而不是「不改」——这是 UNSET 哨兵存在的理由。"""
    repo.rows = {
        "c" * 24: _row(oid="c" * 24, expires_at=datetime.now() + timedelta(days=3))
    }

    view = await credentials.update_entry(
        oid="c" * 24,
        actor={"id": ADMIN, "per": ["admin"]},
        fields={"expires_at": None},
    )

    assert view["expires_at"] is None
    assert repo.rows["c" * 24]["expires_at"] is None


@pytest.mark.asyncio
async def test_update_without_the_key_keeps_the_expiry(repo):
    expiry = datetime.now() + timedelta(days=3)
    repo.rows = {"c" * 24: _row(oid="c" * 24, expires_at=expiry)}

    view = await credentials.update_entry(
        oid="c" * 24,
        actor={"id": ADMIN, "per": ["admin"]},
        fields={"enabled": False},  # exclude_unset 后根本没有 expires_at 这个键
    )

    assert view["expires_at"] == expiry
    assert repo.rows["c" * 24]["expires_at"] == expiry


# ---------------------------------------------------------------------------
# 校验：过去的时间 / 坏格式 / 非字符串
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "value",
    [
        (datetime.now() - timedelta(minutes=1)).isoformat(),  # 已经过去
        "2000-01-01T00:00:00",  # 很久以前
        "not-a-date",
        "2026-13-45T99:99:99",  # 形状像 ISO 但字段越界
        "",
        1234567890,  # 不是字符串
    ],
)
async def test_invalid_expiry_is_400(repo, monkeypatch, value):
    _offline_resolver(monkeypatch)

    with pytest.raises(credentials.CredentialError) as failure:
        await credentials.create_entry(**_create(expires_at=value))

    assert (failure.value.code, failure.value.status_code) == (
        "AI_KEY_BAD_EXPIRY",
        400,
    )
    assert repo.rows == {}  # 校验失败不得落库


@pytest.mark.asyncio
async def test_update_to_a_past_time_is_rejected_and_keeps_the_old_value(repo):
    expiry = datetime.now() + timedelta(days=3)
    repo.rows = {"c" * 24: _row(oid="c" * 24, expires_at=expiry)}

    with pytest.raises(credentials.CredentialError) as failure:
        await credentials.update_entry(
            oid="c" * 24,
            actor={"id": ADMIN, "per": ["admin"]},
            fields={"expires_at": (datetime.now() - timedelta(days=1)).isoformat()},
        )

    assert failure.value.code == "AI_KEY_BAD_EXPIRY"
    assert repo.rows["c" * 24]["expires_at"] == expiry


def test_is_expired_compares_against_now():
    assert credentials._is_expired(None) is False  # NULL = 永不过期
    assert credentials._is_expired(datetime.now() - timedelta(seconds=1)) is True
    assert credentials._is_expired(datetime.now() + timedelta(days=1)) is False
    # 坏值按「不过期」处理：一行坏数据不该让整个目录消失。
    assert credentials._is_expired("garbage") is False


# ---------------------------------------------------------------------------
# 目录遮蔽：过期条目与 disabled 同路径
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_expired_credentials_are_not_in_the_catalog(repo):
    repo.rows = {
        "a" * 24: _row(
            oid="a" * 24,
            name="old",
            models=("gpt-old",),
            expires_at=datetime.now() - timedelta(minutes=1),
        ),
        "b" * 24: _row(
            oid="b" * 24,
            name="fresh",
            models=("gpt-new",),
            expires_at=datetime.now() + timedelta(days=1),
        ),
        "c" * 24: _row(oid="c" * 24, name="forever", models=("gpt-forever",)),
    }

    entries = await credentials.catalog(USER)

    assert [entry["name"] for entry in entries] == ["fresh", "forever"]
    assert [entry["models"] for entry in entries] == [["gpt-new"], ["gpt-forever"]]


@pytest.mark.asyncio
async def test_an_expired_entry_does_not_shadow_a_live_one(repo):
    """同名时过期项不能占用「先出现」的位置，否则有效的那把 key 会被遮蔽掉。"""
    repo.rows = {
        "a" * 24: _row(
            oid="a" * 24,
            scope="personal",
            name="dup",
            models=("mine",),
            expires_at=datetime.now() - timedelta(seconds=1),
        ),
        "b" * 24: _row(oid="b" * 24, name="dup", models=("team",)),
    }

    entries = await credentials.catalog(ADMIN)

    assert [(entry["scope"], entry["models"]) for entry in entries] == [
        ("shared", ["team"])
    ]


@pytest.mark.asyncio
async def test_cached_entries_are_filtered_at_read_time(repo, monkeypatch):
    """TTL 窗口内到期的条目必须立刻消失，而不是等缓存自然过期。

    直接种一份「还在 TTL 内」的缓存（而不是改仓储）：缓存命中时不会重读仓储，
    所以出口过滤是唯一能挡住「缓存期间到期」的地方。
    """
    monkeypatch.setattr(ai_config, "credentials_cache_ttl", lambda: 300.0)
    now = time.monotonic()
    credentials._cache[USER] = (
        now + 300,
        [
            {**_row(oid="a" * 24, name="old", expires_at=datetime.now() - timedelta(seconds=1))},
            {**_row(oid="b" * 24, name="fresh", expires_at=datetime.now() + timedelta(days=1))},
            {**_row(oid="c" * 24, name="forever", expires_at=None)},
        ],
    )

    entries = await credentials.catalog(USER)

    assert [entry["name"] for entry in entries] == ["fresh", "forever"]
    # 缓存命中：没有回去读仓储（否则这条用例证明不了「出口过滤」）。
    assert repo.calls == []


# ---------------------------------------------------------------------------
# 路由层：请求模型透传（创建 / 三态更新）与 /v1/models
# ---------------------------------------------------------------------------


class StubLog:
    def with_text(self, *args, **kwargs):
        return self

    async def insert_log(self):
        return None


def _client(user: dict | None = None) -> TestClient:
    app = FastAPI()
    app.include_router(ai_router.router, prefix="/api/ai")
    identity = lambda: user or {"id": ADMIN, "per": ["admin"]}  # noqa: E731
    app.dependency_overrides[get_current_user] = identity
    # ``/v1/*`` 走独立的鉴权入口（API key 或 JWT）：这个用例测的是模型目录，
    # 与凭据形态无关，两条入口都直接放行同一个身份。
    app.dependency_overrides[ai_router.get_ai_client] = identity
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
    recorder = Recorder({"oid": "a" * 24, "scope": "shared", "name": "team"})
    monkeypatch.setattr(credentials, "create_entry", recorder)
    return recorder


@pytest.fixture
def update_entry(monkeypatch) -> Recorder:
    recorder = Recorder({"oid": "a" * 24, "scope": "shared", "name": "team"})
    monkeypatch.setattr(credentials, "update_entry", recorder)
    return recorder


def _body(**extra) -> dict:
    body = {
        "scope": "shared",
        "name": "team",
        "base_url": "https://api.example.com/v1",
        "models": ["gpt-x"],
        "api_key": SECRET,
    }
    body.update(extra)
    return body


def test_post_passes_the_expiry_through(create_entry):
    expiry = (datetime.now() + timedelta(days=10)).isoformat()

    response = _client().post("/api/ai/keys", json=_body(expires_at=expiry))

    assert response.status_code == 201
    assert create_entry.calls[0]["expires_at"] == expiry


def test_post_without_expiry_sends_none(create_entry):
    response = _client().post("/api/ai/keys", json=_body())

    assert response.status_code == 201
    assert create_entry.calls[0]["expires_at"] is None


def test_patch_passes_the_expiry_through_fields(update_entry):
    expiry = (datetime.now() + timedelta(days=10)).isoformat()

    response = _client().patch(f"/api/ai/keys/{'a' * 24}", json={"expires_at": expiry})

    assert response.status_code == 200
    assert update_entry.calls[0]["fields"]["expires_at"] == expiry


def test_patch_with_explicit_null_keeps_the_key_in_the_payload(update_entry):
    response = _client().patch(f"/api/ai/keys/{'a' * 24}", json={"expires_at": None})

    assert response.status_code == 200
    # 键必须存在（值 None）：服务层按「键是否存在」区分「清除」与「不改」。
    assert "expires_at" in update_entry.calls[0]["fields"]
    assert update_entry.calls[0]["fields"]["expires_at"] is None


def test_patch_without_expiry_omits_the_key(update_entry):
    response = _client().patch(f"/api/ai/keys/{'a' * 24}", json={"enabled": False})

    assert response.status_code == 200
    assert "expires_at" not in update_entry.calls[0]["fields"]


def test_models_endpoint_hides_models_of_expired_keys(repo):
    """``/v1/models`` 的模型来源就是生效目录：过期 key 的模型必须消失。"""
    repo.rows = {
        "a" * 24: _row(
            oid="a" * 24,
            name="old",
            models=("gpt-old",),
            expires_at=datetime.now() - timedelta(minutes=1),
        ),
        "b" * 24: _row(oid="b" * 24, name="fresh", models=("gpt-new",)),
    }

    response = _client().get("/api/ai/v1/models")

    assert response.status_code == 200
    assert [item["id"] for item in response.json()["data"]] == ["gpt-new"]
