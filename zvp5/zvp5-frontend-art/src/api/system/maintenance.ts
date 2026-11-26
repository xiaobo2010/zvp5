import axios from '@/plugins/axios.ts'

// 维护模式状态:公开接口,无需鉴权,维护期间也保持可用
export interface MaintenanceStatus {
  enabled: boolean
  since: number | null
  announcement: string | null
  // 服务端用 Authorization 复核出的 admin 身份。老后端不返回该字段,按 false 处理。
  admin?: boolean
}

export interface MaintenanceStatusResponse {
  status: string
  code: number
  data: MaintenanceStatus
}

// 读取维护状态(公开)
export function getMaintenanceStatus() {
  return axios.get<MaintenanceStatusResponse>('/system/maintenance')
}

// 开启维护模式(严格 admin):reason 必填 1-200 字,announcement 可选 ≤300 字
export function enableMaintenance(reason: string, announcement?: string) {
  const body: { reason: string; announcement?: string } = { reason }
  const trimmed = announcement?.trim()
  if (trimmed) {
    body.announcement = trimmed
  }
  return axios.post('/system/maintenance/enable', body)
}

// 关闭维护模式(严格 admin):body 可省略
export function disableMaintenance(reason?: string) {
  const trimmed = reason?.trim()
  if (!trimmed) {
    return axios.post('/system/maintenance/disable')
  }
  return axios.post('/system/maintenance/disable', { reason: trimmed })
}