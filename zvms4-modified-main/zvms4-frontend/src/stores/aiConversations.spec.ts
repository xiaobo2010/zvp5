import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useAiConversationsStore } from './aiConversations'
import { registerAiAbort } from '@/api/ai/activeRuns'
import type { AiConversation, AiMessage, AiRun } from '@/../types/ai'

/**
 * 会话状态的形状与节奏。
 *
 * 重点在两处：消息按 after_seq 增量（不重拉整段历史），以及 run 的轮询
 * 在终态立刻停（没有活跃 run 就不该有常驻定时器）。
 */

const mocks = vi.hoisted(() => ({
  cancelRun: vi.fn(),
  compactConversation: vi.fn(),
  createConversation: vi.fn(),
  deleteConversation: vi.fn(),
  fetchActiveRuns: vi.fn(),
  fetchRunDetail: vi.fn(),
  getConversation: vi.fn(),
  listConversations: vi.fn(),
  listMessages: vi.fn(),
  submitRun: vi.fn(),
  updateConversation: vi.fn()
}))

vi.mock('@/api/ai', () => mocks)

function conversation(overrides: Partial<AiConversation> = {}): AiConversation {
  return {
    oid: 'c1'.repeat(16),
    mode: 'agent',
    title: '查一下图书馆',
    model: 'gpt-4o-mini',
    status: 'active',
    tokens: { prompt: 120, completion: 30, total: 150 },
    context: { tokens: 120, limit: 32768, ratio: 0.0037 },
    compaction: { upto_seq: 0, summarized: false, degraded_at: null },
    message_count: 2,
    created_at: '2026-10-25T00:00:00',
    updated_at: '2026-10-25T01:00:00',
    ...overrides
  }
}

function message(seq: number, overrides: Partial<AiMessage> = {}): AiMessage {
  return {
    oid: `m${seq}`.repeat(16),
    seq,
    role: 'user',
    content: `第 ${seq} 条`,
    created_at: '2026-10-25T00:01:00',
    ...overrides
  }
}

function run(overrides: Partial<AiRun> = {}): AiRun {
  return {
    oid: 'r1'.repeat(16),
    conversation_oid: 'c1'.repeat(16),
    status: 'running',
    model: 'gpt-4o-mini',
    stop_reason: null,
    rounds: 1,
    error_code: null,
    error_message: null,
    result_message_oid: null,
    prompt_tokens: 100,
    completion_tokens: 20,
    total_tokens: 120,
    cancel_requested: false,
    created_at: '2026-10-25T00:02:00',
    started_at: '2026-10-25T00:02:01',
    finished_at: null,
    ...overrides
  }
}

beforeEach(() => {
  setActivePinia(createPinia())
  for (const mock of Object.values(mocks)) {
    mock.mockReset()
  }
  mocks.listConversations.mockResolvedValue({
    conversations: [conversation()],
    next_cursor: null,
    size: 1
  })
  mocks.listMessages.mockResolvedValue({ messages: [message(1)], has_more: false, total: 1 })
  mocks.fetchActiveRuns.mockResolvedValue([])
  mocks.fetchRunDetail.mockResolvedValue({ run: run(), events: [], proposals: [] })
  mocks.getConversation.mockResolvedValue(conversation())
})

