-- ============================================================================
-- V027__extend_ai_ocr_engines_kind.sql
-- ----------------------------------------------------------------------------
-- 把 AI_OCR_ENGINES.KIND 的 CHECK 从 ('tencent','aliyun','rapidocr') 扩到
-- ('tencent','aliyun','rapidocr','baidu','groq')。
--
-- Background:
--   * 新增两个云端引擎适配器：
--       - baidu  —— 百度智能云 OCR（api_key + secret_key 换 access_token）
--       - groq   —— Groq 视觉模型（OpenAI 兼容，单 api_key）
--     KIND 是引擎类型白名单，不是数据；旧集合会在 INSERT 时以 ORA-02290
--     拒绝这两类引擎，等于「代码支持了但库里存不下」。
--   * 选择「先查 USER_CONSTRAINTS 再决定是否重建」，而不是无条件 DROP+ADD：
--     无条件重建在重复执行时会短暂摘掉约束（窗口内可写入非法 kind），而且
--     每次重跑都会变更数据字典、让约束的 LAST_CHANGE 抖动。先读
--     SEARCH_CONDITION 判断当前是否已是新集合，已是则整段 no-op。
--   * 比较用 INSTR(LOWER(condition), '''baidu''')：Oracle 把 CHECK 表达式
--     规范化后存进 SEARCH_CONDITION（可能重排空白/大小写），但字面量
--     'baidu'/'groq' 一定原样保留，够用来判「已经扩过」。
--
-- Idempotency:
--   与 V026 同风格：
--     * 约束不存在（全新库由 V001 直接建新集合 / 表还没建）→ NO_DATA_FOUND，
--       整段跳过，不报错；
--     * 已是新集合 → 跳过，不产生任何 DDL；
--     * DROP CONSTRAINT 吞 ORA-02443（约束不存在）/ ORA-00942（表不存在）/
--       ORA-00955（兜底）；
--     * ADD CONSTRAINT 吞 ORA-02264（约束名已占用）/ ORA-00942 / ORA-00955。
--   DROP 与 ADD 分成两个内层块：同一块里 DROP 之后 ADD 失败会连同 DROP 一起
--   回滚，重跑时旧约束仍在；分开后每一步都能独立收敛。
--
-- This file is mirrored by db/models.py / V001 (fresh instances get the new
-- set directly from V001); run `python -m db.ddl_check` to verify models vs
-- V001 stay in sync (offline, no DB access).
-- ============================================================================

DECLARE
  v_condition VARCHAR2(4000);
  v_needs_change BOOLEAN := FALSE;
BEGIN
  --------------------------------------------------------------------------
  -- 读当前约束的表达式；约束或表不存在 = 没有什么要改的
  -- --------------------------------------------------------------------------
  BEGIN
    SELECT search_condition
      INTO v_condition
      FROM user_constraints
     WHERE table_name = 'AI_OCR_ENGINES'
       AND constraint_name = 'CK_AI_OCR_ENGINES_KIND'
       AND constraint_type = 'C';
  EXCEPTION
    -- 约束不存在（V001 全新建库已带新集合、或表尚未创建）→ 跳过整段。
    WHEN NO_DATA_FOUND THEN
      v_condition := NULL;
    -- 表还没建的极端顺序问题：同样跳过，由 V001 / V026 负责建表。
    WHEN OTHERS THEN
      IF SQLCODE NOT IN (-942, -955) THEN RAISE; END IF;
      v_condition := NULL;
  END;

  IF v_condition IS NOT NULL THEN
    v_needs_change :=
      INSTR(LOWER(v_condition), '''baidu''') = 0
      OR INSTR(LOWER(v_condition), '''groq''') = 0;

    IF v_needs_change THEN
      ----------------------------------------------------------------------
      -- 先 DROP：旧集合挡住新增 kind
      -- ----------------------------------------------------------------------
      BEGIN
        EXECUTE IMMEDIATE
          'ALTER TABLE ai_ocr_engines DROP CONSTRAINT ck_ai_ocr_engines_kind';
      EXCEPTION
        -- -2443: 约束不存在（重跑/已被删）；-942: 表不存在；-955: 兜底。
        WHEN OTHERS THEN
          IF SQLCODE NOT IN (-2443, -942, -955) THEN RAISE; END IF;
      END;

      ----------------------------------------------------------------------
      -- 再 ADD：新集合与 db/models.py / V001 逐字一致
      -- ----------------------------------------------------------------------
      BEGIN
        EXECUTE IMMEDIATE '
ALTER TABLE ai_ocr_engines ADD CONSTRAINT ck_ai_ocr_engines_kind
  CHECK (kind IN (''tencent'',''aliyun'',''rapidocr'',''baidu'',''groq''))';
      EXCEPTION
        -- -2264: 约束名已被占用（重跑）；-942: 表不存在；-955: 兜底。
        WHEN OTHERS THEN
          IF SQLCODE NOT IN (-2264, -942, -955) THEN RAISE; END IF;
      END;
    END IF;
  END IF;
END;
/
