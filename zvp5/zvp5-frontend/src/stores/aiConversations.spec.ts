import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { STREAM_RETRY_MAX_MS, STREAM_STALE_MS, useAiConversationsStore } from './aiConversations'
import { registerAiAbort } from '@/api/ai/activeRuns'
import type { AiRunEventFrame, AiRunEventsOutcome } from '@/api/ai/runEvents'
import type {
  AiConversation,
  AiMessage,
  AiRun,
  AiRunAssistantLive,
  AiRunToolLive
} from '@/../types/ai'

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
  streamRunEvents: vi.fn(),
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

interface StreamCall {
  runId: string
  signal?: AbortSignal
  onFrame: (frame: AiRunEventFrame) => void
  onActivity?: () => void
  settle: (outcome?: Partial<AiRunEventsOutcome>) => void
}

/** 可控的 SSE 替身：每次建连记录回调，测试自己决定推什么帧、怎么收尾。 */
function stubStream(): StreamCall[] {
  const calls: StreamCall[] = []
  mocks.streamRunEvents.mockImplementation(
    (
      runId: string,
      options: {
        signal?: AbortSignal
        onFrame: (frame: AiRunEventFrame) => void
        onActivity?: () => void
      }
    ) =>
      new Promise<AiRunEventsOutcome>((resolve) => {
        calls.push({
          runId,
          signal: options.signal,
          onFrame: options.onFrame,
          onActivity: options.onActivity,
          settle: (outcome) =>
            resolve({
              done: false,
              aborted: false,
              unsupported: false,
              started: false,
              error: null,
              status: null,
              ...outcome
            })
        })
      })
  )
  return calls
}

function assistantLive(seq: number, round: number, content: string): AiRunAssistantLive {
  return {
    type: 'assistant',
    seq,
    run_id: run().oid,
    round_no: round,
    stream_key: `${run().oid}:${round}`,
    content
  }
}

function toolLive(
  seq: number,
  status: AiRunToolLive['status'],
  extra: Partial<AiRunToolLive> = {}
): AiRunToolLive {
  return {
    type: 'tool',
    seq,
    run_id: run().oid,
    round_no: 1,
    tool_call_id: 'call-1',
    tool_name: 'fs_list',
    tool_kind: 'read',
    status,
    args_summary: '{"path":"/"}',
    ...extra
  }
}

/**
 * 每个用例都记下自己建的 store：SSE 会在 document 上挂可见性监听，
 * 不清干净会串到下一条用例里（同一份 jsdom document）。
 */
const created: Array<ReturnType<typeof useAiConversationsStore>> = []

function newStore(): ReturnType<typeof useAiConversationsStore> {
  const store = useAiConversationsStore()
  created.push(store)
  return store
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
  // 默认 SSE 挂起不返回：既有的轮询测试因此保持「只轮询」的旧节奏。
  mocks.streamRunEvents.mockImplementation(() => new Promise(() => {}))
})

afterEach(() => {
  for (const store of created) {
    store.stopRunStream()
    store.stopPolling()
  }
  created.length = 0
  vi.unstubAllGlobals()
})

describe('会话列表', () => {
  it('拉列表后填充会话与游标', async () => {
    const store = newStore()

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
    const store = newStore()

    await store.loadConversations()

    expect(store.conversations.map((item) => item.oid)).toEqual([conversation().oid])
  })

  it('拉列表失败时给出失败标记并清空列表', async () => {
    mocks.listConversations.mockRejectedValue(new Error('boom'))
    const store = newStore()

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
    const store = newStore()

    await store.loadConversations()
    await store.loadMoreConversations()

    expect(store.conversations).toHaveLength(2)
    expect(store.nextCursor).toBeNull()
  })

  it('新建会话插到列表最前', async () => {
    mocks.createConversation.mockResolvedValue(conversation({ oid: 'c9'.repeat(16) }))
    const store = newStore()
    await store.loadConversations()

    await store.createConversation('agent')

    expect(store.conversations[0]!.oid).toBe('c9'.repeat(16))
  })

  it('改名后列表里的标题跟着变', async () => {
    mocks.updateConversation.mockResolvedValue(conversation({ title: '新标题' }))
    const store = newStore()
    await store.loadConversations()

    await store.renameConversation(conversation().oid, '新标题')

    expect(store.conversations[0]!.title).toBe('新标题')
  })

  it('归档后从活动列表里移除', async () => {
    mocks.updateConversation.mockResolvedValue(conversation({ status: 'archived' }))
    const store = newStore()
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
    const store = newStore()
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
    const store = newStore()
    await store.loadConversations()
    await store.loadArchivedConversations()

    await store.unarchiveConversation(conversation().oid)

    expect(mocks.updateConversation).toHaveBeenCalledWith(conversation().oid, { status: 'active' })
    expect(store.archivedConversations).toEqual([])
    expect(store.conversations.map((item) => item.oid)).toEqual([conversation().oid])
  })

  it('归档列表拉取失败时给出失败标记并清空', async () => {
    mocks.listConversations.mockRejectedValue(new Error('boom'))
    const store = newStore()

    await store.loadArchivedConversations()

    expect(store.archivedFailed).toBe(true)
    expect(store.archivedConversations).toEqual([])
  })

  it('删除当前会话时把当前状态一并清掉', async () => {
    mocks.deleteConversation.mockResolvedValue({ deleted: true, messages: 1, runs: 0 })
    const store = newStore()
    await store.loadConversations()
    await store.selectConversation(conversation().oid)

    await store.removeConversation(conversation().oid)

    expect(store.currentOid).toBe('')
    expect(store.messages).toEqual([])
  })
})

