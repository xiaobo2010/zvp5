-- ============================================================================
-- V051__unify_af_quota.sql
-- ----------------------------------------------------------------------------
-- 用户侧额度从「chat / ocr 两条平行账」收敛成一个 AF 池：
--
--   * AI_QUOTA.KIND：'chat' 与 'ocr' 归并成 'af'。
--     站点级 'ocr_<engine>'（平台出资）**保持不动** —— 用户池与平台池是两个
--     出资方，混在一起会让「某个引擎打满」变成「所有人的口粮被扣」。
--   * 折算：af.used = chat.used + ocr.used * 1（AI_OCR_AF_COST = 1：
--     一次识别 ≈ 一次 1k token 的对话）。
--   * ROLE_QUOTAS.KIND：'chat' → 'af'，'ocr' 行删除（角色表里已经没有「OCR
--     单列额度」这个口径；af 的上限沿用原 chat 的值，保住「这个角色能聊多少」
--     的量级意图）。
--
-- 为什么必须一次迁移做完：CHECK 是库上的最后一道闸。
--   * 只改代码不改 CK：新写入的 'af' 行会被 ck_ai_quota_kind 拒掉（ORA-02290）。
--   * 只改 CK 不归并数据：收紧后的 CK 会因为存量 'chat'/'ocr' 行加不上
--     （ORA-02293）。
-- 所以顺序是「放宽 → 归并 → 收紧」，外加一张备份表兜住「归并算错了」的风险。
--
-- 为什么归并要覆盖**所有月份**而不只是当月：段三的收紧 CK 对**任何** period
-- 的存量行都生效。只归并当月，上个月的 'chat'/'ocr' 行会让段三直接失败；而
-- 删掉历史行又等于抹掉「上个月这个人用了多少」这个已发生的事实。ai_quota 的
-- 行数是「用户 × 月份 × 3」，量级很小，一次性全量归并不构成迁移负担。
--
-- 幂等性：
--   * 段零 CREATE TABLE ... AS SELECT 吞 ORA-00955。**重跑不覆盖备份**：第二次
--     执行时表里已经是第一次的原始快照，覆盖反而会把「出事前的样子」擦掉。
--   * 段一/段三/段四的 DROP CONSTRAINT 吞 ORA-02443（约束不存在）、
--     ADD CONSTRAINT 吞 ORA-02264（约束名已占用）、段零吞 ORA-00955。
--   * 段二是「目标态已达成则 no-op」：它按 (user_oid, period) 找 'chat'/'ocr'
--     源行分组，归并后源行已删，重跑时没有任何分组可迭代。
--   * 归并段兜 ORA-00942（表还不存在）：与 V026 / V050 同口径，不让迁移顺序
--     问题变成启动失败。
--
-- 回滚（务实版，需要停机窗口）：
--   1) ALTER TABLE ai_quota DROP CONSTRAINT ck_ai_quota_kind;
--   2) DELETE FROM ai_quota WHERE kind = 'af';
--   3) INSERT INTO ai_quota SELECT * FROM ai_quota_bak_v051;
--   4) 重新加上 V026 的旧 CK：
--      kind IN ('ocr','chat') OR kind LIKE 'ocr\_%' ESCAPE '\'
--   ROLE_QUOTAS 没有备份表（只有 3×3 个格子）。回滚时按「角色策略」页的历史
--   快照手工补，或者干脆保持空表——缺行 = 回落代码默认值。
--
-- 注意：本文件不写任何 INSERT 种子数据，也不碰 AI_QUOTA_REQUESTS（申请表保留
-- chat/ocr 两个申请口径，审批时映射到 af，见 services/ai/quota_requests.py）。
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 段零：备份
--   CTAS 不复制约束/索引（本来也不需要：它是只读的还原材料）。含当前已用量
--   与上限的完整列，恢复时可直接 INSERT 回 ai_quota。
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE 'CREATE TABLE ai_quota_bak_v051 AS SELECT * FROM ai_quota';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -955 THEN RAISE; END IF;
END;
/

-- ----------------------------------------------------------------------------
-- 段一：放宽 AI_QUOTA 的 CK（让 'af' 能写进去）
--   DROP 与 ADD 必须分两块：同一块里 DROP 之后 ADD 失败会连同 DROP 一起回滚，
--   重跑时旧约束仍在——分块后每一步都能独立收敛（与 V026 同口径）。
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE 'ALTER TABLE ai_quota DROP CONSTRAINT ck_ai_quota_kind';
EXCEPTION
  -- -2443: 约束不存在（已是最新基线，或上一次执行已删）；-942: 表尚未创建。
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-2443, -955, -942) THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'ALTER TABLE ai_quota ADD CONSTRAINT ck_ai_quota_kind CHECK (kind IN (''af'',''ocr'',''chat'') OR kind LIKE ''ocr\_%'' ESCAPE ''\'')';
EXCEPTION
  -- -2264: 约束名已被占用（重跑）；-955/-942: 对象/表不存在。
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-2264, -955, -942) THEN RAISE; END IF;
END;
/

