// Admin AI-gateway API client for the standalone Pro app.
//
// Read-only by design: the Pro console never creates, rotates or deletes AI
// credentials and never receives secret material. Endpoints mirror the main
// frontend (`src/api/ai/*`) and sit under the shared `/api` base; `http.ts`
// adds the Bearer token and unwraps the legacy `{ status, data }` envelope.

import { ApiError, apiList, apiRequest, buildQuery } from './http'

/* -------------------------------------------------------------------------- */
/* Small normalisation helpers                                                 */
/* -------------------------------------------------------------------------- */

type RawRecord = Record<string, unknown>

function asRecord(value: unknown): RawRecord {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as RawRecord)
    : {}
}

function asArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : []
}

/** Accept both a bare array and `{ <key>: [...] }` list envelopes. */
function rowsOf(value: unknown, ...keys: string[]): unknown[] {
  if (Array.isArray(value)) return value
  const record = asRecord(value)
  for (const key of keys) {
    const candidate = record[key]
    if (Array.isArray(candidate)) return candidate
  }
  return []
}

function textOf(value: unknown, fallback = ''): string {
  if (typeof value === 'string') return value
  if (typeof value === 'number' && Number.isFinite(value)) return String(value)
  return fallback
}

function nullableText(value: unknown): string | null {
  const text = textOf(value)
  return text ? text : null
}

function numberOf(value: unknown, fallback = 0): number {
  if (typeof value === 'number' && Number.isFinite(value)) return value
  if (typeof value === 'string' && value.trim()) {
    const parsed = Number(value)
    if (Number.isFinite(parsed)) return parsed
  }
  return fallback
}

function nullableNumber(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) return value
  if (typeof value === 'string' && value.trim()) {
    const parsed = Number(value)
    if (Number.isFinite(parsed)) return parsed
  }
  return null
}

function boolOf(value: unknown, fallback = false): boolean {
  if (typeof value === 'boolean') return value
  return fallback
}

function textList(value: unknown): string[] {
  return asArray(value)
    .map((item) => textOf(item))
    .filter((item) => item.length > 0)
}

/* -------------------------------------------------------------------------- */
/* Health                                                                      */
/* -------------------------------------------------------------------------- */

export interface AiHealthProvider {
  name: string
  kind: string
  baseUrl: string
  models: string[]
  configured: boolean | null
}

export interface AiHealthSharedEntry {
  name: string
  baseUrl: string
  models: string[]
  enabled: boolean
}

export interface AiHealthCredentials {
  personal: number
  shared: number
  server: number
  sharedEntries: AiHealthSharedEntry[]
}

export interface AiHealthData {
  enabled: boolean
  localEngineReady: boolean | null
  maxConcurrency: number
  workerCount: number
  rateLimitPerMinute: Array<{ capability: string; limit: number }>
  providers: AiHealthProvider[]
  models: string[]
  quotaDefaults: Array<{ kind: string; limit: number }>
  credentials: AiHealthCredentials
  cooldownScope: string
  cooldownSeconds: Array<{ provider: string; seconds: number }>
  promptCacheMode: string
}

function normalizeProvider(value: unknown): AiHealthProvider {
  const row = asRecord(value)
  return {
    name: textOf(row.name, '未命名渠道'),
    kind: textOf(row.kind),
    baseUrl: textOf(row.base_url),
    models: textList(row.models),
    configured: typeof row.configured === 'boolean' ? row.configured : null
  }
}

function normalizeSharedEntry(value: unknown): AiHealthSharedEntry {
  const row = asRecord(value)
  return {
    name: textOf(row.name, '未命名共享项'),
    baseUrl: textOf(row.base_url),
    models: textList(row.models),
    enabled: boolOf(row.enabled, true)
  }
}

function normalizeRateLimits(value: unknown): Array<{ capability: string; limit: number }> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return []
  return Object.entries(value as RawRecord)
    .map(([capability, limit]) => ({ capability, limit: numberOf(limit) }))
    .filter((item) => item.capability.length > 0)
    .sort((left, right) => left.capability.localeCompare(right.capability))
}

