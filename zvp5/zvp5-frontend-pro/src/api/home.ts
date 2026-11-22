import type { CanaryActivity, CanaryActivityPage } from './canary'
import type { AdminLog } from './admin-manage'
import { apiRequest, buildQuery } from './http'

export const LOG_TYPE_LABELS: Record<string, string> = {
  risk_alert: '风险告警',
  risk_ban: '风险封禁',
  security_alert: '安全告警',
  platform_misuse_violation: '平台违规告警',
  agent_jailbreak_warning: 'ZVAgent 越狱告警'
}

export interface HomeAgentRun {
  oid: string
  conversation_oid: string
  status: string
  created_at: string | null
  model: string | null
  result_message_oid: string | null
  error_message: string | null
  result?: string
  resultUnavailable?: boolean
}

export async function fetchPendingReviews(): Promise<CanaryActivity[]> {
  const page = await apiRequest<CanaryActivityPage>('/v1/activities?page=1&size=10&status=pending')
  if (!Array.isArray(page?.activities)) throw new Error('Invalid activity page')
  return page.activities
}

export async function fetchRecentAgentRuns(): Promise<HomeAgentRun[]> {
  const page = await apiRequest<{ runs: HomeAgentRun[] }>('/ai/runs?active=0&limit=5')
  if (!Array.isArray(page?.runs)) throw new Error('Invalid run page')
  return Promise.all(page.runs.slice(0, 5).map(async (run) => {
    if (!run.result_message_oid) return run
    const path = `/ai/conversations/${encodeURIComponent(run.conversation_oid)}`
    try {
      const conversation = await apiRequest<{ message_count: number }>(path)
      const afterSeq = Math.max(0, conversation.message_count - 100)
      const page = await apiRequest<{ messages: { oid: string; content: string }[] }>(
        `${path}/messages${buildQuery({ after_seq: afterSeq, limit: 100 })}`
      )
      const result = page.messages.find((message) => message.oid === run.result_message_oid)
      return { ...run, result: result?.content, resultUnavailable: !result }
    } catch {
      return { ...run, resultUnavailable: true }
    }
  }))
}

export async function fetchSuspiciousLogs(): Promise<AdminLog[]> {
  const query = buildQuery({ page: 1, perpage: 5, type: Object.keys(LOG_TYPE_LABELS).join(',') })
  const logs = await apiRequest<AdminLog[]>(`/logs${query}`)
  if (!Array.isArray(logs)) throw new Error('Invalid log page')
  return logs
}
