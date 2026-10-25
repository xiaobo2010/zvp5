import type {
  AiAgentProposal,
  AiAgentProposalListResponse,
  AiAgentProposalResult,
  AiAgentProposalStatus
} from '@/../types/ai'
import { aiFetchJson } from './http'

/**
 * 智能体面的提案端点（确认 / 驳回 / 待办列表）。
 *
 * 与 OpenAI 面同一套协议：成功是裸 JSON，错误是 `{"error":{...}}` + `X-AI-Request-Id`，
 * 所以复用 `aiFetchJson`。
 *
 * run 的提交与轮询在 `conversations.ts`：智能体已任务化，提交拿 202 后按 run_id
 * 轮询，没有「一次请求跑完整个工具循环」这种调用形态了。
 */

/** 提案列表的条数上限：后端 limit ≤ 50，超了会 422。 */
const PROPOSAL_LIST_LIMIT_MAX = 50

/** 提案列表的默认条数。 */
const PROPOSAL_LIST_LIMIT_DEFAULT = 20

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
