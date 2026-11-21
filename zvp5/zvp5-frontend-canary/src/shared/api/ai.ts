import { ApiError, kindOfStatus } from '@/shared/http/errors'
import { readToken, writeToken } from '@/shared/lib/storage'
import type {
  AiConversation,
  AiConversationPage,
  AiConversationStatus,
  AiLiveState,
  AiMessagePage,
  AiProposal,
  AiRiskAck,
  AiRun,
  AiRunDetail,
  AiSubmitRunResult
} from '@/types/domain'

/**
 * ZVAgent（`/api/ai/*`）的真实契约封装。
 *
 * 这一面**不走 axios**：run 事件流是 `text/event-stream`，axios 的 XHR 适配器
 * 拿不到增量字节；而且 AI 错误体是 `{"error":{"message","type","code"}}`，
 * 与仓库其余接口的 `detail` 不同，axios 拦截器会把 code 丢掉。
 *
 * 会话/消息/提案的**成功外壳**也不统一：会话与管理端点带 `{status,code,data}`，
 * 提案与 ack/unlock 是裸 JSON。这里按端点分别拆包，页面只拿领域对象。
 */

const API_BASE = import.meta.env.VITE_API_BASE || '/api'
const AUTH_EXPIRED_EVENT = 'zvp5:auth-expired'
const MAINTENANCE_EVENT = 'zvp5:maintenance'

interface AiErrorShape {
  message: string
  code: string
  type: string
}

function readEnvelope<T>(body: unknown): T {
  if (body && typeof body === 'object' && 'data' in body) {
    return (body as { data: T }).data
  }
  return body as T
}

function readError(body: unknown): AiErrorShape {
  if (body && typeof body === 'object') {
    const record = body as Record<string, unknown>
    const error = record.error
    if (error && typeof error === 'object') {
      const payload = error as Record<string, unknown>
      return {
        message: typeof payload.message === 'string' ? payload.message : '',
        code: typeof payload.code === 'string' ? payload.code : '',
        type: typeof payload.type === 'string' ? payload.type : ''
      }
    }
    const detail = record.detail
    if (typeof detail === 'string') {
      return { message: detail, code: detail, type: '' }
    }
    if (detail && typeof detail === 'object') {
      const payload = detail as Record<string, unknown>
      return {
        message: typeof payload.message === 'string' ? payload.message : '',
        code: typeof payload.code === 'string' ? payload.code : '',
        type: ''
      }
    }
  }
  return { message: '', code: '', type: '' }
}

async function readJsonSafe(response: Response): Promise<unknown> {
  try {
    return await response.json()
  } catch {
    return null
  }
}

function isAbort(cause: unknown): boolean {
  return (
    (cause as { name?: string } | null)?.name === 'AbortError' ||
    (cause as { name?: string } | null)?.name === 'CanceledError'
  )
}

async function aiFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers)
  const token = readToken()
  if (token && !headers.has('Authorization')) {
    headers.set('Authorization', `Bearer ${token}`)
  }
  if (init.body && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json')
  }
  if (!headers.has('Accept')) {
    headers.set('Accept', 'application/json')
  }

  let response: Response
  try {
    response = await fetch(`${API_BASE}${path}`, {
      credentials: 'include',
      ...init,
      headers
    })
  } catch (cause) {
    if (isAbort(cause)) throw cause
    throw new ApiError({
      message: '网络连接失败，请检查网络后重试',
      kind: 'network',
      code: 'NETWORK'
    })
  }

  if (response.status === 401) {
    writeToken('')
    window.dispatchEvent(new CustomEvent(AUTH_EXPIRED_EVENT))
  }

  if (!response.ok) {
    const body = await readJsonSafe(response)
    const info = readError(body)
    const maintenance =
      Boolean(body && typeof body === 'object') &&
      (body as Record<string, unknown>).maintenance === true
    if (maintenance) {
      window.dispatchEvent(new CustomEvent(MAINTENANCE_EVENT))
    }
    throw new ApiError({
      message: info.message || `请求失败（HTTP ${response.status}）`,
      status: response.status,
      kind: maintenance ? 'maintenance' : kindOfStatus(response.status),
      code: info.code,
      detail: body,
      maintenance
    })
  }

  return response
}

