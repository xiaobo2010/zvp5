"""AI 凭据目录的三层合并、遮蔽、权限与 base_url 校验（假仓储，不连库）。

目录（``catalog``）是网关唯一的「这次能打谁」来源，所以这里钉三件事：

* **顺序与遮蔽**：personal → shared → server，同名只留第一个。写反了不会报错，
  只会让管理员建的 personal key 悄悄失效（流量打到平台 key 上，平台付费）。
* **明文不出目录**：``catalog`` 的结果会进 ``/v1/models``、``/health`` 与缓存，
  一旦带明文 key，它就会出现在响应体或进程堆里。
* **可见性 ≠ 可改性**：personal 只有 owner 能改；shared 任意 admin 能改。

仓储用假实现（内存 dict），因为这里要验证的是服务层的合并/权限规则，不是 SQL
（SQL 形状在 ``test_ai_repo_sql.py`` 里单独钉）。
"""

from __future__ import annotations

import ipaddress
from datetime import datetime, timedelta

import pytest

import settings
from db import backends, state
from services.ai import config as ai_config
from services.ai import credentials, crypto

ADMIN = "000000000000000000000001"
OTHER_ADMIN = "000000000000000000000002"
USER = "000000000000000000000003"
SHARED_OWNER = credentials.SHARED_OWNER

SECRET = "sk-unit-test-0123456789abcdef"
OTHER_SECRET = "sk-second-key-0123456789abcdef"


# ---------------------------------------------------------------------------
# 假仓储
# ---------------------------------------------------------------------------


class DuplicateCredential(Exception):
    """与 ``db.repos.oracle.ai_credentials.DuplicateCredential`` 同名。

    ``credentials._is_duplicate`` 按类名判定（避免把 SQLAlchemy 拉进网关导入
    路径），所以假仓储必须用同一个类名才能走到 409 分支。
    """

    def __init__(self, name: str, scope: str) -> None:
        self.name = name
        self.scope = scope
        super().__init__(name)


class FakeRepo:
    def __init__(self, rows: list[dict] | None = None) -> None:
        self.rows: dict[str, dict] = {row["_id"]: dict(row) for row in rows or []}
        self.calls: list[str] = []

    # -- 读 ---------------------------------------------------------------
    async def list_visible(self, user_oid: str) -> list[dict]:
        self.calls.append("list_visible")
        return [
            dict(row)
            for row in self.rows.values()
            if row["scope"] == "shared" or row["owner_oid"] == user_oid
        ]

    async def list_all(self) -> list[dict]:
        self.calls.append("list_all")
        return [dict(row) for row in self.rows.values()]

    async def get(self, oid: str) -> dict | None:
        self.calls.append("get")
        row = self.rows.get(oid)
        return dict(row) if row is not None else None

    # -- 写 ---------------------------------------------------------------
    async def create(self, **fields) -> str:
        self.calls.append("create")
        owner = SHARED_OWNER if fields["scope"] == "shared" else fields["owner_oid"]
        for row in self.rows.values():
            if (
                row["scope"] == fields["scope"]
                and row["owner_oid"] == owner
                and row["name"] == fields["name"]
            ):
                raise DuplicateCredential(fields["name"], fields["scope"])
        oid = f"{len(self.rows) + 100:024x}"
        self.rows[oid] = {
            "_id": oid,
            "scope": fields["scope"],
            "owner_oid": owner,
            "name": fields["name"],
            "kind": fields["kind"],
            "base_url": fields["base_url"],
            "models": list(fields["models"]),
            "api_key_cipher": fields["api_key_cipher"],
            "key_hint": fields.get("key_hint"),
            "enabled": fields["enabled"],
            "created_at": None,
            "updated_at": None,
        }
        return oid

    async def update(self, oid: str, **fields) -> bool:
        self.calls.append("update")
        row = self.rows.get(oid)
        if row is None:
            return False
        candidate = dict(row)
        candidate.update(fields)
        for other_oid, other in self.rows.items():
            if other_oid == oid:
                continue
            if (
                other["scope"] == candidate["scope"]
                and other["owner_oid"] == candidate["owner_oid"]
                and other["name"] == candidate["name"]
            ):
                raise DuplicateCredential(candidate["name"], candidate["scope"])
        row.update(fields)
        return True

    async def delete(self, oid: str) -> bool:
        self.calls.append("delete")
        return self.rows.pop(oid, None) is not None


