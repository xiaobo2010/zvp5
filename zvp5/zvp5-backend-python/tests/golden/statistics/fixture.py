"""Fixture capture / load for the statistics golden harness.

Two halves:

* :func:`capture` — **read-only**. Samples a representative slice out of the
  currently-connected schema (users + their classes, all of their
  ``activity_members`` rows regardless of status, the referenced activities plus
  a few unreferenced *effective* ones) and desensitises it:

  - ``users.name`` is never read into the fixture;
  - ``business_id`` becomes ``<first 4 chars kept><6 random digits>`` (the
    grade prefix the statistics SQL keys on is preserved, the real 学号 is not);
  - ObjectIds are kept as-is (24-hex, no PII).

* :func:`load` — bulk-inserts a fixture into **whatever schema the given session
  is connected to** (the scratch schema under tier-2). The frozen fixture shape
  carries only the fields the statistics domain reads, so ``load`` synthesises
  the remaining NOT-NULL columns (``users.name``, ``activities.atype``/``name``/
  ``creator_oid``/``appointee_oid``/timestamps, ``activity_members.oid``).

Frozen fixture JSON shape (do not change)::

    {
      "users":            [{"oid", "business_id", "class_oid"(nullable)}],
      "groups":           [{"oid", "name"}],
      "activities":       [{"oid", "status", "activity_date"(ISO), "origin"}],
      "activity_members": [{"activity_oid", "member_oid", "member_status",
                            "member_mode", "duration_hours"}]
    }

Boundary coverage the sampler tries to guarantee (warned, not raised, when the
source schema cannot supply it): zero-time users, ``class_oid IS NULL`` users,
activities with no members, and ≥2 distinct activity origins.

Importing this module performs no I/O; only the async functions touch the DB.
"""

from __future__ import annotations

import json
import logging
import random
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import insert

from db import backends
from db.ids import new_oid
from db.models import activities as activities_tbl
from db.models import activity_members as members_tbl
from db.models import groups as groups_tbl
from db.models import users as users_tbl
from db.oracle.session import session_scope

log = logging.getLogger("statistics.fixture")

HERE = Path(__file__).resolve().parent
FIXTURE_DIR = HERE / "fixtures"
FIXTURE_PATH = FIXTURE_DIR / "fixture.json"

#: Sampling defaults — 3 grades x up to 4 classes, 60..120 users.
DEFAULTS: dict[str, int] = {
    "max_users": 120,
    "min_users": 60,
    "no_class_users": 6,
    "grades": 3,
    "classes_per_grade": 4,
    "extra_activities": 5,
}


# ---------------------------------------------------------------------------
# capture
# ---------------------------------------------------------------------------

def _class_of(user: dict[str, Any]) -> str | None:
    """``users.class_id`` (only present when non-NULL in the repo shape)."""
    return user.get("class_id") or user.get("class_oid") or None


def _grade_of(user: dict[str, Any]) -> str:
    return str(user.get("id") or "")[:4]


def _desensitise_business_id(prefix: str, seen: set[str], rng: random.Random) -> str:
    """``<prefix><6 random digits>``, unique within the fixture."""
    while True:
        candidate = f"{prefix}{rng.randint(0, 999999):06d}"
        if candidate not in seen:
            seen.add(candidate)
            return candidate


async def capture(
    session: Any = None,
    *,
    seed: int | None = None,
    **options: int,
) -> dict[str, Any]:
    """Read-only slice capture; opens its own session when none is given."""
    opts = {**DEFAULTS, **options}
    if session is not None:
        return await _capture(session, seed=seed, **opts)
    async with session_scope() as owned:
        return await _capture(owned, seed=seed, **opts)


