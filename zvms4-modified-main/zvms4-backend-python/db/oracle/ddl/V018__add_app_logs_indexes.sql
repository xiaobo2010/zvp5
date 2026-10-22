-- ============================================================================
-- V018__add_app_logs_indexes.sql
-- ----------------------------------------------------------------------------
-- 管理端日志查询（GET /api/logs）的访问路径索引：
--
--   * ix_app_logs_ts_raw         ON app_logs (ts_raw)
--       服务：无过滤/仅时间窗的管理端列表
--       ``SELECT ... ORDER BY ts_raw DESC OFFSET :o ROWS FETCH FIRST :n``。
--   * ix_app_logs_type_level_ts  ON app_logs (ltype, log_level, ts_raw)
--       服务：按 type（可再叠加 level）过滤 + 同一 ts_raw 排序的列表与计数。
--
-- Background:
--   * 列表默认 ``ORDER BY ts_raw DESC``，但 V001 只有 ix_app_logs_ts_at(ts_at)
--     与 ix_app_logs_user_ts(app_user, ts_raw)：前者前导列是 ts_at，后者前导
--     列是 app_user，都不匹配「无过滤、按 ts_raw 排序」的形状 → 每次翻页都要
--     把整张表排序一遍。ts_raw 单列索引让执行路径变成索引反向扫描（Oracle
--     索引默认升序，``ORDER BY ts_raw DESC`` 可以反向扫），取满 size 行即停。
--     时间窗谓词 ``ts_raw > / < :x`` 也落在同一个索引上。
--   * 可索引谓词里剩下的只有 ltype / log_level。复合索引以 ltype 前导，
--     「只按类型」与「类型 + 级别」两种过滤都能用上，且 ts_raw 作为第三列让
--     排序/时间窗继续留在索引内。刻意不以 log_level 前导：level 只有
--     info/warning/error 三个值，做前导列选择性太差，还会把 type-only 查询
--     挡在索引外。只按 level 过滤的请求回落到 ts_raw 索引扫描 + 过滤。
--
-- 不建 DESC 索引：Oracle 能反向扫描升序索引，DESC 索引只在多列混合方向时
-- 才有收益，而且会和 V001 现有索引风格不一致。
--
-- Idempotency:
--   Same EXECUTE IMMEDIATE pattern as V001-V017.  CREATE INDEX swallows
--   ORA-00955 (name already used) and ORA-01408 (index already exists) so
--   re-running against an instance that already has the index is a safe no-op;
--   any other error is re-raised.
--
-- This file is mirrored by db/models.py / V001 (fresh instances get the same
-- indexes from V001); run `python -m db.ddl_check` to verify they stay in sync
-- (offline, no DB access).
-- ============================================================================

BEGIN
  EXECUTE IMMEDIATE 'CREATE INDEX ix_app_logs_ts_raw ON app_logs (ts_raw)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-955, -1408) THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'CREATE INDEX ix_app_logs_type_level_ts ON app_logs (ltype, log_level, ts_raw)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-955, -1408) THEN RAISE; END IF;
END;
/
