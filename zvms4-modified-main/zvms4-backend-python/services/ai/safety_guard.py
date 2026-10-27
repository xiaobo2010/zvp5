"""LLM 安全认证：用一次严格判定的模型调用识别「无关且破坏性」的请求。

**为什么这一层只对 department 生效**：学生会的日常就是平台管理与义工管理，
关键词规则挡不住「换个说法」的破坏性请求（删库、批量封号、导全站数据）。
admin 有无限制模式与人工复核兜底，学生没有；给全体学生加一次上游调用既贵
又慢，收益还不明确。所以这是一道**按角色开启**的灰区闸门，不是全站门禁。

**为什么 fail-open**：安全闸自己挂掉（无凭据 / 超时 / 上游 5xx / 坏 JSON）时
必须放行并留一条 warning 日志。把正常用户的请求挡在一个「可能坏了」的分类器
后面，等于让安全功能成为可用性的单点故障；而被漏掉的灰区请求仍然受关键词
规则与月度警告/封禁梯度约束。

**为什么只判一次、温度 0、限 200 token**：这一步在用户等待的同步路径上
（提交 run / 发一条对话），延迟预算只有几秒。判定要可复现，就不要采样；
输出只需一个 4 字段 JSON，给多了是浪费。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Mapping

from services.ai import config as ai_config
from services.ai import credentials, providers, routing
from services.ai.credentials import CredentialError
from services.ai.routing import RouteError
from typings.log import ZVMSLog

_logger = logging.getLogger(__name__)

# 对外固定文案：不回传内部判定细节，避免攻击者按差异调参。
REJECTED_MESSAGE = "该请求未通过内容安全认证。"

# 正文送给判定模型的长度上限：判定只需要意图，不需要全文（长上下文既贵又会
# 把真正的意图埋掉）。2000 字符足够覆盖一次完整提问。
MAX_SCREEN_CHARS = 2000

SYSTEM_PROMPT = (
    "你是平台的内容安全判定器。平台的两类合法业务是：\n"
    "1) 平台管理：用户/权限/封禁/公告/AI 网关/数据库/服务器运维/日志审计；\n"
    "2) 义工管理：活动、报名、义工时长、登记、附件凭据。\n"
    "判断用户请求：related 表示是否属于上述业务范畴，destructive 表示是否在"
    "要求破坏性、不可逆或越权操作（删库、清空数据、批量封号、提权、窃取凭据、"
    "绕过审核等）。\n"
    "只输出一行 JSON，不要解释、不要代码块：\n"
    '{"related": true|false, "destructive": true|false, '
    '"category": "短标签", "reason": "一句话"}'
)

_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)


@dataclass(frozen=True)
class ScreenResult:
    """认证结论；``allowed`` 为 False 时 ``category`` 非空。"""

    allowed: bool
    reason: str = ""
    category: str = ""


ALLOWED = ScreenResult(allowed=True)


def is_department(user: Mapping[str, Any] | dict) -> bool:
    """是否学生会成员（department 角色）。

    admin 优先豁免：同一账号同时有 admin 与 department 时按管理员处理——
    自动限制管理员会让平台在最需要运维的时候锁死自己。
    """
    per = (user or {}).get("per")
    if not isinstance(per, (list, tuple, set, frozenset)):
        return False
    roles = {role for role in per if isinstance(role, str)}
    return "department" in roles and "admin" not in roles


def _extract_json(text: str) -> dict | None:
    """从模型输出里取第一个 JSON 对象；容忍 ```json 包裹与前后废话。"""
    raw = (text or "").strip()
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except ValueError:
        match = _JSON_BLOCK.search(raw)
        if match is None:
            return None
        try:
            parsed = json.loads(match.group(0))
        except ValueError:
            return None
    return parsed if isinstance(parsed, dict) else None


def _as_bool(value: Any) -> bool:
    """只认真正的布尔；字符串 "true" 也接受（部分模型会写字符串）。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() == "true"
    return False


async def _log_warning(user_oid: str, message: str) -> None:
    """安全闸自身的故障留痕；写日志失败不影响放行结论。"""
    try:
        await ZVMSLog(
            "",
            user_oid,
            message,
            type="ai_safety_guard_error",
            level="warning",
        ).insert_log()
    except Exception:  # noqa: BLE001 - 旁路日志
        _logger.exception("写入 ai_safety_guard_error 日志失败")


async def _pick_route(
    user_oid: str, model_hint: str | None
) -> tuple[dict, str] | None:
    """选一条可用凭据与模型；没有可用项返回 ``None``。

    优先级：显式 ``model_hint``（且目录里确实有 provider 提供它）→ 目录里第一个
    可用 provider 的第一个模型。与 ``routers.ai_chat_conversations_router`` 的
    选择口径一致，避免「判定用的模型」与「真正回答的模型」跨到另一家上游。
    """
    entries = await credentials.catalog(user_oid)
    if not entries:
        return None
    try:
        candidates = routing.candidates(entries)
    except RouteError:
        return None
    if not candidates:
        return None

    hint = str(model_hint).strip() if isinstance(model_hint, str) else ""
    if hint:
        for entry in candidates:
            if hint in (entry.get("models") or []):
                return entry, hint

    for entry in candidates:
        models = entry.get("models") or []
        if models:
            return entry, str(models[0])
    if hint:
        return candidates[0], hint
    return None


async def screen(
    user: Mapping[str, Any] | dict,
    text: object,
    *,
    model_hint: str | None = None,
) -> ScreenResult:
    """对 department 用户做一次 LLM 安全认证。

    非 department 或开关关闭时**直接放行且不触达网络**（零额外开销）。
    其余任何异常都按放行处理，并写 ``ai_safety_guard_error`` 日志。
    """
    if not is_department(user):
        return ALLOWED
    if not ai_config.safety_guard_enabled():
        return ALLOWED

    user_oid = str((user or {}).get("id") or "")
    content = str(text or "").strip()
    if not content:
        return ALLOWED

    route = None
    try:
        route = await _pick_route(user_oid, model_hint)
    except Exception:  # noqa: BLE001 - 目录读取失败按无凭据处理
        _logger.exception("读取 AI 凭据目录失败，安全认证放行")
    if route is None:
        await _log_warning(user_oid, "AI 安全认证无可用凭据/模型，本次放行")
        return ALLOWED

    entry, model = route
    try:
        api_key = await credentials.resolve_api_key(entry)
    except CredentialError:
        await _log_warning(user_oid, "AI 安全认证凭据不可用，本次放行")
        return ALLOWED

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": content[:MAX_SCREEN_CHARS]},
        ],
        "temperature": 0,
        "max_tokens": 200,
        "stream": False,
    }

    timeout = ai_config.safety_guard_timeout_seconds()
    try:
        response = await asyncio.wait_for(
            providers.chat_completion(payload, entry, api_key), timeout=timeout
        )
    except asyncio.TimeoutError:
        await _log_warning(user_oid, f"AI 安全认证超时（{timeout:.0f}s），本次放行")
        return ALLOWED
    except RouteError as exc:
        await _log_warning(user_oid, f"AI 安全认证上游失败（{exc.code}），本次放行")
        return ALLOWED
    except Exception:  # noqa: BLE001 - 任何异常都不该挡住正常用户
        _logger.exception("AI 安全认证调用异常，本次放行")
        await _log_warning(user_oid, "AI 安全认证调用异常，本次放行")
        return ALLOWED

    try:
        message = (response.get("choices") or [{}])[0].get("message") or {}
        body = message.get("content") or ""
    except (AttributeError, IndexError, TypeError):
        body = ""

    verdict = _extract_json(str(body))
    if verdict is None:
        await _log_warning(user_oid, "AI 安全认证返回无法解析的结论，本次放行")
        return ALLOWED

    related = _as_bool(verdict.get("related"))
    destructive = _as_bool(verdict.get("destructive"))
    if destructive and not related:
        category = str(verdict.get("category") or "destructive_request")[:64]
        reason = str(verdict.get("reason") or "")[:200]
        return ScreenResult(allowed=False, reason=reason, category=category)

    return ALLOWED


__all__ = [
    "ALLOWED",
    "MAX_SCREEN_CHARS",
    "REJECTED_MESSAGE",
    "SYSTEM_PROMPT",
    "ScreenResult",
    "is_department",
    "screen",
]
