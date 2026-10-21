"""Web SSH 终端端点。

``POST /session`` 先过「严格 admin + 双人 TOTP」，发一张一次性 ticket；
``WS /ws`` 用 ticket 建终端。浏览器发起 WebSocket 握手时带不了
``Authorization`` 头，所以 WS 侧只认 ticket —— ticket 的发放已经过完整 HTTP
鉴权，且 120 秒内单次有效。

连接目标固定来自服务端配置（``services/ssh_bridge.py``），本文件不接受客户端
指定 host/port/user。
"""

from __future__ import annotations

import asyncio
import json

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    WebSocket,
    WebSocketDisconnect,
)
from pydantic import BaseModel

from db import backends
from services import ssh_bridge
from services.ssh_command_log import CommandLineBuffer
from typings.log import ZVMSLog
from util.dual_auth import (
    DualAuthError,
    audit_system_op,
    require_dual_auth,
    resolve_names,
)
from util.ban import ban_is_active, raise_banned
from util.group import get_user_permissions
from util.object_id import get_current_user

router = APIRouter()

# 终端尺寸上限：只防明显异常的数值把 PTY 撑坏，正常 xterm 在几百以内。
MAX_TERM_DIMENSION = 1000


class SessionRequest(BaseModel):
    # 长度/字符集留给守卫判：它要区分「格式非法 422」与「码不对 403」，
    # 在这里用 Field 提前拦截会把两种错误混成同一条 Pydantic 报错。
    totp_code: str


async def _require_system_admin(user: dict) -> dict:
    """严格 admin + 即时查库复核（与 ``routers/system_router.py`` 同一条闸）。"""
    if "admin" not in (user.get("per") or []):
        raise HTTPException(status_code=403, detail="权限不足")

    me = await backends.get_users_repo().get_by_oid(user["id"])
    if me is None:
        raise HTTPException(status_code=403, detail="权限不足")
    # 到期封禁会保留 banned=1，只有 ban_is_active 才能反映「此刻是否可放行」。
    if ban_is_active(me):
        raise_banned(me)
    if "admin" not in (await get_user_permissions(me)):
        raise HTTPException(status_code=403, detail="权限不足")

    return me


@router.post("/session")
async def create_ssh_session(
    payload: SessionRequest,
    user=Depends(get_current_user),
):
    """校验双人码后发放 ticket；ticket 只能消费一次。"""
    me = await _require_system_admin(user)
    try:
        approver_oid = await require_dual_auth(me, payload.totp_code, action="web_ssh")
    except DualAuthError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    operator_oid = str(me.get("_id") or user["id"])
    ticket, expires_in = ssh_bridge.issue_ticket(operator_oid, approver_oid)
    return {
        "status": "ok",
        "code": 200,
        "data": {"ticket": ticket, "expires_in": expires_in},
    }


async def _send(websocket: WebSocket, payload: dict) -> None:
    await websocket.send_json(payload)


async def _send_quietly(websocket: WebSocket, payload: dict) -> None:
    try:
        await websocket.send_json(payload)
    except (RuntimeError, WebSocketDisconnect):
        pass


async def _close(websocket: WebSocket, code: int, reason: str = "") -> None:
    try:
        await websocket.close(code=code, reason=reason)
    except (RuntimeError, WebSocketDisconnect):
        # 客户端可能已经先断了：关闭码送不到，但清理仍要继续。
        pass


def _valid_size(value) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and 1 <= value <= MAX_TERM_DIMENSION
    )


async def _pump_output(websocket: WebSocket, session: ssh_bridge.SshSession) -> None:
    """SSH → WS：分片下发，进程退出时补一个 exit 帧再收尾。"""
    try:
        while True:
            chunk = await session.read_output()
            if chunk is None:
                code = await session.wait_exit()
                await _send(websocket, {"type": "exit", "code": code})
                await _close(websocket, 1000, "process exited")
                return
            if chunk:
                # 分片本身由 bridge 限到 32KB；这里不再攒包，终端要的是低延迟。
                await _send(websocket, {"type": "output", "data": chunk})
    except (RuntimeError, WebSocketDisconnect):
        return
    except Exception as exc:  # noqa: BLE001 - 泵异常必须转成明确 error 帧
        print(f"web ssh output pump failed: {type(exc).__name__}")
        await _send_quietly(websocket, {"type": "error", "detail": "终端连接已断开"})
        await _close(websocket, ssh_bridge.WS_CLOSE_FAILURE, "终端连接已断开")


