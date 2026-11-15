-- ============================================================================
-- V036__system_settings.sql
-- ----------------------------------------------------------------------------
-- 在线设置中心（管理员可改、存库、改完即时生效）：
--
--   SYSTEM_SETTINGS        ← new（一个可调项一行，值是 JSON 文本）
--   USER_QUOTA_OVERRIDES   ← new（按用户覆盖每日上传配额；NULL = 继承全局）
--   ix_ai_prompt_audit_created ← new（全用户按时间查询的索引）
--
-- 列 / 语义：
--
--   对象                                语义
--   ------------------------------------------------------------------------
--   system_settings.setting_key         注册表里的键（例如 ai.allowed_roles）
--   system_settings.value_json          JSON 编码后的值；字符串/数字/布尔/数组/对象
--   system_settings.updated_by          最后一次修改的管理员 oid（可空：脚本/迁移写入）
--   system_settings.updated_at          最后一次修改时刻（naive TIMESTAMP）
--   user_quota_overrides.user_oid       用户 oid（主键，一人最多一行）
--   user_quota_overrides.daily_max_files  每日可上传文件数；NULL = 继承全局
--   user_quota_overrides.daily_max_bytes  每日可上传字节数；NULL = 继承全局
--
-- Background:
--   * ``value_json`` 用**裸 CLOB** 而不是 ``CLOB + IS JSON``：这里最常见的取值是
--     标量（``"department"`` / ``120`` / ``true``），而 Oracle 19c 的 ``IS JSON``
--     不接受标量（要 21c 的 ``IS JSON SCALAR``）。约束会把合法的单值挡在门外，
--     所以 JSON 编码/解析放在应用层（``db/repos/oracle/system_settings.py``）。
--   * ``USER_QUOTA_OVERRIDES`` 的 NULL 是「继承全局」而不是「0 个」：0 是真实
--     的合法上限（封禁某人的上传），NULL 才是没设过。两列都不建外键——用户
--     删除时这行没有保留价值，但也绝不阻止删除（与全库「删除显式批删」一致）。
--   * ``ix_ai_prompt_audit_created``：现有唯一索引是 ``(user_oid, created_at)``，
--     前导列不匹配，按 ``created_at`` 做全用户保留期清理走不了它。
--
-- Idempotency:
--   Same EXECUTE IMMEDIATE pattern as V024-V035：CREATE TABLE 吞 ORA-00955
--   （对象已存在）；CREATE INDEX 吞 ORA-00955 / ORA-01408（索引已存在）。
--   本文件只建新对象、不 ALTER 既有表。
--
-- This file is mirrored by db/models.py / V001 (fresh instances get the same
-- two tables, checks and the index from V001); run `python -m db.ddl_check` to
-- verify they stay in sync (offline, no DB access).
-- ============================================================================

-- ----------------------------------------------------------------------------
-- SYSTEM_SETTINGS：一个可调项一行
--   SETTING_KEY 直接做主键（天然唯一，不需要额外的代理 oid）；键由应用层
--   注册表白名单校验，数据库不做枚举 —— 注册表是会增加的，把它抄进 CHECK
--   等于每次加一个可调项都要改两张 DDL。
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE '
CREATE TABLE system_settings (
  setting_key VARCHAR2(64 CHAR) NOT NULL,
  value_json CLOB NOT NULL,
  updated_by VARCHAR2(24 CHAR),
  updated_at TIMESTAMP NOT NULL,
  PRIMARY KEY (setting_key)
)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -955 THEN RAISE; END IF;
END;
/

-- ----------------------------------------------------------------------------
-- USER_QUOTA_OVERRIDES：按用户覆盖每日上传配额
--   两列 NULL 都表示「继承全局设置」，不是 0；0 是真实的「一个都不许传」。
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE '
CREATE TABLE user_quota_overrides (
  user_oid VARCHAR2(24 CHAR) NOT NULL,
  daily_max_files NUMBER,
  daily_max_bytes NUMBER,
  updated_by VARCHAR2(24 CHAR),
  updated_at TIMESTAMP NOT NULL,
  PRIMARY KEY (user_oid),
  CONSTRAINT ck_user_quota_overrides_files
    CHECK (daily_max_files IS NULL OR daily_max_files >= 0),
  CONSTRAINT ck_user_quota_overrides_bytes
    CHECK (daily_max_bytes IS NULL OR daily_max_bytes >= 0)
)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -955 THEN RAISE; END IF;
END;
/

-- ----------------------------------------------------------------------------
-- AI_PROMPT_AUDIT 的单列时间索引：现有唯一索引 (user_oid, created_at) 的前导列
--   是 user_oid，保留期清理按 created_at 扫全表时用不上它。
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE '
CREATE INDEX ix_ai_prompt_audit_created ON ai_prompt_audit (created_at)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-955, -1408) THEN RAISE; END IF;
END;
/
