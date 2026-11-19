-- ============================================================================
-- V053__notification_reads.sql
-- ----------------------------------------------------------------------------
-- 在 V001~V052 已应用的实例上补齐 NOTIFICATION_READS（站内通知的每用户已读
-- 状态，跟着帐号走，替代客户端 localStorage）。
--
-- 为什么要有这张表：
--   * 已读状态原本只存在客户端 localStorage：换设备、清缓存、重装就得重读一遍
--     所有历史通知。放到服务端才是「跟着帐号走」。
--   * 存的是**版本号**而不是 bool：已读判定是 read[oid] >= notification.version。
--     通知被编辑后 VERSION + 1，旧值自动小于新版本 —— 语义上重新变未读，不需要
--     任何写扩散（不必去改每个用户的已读行）。
--   * 「不再显示 / suppress」不进这张表：那是设备级偏好（同一账号在手机上压制、
--     在平板上仍想看），留在前端。
--
-- 形状：
--   PK (USER_OID, NOTIFICATION_OID) —— 一条通知对一个用户只有一行，重复标记是
--   upsert（MERGE），不会长成历史流水。
--   没有 OID 列：这行没有独立身份，不会被别的对象引用，也就不需要主键候选键。
--   没有外键：用户与通知都可能被硬删除，而这些行是用户数据、不是审计材料；
--   通知硬删除时由仓储层在同一条 DELETE 事务里清理对应行，防孤儿。
--   索引：PK 自带的 (USER_OID, NOTIFICATION_OID) 同时服务按用户取全量（前缀列）
--   与按通知清理（Oracle 对复合索引可做 skip scan），不再额外建索引。
--
-- 执行：本文件只入库，不会自动执行（应用启动不跑 DDL）。生产在发版窗口用
--   python misc/apply_ddl.py v053
-- 手工执行（--dry-run 只解析不连库）。
--
-- 前置：无（不依赖任何既有对象）。注意后端必须**先**上表再发新版本：新接口
-- 会读写这张表，表不存在时表现为 ORA-00942 → 500。
--
-- 回滚：
--   DROP TABLE notification_reads;   -- 只丢已读状态，通知本身不受影响
--
-- 幂等性：同 V024 起的惯例 —— CREATE TABLE 吞 ORA-00955（对象已存在 = 重跑），
-- 其它错误照常 RAISE。
-- ============================================================================

BEGIN
  EXECUTE IMMEDIATE '
CREATE TABLE notification_reads (
  user_oid VARCHAR2(24 CHAR) NOT NULL,
  notification_oid VARCHAR2(24 CHAR) NOT NULL,
  version NUMBER(19) NOT NULL,
  read_at TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL,
  PRIMARY KEY (user_oid, notification_oid)
)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -955 THEN RAISE; END IF;
END;
/
