import type {
  AiQuotaEntry,
  AiQuotaKind,
  AiQuotaModelUsageRow,
  AiQuotaMutation,
  AiQuotaProviderUsageRow,
  AiQuotaUsage,
  AiQuotaUsageRow
} from '@/../types/ai'
import { aiRequest, aiRequestList } from './http'

/**
 * 额度（/ai/quota）。
 *
 * 个人视角看自己的月度额度；管理端有两种形态：
 * 1. 「先选人，再查看与调整」——按 oid 精确查（GET /ai/quota/{user_oid}）；
 * 2. 用户配额表格（GET /ai/quota/users，仅 admin，支持姓名/学号搜索）。
 *
 * 额度用尽的人不能自己改数字，只能提交「重置申请」（/ai/quota/requests），
 * 由管理员或拥有 quota.manage 的角色批准。
 */

/**
 * 上限的来源。
 *
 * 语义坑：`explicit` **不等于**「管理员手调过」——首次扣费会把当时的默认上限
 * 固化进 limit_count，所以 UI 只能写「库内已固化」，不能写「管理员已设置」。
 */
export type AiQuotaSource = 'explicit' | 'role_default' | 'global_default'

/** 带来源的额度项：types/ai.d.ts 的 AiQuotaEntry 没有 source 字段。 */
export interface AiQuotaEntryView extends AiQuotaEntry {
  /** 老版本后端没有该字段时为 undefined，UI 自行降级为不显示标签。 */
  source?: AiQuotaSource
}

export interface AiQuotaSnapshotView {
  ocr: AiQuotaEntryView
  chat: AiQuotaEntryView
}

/**
 * 来源 → i18n key。
 *
 * 注意「库内已固化」的措辞：explicit 只说明这个数字已经写进 limit_count，
 * 不代表管理员手调过（首次扣费同样会固化默认值）。
 */
export const AI_QUOTA_SOURCE_LABEL_KEYS: Record<AiQuotaSource, string> = {
  explicit: 'ai.quotaAdmin.sourceExplicit',
  role_default: 'ai.quotaAdmin.sourceRoleDefault',
  global_default: 'ai.quotaAdmin.sourceGlobalDefault'
}

/** 来源的展示文案 key；未知/缺失返回 null，调用方不渲染标签。 */
export function aiQuotaSourceLabelKey(source?: AiQuotaSource | null): string | null {
  return source ? (AI_QUOTA_SOURCE_LABEL_KEYS[source] ?? null) : null
}

/** 当前用户本月额度自查。 */
export async function myQuota(): Promise<AiQuotaSnapshotView> {
  return aiRequest<AiQuotaSnapshotView>({ method: 'get', url: '/ai/quota' })
}

/**
 * 本人当月用量统计（/ai/quota/usage）。
 *
 * period 省略时后端按当月统计；传 `202610` 这类周期串可回看历史月份。
 * 后端按渠道（凭据名 / OCR 引擎名）与模型两个维度各给一份小计，
 * 维度值缺失时是 null，由页面显示占位文案。
 */
export async function fetchQuotaUsage(period?: string): Promise<AiQuotaUsage> {
  const params: Record<string, string | number> = {}
  const wanted = period?.trim()
  if (wanted) {
    params.period = wanted
  }
  const data = await aiRequest<unknown>({
    method: 'get',
    url: '/ai/quota/usage',
    params
  })
  const record = asRecord(data)
  return {
    period: textOf(record.period) ?? '',
    total: normalizeUsageRow(record.total),
    by_provider: Array.isArray(record.by_provider)
      ? record.by_provider.map(normalizeProviderUsage)
      : [],
    by_model: Array.isArray(record.by_model) ? record.by_model.map(normalizeModelUsage) : []
  }
}

/** admin 查看某个用户的额度。 */
export async function userQuota(userOid: string): Promise<AiQuotaSnapshotView> {
  return aiRequest<AiQuotaSnapshotView>({ method: 'get', url: `/ai/quota/${userOid}` })
}

