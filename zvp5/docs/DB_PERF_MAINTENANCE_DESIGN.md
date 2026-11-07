# 数据库性能采集 + 清理维护编排 + 义工详情内联名单（设计契约 v1）

本文件是本次三个需求的**唯一契约真相源**。所有实现车道必须逐字遵守；任何偏离需先在主会话裁定。

---

## 0. 需求（用户原话）

1. 「接下来为数据库管理加上性能采集」
2. 「数据库清理操作将使服务器自动进入维护状态，维护结束后将自动恢复」
3. 「义工详情页的查看义工具体发放情况请从点击(X人)按钮改为直接在详情页面下方列表展示，更加直观」

## 1. 用户已裁定（5 项）

| 议题 | 裁定 |
| --- | --- |
| 性能采集形态 | **实时面板 + 时序历史曲线**（后端定时采样落库，页面出趋势图，可回看 24h/7d；现有实时区保留） |
| 采集指标范围 | **全选 7 类**：连接池、表空间使用率与增长趋势、Oracle 负载、Top 慢 SQL、会话与等待事件、主机资源、应用侧 API 耗时 |
| 维护状态粒度 | **全站维护 + 管理员绕过**（非管理员请求统一 503，前端跳维护页显示进度；管理员可照常操作） |
| 清理执行方式 | **后台任务 + 进度可见**（前端轮询进度；成功或失败都自动解除维护） |
| 义工详情发放明细 | **去掉 `(X人)` 按钮，只留内联列表**（不保留弹窗） |

## 2. 已核实事实（线上实测 / 源码 file:line，禁止再调查，可直接采信）

### 2.1 Oracle ADB 动态视图可读性（zgovps-la 实测，19c Enterprise on ADB）

| 视图 | 结果 |
| --- | --- |
| `v$session` / `v$session_wait` / `v$process` | ✅ 可读（`v$session` 6 行，1 ACTIVE / 5 INACTIVE） |
| `v$sqlarea` / `v$sql` / `v$sqlstats` | ✅ 可读（`v$sqlarea` 2.4s，慢） |
| `v$sysstat` | ✅ 可读（9 项 2.5s） |
| `v$system_event` | ✅ 可读（0.8s） |
| `v$active_session_history` | ✅ 可读，6216 行、时间跨度 ~28h、最新样本≈当下 |
| `dba_tablespace_usage_metrics` / `dba_data_files` / `user_segments` | ✅ 可读 |
| `v$parameter` | ✅ 可读（`sessions`、`pga_aggregate_target` 等） |
| `v$system_parameter` | ⚠️ 可读但 `value` 全为 NULL → 不要用它，用 `v$parameter` |
| **`v$sysmetric` / `v$sysmetric_history`** | ❌ **0 行**（ADB 不产采样） |
| **`v$osstat` / `v$resource_limit`** | ❌ **0 行** |
| `session_privs` | ✅ 232 项（账号权限较大） |
| `v$sqlarea.parsing_schema_name` | ✅ 可用；`SYS_CONTEXT('USERENV','CURRENT_SCHEMA')` = **`ADMIN`**（应用即以此账号连库） |
| `v$sqlarea.service` | ❌ 该列**不存在**（ORA-00904），不要用 |
| `v$sqlarea.sql_text` | ✅ `SUBSTR(...,1,200)` 可读（返回真业务 SQL） |
| `v$session` 明细列 | ✅ `sid/username/program/machine/status/event/wait_class/last_call_et/sql_id/logon_time` 均可读 |

**关键推论**：现有 `services/db_monitor.py:102-121` 的「DB 负载」取自 `v$sysmetric` → **线上恒为空**（`db_monitor.py:320` 已有 `no_samples` 降级与文案）。本次必须换源，属于顺带修复。

### 2.2 采集成本（决定采样策略）

单次 SQL 往返实测（同一 session、热态）：

