import type { AiChatMessage } from '@/../types/ai'
import { AiRequestError } from './errors'
import { AI_SCENE_HEADER, aiFetchRaw, aiResponseError, isMaintenanceResponse } from './http'
import type { AiChatScene } from './chat'

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
  const record = firstChoice(frame)
  if (!record) {
    return ''
  }
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

/**
 * 从同一帧里取出思考增量（上游的 ``reasoning_content``）。
 *
 * 缺失 / 类型不对回 ``undefined``：绝大多数帧没有思考内容，这不是异常；
 * 与 ``extractDelta`` 分开是因为两条流要各自累计、分别渲染。
 */
export function extractReasoningDelta(frame: string): string | undefined {
  const record = firstChoice(frame)
  if (!record) {
    return undefined
  }
  const delta = record.delta
  if (!delta || typeof delta !== 'object') {
    return undefined
  }
  const reasoning = (delta as Record<string, unknown>).reasoning_content
  if (typeof reasoning === 'string') {
    return reasoning
  }
  return undefined
}

/** 取 choices[0]；帧坏掉或没有 choices 时给 null。 */
function firstChoice(frame: string): Record<string, unknown> | null {
  let payload: unknown
  try {
    payload = JSON.parse(frame)
  } catch {
    return null
  }
  if (!payload || typeof payload !== 'object') {
    return null
  }
  const choices = (payload as { choices?: unknown }).choices
  if (!Array.isArray(choices) || choices.length === 0) {
    return null
  }
  const first = choices[0]
  if (!first || typeof first !== 'object') {
    return null
  }
  return first as Record<string, unknown>
}

export interface AiStreamOptions {
  model: string
  messages: AiChatMessage[]
  signal?: AbortSignal
  /** 审计用的场景名（后端落 PURPOSE）；不传 = 来源未知。 */
  scene?: AiChatScene
  /** 每收到一段增量文本回调一次。 */
  onDelta?: (delta: string) => void
  /** 每收到一段思考增量（reasoning_content）回调一次；没有该字段的帧不回调。 */
  onReasoningDelta?: (delta: string) => void
}

/** 流开始后上游才失败时，后端写在流里的错误帧。 */
export interface AiStreamError {
  code: string
  message: string
}

export interface AiStreamResult {
  text: string
  /** 思考过程全文（reasoning_content 累计）；上游没给就是空串或缺失。 */
  reasoning?: string
  /** 是否收到 [DONE]。 */
  done: boolean
  /** 流已结束但没收到 [DONE]：回答被截断。 */
  interrupted: boolean
  /** 流内错误帧（明确失败，不是截断）。 */
  error: AiStreamError | null
  requestId: string | null
}

/**
 * 从一帧里取出后端写入的 ``{"error": {...}}``；不是错误帧返回 null。
 *
 * 流式响应头已经发了 200，上游失败（4xx/5xx 往往在首个字节前才抛）只能这样
 * 报给客户端。形状与 OpenAI 面一致：``{error: {code, message}}``。
 */
export function extractStreamError(frame: string): AiStreamError | null {
  let payload: unknown
  try {
    payload = JSON.parse(frame)
  } catch {
    return null
  }
  if (!payload || typeof payload !== 'object') {
    return null
  }
  const error = (payload as { error?: unknown }).error
  if (!error || typeof error !== 'object') {
    return null
  }
  const record = error as Record<string, unknown>
  const code = typeof record.code === 'string' ? record.code.trim() : ''
  const message = typeof record.message === 'string' ? record.message.trim() : ''
  if (!code && !message) {
    return null
  }
  // 没给码时归到通用上游错误：页面按码分支，空码只会落到「未知错误」。
  return { code: code || 'AI_UPSTREAM_ERROR', message }
}

/**
 * 发起一次流式对话。
 *
 * 失败一律抛 AiRequestError（带 status / code / 请求编号 / 退避与额度头）；
 * 调用方自己决定怎么呈现。已经发出 200 之后的失败不抛，走返回值里的 error。
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
    signal: options.signal,
    // 审计元数据，不进请求体：后端读 X-AI-Scene 落 AI_CALL_LOGS.PURPOSE。
    ...(options.scene ? { headers: { [AI_SCENE_HEADER]: options.scene } } : {})
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
    // 200 + 空 body 不是「截断」而是明确失败（后端以前吞异常就会这样）。
    // 现在后端会把失败写成流内 error 帧；这里只是兜住新的或异常的网关。
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
        // 冲刷解码器里剩下的多字节字符，否则末尾一个汉字会被丢掉。
        buffer += decoder.decode()
      }
      const state = parseSseChunk(buffer, '')
      buffer = state.buffer
      consume(state)
      if (state.done) {
        done = true
        break
      }
      // 明确失败就收尾：后端不会再补内容。
      if (error) {
        break
      }
      if (chunk.done) {
        // 上游可能在 ``[DONE]`` 后不带空行直接关连接：帧解析要求空行收尾，
        // 于是 ``done`` 永远是 false、结果被误判成截断。补一个空行再解析一次。
        const tail = parseSseChunk(`${buffer}\n\n`, '')
        buffer = tail.buffer
        consume(tail)
        if (tail.done) {
          done = true
        }
        break
      }
    }
  } finally {
    reader.releaseLock?.()
  }

  return { text, reasoning, done, interrupted: !done && !error, error, requestId }
}
