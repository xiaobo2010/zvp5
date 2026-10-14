# ADB 迁移计划（MongoDB → Oracle Autonomous Database）

> 分支：`zvms4-adb-migration`（基于 `zvp5-dev`）
> 状态：进行中 · 阶段 P0（代码与 Schema，不含实库 I/O）

## 1. 目标与范围

将 `zvms4-backend-python` 的持久化层从 MongoDB（motor）迁移到 **Oracle Autonomous Database (ADB)**。

**已锁定决策**（2026-10-12，用户确认）：

| 项 | 决策 |
|---|---|
| 迁移策略 | 分层渐进：保留 MongoDB 可用，新增独立 SQL 数据访问层，按模块纵切迁移，逐模块切换 |
| Schema 范围 | **只承载 `zvms_new` 结构**；遗留库 `zvms` 逐步 phase out |
| 目标实例 | `zvp5adb`（生产）与 `zvp5adb2`（测试），结构平行，仅连接串/wallet 不同 |
| 技术栈 | SQLAlchemy 2.0 async + python-oracledb（thin mode） |
| 本轮边界 | 只写代码与 Schema，**不连实库、不执行 ETL** |
| 首个纵切 | 用户 + 认证（users / auth） |

## 2. 现状（侦察结论）

- 后端 = FastAPI + motor/MongoDB，两库并存：`zvms`(遗留) + `zvms_new`(现役)。
- 约 30 个文件直连库（15 routers + 9 util + 3 tasks + conversion + main），**29 处聚合管道**（`$lookup`/`$unwind`/`$group`/`$substrCP`）、ObjectId 关联、内嵌数组、`$regex`/`$elemMatch`。
- 无索引、无事务、无 `bulk_write`；ObjectId 生成/解析散落多处（`util/object_id.py`、`utils.py` 重复实现）。
- 权威库结构见 `DB_ALIGNMENT.md`。

### `zvms_new` 集合清单（迁移目标）

| 集合 | 文档数 | 备注 |
|---|---|---|
| users | 1781 | `roles[]` / `class_id` / `session_id` |
| groups | 37 | 仅 `name` |
| activities | 380 | 扁平 |
| activity_members | 13076 | `member`/`activity` 为字符串引用 |
| time | 1785 | |
| time_academic_year | 1780 | 含连字符字段名 |
| time_with_origin | 1807 | `data` 为对象 |
| time_with_origin_stat | 154 | `data` 为对象 |
| group_indicators | 55 | `percentiles`/`indicators` |
| indicators | 1 | 同上 |
| daily_exports | 4 | `data[]` |
| logs | 6792 | `net_session`/`request`/`user`/`timestamp` 对象 |

## 3. ADB 环境

- 实例：`zvp5adb`、`zvp5adb2`（OCI，`adb.ap-chuncheon-1.oraclecloud.com:1522`）。
- Wallet：`~/.oci/adb-wallet-zvp5`、`~/.oci/adb-wallet-zvp5adb2`。
- 管理员凭证：`~/.config/opencode/credentials/adb-zvp5-admin`（不入库、不入代码）。
- 驱动：`oracledb` 已装入后端 venv（thin mode）。
- **连通性（已解，2026-10-13）**：本机 DNS 走 fake-ip 代理（`198.18.0.110`），本机直连实库不可用；改由 `zgovps-la`（正常 DNS/TLS）实测打通。根因：thin 模式**必须同时传 `config_dir` 与 `wallet_location`** 才加载 mTLS 客户端证书，仅传 `config_dir` 报 `ORA-12506`；`db/oracle/engine.py` 本就是两者都传。
- Wallet 口令口径：prod wallet（`adb-wallet-zvp5`）用 `~/.config/opencode/credentials/adb-zvp5-admin`；test wallet（`adb-wallet-zvp5adb2`）用 `~/.oci/adb-zvp5adb2-admin.pass`。`~/.oci/adb-zvp5-admin` 为**同名但非匹配**文件，勿用。

## 4. 执行计划（分阶段）

- **P0（本轮）**：ADB Schema 设计 + 数据访问层脚手架（连接/配置/会话），无实库 I/O。
- **P1**：DDL 落地 + SQLAlchemy 模型 + 仓储层；本地无库条件下用编译/导入/单测验证。
- **P2**：首个纵切（用户 + 认证）迁移，Mongo/Oracle 按模块开关切换。
- **P3**：其余模块逐一切换；`zvms` 遗留路径退役。
- **P4**：Mongo → ADB 单向 ETL（幂等 upsert）。

## 5. 关键技术约束（研究结论，已核）

- **thin mode wallet**：只认 `ewallet.pem`，**不支持 `cwallet.sso`**（thick 专属）。我们的 PEM 是加密的，必须显式传 `wallet_password`（OCI wallet 口令，≠ 数据库用户口令），否则驱动回退交互式 "Enter PEM pass phrase" 并挂起 —— 即本机先前遇到的挂起根因之一。
- **JSON 列**：SQLAlchemy **2.0 无 Oracle JSON DDL 支持**（`Column(JSON)` 编译报错）。栈锁定 2.0.x，故 JSON 列统一走 `CLOB + IS JSON` 检查约束（`daily_exports.data`、`time_with_origin.data`、`indicators.*`、`logs.*` 等）。
- **ObjectId**：建议 `RAW(12)` + `TypeDecorator`（API 层仍呈现 hex 字符串）；`VARCHAR2(24)` 为可读备选。
- **时间**：BSON Date → `TIMESTAMP WITH TIME ZONE`（避免 `DATE`，精度/时区不足）。
- **分页**：`.offset().limit()` → Oracle `OFFSET/FETCH`；必须带唯一 `ORDER BY`（末位加 PK 兜底）保证稳定。
- **异步/池**：oracledb thin 支持 asyncio；pool `pool_pre_ping=True` + `pool_recycle=3600`；Python 3.14 需 SQLAlchemy ≥2.0.48（greenlet 3.14 wheel）。
- **ADB DDL**：应用用户需 `QUOTA`，否则 `create_all` 报 `ORA-01950`；标识符 ≤128。

