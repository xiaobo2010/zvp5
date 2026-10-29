-- ============================================================================
-- V001__initial_schema.sql
-- ----------------------------------------------------------------------------
-- Source collection set : zvms_new (MongoDB)   →   Oracle Autonomous Database
-- Generation date       : 2026-10-12
-- Authoritative spec    : docs/ADB_SCHEMA_SPEC.md §1-§4
--
-- Reconciled decisions (spec §0):
--   * JSON columns    : CLOB + CHECK (col IS JSON)   (SQLAlchemy 2.0 has no
--                       Oracle JSON type to render).
--   * ObjectId (_id)  : VARCHAR2(24) lowercase hex, PK, CHECK REGEXP of
--                       '^[0-9a-f]{24}$'.  (activities.approver carries the
--                       sentinels '' / 'authority', which cannot fit RAW.)
--   * Timestamps      : NAIVE TIMESTAMP (no time zone) - all writes are
--                       datetime.now() naive values; no TZ conversion on ETL.
--   * Foreign keys    : NO ACTION; cascade is enforced in the application.
--
-- Idempotency:
--   Every object is created via EXECUTE IMMEDIATE inside a PL/SQL block that
--   swallows ORA-00955 (name already used by an existing object).  Index
--   creation additionally swallows ORA-01408 (index already exists).  The
--   script is therefore safe to re-run against an instance that already has
--   some or all of these objects.
--
-- DEFERRED: GROUP_INDICATORS.GROUP_OID has NO foreign key in this version.
--   Its current values come from the legacy `zvms.groups` id namespace; the FK
--   to GROUPS(OID) is intentionally deferred until the §7 remap is complete.
--
-- This file is mirrored by db/models.py; run `python -m db.ddl_check` to
-- verify the two stay in sync (offline, no DB access).
-- ============================================================================

-- ----------------------------------------------------------------------------
-- USERS  ← zvms_new.users
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE '
CREATE TABLE users (
  oid VARCHAR2(24 CHAR) NOT NULL,
  business_id VARCHAR2(32 CHAR) NOT NULL,
  name VARCHAR2(64 CHAR) NOT NULL,
  roles CLOB DEFAULT ''[]'' NOT NULL,
  class_oid VARCHAR2(24 CHAR),
  password_hash VARCHAR2(100 CHAR),
  session_id VARCHAR2(64 CHAR),
  sex VARCHAR2(16 CHAR),
  eligibility CLOB,
  past CLOB,
  banned NUMBER(1) DEFAULT 0 NOT NULL,
  ban_reason VARCHAR2(512 CHAR),
  ban_until TIMESTAMP,
  ban_source VARCHAR2(16 CHAR),
  ban_rule VARCHAR2(64 CHAR),
  primary_xuehai_id VARCHAR2(32 CHAR),
  PRIMARY KEY (oid),
  CONSTRAINT ck_users_oid CHECK (REGEXP_LIKE(oid,''^[0-9a-f]{24}$'')),
  CONSTRAINT ck_users_roles_json CHECK (roles IS JSON),
  CONSTRAINT ck_users_eligibility_json CHECK (eligibility IS JSON),
  CONSTRAINT ck_users_past_json CHECK (past IS JSON),
  CONSTRAINT ck_users_banned CHECK (banned IN (0,1)),
  CONSTRAINT uq_users_business_id UNIQUE (business_id),
  CONSTRAINT fk_users_class_oid FOREIGN KEY(class_oid) REFERENCES groups (oid)
)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -955 THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'CREATE INDEX ix_users_class_oid ON users (class_oid)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-955, -1408) THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'CREATE INDEX ix_users_primary_xuehai ON users (primary_xuehai_id)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-955, -1408) THEN RAISE; END IF;
END;
/

-- ----------------------------------------------------------------------------
-- GROUPS  ← zvms_new.groups   (type:"class" is synthesized in code, not stored)
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE '
CREATE TABLE groups (
  oid VARCHAR2(24 CHAR) NOT NULL,
  name VARCHAR2(128 CHAR) NOT NULL,
  type VARCHAR2(16 CHAR),
  permissions CLOB,
  description VARCHAR2(1000 CHAR),
  PRIMARY KEY (oid),
  CONSTRAINT ck_groups_oid CHECK (REGEXP_LIKE(oid,''^[0-9a-f]{24}$'')),
  CONSTRAINT ck_groups_permissions_json CHECK (permissions IS JSON)
)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -955 THEN RAISE; END IF;
END;
/

-- ----------------------------------------------------------------------------
-- ACTIVITIES  ← zvms_new.activities
--   approver has NO FK (carries '' / 'authority' sentinels).
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE '
CREATE TABLE activities (
  oid VARCHAR2(24 CHAR) NOT NULL,
  atype VARCHAR2(16 CHAR) NOT NULL,
  name VARCHAR2(256 CHAR) NOT NULL,
  description VARCHAR2(1000 CHAR),
  activity_date TIMESTAMP NOT NULL,
  created_at TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL,
  updated_at TIMESTAMP NOT NULL,
  appointee_oid VARCHAR2(24 CHAR) NOT NULL,
  approver VARCHAR2(24 CHAR),
  creator_oid VARCHAR2(24 CHAR) NOT NULL,
  status VARCHAR2(16 CHAR) NOT NULL,
  place VARCHAR2(256 CHAR),
  origin VARCHAR2(24 CHAR) NOT NULL,
  PRIMARY KEY (oid),
  CONSTRAINT ck_activities_oid CHECK (REGEXP_LIKE(oid,''^[0-9a-f]{24}$'')),
  CONSTRAINT ck_activities_atype CHECK (atype IN (''on-campus'',''off-campus'',''social-practice'',''hybrid'')),
  CONSTRAINT ck_activities_status CHECK (status IN (''pending'',''effective'',''refused'')),
  CONSTRAINT ck_activities_origin CHECK (origin IN (''labor'',''organization'',''tasks'',''occasions'',''import'',''activities'',''practice'',''club'',''prize'',''other'',''unknown''))
)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -955 THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'CREATE INDEX ix_activities_status ON activities (status)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-955, -1408) THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'CREATE INDEX ix_activities_atype ON activities (atype)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-955, -1408) THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'CREATE INDEX ix_activities_creator_oid ON activities (creator_oid)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-955, -1408) THEN RAISE; END IF;
END;
/

-- ----------------------------------------------------------------------------
-- ACTIVITY_MEMBERS  ← zvms_new.activity_members
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE '
CREATE TABLE activity_members (
  oid VARCHAR2(24 CHAR) NOT NULL,
  activity_oid VARCHAR2(24 CHAR) NOT NULL,
  member_oid VARCHAR2(24 CHAR) NOT NULL,
  member_status VARCHAR2(16 CHAR) NOT NULL,
  member_mode VARCHAR2(16 CHAR) NOT NULL,
  duration_hours BINARY_DOUBLE NOT NULL,
  PRIMARY KEY (oid),
  CONSTRAINT ck_activity_members_oid CHECK (REGEXP_LIKE(oid,''^[0-9a-f]{24}$'')),
  CONSTRAINT ck_activity_members_member_status CHECK (member_status IN (''effective'',''pending'',''refused'')),
  CONSTRAINT ck_activity_members_member_mode CHECK (member_mode IN (''on-campus'',''off-campus'',''social-practice'')),
  CONSTRAINT uq_activity_members_dedup UNIQUE (member_oid, activity_oid, member_mode),
  CONSTRAINT fk_activity_members_activity_oid FOREIGN KEY(activity_oid) REFERENCES activities (oid)
)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -955 THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'CREATE INDEX ix_activity_members_activity_oid ON activity_members (activity_oid)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-955, -1408) THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'CREATE INDEX ix_activity_members_member_oid ON activity_members (member_oid)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-955, -1408) THEN RAISE; END IF;
END;
/

-- ----------------------------------------------------------------------------
-- USER_TIME  ← zvms_new.time   (table renamed TIME -> USER_TIME for clarity)
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE '
CREATE TABLE user_time (
  oid VARCHAR2(24 CHAR) NOT NULL,
  user_oid VARCHAR2(24 CHAR) NOT NULL,
  on_campus_raw BINARY_DOUBLE DEFAULT 0 NOT NULL,
  off_campus_raw BINARY_DOUBLE DEFAULT 0 NOT NULL,
  social_practice BINARY_DOUBLE DEFAULT 0 NOT NULL,
  updated_at TIMESTAMP NOT NULL,
  PRIMARY KEY (oid),
  CONSTRAINT ck_user_time_oid CHECK (REGEXP_LIKE(oid,''^[0-9a-f]{24}$'')),
  CONSTRAINT uq_user_time_user_oid UNIQUE (user_oid),
  CONSTRAINT fk_user_time_user_oid FOREIGN KEY(user_oid) REFERENCES users (oid)
)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -955 THEN RAISE; END IF;
END;
/

-- ----------------------------------------------------------------------------
-- TIME_ACADEMIC_YEAR  ← zvms_new.time_academic_year
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE '
CREATE TABLE time_academic_year (
  oid VARCHAR2(24 CHAR) NOT NULL,
  user_oid VARCHAR2(24 CHAR) NOT NULL,
  entry_year VARCHAR2(8 CHAR) NOT NULL,
  academic_year NUMBER(4) NOT NULL,
  start_of_year TIMESTAMP NOT NULL,
  end_of_year TIMESTAMP NOT NULL,
  on_campus BINARY_DOUBLE NOT NULL,
  off_campus BINARY_DOUBLE NOT NULL,
  social_practice BINARY_DOUBLE NOT NULL,
  PRIMARY KEY (oid),
  CONSTRAINT ck_time_academic_year_oid CHECK (REGEXP_LIKE(oid,''^[0-9a-f]{24}$'')),
  CONSTRAINT uq_time_academic_year_user_year UNIQUE (user_oid, academic_year),
  CONSTRAINT fk_time_academic_year_user_oid FOREIGN KEY(user_oid) REFERENCES users (oid)
)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -955 THEN RAISE; END IF;
END;
/

-- ----------------------------------------------------------------------------
-- TIME_WITH_ORIGIN  ← zvms_new.time_with_origin
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE '
CREATE TABLE time_with_origin (
  oid VARCHAR2(24 CHAR) NOT NULL,
  user_oid VARCHAR2(24 CHAR) NOT NULL,
  data CLOB NOT NULL,
  updated_at TIMESTAMP NOT NULL,
  PRIMARY KEY (oid),
  CONSTRAINT ck_time_with_origin_oid CHECK (REGEXP_LIKE(oid,''^[0-9a-f]{24}$'')),
  CONSTRAINT ck_time_with_origin_data_json CHECK (data IS JSON),
  CONSTRAINT uq_time_with_origin_user_oid UNIQUE (user_oid),
  CONSTRAINT fk_time_with_origin_user_oid FOREIGN KEY(user_oid) REFERENCES users (oid)
)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -955 THEN RAISE; END IF;
END;
/