/** 设置某能力的月上限；请求字段是 limit_count，响应字段是 limit。 */
export async function setUserLimit(
  userOid: string,
  kind: AiQuotaKind,
  limitCount: number
): Promise<AiQuotaMutation> {
  return aiRequest<AiQuotaMutation>({
    method: 'put',
    url: `/ai/quota/${userOid}`,
    data: { kind, limit_count: limitCount }
  })
}

/** 重置某能力本月已用量（上限保留）。 */
export async function resetUserQuota(
  userOid: string,
  kind: AiQuotaKind
): Promise<AiQuotaMutation> {
  return aiRequest<AiQuotaMutation>({
    method: 'post',
    url: `/ai/quota/${userOid}/reset`,
    data: { kind }
  })
}

/* ---------------------------------------------------------------------------
   额度重置申请（/ai/quota/requests）
   学生与学生会自己没有改额度的权限，只能提交一条申请，由管理员（或拥有
   quota.manage 的角色）批准。契约见后端 routers/ai_quota_router.py。
   --------------------------------------------------------------------------- */

/** 申请状态：pending 等待审批，approved / rejected 是终态。 */
export type AiQuotaRequestStatus = 'pending' | 'approved' | 'rejected'

/** 本人的一条申请（GET /ai/quota/requests/mine）。 */
export interface AiQuotaRequest {
  oid: string
  /** 计费周期，形如 202610。 */
  period: string
  kind: AiQuotaKind
  status: AiQuotaRequestStatus
  reason: string | null
  /** 申请人希望的月上限；null = 没提要求。 */
  requested_limit: number | null
  created_at: string | null
  decided_at: string | null
  decision_note: string | null
}

/** 审批人看到的申请：多带申请人信息。 */
export interface AiQuotaRequestAdminItem extends AiQuotaRequest {
  user_oid: string
  user_name: string
  business_id: string | null
}

/** 管理端用户列表里的单项用量：不带 period，source 老版本可能缺失。 */
export interface AiQuotaUsageView {
  used: number
  limit: number
  source?: AiQuotaSource
}

/** GET /ai/quota/users 的一行。 */
export interface AiQuotaUserRow {
  oid: string
  name: string
  business_id: string | null
  quotas: { chat: AiQuotaUsageView; ocr: AiQuotaUsageView }
  /** 该用户当前是否有待审批申请。 */
  pending_request: boolean
}

/** POST /ai/quota/requests 的请求体。 */
export interface AiQuotaSubmitInput {
  kind: AiQuotaKind
  /** 可选说明，后端按 200 字截断。 */
  reason?: string
  /** 可选期望额度；不传表示「只求清零」。 */
  requested_limit?: number
}

/** POST /ai/quota/requests/{oid}/approve 的请求体。 */
export interface AiQuotaApproveInput {
  /** 批准即清零用量（契约里是必填的常量 true）。 */
  reset: true
  /** 同时把上限改成这个值；不传表示上限不变。 */
  limit?: number
}

/** 批准结果：终态申请 + 更新后的该用户额度。 */
export interface AiQuotaApproveResult {
  request: AiQuotaRequest
  quota: AiQuotaSnapshotView
}

/** 分页响应（申请列表与用户列表同形状）。 */
export interface AiQuotaPage<T> {
  items: T[]
  total: number
  page: number
  size: number
}

export interface AiQuotaRequestQuery {
  status?: AiQuotaRequestStatus
  page?: number
  size?: number
}

export interface AiQuotaUserQuery {
  /** 姓名或学号模糊匹配。 */
  query?: string
  page?: number
  size?: number
}

function clampPage(value?: number): number {
  const page = Number.isFinite(value) ? Math.trunc(value as number) : 1
  return Math.max(1, page)
}

function clampSize(value?: number): number {
  const size = Number.isFinite(value) ? Math.trunc(value as number) : 20
  return Math.min(Math.max(1, size), 100)
}

type RawRow = Record<string, unknown>

