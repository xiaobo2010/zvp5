"""agent（智能体）HTTP 层（``/api/ai/agent``）。

五个端点，一套协议：错误体与 ``/v1/chat/completions`` 同形
（``{"error": {"message", "type", "code"}}``）+ ``X-AI-Request-Id`` 头，因为调用方
是同一个前端 AI 封装，多一套形状只会多一处要翻译的地方。

权限门禁与 chat 面同一口径（``AI_ALLOWED_ROLES``，默认 admin / department）：
agent 只是 chat 能力的高级用法，不改另一套白名单。门禁拒绝也落一行审计——「谁在
没有权限的情况下反复试探 agent」在后台必须看得见。executor 在**打上游之前**就
拒绝的那些（模型不在目录、消息形状不对、频控/额度）同样补一行：具体是哪一种由
executor 自己标记（已记账的异常带 ``audited``），这里只补漏掉的，不重复记。

计费说明：**一次 ``/run`` 的每一轮都是一次独立的上游调用**，各写一条
``AI_CALL_LOGS``（``purpose='agent'`` + ``round_no``）并各扣一次 chat 额度。
配额面板上「10 轮的一次 run」看起来是 10 次调用，这是有意的——按真实上游成本
记账。响应里的 ``events`` 是工具调用留痕（读工具的执行、写工具的提案、被拒的
尝试都在里面），``proposals`` 是本次 run 生成的待确认提案。

写操作的确认是**双人**的：``requires_dual_auth`` 的提案必须带另一位管理员的
6 位动态码（``totp_code``）。缺码 422，码错/已被用/无第二管理员 403/409，语义与
``util.dual_auth`` 逐字一致。
"""

from __future__ import annotations

import logging
import re
from uuid import uuid4

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from routers.ai_router import (
    AI_FORBIDDEN_MESSAGE,
    _error_type,
    _has_ai_access,
)
from services.agent import executor, proposal
from services.agent.tool_impl import AgentError
from services.ai import audit
from util.object_id import get_current_user

_logger = logging.getLogger(__name__)

router = APIRouter()

# 提案 oid 的形状：``uuid4().hex``（32 位小写 hex）。形状不对直接当「不存在」，
# 不查库——32 位 hex 的校验不需要一次往返。
PROPOSAL_OID_PATTERN = re.compile(r"^[0-9a-f]{32}$")


class AgentRunRequest(BaseModel):
    """一次 agent 会话的入参。

    ``messages`` 与 OpenAI 的 ``messages`` 同形（``role`` / ``content``），只允许
    ``user`` / ``assistant`` 两种角色 —— ``system`` 由服务端注入（缓存前缀与行为
    护栏都归服务端），``tool`` 角色由服务端自己产生；客户端不能伪造任何一条来
    影响模型判断。

    ``model`` 可省：省了就取该用户可见目录里的第一个模型（前端不必为了「随便用」
    先发一次 ``/v1/models``）。显式指定则必须在该用户可见目录里。
    """

    messages: list[dict] = Field(min_length=1, max_length=50)
    model: str | None = Field(default=None, max_length=80)


class ConfirmRequest(BaseModel):
    """确认提案的入参：``totp_code`` 只在 ``requires_dual_auth`` 时必填。"""

    totp_code: str | None = Field(default=None, max_length=16)


