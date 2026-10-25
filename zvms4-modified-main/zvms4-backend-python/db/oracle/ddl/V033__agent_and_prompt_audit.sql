-- ============================================================================
-- V033__agent_and_prompt_audit.sql
-- ----------------------------------------------------------------------------
-- 相邻的 V032__add_ai_call_logs_prompt_cache.sql（prompt 前缀缓存车道）只加
-- cache 观测列；AI_CALL_LOGS 的 ROUND_NO / ROLES_SNAPSHOT 归本文件。两份迁移
-- 互不重叠、各自幂等（重跑吞 ORA-01430 / ORA-02264），任意顺序执行均可。
--
-- agent（智能体）执行链路的三件事，在 V001 / V031 已应用的实例上补齐：
--
--   AI_CALL_LOGS += ROUND_NO / ROLES_SNAPSHOT（2 列 + 1 个 CHECK）
--   AI_AGENT_EVENTS   ← new（每次工具调用的留痕）
--   AI_PROMPT_AUDIT   ← new（受控的 prompt 正文留存，**本轮只建表**）
--   BAN_RULES += ai_chat_burst 种子（只告警，不封禁）
--
-- 列 / 表 / 索引的语义：
--
--   对象                        语义
--   ------------------------------------------------------------------------
--   ai_call_logs.round_no       agent 一次 run 的第几轮（1 起）；NULL = 非 agent
--   ai_call_logs.roles_snapshot 调用时的角色快照（逗号连接，≤64 字符）
--   ai_agent_events             一次工具调用尝试一行（含 denied/expired）
--   ai_prompt_audit             prompt 正文；写入逻辑由后续监控车道实现
--
-- Background:
--   * **一次 agent run 的每一轮都是一次独立的上游调用、各扣一次额度**，所以
--     每轮各落一条 AI_CALL_LOGS（purpose='agent'，request_id 全轮共用），而不是
--     一次 run 只记一行。ROUND_NO 让「这一轮是第几次」变成事实，不必靠时间猜。
--   * ROLES_SNAPSHOT 是**快照**而不是外键/现查：角色可变，事后改角色会让「当时
--     凭什么放行」失真。chat / OCR 路径同样受益（缺省 NULL = 未采集）。
--   * CAPABILITY 仍只有 ocr/chat 两值——**不**为 agent 新增枚举：agent 是一次
--     chat 能力的高级用法，场景由 PURPOSE='agent' 区分。加枚举需要一整轮 DDL
--     与读侧改造，而两种 kind 的额度口径本来就相同。
--   * ai_agent_events.OID 是 32 位 uuid4().hex（不是 24 位 ObjectId）：提案 oid
--     直接当对外句柄（confirm / reject 的路径参数），与业务 oid 空间分开，避免
--     有人拿活动 id 去撞提案。
--   * ARGS_HASH 是**已落库 ARGS_SUMMARY 的 sha256**，不是原始参数的哈希。确认时
--     必须重算并比对，所以可执行的参数要以可复原的形式落在这张表里（两条写工具
--     的参数都是小 JSON，500 字符足够；超长直接拒绝建提案，不截断——截断会让
--     hash 校验永远失败）。
--   * AI_AGENT_EVENTS / AI_PROMPT_AUDIT 都不建外键：审计材料要活过账号删除
--     （同 AI_CALL_LOGS / BAN_VIOLATIONS）。
--   * ai_chat_burst 只建种子（threshold 80 / 1 小时 / mode=alert），**不实现
--     消费逻辑**：检查项注册与触发判定属于后续监控车道。mode=alert 意味着即使
--     有人在 risk_monitor 里接上它，也只写证据 + 告警，不封人。
--
-- Idempotency:
--   Same EXECUTE IMMEDIATE pattern as V024-V031：ALTER TABLE ADD 列吞 ORA-01430
--   （列已存在）/ ORA-00955（对象由更新的基线建成）；ADD CONSTRAINT 吞 ORA-02264
--   / ORA-00955；CREATE TABLE / CREATE INDEX 吞 ORA-00955（含 -1408：同列索引
--   已存在）；MERGE 的 WHEN NOT MATCHED 分支让种子可重复执行，且**不覆盖**管理
--   员调过的 threshold / window_hours / mode / penalties。
--
-- This file is mirrored by db/models.py / V001 (fresh instances get the same
-- columns, tables, indexes and seed from V001); run `python -m db.ddl_check` to
-- verify they stay in sync (offline, no DB access).
-- ============================================================================

