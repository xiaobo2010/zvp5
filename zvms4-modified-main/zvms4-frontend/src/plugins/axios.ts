import axios, { AxiosError, getAdapter } from 'axios'
import type { AxiosAdapter, AxiosResponse, InternalAxiosRequestConfig } from 'axios'
import { ElMessage } from 'element-plus'
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
import { joinFlight } from '@/plugins/singleFlight'

const apiBase = import.meta.env.VITE_API_BASE ?? '/api'
const apiRaceBase = (import.meta.env.VITE_API_RACE_BASE ?? '').trim().replace(/\/+$/, '')

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

/**
 * 32 位 FNV-1a 短哈希：缓存键里只留登录凭证的指纹，绝不写 token 原文。
 * 重新登录会换 token，旧身份下的缓存自然不会再被命中。
 */
function hashToken(token: string): string {
  let hash = 0x811c9dc5
  for (let index = 0; index < token.length; index += 1) {
    hash ^= token.charCodeAt(index)
    hash = Math.imul(hash, 0x01000193)
  }
  return (hash >>> 0).toString(36)
}

/**
 * GET 缓存的身份维度：登录用户的响应也进缓存，但必须按身份隔离。
 *
 * - 凭证取请求头里的 Authorization（去掉 Bearer 前缀），没有则用请求拦截器
 *   即将注入的 localStorage token；两者归一化后是同一个值，读写键才能对上。
 * - 学海设备号会改变同一账号返回的数据（切学号/企业号），一并参与隔离。
 * - 未登录请求统一落到 anon，登录后不会读到匿名时期的缓存。
 */
function cacheIdentity(config: InternalAxiosRequestConfig): string {
  const authorization = config.headers?.get?.('Authorization') ?? config.headers?.Authorization
  const raw =
    typeof authorization === 'string' && authorization
      ? authorization
      : (localStorage.getItem('token') ?? '')
  const token = raw.replace(/^Bearer\s+/i, '')
  const account = token ? `t${hashToken(token)}` : 'anon'
  const xuehaiId = getXuehaiId()
  return xuehaiId > 0 ? `${account}:x${xuehaiId}` : account
}

function cacheKey(config: InternalAxiosRequestConfig) {
  const params = config.params ? JSON.stringify(config.params) : ''
  const suffix = config.data && config.method !== 'get' ? JSON.stringify(config.data) : ''
  return `${cacheIdentity(config)}|${config.baseURL ?? ''}${config.url ?? ''}${params}${suffix}`
}

/**
 * 航班键 = 身份维度 + 目标 URL + 解锁凭证状态。
 *
 * 解锁凭证必须进键：解锁前后同一个 URL 的请求头集合不同（后端对附件门禁的判定
 * 也不同），混进同一个航班会让「没带凭证」的请求拿到「带凭证」的响应。
 * 凭证本身只留指纹，键里不出现原文。
 */
function flightKey(config: InternalAxiosRequestConfig): string {
  const unlock = cachedUnlockToken()
  const unlockPart = unlock ? `u${hashToken(unlock)}` : 'u-'
  return `${cacheKey(config)}|${unlockPart}`
}

/**
 * 只有「同一时刻的相同 GET」才合并。写请求不可共享；调用方自带取消信号、
 * 进度回调、非 JSON/文本 响应一律不参与——合并它们会改变既有语义。
 */
function canSingleFlight(config: InternalAxiosRequestConfig, callerSignal: unknown): boolean {
  if (String(config.method ?? 'get').toLowerCase() !== 'get') {
    return false
  }
  if (callerSignal) {
    return false
  }
  if (config.onDownloadProgress || config.onUploadProgress) {
    return false
  }
  const responseType = String(config.responseType ?? 'json')
  return responseType === 'json' || responseType === 'text'
}

/**
 * 跟随者的 adapter：不发网络请求，等发起者那一次请求的结果。
 *
 * 数据给一份深拷贝，避免两个调用方互相污染；错误用跟随者自己的 config 重新抛出，
 * 错误拦截器据此识别跟随者并跳过重复处理。
 */
function followerAdapter(
  promise: Promise<AxiosResponse>,
  config: InternalAxiosRequestConfig
): AxiosAdapter {
  return async () => {
    try {
      const response = await promise
      return { ...response, data: cloneForCaller(response.data), config }
    } catch (error) {
      if (axios.isAxiosError(error)) {
        throw new AxiosError(error.message, error.code, config, error.request, error.response)
      }
      // 非 Axios 错误也包成 AxiosError：跟随者据此被识别为跟随者，
      // 不会重复弹窗。
      throw new AxiosError(
        error instanceof Error ? error.message : String(error),
        'ERR_BAD_RESPONSE',
        config
      )
    }
  }
}

/**
 * 发起者的 adapter：包一层，只为了兜住「非 Axios 错误」这一种收尾场景。
 *
 * 跟随者换掉了 axios 自带 adapter（连带丢掉请求超时），它们等的是航班的结果。
 * AxiosError 走的是错误拦截器（可重试的会重试，终态才结束航班），但如果 adapter
 * 抛出的是别的异常，错误拦截器拿不到 config，航班就永远不会结束，跟随者会一直
 *挂着。非 Axios 错误不会被重试，所以在这里直接结束航班。
 */
function leaderAdapter(
  adapter: AxiosAdapter,
  handle: { fail: (error: unknown) => void }
): AxiosAdapter {
  return async (config) => {
    try {
      return await adapter(config)
    } catch (error) {
      if (!axios.isAxiosError(error)) {
        handle.fail(error)
      }
      throw error
    }
  }
}

type CachedConfig = InternalAxiosRequestConfig & { __fromCache?: boolean }

