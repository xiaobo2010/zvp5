import axios, { AxiosError, type AxiosRequestConfig } from 'axios'
import { ApiError, kindOfStatus, messageFromDetail, DEVICE_CODES } from './errors'
import { readToken, writeToken } from '@/shared/lib/storage'

/**
 * HTTP 内核。
 *
 * 只做四件事：带令牌、把错误归一成 ApiError、把 401/维护态广播给应用层、
 * 保持请求头集合稳定。业务缓存/单飞/竞速属于后续性能阶段，不在内核里堆。
 */
export const API_BASE = (import.meta.env.VITE_API_BASE || '/api').replace(/\/+$/, '')

export const http = axios.create({
  baseURL: API_BASE,
  timeout: 15000,
  withCredentials: true,
  headers: { 'Content-Type': 'application/json' }
})

http.interceptors.request.use((config) => {
  const token = readToken()
  if (token && !config.headers.Authorization) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

export const AUTH_EXPIRED_EVENT = 'zvp5:auth-expired'
export const MAINTENANCE_EVENT = 'zvp5:maintenance'

function normalize(error: AxiosError): ApiError {
  if (!error.response) {
    return new ApiError({
      message: '网络连接失败，请检查网络后重试',
      kind: 'network',
      code: 'NETWORK'
    })
  }

  const { status, data, headers } = error.response
  const body = data as Record<string, unknown> | undefined
  const detail = body?.detail
  const maintenance = body?.maintenance === true
  const code =
    detail && typeof detail === 'object' && typeof (detail as Record<string, unknown>).code === 'string'
      ? String((detail as Record<string, unknown>).code)
      : typeof detail === 'string'
        ? detail
        : ''

  let kind = kindOfStatus(status)
  if (maintenance) kind = 'maintenance'
  if (status === 403 && detail === 'USER_BANNED') kind = 'banned'
  if (status === 403 && DEVICE_CODES.has(code)) kind = 'device'

  const message =
    messageFromDetail(detail) ||
    (status === 401 ? '登录已过期，请重新登录' : '') ||
    (status === 503 ? '服务暂时不可用' : '') ||
    error.message ||
    '请求失败'

  const banReason = headers?.['x-ban-reason']
  const apiError = new ApiError({ message, status, kind, code, detail, maintenance })
  if (typeof banReason === 'string' && banReason) {
    ;(apiError as ApiError & { banReason?: string }).banReason = banReason
  }
  return apiError
}

http.interceptors.response.use(
  (response) => response,
  (error: AxiosError) => {
    const apiError = normalize(error)

    const token = readToken()
    if (apiError.status === 401 && token && !error.config?.url?.startsWith('/v1/users/auth') &&
        error.config?.headers.get('Authorization') === `Bearer ${token}`) {
      writeToken('')
      window.dispatchEvent(new CustomEvent(AUTH_EXPIRED_EVENT))
    }
    if (apiError.maintenance) {
      window.dispatchEvent(new CustomEvent(MAINTENANCE_EVENT))
    }

    return Promise.reject(apiError)
  }
)

/** 泛型请求助手：返回 response.data。 */
export async function request<T>(config: AxiosRequestConfig): Promise<T> {
  const response = await http.request<T>(config)
  return response.data
}

/** 后端 envelope `{status,code,data}` 的拆包；非 envelope 原样返回。 */
export function unwrap<T>(payload: unknown): T {
  if (
    payload &&
    typeof payload === 'object' &&
    'data' in payload &&
    'status' in (payload as Record<string, unknown>)
  ) {
    return (payload as { data: T }).data
  }
  return payload as T
}

export default http
