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
import type { AiConversationCompactResult, AiRunEventFrame, AiRunEventsOutcome } from '@/api/ai'
import { abortAllAiRuns, registerAiAbort } from '@/api/ai/activeRuns'
import type {
  AiConversation,
  AiConversationMode,
  AiConversationStatus,
  AiMessage,
  AiMessagePage,
  AiRun,
  AiRunAssistantLive,
  AiRunDetail,
  AiRunStreamStatus,
  AiRunToolLive,
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

/* ---------------------------------------------------------------------------
   SSE（GET /ai/runs/{run_id}/events）
   --------------------------------------------------------------------------- */

/** 断线后第一次重连的退避（毫秒）。 */
export const STREAM_RETRY_BASE_MS = 2_000

/** 退避上限（毫秒）：再慢用户就看不出「它还在跟着」。 */
export const STREAM_RETRY_MAX_MS = 30_000

/** 连续失败这么多次就判定 SSE 不可用：轮询为主，后台继续慢慢重试。 */
export const STREAM_MAX_FAILURES = 3

/** 心跳 15s 一次；超过这么久没有任何字节就当连接死了（隐藏标签回来时判活用）。 */
export const STREAM_STALE_MS = 40_000

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
  /* SSE 实时流                                                             */
  /* --------------------------------------------------------------------- */

  /** 每个 run 的连接状态（designer 只读消费）。 */
  const streamStatuses = ref<Record<string, AiRunStreamStatus>>({})
  /** 每个 run 的累计 assistant 文本（ephemeral，不落库）。 */
  const streamAssistants = ref<Record<string, AiRunAssistantLive[]>>({})
  /** 每个 run 的工具调用进度（ephemeral，不落库）。 */
  const streamTools = ref<Record<string, AiRunToolLive[]>>({})

  /** run → 已应用的最大帧 seq（同一连接内单调，用于去重）。 */
  const streamSeqs = new Map<string, number>()
  /** run → stream_key → 数组下标（避免每帧线性找）。 */
  const assistantSlots = new Map<string, Map<string, number>>()
  /** run → tool_call_id → 数组下标（同一调用原地更新状态）。 */
  const toolSlots = new Map<string, Map<string, number>>()

  /** 一条在途连接：controller 与全局中止表的注销函数成对保存。 */
  interface StreamConnection {
    controller: AbortController
    release: () => void
  }

  /** 正在订阅的 run。 */
  let streamRunId = ''
  /** 当前这条连接（旧连接必须显式 abort，generation 只负责让回调失效）。 */
  let streamConnection: StreamConnection | null = null
  /** 连接代际：换 run / 重连时旧连接的回调不再生效。 */
  let streamGeneration = 0
  /** 连续失败次数（成功收到 snapshot 后清零）。 */
  let streamFailures = 0
  /** 本次连接是否收到过流内 error 帧（收到就不把「正常收流」当终态）。 */
  let streamErrored = false
  /** 下一次重连的定时器。 */
  let streamRetryTimer: ReturnType<typeof setTimeout> | null = null
  /** 最近一次收到字节的时刻（含心跳）。 */
  let streamLastActivityAt = 0
  /** 页面可见性监听是否已挂上（只挂一次，避免重复注册）。 */
  let visibilityBound = false

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
    stopRunStream()
    const runId = activeRun.value?.oid
    if (runId) {
      forgetLive(runId)
      removeStreamStatus(runId)
    }
    activeRun.value = null
    runEvents.value = []
    runProposals.value = []
    runAfterRound.value = 0
    runError.value = null
  }

  /**
   * 中止当前全部 AI 活动：停轮询、中止在途流式连接、复位 run 状态。
   *
   * 用于「整条链路已经失效」的场景（封禁 403）：只把界面上的执行痕迹收干净，
   * **不动** 已落库的消息——那些是服务端的既成事实，重进页面还要看。
   */
  function abortAllRuns(reason = 'aborted'): void {
    // 先停轮询再复位：避免下一拍 tick 把刚清掉的 activeRun 又填回来。
    stopPolling()
    resetRun()
    abortAllAiRuns(reason)
  }

  /** 提交一次后台执行；拿到受理结果后立刻开始轮询。 */
  async function submitRun(
    conversationOid: string,
    content: string,
    options: { clientMsgId?: string } = {}
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
      // 提交成功后自动尝试实时流：SSE 不可用会立刻回落到同一套轮询。
      startRunStream(result.run_id)
    } catch (error) {
      // 失败必须收敛：留着上一轮的 activeRun 与轮询，界面会一直显示「后台执行中」，
      // 而且没有任何一条路径会再去停它。错误本身交给调用方呈现（原样抛出）。
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
    // 接管在途 run：同提交路径，先试 SSE，失败自动轮询。
    startRunStream(run.oid)
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
    const events = detail.events ?? []
    if (events.length > 0) {
      const seen = new Set(runEvents.value.map(eventKey))
      const fresh = events.filter((event) => !seen.has(eventKey(event)))
      runEvents.value = [...runEvents.value, ...fresh]
    }
    const proposals = detail.proposals ?? []
    if (proposals.length > 0) {
      const known = new Set(runProposals.value.map((item) => item.oid))
      const fresh = proposals.filter((item) => !known.has(item.oid))
      runProposals.value = [...runProposals.value, ...fresh]
    }
    runAfterRound.value = Math.max(runAfterRound.value, detail.run.rounds)
  }

  /* --------------------------------------------------------------------- */
  /* SSE 实时流                                                             */
  /*                                                                    */
  /* 三条只读接口给 designer：streamStatus / liveAssistantFor / liveToolsFor。 */
  /* 其余函数都是本 store 内部推进连接用的。                                  */
  /* --------------------------------------------------------------------- */

  /** 同一轮按 seq 升序；不同轮按轮次升序。 */
  function byRoundSeq(
    a: { round_no: number; seq: number },
    b: { round_no: number; seq: number }
  ): number {
    return a.round_no - b.round_no || a.seq - b.seq
  }

  /** 工具帧的归并键：优先用后端给的调用 id。 */
  function toolKey(frame: AiRunToolLive): string {
    return frame.tool_call_id || `${frame.round_no}|${frame.tool_name ?? ''}`
  }

  /** 一次 run 的实时流状态；没订阅过就是 idle。 */
  function streamStatus(runId: string): AiRunStreamStatus {
    return streamStatuses.value[runId] ?? 'idle'
  }

  /** 该 run 的 assistant 累计文本（按 round/seq 升序）；只读。 */
  function liveAssistantFor(runId: string): AiRunAssistantLive[] {
    const list = streamAssistants.value[runId]
    return list ? [...list].sort(byRoundSeq) : []
  }

  /** 该 run 的工具调用进度（按 round/seq 升序）；只读。 */
  function liveToolsFor(runId: string): AiRunToolLive[] {
    const list = streamTools.value[runId]
    return list ? [...list].sort(byRoundSeq) : []
  }

  function setStreamStatus(runId: string, status: AiRunStreamStatus): void {
    if (streamStatus(runId) === status) {
      return
    }
    streamStatuses.value = { ...streamStatuses.value, [runId]: status }
  }

  function removeStreamStatus(runId: string): void {
    if (!(runId in streamStatuses.value)) {
      return
    }
    const next = { ...streamStatuses.value }
    delete next[runId]
    streamStatuses.value = next
  }

  /** 丢掉一个 run 的 ephemeral 展示态（不碰消息与事件留痕）。 */
  function forgetLive(runId: string): void {
    streamSeqs.delete(runId)
    assistantSlots.delete(runId)
    toolSlots.delete(runId)
    if (runId in streamAssistants.value) {
      const assistants = { ...streamAssistants.value }
      delete assistants[runId]
      streamAssistants.value = assistants
    }
    if (runId in streamTools.value) {
      const tools = { ...streamTools.value }
      delete tools[runId]
      streamTools.value = tools
    }
  }

  /**
   * 用 snapshot.live 整体替换该 run 的 ephemeral 状态。
   *
   * 快照是「此刻服务端认为的 live」，所以是替换而不是合并：轮提交后旧轮的
   * 累计文本会因此消失（它已经落库成消息了），不会和持久消息重复。
   */
  function replaceLive(
    runId: string,
    live: { assistant: AiRunAssistantLive[]; tools: AiRunToolLive[] }
  ): void {
    const assistant = [...(live.assistant ?? [])].sort(byRoundSeq)
    const tools = [...(live.tools ?? [])].sort(byRoundSeq)
    streamAssistants.value = { ...streamAssistants.value, [runId]: assistant }
    streamTools.value = { ...streamTools.value, [runId]: tools }
    assistantSlots.set(
      runId,
      new Map(assistant.map((frame, index) => [frame.stream_key, index]))
    )
    toolSlots.set(runId, new Map(tools.map((frame, index) => [toolKey(frame), index])))
  }

  /** assistant 帧是累计文本：按 stream_key 原地替换整段。 */
  function upsertAssistant(runId: string, frame: AiRunAssistantLive): void {
    const current = streamAssistants.value[runId] ?? []
    const slots = assistantSlots.get(runId) ?? new Map<string, number>()
    const index = slots.get(frame.stream_key)
    let next: AiRunAssistantLive[]
    if (index === undefined) {
      next = [...current, frame].sort(byRoundSeq)
      assistantSlots.set(
        runId,
        new Map(next.map((item, position) => [item.stream_key, position]))
      )
    } else {
      next = current.slice()
      next[index] = frame
    }
    streamAssistants.value = { ...streamAssistants.value, [runId]: next }
  }

  /** tool 帧按 call id 原地更新：pending → success / error。 */
  function upsertTool(runId: string, frame: AiRunToolLive): void {
    const current = streamTools.value[runId] ?? []
    const slots = toolSlots.get(runId) ?? new Map<string, number>()
    const key = toolKey(frame)
    const index = slots.get(key)
    let next: AiRunToolLive[]
    if (index === undefined) {
      next = [...current, frame].sort(byRoundSeq)
      toolSlots.set(runId, new Map(next.map((item, position) => [toolKey(item), position])))
    } else {
      next = current.slice()
      const previous = current[index]!
      // 终态帧可能不带 args_summary / result_summary：缺字段时保留旧值。
      next[index] = {
        ...previous,
        ...frame,
        args_summary: frame.args_summary ?? previous.args_summary,
        result_summary: frame.result_summary ?? previous.result_summary
      }
    }
    streamTools.value = { ...streamTools.value, [runId]: next }
  }

  /**
   * 应用一帧 SSE。
   *
   * seq 去重是每连接单调的：重连时服务端可能已经重置序号（进程重启），而
   * 首帧 snapshot 本身是全量事实，所以新连接从 0 重新计。重复的 assistant
   * 帧只是同一段累计文本的替换，tool 帧按 call id 覆盖，都不会重复渲染。
   */
  function applyStreamFrame(runId: string, frame: AiRunEventFrame): void {
    if (!Number.isFinite(frame.seq) || frame.seq <= 0) {
      return
    }
    const last = streamSeqs.get(runId) ?? 0
    if (frame.seq <= last) {
      return
    }
    streamSeqs.set(runId, frame.seq)

    switch (frame.type) {
      case 'snapshot': {
        setStreamStatus(runId, 'live')
        streamFailures = 0
        // snapshot.data 与 GET /runs/{id} 同形：走同一条合并路径，按 oid/seq 去重。
        applyRunDetail(frame.data)
        // live 是独立的 ephemeral 源：整体替换，轮提交后旧文本自动消失。
        replaceLive(runId, frame.live)
        // SSE 健康时轮询只是重复劳动：停掉（首帧已经是最新事实）。
        stopPollTimers()
        if (isTerminalRun(frame.data?.run?.status)) {
          finishStream(runId)
          setStreamStatus(runId, 'closed')
        }
        break
      }
      case 'assistant':
        upsertAssistant(runId, frame)
        break
      case 'tool':
        upsertTool(runId, frame)
        break
      case 'resync':
        // 服务端随后会补一帧新 snapshot（它自己读库），这里不必再加一次请求。
        break
      case 'terminal':
        finishStream(runId)
        setStreamStatus(runId, 'closed')
        break
      case 'error':
        // 流内错误：这条连接不会再给内容，转轮询并择机重连。
        streamErrored = true
        handleStreamLoss(runId)
        break
    }
  }

  /** 终态收尾：停轮询、断流、清 ephemeral、补一次消息增量兜底。 */
  function finishStream(runId: string): void {
    stopPollTimers()
    stopRunStream(runId, { keepStatus: true })
    forgetLive(runId)
    // assistant 最终消息已经落库：补一次增量，别让最后一条靠 ephemeral 撑着。
    void refreshMessages()
  }

  /** 连接断了但没有终态：保证轮询在跑，然后按退避重连。 */
  function handleStreamLoss(runId: string): void {
    if (streamStatus(runId) === 'closed') {
      return
    }
    streamFailures += 1
    if (streamFailures >= STREAM_MAX_FAILURES) {
      setStreamStatus(runId, 'fallback')
    }
    ensurePolling(runId)
    scheduleStreamRetry(runId)
  }

  /** 轮询兜底：已经在轮询同一个 run 就不重启动（免得重置快探预算）。 */
  function ensurePolling(runId: string): void {
    if (polling.value && pollingRunId === runId) {
      return
    }
    startPolling(runId)
  }

  /** 指数退避：2s → 4s → 8s …（上限 30s）。 */
  function retryDelay(failures: number): number {
    const step = Math.min(Math.max(1, Math.trunc(failures)), 6)
    return Math.min(STREAM_RETRY_BASE_MS * 2 ** (step - 1), STREAM_RETRY_MAX_MS)
  }

  function clearStreamTimer(): void {
    if (streamRetryTimer) {
      clearTimeout(streamRetryTimer)
      streamRetryTimer = null
    }
  }

  function scheduleStreamRetry(runId: string): void {
    clearStreamTimer()
    if (streamRunId !== runId) {
      return
    }
    // 后台标签里不重试：轮询已经降到 5 秒，回前台再重建（省一条长连接）。
    if (isDocumentHidden()) {
      return
    }
    streamRetryTimer = setTimeout(() => {
      streamRetryTimer = null
      if (streamRunId !== runId) {
        return
      }
      if (isTerminalRun(activeRun.value?.status ?? null)) {
        return
      }
      void attemptStream(runId)
    }, streamStatus(runId) === 'fallback' ? STREAM_RETRY_MAX_MS : retryDelay(streamFailures))
  }

  /**
   * 收掉当前连接：abort 掉 socket，并从全局中止表摘掉回调。
   *
   * 必须在建新连接之前调用。generation 只能让旧回调失效，旧请求本身
   * （网络 + 服务端订阅）还活着，不能靠「忽略它的结果」来收场。
   */
  function abortStreamConnection(): void {
    const connection = streamConnection
    if (!connection) {
      return
    }
    streamConnection = null
    connection.controller.abort()
    connection.release()
  }

  /** 建连一次；结局统一交给 handleStreamOutcome。 */
  async function attemptStream(runId: string): Promise<void> {
    if (streamRunId !== runId) {
      return
    }
    clearStreamTimer()
    // 回前台判活 / error 帧重试都可能在旧连接还开着时走到这里：先显式收掉它。
    abortStreamConnection()
    const generation = ++streamGeneration
    const controller = new AbortController()
    const connection: StreamConnection = {
      controller,
      release: registerAiAbort(() => controller.abort())
    }
    streamConnection = connection
    streamErrored = false
    // 新连接重新计 seq：首帧 snapshot 是全量事实，旧序号不该挡住新帧。
    streamSeqs.set(runId, 0)
    streamLastActivityAt = Date.now()
    // fallback 期间保持 fallback：重试不该把界面闪回「连接中」。
    if (streamStatus(runId) !== 'fallback') {
      setStreamStatus(runId, 'connecting')
    }

    let outcome: AiRunEventsOutcome
    try {
      outcome = await streamRunEvents(runId, {
        signal: controller.signal,
        onFrame: (frame) => {
          if (generation !== streamGeneration || streamRunId !== runId) {
            return
          }
          applyStreamFrame(runId, frame)
        },
        onActivity: () => {
          if (generation === streamGeneration) {
            streamLastActivityAt = Date.now()
          }
        }
      })
    } catch (error) {
      // 登录失效是统一出口（已跳登录页 + 中止在途请求）：别再拿长连接刷请求。
      if ((error as { status?: number } | null)?.status === 401) {
        stopRunStream(runId)
        return
      }
      // 其余异常按连接失败处理：轮询顶上，稍后重连。
      outcome = {
        done: false,
        aborted: false,
        unsupported: false,
        started: false,
        error: null,
        status: null
      }
    } finally {
      // 只有「我还是当前连接」时才清共享引用：旧连接的收尾不能把新连接摘掉。
      if (streamConnection === connection) {
        streamConnection = null
        connection.release()
      }
    }

    if (generation !== streamGeneration || streamRunId !== runId) {
      return
    }
    handleStreamOutcome(runId, outcome)
  }

  function handleStreamOutcome(runId: string, outcome: AiRunEventsOutcome): void {
    if (streamStatus(runId) === 'closed') {
      return
    }
    if (outcome.aborted) {
      // 主动收尾（切会话 / 终态 / 全局中止）：调用方已经处理状态。
      return
    }
    if (outcome.status === 401 || outcome.error?.status === 401) {
      // 登录已失效：统一出口已经跳登录页，别拿长连接反复刷。
      stopRunStream(runId)
      stopPollTimers()
      return
    }
    if (outcome.done && !streamErrored) {
      // 服务端正常收流：即便没读到 terminal 也说明这次 run 到此为止。
      finishStream(runId)
      setStreamStatus(runId, 'closed')
      return
    }
    if (outcome.unsupported) {
      // 多 worker / 老后端：直接以轮询为主，但保留慢速重试（发布单 worker 后自动回来）。
      streamFailures = Math.max(streamFailures, STREAM_MAX_FAILURES)
      setStreamStatus(runId, 'fallback')
      ensurePolling(runId)
      scheduleStreamRetry(runId)
      return
    }
    handleStreamLoss(runId)
  }

  /**
   * 为一次 run 建立实时订阅。
   *
   * 轮询先跑起来当兜底：SSE 首帧一到就停（两条路都走 applyRunDetail，
   * 按 oid / seq 去重，不会重复追加消息）。
   */
  function startRunStream(runId: string): void {
    stopRunStream()
    streamRunId = runId
    streamFailures = 0
    streamErrored = false
    streamSeqs.delete(runId)
    setStreamStatus(runId, 'connecting')
    bindVisibility()
    startPolling(runId)
    void attemptStream(runId)
  }

  /** 停掉实时订阅；keepStatus=true 时保留状态（终态要显示 closed）。 */
  function stopRunStream(
    runId?: string,
    options: { keepStatus?: boolean } = {}
  ): void {
    const current = streamRunId
    if (runId && current && runId !== current) {
      return
    }
    streamRunId = ''
    streamGeneration += 1
    clearStreamTimer()
    abortStreamConnection()
    unbindVisibility()
    if (!current) {
      return
    }
    if (!options.keepStatus) {
      forgetLive(current)
      removeStreamStatus(current)
    }
  }

  function bindVisibility(): void {
    if (visibilityBound || typeof document === 'undefined') {
      return
    }
    document.addEventListener('visibilitychange', onVisibilityChange)
    visibilityBound = true
  }

  function unbindVisibility(): void {
    if (!visibilityBound || typeof document === 'undefined') {
      return
    }
    document.removeEventListener('visibilitychange', onVisibilityChange)
    visibilityBound = false
  }

  /**
   * 回到前台时判活：隐藏期间浏览器可能把连接掐了，而我们在后台不重试。
   *
   * 判据是「最近有没有收到字节」（15s 一次的心跳也算），不是「有没有业务帧」——
   * 一个长工具调用可能几十秒没有新内容，但连接是好的。
   */
  function onVisibilityChange(): void {
    const runId = streamRunId
    if (!runId || isDocumentHidden()) {
      return
    }
    const status = streamStatus(runId)
    if (status === 'closed') {
      return
    }
    const stale = Date.now() - streamLastActivityAt > STREAM_STALE_MS
    if (status === 'live' && !stale) {
      return
    }
    // 连接可能已死：先拉起轮询兜底，再重建 SSE。
    ensurePolling(runId)
    clearStreamTimer()
    void attemptStream(runId)
  }

  /** 开始轮询一次 run（终态即停）。 */
  function startPolling(runId: string): void {
    stopPollTimers()
    pollingRunId = runId
    polling.value = true
    // 提交后的头几拍用快节奏：首帧早到 0.75 秒，人不用盯着空消息区干等。
    fastPollBudget = POLL_FAST_TICKS
    pollStartedAt = Date.now()
    pollBackoffMs = 0
    // 轮询也要能被全局中止（封禁后继续轮询只是把注定失败的请求刷一遍）。
    unregisterPollAbort = registerAiAbort(() => {
      stopPollTimers()
      resetRun()
    })
    schedulePoll()
  }

  /**
   * 停掉对当前 run 的跟随：轮询与实时流一起收。
   *
   * 组件卸载只需要调这一个（历史调用点就是 `store.stopPolling()`），
   * 否则长连接会跟着页面一起泄漏。
   */
  function stopPolling(): void {
    stopPollTimers()
    stopRunStream()
  }

  /** 只停轮询定时器，不动实时流（SSE 内部切到 live 时用）。 */
  function stopPollTimers(): void {
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
    // SSE 已经在推同一份事实：不要再排轮询，否则就是重复请求。
    if (streamStatus(pollingRunId) === 'live') {
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
    // 上一拍发出后 SSE 首帧到了：这一拍作废，别把已停的轮询再排起来。
    if (streamStatus(runId) === 'live') {
      stopPollTimers()
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
      stopPollTimers()
      if (streamRunId === runId) {
        // 终态由轮询先看到：把实时订阅一起关掉，别留着一条不会再有内容的连接。
        stopRunStream(runId, { keepStatus: true })
        forgetLive(runId)
        setStreamStatus(runId, 'closed')
      }
      // 终态后 assistant 消息已落库：再补一次增量做兜底，接着不用轮询了。
      await refreshMessages()
      return
    }
    // 请求在飞时 SSE 首帧可能刚到：那就别再排下一拍。
    if (streamStatus(runId) === 'live') {
      stopPollTimers()
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
    // 实时流（designer 只读消费这三个；其余是内部动作）
    streamStatus,
    liveAssistantFor,
    liveToolsFor,
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
    startPolling,
    stopPolling,
    startRunStream,
    stopRunStream,
    clearCurrent
  }
})