/**
 * 在途合并（single-flight）的内部标记。
 *
 * - `__flightKey`：本次请求所属航班的键。重试沿用同一个键，否则重试会「加入」
 *   自己发起的航班并永远等下去。
 * - `__flightHandle`：发起者持有的句柄，最终结果由它广播给跟随者。
 * - `__singleFlightFollower`：跟随者标记。跟随者不重复弹窗、不重复跳登录、
 *   不重复重试——那些由发起者那一次请求统一处理。
 */
type FlightConfig = CachedConfig & {
  __flightKey?: string
  __flightHandle?: { settle: (value: AxiosResponse) => void; fail: (error: unknown) => void }
  __singleFlightFollower?: boolean
}

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
    // 调用方自带的取消信号要在 trackRequest 覆盖它之前取：trackRequest 会无条件
    // 给每个请求挂一个受控 signal，用它判断「调用方是否管取消」就永远为真。
    const callerSignal = config.signal
    // Resolve the few machine-local endpoints to the page/content host;
    // all other relative API paths retain the database-adjacent base.
    if (!/^([a-z][a-z\d+.-]*:)?\/\//i.test(String(config.url ?? ''))) {
      config.baseURL = baseURLForRequest(config.url)
    }
    // 命中进程内缓存：直接返回上次的响应，不再发请求
    const cached = cachedResponse(config)
    if (cached) {
      config.adapter = () => Promise.resolve(cached)
      return config
    }
    const token = localStorage.getItem('token')
    if (token && !config.headers['Authorization']) {
      config.headers['Authorization'] = `Bearer ${token}`
    }
    const xuehaiId = getXuehaiId()
    if (xuehaiId > 0 && !config.headers['Xuehai-Id']) {
      config.headers['Xuehai-Id'] = String(xuehaiId)
    }
    // 平板解锁凭证：无票据时也发一个空值头。
    //
    // 浏览器预检缓存的键里含**请求头集合**，一旦中途多出一个头（解锁后才有
    // X-Device-Unlock），已知的每个 URL 都会被重新预检一遍——平板解锁那一刻会
    // 集体重预检。请求头集合保持恒定就不会触发。
    //
    // 空值与不带等价：后端 `(request.headers.get("X-Device-Unlock") or "").strip()`
    // 取到空串，与请求头缺失是同一条分支。
    if (xuehaiId > 0 && !config.headers['X-Device-Unlock']) {
      config.headers['X-Device-Unlock'] = cachedUnlockToken()
    }
    trackRequest(config)
    if (!usesContentHost(config.url)) {
      const relayBase = hedgedGetRelayBase(config, String(config.baseURL ?? ''), apiRaceBase)
      if (relayBase) {
        installHedgedGetAdapter(config, relayBase)
      }
    }
    // 同一时刻的相同 GET 合并成一次网络往返。
    //
    // 跨域请求的 CORS 预检按完整 URL（含 query）与请求头集合分别缓存，冷缓存下
    // 并发 N 个相同请求会打 N 次预检（浏览器不做在途合并），每次预检又是一轮
    // 完整往返。合并后预检与真实请求都只剩一次。
    const flight = config as FlightConfig
    if (!flight.__flightKey && canSingleFlight(config, callerSignal)) {
      const key = flightKey(config)
      const handle = joinFlight<AxiosResponse>(key)
      flight.__flightKey = key
      if (handle.leader) {
        flight.__flightHandle = handle
        // config.adapter 可能是适配器名（'fetch'/'xhr'）而不是函数，先解析成函数。
        let adapter: AxiosAdapter | undefined
        try {
          adapter =
            typeof config.adapter === 'function' ? config.adapter : getAdapter(config.adapter)
        } catch {
          // 解析不了就交给 axios 自己报错，这里不改变原有失败路径。
          adapter = undefined
        }
        if (adapter) {
          config.adapter = leaderAdapter(adapter, handle)
        }
      } else {
        flight.__singleFlightFollower = true
        config.adapter = followerAdapter(handle.promise, config)
      }
    }
    return config
  },
  (error) => {
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
    const config = response.config as FlightConfig
    // 发起者的最终结果：广播给所有跟随者，并结束这个航班。
    config.__flightHandle?.settle(response)
    if (isCacheableGet(config)) {
      // 缓存命中的响应不再回写，避免同一条数据把有效期无限续上
      // 跟随者拿到的是同一份数据，也不必重复回写。
      if (!config.__fromCache && !config.__singleFlightFollower) {
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
    // 非 Axios 错误拿不到 config，航班收尾要用单独的引用兜住。
    const errorConfig = axios.isAxiosError(error)
      ? (error.config as FlightConfig | undefined)
      : undefined
    if (axios.isAxiosError(error)) {
      const config = error.config as FlightConfig | undefined
      releaseRequest(config)
      if (config?.__singleFlightFollower) {
        // 跟随者：错误已由发起者那一次请求判定，这里不再重复弹窗/跳登录/重试。
        return Promise.reject(error)
      }
      if (axios.isCancel(error) || config?.signal?.aborted) {
        config?.__flightHandle?.fail(error)
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
        config?.__flightHandle?.fail(error)
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
            config.__flightHandle?.settle(stale)
            return stale
          }
        }
        const retryConfig = config as TrackedRequestConfig
        if (!retryConfig.__retried) {
          retryConfig.__retried = true
          await backoff()
          // 航班保持 pending：重试后的结果才是终态，由响应拦截器收尾。
          return axiosInstance(config)
        }
      }
    }
    // 终态失败：结束航班，跟随者立即收到同样的错误，不留悬挂的在途请求。
    errorConfig?.__flightHandle?.fail(error)
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
