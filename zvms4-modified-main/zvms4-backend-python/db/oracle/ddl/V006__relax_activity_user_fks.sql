-- ============================================================================
-- V006__relax_activity_user_fks.sql
-- ----------------------------------------------------------------------------
-- Drops the three ACTIVITIES / ACTIVITY_MEMBERS foreign keys that pointed at
-- USERS, so the migration can carry rows whose referenced account was deleted.
--
--   * ACTIVITIES.creator_oid    -> USERS.oid   (fk_activities_creator_oid)
--   * ACTIVITIES.appointee_oid  -> USERS.oid   (fk_activities_appointee_oid)
--   * ACTIVITY_MEMBERS.member_oid -> USERS.oid (fk_activity_members_member_oid)
--
-- Background: production Mongo (zvms_new) holds 26 activities and 4
-- activity_members rows whose creator / appointee / member reference no longer
-- exists in `users` (the accounts were deleted; the reference was left as a
-- dangling 24-hex string).  Verified against the Mongo archive on 2026-10-14:
--   creator   dangling = 24
--   appointee dangling = 26   (union over activities = 26, carrying 1425 members)
--   member    dangling = 4
-- The Mongo-era platform simply resolved those to "未知用户"; the ADB FK gate
-- instead quarantined the whole row.  This dangling-FK subset is part of the 42
-- activities + 1712 members isolated by the ETL integrity gates (see §6.8), so
-- the earlier wording "never reached ADB" was wrong: after this file was applied
-- and the backfill re-run, those rows ARE present - the ADB side carries the same
-- 26 dangling activities / 4 dangling members as the source (verified 2026-10-14).
--
-- Decision (2026-10-14): keep the accounts deleted, keep the dangling OID
-- reference, and relax the constraint - i.e. allow "no such user" to appear.
-- The referenced rows are NOT recreated and the OID values are NOT nulled.
--
-- NOT dropped: fk_activity_members_activity_oid (every referenced activity is
-- imported, so that FK stays valid and keeps its integrity guarantee).
--
-- Idempotency:
--   Same EXECUTE IMMEDIATE pattern as V001-V005.  Each DROP swallows ORA-02443
--   ("constraint does not exist") so re-running is a safe no-op; any other
--   error is re-raised.
--
-- This file is mirrored by db/models.py / V001; run `python -m db.ddl_check`
-- to verify the two stay in sync (offline, no DB access).
-- ============================================================================

BEGIN
  EXECUTE IMMEDIATE 'ALTER TABLE activities DROP CONSTRAINT fk_activities_creator_oid';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -2443 THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'ALTER TABLE activities DROP CONSTRAINT fk_activities_appointee_oid';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -2443 THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'ALTER TABLE activity_members DROP CONSTRAINT fk_activity_members_member_oid';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -2443 THEN RAISE; END IF;
END;
/
