"""Oracle implementation of :class:`db.repos.protocols.FilesRepo`.

SQLAlchemy 2.0 async Core over the ``FILES`` / ``FILE_OCR`` / ``FILE_EVENTS``
tables backing the Files/attachments API (``docs/FILES_API_DESIGN.md`` §3).

Row shapes are Mongo-ready dicts (``"_id"`` = the lowercase-hex ``OID``):

* file rows carry every column **except** ``content``;
* OCR rows map ``ocr_text``→``text`` and ``raw_json``→``raw``;
* event rows carry the audit columns verbatim.

Metadata reads (:meth:`OracleFilesRepo.get_meta` / ``list_page`` /
``thumb_for`` / ``find_by_sha256``) **project columns only** and never select
``CONTENT``: python-oracledb materialises a LOB for every selected BLOB column,
so including it would pull the whole payload into memory on a list page.

:meth:`OracleFilesRepo.get_content` is the first BLOB **read** in this codebase
(``imports`` only ever wrote one) and is **unverified against a live database** —
the design's §12 runtime smoke test (upload → read back → compare sha256) is what
actually exercises it. It tolerates both a driver that returns ``bytes`` and one
that returns an ``oracledb.LOB`` (python-oracledb's default ``fetch_lobs=True``),
reading the LOB explicitly for the sync and async driver modes.

Session ownership mirrors :mod:`db.repos.oracle.users`: with no injected session
each method opens its own through :func:`db.oracle.session.session_scope`, and
writes commit once; an injected session is neither committed nor closed.

Nothing here opens a connection at import time.
"""

from __future__ import annotations

import inspect
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, AsyncIterator

from sqlalchemy import delete, exists, func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from db.ids import new_oid, normalize_oid
from db.models import file_events as file_events_tbl
from db.models import file_ocr as file_ocr_tbl
from db.models import files as files_tbl
from db.oracle.session import session_scope

Row = dict[str, Any]

# Statuses whose bytes are servable (design ``FILES_API_DESIGN.md`` §3.3 / §4 #2):
# the serve gate is ``status IN ('stored','ready','verified')`` and everything
# else — ``deleted`` / ``quarantined`` / ``rejected`` — is a 410 (even with a
# valid signature). :data:`_DEAD_STATUSES` below is the listing-side echo of this
# gate. The dedup lookup below must honour the same gate: it is a
# *skip-the-upload* shortcut, so it may only return a row whose bytes can be
# fetched. Any other row is a guaranteed 410 on every byte read, so "hitting" it
# would hand the caller an oid that can never be served.
#
# This holds regardless of the tombstone retention job: §3.3's purge nulls the
# ``content`` of expired tombstones (``tasks/files_retention.py`` →
# ``purge_expired_attachments``, scheduled daily in ``main.py``), but scheduling
# it is not what makes a tombstone unusable — the serve gate already answers 410
# for it. Should the job run, it only widens the same conclusion from "410" to
# "410 and the bytes are already gone".
_SERVE_STATUSES = ("stored", "ready", "verified")

# Statuses a default listing must never show (the dead ones), i.e. the rows
# whose serve gate rejects them: ``routers/attachments_router.py``'s
# ``SERVE_STATUSES`` + design §4 #2 answer 410 for ``deleted`` / ``rejected`` /
# ``quarantined``, so listing them can only render as an un-fetchable ghost
# entry (same class of bug as the soft-delete ghost, except the client now gets
# a real error detail instead of a silent miss).
#
# Deliberately *not* the complement of ``_SERVE_STATUSES``: the schema CHECK
# (``db/models.py``) also allows ``processing`` — an in-flight status, not a
# dead one (it has no writer today: verification goes through ``set_status`` and
# OCR through ``set_ocr``/``ocr_status``). Deriving this set from the whitelist
# would silently hide any future in-flight status from the listing, which is the
# opposite of what a listing is for. Adding a status here is a product decision.
_DEAD_STATUSES = ("deleted", "rejected", "quarantined")

# 管理员已经给出结论的状态。保留期清理**永不**删除这些行：它们既是审计证据，
# 也是 ``DELETE /api/attachments/{oid}`` 拒绝的对象（design §4 #11）。
_AUDIT_SETTLED_STATUSES = ("verified", "rejected")

