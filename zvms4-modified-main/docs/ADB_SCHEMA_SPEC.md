# ADB Schema Spec — Mongo(zvms_new) → Oracle ADB

> 用途：实现者（fixer）据此落地 DDL / 模型 / 数据访问层。**唯一权威**。
> 来源：`@oracle` 设计规格（2026-10-12），编排者按既定约束做了三处裁决（见 §0）。

## 0. 与研究的裁决（偏差声明）

| 项 | 研究建议 | **本规格裁定** | 依据 |
|---|---|---|---|
| JSON 列 | 原生 `JSON`（ADB 23ai） | **`CLOB` + `CHECK (col IS JSON)`** | 栈锁定 SQLAlchemy **2.0.x**，2.0 无 Oracle JSON 类型渲染（`Column(JSON)` 编译即报错）。此选择与 ADB 版本无关，稳妥。 |
| ObjectId 存储 | `RAW(12)` + TypeDecorator | **`VARCHAR2(24)` 小写 hex** | `activities.approver` 存在 `""`/`"authority"` 哨兵，非 hex 不能入 RAW；跨引用字段在 Mongo 中本就是字符串；hex 字典序=插入序，保留 `_id` 倒序分页语义。 |
| 时间 | `TIMESTAMP WITH TIME ZONE` | **朴素 `TIMESTAMP`（无时区）** | 全部写入为 `datetime.now()` 朴素值；引入 TZ 会导致展示时间偏移。ETL 不做时区换算。 |

其余设计（表结构、重命名映射、DAL 分层、DDL 交付方式、ETL、风险）全部采纳。

## 1. 全局约定

- `_id` → 列名 `OID`，`VARCHAR2(24)` 小写 hex，主键；`CHECK (REGEXP_LIKE(oid,'^[0-9a-f]{24}$'))`。仓库层用 `normalize_oid()`（接受大小写，统一小写）。
- Oracle 标识符：不带引号、snake_case、ASCII（不带引号会折叠为**大写**，一致由 SQLAlchemy Core 保证）。
- 保留字/连字符/camelCase 一律按 §3 重命名映射改写。**任何漏改（`date`/`user`/`group`/`level`/`timestamp`）都会导致 DDL 硬错**。
- 文本：`VARCHAR2(n CHAR)`。数值：时长/百分比用 `BINARY_DOUBLE`（保留 Mongo double 保真），整数用 `NUMBER(k)`。
- 时间：BSON `Date` → 朴素 `TIMESTAMP`。
- 外键：`NO ACTION`；应用层自行级联（现状即如此）。
- 集合位置：**仅 `zvms_new`**；遗留 `zvms` 不建表。

## 2. 表设计

> JSON 列在正文写作 `JSON`，**实现时一律用 `CLOB` + `IS JSON` 检查约束**（§0）。

### USERS ← zvms_new.users
| 列 | Mongo | 类型 | 空 | 备注 |
|---|---|---|---|---|
| OID | _id | VARCHAR2(24) | PK | |
| BUSINESS_ID | id | VARCHAR2(32) | NOT NULL UNIQUE | 学号，可能前导零，禁 NUMBER |
| NAME | name | VARCHAR2(64 CHAR) | NOT NULL | |
| ROLES | roles | JSON | NOT NULL DEFAULT '[]' | 数组，白名单 student/secretary/department/admin/teacher |
| CLASS_OID | class_id | VARCHAR2(24) | NULL FK→GROUPS(OID) | |
| PASSWORD_HASH | password | VARCHAR2(100) | NULL | **NULL 有语义**：初态登录比对明文=学号 |
| SESSION_ID | session_id | VARCHAR2(64) | NULL | 仅保真，代码零引用 |
| SEX | sex | VARCHAR2(16) | NULL | 代码防御式读取 |
| ELIGIBILITY | eligibility | JSON | NULL | 代码引用 |
| PAST | past | JSON | NULL | 代码引用 |

索引：PK(OID)、UQ(BUSINESS_ID)、IX(CLASS_OID)。

### GROUPS ← zvms_new.groups
`OID` PK VARCHAR2(24)；`NAME` VARCHAR2(128 CHAR) NOT NULL。仅这两列（`type:"class"` 由代码合成，不入库）。

