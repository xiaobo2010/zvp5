# zvp-latest 开发状态标注

> 审计对象：`zvp-latest/backend`（FastAPI/Python）+ `zvp-latest/frontend`（Vue3 + Vite + TS + Element Plus + pinia）
> 审计日期：2026-10-08 ｜ 方式：静态审计（读取 `main.py` 路由注册、`routers/**` 装饰器、前端 `src/router`、`src/api/**` 及全仓引用检索），**未运行服务**
> 结论均附 `file:line`；无引用依据的判断已显式标注。

---

## 0. 一句话结论

- 后端接口分为两半：**读取类基本可用**；写入类中 **v1 旧路由仍大面积硬编码 503 停写**，但 **v3 已补齐班级写入（建/改名/删班、用户入班/移出班）与角色修改端点**（见 §1.2a）；登录旧接口 410 不变。
- 前端页面骨架齐全：**活动添加成员、班级改名/删除/创建、班级成员管理、修改用户角色已全部接线 v3 端点**；仅剩「创建用户」按钮仍为 `alert('功能开发中')` 占位。
- **导出（Exports）/导入（Imports）整块后端有实现，前端零接入**——这是最大缺口。
- 后端 `activity_statistics_router`、`mcp_router`、`times_router` **只剩 `.pyc`，源码缺失**。

---

## 1. 后端 API 状态

### 1.1 路由注册总表（`backend/main.py:130-142`）

| 来源 | prefix | 状态 |
|---|---|---|
| `routers/users_router.py` | `/api/users` | 部分可用，写入多条 503 |
| `routers/groups_router.py` | `/api/groups` | 读取可用，写入 503 |
| `routers/exports_router.py` | `/api/exports` | **可用，前端未接入** |
| `routers/imports_router.py` | `/api/imports` | **可用，前端未接入** |
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

### 1.2 可用（前端可用作依据）

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

### 1.2a 2026-10-08 新增 v3 写入端点（本次实施）

| Method | Path | 用途 | 权限 | 定位 |
|---|---|---|---|---|
| POST | `/api/v3/groups`（body `{name, enrollYear?}`）→201 `{id}` | 创建班级 | admin | groups_v3_router.py:313 |
| PUT | `/api/v3/groups/{id}/name` →204 | 班级改名 | admin | groups_v3_router.py:334 |
| DELETE | `/api/v3/groups/{id}` →204 | 删班（成员 class_id 置空） | admin | groups_v3_router.py:361 |
| POST | `/api/v3/groups/{gid}/users/{uid}` →204 | 用户入班 | admin\|department | groups_v3_router.py:388 |
| DELETE | `/api/v3/groups/{gid}/users/{uid}` →204 | 用户移出班 | admin\|department | groups_v3_router.py:420 |
| PUT | `/api/v3/users/{uid}/roles`（body `{roles: list}`）→204 | 修改用户角色（白名单 student/secretary/department/admin/teacher） | admin | users_v3_router.py:271 |
| POST | `/api/v3/activities/{aid}/members`（body `[{member, mode, duration}]`）→204 | 批量添加活动成员（mode 连字符 `social-practice`） | admin\|department | activities_v3_router.py:319 |

### 1.3 停写 503（后端未实现 → 前端对应功能必然不可用）

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

### 1.4 停用 / 弃用

| Method | Path | 状态 | 定位 |
|---|---|---|---|
| POST | `/api/users/auth` | **410 已停用** | users_router.py:50 |
| GET | `/api/users/{oid}/time` | 弃用（README 勾选） | :396 |
| GET | `/api/v2/users/{id}/time` | deprecated | users_v2_router.py:320 |
| GET | `/api/v2/users/{id}/activities` | deprecated/slow | users_v2_router.py:217 |
| POST | `/api/v2/activities/{id}/members` | 弃用（README 勾选） | activities_v2_router.py:303 |

`routers/README.md` 明示：「本目录中的大部分 API 老旧缓慢，将逐步弃用」。

### 1.5 死代码 / 空文件 / 源码缺失

- **未注册**（有源码但无 `include_router`）：`routers/activities_router.py`（全部为 `pass` 占位 + 1 个 stub，:18~105）、`routers/v1/users.py`（api 未挂载，不可达）。
- **0 字节空文件**：`routers/v1/activities.py`、`routers/v1/groups.py`。
- **仅有 `.pyc`、无 `.py` 源码**（`routers/__pycache__/`）：`activity_statistics_router`、`mcp_router`、`times_router`（均 cpython-312/315 两份）。
- 非源码残留：`routers/_不要乱动，新表结构改了`、`backend/_不要乱动，新表结构改了`（0 字节标记文件）。

### 1.6 后端已知 bug（顺带发现）

