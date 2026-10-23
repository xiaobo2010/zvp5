"""LLM 自定义 header / api_path：服务层 + 仓储层（车道 2a）。

接非 OpenAI 形状的 token plan（``X-Api-Key``、``/v1/messages`` 之类）需要在凭据
上多存两样东西，这里钉住四件事：

* **默认不变**：没配 ``api_path``/``headers`` 的存量 provider，地址、``Bearer``
  头、对外行为必须与加列之前逐字节一致；
* **覆盖语义**：自定义头同名覆盖默认值（HTTP 头大小写不敏感）、新键合并；
* **秘密只进请求**：头值可能就是 token，``public_view`` 一律 ``****``，日志与
  审计都不许出现；
* **入库校验**：非法头名 / CR/LF 注入 / 绝对 URL 式 path 一律 400，不落库。

上游 httpx 全部 stub（不发请求），仓储用内存假实现（不连库）；SQL 形状单独用
``_insert_stmt``/``_update_values`` 的编译结果钉。
"""

from __future__ import annotations

import ipaddress
import json

import pytest
from sqlalchemy.dialects import oracle

import settings
from db import backends
from db.repos.oracle import ai_credentials as ai_credentials_repo
from services.ai import audit, config as ai_config, credentials, crypto, providers

ADMIN = "000000000000000000000001"
USER = "000000000000000000000003"
SHARED_OWNER = credentials.SHARED_OWNER

SECRET = "sk-unit-test-0123456789abcdef"
HEADER_SECRET = "header-secret-0123456789"
DIALECT = oracle.dialect()

# ---------------------------------------------------------------------------
# 上游 stub：只记录「发了什么」，不真发
# ---------------------------------------------------------------------------


class FakeResponse:
    status_code = 200
    text = '{"ok": true}'

    def json(self) -> dict:
        return {"ok": True}

    async def aread(self) -> bytes:
        return self.text.encode()


class FakeStream:
    def __init__(self, client: "FakeClient", url: str, headers: dict) -> None:
        self.status_code = 200
        self._chunks = [b'data: {"ok": true}\n\n']

    async def __aenter__(self) -> "FakeStream":
        return self

    async def __aexit__(self, *exc_info) -> bool:
        return False

    async def aiter_raw(self):
        for chunk in self._chunks:
            yield chunk


class FakeClient:
    """``httpx.AsyncClient`` 的最小子集：够 providers 两条路径走完。"""

    def __init__(self) -> None:
        self.requests: list[dict] = []

    async def post(self, url: str, *, json=None, headers=None, timeout=None):
        self.requests.append({"url": url, "json": json, "headers": headers})
        return FakeResponse()

    def stream(self, method: str, url: str, *, json=None, headers=None, timeout=None):
        self.requests.append({"url": url, "json": json, "headers": headers})
        return FakeStream(self, url, headers)


@pytest.fixture
def upstream(monkeypatch) -> FakeClient:
    client = FakeClient()
    monkeypatch.setattr(providers, "_client_instance", lambda: client)
    return client


# ---------------------------------------------------------------------------
# 假仓储
# ---------------------------------------------------------------------------


class DuplicateCredential(Exception):
    """与 Oracle 仓储同类名（``credentials._is_duplicate`` 按类名判定）。"""


class FakeRepo:
    """行形状与 ``OracleAICredentialsRepo._row`` 保持一致。"""

    def __init__(self, rows: list[dict] | None = None) -> None:
        self.rows = {row["_id"]: dict(row) for row in rows or []}

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

    async def create(self, **fields) -> str:
        for row in self.rows.values():
            if (
                row["scope"] == fields["scope"]
                and row["owner_oid"] == fields["owner_oid"]
                and row["name"] == fields["name"]
            ):
                raise DuplicateCredential(fields["name"], fields["scope"])
        oid = f"{len(self.rows) + 100:024x}"
        headers = fields.get("headers")
        self.rows[oid] = {
            "_id": oid,
            "scope": fields["scope"],
            "owner_oid": fields["owner_oid"],
            "name": fields["name"],
            "kind": fields["kind"],
            "base_url": fields["base_url"],
            "models": list(fields["models"]),
            # 真仓储用 JSONText/json.dumps 落库：这里存字符串，读取侧必须能解析。
            "headers_json": json.dumps(headers) if headers is not None else None,
            "api_path": fields.get("api_path"),
            "api_key_cipher": fields["api_key_cipher"],
            "key_hint": fields.get("key_hint"),
            "enabled": fields["enabled"],
            "created_at": None,
            "updated_at": None,
        }
        return oid

    async def update(self, oid: str, **fields) -> bool:
        row = self.rows.get(oid)
        if row is None:
            return False
        headers = fields.pop("headers", None)
        if headers is not None:
            row["headers_json"] = json.dumps(headers)
        row.update(fields)
        return True

    async def delete(self, oid: str) -> bool:
        return self.rows.pop(oid, None) is not None


