"""OpenAI 兼容面的独立 API key：签发、校验、吊销。

为什么要单独一层凭据：``/v1/*`` 原来只认用户的 Bearer JWT，第三方接入要么被迫
保存一个人的登录态（权限、会话、封禁全都跟着走），要么根本接不进来。API key 把
「一个人的平台登录态」和「一个自动化客户端的凭据」分开：

* **只在 ``/v1/*`` 生效**：管理面（``/keys``、``/quota``、``/logs`` ...）仍然
  只认 JWT。key 泄漏的爆炸半径就是「以本人身份对话、花本人的月额度」，不是
  「平台万能凭证」。
* **额度按 ``user_oid`` 计**：同一用户的多把 key 共享月额度（钱是同一个人的），
  但每把 key 另有 60 次/分钟的频率上限（见 ``routers.ai_router``），一把失控的
  客户端不会把同一个人其它 key 一起拖进 429。
* **明文只出现一次**：``token`` 只在创建响应里回，库里存 ``sha256`` hex。丢了
  只能吊销重建——没有「查看 key」这个接口。

前缀 ``zvk_``：与平台的 ``zvp_``（JWT 登录 token）分开，鉴权层才能在不解析
JWT 的前提下认出「这是一把 API key」，也让人一眼看出手里拿的是哪种凭据。
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import time
from datetime import datetime

from db import backends, state
from util.ban import ban_is_active, raise_banned
from util.group import get_user_permissions

from . import config
from .credentials import _as_datetime, _parse_datetime  # noqa: PLC2701 - 同一解析口径

_logger = logging.getLogger(__name__)

TOKEN_PREFIX = "zvk_"
# 32 字节随机 → 43 字符的 urlsafe base64；``secrets.token_urlsafe`` 是 CSPRNG。
TOKEN_BYTES = 32
# 每用户最多几把：够「本地 / CI / 临时脚本」各一把，又不至于让一个人刷出一屏。
MAX_KEYS_PER_USER = 10
# ``key_hint`` 的可见位数：前 8 + 后 4。前 8 里含 ``zvk_`` 前缀，认得出类型。
_HINT_HEAD = 8
_HINT_TAIL = 4
# ``ai_api_keys.name`` 是 VARCHAR2(64) NOT NULL。
_NAME_MAX = 64
# ``last_used_at`` 的采样间隔：每个请求都 UPDATE 一次会把这张表变成写热点。
TOUCH_INTERVAL_SECONDS = 60.0

# 校验缓存：token_hash → (过期时刻, 行)。配了 Redis 就写 ``ai:key:<digest>``
# （TTL 由 ``config.api_key_cache_ttl()`` 决定），命中就不必每个请求读库；CRUD
# 主动失效。**无 Redis / Redis 故障时**回退到下面这份进程内 dict（原实现，
# 语义不变）：多 worker 下最坏是「吊销后另一个进程最多晚 TTL 才生效」。
_CACHE_PREFIX = "ai:key:"
_TOUCH_PREFIX = "ai:key:used:"
_cache: dict[str, tuple[float, dict]] = {}
_touched: dict[str, float] = {}


class ApiKeyError(Exception):
    """API key 相关的可预期错误，由路由层翻译成 HTTP 状态码。"""

    def __init__(self, code: str, message: str, status_code: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


# ---------------------------------------------------------------------------
# 对外视图
# ---------------------------------------------------------------------------


def public_view(row: dict) -> dict:
    """对外视图：永远不含 ``token_hash``（连掩码形式都不给）。"""
    return {
        "oid": row.get("_id"),
        "name": row.get("name"),
        "key_hint": row.get("key_hint"),
        "enabled": bool(row.get("enabled", True)),
        "expires_at": row.get("expires_at"),
        "last_used_at": row.get("last_used_at"),
        "created_at": row.get("created_at"),
    }


def is_api_key(token: object) -> bool:
    """``zvk_`` 前缀 = 这是 API key，不是 JWT。鉴权分发只看这一条。"""
    return isinstance(token, str) and token.startswith(TOKEN_PREFIX)


async def invalidate(oid: str | None = None) -> None:
    """清空校验缓存与 ``last_used_at`` 的采样计时。

    ``oid`` 保留在签名里是为了调用方语义清晰；实现上总是全清：缓存以 token_hash
    为键，按 oid 反查要多一次「hash → oid」的映射，而 CRUD 是低频操作。Redis
    里的同名 key 与进程内 dict 一起清：否则无 Redis 时能立刻生效、有 Redis 时
    却要等 TTL，两种部署的吊销语义就分叉了。
    """
    keys = await state.kv_scan(_CACHE_PREFIX)
    if keys:
        await state.kv_delete(*keys)
    _cache.clear()
    _touched.clear()


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


async def create(user_oid: str, name: object, expires_at: object = None) -> dict:
    """签发一把 key，返回视图 + **明文 token（仅此一次）**。"""
    clean_name = _clean_name(name)
    clean_expiry = _clean_expiry(expires_at)
    repo = backends.get_ai_api_keys_repo()

    if await repo.count_for(user_oid) >= MAX_KEYS_PER_USER:
        raise ApiKeyError(
            "AI_API_KEY_LIMIT",
            f"at most {MAX_KEYS_PER_USER} api keys per user",
            409,
        )

    token = TOKEN_PREFIX + secrets.token_urlsafe(TOKEN_BYTES)
    try:
        oid = await repo.insert(
            user_oid=user_oid,
            name=clean_name,
            token_hash=hash_token(token),
            key_hint=key_hint(token),
            enabled=True,
            expires_at=clean_expiry,
        )
    except Exception as exc:  # noqa: BLE001 - 只翻译撞键，其它照抛
        if _is_duplicate(exc):
            raise ApiKeyError(
                "AI_API_KEY_DUPLICATE", "an api key with this name already exists", 409
            ) from None
        raise

    await invalidate()
    row = await repo.get_by_oid(oid)
    view = public_view(row or {"_id": oid, "name": clean_name, "expires_at": clean_expiry})
    view["token"] = token
    return view


async def list_for(user_oid: str) -> list[dict]:
    rows = await backends.get_ai_api_keys_repo().list_for(user_oid)
    return [public_view(row) for row in rows]


async def update(oid: str, actor: dict, fields: dict) -> dict:
    """部分更新：``name`` / ``enabled`` / ``expires_at``。

    ``expires_at`` 三态与 ``/keys`` 一致：键不存在 = 不改，传值 = 设置，显式
    ``null`` = 清除到期。判定按「键是否在 ``fields`` 里」而不是按值，所以调用方
    必须用 ``model_dump(exclude_unset=True)``。
    """
    repo = backends.get_ai_api_keys_repo()
    row = await repo.get_by_oid(oid)
    if row is None:
        raise ApiKeyError("AI_API_KEY_NOT_FOUND", "api key not found", 404)
    _assert_can_mutate(row, actor)

    changes: dict = {}
    if fields.get("name") is not None:
        changes["name"] = _clean_name(fields["name"])
    if fields.get("enabled") is not None:
        changes["enabled"] = bool(fields["enabled"])
    if "expires_at" in fields:
        changes["expires_at"] = (
            None if fields["expires_at"] is None else _clean_expiry(fields["expires_at"])
        )

    try:
        if changes:
            await repo.update(oid, **changes)
    except Exception as exc:  # noqa: BLE001 - 只翻译撞键
        if _is_duplicate(exc):
            raise ApiKeyError(
                "AI_API_KEY_DUPLICATE", "an api key with this name already exists", 409
            ) from None
        raise

    await invalidate(oid)
    fresh = await repo.get_by_oid(oid)
    return public_view(fresh or row)


async def delete(oid: str, actor: dict) -> None:
    repo = backends.get_ai_api_keys_repo()
    row = await repo.get_by_oid(oid)
    if row is None:
        raise ApiKeyError("AI_API_KEY_NOT_FOUND", "api key not found", 404)
    _assert_can_mutate(row, actor)

    if not await repo.delete(oid):
        raise ApiKeyError("AI_API_KEY_NOT_FOUND", "api key not found", 404)
    await invalidate(oid)


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------


async def authenticate(token: object) -> dict:
    """把 API key 换成与 ``get_current_user`` 同形的 user dict。

    判定顺序（每条失败都是 401，**不区分**原因）：token 形状 → 查 hash
    （缓存 30s）→ 存在 → ``enabled`` → ``expires_at`` → 用户存在 → 封禁。
    封禁单独走 403 ``USER_BANNED``（复用 ``util.ban.raise_banned``）：封禁不是
    「凭据无效」，客户端需要看到明确的拒绝原因，而不是被误导去重新申请 key。

    失败信息统一是 ``AI_API_KEY_INVALID``：区分「不存在/过期/禁用」等于给攻击者
    一个探测存在性的接口。
    """
    if not is_api_key(token):
        raise _invalid()
    raw = str(token).strip()
    if len(raw) <= len(TOKEN_PREFIX):
        raise _invalid()

    digest = hash_token(raw)
    row = await _cached_row(digest)
    if row is None:
        raise _invalid()
    if not row.get("enabled", True):
        raise _invalid()
    if _is_expired(row.get("expires_at")):
        raise _invalid()

    user = await _user_for(row.get("user_oid"))
    if user is None:
        # 账号已删：key 行还在（没有外键），但它已经代表不了任何人。
        raise _invalid()
    if ban_is_active(user):
        raise_banned(user)

    permissions = await get_user_permissions(user)
    await _touch(row)
    return {
        "id": str(row.get("user_oid")),
        "perm": _upgrade_positions(permissions),
        "per": permissions,
        "class_id": user.get("class_id"),
        # 附加字段（不属于 JWT 的 user dict）：路由层要用它在频控上多一条
        # 「每把 key」的维度，并在审计里区分是哪个客户端。它不代表任何权限。
        "api_key_oid": str(row.get("_id")),
        "api_key_name": row.get("name"),
    }


def hash_token(token: str) -> str:
    """``sha256(token)`` 的 hex：库里只有这一种形态。"""
    return hashlib.sha256(str(token).encode("utf-8")).hexdigest()


def key_hint(token: str) -> str:
    """前 8 + 后 4；token 短到两段重叠时按实际长度截（不拼出重复字符）。

    ``zvk_`` + 43 字符是常态（51 字符），hint 永远够切；这里仍处理短值，是为了
    让「有人拿一个畸形 token 来创建」也不会写出一条超长或空 hint。
    """
    text = str(token or "")
    if len(text) <= _HINT_HEAD + _HINT_TAIL:
        return text
    return f"{text[:_HINT_HEAD]}{text[-_HINT_TAIL:]}"


# ---------------------------------------------------------------------------
# 内部
# ---------------------------------------------------------------------------


async def _cached_row(digest: str) -> dict | None:
    """按 hash 取行；命中缓存就不读库。``None`` 不缓存（防探测刷缓存）。

    先问 Redis（多 worker / 多机共享同一份校验结果），再退回进程内 dict；写回
    时 Redis 写成功就不再写本地——两处都写只会让「到底哪份是真相」变成两个答案。
    """
    key = f"{_CACHE_PREFIX}{digest}"
    cached = await state.kv_get(key)
    if isinstance(cached, dict):
        return dict(cached)

    now = time.monotonic()
    local = _cache.get(digest)
    if local is not None and local[0] > now:
        return dict(local[1])

    row = await backends.get_ai_api_keys_repo().get_by_hash(digest)
    if row is None:
        return None
    ttl = config.api_key_cache_ttl()
    if not await state.kv_set(key, dict(row), ttl):
        _cache[digest] = (now + ttl, dict(row))
    return row


async def _touch(row: dict) -> None:
    """采样写 ``last_used_at``：每把 key 最多每 60 秒一次 UPDATE。

    采样计时放 Redis（``SET NX PX``）做分布式去重：多 worker 下每个进程各自计时
    会让 UPDATE 次数按进程数放大。``None`` = Redis 不可用，退回进程内 dict。
    """
    oid = str(row.get("_id") or "")
    if not oid:
        return
    sampled = await state.kv_set_nx(f"{_TOUCH_PREFIX}{oid}", 1, TOUCH_INTERVAL_SECONDS)
    if sampled is None:
        now = time.monotonic()
        if now - _touched.get(oid, 0.0) < TOUCH_INTERVAL_SECONDS:
            return
        _touched[oid] = now
    elif not sampled:
        return
    try:
        await backends.get_ai_api_keys_repo().touch(oid)
    except Exception as exc:  # noqa: BLE001 - 统计字段写失败不影响鉴权
        _logger.warning("ai api key touch failed: %s", type(exc).__name__)


async def _user_for(oid: object) -> dict | None:
    """认证用的用户读：与 ``util.object_id._read_auth_user`` 同一口径。"""
    if not isinstance(oid, str) or not oid:
        return None
    repo = backends.get_users_repo()
    reader = getattr(repo, "get_for_auth", None)
    if reader is None:
        return await repo.get_by_oid(oid)
    return await reader(oid)


def _upgrade_positions(permissions: list[str]) -> list[str]:
    """``per`` → ``perm``：与 JWT 路径同一套 V1→V2 权限映射。

    延迟导入：``util.object_id`` 会拉 FastAPI 的鉴权依赖，本模块在服务层，
    不该为了一个纯函数把那条导入链带进来（也让测试能单独导入本模块）。
    """
    from util.object_id import upgrade_user_positions

    return upgrade_user_positions(permissions)


def _is_expired(value: object, now: datetime | None = None) -> bool:
    """``expires_at`` 非空且 ``<= now`` 即过期（``None`` = 永不过期）。"""
    expiry = _as_datetime(value)
    if expiry is None:
        return False
    return expiry <= (now or datetime.now())


def _clean_expiry(value: object) -> datetime | None:
    """``expires_at`` 入库校验：``None`` = 永不过期，其余必须是**未来**的 ISO 8601。

    与 ``/keys`` 同一套解析口径（带时区的先换算到本地再抹 tzinfo），但错误码是
    ``AI_API_KEY_BAD_EXPIRY``：客户端的处理方式不同（key 面板 vs 凭据面板）。
    过去的时刻直接拒而不是静默存成「立刻过期」——后者只会让用户对着一条查不出
    原因的 401。
    """
    if value is None:
        return None
    if not isinstance(value, str):
        raise ApiKeyError(
            "AI_API_KEY_BAD_EXPIRY", "expires_at must be an ISO 8601 string", 400
        )
    parsed = _parse_datetime(value)
    if parsed is None:
        raise ApiKeyError(
            "AI_API_KEY_BAD_EXPIRY", "expires_at must be an ISO 8601 string", 400
        )
    if parsed <= datetime.now():
        raise ApiKeyError(
            "AI_API_KEY_BAD_EXPIRY", "expires_at must be in the future", 400
        )
    return parsed


def _invalid() -> ApiKeyError:
    return ApiKeyError("AI_API_KEY_INVALID", "invalid api key", 401)


def _assert_can_mutate(row: dict, actor: dict) -> None:
    """owner 或 admin：key 是「我自己的客户端凭据」，本人必须能吊销它。"""
    is_admin = "admin" in (actor.get("per") or [])
    if is_admin or str(row.get("user_oid")) == str(actor.get("id")):
        return
    raise ApiKeyError("AI_FORBIDDEN", "admin or owner required", 403)


def _clean_name(value: object) -> str:
    """名字是给人看的标签：允许中文/空格，但不许控制字符与空串。"""
    name = str(value or "").strip()
    if not name or len(name) > _NAME_MAX:
        raise ApiKeyError(
            "AI_API_KEY_BAD_NAME", f"name length must be 1..{_NAME_MAX}", 400
        )
    if any(ch < " " or ch == "\x7f" for ch in name):
        raise ApiKeyError(
            "AI_API_KEY_BAD_NAME", "name must not contain control characters", 400
        )
    return name


def _is_duplicate(exc: BaseException) -> bool:
    """仓储撞唯一键时抛 ``DuplicateApiKey``（按类名判定，避免依赖具体后端）。"""
    return type(exc).__name__ == "DuplicateApiKey"


__all__ = [
    "MAX_KEYS_PER_USER",
    "TOKEN_PREFIX",
    "ApiKeyError",
    "authenticate",
    "create",
    "delete",
    "hash_token",
    "invalidate",
    "is_api_key",
    "key_hint",
    "list_for",
    "public_view",
    "update",
]
