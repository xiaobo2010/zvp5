-- ============================================================================
-- V007__add_files.sql
-- ----------------------------------------------------------------------------
-- Files/attachments API round: adds the three tables backing photo upload and
-- volunteer-order evidence (docs/FILES_API_DESIGN.md §3.1).
--
--   * FILES        ← new   (attachment metadata + normalised bytes, BLOB)
--   * FILE_OCR     ← new   (per-file OCR results; engine pluggable)
--   * FILE_EVENTS  ← new   (append-only audit trail of state transitions)
--
--   Plus eight indexes:
--     ix_files_owner(owner_oid, created_at)   ix_files_entity(entity_type, entity_oid)
--     ix_files_sha256(owner_oid, sha256)      ix_files_parent(parent_oid)
--     ix_files_cleanup(status, deleted_at)    ix_files_ocr(ocr_status)
--     ix_file_ocr_file(file_oid)              ix_file_events_file(file_oid, created_at)
--
-- Background: the Files API stores normalised image bytes in Oracle (BLOB),
-- continuing the IMPORTS.CONTENT precedent, so no object store is introduced
-- (the app makes zero outbound HTTP calls - design §1 C9).  `files.status` is
-- the single source of truth for the serve gate; owner_oid intentionally has NO
-- foreign key (same relaxation as V006 for deleted accounts).
--
-- Idempotency:
--   Same EXECUTE IMMEDIATE pattern as V001-V006.  CREATE TABLE swallows
--   ORA-00955 (name already used); CREATE INDEX additionally swallows ORA-01408
--   (index already exists).  Re-running against an instance that already has
--   some/all of these objects is a safe no-op; any other error is re-raised.
--
-- This file is mirrored by db/models.py / V001 (fresh instances get the same
-- objects from V001); run `python -m db.ddl_check` to verify they stay in sync
-- (offline, no DB access).
-- ============================================================================

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
  created_at TIMESTAMP NOT NULL,
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
  created_at TIMESTAMP NOT NULL,
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
  created_at TIMESTAMP NOT NULL,
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
