-- ============================================================================
-- V049: 消息的思考过程（reasoning_content）
--
--   在 V001~V048 已应用的实例上补齐：
--     AI_MESSAGES += REASONING CLOB   —— 上游返回的 reasoning_content
--
-- Background:
--
--   * 思考强度（AI_CONVERSATIONS.THINKING，见 V046）只是**请求侧**的档位；上游
--     真正「想了什么」在响应里以 `reasoning_content` 返回（OpenAI 兼容协议与
--     智谱 GLM 同名字段）。不落库这段内容，「显示思考过程」就只能在当前这一轮
--     的进程内存里存在——刷新页面即丢，run 详情永远看不到。
--
--   * 类型与 `content` 完全一致（CLOB）：思考过程可能比答案更长（长链推理常见
--     数千 token），VARCHAR2 的 4000 字节上限会在生产上静默截断或直接 ORA-12899。
--
--   * 可空、无默认值：NULL = 这一轮上游没返回思考过程（普通模型、关闭了思考、
--     或非 assistant 行），是真实事实而不是「未知」。tool / user 行恒为 NULL。
--
--   * 不参与回放：这段内容是给人看的，不塞回下一轮的 messages（多数上游不接受
--     回传 reasoning_content，且它会把缓存前缀撑大）。落库只为审计与展示。
--
-- Idempotency:
--   ALTER TABLE ADD 吞 ORA-01430（列已存在）；表不存在时吞 ORA-00942
--   （由更新的基线建齐）。
--
-- This file is mirrored by db/models.py / V001 (fresh instances get the same
-- column); run `python -m db.ddl_check` to verify models/V001 stay in sync
-- (offline, no DB access).
-- ============================================================================

BEGIN
  EXECUTE IMMEDIATE 'ALTER TABLE ai_messages ADD (reasoning CLOB)';
EXCEPTION
  -- -1430: 列已存在；-942: 表不存在（由更新的基线建成）。
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-1430, -942) THEN RAISE; END IF;
END;
/
