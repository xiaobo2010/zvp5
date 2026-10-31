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
from typing import Any, Mapping
from urllib.parse import urlsplit

import anyio

import settings

from db import backends, state
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

# 上游 ``/models`` 探测的超时（秒）。``0`` / 负数 = 关闭（离线环境与单测用）：
# 探测是**加分项**，不能因为它把一次保存变慢或变失败。
PROBE_TIMEOUT_SETTING = "AI_MODEL_PROBE_TIMEOUT_SECONDS"
PROBE_TIMEOUT_DEFAULT = 2.0

# 上游报上下文窗口的字段名（各家不同，按顺序取第一个能解析成正整数的）。
CONTEXT_KEYS = (
    "context_length",
    "context_window",
    "max_context_tokens",
    "context_size",
    "max_input_tokens",
    "max_model_len",
)

# ``model id`` → 上下文窗口（token）。来源是「最近一次构建的目录」：视图与压缩
# 水位都要求一个**同步**的窗口分母，而目录是 async 的。刷新发生在 ``_build``，
# 也就是每次目录构建时——TTL 之内多个用户看到的是同一份事实。
_model_contexts: dict[str, int] = {}

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

# 目录缓存：配了 Redis 就写 ``ai:cred:<user_oid>``，任何 CRUD 直接清空，不做
# 按用户失效——共享项对所有人可见，按用户失效反而容易漏。Redis 不可用时回退到
# 下面这份进程内 dict（原实现，语义不变）。
_CRED_CACHE_PREFIX = "ai:cred:"
_cache: dict[str, tuple[float, list[dict]]] = {}

# 请求时 SSRF 复核的解析缓存：host → (到期 monotonic, 是否私网)。
# 没有它，每一次上游调用都要多一次 DNS（单 worker 下是纯浪费）；有它，同一个
# host 在 60 秒内只解析一次，也让「录入时是公网、请求时变内网」这种 rebinding
# 至少在一个 TTL 内保持判定稳定。同样的取舍搬到 Redis（``ai:ssrf:<host>``），
# 让多 worker / 多机共享同一份判定；Redis 不可用时仍是这份进程内 dict。
_SSRF_CACHE_TTL = 60.0
_SSRF_CACHE_MAX = 256
_SSRF_CACHE_PREFIX = "ai:ssrf:"
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


def model_entries(value: object) -> list[dict]:
    """``MODELS_JSON`` 的两种形态 → ``[{"id": str, "context": int | None}]``。

    兼容是**硬要求**：库里既有 ``["gpt-4o-mini"]`` 这种字符串数组，也有新写入的
    ``[{"id": "gpt-4o-mini", "context": 128000}]``。两种都要能读，否则一次列结构
    演进就会让全部老凭据的模型列表变成空。
    """
    if isinstance(value, str):
        # 老仓储可能直接把 JSON 文本交出来。
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return []
    if not isinstance(value, (list, tuple)):
        return []

    items: list[dict] = []
    seen: set[str] = set()
    for raw in value:
        if isinstance(raw, str):
            model, context = raw.strip(), None
        elif isinstance(raw, Mapping):
            model = str(raw.get("id") or "").strip()
            context = positive_int(raw.get("context"))
        else:
            continue
        if not model or model in seen:
            continue
        seen.add(model)
        items.append({"id": model, "context": context})
    return items


def model_ids(value: object) -> list[str]:
    """只要模型名（目录、下拉、别名规则的输入都是它）。"""
    return [item["id"] for item in model_entries(value)]


def model_contexts(value: object) -> dict[str, int | None]:
    """模型名 → 上下文窗口（没填就是 ``None``，不是 0）。"""
    return {item["id"]: item["context"] for item in model_entries(value)}


def positive_int(value: object) -> int | None:
    """正整数的宽容解析：``bool`` 是 ``int`` 的子类，必须先排除。

    公开是给 :func:`services.ai.providers.context_window` 用的：``/models`` 的
    字段解析与 ``MODELS_JSON`` 的读取必须用同一份口径（``0`` / 负数 / 布尔都
    不是合法窗口），否则同一个上游数字会在两条路径上得到不同结论。
    """
    if isinstance(value, bool) or value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _refresh_model_contexts(entries: list[dict]) -> None:
    """把这一次目录里的「模型级上下文」灌进进程内索引（供同步查询用）。"""
    global _model_contexts
    index: dict[str, int] = {}
    for entry in entries:
        for model in entry.get("models") or []:
            if not isinstance(model, str):
                continue
            value = (entry.get("model_contexts") or {}).get(model)
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                continue
            index[qualified_model_id(entry, model)] = value
            # 裸名只做 setdefault：同名多渠道时以目录顺序第一个为准（与别名规则同向）。
            index.setdefault(model, value)
    _model_contexts = index