async function aiJson<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await aiFetch(path, init)
  const body = await readJsonSafe(response)
  return body as T
}

function newClientId(): string {
  try {
    return crypto.randomUUID()
  } catch {
    return `c-${Date.now()}-${Math.random().toString(16).slice(2)}`
  }
}

/* ------------------------------ 会话 ------------------------------ */

export async function listConversations(options: {
  status?: AiConversationStatus
  cursor?: string | null
  limit?: number
} = {}): Promise<AiConversationPage> {
  const query = new URLSearchParams()
  query.set('status', options.status ?? 'active')
  query.set('limit', String(options.limit ?? 50))
  if (options.cursor) query.set('cursor', options.cursor)
  const body = await aiJson<unknown>(`/ai/conversations?${query.toString()}`)
  return readEnvelope<AiConversationPage>(body)
}

export async function fetchConversation(oid: string): Promise<AiConversation> {
  const body = await aiJson<unknown>(`/ai/conversations/${encodeURIComponent(oid)}`)
  return readEnvelope<AiConversation>(body)
}

export async function createConversation(title?: string): Promise<AiConversation> {
  const body = await aiJson<unknown>('/ai/conversations', {
    method: 'POST',
    body: JSON.stringify({ mode: 'agent', ...(title ? { title } : {}) })
  })
  return readEnvelope<AiConversation>(body)
}

export async function updateConversation(
  oid: string,
  fields: { title?: string; status?: AiConversationStatus }
): Promise<AiConversation> {
  const body = await aiJson<unknown>(`/ai/conversations/${encodeURIComponent(oid)}`, {
    method: 'PATCH',
    body: JSON.stringify(fields)
  })
  return readEnvelope<AiConversation>(body)
}

export async function deleteConversation(oid: string): Promise<void> {
  await aiFetch(`/ai/conversations/${encodeURIComponent(oid)}`, { method: 'DELETE' })
}

export async function fetchMessages(
  oid: string,
  afterSeq = 0,
  limit = 100
): Promise<AiMessagePage> {
  const query = new URLSearchParams()
  query.set('after_seq', String(afterSeq))
  query.set('limit', String(limit))
  const body = await aiJson<unknown>(
    `/ai/conversations/${encodeURIComponent(oid)}/messages?${query.toString()}`
  )
  return readEnvelope<AiMessagePage>(body)
}

/* ------------------------------- run ------------------------------ */

export async function submitRun(
  conversationOid: string,
  content: string,
  clientMsgId = newClientId()
): Promise<AiSubmitRunResult> {
  const body = await aiJson<unknown>(
    `/ai/conversations/${encodeURIComponent(conversationOid)}/runs`,
    {
      method: 'POST',
      body: JSON.stringify({ content, client_msg_id: clientMsgId })
    }
  )
  return readEnvelope<AiSubmitRunResult>(body)
}

export async function listActiveRuns(): Promise<AiRun[]> {
  const body = await aiJson<unknown>('/ai/runs?active=1')
  const data = readEnvelope<{ runs?: AiRun[] }>(body)
  return Array.isArray(data?.runs) ? data.runs : []
}

export async function fetchRunDetail(
  runId: string,
  afterRound = 0,
  sinceMsgSeq?: number
): Promise<AiRunDetail> {
  const query = new URLSearchParams()
  query.set('after_round', String(afterRound))
  if (sinceMsgSeq !== undefined) query.set('since_msg_seq', String(sinceMsgSeq))
  const body = await aiJson<unknown>(
    `/ai/runs/${encodeURIComponent(runId)}?${query.toString()}`
  )
  return readEnvelope<AiRunDetail>(body)
}

export async function cancelRun(runId: string): Promise<string> {
  const body = await aiJson<unknown>(`/ai/runs/${encodeURIComponent(runId)}/cancel`, {
    method: 'POST'
  })
  const data = readEnvelope<{ status?: string }>(body)
  return typeof data?.status === 'string' ? data.status : ''
}

/* ----------------------------- 提案 ------------------------------ */

