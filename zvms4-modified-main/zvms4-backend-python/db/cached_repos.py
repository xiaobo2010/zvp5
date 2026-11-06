"""热点仓储的 Redis 读穿缓存代理。

注入点在 :mod:`db.backends` 的工厂 —— 接口不变，只把返回对象包一层：

* **事务内直通**：``session`` 非空时整条代理不做缓存。调用方把 session 传进来
  就是为了读自己未提交的写，缓存会把它打回旧值。
* **写后失效**：每个写方法落库后 ``FORGET`` 受影响的 key；整类列表用
  ``epoch`` 版本号整体失效（见 :mod:`db.cache`）。
* **缓存不可用 = 原仓储**：``db.cache`` 在无 ``REDIS_URI``/Redis 故障时全部
  回源，代理是透明的一层。
* **测试安全**：仓库测试普遍 ``monkeypatch.setattr(backends, "get_xxx_repo", ...)``，
  打桩直接绕过工厂，因此 SQL 语句计数类断言不受影响。

缓存对象选择依据（``zvp-frontend`` 的 perfdb，13 天窗口）：

========================  ==============  ==================
对象                       读频             失效方式
========================  ==============  ==================
``users.get_for_auth``     每请求 1 次      写用户后精确删除（30s TTL 兜底）
``users.get_by_oid``       详情/权限判定     同上（60s）
``users.get_names``        每次写审计文案    用户 epoch + 300s
``users.list_by_class``    班级成员/统计页   用户 epoch + 300s
``groups.get``             权限校验+详情     写 group 后精确删除（300s）
``groups.list_classes``    ``GET /api/groups``  group epoch + 60s
``activities.get``         详情/成员页       写活动后精确删除（60s）
``activities.list_effective`` 时长重算/导出  活动 epoch + 600s
``ai_conversations.get``   会话打开/run 启动 写会话后按 oid 删除（30s）
``..latest_by_scene``      场景会话定位      新建会话后删除（30s）
========================  ==============  ==================

刻意**不**缓存的对象：``get_for_update``（行锁语义）、``ai_runs.claim_next``
等 CAS 写、``messages.list_after``（追加写、顺序敏感）、任何带 ``session``
的事务内读。
"""

from __future__ import annotations

import hashlib
import random
from typing import Any

from db import cache
from db.ids import normalize_oid

#: 认证窄投影：封禁/角色改动要尽快可见，只给 30s。
TTL_AUTH = 30.0
#: 用户全量文档（含 password_hash；Redis 只监听 127.0.0.1 且不落盘）。
TTL_USER = 60.0
#: 显示名与班级成员列表：写用户时 epoch 整体失效，TTL 只兜跨进程遗漏。
TTL_NAMES = 300.0
TTL_CLASS_MEMBERS = 300.0
#: 班级/权限组本身几乎不变。
TTL_GROUP = 300.0
TTL_GROUP_LIST = 60.0
#: 活动详情中等频；有效活动全集变化极慢。
TTL_ACTIVITY = 60.0
TTL_ACTIVITIES_EFFECTIVE = 600.0
#: 会话：run 期间频繁 touch，缓存只覆盖「打开会话 / 场景定位」这两条读。
TTL_CONVERSATION = 30.0
#: TTL 抖动比例（±10%）。同一批 key（比如一次首页把所有班级名单都读一遍）如果
#: 用同一个 TTL，就会在同一秒集体过期，然后一起回源——「缓存雪崩」的标准形状。
#: 抖动把过期时刻摊开，代价只是最长多活 10% 的时间。
_TTL_JITTER = 0.1


def _ttl(seconds: float) -> float:
    """给固定 TTL 加 ±10% 抖动（保留原来的量级与语义）。"""
    return seconds * (1.0 + random.uniform(-_TTL_JITTER, _TTL_JITTER))


def _k(*parts: Any) -> str:
    return ":".join((cache.NS, *map(str, parts)))


def _digest(parts: list[str]) -> str:
    return hashlib.sha1("\x1f".join(parts).encode("utf-8")).hexdigest()


def _norm(value: Any) -> str:
    """写路径拿到的 oid 与读路径同口径（``normalize_oid``），否则 key 对不上。"""
    try:
        return normalize_oid(str(value))
    except Exception:  # noqa: BLE001 - 非 oid 形态原样返回
        return str(value)


class _CachedRepo:
    """代理基类：未覆盖的属性/方法原样透传给内层仓储。"""

    def __init__(self, inner: Any, *, use_cache: bool = True) -> None:
        self._inner = inner
        self._use_cache = use_cache

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    async def _read(self, key: str, ttl: float, loader: Any) -> Any:
        if not self._use_cache:
            return await loader()
        # 代理层的写只是「让缓存早点热」：不占响应时间（Redis 抖动不能拖慢读请求）。
        return await cache.remember(
            key, _ttl(ttl), loader, async_backfill=True
        )