class FakeCallLogRepo:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    async def insert(self, **fields) -> None:
        self.rows.append(fields)


def _row(oid: str, *, headers_json=None, api_path=None) -> dict:
    return {
        "_id": oid,
        "scope": "shared",
        "owner_oid": SHARED_OWNER,
        "name": "plan",
        "kind": "openai_compat",
        "base_url": "https://api.example.com/v1",
        "models": ["m"],
        "headers_json": headers_json,
        "api_path": api_path,
        "api_key_cipher": crypto.encrypt_secret(SECRET),
        "key_hint": SECRET[-4:],
        "enabled": True,
        "created_at": None,
        "updated_at": None,
    }


def _strings(value) -> list[str]:
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
    credentials._cache.clear()
    monkeypatch.setenv(crypto._SECRET_ENV, "unit-test-master-secret")
    monkeypatch.setattr(crypto, "_master_key", None)
    monkeypatch.setattr(settings, "AI_PROVIDERS", {}, raising=False)
    monkeypatch.setattr(
        credentials,
        "_resolve_host",
        lambda host: [
            ipaddress.ip_address(
                host if _is_ip(host) else ("127.0.0.1" if host == "localhost" else "93.184.216.34")
            )
        ],
    )
    yield
    credentials._cache.clear()
    crypto._master_key = None


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


@pytest.fixture
def repo(monkeypatch) -> FakeRepo:
    fake = FakeRepo()
    monkeypatch.setattr(backends, "get_ai_credentials_repo", lambda session=None: fake)
    return fake


# ---------------------------------------------------------------------------
# providers：默认行为不变
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_default_endpoint_and_bearer_headers_unchanged(upstream):
    entry = {"base_url": "https://api.example.com/v1", "name": "plain"}

    assert await providers.chat_completion({"model": "m"}, entry, SECRET) == {"ok": True}

    request = upstream.requests[0]
    assert request["url"] == "https://api.example.com/v1/chat/completions"
    assert request["headers"] == {
        "Authorization": f"Bearer {SECRET}",
        "Content-Type": "application/json",
    }


@pytest.mark.asyncio
async def test_missing_credentials_still_raise_the_same_error(upstream):
    from services.ai.routing import RouteError

    with pytest.raises(RouteError) as failure:
        await providers.chat_completion({}, {"base_url": "https://x/v1"}, "")

    assert (failure.value.code, failure.value.status_code) == ("AI_UPSTREAM_AUTH", 502)
    assert upstream.requests == []


@pytest.mark.asyncio
async def test_api_path_replaces_the_default_completions_path(upstream):
    entry = {
        "base_url": "https://plan.example.com/api/",
        "api_path": "/v1/messages",
        "name": "plan",
    }

    await providers.chat_completion({"model": "m"}, entry, SECRET)

    assert upstream.requests[0]["url"] == "https://plan.example.com/api/v1/messages"


@pytest.mark.asyncio
async def test_empty_api_path_falls_back_to_the_default(upstream):
    entry = {"base_url": "https://api.example.com/v1", "api_path": "", "name": "plain"}

    await providers.chat_completion({}, entry, SECRET)

    assert upstream.requests[0]["url"] == "https://api.example.com/v1/chat/completions"


# ---------------------------------------------------------------------------
# providers：自定义头的覆盖与合并
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_custom_authorization_replaces_bearer(upstream):
    entry = {
        "base_url": "https://plan.example.com/v1",
        "headers": {"Authorization": "Token plan-token"},
        "name": "plan",
    }

    await providers.chat_completion({}, entry, SECRET)

    headers = upstream.requests[0]["headers"]
    assert headers["Authorization"] == "Token plan-token"
    assert f"Bearer {SECRET}" not in _strings(headers)


