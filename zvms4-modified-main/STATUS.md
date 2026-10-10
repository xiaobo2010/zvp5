# STATUS — 模块移植与安全审计（zvp → zvms4-modified）

> **Part 1 · 义工管理** | 日期：2026-10-09
> 任务：把现役 zvp（`~/zvp-build-test`，zvp-latest）的**义工管理**（创建义工 / 添加、修改义工成员）移植进本仓库，并对移植面做「外链 / 翻出平台」危险代码审计。
> 审计方式：静态扫描（grep / AST）+ 全量人工读码 + 独立安全复核；未运行服务、未连接数据库。

---

## 1. 后端端点对齐总表（`zvms4-backend-python`）

| 端点 | zvp 侧能力 | 本仓处理 | 状态 |
|---|---|---|---|
| `GET /api/v3/activities` | 全校列表：分页 / 关键词 / 类型 / 状态过滤 + creator 联表返回 `creator_name` | 新增（`routers/activities_v3_router.py:22`） | 已移植 |
| `POST /api/v3/activities` | 简化创建：body `{name, description, type}` → 201 `{id}` | 新增（:156）；适配见 §3 | 已移植（适配） |
| `GET /api/v3/activities/{aid}/members` | 快速成员列表：`$lookup` `zvms_new.users` 返回 `member_name` | 新增（:197） | 已移植 |
| `POST /api/v3/activities/{aid}/members` | 批量加成员：预校验类型匹配、去重（同 member+mode 时长累加）、插入置 `status=effective`、活动回置 `pending`、204 空体 | 整体替换上游旧实现（:312）；body 换 `NewActivityMember {member, mode, duration>0}` | 已移植（替换） |
| v2 全套（创建 / 详情 / 删除 / 成员增删查 / 改时长 / 改状态 / 改信息） | 与上游等价 | 上游已有，`routers/activities_v2_router.py` 未动 | 已存在 |
| `POST /api/imports/activities`（Excel 导入义工） | — | 与 zvp 侧**逐字节相同**（diff 验证） | 已一致，未动 |

## 2. 前端接线（`zvms4-frontend`，最小改动，UI 设计未动）

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

## 3. 与 zvp 的有意适配（差异清单）

| # | zvp 原样 | 本仓适配 | 原因 |
|---|---|---|---|
| 1 | `date/createdAt/updatedAt` 写 **int 毫秒时间戳** | 写 `datetime.now()`（BSON Date） | 实库 `activities` 日期为 BSON `Date` 且零 null（DB_ALIGNMENT §2/§6），严格 typing `Activity.model_validate` 要求 |
| 2 | v3 创建只插 7 个字段 | 补齐 `appointee=creator`、`approver=""`、`place=""`、`origin="activities"` | 实库全字段非空，缺字段会让 v2 读取路径 `model_validate` 崩 |
| 3 | `user.require('admin','department')`（CurrentUser 对象） | `"admin"/"department" in user.get("per", [])`（本仓 dict 风格） | 两侧鉴权层不同，语义等价 |
| 4 | `db.zvms_new.activities` 属性访问 | `db.zvms_new.get_collection("activities")` | 本仓集合访问惯例 |
| 5 | 创建日志带 level/type 参数 | 仅 `with_text` | 本仓 `inject_log` 不支持 level/type |
| 6 | 校验失败 detail 文案「权限不足」（volunteer_member 侧） | 「Permission denied」 | 与同文件上游 volunteer.py 文案一致；行为（抛 403）一致 |

## 4. 权限对齐（本次根因修复，含一个上游旁路封堵）

**根因**：JWT `per` 由 `util/cert.py:106` 从 `zvms_new.users.roles` 签发，词汇为 `student/secretary/department/admin`；而上游权限助手查 `user.get("perm")` / `"volunteer"`——对现役库**永不匹配**，department 用户在 v2 全部义工写操作被 403。
修复：`util/permission/volunteer.py`、`util/permission/volunteer_member.py` 统一改为 `user.get("per", [])` 含 `admin|department`（zvp 语义）。

**连带发现并封堵的上游权限旁路**：`volunteer_member` 两个校验器原为「返回 `False` 不抛异常」，而 v2 三个调用点（加成员 `activities_v2_router.py:323`、删成员 `:371`、改成员记录 `:529`）`await` 后**忽略返回值**直接落库 → 任何登录用户可写成员数据。现改为与 zvp 一致的**抛 403** 语义（全仓已确认无调用方依赖旧返回值行为），一处改动同时封三个调用点。