class CachedUsersRepo(_CachedRepo):
    """``users`` 读写热点。"""

    # ------------------------------------------------------------------ 读
    async def get_by_oid(self, oid: str) -> Any:
        oid = normalize_oid(oid)
        return await self._read(
            _k("user", oid), TTL_USER, lambda: self._inner.get_by_oid(oid)
        )

    async def get_for_auth(self, oid: str) -> Any:
        # 窄投影单独一套 key：认证文档不含 password，任何「整行文档」都不得
        # 冒充它（``tests/test_users_auth_projection.py`` 是这条契约）。
        oid = normalize_oid(oid)
        return await self._read(
            _k("auth", oid), TTL_AUTH, lambda: self._inner.get_for_auth(oid)
        )

    async def get_names(self, oids: list[str]) -> dict[str, str]:
        unique = sorted({normalize_oid(oid) for oid in oids})
        if not unique:
            return {}
        if not self._use_cache:
            return await self._inner.get_names(oids)
        version = await cache.epoch("users")
        key = _k("names", version, _digest(unique))
        return await cache.remember(
            key,
            _ttl(TTL_NAMES),
            lambda: self._inner.get_names(oids),
            async_backfill=True,
        )

    async def list_by_class(self, class_oid: str) -> list[Any]:
        class_oid = normalize_oid(class_oid)
        if not self._use_cache:
            return await self._inner.list_by_class(class_oid)
        version = await cache.epoch("users")
        key = _k("class", version, class_oid)
        return await cache.remember(
            key,
            _ttl(TTL_CLASS_MEMBERS),
            lambda: self._inner.list_by_class(class_oid),
            async_backfill=True,
        )

    # ------------------------------------------------------------------ 写
    async def _forget_user(self, oid: Any, *, epoch: bool = False) -> None:
        if oid is None:
            return
        oid = _norm(oid)
        await cache.forget(_k("user", oid), _k("auth", oid))
        if epoch:
            await cache.bump_epoch("users")

    async def create(self, *args: Any, **kwargs: Any) -> str:
        oid = await self._inner.create(*args, **kwargs)
        await cache.bump_epoch("users")
        return oid

    async def set_password(self, *args: Any, **kwargs: Any) -> None:
        result = await self._inner.set_password(*args, **kwargs)
        await self._forget_user(args[0] if args else kwargs.get("oid"))
        return result

    async def set_password_guarded(self, *args: Any, **kwargs: Any) -> int:
        result = await self._inner.set_password_guarded(*args, **kwargs)
        await self._forget_user(args[0] if args else kwargs.get("oid"))
        return result

    async def set_roles(self, *args: Any, **kwargs: Any) -> None:
        result = await self._inner.set_roles(*args, **kwargs)
        await self._forget_user(args[0] if args else kwargs.get("oid"))
        return result

    async def set_business_id(self, *args: Any, **kwargs: Any) -> None:
        result = await self._inner.set_business_id(*args, **kwargs)
        # ``business_id`` 出现在列表/显示名场景（名单索引），整类失效更省心。
        await self._forget_user(
            args[0] if args else kwargs.get("oid"), epoch=True
        )
        return result

    async def rotate_session(self, *args: Any, **kwargs: Any) -> None:
        result = await self._inner.rotate_session(*args, **kwargs)
        await self._forget_user(args[0] if args else kwargs.get("oid"))
        return result

    async def delete_user_cascade(self, *args: Any, **kwargs: Any) -> str:
        result = await self._inner.delete_user_cascade(*args, **kwargs)
        await self._forget_user(args[0] if args else kwargs.get("oid"), epoch=True)
        return result

    async def purge_trash_batch(self, *args: Any, **kwargs: Any) -> int:
        result = await self._inner.purge_trash_batch(*args, **kwargs)
        await cache.bump_epoch("users")
        return result

    async def set_banned(self, *args: Any, **kwargs: Any) -> None:
        result = await self._inner.set_banned(*args, **kwargs)
        await self._forget_user(args[0] if args else kwargs.get("oid"))
        return result

    async def set_ban(self, *args: Any, **kwargs: Any) -> Any:
        result = await self._inner.set_ban(*args, **kwargs)
        await self._forget_user(args[0] if args else kwargs.get("oid"))
        return result

    async def set_ban_if_stronger(self, *args: Any, **kwargs: Any) -> Any:
        result = await self._inner.set_ban_if_stronger(*args, **kwargs)
        await self._forget_user(args[0] if args else kwargs.get("oid"))
        return result

    async def clear_ban(self, *args: Any, **kwargs: Any) -> None:
        result = await self._inner.clear_ban(*args, **kwargs)
        await self._forget_user(args[0] if args else kwargs.get("oid"))
        return result

    async def set_primary_xuehai(self, *args: Any, **kwargs: Any) -> None:
        result = await self._inner.set_primary_xuehai(*args, **kwargs)
        await self._forget_user(args[0] if args else kwargs.get("oid"))
        return result

    async def clear_primary_xuehai(self, *args: Any, **kwargs: Any) -> None:
        result = await self._inner.clear_primary_xuehai(*args, **kwargs)
        await self._forget_user(args[0] if args else kwargs.get("oid"))
        return result

    async def set_class(self, *args: Any, **kwargs: Any) -> None:
        result = await self._inner.set_class(*args, **kwargs)
        await self._forget_user(
            args[0] if args else kwargs.get("user_oid"), epoch=True
        )
        return result

    async def detach_class(self, *args: Any, **kwargs: Any) -> int:
        result = await self._inner.detach_class(*args, **kwargs)
        # 整个班的成员都变了，逐 oid 失效不现实；epoch 一把掀桌。
        await cache.bump_epoch("users")
        return result

    async def add_past(self, *args: Any, **kwargs: Any) -> None:
        result = await self._inner.add_past(*args, **kwargs)
        await self._forget_user(args[0] if args else kwargs.get("oid"))
        return result

    async def remove_past(self, *args: Any, **kwargs: Any) -> None:
        result = await self._inner.remove_past(*args, **kwargs)
        await self._forget_user(args[0] if args else kwargs.get("oid"))
        return result


