-- ============================================================================
-- V020__risk_rules.sql
-- ----------------------------------------------------------------------------
-- 七条「服务端可观测数据」封禁规则的种子，在 V001~V019 已应用的实例上补齐：
--
--   * BAN_RULES += login_failure_burst / ban_evasion_retry / challenge_flood /
--     temp_password_issue_burst / export_burst / login_success_churn /
--     device_switch_burst
--
-- Background:
--
--   * 这七条规则的证据全部由服务端自己写下，不需要客户端上报：
--     APP_LOGS 里的登录失败 / 成功登录 / 主设备不匹配 / 临时密码签发 / 导出，
--     以及 DEVICE_CHALLENGES 里的挑战签发。服务层求值在
--     ``services.risk_monitor``，检查项在 ``routers.ban_router.TRIGGERS``。
--
--   * 规则 code 与 trigger 同名：管理员在流水里看到的 rule_code 就是检查项，
--     「为什么被封」不需要再查一次映射表。
--
--   * ``config.mode`` 是「达到阈值之后做什么」：``ban`` 按梯度封禁，``alert``
--     只写证据 + 告警审计。新口径先跑 alert 观察误报率，是这次扩展里唯一
--     影响「会不会真封人」的开关；缺省（没有该键）等价于 ``ban``，所以存量
--     两条规则的行为不变。
--
--   * alert 模式的 penalties 落 JSON ``null``：没有处罚梯度，任何读取方都会
--     在 ``rule_penalties`` 里回落到默认值——alert 路径根本不调用封禁，
--     写 null 是为了让「这条规则不封人」在数据里也成立，而不是靠调用方记得。
--
--   * 「登录失败暴破」与「封禁期内重试」共用 ``ltype='login_fail'``：前者数
--     全部失败，后者只数摘要含 ``banned`` 的那部分（账号处于封禁态时的尝试）。
--     两行规则各自的 config 决定阈值，证据是同一份。
--
-- Idempotency:
--   Same EXECUTE IMMEDIATE pattern as V001-V019.  MERGE 的 WHEN NOT MATCHED 分支
--   让它可重复执行（并发插入撞 uq_ban_rules_code 时吞 ORA-00001），并且
--   **不覆盖**管理员调过的 threshold / window_hours / mode / penalties。
--   没有 DDL 变更：本次不新增表 / 列 / 索引。
--
-- This file is mirrored by V001 (fresh instances get the same seeds from V001);
-- run `python -m db.ddl_check` to verify models/V001 stay in sync.
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 内置规则: 登录失败暴破（APP_LOGS ltype=login_fail）
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE '
MERGE INTO ban_rules t
USING (SELECT ''0b0000000000000000000004'' oid FROM dual) s
ON (t.code = ''login_failure_burst'')
WHEN NOT MATCHED THEN INSERT
  (oid, code, name, trigger_code, enabled, config, penalties, builtin, created_at, updated_at)
  VALUES (''0b0000000000000000000004'', ''login_failure_burst'', ''登录失败暴破'',
          ''login_failure_burst'', 1, ''{"threshold":10,"window_hours":1,"mode":"ban"}'',
          ''[1,3,7]'', 1, SYSTIMESTAMP, SYSTIMESTAMP)';
EXCEPTION
  -- -1: 并发执行时另一会话已插入（uq_ban_rules_code），结果一致，忽略。
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-1, -955) THEN RAISE; END IF;
END;
/

-- ----------------------------------------------------------------------------
-- 内置规则: 封禁期内反复尝试登录（APP_LOGS ltype=login_fail 且摘要含 banned）
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE '
MERGE INTO ban_rules t
USING (SELECT ''0b0000000000000000000005'' oid FROM dual) s
ON (t.code = ''ban_evasion_retry'')
WHEN NOT MATCHED THEN INSERT
  (oid, code, name, trigger_code, enabled, config, penalties, builtin, created_at, updated_at)
  VALUES (''0b0000000000000000000005'', ''ban_evasion_retry'', ''封禁期内反复尝试登录'',
          ''ban_evasion_retry'', 1, ''{"threshold":5,"window_hours":1,"mode":"ban"}'',
          ''[1,3,7]'', 1, SYSTIMESTAMP, SYSTIMESTAMP)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-1, -955) THEN RAISE; END IF;
