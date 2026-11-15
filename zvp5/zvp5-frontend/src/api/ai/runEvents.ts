import type {
  AiRunAssistantLive,
  AiRunDetail,
  AiRunStatus,
  AiRunStopReason,
  AiRunToolLive,
  AiRunToolLiveStatus
} from '@/../types/ai'
import { registerAiAbort } from './activeRuns'
import { AiRequestError } from './errors'
import { aiFetchRaw, aiResponseError } from './http'
import { parseSseChunk } from './stream'

/**
 * run 的实时事件流客户端（`GET /ai/runs/{run_id}/events`）。
 *
 * 为什么不用 EventSource：它不能带 `Authorization` 头，也不能自定义鉴权；
 * 所以走 fetch + 手读 body，SSE 帧解析复用 `stream.ts` 的 `parseSseChunk`
 * （一帧被 TCP 切开、多帧挤在一段、注释心跳都已在那边处理好）。
 *
 * 这里只负责「把帧读出来并交给调用方」，不维护任何状态：
 * snapshot 走 `applyRunDetail`、live 只是 ephemeral 展示源，都在 store 里。
 * 契约见 docs/AI_CONVERSATION_DESIGN.md §13。
 */

/** 单个 run 的 ephemeral 展示态（snapshot.live）。 */
export interface AiRunLiveState {
  assistant: AiRunAssistantLive[]
  tools: AiRunToolLive[]
}

/** 首帧 / 轮提交 / resync / 终态前的完整快照：data 与 `GET /runs/{id}` 同形。 */
export interface AiRunSnapshotFrame {
  type: 'snapshot'
  seq: number
  run_id: string
  data: AiRunDetail
  live: AiRunLiveState
}

/** 提示客户端重拉快照；本身不带数据。 */
export interface AiRunResyncFrame {
  type: 'resync'
  seq: number
  run_id: string
}

/** 终态帧：紧跟最终 snapshot，之后是 `data: [DONE]`。 */
export interface AiRunTerminalFrame {
  type: 'terminal'
  seq: number
  run_id: string
  status: AiRunStatus | null
  stop_reason: AiRunStopReason | null
  error_code: string | null
}

/** 流内错误帧（开始流式之后才发生的失败）。 */
export interface AiRunErrorFrame {
  type: 'error'
  seq: number
  run_id: string
  code: string | null
}

/** 客户端会看到的所有帧。 */
export type AiRunEventFrame =
  | AiRunSnapshotFrame
  | AiRunAssistantLive
  | AiRunToolLive
  | AiRunResyncFrame
  | AiRunTerminalFrame
  | AiRunErrorFrame

/** 端点是否「不可能给流」：多 worker 503、老后端 404/405 都算。 */
const UNSUPPORTED_STATUS = new Set([404, 405, 410, 501, 503])

/** 后端多 worker 时的明确错误码。 */
export const AI_RUN_EVENTS_UNSUPPORTED = 'AI_RUN_EVENTS_UNSUPPORTED'

export interface AiRunEventsOutcome {
  /** 收到 terminal（或终态 snapshot 后的 [DONE]）。 */
  done: boolean
  /** 调用方主动中止（切会话、封禁、终态收尾）。 */
  aborted: boolean
  /** 端点不可用，应回退到轮询。 */
  unsupported: boolean
  /** 是否至少收到过一帧 snapshot（SSE 确实健康）。 */
  started: boolean
  /** 建连或流中失败的原因；没失败为 null。 */
  error: AiRequestError | null
  /** HTTP 状态（网络错误为 null）。 */
  status: number | null
}

export interface AiRunEventsOptions {
  signal?: AbortSignal
  /** 每解析出一帧回调一次（按服务端顺序）。 */
  onFrame: (frame: AiRunEventFrame) => void
  /**
   * 收到任意字节（含 15s 一次的心跳注释帧）回调一次。
   *
   * 用于「连接还活着」的判活：长时间没有业务帧不代表断线，只有连字节都没有
   * 才需要重建。
   */
  onActivity?: () => void
}