describe('会话列表', () => {
  it('拉列表后填充会话与游标', async () => {
    const store = useAiConversationsStore()

    await store.loadConversations()

    expect(store.conversations).toHaveLength(1)
    expect(store.currentOid).toBe('')
    expect(store.listFailed).toBe(false)
  })

  it('只留智能体会话：历史 chat 不在列表里', async () => {
    mocks.listConversations.mockResolvedValue({
      conversations: [
        conversation(),
        conversation({ oid: 'c2'.repeat(16), mode: 'chat', title: '历史对话' })
      ],
      next_cursor: null,
      size: 2
    })
    const store = useAiConversationsStore()

    await store.loadConversations()

    expect(store.conversations.map((item) => item.oid)).toEqual([conversation().oid])
  })

  it('拉列表失败时给出失败标记并清空列表', async () => {
    mocks.listConversations.mockRejectedValue(new Error('boom'))
    const store = useAiConversationsStore()

    await store.loadConversations()

    expect(store.listFailed).toBe(true)
    expect(store.conversations).toEqual([])
  })

  it('游标翻页时追加且不重复', async () => {
    mocks.listConversations.mockResolvedValueOnce({
      conversations: [conversation()],
      next_cursor: 'cursor-1',
      size: 1
    })
    mocks.listConversations.mockResolvedValueOnce({
      conversations: [conversation(), conversation({ oid: 'c2'.repeat(16), title: '第二个' })],
      next_cursor: null,
      size: 2
    })
    const store = useAiConversationsStore()

    await store.loadConversations()
    await store.loadMoreConversations()

    expect(store.conversations).toHaveLength(2)
    expect(store.nextCursor).toBeNull()
  })

  it('新建会话插到列表最前', async () => {
    mocks.createConversation.mockResolvedValue(conversation({ oid: 'c9'.repeat(16) }))
    const store = useAiConversationsStore()
    await store.loadConversations()

    await store.createConversation('agent')

    expect(store.conversations[0]!.oid).toBe('c9'.repeat(16))
  })

  it('改名后列表里的标题跟着变', async () => {
    mocks.updateConversation.mockResolvedValue(conversation({ title: '新标题' }))
    const store = useAiConversationsStore()
    await store.loadConversations()

    await store.renameConversation(conversation().oid, '新标题')

    expect(store.conversations[0]!.title).toBe('新标题')
  })

  it('归档后从活动列表里移除', async () => {
    mocks.updateConversation.mockResolvedValue(conversation({ status: 'archived' }))
    const store = useAiConversationsStore()
    await store.loadConversations()

    await store.setConversationStatus(conversation().oid, 'archived')

    expect(store.conversations).toEqual([])
  })

  it('归档列表独立拉取，不与活动列表混存', async () => {
    mocks.listConversations.mockImplementation(
      (options: { status?: string } = {}) =>
        Promise.resolve(
          options.status === 'archived'
            ? {
                conversations: [conversation({ status: 'archived' })],
                next_cursor: null,
                size: 1
              }
            : { conversations: [conversation()], next_cursor: null, size: 1 }
        )
    )
    const store = useAiConversationsStore()
    await store.loadConversations()

    await store.loadArchivedConversations()

    expect(mocks.listConversations).toHaveBeenLastCalledWith({ status: 'archived' })
    expect(store.conversations.map((item) => item.oid)).toEqual([conversation().oid])
    expect(store.archivedConversations.map((item) => item.oid)).toEqual([conversation().oid])
    expect(store.archivedFailed).toBe(false)
  })

  it('恢复归档会话：归档列表移除，活动列表刷新回来', async () => {
    mocks.listConversations.mockImplementation(
      (options: { status?: string } = {}) =>
        Promise.resolve(
          options.status === 'archived'
            ? {
                conversations: [conversation({ status: 'archived' })],
                next_cursor: null,
                size: 1
              }
            : { conversations: [conversation()], next_cursor: null, size: 1 }
        )
    )
    mocks.updateConversation.mockResolvedValue(conversation({ status: 'active' }))
    const store = useAiConversationsStore()
    await store.loadConversations()
    await store.loadArchivedConversations()

    await store.unarchiveConversation(conversation().oid)

    expect(mocks.updateConversation).toHaveBeenCalledWith(conversation().oid, { status: 'active' })
    expect(store.archivedConversations).toEqual([])
    expect(store.conversations.map((item) => item.oid)).toEqual([conversation().oid])
  })

  it('归档列表拉取失败时给出失败标记并清空', async () => {
    mocks.listConversations.mockRejectedValue(new Error('boom'))
    const store = useAiConversationsStore()

    await store.loadArchivedConversations()

    expect(store.archivedFailed).toBe(true)
    expect(store.archivedConversations).toEqual([])
  })

  it('删除当前会话时把当前状态一并清掉', async () => {
    mocks.deleteConversation.mockResolvedValue({ deleted: true, messages: 1, runs: 0 })
    const store = useAiConversationsStore()
    await store.loadConversations()
    await store.selectConversation(conversation().oid)

    await store.removeConversation(conversation().oid)

    expect(store.currentOid).toBe('')
    expect(store.messages).toEqual([])
  })
})

