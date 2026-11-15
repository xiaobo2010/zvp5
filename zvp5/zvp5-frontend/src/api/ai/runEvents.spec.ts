import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { parseRunEventFrame, streamRunEvents } from './runEvents'
import type { AiRunEventFrame } from './runEvents'

/**
 * run 事件流的取帧契约。
 *
 * 网络层只测「怎么读、怎么归一、怎么收尾」：分片、心跳、终态顺序、503、
 * 中止。状态的合并与去重在 store 里测。
 */

const { storeMock } = vi.hoisted(() => ({
  storeMock: {
    isLogin: true,
    bannedNoticeAt: 0,
    banReason: '',
    banUntil: 0
  }
}))

vi.mock('@/plugins/axios', () => ({ default: vi.fn(), baseURL: '/api' }))
vi.mock('@/stores/user', () => ({ useUserStore: () => storeMock }))

const RUN_ID = 'r1'.repeat(16)

/** 最小 fetch 替身：按 chunk 顺序吐字节，最后关流。 */
function streamingResponse(chunks: string[]) {
  let index = 0
  const reader = {
    async read() {
      if (index >= chunks.length) {
        return { done: true, value: undefined }
      }
      return { done: false, value: new TextEncoder().encode(chunks[index++]) }
    },
    async cancel() {
      index = chunks.length
    }
  }
  return {
    ok: true,
    status: 200,
    headers: { get: () => null },
    body: { getReader: () => reader }
  }
}

function errorResponse(status: number, body: unknown) {
  return {
    ok: false,
    status,
    headers: { get: () => null },
    json: async () => body,
    body: null
  }
}

function stubFetch(response: unknown): void {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => response)
  )
}

function frame(type: string, seq: number, extra: Record<string, unknown> = {}): string {
  return `data: ${JSON.stringify({ type, seq, run_id: RUN_ID, ...extra })}\n\n`
}

const SNAPSHOT = frame('snapshot', 4, {
  data: { run: { oid: RUN_ID }, events: [], proposals: [] },
  live: {
    assistant: [
      {
        type: 'assistant',
        seq: 2,
        run_id: RUN_ID,
        round_no: 1,
        stream_key: `${RUN_ID}:1`,
        content: '正在'
      }
    ],
    tools: [
      {
        type: 'tool',
        seq: 3,
        run_id: RUN_ID,
        round_no: 1,
        tool_call_id: 'call-1',
        tool_name: 'fs_list',
        tool_kind: 'read',
        status: 'pending',
        args_summary: '{}'
      }
    ]
  }
})

beforeEach(() => {
  localStorage.setItem('token', 'tok-1')
})

afterEach(() => {
  vi.unstubAllGlobals()
  localStorage.clear()
})

describe('帧归一', () => {
  it('snapshot：data 与 GET /runs/{id} 同形，live 双数组', () => {
    const parsed = parseRunEventFrame(
      JSON.stringify({
        type: 'snapshot',
        seq: 9,
        run_id: RUN_ID,
        data: { run: { oid: RUN_ID, status: 'running' }, events: [], proposals: [] },
        live: { assistant: [], tools: [] }
      })
    )
    expect(parsed?.type).toBe('snapshot')
    if (parsed?.type !== 'snapshot') {
      throw new Error('unreachable')
    }
    expect(parsed.seq).toBe(9)
    expect(parsed.live.assistant).toEqual([])
    expect(parsed.live.tools).toEqual([])
  })

  it('assistant：累计文本 + stream_key', () => {
    const parsed = parseRunEventFrame(
      JSON.stringify({
        type: 'assistant',
        seq: 5,
        run_id: RUN_ID,
        round_no: 2,
        stream_key: `${RUN_ID}:2`,
        content: '累计到这里的文本'
      })
    )
    expect(parsed).toMatchObject({
      type: 'assistant',
      round_no: 2,
      stream_key: `${RUN_ID}:2`,
      content: '累计到这里的文本'
    })
  })

  it('tool：状态缺省是 pending，终态带 result_summary', () => {
    const pending = parseRunEventFrame(
      JSON.stringify({
        type: 'tool',
        seq: 6,
        run_id: RUN_ID,
        round_no: 1,
        tool_call_id: 'call-1',
        tool_name: 'fs_list',
        tool_kind: 'read',
        args_summary: '{}'
      })
    )
    expect(pending).toMatchObject({ type: 'tool', status: 'pending', tool_kind: 'read' })

    const done = parseRunEventFrame(
      JSON.stringify({
        type: 'tool',
        seq: 7,
        run_id: RUN_ID,
        round_no: 1,
        tool_call_id: 'call-1',
        tool_name: 'fs_list',
        tool_kind: 'read',
        status: 'success',
        result_summary: '3 个文件'
      })
    )
    expect(done).toMatchObject({ type: 'tool', status: 'success', result_summary: '3 个文件' })
  })

  it('terminal 与 error 帧', () => {
    expect(
      parseRunEventFrame(
        JSON.stringify({
          type: 'terminal',
          seq: 10,
          run_id: RUN_ID,
          status: 'succeeded',
          stop_reason: 'completed',
          error_code: null
        })
      )
    ).toMatchObject({ type: 'terminal', status: 'succeeded', stop_reason: 'completed' })

    expect(
      parseRunEventFrame(
        JSON.stringify({ type: 'error', seq: 11, run_id: RUN_ID, code: 'AI_RUN_EVENTS_FAILED' })
      )
    ).toMatchObject({ type: 'error', code: 'AI_RUN_EVENTS_FAILED' })
  })

  it('陌生 / 坏帧返回 null，不抛错', () => {
    expect(parseRunEventFrame('not json')).toBeNull()
    expect(parseRunEventFrame('{"type":"future-frame","seq":1}')).toBeNull()
    expect(parseRunEventFrame('{"type":"assistant","seq":1}')).toBeNull()
  })
})

