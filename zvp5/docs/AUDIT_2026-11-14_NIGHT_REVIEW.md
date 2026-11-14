# 夜间代码审查与优化（2026-11-14 → 11-15）

> 范围：Agent 执行 / Agent 请求 / Agent Session 性能 / 义工管理关键 API 健康性。
> 证据链：两条只读侦察车道（Agent 栈、义工 API）+ 生产 Oracle ADB 实测 SQL 性能数据
> （只读查询 v$sql / dba_hist_sqlstat / 索引与表统计）+ oracle 排期裁定 + 两车道实施。
> 本地基线：4503 tests passed（venv 同步 requirements.txt 后）；收尾全量 4599 passed。
> push 仅触发 CI；生产部署仍走 Release 门。

## 一、生产 SQL 性能数据结论（实测）

采集方式：在站点机 `/www/zvp5/backend` 用其 venv + settings 直连生产 ADB
（schema=ADMIN），只读聚合查询；脚本不落地任何凭证。

1. **监控自耗占 DB 时间 ~95%，属设计核算内成本，本轮不动码。**
   - `db_monitor.collect_fast` 的 ASH CTE（`1r2yn72bpb730`）：AWR 窗口 8.6 天
     5112 次 / 4323s / 平均 845ms，全系统第一 SQL；挂在 120s 快档。
   - `collect_slow` 的 Top SQL（`8f4rn69qnyss6`）：平均 1678ms；表空间查询
     平均 33ms。合计与 DB_PERF_MAINTENANCE_DESIGN.md §4 核算的
     「≈26 分钟/天 ≈ 1.8% 占空比」一致。
   - 官方降本通道是抬 `PERF_ORACLE_INTERVAL_SECONDS` /
     `PERF_SLOW_INTERVAL_SECONDS` 环境变量，不改查询。
2. **Agent run 队列轮询已是健康状态，历史高计数是存量。**
   - 领队查询（`_claim_candidate_stmt`，`4x5gtv0ds3nfq`）AWR 累计 98,862 次，
     是 `IDLE_BACKOFF`（1s→5s→30s 空闲退避）上线前的存量。
   - 在线探针实测：95 秒内 claim 3 次（≈30s 稳态退避）、ASH 快照 1 次（120s）
     ——与设计一致，无需修复。
   - watchdog 僵尸收敛 UPDATE 每 30s 一次、rows 恒 0、9 个 buffer gets，
     按设计运行。
3. **认证路径此前已优化**（窄投影 `get_for_auth` 生效；全列 users 读仅剩
   少量读路径，AWR 2224 次 / 8.2s，非热点）。
4. **义工管理表 DB 侧健康**：索引齐备（`UQ_ACTIVITY_MEMBERS_DEDUP`、
   `IX_ACTIVITY_MEMBERS_*`、`UQ_USER_TIME_USER_OID`、`UQ_USERS_BUSINESS_ID`
   等），单查询毫秒级，无慢 SQL。健康性问题集中在写路径与校验（见下）。
5. **真正的乘数是 ADB 写事务成本**（文档口径 0.5–0.95s/次提交）：
   Agent 一个 3 轮 ×2 工具的 run ≈ 18 次写事务 ≈ 9–17s 纯提交延迟——这是
   「Agent Session 性能问题」的实体；义工侧则是「一次成员操作 ≈ 10 条 SQL、
   多步写各自提交」的事务健康问题。

## 二、发现与处置

### 已落地（本轮提交，18 项）

**Agent 栈（8 项）**

