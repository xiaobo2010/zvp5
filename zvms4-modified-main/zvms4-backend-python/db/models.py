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


def _uuid_pk() -> Column:
    """32 位 hex 主键（``uuid4().hex``）：对外句柄，与业务 oid 空间分开。

    刻意**不带** :func:`_oid_check`：那条约束是 24 位正则，套到 32 位 uuid 上
    会让每一次插入都被数据库拒掉。
    """
    return Column("oid", VARCHAR2(32), primary_key=True)


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
# 管理端日志页按 ts_raw 倒序分页，并按类型/级别过滤后再排序；两条索引分别
# 服务「无过滤」与「type(±level) + 时间窗」两种查询形状。
Index("ix_app_logs_ts_raw", app_logs.c.ts_raw)
Index("ix_app_logs_type_level_ts", app_logs.c.ltype, app_logs.c.log_level, app_logs.c.ts_raw)


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
# 导出列表按 task_start 倒序分页；没有它只能全表取回再截断。
Index("ix_export_tasks_task_start", export_tasks.c.task_start)


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
    # 检查项（V016）：求值入口按 trigger 取唯一一条规则，CODE 只是管理员看到的名字。
    # 可空是给存量行加列留的余地（唯一索引允许多个 NULL）。
    # ``quote=False``：TRIGGER 在 Oracle 里是非保留关键字，可以裸用；不加引号
    # 才能让手写 DDL 与 SQLAlchemy 生成的语句保持同一份文本。
    Column("trigger_code", VARCHAR2(64), nullable=True),
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
# 一个检查项最多一条规则：两条时「用哪条」只能靠运气，封禁天数就不可复现。
Index("ux_ban_rules_trigger", ban_rules.c.trigger_code, unique=True)


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
# NOTIFICATION_VERSIONS ← new (通知内容快照; V017)
#   Append-only: one row per successful create / update / delete / rollback,
#   each holding the full content **after** that write. No FK on
#   NOTIFICATION_OID for the same reason NOTIFICATIONS has none on
#   AUTHOR_OID - the history has to outlive the notice it describes. The
#   (notification_oid, version) unique constraint is the last line of defence
#   against two writers claiming the same revision; normal serialisation comes
#   from the FOR UPDATE lock on the NOTIFICATIONS row.
#   CONTENT_HASH is the sha256 of title/content/active, so clients can compare
#   content without fetching and diffing it.
# ---------------------------------------------------------------------------

