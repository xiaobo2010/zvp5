import {
  AxiosError,
  AxiosHeaders,
  getAdapter,
  isCancel,
  type AxiosAdapter,
  type AxiosResponse,
  type InternalAxiosRequestConfig
} from 'axios'
import { normalizeApiPath } from '@/utils/apiRouting'

export const API_RACE_EVENT = 'zvms:api-race'

export type ApiRaceWinner = 'primary' | 'relay' | null
export type ApiRaceBranchOutcome =
  | 'pending'
  | 'success'
  | 'http_error'
  | 'network_error'
  | 'cancelled'
  | 'superseded'
export type ApiRaceReason =
  | 'fastest_complete'
  | 'primary_http_error'
  | 'fallback_http_response'
  | 'both_failed'
  | 'user_cancelled'

export interface ApiRaceBranchObservation {
  origin: string
  durationMs: number | null
  status: number | null
  outcome: ApiRaceBranchOutcome
}

export interface ApiRaceObservation {
  path: string
  winner: ApiRaceWinner
  reason: ApiRaceReason
  totalMs: number
  selectedStatus: number | null
  primary: ApiRaceBranchObservation
  relay: ApiRaceBranchObservation
}

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
  /^\/ai\/conversations\/[^/]+\/messages$/,
  /^\/ai\/runs(?:\/[^/]+)?$/,
  /^\/ai\/agent\/proposals$/,
  /^\/ai\/models$/,
  /^\/ai\/v1\/models$/,
  /^\/ai\/logs$/,
  /^\/ai\/logs\/stats$/,
  /^\/ai\/quota\/usage$/,
  /^\/ai\/quota\/[^/]+$/,
  /^\/ai\/scheduled-tasks$/,
  /^\/ai\/scheduled-tasks\/[^/]+\/runs$/,
  /^\/v2\/groups\/[^/]+\/time$/,
  /^\/v2\/groups\/[^/]+\/statistics\/compliance$/,
  /^\/v2\/times$/,
  /^\/system\/settings$/,
  /^\/system\/user-quotas\/[^/]+$/,
  /^\/v3\/notifications\/admin$/,
  /^\/v3\/notifications\/[^/]+\/versions$/,
  /^\/v3\/ban\/records$/
]

type HedgedConfig = InternalAxiosRequestConfig & {
  __zvmsHedgedGet?: boolean
}

interface BranchState {
  startedAt: number | null
  finishedAt: number | null
  status: number | null
  outcome: ApiRaceBranchOutcome
}

function nowMs(): number {
  return typeof performance !== 'undefined' ? performance.now() : Date.now()
}

function safeOrigin(value: string): string {
  try {
    const base = typeof window === 'undefined' ? 'http://localhost' : window.location.origin
    return new URL(value, base).origin
  } catch {
    return ''
  }
}

/**
 * Performance 和 CustomEvent 只保留标准化路径，不记录 query、请求头或响应体。
 * 24/32 位十六进制对象 ID 统一替换，避免观测数据本身成为长期标识。
 */
export function apiRaceObservationPath(value: unknown): string {
  return normalizeApiPath(value)
    .replace(/\/(?:[0-9a-f]{24}|[0-9a-f]{32})(?=\/|$)/gi, '/:id')
    .slice(0, 160)
}

function branchObservation(state: BranchState, origin: string): ApiRaceBranchObservation {
  return {
    origin,
    durationMs:
      state.startedAt === null || state.finishedAt === null
        ? null
        : Math.max(0, state.finishedAt - state.startedAt),
    status: state.status,
    outcome: state.outcome
  }
}