function normalizeMapCounts(value: unknown): Array<{ kind: string; limit: number }> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return []
  return Object.entries(value as RawRecord).map(([kind, limit]) => ({
    kind,
    limit: numberOf(limit)
  }))
}

function normalizeCooldowns(value: unknown): Array<{ provider: string; seconds: number }> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return []
  return Object.entries(value as RawRecord)
    .map(([provider, seconds]) => ({ provider, seconds: numberOf(seconds) }))
    .filter((item) => item.seconds > 0)
    .sort((left, right) => right.seconds - left.seconds)
}

function normalizeHealth(value: unknown): AiHealthData {
  const row = asRecord(value)
  const credentialsRow = asRecord(row.credentials)
  const promptCache = asRecord(row.prompt_cache)
  const ready = row.local_engine_ready
  return {
    enabled: boolOf(row.enabled),
    localEngineReady: typeof ready === 'boolean' ? ready : null,
    maxConcurrency: numberOf(row.max_concurrency),
    workerCount: numberOf(row.worker_count),
    rateLimitPerMinute: normalizeRateLimits(row.rate_limit_per_minute),
    providers: asArray(row.providers).map(normalizeProvider),
    models: textList(row.models),
    quotaDefaults: normalizeMapCounts(row.quota_defaults),
    credentials: {
      personal: numberOf(credentialsRow.personal),
      shared: numberOf(credentialsRow.shared),
      server: numberOf(credentialsRow.server),
      sharedEntries: asArray(credentialsRow.shared_entries).map(normalizeSharedEntry)
    },
    cooldownScope: textOf(row.cooldown_scope),
    cooldownSeconds: normalizeCooldowns(row.cooldown_seconds),
    promptCacheMode: textOf(promptCache.mode)
  }
}

/** Gateway health + credential/provider counts. Admin only. */
export async function fetchAiHealth(): Promise<AiHealthData> {
  return normalizeHealth(await apiRequest<unknown>('/ai/health'))
}

/* -------------------------------------------------------------------------- */
/* Keys (read-only)                                                            */
/* -------------------------------------------------------------------------- */

export interface AiKeyModelRef {
  id: string
  context: number | null
}

export interface AiKeyRecord {
  oid: string
  scope: string
  ownerOid: string
  name: string
  kind: string
  baseUrl: string
  models: AiKeyModelRef[]
  /** Last-four hint only; the API never returns the secret itself. */
  keyHint: string
  enabled: boolean
  expiresAt: string | null
  updatedAt: string | null
  owned: boolean
}

function normalizeModelRef(value: unknown): AiKeyModelRef | null {
  if (typeof value === 'string') {
    const id = value.trim()
    return id ? { id, context: null } : null
  }
  const row = asRecord(value)
  const id = textOf(row.id).trim()
  if (!id) return null
  return { id, context: nullableNumber(row.context) }
}

function normalizeKey(value: unknown): AiKeyRecord {
  const row = asRecord(value)
  return {
    oid: textOf(row.oid) || textOf(row._id),
    scope: textOf(row.scope),
    ownerOid: textOf(row.owner_oid),
    name: textOf(row.name, '未命名凭据'),
    kind: textOf(row.kind),
    baseUrl: textOf(row.base_url),
    models: asArray(row.models)
      .map(normalizeModelRef)
      .filter((item): item is AiKeyModelRef => item !== null),
    keyHint: textOf(row.key_hint),
    enabled: boolOf(row.enabled, true),
    expiresAt: nullableText(row.expires_at),
    updatedAt: nullableText(row.updated_at),
    owned: boolOf(row.owned)
  }
}

/** All credential records (personal first). Secrets are never present. */
export async function fetchAiKeys(): Promise<AiKeyRecord[]> {
  const data = await apiRequest<unknown>('/ai/keys')
  return rowsOf(data, 'items', 'keys').map(normalizeKey)
}

/* -------------------------------------------------------------------------- */
/* Models (read-only)                                                          */
/* -------------------------------------------------------------------------- */