| 查询 | 耗时 |
| --- | --- |
| 单视图简单聚合（`v$session count`、`v$parameter`） | 0.57–0.71s |
| **合并单语句（会话数 + active/inactive + 6 项 v$sysstat + ASH AAS）** | **1.4s（热）/ 3.2s（冷启首拍）** |
| 同样的数据分开查（6 条语句） | ≈4.3s |
| 慢档三段合计（`waits by class` 0.7s + Top SQL 2.4s + 表空间 0.8s） | **3.9s** |
| `v$sqlarea` 按 schema 过滤后 | 3391 → **288 条**（滤掉 91% 是 Oracle 自维护 SQL） |
| `v$sqlarea` Top N | 2.4s |
| `v$sysstat`（9 项） | 2.5s |
| `v$session_event` | 4.1s（贵且噪声大 → 不用） |
| ASH 相对聚合 | 0.6s |

**硬约束：快档采样必须合并成 1 条语句；慢档（Top SQL）单独低频。**

### 2.3 宿主机指标源（www 用户实测可读）

`/proc/stat`、`/proc/meminfo`、`/proc/loadavg`、`/proc/net/dev`、`/proc/diskstats`、`df -h /`、`/proc/self/status` 全部可读。
- 生产：**单 worker** `uvicorn main:app --host 127.0.0.1 --port 8018`（`systemctl show zvms4 -p ExecStart`）→ **进程内采样/统计可靠，无需多进程聚合**。
- 宿主机：MemTotal 1965888 kB（1.9G）、`/` 29G 用 61%、load ≈0.01。
- 无 `psutil`，且**不得引入第三方依赖** → 一律读 `/proc`。

### 2.4 现有维护模式（已完整存在，勿重复造）

| 层 | 位置 |
| --- | --- |
| 状态持久化 | `db/system_state.py`（SQLite `system_state.db`，`maintenance_state` 单行 + `maintenance_events` 审计，2s TTL 缓存，`init_db()` 惰性建表） |
| HTTP 中间件 503 + admin 绕过 | `main.py:149-172`（`maintenance_middleware`），豁免清单 `main.py:125-134`（含 `/api/system` 前缀） |
| socket.io 准入 + 主动断连 | `realtime.py:127-192`（`MaintenanceSocketGuard`）、`realtime.py:95-124`（`disconnect_all_clients`）、`main.py:198-202` |
| 管理端点 | `routers/system_router.py`（`GET ""` 公开 / `POST /enable` / `POST /disable`），`main.py:314` |
| 前端 | `src/views/MaintenanceView.vue`（维护页，30s 轮询 `:55-59`）、`src/App.vue:299-309`、`src/plugins/axios.ts:126-138`（503 + `maintenance:true` → 跳维护页）、`src/views/user/UserLogin.vue:43-54` |

**结论：本次只需在清理流程外「包一层」进入/退出，不需要新建维护机制。**

### 2.5 清理现状与目标

- `POST /api/system/db/retention/preview`（只数不删）、`POST /api/system/db/retention/run`（**同步**，`routers/db_system_router.py:104-129`）。
- `tasks/retention.py`：`purge_now()` 手动入口 / `purge_expired_records()` 定时入口；批量 `RETENTION_BATCH_SIZE`(5000) × `RETENTION_MAX_BATCHES`(20)，每批独立 session + 独立 commit；进程级 `asyncio.Lock`，并发触发 `RetentionBusy`→**409**；审计写 `app_logs`（type `retention_purge`，level `warning` → 自身不被清理）；调度 `main.py:269-278`（**03:30 HKT**，APScheduler）。
- 附件字节清理 `tasks/files_retention.py`（03:00）**本次不改**（见 §7 我的裁定 3）。

### 2.6 响应封装与既有形状（必须照抄）