-- ----------------------------------------------------------------------------
-- TIME_WITH_ORIGIN_STAT  ← zvms_new.time_with_origin_stat
--   scope_id is polymorphic (grade year / hex group id) - NO FK by design.
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE '
CREATE TABLE time_with_origin_stat (
  oid VARCHAR2(24 CHAR) NOT NULL,
  stat_key VARCHAR2(64 CHAR) NOT NULL,
  scope VARCHAR2(8 CHAR) NOT NULL,
  scope_id VARCHAR2(24 CHAR) NOT NULL,
  require_percentage NUMBER(3) DEFAULT 0 NOT NULL,
  data CLOB NOT NULL,
  updated_at TIMESTAMP NOT NULL,
  PRIMARY KEY (oid),
  CONSTRAINT ck_time_with_origin_stat_oid CHECK (REGEXP_LIKE(oid,''^[0-9a-f]{24}$'')),
  CONSTRAINT ck_time_with_origin_stat_scope CHECK (scope IN (''grade'',''group'')),
  CONSTRAINT ck_time_with_origin_stat_data_json CHECK (data IS JSON),
  CONSTRAINT uq_time_with_origin_stat_stat_key UNIQUE (stat_key)
)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -955 THEN RAISE; END IF;
END;
/

-- ----------------------------------------------------------------------------
-- GROUP_INDICATORS  ← zvms_new.group_indicators
--   Current GROUP_OID values are legacy `zvms.groups` ids: the FK to
--   GROUPS(OID) is intentionally DEFERRED until the §7 remap lands.
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE '
CREATE TABLE group_indicators (
  oid VARCHAR2(24 CHAR) NOT NULL,
  group_oid VARCHAR2(24 CHAR) NOT NULL,
  percentiles CLOB,
  indicators CLOB,
  updated_at TIMESTAMP,
  PRIMARY KEY (oid),
  CONSTRAINT ck_group_indicators_oid CHECK (REGEXP_LIKE(oid,''^[0-9a-f]{24}$'')),
  CONSTRAINT ck_group_indicators_percentiles_json CHECK (percentiles IS JSON),
  CONSTRAINT ck_group_indicators_indicators_json CHECK (indicators IS JSON),
  CONSTRAINT uq_group_indicators_group_oid UNIQUE (group_oid)
)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -955 THEN RAISE; END IF;
END;
/

-- ----------------------------------------------------------------------------
-- INDICATORS  ← zvms_new.indicators   (single-row table, enforced in code)
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE '
CREATE TABLE indicators (
  oid VARCHAR2(24 CHAR) NOT NULL,
  percentiles CLOB,
  indicators CLOB,
  updated_at TIMESTAMP,
  PRIMARY KEY (oid),
  CONSTRAINT ck_indicators_oid CHECK (REGEXP_LIKE(oid,''^[0-9a-f]{24}$'')),
  CONSTRAINT ck_indicators_percentiles_json CHECK (percentiles IS JSON),
  CONSTRAINT ck_indicators_indicators_json CHECK (indicators IS JSON)
)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -955 THEN RAISE; END IF;
END;
/

-- ----------------------------------------------------------------------------
-- DAILY_EXPORTS  ← zvms_new.daily_exports
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE '
CREATE TABLE daily_exports (
  oid VARCHAR2(24 CHAR) NOT NULL,
  created_at TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL,
  status VARCHAR2(16 CHAR) NOT NULL,
  data CLOB DEFAULT ''[]'' NOT NULL,
  PRIMARY KEY (oid),
  CONSTRAINT ck_daily_exports_oid CHECK (REGEXP_LIKE(oid,''^[0-9a-f]{24}$'')),
  CONSTRAINT ck_daily_exports_data_json CHECK (data IS JSON)
)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -955 THEN RAISE; END IF;
END;
/

-- ----------------------------------------------------------------------------
-- CHANGELOG  ← zvms_new.changelog   (release version + release notes)
--   release_date is an ISO YYYY-MM-DD string (Mongo key "date").
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE '
CREATE TABLE changelog (
  oid VARCHAR2(24 CHAR) NOT NULL,
  version VARCHAR2(32 CHAR) NOT NULL,
  release_date VARCHAR2(16 CHAR) NOT NULL,
  items CLOB DEFAULT ''[]'' NOT NULL,
  updated_at TIMESTAMP NOT NULL,
  PRIMARY KEY (oid),
  CONSTRAINT ck_changelog_oid CHECK (REGEXP_LIKE(oid,''^[0-9a-f]{24}$'')),
  CONSTRAINT ck_changelog_items_json CHECK (items IS JSON)
)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -955 THEN RAISE; END IF;
END;
/

-- ----------------------------------------------------------------------------
-- APP_LOGS  ← zvms_new.app_logs   (runtime app logs; written by typings/log.py)
--   `user`/`type`/`level` are Oracle-awkward keys -> renamed APP_USER /
--   LTYPE / LOG_LEVEL.  TS_RAW (epoch seconds) is the authoritative time column:
--   ordering, range filters and the retention purge all use it; TS_AT is only the
--   derived naive TIMESTAMP kept as data.
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE '
CREATE TABLE app_logs (
  oid VARCHAR2(24 CHAR) NOT NULL,
  url VARCHAR2(512 CHAR),
  app_user VARCHAR2(64 CHAR),
  data VARCHAR2(2000 CHAR),
  ts_at TIMESTAMP,
  ts_raw BINARY_DOUBLE,
  ltype VARCHAR2(64 CHAR),
  log_level VARCHAR2(32 CHAR),
  xuehai_id VARCHAR2(32 CHAR),
  client_ip VARCHAR2(45 CHAR),
  user_agent VARCHAR2(256 CHAR),
  PRIMARY KEY (oid),
  CONSTRAINT ck_app_logs_oid CHECK (REGEXP_LIKE(oid,''^[0-9a-f]{24}$''))
)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -955 THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'CREATE INDEX ix_app_logs_xuehai_id ON app_logs (xuehai_id)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-955, -1408) THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'CREATE INDEX ix_app_logs_user_ts ON app_logs (app_user, ts_raw)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-955, -1408) THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'CREATE INDEX ix_app_logs_ts_raw ON app_logs (ts_raw)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-955, -1408) THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'CREATE INDEX ix_app_logs_type_level_ts ON app_logs (ltype, log_level, ts_raw)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-955, -1408) THEN RAISE; END IF;
END;
/

-- ----------------------------------------------------------------------------
-- EXPORT_TASKS  ← zvms_new.tasks   (runtime export queue; `id` is a BSON UUID)
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE '
CREATE TABLE export_tasks (
  oid VARCHAR2(24 CHAR) NOT NULL,
  task_uuid VARCHAR2(36 CHAR) NOT NULL,
  status VARCHAR2(16 CHAR) NOT NULL,
  percentage BINARY_DOUBLE,
  variant VARCHAR2(32 CHAR),
  export_format VARCHAR2(16 CHAR),
  allow_cache NUMBER(1) DEFAULT 0 NOT NULL,
  include_description NUMBER(1) DEFAULT 0 NOT NULL,
  errmsg VARCHAR2(1000 CHAR),
  export_start TIMESTAMP,
  export_end TIMESTAMP,
  task_start TIMESTAMP,
  task_end TIMESTAMP,
  result CLOB DEFAULT ''[]'' NOT NULL,
  PRIMARY KEY (oid),
  CONSTRAINT ck_export_tasks_oid CHECK (REGEXP_LIKE(oid,''^[0-9a-f]{24}$'')),
  CONSTRAINT ck_export_tasks_result_json CHECK (result IS JSON),
  CONSTRAINT uq_export_tasks_uuid UNIQUE (task_uuid)
)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -955 THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'CREATE INDEX ix_export_tasks_status ON export_tasks (status)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-955, -1408) THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'CREATE INDEX ix_export_tasks_task_start ON export_tasks (task_start)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-955, -1408) THEN RAISE; END IF;
END;
/

-- ----------------------------------------------------------------------------
-- IMPORTS  ← zvms_new.imports   (written by routers/imports_router.py)
--   `date` (an ISO string in Mongo) -> IMPORT_DATE; `content` is BSON Binary.
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE '
CREATE TABLE imports (
  oid VARCHAR2(24 CHAR) NOT NULL,
  import_date TIMESTAMP,
  description VARCHAR2(1000 CHAR),
  filename VARCHAR2(256 CHAR),
  name VARCHAR2(256 CHAR),
  user_oid VARCHAR2(24 CHAR),
  content BLOB,
  PRIMARY KEY (oid),
  CONSTRAINT ck_imports_oid CHECK (REGEXP_LIKE(oid,''^[0-9a-f]{24}$''))
)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -955 THEN RAISE; END IF;
END;
/

-- ----------------------------------------------------------------------------
-- FILES  ← new (Files/attachments API; docs/FILES_API_DESIGN.md §3.1)
--   Photo/attachment metadata + normalised bytes.  `content` is the first BLOB
--   *read* in the codebase (IMPORTS only ever wrote one); see
--   db/repos/oracle/files.py.  `status` is the single source of truth (the
--   file_events rows are an audit copy only).  owner_oid carries NO FK (same
--   relaxation as V006).  Also added by V007 for already-deployed instances.
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE '
CREATE TABLE files (
  oid VARCHAR2(24 CHAR) NOT NULL,
  owner_oid VARCHAR2(24 CHAR),
  kind VARCHAR2(16 CHAR) NOT NULL,
  parent_oid VARCHAR2(24 CHAR),
  mime VARCHAR2(64 CHAR) NOT NULL,
  size_bytes NUMBER(19) NOT NULL,
  width NUMBER(10),
  height NUMBER(10),
  sha256 VARCHAR2(64 CHAR) NOT NULL,
  content BLOB,
  original_name VARCHAR2(255 CHAR),
  purpose VARCHAR2(32 CHAR),
  entity_type VARCHAR2(32 CHAR),
  entity_oid VARCHAR2(24 CHAR),
  status VARCHAR2(16 CHAR) NOT NULL,
  ocr_status VARCHAR2(16 CHAR) NOT NULL,
  ocr_retry NUMBER(3) DEFAULT 0 NOT NULL,
  created_at TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL,
  updated_at TIMESTAMP,
  deleted_at TIMESTAMP,
  PRIMARY KEY (oid),
  CONSTRAINT ck_files_oid CHECK (REGEXP_LIKE(oid,''^[0-9a-f]{24}$'')),
  CONSTRAINT ck_files_kind CHECK (kind IN (''original'',''thumb'',''derived'')),
  CONSTRAINT ck_files_status CHECK (status IN (''stored'',''processing'',''ready'',''verified'',''rejected'',''quarantined'',''deleted'')),
  CONSTRAINT ck_files_ocr_status CHECK (ocr_status IN (''pending'',''processing'',''done'',''failed''))
)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -955 THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'CREATE INDEX ix_files_owner ON files (owner_oid, created_at)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-955, -1408) THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'CREATE INDEX ix_files_entity ON files (entity_type, entity_oid)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-955, -1408) THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'CREATE INDEX ix_files_sha256 ON files (owner_oid, sha256)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-955, -1408) THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'CREATE INDEX ix_files_parent ON files (parent_oid)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-955, -1408) THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'CREATE INDEX ix_files_cleanup ON files (status, deleted_at)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-955, -1408) THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'CREATE INDEX ix_files_ocr ON files (ocr_status)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-955, -1408) THEN RAISE; END IF;
END;
/

