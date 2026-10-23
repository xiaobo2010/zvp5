"""AI 调用审计落 ``AI_CALL_LOGS``。

审计是旁路：任何失败都只能变成一条 warning，绝不能改变调用方的返回。写入的字段
只有结构化元数据（谁、哪个能力、哪个供应商、状态、耗时、单位数），密钥、上游原始
报文、OCR 文本一律不进来——审计表是给运营看的，不是抓包。
"""

from __future__ import annotations

import logging

from db import backends

_logger = logging.getLogger(__name__)

ENDPOINT_OCR = "/api/ai/ocr"
ENDPOINT_CHAT = "/api/ai/v1/chat/completions"


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
) -> None:
    """``key_scope`` 记的是这次调用花的谁的钱（personal/shared/server）。

    它不参与鉴权，只用于事后对账：同一份额度报表里，「平台出资」与「管理员自付」
    必须能分开统计，否则个人 key 的调用会被误读成平台成本。
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
        )
    except Exception as exc:  # noqa: BLE001 - 审计失败不影响业务返回
        _logger.warning("ai call audit failed: %s", type(exc).__name__)
