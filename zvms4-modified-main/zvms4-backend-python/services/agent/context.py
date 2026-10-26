"""上下文组装与压缩（纯函数 + 一个显式的压缩执行器）。

契约见 ``docs/AI_CONVERSATION_DESIGN.md`` §5。这个模块只做两件事：

1. 把「系统提示 + 摘要 + 历史 + 本轮消息」拼成一次上游调用的 ``messages``；
2. 在超水位时切出「旧段 / 近段」，并提供一个注入式 ``complete`` 的摘要执行器。

为什么**不写库**：压缩成功后要重建在飞 run 的 ``conversation`` 列表（§5.3 的
「在飞重建」），假如此处直接写库，调用方就得在「DB 已更新、内存还是旧列表」之间
猜哪个是真的。这里一切以返回值说话，落库与降级标记由 run 侧决定。

为什么摘要必须放在**第二条 system**（§5.1）：``prompt_cache.prefix_fingerprint``
只认从 index 0 开始连续的 system 消息。摘要塞尾部 → 指纹退化成「全站同 key」，
别人的前缀缓存与你的尾部拼在一起，命中率与隔离性双输；放第二条 system →
前缀只在压缩点变化，这正是可接受的那点代价。

为什么切点只能在 ``role='user'`` 之前：``assistant(tool_calls)`` 与它的
``role='tool'`` 结果在上游协议里是一条原子链——被切开就是 400，而且没有任何
「补一条占位 tool」的合法写法。所以切点不是「按条数切」，而是「按轮切」。
"""

from __future__ import annotations

import inspect
import json
import math
from typing import Any, Awaitable, Callable

import settings

from services import settings_runtime
from services.ai.usage import CHARS_PER_TOKEN

DEFAULT_CONTEXT_LIMIT = 200_000
DEFAULT_COMPACT_RATIO = 0.75
DEFAULT_KEEP_TURNS = 6
DEFAULT_SUMMARY_MAX_TOKENS = 800

# 配置项名字集中在这里：``services/ai/config.py`` 不归本车道维护，全部用
# ``getattr`` 读取并在本模块做兜底，配置缺失时模块照常可用。
MODEL_LIMITS_SETTING = "AI_MODEL_CONTEXT_LIMITS"
DEFAULT_LIMIT_SETTING = "AI_DEFAULT_CONTEXT_LIMIT"
COMPACT_RATIO_SETTING = "AI_CONTEXT_COMPACT_RATIO"
KEEP_TURNS_SETTING = "AI_CONTEXT_KEEP_TURNS"

FALLBACK_MODEL_KEY = "*"

_SYSTEM_ROLES = ("system", "developer")

# 摘要消息的固定抬头。给模型一个「这段不是当前任务」的显式信号；抬头是常量，
# 摘要正文不变则整条 system 消息逐字不变，前缀指纹才不会抖。
SUMMARY_HEADER = "以下是本次会话更早部分的摘要（原文已不在上下文中）：\n\n"

SUMMARY_SYSTEM_PROMPT = (
    "你是对话压缩器。把用户提供的早期对话记录压缩成一段可直接接续对话的摘要。\n"
    "要求：\n"
    "1. 以第三人称陈述事实，保留已确认的结论、关键标识（活动 id、文件路径、错误码）、"
    "用户明确的偏好与承诺；\n"
    "2. 保留未完成事项与待确认提案（谁、对什么对象、要做什么），它们是后续追问的锚点；\n"
    "3. 丢弃寒暄、重复的中间步骤与已被后续结果推翻的猜测；\n"
    "4. 不要编造记录里没有的内容，不要输出「以下是摘要」之类的前缀，直接给正文；\n"
    "5. 用简体中文，尽量控制在 500 字以内。"
)

# 摘要调用的输入上限（字符）。摘要本身要读完全部旧段，旧段越大越贵；超限时保留
# **靠近切点**的那部分——离当前问题越远的内容，对摘要的贡献越小。
SUMMARY_INPUT_MAX_CHARS = 24000

# 渲染摘要输入时单条消息 / 单个工具结果的截断长度：工具结果动辄 8000 字符，
# 原样喂给摘要器会让 token 全花在日志正文上。
SUMMARY_MESSAGE_MAX_CHARS = 600
SUMMARY_TOOL_MAX_CHARS = 300

Complete = Callable[[list[dict], int], "Awaitable[str] | str"]


def _positive_int(value: Any, default: int) -> int:
    """正整数配置兜底：bool 是 int 的子类，必须先排除。"""
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return default
    return value