## 5. 安全审计结论

**移植面（§1/§2 全部文件）**：无 `requests/httpx/urllib/aiohttp`、无绝对 URL / 外链生成、无 `window.open` / `location` 外跳、无 `eval/exec/subprocess`、无 base64 payload、无硬编码主机 / IP / 凭证。NoSQL 注入面：`query` 一律 `re.escape` 后进 `$regex`；`type/status` 为 `Literal` 白名单；`activity_id`/`member` 走 `validate_object_id`。

**范围外危险代码（zvp 侧存在，均未移植、未触碰）**：

| 位置（zvp 侧） | 内容 | 处置 |
|---|---|---|
| `frontend/src/api/xh.js:12,31` | 学海外呼（`admin.yunzuoye.net` / `ztp.yunzuoye.cloud`）签名代理 | 不移植；义工管理页面无引用 |
| `frontend/src/views/manage/ManageHome.vue:200` | `window.location` 硬编码跳转外部站点 | 不移植 |
| `frontend/src/utils/constants.ts:2,4` | 外部图标 URL | 不移植 |
| `backend/routers/xh_router.py`、`routers/v1/platform.py` | 学海代理 / 平台登录体系 | 不在义工管理范围，未触碰 |

**部署注意（上游既有配置，本次未动）**：`zvms4-frontend/src/plugins/axios.ts:21` `baseURL = 'https://zvp.xiaobopisces.top/api/'` 指向现役 zvp 域名——替换上线时必须改为相对路径 `/api/` 或新域名。

## 6. 验证记录

- 后端：`python3 -m py_compile`（v3 路由 + 两个权限文件）exit 0；危险模式 grep 零命中。
- 前端：`npm ci --legacy-peer-deps`（上游 `unplugin-oxlint`×`oxlint` peer 冲突为纯 dev 工具问题）后 `vue-tsc --noEmit`：**改动文件 0 错误**；存量 6 处上游遗留错误（`App.vue` PWA 虚拟模块/watermark、`ZSelectActivityMode.vue` hybrid 图标、`icons/positions/index.ts`、`plugins/ua.ts`）与本次改动无关。
- 独立安全复核（oracle）：两次派发均因 oracle 模型变体不可用（基础设施故障，非任务问题）失败；改由编排者按同一检查单逐项替代复核（v3 路由 404 行全文通读 + 证据）：
  - 外链 / 外呼 / `eval` / `subprocess` / `print` / 整型时间戳：终局 grep 8 个改动文件（后端 3 + 前端 5）**零命中**（`SWEEP_EXIT=1`）。
  - 注入面：`query` 一律 `re.escape` 后进 `$regex`；`type/status` 为 `Literal` 白名单；`activity_id` 走 `validate_object_id`；成员 body 走 pydantic `NewActivityMember`（`duration gt=0`）。
  - 权限：三处写门均为 `admin|department`（`user.get("per")`）；全仓 grep 确认无调用方依赖 `volunteer_member` 旧「返回 False」语义（v2 三处调用点均为 `await` 后忽略返回值，抛异常语义即正确封堵）。
  - 数据完整性：v3 创建全字段齐备（含 `appointee/approver/place/origin`）且日期为 `datetime`（BSON Date）；`origin="activities"` 属合法 `Literal`；批量加成员「先全量预校验、后写入」、活动回置用 `datetime.now()`；`$convert onError/onNull` 防脏 ObjectId；成员计数切片 `pipeline[:5]` 与「条件 `$match` 恒为阶段」结构一致。
  - **结论：通过。** 注意：本审计为静态验证（本机无后端依赖环境），上线替换前建议在有 Atlas 连接的环境跑一次运行时冒烟（建义工 → 批量加成员 → 列表 / 成员读取回环）。

## 7. 已知遗留 / 偏差

1. `get_user_name` 仍读 legacy `zvms.users`（zvp 同款行为，异常安全回退 "Unknown"）：仅在 `zvms_new.users` 的用户在日志里显示 "Unknown"。两侧一致，未改。
2. v2 单条加成员仍按 zvp 相同方式读 legacy `zvms.users` 校验目标用户——批量主路径已切 v3，单条路径维持两侧一致。
3. `imports_router.py:32` `if not validate_activity_name(name)` truthiness bug（校验永不生效）：zvp 与本仓逐字节相同，维持一致未修。
4. v3 简化创建 `approver=""`、`appointee=creator`、`origin="activities"`：满足本仓严格 typing 的有意适配（见 §3）。
5. v3 `GET members` 无角色门（登录即可读成员姓名）：与 zvp 及本仓 v2 成员端点姿态一致，校内场景可接受。