async def _capture(
    session: Any,
    *,
    seed: int | None = None,
    max_users: int = 120,
    min_users: int = 60,
    no_class_users: int = 6,
    grades: int = 3,
    classes_per_grade: int = 4,
    extra_activities: int = 5,
) -> dict[str, Any]:
    rng = random.Random(seed)
    users_repo = backends.get_users_repo(session)
    groups_repo = backends.get_groups_repo(session)
    activities_repo = backends.get_activities_repo(session)
    members_repo = backends.get_members_repo(session)

    users = await users_repo.list_all()
    groups = await groups_repo.list_non_permission()
    group_oid_set = {str(g["_id"]) for g in groups}

    # class -> predominant grade, and class -> size.
    class_grade: dict[str, Counter] = defaultdict(Counter)
    class_size: Counter = Counter()
    for user in users:
        class_oid = _class_of(user)
        if not class_oid or class_oid not in group_oid_set:
            continue
        class_grade[class_oid][_grade_of(user)] += 1
        class_size[class_oid] += 1

    grade_classes: dict[str, list[str]] = defaultdict(list)
    for class_oid, counter in class_grade.items():
        grade_classes[counter.most_common(1)[0][0]].append(class_oid)

    chosen_grades = sorted(
        grade_classes,
        key=lambda g: (-sum(class_size[c] for c in grade_classes[g]), g),
    )[:grades]

    chosen_classes: list[str] = []
    for grade in chosen_grades:
        ranked = sorted(grade_classes[grade], key=lambda c: (-class_size[c], c))
        chosen_classes.extend(ranked[:classes_per_grade])
    chosen_set = set(chosen_classes)

    class_users = sorted(
        (u for u in users if _class_of(u) in chosen_set),
        key=lambda u: str(u["_id"]),
    )
    no_class_pool = sorted(
        (u for u in users if _class_of(u) is None),
        key=lambda u: str(u["_id"]),
    )

    budget = max(max_users - min(no_class_users, len(no_class_pool)), 0)
    selected = class_users[:budget] + no_class_pool[:no_class_users]

    # Top up to min_users from any remaining class users.
    if len(selected) < min_users:
        seen_oids = {str(u["_id"]) for u in selected}
        for user in class_users[budget:] + no_class_pool[no_class_users:]:
            if str(user["_id"]) in seen_oids:
                continue
            selected.append(user)
            seen_oids.add(str(user["_id"]))
            if len(selected) >= min_users:
                break

    # ------------------------------------------------------------------
    # members + activities
    # ------------------------------------------------------------------
    selected_oids = [str(u["_id"]) for u in selected]
    member_rows = await members_repo.list_by_members(selected_oids) if selected_oids else []

    referenced_activity_oids = sorted({str(m["activity"]) for m in member_rows})
    activities_by_oid: dict[str, dict[str, Any]] = {}
    for activity_oid in referenced_activity_oids:
        doc = await activities_repo.get(activity_oid)
        if doc is not None:
            activities_by_oid[activity_oid] = doc

    # Unreferenced effective activities -> exercises the "activity with no
    # members" branch; prefer distinct origins so multi-origin coverage holds.
    effective = await activities_repo.list_effective()
    extras: list[dict[str, Any]] = []
    origins_seen = {a.get("origin") for a in activities_by_oid.values()}
    for activity in sorted(effective, key=lambda a: str(a["_id"])):
        if str(activity["_id"]) in activities_by_oid:
            continue
        if len(extras) >= extra_activities:
            break
        extras.append(activity)
        origins_seen.add(activity.get("origin"))
    for activity in extras:
        activities_by_oid[str(activity["_id"])] = activity

    activity_oid_set = set(activities_by_oid)

    # ------------------------------------------------------------------
    # build + desensitise
    # ------------------------------------------------------------------
    seen_business_ids: set[str] = set()
    fixture_users: list[dict[str, Any]] = []
    for user in selected:
        real_id = str(user.get("id") or "")
        fixture_users.append(
            {
                "oid": str(user["_id"]),
                "business_id": _desensitise_business_id(
                    real_id[:4], seen_business_ids, rng
                ),
                "class_oid": _class_of(user),
            }
        )

    fixture_groups = [
        {"oid": str(g["_id"]), "name": g["name"]}
        for g in groups
        if str(g["_id"]) in chosen_set
    ]

    fixture_activities = [
        {
            "oid": oid,
            "status": doc["status"],
            "activity_date": doc["date"].isoformat(),
            "origin": doc["origin"],
        }
        for oid, doc in sorted(activities_by_oid.items())
    ]

    fixture_members = [
        {
            "activity_oid": str(m["activity"]),
            "member_oid": str(m["member"]),
            "member_status": m["status"],
            "member_mode": m["mode"],
            "duration_hours": float(m["duration"]),
        }
        for m in member_rows
        if str(m["activity"]) in activity_oid_set
    ]

    fixture = {
        "users": fixture_users,
        "groups": fixture_groups,
        "activities": fixture_activities,
        "activity_members": fixture_members,
    }

    _report_coverage(fixture, referenced_activity_oids)
    return fixture