function asRecord(value: unknown): RawRow {
  return value && typeof value === 'object' ? (value as RawRow) : {}
}

/** 后端既可能回 null，也可能回数字学号；统一成「有值就是字符串」。 */
function textOf(value: unknown): string | null {
  if (typeof value === 'string') {
    return value
  }
  if (typeof value === 'number') {
    return String(value)
  }
  return null
}

function numberOf(value: unknown): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : 0
}

function requestKindOf(value: unknown): AiQuotaKind {
  return value === 'chat' ? 'chat' : 'ocr'
}

function requestStatusOf(value: unknown): AiQuotaRequestStatus {
  if (value === 'approved' || value === 'rejected') {
    return value
  }
  return 'pending'
}

function sourceOf(value: unknown): AiQuotaSource | undefined {
  return value === 'explicit' || value === 'role_default' || value === 'global_default'
    ? value
    : undefined
}

/**
 * 申请行的归一化。
 *
 * 后端仓储层用的是 Mongo 风格的行（`_id`），契约里写的是 `oid`；这里两个都认，
 * 页面只按下发时的字段名用一个口径，不必到处写 `row.oid ?? row._id`。
 */
function normalizeRequest(value: unknown): AiQuotaRequest {
  const row = asRecord(value)
  return {
    oid: textOf(row.oid) ?? textOf(row._id) ?? '',
    period: textOf(row.period) ?? '',
    kind: requestKindOf(row.kind),
    status: requestStatusOf(row.status),
    reason: textOf(row.reason),
    requested_limit:
      typeof row.requested_limit === 'number' ? row.requested_limit : null,
    created_at: textOf(row.created_at),
    decided_at: textOf(row.decided_at),
    decision_note: textOf(row.decision_note)
  }
}

function normalizeRequestAdmin(value: unknown): AiQuotaRequestAdminItem {
  const row = asRecord(value)
  return {
    ...normalizeRequest(row),
    user_oid: textOf(row.user_oid) ?? '',
    user_name: textOf(row.user_name) ?? textOf(row.user_oid) ?? '',
    business_id: textOf(row.business_id) ?? textOf(row.user_id)
  }
}

function normalizeUsage(value: unknown): AiQuotaUsageView {
  const row = asRecord(value)
  return {
    used: numberOf(row.used),
    limit: numberOf(row.limit),
    source: sourceOf(row.source)
  }
}

/**
 * 用户表格行的归一化。
 *
 * 既接受 `quotas: {chat, ocr}` 的契约形态，也接受后端实际下发的扁平形态
 * （`chat` / `ocr` 直接挂在行上，`id` 是学号）。
 */
function normalizeUserRow(value: unknown): AiQuotaUserRow {
  const row = asRecord(value)
  const quotas = asRecord(row.quotas)
  return {
    oid: textOf(row.oid) ?? textOf(row._id) ?? '',
    name: textOf(row.name) ?? '',
    business_id: textOf(row.business_id) ?? textOf(row.id),
    quotas: {
      chat: normalizeUsage(quotas.chat ?? row.chat),
      ocr: normalizeUsage(quotas.ocr ?? row.ocr)
    },
    pending_request: row.pending_request === true
  }
}

/** 用量数值的五项小计：后端字段名与前端逐字一致，缺省按 0。 */
function normalizeUsageRow(value: unknown): AiQuotaUsageRow {
  const row = asRecord(value)
  return {
    calls: numberOf(row.calls),
    units: numberOf(row.units),
    prompt_tokens: numberOf(row.prompt_tokens),
    completion_tokens: numberOf(row.completion_tokens),
    total_tokens: numberOf(row.total_tokens)
  }
}

function normalizeProviderUsage(value: unknown): AiQuotaProviderUsageRow {
  const row = asRecord(value)
  return {
    provider: textOf(row.provider),
    ...normalizeUsageRow(row)
  }
}

function normalizeModelUsage(value: unknown): AiQuotaModelUsageRow {
  const row = asRecord(value)
  return {
    provider: textOf(row.provider),
    model: textOf(row.model),
    ...normalizeUsageRow(row)
  }
}

