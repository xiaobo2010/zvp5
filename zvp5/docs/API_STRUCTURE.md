# ZVMS 后端结构与约定（可直接照抄的实现指南）

> 目的：把「哪些是契约、哪些可以动、新代码往哪放」写成一份能执行的文档。
> 数值为 2026-11-19 死端点清理后实测（展开 included-router 后
> **219** 个叶子路由 = 217 条带 methods 的路由 + 1 个 WebSocket + 1 个 `/socket.io` mount）。
> 版本段裁决与撞车处置见 [`ADR_API_V1_REVERSIONING.md`](./ADR_API_V1_REVERSIONING.md)：内部版本段只剩
> `/api/ai/v1/*`（OpenAI 兼容外部契约，永续）；`/api/v2/mcp` 已随 `mcp_router` 删除。

## 1. 分层

```
main.py                 只做装配：导入、ROUTERS 注册表、按序装配、uvicorn 入口（175 行）
app_setup.py            FastAPI 实例 + CORS/盾牌/安全头/维护/ApiMetrics 中间件装配
scheduler.py            14 个 APScheduler job + 启动/关闭钩子
socket_events.py        socket.io 连接与事件
meta_routes.py          /api/ 、/api/health、/api/cert、/api/version
error_handlers.py       422 中文本地化、InvalidOid→400、兜底 500
bindings.py             运行期活绑定（既有测试 monkeypatch 的打桩点）

routers/                HTTP 层：参数校验、鉴权、调 service/repo、组装响应
routers/ai/             AI 网关按端点域拆分（common/ocr/keys/logs/ocr_engines/quota/health/chat/completion/streaming）
services/               业务编排（AI、agent、设备闸、风控、维护、监控）
db/repos/               仓储实现（oracle/）+ Protocol 定义（protocols/ 包）
db/cache.py             Redis 读穿/降级封装
tasks/                  定时批量任务（夜间报表、留存清理）
util/                   无状态工具
```

判断标准：router 里出现 SQL、跨 3 个以上 repo 的编排、或 >300 行，就该下沉。

## 2. 路由注册表（实测条数）

219 个叶子路由 = 214 条 router 路由（含 1 条 WebSocket）+ 4 条 `main` 自挂（`meta_routes`）+ 1 个 `/socket.io` mount。

| 模块 | 条数 | 挂载前缀 / 域 |
|---|---|---|
| `routers/users_router.py` | 23 | `/api/v1/users`（原 users v1/v2/v3 三份合并为单文件；死端点清理后只剩前端真实调用的端点） |
| `routers/groups_router.py` | 13 | `/api/v1/groups`（原 groups v1/v2/v3 三份合并为单文件） |
| `routers/activities_router.py` / `activity_statistics_router.py` | 12 / 3 | `/api/v1/activities`（原 v2+v3 互补端点合并为单 router）+ `/api/v1/statistics/activities` |
| `routers/times_router.py` | 1 | `/api/v1/times` |
| `routers/attachments_router.py` | 13 | `/api/attachments`（整 router 挂设备解锁依赖） |
| `routers/ban_router.py` / `ban_violations_router.py` | 11 / 2 | 同前缀 `/api/v1/ban` |
| `routers/notifications_router.py` | 7 | `/api/v1/notifications` |
| `routers/changelog_router.py` | 4 | `/api/changelog` |
| `routers/exports_router.py` / `imports_router.py` | 7 / 1 | `/api/exports` / `/api/imports` |
| `routers/logs_router.py` | 1 | `/api/logs` |
| `routers/ai/*`（8 个子模块）+ `ai_models` / `ai_api_keys` / `ai_agent` / `ai_conversations` / `ai_scenes` / `ai_schedule` / `ai_quota_requests` | 60 | AI 网关（`/api/ai/*`；其中 `/api/ai/v1/*` 为 OpenAI 兼容外部契约） |
| `routers/system_*`（maintenance/settings/db/db.metrics/db.content/ssh/files/role-policies） | 51 | 运维面 |
| `main` 自挂（`meta_routes` 4） | 4 | 探针（`/api/`、`/api/health`、`/api/cert`、`/api/version`） |

