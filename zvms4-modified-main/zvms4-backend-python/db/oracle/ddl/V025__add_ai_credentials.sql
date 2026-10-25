-- ============================================================================
-- V025__add_ai_credentials.sql
-- ----------------------------------------------------------------------------
-- AI 凭据: adds AI_CREDENTIALS and the AI_CALL_LOGS.KEY_SCOPE audit column on
-- an instance where V001 / V024 were already applied.
--
--   * AI_CREDENTIALS (管理员个人 key + 通用共享 key，密文存储)
--   * ux_ai_credentials_scope_owner_name ON ai_credentials (scope, owner_oid, name) UNIQUE
--   * ix_ai_credentials_owner            ON ai_credentials (owner_oid, enabled)
--   * ix_ai_credentials_scope            ON ai_credentials (scope, enabled)
--   * ai_call_logs.key_scope             (personal / shared / server；NULL = 存量行)
--   * ck_ai_call_logs_key_scope          ON ai_call_logs (key_scope)
--
-- Background:
--   * 一张表放两类凭据，用 SCOPE 区分：personal（管理员自己的 key）与 shared
--     （所有人可用的通用 key）。两类凭据的字段、生命周期、路由方式完全相同，
--     拆成两张表只会让「按名字找 provider」的读取路径变成两次查询加一次合并。
--   * OWNER_OID 对 shared 行固定写 24 个 0 的哨兵，**不用 NULL**：Oracle 的
--     唯一索引里 NULL 互不相等，owner 可空就等于 shared 行的
--     (scope, owner_oid, name) 唯一性直接失效，同名的共享 provider 能插进任意
--     多行。哨兵是个合法 hex oid、不指向任何用户，但让唯一索引对两类行同样生效。
--   * API_KEY_CIPHER 只存 AES-GCM 密文的 base64：明文永不落库、也不进日志。
--     KEY_HINT 只留末四位用于展示，它是**展示用冗余**，不是解密依据。
--   * MODELS_JSON 是 provider 的模型 id 字符串数组：路由与 /v1/models 的目录都
--     由它驱动，所以它必须是可查询的 JSON（IS JSON 约束挡住手写的坏串）。
--   * 不建外键：owner 账号被删除后，其 personal 凭据仍要能被管理员看到并清理
--     （同 AI_CALL_LOGS 的审计寿命理由）。
--   * AI_CALL_LOGS.KEY_SCOPE 记录这次调用**用的是哪一类 key**。排查「这次调用
--     为什么没扣额度 / 为什么走的是别人配的 provider」时，光有 provider 名不够，
--     还要知道它来自 personal、shared 还是服务端默认配置。存量行保持 NULL。
--
-- Idempotency:
--   Same EXECUTE IMMEDIATE pattern as V001-V024.  CREATE TABLE 吞 ORA-00955；
--   CREATE INDEX 另吞 ORA-01408。ALTER TABLE ADD 列吞 ORA-01430（列已存在）与
--   ORA-00955；ADD CONSTRAINT 吞 ORA-02264（约束名已被占用）/ ORA-01430 /
--   ORA-00955。因此本文件在「已应用过 V024 的存量库」和「全新库」上都能重复
--   执行，且重复执行不改变任何已有对象。
--
-- This file is mirrored by db/models.py / V001 (fresh instances get the same
-- objects from V001); run `python -m db.ddl_check` to verify they stay
-- in sync (offline, no DB access).
-- ============================================================================

-- ----------------------------------------------------------------------------
-- AI_CREDENTIALS: AI provider 凭据（personal = 管理员个人，shared = 通用共享）
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE '
CREATE TABLE ai_credentials (
  oid VARCHAR2(24 CHAR) NOT NULL,
  scope VARCHAR2(8 CHAR) NOT NULL,
  owner_oid VARCHAR2(24 CHAR) NOT NULL,
  name VARCHAR2(64 CHAR) NOT NULL,
  kind VARCHAR2(24 CHAR) NOT NULL,
  base_url VARCHAR2(400 CHAR) NOT NULL,
  models_json CLOB NOT NULL,
  api_key_cipher VARCHAR2(2000 CHAR) NOT NULL,
  key_hint VARCHAR2(16 CHAR),
  enabled NUMBER(1) DEFAULT 1 NOT NULL,
  created_at TIMESTAMP NOT NULL,
  updated_at TIMESTAMP NOT NULL,
  PRIMARY KEY (oid),
  CONSTRAINT ck_ai_credentials_oid CHECK (REGEXP_LIKE(oid,''^[0-9a-f]{24}$'')),
  CONSTRAINT ck_ai_credentials_scope CHECK (scope IN (''personal'',''shared'')),
  CONSTRAINT ck_ai_credentials_kind CHECK (kind IN (''openai_compat'')),
  CONSTRAINT ck_ai_credentials_enabled CHECK (enabled IN (0,1)),
  CONSTRAINT ck_ai_credentials_models_json CHECK (models_json IS JSON)
)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -955 THEN RAISE; END IF;
END;
/

-- 唯一索引而不是 UNIQUE 约束：shared 行的 owner 哨兵让索引对两类行同样生效，
-- 于是「同一 scope 下同一 owner 不能有两个同名 provider」是数据库级事实。
BEGIN
  EXECUTE IMMEDIATE 'CREATE UNIQUE INDEX ux_ai_credentials_scope_owner_name ON ai_credentials (scope, owner_oid, name)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-955, -1408) THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'CREATE INDEX ix_ai_credentials_owner ON ai_credentials (owner_oid, enabled)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-955, -1408) THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'CREATE INDEX ix_ai_credentials_scope ON ai_credentials (scope, enabled)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-955, -1408) THEN RAISE; END IF;
END;
/

-- ----------------------------------------------------------------------------
-- AI_CALL_LOGS.KEY_SCOPE: 本次调用用的是哪一类 key
--   V024 已经建过 AI_CALL_LOGS 的库走这条幂等 ALTER；全新库由 V001 的
--   CREATE TABLE 直接带上同一列与同一约束。
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE 'ALTER TABLE ai_call_logs ADD (key_scope VARCHAR2(16))';
EXCEPTION
  -- -1430: 列已存在（重复执行）；-955: 表已由更新的基线带上了这一列。
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-1430, -955) THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'ALTER TABLE ai_call_logs ADD CONSTRAINT ck_ai_call_logs_key_scope CHECK (key_scope IS NULL OR key_scope IN (''personal'',''shared'',''server''))';
EXCEPTION
  -- -2264: 约束名已被占用（重复执行）；-1430 / -955: 列或表尚不存在（本文件
  -- 的 ALTER 已先执行，理论上不会发生，保留以免并发迁移中断在这里）。
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-2264, -1430, -955) THEN RAISE; END IF;
END;
/
