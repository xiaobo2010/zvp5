"""模型倍率（Agent Fuel）解析：``AI_MODELS.RATE`` → 进程内 TTL 缓存。

为什么要有这一层：计费是**同步**决策（``usage.af_units(total, rate)``），而倍率
存在库里（``AI_MODELS``，管理员可编辑）。所以这里做一份「进程内、60 秒有效」的
只读快照：

* 读路径 ``rate_for(model)`` 是纯同步查表，绝不 ``await``——扣费点不必为它改
  形状；
* 刷新走 ``await ensure_loaded()`` / ``await resolve_rate(model)``：只在缓存过期
  （或首次使用）时读一次库；
* 管理端改完倍率调用 ``invalidate()`` 立刻失效，下一个请求就按新倍率计费。

解析顺序（大小写不敏感，空名直接 1.0）：

1. 完整 ID（``provider/model``）精确命中——同一模型在不同渠道可以不同价；
2. 裸模型名（``model``）精确命中——``AI_MODELS`` 的主键就是裸名；
3. 都没有 → 默认 1.0（基准价）。

读库失败**不抛**：计费不能因为一次目录查询失败把已经生成的答案丢掉。失败时
沿用上一次成功的快照（没有就是空表 → 全部按 1.0），并在一个 TTL 内不再重试，
避免每次请求都去撞一个挂掉的库。最坏后果是「60 秒内按基准价计费」，而不是
「请求 500」。

单 worker 前提与 :mod:`services.ai.credentials` 的目录缓存一致：多 worker 各自
重置各的，60 秒内可能短暂不一致——倍率是运营参数，不是正确性来源。
"""

from __future__ import annotations

import logging
import math
import time

from db import backends

_logger = logging.getLogger(__name__)

#: 倍率快照的有效期（秒）。与凭据缓存同一量级：改完倍率靠 ``invalidate()``
#: 立刻生效，TTL 只负责「别的 worker / 漏掉的失效」最终收敛。
RATE_TTL_SECONDS = 60.0

#: 没有覆盖行 / 没有倍率 / 读不出来时的默认值（1 = 基准价，1000 token = 1 AF）。
DEFAULT_RATE = 1.0

#: ``小写模型名`` → 倍率。键同时包含「完整 ID」与「裸名」，见 ``_load``。
_rates: dict[str, float] = {}
_expires_at: float = 0.0


def _norm(model: object) -> str:
    """模型名归一：去空白 + 小写（上游模型名大小写不敏感）。"""
    return str(model or "").strip().lower()


def _clean_rate(rate: object) -> float:
    """坏倍率一律回 1.0（与 ``usage.af_units`` 的兜底同一口径）。"""
    if isinstance(rate, bool) or not isinstance(rate, (int, float)):
        return DEFAULT_RATE
    value = float(rate)
    if not math.isfinite(value) or value <= 0:
        return DEFAULT_RATE
    return value


def invalidate() -> None:
    """清空快照：管理端 ``PUT/DELETE /api/ai/models`` 成功后调用。

    同步函数（不是协程）：它只清内存，不该让路由多等一个 ``await``。
    """
    global _expires_at
    _rates.clear()
    _expires_at = 0.0


def _build(rows: list[dict] | None) -> dict[str, float]:
    """仓储行 → ``{键: 倍率}``；同一模型写两个键（完整 ID + 裸名）。

    ``provider`` 是管理员填的渠道名（可空）；有它就顺带登记
    ``provider/model`` 这个完整 ID。裸名用 ``setdefault``：完整 ID 应优先命中，
    但两者本来同价（同一行），顺序只影响可读性。
    """
    table: dict[str, float] = {}
    for row in rows or []:
        name = _norm(row.get("model"))
        if not name:
            continue
        rate = _clean_rate(row.get("rate"))
        table.setdefault(name, rate)
        provider = _norm(row.get("provider"))
        if provider:
            table.setdefault(f"{provider}/{name}", rate)
    return table


async def ensure_loaded() -> None:
    """缓存过期（或从未加载）时刷新一次；任何异常都降级为「沿用旧快照」。"""
    global _rates, _expires_at

    now = time.monotonic()
    if _expires_at > now:
        return

    try:
        rows = await backends.get_ai_models_repo().list_all()
    except Exception as exc:  # noqa: BLE001 - 计费不能因为目录读失败而中断
        _logger.warning("ai model rates unavailable: %s", type(exc).__name__)
        # 失败也记一个完整 TTL：否则每个请求都会重试一次必然失败的查询。
        # 旧快照（如果有）继续生效，比退回默认值更接近真值。
        _expires_at = now + RATE_TTL_SECONDS
        return

    _rates = _build(rows)
    _expires_at = now + RATE_TTL_SECONDS


def rate_for(model: str | None) -> float:
    """同步查表：完整 ID → 裸名 → ``DEFAULT_RATE``。

    缓存未加载 / 已过期时返回默认倍率（不阻塞、不读库）——调用方应先用
    :func:`resolve_rate`（或 ``await ensure_loaded()``）把快照准备好。
    """
    name = _norm(model)
    if not name:
        return DEFAULT_RATE
    hit = _rates.get(name)
    if hit is not None:
        return hit
    _prefix, _sep, bare = name.partition("/")
    if bare:
        hit = _rates.get(bare)
        if hit is not None:
            return hit
    return DEFAULT_RATE


async def resolve_rate(model: str | None) -> float:
    """计费点用这个：先确保快照新鲜，再同步查表。"""
    await ensure_loaded()
    return rate_for(model)


__all__ = [
    "DEFAULT_RATE",
    "RATE_TTL_SECONDS",
    "ensure_loaded",
    "invalidate",
    "rate_for",
    "resolve_rate",
]