### ACTIVITIES ← zvms_new.activities
| 列 | Mongo | 类型 | 空 | 备注 |
|---|---|---|---|---|
| OID | _id | VARCHAR2(24) | PK | |
| ATYPE | type | VARCHAR2(16) | NOT NULL | CHECK ∈ on-campus/off-campus/social-practice/hybrid |
| NAME | name | VARCHAR2(256 CHAR) | NOT NULL | |
| DESCRIPTION | description | VARCHAR2(1000 CHAR) | NOT NULL DEFAULT '' | |
| ACTIVITY_DATE | date | TIMESTAMP | NOT NULL | 保留字→重命名 |
| CREATED_AT | createdAt | TIMESTAMP | NOT NULL | |
| UPDATED_AT | updatedAt | TIMESTAMP | NOT NULL | |
| APPOINTEE_OID | appointee | VARCHAR2(24) | NOT NULL FK→USERS(OID) | |
| APPROVER | approver | VARCHAR2(24) | NOT NULL DEFAULT '' | **无 FK**（含 ""/"authority" 哨兵） |
| CREATOR_OID | creator | VARCHAR2(24) | NOT NULL FK→USERS(OID) | |
| STATUS | status | VARCHAR2(16) | NOT NULL | CHECK ∈ pending/effective/refused |
| PLACE | place | VARCHAR2(256 CHAR) | NOT NULL DEFAULT '' | |
| ORIGIN | origin | VARCHAR2(24) | NOT NULL | CHECK ∈ 10 字面量 + 'unknown' |

索引：IX(STATUS)、IX(ATYPE)、IX(CREATOR_OID)。

### ACTIVITY_MEMBERS ← zvms_new.activity_members
| 列 | Mongo | 类型 | 备注 |
|---|---|---|---|
| OID | _id | VARCHAR2(24) | PK |
| ACTIVITY_OID | activity | VARCHAR2(24) | NOT NULL FK→ACTIVITIES(OID) |
| MEMBER_OID | member | VARCHAR2(24) | NOT NULL FK→USERS(OID) |
| MEMBER_STATUS | status | VARCHAR2(16) | NOT NULL CHECK ∈ effective/pending/refused |
| MEMBER_MODE | mode | VARCHAR2(16) | NOT NULL CHECK ∈ on-campus/off-campus/social-practice |
| DURATION_HOURS | duration | BINARY_DOUBLE | NOT NULL |

`UQ(MEMBER_OID, ACTIVITY_OID, MEMBER_MODE)`（编码应用级去重不变量）；IX(ACTIVITY_OID)、IX(MEMBER_OID)。

### USER_TIME ← time（表名 TIME 合法但已重命名以示清晰）
`OID` PK；`USER_OID` VARCHAR2(24) NOT NULL **UNIQUE** FK→USERS(OID)（每用户一行）；`ON_CAMPUS_RAW`/`OFF_CAMPUS_RAW`/`SOCIAL_PRACTICE` BINARY_DOUBLE NOT NULL DEFAULT 0；`UPDATED_AT` TIMESTAMP NOT NULL。

### TIME_ACADEMIC_YEAR ← time_academic_year
`OID` PK；`USER_OID` VARCHAR2(24) NOT NULL FK；`ENTRY_YEAR` VARCHAR2(8) NOT NULL；`ACADEMIC_YEAR` NUMBER(4) NOT NULL；`START_OF_YEAR`/`END_OF_YEAR` TIMESTAMP NOT NULL；`ON_CAMPUS`/`OFF_CAMPUS`/`SOCIAL_PRACTICE` BINARY_DOUBLE NOT NULL（Mongo 连字符字段→snake）。
`UQ(USER_OID, ACADEMIC_YEAR)`；IX(USER_OID)。

### TIME_WITH_ORIGIN ← time_with_origin
`OID` PK；`USER_OID` VARCHAR2(24) NOT NULL **UNIQUE** FK；`DATA` JSON NOT NULL；`UPDATED_AT` TIMESTAMP NOT NULL。
DATA 形如 `{"by_origin":{<origin>:{"on-campus":h,"off-campus":h,"social-practice":h}},"percentage_by_origin":{<origin>:pct}}`。

