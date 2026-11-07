"""Bind large Python strings as CLOB LOBs for python-oracledb.

python-oracledb (thin) cannot bind a ``str`` longer than 4000 characters to a
CLOB column: it is sent as ``DB_TYPE_LONG`` and the server rejects it with
``ORA-01461: can bind a LONG value only for insert into a LONG column``.
``cursor.setinputsizes(v=oracledb.DB_TYPE_CLOB)`` does **not** help. Binding an
actual :class:`oracledb.LOB` (a temporary CLOB created on the live connection)
does work, for both a plain ``INSERT`` and the
``MERGE ... USING (SELECT :v AS c FROM dual)`` shape.

This module installs a ``before_cursor_execute`` listener that swaps any
``str`` longer than :data:`CLOB_THRESHOLD` for such a temporary CLOB before the
statement reaches the driver. It is dialect-agnostic where it safely can be and
a no-op for anything that is not Oracle.

Threshold rationale: no ``VARCHAR2`` column in this schema exceeds 2000
characters, so a string longer than 4000 can only be destined for a ``CLOB``.
This removes the need for column introspection and also covers runtime writes
(e.g. an export ``result`` CLOB) that never go through the ETL specs.

Importing this module performs **no I/O**.
"""

from __future__ import annotations

from typing import Any

import oracledb
from sqlalchemy import event

__all__ = ["CLOB_THRESHOLD", "install_lob_binds"]

#: Strings strictly longer than this are rebound as a temporary CLOB.
CLOB_THRESHOLD = 4000


def _to_lob(conn: Any, value: str) -> Any:
    """Return a temporary CLOB holding ``value`` on ``conn``'s live driver.

    Works for both driver modes: the sync ``oracledb.Connection`` (whose
    ``createlob``/``write`` are synchronous) and the async
    ``oracledb.AsyncConnection`` used by ``oracle+oracledb_async``, whose
    ``createlob`` is a coroutine that is driven through SQLAlchemy's greenlet
    adapter (``await_``). ``createlob(type, data)`` writes the data in one
    step, so no separate ``write`` call is needed.
    """
    fairy = conn.connection
    adapt = fairy.dbapi_connection  # async-adapted connection, or raw (sync)
    raw = fairy.driver_connection  # oracledb.AsyncConnection / Connection
    await_ = getattr(adapt, "await_", None)
    if await_ is not None:
        return await_(raw.createlob(oracledb.DB_TYPE_CLOB, value))
    return raw.createlob(oracledb.DB_TYPE_CLOB, value)


def _replace_large_strings(parameters: Any, conn: Any) -> None:
    """Mutate ``parameters`` in place, swapping large strings for CLOBs."""
    if isinstance(parameters, dict):
        rows: Any = (parameters,)
    elif isinstance(parameters, (list, tuple)):
        rows = parameters
    else:
        return

    for row in rows:
        if not isinstance(row, dict):
            continue
        for key, value in list(row.items()):
            if isinstance(value, str) and len(value) > CLOB_THRESHOLD:
                row[key] = _to_lob(conn, value)


def _bind_large_strings(conn, cursor, statement, parameters, context, executemany):
    """``before_cursor_execute`` listener: large ``str`` -> CLOB LOB."""
    if parameters is None:
        return
    dialect = getattr(conn, "dialect", None)
    if dialect is None or getattr(dialect, "name", None) != "oracle":
        return
    _replace_large_strings(parameters, conn)


def _sync_target(engine_or_connection: Any) -> Any:
    """Resolve an async engine/connection to its synchronous counterpart.

    SQLAlchemy does not implement events on ``AsyncEngine``/``AsyncConnection``
    (raises ``NotImplementedError``); the listener must go on ``.sync_engine``
    / ``.sync_connection``.
    """
    sync_engine = getattr(engine_or_connection, "sync_engine", None)
    if sync_engine is not None:
        return sync_engine
    sync_connection = getattr(engine_or_connection, "sync_connection", None)
    if sync_connection is not None:
        return sync_connection
    return engine_or_connection


def install_lob_binds(engine_or_connection: Any) -> None:
    """Idempotently register the large-string -> CLOB listener.

    Accepts an ``Engine``, ``AsyncEngine``, ``Connection`` or
    ``AsyncConnection`` (async targets are resolved to their sync counterpart).
    LOB creation failures are not swallowed: they propagate unchanged.
    """
    target = _sync_target(engine_or_connection)
    if event.contains(target, "before_cursor_execute", _bind_large_strings):
        return
    event.listen(target, "before_cursor_execute", _bind_large_strings)
