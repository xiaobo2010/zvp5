"""AI 供应商目录：个人 key / 共享 key / 服务端兜底三层的合并与可见性。

三层为什么是这三层：

* ``personal`` —— 管理员自己的 key，**只有本人**能用。这是「用自己额度试新模型」
  的通道，所以调用它时不占平台的月度配额（钱本来就不是平台出的）；
* ``shared`` —— 平台出资的 key，所有登录用户都能调，按 ``AI_QUOTA`` 计费；
* ``server`` —— ``settings.AI_PROVIDERS`` 里的兜底项，只能由运维改，接口改不了。

遮蔽规则（同名只留第一个）：个人 > 共享 > 服务端。理由是排障方向单一——管理员
建一个同名 personal 项就能在不影响别人的前提下覆盖平台配置，而不是「到底哪个
生效」取决于某处的插入顺序。

明文只活在一次请求里：``catalog`` 的缓存存的是密文行，解密发生在
:func:`resolve_api_key`，也就是真正要发上游请求的那一刻。
"""

from __future__ import annotations

import ipaddress
import json
import re
import socket
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

import anyio

from db import backends
from db.repos.protocols import SHARED_OWNER_OID

from . import config, crypto

# 共享行的 owner 是 24 个 0 的哨兵（不用 NULL：唯一索引要把共享行一起约束住）。
# 值从协议层取，避免两处常量各自漂移。
SHARED_OWNER = SHARED_OWNER_OID

SCOPE_PERSONAL = "personal"
SCOPE_SHARED = "shared"
SCOPE_SERVER = "server"
_MUTABLE_SCOPES = frozenset({SCOPE_PERSONAL, SCOPE_SHARED})

# 名字会出现在熔断键、审计 provider 列（VARCHAR2(40)）与前端下拉里：
# 限定 ASCII 小写，跨表引用时才不会因为大小写/中文变得 grep 不到。
_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{1,63}$")
_MODEL_MAX_LEN = 80
_MODELS_MAX = 20
_API_KEY_MIN = 8
_API_KEY_MAX = 512

# RFC 7230 token：HTTP 头名的合法字符集。禁空白/冒号/非 ASCII，CR/LF 自然也在外。
_HEADER_NAME_PATTERN = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")
_HEADERS_MAX = 16
_HEADER_VALUE_MAX = 512
_API_PATH_MAX = 200

# 内网/环回：允许管理员把 base_url 指到内网，等于把网关变成一台可被任意 admin
# 驱使的内网 SSRF 跳板（云上 169.254.169.254 尤其危险）。
_BLOCKED_NETWORKS = tuple(
    ipaddress.ip_network(net)
    for net in (
        "127.0.0.0/8",
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "169.254.0.0/16",
        "0.0.0.0/8",
        "::1/128",
        "fc00::/7",
        "fe80::/10",
    )
)

# 进程内缓存：单 worker（与 routing 的熔断状态同一前提）。任何 CRUD 直接清空，
# 不做按用户失效——共享项对所有人可见，按用户失效反而容易漏。
_cache: dict[str, tuple[float, list[dict]]] = {}

# 请求时 SSRF 复核的解析缓存：host → (到期 monotonic, 是否私网)。
# 没有它，每一次上游调用都要多一次 DNS（单 worker 下是纯浪费）；有它，同一个
# host 在 60 秒内只解析一次，也让「录入时是公网、请求时变内网」这种 rebinding
# 至少在一个 TTL 内保持判定稳定。
_SSRF_CACHE_TTL = 60.0
_SSRF_CACHE_MAX = 256
_ssrf_cache: dict[str, tuple[float, bool]] = {}


