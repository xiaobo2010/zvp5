# ADR：API 全量归并到 /api/v1（v2/v3 退役）

- 状态：已接受（2026-11-07），随本笔 reversioning 提交一并生效
- 推翻：`docs/API_STRUCTURE.md` 旧裁定「路径归属……不要新增 v1 路径」；`docs/REFERENCE_TREES_ALIGNMENT.md:25-32`「拒绝 /api/v1 平行端点」（后者为历史裁定记录，冻结不改，以本 ADR 为准）

## 背景与动机

仓库长期 v2/v3 双版本并存：归并前 256 条路由中 82 条带内部版本段（`/api/v2/*` 34 条 + `/api/v3/*` 48 条），另有 `/api/ai/v1/*` 2 条为 OpenAI 兼容外部契约，其余 172 条无版本段。本次按产品指令实行单版本制：所有内部版本段归并为 `/api/v1`。

## 决策

### 1. 口径（方案 A）

仅把 `/api/v2/*` 与 `/api/v3/*` 的前缀归并为 `/api/v1/*`。无版本域（`/api/users`、`/api/groups`、`/api/exports`、`/api/imports`、`/api/logs`、`/api/changelog`、`/api/attachments`、`/api/system*`、`/api/ai` 管理面）保持原位不动；`/api/ai/v1/*`（models、chat/completions）是 OpenAI 兼容外部契约，被外部 SDK 消费，一字不动。

否决的替代口径 B（把全部路径抬进 `/api/v1/*`）：会连带改动五张写死表——维护豁免白名单（`app_setup.py:104-113`）、metrics 排除表（`services/api_metrics.py:48-49`）、盾牌 UA 前缀（`app_setup.py:100`）、nginx location、WS/SSE 路径——其中任何一张漏改都会造成「维护期锁死登录自救」「探针污染指标」「流式 404 而普通请求正常」这类极难排查的事故。风险不对称，不采纳。

### 2. MCP 外部兼容别名

`/api/v1/mcp` 为主挂载，`/api/v2/mcp` 保留别名（带移除 TODO）。理由：`routers/mcp_router.py` 声明服务 AI agent 与外部工具，外部消费方无法穷举，别名保一个发布窗口的缓冲。

> 追注（2026-11-19）：已删除 `mcp_router` 与 `/api/v2` 别名；唯一没有 agent 对应物的只读能力 `types_summary` 并入 agent 工具（`activity_types_summary`），其余查询由既有 agent 工具覆盖。上游原文保留不改。

### 3. 撞车 5 组裁决

归并后同 method 同 path 撞车全集 = 5 组（FastAPI 先注册先匹配；运行期 `route.matches()` 影子扫描 + 静态枚举双证，无遗漏、无 PUT/DELETE 撞车）。注意：`main.py` 原注册顺序 v2 在前，只改前缀不摘装饰器则 v2 全胜——因此裁决必须以「摘除败方装饰器」落地。

裁决规则：**保「实际现役且功能完备」的一方，证据优先于「v3 为现役面」的教条。**

| 组 | 路径 | 胜者 | 败方处置 |
|---|---|---|---|
| A | GET /api/v1/activities | v3（补强后） | 摘 v2 装饰器；前端 `getAllActivities` 迁 v3 契约 |
| B | POST /api/v1/activities | **v2**（现役创建面） | **摘 v3 装饰器**；前端仅改前缀 |
| C | GET /api/v1/activities/{id}/members | v3 | 摘 v2 装饰器；前端死代码 `member.reads` 删除 |
| D | POST /api/v1/activities/{id}/members | v3（批量+状态闸门更强） | 摘 v2 装饰器；前端死代码 `member.insert` 删除 |
| E | GET /api/v1/groups/{id}/users | v3 | 摘 v2 装饰器；前端死代码 `group/reads.users` 删除 |

逐组依据：

