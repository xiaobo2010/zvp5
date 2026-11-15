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
| B-M2 单成员重算改单条聚合查询 | 属 repo 查询形状重构，需在 <ADB_TEST> 先验执行计划 |
| B-M3/M4/M5 组合索引与查询形状 | 索引需 DDL 纪律：先 <ADB_TEST> 应用验证再上 <ADB_PROD>，今夜不跑 DDL |
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
   主谓词），走 DDL 纪律先上 <ADB_TEST> 验证。
3. **A-M7/A-M8 会话删除原子化 + conversation 锁契约统一**：一次性把
   delete/submit 的锁点收敛成同一把锁。
4. 若要降监控占空比：`PERF_ORACLE_INTERVAL_SECONDS` 120→300，零代码风险。

## 五、第二轮：热 API 健康性拓宽（2026-11-15 续）

> 触发：v1 reversion 核验；把义工/群组/其他高频 API 的同一套健康性设计铺开。
> 证据：生产 `perf_endpoints`（近两周）+ nginx access log 逐条 + 本地路由快照。

### 5.1 v1 reversion 核验：成功

- `main.py` 的 `ROUTERS` 在 `169622e`（11-07 23:49）把 v2/v3 前缀全并入 `/api/v1`；仅留 `/api/v2/mcp` 兼容别名（`include_in_schema=False`，带 TODO）。
- 生产 `perf_endpoints`：所有 `/api/v2|v3/*` 末次调用停在 `11-07 22:43~23:09`，对应 `/api/v1/*` 自 `11-08 12:32` 起接管——与提交/部署完全对齐。
- `tests/test_route_snapshot.py` 4 项全绿（257 对象）。

### 5.2 顺带修复：指标标签回归（FastAPI 0.162 懒挂载）

`_IncludedRouter` 使 `scope["route"].path` 丢 include 前缀：真实 `/api/v1/users/123/time` 被记成 `/{user_id}/time`，导致 `perf_endpoints` 面板标签错误、不同前缀的同名模板撞车。`services/api_metrics.py` 改为从 `_effective_candidates[*].path_format` 还原完整模板（按 route 缓存 + 回退），新增 `test_include_router_prefix_is_preserved_in_recorded_template`。

### 5.3 已落地（本轮）

**群组管理（6 项）**

| # | 处置 |
|---|---|
| G-H1/H2/M5 | 新 `delete_cascade`：`FOR UPDATE` 锁父行 → 清 `group_indicators` → 解绑 `users.class_oid` → 删班，单事务一次 commit；v1/v3 共用。修掉「有指标/成员必 500」「半删除」「反向锁序」 |
| G-H3 | `GROUP_NAME_MAX_LENGTH=128` / `GROUP_DESCRIPTION_MAX_LENGTH=1000`，5 个写入口超长/NUL → 400（原 ORA-12899→500）|
| G-M1 | `/time` 同时接受 `search`（前端历史名）与 `query`，恢复班级时长搜索 |
| G-M3 | v3 删班改用 `compulsory_temporary_token`（长会话 token 不再能直接删班）|
| G-M4 | v1 `/user`、`/time` 分页下推（复用 `members_page` / `time_repo.list_by_class`）|
| G-L1 | 改 description 对不存在群组 404 |

**用户读路径（3 项）**

| # | 处置 |
|---|---|
| U-H1 | `GET /users` 空 query 不再拼 3 个 `REGEXP_LIKE` / 不 `json_serialize(past)`，退化为纯 `select + ORDER BY + OFFSET/FETCH + COUNT(*) OVER()` |
| U-M2 | 用户活动列表 `sort=_id` 改映射活动 oid（原映射成员行 oid，退化为 rowid 序）+ `NULLS LAST` |
| U-M3 | v2 列表 `query` 加 `max_length=64`（超长 → 422，原可全扫慢查询）|

**通知 / 日志（3 项）**

| # | 处置 |
|---|---|
| L-H1 | 日志列表去掉二次 `get_user_display_names` 查询，直接用 JOIN 的 `user_name` 派生显示名（每请求省 1 次 ADB 往返）|
| L-M2 | `type/level` 过滤 IN 列表分块（≤500/块，`OR(IN...)`），1500 值不再 ORA-01795→500 |
| N-M2 | 公告列表缓存 key 放开到 2MB（消除 256KB 静默失效悬崖）+ 修正 20s/120s 注释漂移 |

