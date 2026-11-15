-- ============================================================================
-- V047: 结构优化（低档全量 + created_at 默认值）
--
--   在 V001~V046 已应用的实例上补齐：
--     * 新增 2 条索引：BAN_VIOLATIONS(CREATED_AT) /
--                      AI_CALL_LOGS(STATUS, CREATED_AT)
--     * 删除 4 条索引：IX_BAN_VIOLATIONS_USER（前缀冗余，被 _BATCH 完全覆盖）
--                      IX_TIME_ACADEMIC_YEAR_USER_OID（是 uq 的列前缀）
--                      IX_AI_MESSAGES_CONV_CREATED（无任何查询按 created_at 读）
--                      IX_APP_LOGS_TS_AT（清理已对齐到 ts_raw，ts_at 只留数据）
--     * 删除死表 LOGS（全仓零读写）
--     * 26 张有 CREATED_AT 却没有默认值的表补 DEFAULT SYSTIMESTAMP
--       （AI_API_KEYS 早已有该默认值，不重复加）
--
-- Background:
--
--   * BAN_VIOLATIONS 的管理页是「全表按 created_at 倒序 + 分页」，没有任何前导
--     谓词。既有的两条复合索引都以 user_oid 打头，服务不了这个形状，只能全表扫
--     再排序。新增的单列索引是这个排序唯一的访问路径；它同时让 365 天保留期的
--     清理（created_at < cutoff）不再全表扫。
--
--   * AI_CALL_LOGS 的管理页按 status（可选 provider）过滤后按 created_at 倒序。
--     既有复合索引是 (endpoint, status, created_at)：首列是 endpoint，单 status
--     过滤吃不到它。等值列在前、范围/排序列在后，(status, created_at) 才是那种
--     形状的访问路径。
--
--   * IX_BAN_VIOLATIONS_USER (user_oid, trigger_code, created_at) 是
--     IX_BAN_VIOLATIONS_BATCH (user_oid, trigger_code, created_at, batch_id) 的
--     完整前缀：任何能用前者的查询都能用后者，反之不然，所以前者纯属多一份写
--     放大（V019 当时为「不改存量执行计划」保留，本轮明确回收）。
--
--   * IX_TIME_ACADEMIC_YEAR_USER_OID (user_oid) 是
--     UQ_TIME_ACADEMIC_YEAR_USER_YEAR (user_oid, academic_year) 的唯一索引前缀：
--     按 user_oid 的等值查找（仓储唯一的读形状）走 uq 的唯一索引即可。
--
--   * IX_AI_MESSAGES_CONV_CREATED (conversation_oid, created_at) 只与
--     UQ_AI_MESSAGES_CONV_SEQ (conversation_oid, seq) 共享首列，不是严格前缀；
--     删它是因为**没有任何查询按 created_at 读这张表**：仓储只有「按会话取
--     MAX(seq)」「按会话 + seq > ? 升序取消息」「按会话批删」三种形状，全部
--     走 uq 的唯一索引。留着它只增加每条消息插入的成本。
--
--   * APP_LOGS 有两个时间列，此前口径分裂：管理页排序/时间窗用 TS_RAW
--     （BINARY_DOUBLE，原始 epoch 秒），保留期清理用 TS_AT（naive TIMESTAMP,
--     写入时由 TS_RAW 派生）。TS_RAW 才是语义更稳的那一列——它不受会话时区影响，
--     而 TS_AT 派生于本地时区、且比较时也按本地时区解释。本轮把清理对齐到
--     TS_RAW（边界值不变：cutoff.timestamp() == 派生 TS_AT 用的同一个本地时区
--     换算），TS_AT 只留数据并撤掉索引。注意：**不重写存量数据**，两列内容不动。
--
--   * LOGS 是 zvms_new 设备侧的遗留表，全仓零读零写（运行期日志写
--     APP_LOGS）。它不再出现在 V001 里，存量实例由本文件 DROP。
--
--   * CREATED_AT 补默认值只是「漏传时不至于 500」的兜底：应用侧仍然显式写
--     created_at（审计要的是事实时间，不是插入时间），且**不回填历史行**。
--     SYSTIMESTAMP 落进 TIMESTAMP 列时按会话时区换算，与 datetime.now() 的
--     本地口径一致。
--
-- Idempotency:
--   CREATE INDEX 吞 ORA-00955（名字已被占用）/ ORA-01408（索引已存在）；
--   DROP INDEX 吞 ORA-01418（索引不存在）；DROP TABLE 吞 ORA-00942（表不存在）；
--   ALTER TABLE MODIFY 吞 ORA-00942（表不存在）/ ORA-00904（列不存在）——
--   MODIFY ... DEFAULT 本身可重复执行，重复跑不会报错。
--
-- This file is mirrored by db/models.py / V001 (fresh instances get the same
-- indexes, no LOGS table and the same column defaults); run
-- `python -m db.ddl_check` to verify models/V001 stay in sync (offline, no DB
-- access).
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 1. 新增索引：管理页的两种排序/过滤形状
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE
    'CREATE INDEX ix_ban_violations_created ON ban_violations (created_at)';