def _messages_of(value: Any) -> list[dict]:
    """容忍 ``None`` / 混入非 dict 的列表：调用方常把 ORM 行直接拆出来。"""
    if not isinstance(value, (list, tuple)):
        return []
    return [item for item in value if isinstance(item, dict)]


def context_limit(model: str | None) -> int:
    """模型上下文上限（token）。

    优先 ``AI_MODEL_CONTEXT_LIMITS[model]``，其次该字典的 ``"*"`` 兜底键，最后
    ``AI_DEFAULT_CONTEXT_LIMIT``。非法值（0 / 负数 / bool / 字符串）一律当作没配，
    继续往下找——把 ``0`` 当真会让每次调用都被判超限，整条链路直接死掉。

    默认值本身可以被运行期设置 ``ai.context_limit_default`` 覆盖（在线改完即时
    生效）；没有覆盖时保持原来的 ``settings.AI_DEFAULT_CONTEXT_LIMIT`` 链路。
    """
    override = settings_runtime.get_override("ai.context_limit_default")
    if isinstance(override, int) and not isinstance(override, bool) and override > 0:
        default = override
    else:
        default = _positive_int(
            getattr(settings, DEFAULT_LIMIT_SETTING, DEFAULT_CONTEXT_LIMIT),
            DEFAULT_CONTEXT_LIMIT,
        )
    limits = getattr(settings, MODEL_LIMITS_SETTING, None)
    if not isinstance(limits, dict):
        return default

    if isinstance(model, str):
        key = model.strip()
        if key:
            exact = _positive_int(limits.get(key), 0)
            if exact:
                return exact
    wildcard = _positive_int(limits.get(FALLBACK_MODEL_KEY), 0)
    if wildcard:
        return wildcard
    return default


def compact_ratio() -> float:
    """压缩阈值比例（``0 < ratio <= 1``），非法值回退 0.75。"""
    raw = getattr(settings, COMPACT_RATIO_SETTING, DEFAULT_COMPACT_RATIO)
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return DEFAULT_COMPACT_RATIO
    ratio = float(raw)
    # NaN 与 0 / 负数 / >1 都没意义：前者比较恒 False，后两者会把水位钉死在边界。
    if not 0.0 < ratio <= 1.0:
        return DEFAULT_COMPACT_RATIO
    return ratio


def configured_keep_turns() -> int:
    """保留最近多少轮原文（默认 6）。

    名字带 ``configured_``：``plan_compaction`` 的形参就叫 ``keep_turns``，同名函数
    会在函数体里被形参遮住。
    """
    return _positive_int(
        getattr(settings, KEEP_TURNS_SETTING, DEFAULT_KEEP_TURNS), DEFAULT_KEEP_TURNS
    )


def estimate_tokens(messages: list[dict]) -> int:
    """``len(json.dumps(messages)) / 4`` 向上取整（与 ``usage.prompt_chars`` 同尺）。

    中英混排下这是**下界**（CJK 常见 1~2 字符/token），所以它只用于「还没实测
    值」时的水位估算；有实测 ``prompt_tokens`` 时调用方应优先用实测值。
    """
    items = _messages_of(messages)
    if not items:
        return 0
    try:
        raw = json.dumps(items, ensure_ascii=False)
    except (TypeError, ValueError):
        # 混进不可序列化对象时退化为 repr：估算略偏大，也好过抛给调用方。
        raw = json.dumps(items, ensure_ascii=False, default=str)
    return math.ceil(len(raw) / CHARS_PER_TOKEN)


def _tool_call_ids(message: dict) -> list[str]:
    calls = message.get("tool_calls")
    if not isinstance(calls, list):
        return []
    ids: list[str] = []
    for call in calls:
        if not isinstance(call, dict):
            continue
        call_id = call.get("id")
        if isinstance(call_id, str) and call_id:
            ids.append(call_id)
    return ids


