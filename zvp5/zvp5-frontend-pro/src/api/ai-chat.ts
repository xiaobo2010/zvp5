/**
 * AI Gateway chat API for the standalone Pro app.
 *
 * Two backend surfaces are covered here and they do **not** share a protocol:
 *
 * 1. OpenAI-compatible (`/ai/v1/models`, `/ai/v1/chat/completions`): the body is
 *    the upstream JSON, errors are `{ error: { message, type, code } }`, and the
 *    streaming response is raw SSE. This endpoint is stateless by design — it
 *    never reads or writes a persisted conversation.
 * 2. Persisted conversations (`/ai/conversations`, `/ai/conversations/{oid}/messages`):
 *    the legacy zvp envelope (`{ status, code, data }`, list responses add
 *    `metadata.size`); failures surface as `ApiError` from `http.ts` (the
 *    conversation routes use `{ error: { code, message } }` like the OpenAI
 *    face, so status mapping in the panel is the stable fallback).
 *
 * Contracts are copied field-for-field from the main frontend's
 * `src/api/ai/{chat,stream,conversations}.ts`; the Pro app does not invent
 * field names. Conversations are agent-mode only on the server, so `mode` is
 * kept on the wire shape for legacy rows but new sessions are created as
 * `'agent'`.
 */

import { API_BASE, ApiError, buildQuery } from './http'

/* -------------------------------------------------------------------------- */
/* OpenAI-compatible shapes                                                   */
/* -------------------------------------------------------------------------- */

export interface AiModel {
  id: string
  object?: string
  created?: number
  owned_by?: string
  [key: string]: unknown
}

export interface AiModelList {
  object: string
  data: AiModel[]
}

export type AiChatRole = 'system' | 'user' | 'assistant'

export interface AiChatMessage {
  role: AiChatRole
  content: string
}

/** Audit-only scene header (the backend stores it as `AI_CALL_LOGS.PURPOSE`). */
export type AiChatScene = 'chat' | 'markdown-assistant'

export const AI_SCENE_HEADER = 'X-AI-Scene'

export interface AiChatError {
  status: number
  code: string
  message: string
  requestId: string | null
}

/* -------------------------------------------------------------------------- */
/* SSE parsing                                                                */
/* -------------------------------------------------------------------------- */

export interface SseParseResult {
  /** Data frames parsed from this chunk (without the "data:" prefix). */
  events: string[]
  /** Incomplete text to carry forward to the next chunk. */
  buffer: string
  /** Whether [DONE] was received. */
  done: boolean
}

const BOUNDARIES = ['\r\n\r\n', '\n\n', '\r\r']

function findBoundary(text: string): { start: number; end: number } | null {
  let found: { start: number; end: number } | null = null
  for (const pattern of BOUNDARIES) {
    const index = text.indexOf(pattern)
    if (index >= 0 && (!found || index < found.start)) {
      found = { start: index, end: index + pattern.length }
    }
  }
  return found
}

function extractData(block: string): string | null {
  const lines = block.split(/\r\n|\n|\r/)
  const parts: string[] = []
  for (const line of lines) {
    // Lines starting with ":" are SSE comments (heartbeats) — ignore them.
    if (!line || line.startsWith(':')) continue
    const colon = line.indexOf(':')
    if (colon < 0) {
      if (line === 'data') parts.push('')
      continue
    }
    if (line.slice(0, colon) !== 'data') continue
    let value = line.slice(colon + 1)
    if (value.startsWith(' ')) value = value.slice(1)
    parts.push(value)
  }
  return parts.length ? parts.join('\n') : null
}

/**
 * Parse SSE text incrementally.
 *
 * Input is the previous buffer + the newly decoded chunk; output is the events
 * completed in this pass, the new buffer, and whether `[DONE]` arrived.
 */
export function parseSseChunk(buffer: string, chunk: string): SseParseResult {
  const text = buffer + chunk
  const events: string[] = []
  let rest = text
  let done = false

  for (;;) {
    const boundary = findBoundary(rest)
    if (!boundary) break
    const block = rest.slice(0, boundary.start)
    rest = rest.slice(boundary.end)
    const data = extractData(block)
    if (data === null) continue
    if (data === '[DONE]') {
      done = true
      break
    }
    events.push(data)
  }

  return { events, buffer: done ? '' : rest, done }
}

