-- ============================================================================
-- V052__drop_user_trash_restored_at.sql
-- ----------------------------------------------------------------------------
-- 删除 USER_TRASH.RESTORED_AT（无用的标记列）。
--
-- 依据（全生命周期核对）：
--   * 唯一写入点 db/repos/oracle/users.py 的归档 INSERT 恒写 NULL；
--   * 全仓无任何读取方（routers / services / tasks / tests 均不读该列）；
--   * 无索引、无约束、无触发器依赖；
--   * 保留期 GC（tasks/user_trash_gc.py）只按 deleted_at 判定，与它无关。
-- 换言之它是「写了从不用」的纯负担：每条回收站行白占 7 字节以上，还让
-- 「恢复功能」这个不存在的语义悬在 schema 里。V011 的基线列定义同步删除
-- （与 V047 <-> V001 的「基线同步编辑 + 版本迁移」惯例一致）。
--
-- 执行：本文件只入库，不会自动执行（应用启动不跑 DDL）。生产在发版窗口用
--   python misc/apply_ddl.py v052
-- 手工执行（--dry-run 只解析不连库）。
--
-- 前置：V011 已应用（表甚至可能不存在 —— 那种情况下本文件会如实报
-- ORA-00942，而不是假装成功）。
--
-- 回滚（列加回来，历史值不可恢复 —— 本来也全是 NULL）：
--   ALTER TABLE user_trash ADD restored_at TIMESTAMP NULL;
--
-- 幂等性：重跑时列已不存在，ALTER TABLE ... DROP COLUMN 抛 ORA-00904
-- （invalid identifier，SQLCODE = -904），吞掉它；其它错误照常 RAISE。
-- ============================================================================

BEGIN
  EXECUTE IMMEDIATE 'ALTER TABLE user_trash DROP COLUMN restored_at';
EXCEPTION
  -- -904: 列不存在（已删过 / 重跑）。
  WHEN OTHERS THEN
    IF SQLCODE != -904 THEN RAISE; END IF;
END;
/
