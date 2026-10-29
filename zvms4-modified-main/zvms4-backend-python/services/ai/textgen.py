"""受控文本生成：把管理员的一句运维意图起草成一条 **待人工审阅** 的命令。

本模块只产出文本——不建立 SSH 连接、不执行命令、不碰终端。执行只发生在两条
既有路径上：人把文本粘进已过双人 TOTP 的 Web 终端（``routers/system_ssh_router``
的 ``/ws``），或 agent 的 ``ssh_exec.run_once``（提案 + 双人码）。因此这里
**不 import** ``services.ssh_bridge`` / ``services.agent.ssh_exec``：没有句柄，
就不存在「顺手执行一下」的可能。

错误一律抛 :class:`services.ai.routing.RouteError`（带 ``code`` + HTTP 状态），
由路由层翻成 ``detail=code``；本模块不产生 HTTP 响应体。计费口径与
``/api/ai/v1/chat/completions`` 一致：个人 key 不占平台额度，其余按 chat 扣。
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import NoReturn
from uuid import uuid4

from . import audit, credentials, providers, quota, rate_limit, routing, usage
from . import config as ai_config
from . import model_rates
from . import thinking as thinking_param

logger = logging.getLogger(__name__)

#: 审计里标识「哪个场景在用 AI」（``AI_CALL_LOGS.PURPOSE`` 是 VARCHAR2(24)）。
SUGGEST_PURPOSE = "system-ssh-suggest"

#: 与 ``ssh_exec.MAX_COMMAND_CHARS`` / 终端粘贴上限对齐：超过就没法安全落进终端。
MAX_COMMAND_CHARS = 1024
#: 解释只做提示，超了截断而不是报错（它不进终端）。
MAX_EXPLANATION_CHARS = 200
#: 环境信息的服务端兜底上限（路由层还有一层 pydantic 校验）。
MAX_CONTEXT_CHARS = 2000

#: 服务端固定 system prompt：**不接受客户端覆盖**。规则写死在这里而不是让前端
#: 传模板，否则「绕过安全规则」就只是一个参数的事。
SYSTEM_PROMPT = """\
你是 zvms 运维台的「SSH 命令起草助手」。你的唯一职责是：把管理员的一句运维意图
写成一条可以交给**人类审阅后执行**的 shell 命令。你不连接任何主机，也不执行任何
命令，你只输出文本。

输出格式：只输出一个 JSON 对象，不要 markdown 围栏，不要前后缀说明文字。
{"command": "<一条命令>", "explanation": "<不超过 200 字的中文说明>"}

硬性规则（违反任何一条，这条建议就是不可用的）：
1. command 必须是单行：不要换行，不要用反斜杠续行，不要写 heredoc。
2. 禁止交互式、长驻、或必须占用 TTY 的程序：vim/vi/nano/emacs/top/htop/less/
   more/man/watch/tail -f；`ssh`、`su`、`sudo -i` 打开的交互式 shell；不带
   `-c`/`-e` 的 python/psql/mysql 等 REPL；会等待输入确认的 `passwd`、不带 `-y`
   的 apt/dpkg/apt-get。
3. 禁止无差别破坏：`rm -rf /`、`rm -rf /*`、`rm -rf ~`、写块设备的 dd/mkfs/
   mkswap、`chmod -R 777 /`、fork bomb、`> /dev/sdX`、`shutdown`、`reboot`。
4. 只做意图里要求的那一件事；不要顺手清理、不要猜目标机上不存在的路径。
5. 路径优先用绝对路径；不确定的路径必须在 explanation 里写明「需先确认」。
6. 不要把凭据写进命令：口令/token/私钥用 `<PASSWORD>` 这类占位符代替，并在
   explanation 里提醒管理员手动补全。
7. 宁可给出「先查看现状」的只读命令，也不要给出影响面不明的写操作。

