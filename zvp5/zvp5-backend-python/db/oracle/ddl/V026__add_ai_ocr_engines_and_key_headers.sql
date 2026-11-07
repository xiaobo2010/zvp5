-- ============================================================================
-- V026__add_ai_ocr_engines_and_key_headers.sql
-- ----------------------------------------------------------------------------
-- AI 引擎扩展地基: adds AI_OCR_ENGINES and the AI_CREDENTIALS request-shape
-- columns on an instance where V001 / V024 / V025 were already applied.
--
--   * AI_CREDENTIALS.HEADERS_JSON  (额外请求头的 JSON 对象；NULL = 只用默认)
--   * AI_CREDENTIALS.API_PATH      (provider 的路径；NULL = 用默认路径)
--   * ck_ai_credentials_headers_json ON ai_credentials (headers_json)
--   * AI_OCR_ENGINES               (本地 / 云端 OCR 引擎注册表)
--   * ux_ai_ocr_engines_name       ON ai_ocr_engines (name) UNIQUE
--   * ix_ai_ocr_engines_enabled_priority ON ai_ocr_engines (enabled, priority)
--   * ck_ai_quota_kind 重建为前缀式（'ocr' / 'chat' / 'ocr_*'）
--
-- 编号说明:
--   V024（add_ai_gateway）与 V025（add_ai_credentials）已占用，本文件顺延为
--   V026；任务书里的 "V024" 是重编号前的旧号。
--
-- Background:
--   * 凭据表原来只能描述「OpenAI 兼容 + 固定路径 + Authorization 头」这一类
--     provider。真实引擎不止这一类：有的把 key 放在自定义 header（X-Api-Key），
--     有的把能力挂在非 /v1 路径上。所以把「请求形状」显式化：HEADERS_JSON 是
--     头名 → 值的 JSON 对象（值是模板，含 $KEY 之类占位由服务层展开），
--     API_PATH 是相对 base_url 的路径。两者都可空 = 沿用现有默认行为，存量行
--     因此不需要回填。
--   * AI_OCR_ENGINES 是**引擎注册表**而不是凭据表：一行描述「用哪个厂商的
--     OCR、怎么连、优先级多少」。KIND 是引擎类型（tencent / aliyun /
--     rapidocr），不是 provider 名——同一类型可以有多行（不同 region、
--     不同配额来源）。
--   * SECRET_CIPHER 可空：本地引擎（rapidocr）没有凭证，云端引擎才有。
--     密文与 AI_CREDENTIALS.API_KEY_CIPHER 同口径：只存 AES-GCM 密文的
--     base64，明文永不落库；本地引擎的 NULL 不是「配置缺失」而是「不需要」。
--   * PRIORITY 数字越小越优先（100 是默认档）。路由按 (enabled, priority)
--     取第一行可用引擎，所以索引 ix_ai_ocr_engines_enabled_priority 就是
--     路由查询的形状；NAME 唯一是因为运维靠名字指定/禁用某个引擎。
--   * CK_AI_QUOTA_KIND 改成前缀式（'ocr' / 'chat' / 'ocr_' 开头的子类）。
--     OCR 引擎类型会持续增加（'ocr_tencent'、'ocr_rapidocr' …），如果每加
--     一个引擎都要 DDL 改 CHECK，额度规则就和发布节奏绑死了。前缀式让
--     「某个引擎的月度额度」成为合法的 kind，不再改约束。
--     ESCAPE '\' 是必须的：LIKE 里的 '_' 是单字符通配符，不转义就会把
--     'ocrX' 也放进来。
--   * AI_QUOTA.KIND 已是 VARCHAR2(16 CHAR)，够放 'ocr_aliyun' 这类值，
--     本文件不动列宽（< 16 才需要放宽）。
--   * 不建外键、不动 AI_CREDENTIALS.KIND 的 CHECK（仍只有 openai_compat）：
--     OCR 引擎是独立注册表，不与 provider 凭据混用。
--
-- Idempotency:
--   Same EXECUTE IMMEDIATE pattern as V001-V025.  ALTER TABLE ADD 列吞
--   ORA-01430（列已存在）/ ORA-00955；ADD CONSTRAINT 吞 ORA-02264（约束名已
--   被占用）；DROP CONSTRAINT 吞 ORA-02443（约束不存在）；CREATE TABLE 吞
--   ORA-00955；CREATE INDEX 另吞 ORA-01408。因此本文件在「V001 + V024 + V025 已
--   应用」的存量库和「全新建库」上都能重复执行，且重跑不改变任何已有对象。
--
-- This file is mirrored by db/models.py / V001 (fresh instances get the same
-- objects from V001); run `python -m db.ddl_check` to verify they stay
-- in sync (offline, no DB access).
-- ============================================================================

