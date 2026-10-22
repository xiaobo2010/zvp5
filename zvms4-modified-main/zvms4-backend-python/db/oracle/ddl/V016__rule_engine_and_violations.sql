-- ============================================================================
-- V016__rule_engine_and_violations.sql
-- ----------------------------------------------------------------------------
-- 规则引擎（检查项 TRIGGER）+ 平台违规使用记录，在 V001~V014 已应用的实例上
-- 补齐以下对象：
--
--   * BAN_RULES += TRIGGER（检查项：由平台注册，管理员只能选不能写代码）
--     + ux_ban_rules_trigger（一个检查项最多挂一条规则）
--   * 删除旧规则 multi_device_login（3 台设备/24h 封号：口径太粗、误伤正常换机）
--   * foreign_device_login 的配置从 max_attempts 换成 threshold（与其它规则同形）
--   * BAN_VIOLATIONS（客户端上报的违规信号，窗口内累计计数后按规则梯度封号）
--
-- Background:
--
--   * TRIGGER 是「检查什么」的稳定标识，CODE 是「这条规则叫什么」。把两者分开
--     是因为求值入口必须按检查项找规则（登录闸门只关心「非主设备登录」这一件
--     事），而管理员要能在一个检查项上换一条规则（改阈值/梯度）而不影响入口。
--     唯一索引是求值正确性的前提：同一检查项下有两条规则时，「用哪条」只能靠
--     运气，封禁天数就变成不可复现的。
--
--   * TRIGGER 可空（VARCHAR2(64) 无 NOT NULL）：Oracle 的唯一索引允许多个 NULL，
--     存量行在回填前保持 NULL，不阻塞加列；回填按 CODE 一一对应。
--
--   * 旧规则 multi_device_login **连行一起删**：它的口径（24 小时内 3 台设备）
--     已由 foreign_device_login（主设备 + 临时密码）取代，留着只会让管理员
--     以为它还在生效。历史 BAN_RECORDS 里的 rule_code 字符串保留——那是审计
--     证据，不随规则消失。
--
--   * BAN_VIOLATIONS 无外键：违规记录是审计材料，必须比账号活得久（用户被
--     硬删除后仍要能回答「他当时因为什么被封」）。SIGNAL 是 `iframe` /
--     `bridge` / `devtools`，DETAIL 存判定时的原始依据（如 `top!=self`）。
--     组合索引 (user_oid, trigger_code, created_at) 服务「窗口内计数」这条热路径。
--
-- Idempotency:
--   Same EXECUTE IMMEDIATE pattern as V001-V014.  ALTER TABLE ADD 吞
--   ORA-01430 (column already exists); CREATE TABLE 吞 ORA-00955; CREATE INDEX
--   另吞 ORA-01408 (index already exists); MERGE 的 WHEN NOT MATCHED 分支让它
--   可重复执行（并发插入撞 uq_ban_rules_code 时吞 ORA-00001）。两条 UPDATE 与
--   一条 DELETE 本身幂等（重复执行结果一致）。
--
-- This file is mirrored by db/models.py / V001 (fresh instances get the same
-- objects, and the same seed, from V001); run `python -m db.ddl_check` to
-- verify models/V001 stay in sync (offline, no DB access).
-- ============================================================================

-- ----------------------------------------------------------------------------
-- BAN_RULES: 检查项（trigger）
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE 'ALTER TABLE ban_rules ADD trigger_code VARCHAR2(64 CHAR)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -1430 THEN RAISE; END IF;
END;
/

-- 回填：两条内置规则各自对应一个检查项。
UPDATE ban_rules SET trigger_code = 'foreign_device_login'
 WHERE code = 'foreign_device_login';

-- 配置统一成 {threshold, window_hours} 的形状。threshold=3 保留旧的
-- 「连错 3 次」语义（设备闸读 threshold，回落 max_attempts）。
UPDATE ban_rules SET config = '{"threshold":3}'
 WHERE code = 'foreign_device_login';

-- 旧的多设备规则：口径已被取代，行删除；历史 BAN_RECORDS 不动。
DELETE FROM ban_rules WHERE code = 'multi_device_login';

-- 一个检查项最多一条规则：求值入口按 trigger 取唯一一条，结果才可复现。
BEGIN
  EXECUTE IMMEDIATE
    'CREATE UNIQUE INDEX ux_ban_rules_trigger ON ban_rules (trigger_code)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-955, -1408) THEN RAISE; END IF;
END;
/

-- ----------------------------------------------------------------------------
-- 内置规则: 非主学海设备登录（补齐 trigger）
--   oid 固定为 '0b0000000000000000000002'，与 V001/V014 的 seed 一致；MERGE 的
--   WHEN NOT MATCHED 分支保证重复执行既不重复插入、也不覆盖管理员调过的梯度。
--   （V014 可能未执行过，这里兜底插入。）
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE '
MERGE INTO ban_rules t
USING (SELECT ''0b0000000000000000000002'' oid FROM dual) s
ON (t.code = ''foreign_device_login'')
WHEN NOT MATCHED THEN INSERT
  (oid, code, name, trigger_code, enabled, config, penalties, builtin, created_at, updated_at)
  VALUES (''0b0000000000000000000002'', ''foreign_device_login'', ''非主学海设备登录'',
          ''foreign_device_login'', 1, ''{"threshold":3}'',
          ''[1,3,7,14,30,null]'', 1, SYSTIMESTAMP, SYSTIMESTAMP)';
EXCEPTION
  -- -1: 并发执行时另一会话已插入（uq_ban_rules_code），结果一致，忽略。
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-1, -955) THEN RAISE; END IF;
END;
/

-- ----------------------------------------------------------------------------
-- BAN_VIOLATIONS: 客户端上报的违规信号
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE '
CREATE TABLE ban_violations (
  oid VARCHAR2(24 CHAR) NOT NULL,
  user_oid VARCHAR2(24 CHAR) NOT NULL,
  trigger_code VARCHAR2(64 CHAR) NOT NULL,
  signal VARCHAR2(32 CHAR) NOT NULL,
  xuehai_id VARCHAR2(32 CHAR),
  detail VARCHAR2(500 CHAR),
  created_at TIMESTAMP NOT NULL,
  PRIMARY KEY (oid),
  CONSTRAINT ck_ban_violations_oid CHECK (REGEXP_LIKE(oid,''^[0-9a-f]{24}$''))
)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -955 THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE
    'CREATE INDEX ix_ban_violations_user ON ban_violations (user_oid, trigger_code, created_at)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-955, -1408) THEN RAISE; END IF;
END;
/
