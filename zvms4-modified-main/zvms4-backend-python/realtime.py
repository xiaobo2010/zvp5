"""Socket.IO 实时连接的运行时注册表，以及「维护模式」准入 / 断连。

为什么单独一个模块：``routers.system_router`` 需要在开启维护模式后立刻断开
所有实时连接，而 socket.io 的 ``AsyncServer`` 实例定义在 ``main`` 里。让 router
反向 import ``main`` 会形成循环导入（main → routers → system_router），所以这里
放一个极薄的注册表：``main`` 在 import 时 ``register_server(sio)``，任何一侧都
通过本模块拿到同一个实例，本模块只依赖 ``db.system_state``，不反向依赖任何
上层模块。
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional
from urllib.parse import parse_qs

from db.system_state import is_enabled_fresh_async
from util.object_id import bearer_token, is_admin_token

_logger = logging.getLogger(__name__)

# nginx 把 ``/socket.io`` 反代到同一个后端进程，FastAPI 侧 ``main`` 用
# ``app.mount("/socket.io", socketio.ASGIApp(sio))`` 挂载。
SOCKET_PATH_PREFIX = "/socket.io"

# 握手阶段能看到的 token 来源只有 ``Authorization`` 头。query 传 token 会落进
# 反代 access log（``?token=...`` 被整行记下），因此一律不认；socket.io 客户端
# 该走 CONNECT 包的 ``auth`` 载荷（见 connect_token）。
_server: Any = None


def _token_from(headers) -> Optional[str]:
    """从 header 列表取 token；任何解析失败都返回 ``None``。"""
    try:
        for key, value in headers or []:
            if bytes(key).lower() == b"authorization":
                token = bearer_token(value)
                if token:
                    return token
    except Exception:
        return None
    return None


def handshake_token(scope) -> Optional[str]:
    """握手（Engine.IO open / upgrade）阶段的 token，取不到即 ``None``。"""
    return _token_from(scope.get("headers"))


def connect_token(environ, auth=None) -> Optional[str]:
    """CONNECT 包阶段的 token：优先 socket.io ``auth`` 载荷，其次 ``Authorization`` 头。"""
    try:
        if isinstance(auth, dict):
            for name in ("token", "access_token", "Authorization", "authorization"):
                token = bearer_token(auth.get(name))
                if token:
                    return token
        environ = environ or {}
        token = bearer_token(environ.get("HTTP_AUTHORIZATION"))
        if token:
            return token
    except Exception:
        return None
    return None


async def is_admin_conn(scope) -> bool:
    """握手 scope 是否属于 admin（token 无效 / 缺失一律 ``False``）。"""
    try:
        return await is_admin_token(handshake_token(scope))
    except Exception:
        return False


def register_server(server: Any) -> None:
    """登记全局唯一的 ``socketio.AsyncServer``（main 启动时调用一次）。"""
    global _server
    _server = server


def get_server() -> Any:
    """返回已登记的 server；未登记时返回 ``None``（例如只 import 本模块的测试）。"""
    return _server


async def disconnect_all_clients() -> int:
    """逐个 sid 优雅断开所有已连接的 socket.io 客户端，返回断开数量。

    只走 ``sio.disconnect``（给客户端发 socket.io DISCONNECT 包），刻意**不**调
    ``eio.disconnect``：后者内部 ``Socket.close(wait=True)`` 会
    ``await self.queue.join()``（engineio/async_socket.py:116-128），必须等客户端
    下一次轮询把队列消费掉才返回。对「已经掉线、但服务端 ping 超时前还没清理」
    的会话，这个等待没有上界，会让 ``POST /enable`` 卡住不返回。DISCONNECT 包
    入队后，长轮询客户端下一次 GET / websocket 读取会立刻拿到并自行关闭
    Engine.IO 连接（socket.io-client 收到服务端 DISCONNECT 不会自动重连）。
    """
    server = _server
    if server is None:
        return 0

    manager = server.manager
    disconnected = 0
    try:
        # 先快照：disconnect 会改动 manager.rooms（空房间 / 空命名空间被删掉）。
        for namespace in list(manager.rooms.keys()):
            for sid, _eio_sid in list(manager.get_participants(namespace, None)):
                try:
                    await server.disconnect(sid, namespace=namespace)
                    disconnected += 1
                except Exception as e:  # 单个连接失败不影响其余连接
                    _logger.exception(
                        "Failed to disconnect socket %s [%s]: %s", sid, namespace, e
                    )
    except Exception as e:
        # 断连失败绝不能让调用方（POST /enable）500：状态已经落库，维护模式已生效。
        _logger.exception(
            "disconnect_all_clients aborted after %s: %s", disconnected, e
        )
    return disconnected


class MaintenanceSocketGuard:
    """维护模式期间，在「握手阶段」拒掉新的 Engine.IO / Socket.IO 连接。

    这一层必须自己实现：socket.io 是 ``app.mount("/socket.io", ...)`` 的独立
    ASGI 子应用，``maintenance_middleware`` 只看 ``/api/`` 前缀，管不到它。

    纯 ASGI 中间件（http 与 websocket 两种 scope 都覆盖）：

    * http：握手请求直接 503，客户端拿不到 sid，连接从未建立；
    * websocket：accept 之前 close（ASGI 语义下服务端回 HTTP 403），不会建链。

    只拦「握手」（query 里没有 ``sid``）：带 sid 的请求属于已建立的会话，放行
    它们才能把 ``disconnect_all_clients()`` 入队的 DISCONNECT 包送到客户端手里
    —— 已连接的断开是 enable 那一刻主动做的，不在准入这一层。

    握手带有效 admin token 时放行：维护期间管理员要能照常连实时通道。token
    取不到或解析失败一律按原样拒绝（见 :func:`handshake_token`）。
    """

    def __init__(self, app: Any, path_prefix: str = SOCKET_PATH_PREFIX) -> None:
        self.app = app
        self.path_prefix = path_prefix.rstrip("/")

    async def __call__(self, scope, receive, send) -> None:
        if (
            scope["type"] in ("http", "websocket")
            and self._is_handshake(scope)
            and await is_enabled_fresh_async()  # 绕过 ~2s TTL 缓存，读最新状态
            and not await is_admin_conn(scope)
        ):
            await self._reject(scope, send)
            return
        await self.app(scope, receive, send)

    def _is_handshake(self, scope) -> bool:
        path = scope.get("path", "")
        if path != self.path_prefix and not path.startswith(self.path_prefix + "/"):
            return False
        # latin-1 解码不会抛异常；sid 存在即会话续传，非握手。
        query = parse_qs(scope.get("query_string", b"").decode("latin-1"))
        return "sid" not in query

    async def _reject(self, scope, send) -> None:
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1013})
            return

        body = json.dumps(
            {
                "detail": "系统维护中，请稍后再试",
                "maintenance": True,
                "socket": True,
            },
            ensure_ascii=False,
        ).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": 503,
                "headers": [
                    (b"content-type", b"application/json; charset=utf-8"),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
