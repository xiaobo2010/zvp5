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
  - OTP 登录 token 有效期 1 天（zvp 语义；普通登录 15 天（Part 7 起调整为 10 天）、临时 5 分钟不变）
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
| 1 | **服务端弱密码强制改密**：登录时 `pwd_strength<3` 不发正式 token，走强制改密闭环 | platform.py:119-197 | **已于 Part 6 移植**：`/api/users/auth` 弱密码 long 请求仅发 5 分钟临时 token + `weak_password` 标记，强度规则与前端逐字镜像；short 模式不拦（改密流程不受阻），OTP 路径不受影响；见 §30 |
| 2 | shield_middleware 路径黑名单 + UA 门禁（418 拦截） | main.py:76-119 | 可选加固面；其中 `zvp_session` cookie 分支（:79-81/:110-118）属红线不随迁 |
| 3 | 日志 request_methods / search_props 过滤增强 | platform.py:296-329 | 可选（Part 3 §19-5 已记录：API 能力就绪未接 UI）；session/net_session/xuehai_id 维持红线拒绝 |
| 4 | 前端整页：UserProfile（/me 个人资料页）、ManageRole（按角色管理页） | frontend/src/views | UI 面缺失，需要时另立任务 |
| 5 | /dev、/stats0.html、前端静态挂载 | main.py:50-53,190 | 调试/部署差异，不移植 |

## 28. 与既有决策的对照（对勘察建议的修正）

- 勘察建议移植 `sid` / `password_authz` 语义——**维持 Part 4 §22 决策不变**：`password_authz` 已由 `mode='short'` 功能等价（临时 token 5 分钟）；`sid` 仅服务 zvp 日志 session 字段，本仓日志 schema（Part 3）无会话维度，不移植
- `POST /v1/platform/login` 平行路由——维持不建；其剩余缺口收敛为本表 #1（服务端弱密码强制），在既有端点上补齐

## 29. 红线拒绝面复核（全量第 5 次确认）

xh_router 任意 URL 代理（:159 urlopen + :46 硬编码签名 KEY）、登录设备绑定（platform.py:143-154）、`zvp_session` 设备会话（main.py:79-118）、clarity/xuehai 采集链、日志设备过滤参数、`_1.py` 日志删除脚本——全部维持拒绝，本仓从未引入。

---

# Part 6 — 服务端弱密码强制改密（2026-10-10）

> 任务：移植总账 #1（Part 5 §27）。zvp 在登录时以 `pwd_strength<3` 拒发正式 token（platform.py:174-195），本仓原先仅前端校验强度——服务端对弱/初始密码照发 15 天 access token，直接调 API 可长期绕过改密。本次在服务端补上强制闭环。

## 30. 实现设计

- **强度规则逐字镜像前端**（`util/cert.py pwd_strength` ↔ `stores/user.ts validatePasswordStrength`）：8-14 位可打印 ASCII（0x20-0x7E），小写/大写/数字/符号四类字符 ≥3；独立测试 10/10 用例与前端语义一致（含 15 位超限、非 ASCII、纯数字等边界）
- **判定位置**：`validate_by_cert` 返回 `(token, weak_password)`（全仓唯一调用方 `/api/users/auth` 已同步适配）：
  - 弱密码 = 初始态（`password is None`）或 `pwd_strength(明文口令) < 3`
  - **弱 + long**：只签发 5 分钟 `temporary_token`（不给正式 access token）+ 响应新增 `weak_password: true`
  - **弱 + short**：照常放行——改密流程本身不能被堵死
  - **OTP 动态码**：`weak_password` 恒 false（管理员代登不涉及口令，不受影响）
