import axios, { AxiosError } from 'axios'
import type { AxiosResponse, InternalAxiosRequestConfig } from 'axios'
import { ElMessage } from 'element-plus'
import nprogress from 'nprogress'
import 'nprogress/nprogress.css'
import router from '@/router'
import { useUserStore } from '@/stores/user'
import { cacheGet, cacheSet } from '@/utils/idbCache'
import { getXuehaiId } from '@/plugins/ua'
import { DEVICE_LOCK_DETAIL, cachedUnlockToken, clearDeviceUnlock } from '@/plugins/device-unlock'
import { trackRequest, releaseRequest, type TrackedRequestConfig } from '@/plugins/inflight'
import { handleUserBanned } from '@/api/ai/banSignal'
import { resolveDetailMessage, resolveErrorMessage } from '@/utils/errorMessage'
import {
  memoryGet,
  memorySet,
  clearMemoryCache,
  cloneForCaller,
  ttlFor
} from '@/utils/requestCache'
import { usesContentHost } from '@/utils/apiRouting'
import { hedgedGetRelayBase, installHedgedGetAdapter } from '@/plugins/hedgedGet'

const apiBase = import.meta.env.VITE_API_BASE ?? '/api'

export const baseURL = apiBase.replace(/\/+$/, '')

/**
 * Most API calls deliberately use the database-adjacent API origin.  A small
 * number of endpoints belong to the content host instead.  Keep this base
 * derived from the page origin so the same build works for production, local
 * development, and an emergency page served by the API host.
 */
export const contentBaseURL =
  typeof window === 'undefined'
    ? '/api'
    : `${window.location.origin}/api`

/** Resolve the host by endpoint ownership, not by whether the URL starts with /api. */
export function baseURLForRequest(url: unknown): string {
  return usesContentHost(url) ? contentBaseURL : baseURL
}

const axiosInstance = axios.create({
  baseURL,
  withCredentials: true,
  timeout: 12000,
  headers: {
    'Content-type': 'application/json'
  }
})

/**
 * 封禁响应头解析已抽到 utils/ban.ts(登录页也要用),此处只负责写 store。
 */
function isCacheableGet(config?: InternalAxiosRequestConfig) {
  if (!config || config.method !== 'get') {
    return false
  }
  const responseType = config.responseType
  if (responseType !== undefined && responseType !== 'json') {
    return false
  }
  const authorization = config.headers?.get?.('Authorization') ?? config.headers?.Authorization
  if (authorization) {
    return false
  }
  const path = `${config.baseURL ?? ''}${config.url ?? ''}`.toLowerCase()
  // 设备状态（me/device、ban/devices）必须每次新鲜：缓存会把 403/404 的错误体
  // 也当数据回写，主设备绑定/解绑后的状态读取会被旧值挡住。
  return !(
    path.includes('/auth') ||
    path.includes('/totp') ||
    path.includes('/password') ||
    path.includes('/system/') ||
    path.includes('/device')
  )
}

function isIdempotentGet(config?: InternalAxiosRequestConfig) {
  return config?.method === 'get'
}

function cacheKey(config: InternalAxiosRequestConfig) {
  const params = config.params ? JSON.stringify(config.params) : ''
  const suffix = config.data && config.method !== 'get' ? JSON.stringify(config.data) : ''
  return `${config.baseURL ?? ''}${config.url ?? ''}${params}${suffix}`
}

type CachedConfig = InternalAxiosRequestConfig & { __fromCache?: boolean }

/**
 * 命中进程内缓存时构造一个等价的响应，并让本次请求跳过真实网络。
 * 这样重复进入页面/重复展开同一条数据时是同步可用的，不会再有加载卡顿。
 */
function cachedResponse(config: InternalAxiosRequestConfig): AxiosResponse | null {
  if (!isCacheableGet(config)) {
    return null
  }
  const key = cacheKey(config)
  const entry = memoryGet(key)
  if (!entry || Date.now() - entry.t > ttlFor(key)) {
    return null
  }
  ;(config as CachedConfig).__fromCache = true
  return {
    data: cloneForCaller(entry.data),
    status: 200,
    statusText: 'OK',
    headers: {},
    config,
    request: null
  }
}

const RETRYABLE_STATUS = new Set([502, 503, 504])
const RETRY_BACKOFF_MS = 300

