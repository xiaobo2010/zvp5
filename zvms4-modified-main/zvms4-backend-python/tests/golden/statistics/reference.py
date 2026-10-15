"""Legacy-path reference implementation for the statistics differential harness.

Reconstructs what the *old* per-user recompute produced, reusing the still-present
legacy code (``util.calculate.calculate_user_time`` / ``time_with_origin`` and the
``tasks.compute_time`` pandas helpers) — never a third implementation. The harness
then asks "does the new set-based ``rebuild_statistics`` agree with the old path?".

The legacy entry points (``compute_time`` / ``compute_ay_time`` /
``compute_batch_origins`` / ``compute_group_indicators`` / ``compute_indicators`` /
``compute_tasks``) were deleted in the rework, so this module re-stitches their
loops. It runs the old code **as it stands today**, which already carries the
confirmed semantic fixes F1/F2 (``util/calculate.py``): the ``time_with_origin``
path is effective-only and no longer skips elements while removing. Differences
that remain by design are documented in the harness README and surfaced by
``run_tier2.py`` as known divergences.

Deliberate deviations from the *deployed* legacy behaviour (registered in the
README):

* **F4** — ``compute_ay_time`` used ``entry = oid[:4]`` (the ObjectId's first four
  hex chars, meaningless). This reference uses ``entry = business_id[:4]``, the
  same grade prefix the new SQL uses.
* **group indicators once** — legacy ``compute_tasks`` ran ``compute_group_indicators``
  twice (once directly, once via ``compute_indicators``); this reference runs it
  once.
* **guards** — a class with no users yields no rows (legacy would have crashed in
  ``process_time_data`` on an empty frame).

Writes into whatever schema the active ``sessionmaker`` points at (the scratch
schema under tier-2). Never commits anything but its own session's work.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pandas as pd
from sqlalchemy import delete

from config import BASE_OFF_CAMPUS, BASE_ON_CAMPUS, BASE_SOCIAL_PRACTICE
from db import backends
from db.models import group_indicators as group_tbl
from db.models import indicators as indicators_tbl
from db.models import time_academic_year as ay_tbl
from db.models import time_with_origin as origin_tbl
from db.models import time_with_origin_stat as stat_tbl
from db.models import user_time as time_tbl
from db.oracle.session import session_scope
from tasks.compute_time import (
    describe_df_percentile,
    describe_df_statistical_indicators,
    process_time_data,
)
from util.calculate import calculate_user_time, time_with_origin

MODES = ("on-campus", "off-campus", "social-practice")
BOUNDS = [BASE_ON_CAMPUS, BASE_OFF_CAMPUS, BASE_SOCIAL_PRACTICE]
REQUIRE_PERCENTAGES = (0, 20, 60, 80)

#: Derived tables the legacy ``clear_*`` helpers used to truncate (deleted with
#: the rework, re-stitched here). No FK between them, so order is irrelevant.
_DERIVED = (time_tbl, ay_tbl, origin_tbl, stat_tbl, group_tbl, indicators_tbl)


async def build_reference(
    session: Any = None, *, now: datetime | None = None, log: Any = print
) -> dict[str, int]:
    """Rebuild the six derived tables via the legacy per-user path.

    Returns a per-table row-count summary. ``session`` is used for the truncation
    and the reads; the legacy helpers open their own sessions through
    ``db.oracle.session.session_scope`` (which tier-2 points at the scratch schema).
    """
    if session is None:
        ctx = session_scope()
        owned = await ctx.__aenter__()
        try:
            summary = await _build(owned, now=now, log=log)
        except BaseException:
            try:
                await ctx.__aexit__(None, None, None)
            except Exception:
                pass
            raise
        try:
            await ctx.__aexit__(None, None, None)
        except Exception as exc:
            log(f"warn: reference session close failed after successful build: {exc}")
        return summary
    return await _build(session, now=now, log=log)


async def _build(session: Any, *, now: datetime | None, log: Any) -> dict[str, int]:
    users_repo = backends.get_users_repo(session)
    groups_repo = backends.get_groups_repo(session)
    time_repo = backends.get_time_repo(session)
    stats_repo = backends.get_stats_repo(session)

    # Legacy ``clear_*``: truncate every derived table up front.
    for table in _DERIVED:
        await session.execute(delete(table))
    await session.commit()

    # --- 1. user_time  (legacy ``compute_time``) -------------------------
    users = await users_repo.list_all()
    user_ids = [str(u["_id"]) for u in users]
    for user_id in user_ids:
        await calculate_user_time(user_id, allow_cache=False)

    # --- 2. time_academic_year  (legacy ``compute_ay_time``, F4 fixed) ---
    moment = now or datetime.now()
    ay = moment.year if moment.month >= 9 else moment.year - 1
    soy = moment.replace(
        month=8, day=1, year=ay, hour=0, minute=0, second=0, microsecond=0
    )
    eoy = moment.replace(
        month=7, day=31, year=ay + 1, hour=23, minute=59, second=59, microsecond=999999
    )
    ay_rows = 0
    for user in users:
        user_id = str(user["_id"])
        entry = str(user.get("id") or "")[:4]  # F4: business_id[:4], not oid[:4]
        result = await calculate_user_time(
            user_id, date_start=soy, date_end=eoy, allow_cache=False
        )
        await time_repo.upsert_academic_year(
            user_id,
            entry,
            ay,
            soy,
            eoy,
            result["on-campus"],
            result["off-campus"],
            result["social-practice"],
        )
        ay_rows += 1

    # --- 3. group_indicators  (legacy ``compute_group_indicators``, once) ---
    groups = await groups_repo.list_non_permission()
    group_rows = 0
    for group in groups:
        group_oid = str(group["_id"])
        class_users = await users_repo.list_by_class(group["_id"])
        time_graph = await time_repo.list_by_users(
            [str(u["_id"]) for u in class_users]
        )
        if not time_graph:
            continue  # guard: legacy would have crashed on the empty frame
        df = process_time_data(pd.DataFrame(time_graph))
        if df.empty:
            continue
        percentiles = describe_df_percentile(df, list(MODES), step=1, bounds=BOUNDS)
        indicators = describe_df_statistical_indicators(df, list(MODES))
        await stats_repo.upsert_group_indicators(group_oid, percentiles, indicators)
        group_rows += 1

    # --- 4. indicators  (legacy ``compute_indicators`` global half) ------
    grades = await users_repo.group_oids_by_id_prefix()
    percentiles: dict[str, Any] = {}
    indicators: dict[str, Any] = {}
    for item in grades:
        grade = item["_id"]
        members = await time_repo.list_by_users(
            [str(member) for member in item["originalIds"]]
        )
        if not members:
            continue
        df = process_time_data(pd.DataFrame(members))
        if df.empty:
            continue
        percentiles[str(grade)] = describe_df_percentile(
            df, list(MODES), step=1, bounds=BOUNDS
        )
        indicators[str(grade)] = describe_df_statistical_indicators(df, list(MODES))
    await stats_repo.upsert_indicators(percentiles, indicators)

    # --- 5+6. time_with_origin / _stat  (legacy ``compute_batch_origins``) ---
    for item in grades:
        grade = str(item["_id"])
        for rq in REQUIRE_PERCENTAGES:
            await time_with_origin("grade", grade, rq)
    for group in groups:
        group_oid = str(group["_id"])
        for rq in REQUIRE_PERCENTAGES:
            await time_with_origin("group", group_oid, rq)

    await session.commit()

    summary = {
        "user_time": len(user_ids),
        "time_academic_year": ay_rows,
        "group_indicators": group_rows,
        "indicators": 1 if percentiles else 0,
    }
    log(f"reference build done: {summary}")
    return summary
