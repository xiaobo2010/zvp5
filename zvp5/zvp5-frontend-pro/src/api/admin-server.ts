// Admin server-console API client for the standalone Pro app.
//
// Every endpoint below is a real backend route under `/api`; the helpers in
// `http.ts` add the Bearer token and unwrap the legacy `{ status, data }`
// envelope. The Pro client stays dependency-free (fetch, no axios).

import { apiRequest, buildQuery, jsonBody } from './http'

/* -------------------------------------------------------------------------- */
/* Overview                                                                    */
/* -------------------------------------------------------------------------- */

// `/health` returns a plain "OK" string in the legacy envelope. A failed ping
// comes back as HTTP 503, which `apiRequest` turns into an ApiError.
export async function fetchServerHealth(): Promise<string> {
  const data = await apiRequest<unknown>('/health')
  return typeof data === 'string' ? data : 'OK'
}

export async function fetchServerVersion(): Promise<string> {
  const data = await apiRequest<unknown>('/version')
  if (typeof data === 'string') return data
  if (data && typeof data === 'object' && 'version' in data) {
    const value = (data as { version?: unknown }).version
    if (typeof value === 'string') return value
  }
  return ''
}

/* -------------------------------------------------------------------------- */
/* Maintenance mode                                                            */
/* -------------------------------------------------------------------------- */

export interface MaintenanceStatus {
  enabled: boolean
  since: number | null
  announcement: string | null
  // Returned by the enable/disable mutations; the public GET never exposes it.
  reason?: string | null
  admin?: boolean
}

export function fetchMaintenanceStatus(): Promise<MaintenanceStatus> {
  return apiRequest<MaintenanceStatus>('/system/maintenance')
}

export function enableMaintenance(
  reason: string,
  announcement?: string
): Promise<MaintenanceStatus> {
  const body: { reason: string; announcement?: string } = { reason }
  const trimmed = announcement?.trim()
  if (trimmed) body.announcement = trimmed
  return apiRequest<MaintenanceStatus>('/system/maintenance/enable', jsonBody(body))
}

export function disableMaintenance(reason?: string): Promise<MaintenanceStatus> {
  const trimmed = reason?.trim()
  return apiRequest<MaintenanceStatus>(
    '/system/maintenance/disable',
    trimmed ? jsonBody({ reason: trimmed }) : { method: 'POST' }
  )
}

/* -------------------------------------------------------------------------- */
/* Database monitor                                                            */
/* -------------------------------------------------------------------------- */

export interface DbSectionDegrade {
  reason?: string
  message?: string
}

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

export interface DbTablesSection extends DbSectionDegrade {
  available: boolean
  count: number | null
  total_bytes: number | null
  items: DbTableItem[]
}

export interface DbTablespaceItem {
  name: string
  used_bytes: number | null
  max_bytes: number | null
  used_percent: number | null
  is_application: boolean
}

export interface DbTablespacesSection extends DbSectionDegrade {
  available: boolean
  items: DbTablespaceItem[]
}

export interface DbLoadMetric {
  name: string
  value: number | null
  unit: string | null
  begin_time: string | null
  end_time: string | null
  interval_centiseconds: number | null
  group_id: number | null
}

export interface DbConnectionPool extends DbSectionDegrade {
  available: boolean
  scope: string
  size?: number
  checked_out?: number
  overflow?: number
  capacity?: number
  utilization_percent?: number | null
  waited?: number
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

export function fetchDbStatus(): Promise<DbStatusData> {
  return apiRequest<DbStatusData>('/system/db/status')
}

/* -------------------------------------------------------------------------- */
/* TOTP                                                                        */
/* -------------------------------------------------------------------------- */

export interface TotpData {
  otp: string
  // Unix seconds; the backend re-computes this for every response.
  exp: number
}

export function fetchMyTotp(userId: string): Promise<TotpData> {
  return apiRequest<TotpData>(`/v1/users/${encodeURIComponent(userId)}/totp`)
}

/* -------------------------------------------------------------------------- */
/* SSH targets (read-only catalogue)                                           */
/* -------------------------------------------------------------------------- */

export interface SshTarget {
  id: string
  label: string
  display: string
}

export interface SshTargetsData {
  targets: SshTarget[]
  default: string
}

export function fetchSshTargets(): Promise<SshTargetsData> {
  return apiRequest<SshTargetsData>('/system/ssh/targets')
}

/* -------------------------------------------------------------------------- */
/* Files (read-only listing)                                                   */
/* -------------------------------------------------------------------------- */

export interface FileEntry {
  name: string
  type: 'dir' | 'file' | 'symlink' | 'other'
  size: number
  mode: number
  // Unix seconds, matching `services/file_ops.py:_describe`.
  mtime: number
  is_symlink: boolean
  target?: string | null
}

export interface FileListData {
  path: string
  offset: number
  limit: number
  total: number
  entries: FileEntry[]
}

export function listServerFiles(
  path: string,
  offset = 0,
  limit = 200
): Promise<FileListData> {
  return apiRequest<FileListData>(
    `/system/files/list${buildQuery({ path, offset, limit })}`
  )
}

/* -------------------------------------------------------------------------- */
/* Database content (read-only schema / rows)                                  */
/* -------------------------------------------------------------------------- */

export interface SchemaItem {
  name: string
  type: 'TABLE' | 'VIEW'
  num_rows: number | null
  last_analyzed: string | null
}

export interface SchemaData {
  total: number
  tables: SchemaItem[]
}

export interface DbLobValue {
  $lob: 'CLOB' | 'BLOB'
  length: number
  preview?: string | null
}

export type DbCellValue = string | number | boolean | null | DbLobValue

export interface RowsData {
  table: string
  columns: string[]
  rows: Record<string, DbCellValue>[]
  primary_key: string[]
  offset: number
  limit: number
  has_more: boolean
  total_estimate: number | null
}

export function fetchContentSchema(): Promise<SchemaData> {
  return apiRequest<SchemaData>('/system/db/content/schema')
}

export function fetchTableRows(
  name: string,
  offset = 0,
  limit = 20
): Promise<RowsData> {
  return apiRequest<RowsData>(
    `/system/db/content/table/${encodeURIComponent(name)}/rows${buildQuery({
      offset,
      limit
    })}`
  )
}

export function isLobValue(value: DbCellValue): value is DbLobValue {
  return (
    typeof value === 'object' &&
    value !== null &&
    '$lob' in value &&
    typeof (value as DbLobValue).$lob === 'string'
  )
}
