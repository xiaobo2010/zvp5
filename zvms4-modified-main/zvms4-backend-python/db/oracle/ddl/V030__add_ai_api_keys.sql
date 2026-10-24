-- ============================================================================
-- V030__add_ai_api_keys.sql
-- ----------------------------------------------------------------------------
-- 给 OpenAI 兼容面加**独立 API key**：AI_API_KEYS。
--
--   列               类型              约束
--   ----------------------------------------------------------------------
--   OID              VARCHAR2(24)      PK / REGEXP_LIKE('^[0-9a-f]{24}$')
--   USER_OID         VARCHAR2(24)      NOT NULL
--   NAME             VARCHAR2(64)      NOT NULL（同一 USER_OID 内唯一）
--   TOKEN_HASH       VARCHAR2(64)      NOT NULL / 全局唯一
--   KEY_HINT         VARCHAR2(16)      NOT NULL
--   ENABLED          NUMBER(1)         DEFAULT 1 NOT NULL / IN (0,1)
--   EXPIRES_AT       TIMESTAMP         NULL = 永不过期
--   LAST_USED_AT     TIMESTAMP         NULL = 从未用过（采样写，非精确审计）
--   CREATED_AT       TIMESTAMP         DEFAULT SYSTIMESTAMP NOT NULL
--
-- Background:
--   * 此前 `/v1/*` 只能拿用户的 Bearer JWT：第三方接入要么存一个人的登录态，
--     要么没法接入；也没有「按 key 吊销/限额」的抓手。API key 把「一个人的
--     平台登录态」和「一个自动化客户端的凭据」分开。
--   * TOKEN_HASH 存 sha256(token) 的 hex，**明文只在创建响应里出现一次**。与
--     AI_CREDENTIALS 的 api_key_cipher 不同，这里不需要解密回明文（上游鉴权
--     用的仍是平台自己的 key），所以单向哈希就够——表被读走也拿不到可用 key。
--   * TOKEN_HASH 全局唯一而不是 (USER_OID, TOKEN_HASH)：所有 key 走同一个
--     `WHERE token_hash = ?` 校验入口，撞哈希会让两条记录互相遮蔽。
--   * NAME 在 owner 内唯一（ux_ai_api_keys_owner_name）：名字是管理员认 key 的
--     标签，不同用户各叫「ci」互不影响。
--   * 不建外键：与其它 AI 表同一口径（行要活得比账号久，审计/对账依赖它）。
--     用户被删后 key 自然失效在鉴权层（查不到用户 → 401）。
--   * EXPIRES_AT / LAST_USED_AT 不加 CHECK：「未来时间」是写入时刻的性质，不是
--     行的不变量——今天合法的到期时间，明天自然就过期了。
--
-- Idempotency:
--   Same EXECUTE IMMEDIATE pattern as V024-V029：CREATE TABLE 吞 ORA-00955
--   （表已存在）；CREATE INDEX 另吞 ORA-01408（索引已存在）。因此本文件在
--   「V001 已带该表」的全新建库与「V029 之后」的存量库上都能重复执行，且重跑
--   不改变任何已有对象。
--
-- This file is mirrored by db/models.py / V001 (fresh instances get the same
-- objects from V001); run `python -m db.ddl_check` to verify they stay in
-- sync (offline, no DB access).
-- ============================================================================

BEGIN
  EXECUTE IMMEDIATE '
CREATE TABLE ai_api_keys (
  oid VARCHAR2(24 CHAR) NOT NULL,
  user_oid VARCHAR2(24 CHAR) NOT NULL,
  name VARCHAR2(64 CHAR) NOT NULL,
  token_hash VARCHAR2(64 CHAR) NOT NULL,
  key_hint VARCHAR2(16 CHAR) NOT NULL,
  enabled NUMBER(1) DEFAULT 1 NOT NULL,
  expires_at TIMESTAMP,
  last_used_at TIMESTAMP,
  created_at TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL,
  CONSTRAINT pk_ai_api_keys PRIMARY KEY (oid),
  CONSTRAINT ck_ai_api_keys_oid CHECK (REGEXP_LIKE(oid,''^[0-9a-f]{24}$'')),
  CONSTRAINT ck_ai_api_keys_enabled CHECK (enabled IN (0,1))
)';
EXCEPTION
  -- -955: 表已存在（重复执行 / 全新库由 V001 建好）。
  WHEN OTHERS THEN
    IF SQLCODE != -955 THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'CREATE UNIQUE INDEX ux_ai_api_keys_token_hash ON ai_api_keys (token_hash)';
EXCEPTION
  -- -955: 索引名被占用；-1408: 同列索引已存在。
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-955, -1408) THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'CREATE UNIQUE INDEX ux_ai_api_keys_owner_name ON ai_api_keys (user_oid, name)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-955, -1408) THEN RAISE; END IF;
END;
/
