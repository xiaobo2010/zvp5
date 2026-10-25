"""AI 调用审计落 ``AI_CALL_LOGS``。

审计是旁路：任何失败都只能变成一条 warning，绝不能改变调用方的返回。写入的字段
只有结构化元数据（谁、哪个能力、哪个供应商、状态、耗时、单位数、token 计数），
密钥、上游原始报文、OCR 文本一律不进来——审计表是给运营看的，不是抓包。

``prompt_tokens`` / ``completion_tokens`` / ``total_tokens`` 的 NULL 语义是「上
游没报 usage」，此时该行的 ``units`` 是估算值；不为 NULL 时两者同源。这条不变式
由调用方负责维持（``routers/ai_router`` / ``util/attachment_ocr``），本模块只
如实落库。``cache_read_tokens`` / ``cache_write_tokens`` 沿用同一条 NULL 语义。
"""

from __future__ import annotations

import logging

from db import backends

_logger = logging.getLogger(__name__)

ENDPOINT_OCR = "/api/ai/ocr"
ENDPOINT_CHAT = "/api/ai/v1/chat/completions"
ENDPOINT_AGENT = "/api/ai/agent/run"

# ``ROLES_SNAPSHOT`` 的列宽（V033）。角色快照是**事实**：事后改角色不该改变
# 「当时凭什么放行」，所以每次调用都冻结一份，而不是事后回查用户表。
ROLES_SNAPSHOT_MAX = 64


def roles_snapshot(roles: object) -> str | None:
    """角色列表 → ``ROLES_SNAPSHOT`` 的字符串形态（排序去重，逗号连接）。

    ``None`` / 空集合回 ``None``：NULL 的语义是「没采集」，与空串（Oracle 里也
    是 NULL）和 ``''`` 都要能区分开——审计行宁可留空，也不要一个看起来像
    「这个人没有任何角色」的假值。
    """
    if not roles:
        return None
    if isinstance(roles, str):
        items = [roles]
    elif isinstance(roles, (list, tuple, set, frozenset)):
        items = [item for item in roles if isinstance(item, str) and item]
    else:
        return None
    cleaned = sorted({item.strip() for item in items if item.strip()})
    if not cleaned:
        return None
    text = ",".join(cleaned)
    if len(text) <= ROLES_SNAPSHOT_MAX:
        return text
    # 超长只可能是有人塞了奇怪的 per：截断比让 INSERT 以 ORA-12899 失败好，
    # 而审计失败会被吞掉 —— 那样这次调用就完全没有流水了。
    return text[:ROLES_SNAPSHOT_MAX]


async def record(
    *,
    user_oid: str,
    capability: str,
    endpoint: str,
    provider: str | None = None,
    model: str | None = None,
    status: str = "success",
    http_status: int | None = None,
    error_code: str | None = None,
    latency_ms: int | None = None,
    units: int = 0,
    input_bytes: int | None = None,
    request_id: str | None = None,
    key_scope: str | None = None,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    total_tokens: int | None = None,
    cache_read_tokens: int | None = None,
    cache_write_tokens: int | None = None,
    cache_key: str | None = None,
    attempt_no: int | None = None,
    purpose: str | None = None,
    first_token_ms: int | None = None,
    engine_kind: str | None = None,
    attachment_oid: str | None = None,
    round_no: int | None = None,
    roles_snapshot: str | None = None,
) -> None:
    """``key_scope`` 记的是这次调用花的谁的钱（personal/shared/server）。

    它不参与鉴权，只用于事后对账：同一份额度报表里，「平台出资」与「管理员自付」
    必须能分开统计，否则个人 key 的调用会被误读成平台成本。

    ``attempt_no`` 从 1 起：一次请求的第几次上游尝试。``request_id`` 把同一次
    请求的所有尝试串成一条链（chat 的多 provider 重试、OCR 的多引擎回退）。
    ``purpose`` 是调用场景（前端 ``X-AI-Scene`` / OCR 固定 ``'ocr'``），开放集合。
    ``round_no`` 是 agent run 的第几轮（1 起，NULL = 不是 agent 轮次），
    ``roles_snapshot`` 是调用时的角色快照（见 :func:`roles_snapshot`）。

    ``cache_read_tokens`` / ``cache_write_tokens``：NULL = 上游没报缓存用量（或
    这个上游没有缓存计费），**不是** 0（0 是一个真实的「完全没命中」）。``cache_key``
    是我们自己算的前缀指纹（model + tools + 开头 system 段的哈希，见
    ``services.ai.prompt_cache``），**不含**任何用户正文；NULL = 这次没参与缓存路由。
    """
    try:
        await backends.get_ai_call_logs_repo().insert(
            user_oid=user_oid,
            capability=capability,
            endpoint=endpoint,
            provider=provider,
            model=model,
            status=status,
            http_status=http_status,
            error_code=error_code,
            latency_ms=latency_ms,
            units=int(units or 0),
            input_bytes=input_bytes,
            request_id=request_id,
            key_scope=key_scope,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_write_tokens=cache_write_tokens,
            cache_key=cache_key,
            attempt_no=attempt_no,
            purpose=purpose,
            first_token_ms=first_token_ms,
            engine_kind=engine_kind,
            attachment_oid=attachment_oid,
            round_no=round_no,
            roles_snapshot=roles_snapshot,
        )
    except Exception as exc:  # noqa: BLE001 - 审计失败不影响业务返回
        _logger.warning("ai call audit failed: %s", type(exc).__name__)
