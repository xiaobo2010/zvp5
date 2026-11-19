import type { AiAgentToolKind, AiRunDetail } from '@/../types/ai'
import { registerAiAbort } from './activeRuns'
import { handleUserBanned, isUserBannedCode } from './banSignal'
import { AiRequestError } from './errors'
import { aiFetchRaw, aiResponseError, isMaintenanceResponse } from './http'
import { parseSseChunk } from './stream'

/**
 * 智能体 run 的实时事件流（SSE）。
 *
 * 后端契约见 zvp5/docs/AI_CONVERSATION_DESIGN.md §13：
 * - 首帧一定是完整 snapshot；
 * - `assistant` 是**累计文本**，不是裸 delta；
 * - `tool` 是 ephemeral 的工具开始/结束；
 * - 终态前补最终 snapshot，然后 terminal，再 `[DONE]`；
 * - 多 worker 部署在首字节前直接 503，前端据此降级回轮询。
 *
 * 这里只负责「连接 + 解析 + 分发」，去重、消息补拉与轮询回退由 store 处理。
 */

export interface AiRunLiveAssistant {
  seq: number
  round_no: number
  stream_key: string
  content: string
}

export interface AiRunLiveTool {
  seq: number
  round_no: number
  tool_call_id: string | null
  tool_name: string | null
  tool_kind: AiAgentToolKind | null
  status: 'pending' | 'success' | 'error'
  args_summary?: string | null
  result_summary?: string | null
}

export interface AiRunLiveState {
  assistant: AiRunLiveAssistant[]
  tools: AiRunLiveTool[]
}

export interface AiRunSnapshotFrame {
  type: 'snapshot'
  seq: number
  run_id: string
  data: AiRunDetail
  live?: Partial<AiRunLiveState> | null
}

export interface AiRunAssistantFrame {
  type: 'assistant'
  seq: number
  run_id: string
  round_no: number
  stream_key: string
  content: string
}

export interface AiRunToolFrame {
  type: 'tool'
  seq: number
  run_id: string
  round_no: number
  tool_call_id: string | null
  tool_name: string | null
  tool_kind: AiAgentToolKind | null
  status: 'pending' | 'success' | 'error'
  args_summary?: string | null
  result_summary?: string | null
}

export interface AiRunTerminalFrame {
  type: 'terminal'
  seq: number
  run_id: string
  status: string
  stop_reason: string | null
  error_code: string | null
}

export interface AiRunErrorFrame {
  type: 'error'
  seq: number
  run_id: string
  code: string
}

export interface AiRunEventHandlers {
  onSnapshot?: (frame: AiRunSnapshotFrame) => void
  onAssistant?: (frame: AiRunAssistantFrame) => void
  onTool?: (frame: AiRunToolFrame) => void
  onTerminal?: (frame: AiRunTerminalFrame) => void
  onError?: (frame: AiRunErrorFrame) => void
}

export interface AiRunEventStreamResult {
  /** 收到 terminal 或 [DONE]；false 表示连接提前结束，调用方应回退轮询。 */
  completed: boolean
  /** 流内 error 帧；HTTP 级失败会直接抛 AiRequestError。 */
  error: AiRunErrorFrame | null
  requestId: string | null
}

/** 解析一帧；形状不认识时返回 null（不让一条坏帧打断整条流）。 */
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

function stringValue(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

function numberValue(value: unknown): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : 0
}

/**
 * 订阅一次 run 的事件流。
 *
 * 调用方传 signal 时，中止会走 fetch 的 AbortError；全局封禁扇出也会中止。
 * 正常终态返回 `completed: true`；网络断开 / 服务端 200 后提前关连接返回
 * `completed: false`，由调用方决定是否回退轮询。
 */
