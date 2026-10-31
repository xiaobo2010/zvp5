# ZVP5 · 项目总览与开发状态

> **2026-10-29 状态更新**：运行时数据库为 **Oracle Autonomous Database**（MongoDB 已退役，下文 §三 仅保留为历史对齐依据）；AI 能力统一命名为 **ZVAgent**（原「智能体 / AI 助手」）；安全面已收口（内容审核 fail-closed、登录与重查询频控、管理写操作再认证、文件根目录 jail）。能力清单见 §九，细节契约见 `zvms4-modified-main/docs/` 与 `zvms4-modified-main/zvms4-backend-python/README.md`。

> 本文件是 `xiaobo2010/zvp5` 仓库的**权威文档**，由以下四份原零散文档合并而成（内容**全文保留**于对应章节）：
>
> | 原文件 | 并入章节 | 原文件去向 |
> |---|---|---|
> | `zvms4-modified-main/README.md` | §二 开发主线：zvms4-modified-main | zzj 整合时删除；**2026-10-12 主干合并后保留**（见 §七） |
> | `zvms4-modified-main/DB_ALIGNMENT.md` | §三 数据库对齐（Ground Truth） | 已删除（内容即本节） |
> | `zvms4-modified-main/STATUS.md` | §四 模块移植与安全审计记录（Part 1-7） | zzj 整合时删除；**2026-10-12 主干合并后保留**（Part 1-11 全文，见 §七） |
> | `zvp-latest/DEV_STATUS.md` | §五 zvp-latest 开发状态 | 已删除（内容即本节） |
>
> 正文中出现的 `STATUS.md` / `DB_ALIGNMENT.md` 等文件名，即指上述对应章节；「本文件」等自称亦相应指本章节。
>
> **注意**：本文件 §四 仅覆盖到 **Part 7**；主干 2026-10-12 合并 `zvp5-dev` 后带入的 **Part 8-11** 全文见保留的 `zvms4-modified-main/STATUS.md`（概览见 §七）。

---

## 一、仓库总览

`zvp5` 是镇海中学义工管理平台（ZVMS / ZVP）的私有开发仓库，预计前端 v4.2.0（v5 前瞻版本）正式版实现后开源。仓库含开发主线和一份参考归档：

| 目录 | 角色 | 说明 |
|---|---|---|
| `zvms4-modified-main/` | **开发主线（我们开发的版本）** | 基于 **zvms4 正式版**（上游 `zvms/zvms4-backend-python` + `zvms/zvms4-frontend`）修改。数据库层向现役 zvp 的 MongoDB Atlas 真实结构对齐，并**逐模块从 zvp-latest 迁移功能与数据结构**（义工管理、用户管理 / 登录、日志、鉴权），每个移植面均经安全审计。见 §二。 |
| `reference/` | **参考归档（只读）** | 原 `zvp-latest` 与 `zvp5-gitlab` 已于 2026-10-24 删除；学海 API 整份留档于 `reference/xuehai/`，独立审核流/FAQ/资源/定向 WebSocket 设计留档于 `reference/zvp5-gitlab/`。**不得参与构建或导入主线。** |

> 两套参考树与主线的逐端点 / 逐页面 / 功能面对账，以及删除结果，见
> `zvms4-modified-main/docs/REFERENCE_TREES_ALIGNMENT.md`（2026-10-24）。

### 安全声明（重要）

- 原 `zvp-latest` 代码**存在后门与漏洞**；原树已删除，仅把学海 API 等拒绝面原样归档在 `reference/xuehai/`。归档**不得运行、不得导入主线**。
- 已迁移模块的审计结论与拒绝面（设备采集、用户绑定、外链跳转、学海代理、设备会话等）见 §四。
- 本仓库**不声称任何模块「已审计安全」**；既有审计均为**静态验证**，上线前需在有 Atlas 连接的环境跑一次运行时冒烟。

### 技术栈

| 层 | 栈 |
|---|---|
| 后端 | FastAPI + SQLAlchemy 2.0 async + Pydantic（socket.io / APScheduler） |
| 前端 | Vue3 + Vite + TypeScript + Element Plus + Pinia |
| 数据库 | Oracle Autonomous Database（ADB，python-oracledb thin 模式），唯一运行时数据库；§三 的 MongoDB 双库为历史对齐依据 |

### 文档地图

- §二 开发主线：zvms4-modified-main —— 目录结构、代码来源、DB 对齐、配置运行、已知事项
- §三 数据库对齐（Ground Truth）—— 现役两库真实结构 + 逐条对齐改动清单
- §四 模块移植与安全审计记录 —— Part 1 义工管理 / Part 2 用户管理与登录 / Part 3 日志 / Part 4 登录与鉴权 / Part 5 全量 API 盘点 / Part 6 弱密码强制改密 / Part 7 token 有效期（**Part 8-11 见保留的 `zvms4-modified-main/STATUS.md`**）
- §五 zvp-latest 开发状态 —— 后端 API 与前端页面完成度对账（P0-P2 已清零）
- §六 zvp5-gitlab 开发进度对齐 —— 目录清单 / 完成度 / 不可运行性证据 / 与主线功能面照
- §七 主干合并与 ADB 处置记录 —— `zvp5-dev` 并入主干（Part 8-11）/ Oracle ADB 适配排除 / 与 zzj 文档整合线合流


---

## 二、开发主线：zvms4-modified-main

> 基于上游 **zvms4**（`zvms/zvms4-backend-python` + `zvms/zvms4-frontend`）的定制版本。
> **改动方向一：数据库层向现役 zvp 平台的真实结构对齐。**
> **改动方向二（2026-10-09 起）：义工管理模块（创建义工 / 添加修改义工成员）按指令从现役 zvp 移植，经逐文件安全审计放行，见 `STATUS.md` Part 1。**
> **改动方向三（2026-10-10 起）：用户管理与登录（登录语义补齐 / 用户·班级 v2/v3 API / 设备-绑定-外链三类禁止面清除）按指令从现役 zvp 移植，经全树终局 grep + 写后自审放行，见 `STATUS.md` Part 2。**
> **改动方向四（2026-10-10 起）：日志 API 移植与汉化（type/level 业务语义 + 35 站点全面行为记录 + 管理端过滤），日志端点 GET-only 防篡改，并复查清除历史残面，见 `STATUS.md` Part 3。**
> **改动方向五（2026-10-10 起）：登录与身份验证 API 移植（TOTP 动态码按用户派生 / 重置为初始密码 v3 / 登出与改密语义修复），登录/鉴权路径零外呼，见 `STATUS.md` Part 4。**
> **改动方向六（2026-10-11 起，2026-10-12 主干合并带入）：后端可用状态落地——shield 请求加固中间件按 zvp 语义改写移植（剥离设备会话/学海面），运行缺口修复（依赖缺失 / example 配置字段缺失 / RSA 1024 / 关停竞态），并完成真实运行时端到端验证（41/41 断言），见 `STATUS.md` Part 8。**
> **改动方向七（2026-10-12 起，2026-10-12 主干合并带入）：双库断裂面系统性收敛——群组与用户读取全面双库合并（权限组列表与成员、用户搜索去重、roles 合并、班级选择器修复），PWA SW 即时接管修复旧客户端首页 404，见 `STATUS.md` Part 10。**
> 其余前端 UI 与后端业务逻辑仍以上游 zvms4 为基准，不采用 zvp 的代码（zvp 代码经检查存在漏洞与后门，仅提取其数据结构；义工管理与用户管理移植面经扫描确认无外链 / 外呼 / 越权代码，范围外危险代码一律未引入，上游自带的设备采集 / 外呼代码已在用户管理面上清除）。

---

### 1. 目录结构

```
zvms4-modified-main/（入库目录名；本地目录名 zvp-modified-v4）
├── zvms4-backend-python/     # FastAPI 后端（上游 zvms4，DB 层已对齐；模块按 zvp 移植）
│   ├── README.md
│   ├── requirements.txt
│   ├── config/               # supervisor.conf / nginx 样例
│   └── ...
└── zvms4-frontend/           # Vue3 + Vite 前端（上游为基准；仅部分 API 接线改动，见 §四 Part 1 §2）
```

> 原 `zvms4-modified-main/STATUS.md`（模块移植与安全审计）与 `zvms4-modified-main/DB_ALIGNMENT.md`（数据库对齐 Ground Truth）已并入本仓库根 `README.md`，分别见 §四 / §三。

> **入库路径**：本目录在 GitHub 仓库 `xiaobo2010/zvp5` 中对应 `zvms4-modified-main/`（本地目录名 `zvp-modified-v4` 与入库目录名不同，同步时注意映射）。

### 2. 这份代码从哪来、为什么

| 部分 | 来源 | 说明 |
|---|---|---|
| 前端 | 上游 zvms4 为基准 + zvp 接线 | 仅义工管理与用户管理 API 接线改动（见 `STATUS.md` §2 / §9），UI 与模板未动 |
| 后端逻辑 | 上游 zvms4 为基准 | 除下述义工管理与用户管理移植面外，业务逻辑、路由、权限均保持 zvms4 |
| 义工管理 v3 层 | **现役 zvp**（2026-10-09 移植） | `routers/activities_v3_router.py` 四端点 + 权限修复，逐文件安全审计后放行，见 `STATUS.md` Part 1 |
| 用户管理与登录 v2/v3 层 | **现役 zvp**（2026-10-10 移植） | users/groups v2 列表·详情 + v3 建/改角色/建班/成员进出 + 登录默认密码与 profile，见 `STATUS.md` Part 2；设备 / 绑定 / 外链三类禁止面未迁移，上游同源残留已清除 |
| 日志系统 | **现役 zvp**（2026-10-10 移植） | logs API 业务面（过滤/分页/user_name 联查）+ type/level 语义 + 35 站点汉化与补埋点 + GET-only 防篡改，见 `STATUS.md` Part 3 |
| 登录与身份验证 | **现役 zvp**（2026-10-10 移植） | TOTP 动态码登录（按用户派生，RFC6238 本地自实现）+ 重置为初始密码 v3 端点 + 登出/改密语义修复；设备绑定 / 会话指纹 / 外呼投递 / recovery 问答拒绝移植，见 `STATUS.md` Part 4 |
| 后端数据库层 | 向**现役 zvp** 对齐 | 改动见 §4 |

**zvp（现役平台）贡献「数据在库里长什么样」**：包名、集合名、字段名 / 类型。除经安全审计放行的义工管理移植面外，它的代码（含后门）一律不引入。

### 3. 现役数据库结构（权威依据）

现役 zvp 连接的是 **MongoDB Atlas 远程集群**，使用两个库：

- **`zvms`**（遗留库）：`users`(`id,name,sex,group[],password,past`)、`groups`(`name,description,permissions[],type`)、`tasks`、`logs`、`imports`、`notifications`；`activities` 为空。
- **`zvms_new`**（现役主库）：`users`(`id,name,roles[],class_id,password,session_id`)、`groups`(`name`)、`activities`、`activity_members`、`time`、`time_academic_year`、`time_with_origin`、`time_with_origin_stat`、`indicators`、`group_indicators`、`daily_exports`、`logs`。

完整字段 / 类型 / 文档数、以及「哪些端点用哪个库」的规律，见 **`DB_ALIGNMENT.md`**（由 SSH 只读 dump 建立，是目前唯一权威依据）。

### 4. 本次对齐做了什么

**规则**：v2 / v3 与 exports / imports、登录鉴权路径统一改用 `zvms_new` 的新 schema；v1（用户列表、班级 v1）与 `util/*` 保持遗留 `zvms.*` —— 与现役 zvp 的集合归属完全一致。

- **查询层**：5 个文件 11 处集合目标切换，含字段映射（旧 `user["group"]/sex/past` → 新 `roles/class_id`）。逐条见 `DB_ALIGNMENT.md` §5。
  - `util/cert.py`（登录鉴权读/写切到 `zvms_new.users`，权限取 `roles`）
  - `routers/users_router.py`（改密端点）
  - `routers/users_v2_router.py`
  - `routers/exports_router.py`（用户/班级来源切换，班级按 `class_id`）
  - `routers/imports_router.py`
- **模型层**：`typings/group.py` 的 `UserPosition` 枚举删去实库与 zvp 均不存在的 `auditor/system/inspector`。
  - `typings/activity_v2.py` **保持上游严格版本**（`datetime` + 非空）：实库 `activities` 的日期为 BSON `Date` 且零 null，若改用 zvp 的 `int`/可空版本会让 `Activity.model_validate` 直接失败。见 `DB_ALIGNMENT.md` §6。
- **未改动**：`database.py`（与 zvp 逐字节相同，已连两库）、其余业务逻辑。前端本次改动仅为义工管理 API 接线（见 `STATUS.md` §2），模板 / 样式未动。

验证：`python3 -m compileall zvms4-backend-python` 通过；改动集中在上述 6 个文件。

### 5. 配置与运行

#### 5.1 配置

```bash
cp zvms4-backend-python/settings.example.py zvms4-backend-python/settings.py
```

`settings.py` 填写：

- `MONGODB_URI`：指向现役数据所在的 MongoDB（当前为 Atlas 集群）。
- `MONGODB_DB`：按上游约定留空即可；`database.py` 固定连接 `zvms` 与 `zvms_new` 两个库。

此外后端启动依赖同目录下的密钥文件（上游生成脚本）：

- `generate_key.sh` → 生成 `rsa_public_key.pem` / `rsa_private_key.pem` / `aes_key.txt`。

> 以上文件均属敏感凭证，禁止提交进版本库。

#### 5.2 运行后端

```bash
cd zvms4-backend-python
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
# 生成密钥后
uvicorn main:app --host 0.0.0.0 --port 8000
```

`config/` 内含 `supervisor.conf` 与 `nginx` 样例，可用于生产托管（路径按实际部署调整）。

#### 5.3 前端

前端保持上游 zvms4，按上游方式构建（`npm install` / `npm run build`），产物由 Nginx 或后端静态目录托管。

### 6. 已知事项 / 待办

- ~~**zvp 代码待审核**：本次只吸收了 zvp 的数据结构，未采用其任何业务代码。~~ → **义工管理面已审计并移植（2026-10-09）**：v3 四端点 + 权限修复，逐文件扫描确认无外链 / 外呼 / 越权代码，范围外危险代码（学海代理 `xh_router` / `api/xh.js`、平台登录 `platform.py`、ManageHome 外站跳转、外部图标 URL）均未引入，审计记录见 `STATUS.md`。其余 zvp 模块（安全中间件、其他 v3 路由等）若要参考仍需单独安全审计。
- **用户管理与登录已审计移植（2026-10-10）**：登录语义补齐（默认密码 / profile / 5min 临时 token）+ users/groups v2/v3 端点 + 前端接线；zvp 的 platform 登录（TOTP / sid / 设备核验）与 `xh_router` 全部拒绝移植；上游自带的设备采集（`binding_user_credentials` / 三请求头 / `deviceId` 生成）已从用户管理面清除。roles 编辑后端端点已就绪、前端入口待接（见 `STATUS.md` Part 2 §14-3）。
- **日志 API 已审计移植（2026-10-10）**：`GET /api/logs` 重写（query/type/level/performer/时间过滤 + user_name），schema 加 `type/level`，35 站点全中文 + 13 处补埋点（成员移除/记录修改/活动信息/v1 用户组/导出全套）；zvp 的设备筛选参数与 `_1.py` 删库脚本拒绝移植；复查清掉 6 处历史残面（含 `get_export_file` 无鉴权下载修复）。
- **登录与身份验证已审计移植（2026-10-10）**：TOTP 动态码登录（密码优先/OTP 兜底、按用户派生 secret、RFC6238 官方向量 6/6 验证）+ `DELETE /v3/users/{id}/password` 重置为初始密码（临时 token 重新验证）+ admin 动态码查看端点（`totp_view` warning 日志）+ 登出清 token / 改密死期望两处语义修复；zvp 的设备绑定、`zvp_session` 指纹、`sid` claim、recovery 问答、`/v1/platform/*` 平行端点拒绝移植（见 `STATUS.md` Part 4）。
- **zvp 全量 API 面盘点落档（2026-10-10）**：13 router ~75 端点穷举对照——已移植/等价占绝大多数，未移植总账五项（**服务端弱密码强制改密为首要候选**），四个勘察盲区模块（通知/邮件/推送/socket.io/定时任务等）确认 zvp 零实现，反向确认本仓能力更全（socket.io / apscheduler / notifications / statistics / mcp 独有）；红线拒绝面第 5 次全量复核维持（见 `STATUS.md` Part 5）。
- **服务端弱密码强制改密（2026-10-10，总账 #1 已闭）**：`/api/users/auth` 弱密码 long 请求仅发 5 分钟临时 token + `weak_password` 标记，强度规则与前端逐字镜像（独立测试 10/10）；short 改密流程与 OTP 路径不受影响（见 `STATUS.md` Part 6）。
- **`categories` / `debug_descriptions`**：`conversion/groups.py`、`tasks/categorize.py` 会用到，但现役库中不存在、zvp 也无对应代码。属 zvms4 独有的一次性/调试集合，首次写入 MongoDB 会自动创建；如需正式支持需另行建表。
- **`groups_v2_router.py` 成员读取、`times_router.py`**：仍用遗留 `zvms.users`，与现役 zvp 行为一致，故保留。
- **索引**：现役两库除 `_id_` 外无任何自定义索引；本 fork 未新增索引，性能问题按需另行评估。


