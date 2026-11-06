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
from typing import Any, Awaitable, Callable
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from routers.ai.common import AI_FORBIDDEN_MESSAGE, _error_type, _has_ai_access, ai_scene
from services.agent import run_queue
from services.agent.tool_impl import AgentError
from services.ai import audit
from services.ai import conversations
from typings.log import xuehai_id_from
from util.object_id import get_current_user
from util.oid import hex_id_or_none
from util.permission.features import ensure_feature

_logger = logging.getLogger(__name__)

router = APIRouter()

# 会话/run 的 oid 都是 ``uuid4().hex``（32 位小写 hex）。形状不对直接当「不存在」：
# 32 位 hex 的校验不需要一次数据库往返，也顺手挡掉把奇怪字符串塞进查询的情形。
# 形状判定在 ``util.oid.hex_id_or_none``（24 位业务 oid 走 ``db.ids``）。
SESSION_ID_LENGTH = 32

# 审计里的端点名：与挂载前缀一致，方便和 ``/api/ai/agent/run`` 区分开。
ENDPOINT_CONVERSATIONS = "/api/ai/conversations"


class ConversationCreate(BaseModel):
    """建会话。``mode`` 只剩 ``agent``（缺省即它）。

    ``model`` 可省：缺省取该用户可见目录的第一个。**模型在这里定，发过消息后
    就不能再改**（空会话仍允许 PATCH 纠正）。``thinking`` 是可选思考强度
    （low|medium|high）。
    """

    mode: str = Field(default="agent", max_length=8)
    model: str | None = Field(default=None, max_length=120)
    thinking: str | None = Field(default=None, max_length=16)
    title: str | None = Field(default=None, max_length=400)


class ConversationUpdate(BaseModel):
    """部分更新：只改传了的字段。

    ``model`` 只在**空会话**（``message_count == 0``）上生效：已有消息时会被
    服务层回 409 ``AI_CONVERSATION_MODEL_LOCKED``——把不支持的意图静默忽略会让
    前端以为改成功了。显式 ``null`` = 「清空模型」（执行期回落目录第一个）。
    """

    title: str | None = Field(default=None, max_length=400)
    status: str | None = Field(default=None, max_length=12)
    model: str | None = Field(default=None, max_length=120)
    thinking: str | None = Field(default=None, max_length=16)


class RunCreate(BaseModel):
    """提交一次后台执行。

    ``client_msg_id`` 是幂等键：客户端重试同一个键会拿回同一个 run，不会重复写
    消息、也不会重复计费（§2.3）。

    **没有 ``model``**：一次 run 用哪个模型由会话决定（R2）。老客户端仍会带这个
    字段，pydantic 会忽略未知键——「不再接受」不等于「报错」，否则一次灰度就
    会把所有老前端打成 422。
    """

    content: str = Field(min_length=1, max_length=20000)
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


# ---------------------------------------------------------------------------
# 功能开关（V043 ``ROLE_FEATURES``）
# ---------------------------------------------------------------------------

# 会话只服务 agent 语义：能力开关固定取 ``ai.agent``（``ai.chat`` 只剩
# ``/v1/chat/completions`` 那条无状态链路在用）。
AGENT_FEATURE = "ai.agent"