- **前端最小承接**：`LoginResult` 类型加 `weak_password?: boolean`；`store.setUser` 改为「服务端标记 || 前端本地校验」双保险置 `shouldResetPassword`；既有弱密码 UI 闭环（UserLogin 弱密码分支 → 短 token → 强制改密 → 重登）无需改动
- **日志**：弱密码 long 登录写区分文本「用户 X 登录了系统（密码强度不足，仅发放临时授权）」（`user_login` 类型），管理端可审计强制降权发生
- **有意不做**（zvp 同款行为）：改密端点（`PUT /users/{oid}/password`）不校验新密码强度——UI 端已有客户端校验，直连 API 可设弱密码但下次 long 登录会再次强制；闭环自洽

## 31. 验证记录（2026-10-10）

- 后端 `compileall` **exit 0**
- `pwd_strength` 规则镜像独立测试 **10/10 全过**（强弱判定与强度计数均符合前端语义）
- 前端 `vue-tsc`：改动文件（login.d.ts / stores/user.ts）**0 错误**，存量 3 处上游遗留不变
- `validate_by_cert` 调用方 grep 确认全仓唯一（users_router），返回签名变更无遗漏适配
- 写后自审：short 改密流程不被堵、OTP 不受影响、初始密码用户 long 直连仅得 5 分钟临时 token、规则镜像与前端无分歧口径——**未发现漏洞**
- 仍属静态验证：运行时冒烟（弱密码 long 登录应得临时 token + weak_password 标记 → UI 强制改密 → 强密码重登得正式 token）待 Atlas 环境执行

---

# Part 7 — 登录 token 有效期调整（2026-10-10）

> 用户指令：长期登录 token 有效期 15 天 → **10 天**。唯一改动点 `util/cert.py jwt_encode`（long 分支）；临时 token（5 分钟）与 OTP 登录 token（1 天）不变。前述小节中的历史性"15 天"表述（如 Part 6 背景中"原先照发 15 天"）为当时事实，保留不动；当前行为以本节为准。

---

# Part 8 — 后端可用状态落地：运行时验证 + 运行缺口修复 + shield 加固移植（2026-10-11）

> 任务：把 `zvms4-backend-python` 从「静态编译通过」做到**运行时可用**——后端能真实启动、连库、全 API 面闭环可用；可参考 zvp-latest 后端，但其后门/外站跳转/设备面红线不变；最后更新 README。
> 方式：fixer 泳道移植 shield 中间件（规格先行）∥ 编排者搭运行环境（venv/密钥/临时 MongoDB Docker）→ 端到端冒烟脚本三轮迭代 → 全部缺口修复 → 41/41 断言通过。本机 Python 3.14.4 + mongo:7 临时容器（127.0.0.1:27018，验证后已删）。

## 32. shield_middleware 移植（zvp-latest `main.py:55-119` 语义改写，非照抄）

- **引入**（`main.py` `@app.middleware("http") shield_middleware`）：`SEC_PATTERN` 14 词路径黑名单（`[^\w]env` / `..` / `/.` / `.sql` / `.bak` / `api/chat` / `api/network` / `api/file` / `assets/%` / `swagger` / `doc` / `php` / `.py` / `openapi`，与 zvp 同表）+ HTTP 方法白名单（GET/POST/DELETE/PUT/PATCH/HEAD/OPTIONS）+ UA 门禁（User-Agent 须含 "Mozilla"）→ 违规一律 418 `请求已被拦截`，并写固定文案日志 `系统检测到异常请求`（`type=security_alert`，`level=warning`，ZVMSLog 手工构造，try/except 保护不影响响应）。
- **红线剥离（未引入面）**：`zvp_session` cookie / `request.state` 会话指纹（set_cookie 零调用）、`com.xh` 学海 UA 分支与"平板"文案、`APILogger`、全量请求头/body 落盘（zvp 把请求头写进 err.log 的面）、`/dev` 与 `/stats0.html` 调试路由、`sys.stderr` 重定向、zvp 的脏话响应文案。
- **误拦核对**：本仓 19 个路由前缀（`/api/users`、`/api/v2|v3/*`、`/api/health`、`/api/cert`、`/api/version`、`/socket.io`）逐一比对黑名单词，零误拦；冒烟实测正常路径 200。
- **部署注意**：UA 门禁会拦非浏览器客户端——外部健康检查器（uptime 探针等）须携带含 `Mozilla` 的 UA，或只探测 TCP 端口。418 而非 403/404 是与 zvp 一致的故意姿态（对扫描器暴露假象）。

