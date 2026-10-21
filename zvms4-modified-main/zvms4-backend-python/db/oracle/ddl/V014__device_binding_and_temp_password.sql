-- ============================================================================
-- V014__device_binding_and_temp_password.sql
-- ----------------------------------------------------------------------------
-- 主学海设备绑定 + 一次性临时密码 + 设备级封禁，在 V001~V013 已应用的实例上
-- 补齐以下对象：
--
--   * USERS += PRIMARY_XUEHAI_ID (NULL = 未绑定) + ix_users_primary_xuehai
--   * TEMP_LOGIN_PASSWORDS (一次性临时密码, 只存 bcrypt 哈希)
--   * DEVICE_CHALLENGES   (异设备登录的一次性挑战 + 失败计数)
--   * BANNED_DEVICES      (设备级封禁, 主键 = 学海设备号)
--   * BAN_RULES seed: foreign_device_login
--
-- Background:
--
--   * PRIMARY_XUEHAI_ID 是「这台账号的主设备」，与 USER_DEVICE_BINDINGS 的
--     observe-only 记录**无关**：后者是登录时被动留痕（多行、历史），前者是
--     管理员/用户显式选定的唯一值（单列）。存量账号保持 NULL —— 未绑定不做
--     设备校验，因此本迁移不需要任何数据回填，也就没有「回填错了要回滚」的
--     风险。索引服务登录路径的等值查询（每次登录都要读它）。
--
--   * TEMP_LOGIN_PASSWORDS 只存 bcrypt 哈希：明文只在创建响应里返回一次。
--     单次使用（USED_AT）/ 30 分钟过期（EXPIRES_AT）/ 可撤销（REVOKED_AT）
--     各用一个时间戳而不是一个状态列：三者是不同事实，合并成枚举会丢时间点，
--     而排查「为什么这个码用不了」正需要时间点。无外键：签发人与目标用户都
--     可能被硬删除（回收站清理），而密码行是审计材料，必须比账号活得久。
--
--   * DEVICE_CHALLENGES.TOKEN_HASH 存 SHA-256 hex（VARCHAR2(64) 恰好容纳）：
--     挑战值等价于一次性登录许可，库里的明文等于把许可发给任何能读表的人。
--     唯一索引既是查找路径也是防重放裁决者。ATTEMPTS 必须落在库里（不能放
--     进程内存）：多 worker 下内存计数会让「第 3 次封禁」永远数不满。
--
--   * BANNED_DEVICES 主键就是设备号：一台设备只有一条当前封禁状态，重复封禁
--     覆盖而不是追加。UNTIL NULL = 永久；SOURCE 区分 rule（连错临时密码 3 次）
--     与 admin（人工封禁）。故意没有 OID 列与外键 —— 设备号是外部标识，删除
--     用户不应连带删掉设备封禁。账号封禁**不**镜像到这里：服务层按
--     USERS.PRIMARY_XUEHAI_ID 实时推导，账号解封后设备自动恢复。
--
--   * BAN_RULES seed 的 penalties 末位是 JSON null = 永久封禁（第 6 次）。
--     与 V013 的 multi_device_login 同样用 MERGE + 固定 oid，重复执行既不重复
--     插入，也不覆盖管理员调过的梯度。
--
-- Idempotency:
--   Same EXECUTE IMMEDIATE pattern as V001-V013.  ALTER TABLE ADD 吞
--   ORA-01430 (column already exists); CREATE TABLE 吞 ORA-00955; CREATE INDEX
--   另吞 ORA-01408 (index already exists).  内置规则 MERGE 的 WHEN NOT MATCHED
--   分支让它可重复执行；并发插入撞 uq_ban_rules_code 时吞 ORA-00001。
--
-- This file is mirrored by db/models.py / V001 (fresh instances get the same
-- objects, and the same seed, from V001); run `python -m db.ddl_check` to
-- verify models/V001 stay in sync (offline, no DB access).
-- ============================================================================

-- ----------------------------------------------------------------------------
-- USERS: 主学海设备
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE 'ALTER TABLE users ADD primary_xuehai_id VARCHAR2(32 CHAR)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -1430 THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'CREATE INDEX ix_users_primary_xuehai ON users (primary_xuehai_id)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-955, -1408) THEN RAISE; END IF;
END;
/

