"""Web SSH 会话桥：交互式 PTY 的建立、泵、配额与一次性 ticket。

分层原因：路由只处理 WebSocket 帧，本模块承担与 sshd 打交道的全部细节，
并把「连接」抽成可注入的工厂，测试用替身连接就能覆盖泵、分片、限制逻辑，
不需要真 sshd，也不会让私钥路径进入测试。

目标（target）是**服务端配置的命名集合**：网关部署在 API 服务器上，前端只能
传一个 ``id``，host/port/user/密钥路径全部来自本模块解析的配置。客户端参数
因此不参与连接决策，「模型/浏览器能不能连别处」在类型层面就不存在。

单 worker 前提：ticket 与并发计数都在进程内存里（``util/dual_auth`` 的失败限流
已迁到 Redis，但 ticket 仍需共享存储）。多 worker 部署时每个进程各有一份，
ticket 会在别的 worker 上失效，必须先换成共享存储。
"""

from __future__ import annotations

import codecs
import json
import logging
import os
import re
import secrets
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

import asyncssh

import settings
from util.env import getenv

logger = logging.getLogger(__name__)

# 终端硬约束（契约 §2.4）：输出分片上限、单条输入上限。
MAX_OUTPUT_CHUNK_BYTES = 32 * 1024
MAX_INPUT_BYTES = 8 * 1024

# 空闲按「客户端没有发任何帧」计：终端长时间无输出但用户还在看，不算空闲。
IDLE_TIMEOUT_SECONDS = 15 * 60

# ticket 只需撑过「POST 拿到 ticket → 前端连 WS」这段窗口。
TICKET_TTL_SECONDS = 120

MAX_SESSIONS_PER_OPERATOR = 2
MAX_SESSIONS_TOTAL = 4

# WS 关闭码：4401 = 未授权，4408 = 空闲超时，4429 = 并发超限，4500 = 后端故障。
WS_CLOSE_UNAUTHORIZED = 4401
WS_CLOSE_IDLE = 4408
WS_CLOSE_LIMIT = 4429
WS_CLOSE_FAILURE = 4500

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 22
DEFAULT_USER = "root"
DEFAULT_KEY_PATH = "/www/zvms4/backend/ssh_key"
DEFAULT_KNOWN_HOSTS = "/www/zvms4/backend/ssh_known_hosts"

#: 目标 id 只能是小写标识符：它会进 ticket、审计 detail 和前端路由参数，
#: 收紧字符集就不必在那些地方再做转义。最长 32 字符。
TARGET_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")

#: 老部署（没有配置任何 target）退化成的单目标。
LEGACY_TARGET_ID = "self"
LEGACY_TARGET_LABEL = "本机"

# 初始尺寸只是占位：前端 xterm.js 挂载后会立刻发 resize。
DEFAULT_COLS = 80
DEFAULT_ROWS = 24


@dataclass(frozen=True)
class SshTarget:
    """一个可连接的命名目标；``display`` 只给界面看，不参与连接。"""

    id: str
    label: str
    host: str
    port: int
    user: str
    key_path: str
    known_hosts: str

    @property
    def display(self) -> str:
        """``user@host``；非默认端口追加 ``:port``（默认端口不写，避免噪音）。"""
        if self.port and self.port != DEFAULT_PORT:
            return f"{self.user}@{self.host}:{self.port}"
        return f"{self.user}@{self.host}"


def _resolve(env_name: str, field_name: str, default: str) -> str:
    """env → settings → 默认值。

    settings.py 里可能压根没有这些字段（生产上目前就没有），所以只能用
    ``getattr`` + 默认值；字段名带不带 ``ZVMS_`` 前缀两种写法都认，避免部署时
    因为命名差异静默连到错误目标。
    """
    value = os.environ.get(env_name)
    if value:
        return value
    for name in (env_name, field_name):
        value = getattr(settings, name, None)
        if value not in (None, ""):
            return str(value)
    return default


def _default_port() -> int:
    raw_port = _resolve("ZVMS_SSH_PORT", "SSH_PORT", str(DEFAULT_PORT))
    try:
        return int(raw_port)
    except (TypeError, ValueError):
        return DEFAULT_PORT