def _row(
    *,
    oid: str,
    scope: str,
    owner_oid: str,
    name: str,
    models: tuple[str, ...] = ("gpt-x",),
    base_url: str = "https://api.example.com/v1",
    enabled: bool = True,
    cipher: str | None = None,
    key_hint: str | None = "cdef",
) -> dict:
    return {
        "_id": oid,
        "scope": scope,
        "owner_oid": SHARED_OWNER if scope == "shared" else owner_oid,
        "name": name,
        "kind": "openai_compat",
        "base_url": base_url,
        "models": list(models),
        "api_key_cipher": cipher if cipher is not None else crypto.encrypt_secret(SECRET),
        "key_hint": key_hint,
        "enabled": enabled,
        "created_at": None,
        "updated_at": None,
    }


def _strings(value) -> list[str]:
    """把嵌套结构里所有字符串摊平（含 dict 的键），用于「响应里不许有明文」。"""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        out: list[str] = []
        for key, item in value.items():
            out.extend(_strings(str(key)))
            out.extend(_strings(item))
        return out
    if isinstance(value, (list, tuple, set)):
        out = []
        for item in value:
            out.extend(_strings(item))
        return out
    return []


@pytest.fixture(autouse=True)
def _isolated_gateway(monkeypatch):
    """清目录缓存 + 固定主密钥 + 默认没有服务端 provider。"""
    credentials._cache.clear()
    monkeypatch.setenv(crypto._SECRET_ENV, "unit-test-master-secret")
    monkeypatch.setattr(crypto, "_master_key", None)
    monkeypatch.setattr(settings, "AI_PROVIDERS", {}, raising=False)
    # 单测一律不碰网络：保存时的 ``/models`` 探测默认关掉（专测探测的用例自己开）。
    monkeypatch.setattr(settings, "AI_MODEL_PROBE_TIMEOUT_SECONDS", 0, raising=False)
    yield
    credentials._cache.clear()
    crypto._master_key = None


@pytest.fixture
def repo(monkeypatch) -> FakeRepo:
    fake = FakeRepo()
    monkeypatch.setattr(backends, "get_ai_credentials_repo", lambda session=None: fake)
    return fake


def _offline_resolver(monkeypatch) -> None:
    """把主机名解析替换成固定结果：用例不许碰 DNS（离线、确定性）。

    IP 字面量照原样返回，所以 ``_is_blocked`` 的真实判断逻辑仍然被执行；主机名
    一律映射成环回（``localhost``）或一个公网地址，用来覆盖「内网被拒 / 公网放行」
    两条分支。
    """

    def _resolve(host: str):
        try:
            return [ipaddress.ip_address(host)]
        except ValueError:
            if host == "localhost":
                return [ipaddress.ip_address("127.0.0.1")]
            return [ipaddress.ip_address("93.184.216.34")]

    monkeypatch.setattr(credentials, "_resolve_host", _resolve)


