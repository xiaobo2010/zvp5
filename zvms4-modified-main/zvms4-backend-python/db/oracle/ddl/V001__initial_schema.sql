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
  PRIMARY KEY (oid),
  CONSTRAINT ck_users_oid CHECK (REGEXP_LIKE(oid,''^[0-9a-f]{24}$'')),
  CONSTRAINT ck_users_roles_json CHECK (roles IS JSON),
  CONSTRAINT ck_users_eligibility_json CHECK (eligibility IS JSON),
  CONSTRAINT ck_users_past_json CHECK (past IS JSON),
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

-- ----------------------------------------------------------------------------
-- GROUPS  ← zvms_new.groups   (type:"class" is synthesized in code, not stored)
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE '
CREATE TABLE groups (
  oid VARCHAR2(24 CHAR) NOT NULL,
  name VARCHAR2(128 CHAR) NOT NULL,
  PRIMARY KEY (oid),
  CONSTRAINT ck_groups_oid CHECK (REGEXP_LIKE(oid,''^[0-9a-f]{24}$''))
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
  description VARCHAR2(1000 CHAR) DEFAULT '''' NOT NULL,
  activity_date TIMESTAMP NOT NULL,
  created_at TIMESTAMP NOT NULL,
  updated_at TIMESTAMP NOT NULL,
  appointee_oid VARCHAR2(24 CHAR) NOT NULL,
  approver VARCHAR2(24 CHAR) DEFAULT '''' NOT NULL,
  creator_oid VARCHAR2(24 CHAR) NOT NULL,
  status VARCHAR2(16 CHAR) NOT NULL,
  place VARCHAR2(256 CHAR) DEFAULT '''' NOT NULL,
  origin VARCHAR2(24 CHAR) NOT NULL,
  PRIMARY KEY (oid),
  CONSTRAINT ck_activities_oid CHECK (REGEXP_LIKE(oid,''^[0-9a-f]{24}$'')),
  CONSTRAINT ck_activities_atype CHECK (atype IN (''on-campus'',''off-campus'',''social-practice'',''hybrid'')),
  CONSTRAINT ck_activities_status CHECK (status IN (''pending'',''effective'',''refused'')),
  CONSTRAINT ck_activities_origin CHECK (origin IN (''labor'',''organization'',''tasks'',''occasions'',''import'',''activities'',''practice'',''club'',''prize'',''other'',''unknown'')),
  CONSTRAINT fk_activities_appointee_oid FOREIGN KEY(appointee_oid) REFERENCES users (oid),
  CONSTRAINT fk_activities_creator_oid FOREIGN KEY(creator_oid) REFERENCES users (oid)
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
  CONSTRAINT fk_activity_members_activity_oid FOREIGN KEY(activity_oid) REFERENCES activities (oid),
  CONSTRAINT fk_activity_members_member_oid FOREIGN KEY(member_oid) REFERENCES users (oid)
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

BEGIN
  EXECUTE IMMEDIATE 'CREATE INDEX ix_time_academic_year_user_oid ON time_academic_year (user_oid)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-955, -1408) THEN RAISE; END IF;
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
  created_at TIMESTAMP NOT NULL,
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
-- LOGS  ← zvms_new.logs
--   NOTE: zvms_new.logs is a dead device schema (zero references in this repo).
--   Runtime logs currently write to the LEGACY zvms.logs collection. Whether to
--   ETL this device data or introduce a separate APP_LOGS table is a pending
--   policy decision (spec §2/§7) - it does NOT block this schema round.
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE '
CREATE TABLE logs (
  oid VARCHAR2(24 CHAR) NOT NULL,
  xuehai_id NUMBER(18),
  net_session CLOB,
  request CLOB,
  user_info CLOB,
  ltype VARCHAR2(64 CHAR),
  log_level VARCHAR2(32 CHAR),
  summary VARCHAR2(1000 CHAR),
  ts_at TIMESTAMP,
  ts_raw CLOB,
  PRIMARY KEY (oid),
  CONSTRAINT ck_logs_oid CHECK (REGEXP_LIKE(oid,''^[0-9a-f]{24}$'')),
  CONSTRAINT ck_logs_net_session_json CHECK (net_session IS JSON),
  CONSTRAINT ck_logs_request_json CHECK (request IS JSON),
  CONSTRAINT ck_logs_user_info_json CHECK (user_info IS JSON),
  CONSTRAINT ck_logs_ts_raw_json CHECK (ts_raw IS JSON)
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