def _item_text(item: dict, name: str) -> str:
    value = item.get(name)
    return value.strip() if isinstance(value, str) else ""


def _build_target(item: object, index: int) -> SshTarget | None:
    """把一条配置项变成 :class:`SshTarget`；不合法返回 ``None``。

    warning 只写序号和固定的原因码：配置项里可能有密钥路径/known_hosts 路径，
    日志一旦带上原文就等于把机密复制到第二处。id 在通过正则后才可能出现在
    日志里（重复 id 的排查价值大于泄露风险，且它本身不是机密）。
    """

    def drop(reason: str) -> None:
        logger.warning("ssh target #%d dropped: %s", index, reason)

    if not isinstance(item, dict):
        drop("not an object")
        return None

    raw_id = item.get("id")
    if not isinstance(raw_id, str) or not TARGET_ID_PATTERN.fullmatch(raw_id):
        drop("invalid id")
        return None

    host = _item_text(item, "host")
    if not host:
        drop("missing host")
        return None

    raw_port = item.get("port")
    if raw_port in (None, ""):
        port = _default_port()
    else:
        try:
            port = int(raw_port)
        except (TypeError, ValueError):
            drop("invalid port")
            return None
        if not 0 < port < 65536:
            drop("invalid port")
            return None

    return SshTarget(
        id=raw_id,
        label=_item_text(item, "label") or host,
        host=host,
        port=port,
        # 缺省沿用老部署的 env/settings/默认值：只配 host 也能直接可用。
        user=_item_text(item, "user")
        or _resolve("ZVMS_SSH_USER", "SSH_USER", DEFAULT_USER),
        key_path=_item_text(item, "key_path")
        or _resolve("ZVMS_SSH_KEY_PATH", "SSH_KEY_PATH", DEFAULT_KEY_PATH),
        known_hosts=_item_text(item, "known_hosts")
        or _resolve(
            "ZVMS_SSH_KNOWN_HOSTS", "SSH_KNOWN_HOSTS", DEFAULT_KNOWN_HOSTS
        ),
    )


def _target_items() -> list | None:
    """读原始配置：env 优先，其次 ``settings.SSH_TARGETS``（list 或 JSON 串）。

    ``None`` 表示「完全没配置」，交给 :func:`load_targets` 走单目标退化。
    """

    raw: object = getenv("ZVP5_SSH_TARGETS")
    source = "ZVP5_SSH_TARGETS"
    if not raw:
        raw = getattr(settings, "SSH_TARGETS", None)
        source = "settings.SSH_TARGETS"
    if not raw:
        return None

    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            logger.warning(
                "ssh targets config invalid JSON (%s); using legacy target", source
            )
            return None
    if not isinstance(raw, list):
        logger.warning(
            "ssh targets config is not a list (%s); using legacy target", source
        )
        return None
    return raw


def _legacy_target() -> SshTarget:
    """老部署的单目标：参数仍走 ``ZVMS_SSH_*``/默认值，零改动可用。"""
    return SshTarget(
        id=LEGACY_TARGET_ID,
        label=LEGACY_TARGET_LABEL,
        host=_resolve("ZVMS_SSH_HOST", "SSH_HOST", DEFAULT_HOST),
        port=_default_port(),
        user=_resolve("ZVMS_SSH_USER", "SSH_USER", DEFAULT_USER),
        key_path=_resolve("ZVMS_SSH_KEY_PATH", "SSH_KEY_PATH", DEFAULT_KEY_PATH),
        known_hosts=_resolve(
            "ZVMS_SSH_KNOWN_HOSTS", "SSH_KNOWN_HOSTS", DEFAULT_KNOWN_HOSTS
        ),
    )


def load_targets() -> list[SshTarget]:
    """解析全部可用目标。**保证非空**：没有有效配置时退化为单目标。

    坏配置的语义是「丢弃坏项、留下好项」，全坏/空则退化为老的单目标——这样
    升级时漏配 ``ZVMS_SSH_TARGETS`` 不会让 Web SSH 直接不可用，而写错的一项
    也不会把别的目标一起带下线。
    """
    items = _target_items()
    if items is None:
        return [_legacy_target()]

    targets: list[SshTarget] = []
    seen: set[str] = set()
    for index, item in enumerate(items):
        target = _build_target(item, index)
        if target is None:
            continue
        if target.id in seen:
            logger.warning("ssh target #%d dropped: duplicate id %s", index, target.id)
            continue
        seen.add(target.id)
        targets.append(target)
    if not targets:
        logger.warning("no valid ssh targets; using legacy target")
        return [_legacy_target()]
    return targets


