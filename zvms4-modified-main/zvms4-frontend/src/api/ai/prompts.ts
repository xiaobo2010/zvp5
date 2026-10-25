import type { AiPromptAuditDetail, AiPromptAuditRecord } from '@/../types/ai'
import { aiRequest, aiRequestList } from './http'

/**
 * prompt 审阅（/ai/prompts）。仅 admin 可读，非 admin 返回 403 Permission denied。
 *
 * 两个端点的分工是刻意的：列表只回元数据（后端根本不 SELECT 正文），单条详情
 * 才回正文；详情每读一次，后端就在同一次请求里写一条 APP_LOGS 访问审计。
 * 所以「点开一条」是一次会被留痕的动作，页面必须显式说明。
 */

/** 审阅窗口的可选天数：与后端 PROMPT_MAX_DAYS=90 对齐。 */
export const AI_PROMPT_DAYS_OPTIONS = [7, 14, 30, 60, 90]
export const AI_PROMPT_DAYS_DEFAULT = 7
export const AI_PROMPT_DAYS_MIN = 1
export const AI_PROMPT_DAYS_MAX = 90

export const AI_PROMPTS_PERPAGE_DEFAULT = 20
export const AI_PROMPTS_PERPAGE_MAX = 100

export interface AiPromptsQuery {
  /** 1 起算。 */
  page?: number
  /** 每页条数，默认 20，上限 100。 */
  perpage?: number
  /** 用户 oid 精确过滤；空值不落到 query string。 */
  userOid?: string
  /** 滚动窗口（现在往前 N 天），1..90。 */
  days?: number
}

export interface AiPromptsPage {
  rows: AiPromptAuditRecord[]
  /** 总条数，来自响应体的 metadata.size。 */
  total: number
}

function clampPage(value?: number): number {
  const page = Number.isFinite(value) ? Math.trunc(value as number) : 1
  return Math.max(1, page)
}

function clampPerpage(value?: number): number {
  const size = Number.isFinite(value) ? Math.trunc(value as number) : AI_PROMPTS_PERPAGE_DEFAULT
  return Math.min(Math.max(1, size), AI_PROMPTS_PERPAGE_MAX)
}

function clampDays(value?: number): number {
  const days = Number.isFinite(value) ? Math.trunc(value as number) : AI_PROMPT_DAYS_DEFAULT
  return Math.min(Math.max(AI_PROMPT_DAYS_MIN, days), AI_PROMPT_DAYS_MAX)
}

/** 拉一页 prompt 元数据（新记录在前，不含正文）。 */
export async function listAiPrompts(query: AiPromptsQuery = {}): Promise<AiPromptsPage> {
  const params: Record<string, string | number> = {
    page: clampPage(query.page),
    perpage: clampPerpage(query.perpage),
    days: clampDays(query.days)
  }

  const userOid = query.userOid?.trim()
  if (userOid) {
    params.user_oid = userOid
  }

  const result = await aiRequestList<AiPromptAuditRecord[]>({
    method: 'get',
    url: '/ai/prompts',
    params
  })

  return {
    rows: Array.isArray(result.data) ? result.data : [],
    total: result.metadata.size
  }
}

/** 读一条正文（后端同时写访问审计）；不存在/oid 非法都回 404 AI_PROMPT_NOT_FOUND。 */
export async function getAiPrompt(oid: string): Promise<AiPromptAuditDetail> {
  const result = await aiRequest<AiPromptAuditDetail>({
    method: 'get',
    url: `/ai/prompts/${encodeURIComponent(oid)}`
  })

  return {
    ...result,
    // 正文为空与正文缺失在展示上是同一件事（都是「没有内容可看」），收敛成空串。
    prompt_text: result.prompt_text ?? ''
  }
}