function emitApiRaceObservation(observation: ApiRaceObservation, startedAt: number): void {
  try {
    if (typeof performance !== 'undefined' && typeof performance.measure === 'function') {
      performance.measure(API_RACE_EVENT, {
        start: startedAt,
        end: startedAt + observation.totalMs,
        detail: observation
      })
    }
  } catch {
    // 观测失败不能改变请求结果。
  }

  try {
    if (typeof window !== 'undefined' && typeof CustomEvent === 'function') {
      window.dispatchEvent(
        new CustomEvent<ApiRaceObservation>(API_RACE_EVENT, { detail: observation })
      )
    }
  } catch {
    // 不支持 CustomEvent 的环境只保留 Performance entry。
  }
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

function classifyBranchError(
  error: unknown,
  externalAborted: boolean,
  settled: boolean
): {
  outcome: ApiRaceBranchOutcome
  status: number | null
} {
  if (externalAborted) {
    return { outcome: 'cancelled', status: null }
  }
  if (settled) {
    return { outcome: 'superseded', status: null }
  }
  if (isCancel(error)) {
    return { outcome: 'cancelled', status: null }
  }
  if (error instanceof AxiosError && error.response) {
    return { outcome: 'http_error', status: error.response.status }
  }
  return { outcome: 'network_error', status: null }
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
      const raceStartedAt = nowMs()
      const primaryOrigin = safeOrigin(String(config.baseURL ?? ''))
      const relayOrigin = safeOrigin(relayBaseURL)
      const primaryState: BranchState = {
        startedAt: null,
        finishedAt: null,
        status: null,
        outcome: 'pending'
      }
      const relayState: BranchState = {
        startedAt: null,
        finishedAt: null,
        status: null,
        outcome: 'pending'
      }
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
      let observed = false
      let externalAborted = false

      const finishBranch = (
        state: BranchState,
        outcome: ApiRaceBranchOutcome,
        status: number | null = null
      ) => {
        if (state.finishedAt !== null) {
          return
        }
        state.finishedAt = nowMs()
        state.outcome = outcome
        state.status = status
      }
      const observe = (
        winner: ApiRaceWinner,
        reason: ApiRaceReason,
        selectedStatus: number | null
      ) => {
        if (observed) {
          return
        }
        observed = true
        const totalMs = Math.max(0, nowMs() - raceStartedAt)
        emitApiRaceObservation(
          {
            path: apiRaceObservationPath(config.url),
            winner,
            reason,
            totalMs,
            selectedStatus,
            primary: branchObservation(primaryState, primaryOrigin),
            relay: branchObservation(relayState, relayOrigin)
          },
          raceStartedAt
        )
      }

      const abortBoth = () => {
        externalAborted = true
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
      const succeed = (
        response: AxiosResponse,
        winner: 'primary' | 'relay',
        loser: AbortController,
        loserState: BranchState
      ) => {
        if (settled) return
        settled = true
        cleanup()
        loser.abort()
        finishBranch(loserState, 'superseded')
        observe(winner, 'fastest_complete', response.status)
        response.config = config
        resolve(response)
      }
      const rejectWhenBothFailed = () => {
        if (settled || !primaryDone || !relayDone) return
        settled = true
        cleanup()
        if (primaryResponse && responseAccepted(config, primaryResponse)) {
          observe('primary', 'fallback_http_response', primaryResponse.status)
          primaryResponse.config = config
          resolve(primaryResponse)
          return
        }
        if (relayResponse && responseAccepted(config, relayResponse)) {
          observe('relay', 'fallback_http_response', relayResponse.status)
          relayResponse.config = config
          resolve(relayResponse)
          return
        }
        observe(null, externalAborted ? 'user_cancelled' : 'both_failed', null)
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
        .then(() => {
          primaryState.startedAt = nowMs()
          return primaryAdapter(primaryConfig)
        })
        .then((response) => {
          primaryDone = true
          primaryResponse = response
          if (response.status >= 200 && response.status < 300) {
            finishBranch(primaryState, 'success', response.status)
            succeed(response, 'primary', relayController, relayState)
          } else {
            finishBranch(primaryState, 'http_error', response.status)
            rejectWhenBothFailed()
          }
        })
        .catch((error: unknown) => {
          primaryDone = true
          primaryError = error
          const classification = classifyBranchError(error, externalAborted, settled)
          finishBranch(primaryState, classification.outcome, classification.status)
          if (isDefinitiveClientError(error) && !settled) {
            settled = true
            cleanup()
            relayController.abort()
            finishBranch(relayState, 'superseded')
            observe('primary', 'primary_http_error', classification.status)
            reject(error)
            return
          }
          rejectWhenBothFailed()
        })

      void Promise.resolve()
        .then(() => {
          relayState.startedAt = nowMs()
          return primaryAdapter(relayConfig)
        })
        .then((response) => {
          relayDone = true
          relayResponse = response
          if (response.status >= 200 && response.status < 300) {
            finishBranch(relayState, 'success', response.status)
            succeed(response, 'relay', primaryController, primaryState)
          } else {
            finishBranch(relayState, 'http_error', response.status)
            rejectWhenBothFailed()
          }
        })
        .catch((error: unknown) => {
          relayDone = true
          relayError = error
          const classification = classifyBranchError(error, externalAborted, settled)
          finishBranch(relayState, classification.outcome, classification.status)
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
