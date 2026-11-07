# v4.2.4-2 契约：缺陷修复 + 系统运维能力（SSH / 文件 / 数据库内容管理）

> 本文件是本批**唯一真相源**。所有结论标注已核实的 `file:line`；未核实项显式标注。
> 冻结时间：2026-10-18。基线：`main` = `f50d61f`（含前端液态玻璃回退）。

## 0. 范围

| # | 需求 | 类型 | 阶段 |
|---|---|---|---|
| R1 | 删除义工时后端接口报错、用户被"注销" | 缺陷 | Stage 1 |
| R2 | 删除用户 / 修改学号无前端 UI，链路待验证 | 缺失能力 | Stage 1 |
| R3 | 修改身份（团支书/学生会）提示成功但无效；应有的身份验证未进行 | 缺陷 | Stage 1 |
| R4 | 维护模式下普通用户仍能通过管理员入口拿到 token | 安全缺陷 | Stage 1 |
| R5 | system api 新增 SSH 终端 / 本地文件管理 / 数据库内容管理（审计留痕 + 另一位管理员 TOTP） | 新功能 | Stage 2 |
| R6 | 数据库状态页把本地服务器状态分离为独立 tab | 体验改造 | Stage 1 |
| R7 | 首页用户信息「账号状态：正常」移除（详情页/列表页保持） | 体验改造 | Stage 1 |

## 1. 用户裁定记录（2026-10-18）

| 议题 | 裁定 |
|---|---|
| SSH 终端形态 | **全交互真终端**（xterm.js + WebSocket + asyncssh，真 PTY） |
| 文件管理范围 | **整个文件系统 `/`**，仅 `/proc`、`/sys` 虚拟文件系统保护（2026-10-19 修订，原系统目录黑名单已放开），操作需**再次双管理员验证** |
| DB 内容管理范围 | **含 DDL 的完整管理** |
| 双人 TOTP 落地 | **操作者现场输入另一位管理员的 TOTP 码**，服务端校验（复核人 ≠ 操作人 + 一次性消费防重放） |
| 删除用户语义 | **硬删除 + 事务级联**，同时写入**独立缓冲备份表**，**30 天后由垃圾回收任务清理** |
| 活动删除权限 | 按前端既有口径对齐后端：**创建人 OR admin OR department**（主会话默认，用户未否决） |
| 维护模式拒绝口径 | 非 admin 登录 → **拒绝下发 token**，返回现有 503 维护体；管理员照常登录 |
| 身份修改校验 | **三层全补**：二次认证临时票 + 目标侧保护 + 输入白名单校验 |
| 账号状态显示 | 首页移除；用户详情页、列表页保持原样 |

**全局约束修正（由 L2 / L3B 车道实测反馈，主会话已追认）**
- **语言：项目仅注册 zh-CN**（`src/i18n/index.ts:7-9`，全仓无英文 locale 体系）。本批所有新增文案**只补 zh-CN**；契约初稿中的"中英双语"要求**作废**（笔误）。L2 车道已在 `i18n/locales/maintenance.ts` 预置未接线的 `enUS` 导出 → 属死代码，集成阶段统一处置。
- **角色白名单边界**：后端 `AllowedRole = Literal["student","secretary","department","admin","teacher"]`（`routers/users_v3_router.py:46`），**不含 `auditor`/`system`**；而 `auditor` 在后端确有守卫语义（`routers/users_router.py:256`、`routers/groups_router.py:419` 判 `"auditor" not in user["per"]`）→ 该部门账号若存在，角色编辑会 422（fail-safe，不会丢角色，但表现为报错）。**待 L3A 融合后按下述口径收口**：前端在目标账号含白名单外角色时禁用角色编辑并给出说明，不做静默丢弃。

## 2. 已核实的既有事实（Stage 1 相关）