describe('消息增量', () => {
  it('切会话时拉消息并设当前会话', async () => {
    const store = useAiConversationsStore()
    await store.loadConversations()

    await store.selectConversation(conversation().oid)

    expect(store.current?.oid).toBe(conversation().oid)
    expect(store.messages).toHaveLength(1)
    expect(mocks.listMessages).toHaveBeenCalledWith(conversation().oid, {
      afterSeq: 0,
      limit: 50
    })
  })

  it('同一个会话不重复拉', async () => {
    const store = useAiConversationsStore()
    await store.loadConversations()
    await store.selectConversation(conversation().oid)
    const calls = mocks.listMessages.mock.calls.length

    await store.selectConversation(conversation().oid)

    expect(mocks.listMessages.mock.calls.length).toBe(calls)
  })

  it('长会话切到尾部窗口：先知道总数，再按 after_seq 取尾部', async () => {
    mocks.listMessages.mockResolvedValueOnce({
      messages: [message(1), message(2)],
      has_more: true,
      total: 120
    })
    mocks.listMessages.mockResolvedValueOnce({
      messages: [message(118), message(119), message(120)],
      has_more: false,
      total: 120
    })
    const store = useAiConversationsStore()
    await store.loadConversations()

    await store.selectConversation(conversation().oid)

    expect(mocks.listMessages).toHaveBeenNthCalledWith(2, conversation().oid, {
      afterSeq: 118,
      limit: 50
    })
    expect(store.orderedMessages.map((item) => item.seq)).toEqual([118, 119, 120])
    expect(store.earlierCount).toBe(117)
  })

  it('往上翻：把窗口前面那一段也加载进来', async () => {
    mocks.listMessages.mockResolvedValueOnce({
      messages: [message(1)],
      has_more: true,
      total: 120
    })
    mocks.listMessages.mockResolvedValueOnce({
      messages: [message(119), message(120)],
      has_more: false,
      total: 120
    })
    mocks.listMessages.mockResolvedValueOnce({
      messages: [message(69), message(70)],
      has_more: false,
      total: 120
    })
    const store = useAiConversationsStore()
    await store.loadConversations()
    await store.selectConversation(conversation().oid)

    await store.loadEarlierMessages()

    // 窗口起点是 119，往前翻就是拿更小的 after_seq 再要一页。
    expect(mocks.listMessages).toHaveBeenNthCalledWith(3, conversation().oid, {
      afterSeq: 69,
      limit: 50
    })
    expect(store.orderedMessages.map((item) => item.seq)).toEqual([69, 70, 119, 120])
  })

  it('增量刷新只带最新的 seq，不重拉整段历史', async () => {
    mocks.listMessages.mockResolvedValueOnce({
      messages: [message(1), message(2)],
      has_more: false,
      total: 2
    })
    const store = useAiConversationsStore()
    await store.loadConversations()
    await store.selectConversation(conversation().oid)
    mocks.listMessages.mockClear()

    await store.refreshMessages()

    expect(mocks.listMessages).toHaveBeenCalledWith(conversation().oid, {
      afterSeq: 2,
      limit: 50
    })
  })
})