export async function streamRunEvents(
  runId: string,
  handlers: AiRunEventHandlers = {},
  options: { signal?: AbortSignal } = {}
): Promise<AiRunEventStreamResult> {
  const controller = new AbortController()
  const forwardAbort = () => controller.abort()
  if (options.signal) {
    if (options.signal.aborted) {
      controller.abort()
    } else {
      options.signal.addEventListener('abort', forwardAbort, { once: true })
    }
  }
  const unregister = registerAiAbort(() => controller.abort())

  try {
    const response = await aiFetchRaw(`/ai/runs/${encodeURIComponent(runId)}/events`, {
      method: 'GET',
      signal: controller.signal
    })

    if (!response.ok) {
      if (await isMaintenanceResponse(response)) {
        throw new AiRequestError({ status: 503, code: 'AI_MAINTENANCE', message: '系统维护中' })
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
      return { completed: false, error: null, requestId }
    }

    const reader = body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    let completed = false
    let streamError: AiRunErrorFrame | null = null

    const consume = (state: ReturnType<typeof parseSseChunk>): void => {
      for (const raw of state.events) {
        const frame = readFrame(raw)
        if (!frame) {
          continue
        }
        const type = stringValue(frame.type)
        if (type === 'snapshot') {
          const data = frame.data
          if (data && typeof data === 'object' && !Array.isArray(data)) {
            handlers.onSnapshot?.({
              type: 'snapshot',
              seq: numberValue(frame.seq),
              run_id: stringValue(frame.run_id),
              data: data as AiRunDetail,
              live:
                frame.live && typeof frame.live === 'object'
                  ? (frame.live as AiRunLiveState)
                  : null
            })
          }
          continue
        }
        if (type === 'assistant') {
          handlers.onAssistant?.({
            type: 'assistant',
            seq: numberValue(frame.seq),
            run_id: stringValue(frame.run_id),
            round_no: numberValue(frame.round_no),
            stream_key: stringValue(frame.stream_key),
            content: stringValue(frame.content)
          })
          continue
        }
        if (type === 'tool') {
          const status = stringValue(frame.status)
          handlers.onTool?.({
            type: 'tool',
            seq: numberValue(frame.seq),
            run_id: stringValue(frame.run_id),
            round_no: numberValue(frame.round_no),
            tool_call_id: stringValue(frame.tool_call_id) || null,
            tool_name: stringValue(frame.tool_name) || null,
            tool_kind:
              frame.tool_kind === 'read' || frame.tool_kind === 'write'
                ? frame.tool_kind
                : null,
            status:
              status === 'success' || status === 'error' ? status : 'pending',
            args_summary: typeof frame.args_summary === 'string' ? frame.args_summary : null,
            result_summary:
              typeof frame.result_summary === 'string' ? frame.result_summary : null
          })
          continue
        }
        if (type === 'terminal') {
          completed = true
          handlers.onTerminal?.({
            type: 'terminal',
            seq: numberValue(frame.seq),
            run_id: stringValue(frame.run_id),
            status: stringValue(frame.status),
            stop_reason: typeof frame.stop_reason === 'string' ? frame.stop_reason : null,
            error_code: typeof frame.error_code === 'string' ? frame.error_code : null
          })
          continue
        }
        if (type === 'error') {
          streamError = {
            type: 'error',
            seq: numberValue(frame.seq),
            run_id: stringValue(frame.run_id),
            code: stringValue(frame.code) || 'AI_RUN_EVENTS_FAILED'
          }
          // 流已经发出 200，封禁只能以流内 error 帧到达：与其它 AI 面共用同一套扇出。
          if (isUserBannedCode(streamError.code)) {
            handleUserBanned({ headers: response.headers })
          }
          handlers.onError?.(streamError)
        }
        // resync 不需要客户端动作：服务端紧接着会补一个 snapshot。
      }
    }

    try {
      for (;;) {
        const chunk = await reader.read()
        if (chunk.value) {
          buffer += decoder.decode(chunk.value, { stream: true })
        }
        if (chunk.done) {
          buffer += decoder.decode()
        }
        const state = parseSseChunk(buffer, '')
        buffer = state.buffer
        consume(state)
        if (state.done) {
          completed = true
          break
        }
        if (chunk.done) {
          // 上游可能不带结尾空行直接关连接：补一个空行再解析一次。
          const tail = parseSseChunk(`${buffer}\n\n`, '')
          consume(tail)
          if (tail.done) {
            completed = true
          }
          break
        }
      }
    } finally {
      reader.releaseLock?.()
    }

    return { completed: completed && !streamError, error: streamError, requestId }
  } finally {
    unregister()
    options.signal?.removeEventListener('abort', forwardAbort)
  }
}
