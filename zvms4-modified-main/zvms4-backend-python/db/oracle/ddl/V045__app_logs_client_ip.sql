-- ============================================================================
-- V045: APP_LOGS 补客户端来源（CLIENT_IP / USER_AGENT）
--
--   在 V001~V044 已应用的实例上补齐两列：
--     * CLIENT_IP    VARCHAR2(45 CHAR)  —— IPv6 文本最长形式按 45 取宽
--     * USER_AGENT   VARCHAR2(256 CHAR) —— 浏览器/客户端自述，超长在写入侧截断
--
-- Background:
--
--   * 此前全库没有任何 IP/UA 记录：事后要知道「这条告警是哪台机器发出来的」
--     只能翻 nginx access log，而 access log 的保留期与 APP_LOGS 不一致。
--
--   * 生产在 nginx 之后，uvicorn 只听 127.0.0.1:8018，所以真实来源只能来自
--     X-Forwarded-For（见 typings/log.py::client_ip_from）。
--
--   * 两列都可空、无默认值：历史行没有这个事实就是 NULL，编一个占位值等于
--     伪造证据。写入侧取不到时落空串（Oracle 空串即 NULL，语义相同）。
--
--   * 不建索引：当前没有「按 IP 检索日志」的查询，加索引只会拖慢写路径与
--     保留期批量删除。将来真有需求再加。
--
-- Idempotency:
--   ALTER TABLE ADD 吞 ORA-01430（列已存在）；表不存在时吞 ORA-00942
--   （由更新的基线建齐）。
--
-- This file is mirrored by db/models.py / V001 (fresh instances get the same
-- columns); run `python -m db.ddl_check` to verify models/V001 stay in sync
-- (offline, no DB access).
-- ============================================================================

BEGIN
  EXECUTE IMMEDIATE 'ALTER TABLE app_logs ADD (client_ip VARCHAR2(45 CHAR))';
EXCEPTION
  -- -1430: 列已存在；-942: 表不存在（由更新的基线建成）。
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-1430, -942) THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'ALTER TABLE app_logs ADD (user_agent VARCHAR2(256 CHAR))';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-1430, -942) THEN RAISE; END IF;
END;
/
