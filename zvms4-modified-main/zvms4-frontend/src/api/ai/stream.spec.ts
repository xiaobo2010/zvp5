import { afterEach, describe, expect, it, vi } from 'vitest'
import { extractDelta, extractStreamError, parseSseChunk, streamChatCompletion } from './stream'

describe('SSE 帧解析', () => {
  it('解析单帧并保留空缓冲', () => {
    const state = parseSseChunk('', 'data: {"choices":[]}\n\n')
    expect(state.events).toEqual(['{"choices":[]}'])
    expect(state.buffer).toBe('')
    expect(state.done).toBe(false)
  })

  it('把被切断的帧留在缓冲里，等下一段再拼', () => {
    const first = parseSseChunk('', 'data: {"a":')
    expect(first.events).toEqual([])
    expect(first.buffer).toBe('data: {"a":')

    const second = parseSseChunk(first.buffer, '1}\n\n')
    expect(second.events).toEqual(['{"a":1}'])
    expect(second.buffer).toBe('')
  })

  it('一次解析多帧连发', () => {
    const state = parseSseChunk('', 'data: one\n\ndata: two\n\ndata: three\n\n')
    expect(state.events).toEqual(['one', 'two', 'three'])
    expect(state.done).toBe(false)
  })

  it('遇到 [DONE] 就停并清空缓冲', () => {
    const state = parseSseChunk('', 'data: one\n\ndata: [DONE]\n\ndata: after\n\n')
    expect(state.events).toEqual(['one'])
    expect(state.done).toBe(true)
    expect(state.buffer).toBe('')
  })

  it('忽略非 data 行与注释', () => {
    const state = parseSseChunk('', ': keep-alive\nevent: message\nid: 7\ndata: payload\n\n')
    expect(state.events).toEqual(['payload'])
  })

  it('只留残留缓冲，不吐事件', () => {
    const state = parseSseChunk('', 'data: partial')
    expect(state.events).toEqual([])
    expect(state.buffer).toBe('data: partial')
  })

  it('兼容 CRLF 分帧', () => {
    const state = parseSseChunk('', 'data: one\r\n\r\ndata: two\r\n\r\n')
    expect(state.events).toEqual(['one', 'two'])
  })
})

describe('增量文本提取', () => {
  it('取 delta.content', () => {
    expect(extractDelta('{"choices":[{"delta":{"content":"你好"}}]}')).toBe('你好')
  })

  it('取 message.content', () => {
    expect(extractDelta('{"choices":[{"message":{"content":"答案"}}]}')).toBe('答案')
  })

  it('形状不认识或 JSON 坏了就当没有内容', () => {
    expect(extractDelta('not json')).toBe('')
    expect(extractDelta('{"choices":[]}')).toBe('')
    expect(extractDelta('{}')).toBe('')
  })
})

describe('流内错误帧', () => {
  it('取出 {error: {code, message}}', () => {
    expect(extractStreamError('{"error":{"code":"AI_TIMEOUT","message":"上游超时"}}')).toEqual({
      code: 'AI_TIMEOUT',
      message: '上游超时'
    })
  })

  it('没有码时归到通用上游错误', () => {
    expect(extractStreamError('{"error":{"message":"炸了"}}')).toEqual({
      code: 'AI_UPSTREAM_ERROR',
      message: '炸了'
    })
  })

  it('普通增量帧 / 坏 JSON / 空 error 都不是错误帧', () => {
    expect(extractStreamError('{"choices":[{"delta":{"content":"x"}}]}')).toBeNull()
    expect(extractStreamError('not json')).toBeNull()
    expect(extractStreamError('{"error":{}}')).toBeNull()
  })
})

/** 最小 fetch 替身：只实现 stream.ts 用到的那几个字段。 */
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

async function run(chunks: Array<string | Uint8Array>) {
  stubFetch(fakeResponse(chunks))
  return streamChatCompletion({
    model: 'gpt-x',
    messages: [{ role: 'user', content: 'hi' }]
  })
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('streamChatCompletion 收尾判定', () => {
  it('收到 error 帧：保留已生成文本，error 有值且不算截断', async () => {
    const out = await run([
      'data: {"choices":[{"delta":{"content":"你好"}}]}\n\n',
      'data: {"error":{"code":"AI_UPSTREAM_REJECTED","message":"上游拒绝"}}\n\n',
      'data: [DONE]\n\n'
    ])
    expect(out.text).toBe('你好')
    expect(out.error).toEqual({ code: 'AI_UPSTREAM_REJECTED', message: '上游拒绝' })
    expect(out.interrupted).toBe(false)
    expect(out.done).toBe(false)
  })

  it('尾帧 [DONE] 后没有空行直接断流，也算正常结束', async () => {
    const out = await run(['data: {"choices":[{"delta":{"content":"好"}}]}\n\n', 'data: [DONE]'])
    expect(out.text).toBe('好')
    expect(out.done).toBe(true)
    expect(out.interrupted).toBe(false)
    expect(out.error).toBeNull()
  })

  it('多字节字符被切成两段也能拼回来（收尾冲刷解码器）', async () => {
    const bytes = new TextEncoder().encode(
      'data: {"choices":[{"delta":{"content":"你好"}}]}\n\ndata: [DONE]\n\n'
    )
    // 切在「你」的第二、三个字节之间。
    const cut = bytes.indexOf(0xe5) + 1
    const out = await run([bytes.slice(0, cut), bytes.slice(cut)])
    expect(out.text).toBe('你好')
    expect(out.done).toBe(true)
  })

  it('200 但空 body：明确失败，不报「截断」', async () => {
    stubFetch({
      ok: true,
      status: 200,
      headers: { get: () => null },
      body: null
    })
    const out = await streamChatCompletion({
      model: 'gpt-x',
      messages: [{ role: 'user', content: 'hi' }]
    })
    expect(out.error).toEqual({ code: 'AI_UPSTREAM_ERROR', message: '' })
    expect(out.interrupted).toBe(false)
  })

  it('没收到 [DONE] 也没收到 error：仍是截断', async () => {
    const out = await run(['data: {"choices":[{"delta":{"content":"半"}}]}\n\n'])
    expect(out.text).toBe('半')
    expect(out.done).toBe(false)
    expect(out.interrupted).toBe(true)
    expect(out.error).toBeNull()
  })
})