notification_versions = Table(
    "notification_versions",
    metadata,
    _oid_pk(),
    Column("notification_oid", VARCHAR2(24), nullable=False),
    Column("version", NUMBER(19), nullable=False),
    Column("title", VARCHAR2(200), nullable=False),
    Column("content", VARCHAR2(4000), nullable=False),
    Column("active", NUMBER(1), nullable=False),
    Column("op", VARCHAR2(16), nullable=False),
    Column("source_version", NUMBER(19), nullable=True),
    Column("content_hash", VARCHAR2(64), nullable=False),
    Column("author_oid", VARCHAR2(24), nullable=True),
    Column("author_name", VARCHAR2(128), nullable=False),
    Column("created_at", oracle.TIMESTAMP, nullable=False),
    UniqueConstraint("notification_oid", "version", name="uq_notification_versions"),
)
Index(
    "ix_notification_versions_doc",
    notification_versions.c.notification_oid,
    notification_versions.c.version,
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
# challenge_flood 按 (user_oid, created_at) 数窗口内的挑战数。
Index(
    "ix_device_challenges_user_created",
    device_challenges.c.user_oid,
    device_challenges.c.created_at,
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
# BAN_VIOLATIONS  ← new (客户端上报的违规信号; V016 / V019)
#   append-only：一条信号一行；**计数单位是批次而不是行**（V019 的 BATCH_ID），
#   一次上报里的多个信号只算一次违规。无外键：违规记录是审计材料，必须比
#   账号活得久（用户被硬删除后仍要能回答「他当时因为什么被封」）。
#   BATCH_ID 由服务端生成，旧行为 NULL（以 OID 顶替，等价于「每行一批」）。
# ---------------------------------------------------------------------------

ban_violations = Table(
    "ban_violations",
    metadata,
    _oid_pk(),
    Column("user_oid", VARCHAR2(24), nullable=False),
    Column("trigger_code", VARCHAR2(64), nullable=False),
    Column("signal", VARCHAR2(32), nullable=False),
    Column("xuehai_id", VARCHAR2(32), nullable=True),
    Column("detail", VARCHAR2(500), nullable=True),
    Column("batch_id", VARCHAR2(32), nullable=True),
    Column("created_at", oracle.TIMESTAMP, nullable=False),
    _oid_check("ban_violations"),
)
Index(
    "ix_ban_violations_user",
    ban_violations.c.user_oid,
    ban_violations.c.trigger_code,
    ban_violations.c.created_at,
)
Index(
    "ix_ban_violations_batch",
    ban_violations.c.user_oid,
    ban_violations.c.trigger_code,
    ban_violations.c.created_at,
    ban_violations.c.batch_id,
)


# ---------------------------------------------------------------------------
# AI_CALL_LOGS  ← new (AI 网关调用流水; V024)
#   append-only：一次网关调用一行，success/error/timeout/rejected/busy 都落库
#   ——只记成功就无法回答「额度为什么没扣成」。无外键：审计材料必须比账号
#   活得久（同 BAN_VIOLATIONS）。UNITS 是本次调用扣掉的额度单位，与
#   AI_QUOTA.USED 的增量同源，两边都留下才能对账。
#
#   正文（prompt / completion）**一律不落库**：这是合规决定，不是缺口。表里只
#   有元数据，加上 V031 的字段就是 OTel GenAI 语义约定的一个子集（当自有
#   schema 管理，不承诺外部兼容）。
#
#   不变式：PROMPT_TOKENS / COMPLETION_TOKENS / TOTAL_TOKENS 为 NULL ⟺ 这次
#   调用的 UNITS 是**估算值**（流式上游没回 usage 帧，按「4 字符 ≈ 1 token」算）。
#   上游回了 usage 就是实测量，UNITS 也由它折算。别用 0 代替 NULL：0 是一个
#   真实的 token 数，会把「不知道」伪装成「没有」。
# ---------------------------------------------------------------------------

ai_call_logs = Table(
    "ai_call_logs",
    metadata,
    _oid_pk(),
    Column("user_oid", VARCHAR2(24), nullable=False),
    Column("capability", VARCHAR2(16), nullable=False),
    Column("endpoint", VARCHAR2(40), nullable=False),
    Column("provider", VARCHAR2(40), nullable=True),
    Column("model", VARCHAR2(80), nullable=True),
    Column("status", VARCHAR2(16), nullable=False),
    Column("http_status", _int3, nullable=True),
    Column("error_code", VARCHAR2(40), nullable=True),
    Column("latency_ms", NUMBER(10), nullable=True),
    Column("units", NUMBER(10), nullable=False, server_default=text("0")),
    Column("input_bytes", NUMBER(10), nullable=True),
    Column("request_id", VARCHAR2(64), nullable=True),
    # 本次调用用的 key 来源（personal / shared / server）；V025 新增，存量行 NULL。
    Column("key_scope", VARCHAR2(16), nullable=True),
    # --- V031：可审计粒度（全部 nullable，存量行 NULL = 未知）---
    # 上游 usage 的三个 token 数（非流式必填，流式仅在拿到 usage 帧时填）。
    Column("prompt_tokens", NUMBER(10), nullable=True),
    Column("completion_tokens", NUMBER(10), nullable=True),
    Column("total_tokens", NUMBER(10), nullable=True),
    # 同一次请求的第几次上游尝试（1 起）。多 provider 重试与 OCR 引擎回退共用。
    Column("attempt_no", NUMBER(3), nullable=True),
    # 调用场景（前端 ``X-AI-Scene`` 头 / OCR 固定 'ocr'）。开放集合，不建 CHECK。
    Column("purpose", VARCHAR2(24), nullable=True),
    # 流式首块到达耗时（毫秒）；非流式为 NULL（没有「首块」这个概念）。
    Column("first_token_ms", NUMBER(10), nullable=True),
    # OCR 引擎类型（tencent/aliyun/rapidocr/baidu/groq/zhipu）；OCR 行才填。
    Column("engine_kind", VARCHAR2(24), nullable=True),
    # OCR 针对的附件 oid。无外键：附件删了，这次识别发生过的事实仍然成立。
    Column("attachment_oid", VARCHAR2(24), nullable=True),
    # --- V032/V033：prompt cache + agent 执行链路（全部 nullable，NULL = 未知 / 未注入）---
    # 同一次 agent run 的第几轮（1 起）。一次 run 的**每一轮**各记一行流水，
    # 因为每一轮都是一次独立的上游调用、各扣一次额度。NULL = 不是 agent 轮次。
    Column("round_no", _int3, nullable=True),
    # 调用时的角色快照（逗号连接，如 ``department,secretary``）。角色是可变事实：
    # 事后改角色会让「当时凭什么放行」失真，所以冻结一份在流水行上。
    Column("roles_snapshot", VARCHAR2(64), nullable=True),
    # 归一后的命中 token：OpenAI ``prompt_tokens_details.cached_tokens`` /
    # Anthropic ``cache_read_input_tokens`` / DeepSeek ``prompt_cache_hit_tokens``
    # 三家的口径都落到这一格。NULL = 上游没报，不是「命中 0」。
    Column("cache_read_tokens", NUMBER(10), nullable=True),
    # 写缓存的 token（Anthropic ``cache_creation_input_tokens`` / OpenAI
    # ``cache_write_tokens``）。NULL = 这次没写缓存，或上游没报。
    Column("cache_write_tokens", NUMBER(10), nullable=True),
    # 我们算的稳定前缀哈希（sha256 前 16 位，形如 ``zvp:1a2b3c4d5e6f7890``），
    # **不含用户内容**：只用来把「同一段前缀」的调用归到一起看命中率。
    Column("cache_key", VARCHAR2(64), nullable=True),
    Column("created_at", oracle.TIMESTAMP, nullable=False),
    _oid_check("ai_call_logs"),
    CheckConstraint("capability IN ('ocr','chat')", name="ck_ai_call_logs_capability"),
    CheckConstraint(
        "status IN ('success','error','timeout','rejected','busy')",
        name="ck_ai_call_logs_status",
    ),
    CheckConstraint("units >= 0", name="ck_ai_call_logs_units"),
    CheckConstraint(
        "key_scope IS NULL OR key_scope IN ('personal','shared','server')",
        name="ck_ai_call_logs_key_scope",
    ),
    CheckConstraint(
        "(prompt_tokens IS NULL OR prompt_tokens >= 0)"
        " AND (completion_tokens IS NULL OR completion_tokens >= 0)"
        " AND (total_tokens IS NULL OR total_tokens >= 0)",
        name="ck_ai_call_logs_tokens",
    ),
    CheckConstraint(
        "attempt_no IS NULL OR attempt_no >= 1",
        name="ck_ai_call_logs_attempt_no",
    ),
    CheckConstraint(
        "round_no IS NULL OR round_no >= 1",
        name="ck_ai_call_logs_round_no",
    ),
    CheckConstraint(
        "(cache_read_tokens IS NULL OR cache_read_tokens >= 0)"
        " AND (cache_write_tokens IS NULL OR cache_write_tokens >= 0)",
        name="ck_ai_call_logs_cache_tokens",
    ),
)
Index(
    "ix_ai_call_logs_user_created",
    ai_call_logs.c.user_oid,
    ai_call_logs.c.created_at,
)
Index("ix_ai_call_logs_created", ai_call_logs.c.created_at)
Index(
    "ix_ai_call_logs_endpoint_status",
    ai_call_logs.c.endpoint,
    ai_call_logs.c.status,
    ai_call_logs.c.created_at,
)
# 按 request_id 追一条链（一次 chat 的多 provider 尝试、一次 OCR 的多次引擎回退
# 共用一个 request_id）是排查的主要入口，没有索引就是全表扫。
Index("ix_ai_call_logs_request_id", ai_call_logs.c.request_id)


# ---------------------------------------------------------------------------
# AI_QUOTA  ← new (按 用户 + 月份 + 能力 计的额度行; V024)
#   三元组唯一（ux_ai_quota_user_period_kind），因此额度扣减是单条
#   `UPDATE ... WHERE used + :amount <= limit_count`：并发由数据库串行化，
#   没有读后写、不会超卖。PERIOD 是 CHAR(6) 的 YYYYMM —— 月份是计费口径，
#   不是时间点，用 CHAR 避免「按哪个时区截断月初」的隐式规则。
#   KIND 是前缀式：'ocr' / 'chat' / 'ocr_<engine>'。OCR 引擎类型会持续增加，
#   枚举式 CHECK 会让每加一个引擎都要改 DDL；前缀让「某引擎的月度额度」直接
#   是合法 kind。ESCAPE '\' 必须带上：LIKE 的 '_' 是单字符通配符。
# ---------------------------------------------------------------------------

ai_quota = Table(
    "ai_quota",
    metadata,
    _oid_pk(),
    Column("user_oid", VARCHAR2(24), nullable=False),
    Column("period", oracle.CHAR(6), nullable=False),
    Column("kind", VARCHAR2(16), nullable=False),
    Column("used", NUMBER(10), nullable=False, server_default=text("0")),
    Column("limit_count", NUMBER(10), nullable=False),
    Column("updated_at", oracle.TIMESTAMP, nullable=False),
    _oid_check("ai_quota"),
    CheckConstraint("REGEXP_LIKE(period,'^[0-9]{6}$')", name="ck_ai_quota_period"),
    CheckConstraint(
        "kind IN ('ocr','chat') OR kind LIKE 'ocr\\_%' ESCAPE '\\'",
        name="ck_ai_quota_kind",
    ),
    CheckConstraint("used >= 0", name="ck_ai_quota_used"),
    CheckConstraint("limit_count >= 0", name="ck_ai_quota_limit"),
)
Index(
    "ux_ai_quota_user_period_kind",
    ai_quota.c.user_oid,
    ai_quota.c.period,
    ai_quota.c.kind,
    unique=True,
)


# ---------------------------------------------------------------------------
# AI_CREDENTIALS  ← new (AI provider 凭据; V025)
#   一张表放两类凭据，用 SCOPE 区分：personal（管理员自己的 key）与 shared
#   （所有人可用的通用 key）——字段与读取路径完全相同，拆表只会把「按名字找
#   provider」变成两次查询加一次合并。
#   OWNER_OID 对 shared 行固定是 24 个 0 的哨兵而**不是 NULL**：Oracle 唯一索引
#   里 NULL 互不相等，owner 可空就等于 (scope, owner_oid, name) 的唯一性失效。
#   API_KEY_CIPHER 只存 AES-GCM 密文（base64），明文永不落库；KEY_HINT 只留末
#   四位用于展示。MODELS_JSON 是模型 id 字符串数组，路由与 /v1/models 目录都由
#   它驱动，所以用 CLOB + IS JSON 而不是裸字符串。
#   HEADERS_JSON / API_PATH 是请求形状：头名映射 + 路径，空 = 沿用默认
#   （Authorization + 默认路径）。存量 provider 不需要回填。
#   EXPIRES_AT 是 key 的到期时间，NULL = 永不过期；过期行不进生效目录（服务层
#   与 disabled 同路径处理），因此不需要在每个消费点各判一次。
# ---------------------------------------------------------------------------

ai_credentials = Table(
    "ai_credentials",
    metadata,
    _oid_pk(),
    Column("scope", VARCHAR2(8), nullable=False),
    Column("owner_oid", VARCHAR2(24), nullable=False),
    Column("name", VARCHAR2(64), nullable=False),
    Column("kind", VARCHAR2(24), nullable=False),
    Column("base_url", VARCHAR2(400), nullable=False),
    _json("ai_credentials", "models_json", nullable=False),
    # 额外请求头（头名 -> 值，值是服务层展开的模板）；NULL = 只用默认鉴权头。
    _json("ai_credentials", "headers_json", nullable=True),
    # 相对 base_url 的路径；NULL = 用默认路径。有些引擎的能力不在 /v1 下。
    Column("api_path", VARCHAR2(200), nullable=True),
    Column("api_key_cipher", VARCHAR2(2000), nullable=False),
    Column("key_hint", VARCHAR2(16), nullable=True),
    Column("enabled", NUMBER(1), nullable=False, server_default=text("1")),
    Column("created_at", oracle.TIMESTAMP, nullable=False),
    Column("updated_at", oracle.TIMESTAMP, nullable=False),
    # 这把 key 的到期时间；NULL = 永不过期（存量行不回填：它们本来就该一直有效）。
    # 过期条目在服务层与 disabled 走同一条「不进生效目录」的路径，所以路由与
    # /v1/models 都不需要单独判断它。不建 CHECK：「未来时间」是写入时刻的性质，
    # 不是行的不变量——今天合法的到期时间，明天自然就过期了。
    Column("expires_at", oracle.TIMESTAMP, nullable=True),
    _oid_check("ai_credentials"),
    CheckConstraint("scope IN ('personal','shared')", name="ck_ai_credentials_scope"),
    CheckConstraint("kind IN ('openai_compat')", name="ck_ai_credentials_kind"),
    CheckConstraint("enabled IN (0,1)", name="ck_ai_credentials_enabled"),
    CheckConstraint("models_json IS JSON", name="ck_ai_credentials_models_json"),
    CheckConstraint("headers_json IS JSON", name="ck_ai_credentials_headers_json"),
)
Index(
    "ux_ai_credentials_scope_owner_name",
    ai_credentials.c.scope,
    ai_credentials.c.owner_oid,
    ai_credentials.c.name,
    unique=True,
)
Index(
    "ix_ai_credentials_owner",
    ai_credentials.c.owner_oid,
    ai_credentials.c.enabled,
)
Index(
    "ix_ai_credentials_scope",
    ai_credentials.c.scope,
    ai_credentials.c.enabled,
)


# ---------------------------------------------------------------------------
# AI_OCR_ENGINES  ← new (OCR 引擎注册表; V026, KIND 由 V027 扩展)
#   一行 = 一个可路由的 OCR 引擎配置，KIND 是引擎类型（tencent / aliyun /
#   rapidocr / baidu / groq）而不是 provider 名：同类型可以有多行（不同
#   region / 配额来源）。
#   PRIORITY 数字越小越优先（100 为默认档），路由按 (enabled, priority) 取
#   第一行可用引擎，所以 ix_ai_ocr_engines_enabled_priority 就是查询形状。
#   SECRET_CIPHER 可空：本地引擎（rapidocr）没有凭证，「NULL = 不需要」而不是
#   「配置缺失」。密文口径同 API_KEY_CIPHER：只存 AES-GCM 的 base64。
#   NAME 唯一：运维按名字指定/禁用某个引擎。
# ---------------------------------------------------------------------------

ai_ocr_engines = Table(
    "ai_ocr_engines",
    metadata,
    _oid_pk(),
    Column("name", VARCHAR2(64), nullable=False),
    Column("kind", VARCHAR2(20), nullable=False),
    _json("ai_ocr_engines", "config_json", nullable=True),
    Column("secret_cipher", VARCHAR2(4000), nullable=True),
    Column("key_hint", VARCHAR2(16), nullable=True),
    Column("enabled", NUMBER(1), nullable=False, server_default=text("1")),
    Column("priority", NUMBER(3), nullable=False, server_default=text("100")),
    Column("created_at", oracle.TIMESTAMP, nullable=False),
    Column("updated_at", oracle.TIMESTAMP, nullable=False),
    _oid_check("ai_ocr_engines"),
    CheckConstraint(
        "kind IN ('tencent','aliyun','rapidocr','baidu','groq','zhipu')",
        name="ck_ai_ocr_engines_kind",
    ),
    CheckConstraint("enabled IN (0,1)", name="ck_ai_ocr_engines_enabled"),
    CheckConstraint("config_json IS JSON", name="ck_ai_ocr_engines_config_json"),
)
Index("ux_ai_ocr_engines_name", ai_ocr_engines.c.name, unique=True)
Index(
    "ix_ai_ocr_engines_enabled_priority",
    ai_ocr_engines.c.enabled,
    ai_ocr_engines.c.priority,
)


# ---------------------------------------------------------------------------
# AI_API_KEYS  ← new (OpenAI 兼容面的独立 API key; V030)
#   一行 = 一位用户的一把 key。TOKEN_HASH 存 sha256(token) 的 hex，明文只在
#   创建响应里出现一次，永不落库——所以这张表泄露也拿不到可用的 key。
#   NAME 在**同一 owner 内**唯一：key 的名字是管理人员认 key 的标签，不同用户
#   各叫各的「CI」互不影响（ux_ai_api_keys_owner_name）。
#   TOKEN_HASH 全局唯一：所有 key 走同一个校验入口，撞哈希等于两条记录互相遮蔽。
#   EXPIRES_AT 可空 = 永不过期；到期与 enabled=0 在鉴权层是同一条 401 路径。
#   LAST_USED_AT 是采样写的（最多每 60 秒回写一次），不是精确审计。
# ---------------------------------------------------------------------------

ai_api_keys = Table(
    "ai_api_keys",
    metadata,
    _oid_pk(),
    Column("user_oid", VARCHAR2(24), nullable=False),
    Column("name", VARCHAR2(64), nullable=False),
    Column("token_hash", VARCHAR2(64), nullable=False),
    Column("key_hint", VARCHAR2(16), nullable=False),
    Column("enabled", NUMBER(1), nullable=False, server_default=text("1")),
    Column("expires_at", oracle.TIMESTAMP, nullable=True),
    Column("last_used_at", oracle.TIMESTAMP, nullable=True),
    Column(
        "created_at",
        oracle.TIMESTAMP,
        nullable=False,
        server_default=text("SYSTIMESTAMP"),
    ),
    _oid_check("ai_api_keys"),
    CheckConstraint("enabled IN (0,1)", name="ck_ai_api_keys_enabled"),
)
Index("ux_ai_api_keys_token_hash", ai_api_keys.c.token_hash, unique=True)
Index(
    "ux_ai_api_keys_owner_name",
    ai_api_keys.c.user_oid,
    ai_api_keys.c.name,
    unique=True,
)


# ---------------------------------------------------------------------------
# AI_AGENT_EVENTS  ← new (agent 每次工具调用的留痕; V033 + V034)
#   一行 = 一次工具调用尝试，**包含被拒/过期/驳回的那些**：只记成功的调用等于
#   看不见「模型在试探白名单外的工具」和「写操作被谁驳回」。
#   OID 是 32 位 uuid4().hex（不是 24 位 ObjectId）：提案 oid 直接作为对外句柄
#   （confirm/reject 的路径参数），与业务实体的 oid 空间分开，避免有人拿活动 id
#   去撞提案。
#   ARGS_HASH 是**完整参数（ARGS_PAYLOAD）的 sha256**：确认时要能重算并比对
#   （防「批准的东西」与「执行的东西」不是同一份）。ARGS_PAYLOAD 是 CLOB，
#   可复原的 canonical JSON；ARGS_SUMMARY 只是 ≤500 的展示副本，永不参与校验。
#   V034 之前的老行没有 ARGS_PAYLOAD，确认时回落到 ARGS_SUMMARY。
#   PREVIEW 是确认界面用的结构化预览（diff / command / row_update …），CLOB；
#   服务端不理解它，也不拿它做校验。
#   APPROVER_OID / CONFIRMED_AT 记录「谁批的、什么时候执行的」：运维写工具必须
#   双人授权（TOTP），活动域两条写工具没有 TOTP，两列为 NULL。
#   REQUEST_ID / ROUND_NO 把同一轮 run 里的多个工具调用串起来。
#   ACTOR_OID 是发起 agent 的那个人，不是模型：提案只能由本人确认。
#   RESULT_SUMMARY / ARGS_SUMMARY 都是**摘要**（长度受限），不落完整报文。
#   STATUS 走 CAS 迁移：proposed → executing → confirmed | failed；驳回
#   proposed → rejected；过期 proposed → expired。终态不可被覆盖。
# ---------------------------------------------------------------------------

ai_agent_events = Table(
    "ai_agent_events",
    metadata,
    Column("oid", VARCHAR2(32), primary_key=True),
    Column("request_id", VARCHAR2(64), nullable=True),
    Column("round_no", _int3, nullable=True),
    Column("tool_name", VARCHAR2(48), nullable=True),
    Column("tool_kind", VARCHAR2(8), nullable=True),
    Column("args_hash", VARCHAR2(64), nullable=True),
    Column("args_summary", VARCHAR2(500), nullable=True),
    Column("args_payload", CLOB, nullable=True),
    Column("preview", CLOB, nullable=True),
    Column("result_summary", VARCHAR2(500), nullable=True),
    Column("status", VARCHAR2(12), nullable=False),
    Column("actor_oid", VARCHAR2(32), nullable=True),
    Column("approver_oid", VARCHAR2(32), nullable=True),
    Column("confirmed_at", oracle.TIMESTAMP, nullable=True),
    Column("created_at", oracle.TIMESTAMP, nullable=False),
    CheckConstraint(
        "tool_kind IS NULL OR tool_kind IN ('read','write')",
        name="ck_ai_agent_events_tool_kind",
    ),
    CheckConstraint(
        "status IN ('executed','proposed','executing','confirmed','rejected',"
        "'expired','failed','denied')",
        name="ck_ai_agent_events_status",
    ),
)
Index("ix_ai_agent_events_request_id", ai_agent_events.c.request_id)
Index(
    "ix_ai_agent_events_status_created",
    ai_agent_events.c.status,
    ai_agent_events.c.created_at,
)


# ---------------------------------------------------------------------------
# AI_PROMPT_AUDIT  ← new (受控的 prompt 正文留存; V033)
#   与 AI_CALL_LOGS「正文一律不落库」并存的唯一例外：这张表是**审计专用**，
#   写入逻辑与保留期由后续监控车道实现（本迁移只建表）。
#   ROLES_SNAPSHOT 与 AI_CALL_LOGS 同义：角色可变，放行理由要冻结。
#   PROMPT_TEXT 是 CLOB：一段对话可能远超 VARCHAR2 的 4000 字符上限。
#   USER_OID / REQUEST_ID 都不建外键：审计材料要活得比账号与调用链久。
# ---------------------------------------------------------------------------

ai_prompt_audit = Table(
    "ai_prompt_audit",
    metadata,
    Column("oid", VARCHAR2(32), primary_key=True),
    Column("request_id", VARCHAR2(64), nullable=True),
    Column("user_oid", VARCHAR2(32), nullable=True),
    Column("roles_snapshot", VARCHAR2(64), nullable=True),
    Column("purpose", VARCHAR2(24), nullable=True),
    Column("prompt_text", CLOB, nullable=True),
    Column("created_at", oracle.TIMESTAMP, nullable=False),
)
Index("ix_ai_prompt_audit_user_created", ai_prompt_audit.c.user_oid, ai_prompt_audit.c.created_at)


# ---------------------------------------------------------------------------
# AI_CONVERSATIONS  ← new (持久会话; V035)
#   一段会话一行，OID 是 32 位 uuid4().hex（与提案 oid 同口径：对外句柄，不是
#   业务 id 空间）。MODE 建会话时定死：chat 走同步 SSE，agent 走后台任务，
#   两者的幂等与计费边界不同，混用会让「这条消息为什么没落库」无解。
#   SUMMARY / SUMMARY_UPTO_SEQ 是压缩水位：≤ UPTO_SEQ 的消息不进模型上下文，
#   只进摘要；0 = 从未压缩。摘要只存会话行，所以 ai_messages 没有 summary 角色。
#   TOKENS_* 是**累加器**（DEFAULT 0 NOT NULL）：0 是真实的「还没用过」，不是
#   未知；CONTEXT_TOKENS 是最近一次请求的 prompt token，用来画上下文水位条。
#   MESSAGE_COUNT 是冗余计数：列表页与删除回执都不该去 COUNT 百万行 CLOB 表。
#   LIST 查询不投影 SUMMARY（CLOB）：列表只要元数据。
# ---------------------------------------------------------------------------

ai_conversations = Table(
    "ai_conversations",
    metadata,
    _uuid_pk(),
    Column("owner_oid", VARCHAR2(24), nullable=False),
    # ``MODE`` 是 Oracle 保留字：裸写建表会 ORA-00904，所以物理名带引号。
    # 用大写名 + ``key="mode"``：Oracle 存的是 MODE（与其他列的折叠形式一致），
    # 手写 SQL 写 ``mode`` 仍能解析；仓储层照常写 ``tbl.c.mode``。
    Column("MODE", VARCHAR2(8), key="mode", nullable=False),
    Column("title", VARCHAR2(120), nullable=True),
    Column("model", VARCHAR2(120), nullable=True),
    Column("status", VARCHAR2(12), nullable=False, server_default=text("'active'")),
    Column("summary", CLOB, nullable=True),
    Column("summary_upto_seq", NUMBER(10), nullable=False, server_default=text("0")),
    Column("tokens_prompt", NUMBER(12), nullable=False, server_default=text("0")),
    Column("tokens_completion", NUMBER(12), nullable=False, server_default=text("0")),
    Column("tokens_total", NUMBER(12), nullable=False, server_default=text("0")),
    Column("context_tokens", NUMBER(12), nullable=False, server_default=text("0")),
    Column("message_count", NUMBER(10), nullable=False, server_default=text("0")),
    Column("compaction_degraded_at", oracle.TIMESTAMP, nullable=True),
    Column("created_at", oracle.TIMESTAMP, nullable=False),
    Column("updated_at", oracle.TIMESTAMP, nullable=False),
    # CHECK 里同样要带引号：Oracle 的表达式解析器对保留字一视同仁（ORA-00904），
    # 而 ddl_check 逐字比较表达式，两侧必须写同一个形状。
    CheckConstraint('"MODE" IN (\'chat\',\'agent\')', name="ck_ai_conversations_mode"),
    CheckConstraint("status IN ('active','archived')", name="ck_ai_conversations_status"),
)
# 左栏列表的唯一查询形状：owner + status 过滤、updated_at 倒序键集分页。
Index(
    "ix_ai_conversations_owner_status_updated",
    ai_conversations.c.owner_oid,
    ai_conversations.c.status,
    ai_conversations.c.updated_at.desc(),
)


# ---------------------------------------------------------------------------
# AI_MESSAGES  ← new (会话内消息; V035)
#   SEQ 会话内从 1 起，UNIQUE(CONVERSATION_OID, SEQ) 由 seq 分配所在的会话行锁
#   兜底：并发写同一会话时宁可 ORA-00001 重试，也不写重号。
#   ROLE 只有 user/assistant/tool —— **没有** summary：摘要是会话级水位，不是一条
#   消息，UI 用 SUMMARY_UPTO_SEQ 画分隔线。
#   CONTENT / TOOL_CALLS 是 CLOB：工具结果全文 ≤8000 字符，超过 VARCHAR2 上限。
#   ROUND_NO 与 ai_call_logs(request_id, round_no) 对齐，逐轮 provider/cache/
#   units 可 join；token 列 NULL = 上游没报（同 ai_call_logs 口径），不是 0。
# ---------------------------------------------------------------------------

ai_messages = Table(
    "ai_messages",
    metadata,
    _uuid_pk(),
    Column("conversation_oid", VARCHAR2(32), nullable=False),
    Column("seq", NUMBER(10), nullable=False),
    Column("role", VARCHAR2(12), nullable=False),
    Column("content", CLOB, nullable=True),
    Column("tool_calls", CLOB, nullable=True),
    Column("tool_call_id", VARCHAR2(64), nullable=True),
    Column("tool_name", VARCHAR2(64), nullable=True),
    Column("round_no", _int3, nullable=True),
    Column("finish_reason", VARCHAR2(24), nullable=True),
    Column("request_id", VARCHAR2(64), nullable=True),
    Column("prompt_tokens", NUMBER(10), nullable=True),
    Column("completion_tokens", NUMBER(10), nullable=True),
    Column("total_tokens", NUMBER(10), nullable=True),
    Column("cache_read_tokens", NUMBER(10), nullable=True),
    Column("cache_write_tokens", NUMBER(10), nullable=True),
    Column("created_at", oracle.TIMESTAMP, nullable=False),
    UniqueConstraint("conversation_oid", "seq", name="uq_ai_messages_conv_seq"),
    CheckConstraint("role IN ('user','assistant','tool')", name="ck_ai_messages_role"),
    CheckConstraint("seq >= 1", name="ck_ai_messages_seq"),
    CheckConstraint("round_no IS NULL OR round_no >= 1", name="ck_ai_messages_round_no"),
)
Index(
    "ix_ai_messages_conv_created",
    ai_messages.c.conversation_oid,
    ai_messages.c.created_at,
)


# ---------------------------------------------------------------------------
# AI_RUNS  ← new (智能体执行任务; V035)
#   OID 同时是 ai_call_logs.request_id 与 ai_agent_events.request_id：一次 run 的
#   调用流水、工具留痕、消息三条链路靠它串起来。
#   活跃唯一性是**函数唯一索引**（uq_ai_runs_active_conv / _owner）：键只在
#   queued|running 时求值，终态行键为 NULL 而 Oracle 不索引全 NULL 键，所以终态
#   行不互斥。应用层的 SELECT ... FOR UPDATE + 检查只是第一道闸，并发两个 POST
#   仍可能都通过；数据库这道闸把第二个变成 ORA-00001 → 409 AI_RUN_CONFLICT。
#   CLIENT_MSG_ID 是客户端幂等键（可空）：重试命中 uq_ai_runs_client_msg → 返回
#   已有 run，不重复写消息、不重复计费。NULL 不参与唯一性。
#   CANCEL_REQUESTED 不是状态：worker 在轮边界与工具执行前读它，已执行的工具
#   不可回滚，所以取消是「不再开始下一轮」，终态由 status 记。
#   HEARTBEAT_AT 由独立 ticker 每 30s 刷新（不能只在轮边界刷：单轮最坏可占满
#   剩余 run deadline），watchdog 用它判僵尸。
# ---------------------------------------------------------------------------

ai_runs = Table(
    "ai_runs",
    metadata,
    _uuid_pk(),
    Column("conversation_oid", VARCHAR2(32), nullable=False),
    Column("owner_oid", VARCHAR2(24), nullable=False),
    Column("client_msg_id", VARCHAR2(64), nullable=True),
    Column("status", VARCHAR2(16), nullable=False),
    Column("model", VARCHAR2(120), nullable=True),
    Column("stop_reason", VARCHAR2(24), nullable=True),
    Column("rounds", _int3, nullable=True),
    Column("error_code", VARCHAR2(40), nullable=True),
    Column("error_message", VARCHAR2(500), nullable=True),
    Column("result_message_oid", VARCHAR2(32), nullable=True),
    Column("prompt_tokens", NUMBER(12), nullable=True),
    Column("completion_tokens", NUMBER(12), nullable=True),
    Column("total_tokens", NUMBER(12), nullable=True),
    Column("cancel_requested", NUMBER(1), nullable=False, server_default=text("0")),
    Column("heartbeat_at", oracle.TIMESTAMP, nullable=True),
    Column("created_at", oracle.TIMESTAMP, nullable=False),
    Column("started_at", oracle.TIMESTAMP, nullable=True),
    Column("finished_at", oracle.TIMESTAMP, nullable=True),
    CheckConstraint(
        "status IN ('queued','running','succeeded','failed','cancelled','interrupted')",
        name="ck_ai_runs_status",
    ),
    CheckConstraint("cancel_requested IN (0,1)", name="ck_ai_runs_cancel_requested"),
)
ai_runs.append_constraint(
    Index(
        "uq_ai_runs_active_conv",
        text(
            "CASE WHEN status IN ('queued','running') THEN conversation_oid END"
        ),
        unique=True,
    )
)
ai_runs.append_constraint(
    Index(
        "uq_ai_runs_active_owner",
        text("CASE WHEN status IN ('queued','running') THEN owner_oid END"),
        unique=True,
    )
)
Index(
    "uq_ai_runs_client_msg",
    ai_runs.c.conversation_oid,
    ai_runs.c.client_msg_id,
    unique=True,
)
Index("ix_ai_runs_owner_status", ai_runs.c.owner_oid, ai_runs.c.status)
Index(
    "ix_ai_runs_conv_created",
    ai_runs.c.conversation_oid,
    ai_runs.c.created_at.desc(),
)
Index("ix_ai_runs_status_heartbeat", ai_runs.c.status, ai_runs.c.heartbeat_at)


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
