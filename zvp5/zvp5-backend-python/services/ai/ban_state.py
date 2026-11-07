"""执行期的封禁态查询：进程内 TTL 缓存 + 与鉴权层同一口径。

**为什么需要这一层**：封禁并不只发生在登录那一刻。管理员手工封、违规梯度封、
并发的另一条请求触发封禁，都可能在一次对话/一次 agent run 的**生命周期中间**
落下；而这两个东西的生命周期远长于一个 HTTP 请求（流式回答能吐几分钟，agent run
更久）。请求入口的闸（``util.object_id`` / ``services.ai.api_keys``）只在请求开始
时看一次，之后再也不看——所以执行期需要有地方能问「此刻是否已封」。

**为什么带缓存**：封禁是低变化率事实（秒级陈旧可以接受），而问的频率是「每 32
个 chunk」「每轮工具执行前」。1 秒的 TTL 把一次长回答的查询数从上千次压到几秒
一次；写入方（违规链路）落封后调 :func:`invalidate`，于是「刚封的人在同一次
请求里就该被拦住」不依赖 TTL 到期。

**为什么失败降级为「未封」**：入口闸已经判过封禁，这一层是**在途中止**的加速器，
不是鉴权本身。读库抖动若变成 500，会把计费与对话这类主链路一起打挂；晚一秒止损
的代价远小于此。失败结论同样进缓存——否则读库故障期间每一次 chunk 检查都会再试
一遍并再记一条日志。
"""

from __future__ import annotations

import logging
import time

from db import backends
from util.ban import ban_is_active

_logger = logging.getLogger(__name__)

#: 默认缓存时长（秒）。1 秒足够让「同一轮里的多次检查」共用一次查询，又短到
#: 「管理员封完看到效果」不需要等任何后台任务。
DEFAULT_TTL_SECONDS = 1.0

#: 缓存条目的上界：一次部署里活跃用户远小于它；超了先清过期项，再兜底截断，
#: 免得一个长跑进程把「每个见过的 oid」都留在内存里。
MAX_CACHE_ENTRIES = 4096

# oid -> (expires_at_monotonic, banned)
_cache: dict[str, tuple[float, bool]] = {}


def invalidate(user_oid: str | None = None) -> None:
    """清掉缓存；不传 oid = 全清（只给测试/运维用）。

    封禁写入方**必须**在落封后调一次：同一次请求里的后续检查要立刻看到新状态，
    不能等 TTL 过期。
    """
    if user_oid is None:
        _cache.clear()
        return
    _cache.pop(str(user_oid or ""), None)


def _store(oid: str, banned: bool, ttl: float) -> None:
    if len(_cache) >= MAX_CACHE_ENTRIES:
        now = time.monotonic()
        for key in [key for key, (expires, _) in _cache.items() if expires <= now]:
            _cache.pop(key, None)
        if len(_cache) >= MAX_CACHE_ENTRIES:
            _cache.clear()
    _cache[oid] = (time.monotonic() + max(0.0, ttl), banned)


async def is_banned(user_oid: str, ttl: float = DEFAULT_TTL_SECONDS) -> bool:
    """该账号**此刻**是否处于封禁中（``ban_is_active`` 同一口径）。

    只读 ``USERS.BANNED`` 会把到期封禁的用户永久锁死（``banned`` 列不会自动归零），
    所以判定一律走 :func:`util.ban.ban_is_active`。

    读库异常 → 记一次警告并返回 ``False``（降级为未封）；不抛异常给调用方。
    """
    oid = str(user_oid or "")
    if not oid:
        return False

    cached = _cache.get(oid)
    if cached is not None and cached[0] > time.monotonic():
        return cached[1]

    try:
        doc = await backends.get_users_repo().get_by_oid(oid)
    except Exception:  # noqa: BLE001 - 封禁检查是加速器，读不到不该打挂主链路
        _logger.warning("读取用户封禁状态失败，按未封禁处理（user=%s）", oid, exc_info=True)
        _store(oid, False, ttl)
        return False

    active = ban_is_active(doc)
    _store(oid, active, ttl)
    return active


__all__ = [
    "DEFAULT_TTL_SECONDS",
    "MAX_CACHE_ENTRIES",
    "invalidate",
    "is_banned",
]