## 6. 相关文档 / 产物

- **`docs/ADB_SCHEMA_SPEC.md`** —— 表结构、重命名映射、DAL 分层、DDL 交付、ETL、风险（实现唯一权威）。
- `db/oracle/` —— 连接/配置/会话脚手架（含 thin-mode wallet 与 prod/test 实例解析）。
- `db/oracle/ddl/V001__initial_schema.sql` + `db/models.py` + `db/ids.py` + `db/ddl_check.py` —— Schema 核心（待落地）。
- `db/repos/protocols.py` + `db/backends.py` + `settings.ORACLE_MODULES` —— DAL 分层与开关（待落地）。

## 6.1 进度与验证基线（P2）

- **P1 完成**：`V001` DDL + `db/models.py`(Core) + `db/ids.py` + `db/ddl_check.py`；离线漂移校验实测有效（篡改 DDL → FAIL/exit 1，还原 → exit 0）。
- **P2 完成（首个纵切 users+groups+auth）**：
  - 仓储：`db/repos/mongo/{users,groups}.py`（现状查询逐字搬运）+ `db/repos/oracle/{users,groups}.py`（Core，返回 Mongo 形状 dict）。
  - 工厂：`db/backends.py::get_users_repo/get_groups_repo`（惰性导入，import 期零 I/O）。
  - 接线：`util/cert.py`、`util/user.py`、`util/permission/user.py`、`routers/users_{router,v2,v3}`、`routers/groups_{v2,v3}_router.py`（仅替换 `zvms_new.*` 访问；遗留 `zvms.*` 与双库合并逻辑原样保留）。
  - 验证：flag 关闭 `smoke.py` = **TOTAL 34 / PASS 34 / FAIL 0**（连跑两次一致）；flag-on 结构检查通过（工厂返回 Oracle 仓储、未创建引擎）。
  - 变更面：后端 10 个 tracked 文件（+104/−202）+ 新增 `db/`、`docs/`。
- **已知缺口（flag-on 前须闭合）**：
  1. `groups_v3_router.delete_group_v3` 的 `update_many({"class_id": oid})` 批量摘除仍走原生 Mongo（`UsersRepo` 无批量方法）→ 需补 bulk 方法。
  2. `users_router.py` 双库合并的 5 处 `zvms_new.users` 读取仍在 Mongo（双库语义，待 phase-out）。
  3. `activities/activity_members/time/stats/logs` 域未迁移（后续 slice）。
  4. `groups_v2_router.get_groups_v2` 以「count→全量取」规避 `list_classes` 分页（行为等价，可后续优化）。
- **flag 状态**：仍为关闭（`ORACLE_MODULES=[]`），未对 ADB 执行任何 DDL/连接。
- **flag-on 实库验证**：待 ADB 连通性解决后，对 `zvp5adb2` 执行 `V001` 并跑 `ORACLE_MODULES=["users","groups"]` 的等价 smoke。

## 6.2 仓储层进度（activities / members）

- **已就绪（未接线）**：`db/repos/{mongo,oracle}/activities.py`、`db/repos/{mongo,oracle}/members.py`；`UsersRepo.detach_class()` 已补并接线 `delete_group_v3`。
- **接线前必须处理的分叉点（否则接线即改行为）**：
  1. **activities 列表**：现有 v1/v2/v3 三处列表**都只按 `name` 过滤**（无 description）；v2 用 `type $in [多类型]`（非单值）；v3 额外 `$lookup` 出 `creator_name`。故通用 `ActivitiesRepo.list_page(query, atypes, status, page, size)` 只忠实于 v2 的过滤核心，**v3 的 `creator_name` 需在路由侧补**。
  2. **members 列表**：v3 `list_for_activity` 聚合含 `member_name`（本项目曾保留该字段）；仓储按 6 键契约（`_id/activity/member/status/mode/duration`）返回，**接线 v3 时需路由侧补 `member_name`**。
  3. **`list_for_user`** 沿用 `users_v2` 的非保留 `$unwind`（成员指向已删活动则被丢弃）+ 无 join 的 count，逐字一致。
- **结论**：activities/members 的接线需按端点定制（各列表形态不一），不宜用单一通用方法一把接完；留待专门的接线轮。

## 6.3 仓储层补全 + ETL（2026-10-12）

- **仓储全量就绪（未接线）**：`users/groups/activities/members/time/stats`（+对方新增 `changelog`）的 Mongo 适配器与 Oracle 实现均已落地；工厂 `db/backends.py` 提供 `get_{users,groups,activities?(待补),members?,time,stats,changelog}_repo` 中已实现的部分。
  - 本轮新增：`db/repos/{mongo,oracle}/{times,stats}.py` + 工厂 `get_time_repo`/`get_stats_repo`（flag-on/off 结构检查通过）。