- 统一 envelope：`{"status": <"ok"|"degraded">, "code": 200, "data": {...}}`（`db_system_router.py:54-70`）。
- 异常边界 route class：`_DatabaseStatusRoute`（`db_system_router.py:31-50`）把非 HTTP/校验异常转 **503** + `Cache-Control: no-store`。
- `GET /api/system/db/status` 的 `data` 形状（**不得改动**）：`{storage:{available,tables:{available,count,total_bytes,items[]},tablespaces:{available,items[]}}, load:{available,metrics[],connection_pool{},reason?}, warnings[], observed_at, cache{cached,age_seconds,ttl_seconds}}`。
- `load.metrics[]` 元素字段（`db_monitor.py:283-295`）：`name, value, unit, begin_time, end_time, interval_centiseconds, group_id` → **字段名不变**，仅换数据源与新增项。
- `load.connection_pool`：`services/db_monitor.py:198-225` `_pool_status()` + `_response()` 里注入 `observed_at`。
- 前端消费点：`src/views/server/ServerDatabase.vue:429-508`（load + connection_pool）、`:328-377`（tablespaces）、`:380-426`（tables）、`:511-569`（清理卡）。

### 2.7 数据库连接层

`db/oracle/engine.py:41-60`：thin 模式，`create_async_engine(pool_size=5, max_overflow=5, pool_pre_ping=True, pool_recycle=1800)`，默认 `pool_timeout=30s`。SQLAlchemy 事件钩子先例：`db/oracle/clob.py:83-109`（挂 `sync_engine`）。
> 实测 `v$parameter.sessions = 30` → 池上限 10（5+5）留有充足余量，**本次不动池参数**。

### 2.8 前端既有能力

- 图表库：**`chart.js` + `vue-chartjs`**（`package.json:39,51`），用法参考 `src/components/group/ZGroupTimeStatistics.vue:12-14`。**无 ECharts，不得引入。**
- server 段现状：`src/views/server/{ServerHome,ServerDatabase,ServerMaintenance,ServerTotp}.vue`；`ServerHome.vue:26-42` 硬编码 3 个 tab，`router/index.ts:84-105` 静态子路由，**路由无 meta 权限**，页面内用 `if (!userStore.position.includes('admin')) router.replace('/not-found')`（`ServerDatabase.vue:23-27`）。
- 无通用轮询 composable；参考范式 `src/views/MaintenanceView.vue:24-31`。
- `src/api/system/db.ts:1-151` 已有 `DbStatusData` 等 TS 接口与 axios 封装范式。

### 2.9 义工详情页

- `src/components/activity/ZActivityDetails.vue:186-191` 挂载 `<ZActivityMemberList>`（默认 `mode='button'` → `ZButtonOrCard` 的 Dialog）；「X 人」文案在 `ZActivityMemberList.vue:360`，i18n key `activity.units.person`（`i18n/locales/activity.ts:147`）。
- `ZActivityMemberList.vue` **已支持 `mode='card'` 内联形态**（`ZActivityCreate.vue:323` 在用；递归 `:465`）。
- 数据：`api/activity/member.ts:60` `reads_v3` → `GET /v3/activities/{aid}/members?page&size&query` → `{members:[{_id,activity,member,member_name,mode,status,duration,user}], total}`，已分页（后端 `routers/activities_v3_router.py:98-143`）。**后端无需改动。**
- 复用风险：`ZActivityMemberList` 另有 3 个使用点（含 `selectable`/`wholesale` 批量选择，`ZActivityCreate.vue:323`、`:465`）；`ZActivityDetails` 还被 `ZActivityCard.vue:76`（列表卡片）复用 → 必须用 prop/mode 区分。

---

## 3. 存储选型（我的裁定，可否决）

**本地 SQLite（stdlib `sqlite3`），与业务库解耦。**

- 路径约定沿用 `db/system_state.py:20-24`：后端根下 `perf_samples.db`，支持环境变量覆盖 `ZVMS_PERF_DB`。
- 理由：① ADB 每次写 ≈0.5–0.95s，落 Oracle 会让采样成本翻倍；② 免 DDL 迁移（`db/oracle/ddl/` 需人工应用，见 `db/oracle/ddl/README.md`）；③ 与既有 `system_state.py` 同构（WAL、短连接、惰性建表、无新依赖）。
- 代价：历史随主机/后端目录走，换机丢失；运维指标可接受。

表结构（Lane 1 实现；全部 `CREATE TABLE IF NOT EXISTS`）：

