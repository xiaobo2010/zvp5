"""受控的 prompt 正文留存（审计层，``AI_PROMPT_AUDIT``）。

``AI_CALL_LOGS`` 一律只有元数据、正文一条不落（``services.ai.audit`` 的模块
docstring 写明了这是合规决定）。这张表是**唯一**的例外，理由只有一个：学生会
（``department``）的对话在出现安全事件、申诉或质量评估时，需要能还原「当时
问了什么」。

四条硬约束，少一条这张表就变成事故现场：

* **只留指定角色的正文**：默认 ``AI_PROMPT_CAPTURE_ROLES = ("department",)``。
  管理员的调试对话没有校园治理价值，却是风险最高的正文，所以不在默认集合里。
* **必须脱敏**：18 位身份证（含 ``X`` 校验位）与 11 位手机号在服务端掩码。
  掩码放在这里而不是调用方——少传一次 prompt 是调用方的自由，但「漏遮一个
  号码」是这张表不能承受的失误。口径见 :data:`ID_CARD_MASK_RULE` /
  :data:`MOBILE_MASK_RULE`。
* **必须有上限**：``AI_PROMPT_MAX_CHARS``（默认 8000）之后截断，CLOB 不是垃圾桶。
* **只能做旁路**：任何失败变成一条 **不含正文** 的 warning，绝不影响主请求
  （与 :mod:`services.ai.audit` 同一条原则）。

保留期由 :mod:`tasks.retention` 按 ``AI_PROMPT_RETENTION_DAYS``（默认 30 天）
清理；审阅端点是 ``GET /api/ai/prompts``（admin，访问本身写 ``APP_LOGS``）。
"""

from __future__ import annotations

import logging
import re
from typing import Any

from db import backends
from services.ai import audit
from services.ai import config as ai_config

_logger = logging.getLogger(__name__)

#: 身份证掩码口径：前 3 位 + 11 个 ``*`` + 后 4 位（即只留省级前缀与出生日期尾 +
#: 顺序码尾）。``(?<!\d)`` / ``(?!\d)`` 是边界：更长的数字串不是身份证，不猜。
#: 17 位数字 + ``X``/``x`` 校验位同样是 18 位身份证，一并覆盖。
ID_CARD_MASK_RULE = "18 位身份证：前 3 位与后 4 位明文，中间 11 位替换为 *"
#: 手机号掩码口径：前 3 位（号段）+ 6 个 ``*`` + 后 2 位。
MOBILE_MASK_RULE = "11 位手机号（1[3-9] 开头）：前 3 位与后 2 位明文，中间 6 位替换为 *"

_ID_CARD_RE = re.compile(r"(?<!\d)(\d{17}[0-9Xx])(?!\d)")
_MOBILE_RE = re.compile(r"(?<!\d)(1[3-9]\d{9})(?!\d)")

#: 正文读取的角色集合（``per`` 口径）从配置来，这里只是转发，方便测试替换。
__all__ = [
    "ID_CARD_MASK_RULE",
    "MOBILE_MASK_RULE",
    "capture",
    "last_user_text",
    "mask_pii",
    "prepare_prompt",
    "should_capture",
]


def mask_pii(text: str) -> str:
    """把身份证与手机号替换成掩码形态（其余内容原样保留）。

    先遮身份证再遮手机号：18 位数字串里不会同时命中手机号正则（数字边界会挡），
    反过来先遮手机号则可能切走身份证中的一段。
    """
    if not isinstance(text, str) or not text:
        return text
    text = _ID_CARD_RE.sub(
        lambda match: match.group(1)[:3] + "*" * 11 + match.group(1)[-4:], text
    )
    return _MOBILE_RE.sub(
        lambda match: match.group(1)[:3] + "*" * 6 + match.group(1)[-2:], text
    )


def prepare_prompt(text: str) -> str:
    """脱敏 → 截断。顺序不能反：先截断会切出半个身份证号，正则就再也遮不住。"""
    return mask_pii(text)[: ai_config.prompt_max_chars()]


def _content_text(content: Any) -> str | None:
    """``content`` 可以是字符串，也可以是 OpenAI 的 content 数组。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                # 只取文本段：图片是 base64 / URL，不属于「用户说了什么」。
                text = item.get("text")
                if isinstance(text, str) and text:
                    parts.append(text)
            elif isinstance(item, str) and item:
                parts.append(item)
        return "\n".join(parts) if parts else None
    return None


def last_user_text(messages: Any) -> str | None:
    """取**最后一条** ``role=user`` 的正文。

    只留最后一条是有意的：多轮对话里前面的轮次已经在这张表里各留过一份
    （每一轮请求都是一次捕获），再存一遍只是把同一段话重复 N 次。
    """
    if not isinstance(messages, list):
        return None
    for item in reversed(messages):
        if not isinstance(item, dict):
            continue
        if item.get("role") != "user":
            continue
        return _content_text(item.get("content"))
    return None


def should_capture(user: dict) -> bool:
    """调用者的 ``per`` 是否与捕获角色集相交。"""
    roles = user.get("per")
    if not isinstance(roles, (list, tuple, set, frozenset)):
        return False
    caller = {role for role in roles if isinstance(role, str) and role}
    return bool(caller & ai_config.prompt_capture_roles())


async def capture(
    *,
    user: dict,
    purpose: str | None,
    messages: Any,
    request_id: str | None = None,
) -> str | None:
    """按捕获规则留一份正文，返回新 oid；不需要留 / 留失败回 ``None``。

    ``purpose`` 由调用方给（``chat`` / ``markdown-assistant`` / ``agent``）：
    这张表按场景查询才有意义，而场景在表里只是一个字符串（开放集合）。

    挂点只有两处：``routers.ai_router.chat_completions``（覆盖 chat 与
    markdown-assistant）与 ``services.agent.executor.run``（一次 run 一次）。
    每轮重复捕获 agent 的 prompt 会把同一段话存十遍，也把配额成本变成存储成本。
    """
    try:
        if not should_capture(user):
            return None
        user_oid = str(user.get("id") or "")
        if not user_oid:
            return None
        text = last_user_text(messages)
        if not text or not text.strip():
            return None
        return await backends.get_ai_prompt_audit_repo().insert(
            request_id=request_id,
            user_oid=user_oid,
            roles_snapshot=audit.roles_snapshot(user.get("per")),
            purpose=purpose,
            prompt_text=prepare_prompt(text),
        )
    except Exception as exc:  # noqa: BLE001 - 留存失败不影响主请求
        # 只留异常类名：异常 message 可能带上数据库连接串或 SQL 片段。
        _logger.warning("ai prompt audit capture failed: %s", type(exc).__name__)
        return None