class CachedGroupsRepo(_CachedRepo):
    """``groups``：班级/权限组读极多、写极少。"""

    async def get(self, oid: str) -> Any:
        oid = normalize_oid(oid)
        return await self._read(
            _k("group", oid), TTL_GROUP, lambda: self._inner.get(oid)
        )

    async def get_permission_group(self, oid: str) -> Any:
        oid = normalize_oid(oid)
        return await self._read(
            _k("pgroup", oid), TTL_GROUP, lambda: self._inner.get_permission_group(oid)
        )

    async def list_classes(
        self, query: str = "", page: int = 1, size: int | None = None
    ) -> Any:
        # ``size`` 用 None 表示「用仓储自己的上限」（_CLASS_LIST_CAP），
        # 代理不复制那个默认值，否则仓储改上限时代理会静默截断。
        def _call() -> Any:
            if size is None:
                return self._inner.list_classes(query, page)
            return self._inner.list_classes(query, page, size)

        if not self._use_cache:
            return await _call()
        version = await cache.epoch("groups")
        key = _k("glist", version, query, page, size if size is not None else "-")
        return await cache.remember(key, _ttl(TTL_GROUP_LIST), _call, async_backfill=True)

    async def list_permission_groups(
        self, query: str = "", permissions: list[str] | None = None
    ) -> Any:
        if not self._use_cache:
            return await self._inner.list_permission_groups(query, permissions)
        version = await cache.epoch("groups")
        key = _k(
            "pglist", version, query, _digest(sorted(permissions or []))
        )
        return await cache.remember(
            key,
            _ttl(TTL_GROUP_LIST),
            lambda: self._inner.list_permission_groups(query, permissions),
            async_backfill=True,
        )

    async def create(self, *args: Any, **kwargs: Any) -> str:
        oid = await self._inner.create(*args, **kwargs)
        await cache.bump_epoch("groups")
        return oid

    async def rename(self, *args: Any, **kwargs: Any) -> None:
        result = await self._inner.rename(*args, **kwargs)
        oid = _norm(args[0] if args else kwargs.get("oid"))
        await cache.forget(_k("group", oid), _k("pgroup", oid))
        await cache.bump_epoch("groups")
        return result

    async def set_description(self, *args: Any, **kwargs: Any) -> None:
        result = await self._inner.set_description(*args, **kwargs)
        oid = _norm(args[0] if args else kwargs.get("oid"))
        await cache.forget(_k("group", oid), _k("pgroup", oid))
        await cache.bump_epoch("groups")
        return result

    async def delete(self, *args: Any, **kwargs: Any) -> None:
        result = await self._inner.delete(*args, **kwargs)
        oid = _norm(args[0] if args else kwargs.get("oid"))
        await cache.forget(_k("group", oid), _k("pgroup", oid))
        await cache.bump_epoch("groups")
        return result