```sql
perf_samples (ts INTEGER NOT NULL, series TEXT NOT NULL, value REAL);
CREATE INDEX ix_perf_samples ON perf_samples(series, ts);
perf_top_sql (ts INTEGER, sql_id TEXT, executions INTEGER, elapsed_ms REAL, cpu_ms REAL,
              avg_elapsed_ms REAL, buffer_gets INTEGER, disk_reads INTEGER, sql_text TEXT);
CREATE INDEX ix_perf_top_sql_ts ON perf_top_sql(ts);
perf_waits   (ts INTEGER, wait_class TEXT, sessions INTEGER);
perf_endpoints (ts INTEGER, method TEXT, route TEXT, count INTEGER, err_count INTEGER,
                avg_ms REAL, p95_ms REAL, max_ms REAL);
```
`ts` = epoch 秒（UTC）。

## 4. 采集策略（成本已核算）

- **每 30s 一拍（纯本地，零 DB 往返）**（`PERF_HOST_INTERVAL_SECONDS`，默认 30）：`/proc` 主机指标 + 连接池状态 + 应用侧端点统计刷盘。
- **每 120s**（`PERF_ORACLE_INTERVAL_SECONDS`，默认 120）追加 **1 条** Oracle 合并语句（会话数/active/inactive、`v$sysstat` 差值速率、ASH AAS）——实测 1.4s(热) / 3.2s(冷启首拍)。
- **每 600s**（`PERF_SLOW_INTERVAL_SECONDS`，默认 600）追加慢档（实测合计 3.9s）：`waits by class` + Top SQL（`v$sqlarea`，**必须按 schema 过滤**）+ 表空间使用率。
- **实时端点不依赖采样**：`/metrics/realtime` 的 host 部分即时读 `/proc`，Oracle 部分复用 `get_database_status()` 的 15s 缓存 + 会话明细独立短缓存（≤10s）。
- **速率类指标**：在进程内保存上一拍累计值与时间差计算 `*_per_sec`；进程重启后的第一拍只写累计值对应 series 缺失（不写假值）。
- **ASH 查询规避时区**：用 ASH 自身最大值作基准，禁止与 `SYSTIMESTAMP` 直接比较：
  `... WHERE sample_time > (SELECT MAX(sample_time) FROM v$active_session_history) - INTERVAL '5' MINUTE`
  （实测：与 `SYSTIMESTAMP` 比较会因时区错配返回 0 行。）
- **保留期** `PERF_RETENTION_DAYS` 默认 **14**；每天一次 DELETE（APScheduler job，Lane 1 注册）。
- 成本量级（写进报告）：Oracle 快档 ≈1.4s×720 ≈ **17 分钟/天**；慢档 ≈3.9s×144 ≈ **9 分钟/天**；**合计 ≈26 分钟/天 ≈ 1.8% 占空比**（host / 池 / 端点统计零 DB 成本）。三个间隔全部可配置；要再降本先抬 `PERF_SLOW_INTERVAL_SECONDS`。

### 4.1 series 字典（冻结键名，前端按此渲染）

主机（`/proc`，每拍）：
`host.cpu.user_pct` `host.cpu.system_pct` `host.cpu.idle_pct` `host.cpu.iowait_pct`
`host.mem.total_mb` `host.mem.used_mb` `host.mem.available_mb`
`host.load.1m` `host.load.5m` `host.load.15m`
`host.disk.used_pct` `host.disk.free_gb`
`host.net.rx_kbps` `host.net.tx_kbps`
`host.diskio.read_kbps` `host.diskio.write_kbps`
`host.proc.rss_mb` `host.proc.uptime_hours`

连接池（本地，每拍）：
`pool.size` `pool.checkedout` `pool.overflow` `pool.capacity` `pool.utilization_pct` `pool.waited`（因池满而等待的次数，nice-to-have）

Oracle 快档（每拍）：
`oracle.session.total` `oracle.session.active` `oracle.session.inactive`
`oracle.aas.5m`
`oracle.load.exec_per_sec` `oracle.load.commits_per_sec` `oracle.load.cpu_cs_per_sec`
`oracle.load.logical_reads_per_sec` `oracle.load.physical_reads_per_sec` `oracle.load.parse_per_sec`

