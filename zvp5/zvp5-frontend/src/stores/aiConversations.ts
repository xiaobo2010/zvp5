import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import {
  cancelRun as cancelRunApi,
  compactConversation as compactConversationApi,
  createConversation as createConversationApi,
  deleteConversation as deleteConversationApi,
  fetchActiveRuns as fetchActiveRunsApi,
  fetchRunDetail,
  getConversation as getConversationApi,
  listConversations,
  listMessages,
  streamRunEvents,
  submitRun as submitRunApi,
  updateConversation
} from '@/api/ai'
import { AiRequestError } from '@/api/ai'
import type {
  AiConversationCompactResult,
  AiRunLiveAssistant,
  AiRunLiveState,
  AiRunLiveTool
} from '@/api/ai'
import { abortAllAiRuns, registerAiAbort } from '@/api/ai/activeRuns'
import type {
  AiConversation,
  AiConversationMode,
  AiConversationStatus,
  AiMessage,
  AiMessagePage,
  AiRun,
  AiRunDetail,
  AiRunStatus,
  AiRunStopReason,
  AiThinkingLevel
} from '@/../types/ai'

/**
 * AI 会话状态。
 *
 * 一次会话的三个活动对象都在这里：会话列表（游标分页）、当前会话的消息
 * （按 after_seq 增量）、以及当前会话里那一次后台执行（run）的进度。
 *
 * 轮询节奏：前台 2 秒一次，页面不可见时降到 5 秒（别在后台标签页里
 * 刷请求）。run 落到终态立刻停：没有活跃 run 就不该有常驻定时器。
 * 跑满 60 秒还没终态就指数退避（上限 5 秒）：长 run 仍然要能看到进展，
 * 但不值得每 2 秒打一次库。
 */

/** 前台轮询间隔（毫秒）。 */
export const POLL_INTERVAL_MS = 2_000

/** 提交之后的快探间隔（毫秒）：先抢首帧，别让人干等。 */
export const POLL_FAST_INTERVAL_MS = 750

/** 快探次数：连着快探这么多次之后回落到常规间隔。 */
export const POLL_FAST_TICKS = 3

/** 页面不可见时的轮询间隔（毫秒）。 */
export const POLL_INTERVAL_HIDDEN_MS = 5_000

/** 连续轮询多久之后开始退避（毫秒）。 */
export const POLL_BACKOFF_AFTER_MS = 60_000

/** 退避上限（毫秒）：再慢用户就看不出「它还在跑」了。 */
export const POLL_BACKOFF_MAX_MS = 5_000

/** run 的终态：落在这里就不再轮询。 */
const TERMINAL_RUN_STATUS = new Set(['succeeded', 'failed', 'cancelled', 'interrupted'])

/** 消息一页的条数：与服务端默认一致。 */
const MESSAGE_PAGE_SIZE = 50

/** 自动压缩阈值：上下文水位到这里就该压，界面同一阈值起警示。 */
export const AUTO_COMPACT_RATIO = 0.8

/** 轮询间隔：页面不可见时降频。 */
export function pollIntervalMs(): number {
  if (isDocumentHidden()) {
    return POLL_INTERVAL_HIDDEN_MS
  }
  return POLL_INTERVAL_MS
}

