import axios from '@/plugins/axios.ts'

// —— 契约 §4.5 冻结格式,逐字段对应,未新增字段 —— //

export interface LobValue {
  $lob: 'CLOB' | 'BLOB'
  length: number
  preview?: string | null
}

export function isLob(value: unknown): value is LobValue {
  return (
    typeof value === 'object' &&
    value !== null &&
    '$lob' in value &&
    typeof (value as LobValue).$lob === 'string'
  )
}

export type CellValue = string | number | boolean | null | LobValue

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

export interface ColumnDef {
  name: string
  type: string
  length: number | null
  nullable: boolean
  default: string | number | boolean | null | LobValue
  primary_key: boolean
}

export interface ColumnsData {
  table: string
  columns: ColumnDef[]
  primary_key: string[]
}

export interface RowsData {
  table: string
  columns: string[]
  rows: Record<string, CellValue>[]
  primary_key: string[]
  offset: number
  limit: number
  has_more: boolean
  total_estimate: number | null
}

export interface QueryData {
  columns: string[]
  rows: Record<string, CellValue>[]
  truncated: boolean
  elapsed_ms: number
}

export interface DdlData {
  rowcount: number | null
  committed: boolean
  summary: string
}

export interface ApiResponse<T> {
  status: string
  code: number
  data: T
}

export function getSchema() {
  return axios.get<ApiResponse<SchemaData>>('/system/db/content/schema')
}

export function getColumns(table: string) {
  return axios.get<ApiResponse<ColumnsData>>(`/system/db/content/table/${encodeURIComponent(table)}/columns`)
}

export function getRows(
  table: string,
  offset: number,
  limit: number,
  orderBy?: string | null,
  desc?: boolean
) {
  return axios.get<ApiResponse<RowsData>>(`/system/db/content/table/${encodeURIComponent(table)}/rows`, {
    params: { offset, limit, order_by: orderBy ?? undefined, desc: desc ?? undefined }
  })
}

// 写操作全部带 totp_code;key 必须覆盖全部主键列
export function insertRow(table: string, values: Record<string, CellValue>, totpCode: string) {
  return axios.post<ApiResponse<{ rowcount: number }>>(
    `/system/db/content/table/${encodeURIComponent(table)}/rows`,
    { values, totp_code: totpCode }
  )
}

export function updateRow(
  table: string,
  key: Record<string, CellValue>,
  values: Record<string, CellValue>,
  totpCode: string
) {
  return axios.put<ApiResponse<{ rowcount: number }>>(
    `/system/db/content/table/${encodeURIComponent(table)}/rows`,
    { key, values, totp_code: totpCode }
  )
}

export function deleteRow(table: string, key: Record<string, CellValue>, totpCode: string) {
  return axios.delete<ApiResponse<{ rowcount: number }>>(
    `/system/db/content/table/${encodeURIComponent(table)}/rows`,
    { data: { key, totp_code: totpCode } }
  )
}

// 只读查询,不带双人码
export function runQuery(sql: string, limit = 100) {
  return axios.post<ApiResponse<QueryData>>('/system/db/content/query', { sql, limit })
}

// 双人码;破坏性语句须 confirm 等于目标表名
export function runDdl(sql: string, confirm: string, totpCode: string) {
  return axios.post<ApiResponse<DdlData>>('/system/db/content/ddl', {
    sql,
    confirm,
    totp_code: totpCode
  })
}