/** First `choices[0]` object, or null when the frame is malformed. */
function firstChoice(frame: string): Record<string, unknown> | null {
  let payload: unknown
  try {
    payload = JSON.parse(frame)
  } catch {
    return null
  }
  if (!payload || typeof payload !== 'object') return null

  const choices = (payload as { choices?: unknown }).choices
  if (!Array.isArray(choices) || !choices.length) return null

  const first = choices[0]
  if (!first || typeof first !== 'object') return null
  return first as Record<string, unknown>
}

/**
 * Extract the text delta from an OpenAI-compatible JSON frame.
 * Tries delta.content, message.content, then the legacy `text` field.
 */
export function extractDelta(frame: string): string {
  const record = firstChoice(frame)
  if (!record) return ''

  const delta = record.delta
  if (delta && typeof delta === 'object') {
    const content = (delta as Record<string, unknown>).content
    if (typeof content === 'string') return content
  }

  const message = record.message
  if (message && typeof message === 'object') {
    const content = (message as Record<string, unknown>).content
    if (typeof content === 'string') return content
  }

  if (typeof record.text === 'string') return record.text
  return ''
}

/** Reasoning delta (`reasoning_content`) from the same frame, or undefined. */
export function extractReasoningDelta(frame: string): string | undefined {
  const record = firstChoice(frame)
  if (!record) return undefined
  const delta = record.delta
  if (!delta || typeof delta !== 'object') return undefined
  const reasoning = (delta as Record<string, unknown>).reasoning_content
  return typeof reasoning === 'string' ? reasoning : undefined
}

export interface AiStreamError {
  code: string
  message: string
}

/**
 * Extract the backend-written `{error: {code, message}}` frame.
 *
 * A streaming response has already sent HTTP 200, so an upstream failure can
 * only arrive as a frame. Returns null for non-error frames.
 */
export function extractStreamError(frame: string): AiStreamError | null {
  let payload: unknown
  try {
    payload = JSON.parse(frame)
  } catch {
    return null
  }
  if (!payload || typeof payload !== 'object') return null

  const error = (payload as { error?: unknown }).error
  if (!error || typeof error !== 'object') return null

  const record = error as Record<string, unknown>
  const code = typeof record.code === 'string' ? record.code.trim() : ''
  const message = typeof record.message === 'string' ? record.message.trim() : ''
  if (!code && !message) return null
  // An empty code would fall through to "unknown error" in the UI.
  return { code: code || 'AI_UPSTREAM_ERROR', message }
}

/* -------------------------------------------------------------------------- */
/* HTTP helpers                                                               */
/* -------------------------------------------------------------------------- */

function authToken(): string {
  return localStorage.getItem('token') ?? ''
}

function buildError(status: number, body: unknown, requestId: string | null): AiChatError {
  if (body && typeof body === 'object') {
    const record = body as { error?: unknown; detail?: unknown; message?: unknown }
    const error = record.error
    if (error && typeof error === 'object') {
      const e = error as { message?: unknown; code?: unknown; type?: unknown }
      const message = typeof e.message === 'string' ? e.message : `请求失败（${status}）`
      const code = typeof e.code === 'string' ? e.code : typeof e.type === 'string' ? e.type : ''
      return { status, code, message, requestId }
    }
    // The conversation routes answer with `{detail: "<CODE>"}`.
    if (typeof record.detail === 'string' && record.detail) {
      return { status, code: record.detail, message: record.detail, requestId }
    }
    if (typeof record.message === 'string' && record.message) {
      return { status, code: '', message: record.message, requestId }
    }
  }
  return {
    status,
    code: '',
    message: `请求失败（${status}）`,
    requestId
  }
}

