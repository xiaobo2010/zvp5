-- ============================================================================
-- V022__harden_login_failure_rule.sql
-- ----------------------------------------------------------------------------
-- 把内置「登录失败暴破」规则从 ban 降为 alert。
--
-- Why:
--   `APP_LOGS` 的 login_fail 行只按受害者 oid 归因，没有可信的来源 IP /
--   设备维度。攻击者用公开可查的学号连续提交错误密码，就能把 10 条失败记录
--   写到受害者名下，触发 1 小时阈值并封掉任意学生账号——不需要密码、token
--   或设备。默认只告警；管理员确认来源可信后仍可显式把 mode 改回 ban。
--
-- Scope:
--   Only the builtin row (`code='login_failure_burst' AND builtin=1`). Custom
--   rules and the `ban_evasion_retry` rule are untouched.
--
-- Idempotency:
--   REGEXP_REPLACE + WHERE only matches `"mode":"ban"`; rerunning is a no-op.
-- ============================================================================

UPDATE ban_rules
SET config = REGEXP_REPLACE(
        config,
        '"mode"[[:space:]]*:[[:space:]]*"ban"',
        '"mode":"alert"'
    ),
    updated_at = SYSTIMESTAMP
WHERE code = 'login_failure_burst'
  AND builtin = 1
  AND REGEXP_LIKE(config, '"mode"[[:space:]]*:[[:space:]]*"ban"');

COMMIT;
