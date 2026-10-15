"""Frozen-format dump of the six derived statistics tables.

Reads ``user_time`` / ``time_academic_year`` / ``time_with_origin`` /
``time_with_origin_stat`` / ``indicators`` / ``group_indicators`` through the
SQLAlchemy Core metadata in :mod:`db.models`, drops ``oid`` / ``updated_at`` and
emits the frozen JSON shape::

    {
      "user_time":            [{"user_oid","on_campus_raw","off_campus_raw",
                                "social_practice"}],
      "time_academic_year":   [{"user_oid","entry_year","academic_year",
                                "on_campus","off_campus","social_practice"}],
      "time_with_origin":     [{"user_oid","data"}],
      "time_with_origin_stat":[{"stat_key","scope","scope_id",
                                "require_percentage","data"}],
      "indicators":           {"percentiles","indicators"},
      "group_indicators":     [{"group_oid","percentiles","indicators"}]
    }

``data`` / ``percentiles`` / ``indicators`` are JSON columns; the
:class:`db.models.JSONText` TypeDecorator returns them as Python ``dict``s.
Every list is sorted by its business key (never by ``oid``), so two dumps of the
same logical state are byte-identical.

The ``__main__`` guard connects to the configured database — do not run it in CI.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import select

from db.models import group_indicators as group_tbl
from db.models import indicators as indicators_tbl
from db.models import time_academic_year as ay_tbl
from db.models import time_with_origin as origin_tbl
from db.models import time_with_origin_stat as stat_tbl
from db.models import user_time as time_tbl

HERE = Path(__file__).resolve().parent
DUMP_PATH = HERE / "dump.json"

#: Top-level list tables, in dump order.
LIST_TABLES = (
    "user_time",
    "time_academic_year",
    "time_with_origin",
    "time_with_origin_stat",
    "group_indicators",
)


async def _fetch(session: Any, stmt: Any) -> list[Any]:
    return (await session.execute(stmt)).all()


async def dump(session: Any) -> dict[str, Any]:
    """Return the frozen-shaped dump of the six tables on ``session``'s schema."""
    time_rows = await _fetch(
        session,
        select(
            time_tbl.c.user_oid,
            time_tbl.c.on_campus_raw,
            time_tbl.c.off_campus_raw,
            time_tbl.c.social_practice,
        ),
    )
    ay_rows = await _fetch(
        session,
        select(
            ay_tbl.c.user_oid,
            ay_tbl.c.entry_year,
            ay_tbl.c.academic_year,
            ay_tbl.c.on_campus,
            ay_tbl.c.off_campus,
            ay_tbl.c.social_practice,
        ),
    )
    origin_rows = await _fetch(
        session,
        select(origin_tbl.c.user_oid, origin_tbl.c.data),
    )
    stat_rows = await _fetch(
        session,
        select(
            stat_tbl.c.stat_key,
            stat_tbl.c.scope,
            stat_tbl.c.scope_id,
            stat_tbl.c.require_percentage,
            stat_tbl.c.data,
        ),
    )
    indicator_rows = await _fetch(
        session,
        select(indicators_tbl.c.percentiles, indicators_tbl.c.indicators).limit(1),
    )
    group_rows = await _fetch(
        session,
        select(
            group_tbl.c.group_oid,
            group_tbl.c.percentiles,
            group_tbl.c.indicators,
        ),
    )

    return {
        "user_time": sorted(
            (
                {
                    "user_oid": r._mapping["user_oid"],
                    "on_campus_raw": r._mapping["on_campus_raw"],
                    "off_campus_raw": r._mapping["off_campus_raw"],
                    "social_practice": r._mapping["social_practice"],
                }
                for r in time_rows
            ),
            key=lambda row: row["user_oid"],
        ),
        "time_academic_year": sorted(
            (
                {
                    "user_oid": r._mapping["user_oid"],
                    "entry_year": r._mapping["entry_year"],
                    "academic_year": r._mapping["academic_year"],
                    "on_campus": r._mapping["on_campus"],
                    "off_campus": r._mapping["off_campus"],
                    "social_practice": r._mapping["social_practice"],
                }
                for r in ay_rows
            ),
            key=lambda row: (row["user_oid"], row["academic_year"]),
        ),
        "time_with_origin": sorted(
            (
                {"user_oid": r._mapping["user_oid"], "data": r._mapping["data"]}
                for r in origin_rows
            ),
            key=lambda row: row["user_oid"],
        ),
        "time_with_origin_stat": sorted(
            (
                {
                    "stat_key": r._mapping["stat_key"],
                    "scope": r._mapping["scope"],
                    "scope_id": r._mapping["scope_id"],
                    "require_percentage": r._mapping["require_percentage"],
                    "data": r._mapping["data"],
                }
                for r in stat_rows
            ),
            key=lambda row: row["stat_key"],
        ),
        "indicators": (
            {
                "percentiles": indicator_rows[0]._mapping["percentiles"],
                "indicators": indicator_rows[0]._mapping["indicators"],
            }
            if indicator_rows
            else {"percentiles": None, "indicators": None}
        ),
        "group_indicators": sorted(
            (
                {
                    "group_oid": r._mapping["group_oid"],
                    "percentiles": r._mapping["percentiles"],
                    "indicators": r._mapping["indicators"],
                }
                for r in group_rows
            ),
            key=lambda row: row["group_oid"],
        ),
    }


def write_dump(data: dict[str, Any], path: Path | str = DUMP_PATH) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return target


def read_dump(path: Path | str) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


async def dump_to_file(session: Any, path: Path | str = DUMP_PATH) -> dict[str, Any]:
    data = await dump(session)
    write_dump(data, path)
    return data


async def _main(argv: list[str]) -> int:
    # Connects to the configured Oracle instance — server-side use only.
    from db.oracle.session import session_scope

    print(
        "WARNING: dump.py connects to the configured database. "
        "Keep this out of CI/local runs.",
        file=sys.stderr,
    )
    target = Path(argv[1]) if len(argv) > 1 else DUMP_PATH
    async with session_scope() as session:
        data = await dump(session)
    write_dump(data, target)
    print(f"wrote {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main(sys.argv)))