function isRetryableFailure(config: InternalAxiosRequestConfig, error: AxiosError) {
  if (!isIdempotentGet(config)) {
    return false
  }
  const status = error.response?.status
  return error.response === undefined || (status !== undefined && RETRYABLE_STATUS.has(status))
}

function backoff() {
  return new Promise((resolve) => setTimeout(resolve, RETRY_BACKOFF_MS))
}

let lastStaleNotice = 0
function noticeStaleCache() {
  const now = Date.now()
  if (now - lastStaleNotice < 10000) {
    return
  }
  lastStaleNotice = now
  ElMessage({
    message: '网络不稳定，已显示本地缓存数据',
    type: 'warning',
    grouping: true,
    plain: true
  })
}

// Request Interceptor

axiosInstance.interceptors.request.use(
  (config) => {
    // Resolve the few machine-local endpoints to the page/content host;
    // all other relative API paths retain the database-adjacent base.
    if (!/^([a-z][a-z\d+.-]*:)?\/\//i.test(String(config.url ?? ''))) {
      config.baseURL = baseURLForRequest(config.url)
    }
    // 命中进程内缓存：直接返回上次的响应，不再发请求、不点亮进度条
    const cached = cachedResponse(config)
    if (cached) {
      config.adapter = () => Promise.resolve(cached)
      return config
    }
    nprogress.start()
    const token = localStorage.getItem('token')
    if (token && !config.headers['Authorization']) {
      config.headers['Authorization'] = `Bearer ${token}`
    }
    const xuehaiId = getXuehaiId()
    if (xuehaiId > 0 && !config.headers['Xuehai-Id']) {
      config.headers['Xuehai-Id'] = String(xuehaiId)
    }
    // 平板解锁后带上临时凭证：只认缓存里仍在 5 分钟窗口内的那张
    if (xuehaiId > 0 && !config.headers['X-Device-Unlock']) {
      const unlockToken = cachedUnlockToken()
      if (unlockToken) {
        config.headers['X-Device-Unlock'] = unlockToken
      }
    }
    trackRequest(config)
    if (!usesContentHost(config.url)) {
      const relayBase = hedgedGetRelayBase(config, String(config.baseURL ?? ''), contentBaseURL)
      if (relayBase) {
        installHedgedGetAdapter(config, relayBase)
      }
    }
    return config
  },
  (error) => {
    nprogress.done()
    return Promise.reject(error)
  }
)

/**
 * 登录设备闸的结构化 403 错误码（detail 是对象）。这批错误由登录页接管：
 * 页内要按 code 切到「临时密码」模式、或弹设备封禁说明；拦截器再弹一条通用
 * toast 只会把 JSON 原样甩给用户，和页内文案重复。
 */
const DEVICE_LOGIN_ERROR_CODES = new Set([
  'DEVICE_REQUIRED',
  'DEVICE_BANNED',
  'DEVICE_NOT_PRIMARY',
  'TEMP_PASSWORD_INVALID',
  'DEVICE_CHALLENGE_MISMATCH'
])

function isDeviceLoginDetail(detail: unknown): boolean {
  if (!detail || typeof detail !== 'object') {
    return false
  }
  const code = (detail as { code?: unknown }).code
  return typeof code === 'string' && DEVICE_LOGIN_ERROR_CODES.has(code)
}

// Response Interceptor