export interface AiModelRow {
  model: string
  displayName: string
  provider: string
  providers: string[]
  contextLength: number | null
  rate: number | null
  enabled: boolean
  inCatalog: boolean
  notes: string
  updatedAt: string | null
}

function normalizeModelRow(value: unknown): AiModelRow {
  const row = asRecord(value)
  const provider = textOf(row.provider)
  const providers = textList(row.providers)
  return {
    model: textOf(row.model),
    displayName: textOf(row.display_name),
    provider,
    providers: providers.length ? providers : provider ? [provider] : [],
    contextLength: nullableNumber(row.context_length),
    rate: nullableNumber(row.rate),
    enabled: boolOf(row.enabled, true),
    inCatalog: boolOf(row.in_catalog),
    notes: textOf(row.notes),
    updatedAt: nullableText(row.updated_at)
  }
}

/** Catalogue rows ∪ manual overrides. */
export async function fetchAiModels(): Promise<AiModelRow[]> {
  const data = await apiRequest<unknown>('/ai/models')
  return rowsOf(data, 'models', 'items')
    .map(normalizeModelRow)
    .filter((item) => item.model.length > 0)
}

/* -------------------------------------------------------------------------- */
/* Quota users (read-only)                                                     */
/* -------------------------------------------------------------------------- */

export type AiQuotaSource = 'explicit' | 'role_default' | 'global_default'

export interface AiQuotaUserRow {
  oid: string
  name: string
  businessId: string
  period: string
  /** Unified AF pool: chat and OCR share the same counter. */
  used: number
  limit: number
  source: AiQuotaSource | ''
  pendingRequest: boolean
}

export interface AiQuotaPage<T> {
  items: T[]
  total: number
  page: number
  size: number
}

function normalizeQuotaSource(value: unknown): AiQuotaSource | '' {
  return value === 'explicit' || value === 'role_default' || value === 'global_default'
    ? value
    : ''
}

function normalizeQuotaEntry(value: unknown): {
  period: string
  used: number
  limit: number
  source: AiQuotaSource | ''
} {
  const row = asRecord(value)
  return {
    period: textOf(row.period),
    used: numberOf(row.used),
    limit: numberOf(row.limit),
    source: normalizeQuotaSource(row.source)
  }
}

function normalizeQuotaUser(value: unknown): AiQuotaUserRow {
  const row = asRecord(value)
  const quotas = asRecord(row.quotas)
  // Backend still ships `chat` / `ocr` aliases, but both read the same AF pool —
  // never add them together. Prefer `af` when present.
  const entry = normalizeQuotaEntry(
    quotas.af ?? row.af ?? row.chat ?? row.ocr
  )
  return {
    oid: textOf(row.oid) || textOf(row._id),
    name: textOf(row.name, '未命名用户'),
    businessId: textOf(row.business_id) || textOf(row.id),
    period: textOf(row.period) || entry.period,
    used: entry.used,
    limit: entry.limit,
    source: entry.source,
    pendingRequest: boolOf(row.pending_request)
  }
}

export async function fetchAiQuotaUsers(
  query = '',
  page = 1,
  size = 20
): Promise<AiQuotaPage<AiQuotaUserRow>> {
  const keyword = query.trim()
  const result = await apiList<unknown>(
    `/ai/quota/users${buildQuery({ query: keyword, page, size })}`
  )
  const record = asRecord(result.data)
  const rows = rowsOf(result.data, 'items', 'users')
  const total = numberOf(record.total, result.size || rows.length)
  return {
    items: rows.map(normalizeQuotaUser),
    total,
    page: numberOf(record.page, page),
    size: numberOf(record.size, size)
  }
}

/* -------------------------------------------------------------------------- */
/* Call logs (read-only)                                                       */
/* -------------------------------------------------------------------------- */

export type AiLogStatus = 'success' | 'error' | 'timeout' | 'rejected' | 'busy' | 'warning'

export interface AiLogRecord {
  oid: string
  createdAt: string
  status: string
  capability: string
  provider: string
  model: string
  purpose: string
  userName: string
  userOid: string
  totalTokens: number | null
  latencyMs: number | null
  units: number
  httpStatus: number | null
  errorCode: string
}

