"""Oracle implementation of :class:`db.repos.protocols.TasksRepo`.

SQLAlchemy 2.0 async Core over the ``EXPORT_TASKS`` table (``zvms_new.tasks``).
``TASK_UUID`` stores the canonical string form of the Mongo ``id``
(:class:`uuid.UUID`); returned rows expose ``id`` back as a ``uuid.UUID``.
``RESULT`` is a JSON ``CLOB`` list. ``ALLOW_CACHE``/``INCLUDE_DESCRIPTION`` are
``NUMBER(1)`` booleans exposed as Python ``bool``.

Session ownership mirrors :mod:`db.repos.oracle.users`. ``push_result`` performs a
read-modify-write of the ``result`` list (Oracle has no ``$push``).

Nothing here opens a connection at import time.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from sqlalchemy import delete, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from db.ids import new_oid, normalize_oid
from db.models import export_tasks as tasks_tbl
from db.oracle.session import session_scope

Row = dict[str, Any]

# Mongo field name -> Oracle column name (spec §3 task mapping).
_FIELD_COLUMNS: dict[str, str] = {
    "status": "status",
    "percentage": "percentage",
    "variant": "variant",
    "format": "export_format",
    "allow_cache": "allow_cache",
    "include_description": "include_description",
    "errmsg": "errmsg",
    "export_start": "export_start",
    "export_end": "export_end",
    "task_start": "task_start",
    "task_end": "task_end",
    "result": "result",
}
_BOOL_FIELDS = frozenset({"allow_cache", "include_description"})


def _uuid_str(value: Any) -> str:
    """Return the canonical string form of a ``UUID`` (or UUID-like string)."""
    if isinstance(value, uuid.UUID):
        return str(value)
    return str(uuid.UUID(str(value)))


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


def _task_columns() -> tuple[Any, ...]:
    return (
        tasks_tbl.c.oid,
        tasks_tbl.c.task_uuid,
        tasks_tbl.c.status,
        tasks_tbl.c.percentage,
        tasks_tbl.c.variant,
        tasks_tbl.c.export_format,
        tasks_tbl.c.allow_cache,
        tasks_tbl.c.include_description,
        tasks_tbl.c.errmsg,
        tasks_tbl.c.export_start,
        tasks_tbl.c.export_end,
        tasks_tbl.c.task_start,
        tasks_tbl.c.task_end,
        tasks_tbl.c["result"],
    )


def _insert_stmt(
    oid: str,
    task_uuid: str,
    status: str,
    percentage: float | None,
    variant: str | None,
    export_format: str | None,
    allow_cache: int,
    include_description: int,
    errmsg: str | None,
    export_start: Any,
    export_end: Any,
    task_start: Any,
    task_end: Any,
    result: Any,
):
    return insert(tasks_tbl).values(
        oid=oid,
        task_uuid=task_uuid,
        status=status,
        percentage=percentage,
        variant=variant,
        export_format=export_format,
        allow_cache=allow_cache,
        include_description=include_description,
        errmsg=errmsg,
        export_start=export_start,
        export_end=export_end,
        task_start=task_start,
        task_end=task_end,
        result=result,
    )


def _get_by_uuid_stmt(task_uuid: str):
    return select(*_task_columns()).where(tasks_tbl.c.task_uuid == task_uuid)


def _get_stmt(oid: str):
    return select(*_task_columns()).where(tasks_tbl.c.oid == oid)


def _list_all_stmt():
    return select(*_task_columns()).order_by(tasks_tbl.c.oid.asc())


def _delete_stmt(task_uuid: str):
    return delete(tasks_tbl).where(tasks_tbl.c.task_uuid == task_uuid)


def _update_stmt(task_uuid: str, values: Row):
    return update(tasks_tbl).where(tasks_tbl.c.task_uuid == task_uuid).values(**values)


def _result_stmt(task_uuid: str):
    return select(tasks_tbl.c["result"]).where(tasks_tbl.c.task_uuid == task_uuid)


def _fail_pending_stmt(reason: str):
    return (
        update(tasks_tbl)
        .where(tasks_tbl.c.status != "completed")
        .values(status="failed", errmsg=reason)
    )


def _field_values(fields: Row) -> Row:
    """Map Mongo-keyed ``$set`` fields to Oracle column values (booleans → 0/1)."""
    values: Row = {}
    for key, value in fields.items():
        column = _FIELD_COLUMNS.get(key)
        if column is None:
            continue
        if key in _BOOL_FIELDS:
            value = 1 if value else 0
        values[column] = value
    return values


def _task_row(mapping: Any) -> Row:
    """Mongo document shape; ``id`` is a ``uuid.UUID``, booleans are ``bool``."""
    raw_uuid = mapping["task_uuid"]
    try:
        task_uuid: Any = uuid.UUID(raw_uuid) if raw_uuid is not None else None
    except (ValueError, TypeError):
        task_uuid = raw_uuid
    result = mapping["result"]
    return {
        "_id": mapping["oid"],
        "id": task_uuid,
        "status": mapping["status"],
        "format": mapping["export_format"],
        "variant": mapping["variant"],
        "export_start": mapping["export_start"],
        "export_end": mapping["export_end"],
        "task_start": mapping["task_start"],
        "task_end": mapping["task_end"],
        "percentage": mapping["percentage"],
        "errmsg": mapping["errmsg"] or "",
        "result": result if result is not None else [],
        "allow_cache": bool(mapping["allow_cache"]),
        "include_description": bool(mapping["include_description"]),
    }


class OracleTasksRepo:
    """``TasksRepo`` over Oracle ADB (``zvms_new.tasks``)."""

    def __init__(self, session: AsyncSession | None = None) -> None:
        self._session = session

    async def create(self, document: Row) -> str:
        oid = new_oid()
        stmt = _insert_stmt(
            oid=oid,
            task_uuid=_uuid_str(document["id"]),
            status=document.get("status"),
            percentage=document.get("percentage"),
            variant=document.get("variant"),
            export_format=document.get("format"),
            allow_cache=1 if document.get("allow_cache") else 0,
            include_description=1 if document.get("include_description") else 0,
            errmsg=document.get("errmsg"),
            export_start=document.get("export_start"),
            export_end=document.get("export_end"),
            task_start=document.get("task_start"),
            task_end=document.get("task_end"),
            result=document.get("result") or [],
        )
        async with _session_for(self._session) as (session, owns):
            await session.execute(stmt)
            if owns:
                await session.commit()
        return oid

    async def get_by_uuid(self, task_uuid: Any) -> Row | None:
        stmt = _get_by_uuid_stmt(_uuid_str(task_uuid))
        async with _session_for(self._session) as (session, _owns):
            row = (await session.execute(stmt)).first()
        if row is None:
            return None
        return _task_row(row._mapping)

    async def get(self, oid: str) -> Row | None:
        stmt = _get_stmt(normalize_oid(oid))
        async with _session_for(self._session) as (session, _owns):
            row = (await session.execute(stmt)).first()
        if row is None:
            return None
        return _task_row(row._mapping)

    async def list_all(self) -> list[Row]:
        stmt = _list_all_stmt()
        async with _session_for(self._session) as (session, _owns):
            rows = (await session.execute(stmt)).all()
        return [_task_row(row._mapping) for row in rows]

    async def update_fields(self, task_uuid: Any, fields: Row) -> None:
        values = _field_values(fields)
        if not values:
            return
        stmt = _update_stmt(_uuid_str(task_uuid), values)
        async with _session_for(self._session) as (session, owns):
            await session.execute(stmt)
            if owns:
                await session.commit()

    async def push_result(
        self, task_uuid: Any, rows: list[Row], fields: Row | None = None
    ) -> None:
        uuid_str = _uuid_str(task_uuid)
        values = _field_values(fields) if fields else {}
        async with _session_for(self._session) as (session, owns):
            current = (
                await session.execute(_result_stmt(uuid_str))
            ).scalar_one_or_none()
            values["result"] = list(current or []) + list(rows)
            await session.execute(_update_stmt(uuid_str, values))
            if owns:
                await session.commit()

    async def delete(self, task_uuid: Any) -> None:
        stmt = _delete_stmt(_uuid_str(task_uuid))
        async with _session_for(self._session) as (session, owns):
            await session.execute(stmt)
            if owns:
                await session.commit()

    async def fail_pending(self, reason: str) -> int:
        stmt = _fail_pending_stmt(reason)
        async with _session_for(self._session) as (session, owns):
            result = await session.execute(stmt)
            if owns:
                await session.commit()
        return int(result.rowcount)


__all__ = ["OracleTasksRepo"]