-- ----------------------------------------------------------------------------
-- FILE_OCR  ← new (per-file OCR results; docs/FILES_API_DESIGN.md §3.1/§7)
--   raw_json / confidence are written by the (pluggable) OCR engine; ocr_text
--   is the extracted text.  Also added by V007.
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE '
CREATE TABLE file_ocr (
  oid VARCHAR2(24 CHAR) NOT NULL,
  file_oid VARCHAR2(24 CHAR) NOT NULL,
  engine VARCHAR2(64 CHAR),
  engine_version VARCHAR2(64 CHAR),
  ocr_text CLOB,
  confidence BINARY_DOUBLE,
  raw_json CLOB,
  created_at TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL,
  PRIMARY KEY (oid),
  CONSTRAINT ck_file_ocr_oid CHECK (REGEXP_LIKE(oid,''^[0-9a-f]{24}$'')),
  CONSTRAINT ck_file_ocr_raw_json CHECK (raw_json IS JSON)
)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -955 THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'CREATE INDEX ix_file_ocr_file ON file_ocr (file_oid)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-955, -1408) THEN RAISE; END IF;
END;
/

-- ----------------------------------------------------------------------------
-- FILE_EVENTS  ← new (audit trail; insert + list only, docs/FILES_API_DESIGN.md
--   §8.5).  An append-only copy of every state transition; the `files.status`
--   column remains the source of truth for gating.  Also added by V007.
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE '
CREATE TABLE file_events (
  oid VARCHAR2(24 CHAR) NOT NULL,
  file_oid VARCHAR2(24 CHAR) NOT NULL,
  actor_oid VARCHAR2(24 CHAR),
  action VARCHAR2(24 CHAR) NOT NULL,
  from_status VARCHAR2(16 CHAR),
  to_status VARCHAR2(16 CHAR),
  detail CLOB,
  created_at TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL,
  PRIMARY KEY (oid),
  CONSTRAINT ck_file_events_oid CHECK (REGEXP_LIKE(oid,''^[0-9a-f]{24}$'')),
  CONSTRAINT ck_file_events_detail CHECK (detail IS JSON)
)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -955 THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'CREATE INDEX ix_file_events_file ON file_events (file_oid, created_at)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-955, -1408) THEN RAISE; END IF;
END;
/

-- ----------------------------------------------------------------------------
-- USER_DEVICE_BINDINGS  ← new (学海 ID 身份追溯 MVP; observe-only)
--   Records which 学海 device id a user logs in from.  Added for login-time
--   observation only (no login is ever blocked); also added by
--   V010__add_user_device_bindings.sql for already-deployed instances.
--   user_oid -> USERS(OID) is a real FK: the table starts empty, so no legacy
--   id can violate it.  A revoked row is kept (status='revoked'); the
--   (user_oid, xuehai_id) pair is unique so a re-bind reactivates in place.
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE '
CREATE TABLE user_device_bindings (
  oid VARCHAR2(24 CHAR) NOT NULL,
  user_oid VARCHAR2(24 CHAR) NOT NULL,
  xuehai_id VARCHAR2(32 CHAR) NOT NULL,
  device_id VARCHAR2(64 CHAR),
  source VARCHAR2(16 CHAR) DEFAULT ''auto'' NOT NULL,
  status VARCHAR2(16 CHAR) DEFAULT ''active'' NOT NULL,
  note VARCHAR2(256 CHAR),
  created_at TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL,
  updated_at TIMESTAMP NOT NULL,
  PRIMARY KEY (oid),
  CONSTRAINT ck_udb_oid CHECK (REGEXP_LIKE(oid,''^[0-9a-f]{24}$'')),
  CONSTRAINT ck_udb_source CHECK (source IN (''auto'',''admin'')),
  CONSTRAINT ck_udb_status CHECK (status IN (''active'',''revoked'')),
  CONSTRAINT uq_udb_user_xuehai UNIQUE (user_oid, xuehai_id),
  CONSTRAINT fk_udb_user FOREIGN KEY (user_oid) REFERENCES users (oid)
)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -955 THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'CREATE INDEX ix_udb_xuehai_id ON user_device_bindings (xuehai_id)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-955, -1408) THEN RAISE; END IF;
END;
/

-- ----------------------------------------------------------------------------
-- DUAL_AUTH_USES  ← new (Stage 2 双人授权)
--   One row per accepted TOTP window for a cross-admin approval.  The unique
--   (approver_oid, otp_counter) constraint is the atomic replay guard; also
--   added by V012__add_dual_auth_uses.sql for already-deployed instances.
--   No FK on purpose: the row is an audit trail and must survive deletion of
--   either account.
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE '
CREATE TABLE dual_auth_uses (
  oid VARCHAR2(24 CHAR) NOT NULL,
  approver_oid VARCHAR2(24 CHAR) NOT NULL,
  otp_counter NUMBER(19) NOT NULL,
  action VARCHAR2(64 CHAR) NOT NULL,
  operator_oid VARCHAR2(24 CHAR) NOT NULL,
  created_at TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL,
  PRIMARY KEY (oid),
  CONSTRAINT uq_dual_approver_counter UNIQUE (approver_oid, otp_counter)
)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -955 THEN RAISE; END IF;
END;
/

-- ----------------------------------------------------------------------------
-- BAN_RULES  ← new (规则封禁配置)
--   一行一条自动封禁规则。CONFIG / PENALTIES 是**有界** JSON：用
--   VARCHAR2(2000 CHAR) 而不是 CLOB —— 配置体量固定且总是整体读取，LOB
--   locator 只会多一次往返。CODE 是管理员看到的名字/稳定身份，TRIGGER 是
--   求值入口引用的检查项（V016）：同一检查项最多一条规则，否则「用哪条」
--   只能靠运气。TRIGGER 可空是给存量行加列留的余地（唯一索引允许多个 NULL）。
--   也由 V013__add_ban_rules.sql / V016__rule_engine_and_violations.sql 在
--   存量实例上创建。
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE '
CREATE TABLE ban_rules (
  oid VARCHAR2(24 CHAR) NOT NULL,
  code VARCHAR2(64 CHAR) NOT NULL,
  name VARCHAR2(128 CHAR) NOT NULL,
  trigger_code VARCHAR2(64 CHAR),
  enabled NUMBER(1) DEFAULT 1 NOT NULL,
  config VARCHAR2(2000 CHAR) NOT NULL,
  penalties VARCHAR2(2000 CHAR) NOT NULL,
  builtin NUMBER(1) DEFAULT 0 NOT NULL,
  created_at TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL,
  updated_at TIMESTAMP NOT NULL,
  PRIMARY KEY (oid),
  CONSTRAINT ck_ban_rules_oid CHECK (REGEXP_LIKE(oid,''^[0-9a-f]{24}$'')),
  CONSTRAINT ck_ban_rules_enabled CHECK (enabled IN (0,1)),
  CONSTRAINT ck_ban_rules_builtin CHECK (builtin IN (0,1)),
  CONSTRAINT ck_ban_rules_config_json CHECK (config IS JSON),
  CONSTRAINT ck_ban_rules_penalties_json CHECK (penalties IS JSON),
  CONSTRAINT uq_ban_rules_code UNIQUE (code)
)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -955 THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE
    'CREATE UNIQUE INDEX ux_ban_rules_trigger ON ban_rules (trigger_code)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-955, -1408) THEN RAISE; END IF;
END;
/

-- ----------------------------------------------------------------------------
-- BAN_RECORDS  ← new (封禁流水/审计)
--   每次封禁一行，是「累犯升级处罚」的依据：同一 (user, rule) 的历史行数
--   决定下一档 penalty。USER_OID 带真外键（表从空开始，不会有历史脏 id）；
--   其余列刻意无约束，保留判定当时的原貌。
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE '
CREATE TABLE ban_records (
  oid VARCHAR2(24 CHAR) NOT NULL,
  user_oid VARCHAR2(24 CHAR) NOT NULL,
  source VARCHAR2(16 CHAR) NOT NULL,
  rule_code VARCHAR2(64 CHAR),
  reason VARCHAR2(512 CHAR),
  permanent NUMBER(1) DEFAULT 0 NOT NULL,
  started_at TIMESTAMP NOT NULL,
  expires_at TIMESTAMP,
  operator_oid VARCHAR2(24 CHAR),
  created_at TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL,
  PRIMARY KEY (oid),
  CONSTRAINT ck_ban_records_oid CHECK (REGEXP_LIKE(oid,''^[0-9a-f]{24}$'')),
  CONSTRAINT ck_ban_records_source CHECK (source IN (''admin'',''rule'',''agent'')),
  CONSTRAINT ck_ban_records_permanent CHECK (permanent IN (0,1)),
  CONSTRAINT fk_ban_records_user FOREIGN KEY (user_oid) REFERENCES users (oid)
)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -955 THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'CREATE INDEX ix_ban_records_user ON ban_records (user_oid)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-955, -1408) THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'CREATE INDEX ix_ban_records_started ON ban_records (started_at)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-955, -1408) THEN RAISE; END IF;
END;
/

-- ----------------------------------------------------------------------------
-- TEMP_LOGIN_PASSWORDS  ← new (一次性临时密码)
--   只存 bcrypt 哈希，明文只在创建响应里出现一次。单次使用 + 30 分钟过期 +
--   可撤销三个时间戳，而不是一个状态枚举：「用过/过期/被撤销」在审计上是三种
--   事实，合并会丢时间点。无外键：签发人与目标用户都可能被硬删除，而这行是
--   审计材料。也由 V014__device_binding_and_temp_password.sql 创建。
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE '
CREATE TABLE temp_login_passwords (
  oid VARCHAR2(24 CHAR) NOT NULL,
  target_user_oid VARCHAR2(24 CHAR) NOT NULL,
  created_by_oid VARCHAR2(24 CHAR) NOT NULL,
  code_hash VARCHAR2(100 CHAR) NOT NULL,
  creator_xuehai_id VARCHAR2(32 CHAR),
  expires_at TIMESTAMP NOT NULL,
  used_at TIMESTAMP,
  revoked_at TIMESTAMP,
  created_at TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL,
  PRIMARY KEY (oid),
  CONSTRAINT ck_temp_login_passwords_oid CHECK (REGEXP_LIKE(oid,''^[0-9a-f]{24}$''))
)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -955 THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'CREATE INDEX ix_tlp_target ON temp_login_passwords (target_user_oid, expires_at)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-955, -1408) THEN RAISE; END IF;
END;
/

