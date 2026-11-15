-- ============================================================================
-- V013__add_ban_rules.sql
-- ----------------------------------------------------------------------------
-- 规则封禁（同学号多设备登录等）: adds the ban metadata columns, BAN_RULES /
-- BAN_RECORDS, the built-in rule seed and one APP_LOGS index on an instance
-- where V001 was already applied.
--
--   * USERS += BAN_REASON / BAN_UNTIL / BAN_SOURCE / BAN_RULE
--   * BAN_RULES   (规则配置: code/name/enabled/config/penalties/builtin)
--   * BAN_RECORDS (封禁流水: source/rule_code/reason/permanent/started_at/...)
--   * ix_ban_records_user, ix_ban_records_started
--   * ix_app_logs_user_ts  ON app_logs (app_user, ts_raw)
--
-- Background: `USERS.BANNED` 继续是**唯一**的登录闸门（V002），新增四列只回答
-- 「为什么封、到什么时候、谁或哪条规则判的」：
--
--   * BAN_UNTIL 为 NULL 且 BANNED = 1 表示**永久**封禁；规则封禁写入到期时间，
--     由任务侧到期解封（数据层只存事实，不跑定时器）。
--   * BAN_SOURCE 只有 admin / rule 两种来源；BAN_RULE 存规则 CODE（admin 封禁
--     留空），与 BAN_RULES.CODE 逻辑关联，故意不加外键：规则被删除后，历史
--     封禁记录仍要能读出当初是哪条规则判的。
--   * BAN_RECORDS 是「累犯升级处罚」的唯一依据：同一 (user_oid, rule_code) 的
--     历史行数决定下一次用 PENALTIES 里的第几档。USER_OID 带真外键（表从空
--     开始，不会有历史脏 id），其余列无约束，保留判定当时的原貌。
--   * BAN_RULES.CONFIG / PENALTIES 是有界 JSON，用 VARCHAR2(2000 CHAR) +
--     IS JSON 而不是 CLOB：体量固定且总整体读取，LOB locator 只会多一次往返。
--   * ix_app_logs_user_ts 服务「按用户在时间窗内数不同学海设备」的查询：
--     COUNT(DISTINCT xuehai_id) WHERE app_user = ? AND ts_raw >= ?。单列的
--     ix_app_logs_xuehai_id 只能覆盖一半谓词，复合索引把时间窗留在索引里。
--
-- Idempotency:
--   Same EXECUTE IMMEDIATE pattern as V001-V012.  ALTER TABLE ADD 吞
--   ORA-01430 (column already exists); CREATE TABLE 吞 ORA-00955; CREATE INDEX
--   另吞 ORA-01408 (index already exists).  内置规则用 MERGE ... WHEN NOT
--   MATCHED THEN INSERT：已存在则**什么都不做**，绝不覆盖管理员调过的
--   threshold / penalties，因此可重复执行。
--
-- This file is mirrored by db/models.py / V001 (fresh instances get the same
-- objects, and the same seed, from V001); run `python -m db.ddl_check` to
-- verify they stay in sync (offline, no DB access).
-- ============================================================================

-- ----------------------------------------------------------------------------
-- USERS: 封禁元数据四列
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE 'ALTER TABLE users ADD ban_reason VARCHAR2(512 CHAR)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -1430 THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'ALTER TABLE users ADD ban_until TIMESTAMP';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -1430 THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'ALTER TABLE users ADD ban_source VARCHAR2(16 CHAR)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -1430 THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'ALTER TABLE users ADD ban_rule VARCHAR2(64 CHAR)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -1430 THEN RAISE; END IF;
END;
/

