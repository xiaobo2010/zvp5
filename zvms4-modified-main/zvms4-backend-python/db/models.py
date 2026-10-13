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
from sqlalchemy.dialects.oracle import BINARY_DOUBLE, NUMBER, VARCHAR2
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
        return json.loads(value)


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
    _oid_check("groups"),
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
    ForeignKeyConstraint(
        ["appointee_oid"], ["users.oid"], name="fk_activities_appointee_oid"
    ),
    ForeignKeyConstraint(
        ["creator_oid"], ["users.oid"], name="fk_activities_creator_oid"
    ),
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
    ForeignKeyConstraint(
        ["member_oid"], ["users.oid"], name="fk_activity_members_member_oid"
    ),
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
# GROUP_INDICATORS  (FK to groups intentionally deferred — see V001 header)
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
