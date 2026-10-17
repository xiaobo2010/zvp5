# 数据库状态 API

```http
GET /api/system/db/status
Authorization: Bearer <管理员长期登录令牌>
User-Agent: Mozilla/5.0
```

只读采集当前后端配置选中的 Oracle ADB。仅接受已登录且当前仍有 `admin` 角色的用户，`department` 不视为系统管理员；临时校验令牌不能访问。
每次请求仍会校验用户、封禁状态和实时权限，缓存不会绕过鉴权。

## 响应与错误

HTTP 200 使用项目标准 envelope：`{"status":"ok"或"degraded","code":200,"data":{...}}`。
`ok` 表示三类监控项均可读取，不代表负载低或剩余容量充足。任一项权限不足、查询超时或无负载样本时返回 `degraded`，调用方应先检查各项 `available`。

```json
{
  "status": "degraded",
  "code": 200,
  "data": {
    "observed_at": "2026-10-17T12:00:00+00:00",
    "storage": {
      "available": true,
      "tables": {
        "available": true,
        "count": 1,
        "total_bytes": 327680,
        "items": [{
          "name": "USERS",
          "estimated_rows": 100,
          "last_analyzed": "2026-10-17T10:00:00",
          "table_bytes": 131072,
          "index_bytes": 65536,
          "lob_bytes": 65536,
          "lob_index_bytes": 65536,
          "total_bytes": 327680
        }]
      },
      "tablespaces": {
        "available": false,
        "items": [],
        "reason": "insufficient_privileges",
        "message": "当前数据库账号无法读取该监控项"
      }
    },
    "load": {
      "available": true,
      "source": "V$SYSMETRIC",
      "assessment": "insufficient_capacity_context",
      "metrics": [{
        "name": "Average Active Sessions",
        "value": 1.5,
        "unit": "Active Sessions",
        "begin_time": "2026-10-17T19:59:00",
        "end_time": "2026-10-17T20:00:00",
        "interval_centiseconds": 6000,
        "group_id": 2
      }],
      "connection_pool": {
        "available": true,
        "scope": "this_api_process",
        "size": 5,
        "checked_out": 1,
        "overflow": 0,
        "capacity": 10,
        "utilization_percent": 10,
        "observed_at": "2026-10-17T12:00:00+00:00"
      }
    },
    "warnings": ["estimated_rows 是优化器统计值，需结合 last_analyzed 判断新鲜度。"],
    "cache": {"cached": false, "age_seconds": 0, "ttl_seconds": 15}
  }
}
```

以上为结构示例，非真实数据库采样。`warnings` 的实际文字以服务端为准。

| HTTP 状态 | 含义 |
| --- | --- |
| 401 | 未登录、令牌过期/撤销或令牌用途不符 |
| 403 | 当前用户无系统管理员权限 |
| 503 | 数据库连接、鉴权查库、会话创建或采集发生不可恢复错误 |

503 不返回 SQL、DSN、密码或内部异常类型。监控成功响应和内部错误响应设置 `Cache-Control: no-store`。
现有应用中间件仍可能返回维护/盾牌等状态。

## 存储和表的统计口径

- `storage.tables` 来自当前连接用户的 `USER_TABLES`、`USER_SEGMENTS`、`USER_INDEXES`、`USER_LOBS`。`count` 为该 schema 可见的表数，包含非业务辅助表。
- 字节值是已分配的段空间，分别统计表、普通索引、LOB 及 LOB 索引；按基对象名汇总分区/子分区。普通索引排除 `INDEX_TYPE='LOB'`，避免重复计算。
- `tables.total_bytes` 是这些表的段空间之和，不是整个 ADB 实例的物理存储或 OCI 计费容量；不包含 undo、temp、空闲容量及其他 schema。
- `estimated_rows` 为 `USER_TABLES.NUM_ROWS`，可能为空或滞后，并附 `last_analyzed`。接口不会执行逐表 `COUNT(*)`、收集统计信息或修改业务数据。
- `storage.tablespaces.items` 包含 `name`、`used_bytes`、`max_bytes`、`used_percent`。空间来源为 `DBA_TABLESPACE_USAGE_METRICS`，乘以 `DBA_TABLESPACES.BLOCK_SIZE` 转为字节。
- `max_bytes` 会考虑数据文件自动扩展、底层可用空间和 PDB 限额，并非 OCI 购买的容量。表空间可能包含其他 schema 的对象，不能直接与 `tables.total_bytes` 相减得出本应用剩余空间。
- 不可读取时字段返回 `available:false`，错误原因可为 `insufficient_privileges` 或 `query_timeout`；此时空 `items` 不表示没有表/没有用量。

