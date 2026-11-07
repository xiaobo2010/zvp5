"""运维工具的权限闸：**复用**系统管理端点的判定，不另写一份。

契约（``docs/AI_AGENT_OPS_DESIGN.md`` §0.5）：工具执行前（读）与提案确认时（写）
都要现查库——``per`` 含 admin → 查用户 → 封禁是否**此刻**生效 → 重新算权限。
token 里的 ``per`` 只是快照，撤权与封禁必须立刻生效。

唯一实现是 ``routers.system_router._require_system_admin``：维护模式、数据库内容
控制台、SSH 桥都共用它。这里只做**错误协议翻译**（``HTTPException`` →
``AgentError``），一旦复制权限逻辑，两处就会各自漂移。
"""

from __future__ import annotations

from fastapi import HTTPException

from routers import system_router

from .tool_impl import AgentError

# ``util.ban.raise_banned`` 的 detail 就是这个字面量（403 + 该 detail + 原因/到期头），
# 平台前端已按它分流到「账号封禁」页，所以原样透传成 AgentError 的 code。
BANNED_DETAIL = "USER_BANNED"


def _code_of(detail: str) -> str:
    return "USER_BANNED" if detail == BANNED_DETAIL else "AI_FORBIDDEN"


async def ensure_system_admin(user: dict) -> None:
    """现查库确认「此刻仍是系统管理员」，否则抛 :class:`AgentError`。

    失败口径：

    * 非 admin / 查不到用户 / 权限被回收 → 403 ``AI_FORBIDDEN``；
    * 封禁生效 → 403 ``USER_BANNED``（带 ``ban_headers`` 的原因/到期）；
    * 任何**非预期**异常（仓储炸了、字段缺失）→ 403 ``AI_FORBIDDEN``。

    最后一条是 fail closed：闸门自己坏掉时按拒绝处理，绝不放行——读工具会因此
    拿不到数据，这比「权限检查失效时仍然放行」可接受得多。
    """
    try:
        await system_router._require_system_admin(user)
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
        raise AgentError(
            _code_of(detail),
            detail,
            exc.status_code,
            headers=dict(exc.headers or {}),
        ) from None
    except AgentError:
        raise
    except Exception:  # noqa: BLE001 - 闸门故障按拒绝处理（fail closed）
        raise AgentError("AI_FORBIDDEN", "权限不足", 403) from None


__all__ = ["BANNED_DETAIL", "ensure_system_admin"]