EXCEPTION
  -- -955: 名字已被占用；-1408: 等价索引已存在。
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-955, -1408) THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE
    'CREATE INDEX ix_ai_call_logs_status_created ON ai_call_logs (status, created_at)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-955, -1408) THEN RAISE; END IF;
END;
/

-- ----------------------------------------------------------------------------
-- 2. 删除冗余/无用索引
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE 'DROP INDEX ix_ban_violations_user';
EXCEPTION
  -- -1418: 索引不存在（已经删过，或基线里就没有）。
  WHEN OTHERS THEN
    IF SQLCODE != -1418 THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'DROP INDEX ix_time_academic_year_user_oid';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -1418 THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'DROP INDEX ix_ai_messages_conv_created';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -1418 THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'DROP INDEX ix_app_logs_ts_at';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -1418 THEN RAISE; END IF;
END;
/

-- ----------------------------------------------------------------------------
-- 3. 死表下线
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE 'DROP TABLE logs CASCADE CONSTRAINTS';
EXCEPTION
  -- -942: 表不存在。
  WHEN OTHERS THEN
    IF SQLCODE != -942 THEN RAISE; END IF;
END;
/

-- ----------------------------------------------------------------------------
-- 4. CREATED_AT 默认值（26 张表；不覆盖已有默认值的 AI_API_KEYS）
--    顺序与 V001 中的建表顺序一致，方便对照。
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE 'ALTER TABLE activities MODIFY (created_at DEFAULT SYSTIMESTAMP)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-942, -904) THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'ALTER TABLE daily_exports MODIFY (created_at DEFAULT SYSTIMESTAMP)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-942, -904) THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'ALTER TABLE files MODIFY (created_at DEFAULT SYSTIMESTAMP)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-942, -904) THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'ALTER TABLE file_ocr MODIFY (created_at DEFAULT SYSTIMESTAMP)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-942, -904) THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'ALTER TABLE file_events MODIFY (created_at DEFAULT SYSTIMESTAMP)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-942, -904) THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'ALTER TABLE user_device_bindings MODIFY (created_at DEFAULT SYSTIMESTAMP)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-942, -904) THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'ALTER TABLE dual_auth_uses MODIFY (created_at DEFAULT SYSTIMESTAMP)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-942, -904) THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'ALTER TABLE ban_rules MODIFY (created_at DEFAULT SYSTIMESTAMP)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-942, -904) THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'ALTER TABLE ban_records MODIFY (created_at DEFAULT SYSTIMESTAMP)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-942, -904) THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'ALTER TABLE notifications MODIFY (created_at DEFAULT SYSTIMESTAMP)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-942, -904) THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'ALTER TABLE temp_login_passwords MODIFY (created_at DEFAULT SYSTIMESTAMP)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-942, -904) THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'ALTER TABLE notification_versions MODIFY (created_at DEFAULT SYSTIMESTAMP)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-942, -904) THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'ALTER TABLE device_challenges MODIFY (created_at DEFAULT SYSTIMESTAMP)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-942, -904) THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'ALTER TABLE banned_devices MODIFY (created_at DEFAULT SYSTIMESTAMP)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-942, -904) THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'ALTER TABLE ban_violations MODIFY (created_at DEFAULT SYSTIMESTAMP)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-942, -904) THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'ALTER TABLE user_warnings MODIFY (created_at DEFAULT SYSTIMESTAMP)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-942, -904) THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'ALTER TABLE ai_call_logs MODIFY (created_at DEFAULT SYSTIMESTAMP)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-942, -904) THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'ALTER TABLE ai_credentials MODIFY (created_at DEFAULT SYSTIMESTAMP)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-942, -904) THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'ALTER TABLE ai_ocr_engines MODIFY (created_at DEFAULT SYSTIMESTAMP)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-942, -904) THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'ALTER TABLE ai_agent_events MODIFY (created_at DEFAULT SYSTIMESTAMP)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-942, -904) THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'ALTER TABLE ai_prompt_audit MODIFY (created_at DEFAULT SYSTIMESTAMP)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-942, -904) THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'ALTER TABLE ai_conversations MODIFY (created_at DEFAULT SYSTIMESTAMP)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-942, -904) THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'ALTER TABLE ai_messages MODIFY (created_at DEFAULT SYSTIMESTAMP)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-942, -904) THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'ALTER TABLE ai_runs MODIFY (created_at DEFAULT SYSTIMESTAMP)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-942, -904) THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'ALTER TABLE ai_scheduled_tasks MODIFY (created_at DEFAULT SYSTIMESTAMP)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-942, -904) THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'ALTER TABLE ai_quota_requests MODIFY (created_at DEFAULT SYSTIMESTAMP)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-942, -904) THEN RAISE; END IF;
END;
/