- **Token 口径**：`util/object_id.py:105-108` —— `token_scope` 须为 `access_token`/`temporary_token`；`scope == "long"` 时必须是 `access_token`，否则 `raise_exception`（401）。短期依赖范式：`util/object_id.py:43` `compulsory_temporary_token`（`scope="short"`）。
- **R1 根因**：前端 `api/activity/delete.ts:2,4-6,12` 与 `api/activity/member.ts:2,26,31` 主动送 `temporaryToken`，但后端 `activities_v2_router.py:150,153`（DELETE `/{activity_id}`）与 `:315,319`（DELETE `/{activity_id}/members/{session_id}`）用 `get_current_user` → 401 → `plugins/axios.ts:168-181` 清 token + `removeUser()` 跳登录。
- **R1 附带**：前端允许创建人删除（`components/activity/ZActivityDetails.vue:216-220`），后端仅 admin/department（`util/permission/volunteer.py:12-16`）。
- **R1 死代码**：`api/activity/merge.ts:17` 给已永久下线（410）的 `POST /amalgamation`（`activities_v2_router.py:446-452`）发临时票。
- **R2 现状**：`DELETE /api/users/{target}`（`routers/users_router.py:197-202`）与 `PUT /api/users/{user_oid}`（`:357-365`）均为 503 停用桩；`db/repos/oracle/users.py` 无删除方法、无更新 `business_id` 方法；前端 `components/group/ZUserModification.vue:200-201` 学号可编辑但 `submit()`（`:127-169`）从不提交。
- **R2 数据约束**：学号 = `users.business_id`，`VARCHAR2(32)`，唯一约束 `uq_users_business_id`（`db/oracle/ddl/V001__initial_schema.sql:56`）；校验函数 `util/validation.py:59-94` `validate_number`（仅创建时调用，`users_v3_router.py:142-144`）。
- **R2 级联缺口**：全库**无 `ON DELETE CASCADE`**（`V001__initial_schema.sql:16` 声明由应用层负责）。指向 users 且为 NO ACTION 外键的表：`user_time.user_oid`、`time_academic_year.user_oid`、`time_with_origin.user_oid`、`user_device_bindings.user_oid`。软引用（无 FK）：`activities.creator_oid/appointee_oid`、`activity_members.member_oid`、`imports.user_oid`、`files.owner_oid`。
- **R3 根因**：前端**从不发写身份请求**。`ZUserModification.vue` 修改页签的 `permissionsID`（`:42,217-227`）与 `id` 只做展示；`submit()` modify 分支（`:149-158`）只处理班级增删；`:164-167` 无条件弹"用户已修改"。后端 `PUT /api/v3/users/{user_id}/roles` 存在（`users_v3_router.py:55-81`，admin 校验 `:62-64`，写 `users.roles` `db/repos/oracle/users.py:484-489`），前端 api `updateRoles`（`api/user/crud.ts:137-144`）**零调用者**。
- **R3 副作用**：`_set_roles_stmt` 轮换 `session_id`（`users.py:208-209`）→ 目标用户旧 token 立即 401，须重新登录。
- **R3 缺失校验**：roles 端点无目标侧保护（可改其他 admin / 自我提权）；`groups_v3_router.py:181-212/215-249` 的班级增删未校验目标 group 是否为班级组。可照抄的守卫范式：`users_v3_router.py:104-105`（禁自我封禁）、`util/permission/user.py:38-55`（`credential_change_forbidden`）、`users_v3_router.py:62-64`（admin 判定）。
- **R4 根因**：`/api/users/auth` 被逐字列入 `MAINTENANCE_EXEMPT_PATHS`（`main.py:139`），豁免判定先于 admin 判定（`main.py:163-167`）。"管理员入口"只是前端按钮（`views/MaintenanceView.vue:134`）→ 普通登录页。前端 503 分支：`plugins/axios.ts:129-140`。唯一铸造 JWT 的端点：`routers/users_router.py:94-183`（返回 `:177-183`），铸造链 `util/cert.py:225` → `jwt_encode`（`util/cert.py:151-184`）。
- **R6 现状**：`views/server/ServerDatabase.vue` 共 **9 张 `ElCard` 纵向平铺、无 `ElTabs`**；host 指标（瓦片 `:1104-1208`）与 Oracle 指标同卡，趋势卡同一分组下拉（`:1339-1378`）。后端 host 数据由 `services/host_metrics.py:338-440` 采集，经 `services/db_monitor.py:908` 注入 `/metrics/realtime`（`routers/db_metrics_router.py:57-68`）→ **host 与 Oracle 同端点同源返回**。
- **R7 现状**：首页 `views/user/UserHome.vue:106-109`（key `home.labels.status` / `home.status.normal`，`i18n/locales/home.ts:19,24`）；详情页 `components/activity/ZActivityMember.vue:212-214`；列表页 `components/group/ZGroupUserList.vue:166-171`。
- **R5 地基**：`util/cert.py:129-135` `verify_totp(user_id, code)` **支持校验指定用户**（密钥由 `aes_key.txt` + oid 派生，`:107-112`；步长 60s `:83-84`，**无防重放**）；`routers/system_router.py:36-50` `_require_system_admin` 旁注释已预留"双人授权插入此层"（`:7-8,49`）；审计复用 `app_logs`（`typings/log.py:8-64`、`db/repos/oracle/app_logs.py:285-306`），已有 8 处写入范式；**SSH/终端/文件/进程/DB 内容管理既往零痕迹**（无 paramiko/asyncssh/subprocess/pty 调用）。
- **生产形态**：systemd `zvms4` 单 worker uvicorn `127.0.0.1:8018`，nginx root `/www/zvms4/frontend-dist`；后端 `/www/zvms4/backend`，User=www。curl 自测须带 `-A "Mozilla/5.0"`（否则盾牌 418）。

