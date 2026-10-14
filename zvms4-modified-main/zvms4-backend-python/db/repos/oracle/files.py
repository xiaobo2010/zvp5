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

from sqlalchemy import func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from db.ids import new_oid, normalize_oid
from db.models import file_events as file_events_tbl
from db.models import file_ocr as file_ocr_tbl
from db.models import files as files_tbl
from db.oracle.session import session_scope

Row = dict[str, Any]


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
    return (
        select(*_meta_columns())
        .where(
            files_tbl.c.owner_oid == owner_oid,
            files_tbl.c.sha256 == sha256,
            files_tbl.c.kind == kind,
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
) -> list[Any]:
    conds: list[Any] = []
    if owner_oid is not None:
        conds.append(files_tbl.c.owner_oid == owner_oid)
    if purpose is not None:
        conds.append(files_tbl.c.purpose == purpose)
    if status is not None:
        conds.append(files_tbl.c.status == status)
    if entity_type is not None:
        conds.append(files_tbl.c.entity_type == entity_type)
    if entity_oid is not None:
        conds.append(files_tbl.c.entity_oid == entity_oid)
    return conds


def _list_page_stmt(
    owner_oid: str | None,
    purpose: str | None,
    status: str | None,
    entity_type: str | None,
    entity_oid: str | None,
    page: int,
    size: int,
):
    stmt = select(*_meta_columns())
    conds = _list_conds(owner_oid, purpose, status, entity_type, entity_oid)
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
):
    stmt = select(func.count()).select_from(files_tbl)
    conds = _list_conds(owner_oid, purpose, status, entity_type, entity_oid)
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


def _list_events_stmt(file_oid: str):
    return (
        select(*_event_columns())
        .where(file_events_tbl.c.file_oid == file_oid)
        .order_by(file_events_tbl.c.created_at.asc(), file_events_tbl.c.oid.asc())
    )


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
    ) -> tuple[list[Row], int]:
        page_stmt = _list_page_stmt(
            owner_oid, purpose, status, entity_type, entity_oid, page, size
        )
        count_stmt = _count_stmt(owner_oid, purpose, status, entity_type, entity_oid)
        async with _session_for(self._session) as (session, _owns):
            total = (await session.execute(count_stmt)).scalar_one()
            rows = (await session.execute(page_stmt)).all()
        return [_file_row(row._mapping) for row in rows], int(total)

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

    async def list_events(self, file_oid: str) -> list[Row]:
        stmt = _list_events_stmt(normalize_oid(file_oid))
        async with _session_for(self._session) as (session, _owns):
            rows = (await session.execute(stmt)).all()
        return [_event_row(row._mapping) for row in rows]


__all__ = ["OracleFilesRepo"]