async function aiFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers)
  if (!headers.has('Content-Type') && init.body && typeof init.body === 'string') {
    headers.set('Content-Type', 'application/json')
  }
  const token = authToken()
  if (token && !headers.has('Authorization')) {
    headers.set('Authorization', `Bearer ${token}`)
  }

  return fetch(`${API_BASE}${path}`, {
    ...init,
    headers,
    credentials: 'include'
  })
}

async function aiFetchJson<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await aiFetch(path, init)
  const requestId = response.headers.get('X-AI-Request-Id')

  if (!response.ok) {
    let body: unknown = null
    try {
      body = await response.json()
    } catch {
      // ignore
    }
    throw buildError(response.status, body, requestId)
  }

  return response.json() as Promise<T>
}

/* -------------------------------------------------------------------------- */
/* Envelope helper for conversation/run routes                                */
/* -------------------------------------------------------------------------- */

type LooseRecord = Record<string, unknown>

function asLooseRecord(value: unknown): LooseRecord | null {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as LooseRecord)
    : null
}

/** Parse the stable code from either zvp `{detail}` or AI `{error}` bodies. */
function parseCode(body: unknown): string {
  if (typeof body === 'string') {
    const text = body.trim()
    return /^[A-Z0-9_]+$/.test(text) && text.length <= 80 ? text : ''
  }
  const record = asLooseRecord(body)
  if (!record) return ''

  const detail = record.detail
  if (typeof detail === 'string') return detail.trim()
  if (Array.isArray(detail)) return 'AI_INVALID_REQUEST'
  const detailRecord = asLooseRecord(detail)
  if (detailRecord && typeof detailRecord.code === 'string' && detailRecord.code.trim()) {
    return detailRecord.code.trim()
  }

  const error = record.error
  if (typeof error === 'string') return error.trim()
  const errorRecord = asLooseRecord(error)
  if (errorRecord && typeof errorRecord.code === 'string' && errorRecord.code.trim()) {
    return errorRecord.code.trim()
  }
  return ''
}

/** Parse the human message from either zvp `{detail}` or AI `{error}` bodies. */
function parseMessage(body: unknown): string {
  const record = asLooseRecord(body)
  if (!record) return ''

  const detail = record.detail
  if (typeof detail === 'string' && detail && !/^[A-Z0-9_]+$/.test(detail)) {
    return detail
  }
  const detailRecord = asLooseRecord(detail)
  if (detailRecord && typeof detailRecord.message === 'string') {
    return detailRecord.message
  }

  const error = record.error
  if (typeof error === 'string') return error
  const errorRecord = asLooseRecord(error)
  if (errorRecord && typeof errorRecord.message === 'string') {
    return errorRecord.message
  }
  if (typeof record.message === 'string') return record.message
  return ''
}

interface EnvelopeResult<T> {
  data: T
  size: number
}

/**
 * Request a persisted-conversation/run endpoint and unwrap its envelope.
 *
 * The shared `apiRequest` helper intentionally drops the AI `{error: {code}}`
 * body, but the run routes need the code to branch (`AI_AGENT_UNLOCK_REQUIRED`
 * opens the password dialog; `AI_RUN_CONFLICT` disables the composer). This
 * local helper keeps the same Bearer/envelope conventions while preserving the
 * code.
 */
async function aiEnvelopeRequest<T>(path: string, init: RequestInit = {}): Promise<EnvelopeResult<T>> {
  const headers = new Headers(init.headers)
  if (!headers.has('Content-Type') && init.body && typeof init.body === 'string') {
    headers.set('Content-Type', 'application/json')
  }
  const token = authToken()
  if (token && !headers.has('Authorization')) {
    headers.set('Authorization', `Bearer ${token}`)
  }

  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers,
    credentials: 'include'
  })

  let payload: unknown = null
  const text = await response.text()
  if (text) {
    try {
      payload = JSON.parse(text)
    } catch {
      payload = text
    }
  }

  if (!response.ok) {
    if (response.status === 401) {
      if (token && authToken() === token) localStorage.removeItem('token')
      throw new ApiError('登录状态已失效，请重新登录。', 401, 'UNAUTHORIZED')
    }
    const code = parseCode(payload) || `AI_HTTP_${response.status}`
    const message = parseMessage(payload) || code
    throw new ApiError(message, response.status, code)
  }

  const record = asLooseRecord(payload)
  if (record && record.status === 'error') {
    const code = parseCode(payload) || 'AI_REQUEST_FAILED'
    throw new ApiError(parseMessage(payload) || code, response.status, code)
  }

  const data = record && 'data' in record ? (record.data as T) : (payload as T)
  const metadata = asLooseRecord(record?.metadata)
  const size = Number(metadata?.size)
  return { data, size: Number.isFinite(size) ? size : 0 }
}