## 3. Stage 1 契约

### 3.1 R1 删除义工 token 与权限口径

**改动点**
1. `routers/activities_v2_router.py:150-182` DELETE `/{activity_id}`：依赖 `get_current_user` → `compulsory_temporary_token`（照抄 `users_router.py:189`）。
   **已由 L1 车道实测定案（2026-10-18）**：`compulsory_temporary_token`（`util/object_id.py:43`）返回 dict 的键为 `id`、`perm`、`per`、`scope`、`purpose`、`class_id` —— **`per` 可用于权限判定；但 oid 的键名是 `id` 不是 `oid`**（本契约初稿误写为 `oid`，以本条为准）。该依赖强制 `token_scope == "temporary_token"`，长期票必然 401。`inject_log` 的底层 `authenticated_log_user` 接受两种票据，故与删除路由并列使用不会冲突。
2. `routers/activities_v2_router.py:315-349` DELETE `/{activity_id}/members/{session_id}`：同上。
3. 权限口径：删除活动放行 **创建人 OR admin OR department**；实现位置 `util/permission/volunteer.py`（新增或扩展判定函数），不得放宽其它操作（更新/读取）的现有口径。
4. 补对象 id 校验：两条 DELETE 路由对 `activity_id`/`session_id` 调用 `validate_object_id`（对照 `activities_v2_router.py:140`）。
5. `api/activity/member.ts:35` 前端判定改为与本服务统一响应形状一致（当前判 `result.status === 'ok'` 而后端返回 `{"detail": ...}` → 静默失败）。
6. 死代码：`api/activity/merge.ts:17` 指向 410 桩的调用，删除或改为不再请求（若无调用方则删除文件）。
   **L1 车道处置（2026-10-18）**：全仓确认零调用方，但 `src/api/activity/index.ts:7` 仍再导出该模块（该文件不在 L1 白名单）→ 直接删文件会让 `type-check` 报 `TS2307`。故取"改为不再请求"：保留同签名默认导出、函数体直接抛错、零网络请求。**是否连带删除 `merge.ts` 与 `index.ts:7` 的再导出，留待集成阶段由主会话统一处置。**

**验收**：后端新增测试覆盖「临时票可删除、长期票拒绝、创建人可删、非创建人且非 admin/department 403、非法 id 400/422」；前端 type-check 通过；不得改动其它路由的依赖。

### 3.2 R4 维护模式拒绝下发 token