---

# Part 2 — 用户管理与登录移植（2026-10-10）

> 任务：把现役 zvp 的**用户管理 API**（登录、用户与班级群组管理）移植进本仓库。
> **红线**：记录用户设备 / 绑定用户 ID / 生成外链允许跳转三类代码一律不得迁移；本仓上游自带的同源设备代码在用户管理面一并清除。
> 方式：双侧只读勘察（zvp 源语义 + 本仓现状，两条并行泳道）→ 实施 → 全树危险模式终局 grep → 写后自审（成品代码逐行通读）。未运行服务、未连数据库。

## 8. 后端端点对齐总表（本次新增 / 改造）

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

## 9. 前端接线（最小改动，UI 与模板未动）

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

## 10. 登录链路改造（对齐 zvp 行为，不搬 zvp 端点）

| # | zvp 行为 | 本仓落地 |
|---|---|---|
| 1 | 学号直查 `zvms_new.users` | 登录先按学号查 `zvms_new`，legacy 仅兜底（`users_router.py:43-54`） |
| 2 | `password=None` → 明文与学号比对（初始密码） | `validate_by_cert` 补同款分支（`util/cert.py`）。**原实现对 `password=None` 直接 TypeError→500，初始密码用户全部无法登录——本次移植修复的关键缺陷** |
| 3 | `password_authz` 临时 token 5 分钟 | `jwt_encode` temporary 时长 15min → **5min** |
| 4 | 登录响应含 `profile`（去 password、`_id`/`class_id` 字符串化） | 同款加入响应（`users_router.py:69-84`） |
| 5 | 登录写日志（含全量请求头 / IP / xuehai / net_session） | **仅写业务日志**（url / user / 文本 / 时间），零设备字段，且仅在登录成功后写 |
| 6 | `sid` 会话 ID + `zvp_session` cookie + TOTP | **不移植**（会话绑定属禁止面；TOTP 无上游 UI 为死代码） |

## 11. 禁止面处置（红线执行记录）

| 类别 | zvp 侧位置（未移植） | 本仓上游同源残留（已清除） |
|---|---|---|
| 记录用户设备 | `util/logify.py`（Clarity cookie/头、Xuehai 头、IP、黑名单）、`util/log.py` 全量请求头落库、`main.py` UA 拦截 + `zvp_session` cookie、`platform.py:143-154` 设备核验 | 登录端点 `binding_user_credentials` 依赖与 ZVMSLog 三设备字段（`typings/log.py` 重写为 `{url,user,data,timestamp}`，全部 15 处调用点签名零波及）；`util/logify.py`、`util/blocking.py`、`util/logs.py` 三死文件删除；前端三请求头 + `deviceId` 生成 + `global.d.ts` Clarity 声明 |
| 绑定用户 ID | `xh_router.py` 整文件、`get_user.py` `session_id`、`platform.py:206-220` sid 绑定、前端 `xh.js` + `stores/user.ts:66-86` 设备核验分支 | `ua.ts` `xhBrowserJava` 桥接删除；日志不再存 xuehai_id↔用户关联 |
| 生成外链允许跳转 | `xh_router.py` 任意 URL `urlopen`、`api/xh.js` 外站、`constants.ts` 外部图标、`ManageHome.vue:200` 外跳 | `util/object_id.py` 的 `get_img_token*`（外站图片床 `requests.get`，确认无调用者）+ `import requests` 一并移除；`index.html` 核查无外部追踪脚本（第 5 行为**移除** Cloudflare beacon 的反追踪代码，保留） |

终局 grep：后端全树 `*.py` 对 `logify / binding_user_credentials / is_blocked / get_img_token / randomString` 与 `requests / httpx / aiohttp / urlopen / urllib` **双轮零命中**（后端无任何出站 HTTP 能力点）；前端 `src + types` 对 `clarity / xuehai / _clck / deviceId / xhBrowserJava / yunzuoye / imgbed / window.open / urlopen` 仅剩 §14-1/2 三处展示 / 文案层残留。

## 12. 权限修复（本次根因修复）

