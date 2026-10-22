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
    # 封禁元数据（V013）: ``banned`` 仍是唯一的登录闸门，这四列只回答
    # 「为什么、到什么时候、谁/哪条规则判的」。``ban_until IS NULL`` 且
    # ``banned=1`` = 永久封禁；规则封禁到期由任务侧解封（本层只存事实）。
    Column("ban_reason", VARCHAR2(512), nullable=True),
    Column("ban_until", oracle.TIMESTAMP, nullable=True),
    Column("ban_source", VARCHAR2(16), nullable=True),
    Column("ban_rule", VARCHAR2(64), nullable=True),
    # 主学海设备（V014）: NULL = 未绑定。存量账号保持 NULL，未绑定期间不做设备
    # 校验；一旦绑定，登录只允许来自该设备（或走临时密码）。
    Column("primary_xuehai_id", VARCHAR2(32), nullable=True),
    _oid_check("users"),
    _json_check("users", "roles"),
    _json_check("users", "eligibility"),
    _json_check("users", "past"),
    CheckConstraint("banned IN (0,1)", name="ck_users_banned"),
    UniqueConstraint("business_id", name="uq_users_business_id"),
    ForeignKeyConstraint(["class_oid"], ["groups.oid"], name="fk_users_class_oid"),
)
Index("ix_users_class_oid", users.c.class_oid)
Index("ix_users_primary_xuehai", users.c.primary_xuehai_id)


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
# 规则封禁要按「用户 + 时间窗」数不同学海设备（COUNT(DISTINCT xuehai_id)）；
# 单列索引只能吃到 APP_USER 一半，复合索引让时间窗过滤留在索引里。
Index("ix_app_logs_user_ts", app_logs.c.app_user, app_logs.c.ts_raw)


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
# DUAL_AUTH_USES  ← new (Stage 2 双人授权; V012)
#   One row per accepted TOTP window: the unique (approver_oid, otp_counter)
#   constraint is the atomic replay guard. No FK on purpose - the row must
#   survive deletion of either account (it is an audit trail, not a relation).
# ---------------------------------------------------------------------------

dual_auth_uses = Table(
    "dual_auth_uses",
    metadata,
    _oid_pk(),
    Column("approver_oid", VARCHAR2(24), nullable=False),
    Column("otp_counter", NUMBER(19), nullable=False),
    Column("action", VARCHAR2(64), nullable=False),
    Column("operator_oid", VARCHAR2(24), nullable=False),
    Column("created_at", oracle.TIMESTAMP, nullable=False),
    UniqueConstraint("approver_oid", "otp_counter", name="uq_dual_approver_counter"),
)


# ---------------------------------------------------------------------------
# BAN_RULES  ← new (规则封禁配置; V013)
#   One row per automatic-ban rule. ``CONFIG``/``PENALTIES`` are bounded JSON
#   documents: VARCHAR2(2000) instead of CLOB because they are small, always
#   read whole, and never exceed 4000 bytes — a LOB locator would only add
#   round trips. ``CODE`` is the stable identity the login path quotes.
# ---------------------------------------------------------------------------

ban_rules = Table(
    "ban_rules",
    metadata,
    _oid_pk(),
    Column("code", VARCHAR2(64), nullable=False),
    Column("name", VARCHAR2(128), nullable=False),
    Column("enabled", NUMBER(1), nullable=False, server_default=text("1")),
    Column("config", VARCHAR2(2000), nullable=False),
    Column("penalties", VARCHAR2(2000), nullable=False),
    Column("builtin", NUMBER(1), nullable=False, server_default=text("0")),
    Column("created_at", oracle.TIMESTAMP, nullable=False),
    Column("updated_at", oracle.TIMESTAMP, nullable=False),
    _oid_check("ban_rules"),
    CheckConstraint("enabled IN (0,1)", name="ck_ban_rules_enabled"),
    CheckConstraint("builtin IN (0,1)", name="ck_ban_rules_builtin"),
    CheckConstraint("config IS JSON", name="ck_ban_rules_config_json"),
    CheckConstraint("penalties IS JSON", name="ck_ban_rules_penalties_json"),
    UniqueConstraint("code", name="uq_ban_rules_code"),
)