describe('后台执行', () => {
  it('提交后立刻进入运行态并开始轮询', async () => {
    mocks.submitRun.mockResolvedValue({
      run_id: 'r1'.repeat(16),
      message_oid: 'm1'.repeat(16),
      status: 'queued'
    })
    const store = useAiConversationsStore()
    await store.loadConversations()
    await store.selectConversation(conversation().oid)

    await store.submitRun(conversation().oid, '查一下')

    expect(store.activeRun?.status).toBe('queued')
    expect(store.polling).toBe(true)
    expect(mocks.submitRun).toHaveBeenCalledWith(
      conversation().oid,
      '查一下',
      expect.anything()
    )
  })

  it('轮询到终态就停：不再发请求', async () => {
    vi.useFakeTimers()
    try {
      mocks.submitRun.mockResolvedValue({
        run_id: 'r1'.repeat(16),
        message_oid: 'm1'.repeat(16),
        status: 'queued'
      })
      mocks.fetchRunDetail.mockResolvedValue({
        run: run({ status: 'succeeded', stop_reason: 'completed' }),
        events: [],
        proposals: []
      })
      const store = useAiConversationsStore()
      await store.loadConversations()
      await store.selectConversation(conversation().oid)
      await store.submitRun(conversation().oid, '查一下')
      const calls = mocks.fetchRunDetail.mock.calls.length

      await vi.advanceTimersByTimeAsync(1500)
      await vi.advanceTimersByTimeAsync(5000)

      expect(mocks.fetchRunDetail.mock.calls.length).toBe(calls + 1)
      expect(store.polling).toBe(false)
    } finally {
      vi.useRealTimers()
    }
  })

  it('页面不可见时轮询降到 5 秒', async () => {
    vi.useFakeTimers()
    const hidden = vi.spyOn(document, 'hidden', 'get').mockReturnValue(true)
    try {
      const store = useAiConversationsStore()
      await store.loadConversations()
      await store.selectConversation(conversation().oid)
      mocks.fetchRunDetail.mockClear()

      store.startPolling('r1'.repeat(16))
      await vi.advanceTimersByTimeAsync(1500)
      expect(mocks.fetchRunDetail).not.toHaveBeenCalled()

      await vi.advanceTimersByTimeAsync(3500)
      expect(mocks.fetchRunDetail).toHaveBeenCalledTimes(1)
    } finally {
      hidden.mockRestore()
      vi.useRealTimers()
    }
  })

  it('增量事件按去重键合并，展开态不被冲掉', async () => {
    const event = {
      round_no: 1,
      tool_name: 'fs_list',
      tool_kind: 'read' as const,
      args_summary: null,
      result_summary: '3 个文件',
      status: 'executed' as const
    }
    mocks.fetchRunDetail.mockResolvedValue({ run: run(), events: [event], proposals: [] })
    const store = useAiConversationsStore()
    await store.loadConversations()
    await store.selectConversation(conversation().oid)

    await store.refreshRun('r1'.repeat(16))
    await store.refreshRun('r1'.repeat(16))

    expect(store.runEvents).toHaveLength(1)
  })

  it('进入页面先问一次活跃 run：有就接管', async () => {
    mocks.fetchActiveRuns.mockResolvedValue([run({ status: 'running' })])
    const store = useAiConversationsStore()

    const runs = await store.loadActiveRuns()

    expect(runs).toHaveLength(1)
    await store.adoptRun(runs[0]!)
    expect(store.activeRun?.oid).toBe('r1'.repeat(16))
    expect(store.polling).toBe(true)
  })

  it('中止：调取消接口', async () => {
    mocks.cancelRun.mockResolvedValue({ status: 'cancelled' })
    mocks.submitRun.mockResolvedValue({
      run_id: 'r1'.repeat(16),
      message_oid: 'm1'.repeat(16),
      status: 'queued'
    })
    const store = useAiConversationsStore()
    await store.loadConversations()
    await store.selectConversation(conversation().oid)
    await store.submitRun(conversation().oid, '查一下')

    await store.cancelCurrentRun()

    expect(mocks.cancelRun).toHaveBeenCalledWith('r1'.repeat(16))
  })

  it('提交失败：轮询停掉、run 复位、错误留给调用方，不留下「执行中」假态', async () => {
    mocks.submitRun.mockResolvedValueOnce({
      run_id: 'r1'.repeat(16),
      message_oid: 'm1'.repeat(16),
      status: 'queued'
    })
    const store = useAiConversationsStore()
    await store.loadConversations()
    await store.selectConversation(conversation().oid)
    await store.submitRun(conversation().oid, '查一下')
    expect(store.polling).toBe(true)

    const failure = Object.assign(new Error('AI_RUN_CONFLICT'), {
      status: 409,
      code: 'AI_RUN_CONFLICT'
    })
    mocks.submitRun.mockRejectedValueOnce(failure)

    await expect(store.submitRun(conversation().oid, '再查一下')).rejects.toBe(failure)

    expect(store.activeRun).toBeNull()
    expect(store.polling).toBe(false)
    expect(store.runEvents).toEqual([])
    expect(store.runError).toBe(failure)
    // 已落库的消息不受影响。
    expect(store.orderedMessages.map((item) => item.seq)).toEqual([1])
  })

  it('abortAllRuns：停轮询 + 复位 run + 中止在途流，已落库消息保留', async () => {
    mocks.submitRun.mockResolvedValue({
      run_id: 'r1'.repeat(16),
      message_oid: 'm1'.repeat(16),
      status: 'queued'
    })
    const store = useAiConversationsStore()
    await store.loadConversations()
    await store.selectConversation(conversation().oid)
    await store.submitRun(conversation().oid, '查一下')
    expect(store.polling).toBe(true)
    expect(store.activeRun?.oid).toBe('r1'.repeat(16))

    const aborted: Array<string | undefined> = []
    const off = registerAiAbort((reason) => aborted.push(reason))
    try {
      store.abortAllRuns('banned')
    } finally {
      off()
    }

    expect(aborted).toEqual(['banned'])
    expect(store.polling).toBe(false)
    expect(store.activeRun).toBeNull()
    // 消息是服务端的既成事实：中止执行不清消息。
    expect(store.orderedMessages.map((item) => item.seq)).toEqual([1])
    // 中止后再调一次也不炸（幂等）。
    expect(() => store.abortAllRuns()).not.toThrow()
  })

  it('abortAllRuns 之后不会再重排轮询（封禁不该继续刷必定失败的请求）', async () => {
    vi.useFakeTimers()
    try {
      mocks.submitRun.mockResolvedValue({
        run_id: 'r1'.repeat(16),
        message_oid: 'm1'.repeat(16),
        status: 'queued'
      })
      const store = useAiConversationsStore()
      await store.loadConversations()
      await store.selectConversation(conversation().oid)
      await store.submitRun(conversation().oid, '查一下')
      const calls = mocks.fetchRunDetail.mock.calls.length

      store.abortAllRuns('banned')
      await vi.advanceTimersByTimeAsync(1500)
      await vi.advanceTimersByTimeAsync(5000)

      expect(mocks.fetchRunDetail.mock.calls.length).toBe(calls)
      expect(store.polling).toBe(false)
      expect(store.activeRun).toBeNull()
    } finally {
      vi.useRealTimers()
    }
  })
})