END;
/

-- ----------------------------------------------------------------------------
-- 内置规则: 设备挑战刷取（DEVICE_CHALLENGES 按 user_oid 计数，默认只告警）
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE '
MERGE INTO ban_rules t
USING (SELECT ''0b0000000000000000000006'' oid FROM dual) s
ON (t.code = ''challenge_flood'')
WHEN NOT MATCHED THEN INSERT
  (oid, code, name, trigger_code, enabled, config, penalties, builtin, created_at, updated_at)
  VALUES (''0b0000000000000000000006'', ''challenge_flood'', ''设备挑战刷取'',
          ''challenge_flood'', 1, ''{"threshold":8,"window_hours":1,"mode":"alert"}'',
          ''null'', 1, SYSTIMESTAMP, SYSTIMESTAMP)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-1, -955) THEN RAISE; END IF;
END;
/

-- ----------------------------------------------------------------------------
-- 内置规则: 临时密码签发过频（APP_LOGS ltype=temp_password_create，按签发人）
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE '
MERGE INTO ban_rules t
USING (SELECT ''0b0000000000000000000007'' oid FROM dual) s
ON (t.code = ''temp_password_issue_burst'')
WHEN NOT MATCHED THEN INSERT
  (oid, code, name, trigger_code, enabled, config, penalties, builtin, created_at, updated_at)
  VALUES (''0b0000000000000000000007'', ''temp_password_issue_burst'', ''临时密码签发过频'',
          ''temp_password_issue_burst'', 1, ''{"threshold":20,"window_hours":1,"mode":"alert"}'',
          ''null'', 1, SYSTIMESTAMP, SYSTIMESTAMP)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-1, -955) THEN RAISE; END IF;
END;
/

-- ----------------------------------------------------------------------------
-- 内置规则: 导出任务过频（APP_LOGS ltype=export，按请求者）
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE '
MERGE INTO ban_rules t
USING (SELECT ''0b0000000000000000000008'' oid FROM dual) s
ON (t.code = ''export_burst'')
WHEN NOT MATCHED THEN INSERT
  (oid, code, name, trigger_code, enabled, config, penalties, builtin, created_at, updated_at)
  VALUES (''0b0000000000000000000008'', ''export_burst'', ''导出任务过频'',
          ''export_burst'', 1, ''{"threshold":10,"window_hours":1,"mode":"alert"}'',
          ''null'', 1, SYSTIMESTAMP, SYSTIMESTAMP)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-1, -955) THEN RAISE; END IF;
END;
/

-- ----------------------------------------------------------------------------
-- 内置规则: 短时间反复登录（APP_LOGS ltype=user_login）
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE '
MERGE INTO ban_rules t
USING (SELECT ''0b0000000000000000000009'' oid FROM dual) s
ON (t.code = ''login_success_churn'')
WHEN NOT MATCHED THEN INSERT
  (oid, code, name, trigger_code, enabled, config, penalties, builtin, created_at, updated_at)
  VALUES (''0b0000000000000000000009'', ''login_success_churn'', ''短时间反复登录'',
          ''login_success_churn'', 1, ''{"threshold":15,"window_hours":1,"mode":"alert"}'',
          ''null'', 1, SYSTIMESTAMP, SYSTIMESTAMP)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-1, -955) THEN RAISE; END IF;
END;
/

-- ----------------------------------------------------------------------------
-- 内置规则: 频繁更换设备（APP_LOGS ltype=device_mismatch，24 小时窗口）
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE '
MERGE INTO ban_rules t
USING (SELECT ''0b000000000000000000000a'' oid FROM dual) s
ON (t.code = ''device_switch_burst'')
WHEN NOT MATCHED THEN INSERT
  (oid, code, name, trigger_code, enabled, config, penalties, builtin, created_at, updated_at)
  VALUES (''0b000000000000000000000a'', ''device_switch_burst'', ''频繁更换设备'',
          ''device_switch_burst'', 1, ''{"threshold":5,"window_hours":24,"mode":"alert"}'',
          ''null'', 1, SYSTIMESTAMP, SYSTIMESTAMP)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-1, -955) THEN RAISE; END IF;
END;
/
