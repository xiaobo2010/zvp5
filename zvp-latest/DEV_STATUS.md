# zvp-latest 开发状态标注

> 审计对象：`zvp-latest/backend`（FastAPI/Python）+ `zvp-latest/frontend`（Vue3 + Vite + TS + Element Plus + pinia）
> 审计日期：2026-10-08 ｜ 方式：静态审计（读取 `main.py` 路由注册、`routers/**` 装饰器、前端 `src/router`、`src/api/**` 及全仓引用检索），**未运行服务**
> 结论均附 `file:line`；无引用依据的判断已显式标注。

---

## 0. 一句话结论

- 后端接口分为两半：**读取类基本可用**；**写入类（建/删/改 用户与班级）大面积被硬编码 503 停写、登录旧接口 410**。
- 前端页面骨架齐全，但**管理端写入操作几乎全是 `alert('功能开发中')` 占位**（`ManageHome`、`ActivityPage`、组/成员组件）。
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
| `routers/groups_v3_router.py` | `/api/v3/groups` | 可用（有 import bug） |
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
| groups_v3_router.py:31（文件 :6 未 import） | 调用 `raise HTTPException` 但未导入 → `NameError` |
| groups_v2_router.py:211 | 函数名误用 `read_users`（实为组内时长） |
| activities_v3_router.py:320 起 | `add_activity_member_v3` 无 `return`（204 语义待确认） |
| xh_router.py:151 | `requests` 重写 TODO |

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
| `/activity/details/:id` | ActivityPage.vue | **部分**：`// TODO WIP`、`devMsg` 占位 | ActivityPage.vue:62,178,232 |
| `/manage` | ManageHome.vue | **部分**：建班级/建用户按钮 = `alert('功能开发中')` | ManageHome.vue:199,209,212 |
| `/manage/roles/:role` | ManageRole.vue | 完整 | router:59-63 |
| `/group/:group` | ManageGroup.vue | 完整（`// TODO use V2 api`） | ManageGroup.vue:23 |
| `/about`、`NotFound` | — | 完整 | router:19-23,74-78 |

**孤儿/占位**：
- `views/user/UserPageNew.vue`（217 行）**全仓零引用**（无 import/route）→ 新版用户页未接线。
- `components/group/ZClassDisplay.vue:14,42,50`、`components/activity/ZActivityMemberDisplay.vue:38`：按钮 `alert('功能开发中')`。
- `stores/user.ts:90`：`// TODO 登录失败提示`。
- `views/user/UserPage.vue:72`：残留 `console.log(value)`。

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
| **添加活动成员** | `POST /api/v2|v3/activities/{aid}/members` | 封装存在但零引用（见 4.3） |

### 4.2 前端页面/交互未完成

| 位置 | 缺口 | 对应后端 |
|---|---|---|
| ManageHome.vue:209,212 | 创建班级 / 创建用户按钮 = `alert('功能开发中')` | 后端同为 503（groups_router:43、users_router:98） |
| ActivityPage.vue:232 | 操作按钮 `devMsg` 占位 | v2/v3 活动接口部分可用 |
| ZClassDisplay.vue:42,50 / ZActivityMemberDisplay.vue:38 | `devNotice` 占位按钮 | 组/成员写入接口部分 503 |
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

1. **P0 — 接线导出/导入**：前端补报表导出、Excel 导入入口（后端 `/api/exports`、`/api/imports` 已可用）。价值最高、后端零成本。
2. **P0 — 打通管理写入**：后端解除 `_raise_exc` 或前端隐藏入口；当前"建用户/建班级"前后端双端均为占位，用户可见但不可用。
3. **P1 — 收尾部分实现页面**：`ActivityPage`（WIP）、`ManageHome` 仪表盘、组/成员占位按钮。
4. **P1 — 清理死代码**：删除未注册 `activities_router.py`、不可达 `v1/users.py`、0 字节 `v1/activities.py`/`v1/groups.py`；决定 `UserPageNew.vue` 是接线还是删除。
5. **P2 — 恢复缺失模块**：`activity_statistics_router`/`times_router`/`mcp_router` 源码已丢失，需从上游或反编译恢复；补 `/api/groups/{id}/time` 前端展示。
6. **P2 — 修 bug**：见 §1.6。

---

## 附录 A：证据来源

- 后端：`backend/main.py`（路由注册 :130-142）、`backend/routers/**`、`backend/routers/README.md`、`backend/routers/v1/README.md`
- 前端：`frontend/src/router/index.ts`、`frontend/src/api/**`、`frontend/src/views/**`、`frontend/src/plugins/axios.ts`
- 引用检索：`grep -rn` 覆盖 `frontend/src/{api,views,components,dialogs,stores,services}`

## 附录 B：本文件维护

本文件由静态审计生成，反映 **2026-10-08** 时点的代码状态；后端/前端合入后请同步更新此表。
