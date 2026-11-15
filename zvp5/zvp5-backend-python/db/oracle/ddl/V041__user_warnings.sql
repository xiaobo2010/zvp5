-- ============================================================================
-- V041: 警告账本（USER_WARNINGS）
--
--   在 V001~V040 已应用的实例上补齐：
--     * USER_WARNINGS（oid / user_oid / rule_code / period / reason / detail）
--     * UX_USER_WARNINGS_USER_RULE_PERIOD（同一规则同一自然月最多一行警告）
--
-- Background:
--
--   * 「每月一次警告」必须是库里的**一行事实**，不能放进程内存：部署一天几次，
--     内存计数会在重启后归零，同一个人一个月能被无限次「首次警告」。
--
--   * PERIOD 是 YYYYMM 文本（与 AI_QUOTA 同一口径）。月度归零靠
--     (user_oid, rule_code, period) 的唯一索引天然完成，**不需要 cron**：
--     下个月的第一条 INSERT 就是新行，没有「清理任务没跑」这种失效模式。
--
--   * RULE_CODE 用规则 code 而不是 trigger：管理员新建的规则 code 可能不等于
--     trigger，警告账本与封禁梯度必须按同一把 key 关联（见 device_guard
--     .count_for_user_rule 的口径）。
--
--   * 唯一冲突是**预期的控制流**（「本月已警告过」），不是错误：仓储层的
--     try_record 把 ORA-00001 翻译成 False，调用方据此升级为封禁。
--
--   * 不建外键：警告是审计材料，必须比账号活得久（同 BAN_VIOLATIONS）。
--
-- Idempotency:
--   Same EXECUTE IMMEDIATE pattern as V024-V040.  CREATE TABLE / CREATE INDEX 吞
--   ORA-00955（含 -1408：同列索引已存在）。
--
-- This file is mirrored by db/models.py / V001 (fresh instances get the same table
-- and indexes); run `python -m db.ddl_check` to verify models/V001 stay in sync
-- (offline, no DB access).
-- ============================================================================

-- ----------------------------------------------------------------------------
-- USER_WARNINGS: 每用户/规则/自然月一行警告
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE '
CREATE TABLE user_warnings (
  oid VARCHAR2(24 CHAR) NOT NULL,
  user_oid VARCHAR2(24 CHAR) NOT NULL,
  rule_code VARCHAR2(64 CHAR) NOT NULL,
  period CHAR(6 CHAR) NOT NULL,
  reason VARCHAR2(512 CHAR),
  detail VARCHAR2(500 CHAR),
  created_at TIMESTAMP NOT NULL,
  PRIMARY KEY (oid),
  CONSTRAINT ck_user_warnings_oid CHECK (REGEXP_LIKE(oid,''^[0-9a-f]{24}$'')),
  CONSTRAINT ck_user_warnings_period CHECK (REGEXP_LIKE(period,''^[0-9]{6}$''))
)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -955 THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE '
CREATE UNIQUE INDEX ux_user_warnings_user_rule_period ON user_warnings (user_oid, rule_code, period)';
EXCEPTION
  -- -955: 表不存在（由更新的基线建成）；-1408: 同列索引已存在。
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-955, -1408) THEN RAISE; END IF;
END;
/