- **ETL**：`db/etl/mongo_to_adb.py`（`--dry-run` 默认、`--execute`、`--only`、`--batch`、`--active prod|test`）。
  - 幂等写：身份/父表用 `MERGE`，派生缓存表用「按批 DELETE by PK + INSERT」。
  - 完整性护栏（隔离不强制）：非 hex id、未知枚举、`activity_members` 三键重复、FK 父缺失、文本超长。
  - 实测 dry-run：`groups 5→2`（过滤 permission 组，见下）、`users 9`、`activities 2`、`activity_members 3`，**零 Oracle 连接**（engine 未导入）。
  - **已知限制**：`GROUPS` 现模型仅 class 组（OID/NAME），故默认把 `type=="permission"` 的组过滤掉（计为 `filtered`，非静默丢弃；`--include-permission-groups` 可保留）。表结构对齐（§10）后应改为全量承载。
  - `logs`/`changelog` 显式跳过；`app_logs`/`tasks`/`imports`/`notifications` 因未建模而跳过。

## 6.4 接线轮（activities / members / time / stats）

> 分支 `zvms4-adb-migration` · 本地代码改动，**未提交** · flag 关闭时逐字等价。

### 接线清单

- **工厂**：`db/backends.py` 新增 `get_activities_repo`（`"activities"`）/ `get_members_repo`（`"activity_members"`），并入 `__all__`。
- **activities v3**（`routers/activities_v3_router.py`，全量）：`get_activities_v3`→`ActivitiesRepo.list_page`+路由侧补 `creator_name`；`create_activity_v3`→`create`；`get_activity_members_v3`→`MembersRepo.list_for_activity`+路由侧补 `member_name`；`add_activity_member_v3`→`get`/`get_by_triple`/`update_fields`/`add`/`update`。
- **activities v2**（`routers/activities_v2_router.py`）：`get/update/delete` + `count_for_activity` + `get_by_oid` + `get_by_triple` + `update_fields` + `remove_for_activity`；`create_activity_v2`、`get_activities_v2`、`get_activity_v2`、`get_activity_members_v2`、`get_activity_member_v2`、`add_activity_member_v2` 的原始文档读写原样保留（见“仍留 Mongo”）。
- **activities v1**（`routers/activities_router.py`）：描述/名称/状态更新→`update`；成员时长→`set_duration_for_member`；`read_activities`/`read_activity` 两个内嵌 `members` 聚合保留。
- **mcp**（`routers/mcp_router.py`）：活动存在性→`get`；成员计数→`count_for_activity`；统计端成员时长→`list_by_activity`；列表投影/`$group`/建议保留。
- **statistics**（`routers/activity_statistics_router.py`，全量）：成员分布/layers→`list_by_activity`；`indicators` 单行→`get_indicators`。
- **times**（`routers/times_router.py`）：`time` 读取→`TimeRepo.list_by_users`（含排序/分页）；用户检索保留。
- **util**：`calculate.py`（`time`/`time_with_origin`/`time_with_origin_stat` + `activities`/`activity_members`）、`statement.py`（`get`+`list_for_member`）、`get_class.py`（`list_by_members`+`list_by_oids`）域内访问已接线。
- **tasks**：`compute_time.py`（`clear_for_all`/`upsert_academic_year`/`clear_academic_years`/`list_by_users`/`list_with_origin`/`clear_with_origin`/`clear_with_origin_stats`/`upsert_with_origin_stat`/`upsert_group_indicators`/`upsert_indicators`）。

### 新增仓储方法（protocols + mongo + oracle 同步，均忠实搬运）

- `ActivitiesRepo`：`list_effective(date_start,date_end)`、`list_by_oids(oids,query,page,size)`。
- `MembersRepo`：`get_by_oid`、`get_by_triple`、`list_by_activity`、`list_for_member(status,activity_oids)`、`list_by_members`、`count_for_activity(status,member_oids)`、`remove_for_activity`、`update_fields`、`set_duration_for_member`。
- `TimeRepo`：`list_by_users`、`list_with_origin`、`upsert_with_origin_stat`、`clear_for_all`、`upsert_academic_year`、`clear_academic_years`、`clear_with_origin`、`clear_with_origin_stats`。
- `StatsCacheRepo`：无需扩展（`get_indicators`/`upsert_indicators`/`upsert_group_indicators` 已足够）。
- 语义要点：`list_for_member` 用 `activity_oids is None` 区分「不过滤」与「`$in []` 匹配零行」；`list_by_oids` 空 `query` 不加过滤（Mongo 空正则≈无过滤，Oracle 空 `REGEXP_LIKE` 不匹配任何行，故两侧一致省略）。

### 验证（原始输出关键行）

- 静态：`compileall -q . -x '(.venv|node_modules)'` → exit 0；`import main` → `IMPORT_OK`。
- flag-on 结构：`ORACLE_ENABLED=True` + 7 模块，`get_{users,groups,activities,members,time,stats,changelog}_repo()` 全返回对应 `Oracle*` 类，且 patch `db.oracle.session.get_sessionmaker` 未被触发 → `FLAG_ON_OK (no engine created)`。
- e2e 主 smoke：**TOTAL 40 / PASS 40 / FAIL 0**。
- times/stats 追加校验（独立脚本 `/tmp/opencode/zvms4-e2e/smoke_times_stats.py`，不改动主 smoke）：**TOTAL 6 / PASS 6 / FAIL 0**（`/api/v2/times`、`statistics/{id}/description|layers`、`grades/{grade}/percentiles|indicators`）。
- 更全的 `zvms4-smoke.py`：**39/41**；2 项失败均为 `mc.zvms.logs`（遗留库）相关——本仓日志实际写 `zvms_new.app_logs`（`typings/log.py` 未改动）。已在 **HEAD 干净 worktree** 复现同样 `39/41`，确认与本轮接线无关（属 logs 域既存不一致）。

