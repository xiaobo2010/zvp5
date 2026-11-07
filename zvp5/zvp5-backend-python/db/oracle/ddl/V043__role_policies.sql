-- ============================================================================
-- V043: 角色策略（ROLE_FEATURES / ROLE_QUOTAS）
--
--   在 V001~V042 已应用的实例上补齐两张「角色 → 能力」配置表：
--     * ROLE_FEATURES（role / feature_key / enabled / updated_at / updated_by）
--     * ROLE_QUOTAS  （role / kind / monthly_limit / updated_at / updated_by）
--
-- Background:
--
--   * 现在「角色能做什么」是写死在代码里的散点判断（约 15 处 ``_require_*``
--     guard 与内联 ``position.includes``），管理员改不了；AI 月额度虽然是运行
--     期可调的 ``ai.role_quota``，但它是**整表 JSON**，改一个角色的一个数字要
--     提交整张表，而且与「功能开关」不在同一处。
--
--   * 这两张表是**稀疏覆盖层**，不是完整矩阵：``(role, feature_key)`` 没有行
--     就等于「按代码内置 DEFAULTS」。所以首次部署这两张表是空的，行为与改造前
--     完全一致；管理员只写他真正要改的格子。
--
--   * ``role`` 只允许三个可编辑角色。``admin`` **不落库**：管理员永远全开且
--     无限额，是代码里的短路（``services.roles.policy``），把它写成数据行只会
--     让人以为「取消勾选就能关掉管理员」。
--
--   * 不建外键/不引 JSON：两列都是封闭枚举，CHECK 是唯一可信的护栏（应用层
--     校验会漏，SQL*Plus 手工改库不会）。
--
--   * ``updated_at``/``updated_by`` 可空：手工补数据时不必编造审计字段，语义
--     是「不知道谁在什么时候改的」，用 NULL 比用 ``SYSDATE`` 假装知道更诚实。
--
-- Idempotency:
--   Same EXECUTE IMMEDIATE pattern as V024-V042.  CREATE TABLE 吞 ORA-00955
--   （表已存在）。
--
-- This file is mirrored by db/models.py / V001 (fresh instances get the same
-- tables and CHECK text); run `python -m db.ddl_check` to verify they stay in
-- sync.
-- ============================================================================

-- ----------------------------------------------------------------------------
-- ROLE_FEATURES: 角色 -> 功能开关（稀疏覆盖；缺行 = 代码默认）
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE '
CREATE TABLE role_features (
  role VARCHAR2(32 CHAR) NOT NULL,
  feature_key VARCHAR2(48 CHAR) NOT NULL,
  enabled NUMBER(1) DEFAULT 1 NOT NULL,
  updated_at TIMESTAMP,
  updated_by CHAR(24 CHAR),
  PRIMARY KEY (role, feature_key),
  CONSTRAINT ck_role_features_enabled CHECK (enabled IN (0,1)),
  CONSTRAINT ck_role_features_role CHECK (role IN (''department'',''secretary'',''student''))
)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -955 THEN RAISE; END IF;
END;
/

-- ----------------------------------------------------------------------------
-- ROLE_QUOTAS: 角色 -> 每月额度（chat / ocr / schedule；缺行 = 代码默认）
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE '
CREATE TABLE role_quotas (
  role VARCHAR2(32 CHAR) NOT NULL,
  kind VARCHAR2(16 CHAR) NOT NULL,
  monthly_limit NUMBER(12),
  updated_at TIMESTAMP,
  updated_by CHAR(24 CHAR),
  PRIMARY KEY (role, kind),
  CONSTRAINT ck_role_quotas_kind CHECK (kind IN (''chat'',''ocr'',''schedule'')),
  CONSTRAINT ck_role_quotas_limit CHECK (monthly_limit >= 0)
)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -955 THEN RAISE; END IF;
END;
/
