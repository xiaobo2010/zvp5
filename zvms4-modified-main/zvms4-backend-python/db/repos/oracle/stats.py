"""Oracle implementation of :class:`db.repos.protocols.StatsCacheRepo`.

SQLAlchemy 2.0 async Core over ``INDICATORS`` (global, single-row table enforced
in code) and ``GROUP_INDICATORS`` (one row per group). Column names follow the
rename map in ``docs/ADB_SCHEMA_SPEC.md`` §3 (``group``→``GROUP_OID``;
``percentiles``/``indicators`` are JSON ``CLOB`` columns); returned rows are
translated back to the Mongo document shape.

``upsert_*`` mirror the live Mongo write, which is a **whole-row replace**
(``delete_many`` then ``insert_one``): the delete + insert run in one
transaction and commit **once**.

Session ownership
-----------------
``__init__`` takes an optional ``AsyncSession``:

* ``session is None`` (default): each method opens its own session through
  :func:`db.oracle.session.session_scope`, and **writes commit once** (one write
  = one transaction); the session is closed by ``session_scope``.
* ``session`` supplied: the repo uses it as-is and **never commits or closes**
  it — the caller owns the unit of work.

Nothing here opens a connection at import time.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, AsyncIterator

from sqlalchemy import delete, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.ids import new_oid
from db.models import group_indicators as group_tbl
from db.models import indicators as indicators_tbl
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


def _indicators_columns() -> tuple[Any, ...]:
    """The full INDICATORS projection."""
    return (
        indicators_tbl.c.oid,
        indicators_tbl.c.percentiles,
        indicators_tbl.c.indicators,
        indicators_tbl.c.updated_at,
    )


def _group_indicators_columns() -> tuple[Any, ...]:
    """The full GROUP_INDICATORS projection."""
    return (
        group_tbl.c.oid,
        group_tbl.c.group_oid,
        group_tbl.c.percentiles,
        group_tbl.c.indicators,
        group_tbl.c.updated_at,
    )


def _get_indicators_stmt():
    return select(*_indicators_columns()).limit(1)


def _delete_indicators_stmt():
    # ``NO_PARALLEL`` 是正确性不是调优：ADB 会话带 ``pdml_enabled=YES``，这条
    # 全表 DELETE 会被自动并行，随后同一事务里的 INSERT 撞 ``ORA-12838``。
    # 同款修法见 :mod:`db.repos.oracle.temp_passwords`。
    return delete(indicators_tbl).prefix_with("/*+ NO_PARALLEL */", dialect="oracle")


def _insert_indicators_stmt(
    oid: str, percentiles: Any, indicators: Any, updated_at: datetime | None = None
):
    return insert(indicators_tbl).values(
        oid=oid,
        percentiles=percentiles,
        indicators=indicators,
        updated_at=updated_at or datetime.now(),
    )


def _list_group_indicators_stmt():
    return select(*_group_indicators_columns()).order_by(group_tbl.c.group_oid.asc())


def _delete_group_indicators_stmt(group_oid: str):
    # 同 ``_delete_indicators_stmt``：删完马上插回同一张表，必须串行。
    return (
        delete(group_tbl)
        .where(group_tbl.c.group_oid == group_oid)
        .prefix_with("/*+ NO_PARALLEL */", dialect="oracle")
    )


def _insert_group_indicators_stmt(
    oid: str, group_oid: str, percentiles: Any, indicators: Any
):
    return insert(group_tbl).values(
        oid=oid,
        group_oid=group_oid,
        percentiles=percentiles,
        indicators=indicators,
        updated_at=datetime.now(),
    )


def _indicators_row(mapping: Any) -> Row:
    """Translate an INDICATORS row to the Mongo document shape."""
    return {
        "_id": mapping["oid"],
        "percentiles": mapping["percentiles"],
        "indicators": mapping["indicators"],
        "updated_at": mapping["updated_at"],
    }


def _group_indicators_row(mapping: Any) -> Row:
    """Translate a GROUP_INDICATORS row to the Mongo document shape."""
    return {
        "_id": mapping["oid"],
        "group": mapping["group_oid"],
        "percentiles": mapping["percentiles"],
        "indicators": mapping["indicators"],
        "updated_at": mapping["updated_at"],
    }


class OracleStatsCacheRepo:
    """``StatsCacheRepo`` over Oracle ADB (``indicators``/``group_indicators``)."""

    def __init__(self, session: AsyncSession | None = None) -> None:
        self._session = session

    async def get_indicators(self) -> Row | None:
        stmt = _get_indicators_stmt()
        async with _session_for(self._session) as (session, _owns):
            row = (await session.execute(stmt)).first()
        if row is None:
            return None
        return _indicators_row(row._mapping)

    async def upsert_indicators(self, percentiles: Row, indicators: Row) -> None:
        delete_stmt = _delete_indicators_stmt()
        insert_stmt = _insert_indicators_stmt(new_oid(), percentiles, indicators)
        async with _session_for(self._session) as (session, owns):
            await session.execute(delete_stmt)
            await session.execute(insert_stmt)
            if owns:
                await session.commit()

    def _require_session(self) -> AsyncSession:
        if self._session is None:
            raise RuntimeError(
                "rebuild_* requires a caller-owned session (single-transaction recompute)"
            )
        return self._session

    async def rebuild_indicators(
        self, percentiles: Row, indicators: Row, now: datetime | None = None
    ) -> None:
        """Single-transaction replacement of the single global ``indicators`` row.

        Delete-then-insert (the table is single-row by contract); never commits —
        the caller owns the unit of work.
        """
        session = self._require_session()
        await session.execute(_delete_indicators_stmt())
        await session.execute(
            _insert_indicators_stmt(new_oid(), percentiles, indicators, now)
        )

    async def rebuild_group_indicators(
        self, rows: list[Row], now: datetime | None = None
    ) -> int:
        """Single-transaction replacement of every ``group_indicators`` row.

        ``rows`` carry ``group_oid`` plus the ``percentiles``/``indicators`` JSON
        dicts; oids and ``updated_at`` are minted here. Never commits.
        """
        session = self._require_session()
        await session.execute(
            delete(group_tbl).prefix_with("/*+ NO_PARALLEL */", dialect="oracle")
        )
        ts = now or datetime.now()
        payload = [
            {
                "oid": new_oid(),
                "group_oid": str(row["group_oid"]),
                "percentiles": row["percentiles"],
                "indicators": row["indicators"],
                "updated_at": ts,
            }
            for row in rows
        ]
        if payload:
            await session.execute(insert(group_tbl), payload)
        return len(payload)

    async def list_group_indicators(self) -> list[Row]:
        stmt = _list_group_indicators_stmt()
        async with _session_for(self._session) as (session, _owns):
            rows = (await session.execute(stmt)).all()
        return [_group_indicators_row(row._mapping) for row in rows]

    async def upsert_group_indicators(
        self, group_oid: str, percentiles: Row, indicators: Row
    ) -> None:
        group = str(group_oid)
        delete_stmt = _delete_group_indicators_stmt(group)
        insert_stmt = _insert_group_indicators_stmt(
            new_oid(), group, percentiles, indicators
        )
        async with _session_for(self._session) as (session, owns):
            await session.execute(delete_stmt)
            await session.execute(insert_stmt)
            if owns:
                await session.commit()


__all__ = ["OracleStatsCacheRepo"]
