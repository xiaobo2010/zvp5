"""Apply idempotent DDL migration files to the active Oracle instance.

The application never applies migrations at startup (see ``db/oracle/ddl/README.md``).
Historically the files were run with SQLcl/sqlplus, but the production host ships
neither. This runner uses the application's own session factory, so the wallet,
DSN and credentials come from ``settings.py`` exactly as the runtime does.

Usage (on the server, from the backend root)::

    .venv/bin/python misc/apply_ddl.py --dry-run v034
    .venv/bin/python misc/apply_ddl.py v032 v033 v034

Statements are split on a line containing only ``/`` (the same terminator
SQLcl honours). The migration files are written to be idempotent — every
``ALTER``/``CREATE`` swallows the "already exists" codes — so re-running is the
supported way to repair a half-applied file. ``--dry-run`` only parses and
counts: it never opens a connection.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from sqlalchemy import text

DDL_DIR = Path(__file__).resolve().parent.parent / "db" / "oracle" / "ddl"
TERMINATOR = "/"
_PLSQL_PREFIXES = ("BEGIN", "DECLARE")


def _executable(chunk: str) -> bool:
    """A chunk with nothing but comments/blank lines is not a statement."""
    for line in chunk.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("--"):
            continue
        return True
    return False


def _strip_plain_terminator(chunk: str) -> str:
    """Drop one trailing ``;`` from plain SQL.

    PL/SQL blocks keep theirs (it is part of the block); a plain ``CREATE TABLE
    ...;`` sent with the semicolon fails with ORA-00911.
    """
    head = chunk.lstrip().upper()
    if head.startswith(_PLSQL_PREFIXES):
        return chunk
    if chunk.rstrip().endswith(";"):
        return chunk.rstrip()[:-1].rstrip()
    return chunk


def split_statements(sql: str) -> list[str]:
    """Split a migration file into executable statements (``/`` terminator)."""
    chunks: list[str] = []
    buffer: list[str] = []
    for line in sql.splitlines():
        if line.strip() == TERMINATOR:
            chunk = "\n".join(buffer).strip()
            if chunk and _executable(chunk):
                chunks.append(_strip_plain_terminator(chunk))
            buffer = []
            continue
        buffer.append(line)
    tail = "\n".join(buffer).strip()
    if tail and _executable(tail):
        chunks.append(_strip_plain_terminator(tail))
    return chunks


def resolve(name: str) -> Path:
    """Accept ``v034``, ``V034``, ``V034__foo.sql`` or a full path."""
    raw = Path(name)
    if raw.exists():
        return raw
    lowered = raw.name.lower()
    if lowered.endswith(".sql"):
        stem = lowered[: -len(".sql")]
        prefix = stem.split("_", 1)[0]
    else:
        prefix = lowered
    matches = sorted(DDL_DIR.glob(f"{prefix.upper()}__*.sql"))
    if not matches:
        matches = sorted(DDL_DIR.glob(f"{prefix.lower()}__*.sql"))
    if not matches:
        raise SystemExit(f"no migration matches {name!r} in {DDL_DIR}")
    if len(matches) > 1:
        raise SystemExit(f"{name!r} is ambiguous: {[p.name for p in matches]}")
    return matches[0]


async def _apply(path: Path) -> int:
    from db.oracle.session import session_scope

    statements = split_statements(path.read_text(encoding="utf-8"))
    print(f"{path.name}: {len(statements)} statement(s)")
    async with session_scope() as session:
        for index, statement in enumerate(statements, start=1):
            try:
                await session.execute(text(statement))
                await session.commit()
            except Exception as exc:  # noqa: BLE001 - 报出第几条即可，不要把 SQL 全量打出来
                first = next(
                    (
                        line.strip()
                        for line in statement.splitlines()
                        if line.strip() and not line.strip().startswith("--")
                    ),
                    "",
                )
                raise SystemExit(
                    f"FAILED {path.name} statement {index}/{len(statements)} "
                    f"({first[:80]}): {type(exc).__name__}: {exc}"
                ) from None
            print(f"  ok {index}/{len(statements)}")
    return len(statements)


async def _verify(names: list[str]) -> None:
    """Print the columns of the tables the migrations touch (read-only)."""
    from db.oracle.session import session_scope

    async with session_scope() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT table_name, column_name, data_type "
                    "FROM user_tab_columns "
                    "WHERE table_name IN ("
                    + ", ".join(f":t{i}" for i in range(len(names)))
                    + ") ORDER BY table_name, column_id"
                ),
                {f"t{i}": name.upper() for i, name in enumerate(names)},
            )
        ).all()
    for table, column, data_type in rows:
        print(f"  {table}.{column} {data_type}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("migrations", nargs="+", help="v032 / V033__x.sql / full path")
    parser.add_argument("--dry-run", action="store_true", help="parse only, no connection")
    parser.add_argument(
        "--verify",
        action="append",
        default=[],
        metavar="TABLE",
        help="read-only column dump for a table (repeatable)",
    )
    args = parser.parse_args(argv)

    paths = [resolve(item) for item in args.migrations]
    if args.dry_run:
        for path in paths:
            statements = split_statements(path.read_text(encoding="utf-8"))
            print(f"{path.name}: {len(statements)} statement(s)")
            for index, statement in enumerate(statements, start=1):
                head = next(
                    (
                        line.strip()
                        for line in statement.splitlines()
                        if line.strip() and not line.strip().startswith("--")
                    ),
                    "",
                )
                print(f"  {index:>2}. {head[:100]}")
        return 0

    for path in paths:
        asyncio.run(_apply(path))
    if args.verify:
        asyncio.run(_verify(args.verify))
    return 0


if __name__ == "__main__":
    sys.exit(main())