axiosInstance.interceptors.response.use(
  (response) => {
    releaseRequest(response.config)
    nprogress.done()
    const config = response.config as CachedConfig
    if (isCacheableGet(config)) {
      // 缓存命中的响应不再回写，避免同一条数据把有效期无限续上
      if (!config.__fromCache) {
        memorySet(cacheKey(config), response.data)
        void cacheSet(cacheKey(config), response.data)
      }
    } else if (config.method && config.method !== 'get') {
      // 任何写操作之后立即失效，下次读取拿到的是最新数据
      clearMemoryCache()
    }
    return response
  },
  async (error: Error | AxiosError) => {
    nprogress.done()
    if (axios.isAxiosError(error)) {
      const config = error.config
      releaseRequest(config)
      if (axios.isCancel(error) || config?.signal?.aborted) {
        return Promise.reject(error)
      }
      // 平板解锁凭证被后端否掉（过期/吊销/换了账号）：清掉本地缓存，
      // 下一次从入口重新弹密码框。这里不重试——自动重试会变成密码框风暴。
      if (
        error.response?.status === 403 &&
        (error.response?.data as { detail?: unknown } | undefined)?.detail ===
          DEVICE_LOCK_DETAIL
      ) {
        clearDeviceUnlock()
      }
      // 维护模式 503(响应体带 maintenance=true):跳维护页统一呈现。
      // 必须在重试/缓存回落之前拦截,避免维护期间对业务接口放大请求。
      // 注意:503 不代表登录态失效,不得走下方 401 的清 token / 跳登录分支。
      const maintenance503 =
        error.response?.status === 503 &&
        (error.response?.data as { maintenance?: boolean } | undefined)?.maintenance === true
      if (maintenance503) {
        if (
          !useUserStore().position.includes('admin') &&
          router.currentRoute.value.path !== '/maintenance'
        ) {
          router.push('/maintenance')
        }
        return Promise.reject(error)
      }
      if (config && isRetryableFailure(config, error)) {
        // Offline / server-unavailable: prefer local cache before retrying so the
        // two recovery paths never fire for the same failure.
        if (isCacheableGet(config)) {
          const cached = await cacheGet(cacheKey(config))
          if (cached) {
            noticeStaleCache()
            const stale: AxiosResponse = {
              data: cached.data,
              status: 200,
              statusText: 'OK',
              headers: { 'x-zvms-local-cache': String(cached.t) },
              config
            }
            return stale
          }
        }
        const retryConfig = config as TrackedRequestConfig
        if (!retryConfig.__retried) {
          retryConfig.__retried = true
          await backoff()
          return axiosInstance(config)
        }
      }
    }
    let errorDisplayed = false
    if (axios.isAxiosError(error)) {
      if (error.response?.status === 401) {
        if (!errorDisplayed) {
          errorDisplayed = true
          const token = localStorage.getItem('token')
          ElMessage({
            message: token ? '登录已过期' : '未登录',
            type: 'error',
            grouping: true,
            plain: true
          })
          if (token) {
            localStorage.removeItem('token')
          }
          useUserStore()
            .removeUser()
            .then(() => {
              router.replace('/user/login')
            })
        }
      } else if (
        error.response?.status === 403 &&
        error.response?.data?.detail === 'USER_BANNED'
      ) {
        // 封禁信号：写 ban 状态（供 App.vue 全屏遮罩倒计时）+ 中止所有在途 AI 请求。
        // 具体逻辑与 AI 面共用，见 api/ai/banSignal.ts；本函数保证不抛错，
        // 因此下方统一 reject 的行为不受影响。
        handleUserBanned({ headers: error.response?.headers })
      } else if (
        error.response?.status === 403 &&
        isDeviceLoginDetail(error.response?.data?.detail)
      ) {
        // 设备闸/临时密码登入失败：登录页按 detail.code 自行展示（临时密码引导、
        // 设备封禁说明、重试提示），这里静默 reject，不再叠一条通用 toast。
      } else {
        if (!errorDisplayed) {
          errorDisplayed = true
          let detail: unknown = error.response?.data?.detail
          const rawData = error.response?.data
          // 缩略图/字节类请求(responseType: 'blob' / 'arraybuffer')失败时 data 是二进制,
          // data.detail 取不到就只能走通用兜底。此处按需把二进制读成文本再尝试解析。
          if (rawData instanceof Blob || rawData instanceof ArrayBuffer) {
            try {
              const text =
                rawData instanceof Blob ? await rawData.text() : new TextDecoder().decode(rawData)
              if (text) {
                try {
                  const parsed = JSON.parse(text)
                  if (parsed && typeof parsed === 'object' && 'detail' in parsed) {
                    detail = (parsed as { detail?: unknown }).detail
                  }
                } catch {
                  if (text.length <= 300) {
                    detail = text
                  }
                }
              }
            } catch {
              detail = undefined
            }
          }
          ElMessage({
            message: resolveDetailMessage(detail) || resolveErrorMessage(error),
            type: 'error',
            grouping: true,
            plain: true
          })
        }
      }
    } else {
      if (!errorDisplayed) {
        errorDisplayed = true
        ElMessage({
          message: resolveErrorMessage(error),
          type: 'error',
          grouping: true,
          plain: true
        })
      }
    }
    // 统一出口:错误分支必须 reject,否则调用方拿到的 resolve 值是 undefined。
    return Promise.reject(error)
  }
)

export default axiosInstance
