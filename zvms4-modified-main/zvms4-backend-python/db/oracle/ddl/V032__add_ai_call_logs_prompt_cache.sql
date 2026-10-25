-- ============================================================================
-- V032__add_ai_call_logs_prompt_cache.sql
-- ----------------------------------------------------------------------------
-- 把 prompt 前缀缓存的用量变成**可对账的事实**：在 V001 / V024 / V025 / V031
-- 已应用的实例上给 AI_CALL_LOGS 追加 3 列 + 1 个 CHECK。
--
--   列                类型              语义
--   ----------------------------------------------------------------------
--   CACHE_READ_TOKENS  NUMBER(10)       归一后的命中 token
--   CACHE_WRITE_TOKENS NUMBER(10)       写缓存的 token
--   CACHE_KEY          VARCHAR2(64)     稳定前缀哈希（sha256 前 16 位）
--
-- Background:
--   * CACHE_READ_TOKENS 是**归一后**的命中 token：OpenAI
--     ``prompt_tokens_details.cached_tokens``、Anthropic ``cache_read_input_tokens``、
--     DeepSeek ``prompt_cache_hit_tokens`` 三家的口径都落到这一列。读侧因此只认
--     一列，不必按 provider 分支——provider 分支是归一层的责任，不是报表的。
--   * CACHE_WRITE_TOKENS 是写缓存的 token（Anthropic
--     ``cache_creation_input_tokens``，含 5m / 1h 两档之和 / OpenAI
--     ``cache_write_tokens``）。命中有价值、写入有成本，两者必须分开记，否则
--     「命中率很高但一直在付费写缓存」会被算成净收益。
--   * CACHE_KEY 是我们自己算的**稳定前缀哈希**（sha256 前 16 位，形如
--     ``zvp:1a2b3c4d5e6f7890``），**不含用户内容**——它只回答「这两次调用是不是
--     同一段前缀」，不回答「前缀里写了什么」。长度 64 是给未来的算法变更留余量
--     （换 sha256 全量或多段拼接时不必再来一次 DDL），也与上游
--     ``prompt_cache_key`` 的 64 字符上限同宽。
--   * 全部 nullable，存量行留 NULL。**NULL 不是 0**：上游没报 / 没注入与「命中
--     0 个 token」是两件事，用 0 顶替会把「不知道」伪装成一个观测值，进而把命中
--     率算成 0%。写入侧（``services/ai/usage.py``）因此对缺失字段留 None。
--   * 只给两个 token 列建 CHECK（非负）：它们的不变量是「不可能为负」，与时间
--     窗口无关，适合由数据库兜底。CACHE_KEY 是哈希，取值域由写入侧保证（前缀 +
--     16 位 hex），在这里再抄一份口径只会在归一实现变更后漂移。
--   * **不建索引**：这三列基数低（命中/未命中、少量前缀），而命中率统计是全窗口
--     聚合（一次 SUM 扫过窗口内的行），加索引只会增加写入成本、不会被用到。
--   * 本文件**只管** prompt cache 三列。agent 执行链路的 ROUND_NO /
--     ROLES_SNAPSHOT 由 V033 负责：同一列在两份迁移里各写一次 ALTER 虽然幂等，
--     但会让「这一列到底谁定义的」变成两个答案，读日志排障时多一次无谓的判断。
--
-- Idempotency:
--   Same EXECUTE IMMEDIATE pattern as V024-V031：ALTER TABLE ADD 列吞 ORA-01430
--   （列已存在）/ ORA-00955（对象由更新的基线建成）；ADD CONSTRAINT 吞 ORA-02264
--   （约束名已占用）/ ORA-00955。因此本文件在「V001 + V024/V025/V031 已应用」的
--   存量库与「全新建库」上都能重复执行，且重跑不改变任何已有对象。
--
-- This file is mirrored by db/models.py / V001 (fresh instances get the same
-- three columns and the check from V001); run `python -m db.ddl_check` to
-- verify they stay in sync (offline, no DB access).
-- ============================================================================

-- ----------------------------------------------------------------------------
-- AI_CALL_LOGS：3 列（逐列 ADD，单列失败不会连带回滚其它列）
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE 'ALTER TABLE ai_call_logs ADD (cache_read_tokens NUMBER(10))';
EXCEPTION
  -- -1430: 列已存在（重复执行）；-955: 兜底（对象由更新的基线建成）。
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-1430, -955) THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'ALTER TABLE ai_call_logs ADD (cache_write_tokens NUMBER(10))';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-1430, -955) THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'ALTER TABLE ai_call_logs ADD (cache_key VARCHAR2(64))';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-1430, -955) THEN RAISE; END IF;
END;
/

-- ----------------------------------------------------------------------------
-- 约束：与 db/models.py / V001 逐字一致
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE '
ALTER TABLE ai_call_logs ADD CONSTRAINT ck_ai_call_logs_cache_tokens CHECK ((cache_read_tokens IS NULL OR cache_read_tokens >= 0) AND (cache_write_tokens IS NULL OR cache_write_tokens >= 0))';
EXCEPTION
  -- -2264: 约束名已被占用（重复执行）；-955: 表不存在（对象由更新的基线建成）。
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-2264, -955) THEN RAISE; END IF;
END;
/
