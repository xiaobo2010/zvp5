import {
  AxiosError,
  AxiosHeaders,
  getAdapter,
  type AxiosAdapter,
  type AxiosResponse,
  type InternalAxiosRequestConfig
} from 'axios'
import { normalizeApiPath } from '@/utils/apiRouting'

/**
 * 只竞速首页、通知和活动/用户/分组详情等固定返回 JSON 的常用只读接口。
 * 列表接口在后端都有分页上限，不把文件、导出和流式接口纳入。
 */
const SMALL_JSON_GET_ROUTES: readonly RegExp[] = [
  /^\/(?:version|health|changelog|cert|meta|session)$/,
  /^\/logs$/,
  /^\/system\/(?:maintenance|role-policies(?:\/me)?)$/,
  /^\/v3\/notifications$/,
  /^\/(?:v2|v3)\/activities$/,
  /^\/v2\/activities\/[^/]+$/,
  /^\/v2\/activities\/[^/]+\/members(?:\/[^/]+)?$/,
  /^\/v3\/activities\/[^/]+\/members$/,
  /^\/v2\/statistics\/activities\/[^/]+\/analysis$/,
  /^\/v2\/users(?:\/[^/]+\/(?:activities|time|time_statistics))?$/,
  /^\/v2\/groups(?:\/[^/]+\/(?:activities|users))?$/,
  /^\/v3\/groups\/[^/]+\/users$/,
  /^\/v3\/users\/me\/(?:device|device-bindings)$/,
  /^(?:\/(?:v[23]))?\/users\/[^/]+$/,
  /^(?:\/(?:v[23]))?\/groups\/[^/]+$/,
  /^\/groups$/,
  /^\/v3\/ban\/(?:rules|triggers|users|devices|violations)$/,
  /^\/attachments$/,
  /^\/attachments\/[^/]+\/(?:meta|audit|ocr)$/,
  /^\/ai\/conversations$/,
  /^\/ai\/conversations\/[^/]+$/,
  /^\/ai\/runs(?:\/[^/]+)?$/,
  /^\/ai\/agent\/proposals$/,
  /^\/ai\/v1\/models$/
]

type HedgedConfig = InternalAxiosRequestConfig & {
  __zvmsHedgedGet?: boolean
}

function normalizedBaseURL(value: string): string {
  try {
    const base = typeof window === 'undefined' ? 'http://localhost' : window.location.origin
    return new URL(value, base).href.replace(/\/+$/, '')
  } catch {
    return value.replace(/\/+$/, '')
  }
}

function headerValue(config: InternalAxiosRequestConfig, name: string): string {
  const headers = config.headers
  const value = headers?.get?.(name) ?? headers?.[name]
  return typeof value === 'string' ? value : ''
}

export function isSmallJsonGetPath(value: unknown): boolean {
  const path = normalizeApiPath(value)
  return SMALL_JSON_GET_ROUTES.some((pattern) => pattern.test(path))
}

/**
 * 只竞速白名单内的小型 JSON GET：
 *
 * - 写请求、SSE、文件、Range 和显式流式响应保持单路；
 * - 主备基址相同时（本地开发和同源部署）自然关闭；
 * - 备用基址由部署期显式配置，不在源码里写生产域名。
 */
export function hedgedGetRelayBase(
  config: InternalAxiosRequestConfig,
  primaryBaseURL: string,
  relayBaseURL: string
): string | null {
  if (typeof window === 'undefined' || typeof fetch !== 'function') {
    return null
  }
  if (!relayBaseURL.trim()) {
    return null
  }
  if (String(config.method ?? '').toLowerCase() !== 'get' || config.data != null) {
    return null
  }
  if (!['', 'json', 'text'].includes(String(config.responseType ?? '').toLowerCase())) {
    return null
  }
  if (config.onDownloadProgress || headerValue(config, 'Range')) {
    return null
  }
  if (headerValue(config, 'Accept').toLowerCase().includes('text/event-stream')) {
    return null
  }

  const url = String(config.url ?? '')
  if (!url || /^([a-z][a-z\d+.-]*:)?\/\//i.test(url) || !isSmallJsonGetPath(url)) {
    return null
  }

  const primary = normalizedBaseURL(primaryBaseURL)
  const relay = normalizedBaseURL(relayBaseURL)
  return primary === relay ? null : relayBaseURL
}