-- ----------------------------------------------------------------------------
-- AI_CREDENTIALS.HEADERS_JSON / API_PATH: provider 的请求形状
--   V025 已经建过 AI_CREDENTIALS 的库走这两条幂等 ALTER；全新库由 V001 的
--   CREATE TABLE 直接带上同样的列与约束。两条分开写：多列 ADD 是原子的，
--   合成一条会让「只加了一列」的中间态无法重跑收敛。
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE 'ALTER TABLE ai_credentials ADD (headers_json CLOB)';
EXCEPTION
  -- -1430: 列已存在（重复执行）；-955: 兜底，与 V025 同口径（对象由更新的
  -- 基线建成时也能收敛，不让迁移顺序问题变成重启失败）。
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-1430, -955) THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'ALTER TABLE ai_credentials ADD CONSTRAINT ck_ai_credentials_headers_json CHECK (headers_json IS JSON)';
EXCEPTION
  -- -2264: 约束名已被占用（重复执行）；-1430: 列尚未加上（理论上不会发生）；
  -- -955: 兜底，与 V025 同口径。
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-2264, -1430, -955) THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'ALTER TABLE ai_credentials ADD (api_path VARCHAR2(200 CHAR))';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-1430, -955) THEN RAISE; END IF;
END;
/

-- ----------------------------------------------------------------------------
-- AI_OCR_ENGINES: OCR 引擎注册表（一行 = 一个可路由的引擎配置）
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE '
CREATE TABLE ai_ocr_engines (
  oid VARCHAR2(24 CHAR) NOT NULL,
  name VARCHAR2(64 CHAR) NOT NULL,
  kind VARCHAR2(20 CHAR) NOT NULL,
  config_json CLOB,
  secret_cipher VARCHAR2(4000 CHAR),
  key_hint VARCHAR2(16 CHAR),
  enabled NUMBER(1) DEFAULT 1 NOT NULL,
  priority NUMBER(3) DEFAULT 100 NOT NULL,
  created_at TIMESTAMP NOT NULL,
  updated_at TIMESTAMP NOT NULL,
  CONSTRAINT pk_ai_ocr_engines PRIMARY KEY (oid),
  CONSTRAINT ck_ai_ocr_engines_oid CHECK (REGEXP_LIKE(oid,''^[0-9a-f]{24}$'')),
  CONSTRAINT ck_ai_ocr_engines_kind CHECK (kind IN (''tencent'',''aliyun'',''rapidocr'')),
  CONSTRAINT ck_ai_ocr_engines_enabled CHECK (enabled IN (0,1)),
  CONSTRAINT ck_ai_ocr_engines_config_json CHECK (config_json IS JSON)
)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -955 THEN RAISE; END IF;
END;
/

-- 唯一索引而不是 UNIQUE 约束：沿用 AI_CREDENTIALS 的写法，索引名即口径
-- （同一张注册表里不能有两个同名引擎，运维才能按名字禁用）。
BEGIN
  EXECUTE IMMEDIATE 'CREATE UNIQUE INDEX ux_ai_ocr_engines_name ON ai_ocr_engines (name)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-955, -1408) THEN RAISE; END IF;
END;
/

-- 路由查询的形状：WHERE enabled = 1 ORDER BY priority。
BEGIN
  EXECUTE IMMEDIATE 'CREATE INDEX ix_ai_ocr_engines_enabled_priority ON ai_ocr_engines (enabled, priority)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-955, -1408) THEN RAISE; END IF;
END;
/

-- ----------------------------------------------------------------------------
-- AI_QUOTA.KIND: 由枚举改为前缀式（'ocr' / 'chat' / 'ocr_*'）
--   DROP 与 ADD 必须分两个块：同一块里 DROP 之后 ADD 失败会连同 DROP 一起
--   回滚，重跑时旧约束仍在——分块后每一步都能独立收敛。
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE 'ALTER TABLE ai_quota DROP CONSTRAINT ck_ai_quota_kind';
EXCEPTION
  -- -2443: 约束不存在（已是最新基线，或上一次执行已删）；-942: 表尚未创建。
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-2443, -955, -942) THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'ALTER TABLE ai_quota ADD CONSTRAINT ck_ai_quota_kind CHECK (kind IN (''ocr'',''chat'') OR kind LIKE ''ocr\_%'' ESCAPE ''\'')';
EXCEPTION
  -- -2264: 约束名已被占用（重复执行）；-942: 表尚未创建。
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-2264, -955, -942) THEN RAISE; END IF;
END;
/
