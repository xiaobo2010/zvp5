"""SQLAlchemy 2.0 Core mirror of the Oracle ADB schema (``V001__initial_schema.sql``).

This module is the Python-side ``MetaData`` mirror of the hand-written DDL in
``db/oracle/ddl/V001__initial_schema.sql``. It is used by ``db.ddl_check`` to
detect drift between the two, and will later be reused by the repository layer.

Design notes (see ``docs/ADB_SCHEMA_SPEC.md`` §0–§3):

* **SQLAlchemy 2.0.x Core only** — module-level :class:`~sqlalchemy.MetaData`
  with one :class:`~sqlalchemy.Table` per physical table. No ORM classes.
* ``_id`` is stored as ``VARCHAR2(24)`` lowercase hex in a column named
  ``OID``, guarded by a ``REGEXP_LIKE`` check constraint.
* JSON is modelled as ``CLOB`` + ``CHECK (col IS JSON)`` because SQLAlchemy 2.0
  cannot render an Oracle JSON type. The :class:`JSONText` TypeDecorator adds
  json.dumps/json.loads on the bind/result boundary.
* Times are **naive** ``TIMESTAMP`` (no time zone). Durations/percentages use
  ``BINARY_DOUBLE`` to preserve Mongo double fidelity.
* Oracle identifiers are unquoted snake_case ASCII and fold to upper case; the
  strings below (all lower case) are intentionally unambiguous.

Importing this module performs **no I/O**.
"""

from __future__ import annotations

import json
from typing import Any, Type