describe('派生状态', () => {
  it('上下文比例按 token / limit 算，80% 起警示', async () => {
    mocks.listConversations.mockResolvedValue({
      conversations: [
        conversation({
          context: { tokens: 300, limit: 1000, ratio: 0.3 }
        })
      ],
      next_cursor: null,
      size: 1
    })
    const store = useAiConversationsStore()
    await store.loadConversations()
    await store.selectConversation(conversation().oid)

    expect(store.contextRatio).toBeCloseTo(0.3)
    expect(store.contextWarning).toBe(false)

    // 80% 就是阈值：刚好到线就警示（服务端在这一档自动压缩）。
    store.current = conversation({ context: { tokens: 800, limit: 1000, ratio: 0.8 } })
    expect(store.contextWarning).toBe(true)

    store.current = conversation({ context: { tokens: 790, limit: 1000, ratio: 0.79 } })
    expect(store.contextWarning).toBe(false)
  })

  it('压缩点条数来自会话的 upto_seq', async () => {
    mocks.listConversations.mockResolvedValue({
      conversations: [
        conversation({ compaction: { upto_seq: 12, summarized: true, degraded_at: null } })
      ],
      next_cursor: null,
      size: 1
    })
    const store = useAiConversationsStore()
    await store.loadConversations()
    await store.selectConversation(conversation().oid)

    expect(store.compactedCount).toBe(12)
  })
})