# OCR 上传图清理的单批上限。Oracle 的 ``IN (...)`` 硬上限是 1000 项，一批原图
# 及其缩略图最多 2×200 项，两个列表都留足余量。
_OCR_UPLOAD_PURGE_MAX_BATCH = 200

# ``file_oids_with_ocr`` 的 ``IN`` 分片（同 ``db/repos/oracle/users.py`` 的
# ``_OID_CHUNK``：Oracle 一条 ``IN`` 最多 1000 项，超了是 ORA-01795）。
_OID_CHUNK = 500


@asynccontextmanager
async def _session_for(
    session: AsyncSession | None,
) -> AsyncIterator[tuple[AsyncSession, bool]]:
    """Yield ``(session, owns)``; ``owns`` is True only for repo-created ones."""
    if session is not None:
        yield session, False
        return
    async with session_scope() as owned:
        yield owned, True


def _now() -> datetime:
    """Naive ``datetime.now()`` — the schema convention for ``TIMESTAMP`` columns."""
    return datetime.now()


# ---------------------------------------------------------------------------
# statement builders
# ---------------------------------------------------------------------------

def _meta_columns() -> tuple[Any, ...]:
    """Every ``FILES`` column **except** ``content`` (never project the BLOB)."""
    return (
        files_tbl.c.oid,
        files_tbl.c.owner_oid,
        files_tbl.c.kind,
        files_tbl.c.parent_oid,
        files_tbl.c.mime,
        files_tbl.c.size_bytes,
        files_tbl.c.width,
        files_tbl.c.height,
        files_tbl.c.sha256,
        files_tbl.c.original_name,
        files_tbl.c.purpose,
        files_tbl.c.entity_type,
        files_tbl.c.entity_oid,
        files_tbl.c.status,
        files_tbl.c.ocr_status,
        files_tbl.c.ocr_retry,
        files_tbl.c.created_at,
        files_tbl.c.updated_at,
        files_tbl.c.deleted_at,
    )


def _insert_stmt(
    oid: str,
    owner_oid: str | None,
    kind: str,
    parent_oid: str | None,
    mime: str,
    size_bytes: int,
    width: int | None,
    height: int | None,
    sha256: str,
    content: bytes | None,
    original_name: str | None,
    purpose: str | None,
):
    now = _now()
    return insert(files_tbl).values(
        oid=oid,
        owner_oid=owner_oid,
        kind=kind,
        parent_oid=parent_oid,
        mime=mime,
        size_bytes=size_bytes,
        width=width,
        height=height,
        sha256=sha256,
        content=content,
        original_name=original_name,
        purpose=purpose,
        status="stored",
        ocr_status="pending",
        ocr_retry=0,
        created_at=now,
        updated_at=now,
    )


def _get_meta_stmt(oid: str):
    return select(*_meta_columns()).where(files_tbl.c.oid == oid)


def _get_content_stmt(oid: str):
    return select(files_tbl.c.content).where(files_tbl.c.oid == oid)


def _find_by_sha256_stmt(owner_oid: str, sha256: str, kind: str):
    """Same-owner dedup lookup (design §8.6), restricted to servable rows.

    The ``status`` predicate is not optional: dedup is a *skip-the-upload*
    shortcut, so it may only ever return a row the caller can actually fetch.
    Matching a ``deleted``/``quarantined``/``rejected`` row would return an oid
    that ``GET /api/attachments/{oid}`` (and ``/thumb``) answers 410 for — the
    serve gate rejects those statuses on every byte read — so the upload would
    be reported as "side-stepped" while nothing new was stored.

    Note the §3.3 retention job (``purge_deleted``, wired as
    ``tasks/files_retention.py`` / ``main.py``'s ``daily_purge_files``) is not
    what makes a tombstone unusable, it would merely also clear its bytes.
    """
    return (
        select(*_meta_columns())
        .where(
            files_tbl.c.owner_oid == owner_oid,
            files_tbl.c.sha256 == sha256,
            files_tbl.c.kind == kind,
            files_tbl.c.status.in_(_SERVE_STATUSES),
        )
        .order_by(files_tbl.c.created_at.desc(), files_tbl.c.oid.desc())
        .limit(1)
    )


