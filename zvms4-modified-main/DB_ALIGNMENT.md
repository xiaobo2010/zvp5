# DB 对齐：现役 zvp 真实数据库结构（Ground Truth）

> 数据来源：2026-10-09 通过 SSH 登录 `zgovps-la`，读取 `/www/zvp/backend/settings.py` 的 `MONGODB_URI`（MongoDB Atlas 远程集群），用 `mongosh` **只读** dump。
> 采集方式：`listDatabases` + `getCollectionNames` + `findOne()` 字段/类型采样 + `getIndexes()`。
> 本文件是「数据库结构」的唯一权威依据。代码侧推断一律以此为准。

## 0. 连接与库

- 现役 zvp 后端连的是 **MongoDB Atlas 远程集群**（`ac-lmlqdgx-shard-00-01.r51xm9t.mongodb.net`），**不是** zgovps-la 本机的 mongod（本机 mongod 上的 `zvms_new` 只有 2 张空表，与现役无关）。
- `settings.py` 同时定义 `MONGODB_URI` 与 `MONGODB_DB="zvms"`，但 `database.py` 硬编码连接两个库：`zvms` + `zvms_new`。
- 实际存在数据的库：`zvms`（遗留）与 `zvms_new`（现役主库）。
- 除默认 `_id_` 外，**没有任何自定义索引**（含 `zvms.logs` 的 `timestamp_-1_autocreated`）。

## 1. `zvms`（遗留库）

| collection | 文档数 | 字段（采样类型） |
|---|---|---|
| `users` | 1781 | `_id:object, id:string, name:string, sex:string, group:array<string>, password:string, past:array` |
| `groups` | 40 | `_id:object, name:string, description:string, permissions:array<string>, type:string` |
| `activities` | **0（空）** | — |
| `tasks` | 1 | `_id:object, id:object, status:string, format:string, variant:string, export_start:null, export_end:null, task_start:Date, task_end:null, percentage:number, errmsg:string, result:array<object>, allow_cache:boolean, include_description:boolean` |
| `logs` | 97138 | `_id:object, url:string, user:string, clarity:string, data:string, ip:string, timestamp:number` |
| `imports` | 1 | `_id:object, name:string, filename:string, description:string, user:string, date:string, content:object` |
| `notifications` | **0（空）** | — |

## 2. `zvms_new`（现役主库）

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

## 3. 两库并存的关键点（对齐时必须理解）

- **users 两套并存**：`zvms.users`（`sex`/`group`/`past`，遗留 v1 用）与 `zvms_new.users`（`roles`/`class_id`/`session_id`，现役 v2/v3 用），均 1781 条。
- **groups 两套并存**：`zvms.groups`（`name`/`description`/`permissions`/`type`，遗留）与 `zvms_new.groups`（仅 `name`，现役）。
- **activities 只在 `zvms_new`**：`zvms.activities` 为空；所有活动数据在 `zvms_new.activities` + `zvms_new.activity_members`。
- **统计/时长**：`time` / `time_academic_year` / `time_with_origin` / `time_with_origin_stat` / `group_indicators` / `indicators` / `daily_exports` 全部在 `zvms_new`。
- **tasks / imports / 遗留 logs / notifications** 在 `zvms`。
- **`zvms_new.logs`** 是另一套日志（`xuehai_id`/`net_session`/...），与 `zvms.logs` 结构完全不同。

## 4. 现役 zvp 代码的集合归属规律（`~/zvp-build-test/backend`）

- **v1**（`users_router.py`、`groups_router.py`、`util/get_class.py` 等）→ 遗留 `zvms.*`
- **v2 / v3**（`users_v2_router.py`、`groups_v2_router.py`、`activities_v2/v3_router.py`）→ `zvms_new.*`
- **`exports_router.py` / `imports_router.py`** → 用户/班级已用 `zvms_new.*`，任务表仍 `zvms.tasks`
- 例外：`users_router.py` 中「更新用户角色」类端点用 `zvms_new.users`

详见下方改动清单。

## 5. 查询层改动清单（zvms4 → zvp 对齐，已实施）

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

## 6. 模型层结论（未改动的部分及依据）

- `database.py`：与 zp **逐字节相同**，已连 `zvms` + `zvms_new`，无需改动。
- `typings/activity_v2.py`：**保留** zvms4 的 `datetime` + 非空版本，**不采用** zp 的 `int`/可空版本。依据：实库 `zvms_new.activities` 的 `date/createdAt/updatedAt` 为 **BSON Date**（`typeof=object`），且全字段 **null 数=0**；而 `activities_v2/v3_router` 会对实库文档执行 `Activity.model_validate(...)`，若改成 `int`/可空会直接校验失败。zp 的 int typing 与其自身数据不一致，属 zp 侧历史遗留。
- 其余 `typings/*` 两侧完全一致。

## 7. 与 live 结构相关、但未纳入本次对齐的项（待确认）

- `categories`（`conversion/groups.py`）、`debug_descriptions`（`tasks/categorize.py`）：**live 中不存在**，且 zp 无此代码。属 zvms4 独有的一次性/调试脚本所用集合，MongoDB 首次写入会自动创建；如需正式支持需另行建表。
- `routers/groups_v2_router.py` 的成员读取仍用遗留 `db.zvms.users`（`"group": group_id` + `past`）：与 zp 的 groups_v2 行为一致，故保留。
- `routers/times_router.py`（zvms4 独有）：读 `db.zvms.users`，因 zp 无对应端点、且其与 v1 列表语义一致，暂保留遗留库。

## 8. 记录

- 2026-10-09：基于 zgovps-la 现役 Atlas 集群只读 dump 建立本 ground truth；完成查询层 5 文件 11 处对齐 + `typings/group.py` 枚举对齐；`compileall` 通过。