# ---------------------------------------------------------------------------
# 目录顺序与遮蔽
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_catalog_orders_personal_then_shared_then_server(repo, monkeypatch):
    repo.rows = {
        row["_id"]: row
        for row in (
            _row(oid="a" * 24, scope="personal", owner_oid=USER, name="mine"),
            _row(oid="b" * 24, scope="shared", owner_oid=SHARED_OWNER, name="team"),
        )
    }
    monkeypatch.setenv("ZVP5_AI_FALLBACK_KEY", "server-side-key")
    monkeypatch.setattr(
        settings,
        "AI_PROVIDERS",
        {
            "fallback": {
                "kind": "openai_compat",
                "base_url": "https://fallback.example.com/v1",
                "api_key_env": "ZVP5_AI_FALLBACK_KEY",
                "models": ["fallback-model"],
            }
        },
        raising=False,
    )

    entries = await credentials.catalog(USER)

    assert [entry["scope"] for entry in entries] == ["personal", "shared", "server"]
    assert [entry["key"] for entry in entries] == [
        f"personal:{'a' * 24}",
        f"shared:{'b' * 24}",
        "server:fallback",
    ]


@pytest.mark.asyncio
async def test_catalog_shadows_same_name_personal_over_shared_and_server(repo, monkeypatch):
    repo.rows = {
        row["_id"]: row
        for row in (
            _row(oid="a" * 24, scope="personal", owner_oid=USER, name="dup"),
            _row(oid="b" * 24, scope="shared", owner_oid=SHARED_OWNER, name="dup"),
            _row(oid="c" * 24, scope="shared", owner_oid=SHARED_OWNER, name="team"),
        )
    }
    monkeypatch.setenv("ZVP5_AI_TEAM_KEY", "server-side-key")
    monkeypatch.setattr(
        settings,
        "AI_PROVIDERS",
        {
            "team": {
                "kind": "openai_compat",
                "base_url": "https://team.example.com/v1",
                "api_key_env": "ZVP5_AI_TEAM_KEY",
            }
        },
        raising=False,
    )

    entries = await credentials.catalog(USER)

    assert [entry["name"] for entry in entries] == ["dup", "team"]
    assert entries[0]["scope"] == "personal"
    # 同名 server 项也被 shared 遮蔽（'team' 只出现一次，且是 shared 的）。
    assert entries[1]["scope"] == "shared"


@pytest.mark.asyncio
async def test_deleting_personal_lets_shared_take_over(repo):
    repo.rows = {
        row["_id"]: row
        for row in (
            _row(oid="a" * 24, scope="personal", owner_oid=USER, name="dup"),
            _row(oid="b" * 24, scope="shared", owner_oid=SHARED_OWNER, name="dup"),
        )
    }
    assert (await credentials.catalog(USER))[0]["scope"] == "personal"

    await credentials.delete_entry(oid="a" * 24, actor={"id": USER, "per": ["admin"]})

    entries = await credentials.catalog(USER)
    assert len(entries) == 1
    assert entries[0]["scope"] == "shared"
    assert entries[0]["oid"] == "b" * 24


@pytest.mark.asyncio
async def test_catalog_skips_disabled_entries(repo):
    repo.rows = {
        row["_id"]: row
        for row in (
            _row(oid="a" * 24, scope="personal", owner_oid=USER, name="off", enabled=False),
            _row(oid="b" * 24, scope="shared", owner_oid=SHARED_OWNER, name="on"),
        )
    }

    entries = await credentials.catalog(USER)

    assert [entry["name"] for entry in entries] == ["on"]


@pytest.mark.asyncio
async def test_catalog_hides_other_admins_personal_keys(repo):
    repo.rows = {
        row["_id"]: row
        for row in (
            _row(oid="a" * 24, scope="personal", owner_oid=OTHER_ADMIN, name="theirs"),
            _row(oid="b" * 24, scope="shared", owner_oid=SHARED_OWNER, name="team"),
        )
    }

    entries = await credentials.catalog(USER)

    assert [entry["name"] for entry in entries] == ["team"]