def _list_conds(
    owner_oid: str | None,
    purpose: str | None,
    status: str | None,
    entity_type: str | None,
    entity_oid: str | None,
    *,
    created_from: Any = None,
    created_to: Any = None,
    ocr_status: str | None = None,
    kind: str | None = None,
) -> list[Any]:
    """WHERE conditions shared by :func:`_list_page_stmt` and :func:`_count_stmt`.

    Default semantics: with no explicit ``status`` the listing **excludes every
    dead status** (``deleted`` / ``rejected`` / ``quarantined`` — see
    :data:`_DEAD_STATUSES`), so a ``GET /api/attachments`` (whose caller does not
    pass ``status``) never surfaces an attachment that the serve gate would
    answer 410 for. In-flight statuses such as ``processing`` are **kept**.
    Passing ``status`` restores exact matching, including
    ``status='deleted'`` / ``'rejected'`` for administrative audit views.
    Because both the page query and the count query build their WHERE from this
    one helper, the paged rows and the reported total always share the same
    filter.

    The administrative filters (``created_from`` / ``created_to`` /
    ``ocr_status`` / ``kind``) are half-open on the upper edge: a row is kept
    when ``created_from <= created_at < created_to``. The HTTP layer turns a
    date-only ``created_to`` into the next midnight, so "up to 2026-10-01" means
    "including everything created on 2026-10-01".
    """
    conds: list[Any] = []
    if owner_oid is not None:
        conds.append(files_tbl.c.owner_oid == owner_oid)
    if purpose is not None:
        conds.append(files_tbl.c.purpose == purpose)
    if status is not None:
        conds.append(files_tbl.c.status == status)
    else:
        conds.append(files_tbl.c.status.notin_(_DEAD_STATUSES))
    if entity_type is not None:
        conds.append(files_tbl.c.entity_type == entity_type)
    if entity_oid is not None:
        conds.append(files_tbl.c.entity_oid == entity_oid)
    if created_from is not None:
        conds.append(files_tbl.c.created_at >= created_from)
    if created_to is not None:
        conds.append(files_tbl.c.created_at < created_to)
    if ocr_status is not None:
        conds.append(files_tbl.c.ocr_status == ocr_status)
    if kind is not None:
        conds.append(files_tbl.c.kind == kind)
    return conds


def _list_page_stmt(
    owner_oid: str | None,
    purpose: str | None,
    status: str | None,
    entity_type: str | None,
    entity_oid: str | None,
    page: int,
    size: int,
    *,
    created_from: Any = None,
    created_to: Any = None,
    ocr_status: str | None = None,
    kind: str | None = None,
):
    stmt = select(*_meta_columns())
    conds = _list_conds(
        owner_oid,
        purpose,
        status,
        entity_type,
        entity_oid,
        created_from=created_from,
        created_to=created_to,
        ocr_status=ocr_status,
        kind=kind,
    )
    if conds:
        stmt = stmt.where(*conds)
    return (
        stmt.order_by(files_tbl.c.created_at.desc(), files_tbl.c.oid.desc())
        .offset(0 if page == -1 else (page - 1) * size)
        .limit(size)
    )


def _count_stmt(
    owner_oid: str | None,
    purpose: str | None,
    status: str | None,
    entity_type: str | None,
    entity_oid: str | None,
    *,
    created_from: Any = None,
    created_to: Any = None,
    ocr_status: str | None = None,
    kind: str | None = None,
):
    stmt = select(func.count()).select_from(files_tbl)
    conds = _list_conds(
        owner_oid,
        purpose,
        status,
        entity_type,
        entity_oid,
        created_from=created_from,
        created_to=created_to,
        ocr_status=ocr_status,
        kind=kind,
    )
    if conds:
        stmt = stmt.where(*conds)
    return stmt


def _thumb_for_stmt(parent_oid: str):
    return (
        select(*_meta_columns())
        .where(files_tbl.c.kind == "thumb", files_tbl.c.parent_oid == parent_oid)
        .order_by(files_tbl.c.created_at.desc(), files_tbl.c.oid.desc())
        .limit(1)
    )


def _set_status_stmt(oid: str, status: str):
    return (
        update(files_tbl)
        .where(files_tbl.c.oid == oid)
        .values(status=status, updated_at=_now())
    )


