-- ============================================================================
-- V040: AI 定时任务（AI_SCHEDULED_TASKS）+ AI_RUNS.TASK_OID
--
--   在 V001~V039 已应用的实例上补齐：
--     * AI_SCHEDULED_TASKS（用户自建的周期性智能体任务）
--     * AI_RUNS += TASK_OID（可空；非空 = 这次 run 由某条定时任务提交）
--     * IX_AI_RUNS_TASK_CREATED（某任务的执行历史）
--
-- Background:
--
--   * 任务存在库里而不是进程内：调度器重启（部署一天几次）不能让用户的任务
--     消失；多 worker 下也只有库里的 next_run_at 能当唯一真相。
--
--   * REVIEW_STATUS 是**独立于 enabled 的审核状态**，不是「是否启用」的别名：
--     prompt 由用户在运行时改过之后必须重新过审（改 text → 回 draft），但任务
--     本身和它的排程没有变。合并成一个字段等于每次改文案都要重建任务。
--
--   * PROMPT_TEXT 是业务数据，**不复用 AI_PROMPT_AUDIT**：那张表是「捕获日志」
--     （谁在什么时候说了什么，供审计与保留期清理），生命周期按天算；这里是
--     用户资产，要活到用户删掉它。同一份正文进两张表不是重复，是两个事实。
--
--   * SCHEDULE_KIND 只有 interval / cron 两值。interval 用 INTERVAL_MINUTES
--     （下限 15：定时任务会真实消耗额度，1 分钟一次的配置一定是误操作）；
--     cron 用 CRON_EXPR（5 段式，Asia/Shanghai，解析在 services/ai/scheduled_runner）。
--     两个字段都保留、由 SCHEDULE_KIND 决定读哪个：切换排程方式时不丢原文，
--     切回来还是原来那条表达式。
--
--   * NEXT_RUN_AT 是**调度器唯一的取数依据**（`enabled=1 AND next_run_at<=now`），
--     所以它的推进逻辑必须覆盖所有分支：执行、跳过、失败都要写。「失败就不推进」
--     会让一条坏任务每分钟被重试一次，把日志和额度一起打爆。
--
--   * LAST_RUN_AT 兼作**领取令牌**：claim 的 CAS 是
--     `WHERE oid=:oid AND enabled=1 AND next_run_at<=:now AND last_run_at=:expected`
--     （expected 是 SELECT 时读到的值）。两个 tick 并发时只有一个 UPDATE 能命中；
--     而「领取后进程崩溃」不会把任务永久卡死——下一次 tick 重新读快照，expected
--     就是崩溃时写下的那个时间戳，CAS 自然通过。这是不用悲观锁也能自愈的写法。
--
--   * LAST_STATUS 只记**最近一次调度结论**（queued / skipped_no_key /
--     skipped_quota / skipped_busy / skipped_unreviewed / skipped_no_owner /
--     skipped_moderation / failed）。它不是 run 的结果——run 的终态在 AI_RUNS，
--     通过 TASK_OID 关联（GET /api/ai/scheduled-tasks/{oid}/runs）。
--
--   * 不建外键：任务要活过账号删除（同 AI_CALL_LOGS / AI_CONVERSATIONS 的取舍），
--     删用户时由应用层显式清理。
--
-- Idempotency:
--   Same EXECUTE IMMEDIATE pattern as V024-V039.  CREATE TABLE / CREATE INDEX 吞
--   ORA-00955（含 -1408：同列索引已存在）；ALTER TABLE ADD 列吞 ORA-01430（列
--   已存在）/ ORA-00955（表由更新的基线建成）。
--
-- This file is mirrored by db/models.py / V001 (fresh instances get the same table,
-- column and indexes); run `python -m db.ddl_check` to verify models/V001 stay in
-- sync (offline, no DB access).
-- ============================================================================

-- ----------------------------------------------------------------------------
-- AI_SCHEDULED_TASKS: 周期性智能体任务
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE '
CREATE TABLE ai_scheduled_tasks (
  oid VARCHAR2(24 CHAR) NOT NULL,
  owner_oid VARCHAR2(24 CHAR) NOT NULL,
  name VARCHAR2(128 CHAR) NOT NULL,
  prompt_text CLOB NOT NULL,
  review_status VARCHAR2(16 CHAR) DEFAULT ''draft'' NOT NULL,
  reviewed_by_oid VARCHAR2(24 CHAR),
  reviewed_at TIMESTAMP,
  review_note VARCHAR2(512 CHAR),
  schedule_kind VARCHAR2(8 CHAR) DEFAULT ''interval'' NOT NULL,
  interval_minutes NUMBER DEFAULT 1440 NOT NULL,
  cron_expr VARCHAR2(64 CHAR),
  enabled NUMBER(1) DEFAULT 1 NOT NULL,
  model VARCHAR2(64 CHAR),
  last_run_at TIMESTAMP,
  next_run_at TIMESTAMP,
  run_count NUMBER DEFAULT 0 NOT NULL,
  last_status VARCHAR2(16 CHAR),
  last_error VARCHAR2(512 CHAR),
  created_at TIMESTAMP NOT NULL,
  updated_at TIMESTAMP NOT NULL,
  PRIMARY KEY (oid),
  CONSTRAINT ck_ai_scheduled_tasks_oid CHECK (REGEXP_LIKE(oid,''^[0-9a-f]{24}$'')),
  CONSTRAINT ck_ai_scheduled_tasks_review CHECK (review_status IN (''draft'',''pending'',''approved'',''rejected'')),
  CONSTRAINT ck_ai_scheduled_tasks_kind CHECK (schedule_kind IN (''interval'',''cron'')),
  CONSTRAINT ck_ai_scheduled_tasks_interval CHECK (interval_minutes >= 15),
  CONSTRAINT ck_ai_scheduled_tasks_enabled CHECK (enabled IN (0,1))
)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -955 THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE '
CREATE INDEX ix_ai_scheduled_tasks_owner_created ON ai_scheduled_tasks (owner_oid, created_at DESC)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-955, -1408) THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE '
CREATE INDEX ix_ai_scheduled_tasks_due ON ai_scheduled_tasks (enabled, next_run_at)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-955, -1408) THEN RAISE; END IF;
END;
/

-- ----------------------------------------------------------------------------
-- AI_RUNS += TASK_OID
--   可空：绝大多数 run 来自交互式提交。非空只是「谁提交的」这一条归因，读写
--   两侧都按可空处理（老行永远没有它）。
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE 'ALTER TABLE ai_runs ADD (task_oid VARCHAR2(24 CHAR))';
EXCEPTION
  -- -1430: 列已存在（重复执行）；-955: 表不存在（由更新的基线建成）。
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-1430, -955) THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE '
CREATE INDEX ix_ai_runs_task_created ON ai_runs (task_oid, created_at DESC)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-955, -1408) THEN RAISE; END IF;
END;
/