def _error(
    status_code: int,
    code: str,
    message: str,
    *,
    request_id: str,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    """与 ``ai_router._openai_error`` 同形的错误体（复用它的 type 推导）。"""
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


def _agent_error(exc: AgentError, *, request_id: str) -> JSONResponse:
    headers = dict(exc.headers)
    if exc.retry_after is not None:
        headers["Retry-After"] = str(exc.retry_after)
    return _error(
        exc.status_code,
        exc.code,
        exc.message,
        request_id=request_id,
        headers=headers or None,
    )


async def _audit(user: dict, *, request_id: str, status: str, code: str, http_status: int):
    """一行 ``AI_CALL_LOGS``（``capability='chat'`` + ``purpose='agent'``）。

    审计是旁路：``services.ai.audit.record`` 内部已吞掉写入异常，这里的调用不会
    改变响应结论。
    """
    await audit.record(
        user_oid=str(user.get("id") or ""),
        capability="chat",
        endpoint=audit.ENDPOINT_AGENT,
        status=status,
        http_status=http_status,
        error_code=code,
        latency_ms=0,
        request_id=request_id,
        purpose=executor.PURPOSE_AGENT,
        roles_snapshot=audit.roles_snapshot(user.get("per")),
    )


async def _forbidden(user: dict, *, request_id: str) -> JSONResponse:
    """门禁拒绝：先落审计，再回 403 协议体。"""
    await _audit(
        user,
        request_id=request_id,
        status="rejected",
        code="AI_FORBIDDEN",
        http_status=403,
    )
    return _error(403, "AI_FORBIDDEN", AI_FORBIDDEN_MESSAGE, request_id=request_id)


@router.post("/run")
async def run_agent(
    payload: AgentRunRequest,
    user=Depends(get_current_user),
):
    """跑一次 agent 会话（多轮工具调用）。

    成功：``{"request_id", "content", "stop_reason", "rounds", "usage", "events",
    "proposals"}``。``stop_reason`` 为 ``null`` 时是模型自己收尾；``timeout`` /
    ``token_budget`` / ``max_iterations`` 表示被闸门截断（``content`` 里可能是
    已产生的内容或一句说明）。工具调用留痕在 ``events``，写工具生成的待确认提案
    在 ``proposals``（确认走 ``POST /api/ai/agent/proposals/{oid}/confirm``）。
    """
    request_id = uuid4().hex

    if not _has_ai_access(user):
        return await _forbidden(user, request_id=request_id)

    try:
        data = await executor.run(
            user=user,
            messages=payload.messages,
            model=payload.model,
            request_id=request_id,
        )
    except AgentError as exc:
        # executor 已经为「打了上游的这一轮」写过流水（异常上带 audited），这里
        # 只补 preflight 拒绝（模型不在目录 / 消息形状不对 / 无可选 provider）。
        if not getattr(exc, "audited", False):
            await _audit(
                user,
                request_id=request_id,
                status="rejected",
                code=exc.code,
                http_status=exc.status_code,
            )
        return _agent_error(exc, request_id=request_id)
    except Exception:  # noqa: BLE001 - 兜底：协议体必须是 OpenAI 形状
        _logger.exception("ai agent run failed")
        await _audit(
            user,
            request_id=request_id,
            status="error",
            code="AI_AGENT_INTERNAL_ERROR",
            http_status=500,
        )
        return _error(
            500,
            "AI_AGENT_INTERNAL_ERROR",
            "agent run failed unexpectedly",
            request_id=request_id,
        )

    return JSONResponse(
        status_code=200, content=data, headers={"X-AI-Request-Id": request_id}
    )


@router.post("/proposals/{oid}/confirm")
async def confirm_proposal(
    oid: str,
    payload: ConfirmRequest | None = None,
    user=Depends(get_current_user),
):
    """确认并执行一条写工具提案。

    失败语义：``404`` 提案不存在（含 oid 形状不对）、``403`` 不是本人的提案 /
    双人码错误 / 无第二管理员、``409`` 已处理 / 参数与记录的哈希不符 / 动态码
    已被使用、``410`` 已过期、``422`` 缺动态码或其格式非法。
    """
    request_id = uuid4().hex

    if not _has_ai_access(user):
        return await _forbidden(user, request_id=request_id)

    if not PROPOSAL_OID_PATTERN.match(str(oid)):
        return _error(
            404,
            "AI_AGENT_PROPOSAL_NOT_FOUND",
            "proposal not found",
            request_id=request_id,
        )

    totp_code = payload.totp_code if payload is not None else None
    try:
        data = await proposal.confirm(oid=str(oid), user=user, totp_code=totp_code)
    except AgentError as exc:
        return _agent_error(exc, request_id=request_id)
    except Exception:  # noqa: BLE001 - 兜底：协议体必须是 OpenAI 形状
        _logger.exception("ai agent proposal confirm failed")
        await _audit(
            user,
            request_id=request_id,
            status="error",
            code="AI_AGENT_INTERNAL_ERROR",
            http_status=500,
        )
        return _error(
            500,
            "AI_AGENT_INTERNAL_ERROR",
            "proposal confirmation failed unexpectedly",
            request_id=request_id,
        )

    return JSONResponse(
        status_code=200, content=data, headers={"X-AI-Request-Id": request_id}
    )


@router.post("/proposals/{oid}/reject")
async def reject_proposal(oid: str, user=Depends(get_current_user)):
    """驳回一条提案（不执行任何写）。语义与 confirm 同一组状态码。"""
    request_id = uuid4().hex

    if not _has_ai_access(user):
        return await _forbidden(user, request_id=request_id)

    if not PROPOSAL_OID_PATTERN.match(str(oid)):
        return _error(
            404,
            "AI_AGENT_PROPOSAL_NOT_FOUND",
            "proposal not found",
            request_id=request_id,
        )

    try:
        data = await proposal.reject(oid=str(oid), user=user)
    except AgentError as exc:
        return _agent_error(exc, request_id=request_id)
    except Exception:  # noqa: BLE001 - 兜底：协议体必须是 OpenAI 形状
        _logger.exception("ai agent proposal reject failed")
        await _audit(
            user,
            request_id=request_id,
            status="error",
            code="AI_AGENT_INTERNAL_ERROR",
            http_status=500,
        )
        return _error(
            500,
            "AI_AGENT_INTERNAL_ERROR",
            "proposal rejection failed unexpectedly",
            request_id=request_id,
        )

    return JSONResponse(
        status_code=200, content=data, headers={"X-AI-Request-Id": request_id}
    )


@router.get("/proposals")
async def list_proposals(
    status: str | None = Query(default="proposed", max_length=12),
    limit: int = Query(default=20, ge=1, le=proposal.LIST_LIMIT_MAX),
    user=Depends(get_current_user),
):
    """本人的提案列表（``created_at`` 倒序；过期行顺手落 ``expired``）。

    ``limit`` 上限 50 是端点契约的一部分：这是给「我的待办」用的，不是导出接口。
    只查本人（``actor_oid``）——别人的提案连存在性都不该在这里暴露。
    """
    request_id = uuid4().hex

    if not _has_ai_access(user):
        return await _forbidden(user, request_id=request_id)

    try:
        items = await proposal.list_mine(
            user=user, status=status or None, limit=limit
        )
    except Exception:  # noqa: BLE001 - 兜底：协议体必须是 OpenAI 形状
        _logger.exception("ai agent proposal listing failed")
        await _audit(
            user,
            request_id=request_id,
            status="error",
            code="AI_AGENT_INTERNAL_ERROR",
            http_status=500,
        )
        return _error(
            500,
            "AI_AGENT_INTERNAL_ERROR",
            "proposal listing failed unexpectedly",
            request_id=request_id,
        )

    return JSONResponse(
        status_code=200,
        content={"proposals": items},
        headers={"X-AI-Request-Id": request_id},
    )


@router.get("/runs/{request_id}")
async def get_run(
    request_id: str,
    user=Depends(get_current_user),
):
    """一次 run 的工具事件时间线（刷新页面后回看用）。

    只回**本人**发起的那些行：``request_id`` 是 32 位 hex，猜中的成本和拿到别人
    的对话内容相比太低，不能靠「不可猜」当权限。查不到（或不是本人的）一律 404，
    不区分「不存在」与「不是你的」。
    """
    audit_id = uuid4().hex

    if not _has_ai_access(user):
        return await _forbidden(user, request_id=audit_id)

    if not PROPOSAL_OID_PATTERN.match(str(request_id)):
        return _error(
            404, "AI_AGENT_RUN_NOT_FOUND", "run not found", request_id=audit_id
        )

    try:
        events = await proposal.run_events(request_id=str(request_id), user=user)
    except Exception:  # noqa: BLE001 - 兜底：协议体必须是 OpenAI 形状
        _logger.exception("ai agent run timeline failed")
        await _audit(
            user,
            request_id=audit_id,
            status="error",
            code="AI_AGENT_INTERNAL_ERROR",
            http_status=500,
        )
        return _error(
            500,
            "AI_AGENT_INTERNAL_ERROR",
            "run timeline failed unexpectedly",
            request_id=audit_id,
        )

    if not events:
        return _error(
            404, "AI_AGENT_RUN_NOT_FOUND", "run not found", request_id=audit_id
        )

    return JSONResponse(
        status_code=200,
        content={"request_id": str(request_id), "events": events},
        headers={"X-AI-Request-Id": audit_id},
    )


__all__ = ["router"]