def get_target(target_id: str | None) -> SshTarget | None:
    """按 id 取目标；未知/空 id 返回 ``None``（调用方据此回笼统错误）。"""
    if not isinstance(target_id, str) or not target_id:
        return None
    for target in load_targets():
        if target.id == target_id:
            return target
    return None


def _default_target_id() -> str:
    """默认目标 id：配置的默认值（若仍存在）→ 否则第一个。"""
    configured = _resolve(
        "ZVMS_SSH_DEFAULT_TARGET", "SSH_DEFAULT_TARGET", ""
    ).strip()
    targets = load_targets()
    if configured:
        for target in targets:
            if target.id == configured:
                return configured
        logger.warning(
            "ssh default target %s not configured; falling back to %s",
            configured,
            targets[0].id,
        )
    return targets[0].id


def load_target(target_id: str | None = None) -> SshTarget:
    """解析连接目标：指定 id（已知时）或默认目标。

    连接参数只来自服务端配置，客户端无法指定 host/port/user。未知 id 回落到
    默认目标：本函数的调用方（``connect`` / ``SshSession.open``）拿到的一定是
    一个可连接的目标；需要「未知即拒绝」的 HTTP 入口用 :func:`get_target`。
    """
    if target_id:
        target = get_target(target_id)
        if target is not None:
            return target
        logger.warning("ssh target %s not configured; using default", target_id)
    default_id = _default_target_id()
    target = get_target(default_id)
    if target is None:  # 理论上不可达：默认 id 来自同一份配置
        return _legacy_target()
    return target


ConnectFactory = Callable[[SshTarget], Awaitable[Any]]


async def default_connect(target: SshTarget) -> Any:
    """真实连接：密钥认证，只允许连配置里的目标。

    ``keepalive_interval`` 防止云厂商/NAT 把空闲 TCP 掐掉；终端自身的 15 分钟
    空闲关闭由路由层判定，两者互不干扰。
    """
    return await asyncssh.connect(
        target.host,
        port=target.port,
        username=target.user,
        client_keys=[target.key_path],
        known_hosts=target.known_hosts,
        login_timeout=15,
        keepalive_interval=30,
        # 保留原始字节：分片可能切在多字节字符中间，解码交给 SshSession 的
        # 增量解码器，避免在分片边界上出现乱码。
        encoding=None,
    )


_connect_factory: ConnectFactory = default_connect


def set_connect_factory(factory: ConnectFactory) -> ConnectFactory:
    """替换连接工厂（测试/演练用），返回旧工厂便于复原。"""
    global _connect_factory
    previous = _connect_factory
    _connect_factory = factory
    return previous


async def connect() -> Any:
    """给「单次命令」用的连接（agent 运维工具的 ``ssh_exec``）。

    目标**只**来自 :func:`load_target`：调用方没有参数可以指定主机，这样
    「模型能不能连别处」这个问题在类型层面就不存在。连接生命周期由调用方负责
    关闭；工厂仍可注入，测试不需要真 sshd。
    """
    return await _connect_factory(load_target())