export interface AiLogsPage {
  rows: AiLogRecord[]
  total: number
}

function normalizeLog(value: unknown): AiLogRecord {
  const row = asRecord(value)
  return {
    oid: textOf(row.oid) || textOf(row._id),
    createdAt: textOf(row.created_at),
    status: textOf(row.status, 'unknown'),
    capability: textOf(row.capability),
    provider: textOf(row.provider),
    model: textOf(row.model),
    purpose: textOf(row.purpose),
    userName: textOf(row.user_name),
    userOid: textOf(row.user_oid),
    totalTokens: nullableNumber(row.total_tokens),
    latencyMs: nullableNumber(row.latency_ms),
    units: numberOf(row.units),
    httpStatus: nullableNumber(row.http_status),
    errorCode: textOf(row.error_code)
  }
}

export async function fetchAiLogs(page = 1, perpage = 20): Promise<AiLogsPage> {
  const result = await apiList<unknown>(
    `/ai/logs${buildQuery({ page, perpage })}`
  )
  const record = asRecord(result.data)
  const rows = rowsOf(result.data, 'items', 'logs')
  const total = result.size > 0 ? result.size : numberOf(record.total, rows.length)
  return { rows: rows.map(normalizeLog), total }
}

/* -------------------------------------------------------------------------- */
/* Prompt audit (read-only, admin)                                             */
/* -------------------------------------------------------------------------- */

/**
 * The two prompt endpoints are deliberately split: the list never selects the
 * body (`prompt_chars` is `LENGTH(PROMPT_TEXT)`), and only the detail endpoint
 * returns it. Each detail read writes an `APP_LOGS` admin-access audit row, so
 * opening a record is an audited action and the UI must say so.
 */

/** Rolling window options, aligned with the backend's `PROMPT_MAX_DAYS=90`. */
export const AI_PROMPT_DAYS_OPTIONS = [7, 14, 30, 90] as const
export const AI_PROMPT_DAYS_DEFAULT = 7
export const AI_PROMPT_MAX_DAYS = 90
export const AI_PROMPT_PAGE_SIZE = 20
const AI_PROMPT_MAX_PAGE_SIZE = 100

export interface AiPromptRecord {
  oid: string
  /** Cleared by the backend once the account is removed. */
  userOid: string | null
  userName: string
  purpose: string
  /** Comma-separated role identifiers captured when the prompt was retained. */
  rolesSnapshot: string
  /** Body length in the database; the list never carries the body itself. */
  promptChars: number
  createdAt: string
}

export interface AiPromptsPage {
  rows: AiPromptRecord[]
  total: number
}

export interface AiPromptDetail {
  oid: string
  requestId: string | null
  userOid: string | null
  userName: string
  purpose: string
  rolesSnapshot: string
  promptText: string
  answerText: string
  /** `tool_calls` JSON array text; empty string when nothing was retained. */
  toolCalls: string
  refused: boolean
  warned: boolean
  createdAt: string
}

function normalizePromptRecord(value: unknown): AiPromptRecord {
  const row = asRecord(value)
  return {
    oid: textOf(row.oid) || textOf(row._id),
    userOid: nullableText(row.user_oid),
    userName: textOf(row.user_name, '未知用户'),
    purpose: textOf(row.purpose),
    rolesSnapshot: textOf(row.roles_snapshot),
    promptChars: numberOf(row.prompt_chars),
    createdAt: textOf(row.created_at)
  }
}

/** JSON columns arrive as text, but tolerate an already-parsed value. */
function jsonTextOf(value: unknown): string {
  if (typeof value === 'string') return value
  if (value === null || value === undefined) return ''
  try {
    return JSON.stringify(value) ?? ''
  } catch {
    return ''
  }
}

/** `refused`/`warned` are booleans in ORM rows and 0/1 in raw payloads. */
function flagOf(value: unknown): boolean {
  if (typeof value === 'boolean') return value
  if (typeof value === 'number') return value !== 0
  if (typeof value === 'string') return value === '1' || value.toLowerCase() === 'true'
  return false
}