# ---------------------------------------------------------------------------
# BAN_RECORDS  ← new (封禁流水/审计; V013)
#   每一次封禁一行，是「累犯升级处罚」的依据：同一 (user, rule) 的历史行数
#   决定下一次用第几档 penalty。``USER_OID`` 带真外键（表从空开始），删用户
#   时由级联归档路径处理；其余列无约束，保留判定当时的原貌。
# ---------------------------------------------------------------------------

ban_records = Table(
    "ban_records",
    metadata,
    _oid_pk(),
    Column("user_oid", VARCHAR2(24), nullable=False),
    Column("source", VARCHAR2(16), nullable=False),
    Column("rule_code", VARCHAR2(64), nullable=True),
    Column("reason", VARCHAR2(512), nullable=True),
    Column("permanent", NUMBER(1), nullable=False, server_default=text("0")),
    Column("started_at", oracle.TIMESTAMP, nullable=False),
    Column("expires_at", oracle.TIMESTAMP, nullable=True),
    Column("operator_oid", VARCHAR2(24), nullable=True),
    Column("created_at", oracle.TIMESTAMP, nullable=False),
    _oid_check("ban_records"),
    CheckConstraint("source IN ('admin','rule')", name="ck_ban_records_source"),
    CheckConstraint("permanent IN (0,1)", name="ck_ban_records_permanent"),
    ForeignKeyConstraint(["user_oid"], ["users.oid"], name="fk_ban_records_user"),
)
Index("ix_ban_records_user", ban_records.c.user_oid)
Index("ix_ban_records_started", ban_records.c.started_at)


# ---------------------------------------------------------------------------
# NOTIFICATIONS ← new (站内通知; V015)
#   One row per admin-published notice. AUTHOR_OID / AUTHOR_NAME are written
#   once and never updated: the display name at send time is part of the audit
#   trail, so a later rename must not rewrite history. No FK on AUTHOR_OID for
#   the same reason (the notice survives account deletion).
#   VERSION is the per-notice revision: 1 on create, +1 on every successful
#   edit/deactivate, so clients can tell an edited notice from the one they
#   already acknowledged.
# ---------------------------------------------------------------------------

notifications = Table(
    "notifications",
    metadata,
    _oid_pk(),
    Column("title", VARCHAR2(200), nullable=False),
    Column("content", VARCHAR2(4000), nullable=False),
    Column("active", NUMBER(1), nullable=False, server_default=text("1")),
    Column("author_oid", VARCHAR2(24), nullable=False),
    Column("author_name", VARCHAR2(128), nullable=False),
    Column("version", NUMBER(19), nullable=False, server_default=text("1")),
    Column("created_at", oracle.TIMESTAMP, nullable=False),
    Column("updated_at", oracle.TIMESTAMP, nullable=False),
    _oid_check("notifications"),
    CheckConstraint("active IN (0,1)", name="ck_notifications_active"),
    CheckConstraint("version > 0", name="ck_notifications_version"),
)
Index(
    "ix_notifications_active_created",
    notifications.c.active,
    notifications.c.created_at,
)


# ---------------------------------------------------------------------------
# TEMP_LOGIN_PASSWORDS  ← new (团支书/学生会签发的一次性临时密码; V014)
#   只存 bcrypt 哈希：明文只在创建响应里出现一次，DB 泄露也无法还原。单次使用
#   （``used_at``）+ 30 分钟过期（``expires_at``）+ 可撤销（``revoked_at``）：
#   三个时间戳而不是一个状态列，是因为「用过 / 过期 / 被撤销」在审计上是三种
#   不同的事实，合并成枚举会丢掉时间点。
#   无外键：签发人与目标用户都可能被硬删除，而密码行是审计材料。
# ---------------------------------------------------------------------------

