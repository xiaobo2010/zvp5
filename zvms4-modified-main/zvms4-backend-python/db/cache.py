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
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Awaitable, Callable

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

_client: Any = None
_client_uri: str | None = None
_client_lock: asyncio.Lock | None = None
_degraded_until = 0.0
_forced_client: Any = None


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
        if _client is not None and _client_uri == uri:
            return _client
        try:
            client = _aioredis.from_url(
                uri,
                decode_responses=True,
                socket_connect_timeout=CONNECT_TIMEOUT_SECONDS,
                socket_timeout=CONNECT_TIMEOUT_SECONDS,
            )
            await client.ping()
        except Exception:  # noqa: BLE001 - 连接失败只降级，不冒泡
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


async def remember(
    key: str,
    ttl: float,
    loader: Callable[[], Awaitable[Any]],
    *,
    max_bytes: int = MAX_VALUE_BYTES,
) -> Any:
    """读穿：命中直接返回，未命中执行 ``loader`` 并回填。

    ``loader`` 返回 ``None`` 时不缓存（「不存在」也可能是负缓存陷阱：
    用户刚被创建就立刻登录，30s 的负缓存会让新账号认证失败）。
    """
    client = await _get_client()
    if client is None:
        return await loader()
    try:
        raw = await client.get(key)
    except Exception:  # noqa: BLE001
        _mark_degraded()
        return await loader()
    if raw is not None:
        try:
            return _decode(json.loads(raw))
        except Exception:  # noqa: BLE001 - 脏值当作未命中，别让旧格式毒死路径
            pass
    value = await loader()
    if value is not None:
        try:
            payload = json.dumps(
                _encode(value), ensure_ascii=False, separators=(",", ":"), default=str
            )
        except Exception:  # noqa: BLE001
            return value
        if len(payload.encode("utf-8")) <= max_bytes:
            try:
                await client.set(key, payload, px=max(1, int(ttl * 1000)))
            except Exception:  # noqa: BLE001
                _mark_degraded()
    return value


async def forget(*keys: str) -> None:
    client = await _get_client()
    if client is None or not keys:
        return
    try:
        await client.delete(*keys)
    except Exception:  # noqa: BLE001
        _mark_degraded()


async def epoch(scope: str) -> int:
    """读取某个 scope 的版本号；不可用时返回 0（相当于「无缓存」键空间）。"""
    client = await _get_client()
    if client is None:
        return 0
    try:
        raw = await client.get(f"{NS}:epoch:{scope}")
    except Exception:  # noqa: BLE001
        _mark_degraded()
        return 0
    try:
        return int(raw or 0)
    except (TypeError, ValueError):
        return 0


async def bump_epoch(scope: str) -> None:
    client = await _get_client()
    if client is None:
        return
    try:
        await client.incr(f"{NS}:epoch:{scope}")
    except Exception:  # noqa: BLE001
        _mark_degraded()


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