def _set_entity_stmt(oid: str, entity_type: str, entity_oid: str):
    return (
        update(files_tbl)
        .where(files_tbl.c.oid == oid)
        .values(entity_type=entity_type, entity_oid=entity_oid, updated_at=_now())
    )


def _set_ocr_stmt(oid: str, ocr_status: str, bump_retry: bool):
    values: dict[str, Any] = {"ocr_status": ocr_status, "updated_at": _now()}
    if bump_retry:
        values["ocr_retry"] = files_tbl.c.ocr_retry + 1
    return update(files_tbl).where(files_tbl.c.oid == oid).values(**values)


def _soft_delete_stmt(oid: str):
    now = _now()
    return (
        update(files_tbl)
        .where(files_tbl.c.oid == oid)
        .values(status="deleted", deleted_at=now, updated_at=now)
    )


def _purge_deleted_stmt(before: Any):
    return (
        update(files_tbl)
        .where(files_tbl.c.status == "deleted", files_tbl.c.deleted_at < before)
        .values(content=None, updated_at=_now())
    )


def _clamp_batch_limit(batch_limit: int) -> int:
    """Clamp a caller-supplied batch size into ``1.._OCR_UPLOAD_PURGE_MAX_BATCH``.

    Defensive on purpose: the retention task reads its batch size from a
    deployment config file, and a ``0``/negative/garbage value there would
    otherwise turn one batch into "delete nothing" or "delete everything".
    """
    try:
        value = int(batch_limit)
    except (TypeError, ValueError):
        return _OCR_UPLOAD_PURGE_MAX_BATCH
    return max(1, min(value, _OCR_UPLOAD_PURGE_MAX_BATCH))


def _ocr_upload_candidates_stmt(cutoff: Any, limit: int):
    """One batch of OCR upload originals that are safe to hard-delete.

    The predicate is the whole contract (see ``tasks/ocr_upload_gc.py``):

    * ``purpose='volunteer_order'`` — the only purpose the upload API produces
      today; nothing else is auto-generated material;
    * ``entity_type IS NULL`` — never touches an image bound to an activity (the
      volunteer-order images carry ``entity_type='activity'``);
    * ``kind='original'`` — thumbnails are deleted *through* their parent, never
      selected on their own (otherwise a thumb could be orphaned mid-batch);
    * ``created_at < cutoff`` — the retention window;
    * ``status NOT IN ('verified','rejected')`` — an administrative verdict is
      evidence, and the delete endpoint already refuses those rows for everyone;
    * **no non-thumb child** referencing it through ``parent_oid``. Only
      thumbnails are expected to reference an original today, but a future
      ``kind='derived'`` row would make the original load-bearing: skip it
      rather than leave the derived row dangling (there is no FK to cascade).
    """
    child = files_tbl.alias("child")
    referenced = exists(
        select(1)
        .select_from(child)
        .where(child.c.parent_oid == files_tbl.c.oid, child.c.kind != "thumb")
    )
    return (
        select(files_tbl.c.oid)
        .where(
            files_tbl.c.purpose == "volunteer_order",
            files_tbl.c.entity_type.is_(None),
            files_tbl.c.kind == "original",
            files_tbl.c.created_at < cutoff,
            files_tbl.c.status.notin_(_AUDIT_SETTLED_STATUSES),
            ~referenced,
        )
        .order_by(files_tbl.c.created_at.asc(), files_tbl.c.oid.asc())
        .limit(limit)
    )


def _children_stmt(parent_oids: list[str]):
    return select(files_tbl.c.oid, files_tbl.c.kind).where(
        files_tbl.c.parent_oid.in_(parent_oids)
    )


def _delete_originals_stmt(oids: list[str]):
    """Delete the selected originals, re-asserting the "no verdict" guard.

    ``SELECT`` → ``DELETE`` is not a single statement: an adjudication could land
    in between (Oracle forbids ``FOR UPDATE`` together with ``FETCH FIRST``, so
    the candidate query cannot lock its rows). Re-checking the two predicates
    that matter for evidence — ``kind`` and the settled statuses — keeps a row
    that was just verified/rejected alive; the residual window is the couple of
    statements between the side-table deletes and this one, inside one
    transaction.
    """
    return delete(files_tbl).where(
        files_tbl.c.oid.in_(oids),
        files_tbl.c.kind == "original",
        files_tbl.c.status.notin_(_AUDIT_SETTLED_STATUSES),
    )