async def _write_command_audit(
    operator_oid: str, operator_name: str, command: str
) -> None:
    """落一条「执行过的命令」。

    命令原文会进库，可能包含用户在终端里敲的密码等敏感值：这是运维审计的
    既定要求（目标机侧的 shell 历史同样保存明文），这里**不做脱敏**，否则
    ``mysql -p***`` 这种记录既不能复核也不能重现。写失败只打印，绝不影响
    会话本身——审计是旁路，不能成为终端的单点故障。
    """
    try:
        await ZVMSLog(
            url="system:web_ssh",
            user=operator_oid,
            data=json.dumps(
                {
                    "command": command,
                    "operator": operator_oid,
                    "operator_name": operator_name,
                },
                ensure_ascii=False,
            ),
            type="system_ssh_command",
            level="info",
        ).insert_log()
    except Exception as exc:  # noqa: BLE001 - 审计失败不改变会话结论
        print(f"Failed to write ssh command log: {exc}")


def _spawn_command_audit(
    pending: set[asyncio.Task],
    operator_oid: str,
    operator_name: str,
    command: str,
) -> None:
    """异步写审计。

    不能 ``await``：一次粘贴几十行会把写库延迟叠加到终端输入上，交互感立刻
    崩掉。任务放进 ``pending`` 集合防止被 GC 回收（``asyncio`` 只持弱引用），
    会话结束时由调用方 ``gather`` 收尾，保证「断开前敲的命令」不会丢。
    """
    task = asyncio.create_task(
        _write_command_audit(operator_oid, operator_name, command)
    )
    pending.add(task)
    task.add_done_callback(pending.discard)