**改动点**
1. `routers/users_router.py:94-183` 登录端点：凭据校验通过、**拿到目标用户的 `roles` 之后**，若 `maintenance` 开启（用 `db/system_state.py:299-305` 的 fresh 读）且目标用户**非 admin** → 拒绝下发 token；管理员正常下发。
   **L2 车道实现定案（2026-10-18，主会话已追认）**：响应体用**顶层形状** `{"detail": "系统维护中，仅管理员可登录", "maintenance": true}`（router 内 `return JSONResponse(503, ...)`，与 `main.py:169-172` 逐字同形，仓内已有先例 `activities_v2_router.py:78,411`）——**不是** `raise HTTPException(503, detail={...})` 的嵌套形状（那样会产生 `{"detail":{"detail":...,"maintenance":true}}`，落进 `plugins/axios.ts:198-233` 通用分支导致双 toast，且不命中既有 503 分支）。实现位置 `routers/users_router.py:139-166`，闸门只读已取到的库文档，**零额外查库**。
   **闸门覆盖面（主会话追认）**：作用于整个 `/api/users/auth`，含 `purpose=verify` 的 5 分钟短票；`user_doc` 缺失时 **fail-closed** 拒绝。审计 ltype `auth_maintenance_blocked`、level `warning`，正文不含任何凭据。
2. 审计：拦截事件写 `app_logs`（沿用 `inject_log` 范式），ltype 建议 `auth_maintenance_blocked`，level `warning`。
3. 前端：`views/user/UserLogin.vue` 处理该响应（提示"维护中，仅管理员可登录"）；503 分支复用 `plugins/axios.ts:129-140` 既有逻辑，不得让该响应清 token 或触发"登录已过期"。
4. i18n 文案加在 `i18n/locales/maintenance.ts`（或 user 命名空间），中英双语。

**验收**：新增测试：维护开启 + 普通用户 → 503 且无 `token` 字段；维护开启 + admin → 200 且含 token；维护关闭 → 行为不变。**注意**：现有 `/api/users/auth` 的豁免**保持不变**（否则无法登录恢复维护），拒绝逻辑在端点内实现。

### 3.3 R2 + R3 用户管理闭环

**后端（3.3A）**
1. 身份：`PUT /api/v3/users/{user_id}/roles` 补目标侧保护 —— 禁止修改**其他 admin** 的角色、禁止**自我提权**（可照抄 `util/permission/user.py:38-55` 与 `users_v3_router.py:104-105` 范式）；操作者仍须 admin。
2. 学号：新增 `PUT /api/v3/users/{user_id}/student-id`（请求体 `{"id": "<8位学号>"}`），校验：`validate_number`（`util/validation.py:59-94`）+ 唯一冲突返回 409 + 写 `app_logs`（ltype `student_id_change`）+ 轮换 `session_id`。
3. 删除用户：新增 `DELETE /api/v3/users/{user_id}`，admin 守卫（照抄 `groups_v3_router.py:152-178`），**事务内**：
   a. 先把该用户的 `users` 行 + 关联行（`user_time` / `time_academic_year` / `time_with_origin` / `user_device_bindings` / `activity_members` / `files.owner_oid` 软引用处理）序列化写入**回收站表**（新表，见下）；
   b. 清理关联行（软引用不做物理删除，按语义置空或保留，必须逐项在实现说明中列出决策）；
   c. 删除 `users` 行；
   d. 写 `app_logs`（ltype `user_hard_delete`，level `warning`）。
4. 回收站表（新 DDL 迁移）：`user_trash(oid PK, user_oid, deleted_by, deleted_at, payload CLOB JSON, restored_at NULL)`；DDL 文件放 `db/oracle/ddl/`（编号顺延现有最大版本），**不自动执行生产 DDL**，仅提供文件并在报告中说明应用方式。
   注：RESTORED_AT 已于 V052 删除（2026-11），实际列集合以 `db/oracle/ddl/V011__add_user_trash.sql` 与 `V052__drop_user_trash_restored_at.sql` 为准。
5. 垃圾回收：新增任务（照抄 `tasks/retention.py` 的后台任务范式），`deleted_at < now - 30 days` 的回收站行物理删除，保留期常量可配；接入既有调度（与 retention 同机制）。

