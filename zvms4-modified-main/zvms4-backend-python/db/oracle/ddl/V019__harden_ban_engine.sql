-- ============================================================================
-- V019__harden_ban_engine.sql
-- ----------------------------------------------------------------------------
-- 封禁引擎的三处加固，在 V001~V018 已应用的实例上补齐：
--
--   * BAN_VIOLATIONS += BATCH_ID（一次上报 = 一批违规）
--     + ix_ban_violations_batch（窗口内 COUNT(DISTINCT ...) 的访问路径）
--   * BAN_RULES 补 platform_misuse 内置规则种子
--
-- Background:
--
--   * 计数口径从「一个信号一次」改成「一次上报一次」。页面被嵌套时客户端在
--     同一次 POST 里同时报 iframe / bridge / devtools，按行计数会让一次页面
--     加载就等于三次违规——阈值 2 的规则当场封号。BATCH_ID 由服务端生成
--     （客户端不能传），判定统计窗口内 ``COUNT(DISTINCT COALESCE(BATCH_ID, OID))``：
--     旧行 BATCH_ID 为 NULL，``COALESCE`` 让每一行自成一派，历史语义不变。
--
--   * 索引把 BATCH_ID 放进同一条访问路径：谓词是
--     ``user_oid + trigger_code + created_at >= :since``，BATCH_ID 作为第四列
--     让 COUNT(DISTINCT ...) 不必回表（索引本身有序，去重只走索引）。
--     保留 ix_ban_violations_user 不动：它是 V016 的既有访问路径，删掉只会让
--     存量执行计划失效，收益为零。
--
--   * platform_misuse 是上报闸（services.violation_guard）的检查项，V016 只注册
--     了平台侧能力，没有落种子。缺种子的后果不是「报错」，而是求值入口取不到
--     规则 → 只记录证据、永不封禁，管理员在界面上看不出任何异常。
--     种子 config 与 ban_rules.DEFAULT_CONFIG / violation_guard.DEFAULT_CONFIG
--     统一成 {"threshold":3,"window_hours":24}。
--
-- Idempotency:
--   Same EXECUTE IMMEDIATE pattern as V001-V018.  ALTER TABLE ADD 吞 ORA-01430
--   (column already exists); CREATE INDEX 吞 ORA-00955 / ORA-01408; MERGE 的
--   WHEN NOT MATCHED 分支让它可重复执行（并发插入撞 uq_ban_rules_code 时吞
--   ORA-00001），且不覆盖管理员调过的 threshold / penalties。
--
-- This file is mirrored by db/models.py / V001 (fresh instances get the same
-- column, index and seed from V001); run `python -m db.ddl_check` to verify
-- models/V001 stay in sync (offline, no DB access).
-- ============================================================================

-- ----------------------------------------------------------------------------
-- BAN_VIOLATIONS: 上报批次号
--   32 位是为了容纳「客户端不可传入的服务端标识」，当前服务端生成 24 位 hex。
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE 'ALTER TABLE ban_violations ADD batch_id VARCHAR2(32 CHAR)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -1430 THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE
    'CREATE INDEX ix_ban_violations_batch ON ban_violations (user_oid, trigger_code, created_at, batch_id)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-955, -1408) THEN RAISE; END IF;
END;
/

-- ----------------------------------------------------------------------------
-- 内置规则: 平台违规使用（上报闸的判定规则）
--   oid 固定为 '0b0000000000000000000003'，与 V001 的 seed 一致。
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE '
MERGE INTO ban_rules t
USING (SELECT ''0b0000000000000000000003'' oid FROM dual) s
ON (t.code = ''platform_misuse'')
WHEN NOT MATCHED THEN INSERT
  (oid, code, name, trigger_code, enabled, config, penalties, builtin, created_at, updated_at)
  VALUES (''0b0000000000000000000003'', ''platform_misuse'', ''平台违规使用'',
          ''platform_misuse'', 1, ''{"threshold":3,"window_hours":24}'',
          ''[1,3,7]'', 1, SYSTIMESTAMP, SYSTIMESTAMP)';
EXCEPTION
  -- -1: 并发执行时另一会话已插入（uq_ban_rules_code），结果一致，忽略。
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-1, -955) THEN RAISE; END IF;
END;
/