-- ----------------------------------------------------------------------------
-- DEVICE_CHALLENGES  ← new (异设备登录的一次性挑战)
--   TOKEN_HASH 存 SHA-256 hex：挑战值等价于一次性登录许可，泄露即可被抢用。
--   唯一索引既是查找路径也是防重放裁决者。ATTEMPTS 落在库里（不能放进程内存），
--   服务层在行锁内递增，第 3 次同时封账号与设备。
--   也由 V014__device_binding_and_temp_password.sql 创建。
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE '
CREATE TABLE device_challenges (
  oid VARCHAR2(24 CHAR) NOT NULL,
  token_hash VARCHAR2(64 CHAR) NOT NULL,
  user_oid VARCHAR2(24 CHAR) NOT NULL,
  xuehai_id VARCHAR2(32 CHAR) NOT NULL,
  attempts NUMBER(3) DEFAULT 0 NOT NULL,
  expires_at TIMESTAMP NOT NULL,
  consumed_at TIMESTAMP,
  created_at TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL,
  PRIMARY KEY (oid),
  CONSTRAINT ck_device_challenges_oid CHECK (REGEXP_LIKE(oid,''^[0-9a-f]{24}$''))
)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -955 THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'CREATE UNIQUE INDEX ux_device_challenges_token ON device_challenges (token_hash)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-955, -1408) THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'CREATE INDEX ix_device_challenges_user_created ON device_challenges (user_oid, created_at)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-955, -1408) THEN RAISE; END IF;
END;
/

-- ----------------------------------------------------------------------------
-- BANNED_DEVICES  ← new (设备级封禁)
--   主键就是学海设备号：一台设备只有一条当前状态，重复封禁是覆盖而非追加。
--   UNTIL NULL = 永久；SOURCE 区分规则判罚与管理员手动封禁。账号封禁**不**镜像
--   到这里，由服务层按 USERS.PRIMARY_XUEHAI_ID 实时推导（账号解封/换设备后
--   自动解除）。无 OID、无外键：设备号是外部标识，删用户不应连带删设备封禁。
--   也由 V014__device_binding_and_temp_password.sql 创建。
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE '
CREATE TABLE banned_devices (
  xuehai_id VARCHAR2(32 CHAR) NOT NULL,
  reason VARCHAR2(512 CHAR),
  until TIMESTAMP,
  source VARCHAR2(16 CHAR) NOT NULL,
  rule_code VARCHAR2(64 CHAR),
  operator_oid VARCHAR2(24 CHAR),
  created_at TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL,
  PRIMARY KEY (xuehai_id),
  CONSTRAINT ck_banned_devices_source CHECK (source IN (''rule'',''admin'',''agent''))
)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -955 THEN RAISE; END IF;
END;
/

-- ----------------------------------------------------------------------------
-- DEVICE_BAN_COUNTERS  ← new (设备侧处罚梯度)
--   一台设备一行：BAN_COUNT 决定下一次自动封禁用第几档 [1,3,7,14,30,null]。
--   与 BANNED_DEVICES 分表：解封只删当前封禁行，历史违规次数留着；清零走
--   管理员 reset-gradient。无外键：设备号是外部标识。
--   也由 V039__device_ban_counters.sql 创建。
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE '
CREATE TABLE device_ban_counters (
  xuehai_id VARCHAR2(32 CHAR) NOT NULL,
  ban_count NUMBER(5) DEFAULT 0 NOT NULL,
  last_banned_at TIMESTAMP,
  PRIMARY KEY (xuehai_id)
)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -955 THEN RAISE; END IF;
END;
/

-- ----------------------------------------------------------------------------
-- BAN_VIOLATIONS  ← new (客户端上报的违规信号)
--   一条一行，append-only；命中「窗口内累计计数」后由规则梯度决定封禁。
--   计数单位是 BATCH_ID（一次上报 = 一批，V019）：同一次页面加载里的
--   iframe+bridge+devtools 只该算一次违规。无外键：违规记录是审计材料，
--   必须比账号活得久（用户被硬删除后仍要能回答「他当时因为什么被封」）。
--   SIGNAL 是 iframe / bridge / devtools，DETAIL 存判定时的原始依据。
--   (user_oid, trigger_code, created_at, batch_id) 的组合索引服务「窗口内计数」
--   这条热路径，BATCH_ID 作为第四列让 COUNT(DISTINCT ...) 不必回表；
--   created_at 单列索引服务管理页的「全表按时间倒序」。两者不重叠：
--   前者以 user_oid 打头，排序场景只能走后者。
--   也由 V016__rule_engine_and_violations.sql / V019__harden_ban_engine.sql 在
--   存量实例上创建（V047 把前缀冗余的 ix_ban_violations_user 换成本索引）。
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE '
CREATE TABLE ban_violations (
  oid VARCHAR2(24 CHAR) NOT NULL,
  user_oid VARCHAR2(24 CHAR) NOT NULL,
  trigger_code VARCHAR2(64 CHAR) NOT NULL,
  signal VARCHAR2(32 CHAR) NOT NULL,
  xuehai_id VARCHAR2(32 CHAR),
  detail VARCHAR2(500 CHAR),
  batch_id VARCHAR2(32 CHAR),
  created_at TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL,
  PRIMARY KEY (oid),
  CONSTRAINT ck_ban_violations_oid CHECK (REGEXP_LIKE(oid,''^[0-9a-f]{24}$''))
)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -955 THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE
    'CREATE INDEX ix_ban_violations_batch ON ban_violations (user_oid, trigger_code, created_at, batch_id)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-955, -1408) THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE
    'CREATE INDEX ix_ban_violations_created ON ban_violations (created_at)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-955, -1408) THEN RAISE; END IF;
END;
/

-- ----------------------------------------------------------------------------
-- USER_WARNINGS  ← new (警告账本; V041)
--   每 (用户, 规则, 自然月) 最多一行；月度归零靠唯一索引，没有清理任务。
--   唯一冲突是预期控制流（本月已警告过），由仓储层翻译成 False。
--   也由 V041__user_warnings.sql 在存量实例上创建。
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE '
CREATE TABLE user_warnings (
  oid VARCHAR2(24 CHAR) NOT NULL,
  user_oid VARCHAR2(24 CHAR) NOT NULL,
  rule_code VARCHAR2(64 CHAR) NOT NULL,
  period CHAR(6 CHAR) NOT NULL,
  reason VARCHAR2(512 CHAR),
  detail VARCHAR2(500 CHAR),
  created_at TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL,
  PRIMARY KEY (oid),
  CONSTRAINT ck_user_warnings_oid CHECK (REGEXP_LIKE(oid,''^[0-9a-f]{24}$'')),
  CONSTRAINT ck_user_warnings_period CHECK (REGEXP_LIKE(period,''^[0-9]{6}$''))
)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -955 THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE
    'CREATE UNIQUE INDEX ux_user_warnings_user_rule_period ON user_warnings (user_oid, rule_code, period)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-955, -1408) THEN RAISE; END IF;
END;
/

-- ----------------------------------------------------------------------------
-- NOTIFICATIONS  ← new (站内通知)
--   管理员发布的站内通知。AUTHOR_OID / AUTHOR_NAME 是发布当时的事实，写入后
--   不再修改；不建 FK 是为了账号被删除后历史通知仍能回答「谁发的」。
--   VERSION 是单条通知的修订号：创建为 1，每次成功编辑/停用 +1，客户端据此
--   区分「已阅过的旧内容」与「编辑后的新内容」。
--   SOURCE_REF 指向生成这条公告的来源对象（当前只有 CHANGELOG.OID，V038 补
--   该列）：NULL = 管理员手写，非空 = 自动同步且一个来源最多一条。
--   也由 V015__add_notifications.sql 在存量实例上创建。
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE '
CREATE TABLE notifications (
  oid VARCHAR2(24 CHAR) NOT NULL,
  title VARCHAR2(200 CHAR) NOT NULL,
  content VARCHAR2(4000 CHAR) NOT NULL,
  active NUMBER(1) DEFAULT 1 NOT NULL,
  author_oid VARCHAR2(24 CHAR) NOT NULL,
  author_name VARCHAR2(128 CHAR) NOT NULL,
  version NUMBER(19) DEFAULT 1 NOT NULL,
  source_ref VARCHAR2(24 CHAR),
  created_at TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL,
  updated_at TIMESTAMP NOT NULL,
  PRIMARY KEY (oid),
  CONSTRAINT ck_notifications_oid CHECK (REGEXP_LIKE(oid,''^[0-9a-f]{24}$'')),
  CONSTRAINT ck_notifications_active CHECK (active IN (0,1)),
  CONSTRAINT ck_notifications_version CHECK (version > 0),
  CONSTRAINT uq_notifications_source_ref UNIQUE (source_ref)
)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -955 THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'CREATE INDEX ix_notifications_active_created ON notifications (active, created_at)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-955, -1408) THEN RAISE; END IF;
END;
/

-- ----------------------------------------------------------------------------
-- NOTIFICATION_STATE  ← new (通知全局版本，单行 ID = 1)
--   每次成功的通知创建/编辑/删除与业务行在同一事务里 VERSION + 1；客户端只
--   比较这个数字就知道「有没有变化」，不必轮询整张表。
--   也由 V015__add_notifications.sql 在存量实例上创建并播种。
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE '
CREATE TABLE notification_state (
  id NUMBER(1) NOT NULL,
  version NUMBER(19) DEFAULT 0 NOT NULL,
  updated_at TIMESTAMP NOT NULL,
  PRIMARY KEY (id),
  CONSTRAINT ck_notification_state_id CHECK (id = 1),
  CONSTRAINT ck_notification_state_version CHECK (version >= 0)
)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -955 THEN RAISE; END IF;
END;
/

-- ----------------------------------------------------------------------------
-- NOTIFICATION_STATE seed  ← V015 mirror
--   MERGE ... WHEN NOT MATCHED 保证重复执行不会把已有版本号重置为 0。
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE '
MERGE INTO notification_state t
USING (SELECT 1 id FROM dual) s
ON (t.id = s.id)
WHEN NOT MATCHED THEN INSERT (id, version, updated_at)
  VALUES (1, 0, SYSTIMESTAMP)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-1, -955) THEN RAISE; END IF;
END;
/

-- ----------------------------------------------------------------------------
-- NOTIFICATION_VERSIONS  ← new (通知内容快照; append-only)
--   一行 = 一次写入后的完整内容（create / update / delete / rollback），
--   SOURCE_VERSION 只在 rollback 行上有值。CONTENT_HASH 是
--   sha256(title + '\n' + content + '\n' + "1"/"0") 的 64 位小写 hex。
--   不建 FK：通知被硬删后历史仍要能回答「当时是什么内容、谁改的」。
--   也由 V017__add_notification_versions.sql 在存量实例上创建。
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE '
CREATE TABLE notification_versions (
  oid VARCHAR2(24 CHAR) NOT NULL,
  notification_oid VARCHAR2(24 CHAR) NOT NULL,
  version NUMBER(19) NOT NULL,
  title VARCHAR2(200 CHAR) NOT NULL,
  content VARCHAR2(4000 CHAR) NOT NULL,
  active NUMBER(1) NOT NULL,
  op VARCHAR2(16 CHAR) NOT NULL,
  source_version NUMBER(19),
  content_hash VARCHAR2(64 CHAR) NOT NULL,
  author_oid VARCHAR2(24 CHAR),
  author_name VARCHAR2(128 CHAR) NOT NULL,
  created_at TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL,
  PRIMARY KEY (oid),
  CONSTRAINT uq_notification_versions UNIQUE (notification_oid, version)
)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -955 THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'CREATE INDEX ix_notification_versions_doc ON notification_versions (notification_oid, version)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-955, -1408) THEN RAISE; END IF;
END;
/

