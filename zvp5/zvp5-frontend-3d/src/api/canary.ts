// Minimal canary API client for the 3D concept app.
//
// The concept reuses the exact request conventions of zvp5-frontend-pro (Bearer
// token in localStorage, legacy `{status, data}` envelopes, the Xuehai device
// header) without importing across package boundaries. Only the read endpoints
// the stage needs are implemented here.

const API_BASE = (
  import.meta.env.VITE_API_BASE || 'https://zvp.xiaobopisces.top/api'
).replace(/\/+$/, '')

export class CanaryApiError extends Error {
  readonly status: number
  readonly code: string

  constructor(message: string, status: number, code = '') {
    super(message)
    this.name = 'CanaryApiError'
    this.status = status
    this.code = code
  }
}

export type UserPosition =
  | 'system'
  | 'admin'
  | 'auditor'
  | 'department'
  | 'secretary'
  | 'student'
  | 'teacher'
  | 'volunteer'

export interface CanaryUser {
  _id: string
  id: number | string
  name: string
  group?: string[]
  roles?: UserPosition[]
  class_id?: string
  banned?: boolean
}

export interface CanaryActivity {
  _id: string
  name: string
  type: 'on-campus' | 'off-campus' | 'social-practice' | 'hybrid'
  date: string
  place: string
  status: 'pending' | 'effective' | 'refused'
  creator_name?: string
  mine?: {
    _id: string
    status: 'effective' | 'refused' | 'pending'
    mode: 'on-campus' | 'off-campus' | 'social-practice'
    duration: number
  }
}

export interface CanaryActivityPage {
  activities: CanaryActivity[]
  total: number
}

export interface CanaryTime {
  'on-campus': number
  'off-campus': number
  'social-practice': number
}

export interface CanaryNotice {
  id: string
  title: string
  content: string
  author_name: string
  created_at: string | null
  active: boolean
}

export interface CanaryNoticeList {
  version: number
  notifications: CanaryNotice[]
}

function token(): string {
  return localStorage.getItem('token') ?? ''
}

/**
 * Xuehai device identity, mirrored from the canary client: the native browser
 * bridge exposes the raw user id; the data-center App only has it in the UA.
 * The backend uses this header as the device subject for login/device guards.
 */
function xuehaiId(): string {
  const bridge = (
    window as unknown as { xhBrowserJava?: { getUserId?: () => unknown } }
  ).xhBrowserJava
  const raw = bridge?.getUserId?.()
  const id = Number(raw)
  if (Number.isFinite(id) && id > 0) return String(id)
  const match = /userId\/(\d+)/.exec(navigator.userAgent)
  return match?.[1] ?? ''
}

let cachedToken = ''
let cachedPayload: Record<string, unknown> | null = null

function decodeTokenPayload(value: string): Record<string, unknown> | null {
  if (value === cachedToken) return cachedPayload
  try {
    const encoded = value.split('.')[1]
    if (!encoded) return null
    const normalized = encoded.replace(/-/g, '+').replace(/_/g, '/')
    const padded = normalized + '='.repeat((4 - (normalized.length % 4)) % 4)
    const json = decodeURIComponent(
      Array.from(atob(padded))
        .map((char) => '%' + char.charCodeAt(0).toString(16).padStart(2, '0'))
        .join('')
    )
    cachedToken = value
    cachedPayload = JSON.parse(json) as Record<string, unknown>
    return cachedPayload
  } catch {
    cachedToken = value
    cachedPayload = null
    return null
  }
}

export function isLoggedIn(): boolean {
  const value = token()
  if (!value) return false
  const payload = decodeTokenPayload(value)
  if (!payload) return false
  // Verify/short-lived tokens must never pass: they are not access tokens.
  if (payload.scope !== 'access_token') return false
  const exp = typeof payload.exp === 'number' ? payload.exp : 0
  return exp === 0 || exp * 1000 > Date.now()
}

export function currentUserId(): string {
  const value = token()
  if (!value) return ''
  const payload = decodeTokenPayload(value)
  return typeof payload?.sub === 'string' ? payload.sub : ''
}

export function clearSession(): void {
  localStorage.removeItem('token')
  cachedToken = ''
  cachedPayload = null
}

function unwrap<T>(payload: unknown): T {
  if (payload && typeof payload === 'object' && 'status' in payload) {
    const legacy = payload as {
      status?: unknown
      data?: unknown
      message?: unknown
      code?: unknown
    }
    if (legacy.status === 'error') {
      const message = typeof legacy.message === 'string' ? legacy.message : '请求失败'
      const code = typeof legacy.code === 'string' ? legacy.code : ''
      throw new CanaryApiError(message, 400, code)
    }
    if ((legacy.status === 'ok' || legacy.status === 'success') && 'data' in legacy) {
      return legacy.data as T
    }
  }
  return payload as T
}