@pytest.mark.asyncio
async def test_header_override_is_case_insensitive(upstream):
    entry = {
        "base_url": "https://plan.example.com/v1",
        "headers": {"authorization": "Token plan-token"},
        "name": "plan",
    }

    await providers.chat_completion({}, entry, SECRET)

    headers = upstream.requests[0]["headers"]
    # HTTP 头大小写不敏感：不允许同时发出两条 Authorization。
    assert [key for key in headers if key.lower() == "authorization"] == ["authorization"]


@pytest.mark.asyncio
async def test_extra_headers_are_merged_with_defaults(upstream):
    entry = {
        "base_url": "https://plan.example.com/v1",
        "headers": {"X-Api-Key": HEADER_SECRET, "X-Title": "zvms"},
        "name": "plan",
    }

    await providers.chat_completion({}, entry, SECRET)

    headers = upstream.requests[0]["headers"]
    assert headers == {
        "Authorization": f"Bearer {SECRET}",
        "Content-Type": "application/json",
        "X-Api-Key": HEADER_SECRET,
        "X-Title": "zvms",
    }


@pytest.mark.asyncio
async def test_stream_uses_the_custom_path_and_headers(upstream):
    entry = {
        "base_url": "https://plan.example.com/v1",
        "api_path": "/messages",
        "headers": {"X-Api-Key": HEADER_SECRET},
        "name": "plan",
    }

    chunks = [chunk async for chunk in providers.chat_completion_stream({}, entry, SECRET)]

    assert chunks == [b'data: {"ok": true}\n\n']
    request = upstream.requests[0]
    assert request["url"] == "https://plan.example.com/v1/messages"
    assert request["headers"]["X-Api-Key"] == HEADER_SECRET


# ---------------------------------------------------------------------------
# credentials：entry 契约 / 目录 / 公开视图
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_catalog_parses_headers_json_and_api_path(repo):
    repo.rows = {
        "c" * 24: _row(
            "c" * 24,
            headers_json=json.dumps({"X-Api-Key": HEADER_SECRET}),
            api_path="/v1/messages",
        )
    }

    entry = (await credentials.catalog(USER))[0]

    assert entry["headers"] == {"X-Api-Key": HEADER_SECRET}
    assert entry["api_path"] == "/v1/messages"
    # 目录是发请求用的：值必须是明文，否则上游鉴权不可能成功。
    assert HEADER_SECRET in _strings(entry)


@pytest.mark.asyncio
async def test_catalog_tolerates_broken_headers_json(repo):
    repo.rows = {"c" * 24: _row("c" * 24, headers_json="{not json", api_path="//evil.com")}

    entry = (await credentials.catalog(USER))[0]

    assert entry["headers"] == {}
    assert entry["api_path"] is None


@pytest.mark.asyncio
async def test_catalog_without_new_columns_keeps_the_old_shape(repo):
    repo.rows = {"c" * 24: _row("c" * 24)}

    entry = (await credentials.catalog(USER))[0]

    assert entry["headers"] == {}
    assert entry["api_path"] is None


@pytest.mark.asyncio
async def test_server_scope_settings_pass_headers_and_path_through(repo, monkeypatch):
    monkeypatch.setenv("ZVMS_AI_PLAN_KEY", "server-plan-key")
    monkeypatch.setattr(
        settings,
        "AI_PROVIDERS",
        {
            "plan": {
                "kind": "openai_compat",
                "base_url": "https://plan.example.com/v1",
                "api_key_env": "ZVMS_AI_PLAN_KEY",
                "headers": {"X-Api-Key": HEADER_SECRET, "Bad Name": "dropped"},
                "api_path": "/v1/messages",
            }
        },
        raising=False,
    )

    cfg = ai_config.providers()["plan"]
    assert cfg["headers"] == {"X-Api-Key": HEADER_SECRET}
    assert cfg["api_path"] == "/v1/messages"

    entry = (await credentials.catalog(USER))[0]
    assert entry["scope"] == "server"
    assert entry["headers"] == {"X-Api-Key": HEADER_SECRET}
    assert entry["api_path"] == "/v1/messages"


