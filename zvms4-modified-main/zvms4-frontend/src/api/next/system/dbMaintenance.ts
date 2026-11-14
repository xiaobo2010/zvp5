import axios from '@/plugins/axios'

/**
 * 数据库维护中心（/system/db）。
 *
 * 与「维护模式」（/system/maintenance，站点级开关）不是一回事：这里是 Oracle
 * schema 的体检、优化建议、维护计划（只生成 SQL）与作业执行/进度。
 * 全部严格 admin。
 */

export type DbMaintenanceAction =
  | 'gather_stats'
  | 'rebuild_indexes'
  | 'shrink_space'
  | 'purge_recyclebin'
  | 'flush_cache'
  | 'warm_cache'

export interface DbTableStat {
  table_name: string
  row_count?: number
  size_bytes?: number
  last_analyzed_at?: string | null
  stale?: boolean
  [key: string]: unknown
}

export interface DbOverview {
  available: boolean
  table_count: number
  tables: DbTableStat[]
  components?: Record<string, unknown>
  warnings?: string[]
}

export interface DbSuggestion {
  severity?: 'high' | 'medium' | 'low' | string
  kind?: string
  table?: string
  message?: string
  action?: DbMaintenanceAction | string
  [key: string]: unknown
}

export interface DbPlanItem {
  table: string
  sql: string
  reason?: string
}

export interface DbPlan {
  action: DbMaintenanceAction | string
  items: DbPlanItem[]
  note?: string
}

export interface DbJob {
  job_id: string
  started_at: string
  state: string
}

export interface DbProgress {
  state?: string
  finished?: number
  total?: number
  message?: string
  [key: string]: unknown
}

interface Envelope<T> {
  status: string
  code: number
  data: T
}

const PREFIX = '/system/db'

export async function getDbOverview(): Promise<DbOverview> {
  const { data } = await axios.get<Envelope<DbOverview>>(`${PREFIX}/maintenance/overview`)
  return data.data
}

export async function getDbSuggestions(): Promise<DbSuggestion[]> {
  const { data } = await axios.get<Envelope<DbSuggestion[]>>(`${PREFIX}/maintenance/suggestions`)
  return Array.isArray(data.data) ? data.data : []
}

export async function buildDbPlan(
  action: DbMaintenanceAction,
  tables: string[] = []
): Promise<DbPlan> {
  const { data } = await axios.post<Envelope<DbPlan>>(`${PREFIX}/maintenance/plan`, {
    action,
    tables: tables.length ? tables : null
  })
  return data.data
}

export async function runDbMaintenance(
  action: DbMaintenanceAction,
  tables: string[] = [],
  dryRun = false
): Promise<DbJob> {
  const { data } = await axios.post<Envelope<DbJob>>(`${PREFIX}/maintenance/run`, {
    action,
    tables: tables.length ? tables : null,
    dry_run: dryRun,
    ...(dryRun ? {} : { confirm: 'RUN' })
  })
  return data.data
}

export async function getDbProgress(jobId?: string): Promise<DbProgress> {
  const { data } = await axios.get<Envelope<DbProgress>>(`${PREFIX}/maintenance/progress`, {
    params: jobId ? { job_id: jobId } : undefined
  })
  return data.data
}

export const DB_ACTIONS: { value: DbMaintenanceAction; label: string; hint: string }[] = [
  { value: 'gather_stats', label: '收集统计信息', hint: '让优化器拿到新统计，通常最先做' },
  { value: 'rebuild_indexes', label: '重建索引', hint: '索引碎片多、查询变慢时使用' },
  { value: 'shrink_space', label: '收缩空间', hint: '腾出表空间；会移动行，注意锁' },
  { value: 'purge_recyclebin', label: '清空回收站', hint: '释放被删除对象占用的空间' },
  { value: 'flush_cache', label: '清空缓存', hint: '只清应用缓存，不动数据库' },
  { value: 'warm_cache', label: '预热缓存', hint: '只预热应用缓存，不动数据库' }
]