---

## 三、数据库对齐（Ground Truth）

> 数据来源：2026-10-09 通过 SSH 登录 `zgovps-la`，读取 `/www/zvp/backend/settings.py` 的 `MONGODB_URI`（MongoDB Atlas 远程集群），用 `mongosh` **只读** dump。
> 采集方式：`listDatabases` + `getCollectionNames` + `findOne()` 字段/类型采样 + `getIndexes()`。
> 本文件是「数据库结构」的唯一权威依据。代码侧推断一律以此为准。

### 0. 连接与库

- 现役 zvp 后端连的是 **MongoDB Atlas 远程集群**（`ac-lmlqdgx-shard-00-01.r51xm9t.mongodb.net`），**不是** zgovps-la 本机的 mongod（本机 mongod 上的 `zvms_new` 只有 2 张空表，与现役无关）。
- `settings.py` 同时定义 `MONGODB_URI` 与 `MONGODB_DB="zvms"`，但 `database.py` 硬编码连接两个库：`zvms` + `zvms_new`。
- 实际存在数据的库：`zvms`（遗留）与 `zvms_new`（现役主库）。
- 除默认 `_id_` 外，**没有任何自定义索引**（含 `zvms.logs` 的 `timestamp_-1_autocreated`）。

### 1. `zvms`（遗留库）

| collection | 文档数 | 字段（采样类型） |
|---|---|---|
| `users` | 1781 | `_id:object, id:string, name:string, sex:string, group:array<string>, password:string, past:array` |
| `groups` | 40 | `_id:object, name:string, description:string, permissions:array<string>, type:string` |
| `activities` | **0（空）** | — |
| `tasks` | 1 | `_id:object, id:object, status:string, format:string, variant:string, export_start:null, export_end:null, task_start:Date, task_end:null, percentage:number, errmsg:string, result:array<object>, allow_cache:boolean, include_description:boolean` |
| `logs` | 97138 | `_id:object, url:string, user:string, clarity:string, data:string, ip:string, timestamp:number` |
| `imports` | 1 | `_id:object, name:string, filename:string, description:string, user:string, date:string, content:object` |
| `notifications` | **0（空）** | — |

### 2. `zvms_new`（现役主库）

| collection | 文档数 | 字段（采样类型） |
|---|---|---|
| `users` | 1781 | `_id:object, id:string, name:string, roles:array, class_id:object, password:string, session_id:null` |
| `groups` | 37 | `_id:object, name:string` |
| `activities` | 380 | `_id:object, type:string, name:string, description:string, date:Date, createdAt:Date, updatedAt:Date, appointee:string, approver:string, creator:string, status:string, place:string, origin:string` |
| `activity_members` | 13076 | `_id:object, member:string, activity:string, status:string, mode:string, duration:number` |
| `time` | 1785 | `_id:object, user:string, on_campus_raw:number, off_campus_raw:number, social_practice:number, updated_at:Date` |
| `time_academic_year` | 1780 | `_id:object, user:string, entry:string, academic_year:number, start_of_year:Date, end_of_year:Date, on-campus:number, off-campus:number, social-practice:number` |
| `time_with_origin` | 1807 | `_id:object, user:string, data:object, updated_at:Date` |
| `time_with_origin_stat` | 154 | `_id:object, id:string, scope:string, scope_id:string, require_percentage:number, data:object, updated_at:Date` |
| `group_indicators` | 55 | `_id:object, group:string, percentiles:object, indicators:object, updated_at:Date` |
| `indicators` | 1 | `_id:object, percentiles:object, indicators:object, updated_at:Date` |
| `daily_exports` | 4 | `_id:object, created_at:Date, status:string, data:array` |
| `logs` | 6792 | `_id:object, xuehai_id:number, net_session:object, request:object, user:object, type:string, level:string, summary:string, timestamp:object` |

### 3. 两库并存的关键点（对齐时必须理解）

- **users 两套并存**：`zvms.users`（`sex`/`group`/`past`，遗留 v1 用）与 `zvms_new.users`（`roles`/`class_id`/`session_id`，现役 v2/v3 用），均 1781 条。
- **groups 两套并存**：`zvms.groups`（`name`/`description`/`permissions`/`type`，遗留）与 `zvms_new.groups`（仅 `name`，现役）。
- **activities 只在 `zvms_new`**：`zvms.activities` 为空；所有活动数据在 `zvms_new.activities` + `zvms_new.activity_members`。
- **统计/时长**：`time` / `time_academic_year` / `time_with_origin` / `time_with_origin_stat` / `group_indicators` / `indicators` / `daily_exports` 全部在 `zvms_new`。
- **tasks / imports / 遗留 logs / notifications** 在 `zvms`。
- **`zvms_new.logs`** 是另一套日志（`xuehai_id`/`net_session`/...），与 `zvms.logs` 结构完全不同。

### 4. 现役 zvp 代码的集合归属规律（`~/zvp-build-test/backend`）

- **v1**（`users_router.py`、`groups_router.py`、`util/get_class.py` 等）→ 遗留 `zvms.*`
- **v2 / v3**（`users_v2_router.py`、`groups_v2_router.py`、`activities_v2/v3_router.py`）→ `zvms_new.*`
- **`exports_router.py` / `imports_router.py`** → 用户/班级已用 `zvms_new.*`，任务表仍 `zvms.tasks`
- 例外：`users_router.py` 中「更新用户角色」类端点用 `zvms_new.users`

详见下方改动清单。

### 5. 查询层改动清单（zvms4 → zvp 对齐，已实施）

改动规则：**v2/v3 与 exports/imports、登录鉴权路径统一改用 `zvms_new` 的新 schema；v1（users_router 列表、groups_router）与 util 保持遗留 `zvms.*`**，与现役 zvp 的集合归属一致。
所有改动均在 `zvp-modified-v4/zvms4-backend-python`，已 `python3 -m compileall` 通过。

| # | 文件:行 | 改动 | 依据 |
|---|---|---|---|
| 1 | `util/cert.py:95` | `db.zvms.users` → `db.zvms_new.users`（`validate_by_cert` 登录查库） | zp `cert.py:116` 用 `zvms_new.users` |
| 2 | `util/cert.py:106` | 权限来源 `await get_user_permissions(user)` → `user.get("roles", [])` | 新 schema 无 `group`，角色直接在 `roles`；zp `cert.py:127` 同 |
| 3 | `util/cert.py:84` | `get_renewed_password` 写库 → `zvms_new.users` | 与登录读库保持一致（zp 此处遗留库，为其自身不一致，按 live 纠正） |
| 4 | `util/cert.py:122` | `checkpwd` 查库 → `zvms_new.users` | 同上，保持口令单一权威源 |
| 5 | `routers/users_router.py:168` | `change_password` 取 target → `zvms_new.users` | zp `users_router.py:182` 用 `zvms_new.users` |
| 6 | `routers/users_router.py:177` | `change_password` 改密写库 → `zvms_new.users` | zp `users_router.py:191` 同 |
| 7 | `routers/users_v2_router.py:156` | `get_user_time_statistics_v2` 取 `id` → `zvms_new.users` | zp v2 用户查询走 `zvms_new.users`；字段 `id` 存在 |
| 8 | `routers/exports_router.py:57,136` | 导出遍历用户 → `zvms_new.users` | zp `exports_router.py:57,136` 用 `zvms_new.users` |
| 9 | `routers/exports_router.py:87,131` | 班级来源 `zvms.groups`（按遗留 `user["group"]`）→ `zvms_new.groups`（按 `user["class_id"]`） | 新 schema 用 `class_id`；zp 亦用 `zvms_new.groups`（但其仍读 `user["group"]`，属 zp bug，此处按新 schema 正确映射） |
| 10 | `routers/imports_router.py:112` | Excel 导入查用户 → `zvms_new.users` | zp `imports_router.py:112` 同 |
| 11 | `typings/group.py` `UserPosition` | 删除 `auditor/system/inspector`，对齐 zp | 实库 `zvms.groups.permissions` 仅 `[admin,department,secretary,student]`；zp 亦无此三角色 |

### 6. 模型层结论（未改动的部分及依据）

- `database.py`：与 zp **逐字节相同**，已连 `zvms` + `zvms_new`，无需改动。
- `typings/activity_v2.py`：**保留** zvms4 的 `datetime` + 非空版本，**不采用** zp 的 `int`/可空版本。依据：实库 `zvms_new.activities` 的 `date/createdAt/updatedAt` 为 **BSON Date**（`typeof=object`），且全字段 **null 数=0**；而 `activities_v2/v3_router` 会对实库文档执行 `Activity.model_validate(...)`，若改成 `int`/可空会直接校验失败。zp 的 int typing 与其自身数据不一致，属 zp 侧历史遗留。
- 其余 `typings/*` 两侧完全一致。

### 7. 与 live 结构相关、但未纳入本次对齐的项（待确认）

- `categories`（`conversion/groups.py`）、`debug_descriptions`（`tasks/categorize.py`）：**live 中不存在**，且 zp 无此代码。属 zvms4 独有的一次性/调试脚本所用集合，MongoDB 首次写入会自动创建；如需正式支持需另行建表。
- `routers/groups_v2_router.py` 的成员读取仍用遗留 `db.zvms.users`（`"group": group_id` + `past`）：与 zp 的 groups_v2 行为一致，故保留。
- `routers/times_router.py`（zvms4 独有）：读 `db.zvms.users`，因 zp 无对应端点、且其与 v1 列表语义一致，暂保留遗留库。

### 8. 记录

- 2026-10-09：基于 zgovps-la 现役 Atlas 集群只读 dump 建立本 ground truth；完成查询层 5 文件 11 处对齐 + `typings/group.py` 枚举对齐；`compileall` 通过。


---

## 四、模块移植与安全审计记录（Part 1-7）

### Part 1 — 义工管理移植（2026-10-09）

> **Part 1 · 义工管理** | 日期：2026-10-09
> 任务：把现役 zvp（`~/zvp-build-test`，zvp-latest）的**义工管理**（创建义工 / 添加、修改义工成员）移植进本仓库，并对移植面做「外链 / 翻出平台」危险代码审计。
> 审计方式：静态扫描（grep / AST）+ 全量人工读码 + 独立安全复核；未运行服务、未连接数据库。

---

#### 1. 后端端点对齐总表（`zvms4-backend-python`）

| 端点 | zvp 侧能力 | 本仓处理 | 状态 |
|---|---|---|---|
| `GET /api/v3/activities` | 全校列表：分页 / 关键词 / 类型 / 状态过滤 + creator 联表返回 `creator_name` | 新增（`routers/activities_v3_router.py:22`） | 已移植 |
| `POST /api/v3/activities` | 简化创建：body `{name, description, type}` → 201 `{id}` | 新增（:156）；适配见 §3 | 已移植（适配） |
| `GET /api/v3/activities/{aid}/members` | 快速成员列表：`$lookup` `zvms_new.users` 返回 `member_name` | 新增（:197） | 已移植 |
| `POST /api/v3/activities/{aid}/members` | 批量加成员：预校验类型匹配、去重（同 member+mode 时长累加）、插入置 `status=effective`、活动回置 `pending`、204 空体 | 整体替换上游旧实现（:312）；body 换 `NewActivityMember {member, mode, duration>0}` | 已移植（替换） |
| v2 全套（创建 / 详情 / 删除 / 成员增删查 / 改时长 / 改状态 / 改信息） | 与上游等价 | 上游已有，`routers/activities_v2_router.py` 未动 | 已存在 |
| `POST /api/imports/activities`（Excel 导入义工） | — | 与 zvp 侧**逐字节相同**（diff 验证） | 已一致，未动 |

#### 2. 前端接线（`zvms4-frontend`，最小改动，UI 设计未动）

| 文件 | 变更 |
|---|---|
| `types/activity.v2.d.ts:34` | 新增 `NewActivityMember {member, mode, duration}`（经 `types/v2.ts` `export *` 自动再导出） |
| `src/api/activity/member.ts:18` | `insertMany` 改造：body 换 `NewActivityMember[]`，适配 204 空体（删掉响应解析） |
| `src/api/activity/member.ts:60` | 新增 `reads_v3`（GET `/v3/activities/{aid}/members`，返回含 `member_name`） |
| `src/api/activity/read.ts:66,99` | 新增 `campus_v3`（GET `/v3/activities`，含 `creator_name` / `total`） |
| `src/components/activity/ZActivityMemberList.vue:174` | 成员列表 `reads` → `reads_v3`（v2 路径在实库上会**漏掉不在 legacy `zvms.users` 里的成员**，v3 直连 `zvms_new.users`） |
| `src/components/activity/ZActivityMemberList.vue:266` | 批量添加构造 `{member, mode, duration}`（去掉 `_id/activity/status`） |
| `src/components/activity/getActivity.ts:26` | campus 分支按角色条件切换：admin/department → `campus_v3`；学生保持 v2 兜底（v3 有 403 门） |

单条加成员（`member.insert` → v2）、`searchActivity`、模板与样式：未动。

#### 3. 与 zvp 的有意适配（差异清单）

| # | zvp 原样 | 本仓适配 | 原因 |
|---|---|---|---|
| 1 | `date/createdAt/updatedAt` 写 **int 毫秒时间戳** | 写 `datetime.now()`（BSON Date） | 实库 `activities` 日期为 BSON `Date` 且零 null（DB_ALIGNMENT §2/§6），严格 typing `Activity.model_validate` 要求 |
| 2 | v3 创建只插 7 个字段 | 补齐 `appointee=creator`、`approver=""`、`place=""`、`origin="activities"` | 实库全字段非空，缺字段会让 v2 读取路径 `model_validate` 崩 |
| 3 | `user.require('admin','department')`（CurrentUser 对象） | `"admin"/"department" in user.get("per", [])`（本仓 dict 风格） | 两侧鉴权层不同，语义等价 |
| 4 | `db.zvms_new.activities` 属性访问 | `db.zvms_new.get_collection("activities")` | 本仓集合访问惯例 |
| 5 | 创建日志带 level/type 参数 | 仅 `with_text` | 本仓 `inject_log` 不支持 level/type |
| 6 | 校验失败 detail 文案「权限不足」（volunteer_member 侧） | 「Permission denied」 | 与同文件上游 volunteer.py 文案一致；行为（抛 403）一致 |

#### 4. 权限对齐（本次根因修复，含一个上游旁路封堵）

**根因**：JWT `per` 由 `util/cert.py:106` 从 `zvms_new.users.roles` 签发，词汇为 `student/secretary/department/admin`；而上游权限助手查 `user.get("perm")` / `"volunteer"`——对现役库**永不匹配**，department 用户在 v2 全部义工写操作被 403。
修复：`util/permission/volunteer.py`、`util/permission/volunteer_member.py` 统一改为 `user.get("per", [])` 含 `admin|department`（zvp 语义）。

**连带发现并封堵的上游权限旁路**：`volunteer_member` 两个校验器原为「返回 `False` 不抛异常」，而 v2 三个调用点（加成员 `activities_v2_router.py:323`、删成员 `:371`、改成员记录 `:529`）`await` 后**忽略返回值**直接落库 → 任何登录用户可写成员数据。现改为与 zvp 一致的**抛 403** 语义（全仓已确认无调用方依赖旧返回值行为），一处改动同时封三个调用点。

#### 5. 安全审计结论

**移植面（§1/§2 全部文件）**：无 `requests/httpx/urllib/aiohttp`、无绝对 URL / 外链生成、无 `window.open` / `location` 外跳、无 `eval/exec/subprocess`、无 base64 payload、无硬编码主机 / IP / 凭证。NoSQL 注入面：`query` 一律 `re.escape` 后进 `$regex`；`type/status` 为 `Literal` 白名单；`activity_id`/`member` 走 `validate_object_id`。