## 33. 运行缺口修复（冒烟实测暴露，全部为本仓/上游真实缺陷）

| # | 问题 | 根因 | 修复 |
|---|---|---|---|
| 1 | `pip install -r requirements.txt` 在 Python 3.14 直接失败 | `pipreqs==0.5.0` requires-python <3.13（pipreqs/yarg/docopt 是生成 requirements 的开发工具，非运行时依赖） | requirements.txt 删 `pipreqs`/`yarg`/`docopt` 三行 |
| 2 | 全后端零出站能力但依赖面含 requests | Part 2 已删全部 `import requests`，requirements 残留 | requirements.txt 删 `requests==2.32.5` |
| 3 | 导出任务 `/api/exports/{id}/file` 500 | `pandas.to_excel` 需要 `openpyxl`，上游 requirements 遗漏该运行时依赖 | requirements.txt 补 `openpyxl==3.1.5`，实测下载 200（合法 xlsx，PK 魔数） |
| 4 | 新部署按 example 配置即启动崩溃 | `settings.example.py` 缺 `LANGUAGE`（`util/calculate.py:6`）与 `CHINESE_FONT`（`util/statement.py:24`） | example 补两字段（zh-CN / NotoSansCJK 路径，后者有内置 fallback 链） |
| 5 | RSA-1024 弱密钥 | `generate_key.sh` 上游 1024 位 | 升 2048（PKCS1_OAEP-SHA256 加密登录凭据，实测登录链正常） |
| 6 | SIGTERM 时 "Cannot use MongoClient after close" 竞态 | `startup_event` 手动 `signal.signal(SIGINT/SIGTERM)` 创建 shutdown task，与 FastAPI `add_event_handler("shutdown")` **双路并发执行** `shutdown_event`，先到者 close 连接后到者炸 | 删手动 signal 注册（uvicorn 本就会在 SIGINT/SIGTERM 触发 FastAPI shutdown handler）+ 删死 import（`signal`/`asyncio` 零引用）；实测 SIGTERM 退出 "Shutting down" 仅 1 次、零竞态 |
| 7 | `get_export_file` 死代码 | `buffer = BytesIO()` 写入后从未使用（上游遗留） | **未动**（行为无影响，属清理面，非本次范围） |

## 34. 运行时验证记录（2026-10-11，41/41 PASS）

- **环境**：`.venv`（Python 3.14.4）+ 过滤后 requirements 全量安装成功；`generate_key.sh` 生成 2048 位密钥；`settings.py` 指向本地 mongo:7 容器；`uvicorn main:app` 启动、107 路由注册。
- **冒烟脚本**（三轮迭代至全绿）：drop+reseed 两库（admin/学生/班级/legacy 兜底），覆盖：
  1. `/api/health`、`/api/cert`（PEM 公钥）
  2. **弱密码强制改密闭环**：初始密码（=学号）long 登录 → 仅 5 分钟临时 token + `weak_password=true` + profile；short 模式不拦；临时 token `PUT /password` 改密 → 强密码重登 `weak_password=false` + 10 天正式 token
  3. 用户与班级：`GET /api/v2/users`（admin，联表 total）、`POST /api/v3/users`（8 位学号校验）、`PUT roles`、`GET profile`、建班/改名、成员列表 `pwdm` 布尔化
  4. 义工：`POST /api/v3/activities`、批量加成员 204、活动列表 `creator_name` 联表、成员列表 `member_name` 联表
  5. **TOTP 闭环**：admin 查码（6 位）→ `*code*` 动态码登录 200
  6. **重置初始密码闭环**：admin（短 token 重新验证）给学生设强密码 → 学生强密码登录 → admin `DELETE /v3/users/{id}/password` → DB `password=None` → 学生初始密码重登
  7. 日志：query/type/level 过滤 + 11 类 type 全覆盖（user_login / passwd_change / role_change / group_create / group_update / user_create / activity_create / activity_member_add / totp_view / security_alert / export）
  8. **导出闭环**：建任务 → 后台完成 → 下载 xlsx（200，合法 PK 文件）
  9. 越权门：学生 token 打 `/api/v2/users`、totp 端点、`/api/logs` 全 403
  10. **shield**：非 Mozilla UA → 418、缺 UA → 418、路径穿越 `/api/../etc/passwd` → 418、正常浏览器 UA → 200 不误拦，且 418 全部落 security_alert 日志
