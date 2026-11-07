# ZVMS 后端结构与约定（可直接照抄的实现指南）

> 目的：把「哪些是契约、哪些可以动、新代码往哪放」写成一份能执行的文档。
> 数值均为 2026-11-06 夜间重构后实测（`app.routes` 共 **257** 条）。

## 1. 分层

```
main.py                 只做装配：导入、ROUTERS 注册表、按序装配、uvicorn 入口（180 行）
app_setup.py            FastAPI 实例 + CORS/盾牌/安全头/维护/ApiMetrics 中间件装配
scheduler.py            14 个 APScheduler job + 启动/关闭钩子
socket_events.py        socket.io 连接与事件
meta_routes.py          /api/ 、/api/health、/api/cert、/api/version
error_handlers.py       422 中文本地化、InvalidOid→400、兜底 500
legacy_redirects.py     6 条单数→复数 301/307
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

| 模块 | 条数 | 域 |
|---|---|---|
| `routers/users_router.py` | 17 | 用户 v1（含 4 条 503 契约桩） |
| `routers/users_v3_router.py` | 18 | 用户 v3（现役主面） |
| `routers/groups_router.py` / `groups_v2` / `groups_v3` | 10 / 6 / 6 | 群组/班级 |
| `routers/activities_v2` / `activities_v3` / `activity_statistics` | 13 / 4 / 5 | 活动 |
| `routers/attachments_router.py` | 13 | 附件（整 router 挂设备解锁依赖） |
| `routers/ban_router.py` / `ban_violations_router.py` | 11 / 2 | 封禁（同前缀 `/api/v3/ban`） |
| `routers/notifications_router.py` | 7 | 通知 |
| `routers/changelog_router.py` | 4 | 变更日志 |
| `routers/exports_router.py` / `imports_router.py` | 7 / 1 | 导入导出 |
| `routers/times_router.py` / `users_v2_router.py` | 1 / 4 | 时间记录 |
| `routers/mcp_router.py` | 5 | MCP |
| `routers/ai/*`（8 个子模块）+ `ai_models` / `ai_api_keys` / `ai_agent` / `ai_conversations` / `ai_scenes` / `ai_schedule` / `ai_quota_requests` | 60 | AI 网关 |
| `routers/system_*`（maintenance/settings/db/db.metrics/db.content/ssh/files/role-policies） | 51 | 运维面 |
| `main` 自挂（`meta_routes` 4 + `legacy_redirects` 6） | 10 | 探针与兼容 |

## 3. 响应契约：三套并存，**不要统一**

| 形态 | 用在哪 | 例子 |
|---|---|---|
| envelope `{"status":"ok","code":200,"data":...}` | v1 读面、system、ai、db、users v3 多数端点 | `GET /api/`、`GET /api/groups` |
| 裸对象/裸列表（含 `{"data","total"}`） | v2/v3 新面 | `GET /api/v3/users/{oid}` |
| `JSONResponse` + 自定义状态码 | 201 创建、`{"detail":"... updated"}` | `POST /api/v2/activities` |

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
- 首页 → 6 条 redirect → health/cert → version 的注册顺序（`meta_routes` 里拆成三个 register 函数就是为了保序）。

`tests/test_route_snapshot.py` 冻结了 257 条 `(path, methods)`，任何重排都会失败并打印差异项。

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

- [ ] 路径归属：`/api/v3/...`（现役）或既有域；不要新增 v1 路径
- [ ] 鉴权走 `util/object_id.py` 的 `get_current_user` / `optional_current_user` + `util/permission/features.ensure_feature`
- [ ] 响应 envelope，错误用 `HTTPException`（`detail` 用中文整句）
- [ ] 读热点先想「能不能用 repo 现有缓存」，别在 router 里自己起缓存
- [ ] 写操作后 `bump_epoch(<domain>)`
- [ ] 补测试：`tests/test_route_snapshot.py` 会自动因新路由失败 → 同步更新冻结表
- [ ] 跑 `ruff check .`（只有 F401/F821）+ `pytest -q`

## 10. 验证命令

```bash
cd zvms4-modified-main/zvms4-backend-python
.venv/bin/python -c "import main; print(len(main.app.routes))"   # 257
.venv/bin/python -m pytest -q                                     # 4378 passed
.venv/bin/python -m ruff check .                                  # 未安装则跳过
```

## 11. 已知的历史包袱（有意保留）

- v1 写端点返回 **503 + 文案**，是 zvp 兼容期的契约桩，不是死代码（前端仍展示该文案）。
- 前端仍在调 v1 读面：`/api/users/auth`、`/auth/logout`、`/auth/device`、`/api/users/{id}/past`、`/api/groups`、`/groups/{id}/name|description`。
- `/api/ai/keys`（旧）与 `/api/ai/api-keys`（新）双实现并存，前端仍用旧面；迁移完成前都不要删。
- 运维面 `/api/system/*` 在维护期全放行（自救路径），不能收紧。
- 维护状态存独立 SQLite（`ZVMS_SYSTEM_DB`），不在 ADB 备份覆盖范围内，跨机部署要单独持久化。