# ---------------------------------------------------------------------------
# 明文不出目录 / 公开视图
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_catalog_never_carries_plaintext_keys(repo):
    repo.rows = {
        row["_id"]: row
        for row in (
            _row(oid="a" * 24, scope="personal", owner_oid=USER, name="mine"),
            _row(oid="b" * 24, scope="shared", owner_oid=SHARED_OWNER, name="team"),
        )
    }

    entries = await credentials.catalog(USER)

    joined = "".join(_strings(entries))
    assert SECRET not in joined
    # 目录里确实有这把 key（只是密文）：解密必须还原出明文，否则上一条断言是空的。
    assert crypto.decrypt_secret(entries[0]["api_key_cipher"]) == SECRET
    # 展示用 hint 只允许是末四位。
    assert entries[0]["key_hint"] == SECRET[-4:]


@pytest.mark.asyncio
async def test_resolve_api_key_decrypts_user_entries(repo):
    repo.rows = {"a" * 24: _row(oid="a" * 24, scope="personal", owner_oid=USER, name="mine")}
    entry = (await credentials.catalog(USER))[0]

    assert await credentials.resolve_api_key(entry) == SECRET


@pytest.mark.asyncio
async def test_resolve_api_key_reads_env_for_server_scope(repo, monkeypatch):
    monkeypatch.setenv("ZVP5_AI_FALLBACK_KEY", "server-side-key")
    monkeypatch.setattr(
        settings,
        "AI_PROVIDERS",
        {
            "fallback": {
                "kind": "openai_compat",
                "base_url": "https://fallback.example.com/v1",
                "api_key_env": "ZVP5_AI_FALLBACK_KEY",
            }
        },
        raising=False,
    )

    entry = (await credentials.catalog(USER))[0]

    assert entry["scope"] == "server"
    assert entry["api_key_cipher"] is None
    assert await credentials.resolve_api_key(entry) == "server-side-key"


def test_public_view_has_no_secret_fields():
    entry = _row(oid="a" * 24, scope="personal", owner_oid=USER, name="mine")
    entry["api_key"] = None

    view = credentials.public_view(entry)

    assert "api_key" not in view
    assert "api_key_cipher" not in view
    assert SECRET not in "".join(_strings(view))
    assert entry["api_key_cipher"] not in "".join(_strings(view))


@pytest.mark.asyncio
async def test_list_entries_is_admin_only_and_marks_owned(repo):
    repo.rows = {
        row["_id"]: row
        for row in (
            _row(oid="a" * 24, scope="personal", owner_oid=ADMIN, name="mine"),
            _row(oid="b" * 24, scope="personal", owner_oid=OTHER_ADMIN, name="theirs"),
            _row(oid="c" * 24, scope="shared", owner_oid=SHARED_OWNER, name="team"),
        )
    }

    views = await credentials.list_entries({"id": ADMIN, "per": ["admin"]})

    assert [(view["name"], view["owned"]) for view in views] == [
        ("mine", True),
        ("theirs", False),
        ("team", False),
    ]
    assert SECRET not in "".join(_strings(views))

    with pytest.raises(credentials.CredentialError) as failure:
        await credentials.list_entries({"id": USER, "per": []})
    assert (failure.value.code, failure.value.status_code) == ("AI_KEY_FORBIDDEN", 403)


# ---------------------------------------------------------------------------
# 权限：personal 只有 owner，shared 任意 admin
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_personal_credential_is_owner_only(repo):
    repo.rows = {
        "a" * 24: _row(oid="a" * 24, scope="personal", owner_oid=OTHER_ADMIN, name="theirs")
    }
    actor = {"id": ADMIN, "per": ["admin"]}

    with pytest.raises(credentials.CredentialError) as update_failure:
        await credentials.update_entry(
            oid="a" * 24, actor=actor, fields={"base_url": "https://api.example.com/v2"}
        )
    assert (update_failure.value.code, update_failure.value.status_code) == (
        "AI_KEY_FORBIDDEN",
        403,
    )

    with pytest.raises(credentials.CredentialError) as delete_failure:
        await credentials.delete_entry(oid="a" * 24, actor=actor)
    assert (delete_failure.value.code, delete_failure.value.status_code) == (
        "AI_KEY_FORBIDDEN",
        403,
    )

    # 没被改也没被删。
    assert repo.rows["a" * 24]["base_url"] == "https://api.example.com/v1"


