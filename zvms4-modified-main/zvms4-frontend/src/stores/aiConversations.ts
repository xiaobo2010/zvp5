import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import {
  cancelRun as cancelRunApi,
  createConversation as createConversationApi,
  deleteConversation as deleteConversationApi,
  fetchActiveRuns as fetchActiveRunsApi,
  fetchRunDetail,
  getConversation as getConversationApi,
  listConversations,
  listMessages,
  submitRun as submitRunApi,
  updateConversation
} from '@/api/ai'
import type {
  AiConversation,
  AiConversationMode,
  AiConversationStatus,
  AiMessage,
  AiMessagePage,
  AiRun,
  AiRunDetail
} from '@/../types/ai'

/**
 * AI 会话状态。
 *
 * 一次会话的三个活动对象都在这里：会话列表（游标分页）、当前会话的消息
 * （按 after_seq 增量）、以及当前会话里那一次后台执行（run）的进度。
 *
 * 轮询节奏：前台 1.5 秒一次，页面不可见时降到 5 秒（别在后台标签页里
 * 刷请求）。run 落到终态立刻停：没有活跃 run 就不该有常驻定时器。
 */

/** 前台轮询间隔（毫秒）。 */
export const POLL_INTERVAL_MS = 1_500

/** 页面不可见时的轮询间隔（毫秒）。 */
export const POLL_INTERVAL_HIDDEN_MS = 5_000

/** run 的终态：落在这里就不再轮询。 */
const TERMINAL_RUN_STATUS = new Set(['succeeded', 'failed', 'cancelled', 'interrupted'])

/** 消息一页的条数：与服务端默认一致。 */
const MESSAGE_PAGE_SIZE = 50

/** 轮询间隔：页面不可见时降频。 */
export function pollIntervalMs(): number {
  if (typeof document !== 'undefined' && document.hidden) {
    return POLL_INTERVAL_HIDDEN_MS
  }
  return POLL_INTERVAL_MS
}

/** 是不是终态（终态即停轮询）。 */
export function isTerminalRun(status: string | null | undefined): boolean {
  return status ? TERMINAL_RUN_STATUS.has(status) : true
}

/** 事件的去重键：同一轮里同一个工具的同一条留痕不重复渲染。 */
function eventKey(event: { round_no: number; tool_name: string | null; result_summary: string }): string {
  return `${event.round_no}|${event.tool_name ?? ''}|${event.result_summary}`
}

