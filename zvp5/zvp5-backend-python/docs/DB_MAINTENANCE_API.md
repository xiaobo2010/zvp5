# 数据库维护 API（ADB）

前缀 `/api/system/db`，**仅 admin**（`_require_system_admin`：即时查库复核，不信
token 里的 `per` 快照）。所有响应沿用统一 envelope：

```json
{"status": "ok", "code": 200, "data": { ... }}
```

失败时是 FastAPI 的 `{"detail": "..."}`，4xx 语义保留（不会被 router 的兜底
转成 503）；只有「意外的数据库不可用」才会是 503。所有端点都带
`Cache-Control: no-store`。

- 只读体检：`overview` / `suggestions`
- 计划与执行：`plan` / `run` / `progress`
- 缓存：`cache/flush` / `cache/refresh`

---

## 动作（action）

| action | 作用 | 生成的 SQL | 结束后自动失效缓存 |
|---|---|---|---|
| `gather_stats` | 重建优化器统计信息 | `BEGIN DBMS_STATS.GATHER_TABLE_STATS(ownname=>USER, tabname=>'T', cascade=>TRUE, degree=>4); END;` | users / groups / activities / conversations |
| `rebuild_indexes` | 在线重建索引 | `ALTER INDEX "IDX" REBUILD ONLINE` | 同上 |
| `shrink_space` | 回收表内空闲空间 | `ALTER TABLE "T" SHRINK SPACE CASCADE` | 同上 |
| `purge_recyclebin` | 清空回收站 | `PURGE RECYCLEBIN` | 同上 |
| `flush_cache` | 只失效缓存，不碰数据库 | 无 | 全部 6 个域 |
| `warm_cache` | 失效后重新生成进程内快照 | 无 | 全部 6 个域 + `settings_runtime.refresh(force=True)` + `roles.policy.refresh(force=True)` |

`tables` 上限 50，每项必须匹配 `^[A-Za-z][A-Za-z0-9_$#]{0,29}$`（`validate_identifier`，
自动大写）。`shrink_space` 必须显式点名表，不支持全库。

---

## GET /api/system/db/maintenance/overview

表级体量 + 统计新鲜度。字典视图按**组件**降级：权限不足 / 超时只让对应字段
变 `null`，`components` 里给出 `{"available": false, "reason": "..."}`，HTTP 仍是
200。`reason` ∈ `insufficient_privileges` | `query_timeout` | `unavailable`。

```json
{
  "status": "ok", "code": 200,
  "data": {
    "available": true,
    "table_count": 1,
    "tables": [{
      "table_name": "USERS",
      "num_rows": 12000,
      "blocks": 200,
      "avg_row_len": 120,
      "last_analyzed": "2026-11-04T12:00:00",
      "index_count": 3,
      "index_bytes": 8192,
      "table_bytes": 65536,
      "total_bytes": 73728
    }],
    "components": {
      "tables": {"available": true},
      "statistics": {"available": true},
      "indexes": {"available": true},
      "index_columns": {"available": true},
      "segments": {"available": false, "reason": "insufficient_privileges"}
    },
    "warnings": ["数据字典 segments 不可用（insufficient_privileges），相关字段为空"],
    "observed_at": "2026-11-04T04:00:00+00:00"
  }
}
```

数据来源：`USER_TABLES`（基数兜底）、`USER_TAB_STATISTICS`（最近一次 GATHER 的
真实值，优先）、`USER_INDEXES`、`USER_IND_COLUMNS`（`duplicate_index` 需要的列
集合）、`USER_SEGMENTS`（真实占用）。`total_bytes = table_bytes + index_bytes`，
不含 LOB 段。

## GET /api/system/db/maintenance/suggestions

```json
{
  "status": "ok", "code": 200,
  "data": {
    "available": true,
    "suggestions": [{
      "code": "index_unusable",
      "severity": "high",
      "table": "USERS",
      "message": "索引 IDX_USERS_BAD 状态为 UNUSABLE，优化器不会使用它",
      "action": "rebuild_indexes"
    }],
    "counts": {"high": 1, "medium": 2, "low": 1},
    "truncated": false,
    "components": { "...": "同 overview" },
    "generated_at": "2026-11-04T04:00:00+00:00"
  }
}
```