### TIME_WITH_ORIGIN_STAT ← time_with_origin_stat
`OID` PK；`STAT_KEY` VARCHAR2(64) NOT NULL UNIQUE（Mongo `id`，`f"{scope}_{scope_id}_{pct}"`）；`SCOPE` VARCHAR2(8) NOT NULL CHECK ∈ grade/group；`SCOPE_ID` VARCHAR2(24) NOT NULL（**多态**：grade→4 位年份；group→hex group id；**无 FK**）；`REQUIRE_PERCENTAGE` NUMBER(3) NOT NULL DEFAULT 0；`DATA` JSON NOT NULL；`UPDATED_AT` TIMESTAMP NOT NULL。

### GROUP_INDICATORS ← group_indicators
`OID` PK；`GROUP_OID` VARCHAR2(24) NOT NULL **UNIQUE**；`PERCENTILES` JSON；`INDICATORS` JSON；`UPDATED_AT` TIMESTAMP。
**FK 暂缓**：现值来自遗留 `zvms.groups` id；迁移后再加 FK（见 §7 重映射）。

### INDICATORS ← indicators
`OID` PK；`PERCENTILES` JSON；`INDICATORS` JSON；`UPDATED_AT` TIMESTAMP。单例行（应用层保证）。

### DAILY_EXPORTS ← daily_exports
`OID` PK；`CREATED_AT` TIMESTAMP NOT NULL；`STATUS` VARCHAR2(16) NOT NULL（无 CHECK）；`DATA` JSON NOT NULL DEFAULT '[]'。

### LOGS ← zvms_new.logs
`OID` PK；`XUEHAI_ID` NUMBER(18) NULL；`NET_SESSION` JSON NULL；`REQUEST` JSON NULL；`USER_INFO` JSON NULL（Mongo `user`）；`LTYPE` VARCHAR2(64) NULL（Mongo `type`）；`LOG_LEVEL` VARCHAR2(32) NULL（Mongo `level`）；`SUMMARY` VARCHAR2(1000 CHAR) NULL；`TS_AT` TIMESTAMP NULL；`TS_RAW` JSON NULL（`timestamp` 非干净可转时保留原值）。
**⚠ 策略待定（不阻塞本轮）**：`zvms_new.logs` 是 zvp 设备 schema 死集合（本仓零引用）；运行期日志写的是遗留 `zvms.logs`（`{url,user,data,timestamp:float,type,level}`）。logs 模块迁移前需就「是否 ETL 这批设备数据 / 是否新建 APP_LOGS」拍板。

## 3. 重命名映射（实现者逐字照抄）

| Mongo key | Oracle 列 |
|---|---|
| `_id` | `OID`（所有表） |
| users.`id` | `BUSINESS_ID` |
| `class_id` | `CLASS_OID` |
| activities.`type/date/creator/appointee/approver/createdAt/updatedAt` | `ATYPE / ACTIVITY_DATE / CREATOR_OID / APPOINTEE_OID / APPROVER / CREATED_AT / UPDATED_AT` |
| activity_members.`activity/member/status/mode/duration` | `ACTIVITY_OID / MEMBER_OID / MEMBER_STATUS / MEMBER_MODE / DURATION_HOURS` |
| time.`user`；表 `time`→`USER_TIME` | `USER_OID` |
| time_academic_year.`entry/on-campus/off-campus/social-practice` | `ENTRY_YEAR / ON_CAMPUS / OFF_CAMPUS / SOCIAL_PRACTICE` |
| time_with_origin_stat.`id/scope_id/require_percentage` | `STAT_KEY / SCOPE_ID / REQUIRE_PERCENTAGE` |
| group_indicators.`group` | `GROUP_OID` |
| logs.`user/level/type/timestamp` | `USER_INFO / LOG_LEVEL / LTYPE / TS_AT` |

## 4. DDL 交付与离线校验

