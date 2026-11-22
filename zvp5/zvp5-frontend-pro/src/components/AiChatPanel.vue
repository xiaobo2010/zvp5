<script setup lang="ts">
import AiChatUnlockDialog from '@/components/AiChatUnlockDialog.vue'
import BubbleFeedback from '@/components/BubbleFeedback.vue'
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue'
import {
  cancelRun,
  createConversation,
  fetchActiveRuns,
  fetchRunDetail,
  getConversation,
  listConversations,
  listMessages,
  listModels,
  submitRun,
  type AiConversation,
  type AiMessage,
  type AiModel,
  type AiRun,
  type AiRunDetail,
  type AiRunSubmitResult
} from '@/api/ai-chat'
import { ApiError } from '@/api/http'

/* -------------------------------------------------------------------------- */
/* Constants                                                                  */
/* -------------------------------------------------------------------------- */

/** Server caps one message page at 100. */
const HISTORY_PAGE_SIZE = 100

/** Polling: first few ticks are fast, then settle; hidden tabs back off. */
const POLL_FAST_MS = 750
const POLL_INTERVAL_MS = 2_000
const POLL_HIDDEN_MS = 5_000
const POLL_FAST_TICKS = 3
const POLL_MAX_FAILURES = 5

const ACTIVE_RUN_STATUSES = new Set<AiRun['status']>(['queued', 'running'])
const TERMINAL_RUN_STATUSES = new Set<AiRun['status']>([
  'succeeded',
  'failed',
  'cancelled',
  'interrupted'
])

/* -------------------------------------------------------------------------- */
/* State                                                                      */
/* -------------------------------------------------------------------------- */

const models = ref<AiModel[]>([])
const modelsLoading = ref(false)
const modelsError = ref('')

const selectedModel = ref('')

/* Persisted sessions ------------------------------------------------------ */

const conversations = ref<AiConversation[]>([])
const sessionsLoading = ref(false)
const sessionsError = ref('')
const creatingSession = ref(false)

const activeOid = ref('')
const activeConversation = ref<AiConversation | null>(null)

const historyMessages = ref<AiMessage[]>([])
const historyTotal = ref(0)
const historyLoading = ref(false)
const historyError = ref('')

/* Send / run -------------------------------------------------------------- */

const input = ref('')
const submitting = ref(false)
/** Optimistic echo of the message until the server-persisted row arrives. */
const pendingUser = ref('')
const pendingUserOid = ref('')
const activeRun = ref<AiRun | null>(null)
const runError = ref('')
const pendingProposalCount = ref(0)
/** True while checking for an active run after selecting a conversation. */
const resuming = ref(false)

const unlockDialog = ref<InstanceType<typeof AiChatUnlockDialog>>()
const listRef = ref<HTMLElement>()

/* Async guards ------------------------------------------------------------ */

let viewEpoch = 0
let historyToken = 0
let sessionsToken = 0
let submitToken = 0
let adoptToken = 0

/* Polling state ----------------------------------------------------------- */

let pollTimer: ReturnType<typeof setTimeout> | null = null
let pollRunId = ''
let pollConversationOid = ''
let pollEpoch = 0
let pollAfterRound = 0
let pollMessageSeq = 0
let pollFastTicks = 0
let pollFailures = 0

/* -------------------------------------------------------------------------- */
/* Computed                                                                   */
/* -------------------------------------------------------------------------- */

const hasModels = computed(() => models.value.length > 0)
const runActive = computed(() => {
  const run = activeRun.value
  return run !== null && ACTIVE_RUN_STATUSES.has(run.status)
})
const sessionIsLegacyChat = computed(() => activeConversation.value?.mode === 'chat')
const canSend = computed(() =>
  Boolean(
    input.value.trim() &&
      activeOid.value &&
      !submitting.value &&
      !runActive.value &&
      !resuming.value &&
      !historyLoading.value &&
      !sessionIsLegacyChat.value
  )
)
const isEmpty = computed(
  () =>
    historyMessages.value.length === 0 &&
    !pendingUser.value &&
    !runActive.value &&
    !resuming.value &&
    !historyLoading.value
)
const activeConversationTitle = computed(
  () => activeConversation.value?.title || (activeOid.value ? '会话' : '')
)
const runTokens = computed(() => activeRun.value?.total_tokens ?? 0)
const runStatusText = computed(() => {
  const run = activeRun.value
  if (!run) return ''
  if (run.status === 'queued') return run.cancel_requested ? '正在取消…' : '排队中…'
  if (run.status === 'running') {
    if (run.cancel_requested) return '正在停止…'
    return run.rounds > 0 ? `正在生成…（第 ${run.rounds} 轮）` : '正在生成…'
  }
  if (run.status === 'succeeded') {
    if (run.stop_reason === 'max_iterations') return '已完成（达到轮次上限）'
    if (run.stop_reason === 'token_budget') return '已完成（达到 token 预算）'
    return '已完成'
  }
  if (run.status === 'cancelled') return '已停止'
  if (run.status === 'interrupted') return '回答被截断'
  return '执行失败'
})

/* -------------------------------------------------------------------------- */
/* Helpers                                                                    */
/* -------------------------------------------------------------------------- */

function pad(value: number): string {
  return String(value).padStart(2, '0')
}

function formatSessionTime(value: string): string {
  if (!value) return '—'
  const raw = value.includes('T') ? value : value.replace(' ', 'T')
  const date = new Date(raw)
  if (Number.isNaN(date.getTime())) return '—'
  const diff = Date.now() - date.getTime()
  if (diff >= 0 && diff < 60_000) return '刚刚'
  if (diff >= 0 && diff < 3_600_000) return `${Math.floor(diff / 60_000)} 分钟前`
  if (diff >= 0 && diff < 86_400_000) return `${Math.floor(diff / 3_600_000)} 小时前`
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
}

