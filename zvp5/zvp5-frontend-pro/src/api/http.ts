// Shared request helpers for the standalone Pro app.
//
// The Pro client is intentionally dependency-free: this module keeps the same
// response conventions as the main frontend (legacy `{ status, data }`
// envelopes, `metadata.size` on list responses, Bearer auth) without pulling in
// axios.

export const API_BASE = (
  import.meta.env.VITE_API_BASE || 'https://zvp.xiaobopisces.top/api'
).replace(/\/+$/, '')

export class ApiError extends Error {
  readonly status: number
  readonly code: string

  constructor(message: string, status: number, code = '') {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
  }
}

function authToken(): string {
  return localStorage.getItem('token') ?? ''
}

export function currentUserId(): string {
  const value = authToken()
  if (!value) return ''
  try {
    const encoded = value.split('.')[1]
    if (!encoded) return ''
    const normalized = encoded.replace(/-/g, '+').replace(/_/g, '/')
    const padded = normalized + '='.repeat((4 - (normalized.length % 4)) % 4)
    const payload = JSON.parse(atob(padded)) as { sub?: unknown }
    return typeof payload.sub === 'string' ? payload.sub : ''
  } catch {
    return ''
  }
}

function isEnvelope(value: unknown): value is { status?: unknown; data?: unknown; message?: unknown; code?: unknown } {
  if (!value || typeof value !== 'object' || !('status' in value)) return false
  return value.status === 'error' ||
    ('data' in value && (value.status === 'ok' || value.status === 'success'))
}

function unwrap<T>(payload: unknown): T {
  if (isEnvelope(payload)) {
    if (payload.status === 'error') {
      const message = typeof payload.message === 'string' ? payload.message : '请求失败'
      const code = payload.code === undefined ? '' : String(payload.code)
      throw new ApiError(message, 400, code)
    }
    return payload.data as T
  }
  return payload as T
}

function errorMessage(payload: unknown, fallback: string): string {
  if (payload && typeof payload === 'object') {
    const body = payload as { detail?: unknown; message?: unknown; code?: unknown }
    if (typeof body.detail === 'string') return body.detail
    if (body.detail && typeof body.detail === 'object') {
      const detail = body.detail as { message?: unknown; code?: unknown }
      if (typeof detail.message === 'string') return detail.message
      if (typeof detail.code === 'string') return detail.code
    }
    if (typeof body.message === 'string') return body.message
    if (typeof body.code === 'string') return body.code
  }
  return fallback
}

function errorCode(payload: unknown): string {
  if (payload && typeof payload === 'object') {
    const body = payload as { code?: unknown; detail?: unknown }
    if (typeof body.code === 'string') return body.code
    if (typeof body.detail === 'string' && /^[A-Z0-9_]+$/.test(body.detail)) {
      return body.detail
    }
    if (body.detail && typeof body.detail === 'object') {
      const code = (body.detail as { code?: unknown }).code
      if (typeof code === 'string') return code
    }
  }
  return ''
}

export function buildQuery(params: Record<string, string | number | boolean | null | undefined>): string {
  const search = new URLSearchParams()
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== '') search.set(key, String(value))
  })
  const text = search.toString()
  return text ? `?${text}` : ''
}

async function requestRaw(path: string, init: RequestInit = {}): Promise<{ payload: unknown; response: Response }> {
  const headers = new Headers(init.headers)
  if (!headers.has('Content-Type') && init.body && !(init.body instanceof FormData)) {
    headers.set('Content-Type', 'application/json')
  }
  const value = authToken()
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
      if (value && headers.get('Authorization') === `Bearer ${value}` && authToken() === value) {
        localStorage.removeItem('token')
      }
      throw new ApiError('登录状态已失效，请重新登录。', 401, 'UNAUTHORIZED')
    }
    throw new ApiError(
      errorMessage(payload, `请求失败（${response.status}）`),
      response.status,
      errorCode(payload)
    )
  }

  return { payload, response }
}

export async function apiRequest<T>(path: string, init: RequestInit = {}): Promise<T> {
  const { payload } = await requestRaw(path, init)
  return unwrap<T>(payload)
}

export interface ListResult<T> {
  data: T
  size: number
}

export async function apiList<T>(path: string, init: RequestInit = {}): Promise<ListResult<T>> {
  const { payload } = await requestRaw(path, init)
  const data = unwrap<T>(payload)
  const metadata =
    payload && typeof payload === 'object' && 'metadata' in payload
      ? (payload as { metadata?: { size?: unknown } }).metadata
      : undefined
  const size = Number(metadata?.size)
  return { data, size: Number.isFinite(size) ? size : 0 }
}

export async function apiVoid(path: string, init: RequestInit = {}): Promise<void> {
  const { payload } = await requestRaw(path, init)
  unwrap<unknown>(payload)
}

export function jsonBody(value: unknown): RequestInit {
  return { method: 'POST', body: JSON.stringify(value) }
}

export function readableError(reason: unknown, fallback: string): string {
  return reason instanceof ApiError ? reason.message : fallback
}
