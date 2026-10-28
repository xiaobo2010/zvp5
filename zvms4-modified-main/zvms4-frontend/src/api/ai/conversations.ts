import type {
  AiConversation,
  AiConversationDeleteResult,
  AiConversationMode,
  AiConversationPage,
  AiConversationStatus,
  AiMessage,
  AiMessagePage,
  AiRun,
  AiRunDetail,
  AiRunSubmitResult
} from '@/../types/ai'
import { aiRequest, aiRequestList } from './http'

/**
 * 持久会话面（/ai/conversations、/ai/runs）。
 *
 * 与 keys / quota 那几个 zvp 面接口同一套外壳：响应是
 * `{"status":"ok","data":{...}}`，错误是 `{"detail":"<code>"}`，
 * 所以这里走 `aiRequest` / `aiRequestList`（拆 envelope、错误归一成
 * AiRequestError）。
 *
 * 契约见 docs/AI_CONVERSATION_DESIGN.md §3；字段名与后端视图逐字对齐，
 * 前端不自造字段。
 */

/** 会话列表默认条数与上限（后端 limit ≤ 100）。 */
const CONVERSATION_LIST_LIMIT = 50
const CONVERSATION_LIST_LIMIT_MAX = 100

/** 消息增量拉取的默认条数。 */
const MESSAGE_PAGE_LIMIT = 50

function clampLimit(limit: number, max: number, fallback: number): number {
  if (!Number.isFinite(limit)) {
    return fallback
  }
  return Math.min(max, Math.max(1, Math.trunc(limit)))
}

function oid(path: string): string {
  return encodeURIComponent(path)
}

/* ---------------------------------------------------------------------------
   会话
   --------------------------------------------------------------------------- */

/** 建一个会话；mode 建完不可改。 */
export async function createConversation(
  mode: AiConversationMode,
  title?: string,
  model?: string
): Promise<AiConversation> {
  return aiRequest<AiConversation>({
    method: 'post',
    url: '/ai/conversations',
    data: {
      mode,
      ...(title ? { title } : {}),
      ...(model ? { model } : {})
    }
  })
}

/**
 * 会话列表：游标分页，按 updated_at 倒序。
 *
 * 列表不含消息正文，也不含活跃 run（那部分走 `GET /ai/runs?active=1`），
 * 所以翻页的载荷始终很小。
 */
export async function listConversations(
  options: {
    status?: AiConversationStatus
    limit?: number
    cursor?: string | null
  } = {}
): Promise<AiConversationPage> {
  const limit = clampLimit(
    options.limit ?? CONVERSATION_LIST_LIMIT,
    CONVERSATION_LIST_LIMIT_MAX,
    CONVERSATION_LIST_LIMIT
  )
  const { data, metadata } = await aiRequestList<{
    conversations?: AiConversation[]
    next_cursor?: string | null
  }>({
    method: 'get',
    url: '/ai/conversations',
    params: {
      status: options.status ?? 'active',
      limit,
      ...(options.cursor ? { cursor: options.cursor } : {})
    }
  })
  return {
    conversations: Array.isArray(data?.conversations) ? data.conversations : [],
    next_cursor:
      typeof data?.next_cursor === 'string' && data.next_cursor ? data.next_cursor : null,
    size: metadata.size
  }
}

/** 取一个会话的详情。 */
export async function getConversation(conversationOid: string): Promise<AiConversation> {
  return aiRequest<AiConversation>({
    method: 'get',
    url: `/ai/conversations/${oid(conversationOid)}`
  })
}

/** 改会话：标题、状态、模型至少给一个。 */
export async function updateConversation(
  conversationOid: string,
  payload: {
    title?: string
    status?: AiConversationStatus
    model?: string | null
  }
): Promise<AiConversation> {
  return aiRequest<AiConversation>({
    method: 'patch',
    url: `/ai/conversations/${oid(conversationOid)}`,
    data: payload
  })
}

/**
 * 硬删会话（消息与运行记录一并删）。
 *
 * 存在进行中的 run 时后端回 409 AI_RUN_CONFLICT：先中止再删。
 */
export async function deleteConversation(
  conversationOid: string
): Promise<AiConversationDeleteResult> {
  return aiRequest<AiConversationDeleteResult>({
    method: 'delete',
    url: `/ai/conversations/${oid(conversationOid)}`
  })
}