/** 页面是否在后台（标签页不可见）。 */
export function isDocumentHidden(): boolean {
  return typeof document !== 'undefined' && document.hidden
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

  /** 已归档会话：与活动列表分开存，切视图时才拉。 */
  const archivedConversations = ref<AiConversation[]>([])
  const archivedLoading = ref(false)
  const archivedFailed = ref(false)

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
  /** 最近一次提交 run 的失败原因（AiRequestError 或其它）；成功提交/切换会话时清空。 */
  const runError = ref<unknown>(null)

  /* --------------------------------------------------------------------- */
  /* 实时事件流（SSE）                                                      */
  /* --------------------------------------------------------------------- */

  /** 用户偏好：是否优先用 SSE 跟进 run。默认 false，由页面按本地设置打开。 */
  const preferStreaming = ref(false)
  /** 当前是否有 SSE 连接。 */
  const streaming = ref(false)
  /** 当前轮的上游累计文本（ephemeral，不落库）。 */
  const liveAssistantText = ref('')
  /** 累计文本所属轮次。 */
  const liveAssistantRound = ref(0)
  /** 当前 run 的 ephemeral 工具帧（按 tool_call_id 合并）。 */
  const liveTools = ref<AiRunLiveTool[]>([])
  /** 当前部署不支持 SSE（多 worker 503），已自动回退轮询。 */
  const streamFallback = ref(false)
  /** 流内错误码；网络级失败会走 runError / 轮询兜底。 */
  const streamError = ref<string | null>(null)

  let streamController: AbortController | null = null
  let streamRunId = ''

  let pollTimer: ReturnType<typeof setTimeout> | null = null
  let pollingRunId = ''
  /** 还剩几次快探：提交后先用 750ms 抢首帧，再回落到 2s。 */
  let fastPollBudget = 0
  /** 本次轮询开始的时刻：超过 60 秒仍未终态就退避。 */
  let pollStartedAt = 0
  /** 当前退避间隔（毫秒）；0 表示还没进入退避。 */
  let pollBackoffMs = 0
  /** 轮询期间登记到全局中止表里的注销函数：停轮询就摘掉。 */
  let unregisterPollAbort: (() => void) | null = null
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

  /** 水位达到 80% 就该警示：服务端在同一阈值上自动压缩。 */
  const contextWarning = computed(() => contextRatio.value >= AUTO_COMPACT_RATIO)

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

  /** 拉已归档会话；归档视图打开时才拉，不与活动列表混存。 */
  async function loadArchivedConversations(): Promise<void> {
    if (archivedLoading.value) {
      return
    }
    archivedLoading.value = true
    try {
      const page = await listConversations({ status: 'archived' })
      archivedConversations.value = page.conversations.filter((item) => item.mode === 'agent')
      archivedFailed.value = false
    } catch {
      archivedConversations.value = []
      archivedFailed.value = true
    } finally {
      archivedLoading.value = false
    }
  }

  /** 取消归档：回到活动列表，并从归档列表里摘掉。 */
  async function unarchiveConversation(conversationOid: string): Promise<AiConversation> {
    const updated = await setConversationStatus(conversationOid, 'active')
    archivedConversations.value = archivedConversations.value.filter(
      (item) => item.oid !== conversationOid
    )
    await loadConversations()
    return updated
  }

  /** 建一个会话并插到列表最前；模型在这里一次性定下，之后不可改。 */
  async function createConversation(
    mode: AiConversationMode,
    model?: string
  ): Promise<AiConversation> {
    const created = await createConversationApi(mode, undefined, model)
    conversations.value = [created, ...conversations.value.filter((item) => item.oid !== created.oid)]
    return created
  }

  /** 改思考强度；成功后同步列表与当前会话。 */
  async function setConversationThinking(
    conversationOid: string,
    thinking: AiThinkingLevel | null
  ): Promise<AiConversation> {
    const updated = await updateConversation(conversationOid, { thinking })
    applyConversation(updated)
    return updated
  }

  /**
   * 改模型：只对空会话有效（后端在有消息时回 409）。
   *
   * 先乐观改本地，失败再回滚——选模型是「点一下就该变」的操作，等一圈网络
   * 回来才刷新会让选择器看起来卡住。
   */
  async function setConversationModel(
    conversationOid: string,
    model: string
  ): Promise<AiConversation> {
    const target =
      currentOid.value === conversationOid
        ? current.value
        : conversations.value.find((item) => item.oid === conversationOid) ?? null
    const previous = target?.model ?? null
    if (target) {
      target.model = model
    }
    try {
      const updated = await updateConversation(conversationOid, { model })
      applyConversation(updated)
      return updated
    } catch (error) {
      if (target) {
        target.model = previous
      }
      throw error
    }
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
    archivedConversations.value = archivedConversations.value.filter(
      (item) => item.oid !== conversationOid
    )
    if (currentOid.value === conversationOid) {
      clearCurrent()
    }
  }

  /**
   * 手动压缩上下文。
   *
   * `compacted=true` 说明后端真的压了：水位与压缩点都由服务端重算，所以重拉
   * 一次详情，让进度条按后端口径回落，不用返回值手工拼 context。
   * `compacted=false` 是「没有可压的内容」，不是失败：不刷新，把原因交回组件。
   * 409（有活跃 run）与其它错误原样抛给调用方提示。
   */
  async function compactConversation(
    conversationOid: string
  ): Promise<AiConversationCompactResult> {
    const result = await compactConversationApi(conversationOid)
    if (result.compacted) {
      const detail = await getConversationApi(conversationOid)
      applyConversation(detail)
    }
    return result
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
    // 换会话时先停掉上一个的连接：run 的进度只属于它自己的会话。
    stopStreaming()
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

  /** 当前窗口里最大的 seq：增量口径的统一入口。 */
  function lastMessageSeq(): number {
    return messages.value.reduce((max, item) => Math.max(max, item.seq), 0)
  }

  /** 把一批消息并进来：按 seq 去重，保持升序。 */
  function appendMessages(incoming: AiMessage[]): void {
    if (incoming.length === 0) {
      return
    }
    const known = new Set(messages.value.map((item) => item.seq))
    const fresh = incoming.filter((item) => !known.has(item.seq))
    if (fresh.length === 0) {
      return
    }
    messages.value = [...messages.value, ...fresh].sort((a, b) => a.seq - b.seq)
    totalMessages.value += fresh.length
  }

  /** 增量补最新消息（run 落库后用）。 */
  async function refreshMessages(): Promise<void> {
    const conversationOid = currentOid.value
    if (!conversationOid) {
      return
    }
    await loadMessages(conversationOid, { afterSeq: lastMessageSeq() })
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

  /** 清掉当前会话上的 run 状态（消息与已落库内容不动）。 */
  function resetRun(): void {
    activeRun.value = null
    runEvents.value = []
    runProposals.value = []
    runAfterRound.value = 0
    runError.value = null
    liveAssistantText.value = ''
    liveAssistantRound.value = 0
    liveTools.value = []
    streamError.value = null
  }

  /** 页面自己决定要不要优先走 SSE；不改已有连接（切换只影响下一次 run）。 */
  function setPreferStreaming(value: boolean): void {
    preferStreaming.value = Boolean(value)
    if (!preferStreaming.value) {
      // 关掉流式后「当前部署不支持」的提示就不再适用，别让旧提示挂着。
      streamFallback.value = false
    }
  }

  /** 用服务端 snapshot 里的 ephemeral 状态覆盖本地视图。 */
  function applyLiveState(live: Partial<AiRunLiveState> | null | undefined): void {
    if (!live) {
      return
    }
    const assistant = Array.isArray(live.assistant) ? live.assistant : []
    let latest: AiRunLiveAssistant | null = null
    for (const item of assistant) {
      if (!latest || item.seq >= latest.seq) {
        latest = item
      }
    }
    liveAssistantText.value = latest?.content ?? ''
    liveAssistantRound.value = latest?.round_no ?? 0
    liveTools.value = Array.isArray(live.tools) ? [...live.tools] : []
  }

  function applyAssistantFrame(roundNo: number, content: string): void {
    if (!content) {
      return
    }
    liveAssistantRound.value = roundNo
    liveAssistantText.value = content
  }

  function applyToolFrame(frame: AiRunLiveTool): void {
    const index = liveTools.value.findIndex((item) =>
      frame.tool_call_id
        ? item.tool_call_id === frame.tool_call_id
        : item.round_no === frame.round_no && item.tool_name === frame.tool_name
    )
    if (index >= 0) {
      const next = [...liveTools.value]
      next[index] = { ...next[index]!, ...frame }
      liveTools.value = next
      return
    }
    liveTools.value = [...liveTools.value, frame].slice(-24)
  }

  /**
   * 停掉 SSE。
   *
   * `clearLive=false` 留给「刚收到终态」的路径：最终消息还在补拉，先把连接收掉，
   * 但不要把最后一帧文本闪掉；消息落库后由 reset/下一次 run 清。
   */
  function stopStreaming(clearLive = true): void {
    streamController?.abort()
    streamController = null
    streamRunId = ''
    streaming.value = false
    if (clearLive) {
      liveAssistantText.value = ''
      liveAssistantRound.value = 0
      liveTools.value = []
    }
  }

  function isAbortError(error: unknown): boolean {
    return error instanceof DOMException
      ? error.name === 'AbortError'
      : Boolean((error as { name?: string } | null)?.name === 'AbortError')
  }

  /** SSE 不可用时静默回退轮询：用户仍能看到进度，只是退化为 2 秒一拍。 */
  function fallbackToPolling(runId: string): void {
    stopStreaming()
    if (activeRun.value?.oid === runId && !isTerminalRun(activeRun.value.status)) {
      startPolling(runId)
      void refreshMessages()
    }
  }

  /**
   * 开一条 run 事件流。
   *
   * 与轮询二选一：连接建立前先把轮询停掉，避免同一轮同时打两条链路。
   * 首帧 snapshot 会补齐 run 状态、事件、提案；assistant 帧只更新 ephemeral
   * 文本，等轮提交后的 snapshot 再以落库消息为准。
   */
  function startStreaming(runId: string): void {
    stopStreaming()
    stopPolling()
    streamRunId = runId
    streamController = new AbortController()
    streaming.value = true
    streamFallback.value = false
    streamError.value = null
    const controller = streamController

    void (async () => {
      try {
        const result = await streamRunEvents(
          runId,
          {
            onSnapshot: (frame) => {
              if (streamRunId !== runId) {
                return
              }
              applyRunDetail(frame.data)
              if (frame.live) {
                applyLiveState(frame.live)
              }
              // snapshot 可能带新一轮的落库消息：增量补拉，避免与流内 ephemeral 文本重复。
              void refreshMessages()
            },
            onAssistant: (frame) => {
              if (streamRunId !== runId) {
                return
              }
              applyAssistantFrame(frame.round_no, frame.content)
            },
            onTool: (frame) => {
              if (streamRunId !== runId) {
                return
              }
              applyToolFrame(frame)
            },
            onTerminal: (frame) => {
              if (streamRunId !== runId || !activeRun.value) {
                return
              }
              activeRun.value = {
                ...activeRun.value,
                status: frame.status as AiRunStatus,
                stop_reason: frame.stop_reason as AiRunStopReason | null,
                error_code: frame.error_code
              }
            },
            onError: (frame) => {
              streamError.value = frame.code
            }
          },
          { signal: controller.signal }
        )

        if (streamRunId !== runId) {
          return
        }
        if (result.completed && !result.error) {
          stopStreaming(false)
          // 终态后 assistant 消息已落库：补一次增量做兜底，再收掉 ephemeral 文本。
          await refreshMessages()
          liveAssistantText.value = ''
          liveAssistantRound.value = 0
          liveTools.value = []
          return
        }
        fallbackToPolling(runId)
      } catch (error) {
        if (streamRunId !== runId) {
          return
        }
        if (isAbortError(error) || controller.signal.aborted) {
          return
        }
        if (error instanceof AiRequestError && error.code === 'AI_RUN_EVENTS_UNSUPPORTED') {
          streamFallback.value = true
        } else if (error instanceof AiRequestError) {
          streamError.value = error.code
        }
        fallbackToPolling(runId)
      }
    })()
  }

  /**
   * 中止当前全部 AI 活动：停轮询、中止在途流式连接、复位 run 状态。
   *
   * 用于「整条链路已经失效」的场景（封禁 403）：只把界面上的执行痕迹收干净，
   * **不动** 已落库的消息——那些是服务端的既成事实，重进页面还要看。
   */
  function abortAllRuns(reason = 'aborted'): void {
    // 先停连接再复位：避免下一拍/下一帧把刚清掉的 activeRun 又填回来。
    stopStreaming()
    stopPolling()
    resetRun()
    abortAllAiRuns(reason)
  }

  /** 提交一次后台执行；拿到受理结果后按偏好开 SSE 或轮询。 */
  async function submitRun(
    conversationOid: string,
    content: string,
    options: { clientMsgId?: string; streaming?: boolean } = {}
  ): Promise<void> {
    try {
      const result = await submitRunApi(conversationOid, content, options)
      resetRun()
      activeRun.value = {
        oid: result.run_id,
        conversation_oid: conversationOid,
        status: result.status,
        model: current.value?.model ?? null,
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
      if (options.streaming ?? preferStreaming.value) {
        startStreaming(result.run_id)
      } else {
        startPolling(result.run_id)
      }
    } catch (error) {
      // 失败必须收敛：留着上一轮的 activeRun 与连接，界面会一直显示「后台执行中」，
      // 而且没有任何一条路径会再去停它。错误本身交给调用方呈现（原样抛出）。
      stopStreaming()
      stopPolling()
      resetRun()
      runError.value = error
      throw error
    }
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

  /** 接管一次进行中的 run：切到它的会话并按偏好开 SSE 或轮询。 */
  async function adoptRun(run: AiRun, options: { streaming?: boolean } = {}): Promise<void> {
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
    if (options.streaming ?? preferStreaming.value) {
      startStreaming(run.oid)
    } else {
      startPolling(run.oid)
    }
  }

  /** 拉一次 run 详情，把增量并进来（含运行中刚落的会话消息）。 */
  async function refreshRun(runId: string): Promise<AiRunDetail | null> {
    try {
      const detail = await fetchRunDetail(runId, runAfterRound.value, lastMessageSeq())
      applyRunDetail(detail)
      return detail
    } catch {
      return null
    }
  }

  /** 合并一次 run 的详情：消息、事件与提案都按各自的键增量追加。 */
  function applyRunDetail(detail: AiRunDetail): void {
    activeRun.value = detail.run
    appendMessages(detail.messages ?? [])
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

  /** 开始轮询一次 run（终态即停）。SSE 与轮询二选一。 */
  function startPolling(runId: string): void {
    stopStreaming()
    stopPolling()
    pollingRunId = runId
    polling.value = true
    // 提交后的头几拍用快节奏：首帧早到 0.75 秒，人不用盯着空消息区干等。
    fastPollBudget = POLL_FAST_TICKS
    pollStartedAt = Date.now()
    pollBackoffMs = 0
    // 轮询也要能被全局中止（封禁后继续轮询只是把注定失败的请求刷一遍）。
    unregisterPollAbort = registerAiAbort(() => {
      stopPolling()
      resetRun()
    })
    schedulePoll()
  }

  /** 停掉轮询。 */
  function stopPolling(): void {
    if (pollTimer) {
      clearTimeout(pollTimer)
      pollTimer = null
    }
    if (unregisterPollAbort) {
      unregisterPollAbort()
      unregisterPollAbort = null
    }
    pollingRunId = ''
    polling.value = false
  }

  function schedulePoll(): void {
    if (!pollingRunId) {
      return
    }
    // 页面在后台时不做快探：直接走隐藏档（5 秒）。
    const fast = fastPollBudget > 0 && !isDocumentHidden()
    if (fast) {
      fastPollBudget -= 1
    }
    const base = fast ? POLL_FAST_INTERVAL_MS : pollIntervalMs()
    pollTimer = setTimeout(() => {
      pollTimer = null
      void tick()
    }, nextPollDelay(base))
  }

  /**
   * 下一拍的间隔：前 60 秒按基础节奏，之后每次翻倍（上限 5 秒）。
   *
   * 长 run（10 轮工具调用可以跑几分钟）不该一直按 2 秒打一次；但也不能慢到
   * 用户以为页面死了。退避只影响节奏，不影响增量协议：每拍照样带
   * after_round / since_msg_seq。
   */
  function nextPollDelay(base: number): number {
    if (Date.now() - pollStartedAt < POLL_BACKOFF_AFTER_MS) {
      pollBackoffMs = 0
      return base
    }
    pollBackoffMs = pollBackoffMs > 0 ? pollBackoffMs * 2 : base
    return Math.min(pollBackoffMs, POLL_BACKOFF_MAX_MS)
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
      // 终态后 assistant 消息已落库：再补一次增量做兜底，接着不用轮询了。
      await refreshMessages()
      return
    }
    schedulePoll()
  }

  /** 清空当前会话的全部状态（切换会话、退出页面时用）。 */
  function clearCurrent(): void {
    stopStreaming()
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
    archivedConversations,
    archivedLoading,
    archivedFailed,
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
    runError,
    // 实时事件流
    preferStreaming,
    streaming,
    liveAssistantText,
    liveAssistantRound,
    liveTools,
    streamFallback,
    streamError,
    // 派生
    orderedMessages,
    contextRatio,
    contextWarning,
    compactedCount,
    earlierCount,
    // 动作
    loadConversations,
    loadMoreConversations,
    loadArchivedConversations,
    unarchiveConversation,
    createConversation,
    renameConversation,
    setConversationStatus,
    setConversationThinking,
    setConversationModel,
    removeConversation,
    compactConversation,
    selectConversation,
    loadMessages,
    loadEarlierMessages,
    refreshMessages,
    submitRun,
    cancelCurrentRun,
    abortAllRuns,
    loadActiveRuns,
    adoptRun,
    refreshRun,
    setPreferStreaming,
    startStreaming,
    stopStreaming,
    startPolling,
    stopPolling,
    clearCurrent
  }
})
