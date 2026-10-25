"""Web SSH 会话桥：交互式 PTY 的建立、泵、配额与一次性 ticket。

分层原因：路由只处理 WebSocket 帧，本模块承担与 sshd 打交道的全部细节，
并把「连接」抽成可注入的工厂，测试用替身连接就能覆盖泵、分片、限制逻辑，
不需要真 sshd，也不会让私钥路径进入测试。

单 worker 前提：ticket 与并发计数都在进程内存里（与 ``util/dual_auth`` 的失败
限流同一前提）。多 worker 部署时每个进程各有一份，ticket 会在别的 worker 上
失效，必须先换成共享存储。
"""

from __future__ import annotations

import codecs
import os
import secrets
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

import asyncssh

import settings

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

# 初始尺寸只是占位：前端 xterm.js 挂载后会立刻发 resize。
DEFAULT_COLS = 80
DEFAULT_ROWS = 24


@dataclass(frozen=True)
class SshTarget:
    host: str
    port: int
    user: str
    key_path: str
    known_hosts: str


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


def load_target() -> SshTarget:
    """解析连接目标。连接参数只来自服务端配置，客户端无法指定。"""
    raw_port = _resolve("ZVMS_SSH_PORT", "SSH_PORT", str(DEFAULT_PORT))
    try:
        port = int(raw_port)
    except (TypeError, ValueError):
        port = DEFAULT_PORT
    return SshTarget(
        host=_resolve("ZVMS_SSH_HOST", "SSH_HOST", DEFAULT_HOST),
        port=port,
        user=_resolve("ZVMS_SSH_USER", "SSH_USER", DEFAULT_USER),
        key_path=_resolve("ZVMS_SSH_KEY_PATH", "SSH_KEY_PATH", DEFAULT_KEY_PATH),
        known_hosts=_resolve(
            "ZVMS_SSH_KNOWN_HOSTS", "SSH_KNOWN_HOSTS", DEFAULT_KNOWN_HOSTS
        ),
    )


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
        cls, cols: int = DEFAULT_COLS, rows: int = DEFAULT_ROWS
    ) -> "SshSession":
        connection = await _connect_factory(load_target())
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


# ticket -> 授权信息。进程内、单次消费，见模块 docstring 的单 worker 前提。
_tickets: dict[str, Ticket] = {}


def issue_ticket(
    operator_oid: str,
    approver_oid: str = "",
    *,
    ttl_seconds: float | None = None,
) -> tuple[str, int]:
    """发放一次性 ticket。返回 ``(ticket, expires_in)``。

    用 ``monotonic`` 而不是墙上时间：系统时钟被校准时不会让 ticket 提前失效或
    永不过期。
    """
    ttl = TICKET_TTL_SECONDS if ttl_seconds is None else ttl_seconds
    ticket = secrets.token_urlsafe(32)
    _tickets[ticket] = Ticket(
        operator_oid=operator_oid,
        approver_oid=approver_oid,
        expires_at=time.monotonic() + ttl,
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