def _delete_thumb_children_stmt(parent_oids: list[str]):
    return delete(files_tbl).where(
        files_tbl.c.kind == "thumb", files_tbl.c.parent_oid.in_(parent_oids)
    )


def _delete_ocr_rows_stmt(file_oids: list[str]):
    return delete(file_ocr_tbl).where(file_ocr_tbl.c.file_oid.in_(file_oids))


def _delete_file_events_stmt(file_oids: list[str]):
    return delete(file_events_tbl).where(file_events_tbl.c.file_oid.in_(file_oids))


def _ocr_columns() -> tuple[Any, ...]:
    return (
        file_ocr_tbl.c.oid,
        file_ocr_tbl.c.file_oid,
        file_ocr_tbl.c.engine,
        file_ocr_tbl.c.engine_version,
        file_ocr_tbl.c.ocr_text,
        file_ocr_tbl.c.confidence,
        file_ocr_tbl.c.raw_json,
        file_ocr_tbl.c.created_at,
    )


def _insert_ocr_stmt(
    oid: str,
    file_oid: str,
    engine: str | None,
    engine_version: str | None,
    text: str | None,
    confidence: float | None,
    raw: Any,
):
    return insert(file_ocr_tbl).values(
        oid=oid,
        file_oid=file_oid,
        engine=engine,
        engine_version=engine_version,
        ocr_text=text,
        confidence=confidence,
        raw_json=raw,
        created_at=_now(),
    )


def _get_ocr_stmt(file_oid: str):
    return (
        select(*_ocr_columns())
        .where(file_ocr_tbl.c.file_oid == file_oid)
        .order_by(file_ocr_tbl.c.created_at.desc(), file_ocr_tbl.c.oid.desc())
        .limit(1)
    )


def _ocr_oids_stmt(oids: list[str]):
    """Which of ``oids`` have at least one ``FILE_OCR`` row (one round trip).

    Used by the listing to fill ``has_ocr`` for a whole page: the per-row
    :meth:`OracleFilesRepo.get_ocr` would pull every OCR text/raw JSON (a CLOB
    plus a JSON document per row) just to answer a boolean, i.e. the classic
    N+1 with a payload.
    """
    return (
        select(file_ocr_tbl.c.file_oid)
        .where(file_ocr_tbl.c.file_oid.in_(oids))
        .distinct()
    )


def _event_columns() -> tuple[Any, ...]:
    return (
        file_events_tbl.c.oid,
        file_events_tbl.c.file_oid,
        file_events_tbl.c.actor_oid,
        file_events_tbl.c.action,
        file_events_tbl.c.from_status,
        file_events_tbl.c.to_status,
        file_events_tbl.c.detail,
        file_events_tbl.c.created_at,
    )


def _insert_event_stmt(
    oid: str,
    file_oid: str,
    actor_oid: str | None,
    action: str,
    from_status: str | None,
    to_status: str | None,
    detail: Any,
):
    return insert(file_events_tbl).values(
        oid=oid,
        file_oid=file_oid,
        actor_oid=actor_oid,
        action=action,
        from_status=from_status,
        to_status=to_status,
        detail=detail,
        created_at=_now(),
    )


def _list_events_stmt(file_oid: str, *, limit: int | None = None):
    """Chronological timeline; ``limit`` keeps the **newest** N rows.

    ``DESC`` + ``LIMIT`` is what makes "the newest 50" cheap on a row with a
    long audit history; the rows are reversed back to chronological order by
    the caller. Oracle's ``FETCH FIRST`` supports ``ORDER BY`` fine (only
    ``DELETE`` lacks it — see ``_purge_trash_stmt`` in ``users.py``).
    """
    if limit is None:
        order = (file_events_tbl.c.created_at.asc(), file_events_tbl.c.oid.asc())
    else:
        order = (file_events_tbl.c.created_at.desc(), file_events_tbl.c.oid.desc())
    stmt = (
        select(*_event_columns())
        .where(file_events_tbl.c.file_oid == file_oid)
        .order_by(*order)
    )
    if limit is not None:
        stmt = stmt.limit(limit)
    return stmt


# ---------------------------------------------------------------------------
# row mappers + LOB reader
# ---------------------------------------------------------------------------

