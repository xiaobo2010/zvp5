-- ============================================================================
-- V050: AI 模型目录（Agent Fuel 倍率 + 元数据）
--
--   在 V001~V049 已应用的实例上补齐：
--     AI_MODELS（新表）
--     ix_ai_models_provider（新索引）
--
-- Background:
--
--   * 模型名一直只活在 AI_CREDENTIALS.MODELS_JSON 里（两种形态：字符串或
--     {id, context}），那是「哪个渠道能调哪些模型」的事实，不是「这个模型多少钱」
--     的配置。倍率、展示名、显式上下文窗口与启停需要一个可写、可审计的落点。
--
--   * 这张表是稀疏覆盖层：没有行 = 按默认口径（RATE=1、ENABLED=1）。所以建表
--     本身不改变任何现有行为，也不需要给存量渠道回填——回填只会制造一堆「看起来
--     有人配过」的行。
--
--   * MODEL 直接做主键（不是 oid）：模型名就是业务标识，写入路径是按模型名
--     upsert，再造一层 oid 只会多一次查询。
--
--   * RATE 是 Agent Fuel 倍率，CHECK 要求 > 0：0 或负数会让计量变成免费/倒扣，
--     这不是配置精度问题而是数据损坏，必须在库上挡住而不是只靠路由校验。
--
--   * ENABLED 只表达启用状态，不删行：停用一个模型不该连同配好的倍率一起丢掉。
--
--   * 无外键：模型名来自 JSON 列，凭据删掉后这一行仍是审计与回滚材料。
--
-- Idempotency:
--   表/索引重名吞 ORA-00955；索引已存在吞 ORA-01408；其余错误继续抛。
--
-- This file is mirrored by db/models.py / V001 (fresh instances get the same
-- table + index); run `python -m db.ddl_check` to verify models/V001 stay in
-- sync (offline, no DB access).
-- ============================================================================

BEGIN
  EXECUTE IMMEDIATE '
CREATE TABLE ai_models (
  model VARCHAR2(80 CHAR) NOT NULL,
  display_name VARCHAR2(120 CHAR),
  provider VARCHAR2(40 CHAR),
  context_length NUMBER(10),
  rate NUMBER(10,4) DEFAULT 1 NOT NULL,
  enabled NUMBER(1) DEFAULT 1 NOT NULL,
  notes VARCHAR2(400 CHAR),
  updated_at TIMESTAMP NOT NULL,
  updated_by CHAR(24 CHAR),
  PRIMARY KEY (model),
  CONSTRAINT ck_ai_models_enabled CHECK (enabled IN (0,1)),
  CONSTRAINT ck_ai_models_rate CHECK (rate > 0)
)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -955 THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE '
CREATE INDEX ix_ai_models_provider ON ai_models (provider)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-955, -1408) THEN RAISE; END IF;
END;
/