@pytest.mark.asyncio
async def test_personal_credential_owner_can_update(repo, monkeypatch):
    _offline_resolver(monkeypatch)
    repo.rows = {"a" * 24: _row(oid="a" * 24, scope="personal", owner_oid=ADMIN, name="mine")}

    view = await credentials.update_entry(
        oid="a" * 24,
        actor={"id": ADMIN, "per": ["admin"]},
        fields={"base_url": "https://api.example.com/v2", "models": ["m2", "m2", "m3"]},
    )

    assert view["base_url"] == "https://api.example.com/v2"
    assert view["models"] == ["m2", "m3"]  # 重复模型去重
    assert view["owned"] is True


@pytest.mark.asyncio
async def test_shared_credential_is_mutable_by_any_admin(repo, monkeypatch):
    _offline_resolver(monkeypatch)
    repo.rows = {
        "c" * 24: _row(oid="c" * 24, scope="shared", owner_oid=SHARED_OWNER, name="team")
    }

    view = await credentials.update_entry(
        oid="c" * 24,
        actor={"id": ADMIN, "per": ["admin"]},
        fields={"enabled": False},
    )
    assert view["enabled"] is False

    await credentials.delete_entry(oid="c" * 24, actor={"id": OTHER_ADMIN, "per": ["admin"]})
    assert "c" * 24 not in repo.rows


@pytest.mark.asyncio
async def test_non_admin_cannot_mutate(repo):
    repo.rows = {
        "c" * 24: _row(oid="c" * 24, scope="shared", owner_oid=SHARED_OWNER, name="team")
    }

    with pytest.raises(credentials.CredentialError) as failure:
        await credentials.update_entry(
            oid="c" * 24, actor={"id": USER, "per": []}, fields={"enabled": False}
        )
    assert (failure.value.code, failure.value.status_code) == ("AI_KEY_FORBIDDEN", 403)


# ---------------------------------------------------------------------------
# base_url：内网/环回/云元数据/坏 scheme/带 query 一律 400
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "base_url",
    [
        "http://127.0.0.1:11434/v1",  # 本机 Ollama
        "http://10.1.2.3/v1",  # 内网
        "http://192.168.1.5/v1",  # 内网
        "http://localhost:8080/v1",  # 主机名形式的内网
        "http://169.254.169.254/latest",  # 云元数据
        "http://[::1]:8080/v1",  # IPv6 环回
        "ftp://x/v1",  # scheme 不支持
        "https://api.example.com/v1?token=1",  # 带 query：拼 /chat/completions 会坏
        "https://api.example.com/v1#frag",
        "not-a-url",
    ],
)
async def test_base_url_rejects_dangerous_targets(repo, monkeypatch, base_url):
    _offline_resolver(monkeypatch)

    with pytest.raises(credentials.CredentialError) as failure:
        await credentials.create_entry(
            user_oid=ADMIN,
            scope="shared",
            name="bad",
            base_url=base_url,
            models=["m"],
            api_key=SECRET,
        )

    assert (failure.value.code, failure.value.status_code) == ("AI_KEY_INVALID", 400)
    assert repo.rows == {}  # 校验失败不得落库


@pytest.mark.asyncio
async def test_base_url_accepts_public_target_and_strips_trailing_slash(repo, monkeypatch):
    _offline_resolver(monkeypatch)

    view = await credentials.create_entry(
        user_oid=ADMIN,
        scope="shared",
        name="good",
        base_url="http://8.8.8.8:8080/v1/",  # IP 字面量：不触发 DNS
        models=["m"],
        api_key=SECRET,
    )

    assert view["base_url"] == "http://8.8.8.8:8080/v1"