def _file_row(mapping: Any) -> Row:
    """File metadata row (no ``content`` — it is never selected)."""
    return {
        "_id": mapping["oid"],
        "owner_oid": mapping["owner_oid"],
        "kind": mapping["kind"],
        "parent_oid": mapping["parent_oid"],
        "mime": mapping["mime"],
        "size_bytes": mapping["size_bytes"],
        "width": mapping["width"],
        "height": mapping["height"],
        "sha256": mapping["sha256"],
        "original_name": mapping["original_name"],
        "purpose": mapping["purpose"],
        "entity_type": mapping["entity_type"],
        "entity_oid": mapping["entity_oid"],
        "status": mapping["status"],
        "ocr_status": mapping["ocr_status"],
        "ocr_retry": mapping["ocr_retry"],
        "created_at": mapping["created_at"],
        "updated_at": mapping["updated_at"],
        "deleted_at": mapping["deleted_at"],
    }


def _ocr_row(mapping: Any) -> Row:
    return {
        "_id": mapping["oid"],
        "file_oid": mapping["file_oid"],
        "engine": mapping["engine"],
        "engine_version": mapping["engine_version"],
        "text": mapping["ocr_text"],
        "confidence": mapping["confidence"],
        "raw": mapping["raw_json"],
        "created_at": mapping["created_at"],
    }


def _event_row(mapping: Any) -> Row:
    return {
        "_id": mapping["oid"],
        "file_oid": mapping["file_oid"],
        "actor_oid": mapping["actor_oid"],
        "action": mapping["action"],
        "from_status": mapping["from_status"],
        "to_status": mapping["to_status"],
        "detail": mapping["detail"],
        "created_at": mapping["created_at"],
    }


async def _read_blob(value: Any) -> bytes | None:
    """Return a selected ``BLOB`` column value as ``bytes``.

    python-oracledb defaults to ``fetch_lobs=True``, so the driver may hand back
    an ``oracledb.LOB`` (sync) or its async variant instead of ``bytes``; read it
    explicitly. ``LOB.read()`` returns ``bytes`` on the sync driver and a
    coroutine on the async driver (driven through SQLAlchemy's greenlet adapter),
    so the result is awaited when it is awaitable. NOTE: unverified against a
    live database — exercised by the design's §12 runtime smoke test.
    """
    if value is None:
        return None
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value)
    read = getattr(value, "read", None)
    if read is None:
        return bytes(value)
    data = read()
    if inspect.isawaitable(data):
        data = await data
    if data is None:
        return None
    if isinstance(data, str):
        data = data.encode()
    return bytes(data)


