"""agent 执行循环（OpenAI function-calling，逐轮计费、逐轮审计）。

一轮 = 一次上游调用。循环这么走：

1. run 级截止时间检查 + 频控 / 额度预检（复用 ``services.ai.rate_limit`` /
   ``quota``，与 ``/v1/chat/completions`` 同一套机制，不另造）；
2. ``providers.chat_completion``（**强制** ``stream=False``：流式下没法在工具调用
   之前拿到完整的 ``tool_calls``，而 agent 的下一步取决于它）；
3. 有 ``tool_calls`` → 逐个过白名单 + 权限（+ 现查库的 ``guard``）→ 执行（读）或
   生成提案（写）→ 结果按 OpenAI 的 ``role="tool"`` 消息追加 → 进入下一轮；
4. 没有 ``tool_calls`` → 终止，返回 ``content``。

四个成本 / 时间闸：

* ``AI_AGENT_MAX_ITERATIONS``（默认 10）：轮数上限，防「模型反复调工具不收敛」；
* ``AI_AGENT_TOKEN_BUDGET``（默认 100000）：累计 token 上限，防「单轮很长 × 很多轮」；
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
import json
import logging
import time
from typing import Any
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

from . import proposal, tool_impl
from .prompt import PROMPT_VERSION, SYSTEM_PROMPT
from .tool_impl import AgentError
from .tools import READ, WRITE, get_tool, prepare_arguments, run_allowed, tool_specs

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
    """确定这次 run 用的模型与候选 provider。

    ``model`` 缺省时取目录里第一个可用模型：前端不该为了「随便用」而先发一次
    ``/v1/models``；显式指定的模型必须在**该用户可见目录**里（个人 key 只对本人
    可见，不能靠猜名字用别人的模型）。
    """
    entries = await credentials.catalog(user_oid)
    if not entries:
        raise AgentError("AI_NO_PROVIDER", routing.NO_PROVIDER_MESSAGE, 503)

    available = await ai_config.allowed_models_for(user_oid)
    if not available:
        raise AgentError("AI_NO_PROVIDER", routing.NO_PROVIDER_MESSAGE, 503)

    if model is None or model == "":
        chosen = next(iter(available))
    elif isinstance(model, str) and model in available:
        chosen = model
    else:
        raise AgentError(
            "model_not_found", "the requested model is not available", 404
        )

    try:
        selected = routing.candidates(entries)
    except RouteError as exc:
        raise AgentError(exc.code, exc.message, exc.status_code) from None

    candidates = [
        entry for entry in selected if chosen in (entry.get("models") or [])
    ][:MAX_PROVIDER_ATTEMPTS]
    if not candidates:
        # 模型在目录里但 provider 正在冷却。
        raise AgentError("AI_NO_PROVIDER", routing.NO_PROVIDER_MESSAGE, 503)
    return chosen, candidates


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

    if tool.kind == WRITE:
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

    try:
        result = await tool.impl(user, **kwargs)
    except AgentError as exc:
        note = _summary_text(f"执行失败：{exc.message}")
        await _write_event(
            request_id=request_id,
            round_no=round_no,
            tool_name=tool.name,
            tool_kind=READ,
            args_summary=args_text,
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
            tool_kind=READ,
            args_summary=args_text,
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
        tool_kind=READ,
        args_summary=args_text,
        result_summary=note,
        status="executed",
        user_oid=user_oid,
    )
    return (_tool_message(call_id, result), None, "executed", note)


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
    """客户端消息只允许 ``user`` / ``assistant``。

    ``system`` 由服务端注入（:data:`services.agent.prompt.SYSTEM_PROMPT`）：
    客户端能写 system 就等于能改行为护栏，也能让缓存前缀随人漂移。``tool`` 角色
    同样禁止——工具结果只能由服务端自己产生。
    """
    if not isinstance(messages, list) or not messages:
        raise AgentError(
            "AI_AGENT_BAD_ARGUMENTS", "messages must be a non-empty array", 400
        )
    cleaned: list[dict] = []
    for item in messages:
        if not isinstance(item, dict):
            raise AgentError(
                "AI_AGENT_BAD_ARGUMENTS", "each message must be an object", 400
            )
        role = item.get("role")
        if role == "system":
            raise AgentError(
                "AI_AGENT_BAD_ARGUMENTS",
                "system messages are injected by the server; role must be user|assistant",
                400,
            )
        if role not in ("user", "assistant"):
            raise AgentError(
                "AI_AGENT_BAD_ARGUMENTS",
                "message role must be user|assistant",
                400,
            )
        if not isinstance(item.get("content"), (str, list)):
            raise AgentError(
                "AI_AGENT_BAD_ARGUMENTS",
                "message content must be a string or content array",
                400,
            )
        cleaned.append({"role": role, "content": item["content"]})
    return cleaned


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


async def run(
    *,
    user: dict,
    messages: list[dict],
    model: str | None = None,
    request_id: str | None = None,
) -> dict:
    """跑完一次 agent 会话。

    返回 ``{request_id, content, stop_reason, rounds, usage, events, proposals}``。
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
    conversation: list[dict] = [
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

    for round_no in range(1, max_iterations + 1):
        # 0) run 级截止时间：超时不发新一轮（上游调用另有 wait_for 兜住长尾）。
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
            "model": chosen,
            "messages": conversation,
            # 强制非流式：工具调用的完整参数只在整条响应里，流式边收边执行等于
            # 猜半个 JSON。
            "stream": False,
            "tools": tool_specs(),
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

        if not tool_calls:
            stop_reason = None
            break

        # 预算闸（工具执行之前）：超预算的那一轮不落任何业务写入。
        if total_tokens >= budget:
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

        for index, call in enumerate(tool_calls):
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
                conversation.append(_tool_message(_call_id, note))
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
    round_no: int,
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
) -> None:
    """一轮一条流水（``capability='chat'`` + ``purpose='agent'`` + ``round_no``）。"""
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
        purpose=PURPOSE_AGENT,
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
    "run",
    "run_timeout_seconds",
]