1. **词汇断裂**：`util/permission/user.py` 原查 `user.get("perm")`（v1→v2 升级词汇 monitor/volunteer/club，club 分支死代码）→ 统一改现役 JWT `per` 词汇（与 Part 1 §4 同族）。
2. **读权限旁路**：两个校验器原「返回 `False` 不抛异常」，而 `users_v2` activities/time 与 `groups_v2` activities/compliance 四个调用点 `await` 后忽略返回值 → 任何登录用户可读任意用户/班级数据 → 改**抛 403**（同 Part 1 §4 volunteer_member 手法，一处改动封四个调用点）。
3. **secretary 同班核验补全**：zvp 侧注释掉的同班校验按意图实现，比对两侧 `zvms_new.users.class_id`；班级读权限同班校验由 legacy `get_user_class` 改读现役 `class_id`。
4. **`DELETE /api/users/{target}` 无 admin 校验**（任何登录用户可删任意用户、写 legacy 库）→ v1 删除端点 503 封堵（zvp 同款），整类风险归零。
5. `GET /api/users/{oid}` 无鉴权 → 加登录门。

## 13. 验证记录（2026-10-10）

- 后端：`python3 -m compileall` **exit 0**；两轮危险模式 grep 零命中（§11）。
- 前端：`npm run type-check`（vue-tsc）：**改动文件 0 错误**；存量 4 处上游遗留错误（`App.vue` PWA 虚拟模块、`App.vue` watermark、`ZSelectActivityMode.vue` hybrid、`icons/positions/index.ts` Record 断言——`teacher` 入列使该**存量**报错多列一个缺失键，错误本身上游既有，该文件未触碰）。
- **写后自审（按用户要求，成品代码逐行通读）**：两个新路由全文 + 登录改造 + 权限模块复查——权限门完整（admin / department / secretary 同班 / 本人；secretary 探测他人班级统一 403，不泄露存在性）；密码哈希经聚合后在进程内 pop，任何分支不出响应；`query` 全部 `re.escape` 后进 `$regex`；ObjectId 全 `validate_object_id`；角色双重白名单（pydantic Literal + set）；建用户补齐 `roles` 默认值、班级存在性校验；登录日志仅成功路径、零设备字段。**未发现漏洞。**
- 仍属静态验证：上线前建议运行时冒烟——登录（初始密码 → 强制改密 → 正常登录）→ 建用户 → 改角色 → 建班 / 改名 / 删班 → 加/移成员 → 各列表回环；secretary 越班读应 403。

## 14. 已知遗留 / 偏差（Part 2）

1. **展示层历史设备字段**：`ZLogCard.vue:37-41`、`types/log.d.ts` 仍含 clarity/xuehai 渲染与类型——仅为**历史日志数据**展示面（采集已死，新日志不含此字段，`v-if` 不渲染）；按「UI 不动」红线保留。
2. `i18n/locales/about.ts:29` About 页隐私文案提及 Microsoft Clarity/Advertising：纯文案（无脚本加载、无外跳），上游既有，未动。
3. **roles 编辑无前端入口**：后端 `PUT /v3/users/{id}/roles` 已就绪，但上游 UI 无对应控件（zvp 有独立 ManageRole 页）——按「保持上游 UI」红线不新增页面；`api.user.updateRoles` 已导出待接。
4. **用户改名 / 删除不可用**：zvp live 同样封死（503），本仓 v1 写端点同款封堵；UI 按钮保留但会收到 503 提示（与 zvp 行为一致）。
5. `ZUserModification` 的「权限组」复选框为 legacy 模型残面（`zvms_new` 无 permission group）：展示 legacy 数据、提交时忽略——UI 红线下的有意保留。
6. `ZActivityMemberList.vue:128` 批量导入按学号解析用户仍读 legacy v1（Part 1 决策维持，依赖两库 `_id` 一致的既有假设）。
7. 班级时间 / 统计页（`ZGroupUserTimeList` / `ZGroupTimeStatistics`）仍读 legacy v2 time 端点：属时长域，不在本次用户管理范围。
8. `groups_v2` 的 `/{id}/activities`、`/{id}/time`、`/{id}/users` 三个 legacy 读端点保留未动（zvp 亦如此），新消费方一律走 v3。

---

# Part 3 — 日志 API 移植与汉化（2026-10-10）

> 任务：移植 zvp 的 logs API；查看记录项并做汉化支持；尽可能全面记录用户行为；红线同前——不生成外链、不允许跳转外站、不允许随意修改日志内容；并对之前移植的代码做全树复查。
> 方式：双泳道只读勘察（zvp 日志全貌 + 本仓覆盖缺口）→ 实施 → 终局 grep + 写后自审。未运行服务、未连数据库。

## 15. 日志端点与 schema

