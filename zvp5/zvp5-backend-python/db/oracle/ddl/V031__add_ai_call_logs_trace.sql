-- ============================================================================
-- V031__add_ai_call_logs_trace.sql
-- ----------------------------------------------------------------------------
-- 把 AI_CALL_LOGS 细化到「可审计」粒度：在 V001 / V024 / V025 已应用的实例上
-- 追加 8 列 + 1 个索引。
--
--   列             类型              语义
--   ----------------------------------------------------------------------
--   PROMPT_TOKENS     NUMBER(10)      上游 usage 报的输入 token 数
--   COMPLETION_TOKENS NUMBER(10)      上游 usage 报的输出 token 数
--   TOTAL_TOKENS      NUMBER(10)      上游 usage 报的合计（= 前两者，由上游给）
--   ATTEMPT_NO        NUMBER(3)       同一次请求的第几次上游尝试（1 起）
--   PURPOSE           VARCHAR2(24)    调用场景（X-AI-Scene / OCR 固定 'ocr'）
--   FIRST_TOKEN_MS    NUMBER(10)      流式首块到达耗时（毫秒）
--   ENGINE_KIND       VARCHAR2(24)    OCR 引擎类型（tencent/aliyun/...）
--   ATTACHMENT_OID    VARCHAR2(24)    OCR 针对的附件 oid
--
--   索引
--   ix_ai_call_logs_request_id ON ai_call_logs (request_id)
--
-- Background:
--   * 正文（prompt / completion）**一律不落库**，这是既定合规决定，本轮维持不
--     动；本迁移只加元数据。字段命名对齐 OTel GenAI 语义约定的子集（当自有
--     schema 管理，不承诺外部兼容）。
--   * 全部 nullable，存量行留 NULL。**NULL 不是 0**：token 数未知与「0 个
--     token」是两件事，用 0 顶替会把「不知道」伪装成一个观测值。
--   * 不变式：三个 token 列为 NULL ⟺ 该行的 UNITS 是**估算值**（流式上游没回
--     usage 帧时按「4 字符 ≈ 1 token」折算）。上游回了 usage，token 列与 UNITS
--     都来自它。写进注释是为了让报表能一眼判断「这行额度可不可信」。
--   * ATTEMPT_NO 从 1 起：chat 的多 provider 重试与 OCR 的引擎回退都是「每次
--     尝试落一行」，此前这些行只能靠「同 user + 相邻时间」猜是不是同一次请求，
--     attempt_no 把「第几次」变成事实。配合 REQUEST_ID（chat 已在用，OCR 本轮
--     补上）就能整条链聚合。
--   * PURPOSE 是**开放集合**，刻意不建 CHECK：新增一个前端场景不该需要一次
--     DDL 迁移（与 ai_quota.kind 加引擎时的取舍同源）。ENGINE_KIND 同理——
--     它的取值域由 AI_OCR_ENGINES.KIND 的 CHECK 守着，这里再抄一份只会漂移。
--   * 只给 token 列与 attempt_no 建 CHECK（非负 / ≥1）：它们的不变量是
--     「不可能为负」，与时间窗口无关，适合由数据库兜底。
--   * OCR 的三列（ENGINE_KIND / ATTACHMENT_OID / ATTEMPT_NO）让
--     「这张图试过哪几个引擎、第几次成的、耗时多少」不再需要跨表猜。
--   * ATTACHMENT_OID 不建外键：审计行要能活过附件删除（同 USER_OID 不建外键）。
--
-- Idempotency:
--   Same EXECUTE IMMEDIATE pattern as V024-V030：ALTER TABLE ADD 列吞 ORA-01430
--   （列已存在）/ ORA-00955（表已由更新的基线带上这一列）；ADD CONSTRAINT 吞
--   ORA-02264（约束名已占用）/ ORA-00955；CREATE INDEX 吞 ORA-00955 / ORA-01408。
--   因此本文件在「V001 + V024/V025 已应用」的存量库与「全新建库」上都能重复
--   执行，且重跑不改变任何已有对象。
--
-- This file is mirrored by db/models.py / V001 (fresh instances get the same
-- columns and index from V001); run `python -m db.ddl_check` to verify they
-- stay in sync (offline, no DB access).
-- ============================================================================

-- ----------------------------------------------------------------------------
-- AI_CALL_LOGS：8 列（逐列 ADD，单列失败不会连带回滚其它列）
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE 'ALTER TABLE ai_call_logs ADD (prompt_tokens NUMBER(10))';
EXCEPTION
  -- -1430: 列已存在（重复执行）；-955: 兜底（对象由更新的基线建成）。
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-1430, -955) THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'ALTER TABLE ai_call_logs ADD (completion_tokens NUMBER(10))';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-1430, -955) THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'ALTER TABLE ai_call_logs ADD (total_tokens NUMBER(10))';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-1430, -955) THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'ALTER TABLE ai_call_logs ADD (attempt_no NUMBER(3))';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-1430, -955) THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'ALTER TABLE ai_call_logs ADD (purpose VARCHAR2(24))';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-1430, -955) THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'ALTER TABLE ai_call_logs ADD (first_token_ms NUMBER(10))';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-1430, -955) THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'ALTER TABLE ai_call_logs ADD (engine_kind VARCHAR2(24))';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-1430, -955) THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'ALTER TABLE ai_call_logs ADD (attachment_oid VARCHAR2(24))';
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
ALTER TABLE ai_call_logs ADD CONSTRAINT ck_ai_call_logs_tokens CHECK ((prompt_tokens IS NULL OR prompt_tokens >= 0) AND (completion_tokens IS NULL OR completion_tokens >= 0) AND (total_tokens IS NULL OR total_tokens >= 0))';
EXCEPTION
  -- -2264: 约束名已被占用（重复执行）；-955: 表不存在（对象由更新的基线建成）。
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-2264, -955) THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE '
ALTER TABLE ai_call_logs ADD CONSTRAINT ck_ai_call_logs_attempt_no CHECK (attempt_no IS NULL OR attempt_no >= 1)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-2264, -955) THEN RAISE; END IF;
END;
/

-- ----------------------------------------------------------------------------
-- 索引：按 request_id 追一条链
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE 'CREATE INDEX ix_ai_call_logs_request_id ON ai_call_logs (request_id)';
EXCEPTION
  -- -955: 索引名被占用；-1408: 同列索引已存在；-942: 表不存在（同上）。
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-955, -1408, -942) THEN RAISE; END IF;
END;
/