| code | severity | 触发条件 | 建议 action |
|---|---|---|---|
| `index_unusable` | high | `USER_INDEXES.STATUS != 'VALID'` | `rebuild_indexes` |
| `table_large_no_index` | high | 无索引且（`num_rows ≥ 5000` 或 `total_bytes ≥ 50MB`） | 人工评估 |
| `stats_stale` | medium | `last_analyzed` 为 NULL 或超过 30 天 | `gather_stats` |
| `index_high_blevel` | medium | `blevel ≥ 3` | `rebuild_indexes` |
| `duplicate_index` | low | 同表同列集合存在多个索引 | 人工评估（`DROP INDEX`） |

按 severity 排序（high → medium → low），最多 50 条；`counts` 是截断前的全量
计数。`USER_IND_COLUMNS` 不可用时跳过 `duplicate_index` 并在 `components` 里标注。

## POST /api/system/db/maintenance/plan

只生成 SQL，**不执行**。

```json
// 请求
{"action": "gather_stats", "tables": ["users"]}

// 响应
{
  "status": "ok", "code": 200,
  "data": {
    "action": "gather_stats",
    "items": [{
      "table": "USERS",
      "sql": "BEGIN DBMS_STATS.GATHER_TABLE_STATS(ownname=>USER, tabname=>'USERS', cascade=>TRUE, degree=>4); END;",
      "reason": "重建 USERS 的优化器统计信息（含索引级 cascade）"
    }],
    "note": ""
  }
}
```

- `gather_stats` 不传 `tables` → 只针对统计过期/从未收集的表。
- `rebuild_indexes` 不传 `tables` → 只针对不可用 / `blevel ≥ 3` 的索引；传了 →
  该表上的全部索引。
- `shrink_space` 必须传 `tables`。
- `purge_recyclebin` 忽略 `tables`，恒为一条 `PURGE RECYCLEBIN`。
- `flush_cache` / `warm_cache` 的 `items` 恒为空，`note` 说明不产生 SQL。

错误：未知 action / 非法表名 / 表不存在 / 超过 50 张表 → 400。

## POST /api/system/db/maintenance/run

后台任务，立即返回句柄；进度用 `/maintenance/progress` 轮询。

```json
// 请求
{"action": "rebuild_indexes", "tables": ["users"], "confirm": "RUN"}

// 响应
{
  "status": "ok", "code": 200,
  "data": {"job_id": "<uuid>", "started_at": "2026-11-04T04:00:00+00:00", "state": "running"}
}
```

- `confirm` 必须等于 `"RUN"`，否则 400。
- `dry_run: true` 时只生成计划、不执行 SQL，此时 `confirm` 可省略；结果在
  `progress.result.items` 里。
- `dry_run: true` 时**不产生任何副作用**：不执行 SQL、不失效缓存、不重建快照
  （`result.cache` 为空、`note` 标注「预演未失效缓存」）。
- 已有作业在跑 → **409**（detail 带当前 phase），不排队。
- `tables` 校验同 `plan`。

## GET /api/system/db/maintenance/progress

```json
{
  "status": "ok", "code": 200,
  "data": {
    "running": false,
    "job_id": "<uuid>",
    "action": "shrink_space",
    "dry_run": false,
    "phase": "done",
    "started_at": "2026-11-04T04:00:00+00:00",
    "finished_at": "2026-11-04T04:01:10+00:00",
    "progress": {"total": 2, "done": 1, "failed": 1, "current": null},
    "result": {
      "action": "shrink_space",
      "dry_run": false,
      "items": [{"table": "USERS", "sql": "ALTER TABLE \"USERS\" SHRINK SPACE CASCADE", "reason": "..."}],
      "note": "",
      "executed": 1,
      "failed": 1,
      "errors": [{"table": "USERS", "error": "ORA-65040"}],
      "cache": {"scopes": ["users", "groups", "activities", "conversations"], "flushed": {"users": 12}, "versions": {"users": 8}}
    },
    "error": null
  }
}
```

