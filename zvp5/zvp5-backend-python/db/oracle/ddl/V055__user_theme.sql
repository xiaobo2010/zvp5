-- ============================================================================
-- V055__user_theme.sql
-- ----------------------------------------------------------------------------
-- 在 V001~V054 已应用的实例上给 USERS 补 THEME（用户主题偏好）。
--
-- 为什么要有这一列：
--   * 主题此前只存在于客户端 localStorage：换设备/清缓存/重装就丢，而「我选
--     的是深色还是标准版」是用户对自己的描述，不是设备属性。
--   * 加主题不该动 DDL：列只存主题 id 字符串（VARCHAR2(32)），合法性由
--     services/theme_registry.py 的注册表判定。这里刻意不做 CHECK / 外键 ——
--     否则每次加主题都要一条迁移，且历史 id 下线后既有行会变成非法数据。
--
-- 形状：
--   THEME VARCHAR2(32 CHAR) DEFAULT 'canary' NOT NULL
--   * 有默认值：存量账号在 ALTER 时一次补齐（Oracle 会回填默认值），读路径
--     因此永远拿得到值；'canary' 与注册表 DEFAULT_THEME 一致。
--   * 无索引：统计接口是整个表 GROUP BY（低频管理操作），单用户读写都按主键
--     OID 定位，加索引只会拖慢写路径。
--
-- 回滚：
--   ALTER TABLE users DROP COLUMN theme;   -- 只丢主题偏好，不影响认证/权限
--
-- 幂等性：ALTER TABLE ADD 吞 ORA-01430（列已存在 = 重跑），其它错误照常 RAISE。
--
-- 执行：本文件只入库，不会自动执行（应用启动不跑 DDL）。生产在发版窗口用
--   python misc/apply_ddl.py v055
-- 手工执行（--dry-run 只解析不连库）。
--
-- 前置：无（V001 的 USERS 已存在）。注意后端必须**先**上列再发新版本：新接口
-- 会读写这一列，列不存在时表现为 ORA-00904 → 500。
--
-- This file is mirrored by db/models.py / V001 (fresh instances get the same
-- column); run `python -m db.ddl_check` to verify models/V001 stay in sync
-- (offline, no DB access).
-- ============================================================================

BEGIN
  EXECUTE IMMEDIATE 'ALTER TABLE users ADD (theme VARCHAR2(32 CHAR) DEFAULT ''canary'' NOT NULL)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -1430 THEN RAISE; END IF;
END;
/