/* -------------------------------------------------------------------------- */
/* Persisted conversations (wire shapes)                                      */
/* -------------------------------------------------------------------------- */

export type AiConversationMode = 'chat' | 'agent'
export type AiConversationStatus = 'active' | 'archived'
export type AiThinkingLevel = 'low' | 'medium' | 'high'
export type AiMessageRole = 'user' | 'assistant' | 'tool'

export interface AiConversationTokens {
  prompt: number
  completion: number
  total: number
}

export interface AiConversationContext {
  tokens: number
  /** Model context window; 0 when the backend could not determine it. */
  limit: number
  /** Occupancy ratio (0–1), clamped by the backend. */
  ratio: number
}

export interface AiConversationCompaction {
  upto_seq: number
  summarized: boolean
  degraded_at: string | null
}

/** A conversation row (list and detail share the same shape). */
export interface AiConversation {
  oid: string
  mode: AiConversationMode
  title: string
  /** Model locked at creation; null = server default. */
  model: string | null
  thinking?: AiThinkingLevel | null
  /** Read-only scene (`terminal` / `file_edit` / …); null = normal session. */
  scene?: string | null
  status: AiConversationStatus
  tokens: AiConversationTokens
  context: AiConversationContext
  compaction: AiConversationCompaction
  message_count: number
  created_at: string
  updated_at: string
}

export interface AiConversationPage {
  conversations: AiConversation[]
  /** Opaque cursor (base64 `updated_at|oid`); null = last page. */
  next_cursor: string | null
  /** This page's row count (`metadata.size`). */
  size: number
}

export interface AiMessageUsage {
  prompt_tokens: number | null
  completion_tokens: number | null
  total_tokens: number | null
  cache_read_tokens: number | null
  cache_write_tokens: number | null
}

export interface AiMessage {
  oid: string
  /** Monotonic within a conversation; `seq` is the incremental cursor. */
  seq: number
  role: AiMessageRole
  content: string
  reasoning?: string | null
  tool_calls?: unknown
  tool_call_id?: string | null
  tool_name?: string | null
  round_no?: number | null
  request_id?: string | null
  usage?: AiMessageUsage | null
  created_at: string
}

export interface AiMessagePage {
  messages: AiMessage[]
  /** The server filled a page: another `after_seq` fetch may follow. */
  has_more: boolean
  /** Total message count for the conversation (metadata column). */
  total: number
}

const CONVERSATION_LIST_LIMIT = 50
const CONVERSATION_LIST_LIMIT_MAX = 100
const MESSAGE_PAGE_LIMIT = 50
const MESSAGE_PAGE_LIMIT_MAX = 100

function clampLimit(limit: number, max: number, fallback: number): number {
  if (!Number.isFinite(limit)) return fallback
  return Math.min(max, Math.max(1, Math.trunc(limit)))
}

function oidPath(oid: string): string {
  return encodeURIComponent(oid)
}

/* -------------------------------------------------------------------------- */
/* Conversation API                                                           */
/* -------------------------------------------------------------------------- */

/**
 * Create a conversation. `mode` is fixed at creation and cannot be changed.
 *
 * The server currently only accepts `'agent'`; creating a legacy `'chat'`
 * session returns 409 `AI_CONVERSATION_MODE_MISMATCH`.
 */
export async function createConversation(
  mode: AiConversationMode,
  title?: string,
  model?: string
): Promise<AiConversation> {
  const { data } = await aiEnvelopeRequest<AiConversation>('/ai/conversations', {
    method: 'post',
    body: JSON.stringify({
      mode,
      ...(title ? { title } : {}),
      ...(model ? { model } : {})
    })
  })
  return data
}