export async function listProposals(
  status = 'proposed',
  limit = 20
): Promise<AiProposal[]> {
  const query = new URLSearchParams()
  query.set('status', status)
  query.set('limit', String(limit))
  const body = await aiJson<unknown>(`/ai/agent/proposals?${query.toString()}`)
  const proposals =
    body && typeof body === 'object'
      ? (body as Record<string, unknown>).proposals
      : null
  return Array.isArray(proposals) ? (proposals as AiProposal[]) : []
}

export async function confirmProposal(oid: string, totpCode?: string): Promise<void> {
  const code = totpCode?.trim()
  await aiFetch(`/ai/agent/proposals/${encodeURIComponent(oid)}/confirm`, {
    method: 'POST',
    body: JSON.stringify(code ? { totp_code: code } : {})
  })
}

export async function rejectProposal(oid: string): Promise<void> {
  await aiFetch(`/ai/agent/proposals/${encodeURIComponent(oid)}/reject`, {
    method: 'POST'
  })
}

/* --------------------------- 解锁与须知 --------------------------- */

export async function unlockAgent(password: string): Promise<string> {
  const body = await aiJson<unknown>('/ai/agent/unlock', {
    method: 'POST',
    body: JSON.stringify({ password })
  })
  if (body && typeof body === 'object') {
    const value = (body as Record<string, unknown>).unlocked_until
    return typeof value === 'string' ? value : ''
  }
  return ''
}

export async function fetchRiskAck(): Promise<AiRiskAck> {
  const body = await aiJson<unknown>('/ai/agent/ack')
  return readAck(body)
}

export async function ackRisk(): Promise<AiRiskAck> {
  const body = await aiJson<unknown>('/ai/agent/ack', { method: 'POST' })
  return readAck(body)
}

function readAck(body: unknown): AiRiskAck {
  if (!body || typeof body !== 'object') return { acked: false, acked_at: null }
  const record = body as Record<string, unknown>
  return {
    acked: record.acked === true,
    acked_at: typeof record.acked_at === 'string' ? record.acked_at : null
  }
}

/* ----------------------------- 事件流 ----------------------------- */

export interface AiStreamSnapshotFrame {
  type: 'snapshot'
  seq: number
  run_id: string
  data: AiRunDetail
  live: Partial<AiLiveState> | null
}

export interface AiStreamAssistantFrame {
  type: 'assistant'
  seq: number
  run_id: string
  round_no: number
  stream_key: string
  content: string
}

export interface AiStreamToolFrame {
  type: 'tool'
  seq: number
  run_id: string
  round_no: number
  tool_call_id: string | null
  tool_name: string | null
  tool_kind: 'read' | 'write' | null
  status: 'pending' | 'success' | 'error'
  args_summary: string | null
  result_summary: string | null
}

export interface AiStreamTerminalFrame {
  type: 'terminal'
  seq: number
  run_id: string
  status: string
  stop_reason: string | null
  error_code: string | null
}

export interface AiStreamErrorFrame {
  type: 'error'
  seq: number
  run_id: string
  code: string
}

export interface AiRunStreamHandlers {
  onSnapshot?: (frame: AiStreamSnapshotFrame) => void
  onAssistant?: (frame: AiStreamAssistantFrame) => void
  onTool?: (frame: AiStreamToolFrame) => void
  onTerminal?: (frame: AiStreamTerminalFrame) => void
  onError?: (frame: AiStreamErrorFrame) => void
}

export interface AiRunStreamResult {
  completed: boolean
  aborted: boolean
  error: AiStreamErrorFrame | null
  requestId: string | null
}

function readFrame(raw: string): Record<string, unknown> | null {
  try {
    const value: unknown = JSON.parse(raw)
    return value && typeof value === 'object' && !Array.isArray(value)
      ? (value as Record<string, unknown>)
      : null
  } catch {
    return null
  }
}

function str(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

function num(value: unknown): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : 0
}

/**
 * 订阅一次 run 的 SSE 事件流。
 *
 * 首帧是完整 snapshot，`assistant` 是**累计文本**（不是 delta），终态前会补
 * 最终 snapshot，然后 `terminal` 与 `[DONE]`。HTTP 级失败（403/404/503）直接
 * 抛 ApiError，调用方据此回退到轮询；连接提前结束返回 `completed: false`。
 */