-- ----------------------------------------------------------------------------
-- TEMP_LOGIN_PASSWORDS: 一次性临时密码
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE '
CREATE TABLE temp_login_passwords (
  oid VARCHAR2(24 CHAR) NOT NULL,
  target_user_oid VARCHAR2(24 CHAR) NOT NULL,
  created_by_oid VARCHAR2(24 CHAR) NOT NULL,
  code_hash VARCHAR2(100 CHAR) NOT NULL,
  creator_xuehai_id VARCHAR2(32 CHAR),
  expires_at TIMESTAMP NOT NULL,
  used_at TIMESTAMP,
  revoked_at TIMESTAMP,
  created_at TIMESTAMP NOT NULL,
  PRIMARY KEY (oid),
  CONSTRAINT ck_temp_login_passwords_oid CHECK (REGEXP_LIKE(oid,''^[0-9a-f]{24}$''))
)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -955 THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'CREATE INDEX ix_tlp_target ON temp_login_passwords (target_user_oid, expires_at)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-955, -1408) THEN RAISE; END IF;
END;
/

-- ----------------------------------------------------------------------------
-- DEVICE_CHALLENGES: 异设备登录挑战 + 失败计数
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE '
CREATE TABLE device_challenges (
  oid VARCHAR2(24 CHAR) NOT NULL,
  token_hash VARCHAR2(64 CHAR) NOT NULL,
  user_oid VARCHAR2(24 CHAR) NOT NULL,
  xuehai_id VARCHAR2(32 CHAR) NOT NULL,
  attempts NUMBER(3) DEFAULT 0 NOT NULL,
  expires_at TIMESTAMP NOT NULL,
  consumed_at TIMESTAMP,
  created_at TIMESTAMP NOT NULL,
  PRIMARY KEY (oid),
  CONSTRAINT ck_device_challenges_oid CHECK (REGEXP_LIKE(oid,''^[0-9a-f]{24}$''))
)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -955 THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'CREATE UNIQUE INDEX ux_device_challenges_token ON device_challenges (token_hash)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-955, -1408) THEN RAISE; END IF;
END;
/

-- ----------------------------------------------------------------------------
-- BANNED_DEVICES: 设备级封禁
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE '
CREATE TABLE banned_devices (
  xuehai_id VARCHAR2(32 CHAR) NOT NULL,
  reason VARCHAR2(512 CHAR),
  until TIMESTAMP,
  source VARCHAR2(16 CHAR) NOT NULL,
  rule_code VARCHAR2(64 CHAR),
  operator_oid VARCHAR2(24 CHAR),
  created_at TIMESTAMP NOT NULL,
  PRIMARY KEY (xuehai_id),
  CONSTRAINT ck_banned_devices_source CHECK (source IN (''rule'',''admin''))
)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -955 THEN RAISE; END IF;
END;
/

-- ----------------------------------------------------------------------------
-- 内置规则: 非主学海设备登录
--   oid 固定为 '0b0000000000000000000002'（'...0001' 已被
--   multi_device_login 占用），与 V001 的 seed 完全一致；MERGE 的
--   WHEN NOT MATCHED 分支保证重复执行时既不重复插入，也不覆盖已有配置。
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE '
MERGE INTO ban_rules t
USING (SELECT ''0b0000000000000000000002'' oid FROM dual) s
ON (t.code = ''foreign_device_login'')
WHEN NOT MATCHED THEN INSERT
  (oid, code, name, enabled, config, penalties, builtin, created_at, updated_at)
  VALUES (''0b0000000000000000000002'', ''foreign_device_login'', ''非主学海设备登录'',
          1, ''{"max_attempts":3}'', ''[1,3,7,14,30,null]'', 1,
          SYSTIMESTAMP, SYSTIMESTAMP)';
EXCEPTION
  -- -1: 并发执行时另一会话已插入（uq_ban_rules_code），结果一致，忽略。
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-1, -955) THEN RAISE; END IF;
END;
/
