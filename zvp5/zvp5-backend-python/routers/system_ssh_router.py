"""Web SSH 终端端点。

``POST /session`` 先过「严格 admin + 双人 TOTP」，发一张一次性 ticket；
``WS /ws`` 用 ticket 建终端。浏览器发起 WebSocket 握手时带不了
``Authorization`` 头，所以 WS 侧只认 ticket —— ticket 的发放已经过完整 HTTP
鉴权，且 120 秒内单次有效。

``GET /targets`` 只列出服务端配置的命名目标（id/label/display）；连接参数
（host/port/user/密钥路径）永远不出现在响应里。``POST /session`` 的 ``target``
只是一个 id，未配置的 id 一律 400 ``SSH_UNKNOWN_TARGET``。

``POST /suggest`` 只让 AI 起草一条命令文本（生成 ≠ 执行）：它不发 ticket、不开
连接、不执行任何东西，执行仍然只发生在 ``/ws`` 里，由人粘贴并亲自回车。

连接目标固定来自服务端配置（``services/ssh_bridge.py``），本文件不接受客户端
指定 host/port/user。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    WebSocket,
    WebSocketDisconnect,
)
from pydantic import BaseModel, Field

from db import backends
from services import ssh_bridge
from services.ai import routing as ai_routing
from services.ai import textgen
from services.ai import thinking as ai_thinking
from services.ssh_command_log import CommandLineBuffer
from typings.log import ZVMSLog
from util import env as env_config
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

logger = logging.getLogger(__name__)

# 终端尺寸上限：只防明显异常的数值把 PTY 撑坏，正常 xterm 在几百以内。
MAX_TERM_DIMENSION = 1000

# 一次粘贴 N 行会派生 N 个审计写任务；限并发只是为了不把连接池打满，
# 超出上限的命令排队，不丢弃。
AUDIT_CONCURRENCY_DEFAULT = 8


def audit_concurrency() -> int:
    raw = env_config.getenv("ZVP5_SSH_AUDIT_CONCURRENCY")
    try:
        value = int(str(raw))
    except (TypeError, ValueError):
        return AUDIT_CONCURRENCY_DEFAULT
    return value if value > 0 else AUDIT_CONCURRENCY_DEFAULT


_audit_semaphore = asyncio.Semaphore(audit_concurrency())


class SessionRequest(BaseModel):
    # 长度/字符集留给守卫判：它要区分「格式非法 422」与「码不对 403」，
    # 在这里用 Field 提前拦截会把两种错误混成同一条 Pydantic 报错。
    totp_code: str
    # 只接受目标 id，连接参数仍由服务端配置决定；未知 id 在端点里判 400。
    target: str | None = None


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


@router.get("/targets")
async def list_ssh_targets(user=Depends(get_current_user)):
    """列出可选目标：只回 id/label/display，密钥路径与 known_hosts 永不出圈。"""
    await _require_system_admin(user)
    targets = ssh_bridge.load_targets()
    default = ssh_bridge.load_target()
    return {
        "status": "ok",
        "code": 200,
        "data": {
            "targets": [
                {"id": t.id, "label": t.label, "display": t.display} for t in targets
            ],
            "default": default.id,
        },
    }


@router.post("/session")
async def create_ssh_session(
    payload: SessionRequest,
    user=Depends(get_current_user),
):
    """校验双人码后发放 ticket；ticket 只能消费一次。"""
    me = await _require_system_admin(user)

    # 先解析目标再验码：未知 id 不是「批准过的动作」，没必要消耗一次动态码。
    target_id = (payload.target or "").strip()
    if target_id:
        target = ssh_bridge.get_target(target_id)
        if target is None:
            raise HTTPException(status_code=400, detail="SSH_UNKNOWN_TARGET")
    else:
        # 兼容老前端：不传 target 就是默认目标。
        target = ssh_bridge.load_target()

    try:
        approver_oid = await require_dual_auth(me, payload.totp_code, action="web_ssh")
    except DualAuthError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    operator_oid = str(me.get("_id") or user["id"])
    ticket, expires_in = ssh_bridge.issue_ticket(
        operator_oid, approver_oid, target_id=target.id
    )
    return {
        "status": "ok",
        "code": 200,
        "data": {
            "ticket": ticket,
            "expires_in": expires_in,
            "target": {"id": target.id, "label": target.label},
        },
    }


class SuggestRequest(BaseModel):
    """AI 起草命令的入参。

    这里**没有** host/port/user/command 字段：能生成什么只由意图文本决定，落点
    永远由服务端配置决定。``intent`` 与 ``context`` 的长度上限是「别把网关当
    长文入口」的硬边界，不是安全边界（真正的安全边界在 system prompt 与人工审阅）。

    ``thinking`` 是平台档位（``low`` / ``medium`` / ``high``，空 = 上游默认）：档位
    合法性在端点里判（400 中文），不用 pydantic 枚举——那会变成 422。
    """

    intent: str = Field(min_length=1, max_length=1000)
    context: str | None = Field(default=None, max_length=2000)
    model: str | None = Field(default=None, max_length=64)
    thinking: str | None = Field(default=None, max_length=16)


def _clean_thinking(value: object) -> str | None:
    """思考强度归一：``low`` / ``medium`` / ``high``，空串/None → ``None``。

    与会话/场景面同一份档位常量（:data:`services.ai.thinking.LEVELS`），未知值 400：
    静默退默认会让管理员以为「这次用了 high」。
    """
    text = str(value or "").strip().lower()
    if not text:
        return None
    if text not in ai_thinking.LEVELS:
        raise HTTPException(
            status_code=400, detail="思考强度只能是 low、medium、high，或留空"
        )
    return text


#: 审计里只留命令的头部：足够复盘「生成的是不是危险命令」，又不至于把整条命令
#: （可能带密码）复制到第二张表里。
SUGGEST_HEAD_CHARS = 200


def _command_head(command: str) -> str:
    """脱敏后的命令头部（``redact`` 见 ``services.agent.ops_tools``）。

    截断放在脱敏之前：``redact`` 对超过 200 字符的字符串直接换成 ``<len N>``，
    先截再抹才能保住「前 200 字符」这个可读的复核窗口。
    """
    # 局部导入：路由模块加载时不必拉起整张 agent 工具注册表。
    from services.agent.ops_tools import redact

    return str(redact(command[:SUGGEST_HEAD_CHARS]))


async def _write_suggest_audit(
    operator_oid: str, intent_len: int, model: str, command: str
) -> None:
    """落一条「AI 生成过一次命令建议」。

    与 ``system_ssh_command``（真的被执行过的命令）分开存：生成不等于执行，混在
    一起会让「谁执行过什么」失真。写失败只打印——审计是旁路。
    """
    try:
        await ZVMSLog(
            url="system:web_ssh",
            user=operator_oid,
            data=json.dumps(
                {
                    "intent_len": intent_len,
                    "model": model,
                    "command_head": _command_head(command),
                },
                ensure_ascii=False,
            ),
            type="system_ssh_suggest",
            level="info",
        ).insert_log()
    except Exception as exc:  # noqa: BLE001 - 审计失败不改变本请求结论
        logger.error("web ssh suggest audit failed: %s", type(exc).__name__)


@router.post("/suggest")
async def suggest_ssh_command(
    payload: SuggestRequest,
    user=Depends(get_current_user),
):
    """让 AI 起草一条 SSH 命令，**只返回文本**。

    本端点不建立 SSH 连接、不执行命令、不发 ticket：执行只发生在已过双人 TOTP
    的终端（``/ws``）里，由人把命令插进去并亲自按回车。也因此这里不要 TOTP——
    没有可被批准的动作；但频控与月额度照旧，避免它变成免费的上游用量入口。
    """
    me = await _require_system_admin(user)
    operator_oid = str(me.get("_id") or user["id"])
    thinking = _clean_thinking(payload.thinking)
    # 额度默认值按「有效权限」算（admin 的 chat 额度比学生高），而 ``me`` 是库文档、
    # 不含 ``per``；认证态里的 ``per`` 已经过 ``_require_system_admin`` 复核，直
    # 接用，缺了再查一次权限表。
    actor = {
        **me,
        "per": list(user.get("per") or await get_user_permissions(me)),
    }

    try:
        data = await textgen.suggest_ssh_command(
            actor,
            intent=payload.intent,
            context=payload.context,
            model=payload.model,
            thinking=thinking,
        )
    except ai_routing.RouteError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.code) from None

    await _write_suggest_audit(
        operator_oid, len(payload.intent), data["model"], data["command"]
    )
    return {"status": "ok", "code": 200, "data": data}


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
        logger.error("web ssh output pump failed: %s", type(exc).__name__)
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
        async with _audit_semaphore:
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
        logger.error("web ssh command audit failed: %s", type(exc).__name__)


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

    # 目标由 ticket 决定：发 ticket 之后配置被改掉时宁可拒绝，也不静默连到别的
    # 机器——否则审计里的 target 会和实际落点不一致。空 target_id 是升级前/测试
    # 里手工造的 ticket，按默认目标处理，老语义不变。
    target_id = getattr(entry, "target_id", "") or ""
    target = (
        ssh_bridge.get_target(target_id) if target_id else ssh_bridge.load_target()
    )
    if target is None:
        logger.error("web ssh target unavailable: %s", target_id)
        await _send_quietly(
            websocket, {"type": "error", "detail": "终端目标不可用"}
        )
        await _close(websocket, ssh_bridge.WS_CLOSE_FAILURE, "终端目标不可用")
        await audit_system_op(
            operator_oid,
            action="web_ssh",
            detail=f"reject:target target={target_id}",
            log_type="system_ssh",
        )
        return

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
            session = await ssh_bridge.SshSession.open(target=target)
        except Exception as exc:  # noqa: BLE001 - 连接失败只回笼统提示
            # 只打异常类型：异常消息里可能带私钥路径或主机指纹。
            logger.error("web ssh connect failed: %s", type(exc).__name__)
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
            detail=f"open approver={entry.approver_oid} target={target.id}",
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
                detail=f"close:{reason} target={target.id}",
                log_type="system_ssh",
            )