class OracleFilesRepo:
    """``FilesRepo`` over Oracle ADB (``FILES`` / ``FILE_OCR`` / ``FILE_EVENTS``)."""

    def __init__(self, session: AsyncSession | None = None) -> None:
        self._session = session

    async def create(
        self,
        owner_oid: str | None,
        kind: str,
        parent_oid: str | None,
        mime: str,
        size_bytes: int,
        width: int | None,
        height: int | None,
        sha256: str,
        content: bytes,
        original_name: str | None,
        purpose: str | None,
    ) -> str:
        oid = new_oid()
        payload = bytes(content) if content is not None else None
        stmt = _insert_stmt(
            oid,
            owner_oid,
            kind,
            parent_oid,
            mime,
            size_bytes,
            width,
            height,
            sha256,
            payload,
            original_name,
            purpose,
        )
        async with _session_for(self._session) as (session, owns):
            await session.execute(stmt)
            if owns:
                await session.commit()
        return oid

    async def get_meta(self, oid: str) -> Row | None:
        stmt = _get_meta_stmt(normalize_oid(oid))
        async with _session_for(self._session) as (session, _owns):
            row = (await session.execute(stmt)).first()
        if row is None:
            return None
        return _file_row(row._mapping)

    async def get_content(self, oid: str) -> bytes | None:
        stmt = _get_content_stmt(normalize_oid(oid))
        async with _session_for(self._session) as (session, _owns):
            row = (await session.execute(stmt)).first()
            if row is None:
                return None
            # Read the LOB inside the scope: a LOB handle is only valid while its
            # connection stays open.
            return await _read_blob(row._mapping["content"])

    async def find_by_sha256(
        self, owner_oid: str, sha256: str, kind: str
    ) -> Row | None:
        stmt = _find_by_sha256_stmt(owner_oid, sha256, kind)
        async with _session_for(self._session) as (session, _owns):
            row = (await session.execute(stmt)).first()
        if row is None:
            return None
        return _file_row(row._mapping)

    async def list_page(
        self,
        owner_oid: str | None,
        purpose: str | None,
        status: str | None,
        entity_type: str | None,
        entity_oid: str | None,
        page: int,
        size: int,
        *,
        created_from: Any = None,
        created_to: Any = None,
        ocr_status: str | None = None,
        kind: str | None = None,
    ) -> tuple[list[Row], int]:
        page_stmt = _list_page_stmt(
            owner_oid,
            purpose,
            status,
            entity_type,
            entity_oid,
            page,
            size,
            created_from=created_from,
            created_to=created_to,
            ocr_status=ocr_status,
            kind=kind,
        )
        count_stmt = _count_stmt(
            owner_oid,
            purpose,
            status,
            entity_type,
            entity_oid,
            created_from=created_from,
            created_to=created_to,
            ocr_status=ocr_status,
            kind=kind,
        )
        async with _session_for(self._session) as (session, _owns):
            total = (await session.execute(count_stmt)).scalar_one()
            rows = (await session.execute(page_stmt)).all()
        return [_file_row(row._mapping) for row in rows], int(total)

    async def file_oids_with_ocr(self, oids: list[str]) -> set[str]:
        """Return the subset of ``oids`` that has any ``FILE_OCR`` row.

        One statement per ≤:data:`_OID_CHUNK` ids, no payload columns: this is
        the listing's ``has_ocr`` flag, not an OCR read.
        """
        unique = list(dict.fromkeys(normalize_oid(oid) for oid in oids))
        if not unique:
            return set()
        found: set[str] = set()
        async with _session_for(self._session) as (session, _owns):
            for start in range(0, len(unique), _OID_CHUNK):
                chunk = unique[start : start + _OID_CHUNK]
                rows = (await session.execute(_ocr_oids_stmt(chunk))).all()
                found.update(str(row[0]) for row in rows)
        return found

    async def thumb_for(self, parent_oid: str) -> Row | None:
        stmt = _thumb_for_stmt(normalize_oid(parent_oid))
        async with _session_for(self._session) as (session, _owns):
            row = (await session.execute(stmt)).first()
        if row is None:
            return None
        return _file_row(row._mapping)

    async def set_status(self, oid: str, status: str) -> None:
        stmt = _set_status_stmt(normalize_oid(oid), status)
        async with _session_for(self._session) as (session, owns):
            await session.execute(stmt)
            if owns:
                await session.commit()

    async def set_entity(self, oid: str, entity_type: str, entity_oid: str) -> None:
        stmt = _set_entity_stmt(normalize_oid(oid), entity_type, entity_oid)
        async with _session_for(self._session) as (session, owns):
            await session.execute(stmt)
            if owns:
                await session.commit()

    async def set_ocr(
        self, oid: str, ocr_status: str, *, bump_retry: bool = False
    ) -> None:
        stmt = _set_ocr_stmt(normalize_oid(oid), ocr_status, bump_retry)
        async with _session_for(self._session) as (session, owns):
            await session.execute(stmt)
            if owns:
                await session.commit()

    async def soft_delete(self, oid: str) -> None:
        stmt = _soft_delete_stmt(normalize_oid(oid))
        async with _session_for(self._session) as (session, owns):
            await session.execute(stmt)
            if owns:
                await session.commit()

    async def purge_deleted(self, before: Any) -> int:
        stmt = _purge_deleted_stmt(before)
        async with _session_for(self._session) as (session, owns):
            result = await session.execute(stmt)
            if owns:
                await session.commit()
        return int(result.rowcount)

    async def purge_ocr_uploads(
        self, cutoff: Any, batch_limit: int = _OCR_UPLOAD_PURGE_MAX_BATCH
    ) -> dict[str, int]:
        """Hard-delete one batch of expired OCR upload images and their children.

        Selection: :func:`_ocr_upload_candidates_stmt` (see its docstring for the
        full predicate). For every selected original this also removes

        * its ``kind='thumb'`` children (same ``FILES`` table, linked by
          ``parent_oid`` — the schema has no FK, so nothing cascades);
        * the ``FILE_OCR`` rows of the original **and** of its thumbnails;
        * the ``FILE_EVENTS`` audit rows of the same set (a hard delete leaves no
          row to attach history to).

        Deleting the audit rows is deliberate: the retention rule is about
        transient upload material that nobody adjudicated, and keeping
        ``FILE_EVENTS`` rows pointing at a vanished ``FILES`` row would create
        exactly the dangling references this method avoids elsewhere.

        Ordering matters — children and side tables first, the originals last,
        so an interrupted batch never leaves a thumb/OCR row whose parent is
        already gone. Everything runs in the caller's session when one was
        injected, otherwise in one owned transaction (one commit per batch, the
        ``tasks/user_trash_gc.py`` discipline). The final ``DELETE`` re-asserts
        ``kind='original'`` + "no verdict yet" (see :func:`_delete_originals_stmt`).

        Returns ``{"originals", "thumbs", "ocr_rows", "events"}`` deleted counts.
        """
        limit = _clamp_batch_limit(batch_limit)
        counts = {"originals": 0, "thumbs": 0, "ocr_rows": 0, "events": 0}
        async with _session_for(self._session) as (session, owns):
            candidates = (
                await session.execute(_ocr_upload_candidates_stmt(cutoff, limit))
            ).all()
            originals = [str(row[0]) for row in candidates]
            if not originals:
                return counts

            children = (await session.execute(_children_stmt(originals))).all()
            thumbs = [str(row[0]) for row in children if str(row[1]) == "thumb"]

            file_oids = originals + thumbs
            ocr_result = await session.execute(_delete_ocr_rows_stmt(file_oids))
            events_result = await session.execute(_delete_file_events_stmt(file_oids))
            thumbs_result = await session.execute(
                _delete_thumb_children_stmt(originals)
            )
            originals_result = await session.execute(_delete_originals_stmt(originals))

            counts["ocr_rows"] = max(0, int(ocr_result.rowcount))
            counts["events"] = max(0, int(events_result.rowcount))
            counts["thumbs"] = max(0, int(thumbs_result.rowcount))
            counts["originals"] = max(0, int(originals_result.rowcount))
            if owns:
                await session.commit()
        return counts

    async def add_ocr(
        self,
        file_oid: str,
        engine: str | None,
        engine_version: str | None,
        text: str | None,
        confidence: float | None,
        raw: Any,
    ) -> str:
        oid = new_oid()
        stmt = _insert_ocr_stmt(
            oid,
            normalize_oid(file_oid),
            engine,
            engine_version,
            text,
            confidence,
            raw,
        )
        async with _session_for(self._session) as (session, owns):
            await session.execute(stmt)
            if owns:
                await session.commit()
        return oid

    async def get_ocr(self, file_oid: str) -> Row | None:
        stmt = _get_ocr_stmt(normalize_oid(file_oid))
        async with _session_for(self._session) as (session, _owns):
            row = (await session.execute(stmt)).first()
        if row is None:
            return None
        return _ocr_row(row._mapping)

    async def add_event(
        self,
        file_oid: str,
        actor_oid: str | None,
        action: str,
        from_status: str | None,
        to_status: str | None,
        detail: Any,
    ) -> str:
        oid = new_oid()
        stmt = _insert_event_stmt(
            oid,
            normalize_oid(file_oid),
            actor_oid,
            action,
            from_status,
            to_status,
            detail,
        )
        async with _session_for(self._session) as (session, owns):
            await session.execute(stmt)
            if owns:
                await session.commit()
        return oid

    async def list_events(self, file_oid: str, *, limit: int | None = None) -> list[Row]:
        """Return ``file_oid``'s audit events, oldest first.

        ``limit`` keeps only the **newest** ``limit`` events (still returned
        oldest-first, so the client renders a chronological timeline).
        """
        stmt = _list_events_stmt(normalize_oid(file_oid), limit=limit)
        async with _session_for(self._session) as (session, _owns):
            rows = (await session.execute(stmt)).all()
        events = [_event_row(row._mapping) for row in rows]
        if limit is not None:
            events.reverse()
        return events


__all__ = ["OracleFilesRepo"]