/**
 * 拉会话内的消息。
 *
 * `afterSeq` 是增量口径：只回 seq 更大的那些，轮询与刷新都用它，
 * 不必每次把整段历史重新拉一遍。
 */
export async function listMessages(
  conversationOid: string,
  options: { afterSeq?: number; limit?: number } = {}
): Promise<AiMessagePage> {
  const limit = clampLimit(options.limit ?? MESSAGE_PAGE_LIMIT, 100, MESSAGE_PAGE_LIMIT)
  const data = await aiRequest<{
    messages?: AiMessage[]
    has_more?: boolean
    total?: number
  }>({
    method: 'get',
    url: `/ai/conversations/${oid(conversationOid)}/messages`,
    params: {
      after_seq: options.afterSeq ?? 0,
      limit
    }
  })
  return {
    messages: Array.isArray(data?.messages) ? data.messages : [],
    has_more: data?.has_more === true,
    total: Number(data?.total ?? 0)
  }
}

/* ---------------------------------------------------------------------------
   后台执行（run）
   --------------------------------------------------------------------------- */

/**
 * 提交一次后台执行。
 *
 * 后端在一个事务里写用户消息与 run 行，然后立刻 202 返回，不等执行完。
 * `clientMsgId` 是幂等键：重试同一个键会拿回同一个 run，不会重复写消息、
 * 也不会重复计费。
 */
export async function submitRun(
  conversationOid: string,
  content: string,
  options: { model?: string; clientMsgId?: string } = {}
): Promise<AiRunSubmitResult> {
  return aiRequest<AiRunSubmitResult>({
    method: 'post',
    url: `/ai/conversations/${oid(conversationOid)}/runs`,
    data: {
      content,
      ...(options.model ? { model: options.model } : {}),
      ...(options.clientMsgId ? { client_msg_id: options.clientMsgId } : {})
    }
  })
}

/**
 * 取一次 run 的状态与增量事件。
 *
 * `afterRound` 之外的事件才回：轮询的载荷随轮次线性增长，而不是平方。
 */
export async function fetchRunDetail(runId: string, afterRound = 0): Promise<AiRunDetail> {
  const data = await aiRequest<{
    run?: AiRun
    events?: AiRunDetail['events']
    proposals?: AiRunDetail['proposals']
  }>({
    method: 'get',
    url: `/ai/runs/${oid(runId)}`,
    params: { after_round: afterRound }
  })
  return {
    run: data?.run ?? emptyRun(runId),
    events: Array.isArray(data?.events) ? data.events : [],
    proposals: Array.isArray(data?.proposals) ? data.proposals : []
  }
}

/** 中止一次 run：置 cancel_requested，worker 在轮边界收手。 */
export async function cancelRun(runId: string): Promise<{ status: string }> {
  return aiRequest<{ status: string }>({
    method: 'post',
    url: `/ai/runs/${oid(runId)}/cancel`
  })
}

/** 本人进行中的 run：进页面时先问一次，重连后接管进度。 */
export async function fetchActiveRuns(): Promise<AiRun[]> {
  const data = await aiRequest<{ runs?: AiRun[] }>({
    method: 'get',
    url: '/ai/runs',
    params: { active: 1 }
  })
  return Array.isArray(data?.runs) ? data.runs : []
}

/**
 * 当前用户最近的后台执行记录。历史列表由首页摘要使用，服务端只返回本人记录。
 */
export async function listRecentRuns(limit = 5): Promise<AiRun[]> {
  const safeLimit = Math.min(10, Math.max(1, Math.trunc(limit)))
  const data = await aiRequest<{ runs?: AiRun[] }>({
    method: 'get',
    url: '/ai/runs',
    params: { active: 0, limit: safeLimit }
  })
  return Array.isArray(data?.runs) ? data.runs : []
}

/** 形状不认识时给一个空 run：界面据此显示「未知状态」而不是崩掉。 */
function emptyRun(runId: string): AiRun {
  return {
    oid: runId,
    conversation_oid: '',
    status: 'interrupted',
    model: null,
    stop_reason: 'error',
    rounds: 0,
    error_code: null,
    error_message: null,
    result_message_oid: null,
    prompt_tokens: 0,
    completion_tokens: 0,
    total_tokens: 0,
    cancel_requested: false,
    created_at: '',
    started_at: null,
    finished_at: null
  }
}
