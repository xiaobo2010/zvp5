/**
 * AI Gateway chat API (OpenAI-compatible endpoints).
 *
 * These endpoints return raw OpenAI-style responses without zvp envelopes:
 * - Success: upstream JSON (e.g., { object: "list", data: [...] })
 * - Error: { error: { message, type, code } }
 */

import { API_BASE } from './http'

/* -------------------------------------------------------------------------- */
/* Types                                                                      */
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

export interface AiChatMessage {
  role: 'system' | 'user' | 'assistant'
  content: string
}

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
  /** Data frames parsed from this chunk (without "data:" prefix). */
  events: string[]
  /** Incomplete text to carry forward to next chunk. */
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
 * Parse SSE chunk incrementally.
 * Input is previous buffer + new chunk; output is events, new buffer, and done flag.
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

/**
 * Extract text delta from OpenAI-compatible JSON frame.
 * Tries delta.content, message.content, then text field.
 */
export function extractDelta(frame: string): string {
  let payload: unknown
  try {
    payload = JSON.parse(frame)
  } catch {
    return ''
  }
  if (!payload || typeof payload !== 'object') return ''

  const choices = (payload as { choices?: unknown }).choices
  if (!Array.isArray(choices) || !choices.length) return ''

  const first = choices[0]
  if (!first || typeof first !== 'object') return ''

  const record = first as Record<string, unknown>

  // Try delta.content (streaming)
  const delta = record.delta
  if (delta && typeof delta === 'object') {
    const content = (delta as Record<string, unknown>).content
    if (typeof content === 'string') return content
  }

  // Try message.content (non-streaming or some providers)
  const message = record.message
  if (message && typeof message === 'object') {
    const content = (message as Record<string, unknown>).content
    if (typeof content === 'string') return content
  }

  // Try text field (legacy or alt format)
  if (typeof record.text === 'string') return record.text

  return ''
}

/* -------------------------------------------------------------------------- */
/* HTTP helpers                                                               */
/* -------------------------------------------------------------------------- */

function authToken(): string {
  return localStorage.getItem('token') ?? ''
}

function buildError(status: number, body: unknown, requestId: string | null): AiChatError {
  if (body && typeof body === 'object') {
    const error = (body as { error?: unknown }).error
    if (error && typeof error === 'object') {
      const e = error as { message?: unknown; code?: unknown; type?: unknown }
      const message = typeof e.message === 'string' ? e.message : `请求失败（${status}）`
      const code = typeof e.code === 'string' ? e.code : typeof e.type === 'string' ? e.type : ''
      return { status, code, message, requestId }
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
/* Public API                                                                 */
/* -------------------------------------------------------------------------- */

/**
 * List available models for the current user.
 * Returns OpenAI-style { object: "list", data: [...] } without envelope.
 */
export async function listModels(): Promise<AiModelList> {
  return aiFetchJson<AiModelList>('/ai/v1/models', { method: 'GET' })
}

export interface StreamChatOptions {
  model: string
  messages: AiChatMessage[]
  signal?: AbortSignal
  onDelta?: (delta: string) => void
}

export interface StreamChatResult {
  text: string
  done: boolean
  interrupted: boolean
  requestId: string | null
}

/**
 * Stream chat completion via SSE.
 * Throws AiChatError on failure.
 */
export async function streamChatCompletion(options: StreamChatOptions): Promise<StreamChatResult> {
  const response = await aiFetch('/ai/v1/chat/completions', {
    method: 'POST',
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
    return { text: '', done: false, interrupted: true, requestId }
  }

  const reader = body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let text = ''
  let done = false

  try {
    for (;;) {
      const chunk = await reader.read()
      if (chunk.value) {
        const decoded = decoder.decode(chunk.value, { stream: true })
        const result = parseSseChunk(buffer, decoded)
        buffer = result.buffer

        for (const frame of result.events) {
          const delta = extractDelta(frame)
          if (delta) {
            text += delta
            options.onDelta?.(delta)
          }
        }

        if (result.done) {
          done = true
          break
        }
      }

      if (chunk.done) break
    }
  } finally {
    reader.releaseLock?.()
  }

  return { text, done, interrupted: !done, requestId }
}

/**
 * Non-streaming chat completion.
 * Returns upstream JSON; extract text with readCompletionText.
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
 * Extract text from non-streaming completion response.
 * Handles message.content, text, or returns empty string.
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
