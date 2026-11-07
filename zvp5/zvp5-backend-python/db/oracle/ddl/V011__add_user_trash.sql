-- ============================================================================
-- V011__add_user_trash.sql
-- ----------------------------------------------------------------------------
-- 用户硬删除的回收站: adds USER_TRASH on an instance where V001 was already
-- applied.
--
--   * USER_TRASH (oid, user_oid, deleted_by, deleted_at, payload)
--     + ck_user_trash_oid / ck_user_trash_payload_json
--     + ix_user_trash_deleted_at (deleted_at)
--     + ix_user_trash_user_oid  (user_oid)
--
-- Legacy: this file originally also declared a `restored_at TIMESTAMP` column.
-- It was written as NULL on every insert and never read, so V052 drops it; the
-- baseline column definition was removed here in the same change (same
-- baseline-sync convention as V047 <-> V001).
--
-- Background (v4.2.4-2 契约 §3.3A-3/4): `DELETE /api/v3/users/{user_id}` 是
-- **硬删除 + 事务级联**。因为全库没有 ON DELETE CASCADE（V001 头部声明由应用
-- 层负责），删除前先把 users 行与全部关联行（user_time /
-- time_academic_year / time_with_origin / user_device_bindings 这几张真外键
-- 子表，外加 activity_members / files 元数据这两类软引用）序列化成 JSON 写进本
-- 表，再逐项清理、最后删 users 行; 整个过程在一个事务里完成。
--
--   * payload 是整包快照（CLOB + IS JSON），供人工回溯/恢复；
--   * deleted_at 是回收站的保留期基准: `tasks/user_trash_gc.py` 把
--     deleted_at < now - 30 天（可配）的行物理删除; GC **只**看 deleted_at
--     （契约 §3.3A-5 写的就是 deleted_at < now - 30 days）。
--   * 这里曾有 restored_at（「未被恢复过」的标记位），但它全生命周期只写
--     NULL、无读取方，V052 已删除该列；回收站不提供恢复功能。
--
-- user_oid **故意不加外键**指向 USERS(OID): 回收站的意义就是在 USERS 行被删掉
-- 之后仍然保留整包数据; 加了外键删除会直接被 ORA-02292 拒绝，回收站也就失去
-- 意义。deleted_by 只记录操作者 oid，同样不加外键（操作者账号随后也可能被删）。
--
-- Idempotency:
--   Same EXECUTE IMMEDIATE pattern as V001-V010.  CREATE TABLE swallows
--   ORA-00955 (name already used); CREATE INDEX additionally swallows ORA-01408
--   (index already exists).  Re-running against an instance that already has
--   some/all of these objects is a safe no-op; any other error is re-raised.
--
-- NOT mirrored in db/models.py / V001 (whitelist: v4.2.4-2 L3A 只允许新增本迁移
-- 文件), therefore `python -m db.ddl_check` does NOT cover this table; the
-- SQLAlchemy mirror of USER_TRASH lives next to its only consumer in
-- db/repos/oracle/users.py (`user_trash_tbl`, on a private MetaData precisely so
-- it cannot drift that check).
--
-- Applying: `python misc/apply_ddl.py v011` (idempotent; `--dry-run` parses
-- without connecting). SQLcl / sqlplus still work too — see
-- db/oracle/ddl/README.md ("Applying versioned migrations").
-- ============================================================================

BEGIN
  EXECUTE IMMEDIATE '
CREATE TABLE user_trash (
  oid VARCHAR2(24 CHAR) NOT NULL,
  user_oid VARCHAR2(24 CHAR) NOT NULL,
  deleted_by VARCHAR2(24 CHAR),
  deleted_at TIMESTAMP NOT NULL,
  payload CLOB NOT NULL,
  PRIMARY KEY (oid),
  CONSTRAINT ck_user_trash_oid CHECK (REGEXP_LIKE(oid,''^[0-9a-f]{24}$'')),
  CONSTRAINT ck_user_trash_payload_json CHECK (payload IS JSON)
)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -955 THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'CREATE INDEX ix_user_trash_deleted_at ON user_trash (deleted_at)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-955, -1408) THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'CREATE INDEX ix_user_trash_user_oid ON user_trash (user_oid)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-955, -1408) THEN RAISE; END IF;
END;
/
