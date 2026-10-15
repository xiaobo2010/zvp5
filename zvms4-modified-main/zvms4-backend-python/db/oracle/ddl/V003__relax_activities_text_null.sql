-- ============================================================================
-- V003__relax_activities_text_null.sql
-- ----------------------------------------------------------------------------
-- Relaxes the three ACTIVITIES text columns whose ``DEFAULT '' NOT NULL`` in
-- V001 is self-contradictory: in Oracle an empty string IS NULL, so the
-- declaration cannot be satisfied.  Production Mongo stores '' in exactly
-- these fields (place 395/395, description 355, approver 41), so the ETL
-- previously failed with ORA-01400 inserting NULL into a NOT NULL column.
--
--   * DESCRIPTION VARCHAR2(1000), NULL allowed, no default
--   * APPROVER    VARCHAR2(24),   NULL allowed, no default
--   * PLACE       VARCHAR2(256),  NULL allowed, no default
--
-- Idempotency:
--   MODIFY-to-NULL on an already-nullable column raises ORA-01451, which the
--   wrapper swallows so re-running is a no-op.  Any other error (missing
--   column etc.) still re-raises so genuine failures are not hidden.
--
-- This file is mirrored by db/models.py / V001; run `python -m db.ddl_check`
-- to verify the two stay in sync (offline, no DB access).
-- ============================================================================

BEGIN
  EXECUTE IMMEDIATE 'ALTER TABLE activities MODIFY (description NULL)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -1451 THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'ALTER TABLE activities MODIFY (approver NULL)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -1451 THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'ALTER TABLE activities MODIFY (place NULL)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -1451 THEN RAISE; END IF;
END;
/