def model_context(model: object) -> int | None:
    """该模型**目录里配的**上下文窗口（token）；没有就是 ``None``。

    合格 ID 先查，再退裸名：``provider/model`` 才是唯一键，裸名只是老数据的形状。
    反向也要能查（裸名 → 合格 ID）：同名多渠道且窗口不一致时返回 ``None``——
    猜一个窗口比不给更危险（压缩水位会按错的数字算）。索引为空（还没构建过目录）
    时同样返回 ``None``，调用方继续走配置表兜底。
    """
    name = str(model or "").strip()
    if not name:
        return None
    value = _model_contexts.get(name)
    if value is None:
        bare = name.partition("/")[2]
        if bare:
            value = _model_contexts.get(bare)
    if value is None and "/" not in name:
        hits = {
            context
            for qualified, context in _model_contexts.items()
            if qualified.partition("/")[2] == name
        }
        if len(hits) == 1:
            value = hits.pop()
    return value


async def catalog(user_oid: str) -> list[dict]:
    """该用户当前生效的 provider 列表，顺序 personal → shared → server。

    缓存里存的已经是「未过期」的条目，但 TTL 窗口内条目仍可能到期：所以读缓存
    之后还要按当前时刻再筛一遍（这是「缓存消费处」的过滤点）。不这么做，一把
    5 分钟前到期的 key 会一直出现在 ``/v1/models`` 里，直到缓存自然过期。

    缓存位置：配了 Redis 就写 ``ai:cred:<user_oid>``（跨 worker 共享）；Redis
    不可用/写失败时退回进程内 dict。读的时候顺序相反：先 Redis，再本地。
    """
    key = f"{_CRED_CACHE_PREFIX}{user_oid}"
    cached = await state.kv_get(key)
    if cached is None:
        now = time.monotonic()
        local = _cache.get(user_oid)
        if local is not None and local[0] > now:
            cached = local[1]
    if isinstance(cached, list):
        return _live_entries(cached)

    entries = await _build(user_oid)
    ttl = config.credentials_cache_ttl()
    if not await state.kv_set(key, entries, ttl):
        _cache[user_oid] = (time.monotonic() + ttl, entries)
    return _live_entries(entries)


async def invalidate(user_oid: str | None = None) -> None:
    """清空目录缓存。

    ``user_oid`` 保留在签名里是为了调用方语义清晰；实现上总是全清：共享项的
    变更影响所有人，按用户失效迟早漏掉一条路径。Redis 里的同前缀 key 与进程内
    dict 一起清（两种部署的失效语义必须一致）。
    """
    keys = await state.kv_scan(_CRED_CACHE_PREFIX)
    if keys:
        await state.kv_delete(*keys)
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
                "models": model_ids(row.get("models")),
                "model_contexts": model_contexts(row.get("models")),
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
                "models": model_ids(models),
                "model_contexts": {},
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

    _refresh_model_contexts(entries)
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


def probe_timeout() -> float:
    """上游 ``/models`` 探测的超时；``<= 0`` 表示关掉（离线/单测）。

    公开是因为 ``providers.list_models`` 也要用它：探测超时只该有一个配置入口
    （``AI_MODEL_PROBE_TIMEOUT_SECONDS``），两处各读一遍迟早漂。
    """
    raw = getattr(settings, PROBE_TIMEOUT_SETTING, PROBE_TIMEOUT_DEFAULT)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return PROBE_TIMEOUT_DEFAULT
    if value <= 0:
        return 0.0
    return value


def _models_payload(discovered: Mapping[str, int], models: object) -> list:
    """把探测到的窗口并回原有列表，**保持原顺序**；没有窗口的仍留字符串。

    已知 context 的条目只补空缺：管理员手填的值比上游报的更可信（有些兼容层会
    报成上游母模型的窗口，比实际可用的大）。字符串原样保留是为了让「这次保存到底
    改了什么」在库里的 diff 上一眼看得出。
    """
    items: list = []
    for item in model_entries(models):
        context = item["context"] or discovered.get(item["id"])
        items.append({"id": item["id"], "context": context} if context else item["id"])
    return items


