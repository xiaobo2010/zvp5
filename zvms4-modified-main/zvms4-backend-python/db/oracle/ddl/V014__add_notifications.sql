-- ============================================================================
-- V014__add_notifications.sql
-- ----------------------------------------------------------------------------
-- 站内通知: adds NOTIFICATIONS and the singleton NOTIFICATION_STATE counter on
-- an instance where V001 was already applied.
--
--   * NOTIFICATIONS (管理员发布的通知 + 不可变发布人 + 单条修订号)
--   * NOTIFICATION_STATE (单行全局版本号，客户端用它判断是否需要刷新)
--   * ix_notifications_active_created  ON notifications (active, created_at)
--   * NOTIFICATION_STATE seed (id = 1, version = 0)
--
-- Background:
--   * AUTHOR_OID / AUTHOR_NAME 是**发布当时**的事实。写入后不随用户改名而
--     变化，也不建外键：账号被删除后历史通知仍要能回答「谁发的」。
--   * VERSION 是单条通知的修订号：创建 1，每次成功编辑/停用 +1。客户端用它
--     判断「这条已阅过的通知是否被改过」；它不随其他通知变化。
--   * NOTIFICATION_STATE 只有 id = 1 一行。每次成功的创建/编辑/删除在同一
--     事务里把 VERSION + 1；单行 UPDATE 在数据库层串行化并发管理员写入。
--     客户端本地记录的「已阅 / 不再显示」标记只影响本设备/本账号，绝不回写
--     服务端，因此不会推进全局版本。
--   * CONTENT 用 VARCHAR2(4000 CHAR) 而不是 CLOB：通知正文有明确上限，整行
--     读取，不需要 LOB locator 的额外往返。
--
-- Idempotency:
--   Same EXECUTE IMMEDIATE pattern as V001-V013.  CREATE TABLE 吞 ORA-00955;
--   CREATE INDEX 另吞 ORA-01408。种子用 MERGE ... WHEN NOT MATCHED THEN
--   INSERT：已存在则什么都不做，绝不把线上版本号重置回 0。
--
-- This file is mirrored by db/models.py / V001 (fresh instances get the same
-- objects and seed from V001); run `python -m db.ddl_check` to verify they stay
-- in sync (offline, no DB access).
-- ============================================================================

-- ----------------------------------------------------------------------------
-- NOTIFICATIONS: 管理员通知
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE '
CREATE TABLE notifications (
  oid VARCHAR2(24 CHAR) NOT NULL,
  title VARCHAR2(200 CHAR) NOT NULL,
  content VARCHAR2(4000 CHAR) NOT NULL,
  active NUMBER(1) DEFAULT 1 NOT NULL,
  author_oid VARCHAR2(24 CHAR) NOT NULL,
  author_name VARCHAR2(128 CHAR) NOT NULL,
  version NUMBER(19) DEFAULT 1 NOT NULL,
  created_at TIMESTAMP NOT NULL,
  updated_at TIMESTAMP NOT NULL,
  PRIMARY KEY (oid),
  CONSTRAINT ck_notifications_oid CHECK (REGEXP_LIKE(oid,''^[0-9a-f]{24}$'')),
  CONSTRAINT ck_notifications_active CHECK (active IN (0,1)),
  CONSTRAINT ck_notifications_version CHECK (version > 0)
)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -955 THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'CREATE INDEX ix_notifications_active_created ON notifications (active, created_at)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-955, -1408) THEN RAISE; END IF;
END;
/

-- ----------------------------------------------------------------------------
-- NOTIFICATION_STATE: 全局版本号（单行）
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE '
CREATE TABLE notification_state (
  id NUMBER(1) NOT NULL,
  version NUMBER(19) DEFAULT 0 NOT NULL,
  updated_at TIMESTAMP NOT NULL,
  PRIMARY KEY (id),
  CONSTRAINT ck_notification_state_id CHECK (id = 1),
  CONSTRAINT ck_notification_state_version CHECK (version >= 0)
)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -955 THEN RAISE; END IF;
END;
/

-- ----------------------------------------------------------------------------
-- 种子: id = 1, version = 0
--   固定与 V001 完全一致；MERGE 的 WHEN NOT MATCHED 分支保证重复执行时既不
--   重复插入，也不覆盖已有版本号。
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE '
MERGE INTO notification_state t
USING (SELECT 1 id FROM dual) s
ON (t.id = s.id)
WHEN NOT MATCHED THEN INSERT (id, version, updated_at)
  VALUES (1, 0, SYSTIMESTAMP)';
EXCEPTION
  -- -1: 并发执行时另一会话已插入（主键冲突），结果一致，忽略。
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-1, -955) THEN RAISE; END IF;
END;
/