export async function streamRunEvents(
  runId: string,
  handlers: AiRunStreamHandlers,
  signal?: AbortSignal
): Promise<AiRunStreamResult> {
  let response: Response
  try {
    response = await aiFetch(`/ai/runs/${encodeURIComponent(runId)}/events`, {
      method: 'GET',
      headers: { Accept: 'text/event-stream' },
      signal
    })
  } catch (cause) {
    if (isAbort(cause)) {
      return { completed: false, aborted: true, error: null, requestId: null }
    }
    throw cause
  }

  const requestId = response.headers.get('X-AI-Request-Id')
  const body = response.body
  if (!body) {
    return { completed: false, aborted: false, error: null, requestId }
  }

  const reader = body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let completed = false
  let streamError: AiStreamErrorFrame | null = null

  const handleBlock = (block: string): void => {
    const data = block
      .split('\n')
      .filter((line) => line.startsWith('data:'))
      .map((line) => line.slice(5).trimStart())
      .join('\n')
    if (!data) return
    if (data === '[DONE]') {
      completed = true
      return
    }
    const frame = readFrame(data)
    if (!frame) return

    const type = str(frame.type)
    if (type === 'snapshot') {
      const detail = frame.data
      if (!detail || typeof detail !== 'object' || Array.isArray(detail)) return
      let live: Partial<AiLiveState> | null = null
      if (frame.live && typeof frame.live === 'object' && !Array.isArray(frame.live)) {
        live = frame.live as Partial<AiLiveState>
      }
      handlers.onSnapshot?.({
        type: 'snapshot',
        seq: num(frame.seq),
        run_id: str(frame.run_id),
        data: detail as AiRunDetail,
        live
      })
      return
    }
    if (type === 'assistant') {
      handlers.onAssistant?.({
        type: 'assistant',
        seq: num(frame.seq),
        run_id: str(frame.run_id),
        round_no: num(frame.round_no),
        stream_key: str(frame.stream_key),
        content: str(frame.content)
      })
      return
    }
    if (type === 'tool') {
      const status = str(frame.status)
      handlers.onTool?.({
        type: 'tool',
        seq: num(frame.seq),
        run_id: str(frame.run_id),
        round_no: num(frame.round_no),
        tool_call_id: str(frame.tool_call_id) || null,
        tool_name: str(frame.tool_name) || null,
        tool_kind:
          frame.tool_kind === 'read' || frame.tool_kind === 'write'
            ? frame.tool_kind
            : null,
        status: status === 'success' || status === 'error' ? status : 'pending',
        args_summary: typeof frame.args_summary === 'string' ? frame.args_summary : null,
        result_summary:
          typeof frame.result_summary === 'string' ? frame.result_summary : null
      })
      return
    }
    if (type === 'terminal') {
      completed = true
      handlers.onTerminal?.({
        type: 'terminal',
        seq: num(frame.seq),
        run_id: str(frame.run_id),
        status: str(frame.status),
        stop_reason: typeof frame.stop_reason === 'string' ? frame.stop_reason : null,
        error_code: typeof frame.error_code === 'string' ? frame.error_code : null
      })
      return
    }
    if (type === 'error') {
      streamError = {
        type: 'error',
        seq: num(frame.seq),
        run_id: str(frame.run_id),
        code: str(frame.code) || 'AI_RUN_EVENTS_FAILED'
      }
      handlers.onError?.(streamError)
    }
  }

  try {
    for (;;) {
      const chunk = await reader.read()
      if (chunk.value) {
        buffer += decoder.decode(chunk.value, { stream: true }).replace(/\r\n/g, '\n')
      }
      if (chunk.done) {
        buffer += decoder.decode()
      }
      let index = buffer.indexOf('\n\n')
      while (index >= 0) {
        handleBlock(buffer.slice(0, index))
        buffer = buffer.slice(index + 2)
        index = buffer.indexOf('\n\n')
      }
      if (chunk.done) {
        if (buffer.trim()) handleBlock(buffer)
        break
      }
    }
  } catch (cause) {
    if (signal?.aborted || isAbort(cause)) {
      return { completed: false, aborted: true, error: null, requestId }
    }
    throw cause
  } finally {
    reader.releaseLock?.()
  }

  return {
    completed: completed && streamError === null,
    aborted: false,
    error: streamError,
    requestId
  }
}
