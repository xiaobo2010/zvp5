import axios from '@/plugins/axios.ts'

// Oracle 数据库只读监控:严格 admin,非 admin 返回 403,后端异常返回 503
// 响应头 Cache-Control: no-store,后端自带 15 秒缓存(见 cache.ttl_seconds)

// 单张表的存储占用与估算行数(estimated_rows 来自 USER_TABLES.NUM_ROWS,是优化器估算值)
export interface DbTableItem {
  name: string
  estimated_rows: number | null
  last_analyzed: string | null
  table_bytes: number
  index_bytes: number
  lob_bytes: number
  lob_index_bytes: number
  total_bytes: number
}

// 表空间容量:used_percent 为已用空间占 max_bytes 的百分比
// is_application 标记该表空间是否承载平台业务表(生产上仅 DATA 为 true,
// SYSTEM/SYSAUX/TEMP/UNDO/DBFS_DATA 等 Oracle 自有表空间为 false)
export interface DbTablespaceItem {
  name: string
  used_bytes: number | null
  max_bytes: number | null
  used_percent: number | null
  is_application: boolean
}

// 监控项部分降级时的附加字段
export interface DbSectionDegrade {
  reason?: string
  message?: string
}

export interface DbTablesSection extends DbSectionDegrade {
  available: boolean
  count: number | null
  total_bytes: number | null
  items: DbTableItem[]
}

export interface DbTablespacesSection extends DbSectionDegrade {
  available: boolean
  items: DbTablespaceItem[]
}

// V$SYSMETRIC 近期负载采样
export interface DbLoadMetric {
  name: string
  value: number | null
  unit: string | null
  begin_time: string | null
  end_time: string | null
  interval_centiseconds: number | null
  group_id: number | null
}

// 连接池指标仅代表当前 API 进程,不代表数据库全局
export interface DbConnectionPool extends DbSectionDegrade {
  available: boolean
  scope: string
  size?: number
  checked_out?: number
  overflow?: number
  capacity?: number
  utilization_percent?: number | null
  observed_at?: string
}

export interface DbLoadSection extends DbSectionDegrade {
  available: boolean
  source: string
  metrics: DbLoadMetric[]
  assessment: string
  connection_pool: DbConnectionPool
}

export interface DbCacheInfo {
  cached: boolean
  age_seconds: number
  ttl_seconds: number
}

export interface DbStatusData {
  observed_at: string
  storage: {
    available: boolean
    tables: DbTablesSection
    tablespaces: DbTablespacesSection
  }
  load: DbLoadSection
  warnings: string[]
  cache: DbCacheInfo
}

export interface DbStatusResponse {
  status: string
  code: number
  data: DbStatusData
}

// 读取 Oracle 数据库状态(严格 admin)
export function getDatabaseStatus() {
  return axios.get<DbStatusResponse>('/system/db/status')
}

// 数据保留期清理:预览只统计,执行为写操作;清理进行中返回 HTTP 409

export interface RetentionTablePreview {
  cutoff: string
  deletable: number
}

export interface RetentionPreviewData {
  app_logs: RetentionTablePreview
  export_tasks: RetentionTablePreview
}

export interface RetentionPreviewResponse {
  status: string
  code: number
  data: RetentionPreviewData
}

export interface RetentionTableRun {
  cutoff: string
  deleted: number
  remaining: number
}

export interface RetentionRunData {
  app_logs: RetentionTableRun
  export_tasks: RetentionTableRun
  batches: number
}

export interface RetentionRunResponse {
  status: string
  code: number
  data: RetentionRunData
}

// 预览两表按保留期可删除的行数(只读 COUNT,不删除数据)
export function previewRetention() {
  return axios.post<RetentionPreviewResponse>('/system/db/retention/preview')
}

// 执行保留期清理(同步跑至批次上限或清空,删除的行不可恢复)
export function runRetention() {
  return axios.post<RetentionRunResponse>('/system/db/retention/run')
}

// —— 清理后台化(L2 契约 §5.5)——
// 新版 POST /retention/run 立即返回后台任务句柄;旧后端仍返回同步结果。按 job_id 出现与否区分。
export interface RetentionJobStartData {
  job_id: string
  started_at: string
  state: string
}

export function isRetentionJobStart(
  data: RetentionRunData | RetentionJobStartData
): data is RetentionJobStartData {
  return 'job_id' in data
}

// 清理阶段(逐字对齐 §5.5,未知值以原文回显)
export type RetentionPhase =
  | 'entering_maintenance'
  | 'purging_app_logs'
  | 'purging_export_tasks'
  | 'leaving_maintenance'
  | 'done'
  | 'failed'

export interface RetentionMaintenanceInfo {
  enabled: boolean
  since: string | null
  reason: string | null
  auto: boolean
}

export interface RetentionProgressTable {
  deleted: number
  remaining: number
}

export interface RetentionProgressData {
  running: boolean
  job_id: string | null
  phase: string | null
  started_at: string | null
  finished_at: string | null
  maintenance: RetentionMaintenanceInfo | null
  progress: {
    app_logs: RetentionProgressTable
    export_tasks: RetentionProgressTable
    batches: number
    max_batches: number
  } | null
  result: RetentionRunData | null
  error: string | null
  last_run: {
    job_id: string
    started_at: string
    finished_at: string
    state: string
    result: RetentionRunData
  } | null
}

export interface RetentionProgressResponse {
  status: string
  code: number
  data: RetentionProgressData
}

// 读取清理进度(新增端点;后端未部署时 404,调用方按 progressSupported 降级)
export function getRetentionProgress() {
  return axios.get<RetentionProgressResponse>('/system/db/retention/progress')
}