### 仍留 Mongo 的点及原因

1. **v1 内嵌 `members` 聚合**（`activities_router.read_activities`/`read_activity`）：`$filter`/`$map`/`$sortArray` 作用于旧版内嵌数组，无对应表结构/行形状。
2. **v2 原始文档响应端点**（`get_activities_v2`/`get_activity_v2`/`get_activity_members_v2`/`get_activity_member_v2`）与两处 raw `insert_one`（`create_activity_v2`/`add_activity_member_v2`，`model_dump()` 含字符串 `_id`）：仓储返回 canonical 13/6 字段、且无法忠实重放 raw 文档/自定义 `sort` 字段，直接接线会改响应契约。
3. **mcp 列表投影 / `types/summary` 的 `$group` / `search/suggestions`**：投影子集与聚合分组不在建模范围。
4. **跨域 users/groups 读取**（`times_router`、`calculate.time_with_origin`、`get_class` 的 `users`/`groups` 查询，`exports_router` 的 `users.find({})`）：属用户/组域，本轮不扩展其仓储。
5. **`tasks/data_fix.py`**：读 `title` 遗留字段（无此列）且需 `list_all`/`list_by_status`；**`routers/exports_router.py`**：`tasks`/`daily_exports` 队列明确不在本轮。
6. **logs 域**（`zvms_new.logs` 设备 schema / `app_logs`）：维持原样。

### 待决项（不阻塞本轮）

- **exports 用户半** 需 `UsersRepo.list_all()`，超出本轮「仅扩展 activities/members/time/stats」的范围；与 `tasks` 队列一并留待导出 slice。
- **v2 原始文档端点的取舍**：要「逐字等价」需仓储提供 raw 文档行形状（Oracle 侧无对应列），否则须接受响应仅含 canonical 字段——需要架构裁决。
- **`tasks/data_fix.py`** 的 `title` 字段语义（疑似历史遗留错字，应确认是 `name` 还是死字段）后再接线。
- **flag-on 实库验证**：Oracle 新增 SQL 仅通过结构检查（未连 ADB）；待连通性解决后随 `V001` 落地做等价 smoke。

## 6.5 收口轮（跨域 + raw 端点）

> 分支 `zvms4-adb-migration` · 本地未提交 · flag 关闭时逐字等价（golden 深比较全绿）。

### 新接线清单（本轮）

- **times**（`routers/times_router.py`）：用户检索 count+find → `UsersRepo.search(query, logged_in)`（三支 `$or` 原样、未转义；未登录走 `{"id": query}`）；逐行用户信息 → `get_by_oid`。
- **calculate**（`util/calculate.py::time_with_origin`）：grade 支 `{"id": {"$regex": "^<scope_id>"}}` → `list_by_id_prefix`；group 支 `{"class_id": oid}` → `list_by_class`。
- **get_class**（`util/get_class.py`）：`get_user_class`/`get_user_classname` 的 users/groups `find_one` → `get_by_oid`/`GroupsRepo.get`；`get_activities_related_to_user` 的 `users.find({"class_id": oid})` → `list_by_class`；`get_classname` 的 `groups.find()` → `GroupsRepo.list_all`。
- **exports**（`routers/exports_router.py::process_task`）：两处 `users.find({})` → `list_all`；两处 `groups.find_one({"_id": user.class_id})` → `GroupsRepo.get`（`class_id` 为空时保持 `None`）。
- **compute_time**（`tasks/compute_time.py`）：`compute_time`/`compute_ay_time` 的 `users.find({})` → `list_all`；`generate_reports`/`compute_group_indicators` 的 `users.find({"class_id": oid})` → `list_by_class`；`compute_batch_origins`/`compute_indicators` 的 `$group($substrCP id 0 4)` 聚合 → `group_oids_by_id_prefix`。
- **data_fix**（`tasks/data_fix.py`）：`activities.find({})`/`find({"status":"refused"})` → `list_raw`；标题改写 → `update`；零参与者判定 → `count_for_activity(status="effective")`；拒绝活动/测试活动清理 → `remove_for_activity` + `delete`。
- **activities v2**（`routers/activities_v2_router.py`）：`get_activities_v2` 列表 count+聚合 → `list_raw`（自定义 `sort` 原样透传）；`get_activity_v2` → `get`；`get_activity_members_v2` 的用户检索 → `list_by_name_or_id`，成员 count+聚合 → `MembersRepo.list_raw`；`add_activity_member_v2` 的 `users.find_one` → `get_by_oid`。返回体仍为 raw 文档（`_id` 字符串），逐字一致。
- **mcp**（`routers/mcp_router.py`）：`mcp_get_activities` → `list_raw` + 路由侧投影（`location`/`duration` 无列 → 键自然缺省，与 Mongo 相同）；`mcp_get_activity_details` → `get` + 投影；`search/suggestions` → `list_raw(sort=None)` + 投影。

### 新增仓储方法（protocols + mongo + oracle 同步，均忠实搬运）

