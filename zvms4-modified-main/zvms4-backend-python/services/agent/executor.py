"""agent 执行循环（OpenAI function-calling，逐轮计费、逐轮审计）。

一轮 = 一次上游调用。循环这么走：

1. run 级截止时间检查 + 频控 / 额度预检（复用 ``services.ai.rate_limit`` /
   ``quota``，与 ``/v1/chat/completions`` 同一套机制，不另造）；
2. ``providers.chat_completion``（**强制** ``stream=False``：流式下没法在工具调用
   之前拿到完整的 ``tool_calls``，而 agent 的下一步取决于它）；
3. 有 ``tool_calls`` → 逐个过白名单 + 权限（+ 现查库的 ``guard``）→ 执行（读）或
   生成提案（写）→ 结果按 OpenAI 的 ``role="tool"`` 消息追加 → 进入下一轮；
   ``direct=True`` 的写工具（仅封禁/解封这一类，见 ``tools.DIRECT_WRITE_TOOL_NAMES``）
   走的是「执行」而不是「提案」：巡检 agent 需要在管理员上线之前就把灌爆接口的
   账号关掉，但仍然要过同一套 permission + guard + 参数收敛。
4. 没有 ``tool_calls`` → 终止，返回 ``content``。

四个成本 / 时间闸：

* ``AI_AGENT_MAX_ITERATIONS``（默认 10）：轮数上限，防「模型反复调工具不收敛」；
* ``AI_AGENT_TOKEN_BUDGET``（默认 200000）：累计 token 上限，防「单轮很长 × 很多轮」；
  **在工具执行之前**判——超预算的那一轮不该再落任何业务写入；
* ``AI_AGENT_RUN_TIMEOUT_SECONDS``（默认 300）：一次 run 的 wall-clock 上限。超时
  **不丢已有结果**：返回当前 ``content``（可能为 ""）+ ``stop_reason="timeout"``，
  并落一行 ``status='timeout'`` 审计；
* 既有的分钟频控与月额度：每一轮都各查一次、各扣一次。

**一次 run 的每一轮都是一次独立计费**：每轮写一条 ``AI_CALL_LOGS``
（``purpose='agent'``、``round_no`` 递增、``request_id`` 全轮共用一个），并按该轮
的 usage 扣一次 chat 额度。所以配额面板上「10 轮的一次 run」看起来是 10 次调用，
这是有意的——按真实上游成本记账，而不是按用户点了几个按钮。

为什么要 ``purpose='agent'`` 而不是新增 capability：``AI_CALL_LOGS.CAPABILITY``
的 CHECK 只有 ``ocr`` / ``chat``（V024），agent 是 chat 能力的高级用法，场景由
``purpose`` 区分即可；加枚举要一整轮 DDL + 读侧改造，而两者额度口径本来就一样。

服务端 system 段（:mod:`services.agent.prompt`）固定在最前，``tools`` 按 name 排序：
「前缀稳定」是缓存命中的前提，而动态内容（工具结果、用户消息）只追加在尾部。
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import math
import time
from typing import Any, Callable
from uuid import uuid4

import settings
from db import backends
from services import risk_monitor
from services.ai import audit, credentials, providers, quota, rate_limit, routing
from services.ai import config as ai_config
from services.ai import prompt_audit, prompt_cache
from services.ai import usage as ai_usage
from services.ai.credentials import CredentialError
from services.ai.routing import RouteError

from . import context as agent_context
from . import proposal, tool_impl
from .prompt import PROMPT_VERSION, SYSTEM_PROMPT
from .tool_impl import AgentError
from .tools import WRITE, get_tool, prepare_arguments, run_allowed, tool_specs

_logger = logging.getLogger(__name__)

# 与 ``routers.ai_router`` 同值：一次上游调用的最多候选数与可重试错误码。
MAX_PROVIDER_ATTEMPTS = 2
RETRYABLE_CODES = frozenset(
    {"AI_UPSTREAM_RATE_LIMIT", "AI_UPSTREAM_ERROR", "AI_TIMEOUT"}
)

# 单个工具结果塞回对话的上限（字符）。活动列表一页 100 条很容易超；截断并注明
# 比让上游 400（context 超限）好，但必须让模型知道「这不是全部」。
MAX_TOOL_RESULT_CHARS = 8000

# 单轮最多执行几个工具调用。模型偶尔会一次吐十几个调用（尤其是被要求"批量"时），
# 一次 run 的成本与副作用面都会失控；超出部分不执行、只回一条说明。
MAX_TOOL_CALLS_PER_ROUND = 5

# run 级 wall-clock 上限（秒）。配在 ``settings`` 里（``AI_AGENT_RUN_TIMEOUT_SECONDS``）：
# ``services.ai.config`` 不归本模块维护，这里直接读 settings 并自己做正整数兜底。
DEFAULT_RUN_TIMEOUT_SECONDS = 300

# 目的标记：``AI_CALL_LOGS.PURPOSE`` 是开放集合，这里是 agent 的唯一取值。
PURPOSE_AGENT = "agent"

# 压缩专用场景标记（契约 §5.4）：与 ``agent`` 分开，便于对账「压缩成本」，
# 也让 ``round_no=NULL`` 这件事在报表里自解释（它不是某一轮的工具循环）。
PURPOSE_COMPACT = "compact"

# 压缩失败时的候选重试次数（§5.5：换候选重试 1 次 = 共 2 次机会）。
COMPACT_ATTEMPTS = 2

# 送上游的 messages 只允许这些字段。会话化之后消息会带 ``seq`` 等存储侧字段，
# 原样发出去轻则被上游忽略、重则被严格实现判 400；``seq`` 只服务于压缩切点。
_WIRE_KEYS = ("role", "content", "tool_calls", "tool_call_id", "name")

# run 级超时的审计错误码（与上游自己的 ``AI_TIMEOUT`` 区分：那是单次调用超时）。
RUN_TIMEOUT_CODE = "AI_AGENT_RUN_TIMEOUT"

_TOKEN_KEYS = (
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
)


def _audit_status(code: str) -> str:
    """错误码 → 审计 status 枚举（与 ``ai_router._audit_status`` 同口径）。"""
    if code == "AI_TIMEOUT":
        return "timeout"
    if code == "AI_BUSY":
        return "busy"
    if code in ("AI_NO_PROVIDER", "AI_UPSTREAM_AUTH", "AI_UPSTREAM_REJECTED"):
        return "rejected"
    if code in ("AI_RATE_LIMITED", "insufficient_quota", "AI_FORBIDDEN"):
        return "rejected"
    return "error"


def _billable(entry: dict) -> bool:
    """个人 key 不占平台额度（钱是管理员自己出的）——与 chat 面同一条规则。"""
    return entry.get("scope") != credentials.SCOPE_PERSONAL


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.monotonic() - started) * 1000))


def _truncate(text: str, limit: int = MAX_TOOL_RESULT_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n…（结果已被截断，请缩小查询范围）"


def run_timeout_seconds() -> int:
    """``settings.AI_AGENT_RUN_TIMEOUT_SECONDS``，非法值回退 300。

    配成 0 / 负数 / bool 会把整条链路掐死（一轮都不跑）或让它立刻超时，两种都该
    回退到默认值而不是静默生效。
    """
    raw = getattr(settings, "AI_AGENT_RUN_TIMEOUT_SECONDS", DEFAULT_RUN_TIMEOUT_SECONDS)
    if isinstance(raw, bool) or not isinstance(raw, int) or raw <= 0:
        return DEFAULT_RUN_TIMEOUT_SECONDS
    return raw


async def _select_model(user_oid: str, model: object) -> tuple[str, list[dict]]:
    """确定这次 run 用的模型（合格 ID）与候选 provider。

    ``model`` 缺省时取目录里第一个可用模型：前端不该为了「随便用」而先发一次
    ``/v1/models``；显式指定的模型必须在**该用户可见目录**里（个人 key 只对本人
    可见，不能靠猜名字用别人的模型）。

    返回值是**合格 ID**（``provider/model``）：同名模型在多渠道上只有它能把
    「用哪家」表达清楚。裸名仍接受（老会话/老定时任务存的就是裸名），但只在这家
    唯一提供它时成立，否则报 ``model_ambiguous`` 并列出候选。
    """
    entries = await credentials.catalog(user_oid)
    if not entries:
        raise AgentError("AI_NO_PROVIDER", routing.NO_PROVIDER_MESSAGE, 503)

    available = await ai_config.allowed_models_for(user_oid)
    if not available:
        raise AgentError("AI_NO_PROVIDER", routing.NO_PROVIDER_MESSAGE, 503)

    wanted = next(iter(available)) if model is None or model == "" else model
    try:
        resolved = credentials.resolve_model(entries, wanted)
    except credentials.ModelAmbiguous as ambiguous:
        raise AgentError("model_ambiguous", ambiguous.message, 400) from None
    if resolved is None:
        raise AgentError(
            "model_not_found", "the requested model is not available", 404
        )

    try:
        selected = routing.candidates(entries)
    except RouteError as exc:
        raise AgentError(exc.code, exc.message, exc.status_code) from None

    candidates = credentials.route_candidates(selected, resolved)[
        :MAX_PROVIDER_ATTEMPTS
    ]
    if not candidates:
        # 模型在目录里但 provider 正在冷却。
        raise AgentError("AI_NO_PROVIDER", routing.NO_PROVIDER_MESSAGE, 503)
    return resolved.qualified, candidates


def _attach_entry(exc: AgentError, entry: dict | None) -> AgentError:
    """把「实际最后尝试的那个 provider」挂到错误上，供逐轮审计归因。

    ``AgentError`` 不带 provider 字段（它属于工具层，不该知道目录条目），而审计
    报表要回答「这次失败算在哪个上游头上」。动态属性是最小侵入的做法；审计侧用
    ``getattr(exc, "entry", None)`` 读，读不到就退回候选列表第一个。
    """
    exc.entry = entry  # type: ignore[attr-defined]
    return exc


async def _call_upstream(
    payload: dict, candidates: list[dict]
) -> tuple[dict, dict, str | None]:
    """发一次上游调用（按目录顺序重试）。

    返回 ``(响应体, 命中的 entry, cache_key)``。每个候选各自做一次缓存注入
    （``prompt_cache.request_body``）：不同 provider 的注入规则不同，必须逐条算；
    ``cache_key`` 是本进程算出来的审计值（NULL = 没注入），要透传进审计行。
    """
    last_error: RouteError | None = None
    last_entry: dict | None = None
    for entry in candidates:
        last_entry = entry
        key = str(entry.get("key") or "")
        try:
            api_key = await credentials.resolve_api_key(entry)
        except CredentialError as exc:
            routing.mark_failure(key)
            raise _attach_entry(
                AgentError(exc.code, exc.message, exc.status_code), entry
            ) from None

        body, cache_key = prompt_cache.request_body(payload, entry)
        try:
            async with routing.slot():
                result = await providers.chat_completion(body, entry, api_key)
        except RouteError as exc:
            # 并发闸门满不是上游故障，不计入熔断（与 chat 面一致）。
            if exc.code != "AI_BUSY":
                routing.mark_failure(key)
            if exc.code not in RETRYABLE_CODES:
                raise _attach_entry(
                    AgentError(exc.code, exc.message, exc.status_code), entry
                ) from None
            last_error = exc
            continue

        routing.mark_success(key)
        return result, entry, cache_key

    failure = last_error or RouteError(
        "AI_NO_PROVIDER", routing.NO_PROVIDER_MESSAGE, 503
    )
    raise _attach_entry(
        AgentError(failure.code, failure.message, failure.status_code), last_entry
    )


def _message_of(result: object) -> tuple[str, list[dict]]:
    """从上游响应里取第一条 choice 的 content / tool_calls。"""
    if not isinstance(result, dict):
        raise AgentError(
            "AI_UPSTREAM_ERROR", "upstream returned an unexpected payload", 502
        )
    choices = result.get("choices")
    if not isinstance(choices, list) or not choices:
        raise AgentError(
            "AI_UPSTREAM_ERROR", "upstream returned no choices", 502
        )
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    if not isinstance(message, dict):
        raise AgentError(
            "AI_UPSTREAM_ERROR", "upstream returned no message", 502
        )
    content = message.get("content")
    calls = message.get("tool_calls")
    return (
        content if isinstance(content, str) else "",
        [call for call in calls if isinstance(call, dict)] if isinstance(calls, list) else [],
    )


async def _write_event(
    *,
    request_id: str,
    round_no: int,
    tool_name: object,
    tool_kind: str | None,
    args_summary: str | None,
    result_summary: str,
    status: str,
    user_oid: str,
    args_hash: str | None = None,
) -> None:
    """工具事件落库（失败只 warning：事件写失败不该把一次可用的调用打挂）。

    与 ``audit.record`` 同一原则：审计是旁路。代价是极端情况下会缺一行，但
    「模型答完却被审计写失败搞成 500」是更糟的取舍。
    """
    try:
        await backends.get_ai_agent_events_repo().insert(
            oid=uuid4().hex,
            request_id=request_id,
            round_no=round_no,
            tool_name=tool_name if isinstance(tool_name, str) else None,
            tool_kind=tool_kind,
            args_hash=args_hash,
            args_summary=args_summary,
            result_summary=result_summary[:500],
            status=status,
            actor_oid=user_oid,
        )
    except Exception:  # noqa: BLE001 - 审计旁路
        _logger.warning("ai agent event write failed", exc_info=True)


def _tool_call_parts(call: dict) -> tuple[str, str, object]:
    """OpenAI 的 ``tool_calls[i]`` → ``(call_id, 工具名, arguments)``。"""
    call_id = call.get("id")
    function = call.get("function") if isinstance(call, dict) else None
    if not isinstance(function, dict):
        return (call_id if isinstance(call_id, str) else "", "", None)
    name = function.get("name")
    return (
        call_id if isinstance(call_id, str) else "",
        name if isinstance(name, str) else "",
        function.get("arguments"),
    )


async def _run_tool(
    *,
    call: dict,
    user: dict,
    request_id: str,
    round_no: int,
) -> tuple[dict, dict | None, str, str]:
    """处理一个 ``tool_call``：``(tool 消息, 提案或 None, 事件状态, 事件摘要)``。

    状态由这里显式给出而不是从消息内容反推：大结果会被截断（``MAX_TOOL_RESULT_CHARS``），
    截断后的文本不是合法 JSON，反推会把一次成功的读工具记成 ``failed``。

    第四个返回值是**落库/回给前端的事件摘要**（已脱敏、≤500），与给模型的
    ``content`` 分开：模型要看到文件正文才能干活，审计不该留正文（契约 §0.7）。
    """
    call_id, raw_name, raw_args = _tool_call_parts(call)
    user_oid = user["id"]

    tool = get_tool(raw_name)
    if tool is None:
        # 白名单外：拒绝并告诉模型「没有这个工具」，让它自己纠偏。
        note = "工具不在白名单内，已拒绝"
        await _write_event(
            request_id=request_id,
            round_no=round_no,
            tool_name=raw_name,
            tool_kind=None,
            args_summary=None,
            result_summary=note,
            status="denied",
            user_oid=user_oid,
        )
        return (
            _tool_message(call_id, {"error": "unknown_tool", "tool": raw_name}),
            None,
            "denied",
            note,
        )

    if not run_allowed(tool, user):
        note = "当前用户没有调用该工具的权限，已拒绝"
        await _write_event(
            request_id=request_id,
            round_no=round_no,
            tool_name=tool.name,
            tool_kind=tool.kind,
            args_summary=_args_text(raw_args),
            result_summary=note,
            status="denied",
            user_oid=user_oid,
        )
        return (
            _tool_message(
                call_id, {"error": "permission_denied", "tool": tool.name}
            ),
            None,
            "denied",
            note,
        )

    args_text = _args_text(raw_args)
    if tool.guard is not None:
        # 现查库的权限闸（运维工具必填）：``permission`` 是纯函数，只能做快速
        # 否决；「现在是不是系统管理员」必须每次重新算，不信 token 快照。
        try:
            await tool.guard(user)
        except AgentError as exc:
            note = _summary_text(f"权限复核未通过：{exc.message}")
            await _write_event(
                request_id=request_id,
                round_no=round_no,
                tool_name=tool.name,
                tool_kind=tool.kind,
                args_summary=args_text,
                result_summary=note,
                status="denied",
                user_oid=user_oid,
            )
            return (
                _tool_message(
                    call_id,
                    {"error": "permission_denied", "code": exc.code, "message": exc.message},
                ),
                None,
                "denied",
                note,
            )
        except Exception:  # noqa: BLE001 - 权限闸抛错按拒绝处理（fail closed）
            note = "权限复核失败，已拒绝"
            await _write_event(
                request_id=request_id,
                round_no=round_no,
                tool_name=tool.name,
                tool_kind=tool.kind,
                args_summary=args_text,
                result_summary=note,
                status="denied",
                user_oid=user_oid,
            )
            return (
                _tool_message(
                    call_id, {"error": "permission_denied", "tool": tool.name}
                ),
                None,
                "denied",
                note,
            )

    try:
        kwargs = prepare_arguments(tool, raw_args)
    except AgentError as exc:
        note = _summary_text(f"参数校验失败：{exc.message}")
        await _write_event(
            request_id=request_id,
            round_no=round_no,
            tool_name=tool.name,
            tool_kind=tool.kind,
            args_summary=args_text,
            result_summary=note,
            status="failed",
            user_oid=user_oid,
        )
        return (
            _tool_message(
                call_id,
                {"error": "bad_arguments", "code": exc.code, "message": exc.message},
            ),
            None,
            "failed",
            note,
        )

    if tool.kind == WRITE and not tool.direct:
        # 写操作**不执行**：生成提案，等用户确认。
        try:
            created = await proposal.propose(
                user=user,
                tool=tool,
                arguments=kwargs,
                request_id=request_id,
                round_no=round_no,
            )
        except AgentError as exc:
            # 提案没建成也要留痕：否则「模型要求写、被参数/预览校验挡住」这件事
            # 在事件表里完全消失，只剩模型自己的一句话。
            note = _summary_text(f"提案创建失败：{exc.message}")
            await _write_event(
                request_id=request_id,
                round_no=round_no,
                tool_name=tool.name,
                tool_kind=WRITE,
                args_summary=args_text,
                result_summary=note,
                status="failed",
                user_oid=user_oid,
            )
            return (
                _tool_message(
                    call_id,
                    {"error": "proposal_failed", "code": exc.code, "message": exc.message},
                ),
                None,
                "failed",
                note,
            )
        # 提案行由 ``proposal.propose`` 自己写入（status='proposed'）；这里只回给
        # 模型一句「等你确认」，事件摘要同样不含参数原文。
        note = _summary_text(f"已生成待确认提案：{created['summary']}")
        return (
            _tool_message(
                call_id,
                {
                    "status": "proposed",
                    "proposal_oid": created["oid"],
                    "summary": created["summary"],
                    "expires_at": created["expires_at"],
                    "requires_dual_auth": created["requires_dual_auth"],
                    "message": "写操作已生成待确认提案，用户在平台上确认后才会执行。",
                },
            ),
            created,
            "proposed",
            note,
        )

    # 走到这里的只有两种：读工具，以及 ``direct=True`` 的写工具（巡检通道）。
    # 后者虽已过 permission + guard + 参数收敛，但**没有**人工确认，所以事件里
    # 必须留痕（``args_summary`` 带 ``direct``，见 :func:`_direct_args_text`）：
    # 事后要能一眼分出「哪些副作用是模型自己落库的」。``status`` 仍是
    # ``executed``——这个枚举描述的是「工具真的跑了」，与读工具同义。
    direct_write = tool.kind == WRITE
    event_args = _direct_args_text(raw_args) if direct_write else args_text
    tool_kind = tool.kind

    try:
        with tool_impl.operator_context(user):
            result = await tool.impl(user, **kwargs)
    except AgentError as exc:
        note = _summary_text(f"执行失败：{exc.message}")
        await _write_event(
            request_id=request_id,
            round_no=round_no,
            tool_name=tool.name,
            tool_kind=tool_kind,
            args_summary=event_args,
            result_summary=note,
            status="failed",
            user_oid=user_oid,
        )
        return (
            _tool_message(
                call_id, {"error": "tool_failed", "code": exc.code, "message": exc.message}
            ),
            None,
            "failed",
            note,
        )
    except Exception:  # noqa: BLE001 - 工具异常不能把整个 run 打挂
        note = "执行失败：内部错误"
        await _write_event(
            request_id=request_id,
            round_no=round_no,
            tool_name=tool.name,
            tool_kind=tool_kind,
            args_summary=event_args,
            result_summary=note,
            status="failed",
            user_oid=user_oid,
        )
        return (
            _tool_message(call_id, {"error": "tool_failed", "code": "internal_error"}),
            None,
            "failed",
            note,
        )

    note = _summary_text(result)
    await _write_event(
        request_id=request_id,
        round_no=round_no,
        tool_name=tool.name,
        tool_kind=tool_kind,
        args_summary=event_args,
        result_summary=note,
        status="executed",
        user_oid=user_oid,
    )
    # 直接执行的写工具要在 tool 消息里说清「已经落库了」：模型若以为是提案，
    # 下一轮会重复调用（封禁是幂等的，但解封/设备操作不一定）。
    payload = (
        {"status": "executed", "direct": True, "result": result}
        if direct_write
        else result
    )
    return (_tool_message(call_id, payload), None, "executed", note)


def _args_text(raw_args: object) -> str | None:
    """事件里的 ``ARGS_SUMMARY``：脱敏后的 JSON 文本（≤500）。

    模型给的参数里可能有文件正文、SQL 参数或 SSH 命令——``password`` / ``token``
    与超长字符串不能进事件摘要（契约 §0.7）。能解析成 JSON 就按结构脱敏，解析
    不了才退化成「对整段文本脱敏」。
    """
    if raw_args is None:
        return None
    if isinstance(raw_args, str):
        try:
            parsed = json.loads(raw_args)
        except ValueError:
            masked = proposal.redacted(raw_args)
            return (masked if isinstance(masked, str) else raw_args)[:500]
        return tool_impl.to_json_text(proposal.redacted(parsed))[:500]
    return tool_impl.to_json_text(proposal.redacted(raw_args))[:500]


def _direct_args_text(raw_args: object) -> str | None:
    """直接执行的写工具在事件里的 ``ARGS_SUMMARY``：脱敏 JSON + ``direct`` 标记。

    与 :func:`_args_text` 的唯一差别是多一个 ``"direct": true``。审计侧据此把
    「模型自己落库的副作用」与「用户确认过的提案」分开统计——两者都写
    ``status='executed'``，不标就分不出来。
    """
    base = _args_text(raw_args)
    if base is None:
        return '{"direct": true}'
    try:
        parsed = json.loads(base)
    except ValueError:
        # 原文已被截断/不是 JSON：打标记比丢掉更容易查。
        return f"{base} [direct]"[:500]
    if isinstance(parsed, dict):
        parsed["direct"] = True
        return tool_impl.to_json_text(parsed)[:500]
    return f"{base} [direct]"[:500]


def _summary_text(value: object) -> str:
    """事件里的 ``RESULT_SUMMARY``：脱敏后 ≤500（正文只在响应体/预览里）。"""
    return tool_impl.to_json_text(proposal.redacted(value))[:500]


def _tool_message(call_id: str, payload: object) -> dict:
    """OpenAI 的 ``role="tool"`` 消息：``tool_call_id`` 必须与调用一一对应。"""
    return {
        "role": "tool",
        "tool_call_id": call_id,
        "content": _truncate(tool_impl.to_json_text(payload)),
    }


def _validate_messages(messages: object) -> list[dict]:
    """一次 run 的上下文消息：``user`` / ``assistant`` / ``tool``，拒绝 ``system``。

    会话化之后 ``messages`` 不再只是「本轮用户说了什么」，而是整段可回放上下文
    （``run_queue._replay`` 把库里的消息摊成 OpenAI 形状）。工具历史是其中不可缺的
    一段：``assistant(tool_calls)`` 与对应的 ``tool`` 结果在上游协议里是原子配对，
    少了任何一半，**第二次提问在入口就会 400**——用户看到的是「整个会话不可用」。

    ``system`` 仍然禁止：服务端前缀（:data:`services.agent.prompt.SYSTEM_PROMPT` +
    压缩摘要）只能由服务端注入，客户端能写就等于能改行为护栏，也能让缓存前缀随人
    漂移。

    这里**不做**自动修复或丢弃：静默改上下文比报错更难查。畸形输入一律 400，并在
    message 里点名第几条，便于定位是哪段回放出了问题。
    """
    if not isinstance(messages, list) or not messages:
        raise AgentError(
            "AI_AGENT_BAD_ARGUMENTS", "messages must be a non-empty array", 400
        )

    cleaned: list[dict] = []
    # 尚未被消费的 tool_call id：assistant(tool_calls) 开启，tool 消息逐个消费。
    # user / 普通 assistant 会中断这条链（上游协议里工具结果必须紧跟它的调用）。
    pending_calls: set[str] = set()

    for position, item in enumerate(messages, start=1):
        if not isinstance(item, dict):
            raise AgentError(
                "AI_AGENT_BAD_ARGUMENTS", "each message must be an object", 400
            )
        role = item.get("role")
        if role == "system":
            raise AgentError(
                "AI_AGENT_BAD_ARGUMENTS",
                f"message {position}: system messages are injected by the server; "
                "role must be user|assistant|tool",
                400,
            )
        if role not in ("user", "assistant", "tool"):
            raise AgentError(
                "AI_AGENT_BAD_ARGUMENTS",
                f"message {position}: message role must be user|assistant|tool",
                400,
            )

        message: dict = {"role": role}

        if role == "tool":
            call_id = item.get("tool_call_id")
            if not isinstance(call_id, str) or not call_id:
                raise AgentError(
                    "AI_AGENT_BAD_ARGUMENTS",
                    f"message {position}: tool message requires tool_call_id",
                    400,
                )
            if call_id not in pending_calls:
                raise AgentError(
                    "AI_AGENT_BAD_ARGUMENTS",
                    f"message {position}: tool message {call_id} has no matching "
                    "assistant tool_calls before it",
                    400,
                )
            pending_calls.discard(call_id)
            message["tool_call_id"] = call_id
            message["content"] = _content_of(item, position)
        elif role == "assistant":
            # OpenAI 允许 assistant 只有 tool_calls、没有 content；缺失按空串补，
            # 否则上游会把「缺字段」当成坏请求。
            content = item.get("content")
            message["content"] = "" if content is None else _content_of(item, position)
            calls = item.get("tool_calls")
            if calls is None:
                pending_calls = set()
            else:
                if not isinstance(calls, list) or any(
                    not isinstance(call, dict) for call in calls
                ):
                    raise AgentError(
                        "AI_AGENT_BAD_ARGUMENTS",
                        f"message {position}: tool_calls must be an array of objects",
                        400,
                    )
                # 原样保留（含顺序）：改形状会让上游对不上调用 id，也会破坏缓存前缀。
                message["tool_calls"] = calls
                pending_calls = {
                    call_id
                    for call_id in (_tool_call_id(call) for call in calls)
                    if call_id
                }
        else:
            message["content"] = _content_of(item, position)
            pending_calls = set()

        # ``name`` 是协议字段（多说话人场景），其余存储字段（``seq`` / ``round_no``）
        # 只在内部流转换时保留：``_wire`` 送上游前会剥掉。
        name = item.get("name")
        if isinstance(name, str) and name:
            message["name"] = name
        seq = item.get("seq")
        if isinstance(seq, int) and not isinstance(seq, bool):
            message["seq"] = seq
        cleaned.append(message)
    return cleaned


def _content_of(item: dict, position: int) -> object:
    """``user`` / ``tool`` 的 content 必填（字符串或多模态数组）。"""
    content = item.get("content")
    if not isinstance(content, (str, list)):
        raise AgentError(
            "AI_AGENT_BAD_ARGUMENTS",
            f"message {position}: content must be a string or content array",
            400,
        )
    return content


def _tool_call_id(call: dict) -> str:
    """``tool_calls[i].id``（形状不对就当没有，交给配对校验去报错）。"""
    call_id = call.get("id")
    return call_id if isinstance(call_id, str) else ""


def _round_tokens(tokens: dict, payload: dict, content: str) -> int:
    """这一轮算作多少 token（预算闸只认这个数）。

    优先级：上游的 ``total_tokens`` → ``prompt + completion`` → 字符 / 4 估算。
    最后一级是**必须有**的：有些 OpenAI 兼容实现根本不回 ``usage``，若按 0 记，
    ``AI_AGENT_TOKEN_BUDGET`` 就永远不会触发（预算闸形同不存在）。
    """
    total = tokens.get("total_tokens")
    if total is not None:
        return max(0, int(total))

    prompt = tokens.get("prompt_tokens")
    completion = tokens.get("completion_tokens")
    if prompt is not None or completion is not None:
        counted = max(0, int(prompt or 0)) + max(0, int(completion or 0))
        if counted:
            return counted

    estimated = ai_usage.prompt_chars(payload) // ai_usage.CHARS_PER_TOKEN
    estimated += len(content) // ai_usage.CHARS_PER_TOKEN
    return max(1, estimated)


def _add_usage(acc: dict, tokens: dict) -> dict:
    """全轮累计 usage（``None`` = 上游没报，**不是** 0）。"""
    out: dict[str, int | None] = {}
    for key in _TOKEN_KEYS:
        value = tokens.get(key)
        if value is None:
            out[key] = acc.get(key)
        else:
            out[key] = (acc.get(key) or 0) + int(value)
    return out


def _wire(messages: list[dict]) -> list[dict]:
    """裁剪成上游协议能接受的形状（``seq`` 等存储字段不出去）。"""
    return [
        {key: message[key] for key in _WIRE_KEYS if key in message}
        for message in messages
    ]


def _finish_reason(result: dict) -> str | None:
    """上游的 ``choices[0].finish_reason``：诊断截断回答用，拿不到就是 ``None``。"""
    choices = result.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        value = choices[0].get("finish_reason")
        if isinstance(value, str):
            return value
    return None


def _last_seq(messages: list[dict]) -> int | None:
    """旧段里最大的 ``seq``（= 摘要覆盖到第几条）。

    倒着找而不是取 ``messages[-1]``：旧段末尾在极端情况下可能是在飞的新消息
    （没有 ``seq``），而摘要的边界只能落在**已落库**的行上。
    """
    for message in reversed(messages):
        seq = message.get("seq")
        if isinstance(seq, int) and not isinstance(seq, bool):
            return seq
    return None


async def _emit(callback, *args) -> None:
    """调用调用方回调；同步/异步都支持（``run_queue`` 侧可能直接给协程函数）。"""
    if callback is None:
        return
    result = callback(*args)
    if inspect.isawaitable(result):
        await result


async def _should_stop(should_stop) -> bool:
    """取消/中断闸：回调可以是同步也可以是协程（会话侧要查库）。

    ``bool(coroutine)`` 恒为真——不 await 就等于「每次 run 第一轮就被取消」，而
    同步回调又不必强迫调用方包一层 async，所以这里两种都吃。

    回调异常按「不取消」处理：取消是尽力而为的加速器，不是正确性来源——真正的
    保证是 run 侧在写库/扣费前复核 run 状态。让一次取消失败的探测把整个 run 打挂，
    代价更大。
    """
    if should_stop is None:
        return False
    try:
        result = should_stop()
        if inspect.isawaitable(result):
            result = await result
        return bool(result)
    except Exception:  # noqa: BLE001 - 回调异常不该改变执行语义
        _logger.warning("ai agent should_stop callback failed", exc_info=True)
        return False


def _round_payload(
    *,
    round_no: int,
    content: str,
    finish_reason: str | None,
    tool_calls: list[dict],
    tools: list[dict],
    tokens: dict,
    units: int,
) -> dict:
    """一轮收尾交给 ``on_round`` 的载荷（executor 自己**不写库**）。

    只会出现本轮的 usage：落库侧要把它写到该轮的 assistant/tool 行上，累计值由
    会话行自己加——两处都算累计会重复。
    """
    return {
        "round_no": round_no,
        "assistant": {
            "content": content,
            "tool_calls": tool_calls,
            "finish_reason": finish_reason,
        },
        "tools": tools,
        "usage": {
            "prompt": tokens.get("prompt_tokens"),
            "completion": tokens.get("completion_tokens"),
            "total": tokens.get("total_tokens"),
            "cache_read": tokens.get("cache_read_tokens"),
            "cache_write": tokens.get("cache_write_tokens"),
        },
        "units": units,
    }


def _compact_budget(limit: int) -> int:
    """硬截断的目标水位：压缩阈值本身，而不是模型上限。

    截到「上限」等于什么都没做（下一轮立刻又超阈值）；截到阈值以下才算真正降水位。
    """
    return max(1, math.ceil(limit * agent_context.compact_ratio()))


async def _compact_context(
    *,
    conversation: list[dict],
    model: str,
    candidates: list[dict],
    user_oid: str,
    roles,
    request_id: str,
    snapshot: str | None,
    prompt_tokens: int | None,
    on_summary,
) -> tuple[list[dict], bool]:
    """判断并执行一次压缩（含降级）。返回 ``(重建后的对话, 是否动过)``。

    「动过」包括压缩成功与降级到硬截断：一次 run 只允许压一次（§5.3 防抖动），
    否则每轮都在阈值边缘反复触发，既贵又抖。
    """
    limit = agent_context.context_limit(model)
    if not agent_context.needs_compaction(prompt_tokens, conversation, limit):
        return conversation, False

    head = 0
    while head < len(conversation) and conversation[head].get("role") in (
        "system",
        "developer",
    ):
        head += 1
    older, recent = agent_context.plan_compaction(
        conversation[head:], agent_context.configured_keep_turns()
    )
    if not older:
        # 切不出来（轮数不够 / 旧段本身是残链）：宁可不压，也不切一条坏历史。
        return conversation, False

    upto_seq = _last_seq(older)
    if upto_seq is None:
        # 契约把压缩限定在「消息带 seq」的会话化调用上：没有 seq 就写不了
        # ``summary_upto_seq``，摘要会在下一轮被重新发一遍全量历史，等于白花钱。
        # 这里也不硬截断——没有 seq 的场景（旧的无状态调用）不该被静默丢历史。
        return conversation, False

    summary_text = ""
    for attempt in range(COMPACT_ATTEMPTS):
        # 换候选重试（§5.5）：第二轮把上次首选的 provider 挪到末尾。
        rotated = candidates[attempt:] + candidates[:attempt]
        try:
            summary_text = await _summarize_history(
                older=older,
                recent=recent,
                model=model,
                candidates=rotated,
                user_oid=user_oid,
                roles=roles,
                request_id=request_id,
                snapshot=snapshot,
            )
            break
        except Exception as exc:  # noqa: BLE001 - 压缩失败必须降级，不能打挂 run
            _logger.warning(
                "ai agent compaction attempt %s failed: %s",
                attempt + 1,
                type(exc).__name__,
            )

    if not summary_text:
        _logger.warning("ai agent compaction degraded to hard truncate")
        return _hard_truncate(conversation, limit), True

    rebuilt = agent_context.build_messages(SYSTEM_PROMPT, summary_text, recent, [])
    # 摘要之后仍超限（旧段本身很小但最近 N 轮就很大）：截断补齐（§5.5-3）。
    if agent_context.needs_compaction(None, rebuilt, limit):
        rebuilt = _hard_truncate(rebuilt, limit)
    await _emit(on_summary, summary_text, upto_seq)
    return rebuilt, True


def _hard_truncate(conversation: list[dict], limit: int) -> list[dict]:
    """降级路径：丢弃最旧的消息直到回到阈值以下（保留开头的 system 段）。"""
    return agent_context.hard_truncate(conversation, _compact_budget(limit))


async def _maybe_compact(
    *,
    conversation: list[dict],
    compacted: bool,
    model: str,
    candidates: list[dict],
    user_oid: str,
    roles,
    run_id: str,
    snapshot: str | None,
    prompt_tokens: int | None,
    on_summary,
) -> tuple[list[dict], bool]:
    """压缩判定的兜底外壳：全链路异常不外溢（§5.5-4）。

    返回未改动的对话 + ``True``：这次尝试算「用掉了唯一一次机会」，避免每轮都在
    同一个坏输入上重试（既贵又可能把上下文切碎）。
    """
    if compacted:
        return conversation, True
    try:
        return await _compact_context(
            conversation=conversation,
            model=model,
            candidates=candidates,
            user_oid=user_oid,
            roles=roles,
            request_id=run_id,
            snapshot=snapshot,
            prompt_tokens=prompt_tokens,
            on_summary=on_summary,
        )
    except Exception:  # noqa: BLE001 - 压缩是旁路，不能打挂 run
        _logger.warning("ai agent context compaction failed", exc_info=True)
        return conversation, True


async def _summarize_history(
    *,
    older: list[dict],
    recent: list[dict],
    model: str,
    candidates: list[dict],
    user_oid: str,
    roles,
    request_id: str,
    snapshot: str | None,
) -> str:
    """摘要调用：计费照扣、审计 ``purpose='compact'``、``round_no=NULL``。

    契约 §5.4：压缩是**用户额度**里的真实成本，但不计入 run 的 token 预算（省下来
    的上下文还没用上就先被预算掐死，等于白花钱）。
    """

    async def complete(prompt_messages: list[dict], max_tokens: int) -> str:
        if _billable(candidates[0]):
            allowed, _used, _limit = await quota.peek(
                user_oid, "chat", 1, roles=roles
            )
            if not allowed:
                raise AgentError(
                    "insufficient_quota", "monthly chat quota is exhausted", 429
                )

        payload = {
            # ``model`` 是合格 ID（审计用），上游要的是裸模型名。
            "model": credentials.upstream_model_name(candidates, model),
            "messages": prompt_messages,
            "stream": False,
            "max_tokens": max_tokens,
        }
        started = time.monotonic()
        try:
            result, entry, cache_key = await _call_upstream(payload, candidates)
        except AgentError as exc:
            last_entry = getattr(exc, "entry", None) or (
                candidates[0] if candidates else None
            )
            await _audit_round(
                user_oid=user_oid,
                model=model,
                request_id=request_id,
                round_no=None,
                snapshot=snapshot,
                status=_audit_status(exc.code),
                http_status=exc.status_code,
                error_code=exc.code,
                latency_ms=_elapsed_ms(started),
                provider=last_entry.get("name") if last_entry else None,
                key_scope=last_entry.get("scope") if last_entry else None,
                purpose=PURPOSE_COMPACT,
            )
            raise

        text, _calls = _message_of(result)
        tokens = ai_usage.token_fields(result.get("usage"))
        units = ai_usage.units_from_total_tokens(tokens["total_tokens"])
        if _billable(entry):
            try:
                await quota.consume(user_oid, "chat", units, roles=roles)
            except Exception as exc:  # noqa: BLE001 - 回写失败不能丢掉摘要
                _logger.warning(
                    "ai agent compact quota write-back failed: %s",
                    type(exc).__name__,
                )
        await _audit_round(
            user_oid=user_oid,
            model=model,
            request_id=request_id,
            round_no=None,
            snapshot=snapshot,
            status="success",
            http_status=200,
            latency_ms=_elapsed_ms(started),
            provider=entry.get("name"),
            key_scope=entry.get("scope"),
            units=units,
            tokens=tokens,
            cache_key=cache_key,
            purpose=PURPOSE_COMPACT,
        )
        return text

    return await agent_context.compact(older, recent, complete=complete)


async def run(
    *,
    user: dict,
    messages: list[dict],
    model: str | None = None,
    request_id: str | None = None,
    summary: str | None = None,
    on_round: Callable | None = None,
    should_stop: Callable[[], bool] | None = None,
    on_summary: Callable[[str, int], None] | None = None,
) -> dict:
    """跑完一次 agent 会话。

    返回 ``{request_id, content, stop_reason, rounds, usage, events, proposals}``。

    会话化之后本函数仍是**纯执行器**：不写会话表、不分配 ``seq``。每轮收尾通过
    ``on_round`` 交出去（落库/计费口径都在那一侧），压缩结果通过 ``on_summary``
    交出去（``upto_seq`` = 摘要覆盖到哪条消息）。
    """
    user_oid = user["id"]
    roles = user.get("per")
    snapshot = audit.roles_snapshot(roles)
    run_id = request_id or uuid4().hex

    # prompt 受控留存：**一次 run 只留一次**（不在每轮重复）。一次 run 的每一轮
    # 都是同一段对话的延续，每轮留一份只会把同一段话存 N 遍；而「这次 run 从
    # 什么问起」这件事，留入口这一份就够了。旁路：内部吞掉所有异常。
    await prompt_audit.capture(
        user=user,
        purpose=PURPOSE_AGENT,
        messages=messages,
        request_id=run_id,
    )

    client_messages = _validate_messages(messages)
    # 服务端 system 段固定在最前（缓存前缀 + 行为护栏）；动态内容只追加在尾部。
    # 有摘要/压缩回调时走 ``context.build_messages``：摘要必须是**第二条 system**
    # （§5.1），拼到尾部会让前缀指纹退化成全站同 key。
    # 旧调用（无 summary / 无 on_summary）保留原路径：那是不带会话语义的无状态
    # 执行器，不该因为引入压缩而多出一层模块耦合。
    use_context = on_summary is not None or bool(summary)
    if use_context:
        conversation: list[dict] = agent_context.build_messages(
            SYSTEM_PROMPT, summary, client_messages, []
        )
    else:
        conversation = [
            {"role": "system", "content": SYSTEM_PROMPT},
            *client_messages,
        ]
    chosen, candidates = await _select_model(user_oid, model)

    max_iterations = ai_config.agent_max_iterations()
    budget = ai_config.agent_token_budget()
    deadline = time.monotonic() + run_timeout_seconds()

    events: list[dict] = []
    proposals: list[dict] = []
    content = ""
    total_tokens = 0
    rounds = 0
    usage: dict[str, int | None] = {key: None for key in _TOKEN_KEYS}
    stop_reason: str | None = None
    # 压缩判水位优先用上一轮实测 ``prompt_tokens``（估算只是下界）。
    last_prompt_tokens: int | None = None
    compacted = False

    for round_no in range(1, max_iterations + 1):
        # 0) 取消 / 中断：轮边界判一次（工具循环里每次执行前再判）。
        if await _should_stop(should_stop):
            stop_reason = "cancelled"
            break

        # 0a) run 级截止时间：超时不发新一轮（上游调用另有 wait_for 兜住长尾）。
        if time.monotonic() >= deadline:
            stop_reason = "timeout"
            break

        # 0b) 预算闸（循环入口再判一次）：上一轮工具已执行完，这里挡住「明知超
        #     预算还再买一次上游调用」。
        if total_tokens >= budget:
            stop_reason = "token_budget"
            break

        # 1) 频控：每一轮都各记一次窗口（一轮是一次真实上游调用）。
        try:
            rate_limit.check(user_oid, "chat")
        except rate_limit.RateLimitError as exc:
            rejection = AgentError(
                "AI_RATE_LIMITED",
                exc.message,
                429,
                retry_after=exc.retry_after,
            )
            await _audit_round(
                user_oid=user_oid,
                model=chosen,
                request_id=run_id,
                round_no=round_no,
                snapshot=snapshot,
                status="rejected",
                http_status=429,
                error_code="AI_RATE_LIMITED",
                latency_ms=0,
                provider=candidates[0].get("name") if candidates else None,
                key_scope=candidates[0].get("scope") if candidates else None,
            )
            _attach_entry(rejection, candidates[0] if candidates else None)
            rejection.audited = True  # type: ignore[attr-defined]
            raise rejection from None

        # 2) 额度预检：明显超额的请求不该先花掉上游成本再被拒。
        if _billable(candidates[0]):
            allowed, used, limit = await quota.peek(
                user_oid, "chat", 1, roles=roles
            )
            if not allowed:
                rejection = AgentError(
                    "insufficient_quota",
                    "monthly chat quota is exhausted",
                    429,
                    headers={
                        "X-AI-Quota-Used": str(used),
                        "X-AI-Quota-Limit": str(limit),
                    },
                )
                await _audit_round(
                    user_oid=user_oid,
                    model=chosen,
                    request_id=run_id,
                    round_no=round_no,
                    snapshot=snapshot,
                    status="rejected",
                    http_status=429,
                    error_code="insufficient_quota",
                    latency_ms=0,
                    provider=candidates[0].get("name"),
                    key_scope=candidates[0].get("scope"),
                )
                _attach_entry(rejection, candidates[0])
                rejection.audited = True  # type: ignore[attr-defined]
                raise rejection

        payload = {
            # 合格 ID 是我们自己的命名空间；上游只认裸模型名。
            "model": credentials.upstream_model_name(candidates, chosen),
            "messages": _wire(conversation),
            # 强制非流式：工具调用的完整参数只在整条响应里，流式边收边执行等于
            # 猜半个 JSON。
            "stream": False,
            "tools": tool_specs(user),
        }

        started = time.monotonic()
        try:
            remaining = max(0.0, deadline - started)
            result, entry, cache_key = await asyncio.wait_for(
                _call_upstream(payload, candidates), timeout=remaining
            )
        except (asyncio.TimeoutError, TimeoutError):
            # run 级超时：不丢已有结果，也不把整个请求变成 5xx。
            await _audit_round(
                user_oid=user_oid,
                model=chosen,
                request_id=run_id,
                round_no=round_no,
                snapshot=snapshot,
                status="timeout",
                http_status=504,
                error_code=RUN_TIMEOUT_CODE,
                latency_ms=_elapsed_ms(started),
                provider=candidates[0].get("name") if candidates else None,
                key_scope=candidates[0].get("scope") if candidates else None,
            )
            stop_reason = "timeout"
            break
        except AgentError as exc:
            last_entry = getattr(exc, "entry", None) or (
                candidates[0] if candidates else None
            )
            await _audit_round(
                user_oid=user_oid,
                model=chosen,
                request_id=run_id,
                round_no=round_no,
                snapshot=snapshot,
                status=_audit_status(exc.code),
                http_status=exc.status_code,
                error_code=exc.code,
                latency_ms=_elapsed_ms(started),
                provider=last_entry.get("name") if last_entry else None,
                key_scope=last_entry.get("scope") if last_entry else None,
            )
            exc.audited = True  # type: ignore[attr-defined]
            raise

        rounds += 1
        tokens = ai_usage.token_fields(result.get("usage"))
        units = ai_usage.units_from_total_tokens(tokens["total_tokens"])
        usage = _add_usage(usage, tokens)

        # 先解析再记账：响应体形状不对时，这一轮应该是一行 error（没产出任何
        # 可用结果），而不是「success + 答案丢失」。
        try:
            content, tool_calls = _message_of(result)
        except AgentError as exc:
            await _audit_round(
                user_oid=user_oid,
                model=chosen,
                request_id=run_id,
                round_no=round_no,
                snapshot=snapshot,
                status="error",
                http_status=exc.status_code,
                error_code=exc.code,
                latency_ms=_elapsed_ms(started),
                provider=entry.get("name"),
                key_scope=entry.get("scope"),
                tokens=tokens,
                cache_key=cache_key,
            )
            exc.audited = True  # type: ignore[attr-defined]
            raise

        total_tokens += _round_tokens(tokens, payload, content)
        last_prompt_tokens = tokens.get("prompt_tokens")

        if _billable(entry):
            # 后置扣减是 best-effort：回写失败不能丢掉已经生成的答案。
            try:
                await quota.consume(user_oid, "chat", units, roles=roles)
            except Exception as exc:  # noqa: BLE001 - 回写失败不能丢掉答案
                _logger.warning(
                    "ai agent quota write-back failed: %s", type(exc).__name__
                )

        await _audit_round(
            user_oid=user_oid,
            model=chosen,
            request_id=run_id,
            round_no=round_no,
            snapshot=snapshot,
            status="success",
            http_status=200,
            latency_ms=_elapsed_ms(started),
            provider=entry.get("name"),
            key_scope=entry.get("scope"),
            units=units,
            tokens=tokens,
            cache_key=cache_key,
        )

        finish_reason = _finish_reason(result)
        handled_tools: list[dict] = []

        if not tool_calls:
            # 收尾回调：即使模型自己停也要交出去，否则最后一轮的回答不落库。
            await _emit(
                on_round,
                _round_payload(
                    round_no=round_no,
                    content=content,
                    finish_reason=finish_reason,
                    tool_calls=[],
                    tools=[],
                    tokens=tokens,
                    units=units,
                ),
            )
            # 最后一轮也可能把水位顶到阈值：收尾后仍判一次（每 run 最多压一次）。
            if on_summary is not None and not compacted:
                conversation, compacted = await _maybe_compact(
                    conversation=conversation,
                    compacted=compacted,
                    model=chosen,
                    candidates=candidates,
                    user_oid=user_oid,
                    roles=roles,
                    run_id=run_id,
                    snapshot=snapshot,
                    prompt_tokens=last_prompt_tokens,
                    on_summary=on_summary,
                )
            stop_reason = None
            break

        # 预算闸（工具执行之前）：超预算的那一轮不落任何业务写入。
        if total_tokens >= budget:
            await _emit(
                on_round,
                _round_payload(
                    round_no=round_no,
                    content=content,
                    finish_reason=finish_reason,
                    tool_calls=tool_calls,
                    tools=[],
                    tokens=tokens,
                    units=units,
                ),
            )
            stop_reason = "token_budget"
            break

        # 把助手的工具调用原样写回对话：OpenAI 要求 tool 消息必须能对上一条
        # assistant 的 tool_calls，否则上游 400。
        conversation.append(
            {
                "role": "assistant",
                "content": content,
                "tool_calls": tool_calls,
            }
        )

        interrupted = False
        for index, call in enumerate(tool_calls):
            # 每个工具执行前再判一次取消：工具可能有副作用，「已经点取消之后
            # 再跑下一个」是最不该发生的。
            if await _should_stop(should_stop):
                interrupted = True
                break

            _call_id, tool_name, _args = _tool_call_parts(call)
            tool = get_tool(tool_name)
            if index >= MAX_TOOL_CALLS_PER_ROUND:
                # 超出单轮上限：不执行，但仍要给每个 call 一条 tool 消息，否则
                # 下一轮上游会因为「tool_calls 没有对应结果」直接 400。
                note = {
                    "error": "too_many_tool_calls",
                    "message": (
                        f"单轮最多执行 {MAX_TOOL_CALLS_PER_ROUND} 个工具调用，"
                        "本次超出上限的部分未执行；请分批调用。"
                    ),
                }
                message = _tool_message(_call_id, note)
                conversation.append(message)
                handled_tools.append(
                    {
                        "tool_call_id": _call_id,
                        "tool_name": tool_name or None,
                        "content": message["content"],
                    }
                )
                await _write_event(
                    request_id=run_id,
                    round_no=round_no,
                    tool_name=tool_name or None,
                    tool_kind=tool.kind if tool is not None else None,
                    args_summary=_args_text(_args),
                    result_summary=(
                        f"超出单轮工具调用上限（{MAX_TOOL_CALLS_PER_ROUND}），未执行"
                    ),
                    status="denied",
                    user_oid=user_oid,
                )
                events.append(
                    {
                        "round_no": round_no,
                        "tool_name": tool_name or None,
                        "tool_kind": tool.kind if tool is not None else None,
                        "args_summary": _args_text(_args),
                        "result_summary": note["message"],
                        "status": "denied",
                    }
                )
                continue

            message, created, status, event_summary = await _run_tool(
                call=call, user=user, request_id=run_id, round_no=round_no
            )
            conversation.append(message)
            handled_tools.append(
                {
                    "tool_call_id": message.get("tool_call_id"),
                    "tool_name": tool_name or None,
                    "content": message.get("content"),
                }
            )
            events.append(
                {
                    "round_no": round_no,
                    "tool_name": tool_name or None,
                    "tool_kind": tool.kind if tool is not None else None,
                    "args_summary": _args_text(_args),
                    "result_summary": event_summary,
                    "status": status,
                }
            )
            if created is not None:
                proposals.append(created)

        await _emit(
            on_round,
            _round_payload(
                round_no=round_no,
                content=content,
                finish_reason=finish_reason,
                tool_calls=tool_calls,
                tools=handled_tools,
                tokens=tokens,
                units=units,
            ),
        )

        if interrupted:
            # 已跑完的轮次照常交付，已执行的工具结果保留；不再发下一轮。
            stop_reason = "cancelled"
            break

        if on_summary is not None and not compacted:
            conversation, compacted = await _maybe_compact(
                conversation=conversation,
                compacted=compacted,
                model=chosen,
                candidates=candidates,
                user_oid=user_oid,
                roles=roles,
                run_id=run_id,
                snapshot=snapshot,
                prompt_tokens=last_prompt_tokens,
                on_summary=on_summary,
            )
    else:
        # for-else：跑满 max_iterations 仍在要求工具 → 截断。
        stop_reason = "max_iterations"

    if stop_reason == "max_iterations" and not content:
        content = (
            f"已达到单次会话的最大工具调用轮数（{max_iterations} 轮），"
            "请缩小问题范围后重试。"
        )
    elif stop_reason == "token_budget" and not content:
        content = (
            f"已达到单次会话的 token 预算（{budget}），请缩小问题范围后重试。"
        )

    # run 完成处评估「AI 对话请求突发」：一次 run 最多只评估一次（不是每轮）。
    # 规则自带 60 秒 (trigger, user) 节流与宽 except，这里不再包 try。
    await risk_monitor.evaluate("ai_chat_burst", user_oid)

    return {
        "request_id": run_id,
        "content": content,
        "stop_reason": stop_reason,
        "rounds": rounds,
        "usage": usage,
        "events": events,
        "proposals": proposals,
    }


async def _audit_round(
    *,
    user_oid: str,
    model: str,
    request_id: str,
    round_no: int | None,
    snapshot: str | None,
    status: str,
    http_status: int | None,
    latency_ms: int,
    error_code: str | None = None,
    provider: str | None = None,
    key_scope: str | None = None,
    units: int = 0,
    tokens: dict | None = None,
    cache_key: str | None = None,
    purpose: str = PURPOSE_AGENT,
) -> None:
    """一轮一条流水（``capability='chat'`` + ``purpose`` + ``round_no``）。

    ``purpose='compact'`` 是压缩调用：``round_no=None``（它不是工具循环的一轮），
    额度照扣但预算不计（§5.4）。
    """
    await audit.record(
        user_oid=user_oid,
        capability="chat",
        endpoint=audit.ENDPOINT_AGENT,
        provider=provider,
        model=model,
        status=status,
        http_status=http_status,
        error_code=error_code,
        latency_ms=latency_ms,
        units=units,
        request_id=request_id,
        key_scope=key_scope,
        purpose=purpose,
        round_no=round_no,
        roles_snapshot=snapshot,
        cache_key=cache_key,
        **(
            tokens
            or {"prompt_tokens": None, "completion_tokens": None, "total_tokens": None}
        ),
    )


__all__ = [
    "DEFAULT_RUN_TIMEOUT_SECONDS",
    "MAX_TOOL_CALLS_PER_ROUND",
    "MAX_TOOL_RESULT_CHARS",
    "PROMPT_VERSION",
    "PURPOSE_AGENT",
    "PURPOSE_COMPACT",
    "run",
    "run_timeout_seconds",
]