describe('手动压缩上下文', () => {
  it('compacted=true：重拉会话详情，进度条按后端口径回落', async () => {
    mocks.compactConversation.mockResolvedValue({
      compacted: true,
      upto_seq: 8,
      tokens_before: 900,
      tokens_after: 200,
      degraded: false,
      reason: null
    })
    mocks.getConversation.mockResolvedValue(
      conversation({
        context: { tokens: 200, limit: 1000, ratio: 0.2 },
        compaction: { upto_seq: 8, summarized: true, degraded_at: null }
      })
    )
    const store = useAiConversationsStore()
    await store.loadConversations()
    await store.selectConversation(conversation().oid)

    const result = await store.compactConversation(conversation().oid)

    expect(mocks.compactConversation).toHaveBeenCalledWith(conversation().oid)
    expect(mocks.getConversation).toHaveBeenCalledWith(conversation().oid)
    expect(result.compacted).toBe(true)
    expect(store.current?.context.tokens).toBe(200)
    expect(store.contextRatio).toBeCloseTo(0.2)
    expect(store.contextWarning).toBe(false)
    expect(store.compactedCount).toBe(8)
  })

  it('compacted=false：不刷新详情，原样把原因交回调用方', async () => {
    mocks.compactConversation.mockResolvedValue({
      compacted: false,
      upto_seq: null,
      tokens_before: 120,
      tokens_after: 120,
      degraded: false,
      reason: 'nothing_to_compact'
    })
    const store = useAiConversationsStore()
    await store.loadConversations()
    await store.selectConversation(conversation().oid)
    mocks.getConversation.mockClear()

    const result = await store.compactConversation(conversation().oid)

    expect(mocks.getConversation).not.toHaveBeenCalled()
    expect(result.compacted).toBe(false)
    expect(result.reason).toBe('nothing_to_compact')
    expect(store.current?.context.tokens).toBe(120)
  })

  it('失败：把错误抛给调用方，当前会话不动', async () => {
    mocks.compactConversation.mockRejectedValue(new Error('boom'))
    const store = useAiConversationsStore()
    await store.loadConversations()
    await store.selectConversation(conversation().oid)
    mocks.getConversation.mockClear()

    await expect(store.compactConversation(conversation().oid)).rejects.toThrow('boom')

    expect(mocks.getConversation).not.toHaveBeenCalled()
    expect(store.current?.context.tokens).toBe(120)
  })

  it('409 有活跃 run：冲突原样传播，不吞成「无内容可压」', async () => {
    const conflict = Object.assign(new Error('AI_RUN_CONFLICT'), {
      status: 409,
      code: 'AI_RUN_CONFLICT'
    })
    mocks.compactConversation.mockRejectedValue(conflict)
    const store = useAiConversationsStore()
    await store.loadConversations()
    await store.selectConversation(conversation().oid)

    await expect(store.compactConversation(conversation().oid)).rejects.toMatchObject({
      status: 409,
      code: 'AI_RUN_CONFLICT'
    })
  })
})

describe('模型锁定与思考强度', () => {
  it('建会话时把预选模型一起提交', async () => {
    mocks.createConversation.mockResolvedValue(conversation({ oid: 'c9'.repeat(16) }))
    const store = useAiConversationsStore()
    await store.loadConversations()

    await store.createConversation('agent', 'gpt-4o-mini')

    expect(mocks.createConversation).toHaveBeenCalledWith('agent', undefined, 'gpt-4o-mini')
  })

  it('空会话换模型：PATCH model 并同步当前会话', async () => {
    mocks.updateConversation.mockResolvedValue(conversation({ model: 'gpt-4.1' }))
    const store = useAiConversationsStore()
    await store.loadConversations()
    await store.selectConversation(conversation().oid)

    await store.setConversationModel(conversation().oid, 'gpt-4.1')

    expect(mocks.updateConversation).toHaveBeenCalledWith(conversation().oid, {
      model: 'gpt-4.1'
    })
    expect(store.current?.model).toBe('gpt-4.1')
    expect(store.conversations[0]!.model).toBe('gpt-4.1')
  })

  it('换模型失败（有消息后 409）：本地先乐观改，失败回滚成旧值', async () => {
    mocks.updateConversation.mockRejectedValue(new Error('AI_CONVERSATION_MODEL_LOCKED'))
    const store = useAiConversationsStore()
    await store.loadConversations()
    await store.selectConversation(conversation().oid)

    await expect(store.setConversationModel(conversation().oid, 'gpt-4.1')).rejects.toThrow(
      'AI_CONVERSATION_MODEL_LOCKED'
    )

    expect(store.current?.model).toBe('gpt-4o-mini')
    expect(store.conversations[0]!.model).toBe('gpt-4o-mini')
  })

  it('改思考强度：PATCH 会话并同步当前值', async () => {
    mocks.updateConversation.mockResolvedValue(conversation({ thinking: 'medium' }))
    const store = useAiConversationsStore()
    await store.loadConversations()
    await store.selectConversation(conversation().oid)

    await store.setConversationThinking(conversation().oid, 'medium')

    expect(mocks.updateConversation).toHaveBeenCalledWith(conversation().oid, {
      thinking: 'medium'
    })
    expect(store.current?.thinking).toBe('medium')
  })

  it('默认档传 null', async () => {
    mocks.updateConversation.mockResolvedValue(conversation({ thinking: null }))
    const store = useAiConversationsStore()
    await store.loadConversations()
    await store.selectConversation(conversation().oid)

    await store.setConversationThinking(conversation().oid, null)

    expect(mocks.updateConversation).toHaveBeenCalledWith(conversation().oid, { thinking: null })
  })
})

