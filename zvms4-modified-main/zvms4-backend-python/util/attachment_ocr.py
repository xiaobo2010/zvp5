"""Pluggable OCR interface + state machine helpers (design §7).

Only the contract and the no-op placeholder land here; no concrete engine and
no new dependency (no tesseract / paddleocr / opencv). Attaching a real engine
later keeps the HTTP API unchanged.

State machine: ``stored -> (ocr pending -> processing -> done|failed) ->
verified|rejected``. Every transition is driven through
``FilesRepo.set_ocr`` with an audit row in ``file_events``.
"""

from __future__ import annotations

from typing import Any, Protocol

import anyio

from db import backends

OCR_PENDING = "pending"
OCR_PROCESSING = "processing"
OCR_DONE = "done"
OCR_FAILED = "failed"


class OcrEngine(Protocol):
    """A synchronous OCR engine. ``recognize`` runs inside a worker thread."""

    name: str
    version: str

    def recognize(self, image: bytes) -> dict[str, Any]:
        """Return ``{"text": str, "confidence": float, "raw": Any}``."""
        ...


class NoopEngine:
    """Placeholder engine: recognizes nothing, never fails."""

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


async def process_ocr(file_oid: str, engine: OcrEngine | None = None) -> None:
    """Run ``engine`` against the file's normalized bytes and record the result.

    Drives ``set_ocr`` transitions and writes ``file_ocr`` + ``file_events``.
    The engine is CPU-bound, so it executes via ``to_thread`` (design §7 /
    P1-1). Any failure lands the file in ``failed`` (retry bumped) and is
    swallowed so a background task can never crash the caller.
    """
    engine = engine or DEFAULT_ENGINE
    repo = backends.get_files_repo()
    try:
        await mark_ocr_processing(file_oid)
        content = await repo.get_content(file_oid)
        if content is None:
            raise ValueError("Attachment content is unavailable")
        result = await anyio.to_thread.run_sync(engine.recognize, content)
        await repo.add_ocr(
            file_oid,
            engine.name,
            engine.version,
            str(result.get("text", "")),
            float(result.get("confidence", 0.0)),
            result.get("raw", {}),
        )
        await mark_ocr_done(file_oid)
    except Exception:
        try:
            await mark_ocr_failed(file_oid)
        except Exception:
            pass