### 端点（GET-only，无任何日志写/改/删端点）

| 端点 | zvp 侧能力 | 本仓处理 | 状态 |
|---|---|---|---|
| `GET /api/logs`（logs_router） | zvp 另有 `/v1/platform/logs`（含设备筛选） | 重写：admin 门、`page(-1=不分页)/perpage/query(url+data 搜索，已剥 clarity/xuehai 遗留分支)/performer/type/level(逗号分隔 $in)/time_start/time_end(秒)`，行内补 `user_name`（`zvms_new` 名 → legacy 兜底 → 未知用户） | 已移植（设备参数剔除） |
| `GET /api/users/{oid}/logs` | zvp 有（搜 clarity/xuehai） | Part 2 已清理搜索面，保留 API 兼容；前端已统一走 `/logs?performer=` | 已存在 |
| zvp `/v1/platform/logs` 的 `xuehai_id/session/net_session` 参数 | 设备/会话筛选 | **不移植** | 已剔除 |

### Schema（记录项）

`ZVMSLog`（`typings/log.py`）：`{url, user, data, timestamp, type, level}` → 写 `db.zvms.logs`。

- 新增业务字段：`type`（动作分类）+ `level`（默认 info）——取 zvp 新链路语义，设备面字段一律不引入
- **写入目标维持 `zvms.logs`**（有意偏离 zvp：实库 `zvms_new.logs` 6792 条全为 zvp 设备型 schema（xuehai_id/net_session/request.headers），读写它会混 schema 并向管理界面透出设备数据；本仓 97138 条历史 + Part 1/2 写入均在 `zvms.logs`，单一 schema 单一写入点）

`type` 词汇表（18 词，前端 `ZLogCard.vue` 有中文映射）：`user_login / passwd_change / user_create / role_change / past_change / group_create / group_update / group_delete / group_member_add / group_member_remove / activity_create / activity_update / activity_delete / activity_member_add / activity_member_update / activity_member_remove / import_excel / export`

## 16. 汉化与行为记录覆盖

- **35 个日志写入点全部中文化**（33 处 `with_text` + 登录与报告下载 2 处手工构造），文本含操作者真名（新助手 `util/user.py:get_user_display_names` 批量解析：`zvms_new` 优先 → legacy 兜底 → 未知用户）；审核状态值映射中文（生效/待审核/已驳回）
- 相比 Part 2（22 站点）**补埋点 13 处**，封掉 zvp 也存在的缺口：
  1. v2 义工成员移除（原无日志）
  2. v2 义工成员记录修改（时长/模式，原无日志）
  3. v2 义工活动信息修改（名称/描述，原无日志）
  4. v1 用户组四写端点：建/改名/改描述/删（原零日志）
  5. 导出五处：用户列表导出、时长报表导出、报告下载授权、报告文件下载、导出文件下载（原模块零日志——导出即数据外流，必须可审计）
- 修正既有日志**时序错误**：改密、历史身份增删原在校验/写库前写日志（404 也会留下"已修改"记录）→ 全部移到成功后
- **有意不记**：登录失败（`POST /auth` 为无鉴权端点，记失败 = 开放未认证者向日志集合无限注入内容的污染/DoS 面，与 zvp 行为一致，守住「不能随意修改 log 内容」的完整性）；amalgamation（上游已 410 封死）；读操作

## 17. 禁止面处置

| 红线 | 执行 |
|---|---|
| 不生成外链 | 日志 `url` 字段恒为本站 API 地址（`str(request.url)`）；前端渲染纯文本 `{{ }}`，无任何 `<a>`/`window.open`/v-html |
| 不允许跳转外站 | 全前端复查 `window.open / location.href / v-html / :href / target=_blank` **零命中**；后端 RedirectResponse 均为同源单复数重定向 |
| 不能随意修改 log | 日志端点 GET-only；运行时唯一写入点 `typings/log.py` `insert_log`；全后端无对日志集合的 update/delete 代码路径；日志文本仅由服务端以已校验字段拼接 |

## 18. 复查之前的代码（全树）+ 本次修复的残面

复查结论：Part 1/2 移植面与全树无外链生成、无外站跳转、无日志篡改路径。复查发现并**本次已修复**：