explanation 用中文，不超过 200 字：说清这条命令做什么、影响范围、以及执行前必须
确认什么。
"""

# 模型常常把命令包在 ``` 里，或者顺手带上 ``$ `` 提示符。
_FENCE_OPEN_RE = re.compile(r"^```[A-Za-z0-9_+.-]*")
_PROMPT_PREFIX_RE = re.compile(r"^[$#]\s+")
# 换行 + 两侧空白压成一个空格：换行一旦被前端粘进终端就等于按下回车，
# 会立刻执行「人还没看完的那一部分」。
_LINE_BREAK_RE = re.compile(r"[ \t]*\r?\n[ \t]*")


def _billable(entry: dict) -> bool:
    """个人 key 不占平台额度（钱是管理员自己出的），口径同 ``routers/ai_router``。"""
    return entry.get("scope") != credentials.SCOPE_PERSONAL


def _audit_status(code: str) -> str:
    """错误码 → ``AI_CALL_LOGS.STATUS`` 枚举（与 chat 面同一把尺子）。"""
    if code == "AI_TIMEOUT":
        return "timeout"
    if code == "AI_BUSY":
        return "busy"
    if code in ("AI_NO_PROVIDER", "AI_DISABLED", "model_not_found",
                "model_ambiguous", "AI_UPSTREAM_AUTH", "AI_UPSTREAM_REJECTED",
                "AI_UPSTREAM_UNREACHABLE"):
        return "rejected"
    if code in ("AI_RATE_LIMITED", "insufficient_quota"):
        return "rejected"
    return "error"


def _as_text(value: object) -> str:
    return value if isinstance(value, str) else ""


def single_line(text: str) -> str:
    """把模型给的命令收敛成**真正的一行**。

    不拒绝多行是刻意的：多行在人工审阅里仍然可读，而拒绝会让「模型多说了一句
    话」变成一次完全无用的失败。但也不能原样下发——带换行的文本粘进 xterm 时
    第一行会被立刻执行。折中是压平：内容不丢、语义可能变，人必须自己确认。
    """
    text = _PROMPT_PREFIX_RE.sub("", str(text or "").strip())
    return _LINE_BREAK_RE.sub(" ", text).strip()


def strip_fence(text: str) -> str:
    """去掉 ``` / ```json 围栏（只认整体包裹，不做全文扫描）。"""
    body = str(text or "").strip()
    if not body.startswith("```"):
        return body
    body = _FENCE_OPEN_RE.sub("", body, count=1).strip()
    if body.endswith("```"):
        body = body[:-3]
    return body.strip()


def parse_output(content: str) -> tuple[str, str]:
    """上游正文 → ``(command, explanation)``。

    JSON 解析失败时**整段当作命令**（并且因为要压成一行，前台渲染成一条长串），
    这是给「模型没听话但内容仍然有用」留的出口；但空命令直接判为上游故障，
    因为一条空命令在终端里按回车等于什么都不做，只会让人以为生成成功了。
    """
    text = strip_fence(content)
    command = ""
    explanation = ""
    try:
        data = json.loads(text)
    except ValueError:
        data = None
    if isinstance(data, dict):
        command = _as_text(data.get("command"))
        explanation = _as_text(data.get("explanation"))
    else:
        command = text

    command = single_line(command)
    if not command:
        raise routing.RouteError(
            "AI_UPSTREAM_ERROR", "upstream returned no command", 502
        )
    return command, single_line(explanation)[:MAX_EXPLANATION_CHARS]


def _content_of(result: object) -> str:
    """取 ``choices[0].message.content``（兼容 content 为分段数组的兼容层）。"""
    try:
        message = result["choices"][0]["message"]  # type: ignore[index]
    except (KeyError, IndexError, TypeError):
        raise routing.RouteError(
            "AI_UPSTREAM_ERROR", "upstream returned no message", 502
        ) from None
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, list):
        content = "".join(
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and isinstance(part.get("text"), str)
        )
    if not isinstance(content, str) or not content.strip():
        raise routing.RouteError(
            "AI_UPSTREAM_ERROR", "upstream returned empty content", 502
        )
    return content


def _user_message(intent: str, context: str | None) -> str:
    parts = [f"运维意图：{intent}"]
    if context:
        parts.append(f"环境信息（管理员提供，可能不完整）：{context[:MAX_CONTEXT_CHARS]}")
    parts.append("请按规则只输出 JSON 对象。")
    return "\n".join(parts)


async def _record_failure(
    *,
    user_oid: str,
    model: str | None,
    request_id: str,
    exc: routing.RouteError,
    latency_ms: int,
    entry: dict | None,
    roles: list[str] | None,
) -> None:
    """拒绝/失败也留一行流水：否则「谁在反复试探」在后台完全看不见。"""
    await audit.record(
        user_oid=user_oid,
        capability="chat",
        endpoint=audit.ENDPOINT_CHAT,
        provider=(entry or {}).get("name"),
        model=model,
        status=_audit_status(exc.code),
        http_status=exc.status_code,
        error_code=exc.code,
        latency_ms=latency_ms,
        request_id=request_id,
        key_scope=(entry or {}).get("scope"),
        purpose=SUGGEST_PURPOSE,
        roles_snapshot=audit.roles_snapshot(roles),
    )


async def suggest_ssh_command(
    user: dict,
    *,
    intent: str,
    context: str | None = None,
    model: str | None = None,
    thinking: str | None = None,
) -> dict:
    """起草一条 SSH 命令。返回 ``{command, explanation, model, request_id}``。

    这里**没有**任何「执行」出口：调用方拿到的是字符串，最多能把它显示给人看。

    ``thinking`` 是平台档位（``low`` / ``medium`` / ``high``，空 = 上游默认）。字段
    名随 provider 变（``reasoning_effort`` / 智谱的 ``thinking``），所以翻译必须等
    「这一条候选」确定之后：与 chat 面共用 :mod:`services.ai.thinking` 的同一套规则。
    """
    user_oid = str(user.get("_id") or user["id"])
    roles = user.get("per")
    request_id = uuid4().hex
    started = time.monotonic()

    async def reject(
        exc: routing.RouteError, entry: dict | None = None, *, model_name=None
    ) -> NoReturn:
        """拒绝路径先落一行流水再抛（与 chat 面一致：被拒的请求也要看得见）。"""
        await _record_failure(
            user_oid=user_oid,
            model=model_name or model,
            request_id=request_id,
            exc=exc,
            latency_ms=max(0, int((time.monotonic() - started) * 1000)),
            entry=entry,
            roles=roles,
        )
        raise exc

    if not ai_config.is_enabled():
        await reject(routing.RouteError("AI_DISABLED", "ai gateway is disabled", 503))

    entries = await credentials.catalog(user_oid)
    if not entries:
        # 一个可用 provider 都没有是配置问题，不是「模型选错了」。
        await reject(
            routing.RouteError("AI_NO_PROVIDER", routing.NO_PROVIDER_MESSAGE, 503)
        )

    available = await ai_config.allowed_models_for(user_oid)
    if not available:
        await reject(
            routing.RouteError("AI_NO_PROVIDER", routing.NO_PROVIDER_MESSAGE, 503)
        )

    selected_model = model or next(iter(available))
    try:
        resolved = credentials.resolve_model(entries, selected_model)
    except credentials.ModelAmbiguous as ambiguous:
        await reject(
            routing.RouteError("model_ambiguous", ambiguous.message, 400),
            model_name=selected_model,
        )
    if resolved is None:
        await reject(
            routing.RouteError(
                "model_not_found", "the requested model is not available", 404
            ),
            model_name=selected_model,
        )
    # 对外/审计用合格 ID，发给上游的是裸模型名。
    selected_model = resolved.qualified
    upstream_model = resolved.model

    # 熔断中的 provider 不出现在 candidates 里；模型在目录里但没有可用 provider
    # 时与 chat 面一致报 AI_NO_PROVIDER。
    try:
        selected = routing.candidates(entries)
    except routing.RouteError as exc:
        await reject(exc, model_name=selected_model)
    entry = next(iter(credentials.route_candidates(selected, resolved)), None)
    if entry is None:
        await reject(
            routing.RouteError("AI_NO_PROVIDER", routing.NO_PROVIDER_MESSAGE, 503),
            model_name=selected_model,
        )

    try:
        rate_limit.check(user_oid, "chat")
    except rate_limit.RateLimitError as exc:
        await reject(
            routing.RouteError("AI_RATE_LIMITED", exc.message, 429),
            entry,
            model_name=selected_model,
        )

    billable = _billable(entry)
    if billable:
        allowed, used, limit = await quota.peek(
            user_oid, "chat", 1, roles=roles
        )
        if not allowed:
            await reject(
                routing.RouteError(
                    "insufficient_quota",
                    f"monthly chat quota is exhausted ({used}/{limit})",
                    429,
                ),
                entry,
                model_name=selected_model,
            )

    payload = {
        "model": upstream_model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": _user_message(intent, context)},
        ],
        # 低温度：命令要稳定可复现，不需要「创意」。
        "temperature": 0.2,
        "stream": False,
    }

    try:
        api_key = await credentials.resolve_api_key(entry)
    except credentials.CredentialError as exc:
        await reject(
            routing.RouteError(exc.code, exc.message, exc.status_code),
            entry,
            model_name=selected_model,
        )

    # 档位 → 上游字段：只有拿到这一条候选才知道该发 ``reasoning_effort`` 还是 ``thinking``
    # （空档不加任何字段，交给上游默认）。
    thinking_param.apply(payload, thinking, entry)

    key = str(entry.get("key") or "")
    try:
        async with routing.slot():
            # 不接 safety_guard：本入口是 admin-only（SSH 命令起草），调用者的
            # ``per`` 必含 admin，``is_department`` 恒为 False。挂闸门只会白烧
            # 一次上游调用。覆盖清单里登记为 GATED（理由同此）。
            result = await providers.chat_completion(payload, entry, api_key)
    except routing.RouteError as exc:
        # 并发闸门满不是上游的错：算进熔断会把健康供应商也熔掉（同 chat 面）。
        if exc.code != "AI_BUSY":
            routing.mark_failure(key)
        await reject(exc, entry, model_name=selected_model)
    routing.mark_success(key)

    command, explanation = parse_output(_content_of(result))
    if len(command) > MAX_COMMAND_CHARS:
        # 与终端/单次执行的硬上限对齐：超长命令粘不进去，也不该被当成可执行文本。
        await reject(
            routing.RouteError(
                "AI_AGENT_BAD_ARGUMENT",
                f"generated command exceeds {MAX_COMMAND_CHARS} characters",
                400,
            ),
            entry,
            model_name=selected_model,
        )

    tokens = usage.token_fields(
        result.get("usage") if isinstance(result, dict) else None
    )
    # AF = 千 token × 模型倍率；没拿到 usage 时按字符估算（同一公式）。
    total_tokens = tokens["total_tokens"]
    if total_tokens is None:
        total_tokens = usage.estimate_tokens_from_chars(
            usage.prompt_chars(payload), len(command)
        )
    units = usage.af_units(
        total_tokens, await model_rates.resolve_rate(selected_model)
    )
    if billable:
        try:
            await quota.consume(user_oid, "chat", units, roles=roles)
        except Exception as exc:  # noqa: BLE001 - 回写失败不能丢掉已生成的答案
            logger.warning("ssh suggest quota write-back failed: %s", type(exc).__name__)

    await audit.record(
        user_oid=user_oid,
        capability="chat",
        endpoint=audit.ENDPOINT_CHAT,
        provider=entry.get("name"),
        model=selected_model,
        status="success",
        http_status=200,
        latency_ms=max(0, int((time.monotonic() - started) * 1000)),
        units=units,
        request_id=request_id,
        key_scope=entry.get("scope"),
        purpose=SUGGEST_PURPOSE,
        roles_snapshot=audit.roles_snapshot(roles),
        **tokens,
    )

    return {
        "command": command,
        "explanation": explanation,
        "model": selected_model,
        "request_id": request_id,
    }
