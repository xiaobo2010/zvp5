-- ============================================================================
-- V037: agent 输入审核（AI_CALL_LOGS *= 'warning'）与越狱封禁规则种子
--
--   在 V001~V036 已应用的实例上补齐：
--     * AI_CALL_LOGS.STATUS 的 CHECK 重建：在既有值上新增 'warning'
--     * BAN_RULES 补内置规则 agent_jailbreak（越狱/提示注入）
--
-- Background:
--
--   * 'warning' 是「我们主动放行但必须留痕」的网关结论，与 rejected（挡掉）和
--     success（正常调用）都不同：管理员打开「无限制模式」时审核确实没跑，事后
--     追查不能只看到一串正常的 success。STATUS 是网关视角的结果，这属于同一
--     维度的第三个取值，不是另一张表。
--
--   * 约束必须 DROP 后 ADD（顺序不能反）：同名 ADD 会在 ORA-02264 上被吞掉，
--     于是新值永远加不进去。写法照抄 V034 的 ai_agent_events 那次重建。
--
--   * agent_jailbreak 是 violation_guard 的第二个检查项（第一个是
--     platform_misuse）。config 与 platform_misuse 同形（threshold /
--     window_hours / mode），梯度 [1,3,7,14,30,null] 与其末位 null = 永久一致。
--     固定 oid + MERGE 的 WHEN NOT MATCHED 分支：重复执行既不重复插入，也不
--     覆盖管理员调过的阈值与梯度。缺种子的后果是「只记录不封禁」——审核命中有
--     证据，却永远不会触发处罚。
--
-- Idempotency:
--   Same EXECUTE IMMEDIATE pattern as V001-V036.  DROP CONSTRAINT 吞 ORA-02443 /
--   ORA-00955；ADD CONSTRAINT 吞 ORA-02264 / ORA-00955；MERGE 吞 ORA-00001 /
--   ORA-00955。全部语句可重复执行。
--
-- This file is mirrored by db/models.py / V001 (fresh instances get the same
-- seed and widened CHECK from V001); run `python -m db.ddl_check` to verify
-- models/V001 stay in sync (offline, no DB access).
-- ============================================================================

-- ----------------------------------------------------------------------------
-- AI_CALL_LOGS.STATUS: 加入 'warning'
--   先 DROP 再 ADD，顺序不可颠倒（ADD 同名约束只会被吞掉）。
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE 'ALTER TABLE ai_call_logs DROP CONSTRAINT ck_ai_call_logs_status';
EXCEPTION
  -- -2443: 约束不存在（全新实例 / 已重跑）；-955: 表不存在（由更新的基线建成）。
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-2443, -2264, -955) THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE '
ALTER TABLE ai_call_logs ADD CONSTRAINT ck_ai_call_logs_status CHECK (status IN (''success'',''error'',''timeout'',''rejected'',''busy'',''warning''))';
EXCEPTION
  -- -2264: 约束名已被占用（重复执行）；-955: 表不存在（由更新的基线建成）。
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-2264, -955) THEN RAISE; END IF;
END;
/

-- ----------------------------------------------------------------------------
-- 内置规则: agent 越狱 / 提示注入
--   oid '0b000000000000000000000c' 是 0b 序列里的下一个空位（0001-000b 已被
--   V001 的种子占用）；trigger 与 code 同名，求值入口按 trigger 取这一行。
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE '
MERGE INTO ban_rules t
USING (SELECT ''0b000000000000000000000c'' oid FROM dual) s
ON (t.code = ''agent_jailbreak'')
WHEN NOT MATCHED THEN INSERT
  (oid, code, name, trigger_code, enabled, config, penalties, builtin, created_at, updated_at)
  VALUES (''0b000000000000000000000c'', ''agent_jailbreak'', ''agent 越狱与提示注入'',
          ''agent_jailbreak'', 1, ''{"threshold":3,"window_hours":24,"mode":"ban","max_attempts":3}'',
          ''[1,3,7,14,30,null]'', 1, SYSTIMESTAMP, SYSTIMESTAMP)';
EXCEPTION
  -- -1: 并发执行时另一会话已插入（uq_ban_rules_code），结果一致，忽略。
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-1, -955) THEN RAISE; END IF;
END;
/