- **方式**：手写版本化 SQL（`db/oracle/ddl/V001__initial_schema.sql`）+ `db/models.py`（SQLAlchemy Core `Table` 镜像）+ `db/ddl_check.py`（离线漂移校验）。**本轮不引入 Alembic**。
- `ddl_check.py`：对每个表 `CreateTable(t).compile(dialect=OracleDialect())`，与 `V001` 归一化后字符串比对（忽略大小写/空白/`CHAR` 语义差异），不一致即失败。**无 DB I/O**。
- 应用方式（写入 `db/oracle/ddl/README.md`）：先用 SQLcl 在 `zvp5adb2` 应用并验证，再上 `zvp5adb`；每个对象用 `EXECUTE IMMEDIATE` + `-955` 容错实现幂等。

## 5. 数据访问层布局与开关

```
db/
  models.py        # Core Table 镜像
  ids.py           # new_oid() / normalize_oid()
  ddl_check.py     # 离线漂移校验
  repos/
    __init__.py
    protocols.py   # UsersRepo / GroupsRepo / ... (typing.Protocol)
    mongo/*.py     # 现状 motor 查询的“纯搬运”适配器
    oracle/*.py    # SQLAlchemy Core 实现
  backends.py      # 仓库工厂 + 模块级开关
  oracle/          # 已有脚手架（config/engine/session/ddl）
```
- 开关：`settings.ORACLE_MODULES: list[str] = []`（合法值 users/groups/activities/activity_members/time/stats/logs）；`db/backends.py` 按模块决定返回 Mongo 还是 Oracle 实现。
- 事务：一个写方法 = 一次提交；读不提交。日志写入在 logs 模块迁移前**仍走遗留 Mongo**，且不入业务事务。
- 健康检查：仅当 `ORACLE_ENABLED` 时，在 `/api/health` 追加 `SELECT 1 FROM DUAL`。
- 单进程单引擎；prod/test 仅靠 `settings` + wallet 目录区分。

## 6. 首个纵切（users + groups/auth）范围

移植（zvms_new 读写）：`util/cert.py`（登录/checkpwd）、`users_router.py`（id 解析/profile/改密）、`users_v3_router.py`（CRUD/roles/重置口令）、`util/user.py`（显示名 zvms_new 侧）、`util/permission/user.py`、`users_v2_router.py`、`groups_v3_router.py`、`groups_v2_router.py`（仅 class 侧）。
暂留 Mongo（遗留语义，phase-out 轮再处理）：`util/group.py::is_in_a_same_class`、`users_router.py` 双库合并的遗留半、`get_class.py`、past/notifications、`groups_v2_router.py` 遗留成员路径。
仓库方法最小集见 §2 表 + 上游设计（UsersRepo: get_by_oid/find_by_business_id/list_page/create/set_password/set_roles/set_class/get_names；GroupsRepo: get/list_classes/create/rename/delete/members_page）。

## 7. ETL 轮廓（本轮不执行）

`db/etl/mongo_to_adb.py`（async，`--dry-run` 默认、`--only`、`--batch 500`、`--active prod|test`）。
顺序：groups→users→activities→activity_members→user_time→time_academic_year→time_with_origin→time_with_origin_stat→indicators→group_indicators→daily_exports→logs。
读：motor `find({}).batch_size(500)`，仅 `zvms_new`。写：批量 `MERGE`（幂等）。
护栏（不合规即隔离到 `etl_quarantine.jsonl`，不强制）：非 hex id、越界枚举、`activity_members` 三键重复、FK 父缺失、文本超长。
**重映射**：`group_indicators.group` 与 `time_with_origin_stat.scope_id(group)` 现为遗留 group id → 推荐**不 ETL 这两张**，由移植后的夜间重算重建（本来就是整体重写）。

## 9. 路由接线规范（P2 接线轮）

- **工厂**（扩展 `db/backends.py`）：
  - `get_users_repo(session=None) -> UsersRepo`、`get_groups_repo(session=None) -> GroupsRepo`。
  - `oracle_enabled_for(domain)` 为真 → `OracleUsersRepo(session)` / `OracleGroupsRepo(session)`；为假 → `MongoUsersRepo()` / `MongoGroupsRepo()`。
