-- ============================================================================
-- V046: agent 会话控制与合规审计扩字段
--
--   在 V001~V045 已应用的实例上补齐：
--     AI_CONVERSATIONS += THINKING              VARCHAR2(16)  —— low|medium|high，NULL=上游默认
--     AI_PROMPT_AUDIT  += ANSWER_TEXT           CLOB          —— 最终 assistant 文本
--                         TOOL_CALLS            CLOB          —— 本轮工具调用摘要（JSON 数组）
--                         REFUSED               NUMBER(1) DEFAULT 0 NOT NULL
--                         WARNED                NUMBER(1) DEFAULT 0 NOT NULL
--
-- Background:
--
--   * THINKING 是**会话级**开关，不是 run 级：同一次会话里中途换档会让「同一段
--     历史、不同档位」的缓存前缀整体失效，而那正是长会话的主要成本。NULL 表示
--     「不给上游附带任何字段」，不是「low」——上游默认档位只有它自己知道。
--
--   * AI_PROMPT_AUDIT 之前只留「问了什么」（PROMPT_TEXT）。出现安全事件时，
--     「他得到了什么回答」「模型在这轮调了哪些工具」与提问同等重要：只看提问
--     无法判断模型是否真的执行了被禁止的操作，也无法解释拒绝。
--
--   * TOOL_CALLS 只存**摘要**（[{name,args_summary,result_summary,status}]），
--     不存参数原文：原文在 AI_AGENT_EVENTS 与提案表里，正文再抄一份只是把暴露
--     面翻倍（与 AI_CALL_LOGS 不留正文同一条原则）。
--
--   * REFUSED / WARNED 是布尔标记（NUMBER(1)，与 BAN_RULES.ENABLED 同口径）：
--     拒绝与警告都要能从留存行本身筛出来，不依赖回查 AI_CALL_LOGS 的错误码。
--     默认 0 而不是 NULL：老行的事实是「没记录过」，但这一列要被 WHERE 直接筛，
--     三值逻辑只会让 `refused = 0` 漏掉老行。
--
--   * 两个 CLOB 都可空：拒绝路径没有回答正文，读工具一轮也可能没有任何工具。
--     空串与 NULL 在 Oracle 里同义，写入侧不必区分。
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
  EXECUTE IMMEDIATE 'ALTER TABLE ai_conversations ADD (thinking VARCHAR2(16 CHAR))';
EXCEPTION
  -- -1430: 列已存在；-942: 表不存在（由更新的基线建成）。
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-1430, -942) THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'ALTER TABLE ai_prompt_audit ADD (answer_text CLOB)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-1430, -942) THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'ALTER TABLE ai_prompt_audit ADD (tool_calls CLOB)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-1430, -942) THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'ALTER TABLE ai_prompt_audit ADD (refused NUMBER(1) DEFAULT 0 NOT NULL)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-1430, -942) THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'ALTER TABLE ai_prompt_audit ADD (warned NUMBER(1) DEFAULT 0 NOT NULL)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-1430, -942) THEN RAISE; END IF;
END;
/
