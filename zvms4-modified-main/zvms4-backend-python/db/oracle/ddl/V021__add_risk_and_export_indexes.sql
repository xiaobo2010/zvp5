-- V021: 风控计数与导出列表的索引补齐。
--
-- Background:
--
--   * `challenge_flood` 按 `(user_oid, created_at)` 统计窗口内挑战数，而
--     `device_challenges` 只有 `token_hash` 的唯一索引；没有这条索引时每次
--     登录擦边都要全表扫一遍，且该表没有保留期任务会持续增长。
--   * `GET /api/exports` 的时间窗过滤与 `ORDER BY task_start DESC, oid DESC`
--     都没有可用索引，只能扫 `export_tasks` 后再截断。
--
-- Idempotency:
--   Same EXECUTE IMMEDIATE pattern as V001-V020: swallow ORA-00955 (name
--   already used) and ORA-01408 (such column list already indexed).
--
-- This file is mirrored by db/models.py / V001 (fresh instances get the same
-- indexes from the initial schema).

BEGIN
  EXECUTE IMMEDIATE 'CREATE INDEX ix_device_challenges_user_created ON device_challenges (user_oid, created_at)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-955, -1408) THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'CREATE INDEX ix_export_tasks_task_start ON export_tasks (task_start)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-955, -1408) THEN RAISE; END IF;
END;
/