def _report_coverage(fixture: dict[str, Any], referenced_activity_oids: list[str]) -> None:
    users = fixture["users"]
    members = fixture["activity_members"]
    effective_users = {
        m["member_oid"]
        for m in members
        if m["member_status"] == "effective"
    }
    zero_time = [u for u in users if u["oid"] not in effective_users]
    no_class = [u for u in users if u["class_oid"] is None]
    referenced = set(referenced_activity_oids)
    no_member = [a for a in fixture["activities"] if a["oid"] not in referenced]
    origins = {a["origin"] for a in fixture["activities"]}
    log.info(
        "fixture: users=%d groups=%d activities=%d members=%d "
        "(zero-time=%d no-class=%d no-member-activities=%d origins=%d)",
        len(users),
        len(fixture["groups"]),
        len(fixture["activities"]),
        len(members),
        len(zero_time),
        len(no_class),
        len(no_member),
        len(origins),
    )
    for label, present in (
        ("zero-time users", bool(zero_time)),
        ("class-less users", bool(no_class)),
        ("member-less activities", bool(no_member)),
        ("multiple origins", len(origins) >= 2),
    ):
        if not present:
            log.warning("fixture boundary not covered by the source schema: %s", label)


# ---------------------------------------------------------------------------
# load
# ---------------------------------------------------------------------------

def _parse_ts(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


async def load(session: Any, fixture: dict[str, Any]) -> dict[str, int]:
    """Bulk-insert ``fixture`` into the schema ``session`` is connected to.

    Commits exactly once at the end. Returns the per-table row counts.
    """
    groups = fixture.get("groups", [])
    users = fixture.get("users", [])
    activities = fixture.get("activities", [])
    members = fixture.get("activity_members", [])

    creator = users[0]["oid"] if users else "0" * 24

    if groups:
        await session.execute(
            insert(groups_tbl),
            [{"oid": g["oid"], "name": g["name"]} for g in groups],
        )
    if users:
        await session.execute(
            insert(users_tbl),
            [
                {
                    "oid": u["oid"],
                    "business_id": u["business_id"],
                    "name": f"u_{u['oid'][:8]}",
                    "roles": [],
                    "class_oid": u["class_oid"],
                    "banned": 0,
                }
                for u in users
            ],
        )
    if activities:
        await session.execute(
            insert(activities_tbl),
            [
                {
                    "oid": a["oid"],
                    "atype": "hybrid",
                    "name": f"a_{a['oid'][:8]}",
                    "activity_date": _parse_ts(a["activity_date"]),
                    "created_at": _parse_ts(a["activity_date"]),
                    "updated_at": _parse_ts(a["activity_date"]),
                    "appointee_oid": creator,
                    "creator_oid": creator,
                    "status": a["status"],
                    "origin": a["origin"],
                }
                for a in activities
            ],
        )
    if members:
        await session.execute(
            insert(members_tbl),
            [
                {
                    "oid": new_oid(),
                    "activity_oid": m["activity_oid"],
                    "member_oid": m["member_oid"],
                    "member_status": m["member_status"],
                    "member_mode": m["member_mode"],
                    "duration_hours": float(m["duration_hours"]),
                }
                for m in members
            ],
        )
    await session.commit()
    return {
        "groups": len(groups),
        "users": len(users),
        "activities": len(activities),
        "activity_members": len(members),
    }


# ---------------------------------------------------------------------------
# json helpers
# ---------------------------------------------------------------------------

def write_fixture(fixture: dict[str, Any], path: Path | str = FIXTURE_PATH) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(fixture, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return target


def read_fixture(path: Path | str = FIXTURE_PATH) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))