1. `logs_router.py` 搜索 `$or` 仍含 clarity/xuehai 分支（Part 2 遗留）→ 重写时剔除
2. `ZLogCard.vue` IP/Clarity/学海三展示行 + 死导入（Part 2 §14-1 遗留）→ 移除，换 type/level 中文徽标
3. `types/log.d.ts` clarity/xuehai/ip 字段 → 对齐新 schema
4. `env.d.ts` ClarityFunction/window.clarity 全局类型声明 → 删除（继 Part 2 删 `global.d.ts` 后的最后一处 Clarity 残面）
5. `about.ts` 隐私文案声称使用 Microsoft Clarity 并含外站 URL 字符串 → 改写为与本仓实际一致的无追踪声明（纠错 + 消除唯一外站 URL 字面量）
6. `GET /api/exports/{task_id}/file` **无鉴权**即可下载用户列表/时长报表（上游遗留，凭 task id 取数）→ 补 `get_current_user` + admin 门（前端走 axios Bearer，合法流程不受影响）+ 补日志

## 19. 已知遗留 / 偏差（Part 3）

1. `misc/remove_classes.py:23` 存在离线 `logs.delete_many`（手跑运维脚本，非 HTTP 面）——记录不移植不删；上线部署时建议移出生产环境
2. 时间筛选参数单位为**秒**（本仓日志 timestamp 为 float 秒；zvp 用毫秒配 BSON Date）——偏离已记录
3. `typings/activity.py:35,76` 的活动模型英文文本方法（非日志写入路径，上游遗留）未动
4. 历史日志（97138 条）无 type/level → 前端类型徽标对旧行留空（v-if 不渲染），`user_name` 正常解析
5. `GET /api/logs` 的 type/level/time 筛选为 API 能力，前端 `ZLogList` 保持上游单搜索框 UI 未接筛选控件（「UI 不动」红线，需要时可后补）

## 20. 验证记录（2026-10-10）

- 后端 `python3 -m compileall` **exit 0**
- 前端 `vue-tsc --noEmit`：**改动文件 0 错误**；存量 3 处上游遗留（App.vue watermark、ZSelectActivityMode hybrid、icons/positions Record 断言）；Part 2 时的 App.vue PWA 虚拟模块错误随 `env.d.ts` 重写消失
- 审计 grep：33 处 `with_text` **全部带 type=**；手工 `ZVMSLog` 构造仅 3 处合法（登录 / 报告下载 / inject_log 本体）；日志集合引用 = 4 读 + 1 写（`insert_log` 唯一写入点）；`_actor_name` 零残留；`get_user_name` 剩余引用均为合法路径（登录兜底 / activity 模型文本 / 定义本身）
- 前端外链/设备面终局 grep **FRONTEND_CLEAN**（window.open / location.href / v-html / :href / target=_blank / clarity / xuehai / _clck / deviceId / xhBrowserJava 全零）
- **写后自审**：logs 端点权限门 / 正则转义 / 分页边界；35 站点日志时序（全部成功后写）；type 词汇表前后端一致性（18 词逐一比对）；导出鉴权修复对前端流程影响（axios Bearer 确认）；**未发现漏洞**
- 仍属静态验证：上线前建议运行时冒烟（登录 → 改密 → 建活动/成员进出 → 导出 → 管理端日志列表/搜索/分页回环，确认 type 徽标与 user_name 正常）

---

# Part 4 — 登录与身份验证 API 移植（2026-10-10）

> 任务：移植 zvp 的 login API 与身份验证 API；红线同前——不能记录设备、不能绑定用户 ID、不生成外链/跳转，且登录/鉴权路径零外呼（「不能容忍任何人越狱/访问外网」）。
> 方式：双泳道只读勘察（exp-5 zvp 登录全量 / exp-6 本仓鉴权现状与差距）→ 实施 → RFC 6238 向量验证 + 终局 grep + 写后自审。

## 21. 移植内容

### 21.1 动态码（TOTP）登录——zvp `*code*` 语义，按用户派生
- zvp 实现：`cktotp` 外部包（本机不可得）+ `aes_key.txt` 单 key **全站共享码**（`platform.py:248-272`）
- 本仓实现（`util/cert.py`）：
  - 纯本地 RFC6238 自实现（hmac/hashlib/struct，零依赖零出网）
  - **按用户派生 secret**：`HMAC-SHA256(aes_key, "totp:{user_id}")`——无状态、无新字段、消除全站共享码风险
  - 登录口令 `*<6位码>*`，30s 步进，接受当前+上一窗口
  - **密码优先、OTP 兜底**（zvp 同款语义）：密码校验通过绝不进 OTP 分支——真实密码形如 `*Abc12345*` 的用户不受影响
  - OTP 登录 token 有效期 1 天（zvp 语义；普通登录 15 天、临时 5 分钟不变）
  - 60s 凭据时间窗仍前置强制（防重放）