Oracle 慢档（每 5 分钟）：
`oracle.tablespace.<TABLESPACE_NAME>.used_pct`
`oracle.wait.<WAIT_CLASS>.sessions`

> 速率类：`v$sysstat` 累计值（`execute count`、`user commits`、`CPU used by this session`、`session logical reads`、`physical reads`、`parse count (total)`）。

## 5. API 契约（逐字冻结）

新增 router：`routers/db_metrics_router.py`，挂载前缀 `/api/system/db/metrics`，**Lane 1 自带等价的 `_MetricsStatusRoute`（复制而非 import Lane 2 的私有类）**，鉴权 `_require_system_admin`（`routers/system_router.py:36-50`），envelope 同 §2.6。

### 5.1 `GET /api/system/db/metrics/realtime`

```json
{
  "status": "ok",
  "code": 200,
  "data": {
    "observed_at": "2026-10-18T03:40:12+00:00",
    "cache": {"cached": false, "age_seconds": 0.0, "ttl_seconds": 15},
    "storage": { "... 与 GET /status 的 data.storage 完全同形状，不得改动 ..." },
    "load": { "... 同 §2.6；source 改为 "V$SYSSTAT+V$ACTIVE_SESSION_HISTORY"，metrics[] 元素字段名不变 ..." },
    "pool": {"size": 5, "checkedout": 1, "overflow": 0, "capacity": 10, "utilization_percent": 10.0, "waited": 0},
    "host": {
      "cpu": {"user_pct": 1.2, "system_pct": 0.4, "idle_pct": 98.3, "iowait_pct": 0.1},
      "mem": {"total_mb": 1919.8, "used_mb": 900.1, "available_mb": 1101.8},
      "load": {"1m": 0.01, "5m": 0.02, "15m": 0.0},
      "disk": {"mount": "/", "used_pct": 61.0, "free_gb": 12.0},
      "net": {"rx_kbps": 12.3, "tx_kbps": 4.5},
      "diskio": {"read_kbps": 0.0, "write_kbps": 8.1},
      "proc": {"rss_mb": 180.2, "uptime_hours": 3.1}
    },
    "sessions": {
      "total": 6, "active": 1, "inactive": 5,
      "items": [
        {"sid": 6360, "username": "ADMIN", "program": "python3.12", "machine": "XiaoBoCloud-CMCU",
         "status": "INACTIVE", "event": "SQL*Net message from client", "wait_class": "Idle",
         "last_call_et": 29, "sql_id": null, "logon_time": "2026-10-18T03:18:35+00:00"}
      ]
    },
    "api": {
      "window_seconds": 60,
      "items": [
        {"method": "GET", "route": "/api/v3/activities/{aid}/members", "count": 12,
         "err_count": 0, "avg_ms": 420.5, "p95_ms": 810.2, "max_ms": 1120.0}
      ]
    },
    "warnings": []
  }
}
```

- `pool.overflow` 必须 `max(0, pool.overflow())`：SQLAlchemy 的 `_overflow` 计数在连接归还后会变**负数**（线上实测 `-4`），裸用会显示负溢出。既有 `load.connection_pool` 同步修此口径（严格改进，不改字段名）。
- 新增键（`pool` / `host` / `sessions` / `api`）**无条件发射**，形状可预期；`load` 内部除新增外**不要**删除既有 `load.connection_pool`（保持向后兼容，同一份池数据可两处并存）。
- 组件级降级沿用现有范式：权限不足（ORA-00942/01031）或超时 → `{"available": false, "reason": "..."}`，其余异常 → route class 转 503。

### 5.2 `GET /api/system/db/metrics/history`

Query：`series`（逗号分隔，必填，≤12 个）、`range`（`1h|6h|24h|7d`，默认 `24h`）、`agg`（`avg|max|min`，默认 `avg`）。