- `UsersRepo`：`list_all`、`list_by_id_prefix(prefix)`、`list_by_class(class_oid)`、`search(query, logged_in)`、`list_by_name_or_id(pattern)`、`group_oids_by_id_prefix()`。
  - 语义要点：`search` 的 Mongo 侧**不** `re.escape`（原码即未转义）；Oracle 侧 `past` 用 `JSON_EXISTS(... like_regex ... 'i')` 复刻 `$elemMatch`/`$regex`。`list_by_name_or_id` 的模式由调用方预转义，空模式 = 全部。`group_oids_by_id_prefix` Oracle 用 `SUBSTR + JSON_ARRAYAGG` 复刻 `$group + $push`（元素已字符串化）。
- `GroupsRepo`：`list_all()`（class 形状 `{_id,name}`；Oracle 仅 OID/NAME 两列）。
- `ActivitiesRepo`：`list_raw(search, atypes, status, sort, asc, page, size)` —— 返回 raw 文档（`_id` 字符串、自定义排序、`page=None` 不分页）。
- `MembersRepo`：`list_raw(activity_oid, member_oids, sort, asc, page, size)` —— raw 成员文档；`member_oids=[]` 与 `None` 区分（`$in []` 匹配零行）。
- 未新增 `UsersRepo.list_by_ids`：核查后无调用点需要（`get_names`/`list_by_name_or_id`/`search` 已覆盖全部实际读取），按「按需补」原则略过。

### golden 深比较结论

脚本 `/tmp/opencode/round2-golden/capture.py`（capture 先 `seed.py` 再灌确定性夹具；compare 只重灌夹具，并对 `_id` 与 ISO 时间戳做**显式规范化**：`_id` 映射回 seed role（无 ids 映射时退化为 `<oid>`）、时间戳统一为 `<ts>`，故重灌 seed 后仍可复跑）。结果：**16 HTTP + 7 direct = 23 项全绿**。

- HTTP：`/api/v2/times`（sort=id / on-campus）、`/api/v2/activities`（列表 / 关键词）、`/api/v2/activities/{id}`、`.../members`（全部 / 关键词）、`/api/v2/mcp/activities`（/ 关键词）、`/api/v2/mcp/activities/{id}`、`/api/v2/mcp/activities/types/summary`（见待决）、`.../search/suggestions`、`/api/groups/{id}/activities`、`/api/v2/users/{id}/time_statistics`、`/api/v2/users/{id}/time`、`/api/v2/users`。
- direct（非 HTTP 路径）：`time_with_origin(grade/group)` 返回、`get_classname`、`get_user_class`、`users $group by id-prefix`、`process_task`（users / time 两种导出的 `result`）。

### 验证（原始输出关键行）

- 静态：`compileall -q . -x '(.venv|node_modules)'` → exit 0；`import main` → `IMPORT_OK`。
- Oracle 语句离线编译：12 条新语句 `compile(dialect=OracleDialect())` 全通过（`ALL_STMTS_OK`）。
- flag-on 结构：7 个工厂返回对应 `Oracle*`，`engine_created: False` → `FLAG_ON_OK`。
- e2e 主 smoke：**TOTAL 40 / PASS 40 / FAIL 0**。
- times/stats 追加校验：**TIMES_STATS TOTAL 6 / PASS 6 / FAIL 0**。
- 更全的 `zvms4-smoke.py`：**total=41 pass=39 fail=2**；两项失败均为 `mc.zvms.logs`（遗留库）相关，与既有基线一致，**无新增失败**；其中 `export task completes` = PASS（覆盖 exports 接线）。

### 仍待决项及确切原因

1. **activities v1 列表/内嵌 members**（`routers/activities_router.py::read_activities`/`read_activity`）：聚合作用于旧版**内嵌 `members` 数组**（`$filter`/`$map`/`$sortArray`），并投影 `special`/`registration`、按遗留 `type` 字面量（`specified/social/scale/special`）过滤 —— 新 schema 无内嵌数组、无 `special`/`registration` 列、`ATYPE` 亦不含这些字面量。忠实接线会改行为 → 保留 Mongo。
2. **mcp `types/summary`**（`routers/mcp_router.py`）：现网 `$project` 管道本身在驱动层报错（`'$this' is a field path reference which is not allowed in this context` → **HTTP 400**）。接成可用的仓储等价物会把响应从 400 变成 200，**可观察行为改变** → 原样保留。
3. **`GroupsRepo.list_non_permission`**（`{"type": {"$ne": "permission"}}`）：`db/models.py` 的 `GROUPS` 仅 `OID/NAME`，无 `TYPE` 列；不发明字段则无法忠实实现 → 登记待决。连带 `tasks/compute_time.py` 的 3 处 `groups.find({"type": {"$ne": "permission"}})`（`generate_reports`/`compute_batch_origins`/`compute_group_indicators`）保留 Mongo。
4. **`tasks/data_fix.py` 的 `{"title": {"$regex": ...}}`**：`ACTIVITIES` 无 `title` 列（文档字段为 `name`）→ 该子块保留 Mongo。（该函数其余 activities/members 读取已接线。）
5. **v2 raw 写入**（`create_activity_v2` / `add_activity_member_v2` 的 `insert_one(model_dump())`）：仓储 `create`/`add` 不接受调用方 `_id`、且会改写时间戳/哨兵字段；接入会改变落库文档 → 保留 Mongo（本端点仍属 raw 写入路径）。注：`add_activity_member_v2` 的**读取侧**（用户存在性/权限）已接线。
6. **exports `tasks` 队列 / `daily_exports`** 与 **`logs` 域**：维持原样（不在本轮范围）。
7. **malformed id 的错误码**：仓库边界 `normalize_oid` 抛 `ValueError`（→500）而原 `validate_object_id` 抛 400；本轮在改写点**保留**了 `validate_object_id` 前置校验以维持 400 语义，但这是迁移的既存属性（历史已接线端点同样如此），记录备查。

