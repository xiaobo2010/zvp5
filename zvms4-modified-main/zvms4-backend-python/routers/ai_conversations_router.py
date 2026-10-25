"""持久会话面的 HTTP 层（``/api/ai/conversations``、``/api/ai/runs``，契约 §3.1/§3.2）。

与 ``/api/ai/api-keys`` 同一套外壳：成功是仓库约定的 envelope
（``{"status","code","data"}``，列表再带 ``metadata.size``），错误 ``detail`` 用
**错误码**而不是长句——前端按 code 分支（``src/api/ai/errors.ts``）。

鉴权与门禁与 agent 路由同一口径（``get_current_user`` + ``AI_ALLOWED_ROLES``）：
会话是 agent/chat 的容器，不该另设一套白名单。门禁拒绝落一行审计——「谁在没权限
的情况下反复建会话」在后台必须看得见；业务性 404/409 不落：轮询会查别人的 oid、
也会查到已删除的会话，那些是正常控制流，记进流水只会把审计冲成噪音。

所有查询都强制 ``owner_oid = 当前用户``（服务层做），本层只做协议翻译与参数校验。
"""

from __future__ import annotations

import logging
import re
from typing import Any, Awaitable, Callable
from uuid import uuid4

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from routers.ai_router import AI_FORBIDDEN_MESSAGE, _error_type, _has_ai_access, ai_scene
from services.agent import run_queue
from services.agent.tool_impl import AgentError
from services.ai import audit
from services.ai import conversations
from util.object_id import get_current_user

_logger = logging.getLogger(__name__)

router = APIRouter()

# 会话/run 的 oid 都是 ``uuid4().hex``（32 位小写 hex）。形状不对直接当「不存在」：
# 32 位 hex 的校验不需要一次数据库往返，也顺手挡掉把奇怪字符串塞进查询的情形。
OID_PATTERN = re.compile(r"^[0-9a-f]{32}$")

# 审计里的端点名：与挂载前缀一致，方便和 ``/api/ai/agent/run`` 区分开。
ENDPOINT_CONVERSATIONS = "/api/ai/conversations"


class ConversationCreate(BaseModel):
    """建会话。``title`` 可省（首条用户消息会补上，§2.1）；``model`` 可省（服务端默认）。"""

    mode: str = Field(max_length=8)
    model: str | None = Field(default=None, max_length=120)
    title: str | None = Field(default=None, max_length=400)


class ConversationUpdate(BaseModel):
    """部分更新：只改传了的字段。

    ``model`` 的三种意图靠 ``exclude_unset`` 区分：不传 = 不改，传字符串 = 设置，
    显式 ``null`` = 清空（回到服务端默认）。
    """

    title: str | None = Field(default=None, max_length=400)
    status: str | None = Field(default=None, max_length=12)
    model: str | None = Field(default=None, max_length=120)


class RunCreate(BaseModel):
    """提交一次后台执行。

    ``client_msg_id`` 是幂等键：客户端重试同一个键会拿回同一个 run，不会重复写
    消息、也不会重复计费（§2.3）。
    """

    content: str = Field(min_length=1, max_length=20000)
    model: str | None = Field(default=None, max_length=120)
    client_msg_id: str | None = Field(default=None, max_length=64)


# ---------------------------------------------------------------------------
# 协议外壳
# ---------------------------------------------------------------------------

def _error(
    status_code: int,
    code: str,
    message: str,
    *,
    request_id: str,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    """与 ``ai_agent_router._error`` 同形（复用 ``ai_router`` 的 type 推导）。"""
    merged = {"X-AI-Request-Id": request_id}
    if headers:
        merged.update(headers)
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "message": message,
                "type": _error_type(status_code),
                "code": code,
            }
        },
        headers=merged,
    )


def _ok(data: Any, *, status_code: int, request_id: str) -> JSONResponse:
    body: dict[str, Any] = {"status": "ok", "code": status_code, "data": data}
    if isinstance(data, dict) and isinstance(data.get("conversations"), list):
        # 列表响应带 metadata.size：前端列表封装统一读它当「本页条数」。
        body["metadata"] = {"size": len(data["conversations"])}
    return JSONResponse(
        status_code=status_code, content=body, headers={"X-AI-Request-Id": request_id}
    )


