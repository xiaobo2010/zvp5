import type { AiChatMessage } from '@/../types/ai'
import { AiRequestError } from './errors'
import { aiFetchRaw, aiResponseError, isMaintenanceResponse } from './http'

/**
 * OpenAI 兼容面的流式客户端。
 *
 * 走 fetch 而不是 axios：axios 实例有 12 秒超时，流式回答必然被腰斩，
 * 而「回答到一半被掐断」在网关语义里等于失败。
 *
 * SSE 帧解析抽成纯函数（parseSseChunk），网络层只负责喂增量：
 * 一帧可能被 TCP 切成多段，也可能多帧挤在一段里，纯函数处理这两种情况，
 * 单测不必碰网络。
 */

export interface SseParseResult {
  /** 本次增量解析出的 data 帧（已去掉 "data:" 前缀）。 */
  events: string[]
  /** 尚未构成完整帧的残留文本，下一次增量继续拼接。 */
  buffer: string
  /** 是否收到 [DONE]。 */
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
    // 以冒号开头的行是注释，忽略。
    if (!line || line.startsWith(':')) {
      continue
    }
    const colon = line.indexOf(':')
    if (colon < 0) {
      if (line === 'data') {
        parts.push('')
      }
      continue
    }
    if (line.slice(0, colon) !== 'data') {
      continue
    }
    let value = line.slice(colon + 1)
    if (value.startsWith(' ')) {
      value = value.slice(1)
    }
    parts.push(value)
  }
  if (parts.length === 0) {
    return null
  }
  return parts.join('\n')
}

/**
 * 增量解析 SSE 文本。
 *
 * 输入是「上次的残留 buffer + 本次新增的 chunk」，输出是本次解析出的事件、
 * 新的残留 buffer 与是否收到 [DONE]。非 data 行（event / id / 注释）一律忽略。
 */
export function parseSseChunk(buffer: string, chunk: string): SseParseResult {
  const text = buffer + chunk
  const events: string[] = []
  let rest = text
  let done = false

  for (;;) {
    const boundary = findBoundary(rest)
    if (!boundary) {
      break
    }
    const block = rest.slice(0, boundary.start)
    rest = rest.slice(boundary.end)
    const data = extractData(block)
    if (data === null) {
      continue
    }
    if (data === '[DONE]') {
      done = true
      break
    }
    events.push(data)
  }

  return { events, buffer: done ? '' : rest, done }
}

/** 从一帧 OpenAI 兼容 JSON 里取出增量文本；形状不认识就当没有内容。 */
export function extractDelta(frame: string): string {
  let payload: unknown
  try {
    payload = JSON.parse(frame)
  } catch {
    return ''
  }
  if (!payload || typeof payload !== 'object') {
    return ''
  }
  const choices = (payload as { choices?: unknown }).choices
  if (!Array.isArray(choices) || choices.length === 0) {
    return ''
  }
  const first = choices[0]
  if (!first || typeof first !== 'object') {
    return ''
  }
  const record = first as Record<string, unknown>
  const delta = record.delta
  if (delta && typeof delta === 'object') {
    const content = (delta as Record<string, unknown>).content
    if (typeof content === 'string') {
      return content
    }
  }
  const message = record.message
  if (message && typeof message === 'object') {
    const content = (message as Record<string, unknown>).content
    if (typeof content === 'string') {
      return content
    }
  }
  if (typeof record.text === 'string') {
    return record.text
  }
  return ''
}

export interface AiStreamOptions {
  model: string
  messages: AiChatMessage[]
  signal?: AbortSignal
  /** 每收到一段增量文本回调一次。 */
  onDelta?: (delta: string) => void
}

export interface AiStreamResult {
  text: string
  /** 是否收到 [DONE]。 */
  done: boolean
  /** 流已结束但没收到 [DONE]：回答被截断。 */
  interrupted: boolean
  requestId: string | null
}

/**
 * 发起一次流式对话。
 *
 * 失败一律抛 AiRequestError（带 status / code / 请求编号 / 退避与额度头）；
 * 调用方自己决定怎么呈现。
 */
export async function streamChatCompletion(options: AiStreamOptions): Promise<AiStreamResult> {
  const payload = {
    model: options.model,
    messages: options.messages,
    stream: true
  }

  const response = await aiFetchRaw('/ai/v1/chat/completions', {
    method: 'POST',
    body: JSON.stringify(payload),
    signal: options.signal
  })

  if (!response.ok) {
    if (await isMaintenanceResponse(response)) {
      throw new AiRequestError({
        status: 503,
        code: 'AI_MAINTENANCE',
        message: '系统维护中'
      })
    }
    let body: unknown = null
    try {
      body = await response.json()
    } catch {
      body = null
    }
    throw aiResponseError(response, body)
  }

  const requestId = response.headers.get('X-AI-Request-Id')
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
        buffer += decoder.decode(chunk.value, { stream: true })
      }
      const state = parseSseChunk(buffer, '')
      buffer = state.buffer
      for (const frame of state.events) {
        const delta = extractDelta(frame)
        if (delta) {
          text += delta
          options.onDelta?.(delta)
        }
      }
      if (state.done) {
        done = true
        break
      }
      if (chunk.done) {
        break
      }
    }
  } finally {
    reader.releaseLock?.()
  }

  return { text, done, interrupted: !done, requestId }
}