| # | 发现 | 处置 |
|---|---|---|
| A-H1 | 每个工具事件独立写事务（`_write_event` 逐条 commit），写放大主导 run 延迟：一轮 2-3 个工具多付 1-3s，且逐个占连接池槽位 | 本轮非提案事件在内存缓冲，随 `on_round` 的 `append_round` **同一事务**落库（`insert_many` 数组绑定）；事件写入套 `begin_nested()` savepoint，失败仅 warning 不拖垮消息；裸 executor（无 `on_round`）保留逐条写；`proposed` 行仍即时写；`created_at` 定格在工具完成时刻；`run_cache.append_event` 保持即时（前端亚秒刷新不受影响） |
| A-H2 | `hard_truncate` 每弹一条消息对剩余整段 `json.dumps` 两次，O(n²)：400 条历史 × 8KB 工具结果时一次降级可达 GB 级序列化，卡死单 worker 事件循环（心跳停摆会连带触发 watchdog） | 每条只序列化一次（`_item_chars`），滑动窗口维护区间和；判断式与 `estimate_tokens` 算术逐字等价（`Σlen(item)+2(n-1)+2`），输出与旧算法**字节相同**（300 组随机等价测试保障）；孤儿 tool 配对与 `len>1` 边界不变 |
| A-H3 | 压缩上游调用无 `wait_for`，无视 run 死线与取消：最坏 4 次/×2 尝试 ×120s httpx 超时占住并发信号量，破坏「单次 run 的 wall-clock 上限」契约 | run 死线贯通 `_maybe_compact → compact_history → _summarize_history`，只包住压缩那一次 `_call_upstream`（`wait_for(剩余)`，已过期则一次不发）；超时走既有重试→降级硬截断链路；手动压缩不传死线、行为不变 |
| A-M1 | GET 消息接口对同一会话行取两次（router 与 service 各 `require_row` 一次），1 秒轮询接口白白翻倍 | `list_messages(conversation=)` 接受已校验行，router 下传；归属→功能开关顺序不变 |
| A-M2 | `append_batch` 每条消息一次 INSERT（assistant+工具行），N+3 次跨洋往返 | 单条 array-bound INSERT（executemany）；`_allocate_seq` 锁、连续 seq、会话累加、单次 commit 语义不变 |
| A-M9 | 会话删除作废提案只扫 actor 名下**一页 500 条**，第 501 条起漏杀且仍可被确认——设计 P0-3 明令堵死的洞 | 下推单条 `UPDATE ... WHERE status='proposed' AND request_id IN (SELECT oid FROM ai_runs WHERE conversation_oid=:c)`（`fail_proposed_for_conversation`，返回 rowcount）；能力探测优先走下推，内存替身保留原回退 |
| A-L1 | `context_limit` 两处实现漂移：conversations 副本抄丢 `"*"` 通配规则，UI 分母与压缩真实水位可不一致 | 单一来源：完全委派 `services.agent.context.context_limit`；`CONTEXT_LIMIT_DEFAULT` 降级为同常量别名；`"*"`/裸名/运行期覆盖两处同口径 |
| A-L4 | `mcp_router._require_admin` 对缺 `per` 的用户 `KeyError` → 500 | `"admin" not in (user.get("per") or [])`，缺失/None 一律 403 |

**义工管理（10 项）**

| # | 发现 | 处置 |
|---|---|---|
| B-H1 | 活动删除两事务、锁序倒置（先删子后删父，与 v3 加成员「先锁父」相反）→ ORA-00060 死锁 / 半删除状态 / 500；v2 创建同样三段各自提交 | 删除/创建各并为**单事务**：先 `get_for_update` 锁父行 → 权限复核 → 删子 → 删父 → 审计同会话写（锁序与 v3 一致） |
| B-H2 | 计时记录改 `mode` 撞 `UQ_ACTIVITY_MEMBERS_DEDUP` → ORA-00001 → 500 | 定点捕获 `IntegrityError` 且确认为 ORA-00001 时转 409（友好中文文案）；其余完整性错误照常冒泡；冲突路径不重算不审计 |
| B-H3 | 批量加成员 / 删成员 / Excel 导入后从不刷新 `user_time`，缓存时数一直错到次日 01:00 全量重建 | 三路径接线：v3 批量加与导入在**提交后**按活动调度刷新；**删除路径显式刷新被删成员本人**（按活动刷只会刷到幸存者——评审关键纠偏点）；活动删除在行锁内先抓受影响成员集，提交后按人刷新（>500 交每日全量） |
| B-H4 | name(256)/place(256)/approver(24) 无长度校验 → ORA-12899 → 500，任何管理员一次输入就能触发 | `util/validation.py` 集中列宽（与 DDL 同源注释），超长 400 不截断，NUL 拒绝；覆盖 v2 创建/信息更新、v3 创建、Excel 导入 |
| B-H5 | `list_for_member` 等 6 处 IN 列表未分块 → 有效活动超 1000 必 ORA-01795 → 500 | 单语句 `OR(IN chunk1, IN chunk2...)` 分块（排序/分页/COUNT 留在 SQL 侧），含漏报的 `_list_raw_stmt`；`None`/`[]` 语义不变 |
| B-M6/M7 | 按 session_id 读/删成员记录完全不校验路径活动归属：换活动 ID 可读任意记录；删错记录且审计写错活动名 | GET/DELETE 先验 `target_member["activity"] == activity_id`（不符 404）；GET 本人可读、非本人走管理员闸；DELETE 保留管理员闸 + 删除后显式刷新被删成员 |
| B-M9 | 活动/成员状态流转「读-判-写」无锁：并发审核双过、后写覆盖先写、approver 与终态可不一致 | CAS：`UPDATE ... WHERE oid=:id AND status=:expected`，`rowcount==0` → 409，失败不重算不审计；同状态 400 与 approver 规则不变 |
| B-M10 | `UpdateUserRecord.duration` 接受 0/负数/NaN/Infinity，一条 NaN 毒化所有 SUM 下游 | `Field(gt=0, allow_inf_nan=False)` → 422，与 `NewActivityMember` 同口径 |
| B-M11 | 500 人即时重算闸门在拉全量成员**之后**才判：万人活动先运 10k 行过 ADB 再说「跳过」 | 先 `COUNT(DISTINCT member_oid)`（同一人多模式行，必须按人为单位）再决定：0 直接回、超限交每日全量、仅 1..500 才拉行 |
| B-L1 | 活动详情在 404 判定前先跑 `count_for_activity`，坏 id 白付一次跨洋往返 | 先判存在再 count |