-- ----------------------------------------------------------------------------
-- AI_CALL_LOGS  ← new (AI 网关调用流水; append-only)
--   一次网关调用一行，成功/失败/超时/拒绝/忙都落库——只记成功就无法回答
--   「额度为什么没扣成」。无外键：审计要活得比账号久（同 BAN_VIOLATIONS）。
--   UNITS 是本次调用扣掉的额度单位，与 AI_QUOTA.USED 的增量同源。
--   KEY_SCOPE 记录用的是哪一类 key（personal / shared / server；存量行为 NULL），
--   光有 provider 名回答不了「这次调用凭什么扣的额度」。
--   也由 V024__add_ai_gateway.sql 在存量实例上创建。
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE '
CREATE TABLE ai_call_logs (
  oid VARCHAR2(24 CHAR) NOT NULL,
  user_oid VARCHAR2(24 CHAR) NOT NULL,
  capability VARCHAR2(16 CHAR) NOT NULL,
  endpoint VARCHAR2(40 CHAR) NOT NULL,
  provider VARCHAR2(40 CHAR),
  model VARCHAR2(80 CHAR),
  status VARCHAR2(16 CHAR) NOT NULL,
  http_status NUMBER(3),
  error_code VARCHAR2(40 CHAR),
  latency_ms NUMBER(10),
  units NUMBER(10) DEFAULT 0 NOT NULL,
  input_bytes NUMBER(10),
  request_id VARCHAR2(64 CHAR),
  key_scope VARCHAR2(16 CHAR),
  prompt_tokens NUMBER(10),
  completion_tokens NUMBER(10),
  total_tokens NUMBER(10),
  attempt_no NUMBER(3),
  purpose VARCHAR2(24 CHAR),
  first_token_ms NUMBER(10),
  engine_kind VARCHAR2(24 CHAR),
  attachment_oid VARCHAR2(24 CHAR),
  round_no NUMBER(3),
  roles_snapshot VARCHAR2(64 CHAR),
  cache_read_tokens NUMBER(10),
  cache_write_tokens NUMBER(10),
  cache_key VARCHAR2(64 CHAR),
  created_at TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL,
  PRIMARY KEY (oid),
  CONSTRAINT ck_ai_call_logs_oid CHECK (REGEXP_LIKE(oid,''^[0-9a-f]{24}$'')),
  CONSTRAINT ck_ai_call_logs_capability CHECK (capability IN (''ocr'',''chat'')),
  CONSTRAINT ck_ai_call_logs_status CHECK (status IN (''success'',''error'',''timeout'',''rejected'',''busy'',''warning'')),
  CONSTRAINT ck_ai_call_logs_units CHECK (units >= 0),
  CONSTRAINT ck_ai_call_logs_key_scope CHECK (key_scope IS NULL OR key_scope IN (''personal'',''shared'',''server'')),
  CONSTRAINT ck_ai_call_logs_tokens CHECK ((prompt_tokens IS NULL OR prompt_tokens >= 0) AND (completion_tokens IS NULL OR completion_tokens >= 0) AND (total_tokens IS NULL OR total_tokens >= 0)),
  CONSTRAINT ck_ai_call_logs_attempt_no CHECK (attempt_no IS NULL OR attempt_no >= 1),
  CONSTRAINT ck_ai_call_logs_round_no CHECK (round_no IS NULL OR round_no >= 1),
  CONSTRAINT ck_ai_call_logs_cache_tokens CHECK ((cache_read_tokens IS NULL OR cache_read_tokens >= 0) AND (cache_write_tokens IS NULL OR cache_write_tokens >= 0))
)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -955 THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'CREATE INDEX ix_ai_call_logs_user_created ON ai_call_logs (user_oid, created_at)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-955, -1408) THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'CREATE INDEX ix_ai_call_logs_created ON ai_call_logs (created_at)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-955, -1408) THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'CREATE INDEX ix_ai_call_logs_endpoint_status ON ai_call_logs (endpoint, status, created_at)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-955, -1408) THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'CREATE INDEX ix_ai_call_logs_status_created ON ai_call_logs (status, created_at)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-955, -1408) THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'CREATE INDEX ix_ai_call_logs_request_id ON ai_call_logs (request_id)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-955, -1408) THEN RAISE; END IF;
END;
/

-- ----------------------------------------------------------------------------
-- AI_QUOTA  ← new (按 用户 + 月份 + 能力 计的额度行)
--   三元组唯一（ux_ai_quota_user_period_kind），所以额度扣减可以用单条
--   UPDATE ... WHERE used + :amount <= limit_count 原子完成，不读后写、不超卖。
--   PERIOD 是 CHAR(6) 的 YYYYMM：月份是计费口径而不是时间点。
--   KIND 是前缀式（'ocr' / 'chat' / 'ocr_<engine>'）：OCR 引擎会持续增加，
--   前缀让「某引擎的月度额度」不必每次改 DDL 约束；ESCAPE '\' 是必须的，
--   LIKE 的 '_' 是单字符通配符。
--   也由 V024__add_ai_gateway.sql 在存量实例上创建（V026 把 KIND 放宽为前缀式）。
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE '
CREATE TABLE ai_quota (
  oid VARCHAR2(24 CHAR) NOT NULL,
  user_oid VARCHAR2(24 CHAR) NOT NULL,
  period CHAR(6) NOT NULL,
  kind VARCHAR2(16 CHAR) NOT NULL,
  used NUMBER(10) DEFAULT 0 NOT NULL,
  limit_count NUMBER(10) NOT NULL,
  updated_at TIMESTAMP NOT NULL,
  PRIMARY KEY (oid),
  CONSTRAINT ck_ai_quota_oid CHECK (REGEXP_LIKE(oid,''^[0-9a-f]{24}$'')),
  CONSTRAINT ck_ai_quota_period CHECK (REGEXP_LIKE(period,''^[0-9]{6}$'')),
  CONSTRAINT ck_ai_quota_kind CHECK (kind IN (''ocr'',''chat'') OR kind LIKE ''ocr\_%'' ESCAPE ''\''),
  CONSTRAINT ck_ai_quota_used CHECK (used >= 0),
  CONSTRAINT ck_ai_quota_limit CHECK (limit_count >= 0)
)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -955 THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'CREATE UNIQUE INDEX ux_ai_quota_user_period_kind ON ai_quota (user_oid, period, kind)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-955, -1408) THEN RAISE; END IF;
END;
/

-- ----------------------------------------------------------------------------
-- AI_CREDENTIALS  ← new (AI provider 凭据；personal = 管理员个人，shared = 通用)
--   OWNER_OID 对 shared 行固定是 24 个 0 的哨兵，不用 NULL：Oracle 唯一索引里
--   NULL 互不相等，owner 可空就等于 (scope, owner_oid, name) 的唯一性失效。
--   API_KEY_CIPHER 只存 AES-GCM 密文（base64），明文永不落库。
--   MODELS_JSON 是模型 id 字符串数组，IS JSON 约束挡住坏串。
--   HEADERS_JSON / API_PATH 是请求形状（头名映射 + 路径），空 = 沿用默认；
--   由 V026__add_ai_ocr_engines_and_key_headers.sql 在存量实例上补列。
--   也由 V025__add_ai_credentials.sql 在存量实例上创建。
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE '
CREATE TABLE ai_credentials (
  oid VARCHAR2(24 CHAR) NOT NULL,
  scope VARCHAR2(8 CHAR) NOT NULL,
  owner_oid VARCHAR2(24 CHAR) NOT NULL,
  name VARCHAR2(64 CHAR) NOT NULL,
  kind VARCHAR2(24 CHAR) NOT NULL,
  base_url VARCHAR2(400 CHAR) NOT NULL,
  models_json CLOB NOT NULL,
  headers_json CLOB,
  api_path VARCHAR2(200 CHAR),
  api_key_cipher VARCHAR2(2000 CHAR) NOT NULL,
  key_hint VARCHAR2(16 CHAR),
  enabled NUMBER(1) DEFAULT 1 NOT NULL,
  created_at TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL,
  updated_at TIMESTAMP NOT NULL,
  expires_at TIMESTAMP,
  PRIMARY KEY (oid),
  CONSTRAINT ck_ai_credentials_oid CHECK (REGEXP_LIKE(oid,''^[0-9a-f]{24}$'')),
  CONSTRAINT ck_ai_credentials_scope CHECK (scope IN (''personal'',''shared'')),
  CONSTRAINT ck_ai_credentials_kind CHECK (kind IN (''openai_compat'')),
  CONSTRAINT ck_ai_credentials_enabled CHECK (enabled IN (0,1)),
  CONSTRAINT ck_ai_credentials_models_json CHECK (models_json IS JSON),
  CONSTRAINT ck_ai_credentials_headers_json CHECK (headers_json IS JSON)
)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -955 THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'CREATE UNIQUE INDEX ux_ai_credentials_scope_owner_name ON ai_credentials (scope, owner_oid, name)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-955, -1408) THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'CREATE INDEX ix_ai_credentials_owner ON ai_credentials (owner_oid, enabled)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-955, -1408) THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'CREATE INDEX ix_ai_credentials_scope ON ai_credentials (scope, enabled)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-955, -1408) THEN RAISE; END IF;
END;
/

-- ----------------------------------------------------------------------------
-- AI_OCR_ENGINES  ← new (OCR 引擎注册表；本地 / 云端引擎统一一行)
--   KIND 是引擎类型（tencent / aliyun / rapidocr / baidu / groq）而不是
--   provider 名：同类型可以有多行（不同 region / 配额来源）。PRIORITY 越小
--   越优先，路由按 (enabled, priority) 取第一行可用引擎。
--   SECRET_CIPHER 可空：本地引擎没有凭证（NULL = 不需要，不是配置缺失）。
--   NAME 唯一：运维按名字指定/禁用某个引擎。
--   也由 V026__add_ai_ocr_engines_and_key_headers.sql 在存量实例上创建；
--   KIND 集合由 V027__extend_ai_ocr_engines_kind.sql 扩到含 baidu / groq。
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE '
CREATE TABLE ai_ocr_engines (
  oid VARCHAR2(24 CHAR) NOT NULL,
  name VARCHAR2(64 CHAR) NOT NULL,
  kind VARCHAR2(20 CHAR) NOT NULL,
  config_json CLOB,
  secret_cipher VARCHAR2(4000 CHAR),
  key_hint VARCHAR2(16 CHAR),
  enabled NUMBER(1) DEFAULT 1 NOT NULL,
  priority NUMBER(3) DEFAULT 100 NOT NULL,
  created_at TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL,
  updated_at TIMESTAMP NOT NULL,
  CONSTRAINT pk_ai_ocr_engines PRIMARY KEY (oid),
  CONSTRAINT ck_ai_ocr_engines_oid CHECK (REGEXP_LIKE(oid,''^[0-9a-f]{24}$'')),
  CONSTRAINT ck_ai_ocr_engines_kind CHECK (kind IN (''tencent'',''aliyun'',''rapidocr'',''baidu'',''groq'',''zhipu'')),
  CONSTRAINT ck_ai_ocr_engines_enabled CHECK (enabled IN (0,1)),
  CONSTRAINT ck_ai_ocr_engines_config_json CHECK (config_json IS JSON)
)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -955 THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'CREATE UNIQUE INDEX ux_ai_ocr_engines_name ON ai_ocr_engines (name)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-955, -1408) THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'CREATE INDEX ix_ai_ocr_engines_enabled_priority ON ai_ocr_engines (enabled, priority)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-955, -1408) THEN RAISE; END IF;
END;
/