```json
{
  "status": "ok", "code": 200,
  "data": {
    "from": "2026-10-17T03:40:00+00:00",
    "to": "2026-10-18T03:40:00+00:00",
    "step_seconds": 720,
    "agg": "avg",
    "unknown_series": ["foo.bar"],
    "series": [
      {"key": "host.cpu.user_pct", "unit": "%",
       "points": [{"ts": "2026-10-18T03:00:00+00:00", "value": 1.2}]}
    ]
  }
}
```
- `step_seconds = ceil(range_seconds / 120 / 60) * 60`（每序列 ≤120 点，60 的整数倍：1h→60、6h→180、24h→720、7d→5040）。
- `unknown_series` 里的键被忽略，不报错。
- `points[].ts` 为 UTC ISO8601；前端本地时区展示。

### 5.3 `GET /api/system/db/metrics/top-sql`

Query：`limit`（默认 10，上限 50）。
```json
{"status":"ok","code":200,
 "data":{"observed_at":"2026-10-18T03:40:00+00:00",
   "items":[{"sql_id":"b39m8n96gxk7c","executions":298,"elapsed_ms":728319.3,"cpu_ms":697343.8,
             "avg_elapsed_ms":2444.0,"buffer_gets":26087739,"disk_reads":494671,"sql_text":"SELECT ..."}]}}
```
**必须过滤 Oracle 自维护 SQL**：`WHERE parsing_schema_name = SYS_CONTEXT('USERENV','CURRENT_SCHEMA')`（实测 = `ADMIN`）。不过滤时榜单前几名是 `dbms_autotask_prvt.run_autotask`、`dbms_auto_index_internal.capture_sts` 这类内部任务；过滤后 3391 → 288 条、且全是真业务 SQL。

`sql_text` = `SUBSTR(sql_text, 1, 200)`；不可读时为 `null`（降级，不报错）。`v$sqlarea` **没有** `service` 列（ORA-00904），不要用。

### 5.4 `GET /api/system/db/metrics/endpoints`

Query：`range`（`1h|6h|24h|7d`，默认 `1h`）、`limit`（默认 20，上限 100）。
```json
{"status":"ok","code":200,
 "data":{"window_seconds":3600,
   "items":[{"method":"GET","route":"/api/v3/activities/{aid}/members","count":120,
             "err_count":1,"avg_ms":420.5,"p95_ms":810.2,"max_ms":1120.0}]}}
```
- **route 必须用 FastAPI 路由模板**（`request.scope.get("route").path`），禁止用原始 URL，避免基数爆炸。
- 排除清单：`/api/health`、`/api/version`、静态资源；`/api/system/db/metrics/*` 自身**必须排除**（否则自我观测污染）。

### 5.5 清理端点（Lane 2）

- `POST /api/system/db/retention/preview`：**不变**。
- `POST /api/system/db/retention/run`：改为**后台任务**，立即返回
  `{"status":"ok","code":200,"data":{"job_id":"<uuid>","started_at":"...","state":"running"}}`；
  已有任务在跑 → **409**（沿用现约定）并在 detail 里带当前 phase。
  > 注意：HTTP 状态码仍 200（envelope 内 `status` 为 `ok`），仅在冲突时 409 —— 与现有 `RetentionBusy`→409 语义一致。
- `GET /api/system/db/retention/progress`（新增）：
```json
{"status":"ok","code":200,
 "data":{
   "running": true,
   "job_id": "9f1c...",
   "phase": "entering_maintenance|purging_app_logs|purging_export_tasks|leaving_maintenance|done|failed",
   "started_at": "2026-10-18T03:40:00+00:00",
   "finished_at": null,
   "maintenance": {"enabled": true, "since": "2026-10-18T03:40:00+00:00", "reason": "数据库清理", "auto": true},
   "progress": {"app_logs": {"deleted": 0, "remaining": 1234},
                "export_tasks": {"deleted": 0, "remaining": 56},
                "batches": 0, "max_batches": 20},
   "result": null,
   "error": null,
   "last_run": {"job_id": "prev...", "started_at": "...", "finished_at": "...", "state": "done",
                "result": {"app_logs": {"cutoff": "...", "deleted": 0, "remaining": 0},
                           "export_tasks": {"cutoff": "...", "deleted": 0, "remaining": 0},
                           "batches": 0}}
 }}
```
- 进度来源：`tasks/retention.py` 内部的进程内状态（单 worker 可靠）+ 现有批量循环的每批增量。