| 位置 | 问题 |
|---|---|
| activities_v2_router.py:420 | `status_cod=` 拼写错误（应为 `status_code`） |
| ~~groups_v3_router.py:31~~ | ~~`raise HTTPException` 未导入 → `NameError`~~ **已修复（2026-10-08 补导入）** |
| groups_v2_router.py:211 | 函数名误用 `read_users`（实为组内时长） |
| ~~activities_v3_router.py:320 起~~ | ~~无 `return`~~ **确认为 204 空体语义，非故障** |
| xh_router.py:151 | `requests` 重写 TODO |
| ~~main.py:7/13/15~~ | ~~`lazy import` 三行非法语法（服务无法启动）~~ **已修复为正常 import** |
| ~~activities_v3_router.py NewActivityMember~~ | ~~mode 下划线 `social_practice` 与活动类型 `social-practice` 永不匹配~~ **已改为连字符并作为加成员 body schema** |

---

## 2. 前端页面完成度

路由集中在 `src/router/index.ts`（**全部路由均无 `meta`**）。

| Path | 组件 | 完成度 | 依据 |
|---|---|---|---|
| `/user/login` | UserLogin.vue | 完整 | router:24-28 |
| `/user` | UserHome.vue | 完整 | router:29-33 |
| `/me` | UserProfile.vue | 完整 | router:34-38 |
| `/user/:id[/:action]` | UserPage.vue | 完整 | router:39-48 |
| `/activities` | ActivityList.vue | 完整（列表+创建） | router:49-53 |
| `/activity/details/:id` | ActivityPage.vue | **基本完整**：添加成员弹窗已接线（v3 批量）；仅 `doEdit` 尾部残留 `// TODO WIP` | ActivityPage.vue |
| `/manage` | ManageHome.vue | **基本完整**：创建班级弹窗已接线；创建用户按钮仍为 `alert('功能开发中')` 占位（ManageHome.vue:238）；`x<script>` 脏字符已修 | ManageHome.vue |
| `/manage/roles/:role` | ManageRole.vue | 完整 | router:59-63 |
| `/group/:group` | ManageGroup.vue | 完整（`// TODO use V2 api`） | ManageGroup.vue:23 |
| `/about`、`NotFound` | — | 完整 | router:19-23,74-78 |

**孤儿/占位**：
- `views/user/UserPageNew.vue`（217 行）**全仓零引用**（无 import/route）→ 新版用户页未接线。
- ~~ZClassDisplay 按钮 `alert('功能开发中')`~~ **已接线改名/删除（v3），admin 可见**。
- ZActivityMemberDisplay.vue:38 `devNotice` 为死代码（模板未绑定，可清理）。
- `stores/user.ts:90`：`// TODO 登录失败提示`。
- `views/user/UserPage.vue:72`：残留 `console.log(value)`。

**2026-10-08 新接线**：
- ActivityPage「添加成员」多行弹窗 → `member.insertMany` → `POST /api/v3/activities/{aid}/members`。
- ManageGroup 班级详情：添加成员弹窗 + 每行移出班级（admin\|department）。
- ManageRole：每用户「修改角色」弹窗（checkbox 预填 roles）→ `PUT /api/v3/users/{uid}/roles`。

---

## 3. 前端已接入的接口（`baseURL = /api/`，`plugins/axios.ts:18`）

Activities：`/api/v3/activities`(GET/POST)、`/api/v2/activities/:id`、`/:aid/info|status`、`/:aid/members[:/sid]`、`/api/v3/activities/:aid/members`、`/:aid/members/:user/record`
Groups：`/api/v2/groups`、`/api/v2|v3/groups/:id/users`、`/api/v2|v3/groups/:cid/activities`
Users：`/api/users`、`/api/users/:id`、`/api/v2/users`、`/api/v2|v3/users/:id/activities`、`/api/v3/users/:id/time`、`/api/v2/users/:id/password`、`/api/users/:user/past/:idx`
Platform：`/api/v1/platform/{cert,login,totp,logs}`
Logs：`/api/logs`、`/api/users/:user/logs`
XH：`/xh/api`（`api/xh.js`）

---

## 4. ★ 对账：后端有、前端未实现/未接入

### 4.1 整块零接入（后端可用）—— 最高优先级

| 后端能力 | 端点 | 前端证据 |
|---|---|---|
| **报表导出** | `/api/exports/users`、`/time`、`/reports`、`/reports/download`、`/{task_id}`、`/{task_id}/file`、`GET /api/exports` | 全仓无 `/exports` 引用；`api/export.ts` 只是 barrel，非导出接口 |
| **Excel 导入活动** | `POST /api/imports/activities` | 全仓无 `/imports`、`upload_activity` 引用 |
| **班级模板导出** | `GET /api/groups/{id}/template` | 无引用 |
| **班级时长统计** | `GET /api/groups/{id}/time`（v1）、`/api/v2/groups/{id}/time` | `api/group/*` 无 time；无引用 |
| **添加活动成员** | `POST /api/v2|v3/activities/{aid}/members` | **已接线（2026-10-08）**：ActivityPage「添加成员」弹窗 → `member.insertMany`（v3） |

### 4.2 前端页面/交互未完成