function normalizePromptDetail(value: unknown): AiPromptDetail {
  const row = asRecord(value)
  return {
    oid: textOf(row.oid) || textOf(row._id),
    requestId: nullableText(row.request_id),
    userOid: nullableText(row.user_oid),
    userName: textOf(row.user_name, '未知用户'),
    purpose: textOf(row.purpose),
    rolesSnapshot: textOf(row.roles_snapshot),
    promptText: textOf(row.prompt_text),
    answerText: textOf(row.answer_text),
    toolCalls: jsonTextOf(row.tool_calls),
    refused: flagOf(row.refused),
    warned: flagOf(row.warned),
    createdAt: textOf(row.created_at)
  }
}

function clampPromptDays(days: number): number {
  if (!Number.isFinite(days)) return AI_PROMPT_DAYS_DEFAULT
  return Math.min(AI_PROMPT_MAX_DAYS, Math.max(1, Math.trunc(days)))
}

/** One page of retained prompt metadata (newest first, never the body). */
export async function fetchAiPrompts(
  page = 1,
  perpage = AI_PROMPT_PAGE_SIZE,
  days = AI_PROMPT_DAYS_DEFAULT,
  userOid = ''
): Promise<AiPromptsPage> {
  const safePage = Number.isFinite(page) && page > 0 ? Math.trunc(page) : 1
  const safeSize = Number.isFinite(perpage)
    ? Math.min(AI_PROMPT_MAX_PAGE_SIZE, Math.max(1, Math.trunc(perpage)))
    : AI_PROMPT_PAGE_SIZE
  const result = await apiList<unknown>(
    `/ai/prompts${buildQuery({
      page: safePage,
      perpage: safeSize,
      days: clampPromptDays(days),
      user_oid: userOid.trim() || undefined
    })}`
  )
  const rows = rowsOf(result.data, 'items', 'prompts', 'rows')
    .map(normalizePromptRecord)
    .filter((item) => item.oid.length > 0)
  const total = result.size > 0 ? result.size : rows.length
  return { rows, total }
}

/** Read one retained prompt body. The backend writes an access-audit row. */
export async function fetchAiPromptDetail(oid: string): Promise<AiPromptDetail> {
  const target = oid.trim()
  if (!target) throw new ApiError('缺少记录编号，无法读取正文。', 400)
  return normalizePromptDetail(
    await apiRequest<unknown>(`/ai/prompts/${encodeURIComponent(target)}`)
  )
}

/* -------------------------------------------------------------------------- */
/* Call-log statistics                                                         */
/* -------------------------------------------------------------------------- */

export interface AiLogDailyStat {
  date: string
  calls: number
  errors: number
  promptTokens: number
  completionTokens: number
  totalTokens: number
  avgLatencyMs: number | null
}

export interface AiLogStatusStat {
  status: string
  calls: number
}

export interface AiLogGroupStat {
  key: string
  calls: number
  errors: number
  units: number
  totalTokens: number
}

export interface AiLogStats {
  days: number
  since: string
  daily: AiLogDailyStat[]
  byStatus: AiLogStatusStat[]
  byProvider: AiLogGroupStat[]
  byModel: AiLogGroupStat[]
}

function normalizeDaily(value: unknown): AiLogDailyStat {
  const row = asRecord(value)
  return {
    date: textOf(row.date),
    calls: numberOf(row.calls),
    errors: numberOf(row.errors),
    promptTokens: numberOf(row.prompt_tokens),
    completionTokens: numberOf(row.completion_tokens),
    totalTokens: numberOf(row.total_tokens),
    avgLatencyMs: nullableNumber(row.avg_latency_ms)
  }
}

function normalizeStatusStat(value: unknown): AiLogStatusStat {
  const row = asRecord(value)
  return {
    status: textOf(row.status, 'unknown'),
    calls: numberOf(row.calls)
  }
}

function normalizeGroupStat(value: unknown, keyField: string): AiLogGroupStat {
  const row = asRecord(value)
  return {
    key: textOf(row[keyField], '未标注'),
    calls: numberOf(row.calls),
    errors: numberOf(row.errors),
    units: numberOf(row.units),
    totalTokens: numberOf(row.total_tokens)
  }
}

