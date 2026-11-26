import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  cancelRun,
  compactConversation,
  createConversation,
  deleteConversation,
  fetchActiveRuns,
  fetchRunDetail,
  getConversation,
  listConversations,
  listMessages,
  submitRun,
  updateConversation
} from './conversations'
import { AiRequestError } from './errors'

/**
 * 会话面的请求契约。
 *
 * 只测「发出去的东西」与「回来怎么归一」：路径、查询参数、请求体、错误码。
 * 服务端行为由后端保证，前端不假设更多。
 */

const { axiosMock } = vi.hoisted(() => ({ axiosMock: vi.fn() }))

vi.mock('@/plugins/axios', () => ({
  default: axiosMock,
  baseURL: '/api'
}))

const conversationFixture = {
  oid: 'c1'.repeat(16),
  mode: 'agent' as const,
  title: '查一下图书馆',
  model: 'gpt-4o-mini',
  status: 'active' as const,
  tokens: { prompt: 120, completion: 30, total: 150 },
  context: { tokens: 120, limit: 32768, ratio: 0.0037 },
  compaction: { upto_seq: 0, summarized: false, degraded_at: null },
  message_count: 4,
  created_at: '2026-10-25T00:00:00',
  updated_at: '2026-10-25T01:00:00'
}

const messageFixture = {
  oid: 'm1'.repeat(16),
  seq: 1,
  role: 'user' as const,
  content: '查一下图书馆',
  created_at: '2026-10-25T00:01:00'
}

const runFixture = {
  oid: 'r1'.repeat(16),
  conversation_oid: conversationFixture.oid,
  status: 'running' as const,
  model: 'gpt-4o-mini',
  stop_reason: null,
  rounds: 2,
  error_code: null,
  error_message: null,
  result_message_oid: null,
  prompt_tokens: 100,
  completion_tokens: 20,
  total_tokens: 120,
  cancel_requested: false,
  created_at: '2026-10-25T00:02:00',
  started_at: '2026-10-25T00:02:01',
  finished_at: null
}

function ok(data: unknown, status = 200) {
  return { status, data: { status: 'ok', code: status, data }, headers: {} }
}

beforeEach(() => {
  axiosMock.mockReset()
})

describe('会话 CRUD', () => {
  it('建会话：POST /ai/conversations，只带 mode', async () => {
    axiosMock.mockResolvedValueOnce(ok(conversationFixture, 201))

    const result = await createConversation('agent')

    expect(result.oid).toBe(conversationFixture.oid)
    expect(axiosMock).toHaveBeenCalledWith(
      expect.objectContaining({
        method: 'post',
        url: '/ai/conversations',
        data: { mode: 'agent' }
      })
    )
  })

  it('建会话：给了标题与模型才带上这两个字段', async () => {
    axiosMock.mockResolvedValueOnce(ok(conversationFixture, 201))

    await createConversation('agent', '标题', 'gpt-4o-mini')

    expect(axiosMock).toHaveBeenCalledWith(
      expect.objectContaining({
        data: { mode: 'agent', title: '标题', model: 'gpt-4o-mini' }
      })
    )
  })

  it('列表：默认 active + limit 50，按游标翻页', async () => {
    axiosMock.mockResolvedValueOnce(
      ok({ conversations: [conversationFixture], next_cursor: 'Y3Vyc29y' }, 200)
    )

    const page = await listConversations()

    expect(page.conversations).toEqual([conversationFixture])
    expect(page.next_cursor).toBe('Y3Vyc29y')
    expect(axiosMock).toHaveBeenCalledWith(
      expect.objectContaining({
        method: 'get',
        url: '/ai/conversations',
        params: { status: 'active', limit: 50 }
      })
    )
  })

  it('列表：带游标时把 cursor 也放进查询参数', async () => {
    axiosMock.mockResolvedValueOnce(ok({ conversations: [], next_cursor: null }))

    await listConversations({ cursor: 'abc' })

    expect(axiosMock).toHaveBeenCalledWith(
      expect.objectContaining({ params: { status: 'active', limit: 50, cursor: 'abc' } })
    )
  })

  it('列表：外壳不认识时给空数组，不抛错', async () => {
    axiosMock.mockResolvedValueOnce(ok({ whatever: true }))

    const page = await listConversations()

    expect(page.conversations).toEqual([])
    expect(page.next_cursor).toBeNull()
  })

  it('详情：GET /ai/conversations/{oid}', async () => {
    axiosMock.mockResolvedValueOnce(ok(conversationFixture))

    const result = await getConversation(conversationFixture.oid)

    expect(result.mode).toBe('agent')
    expect(axiosMock).toHaveBeenCalledWith(
      expect.objectContaining({
        method: 'get',
        url: `/ai/conversations/${conversationFixture.oid}`
      })
    )
  })

  it('改名：PATCH 只带 title', async () => {
    axiosMock.mockResolvedValueOnce(ok({ ...conversationFixture, title: '新标题' }))

    const result = await updateConversation(conversationFixture.oid, { title: '新标题' })

    expect(result.title).toBe('新标题')
    expect(axiosMock).toHaveBeenCalledWith(
      expect.objectContaining({
        method: 'patch',
        url: `/ai/conversations/${conversationFixture.oid}`,
        data: { title: '新标题' }
      })
    )
  })

  it('改思考强度：PATCH 只带 thinking', async () => {
    axiosMock.mockResolvedValueOnce(ok({ ...conversationFixture, thinking: 'high' }))

    const result = await updateConversation(conversationFixture.oid, { thinking: 'high' })

    expect(result.thinking).toBe('high')
    expect(axiosMock).toHaveBeenCalledWith(
      expect.objectContaining({
        method: 'patch',
        url: `/ai/conversations/${conversationFixture.oid}`,
        data: { thinking: 'high' }
      })
    )
  })

  it('空会话换模型：PATCH 只带 model', async () => {
    axiosMock.mockResolvedValueOnce(ok({ ...conversationFixture, model: 'gpt-4.1' }))

    const result = await updateConversation(conversationFixture.oid, { model: 'gpt-4.1' })

    expect(result.model).toBe('gpt-4.1')
    expect(axiosMock).toHaveBeenCalledWith(
      expect.objectContaining({
        method: 'patch',
        url: `/ai/conversations/${conversationFixture.oid}`,
        data: { model: 'gpt-4.1' }
      })
    )
  })

  it('删除：DELETE 拿回删掉的消息与运行条数', async () => {
    axiosMock.mockResolvedValueOnce(ok({ deleted: true, messages: 4, runs: 1 }))

    const result = await deleteConversation(conversationFixture.oid)

    expect(result).toEqual({ deleted: true, messages: 4, runs: 1 })
    expect(axiosMock).toHaveBeenCalledWith(
      expect.objectContaining({
        method: 'delete',
        url: `/ai/conversations/${conversationFixture.oid}`
      })
    )
  })

  it('删除被活跃 run 拦下时抛带错误码的 AI 错误', async () => {
    axiosMock.mockResolvedValueOnce({
      status: 409,
      data: { detail: 'AI_RUN_CONFLICT' },
      headers: {}
    })

    const failure = await deleteConversation(conversationFixture.oid).catch(
      (error: unknown) => error
    )

    expect(failure).toBeInstanceOf(AiRequestError)
    expect(failure).toMatchObject({ status: 409, code: 'AI_RUN_CONFLICT' })
  })
})

