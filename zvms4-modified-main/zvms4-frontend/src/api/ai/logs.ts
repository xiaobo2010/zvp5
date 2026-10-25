import type {
  AiLogCapability,
  AiLogKeyScope,
  AiLogRecord,
  AiLogStats,
  AiLogStatus
} from '@/../types/ai'
import { aiRequestList, aiRequest } from './http'

/**
 * 调用日志（/ai/logs）。仅 admin 可读，非 admin 返回 403 Permission denied。
 *
 * 过滤条件全部可空：空值不落到 query string，由后端按「不过滤」处理。
 * 时间边界统一折成 epoch 秒 —— 后端契约里的 time_start / time_end 是秒。
 */

export type AiLogTimeInput = number | string | Date

export interface AiLogsQuery {
  /** 1 起算。 */
  page?: number
  /** 每页条数，默认 20，上限 100。 */
  perpage?: number
  user_oid?: string
  capability?: AiLogCapability
  status?: AiLogStatus
  key_scope?: AiLogKeyScope
  /** 精确匹配：从响应头 X-AI-Request-Id 抄下来的那一条链。 */
  request_id?: string
  provider?: string
  model?: string
  /** 场景来源（chat / markdown-assistant / ocr）。 */
  purpose?: string
  time_start?: AiLogTimeInput
  time_end?: AiLogTimeInput
}

/** 统计窗口的合法范围：1..90 天（与流水保留期一致）。 */
export const AI_LOG_STATS_MIN_DAYS = 1
export const AI_LOG_STATS_MAX_DAYS = 90
export const AI_LOG_STATS_DEFAULT_DAYS = 7

export interface AiLogsPage {
  rows: AiLogRecord[]
  /** 总条数，来自响应体的 metadata.size。 */
  total: number
}

export const AI_LOGS_PERPAGE_DEFAULT = 20
export const AI_LOGS_PERPAGE_MAX = 100

/** 时间边界折成 epoch 秒；无法解析时返回 undefined（该过滤项不生效）。 */
export function toEpochSeconds(value?: AiLogTimeInput | null): number | undefined {
  if (value === null || value === undefined || value === '') {
    return undefined
  }
  if (typeof value === 'number') {
    return Number.isFinite(value) ? Math.floor(value) : undefined
  }
  const parsed = new Date(value).getTime()
  return Number.isNaN(parsed) ? undefined : Math.floor(parsed / 1000)
}

function clampPage(value?: number): number {
  const page = Number.isFinite(value) ? Math.trunc(value as number) : 1
  return Math.max(1, page)
}

function clampPerpage(value?: number): number {
  const size = Number.isFinite(value) ? Math.trunc(value as number) : AI_LOGS_PERPAGE_DEFAULT
  return Math.min(Math.max(1, size), AI_LOGS_PERPAGE_MAX)
}

/** 拉一页调用日志。 */
export async function listAiLogs(query: AiLogsQuery = {}): Promise<AiLogsPage> {
  const params: Record<string, string | number> = {
    page: clampPage(query.page),
    perpage: clampPerpage(query.perpage)
  }

  const userOid = query.user_oid?.trim()
  if (userOid) {
    params.user_oid = userOid
  }
  if (query.capability) {
    params.capability = query.capability
  }
  if (query.status) {
    params.status = query.status
  }
  if (query.key_scope) {
    params.key_scope = query.key_scope
  }
  const requestId = query.request_id?.trim()
  if (requestId) {
    params.request_id = requestId
  }
  const provider = query.provider?.trim()
  if (provider) {
    params.provider = provider
  }
  const model = query.model?.trim()
  if (model) {
    params.model = model
  }
  const purpose = query.purpose?.trim()
  if (purpose) {
    params.purpose = purpose
  }
  const timeStart = toEpochSeconds(query.time_start)
  if (timeStart !== undefined) {
    params.time_start = timeStart
  }
  const timeEnd = toEpochSeconds(query.time_end)
  if (timeEnd !== undefined) {
    params.time_end = timeEnd
  }

  const result = await aiRequestList<AiLogRecord[]>({
    method: 'get',
    url: '/ai/logs',
    params
  })

  return {
    rows: Array.isArray(result.data) ? result.data : [],
    total: result.metadata.size
  }
}

function clampStatsDays(days?: number): number {
  const value = Number.isFinite(days) ? Math.trunc(days as number) : AI_LOG_STATS_DEFAULT_DAYS
  return Math.min(Math.max(AI_LOG_STATS_MIN_DAYS, value), AI_LOG_STATS_MAX_DAYS)
}

/**
 * 拉统计概览（按日 + 按 provider/model/purpose 小计）。
 *
 * 窗口由后端按「今天零点往前 days-1 天」计算：前端只传天数，不传时间边界，
 * 否则浏览器时区与服务器时区不一致时第一个自然日会变成半天。
 */
export async function getAiLogStats(days?: number): Promise<AiLogStats> {
  const result = await aiRequest<AiLogStats>({
    method: 'get',
    url: '/ai/logs/stats',
    params: { days: clampStatsDays(days) }
  })
  return {
    ...result,
    daily: Array.isArray(result.daily) ? result.daily : [],
    by_provider: Array.isArray(result.by_provider) ? result.by_provider : [],
    by_model: Array.isArray(result.by_model) ? result.by_model : [],
    by_purpose: Array.isArray(result.by_purpose) ? result.by_purpose : []
  }
}
