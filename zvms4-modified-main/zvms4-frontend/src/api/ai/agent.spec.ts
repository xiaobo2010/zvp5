import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  confirmProposal,
  fetchPendingProposals,
  getModerationUnrestricted,
  rejectProposal,
  setModerationUnrestricted,
  unlockAgent
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
  const headers: Record<string, string> = {}
  for (const [name, value] of Object.entries(init.headers ?? {})) {
    headers[name.toLowerCase()] = value
  }
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: {
      ...headers,
      get: (name: string) => headers[name.toLowerCase()] ?? null
    },
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

describe('解锁与内容审核开关', () => {
  it('unlock 走 /ai/agent/unlock，body 是 {password}', async () => {
    stubFetch(jsonResponse({ unlocked_until: '2026-10-25T12:30:00' }))

    const until = await unlockAgent('secret')

    const [url, init] = vi.mocked(fetch).mock.calls[0]!
    expect(String(url)).toContain('/ai/agent/unlock')
    expect(init?.method).toBe('POST')
    expect(JSON.parse(String(init?.body))).toEqual({ password: 'secret' })
    expect(until).toBe('2026-10-25T12:30:00')
  })

  it('响应里没有 unlocked_until 时给空串，不编窗口', async () => {
    stubFetch(jsonResponse({ ok: true }))

    await expect(unlockAgent('secret')).resolves.toBe('')
  })

  it('密码错归一成 AI_AGENT_UNLOCK_FAILED', async () => {
    stubFetch(
      jsonResponse({ error: { code: 'AI_AGENT_UNLOCK_FAILED', message: 'bad' } }, { status: 403 })
    )

    await expect(unlockAgent('wrong')).rejects.toMatchObject({
      name: 'AiRequestError',
      status: 403,
      code: 'AI_AGENT_UNLOCK_FAILED'
    })
  })

  it('连错锁定归一成 AI_AGENT_UNLOCK_LOCKED 并带退避秒数', async () => {
    stubFetch(
      jsonResponse(
        { error: { code: 'AI_AGENT_UNLOCK_LOCKED', message: 'locked' } },
        { status: 429, headers: { 'retry-after': '60' } }
      )
    )

    const error = await unlockAgent('wrong').catch((caught: unknown) => caught)
    expect(error).toBeInstanceOf(AiRequestError)
    expect((error as AiRequestError).code).toBe('AI_AGENT_UNLOCK_LOCKED')
    expect((error as AiRequestError).retryAfter).toBe(60)
  })

  it('读无限制模式走 GET', async () => {
    stubFetch(jsonResponse({ enabled: true }))

    await expect(getModerationUnrestricted()).resolves.toBe(true)

    const [url, init] = vi.mocked(fetch).mock.calls[0]!
    expect(String(url)).toContain('/ai/agent/moderation/unrestricted')
    expect(init?.method ?? 'GET').toBe('GET')
  })

  it('响应缺 enabled 时按关闭处理', async () => {
    stubFetch(jsonResponse({}))

    await expect(getModerationUnrestricted()).resolves.toBe(false)
  })

  it('写无限制模式走 PUT，body 是 {enabled}', async () => {
    stubFetch(jsonResponse({ enabled: true }))

    await expect(setModerationUnrestricted(true)).resolves.toBe(true)

    const [url, init] = vi.mocked(fetch).mock.calls[0]!
    expect(String(url)).toContain('/ai/agent/moderation/unrestricted')
    expect(init?.method).toBe('PUT')
    expect(JSON.parse(String(init?.body))).toEqual({ enabled: true })
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