class SshSession:
    """一条交互式终端：写入、改窗口、分片读取、退出码。"""

    def __init__(self, connection: Any, process: Any) -> None:
        self._connection = connection
        self._process = process
        self._decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self._closed = False

    @classmethod
    async def open(
        cls,
        cols: int = DEFAULT_COLS,
        rows: int = DEFAULT_ROWS,
        target: SshTarget | None = None,
    ) -> "SshSession":
        connection = await _connect_factory(target or load_target())
        try:
            process = await connection.create_process(
                term_type="xterm-256color",
                term_size=(cols, rows),
                encoding=None,
            )
        except BaseException:
            # PTY 没建起来也要回收连接，否则每次失败都漏一条 TCP + 一个 fd。
            _close_quietly(connection)
            raise
        return cls(connection, process)

    async def read_output(self) -> str | None:
        """读一片输出；``None`` 表示对端已关闭（已把残留字节冲出来）。"""
        data = await self._process.stdout.read(MAX_OUTPUT_CHUNK_BYTES)
        if not data:
            tail = self._decoder.decode(b"", True)
            return tail or None
        return self._decoder.decode(data)

    async def write(self, data: str) -> None:
        """写入键盘输入；``drain`` 是对端处理不过来时的背压点。"""
        self._process.stdin.write(data.encode("utf-8"))
        await self._process.stdin.drain()

    def resize(self, cols: int, rows: int) -> None:
        self._process.change_terminal_size(cols, rows)

    async def wait_exit(self) -> int:
        try:
            result = await self._process.wait()
        except (asyncssh.Error, OSError):
            return -1
        status = getattr(result, "exit_status", None)
        return -1 if status is None else int(status)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        _close_quietly(self._process)
        _close_quietly(self._connection)


def _close_quietly(obj: Any) -> None:
    """关闭失败不影响清理流程（对端可能已经先断了）。"""
    try:
        obj.close()
    except Exception:  # noqa: BLE001 - 清理路径不抛
        pass


class SessionRegistry:
    """并发配额。

    方法里没有 ``await``，计数在事件循环线程内读改是原子的，不需要锁。
    """

    def __init__(
        self,
        max_per_operator: int = MAX_SESSIONS_PER_OPERATOR,
        max_total: int = MAX_SESSIONS_TOTAL,
    ) -> None:
        self._max_per_operator = max_per_operator
        self._max_total = max_total
        self._total = 0
        self._per_operator: dict[str, int] = {}

    def acquire(self, operator_oid: str) -> bool:
        if self._total >= self._max_total:
            return False
        if self._per_operator.get(operator_oid, 0) >= self._max_per_operator:
            return False
        self._total += 1
        self._per_operator[operator_oid] = self._per_operator.get(operator_oid, 0) + 1
        return True

    def release(self, operator_oid: str) -> None:
        current = self._per_operator.get(operator_oid, 0)
        if current <= 1:
            self._per_operator.pop(operator_oid, None)
        else:
            self._per_operator[operator_oid] = current - 1
        if self._total > 0:
            self._total -= 1

    def snapshot(self) -> tuple[int, int]:
        return self._total, self._max_total


_registry = SessionRegistry()


def registry() -> SessionRegistry:
    return _registry


@dataclass(frozen=True)
class Ticket:
    operator_oid: str
    approver_oid: str
    expires_at: float
    target_id: str = ""


# ticket -> 授权信息。进程内、单次消费，见模块 docstring 的单 worker 前提。
_tickets: dict[str, Ticket] = {}


def issue_ticket(
    operator_oid: str,
    approver_oid: str = "",
    *,
    ttl_seconds: float | None = None,
    target_id: str | None = None,
) -> tuple[str, int]:
    """发放一次性 ticket。返回 ``(ticket, expires_in)``。

    ``target_id`` 绑进 ticket：WS 侧只认 ticket 里的目标，重连/改参数都换不掉
    本次授权连向哪里。缺省取默认目标 id（老调用方不必改）。

    用 ``monotonic`` 而不是墙上时间：系统时钟被校准时不会让 ticket 提前失效或
    永不过期。
    """
    ttl = TICKET_TTL_SECONDS if ttl_seconds is None else ttl_seconds
    ticket = secrets.token_urlsafe(32)
    _tickets[ticket] = Ticket(
        operator_oid=operator_oid,
        approver_oid=approver_oid,
        expires_at=time.monotonic() + ttl,
        target_id=target_id or load_target().id,
    )
    return ticket, max(0, int(ttl))


def consume_ticket(ticket: str) -> Ticket | None:
    """校验并**弹出** ticket：两次并发使用同一个 ticket，只有一个能拿到。"""
    now = time.monotonic()
    # 顺手清掉过期项，避免 dict 随失败尝试无界增长。
    for key in [k for k, v in _tickets.items() if v.expires_at <= now]:
        _tickets.pop(key, None)

    if not isinstance(ticket, str) or not ticket:
        return None
    entry = _tickets.pop(ticket, None)
    if entry is None or entry.expires_at <= now:
        return None
    return entry