**范围外危险代码（zvp 侧存在，均未移植、未触碰）**：

| 位置（zvp 侧） | 内容 | 处置 |
|---|---|---|
| `frontend/src/api/xh.js:12,31` | 学海外呼（`admin.yunzuoye.net` / `ztp.yunzuoye.cloud`）签名代理 | 不移植；义工管理页面无引用 |
| `frontend/src/views/manage/ManageHome.vue:200` | `window.location` 硬编码跳转外部站点 | 不移植 |
| `frontend/src/utils/constants.ts:2,4` | 外部图标 URL | 不移植 |
| `backend/routers/xh_router.py`、`routers/v1/platform.py` | 学海代理 / 平台登录体系 | 不在义工管理范围，未触碰 |

**部署结构**：主站负责页面与静态资源分发，另一域名负责 `/api` 请求并可解析到距离
数据库更近的后端。生产构建通过环境变量 `VITE_API_BASE` 注入完整 API 基址；后端
通过环境变量 `ZVMS_CORS_ALLOWED_ORIGINS` 接收逗号分隔的主站 Origin 白名单。Nginx
CSP 模板中的 `__ZVMS_API_ORIGIN__` / `__ZVMS_API_WS_ORIGIN__` 同样由部署环境替换，
仓库不保存生产域名。

```text
VITE_API_BASE=https://<api-domain>/api
ZVMS_CORS_ALLOWED_ORIGINS=https://<content-domain>
```

后端只在进程启动时读取 `ZVMS_CORS_ALLOWED_ORIGINS`，修改后需重启服务。

#### 6. 验证记录

- 后端：`python3 -m py_compile`（v3 路由 + 两个权限文件）exit 0；危险模式 grep 零命中。
- 前端：`npm ci --legacy-peer-deps`（上游 `unplugin-oxlint`×`oxlint` peer 冲突为纯 dev 工具问题）后 `vue-tsc --noEmit`：**改动文件 0 错误**；存量 6 处上游遗留错误（`App.vue` PWA 虚拟模块/watermark、`ZSelectActivityMode.vue` hybrid 图标、`icons/positions/index.ts`、`plugins/ua.ts`）与本次改动无关。
- 独立安全复核（oracle）：两次派发均因 oracle 模型变体不可用（基础设施故障，非任务问题）失败；改由编排者按同一检查单逐项替代复核（v3 路由 404 行全文通读 + 证据）：
  - 外链 / 外呼 / `eval` / `subprocess` / `print` / 整型时间戳：终局 grep 8 个改动文件（后端 3 + 前端 5）**零命中**（`SWEEP_EXIT=1`）。
  - 注入面：`query` 一律 `re.escape` 后进 `$regex`；`type/status` 为 `Literal` 白名单；`activity_id` 走 `validate_object_id`；成员 body 走 pydantic `NewActivityMember`（`duration gt=0`）。
  - 权限：三处写门均为 `admin|department`（`user.get("per")`）；全仓 grep 确认无调用方依赖 `volunteer_member` 旧「返回 False」语义（v2 三处调用点均为 `await` 后忽略返回值，抛异常语义即正确封堵）。
  - 数据完整性：v3 创建全字段齐备（含 `appointee/approver/place/origin`）且日期为 `datetime`（BSON Date）；`origin="activities"` 属合法 `Literal`；批量加成员「先全量预校验、后写入」、活动回置用 `datetime.now()`；`$convert onError/onNull` 防脏 ObjectId；成员计数切片 `pipeline[:5]` 与「条件 `$match` 恒为阶段」结构一致。
  - **结论：通过。** 注意：本审计为静态验证（本机无后端依赖环境），上线替换前建议在有 Atlas 连接的环境跑一次运行时冒烟（建义工 → 批量加成员 → 列表 / 成员读取回环）。

#### 7. 已知遗留 / 偏差

1. `get_user_name` 仍读 legacy `zvms.users`（zvp 同款行为，异常安全回退 "Unknown"）：仅在 `zvms_new.users` 的用户在日志里显示 "Unknown"。两侧一致，未改。
2. v2 单条加成员仍按 zvp 相同方式读 legacy `zvms.users` 校验目标用户——批量主路径已切 v3，单条路径维持两侧一致。
3. `imports_router.py:32` `if not validate_activity_name(name)` truthiness bug（校验永不生效）：zvp 与本仓逐字节相同，维持一致未修。
4. v3 简化创建 `approver=""`、`appointee=creator`、`origin="activities"`：满足本仓严格 typing 的有意适配（见 §3）。
5. v3 `GET members` 无角色门（登录即可读成员姓名）：与 zvp 及本仓 v2 成员端点姿态一致，校内场景可接受。

---

### Part 2 — 用户管理与登录移植（2026-10-10）

> 任务：把现役 zvp 的**用户管理 API**（登录、用户与班级群组管理）移植进本仓库。
> **红线**：记录用户设备 / 绑定用户 ID / 生成外链允许跳转三类代码一律不得迁移；本仓上游自带的同源设备代码在用户管理面一并清除。
> 方式：双侧只读勘察（zvp 源语义 + 本仓现状，两条并行泳道）→ 实施 → 全树危险模式终局 grep → 写后自审（成品代码逐行通读）。未运行服务、未连数据库。

#### 8. 后端端点对齐总表（本次新增 / 改造）

| 端点 | zvp 侧能力 | 本仓处理 | 状态 |
|---|---|---|---|
| `POST /api/users/auth`（v1 登录） | zvp 已停用 v1、改用 `/v1/platform/login` | **不整端点搬运** platform 登录（拖入 APILogger 设备日志 / sid 会话绑定 / TOTP，触红线或无上游 UI 成死代码），改为补齐本仓登录的 zvp 语义，见 §10 | 已改造 |
| `GET /api/v2/users` | 用户列表：分页 / 关键词 / 角色过滤 + 班级联表 | 新增（`routers/users_v2_router.py`，admin/department，带 role 时仅 admin） | 已移植 |
| `GET /api/v3/users/{user_id}` | zvp 无对应端点（其登录即返 profile） | 新增（`routers/users_v3_router.py`）：`zvms_new` 用户 profile（本人 / admin / department / secretary 同班可读），服务前端刷新与用户详情 | 新增适配端点 |
| `PUT /api/v3/users/{user_id}/roles` | admin 改角色，`AllowedRole` 白名单，204 | 新增 | 已移植 |
| `POST /api/v3/users` | 建用户：默认密码=学号(bcrypt)、roles 白名单、学号查重 409、班级存在性校验 | 新增；不写 `session_id`（zvp 同款写法） | 已移植 |
| `GET /api/v2/groups` | 班级列表（admin/department） | 新增（`routers/groups_v2_router.py`）；**不投影 enrollYear**（实库 `zvms_new.groups` 仅 `name`，DB_ALIGNMENT §2） | 已移植（适配） |
| `GET /api/v2/groups/{group_id}` | 单班级（本人班级或 admin/department） | 新增；响应补合成字段 `type:"class"`（保前端既有判断语义） | 已移植（适配） |
| `GET /api/v3/groups/{group_id}/users` | 班级成员分页（`zvms_new.users` by `class_id`） | 新增（`routers/groups_v3_router.py`）；扩展 `pwdm` 仅返回布尔（哈希经 pop 永不出响应） | 已移植（扩展） |
| `POST /api/v3/groups` | 建班（admin） | 新增；**剔除 enrollYear**（实库无此字段） | 已移植（适配） |
| `PUT /api/v3/groups/{group_id}/name` | 改名（admin） | 新增 | 已移植 |
| `DELETE /api/v3/groups/{group_id}` | 删班（admin，成员 `class_id` 置空） | 新增 | 已移植 |
| `POST / DELETE /api/v3/groups/{group_id}/users/{user_id}` | 加 / 移班级成员（admin/department；移除校验「不在此班级」400） | 新增 | 已移植 |
| v1 用户写端点（`POST ""`、`DELETE /{target}`、`PUT /{user_oid}`、`POST|DELETE /{user_oid}/group`） | zvp 全部 503 封堵（`_raise_exc`） | **同款 503 封堵**，杜绝前端静默写 legacy 库 | 已封堵（zvp parity） |
| `GET /api/users/{user_oid}`（v1 详情） | zvp 同样无鉴权 | **加登录门**（偏离 zvp，收紧） | 已收紧 |
| `GET /api/v2/groups/{id}/statistics/compliance` | — | 上游鉴权被注释 → **恢复**（admin/department/secretary 同班） | 已修复 |
| v1 `PUT /api/users/{oid}/password` | zvp 改密走 `PUT /v2/users/{id}/password` | 上游端点保留（已切 `zvms_new`，DB_ALIGNMENT §5），为改密主路径；zvp 的 `DELETE /v2/users/{id}/password`（重置为初始密码）**未移植**——目标前端无对应入口，需要时再补 v3 端点 | 已存在 |

#### 9. 前端接线（最小改动，UI 与模板未动）

| 文件 | 变更 |
|---|---|
| `src/plugins/axios.ts` | **剥除 Clarity-ID / Xuehai-ID / Device-ID 三请求头**与 `_clck` cookie 读取（设备上报面归零） |
| `src/plugins/ua.ts` | 删 `getXuehaiId()`（`window.xhBrowserJava` 学海设备桥接）；`pad()` 收敛为纯三星平板 UA 判定（本地布局适配，不外传） |
| `src/global.d.ts` | **整文件删除**（仅含 Clarity 全局类型声明，无加载无调用的死面） |
| `src/App.vue` | 删 `deviceId` 随机生成与 localStorage 写入（唯一消费方是已删的 Device-ID 头） |
| `types/login.d.ts` | 新增 `UserProfile`；`LoginResult.profile?` |
| `types/user.d.ts` | `User` 增 `roles?` / `class_id?`；`UserPosition` 联合增 `teacher`（zvp AllowedRole 词汇） |
| `src/api/user/crud.ts` + `index.ts` | 新增 `read_v2`（GET /v2/users）、`readOne_v3`（GET /v3/users/{id}）、`updateRoles`（PUT roles）、`create_v3`（POST /v3/users）并导出 |
| `src/api/group/crud.ts` | `readOne` 切 `GET /v2/groups/{id}`；`delete` 切 v3（沿用临时 token 弹窗）；新增 `read_v2` / `createGroupV3` / `renameGroupV3` / `addGroupUserV3` / `removeGroupUserV3` |
| `src/api/group/reads.ts` + `index.ts` | 新增 `users_v3`（GET /v3/groups/{id}/users，支持 pwdm）并导出 |
| `src/stores/user.ts` | 登录直取 `profile`：`position = roles`（现役词汇直达，不再经 legacy group/permissions 推导）、`class_id` 直取；`refreshUser` 切 `readOne_v3`；`removeUser` 补清 `position` / `class_id`（原登出残留 bug）；无 profile 时保留 legacy 兜底路径 |
| `src/components/group/ZUserModification.vue` | 创建 → `create_v3`（默认密码=学号）；修改 → 班级变更走 v3 加/移成员（改名/删除在 zvp live 同为 503）；班级下拉数据切 `read_v2` |
| `src/components/group/ZGroupList.vue` | 列表切 `read_v2`（`zvms_new.groups`）；删除按钮走 v3 |
| `src/components/group/ZGroupUserList.vue` | 成员列表切 `users_v3`（by `class_id` + pwdm）；全校模式切 `read_v2`；可见性判断 `groups.includes(id)` → `relatedGroup(id)`（profile 登录后 groups 不再承载班级关系） |
| `src/components/form/ZSelectClass.vue` | 班级搜索按角色条件切换：admin/department → `read_v2`；secretary → 本人班级 `readOne`；其余保留 v1 兜底 |
| `src/views/user/UserPage.vue` | 用户详情切 `readOne_v3`（`zvms_new` profile） |

#### 10. 登录链路改造（对齐 zvp 行为，不搬 zvp 端点）

| # | zvp 行为 | 本仓落地 |
|---|---|---|
| 1 | 学号直查 `zvms_new.users` | 登录先按学号查 `zvms_new`，legacy 仅兜底（`users_router.py:43-54`） |
| 2 | `password=None` → 明文与学号比对（初始密码） | `validate_by_cert` 补同款分支（`util/cert.py`）。**原实现对 `password=None` 直接 TypeError→500，初始密码用户全部无法登录——本次移植修复的关键缺陷** |
| 3 | `password_authz` 临时 token 5 分钟 | `jwt_encode` temporary 时长 15min → **5min** |
| 4 | 登录响应含 `profile`（去 password、`_id`/`class_id` 字符串化） | 同款加入响应（`users_router.py:69-84`） |
| 5 | 登录写日志（含全量请求头 / IP / xuehai / net_session） | **仅写业务日志**（url / user / 文本 / 时间），零设备字段，且仅在登录成功后写 |
| 6 | `sid` 会话 ID + `zvp_session` cookie + TOTP | **不移植**（会话绑定属禁止面；TOTP 无上游 UI 为死代码） |

#### 11. 禁止面处置（红线执行记录）

| 类别 | zvp 侧位置（未移植） | 本仓上游同源残留（已清除） |
|---|---|---|
| 记录用户设备 | `util/logify.py`（Clarity cookie/头、Xuehai 头、IP、黑名单）、`util/log.py` 全量请求头落库、`main.py` UA 拦截 + `zvp_session` cookie、`platform.py:143-154` 设备核验 | 登录端点 `binding_user_credentials` 依赖与 ZVMSLog 三设备字段（`typings/log.py` 重写为 `{url,user,data,timestamp}`，全部 15 处调用点签名零波及）；`util/logify.py`、`util/blocking.py`、`util/logs.py` 三死文件删除；前端三请求头 + `deviceId` 生成 + `global.d.ts` Clarity 声明 |
| 绑定用户 ID | `xh_router.py` 整文件、`get_user.py` `session_id`、`platform.py:206-220` sid 绑定、前端 `xh.js` + `stores/user.ts:66-86` 设备核验分支 | `ua.ts` `xhBrowserJava` 桥接删除；日志不再存 xuehai_id↔用户关联 |
| 生成外链允许跳转 | `xh_router.py` 任意 URL `urlopen`、`api/xh.js` 外站、`constants.ts` 外部图标、`ManageHome.vue:200` 外跳 | `util/object_id.py` 的 `get_img_token*`（外站图片床 `requests.get`，确认无调用者）+ `import requests` 一并移除；`index.html` 核查无外部追踪脚本（第 5 行为**移除** Cloudflare beacon 的反追踪代码，保留） |

终局 grep：后端全树 `*.py` 对 `logify / binding_user_credentials / is_blocked / get_img_token / randomString` 与 `requests / httpx / aiohttp / urlopen / urllib` **双轮零命中**（后端无任何出站 HTTP 能力点）；前端 `src + types` 对 `clarity / xuehai / _clck / deviceId / xhBrowserJava / yunzuoye / imgbed / window.open / urlopen` 仅剩 §14-1/2 三处展示 / 文案层残留。

#### 12. 权限修复（本次根因修复）

1. **词汇断裂**：`util/permission/user.py` 原查 `user.get("perm")`（v1→v2 升级词汇 monitor/volunteer/club，club 分支死代码）→ 统一改现役 JWT `per` 词汇（与 Part 1 §4 同族）。
2. **读权限旁路**：两个校验器原「返回 `False` 不抛异常」，而 `users_v2` activities/time 与 `groups_v2` activities/compliance 四个调用点 `await` 后忽略返回值 → 任何登录用户可读任意用户/班级数据 → 改**抛 403**（同 Part 1 §4 volunteer_member 手法，一处改动封四个调用点）。
3. **secretary 同班核验补全**：zvp 侧注释掉的同班校验按意图实现，比对两侧 `zvms_new.users.class_id`；班级读权限同班校验由 legacy `get_user_class` 改读现役 `class_id`。
4. **`DELETE /api/users/{target}` 无 admin 校验**（任何登录用户可删任意用户、写 legacy 库）→ v1 删除端点 503 封堵（zvp 同款），整类风险归零。
5. `GET /api/users/{oid}` 无鉴权 → 加登录门。

#### 13. 验证记录（2026-10-10）

