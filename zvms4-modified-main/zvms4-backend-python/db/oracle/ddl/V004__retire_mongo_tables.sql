-- ============================================================================
-- V004__retire_mongo_tables.sql
-- ----------------------------------------------------------------------------
-- Mongo-retirement round: adds the last three source collections as tables and
-- extends GROUPS with the permission-group columns.
--
--   * APP_LOGS     ← zvms_new.app_logs   (runtime app logs; typings/log.py)
--   * EXPORT_TASKS ← zvms_new.tasks      (runtime export queue; `id` is a UUID)
--   * IMPORTS      ← zvms_new.imports    (import jobs; content = BSON Binary)
--   * GROUPS       += TYPE, PERMISSIONS  (permission groups)
--
-- Idempotency:
--   Same EXECUTE IMMEDIATE pattern as V001-V003.  CREATE TABLE / CREATE INDEX
--   swallow ORA-00955 (name already used) / ORA-01408 (index already exists);
--   the GROUPS ALTER swallows ORA-01430 (column already added) and the JSON
--   check swallows ORA-02264 / ORA-00955.
--
-- This file is mirrored by db/models.py / V001; run `python -m db.ddl_check`
-- to verify the two stay in sync (offline, no DB access).
-- ============================================================================

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
  PRIMARY KEY (oid),
  CONSTRAINT ck_app_logs_oid CHECK (REGEXP_LIKE(oid,''^[0-9a-f]{24}$''))
)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -955 THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'CREATE INDEX ix_app_logs_ts_at ON app_logs (ts_at)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-955, -1408) THEN RAISE; END IF;
END;
/

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

BEGIN
  EXECUTE IMMEDIATE 'ALTER TABLE groups ADD (type VARCHAR2(16 CHAR), permissions CLOB)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -1430 THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'ALTER TABLE groups ADD CONSTRAINT ck_groups_permissions_json CHECK (permissions IS JSON)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-2264, -955) THEN RAISE; END IF;
END;
/
