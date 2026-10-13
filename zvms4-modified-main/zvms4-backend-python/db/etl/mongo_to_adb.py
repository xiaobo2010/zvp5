"""One-way, idempotent ETL: MongoDB ``zvms_new`` -> Oracle ADB.

Design reference: ``docs/ADB_SCHEMA_SPEC.md`` §2 (tables), §3 (rename map),
§7 (ETL outline). Authority for column names is ``db/models.py``.

Guarantees
----------
* **Read-only, single-source Mongo side.** A private ``motor`` client bound to
  ``settings.MONGODB_URI`` and the database ``zvms_new`` **only**. The legacy
  ``zvms`` database is never opened.
* **No Oracle I/O unless ``--execute``.** Importing this module and running the
  default ``--dry-run`` never import ``db.oracle.engine``, never build the
  engine and never open a connection. The run report records the engine state
  so the claim is auditable.
* **Idempotent writes.** Two strategies, chosen per table (see
  :func:`build_specs`): ``MERGE`` (row upsert by PK) or cache replace
  (``DELETE ... IN (pks)`` + ``INSERT``). Re-running is safe either way.
* **Integrity gates are non-destructive.** Rows that trip a gate (non-hex id,
  unknown enum, duplicate ``(member, activity, mode)``, missing FK parent,
  oversize text, unserialisable JSON, wrong type) are quarantined to
  ``etl_quarantine.jsonl`` and skipped; the run keeps going. Nothing is coerced
  silently.

CLI
---
    python -m db.etl.mongo_to_adb                     # dry-run (default)
    python -m db.etl.mongo_to_adb --execute --active test
    python -m db.etl.mongo_to_adb --only users,groups --batch 250

FK-safe order
-------------
``groups -> users -> activities -> activity_members -> user_time ->
time_academic_year -> time_with_origin -> time_with_origin_stat -> indicators
-> group_indicators -> daily_exports -> changelog``. Parents are fully
transformed before children, so the FK gate validates against the set of parent
rows that will actually be written.

Importing this module performs **no I/O**.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Iterator, Optional, Sequence

from bson import ObjectId
from sqlalchemy import bindparam, text

import settings
from db import models as db_models
from db.ids import normalize_oid

DB_NAME = "zvms_new"
VALID_ACTIVE = ("prod", "test")
DEFAULT_BATCH = 500
DELETE_IN_CHUNK = 1000  # Oracle caps ``IN`` lists at 1000 literals
QUARANTINE_FILENAME = "etl_quarantine.jsonl"
REPORT_PREFIX = "etl_report_"

# ---------------------------------------------------------------------------
# Enum whitelists (spec §2). Unknown values are quarantined, never coerced.
# ---------------------------------------------------------------------------

ATYPES = frozenset({"on-campus", "off-campus", "social-practice", "hybrid"})
ACTIVITY_STATUSES = frozenset({"pending", "effective", "refused"})
MEMBER_STATUSES = frozenset({"effective", "pending", "refused"})
MEMBER_MODES = frozenset({"on-campus", "off-campus", "social-practice"})
SCOPES = frozenset({"grade", "group"})
ORIGINS = frozenset(
    {
        "labor",
        "organization",
        "tasks",
        "occasions",
        "import",
        "activities",
        "practice",
        "club",
        "prize",
        "other",
        "unknown",
    }
)

# ---------------------------------------------------------------------------
# Column kinds
# ---------------------------------------------------------------------------

OID = "oid"        # Mongo ``_id`` -> lowercase 24-hex PK
REF = "ref"        # a reference id (24-hex, nullable/required) with optional FK
STR = "str"        # VARCHAR2 text (enum + length checked)
INT = "int"        # NUMBER(k)
FLOAT = "float"    # BINARY_DOUBLE
TS = "ts"          # naive TIMESTAMP
JSON_KIND = "json"  # CLOB + IS JSON (bound as a Python object)
BOOL_KIND = "bool"  # Mongo truthiness -> NUMBER(1) 0/1

_MISSING = object()


@dataclass(frozen=True)
class ColumnSpec:
    """One target column: modelled name, Mongo source key and transform."""

    column: str
    source: str
    kind: str
    required: bool = False
    default: Any = _MISSING
    enum: Optional[frozenset] = None
    fk: Optional[str] = None  # parent TABLE name in db/models.py


@dataclass(frozen=True)
class TableSpec:
    """One collection -> table mapping, in FK-safe processing order."""

    name: str        # db/models.py table name (metadata key)
    collection: str  # Mongo collection in ``zvms_new``
    strategy: str    # "merge" | "cache"
    columns: tuple[ColumnSpec, ...]
    dedup: Optional[tuple[str, ...]] = None
    row_filter: Optional[Callable[[dict], bool]] = None


def _class_group_only(doc: dict) -> bool:
    """Keep class groups; drop ``type == "permission"`` sentinels (spec §2).

    ``GROUPS`` models class groups only (``OID``/``NAME``; the ``type:"class"``
    marker is synthesised in code, not stored) and ``OracleGroupsRepo`` treats
    every row as a class. Inserting permission groups here would surface
    "团支书 / 学生会 / 管理员" as classes, so they are excluded - as a *counted*
    filter, not a silent drop.
    """
    return doc.get("type") != "permission"


def build_specs(include_permission_groups: bool = False) -> list[TableSpec]:
    """Return the ordered table plan, wired to the ``db/models.py`` schema."""
    C = ColumnSpec
    groups_filter = None if include_permission_groups else _class_group_only
    return [
        # groups -> GROUPS : MERGE.
        #   Referenced by users.class_oid; a small parent set, so the row-level
        #   MERGE upsert keeps it idempotent without delete churn.
        TableSpec(
            "groups",
            "groups",
            "merge",
            (
                C("oid", "_id", OID, required=True),
                C("name", "name", STR, required=True),
            ),
            row_filter=groups_filter,
        ),
        # users -> USERS : MERGE.
        #   The central parent table (almost every other table FKs to it).
        TableSpec(
            "users",
            "users",
            "merge",
            (
                C("oid", "_id", OID, required=True),
                C("business_id", "id", STR, required=True),
                C("name", "name", STR, required=True),
                C("roles", "roles", JSON_KIND, default=[]),
                C("class_oid", "class_id", REF, fk="groups"),
                C("password_hash", "password", STR),
                C("session_id", "session_id", STR),
                C("sex", "sex", STR),
                C("eligibility", "eligibility", JSON_KIND),
                C("past", "past", JSON_KIND),
                C("banned", "banned", BOOL_KIND, default=False),
            ),
        ),
        # activities -> ACTIVITIES : MERGE.
        #   Referenced by activity_members; flat schema, row upsert by PK.
        TableSpec(
            "activities",
            "activities",
            "merge",
            (
                C("oid", "_id", OID, required=True),
                C("atype", "type", STR, required=True, enum=ATYPES),
                C("name", "name", STR, required=True),
                C("description", "description", STR, default=""),
                C("activity_date", "date", TS, required=True),
                C("created_at", "createdAt", TS, required=True),
                C("updated_at", "updatedAt", TS, required=True),
                C("appointee_oid", "appointee", REF, required=True, fk="users"),
                C("approver", "approver", STR, default=""),  # ""/"authority" sentinel, no FK
                C("creator_oid", "creator", REF, required=True, fk="users"),
                C("status", "status", STR, required=True, enum=ACTIVITY_STATUSES),
                C("place", "place", STR, default=""),
                C("origin", "origin", STR, required=True, enum=ORIGINS),
            ),
        ),
        # activity_members -> ACTIVITY_MEMBERS : MERGE.
        #   Carries the UQ(member, activity, mode) invariant; duplicates in the
        #   source are quarantined so the app-level uniqueness stays true.
        TableSpec(
            "activity_members",
            "activity_members",
            "merge",
            (
                C("oid", "_id", OID, required=True),
                C("activity_oid", "activity", REF, required=True, fk="activities"),
                C("member_oid", "member", REF, required=True, fk="users"),
                C("member_status", "status", STR, required=True, enum=MEMBER_STATUSES),
                C("member_mode", "mode", STR, required=True, enum=MEMBER_MODES),
                C("duration_hours", "duration", FLOAT, required=True),
            ),
            dedup=("member_oid", "activity_oid", "member_mode"),
        ),
        # time -> USER_TIME : cache replace (pure derived cache, one row/user).
        #   DELETE by PK IN (batch) then INSERT; idempotent per batch.
        TableSpec(
            "user_time",
            "time",
            "cache",
            (
                C("oid", "_id", OID, required=True),
                C("user_oid", "user", REF, required=True, fk="users"),
                C("on_campus_raw", "on_campus_raw", FLOAT, default=0),
                C("off_campus_raw", "off_campus_raw", FLOAT, default=0),
                C("social_practice", "social_practice", FLOAT, default=0),
                C("updated_at", "updated_at", TS, required=True),
            ),
        ),
        # time_academic_year : cache replace (pure derived cache).
        #   Kebab Mongo keys on-campus/off-campus/social-practice -> snake.
        TableSpec(
            "time_academic_year",
            "time_academic_year",
            "cache",
            (
                C("oid", "_id", OID, required=True),
                C("user_oid", "user", REF, required=True, fk="users"),
                C("entry_year", "entry", STR, required=True),
                C("academic_year", "academic_year", INT, required=True),
                C("start_of_year", "start_of_year", TS, required=True),
                C("end_of_year", "end_of_year", TS, required=True),
                C("on_campus", "on-campus", FLOAT, required=True),
                C("off_campus", "off-campus", FLOAT, required=True),
                C("social_practice", "social-practice", FLOAT, required=True),
            ),
        ),
        # time_with_origin : cache replace (pure derived cache, one row/user).
        TableSpec(
            "time_with_origin",
            "time_with_origin",
            "cache",
            (
                C("oid", "_id", OID, required=True),
                C("user_oid", "user", REF, required=True, fk="users"),
                C("data", "data", JSON_KIND, required=True),
                C("updated_at", "updated_at", TS, required=True),
            ),
        ),
        # time_with_origin_stat : MERGE.
        #   Derived cache keyed by the UNIQUE stat_key; nothing references it,
        #   but MERGE by PK keeps the natural key idempotent on re-run.
        TableSpec(
            "time_with_origin_stat",
            "time_with_origin_stat",
            "merge",
            (
                C("oid", "_id", OID, required=True),
                C("stat_key", "id", STR, required=True),
                C("scope", "scope", STR, required=True, enum=SCOPES),
                C("scope_id", "scope_id", STR, required=True),  # polymorphic, no FK
                C("require_percentage", "require_percentage", INT, default=0),
                C("data", "data", JSON_KIND, required=True),
                C("updated_at", "updated_at", TS, required=True),
            ),
        ),
        # indicators : cache replace (single-row global cache).
        TableSpec(
            "indicators",
            "indicators",
            "cache",
            (
                C("oid", "_id", OID, required=True),
                C("percentiles", "percentiles", JSON_KIND),
                C("indicators", "indicators", JSON_KIND),
                C("updated_at", "updated_at", TS),
            ),
        ),
        # group_indicators : cache replace (per-group cache; FK deferred §7).
        TableSpec(
            "group_indicators",
            "group_indicators",
            "cache",
            (
                C("oid", "_id", OID, required=True),
                C("group_oid", "group", STR, required=True),
                C("percentiles", "percentiles", JSON_KIND),
                C("indicators", "indicators", JSON_KIND),
                C("updated_at", "updated_at", TS),
            ),
        ),
        # daily_exports : cache replace (append-only export snapshot cache).
        TableSpec(
            "daily_exports",
            "daily_exports",
            "cache",
            (
                C("oid", "_id", OID, required=True),
                C("created_at", "created_at", TS, required=True),
                C("status", "status", STR, required=True),
                C("data", "data", JSON_KIND, default=[]),
            ),
        ),
        # changelog -> CHANGELOG : MERGE.
        #   Release metadata (`items` is a JSON list); nothing FKs to it and the
        #   set is small, so the row upsert by PK keeps it idempotent.
        TableSpec(
            "changelog",
            "changelog",
            "merge",
            (
                C("oid", "_id", OID, required=True),
                C("version", "version", STR, required=True),
                C("release_date", "date", STR, required=True),
                C("items", "items", JSON_KIND, default=[]),
                C("updated_at", "updated_at", TS, required=True),
            ),
        ),
    ]


# Collections present in the single-db world that are intentionally NOT moved
# this round, each with a clear reason (spec §2/§7/§10).
SKIPPED_COLLECTIONS: dict[str, str] = {
    "logs": "table modelled but ETL deferred - dead device schema (spec §2/§7)",
    "app_logs": "no table in db/models.py - runtime app logs (spec §10, pending)",
    "tasks": "no table in db/models.py",
    "imports": "no table in db/models.py",
    "notifications": "no table in db/models.py",
}


# ---------------------------------------------------------------------------
# Value coercion
# ---------------------------------------------------------------------------

def _short(value: Any, limit: int = 60) -> str:
    text = repr(value)
    return text if len(text) <= limit else text[:limit] + "..."


def _as_ref(value: Any) -> Optional[str]:
    """Return a lowercase 24-hex reference id, or ``None`` if not an id."""
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, str):
        stripped = value.strip()
        if stripped == "":
            return None
        try:
            return normalize_oid(stripped)
        except ValueError:
            return None
    return None


def _convert(
    cs: ColumnSpec,
    value: Any,
    spec: TableSpec,
    ctx: "_Ctx",
    reasons: list[str],
) -> Any:
    """Convert one non-null value; append a reason and return ``None`` on fail."""
    kind = cs.kind

    if kind == REF:
        if isinstance(value, str) and value.strip() == "":
            if cs.required:
                reasons.append(f"empty_reference:{cs.column}")
            return None
        out = _as_ref(value)
        if out is None:
            reasons.append(f"non_hex_id:{cs.column}={_short(value)}")
            return None
        if cs.fk:
            if cs.fk in ctx.selected:
                if out not in ctx.parent_sets.get(cs.fk, ()):  # type: ignore[arg-type]
                    reasons.append(f"missing_fk_parent:{cs.column}={out}->{cs.fk}")
            else:
                ctx.warn(
                    f"fk_unverified: {spec.name}.{cs.column} -> {cs.fk} "
                    "(parent table not selected)"
                )
        return out

    if kind == STR:
        if isinstance(value, bool) or not isinstance(value, (str, int, float)):
            reasons.append(f"bad_type:{cs.column}={type(value).__name__}")
            return None
        text = value if isinstance(value, str) else str(value)
        if cs.enum is not None and text not in cs.enum:
            reasons.append(f"unknown_enum:{cs.column}={text!r}")
            return None
        limit = ctx.max_len.get(cs.column)
        if limit is not None and len(text) > limit:
            reasons.append(f"oversize_text:{cs.column}(len={len(text)}>{limit})")
            return None
        return text

    if kind == INT:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            reasons.append(f"bad_type:{cs.column}={type(value).__name__}")
            return None
        if isinstance(value, float) and not value.is_integer():
            reasons.append(f"bad_type:{cs.column}={value!r}")
            return None
        number = int(value)
        digits = ctx.num_digits.get(cs.column)
        if digits is not None and abs(number) >= 10 ** digits:
            reasons.append(f"out_of_range:{cs.column}={number}")
            return None
        return number

    if kind == FLOAT:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            reasons.append(f"bad_type:{cs.column}={type(value).__name__}")
            return None
        return float(value)

    if kind == TS:
        if not isinstance(value, datetime):
            reasons.append(f"bad_type:{cs.column}={type(value).__name__}")
            return None
        # No timezone maths: BSON dates arrive naive (tz_aware=False); a rare
        # aware value just has its tzinfo dropped so the TIMESTAMP stays naive.
        return value.replace(tzinfo=None) if value.tzinfo is not None else value

    if kind == JSON_KIND:
        if isinstance(value, str):
            try:
                json.loads(value)
            except ValueError:
                reasons.append(f"bad_json_text:{cs.column}")
                return None
            return value
        try:
            json.dumps(value, ensure_ascii=False)
        except (TypeError, ValueError):
            reasons.append(f"unserializable_json:{cs.column}")
            return None
        return value

    if kind == BOOL_KIND:
        return 1 if value else 0

    reasons.append(f"unknown_kind:{cs.column}={kind}")
    return None


def _transform(
    doc: dict,
    spec: TableSpec,
    table: Any,
    ctx: "_Ctx",
) -> tuple[Optional[dict], list[str]]:
    """Transform one Mongo document into an Oracle-column-keyed row."""
    reasons: list[str] = []
    row: dict[str, Any] = {}

    first = spec.columns[0]
    oid = _as_ref(doc.get(first.source))
    if oid is None:
        return None, [f"non_hex_id:oid={_short(doc.get(first.source))}"]
    row["oid"] = oid

    for cs in spec.columns[1:]:
        value = doc.get(cs.source)
        if value is None:
            if cs.default is not _MISSING:
                value = copy.deepcopy(cs.default)
            elif cs.required:
                reasons.append(f"missing_field:{cs.column}")
                continue
            else:
                row[cs.column] = None
                continue
        row[cs.column] = _convert(cs, value, spec, ctx, reasons)

    if reasons:
        return None, reasons

    if spec.dedup is not None:
        key = tuple(row[c] for c in spec.dedup)
        seen = ctx.dedup.setdefault(spec.name, set())
        if key in seen:
            return None, [f"duplicate_key:{'+'.join(spec.dedup)}"]
        seen.add(key)

    return row, []


# ---------------------------------------------------------------------------
# Table constraints (derived from db/models.py - the schema authority)
# ---------------------------------------------------------------------------

def _constraints(table: Any) -> tuple[dict[str, int], dict[str, int]]:
    """Return ``(max_length, number_digits)`` keyed by column name."""
    max_len: dict[str, int] = {}
    num_digits: dict[str, int] = {}
    for col in table.columns:
        length = getattr(col.type, "length", None)
        if isinstance(length, int):
            max_len[col.name] = length
        precision = getattr(col.type, "precision", None)
        if isinstance(precision, int):
            num_digits[col.name] = precision
    return max_len, num_digits


# ---------------------------------------------------------------------------
# Write strategies
# ---------------------------------------------------------------------------

def _chunks(items: Sequence[Any], size: int) -> Iterator[Sequence[Any]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def _merge_statement(table: Any):
    """Build ``MERGE INTO <t> USING (SELECT :binds FROM dual)`` for row upsert.

    Bind parameters carry the modelled ``Column.type`` so the SQLAlchemy type
    decorators run on the way in - notably :class:`db.models.JSONText`, which
    turns dict/list Python objects into CLOB JSON text.
    """
    names = [c.name for c in table.columns]
    pk = [c.name for c in table.primary_key.columns]
    binds = [bindparam(n, type_=table.c[n].type) for n in names]
    using = ", ".join(f":{n} AS {n}" for n in names)
    on_clause = " AND ".join(f"t.{n} = s.{n}" for n in pk)
    non_pk = [n for n in names if n not in pk]
    matched = ", ".join(f"t.{n} = s.{n}" for n in non_pk)
    insert_cols = ", ".join(names)
    insert_vals = ", ".join(f"s.{n}" for n in names)
    sql = (
        f"MERGE INTO {table.name} t "
        f"USING (SELECT {using} FROM dual) s "
        f"ON ({on_clause}) "
        f"WHEN MATCHED THEN UPDATE SET {matched} "
        f"WHEN NOT MATCHED THEN INSERT ({insert_cols}) VALUES ({insert_vals})"
    )
    return text(sql).bindparams(*binds)


async def _write_batch(session: Any, spec: TableSpec, table: Any, rows: list[dict]) -> None:
    """Write one transformed batch; strategy chosen per table in build_specs."""
    if spec.strategy == "cache":
        oids = [row["oid"] for row in rows]
        for chunk in _chunks(oids, DELETE_IN_CHUNK):
            await session.execute(table.delete().where(table.c.oid.in_(list(chunk))))
        # Core insert -> executemany; JSONText serialises Python objects.
        await session.execute(table.insert(), rows)
    else:
        # Raw MERGE -> executemany; typed binds apply JSONText etc.
        await session.execute(_merge_statement(table), rows)
    await session.commit()


# ---------------------------------------------------------------------------
# Run context / reporting
# ---------------------------------------------------------------------------

@dataclass
class _Ctx:
    args: argparse.Namespace
    selected: set[str]
    parent_sets: dict[str, set[str]] = field(default_factory=dict)
    dedup: dict[str, set] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    max_len: dict[str, int] = field(default_factory=dict)
    num_digits: dict[str, int] = field(default_factory=dict)
    quarantine_fh: Any = None
    quarantine_count: int = 0

    def warn(self, message: str) -> None:
        if message not in self.warnings:
            self.warnings.append(message)

    def record_quarantine(self, spec: TableSpec, doc: dict, reasons: list[str]) -> None:
        record = {
            "table": spec.name,
            "collection": spec.collection,
            "oid": _short(doc.get("_id"), 40),
            "reasons": reasons,
        }
        self.quarantine_fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        self.quarantine_count += 1


def _new_entry(spec: TableSpec) -> dict:
    return {
        "collection": spec.collection,
        "strategy": spec.strategy,
        "read": 0,
        "accepted": 0,
        "filtered": 0,
        "quarantined": 0,
        "written": 0,
        "would_write": 0,
        "by_reason": {},
    }


async def _process_table(
    collection: Any,
    spec: TableSpec,
    table: Any,
    ctx: _Ctx,
    session: Any,
    entry: dict,
) -> None:
    cursor = collection.find({}).batch_size(ctx.args.batch)
    batch: list[dict] = []

    async for doc in cursor:
        entry["read"] += 1
        if spec.row_filter is not None and not spec.row_filter(doc):
            entry["filtered"] += 1
            continue

        row, reasons = _transform(doc, spec, table, ctx)
        if row is None:
            entry["quarantined"] += 1
            for reason in reasons:
                code = reason.split(":", 1)[0]
                entry["by_reason"][code] = entry["by_reason"].get(code, 0) + 1
            ctx.record_quarantine(spec, doc, reasons)
            continue

        entry["accepted"] += 1
        ctx.parent_sets.setdefault(spec.name, set()).add(row["oid"])
        batch.append(row)
        if len(batch) >= ctx.args.batch:
            await _flush(session, spec, table, batch, entry)
            batch = []

    if batch:
        await _flush(session, spec, table, batch, entry)


async def _flush(session: Any, spec: TableSpec, table: Any, rows: list[dict], entry: dict) -> None:
    if session is None:
        entry["would_write"] += len(rows)
        return
    await _write_batch(session, spec, table, rows)
    entry["written"] += len(rows)


def select_specs(only: Optional[Sequence[str]], include_permission_groups: bool = False) -> list[TableSpec]:
    """Filter the plan by ``--only`` (collection or modelled table name)."""
    specs = build_specs(include_permission_groups)
    if not only:
        return specs
    wanted = {item.strip() for item in only if item.strip()}
    known: dict[str, TableSpec] = {}
    for spec in specs:
        known[spec.name] = spec
        known[spec.collection] = spec
    unknown = sorted(item for item in wanted if item not in known)
    if unknown:
        valid = ", ".join(spec.collection for spec in specs)
        raise SystemExit(f"unknown --only target(s): {', '.join(unknown)}; valid: {valid}")
    return [spec for spec in specs if spec.name in wanted or spec.collection in wanted]


def _resolve_active(args: argparse.Namespace) -> str:
    """Resolve the ADB instance name the way ``db.oracle.config`` does."""
    active = (args.active or getattr(settings, "ORACLE_ACTIVE", "prod") or "prod")
    active = str(active).strip().lower()
    return active if active in VALID_ACTIVE else "prod"


def _oracle_engine_state() -> str:
    """Report whether the Oracle engine module/engine was ever touched."""
    module = sys.modules.get("db.oracle.engine")
    if module is None:
        return "not-imported"
    return "created" if getattr(module, "_engine", None) is not None else "imported-not-created"


async def run(args: argparse.Namespace) -> int:
    started = datetime.now()
    specs = select_specs(args.only, args.include_permission_groups)
    selected = {spec.name for spec in specs}
    ctx = _Ctx(args=args, selected=selected)
    active = _resolve_active(args)

    report: dict = {
        "generated_at": started.isoformat(timespec="seconds"),
        "mode": "execute" if args.execute else "dry-run",
        "active_instance": active if args.execute else None,
        "database": DB_NAME,
        "batch": args.batch,
        "only": args.only,
        "plan": [spec.collection for spec in specs],
        "skipped": dict(SKIPPED_COLLECTIONS),
        "tables": {},
        "totals": {},
        "warnings": [],
        "oracle_connections": 0,
    }

    print(f"zvms_new -> ADB ETL  [{'EXECUTE' if args.execute else 'DRY-RUN'}]  batch={args.batch}")
    print(f"plan   : {' -> '.join(spec.collection for spec in specs)}")
    for name, reason in SKIPPED_COLLECTIONS.items():
        print(f"skipped: {name} ({reason})")

    # Imported lazily: keeping motor out of the module import path is part of
    # the "no I/O on import / no Oracle on dry-run" guarantee.
    from motor.motor_asyncio import AsyncIOMotorClient

    client = AsyncIOMotorClient(settings.MONGODB_URI, tz_aware=False)
    mongo_db = client[DB_NAME]

    quarantine_fh = open(QUARANTINE_FILENAME, "w", encoding="utf-8")
    ctx.quarantine_fh = quarantine_fh

    session = None
    original_active = getattr(settings, "ORACLE_ACTIVE", None)
    try:
        if args.execute:
            # Mutate the runtime attribute (never the file) so db.oracle.engine
            # resolves the requested wallet/DSN via db.oracle.config.
            settings.ORACLE_ACTIVE = active
            from db.oracle import session as oracle_session

            session = oracle_session.get_sessionmaker()()

        for spec in specs:
            table = db_models.get_table(spec.name)
            ctx.max_len, ctx.num_digits = _constraints(table)
            entry = report["tables"][spec.name] = _new_entry(spec)
            await _process_table(
                mongo_db.get_collection(spec.collection),
                spec,
                table,
                ctx,
                session,
                entry,
            )
    finally:
        if session is not None:
            await session.close()
        if args.execute and original_active is not None:
            settings.ORACLE_ACTIVE = original_active
        quarantine_fh.close()
        client.close()

    # Totals + derived report fields.
    totals = {"read": 0, "accepted": 0, "filtered": 0, "quarantined": 0, "written": 0, "would_write": 0}
    for entry in report["tables"].values():
        for key in totals:
            totals[key] += entry[key]
    report["totals"] = totals
    report["quarantine_count"] = ctx.quarantine_count
    report["quarantine_file"] = QUARANTINE_FILENAME
    report["warnings"] = ctx.warnings
    report["oracle_engine"] = _oracle_engine_state()

    _print_summary(report)

    stamp = started.strftime("%Y%m%dT%H%M%S")
    report_path = f"{REPORT_PREFIX}{stamp}.json"
    with open(report_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
    print(f"report : {report_path}")
    print(f"quarantine: {QUARANTINE_FILENAME} ({ctx.quarantine_count} rows)")
    if args.execute:
        print(f"oracle : engine={report['oracle_engine']} active={active}")
    else:
        print(f"oracle : no connection attempted (dry-run); engine={report['oracle_engine']}")
    return 0


def _print_summary(report: dict) -> None:
    header = f"{'table':<20} {'strategy':<8} {'read':>6} {'ok':>6} {'filt':>5} {'quar':>6} {'write':>6}"
    print()
    print(header)
    print("-" * len(header))
    write_key = "written" if report["mode"] == "execute" else "would_write"
    for name, entry in report["tables"].items():
        print(
            f"{name:<20} {entry['strategy']:<8} "
            f"{entry['read']:>6} {entry['accepted']:>6} {entry['filtered']:>5} "
            f"{entry['quarantined']:>6} {entry[write_key]:>6}"
        )
    totals = report["totals"]
    print("-" * len(header))
    print(
        f"{'TOTAL':<20} {'':<8} "
        f"{totals['read']:>6} {totals['accepted']:>6} {totals['filtered']:>5} "
        f"{totals['quarantined']:>6} {totals[write_key]:>6}"
    )
    reasons: dict[str, int] = {}
    for entry in report["tables"].values():
        for code, count in entry["by_reason"].items():
            reasons[code] = reasons.get(code, 0) + count
    if reasons:
        print("quarantine reasons: " + ", ".join(f"{k}={v}" for k, v in sorted(reasons.items())))
    if report["warnings"]:
        print("warnings:")
        for warning in report["warnings"]:
            print(f"  - {warning}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m db.etl.mongo_to_adb",
        description=(
            "One-way, idempotent ETL from MongoDB zvms_new to Oracle ADB. "
            "Defaults to --dry-run (read + transform + report only, no Oracle)."
        ),
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="read + transform + report only; never connects to Oracle (DEFAULT)",
    )
    mode.add_argument(
        "--execute",
        action="store_true",
        help="actually write to Oracle ADB (required to leave dry-run)",
    )
    parser.add_argument(
        "--only",
        default=None,
        help="comma-separated collection filter (e.g. users,groups,time)",
    )
    parser.add_argument(
        "--batch",
        type=int,
        default=DEFAULT_BATCH,
        help=f"rows per read/write batch (default {DEFAULT_BATCH})",
    )
    parser.add_argument(
        "--active",
        choices=VALID_ACTIVE,
        default=None,
        help="ADB instance to target under --execute (default: settings.ORACLE_ACTIVE)",
    )
    parser.add_argument(
        "--include-permission-groups",
        action="store_true",
        help=(
            "also ETL zvms_new.groups rows with type=='permission' (default: "
            "excluded, because GROUPS models class groups only)"
        ),
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.batch is not None and args.batch < 1:
        parser.error("--batch must be >= 1")
    args.execute = bool(args.execute)
    args.dry_run = not args.execute
    args.only = (
        [item.strip() for item in args.only.split(",") if item.strip()]
        if args.only
        else None
    )
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