- 后端：`python3 -m compileall` **exit 0**；两轮危险模式 grep 零命中（§11）。
- 前端：`npm run type-check`（vue-tsc）：**改动文件 0 错误**；存量 4 处上游遗留错误（`App.vue` PWA 虚拟模块、`App.vue` watermark、`ZSelectActivityMode.vue` hybrid、`icons/positions/index.ts` Record 断言——`teacher` 入列使该**存量**报错多列一个缺失键，错误本身上游既有，该文件未触碰）。
- **写后自审（按用户要求，成品代码逐行通读）**：两个新路由全文 + 登录改造 + 权限模块复查——权限门完整（admin / department / secretary 同班 / 本人；secretary 探测他人班级统一 403，不泄露存在性）；密码哈希经聚合后在进程内 pop，任何分支不出响应；`query` 全部 `re.escape` 后进 `$regex`；ObjectId 全 `validate_object_id`；角色双重白名单（pydantic Literal + set）；建用户补齐 `roles` 默认值、班级存在性校验；登录日志仅成功路径、零设备字段。**未发现漏洞。**
- 仍属静态验证：上线前建议运行时冒烟——登录（初始密码 → 强制改密 → 正常登录）→ 建用户 → 改角色 → 建班 / 改名 / 删班 → 加/移成员 → 各列表回环；secretary 越班读应 403。

#### 14. 已知遗留 / 偏差（Part 2）

1. **展示层历史设备字段**：`ZLogCard.vue:37-41`、`types/log.d.ts` 仍含 clarity/xuehai 渲染与类型——仅为**历史日志数据**展示面（采集已死，新日志不含此字段，`v-if` 不渲染）；按「UI 不动」红线保留。
2. `i18n/locales/about.ts:29` About 页隐私文案提及 Microsoft Clarity/Advertising：纯文案（无脚本加载、无外跳），上游既有，未动。
3. **roles 编辑无前端入口**：后端 `PUT /v3/users/{id}/roles` 已就绪，但上游 UI 无对应控件（zvp 有独立 ManageRole 页）——按「保持上游 UI」红线不新增页面；`api.user.updateRoles` 已导出待接。
4. **用户改名 / 删除不可用**：zvp live 同样封死（503），本仓 v1 写端点同款封堵；UI 按钮保留但会收到 503 提示（与 zvp 行为一致）。
5. `ZUserModification` 的「权限组」复选框为 legacy 模型残面（`zvms_new` 无 permission group）：展示 legacy 数据、提交时忽略——UI 红线下的有意保留。
6. `ZActivityMemberList.vue:128` 批量导入按学号解析用户仍读 legacy v1（Part 1 决策维持，依赖两库 `_id` 一致的既有假设）。
7. 班级时间 / 统计页（`ZGroupUserTimeList` / `ZGroupTimeStatistics`）仍读 legacy v2 time 端点：属时长域，不在本次用户管理范围。
8. `groups_v2` 的 `/{id}/activities`、`/{id}/time`、`/{id}/users` 三个 legacy 读端点保留未动（zvp 亦如此），新消费方一律走 v3。

---

### Part 3 — 日志 API 移植与汉化（2026-10-10）

> 任务：移植 zvp 的 logs API；查看记录项并做汉化支持；尽可能全面记录用户行为；红线同前——不生成外链、不允许跳转外站、不允许随意修改日志内容；并对之前移植的代码做全树复查。
> 方式：双泳道只读勘察（zvp 日志全貌 + 本仓覆盖缺口）→ 实施 → 终局 grep + 写后自审。未运行服务、未连数据库。

#### 15. 日志端点与 schema

##### 端点（GET-only，无任何日志写/改/删端点）

| 端点 | zvp 侧能力 | 本仓处理 | 状态 |
|---|---|---|---|
| `GET /api/logs`（logs_router） | zvp 另有 `/v1/platform/logs`（含设备筛选） | 重写：admin 门、`page(-1=不分页)/perpage/query(url+data 搜索，已剥 clarity/xuehai 遗留分支)/performer/type/level(逗号分隔 $in)/time_start/time_end(秒)`，行内补 `user_name`（`zvms_new` 名 → legacy 兜底 → 未知用户） | 已移植（设备参数剔除） |
| `GET /api/users/{oid}/logs` | zvp 有（搜 clarity/xuehai） | Part 2 已清理搜索面，保留 API 兼容；前端已统一走 `/logs?performer=` | 已存在 |
| zvp `/v1/platform/logs` 的 `xuehai_id/session/net_session` 参数 | 设备/会话筛选 | **不移植** | 已剔除 |

##### Schema（记录项）

`ZVMSLog`（`typings/log.py`）：`{url, user, data, timestamp, type, level}` → 写 `db.zvms.logs`。

- 新增业务字段：`type`（动作分类）+ `level`（默认 info）——取 zvp 新链路语义，设备面字段一律不引入
- **写入目标维持 `zvms.logs`**（有意偏离 zvp：实库 `zvms_new.logs` 6792 条全为 zvp 设备型 schema（xuehai_id/net_session/request.headers），读写它会混 schema 并向管理界面透出设备数据；本仓 97138 条历史 + Part 1/2 写入均在 `zvms.logs`，单一 schema 单一写入点）

`type` 词汇表（18 词，前端 `ZLogCard.vue` 有中文映射）：`user_login / passwd_change / user_create / role_change / past_change / group_create / group_update / group_delete / group_member_add / group_member_remove / activity_create / activity_update / activity_delete / activity_member_add / activity_member_update / activity_member_remove / import_excel / export`

#### 16. 汉化与行为记录覆盖

- **35 个日志写入点全部中文化**（33 处 `with_text` + 登录与报告下载 2 处手工构造），文本含操作者真名（新助手 `util/user.py:get_user_display_names` 批量解析：`zvms_new` 优先 → legacy 兜底 → 未知用户）；审核状态值映射中文（生效/待审核/已驳回）
- 相比 Part 2（22 站点）**补埋点 13 处**，封掉 zvp 也存在的缺口：
  1. v2 义工成员移除（原无日志）
  2. v2 义工成员记录修改（时长/模式，原无日志）
  3. v2 义工活动信息修改（名称/描述，原无日志）
  4. v1 用户组四写端点：建/改名/改描述/删（原零日志）
  5. 导出五处：用户列表导出、时长报表导出、报告下载授权、报告文件下载、导出文件下载（原模块零日志——导出即数据外流，必须可审计）
- 修正既有日志**时序错误**：改密、历史身份增删原在校验/写库前写日志（404 也会留下"已修改"记录）→ 全部移到成功后
- **有意不记**：登录失败（`POST /auth` 为无鉴权端点，记失败 = 开放未认证者向日志集合无限注入内容的污染/DoS 面，与 zvp 行为一致，守住「不能随意修改 log 内容」的完整性）；amalgamation（上游已 410 封死）；读操作

#### 17. 禁止面处置

| 红线 | 执行 |
|---|---|
| 不生成外链 | 日志 `url` 字段恒为本站 API 地址（`str(request.url)`）；前端渲染纯文本 `{{ }}`，无任何 `<a>`/`window.open`/v-html |
| 不允许跳转外站 | 全前端复查 `window.open / location.href / v-html / :href / target=_blank` **零命中**；后端 RedirectResponse 均为同源单复数重定向 |
| 不能随意修改 log | 日志端点 GET-only；运行时唯一写入点 `typings/log.py` `insert_log`；全后端无对日志集合的 update/delete 代码路径；日志文本仅由服务端以已校验字段拼接 |

#### 18. 复查之前的代码（全树）+ 本次修复的残面

复查结论：Part 1/2 移植面与全树无外链生成、无外站跳转、无日志篡改路径。复查发现并**本次已修复**：

1. `logs_router.py` 搜索 `$or` 仍含 clarity/xuehai 分支（Part 2 遗留）→ 重写时剔除
2. `ZLogCard.vue` IP/Clarity/学海三展示行 + 死导入（Part 2 §14-1 遗留）→ 移除，换 type/level 中文徽标
3. `types/log.d.ts` clarity/xuehai/ip 字段 → 对齐新 schema
4. `env.d.ts` ClarityFunction/window.clarity 全局类型声明 → 删除（继 Part 2 删 `global.d.ts` 后的最后一处 Clarity 残面）
5. `about.ts` 隐私文案声称使用 Microsoft Clarity 并含外站 URL 字符串 → 改写为与本仓实际一致的无追踪声明（纠错 + 消除唯一外站 URL 字面量）
6. `GET /api/exports/{task_id}/file` **无鉴权**即可下载用户列表/时长报表（上游遗留，凭 task id 取数）→ 补 `get_current_user` + admin 门（前端走 axios Bearer，合法流程不受影响）+ 补日志

#### 19. 已知遗留 / 偏差（Part 3）

1. `misc/remove_classes.py:23` 存在离线 `logs.delete_many`（手跑运维脚本，非 HTTP 面）——记录不移植不删；上线部署时建议移出生产环境
2. 时间筛选参数单位为**秒**（本仓日志 timestamp 为 float 秒；zvp 用毫秒配 BSON Date）——偏离已记录
3. `typings/activity.py:35,76` 的活动模型英文文本方法（非日志写入路径，上游遗留）未动
4. 历史日志（97138 条）无 type/level → 前端类型徽标对旧行留空（v-if 不渲染），`user_name` 正常解析
5. `GET /api/logs` 的 type/level/time 筛选为 API 能力，前端 `ZLogList` 保持上游单搜索框 UI 未接筛选控件（「UI 不动」红线，需要时可后补）

#### 20. 验证记录（2026-10-10）

- 后端 `python3 -m compileall` **exit 0**
- 前端 `vue-tsc --noEmit`：**改动文件 0 错误**；存量 3 处上游遗留（App.vue watermark、ZSelectActivityMode hybrid、icons/positions Record 断言）；Part 2 时的 App.vue PWA 虚拟模块错误随 `env.d.ts` 重写消失
- 审计 grep：33 处 `with_text` **全部带 type=**；手工 `ZVMSLog` 构造仅 3 处合法（登录 / 报告下载 / inject_log 本体）；日志集合引用 = 4 读 + 1 写（`insert_log` 唯一写入点）；`_actor_name` 零残留；`get_user_name` 剩余引用均为合法路径（登录兜底 / activity 模型文本 / 定义本身）
- 前端外链/设备面终局 grep **FRONTEND_CLEAN**（window.open / location.href / v-html / :href / target=_blank / clarity / xuehai / _clck / deviceId / xhBrowserJava 全零）
- **写后自审**：logs 端点权限门 / 正则转义 / 分页边界；35 站点日志时序（全部成功后写）；type 词汇表前后端一致性（18 词逐一比对）；导出鉴权修复对前端流程影响（axios Bearer 确认）；**未发现漏洞**
- 仍属静态验证：上线前建议运行时冒烟（登录 → 改密 → 建活动/成员进出 → 导出 → 管理端日志列表/搜索/分页回环，确认 type 徽标与 user_name 正常）

---

### Part 4 — 登录与身份验证 API 移植（2026-10-10）

> 任务：移植 zvp 的 login API 与身份验证 API；红线同前——不能记录设备、不能绑定用户 ID、不生成外链/跳转，且登录/鉴权路径零外呼（「不能容忍任何人越狱/访问外网」）。
> 方式：双泳道只读勘察（exp-5 zvp 登录全量 / exp-6 本仓鉴权现状与差距）→ 实施 → RFC 6238 向量验证 + 终局 grep + 写后自审。

#### 21. 移植内容

##### 21.1 动态码（TOTP）登录——zvp `*code*` 语义，按用户派生
- zvp 实现：`cktotp` 外部包（本机不可得）+ `aes_key.txt` 单 key **全站共享码**（`platform.py:248-272`）
- 本仓实现（`util/cert.py`）：
  - 纯本地 RFC6238 自实现（hmac/hashlib/struct，零依赖零出网）
  - **按用户派生 secret**：`HMAC-SHA256(aes_key, "totp:{user_id}")`——无状态、无新字段、消除全站共享码风险
  - 登录口令 `*<6位码>*`，30s 步进，接受当前+上一窗口
  - **密码优先、OTP 兜底**（zvp 同款语义）：密码校验通过绝不进 OTP 分支——真实密码形如 `*Abc12345*` 的用户不受影响
  - OTP 登录 token 有效期 1 天（zvp 语义；普通登录 15 天（Part 7 起调整为 10 天）、临时 5 分钟不变）
  - 60s 凭据时间窗仍前置强制（防重放）
- 新端点 `GET /api/v3/users/{id}/totp`（users_v3_router）：admin 取某用户当前动态码（zvp platform totp 的移植落点）；**超出 zvp 的加固：查看即写 `totp_view` warning 日志**（zvp 不记——凭据接触必须可审计）
- 前端入口：暂无 UI（zvp 的 ZDisplayTOTP 未移植），后端+日志已就绪，同 updateRoles 模式记录待接

##### 21.2 重置为初始密码——zvp `DELETE /v2/users/{id}/password`
- 新端点 `DELETE /api/v3/users/{id}/password`（users_v3_router）：
  - 鉴权 **compulsory_temporary_token**（zvp `get_authz_user` 同款——凭据类操作先重新验证身份）
  - 权限 admin/department/secretary 同班（zvp 同款；同班校验基于 zvms_new `class_id` 比对且要求双方非空）
  - 置 `password=None` → 恢复真初始态（下次登录走默认密码=学号分支）
  - 写 `passwd_change` 日志（操作者+目标）
- **前端接线（替换既有客户端实现）**：`ZActivityMember.vue` 重置按钮原为客户端方案（临时 token → 把成员密码 PUT 成学号的 bcrypt——**永久丢失 password=None 初始态**，且「初始密码=学号」语义散落前端）；现改为直调 v3 DELETE，语义归服务端，重新验证身份的弹窗流程不变

##### 21.3 登录/登出语义修复（勘察发现的真问题）
- **登出清 token**：zvp 的 `removeUser` 清 `localStorage.token`，本仓漏了（登出后 token 残留至下次访问登录页才兜底清除）→ `stores/user.ts` 补 `removeItem`
- **改密死期望移除**：`auth.ts resetPassword` 的 `if (!reset) localStorage.setItem('token', result.data.token)`——后端从不返回 token，`setItem(key, undefined)` 把字面量 `"undefined"` 写进 token 位，**每次自助改密都污染已存 token**（下次重载即伪登出）→ 删死参 `reset` + 死块；全部调用点核对（store / UserLogin 3 参、ZActivityMember 重接 v3）
- 登录日志区分动态码：`用户 X 使用动态码登录了系统`（`used_otp_credential` 实际验证动态码后才标注，非模式匹配——密码恰为 `*...*` 时不会误标）

##### 21.4 死代码清理
- `cert.py get_renewed_password`（全仓零引用，删除前 grep 确认）
- `users_router PUT password` 的 `forbid_groups` legacy 查询 + TODO 注释块（每次改密白跑一次无效查询）

#### 22. 有意不移植（记录）

| zvp 能力 | 不移植理由 |
|---|---|
| `POST /v1/platform/login` 整端点 + `/v1/platform/*` 路由 | 本仓已有 `/api/users/auth`（Part 2 补齐语义且前端全量接线）；建平行端点 = 无消费死代码，按「UI 不动」原则增强既有端点 |
| 登录请求内联 newPassword（弱密码登录时顺带改密） | 本仓走两步流（短 token → PUT → 重登录），上游前端已有闭环 |
| `sid` claim / session_id | 仅用于 zvp 日志 session 字段；本仓日志 schema（Part 3）无会话维度，无需签发 |
| `zvp_session` cookie / `net_session` | 浏览器指纹（禁止面） |
| 登录设备绑定校验（verifiedId/verifiedName） | 用户↔设备绑定（禁止面） |
| recovery 问答找回（recovery_a1/2/3） | zvms_new 无对应字段 + 无 UI 支撑 + 凭据旁路语义 |
| refresh / 后端登出端点 | zvp 也没有（JWT 无状态，登出 = 前端清 token） |
| 登录响应 status/code 包裹 | zvp 亦为裸 `{token, profile}`；前端 `status==='error'` 判空属上游遗留防御，无害不动 |

#### 23. 红线核对（Part 4）

- **零外呼**：后端全树 grep `requests|urlopen|httpx|smtplib|urllib|aiohttp` → **OUTBOUND_CLEAN**；TOTP 为纯本地 HMAC，无短信/邮件/学海/第三方投递
- **零设备/会话绑定**：token claims 仍仅 `iss/exp/iat/sub/scope/per/elg/jti`（无 sid/device）；TOTP secret 按用户派生、不落库、不绑设备
- **零外链/跳转**：登录链无 RedirectResponse、无 OAuth 跳转；前端登录页无外链（复查确认）
- zvp 登录链上的 xh 外呼（`getXHProfile` → `xh_router` urlopen）整面未触碰——本仓从无此代码

