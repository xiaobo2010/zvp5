"""只读场景面（``/api/ai/scenes``）：终端 / 文件编辑的一次提问。

两个场景共用会话 + run 管线（``run_queue.submit_scene`` → 同一个泵、同一套计费与
审计），差别只有三处，且都落在服务层：

* 会话按 ``(user, scene)`` 复用，标题固定「终端智能」/「文件编辑智能」；
* 工具声明按 ``kind='read'`` 裁剪（执行器侧），写工具、提案流、``ssh_exec`` 与
  数据库 DML/DDL 都不存在；
* 场景会话不出现在普通会话列表里（``AI_CONVERSATIONS.SCENE IS NULL`` 过滤）。

门禁：``admin`` + ``ai.agent`` 功能开关。**故意不挂密码门**（30 分钟解锁）：只读
场景的风险面是「能看」，不是「能改」，而 ``submit`` 里也把这条豁免写成了
「只读场景 + admin」的显式条件（测试钉住），不是靠路由层不调用。

协议壳与 ``/api/ai/conversations`` 逐字一致（``{"status","code","data"}`` / 错误体
``{"error":{...}}`` + ``X-AI-Request-Id``），前端同一套解析。
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from routers.ai_router import AI_FORBIDDEN_MESSAGE, _error_type, _has_ai_access
from services.agent import run_queue
from services.agent.tool_impl import AgentError
from services.ai import audit, conversations
from typings.log import xuehai_id_from
from util.object_id import get_current_user
from util.permission.features import ensure_feature

_logger = logging.getLogger(__name__)

router = APIRouter()

#: 审计里的端点名（与挂载前缀一致，便于和普通会话面分开统计）。
ENDPOINT_SCENES = "/api/ai/scenes"
AGENT_FEATURE = "ai.agent"


class SceneRunCreate(BaseModel):
    """一次场景提问。``content`` 是问题，``context`` 是选中的终端输出/文件片段。

    两个上限都是**入参**上限：``content`` 比普通会话窄（场景是一问一查，不是写
    长文），``context`` 与留存上限同量级——再长的「参考内容」只会挤占上下文。

    ``model`` / ``thinking`` 是**按次覆盖**（空值 = 沿用会话现有值）：场景会话是
    「一问一查」的，卡片上换模型/档位不该改写那条固定会话——否则下一次提问会莫名
    沿用上一次的临时选择。档位合法性在端点里判（400 中文），不在这里用 pydantic
    枚举：那会变成 422，前端拿不到可读的中文原因。
    """

    content: str = Field(min_length=1, max_length=4000)
    context: str | None = Field(default=None, max_length=8000)
    model: str | None = Field(default=None, max_length=120)
    thinking: str | None = Field(default=None, max_length=16)


def _clean_thinking(value: object) -> str | None:
    """思考强度归一：``low`` / ``medium`` / ``high``，空串/None → ``None``。

    未知档位一律 400（不静默退默认）：前端拼错一个档位时，静默成功会让人以为设置
    生效了，而实际行为一直是默认。判定口径与会话侧（``conversations``）同一份
    常量，只是文案用中文。
    """
    text = str(value or "").strip().lower()
    if not text:
        return None
    if text not in conversations.THINKING_LEVELS:
        raise AgentError(
            "AI_BAD_REQUEST", "思考强度只能是 low、medium、high，或留空", 400
        )
    return text


def _error(
    status_code: int,
    code: str,
    message: str,
    *,
    request_id: str,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "message": message,
                "type": _error_type(status_code),
                "code": code,
            }
        },
        headers={"X-AI-Request-Id": request_id},
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
    await audit.record(
        user_oid=str(user.get("id") or ""),
        capability="chat",
        endpoint=ENDPOINT_SCENES,
        status=status,
        http_status=http_status,
        error_code=code,
        latency_ms=0,
        request_id=request_id,
        purpose=scene,
        roles_snapshot=audit.roles_snapshot(user.get("per")),
    )


def _require_admin(user: dict) -> None:
    """严格 admin：只读场景能读到站点文件与库里数据，不该按「AI 可用角色」放行。"""
    if "admin" not in (user.get("per") or []):
        raise AgentError("AI_FORBIDDEN", AI_FORBIDDEN_MESSAGE, 403)


def _require_feature(user: dict) -> None:
    """功能开关拒绝不落审计：那是管理员收起了能力，不是探测信号。"""
    try:
        ensure_feature(user, AGENT_FEATURE)
    except HTTPException as exc:
        raise AgentError("AI_FORBIDDEN", str(exc.detail), 403) from exc


async def _dispatch(
    action: Callable[[], Awaitable[Any]],
    *,
    user: dict,
    scene: str | None,
) -> JSONResponse:
    request_id = uuid4().hex
    if not _has_ai_access(user):
        await _audit(
            user,
            scene=scene,
            request_id=request_id,
            status="rejected",
            code="AI_FORBIDDEN",
            http_status=403,
        )
        return _error(403, "AI_FORBIDDEN", AI_FORBIDDEN_MESSAGE, request_id=request_id)
    try:
        data = await action()
    except AgentError as exc:
        return _error(
            exc.status_code, exc.code, exc.message, request_id=request_id
        )
    except Exception:  # noqa: BLE001 - 兜底：协议体必须是可解析的形状
        _logger.exception("ai scene request failed")
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
            "scene request failed unexpectedly",
            request_id=request_id,
        )
    return JSONResponse(
        status_code=202,
        content={"status": "ok", "code": 202, "data": data},
        headers={"X-AI-Request-Id": request_id},
    )


@router.post("/scenes/{scene}/runs")
async def submit_scene_run(
    scene: str,
    payload: SceneRunCreate,
    request: Request,
    user=Depends(get_current_user),
):
    """提交一次只读场景 run：立刻 202，用既有 ``GET /ai/runs/{run_id}`` 轮询。

    门禁顺序：admin → 场景枚举 → 功能开关 → 入队。枚举先于开关，是因为「场景名
    拼错」是参数错误（400），不是「能力被关掉」（403）；两者混成一个码，运维会
    去查开关配置而问题其实在 URL 里。
    """

    normalized = str(scene or "").strip().lower()
    xuehai_id = xuehai_id_from(request)

    async def action() -> dict:
        _require_admin(user)
        if normalized not in conversations.SCENES:
            raise AgentError(
                conversations.SCENE_BAD, "scene must be terminal or file_edit", 400
            )
        thinking = _clean_thinking(payload.thinking)
        model = str(payload.model or "").strip() or None
        _require_feature(user)
        data = await run_queue.submit_scene(
            user=user,
            scene=normalized,
            content=payload.content,
            context=payload.context,
            xuehai_id=xuehai_id,
            model=model,
            thinking=thinking,
        )
        await run_queue.kick()
        return data

    return await _dispatch(action, user=user, scene=normalized or None)


__all__ = ["router"]
