-- ============================================================================
-- V010__add_user_device_bindings.sql
-- ----------------------------------------------------------------------------
-- 学海 ID 身份追溯 MVP: adds USER_DEVICE_BINDINGS on an instance where V001 was
-- already applied.
--
--   * USER_DEVICE_BINDINGS (user_oid, xuehai_id, device_id, source, status,
--     note, created_at, updated_at) + uq_udb_user_xuehai UNIQUE(user_oid,
--     xuehai_id) + ix_udb_xuehai_id(xuehai_id).
--
-- Background: records which 学海 device id a user logs in from.  This round is
-- **observe-only** — no login is ever blocked; the rows feed the admin binding
-- endpoints and the login-time device_bind / binding_mismatch audit logs.
-- user_oid -> USERS(OID) is a real FK (the table starts empty, so no legacy id
-- can violate it).  A revoked row is kept (status='revoked'); the
-- (user_oid, xuehai_id) pair is unique so a re-bind reactivates in place.
--
-- Idempotency:
--   Same EXECUTE IMMEDIATE pattern as V001-V009.  CREATE TABLE swallows
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
CREATE TABLE user_device_bindings (
  oid VARCHAR2(24 CHAR) NOT NULL,
  user_oid VARCHAR2(24 CHAR) NOT NULL,
  xuehai_id VARCHAR2(32 CHAR) NOT NULL,
  device_id VARCHAR2(64 CHAR),
  source VARCHAR2(16 CHAR) DEFAULT ''auto'' NOT NULL,
  status VARCHAR2(16 CHAR) DEFAULT ''active'' NOT NULL,
  note VARCHAR2(256 CHAR),
  created_at TIMESTAMP NOT NULL,
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