function sortGroups(rows: AiLogGroupStat[]): AiLogGroupStat[] {
  return [...rows].sort((left, right) => right.calls - left.calls)
}

export async function fetchAiLogStats(days = 7): Promise<AiLogStats> {
  const data = await apiRequest<unknown>(`/ai/logs/stats${buildQuery({ days })}`)
  const row = asRecord(data)
  return {
    days: numberOf(row.days, days),
    since: textOf(row.since),
    daily: asArray(row.daily).map(normalizeDaily),
    byStatus: asArray(row.by_status).map(normalizeStatusStat),
    byProvider: sortGroups(
      asArray(row.by_provider).map((item) => normalizeGroupStat(item, 'provider'))
    ),
    byModel: sortGroups(
      asArray(row.by_model).map((item) => normalizeGroupStat(item, 'model'))
    )
  }
}

/* -------------------------------------------------------------------------- */
/* Scheduled tasks (read-only)                                                 */
/* -------------------------------------------------------------------------- */

export interface AiScheduledTask {
  oid: string
  ownerOid: string
  ownerName: string
  name: string
  reviewStatus: string
  scheduleKind: string
  intervalMinutes: number
  cronExpr: string
  enabled: boolean
  model: string
  lastRunAt: string | null
  nextRunAt: string | null
  runCount: number
  lastStatus: string
  lastError: string
}

function normalizeScheduledTask(value: unknown): AiScheduledTask {
  const row = asRecord(value)
  return {
    oid: textOf(row.oid) || textOf(row._id),
    ownerOid: textOf(row.owner_oid),
    ownerName: textOf(row.owner_name),
    name: textOf(row.name, '未命名任务'),
    reviewStatus: textOf(row.review_status, 'draft'),
    scheduleKind: textOf(row.schedule_kind, 'interval'),
    intervalMinutes: numberOf(row.interval_minutes),
    cronExpr: textOf(row.cron_expr),
    enabled: boolOf(row.enabled),
    model: textOf(row.model),
    lastRunAt: nullableText(row.last_run_at),
    nextRunAt: nullableText(row.next_run_at),
    runCount: numberOf(row.run_count),
    lastStatus: textOf(row.last_status),
    lastError: textOf(row.last_error)
  }
}

/** Admin view across all owners. `limit` caps a single page (backend max 500). */
export async function fetchAiScheduledTasks(limit = 50): Promise<AiScheduledTask[]> {
  const result = await apiList<unknown>(
    `/ai/scheduled-tasks${buildQuery({ all_tasks: 1, limit })}`
  )
  return rowsOf(result.data, 'items', 'tasks').map(normalizeScheduledTask)
}

/* -------------------------------------------------------------------------- */
/* OCR engines (read-only)                                                     */
/* -------------------------------------------------------------------------- */

export interface AiOcrEngine {
  oid: string
  name: string
  kind: string
  enabled: boolean
  hasSecret: boolean
  keyHint: string
  priority: number
  models: string[]
  updatedAt: string | null
}

function engineModels(config: RawRecord): string[] {
  const models = textList(config.models)
  if (models.length) return models
  const single = textOf(config.model)
  return single ? [single] : []
}

function normalizeOcrEngine(value: unknown): AiOcrEngine {
  const row = asRecord(value)
  const config = asRecord(row.config)
  return {
    oid: textOf(row.oid) || textOf(row._id),
    name: textOf(row.name, '未命名引擎'),
    kind: textOf(row.kind),
    enabled: boolOf(row.enabled, true),
    hasSecret: boolOf(row.has_secret),
    keyHint: textOf(row.key_hint),
    priority: numberOf(row.priority),
    models: engineModels(config),
    updatedAt: nullableText(row.updated_at)
  }
}

export async function fetchAiOcrEngines(): Promise<AiOcrEngine[]> {
  const data = await apiRequest<unknown>('/ai/ocr-engines')
  return rowsOf(data, 'items', 'engines').map(normalizeOcrEngine)
}