async def _audit(
    user: dict,
    *,
    scene: str | None,
    request_id: str,
    status: str,
    code: str,
    http_status: int,
) -> None:
    """一行 ``AI_CALL_LOGS``（``capability='chat'``，purpose 取 ``X-AI-Scene``）。

    审计是旁路：``services.ai.audit.record`` 内部吞掉写入异常，不改变响应结论。
    """
    await audit.record(
        user_oid=str(user.get("id") or ""),
        capability="chat",
        endpoint=ENDPOINT_CONVERSATIONS,
        status=status,
        http_status=http_status,
        error_code=code,
        latency_ms=0,
        request_id=request_id,
        purpose=scene,
        roles_snapshot=audit.roles_snapshot(user.get("per")),
    )


async def _forbidden(user: dict, *, scene: str | None, request_id: str) -> JSONResponse:
    await _audit(
        user,
        scene=scene,
        request_id=request_id,
        status="rejected",
        code="AI_FORBIDDEN",
        http_status=403,
    )
    return _error(403, "AI_FORBIDDEN", AI_FORBIDDEN_MESSAGE, request_id=request_id)


async def _dispatch(
    action: Callable[[], Awaitable[Any]],
    *,
    user: dict,
    scene: str | None,
    status_code: int = 200,
) -> JSONResponse:
    """统一外壳：门禁 → 执行 → 错误翻译（``AgentError`` / 500 兜底 + 审计）。"""
    request_id = uuid4().hex
    if not _has_ai_access(user):
        return await _forbidden(user, scene=scene, request_id=request_id)
    try:
        data = await action()
    except AgentError as exc:
        return _error(
            exc.status_code,
            exc.code,
            exc.message,
            request_id=request_id,
            headers=exc.headers or None,
        )
    except Exception:  # noqa: BLE001 - 兜底：协议体必须是可解析的形状
        _logger.exception("ai conversation request failed")
        await _audit(
            user,
            scene=scene,
            request_id=request_id,
            status="error",
            code="AI_AGENT_INTERNAL_ERROR",
            http_status=500,
        )
        return _error(
            500,
            "AI_AGENT_INTERNAL_ERROR",
            "conversation request failed unexpectedly",
            request_id=request_id,
        )
    return _ok(data, status_code=status_code, request_id=request_id)


def _require_oid(oid: str, *, code: str, message: str) -> str:
    value = str(oid or "").strip().lower()
    if not OID_PATTERN.match(value):
        raise AgentError(code, message, 404)
    return value


# ---------------------------------------------------------------------------
# 会话
# ---------------------------------------------------------------------------

@router.post("/conversations")
async def create_conversation(
    payload: ConversationCreate,
    user=Depends(get_current_user),
    scene: str | None = Depends(ai_scene),
):
    """建会话；``mode`` 建完不可改（``chat`` 走同步 SSE，``agent`` 走后台任务）。"""

    async def action() -> dict:
        return await conversations.create(
            user=user, mode=payload.mode, title=payload.title, model=payload.model
        )

    return await _dispatch(action, user=user, scene=scene, status_code=201)


@router.get("/conversations")
async def list_conversations(
    status: str | None = Query(default="active", max_length=12),
    limit: int = Query(default=conversations.LIST_LIMIT_DEFAULT, ge=1, le=conversations.LIST_LIMIT_MAX),
    cursor: str | None = Query(default=None, max_length=200),
    user=Depends(get_current_user),
    scene: str | None = Depends(ai_scene),
):
    """本人的会话列表（``updated_at`` 倒序，游标分页）。

    列表**不含**消息正文、也不含活跃 run（那部分走 ``GET /runs?active=1``）：翻页
    载荷要稳定地小，正文只在 ``/messages`` 里取。
    """

    async def action() -> dict:
        return await conversations.list_page(
            user=user, status=status, cursor=cursor, limit=limit
        )

    return await _dispatch(action, user=user, scene=scene)


@router.get("/conversations/{oid}")
async def get_conversation(
    oid: str,
    user=Depends(get_current_user),
    scene: str | None = Depends(ai_scene),
):
    async def action() -> dict:
        return await conversations.get(
            user=user,
            oid=_require_oid(oid, code=conversations.NOT_FOUND, message="conversation not found"),
        )

    return await _dispatch(action, user=user, scene=scene)


@router.patch("/conversations/{oid}")
async def update_conversation(
    oid: str,
    payload: ConversationUpdate,
    user=Depends(get_current_user),
    scene: str | None = Depends(ai_scene),
):
    """改名 / 归档 / 换模型（``model: null`` = 清空回服务端默认）。"""
    fields = payload.model_dump(exclude_unset=True)

    async def action() -> dict:
        return await conversations.update(
            user=user,
            oid=_require_oid(oid, code=conversations.NOT_FOUND, message="conversation not found"),
            title=fields.get("title"),
            status=fields.get("status"),
            model=fields.get("model"),
            clear_model="model" in fields and fields["model"] is None,
        )

    return await _dispatch(action, user=user, scene=scene)