**前端（3.3B）**
1. `components/group/ZUserModification.vue`：modify 分支真正提交身份（调 `api.user.updateRoles`）、学号变更、班级变更；**成功提示必须在请求成功后**，失败必须展示原因（禁止无条件 toast）。
2. 用户详情页新增"删除用户"入口（admin 可见，二次确认），调用新 DELETE 端点。
3. 身份修改成功后提示"该用户需重新登录"，并处理自身被改角色时的本地态刷新（`stores/user.ts:82-85` `refreshUser`）。
4. api 层：`api/user/crud.ts` 增补 `updateRoles`（已存在，接上调用方）、`updateStudentId`、`deleteUser`。
5. i18n：`i18n/locales/manage.ts` 增补文案（中英双语），复用既有 `manage.groupDetails.userList.columns.remove` 悬空 key 或新增。

**验收**：后端新增测试覆盖身份三层校验、学号校验与 409、删除事务与回收站写入、30 天 GC；前端 type-check 通过；不得改动 `activities_*`、`db_metrics_*`、`main.py`。

### 3.4 R6 + R7 体验改造

**R6（3.4A 后端 / 3.4B 前端）**
1. 后端新增 `GET /api/system/db/metrics/host`（`routers/db_metrics_router.py`）：仅返回 host 段 + 进程池信息，**零 Oracle 往返**（`services/host_metrics.py` + `services/db_monitor.py` 内新增 host-only 快照函数），响应形状与 `/realtime` 的 `host`/`pool` 字段**逐字一致**，便于前端复用。
   **已由 L4 车道实测定案（2026-10-18）**：
   - 形状：`{"status": "ok", "code": 200, "data": {"host": ..., "pool": ..., "api": ...}}` + `Cache-Control: no-store`；`data` **不含** `storage/load/sessions/warnings/observed_at`。
   - `data.host` 精确 7 键（无夹带）：`cpu{user_pct,system_pct,idle_pct,iowait_pct}`、`mem{total_mb,used_mb,available_mb}`、`load{1m,5m,15m}`、`disk{mount,used_pct,free_gb}`、`net{rx_kbps,tx_kbps}`、`diskio{read_kbps,write_kbps}`、`proc{rss_mb,uptime_hours}`。
   - `data.pool` 精确 6 键：`size, checkedout, overflow, capacity, utilization_percent, waited`（异常降级形状为 `{available:false, reason:"pool_metrics_unavailable"}`）。
   - "零 Oracle" 的界定：**数据路径**零 Oracle（端点不声明 `get_session` 依赖、不调用任何 Oracle 采集函数，测试用记数桩 + session 依赖断言双重证明）；**鉴权路径仍有 2 次 users 表主键查询**（`get_current_user` + `_require_system_admin`），与既有端点一致，为避免安全降级予以保留。
   - 副作用（影响 L5 轮询策略）：`host_metrics` 的速率计算依赖**模块级 `_previous` 共享状态** → `/realtime` 与 `/host` **不得同时轮询**，只有激活 tab 才轮询自己的端点，否则速率窗口互相扰动出现尖刺。
   - 进程启动后**首次**调用时速率类字段（`cpu.*`、`net.*`、`diskio.*`）为 `null`（既有 collector 行为，非本次引入）。
2. 前端 `views/server/ServerDatabase.vue`：改为页内 tab（复用 `ElSegmented` 既有范式，参考 `views/server/ServerHome.vue:26-56`），把现有 9 卡归入 **「数据库」/「本地服务器」** 两个 tab：本地服务器 tab 只依赖新端点（首屏即出，不等 Oracle），数据库 tab 保持现有 5 个端点调用不变。
3. host 趋势分组保留在数据库 tab 的趋势卡内（不迁移），避免破坏既有图表能力。
4. i18n：`i18n/locales/server.ts` 增补 tab 文案（中英双语），不得删除既有 key。

**R7（3.4B）**
1. `views/user/UserHome.vue:106-109` 移除「账号状态」整行。
2. `i18n/locales/home.ts` 的 `labels.status` / `status.normal` / `status.banned` **保留**（详情页与列表页仍在用）。

**验收**：`npx pnpm@10 type-check` 通过；本地服务器 tab 首屏不依赖 Oracle 端点；首页不再出现「账号状态」。

## 4. Stage 2 契约（框架级，细化前需复核）

> Stage 2 涉及 shell 权限、全盘文件读写与 DDL，风险等级最高：**冻结细化契约 → 只读复核 → 用户放行 → 分车道实现**。