// —— 性能采集(L1 契约 §5.1–5.4)——
// 主机资源:读 /proc,每次实时拍即时计算
export interface DbMetricsHostCpu {
  user_pct: number
  system_pct: number
  idle_pct: number
  iowait_pct: number
}

export interface DbMetricsHostMem {
  total_mb: number
  used_mb: number
  available_mb: number
}

export interface DbMetricsHostLoad {
  '1m': number
  '5m': number
  '15m': number
}

export interface DbMetricsHostDisk {
  mount: string
  used_pct: number
  free_gb: number
}

export interface DbMetricsHostNet {
  rx_kbps: number
  tx_kbps: number
}

export interface DbMetricsHostDiskIo {
  read_kbps: number
  write_kbps: number
}

export interface DbMetricsHostProc {
  rss_mb: number
  uptime_hours: number
}

export interface DbMetricsHost {
  cpu: DbMetricsHostCpu
  mem: DbMetricsHostMem
  load: DbMetricsHostLoad
  disk: DbMetricsHostDisk
  net: DbMetricsHostNet
  diskio: DbMetricsHostDiskIo
  proc: DbMetricsHostProc
}

// 连接池(新键,与既有 load.connection_pool 并存;checkedout 无下划线,系契约原文)
export interface DbMetricsPool {
  size: number
  checkedout: number
  overflow: number
  capacity: number
  utilization_percent: number
  waited: number
}

// v$session 会话明细
export interface DbMetricsSessionItem {
  sid: number
  username: string | null
  program: string | null
  machine: string | null
  status: string | null
  event: string | null
  wait_class: string | null
  last_call_et: number | null
  sql_id: string | null
  logon_time: string | null
}

export interface DbMetricsSessions {
  total: number
  active: number
  inactive: number
  items: DbMetricsSessionItem[]
}

// 应用侧端点统计(近窗口)
export interface DbMetricsEndpointItem {
  method: string
  route: string
  count: number
  err_count: number
  avg_ms: number
  p95_ms: number
  max_ms: number
}

export interface DbMetricsRealtimeData {
  observed_at: string
  cache: DbCacheInfo
  storage: DbStatusData['storage']
  load: DbLoadSection
  pool: DbMetricsPool
  host: DbMetricsHost
  sessions: DbMetricsSessions
  api: {
    window_seconds: number
    items: DbMetricsEndpointItem[]
  }
  warnings: string[]
}

export interface DbMetricsRealtimeResponse {
  status: string
  code: number
  data: DbMetricsRealtimeData
}

export type DbMetricsRange = '1h' | '6h' | '24h' | '7d'

export interface DbMetricsHistoryPoint {
  ts: string
  value: number | null
}

export interface DbMetricsHistorySeries {
  key: string
  unit: string | null
  points: DbMetricsHistoryPoint[]
}

export interface DbMetricsHistoryData {
  from: string
  to: string
  step_seconds: number
  agg: string
  unknown_series: string[]
  series: DbMetricsHistorySeries[]
}

export interface DbMetricsHistoryResponse {
  status: string
  code: number
  data: DbMetricsHistoryData
}

export interface DbMetricsTopSqlItem {
  sql_id: string
  executions: number
  elapsed_ms: number
  cpu_ms: number
  avg_elapsed_ms: number
  buffer_gets: number
  disk_reads: number
  sql_text: string | null
}

export interface DbMetricsTopSqlData {
  observed_at: string
  items: DbMetricsTopSqlItem[]
}

export interface DbMetricsTopSqlResponse {
  status: string
  code: number
  data: DbMetricsTopSqlData
}

// 实时面板(Oracle 部分 = 既有 get_database_status 缓存 + 会话明细短缓存)
export function getMetricsRealtime() {
  return axios.get<DbMetricsRealtimeResponse>('/system/db/metrics/realtime')
}

// 本地服务器快照(零 Oracle 往返):host/pool 字段与 /realtime 逐字一致(R6 §3.4)
export interface DbMetricsHostData {
  host: DbMetricsHost
  pool: DbMetricsPool
  observed_at?: string
}

export interface DbMetricsHostResponse {
  status: string
  code: number
  data: DbMetricsHostData
}

export function getMetricsHost() {
  return axios.get<DbMetricsHostResponse>('/system/db/metrics/host')
}

// 时序历史:series 为逗号分隔的 key 列表(§4.1 冻结键),未知 key 被后端忽略
export function getMetricsHistory(params: {
  series: string[]
  range: DbMetricsRange
  agg?: 'avg' | 'max' | 'min'
}) {
  return axios.get<DbMetricsHistoryResponse>('/system/db/metrics/history', {
    params: {
      series: params.series.join(','),
      range: params.range,
      agg: params.agg ?? 'avg'
    }
  })
}

// Top 慢 SQL(已按业务 schema 过滤 Oracle 自维护语句)
export function getMetricsTopSql(limit = 10) {
  return axios.get<DbMetricsTopSqlResponse>('/system/db/metrics/top-sql', {
    params: { limit }
  })
}

// 应用侧慢端点(range 语义:统计回看窗口)
export function getMetricsEndpoints(range: DbMetricsRange = '1h', limit = 20) {
  return axios.get<DbMetricsEndpointsResponse>('/system/db/metrics/endpoints', {
    params: { range, limit }
  })
}

export interface DbMetricsEndpointsData {
  window_seconds: number
  items: DbMetricsEndpointItem[]
}

export interface DbMetricsEndpointsResponse {
  status: string
  code: number
  data: DbMetricsEndpointsData
}