- `phase ∈ planning | running | flushing_cache | warming_cache | done | failed`。
- `progress.failed` 是**逐表失败计数**；部分失败时作业仍是 `done`，全部失败才
  是 `failed`。异常逃逸（规划阶段报错、被取消）也会收尾成 `failed`。
- `result.errors[].error` 只含 `ORA-nnnnn` 或异常类名：Oracle 报错原文可能夹带
  SQL / 连接信息，只留在服务端日志。
- 作业状态是**单 worker 进程内**变量（与 `tasks/retention.py` 同前提）；多 worker
  部署时每个进程只看到自己的作业。

## POST /api/system/db/cache/flush

```json
// 请求（scopes 必须是 users/groups/activities/conversations/settings/roles 的子集）
{"scopes": ["users", "groups"]}

// 响应
{
  "status": "ok", "code": 200,
  "data": {
    "scopes": ["users", "groups"],
    "flushed": {"users": 12, "groups": 3},
    "versions": {"users": 8, "groups": 2}
  }
}
```

未知 scope → 400。缓存层不可用（未配置 Redis / 连接失败）时 `flushed` 计数可能
为 0，接口仍 200 —— 失效是尽力而为，不会把管理页变成 500。

## POST /api/system/db/cache/refresh

失效后重新生成进程内快照（设置中心 + 角色策略）。

```json
// 请求（scopes 可选，仅用于校验）
{"scopes": ["settings", "roles"]}

// 响应
{"status": "ok", "code": 200, "data": {"settings": true, "roles": false}}
```

两个 bool 分别表示该处刷新是否成功；失败的那一项已在服务端日志里记录原因，
另一项不受影响。

**强制回源**：两处 `refresh()` 平时带 5 分钟版本闸（版本号没变就直接返回），
本端点一律 `force=True` 绕过闸门 —— 点了刷新就一定会重新读库，不受版本闸
影响。`scopes` 仅用于校验，不改变「两处都刷新」的语义。

---

## ADB 限制与注意事项

1. **`SHRINK SPACE` 可能被 ADB 拒绝**（不是所有 ADB 版本都允许在线 shrink，
   常见 `ORA-65040`/`ORA-00439`）。执行是**逐表 catch + 独立提交**：一张失败只
   记进 `result.errors` 并继续下一张，不会中断整个作业。
2. `GATHER_TABLE_STATS` 在大表上可能跑几十秒；单条语句超时 60s，超时按该表失败
   记录。
3. `REBUILD ONLINE` 期间索引仍可读，但会占额外临时空间；磁盘紧张时先看
   `/maintenance/overview` 的 `index_bytes`。
4. `PURGE RECYCLEBIN` 只清当前 schema（`USER` 视角），不可恢复。
5. `overview`/`suggestions` 的 `USER_*` 字典视图只覆盖当前登录 schema，不会跨
   schema 读别的账号。

## 回滚方式

| 动作 | 可安全重跑 | 失败影响 |
|---|---|---|
| `gather_stats` | 是（幂等） | 无数据影响，只是统计没更新 |
| `rebuild_indexes` | 是（幂等） | 索引短暂不可用/重建失败时保留原索引，不丢数据 |
| `purge_recyclebin` | 是（幂等） | 已 DROP 的对象本就不可用，清空后不可恢复 |
| `shrink_space` | 是（可重复执行） | **失败不影响数据**：ORA 拒绝时表保持原状；成功只做段内整理 |
| `flush_cache` | 是 | 只是让缓存回源，下一次读自动重建 |
| `warm_cache` | 是 | 刷新快照失败会保留旧快照（`refresh()` 内部回落默认值） |

没有「自动回滚」：`rebuild_indexes` 失败时 Oracle 保留原索引；`shrink_space`
失败时表数据不动。任何动作都不会删表/删行，唯一的不可逆操作是
`purge_recyclebin`（清回收站）。