- **退出验证**：SIGTERM 单一 shutdown 路径（见 §33-6）。

## 35. 已知遗留（Part 8）

1. `mark_all_tasks_failed` 在 MongoDB 不可达时会把退出阻塞到 serverSelectionTimeout（默认 30s）——pymongo 默认行为，mongo 在线的正常部署为瞬时操作，未动。
2. 冒烟脚本未入库（依赖运行中的服务与数据库，CI 的 pytest 面另行规划）；本次脚本在 `/tmp/opencode/zvms4-smoke.py`，覆盖清单见 §34。
3. zvp-latest 的 `/dev` 调试页、`/stats0.html`、静态挂载、stderr 落盘维持 Part 5 §27-5 决策不移植。
4. `get_export_file` 的 `buffer` 死代码（§33-7）待后续清理面统一处理。

---

# Part 9 — 生产部署切换 + logo 替换 + 用户报告 bug 存档（2026-10-11）

> 本节为部署与交接记录。§38 的 4 个 bug 为**用户线上报告**，按用户指示**仅存档、本轮不修**，留待新 session 处理；届时**只改本地仓、不部署 zgovps-la**，同步部署等用户指令。

## 36. 部署切换记录（zgovps-la，域名不变 zvp.xiaobopisces.top）

- 形态：nginx（宝塔）静态服务 `/www/zvms4/frontend-dist` + `/api/`、`/socket.io/` 反代 → systemd `zvms4`（uvicorn 127.0.0.1:8018，www 用户）+ mongo:7 容器 `zvms4-mongo`（127.0.0.1:27017，数据 `/www/zvms4/mongo-data`）
- 数据：停旧后端后零丢失捕获 Atlas（128591 docs：zvms 1781/40/97138，zvms_new 2566/54/380/13076/7968）；Atlas 原库未动（冻结备份），`/www/zvms4/dump-final` 为切换快照
- 验证：端到端冒烟 21/21（`/tmp/opencode/zvms4-deploy-smoke.py`，生产数据副本上插临时用户→全链路→清理→计数回归基线）；切换后真实用户 HTTPS 实测在线（访问日志 91×200）
- 部署期代码修复（前后端对齐）：baseURL 环境变量化（`VITE_API_BASE`，默认 `/api`）、删除用户死按钮移除、分组 type 过滤、自助改密返回值、9 处存量类型错误清零（`pnpm build` 全绿）、**导出 users 变体 null class_id 崩溃**（exports_router.py:138，真实库 5 个无班级用户必崩，改为 `if group else ""`）
- 注意：JWT/RSA 密钥全新生成 → **全体用户需重新登录一次**（旧 token 一律 401 → 前端自动跳登录，属预期）
- 部署前置发现：真实库 roles 语义 = 学生缺省 `[]`（2466/2566 空数组，56 department/41 secretary/6 admin 显式），zvp 现役同款（backend platform.py:204-210 原样返回、frontend stores/user.ts:42 position=user.roles）——zvms4-modified 行为与之一致，未改动

## 37. logo 替换记录（本地 + zgovps-la 同步）

