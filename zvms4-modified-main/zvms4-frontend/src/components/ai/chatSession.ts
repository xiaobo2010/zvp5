import type { AiChatMessage, AiChatRole } from '@/../types/ai'

/**
 * 对话页的状态模型。
 *
 * 抽出来是为了让「请求体长什么样」「失败怎么记」「断流怎么标」这几件事
 * 有单测可覆盖，不必每次都把组件挂起来跑一遍网络。
 */

export type ChatTurnStatus = 'pending' | 'done' | 'error' | 'stopped' | 'interrupted'

export interface ChatTurn {
  id: number
  role: 'user' | 'assistant'
  content: string
  status: ChatTurnStatus
  /** 失败时的错误码（后端 detail / OpenAI error.code）。 */
  errorCode?: string
  errorTitle?: string
  errorHint?: string
  /** 请求编号，出问题时给管理员对账用。 */
  requestId?: string | null
  /** 配额类错误时展示已用量与上限。 */
  quotaUsed?: number | null
  quotaLimit?: number | null
}

export interface ChatTurnInput {
  role: AiChatRole
  content: string
}

/**
 * 构造发往网关的请求体。
 *
 * 网关只读 model / messages / stream / provider，其余字段原样透传上游，
 * 所以这里只放这三个字段：错误的一轮不进历史，流式开关原样透传。
 */
export function buildChatRequest(
  turns: ChatTurnInput[],
  model: string,
  stream: boolean
): { model: string; messages: AiChatMessage[]; stream: boolean } {
  return {
    model,
    messages: turns
      .filter((turn) => turn.content.trim().length > 0)
      .map((turn) => ({ role: turn.role, content: turn.content })),
    stream
  }
}

/**
 * 历史轮次 → 请求体里的 messages。
 * 失败与被中断的一轮不进历史：它们没有完整答案，带进去会污染下一轮上下文。
 */
export function turnsToMessages(turns: ChatTurn[]): AiChatMessage[] {
  return turns
    .filter((turn) => turn.status === 'done' || turn.status === 'interrupted')
    .filter((turn) => turn.content.trim().length > 0)
    .map((turn) => ({ role: turn.role, content: turn.content }))
}
