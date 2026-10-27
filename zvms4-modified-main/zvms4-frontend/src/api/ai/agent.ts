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
 * 用账号密码解锁智能体交互。
 *
 * 解锁是一次显式验证：服务端校验通过后给一个到期时间（`unlocked_until`），
 * 之后提交 run、确认 / 驳回提案都靠它放行。密码错是 403
 * `AI_AGENT_UNLOCK_FAILED`，连错锁定是 429 `AI_AGENT_UNLOCK_LOCKED`。
 *
 * 返回值是这个到期时间；后端没给或不认识时返回空串，让调用方按「未解锁」处理，
 * 不在这里编一个假的窗口。
 */
export async function unlockAgent(password: string): Promise<string> {
  const body = await aiFetchJson<unknown>('/ai/agent/unlock', {
    method: 'POST',
    body: JSON.stringify({ password })
  })
  return readUnlockedUntil(body)
}

/**
 * 读当前的无限制模式开关（仅 admin）。
 *
 * 开关是账号级设置，不是本会话的临时状态：进页面读一次，之后由 PUT 的结果对齐。
 */
export async function getModerationUnrestricted(): Promise<boolean> {
  const body = await aiFetchJson<unknown>('/ai/agent/moderation/unrestricted')
  return readModerationEnabled(body)
}

/** 写无限制模式开关（仅 admin）；返回服务端落库后的值。 */
export async function setModerationUnrestricted(
  enabled: boolean,
  verificationToken?: string
): Promise<boolean> {
  const body = await aiFetchJson<unknown>('/ai/agent/moderation/unrestricted', {
    method: 'PUT',
    body: JSON.stringify(
      verificationToken ? { enabled, verification_token: verificationToken } : { enabled }
    )
  })
  return readModerationEnabled(body)
}

function readUnlockedUntil(body: unknown): string {
  if (!body || typeof body !== 'object') {
    return ''
  }
  const value = (body as Record<string, unknown>).unlocked_until
  return typeof value === 'string' ? value : ''
}

function readModerationEnabled(body: unknown): boolean {
  if (!body || typeof body !== 'object') {
    return false
  }
  return (body as Record<string, unknown>).enabled === true
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