- **双人复核基建**：`util/permission/dual_control.py` —— 入参 `(operator, approver_id, code, action, target)`；强制 `approver_id != operator.oid`、`approver` 必须是 admin、`verify_totp(approver_id, code)` 通过、**同一 (approver_id, 时间步/码哈希) 只能消费一次**（防重放）；消费记录落本地 SQLite（沿用 `db/system_state.py` 的路径范式，env 可覆写），审计写 `app_logs`（含 approver_id、action、target、结果）。
- **终端**：`GET/POST` 建立的 WebSocket 端点（FastAPI WebSocket，鉴权失败用 `WebSocketException`，不用 `HTTPException`）；连接建立前需双人 TOTP；PTY resize、心跳、会话注册表（session_id → handle）、空闲超时、强制断开；审计记录连接/断开/操作者/审批人/来源 IP。
- **文件管理**：根 `/`，仅保留 `/proc`、`/sys` 两个**虚拟文件系统**的保护（2026-10-19 按用户裁定放开系统目录黑名单，`/etc`、`/root`、`/usr`、`/dev` 等由 OS 权限裁决）；所有路径先 `realpath` 解析符号链接再做虚拟文件系统断言；读写/删除/上传/重命名/新建目录均需双人 TOTP + 审计；上传走临时文件 + `os.replace` 原子替换。
- **数据库内容管理**：查询（限行/超时/禁多语句）与执行（含 DDL）；**写与 DDL 一律双人 TOTP + 逐条审计**（记录 SQL 全文、影响行数、耗时、错误码）。
- **前端**：新增 `views/server/ServerTerminal.vue`（xterm.js）、`ServerFiles.vue`、`ServerDbConsole.vue`，`views/server/ServerHome.vue` 增 tab，`router/index.ts` 增子路由，`i18n/locales/server.ts` 增文案；依赖 `@xterm/xterm` + `@xterm/addon-fit` 等。
- **反代**：nginx/Caddy 必须放行 WebSocket 升级并调大 `proxy_read_timeout`（部署项，实测后再上线）。

## 5. 车道与文件白名单（Stage 1）

| 车道 | 所有权（只允许改这些文件） |
|---|---|
| L1 | `routers/activities_v2_router.py`、`util/permission/volunteer.py`、`util/permission/volunteer_member.py`、`api/activity/member.ts`、`api/activity/merge.ts`、新增 `tests/test_v4242_activity_delete.py` |
| L2 | `routers/users_router.py`、`views/user/UserLogin.vue`、`i18n/locales/maintenance.ts`、新增 `tests/test_v4242_maintenance_login.py` |
| L3A | `routers/users_v3_router.py`、`db/repos/oracle/users.py`、`util/permission/user.py`、`db/oracle/ddl/V0NN__user_trash.sql`（新增）、`tasks/`（新增回收站 GC 任务文件）、新增 `tests/test_v4242_user_admin.py` |
| L3B | `components/group/ZUserModification.vue`、`views/user/UserPage.vue`、`api/user/crud.ts`、`i18n/locales/manage.ts` |
| L4 | `routers/db_metrics_router.py`、`services/db_monitor.py`、新增 `tests/test_v4242_host_metrics.py` |
| L5 | `views/server/ServerDatabase.vue`、`api/system/db.ts`、`i18n/locales/server.ts`、`views/user/UserHome.vue` |

**通用约束**：不执行 git 提交/推送；**不运行前端生产构建**（`build-only`/`build`）——仅跑 `type-check`；后端只跑自己的测试文件（`pytest tests/test_v4242_*.py`），避免干扰并行车道；`main.py` 本阶段不允许修改；不确定的既有约定先只读核实再动手，禁止猜测。

## 6. 验证与部署要求

- 集成门：后端全量 `pytest`、前端 `type-check`、前端 `test:unit`，由主会话在全部车道合流后统一执行。
- 生产 DDL（回收站表）与后端依赖（Stage 2 的 `asyncssh`、前端 `@xterm/*`）均属**部署动作**，需在集成后由用户放行；DDL 不得由车道自行执行到生产库。
- 部署 rsync 排除清单沿用既有约定（含 `system_state.db*` / `perf_samples.db*`，Stage 2 新增的本地库同理需加排除）。