## 6.6 上线轮（实库落地 + 生产切换，2026-10-13）

> 分支 `zvms4-adb-migration` · 生产 `zgovps-la` 已切至 ADB · 首次实库端到端验证。

### 实库落地（`zvp5adb2` 测试 → `zvp5adb` 生产）

- 两实例均以 `config_dir`+`wallet_location` 连通（见 §3）。
- DDL 落库：`V001`（**前向 FK 致单遍失败，幂等重跑第 2 遍收敛 20/20、0 error**）→ `V002`（users.banned 增量）→ `V003`（activities 文本列改可空增量）。测试库与生产库均达 13 业务表 + `KEEPALIVE_HEARTBEAT`。
- ETL 全量（`--execute`，batch=500）双侧一致，最终写 21919 行；FK 父行缺失等隔离 1765 行（见下表）。

| 表 | Mongo | ADB | 差异 | 原因 |
|---|---|---|---|---|
| users | 2562 | 2562 | 0 | — |
| groups | 57 | 54 | −3 | 3 个 legacy 权限组被过滤（预期） |
| activities | 395 | 354 | −41 | FK 父行缺失隔离 |
| activity_members | 13254 | 11542 | −1712 | FK 父行缺失隔离 |
| user_time | 2566 | 2562 | −4 | FK 父行缺失隔离 |
| time_academic_year | 2566 | 2562 | −4 | 同上 |
| time_with_origin | 2075 | 2071 | −4 | 同上 |
| time_with_origin_stat / indicators / group_indicators / daily_exports | 138/1/72/1 | 138/1/72/1 | 0 | — |

### 生产切换（`zgovps-la`）

- 部署：后端整包 rsync（排除 `settings.py`/`.venv`/密钥/`*.pyc`）→ `v4.2.2-2`；前端 `pnpm build` 后 rsync `dist/` → `index-BQIsDUD_.js`。
- 配置：wallet 落 `/www/zvms4/adb-wallet`（`www:www 700`）；`settings.py` 增 `ORACLE_ENABLED=True`、`ORACLE_ACTIVE="prod"`、`ORACLE_DSN="zvp5adb_medium"`、`ORACLE_MODULES=["users","groups","activities","activity_members","time","stats"]`。
- 备份：整包 `/www/zvms4/backups/pre-adb-20261013-2251.tar.gz`；旧关键文件 `/root/zvms4-backup-20261013/`。

### 本轮发现并修复的问题

1. **`ORA-12506`**：thin 模式仅传 `config_dir` → 无 mTLS 客户端证书；须 `config_dir`+`wallet_location` 同传（`db/oracle/engine.py` 已正确）。
2. **V001 前向 FK**：`users` 引用其后创建的 `groups` → 首遍部分失败；DDL 幂等，重跑收敛。
3. **`UQ_USERS_BUSINESS_ID` 冲突**：源 Mongo `users.id` 有 3 个重复值（`20251240`/`20252559`/`20251316`；2566 总、2563 唯一）→ 数据修复：删 2 条「屏蔽」占位 + 2 条废弃 `20252559`，保留 2 条真实班级记录。
4. **`DEFAULT '' NOT NULL` 自相矛盾**：Oracle 中 `''` 即 `NULL`，`activities.description/approver/place` 三列致 `ORA-01400`（生产 `place` 395/395、`description` 355、`approver` 41 为空串）→ `V001` 改可空 + 新增 `V003` 增量 + `db/models.py` + Oracle activities 读取侧 `None→''` 归一。
5. **oracledb JSON 自动解码**：带 `IS JSON` 约束的 CLOB 被驱动直接返回已解码 `list/dict`，`JSONText.process_result_value` 再 `json.loads` 抛 `TypeError`（真实库上表现为登录 500）→ 改为仅对 `str` 解析。

### 用户封禁状态（本轮新增能力）

- Mongo `users.banned`（缺失即未封禁）→ ADB `USERS.BANNED NUMBER(1) DEFAULT 0 NOT NULL` + `ck_users_banned`（`V001` + `V002` 增量）。
- 拦截点：`util/cert.py::validate_by_cert` 在 404 检查后早退，`banned=true` → `403 User is banned`，覆盖口令/弱口令短令牌/TOTP 全模式。
- ETL 新增 `BOOL_KIND`，`users.banned` → `0/1`（缺省 0）。

### 验证证据（原始关键行）

- `python -m db.ddl_check` → `OK 13 tables, 7 indexes - no drift`。
- ETL → `ETL_EXIT 0`（测试库、生产库各一次）。
- 鉴权差分：`20251240`/`20251316` → `403 {"detail":"User is banned"}`；未封禁用户 → `403 {"detail":"Password incorrect"}`（过封禁门后进凭据校验）。
- 仓储对拍（Oracle 读出 vs Mongo 原始）：users/activities/groups 字段一致（含 `place/description` 由 NULL 归一为 `''`、`roles` 列表、`banned`）。
- 切换后 5 分钟：`84×200 / 1×401 / 0 traceback`。