-- ----------------------------------------------------------------------------
-- 段二：数据归并（按 user_oid + period，覆盖所有月份）
--   * 上限：优先 chat 行（对话额度是主口径）；没有 chat 行才用 ocr 行的。
--   * 已用量：chat.used + ocr.used × 1。
--   * 已有 af 行（理论上不存在，重跑/手工造数才会遇到）→ 改成累加 used，
--     上限保持 af 行自己的值，不去覆盖别人后来的设置。
--   * oid 必须是 24 位小写 hex（ck_ai_quota_oid），SYS_GUID 是 32 位，截断。
--   * 循环里 DELETE 自己正在扫的表是安全的：隐式游标带 GROUP BY，结果集在取第一
--     行前就已算完（不会被后续 DELETE 改写），而且整段没有 COMMIT——不存在
--     「游标被提交失效（ORA-01002）」的窗口。
-- ----------------------------------------------------------------------------
DECLARE
  v_used  NUMBER;
  v_limit NUMBER;
BEGIN
  FOR r IN (
    SELECT user_oid, period
      FROM ai_quota
     WHERE kind IN ('chat','ocr')
     GROUP BY user_oid, period
  ) LOOP
    BEGIN
      SELECT limit_count
        INTO v_limit
        FROM ai_quota
       WHERE user_oid = r.user_oid
         AND period = r.period
         AND kind = 'chat';
    EXCEPTION
      WHEN NO_DATA_FOUND THEN
        BEGIN
          SELECT limit_count
            INTO v_limit
            FROM ai_quota
           WHERE user_oid = r.user_oid
             AND period = r.period
             AND kind = 'ocr';
        EXCEPTION
          WHEN NO_DATA_FOUND THEN v_limit := 0;
        END;
    END;

    SELECT NVL(SUM(CASE WHEN kind = 'chat' THEN used ELSE used * 1 END), 0)
      INTO v_used
      FROM ai_quota
     WHERE user_oid = r.user_oid
       AND period = r.period
       AND kind IN ('chat','ocr');

    BEGIN
      INSERT INTO ai_quota (oid, user_oid, period, kind, used, limit_count, updated_at)
      VALUES (
        SUBSTR(LOWER(RAWTOHEX(SYS_GUID())), 1, 24),
        r.user_oid,
        r.period,
        'af',
        v_used,
        v_limit,
        SYSTIMESTAMP
      );
    EXCEPTION
      WHEN DUP_VAL_ON_INDEX THEN
        UPDATE ai_quota
           SET used = used + v_used,
               updated_at = SYSTIMESTAMP
         WHERE user_oid = r.user_oid
           AND period = r.period
           AND kind = 'af';
    END;

    DELETE FROM ai_quota
     WHERE user_oid = r.user_oid
       AND period = r.period
       AND kind IN ('chat','ocr');
  END LOOP;
EXCEPTION
  -- -942: 表还不存在（迁移顺序问题），交给后续基线收敛。
  WHEN OTHERS THEN
    IF SQLCODE != -942 THEN RAISE; END IF;
END;
/

-- ----------------------------------------------------------------------------
-- 段三：收紧 AI_QUOTA 的 CK
--   最终态：kind='af' 或站点级 'ocr_<engine>'。历史 kind 已在上一步归并干净，
--   这里的 ADD 若报 ORA-02293（存量行仍违反），说明段二没跑成功——继续抛，
--   不要用「吞异常」把它变成一个看起来成功的半成品迁移。
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE 'ALTER TABLE ai_quota DROP CONSTRAINT ck_ai_quota_kind';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-2443, -955, -942) THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'ALTER TABLE ai_quota ADD CONSTRAINT ck_ai_quota_kind CHECK (kind = ''af'' OR kind LIKE ''ocr\_%'' ESCAPE ''\'')';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-2264, -955, -942) THEN RAISE; END IF;
END;
/

-- ----------------------------------------------------------------------------
-- 段四：ROLE_QUOTAS 同步
--   顺序不能是「先 UPDATE 再改 CK」：旧 CK 只允许 chat/ocr/schedule，把行改成
--   'af' 会被它自己拒掉（ORA-02290）。所以同样走「放宽 → 迁数据 → 收紧」。
--   一个角色同时有 chat 与 ocr 行时：chat → af（保住对话额度），ocr 行删除。
-- ----------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE 'ALTER TABLE role_quotas DROP CONSTRAINT ck_role_quotas_kind';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-2443, -955, -942) THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'ALTER TABLE role_quotas ADD CONSTRAINT ck_role_quotas_kind CHECK (kind IN (''af'',''chat'',''ocr'',''schedule''))';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-2264, -955, -942) THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'UPDATE role_quotas SET kind = ''af'' WHERE kind = ''chat''';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -942 THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'DELETE FROM role_quotas WHERE kind = ''ocr''';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -942 THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'ALTER TABLE role_quotas DROP CONSTRAINT ck_role_quotas_kind';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-2443, -955, -942) THEN RAISE; END IF;
END;
/

BEGIN
  EXECUTE IMMEDIATE 'ALTER TABLE role_quotas ADD CONSTRAINT ck_role_quotas_kind CHECK (kind IN (''af'',''schedule''))';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE NOT IN (-2264, -955, -942) THEN RAISE; END IF;
END;
/