function isDefinitiveClientError(error: unknown): boolean {
  if (!(error instanceof AxiosError) || !error.response) {
    return false
  }
  const status = error.response.status
  return status >= 400 && status < 500 && ![408, 425, 429].includes(status)
}

function responseAccepted(config: InternalAxiosRequestConfig, response: AxiosResponse): boolean {
  if (response.status >= 200 && response.status < 300) {
    return true
  }
  return config.validateStatus ? config.validateStatus(response.status) : false
}

/**
 * 两路低层 Axios adapter 同时启动；2xx 响应优先，先完整收到者获胜并中止另一条。
 * AI 请求会放开 validateStatus，因此非 2xx 也必须等待另一条，不能抢先获胜。
 */
export function createHedgedGetAdapter(
  primaryAdapter: AxiosAdapter,
  relayBaseURL: string
): AxiosAdapter {
  return (config) =>
    new Promise<AxiosResponse>((resolve, reject) => {
      const primaryController = new AbortController()
      const relayController = new AbortController()
      const primarySignal = config.signal
      let primaryError: unknown
      let relayError: unknown
      let primaryResponse: AxiosResponse | undefined
      let relayResponse: AxiosResponse | undefined
      let primaryDone = false
      let relayDone = false
      let settled = false

      const abortBoth = () => {
        primaryController.abort()
        relayController.abort()
      }
      const onExternalAbort = () => abortBoth()
      if (primarySignal?.aborted) {
        onExternalAbort()
      } else {
        primarySignal?.addEventListener?.('abort', onExternalAbort, { once: true })
      }

      const cleanup = () => {
        primarySignal?.removeEventListener?.('abort', onExternalAbort)
      }
      const succeed = (response: AxiosResponse, loser: AbortController) => {
        if (settled) return
        settled = true
        cleanup()
        loser.abort()
        response.config = config
        resolve(response)
      }
      const rejectWhenBothFailed = () => {
        if (settled || !primaryDone || !relayDone) return
        settled = true
        cleanup()
        const accepted = primaryResponse ?? relayResponse
        if (accepted && responseAccepted(config, accepted)) {
          accepted.config = config
          resolve(accepted)
          return
        }
        reject(
          primaryError ??
            relayError ??
            new AxiosError('Both API paths failed', AxiosError.ERR_NETWORK, config)
        )
      }

      const relayConfig: InternalAxiosRequestConfig = {
        ...config,
        baseURL: relayBaseURL,
        headers: AxiosHeaders.from(config.headers),
        signal: relayController.signal
      }
      const primaryConfig: InternalAxiosRequestConfig = {
        ...config,
        headers: AxiosHeaders.from(config.headers),
        signal: primaryController.signal
      }

      void Promise.resolve()
        .then(() => primaryAdapter(primaryConfig))
        .then((response) => {
          primaryDone = true
          primaryResponse = response
          if (response.status >= 200 && response.status < 300) {
            succeed(response, relayController)
          } else {
            rejectWhenBothFailed()
          }
        })
        .catch((error: unknown) => {
          primaryDone = true
          primaryError = error
          if (isDefinitiveClientError(error) && !settled) {
            settled = true
            cleanup()
            relayController.abort()
            reject(error)
            return
          }
          rejectWhenBothFailed()
        })

      void Promise.resolve()
        .then(() => primaryAdapter(relayConfig))
        .then((response) => {
          relayDone = true
          relayResponse = response
          if (response.status >= 200 && response.status < 300) {
            succeed(response, primaryController)
          } else {
            rejectWhenBothFailed()
          }
        })
        .catch((error: unknown) => {
          relayDone = true
          relayError = error
          rejectWhenBothFailed()
        })
    })
}

/**
 * 在请求拦截器里安装竞速 adapter。两路都走 Axios 的 fetch adapter，以便复用
 * 超时、鉴权头和状态码处理。
 */
export function installHedgedGetAdapter(
  config: InternalAxiosRequestConfig,
  relayBaseURL: string
): void {
  const raceConfig = config as HedgedConfig
  if (raceConfig.__zvmsHedgedGet) {
    return
  }
  const adapter = getAdapter(['fetch']) || getAdapter(config.adapter)
  if (!adapter) {
    return
  }
  raceConfig.__zvmsHedgedGet = true
  config.adapter = createHedgedGetAdapter(adapter, relayBaseURL)
}