### 2.1 `/api/v1` 撞车裁决（route 级）

`main.py` 的 `ROUTERS` 顺序即契约，同 method 同 path 时先注册者胜。
归并期撞车已按 ADR 裁决；败方函数体已随合并删除（activities v2、groups `get_group_users_v2`、
users/groups 的未版本化重复实现），因此**不能**按「v3 后注册所以覆盖 v2」理解——合并后每个
(method, path) 只有一份实现：

| 路径 | 现役实现 |
|---|---|
| `POST /api/v1/activities` | v2（`create_activity_v2`，保留 `date/place/approver/origin` 完整契约；v3 create 路由已摘除） |
| `GET /api/v1/activities` | v3（`get_activities_v3`，可选 `sort`/`asc` 白名单 + place/origin/appointee/approver 投影） |
| `GET /api/v1/activities/{activity_id}/members` | v3（`get_activity_members_v3`） |
| `POST /api/v1/activities/{activity_id}/members` | v3（`add_activity_member_v3`，批量契约） |
| `GET /api/v1/groups/{group_id}/users` | v3（`get_group_users_v3`，`pwdm` = 是否设置过密码） |
| `GET /api/v1/users` / `GET /api/v1/groups` | v2 列表（`get_users_v2` / `get_groups_v2`，`{"data","total"}`） |
| `GET /api/v1/users/{id}` | v3（`get_user_v3`，`user_profile` 投影） |
| `GET /api/v1/users/{id}/activities` / `/time` / `/time_statistics` | v2 孪生体（前端按 `activities`/三档原始字段读取） |
| `GET /api/v1/groups/{id}` | v2（`get_group_v2`；学生仅限本班） |
| `GET /api/v1/groups/{id}/activities` / `/time` | v2（分页/搜索/排序全部下推） |
| `DELETE /api/v1/groups/{id}` / `PUT /{id}/name` / `POST /api/v1/groups` | v3（临时凭据闸 / 204 / 201） |

## 3. 响应契约：三套并存，**不要统一**

| 形态 | 用在哪 | 例子 |
|---|---|---|
| envelope `{"status":"ok","code":200,"data":...}` | 无版本段读面（`/api/exports`、`/api/logs` 等）、system、ai、db，以及 `/api/v1/*` 多数端点 | `GET /api/`、`GET /api/v1/groups/{id}` |
| 裸对象/裸列表（含 `{"data","total"}`） | `/api/v1` 新面（原 v2/v3 实现） | `GET /api/v1/users/{oid}` |
| `JSONResponse` + 自定义状态码 | 201 创建、`{"detail":"... updated"}` | `POST /api/v1/activities`（v2 契约） |

前端硬依赖点（改动前必须读）：
`src/api/group/crud.ts`（判 `status === 'ok'`）、`src/api/user/auth.ts:60`、
`src/plugins/axios.ts`（`detail` / `maintenance` / `USER_BANNED` / detail 对象 code 四个分支）。

**新端点**一律 envelope；老端点不改形状，也不加 `response_model`（OpenAPI 已关闭）。

## 4. 错误体四种特例（形状即契约）

1. `{"detail": "USER_BANNED"}` + `X-Ban-Reason` / `X-Ban-Until`（`util/ban.py`）
2. `{"detail": {"code": "TEMP_PASSWORD_INVALID" | "DEVICE_BAN_APPLIED", ...}}`（`services/device_guard.py`）
3. 维护期 `{"detail": ..., "maintenance": true}` + 503（`app_setup.py`、`users_router`）
4. 盾牌 418 的**裸字符串** body（非 Mozilla UA）

`/api/ai/v1/*` 是 OpenAI 兼容面：原始形状，不包 envelope，错误体也不同。

## 5. 中间件顺序（顺序即契约）

由外到内：`shield(418) → security_headers → maintenance(503) → ApiMetrics → CORS → MaintenanceSocketGuard`

