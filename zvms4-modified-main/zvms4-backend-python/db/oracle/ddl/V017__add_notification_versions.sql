-- ============================================================================
-- V017__add_notification_versions.sql
-- ----------------------------------------------------------------------------
-- 通知版本历史: adds NOTIFICATION_VERSIONS on an instance where V001 was already
-- applied.
--
--   * NOTIFICATION_VERSIONS (每次创建/编辑/删除/回滚的完整内容快照)
--   * uq_notification_versions (notification_oid, version)
--   * ix_notification_versions_doc ON notification_versions
--     (notification_oid, version)
--
-- Background:
--   * 一行 = 一次写入后的完整内容。不存 diff：回滚只需要「拿回某一版」，而
--     diff 链一旦中间行被误删就再也拼不出来。
--   * CONTENT_HASH 是 sha256(title + "\n" + content + "\n" + active 的 "1"/"0")，
--     64 位小写 hex。客户端只比较哈希就能判断内容是否等价，不必逐字对比。
--   * OP 取值 create / update / delete / rollback。SOURCE_VERSION 只在 rollback
--     行上有值，记录回滚到的目标版本。
--   * 不建外键：与 NOTIFICATIONS 相同口径——通知被硬删后历史仍要能回答
--     「当时是什么内容、谁改的」。
--   * (NOTIFICATION_OID, VERSION) 唯一：并发编辑在 NOTIFICATIONS 行锁上串行化
--     （SELECT ... FOR UPDATE），版本号不会交错；唯一约束是最后一道防线。
--
-- Idempotency:
--   Same EXECUTE IMMEDIATE pattern as V001-V016. CREATE TABLE 吞 ORA-00955；
--   CREATE INDEX 另吞 ORA-01408；表级 UNIQUE 约束已存在时 CREATE TABLE 本身
--   只会是 ORA-00955，重复执行不会重复建约束。
--
-- This file is mirrored by db/models.py / V001 (fresh instances get the same
-- objects from V001); run `python -m db.ddl_check` to verify they stay in sync
-- (offline, no DB access).
-- ============================================================================

-- ----------------------------------------------------------------------------
-- NOTIFICATION_VERSIONS: 通知内容快照（append-only）
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE '
CREATE TABLE notification_versions (
  oid VARCHAR2(24 CHAR) NOT NULL,
  notification_oid VARCHAR2(24 CHAR) NOT NULL,
  version NUMBER(19) NOT NULL,
  title VARCHAR2(200 CHAR) NOT NULL,
  content VARCHAR2(4000 CHAR) NOT NULL,
  active NUMBER(1) NOT NULL,
  op VARCHAR2(16 CHAR) NOT NULL,
  source_version NUMBER(19),
  content_hash VARCHAR2(64 CHAR) NOT NULL,
  author_oid VARCHAR2(24 CHAR),
  author_name VARCHAR2(128 CHAR) NOT NULL,
  created_at TIMESTAMP NOT NULL,
  PRIMARY KEY (oid),
  CONSTRAINT uq_notification_versions UNIQUE (notification_oid, version)
)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -955 THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'CREATE INDEX ix_notification_versions_doc ON notification_versions (notification_oid, version)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-955, -1408) THEN RAISE; END IF;
END;
/
