// Minimal canary API client for the standalone Pro frontend.
// The Pro app deliberately keeps its dependency surface small, so this uses
// the browser fetch API instead of the canary2 axios stack.

const API_BASE = (
  import.meta.env.VITE_API_BASE || 'https://zvp.xiaobopisces.top/api'
).replace(/\/+$/, '')

export class CanaryApiError extends Error {
  readonly status: number
  readonly code: string
  readonly challenge: string

  constructor(message: string, status: number, code = '', challenge = '') {
    super(message)
    this.name = 'CanaryApiError'
    this.status = status
    this.code = code
    this.challenge = challenge
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
  binding_mismatch?: boolean
  profile?: {
    _id: string
    id: string
    name: string
    roles: UserPosition[]
    class_id: string
    group?: string[]
  }
}

function token(): string {
  return localStorage.getItem('token') ?? ''
}

/**
 * Xuehai device identity, mirrored from the canary client: the native browser
 * bridge exposes the raw user id; the data-center App only has it in the UA.
 * The backend uses this header as the device subject for login/device guards,
 * so it must accompany every request (browser tabs without either source stay
 * header-less, which the backend treats as a normal desktop client).
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
  // Verify/short-lived tokens (weak-password login, password re-verification)
  // must never pass the route guard: they are not access tokens.
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
    // Only `{status: ok|success, data}` is a legacy success envelope. A bare
    // object that merely happens to have a `status` field (an activity row, a
    // health/meta payload, `{status: 'ok'}` without data) must pass through
    // unchanged instead of being flattened to `undefined`.
    if (
      (legacy.status === 'ok' || legacy.status === 'success') &&
      'data' in legacy
    ) {
      return legacy.data as T
    }
  }
  return payload as T
}

interface ErrorMeta {
  message: string
  code: string
  challenge: string
}

function errorMeta(payload: unknown, fallback: string): ErrorMeta {
  const meta: ErrorMeta = { message: fallback, code: '', challenge: '' }
  if (!payload || typeof payload !== 'object') return meta
  const body = payload as { detail?: unknown; message?: unknown; code?: unknown }
  if (typeof body.detail === 'string') {
    meta.message = body.detail
  } else if (body.detail && typeof body.detail === 'object') {
    const detail = body.detail as { message?: unknown; code?: unknown; challenge?: unknown }
    if (typeof detail.message === 'string') meta.message = detail.message
    if (typeof detail.code === 'string') meta.code = detail.code
    if (typeof detail.challenge === 'string') meta.challenge = detail.challenge
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

async function request<T>(
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
    throw new CanaryApiError(meta.message, response.status, meta.code, meta.challenge)
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
  const pending = request<CanaryGroup>(`/v1/groups/${groupId}`)
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

export async function fetchHealth(): Promise<string> {
  const value = await request<string>('/health')
  return typeof value === 'string' ? value : 'OK'
}

export interface CreateActivityInput {
  name: string
  description: string
  type: 'on-campus' | 'off-campus' | 'social-practice' | 'hybrid'
  /** YYYY-MM-DD or an ISO timestamp. Defaults to now when omitted. */
  date?: string
  place?: string
}

export async function createActivity(input: CreateActivityInput): Promise<string> {
  // `/api/v1/activities` 同时挂着 v2 与 v3 路由，v2 的 POST 先注册；
  // 因此这里按 v2 的完整 Activity 契约提交，避免最小 v3 请求体被 v2 校验拒绝。
  const now = new Date().toISOString()
  const user = currentUserId()
  const date =
    input.date && /^\d{4}-\d{2}-\d{2}$/.test(input.date)
      ? `${input.date}T00:00:00`
      : input.date || now
  const result = await request<{ id?: string }>('/v1/activities', {
    method: 'POST',
    body: JSON.stringify({
      _id: '000000000000000000000000',
      type: input.type,
      name: input.name,
      description: input.description,
      date,
      createdAt: now,
      updatedAt: now,
      appointee: user,
      approver: 'authority',
      creator: user,
      status: 'pending',
      place: input.place?.trim() ?? '',
      origin: 'other'
    })
  })
  if (!result?.id) throw new CanaryApiError('活动创建成功但未返回 ID。', 500)
  return result.id
}

