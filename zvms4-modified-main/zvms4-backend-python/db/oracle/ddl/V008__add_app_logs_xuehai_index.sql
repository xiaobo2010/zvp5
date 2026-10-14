-- ============================================================================
-- V008__add_app_logs_xuehai_index.sql
-- ----------------------------------------------------------------------------
-- Adds one index to APP_LOGS so the admin log browser can be filtered by the
-- captured device 学海 ID (docs/DEVICE_TRACKING_DESIGN.md):
--
--   * ix_app_logs_xuehai_id   ON app_logs (xuehai_id)
--
-- Background: APP_LOGS stores the 学海 (XueHai) device user id of the requesting
-- device in XUEHAI_ID (written from the `Xuehai-Id` request header). The log
-- endpoint GET /api/logs now accepts a `xuehai_id` equality filter so an admin
-- can trace one physical pad to the platform accounts that used it. Without an
-- index that filter degrades to a full scan of the log table.
--
-- Idempotency:
--   Same EXECUTE IMMEDIATE pattern as V001-V007.  CREATE INDEX swallows
--   ORA-00955 (name already used) and ORA-01408 (index already exists) so
--   re-running against an instance that already has the index is a safe no-op;
--   any other error is re-raised.
--
-- This file is mirrored by db/models.py / V001 (fresh instances get the same
-- index from V001); run `python -m db.ddl_check` to verify they stay in sync
-- (offline, no DB access).
-- ============================================================================

BEGIN
  EXECUTE IMMEDIATE 'CREATE INDEX ix_app_logs_xuehai_id ON app_logs (xuehai_id)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-955, -1408) THEN RAISE; END IF;
END;
/
