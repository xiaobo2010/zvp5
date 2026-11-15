-- ============================================================================
-- V012__add_dual_auth_uses.sql
-- ----------------------------------------------------------------------------
-- Stage 2 双人授权: adds DUAL_AUTH_USES on an instance where V001 was already
-- applied.
--
--   * DUAL_AUTH_USES (oid, approver_oid, otp_counter, action, operator_oid,
--     created_at) + uq_dual_approver_counter UNIQUE (approver_oid, otp_counter)
--
-- Background: Web SSH / 全盘文件管理 / 数据库内容管理 这类高危操作要求操作者
-- 现场输入**另一位管理员**的 TOTP 动态码（util/dual_auth.py）。每成功一次就在
-- 这里落一行，(approver_oid, otp_counter) 才是真正的防重放裁决点：同一管理员
-- 同一个 60s 窗口的动态码只能批准一件事。
--
--   * 唯一约束是原子的 insert 冲突源（ORA-00001 -> 409），不是「先查后插」的
--     软判定——并发两个请求在同一窗口提交同一个码时，只有一行能写进去。
--   * otp_counter 存的是 TOTP 计数器（epoch // OTP_STEP），不是 6 位动态码本身：
--     同一个码字符串可能在不同窗口重复出现，计数器才是窗口身份。
--   * 故意不加外键：审批人/操作者账号之后都可能被删除，而这行是审计线索，
--     必须留下。同样没有 action 枚举的 CHECK，动作集合会随功能迭代扩展。
--   * created_at 用于事后按时间段清理（保留策略由运维决定，本迁移不建任务）。
--
-- Idempotency:
--   Same EXECUTE IMMEDIATE pattern as V001-V011.  CREATE TABLE swallows
--   ORA-00955 (name already used);  any other error is re-raised.  Re-running
--   against an instance that already has the table is a safe no-op.
--
-- This file is mirrored by db/models.py / V001 (fresh instances get the same
-- table from V001); run `python -m db.ddl_check` to verify they stay in sync
-- (offline, no DB access).  The unique constraint creates its own index.
-- ============================================================================

BEGIN
  EXECUTE IMMEDIATE '
CREATE TABLE dual_auth_uses (
  oid VARCHAR2(24 CHAR) NOT NULL,
  approver_oid VARCHAR2(24 CHAR) NOT NULL,
  otp_counter NUMBER(19) NOT NULL,
  action VARCHAR2(64 CHAR) NOT NULL,
  operator_oid VARCHAR2(24 CHAR) NOT NULL,
  created_at TIMESTAMP NOT NULL,
  PRIMARY KEY (oid),
  CONSTRAINT uq_dual_approver_counter UNIQUE (approver_oid, otp_counter)
)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -955 THEN RAISE; END IF;
END;
/