describe('压缩上下文', () => {
  it('POST /ai/conversations/{oid}/compact，不带请求体', async () => {
    axiosMock.mockResolvedValueOnce(
      ok({
        compacted: true,
        upto_seq: 12,
        tokens_before: 9000,
        tokens_after: 1200,
        degraded: false,
        reason: null
      })
    )

    const result = await compactConversation(conversationFixture.oid)

    expect(result).toEqual({
      compacted: true,
      upto_seq: 12,
      tokens_before: 9000,
      tokens_after: 1200,
      degraded: false,
      reason: null
    })
    expect(axiosMock).toHaveBeenCalledWith(
      expect.objectContaining({
        method: 'post',
        url: `/ai/conversations/${conversationFixture.oid}/compact`
      })
    )
    expect(vi.mocked(axiosMock).mock.calls[0]![0]).toMatchObject({ data: undefined })
  })

  it('没有可压缩内容：compacted=false 带上原因', async () => {
    axiosMock.mockResolvedValueOnce(
      ok({
        compacted: false,
        upto_seq: null,
        tokens_before: 300,
        tokens_after: 300,
        degraded: false,
        reason: 'nothing_to_compact'
      })
    )

    const result = await compactConversation(conversationFixture.oid)

    expect(result.compacted).toBe(false)
    expect(result.upto_seq).toBeNull()
    expect(result.reason).toBe('nothing_to_compact')
  })

  it('外壳缺字段时给安全默认值，不抛错', async () => {
    axiosMock.mockResolvedValueOnce(ok({}))

    const result = await compactConversation(conversationFixture.oid)

    expect(result).toEqual({
      compacted: false,
      upto_seq: null,
      tokens_before: 0,
      tokens_after: 0,
      degraded: false,
      reason: null
    })
  })

  it('有活跃 run 时抛 409 AI_RUN_CONFLICT', async () => {
    axiosMock.mockResolvedValueOnce({
      status: 409,
      data: { detail: 'AI_RUN_CONFLICT' },
      headers: {}
    })

    const failure = await compactConversation(conversationFixture.oid).catch(
      (error: unknown) => error
    )

    expect(failure).toBeInstanceOf(AiRequestError)
    expect(failure).toMatchObject({ status: 409, code: 'AI_RUN_CONFLICT' })
  })
})

