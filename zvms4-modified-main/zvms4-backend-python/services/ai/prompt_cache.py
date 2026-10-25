"""上游 Prompt Cache 的请求体加工（纯函数，无 IO、不读库、不记日志）。

有两层能力，默认只开第一层，因为它们对上游的要求完全不同：

* **``prompt_cache_key``（路由提示）**：OpenAI 2026 的标准请求字段，语义只有
  「同一前缀的请求尽量落到同一个缓存节点」。它不改 ``messages`` / ``tools`` 的
  任何内容，最坏情况是上游**忽略**它（缓存命中率不变，结果与计费都不受影响）。
  所以自动模式（``auto``）默认只做这一件事。
* **``cache_control``（显式断点）**：Anthropic 形状的标记，要**改写** content 的
  结构（字符串 → ``[{"type":"text",...}]``）。只有确认会把它翻译成上游原生缓存
  标记的兼容层（OpenRouter / LiteLLM 一类）才受得住；直连 OpenAI 官方端点带上
  它属于「未知字段」，严格校验请求体的实现直接 400。因此它挂在独立的白名单
  （``prompt_cache_breakpoint_hosts``）后面、且默认是空集——explicit 默认关。

为什么 key 用**稳定前缀指纹**而不是每次随机：

    随机 key 每次都不同 → 上游的路由亲和每次都被打散 → 缓存永远不命中，还多花
    一个字段的带宽。反过来，指纹只覆盖「稳定前缀」（model + tools + 开头连续的
    system/developer 消息），多轮对话的尾部变化不进指纹：同一段会话在轮次推进时
    仍然映射到同一个 key，命中率才成立。

为什么按 host 而不是按 provider 名判断：与 ``providers.wants_stream_usage`` 同
一个理由——网关里的 provider 名是运维起的标签，与上游真实实现没有绑定关系
（``ai_credentials.kind`` 只有一个 ``openai_compat``）。

本模块只产出「发给上游的 body」与「用于审计的 cache_key」，不落库：NULL 的语义
由调用方（``routers/ai_router``）负责，同 ``services/ai/usage.py`` 的不变式。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any
from urllib.parse import urlsplit

from . import config as ai_config

# 与 ``ai_config.PROMPT_CACHE_MODES`` 同一份来源（config 不依赖本模块，反方向
# 会成环，所以常量留在 config 里、这里只做别名）。
MODES = ai_config.PROMPT_CACHE_MODES

# ``prompt_cache_key`` 的上游上限。超长不是截断就是 400，宁可在本地截断。
KEY_MAX_LEN = 64

# 参与「稳定前缀」的角色。``developer`` 是 OpenAI 给 ``system`` 的新名字。
_SYSTEM_ROLES = ("system", "developer")

# 指纹长度：16 个 hex 字符（64 bit）。碰撞概率对「同机房内区分路由」足够，又给
# ``prefix:`` 留出足够空间（默认前缀 3 字符 + 冒号 + 16 = 20 << 64）。
_FINGERPRINT_LEN = 16


def host_of(entry: dict) -> str:
    """``entry["base_url"]`` 的 host（小写）；解析失败返回空串。

    语义与 ``services.ai.providers._host`` 完全一致；那边的实现改为委托本函数，
    保证「哪个 host 命中白名单」在两处只有一份判断。
    """
    if not isinstance(entry, dict):
        return ""
    base = entry.get("base_url")
    if not isinstance(base, str) or not base:
        return ""
    try:
        return (urlsplit(base).hostname or "").lower()
    except ValueError:
        return ""


def prefix_fingerprint(payload: dict) -> str:
    """稳定前缀的指纹（16 位 hex）；没有任何稳定成分时返回空串。

    参与计算的只有三样：``model``、``tools``（存在即整段参与）、以及 ``messages``
    里**从 index 0 开始连续**的 ``system`` / ``developer`` 消息。第一条非系统消息
    之后的内容一律不看：那里是多轮的尾部（用户改一个字就变），拿它算 key 等于
    每次换一个路由。

    规范化用 ``json.dumps(sort_keys=True)``：dict 的键序是 Python 的实现细节，
    不同构造路径写出同一个字典时键序可能不同，但语义相同——不排序就会出现「同一
    前缀两个 key」，白白丢掉亲和。
    """
    if not isinstance(payload, dict):
        return ""

    components: dict[str, Any] = {"model": ""}
    model = payload.get("model")
    if isinstance(model, str) and model:
        components["model"] = model

    tools = payload.get("tools")
    if tools is not None:
        components["tools"] = tools

    leading = _leading_system_messages(payload)
    if leading:
        components["messages"] = leading

    if len(components) == 1:
        # 只有空的 model：连「这段请求长什么样」都没法描述。
        return ""

    try:
        canonical = json.dumps(
            components,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        )
    except (TypeError, ValueError):
        return ""
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:_FINGERPRINT_LEN]


def request_body(payload: Any, entry: Any) -> tuple[Any, str | None]:
    """``(发给上游的 body, 审计用的 cache_key)``。

    ``payload`` 原样不动：调用方还要用它算估算字符数、记审计。所有变更都在浅拷贝
    上做，只有真要改的那一层（``messages`` 列表 / 其中的 message）才再拷一次。

    第二项只在「确实往 body 里放了 key」或「客户端自带 key」时非空；其余情况是
    ``None``，落库后就是 NULL（= 这次没参与缓存路由），不要用空串代替。
    """
    if not isinstance(payload, dict) or not isinstance(entry, dict):
        return payload, None

    mode = ai_config.prompt_cache_mode()
    if mode not in MODES or mode == "off":
        return payload, None

    host = host_of(entry)
    # 惰性拷贝：什么都没注入时直接把原 payload 传回去，省一次无意义的 dict 复制
    # （大 payload 的浅拷贝不贵，但「返回值恒等入参」让调用方更容易断言）。
    body: Any = payload
    cache_key: str | None = None

    if _allowed(host, ai_config.prompt_cache_key_hosts()):
        existing = payload.get("prompt_cache_key")
        if isinstance(existing, str) and existing.strip():
            # 客户端自己算的 key（例如它想让多个会话共享缓存）：原样保留，不覆盖。
            # 审计只存截断后的值，超长部分是客户端的问题，不该撑爆审计列。
            cache_key = existing.strip()[:KEY_MAX_LEN]
        else:
            fingerprint = prefix_fingerprint(payload)
            if fingerprint:
                key = f"{ai_config.prompt_cache_key_prefix()}:{fingerprint}"
                key = key[:KEY_MAX_LEN]
                body = dict(payload)
                body["prompt_cache_key"] = key
                cache_key = key
            # 指纹为空（例如只有一条 user 消息）：不注入。凭空的随机 key 只会
            # 打散路由亲和，比不传更糟。

    if mode == "explicit" and _allowed(host, ai_config.prompt_cache_breakpoint_hosts()):
        # 两段都是 best-effort：形状不认识就跳过该项，绝不抛异常——缓存优化不
        # 值得把一次正常对话变成 500。
        if body is payload:
            body = dict(payload)
        _inject_system_breakpoint(body)
        _inject_tools_breakpoint(body)

    return body, cache_key


def _allowed(host: str, hosts: frozenset[str]) -> bool:
    """host 是否命中白名单；``"*"`` 代表全部。空 host 一律不命中。"""
    if not host:
        return False
    return "*" in hosts or host in hosts


def _leading_system_messages(payload: dict) -> list:
    """``messages`` 开头连续的系统消息（遇到非系统消息立即停）。"""
    messages = payload.get("messages")
    if not isinstance(messages, list):
        return []
    index = _last_leading_index(messages)
    return messages[: index + 1] if index >= 0 else []


def _ephemeral() -> dict[str, str]:
    """断点标记，每次新建：同一个 dict 被两处引用时，下游改一处会连带另一处。"""
    return {"type": "ephemeral"}


def _last_leading_index(messages: list) -> int:
    """最后一个开头连续系统消息的下标；没有则 -1。"""
    index = -1
    for position, item in enumerate(messages):
        if not isinstance(item, dict):
            break
        role = item.get("role")
        if not isinstance(role, str) or role.lower() not in _SYSTEM_ROLES:
            break
        index = position
    return index


def _inject_system_breakpoint(body: dict) -> bool:
    """在最后一个系统消息上打断点；形状不认识返回 False 且不改 body。"""
    messages = body.get("messages")
    if not isinstance(messages, list) or not messages:
        return False
    index = _last_leading_index(messages)
    if index < 0:
        return False

    message = messages[index]
    if not isinstance(message, dict):
        return False
    content = message.get("content")

    if isinstance(content, str):
        if not content:
            # 空串不换：``[{"type":"text","text":""}]`` 会被要求「text 非空」的实现
            # 拒绝，而空系统消息本来也没有可缓存的内容。
            return False
        new_content: Any = [
            {"type": "text", "text": content, "cache_control": _ephemeral()}
        ]
    elif isinstance(content, list) and content:
        last = content[-1]
        if (
            not isinstance(last, dict)
            or last.get("type") != "text"
            or "cache_control" in last
        ):
            # 只认「最后一块是 text 且还没标记」：多模态块（image 等）不是断点的
            # 合法落点，已有 cache_control 说明调用方比我们更清楚该断在哪。
            return False
        new_content = list(content)
        patched = dict(last)
        patched["cache_control"] = _ephemeral()
        new_content[-1] = patched
    else:
        return False

    new_message = dict(message)
    new_message["content"] = new_content
    new_messages = list(messages)
    new_messages[index] = new_message
    body["messages"] = new_messages
    return True


def _inject_tools_breakpoint(body: dict) -> bool:
    """在最后一个 tool 定义上打断点；形状不认识返回 False 且不改 body。"""
    tools = body.get("tools")
    if not isinstance(tools, list) or not tools:
        return False
    last = tools[-1]
    if not isinstance(last, dict) or "cache_control" in last:
        return False
    new_tools = list(tools)
    patched = dict(last)
    patched["cache_control"] = _ephemeral()
    new_tools[-1] = patched
    body["tools"] = new_tools
    return True


__all__ = [
    "KEY_MAX_LEN",
    "MODES",
    "host_of",
    "prefix_fingerprint",
    "request_body",
]
