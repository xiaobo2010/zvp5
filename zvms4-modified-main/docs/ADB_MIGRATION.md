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
- **连通性缺口**：本机 DNS 走 fake-ip 代理（`198.18.0.110`），TCP 1522 通，但 oracledb TLS 握手挂起 → 本机直连实库暂不通。本轮不依赖实库。

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

## 7. 待决（不阻塞本轮）

- **logs 双重性**：`zvms_new.logs` 是设备 schema 死集合（本仓零引用），运行期日志走遗留 `zvms.logs`。logs 模块迁移前需拍板：是否 ETL 这批设备数据 / 是否新建 `APP_LOGS`。
- **遗留 id 重映射**：`group_indicators.group`、`time_with_origin_stat.scope_id(group)` 仍持遗留 group id；倾向不 ETL、由移植后的夜间重算重建。
- **实库连通**：本机 TLS 握手问题（fake-ip 代理）解决后，方可对 `zvp5adb2` 做 DDL 落地与端到端验证。

_本文档随各 lane 产出持续更新。_
