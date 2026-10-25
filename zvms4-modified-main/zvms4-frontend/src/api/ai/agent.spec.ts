import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  AI_SCENE_AGENT,
  confirmProposal,
  fetchPendingProposals,
  fetchRun,
  rejectProposal,
  runAgent
} from './agent'
import { AiRequestError } from './errors'

/**
 * 智能体面的请求契约。
 *
 * 只测「发出去的东西」与「错误怎么归一」：路径、场景头、请求体、错误码/请求编号。
 * 上游行为由后端保证，前端不假设更多。
 */

function jsonResponse(
  body: unknown,
  init: { status?: number; headers?: Record<string, string> } = {}
) {
  const status = init.status ?? 200
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: (name: string) => init.headers?.[name] ?? null },
    json: async () => body
  }
}

function stubFetch(response: unknown): void {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => response)
  )
}

function lastInit(): RequestInit {
  return (vi.mocked(fetch).mock.calls[0]![1] ?? {}) as RequestInit
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('runAgent', () => {
  it('POST /ai/agent/run，带 X-AI-Scene: agent', async () => {
    stubFetch(
      jsonResponse({
        request_id: 'req-1',
        content: '好了',
        events: [],
        proposals: []
      })
    )

    const result = await runAgent([{ role: 'user', content: '查一下' }], 'gpt-x')

    const [url, init] = vi.mocked(fetch).mock.calls[0]!
    expect(String(url)).toContain('/ai/agent/run')
    expect(init?.method).toBe('POST')
    const headers = lastInit().headers as Record<string, string>
    expect(headers['X-AI-Scene']).toBe(AI_SCENE_AGENT)
    // 场景只走头，不进请求体。
    expect(String(lastInit().body)).not.toContain('agent')
    expect(JSON.parse(String(lastInit().body))).toEqual({
      messages: [{ role: 'user', content: '查一下' }],
      model: 'gpt-x'
    })
    expect(result.content).toBe('好了')
    expect(result.request_id).toBe('req-1')
  })

  it('省掉 model 时请求体里不带该字段（后端取目录里第一个模型）', async () => {
    stubFetch(jsonResponse({ request_id: 'req-2', content: '', events: [], proposals: [] }))

    await runAgent([{ role: 'user', content: 'hi' }])

    expect(JSON.parse(String(lastInit().body))).toEqual({
      messages: [{ role: 'user', content: 'hi' }]
    })
  })

  it('失败按 {"error":{code,message}} 归一成 AiRequestError（带请求编号）', async () => {
    stubFetch(
      jsonResponse(
        { error: { code: 'AI_AGENT_PROPOSAL_EXPIRED', message: 'expired' } },
        { status: 410, headers: { 'X-AI-Request-Id': 'req-410' } }
      )
    )

    const failure = await runAgent([{ role: 'user', content: 'hi' }]).catch(
      (error: unknown) => error
    )

    expect(failure).toBeInstanceOf(AiRequestError)
    const error = failure as AiRequestError
    expect(error.status).toBe(410)
    expect(error.code).toBe('AI_AGENT_PROPOSAL_EXPIRED')
    expect(error.requestId).toBe('req-410')
  })
})

describe('提案的确认与驳回', () => {
  it('confirm 走 /proposals/{oid}/confirm', async () => {
    stubFetch(jsonResponse({ status: 'confirmed', result_summary: '{"ok":true}' }))

    const result = await confirmProposal('a'.repeat(32))

    const [url, init] = vi.mocked(fetch).mock.calls[0]!
    expect(String(url)).toContain(`/ai/agent/proposals/${'a'.repeat(32)}/confirm`)
    expect(init?.method).toBe('POST')
    expect(result.status).toBe('confirmed')
    expect(result.result_summary).toBe('{"ok":true}')
  })

  it('不带动态码时请求体里没有 totp_code', async () => {
    stubFetch(jsonResponse({ status: 'confirmed' }))

    await confirmProposal('a'.repeat(32))

    expect(JSON.parse(String(lastInit().body))).toEqual({})
  })

  it('带动态码时请求体是 {totp_code}', async () => {
    stubFetch(jsonResponse({ status: 'confirmed', approver_oid: 'b'.repeat(32) }))

    const result = await confirmProposal('a'.repeat(32), ' 123456 ')

    expect(JSON.parse(String(lastInit().body))).toEqual({ totp_code: '123456' })
    expect(result.approver_oid).toBe('b'.repeat(32))
  })

  it('空字符串的动态码按「没给码」处理，body 是 {}', async () => {
    stubFetch(jsonResponse({ status: 'confirmed' }))

    await confirmProposal('a'.repeat(32), '   ')

    expect(JSON.parse(String(lastInit().body))).toEqual({})
  })

  it('确认结果带 30 分钟授权窗口标记', async () => {
    stubFetch(
      jsonResponse({ status: 'confirmed', dual_auth_cached: true, approver_oid: 'c'.repeat(32) })
    )

    const result = await confirmProposal('a'.repeat(32))

    expect(result.dual_auth_cached).toBe(true)
  })

  it('reject 走 /proposals/{oid}/reject', async () => {
    stubFetch(jsonResponse({ status: 'rejected' }))

    const result = await rejectProposal('b'.repeat(32))

    const [url, init] = vi.mocked(fetch).mock.calls[0]!
    expect(String(url)).toContain(`/ai/agent/proposals/${'b'.repeat(32)}/reject`)
    expect(init?.method).toBe('POST')
    expect(result.status).toBe('rejected')
  })
})

describe('待确认提案列表', () => {
  it('默认查 proposed 且 limit 不超过 50', async () => {
    stubFetch(jsonResponse({ proposals: [] }))

    await fetchPendingProposals()

    const [url] = vi.mocked(fetch).mock.calls[0]!
    expect(String(url)).toContain('/ai/agent/proposals?')
    expect(String(url)).toContain('status=proposed')
    expect(String(url)).toContain('limit=20')
  })

  it('limit 超过上限时按 50 截断', async () => {
    stubFetch(jsonResponse({ proposals: [] }))

    await fetchPendingProposals('proposed', 500)

    const [url] = vi.mocked(fetch).mock.calls[0]!
    expect(String(url)).toContain('limit=50')
  })

  it('外层是裸数组时不认（外壳已冻结为 {proposals}）', async () => {
    stubFetch(jsonResponse([{ oid: 'a'.repeat(32) }]))

    const result = await fetchPendingProposals()

    expect(result.proposals).toEqual([])
  })

  it('形状不认识时给空数组，不抛错', async () => {
    stubFetch(jsonResponse({ whatever: true }))

    const result = await fetchPendingProposals()

    expect(result.proposals).toEqual([])
  })

  it('只认 proposals 这一个键', async () => {
    stubFetch(jsonResponse({ items: [{ oid: 'a'.repeat(32) }] }))

    const result = await fetchPendingProposals()

    expect(result.proposals).toEqual([])
  })
})

describe('run 回放', () => {
  it('按 request_id 取回事件时间线', async () => {
    stubFetch(
      jsonResponse({
        request_id: 'req-1',
        events: [{ round_no: 1, tool_name: 'fs_list', tool_kind: 'read', status: 'executed' }]
      })
    )

    const result = await fetchRun('req-1')

    const [url] = vi.mocked(fetch).mock.calls[0]!
    expect(String(url)).toContain('/ai/agent/runs/req-1')
    expect(result.request_id).toBe('req-1')
    expect(result.events).toHaveLength(1)
  })

  it('没有 events 时给空数组', async () => {
    stubFetch(jsonResponse({}))

    const result = await fetchRun('req-1')

    expect(result.events).toEqual([])
    expect(result.request_id).toBe('req-1')
  })
})