/**
 * List the current user's conversations (`updated_at` descending, cursor page).
 *
 * The list deliberately omits message bodies; the transcript is only fetched
 * via `listMessages`.
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
  const query = buildQuery({
    status: options.status ?? 'active',
    limit,
    cursor: options.cursor ?? undefined
  })
  const result = await aiEnvelopeRequest<{
    conversations?: AiConversation[]
    next_cursor?: string | null
  }>(`/ai/conversations${query}`)
  const data = result.data
  const conversations = data?.conversations
  const nextCursor = data?.next_cursor
  return {
    conversations: Array.isArray(conversations) ? conversations : [],
    next_cursor: typeof nextCursor === 'string' && nextCursor ? nextCursor : null,
    size: result.size
  }
}

/** Fetch one conversation's detail (ownership is enforced server-side). */
export async function getConversation(conversationOid: string): Promise<AiConversation> {
  const { data } = await aiEnvelopeRequest<AiConversation>(
    `/ai/conversations/${oidPath(conversationOid)}`
  )
  return data
}

/**
 * Fetch messages with `seq > after_seq` (ascending).
 *
 * This is both the refresh and the polling path. `afterSeq: 0` starts from the
 * beginning; long conversations should request the tail window explicitly
 * (see `AiChatPanel`).
 */
export async function listMessages(
  conversationOid: string,
  options: { afterSeq?: number; limit?: number } = {}
): Promise<AiMessagePage> {
  const limit = clampLimit(
    options.limit ?? MESSAGE_PAGE_LIMIT,
    MESSAGE_PAGE_LIMIT_MAX,
    MESSAGE_PAGE_LIMIT
  )
  const query = buildQuery({
    after_seq: options.afterSeq ?? 0,
    limit
  })
  const { data } = await aiEnvelopeRequest<{
    messages?: AiMessage[]
    has_more?: boolean
    total?: number
  }>(`/ai/conversations/${oidPath(conversationOid)}/messages${query}`)
  const messages = data?.messages
  return {
    messages: Array.isArray(messages) ? messages : [],
    has_more: data?.has_more === true,
    total: Number(data?.total ?? 0)
  }
}

/* -------------------------------------------------------------------------- */
/* Runs (persisted send path)                                                 */
/* -------------------------------------------------------------------------- */

export type AiRunStatus =
  | 'queued'
  | 'running'
  | 'succeeded'
  | 'failed'
  | 'cancelled'
  | 'interrupted'

export type AiRunStopReason =
  | 'completed'
  | 'max_iterations'
  | 'token_budget'
  | 'timeout'
  | 'cancelled'
  | 'error'

export interface AiRun {
  oid: string
  conversation_oid: string
  status: AiRunStatus
  model: string | null
  stop_reason: AiRunStopReason | null
  rounds: number
  error_code: string | null
  error_message: string | null
  result_message_oid: string | null
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
  cancel_requested: boolean
  created_at: string
  started_at: string | null
  finished_at: string | null
}

export interface AiRunSubmitResult {
  run_id: string
  /** The user message written in the same transaction as the run. */
  message_oid: string
  status: 'queued'
  /** True when `client_msg_id` hit its idempotency key (no duplicate write/billing). */
  deduped?: boolean
}

export interface AiRunDetail {
  run: AiRun
  /** Incremental agent events (`round_no > after_round`); not rendered by Pro. */
  events: unknown[]
  /** Pending write-tool proposals; Pro is read-only and never confirms them. */
  proposals: unknown[]
  /** Incremental conversation messages (`seq > since_msg_seq`, ascending). */
  messages: AiMessage[]
}

/**
 * Submit one user message to a persisted agent conversation.
 *
 * The server writes the user message + a `queued` run in one transaction and
 * returns 202 immediately; the answer arrives through `fetchRunDetail`.
 * `clientMsgId` is the idempotency key: retrying the same key (for example
 * after the unlock prompt) never writes or bills twice.
 */
