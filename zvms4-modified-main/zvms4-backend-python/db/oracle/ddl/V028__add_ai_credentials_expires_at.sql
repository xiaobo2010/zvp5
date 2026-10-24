-- ============================================================================
-- V028__add_ai_credentials_expires_at.sql
-- ----------------------------------------------------------------------------
-- AI 凭据到期: adds AI_CREDENTIALS.EXPIRES_AT on an instance where V001 / V025 /
-- V026 were already applied.
--
--   * AI_CREDENTIALS.EXPIRES_AT (TIMESTAMP; NULL = 永不过期)
--
-- 编号说明:
--   V027（extend_ai_ocr_engines_kind）已占用，本文件顺延为 V028。
--
-- Background:
--   * 上游 provider 的 token 大多有有效期（试用 key、季度结算的合同 key）。
--     以前凭据只有 ENABLED 一个开关：key 一到期，网关就会拿一把已经失效的 key
--     反复打上游，管理员看到的是「莫名其妙的 401」而不是「这把 key 过期了」。
--   * NULL = 永不过期，而不是「未知」。存量行因此不需要回填：它们本来就该一直
--     有效，写一个默认到期时间等于替管理员做了他从未做过的决定。
--   * 到期判断在服务层的目录（services/ai/credentials.py）：过期条目与 disabled
--     条目走同一条「不进生效目录」的路径，于是 /v1/models 自然不再列出它的模型，
--     路由也不会把它当候选。数据库只负责存事实，不建 CHECK（「未来时间」是写入
--     时刻的性质，不是行的不变量——今天合法的到期时间，明天自然就过期了）。
--   * 列类型与 CREATED_AT / UPDATED_AT 同族（TIMESTAMP）：同表的时间列保持同一种
--     精度与时区口径，比较时才不需要转换。
--
-- Idempotency:
--   Same EXECUTE IMMEDIATE pattern as V001-V027.  ALTER TABLE ADD 列吞 ORA-01430
--   （列已存在）/ ORA-00955（表已由更新的基线带上这一列）。因此本文件在
--   「V001 + V025 + V026 已应用」的存量库和「全新建库」上都能重复执行，且重跑
--   不改变任何已有对象。
--
-- This file is mirrored by db/models.py / V001 (fresh instances get the same
-- column from V001); run `python -m db.ddl_check` to verify they stay
-- in sync (offline, no DB access).
-- ============================================================================

-- ----------------------------------------------------------------------------
-- AI_CREDENTIALS.EXPIRES_AT: 这把 key 的到期时间（NULL = 永不过期）
--   V001 的全新建库路径已经在 CREATE TABLE 里带上同一列；这里只服务存量库。
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE 'ALTER TABLE ai_credentials ADD (expires_at TIMESTAMP)';
EXCEPTION
  -- -1430: 列已存在（重复执行）；-955: 兜底，与 V025/V026 同口径（对象由更新的
  -- 基线建成时也能收敛，不让迁移顺序问题变成重启失败）。
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-1430, -955) THEN RAISE; END IF;
END;
/