def test_config_skips_illegal_server_shape(monkeypatch):
    monkeypatch.setattr(
        settings,
        "AI_PROVIDERS",
        {
            "broken": {
                "kind": "openai_compat",
                "base_url": "https://x/v1",
                "headers": {"X-Ok": "v", "X:Injected": "v"},
                "api_path": "//evil.com/v1",
            },
            "not-a-dict": "nope",
        },
        raising=False,
    )

    view = ai_config.providers()

    assert set(view) == {"broken"}
    assert view["broken"]["headers"] == {"X-Ok": "v"}
    assert "api_path" not in view["broken"]


@pytest.mark.asyncio
async def test_public_view_masks_header_values_and_keeps_names(repo):
    repo.rows = {
        "c" * 24: _row(
            "c" * 24,
            headers_json=json.dumps({"X-Api-Key": HEADER_SECRET}),
            api_path="/v1/messages",
        )
    }

    views = await credentials.list_entries({"id": ADMIN, "per": ["admin"]})

    view = views[0]
    assert view["headers"] == {"X-Api-Key": "****"}
    assert view["api_path"] == "/v1/messages"
    assert HEADER_SECRET not in "".join(_strings(views))


@pytest.mark.asyncio
async def test_audit_and_logs_never_receive_header_values(repo, upstream, monkeypatch, caplog):
    call_logs = FakeCallLogRepo()
    monkeypatch.setattr(backends, "get_ai_call_logs_repo", lambda session=None: call_logs)
    repo.rows = {
        "c" * 24: _row(
            "c" * 24,
            headers_json=json.dumps({"X-Api-Key": HEADER_SECRET}),
            api_path="/v1/messages",
        )
    }
    caplog.set_level("DEBUG")

    entry = (await credentials.catalog(USER))[0]
    await providers.chat_completion({"model": "m"}, entry, SECRET)
    await audit.record(
        user_oid=USER,
        capability="chat",
        endpoint=audit.ENDPOINT_CHAT,
        provider=str(entry.get("name") or ""),
        model="m",
        key_scope=entry.get("scope"),
    )

    assert HEADER_SECRET not in caplog.text
    assert call_logs.rows
    assert HEADER_SECRET not in "".join(_strings(call_logs.rows))
    # 反向对照：这次请求真的带上了那个头，上面的「没泄露」才有意义。
    assert upstream.requests[0]["headers"]["X-Api-Key"] == HEADER_SECRET


# ---------------------------------------------------------------------------
# credentials：CRUD 与校验
# ---------------------------------------------------------------------------


async def _create(repo, **extra):
    payload = {
        "user_oid": ADMIN,
        "scope": "shared",
        "name": "plan",
        "base_url": "http://8.8.8.8/v1",
        "models": ["m"],
        "api_key": SECRET,
    }
    payload.update(extra)
    return await credentials.create_entry(**payload)


@pytest.mark.asyncio
async def test_create_entry_without_new_params_keeps_the_old_behaviour(repo):
    view = await _create(repo)

    row = repo.rows[view["oid"]]
    assert row["headers_json"] in (None, "{}")
    assert row["api_path"] is None
    assert view["headers"] == {}
    assert view["api_path"] is None


@pytest.mark.asyncio
async def test_create_entry_persists_headers_and_api_path(repo):
    view = await _create(
        repo,
        headers={"X-Api-Key": HEADER_SECRET, "X-Title": "zvms"},
        api_path="/v1/messages",
    )

    row = repo.rows[view["oid"]]
    assert json.loads(row["headers_json"]) == {
        "X-Api-Key": HEADER_SECRET,
        "X-Title": "zvms",
    }
    assert row["api_path"] == "/v1/messages"
    # 返回值是脱敏视图，不是明文回显。
    assert view["headers"] == {"X-Api-Key": "****", "X-Title": "****"}
    assert view["api_path"] == "/v1/messages"