async def _discover_model_contexts(
    *, base_url: object, api_key: str | None, headers: Mapping[str, Any] | None = None
) -> dict[str, int]:
    """best-effort 问一次上游 ``/models``，回 ``{model: context}``（可能为空）。

    实现委托 :func:`services.ai.providers.list_models`：请求超时、SSRF 复核、
    鉴权头与连接池都归调用层，这里只保留「探测失败 = 没探到」这条保存路径语义。

    为什么放在保存路径：模型窗口是上游事实，让管理员为每个模型手抄一遍数字既不
    可靠也不会有第二次。探测失败一律当作「没探到」——保存本身不能因此失败。
    """
    if probe_timeout() <= 0 or not api_key:
        return {}
    url = str(base_url or "").strip()
    if not url:
        return {}

    # 延迟导入：``providers`` 在模块级导入本模块（取 SCOPE_SERVER / context 解析），
    # 这里再顶层导入就成环了。
    from . import providers

    try:
        rows = await providers.list_models(
            {"name": "", "base_url": url, "headers": dict(headers or {})},
            api_key,
        )
    except Exception:  # noqa: BLE001 - 探测是加分项，任何失败都只是「没探到」
        return {}

    found: dict[str, int] = {}
    for row in rows:
        # 兼容非 dict 行与缺键：这里在保存路径上，任何一行怪形状都不该让保存失败。
        if not isinstance(row, Mapping):
            continue
        model = row.get("id")
        value = positive_int(row.get("context"))
        if isinstance(model, str) and model and value is not None:
            found.setdefault(model, value)
    return found


async def _autofill_model_contexts(
    repo: Any,
    oid: str,
    *,
    base_url: object,
    api_key: str | None,
    models: object,
    headers: Mapping[str, Any] | None = None,
) -> list:
    """探测 → 补 ``context`` → 若确有新增就写回库；返回最终要回显的 models 列表。"""
    items = model_entries(models)
    if not items or probe_timeout() <= 0:
        return items
    discovered = await _discover_model_contexts(
        base_url=base_url, api_key=api_key, headers=headers
    )
    if not discovered:
        return items
    filled = _models_payload(discovered, models)
    before = [item["context"] for item in items]
    after = [item["context"] for item in filled]
    if before == after:
        return items
    try:
        await repo.update(oid, models=filled)
    except Exception:  # noqa: BLE001 - 回写失败不该让保存失败（下次保存再试）
        return items
    return filled


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


async def _entry_secret(row: Mapping[str, Any]) -> str | None:
    """探测用明文 key：拿不到就 ``None``（探测跳过，保存不受影响）。"""
    if row.get("scope") == SCOPE_SERVER:
        cfg = config.providers().get(str(row.get("name") or "")) or {}
        return config.provider_api_key(cfg)
    token = row.get("api_key_cipher")
    if not token:
        return None
    try:
        return crypto.decrypt_secret(str(token))
    except ValueError:
        return None


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
        # 模型名与窗口分开给：下拉/多选只要名字，表单要的「每个模型的窗口」是
        # 另一张表，混在一个数组里会让所有既有消费方都要改。
        "models": model_ids(entry.get("models")),
        "model_contexts": model_contexts(entry.get("models")),
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


