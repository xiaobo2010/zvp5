-- ============================================================================
-- V038__changelog_notifications.sql
-- ----------------------------------------------------------------------------
-- CHANGELOG 自动同步为 SYSTEM 版本公告: adds
--
--   * NOTIFICATIONS.SOURCE_REF            ← new（来源对象 oid，可空）
--   * uq_notifications_source_ref         ← new（UNIQUE(source_ref)）
--
-- Background:
--
--   * 版本公告是「一条 CHANGELOG 行」的投影，不是管理员手写的内容。同步逻辑
--     （services/changelog_notifications.py）必须能判断「这条 CHANGELOG 已经发过
--     公告了吗」，否则每次刷新都会重复建公告。用 CHANGELOG.OID 做 ``SOURCE_REF``
--     是唯一稳定的关联键：版本号可能被编辑改掉（VARCHAR2，无唯一约束），日期也
--     可能重复，只有主键在整条记录生命周期里不变。
--   * 唯一约束允许任意多个 NULL（Oracle 的唯一性判定把 NULL 视为互不相等），
--     所以管理员手写的通知 ``SOURCE_REF IS NULL`` 不受影响——它们本来就没有
--     来源对象，也不该被强制编造一个。同步公告则一定带值，唯一约束是并发
--     双写（两个管理员同时刷新）时的最后一道防线。
--   * 不建外键：CHANGELOG 行被硬删后公告仍要保留（发布过的事实已经发生），
--     与 NOTIFICATIONS 不对 AUTHOR_OID 建外键是同一口径。删除 CHANGELOG 时由
--     路由把对应公告置为 active = 0，而不是删掉它。
--   * 快照表 NOTIFICATION_VERSIONS 不动：它记录的是公告自身的内容修订，
--     来源引用不随修订变化，也没有「回滚到另一个来源」这种操作。
--
-- Idempotency:
--   Same EXECUTE IMMEDIATE pattern as V001-V037.  ALTER TABLE ADD 吞
--   ORA-01430（列已存在）/ ORA-00955（表不存在，由更新的基线建成）；
--   ADD CONSTRAINT 吞 ORA-02264（约束名已占用）/ ORA-00955 / ORA-00001
--   （并发执行时另一会话已建好，结果一致）。两条语句都可重复执行。
--
-- This file is mirrored by db/models.py / V001 (fresh instances get the column
-- and the unique constraint from V001); run `python -m db.ddl_check` to verify
-- models/V001 stay in sync (offline, no DB access).
-- ============================================================================

-- ----------------------------------------------------------------------------
-- NOTIFICATIONS.SOURCE_REF: 该公告由哪个对象生成（当前只有 CHANGELOG.OID）
--   空值 = 管理员手写通知。列宽与全库 oid 一致（24 位小写 hex），但不加
--   REGEXP_LIKE CHECK：来源对象将来可能不是 oid 形状的记录，约束会把合法值
--   挡在门外，校验放在写入口（服务层永远写 CHANGELOG 主键）。
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE 'ALTER TABLE notifications ADD (source_ref VARCHAR2(24 CHAR))';
EXCEPTION
  -- -1430: 列已存在（重复执行）；-955: 表不存在（由更新的基线建成）。
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-1430, -955) THEN RAISE; END IF;
END;
/

-- ----------------------------------------------------------------------------
-- uq_notifications_source_ref: 一个来源最多一条公告
--   Oracle 建 UNIQUE 约束时自动建同名唯一索引，查询 get_by_source_ref 直接
--   走它，不需要再单独 CREATE INDEX。
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE '
ALTER TABLE notifications ADD CONSTRAINT uq_notifications_source_ref UNIQUE (source_ref)';
EXCEPTION
  -- -2264: 约束名已占用（重复执行）；-955: 表不存在；-1: 并发执行时另一会话
  -- 已建好同名唯一约束，结果一致。
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-2264, -955, -1) THEN RAISE; END IF;
END;
/