# ---------------------------------------------------------------------------
# 唯一键冲突 / 非法入参
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_duplicate_name_maps_to_409(repo, monkeypatch):
    _offline_resolver(monkeypatch)
    repo.rows = {
        "c" * 24: _row(oid="c" * 24, scope="shared", owner_oid=SHARED_OWNER, name="team")
    }

    with pytest.raises(credentials.CredentialError) as failure:
        await credentials.create_entry(
            user_oid=ADMIN,
            scope="shared",
            name="team",
            base_url="http://8.8.8.8/v1",
            models=["m"],
            api_key=SECRET,
        )
    assert (failure.value.code, failure.value.status_code) == ("AI_KEY_DUPLICATE", 409)


@pytest.mark.asyncio
async def test_rename_onto_existing_name_maps_to_409(repo, monkeypatch):
    _offline_resolver(monkeypatch)
    repo.rows = {
        row["_id"]: row
        for row in (
            _row(oid="c" * 24, scope="shared", owner_oid=SHARED_OWNER, name="team"),
            _row(oid="d" * 24, scope="shared", owner_oid=SHARED_OWNER, name="other"),
        )
    }

    with pytest.raises(credentials.CredentialError) as failure:
        await credentials.update_entry(
            oid="d" * 24, actor={"id": ADMIN, "per": ["admin"]}, fields={"name": "team"}
        )
    assert (failure.value.code, failure.value.status_code) == ("AI_KEY_DUPLICATE", 409)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kwargs,code",
    [
        ({"scope": "server"}, "AI_KEY_INVALID"),  # server 只能由运维改
        ({"name": "Bad Name"}, "AI_KEY_INVALID"),
        ({"api_key": "short"}, "AI_KEY_INVALID"),
        ({"models": [f"m{i}" for i in range(21)]}, "AI_KEY_INVALID"),
    ],
)
async def test_invalid_input_is_400(repo, monkeypatch, kwargs, code):
    _offline_resolver(monkeypatch)
    payload = {
        "user_oid": ADMIN,
        "scope": "shared",
        "name": "good",
        "base_url": "http://8.8.8.8/v1",
        "models": ["m"],
        "api_key": SECRET,
    }
    payload.update(kwargs)

    with pytest.raises(credentials.CredentialError) as failure:
        await credentials.create_entry(**payload)

    assert failure.value.code == code
    assert failure.value.status_code == 400


# ---------------------------------------------------------------------------
# 缓存失效
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_crud_invalidates_the_catalog_cache_immediately(repo, monkeypatch):
    """TTL 内也必须看到新值：失效是 CRUD 的一部分，不是等 30 秒。"""
    _offline_resolver(monkeypatch)
    monkeypatch.setattr(ai_config, "credentials_cache_ttl", lambda: 300.0)

    assert await credentials.catalog(ADMIN) == []

    created = await credentials.create_entry(
        user_oid=ADMIN,
        scope="shared",
        name="team",
        base_url="http://8.8.8.8/v1",
        models=["m1"],
        api_key=SECRET,
    )
    assert [entry["name"] for entry in await credentials.catalog(ADMIN)] == ["team"]

    await credentials.update_entry(
        oid=created["oid"], actor={"id": ADMIN, "per": ["admin"]}, fields={"models": ["m2"]}
    )
    assert (await credentials.catalog(ADMIN))[0]["models"] == ["m2"]

    await credentials.delete_entry(oid=created["oid"], actor={"id": ADMIN, "per": ["admin"]})
    assert await credentials.catalog(ADMIN) == []


