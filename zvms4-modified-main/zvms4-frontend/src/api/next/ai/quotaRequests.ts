import { aiRequest } from '@/api/ai/http'

/**
 * AI 配额申请（/ai/quota/requests）。
 *
 * 三个视角：
 * - 任意登录人：提交申请、看自己最近 10 条；
 * - admin 或具备 quota.manage：审批队列、通过、拒绝；
 * - 仅 admin：用户额度一览（另一个模块负责）。
 *
 * V051 起用户侧额度只有 `af` 一个池，申请表仍保留 chat / ocr 两个历史口径，
 * 但落到额度上都是同一个池子。
 */

export type QuotaRequestKind = 'chat' | 'ocr'
export type QuotaRequestStatus = 'pending' | 'approved' | 'rejected' | 'cancelled'

export interface QuotaRequestRow {
  oid: string
  user_oid: string
  kind: QuotaRequestKind
  status: QuotaRequestStatus
  reason: string | null
  requested_limit: number | null
  note: string | null
  period: string
  created_at: string
  decided_at: string | null
  decided_by: string | null
  /** 审批队列里附带，便于直接展示申请人 */
  user_name?: string | null
  user_id?: string | null
  business_id?: string | null
}

export interface QuotaRequestPage {
  items: QuotaRequestRow[]
  total: number
  page: number
  size: number
}

export async function submitQuotaRequest(
  kind: QuotaRequestKind,
  reason?: string,
  requestedLimit?: number
): Promise<QuotaRequestRow> {
  return aiRequest<QuotaRequestRow>({
    method: 'post',
    url: '/ai/quota/requests',
    data: {
      kind,
      ...(reason ? { reason } : {}),
      ...(typeof requestedLimit === 'number' ? { requested_limit: requestedLimit } : {})
    }
  })
}

export async function listMyQuotaRequests(): Promise<QuotaRequestRow[]> {
  const data = await aiRequest<QuotaRequestRow[] | { items?: QuotaRequestRow[] }>({
    method: 'get',
    url: '/ai/quota/requests/mine'
  })
  if (Array.isArray(data)) {
    return data
  }
  return Array.isArray(data?.items) ? data.items : []
}

export async function listQuotaRequests(options: {
  status?: QuotaRequestStatus | 'all'
  page?: number
  size?: number
} = {}): Promise<QuotaRequestPage> {
  const data = await aiRequest<Partial<QuotaRequestPage>>({
    method: 'get',
    url: '/ai/quota/requests',
    params: {
      status: options.status && options.status !== 'all' ? options.status : 'pending',
      page: options.page ?? 1,
      size: options.size ?? 20
    }
  })
  return {
    items: Array.isArray(data?.items) ? data.items : [],
    total: Number(data?.total ?? 0),
    page: Number(data?.page ?? 1),
    size: Number(data?.size ?? 20)
  }
}

export async function approveQuotaRequest(
  oid: string,
  options: { reset?: boolean; limit?: number | null } = {}
): Promise<{ request: QuotaRequestRow; quota?: unknown }> {
  return aiRequest({
    method: 'post',
    url: `/ai/quota/requests/${encodeURIComponent(oid)}/approve`,
    data: {
      reset: options.reset !== false,
      ...(typeof options.limit === 'number' ? { limit: options.limit } : {})
    }
  })
}

export async function rejectQuotaRequest(
  oid: string,
  note?: string
): Promise<QuotaRequestRow> {
  return aiRequest<QuotaRequestRow>({
    method: 'post',
    url: `/ai/quota/requests/${encodeURIComponent(oid)}/reject`,
    data: note ? { note } : {}
  })
}