class CachedActivitiesRepo(_CachedRepo):
    """``activities``：详情与「有效活动全集」两条读路径。"""

    async def get(self, oid: str) -> Any:
        oid = normalize_oid(oid)
        return await self._read(
            _k("activity", oid), TTL_ACTIVITY, lambda: self._inner.get(oid)
        )

    async def list_effective(
        self, date_start: Any = None, date_end: Any = None
    ) -> Any:
        # 只有「不带日期区间」才是全量稳定结果；带区间的按调用方参数缓存
        # 收益低（每用户重算的区间都不同），直接回源。
        if not self._use_cache or date_start is not None or date_end is not None:
            return await self._inner.list_effective(date_start, date_end)
        version = await cache.epoch("activities")
        key = _k("activities", "effective", version)
        return await cache.remember(
            key,
            _ttl(TTL_ACTIVITIES_EFFECTIVE),
            lambda: self._inner.list_effective(date_start, date_end),
            async_backfill=True,
        )

    async def create(self, *args: Any, **kwargs: Any) -> str:
        oid = await self._inner.create(*args, **kwargs)
        await cache.bump_epoch("activities")
        return oid

    async def update(self, *args: Any, **kwargs: Any) -> None:
        result = await self._inner.update(*args, **kwargs)
        oid = _norm(args[0] if args else kwargs.get("oid"))
        await cache.forget(_k("activity", oid))
        await cache.bump_epoch("activities")
        return result

    async def delete(self, *args: Any, **kwargs: Any) -> None:
        result = await self._inner.delete(*args, **kwargs)
        oid = _norm(args[0] if args else kwargs.get("oid"))
        await cache.forget(_k("activity", oid))
        await cache.bump_epoch("activities")
        return result


class CachedAIConversationsRepo(_CachedRepo):
    """AI 会话（agent session）的「打开 / 场景定位」两条读路径。

    刻意不缓存消息列表与 run 记录：``messages`` 是追加写、``ai_runs`` 有 CAS
    状态机，任何缓存都会破坏顺序/认领语义。
    """

    async def get(self, oid: str, owner_oid: str) -> Any:
        oid, owner_oid = str(oid), str(owner_oid)
        key = _k("conv", oid)
        if not self._use_cache:
            return await self._inner.get(oid, owner_oid)
        row = await cache.remember(
            key,
            _ttl(TTL_CONVERSATION),
            lambda: self._inner.get(oid, owner_oid),
            async_backfill=True,
        )
        # key 只带 oid（写方法大多只知道 oid），所以归属校验在命中后补一次：
        # 别人的会话读出来必须返回 None，且不落缓存。
        if row is not None and str(row.get("owner_oid")) != owner_oid:
            return None
        return row

    async def latest_by_scene(self, owner_oid: str, scene: str) -> Any:
        owner_oid, scene = str(owner_oid), str(scene)
        key = _k("convlatest", owner_oid, scene)
        return await self._read(
            key,
            TTL_CONVERSATION,
            lambda: self._inner.latest_by_scene(owner_oid, scene),
        )

    async def create(self, *args: Any, **kwargs: Any) -> Any:
        row = await self._inner.create(*args, **kwargs)
        scene = kwargs.get("scene")
        owner_oid = kwargs.get("owner_oid")
        if scene and owner_oid:
            await cache.forget(_k("convlatest", owner_oid, scene))
        return row

    async def update(self, *args: Any, **kwargs: Any) -> bool:
        result = await self._inner.update(*args, **kwargs)
        oid = _norm(args[0] if args else kwargs.get("oid"))
        await cache.forget(_k("conv", oid))
        return result

    async def touch(self, *args: Any, **kwargs: Any) -> None:
        await self._inner.touch(*args, **kwargs)
        oid = _norm(args[0] if args else kwargs.get("oid"))
        await cache.forget(_k("conv", oid))

    async def add_tokens(self, *args: Any, **kwargs: Any) -> None:
        await self._inner.add_tokens(*args, **kwargs)
        oid = _norm(args[0] if args else kwargs.get("oid"))
        await cache.forget(_k("conv", oid))

    async def set_summary(self, *args: Any, **kwargs: Any) -> None:
        await self._inner.set_summary(*args, **kwargs)
        oid = _norm(args[0] if args else kwargs.get("oid"))
        await cache.forget(_k("conv", oid))

    async def set_context_tokens(self, *args: Any, **kwargs: Any) -> None:
        await self._inner.set_context_tokens(*args, **kwargs)
        oid = _norm(args[0] if args else kwargs.get("oid"))
        await cache.forget(_k("conv", oid))

    async def mark_compaction_degraded(self, *args: Any, **kwargs: Any) -> None:
        await self._inner.mark_compaction_degraded(*args, **kwargs)
        oid = _norm(args[0] if args else kwargs.get("oid"))
        await cache.forget(_k("conv", oid))

    async def delete_batched(self, *args: Any, **kwargs: Any) -> int:
        result = await self._inner.delete_batched(*args, **kwargs)
        oid = _norm(args[0] if args else kwargs.get("oid"))
        await cache.forget(_k("conv", oid))
        return result


__all__ = [
    "CachedUsersRepo",
    "CachedGroupsRepo",
    "CachedActivitiesRepo",
    "CachedAIConversationsRepo",
]