- **A 组**：现役 UI 能表头排序（`ZActivityList.vue` sortable 列 → v2 `sort/asc` 参数）。为不损失该能力，v3 获得可选 `sort`/`asc` 参数（独立白名单 name/type/status/date/_id，`_id`→oid，白名单外 400；不传维持原 oid DESC）；投影补齐 place/origin/appointee/approver 四字段（repo `_activity_doc` 本就返回，v3 handler 此前是裁剪）。前端 `getAllActivities` 迁 v3（参数映射 perpage→size、search→query、activity_type→type 单值），对外签名与返回形状不变（v3 响应无 page/perpage 回显，以入参回填）。
- **B 组（对教条的反转）**：现役创建链 = `ZActivityCreate.vue` 表单（传 date/place/approver/origin）→ `api/activity/create.ts` → `POST /v2/activities`；v3 create 全仓零调用方，且请求体仅 {name,description,type}，丢失 date/place/approver/origin 与 name 校验——归并到 v3 等于功能回归。故保 v2 为 `/api/v1/activities` 的 POST 实现。本条显式推翻根 `README.md:290` 对「POST /api/v3/activities」的现役标注。
- **C/D/E 组**：三组的 v2 前端函数全仓零调用方（`member.ts` reads、`member.ts` insert 的 `status==='ok'` 成功分支在 {id,count} 响应下永不成立、`group/reads.ts` users——`ZGroupUserList.vue` 用的是 users_v3），连同函数删除；v3 侧已被现役页面消费且能力更强。

实现纪律：5 个败方仅摘 `@router` 装饰器行，**函数体保留**——`services/agent/tool_impl.py` 以 v2 成员 handler 为「同形」参考实现，保留函数体使 imports 全部有效、ruff F401 零新增；整体删除留待后续卫生项。

### 4. pwdm 语义登记

groups users 端点的 v2 语义为「自定义密码（≠学号）」、v3 语义为「是否设置过密码（password_hash IS NOT NULL）」。UI 已消费 v3 语义；v2 退役后「自定义密码」判定不再有 HTTP 面（如需恢复属新功能，非本次范畴）。

### 5. 归并后版本段残留

全后端仅存两处版本段：`/api/ai/v1`（OpenAI 兼容外部契约，永续）与 `/api/v2/mcp`（兼容别名，带移除 TODO）。

## 升级提示与字样同步

`users_router.py` 三处「请使用 POST /api/v3/...」类升级提示、`util/activity_signoff.py` 的 `/api/v2` 引用、agent 工具描述（`services/agent/tool_impl.py`、`tools.py`）中的 `/api/v2` 字样，全部改指 `/api/v1`。

## 测试策略

- 全量前缀机械更新（tests/ 内约 234 处 `/api/v[23]` 引用；`/api/ai/v1` 不动）。
- 语义改写：`test_group_pwdm_flags`（改 v3 的 password 存在性语义）、`test_group_privacy`（改 v3 路径与 response.data 形状；v2 特有危险 regex 用例随 v2 regex 参数退役删除）。
- v3 create 用例随路由退役删除；v3 列表用例随投影补齐与 sort/asc 白名单扩充（含非法值 400、缺省不传=旧行为）。
- `tests/test_route_snapshot.py` 按新路由表全量重冻结。

## 锁步发版（生产部署另行窗口执行，不在本笔范围）

时序：生产后端（带 /api/v1）上线 → 生产前端 dist 更新 → 预览站重发。窗口内持有 PWA 旧缓存的用户可能短暂出现旧前端调 `/v2|/v3` 404，刷新或 Service Worker 更新后自愈；后端先上可保证旧前端全量兼容（v2/v3 前缀在前端更新前仍有效——如保留别名则无感，本方案为干净切换，故按上述时序执行）。

## 关联文档

- `docs/API_STRUCTURE.md`：路由归属表随本变更重写，其旧裁定由本 ADR 取代。
- 根 `README.md:290/:849/:868`：v3 现役标注随本变更修正。

## 追注（2026-11-19）：users / groups 全量收口 `/api/v1`

本 ADR 原文保留不改（含上文「无版本域保持原位」的口径）；本追注只记录此后发生的
实际变化，二者冲突时以本追注为准。