- 新端点 `GET /api/v3/users/{id}/totp`（users_v3_router）：admin 取某用户当前动态码（zvp platform totp 的移植落点）；**超出 zvp 的加固：查看即写 `totp_view` warning 日志**（zvp 不记——凭据接触必须可审计）
- 前端入口：暂无 UI（zvp 的 ZDisplayTOTP 未移植），后端+日志已就绪，同 updateRoles 模式记录待接

### 21.2 重置为初始密码——zvp `DELETE /v2/users/{id}/password`
- 新端点 `DELETE /api/v3/users/{id}/password`（users_v3_router）：
  - 鉴权 **compulsory_temporary_token**（zvp `get_authz_user` 同款——凭据类操作先重新验证身份）
  - 权限 admin/department/secretary 同班（zvp 同款；同班校验基于 zvms_new `class_id` 比对且要求双方非空）
  - 置 `password=None` → 恢复真初始态（下次登录走默认密码=学号分支）
  - 写 `passwd_change` 日志（操作者+目标）
- **前端接线（替换既有客户端实现）**：`ZActivityMember.vue` 重置按钮原为客户端方案（临时 token → 把成员密码 PUT 成学号的 bcrypt——**永久丢失 password=None 初始态**，且「初始密码=学号」语义散落前端）；现改为直调 v3 DELETE，语义归服务端，重新验证身份的弹窗流程不变

### 21.3 登录/登出语义修复（勘察发现的真问题）
- **登出清 token**：zvp 的 `removeUser` 清 `localStorage.token`，本仓漏了（登出后 token 残留至下次访问登录页才兜底清除）→ `stores/user.ts` 补 `removeItem`
- **改密死期望移除**：`auth.ts resetPassword` 的 `if (!reset) localStorage.setItem('token', result.data.token)`——后端从不返回 token，`setItem(key, undefined)` 把字面量 `"undefined"` 写进 token 位，**每次自助改密都污染已存 token**（下次重载即伪登出）→ 删死参 `reset` + 死块；全部调用点核对（store / UserLogin 3 参、ZActivityMember 重接 v3）
- 登录日志区分动态码：`用户 X 使用动态码登录了系统`（`used_otp_credential` 实际验证动态码后才标注，非模式匹配——密码恰为 `*...*` 时不会误标）

### 21.4 死代码清理
- `cert.py get_renewed_password`（全仓零引用，删除前 grep 确认）
- `users_router PUT password` 的 `forbid_groups` legacy 查询 + TODO 注释块（每次改密白跑一次无效查询）

## 22. 有意不移植（记录）

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

## 23. 红线核对（Part 4）

- **零外呼**：后端全树 grep `requests|urlopen|httpx|smtplib|urllib|aiohttp` → **OUTBOUND_CLEAN**；TOTP 为纯本地 HMAC，无短信/邮件/学海/第三方投递
- **零设备/会话绑定**：token claims 仍仅 `iss/exp/iat/sub/scope/per/elg/jti`（无 sid/device）；TOTP secret 按用户派生、不落库、不绑设备
- **零外链/跳转**：登录链无 RedirectResponse、无 OAuth 跳转；前端登录页无外链（复查确认）
- zvp 登录链上的 xh 外呼（`getXHProfile` → `xh_router` urlopen）整面未触碰——本仓从无此代码

## 24. 验证记录（2026-10-10）

- 后端 `compileall` **exit 0**
- **TOTP 算法 RFC 6238 附录 B 官方向量 6/6 全过**（SHA1 六向量的 6 位截断；T=2000000000 的期望值经 rfc-editor.org 原文对证——差异源于我方记错向量而非算法）；窗口逻辑（当前+上一窗口接受、更早拒绝）与 per-user secret 派生（确定性、按用户区分）独立测试通过
- 前端 `vue-tsc`：**改动文件 0 错误**；存量 3 处上游遗留不变（App.vue watermark / ZSelectActivityMode hybrid / icons/positions）；实施中曾引入 ZActivityMember 4 参调用错（勘察未覆盖的第 4 调用点），已随 v3 重接消除
- 审计 grep：`get_renewed_password` 零残留；`used_otp_credential` 三站点（定义/导入/调用）签名一致；出网面全树零命中
- 写后自审：密码优先/OTP 兜底次序、`*` 包裹真实密码边界、60s 防重放窗口、临时 token 收紧对前端弹窗流程的兼容性、secretary 同班 None 边界——**未发现漏洞**