export async function submitRun(
  conversationOid: string,
  content: string,
  options: { clientMsgId?: string } = {}
): Promise<AiRunSubmitResult> {
  const { data } = await aiEnvelopeRequest<AiRunSubmitResult>(
    `/ai/conversations/${oidPath(conversationOid)}/runs`,
    {
      method: 'post',
      body: JSON.stringify({
        content,
        ...(options.clientMsgId ? { client_msg_id: options.clientMsgId } : {})
      })
    }
  )
  return data
}

/**
 * Fetch a run's status and the messages written since `sinceMsgSeq`.
 *
 * `afterRound` only returns events from later rounds, so polling payloads stay
 * linear instead of quadratic. The run view is the source of truth for the
 * terminal status; `messages` shares the exact `GET /messages` shape.
 */
export async function fetchRunDetail(
  runId: string,
  afterRound = 0,
  sinceMsgSeq?: number
): Promise<AiRunDetail> {
  const query = buildQuery({
    after_round: afterRound,
    ...(typeof sinceMsgSeq === 'number' ? { since_msg_seq: sinceMsgSeq } : {})
  })
  const { data } = await aiEnvelopeRequest<{
    run?: AiRun
    events?: unknown[]
    proposals?: unknown[]
    messages?: AiMessage[]
  }>(`/ai/runs/${oidPath(runId)}${query}`)
  const run = data?.run
  const events = data?.events
  const proposals = data?.proposals
  const messages = data?.messages
  return {
    run: run ?? emptyRun(runId),
    events: Array.isArray(events) ? events : [],
    proposals: Array.isArray(proposals) ? proposals : [],
    messages: Array.isArray(messages) ? messages : []
  }
}

/** Request cancellation; the worker stops at the next round/tool boundary. */
export async function cancelRun(runId: string): Promise<{ status: string }> {
  const { data } = await aiEnvelopeRequest<{ status?: string }>(
    `/ai/runs/${oidPath(runId)}/cancel`,
    { method: 'post' }
  )
  const status = data?.status
  return { status: typeof status === 'string' ? status : '' }
}

/** The current user's active runs, used to resume after a reload/switch. */
export async function fetchActiveRuns(): Promise<AiRun[]> {
  const { data } = await aiEnvelopeRequest<{ runs?: AiRun[] }>('/ai/runs?active=1')
  const runs = data?.runs
  return Array.isArray(runs) ? runs : []
}

/**
 * Unlock agent actions for 30 minutes with the account password.
 *
 * This is a server-side, per-user window (shared through Redis when configured):
 * once unlocked in the main app, the Pro console inherits it. The endpoint
 * returns bare JSON, so it uses the OpenAI-face helper rather than the
 * conversation envelope helper.
 */
export async function unlockAgent(password: string): Promise<string> {
  const body = await aiFetchJson<unknown>('/ai/agent/unlock', {
    method: 'POST',
    body: JSON.stringify({ password })
  })
  const record = asLooseRecord(body)
  if (!record) return ''
  return typeof record.unlocked_until === 'string' ? record.unlocked_until : ''
}

/** Shape-safe placeholder when the detail body is malformed. */
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

/* -------------------------------------------------------------------------- */
/* Public OpenAI-compatible API                                               */
/* -------------------------------------------------------------------------- */

/**
 * List available models for the current user.
 * Returns OpenAI-style `{ object: "list", data: [...] }` without an envelope.
 */
export async function listModels(): Promise<AiModelList> {
  return aiFetchJson<AiModelList>('/ai/v1/models', { method: 'GET' })
}

export interface StreamChatOptions {
  model: string
  messages: AiChatMessage[]
  signal?: AbortSignal
  /** Audit-only scene name; not part of the request body. */
  scene?: AiChatScene
  onDelta?: (delta: string) => void
  onReasoningDelta?: (delta: string) => void
}

export interface StreamChatResult {
  text: string
  /** Accumulated `reasoning_content`; empty when the upstream did not send it. */
  reasoning?: string
  /** Whether `[DONE]` was received. */
  done: boolean
  /** The stream ended without `[DONE]` and without an explicit error frame. */
  interrupted: boolean
  /** Explicit in-stream failure (HTTP 200 already sent). */
  error: AiStreamError | null
  requestId: string | null
}