### 回退语义（**非无损**）

- 回退 = `settings.py` 置 `ORACLE_ENABLED=False` + `systemctl restart zvms4` → 回 Mongo；或还原整包备份。
- **注意**：切换后 6 个模块的写入只落 ADB，Mongo 对应集合停写；回退到 Mongo 会丢失 ADB 窗口内写入，故仅作故障应急，不做数据往返。

## 6.7 MongoDB 退役完成（2026-10-14）

> ADB 成为**唯一**数据后端；MongoDB 已从代码、依赖与生产运行中彻底移除。

- **删除**：`database.py`；`db/repos/mongo/**`（12 模块）；`db/etl/**`（Mongo 源 ETL，数据已迁毕）。
- **重写**：`db/backends.py` 11 个工厂无条件返回 Oracle 仓储（去掉 `ORACLE_MODULES` 开关与 Mongo 分支）；`main.py` 去掉 motor/`connect_to_mongo`/`close_mongo_connection` 与 `pymongo.errors.OperationFailure` 处理器。
- **依赖**：`requirements.txt` 去 `motor`/`pymongo`（生产 venv 已卸载）；`settings.example.py`/生产 `settings.py` 去 `MONGODB_URI`。
- **`bson` 清零**：`db/ids.py` 增设 `is_valid_oid`/`validate_oid`，`new_oid()` 改为「时间前缀 + 随机」（维持 `ORDER BY oid DESC` = 最新在前）；8 文件约 49 处 `ObjectId(` 全部替换。
- **生产切全模块**：`ORACLE_MODULES` = users/groups/activities/activity_members/time/stats/changelog/logs/tasks/imports/daily_exports；数据同步 groups 57 / app_logs 294 / export_tasks 1 / imports 1。
- **验收**：health/version/changelog/activities/users/times/groups/logs/exports 全 200；封禁用户 403。

### 两个 ADB 专有陷阱（本轮发现并修复）

1. **大文本 CLOB 绑定**：python-oracledb thin 对 `>4000` 字符的 `str` 一律 `ORA-01461`，且 `setinputsizes(DB_TYPE_CLOB)` **无效**；必须绑定 LOB 对象（`connection.createlob(DB_TYPE_CLOB)` + `write`）。已做成通用机制 `db/oracle/clob.py::install_lob_binds`（注册于 `db/oracle/engine.py`），同时覆盖运行期 `OracleTasksRepo` 写大 `result`。
2. **`like_regex` 的 `flag` 子句**：ADB 当前为 Oracle **19.33**，SQL/JSON 路径不支持 `like_regex … flag "i"`（21c+ 才有）→ `ORA-40597`。`db/repos/oracle/users.py::_PAST_MATCH` 改为 `regexp_like(json_serialize(past), :pat, 'i')`（保留大小写不敏感、未转义正则语义）。

### 仍待办（不阻塞）

- `ORACLE_ENABLED` 开关残留（`db/oracle/session.py`/`config.py`/`main.py` 仍读取，实为恒真）；可后续移除。
- `zvms4-mongo` 容器与历史数据是否退休（破坏性操作，需显式确认）。
- 既存属性：`normalize_oid` 对非 16 进制输入抛 `ValueError`（→500），原 `validate_object_id` 为 400。

## 7. 待决（不阻塞本轮）

- **logs 双重性**：`zvms_new.logs` 是设备 schema 死集合（本仓零引用），运行期日志走遗留 `zvms.logs`；`app_logs` 在 `db/models.py` 无表，运行期仍写 Mongo。logs 模块迁移前需拍板：是否 ETL 设备数据 / 是否新建 `APP_LOGS`。
- **changelog**：本轮已接入 ETL（Mongo `zvms_new.changelog` → `CHANGELOG`）；落库 + 开模块后即可切换。
- **遗留 id 重映射**：`group_indicators.group`、`time_with_origin_stat.scope_id(group)` 仍持遗留 group id；倾向不 ETL、由移植后的夜间重算重建。
- **仍留 Mongo 的路径**（见 §6.4/§6.5 待决项）：v1 内嵌 `members`、v2 raw 写入、mcp `types/summary`、`GroupsRepo.list_non_permission`（缺 `TYPE` 列）、`tasks`/`imports`/`notifications`（无表）。
- **notification API（暂废弃，待重构）**：`/api/users/{oid}/notification`（`routers/users_router.py`）与 `notifications` 集合前端零调用（仅 UI 库 `ElNotification`，非该 API）、集合计数 0 → 随 Mongo 退役移除端点；**后续按新设计重构该 API**，不复刻旧 Mongo 形状。
- **Mongo 退役决策（2026-10-13）**：彻底移除 Mongo 依赖（含遗留 `zvms` 库；实测该库已不存在，双库合并为死路径）。新增表 `APP_LOGS`/`EXPORT_TASKS`/`IMPORTS`，`GROUPS` 补 `TYPE`+`PERMISSIONS`（承载 3 个权限组）。设备 `logs`（7968 行、本仓零引用）保留 ADB 空表不迁。死代码（`conversion/*`、`tasks/categorize.py`、`misc/` 一次性脚本）删除。v1 `activities_router` 废弃（前端未调用、内嵌 `members` 全库为 0）。
  **执行方式（2026-10-13 追加定调）**：**完全去除** Mongo 兼容与代码，**不保留过渡适配器与开关**——不保留 `db/repos/mongo/*`、`db/backends.py` 的 Mongo 分支、`database.py`/motor/`MONGODB_URI`，所有 router/util/task 直连 Oracle 仓储。顺序：先补全 Oracle 仓储与接线 → 把剩余数据（`app_logs`/`tasks`/`imports`/权限组）ETL 同步进 `zvp5adb` → prod 验证 → 最后物理删除 Mongo 代码/依赖并停用 Mongo 容器（ETL 工具届时一并移除或归档）。