#### 24. 验证记录（2026-10-10）

- 后端 `compileall` **exit 0**
- **TOTP 算法 RFC 6238 附录 B 官方向量 6/6 全过**（SHA1 六向量的 6 位截断；T=2000000000 的期望值经 rfc-editor.org 原文对证——差异源于我方记错向量而非算法）；窗口逻辑（当前+上一窗口接受、更早拒绝）与 per-user secret 派生（确定性、按用户区分）独立测试通过
- 前端 `vue-tsc`：**改动文件 0 错误**；存量 3 处上游遗留不变（App.vue watermark / ZSelectActivityMode hybrid / icons/positions）；实施中曾引入 ZActivityMember 4 参调用错（勘察未覆盖的第 4 调用点），已随 v3 重接消除
- 审计 grep：`get_renewed_password` 零残留；`used_otp_credential` 三站点（定义/导入/调用）签名一致；出网面全树零命中
- 写后自审：密码优先/OTP 兜底次序、`*` 包裹真实密码边界、60s 防重放窗口、临时 token 收紧对前端弹窗流程的兼容性、secretary 同班 None 边界——**未发现漏洞**

#### 25. 已知遗留（Part 4）

1. 动态码查看无前端 UI（后端+日志已就绪）——需要时可仿 zvp ZDisplayTOTP 在管理端加按用户取码入口
2. `elg` claim 恒空（zvms_new.users 无 eligibility 字段）——上游遗留占位，未动
3. OTP 短模式（`mode=short` + 动态码）签发 5 分钟临时 token：与 zvp 行为一致，属 admin 测试便利面，无扩大风险（动态码知情者本就 admin 限定）
4. 运行时冒烟仍待有 Atlas 环境执行：学号登录（默认密码/强密码/动态码三路）、改密闭环、重置为初始密码回环、登出后 token 残留复查

---

### Part 5 — zvp 全量 API 面盘点与未移植总账（2026-10-10）

> 背景：Part 1-4 均按移植范围勘察，本次对 zvp 全部 13 个 router（~75 端点）做穷举对照，并补查四次勘察从未看过的模块（通知/邮件/推送/任务/设置/文件上传/socket.io/定时任务），产出未移植总账。方式：单泳道只读穷举（exp-7）。

#### 26. 盘点结论

- **zvp API 面已几乎全覆盖**：13 router ~75 端点绝大部分已移植（✅）或功能等价（🟰）；zvp 使用的全部数据库集合本仓均有引用，无反向缺口
- **勘察盲区模块在 zvp 零实现**（不存在即无从移植）：通知路由、邮件/smtp、消息推送、独立任务管理、设置端点、socket.io 事件、定时任务/后台 job——其中 socket.io、apscheduler、notifications 读、activity statistics、mcp 反而是**本仓独有能力**
- zvp 两处死桩（无装饰器函数）本仓已实现：`POST /users/{oid}/past`（加历史身份）、`GET /users/{oid}/notification`（读 `zvms.notifications`）
- zvp `GET /api/cert` 已 410（强制升级）；本仓 `/api/cert` 正常下发公钥——语义不同属部署阶段差异
- zvp 无 v1 activities/times/mcp/statistics router——本仓在这些面是超集

#### 27. 未移植总账（按优先级）

| # | 项 | zvp 参照 | 评估 |
|---|---|---|---|
| 1 | **服务端弱密码强制改密**：登录时 `pwd_strength<3` 不发正式 token，走强制改密闭环 | platform.py:119-197 | **已于 Part 6 移植**：`/api/users/auth` 弱密码 long 请求仅发 5 分钟临时 token + `weak_password` 标记，强度规则与前端逐字镜像；short 模式不拦（改密流程不受阻），OTP 路径不受影响；见 §30 |
| 2 | shield_middleware 路径黑名单 + UA 门禁（418 拦截） | main.py:76-119 | 可选加固面；其中 `zvp_session` cookie 分支（:79-81/:110-118）属红线不随迁 |
| 3 | 日志 request_methods / search_props 过滤增强 | platform.py:296-329 | 可选（Part 3 §19-5 已记录：API 能力就绪未接 UI）；session/net_session/xuehai_id 维持红线拒绝 |
| 4 | 前端整页：UserProfile（/me 个人资料页）、ManageRole（按角色管理页） | frontend/src/views | UI 面缺失，需要时另立任务 |
| 5 | /dev、/stats0.html、前端静态挂载 | main.py:50-53,190 | 调试/部署差异，不移植 |

#### 28. 与既有决策的对照（对勘察建议的修正）

- 勘察建议移植 `sid` / `password_authz` 语义——**维持 Part 4 §22 决策不变**：`password_authz` 已由 `mode='short'` 功能等价（临时 token 5 分钟）；`sid` 仅服务 zvp 日志 session 字段，本仓日志 schema（Part 3）无会话维度，不移植
- `POST /v1/platform/login` 平行路由——维持不建；其剩余缺口收敛为本表 #1（服务端弱密码强制），在既有端点上补齐

#### 29. 红线拒绝面复核（全量第 5 次确认）

xh_router 任意 URL 代理（:159 urlopen + :46 硬编码签名 KEY）、登录设备绑定（platform.py:143-154）、`zvp_session` 设备会话（main.py:79-118）、clarity/xuehai 采集链、日志设备过滤参数、`_1.py` 日志删除脚本——全部维持拒绝，本仓从未引入。

---

### Part 6 — 服务端弱密码强制改密（2026-10-10）

> 任务：移植总账 #1（Part 5 §27）。zvp 在登录时以 `pwd_strength<3` 拒发正式 token（platform.py:174-195），本仓原先仅前端校验强度——服务端对弱/初始密码照发 15 天 access token，直接调 API 可长期绕过改密。本次在服务端补上强制闭环。

#### 30. 实现设计

- **强度规则逐字镜像前端**（`util/cert.py pwd_strength` ↔ `stores/user.ts validatePasswordStrength`）：8-14 位可打印 ASCII（0x20-0x7E），小写/大写/数字/符号四类字符 ≥3；独立测试 10/10 用例与前端语义一致（含 15 位超限、非 ASCII、纯数字等边界）
- **判定位置**：`validate_by_cert` 返回 `(token, weak_password)`（全仓唯一调用方 `/api/users/auth` 已同步适配）：
  - 弱密码 = 初始态（`password is None`）或 `pwd_strength(明文口令) < 3`
  - **弱 + long**：只签发 5 分钟 `temporary_token`（不给正式 access token）+ 响应新增 `weak_password: true`
  - **弱 + short**：照常放行——改密流程本身不能被堵死
  - **OTP 动态码**：`weak_password` 恒 false（管理员代登不涉及口令，不受影响）
- **前端最小承接**：`LoginResult` 类型加 `weak_password?: boolean`；`store.setUser` 改为「服务端标记 || 前端本地校验」双保险置 `shouldResetPassword`；既有弱密码 UI 闭环（UserLogin 弱密码分支 → 短 token → 强制改密 → 重登）无需改动
- **日志**：弱密码 long 登录写区分文本「用户 X 登录了系统（密码强度不足，仅发放临时授权）」（`user_login` 类型），管理端可审计强制降权发生
- **有意不做**（zvp 同款行为）：改密端点（`PUT /users/{oid}/password`）不校验新密码强度——UI 端已有客户端校验，直连 API 可设弱密码但下次 long 登录会再次强制；闭环自洽

#### 31. 验证记录（2026-10-10）

- 后端 `compileall` **exit 0**
- `pwd_strength` 规则镜像独立测试 **10/10 全过**（强弱判定与强度计数均符合前端语义）
- 前端 `vue-tsc`：改动文件（login.d.ts / stores/user.ts）**0 错误**，存量 3 处上游遗留不变
- `validate_by_cert` 调用方 grep 确认全仓唯一（users_router），返回签名变更无遗漏适配
- 写后自审：short 改密流程不被堵、OTP 不受影响、初始密码用户 long 直连仅得 5 分钟临时 token、规则镜像与前端无分歧口径——**未发现漏洞**
- 仍属静态验证：运行时冒烟（弱密码 long 登录应得临时 token + weak_password 标记 → UI 强制改密 → 强密码重登得正式 token）待 Atlas 环境执行

---

### Part 7 — 登录 token 有效期调整（2026-10-10）

> 用户指令：长期登录 token 有效期 15 天 → **10 天**。唯一改动点 `util/cert.py jwt_encode`（long 分支）；临时 token（5 分钟）与 OTP 登录 token（1 天）不变。前述小节中的历史性"15 天"表述（如 Part 6 背景中"原先照发 15 天"）为当时事实，保留不动；当前行为以本节为准。


---

## 五、zvp-latest 开发状态（历史记录）

> **2026-10-24：原 `zvp-latest/` 已删除。** 学海相关原始文件归档在
> `reference/xuehai/zvp-latest/`；本节及以下路径均为删除前的历史记录。
>
> 审计对象：`zvp-latest/backend`（FastAPI/Python）+ `zvp-latest/frontend`（Vue3 + Vite + TS + Element Plus + pinia）
> 审计日期：2026-10-08 ｜ 方式：静态审计（读取 `main.py` 路由注册、`routers/**` 装饰器、前端 `src/router`、`src/api/**` 及全仓引用检索），**未运行服务**
> 结论均附 `file:line`；无引用依据的判断已显式标注。

---

### 0. 一句话结论

- 后端接口分为两半：**读取类基本可用**；写入类中 **v1 旧路由仍大面积硬编码 503 停写**，但 **v3 已补齐班级写入（建/改名/删班、用户入班/移出班）、角色修改与创建用户端点**（见 §1.2a）；登录旧接口 410 不变。
- 前端页面骨架齐全：**活动添加成员、班级改名/删除/创建、班级成员管理、修改用户角色、创建用户、下载报表、班级时长统计已全部接线**；用户页新增义工时间仪表盘（UserPageNew 合入并删除孤儿文件）。
- **导出（Exports）/导入（Imports）后端有实现，前端已接线（2026-10-08）**：用户表/时长表导出（任务轮询+下载）、报表打包下载（tar.gz）、班级模板导出、Excel 导入义工。
- ~~后端 `activity_statistics_router`、`mcp_router`、`times_router` **只剩 `.pyc`，源码缺失**~~ **已查证关闭（2026-10-10）：三模块在 canonical 仓库全仓零引用（main.py 从未注册、无动态 import）、git 从未跟踪其 pyc——系审计副本中的陈旧本地产物，非代码库缺陷，无需恢复（见 §1.5）**。**至此 STATUS 全部待修项清零。**

---

### 1. 后端 API 状态

#### 1.1 路由注册总表（`backend/main.py:130-142`）

| 来源 | prefix | 状态 |
|---|---|---|
| `routers/users_router.py` | `/api/users` | 部分可用，写入多条 503 |
| `routers/groups_router.py` | `/api/groups` | 读取可用，写入 503 |
| `routers/exports_router.py` | `/api/exports` | 可用，**前端已接线（2026-10-08）** |
| `routers/imports_router.py` | `/api/imports` | 可用，**前端已接线（2026-10-08）** |
| `routers/logs_router.py` | `/api/logs` | 可用 |
| `routers/activities_v2_router.py` | `/api/v2/activities` | 可用（个别 bug） |
| `routers/users_v2_router.py` | `/api/v2/users` | 部分可用，2 条 deprecated |
| `routers/users_v3_router.py` | `/api/v3/users` | 可用 |
| `routers/groups_v2_router.py` | `/api/v2/groups` | 可用 |
| `routers/groups_v3_router.py` | `/api/v3/groups` | 可用（import bug 已修，含写入端点） |
| `routers/activities_v3_router.py` | `/api/v3/activities` | 可用 |
| `routers/xh_router.py`（`app`） | `/xh` | 可用（签名代理） |
| `routers/v1/platform.py`（`api`） | `/api/v1/platform` | 可用（新版平台接口） |

`main.py` 直挂：`GET /api/health`（:144）、`GET /api/cert`→**410 强制升级**（:155）、`GET /dev`（:50）。

#### 1.2 可用（前端可用作依据）

| Method | Path | 用途 | 定位 |
|---|---|---|---|
| GET | `/api/users` / `/{user_oid}` | 用户列表/详情 | users_router.py:201/:266 |
| PUT | `/api/users/{oid}/password` | 改密 | users_router.py:155 |
| GET | `/api/users/{oid}/logs` | 用户日志 | users_router.py:435 |
| GET | `/api/groups`、`/{id}`、`/{id}/activities`、`/{id}/user`、`/{id}/time`、`/{id}/template` | 组读取 + 模板导出 | groups_router.py:58/101/148/189/236/363 |
| — | `/api/exports/*`（7 端点） | 用户表/时长表导出、报表打包下载 | exports_router.py:193~357 |
| — | `/api/imports/activities` | Excel 批量导入活动/成员 | imports_router.py:20 |
| — | `/api/v2|v3/activities/*` | 活动 CRUD + 成员 | v2:23~510 / v3:24~400 |
| — | `/api/v2|v3/users/*`、`/api/v2|v3/groups/*` | 用户/组读取（v3 为快实现） | 见各文件 |
| — | `/api/v1/platform/{login,totp,logs,cert}` | 统一登录/TOTP/日志/证书 | platform.py:119/248/287/513 |
| — | `/xh/api` | 学海请求签名代理 | xh_router.py:128 |

#### 1.2a 2026-10-08 新增 v3 写入端点（本次实施）

| Method | Path | 用途 | 权限 | 定位 |
|---|---|---|---|---|
| POST | `/api/v3/groups`（body `{name, enrollYear?}`）→201 `{id}` | 创建班级 | admin | groups_v3_router.py:313 |
| PUT | `/api/v3/groups/{id}/name` →204 | 班级改名 | admin | groups_v3_router.py:334 |
| DELETE | `/api/v3/groups/{id}` →204 | 删班（成员 class_id 置空） | admin | groups_v3_router.py:361 |
| POST | `/api/v3/groups/{gid}/users/{uid}` →204 | 用户入班 | admin\|department | groups_v3_router.py:388 |
| DELETE | `/api/v3/groups/{gid}/users/{uid}` →204 | 用户移出班 | admin\|department | groups_v3_router.py:420 |
| PUT | `/api/v3/users/{uid}/roles`（body `{roles: list}`）→204 | 修改用户角色（白名单 student/secretary/department/admin/teacher） | admin | users_v3_router.py:271 |
| POST | `/api/v3/activities/{aid}/members`（body `[{member, mode, duration}]`）→204 | 批量添加活动成员（mode 连字符 `social-practice`） | admin\|department | activities_v3_router.py:319 |
| POST | `/api/v3/users`（body `{id, name, roles?, class_id?}`）→201 `{id}` | 创建用户（初始密码=bcrypt(学号)，学号/姓名走 validate_*，409 学号重复） | admin | users_v3_router.py:323 |

#### 1.3 停写 503（后端未实现 → 前端对应功能必然不可用）

| Method | Path | 定位 |
|---|---|---|
| POST | `/api/users`（建用户） | users_router.py:98 |
| DELETE | `/api/users/{target}`（删用户） | :133 |
| PUT | `/api/users/{user_oid}`（改用户） | :306 |
| POST | `/api/users/{oid}/group`（加入组） | :354 |
| DELETE | `/api/users/{oid}/group/{gid}`（移出组） | :370 |
| POST | `/api/groups`（建班级） | groups_router.py:43 |
| PUT | `/api/groups/{id}/name`（改名） | :136 |
| PUT | `/api/groups/{id}/description` | :332 |
| DELETE | `/api/groups/{id}`（删班级） | :353 |

> 说明：`_raise_exc`（users_router.py:33）统一抛 503，属"故意停写"。
> 2026-10-08 起：**建班/改名/删班、用户入班/移出班、修改角色、活动加成员均有 v3 端点可用（§1.2a）**；上表 v1 503 端点保持原状未动。

#### 1.4 停用 / 弃用

| Method | Path | 状态 | 定位 |
|---|---|---|---|
| POST | `/api/users/auth` | **410 已停用** | users_router.py:50 |
| GET | `/api/users/{oid}/time` | 弃用（README 勾选） | :396 |
| GET | `/api/v2/users/{id}/time` | deprecated | users_v2_router.py:320 |
| GET | `/api/v2/users/{id}/activities` | deprecated/slow | users_v2_router.py:217 |
| POST | `/api/v2/activities/{id}/members` | 弃用（README 勾选） | activities_v2_router.py:303 |

`routers/README.md` 明示：「本目录中的大部分 API 老旧缓慢，将逐步弃用」。