- 源：旧 zvp **线上部署版**图标（zgovps-la:/www/zvp/frontend/public.1，母本 favicon.png 2480×2480 RGBA；注意与本地 zvp-latest 快照 md5 不同，以线上为准）
- 目标：zvms4-frontend/public/ 15 文件——直接映射 zvp 现成图标（favicon.png、apple-touch-icon(-180x180)、maskable-512、pwa-64/192/512）+ 从母本生成（favicon.ico 多尺寸、favicon-16/32、android-chrome-192/256、mstile-150、brand.jpeg 200×200、safari-pinned-tab.svg 自包含内嵌）
- `brand.jpeg` 在源码中零引用（死资产），仍一并替换保持一致；`generate-pwa-assets` 的源（public/favicon.png）随之更新
- 脚本：`/tmp/opencode/swap-logo.py`
- 补记（同日 23:05）：版本号 bump **v4.2.0-2**——前端 `AboutView.vue` sub-title `v4.1.12`→`v4.2.0-2`、`package.json` `0.1.0-beta.1`→`4.2.0-2`、后端 `/api/version` 与 `/api/` root 的 `"4.1"`→`"v4.2.0-2"`；`/about` 的 logo img 加 `?v=4.2.0-2` 缓存参数强制取新图（服务器字节本已新，旧观感来自客户端 SW/HTTP 缓存，重载一次即激活新 SW）

## 38. 用户报告 bug 存档（已于 2026-10-11 修复，仅本地仓、未部署，见 §40）

| # | 现象（用户原话摘要） | 已查事实 / 候选根因 | 修复方向 |
|---|---|---|---|
| 1 | **用户身份一栏空白** | 真实库 2466/2566 用户 `roles=[]`（学生身份靠缺省）；登录 profile.roles=[] → `stores/user.ts:37-46` `Array.isArray([])` 为 true → `position=[]` → 身份徽标无内容。zvp 现役同语义但需查其 UI 对空 position 的渲染差异 | 前端 store 对空 roles 兜底 `['student']`；或后端 profile 归一化。**先对照 zvp 线上 UI 行为**（zvp-latest frontend）避免引入新差异 |
| 2 | **用户详细信息显示"班级不存在"，用户判断群组读取不对** | 候选 A：两库班级主键断裂——`zvms.groups`（40 个，legacy 独立 _id）vs `zvms_new.groups`（54 个，独立 _id，DB_ALIGNMENT §1/§2），详情链路若把 legacy `user.group` 的组 id 传给 v2/v3 班级端点必 404；候选 B：class_id=null 的 5 个用户被当"应有班级"查询 | 先定位具体视图与 404 的 API（浏览器复现/nginx 日志），再理顺班级解析链（详情应走 profile.class_id 直取，不经 legacy group） |
| 3 | **logs 中未登录请求被记录成"无此用户"** | shield 写 security_alert 日志 `user=""`（main.py:83-94）；logs_router 行内 user_name 解析（zvms_new→legacy→"未知用户"）对空 user 落入"用户不存在"文案 | 后端对 `user==""` 返回"未登录"（区别于"未知用户"），或前端 ZLogCard 空 user 渲染"未登录" |
| 4 | **删除完义工后应跳转回 /activities** | 删除成功后停留在已删除活动的详情页（delete 调用点 ZActivityDetails.vue:99 / api/activity/delete.ts:9） | 删除成功回调补 `router.push('/activities')` |

## 39. 旧 zvp 停用宣告（zgovps-la）

- `supervisorctl stop zvms4`（旧程序名即"zvms4"，与 systemd 新服务同名）+ conf 改名 `/etc/supervisor/conf.d/zvp-old.conf.disabled` + `reread/update`（程序组已移除，重启机器不会复活）
- 标记文件：`/www/zvp/DEPRECATED-20261011.md`（含回滚步骤：conf 改回 + supervisor start + nginx vhost 备份恢复）
- `/www/zvp` 目录与 Atlas 原库均未删除/未改动

## 40. §38 四 bug 修复记录（2026-10-11，仅本地仓，未部署 zgovps-la）