- **调用方式（最小侵入，不改端点签名）**：`repo = backends.get_users_repo()` 后直接 `await repo.xxx()`。Oracle 仓储在 `session=None` 时自持 session（`session_scope`），Mongo 适配器无需 session。
- **接线文件清单（slice 1）**：
  - users 侧：`util/cert.py`、`util/user.py`、`util/permission/user.py`、`routers/users_router.py`、`routers/users_v3_router.py`、`routers/users_v2_router.py`
  - groups 侧：`routers/groups_v3_router.py`、`routers/groups_v2_router.py`（class 侧）
- **语义一致性（Mongo 适配器与 Oracle 仓储必须一致）**：
  1. `re.escape` 由仓储内部执行（复刻 Mongo `$regex` 字面 contains）。
  2. 空 query → 不加过滤（Mongo 空正则匹配全部；Oracle `REGEXP_LIKE(x,'')` 不匹配任何行）。
  3. 排序以 `id`（business_id，UNIQUE）为准；`oid` 兜底可有可无（唯一键已确定序）。
  4. `class_id` 缺失 → `""`；`class_name` 缺失 → `"未知班级"`；`get_by_oid` 中可选列（class_id/session_id/sex/eligibility/past）为 NULL 时**省略键**（保留 `"x" in user` 语义）；`password` 恒存在。
  5. 行形状 = 聚合 `$project` 形状：`list_page` → `{_id,id,name,roles,class_id,class_name}`（不含 `group`，由 router 补）；`members_page` 额外含 `password`；`list_classes` → `{_id,name}`。
- **验证门槛**：flag 关闭时 `smoke.py` 必须 **34/34**；`compileall` + import 全绿；逐端点人工比对 repo 路径与原 inline 代码。

## 10. 收敛后单库模型的差异（2026-10-12 记录，待对齐）

对方会话已完成 `zvms` 遗留库 phase-out，世界收敛为**单库 `zvms_new`**（依据其 e2e seed 的 world model 注释，production-verified）。这相对本规格的 V001 有以下差异，**待共享 schema 文件（`models.py`/`V001`）空闲时对齐**（当前它们正被对方编辑，避免并发覆盖）：

| 差异 | 现状 V001 | 收敛后需要 |
|---|---|---|
| `groups` | 仅 `OID,NAME`（class only） | 增加 `TYPE`（class 为 NULL / `permission`）与 `PERMISSIONS`（JSON 数组：secretary/department/admin） |
| `app_logs` | 无（原 `zvms.logs` 在遗留库） | **新增表** `APP_LOGS(url,user,data,timestamp,type,level)` —— 运行期日志集合 |
| `zvms_new.logs`（设备 schema） | V001 有 `LOGS` 表 | 标为死集合（对方 seed 明示「never used」）→ 可保留为归档或删除 |
| `tasks`/`imports`/`notifications` | 原在遗留 `zvms`，未建模 | 移入 `zvms_new`，**需新增表** |
| `changelog` | 无 | 对方已加入 `models.py`/`V001`（`ChangelogRepo` 域） |

> 影响：`GROUPS` 若承载 permission 组，则 `GroupsRepo` 的 `list_classes` 需按 `type` 过滤；`USERS.CLASS_OID` 的 FK 只能指向 class 组。
> 行动：待对方 schema 编辑停止后，(a) 把上述增量并入 `V001`/`models.py` 并跑 `ddl_check`，(b) 若采用版本化，则新开 `V002__single_db_converged.sql`。

## 8. 风险（按严重度）

1. **ID 命名空间重映射**（遗留 id 混入目标集合）—— 见 §7。
2. **墙钟 vs UTC**：全程朴素时间，禁 TZ 换算、禁会话时区。
3. **Oracle 标识符折叠/保留字**：靠 §3 映射，漏改即硬错。
4. **VARCHAR2 CHAR 语义 + CJK**：超长走 ETL 护栏，不静默截断。
5. **JSON 索引诱惑**：V001 不建 JSON 函数索引（2.5k 行级先不用）。
6. **异步池 vs ADB**：`pool_pre_ping` + `pool_recycle` 已有；单进程单引擎。
7. **增量期双写漂移**：策略是**单后端/模块**（开关同时切读写），绝不双写。
8. **字符串 id 大小写/合法性**：仓库边界统一 `normalize_oid`。
9. **ETL 期数据完整性缺口**：护栏报告不强制。
10. **logs 双重性**：见 LOGS 表下方策略提示。
