-- ============================================================================
-- V042: 放宽封禁来源枚举，允许 AGENT 作为 source
--
-- Background:
--
--   agent 平台工具（tools_platform.user_ban / user_unban）需要把「由模型发起
--   的封禁」与「管理员手点」和「规则自动」区分开：三者在管理端的可信度不同，
--   事后追责也要能一眼看出是谁干的。原来的 CHECK 只允许 ('admin','rule')，
--   写 source='agent' 会被数据库直接拒绝（ORA-02290），表现为工具 503。
--
--   BANNED_DEVICES 同步放宽：现在只有解封，但设备侧迟早也要能由 agent 封，
--   两边口径不一致会在下一次扩展时再撞一次。
--
-- Idempotency:
--   DROP 吞 ORA-02443（约束不存在）；ADD 吞 ORA-02264（同名约束已存在）。
--   重复执行等价于「删了再建同一条」，对已有数据无影响（新集合是旧集合的超集）。
--
-- This file is mirrored by db/models.py / V001 (fresh instances get the same
-- CHECK text); run `python -m db.ddl_check` to verify they stay in sync.
-- ============================================================================

BEGIN
  EXECUTE IMMEDIATE 'ALTER TABLE ban_records DROP CONSTRAINT ck_ban_records_source';
EXCEPTION
  -- -2443: 约束不存在（首次执行/已被删掉）。
  WHEN OTHERS THEN
    IF SQLCODE != -2443 THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE '
ALTER TABLE ban_records ADD CONSTRAINT ck_ban_records_source
  CHECK (source IN (''admin'',''rule'',''agent''))';
EXCEPTION
  -- -2264: 同名约束已存在（重复执行）。
  WHEN OTHERS THEN
    IF SQLCODE != -2264 THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'ALTER TABLE banned_devices DROP CONSTRAINT ck_banned_devices_source';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -2443 THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE '
ALTER TABLE banned_devices ADD CONSTRAINT ck_banned_devices_source
  CHECK (source IN (''rule'',''admin'',''agent''))';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -2264 THEN RAISE; END IF;
END;
/
