/** 统一的 API 错误模型：页面只关心 kind/code/message。 */

export type ApiErrorKind =
  | 'network'
  | 'unauthorized'
  | 'forbidden'
  | 'not-found'
  | 'conflict'
  | 'maintenance'
  | 'banned'
  | 'device'
  | 'server'
  | 'unknown'

export class ApiError extends Error {
  readonly status: number
  readonly kind: ApiErrorKind
  readonly code: string
  readonly detail: unknown
  readonly maintenance: boolean

  constructor(options: {
    message: string
    status?: number
    kind?: ApiErrorKind
    code?: string
    detail?: unknown
    maintenance?: boolean
  }) {
    super(options.message)
    this.name = 'ApiError'
    this.status = options.status ?? 0
    this.kind = options.kind ?? 'unknown'
    this.code = options.code ?? ''
    this.detail = options.detail
    this.maintenance = options.maintenance ?? false
  }
}

/** 设备闸的结构化错误码：登录页据此切换到临时密码流程。 */
export const DEVICE_CODES = new Set([
  'DEVICE_REQUIRED',
  'DEVICE_BANNED',
  'DEVICE_NOT_PRIMARY',
  'TEMP_PASSWORD_INVALID',
  'DEVICE_CHALLENGE_MISMATCH'
])

export function kindOfStatus(status: number): ApiErrorKind {
  if (status === 401) return 'unauthorized'
  if (status === 403) return 'forbidden'
  if (status === 404) return 'not-found'
  if (status === 409) return 'conflict'
  if (status >= 500) return 'server'
  return 'unknown'
}

export function messageFromDetail(detail: unknown): string {
  if (typeof detail === 'string') return detail
  if (detail && typeof detail === 'object') {
    const record = detail as Record<string, unknown>
    if (typeof record.message === 'string' && record.message) return record.message
    if (typeof record.code === 'string' && record.code) return record.code
  }
  return ''
}
