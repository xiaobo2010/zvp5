-- ============================================================================
-- V035__ai_conversations.sql
-- ----------------------------------------------------------------------------
-- 持久会话 + 后台任务化的智能体执行（docs/AI_CONVERSATION_DESIGN.md §2）：
--
--   AI_CONVERSATIONS  ← new（一段会话，oid 是对外句柄）
--   AI_MESSAGES       ← new（会话内每一条消息，seq 会话内递增）
--   AI_RUNS           ← new（一次智能体执行；oid 同时是 request_id）
--
-- 列 / 索引的语义：
--
--   对象                          语义
--   ------------------------------------------------------------------------
--   ai_conversations.mode         'chat' | 'agent'，建会话时定，不可改（物理名带引号，见下）
--   ai_conversations.status       'active' | 'archived'；保留期只删 archived
--   ai_conversations.summary      压缩摘要正文；NULL = 从未压缩
--   ai_conversations.summary_upto_seq  已压入摘要的最大 seq；0 = 未压缩
--   ai_conversations.context_tokens    **最近一次**请求的 prompt token（水位）
--   ai_conversations.message_count    冗余计数：列表页与删除回执不 COUNT 消息表
--   ai_messages.seq               会话内从 1 起，UNIQUE(conversation_oid, seq)
--   ai_messages.role              'user' | 'assistant' | 'tool'（**没有** summary）
--   ai_messages.round_no          所属 run 的轮次，与 ai_call_logs 对齐后逐轮 join
--   ai_runs.status                queued/running/succeeded/failed/cancelled/interrupted
--   ai_runs.result_message_oid    最终 assistant 消息 oid（前端不发第二次查询）
--
-- Background:
--   * 三张表之间**不建外键**（与全库一致：删用户/删会话都要显式批删）。删除顺序
--     由应用层保证 messages → runs → conversation，因为 Oracle 没有
--     ON DELETE CASCADE，靠约束级联反而会在删除大会话时长时间持锁。
--   * **活跃 run 的唯一性由数据库兜底，不是可选项**。应用层的
--     ``SELECT ... FOR UPDATE`` + 检查只是第一道闸；两个并发 POST 仍可能都通过
--     检查。``uq_ai_runs_active_conv`` / ``uq_ai_runs_active_owner`` 是函数唯一
--     索引：键只在 status 是 queued|running 时才求值，终态行键为 NULL，而 Oracle
--     不索引全 NULL 键，于是终态行不互斥。冲突报 ORA-00001 → 409 而不是两条
--     并行 run 同写一段会话。
--   * ``uq_ai_runs_client_msg`` 是客户端幂等键（可空）：重试同一 client_msg_id 时
--     命中唯一索引 → 返回已存在的 run，不重复写消息、不重复计费。NULL 不参与
--     唯一性（Oracle 里 NULL 互不相等），所以不带幂等键的提交不受影响。
--   * ``ai_messages.content`` / ``tool_calls`` 用 CLOB：工具结果全文 ≤8000 字符，
--     而 VARCHAR2 的上限是 4000。**列表查询不得 select content**（单会话 200 轮
--     ≈1.6MB），投影列由仓储层的 ``_columns()`` 与 ``_list_columns()`` 分开钉住。
--   * 计数字段（tokens_* / message_count / summary_upto_seq / cancel_requested）
--     一律 DEFAULT 0 NOT NULL：它们是**累加器**，NULL 会让每次累加都要先判空，
--     而且「0 次调用」是真实值，不是未知。上游 token 用量本身仍是可空的
--     （ai_messages 的 token 列 NULL = 上游没报，与 ai_call_logs 同口径）。
--   * ``compaction_degraded_at`` 可空：NULL = 压缩一直正常；写入后不清零，它是
--     「最近一次降级」的事实，不是当前状态开关。
--
-- Idempotency:
--   Same EXECUTE IMMEDIATE pattern as V024-V034：CREATE TABLE 吞 ORA-00955
--   （对象已存在）；CREATE INDEX 吞 ORA-00955 / ORA-01408（索引已存在）。
--   本文件只建新对象、不 ALTER 既有表，因此 ORA-01430 / ORA-02264 / ORA-02443
--   这几类「列/约束冲突」在这里不可达，不写进 WHEN 列表（写了只会掩盖真实错误）。
--
-- This file is mirrored by db/models.py / V001 (fresh instances get the same
-- three tables, checks and indexes from V001); run `python -m db.ddl_check` to
-- verify they stay in sync (offline, no DB access).
-- ============================================================================