export const useAiConversationsStore = defineStore('aiConversations', () => {
  /* --------------------------------------------------------------------- */
  /* 会话列表                                                               */
  /* --------------------------------------------------------------------- */

  const conversations = ref<AiConversation[]>([])
  const nextCursor = ref<string | null>(null)
  const listLoading = ref(false)
  const listFailed = ref(false)

  /* --------------------------------------------------------------------- */
  /* 当前会话与消息                                                         */
  /* --------------------------------------------------------------------- */

  const currentOid = ref('')
  const current = ref<AiConversation | null>(null)
  const messages = ref<AiMessage[]>([])
  const messagesLoading = ref(false)
  const messagesFailed = ref(false)
  const totalMessages = ref(0)

  /* --------------------------------------------------------------------- */
  /* 后台执行（run）                                                        */
  /* --------------------------------------------------------------------- */

  /** 当前会话里正在跑（或刚跑完）的那一次执行。 */
  const activeRun = ref<AiRun | null>(null)
  /** run 的事件留痕（增量追加）。 */
  const runEvents = ref<AiRunDetail['events']>([])
  /** run 产生的提案（增量追加）。 */
  const runProposals = ref<AiRunDetail['proposals']>([])
  /** 已经拉到第几轮；下一次请求带 after_round 只取增量。 */
  const runAfterRound = ref(0)
  /** 本人进行中的 run（进页面时先问一次，用于重连接管）。 */
  const activeRuns = ref<AiRun[]>([])
  /** 轮询是否在跑。 */
  const polling = ref(false)

  let pollTimer: ReturnType<typeof setTimeout> | null = null
  let pollingRunId = ''
  /** 已经成功拉过消息的会话（用来判断「要不要重拉」）。 */
  let loadedOid = ''

  /* --------------------------------------------------------------------- */
  /* 派生状态                                                               */
  /* --------------------------------------------------------------------- */

  /** 消息按 seq 排好（服务端已排序，这里再兜一层）。 */
  const orderedMessages = computed(() =>
    [...messages.value].sort((a, b) => a.seq - b.seq)
  )

  /** 上下文水位比例（0–1）；上游没报时给 0。 */
  const contextRatio = computed(() => {
    const context = current.value?.context
    if (!context || !context.limit) {
      return 0
    }
    if (typeof context.ratio === 'number' && context.ratio > 0) {
      return Math.min(1, context.ratio)
    }
    return Math.min(1, context.tokens / context.limit)
  })

  /** 水位达到 90% 就该警示。 */
  const contextWarning = computed(() => contextRatio.value >= 0.9)

  /** 已压缩掉的消息条数（压缩点之前）。 */
  const compactedCount = computed(() => current.value?.compaction.upto_seq ?? 0)

  /** 当前窗口前面还有多少条没加载（往上翻时才看得到）。 */
  const earlierCount = computed(() =>
    Math.max(0, totalMessages.value - messages.value.length)
  )

  /* --------------------------------------------------------------------- */
  /* 会话列表                                                               */
  /* --------------------------------------------------------------------- */

  /** 拉会话列表；带游标时是「加载更多」。 */
  async function loadConversations(options: { cursor?: string | null } = {}): Promise<void> {
    if (listLoading.value) {
      return
    }
    listLoading.value = true
    try {
      const page = await listConversations({
        status: 'active',
        cursor: options.cursor ?? null
      })
      // 对话模式已下线：列表接口暂不支持按 mode 过滤，这里在客户端筛掉。
      // chat 会话是历史遗留，保留在库里（不删、不改状态），只是不再出现在界面。
      const agentOnly = page.conversations.filter((item) => item.mode === 'agent')
      if (options.cursor) {
        const known = new Set(conversations.value.map((item) => item.oid))
        conversations.value = [
          ...conversations.value,
          ...agentOnly.filter((item) => !known.has(item.oid))
        ]
      } else {
        conversations.value = agentOnly
      }
      nextCursor.value = page.next_cursor
      listFailed.value = false
    } catch {
      if (!options.cursor) {
        conversations.value = []
        nextCursor.value = null
      }
      listFailed.value = true
    } finally {
      listLoading.value = false
    }
  }

  /** 加载下一页（游标分页）。 */
  async function loadMoreConversations(): Promise<void> {
    if (!nextCursor.value) {
      return
    }
    await loadConversations({ cursor: nextCursor.value })
  }

  /** 建一个会话并插到列表最前。 */
  async function createConversation(mode: AiConversationMode): Promise<AiConversation> {
    const created = await createConversationApi(mode)
    conversations.value = [created, ...conversations.value.filter((item) => item.oid !== created.oid)]
    return created
  }

  /** 改标题；成功后同步列表与当前会话。 */
  async function renameConversation(conversationOid: string, title: string): Promise<AiConversation> {
    const updated = await updateConversation(conversationOid, { title })
    applyConversation(updated)
    return updated
  }

  /** 改状态（归档 / 取消归档）。 */
  async function setConversationStatus(
    conversationOid: string,
    status: AiConversationStatus
  ): Promise<AiConversation> {
    const updated = await updateConversation(conversationOid, { status })
    applyConversation(updated)
    if (status === 'archived') {
      conversations.value = conversations.value.filter((item) => item.oid !== conversationOid)
    } else if (!conversations.value.some((item) => item.oid === conversationOid)) {
      conversations.value = [updated, ...conversations.value]
    }
    return updated
  }

  /** 硬删会话；同时清掉本地状态。 */
  async function removeConversation(conversationOid: string): Promise<void> {
    await deleteConversationApi(conversationOid)
    conversations.value = conversations.value.filter((item) => item.oid !== conversationOid)
    if (currentOid.value === conversationOid) {
      clearCurrent()
    }
  }

  /** 把服务端返回的会话视图同步进列表与当前会话。 */
  function applyConversation(conversation: AiConversation): void {
    const index = conversations.value.findIndex((item) => item.oid === conversation.oid)
    if (index >= 0) {
      conversations.value[index] = conversation
    }
    if (currentOid.value === conversation.oid) {
      current.value = conversation
    }
  }

  /* --------------------------------------------------------------------- */
  /* 消息                                                                   */
  /* --------------------------------------------------------------------- */

  /** 切换当前会话并拉它的消息。 */
  async function selectConversation(conversationOid: string): Promise<void> {
    if (currentOid.value === conversationOid && loadedOid === conversationOid) {
      return
    }
    // 换会话时先停掉上一个的轮询：run 的进度只属于它自己的会话。
    stopPolling()
    currentOid.value = conversationOid
    current.value = conversations.value.find((item) => item.oid === conversationOid) ?? null
    messages.value = []
    totalMessages.value = 0
    resetRun()
    const page = await loadMessages(conversationOid, { replace: true })
    if (!page) {
      return
    }
    // 长会话：界面要看的是最近的对话。第一页拿到 total 后再取尾部窗口。
    const size = page.messages.length
    if (page.total > size && size > 0) {
      await loadMessages(conversationOid, {
        afterSeq: Math.max(0, page.total - size),
        replace: true
      })
    }
  }

  /**
   * 拉消息。
   *
   * `replace` = 整段替换（切换会话）；否则按 after_seq 增量追加
   * （轮询到新消息时用它，不重拉整段历史）。
   */
  async function loadMessages(
    conversationOid: string,
    options: { afterSeq?: number; replace?: boolean } = {}
  ): Promise<AiMessagePage | null> {
    const afterSeq = options.afterSeq ?? 0
    if (options.replace) {
      messagesLoading.value = true
      loadedOid = ''
    }
    try {
      const page = await listMessages(conversationOid, { afterSeq, limit: MESSAGE_PAGE_SIZE })
      if (options.replace) {
        messages.value = page.messages
        loadedOid = conversationOid
      } else {
        const known = new Set(messages.value.map((item) => item.oid))
        messages.value = [
          ...messages.value,
          ...page.messages.filter((item) => !known.has(item.oid))
        ]
      }
      totalMessages.value = page.total
      messagesFailed.value = false
      return page
    } catch {
      if (options.replace) {
        messages.value = []
      }
      messagesFailed.value = true
      return null
    } finally {
      messagesLoading.value = false
    }
  }

  /** 增量补最新消息（run 落库后用）。 */
  async function refreshMessages(): Promise<void> {
    const conversationOid = currentOid.value
    if (!conversationOid) {
      return
    }
    const last = messages.value.reduce((max, item) => Math.max(max, item.seq), 0)
    await loadMessages(conversationOid, { afterSeq: last })
  }

  /**
   * 往上翻：把窗口前面那一段也加载进来。
   *
   * after_seq 是「取 seq 更大的那些」，所以往前翻就是拿更小的 after_seq 再
   * 拉一页：窗口起点前 50 条，一次一页。
   */
  async function loadEarlierMessages(): Promise<void> {
    const conversationOid = currentOid.value
    if (!conversationOid || earlierCount.value <= 0 || messagesLoading.value) {
      return
    }
    const first = messages.value.reduce(
      (min, item) => Math.min(min, item.seq),
      Number.POSITIVE_INFINITY
    )
    const start = Math.max(0, first - MESSAGE_PAGE_SIZE)
    const page = await listMessages(conversationOid, {
      afterSeq: start,
      limit: MESSAGE_PAGE_SIZE
    })
    if (page.messages.length === 0) {
      return
    }
    const known = new Set(messages.value.map((item) => item.oid))
    const older = page.messages.filter((item) => !known.has(item.oid))
    if (older.length === 0) {
      return
    }
    messages.value = [...older, ...messages.value]
  }

  /* --------------------------------------------------------------------- */
  /* 后台执行                                                               */
  /* --------------------------------------------------------------------- */

  /** 清掉当前会话上的 run 状态。 */
  function resetRun(): void {
    activeRun.value = null
    runEvents.value = []
    runProposals.value = []
    runAfterRound.value = 0
  }

  /** 提交一次后台执行；拿到受理结果后立刻开始轮询。 */
  async function submitRun(
    conversationOid: string,
    content: string,
    options: { model?: string; clientMsgId?: string } = {}
  ): Promise<void> {
    const result = await submitRunApi(conversationOid, content, options)
    resetRun()
    activeRun.value = {
      oid: result.run_id,
      conversation_oid: conversationOid,
      status: result.status,
      model: options.model ?? null,
      stop_reason: null,
      rounds: 0,
      error_code: null,
      error_message: null,
      result_message_oid: null,
      prompt_tokens: 0,
      completion_tokens: 0,
      total_tokens: 0,
      cancel_requested: false,
      created_at: '',
      started_at: null,
      finished_at: null
    }
    startPolling(result.run_id)
  }

  /** 中止当前 run。 */
  async function cancelCurrentRun(): Promise<void> {
    const run = activeRun.value
    if (!run) {
      return
    }
    await cancelRunApi(run.oid)
  }

  /** 问一次「有没有在跑的」，用于重连接管。 */
  async function loadActiveRuns(): Promise<AiRun[]> {
    try {
      const runs = await fetchActiveRunsApi()
      activeRuns.value = runs
      return runs
    } catch {
      activeRuns.value = []
      return []
    }
  }

  /** 接管一次进行中的 run：切到它的会话并开始轮询。 */
  async function adoptRun(run: AiRun): Promise<void> {
    if (run.conversation_oid) {
      // 会话可能不在当前列表里（归档过、或列表只拉了一页）：补一次详情。
      if (!conversations.value.some((item) => item.oid === run.conversation_oid)) {
        try {
          applyConversation(await getConversationApi(run.conversation_oid))
        } catch {
          // 取不到也不妨碍接管：进度照常跟。
        }
      }
      await selectConversation(run.conversation_oid)
    }
    resetRun()
    activeRun.value = run
    startPolling(run.oid)
  }

  /** 拉一次 run 详情，把增量并进来。 */
  async function refreshRun(runId: string): Promise<AiRunDetail | null> {
    try {
      const detail = await fetchRunDetail(runId, runAfterRound.value)
      applyRunDetail(detail)
      return detail
    } catch {
      return null
    }
  }

  /** 合并一次 run 的详情：事件与提案按去重键增量追加。 */
  function applyRunDetail(detail: AiRunDetail): void {
    activeRun.value = detail.run
    if (detail.events.length > 0) {
      const seen = new Set(runEvents.value.map(eventKey))
      const fresh = detail.events.filter((event) => !seen.has(eventKey(event)))
      runEvents.value = [...runEvents.value, ...fresh]
    }
    if (detail.proposals.length > 0) {
      const known = new Set(runProposals.value.map((item) => item.oid))
      const fresh = detail.proposals.filter((item) => !known.has(item.oid))
      runProposals.value = [...runProposals.value, ...fresh]
    }
    runAfterRound.value = Math.max(runAfterRound.value, detail.run.rounds)
  }

  /** 开始轮询一次 run（终态即停）。 */
  function startPolling(runId: string): void {
    stopPolling()
    pollingRunId = runId
    polling.value = true
    schedulePoll()
  }

  /** 停掉轮询。 */
  function stopPolling(): void {
    if (pollTimer) {
      clearTimeout(pollTimer)
      pollTimer = null
    }
    pollingRunId = ''
    polling.value = false
  }

  function schedulePoll(): void {
    if (!pollingRunId) {
      return
    }
    pollTimer = setTimeout(() => {
      pollTimer = null
      void tick()
    }, pollIntervalMs())
  }

  async function tick(): Promise<void> {
    const runId = pollingRunId
    if (!runId) {
      return
    }
    const detail = await refreshRun(runId)
    if (!detail) {
      // 拉取失败不放弃：下一拍再试；但没有 run 句柄就停。
      if (pollingRunId === runId) {
        schedulePoll()
      }
      return
    }
    if (isTerminalRun(detail.run.status)) {
      stopPolling()
      // 终态后 assistant 消息已落库：补一次增量消息。
      await refreshMessages()
      return
    }
    schedulePoll()
  }

  /** 清空当前会话的全部状态（切换会话、退出页面时用）。 */
  function clearCurrent(): void {
    stopPolling()
    currentOid.value = ''
    current.value = null
    messages.value = []
    totalMessages.value = 0
    loadedOid = ''
    resetRun()
  }

  return {
    // 会话列表
    conversations,
    nextCursor,
    listLoading,
    listFailed,
    // 当前会话与消息
    currentOid,
    current,
    messages,
    messagesLoading,
    messagesFailed,
    totalMessages,
    // run
    activeRun,
    runEvents,
    runProposals,
    runAfterRound,
    activeRuns,
    polling,
    // 派生
    orderedMessages,
    contextRatio,
    contextWarning,
    compactedCount,
    earlierCount,
    // 动作
    loadConversations,
    loadMoreConversations,
    createConversation,
    renameConversation,
    setConversationStatus,
    removeConversation,
    selectConversation,
    loadMessages,
    loadEarlierMessages,
    refreshMessages,
    submitRun,
    cancelCurrentRun,
    loadActiveRuns,
    adoptRun,
    refreshRun,
    startPolling,
    stopPolling,
    clearCurrent
  }
})
