import { request, unwrap } from '@/shared/http/client'
import type {
  BackupSnapshot,
  DatabaseStatus,
  HostSnapshot,
  MaintenanceState,
  RetentionPreview,
  RetentionProgress
} from '@/types/domain'

/** 维护模式状态：公开端点，维护期间也能读。 */
export async function fetchMaintenanceState(): Promise<MaintenanceState> {
  return unwrap<MaintenanceState>(
    await request({ url: '/system/maintenance', method: 'GET' })
  )
}

/** 开启维护模式：后端要求 reason（1..200），announcement 可选（≤300）。 */
export async function enableMaintenance(payload: {
  reason: string
  announcement?: string
}): Promise<MaintenanceState> {
  return unwrap<MaintenanceState>(
    await request({ url: '/system/maintenance/enable', method: 'POST', data: payload })
  )
}

export async function disableMaintenance(payload?: {
  reason?: string
}): Promise<MaintenanceState> {
  return unwrap<MaintenanceState>(
    await request({ url: '/system/maintenance/disable', method: 'POST', data: payload ?? {} })
  )
}

/** 数据库容量与负载（15s 缓存；`status` 在 envelope 顶层而不是 data 里）。 */
export async function fetchDatabaseStatus(): Promise<{
  status: 'ok' | 'degraded'
  data: DatabaseStatus | null
}> {
  const body = await request<{ status?: string; data?: DatabaseStatus }>({
    url: '/system/db/status',
    method: 'GET'
  })
  return { status: body.status === 'degraded' ? 'degraded' : 'ok', data: body.data ?? null }
}

/** 本机指标：不依赖数据库，DB 不可用时也能读。 */
export async function fetchHostSnapshot(): Promise<HostSnapshot | null> {
  return unwrap<HostSnapshot | null>(
    await request({ url: '/system/db/metrics/host', method: 'GET' })
  )
}

export async function fetchRetentionProgress(): Promise<RetentionProgress | null> {
  return unwrap<RetentionProgress | null>(
    await request({ url: '/system/db/retention/progress', method: 'GET' })
  )
}

/** 只统计、不删除：按下一次清理会用的截止时间计算各表可删行数。 */
export async function fetchRetentionPreview(): Promise<RetentionPreview | null> {
  return unwrap<RetentionPreview | null>(
    await request({ url: '/system/db/retention/preview', method: 'POST' })
  )
}

export async function fetchBackupProgress(): Promise<BackupSnapshot | null> {
  return unwrap<BackupSnapshot | null>(
    await request({ url: '/system/db/backup/progress', method: 'GET' })
  )
}