-- ----------------------------------------------------------------------------
-- BAN_RULES: 规则配置
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE '
CREATE TABLE ban_rules (
  oid VARCHAR2(24 CHAR) NOT NULL,
  code VARCHAR2(64 CHAR) NOT NULL,
  name VARCHAR2(128 CHAR) NOT NULL,
  enabled NUMBER(1) DEFAULT 1 NOT NULL,
  config VARCHAR2(2000 CHAR) NOT NULL,
  penalties VARCHAR2(2000 CHAR) NOT NULL,
  builtin NUMBER(1) DEFAULT 0 NOT NULL,
  created_at TIMESTAMP NOT NULL,
  updated_at TIMESTAMP NOT NULL,
  PRIMARY KEY (oid),
  CONSTRAINT ck_ban_rules_oid CHECK (REGEXP_LIKE(oid,''^[0-9a-f]{24}$'')),
  CONSTRAINT ck_ban_rules_enabled CHECK (enabled IN (0,1)),
  CONSTRAINT ck_ban_rules_builtin CHECK (builtin IN (0,1)),
  CONSTRAINT ck_ban_rules_config_json CHECK (config IS JSON),
  CONSTRAINT ck_ban_rules_penalties_json CHECK (penalties IS JSON),
  CONSTRAINT uq_ban_rules_code UNIQUE (code)
)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -955 THEN RAISE; END IF;
END;
/

-- ----------------------------------------------------------------------------
-- BAN_RECORDS: 封禁流水
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE '
CREATE TABLE ban_records (
  oid VARCHAR2(24 CHAR) NOT NULL,
  user_oid VARCHAR2(24 CHAR) NOT NULL,
  source VARCHAR2(16 CHAR) NOT NULL,
  rule_code VARCHAR2(64 CHAR),
  reason VARCHAR2(512 CHAR),
  permanent NUMBER(1) DEFAULT 0 NOT NULL,
  started_at TIMESTAMP NOT NULL,
  expires_at TIMESTAMP,
  operator_oid VARCHAR2(24 CHAR),
  created_at TIMESTAMP NOT NULL,
  PRIMARY KEY (oid),
  CONSTRAINT ck_ban_records_oid CHECK (REGEXP_LIKE(oid,''^[0-9a-f]{24}$'')),
  CONSTRAINT ck_ban_records_source CHECK (source IN (''admin'',''rule'')),
  CONSTRAINT ck_ban_records_permanent CHECK (permanent IN (0,1)),
  CONSTRAINT fk_ban_records_user FOREIGN KEY (user_oid) REFERENCES users (oid)
)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -955 THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'CREATE INDEX ix_ban_records_user ON ban_records (user_oid)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-955, -1408) THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'CREATE INDEX ix_ban_records_started ON ban_records (started_at)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-955, -1408) THEN RAISE; END IF;
END;
/

-- ----------------------------------------------------------------------------
-- APP_LOGS: 按用户 + 时间窗统计不同学海设备
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE 'CREATE INDEX ix_app_logs_user_ts ON app_logs (app_user, ts_raw)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-955, -1408) THEN RAISE; END IF;
END;
/

-- ----------------------------------------------------------------------------
-- 内置规则: 同学号多设备登录
--   oid 固定为 '0b0000000000000000000001'，与 V001 的 seed 完全一致；MERGE 的
--   WHEN NOT MATCHED 分支保证重复执行时既不重复插入，也不覆盖已有配置。
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE '
MERGE INTO ban_rules t
USING (SELECT ''0b0000000000000000000001'' oid FROM dual) s
ON (t.code = ''multi_device_login'')
WHEN NOT MATCHED THEN INSERT
  (oid, code, name, enabled, config, penalties, builtin, created_at, updated_at)
  VALUES (''0b0000000000000000000001'', ''multi_device_login'', ''同学号多设备登录'',
          1, ''{"threshold":3,"window_hours":24}'', ''[1,2,7]'', 1,
          SYSTIMESTAMP, SYSTIMESTAMP)';
EXCEPTION
  -- -1: 并发执行时另一会话已插入（uq_ban_rules_code），结果一致，忽略。
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-1, -955) THEN RAISE; END IF;
END;
/