@pytest.mark.asyncio
async def test_catalog_cache_is_real_so_invalidation_has_meaning(repo, monkeypatch):
    """反向对照：绕过服务层直接改仓储时目录仍是旧值（证明缓存生效）。"""
    monkeypatch.setattr(ai_config, "credentials_cache_ttl", lambda: 300.0)
    repo.rows = {
        "c" * 24: _row(oid="c" * 24, scope="shared", owner_oid=SHARED_OWNER, name="team")
    }

    assert [entry["name"] for entry in await credentials.catalog(ADMIN)] == ["team"]

    repo.rows["c" * 24]["name"] = "renamed"
    assert [entry["name"] for entry in await credentials.catalog(ADMIN)] == ["team"]

    await credentials.invalidate()
    assert [entry["name"] for entry in await credentials.catalog(ADMIN)] == ["renamed"]


@pytest.mark.asyncio
async def test_catalog_cache_is_shared_through_redis_and_invalidate_clears_it(
    repo, monkeypatch
):
    """配了 Redis 时目录缓存落 ``ai:cred:<user_oid>``：命中不回源，失效立刻双清。"""
    from db import cache as db_cache
    from fake_redis import FakeRedis

    client = FakeRedis()
    db_cache._install_client_for_tests(client)
    monkeypatch.setattr(ai_config, "credentials_cache_ttl", lambda: 300.0)
    repo.rows = {
        "c" * 24: _row(oid="c" * 24, scope="shared", owner_oid=SHARED_OWNER, name="team")
    }

    assert [entry["name"] for entry in await credentials.catalog(ADMIN)] == ["team"]
    assert f"zvms:state:kv:ai:cred:{ADMIN}" in client.kv
    # 进程内 dict 不该同时留一份：真相只有一个。
    assert credentials._cache == {}
    reads = repo.calls.count("list_visible")

    repo.rows["c" * 24]["name"] = "renamed"
    assert [entry["name"] for entry in await credentials.catalog(ADMIN)] == ["team"]
    assert repo.calls.count("list_visible") == reads  # 命中缓存，没回源

    await credentials.invalidate()
    assert f"zvms:state:kv:ai:cred:{ADMIN}" not in client.kv
    assert [entry["name"] for entry in await credentials.catalog(ADMIN)] == ["renamed"]


@pytest.mark.asyncio
async def test_redis_cached_entries_are_still_filtered_at_read_time(repo, monkeypatch):
    """Redis 往返会把 ``datetime`` 编成字符串，出口过滤的口径不能因此变松。"""
    from db import cache as db_cache
    from fake_redis import FakeRedis

    client = FakeRedis()
    db_cache._install_client_for_tests(client)
    monkeypatch.setattr(ai_config, "credentials_cache_ttl", lambda: 300.0)

    def _cached(oid: str, name: str, expires_at) -> dict:
        return {
            **_row(oid=oid, scope="shared", owner_oid=SHARED_OWNER, name=name),
            "expires_at": expires_at,
        }

    entries = [
        _cached("a" * 24, "old", datetime.now() - timedelta(seconds=1)),
        _cached("b" * 24, "fresh", datetime.now() + timedelta(days=1)),
        _cached("c" * 24, "forever", None),
    ]
    assert await state.kv_set(f"ai:cred:{USER}", entries, 300.0)

    served = await credentials.catalog(USER)

    assert [entry["name"] for entry in served] == ["fresh", "forever"]
    assert repo.calls == []  # 命中缓存，没有回源


# ---------------------------------------------------------------------------
# 模型条目的两种形态 + 上下文窗口自动填充（R4）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_models_accept_objects_with_context_and_keep_the_shape(
    repo, monkeypatch
) -> None:
    """``{"id", "context"}`` 与字符串混用：原形态落库，视图拆成 ids + contexts。"""
    _offline_resolver(monkeypatch)

    view = await credentials.create_entry(
        user_oid=ADMIN,
        scope="shared",
        name="lab",
        base_url="https://api.example.com/v1",
        models=["gpt-x", {"id": "gpt-y", "context": 128000}],
        api_key=SECRET,
    )

    assert view["models"] == ["gpt-x", "gpt-y"]
    assert view["model_contexts"] == {"gpt-x": None, "gpt-y": 128000}
    stored = repo.rows[view["oid"]]["models"]
    # 字符串还是字符串：把老数据改写成对象没有收益。
    assert stored == ["gpt-x", {"id": "gpt-y", "context": 128000}]


