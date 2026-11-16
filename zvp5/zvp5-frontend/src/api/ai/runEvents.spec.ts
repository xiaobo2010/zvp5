import { afterEach, describe, expect, it, vi } from 'vitest'
import { AiRequestError } from './errors'
import { streamRunEvents } from './runEvents'

/** 最小 fetch 替身：只实现 runEvents.ts 用到的字段。 */
function fakeResponse(chunks: Array<string | Uint8Array>, requestId = 'req-1') {
  let index = 0
  const reader = {
    async read() {
      if (index >= chunks.length) {
        return { done: true, value: undefined }
      }
      const item = chunks[index++]
      return {
        done: false,
        value: typeof item === 'string' ? new TextEncoder().encode(item) : item
      }
    },
    releaseLock() {}
  }
  return {
    ok: true,
    status: 200,
    headers: { get: (name: string) => (name === 'X-AI-Request-Id' ? requestId : null) },
    body: { getReader: () => reader }
  }
}

function stubFetch(response: unknown): void {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => response)
  )
}

function frame(value: unknown): string {
  return `data: ${JSON.stringify(value)}\n\n`
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('streamRunEvents', () => {
  it('按契约分发 snapshot / assistant / tool / terminal，并在 [DONE] 收尾', async () => {
    const snapshot = {
      run: { oid: 'run-1', status: 'running', rounds: 0 },
      events: [],
      proposals: []
    }
    stubFetch(
      fakeResponse([
        frame({ type: 'snapshot', seq: 1, run_id: 'run-1', data: snapshot, live: {} }),
        frame({
          type: 'assistant',
          seq: 2,
          run_id: 'run-1',
          round_no: 1,
          stream_key: 'run-1:1',
          content: '你'
        }),
        frame({
          type: 'assistant',
          seq: 3,
          run_id: 'run-1',
          round_no: 1,
          stream_key: 'run-1:1',
          content: '你好'
        }),
        frame({
          type: 'tool',
          seq: 4,
          run_id: 'run-1',
          round_no: 1,
          tool_call_id: 'call-1',
          tool_name: 'search',
          tool_kind: 'read',
          status: 'pending'
        }),
        frame({ type: 'terminal', seq: 5, run_id: 'run-1', status: 'succeeded', stop_reason: 'completed', error_code: null }),
        'data: [DONE]\n\n'
      ])
    )

    const snapshots: unknown[] = []
    const assistants: string[] = []
    const tools: unknown[] = []
    const terminals: unknown[] = []

    const result = await streamRunEvents('run-1', {
      onSnapshot: (item) => snapshots.push(item.data),
      onAssistant: (item) => assistants.push(item.content),
      onTool: (item) => tools.push(item),
      onTerminal: (item) => terminals.push(item)
    })

    expect(result).toMatchObject({ completed: true, error: null, requestId: 'req-1' })
    expect(snapshots).toEqual([snapshot])
    expect(assistants).toEqual(['你', '你好'])
    expect(tools).toHaveLength(1)
    expect(terminals).toHaveLength(1)
  })

  it('流内 error 帧是非致命收尾：回调收到、结果标 error、completed=false', async () => {
    stubFetch(
      fakeResponse([
        frame({ type: 'error', seq: 1, run_id: 'run-1', code: 'AI_RUN_EVENTS_FAILED' }),
        'data: [DONE]\n\n'
      ])
    )

    const codes: string[] = []
    const result = await streamRunEvents('run-1', {
      onError: (item) => codes.push(item.code)
    })

    expect(codes).toEqual(['AI_RUN_EVENTS_FAILED'])
    expect(result.completed).toBe(false)
    expect(result.error?.code).toBe('AI_RUN_EVENTS_FAILED')
  })

  it('多 worker 的 503 变成带 code 的 AiRequestError，供 store 回退轮询', async () => {
    const body = { error: { code: 'AI_RUN_EVENTS_UNSUPPORTED', message: 'single worker only' } }
    const response = {
      ok: false,
      status: 503,
      headers: { get: () => null },
      clone() {
        return { json: async () => body }
      },
      json: async () => body
    }
    stubFetch(response)

    await expect(streamRunEvents('run-1')).rejects.toMatchObject({
      status: 503,
      code: 'AI_RUN_EVENTS_UNSUPPORTED'
    })
    await expect(streamRunEvents('run-1')).rejects.toBeInstanceOf(AiRequestError)
  })

  it('连接提前结束且没有 terminal：completed=false，交给调用方轮询兜底', async () => {
    stubFetch(fakeResponse([frame({ type: 'snapshot', seq: 1, run_id: 'run-1', data: {} })]))

    const result = await streamRunEvents('run-1')
    expect(result.completed).toBe(false)
    expect(result.error).toBeNull()
  })
})