@router.delete("/conversations/{oid}")
async def delete_conversation(
    oid: str,
    user=Depends(get_current_user),
    scene: str | None = Depends(ai_scene),
):
    """硬删会话（含消息与 run）。

    存在活跃 run 时 409：先 cancel 再删，否则 worker 会在一个已经不存在的会话上
    继续写消息。删除前会把该会话的待确认提案作废（服务层做，§3.1）。
    """

    async def action() -> dict:
        return await conversations.delete(
            user=user,
            oid=_require_oid(oid, code=conversations.NOT_FOUND, message="conversation not found"),
        )

    return await _dispatch(action, user=user, scene=scene)


@router.get("/conversations/{oid}/messages")
async def list_messages(
    oid: str,
    after_seq: int = Query(default=0, ge=0),
    limit: int = Query(default=conversations.MESSAGE_LIMIT_DEFAULT, ge=1, le=conversations.MESSAGE_LIMIT_MAX),
    user=Depends(get_current_user),
    scene: str | None = Depends(ai_scene),
):
    """``seq > after_seq`` 的增量消息（刷新与轮询同一条路径）。"""

    async def action() -> dict:
        return await conversations.list_messages(
            user=user,
            oid=_require_oid(oid, code=conversations.NOT_FOUND, message="conversation not found"),
            after_seq=after_seq,
            limit=limit,
        )

    return await _dispatch(action, user=user, scene=scene)


# ---------------------------------------------------------------------------
# 智能体（任务化）
# ---------------------------------------------------------------------------

@router.post("/conversations/{oid}/runs")
async def submit_run(
    oid: str,
    payload: RunCreate,
    user=Depends(get_current_user),
    scene: str | None = Depends(ai_scene),
):
    """提交一次后台执行：一个事务写用户消息 + ``queued`` run，立刻 202。

    紧接着 ``kick()`` 让泵去领：提交与执行解耦，浏览器关掉也跑完（§4）。
    """

    async def action() -> dict:
        data = await run_queue.submit(
            user=user,
            conversation_oid=_require_oid(
                oid, code=conversations.NOT_FOUND, message="conversation not found"
            ),
            content=payload.content,
            model=payload.model,
            client_msg_id=payload.client_msg_id,
        )
        await run_queue.kick()
        return data

    return await _dispatch(action, user=user, scene=scene, status_code=202)


@router.get("/runs")
async def list_runs(
    active: int = Query(default=1, ge=0, le=1),
    user=Depends(get_current_user),
    scene: str | None = Depends(ai_scene),
):
    """本人的在飞 run（重连横幅用）。

    ``active`` 只在契约里出现过 ``=1`` 这一个取值；本期没有「历史 run 列表」的
    需求（历史从消息增量读），所以 ``active=0`` 回空表而不是另一种语义。
    """

    async def action() -> dict:
        if not active:
            return {"runs": []}
        rows = await run_queue.list_active(str(user.get("id") or ""))
        return {"runs": [run_queue.run_view(row) for row in rows]}

    return await _dispatch(action, user=user, scene=scene)


@router.get("/runs/{run_id}")
async def get_run(
    run_id: str,
    after_round: int = Query(default=0, ge=0),
    user=Depends(get_current_user),
    scene: str | None = Depends(ai_scene),
):
    """run 状态 + 增量事件 + 提案。

    ``after_round`` 只回 ``round_no > after_round`` 的事件：轮询 1.5s 一次时，载荷
    随轮次线性增长而不是平方（§3.2）。
    """

    async def action() -> dict:
        return await run_queue.run_detail(
            user=user,
            run_id=_require_oid(run_id, code=run_queue.RUN_NOT_FOUND, message="run not found"),
            after_round=after_round,
        )

    return await _dispatch(action, user=user, scene=scene)


@router.post("/runs/{run_id}/cancel")
async def cancel_run(
    run_id: str,
    user=Depends(get_current_user),
    scene: str | None = Depends(ai_scene),
):
    """请求中止：``queued`` 直接进终态，``running`` 由 worker 在轮边界收手。"""

    async def action() -> dict:
        return await run_queue.cancel(
            user=user,
            run_id=_require_oid(run_id, code=run_queue.RUN_NOT_FOUND, message="run not found"),
        )

    return await _dispatch(action, user=user, scene=scene)


__all__ = ["router"]