## 6. 维护编排契约（Lane 2）

1. **进入**：置进程内「自动清理进行中」标志 → 调 `db/system_state.set_state(True, reason="数据库清理", announcement=<见下>, updated_by="auto:retention")` → 复用现有 `realtime.disconnect_all_clients()`。
2. **恢复**：`try/finally` 保证任何异常路径都 `set_state(False, updated_by="auto:retention")` 并写审计事件。
3. **手动 / 定时同源**：`POST /retention/run`（后台任务）与 APScheduler `daily_retention_purge`（03:30）走**同一个编排函数**，都进维护。
4. **崩溃自愈（关键，防"永久 503"）**：
   - 不新增 schema 列；**复用 `maintenance_state.updated_by`** 编码自动来源（值以 `auto:` 开头即自动）。
   - `db/system_state.py` 暴露 `set_auto_source_probe(fn)`（由 `tasks/retention.py` 在 import 时注册，避免循环 import）；`is_enabled()` 判定时：若 `enabled=1` 且 `updated_by` 以 `auto:` 开头且 `probe()` 返回 False ⇒ **视为未维护**，并惰性清除该行 + 记审计事件。
   - 语义依据：单 worker 进程内标志即真相；进程重启后不可能还有清理在跑 ⇒ 陈旧态必须自愈。人工维护（`updated_by` 为 oid）**永不**自愈。
   - 顺序要求：**先置进程内标志，再 `set_state(True)`**，消除窗口期误自愈。
   - 探针必须在服务开始受理请求前注册：在 `tasks/retention.py` **模块级**注册即可（`main.py` 已 import 该模块用于调度），**因此 L2 不需要改 `main.py`**。
5. `announcement` 文案：`系统正在清理数据库历史数据，期间暂时无法访问，清理完成后自动恢复。`（≤300 字，见 `ServerMaintenance.vue` 校验）
6. 不加 `auto` 之外的维护窗口机制；不改 `main.py`；不改 `tasks/files_retention.py`。

## 7. 我的裁定（用户可否决/回退）

1. **时序落 SQLite 而非 Oracle 表**（理由见 §3；ADB 写入成本 + 免 DDL 迁移）。
2. **`load` 数据源从 `v$sysmetric` 换为 `v$sysstat` + ASH**（原视图线上恒空，属修复；`load.metrics[]` 字段名不变，前端兼容）。
3. **附件字节清理（03:00，`tasks/files_retention.py`）本次不纳入维护窗口**：用户说的是「数据库清理操作」，对应 DB 管理页清理卡那条；范围最小、语义清晰。要扩是一行改动。
4. **`pool_size`/`max_overflow` 不动**（实测 `sessions=30` 上限，池 10 有余量）。
5. **`(X人)` 按钮直接删除，不保留弹窗**（用户明确裁定）。

## 8. 车道划分与文件白名单（并行，禁止越界）

| 车道 | 范围 | 白名单（**只能改这些**） |
| --- | --- | --- |
| **L1** 后端采集与存储 | 采样器 + SQLite + 4 个 metrics 端点 + 主机指标 + 应用侧端点统计 | `services/db_monitor.py`、新增 `services/perf_sampler.py`、新增 `services/host_metrics.py`、新增 `services/api_metrics.py`、新增 `routers/db_metrics_router.py`、`main.py`、新增 `tests/test_perf_sampler.py`、`tests/test_host_metrics.py`、`tests/test_db_metrics_router.py`、`tests/test_api_metrics.py` |
| **L2** 清理后台化 + 维护编排 | 后台任务、进度端点、维护包封、崩溃自愈 | `tasks/retention.py`、`routers/db_system_router.py`、`db/system_state.py`、新增 `tests/test_retention_async.py`、`tests/test_maintenance_auto_heal.py`、`tests/test_db_system_router.py`（既有文件，可补） |
| **L3** 前端数据库页 | 性能面板 + 趋势图 + 清理进度 | `src/views/server/ServerDatabase.vue`、`src/api/system/db.ts`、`src/i18n/locales/**`、新增图表子组件（限 `src/components/server/` 或 `src/components/utils/`） |
| **L4** 前端义工详情内联 | 发放明细内联 | `src/components/activity/ZActivityMemberList.vue`、`src/components/activity/ZActivityDetails.vue`、`src/components/utils/ZButtonOrCard.vue`（仅必要时）、`src/i18n/locales/activity.ts` |