describe('streamRunEvents', () => {
  it('用 fetch 读 SSE：带 Authorization，分片帧能拼回来', async () => {
    stubFetch(
      streamingResponse([
        'data: {"type":"assistant","seq":1,"run_id":"',
        `${RUN_ID}","round_no":1,"stream_key":"${RUN_ID}:1","content":"你`,
        '好"}\n\n',
        SNAPSHOT,
        'data: [DONE]\n\n'
      ])
    )
    const frames: AiRunEventFrame[] = []

    const outcome = await streamRunEvents(RUN_ID, { onFrame: (item) => frames.push(item) })

    expect(frames.map((item) => item.type)).toEqual(['assistant', 'snapshot'])
    expect(frames[0]).toMatchObject({ content: '你好' })
    expect(outcome.done).toBe(true)
    expect(outcome.started).toBe(true)
    expect(outcome.unsupported).toBe(false)

    const init = (vi.mocked(fetch).mock.calls[0]![1] ?? {}) as RequestInit
    const headers = init.headers as Record<string, string>
    expect(headers.Authorization).toBe('Bearer tok-1')
    expect(headers.Accept).toBe('text/event-stream')
    expect(String(vi.mocked(fetch).mock.calls[0]![0])).toContain(`/ai/runs/${RUN_ID}/events`)
  })

  it('心跳注释帧不产生事件，但算「连接还活着」', async () => {
    stubFetch(streamingResponse([': ping\n\n', ': ping\n\n', 'data: [DONE]\n\n']))
    const frames: AiRunEventFrame[] = []
    let activity = 0

    const outcome = await streamRunEvents(RUN_ID, {
      onFrame: (item) => frames.push(item),
      onActivity: () => {
        activity += 1
      }
    })

    expect(frames).toEqual([])
    expect(activity).toBeGreaterThan(0)
    expect(outcome.done).toBe(true)
  })

  it('终态顺序：最终 snapshot → terminal → [DONE]', async () => {
    stubFetch(
      streamingResponse([
        SNAPSHOT,
        frame('terminal', 5, {
          status: 'succeeded',
          stop_reason: 'completed',
          error_code: null
        }),
        'data: [DONE]\n\n'
      ])
    )
    const types: string[] = []

    const outcome = await streamRunEvents(RUN_ID, {
      onFrame: (item) => types.push(item.type)
    })

    expect(types).toEqual(['snapshot', 'terminal'])
    expect(outcome.done).toBe(true)
  })

  it('多 worker 503：unsupported=true，交给上层回落轮询', async () => {
    stubFetch(
      errorResponse(503, {
        error: {
          code: 'AI_RUN_EVENTS_UNSUPPORTED',
          message: 'live run events require a single worker'
        }
      })
    )

    const outcome = await streamRunEvents(RUN_ID, { onFrame: () => {} })

    expect(outcome.unsupported).toBe(true)
    expect(outcome.status).toBe(503)
    expect(outcome.error?.code).toBe('AI_RUN_EVENTS_UNSUPPORTED')
    expect(outcome.done).toBe(false)
  })

  it('老后端 404 也按不支持处理（继续用轮询）', async () => {
    stubFetch(errorResponse(404, { error: { code: 'Not Found' } }))

    const outcome = await streamRunEvents(RUN_ID, { onFrame: () => {} })

    expect(outcome.unsupported).toBe(true)
    expect(outcome.status).toBe(404)
  })

  it('流读到一半断掉：done=false，已到的帧保留', async () => {
    stubFetch(streamingResponse([SNAPSHOT]))

    const frames: AiRunEventFrame[] = []
    const outcome = await streamRunEvents(RUN_ID, { onFrame: (item) => frames.push(item) })

    expect(frames).toHaveLength(1)
    expect(outcome.done).toBe(false)
    expect(outcome.aborted).toBe(false)
    expect(outcome.started).toBe(true)
  })

  it('中止：signal 一响就收手，返回 aborted', async () => {
    const controller = new AbortController()
    vi.stubGlobal(
      'fetch',
      vi.fn(
        (_url: string, init: RequestInit) =>
          new Promise<Response>((_resolve, reject) => {
            const signal = init.signal as AbortSignal
            if (signal.aborted) {
              reject(Object.assign(new Error('aborted'), { name: 'AbortError' }))
              return
            }
            signal.addEventListener(
              'abort',
              () => reject(Object.assign(new Error('aborted'), { name: 'AbortError' })),
              { once: true }
            )
          })
      )
    )

    const pending = streamRunEvents(RUN_ID, {
      signal: controller.signal,
      onFrame: () => {}
    })
    controller.abort()

    const outcome = await pending
    expect(outcome.aborted).toBe(true)
    expect(outcome.done).toBe(false)
  })
})