- **前缀**：`users` 与 `groups` 不再有无版本挂载点。全部端点统一挂 `/api/v1/users`
  与 `/api/v1/groups`，前端 `src/api/user/*`、`src/api/group/*` 已在上一条车道把
  所有 axios 路径迁到 v1（子路径不变）。
- **单文件合并**：`users_router.py` ← 原 `users_router` + `users_v2_router` +
  `users_v3_router`；`groups_router.py` ← 原三份。`users_v2/v3_router.py`、
  `groups_v2/v3_router.py` 四个旧文件删除。合并按「前端实际调用」逐端点裁决，
  每个 `(method, path)` 只留一份实现；未版本化侧与 v1 侧重复的读/写端点一律删
  未版本化实现。由于前端此前已经全部打到 v1，未版本化实现实际上已是不可达代码。
- **legacy_redirects 退役**：单数 → 复数重定向整模块删除（`/api/user`、`/api/group`
  的目标前缀已不存在；`/api/export`、`/api/import`、`/api/log` 在前后端仓库内零调用，
  仍可用的目标是 `/api/exports`、`/api/imports`、`/api/logs`）。`main.py` 注册点、
  `tests/test_main_decomposition.py` 的 legacy 锁、`tests/test_route_snapshot.py` 的
  legacy 条目的同步删除/重冻结。
- **规模**：叶子路由 249 → 231（users 38 → 32、groups 21 → 14、legacy redirect −5）。
- **503 契约桩**：原 `/api/users` 下的桩随合并迁入 `/api/v1/users`（`PUT /{user_id}`、
  `POST /{user_id}/group`、`DELETE /{user_id}/group/{group_id}`），文案保持不变；
  `DELETE /{user_id}` 的旧桩让位给 v3 的真实硬删除实现。
- **保留的已知候选**：`GET /api/v1/groups/{group_id}/user`（单数）与
  `PUT /api/v1/users/{user_id}`（桩）在前端均无真实调用方，本次迁入不删，待裁决。

## 追注（2026-11-19）：死端点清理（用户裁决）

ADR 与上一条追注原文保留不改；本条只记录裁决结果。全部删除项均已在前端
`zvp5-frontend/src` 复核为零调用（`grep` 覆盖 `src/api/**` 与模板；`hedgedGet.ts`
的 `/v1/users/me/device-bindings` 只是 HTTP 对冲白名单里的历史正则，不是调用方）。

- **503 契约桩归零**：`PUT /api/v1/users/{user_id}`、`POST /{user_id}/group`、
  `DELETE /{user_id}/group/{group_id}` 删除；桩专属的 `PutUser` 模型一并移除。
- **groups 单数死端点**：`GET /api/v1/groups/{group_id}/user` 删除（前端只用
  `/users`）；`custom_password_flags_async` / `public_user` 在该 router 的引用随删。
- **users_v3 零调用端点**：`GET|PUT /users/{user_id}/bindings`、
  `DELETE /users/{user_id}/bindings/{xuehai_id}`、`GET /users/me/device-bindings`、
  `POST /users/{user_id}/ban-records/reset`、`GET /users/{user_id}/logs` 删除；
  `BindingsRepo` 的 `list_for_user` / `revoke` 仅服务于这些端点，协议与 Oracle
  实现同步移除（`upsert` / `get_active_for_user` 保留给登录链路）。
  `scripts/purge_legacy_device_bindings.py` 直接操作表，不依赖被删方法。
- **statistics 旧回退端点**：`GET /statistics/activities/{id}/description` 与
  `/layers` 删除；前端只用 `/analysis`（响应本就是两者的超集）。
- **连带死代码**：`util/read_throttle.check_anonymous`（匿名档无调用方）删除；
  模块本体仍被 exports/ai/logs/system 的读预算使用，保留。
- **规模**：叶子路由 231 → 219（带 methods 229 → 217）；回滚方式是还原本次
  后端改动 + 重跑 `tests/test_route_snapshot.py` 重冻结。