async def entry_for_name(name: str) -> dict | None:
    """按名字取**一条**凭据条目（admin 视角、含密文行），供需要真 key 的维护动作。

    遮蔽顺序与 :func:`catalog` 同向：**生效中的**个人项优先于生效中的共享项，
    同名多渠道时拿到的就是「目录里真正会生效的那一条」。禁用/过期的行仍然返回
    （排在生效行之后），只是不再抢遮蔽位——管理员要修一条停用的渠道时，仍然得
    先看到它自己的 key。服务端作用域不在本表里，返回 ``None``——调用方据此区分
    「没有这个渠道」（404）与「运维配置不可回写」（400）。

    返回的是内部形状（含 ``api_key_cipher``），**不能**直接回给客户端；它只给
    「从上游拉取模型元数据」这条需要 ``resolve_api_key`` + 真实请求的路径用。
    """
    clean = str(name or "").strip()
    if not clean:
        return None
    rows = await backends.get_ai_credentials_repo().list_all()
    ordered = sorted(
        rows or [],
        key=lambda row: (
            0
            if bool(row.get("enabled", True))
            and not _is_expired(row.get("expires_at"))
            else 1,
            0 if str(row.get("scope") or "") == SCOPE_PERSONAL else 1,
        ),
    )
    matches = [row for row in ordered if str(row.get("name") or "") == clean]
    if not matches:
        return None
    row = matches[0]
    return {
        "key": f"{row.get('scope')}:{row.get('_id') or clean}",
        "scope": str(row.get("scope") or ""),
        "owner_oid": row.get("owner_oid"),
        "name": clean,
        "kind": str(row.get("kind") or config.OPENAI_COMPAT),
        "base_url": row.get("base_url"),
        "models": model_ids(row.get("models")),
        "model_contexts": model_contexts(row.get("models")),
        "headers": _stored_headers(row.get("headers_json")),
        "api_path": _stored_api_path(row.get("api_path")),
        "api_key_cipher": row.get("api_key_cipher"),
        "api_key": None,
        "key_hint": row.get("key_hint"),
        "enabled": bool(row.get("enabled", True)),
        "oid": row.get("_id"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        "expires_at": row.get("expires_at"),
    }


async def create_entry(
    *,
    user_oid: str,
    scope: str,
    name: str,
    base_url: str,
    models: list[str | dict],
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
    final_models = await _autofill_model_contexts(
        repo,
        oid,
        base_url=clean_url,
        api_key=secret,
        models=clean_models,
        headers=clean_headers,
    )
    view = public_view(
        {
            "oid": oid,
            "scope": scope,
            "owner_oid": owner_oid,
            "name": clean_name,
            "kind": config.OPENAI_COMPAT,
            "base_url": clean_url,
            "models": final_models,
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
    # 只在「可能影响模型窗口」的字段变了才探测：改个名字不该打一次上游。
    if {"models", "base_url", "api_key_cipher"} & set(changes):
        final_models = await _autofill_model_contexts(
            repo,
            oid,
            base_url=fresh.get("base_url"),
            api_key=(
                _clean_api_key(fields["api_key"])
                if fields.get("api_key") is not None
                else await _entry_secret(fresh)
            ),
            models=fresh.get("models"),
            headers=_stored_headers(fresh.get("headers_json")),
        )
        fresh = dict(fresh, models=final_models)
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


def _clean_models(value: object) -> list[str | dict]:
    """``models`` 两种形态入库校验：``"model-id"`` 或 ``{"id", "context"}``。

    原形态原样落库（字符串还是字符串）：把老行悄悄改写成对象没有收益，反而让
    「这条 context 是管理员填的还是上游报的」变得不可判。``context`` 只收正整数
    或 ``null``——``0`` 与负数不是「未知」，是错的输入，直接 400 比留个假窗口好。

    路由层的 Pydantic schema 是 ``list[str | dict]``（对象形态必须过 HTTP 层，否则
    「按模型填上下文」会 422）；**形状校验留在这里**：``dict`` 只认 ``id`` /
    ``context`` 两个键，``id`` 必须是非空字符串。错误文案是中文——它直接进
    管理端的提示框。
    """
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        raise CredentialError("AI_KEY_INVALID", "models 必须是数组", 400)
    models: list[str | dict] = []
    seen: set[str] = set()
    for item in value:
        context: int | None = None
        if isinstance(item, str):
            model = item.strip()
            shaped: str | dict = model
        elif isinstance(item, Mapping):
            extra = set(item) - {"id", "context"}
            if extra:
                raise CredentialError(
                    "AI_KEY_INVALID",
                    "models 里的对象只支持 id 与 context 两个字段",
                    400,
                )
            raw_id = item.get("id")
            if not isinstance(raw_id, str):
                raise CredentialError(
                    "AI_KEY_INVALID", "models 里的对象必须有字符串 id", 400
                )
            model = raw_id.strip()
            raw_context = item.get("context")
            if raw_context is not None:
                context = positive_int(raw_context)
                if context is None:
                    raise CredentialError(
                        "AI_KEY_INVALID", "模型的 context 必须是正整数", 400
                    )
            shaped = {"id": model, "context": context}
        else:
            raise CredentialError(
                "AI_KEY_INVALID",
                "models 只能是字符串或 {id, context} 对象",
                400,
            )
        if not 1 <= len(model) <= _MODEL_MAX_LEN:
            raise CredentialError(
                "AI_KEY_INVALID",
                f"每个模型名长度必须在 1..{_MODEL_MAX_LEN} 之间",
                400,
            )
        if model in seen:  # 去重：同一个模型写两遍只会让候选列表变长
            continue
        seen.add(model)
        models.append(shaped)
    if len(models) > _MODELS_MAX:
        raise CredentialError(
            "AI_KEY_INVALID", f"每个凭据最多 {_MODELS_MAX} 个模型", 400
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
    cache_key = f"{_SSRF_CACHE_PREFIX}{key}"
    cached = await state.kv_get(cache_key)
    if isinstance(cached, bool):
        return cached
    local = _ssrf_cache.get(key)
    if local is not None and local[0] > now:
        return local[1]

    addresses = await anyio.to_thread.run_sync(_lookup_addresses, key)
    if addresses is None:
        return False
    blocked = any(_is_blocked(address) for address in addresses)
    if not await state.kv_set(cache_key, blocked, _SSRF_CACHE_TTL):
        if len(_ssrf_cache) >= _SSRF_CACHE_MAX:
            # 上界只是防「随机子域名轰炸」把 dict 撑爆；清空比 LRU 便宜。
            _ssrf_cache.clear()
        _ssrf_cache[key] = (now + _SSRF_CACHE_TTL, blocked)
    return blocked


async def clear_ssrf_cache() -> None:
    """测试与「改了 DNS/网络策略」后的手动失效入口（Redis + 进程内 dict）。"""
    keys = await state.kv_scan(_SSRF_CACHE_PREFIX)
    if keys:
        await state.kv_delete(*keys)
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
