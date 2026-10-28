-- ============================================================================
-- V044: AI 配额重置申请（AI_QUOTA_REQUESTS）
--
--   在 V001~V043 已应用的实例上补齐一张「额度用尽 → 申请 → 审批」的账本：
--     * AI_QUOTA_REQUESTS
--     * IX_AI_QUOTA_REQUESTS_USER_PERIOD (user_oid, period, kind, status)
--       —— 服务层查重（同一人本月这个能力是否已有 pending）走这条。
--     * IX_AI_QUOTA_REQUESTS_STATUS (status, created_at)
--       —— 审批台按状态翻页、按创建时间倒序。
--
-- Background:
--
--   * 额度用尽后目前只有「管理员直接调接口改库」这一条路径：没有申请，就没有
--     「谁在什么时候因为什么要额度、谁批的」这条事实，事后对账只能翻日志。
--
--   * PERIOD 与 AI_QUOTA 同口径（YYYYMM 文本，本地时区）。申请只对**当月**有效：
--     下个月额度本来就重置，不需要「跨月继承」这种隐藏语义。
--
--   * REQUESTED_LIMIT 可空：NULL = 「只要求重置已用量」，非 NULL = 「顺便把上限
--     提到这个数」。0 是合法值（「本月到此为止」），所以不能用 0 顶替 NULL。
--
--   * DECIDED_BY / DECIDED_AT / DECISION_NOTE 可空：pending 行还没有审批人，
--     用 NULL 表达「还没决定」比编一个哨兵诚实。
--
--   * 不建外键：申请是审计材料，必须比账号/额度行活得久（同 AI_CALL_LOGS）。
--
--   * 「同一用户同一 period 同一 kind 只允许一条 pending」放在服务层（先查后写）：
--     写成函数唯一索引要把终态行排除在外（CASE 索引），收益不抵复杂度；并发双
--     提交的极小窗口由审批端的「非 pending 再批 → 409」兜住。
--
-- Idempotency:
--   Same EXECUTE IMMEDIATE pattern as V024-V043.  CREATE TABLE 吞 ORA-00955
--   （表已存在）；CREATE INDEX 额外吞 -1408（同列索引已存在）。
--
-- This file is mirrored by db/models.py / V001 (fresh instances get the same table
-- and indexes); run `python -m db.ddl_check` to verify models/V001 stay in sync
-- (offline, no DB access).
-- ============================================================================

-- ----------------------------------------------------------------------------
-- AI_QUOTA_REQUESTS: 一行一次申请（提交即 pending，审批后落终态）
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE '
CREATE TABLE ai_quota_requests (
  oid CHAR(24 CHAR) NOT NULL,
  user_oid CHAR(24 CHAR) NOT NULL,
  period CHAR(6 CHAR) NOT NULL,
  kind VARCHAR2(16 CHAR) NOT NULL,
  status VARCHAR2(16 CHAR) DEFAULT ''pending'' NOT NULL,
  reason VARCHAR2(500 CHAR),
  requested_limit NUMBER(12),
  decided_by CHAR(24 CHAR),
  decided_at TIMESTAMP,
  decision_note VARCHAR2(500 CHAR),
  created_at TIMESTAMP NOT NULL,
  updated_at TIMESTAMP NOT NULL,
  PRIMARY KEY (oid),
  CONSTRAINT ck_ai_quota_requests_oid CHECK (REGEXP_LIKE(oid,''^[0-9a-f]{24}$'')),
  CONSTRAINT ck_ai_quota_requests_period CHECK (REGEXP_LIKE(period,''^[0-9]{6}$'')),
  CONSTRAINT ck_ai_quota_requests_kind CHECK (kind IN (''chat'',''ocr'')),
  CONSTRAINT ck_ai_quota_requests_status CHECK (status IN (''pending'',''approved'',''rejected'',''cancelled'')),
  CONSTRAINT ck_ai_quota_requests_limit CHECK (requested_limit >= 0)
)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -955 THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE '
CREATE INDEX ix_ai_quota_requests_user_period ON ai_quota_requests (user_oid, period, kind, status)';
EXCEPTION
  -- -955: 表不存在（由更新的基线建成）；-1408: 同列索引已存在。
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-955, -1408) THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE '
CREATE INDEX ix_ai_quota_requests_status ON ai_quota_requests (status, created_at)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-955, -1408) THEN RAISE; END IF;
END;
/