temp_login_passwords = Table(
    "temp_login_passwords",
    metadata,
    _oid_pk(),
    Column("target_user_oid", VARCHAR2(24), nullable=False),
    Column("created_by_oid", VARCHAR2(24), nullable=False),
    Column("code_hash", VARCHAR2(100), nullable=False),
    Column("creator_xuehai_id", VARCHAR2(32), nullable=True),
    Column("expires_at", oracle.TIMESTAMP, nullable=False),
    Column("used_at", oracle.TIMESTAMP, nullable=True),
    Column("revoked_at", oracle.TIMESTAMP, nullable=True),
    Column("created_at", oracle.TIMESTAMP, nullable=False),
    _oid_check("temp_login_passwords"),
)
Index(
    "ix_tlp_target",
    temp_login_passwords.c.target_user_oid,
    temp_login_passwords.c.expires_at,
)


# ---------------------------------------------------------------------------
# NOTIFICATION_STATE ← new (通知全局版本; V015)
#   Single-row counter (ID = 1). Every successful notification create / edit /
#   delete bumps VERSION in the same transaction as the row change, which is
#   the client's cheap "is there anything new?" signal. Keeping it in one row
#   also serialises concurrent admin writes at the database.
# ---------------------------------------------------------------------------

notification_state = Table(
    "notification_state",
    metadata,
    Column("id", NUMBER(1), primary_key=True),
    Column("version", NUMBER(19), nullable=False, server_default=text("0")),
    Column("updated_at", oracle.TIMESTAMP, nullable=False),
    CheckConstraint("id = 1", name="ck_notification_state_id"),
    CheckConstraint("version >= 0", name="ck_notification_state_version"),
)


# ---------------------------------------------------------------------------
# DEVICE_CHALLENGES  ← new (异设备登录的一次性挑战; V014)
#   ``token_hash`` 存 SHA-256 hex 而不是明文 token：挑战值等价于「一次登录
#   许可」，泄露即可被抢用。唯一索引既是查找路径也是防重放裁决者。
#   ``attempts`` 由服务层在行锁（``SELECT ... FOR UPDATE``）内递增，第 3 次
#   同时封账号与设备——所以计数必须落在库里，不能放进程内存。
# ---------------------------------------------------------------------------

device_challenges = Table(
    "device_challenges",
    metadata,
    _oid_pk(),
    Column("token_hash", VARCHAR2(64), nullable=False),
    Column("user_oid", VARCHAR2(24), nullable=False),
    Column("xuehai_id", VARCHAR2(32), nullable=False),
    Column("attempts", NUMBER(3), nullable=False, server_default=text("0")),
    Column("expires_at", oracle.TIMESTAMP, nullable=False),
    Column("consumed_at", oracle.TIMESTAMP, nullable=True),
    Column("created_at", oracle.TIMESTAMP, nullable=False),
    _oid_check("device_challenges"),
)
Index(
    "ux_device_challenges_token",
    device_challenges.c.token_hash,
    unique=True,
)


# ---------------------------------------------------------------------------
# BANNED_DEVICES  ← new (设备级封禁; V014)
#   主键就是学海设备号：一台设备只有一条当前封禁状态，重复封禁是覆盖而不是
#   追加。``until`` NULL = 永久；``source`` 区分规则判罚（连错临时密码 3 次）
#   与管理员手动封禁。账号封禁**不**镜像到这里：由服务层按
#   ``users.primary_xuehai_id`` 实时推导，账号解封/换设备后自动失效。
#   无 ``oid``、无外键：设备号是外部标识，删除用户不应连带删设备封禁。
# ---------------------------------------------------------------------------

banned_devices = Table(
    "banned_devices",
    metadata,
    Column("xuehai_id", VARCHAR2(32), primary_key=True),
    Column("reason", VARCHAR2(512), nullable=True),
    Column("until", oracle.TIMESTAMP, nullable=True),
    Column("source", VARCHAR2(16), nullable=False),
    Column("rule_code", VARCHAR2(64), nullable=True),
    Column("operator_oid", VARCHAR2(24), nullable=True),
    Column("created_at", oracle.TIMESTAMP, nullable=False),
    CheckConstraint("source IN ('rule','admin')", name="ck_banned_devices_source"),
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