#### 1.5 死代码 / 空文件 / 源码缺失

- ~~**未注册**（有源码但无 `include_router`）：`routers/activities_router.py`（全部为 `pass` 占位 + 1 个 stub，:18~105）、`routers/v1/users.py`（api 未挂载，不可达）~~ **已删除（2026-10-08，删除前 grep 确认零引用）**。
- ~~**0 字节空文件**：`routers/v1/activities.py`、`routers/v1/groups.py`~~ **已删除（2026-10-08）**。
- ~~**仅有 `.pyc`、无 `.py` 源码**（`routers/__pycache__/`）：`activity_statistics_router`、`mcp_router`、`times_router`（均 cpython-312/315 两份）——仍开放，需从上游或反编译恢复~~ **已查证关闭（2026-10-10）**：`grep -rn` 三模块名全仓零引用（main.py:16-27 注册表无它们、无 `getattr`/`importlib` 动态导入、`routers/__init__.py` 仅 `from . import v1`）；`git ls-files`/`git log --diff-filter=A` 证明其 pyc 从未入 git——即审计时所见 pyc 系被审计部署副本的陈旧未跟踪产物，canonical 仓库中三模块不存在且无人引用，恢复无意义。若部署目录仍有残留 pyc 可直接删除（惰性无害）。
- 非源码残留：`routers/_不要乱动，新表结构改了`、`backend/_不要乱动，新表结构改了`（0 字节标记文件）。

#### 1.6 后端已知 bug（顺带发现）

| 位置 | 问题 |
|---|---|
| ~~activities_v2_router.py:420~~ | ~~`status_cod=` 拼写错误（应为 `status_code`）~~ **已修复（2026-10-08）** |
| ~~groups_v3_router.py:31~~ | ~~`raise HTTPException` 未导入 → `NameError`~~ **已修复（2026-10-08 补导入）** |
| ~~groups_v2_router.py:211~~ | ~~函数名误用 `read_users`（实为组内时长）~~ **已改名为 `read_group_time`（2026-10-08，路由路径不变）** |
| ~~activities_v3_router.py:320 起~~ | ~~无 `return`~~ **确认为 204 空体语义，非故障** |
| xh_router.py:151 | `requests` 重写 TODO |
| ~~main.py:7/13/15~~ | ~~`lazy import` 三行非法语法（服务无法启动）~~ **已修复为正常 import** |
| ~~activities_v3_router.py NewActivityMember~~ | ~~mode 下划线 `social_practice` 与活动类型 `social-practice` 永不匹配~~ **已改为连字符并作为加成员 body schema** |
| ~~前端 ManageHome/ManageGroup~~ | ~~`api.exports.*` 调用名与 `exports.ts` 实际导出名不匹配（运行时必然 TypeError）~~ **已修复（2026-10-08，对齐 `getExport/getExportFile/saveBlob/exportUsers/exportTime/getGroupTemplate`）** |

---

### 2. 前端页面完成度

路由集中在 `src/router/index.ts`（**全部路由均无 `meta`**）。

| Path | 组件 | 完成度 | 依据 |
|---|---|---|---|
| `/user/login` | UserLogin.vue | 完整（登录失败提示已实现） | router:24-28 |
| `/user` | UserHome.vue | 完整 | router:29-33 |
| `/me` | UserProfile.vue | 完整 | router:34-38 |
| `/user/:id[/:action]` | UserPage.vue | 完整：info tab 顶部为义工时间仪表盘（校内/校外/社会实践 + 折算开关，原 UserPageNew 合入并补数据加载 `api.user.time.read`） | UserPage.vue |
| `/activities` | ActivityList.vue | 完整（列表+创建+Excel 导入） | router:49-53 |
| `/activity/details/:id` | ActivityPage.vue | 完整：添加成员弹窗已接线（v3 批量）；doEdit 已收尾（TODO WIP 注释清除） | ActivityPage.vue |
| `/manage` | ManageHome.vue | 完整：创建班级/创建用户弹窗、导出用户表/时长表/下载报表、总览 tab 含数据概览统计卡片 + TOTP | ManageHome.vue |
| `/manage/roles/:role` | ManageRole.vue | 完整 | router:59-63 |
| `/group/:group` | ManageGroup.vue | 完整：添加/移出成员、导出模板、时长统计弹窗（学号/姓名/校内/校外/社会实践） | ManageGroup.vue:23 |
| `/about`、`NotFound` | — | 完整 | router:19-23,74-78 |

**孤儿/占位**：
- ~~`views/user/UserPageNew.vue`（217 行）全仓零引用~~ **已删除（2026-10-08）：时间仪表盘合入 UserPage.vue info tab，补数据加载后孤儿文件移除**。
- ~~ZClassDisplay 按钮 `alert('功能开发中')`~~ **已接线改名/删除（v3），admin 可见**。
- ~~ZActivityMemberDisplay.vue:38 `devNotice` 为死代码~~ **已删除（2026-10-08）**。
- ~~`stores/user.ts:90`：`// TODO 登录失败提示`~~ **已实现（2026-10-08）：ElMessage error 提示**。
- ~~`views/user/UserPage.vue:72`：残留 `console.log(value)`~~ **已删除（2026-10-08）**。

**2026-10-08 新接线**：
- ActivityPage「添加成员」多行弹窗 → `member.insertMany` → `POST /api/v3/activities/{aid}/members`。
- ManageGroup 班级详情：添加成员弹窗 + 每行移出班级（admin\|department）。
- ManageRole：每用户「修改角色」弹窗（checkbox 预填 roles）→ `PUT /api/v3/users/{uid}/roles`。

---

### 3. 前端已接入的接口（`baseURL = /api/`，`plugins/axios.ts:18`）

Activities：`/api/v3/activities`(GET/POST)、`/api/v2/activities/:id`、`/:aid/info|status`、`/:aid/members[:/sid]`、`/api/v3/activities/:aid/members`、`/:aid/members/:user/record`
Groups：`/api/v2/groups`、`/api/v2|v3/groups/:id/users`、`/api/v2|v3/groups/:cid/activities`
Users：`/api/users`、`/api/users/:id`、`/api/v2/users`、`/api/v2|v3/users/:id/activities`、`/api/v3/users/:id/time`、`/api/v2/users/:id/password`、`/api/users/:user/past/:idx`
Platform：`/api/v1/platform/{cert,login,totp,logs}`
Logs：`/api/logs`、`/api/users/:user/logs`
XH：`/xh/api`（`api/xh.js`）

---

### 4. ★ 对账：后端有、前端未实现/未接入

#### 4.1 整块零接入（后端可用）—— 最高优先级

| 后端能力 | 端点 | 前端证据 |
|---|---|---|
| **报表导出** | `/api/exports/users`、`/time`、`/{task_id}`、`/{task_id}/file`、`/reports`、`/reports/download`、`GET /api/exports` | **全部已接线（2026-10-08）**：ManageHome「导出用户表/导出时长表」（建任务→轮询→blob 下载）+「下载报表」（`POST /reports` 取 token → `GET /reports/download?token=` 存 `export.tar.gz`） |
| **Excel 导入活动** | `POST /api/imports/activities` | **已接线（2026-10-08）**：ActivityList「导入义工」弹窗（名称/描述/文件）→ `api/exports.ts` `importActivityExcel` |
| **班级模板导出** | `GET /api/groups/{id}/template` | **已接线（2026-10-08）**：ManageGroup「导出模板」→ blob 下载 |
| **班级时长统计** | `GET /api/groups/{id}/time`（v1）、`/api/v2/groups/{id}/time` | **已接线（2026-10-08）**：ManageGroup「时长统计」弹窗（`api/group/reads.ts` `time()`，ElTable 学号/姓名/校内/校外/社会实践） |
| **添加活动成员** | `POST /api/v2|v3/activities/{aid}/members` | **已接线（2026-10-08）**：ActivityPage「添加成员」弹窗 → `member.insertMany`（v3） |

#### 4.2 前端页面/交互未完成

| 位置 | 缺口 | 对应后端 |
|---|---|---|
| ~~ManageHome.vue 创建班级~~ | **已实现**（弹窗 → `POST /api/v3/groups`） | §1.2a |
| ~~ManageHome.vue 创建用户按钮~~ | **已实现（2026-10-08）**：弹窗（学号/姓名/角色/班级）→ `POST /api/v3/users`，成功后 usersVersion 刷新 | §1.2a users_v3_router.py:323 |
| ~~ActivityPage.vue 添加成员~~ | **已实现**（多行弹窗 → v3 批量加成员） | §1.2a |
| ~~ZClassDisplay.vue 修改/删除~~ | **已实现**（prompt 改名 / confirm 删班 → v3） | §1.2a |
| ~~ManageGroup.vue 班级成员管理~~ | **已实现**（添加成员弹窗 + 移出班级） | §1.2a |
| ~~ManageRole.vue 修改角色~~ | **已实现**（checkbox 弹窗 → `PUT /api/v3/users/{uid}/roles`） | §1.2a |
| ~~UserPageNew.vue~~ | **已删除（2026-10-08）**：时间仪表盘合入 UserPage.vue info tab 并补数据加载 | — |
| ~~stores/user.ts:90~~ | **已实现（2026-10-08）**：登录失败 ElMessage 提示 | — |
| ~~ManageHome.vue 仪表盘空 pane~~ | **已实现（2026-10-08）**：总览 tab 新增数据概览统计卡片（班级/用户/活动总数，ElStatistic + ElCard） | 读取类端点 |

#### 4.3 前端有封装但零引用（接线缺失，非后端缺失）

`api/activity/member.ts`：`insert`、`insertMany`、`read`、`reads`
`api/activity/read.ts`：`read.class`（v2 旧）
`api/user/activity.ts`：`read`、`read_v3`、`read.mine`
`api/group/crud.ts`：`createGroup`（未导出）、`editGroupName`、`deleteGroup`
`api/user/crud.ts`：`getUsers`(v1)、`putUser`、`deleteUser`

> 结论：这些前端封装指向的后端，一部分**后端自己也是 503/410**（建/删/改用户、建/删/改班级），另一部分是**后端可用但前端没接线**（添加成员）。两侧都需处理。

---

### 5. 前端有、后端无/不完整

- 前端定义一个统一 `POST /api/users` 建用户 → 后端 503（users_router.py:98）。
- `putUser`/`deleteUser`（crud.ts）→ 后端 PUT/DELETE 用户 503。
- `editGroupName`/`deleteGroup`/`createGroup`（crud.ts）→ 后端 503。
- `api/xh.js` 的 `/xh/api` 依赖 `xh_router.py:151` 未完成的重写（TODO）。

---

### 6. 建议优先级