from sqlalchemy import (
    CheckConstraint,
    Column,
    ForeignKeyConstraint,
    Index,
    MetaData,
    Table,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects import oracle
from sqlalchemy.dialects.oracle import BINARY_DOUBLE, BLOB, CLOB, NUMBER, VARCHAR2
from sqlalchemy.types import TypeDecorator

__all__ = ["metadata", "JSONText", "get_table", "table_names"]

# ---------------------------------------------------------------------------
# JSON columns: CLOB + IS JSON check (SQLAlchemy 2.0 has no Oracle JSON type).
# ---------------------------------------------------------------------------

OID_REGEX = "^[0-9a-f]{24}$"


class JSONText(TypeDecorator):
    """Store JSON as an Oracle ``CLOB`` with ``json.dumps`` / ``json.loads``.

    ``impl`` is the Oracle ``CLOB`` type so DDL renders as ``CLOB``. The
    matching ``IS JSON`` check constraint is added explicitly on each JSON
    column (see the ``_json`` helper below) because a TypeDecorator cannot
    carry a DDL-level check constraint.
    """

    impl = oracle.CLOB
    cache_ok = True

    def process_bind_param(self, value: Any, dialect) -> Any:
        if value is None:
            return None
        if isinstance(value, str):
            return value
        return json.dumps(value, ensure_ascii=False)

    def process_result_value(self, value: Any, dialect) -> Any:
        if value is None:
            return None
        if isinstance(value, str):
            return json.loads(value)
        return value


metadata = MetaData()

# Reusable column type aliases.
_oid = VARCHAR2(24)
_int18 = NUMBER(18)
_int4 = NUMBER(4)
_int3 = NUMBER(3)
_hours = BINARY_DOUBLE
_ts = oracle.TIMESTAMP


def _oid_pk() -> Column:
    return Column("oid", _oid, primary_key=True)


def _oid_check(table_name: str) -> CheckConstraint:
    return CheckConstraint(
        f"REGEXP_LIKE(oid,'{OID_REGEX}')",
        name=f"ck_{table_name}_oid",
    )


def _json(table_name: str, column: str, *, nullable: bool = True,
          default: str | None = None) -> Column:
    return Column(
        column,
        JSONText(),
        nullable=nullable,
        server_default=(text(default) if default is not None else None),
    )


def _json_check(table_name: str, column: str) -> CheckConstraint:
    return CheckConstraint(f"{column} IS JSON", name=f"ck_{table_name}_{column}_json")


# ---------------------------------------------------------------------------
# USERS
# ---------------------------------------------------------------------------

users = Table(
    "users",
    metadata,
    _oid_pk(),
    Column("business_id", VARCHAR2(32), nullable=False),
    Column("name", VARCHAR2(64), nullable=False),
    _json("users", "roles", nullable=False, default="'[]'"),
    Column("class_oid", VARCHAR2(24), nullable=True),
    Column("password_hash", VARCHAR2(100), nullable=True),
    Column("session_id", VARCHAR2(64), nullable=True),
    Column("sex", VARCHAR2(16), nullable=True),
    _json("users", "eligibility", nullable=True),
    _json("users", "past", nullable=True),
    Column("banned", NUMBER(1), nullable=False, server_default=text("0")),
    _oid_check("users"),
    _json_check("users", "roles"),
    _json_check("users", "eligibility"),
    _json_check("users", "past"),
    CheckConstraint("banned IN (0,1)", name="ck_users_banned"),
    UniqueConstraint("business_id", name="uq_users_business_id"),
    ForeignKeyConstraint(["class_oid"], ["groups.oid"], name="fk_users_class_oid"),
)
Index("ix_users_class_oid", users.c.class_oid)


# ---------------------------------------------------------------------------
# GROUPS
# ---------------------------------------------------------------------------

groups = Table(
    "groups",
    metadata,
    _oid_pk(),
    Column("name", VARCHAR2(128), nullable=False),
    Column("type", VARCHAR2(16), nullable=True),
    _json("groups", "permissions", nullable=True),
    Column("description", VARCHAR2(1000), nullable=True),
    _oid_check("groups"),
    _json_check("groups", "permissions"),
)


# ---------------------------------------------------------------------------
# ACTIVITIES
# ---------------------------------------------------------------------------

_ORIGINS = (
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
)
_ORIGIN_IN = ",".join(f"'{o}'" for o in _ORIGINS + ("unknown",))

activities = Table(
    "activities",
    metadata,
    _oid_pk(),
    Column("atype", VARCHAR2(16), nullable=False),
    Column("name", VARCHAR2(256), nullable=False),
    Column("description", VARCHAR2(1000), nullable=True),
    Column("activity_date", oracle.TIMESTAMP, nullable=False),
    Column("created_at", oracle.TIMESTAMP, nullable=False),
    Column("updated_at", oracle.TIMESTAMP, nullable=False),
    Column("appointee_oid", VARCHAR2(24), nullable=False),
    Column("approver", VARCHAR2(24), nullable=True),
    Column("creator_oid", VARCHAR2(24), nullable=False),
    Column("status", VARCHAR2(16), nullable=False),
    Column("place", VARCHAR2(256), nullable=True),
    Column("origin", VARCHAR2(24), nullable=False),
    _oid_check("activities"),
    CheckConstraint(
        "atype IN ('on-campus','off-campus','social-practice','hybrid')",
        name="ck_activities_atype",
    ),
    CheckConstraint(
        "status IN ('pending','effective','refused')",
        name="ck_activities_status",
    ),
    CheckConstraint(
        f"origin IN ({_ORIGIN_IN})",
        name="ck_activities_origin",
    ),
    # NOTE: no FK on appointee_oid / creator_oid.  Production carries activities
    # whose creator/appointee account was deleted (dangling ObjectId), matching
    # Mongo semantics where a missing user simply resolves to "未知用户".
    # V006 drops these two constraints on already-deployed instances.
)
Index("ix_activities_status", activities.c.status)
Index("ix_activities_atype", activities.c.atype)
Index("ix_activities_creator_oid", activities.c.creator_oid)


# ---------------------------------------------------------------------------
# ACTIVITY_MEMBERS
# ---------------------------------------------------------------------------

activity_members = Table(
    "activity_members",
    metadata,
    _oid_pk(),
    Column("activity_oid", VARCHAR2(24), nullable=False),
    Column("member_oid", VARCHAR2(24), nullable=False),
    Column("member_status", VARCHAR2(16), nullable=False),
    Column("member_mode", VARCHAR2(16), nullable=False),
    Column("duration_hours", BINARY_DOUBLE, nullable=False),
    _oid_check("activity_members"),
    CheckConstraint(
        "member_status IN ('effective','pending','refused')",
        name="ck_activity_members_member_status",
    ),
    CheckConstraint(
        "member_mode IN ('on-campus','off-campus','social-practice')",
        name="ck_activity_members_member_mode",
    ),
    UniqueConstraint(
        "member_oid",
        "activity_oid",
        "member_mode",
        name="uq_activity_members_dedup",
    ),
    ForeignKeyConstraint(
        ["activity_oid"], ["activities.oid"], name="fk_activity_members_activity_oid"
    ),
    # NOTE: no FK on member_oid - a few member rows reference a since-deleted
    # account (dangling ObjectId), same "未知用户" semantics as activities.
    # V006 drops this constraint on already-deployed instances.
)
Index("ix_activity_members_activity_oid", activity_members.c.activity_oid)
Index("ix_activity_members_member_oid", activity_members.c.member_oid)


# ---------------------------------------------------------------------------
# USER_TIME  (Mongo collection: time)
# ---------------------------------------------------------------------------

user_time = Table(
    "user_time",
    metadata,
    _oid_pk(),
    Column("user_oid", VARCHAR2(24), nullable=False),
    Column("on_campus_raw", BINARY_DOUBLE, nullable=False, server_default=text("0")),
    Column("off_campus_raw", BINARY_DOUBLE, nullable=False, server_default=text("0")),
    Column("social_practice", BINARY_DOUBLE, nullable=False, server_default=text("0")),
    Column("updated_at", oracle.TIMESTAMP, nullable=False),
    _oid_check("user_time"),
    UniqueConstraint("user_oid", name="uq_user_time_user_oid"),
    ForeignKeyConstraint(["user_oid"], ["users.oid"], name="fk_user_time_user_oid"),
)


# ---------------------------------------------------------------------------
# TIME_ACADEMIC_YEAR
# ---------------------------------------------------------------------------

time_academic_year = Table(
    "time_academic_year",
    metadata,
    _oid_pk(),
    Column("user_oid", VARCHAR2(24), nullable=False),
    Column("entry_year", VARCHAR2(8), nullable=False),
    Column("academic_year", NUMBER(4), nullable=False),
    Column("start_of_year", oracle.TIMESTAMP, nullable=False),
    Column("end_of_year", oracle.TIMESTAMP, nullable=False),
    Column("on_campus", BINARY_DOUBLE, nullable=False),
    Column("off_campus", BINARY_DOUBLE, nullable=False),
    Column("social_practice", BINARY_DOUBLE, nullable=False),
    _oid_check("time_academic_year"),
    UniqueConstraint(
        "user_oid", "academic_year", name="uq_time_academic_year_user_year"
    ),
    ForeignKeyConstraint(
        ["user_oid"], ["users.oid"], name="fk_time_academic_year_user_oid"
    ),
)
Index("ix_time_academic_year_user_oid", time_academic_year.c.user_oid)


# ---------------------------------------------------------------------------
# TIME_WITH_ORIGIN
# ---------------------------------------------------------------------------

time_with_origin = Table(
    "time_with_origin",
    metadata,
    _oid_pk(),
    Column("user_oid", VARCHAR2(24), nullable=False),
    _json("time_with_origin", "data", nullable=False),
    Column("updated_at", oracle.TIMESTAMP, nullable=False),
    _oid_check("time_with_origin"),
    _json_check("time_with_origin", "data"),
    UniqueConstraint("user_oid", name="uq_time_with_origin_user_oid"),
    ForeignKeyConstraint(
        ["user_oid"], ["users.oid"], name="fk_time_with_origin_user_oid"
    ),
)


# ---------------------------------------------------------------------------
# TIME_WITH_ORIGIN_STAT
# ---------------------------------------------------------------------------

time_with_origin_stat = Table(
    "time_with_origin_stat",
    metadata,
    _oid_pk(),
    Column("stat_key", VARCHAR2(64), nullable=False),
    Column("scope", VARCHAR2(8), nullable=False),
    Column("scope_id", VARCHAR2(24), nullable=False),
    Column("require_percentage", NUMBER(3), nullable=False, server_default=text("0")),
    _json("time_with_origin_stat", "data", nullable=False),
    Column("updated_at", oracle.TIMESTAMP, nullable=False),
    _oid_check("time_with_origin_stat"),
    CheckConstraint("scope IN ('grade','group')", name="ck_time_with_origin_stat_scope"),
    _json_check("time_with_origin_stat", "data"),
    UniqueConstraint("stat_key", name="uq_time_with_origin_stat_stat_key"),
)


# ---------------------------------------------------------------------------
# GROUP_INDICATORS  (FK to groups is remap-gated: declared here + in V009, not
# V001 — see the V001 header and V009's ORDERING WARNING)
# ---------------------------------------------------------------------------

group_indicators = Table(
    "group_indicators",
    metadata,
    _oid_pk(),
    Column("group_oid", VARCHAR2(24), nullable=False),
    _json("group_indicators", "percentiles", nullable=True),
    _json("group_indicators", "indicators", nullable=True),
    Column("updated_at", oracle.TIMESTAMP, nullable=True),
    _oid_check("group_indicators"),
    _json_check("group_indicators", "percentiles"),
    _json_check("group_indicators", "indicators"),
    UniqueConstraint("group_oid", name="uq_group_indicators_group_oid"),
    ForeignKeyConstraint(
        ["group_oid"], ["groups.oid"], name="fk_group_indicators_group_oid"
    ),
)


# ---------------------------------------------------------------------------
# INDICATORS  (single-row table, enforced by the application)
# ---------------------------------------------------------------------------

indicators = Table(
    "indicators",
    metadata,
    _oid_pk(),
    _json("indicators", "percentiles", nullable=True),
    _json("indicators", "indicators", nullable=True),
    Column("updated_at", oracle.TIMESTAMP, nullable=True),
    _oid_check("indicators"),
    _json_check("indicators", "percentiles"),
    _json_check("indicators", "indicators"),
)


# ---------------------------------------------------------------------------
# DAILY_EXPORTS
# ---------------------------------------------------------------------------

daily_exports = Table(
    "daily_exports",
    metadata,
    _oid_pk(),
    Column("created_at", oracle.TIMESTAMP, nullable=False),
    Column("status", VARCHAR2(16), nullable=False),
    _json("daily_exports", "data", nullable=False, default="'[]'"),
    _oid_check("daily_exports"),
    _json_check("daily_exports", "data"),
)


# ---------------------------------------------------------------------------
# LOGS  (zvms_new device schema; see spec §2/§7 for pending policy)
# ---------------------------------------------------------------------------

logs = Table(
    "logs",
    metadata,
    _oid_pk(),
    Column("xuehai_id", NUMBER(18), nullable=True),
    _json("logs", "net_session", nullable=True),
    _json("logs", "request", nullable=True),
    _json("logs", "user_info", nullable=True),
    Column("ltype", VARCHAR2(64), nullable=True),
    Column("log_level", VARCHAR2(32), nullable=True),
    Column("summary", VARCHAR2(1000), nullable=True),
    Column("ts_at", oracle.TIMESTAMP, nullable=True),
    _json("logs", "ts_raw", nullable=True),
    _oid_check("logs"),
    _json_check("logs", "net_session"),
    _json_check("logs", "request"),
    _json_check("logs", "user_info"),
    _json_check("logs", "ts_raw"),
)


# ---------------------------------------------------------------------------
# CHANGELOG  ← zvms_new.changelog  (release version + release notes)
# ---------------------------------------------------------------------------

changelog = Table(
    "changelog",
    metadata,
    _oid_pk(),
    Column("version", VARCHAR2(32), nullable=False),
    Column("release_date", VARCHAR2(16), nullable=False),
    _json("changelog", "items", nullable=False, default="'[]'"),
    Column("updated_at", oracle.TIMESTAMP, nullable=False),
    _oid_check("changelog"),
    _json_check("changelog", "items"),
)


# ---------------------------------------------------------------------------
# APP_LOGS  ← zvms_new.app_logs  (runtime app logs; written by typings/log.py)
# ---------------------------------------------------------------------------

app_logs = Table(
    "app_logs",
    metadata,
    _oid_pk(),
    Column("url", VARCHAR2(512), nullable=True),
    Column("app_user", VARCHAR2(64), nullable=True),
    Column("data", VARCHAR2(2000), nullable=True),
    Column("ts_at", oracle.TIMESTAMP, nullable=True),
    Column("ts_raw", BINARY_DOUBLE, nullable=True),
    Column("ltype", VARCHAR2(64), nullable=True),
    Column("log_level", VARCHAR2(32), nullable=True),
    Column("xuehai_id", VARCHAR2(32), nullable=True),
    _oid_check("app_logs"),
)
Index("ix_app_logs_ts_at", app_logs.c.ts_at)
Index("ix_app_logs_xuehai_id", app_logs.c.xuehai_id)


# ---------------------------------------------------------------------------
# EXPORT_TASKS  ← zvms_new.tasks  (runtime export queue; `id` is a BSON UUID)
# ---------------------------------------------------------------------------

export_tasks = Table(
    "export_tasks",
    metadata,
    _oid_pk(),
    Column("task_uuid", VARCHAR2(36), nullable=False),
    Column("status", VARCHAR2(16), nullable=False),
    Column("percentage", BINARY_DOUBLE, nullable=True),
    Column("variant", VARCHAR2(32), nullable=True),
    Column("export_format", VARCHAR2(16), nullable=True),
    Column("allow_cache", NUMBER(1), nullable=False, server_default=text("0")),
    Column("include_description", NUMBER(1), nullable=False, server_default=text("0")),
    Column("errmsg", VARCHAR2(1000), nullable=True),
    Column("export_start", oracle.TIMESTAMP, nullable=True),
    Column("export_end", oracle.TIMESTAMP, nullable=True),
    Column("task_start", oracle.TIMESTAMP, nullable=True),
    Column("task_end", oracle.TIMESTAMP, nullable=True),
    _json("export_tasks", "result", nullable=False, default="'[]'"),
    _oid_check("export_tasks"),
    _json_check("export_tasks", "result"),
    UniqueConstraint("task_uuid", name="uq_export_tasks_uuid"),
)
Index("ix_export_tasks_status", export_tasks.c.status)


# ---------------------------------------------------------------------------
# IMPORTS  ← zvms_new.imports  (written by routers/imports_router.py)
# ---------------------------------------------------------------------------

imports = Table(
    "imports",
    metadata,
    _oid_pk(),
    Column("import_date", oracle.TIMESTAMP, nullable=True),
    Column("description", VARCHAR2(1000), nullable=True),
    Column("filename", VARCHAR2(256), nullable=True),
    Column("name", VARCHAR2(256), nullable=True),
    Column("user_oid", VARCHAR2(24), nullable=True),
    Column("content", BLOB, nullable=True),
    _oid_check("imports"),
)


# ---------------------------------------------------------------------------
# FILES  ← new (Files/attachments API; docs/FILES_API_DESIGN.md §3.1, V007)
#   `content` is a BLOB read back by db/repos/oracle/files.py; `owner_oid` has
#   NO foreign key (same relaxation as V006).
# ---------------------------------------------------------------------------

files = Table(
    "files",
    metadata,
    _oid_pk(),
    Column("owner_oid", VARCHAR2(24), nullable=True),
    Column("kind", VARCHAR2(16), nullable=False),
    Column("parent_oid", VARCHAR2(24), nullable=True),
    Column("mime", VARCHAR2(64), nullable=False),
    Column("size_bytes", NUMBER(19), nullable=False),
    Column("width", NUMBER(10), nullable=True),
    Column("height", NUMBER(10), nullable=True),
    Column("sha256", VARCHAR2(64), nullable=False),
    Column("content", BLOB, nullable=True),
    Column("original_name", VARCHAR2(255), nullable=True),
    Column("purpose", VARCHAR2(32), nullable=True),
    Column("entity_type", VARCHAR2(32), nullable=True),
    Column("entity_oid", VARCHAR2(24), nullable=True),
    Column("status", VARCHAR2(16), nullable=False),
    Column("ocr_status", VARCHAR2(16), nullable=False),
    Column("ocr_retry", NUMBER(3), nullable=False, server_default=text("0")),
    Column("created_at", oracle.TIMESTAMP, nullable=False),
    Column("updated_at", oracle.TIMESTAMP, nullable=True),
    Column("deleted_at", oracle.TIMESTAMP, nullable=True),
    _oid_check("files"),
    CheckConstraint(
        "kind IN ('original','thumb','derived')",
        name="ck_files_kind",
    ),
    CheckConstraint(
        "status IN ('stored','processing','ready','verified','rejected',"
        "'quarantined','deleted')",
        name="ck_files_status",
    ),
    CheckConstraint(
        "ocr_status IN ('pending','processing','done','failed')",
        name="ck_files_ocr_status",
    ),
)
Index("ix_files_owner", files.c.owner_oid, files.c.created_at)
Index("ix_files_entity", files.c.entity_type, files.c.entity_oid)
Index("ix_files_sha256", files.c.owner_oid, files.c.sha256)
Index("ix_files_parent", files.c.parent_oid)
Index("ix_files_cleanup", files.c.status, files.c.deleted_at)
Index("ix_files_ocr", files.c.ocr_status)


# ---------------------------------------------------------------------------
# FILE_OCR  ← new (per-file OCR results; docs/FILES_API_DESIGN.md §3.1/§7)
# ---------------------------------------------------------------------------

file_ocr = Table(
    "file_ocr",
    metadata,
    _oid_pk(),
    Column("file_oid", VARCHAR2(24), nullable=False),
    Column("engine", VARCHAR2(64), nullable=True),
    Column("engine_version", VARCHAR2(64), nullable=True),
    Column("ocr_text", CLOB, nullable=True),
    Column("confidence", BINARY_DOUBLE, nullable=True),
    _json("file_ocr", "raw_json", nullable=True),
    Column("created_at", oracle.TIMESTAMP, nullable=False),
    _oid_check("file_ocr"),
    _json_check("file_ocr", "raw_json"),
)
Index("ix_file_ocr_file", file_ocr.c.file_oid)


# ---------------------------------------------------------------------------
# FILE_EVENTS  ← new (append-only audit trail; docs/FILES_API_DESIGN.md §8.5)
# ---------------------------------------------------------------------------

file_events = Table(
    "file_events",
    metadata,
    _oid_pk(),
    Column("file_oid", VARCHAR2(24), nullable=False),
    Column("actor_oid", VARCHAR2(24), nullable=True),
    Column("action", VARCHAR2(24), nullable=False),
    Column("from_status", VARCHAR2(16), nullable=True),
    Column("to_status", VARCHAR2(16), nullable=True),
    _json("file_events", "detail", nullable=True),
    Column("created_at", oracle.TIMESTAMP, nullable=False),
    _oid_check("file_events"),
    _json_check("file_events", "detail"),
)
Index("ix_file_events_file", file_events.c.file_oid, file_events.c.created_at)


# ---------------------------------------------------------------------------
# USER_DEVICE_BINDINGS  ← new (学海 ID 身份追溯 MVP; observe-only, V010)
#   user_oid -> USERS(OID) is a real FK (new, empty table - no legacy ids).
#   A revoked row is kept (status='revoked') rather than deleted; the
#   (user_oid, xuehai_id) pair is unique so a re-bind reactivates in place.
# ---------------------------------------------------------------------------

user_device_bindings = Table(
    "user_device_bindings",
    metadata,
    _oid_pk(),
    Column("user_oid", VARCHAR2(24), nullable=False),
    Column("xuehai_id", VARCHAR2(32), nullable=False),
    Column("device_id", VARCHAR2(64), nullable=True),
    Column("source", VARCHAR2(16), nullable=False, server_default=text("'auto'")),
    Column("status", VARCHAR2(16), nullable=False, server_default=text("'active'")),
    Column("note", VARCHAR2(256), nullable=True),
    Column("created_at", oracle.TIMESTAMP, nullable=False),
    Column("updated_at", oracle.TIMESTAMP, nullable=False),
    CheckConstraint(f"REGEXP_LIKE(oid,'{OID_REGEX}')", name="ck_udb_oid"),
    CheckConstraint("source IN ('auto','admin')", name="ck_udb_source"),
    CheckConstraint("status IN ('active','revoked')", name="ck_udb_status"),
    UniqueConstraint("user_oid", "xuehai_id", name="uq_udb_user_xuehai"),
    ForeignKeyConstraint(["user_oid"], ["users.oid"], name="fk_udb_user"),
)
Index("ix_udb_xuehai_id", user_device_bindings.c.xuehai_id)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_table(name: str) -> Table:
    """Return the mirrored :class:`~sqlalchemy.Table` for ``name``.

    ``name`` is matched case-insensitively against the physical table name.
    """
    key = name.strip().lower()
    if key in metadata.tables:
        return metadata.tables[key]
    raise KeyError(f"Unknown table: {name!r}")


def table_names() -> list[str]:
    """Return the physical table names in creation order."""
    return list(metadata.tables.keys())