describe('消息增量', () => {
  it('切会话时拉消息并设当前会话', async () => {
    const store = newStore()
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
    const store = newStore()
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
    const store = newStore()
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
    const store = newStore()
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
    const store = newStore()
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
    const store = newStore()
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
      const store = newStore()
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

  it('长 run 超过 60 秒后轮询退避到上限，但仍在跑', async () => {
    vi.useFakeTimers()
    try {
      mocks.submitRun.mockResolvedValue({
        run_id: 'r1'.repeat(16),
        message_oid: 'm1'.repeat(16),
        status: 'queued'
      })
      const store = newStore()
      await store.loadConversations()
      await store.selectConversation(conversation().oid)
      await store.submitRun(conversation().oid, '查一下')

      await vi.advanceTimersByTimeAsync(60_000)
      expect(store.polling).toBe(true)
      mocks.fetchRunDetail.mockClear()

      // 退避到 5 秒后，一个 5 秒窗口最多两拍；关键是别再按 2 秒刷。
      await vi.advanceTimersByTimeAsync(5_000)
      const polls = mocks.fetchRunDetail.mock.calls.length
      expect(polls).toBeGreaterThanOrEqual(1)
      expect(polls).toBeLessThanOrEqual(2)
      expect(store.polling).toBe(true)
    } finally {
      vi.useRealTimers()
    }
  })

  it('页面不可见时轮询降到 5 秒', async () => {
    vi.useFakeTimers()
    const hidden = vi.spyOn(document, 'hidden', 'get').mockReturnValue(true)
    try {
      const store = newStore()
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
    const store = newStore()
    await store.loadConversations()
    await store.selectConversation(conversation().oid)

    await store.refreshRun('r1'.repeat(16))
    await store.refreshRun('r1'.repeat(16))

    expect(store.runEvents).toHaveLength(1)
  })

  it('进入页面先问一次活跃 run：有就接管', async () => {
    mocks.fetchActiveRuns.mockResolvedValue([run({ status: 'running' })])
    const store = newStore()

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
    const store = newStore()
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
    const store = newStore()
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
    const store = newStore()
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
      const store = newStore()
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
    const store = newStore()
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
    const store = newStore()
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
    const store = newStore()
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
    const store = newStore()
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
    const store = newStore()
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
    const store = newStore()
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
    const store = newStore()
    await store.loadConversations()

    await store.createConversation('agent', 'gpt-4o-mini')

    expect(mocks.createConversation).toHaveBeenCalledWith('agent', undefined, 'gpt-4o-mini')
  })

  it('空会话换模型：PATCH model 并同步当前会话', async () => {
    mocks.updateConversation.mockResolvedValue(conversation({ model: 'gpt-4.1' }))
    const store = newStore()
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
    const store = newStore()
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
    const store = newStore()
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
    const store = newStore()
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
      const store = newStore()
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
      const store = newStore()
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
      const store = newStore()
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

describe('实时事件流（SSE）', () => {
  async function submitted() {
    mocks.submitRun.mockResolvedValue({
      run_id: run().oid,
      message_oid: 'm1'.repeat(16),
      status: 'queued'
    })
    const store = newStore()
    await store.loadConversations()
    await store.selectConversation(conversation().oid)
    await store.submitRun(conversation().oid, '查一下')
    return store
  }

  it('首帧 snapshot + live 即恢复：轮询停掉，累计文本与工具立即可读', async () => {
    const calls = stubStream()
    const store = await submitted()

    expect(store.streamStatus(run().oid)).toBe('connecting')
    expect(calls).toHaveLength(1)

    calls[0]!.onFrame({
      type: 'snapshot',
      seq: 5,
      run_id: run().oid,
      data: {
        run: run({ rounds: 1 }),
        events: [],
        proposals: [],
        messages: [message(2, { role: 'assistant', content: '第一轮' })]
      },
      live: {
        assistant: [assistantLive(3, 2, '正在')],
        tools: [toolLive(4, 'pending')]
      }
    })

    expect(store.streamStatus(run().oid)).toBe('live')
    // 健康 SSE 时轮询是重复劳动。
    expect(store.polling).toBe(false)
    expect(store.orderedMessages.map((item) => item.seq)).toEqual([1, 2])
    expect(store.liveAssistantFor(run().oid).map((item) => item.content)).toEqual(['正在'])
    expect(store.liveToolsFor(run().oid)[0]!.status).toBe('pending')
  })

  it('assistant 帧是累计文本：原地替换，按 round/seq 升序', async () => {
    const calls = stubStream()
    const store = await submitted()
    const stream = calls[0]!
    stream.onFrame({
      type: 'snapshot',
      seq: 1,
      run_id: run().oid,
      data: { run: run(), events: [], proposals: [] },
      live: { assistant: [], tools: [] }
    })

    stream.onFrame(assistantLive(2, 1, '正在'))
    stream.onFrame(assistantLive(3, 1, '正在查'))
    stream.onFrame(assistantLive(4, 1, '正在查图书馆'))
    stream.onFrame(assistantLive(5, 2, '第二轮'))

    const live = store.liveAssistantFor(run().oid)
    expect(live.map((item) => item.content)).toEqual(['正在查图书馆', '第二轮'])
    expect(live.map((item) => item.round_no)).toEqual([1, 2])
    expect(new Set(live.map((item) => item.stream_key)).size).toBe(2)
  })

  it('tool 帧按 call id 原地更新：pending → success，参数摘要不丢', async () => {
    const calls = stubStream()
    const store = await submitted()
    const stream = calls[0]!
    stream.onFrame({
      type: 'snapshot',
      seq: 1,
      run_id: run().oid,
      data: { run: run(), events: [], proposals: [] },
      live: { assistant: [], tools: [] }
    })

    stream.onFrame(toolLive(2, 'pending'))
    stream.onFrame(toolLive(3, 'success', { result_summary: '3 个文件' }))
    stream.onFrame(toolLive(4, 'success', { result_summary: '3 个文件' }))

    const tools = store.liveToolsFor(run().oid)
    expect(tools).toHaveLength(1)
    expect(tools[0]!.status).toBe('success')
    expect(tools[0]!.result_summary).toBe('3 个文件')
    expect(tools[0]!.args_summary).toBe('{"path":"/"}')
  })

  it('seq 去重：重复 / 回退的帧不再应用', async () => {
    const calls = stubStream()
    const store = await submitted()
    const stream = calls[0]!
    stream.onFrame({
      type: 'snapshot',
      seq: 5,
      run_id: run().oid,
      data: { run: run(), events: [], proposals: [] },
      live: { assistant: [assistantLive(3, 1, '累计文本')], tools: [] }
    })

    stream.onFrame(assistantLive(6, 1, '累计文本更多'))
    // 同一序号再来一帧（重发 / 回退）：必须忽略。
    stream.onFrame(assistantLive(6, 1, '不该出现'))
    stream.onFrame(assistantLive(5, 1, '更不该出现'))

    expect(store.liveAssistantFor(run().oid).map((item) => item.content)).toEqual(['累计文本更多'])
  })

  it('轮提交后的 snapshot 清掉旧 live：文本已经落库，不重复渲染', async () => {
    const calls = stubStream()
    const store = await submitted()
    const stream = calls[0]!
    stream.onFrame({
      type: 'snapshot',
      seq: 3,
      run_id: run().oid,
      data: { run: run(), events: [], proposals: [] },
      live: { assistant: [assistantLive(1, 1, '草稿')], tools: [toolLive(2, 'success')] }
    })
    expect(store.liveAssistantFor(run().oid)).toHaveLength(1)

    stream.onFrame({
      type: 'snapshot',
      seq: 6,
      run_id: run().oid,
      data: {
        run: run({ rounds: 1 }),
        events: [],
        proposals: [],
        messages: [message(2, { role: 'assistant', content: '草稿' })]
      },
      live: { assistant: [], tools: [] }
    })

    expect(store.liveAssistantFor(run().oid)).toEqual([])
    expect(store.liveToolsFor(run().oid)).toEqual([])
    expect(store.orderedMessages.map((item) => item.seq)).toEqual([1, 2])
  })

  it('重连：退避后重建连接，快照不会重复追加消息或工具', async () => {
    vi.useFakeTimers()
    try {
      const calls = stubStream()
      const store = await submitted()

      calls[0]!.onFrame({
        type: 'snapshot',
        seq: 4,
        run_id: run().oid,
        data: {
          run: run(),
          events: [],
          proposals: [],
          messages: [message(2, { role: 'assistant', content: '第一轮' })]
        },
        live: { assistant: [assistantLive(2, 1, '第二')], tools: [toolLive(3, 'pending')] }
      })
      calls[0]!.settle({ done: false })

      await vi.advanceTimersByTimeAsync(2_000)

      expect(calls).toHaveLength(2)
      expect(calls[1]!.signal).not.toBe(calls[0]!.signal)

      // 重连首帧是同一份快照：消息与工具都不能翻倍。
      calls[1]!.onFrame({
        type: 'snapshot',
        seq: 4,
        run_id: run().oid,
        data: {
          run: run(),
          events: [],
          proposals: [],
          messages: [message(2, { role: 'assistant', content: '第一轮' })]
        },
        live: {
          assistant: [assistantLive(2, 1, '第二')],
          tools: [toolLive(3, 'pending')]
        }
      })

      expect(store.orderedMessages.map((item) => item.seq)).toEqual([1, 2])
      expect(store.liveAssistantFor(run().oid)).toHaveLength(1)
      expect(store.liveToolsFor(run().oid)).toHaveLength(1)
      expect(store.streamStatus(run().oid)).toBe('live')
    } finally {
      vi.useRealTimers()
    }
  })

  it('terminal：连接关闭、ephemeral 清空、补一次消息增量', async () => {
    const calls = stubStream()
    const store = await submitted()
    const stream = calls[0]!
    stream.onFrame({
      type: 'snapshot',
      seq: 4,
      run_id: run().oid,
      data: {
        run: run({ rounds: 1 }),
        events: [],
        proposals: [],
        messages: [message(2, { role: 'assistant', content: '结论' })]
      },
      live: { assistant: [assistantLive(2, 2, '还在写')], tools: [] }
    })
    mocks.listMessages.mockClear()

    stream.onFrame({
      type: 'terminal',
      seq: 5,
      run_id: run().oid,
      status: 'succeeded',
      stop_reason: 'completed',
      error_code: null
    })
    // 终态收尾里的 refreshMessages 是 void 的：让它的微任务先跑完。
    for (let i = 0; i < 4; i += 1) {
      await Promise.resolve()
    }

    expect(store.streamStatus(run().oid)).toBe('closed')
    expect(store.polling).toBe(false)
    expect(stream.signal?.aborted).toBe(true)
    expect(store.liveAssistantFor(run().oid)).toEqual([])
    expect(mocks.listMessages).toHaveBeenCalledWith(conversation().oid, {
      afterSeq: 2,
      limit: 50
    })
  })

  it('503 / 不支持：回落到轮询，并在稍后自动重试 SSE', async () => {
    vi.useFakeTimers()
    try {
      const calls = stubStream()
      const store = await submitted()

      calls[0]!.settle({ unsupported: true, status: 503 })
      await Promise.resolve()

      expect(store.streamStatus(run().oid)).toBe('fallback')
      expect(store.polling).toBe(true)

      await vi.advanceTimersByTimeAsync(30_000)

      expect(calls).toHaveLength(2)
      // 轮询仍在跑（fallback 期间是主力）。
      expect(store.polling).toBe(true)

      calls[1]!.onFrame({
        type: 'snapshot',
        seq: 1,
        run_id: run().oid,
        data: { run: run(), events: [], proposals: [] },
        live: { assistant: [], tools: [] }
      })

      expect(store.streamStatus(run().oid)).toBe('live')
      expect(store.polling).toBe(false)
    } finally {
      vi.useRealTimers()
    }
  })

  it('abortAllRuns：断流、清 live、不留重连定时器', async () => {
    vi.useFakeTimers()
    try {
      const calls = stubStream()
      const store = await submitted()

      store.abortAllRuns('banned')

      expect(calls[0]!.signal?.aborted).toBe(true)
      expect(store.streamStatus(run().oid)).toBe('idle')
      expect(store.liveAssistantFor(run().oid)).toEqual([])
      expect(store.polling).toBe(false)

      await vi.advanceTimersByTimeAsync(STREAM_RETRY_MAX_MS + 1_000)
      expect(calls).toHaveLength(1)
    } finally {
      vi.useRealTimers()
    }
  })

  it('回到前台且连接可能已死：重建 SSE 并拉起轮询兜底', async () => {
    vi.useFakeTimers()
    const hidden = vi.spyOn(document, 'hidden', 'get').mockReturnValue(false)
    try {
      const calls = stubStream()
      const store = await submitted()

      calls[0]!.onFrame({
        type: 'snapshot',
        seq: 2,
        run_id: run().oid,
        data: { run: run(), events: [], proposals: [] },
        live: { assistant: [assistantLive(1, 1, '写到一半')], tools: [] }
      })
      expect(store.polling).toBe(false)

      // 没有心跳也没有业务帧，超过判活阈值。
      await vi.advanceTimersByTimeAsync(STREAM_STALE_MS + 1_000)
      document.dispatchEvent(new Event('visibilitychange'))

      expect(calls).toHaveLength(2)
      // 旧连接必须被显式收掉：generation 只让回调失效，socket 还占着。
      expect(calls[0]!.signal?.aborted).toBe(true)
      expect(store.polling).toBe(true)
      // 重连期间旧文本还在（等首帧 snapshot 替换，不闪空）。
      expect(store.liveAssistantFor(run().oid)).toHaveLength(1)
    } finally {
      hidden.mockRestore()
      vi.useRealTimers()
    }
  })

  it('error 帧后重连：旧连接先 abort，旧的晚到帧与收尾都不影响新连接', async () => {
    vi.useFakeTimers()
    try {
      const calls = stubStream()
      const store = await submitted()
      const first = calls[0]!
      first.onFrame({
        type: 'snapshot',
        seq: 2,
        run_id: run().oid,
        data: { run: run(), events: [], proposals: [] },
        live: { assistant: [assistantLive(1, 1, '旧连接')], tools: [] }
      })
      // 流内错误：服务端随后会关，但此刻连接还开着。
      first.onFrame({
        type: 'error',
        seq: 3,
        run_id: run().oid,
        code: 'AI_RUN_EVENTS_FAILED'
      })

      await vi.advanceTimersByTimeAsync(2_000)

      expect(calls).toHaveLength(2)
      expect(first.signal?.aborted).toBe(true)

      // 旧连接晚到的帧与收尾：generation 已失效，不能改 live、不能改状态。
      first.onFrame(assistantLive(4, 1, '旧连接晚到'))
      first.settle({ done: false })
      await Promise.resolve()
      expect(store.liveAssistantFor(run().oid).map((item) => item.content)).toEqual(['旧连接'])
      expect(store.streamStatus(run().oid)).toBe('connecting')

      // 新连接仍是「当前连接」：收尾能真的把它 abort 掉。
      store.stopPolling()
      expect(calls[1]!.signal?.aborted).toBe(true)
    } finally {
      vi.useRealTimers()
    }
  })

  it('连接还新鲜时回前台不重建连接', async () => {
    vi.useFakeTimers()
    const hidden = vi.spyOn(document, 'hidden', 'get').mockReturnValue(false)
    try {
      const calls = stubStream()
      const store = await submitted()

      calls[0]!.onFrame({
        type: 'snapshot',
        seq: 2,
        run_id: run().oid,
        data: { run: run(), events: [], proposals: [] },
        live: { assistant: [], tools: [] }
      })
      await vi.advanceTimersByTimeAsync(1_000)
      document.dispatchEvent(new Event('visibilitychange'))

      expect(calls).toHaveLength(1)
    } finally {
      hidden.mockRestore()
      vi.useRealTimers()
    }
  })
})