-- ----------------------------------------------------------------------------
-- AI_API_KEYS  ← new (OpenAI 兼容面的独立 API key)
--   TOKEN_HASH 是 sha256(token) 的 hex：明文只在创建响应里出现一次，永不落库。
--   NAME 在同一 USER_OID 内唯一（ux_ai_api_keys_owner_name）；TOKEN_HASH 全局
--   唯一（所有 key 共用一个校验入口，撞哈希等于两条记录互相遮蔽）。
--   EXPIRES_AT 可空 = 永不过期；LAST_USED_AT 是采样写（最多每 60 秒一次），
--   不是精确审计。
--   也由 V030__add_ai_api_keys.sql 在存量实例上创建。
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE '
CREATE TABLE ai_api_keys (
  oid VARCHAR2(24 CHAR) NOT NULL,
  user_oid VARCHAR2(24 CHAR) NOT NULL,
  name VARCHAR2(64 CHAR) NOT NULL,
  token_hash VARCHAR2(64 CHAR) NOT NULL,
  key_hint VARCHAR2(16 CHAR) NOT NULL,
  enabled NUMBER(1) DEFAULT 1 NOT NULL,
  expires_at TIMESTAMP,
  last_used_at TIMESTAMP,
  created_at TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL,
  CONSTRAINT pk_ai_api_keys PRIMARY KEY (oid),
  CONSTRAINT ck_ai_api_keys_oid CHECK (REGEXP_LIKE(oid,''^[0-9a-f]{24}$'')),
  CONSTRAINT ck_ai_api_keys_enabled CHECK (enabled IN (0,1))
)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -955 THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'CREATE UNIQUE INDEX ux_ai_api_keys_token_hash ON ai_api_keys (token_hash)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-955, -1408) THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'CREATE UNIQUE INDEX ux_ai_api_keys_owner_name ON ai_api_keys (user_oid, name)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-955, -1408) THEN RAISE; END IF;
END;
/

-- ----------------------------------------------------------------------------
-- BAN_RULES seed  ← 内置规则（非主学海设备登录；也由 V014/V016 mirror）
--   惩罚梯度末位 null = 永久。用固定 oid + MERGE：重复执行不会覆盖管理员
--   调过的 threshold / penalties。旧的 multi_device_login 已随 V016 删除
--   （口径被「主设备 + 临时密码」取代），它的历史流水保留在 BAN_RECORDS 里。
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE '
MERGE INTO ban_rules t
USING (SELECT ''0b0000000000000000000002'' oid FROM dual) s
ON (t.code = ''foreign_device_login'')
WHEN NOT MATCHED THEN INSERT
  (oid, code, name, trigger_code, enabled, config, penalties, builtin, created_at, updated_at)
  VALUES (''0b0000000000000000000002'', ''foreign_device_login'', ''非主学海设备登录'',
          ''foreign_device_login'', 1, ''{"threshold":3}'',
          ''[1,3,7,14,30,null]'', 1, SYSTIMESTAMP, SYSTIMESTAMP)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-1, -955) THEN RAISE; END IF;
END;
/

-- ----------------------------------------------------------------------------
-- BAN_RULES seed  ← 内置规则（临时密码滥用的设备梯度；也由 V039 mirror）
--   连错次数阈值仍取 foreign_device_login，本规则只提供设备封禁的 penalties。
--   trigger 与 code 同名：检查项唯一索引要求它独占一个值，求值按 code 取。
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE '
MERGE INTO ban_rules t
USING (SELECT ''0b000000000000000000000d'' oid FROM dual) s
ON (t.code = ''device_temp_password_abuse'')
WHEN NOT MATCHED THEN INSERT
  (oid, code, name, trigger_code, enabled, config, penalties, builtin, created_at, updated_at)
  VALUES (''0b000000000000000000000d'', ''device_temp_password_abuse'',
          ''临时密码滥用（设备）'', ''device_temp_password_abuse'', 1,
          ''{"threshold":3,"window_hours":24}'', ''[1,3,7,14,30,null]'', 1,
          SYSTIMESTAMP, SYSTIMESTAMP)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-1, -955) THEN RAISE; END IF;
END;
/

-- ----------------------------------------------------------------------------
-- BAN_RULES seed  ← 内置规则（平台违规使用；也由 V019__harden_ban_engine.sql mirror）
--   上报闸（services.violation_guard）按 trigger 取这条规则。没有种子的后果是
--   「只记录不封禁」——上报端点看起来在工作，实际永远不会处罚。config 与
--   ban_rules.DEFAULT_CONFIG / violation_guard.DEFAULT_CONFIG 同一形状与数值。
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE '
MERGE INTO ban_rules t
USING (SELECT ''0b0000000000000000000003'' oid FROM dual) s
ON (t.code = ''platform_misuse'')
WHEN NOT MATCHED THEN INSERT
  (oid, code, name, trigger_code, enabled, config, penalties, builtin, created_at, updated_at)
  VALUES (''0b0000000000000000000003'', ''platform_misuse'', ''平台违规使用'',
          ''platform_misuse'', 1, ''{"threshold":3,"window_hours":24}'',
          ''[1,3,7]'', 1, SYSTIMESTAMP, SYSTIMESTAMP)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-1, -955) THEN RAISE; END IF;
END;
/

-- ----------------------------------------------------------------------------
-- BAN_RULES seed  ← 七条服务端可观测规则的默认行（也由 V020__risk_rules.sql mirror）
--   config.mode 决定达到阈值后「只告警」（alert）还是「按梯度封禁」（ban）；
--   缺省等价于 ban。alert 的 penalties 是 JSON null（该规则不封人）。
--   固定 oid + MERGE：重复执行既不重复插入，也不覆盖管理员调过的阈值与梯度。
-- ----------------------------------------------------------------------------
-- ----------------------------------------------------------------------------
-- 内置规则: 登录失败暴破（APP_LOGS ltype=login_fail）
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE '
MERGE INTO ban_rules t
USING (SELECT ''0b0000000000000000000004'' oid FROM dual) s
ON (t.code = ''login_failure_burst'')
WHEN NOT MATCHED THEN INSERT
  (oid, code, name, trigger_code, enabled, config, penalties, builtin, created_at, updated_at)
  VALUES (''0b0000000000000000000004'', ''login_failure_burst'', ''登录失败暴破'',
          ''login_failure_burst'', 1, ''{"threshold":10,"window_hours":1,"mode":"alert"}'',
          ''[1,3,7]'', 1, SYSTIMESTAMP, SYSTIMESTAMP)';
EXCEPTION
  -- -1: 并发执行时另一会话已插入（uq_ban_rules_code），结果一致，忽略。
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-1, -955) THEN RAISE; END IF;
END;
/

-- ----------------------------------------------------------------------------
-- 内置规则: 封禁期内反复尝试登录（APP_LOGS ltype=login_fail 且摘要含 banned）
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE '
MERGE INTO ban_rules t
USING (SELECT ''0b0000000000000000000005'' oid FROM dual) s
ON (t.code = ''ban_evasion_retry'')
WHEN NOT MATCHED THEN INSERT
  (oid, code, name, trigger_code, enabled, config, penalties, builtin, created_at, updated_at)
  VALUES (''0b0000000000000000000005'', ''ban_evasion_retry'', ''封禁期内反复尝试登录'',
          ''ban_evasion_retry'', 1, ''{"threshold":5,"window_hours":1,"mode":"ban"}'',
          ''[1,3,7]'', 1, SYSTIMESTAMP, SYSTIMESTAMP)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-1, -955) THEN RAISE; END IF;
END;
/

-- ----------------------------------------------------------------------------
-- 内置规则: 设备挑战刷取（DEVICE_CHALLENGES 按 user_oid 计数，默认只告警）
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE '
MERGE INTO ban_rules t
USING (SELECT ''0b0000000000000000000006'' oid FROM dual) s
ON (t.code = ''challenge_flood'')
WHEN NOT MATCHED THEN INSERT
  (oid, code, name, trigger_code, enabled, config, penalties, builtin, created_at, updated_at)
  VALUES (''0b0000000000000000000006'', ''challenge_flood'', ''设备挑战刷取'',
          ''challenge_flood'', 1, ''{"threshold":8,"window_hours":1,"mode":"alert"}'',
          ''null'', 1, SYSTIMESTAMP, SYSTIMESTAMP)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-1, -955) THEN RAISE; END IF;
END;
/

-- ----------------------------------------------------------------------------
-- 内置规则: 临时密码签发过频（APP_LOGS ltype=temp_password_create，按签发人）
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE '
MERGE INTO ban_rules t
USING (SELECT ''0b0000000000000000000007'' oid FROM dual) s
ON (t.code = ''temp_password_issue_burst'')
WHEN NOT MATCHED THEN INSERT
  (oid, code, name, trigger_code, enabled, config, penalties, builtin, created_at, updated_at)
  VALUES (''0b0000000000000000000007'', ''temp_password_issue_burst'', ''临时密码签发过频'',
          ''temp_password_issue_burst'', 1, ''{"threshold":20,"window_hours":1,"mode":"alert"}'',
          ''null'', 1, SYSTIMESTAMP, SYSTIMESTAMP)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-1, -955) THEN RAISE; END IF;
END;
/

-- ----------------------------------------------------------------------------
-- 内置规则: 导出任务过频（APP_LOGS ltype=export，按请求者）
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE '
MERGE INTO ban_rules t
USING (SELECT ''0b0000000000000000000008'' oid FROM dual) s
ON (t.code = ''export_burst'')
WHEN NOT MATCHED THEN INSERT
  (oid, code, name, trigger_code, enabled, config, penalties, builtin, created_at, updated_at)
  VALUES (''0b0000000000000000000008'', ''export_burst'', ''导出任务过频'',
          ''export_burst'', 1, ''{"threshold":10,"window_hours":1,"mode":"alert"}'',
          ''null'', 1, SYSTIMESTAMP, SYSTIMESTAMP)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-1, -955) THEN RAISE; END IF;
END;
/

-- ----------------------------------------------------------------------------
-- 内置规则: 短时间反复登录（APP_LOGS ltype=user_login）
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE '
MERGE INTO ban_rules t
USING (SELECT ''0b0000000000000000000009'' oid FROM dual) s
ON (t.code = ''login_success_churn'')
WHEN NOT MATCHED THEN INSERT
  (oid, code, name, trigger_code, enabled, config, penalties, builtin, created_at, updated_at)
  VALUES (''0b0000000000000000000009'', ''login_success_churn'', ''短时间反复登录'',
          ''login_success_churn'', 1, ''{"threshold":15,"window_hours":1,"mode":"alert"}'',
          ''null'', 1, SYSTIMESTAMP, SYSTIMESTAMP)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-1, -955) THEN RAISE; END IF;
END;
/