describe('回复及时同步', () => {
  it('轮询带上 since_msg_seq：增量消息按 seq 追加并去重', async () => {
    vi.useFakeTimers()
    try {
      mocks.submitRun.mockResolvedValue({
        run_id: run().oid,
        message_oid: 'm1'.repeat(16),
        status: 'queued'
      })
      mocks.fetchRunDetail.mockResolvedValue({
        run: run(),
        events: [],
        proposals: [],
        messages: [message(2, { role: 'assistant', content: '第一轮结论' })]
      })
      const store = useAiConversationsStore()
      await store.loadConversations()
      await store.selectConversation(conversation().oid)
      await store.submitRun(conversation().oid, '查一下')

      await vi.advanceTimersByTimeAsync(1500)

      expect(mocks.fetchRunDetail).toHaveBeenCalledWith(run().oid, 0, 1)
      expect(store.orderedMessages.map((item) => item.seq)).toEqual([1, 2])
      expect(store.orderedMessages[1]!.content).toBe('第一轮结论')

      // 后端重复回同一条（或下一拍又把它带上）不会再插一遍。
      await store.refreshRun(run().oid)
      expect(mocks.fetchRunDetail).toHaveBeenLastCalledWith(run().oid, 1, 2)
      expect(store.orderedMessages.map((item) => item.seq)).toEqual([1, 2])
    } finally {
      vi.useRealTimers()
    }
  })

  it('终态后仍补一次消息增量做兜底', async () => {
    vi.useFakeTimers()
    try {
      mocks.submitRun.mockResolvedValue({
        run_id: run().oid,
        message_oid: 'm1'.repeat(16),
        status: 'queued'
      })
      mocks.fetchRunDetail.mockResolvedValue({
        run: run({ status: 'succeeded', stop_reason: 'completed' }),
        events: [],
        proposals: [],
        messages: []
      })
      mocks.listMessages.mockResolvedValueOnce({
        messages: [message(1)],
        has_more: false,
        total: 1
      })
      mocks.listMessages.mockResolvedValue({
        messages: [message(1), message(2, { role: 'assistant', content: '跑完了' })],
        has_more: false,
        total: 2
      })
      const store = useAiConversationsStore()
      await store.loadConversations()
      await store.selectConversation(conversation().oid)
      await store.submitRun(conversation().oid, '查一下')
      mocks.listMessages.mockClear()

      await vi.advanceTimersByTimeAsync(1500)

      expect(store.polling).toBe(false)
      expect(mocks.listMessages).toHaveBeenCalledWith(conversation().oid, {
        afterSeq: 1,
        limit: 50
      })
      expect(store.orderedMessages.map((item) => item.seq)).toEqual([1, 2])
    } finally {
      vi.useRealTimers()
    }
  })

  it('轮询合并时 reasoning 原样保留：不因合并丢字段', async () => {
    vi.useFakeTimers()
    try {
      mocks.submitRun.mockResolvedValue({
        run_id: run().oid,
        message_oid: 'm1'.repeat(16),
        status: 'queued'
      })
      mocks.fetchRunDetail.mockResolvedValue({
        run: run(),
        events: [],
        proposals: [],
        messages: [
          message(2, {
            role: 'assistant',
            content: '结论',
            reasoning: '先看用户角色'
          })
        ]
      })
      const store = useAiConversationsStore()
      await store.loadConversations()
      await store.selectConversation(conversation().oid)
      await store.submitRun(conversation().oid, '查一下')

      await vi.advanceTimersByTimeAsync(1500)

      const assistant = store.orderedMessages.find((item) => item.seq === 2)
      expect(assistant?.reasoning).toBe('先看用户角色')
    } finally {
      vi.useRealTimers()
    }
  })
})