def _drop_dangling_tool_calls(messages: list[dict]) -> list[dict]:
    """丢掉「半条工具链」：缺 tool 回复的 assistant、以及无主的 tool 消息。

    §2.2 不变量 2 的回放侧防御。上游对 ``assistant(tool_calls)`` 与对应 ``tool``
    的要求是「一条都不能少」，残链只有两种结局：400 或者模型拿到半截事实后瞎猜。
    宁可少一轮工具结果，也不发一个 400 把整个 run 打挂。
    """
    items = _messages_of(messages)
    kept: list[dict] = []
    index = 0
    total = len(items)
    while index < total:
        message = items[index]
        role = message.get("role")

        if role == "tool":
            # 走到这里说明它的 assistant 已被丢弃（或本来就无主）。
            index += 1
            continue

        if role != "assistant" or not message.get("tool_calls"):
            kept.append(message)
            index += 1
            continue

        ids = _tool_call_ids(message)
        following: list[dict] = []
        cursor = index + 1
        while cursor < total and items[cursor].get("role") == "tool":
            following.append(items[cursor])
            cursor += 1

        answered = {
            item.get("tool_call_id")
            for item in following
            if isinstance(item.get("tool_call_id"), str)
        }
        # ids 为空 = 调用结构本身不可解析，同样按「残缺」处理。
        if not ids or any(call_id not in answered for call_id in ids):
            index = cursor
            continue

        matched = {call_id: None for call_id in ids}
        kept.append(message)
        for item in following:
            if item.get("tool_call_id") in matched:
                kept.append(item)
        index = cursor
    return kept


def build_messages(
    system_prompt: str,
    summary: str | None,
    history: list[dict],
    pending: list[dict],
) -> list[dict]:
    """按 §5.1 的固定顺序组装：system → 摘要 system → 历史 → 本轮。

    摘要单独占一条 system 而不是拼进尾部，是为了让前缀指纹（只看 leading
    system）在「同一摘要」下保持稳定；两次未压缩调用之间则完全同前缀。

    历史与本轮一起做残链清理：跨边界的一条工具链（assistant 在历史里、tool 落在
    本轮的极端情况）也必须整体判定，否则切一半仍然是 400。
    """
    tail = _drop_dangling_tool_calls([*_messages_of(history), *_messages_of(pending)])

    messages: list[dict] = []
    if isinstance(system_prompt, str) and system_prompt:
        messages.append({"role": "system", "content": system_prompt})

    text = summary.strip() if isinstance(summary, str) else ""
    if text:
        messages.append({"role": "system", "content": SUMMARY_HEADER + text})

    messages.extend(dict(item) for item in tail)
    return messages


def needs_compaction(
    prompt_tokens: int | None, messages: list[dict], limit: int
) -> bool:
    """水位是否触顶：``tokens >= ceil(limit * ratio)``。

    ``prompt_tokens`` 是上一轮的实测值（上游 usage），有就优先用它——估算是下界，
    用它判超限会偏晚。传 ``None`` 表示没有实测（首轮 / 上游未报 usage），此时代码
    自己按 ``chars/4`` 估。
    """
    ceiling = _positive_int(limit, context_limit(None))
    threshold = math.ceil(ceiling * compact_ratio())

    measured = prompt_tokens
    usable = (
        measured
        if isinstance(measured, int)
        and not isinstance(measured, bool)
        and measured >= 0
        else None
    )
    tokens = usable if usable is not None else estimate_tokens(messages)
    return tokens >= threshold


def plan_compaction(
    messages: list[dict], keep_turns: int
) -> tuple[list[dict], list[dict]]:
    """切出 ``(older, recent)``：最近 ``keep_turns`` 轮原文留在 recent。

    返回 ``([], messages)`` 表示「不压缩」——没有 user 消息、轮数不足以切、或切点
    前的旧段本身是残链时，宁可原样发也不发一条被切坏的历史。
    """
    items = _messages_of(messages)
    if not items:
        return ([], [])

    turns = keep_turns
    if isinstance(turns, bool) or not isinstance(turns, int) or turns < 1:
        # 0 会把「最近 N 轮原文」这层安全垫整段撤掉，等于压缩后立刻失忆。
        turns = configured_keep_turns()

    user_indexes = [
        index for index, item in enumerate(items) if item.get("role") == "user"
    ]
    if not user_indexes:
        return ([], items)

    keep = min(turns, len(user_indexes))
    cut = user_indexes[-keep]
    if cut <= 0:
        return ([], items)

    older = items[:cut]
    # 旧段必须自洽：切点虽然落在 user 之前，但若输入本身有残链（缺 tool 的
    # assistant / 无主 tool），摘要会把这些残骸当事实写进去。
    if len(_drop_dangling_tool_calls(older)) != len(older):
        return ([], items)
    return (older, items[cut:])


def _clip(text: str, limit: int) -> str:
    if limit <= 0 or len(text) <= limit:
        return text
    return text[:limit] + "…"


