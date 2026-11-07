# 2026-11-06 夜间重构记录（API 结构 / 性能 / 可维护性）

分支：`main`。基线：`6580be2` → 本次收尾见 `git log --oneline`。
全量回归：**后端 4378+ passed / 0 failed**（基线 4353），**前端 91 文件 / 1023 用例 passed**，`vue-tsc` 0 error。
路由表冻结在 **257** 条（`tests/test_route_snapshot.py`），整夜所有拆分均未改变任何 HTTP 契约。

## 一、改了什么

### 1. 对外链接
`GET /api/` 的 `source` 与前端 `package.json` 的 `repository` 指向 `github.com/xiaobo2010/zvp5`；团队署名（`author: ZZDev`）、`@zvms` 包名、about 页文案保持不变。

### 2. 结构（纯搬家，行为零变化）
| 原文件 | 拆成 | 行数变化 |
|---|---|---|
| `main.py` 886 | `main.py` 180 + `app_setup.py` 288 + `scheduler.py` 260 + `meta_routes.py` 147 + `error_handlers.py` 92 + `legacy_redirects.py` 45 + `socket_events.py` 57 + `bindings.py` 55 | 单文件降 80% |
| `routers/ai_router.py` 2844 | 聚合器 218 + `routers/ai/` 10 模块（common/ocr/keys/logs/ocr_engines/quota/health/chat/completion/streaming） | 最大 578 |
| `db/repos/protocols.py` 3562 | `db/repos/protocols/` 15 个领域模块，`__init__` re-export | 最大 549 |

细节：
- `main.py` 的中间件注册顺序、「顺序即契约」注释整段搬入 `app_setup.create_app()`；既有 8 个 `monkeypatch.setattr(main, ...)` 打桩点用 `bindings.py` 活绑定保住。
- `ai_router` 聚合器按原装饰器顺序 include，`/quota/{user_oid}` 遮蔽关系不变；旧符号仍 re-export，4 个子 router 的反向 import 改指 `routers.ai.common`。
- `protocols` 拆包后调用方一行未改；43 个顶层定义的 AST 逐字比对无差异。

### 3. 死代码 / 技术债
- 删除孤儿模块 8 个（`typings/{v2,group_v2,notification,rating,trophy}.py`、`util/cases.py`、`util/time_export.py`、`util/response.py`）、死函数 7 个、`conversion/` 残渣、`settings.py.bak.*`。
- 前端删 11 个不可达文件 + 5 处 `console.log` + 恒空字段 `primary_xuehai_id`。
- 新增 `pyproject.toml`（ruff 仅 `F401`/`F821`）+ 路由冻结测试 + `test_main_decomposition.py` + `test_ai_id_shapes.py`。
- 修真实 bug 3 个：
  - `GET /api/users/{oid}/time` 恒 500（读不存在的 `total`）；
  - `activities_v2` 状态变更仍读 legacy `perm` 键导致 admin 闸门恒假；
  - `ai_conversations` / `ai_agent` 的 `{32}` 正则曾被我误判为 bug，实测是正确的 `uuid4().hex` handle 口径，现由测试钉住「24 位=业务 oid / 32 位=会话 handle」边界。

### 4. 性能
| 项 | 改前 | 改后 |
|---|---|---|
| 夜间报表 `generate_reports` | 逐用户 N+1：45~61 次查询/人，2000 人 ≈ 数小时 | 整批预加载：**12 次**（与人数无关），输出逐字节一致 |
| 导出 time/users 变体 | 逐用户循环 + 逐人 `groups.get` | 一次 `list_all` + map + `calculate_user_time_bulk` |
| `/api/`、`/api/changelog` | 每次一次跨洋 ADB 查询（138ms） | Redis 读穿，300s，写后 bump epoch |
| `/api/v3/notifications` | 每请求 2 条 SQL | 列表+版本同快照缓存 2min |
| 提案列表 `list_mine` | 200 行 → 200 次 UPDATE + 200 次 Redis GET | 1 次批量 UPDATE + 1 次授权查询 |
| agent run queue 空转 | 每 1s 一次 SELECT+CAS | 1s→5s→30s 退避，kick 立即回 1s |
| DB 语句超时 | 无（慢查询可永久占用连接，池满冻结事件循环） | `call_timeout` 默认 60s（`ZVMS_DB_CALL_TIMEOUT`） |
| `IN (...)` >1000 | ORA-01795 | 按 `_OID_CHUNK` 分块 |
| 前端入口 CSS | 396KB（含全量 Vant） | 199KB（−50%） |
| 登记页首包 | 439KB | 73KB（xlsx 动态化，−83%） |
| 登录态 GET 缓存 | 实际从不命中（缓存键无身份、写缓存被 Authorization 拦截） | 按身份隔离命中，写操作清缓存 |
| 竞速白名单 | 缺口 13 项（含最热的 AI messages 轮询） | 已补齐 16 条正则，并补「不该竞速」反例 |

## 二、验收

```bash
cd zvms4-modified-main/zvms4-backend-python
.venv/bin/python -c "import main; print(len(main.app.routes))"      # 257
.venv/bin/python -m pytest -q                                      # 0 failed
cd ../zvms4-frontend
npx vue-tsc --noEmit -p tsconfig.vitest.json --composite false      # 0 error
npx vitest run                                                     # 1023 passed
VITE_API_BASE=https://zvp.xiaobopisces.top/api \
VITE_API_RACE_BASE=https://zvpapi.xiaobopisces.top/api npm run build
```

前端产物自检：`index-*.js` 同时含两个 API 基址与竞速模块（`Both API paths failed`）。

## 三、还没做（按性价比排序，留给后续）

1. **应用层 gzip / orjson**：目前依赖 nginx 压缩（LA 已开，43 需按 `deployment/nginx/zvms-api-gzip.conf` 安装）。应用层 `GZipMiddleware` 对 SSE 需排除；orjson 要处理 `Decimal`，且需联网安装依赖。
2. **`/api/system/*` 与运维面的 `SCAN` 失效**：`db/cache.py` 的 `flush_prefix` 仍会遍历 keyspace，建议改成 key 索引 SET + `UNLINK`。
3. **前端 store 化剩余部分**：users / device / notifications 三个 store，替换组件内的重复拉取（groups 已完成）。
4. **超大 SFC**：`VolunteerRegister.vue`(4693)、`ZAiChat.vue`(2888)、`ServerDatabase.vue`(2030) 需单独立项拆。
5. **依赖清理**：`socket.io-client`、`js-md5`、`md5`、`js-yaml`、`jsencrypt`、`bson-objectid`、`universal-cookie`、`vconsole`、`@ckeditor/*`、`@amap/*`、`mockjs`、`oxlint` 等前端 0 引用包（需联网 `pnpm install` 更新 lockfile）；`tqdm` 在后端已无使用者。
6. **`tasks/data_fix.py`**：文档标注「Mongo 遗留、待接线」，删或接线需产品决策。
7. **生产部署**：本轮只做了代码与文档，未动线上。后端是 rsync 部署（`/www/zvms4/backend`，无 git），发布前需备份 `backend.bak-<ts>` 并 `systemctl restart zvms4`；nginx 预检/gzip 片段见 `docs/DEPLOY_RACE_GZIP_PREFLIGHT.md`。
