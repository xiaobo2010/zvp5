import type {
  AiAgentProposal,
  AiAgentProposalListResponse,
  AiAgentProposalResult,
  AiAgentProposalStatus,
  AiAgentRunReplay,
  AiAgentRunResponse,
  AiChatMessage
} from '@/../types/ai'
import { AI_SCENE_HEADER, aiFetchJson } from './http'

/**
 * 智能体面（/ai/agent/*）。
 *
 * 与 OpenAI 面同一套协议：成功是裸 JSON，错误是 `{"error":{...}}` + `X-AI-Request-Id`，
 * 所以复用 `aiFetchJson`（错误统一归一成 AiRequestError，带错误码与请求编号）。
 *
 * 与 chat.ts 的分界：这里**不流式**。后端一次 run 会跑完整个工具循环才返回，
 * 中途没有增量可推；用流式接口只会让前端假装在打字。
 */

/** 审计场景名：后端把它落进 AI_CALL_LOGS.PURPOSE（只用于统计）。 */
export const AI_SCENE_AGENT = 'agent'

/** 提案列表的条数上限：后端 limit ≤ 50，超了会 422。 */
const PROPOSAL_LIST_LIMIT_MAX = 50

/** 提案列表的默认条数。 */
const PROPOSAL_LIST_LIMIT_DEFAULT = 20

/**
 * 跑一次智能体会话。
 *
 * `model` 可省：省了由后端取该用户可见目录里的第一个模型；显式指定则必须
 * 在该目录内（否则 404 `model_not_found`）。`signal` 用于「停止」按钮中断等待。
 */
export async function runAgent(
  messages: AiChatMessage[],
  model?: string,
  signal?: AbortSignal
): Promise<AiAgentRunResponse> {
  return aiFetchJson<AiAgentRunResponse>('/ai/agent/run', {
    method: 'POST',
    body: JSON.stringify({ messages, model }),
    headers: { [AI_SCENE_HEADER]: AI_SCENE_AGENT },
    signal
  })
}

/**
 * 确认并执行一条写操作提案（服务端二次校验权限、状态、时效与参数哈希）。
 *
 * `totpCode` 只在提案 `requires_dual_auth` 时必填：运维写工具（文件 / 数据库 / SSH）
 * 一律要另一位管理员的 6 位动态码，带上时进请求体 `{totp_code}`；不带就发空对象，
 * 让服务端按「缺码」回 422 `AI_AGENT_TOTP_REQUIRED`，而不是让前端猜。
 */
export async function confirmProposal(
  oid: string,
  totpCode?: string
): Promise<AiAgentProposalResult> {
  const code = totpCode?.trim()
  return aiFetchJson<AiAgentProposalResult>(
    `/ai/agent/proposals/${encodeURIComponent(oid)}/confirm`,
    {
      method: 'POST',
      body: JSON.stringify(code ? { totp_code: code } : {})
    }
  )
}

/** 驳回一条提案（不执行任何写）。 */
export async function rejectProposal(oid: string): Promise<AiAgentProposalResult> {
  return aiFetchJson<AiAgentProposalResult>(
    `/ai/agent/proposals/${encodeURIComponent(oid)}/reject`,
    { method: 'POST' }
  )
}

/**
 * 拉本人待确认的提案。
 *
 * 后端按 `created_at` 倒序返回，并顺手把过期行落成 expired；只查本人
 * （`actor_oid`）。页面刷新后靠它把「还没处理的提案」找回来。
 */
export async function fetchPendingProposals(
  status: AiAgentProposalStatus = 'proposed',
  limit: number = PROPOSAL_LIST_LIMIT_DEFAULT
): Promise<AiAgentProposalListResponse> {
  const query = new URLSearchParams()
  query.set('status', status)
  query.set('limit', String(clampLimit(limit)))
  const body = await aiFetchJson<unknown>(`/ai/agent/proposals?${query.toString()}`)
  return { proposals: readProposalList(body) }
}

/**
 * 取一次 run 的事件时间线（刷新后回看）。
 *
 * 后端用 `list_for_request` 按 request_id 取回那一轮的留痕。
 */
export async function fetchRun(requestId: string): Promise<AiAgentRunReplay> {
  const body = await aiFetchJson<unknown>(`/ai/agent/runs/${encodeURIComponent(requestId)}`)
  const record = (body ?? {}) as Record<string, unknown>
  return {
    request_id:
      typeof record.request_id === 'string' && record.request_id ? record.request_id : requestId,
    events: Array.isArray(record.events) ? (record.events as AiAgentRunReplay['events']) : [],
    proposals: Array.isArray(record.proposals) ? (record.proposals as AiAgentProposal[]) : undefined
  }
}

function clampLimit(limit: number): number {
  if (!Number.isFinite(limit)) {
    return PROPOSAL_LIST_LIMIT_DEFAULT
  }
  return Math.min(PROPOSAL_LIST_LIMIT_MAX, Math.max(1, Math.trunc(limit)))
}

/**
 * 列表响应的形状归一。
 *
 * 外壳已冻结为 `{"proposals": [...]}`：只认这一个键，不认识的形状给空数组，
 * 不让后端换个外壳前端就白屏。
 */
function readProposalList(body: unknown): AiAgentProposal[] {
  if (!body || typeof body !== 'object' || Array.isArray(body)) {
    return []
  }
  const value = (body as Record<string, unknown>).proposals
  return Array.isArray(value) ? (value as AiAgentProposal[]) : []
}
