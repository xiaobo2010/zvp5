"""Pluggable OCR interface + state machine helpers (design §7).

引擎的**实现**不在这里：真实适配器都在 :mod:`services.ai.ocr_engines`（腾讯 /
阿里 / RapidOCR），本模块只负责「按注册表挑一个引擎 → 跑 → 落库 → 推进状态机」。

引擎由入队方选定，本模块只**跟随**，绝不二次扣费、也不自选：

``services.ai.ocr.enqueue`` 已经扣过用户额度、按 ``priority`` 选好引擎并扣过站点
额度（``/api/ai/ocr`` 与 ``POST /api/attachments/{oid}/ocr`` 两条入口共用同一
套计价）。入队时选的是哪一行，这里就必须跑哪一行——即使它在入队后被改名或禁用，
也只能如实失败交给状态机重试，绝不换一个引擎去花另一份额度。

State machine: ``stored -> (ocr pending -> processing -> done|failed) ->
verified|rejected``. Every transition is driven through
``FilesRepo.set_ocr`` with an audit row in ``file_events``.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Protocol

from db import backends
from services.ai import crypto, ocr_engines

_logger = logging.getLogger(__name__)

OCR_PENDING = "pending"
OCR_PROCESSING = "processing"
OCR_DONE = "done"
OCR_FAILED = "failed"


class OcrEngine(Protocol):
    """A synchronous OCR engine (legacy in-process shape)."""

    name: str
    version: str

    def recognize(self, image: bytes) -> dict[str, Any]:
        """Return ``{"text": str, "confidence": float, "raw": Any}``."""
        ...


class NoopEngine:
    """Placeholder engine: recognizes nothing, never fails.

    保留是为了向后兼容（旧代码/第三方引用按类名导入）；``process_ocr`` 不再用它
    ——现在识别走注册表里的真实引擎。
    """

    name = "noop"
    version = "0"

    def recognize(self, image: bytes) -> dict[str, Any]:
        return {"text": "", "confidence": 0.0, "raw": {}}


DEFAULT_ENGINE: OcrEngine = NoopEngine()


async def _set_ocr(file_oid: str, status: str, *, bump_retry: bool = False) -> None:
    await backends.get_files_repo().set_ocr(
        file_oid, status, bump_retry=bump_retry
    )


async def _add_event(
    file_oid: str, action: str, from_status: str | None, to_status: str | None
) -> None:
    # ``None`` actor = system-driven transition.
    await backends.get_files_repo().add_event(
        file_oid, None, action, from_status, to_status, ""
    )


async def mark_ocr_processing(file_oid: str) -> None:
    await _set_ocr(file_oid, OCR_PROCESSING)
    await _add_event(file_oid, "ocr_start", OCR_PENDING, OCR_PROCESSING)


async def mark_ocr_done(file_oid: str) -> None:
    await _set_ocr(file_oid, OCR_DONE)
    await _add_event(file_oid, "ocr_done", OCR_PROCESSING, OCR_DONE)


async def mark_ocr_failed(file_oid: str) -> None:
    await _set_ocr(file_oid, OCR_FAILED, bump_retry=True)
    await _add_event(file_oid, "ocr_failed", OCR_PROCESSING, OCR_FAILED)


async def process_ocr(
    file_oid: str,
    engine_kind: str,
    engine_name: str | None = None,
) -> None:
    """Run the selected engine against the file's stored bytes and record it.

    ``engine_kind`` / ``engine_name`` 由入队方透传（见模块 docstring），必填：
    引擎与额度都在入队时定好，这里不做任何选择或扣费。

    Drives ``set_ocr`` transitions and writes ``file_ocr`` + ``file_events``.
    Any failure lands the file in ``failed`` (retry bumped) and is swallowed so a
    background task can never crash the caller. 异常消息**不含凭证**：适配器的
    ``OcrEngineError`` 只拼引擎名与字段名，解密失败抛的是通用 ``ValueError``。
    """
    repo = backends.get_files_repo()
    try:
        await mark_ocr_processing(file_oid)
        content = await repo.get_content(file_oid)
        if content is None:
            raise ValueError("Attachment content is unavailable")

        meta = await repo.get_meta(file_oid) or {}
        mime = str(meta.get("mime") or "").lower()

        row = await _select_row(engine_kind, engine_name)
        kind = str(row["kind"])
        adapter = ocr_engines.ENGINES[kind]()
        result = await adapter.recognize(
            bytes(content), mime, _secret(row), row.get("config") or {}
        )

        await repo.add_ocr(
            file_oid,
            _engine_label(row, kind),
            type(adapter).VERSION,
            str(result.get("text", "")),
            _confidence(result),
            result.get("raw", {}),
        )
        await mark_ocr_done(file_oid)
    except Exception as exc:  # noqa: BLE001 - 后台任务不能把异常抛回调用方
        # 只记类型与消息：适配器的消息里只有引擎名/字段名，不含凭证值。
        _logger.warning(
            "ocr failed for %s: %s: %s", file_oid, type(exc).__name__, exc
        )
        try:
            await mark_ocr_failed(file_oid)
        except Exception:  # noqa: BLE001 - 状态机本身失败只能放弃
            pass


async def _select_row(engine_kind: str, engine_name: str | None) -> dict:
    """按 kind/name 取引擎行。

    ``name`` 只是同一 ``kind`` 内的消歧（同类型可以有多行：不同 region / 不同
    配额来源）；名字对不上就回落到该 kind 的第一行，而不是直接失败——引擎被改名
    后旧的入队记录仍然应该能跑完。
    """
    rows = await backends.get_ai_ocr_engines_repo().list_all(enabled_only=True)
    if not rows:
        raise ocr_engines.OcrEngineError("no OCR engine is enabled")

    candidates = [row for row in rows if str(row.get("kind") or "") == engine_kind]
    if not candidates:
        # 引擎在入队之后被禁用/删除了：如实失败（状态机可重试），不悄悄换一个
        # 引擎去花另一份站点额度。
        raise ocr_engines.OcrEngineError(
            f"OCR engine {engine_kind!r} is no longer enabled"
        )
    if engine_name:
        for row in candidates:
            if str(row.get("name") or "") == engine_name:
                return row
    return candidates[0]


def _secret(row: dict) -> dict:
    """``secret_cipher`` → 凭证 dict；``None`` = 本地引擎，不需要凭证。"""
    cipher = row.get("secret_cipher")
    if not cipher:
        return {}
    parsed = json.loads(crypto.decrypt_secret(str(cipher)))
    if not isinstance(parsed, dict):
        raise ocr_engines.OcrEngineError("stored OCR credential is not an object")
    return parsed


def _engine_label(row: dict, kind: str) -> str:
    """落 ``file_ocr.engine``：用注册表里的名字（运维按名字认引擎）。"""
    return str(row.get("name") or kind)


def _confidence(result: dict) -> float:
    """统一结果没有顶层 confidence：按行取平均（全空则 0.0）。

    单行结果下平均值就是那一行的分数，多行时它代表整张图的识别质量——比取
    第一行稳定（第一行往往是标题，分数分布和正文不同）。
    """
    scores = [
        float(line["confidence"])
        for line in result.get("lines") or []
        if isinstance(line, dict) and line.get("confidence") is not None
    ]
    if not scores:
        return 0.0
    return max(0.0, min(1.0, sum(scores) / len(scores)))


__all__ = [
    "DEFAULT_ENGINE",
    "NoopEngine",
    "OCR_DONE",
    "OCR_FAILED",
    "OCR_PENDING",
    "OCR_PROCESSING",
    "OcrEngine",
    "mark_ocr_done",
    "mark_ocr_failed",
    "mark_ocr_processing",
    "process_ocr",
]