async def _pump_input(
    websocket: WebSocket,
    session: ssh_bridge.SshSession,
    operator_oid: str = "",
    operator_name: str = "",
    pending: set[asyncio.Task] | None = None,
) -> str:
    """WS → SSH；返回关闭原因（只用于审计）。"""
    buffer = CommandLineBuffer()
    pending = pending if pending is not None else set()
    while True:
        try:
            message = await asyncio.wait_for(
                websocket.receive_json(),
                timeout=ssh_bridge.IDLE_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            await _send_quietly(
                websocket, {"type": "error", "detail": "空闲超时，终端已关闭"}
            )
            await _close(websocket, ssh_bridge.WS_CLOSE_IDLE, "空闲超时")
            return "idle"
        except WebSocketDisconnect:
            return "client"
        except RuntimeError:
            # 读端已经先一步 close（例如 shell 退出）后，再 receive 会抛这个。
            return "client"
        except (ValueError, TypeError):
            await _send_quietly(
                websocket, {"type": "error", "detail": "消息不是合法的 JSON"}
            )
            continue

        if not isinstance(message, dict):
            await _send_quietly(
                websocket, {"type": "error", "detail": "消息必须是 JSON 对象"}
            )
            continue

        kind = message.get("type")

        if kind == "input":
            data = message.get("data")
            if not isinstance(data, str):
                await _send_quietly(
                    websocket, {"type": "error", "detail": "input.data 必须是字符串"}
                )
                continue
            # 按字节算：前端可能一次粘贴多字节字符，按字符数算会放过 3 倍流量。
            if len(data.encode("utf-8")) > ssh_bridge.MAX_INPUT_BYTES:
                await _send_quietly(
                    websocket, {"type": "error", "detail": "输入过长（单条上限 8KB）"}
                )
                continue
            try:
                await session.write(data)
            except (OSError, RuntimeError):
                await _send_quietly(
                    websocket, {"type": "error", "detail": "终端连接已断开"}
                )
                return "write_failed"
            # 只解析已经写成功的输入：写失败说明这行没到达远端，不该留成命令。
            for command in buffer.feed(data):
                if operator_oid:
                    _spawn_command_audit(
                        pending, operator_oid, operator_name, command
                    )
            continue

        if kind == "resize":
            cols = message.get("cols")
            rows = message.get("rows")
            if not (_valid_size(cols) and _valid_size(rows)):
                await _send_quietly(
                    websocket,
                    {"type": "error", "detail": "resize 参数非法（1-1000 的整数）"},
                )
                continue
            try:
                session.resize(cols, rows)
            except (OSError, RuntimeError):
                await _send_quietly(
                    websocket, {"type": "error", "detail": "终端连接已断开"}
                )
                return "resize_failed"
            continue

        if kind == "ping":
            await _send_quietly(websocket, {"type": "pong"})
            continue

        await _send_quietly(
            websocket, {"type": "error", "detail": "未知消息类型"}
        )


@router.websocket("/ws")
async def open_terminal(websocket: WebSocket, ticket: str = Query(default="")):
    entry = ssh_bridge.consume_ticket(ticket)

    # 必须先完成握手再 close：握手阶段直接拒绝时浏览器拿不到 4401 这个自定义
    # 关闭码，前端只能看到「连接失败」，没法区分「重开一个 ticket」以外的处置。
    await websocket.accept()
    if entry is None:
        await _close(
            websocket, ssh_bridge.WS_CLOSE_UNAUTHORIZED, "ticket 无效或已过期"
        )
        return

    operator_oid = entry.operator_oid
    registry = ssh_bridge.registry()
    if not registry.acquire(operator_oid):
        await _send_quietly(
            websocket, {"type": "error", "detail": "并发会话已达上限"}
        )
        await _close(websocket, ssh_bridge.WS_CLOSE_LIMIT, "并发会话已达上限")
        await audit_system_op(
            operator_oid,
            action="web_ssh",
            detail="reject:concurrency",
            log_type="system_ssh",
        )
        return

    session: ssh_bridge.SshSession | None = None
    output_task: asyncio.Task | None = None
    # 命令审计在后台跑：集合持有引用，finally 里统一收尾。
    audit_tasks: set[asyncio.Task] = set()
    reason = "client"
    try:
        try:
            session = await ssh_bridge.SshSession.open()
        except Exception as exc:  # noqa: BLE001 - 连接失败只回笼统提示
            # 只打异常类型：异常消息里可能带私钥路径或主机指纹。
            print(f"web ssh connect failed: {type(exc).__name__}")
            await _send_quietly(
                websocket, {"type": "error", "detail": "终端连接失败"}
            )
            await _close(websocket, ssh_bridge.WS_CLOSE_FAILURE, "终端连接失败")
            reason = "connect_failed"
            return

        # 姓名只查一次：之后每条命令审计复用，避免「每敲一行查一次库」。
        operator_name = (await resolve_names([operator_oid])).get(operator_oid, "")

        await audit_system_op(
            operator_oid,
            action="web_ssh",
            detail=f"open approver={entry.approver_oid}",
            log_type="system_ssh",
        )

        output_task = asyncio.create_task(_pump_output(websocket, session))
        reason = await _pump_input(
            websocket, session, operator_oid, operator_name, audit_tasks
        )
    except WebSocketDisconnect:
        reason = "client"
    finally:
        try:
            # 先关 SSH 再取消泵：让阻塞在 read 上的协程尽快拿到 EOF 退出。
            if session is not None:
                session.close()
            if output_task is not None:
                output_task.cancel()
                # gather 收掉异常；外层被取消时 CancelledError 会继续向上传，
                # 不会被这里吞掉（registry 释放放在内层 finally）。
                await asyncio.gather(output_task, return_exceptions=True)
            if audit_tasks:
                # 等命令审计落库：断开瞬间还在排队的几条不能丢。审计自身
                # 吞掉所有异常，return_exceptions 只兜调度层的意外。
                await asyncio.gather(*audit_tasks, return_exceptions=True)
        finally:
            registry.release(operator_oid)
            await audit_system_op(
                operator_oid,
                action="web_ssh",
                detail=f"close:{reason}",
                log_type="system_ssh",
            )