-- ----------------------------------------------------------------------------
-- AI_CALL_LOGS：2 列（逐列 ADD，单列失败不会连带回滚其它列）
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE 'ALTER TABLE ai_call_logs ADD (round_no NUMBER(3))';
EXCEPTION
  -- -1430: 列已存在（重复执行）；-955: 兜底（对象由更新的基线建成）。
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-1430, -955) THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'ALTER TABLE ai_call_logs ADD (roles_snapshot VARCHAR2(64 CHAR))';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-1430, -955) THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE '
ALTER TABLE ai_call_logs ADD CONSTRAINT ck_ai_call_logs_round_no CHECK (round_no IS NULL OR round_no >= 1)';
EXCEPTION
  -- -2264: 约束名已被占用（重复执行）；-955: 表不存在（由更新的基线建成）。
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-2264, -955) THEN RAISE; END IF;
END;
/

-- ----------------------------------------------------------------------------
-- AI_AGENT_EVENTS：每次工具调用尝试一行
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE '
CREATE TABLE ai_agent_events (
  oid VARCHAR2(32 CHAR) NOT NULL,
  request_id VARCHAR2(64 CHAR),
  round_no NUMBER(3),
  tool_name VARCHAR2(48 CHAR),
  tool_kind VARCHAR2(8 CHAR),
  args_hash VARCHAR2(64 CHAR),
  args_summary VARCHAR2(500 CHAR),
  result_summary VARCHAR2(500 CHAR),
  status VARCHAR2(12 CHAR) NOT NULL,
  actor_oid VARCHAR2(32 CHAR),
  created_at TIMESTAMP NOT NULL,
  PRIMARY KEY (oid),
  CONSTRAINT ck_ai_agent_events_tool_kind CHECK (tool_kind IS NULL OR tool_kind IN (''read'',''write'')),
  CONSTRAINT ck_ai_agent_events_status CHECK (status IN (''executed'',''proposed'',''confirmed'',''rejected'',''expired'',''failed'',''denied''))
)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -955 THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'CREATE INDEX ix_ai_agent_events_request_id ON ai_agent_events (request_id)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-955, -1408) THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'CREATE INDEX ix_ai_agent_events_status_created ON ai_agent_events (status, created_at)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-955, -1408) THEN RAISE; END IF;
END;
/

-- ----------------------------------------------------------------------------
-- AI_PROMPT_AUDIT：正文留存的**表就位**，写入逻辑留给后续监控车道
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE '
CREATE TABLE ai_prompt_audit (
  oid VARCHAR2(32 CHAR) NOT NULL,
  request_id VARCHAR2(64 CHAR),
  user_oid VARCHAR2(32 CHAR),
  roles_snapshot VARCHAR2(64 CHAR),
  purpose VARCHAR2(24 CHAR),
  prompt_text CLOB,
  created_at TIMESTAMP NOT NULL,
  PRIMARY KEY (oid)
)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -955 THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'CREATE INDEX ix_ai_prompt_audit_user_created ON ai_prompt_audit (user_oid, created_at)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-955, -1408) THEN RAISE; END IF;
END;
/

-- ----------------------------------------------------------------------------
-- 内置规则种子: AI 对话请求突发（只告警不封禁；消费逻辑在后续车道）
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE '
MERGE INTO ban_rules t
USING (SELECT ''0b000000000000000000000b'' oid FROM dual) s
ON (t.code = ''ai_chat_burst'')
WHEN NOT MATCHED THEN INSERT
  (oid, code, name, trigger_code, enabled, config, penalties, builtin, created_at, updated_at)
  VALUES (''0b000000000000000000000b'', ''ai_chat_burst'', ''AI 对话请求突发'',
          ''ai_chat_burst'', 1, ''{"threshold":80,"window_hours":1,"mode":"alert"}'',
          ''null'', 1, SYSTIMESTAMP, SYSTIMESTAMP)';
EXCEPTION
  -- -1: 并发执行时另一会话已插入（uq_ban_rules_code），结果一致，忽略。
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-1, -955) THEN RAISE; END IF;
END;
/