/**
 * Stream a chat completion via SSE.
 *
 * Throws `AiChatError` on pre-stream failures; an abort surfaces as the native
 * `AbortError`, which callers treat as the user pressing "stop".
 */
export async function streamChatCompletion(options: StreamChatOptions): Promise<StreamChatResult> {
  const response = await aiFetch('/ai/v1/chat/completions', {
    method: 'POST',
    headers: {
      // Explicit SSE accept: some proxies/upstreams only stream when asked.
      Accept: 'text/event-stream',
      ...(options.scene ? { [AI_SCENE_HEADER]: options.scene } : {})
    },
    body: JSON.stringify({
      model: options.model,
      messages: options.messages,
      stream: true
    }),
    signal: options.signal
  })

  const requestId = response.headers.get('X-AI-Request-Id')

  if (!response.ok) {
    let body: unknown = null
    try {
      body = await response.json()
    } catch {
      // ignore
    }
    throw buildError(response.status, body, requestId)
  }

  const body = response.body
  if (!body) {
    // 200 with an empty body is an explicit failure, not a truncation.
    return {
      text: '',
      reasoning: '',
      done: false,
      interrupted: false,
      error: { code: 'AI_UPSTREAM_ERROR', message: '' },
      requestId
    }
  }

  const reader = body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let text = ''
  let reasoning = ''
  let done = false
  let error: AiStreamError | null = null

  const consume = (state: SseParseResult): void => {
    for (const frame of state.events) {
      const failure = extractStreamError(frame)
      if (failure) {
        error = failure
        continue
      }
      const thought = extractReasoningDelta(frame)
      if (thought) {
        reasoning += thought
        options.onReasoningDelta?.(thought)
      }
      const delta = extractDelta(frame)
      if (delta) {
        text += delta
        options.onDelta?.(delta)
      }
    }
  }

  try {
    for (;;) {
      const chunk = await reader.read()
      if (chunk.value) {
        buffer += decoder.decode(chunk.value, { stream: true })
      }
      if (chunk.done) {
        // Flush a trailing multi-byte character before the final parse.
        buffer += decoder.decode()
      }

      const state = parseSseChunk(buffer, '')
      buffer = state.buffer
      consume(state)

      if (state.done) {
        done = true
        break
      }
      // An explicit error means the upstream will not send more content.
      if (error) break
      if (chunk.done) {
        // Some upstreams close right after `[DONE]` without the blank line the
        // parser needs. Add one and parse the tail once more.
        const tail = parseSseChunk(`${buffer}\n\n`, '')
        buffer = tail.buffer
        consume(tail)
        if (tail.done) done = true
        break
      }
    }
  } finally {
    reader.releaseLock?.()
  }

  return { text, reasoning, done, interrupted: !done && !error, error, requestId }
}

/**
 * Non-streaming chat completion.
 * Returns upstream JSON; extract text with `readCompletionText`.
 */
export async function createChatCompletion(
  model: string,
  messages: AiChatMessage[]
): Promise<Record<string, unknown>> {
  return aiFetchJson<Record<string, unknown>>('/ai/v1/chat/completions', {
    method: 'POST',
    body: JSON.stringify({ model, messages, stream: false })
  })
}

/**
 * Extract text from a non-streaming completion response.
 * Handles message.content, text, or returns an empty string.
 */
export function readCompletionText(payload: unknown): string {
  if (!payload || typeof payload !== 'object') return ''

  const choices = (payload as { choices?: unknown }).choices
  if (!Array.isArray(choices) || !choices.length) return ''

  const first = choices[0]
  if (!first || typeof first !== 'object') return ''

  const message = (first as Record<string, unknown>).message
  if (message && typeof message === 'object') {
    const content = (message as Record<string, unknown>).content
    if (typeof content === 'string') return content
  }

  const text = (first as Record<string, unknown>).text
  if (typeof text === 'string') return text

  return ''
}