## 25. 已知遗留（Part 4）

1. 动态码查看无前端 UI（后端+日志已就绪）——需要时可仿 zvp ZDisplayTOTP 在管理端加按用户取码入口
2. `elg` claim 恒空（zvms_new.users 无 eligibility 字段）——上游遗留占位，未动
3. OTP 短模式（`mode=short` + 动态码）签发 5 分钟临时 token：与 zvp 行为一致，属 admin 测试便利面，无扩大风险（动态码知情者本就 admin 限定）
4. 运行时冒烟仍待有 Atlas 环境执行：学号登录（默认密码/强密码/动态码三路）、改密闭环、重置为初始密码回环、登出后 token 残留复查

---

# Part 5 — zvp 全量 API 面盘点与未移植总账（2026-10-10）

> 背景：Part 1-4 均按移植范围勘察，本次对 zvp 全部 13 个 router（~75 端点）做穷举对照，并补查四次勘察从未看过的模块（通知/邮件/推送/任务/设置/文件上传/socket.io/定时任务），产出未移植总账。方式：单泳道只读穷举（exp-7）。

## 26. 盘点结论

- **zvp API 面已几乎全覆盖**：13 router ~75 端点绝大部分已移植（✅）或功能等价（🟰）；zvp 使用的全部数据库集合本仓均有引用，无反向缺口
- **勘察盲区模块在 zvp 零实现**（不存在即无从移植）：通知路由、邮件/smtp、消息推送、独立任务管理、设置端点、socket.io 事件、定时任务/后台 job——其中 socket.io、apscheduler、notifications 读、activity statistics、mcp 反而是**本仓独有能力**
- zvp 两处死桩（无装饰器函数）本仓已实现：`POST /users/{oid}/past`（加历史身份）、`GET /users/{oid}/notification`（读 `zvms.notifications`）
- zvp `GET /api/cert` 已 410（强制升级）；本仓 `/api/cert` 正常下发公钥——语义不同属部署阶段差异
- zvp 无 v1 activities/times/mcp/statistics router——本仓在这些面是超集

## 27. 未移植总账（按优先级）

| # | 项 | zvp 参照 | 评估 |
|---|---|---|---|
| 1 | **服务端弱密码强制改密**：登录时 `pwd_strength<3` 不发正式 token，走强制改密闭环 | platform.py:119-197 | **首要移植候选**：本仓仅前端校验强度（stores/user.ts:117-135），服务端对弱/初始密码照发 15 天 access token——初始密码（=学号）用户可长期绕过改密（zvp 在服务端拒绝）。建议并入 `/api/users/auth` 增强（弱密码签发 5 分钟临时 token + 前端既有 shouldResetPassword 闭环承接），不新建平行端点 |
| 2 | shield_middleware 路径黑名单 + UA 门禁（418 拦截） | main.py:76-119 | 可选加固面；其中 `zvp_session` cookie 分支（:79-81/:110-118）属红线不随迁 |
| 3 | 日志 request_methods / search_props 过滤增强 | platform.py:296-329 | 可选（Part 3 §19-5 已记录：API 能力就绪未接 UI）；session/net_session/xuehai_id 维持红线拒绝 |
| 4 | 前端整页：UserProfile（/me 个人资料页）、ManageRole（按角色管理页） | frontend/src/views | UI 面缺失，需要时另立任务 |
| 5 | /dev、/stats0.html、前端静态挂载 | main.py:50-53,190 | 调试/部署差异，不移植 |

## 28. 与既有决策的对照（对勘察建议的修正）

- 勘察建议移植 `sid` / `password_authz` 语义——**维持 Part 4 §22 决策不变**：`password_authz` 已由 `mode='short'` 功能等价（临时 token 5 分钟）；`sid` 仅服务 zvp 日志 session 字段，本仓日志 schema（Part 3）无会话维度，不移植
- `POST /v1/platform/login` 平行路由——维持不建；其剩余缺口收敛为本表 #1（服务端弱密码强制），在既有端点上补齐

## 29. 红线拒绝面复核（全量第 5 次确认）

xh_router 任意 URL 代理（:159 urlopen + :46 硬编码签名 KEY）、登录设备绑定（platform.py:143-154）、`zvp_session` 设备会话（main.py:79-118）、clarity/xuehai 采集链、日志设备过滤参数、`_1.py` 日志删除脚本——全部维持拒绝，本仓从未引入。