> 根因比 §38 存档假设更深一层：**bug1 与 bug2 是同一条身份渲染链的两面**。侦察证据链（两路：zvms4 现场 + zvp-latest 现役参照）：用户卡走 v1 `/api/users/{id}` 读 `zvms.users`（返回 legacy group id）→ 前端 `api.group.readOne`（getGroup）打 `/v2/groups/{id}` 只查 `zvms_new.groups` → 404「班级不存在」（axios 拦截器弹 toast）；即便命中，新库班级只有 `{_id,name}` 无 `permissions` → `ZUserPosition` 按 `position.includes(...)` 过滤后零渲染 → 身份栏空白。上游 zvms4 设计意图是班级组带 `permissions:['student']`，迁移后新库丢失该字段。

| # | 根因 | 修复（9 文件，+46/-8） | 验证 |
|---|---|---|---|
| 1 | 身份徽标源自 `group.permissions`，新库班级无该字段 → 零渲染无兜底 | `ZUserGroup.vue`：permissions 空兜底 `['student']`（渲染班级名+学生图标徽标）；error 态分出「未知」不再假 Loading；`UserHome.vue`/`ZActivityMember.vue`：零组时渲染首要角色标签（学生→「学生」，有角色→角色名） | type-check+build 全绿；渲染为模板逻辑，未做浏览器实测 |
| 2 | 两库 `_id` 命名空间断裂：v1 读回 legacy group id 打到 v2 端点必 404 | 后端 `groups_router.get_group` 双库解析（zvms.groups→zvms_new.groups 回退，新库构造 `{_id,name,type:'class'}`）；后端 `users_router.read_user` 双库解析（zvms.users 优先→zvms_new.users 回退，映射 v1 兼容形状，`pop("password",None)` 防 KeyError）——顺带修复 ~785 个纯新库用户卡「无此用户」；前端 `api/group/crud.ts getGroup` 切到 `/groups/{id}` 单次调用 | 本地 e2e 15/15（mongo:7@27018 种子复现双库断裂，legacy/new/orphan 三态全过） |
| 3 | shield 写日志 `user=""`，logs 路由把空 user 落入「未知用户」；前端 ZLogCard 用 ZActivityMember 渲染空 id →「无此用户」 | 后端 `logs_router`：空 user → `user_name="未登录"`（非空仍「未知用户」）；前端 `ZLogCard.vue`：空 user 渲染「未登录」ElTag | e2e 4 态全过（未登录/legacy 命中/新库命中/未知用户保持原样） |
| 4 | 删除回调只 `emits('refresh')` 无导航 | `ZActivityDetails.vue`：删除成功后 `router.push('/activities')`（失败 throw 不跳转） | build 通过 |

- 验证证据：后端 `compileall` 通过；本地起 uvicorn:8019 + mongo:7 种子库，`/tmp/opencode/zvms4-e2e/smoke.py` 15/15 PASS（admin RSA 登录→全链路）；前端 `pnpm type-check` 0 错误、`pnpm build` EXIT=0
- e2e 脚本留存：`/tmp/opencode/zvms4-e2e/`（seed.py/smoke.py/ids.json；mongo 容器 zvms4-e2e-mongo 已删，部署同步验证时可重建复用）
- 语义对照（zvp-latest 现役参照）：bug3 文案对齐旧系统新版日志「未登录」；bug4 为用户明确要求的有意改进（旧系统是 `router.back()`）；bug1 班级名徽标即上游设计意图的代码层兜底（数据层 permissions 缺失不改库）
- 有意未动：`stores/user.ts` position 派生（roles=[] 保持 []，避免影响权限门控语义）、`/v2/groups/{id}` 端点行为（其 403/404 守卫语义保留）、shield 写日志 `user=""` 标记、生产数据
- 部署状态：**未部署**，等用户指令后同步 zgovps-la（前端 dist 重建 + 后端 3 文件同步 + systemd 重启）

## 41. 版本发布规范落地 + CHANGELOG 页面（2026-10-11，仅本地仓，未部署）