### 明确搁置（今夜不做的原因，供后续排期）

| # | 搁置原因 |
|---|---|
| A-M3 额度每轮 3 次读写 → 1 次 | 额度是计费闸；内存扣减需覆盖同人 scheduled task 并发与异常恢复，不能由 `MAX_RUNS_PER_USER=1` 推断安全。**这是 Agent 性能的最大剩余单点** |
| A-M4 run_detail 增量下推 SQL | 增量会改变 cache-miss 下 proposals 组装语义，需先冻结全量/增量响应契约 |
| A-M5 管理员闸每轮记忆化 | 每次工具调用实查管理员是刻意安全节奏，不用性能理由弱化 |
| A-M6 run 行二次读复用 | 第二次读是写入前最后的取消/watchdog 闸，复用本轮缓存会扩大取消竞态窗口 |
| A-M7 会话删除原子性 | 批删上限（400 批）与跨表非原子需可恢复级联/统一事务设计 |
| A-M8 删除/提交 TOCTOU | 两端锁点不同（delete 无锁 vs submit 在 `_allocate_seq` 内锁），需先统一 conversation 锁契约 |
| A-M10 安全审核移出 202 路径 | 移到 claim 时会改变 422 与 failed-run 的用户可见契约 |
| A-L6 `on_round` 无护栏 | 需幂等重试/恢复协议，不是加 try/except；吞异常会把「已扣费无消息」变成静默数据丢失 |
| A-L2/L3/L5 run_cache 冷拷贝等 | 无今夜级可独立验证收益，保留下一轮 |
| B-M1 批量加成员 MERGE/executemany | 会改变 dedupe/overwrite/唯一冲突语义，需 Oracle 集成测试 |
| B-M2 单成员重算改单条聚合查询 | 属 repo 查询形状重构，需在 zvp5adb2 先验执行计划 |
| B-M3/M4/M5 组合索引与查询形状 | 索引需 DDL 纪律：先 zvp5adb2 应用验证再上 zvp5adb，今夜不跑 DDL |
| B-L6 附件孤儿（entity_type='activity'） | 涉及 original/thumb/OCR/file_events BLOB 生命周期与存量回填，需级联设计 |
| B-L8 大 OFFSET/page-size | 涉及 API 与前端分页契约，另做 keyset/上限设计 |
| 监控 ASH 查询 | 设计核算内成本；要降先抬 `PERF_ORACLE_INTERVAL_SECONDS`（120→300 即腰斩） |

### 核验为「非缺陷」

v2/v3 未挂装饰器的 handler（`get_activities_v2` / `get_activity_members_v2` /
`add_activity_member_v2` / `create_activity_v3` / `get_group_users_v2`）是
「全量 reversioning 到 /api/v1，v2/v3 退役」的**有意退役**，前端零调用。
侦察报告中的 M8（v3 create 不可达）据此修正，不构成缺陷。

## 三、验证记录

- 车道聚焦：Agent 车道 363 passed（事件批量 12 + 压缩死线 8 + context_limit
  单源 12 等）；义工车道 126 passed（访问控制 / 分块 / 校验 / 事务 / CAS /
  刷新）。
- 回归切片：`-k "ai_ or agent_"` 1953 passed。
- 收尾全量：`pytest -q` → **4599 passed, 0 failed**（基线 4503，新增 96 用例）。
- CI 同款门禁本地复刻：`ruff check --select F821 .` ✅；
  `ruff check --select F401 main.py app_setup.py routers services util db typings tasks` ✅；
  `compileall -q routers services util db typings tasks` ✅。

## 四、后续建议（优先级排序）

1. **A-M3（额度每轮 3→1 次 DB 操作）**：Agent run 时延的最大剩余单点，
   需先做计费安全论证 + 异常恢复协议。
2. **两枚组合索引**：`activity_members(activity_oid, member_status)` 与
   `(member_oid, member_status, activity_oid)`（时长聚合与按人/按活动页的
   主谓词），走 DDL 纪律先上 zvp5adb2 验证。
3. **A-M7/A-M8 会话删除原子化 + conversation 锁契约统一**：一次性把
   delete/submit 的锁点收敛成同一把锁。
4. 若要降监控占空比：`PERF_ORACLE_INTERVAL_SECONDS` 120→300，零代码风险。
