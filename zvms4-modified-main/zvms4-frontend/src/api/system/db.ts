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