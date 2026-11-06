"""Files and attachments (``FILES`` / ``FILE_OCR`` / ``FILE_EVENTS``)."""

from __future__ import annotations

from typing import Any, Protocol

from .common import Row


class FilesRepo(Protocol):
    """Data access for the Files/attachments tables (``FILES`` / ``FILE_OCR`` /
    ``FILE_EVENTS``; ``docs/FILES_API_DESIGN.md`` §3).

    Satisfied by ``db.repos.oracle.files.OracleFilesRepo``. Row shapes are the
    Mongo document dicts keyed by the physical columns:

    * a file row is ``{"_id", "owner_oid", "kind", "parent_oid", "mime",
      "size_bytes", "width", "height", "sha256", "original_name", "purpose",
      "entity_type", "entity_oid", "status", "ocr_status", "ocr_retry",
      "created_at", "updated_at", "deleted_at"}`` — **``content`` is never
      projected**, so metadata reads never materialise the BLOB;
    * an OCR row is ``{"_id", "file_oid", "engine", "engine_version", "text",
      "confidence", "raw", "created_at"}``;
    * an event row is ``{"_id", "file_oid", "actor_oid", "action",
      "from_status", "to_status", "detail", "created_at"}``.

    ``FILES.STATUS`` is the single source of truth for the serve gate
    (``stored``/``processing``/``ready``/``verified``/``rejected``/
    ``quarantined``/``deleted``); ``FILE_EVENTS`` is an append-only audit copy.
    Only bytes are read through :meth:`get_content`; every other read projects
    the metadata columns.
    """

    async def create(self, owner_oid, kind, parent_oid, mime, size_bytes, width,
                     height, sha256, content: bytes, original_name, purpose) -> str:
        """Insert a file row and return its new ``_id`` as a hex string.

        ``content`` is the normalised bytes (bound to the ``CONTENT`` BLOB);
        ``status`` starts ``stored`` and ``ocr_status`` ``pending``.
        """
        ...

    async def get_meta(self, oid: str) -> Row | None:
        """Return the file's metadata row (never ``content``), or ``None``."""
        ...

    async def get_content(self, oid: str) -> bytes | None:
        """Return the file's stored bytes, or ``None`` when absent/NULL."""
        ...

    async def find_by_sha256(self, owner_oid: str, sha256: str, kind: str) -> Row | None:
        """Return the newest metadata row for ``(owner, sha256, kind)``, or ``None``.

        Same-owner dedup lookup only — cross-owner hashes are never shared.
        """
        ...

    async def list_page(self, owner_oid: str | None, purpose: str | None, status: str | None,
                        entity_type: str | None, entity_oid: str | None,
                        page: int, size: int, *,
                        created_from: Any = None,
                        created_to: Any = None,
                        ocr_status: str | None = None,
                        kind: str | None = None) -> tuple[list[Row], int]:
        """Return ``(rows, total)`` for one page, filtered by the given columns.

        ``None`` filters are "no filter"; ``page == -1`` means "no pagination".
        Rows are metadata only (never ``content``). ``created_to`` is an open
        upper bound (``created_at < created_to``).
        """
        ...

    async def file_oids_with_ocr(self, oids: list[str]) -> set[str]:
        """Return the subset of ``oids`` that has a ``FILE_OCR`` row.

        One query for a whole page — never projects the OCR body.
        """
        ...

    async def purge_ocr_uploads(self, cutoff: Any, batch_limit: int = 200) -> dict[str, int]:
        """Hard-delete up to ``batch_limit`` expired OCR upload originals plus
        their thumb / ``FILE_OCR`` / ``FILE_EVENTS`` rows.

        Scope: ``purpose='volunteer_order' AND entity_type IS NULL AND
        kind='original'`` with ``status NOT IN ('verified','rejected')``.
        Returns ``{"originals", "thumbs", "ocr_rows", "events"}``.
        """
        ...

    async def count_snapshot(self, *, gc_cutoff: Any = None) -> dict:
        """Read-only aggregate counts over ``FILES`` (governance dashboard).

        ``gc_cutoff`` given → ``{"gc": {"candidates": n}}`` computed from the
        same predicate as :meth:`purge_ocr_uploads`; otherwise
        ``{"groups": [{kind, purpose, status, ocr_status, files, bytes}],
        "orphans": {"thumb_without_parent", "original_without_thumb"}}``.

        Never reads ``CONTENT``; never writes.
        """
        ...

    async def thumb_for(self, parent_oid: str) -> Row | None:
        """Return the thumbnail row whose ``parent_oid`` is the given original, or ``None``."""
        ...

    async def set_status(self, oid: str, status: str) -> None:
        """Set a file's ``status`` (and bump ``updated_at``)."""
        ...

    async def set_entity(self, oid: str, entity_type: str, entity_oid: str) -> None:
        """Bind a file to ``(entity_type, entity_oid)``."""
        ...

    async def set_ocr(self, oid: str, ocr_status: str, *, bump_retry: bool = False) -> None:
        """Set a file's ``ocr_status``; optionally increment ``ocr_retry``."""
        ...

    async def soft_delete(self, oid: str) -> None:
        """Soft-delete a file (``status='deleted'`` + ``deleted_at``)."""
        ...

    async def purge_deleted(self, before: Any) -> int:
        """Tombstone ``deleted`` files older than ``before`` and return the row count.

        Sets ``content = NULL`` while keeping the metadata row.
        """
        ...

    async def add_ocr(self, file_oid, engine, engine_version, text, confidence, raw) -> str:
        """Insert an OCR result and return its new ``_id`` as a hex string."""
        ...

    async def get_ocr(self, file_oid: str) -> Row | None:
        """Return the newest OCR result for ``file_oid``, or ``None``."""
        ...

    async def add_event(self, file_oid, actor_oid, action, from_status, to_status, detail) -> str:
        """Append an audit event and return its new ``_id`` as a hex string."""
        ...

    async def list_events(self, file_oid: str, *, limit: int | None = None) -> list[Row]:
        """Return ``file_oid``'s audit events in chronological order.

        ``limit`` keeps the newest N rows but still returns them oldest-first.
        """
        ...