class CredentialError(Exception):
    """凭据相关的可预期错误，由路由层翻译成 HTTP 状态码。"""

    def __init__(self, code: str, message: str, status_code: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


# ---------------------------------------------------------------------------
# 目录
# ---------------------------------------------------------------------------


async def catalog(user_oid: str) -> list[dict]:
    """该用户当前生效的 provider 列表，顺序 personal → shared → server。

    缓存里存的已经是「未过期」的条目，但 TTL 窗口内条目仍可能到期：所以读缓存
    之后还要按当前时刻再筛一遍（这是「缓存消费处」的过滤点）。不这么做，一把
    5 分钟前到期的 key 会一直出现在 ``/v1/models`` 里，直到缓存自然过期。
    """
    now = time.monotonic()
    cached = _cache.get(user_oid)
    if cached is not None and cached[0] > now:
        return _live_entries(cached[1])

    entries = await _build(user_oid)
    _cache[user_oid] = (now + config.credentials_cache_ttl(), entries)
    return _live_entries(entries)


async def invalidate(user_oid: str | None = None) -> None:
    """清空目录缓存。

    ``user_oid`` 保留在签名里是为了调用方语义清晰；实现上总是全清：共享项的
    变更影响所有人，按用户失效迟早漏掉一条路径。
    """
    _cache.clear()


async def _build(user_oid: str) -> list[dict]:
    rows = await backends.get_ai_credentials_repo().list_visible(user_oid)

    entries: list[dict] = []
    seen: set[str] = set()

    for row in _ordered(rows):
        if not row.get("enabled", True):
            continue
        # 到期与 disabled 是同一条「不进生效目录」的路径：这样路由与
        # /v1/models 都不需要各自再判一次时间。
        if _is_expired(row.get("expires_at")):
            continue
        name = str(row.get("name") or "")
        if not name or name in seen:
            continue
        seen.add(name)
        scope = str(row.get("scope") or "")
        oid = row.get("_id")
        entries.append(
            {
                "key": f"{scope}:{oid or name}",
                "scope": scope,
                "owner_oid": row.get("owner_oid"),
                "name": name,
                "kind": str(row.get("kind") or config.OPENAI_COMPAT),
                "base_url": row.get("base_url"),
                "models": [m for m in (row.get("models") or []) if isinstance(m, str)],
                "headers": _stored_headers(row.get("headers_json")),
                "api_path": _stored_api_path(row.get("api_path")),
                "api_key_cipher": row.get("api_key_cipher"),
                # 服务端兜底的 key 不落缓存（见 resolve_api_key），这里恒为 None。
                "api_key": None,
                "key_hint": row.get("key_hint"),
                "enabled": True,
                "oid": oid,
                "created_at": row.get("created_at"),
                "updated_at": row.get("updated_at"),
                "expires_at": row.get("expires_at"),
            }
        )

    for name, cfg in config.providers().items():
        if name in seen or not config.is_configured(cfg):
            continue
        seen.add(name)
        models = cfg.get("models") or []
        entries.append(
            {
                "key": f"{SCOPE_SERVER}:{name}",
                "scope": SCOPE_SERVER,
                "owner_oid": None,
                "name": name,
                "kind": str(cfg.get("kind") or config.OPENAI_COMPAT),
                "base_url": cfg.get("base_url"),
                "models": [m for m in models if isinstance(m, str)]
                if isinstance(models, list)
                else [],
                "headers": _stored_headers(cfg.get("headers")),
                "api_path": _stored_api_path(cfg.get("api_path")),
                "api_key_cipher": None,
                "api_key": None,
                "key_hint": None,
                "enabled": True,
                "oid": None,
                "created_at": None,
                "updated_at": None,
                # 服务端兜底项没有到期概念：env 里的 key 由运维直接换。
                "expires_at": None,
            }
        )

    return entries


def _live_entries(entries: list[dict]) -> list[dict]:
    """缓存读出口的过滤：只留未到期的条目（返回新列表，不改缓存里的那份）。"""
    return [entry for entry in entries if not _is_expired(entry.get("expires_at"))]


def _is_expired(value: object, now: datetime | None = None) -> bool:
    """``expires_at`` 非空且 ``<= now`` 即视为过期（``None`` = 永不过期）。

    比较口径：仓储出来的是 naive datetime（``TIMESTAMP`` 列的约定），所以
    ``now`` 也用 naive 本地时间。带时区的值先换算到本地再抹掉 tzinfo，避免
    「库里的 12:00 是哪个时区」变成隐式规则。
    """
    expiry = _as_datetime(value)
    if expiry is None:
        return False
    return expiry <= (now or datetime.now())


def _as_datetime(value: object) -> datetime | None:
    """把 ``expires_at`` 归一成 naive 本地 datetime；无法解析时返回 ``None``。"""
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            return value.astimezone().replace(tzinfo=None)
        return value
    if isinstance(value, str) and value.strip():
        return _parse_datetime(value)
    return None


def _ordered(rows: list[dict]) -> list[dict]:
    """personal 在 shared 之前；同层保持仓储顺序（稳定排序）。"""
    return sorted(
        rows or [],
        key=lambda row: 0 if str(row.get("scope") or "") == SCOPE_PERSONAL else 1,
    )


# ---------------------------------------------------------------------------
# 模型解析（合格 ID = ``provider/model``）
# ---------------------------------------------------------------------------
#
# 为什么需要合格 ID：不同渠道可以给出同名模型（两个 provider 都有
# ``gpt-4o-mini``）。只按裸名选路时，「先出现者胜」把另一个渠道彻底变成不可达
# 的死配置——而目录顺序（personal → shared → server）是遮蔽规则，不是路由意图。
# 所以对外标识改成 ``provider/model``，裸名只作为**旧数据**的兼容别名保留。


@dataclass(frozen=True)
class ResolvedModel:
    """一次解析的结果：命中哪个 entry，上游真实模型名是什么。"""

    entry: dict
    #: 发给上游的模型名（去掉 provider 前缀）。
    model: str
    #: 对外/落库的合格 ID。
    qualified: str


class ModelAmbiguous(Exception):
    """裸名在目录里命中多个 provider：调用方必须要求客户端改用合格 ID。

    不用「返回一个魔法字符串」表达歧义：调用方拿到异常时天然带着候选列表，
    而且不会有人误把它当成一个正常的 entry。
    """

    def __init__(self, model: str, candidates: list[str]):
        self.model = model
        self.candidates = list(candidates)
        self.message = (
            f"model '{model}' is ambiguous, use " + " or ".join(self.candidates)
        )
        super().__init__(self.message)


def qualified_model_id(entry: dict, model: str) -> str:
    """``entry + 上游模型名`` → 对外的合格 ID。"""
    return f"{entry.get('name')}/{model}"


def catalog_models(entries: list[dict] | None) -> list[tuple[dict, str, str]]:
    """目录 → ``[(entry, 上游模型名, 合格 ID)]``（保持目录顺序，逐 provider 展开）。"""
    rows: list[tuple[dict, str, str]] = []
    for entry in entries or []:
        for model in entry.get("models") or []:
            if isinstance(model, str) and model:
                rows.append((entry, model, qualified_model_id(entry, model)))
    return rows


def resolve_model(entries: list[dict] | None, model: object) -> ResolvedModel | None:
    """把客户端给的 model 解析成 ``(entry, 上游模型名)``。

    规则（顺序固定）：

    1. 含 ``/`` 且第一段能（大小写不敏感地）匹配某个 entry 的 ``name`` —— 按
       ``provider/model`` 解释，只认这个 provider。该 provider 没有这个模型时
       返回 ``None``：**不**退化成裸名再去别的 provider 里猜，否则「我明确指定了
       渠道」会被静默改写成另一个渠道。
    2. 否则按裸名在目录里精确匹配：唯一命中用它；多个命中抛
       :class:`ModelAmbiguous`；零命中返回 ``None``。
    3. 第一段不是任何 provider 名（模型 id 自己带 ``/``，如
       ``Qwen/Qwen2.5-7B-Instruct``）时同样按裸名整串匹配。
    """
    name = str(model or "").strip()
    if not name:
        return None
    entries = list(entries or [])

    prefix, sep, rest = name.partition("/")
    if sep:
        by_name = {
            str(entry.get("name") or "").lower(): entry
            for entry in entries
            if entry.get("name")
        }
        matched = by_name.get(prefix.lower())
        if matched is not None:
            models = [m for m in matched.get("models") or [] if isinstance(m, str)]
            if not rest or rest not in models:
                return None
            # 前缀大小写不敏感，但落库/回显统一用目录里的规范名。
            return ResolvedModel(
                entry=matched, model=rest, qualified=qualified_model_id(matched, rest)
            )

    hits = [
        (entry, bare)
        for entry, bare, _qualified in catalog_models(entries)
        if bare == name
    ]
    if not hits:
        return None
    if len(hits) > 1:
        raise ModelAmbiguous(
            name, [qualified_model_id(entry, bare) for entry, bare in hits]
        )
    entry, bare = hits[0]
    return ResolvedModel(
        entry=entry, model=bare, qualified=qualified_model_id(entry, bare)
    )


def route_candidates(candidates: list[dict] | None, resolved: ResolvedModel) -> list[dict]:
    """在「未冷却的候选」里挑出能跑这个模型的项，命中的 provider 排最前。

    命中项一定在最前：合格 ID 指定了渠道就是首选，不会被目录顺序改写。其余提供
    **同一上游模型名**的渠道保留在后面，只用于「可重试的上游错误」兜底（与改造前
    的多 provider 重试同一语义），而不是把「用户明确指定 A」悄悄变成「跑在 B」。
    """
    items = list(candidates or [])
    head = [entry for entry in items if entry is resolved.entry]
    tail = [
        entry
        for entry in items
        if entry is not resolved.entry
        and resolved.model in (entry.get("models") or [])
    ]
    return head + tail


def upstream_model_name(entries: list[dict] | None, model: object) -> str:
    """合格 ID → 发给上游的真实模型名；解析不了就原样返回。

    只给「手上已经有合格 ID、马上要拼请求体」的场景用（agent 的每轮 payload）。
    原样返回是刻意的：调用方在这之前已经做过一次 ``resolve_model`` 的校验，这里
    再抛一次只会把一条已通过门禁的请求变成 500。
    """
    try:
        resolved = resolve_model(entries, model)
    except ModelAmbiguous:
        return str(model or "")
    return resolved.model if resolved is not None else str(model or "")


async def resolve_api_key(entry: dict) -> str:
    """真正发请求前才取明文。

    服务端兜底项走 ``settings/env``，用户建的项才解密：把 env 里的 key 复制进
    目录缓存等于让一把长期密钥在进程里多存活一份副本，没有任何收益。
    """
    if entry.get("scope") == SCOPE_SERVER:
        cfg = config.providers().get(str(entry.get("name") or "")) or {}
        key = config.provider_api_key(cfg)
        if not key:
            raise CredentialError(
                "AI_CREDENTIAL_UNREADABLE", "server credential is missing", 502
            )
        return key

    token = entry.get("api_key_cipher")
    if not token:
        raise CredentialError(
            "AI_CREDENTIAL_UNREADABLE", "credential has no secret", 502
        )
    try:
        # AES-GCM 作用在几十字节上，耗时在微秒级，不构成事件循环阻塞。
        return crypto.decrypt_secret(str(token))
    except ValueError:
        raise CredentialError(
            "AI_CREDENTIAL_UNREADABLE",
            "stored credential cannot be decrypted",
            502,
        ) from None


def public_view(entry: dict) -> dict:
    """对外视图：永远不含 api_key / api_key_cipher。

    ``headers`` 只留头名，值统一 ``****``：头值里可能放的就是 token（``X-Api-Key``
    这类非标准鉴权），原样回给前端等于把密钥从后门放出来。``api_path`` 不含秘密，
    明文可见（管理员要知道生效的是哪条路径）。
    """
    return {
        "oid": entry.get("oid"),
        "scope": entry.get("scope"),
        "owner_oid": entry.get("owner_oid"),
        "name": entry.get("name"),
        "kind": entry.get("kind"),
        "base_url": entry.get("base_url"),
        "models": list(entry.get("models") or []),
        "headers": {
            str(name): "****"
            for name in (entry.get("headers") or {})
            if isinstance(name, str)
        },
        "api_path": entry.get("api_path"),
        "key_hint": entry.get("key_hint"),
        "enabled": bool(entry.get("enabled", True)),
        "created_at": entry.get("created_at"),
        "updated_at": entry.get("updated_at"),
        # 与 created_at / updated_at 同样原样透传（datetime 交给 JSON 编码器）。
        "expires_at": entry.get("expires_at"),
    }


# ---------------------------------------------------------------------------
# CRUD（admin）
# ---------------------------------------------------------------------------


async def list_entries(user: dict) -> list[dict]:
    """全部凭据（跨用户），个人在前。仅 admin 可见。"""
    _require_admin(user)
    rows = await backends.get_ai_credentials_repo().list_all()
    actor_id = user.get("id")
    views: list[dict] = []
    for row in _ordered(rows):
        view = public_view(
            {
                "oid": row.get("_id"),
                "scope": row.get("scope"),
                "owner_oid": row.get("owner_oid"),
                "name": row.get("name"),
                "kind": row.get("kind"),
                "base_url": row.get("base_url"),
                "models": row.get("models"),
                "headers": _stored_headers(row.get("headers_json")),
                "api_path": _stored_api_path(row.get("api_path")),
                "key_hint": row.get("key_hint"),
                "enabled": row.get("enabled", True),
                "created_at": row.get("created_at"),
                "updated_at": row.get("updated_at"),
                "expires_at": row.get("expires_at"),
            }
        )
        view["owned"] = (
            view["scope"] == SCOPE_PERSONAL and view["owner_oid"] == actor_id
        )
        views.append(view)
    return views


async def create_entry(
    *,
    user_oid: str,
    scope: str,
    name: str,
    base_url: str,
    models: list[str],
    api_key: str,
    enabled: bool = True,
    headers: dict | None = None,
    api_path: str | None = None,
    expires_at: str | None = None,
) -> dict:
    if scope not in _MUTABLE_SCOPES:
        raise CredentialError("AI_KEY_INVALID", "unknown scope", 400)

    clean_name = _clean_name(name)
    clean_url = await _clean_base_url(base_url)
    clean_models = _clean_models(models)
    secret = _clean_api_key(api_key)
    clean_headers = _clean_headers(headers)
    clean_path = _clean_api_path(api_path)
    clean_expiry = _clean_expires_at(expires_at)
    owner_oid = user_oid if scope == SCOPE_PERSONAL else SHARED_OWNER

    repo = backends.get_ai_credentials_repo()
    try:
        oid = await repo.create(
            scope=scope,
            owner_oid=owner_oid,
            name=clean_name,
            kind=config.OPENAI_COMPAT,
            base_url=clean_url,
            models=clean_models,
            api_key_cipher=crypto.encrypt_secret(secret),
            key_hint=crypto.key_hint(secret),
            enabled=bool(enabled),
            headers=clean_headers,
            api_path=clean_path,
            expires_at=clean_expiry,
        )
    except Exception as exc:
        if _is_duplicate(exc):
            raise CredentialError(
                "AI_KEY_DUPLICATE", "a credential with this name already exists", 409
            ) from None
        raise

    await invalidate()
    row = await repo.get(oid)
    view = public_view(
        {
            "oid": oid,
            "scope": scope,
            "owner_oid": owner_oid,
            "name": clean_name,
            "kind": config.OPENAI_COMPAT,
            "base_url": clean_url,
            "models": clean_models,
            "headers": clean_headers,
            "api_path": clean_path,
            "key_hint": crypto.key_hint(secret),
            "enabled": bool(enabled),
            "created_at": (row or {}).get("created_at"),
            "updated_at": (row or {}).get("updated_at"),
            "expires_at": (row or {}).get("expires_at", clean_expiry),
        }
    )
    view["owned"] = scope == SCOPE_PERSONAL and owner_oid == user_oid
    return view


async def update_entry(
    *,
    oid: str,
    actor: dict,
    fields: dict,
    headers: dict | None = None,
    api_path: str | None = None,
) -> dict:
    repo = backends.get_ai_credentials_repo()
    row = await repo.get(oid)
    if row is None:
        raise CredentialError("AI_KEY_NOT_FOUND", "credential not found", 404)
    _assert_can_mutate(row, actor)

    changes: dict[str, Any] = {}
    if fields.get("name") is not None:
        changes["name"] = _clean_name(fields["name"])
    if fields.get("base_url") is not None:
        changes["base_url"] = await _clean_base_url(fields["base_url"])
    if fields.get("models") is not None:
        changes["models"] = _clean_models(fields["models"])
    if fields.get("enabled") is not None:
        changes["enabled"] = bool(fields["enabled"])
    if fields.get("api_key") is not None:
        secret = _clean_api_key(fields["api_key"])
        changes["api_key_cipher"] = crypto.encrypt_secret(secret)
        changes["key_hint"] = crypto.key_hint(secret)
    # ``expires_at`` 按「键是否存在」判断而不是按值：``fields`` 来自
    # ``model_dump(exclude_unset=True)``，所以「没传」= 键不存在（不改），
    # 显式 ``null`` = 键存在且值为 None（清除到期）。仓储用 UNSET 哨兵区分这两者。
    if "expires_at" in fields:
        changes["expires_at"] = _clean_expires_at(fields["expires_at"])
    # 显式参数优先于 ``fields``：``fields`` 是路由层透传的 payload，显式关键字
    # 是服务层调用方的意图，两者都支持是为了不让路由层改写 payload 形状。
    raw_headers = headers if headers is not None else fields.get("headers")
    if raw_headers is not None:
        changes["headers"] = _clean_headers(raw_headers)
    raw_path = api_path if api_path is not None else fields.get("api_path")
    if raw_path is not None:
        changes["api_path"] = _clean_api_path(raw_path)

    if changes:
        try:
            updated = await repo.update(oid, **changes)
        except Exception as exc:
            if _is_duplicate(exc):
                raise CredentialError(
                    "AI_KEY_DUPLICATE",
                    "a credential with this name already exists",
                    409,
                ) from None
            raise
        if not updated:
            raise CredentialError("AI_KEY_NOT_FOUND", "credential not found", 404)
        await invalidate()

    fresh = await repo.get(oid) or row
    view = public_view(
        {
            "oid": oid,
            "scope": fresh.get("scope"),
            "owner_oid": fresh.get("owner_oid"),
            "name": fresh.get("name"),
            "kind": fresh.get("kind"),
            "base_url": fresh.get("base_url"),
            "models": fresh.get("models"),
            "headers": _stored_headers(fresh.get("headers_json")),
            "api_path": _stored_api_path(fresh.get("api_path")),
            "key_hint": fresh.get("key_hint"),
            "enabled": fresh.get("enabled", True),
            "created_at": fresh.get("created_at"),
            "updated_at": fresh.get("updated_at"),
            "expires_at": fresh.get("expires_at"),
        }
    )
    view["owned"] = (
        view["scope"] == SCOPE_PERSONAL and view["owner_oid"] == actor.get("id")
    )
    return view


async def delete_entry(*, oid: str, actor: dict) -> None:
    repo = backends.get_ai_credentials_repo()
    row = await repo.get(oid)
    if row is None:
        raise CredentialError("AI_KEY_NOT_FOUND", "credential not found", 404)
    _assert_can_mutate(row, actor)

    if not await repo.delete(oid):
        raise CredentialError("AI_KEY_NOT_FOUND", "credential not found", 404)
    await invalidate()


# ---------------------------------------------------------------------------
# 校验与权限
# ---------------------------------------------------------------------------


def _require_admin(actor: dict) -> None:
    """路由层已经拦过一次；服务层再断言一次，避免以后有人绕过路由直接调。"""
    if "admin" not in (actor.get("per") or []):
        raise CredentialError("AI_KEY_FORBIDDEN", "admin required", 403)


def _assert_can_mutate(row: dict, actor: dict) -> None:
    _require_admin(actor)
    if row.get("scope") == SCOPE_PERSONAL and row.get("owner_oid") != actor.get("id"):
        raise CredentialError(
            "AI_KEY_FORBIDDEN", "not the owner of this credential", 403
        )


def _is_duplicate(exc: BaseException) -> bool:
    """仓储撞唯一键时抛 ``DuplicateCredential``（db/repos/oracle/ai_credentials）。

    按类名判定而不是导入那个类：导入它会连带把 SQLAlchemy 的 Oracle 模型拉进
    网关的导入路径，而 ``db.backends`` 刻意保持懒导入。撞键语义由仓储保证，
    类名是两侧约定的接口名。
    """
    return type(exc).__name__ == "DuplicateCredential"


def _clean_name(value: object) -> str:
    name = str(value or "").strip()
    if not _NAME_PATTERN.match(name):
        raise CredentialError(
            "AI_KEY_INVALID",
            "name must match ^[a-z0-9][a-z0-9_-]{1,63}$",
            400,
        )
    return name


def _clean_api_key(value: object) -> str:
    key = str(value or "").strip()
    if not _API_KEY_MIN <= len(key) <= _API_KEY_MAX:
        raise CredentialError(
            "AI_KEY_INVALID",
            f"api_key length must be {_API_KEY_MIN}..{_API_KEY_MAX}",
            400,
        )
    return key


def _clean_models(value: object) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        raise CredentialError("AI_KEY_INVALID", "models must be a list", 400)
    models: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise CredentialError("AI_KEY_INVALID", "models must be strings", 400)
        model = item.strip()
        if not 1 <= len(model) <= _MODEL_MAX_LEN:
            raise CredentialError(
                "AI_KEY_INVALID",
                f"each model must be 1..{_MODEL_MAX_LEN} characters",
                400,
            )
        if model not in models:  # 去重：同一个模型写两遍只会让候选列表变长
            models.append(model)
    if len(models) > _MODELS_MAX:
        raise CredentialError(
            "AI_KEY_INVALID", f"at most {_MODELS_MAX} models per credential", 400
        )
    return models


def _has_crlf(value: str) -> bool:
    return "\r" in value or "\n" in value or "\x00" in value


def _clean_headers(value: object) -> dict[str, str]:
    """额外请求头的入库校验。

    ``X-Api-Key`` 这类头放的就是鉴权 token，所以头值只做「可安全发出去」的检查，
    不做内容判断；头名必须是 RFC 7230 token（冒号/空白/非 ASCII 都可能被上游
    当成两个头或截断，等于让管理员无意中改写请求行）。
    """
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise CredentialError("AI_KEY_INVALID", "headers must be an object", 400)
    if len(value) > _HEADERS_MAX:
        raise CredentialError(
            "AI_KEY_INVALID", f"at most {_HEADERS_MAX} headers per credential", 400
        )
    headers: dict[str, str] = {}
    for raw_name, raw_value in value.items():
        if not isinstance(raw_name, str) or not _HEADER_NAME_PATTERN.match(raw_name):
            raise CredentialError(
                "AI_KEY_INVALID", "header names must be RFC 7230 tokens", 400
            )
        if not isinstance(raw_value, str):
            raise CredentialError(
                "AI_KEY_INVALID", "header values must be strings", 400
            )
        if len(raw_value) > _HEADER_VALUE_MAX:
            raise CredentialError(
                "AI_KEY_INVALID",
                f"header values must be at most {_HEADER_VALUE_MAX} characters",
                400,
            )
        if _has_crlf(raw_name) or _has_crlf(raw_value):
            raise CredentialError(
                "AI_KEY_INVALID", "header names/values must not contain CR/LF", 400
            )
        headers[raw_name] = raw_value
    return headers


def _clean_expires_at(value: object) -> datetime | None:
    """``expires_at`` 的入库校验：``None`` = 永不过期，其余必须晚于「现在」。

    解析口径：ISO 8601（``datetime.fromisoformat``）。带时区（``+08:00`` /
    ``Z``）的先换算到服务器本地再抹掉 tzinfo；**不带时区的按服务器本地时间
    解释**。落库是 naive ``TIMESTAMP``，与 ``created_at`` / ``updated_at``
    同口径，比较时不需要再做时区转换。

    为什么「必须晚于现在」是 400 而不是自动清空：把过去的时刻存下来等于存一条
    「立刻过期」的凭据——管理员想停用就该用 ``enabled``，想让 key 立刻消失就该
    删除。静默接受只会让「我明明设了到期」变成一条查不出原因的 401。
    """
    if value is None:
        return None
    if not isinstance(value, str):
        raise CredentialError(
            "AI_KEY_BAD_EXPIRY", "expires_at must be an ISO 8601 string", 400
        )
    expiry = _parse_datetime(value)
    if expiry is None:
        raise CredentialError(
            "AI_KEY_BAD_EXPIRY", "expires_at must be an ISO 8601 string", 400
        )
    if expiry <= datetime.now():
        raise CredentialError(
            "AI_KEY_BAD_EXPIRY", "expires_at must be in the future", 400
        )
    return expiry


def _parse_datetime(value: str) -> datetime | None:
    """ISO 8601 → naive 本地 datetime；解析不了返回 ``None``（由调用方定错误码）。"""
    text = value.strip()
    if not text:
        return None
    # ``Z`` 是 ISO 8601 的 UTC 后缀，3.11+ 的 fromisoformat 已经认；这里显式
    # 归一化是为了不把行为绑在解释器版本上。
    if text[-1] in ("Z", "z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        return parsed.astimezone().replace(tzinfo=None)
    return parsed


def _clean_api_path(value: object) -> str | None:
    """相对 ``base_url`` 的路径；``None``/空串表示沿用默认 ``/chat/completions``。

    path 没有 host，所以这里防的不是 SSRF 目标，而是「把 path 写成绝对 URL 或
    ``//host/path`` 绕过 base_url 的主机校验」，以及空白/控制字符把请求行拆坏。
    """
    if value is None:
        return None
    path = str(value).strip()
    if not path:
        return None
    if len(path) > _API_PATH_MAX:
        raise CredentialError(
            "AI_KEY_INVALID", f"api_path must be at most {_API_PATH_MAX} characters", 400
        )
    if not path.startswith("/") or path.startswith("//"):
        raise CredentialError(
            "AI_KEY_INVALID", "api_path must start with a single '/'", 400
        )
    if "://" in path:
        raise CredentialError("AI_KEY_INVALID", "api_path must be a relative path", 400)
    if "?" in path or "#" in path:
        # 与 base_url 同理：带 query/fragment 的路径拼出来是坏地址，录入时就说清楚。
        raise CredentialError(
            "AI_KEY_INVALID", "api_path must not contain query or fragment", 400
        )
    if any(ch.isspace() or ch < " " or ch == "\x7f" for ch in path):
        raise CredentialError(
            "AI_KEY_INVALID",
            "api_path must not contain whitespace or control characters",
            400,
        )
    return path


def _stored_headers(raw: object) -> dict[str, str]:
    """读取侧的宽容解析：坏 JSON / 非法头按空处理，绝不因为一行坏数据让目录 500。

    DB 的 ``headers_json`` 经 ``JSONText`` 出来可能已是 dict，settings 里是 dict，
    老数据/手工写的可能是字符串，所以三种形态都要接。
    """
    if hasattr(raw, "read"):
        raw = raw.read()
    if isinstance(raw, (bytes, bytearray)):
        raw = bytes(raw).decode("utf-8", "replace")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return {}
    if not isinstance(raw, dict):
        return {}
    headers: dict[str, str] = {}
    for name, value in raw.items():
        if not isinstance(name, str) or not isinstance(value, str) or not name:
            continue
        if not _HEADER_NAME_PATTERN.match(name) or _has_crlf(name) or _has_crlf(value):
            continue
        headers[name] = value
    return headers


def _stored_api_path(raw: object) -> str | None:
    """读取侧的宽容解析：只接受以单个 ``/`` 开头、无空白的短路径，其余按未设置。"""
    if not isinstance(raw, str):
        return None
    path = raw.strip()
    if not path or path.startswith("//") or "://" in path:
        return None
    if "?" in path or "#" in path:
        return None
    if len(path) > _API_PATH_MAX or not path.startswith("/"):
        return None
    if any(ch.isspace() or ch < " " or ch == "\x7f" for ch in path):
        return None
    return path


async def _clean_base_url(value: object) -> str:
    url = str(value or "").strip()
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise CredentialError(
            "AI_KEY_INVALID", "base_url must be an http(s) URL", 400
        )
    if parts.query or parts.fragment:
        # 我们在 base_url 后面拼 "/chat/completions"：带 query/fragment 时拼出来
        # 的地址是坏的（"...?x=1/chat/completions"），不如在录入时就说清楚。
        raise CredentialError(
            "AI_KEY_INVALID", "base_url must not contain query or fragment", 400
        )
    host = parts.hostname
    addresses = await anyio.to_thread.run_sync(_resolve_host, host)
    for address in addresses:
        if _is_blocked(address):
            raise CredentialError(
                "AI_KEY_INVALID", "base_url must not point to a private address", 400
            )
    return url.rstrip("/")


def _resolve_host(host: str) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    """DNS 解析放在线程里：``getaddrinfo`` 是同步阻塞调用，会卡住整个事件循环。"""
    addresses = _lookup_addresses(host)
    if not addresses:
        raise CredentialError(
            "AI_KEY_INVALID", "base_url host cannot be resolved", 400
        )
    return addresses


def _lookup_addresses(
    host: str,
) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address] | None:
    """解析失败返回 ``None``（而不是抛）：写入侧要报错、请求侧只是放过。"""
    try:
        infos = socket.getaddrinfo(host, None)
    except (OSError, UnicodeError):
        return None
    addresses = []
    for info in infos:
        try:
            addresses.append(ipaddress.ip_address(info[4][0]))
        except ValueError:
            continue
    return addresses or None


def _is_blocked(address) -> bool:
    if address.version == 6 and address.ipv4_mapped is not None:
        # ::ffff:127.0.0.1 是环回的另一种写法，不折叠就会绕过 IPv4 黑名单。
        address = address.ipv4_mapped
    return any(
        address in network
        for network in _BLOCKED_NETWORKS
        if network.version == address.version
    )


def host_of_url(value: object) -> str:
    """URL（或裸 host，如阿里 OCR 的 ``endpoint``）→ 小写 hostname；拿不到返回空串。

    Tea SDK 的 endpoint 与百度/Groq 的 base_url 形状不同（前者常常没有 scheme），
    所以没有 ``://`` 时按「裸 host[:port]」解析——少了这一步，``127.0.0.1:8080``
    会被当成一个普通路径，私网判定直接失效。
    """
    if not isinstance(value, str) or not value.strip():
        return ""
    raw = value.strip()
    candidate = raw if ("://" in raw or raw.startswith("//")) else "//" + raw
    try:
        parts = urlsplit(candidate)
        return (parts.hostname or "").lower()
    except ValueError:
        return ""


async def host_is_private(host: str) -> bool:
    """``host`` 解析出来的地址里是否有私网/环回（结果缓存 60 秒）。

    写入侧（:func:`_clean_base_url`）在**录入时**拦一次，这里给**请求时**再用一次：
    两次之间 DNS 可能被换成内网地址（DNS rebinding），只靠录入校验是拦不住的。

    解析失败返回 ``False``：那是 httpx 的错误路径（``AI_UPSTREAM_ERROR``），把它
    当成「私网被拦」只会让「DNS 抖动」看起来像「有人在做 SSRF」。
    """
    key = (host or "").strip().strip("[]").lower()
    if not key:
        return False
    try:
        literal = ipaddress.ip_address(key)
    except ValueError:
        literal = None
    if literal is not None:
        # IP 字面量不需要 DNS：``http://2130706433`` 这类整数写法会走下面的解析。
        return _is_blocked(literal)

    now = time.monotonic()
    cached = _ssrf_cache.get(key)
    if cached is not None and cached[0] > now:
        return cached[1]

    addresses = await anyio.to_thread.run_sync(_lookup_addresses, key)
    if addresses is None:
        return False
    blocked = any(_is_blocked(address) for address in addresses)
    if len(_ssrf_cache) >= _SSRF_CACHE_MAX:
        # 上界只是防「随机子域名轰炸」把 dict 撑爆；清空比 LRU 便宜。
        _ssrf_cache.clear()
    _ssrf_cache[key] = (now + _SSRF_CACHE_TTL, blocked)
    return blocked


def clear_ssrf_cache() -> None:
    """测试与「改了 DNS/网络策略」后的手动失效入口。"""
    _ssrf_cache.clear()


__all__ = [
    "SHARED_OWNER",
    "SCOPE_PERSONAL",
    "SCOPE_SHARED",
    "SCOPE_SERVER",
    "CredentialError",
    "ModelAmbiguous",
    "ResolvedModel",
    "catalog",
    "catalog_models",
    "clear_ssrf_cache",
    "create_entry",
    "delete_entry",
    "host_is_private",
    "host_of_url",
    "invalidate",
    "list_entries",
    "public_view",
    "qualified_model_id",
    "resolve_api_key",
    "resolve_model",
    "route_candidates",
    "update_entry",
    "upstream_model_name",
]
