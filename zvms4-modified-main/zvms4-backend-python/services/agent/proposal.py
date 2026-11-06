"""写工具的 human-in-the-loop：提案 → 用户确认 → 以用户权限执行。

模型**不能**直接写库。它对写工具的调用只会生成一条提案（``AI_AGENT_EVENTS`` 里
``status='proposed'``），真正的执行发生在用户点了确认之后，并且：

1. **归属**：提案只能由发起它的那个人确认（``actor_oid`` 比对）。换个人确认等于
   借用别人的身份执行自己提出的改动。
2. **一次**：状态迁移用 ``WHERE status = <预期>`` 做 CAS，第二次确认影响 0 行
   → 409，而不是把已经执行过的写入再跑一遍。
3. **时效**：``created_at + TTL`` 之后不可执行（→ 410 ``expired``）。「10 分钟前
   看到的那条改动」不该在半小时后被确认。
4. **内容一致**：``args_hash`` 覆盖**完整参数**（``ARGS_PAYLOAD``）的 canonical
   JSON，确认时重算比对。这一条防的是「批准的那份参数」与「真正执行的参数」不是
   同一份；``ARGS_SUMMARY`` 只用于展示，永不参与校验。
5. **执行层二次校验**：执行前**重新**用当前用户的权限判一次（工具注册表的
   ``permission``），运维工具还要过 ``guard``（现查库）。模型自称「用户已批准」
   不算凭据，数据库里的提案才算。
6. **双人授权**：``ToolDef.dual_auth_action`` 非空的提案，确认时必须带**另一位**
   管理员的 6 位动态码（``util.dual_auth.require_dual_auth``）。审批人落
   ``APPROVER_OID``，执行时间落 ``CONFIRMED_AT``——「谁批的、什么时候执行的」
   是这张表要回答的问题。

状态机（V034）：``proposed → executing → confirmed | failed``；驳回
``proposed → rejected``；过期 ``proposed → expired``。**全部走 CAS**，终态绝不会
被后到的迁移覆盖（并发确认 / 过期与确认撞车时，输掉竞态的那一方拿到 409/410）。

所有失败路径都落审计（``denied`` / ``rejected`` / ``expired`` / ``failed``），
因为「谁想改什么、被什么闸拦下」正是治理要回答的问题。
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

from db import backends
from services.ai import config as ai_config
from util import dual_auth

from . import tool_impl
from .tool_impl import AgentError
from .tools import TOOLS, WRITE, ToolDef, get_tool, prepare_arguments, run_allowed

_logger = logging.getLogger(__name__)

# ``ARGS_SUMMARY`` 的列宽（V033）。它**只是展示**：超过 500 就截断加 ``…``，
# 不再拒绝建提案——完整参数在 ``ARGS_PAYLOAD``（CLOB）里，校验用的也是它。
ARGS_SUMMARY_MAX = 500
# 可复原参数（ARGS_PAYLOAD, CLOB）的硬上限。正文类参数（fs_write.content）整段
# 落库，没有上限时一次提案就能把库和 diff 计算拖垮；512KB 是文件编辑上限，
# 1MB 给 JSON 转义留余量。
ARGS_PAYLOAD_MAX = 1_048_576
# ``ProposalDraft.summary`` 的上限（人读一句话）。超出截断，不报错。
DRAFT_SUMMARY_MAX = 200
RESULT_SUMMARY_MAX = 500

STATUS_PROPOSED = "proposed"
STATUS_EXECUTING = "executing"
STATUS_CONFIRMED = "confirmed"
STATUS_REJECTED = "rejected"
STATUS_EXPIRED = "expired"
STATUS_DENIED = "denied"
STATUS_FAILED = "failed"

# ``GET /proposals`` 一次最多返回多少条（契约 §1.4：limit ≤ 50）。
LIST_LIMIT_MAX = 50
# 批量 UPDATE 的单条 ``IN`` 上限（Oracle 表达式列表硬上限 1000，留余量）。
_EXPIRE_CHUNK = 900


@dataclass(frozen=True)
class ProposalDraft:
    """写工具的「确认界面」：一句人读摘要 + 结构化预览。

    ``preview`` 的形状由工具自己定（``diff`` / ``command`` / ``row_update`` …），
    原样落 ``PREVIEW``（CLOB）并回给前端；服务端不理解它，也不拿它做校验。
    """

    summary: str
    preview: dict


def _canonical(arguments: dict) -> str:
    """参数的唯一文本形态（键排序、无空白）：哈希与可复原存储都用它。"""
    return json.dumps(
        arguments, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _expires_at(created_at: datetime) -> datetime:
    return created_at + timedelta(seconds=ai_config.agent_proposal_ttl_seconds())


def _display_summary(canonical: str, limit: int = ARGS_SUMMARY_MAX) -> str:
    """``ARGS_SUMMARY``：完整参数的可读截断（超长加 ``…``，不报错）。"""
    if len(canonical) <= limit:
        return canonical
    return canonical[: limit - 1] + "…"


def redacted(value: Any) -> Any:
    """``ops_tools.redact``（存在时）的惰性包装：脱敏失败就原样返回。

    ``ARGS_SUMMARY`` 是**展示**列（契约 §4.2）：文件正文、SQL 参数值、命令输出
    里的 ``password`` / ``token`` 与超长字符串不能在这里留原文。完整原文在
    ``ARGS_PAYLOAD`` / ``PREVIEW`` 里——它们只在确认界面与重放时读。

    惰性导入：``ops_tools`` 依赖本模块（它要 :class:`ProposalDraft`），在模块顶层
    互相导入会成环；而这条路径每次提案只走一次，import 缓存之后是字典查找。
    """
    try:
        from .ops_tools import redact
    except Exception:  # noqa: BLE001 - 没有运维工具车道时（纯活动域）不脱敏
        return value
    try:
        return redact(value)
    except Exception:  # noqa: BLE001 - 脱敏是旁路，失败也要能建提案
        return value


def _display_arguments(arguments: dict) -> str:
    """``ARGS_SUMMARY`` 的最终形态：先脱敏、再 canonical、再截断。"""
    return _display_summary(_canonical(redacted(arguments) or {}))


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


async def _draft(tool: ToolDef, user: dict, kwargs: dict) -> ProposalDraft | None:
    """跑工具的 ``prepare`` 生成预览；没有 prepare 就返回 ``None``（调用方回落）。

    ``prepare`` 是工具自己的代码，可能查库/读文件（算 diff 要读现状），所以这里
    兜住**所有**异常：预览生成失败不该让「模型提了个写请求」变成 500 —— 回落成
    ``{"kind": "args"}`` 仍然让用户看见将要执行的参数，确认闸门一个都没少。
    """
    if tool.prepare is None:
        return None
    try:
        draft = await tool.prepare(user, **kwargs)
    except AgentError:
        raise
    except Exception:  # noqa: BLE001 - 预览是旁路，失败回落
        return None
    if isinstance(draft, ProposalDraft):
        return draft
    # 跨模块容错：允许 prepare 直接返回 ``{"summary":…, "preview":…}`` 形状。
    if isinstance(draft, dict) and isinstance(draft.get("preview"), dict):
        return ProposalDraft(
            summary=str(draft.get("summary") or ""),
            preview=draft["preview"],
        )
    return None


async def propose(
    *,
    user: dict,
    tool: ToolDef,
    arguments: dict,
    request_id: str,
    round_no: int | None,
) -> dict:
    """为一次写工具调用生成提案（不执行任何写）。

    返回 ``{oid, tool_name, summary, args_summary, preview, expires_at,
    requires_dual_auth}``：``oid`` 就是后续 ``/proposals/{oid}/confirm`` 的句柄。
    """
    if tool.kind != WRITE:
        raise AgentError(
            "AI_AGENT_NOT_WRITE_TOOL", "only write tools can be proposed", 400
        )

    canonical = _canonical(arguments)
    if len(canonical) > ARGS_PAYLOAD_MAX:
        # 正文（如 fs_write 的 content）会整段落进 CLOB：没有上限时一次提案就能
        # 把库和 diff 计算拖垮。512KB 是文件编辑上限，1MB 给 JSON 转义留余量。
        raise AgentError(
            "AI_AGENT_ARGS_TOO_LARGE",
            f"tool arguments exceed {ARGS_PAYLOAD_MAX} characters",
            413,
        )
    args_summary = _display_arguments(arguments)
    draft = await _draft(tool, user, arguments)
    if draft is None:
        preview: dict = {"kind": "args", "args": arguments}
        summary = f"{tool.name}：{_clip(args_summary, DRAFT_SUMMARY_MAX - 40)}"
    else:
        preview = dict(draft.preview)
        summary = _clip(draft.summary or tool.name, DRAFT_SUMMARY_MAX)

    oid = uuid4().hex
    created_at = datetime.now()
    pending = _clip(
        f"等待确认：{summary}（{ai_config.agent_proposal_ttl_seconds()} 秒内有效）",
        RESULT_SUMMARY_MAX,
    )
    await backends.get_ai_agent_events_repo().insert(
        oid=oid,
        request_id=request_id,
        round_no=round_no,
        tool_name=tool.name,
        tool_kind=WRITE,
        args_hash=_hash(canonical),
        args_summary=args_summary,
        args_payload=canonical,
        preview=json.dumps(preview, sort_keys=True, ensure_ascii=False),
        result_summary=pending,
        status=STATUS_PROPOSED,
        actor_oid=user["id"],
    )
    return {
        "oid": oid,
        "tool_name": tool.name,
        "summary": summary,
        "args_summary": args_summary,
        "preview": preview,
        "expires_at": _expires_at(created_at).isoformat(sep=" ", timespec="seconds"),
        "requires_dual_auth": bool(tool.dual_auth_action),
    }


async def _load_proposal(oid: str, user: dict) -> dict:
    """取提案并做前三道闸：存在 → 归属 → 状态 → 时效。

    顺序是刻意的：先判归属再判状态，避免「用 409 探出别人的提案存在」这种信息
    泄露；而过期是**数据本身**的性质，与是谁无关，放在归属之后仍然只回给本人。
    """
    row = await backends.get_ai_agent_events_repo().get(str(oid))
    if row is None:
        raise AgentError("AI_AGENT_PROPOSAL_NOT_FOUND", "proposal not found", 404)

    if str(row.get("actor_oid") or "") != str(user.get("id") or ""):
        raise AgentError(
            "AI_AGENT_PROPOSAL_FORBIDDEN", "this proposal belongs to another user", 403
        )

    if row.get("status") != STATUS_PROPOSED:
        raise AgentError(
            "AI_AGENT_PROPOSAL_CONFLICT",
            f"proposal is already {row.get('status')}",
            409,
        )

    created_at = row.get("created_at")
    if not isinstance(created_at, datetime):
        # 脏数据（NULL / 非时间）：当作过期处理比「永不过期」安全。
        await _mark_expired(str(oid))
        raise AgentError(
            "AI_AGENT_PROPOSAL_EXPIRED", "proposal has expired", 410
        )

    if datetime.now() >= _expires_at(created_at):
        await _mark_expired(str(oid))
        raise AgentError("AI_AGENT_PROPOSAL_EXPIRED", "proposal has expired", 410)

    return row


async def _mark_expired(oid: str) -> None:
    """把提案落成 ``expired``（幂等，但**只从 ``proposed``**）。

    CAS 而不是无条件更新：过期判定与确认执行可能同时发生，无条件写会把已经
    ``confirmed`` / ``executing`` 的提案改回 ``expired``——那等于让「已执行的写」
    在审计里看起来从未发生。输掉竞态时影响 0 行，视同「别人已经处理了」。
    """
    try:
        await backends.get_ai_agent_events_repo().update_status(
            oid,
            STATUS_EXPIRED,
            from_status=STATUS_PROPOSED,
            result_summary="提案已过期（未执行）",
        )
    except Exception:  # noqa: BLE001 - 审计写失败不该把 410 变成 500
        pass


def _stored_arguments(row: dict) -> str:
    """取「参与校验的那份参数文本」：优先 ``ARGS_PAYLOAD``，旧行回落 summary。"""
    payload = row.get("args_payload")
    if isinstance(payload, str) and payload:
        return payload
    summary = row.get("args_summary")
    if isinstance(summary, str) and summary:
        return summary
    raise AgentError(
        "AI_AGENT_PROPOSAL_TAMPERED", "proposal arguments are missing", 409
    )


def _decode_arguments(row: dict) -> dict:
    """把落库的参数复原成 dict，并用 ``ARGS_HASH`` 校验一致性。

    ``args_hash`` 覆盖的是**存下来的完整文本**：只要两者对得上，就说明执行用的
    参数与用户确认的是同一份（这是「批准 ≠ 执行」的唯一证据链）。V034 之前建的
    提案没有 ``ARGS_PAYLOAD``，回落到 ``ARGS_SUMMARY``——当时的 hash 就是它的
    哈希，老提案因此继续可确认。
    """
    text = _stored_arguments(row)
    if _hash(text) != row.get("args_hash"):
        raise AgentError(
            "AI_AGENT_PROPOSAL_TAMPERED",
            "proposal arguments do not match the recorded hash",
            409,
        )
    try:
        arguments = json.loads(text)
    except ValueError:
        raise AgentError(
            "AI_AGENT_PROPOSAL_TAMPERED", "proposal arguments are not valid json", 409
        ) from None
    if not isinstance(arguments, dict):
        raise AgentError(
            "AI_AGENT_PROPOSAL_TAMPERED", "proposal arguments are not an object", 409
        )
    return arguments


def _summary(value: object, limit: int = RESULT_SUMMARY_MAX) -> str:
    text = tool_impl.to_json_text(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


# ``dual_auth`` 的拒绝语义逐字照抄（401/403/409/422/429），只换一个可编程的错误码。
_DUAL_AUTH_CODES = {
    403: "AI_AGENT_TOTP_INVALID",
    409: "AI_AGENT_TOTP_USED",
    422: "AI_AGENT_TOTP_INVALID",
    429: "AI_AGENT_TOTP_RATE_LIMITED",
}


async def _require_dual_auth(
    tool: ToolDef, user: dict, totp_code: str | None
) -> tuple[str | None, bool]:
    """运维写工具的 TOTP 闸；返回 ``(审批人 oid, 是否命中 30 分钟缓存)``。

    活动域工具没有 ``dual_auth_action`` → ``(None, False)``。
    窗口内的提案**不再要求**动态码（见 ``util.dual_auth.require_dual_auth_cached``）：
    同一 operator 的同一 action 刚被另一位管理员批准过，重复输码只是摩擦。
    """
    action = tool.dual_auth_action
    if not action:
        return None, False
    code = totp_code.strip() if isinstance(totp_code, str) else ""
    if not code and await dual_auth.cached_approval(user, action=action) is None:
        raise AgentError(
            "AI_AGENT_TOTP_REQUIRED",
            "this proposal requires a 6-digit code from another administrator",
            422,
        )
    try:
        return await dual_auth.require_dual_auth_cached(user, code, action=action)
    except dual_auth.DualAuthError as exc:
        raise AgentError(
            _DUAL_AUTH_CODES.get(exc.status_code, "AI_AGENT_TOTP_INVALID"),
            exc.detail,
            exc.status_code,
        ) from None


async def _deny(oid: str, reason: str) -> None:
    """把提案落成 ``denied``（只从 ``proposed``，CAS）。"""
    try:
        await backends.get_ai_agent_events_repo().update_status(
            str(oid),
            STATUS_DENIED,
            from_status=STATUS_PROPOSED,
            result_summary=reason,
        )
    except Exception:  # noqa: BLE001 - 状态写失败不该改变 403 的结论
        pass


async def confirm(
    *, oid: str, user: dict, totp_code: str | None = None
) -> dict:
    """确认并执行一条提案。

    先抢占状态（``proposed → executing``）再执行：CAS 成功之后才动数据，这样并发
    确认只有一个赢家。执行失败则把状态落成 ``failed`` 并带回原因——**不**回滚成
    ``proposed``：让用户看到「这次失败了」，比让它看起来从未发生过更接近事实。
    """
    row = await _load_proposal(oid, user)
    arguments = _decode_arguments(row)

    tool = get_tool(row.get("tool_name"))
    if tool is None or tool.kind != WRITE:
        # 注册表是封闭集合：提案里的工具名可能来自旧版本（或被人改过）。
        raise AgentError(
            "AI_AGENT_PROPOSAL_UNKNOWN_TOOL",
            "proposal references an unknown tool",
            409,
        )

    if not run_allowed(tool, user):
        await _deny(str(oid), "执行时权限校验未通过（未执行）")
        raise AgentError(
            "AI_AGENT_PROPOSAL_DENIED",
            "you are not allowed to run this tool",
            403,
        )

    if tool.guard is not None:
        try:
            await tool.guard(user)
        except AgentError as exc:
            await _deny(str(oid), f"执行时权限校验未通过：{exc.message}"[:RESULT_SUMMARY_MAX])
            raise AgentError(
                "AI_AGENT_PROPOSAL_DENIED", exc.message, exc.status_code
            ) from None
        except Exception:  # noqa: BLE001 - 权限闸抛错按拒绝处理（fail closed）
            await _deny(str(oid), "执行时权限校验未通过（现查库失败）")
            raise AgentError(
                "AI_AGENT_PROPOSAL_DENIED",
                "you are not allowed to run this tool",
                403,
            ) from None

    # 二次校验参数：即使 hash 对得上，也要按当前 schema 再收敛一次（schema 可能
    # 在提案生成后收紧了，比如某个枚举删掉了一个值）。
    kwargs = prepare_arguments(tool, arguments)

    # 双人授权在**抢状态之前**：错码不该把提案烧成 executing/failed，用户换一个
    # 正确的码还能继续确认（dual_auth 自带防重放与失败限流）。
    approver_oid, dual_auth_cached = await _require_dual_auth(tool, user, totp_code)

    repo = backends.get_ai_agent_events_repo()
    claimed = await repo.update_status(
        str(oid),
        STATUS_EXECUTING,
        from_status=STATUS_PROPOSED,
        result_summary="执行中",
        approver_oid=approver_oid,
    )
    if not claimed:
        raise AgentError(
            "AI_AGENT_PROPOSAL_CONFLICT", "proposal was already handled", 409
        )

    try:
        with tool_impl.operator_context(user):
            result = await tool.impl(user, **kwargs)
    except AgentError as exc:
        await repo.update_status(
            str(oid),
            STATUS_FAILED,
            from_status=STATUS_EXECUTING,
            result_summary=_summary({"error": exc.code}),
        )
        raise
    except Exception as exc:  # noqa: BLE001 - 执行失败要落审计，但细节不外泄
        await repo.update_status(
            str(oid),
            STATUS_FAILED,
            from_status=STATUS_EXECUTING,
            result_summary=_summary({"error": type(exc).__name__}),
        )
        raise AgentError(
            "AI_AGENT_TOOL_FAILED", "tool execution failed", 502
        ) from None

    summary = _summary(result)
    await repo.update_status(
        str(oid),
        STATUS_CONFIRMED,
        from_status=STATUS_EXECUTING,
        result_summary=summary,
        confirmed_at=datetime.now(),
    )
    return {
        "status": STATUS_CONFIRMED,
        "result_summary": summary,
        "approver_oid": approver_oid,
        "dual_auth_cached": dual_auth_cached,
    }


async def reject(*, oid: str, user: dict) -> dict:
    """驳回一条提案（不执行任何写）。"""
    row = await _load_proposal(oid, user)
    if row.get("status") != STATUS_PROPOSED:  # _load_proposal 已挡，保留可读性
        raise AgentError(
            "AI_AGENT_PROPOSAL_CONFLICT", "proposal was already handled", 409
        )

    updated = await backends.get_ai_agent_events_repo().update_status(
        str(oid),
        STATUS_REJECTED,
        from_status=STATUS_PROPOSED,
        result_summary="用户驳回了该提案（未执行）",
    )
    if not updated:
        raise AgentError(
            "AI_AGENT_PROPOSAL_CONFLICT", "proposal was already handled", 409
        )
    return {"status": STATUS_REJECTED}


def _requires_dual_auth(tool_name: object) -> bool:
    tool = get_tool(tool_name)
    return bool(tool is not None and tool.dual_auth_action)


def _preview_of(row: dict) -> dict | None:
    raw = row.get("preview")
    if isinstance(raw, str) and raw:
        try:
            parsed = json.loads(raw)
        except ValueError:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def _list_item(row: dict, *, cached_dual_auth: bool = False) -> dict:
    created_at = row.get("created_at")
    expires_at = (
        _expires_at(created_at).isoformat(sep=" ", timespec="seconds")
        if isinstance(created_at, datetime)
        else None
    )
    return {
        "oid": str(row.get("_id") or row.get("oid") or ""),
        "tool_name": row.get("tool_name"),
        "summary": row.get("result_summary"),
        "args_summary": row.get("args_summary"),
        "preview": _preview_of(row),
        "status": row.get("status"),
        "expires_at": expires_at,
        "requires_dual_auth": _requires_dual_auth(row.get("tool_name")),
        "dual_auth_cached": cached_dual_auth,
        "created_at": (
            created_at.isoformat(sep=" ", timespec="seconds")
            if isinstance(created_at, datetime)
            else None
        ),
    }


def _row_action(row: dict) -> str | None:
    """工具 → 双人授权 action（没有该字段的工具返回 ``None``）。"""
    tool = get_tool(row.get("tool_name"))
    return tool.dual_auth_action if tool is not None else None


async def _dual_auth_flags(user: dict, rows: list[dict]) -> dict[str, bool]:
    """一次取完这一页需要的「有没有在窗口内的授权」，返回 ``{action: bool}``。

    历史写法是每行一次 ``cached_approval``（一次 Redis GET）：200 行的列表就是
    200 次往返，而 **action 的取值只有工具注册表那么几个**（当前所有写工具共用
    ``agent_write``）。按 action 去重后一页最多几次 GET，等价于 MGET 的往返数，
    又不必在服务层复刻 ``dual_auth`` 的键名布局（那样会导致两处键格式漂移）。
    """
    actions = {action for row in rows if (action := _row_action(row)) is not None}
    if not actions:
        return {}
    return {
        action: await dual_auth.cached_approval(user, action=action) is not None
        for action in actions
    }


async def _expire_many(oids: list[str]) -> None:
    """把一批提案一次落成 ``expired``（``WHERE status='proposed'`` 仍然是 CAS）。

    逐条 ``update_status`` = 每条一次 UPDATE + 一次 COMMIT（跨洋 2×138ms）；
    列表页一次最多 200 条，光是「标记过期」就能把一个 GET 拖成十几秒。
    这里合成一条 ``UPDATE ... WHERE oid IN (...) AND status='proposed'``：
    CAS 语义不变（抢不到已 ``confirmed`` / ``executing`` 的行），行数上限按
    Oracle 的 ``IN`` 上限分块。
    """
    if not oids:
        return
    repo = backends.get_ai_agent_events_repo()
    if not hasattr(repo, "_session"):
        # 测试替身 / 非 Oracle 实现：没有 caller-owned session 可借，走公开 API。
        for oid in oids:
            await _mark_expired(oid)
        return
    try:
        # 延迟导入：与 services/agent/tools_*.py 的写法一致（不在模块导入时建引擎）。
        from sqlalchemy import update

        from db.models import ai_agent_events as events_tbl
        from db.oracle.session import session_scope

        async with session_scope() as session:
            for start in range(0, len(oids), _EXPIRE_CHUNK):
                chunk = oids[start : start + _EXPIRE_CHUNK]
                await session.execute(
                    update(events_tbl)
                    .where(
                        events_tbl.c.oid.in_(chunk),
                        events_tbl.c.status == STATUS_PROPOSED,
                    )
                    .values(
                        status=STATUS_EXPIRED,
                        result_summary="提案已过期（未执行）",
                    )
                )
            await session.commit()
    except Exception as exc:  # noqa: BLE001 - 审计写失败不该把 200 变成 500
        _logger.warning("bulk expire of %d proposals failed: %r", len(oids), exc)
        for oid in oids:
            await _mark_expired(oid)


async def list_mine(
    *, user: dict, status: str | None = None, limit: int = 20
) -> list[dict]:
    """本人（``actor_oid``）的提案列表，``created_at`` 倒序。

    顺手把已过期的行落成 ``expired``：它们本来就不能再确认，留在 ``proposed``
    列表里只会让「待办」越堆越多。落库走 CAS，撞上并发确认时不影响别人的终态。
    """
    limit = max(1, min(int(limit or 20), LIST_LIMIT_MAX))
    wanted = status or None
    # 多取一些：过期行会被剔除，刚好取 limit 条会让「后面还有有效提案」看不见。
    rows = await backends.get_ai_agent_events_repo().list_by_actor(
        str(user.get("id") or ""),
        status=wanted,
        limit=min(limit + LIST_LIMIT_MAX, 200),
    )

    now = datetime.now()
    keep: list[dict] = []
    expired: list[str] = []
    for row in rows:
        if row.get("status") == STATUS_PROPOSED:
            created_at = row.get("created_at")
            if not isinstance(created_at, datetime) or now >= _expires_at(created_at):
                expired.append(str(row.get("_id") or row.get("oid") or ""))
                continue
        keep.append(row)
    # 先收集、后一次写：过期判定是纯本地计算，不值得为每条行各开一个事务。
    await _expire_many(expired)

    flags = await _dual_auth_flags(user, keep[:limit])
    out: list[dict] = []
    for row in keep:
        action = _row_action(row)
        out.append(
            _list_item(row, cached_dual_auth=bool(action and flags.get(action)))
        )
        if len(out) >= limit:
            break
    return out[:limit]


async def run_events(
    *, request_id: str, user: dict, rows: list[dict] | None = None
) -> list[dict]:
    """一次 run 的工具事件时间线（只回本人的行）。

    ``rows`` 允许调用方把已经取到的 ``list_for_request`` 结果传进来：run 详情同时
    要事件与提案，两处各查一遍同一张表是一次纯粹的重复往返。
    """
    if rows is None:
        rows = await backends.get_ai_agent_events_repo().list_for_request(str(request_id))
    actor = str(user.get("id") or "")
    rows = [row for row in rows if str(row.get("actor_oid") or "") == actor]
    return [
        {
            "oid": str(row.get("_id") or ""),
            "round_no": row.get("round_no"),
            "tool_name": row.get("tool_name"),
            "tool_kind": row.get("tool_kind"),
            "args_summary": row.get("args_summary"),
            "result_summary": row.get("result_summary"),
            "status": row.get("status"),
            "created_at": (
                row["created_at"].isoformat(sep=" ", timespec="seconds")
                if isinstance(row.get("created_at"), datetime)
                else None
            ),
        }
        for row in rows
    ]


def write_tools() -> list[ToolDef]:
    """全部写工具（供文档/测试核对「谁需要确认」）。"""
    return [tool for tool in TOOLS.values() if tool.kind == WRITE]


__all__ = [
    "ARGS_SUMMARY_MAX",
    "LIST_LIMIT_MAX",
    "ProposalDraft",
    "confirm",
    "list_mine",
    "propose",
    "redacted",
    "reject",
    "run_events",
    "write_tools",
]