- 418 必须先于一切，否则维护期会先返回 503；
- ApiMetrics 必须在内侧，才不会统计被 503 的请求；
- 维护 503 必须带 CORS 头，否则前端读不到 detail。
- 已由 `tests/test_main_decomposition.py` 钉住，改动会红。

## 6. 注册顺序陷阱

FastAPI 按注册顺序匹配。以下顺序**不能动**：

- `ai_quota_requests_router`（`/api/ai/quota/...`）必须先于 `ai_router`，否则 6 个端点被 `GET /quota/{user_oid}` 吃掉变 400；
- `/api/ai/models/discover` 与被 `{model:path}` 的动态段共存；
- `system/role-policies/{role}` PUT 在 `.../me` GET 之前；
- 首页 → health/cert/version 的注册顺序（`meta_routes` 里拆成三个 register 函数就是为了保序）；单数 → 复数重定向层已在 v1 收口时整体退役；
- 合并后的 users/groups 单文件里，静态段（`/auth`、`/me/*`、`/{id}/template`）必须先于参数段 `/{user_id}`、`/{group_id}` 注册。

`tests/test_route_snapshot.py` 冻结了 217 条 `(path, methods)`，任何重排都会失败并打印差异项。

## 7. Redis 缓存策略

客户端：`db/cache.py`，`REDIS_URI` 为空即整体禁用（可选能力），读失败自动降级，写回填可 fire-and-forget。

| 数据 | key 域 | TTL | 失效 |
|---|---|---|---|
| 用户 `get_by_oid` / `get_for_auth` / `get_names` / `list_by_class` | `zvms:user:*` | auth 180s / user 600s / names 1h / 班级成员 30min（±10% 抖动） | `user` epoch 自增 + 精确 forget |
| 群组 `get` / `list_classes` / `list_permission_groups` | `zvms:group:*` | 1h / 列表 10min | `group` epoch |
| 活动 `get` / `list_effective`（无日期区间时） | `zvms:activity:*` | 10min / 2h | `activity` epoch |
| AI 会话 `get` / `latest_by_scene` | `zvms:conv:*` | 2min | 写入即失效 |
| 变更日志 `list_all` | `zvms:changelog:*` | 30min | create/update/delete bump |
| 通知 `list_active`（列表与版本同快照） | `zvms:notify:*` | 2min | 写操作 bump |

TTL 只兜「失效没送达」。放长的前提是写路径一定失效，因此两类**旁路写**必须显式补偿版本号：

- 自带 `session` 的事务写会整体绕过缓存代理（`use_cache=session is None`）——目前只有 Excel 导入（`routers/imports_router.py`）与活动成员登记，导入在提交后 `bump_epoch("activities")`；
- DB 控制台（`/api/system/db/content`）能改任意行，四个写端点提交后统一 `_invalidate_read_caches()`（users/groups/activities/roles/settings 各 bump 一次，O(1) 不发 SCAN）。

新增旁路写时如果忘了补，缓存会旧到 TTL 上限；`tests/test_cache_policy.py` 钉了 TTL 区间与补偿函数。

**不要加的缓存**：`USER_TIME`（DB 已是物化结果，再加缓存双写不一致）、`AI_MESSAGES.list_after`（追加写、顺序敏感）、`ai_runs.claim_next`（CAS）。

失效用 epoch 自增，不要用 `flush_prefix` 的 SCAN 全库遍历。

## 8. 性能守则

1. **禁止逐条循环查库**。ADB 跨洋 RTT ≈138ms，单 worker 下每次多余查询都是整站延迟。
   批量场景用现成能力：`calculate_user_time_bulk`、`upsert_many_for_users`、`members.list_by_members`、
   `users.get_many_by_oids`、`activities.list_effective`。