- **禁止交叉**：L1 不得改 `db_system_router.py` / `tasks/retention.py` / `db/system_state.py`；L2 不得改 `main.py` / `services/db_monitor.py`。
- L4 已完成派发（进行中），与 L1/L2/L3 无文件交集。
- L3 依赖 L1/L2 的契约（§5），**按契约实现、不等待后端落地**；新键缺失时前端必须优雅降级（不报错）。

## 9. 验证要求（各车道必须原样回贴输出）

- L1/L2：`cd zvms4-backend-python && .venv/bin/python -m pytest tests -q`（全绿）＋ 各自新增测试文件单跑输出；**在线实测**：`ssh zgovps-la` + 只读探针（新端点用 `curl 127.0.0.1:8018` 或 `--resolve`）贴真实 JSON 片段。
- L3/L4：`cd zvms4-frontend && npx --yes pnpm@10 run type-check` ＋ `npx --yes pnpm@10 run test:unit`（本机 pnpm 9.5 不认 workspace，必须 `npx --yes pnpm@10`）。
- L1 线上实测必须回贴：① 合并语句真实耗时；② `v$sqlarea` 经 schema 过滤后的 Top 10（证明无 Oracle 内部 SQL 混入）；③ `/metrics/realtime`、`/metrics/history` 的真实 JSON 片段。
- 契约只增不改：`GET /status`、`/retention/preview` 的既有字段必须逐字保留（测试断言钉死）。
- 不得 commit / push / 部署。

## 10. 风险与遗留

| 风险 | 处置 |
| --- | --- |
| 清理中进程崩溃 → 陈旧维护态 | §6.4 自愈（单 worker 进程内探针） |
| 采集成本（≈1.8% DB 占空比） | 三档间隔均可配；Oracle 侧恒为 1 条合并语句；降本先抬慢档间隔 |
| Top SQL 被 Oracle 自维护语句污染 | 强制 `parsing_schema_name` 过滤（§5.3），并列入 L1 线上验收 |
| ADB 视图权限随时可能变 | 组件级降级 `{available:false,reason}`，不让整页 503 |
| `sql_text` 可能不可读 | null 降级 |
| 维护期间前端轮询打爆 API | L3 在维护中降频/暂停轮询 |
| 采样表随目录迁移丢失 | 运维指标可接受；已在报告中声明 |
| **部署时把本地状态库/采样库同步到生产** | rsync 必须排除 `system_state.db*` 与 `perf_samples.db*`（否则本地测试产物会覆盖生产维护状态与采样历史）；`.gitignore` 同步补这两条。**已复现确认**：跑 `tests/golden/retention` 会在后端目录生成 `system_state.db` |

## 11. 复核记录

- 契约由主会话自行冻结，§2 全部条目来自**源码 file:line 核对**或**线上只读探针实测**（4 轮探针，zgovps-la）。
- **原计划的 @oracle 独立设计复核已按用户指示取消（模型配额不足），本次不设第三方复核门。** 替代措施：高风险项（§3 存储选型、§6.4 崩溃自愈、§5 契约兼容性、§8 车道文件白名单零交集）由主会话自查；实现完成后由主会话独立复跑测试 + 线上实测验收。
- 未由第三方复核、需实现方特别小心的项：§6.4 自愈与中间件 2s TTL 缓存的并发交互；多 worker 会破坏「进程内即真相」假设（当前实测单 worker，实现须在注释中写明该前提）。
