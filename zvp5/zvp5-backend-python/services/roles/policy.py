"""角色策略层：``ROLE_FEATURES`` / ``ROLE_QUOTAS`` 的唯一读取入口。

为什么要这一层：「角色能做什么」目前散在约 15 处 ``_require_*`` guard 与内联
``position.includes`` 里，管理员改不了，改一个角色要动代码 + 重启。这里把
「角色 → 功能开关 + 月额度」收拢成一张**注册表 + 稀疏覆盖层**：

* :data:`FEATURE_KEYS` 是封闭集合，顺序即 UI 顺序；
* :data:`DEFAULTS` 是「库里没有任何覆盖」时的行为，等价改造前的线上行为；
* 库里每多一行就多一个格子的覆盖，删掉那行就回落默认。

三个刻意的设计决定：

1. **同步读取 + 进程内快照**。guard 散落在同步的 FastAPI 依赖、同步工具函数里
   （``util/attachment.py``、``services/ai/quota.py`` 的同步路径），把它们改成
   协程会连锁改一大片调用方。所以快照由 :func:`refresh`（启动挂到调度器，每
   5 秒一次）刷新，读取点用 :func:`feature_enabled` / :func:`monthly_limit`
   直接读内存；需要「一定是最新」的接口路径用 :func:`ensure_fresh` 先刷一次。
2. **admin 是代码里的短路，不是数据行**。管理员永远全开且无限额
   （:func:`monthly_limit` 返回 ``None`` = 无限）。写成数据行只会让人以为
   「取消勾选就能关掉管理员」。
3. **多角色取并集，额度取优先级第一个命中**。功能开关是「任一角色给了就用」；
   额度是数字，并集没有意义（取最大会让「角色组合」变成一条没人能预测的
   额度曲线），沿用 :data:`services.ai.config.ROLE_QUOTA_PRIORITY` 的「第一个
   命中」语义。

读库失败时**回落代码默认值并打日志**，绝不把异常抛给业务：策略表是运维便利，
一次 ADB 抖动不该让所有端点 500。
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any, Iterable

from db import cache

logger = logging.getLogger(__name__)

# 快照有效期（秒）。与 ``services.settings_runtime.REFRESH_SECONDS`` 同口径：
# 用 ``time.monotonic`` 计时，系统时间跳变不会拉长或立刻作废缓存。
REFRESH_SECONDS = 5.0

#: 版本闸生效时最多多久不回源数据库（秒）。版本号只能证明「没人通过 API 改过」，
#: 挡不住直接改库；5 分钟的强制回源是最坏情况下的兜底。
FULL_RELOAD_SECONDS = 300.0

#: Redis 里本域的版本键（写策略时由 ``cache.flush_scopes(["roles"])`` 自增）。
VERSION_SCOPE = "roles"

_ADMIN_ROLE = "admin"

#: 功能开关的封闭集合。**顺序即 UI 顺序**（管理页照这个顺序渲染勾选表）。
FEATURE_KEYS: tuple[str, ...] = (
    "ai.chat",
    "ai.ocr",
    "ai.agent",
    "ai.schedule",
    "ai.keys",
    "activity.stats",
    "attachment.manage",
    "export.time",
    "import.activity",
    "logs.read",
    "notify.publish",
    "ban.read",
    "quota.manage",
)

# 说明：这里**不给**每个 key 配中文名。展示文案属于前端（``manage.ts`` 的
# ``rolePolicies`` 段），后端再抄一份只会让两处文案各自漂移；``FEATURE_KEYS``
# 的注释里有一句口径说明，够 review 用了。

#: 可编辑（可落库）的角色。``admin`` 不在此列 —— 见模块头的第 2 点。
EDITABLE_ROLES: tuple[str, ...] = ("department", "secretary", "student")

#: 角色级月额度的能力集合。与 ``ck_role_quotas_kind`` 完全一致。
#: V051 起用户侧的 ``chat`` / ``ocr`` 归并成统一 AF 池 ``af``（一次 OCR 折
#: ``AI_OCR_AF_COST`` AF），``schedule`` 是「能建几条定时任务」，与 AF 无关。
QUOTA_KINDS: tuple[str, ...] = ("af", "schedule")

#: 额度取数的角色优先级（第一个命中的角色说了算，不叠加、不取最大值）。
QUOTA_ROLE_PRIORITY: tuple[str, ...] = EDITABLE_ROLES

#: department 在 V043 之前拿不到的能力：导出/导入/日志/通知/封禁只读/配额管理
#: 原先都只有 admin 能进。默认关 = 与改造前行为完全一致；管理员在「角色策略」页
#: 打开某一项，才是真的把这项能力下放给学生会。
ADMIN_ONLY_FEATURE_KEYS: tuple[str, ...] = (
    "export.time",
    "import.activity",
    "logs.read",
    "notify.publish",
    "ban.read",
    "quota.manage",
)

#: 「库里没有任何覆盖」时的行为。**改动这里等于改动线上默认行为**。
DEFAULTS: dict[str, dict[str, Any]] = {
    "department": {
        "features": {
            key: key not in ADMIN_ONLY_FEATURE_KEYS for key in FEATURE_KEYS
        },
        # af 取改造前 ``chat`` 的口径：OCR 现在也吃这个池子，原 ``ocr: 100``
        # 的独立额度已无处可加（合并后按 AF 折算：1 次 OCR = 100 AF，
        # 1 AF = 1k token 基准对话成本）。
        "quotas": {"af": 200_000, "schedule": 1},
    },
    "secretary": {
        "features": {
            key: key in ("activity.stats", "ai.ocr") for key in FEATURE_KEYS
        },
        "quotas": {"af": 0, "schedule": 0},
    },
    "student": {
        "features": {key: False for key in FEATURE_KEYS},
        "quotas": {"af": 0, "schedule": 0},
    },
}

# ---------------------------------------------------------------------------
# 进程内快照
# ---------------------------------------------------------------------------
#
# 只放**库里真实存在的覆盖**：没覆盖的格子不在这里，读取时再叠加 :data:`DEFAULTS`。
# 这样「把整张表删空」与「从没配过」是同一种状态。

_features: dict[tuple[str, str], bool] = {}
_quotas: dict[tuple[str, str], int] = {}
_meta: dict[str, tuple[datetime | None, str | None]] = {}
_last_refresh = 0.0
_refreshing = False
#: 上次读库时 Redis 里的版本号；``None`` = Redis 不可用 / 还没读过。
_last_version: int | None = None
#: 上次真正回源数据库的时刻（``_last_refresh`` 是「上次确认快照可用」的时刻）。
_last_full_read = 0.0


def _reset_state() -> None:
    """清空快照（测试钩子；也让下一次读取重新按 DEFAULTS 算）。"""
    global _last_refresh, _last_version, _last_full_read
    _features.clear()
    _quotas.clear()
    _meta.clear()
    _last_refresh = 0.0
    _last_version = None
    _last_full_read = 0.0


def reset_cache() -> None:
    """公开的缓存失效入口（测试/管理脚本用）。"""
    _reset_state()


def invalidate() -> None:
    """写后失效：下一次 :func:`ensure_fresh` 必刷（不等 TTL、也不受版本闸影响）。"""
    global _last_refresh, _last_full_read
    _last_refresh = 0.0
    _last_full_read = 0.0


def _repo():
    """仓储入口（单一接缝：测试打桩这一层就够，不必碰 db.backends）。

    延迟导入是为了保持「导入本模块不做任何 I/O」，也避免 ORM 元数据在 import
    阶段被拉进只需要常量（DEFAULTS / FEATURE_KEYS）的调用方。
    """
    from db.repos.oracle.role_policies import OracleRolePoliciesRepo

    return OracleRolePoliciesRepo()


async def _current_version() -> int | None:
    """Redis 里的角色策略版本号；Redis 不可用返回 ``None``（闸门禁用，照旧读库）。"""
    try:
        return await cache.version_or_none(VERSION_SCOPE)
    except Exception:  # noqa: BLE001 - 缓存层不该影响策略读取
        return None


async def refresh(*, force: bool = False) -> None:
    """重建进程内快照；``force`` 绕过版本闸直接回源。

    每次先花一次 Redis GET 看 ``roles`` 版本号：版本没变、且上次回源不到
    :data:`FULL_RELOAD_SECONDS` → 库里一定还是那份，直接返回。写路径
    （:func:`replace_role`）自增版本号，别的进程下一轮即可见。
    任何读库失败都回落代码默认值。
    """
    global _refreshing, _last_refresh, _last_version, _last_full_read
    if _refreshing:
        # 上一轮还没读完：这一轮直接跳过，不让并发刷新叠加连接。
        return
    _refreshing = True
    try:
        version = await _current_version()
        now = time.monotonic()
        if (
            not force
            and version is not None
            and version == _last_version
            and _last_full_read > 0
            and now - _last_full_read < FULL_RELOAD_SECONDS
        ):
            # 版本没变：别的进程没写过，快照仍然可信，只把「确认时刻」推后。
            _last_refresh = now
            return

        features: dict[tuple[str, str], bool] = {}
        quotas: dict[tuple[str, str], int] = {}
        meta: dict[str, tuple[datetime | None, str | None]] = {}
        rows: dict[str, list[dict[str, Any]]] = {"features": [], "quotas": []}
        try:
            rows = await _repo().list_all() or rows
        except Exception:  # noqa: BLE001 - 策略表不可用时按默认行为
            logger.exception("读取 ROLE_FEATURES / ROLE_QUOTAS 失败，本次回落代码默认值")
            rows = {"features": [], "quotas": []}

        for row in rows.get("features") or []:
            role = row.get("role")
            key = row.get("feature_key")
            if role not in EDITABLE_ROLES or key not in FEATURE_KEYS:
                # 手工改库能绕过 CHECK 里没覆盖的部分（比如加了个新 key 但代码还没
                # 认）：忽略而不是崩，错误在日志里可见。
                logger.warning("忽略无法识别的角色开关：%r/%r", role, key)
                continue
            features[(str(role), str(key))] = bool(row.get("enabled"))
            _touch_meta_into(meta, role, row)

        for row in rows.get("quotas") or []:
            role = row.get("role")
            kind = row.get("kind")
            limit = row.get("monthly_limit")
            if role not in EDITABLE_ROLES or kind not in QUOTA_KINDS:
                logger.warning("忽略无法识别的角色额度：%r/%r", role, kind)
                continue
            if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
                logger.warning("角色额度不是非负整数，忽略：%r/%r=%r", role, kind, limit)
                continue
            quotas[(str(role), str(kind))] = limit
            _touch_meta_into(meta, role, row)

        _features.clear()
        _features.update(features)
        _quotas.clear()
        _quotas.update(quotas)
        _meta.clear()
        _meta.update(meta)
        _last_refresh = time.monotonic()
        _last_full_read = _last_refresh
        _last_version = version
    finally:
        _refreshing = False


def _touch_meta_into(
    meta: dict[str, tuple[datetime | None, str | None]],
    role: str,
    row: dict[str, Any],
) -> None:
    """:func:`refresh` 的局部版 ``_meta`` 累加（不碰模块级状态）。"""
    current_at, current_by = meta.get(role, (None, None))
    updated_at = row.get("updated_at")
    updated_by = row.get("updated_by")
    if updated_at is not None and (current_at is None or updated_at > current_at):
        current_at = updated_at
    meta[role] = (current_at, updated_by or current_by)


async def ensure_fresh(force: bool = False) -> None:
    """快照过期（或 ``force``）时刷一次；没有库也不会抛。"""
    if (
        force
        or _last_refresh <= 0
        or time.monotonic() - _last_refresh >= REFRESH_SECONDS
    ):
        await refresh(force=force)


# ---------------------------------------------------------------------------
# 同步读取（guard 用）
# ---------------------------------------------------------------------------


def _role_set(roles: Iterable[str] | None) -> set[str]:
    return {role for role in (roles or ()) if isinstance(role, str) and role}


def feature_enabled(roles: Iterable[str] | None, key: str) -> bool:
    """该组角色是否拥有某个功能开关；``admin`` 恒为 True。

    多角色取**并集**：任何一个是 True 就放行（与 ``AI_PROMPT_CAPTURE_ROLES``
    的多角色口径一致）。未知 key 一律 False —— 拼错的 key 不该被静默放行。
    """
    role_set = _role_set(roles)
    if _ADMIN_ROLE in role_set:
        return True
    if key not in FEATURE_KEYS:
        return False
    for role in EDITABLE_ROLES:
        if role not in role_set:
            continue
        value = _features.get((role, key))
        if value is None:
            value = bool(DEFAULTS[role]["features"].get(key, False))
        if value:
            return True
    return False


def monthly_limit(roles: Iterable[str] | None, kind: str) -> int | None:
    """角色级月额度；``None`` = 没有意见（admin 无限 / kind 不是角色级）。

    * ``admin`` → ``None``：调用方按「无限」处理（:func:`effective_policy` 会把它
      显式翻成 ``unlimited=True``）。
    * ``kind`` 不在 :data:`QUOTA_KINDS` 里（例如站点级 ``ocr_tencent``，或 V051
      之前的历史 kind ``chat`` / ``ocr``）→ ``None``：站点额度是平台出资的口径，
      与角色无关，调用方继续走自己的链路。
    * 其余按 :data:`QUOTA_ROLE_PRIORITY` 取**第一个命中**的角色；没有任何角色
      命中时 ``None``（调用方回落全局默认）。
    """
    role_set = _role_set(roles)
    if _ADMIN_ROLE in role_set:
        return None
    if kind not in QUOTA_KINDS:
        return None
    for role in QUOTA_ROLE_PRIORITY:
        if role not in role_set:
            continue
        value = _quotas.get((role, kind))
        if value is None:
            raw = DEFAULTS[role]["quotas"].get(kind, 0)
            value = (
                int(raw)
                if isinstance(raw, int) and not isinstance(raw, bool)
                else 0
            )
        return max(0, int(value))
    return None


# ---------------------------------------------------------------------------
# 视图（管理页 / 自助页）
# ---------------------------------------------------------------------------


def _role_view(role: str) -> dict[str, Any]:
    """一个角色的**生效**矩阵（覆盖后 = 覆盖值，否则 DEFAULTS）。"""
    defaults = DEFAULTS[role]
    features = {
        key: _features.get((role, key), bool(defaults["features"].get(key, False)))
        for key in FEATURE_KEYS
    }
    quotas = {
        kind: _quotas.get((role, kind), int(defaults["quotas"].get(kind, 0)))
        for kind in QUOTA_KINDS
    }
    updated_at, updated_by = _meta.get(role, (None, None))
    return {
        "role": role,
        "features": features,
        "quotas": quotas,
        "updated_at": updated_at,
        "updated_by": updated_by,
    }


async def all_policies(force: bool = True) -> dict[str, Any]:
    """管理页视图：三个可编辑角色的生效矩阵 + 键清单。

    ``force=True`` 是管理页的默认口径：管理员打开页面看到的必须是库里的当前
    值，不能是别的 worker 5 秒前的快照。
    """
    await ensure_fresh(force=force)
    return {
        "roles": [_role_view(role) for role in EDITABLE_ROLES],
        "editable_roles": list(EDITABLE_ROLES),
        # 纯 key 数组（顺序即 UI 顺序）：前端用 ``ROLE_FEATURE_KEYS`` 兜底，
        # 后端只保证「后端认哪些 key」，文案不在这里。
        "feature_keys": list(FEATURE_KEYS),
    }


async def replace_role(
    role: str,
    features: dict[str, bool],
    quotas: dict[str, int],
    actor: str | None = None,
) -> dict[str, Any]:
    """写库（单事务全量替换）→ 立刻重读快照 → 广播版本号 → 返回最新矩阵。

    写后**主动 refresh** 而不是只 ``invalidate()``：管理员点完保存，本进程下一次
    guard 判断就必须是新值；把快照清空会让中间那一小段时间回落到 DEFAULTS，
    那是比「多一次查询」更糟的错值。调用方（路由）负责校验与双人授权。

    广播放最后：别的进程看到版本变化时，本进程的快照已经是最新的了。
    """
    await _repo().upsert_role(role, features, quotas, actor)
    await refresh()
    await _publish_change()
    return _role_view(role)


async def _publish_change() -> None:
    """失效本域缓存并自增版本号；失败只降级（写已经落库，不能因此 500）。"""
    global _last_version
    try:
        await cache.flush_scopes([VERSION_SCOPE])
        _last_version = await _current_version()
    except Exception:  # noqa: BLE001 - 广播失败只影响跨进程时效
        _last_version = None


async def effective_policy(roles: Iterable[str] | None) -> dict[str, Any]:
    """当前登录人的生效策略：``{features, quotas, unlimited}``。

    ``quotas`` 里 ``None`` 表示该能力无限（只有 admin 会这样）；``unlimited``
    是给前端一个明确的布尔，免得它去猜「三个额度都是 null」的含义。
    形状刻意**不含** role / updated_*：这是「我此刻能做什么」，不是「某一行
    配置的记录」，把配置元数据塞进来只会让人以为它是可提交回去的草稿。
    """
    await ensure_fresh()
    role_set = _role_set(roles)
    unlimited = _ADMIN_ROLE in role_set
    return {
        "features": {key: feature_enabled(role_set, key) for key in FEATURE_KEYS},
        "quotas": {
            kind: (None if unlimited else monthly_limit(role_set, kind))
            for kind in QUOTA_KINDS
        },
        "unlimited": unlimited,
    }


__all__ = [
    "DEFAULTS",
    "EDITABLE_ROLES",
    "FEATURE_KEYS",
    "QUOTA_KINDS",
    "QUOTA_ROLE_PRIORITY",
    "REFRESH_SECONDS",
    "all_policies",
    "effective_policy",
    "ensure_fresh",
    "feature_enabled",
    "invalidate",
    "monthly_limit",
    "refresh",
    "replace_role",
    "reset_cache",
]