- 版本升 v4.2.0-2 → **v4.2.0-3**（承载 §40 四 bug 修复，随下次部署发布）
- 版本串同步 5 处：后端 `main.py:192`（`/api/` root）与 `main.py:296`（`/api/version`）、前端 `package.json`、`/about` 页显示与 favicon 缓存参数（后两者经 `APP_VERSION` 自动引用，不再硬编码）
- 新增：`src/data/changelog.ts`（版本记录唯一数据源，导出 `changelog` 条目数组 + `APP_VERSION`）、`src/views/ChangelogView.vue`（ElTimeline 渲染）；新增 `/changelog` 公共路由、About 页「查看更新日志」入口、`nav.ts` 的 `changelog: '更新日志'` 键
- CHANGELOG 首两版条目：v4.2.0-3（§40 四 bug 修复）、v4.2.0-2（§36/§37 部署切换 + logo 替换 + 部署期修复）
- 发版流程（今后）：changelog.ts 加新版本条目 + package.json 升版本 + 后端 main.py 两处版本串
- 验证：`pnpm type-check` 0 错误、`pnpm build` EXIT=0、后端 `compileall` 通过、全仓版本串扫描仅余 v4.2.0-3（changelog.ts 内 v4.2.0-2 为历史条目属预期）
- 部署状态：未部署，随 §40 一并等用户指令同步 zgovps-la

# Part 10 — 第二批用户报告 bug：权限组/高一2026级/旧 PWA 首页 404（2026-10-12）

## 42. 现象存档（用户原话摘要）+ 生产取证

1. **群组**：团支书、学生会、管理员权限组没有正确显示在管理页的群组中，也没有在个人身份中显示；查看管理员群组的用户根本没用户，其他权限组也一样。
2. **高一2026级**：添加义工和日志时显示「无此用户」；在用户页获取用户列表时查看高一账号返回 `Group not found`。
3. **首页**：访问平台第一页报告 404 错误。

生产取证（zgovps-la 只读，2026-10-12，`/www/wwwlogs/zvp.xiaobopisces.top.log` + mongosh）：

- 404 响应体二分：**27 字节 = `{"detail":"User not found"}`**（v1 用户链，§40 已修未部署）；**22 字节 = `{"detail":"Not Found"}`**（路由不存在）。
- **旧 PWA 铁证**：GET `/api/v1/platform/totp` ×1145、POST `/api/v1/platform/login` ×498、GET `/api/v3/users/{id}/time` ×1072、`/api/v3/users/{id}/activities` ×845 全部 404，referer 均为 `/index.html`（PWA 壳）——大量学生手机仍运行旧 zvp 前端（其 SW 缓存未被新版接管：旧 SW 无 skipWaiting 释放、新 SW 此前无 clientsClaim/主动接管）。当前前端首页实际调用 `/api/v2/users/{id}/time`（存在），旧端点不属于新前端调用面。
- **DB 真相**：权限组（团支书/学生会/管理员）仅存在于 `zvms.groups`（`type:"permission"`，permissions 分别 secretary/department/admin）；高一/蛟一 17 个班级仅存在于 `zvms_new.groups`；高一用户（如 20260101 暴墨涵）legacy 库零命中；权限组成员靠 legacy `user.group` 数组关联（管理员 8 / 团支书 41 / 学生会 41），新库无此关联、角色在 `roles`。
- 登录面：POST `/api/users/auth` 200×184 / 403×197（口令错误与弱密码临时授权）/ 404×22（库内无此学号），未见系统性登录故障。

## 43. 根因与修复（前后端 11 文件 + e2e）