1. ~~**P0 — 接线导出/导入**~~ **已完成（2026-10-08）**：用户表/时长表导出（任务轮询+下载）、班级模板导出、Excel 导入义工、报表打包下载（`/reports`）、班级时长统计全部接线。
2. ~~**P0 — 打通管理写入**~~ **已完成（2026-10-08）**：班级写入/角色修改/活动加成员/**创建用户**走 v3 新端点（§1.2a）+ 前端接线（`POST /api/v3/users` 已补端点并接弹窗）。
3. ~~**P1 — 收尾部分实现页面**~~ **已完成（2026-10-08）**：ManageHome 仪表盘统计卡片、ActivityPage `doEdit` 收尾、创建用户弹窗全部落地；UserPageNew 合入 UserPage（时间仪表盘 + 数据加载）。
4. ~~**P1 — 清理死代码**~~ **已完成（2026-10-08）**：删除 `activities_router.py`、`v1/users.py`、`v1/activities.py`、`v1/groups.py`、`UserPageNew.vue`、`devNotice` 死函数、`console.log` 残留。
5. ~~**P2 — 恢复缺失模块**~~ **已关闭（2026-10-10，无需恢复）**：`activity_statistics_router`/`times_router`/`mcp_router` 经查证为审计副本陈旧 pyc 残留——canonical 仓库全仓零引用、从未入 git、main.py 从未注册，非代码库缺口（证据见 §1.5）。
6. ~~**P2 — 修 bug**~~ **已完成（2026-10-08）**：activities_v2 `status_cod`、groups_v2 `read_users` 命名、前端 `api.exports.*` 调用名不匹配均已修。

---

### 附录 A：证据来源

- 后端：`backend/main.py`（路由注册 :130-142）、`backend/routers/**`、`backend/routers/README.md`、`backend/routers/v1/README.md`
- 前端：`frontend/src/router/index.ts`、`frontend/src/api/**`、`frontend/src/views/**`、`frontend/src/plugins/axios.ts`
- 引用检索：`grep -rn` 覆盖 `frontend/src/{api,views,components,dialogs,stores,services}`

### 附录 B：本文件维护

本文件由静态审计生成，反映 **2026-10-08** 时点的代码状态；后端/前端合入后请同步更新此表。

### 更新记录

- **2026-10-10（pyc 悬案关闭）**：查证 §6.5 三模块（`activity_statistics_router`/`times_router`/`mcp_router`）——全仓 `grep` 零引用（main.py:16-27 注册表不含、无 `getattr`/`importlib`/`__import__` 动态导入、`routers/__init__.py` 仅 `from . import v1`、main.py 中 `routers.` 唯一用法为 :142 `routers.v1.platform.api`）；`git ls-files` 与 `git log --diff-filter=A -- backend/routers/__pycache__/*` 双双证明其 pyc 从未被 git 跟踪；本 clone 的 `routers/__pycache__/` 仅有本轮 py_compile 生成的 314 版缓存。结论：审计所见 pyc 系被审计部署副本的陈旧未跟踪产物，canonical 仓库无需恢复，§6.5 关闭，STATUS 待修项全部清零。
- **2026-10-08（STATUS 收尾轮）**：修复 activities_v2 `status_cod` 拼写、groups_v2 `read_users`→`read_group_time` 命名、前端 `api.exports.*` 调用名与导出不匹配（运行时 TypeError）三个 bug；删除死代码 `routers/activities_router.py`、`routers/v1/{users,activities,groups}.py`、`UserPageNew.vue`（时间仪表盘合入 UserPage.vue info tab 并补 `api.user.time.read` 数据加载）、`devNotice` 死函数、`UserPage.vue` `console.log` 残留；新增 `POST /api/v3/users` 建用户端点（users_v3_router.py:323，初始密码=bcrypt(学号)）并接 ManageHome 创建用户弹窗（usersVersion 刷新）；接线「下载报表」（`POST /reports` token → `GET /reports/download` tar.gz）与 ManageGroup「时长统计」弹窗（`api/group/reads.ts` `time()`）；ManageHome 总览 tab 新增数据概览统计卡片（班级/用户/活动总数）；stores/user.ts 登录失败 ElMessage 提示；ActivityPage `doEdit` TODO WIP 清理。验证：后端 `py_compile` 通过、前端 `vite build` 通过（叠加终态）、残留 grep 零匹配。
- **2026-10-08（实施）**：打通「活动添加成员」链路（前端 ActivityPage 弹窗 → `POST /api/v3/activities/{aid}/members`，body 改用 `NewActivityMember`，修 `social-practice` 连字符）；新增 v3 班级写入（建/改名/删班、用户入班/移出班）与 `PUT /api/v3/users/{uid}/roles` 角色修改端点，前端 ZClassDisplay / ManageHome / ManageGroup / ManageRole 全部接线；修 `groups_v3` HTTPException 未导入、`main.py` 三行非法 `lazy import`、ManageHome `x<script>` 脏字符。验证：后端 `py_compile` 通过、前端 `vite build` 通过。
- **2026-10-08（P0 导出/导入接线）**：新增 `frontend/src/api/exports.ts`（导出任务创建/轮询/文件下载、班级模板、Excel 导入、blob 保存，经 `api/export.ts` barrel 挂为 `exports` 命名空间）；ManageHome 头部新增「导出用户表/导出时长表」（POST 建任务 → 轮询至完成 → 自动下载）；ManageGroup 新增「导出模板」；ActivityList 新增「导入义工」弹窗（名称/描述/Excel 文件 → `POST /api/imports/activities`）。验证：`vite build` 通过。


---

## 六、zvp5-gitlab 开发进度对齐（历史记录）

> **2026-10-24：原 `zvp5-gitlab/` 已删除。** 独立审核流、FAQ/资源中心、定向
> WebSocket 推送相关文件归档在 `reference/zvp5-gitlab/`；本节为删除前的历史记录。
>
> 原定 zvp5 后端的开发中代码，作者 zzj 在 GitLab 账号被封禁前留存的快照，**从未正式完成**。
> 本章为 **2026-10-10 静态对账**结果：目录清单、逐模块完成度、不可运行性的硬证据、与主线 §二 的功能面照。结论一句话：**这是一次彻底的后端重写（v5 架构），设计与数据模型基本成形，但实现层几近空白且不可运行**。

### 6.0 与主线的关系（先定性）

`zvp5-gitlab/` 与 §二 的 `zvms4-modified-main/` **不是同一份代码的两个版本，而是两套并行实现**：

| 维度 | 主线 `zvms4-modified-main`（§二） | 本目录 `zvp5-gitlab` |
|---|---|---|
| 数据层 | FastAPI + **Motor（异步 MongoDB）** 双库 `zvms`/`zvms_new` | FastAPI + **SQLAlchemy 2.0 async（关系型库，`create_async_engine`）** |
| 缓存/推送 | 可选 socket.io / APScheduler | **Redis（asyncio）+ WebSocket（PushService）** |
| 鉴权 | JWT + bcrypt + TOTP（已落地） | JWT + bcrypt + 自研 TOTP（**仅设计/半实现**） |
| 域模型 | 用户/班级/活动/成员/日志/导出（依托现役库结构） | 额外引入 **多学校 `School`、审核流程 `Workflow`、FAQ `Help`、资源 `Resource`** 等更"平台化"的表 |

因此二者**无法直接合并**；下文只做「进度对账」，不改动本目录代码。

### 6.1 目录清单与逐模块完成度

共 31 个 `.py`（约 2,183 行）+ 2 个 `.bak` + `README.md` / `LICENSE` / `.gitignore` / `config.example.py`。完成度分档：**D 设计/骨架** · **P 部分实现** · **S 桩（`pass`/空）** · **✗ 语法不过** · **缺失**。

| 路径 | 行数 | 状态 | 说明 |
|---|---|---|---|
| `app.py` | 0 | **S** | **空文件**——无 FastAPI 实例、无路由注册、无入口 |
| `routers/misc.py` | 0 | **S** | **空文件** |
| `routers/v1/users.py` | 97 | **P/S** | `GET/POST/DELETE ''` 三端点全为 `pass`；仅 `batch-query` 有实现，但 `@post` 未写成 `@router.post`、导入未定义名 |
| `routers/v1/platform.py` | 132 | **✗** | 仅 `POST /login`（六类登录分支已写）；**编译失败** |
| `routers/v1/activities.py` | 427 | **✗** | 列表/详情/成员/创建/批量删除五端点已铺开（本目录最大文件），但**编译失败**且含大量拼写错位 |
| `models/__init__.py` | 9 | D | 汇总导入（导入了**不存在的** `relations`） |
| `models/shared.py` | 39 | D | `_col`/`_foreigncol`/`_ienumcol`/`_jsoncol`/`_rel` 等列简写 |
| `models/users.py` | 118 | D | `User`（upload/short/full 三档 schema）+ `Token` + `Token.SYSTEM` |
| `models/groups.py` | 101 | D | `School` / `Class` / `Group` |
| `models/activities.py` | 126 | D | `ActivityTemplate` / `Activity` / `ActivityMember` |
| `models/workflows.py` | 47 | D | `Workflow` / `WorkflowItem` |
| `models/tasks.py` | 31 | D | `SavedTask`（导出/长任务） |
| `models/push.py` | 64 | D | `Notification` / `Notice` |
| `models/mgmt.py` | 53 | **S** | `Device` 为空壳（仅 TODO 占位）；`Log` 引用了不存在的 `LogType.GENERIC` |
| `models/rsrc.py` | 50 | D | `Help`（FAQ）/ `Resource`；`import` 了不存在的顶层 `typings` |
| `dtypes/`（6×） | 216 | D | 枚举与数据类型（见 6.2） |
| `database.py` | 92 | P | async engine、`Json`/`ObjectId`/`Hashed`/`IntegralEnum` 装饰器、`Model`/`Schema`、`RelationTable`、`session` |
| `cache.py` | 4 | P | Redis 客户端 |
| `crypto.py` | 69 | P | bcrypt 校验/哈希、JWT 创建与 `token`/`authz_token` 依赖；**`decode_credentials` 为 `NotImplementedError`**，且引用了未导入的 `db`/`Token`/`errors`/`JWTError` |
| `errors.py` | 154 | P | 完整业务错误码表（32 条，含 4xx/401/403/404/5xx）；`raise_` 依赖未定义的 `utils.module_init` |
| `schemas.py` | 92 | P | 登录判别联合、密保、Workflow 更新请求；引用拼写错误的 `UpdatedMember` |
| `serializer.py` | 60 | P | 自研 `dump`/`dumps`/`make_response`（含 `__ser_keys__` 协议） |
| `tasking.py` | 90 | P | `TaskManager`（后台任务+进度回调）、`PushService`（WebSocket 通知）骨架；引用了未定义的 `NotifyTasks`/`TaskStatus`/`db.session_factory` |
| `utils.py` | 109 | **✗** | `object_id`/`timestamp`/请求元数据/`generate_totp`/`paginate`/`escape_str`/i-enum 校验器；**编译失败**（`operators.or`） |
| `models.py.bak` | — | 备份 | 早期**单文件版**模型（重构中途拆包前的版本） |
| `dtypes.py.bak` | — | 备份 | 旧 dtypes，含拆分时**丢失**的 `ActivityMode`/`WorkflowItemType`/`MemberModificationType`/`DeviceInfo`/`SystemUser` |
| `config.example.py` | 3 | D | `DATABASE_URI` / `REDIS_URI` 样例；`config.py` 本身缺失，需自行复制 |

### 6.2 已定义的数据类型与域模型（设计层，完成度高）

- **枚举/类型**：`UserRole`（IntFlag：SECRETARY/STU_UNION/ADMIN/SYSTEM）、`SchoolType`（学制位标志）、`WorkflowStatus`（=别名 `ActivityStatus`）、`LogType`（70 个业务码）/`LogLevel`、`FaqCategory`、`ActivityTime`（校内/校外/社会实践三轴时长，支持 `+`/`+=`）。
- **域模型**：用户与令牌、学校/班级/组、活动模板/活动/活动成员、审核流程与流程项、保存任务、通知与公告、设备与日志、FAQ 与资源。
- **请求 schema**：登录判别联合（`password_authz` / `password_authr` / `xhbrowser` / `xhdatacenter` / `token` / `recovery`）、`DecodedCredentials`、密保问答、Workflow 多动作更新。

### 6.3 不可运行性的硬证据（2026-10-10 核对）

用 Python 3.12 `compileall` 与静态检索逐条核对，本目录**当前不具备可运行性**：

1. **连语法都不通过（3 个文件）**：
   - `routers/v1/activities.py:144` —— `SyntaxError: invalid syntax. Perhaps you forgot a comma?`（返回字典漏逗号）
   - `routers/v1/platform.py:78` —— `SyntaxError: cannot use assignment expressions with expression`（`and registered := True` 误用海象运算符）
   - `utils.py:106` —— `SyntaxError: invalid syntax`（`operators.or`，模块名/关键字误写）
2. **关键模块缺失**：`models/relations.py`（被 `models/__init__.py` 与 users/groups/activities/rsrc 导入）、`models/relation.py`（push/workflows 的错拼）、`dtypes/activities.py`（实际文件名错拼为 `activitities.py`）、顶层 `typings`（`models/rsrc.py` 引用）、`config.py`（仅有 example）。
3. **包结构不完整**：根目录、`routers/`、`routers/v1/` 均**无 `__init__.py`**。
4. **大量被引用却未定义的名字**（导入即 `NameError`/`ImportError`）：`utils`（`export`/`module_init`/`Token`/`DecodedCredentials`/`db`/`assert_`/`one_value_only`/`xand`/`coerce_ienum_raw`）、`tasking`（`NotifyTasks`/`TaskCallback`）、`database`（`OrmSchema`/`session_factory`）、`dtypes`（`ObjectId`/`ConversionRule`/`DeviceType`/`TaskType`/`TaskStatus`）、`models`（`UserDeviceTable`/`WorkflowItemType`）。
5. **`activities.py` 内的实现级错误**（即便语法修好也无法运行）：`acitivity_oid`/`AcitivityTime`/`Activiy`/`activiy`/`resulr`/`shere` 等拼写错位、`x.user_id` 引用未定义变量、`payload.members` 上未定义的 `all(...)`、`utils.object_id` 漏调用、`TaskManager.run_task` 调用签名不符、`PushService.notify_all` 以类调用等。

### 6.4 已有 / 未有的能力（对照）

| 能力 | 现状 |
|---|---|
| 数据模型与 schema | **已有**（设计层基本齐全，见 6.2） |
| 基础设施（DB/Redis/序列化/错误码/后台任务/WebSocket） | **骨架已有**，多处依赖缺失、不可导入 |
| 应用入口与路由注册 | **没有**（`app.py` 为空） |
| 业务端点 | **极少**：全树仅 **10 个 handler**（`users` 4 个，其中 3 个是 `pass` 空桩；`platform` 1 个 `POST /login`；`activities` 5 个），而 `activities`/`platform` 两文件编译不过 → 真正可用者仅 1–2 个 |
| 鉴权闭环 | **未闭环**：`crypto.decode_credentials` 未实现，登录流程无法真正解密凭证 |
| 设备识别 | `Device` 模型为空壳，`DeviceInfo.from_ua` 仅在旧 `.bak` 里且是 STUB |

### 6.5 与主线功能面对照

| 功能面 | 主线 §二（zvms4-modified-main） | zvp5-gitlab |
|---|---|---|
| 用户 / 班级 / 组 | ✅ 已移植（v2/v3） | 模型已定义，路由基本未写 |
| 活动 / 成员 | ✅ 已移植（v2/v3 + 加成员） | 模型 + 部分路由（编译不过） |
| 审核流程 | ⚠️ 以 `activities.status`/`approver`/`appointee` 表达，无独立流程表 | **设计了独立 `Workflow`/`WorkflowItem`/审核请求表**（主线无） |
| 多学校 | ⚠️ 单校（隐含） | **设计了 `School`/多校 `Class` 模型**（主线无） |
| 日志 | ✅ 已移植（type/level + 35 站点） | 模型 + `LogType` 枚举（未接线） |
| 通知 / 推送 | ✅ socket.io / notifications | 设计有 `Notification`/`Notice` + WebSocket `PushService` |
| 导出 / 长任务 | ✅ exports 已接线 | 设计有 `SavedTask` + `TaskManager` |
| FAQ / 资源 | 无 | 设计有 `Help`/`Resource` |
| 登录 / 鉴权 | ✅ JWT+bcrypt+TOTP 已落地 | 设计有、实现未完成 |

### 6.6 进度评估与结论

- **大体量级**：架构与数据模型完成度高（设计层约八成），**实现层约一成**，入口为 0。整体约 **15%–20%**，且**不可运行**。
- **性质**：这是**另起炉灶的第二套后端**（关系库 + 独立审核流程 + 多学校），不是主线的分支或补丁。
- **"与主线对齐"若要落地，需先定向**：是**续写本目录**（补 `app.py`/`relations.py`/`config.py`、修语法与未定义名、逐步实现端点），还是**以主线为准**（本目录仅作历史留档）。两者数据层根本不同，方向不同则后续工作完全不同。
- 当前处置：**保持原状，仅作历史留档，不参与构建**；开发主线仍为 §二 的 `zvms4-modified-main/`。

### 更新记录

- **2026-10-10（进度对齐）**：对 `zvp5-gitlab/` 做静态对账并落档本章——31 个 `.py`（约 2,183 行）+ 2 个 `.bak`；`compileall` 证得 `routers/v1/{activities,platform}.py` 与 `utils.py` 三处语法错误；核对出 `models/relations.py`、`dtypes/activities.py`、`config.py`、顶层 `typings`、包 `__init__.py` 等缺失面与被引用未定义名清单；确认 `app.py`/`routers/misc.py` 为空、全树仅 10 个 handler（可用者 1–2 个）。结论：设计层约八成、实现层约一成，整体 15%–20%，不具备可运行性；与主线为两套并行实现，需定向后方可续写。

---

## 七、主干合并与 ADB 处置记录（2026-10-12）

> 本次操作：将 `zvp5-dev` 分支的**非 ADB** 内容并入主干 `main`，并按稳健性要求排除 Oracle ADB 适配；同时回填 zzj 的 `origin/main` 文档整合线。

### 7.1 合并内容（`zvp5-dev` → `main`）

`zvp5-dev` 相对原 `main` 共 **13** 个提交，其中 **12** 个（`16eb170…28c939f`，即 Part 8-11 全部改进）已并入主干，仅 `cdf85ad` 被排除（见 7.2）。改动全部落在 `zvms4-modified-main/`，概览：

| 批次 | 内容 | 版本 |
|---|---|---|
| Part 8 | 后端可用状态落地——shield 请求加固中间件按 zvp 语义改写移植（剥离设备会话 / 学海面），运行缺口修复（依赖缺失 / example 字段 / RSA 2048 / 关停竞态），41 项运行时端到端冒烟 | — |
| Part 9 | 四用户报告 bug 修复（双库断裂 / 未登录日志 / 删除跳转）+ CHANGELOG 页面 + logo 替换 | v4.2.0-3 |
| Part 10 | 群组与用户读取双库收敛（权限组列表与成员、搜索去重、roles 合成、班级选择器）+ 身份徽标常显 + PWA SW 即时接管 | v4.2.0-4 |
| Part 11 | changelog 免登录 + PWA 版本自动接管（SW 轮询 30min + controllerchange 自动 reload）+ IndexedDB 本地缓存 + 关于 / 更新日志返回按钮 | v4.2.1-1 |

**主干当前版本：v4.2.1-1**；Part 1-11 全文见保留的 `zvms4-modified-main/STATUS.md`（本文件 §四 仅覆盖至 Part 7）。

### 7.2 ADB 处置（排除）

- **排除提交**：`cdf85ad feat(zvp5-gitlab): 适配 Oracle Autonomous Database (ADB)`
- **处置方式**：快进至 `zvp5-dev` 顶点后，在主干上 `revert` 该提交——`zvp5-gitlab/` 全量还原为 ex-GitLab 快照（`d86cbb6`）状态：`database.py` 的 `String(255)`、models 的 CLOB 化、`config.example.py` 的 ADB 连接示例、README 的 ADB 选型记录**一律不进入主干**。
- **理由**：ADB（甲骨文自治数据库）适配线与主干基准（MongoDB 双库 `zvms` / `zvms_new`）无关，剥离后主干更稳健；独立的 `zvms4-adb-migration` 工作线保持不动、另行处置。
- **校验**：主干全树仅剩 `zvms4-modified-main/STATUS.md` 一处**文字注记**提及 ADB（说明其为独立工作线），无任何 ADB 代码或依赖残留。

### 7.3 与 zzj 文档整合线的合流

主干合并同时回填了 `origin/main` 上 zzj 的 5 个提交（`24cbb15…d00226e`，含「零散文档并入根 README」）。冲突处置：

- `zvms4-modified-main/README.md`、`STATUS.md`：zzj 线删除 / `zvp5-dev` 线修改 → **保留修改**（不丢 Part 8-11 增量）。
- `zvms4-modified-main/DB_ALIGNMENT.md`、`zvp-latest/DEV_STATUS.md`：**随 zzj 线删除**（内容已并入本文件 §三 / §五）。

### 7.4 第二批合并：单库收敛 + CHANGELOG 数据库化（2026-10-12，剥离 ADB）

**背景**：生产 `zgovps-la` 实测运行的是 `zvms4-adb-migration` 线的 **v4.2.2-1**（含 `ffc5ae2` 仓储层+双库收敛、`8fb1e82` CHANGELOG 数据库化）。为让主干与生产功能面对齐，同时保持「主干无 ADB」，这两个提交已按 **Mongo-only** 方式并入主干：

| 提交 | 内容 | 处置 |
|---|---|---|
| `ffc5ae2` 仓储层落地与双库收敛 | `db/` 仓储层（protocols + Mongo 适配器）、routers/util/tasks/conversion 接入仓储层、`database.py` 收敛为单库 `zvms_new`、`misc/migrate_legacy_to_new.py` | **并入**（Mongo 部分） |
| `8fb1e82` CHANGELOG 数据库化 (v4.2.2-1) | `/api/changelog` 路由 + `changelog` 仓储 + `misc/seed_changelog.py` + 前端 `/changelog` 读库、`/about` 版本读库 | **并入**（无 ADB 面） |

**剥离的 Oracle/ADB 面**（不进入主干）：`db/oracle/*`、`db/repos/oracle/*`、`db/models.py`（ADB schema 的 SQLAlchemy Core 镜像）、`db/ddl_check.py`、`docs/ADB_MIGRATION.md`、`docs/ADB_SCHEMA_SPEC.md`、`requirements.txt` 的 `sqlalchemy[asyncio]` / `oracledb`、`settings.example.py` 的 `ORACLE_*`；`db/backends.py` 精简为 Mongo-only 工厂（`get_users_repo` / `get_groups_repo` / `get_changelog_repo`）。ADB 线**完整保留在 `zvms4-adb-migration` 分支**，与主干互不影响。

**校验**：主干全树无 `oracle` / `oracledb` / `sqlalchemy` / `db.zvms` 代码引用（仅 `STATUS.md` / 本文件的历史记述文字）；后端 `compileall` exit 0；`import main` 通过且 `/api/changelog`、`/api/version` 路由在位；工厂返回 `MongoUsersRepo` / `MongoGroupsRepo` / `MongoChangelogRepo`。

### 7.5 第三批合并：ADB 统一（2026-10-14，撤销 7.2 / 7.4 的 ADB 排除）

> 本次操作：将 `zvms4-adb-migration` 分支**整体并入** `main`，撤销 7.2 / 7.4 的「无 ADB」剥离。**ADB 成为唯一数据后端**。

- **代码统一**：主干取得 `db/models.py`、`db/oracle/*`（含 `clob.py`、增量 DDL V001–V005）、`db/repos/oracle/*`、`docs/ADB_*.md`；同时清除 Mongo 一侧的 `database.py`、`db/repos/mongo/*`、`db/etl/*`、`misc/*` 一次性脚本与 `conversion/*`、v1 `routers/activities_router.py`。
- **数据库开关移除**：`ORACLE_ENABLED` 已删除（`settings.example.py`、`OracleConfig`、`db/oracle/session.py`、`main.py` 健康检查改为无条件 Oracle ping），**不存在非 ADB 回退路径**；`ORACLE_ACTIVE` 保留，仅用于在 ADB 实例间选择 prod / test。
- **生产状态**：11 个模块全部走 ADB，带鉴权端到端全 200；`zvms4-mongo` 与历史数据按决策**保留归档**（已无任何代码引用）。
- **附带修正**：数据边界的非法 id 由 500 修正为 400（`db/ids.py::InvalidOid` + 全局异常处理）。
- **义工数据补录（2026-10-14）**：ADB 全量上线后发现生产「少了很多义工」——ETL 完整性护栏隔离了 **42 个 activities + 1712 个 activity_members**（悬空 creator/appointee FK、超长 description、Int64 时间戳、迁移后测试数据）。已按决策补录 41 个（排除测试数据 TestDB）+ 1712 条成员，并新增 `V006__relax_activity_user_fks.sql` 放开 3 个指向已删除账号的 FK。详见 `zvms4-modified-main/docs/ADB_MIGRATION.md` §6.8。

---

## 八、学海（XueHai / 云作业）API 调用调查报告

> **2026-10-24：原两棵参考树已删除。** 下文 `zvp-latest/...` 路径对应归档
> `reference/xuehai/zvp-latest/...`；`zvp5-gitlab/...` 仅保留学海登录相关历史结论。
>
> 调查对象：本仓库各代码线对**学海生态 API**的调用。结论一句话：**真正发起学海 API 调用的是原 `zvp-latest`（参考 zvp 线）与原 `zvp5-gitlab`（学海登录体系）；`zvms4-modified-main`（移植主线）已红线剥离，零调用**。

### 0. 学海生态域名

| 域名 | 角色 | 出现位置 |
|---|---|---|
| `admin.yunzuoye.net` | 学海管理后台 API（`/Other/TransferInterface2`） | `zvp-latest/frontend/src/api/xh.js:12` |
| `ztp.yunzuoye.cloud` | 学海平台 API（`/api/v1/pub/platform/users/{id}/profile`） | `zvp-latest/frontend/src/api/xh.js:31` |
| `oss.yunzuoye.net` | 学海静态资源（应用图标） | `zvp-latest/frontend/src/utils/constants.ts:2,4` |
| `cloud-resource.yunzuoye.net` | 学海前端打包资源（`js/app.js`，`@/utils/axios.ts`） | `zvp-latest/backend/routers/xh_router.py:69`（签名算法出处注释） |

### 1. 调用总览

| # | 调用方 | 形态 | 目标 | 签名 | 用途 |
|---|---|---|---|---|---|
| 1 | `zvp-latest` 后端 `POST /xh/api` | 通用签名代理（任意 URL） | 由前端传入 | `V1` / `encrytred` / `disabled` | 代前端向学海发起带签名的 HTTP |
| 2 | `zvp-latest` 前端 `transfer()` | `POST /../xh/api`（经 1） | `https://admin.yunzuoye.net/Other/TransferInterface2` | `disabled`（不签名） | 学海数据接口透传 |
| 3 | `zvp-latest` 前端 `getXHProfile()` | 经 1+2 | `http://ztp.yunzuoye.cloud/api/v1/pub/platform/users/{userId}/profile` | `disabled` | 取学海用户资料（`username`） |
| 4 | `zvp5-gitlab` 后端 | 登录请求类型 | 学海平板/数据中心设备 | `xhid` / `xhid_md5` | 以学海设备身份登录 ZVMS |

### 2. 后端签名代理 —— `zvp-latest/backend/routers/xh_router.py`

- **挂载**：`main.py:141` `app.include_router(xh_router.app, prefix="/xh")` → 对外端点 **`POST /xh/api`**（`xh_router.py:128`）。
- **请求体**（Pydantic 判别联合，`xh_router.py:102-126`）：`method`、`url`（任意）、`headers`、`body`、`token`、`options{sign_version,response_is_json,response_encoding}`。
- **签名算法 `sign_request`（:51-94）**，三档：
  - **`V1`**（默认，:63-67）：`sign = md5(method + url + t + KEY + body + "Authorization: " + token)`，其中 `t = 当前毫秒`；结果以 `?sign=<sign>&t=<t>`（已有 `?` 则 `&`）附加到 URL。
  - **`encrytred`**（:68-92）：`appCode='WEB002001'`、`appKey=md5('XH'+appCode)`、`timestamp=t`；GET 时把原 query 并入待签名对象；`raw = ''.join(f'{k}={obj[k]}&' for k in sorted(obj))`；`sign = md5(md5(raw + body))`；并写入请求头 `timestamp/sign/X-B3-SpanId/X-B3-TraceId/loginSign(空)/token`。
  - **`disabled`**（:61-62）：原样 URL，不签名。
- **硬编码密钥**（:46）：`KEY = 'eptim]q34imt5b]-q04i5q=fdkfjfsadlkjfasdfrt573df4pltoy]-pn965498d'`，注释标明 *dumped from `xhcore.so`*。
- **出站与 TLS**（:96-98, :152-159）：用 `urllib.request.urlopen(req, timeout=10, context=ssl_ctx)`；`ssl_ctx.check_hostname=False`、`verify_mode=CERT_NONE`，即**关闭证书校验**。
- **签名例外**（:147）：`body` 序列化后 `≥ 1 MiB` 时**不参与签名**（`None`）。
- **响应**（:168-173）：原样回传 `{actual_url, status, headers, body}`；`response_encoding='raw'` 时以字节返回。
- **错误映射**（:174-197）：JSON 解析失败 502、DNS 失败 502、超时 504、连接拒绝/重置 502、编码不存在 400、其它 500。
- **旧实现注释**（:199-201）：`/xhapi.html` 的 `GET /index.html` 已注释掉。

### 3. 前端 —— `zvp-latest/frontend/src/api/xh.js`

- `transfer(options)`（:5-26）：固定 `POST /../xh/api`，代理到 `https://admin.yunzuoye.net/Other/TransferInterface2`，`options.sign_version='disabled'`，把调用参数包进 `body` 的 `header` 字段。
- `getXHProfile(userId = getXuehaiId())`（:28-43）：经 `transfer` 请求 `http://ztp.yunzuoye.cloud/api/v1/pub/platform/users/{userId}/profile`；失败用 `ElMessage` 报错并抛异常。
- **唯一消费点**：`stores/user.ts:68` —— `username = verifiedName ?? window.webView?.getUserName?.() ?? (await import('@/api')).xh.getXHProfile(getXuehaiId()).username`；`stores/user.ts:8` 的静态导入已注释。

### 4. 设备桥接与 UA 判定

- `frontend/src/plugins/ua.ts`：
  - `getXuehaiId(): number` = `window.xhBrowserJava?.getUserId?.() || 0`（:7-9）；
  - `pad()` = `!!getXuehaiId()`（:11）；
  - `isXuehaiDCenter(requireFeature)` 依赖 UA 含 `userId/`、`deviceId/` 与 `window.webView.getUserName`（:13）。
- 后端 UA 门禁放行设备端：`zvp-latest/backend/main.py:82` 允许 UA 含 `com.xh`；:98 识别 `X-Requested-With ∈ {com.xh.browser, com.xh.datacenter}`。
- 学海设备请求头 `Xuehai-Id` 被采集进日志：`zvp-latest/backend/util/log.py:36`（`optional_int(header(req,'Xuehai-Id'))`）、`util/logify.py:26-30`。
- 前端常量含学海资源 URL：`utils/constants.ts:2,4`（`oss.yunzuoye.net`）。

### 5. `zvp5-gitlab` 的学海登录体系

- 请求类型（`schemas.py:20-32`）：
  - `XuehaiBrowserLoginRequest`：`login_type='xhbrowser'`、`device_identifier`、`xhid:int`；
  - `XuehaiDataCenterLoginRequest`：`login_type='xhdatacenter'`、`device_identifier`、`xhid`、`xhid_md5`、`name`、`class_name`。
- 校验（`routers/v1/platform.py:72-99`）：`device_identifier` 必须等于 `x_device_id` 且设备已注册（redis 或 `Device` 表）；按 `xhid` 查 `User`；数据中心登录额外校验 `md5(xhid).hexdigest()==xhid_md5` 与 `user.name==name`（否则 `DO_NOT_STEAL_ACCOUNT`）。

### 6. `zvms4-modified-main`（移植主线）的处置 —— 不调用

移植主线**有意不引入**学海调用面，历史记录见其 `STATUS.md` / `README.md`：

- 未移植：`xh_router.py` 整文件、`frontend/src/api/xh.js`、`routers/v1/platform.py` 学海登录、`util/logify.py`、`util/blocking.py`、`util/logs.py`。
- 剥离的设备面：`src/plugins/axios.ts` 的 Clarity/Xuehai/Device 三请求头与 `_clck`；`src/plugins/ua.ts` 的 `getXuehaiId()`/`xhBrowserJava` 桥接；`main.py` 的 `zvp_session` 设备会话 cookie；登录日志的三设备字段。
- 终局审计：后端全树对 `requests|httpx|aiohttp|urlopen|urllib` **零命中**（无任何出站 HTTP 能力点）；前端对 `xuehai|clarity|_clck|deviceId|xhBrowserJava|yunzuoye|window.open|urlopen` 仅剩历史日志展示/文案层残留。
- 文字/数据残留：`frontend/README.md`、`SECURITY.md`、`TECH_REPORT.md` 提及 XueHai 平板；ADB 线 `db/models.py` 的 `LOGS` 表保留 `XUEHAI_ID NUMBER(18)` 列（仅镜像 `zvms_new.logs` 历史设备 schema，代码零引用）。

### 7. 风险清单（学海调用面）

1. **硬编码签名密钥**：`KEY` 源自逆向 `xhcore.so`，随源码分发即泄露；任何持有者可为学海请求伪造签名。
2. **TLS 证书校验关闭**：`CERT_NONE` + `check_hostname=False`，中间人可篡改/窃听。
3. **开放 URL 签名代理（SSRF）**：`/xh/api` 接受任意 `url`，服务端代为发起请求且回传完整响应头，可被用于探测内网/绕过网络边界。
4. **设备身份与账号绑定**：`xhid`/`device_identifier` 直接映射到 `User`，设备指纹泄露面大。
5. **明文信道**：`getXHProfile` 使用 `http://ztp.yunzuoye.cloud`。
6. **≥1MiB body 免签名**：大请求体绕过签名校验。

### 8. 证据索引

- 后端代理与签名：`zvp-latest/backend/routers/xh_router.py:46,51-94,96-98,128-197`
- 挂载：`zvp-latest/backend/main.py:141`（UA 放行 :82,98）
- 前端调用：`zvp-latest/frontend/src/api/xh.js:5-43`；消费点 `stores/user.ts:68`
- 设备桥接：`zvp-latest/frontend/src/plugins/ua.ts:7-13`；常量 `utils/constants.ts:2,4`
- 日志采集：`zvp-latest/backend/util/log.py:36`、`util/logify.py:26-30`
- 学海登录：`zvp5-gitlab/schemas.py:20-32`、`routers/v1/platform.py:72-99`
- 主线剥离记录：`zvms4-modified-main/STATUS.md`（§红线剥离 / 终局 grep）、`zvms4-adb-migration:zvms4-modified-main/docs/ADB_SCHEMA_SPEC.md`（LOGS.XUEHAI_ID）

---

## 九、ZVAgent 与安全收敛（2026-10-29）

AI 能力统一以 **ZVAgent** 对外（代码与文档中的「智能体 / AI 助手 / Agent」同指此产品；「AI 网关」仍是模块名）。本轮的实现与契约以代码为准，文档入口如下：

| 主题 | 结论 | 文档 |
|---|---|---|
| 内容安全 | 提交瞬间做规则审核 + 灰区 LLM 判定，**fail-closed**：判定跑不起来就拒绝本次回答；对外统一 `AI_SAFETY_REJECTED`（run 提交 422、`/v1` 403）。判定成立即记违规 + 警告 + 按 `BAN_RULES.penalties` **首档立即封号**（原「首次只警告」语义废除）；admin 天然旁路并写 `ai_moderation_unrestricted` | `zvms4-modified-main/docs/AI_AGENT_OPS_DESIGN.md` §10、`zvms4-modified-main/docs/AI_GATEWAY_API.md` §8.5 |
| 会话 | 模型在**建会话时锁定**（PATCH 改模型 409 `AI_CONVERSATION_MODEL_LOCKED`）；会话级思考强度 `low/medium/high`；`chat` 会话模式下线（存量只读，新建 409）；`/v1/chat/completions` 保留为无状态兼容面 | `zvms4-modified-main/docs/AI_CONVERSATION_DESIGN.md` §12 |
| 只读场景 | `POST /api/ai/scenes/{scene}/runs`（`terminal` / `file_edit`）：admin + `ai.agent`，工具只声明只读集，参考内容脱敏后进 prompt 但不落消息表，跳过 30 分钟密码门，场景会话 14 天保留 | `zvms4-modified-main/docs/AI_GATEWAY_API.md` §16 |
| 文件 | `/api/system/files` 与 agent `fs_*` 共同受根目录 jail：只允许 `/www/zvms4` 与 `/home`，越界 403 `FILES_ROOT_FORBIDDEN`；预览图片新增 `POST /api/ai/ocr/file` 路径桥 | `zvms4-modified-main/docs/FILES_API_DESIGN.md` §13、`zvms4-modified-main/docs/AI_GATEWAY_API.md` §2.5 |
| 账号与应用安全 | 登录频控（账号 5 次/分 + IP 60 次/分 → 429 `AUTH_RATE_LIMITED`）、登出轮换 `session_id`、管理写操作密码再认证、重查询读预算 60 次/分/用户（429 `READ_RATE_LIMITED`）、安全响应头 + CSP、shield 418 追责采集、socket.io 仅认 `Authorization` 头与 auth 载荷 | `zvms4-modified-main/zvms4-backend-python/README.md` |
| 日志与审计 | `APP_LOGS` 新增 `client_ip` / `user_agent`（V045）；事件常量集中在 `services/log_events.py`；`AI_PROMPT_AUDIT` 扩 `answer_text` / `tool_calls` / `refused` / `warned`（V046） | `zvms4-modified-main/docs/AI_GATEWAY_API.md` §6.8、`zvms4-modified-main/zvms4-backend-python/docs/database-status.md` |
| 前端 | 会话运行中逐轮见回复、模型只读展示、思考强度三档、归档会话视图与恢复、平台管理可添加班级、日志卡片展示 IP/UA、文件预览 OCR / 编辑器「AI 分析」/ 终端「智能分析」 | `zvms4-modified-main/zvms4-frontend/README.md` |
