/**
 * 对话页的一轮模型。
 *
 * 对话模式已下线（只保留智能体）：这里只留下 turn 的类型定义，
 * 供 agentSession 的展示模型复用；请求体构造随对话模式一并移除。
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
