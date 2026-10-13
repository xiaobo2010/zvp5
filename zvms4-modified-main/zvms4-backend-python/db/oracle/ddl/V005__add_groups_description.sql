-- ============================================================================
-- V005__add_groups_description.sql
-- ----------------------------------------------------------------------------
-- Adds GROUPS.DESCRIPTION for the group-description endpoint
-- (routers/groups_router.py  PUT /groups/{gid}/description).  No group currently
-- carries a description; the column exists so the migrated read/write path can
-- hold one.
--
-- Idempotency:
--   Same EXECUTE IMMEDIATE pattern as V001-V004.  The ALTER swallows
--   ORA-01430 (column already added) and ORA-00955 (name already used).
--
-- This file is mirrored by db/models.py / V001; run `python -m db.ddl_check`
-- to verify the two stay in sync (offline, no DB access).
-- ============================================================================

BEGIN
  EXECUTE IMMEDIATE 'ALTER TABLE groups ADD (description VARCHAR2(1000 CHAR))';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-1430, -955) THEN RAISE; END IF;
END;
/