export interface CanaryAiHealth {
  enabled: boolean
  max_concurrency: number
  worker_count: number
  rate_limit_per_minute: Record<string, number>
  models: string[]
  providers?: unknown
  credentials?: {
    personal?: number
    shared?: number
    server?: number
    shared_entries?: Array<{ name?: string; base_url?: string; models?: string[]; enabled?: boolean }>
  }
}

export async function fetchAiHealth(): Promise<CanaryAiHealth> {
  return request<CanaryAiHealth>('/ai/health')
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
      sort: '_id',
      asc: false
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

/**
 * Mirror of the backend `pwd_strength` rule: 8-14 printable ASCII characters
 * with at least three of lower/upper/digit/symbol. The backend treats anything
 * weaker as a forced-password-change credential, so the UI must never submit it
 * as a final password.
 */
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

async function encryptedCredential(password: string): Promise<string> {
  const publicKey = await request<string>('/cert?type=public&method=RSA')
  return encryptCredential(publicKey, password)
}

export async function login(id: string, password: string): Promise<CanaryLoginResult> {
  const credential = await encryptedCredential(password)
  const result = await request<CanaryLoginResult>('/v1/users/auth', {
    method: 'POST',
    body: JSON.stringify({
      id,
      credential,
      mode: 'long',
      purpose: 'login'
    })
  })
  if (!result?.token) throw new CanaryApiError('登录没有返回凭证。', 500)
  if (result.weak_password) {
    // Weak passwords mint a 5-minute password_change token, not an access
    // token. Keep it in memory for the forced-change step and never arm the
    // route guard with it.
    clearSession()
  } else {
    localStorage.setItem('token', result.token)
  }
  return result
}

/**
 * Device challenge exchange (`POST /api/v1/users/auth/device`): the challenge is
 * minted by a 403 `DEVICE_NOT_PRIMARY` login, the 8-digit temp password is
 * issued by a secretary/admin. Both are one-time; the resulting session lasts
 * one hour and is a real access token.
 */
export async function loginWithTempPassword(
  challenge: string,
  tempPassword: string
): Promise<CanaryLoginResult> {
  const result = await request<CanaryLoginResult>('/v1/users/auth/device', {
    method: 'POST',
    body: JSON.stringify({
      challenge,
      temp_password: tempPassword
    })
  })
  if (!result?.token) throw new CanaryApiError('临时密码登录没有返回凭证。', 500)
  localStorage.setItem('token', result.token)
  return result
}

/**
 * Exchange the current password for a 5-minute `purpose=verify` token used by
 * `PUT /v1/users/{oid}/password`. Does not touch the stored session.
 */
export async function verifyPassword(id: string, password: string): Promise<string> {
  const credential = await encryptedCredential(password)
  const result = await request<CanaryLoginResult>(
    '/v1/users/auth',
    {
      method: 'POST',
      body: JSON.stringify({
        id,
        credential,
        mode: 'short',
        purpose: 'verify'
      })
    },
    { keepSessionOn401: true }
  )
  if (!result?.token) throw new CanaryApiError('密码验证没有返回凭证。', 500)
  return result.token
}

/**
 * `PUT /api/v1/users/{oid}/password` with an explicit temporary token (either a
 * verify token or the password_change token returned by a weak login).
 */
export async function changePassword(
  userOid: string,
  newPassword: string,
  passwordToken: string
): Promise<void> {
  const credential = await encryptedCredential(newPassword)
  await request<unknown>(
    `/v1/users/${encodeURIComponent(userOid)}/password`,
    {
      method: 'PUT',
      body: JSON.stringify({ credential })
    },
    { token: passwordToken, keepSessionOn401: true }
  )
}

/** Rotate the server-side session id, then always drop the local token. */
export async function logout(): Promise<void> {
  const value = token()
  if (value) {
    try {
      await request<unknown>(
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