2. 夜间报表/导出已改「一次取数 + 内存组装」：批量 12 次查询（与人数无关），旧路径 45~61 次；纯 Python 组装走 `asyncio.to_thread`。
3. `IN (...)` 必须按 `_OID_CHUNK` 分块，超过 1000 会 ORA-01795。
4. `connect_args` 有 `call_timeout`（`ZVMS_DB_CALL_TIMEOUT`，默认 60s）。`pool_timeout`（`ZVMS_DB_POOL_TIMEOUT`，默认 5s）的 checkout 是 greenlet 里的**阻塞** `queue.get`，池满会冻结事件循环——16 条连接被占满时全站卡顿，别再放大它。
5. 缓存 key 一律带 domain epoch，TTL 带 ±10% 抖动。
6. 空闲轮询要退避（agent run queue 已 1s→5s→30s，有 kick 立即回 1s）。

## 9. 新增端点 checklist

- [ ] 路径归属：`/api/v1/...`（单版本制的唯一内部版本段；users、groups、activities、ban、notifications、times、statistics 都在这里）或既有无版本域（`/api/exports`、`/api/imports`、`/api/logs`、`/api/changelog`、`/api/attachments`、`/api/system*`、`/api/ai` 管理面）；**不要再引入 `/api/v2` / `/api/v3` 内部版本段**，裁决依据见 [`ADR_API_V1_REVERSIONING.md`](./ADR_API_V1_REVERSIONING.md)
- [ ] 鉴权走 `util/object_id.py` 的 `get_current_user` / `optional_current_user` + `util/permission/features.ensure_feature`
- [ ] 响应 envelope，错误用 `HTTPException`（`detail` 用中文整句）
- [ ] 读热点先想「能不能用 repo 现有缓存」，别在 router 里自己起缓存
- [ ] 写操作后 `bump_epoch(<domain>)`
- [ ] 补测试：`tests/test_route_snapshot.py` 会自动因新路由失败 → 同步更新冻结表
- [ ] 跑 `ruff check .`（只有 F401/F821）+ `pytest -q`

## 10. 验证命令

```bash
cd zvp5/zvp5-backend-python
.venv/bin/python -c "import main; from tests.route_utils import iter_leaf_routes; print(len(list(iter_leaf_routes(main.app.routes))))"   # 219
.venv/bin/python -m pytest -q                                     # 4725 passed
.venv/bin/python -m ruff check .                                  # 未安装则跳过
```

## 11. 已知的历史包袱（有意保留）

- 503 契约桩：**已归零**（2026-11-19 死端点清理）。`PUT /{user_id}`（改资料）、`POST /{user_id}/group`（加入分组）、`DELETE /{user_id}/group/{group_id}`（移出分组）三条桩路由已删除，前端 `api/user/crud.ts:putUser` 这条零真实调用的封装随之失去后端目标；分组增删成员的唯一路径是 `POST|DELETE /api/v1/groups/{group_id}/users/{user_id}`。因此本文件不再有「前端仍展示 503 文案」的端点。
- 前端全部走 v1：`/api/v1/users/auth`、`/auth/logout`、`/auth/device`、`/api/v1/users/{id}/past`、`/api/v1/users/me/*`、`/api/v1/groups`、`/api/v1/groups/{id}/name|description`。未版本化 users/groups 路由与单数→复数重定向层已全部删除。
- 死端点已清理（2026-11-19，全部核实前端零调用）：`GET /api/v1/groups/{group_id}/user`（单数；前端只用复数 `/users`）、`GET|PUT /api/v1/users/{user_id}/bindings` 与 `DELETE .../bindings/{xuehai_id}`、`GET /api/v1/users/me/device-bindings`、`POST /api/v1/users/{user_id}/ban-records/reset`、`GET /api/v1/users/{user_id}/logs`、`GET /api/v1/statistics/activities/{activity_id}/description|layers`（前端只用 `/analysis`）。`util/read_throttle.check_anonymous` 与绑定仓储的 `list_for_user`/`revoke` 只服务于这些端点，一并删除。
- `/api/ai/keys`（旧）与 `/api/ai/api-keys`（新）双实现并存，前端仍用旧面；迁移完成前都不要删。
- 运维面 `/api/system/*` 在维护期全放行（自救路径），不能收紧。
- 维护状态存独立 SQLite（`ZVMS_SYSTEM_DB`），不在 ADB 备份覆盖范围内，跨机部署要单独持久化。