-- ----------------------------------------------------------------------------
-- 内置规则: 频繁更换设备（APP_LOGS ltype=device_mismatch，24 小时窗口）
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE '
MERGE INTO ban_rules t
USING (SELECT ''0b000000000000000000000a'' oid FROM dual) s
ON (t.code = ''device_switch_burst'')
WHEN NOT MATCHED THEN INSERT
  (oid, code, name, trigger_code, enabled, config, penalties, builtin, created_at, updated_at)
  VALUES (''0b000000000000000000000a'', ''device_switch_burst'', ''频繁更换设备'',
          ''device_switch_burst'', 1, ''{"threshold":5,"window_hours":24,"mode":"alert"}'',
          ''null'', 1, SYSTIMESTAMP, SYSTIMESTAMP)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-1, -955) THEN RAISE; END IF;
END;
/

-- ----------------------------------------------------------------------------
-- AI_AGENT_EVENTS  ← new (agent 每次工具调用的留痕; V033 + V034)
--   一行 = 一次工具调用尝试，包含被拒 / 驳回 / 过期的那些：只记成功就看不见
--   「模型在试探白名单外的工具」。OID 是 32 位 hex（提案句柄，与业务 oid 空间分
--   开），ARGS_HASH 是**完整参数 ARGS_PAYLOAD** 的 sha256——确认时必须能重算比对；
--   ARGS_SUMMARY 只是 ≤500 的展示副本。PREVIEW 是确认界面的结构化预览。
--   APPROVER_OID / CONFIRMED_AT 记录双人码审批人与执行时间（无 TOTP 时 NULL）。
--   STATUS 走 CAS：proposed → executing → confirmed | failed（终态不可覆盖）。
--   无外键：审计要活过账号删除（同 AI_CALL_LOGS）。也由
--   V033 / V034 在存量实例上创建/补齐。
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE '
CREATE TABLE ai_agent_events (
  oid VARCHAR2(32 CHAR) NOT NULL,
  request_id VARCHAR2(64 CHAR),
  round_no NUMBER(3),
  tool_name VARCHAR2(48 CHAR),
  tool_kind VARCHAR2(8 CHAR),
  args_hash VARCHAR2(64 CHAR),
  args_summary VARCHAR2(500 CHAR),
  args_payload CLOB,
  preview CLOB,
  result_summary VARCHAR2(500 CHAR),
  status VARCHAR2(12 CHAR) NOT NULL,
  actor_oid VARCHAR2(32 CHAR),
  approver_oid VARCHAR2(32 CHAR),
  confirmed_at TIMESTAMP,
  created_at TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL,
  PRIMARY KEY (oid),
  CONSTRAINT ck_ai_agent_events_tool_kind CHECK (tool_kind IS NULL OR tool_kind IN (''read'',''write'')),
  CONSTRAINT ck_ai_agent_events_status CHECK (status IN (''executed'',''proposed'',''executing'',''confirmed'',''rejected'',''expired'',''failed'',''denied''))
)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -955 THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'CREATE INDEX ix_ai_agent_events_request_id ON ai_agent_events (request_id)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-955, -1408) THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'CREATE INDEX ix_ai_agent_events_status_created ON ai_agent_events (status, created_at)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-955, -1408) THEN RAISE; END IF;
END;
/

-- ----------------------------------------------------------------------------
-- AI_PROMPT_AUDIT  ← new (受控的 prompt 正文留存; V033)
--   本迁移只建表：写入逻辑与保留期由后续监控车道实现。PROMPT_TEXT 用 CLOB，
--   一段对话可能远超 VARCHAR2 的 4000 字符。也由 V033 在存量实例上创建。
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE '
CREATE TABLE ai_prompt_audit (
  oid VARCHAR2(32 CHAR) NOT NULL,
  request_id VARCHAR2(64 CHAR),
  user_oid VARCHAR2(32 CHAR),
  roles_snapshot VARCHAR2(64 CHAR),
  purpose VARCHAR2(24 CHAR),
  prompt_text CLOB,
  answer_text CLOB,
  tool_calls CLOB,
  refused NUMBER(1) DEFAULT 0 NOT NULL,
  warned NUMBER(1) DEFAULT 0 NOT NULL,
  created_at TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL,
  PRIMARY KEY (oid)
)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -955 THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'CREATE INDEX ix_ai_prompt_audit_user_created ON ai_prompt_audit (user_oid, created_at)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-955, -1408) THEN RAISE; END IF;
END;
/

-- ----------------------------------------------------------------------------
-- AI_CONVERSATIONS  ← new (持久会话; V035)
--   一段会话一行，OID 是 32 位 hex（对外句柄）。MODE 是 Oracle 保留字，只能
--   带引号：物理名 "MODE"，存为大写（与其他列一致），裸写 mode 仍可解析。
--   SUMMARY / SUMMARY_UPTO_SEQ 是压缩水位（0 = 从未压缩）；摘要只存会话行，
--   所以 AI_MESSAGES 没有 summary 角色。计数字段 DEFAULT 0 NOT NULL（累加器），
--   上游 token 在 AI_MESSAGES 上仍可空（NULL = 上游没报）。也由 V035 创建。
--   THINKING 是会话级思考强度（V046）；SCENE 是只读场景标记（V048）：
--   terminal | file_edit，NULL = 普通会话（列表查询只取 NULL）。
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE '
CREATE TABLE ai_conversations (
  oid VARCHAR2(32 CHAR) NOT NULL,
  owner_oid VARCHAR2(24 CHAR) NOT NULL,
  "MODE" VARCHAR2(8 CHAR) NOT NULL,
  title VARCHAR2(120 CHAR),
  model VARCHAR2(120 CHAR),
  thinking VARCHAR2(16 CHAR),
  scene VARCHAR2(16 CHAR),
  status VARCHAR2(12 CHAR) DEFAULT ''active'' NOT NULL,
  summary CLOB,
  summary_upto_seq NUMBER(10) DEFAULT 0 NOT NULL,
  tokens_prompt NUMBER(12) DEFAULT 0 NOT NULL,
  tokens_completion NUMBER(12) DEFAULT 0 NOT NULL,
  tokens_total NUMBER(12) DEFAULT 0 NOT NULL,
  context_tokens NUMBER(12) DEFAULT 0 NOT NULL,
  message_count NUMBER(10) DEFAULT 0 NOT NULL,
  compaction_degraded_at TIMESTAMP,
  created_at TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL,
  updated_at TIMESTAMP NOT NULL,
  PRIMARY KEY (oid),
  CONSTRAINT ck_ai_conversations_mode CHECK ("MODE" IN (''chat'',''agent'')),
  CONSTRAINT ck_ai_conversations_status CHECK (status IN (''active'',''archived''))
)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -955 THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE '
CREATE INDEX ix_ai_conversations_owner_status_updated ON ai_conversations (owner_oid, status, updated_at DESC)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-955, -1408) THEN RAISE; END IF;
END;
/

-- ----------------------------------------------------------------------------
-- AI_MESSAGES  ← new (会话内消息; V035)
--   SEQ 会话内从 1 起，UNIQUE(CONVERSATION_OID, SEQ) 由会话行锁内的 seq 分配兜底。
--   ROLE 只有 user/assistant/tool（没有 summary）。CLOB 是必需：工具结果全文
--   ≤8000 字符，超过 VARCHAR2 上限。列表查询不得 select content。也由 V035 创建。
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE '
CREATE TABLE ai_messages (
  oid VARCHAR2(32 CHAR) NOT NULL,
  conversation_oid VARCHAR2(32 CHAR) NOT NULL,
  seq NUMBER(10) NOT NULL,
  role VARCHAR2(12 CHAR) NOT NULL,
  content CLOB,
  reasoning CLOB,
  tool_calls CLOB,
  tool_call_id VARCHAR2(64 CHAR),
  tool_name VARCHAR2(64 CHAR),
  round_no NUMBER(3),
  finish_reason VARCHAR2(24 CHAR),
  request_id VARCHAR2(64 CHAR),
  prompt_tokens NUMBER(10),
  completion_tokens NUMBER(10),
  total_tokens NUMBER(10),
  cache_read_tokens NUMBER(10),
  cache_write_tokens NUMBER(10),
  created_at TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL,
  PRIMARY KEY (oid),
  CONSTRAINT uq_ai_messages_conv_seq UNIQUE (conversation_oid, seq),
  CONSTRAINT ck_ai_messages_role CHECK (role IN (''user'',''assistant'',''tool'')),
  CONSTRAINT ck_ai_messages_seq CHECK (seq >= 1),
  CONSTRAINT ck_ai_messages_round_no CHECK (round_no IS NULL OR round_no >= 1)
)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -955 THEN RAISE; END IF;
END;
/

-- ----------------------------------------------------------------------------
-- AI_RUNS  ← new (智能体执行任务; V035)
--   OID 同时是 ai_call_logs.request_id 与 ai_agent_events.request_id。
--   活跃唯一性是**函数唯一索引**（只在 queued|running 时求值，终态行键为 NULL
--   而 Oracle 不索引全 NULL 键）：应用层的 SELECT ... FOR UPDATE 只是第一道闸，
--   并发第二个 POST 由它在 ORA-00001 上挡下 → 409。CLIENT_MSG_ID 幂等键可空。
--   也由 V035 创建。
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE '
CREATE TABLE ai_runs (
  oid VARCHAR2(32 CHAR) NOT NULL,
  conversation_oid VARCHAR2(32 CHAR) NOT NULL,
  owner_oid VARCHAR2(24 CHAR) NOT NULL,
  client_msg_id VARCHAR2(64 CHAR),
  status VARCHAR2(16 CHAR) NOT NULL,
  model VARCHAR2(120 CHAR),
  task_oid VARCHAR2(24 CHAR),
  stop_reason VARCHAR2(24 CHAR),
  rounds NUMBER(3),
  error_code VARCHAR2(40 CHAR),
  error_message VARCHAR2(500 CHAR),
  result_message_oid VARCHAR2(32 CHAR),
  prompt_tokens NUMBER(12),
  completion_tokens NUMBER(12),
  total_tokens NUMBER(12),
  cancel_requested NUMBER(1) DEFAULT 0 NOT NULL,
  heartbeat_at TIMESTAMP,
  created_at TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL,
  started_at TIMESTAMP,
  finished_at TIMESTAMP,
  PRIMARY KEY (oid),
  CONSTRAINT ck_ai_runs_status CHECK (status IN (''queued'',''running'',''succeeded'',''failed'',''cancelled'',''interrupted'')),
  CONSTRAINT ck_ai_runs_cancel_requested CHECK (cancel_requested IN (0,1))
)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -955 THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE '
CREATE UNIQUE INDEX uq_ai_runs_active_conv ON ai_runs (CASE WHEN status IN (''queued'',''running'') THEN conversation_oid END)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-955, -1408) THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE '
CREATE UNIQUE INDEX uq_ai_runs_active_owner ON ai_runs (CASE WHEN status IN (''queued'',''running'') THEN owner_oid END)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-955, -1408) THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE '
CREATE UNIQUE INDEX uq_ai_runs_client_msg ON ai_runs (conversation_oid, client_msg_id)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-955, -1408) THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE '
CREATE INDEX ix_ai_runs_owner_status ON ai_runs (owner_oid, status)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-955, -1408) THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE '
CREATE INDEX ix_ai_runs_conv_created ON ai_runs (conversation_oid, created_at DESC)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-955, -1408) THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE '
CREATE INDEX ix_ai_runs_status_heartbeat ON ai_runs (status, heartbeat_at)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-955, -1408) THEN RAISE; END IF;
END;
/