## 负载与缓存

`V$SYSMETRIC` 选择 `group_id=2` 的近期约 60 秒样本，保留指标名称、值、原始单位、采样起止时间和窗口长度。
白名单包括活动会话、CPU 时间比例、执行/事务/调用频率、会话数、物理读写、逻辑读及 SQL 响应时间。数据库版本/权限不同可能仅返回其中部分指标；无样本标记 `reason:no_samples`。

`Database CPU Time Ratio` 是数据库时间中的 CPU 时间占比，不能当作计算资源利用率。当前缺少 ECPU/OCPU 容量和基线，故 `assessment` 固定为 `insufficient_capacity_context`，不臆测“负载高/低”。
这些视图的可见范围取决于当前连接实例/容器；没有跨 RAC 实例聚合。`connection_pool` 仅反映当前 API worker 的 SQLAlchemy 池，响应时刷新，包含该请求自身可能占用的连接，不是整个数据库的会话总数。

采集结果缓存 15 秒，同一进程内并发请求合并采集；不同 worker 各自缓存。`data.observed_at` 是 UTC 采集完成时间；Oracle 的 `last_analyzed` 和指标起止时间按数据库原始时间返回，未擅自附加 UTC 时区。
缓存期间不重新执行监控 SQL，因此它是近期观察值，实时存活检查可使用 `/api/health`。
每条监控 SQL 的执行等待上限为 3 秒，不是整个 HTTP 请求的总超时；鉴权查询、连接获取和多个降级恢复步骤不包含在单条 SQL 的窗口中。
权限错误（ORA-00942/ORA-01031）和单项超时经回滚、连接探测后降级；断连、SQL 错误或无法恢复的 session 返回 503。

## OCI 提供的指标 API（后续接入）

Oracle OCI Monitoring 的 `SummarizeMetricsData` 可以查询 Autonomous Database 的云端指标。Serverless 共享基础设施常用 namespace 为 `oci_autonomous_database`，指标包括 `CpuUtilization`、`AverageActiveSessions`、`Sessions`、`SessionUtilization`、`QueuedStatements`、`StorageAllocated`、`StorageUsed`、`StorageMax` 和 `StorageUtilization`。

接入需要正确的 region、compartment OCID、数据库资源 OCID，以及允许读取指标的 IAM 身份。此接口当前不调用 OCI、不新增云端凭据，也不自动授予数据库字典视图权限。
后续可用 OCI 的 CPU 利用率、会话利用率和存储容量补充本接口，并保留来源、时间窗及各自单位。当前尚无数据库切换或写配置 API。

官方参考：

- [Autonomous Database Serverless Metrics](https://docs.oracle.com/en-us/iaas/autonomous-database-serverless/doc/autonomous-monitor-metrics-list.html)
- [SummarizeMetricsData API](https://docs.oracle.com/en-us/iaas/api/#/en/monitoring/20180401/MetricData/SummarizeMetricsData)
- [DBA_TABLESPACE_USAGE_METRICS](https://docs.oracle.com/en/database/oracle/oracle-database/19/refrn/DBA_TABLESPACE_USAGE_METRICS.html)
- [DBA_SEGMENTS](https://docs.oracle.com/en/database/oracle/oracle-database/19/refrn/DBA_SEGMENTS.html)
- [V$SYSMETRIC](https://docs.oracle.com/en/database/oracle/oracle-database/19/refrn/V-SYSMETRIC.html)

## 验收边界

本轮已在本地运行离线测试，覆盖登录/权限、接口错误、缓存隔离、监控部分降级、负载单位和存储聚合。聚合 SQL 使用字典视图形状的 fixture 验证，不构成真实 Oracle SQL/权限联调。
由于本地未提供 ADB 凭据及 wallet，上线前仍需在测试实例上确认字典视图权限、SQL/驱动兼容性、取消后的连接恢复及负载指标可见性。