def _require_agent_feature(user: dict) -> None:
    """功能开关不满足即抛 403，由 :func:`_dispatch` 翻译成既有的 ``AI_FORBIDDEN``。

    为什么抛 ``AgentError`` 而不是像 ``ai_agent_router`` 那样返回 ``JSONResponse``：
    这里的判定发生在 ``action()`` 内部，抛出去才能保证「闸门恒在任何写入之前」——
    包括不建会话、不写 ``AI_CALL_LOGS``、不入 run 队列。开关拒绝**不落审计**：
    角色白名单拒绝是探测信号，开关拒绝只是管理员收起了能力，记流水只会把审计
    冲成噪音（与 ``ai_agent_router._feature_denied`` 同一口径）。
    """
    try:
        ensure_feature(user, AGENT_FEATURE)
    except HTTPException as exc:
        raise AgentError("AI_FORBIDDEN", str(exc.detail), 403) from exc



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
    value = hex_id_or_none(oid, length=SESSION_ID_LENGTH)
    if value is None:
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
    """建一个 agent 会话（``mode`` 缺省即 ``agent``，``chat`` 回 409）。

    开关在 ``conversations.create`` 之前判：关掉 ``ai.agent`` 之后连会话都建不出来。
    """

    async def action() -> dict:
        _require_agent_feature(user)
        return await conversations.create(
            user=user,
            mode=payload.mode,
            title=payload.title,
            model=payload.model,
            thinking=payload.thinking,
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
        page = await conversations.list_page(
            user=user, status=status, cursor=cursor, limit=limit
        )
        return page

    return await _dispatch(action, user=user, scene=scene)


@router.get("/conversations/{oid}")
async def get_conversation(
    oid: str,
    user=Depends(get_current_user),
    scene: str | None = Depends(ai_scene),
):
    async def action() -> dict:
        row = await conversations.require_row(
            user=user,
            oid=_require_oid(oid, code=conversations.NOT_FOUND, message="conversation not found"),
        )
        _require_agent_feature(user)
        return conversations.conversation_view(row)

    return await _dispatch(action, user=user, scene=scene)


@router.patch("/conversations/{oid}")
async def update_conversation(
    oid: str,
    payload: ConversationUpdate,
    user=Depends(get_current_user),
    scene: str | None = Depends(ai_scene),
):
    """改名 / 归档 / 调思考强度 / 空会话改模型。

    已有消息（``message_count > 0``）的会话改模型一律 409
    （``AI_CONVERSATION_MODEL_LOCKED``）：换模型会让历史回放的缓存前缀与计费口
    径都换一套。空会话则放行——那是「建完发现选错模型」的唯一修复窗口，见
    ``conversations.update``。

    故意不接功能开关：改名/归档不是 AI 能力调用，关掉 ``ai.chat`` 之后用户仍该
    能收拾自己的历史（否则会话变成删不掉也改不了的遗留数据）。
    """
    fields = payload.model_dump(exclude_unset=True)

    async def action() -> dict:
        return await conversations.update(
            user=user,
            oid=_require_oid(oid, code=conversations.NOT_FOUND, message="conversation not found"),
            title=fields.get("title"),
            status=fields.get("status"),
            thinking=fields.get("thinking"),
            clear_thinking="thinking" in fields and fields["thinking"] is None,
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

    与 ``PATCH`` 同理不接功能开关：删除是清理自己的数据，不是 AI 能力调用。
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
        conversation_oid = _require_oid(
            oid, code=conversations.NOT_FOUND, message="conversation not found"
        )
        await conversations.require_row(user=user, oid=conversation_oid)
        _require_agent_feature(user)
        return await conversations.list_messages(
            user=user,
            oid=conversation_oid,
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
    request: Request,
    user=Depends(get_current_user),
    scene: str | None = Depends(ai_scene),
):
    """提交一次后台执行：一个事务写用户消息 + ``queued`` run，立刻 202。

    紧接着 ``kick()`` 让泵去领：提交与执行解耦，浏览器关掉也跑完（§4）。

    功能开关必须在 ``run_queue.submit`` 之前：``submit`` 内部是密码门 → 审核 →
    灰区闸门 → 入队的固定顺序（不能在这里插一道），而开关拒绝要保证「一个 run
    都不入队」。多出来的这次会话读是一次索引查询，换「关掉 ``ai.agent`` 之后
    不可能再有新 run」这条结论，值得。
    """

    async def action() -> dict:
        conversation_oid = _require_oid(
            oid, code=conversations.NOT_FOUND, message="conversation not found"
        )
        conversation = await conversations.require_row(user=user, oid=conversation_oid)
        _require_agent_feature(user)
        data = await run_queue.submit(
            user=user,
            conversation_oid=conversation_oid,
            content=payload.content,
            client_msg_id=payload.client_msg_id,
            xuehai_id=xuehai_id_from(request),
            # 刚读过的行直接交下去：submit 不必再查一遍同一条会话。
            conversation=conversation,
        )
        await run_queue.kick()
        return data

    return await _dispatch(action, user=user, scene=scene, status_code=202)


@router.post("/conversations/{oid}/compact")
async def compact_conversation(
    oid: str,
    user=Depends(get_current_user),
    scene: str | None = Depends(ai_scene),
):
    """手动压缩一次会话上下文（返回是否真的压了 + 水位前后）。

    与自动压缩（会话占用越过阈值时在 run 内触发）共用同一份实现，区别只有
    ``force=True``：用户点了就要压，不判阈值。

    两道闸的顺序是「功能开关 → 归属（服务层 require_row）」：关掉 ``ai.agent`` 之后
    连压缩都不该发生（它会花一次真实上游调用并扣额度）。活跃 run 的处理在服务层：
    409 ``AI_RUN_CONFLICT``，与「同会话已有 run」用同一个错误码。
    """

    async def action() -> dict:
        _require_agent_feature(user)
        return await run_queue.compact_conversation(
            user=user,
            conversation_oid=_require_oid(
                oid, code=conversations.NOT_FOUND, message="conversation not found"
            ),
        )

    return await _dispatch(action, user=user, scene=scene)


@router.get("/runs")
async def list_runs(
    active: int = Query(default=1, ge=0, le=1),
    limit: int = Query(
        default=run_queue.RECENT_LIMIT_DEFAULT,
        ge=1,
        le=run_queue.RECENT_LIMIT_MAX,
    ),
    user=Depends(get_current_user),
    scene: str | None = Depends(ai_scene),
):
    """本人的 run 列表；``active`` 决定语义，``limit`` 只对 ``active=0`` 生效。

    * ``active=1``（默认）：在飞 ``queued|running`` run，``created_at`` 升序。
      重连横幅用它恢复任务；在飞 run 每用户最多一条，``limit`` 在这里没有意义
      （忽略，不报错，老客户端继续可用）。
    * ``active=0``：最近 ``limit`` 条 run（任意状态），``created_at`` 倒序。
      个人首页用这一支——「最近发生了什么」不能只有成功的那几条。

    两支都只查 ``owner_oid = 当前用户``，越权读取没有参数可传。

    run 只存在于 agent 模式会话（``run_queue.submit`` 用 409 挡住其余），所以这里
    按 ``ai.agent`` 静态判，不必先读会话。
    """

    async def action() -> dict:
        _require_agent_feature(user)
        owner_oid = str(user.get("id") or "")
        if active:
            rows = await run_queue.list_active(owner_oid)
        else:
            rows = await run_queue.list_recent(owner_oid, limit=limit)
        return {"runs": [run_queue.run_view(row) for row in rows]}

    return await _dispatch(action, user=user, scene=scene)


@router.get("/runs/{run_id}")
async def get_run(
    run_id: str,
    after_round: int = Query(default=0, ge=0),
    since_msg_seq: int | None = Query(default=None, ge=0),
    user=Depends(get_current_user),
    scene: str | None = Depends(ai_scene),
):
    """run 状态 + 增量事件 + 提案（+ 可选增量消息）。

    ``after_round`` 只回 ``round_no > after_round`` 的事件：轮询 1.5s 一次时，载荷
    随轮次线性增长而不是平方（§3.2）。

    ``since_msg_seq`` 给了就额外带 ``messages``（``seq > since_msg_seq``，升序，
    形状与 ``GET /conversations/{oid}/messages`` 一致）——一轮的工具消息与最终
    回答因此能在同一次轮询里到达；不传则响应与改造前逐字相同。
    """

    async def action() -> dict:
        _require_agent_feature(user)
        return await run_queue.run_detail(
            user=user,
            run_id=_require_oid(run_id, code=run_queue.RUN_NOT_FOUND, message="run not found"),
            after_round=after_round,
            since_msg_seq=since_msg_seq,
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
        _require_agent_feature(user)
        return await run_queue.cancel(
            user=user,
            run_id=_require_oid(run_id, code=run_queue.RUN_NOT_FOUND, message="run not found"),
        )

    return await _dispatch(action, user=user, scene=scene)


__all__ = ["router"]
