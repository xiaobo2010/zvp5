-- ============================================================================
-- V002__add_users_banned.sql
-- ----------------------------------------------------------------------------
-- Adds the USERS.BANNED login gate to an instance where V001 was already
-- applied: `banned = 1` -> the user cannot log in.
--
--   * BANNED NUMBER(1) DEFAULT 0 NOT NULL, CHECK (banned IN (0,1)).
--
-- Idempotency:
--   Same EXECUTE IMMEDIATE pattern as V001.  The ALTER TABLE swallows
--   ORA-01430 (column already exists); the constraint swallows ORA-02264
--   (constraint name already used) and ORA-00955 (name already used).  Any
--   other error is re-raised, so re-running is a safe no-op.
--
-- This file is mirrored by db/models.py / V001; run `python -m db.ddl_check`
-- to verify the two stay in sync (offline, no DB access).
-- ============================================================================

BEGIN
  EXECUTE IMMEDIATE 'ALTER TABLE users ADD banned NUMBER(1) DEFAULT 0 NOT NULL';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -1430 THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'ALTER TABLE users ADD CONSTRAINT ck_users_banned CHECK (banned IN (0,1))';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-2264, -955) THEN RAISE; END IF;
END;
/
