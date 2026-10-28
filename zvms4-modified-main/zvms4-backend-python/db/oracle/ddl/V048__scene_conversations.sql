-- ============================================================================
-- V048: 只读场景会话（终端 / 文件编辑）
--
--   在 V001~V047 已应用的实例上补齐：
--     AI_CONVERSATIONS += SCENE VARCHAR2(16)   —— terminal | file_edit | NULL
--
-- Background:
--
--   * 终端与文件编辑这两个场景共用会话 + run 管线，但不该出现在普通的 ZAiChat
--     会话列表里：`SCENE` 非空即「场景会话」，列表查询固定过滤 `SCENE IS NULL`。
--     用一列而不用单独一张表，是因为消息、run、提案、摘要水位、级联删除全都挂在
--     AI_CONVERSATIONS 上；另起一张表等于把那条链路复制一遍。
--
--   * 取值只有 `terminal` / `file_edit`（VARCHAR2(16)，与 THINKING 同宽同风格）。
--     NULL 是「普通会话」这个真实事实，不是「未知」——所以不加 DEFAULT。
--     不加 CHECK：存量实例上补 CHECK 需要先扫全表，而写入侧只有
--     `services.ai.conversations.SCENES` 一个来源（越界在建会话前就被拒）。
--
--   * 场景会话是**只读**的：执行器按 `SCENE` 把工具声明裁到 `kind='read'`，写工具、
--     提案流、ssh_exec、数据库 DML/DDL 都不在 tools 数组里。这不是靠提示词约束。
--
--   * 保留窗口 14 天（比普通会话激进）：这类会话是「一次性排障记录」，内容还包含
--     终端/文件片段，留久了只是把它们堆在库里。清理走既有会话级联删除
--     （AI_MESSAGES → AI_RUNS → AI_CONVERSATIONS），接在 retention 作业里，
--     键名 `scene_conversations`。
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
  EXECUTE IMMEDIATE 'ALTER TABLE ai_conversations ADD (scene VARCHAR2(16 CHAR))';
EXCEPTION
  -- -1430: 列已存在；-942: 表不存在（由更新的基线建成）。
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-1430, -942) THEN RAISE; END IF;
END;
/