-- ----------------------------------------------------------------------------
-- AI_CONVERSATIONS：一段会话一行
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE '
CREATE TABLE ai_conversations (
  oid VARCHAR2(32 CHAR) NOT NULL,
  owner_oid VARCHAR2(24 CHAR) NOT NULL,
  "MODE" VARCHAR2(8 CHAR) NOT NULL,
  title VARCHAR2(120 CHAR),
  model VARCHAR2(120 CHAR),
  status VARCHAR2(12 CHAR) DEFAULT ''active'' NOT NULL,
  summary CLOB,
  summary_upto_seq NUMBER(10) DEFAULT 0 NOT NULL,
  tokens_prompt NUMBER(12) DEFAULT 0 NOT NULL,
  tokens_completion NUMBER(12) DEFAULT 0 NOT NULL,
  tokens_total NUMBER(12) DEFAULT 0 NOT NULL,
  context_tokens NUMBER(12) DEFAULT 0 NOT NULL,
  message_count NUMBER(10) DEFAULT 0 NOT NULL,
  compaction_degraded_at TIMESTAMP,
  created_at TIMESTAMP NOT NULL,
  updated_at TIMESTAMP NOT NULL,
  PRIMARY KEY (oid),
  CONSTRAINT ck_ai_conversations_mode CHECK ("MODE" IN (''chat'',''agent'')),
  CONSTRAINT ck_ai_conversations_status CHECK (status IN (''active'',''archived''))
)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -955 THEN RAISE; END IF;
END;
/

-- 左栏列表的唯一查询形状：按 (owner, status) 过滤、updated_at 倒序键集分页。
BEGIN
  EXECUTE IMMEDIATE '
CREATE INDEX ix_ai_conversations_owner_status_updated ON ai_conversations (owner_oid, status, updated_at DESC)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-955, -1408) THEN RAISE; END IF;
END;
/

-- ----------------------------------------------------------------------------
-- AI_MESSAGES：会话内每一条消息一行
--   UNIQUE(conversation_oid, seq) 是表的唯一约束而不是单独索引：seq 分配在会话
--   行锁内完成，并发写同一会话时由它兜底（ORA-00001 → 重试而不是写重号）。
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE '
CREATE TABLE ai_messages (
  oid VARCHAR2(32 CHAR) NOT NULL,
  conversation_oid VARCHAR2(32 CHAR) NOT NULL,
  seq NUMBER(10) NOT NULL,
  role VARCHAR2(12 CHAR) NOT NULL,
  content CLOB,
  tool_calls CLOB,
  tool_call_id VARCHAR2(64 CHAR),
  tool_name VARCHAR2(64 CHAR),
  round_no NUMBER(3),
  finish_reason VARCHAR2(24 CHAR),
  request_id VARCHAR2(64 CHAR),
  prompt_tokens NUMBER(10),
  completion_tokens NUMBER(10),
  total_tokens NUMBER(10),
  cache_read_tokens NUMBER(10),
  cache_write_tokens NUMBER(10),
  created_at TIMESTAMP NOT NULL,
  PRIMARY KEY (oid),
  CONSTRAINT uq_ai_messages_conv_seq UNIQUE (conversation_oid, seq),
  CONSTRAINT ck_ai_messages_role CHECK (role IN (''user'',''assistant'',''tool'')),
  CONSTRAINT ck_ai_messages_seq CHECK (seq >= 1),
  CONSTRAINT ck_ai_messages_round_no CHECK (round_no IS NULL OR round_no >= 1)
)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -955 THEN RAISE; END IF;
END;
/