-- 某条定时任务的执行历史（V040）：TASK_OID 可空，非空行才是定时任务产物。
BEGIN
  EXECUTE IMMEDIATE '
CREATE INDEX ix_ai_runs_task_created ON ai_runs (task_oid, created_at DESC)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-955, -1408) THEN RAISE; END IF;
END;
/

-- ----------------------------------------------------------------------------
-- 内置规则: AI 对话请求突发（审计「谁在刷」，只告警不封禁）
--   只建种子，不实现消费逻辑（后续监控车道）。
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE '
MERGE INTO ban_rules t
USING (SELECT ''0b000000000000000000000b'' oid FROM dual) s
ON (t.code = ''ai_chat_burst'')
WHEN NOT MATCHED THEN INSERT
  (oid, code, name, trigger_code, enabled, config, penalties, builtin, created_at, updated_at)
  VALUES (''0b000000000000000000000b'', ''ai_chat_burst'', ''AI 对话请求突发'',
          ''ai_chat_burst'', 1, ''{"threshold":80,"window_hours":1,"mode":"alert"}'',
          ''[]'', 1, SYSTIMESTAMP, SYSTIMESTAMP)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-1, -955) THEN RAISE; END IF;
END;
/

-- ----------------------------------------------------------------------------
-- 内置规则: agent 越狱 / 提示注入（也由 V037__ai_agent_moderation.sql mirror）
--   审核命中（services.violation_guard.record_agent_jailbreak）按 trigger 取这一
--   行；config 与 platform_misuse 同形，梯度末位 null = 永久。
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE '
MERGE INTO ban_rules t
USING (SELECT ''0b000000000000000000000c'' oid FROM dual) s
ON (t.code = ''agent_jailbreak'')
WHEN NOT MATCHED THEN INSERT
  (oid, code, name, trigger_code, enabled, config, penalties, builtin, created_at, updated_at)
  VALUES (''0b000000000000000000000c'', ''agent_jailbreak'', ''agent 越狱与提示注入'',
          ''agent_jailbreak'', 1, ''{"threshold":3,"window_hours":24,"mode":"ban","max_attempts":3}'',
          ''[1,3,7,14,30,null]'', 1, SYSTIMESTAMP, SYSTIMESTAMP)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-1, -955) THEN RAISE; END IF;
END;
/

-- ----------------------------------------------------------------------------
-- SYSTEM_SETTINGS  ← new (在线设置中心; V036)
--   一个可调项一行，SETTING_KEY 直接做主键；VALUE_JSON 是 JSON 文本（裸 CLOB，
--   因为标量值 "x"/1/true 在 Oracle 19c 的 IS JSON 下不合法）。
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE '
CREATE TABLE system_settings (
  setting_key VARCHAR2(64 CHAR) NOT NULL,
  value_json CLOB NOT NULL,
  updated_by VARCHAR2(24 CHAR),
  updated_at TIMESTAMP NOT NULL,
  PRIMARY KEY (setting_key)
)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -955 THEN RAISE; END IF;
END;
/

-- ----------------------------------------------------------------------------
-- USER_QUOTA_OVERRIDES  ← new (按用户覆盖每日上传配额; V036)
--   两列 NULL = 继承全局设置（不是 0）。
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE '
CREATE TABLE user_quota_overrides (
  user_oid VARCHAR2(24 CHAR) NOT NULL,
  daily_max_files NUMBER,
  daily_max_bytes NUMBER,
  updated_by VARCHAR2(24 CHAR),
  updated_at TIMESTAMP NOT NULL,
  PRIMARY KEY (user_oid),
  CONSTRAINT ck_user_quota_overrides_files
    CHECK (daily_max_files IS NULL OR daily_max_files >= 0),
  CONSTRAINT ck_user_quota_overrides_bytes
    CHECK (daily_max_bytes IS NULL OR daily_max_bytes >= 0)
)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -955 THEN RAISE; END IF;
END;
/

-- ----------------------------------------------------------------------------
-- AI_SCHEDULED_TASKS  ← new (AI 定时任务; V040)
--   REVIEW_STATUS 与 ENABLED 分开：改 prompt 要重新过审，但排程不必重建。
--   LAST_RUN_AT 兼作 claim 的 CAS 期望值（见 V040 头注释）。
--   NEXT_RUN_AT 是调度器唯一取数依据，任何分支都必须推进它。
--   也由 V040__ai_scheduled_tasks.sql 在存量实例上创建。
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE '
CREATE TABLE ai_scheduled_tasks (
  oid VARCHAR2(24 CHAR) NOT NULL,
  owner_oid VARCHAR2(24 CHAR) NOT NULL,
  name VARCHAR2(128 CHAR) NOT NULL,
  prompt_text CLOB NOT NULL,
  review_status VARCHAR2(16 CHAR) DEFAULT ''draft'' NOT NULL,
  reviewed_by_oid VARCHAR2(24 CHAR),
  reviewed_at TIMESTAMP,
  review_note VARCHAR2(512 CHAR),
  schedule_kind VARCHAR2(8 CHAR) DEFAULT ''interval'' NOT NULL,
  interval_minutes NUMBER DEFAULT 1440 NOT NULL,
  cron_expr VARCHAR2(64 CHAR),
  enabled NUMBER(1) DEFAULT 1 NOT NULL,
  model VARCHAR2(64 CHAR),
  last_run_at TIMESTAMP,
  next_run_at TIMESTAMP,
  run_count NUMBER DEFAULT 0 NOT NULL,
  last_status VARCHAR2(16 CHAR),
  last_error VARCHAR2(512 CHAR),
  created_at TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL,
  updated_at TIMESTAMP NOT NULL,
  PRIMARY KEY (oid),
  CONSTRAINT ck_ai_scheduled_tasks_oid CHECK (REGEXP_LIKE(oid,''^[0-9a-f]{24}$'')),
  CONSTRAINT ck_ai_scheduled_tasks_review CHECK (review_status IN (''draft'',''pending'',''approved'',''rejected'')),
  CONSTRAINT ck_ai_scheduled_tasks_kind CHECK (schedule_kind IN (''interval'',''cron'')),
  CONSTRAINT ck_ai_scheduled_tasks_interval CHECK (interval_minutes >= 15),
  CONSTRAINT ck_ai_scheduled_tasks_enabled CHECK (enabled IN (0,1))
)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -955 THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE '
CREATE INDEX ix_ai_scheduled_tasks_owner_created ON ai_scheduled_tasks (owner_oid, created_at DESC)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-955, -1408) THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE '
CREATE INDEX ix_ai_scheduled_tasks_due ON ai_scheduled_tasks (enabled, next_run_at)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-955, -1408) THEN RAISE; END IF;
END;
/

-- ----------------------------------------------------------------------------
-- ROLE_FEATURES  ← new (角色 -> 功能开关; V043)
--   稀疏覆盖层：没有行 = 按 services.roles.policy.DEFAULTS。role 是封闭枚举，
--   admin 不落库（管理员永远全开，是代码里的短路）。
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE '
CREATE TABLE role_features (
  role VARCHAR2(32 CHAR) NOT NULL,
  feature_key VARCHAR2(48 CHAR) NOT NULL,
  enabled NUMBER(1) DEFAULT 1 NOT NULL,
  updated_at TIMESTAMP,
  updated_by CHAR(24 CHAR),
  PRIMARY KEY (role, feature_key),
  CONSTRAINT ck_role_features_enabled CHECK (enabled IN (0,1)),
  CONSTRAINT ck_role_features_role CHECK (role IN (''department'',''secretary'',''student''))
)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -955 THEN RAISE; END IF;
END;
/

-- ----------------------------------------------------------------------------
-- ROLE_QUOTAS  ← new (角色 -> 每月额度; V043)
--   只有 chat / ocr / schedule 三种角色级额度；缺行 = 按代码默认。
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE '
CREATE TABLE role_quotas (
  role VARCHAR2(32 CHAR) NOT NULL,
  kind VARCHAR2(16 CHAR) NOT NULL,
  monthly_limit NUMBER(12),
  updated_at TIMESTAMP,
  updated_by CHAR(24 CHAR),
  PRIMARY KEY (role, kind),
  CONSTRAINT ck_role_quotas_kind CHECK (kind IN (''chat'',''ocr'',''schedule'')),
  CONSTRAINT ck_role_quotas_limit CHECK (monthly_limit >= 0)
)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -955 THEN RAISE; END IF;
END;
/

-- AI_PROMPT_AUDIT 的单列时间索引（V036）：保留期清理按 created_at 扫。
BEGIN
  EXECUTE IMMEDIATE '
CREATE INDEX ix_ai_prompt_audit_created ON ai_prompt_audit (created_at)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-955, -1408) THEN RAISE; END IF;
END;
/

-- ----------------------------------------------------------------------------
-- AI_QUOTA_REQUESTS  ← new (AI 配额重置申请; V044)
--   一行一次申请：提交即 pending，审批后落 approved / rejected。
--   PERIOD 与 AI_QUOTA 同口径（YYYYMM）：申请只对当月有效。
--   REQUESTED_LIMIT 可空：NULL = 只要求重置已用量，非 NULL = 顺便把上限提到这个数；
--   0 是合法值（「本月到此为止」），不能用 0 顶替 NULL。
--   DECIDED_* 可空：pending 行还没有审批人。无外键：申请是审计材料。
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE '
CREATE TABLE ai_quota_requests (
  oid CHAR(24 CHAR) NOT NULL,
  user_oid CHAR(24 CHAR) NOT NULL,
  period CHAR(6 CHAR) NOT NULL,
  kind VARCHAR2(16 CHAR) NOT NULL,
  status VARCHAR2(16 CHAR) DEFAULT ''pending'' NOT NULL,
  reason VARCHAR2(500 CHAR),
  requested_limit NUMBER(12),
  decided_by CHAR(24 CHAR),
  decided_at TIMESTAMP,
  decision_note VARCHAR2(500 CHAR),
  created_at TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL,
  updated_at TIMESTAMP NOT NULL,
  PRIMARY KEY (oid),
  CONSTRAINT ck_ai_quota_requests_oid CHECK (REGEXP_LIKE(oid,''^[0-9a-f]{24}$'')),
  CONSTRAINT ck_ai_quota_requests_period CHECK (REGEXP_LIKE(period,''^[0-9]{6}$'')),
  CONSTRAINT ck_ai_quota_requests_kind CHECK (kind IN (''chat'',''ocr'')),
  CONSTRAINT ck_ai_quota_requests_status CHECK (status IN (''pending'',''approved'',''rejected'',''cancelled'')),
  CONSTRAINT ck_ai_quota_requests_limit CHECK (requested_limit >= 0)
)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -955 THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE '
CREATE INDEX ix_ai_quota_requests_user_period ON ai_quota_requests (user_oid, period, kind, status)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-955, -1408) THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE '
CREATE INDEX ix_ai_quota_requests_status ON ai_quota_requests (status, created_at)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-955, -1408) THEN RAISE; END IF;
END;
/
