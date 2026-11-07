-- ============================================================================
-- V009__statistics_rework.sql
-- ----------------------------------------------------------------------------
-- Adds the deferred GROUP_INDICATORS.GROUP_OID -> GROUPS(OID) foreign key that
-- V001 deliberately left out (the §7 group-id remap).
--
--   * GROUP_INDICATORS.group_oid -> GROUPS.oid  (fk_group_indicators_group_oid)
--
-- Background: V001 created GROUP_INDICATORS without this FK because the migrated
-- GROUP_OID values belonged to the legacy `zvms.groups` id namespace, so the FK
-- could not be validated (see the V001 header and the GROUP_INDICATORS block
-- comment).  The nightly statistics rework now rewrites
-- GROUP_INDICATORS.GROUP_OID with the current GROUPS.OID inside its
-- single-transaction, set-based rebuild, which closes out the legacy ids and
-- makes the FK satisfiable.
--
-- !! ORDERING WARNING - READ BEFORE APPLYING !!
--   Apply this file ONLY AFTER the target instance has completed its first
--   `rebuild_statistics` run (the rework above).  Applying it while legacy
--   GROUP_OID values are still present makes the constraint validation fail
--   with ORA-02298 ("cannot validate - parent keys not found"), because the
--   existing GROUP_INDICATORS rows do not all reference a GROUPS.OID yet.
--   Run the rebuild first, then apply V009.
--
--   Fresh instances are affected too: V001 keeps the FK deferred and the ETL
--   loads the legacy ids as-is, so a fresh instance must also rebuild once
--   before V009 can be applied.
--
-- No new index: uq_group_indicators_group_oid (plus the existing
-- ix_activity_members_* set) already covers the lookups this pathway needs, so
-- V009 only adds the constraint and nothing else.
--
-- Idempotency:
--   Same EXECUTE IMMEDIATE pattern as V001-V008.  The ALTER swallows ORA-02275
--   (such a referential constraint already exists) and ORA-00955 (name already
--   used); any other error is re-raised, so re-running is a safe no-op.
--
-- This constraint is mirrored by db/models.py.  It lives here rather than in
-- V001 (which cannot carry it - see ORDERING WARNING), so `python -m db.ddl_check`
-- deliberately reports the models-side FK as a V001 difference: the remap-gated
-- FK is the one intentional divergence between models.py and V001.
-- ============================================================================

BEGIN
  EXECUTE IMMEDIATE 'ALTER TABLE group_indicators ADD CONSTRAINT fk_group_indicators_group_oid FOREIGN KEY (group_oid) REFERENCES groups (oid)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-2275, -955) THEN RAISE; END IF;
END;
/
