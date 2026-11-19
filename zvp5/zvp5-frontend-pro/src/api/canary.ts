// Minimal canary API client for the standalone Pro frontend.
// The Pro app deliberately keeps its dependency surface small, so this uses
// the browser fetch API instead of the canary2 axios stack.

const API_BASE = (
  import.meta.env.VITE_API_BASE || 'https://zvp.xiaobopisces.top/api'
).replace(/\/+$/, '')

export class CanaryApiError extends Error {
  readonly status: number

  constructor(message: string, status: number) {
    super(message)
    this.name = 'CanaryApiError'
    this.status = status
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

export interface CanaryUser {
  _id: string
  id: number | string
  name: string
  group?: string[]
  roles?: UserPosition[]
  class_id?: string
  banned?: boolean
}

export interface CanaryGroup {
  _id: string
  name: string
  type?: 'class' | 'permission' | 'group' | 'club'
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

export interface CanaryLoginResult {
  token: string
  _id: string
  weak_password?: boolean
  profile?: {
    _id: string
    id: string
    name: string
    roles: UserPosition[]
    class_id: string
    group?: string[]
  }
}

interface LegacyResponse<T> {
  status: 'ok' | 'error'
  data: T
  message?: string
  code?: number | string
}

function token(): string {
  return localStorage.getItem('token') ?? ''
}

// Decoding the JWT is pure and repeated by every request/auth check. Cache the
// last payload so navigation does not re-base64-decode the same token.
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
    const legacy = payload as LegacyResponse<T>
    if (legacy.status === 'error') {
      throw new CanaryApiError(legacy.message || '请求失败', 400)
    }
    return legacy.data
  }
  return payload as T
}

function errorMessage(payload: unknown, fallback: string): string {
  if (payload && typeof payload === 'object') {
    const body = payload as { detail?: unknown; message?: unknown }
    if (typeof body.detail === 'string') return body.detail
    if (body.detail && typeof body.detail === 'object') {
      const detail = body.detail as { message?: unknown }
      if (typeof detail.message === 'string') return detail.message
    }
    if (typeof body.message === 'string') return body.message
  }
  return fallback
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers)
  if (!headers.has('Content-Type') && init.body) {
    headers.set('Content-Type', 'application/json')
  }
  const value = token()
  if (value && !headers.has('Authorization')) {
    headers.set('Authorization', `Bearer ${value}`)
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
    throw new CanaryApiError(
      errorMessage(payload, `请求失败（${response.status}）`),
      response.status
    )
  }

  return unwrap<T>(payload)
}

function query(params: Record<string, string | number | boolean | undefined>): string {
  const search = new URLSearchParams()
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== '') search.set(key, String(value))
  })
  const text = search.toString()
  return text ? `?${text}` : ''
}

export async function fetchProfile(): Promise<CanaryUser> {
  const id = currentUserId()
  if (!id) throw new CanaryApiError('请先登录 canary 账号。', 401)
  return request<CanaryUser>(`/v1/users/${id}`)
}

const groupNameCache = new Map<string, Promise<string>>()

export function fetchGroupName(groupId: string): Promise<string> {
  if (!groupId) return Promise.resolve('')
  const cached = groupNameCache.get(groupId)
  if (cached) return cached
  const pending = request<CanaryGroup>(`/groups/${groupId}`)
    .then((group) => group?.name ?? '')
    .catch(() => '')
  groupNameCache.set(groupId, pending)
  return pending
}

export async function fetchNotices(): Promise<CanaryNoticeList> {
  return request<CanaryNoticeList>('/v1/notifications')
}

export async function fetchVersion(): Promise<string> {
  const value = await request<string | { version?: string }>('/version')
  if (typeof value === 'string') return value
  return value?.version ?? ''
}

export async function fetchTime(): Promise<CanaryTime> {
  const id = currentUserId()
  if (!id) throw new CanaryApiError('请先登录 canary 账号。', 401)
  return request<CanaryTime>(`/v1/users/${id}/time`)
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
    return request<CanaryActivityPage>(
      `/v1/users/${id}/activities${query({
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
    return request<CanaryActivityPage>(
      `/v1/groups/${classId}/activities${query({
        page,
        perpage: size,
        search,
        classid: classId,
        sort: '_id',
        asc: false
      })}`
    )
  }

  return request<CanaryActivityPage>(
    `/v1/activities${query({
      page,
      size,
      query: search,
      type: '',
      status: ''
    })}`
  )
}

/* -------------------------------------------------------------------------- */
/* Login                                                                       */
/* -------------------------------------------------------------------------- */

function base64ToBytes(value: string): Uint8Array {
  const normalized = value.replace(/-/g, '+').replace(/_/g, '/')
  const padded = normalized + '='.repeat((4 - (normalized.length % 4)) % 4)
  const binary = atob(padded)
  const bytes = new Uint8Array(binary.length)
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i)
  return bytes
}

function bytesToHex(bytes: Uint8Array): string {
  return Array.from(bytes)
    .map((byte) => byte.toString(16).padStart(2, '0'))
    .join('')
}

async function encryptCredential(publicKey: string, password: string): Promise<string> {
  const pem = publicKey
    .replace('-----BEGIN PUBLIC KEY-----', '')
    .replace('-----END PUBLIC KEY-----', '')
    .split(/\s+/)
    .join('')
  const key = await crypto.subtle.importKey(
    'spki',
    base64ToBytes(pem),
    { name: 'RSA-OAEP', hash: 'SHA-256' },
    true,
    ['encrypt']
  )
  const payload = new TextEncoder().encode(
    JSON.stringify({ password, time: Date.now() })
  )
  const encrypted = await crypto.subtle.encrypt({ name: 'RSA-OAEP' }, key, payload)
  return bytesToHex(new Uint8Array(encrypted))
}

export async function login(id: string, password: string): Promise<CanaryLoginResult> {
  const publicKey = await request<string>('/cert?type=public&method=RSA')
  const credential = await encryptCredential(publicKey, password)
  const result = await request<CanaryLoginResult>('/users/auth', {
    method: 'POST',
    body: JSON.stringify({
      id,
      credential,
      mode: 'long',
      purpose: 'login'
    })
  })
  if (!result?.token) throw new CanaryApiError('登录没有返回凭证。', 500)
  localStorage.setItem('token', result.token)
  return result
}