function newClientMsgId(): string {
  const cryptoObject = globalThis.crypto as unknown as
    { randomUUID?: () => string } | undefined
  if (cryptoObject && typeof cryptoObject.randomUUID === 'function') {
    return cryptoObject.randomUUID()
  }
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`
}

function historyRoleLabel(message: AiMessage): string {
  if (message.role === 'user') return '我'
  if (message.role === 'assistant') return '助手'
  return message.tool_name ? `工具 · ${message.tool_name}` : '工具'
}

function historyRoleClass(message: AiMessage): string {
  if (message.role === 'user') return 'user'
  if (message.role === 'assistant') return 'assistant'
  return 'tool'
}

/** Friendly copy for a status/code from either the envelope or OpenAI face. */
function errorText(reason: unknown, fallback: string): string {
  if (reason instanceof TypeError) return '网络连接失败，请稍后重试。'
  if (reason instanceof ApiError) {
    switch (reason.code) {
      case 'AI_RUN_CONFLICT':
        return '这个会话或当前账号已有进行中的执行，请先等待或停止。'
      case 'AI_CONVERSATION_MODE_MISMATCH':
        return '历史 chat 会话不能继续发送。'
      case 'AI_AGENT_UNLOCK_REQUIRED':
        return '发送前需要解锁智能体权限（账号密码，30 分钟有效）。'
      case 'AI_AGENT_UNLOCK_FAILED':
        return '密码不正确。'
      case 'AI_AGENT_UNLOCK_LOCKED':
        return '连续验证失败次数过多，请 10 分钟后再试。'
      case 'AI_SAFETY_REJECTED':
        return '这条消息未通过内容安全审核。'
      case 'AI_FORBIDDEN':
        return '当前账号没有智能体权限。'
      case 'AI_RUN_NOT_FOUND':
        return '这次执行记录已不存在，可能已过期。'
      case 'AI_RUN_NOT_CANCELLABLE':
        return '这次执行已经结束，无需停止。'
      case 'AI_CONVERSATION_NOT_FOUND':
        return '会话不存在，或该接口在当前服务端不可用。'
      case 'AI_UPSTREAM_ERROR':
        return '上游模型暂时不可用，请稍后重试。'
      default:
        break
    }
    if (reason.status === 401) return '登录状态已失效，请重新登录。'
    if (reason.status === 403) return '当前账号没有执行权限。'
    if (reason.status === 429) return '请求过于频繁，请稍后再试。'
    if (reason.status === 503) return '服务暂不可用，请稍后重试。'
    return reason.message || fallback
  }
  if (reason && typeof reason === 'object' && 'message' in reason) {
    const record = reason as { message?: unknown; status?: unknown; code?: unknown }
    const code = typeof record.code === 'string' ? record.code : ''
    const status = typeof record.status === 'number' ? record.status : 0
    if (code === 'AI_FORBIDDEN' || status === 403) return '当前账号没有 AI 对话权限。'
    if (code === 'AI_UNAUTHORIZED' || status === 401) return '登录状态已失效，请重新登录。'
    if (status === 503) return '服务暂不可用，请稍后重试。'
    const message = String(record.message ?? '')
    if (message) return message
  }
  return fallback
}

function lastMessageSeq(): number {
  return historyMessages.value.reduce((max, message) => Math.max(max, message.seq), 0)
}

/** Merge an incremental page by `seq`; the transcript stays ascending. */
function appendMessages(incoming: AiMessage[]): number {
  if (!incoming.length) return 0
  const known = new Set(historyMessages.value.map((message) => message.seq))
  const fresh = incoming.filter((message) => !known.has(message.seq))
  if (!fresh.length) {
    if (pendingUserOid.value && incoming.some((message) => message.oid === pendingUserOid.value)) {
      pendingUser.value = ''
      pendingUserOid.value = ''
    }
    return 0
  }
  historyMessages.value = [...historyMessages.value, ...fresh].sort((a, b) => a.seq - b.seq)
  historyTotal.value = Math.max(
    historyTotal.value + fresh.length,
    historyMessages.value.length
  )
  if (pendingUserOid.value && fresh.some((message) => message.oid === pendingUserOid.value)) {
    pendingUser.value = ''
    pendingUserOid.value = ''
  }
  return fresh.length
}

/* -------------------------------------------------------------------------- */
/* Models                                                                     */
/* -------------------------------------------------------------------------- */

async function loadModels() {
  modelsLoading.value = true
  modelsError.value = ''
  try {
    const result = await listModels()
    const list = Array.isArray(result?.data) ? result.data : []
    models.value = list
    if (
      list.length > 0 &&
      (!selectedModel.value || !list.some((item) => item.id === selectedModel.value))
    ) {
      selectedModel.value = list[0]!.id
    }
    if (list.length === 0) {
      selectedModel.value = ''
      modelsError.value = '当前没有可用的模型。'
    }
  } catch (error) {
    models.value = []
    selectedModel.value = ''
    modelsError.value = errorText(error, '模型列表加载失败，请重试。')
  } finally {
    modelsLoading.value = false
  }
}

/* -------------------------------------------------------------------------- */
/* Sessions                                                                   */
/* -------------------------------------------------------------------------- */

async function loadSessions() {
  const token = ++sessionsToken
  sessionsLoading.value = true
  sessionsError.value = ''
  try {
    const page = await listConversations({ status: 'active', limit: 50 })
    if (token !== sessionsToken) return
    conversations.value = page.conversations
    if (activeOid.value) {
      const current = page.conversations.find((item) => item.oid === activeOid.value)
      if (current) {
        activeConversation.value = current
      }
      // A first-page miss (e.g. the session is older than the 50 most recently
      // updated) is not proof of deletion. Keep the selection; a real deletion
      // surfaces as a 404 on the next history/poll request.
    }
  } catch (reason) {
    if (token !== sessionsToken) return
    sessionsError.value = errorText(reason, '会话列表读取失败，请重试。')
  } finally {
    if (token === sessionsToken) sessionsLoading.value = false
  }
}

function resetRunState() {
  submitToken += 1
  unlockDialog.value?.cancel()
  input.value = ''
  adoptToken += 1
  submitting.value = false
  resuming.value = false
  pendingUser.value = ''
  pendingUserOid.value = ''
  activeRun.value = null
  runError.value = ''
  pendingProposalCount.value = 0
}

async function selectSession(oid: string) {
  if (!oid) return

  if (oid === activeOid.value) {
    // Re-selecting the current session refreshes history (and re-adopts a run),
    // but must not bump the view epoch: that would detach the live poll.
    if (!historyLoading.value) {
      const epoch = viewEpoch
      await loadHistory(oid, epoch)
      if (epoch === viewEpoch && activeOid.value === oid) {
        void adoptActiveRun(oid, epoch)
      }
    }
    return
  }

  stopPolling()
  resetRunState()
  const epoch = ++viewEpoch
  activeOid.value = oid
  activeConversation.value = conversations.value.find((item) => item.oid === oid) ?? null
  historyMessages.value = []
  historyTotal.value = 0
  historyError.value = ''
  historyLoading.value = false

  await loadHistory(oid, epoch)
  if (epoch !== viewEpoch) return
  void scrollToBottom()
  void adoptActiveRun(oid, epoch)
}

async function loadHistory(oid: string, epoch: number) {
  const token = ++historyToken
  historyLoading.value = true
  historyError.value = ''

  const isCurrent = () =>
    token === historyToken && epoch === viewEpoch && activeOid.value === oid

  try {
    const detail = await getConversation(oid)
    if (!isCurrent()) return
    activeConversation.value = detail
    const index = conversations.value.findIndex((item) => item.oid === oid)
    if (index >= 0) conversations.value[index] = detail

    const first = await listMessages(oid, { afterSeq: 0, limit: HISTORY_PAGE_SIZE })
    if (!isCurrent()) return

    let messages = first.messages
    if (first.total > first.messages.length && first.messages.length > 0) {
      const tail = await listMessages(oid, {
        afterSeq: Math.max(0, first.total - first.messages.length),
        limit: HISTORY_PAGE_SIZE
      })
      if (!isCurrent()) return
      messages = tail.messages
    }

    historyMessages.value = messages
    historyTotal.value = first.total
    if (pollRunId && pollConversationOid === oid) {
      pollMessageSeq = Math.max(pollMessageSeq, lastMessageSeq())
    }
    if (pendingUserOid.value && messages.some((message) => message.oid === pendingUserOid.value)) {
      pendingUser.value = ''
      pendingUserOid.value = ''
    }
    void scrollToBottom()
  } catch (reason) {
    if (!isCurrent()) return
    // Keep the last good transcript on a refresh failure; the error banner is
    // enough signal and wiping the bubbles would look like data loss.
    historyError.value = errorText(reason, '会话消息读取失败，请重试。')
  } finally {
    if (isCurrent()) historyLoading.value = false
  }
}

function retryHistory() {
  const oid = activeOid.value
  if (!oid) return
  void loadHistory(oid, viewEpoch)
  // Recover a poll that stopped after transient failures; a finished run simply
  // yields no active row and leaves the existing error/transcript untouched.
  if (!pollRunId) void adoptActiveRun(oid, viewEpoch)
}

async function createSession(): Promise<string | null> {
  if (creatingSession.value) return null
  creatingSession.value = true
  sessionsError.value = ''
  try {
    // No title: the first user message becomes the title server-side, matching
    // the main client's behavior.
    const created = await createConversation(
      'agent',
      undefined,
      selectedModel.value || undefined
    )
    sessionsToken += 1
    sessionsLoading.value = false
    conversations.value = [
      created,
      ...conversations.value.filter((item) => item.oid !== created.oid)
    ]
    await selectSession(created.oid)
    return created.oid
  } catch (reason) {
    sessionsError.value = errorText(reason, '新建会话失败，请稍后重试。')
    return null
  } finally {
    creatingSession.value = false
  }
}

/* -------------------------------------------------------------------------- */
/* Run polling                                                                */
/* -------------------------------------------------------------------------- */

function isRunActive(status: AiRun['status']): boolean {
  return ACTIVE_RUN_STATUSES.has(status)
}

function isPollCurrent(runId: string, oid: string, epoch: number): boolean {
  return pollRunId === runId && activeOid.value === oid && viewEpoch === epoch
}

function nextPollDelay(): number {
  if (typeof document !== 'undefined' && document.hidden) return POLL_HIDDEN_MS
  if (pollFastTicks > 0) {
    pollFastTicks -= 1
    return POLL_FAST_MS
  }
  return POLL_INTERVAL_MS
}

function schedulePoll(delay: number) {
  if (pollTimer) clearTimeout(pollTimer)
  pollTimer = setTimeout(() => {
    pollTimer = null
    void pollTick()
  }, delay)
}

function stopPolling() {
  if (pollTimer) {
    clearTimeout(pollTimer)
    pollTimer = null
  }
  pollRunId = ''
  pollConversationOid = ''
  pollEpoch = 0
  pollFailures = 0
}

function startPolling(runId: string, oid: string, epoch: number, afterRound = 0) {
  stopPolling()
  pollRunId = runId
  pollConversationOid = oid
  pollEpoch = epoch
  pollAfterRound = Math.max(0, afterRound)
  pollMessageSeq = lastMessageSeq()
  pollFastTicks = POLL_FAST_TICKS
  pollFailures = 0
  schedulePoll(POLL_FAST_MS)
}

async function pollTick() {
  const runId = pollRunId
  const oid = pollConversationOid
  const epoch = pollEpoch
  if (!runId) return

  try {
    const detail = await fetchRunDetail(runId, pollAfterRound, pollMessageSeq)
    if (!isPollCurrent(runId, oid, epoch)) return

    activeRun.value = detail.run
    pollAfterRound = Math.max(pollAfterRound, detail.run.rounds)
    const added = appendMessages(detail.messages)
    pollMessageSeq = Math.max(pollMessageSeq, lastMessageSeq())
    pendingProposalCount.value = detail.proposals.length
    pollFailures = 0
    if (added > 0) void scrollToBottom()

    if (TERMINAL_RUN_STATUSES.has(detail.run.status)) {
      finishRun(detail, oid, epoch)
      return
    }
    schedulePoll(nextPollDelay())
  } catch (error) {
    if (!isPollCurrent(runId, oid, epoch)) return
    if (error instanceof ApiError && error.status === 404) {
      stopPolling()
      runError.value = errorText(error, '这次执行记录已不存在。')
      return
    }
    pollFailures += 1
    if (pollFailures >= POLL_MAX_FAILURES) {
      stopPolling()
      runError.value = errorText(error, '执行进度读取失败，请刷新会话重试。')
      return
    }
    schedulePoll(POLL_INTERVAL_MS)
  }
}

function finishRun(detail: AiRunDetail, oid: string, epoch: number) {
  stopPolling()
  activeRun.value = detail.run
  pendingUser.value = ''
  pendingUserOid.value = ''
  pendingProposalCount.value = detail.proposals.length

  if (detail.run.status === 'failed' || detail.run.status === 'interrupted') {
    runError.value = detail.run.error_message || '这次执行没有正常完成。'
  } else if (detail.run.status === 'cancelled') {
    runError.value = ''
  }

  if (activeOid.value !== oid || viewEpoch !== epoch) return
  // Reload the tail so the final assistant/tool messages are present even if the
  // last poll raced the terminal write; also refreshes title/message_count.
  void loadHistory(oid, epoch)
  void loadSessions()
}

/** Resume an active run after selecting/reloading a conversation. */
async function adoptActiveRun(oid: string, epoch: number) {
  if (sessionIsLegacyChat.value) return
  const token = ++adoptToken
  resuming.value = true
  try {
    const runs = await fetchActiveRuns()
    if (token !== adoptToken || activeOid.value !== oid || viewEpoch !== epoch) return
    const run = runs.find(
      (item) => item.conversation_oid === oid && isRunActive(item.status)
    )
    if (!run) {
      // A previous poll may have missed the terminal write. Only reload when we
      // actually held a running state (or a poll) — a fresh selection already
      // loaded its history and must not issue a duplicate request.
      const current = activeRun.value
      const hadActive =
        current !== null &&
        current.conversation_oid === oid &&
        isRunActive(current.status)
      if (hadActive || pollRunId) {
        stopPolling()
        if (hadActive) activeRun.value = null
        void loadHistory(oid, epoch)
      }
      return
    }
    activeRun.value = run
    runError.value = ''
    startPolling(run.oid, oid, epoch, run.rounds)
  } catch {
    // Best-effort only: a failed resume must not block reading history.
  } finally {
    if (
      token === adoptToken &&
      activeOid.value === oid &&
      viewEpoch === epoch
    ) {
      resuming.value = false
    }
  }
}

/* -------------------------------------------------------------------------- */
/* Send                                                                       */
/* -------------------------------------------------------------------------- */

function placeholderRun(result: { run_id: string; status: 'queued' }, oid: string): AiRun {
  return {
    oid: result.run_id,
    conversation_oid: oid,
    status: result.status,
    model: activeConversation.value?.model ?? null,
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
}

async function send() {
  if (!canSend.value) return

  const oid = activeOid.value
  const text = input.value.trim()
  if (!oid || !text) return

  const epoch = viewEpoch
  const token = ++submitToken
  const clientMsgId = newClientMsgId()

  runError.value = ''
  pendingProposalCount.value = 0
  pendingUser.value = text
  pendingUserOid.value = ''
  input.value = ''
  submitting.value = true
  void scrollToBottom()

  const submit = () => submitRun(oid, text, { clientMsgId })

  try {
    let result: AiRunSubmitResult | null = null
    try {
      result = await submit()
    } catch (error) {
      if (!(error instanceof ApiError) || error.code !== 'AI_AGENT_UNLOCK_REQUIRED') {
        throw error
      }
      if (token !== submitToken || epoch !== viewEpoch || activeOid.value !== oid) return
      // The agent send path is password-gated server-side (30-minute window).
      const unlocked = (await unlockDialog.value?.request()) ?? false
      if (!unlocked) {
        if (token === submitToken && epoch === viewEpoch && activeOid.value === oid) {
          pendingUser.value = ''
          if (!input.value) input.value = text
          runError.value = '发送前需要解锁智能体权限（账号密码，30 分钟有效）。'
        }
        return
      }
      if (token !== submitToken || epoch !== viewEpoch || activeOid.value !== oid) return
      // Reuse the same idempotency key: the server returns the same run/message
      // if the first attempt had actually landed before the unlock rejection.
      result = await submit()
    }

    if (!result) return
    if (token !== submitToken || epoch !== viewEpoch || activeOid.value !== oid) return

    pendingUserOid.value = result.message_oid
    const alreadyVisible = historyMessages.value.some(
      (message) => message.oid === result.message_oid
    )
    if (alreadyVisible) {
      pendingUser.value = ''
      pendingUserOid.value = ''
    }
    activeRun.value = placeholderRun(result, oid)
    startPolling(result.run_id, oid, epoch)
    // The first message sets the server-side title; refresh the rail for it.
    void loadSessions()
  } catch (error) {
    if (token === submitToken && epoch === viewEpoch && activeOid.value === oid) {
      pendingUser.value = ''
      pendingUserOid.value = ''
      if (!input.value) input.value = text
      runError.value = errorText(error, '消息发送失败，请重试。')
    }
  } finally {
    if (token === submitToken) submitting.value = false
  }
}

async function cancelCurrentRun() {
  const run = activeRun.value
  if (!run || !isRunActive(run.status)) return
  try {
    await cancelRun(run.oid)
    const current = activeRun.value
    if (current && current.oid === run.oid) {
      activeRun.value = { ...current, cancel_requested: true }
    }
  } catch (error) {
    runError.value = errorText(error, '停止失败，请稍后重试。')
  }
}

/* -------------------------------------------------------------------------- */
/* Lifecycle                                                                  */
/* -------------------------------------------------------------------------- */

async function scrollToBottom() {
  const oid = activeOid.value
  await nextTick()
  if (oid !== activeOid.value) return
  const element = listRef.value
  if (element) element.scrollTop = element.scrollHeight
}

function handleKeydown(event: KeyboardEvent) {
  if (event.isComposing) return
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault()
    void send()
  }
}

onMounted(() => {
  void loadModels()
  void loadSessions()
  void resumeActiveRunOnMount()
})

/**
 * After a remount (tab switch/reload) pick up the user's in-flight run and
 * select its conversation. Without this, the run keeps going server-side but the
 * fresh panel would show no progress until the user clicks the session again.
 */
async function resumeActiveRunOnMount() {
  const epoch = viewEpoch
  try {
    const runs = await fetchActiveRuns()
    if (epoch !== viewEpoch || activeOid.value) return
    const run = runs.find((item) => isRunActive(item.status) && item.conversation_oid)
    if (!run) return
    await selectSession(run.conversation_oid)
  } catch {
    // No active run / feature unavailable: stay on the empty state.
  }
}

onUnmounted(() => {
  // Invalidate/settle every in-flight load, submit, and poll before releasing.
  viewEpoch += 1
  historyToken += 1
  sessionsToken += 1
  submitToken += 1
  adoptToken += 1
  unlockDialog.value?.cancel()
  stopPolling()
})
</script>

<template>
  <div class="ai-chat-panel">
    <div class="chat-layout">
      <!-- Session rail -->
      <aside class="session-rail" aria-label="持久会话">
        <div class="session-rail__head">
          <span class="session-rail__title">持久会话</span>
          <div class="session-rail__actions">
            <button
              type="button"
              class="ghost-button ghost-button--small"
              :disabled="creatingSession"
              @click="createSession"
            >
              {{ creatingSession ? '新建中…' : '新建' }}
            </button>
            <button
              type="button"
              class="ghost-button ghost-button--small"
              :disabled="sessionsLoading"
              @click="loadSessions"
            >
              {{ sessionsLoading ? '刷新中…' : '刷新' }}
            </button>
          </div>
        </div>

        <BubbleFeedback
          v-if="sessionsError"
          class="session-note glass-feedback--error"
          role="alert"
        >
          {{ sessionsError }}
        </BubbleFeedback>

        <p v-if="sessionsLoading && !conversations.length" class="session-note">
          正在读取会话…
        </p>
        <p v-else-if="!conversations.length" class="session-note">
          还没有持久会话。新建后，发送的消息会写入服务端。
        </p>
        <ul v-else class="session-list">
          <li v-for="conversation in conversations" :key="conversation.oid">
            <button
              type="button"
              class="session-item"
              :class="{ 'is-active': conversation.oid === activeOid }"
              :aria-current="conversation.oid === activeOid ? 'true' : undefined"
              @click="selectSession(conversation.oid)"
            >
              <span class="session-item__title">
                {{ conversation.title || '未命名会话' }}
              </span>
              <span class="session-item__meta">
                <span class="session-badge">
                  {{ conversation.mode === 'agent' ? '智能体' : '历史' }}
                </span>
                <span>{{ conversation.message_count }} 条</span>
                <span>{{ formatSessionTime(conversation.updated_at) }}</span>
              </span>
            </button>
          </li>
        </ul>
        <p class="session-rail__footnote">
          会话按当前账号隔离；消息与回答写入服务端，刷新后可继续。
        </p>
      </aside>

      <!-- Chat column -->
      <div class="chat-main">
        <!-- Controls -->
        <div class="chat-controls">
          <div class="control-row">
            <label for="chat-model-select" class="control-label">
              {{ activeOid ? '新建模型' : '模型' }}
            </label>
            <select
              id="chat-model-select"
              v-model="selectedModel"
              class="model-select"
              :disabled="modelsLoading || !hasModels"
            >
              <option v-if="modelsLoading" value="" disabled>加载中…</option>
              <option v-else-if="!hasModels" value="" disabled>无可用模型</option>
              <option v-for="model in models" :key="model.id" :value="model.id">
                {{ model.id }}
              </option>
            </select>
            <button
              type="button"
              class="ghost-button ghost-button--small"
              :disabled="modelsLoading"
              @click="loadModels"
            >
              {{ modelsLoading ? '刷新中…' : '刷新' }}
            </button>
            <span v-if="activeOid" class="control-session">
              当前会话模型 · {{ activeConversation?.model || '服务端默认' }}（锁定）
            </span>
            <span v-else class="control-session">
              新建会话将使用 {{ selectedModel || '服务端默认' }}
            </span>
          </div>

          <div class="control-row">
            <span class="control-hint">
              消息与回答会写入当前会话，刷新后仍可查看。
            </span>
            <button
              type="button"
              class="ghost-button ghost-button--small"
              :disabled="!activeOid || historyLoading"
              @click="retryHistory"
            >
              {{ historyLoading ? '读取中…' : '刷新消息' }}
            </button>
          </div>
        </div>

        <!-- Model error -->
        <div v-if="modelsError && !modelsLoading" class="error-banner">
          <BubbleFeedback class="glass-feedback--error" role="alert">{{ modelsError }}</BubbleFeedback>
          <button type="button" class="ghost-button ghost-button--small" @click="loadModels">
            重试
          </button>
        </div>

        <!-- Messages -->
        <div ref="listRef" class="chat-list" role="log" aria-live="polite" aria-atomic="false">
          <div v-if="activeOid" class="history-head">
            <span class="history-head__title">{{ activeConversationTitle }}</span>
            <span class="history-head__meta">
              <template v-if="historyLoading">正在加载消息…</template>
              <template v-else>共 {{ historyTotal }} 条消息</template>
            </span>
          </div>

          <div v-if="historyError" class="history-error" role="alert">
            <BubbleFeedback class="glass-feedback--error">
              {{ historyError }}
            </BubbleFeedback>
            <button
              type="button"
              class="ghost-button ghost-button--small"
              @click="retryHistory"
            >
              重试
            </button>
          </div>

          <div v-if="runError" class="history-error" role="alert">
            <BubbleFeedback class="glass-feedback--error">{{ runError }}</BubbleFeedback>
          </div>

          <div
            v-if="historyLoading && !historyMessages.length"
            class="history-loading"
            role="status"
          >
            <span class="spinner" aria-hidden="true"></span>
            正在读取会话消息…
          </div>
          <p
            v-else-if="
              activeOid &&
              !historyMessages.length &&
              !historyError &&
              !pendingUser &&
              !runActive
            "
            class="history-empty"
          >
            这个会话还没有消息。
          </p>

          <p v-if="sessionIsLegacyChat" class="history-empty">
            历史 chat 会话已下线，只能查看与整理，不能继续发送。
          </p>

          <div v-if="activeRun" class="run-status" :class="{ 'is-active': runActive }">
            <span v-if="runActive" class="spinner" aria-hidden="true"></span>
            <span class="run-status__text">{{ runStatusText }}</span>
            <span v-if="runActive" class="run-status__hint">
              回答会在轮次结束时写入会话
            </span>
            <span v-else-if="runTokens > 0" class="run-status__hint">
              {{ runTokens }} tokens
            </span>
          </div>

          <div
            v-for="message in historyMessages"
            :key="`history-${message.oid || message.seq}`"
            class="chat-turn"
            :class="historyRoleClass(message)"
          >
            <div class="turn-header">
              <span class="turn-role">{{ historyRoleLabel(message) }}</span>
              <span class="turn-status">{{ formatSessionTime(message.created_at) }}</span>
            </div>
            <div class="turn-content">
              <p class="turn-text">{{ message.content || '（无文本内容）' }}</p>
            </div>
          </div>

          <div v-if="pendingUser" class="chat-turn user pending">
            <div class="turn-header">
              <span class="turn-role">我</span>
              <span class="turn-status" role="status">
                <span class="spinner" aria-hidden="true"></span>
                发送中…
              </span>
            </div>
            <div class="turn-content">
              <p class="turn-text">{{ pendingUser }}</p>
            </div>
          </div>

          <BubbleFeedback
            v-if="pendingProposalCount > 0"
            class="run-note"
            role="status"
          >
            本次执行产生了 {{ pendingProposalCount }} 条待确认的写操作提案。Pro 控制台只读，
            请到主站确认或驳回。
          </BubbleFeedback>

          <div v-if="isEmpty" class="empty-state">
            <svg width="64" height="64" viewBox="0 0 24 24" fill="none" aria-hidden="true">
              <path
                d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"
                stroke="currentColor"
                stroke-width="1.5"
                stroke-linecap="round"
                stroke-linejoin="round"
                fill="none"
              />
            </svg>
            <p class="empty-hint">
              {{
                !activeOid
                  ? '新建或选择一个会话后开始对话'
                  : sessionIsLegacyChat
                    ? '历史 chat 会话只读'
                    : '输入消息开始对话；回答会写入服务端会话'
              }}
            </p>
          </div>
        </div>

        <!-- Input -->
        <div class="chat-input-area">
          <textarea
            v-model="input"
            class="chat-input"
            placeholder="输入消息…按 Enter 发送，Shift+Enter 换行"
            rows="3"
            maxlength="20000"
            :disabled="submitting || !activeOid || sessionIsLegacyChat"
            @keydown="handleKeydown"
          ></textarea>
          <div class="input-actions">
            <span class="input-hint">
              {{
                !activeOid
                  ? '先新建或选择一个会话'
                  : sessionIsLegacyChat
                    ? '历史 chat 会话只读'
                    : runActive
                      ? '正在生成，可停止或等待完成'
                      : submitting
                        ? '正在提交…'
                        : '首次发送需要账号密码解锁，30 分钟内有效'
              }}
            </span>
            <button
              v-if="runActive"
              type="button"
              class="ghost-button"
              @click="cancelCurrentRun"
            >
              <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
                <rect x="4" y="4" width="8" height="8" rx="1" />
              </svg>
              停止
            </button>
            <button
              v-else
              type="button"
              class="primary-button"
              :disabled="submitting || !canSend"
              @click="send"
            >
              {{ submitting ? '提交中…' : '发送' }}
            </button>
          </div>
        </div>
      </div>
    </div>

    <AiChatUnlockDialog ref="unlockDialog" />
  </div>
</template>

<style scoped>
.ai-chat-panel {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  min-height: 500px;
}

/* -------------------------------------------------------------------------- */
/* Layout: session rail + chat column                                          */
/* -------------------------------------------------------------------------- */

.chat-layout {
  display: grid;
  grid-template-columns: minmax(200px, 240px) minmax(0, 1fr);
  gap: var(--space-4);
  align-items: start;
  min-width: 0;
}

.session-rail {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding: var(--space-3);
  border-radius: var(--radius-lg);
  background: rgba(255, 255, 255, 0.42);
  border: 1px solid rgba(255, 255, 255, 0.68);
  min-width: 0;
}

.session-rail__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
  flex-wrap: wrap;
}

.session-rail__title {
  font-size: var(--font-size-caption);
  font-weight: 600;
  color: var(--color-text-secondary);
}

.session-rail__actions {
  display: flex;
  align-items: center;
  gap: 4px;
  flex-wrap: wrap;
}

.session-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
  max-height: 440px;
  overflow-y: auto;
}

.session-item {
  width: 100%;
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 8px 10px;
  border: 1px solid transparent;
  border-radius: var(--radius-md);
  background: transparent;
  color: inherit;
  font: inherit;
  text-align: left;
  cursor: pointer;
  transition:
    background var(--duration-fast) var(--ease-smooth),
    border-color var(--duration-fast) var(--ease-smooth);
}

.session-item:hover {
  background: rgba(255, 255, 255, 0.62);
}

.session-item.is-active {
  background: var(--tone-blue-bg);
  border-color: rgba(0, 113, 227, 0.24);
}

.session-item:focus-visible {
  outline: 2px solid var(--color-brand);
  outline-offset: 1px;
}

.session-item__title {
  font-size: var(--font-size-callout);
  font-weight: 600;
  color: var(--color-text-primary);
  line-height: var(--line-height-snug);
  overflow-wrap: anywhere;
}

.session-item__meta {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  font-size: var(--font-size-caption);
  color: var(--color-text-tertiary);
}

.session-badge {
  padding: 1px 6px;
  border-radius: var(--radius-pill);
  background: var(--tone-ink-bg);
  color: var(--tone-ink);
  font-size: 11px;
  font-weight: 600;
}

.session-note {
  margin: 0;
  font-size: var(--font-size-caption);
  color: var(--color-text-tertiary);
  line-height: var(--line-height-normal);
}

.session-rail__footnote {
  margin: 0;
  font-size: 11px;
  color: var(--color-text-tertiary);
  line-height: var(--line-height-normal);
}

.chat-main {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  min-width: 0;
}

.control-session {
  min-width: 0;
  max-width: 100%;
  margin-left: auto;
  font-size: var(--font-size-caption);
  color: var(--color-text-tertiary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* -------------------------------------------------------------------------- */
/* Persisted history                                                           */
/* -------------------------------------------------------------------------- */

.history-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--space-2);
  flex-wrap: wrap;
  padding-bottom: var(--space-2);
  margin-bottom: var(--space-3);
  border-bottom: 1px solid var(--color-border-hairline);
  font-size: var(--font-size-caption);
  color: var(--color-text-secondary);
}

.history-head__title {
  font-weight: 600;
  color: var(--color-text-primary);
  overflow-wrap: anywhere;
}

.history-loading {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-2) 0;
  font-size: var(--font-size-callout);
  color: var(--color-text-tertiary);
}

.history-empty {
  padding: var(--space-2) 0;
  font-size: var(--font-size-callout);
  color: var(--color-text-tertiary);
}

.history-error {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-wrap: wrap;
  margin-bottom: var(--space-3);
}

.history-divider {
  margin: var(--space-4) 0 var(--space-3);
  padding-top: var(--space-2);
  border-top: 1px dashed var(--color-border-hairline);
  font-size: var(--font-size-caption);
  color: var(--color-text-tertiary);
  text-align: center;
}

.run-status {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-wrap: wrap;
  padding: var(--space-2) var(--space-3);
  margin-bottom: var(--space-3);
  border-radius: var(--radius-md);
  border: 1px solid var(--color-border-hairline);
  background: rgba(255, 255, 255, 0.5);
  font-size: var(--font-size-caption);
  color: var(--color-text-secondary);
}

.run-status.is-active {
  border-color: rgba(0, 113, 227, 0.24);
  background: var(--tone-blue-bg);
  color: var(--color-brand);
}

.run-status__text {
  font-weight: 600;
}

.run-status__hint {
  color: var(--color-text-tertiary);
}

.run-note {
  margin-top: var(--space-3);
}

.chat-turn.pending {
  opacity: 0.72;
}

.chat-turn.tool .turn-role {
  color: var(--tone-amber);
}

.chat-turn.tool .turn-text {
  max-height: 220px;
  overflow-y: auto;
  background: var(--tone-amber-bg);
  border-color: rgba(255, 159, 10, 0.24);
  color: var(--color-text-secondary);
}

/* -------------------------------------------------------------------------- */
/* Controls                                                                    */
/* -------------------------------------------------------------------------- */

.chat-controls {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding: var(--space-4);
  border-radius: var(--radius-lg);
  background: rgba(255, 255, 255, 0.42);
  border: 1px solid rgba(255, 255, 255, 0.68);
}

.control-row {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  flex-wrap: wrap;
}

.control-label {
  font-size: var(--font-size-caption);
  font-weight: 600;
  color: var(--color-text-secondary);
  min-width: 64px;
}

.control-hint {
  font-size: var(--font-size-caption);
  color: var(--color-text-tertiary);
  margin-left: auto;
}

.model-select {
  flex: 1 1 200px;
  min-width: 0;
  max-width: 320px;
  padding: 8px var(--space-3);
  border: 1px solid var(--color-border-moderate);
  border-radius: var(--radius-md);
  background: rgba(255, 255, 255, 0.72);
  color: var(--color-text-primary);
  font: inherit;
  font-size: var(--font-size-callout);
  line-height: var(--line-height-normal);
  cursor: pointer;
  transition: border-color var(--duration-fast) var(--ease-smooth);
}

.model-select:focus-visible {
  outline: 2px solid var(--color-brand);
  outline-offset: 1px;
  border-color: transparent;
}

.model-select:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}

.toggle-switch {
  position: relative;
  display: inline-block;
  width: 44px;
  height: 24px;
  cursor: pointer;
}

.toggle-switch input {
  position: absolute;
  opacity: 0;
  width: 0;
  height: 0;
}

.toggle-slider {
  position: absolute;
  inset: 0;
  background: var(--tone-ink-bg);
  border-radius: var(--radius-pill);
  transition: background var(--duration-fast) var(--ease-smooth);
}

.toggle-slider::before {
  content: '';
  position: absolute;
  left: 2px;
  top: 2px;
  width: 20px;
  height: 20px;
  background: #ffffff;
  border-radius: 50%;
  box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);
  transition: transform var(--duration-fast) var(--ease-spring);
}

.toggle-switch input:checked + .toggle-slider {
  background: var(--color-brand);
}

.toggle-switch input:checked + .toggle-slider::before {
  transform: translateX(20px);
}

.toggle-switch input:focus-visible + .toggle-slider {
  outline: 2px solid var(--color-brand);
  outline-offset: 2px;
}

.toggle-switch input:disabled + .toggle-slider {
  opacity: 0.55;
  cursor: not-allowed;
}

/* -------------------------------------------------------------------------- */
/* Error banner                                                                */
/* -------------------------------------------------------------------------- */

.error-banner {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  flex-wrap: wrap;
}

/* -------------------------------------------------------------------------- */
/* Messages                                                                    */
/* -------------------------------------------------------------------------- */

.chat-list {
  flex: 1;
  min-height: 360px;
  max-height: 520px;
  overflow-y: auto;
  padding: var(--space-4);
  border-radius: var(--radius-lg);
  background: rgba(255, 255, 255, 0.38);
  border: 1px solid rgba(255, 255, 255, 0.68);
  scroll-behavior: smooth;
}

.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--space-3);
  min-height: 280px;
  color: var(--color-text-tertiary);
}

.empty-state svg {
  opacity: 0.4;
}

.empty-hint {
  font-size: var(--font-size-callout);
  color: var(--color-text-tertiary);
  text-align: center;
}

.chat-turn {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  margin-bottom: var(--space-4);
  animation: surface-enter-keyframes 250ms var(--ease-settle) backwards;
}

.turn-header {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  font-size: var(--font-size-caption);
}

.turn-role {
  font-weight: 600;
  color: var(--color-text-secondary);
}

.chat-turn.user .turn-role {
  color: var(--color-brand);
}

.turn-status {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: var(--font-size-caption);
  color: var(--color-text-tertiary);
}

.turn-status--warn {
  color: #a86400;
}

.turn-status--error {
  color: var(--tone-rose);
}

.turn-content {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.chat-turn.user .turn-content {
  align-items: flex-end;
}

.turn-text {
  max-width: 85%;
  padding: var(--space-3) var(--space-4);
  border-radius: var(--radius-lg);
  background: rgba(255, 255, 255, 0.82);
  border: 1px solid rgba(255, 255, 255, 0.92);
  color: var(--color-text-primary);
  font-size: var(--font-size-callout);
  line-height: var(--line-height-relaxed);
  white-space: pre-wrap;
  word-wrap: break-word;
  overflow-wrap: anywhere;
  box-shadow: 0 2px 8px rgba(18, 48, 82, 0.04);
}

.chat-turn.user .turn-text {
  background: linear-gradient(145deg, #eaf4fd, #d9e8f8);
  border-color: #c3d9ee;
  color: #194873;
  box-shadow: var(--glass-rim), 0 2px 8px #164b7914;
}

.turn-placeholder {
  padding: var(--space-3) var(--space-4);
  color: var(--color-text-tertiary);
  font-size: var(--font-size-callout);
  animation: pulse 1200ms ease-in-out infinite;
}

@keyframes pulse {
  0%, 100% {
    opacity: 0.4;
  }
  50% {
    opacity: 0.8;
  }
}

.turn-error {
  max-width: 85%;
}

.error-code {
  font-family: 'SF Mono', 'Menlo', 'Monaco', 'Courier New', monospace;
  font-size: 11px;
  opacity: 0.8;
}

/* -------------------------------------------------------------------------- */
/* Input                                                                       */
/* -------------------------------------------------------------------------- */

.chat-input-area {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding: var(--space-3);
  border-radius: var(--radius-lg);
  background: rgba(255, 255, 255, 0.62);
  border: 1px solid rgba(255, 255, 255, 0.82);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.92);
}

.chat-input {
  width: 100%;
  min-height: 72px;
  padding: var(--space-2) var(--space-3);
  border: 1px solid var(--color-border-moderate);
  border-radius: var(--radius-md);
  background: rgba(255, 255, 255, 0.88);
  color: var(--color-text-primary);
  font: inherit;
  font-size: var(--font-size-callout);
  line-height: var(--line-height-relaxed);
  resize: vertical;
  transition: border-color var(--duration-fast) var(--ease-smooth);
}

.chat-input:focus-visible {
  outline: 2px solid var(--color-brand);
  outline-offset: 1px;
  border-color: transparent;
}

.chat-input:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}

.chat-input::placeholder {
  color: var(--color-text-tertiary);
}

.input-actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: var(--space-2);
}

.input-hint {
  margin-right: auto;
  font-size: var(--font-size-caption);
  color: var(--color-text-tertiary);
  line-height: var(--line-height-normal);
  overflow-wrap: anywhere;
}

/* -------------------------------------------------------------------------- */
/* Buttons                                                                     */
/* -------------------------------------------------------------------------- */

.ghost-button,
.primary-button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  min-height: 38px;
  padding: 0 var(--space-4);
  border-radius: var(--radius-pill);
  border: 1px solid rgba(255, 255, 255, 0.82);
  background: linear-gradient(
    135deg,
    rgba(255, 255, 255, 0.72) 0%,
    rgba(232, 242, 252, 0.5) 100%
  );
  color: var(--color-text-primary);
  font-family: inherit;
  font-size: var(--font-size-callout);
  font-weight: 600;
  line-height: 1;
  white-space: nowrap;
  cursor: pointer;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.86),
    0 2px 8px rgba(18, 48, 82, 0.07);
  transition:
    transform var(--duration-fast) var(--ease-spring),
    box-shadow var(--duration-fast) var(--ease-smooth);
}

.ghost-button:hover:not(:disabled),
.primary-button:hover:not(:disabled) {
  transform: translateY(-1px);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.94),
    0 4px 12px rgba(18, 48, 82, 0.11);
}

.ghost-button:active:not(:disabled),
.primary-button:active:not(:disabled) {
  transform: scale(0.97);
}

.ghost-button:disabled,
.primary-button:disabled {
  opacity: 0.55;
  cursor: not-allowed;
  transform: none;
}

.ghost-button:focus-visible,
.primary-button:focus-visible {
  outline: 2px solid var(--color-brand);
  outline-offset: 2px;
}

.ghost-button--small {
  min-height: 32px;
  padding: 0 var(--space-3);
  font-size: var(--font-size-caption);
}

.primary-button {
  border-color: rgba(0, 113, 227, 0.22);
  background: linear-gradient(135deg, #409cff 0%, #0071e3 100%);
  color: #ffffff;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.34),
    0 3px 12px rgba(0, 113, 227, 0.24);
}

.primary-button:hover:not(:disabled) {
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.4),
    0 6px 18px rgba(0, 113, 227, 0.3);
}

.spinner {
  width: 12px;
  height: 12px;
  border-radius: 50%;
  border: 2px solid rgba(0, 113, 227, 0.2);
  border-top-color: var(--color-brand);
  animation: spin 700ms linear infinite;
  flex-shrink: 0;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}

/* -------------------------------------------------------------------------- */
/* Responsive                                                                  */
/* -------------------------------------------------------------------------- */

@media (max-width: 900px) {
  .chat-layout {
    grid-template-columns: minmax(0, 1fr);
  }

  .session-list {
    max-height: 180px;
  }

  .control-session {
    margin-left: 0;
    white-space: normal;
  }
}

@media (max-width: 640px) {
  .ai-chat-panel {
    min-height: 420px;
  }

  .chat-controls {
    padding: var(--space-3);
  }

  .control-row {
    flex-direction: column;
    align-items: flex-start;
  }

  .control-label {
    min-width: auto;
  }

  .control-hint {
    margin-left: 0;
  }

  .model-select {
    width: 100%;
    max-width: none;
  }

  .chat-list {
    min-height: 280px;
    max-height: 420px;
    padding: var(--space-3);
  }

  .turn-text,
  .turn-error {
    max-width: 92%;
  }

  .chat-input {
    min-height: 64px;
  }

  .input-actions {
    flex-wrap: wrap;
  }

  .input-hint {
    width: 100%;
    margin-right: 0;
  }
}

@media (max-width: 320px) {
  .turn-text,
  .turn-error {
    max-width: 100%;
  }
}

/* -------------------------------------------------------------------------- */
/* Reduced motion                                                              */
/* -------------------------------------------------------------------------- */

@media (prefers-reduced-motion: reduce) {
  .chat-turn,
  .turn-placeholder,
  .ghost-button,
  .primary-button,
  .toggle-slider,
  .toggle-slider::before,
  .model-select,
  .chat-input,
  .spinner {
    animation: none;
    transition: none;
  }

  .ghost-button:hover:not(:disabled),
  .primary-button:hover:not(:disabled) {
    transform: none;
  }

  .chat-list {
    scroll-behavior: auto;
  }
}
</style>
