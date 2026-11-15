"""Socket.IO 服务端实例与 CONNECT / DISCONNECT 事件。

``realtime`` 需要 ``AsyncServer`` 实例来断开所有实时连接，但又不能反向 import
``main``（会成环），所以实例登记走 ``realtime.register_server``。
``app.mount("/socket.io", socket)`` 留在 ``main``：挂载点与路由表顺序相关。
"""

from __future__ import annotations

import logging

import socketio
from fastapi import HTTPException
from socketio.exceptions import ConnectionRefusedError as SocketIOConnectionRefusedError

import bindings
import realtime
from db.system_state import is_enabled_fresh_async as maintenance_enabled_fresh
from util.object_id import _authenticate, is_admin_token

_logger = logging.getLogger(__name__)

sio = socketio.AsyncServer(async_mode="asgi")
socket = socketio.ASGIApp(sio)

# 登记给 routers 用（开启维护模式时要断开所有实时连接）。
realtime.register_server(sio)


@sio.event
async def connect(sid, environ, auth=None):
    # 兜底：正常路径上 MaintenanceSocketGuard 已在握手阶段拒掉新连接（并且
    # GET /api/system/maintenance 已放行），这里再查一次**绕过缓存**的最新状态，
    # 万一有入口绕过那层中间件也不会漏进连接。admin 在维护期间照常连接。
    token = realtime.connect_token(environ, auth)
    if await bindings.get(
        "maintenance_enabled_fresh", maintenance_enabled_fresh
    )() and not await is_admin_token(token):
        _logger.warning("refuse socket connect %s (maintenance)", sid)
        raise SocketIOConnectionRefusedError("系统维护中，请稍后再试")
    if token:
        try:
            await _authenticate(token, "long", True)
        except HTTPException as exc:
            _logger.warning(
                "refuse socket connect %s (auth rejected: %s)", sid, exc.status_code
            )
            return False
        except Exception:
            _logger.exception("refuse socket connect %s (auth unavailable)", sid)
            return False
    else:
        # 实时通道原先对匿名连接敞开：任何人建连就能订阅广播事件。项目内目前
        # 没有按连接推送的内容，但连接面本身不该有匿名免检入口。
        _logger.warning("refuse socket connect %s (no token)", sid)
        raise SocketIOConnectionRefusedError("请先登录后再建立实时连接")
    _logger.info("connect %s", sid)


@sio.event
async def disconnect(sid):
    _logger.info("disconnect %s", sid)
