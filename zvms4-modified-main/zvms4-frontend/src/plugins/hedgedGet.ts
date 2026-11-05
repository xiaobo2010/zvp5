import {
  AxiosError,
  AxiosHeaders,
  getAdapter,
  type AxiosAdapter,
  type AxiosResponse,
  type InternalAxiosRequestConfig
} from 'axios'

/**
 * 小型只读请求允许经 LA 再竞速一次。这个上限只约束备用线路：超过它的响应
 * 仍由首尔主线路完整接收，避免为了兜底而把大文件复制两份。
 */
export const SMALL_GET_RACE_LIMIT_BYTES = 256 * 1024

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

/**
 * 只竞速普通的小型 JSON/text GET：
 *
 * - 写请求、SSE、文件、Range 和显式流式响应保持单路；
 * - 主备基址相同时（本地开发和同源部署）自然关闭；
 * - 页面源/api 是备用线路，生产环境即 LA 上的 /api 反代。
 */
export function hedgedGetRelayBase(
  config: InternalAxiosRequestConfig,
  primaryBaseURL: string,
  relayBaseURL: string
): string | null {
  if (typeof window === 'undefined' || typeof fetch !== 'function') {
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
  if (!url || /^([a-z][a-z\d+.-]*:)?\/\//i.test(url)) {
    return null
  }

  const primary = normalizedBaseURL(primaryBaseURL)
  const relay = normalizedBaseURL(relayBaseURL)
  return primary === relay ? null : relayBaseURL
}

function relayMaxContentLength(config: InternalAxiosRequestConfig): number {
  const configured = config.maxContentLength
  return typeof configured === 'number' && configured >= 0
    ? Math.min(configured, SMALL_GET_RACE_LIMIT_BYTES)
    : SMALL_GET_RACE_LIMIT_BYTES
}

function responseBytes(response: AxiosResponse): number | null {
  const headers = response.headers as unknown as {
    get?: (name: string) => unknown
    [name: string]: unknown
  }
  const declared =
    typeof headers.get === 'function' ? headers.get('content-length') : headers['content-length']
  const declaredNumber = Number(declared)
  if (Number.isFinite(declaredNumber) && declaredNumber >= 0) {
    return declaredNumber
  }

  const data = response.data
  if (typeof data === 'string') {
    return typeof TextEncoder === 'function'
      ? new TextEncoder().encode(data).byteLength
      : data.length
  }
  if (data instanceof ArrayBuffer) {
    return data.byteLength
  }
  if (ArrayBuffer.isView(data)) {
    return data.byteLength
  }
  if (typeof Blob !== 'undefined' && data instanceof Blob) {
    return data.size
  }
  if (data != null && typeof data === 'object') {
    try {
      return new TextEncoder().encode(JSON.stringify(data)).byteLength
    } catch {
      return null
    }
  }
  return null
}

/**
 * 两路低层 Axios adapter 同时启动；两路都完整收到后才算完成，第一条成功响应
 * 获胜并中止另一条。若一路失败，另一条仍可完成；两路都失败时保留首尔错误。
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
        signal: relayController.signal,
        maxContentLength: relayMaxContentLength(config),
        onDownloadProgress: (event) => {
          if (event.loaded > SMALL_GET_RACE_LIMIT_BYTES) {
            relayController.abort()
          }
        }
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
          succeed(response, relayController)
        })
        .catch((error: unknown) => {
          primaryDone = true
          primaryError = error
          rejectWhenBothFailed()
        })

      void Promise.resolve()
        .then(() => primaryAdapter(relayConfig))
        .then((response) => {
          relayDone = true
          const size = responseBytes(response)
          if (size !== null && size > SMALL_GET_RACE_LIMIT_BYTES) {
            relayError = new AxiosError(
              'Hedged GET response exceeded the small-response limit',
              AxiosError.ERR_BAD_RESPONSE,
              config,
              response.request,
              response
            )
            relayController.abort()
            rejectWhenBothFailed()
            return
          }
          succeed(response, primaryController)
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
 * 超时、鉴权头、状态码处理，并让备用线路在读取过程中执行响应大小上限。
 */
export function installHedgedGetAdapter(
  config: InternalAxiosRequestConfig,
  relayBaseURL: string
): void {
  const raceConfig = config as HedgedConfig
  if (raceConfig.__zvmsHedgedGet) {
    return
  }
  // fetch adapter 会在读取过程中执行 maxContentLength；这样备用线路超过小型
  // 响应上限时会直接取消，而不是先完整下载再丢弃。
  const adapter = getAdapter(['fetch']) || getAdapter(config.adapter)
  if (!adapter) {
    return
  }
  raceConfig.__zvmsHedgedGet = true
  config.adapter = createHedgedGetAdapter(adapter, relayBaseURL)
}
