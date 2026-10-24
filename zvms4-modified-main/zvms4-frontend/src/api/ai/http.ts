import axios, { baseURL } from '@/plugins/axios'
import type { Response as AiEnvelope } from '@/../types'
import { AiRequestError, parseAiCode, parseAiMessage } from './errors'

/**
 * AI 网关的请求层。
 *
 * zvp 面（/ai/ocr、/ai/keys、/ai/quota、/ai/health）沿用仓库的 axios + envelope 约定；
 * OpenAI 面（/ai/v1/*）没有 envelope，错误体形状也不同，单独走 fetch。
 *
 * 这里刻意把 validateStatus 放开成「全部成功」：AI 的错误码要交给页面按字段
 * 分支（409 落名称字段、400 落表单），若交给 axios 响应拦截器统一弹 toast，
 * 用户会同时看到一条原始错误码和一条人话提示。拦截器仍负责 nprogress 与
 * 在飞请求计数，401 与维护模式跳转在本文件复刻。
 */

function readHeader(headers: unknown, name: string): string | null {
  if (!headers || typeof headers !== 'object') {
    return null
  }
  const record = headers as Record<string, unknown>
  const key = name.toLowerCase()
  const value = record[key]
  if (typeof value === 'string') {
    return value
  }
  return null
}

function readNumberHeader(headers: unknown, name: string): number | null {
  const raw = readHeader(headers, name)
  if (raw === null) {
    return null
  }
  const value = Number(raw)
  return Number.isFinite(value) ? value : null
}

function isMaintenance(body: unknown): boolean {
  if (!body || typeof body !== 'object') {
    return false
  }
  return (body as Record<string, unknown>).maintenance === true
}

async function redirectToMaintenance(): Promise<void> {
  try {
    const [{ useUserStore }, { default: router }] = await Promise.all([
      import('@/stores/user'),
      import('@/router')
    ])
    if (
      !useUserStore().position.includes('admin') &&
      router.currentRoute.value.path !== '/maintenance'
    ) {
      router.push('/maintenance')
    }
  } catch {
    // 跳转失败不阻塞错误提示
  }
}

async function handleUnauthorized(): Promise<void> {
  try {
    const [{ useUserStore }, { default: router }] = await Promise.all([
      import('@/stores/user'),
      import('@/router')
    ])
    localStorage.removeItem('token')
    await useUserStore().removeUser()
    router.replace('/user/login')
  } catch {
    localStorage.removeItem('token')
  }
}

export interface AiRequestConfig {
  method: 'get' | 'post' | 'put' | 'patch' | 'delete'
  url: string
  data?: unknown
}

/**
 * zvp 面请求：只判状态码，成功返回原始响应。
 */
async function request(config: AiRequestConfig) {
  const response = await axios({
    method: config.method,
    url: config.url,
    data: config.data,
    validateStatus: () => true
  })

  const status = response.status
  const body = response.data

  if (status === 401) {
    await handleUnauthorized()
    throw new AiRequestError({
      status,
      code: 'AI_UNAUTHORIZED',
      message: '登录已过期'
    })
  }

  if (status === 503 && isMaintenance(body)) {
    await redirectToMaintenance()
    throw new AiRequestError({
      status,
      code: 'AI_MAINTENANCE',
      message: '系统维护中'
    })
  }

  if (status < 200 || status >= 300) {
    throw new AiRequestError({
      status,
      code: parseAiCode(body) || `AI_HTTP_${status}`,
      message: parseAiMessage(body),
      requestId: readHeader(response.headers, 'X-AI-Request-Id'),
      retryAfter: readNumberHeader(response.headers, 'Retry-After'),
      quotaUsed: readNumberHeader(response.headers, 'X-AI-Quota-Used'),
      quotaLimit: readNumberHeader(response.headers, 'X-AI-Quota-Limit')
    })
  }

  return response
}

/**
 * zvp 面请求：成功返回 envelope 里的 data；失败抛 AiRequestError。
 */
export async function aiRequest<T>(config: AiRequestConfig): Promise<T> {
  const response = await request(config)
  const payload = response.data as AiEnvelope<T>
  if (!payload || payload.status !== 'ok') {
    throw new AiRequestError({
      status: response.status,
      code: parseAiCode(response.data) || 'AI_INVALID_RESPONSE',
      message: payload?.message ?? ''
    })
  }
  return payload.data
}

/**
 * 无响应体的请求（删除成功是 204）：只判状态码，不拆 envelope。
 */
export async function aiRequestVoid(config: AiRequestConfig): Promise<void> {
  await request(config)
}

/**
 * OpenAI 面请求：响应没有 envelope，成功即原始 JSON。
 */
export async function aiFetchJson<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await aiFetchRaw(path, init)
  let body: unknown = null
  try {
    body = await response.json()
  } catch {
    body = null
  }
  if (!response.ok) {
    throw aiResponseError(response, body)
  }
  return body as T
}

/**
 * OpenAI 面原始响应（流式用）：只做鉴权头与 401 / 维护模式处理，
 * 状态码判断交给调用方。
 */
export async function aiFetchRaw(path: string, init: RequestInit = {}): Promise<Response> {
  const token = localStorage.getItem('token')
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...((init.headers as Record<string, string>) ?? {})
  }
  if (token) {
    headers.Authorization = `Bearer ${token}`
  }

  const response = await fetch(`${baseURL}${path}`, { ...init, headers })

  if (response.status === 401) {
    await handleUnauthorized()
    throw new AiRequestError({
      status: 401,
      code: 'AI_UNAUTHORIZED',
      message: '登录已过期'
    })
  }

  return response
}

/** 非 2xx 响应 → AiRequestError（携带请求编号、退避与额度头）。 */
export function aiResponseError(response: Response, body: unknown): AiRequestError {
  return new AiRequestError({
    status: response.status,
    code: parseAiCode(body) || `AI_HTTP_${response.status}`,
    message: parseAiMessage(body),
    requestId: response.headers.get('X-AI-Request-Id'),
    retryAfter: readNumberHeader(response.headers, 'Retry-After'),
    quotaUsed: readNumberHeader(response.headers, 'X-AI-Quota-Used'),
    quotaLimit: readNumberHeader(response.headers, 'X-AI-Quota-Limit')
  })
}

/** 维护模式响应：返回 true 表示这是维护 503，需要跳维护页。 */
export async function isMaintenanceResponse(response: Response): Promise<boolean> {
  if (response.status !== 503) {
    return false
  }
  let body: unknown = null
  try {
    body = await response.clone().json()
  } catch {
    body = null
  }
  if (!isMaintenance(body)) {
    return false
  }
  await redirectToMaintenance()
  return true
}
