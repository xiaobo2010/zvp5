-- ============================================================================
-- V024__add_ai_gateway.sql
-- ----------------------------------------------------------------------------
-- AI 网关: adds AI_CALL_LOGS and AI_QUOTA on an instance where V001 was
-- already applied.
--
--   * AI_CALL_LOGS (每次网关调用的审计流水, append-only)
--   * AI_QUOTA (按 用户 + 月份 + 能力 计额度)
--   * ix_ai_call_logs_user_created    ON ai_call_logs (user_oid, created_at)
--   * ix_ai_call_logs_created         ON ai_call_logs (created_at)
--   * ix_ai_call_logs_endpoint_status ON ai_call_logs (endpoint, status, created_at)
--   * ux_ai_quota_user_period_kind    ON ai_quota (user_oid, period, kind) UNIQUE
--
-- Background:
--   * 两张表都**不建外键**。AI_CALL_LOGS 是审计材料：账号被删除之后仍要能
--     回答「谁在什么时候用了多少额度、失败在哪个环节」，外键会把审计的寿命
--     绑在账号的寿命上（同 BAN_VIOLATIONS 的理由）。
--   * AI_QUOTA 的额度是「用户 + YYYYMM + 能力」三元组的事实。PERIOD 用
--     CHAR(6) 而不是 DATE：月份是计费口径而不是时间点，CHAR(6) 让相等比较和
--     唯一索引都直接可用，也避免了「月初该按哪个时区截断」的隐式规则。
--   * USED 只由一条 `UPDATE ... WHERE used + :amount <= limit_count` 扣减，
--     唯一索引 ux_ai_quota_user_period_kind 保证三元组只有一行，所以并发扣减
--     由数据库串行化，不会超卖（实现里没有读后写）。
--   * AI_CALL_LOGS.UNITS 是本次调用消耗的额度单位（OCR 页数 / chat 次数），
--     与 AI_QUOTA.USED 的增量同源；只有流水与额度都留下才能对账。
--   * STATUS 记录的是**网关视角**的结果：rejected（额度/鉴权拒绝）与 busy
--     （上游限流）必须能和 error（上游真失败）区分开，否则排查时会把「我们
--     自己挡掉的」当成「上游挂了」。
--
-- Idempotency:
--   Same EXECUTE IMMEDIATE pattern as V001-V021.  CREATE TABLE 吞 ORA-00955;
--   CREATE INDEX 另吞 ORA-01408。
--
-- This file is mirrored by db/models.py / V001 (fresh instances get the same
-- objects from V001); run `python -m db.ddl_check` to verify they stay
-- in sync (offline, no DB access).
-- ============================================================================

-- ----------------------------------------------------------------------------
-- AI_CALL_LOGS: AI 网关调用流水（append-only）
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE '
CREATE TABLE ai_call_logs (
  oid VARCHAR2(24 CHAR) NOT NULL,
  user_oid VARCHAR2(24 CHAR) NOT NULL,
  capability VARCHAR2(16 CHAR) NOT NULL,
  endpoint VARCHAR2(40 CHAR) NOT NULL,
  provider VARCHAR2(40 CHAR),
  model VARCHAR2(80 CHAR),
  status VARCHAR2(16 CHAR) NOT NULL,
  http_status NUMBER(3),
  error_code VARCHAR2(40 CHAR),
  latency_ms NUMBER(10),
  units NUMBER(10) DEFAULT 0 NOT NULL,
  input_bytes NUMBER(10),
  request_id VARCHAR2(64 CHAR),
  created_at TIMESTAMP NOT NULL,
  PRIMARY KEY (oid),
  CONSTRAINT ck_ai_call_logs_oid CHECK (REGEXP_LIKE(oid,''^[0-9a-f]{24}$'')),
  CONSTRAINT ck_ai_call_logs_capability CHECK (capability IN (''ocr'',''chat'')),
  CONSTRAINT ck_ai_call_logs_status CHECK (status IN (''success'',''error'',''timeout'',''rejected'',''busy'')),
  CONSTRAINT ck_ai_call_logs_units CHECK (units >= 0)
)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -955 THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'CREATE INDEX ix_ai_call_logs_user_created ON ai_call_logs (user_oid, created_at)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-955, -1408) THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'CREATE INDEX ix_ai_call_logs_created ON ai_call_logs (created_at)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-955, -1408) THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'CREATE INDEX ix_ai_call_logs_endpoint_status ON ai_call_logs (endpoint, status, created_at)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-955, -1408) THEN RAISE; END IF;
END;
/

-- ----------------------------------------------------------------------------
-- AI_QUOTA: 按 用户 + 月份 + 能力 计的额度行
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE '
CREATE TABLE ai_quota (
  oid VARCHAR2(24 CHAR) NOT NULL,
  user_oid VARCHAR2(24 CHAR) NOT NULL,
  period CHAR(6) NOT NULL,
  kind VARCHAR2(16 CHAR) NOT NULL,
  used NUMBER(10) DEFAULT 0 NOT NULL,
  limit_count NUMBER(10) NOT NULL,
  updated_at TIMESTAMP NOT NULL,
  PRIMARY KEY (oid),
  CONSTRAINT ck_ai_quota_oid CHECK (REGEXP_LIKE(oid,''^[0-9a-f]{24}$'')),
  CONSTRAINT ck_ai_quota_period CHECK (REGEXP_LIKE(period,''^[0-9]{6}$'')),
  CONSTRAINT ck_ai_quota_kind CHECK (kind IN (''ocr'',''chat'')),
  CONSTRAINT ck_ai_quota_used CHECK (used >= 0),
  CONSTRAINT ck_ai_quota_limit CHECK (limit_count >= 0)
)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -955 THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'CREATE UNIQUE INDEX ux_ai_quota_user_period_kind ON ai_quota (user_oid, period, kind)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-955, -1408) THEN RAISE; END IF;
END;
/
