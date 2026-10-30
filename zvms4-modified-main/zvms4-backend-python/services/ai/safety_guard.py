"""LLM 安全认证：用一次严格判定的模型调用识别「与平台职能无关」的请求。

**判定口径是白名单**：zvms4 是学校的志愿服务（义工）管理平台，ZVAgent 只是平台
内部的运维助手，仅服务义工管理、活动组织、班级学业事务、设备与系统运维、账号
权限。与这些功能无关的请求一律拒绝——用户拍板「拦截所有和平台运维和义工管理
无关的内容」，不相关即拒绝并按首档封号处理。``related`` 因此是唯一的放行条件，
``destructive`` 只进证据，不参与判定。

**为什么这一层只对 department 生效**：学生会的日常就是平台管理与义工管理，
关键词规则挡不住「换个说法」的越界请求（删库、批量封号、导全站数据、跑私活）。
admin 天然旁路（``is_admin`` 直接跳过审核），学生没有；给全体学生加一次上游
调用既贵又慢，收益还不明确。所以这是一道**按角色开启**的判定闸门，不是全站
门禁。

**为什么 fail-closed**：安全闸自己跑不起来（无凭据 / 超时 / 上游 5xx / 坏
JSON）时**拒绝本次回答**，但**不记违规、不封号**——没有判定结论就没有证据。
这类结论用 ``ScreenResult.unavailable`` 与「真的判定为无关请求」区分开：
对外同一个错误码，对内只有后者能进违规链路。空文本例外（无内容可审，放行）。

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
from services.ai.moderation import REJECTED_MESSAGE
from services.ai.routing import RouteError
from typings.log import ZVMSLog

_logger = logging.getLogger(__name__)

# 判定跑不起来时的内部分类：调用方据此**不写违规证据**（对外仍是同一个错误码）。
SAFETY_UNAVAILABLE = "safety_unavailable"

# 对外唯一错误码：不再区分「已警告」与「已封禁」。
REJECTED_CODE = "AI_SAFETY_REJECTED"

# 正文送给判定模型的长度上限：判定只需要意图，不需要全文（长上下文既贵又会
# 把真正的意图埋掉）。2000 字符足够覆盖一次完整提问。
MAX_SCREEN_CHARS = 2000

SYSTEM_PROMPT = (
    "zvms4 是学校的志愿服务（义工）管理平台。平台内的 ZVAgent 是平台自身的运维"
    "助手，只有一种用途：处理与平台功能相关的请求。\n"
    "合法范围仅限：\n"
    "1) 义工管理：活动、报名、签到签退、义工时长/工时、登记、附件凭据；\n"
    "2) 平台管理：用户/权限/封禁/公告/AI 网关/数据库/服务器运维/日志审计；\n"
    "3) 班级与学业事务；\n"
    "4) 账号与权限问题。\n"
    "related 表示请求内容是否与上述平台功能相关。与平台功能无关的一律"
    " related=false，例如闲聊、常识问答、写作、翻译、编程求助、角色扮演/cosplay、"
    "情感陪伴、娱乐内容——即使这些请求本身无害。\n"
    "destructive 只作证据记录：请求是否在要求破坏性、不可逆或越权操作（删库、"
    "清空数据、批量封号、提权、窃取凭据、绕过审核等），它不决定放行与否。\n"
    "只输出一行 JSON，不要解释、不要代码块：\n"
    '{"related": true|false, "destructive": true|false, '
    '"category": "短标签", "reason": "一句话"}'
)

_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)


@dataclass(frozen=True)
class ScreenResult:
    """认证结论；``allowed`` 为 False 时 ``category`` 非空。

    ``unavailable`` 区分两种拒绝：``True`` = 判定没跑起来（无凭据/超时/坏
    JSON），只拒绝本次回答；``False`` = 判定确认请求与平台职能无关，属于违规
    证据。
    """

    allowed: bool
    reason: str = ""
    category: str = ""
    unavailable: bool = False


ALLOWED = ScreenResult(allowed=True)


def unavailable() -> ScreenResult:
    """判定跑不起来：拒绝本次回答，但不构成任何违规证据。"""
    return ScreenResult(
        allowed=False, category=SAFETY_UNAVAILABLE, unavailable=True
    )


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
    """安全闸自身的故障留痕；写日志失败不影响拒绝结论。"""
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

    优先级：显式 ``model_hint``（合格 ID 或裸名，且目录里确实有 provider 提供它）
    → 目录里第一个可用 provider 的第一个模型。与 ``services.agent.executor`` 的
    选择口径一致，避免「判定用的模型」与「真正回答的模型」跨到另一家上游。

    这里**不**把歧义当错误往外抛：裸名命中多家时退回「目录里第一个可用」并照常
    判定，比让用户的请求因为分类器选路问题被拒好。
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
        try:
            resolved = credentials.resolve_model(candidates, hint)
        except credentials.ModelAmbiguous:
            resolved = None
        if resolved is not None:
            return resolved.entry, resolved.model

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
    其余任何「判定没跑起来」的情形都返回 :func:`unavailable`（拒绝回答、不记
    违规），并写 ``ai_safety_guard_error`` 日志；空文本无内容可审，放行。
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
        _logger.exception("读取 AI 凭据目录失败，安全认证无法判定")
    if route is None:
        await _log_warning(user_oid, "AI 安全认证无可用凭据/模型，本次拒绝回答")
        return unavailable()

    entry, model = route
    try:
        api_key = await credentials.resolve_api_key(entry)
    except CredentialError:
        await _log_warning(user_oid, "AI 安全认证凭据不可用，本次拒绝回答")
        return unavailable()

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
        await _log_warning(user_oid, f"AI 安全认证超时（{timeout:.0f}s），本次拒绝回答")
        return unavailable()
    except RouteError as exc:
        await _log_warning(
            user_oid, f"AI 安全认证上游失败（{exc.code}），本次拒绝回答"
        )
        return unavailable()
    except Exception:  # noqa: BLE001 - 判定跑不起来就拒绝，绝不冒充「通过」
        _logger.exception("AI 安全认证调用异常，本次拒绝回答")
        await _log_warning(user_oid, "AI 安全认证调用异常，本次拒绝回答")
        return unavailable()

    try:
        message = (response.get("choices") or [{}])[0].get("message") or {}
        body = message.get("content") or ""
    except (AttributeError, IndexError, TypeError):
        body = ""

    verdict = _extract_json(str(body))
    if verdict is None:
        await _log_warning(user_oid, "AI 安全认证返回无法解析的结论，本次拒绝回答")
        return unavailable()

    related = _as_bool(verdict.get("related"))
    if not related:
        # 白名单口径（用户拍板）：与平台职能无关即拒绝，不再要求 destructive。
        category = str(verdict.get("category") or "unrelated_request")[:64]
        reason = str(verdict.get("reason") or "")[:200]
        return ScreenResult(allowed=False, reason=reason, category=category)

    return ALLOWED


__all__ = [
    "ALLOWED",
    "MAX_SCREEN_CHARS",
    "REJECTED_CODE",
    "REJECTED_MESSAGE",
    "SAFETY_UNAVAILABLE",
    "SYSTEM_PROMPT",
    "ScreenResult",
    "is_department",
    "screen",
    "unavailable",
]