@pytest.mark.asyncio
async def test_bad_model_context_is_a_400(repo, monkeypatch) -> None:
    _offline_resolver(monkeypatch)

    with pytest.raises(credentials.CredentialError) as failure:
        await credentials.create_entry(
            user_oid=ADMIN,
            scope="shared",
            name="lab",
            base_url="https://api.example.com/v1",
            models=[{"id": "gpt-x", "context": "big"}],
            api_key=SECRET,
        )

    assert failure.value.code == "AI_KEY_INVALID"


@pytest.mark.asyncio
async def test_saving_fills_context_from_the_upstream_models_endpoint(
    repo, monkeypatch
) -> None:
    """探测成功 → 空窗口被补上；管理员手填的值不被覆盖。"""
    _offline_resolver(monkeypatch)
    monkeypatch.setattr(settings, "AI_MODEL_PROBE_TIMEOUT_SECONDS", 2.0, raising=False)
    seen: dict = {}

    async def fake_discover(*, base_url, api_key, headers=None):
        seen["base_url"] = base_url
        return {"gpt-x": 64000, "gpt-y": 32000}

    monkeypatch.setattr(credentials, "_discover_model_contexts", fake_discover)

    view = await credentials.create_entry(
        user_oid=ADMIN,
        scope="shared",
        name="lab",
        base_url="https://api.example.com/v1",
        models=["gpt-x", {"id": "gpt-y", "context": 999}],
        api_key=SECRET,
    )

    assert seen["base_url"] == "https://api.example.com/v1"
    assert view["model_contexts"] == {"gpt-x": 64000, "gpt-y": 999}


@pytest.mark.asyncio
async def test_failed_probe_keeps_the_models_untouched(repo, monkeypatch) -> None:
    _offline_resolver(monkeypatch)
    monkeypatch.setattr(settings, "AI_MODEL_PROBE_TIMEOUT_SECONDS", 2.0, raising=False)

    async def fake_discover(*, base_url, api_key, headers=None):
        return {}

    monkeypatch.setattr(credentials, "_discover_model_contexts", fake_discover)

    view = await credentials.create_entry(
        user_oid=ADMIN,
        scope="shared",
        name="lab",
        base_url="https://api.example.com/v1",
        models=["gpt-x"],
        api_key=SECRET,
    )

    assert view["models"] == ["gpt-x"]
    assert view["model_contexts"] == {"gpt-x": None}
    assert repo.rows[view["oid"]]["models"] == ["gpt-x"]


@pytest.mark.asyncio
async def test_catalog_parses_object_models_and_exposes_model_context(
    repo, monkeypatch
) -> None:
    """目录把两种形态都摊平成「模型名 + 窗口」，同步查询 ``model_context`` 可用。"""
    _offline_resolver(monkeypatch)
    repo.rows = {
        "a" * 24: dict(
            _row(oid="a" * 24, scope="shared", owner_oid=SHARED_OWNER, name="lab"),
            models=["gpt-x", {"id": "gpt-y", "context": 128000}],
        )
    }

    entries = await credentials.catalog(ADMIN)

    assert entries[0]["models"] == ["gpt-x", "gpt-y"]
    assert entries[0]["model_contexts"] == {"gpt-x": None, "gpt-y": 128000}
    assert credentials.model_context("lab/gpt-y") == 128000
    assert credentials.model_context("gpt-y") == 128000
    assert credentials.model_context("lab/gpt-x") is None
    assert set(await ai_config.allowed_models_for(ADMIN)) == {
        "lab/gpt-x",
        "lab/gpt-y",
        "gpt-x",
        "gpt-y",
    }