**Agent 栈（3 项）+ 义工（3 项）**（承接上一轮搁置）

| # | 处置 |
|---|---|
| A-M3(a) | `quota.consume(want_state=False)`：成功分支跳过回读 SELECT（失败分支保留建行判据；OCR 仍拿 used/limit）|
| A-M8 | `_allocate_seq` 校验 `FOR UPDATE` 命中行，会话已删 → not-found（关掉「删后仍插孤儿消息」）|
| A-L2/L3/L5 | run_cache 命中返回引用（不再逐条深拷贝）+ 真 LRU 淘汰 |
| B-M2 | 单成员重算改单条聚合（不再拉全表活动 + Python 求和）|
| B-M1 | 批量加成员 2N 条往返 → 2 条 executemany（顺带修 `existing+duration` 读改写丢更新）|
| B-L8 | 分页 `size` 语句层夹 200（Query 校验不动）+ `users_router` 活动列表 Python 侧 OFFSET 下推 |

### 5.4 PREPARE / 待决策（本轮不改码或需拍板）

| # | 事项 | 卡点 |
|---|---|---|
| B-M3/M4/M5 | `activity_members(activity_oid, member_status)`、`(member_oid, member_status, activity_oid)` 组合索引 | DDL 纪律：先 <ADB_TEST> `EXPLAIN` 验证再上；A 选择性差，可能需改 covering `(activity_oid, member_status, duration_hours)` |
| A-M3(b) | 额度 peek 收敛到每 run 首次（3→1 SQL）| 额度刚好耗尽时最多多花一轮上游费用（有界），需拍板 |
| A-M4 | run_detail 增量下推 | 需先冻结「缓存必须全量 + proposals 与窗口解耦」契约 |
| A-L6 | on_round 幂等重试 | 需确定性 oids + 具名终态 + 扣费顺序结论 |
| A-M7 | 会话删除统一事务 | 需可恢复状态机，不能一行改 |
| B-L6 | 活动附件孤儿级联 | BLOB 生命周期 + 软删/硬删产品决策 |
| G-M2 | 加/移成员 CAS（`set_class` 条件更新）| 需 409/400 契约，未做 |
| G-H4 | 多权限组成员只取 `permissions[0]` | 需先查现网 `groups.permissions` 长度 |
| N-M1 | `/admin` 公告列表无上限 | 需分页契约 + 改 `NOTIFICATIONS_API.md` |
| N-M3 | 通知 `?since_version=` 短路 | 省重复正文，向后兼容 |
| N-M4 | canary 前端已调 `/read`，主库无此功能 | 需先 V053 `notification_reads` + 产品决策；当前 405/404 属发布错位 |
| L-M1/M3 | 日志 `COUNT(*) OVER()` 全量窗口 + 深 OFFSET | 建议只第 1 页算总数 / keyset |
| 监控 ASH | 抬 `PERF_ORACLE_INTERVAL_SECONDS` 120→300 | 生产 env 变更，零代码风险，需运维窗口 |
| U-M5/M6/U-L1 | `/time` 未命中不回写、time_statistics 多往返、管理写路径无 CAS | 无热点证据 / 非高频目标 |

### 5.5 验证（本轮，统一门禁）

- 全量 `pytest -q` → **4676 passed, 1 warning**（exit 0）。
- `ruff check --select F821 .` → All checks passed；`ruff check --select F401 main.py app_setup.py routers services util db typings tasks` → All checks passed。
- `compileall -q routers services util db typings tasks` → exit 0。
- `python -m db.ddl_check` → `OK 48 tables, 62 indexes - no drift`。

### 5.6 生产实测结论（本轮取证）

- `GET /api/v3/notifications`、`GET /api/logs` 近两周 nginx 逐条统计 **零 5xx**；错误率 = 401 token 抖动 + 499 客户端断连 + 418 盾牌 + canary 前端 `/read` 405（发布错位）。
- `POST /api/users/auth` 均延迟 232ms ≈ 单次 bcrypt（cost 12，本机实测 256ms/次）；37% 错误率是 401/403/429 正常拒绝被 `err_count>=400` 一并计入，非 5xx。
- `GET /api/users` 空 query 曾对 `past` CLOB 逐行 `json_serialize`（已修）；日志 `type/level` 无上限曾可 ORA-01795（已修）。
