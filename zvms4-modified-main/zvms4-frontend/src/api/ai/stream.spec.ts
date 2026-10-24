import { describe, expect, it } from 'vitest'
import { extractDelta, parseSseChunk } from './stream'

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
    const state = parseSseChunk(
      '',
      ': keep-alive\nevent: message\nid: 7\ndata: payload\n\n'
    )
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