function normalizeSeq(value: unknown): number {
  return typeof value === 'number' && Number.isFinite(value) ? Math.trunc(value) : 0
}

function normalizeRound(value: unknown): number {
  const round = normalizeSeq(value)
  return round > 0 ? round : 0
}

function normalizeToolStatus(value: unknown): AiRunToolLiveStatus {
  return value === 'success' || value === 'error' ? value : 'pending'
}

function normalizeToolKind(value: unknown): AiRunToolLive['tool_kind'] {
  return value === 'read' || value === 'write' ? value : null
}

function stringOrNull(value: unknown): string | null {
  return typeof value === 'string' && value ? value : null
}

function liveList<T>(value: unknown): T[] {
  return Array.isArray(value) ? (value as T[]) : []
}

/**
 * 把一帧 `data:` JSON 归一成 `AiRunEventFrame`；形状不认识返回 null。
 *
 * 不认识的帧直接忽略而不是抛错：新增帧类型时老客户端应当继续工作。
 */
export function parseRunEventFrame(raw: string): AiRunEventFrame | null {
  let payload: unknown
  try {
    payload = JSON.parse(raw)
  } catch {
    return null
  }
  if (!payload || typeof payload !== 'object' || Array.isArray(payload)) {
    return null
  }
  const record = payload as Record<string, unknown>
  const type = typeof record.type === 'string' ? record.type : ''
  const seq = normalizeSeq(record.seq)
  const runId = typeof record.run_id === 'string' ? record.run_id : ''

  switch (type) {
    case 'snapshot': {
      const data = record.data
      if (!data || typeof data !== 'object') {
        return null
      }
      const live = (record.live ?? {}) as Record<string, unknown>
      return {
        type: 'snapshot',
        seq,
        run_id: runId,
        data: data as AiRunDetail,
        live: {
          assistant: liveList<AiRunAssistantLive>(live.assistant),
          tools: liveList<AiRunToolLive>(live.tools)
        }
      }
    }
    case 'assistant': {
      // stream_key 是归并键；缺了它这条帧无法定位到某一轮回答。
      const streamKey = typeof record.stream_key === 'string' ? record.stream_key : ''
      if (!streamKey) {
        return null
      }
      return {
        type: 'assistant',
        seq,
        run_id: runId,
        round_no: normalizeRound(record.round_no),
        stream_key: streamKey,
        content: typeof record.content === 'string' ? record.content : ''
      }
    }
    case 'tool': {
      const callId = stringOrNull(record.tool_call_id)
      const toolName = stringOrNull(record.tool_name)
      // 没有 id 也没有名字的帧没法渲染，丢掉。
      if (!callId && !toolName) {
        return null
      }
      return {
        type: 'tool',
        seq,
        run_id: runId,
        round_no: normalizeRound(record.round_no),
        tool_call_id: callId,
        tool_name: toolName,
        tool_kind: normalizeToolKind(record.tool_kind),
        status: normalizeToolStatus(record.status),
        args_summary: stringOrNull(record.args_summary),
        result_summary: stringOrNull(record.result_summary)
      }
    }
    case 'resync':
      return { type: 'resync', seq, run_id: runId }
    case 'terminal':
      return {
        type: 'terminal',
        seq,
        run_id: runId,
        status: typeof record.status === 'string' ? (record.status as AiRunStatus) : null,
        stop_reason:
          typeof record.stop_reason === 'string' ? (record.stop_reason as AiRunStopReason) : null,
        error_code: stringOrNull(record.error_code)
      }
    case 'error':
      return {
        type: 'error',
        seq,
        run_id: runId,
        code: stringOrNull(record.code)
      }
    default:
      return null
  }
}

/** AbortError 判定：浏览器与自建 controller 抛的形状不完全一样。 */
function isAbortError(error: unknown, signal: AbortSignal): boolean {
  if (signal.aborted) {
    return true
  }
  if (!error || typeof error !== 'object') {
    return false
  }
  const name = (error as { name?: unknown }).name
  return name === 'AbortError'
}

function asRequestError(error: unknown): AiRequestError | null {
  if (error instanceof AiRequestError) {
    return error
  }
  return null
}

