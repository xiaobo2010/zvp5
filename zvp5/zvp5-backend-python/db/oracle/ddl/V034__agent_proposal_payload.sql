-- ============================================================================
-- V034__agent_proposal_payload.sql
-- ----------------------------------------------------------------------------
-- 把「提案」从「一份小 JSON」升级成「可复原 + 可审阅 + 双人授权」的完整记录。
-- 在 V033（新建 AI_AGENT_EVENTS）已应用的实例上做纯增量：
--
--   AI_AGENT_EVENTS += ARGS_PAYLOAD / PREVIEW / APPROVER_OID / CONFIRMED_AT
--   ck_ai_agent_events_status 重建（+ executing）
--
-- 列 / 约束的语义：
--
--   列             类型            语义
--   ------------------------------------------------------------------------
--   ARGS_PAYLOAD   CLOB            完整可复原参数（canonical JSON）；ARGS_HASH
--                                  覆盖它，确认时重算比对
--   PREVIEW        CLOB            确认界面用的结构化预览（diff / command /
--                                  SQL / 行变更）；服务端不理解也不校验
--   APPROVER_OID   VARCHAR2(32)    双人码审批人（另一位管理员）；无 TOTP 时为 NULL
--   CONFIRMED_AT   TIMESTAMP       实际执行时间
--
-- Background:
--   * **批准的就是执行的**：V033 里 ARGS_HASH 覆盖 ARGS_SUMMARY（≤500 的展示
--     副本），运营/运维工具的参数（文件内容、SQL、命令）根本放不进 500 字符。
--     V034 把完整参数放进 CLOB，hash 覆盖全文；ARGS_SUMMARY 退化为纯展示（超长
--     截断加 …），**不再**因为超长拒绝建提案（原 AI_AGENT_ARGS_TOO_LARGE 删除）。
--   * **兼容旧行**：V034 之前建的提案没有 ARGS_PAYLOAD，确认时回落到
--     ARGS_SUMMARY 校验（当时的 hash 就是它的哈希），活动域已落库的提案不失效。
--   * **状态机**：proposed → executing → confirmed | failed；驳回
--     proposed → rejected；过期 proposed → expired。全部走
--     ``WHERE status = <预期>`` 的 CAS，终态（含 executing）不会被并发的确认/
--     过期覆盖。因此 CHECK 必须接受 executing。
--   * **双人授权可追溯**：审批人与执行时间落在同一行上，而不是只留在
--     APP_LOGS 里——「这条提案是另一位管理员批的」必须与提案本身同生命周期。
--   * 四列全部 nullable：存量行（活动域的执行事件、已确认的提案）本来就没有
--     这些信息，NULL 是真实语义（不是 0、不是空串）。
--
-- Idempotency:
--   Same EXECUTE IMMEDIATE pattern as V024-V033：ALTER TABLE ADD 列吞 ORA-01430
--   （列已存在）/ ORA-00955（对象由更新的基线建成）；DROP CONSTRAINT 吞
--   ORA-02443（约束不存在）/ ORA-02264（同名对象其它类型）/ ORA-00955；
--   ADD CONSTRAINT 吞 ORA-02264 / ORA-00955。因此本文件在「V033 已应用」的存量
--   库与「全新建库」上都能重复执行，且重跑不改变任何已有对象。
--
-- This file is mirrored by db/models.py / V001 (fresh instances get the same
-- four columns and the widened CHECK from V001); run `python -m db.ddl_check`
-- to verify they stay in sync (offline, no DB access).
-- ============================================================================

-- ----------------------------------------------------------------------------
-- AI_AGENT_EVENTS：4 列（逐列 ADD，单列失败不会连带回滚其它列）
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE 'ALTER TABLE ai_agent_events ADD (args_payload CLOB)';
EXCEPTION
  -- -1430: 列已存在（重复执行）；-955: 兜底（对象由更新的基线建成）。
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-1430, -955) THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'ALTER TABLE ai_agent_events ADD (preview CLOB)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-1430, -955) THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'ALTER TABLE ai_agent_events ADD (approver_oid VARCHAR2(32 CHAR))';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-1430, -955) THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'ALTER TABLE ai_agent_events ADD (confirmed_at TIMESTAMP)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-1430, -955) THEN RAISE; END IF;
END;
/

-- ----------------------------------------------------------------------------
-- 状态 CHECK：重建以接受 executing（先 DROP 再 ADD，幂等吞错）
--   顺序不可颠倒：ADD 同名约束会在 -2264 上被吞掉，于是执行仍然缺 executing。
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE 'ALTER TABLE ai_agent_events DROP CONSTRAINT ck_ai_agent_events_status';
EXCEPTION
  -- -2443: 约束不存在（全新实例 / 已重跑）；-2264: 同名对象类型不符；
  -- -955: 表不存在（由更新的基线建成）。
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-2443, -2264, -955) THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE '
ALTER TABLE ai_agent_events ADD CONSTRAINT ck_ai_agent_events_status CHECK (status IN (''executed'',''proposed'',''executing'',''confirmed'',''rejected'',''expired'',''failed'',''denied''))';
EXCEPTION
  -- -2264: 约束名已被占用（重复执行）；-955: 表不存在（由更新的基线建成）。
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-2264, -955) THEN RAISE; END IF;
END;
/