function errorMeta(payload: unknown, fallback: string): { message: string; code: string } {
  const meta = { message: fallback, code: '' }
  if (!payload || typeof payload !== 'object') return meta
  const body = payload as { detail?: unknown; message?: unknown; code?: unknown }
  if (typeof body.detail === 'string') {
    meta.message = body.detail
  } else if (body.detail && typeof body.detail === 'object') {
    const detail = body.detail as { message?: unknown; code?: unknown }
    if (typeof detail.message === 'string') meta.message = detail.message
    if (typeof detail.code === 'string') meta.code = detail.code
  }
  if (typeof body.message === 'string') meta.message = body.message
  if (typeof body.code === 'string') meta.code = body.code
  return meta
}

export async function canaryRequest<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers)
  if (!headers.has('Content-Type') && init.body) {
    headers.set('Content-Type', 'application/json')
  }
  const value = token()
  if (value && !headers.has('Authorization')) {
    headers.set('Authorization', `Bearer ${value}`)
  }
  const device = xuehaiId()
  if (device && !headers.has('Xuehai-Id')) {
    headers.set('Xuehai-Id', device)
  }

  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers,
    credentials: 'include'
  })

  let payload: unknown = null
  const text = await response.text()
  if (text) {
    try {
      payload = JSON.parse(text)
    } catch {
      payload = text
    }
  }

  if (!response.ok) {
    if (response.status === 401) {
      clearSession()
      throw new CanaryApiError('登录状态已失效，请重新登录。', 401)
    }
    const meta = errorMeta(payload, `请求失败（${response.status}）`)
    throw new CanaryApiError(meta.message, response.status, meta.code)
  }

  return unwrap<T>(payload)
}

export function buildQuery(
  params: Record<string, string | number | boolean | null | undefined>
): string {
  const search = new URLSearchParams()
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== '') search.set(key, String(value))
  })
  const text = search.toString()
  return text ? `?${text}` : ''
}

export function readableError(reason: unknown, fallback: string): string {
  return reason instanceof CanaryApiError ? reason.message : fallback
}

export function isAuthError(reason: unknown): boolean {
  return reason instanceof CanaryApiError && reason.status === 401
}

export async function fetchProfile(): Promise<CanaryUser> {
  const id = currentUserId()
  if (!id) throw new CanaryApiError('请先登录 canary 账号。', 401)
  return canaryRequest<CanaryUser>(`/v1/users/${encodeURIComponent(id)}`)
}

export async function fetchTime(): Promise<CanaryTime> {
  const id = currentUserId()
  if (!id) throw new CanaryApiError('请先登录 canary 账号。', 401)
  return canaryRequest<CanaryTime>(`/v1/users/${encodeURIComponent(id)}/time`)
}

export type ActivityScope = 'mine' | 'class' | 'campus'

export async function fetchActivities(
  scope: ActivityScope,
  page: number,
  size: number,
  search: string,
  classId = ''
): Promise<CanaryActivityPage> {
  const id = currentUserId()
  if (!id) throw new CanaryApiError('请先登录 canary 账号。', 401)

  if (scope === 'mine') {
    return canaryRequest<CanaryActivityPage>(
      `/v1/users/${encodeURIComponent(id)}/activities${buildQuery({
        page,
        perpage: size,
        search,
        sort: '_id',
        asc: false
      })}`
    )
  }

  if (scope === 'class') {
    if (!classId) return { activities: [], total: 0 }
    return canaryRequest<CanaryActivityPage>(
      `/v1/groups/${encodeURIComponent(classId)}/activities${buildQuery({
        page,
        perpage: size,
        search,
        classid: classId,
        sort: '_id',
        asc: false
      })}`
    )
  }

  return canaryRequest<CanaryActivityPage>(
    `/v1/activities${buildQuery({ page, size, query: search, sort: '_id', asc: false })}`
  )
}

export async function fetchNotices(): Promise<CanaryNoticeList> {
  return canaryRequest<CanaryNoticeList>('/v1/notifications')
}

const groupNameCache = new Map<string, Promise<string>>()

export function fetchGroupName(groupId: string): Promise<string> {
  if (!groupId) return Promise.resolve('')
  const cached = groupNameCache.get(groupId)
  if (cached) return cached
  const pending = canaryRequest<{ name?: string }>(`/v1/groups/${encodeURIComponent(groupId)}`)
    .then((group) => group?.name ?? '')
    .catch(() => '')
  groupNameCache.set(groupId, pending)
  return pending
}