@pytest.mark.asyncio
async def test_update_entry_sets_and_clears_headers(repo):
    view = await _create(repo, headers={"X-Api-Key": HEADER_SECRET})
    actor = {"id": ADMIN, "per": ["admin"]}

    updated = await credentials.update_entry(
        oid=view["oid"],
        actor=actor,
        fields={},
        headers={"X-New-Key": HEADER_SECRET},
        api_path="/v1/messages",
    )
    row = repo.rows[view["oid"]]
    assert json.loads(row["headers_json"]) == {"X-New-Key": HEADER_SECRET}
    assert row["api_path"] == "/v1/messages"
    assert updated["headers"] == {"X-New-Key": "****"}

    cleared = await credentials.update_entry(
        oid=view["oid"], actor=actor, fields={"headers": {}}
    )
    assert json.loads(repo.rows[view["oid"]]["headers_json"]) == {}
    assert cleared["headers"] == {}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "headers",
    [
        {"Bad Name": "v"},  # 头名含空格
        {"X:Injected": "v"},  # 头名含冒号
        {"X-Ünicode": "v"},  # 非 ASCII 头名
        {"": "v"},  # 空头名
        {"X-Ok": 123},  # 值不是字符串
        {"X-Ok": "a" * 513},  # 超长值
        {"X-Ok": "v\r\nX-Evil: 1"},  # CRLF 注入
        {f"X-H{i}": "v" for i in range(17)},  # 头数量上限
    ],
)
async def test_illegal_headers_are_400_and_not_persisted(repo, headers):
    with pytest.raises(credentials.CredentialError) as failure:
        await _create(repo, headers=headers)

    assert (failure.value.code, failure.value.status_code) == ("AI_KEY_INVALID", 400)
    assert repo.rows == {}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "api_path",
    [
        "v1/messages",  # 缺前导 /
        "//evil.com/v1",  # 协议相对 URL：会绕过 base_url 的主机校验
        "http://evil.com/v1",  # 绝对 URL
        "/v1/messages?x=1",  # 带 query 拼出来是坏地址
        "/v1/messages\r\nX-Evil: 1",  # CRLF
        "/v1/a b",  # 空白
        "/" + "a" * 200,  # 超过 200 字符
    ],
)
async def test_illegal_api_path_is_400(repo, api_path):
    with pytest.raises(credentials.CredentialError) as failure:
        await _create(repo, api_path=api_path)

    assert (failure.value.code, failure.value.status_code) == ("AI_KEY_INVALID", 400)
    assert repo.rows == {}


def test_clean_headers_keeps_legal_values_verbatim():
    cleaned = credentials._clean_headers(
        {"X-Api-Key": HEADER_SECRET, "X-Empty": "", "X-Sym": "!#$%&'*+-.^_`|~"}
    )

    assert cleaned == {
        "X-Api-Key": HEADER_SECRET,
        "X-Empty": "",
        "X-Sym": "!#$%&'*+-.^_`|~",
    }
    assert credentials._clean_headers(None) == {}


# ---------------------------------------------------------------------------
# 仓储：新列的投影与绑定
# ---------------------------------------------------------------------------


def _bind_params(stmt) -> dict:
    return stmt.compile(dialect=DIALECT).params


def test_repo_columns_project_the_new_columns():
    names = [column.name for column in ai_credentials_repo._columns()]

    assert "headers_json" in names
    assert "api_path" in names


def test_repo_insert_binds_json_headers_and_path():
    params = _bind_params(
        ai_credentials_repo._insert_stmt(
            oid="a" * 24,
            scope="shared",
            owner_oid=SHARED_OWNER,
            name="plan",
            kind="openai_compat",
            base_url="https://plan.example.com/v1",
            models=["m"],
            api_key_cipher="cipher",
            key_hint="cdef",
            enabled=True,
            created_at=None,
            updated_at=None,
            headers={"X-Api-Key": HEADER_SECRET},
            api_path="/v1/messages",
        )
    )

    assert json.loads(params["headers_json"]) == {"X-Api-Key": HEADER_SECRET}
    assert params["api_path"] == "/v1/messages"


def test_repo_update_distinguishes_unset_from_cleared_headers():
    assert "headers_json" not in ai_credentials_repo._update_values(headers=None)
    assert "api_path" not in ai_credentials_repo._update_values(api_path=None)
    assert ai_credentials_repo._update_values(headers={})["headers_json"] == "{}"
    assert (
        ai_credentials_repo._update_values(api_path="/v1/messages")["api_path"]
        == "/v1/messages"
    )
