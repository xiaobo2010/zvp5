-- ============================================================================
-- V039: 设备侧处罚梯度（DEVICE_BAN_COUNTERS）与 device_temp_password_abuse 规则种子
--
--   在 V001~V038 已应用的实例上补齐：
--     * DEVICE_BAN_COUNTERS（xuehai_id PK / ban_count / last_banned_at）
--     * BAN_RULES 补内置规则 device_temp_password_abuse（设备封禁梯度）
--
-- Background:
--
--   * 临时密码连错从「封账号 + 永久封设备」改成「只封当事设备、按设备梯度
--     计时」：同一台机器反复违规要逐档加刑 [1,3,7,14,30,永久]，而账号不再
--     被连坐。梯度必须落在库里（不能放内存）：多 worker 下内存计数会让
--     「第 3 次」永远数不满。
--
--   * 计数器与 BANNED_DEVICES 分表：解封只删当前封禁行，历史违规次数留着；
--     管理员要真正「一笔勾销」走 reset-gradient。两件事的生命周期不同，合并
--     成一张表会让解封悄悄重置梯度。
--
--   * device_temp_password_abuse 是**梯度规则**，不是新的证据来源：连错次数
--     的阈值仍取 foreign_device_login 的 config，这条规则只提供 penalties。
--     trigger_code 与 code 同名（ux_ban_rules_trigger 要求检查项唯一），
--     求值代码按 code 取它，不按 trigger 走通用闸门。
--
-- Idempotency:
--   Same EXECUTE IMMEDIATE pattern as V001-V038.  CREATE TABLE 吞 ORA-00955；
--   内置规则 MERGE 的 WHEN NOT MATCHED 分支让它可重复执行；并发插入撞
--   uq_ban_rules_code 时吞 ORA-00001。
--
-- This file is mirrored by db/models.py / V001 (fresh instances get the same
-- table and seed); run `python -m db.ddl_check` to verify models/V001 stay in
-- sync (offline, no DB access).
-- ============================================================================

-- ----------------------------------------------------------------------------
-- DEVICE_BAN_COUNTERS: 设备侧处罚梯度
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE '
CREATE TABLE device_ban_counters (
  xuehai_id VARCHAR2(32 CHAR) NOT NULL,
  ban_count NUMBER(5) DEFAULT 0 NOT NULL,
  last_banned_at TIMESTAMP,
  PRIMARY KEY (xuehai_id)
)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -955 THEN RAISE; END IF;
END;
/

-- ----------------------------------------------------------------------------
-- 内置规则: 临时密码滥用的设备梯度
--   oid '0b000000000000000000000d' 是 0b 序列里的下一个空位（000c 已被 V037
--   的 agent_jailbreak 占用）。
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE '
MERGE INTO ban_rules t
USING (SELECT ''0b000000000000000000000d'' oid FROM dual) s
ON (t.code = ''device_temp_password_abuse'')
WHEN NOT MATCHED THEN INSERT
  (oid, code, name, trigger_code, enabled, config, penalties, builtin, created_at, updated_at)
  VALUES (''0b000000000000000000000d'', ''device_temp_password_abuse'',
          ''临时密码滥用（设备）'', ''device_temp_password_abuse'', 1,
          ''{"threshold":3,"window_hours":24}'', ''[1,3,7,14,30,null]'', 1,
          SYSTIMESTAMP, SYSTIMESTAMP)';
EXCEPTION
  -- -1: 并发执行时另一会话已插入（uq_ban_rules_code），结果一致，忽略。
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-1, -955) THEN RAISE; END IF;
END;
/