describe('消息增量', () => {
  it('默认 after_seq=0', async () => {
    axiosMock.mockResolvedValueOnce(ok({ messages: [messageFixture], has_more: false, total: 1 }))

    const page = await listMessages(conversationFixture.oid)

    expect(page.messages).toEqual([messageFixture])
    expect(page.total).toBe(1)
    expect(axiosMock).toHaveBeenCalledWith(
      expect.objectContaining({
        method: 'get',
        url: `/ai/conversations/${conversationFixture.oid}/messages`,
        params: { after_seq: 0, limit: 50 }
      })
    )
  })

  it('带 after_seq 时只取增量', async () => {
    axiosMock.mockResolvedValueOnce(ok({ messages: [], has_more: false, total: 9 }))

    await listMessages(conversationFixture.oid, { afterSeq: 7 })

    expect(axiosMock).toHaveBeenCalledWith(
      expect.objectContaining({ params: { after_seq: 7, limit: 50 } })
    )
  })

  it('外壳不认识时给空数组', async () => {
    axiosMock.mockResolvedValueOnce(ok({ items: [] }))

    const page = await listMessages(conversationFixture.oid)

    expect(page.messages).toEqual([])
    expect(page.has_more).toBe(false)
  })
})

describe('后台执行', () => {
  it('提交：POST /ai/conversations/{oid}/runs，202 受理，不带 model', async () => {
    axiosMock.mockResolvedValueOnce(
      ok({ run_id: runFixture.oid, message_oid: messageFixture.oid, status: 'queued' }, 202)
    )

    const result = await submitRun(conversationFixture.oid, '查一下', { clientMsgId: 'abc' })

    expect(result.run_id).toBe(runFixture.oid)
    expect(result.status).toBe('queued')
    expect(axiosMock).toHaveBeenCalledWith(
      expect.objectContaining({
        method: 'post',
        url: `/ai/conversations/${conversationFixture.oid}/runs`,
        data: { content: '查一下', client_msg_id: 'abc' }
      })
    )
  })

  it('查询：GET /ai/runs/{id} 回 after_round 与 since_msg_seq 之外的增量', async () => {
    axiosMock.mockResolvedValueOnce(
      ok({
        run: runFixture,
        events: [{ round_no: 3, tool_name: 'fs_list', tool_kind: 'read', status: 'executed' }],
        proposals: [],
        messages: [messageFixture]
      })
    )

    const detail = await fetchRunDetail(runFixture.oid, 2, 7)

    expect(detail.run.rounds).toBe(2)
    expect(detail.events).toHaveLength(1)
    expect(detail.messages).toEqual([messageFixture])
    expect(axiosMock).toHaveBeenCalledWith(
      expect.objectContaining({
        method: 'get',
        url: `/ai/runs/${runFixture.oid}`,
        params: { after_round: 2, since_msg_seq: 7 }
      })
    )
  })

  it('查询：没传 since_msg_seq 时不带这个参数', async () => {
    axiosMock.mockResolvedValueOnce(ok({ run: runFixture, events: [], proposals: [] }))

    await fetchRunDetail(runFixture.oid)

    expect(axiosMock).toHaveBeenCalledWith(
      expect.objectContaining({ params: { after_round: 0 } })
    )
  })

  it('查询：外壳不认识时给一个空 run，界面不会崩', async () => {
    axiosMock.mockResolvedValueOnce(ok({}))

    const detail = await fetchRunDetail('missing')

    expect(detail.run.oid).toBe('missing')
    expect(detail.events).toEqual([])
    expect(detail.proposals).toEqual([])
  })

  it('中止：POST /ai/runs/{id}/cancel', async () => {
    axiosMock.mockResolvedValueOnce(ok({ status: 'cancelled' }))

    const result = await cancelRun(runFixture.oid)

    expect(result.status).toBe('cancelled')
    expect(axiosMock).toHaveBeenCalledWith(
      expect.objectContaining({
        method: 'post',
        url: `/ai/runs/${runFixture.oid}/cancel`
      })
    )
  })

  it('活跃 run：GET /ai/runs?active=1', async () => {
    axiosMock.mockResolvedValueOnce(ok({ runs: [runFixture] }))

    const runs = await fetchActiveRuns()

    expect(runs).toEqual([runFixture])
    expect(axiosMock).toHaveBeenCalledWith(
      expect.objectContaining({ method: 'get', url: '/ai/runs', params: { active: 1 } })
    )
  })

  it('活跃 run：没有就给空数组', async () => {
    axiosMock.mockResolvedValueOnce(ok({}))

    await expect(fetchActiveRuns()).resolves.toEqual([])
  })

  it('run 不存在（404）时抛带错误码的 AI 错误', async () => {
    axiosMock.mockResolvedValueOnce({
      status: 404,
      data: { detail: 'AI_RUN_NOT_FOUND' },
      headers: {}
    })

    const failure = await fetchRunDetail('missing').catch((error: unknown) => error)

    expect(failure).toMatchObject({ status: 404, code: 'AI_RUN_NOT_FOUND' })
  })
})