def _render_message(message: dict) -> str:
    role = message.get("role")
    role = role if isinstance(role, str) else "unknown"
    content = message.get("content")
    body = content if isinstance(content, str) else ""
    parts = [f"[{role}] {_clip(body.strip(), SUMMARY_MESSAGE_MAX_CHARS)}"]

    ids = _tool_call_ids(message)
    if ids:
        names = []
        for call in message.get("tool_calls") or []:
            if not isinstance(call, dict):
                continue
            function = call.get("function")
            name = function.get("name") if isinstance(function, dict) else None
            names.append(name if isinstance(name, str) and name else "?")
        parts.append(f"[调用工具] {', '.join(names) if names else '?'}")

    if role == "tool":
        name = message.get("tool_name")
        if isinstance(name, str) and name:
            parts[0] = f"[tool:{name}] " + _clip(body.strip(), SUMMARY_TOOL_MAX_CHARS)
    return "\n".join(part for part in parts if part)


def compose_summary_prompt(older: list[dict]) -> list[dict]:
    """旧段 → 摘要器的 ``messages``（固定 system + 一段渲染后的对话记录）。

    输入超 ``SUMMARY_INPUT_MAX_CHARS`` 时从**最旧**的一端丢：靠近切点的内容才是
    接下来对话要用的上下文。
    """
    lines = [_render_message(item) for item in _messages_of(older)]
    transcript = "\n\n".join(line for line in lines if line)
    if len(transcript) > SUMMARY_INPUT_MAX_CHARS:
        # 从尾部保留：先截断再对齐到下一段起始，避免半个角色头被留下。
        kept = transcript[-SUMMARY_INPUT_MAX_CHARS:]
        marker = "\n\n["
        position = kept.find(marker)
        if position > 0:
            kept = kept[position + len(marker) - 1 :]
        transcript = "…（更早的记录已省略）\n\n" + kept

    return [
        {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
        {"role": "user", "content": "以下是需要压缩的对话记录：\n\n" + transcript},
    ]


def parse_summary(text: str, limit_chars: int) -> str:
    """清洗上游摘要：去首尾空白 + 截断到 ``limit_chars``（非 str 视为空）。"""
    if not isinstance(text, str):
        return ""
    limit = _positive_int(limit_chars, DEFAULT_SUMMARY_MAX_TOKENS * CHARS_PER_TOKEN)
    return text.strip()[:limit].strip()


def hard_truncate(messages: list[dict], limit_tokens: int) -> list[dict]:
    """超限时的最后一道闸：从最旧开始丢，直到估回水位以下。

    约束是「不许留下悬空配对」：丢掉 ``assistant(tool_calls)`` 后紧跟的 ``tool``
    必须一起走（它们已经无主）。开头几条 system 永不丢——那是行为护栏与缓存前缀，
    丢了等于换了一个助手。
    """
    budget = _positive_int(limit_tokens, DEFAULT_CONTEXT_LIMIT)
    items = [dict(item) for item in _messages_of(messages)]

    head_end = 0
    while head_end < len(items) and items[head_end].get("role") in _SYSTEM_ROLES:
        head_end += 1
    head, rest = items[:head_end], items[head_end:]

    def drop_orphans(target: list[dict]) -> None:
        while target and target[0].get("role") == "tool":
            target.pop(0)

    drop_orphans(rest)
    while len(rest) > 1 and estimate_tokens(head) + estimate_tokens(rest) > budget:
        rest.pop(0)
        # 被丢的可能是 assistant(tool_calls)：它后面的 tool 就地变成孤儿。
        drop_orphans(rest)
    drop_orphans(rest)
    return head + rest


async def compact(
    older: list[dict],
    recent: list[dict],
    *,
    complete: Complete,
    max_tokens: int = DEFAULT_SUMMARY_MAX_TOKENS,
) -> str:
    """把 ``older`` 压成摘要正文。

    ``complete`` 由调用方注入（``complete(messages, max_tokens) -> str``）：换候选
    provider、加计费审计、做 ``quota.peek`` 都在调用方那一层，本模块只负责「喂什么、
    怎么裁」。异常**不吞**：摘要失败后是重试候选还是降级硬截断，只有调用方知道。
    """
    items = _messages_of(older)
    # 没有任何旧轮次就不该花一次上游调用：空摘要等价于「未压缩」。
    if not items:
        return ""

    budget = _positive_int(max_tokens, DEFAULT_SUMMARY_MAX_TOKENS)
    prompt = compose_summary_prompt(items)
    result = complete(prompt, budget)
    if inspect.isawaitable(result):
        result = await result
    # ``recent`` 保留在签名里：调用方总是成对拿到两段，摘要器只需 old；这里显式
    # 忽略它，避免调用方误以为要「自己先拼好」。
    del recent
    return parse_summary(result, budget * CHARS_PER_TOKEN)