- **已修生产缺陷（2026-10-13）**：`routers/activities_v2_router.py` 创建活动 / 添加成员原为 raw Mongo 写入，与已切 Oracle 的读路径不一致 → 报 `404 Activity not found`；已改为 `backends.get_activities_repo().create(...)` + `get_members_repo().add(...)`，并移除该文件未使用的 `database.db` 导入。

## 6.8 义工数据补录（2026-10-14）

> 生产反馈「迁移后少了很多义工」。只读对账 `zvms4-mongo`（`zvms_new`）↔ `zvp5adb`（生产）后定位并补齐。

### 缺口

| 集合 | Mongo | ADB（补录前） | 差 | 补录后 ADB |
|---|---|---|---|---|
| activities | 396 | 354 | −42 | **395** |
| activity_members | 13254 | 11575 | −1679 | **13287** |

（ADB 侧另有 33 条迁移后新增成员、3 个迁移后新增用户，均保留；反向差异为零 → 补录为纯追加。）

### 42 个缺失义工的根因（ETL 完整性护栏隔离，非数据丢失）

1. **FK 悬空（26 个）**：`creator`/`appointee` 指向 **9 个已删除账号**（`65e6fa21…` 同批，2024-03 建号，Sep 25 及更早的 dump、以及原始 Atlas 库中均已不存在，无任何名字/学号残留）→ ETL 的 `missing_fk_parent` 门隔离。
2. **description 超长（20 个）**：ETL 文本护栏按模型 `VARCHAR2(1000)` 卡；实际最长 **54968** 字符（多为开发笔记）。
3. **时间戳类型异常（1 个，「校合唱队」）**：`updatedAt` 存的是 Int64 毫秒（`1791718430715`）→ ETL 的 `TS` 校验拒绝。
4. **迁移后测试写入（1 个，「TestDB」）**：2026-10-13 23:21 建，在 ETL 之后 → 判为测试数据。

**1712 条缺失成员**：1708 条属于上面被隔离的义工；4 条 `member` 指向已删除账号（其中 `68c3e31e…` = 周金开颜/学号 20252559，于 10-13 学号去重时删除，其 `time` 缓存行一并悬空）。

### 处置（用户拍板，2026-10-14）

- **悬空账号**：**保持删除，允许出现「无此用户」** —— 保留原始 oid 引用，放开对应 FK（不建占位、不置 NULL、不恢复账号）。
- **超长 description**：**直接置空**（不改 CLOB）。
- **范围**：纳入「校合唱队」（修正时间戳），排除「TestDB」。

### 变更

- **DDL `V006__relax_activity_user_fks.sql`**（幂等）：DROP `fk_activities_creator_oid`、`fk_activities_appointee_oid`、`fk_activity_members_member_oid`；**保留** `fk_activity_members_activity_oid`（被引用活动均已补录，完整性保留）。
- **`db/models.py` / `V001`** 同步移除上述 3 个 FK（`python -m db.ddl_check` → `OK 16 tables, 9 indexes - no drift`）。
- **补录脚本**：幂等 `MERGE … WHEN NOT MATCHED`（只增不改），先在 `zvp5adb2` 演练再上 `zvp5adb`。脚本路径 `/root/backfill_missing_volunteers.py`（服务器），副本 `/tmp/opencode/backfill_missing_volunteers.py`。

### 验证证据

- `zvp5adb2`：activities 354→395、members 11542→13254；`zvp5adb`：354→395、11575→13287。
- FK 现状：`ACTIVITIES`/`ACTIVITY_MEMBERS` 仅剩 `FK_ACTIVITY_MEMBERS_ACTIVITY_OID`。
- 抽样：`校合唱队.updated_at = 2026-10-11 11:33:50`（Int64 已转）；`布置考场`（悬空 creator）oid 原样保留、description 已置空；`TestDB` 不存在；周金开颜名下 2 条成员已补回。
- 走真实读路径 `OracleActivitiesRepo.list_page` → `total = 395`；线上 `/api/health`、`/api/version`（v4.2.2-2）200，日志无新错误。
- 复跑缺口报告：Mongo→ADB 缺失 activities = 1（仅 TestDB，决策排除）、缺失 members = 0。

### 发现（本轮顺带，未处理）

- **ADB 每日备份同步失败**：`/root/adb-backup/sync_adb.py`（prod→adb2，cron 03:30）于 2026-10-14 03:30 报 `ORA-02292: FK_ACTIVITY_MEMBERS_ACTIVITY_OID violated` —— 其「整表 DELETE 后重灌」的顺序在父表先删、子表仍引用时违反 FK。属既存缺陷，需按子表优先或临时禁用约束修正。
- **`user_time` 派生缓存**：Mongo 2566 vs ADB 2565（悬空账号的行），由夜间 `compute_time` 重算自愈，未纳入本次补录。

_本文档随各 lane 产出持续更新。_
