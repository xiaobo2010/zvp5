"""可选 Redis 读穿缓存。

设计边界（与仓库既有约定对齐）：

* **可缺省**：``settings.REDIS_URI`` 为空或 ``redis`` 包未安装时整套缓存退化为
  直读，代理层行为与原始仓储完全一致。仓库里所有「可选能力」都是这个写法
  （见 ``services/ai/config.py`` 的模块 docstring）。
* **读失败不抛出**：Redis 抖动/重启不能把业务请求变成 500。读路径吞异常后回源，
  连续失败进入 5s 降级窗口，窗口内不再尝试连接（与 ``db/system_state.py`` 的
  fail-open 语义一致，只是这里放宽到网络故障）。
* **值是带类型标签的 JSON**：仓储返回的文档里混有 ``datetime`` / ``date`` /
  ``Decimal``（``_user_doc``、``_activity_doc`` 都保留原始类型），直接
  ``json.dumps`` 会丢类型。标签编解码保证写回后调用方的 ``isinstance`` 判断、
  ``ban_until.isoformat()`` 之流的行为不变。
* **写操作显式失效**：TTL 只是兜底（漏掉的失效/别的进程），读多写少的对象在
  写方法返回后立刻 ``FORGET``。跨进程天然成立——Redis 本身就是共享状态，
  不像 ``services/ai/*`` 的进程内 dict 需要等 TTL。

键空间：``zvms:<scope>:<...>``。``epoch``（单调计数器）用于「整类失效」：
key 里带版本号，写时 INCR 一次，旧 key 靠 TTL 自然淘汰。

``epoch`` 同时充当**跨进程版本号**：``services.settings_runtime`` /
``services.roles.policy`` 每 5 秒只花一次 GET 看版本有没有变，变了才回源 ADB
（写事件自动刷新）。为此 epoch key 一律带 30 天 TTL —— 它们是缓存元数据，
不是业务数据，不该有永久 key。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Awaitable, Callable, Sequence

try:  # 可选依赖：装不上就整层禁用，不影响主流程
    from redis import asyncio as _aioredis
except Exception:  # pragma: no cover - 只在极简环境里走到
    _aioredis = None

_logger = logging.getLogger(__name__)

NS = "zvms"
CONNECT_TIMEOUT_SECONDS = 0.5
DEGRADE_SECONDS = 5.0
#: 单条 value 超过这个大小就不进缓存（``past`` / ``eligibility`` 是 CLOB，
#: 极少数用户能到几百 KB；把它们塞进 Redis 挤占内存不划算）。
MAX_VALUE_BYTES = 256 * 1024
#: epoch key 的 TTL（30 天）。epoch 是缓存元数据，允许过期（过期 = 回到 0，
#: 只是让旧 key 失去意义，不会造成错误读）；永久 key 会在 Redis 里越攒越多。
EPOCH_TTL_SECONDS = 30 * 24 * 3600
#: ``SCAN`` 的 COUNT 提示 / 单次 ``UNLINK`` 的批量。批量删除避免一次命令塞几万
#: 个 key 把单条命令的耗时抬起来（Redis 是单线程）。
SCAN_COUNT = 500
UNLINK_BATCH = 200

#: 逻辑域 -> 该域覆盖的 key 前缀（都是 ``zvms:<前缀>...`` 的形态）。
SCOPE_PREFIXES: dict[str, tuple[str, ...]] = {
    "users": ("user", "auth", "names", "class"),
    "groups": ("group", "pgroup", "glist", "pglist"),
    "activities": ("activity", "activities"),
    "conversations": ("conv", "convlatest"),
    "notifications": ("notif",),
    "changelog": ("changelog",),
    "settings": ("cfg",),
    "roles": ("role",),
}

_client: Any = None
_client_uri: str | None = None
_client_lock: asyncio.Lock | None = None
_degraded_until = 0.0
_forced_client: Any = None
#: 连接池上限。默认 ``max_connections`` 是 2**31——每个并发请求都能开一条新连接，
#: 单 worker 下 Redis 端会被打进 ``maxclients``，然后所有请求一起超时降级。
#: 32 远大于本进程的并发 SQL 数（池只有 8+8），够用且给 Redis 一个上界。
MAX_CONNECTIONS = 32
#: 空闲连接健康检查间隔（秒）：``health_check_interval=0``（默认）时，被 Redis
#: 端掐掉的空闲连接要等第一次真正读写才暴露。30s 顺手做一次 PING。
HEALTH_CHECK_SECONDS = 30

#: 异步回填尚未落地的 value（key -> JSON payload）。见 :func:`remember` 的
#: ``async_backfill``：同一个事件循环里「刚读过的 key 立刻再读」不该因为回填
#: 还没写进 Redis 而再打一次数据库。
_pending: dict[str, str] = {}
#: 挂在事件循环上的回填任务集合（持有强引用，避免任务被 GC 掉）。
_backfills: set[asyncio.Task] = set()
#: 同 key 并发未命中的裁决表：``key -> (leader 的事件循环, Future)``。
_inflight: dict[str, tuple[asyncio.AbstractEventLoop, "asyncio.Future[Any]"]] = {}


def _uri() -> str:
    try:
        import settings  # 延迟导入：测试/工具脚本里 settings 可能不存在

        return str(getattr(settings, "REDIS_URI", "") or "").strip()
    except Exception:  # pragma: no cover
        return ""


def redis_configured() -> bool:
    """是否配置了 Redis（与「此刻连得上」是两回事）。"""
    return _forced_client is not None or (_aioredis is not None and bool(_uri()))


def note_failure() -> None:
    """供 :mod:`db.state` 复用：外部 Redis 命令失败时进入降级窗口。"""
    _mark_degraded()


def _mark_degraded() -> None:
    global _degraded_until
    _degraded_until = time.monotonic() + DEGRADE_SECONDS


async def _get_client() -> Any:
    """懒初始化客户端；不可用时返回 ``None``（调用方直接回源）。"""
    global _client, _client_uri, _client_lock

    if _forced_client is not None:
        return _forced_client
    if _aioredis is None:
        return None
    uri = _uri()
    if not uri:
        return None
    if time.monotonic() < _degraded_until:
        return None
    if _client is not None and _client_uri == uri:
        return _client
    if _client_lock is None:
        _client_lock = asyncio.Lock()
    async with _client_lock:
        # 锁外那次检查可能已过期：Redis 刚宕机时一批请求会排在锁上，
        # 不复查就会逐个等满 0.5s 的连接超时。进锁后以最新状态为准。
        if time.monotonic() < _degraded_until:
            return None
        if _client is not None and _client_uri == uri:
            return _client
        client: Any = None
        try:
            client = _aioredis.from_url(
                uri,
                decode_responses=True,
                socket_connect_timeout=CONNECT_TIMEOUT_SECONDS,
                socket_timeout=CONNECT_TIMEOUT_SECONDS,
                max_connections=MAX_CONNECTIONS,
                health_check_interval=HEALTH_CHECK_SECONDS,
            )
            await client.ping()
        except Exception:  # noqa: BLE001 - 连接失败只降级，不冒泡
            if client is not None:
                # ping 失败的 client 没人会再碰它，交给 GC 会留下未关闭的连接池。
                try:
                    await client.aclose()
                except Exception:  # noqa: BLE001
                    pass
            _mark_degraded()
            _logger.warning("redis unavailable, cache disabled for %.0fs", DEGRADE_SECONDS)
            return None
        _client, _client_uri = client, uri
        return client


def _encode(value: Any) -> Any:
    if isinstance(value, datetime):
        return {"$dt": value.isoformat()}
    if isinstance(value, date):
        return {"$d": value.isoformat()}
    if isinstance(value, Decimal):
        return {"$dec": str(value)}
    if isinstance(value, dict):
        return {str(k): _encode(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_encode(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)  # 未知类型降级为字符串，保证可序列化


def _decode(value: Any) -> Any:
    if isinstance(value, dict):
        if len(value) == 1:
            try:
                if "$dt" in value:
                    return datetime.fromisoformat(value["$dt"])
                if "$d" in value:
                    return date.fromisoformat(value["$d"])
                if "$dec" in value:
                    return Decimal(value["$dec"])
            except (TypeError, ValueError):
                pass  # 不是我们写的标签，当普通字典
        return {key: _decode(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_decode(item) for item in value]
    return value


async def _write_backfill(client: Any, key: str, payload: str, ttl: float) -> None:
    """落地一条回填；失败只降级，不冒泡（回填失败本来就只是少一次缓存）。"""
    try:
        await client.set(key, payload, px=max(1, int(ttl * 1000)))
    except Exception:  # noqa: BLE001
        _mark_degraded()
    finally:
        _pending.pop(key, None)


def _spawn_backfill(client: Any, key: str, payload: str, ttl: float) -> None:
    """把回填写挂到事件循环上，**不等待**。

    Redis 抖动（或单次写阻塞）不该被算进业务请求的响应时间里：读路径本来就是
    「读不到就回源」，晚几百毫秒回填只影响下一次读的命中率。没有运行中的循环
    （同步调用方 / 测试收尾）时直接放弃回填。
    """
    try:
        task = asyncio.create_task(_write_backfill(client, key, payload, ttl))
    except RuntimeError:  # pragma: no cover - 没有事件循环
        _pending.pop(key, None)
        return
    _backfills.add(task)
    task.add_done_callback(_backfills.discard)


async def _load_once(
    key: str, loader: Callable[[], Awaitable[Any]]
) -> tuple[Any, bool]:
    """同一 key 的并发未命中只跑一次 ``loader``（进程内 singleflight）。

    返回 ``(值, 是否由本次回源)``：只有领导者负责回填，跟随者拿同一份结果
    直接返回，不重复 ``SET``，也不重复排队回填。

    领导者登记 ``(当前循环, Future)`` 并亲自回源；同一循环上的跟随者等这个
    Future。循环不同就各自回源——等一个挂在别人循环上的 Future 只会卡死。
    领导者失败/取消先把落脚点定在 Future 上再清理登记表，跟随者拿到同一份
    异常，不会有人等一个永远不完成的 Future。
    """
    loop = asyncio.get_running_loop()
    entry = _inflight.get(key)
    if entry is not None and entry[0] is loop:
        return await asyncio.shield(entry[1]), False

    future: asyncio.Future = loop.create_future()
    _inflight[key] = (loop, future)
    try:
        value = await loader()
    except BaseException as exc:  # noqa: BLE001 - 异常原样广播给所有等待者
        if not future.done():
            if isinstance(exc, asyncio.CancelledError):
                future.cancel()
            else:
                future.set_exception(exc)
                future.exception()
        raise
    else:
        if not future.done():
            future.set_result(value)
        return value, True
    finally:
        current = _inflight.get(key)
        if current is not None and current[1] is future:
            _inflight.pop(key, None)


async def remember(
    key: str,
    ttl: float,
    loader: Callable[[], Awaitable[Any]],
    *,
    max_bytes: int = MAX_VALUE_BYTES,
    async_backfill: bool = False,
) -> Any:
    """读穿：命中直接返回，未命中执行 ``loader`` 并回填。

    ``loader`` 返回 ``None`` 时不缓存（「不存在」也可能是负缓存陷阱：
    用户刚被创建就立刻登录，30s 的负缓存会让新账号认证失败）。

    ``async_backfill=True``：回填写 fire-and-forget（不占响应时间）。此时同一
    事件循环里紧接着的第二次读可能还没看到 Redis 里的值，因此 ``_pending`` 记一份
    在途 payload 当作命中——否则「一次请求里读两遍同一个 key」会打两次数据库。
    默认 ``False``：写落库后才返回，语义与历史完全一致（缓存层自己的测试依赖
    这个同步点）。

    同 key 的并发未命中在进程内合并（见 :func:`_load_once`）：只有一个调用者
    跑 ``loader``，其余等同一次结果；异常对该 key 的所有等待者一致。
    """
    client = await _get_client()
    if client is None:
        return await loader()
    try:
        raw = await client.get(key)
    except Exception:  # noqa: BLE001
        _mark_degraded()
        return await loader()
    if raw is None:
        raw = _pending.get(key)
    if raw is not None:
        try:
            return _decode(json.loads(raw))
        except Exception:  # noqa: BLE001 - 脏值当作未命中，别让旧格式毒死路径
            pass
    value, owned = await _load_once(key, loader)
    if value is not None and owned:
        try:
            payload = json.dumps(
                _encode(value), ensure_ascii=False, separators=(",", ":"), default=str
            )
        except Exception:  # noqa: BLE001
            return value
        if len(payload.encode("utf-8")) <= max_bytes:
            if async_backfill:
                _pending[key] = payload
                _spawn_backfill(client, key, payload, ttl)
            else:
                try:
                    await client.set(key, payload, px=max(1, int(ttl * 1000)))
                except Exception:  # noqa: BLE001
                    _mark_degraded()
    return value


async def forget(*keys: str) -> None:
    client = await _get_client()
    if not keys:
        return
    for key in keys:
        _pending.pop(key, None)
    if client is None:
        return
    try:
        await client.delete(*keys)
    except Exception:  # noqa: BLE001
        _mark_degraded()


async def _read_epoch(scope: str) -> int | None:
    """读 epoch；``None`` = Redis 读不到（与「版本就是 0」区分开）。"""
    client = await _get_client()
    if client is None:
        return None
    try:
        raw = await client.get(f"{NS}:epoch:{scope}")
    except Exception:  # noqa: BLE001
        _mark_degraded()
        return None
    try:
        return int(raw or 0)
    except (TypeError, ValueError):
        return 0


async def epoch(scope: str) -> int:
    """读取某个 scope 的版本号；不可用时返回 0（相当于「无缓存」键空间）。"""
    value = await _read_epoch(scope)
    return 0 if value is None else value


async def bump_epoch(scope: str) -> None:
    """整类失效 + 版本号自增。

    ``INCR`` 与 ``EXPIRE`` 放进同一个 ``MULTI/EXEC``：两条命令之间一旦断连，
    就会留下一个没有 TTL 的永久 epoch key。客户端没有 ``pipeline``（测试替身）
    时退回顺序两条命令，Redis 不可用时的降级语义不变。
    """
    client = await _get_client()
    if client is None:
        return
    key = f"{NS}:epoch:{scope}"
    try:
        pipeline = getattr(client, "pipeline", None)
        if callable(pipeline):
            async with pipeline(transaction=True) as pipe:
                pipe.incr(key)
                pipe.expire(key, EPOCH_TTL_SECONDS)
                await pipe.execute()
        else:
            await client.incr(key)
            await client.expire(key, EPOCH_TTL_SECONDS)
    except Exception:  # noqa: BLE001
        _mark_degraded()


async def version(scope: str) -> int:
    """配置/数据版本号（= :func:`epoch`）。

    供上层做「别的进程改过没有」的廉价判定：版本没变就不必回源数据库。
    """
    return await epoch(scope)


async def version_or_none(scope: str) -> int | None:
    """同 :func:`version`，但「Redis 读不到」返回 ``None`` 而不是 0。

    版本闸必须能区分「版本就是 0」（Redis 活着、没人写过）与「读不到」
    （Redis 挂了 / 在降级窗口）：后者要退回每次读库，前者才可以跳过。
    """
    return await _read_epoch(scope)


async def _drop_keys(client: Any, keys: Sequence[str]) -> int:
    """批量删除；``UNLINK`` 不可用（老版本 / 测试替身）时退回 ``DELETE``。"""
    if not keys:
        return 0
    unlink = getattr(client, "unlink", None)
    try:
        if unlink is not None:
            return int(await unlink(*keys))
        return int(await client.delete(*keys))
    except AttributeError:  # 替身只实现了 delete
        return int(await client.delete(*keys))


async def flush_prefix(prefix: str) -> int:
    """删除所有以 ``zvms:<prefix>`` 开头的 key，返回删除数。

    用 ``SCAN`` 而不是 ``KEYS``：``KEYS`` 在几十万 key 时会把 Redis 单线程
    卡住，是标准的线上事故写法。
    """
    client = await _get_client()
    if client is None:
        return 0
    pattern = f"{NS}:{prefix}*"
    removed = 0
    batch: list[str] = []
    try:
        async for key in client.scan_iter(match=pattern, count=SCAN_COUNT):
            batch.append(key)
            if len(batch) >= UNLINK_BATCH:
                removed += await _drop_keys(client, batch)
                batch = []
        removed += await _drop_keys(client, batch)
    except Exception:  # noqa: BLE001 - 失效失败只降级，不回滚业务写
        _mark_degraded()
    return removed


async def flush_scopes(scopes: Sequence[str]) -> dict[str, int]:
    """按逻辑域失效；返回 ``{scope: 删除的 key 数}``（含 epoch 自增）。

    未知 scope 直接抛 ``ValueError``（在校验阶段就抛，一个 key 都不删），
    调用方负责翻成 400。
    """
    wanted = list(dict.fromkeys(scopes))  # 去重且保序（同一域只失效一次）
    unknown = [str(scope) for scope in wanted if scope not in SCOPE_PREFIXES]
    if unknown:
        raise ValueError(f"未知缓存域：{', '.join(unknown)}")

    result: dict[str, int] = {}
    for scope in wanted:
        removed = 0
        for prefix in SCOPE_PREFIXES[scope]:
            removed += await flush_prefix(prefix)
        await bump_epoch(scope)
        result[scope] = removed
    return result


async def aclose() -> None:
    global _client, _client_uri
    client, _client, _client_uri = _client, None, None
    if client is None:
        return
    try:
        await client.aclose()
    except Exception:  # noqa: BLE001
        pass


def _install_client_for_tests(client: Any) -> None:
    """测试注入：绕过 ``REDIS_URI`` 检查，直接用给定客户端。"""
    global _forced_client, _degraded_until
    _forced_client = client
    _degraded_until = 0.0


def _reset_for_tests() -> None:
    global _forced_client, _degraded_until
    _forced_client = None
    _degraded_until = 0.0
    _pending.clear()
    _inflight.clear()