-- 会话内回放按 seq 走唯一约束的索引；这条按 created_at 服务「按时间清理/审计」，
-- 与 seq 的顺序无关（导入的历史消息 seq 与落库时间可以不同）。
BEGIN
  EXECUTE IMMEDIATE '
CREATE INDEX ix_ai_messages_conv_created ON ai_messages (conversation_oid, created_at)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-955, -1408) THEN RAISE; END IF;
END;
/

-- ----------------------------------------------------------------------------
-- AI_RUNS：一次智能体执行一行；oid 同时是 ai_call_logs.request_id 与
-- ai_agent_events.request_id，三条链路靠它串起来。
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE '
CREATE TABLE ai_runs (
  oid VARCHAR2(32 CHAR) NOT NULL,
  conversation_oid VARCHAR2(32 CHAR) NOT NULL,
  owner_oid VARCHAR2(24 CHAR) NOT NULL,
  client_msg_id VARCHAR2(64 CHAR),
  status VARCHAR2(16 CHAR) NOT NULL,
  model VARCHAR2(120 CHAR),
  stop_reason VARCHAR2(24 CHAR),
  rounds NUMBER(3),
  error_code VARCHAR2(40 CHAR),
  error_message VARCHAR2(500 CHAR),
  result_message_oid VARCHAR2(32 CHAR),
  prompt_tokens NUMBER(12),
  completion_tokens NUMBER(12),
  total_tokens NUMBER(12),
  cancel_requested NUMBER(1) DEFAULT 0 NOT NULL,
  heartbeat_at TIMESTAMP,
  created_at TIMESTAMP NOT NULL,
  started_at TIMESTAMP,
  finished_at TIMESTAMP,
  PRIMARY KEY (oid),
  CONSTRAINT ck_ai_runs_status CHECK (status IN (''queued'',''running'',''succeeded'',''failed'',''cancelled'',''interrupted'')),
  CONSTRAINT ck_ai_runs_cancel_requested CHECK (cancel_requested IN (0,1))
)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -955 THEN RAISE; END IF;
END;
/

-- ----------------------------------------------------------------------------
-- 活跃 run 的唯一性（评审 P0-1）：函数唯一索引只在 queued|running 时求值
--   键为 NULL 的行不进索引（Oracle 不索引全 NULL 键），所以终态行不互斥。
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE '
CREATE UNIQUE INDEX uq_ai_runs_active_conv ON ai_runs (CASE WHEN status IN (''queued'',''running'') THEN conversation_oid END)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-955, -1408) THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE '
CREATE UNIQUE INDEX uq_ai_runs_active_owner ON ai_runs (CASE WHEN status IN (''queued'',''running'') THEN owner_oid END)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-955, -1408) THEN RAISE; END IF;
END;
/

-- 客户端幂等键：重试同一 client_msg_id → 命中唯一索引 → 返回已有 run。
BEGIN
  EXECUTE IMMEDIATE '
CREATE UNIQUE INDEX uq_ai_runs_client_msg ON ai_runs (conversation_oid, client_msg_id)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-955, -1408) THEN RAISE; END IF;
END;
/

-- 重连横幅（GET /runs?active=1）与每用户活跃数上限都按 (owner, status) 过滤。
BEGIN
  EXECUTE IMMEDIATE '
CREATE INDEX ix_ai_runs_owner_status ON ai_runs (owner_oid, status)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-955, -1408) THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE '
CREATE INDEX ix_ai_runs_conv_created ON ai_runs (conversation_oid, created_at DESC)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-955, -1408) THEN RAISE; END IF;
END;
/

-- watchdog 每 30s 扫「running 且心跳过期」的行；没有它每次都是全表扫 running。
BEGIN
  EXECUTE IMMEDIATE '
CREATE INDEX ix_ai_runs_status_heartbeat ON ai_runs (status, heartbeat_at)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-955, -1408) THEN RAISE; END IF;
END;
/