const EMPTY_OUTCOME: AiRunEventsOutcome = {
  done: false,
  aborted: false,
  unsupported: false,
  started: false,
  error: null,
  status: null
}

/**
 * 订阅一次 run 的实时事件流，直到终态 / [DONE] / 中止 / 失败。
 *
 * 不抛错：所有结局都体现在返回值里（调用方据此决定重连、退避还是回退轮询）。
 * 建连阶段的 401 仍由 `aiFetchRaw` 按既有约定抛出——登录失效必须走统一出口。
 */
export async function streamRunEvents(
  runId: string,
  options: AiRunEventsOptions
): Promise<AiRunEventsOutcome> {
  const controller = new AbortController()
  const forwardAbort = () => controller.abort()
  if (options.signal) {
    if (options.signal.aborted) {
      controller.abort()
    } else {
      options.signal.addEventListener('abort', forwardAbort, { once: true })
    }
  }
  // 封禁 / 全局中止同样要能掐断这条长连接。
  const unregister = registerAiAbort(() => controller.abort())

  const outcome: AiRunEventsOutcome = { ...EMPTY_OUTCOME }

  try {
    const response = await aiFetchRaw(`/ai/runs/${encodeURIComponent(runId)}/events`, {
      method: 'GET',
      signal: controller.signal,
      headers: { Accept: 'text/event-stream' }
    })

    if (!response.ok) {
      let body: unknown = null
      try {
        body = await response.json()
      } catch {
        body = null
      }
      const error = aiResponseError(response, body)
      outcome.error = error
      outcome.status = response.status
      outcome.unsupported =
        UNSUPPORTED_STATUS.has(response.status) || error.code === AI_RUN_EVENTS_UNSUPPORTED
      return outcome
    }

    const body = response.body
    if (!body) {
      // 200 + 空 body：不是「终态」，按失败处理让上层回退轮询。
      outcome.error = new AiRequestError({
        status: response.status,
        code: 'AI_RUN_EVENTS_EMPTY',
        message: ''
      })
      outcome.status = response.status
      return outcome
    }

    const reader = body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    let done = false

    /** 消费一批原始帧；返回是否已到终态。 */
    const consume = (events: string[]): boolean => {
      for (const raw of events) {
        const frame = parseRunEventFrame(raw)
        if (!frame) {
          continue
        }
        if (frame.type === 'snapshot') {
          outcome.started = true
        }
        options.onFrame(frame)
        if (frame.type === 'terminal') {
          return true
        }
      }
      return false
    }

    try {
      for (;;) {
        const chunk = await reader.read()
        if (chunk.value && chunk.value.byteLength > 0) {
          buffer += decoder.decode(chunk.value, { stream: true })
          options.onActivity?.()
        }
        if (chunk.done) {
          // 冲刷解码器里剩下的多字节字符，否则末尾一个汉字会被丢掉。
          buffer += decoder.decode()
        }
        const state = parseSseChunk(buffer, '')
        buffer = state.buffer
        if (consume(state.events) || state.done) {
          done = true
          break
        }
        if (chunk.done) {
          // 上游可能不带空行直接关连接：补一个空行再解析一次，别把末帧当截断。
          const tail = parseSseChunk(`${buffer}\n\n`, '')
          buffer = tail.buffer
          done = consume(tail.events) || tail.done
          break
        }
      }
    } finally {
      // 收到终态就主动收连接：服务端 finally 里会取消订阅。
      try {
        await reader.cancel()
      } catch {
        // 连接已经关了：无所谓。
      }
    }

    outcome.done = done
    return outcome
  } catch (error) {
    if (isAbortError(error, controller.signal)) {
      outcome.aborted = true
      return outcome
    }
    outcome.error =
      asRequestError(error) ??
      new AiRequestError({
        status: 0,
        code: 'AI_RUN_EVENTS_FAILED',
        message: error instanceof Error ? error.message : ''
      })
    return outcome
  } finally {
    unregister()
    options.signal?.removeEventListener('abort', forwardAbort)
  }
}