| # | 根因 | 修复 |
|---|---|---|
| 1 | 管理页群组列表 `ZGroupList`→`/v2/groups` 只查 `zvms_new.groups`，权限组在 legacy | 后端 `get_groups_v2` 追加 legacy `type:'permission'` 行（`{_id,name,type,permissions}`），total 合并；班级在前按 _id 升序、权限组在后按 name |
| 2 | `/v3/groups/{gid}/users` 按 `class_id` 查新库，权限组成员关联在 legacy `group` 数组 → 恒空 | 后端 `get_group_users_v3`：gid 不在 `zvms_new.groups` 时按 legacy 组解析成员，逐成员按学生 id 映射新库 `roles/class_id/class_name`（新库 `_id` 优先，无对应留空） |
| 3 | 角色标签仅在零组时渲染（§40）；且 v1 `read_user` legacy 主路径文档无 `roles` 字段 | 前端 `UserHome.vue`/`ZActivityMember.vue`：存在非 student 角色即常显标签，零组保持 student 兜底；后端 `read_user` legacy 命中后按学生 id 合并新库 `roles` |
| 4 | 添加义工/日志选人走 v1 `/users` 仅查 legacy → 纯新库用户搜不到 →「无此用户」 | 后端 `read_users` 双库合并搜索（name/id + legacy past），按学生 id 去重、新库 `_id` 优先，id 升序，分页语义不变（page=-1 全量） |
| 5 | 生产版 v1 `get_group` 无 zvms_new 回退 → 高一账号 `Group not found` | 已在 §40 修复，本次随部署生效 |
| 6 | 旧 PWA 404：旧 SW 不被新 SW 接管 | `vite.config.ts` workbox 加 `skipWaiting`+`clientsClaim`、`cleanupOutdatedCaches` 开——旧客户端一次导航即被新 SW 接管，旧 workbox 缓存随 activation 清理 |
| 7 | 学生班级选择器走 v1 `/groups`：原 `per==[]` 直接 403，且仅 legacy 37 班、缺高一/蛟一 | 后端 `get_groups` 双库合并 + `type` 参数（class/permission/all）+ 删除空 per 403 门禁（登录即可）；前端 `ZSelectClass` 学生分支 perpage 5→50 |
| 8 | v2 合并副作用：班级指派/选择列表可能混入权限组 | `ZSelectClass` admin 分支、`ZUserModification` 两处 read_v2 数据源过滤 `type!=='permission'`；`ZGroupList` permissions 迭代兜底；`types/group.d.ts` `type`/`permissions` 转可选 |

版本：v4.2.0-3 → **v4.2.0-4**（按 §41 规范：changelog.ts 新条目 + package.json + main.py 两处版本串）。

## 44. 验证（2026-10-12）

- 后端：`compileall` 通过；e2e 扩展后 **34/34 PASS**（seed 补种：3 权限组、legacy 班级、6 legacy 用户、双库同 id 不同 `_id` 用户、纯新库高一班级+用户；断言覆盖 fix1~fix5 全部行为，§40 原有断言保留全过）；orchestrator 复核：重启 uvicorn（磁盘对齐）后独立复跑 **34/34**。
- 前端：`pnpm type-check` 0 错误；`pnpm build` EXIT=0；`dist/sw.js` skipWaiting/clientsClaim/cleanupOutdatedCaches 三标记均命中。
- 红线：diff 新增行 `zvp_session|com.xh|xuehai|APILogger|set_cookie|request.state|stats0|clarity` 零命中；无新依赖。
- 版本串全仓扫描：代码面仅余 v4.2.0-4（changelog.ts 内 v4.2.0-2/-3 为历史条目，属预期）。
- e2e 环境：uvicorn 停止、mongo 容器删除、端口释放；脚本留存 `/tmp/opencode/zvms4-e2e/`（seed/smoke/ids.json），部署同步验证可复用。
- 有意保留：v3 权限组成员 `pwdm` 读 legacy 成员密码位（与 v1 `get_users_in_class` 同语义）；`get_groups` 的 `type` 参数名在局部作用域遮蔽内置名（无害）。
- 部署提示（等下一条指令执行）：前端 dist 重建 + 后端 5 文件同步 + systemd 重启；上线后观察 `/api/v1/platform/*` 调用是否归零（SW 接管生效信号），旧客户端最迟一次导航后切换到新版。