| 位置 | 缺口 | 对应后端 |
|---|---|---|
| ~~ManageHome.vue 创建班级~~ | **已实现**（弹窗 → `POST /api/v3/groups`） | §1.2a |
| ManageHome.vue:238 创建用户按钮 | 仍为 `alert('功能开发中')` 占位 | v1 503（users_router:98），v3 未提供建用户端点 |
| ~~ActivityPage.vue 添加成员~~ | **已实现**（多行弹窗 → v3 批量加成员） | §1.2a |
| ~~ZClassDisplay.vue 修改/删除~~ | **已实现**（prompt 改名 / confirm 删班 → v3） | §1.2a |
| ManageGroup.vue 班级成员管理 | **已实现**（添加成员弹窗 + 移出班级） | §1.2a |
| ManageRole.vue 修改角色 | **已实现**（checkbox 弹窗 → `PUT /api/v3/users/{uid}/roles`） | §1.2a |
| UserPageNew.vue | 孤儿页面，未接线 | 新用户页 WIP |
| stores/user.ts:90 | 登录失败提示 TODO | — |

### 4.3 前端有封装但零引用（接线缺失，非后端缺失）

`api/activity/member.ts`：`insert`、`insertMany`、`read`、`reads`
`api/activity/read.ts`：`read.class`（v2 旧）
`api/user/activity.ts`：`read`、`read_v3`、`read.mine`
`api/group/crud.ts`：`createGroup`（未导出）、`editGroupName`、`deleteGroup`
`api/user/crud.ts`：`getUsers`(v1)、`putUser`、`deleteUser`

> 结论：这些前端封装指向的后端，一部分**后端自己也是 503/410**（建/删/改用户、建/删/改班级），另一部分是**后端可用但前端没接线**（添加成员）。两侧都需处理。

---

## 5. 前端有、后端无/不完整

- 前端定义一个统一 `POST /api/users` 建用户 → 后端 503（users_router.py:98）。
- `putUser`/`deleteUser`（crud.ts）→ 后端 PUT/DELETE 用户 503。
- `editGroupName`/`deleteGroup`/`createGroup`（crud.ts）→ 后端 503。
- `api/xh.js` 的 `/xh/api` 依赖 `xh_router.py:151` 未完成的重写（TODO）。

---

## 6. 建议优先级

1. **P0 — 接线导出/导入**：前端补报表导出、Excel 导入入口（后端 `/api/exports`、`/api/imports` 已可用）。价值最高、后端零成本。（**仍未做**）
2. ~~**P0 — 打通管理写入**~~ **已完成（2026-10-08）**：班级写入/角色修改/活动加成员走 v3 新端点（§1.2a）+ 前端接线；唯「创建用户」仍双端占位（后端无 v3 建用户端点）。
3. **P1 — 收尾部分实现页面**：~~ActivityPage 添加成员 / 组/成员占位按钮~~ **已接线**；剩 `ManageHome` 仪表盘空 pane、ActivityPage `doEdit` TODO WIP、创建用户占位。
4. **P1 — 清理死代码**：删除未注册 `activities_router.py`、不可达 `v1/users.py`、0 字节 `v1/activities.py`/`v1/groups.py`；决定 `UserPageNew.vue` 是接线还是删除；可顺带清 `ZActivityMemberDisplay.vue:38` 死 `devNotice`。
5. **P2 — 恢复缺失模块**：`activity_statistics_router`/`times_router`/`mcp_router` 源码已丢失，需从上游或反编译恢复；补 `/api/groups/{id}/time` 前端展示。
6. **P2 — 修 bug**：见 §1.6（余 activities_v2 `status_cod`、groups_v2 `read_users` 命名）。

---

## 附录 A：证据来源

- 后端：`backend/main.py`（路由注册 :130-142）、`backend/routers/**`、`backend/routers/README.md`、`backend/routers/v1/README.md`
- 前端：`frontend/src/router/index.ts`、`frontend/src/api/**`、`frontend/src/views/**`、`frontend/src/plugins/axios.ts`
- 引用检索：`grep -rn` 覆盖 `frontend/src/{api,views,components,dialogs,stores,services}`

## 附录 B：本文件维护

本文件由静态审计生成，反映 **2026-10-08** 时点的代码状态；后端/前端合入后请同步更新此表。

## 更新记录

- **2026-10-08（实施）**：打通「活动添加成员」链路（前端 ActivityPage 弹窗 → `POST /api/v3/activities/{aid}/members`，body 改用 `NewActivityMember`，修 `social-practice` 连字符）；新增 v3 班级写入（建/改名/删班、用户入班/移出班）与 `PUT /api/v3/users/{uid}/roles` 角色修改端点，前端 ZClassDisplay / ManageHome / ManageGroup / ManageRole 全部接线；修 `groups_v3` HTTPException 未导入、`main.py` 三行非法 `lazy import`、ManageHome `x<script>` 脏字符。验证：后端 `py_compile` 通过、前端 `vite build` 通过。
