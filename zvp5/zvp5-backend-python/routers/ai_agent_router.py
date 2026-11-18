"""agent（智能体）HTTP 层（``/api/ai/agent``）。

若干端点：确认提案、驳回提案、本人的提案列表，以及「首次使用安全警示已确认」
（``/ack``，V054）的读写。错误体与
``/v1/chat/completions`` 同形（``{"error": {"message", "type", "code"}}``）+
``X-AI-Request-Id`` 头，因为调用方是同一个前端 AI 封装，多一套形状只会多一处要
翻译的地方。

**发起 run 的端点已删除**（``POST /run``）与 ``GET /runs/{request_id}``：会话化
之后由 ``POST /api/ai/conversations/{oid}/runs`` 与
``GET /api/ai/runs/{run_id}`` 提供（契约 §3.3）。这里保留的只有提案的确认语义，
它不依赖 run 是否仍在队列里。

权限门禁与 chat 面同一口径（``AI_ALLOWED_ROLES``，默认 admin / department）：
agent 只是 chat 能力的高级用法，不改另一套白名单。门禁拒绝也落一行审计——「谁在
没有权限的情况下反复试探 agent」在后台必须看得见。

写操作的确认是**双人**的：``requires_dual_auth`` 的提案必须带另一位管理员的
6 位动态码（``totp_code``）。缺码 422，码错/已被用/无第二管理员 403/409，语义与
``util.dual_auth`` 逐字一致。
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from db import backends
from routers.ai.common import (
    AI_FORBIDDEN_MESSAGE,
    _error_type,
    _has_ai_access,
)
from services.agent import executor, proposal
from services.agent.tool_impl import AgentError
from services.ai import audit
from util import agent_unlock
from util.cert import verify_password_async
from util.object_id import get_current_user
from util.permission.features import ensure_feature

_logger = logging.getLogger(__name__)

router = APIRouter()

# 提案 oid 的形状：``uuid4().hex``（32 位小写 hex）。形状不对直接当「不存在」，
# 不查库——32 位 hex 的校验不需要一次往返。
PROPOSAL_OID_PATTERN = re.compile(r"^[0-9a-f]{32}$")


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


def _feature_denied(user: dict, key: str, *, request_id: str) -> JSONResponse | None:
    """功能开关门禁（``ROLE_FEATURES``）的拒绝体，与角色门禁同形。

    与 :func:`_forbidden` 的区别：这里**不**写 ``AI_CALL_LOGS``。角色白名单拒绝是
    「这个身份不该出现在 AI 面」，属于要审计的探测信号；开关拒绝只是管理员收起了
    某项能力，不该给运维刷噪音。

    位置恒在既有角色/密码判定**之后**：先角色（文案不变），再开关。
    """
    try:
        ensure_feature(user, key)
    except HTTPException as exc:
        return _error(
            exc.status_code, "AI_FORBIDDEN", str(exc.detail), request_id=request_id
        )
    return None


async def _require_unlock(user: dict, *, request_id: str) -> JSONResponse | None:
    """确认/驳回前的密码门；未解锁回 422（与双人码的 422 同形）。"""
    user_oid = str(user.get("id") or "")
    if await agent_unlock.is_unlocked(user_oid):
        return None
    await _audit(
        user,
        request_id=request_id,
        status="rejected",
        code="AI_AGENT_UNLOCK_REQUIRED",
        http_status=422,
    )
    return _error(
        422,
        "AI_AGENT_UNLOCK_REQUIRED",
        "agent access requires an unlock with your account password",
        request_id=request_id,
    )


class UnlockRequest(BaseModel):
    """密码门入参；空密码交给校验层判失败（不在这里 422 出另一套错误体）。"""

    password: str = Field(default="", max_length=200)


@router.post("/unlock")
async def unlock(payload: UnlockRequest | None = None, user=Depends(get_current_user)):
    """用本人账号密码换取 30 分钟 agent 授权。

    失败语义：``403 AI_AGENT_UNLOCK_FAILED`` 密码错误、``429
    AI_AGENT_UNLOCK_LOCKED`` 连错已锁（带 ``Retry-After``）。锁定判定在校验之前
    ——锁内即使输对也拒，否则锁定就只是「慢一点」。
    """
    request_id = uuid4().hex
    owner_oid = str(user.get("id") or "")

    # 解锁本身也要过开关：否则「用密码换了授权，却什么都干不了」——前端会拿着一
    # 个看起来成功的凭据去撞所有端点。放在锁定/密码判定之前，不给被收权的角色留
    # 一条「继续撞密码」的路径。
    denied = _feature_denied(user, "ai.agent", request_id=request_id)
    if denied is not None:
        return denied

    retry_after = await agent_unlock.unlock_retry_after(owner_oid)
    if retry_after > 0:
        return _error(
            429,
            "AI_AGENT_UNLOCK_LOCKED",
            "too many failed unlock attempts",
            request_id=request_id,
            headers={"Retry-After": str(retry_after)},
        )

    password = payload.password if payload is not None else ""
    hashed = None
    try:
        row = await backends.get_users_repo().get_by_oid(owner_oid)
        hashed = (row or {}).get("password")
    except Exception:  # noqa: BLE001 - 读库失败按「密码错误」处理，不泄露内部状态
        _logger.exception("读取用户密码哈希失败")
    authenticated = bool(hashed) and await verify_password_async(password, hashed)

    if not authenticated:
        if await agent_unlock.record_unlock_failure(owner_oid):
            return _error(
                429,
                "AI_AGENT_UNLOCK_LOCKED",
                "too many failed unlock attempts",
                request_id=request_id,
                headers={
                    "Retry-After": str(await agent_unlock.unlock_retry_after(owner_oid))
                },
            )
        return _error(
            403,
            "AI_AGENT_UNLOCK_FAILED",
            "password is incorrect",
            request_id=request_id,
        )

    unlocked_until = await agent_unlock.mark_unlocked(owner_oid)
    return JSONResponse(
        status_code=200,
        content={"unlocked_until": unlocked_until.isoformat()},
        headers={"X-AI-Request-Id": request_id},
    )


@router.get("/ack")
async def get_agent_ack(user=Depends(get_current_user)):
    """本人的「首次使用安全警示已确认」状态（``AI_AGENT_ACKS``, V054）。"""
    request_id = uuid4().hex

    if not _has_ai_access(user):
        return await _forbidden(user, request_id=request_id)

    denied = _feature_denied(user, "ai.agent", request_id=request_id)
    if denied is not None:
        return denied

    try:
        acked_at = await backends.get_ai_agent_acks_repo().get(
            str(user.get("id") or "")
        )
    except Exception:  # noqa: BLE001 - 兜底：协议体必须是 OpenAI 形状
        _logger.exception("ai agent ack lookup failed")
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
            "acknowledgement lookup failed unexpectedly",
            request_id=request_id,
        )

    return JSONResponse(
        status_code=200,
        content={
            "acked": acked_at is not None,
            "acked_at": acked_at.isoformat() if acked_at is not None else None,
        },
        headers={"X-AI-Request-Id": request_id},
    )


@router.post("/ack")
async def ack_agent_warning(user=Depends(get_current_user)):
    """确认已阅读首次使用安全警示。

    幂等：首次写库，重复调用不覆盖原 ``acked_at``，返回库里已有的值。并发的
    两次点击由仓储层的 ``MERGE`` + 主键保证只有一行、两边都拿到同一个时间。
    """
    request_id = uuid4().hex

    if not _has_ai_access(user):
        return await _forbidden(user, request_id=request_id)

    denied = _feature_denied(user, "ai.agent", request_id=request_id)
    if denied is not None:
        return denied

    try:
        acked_at = await backends.get_ai_agent_acks_repo().ack(
            str(user.get("id") or ""), datetime.now()
        )
    except Exception:  # noqa: BLE001 - 兜底：协议体必须是 OpenAI 形状
        _logger.exception("ai agent ack write failed")
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
            "acknowledgement failed unexpectedly",
            request_id=request_id,
        )

    return JSONResponse(
        status_code=200,
        content={"acked": True, "acked_at": acked_at.isoformat()},
        headers={"X-AI-Request-Id": request_id},
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

    denied = _feature_denied(user, "ai.agent", request_id=request_id)
    if denied is not None:
        return denied

    denied = await _require_unlock(user, request_id=request_id)
    if denied is not None:
        return denied

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

    denied = _feature_denied(user, "ai.agent", request_id=request_id)
    if denied is not None:
        return denied

    denied = await _require_unlock(user, request_id=request_id)
    if denied is not None:
        return denied

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

    denied = _feature_denied(user, "ai.agent", request_id=request_id)
    if denied is not None:
        return denied

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


__all__ = ["router"]