/**
 * 拆分页响应。
 *
 * 后端两种形态都能落地：`{items,total,page,size}` 整包，或 items 放在 data
 * 里、total 走 metadata.size 的列表约定。这里都接受，缺字段按空值兜底。
 */function readPage(data: unknown, metadata?: { size?: number }): {
  items: unknown[]
  total: number
  page: number
  size: number
} {
  if (Array.isArray(data)) {
    return { items: data, total: metadata?.size ?? data.length, page: 1, size: data.length }
  }
  const record = asRecord(data)
  const items = Array.isArray(record.items) ? record.items : []
  const total = typeof record.total === 'number' ? record.total : (metadata?.size ?? items.length)
  return {
    items,
    total,
    page: typeof record.page === 'number' ? record.page : 1,
    size: typeof record.size === 'number' ? record.size : items.length
  }
}

async function requestPage<T>(
  url: string,
  params: Record<string, string | number>,
  normalize: (value: unknown) => T
): Promise<AiQuotaPage<T>> {
  const result = await aiRequestList<unknown>({ method: 'get', url, params })
  const page = readPage(result.data, result.metadata)
  return {
    items: page.items.map(normalize),
    total: page.total,
    page: page.page,
    size: page.size
  }
}

/** 提交一条额度重置申请；同一能力已有 pending 时后端返回 409。 */
export async function createQuotaRequest(input: AiQuotaSubmitInput): Promise<AiQuotaRequest> {
  const data = await aiRequest<unknown>({
    method: 'post',
    url: '/ai/quota/requests',
    data: input
  })
  return normalizeRequest(data)
}

/** 本人全部申请（含已处理的历史，调用方自行按 status 过滤）。 */
export async function myQuotaRequests(): Promise<AiQuotaRequest[]> {
  const data = await aiRequest<unknown>({
    method: 'get',
    url: '/ai/quota/requests/mine'
  })
  const items = Array.isArray(data) ? data : asRecord(data).items
  return Array.isArray(items) ? items.map(normalizeRequest) : []
}

/** 审批列表（admin 或 quota.manage）。 */
export async function listQuotaRequests(
  query: AiQuotaRequestQuery = {}
): Promise<AiQuotaPage<AiQuotaRequestAdminItem>> {
  const params: Record<string, string | number> = {
    page: clampPage(query.page),
    size: clampSize(query.size)
  }
  if (query.status) {
    params.status = query.status
  }
  return requestPage('/ai/quota/requests', params, normalizeRequestAdmin)
}

/** 用户配额表格（仅 admin）。 */
export async function listQuotaUsers(
  query: AiQuotaUserQuery = {}
): Promise<AiQuotaPage<AiQuotaUserRow>> {
  const params: Record<string, string | number> = {
    page: clampPage(query.page),
    size: clampSize(query.size)
  }
  const keyword = query.query?.trim()
  if (keyword) {
    params.query = keyword
  }
  return requestPage('/ai/quota/users', params, normalizeUserRow)
}

/** 批准申请：清零用量，可选同时改上限。 */
export async function approveQuotaRequest(
  requestOid: string,
  input: AiQuotaApproveInput
): Promise<AiQuotaApproveResult> {
  const data = await aiRequest<unknown>({
    method: 'post',
    url: `/ai/quota/requests/${requestOid}/approve`,
    data: input
  })
  const record = asRecord(data)
  return {
    request: normalizeRequest(record.request),
    quota: record.quota as AiQuotaSnapshotView
  }
}

/** 驳回申请；理由可选。 */
export async function rejectQuotaRequest(
  requestOid: string,
  note?: string
): Promise<AiQuotaRequest> {
  const text = note?.trim()
  const data = await aiRequest<unknown>({
    method: 'post',
    url: `/ai/quota/requests/${requestOid}/reject`,
    data: text ? { note: text } : {}
  })
  return normalizeRequest(data)
}
