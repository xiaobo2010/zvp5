"""AI 配额：月份口径 + 读写封装。

``consume`` 的原子性由仓储保证（``AI_QUOTA`` 单语句自增），本层只补默认额度与
月份字符串。月份用**本地时区**：用户看到的是「本月额度」，服务器时区就是运营口径。
"""

from __future__ import annotations

from datetime import datetime

from db import backends
from db.repos.protocols import SHARED_OWNER_OID

from .config import (
    DEFAULT_QUOTA,
    ROLE_QUOTA_PRIORITY,
    default_site_ocr_limit,
    role_quota,
    site_quota,
)
from .ocr_engines import ENGINE_KINDS

KINDS = ("ocr", "chat")


class QuotaError(Exception):
    """额度参数错误；``code`` 直接回给客户端（路由层翻成 HTTP）。"""

    def __init__(self, code: str, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def check_kind(kind: object) -> str:
    """能力名是封闭集合（与 ``ck_ai_quota_kind`` 一致），别把拼错的 key 写进库。

    除 ``ocr`` / ``chat`` 两个大类外，**还放行站点级 ``ocr_<engine>``**：管理端要能
    给单条引擎设站点月额度（``PUT /api/ai/quota/{oid}`` → ``set_user_limit``），而
    ``site_consume`` 内部本来就拿这些 kind 记账。不放行的话，管理员只能靠
    ``settings.AI_SITE_QUOTA`` 重启生效，管理接口等于对站点额度失效。
    """
    if not isinstance(kind, str) or (kind not in KINDS and kind not in SITE_KINDS):
        allowed = ", ".join(KINDS + SITE_KINDS)
        raise QuotaError("AI_QUOTA_BAD_KIND", f"kind must be one of: {allowed}")
    return kind


def check_limit(limit_count: object) -> int:
    """上限必须是非负整数：负数会让所有人立刻超额，也违反表上的 CHECK。"""
    if (
        isinstance(limit_count, bool)
        or not isinstance(limit_count, int)
        or limit_count < 0
    ):
        raise QuotaError("AI_QUOTA_BAD_LIMIT", "limit_count must be a non-negative integer")
    return limit_count


def period_now(now: datetime | None = None) -> str:
    moment = now or datetime.now()
    return moment.strftime("%Y%m")


def _default_limit(kind: str) -> int:
    """默认月额度。

    站点级 ``ocr_*`` 走**站点口径**（``settings.AI_SITE_QUOTA`` → 兜底
    ``AI_DEFAULT_SITE_OCR_MONTHLY_UNITS``），**不**回落到用户的
    ``DEFAULT_QUOTA["ocr"]``：后者是「一个用户每月免费几次」，前者是「平台愿意
    为全站垫多少上游调用」，两者差一个数量级。回落会让几十个用户一天打满站点
    额度，之后全站 OCR 一起 503（2026-10-24 上线实测 limit=100 就是这个坑）。
    """
    if kind in SITE_KINDS:
        override = site_quota().get(kind)
        return override if override is not None else default_site_ocr_limit()
    base = kind.split("_", 1)[0] if kind not in DEFAULT_QUOTA else kind
    try:
        return int(DEFAULT_QUOTA.get(kind, DEFAULT_QUOTA.get(base, 0)))
    except (TypeError, ValueError):
        return 0


def default_limit_for(roles: list[str] | None, kind: str) -> int:
    """按角色取默认月额度；未列出的角色回退 ``AI_DEFAULT_*``。

    ``roles`` 是 ``get_current_user`` 的 ``per``（原始角色）。按
    :data:`ROLE_QUOTA_PRIORITY` 的顺序取**第一个命中**的角色：一个人同时有
    ``admin`` 与 ``department`` 时按管理员算，不叠加、不取最大值——叠加会让
    「角色组合」变成一条没人能预测的额度曲线。

    **只影响新建行/新月份**：``limit_count`` 是写进库的事实，行一旦存在就按库
    里的值走（管理端 ``PUT /api/ai/quota/{oid}`` 可改）。所以这里不做「每次调用
    重算上限」——那会让管理员手调的值被下一次调用悄悄覆盖。

    ``ocr_*`` 站点级 kind 与 ``_default_limit`` 一样回落到大类（站点额度没有角色
    维度，它是平台出资的）。
    """
    base = kind.split("_", 1)[0] if kind not in DEFAULT_QUOTA else kind
    role_set = {item for item in (roles or []) if isinstance(item, str)}
    table = role_quota()
    for role in ROLE_QUOTA_PRIORITY:
        if role not in role_set:
            continue
        value = (table.get(role) or {}).get(base)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return value
    return _default_limit(kind)


def _row_values(
    row: dict | None, kind: str, roles: list[str] | None = None
) -> tuple[int, int]:
    """仓储行的字段名容错：``used``/``used_count`` 二选一，缺列按默认值。"""
    default_limit = default_limit_for(roles, kind)
    if not row:
        return 0, default_limit
    used = row.get("used")
    if used is None:
        used = row.get("used_count")
    limit = row.get("limit")
    if limit is None:
        limit = row.get("limit_count")
    try:
        used_value = int(used or 0)
    except (TypeError, ValueError):
        used_value = 0
    try:
        limit_value = int(limit if limit is not None else default_limit)
    except (TypeError, ValueError):
        limit_value = default_limit
    return used_value, limit_value


async def peek(
    user_oid: str,
    kind: str,
    amount: int = 1,
    *,
    roles: list[str] | None = None,
) -> tuple[bool, int, int]:
    """只读预检：先判断再花钱。

    ``chat`` 的一次调用成本在供应商侧，先把明显已超额的请求挡掉，避免上游费用花在
    注定被拒的用户身上；真正的扣减仍由 :func:`consume` 原子完成。

    ``roles`` 只在「行还不存在」时影响展示的上限；行存在时读库里的 ``limit_count``。
    """
    row = await backends.get_ai_quota_repo().get(user_oid, period_now(), kind)
    used, limit = _row_values(row, kind, roles)
    return used + int(amount) <= limit, used, limit


async def consume(
    user_oid: str,
    kind: str,
    amount: int,
    *,
    roles: list[str] | None = None,
) -> tuple[bool, int, int]:
    allowed, used, limit = await backends.get_ai_quota_repo().consume(
        user_oid,
        period_now(),
        kind,
        int(amount),
        default_limit_for(roles, kind),
    )
    return bool(allowed), int(used), int(limit)


async def snapshot(user_oid: str, *, roles: list[str] | None = None) -> dict:
    result: dict = {}
    period = period_now()
    for kind in ("ocr", "chat"):
        row = await backends.get_ai_quota_repo().get(user_oid, period, kind)
        used, limit = _row_values(row, kind, roles)
        result[kind] = {"period": period, "used": used, "limit": limit}
    return result


def _entry(period: str, kind: str, used: int, limit: int) -> dict:
    """单能力视图：管理端每次只动一个 kind，回这一个就够，不必带另一种能力。"""
    return {"period": period, "kind": kind, "used": used, "limit": limit}


async def admin_snapshot(user_oid: str) -> dict:
    """admin 看别人：与自助视图同形（两种能力各一条），今天没有第二种口径。"""
    return await snapshot(user_oid)


async def set_user_limit(user_oid: str, kind: object, limit_count: object) -> dict:
    """设置某能力的月上限；已用额度不受影响（调上限 ≠ 重置用量）。"""
    target_kind = check_kind(kind)
    target_limit = check_limit(limit_count)
    period = period_now()
    repo = backends.get_ai_quota_repo()
    await repo.set_limit(user_oid, period, target_kind, target_limit)
    row = await repo.get(user_oid, period, target_kind)
    used, limit = _row_values(row, target_kind)
    return _entry(period, target_kind, used, limit)


async def reset_used(user_oid: str, kind: object) -> dict:
    """把某能力当月已用量清零，保留上限。

    行不存在时仓储返回 0（幂等）：用户本月还没用过，重置本来就已是目标状态，
    回默认上限下的空快照即可，不必为此建行。
    """
    target_kind = check_kind(kind)
    period = period_now()
    repo = backends.get_ai_quota_repo()
    await repo.reset_used(user_oid, period, target_kind)
    row = await repo.get(user_oid, period, target_kind)
    used, limit = _row_values(row, target_kind)
    return _entry(period, target_kind, used, limit)



# ---------------------------------------------------------------------------
# 站点级（平台出资）OCR 额度
# ---------------------------------------------------------------------------

# ``ocr_`` 是 V026 放宽后的 kind 前缀：``ck_ai_quota_kind`` 允许 ``ocr`` /
# ``chat`` / ``ocr_*``。站点级额度按**引擎**分开计，走的是 ``ocr_<engine_kind>``
# 这一族，与用户自己的 ``ocr`` 额度互不挤占。
SITE_KIND_PREFIX = "ocr_"

# 白名单：只有注册表里的引擎能成为站点级 kind。收成常量而不是现算，是为了让
# 「有人加了个新引擎」与「额度口径跟着变」这两件事在 review 时看得见。
SITE_KINDS: tuple[str, ...] = tuple(f"{SITE_KIND_PREFIX}{kind}" for kind in ENGINE_KINDS)


def site_kind(engine_kind: object) -> str:
    """引擎 kind → 站点级额度 kind（``tencent`` → ``ocr_tencent``）。

    非白名单一律拒：拼错的 kind 会让额度记到一个永远不会被查询的行上，等于
    「没扣」，而且不会有任何报错。
    """
    if not isinstance(engine_kind, str) or engine_kind not in ENGINE_KINDS:
        raise QuotaError(
            "AI_QUOTA_BAD_KIND",
            f"engine_kind must be one of: {', '.join(ENGINE_KINDS)}",
        )
    return f"{SITE_KIND_PREFIX}{engine_kind}"


async def site_consume(
    engine_kind: str, amount: int = 1
) -> tuple[bool, int, int]:
    """扣一次站点级 OCR 额度，返回 ``(allowed, used, limit)``。

    归属是 :data:`SHARED_OWNER_OID`（24 个 0 的哨兵）：站点额度不属于任何用户，
    但也不能用 NULL（唯一索引对 NULL 不生效，会退化成多行）。真正的原子性仍在
    仓储的单语句条件 UPDATE 里，这里只是把「谁的额度」和「哪种 kind」定下来。
    """
    return await consume(SHARED_OWNER_OID, site_kind(engine_kind), int(amount))


__all__ = [
    "KINDS",
    "SITE_KIND_PREFIX",
    "SITE_KINDS",
    "QuotaError",
    "check_kind",
    "check_limit",
    "default_limit_for",
    "period_now",
    "peek",
    "consume",
    "snapshot",
    "admin_snapshot",
    "set_user_limit",
    "reset_used",
    "site_kind",
    "site_consume",
]
