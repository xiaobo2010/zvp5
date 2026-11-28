// Minimal ZVMS API client for the Astra-Alpha 3D theme.
//
// The theme speaks the same public API conventions as the other ZVMS themes
// (Bearer token in localStorage, legacy `{status, data}` envelopes, the Xuehai
// device header) without importing another frontend's modules.

function resolveApiBase(): string {
  const configured = import.meta.env.VITE_API_BASE
  const injected =
    typeof document === 'undefined'
      ? ''
      : document.querySelector<HTMLMetaElement>('meta[name="zvms-api-base"]')
          ?.content || ''
  const raw = configured || injected || 'https://zvp.xiaobopisces.top/api'
  // Theme builds can inject `/api`; resolve it against the theme origin so a
  // request under /themes/astra-alpha/ never becomes /themes/astra-alpha/api.
  if (raw.startsWith('/')) return `${window.location.origin}${raw}`
  return raw
}

const API_BASE = resolveApiBase().replace(/\/+$/, '')

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
let serverOffsetMs = 0

/**
 * The API responses carry a standard `Date` header. Keep the difference from
 * the local clock so the stage can show a server-time card that keeps ticking.
 */
function noteServerClock(response: Response): void {
  const header = response.headers.get('date')
  if (!header) return
  const parsed = Date.parse(header)
  if (Number.isFinite(parsed)) serverOffsetMs = parsed - Date.now()
}

export function serverTimeOffset(): number {
  return serverOffsetMs
}

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

interface RequestOptions {
  /** Explicit bearer token (password verification/change use a short token). */
  token?: string
  /** A rejected short token must not clear the long-lived session token. */
  keepSessionOn401?: boolean
}

export async function canaryRequest<T>(
  path: string,
  init: RequestInit = {},
  options: RequestOptions = {}
): Promise<T> {
  const headers = new Headers(init.headers)
  if (!headers.has('Content-Type') && init.body) {
    headers.set('Content-Type', 'application/json')
  }
  const value = options.token ?? token()
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

  noteServerClock(response)

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
      if (!options.keepSessionOn401) clearSession()
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

/* -------------------------------------------------------------------------- */
/* Account actions                                                             */
/* -------------------------------------------------------------------------- */

export interface CanaryDeviceInfo {
  xuehai_id: string | null
  primary_xuehai_id: string | null
  device_banned: boolean
  device_ban_reason: string | null
  device_ban_until: string | null
}

export async function fetchMyDevice(): Promise<CanaryDeviceInfo> {
  return canaryRequest<CanaryDeviceInfo>('/v1/users/me/device')
}

function base64ToBytes(value: string): Uint8Array<ArrayBuffer> {
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

async function encryptedCredential(password: string): Promise<string> {
  const publicKey = await canaryRequest<string>('/cert?type=public&method=RSA')
  return encryptCredential(publicKey, password)
}

/** Mirrors the backend's password strength rule (8–14 ASCII, 3 classes). */
export function validatePasswordStrength(password: string): boolean {
  if (password.length < 8 || password.length > 14) return false
  if (!/^[\x20-\x7e]+$/.test(password)) return false
  let classes = 0
  if (/[a-z]/.test(password)) classes += 1
  if (/[A-Z]/.test(password)) classes += 1
  if (/[0-9]/.test(password)) classes += 1
  if (/[^a-zA-Z0-9]/.test(password)) classes += 1
  return classes >= 3
}

/** Exchange the current password for the short-lived verify token. */
export async function verifyPassword(id: string, password: string): Promise<string> {
  const credential = await encryptedCredential(password)
  const result = await canaryRequest<{ token?: string }>(
    '/v1/users/auth',
    {
      method: 'POST',
      body: JSON.stringify({ id, credential, mode: 'short', purpose: 'verify' })
    },
    { keepSessionOn401: true }
  )
  if (!result?.token) throw new CanaryApiError('密码验证没有返回凭证。', 500)
  return result.token
}

export async function changePassword(
  userOid: string,
  newPassword: string,
  passwordToken: string
): Promise<void> {
  const credential = await encryptedCredential(newPassword)
  await canaryRequest<unknown>(
    `/v1/users/${encodeURIComponent(userOid)}/password`,
    {
      method: 'PUT',
      body: JSON.stringify({ credential })
    },
    { token: passwordToken, keepSessionOn401: true }
  )
}

/** The backend rotates the session on password change, so always drop it. */
export async function logout(): Promise<void> {
  const value = token()
  if (value) {
    try {
      await canaryRequest<unknown>(
        '/v1/users/auth/logout',
        { method: 'POST' },
        { token: value, keepSessionOn401: true }
      )
    } catch {
      // Expired/invalid tokens still get dropped locally.
    }
  }
  clearSession()
}

export async function unbindPrimaryDevice(verificationToken: string): Promise<void> {
  await canaryRequest<unknown>('/v1/users/me/primary-device', {
    method: 'DELETE',
    body: JSON.stringify({ verification_token: verificationToken })
  })
}
