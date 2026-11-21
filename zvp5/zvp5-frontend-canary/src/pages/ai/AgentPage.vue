<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'
import {
  ackRisk,
  cancelRun,
  confirmProposal,
  createConversation,
  deleteConversation,
  fetchConversation,
  fetchMessages,
  fetchRiskAck,
  fetchRunDetail,
  listActiveRuns,
  listConversations,
  listProposals,
  rejectProposal,
  streamRunEvents,
  submitRun,
  unlockAgent,
  updateConversation,
  type AiStreamToolFrame
} from '@/shared/api/ai'
import type {
  AiConversation,
  AiConversationStatus,
  AiLiveAssistant,
  AiLiveState,
  AiLiveTool,
  AiMessage,
  AiProposal,
  AiRun,
  AiRunDetail,
  AiRunStatus
} from '@/types/domain'
import { ApiError } from '@/shared/http/errors'
import { formatDateTime } from '@/shared/lib/format'
import { useUiStore } from '@/shared/stores/ui'
import ZAlert from '@/shared/ui/ZAlert.vue'
import ZBadge from '@/shared/ui/ZBadge.vue'
import ZButton from '@/shared/ui/ZButton.vue'
import ZEmpty from '@/shared/ui/ZEmpty.vue'
import ZField from '@/shared/ui/ZField.vue'
import ZMarkdown from '@/shared/ui/ZMarkdown.vue'
import ZModal from '@/shared/ui/ZModal.vue'
import ZProgress from '@/shared/ui/ZProgress.vue'
import ZSegmented from '@/shared/ui/ZSegmented.vue'
import ZSkeleton from '@/shared/ui/ZSkeleton.vue'
import AgentProposalCard from './AgentProposalCard.vue'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const ui = useUiStore()

const initialSection = typeof route.params.section === 'string' ? route.params.section : ''

/* ------------------------------ 会话列表 ------------------------------ */

const tab = ref('active')
const conversations = ref<AiConversation[]>([])
const nextCursor = ref<string | null>(null)
const listLoading = ref(true)
const listLoadingMore = ref(false)
const listError = ref('')
const creating = ref(false)

const selectedOid = ref('')
const conversation = ref<AiConversation | null>(null)

const tabOptions = computed(() => [
  { value: 'active', label: t('ai.activeTab') },
  { value: 'archived', label: t('ai.archivedTab') }
])

function currentStatus(): AiConversationStatus {
  return tab.value === 'archived' ? 'archived' : 'active'
}

const modelShort = computed(() => {
  const model = conversation.value?.model
  if (!model) return t('ai.modelUnknown')
  return model.includes('/') ? model.slice(model.lastIndexOf('/') + 1) : model
})

const thinkingLabel = computed(() => {
  const value = conversation.value?.thinking
  if (value === 'low') return t('ai.thinkingLow')
  if (value === 'medium') return t('ai.thinkingMedium')
  if (value === 'high') return t('ai.thinkingHigh')
  return t('ai.thinkingDefault')
})

/* ------------------------------- 消息 ------------------------------- */

const messages = ref<AiMessage[]>([])
const messagesLoading = ref(false)
const messagesError = ref('')
const timelineEl = ref<HTMLElement | null>(null)
const stickToBottom = ref(true)
let messageGeneration = 0

const lastSeq = computed(() => {
  const last = messages.value[messages.value.length - 1]
  return last?.seq ?? 0
})

/* -------------------------------- run -------------------------------- */

const run = ref<AiRun | null>(null)
const liveAssistant = ref<AiLiveAssistant[]>([])
const liveTools = ref<AiLiveTool[]>([])
const activeRuns = ref<AiRun[]>([])
const cancelling = ref(false)
let runGeneration = 0
let streamAbort: AbortController | null = null

const runActive = computed(
  () => run.value !== null && (run.value.status === 'queued' || run.value.status === 'running')
)

const liveText = computed(() => {
  if (!liveAssistant.value.length) return ''
  const ordered = [...liveAssistant.value].sort((left, right) => left.seq - right.seq)
  return ordered[ordered.length - 1]?.content ?? ''
})

const liveToolList = computed(() =>
  [...liveTools.value].sort((left, right) => left.seq - right.seq)
)

const runStatusLabel = computed(() => {
  const status = run.value?.status
  if (status === 'queued') return t('ai.runQueued')
  if (status === 'running') return t('ai.runRunning')
  if (status === 'succeeded') return t('ai.runSucceeded')
  if (status === 'failed') return t('ai.runFailed')
  if (status === 'cancelled') return t('ai.runCancelled')
  if (status === 'interrupted') return t('ai.runInterrupted')
  return t('ai.runRunning')
})

/* ------------------------------- 提案 ------------------------------- */

const proposals = ref<AiProposal[]>([])
const proposalsLoading = ref(false)
const proposalWorkingOid = ref('')

const proposalOpen = ref(false)
const proposalTarget = ref<AiProposal | null>(null)
const totp = ref('')
const proposalBusy = ref(false)
const proposalError = ref('')

/* ------------------------------ 解锁门 ------------------------------ */

const unlockOpen = ref(false)
const unlockPassword = ref('')
const unlockBusy = ref(false)
const unlockError = ref('')
let afterUnlock: (() => void) | null = null

/* ------------------------------ 风险须知 ------------------------------ */

const acked = ref(true)
const ackOpen = ref(false)
const ackBusy = ref(false)

/* ----------------------------- 会话操作 ----------------------------- */

const renameOpen = ref(false)
const renameTitle = ref('')
const renameBusy = ref(false)
const renameError = ref('')

const archiving = ref(false)

const deleteOpen = ref(false)
const deleteBusy = ref(false)
const deleteError = ref('')

/* ----------------------------- 错误文案 ----------------------------- */

const AI_ERROR_KEYS: Record<string, string> = {
  AI_FORBIDDEN: 'ai.errors.forbidden',
  AI_RUN_CONFLICT: 'ai.errors.runConflict',
  AI_RUN_NOT_FOUND: 'ai.errors.runNotFound',
  AI_RUN_NOT_CANCELLABLE: 'ai.errors.runFinished',
  AI_CONVERSATION_NOT_FOUND: 'ai.errors.conversationNotFound',
  AI_SAFETY_REJECTED: 'ai.errors.safety',
  AI_AGENT_MODERATION_REJECTED: 'ai.errors.safety',
  AI_AGENT_PROPOSAL_CONFLICT: 'ai.errors.proposalConflict',
  AI_AGENT_PROPOSAL_EXPIRED: 'ai.errors.proposalExpired',
  AI_AGENT_PROPOSAL_TAMPERED: 'ai.errors.proposalTampered',
  AI_AGENT_TOTP_REQUIRED: 'ai.errors.totpRequired',
  AI_AGENT_TOTP_INVALID: 'ai.errors.totpInvalid',
  AI_AGENT_TOTP_USED: 'ai.errors.totpUsed',
  AI_AGENT_TOTP_RATE_LIMITED: 'ai.errors.totpRateLimited'
}

function errorText(cause: unknown): string {
  if (cause instanceof ApiError) {
    if (cause.code === 'AI_AGENT_UNLOCK_REQUIRED') return t('ai.unlockRequired')
    const key = AI_ERROR_KEYS[cause.code]
    if (key) return t(key)
    return cause.message || t('common.error')
  }
  return t('common.error')
}

/* ------------------------------ 格式化 ------------------------------ */

function formatTokens(value: number): string {
  return new Intl.NumberFormat().format(value)
}

function clip(text: string, max = 1600): string {
  const value = text ?? ''
  return value.length > max ? `${value.slice(0, max)}\n${t('ai.truncated')}` : value
}

function toolStatusLabel(status: AiLiveTool['status']): string {
  if (status === 'success') return t('ai.toolSuccess')
  if (status === 'error') return t('ai.toolError')
  return t('ai.toolPending')
}

/* --------------------------- 滚动与消息合并 --------------------------- */

async function scrollToBottom(force = false): Promise<void> {
  await nextTick()
  const element = timelineEl.value
  if (!element) return
  if (force || stickToBottom.value) element.scrollTop = element.scrollHeight
}

function onTimelineScroll(): void {
  const element = timelineEl.value
  if (!element) return
  stickToBottom.value = element.scrollHeight - element.scrollTop - element.clientHeight < 80
}

function mergeMessages(incoming: AiMessage[]): void {
  if (!incoming.length) return
  const map = new Map(messages.value.map((message) => [message.oid, message]))
  for (const message of incoming) map.set(message.oid, message)
  messages.value = [...map.values()].sort((left, right) => left.seq - right.seq)

  const committed = new Set<number>()
  for (const message of messages.value) {
    if (message.role === 'assistant' && message.round_no !== null) committed.add(message.round_no)
  }
  if (committed.size) {
    liveAssistant.value = liveAssistant.value.filter((item) => !committed.has(item.round_no))
  }
}

function mergeProposals(incoming: AiProposal[]): void {
  const pending = incoming.filter((proposal) => proposal.status === 'proposed')
  if (!pending.length) return
  const map = new Map(proposals.value.map((proposal) => [proposal.oid, proposal]))
  for (const proposal of pending) map.set(proposal.oid, proposal)
  proposals.value = [...map.values()]
}

/* ------------------------------ 数据加载 ------------------------------ */

async function loadConversations(reset = true): Promise<void> {
  if (reset) {
    listLoading.value = true
    listError.value = ''
    nextCursor.value = null
  } else {
    listLoadingMore.value = true
  }
  try {
    const page = await listConversations({
      status: currentStatus(),
      limit: 50,
      cursor: reset ? null : nextCursor.value
    })
    if (reset) {
      conversations.value = page.conversations
    } else {
      const known = new Set(conversations.value.map((item) => item.oid))
      conversations.value = [
        ...conversations.value,
        ...page.conversations.filter((item) => !known.has(item.oid))
      ]
    }
    nextCursor.value = page.next_cursor
    const current = conversations.value.find((item) => item.oid === selectedOid.value)
    if (current) conversation.value = current
  } catch (cause) {
    if (reset) listError.value = errorText(cause)
    else ui.notify(errorText(cause), 'danger')
  } finally {
    listLoading.value = false
    listLoadingMore.value = false
  }
}

async function refreshConversations(): Promise<void> {
  try {
    const page = await listConversations({ status: currentStatus(), limit: 50 })
    conversations.value = page.conversations
    nextCursor.value = page.next_cursor
    const current = page.conversations.find((item) => item.oid === selectedOid.value)
    if (current) conversation.value = current
  } catch {
    // 列表刷新失败保留旧数据，不打断正在进行的对话。
  }
}

async function loadMessages(oid: string): Promise<void> {
  const token = ++messageGeneration
  messagesLoading.value = true
  messagesError.value = ''
  try {
    let after = 0
    const collected: AiMessage[] = []
    for (let page = 0; page < 10; page += 1) {
      const result = await fetchMessages(oid, after, 100)
      if (token !== messageGeneration || selectedOid.value !== oid) return
      collected.push(...result.messages)
      const lastMessage = result.messages[result.messages.length - 1]
      const next = lastMessage?.seq ?? 0
      if (!result.has_more || next <= after) break
      after = next
    }
    if (token !== messageGeneration || selectedOid.value !== oid) return
    messages.value = collected.sort((left, right) => left.seq - right.seq)
  } catch (cause) {
    if (token === messageGeneration && selectedOid.value === oid) {
      messagesError.value = errorText(cause)
    }
  } finally {
    if (token === messageGeneration && selectedOid.value === oid) {
      messagesLoading.value = false
    }
  }
}

async function refreshMessagesAfter(oid: string, seq: number): Promise<void> {
  try {
    const page = await fetchMessages(oid, seq, 100)
    if (selectedOid.value === oid) mergeMessages(page.messages)
  } catch {
    // 增量失败不阻塞 run：终态收尾还会再拉一次。
  }
}

async function loadActiveRuns(): Promise<void> {
  try {
    activeRuns.value = await listActiveRuns()
  } catch {
    activeRuns.value = []
  }
}

async function loadProposals(): Promise<void> {
  proposalsLoading.value = true
  try {
    proposals.value = await listProposals('proposed', 20)
  } catch {
    // 提案是旁路：读取失败不阻塞对话。
  } finally {
    proposalsLoading.value = false
  }
}

async function selectConversation(oid: string): Promise<void> {
  if (!oid) return
  if (oid === selectedOid.value && conversation.value) return

  beginGeneration()
  run.value = null
  liveAssistant.value = []
  liveTools.value = []
  messages.value = []
  messagesError.value = ''
  draft.value = ''
  selectedOid.value = oid
  conversation.value = conversations.value.find((item) => item.oid === oid) ?? null
  stickToBottom.value = true

  try {
    if (!conversation.value) conversation.value = await fetchConversation(oid)
    await loadMessages(oid)
  } catch (cause) {
    messagesError.value = errorText(cause)
    return
  }

  const active = activeRuns.value.find((item) => item.conversation_oid === oid)
  if (active) void startRun(active.oid)
}

async function loadMore(): Promise<void> {
  if (!nextCursor.value || listLoadingMore.value) return
  await loadConversations(false)
}

async function createNew(): Promise<void> {
  if (creating.value) return
  creating.value = true
  try {
    const created = await createConversation()
    tab.value = 'active'
    conversations.value = [created, ...conversations.value.filter((item) => item.oid !== created.oid)]
    await selectConversation(created.oid)
  } catch (cause) {
    ui.notify(errorText(cause), 'danger')
  } finally {
    creating.value = false
  }
}

/* ------------------------------ run 流 ------------------------------ */

function runStatusOf(value: string): AiRunStatus {
  if (
    value === 'queued' ||
    value === 'running' ||
    value === 'succeeded' ||
    value === 'failed' ||
    value === 'cancelled' ||
    value === 'interrupted'
  ) {
    return value
  }
  return 'interrupted'
}

function placeholderRun(runId: string, conversationOid: string): AiRun {
  return {
    oid: runId,
    conversation_oid: conversationOid,
    status: 'queued',
    model: conversation.value?.model ?? null,
    stop_reason: null,
    rounds: 0,
    error_code: null,
    error_message: null,
    result_message_oid: null,
    prompt_tokens: 0,
    completion_tokens: 0,
    total_tokens: 0,
    cancel_requested: false,
    created_at: null,
    started_at: null,
    finished_at: null
  }
}

function liveToolOf(frame: AiStreamToolFrame): AiLiveTool {
  return {
    seq: frame.seq,
    round_no: frame.round_no,
    tool_call_id: frame.tool_call_id,
    tool_name: frame.tool_name,
    tool_kind: frame.tool_kind,
    status: frame.status,
    args_summary: frame.args_summary,
    result_summary: frame.result_summary
  }
}

function beginGeneration(): number {
  runGeneration += 1
  streamAbort?.abort()
  streamAbort = null
  return runGeneration
}

function applySnapshot(detail: AiRunDetail, live?: Partial<AiLiveState> | null): void {
  run.value = detail.run
  mergeMessages(detail.messages ?? [])
  if (detail.proposals?.length) mergeProposals(detail.proposals)
  if (live?.assistant) liveAssistant.value = [...live.assistant]
  if (live?.tools) liveTools.value = [...live.tools]
}

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => {
    window.setTimeout(resolve, ms)
  })
}

async function startRun(runId: string): Promise<void> {
  const oid = selectedOid.value
  if (!oid) return
  const generation = beginGeneration()
  run.value = placeholderRun(runId, oid)
  liveAssistant.value = []
  liveTools.value = []
  const controller = new AbortController()
  streamAbort = controller

  try {
    const result = await streamRunEvents(
      runId,
      {
        onSnapshot(frame) {
          if (generation !== runGeneration) return
          applySnapshot(frame.data, frame.live)
        },
        onAssistant(frame) {
          if (generation !== runGeneration) return
          const others = liveAssistant.value.filter(
            (item) => item.stream_key !== frame.stream_key
          )
          liveAssistant.value = [
            ...others,
            {
              seq: frame.seq,
              round_no: frame.round_no,
              stream_key: frame.stream_key,
              content: frame.content
            }
          ]
        },
        onTool(frame) {
          if (generation !== runGeneration) return
          const next = liveToolOf(frame)
          const keyOf = (item: AiLiveTool): string =>
            item.tool_call_id ?? `${item.round_no}:${item.tool_name ?? ''}`
          const key = keyOf(next)
          const current = liveTools.value.find((item) => keyOf(item) === key)
          liveTools.value = current
            ? liveTools.value.map((item) => (keyOf(item) === key ? next : item))
            : [...liveTools.value, next]
        },
        onTerminal(frame) {
          if (generation !== runGeneration) return
          if (run.value) run.value = { ...run.value, status: runStatusOf(frame.status) }
        },
        onError(frame) {
          if (generation !== runGeneration) return
          ui.notify(
            errorText(new ApiError({ message: frame.code, code: frame.code })),
            'danger'
          )
        }
      },
      controller.signal
    )

    if (generation !== runGeneration) return
    if (result.aborted) return
    if (!result.completed) {
      await pollRun(runId, oid, generation)
      return
    }
  } catch (cause) {
    if (generation !== runGeneration) return
    if (cause instanceof ApiError && cause.status === 401) return
    // SSE 不可用（多 worker 503、代理断流）：退回轮询，结论仍以后端为准。
    await pollRun(runId, oid, generation)
    return
  } finally {
    if (generation === runGeneration) streamAbort = null
  }

  if (generation === runGeneration) await finalizeRun(runId, oid, generation)
}

async function pollRun(runId: string, oid: string, generation: number): Promise<void> {
  let afterRound = 0
  let sinceSeq = lastSeq.value
  for (let tick = 0; tick < 600; tick += 1) {
    if (generation !== runGeneration || selectedOid.value !== oid) return
    try {
      const detail = await fetchRunDetail(runId, afterRound, sinceSeq)
      if (generation !== runGeneration || selectedOid.value !== oid) return
      run.value = detail.run
      mergeMessages(detail.messages ?? [])
      if (detail.proposals?.length) mergeProposals(detail.proposals)
      afterRound = Math.max(afterRound, ...detail.events.map((event) => event.round_no ?? 0))
      sinceSeq = lastSeq.value
      if (detail.run.status !== 'queued' && detail.run.status !== 'running') {
        await finalizeRun(runId, oid, generation)
        return
      }
    } catch (cause) {
      if (generation !== runGeneration) return
      if (cause instanceof ApiError && [401, 403, 404].includes(cause.status)) {
        ui.notify(errorText(cause), 'danger')
        return
      }
    }
    await delay(1500)
  }
  if (generation === runGeneration) {
    ui.notify(t('ai.pollTimeout'), 'danger')
    await finalizeRun(runId, oid, generation)
  }
}

async function finalizeRun(runId: string, oid: string, generation: number): Promise<void> {
  if (generation !== runGeneration || selectedOid.value !== oid) return
  try {
    const detail = await fetchRunDetail(runId, 0, lastSeq.value)
    if (generation !== runGeneration || selectedOid.value !== oid) return
    run.value = detail.run
    mergeMessages(detail.messages ?? [])
    if (detail.proposals?.length) mergeProposals(detail.proposals)
  } catch {
    // 收尾拉取失败不影响下面的列表刷新。
  }
  if (generation !== runGeneration || selectedOid.value !== oid) return

  await refreshMessagesAfter(oid, lastSeq.value)
  await Promise.all([refreshConversations(), loadProposals(), loadActiveRuns()])
  if (generation !== runGeneration) return

  const finished = run.value
  run.value = null
  liveAssistant.value = []
  liveTools.value = []
  streamAbort = null
  await scrollToBottom(true)

  if (finished?.status === 'failed') {
    ui.notify(
      finished.error_message ||
        errorText(new ApiError({ message: finished.error_code ?? '', code: finished.error_code ?? '' })),
      'danger'
    )
  } else if (finished?.status === 'interrupted') {
    ui.notify(t('ai.runInterrupted'), 'danger')
  }
}

/* ------------------------------ 发送与中止 ------------------------------ */

const draft = ref('')
const sending = ref(false)

async function send(): Promise<void> {
  if (!conversation.value || runActive.value || sending.value) return
  const content = draft.value.trim()
  if (!content) return
  if (!acked.value) {
    ackOpen.value = true
    return
  }
  const oid = conversation.value.oid
  sending.value = true
  try {
    const result = await submitRun(oid, content)
    if (selectedOid.value !== oid) return
    draft.value = ''
    await refreshMessagesAfter(oid, lastSeq.value)
    await scrollToBottom(true)
    void startRun(result.run_id)
  } catch (cause) {
    if (cause instanceof ApiError && cause.code === 'AI_AGENT_UNLOCK_REQUIRED') {
      askUnlock(() => {
        void send()
      })
      return
    }
    ui.notify(errorText(cause), 'danger')
  } finally {
    sending.value = false
  }
}

async function cancel(): Promise<void> {
  if (!run.value || cancelling.value) return
  cancelling.value = true
  try {
    await cancelRun(run.value.oid)
    ui.notify(t('ai.stopRequested'), 'neutral')
  } catch (cause) {
    ui.notify(errorText(cause), 'danger')
  } finally {
    cancelling.value = false
  }
}

/* ------------------------------ 会话操作 ------------------------------ */

function openRename(): void {
  renameTitle.value = conversation.value?.title ?? ''
  renameError.value = ''
  renameOpen.value = true
}

async function submitRename(): Promise<void> {
  if (!conversation.value || renameBusy.value) return
  const title = renameTitle.value.trim()
  if (!title) {
    renameError.value = t('ai.renameRequired')
    return
  }
  renameBusy.value = true
  try {
    const updated = await updateConversation(conversation.value.oid, { title })
    conversation.value = updated
    const index = conversations.value.findIndex((item) => item.oid === updated.oid)
    if (index >= 0) conversations.value[index] = updated
    renameOpen.value = false
    ui.notify(t('ai.renamed'), 'success')
  } catch (cause) {
    renameError.value = errorText(cause)
  } finally {
    renameBusy.value = false
  }
}

async function toggleArchive(): Promise<void> {
  if (!conversation.value || archiving.value) return
  archiving.value = true
  const next: AiConversationStatus = conversation.value.status === 'active' ? 'archived' : 'active'
  try {
    const updated = await updateConversation(conversation.value.oid, { status: next })
    conversation.value = updated
    const index = conversations.value.findIndex((item) => item.oid === updated.oid)
    if (index >= 0) conversations.value[index] = updated
    ui.notify(next === 'archived' ? t('ai.archivedNotice') : t('ai.unarchivedNotice'), 'success')
    void refreshConversations()
  } catch (cause) {
    ui.notify(errorText(cause), 'danger')
  } finally {
    archiving.value = false
  }
}

function openDelete(): void {
  deleteError.value = ''
  deleteOpen.value = true
}

async function submitDelete(): Promise<void> {
  if (!conversation.value || deleteBusy.value) return
  deleteBusy.value = true
  deleteError.value = ''
  try {
    await deleteConversation(conversation.value.oid)
    deleteOpen.value = false
    beginGeneration()
    conversation.value = null
    selectedOid.value = ''
    messages.value = []
    await refreshConversations()
    const next = conversations.value[0]
    if (next) await selectConversation(next.oid)
    ui.notify(t('ai.deleted'), 'success')
  } catch (cause) {
    deleteError.value = errorText(cause)
  } finally {
    deleteBusy.value = false
  }
}

/* ------------------------------ 提案处理 ------------------------------ */

function openProposal(proposal: AiProposal): void {
  proposalTarget.value = proposal
  totp.value = ''
  proposalError.value = ''
  proposalOpen.value = true
}

async function submitProposal(): Promise<void> {
  const target = proposalTarget.value
  if (!target || proposalBusy.value) return
  const needsCode = target.requires_dual_auth && !target.dual_auth_cached
  if (needsCode && !totp.value.trim()) {
    proposalError.value = t('ai.proposalTotpRequired')
    return
  }
  proposalBusy.value = true
  proposalError.value = ''
  try {
    await confirmProposal(target.oid, needsCode ? totp.value.trim() : undefined)
    proposals.value = proposals.value.filter((item) => item.oid !== target.oid)
    proposalOpen.value = false
    ui.notify(t('ai.proposalConfirmed'), 'success')
    void refreshConversations()
  } catch (cause) {
    if (cause instanceof ApiError && cause.code === 'AI_AGENT_UNLOCK_REQUIRED') {
      proposalOpen.value = false
      askUnlock(() => openProposal(target))
      return
    }
    proposalError.value = errorText(cause)
  } finally {
    proposalBusy.value = false
  }
}

async function onProposalReject(proposal: AiProposal): Promise<void> {
  if (proposalWorkingOid.value) return
  proposalWorkingOid.value = proposal.oid
  try {
    await rejectProposal(proposal.oid)
    proposals.value = proposals.value.filter((item) => item.oid !== proposal.oid)
    ui.notify(t('ai.proposalRejected'), 'success')
  } catch (cause) {
    if (cause instanceof ApiError && cause.code === 'AI_AGENT_UNLOCK_REQUIRED') {
      askUnlock(() => {
        void onProposalReject(proposal)
      })
      return
    }
    ui.notify(errorText(cause), 'danger')
  } finally {
    proposalWorkingOid.value = ''
  }
}

/* ------------------------------ 解锁与须知 ------------------------------ */

function askUnlock(action: () => void): void {
  afterUnlock = action
  unlockPassword.value = ''
  unlockError.value = ''
  unlockOpen.value = true
}

watch(unlockOpen, (open) => {
  if (!open) {
    unlockPassword.value = ''
    unlockError.value = ''
    afterUnlock = null
  }
})

async function submitUnlock(): Promise<void> {
  if (unlockBusy.value) return
  if (!unlockPassword.value) {
    unlockError.value = t('ai.unlockPasswordRequired')
    return
  }
  unlockBusy.value = true
  unlockError.value = ''
  try {
    await unlockAgent(unlockPassword.value)
    const action = afterUnlock
    afterUnlock = null
    unlockOpen.value = false
    ui.notify(t('ai.unlocked'), 'success')
    action?.()
  } catch (cause) {
    if (cause instanceof ApiError && cause.code === 'AI_AGENT_UNLOCK_FAILED') {
      unlockError.value = t('ai.unlockFailed')
    } else if (cause instanceof ApiError && cause.code === 'AI_AGENT_UNLOCK_LOCKED') {
      unlockError.value = t('ai.unlockLocked')
    } else {
      unlockError.value = errorText(cause)
    }
  } finally {
    unlockBusy.value = false
  }
}

async function loadAck(): Promise<void> {
  try {
    const result = await fetchRiskAck()
    acked.value = result.acked
    if (!result.acked) ackOpen.value = true
  } catch {
    // 读取失败不阻塞使用：ack 只是前端提示，后端不据此拦截 run。
  }
}

async function acknowledge(): Promise<void> {
  if (ackBusy.value) return
  ackBusy.value = true
  try {
    await ackRisk()
    acked.value = true
    ackOpen.value = false
    ui.notify(t('ai.riskAcked'), 'success')
  } catch (cause) {
    ui.notify(errorText(cause), 'danger')
  } finally {
    ackBusy.value = false
  }
}

function cancelRisk(): void {
  ackOpen.value = false
  void router.push({ name: 'home' })
}

/* ------------------------------- 生命周期 ------------------------------- */

watch(tab, () => {
  void loadConversations(true)
})

watch([() => messages.value.length, liveText], () => {
  void scrollToBottom()
})

onMounted(async () => {
  void loadAck()
  void loadProposals()
  await Promise.all([loadConversations(true), loadActiveRuns()])
  const target = initialSection || conversations.value[0]?.oid || ''
  if (target) await selectConversation(target)
})

onBeforeUnmount(() => {
  beginGeneration()
})
</script>

<template>
  <div class="page">
    <header class="head">
      <div>
        <p class="eyebrow">ZVAGENT</p>
        <h1 class="title">{{ t('ai.title') }}</h1>
        <p class="sub">{{ t('ai.subtitle') }}</p>
      </div>
      <ZButton variant="primary" size="sm" icon="plus" :loading="creating" @click="createNew">
        {{ t('ai.newConversation') }}
      </ZButton>
    </header>

    <div class="workspace">
      <aside class="sessions" :aria-label="t('ai.sessions')">
        <ZSegmented v-model="tab" :options="tabOptions" size="sm" :aria-label="t('ai.sessions')" />

        <div v-if="listLoading" class="session-skeletons">
          <div v-for="index in 5" :key="index" class="session-skeleton">
            <ZSkeleton width="72%" height="0.85rem" />
            <ZSkeleton width="46%" height="0.65rem" />
          </div>
        </div>

        <ZAlert v-else-if="listError" tone="danger" :description="listError">
          <div class="retry">
            <ZButton size="sm" icon="refresh" @click="loadConversations(true)">
              {{ t('common.retry') }}
            </ZButton>
          </div>
        </ZAlert>

        <div v-else-if="!conversations.length" class="sessions-empty">
          <p>{{ tab === 'archived' ? t('ai.archivedEmpty') : t('ai.noSessions') }}</p>
          <p class="hint">{{ t('ai.noSessionsHint') }}</p>
        </div>

        <ul v-else class="session-list">
          <li v-for="item in conversations" :key="item.oid">
            <button
              type="button"
              class="session"
              :class="{ 'is-active': item.oid === selectedOid }"
              @click="selectConversation(item.oid)"
            >
              <span class="session-title">{{ item.title || t('ai.untitled') }}</span>
              <span class="session-meta">
                <span class="num">{{ t('ai.messageCount', { n: item.message_count }) }}</span>
                <span aria-hidden="true">·</span>
                <span>{{ formatDateTime(item.updated_at) }}</span>
              </span>
            </button>
          </li>
        </ul>

        <ZButton
          v-if="nextCursor"
          size="sm"
          variant="ghost"
          block
          :loading="listLoadingMore"
          @click="loadMore"
        >
          {{ t('ai.loadMore') }}
        </ZButton>
      </aside>

      <section class="chat">
        <template v-if="conversation">
          <header class="chat-head">
            <div class="chat-titles">
              <div class="chat-title-line">
                <h2>{{ conversation.title || t('ai.untitled') }}</h2>
                <ZBadge v-if="conversation.status === 'archived'" tone="neutral">
                  {{ t('ai.archived') }}
                </ZBadge>
              </div>
              <div class="chips">
                <span class="chip" :title="conversation.model || ''">{{ modelShort }}</span>
                <span class="chip">{{ thinkingLabel }}</span>
                <span class="chip num">
                  {{
                    t('ai.contextUsage', {
                      used: formatTokens(conversation.context.tokens),
                      limit: formatTokens(conversation.context.limit)
                    })
                  }}
                </span>
              </div>
              <ZProgress :value="conversation.context.ratio * 100" :height="4" />
            </div>
            <div class="chat-actions">
              <ZButton size="sm" variant="quiet" icon="pencil" @click="openRename">
                {{ t('ai.rename') }}
              </ZButton>
              <ZButton size="sm" variant="quiet" :loading="archiving" @click="toggleArchive">
                {{ conversation.status === 'active' ? t('ai.archive') : t('ai.unarchive') }}
              </ZButton>
              <ZButton size="sm" variant="quiet" icon="x" @click="openDelete">
                {{ t('ai.remove') }}
              </ZButton>
            </div>
          </header>

          <div ref="timelineEl" class="timeline" @scroll="onTimelineScroll">
            <div v-if="messagesLoading" class="timeline-loading">
              <div v-for="index in 3" :key="index" class="timeline-loading-row">
                <ZSkeleton width="38%" height="0.9rem" />
                <ZSkeleton width="86%" height="0.9rem" />
              </div>
            </div>

            <ZAlert v-else-if="messagesError" tone="danger" :description="messagesError">
              <div class="retry">
                <ZButton size="sm" icon="refresh" @click="loadMessages(conversation.oid)">
                  {{ t('common.retry') }}
                </ZButton>
              </div>
            </ZAlert>

            <template v-else>
              <ZEmpty
                v-if="!messages.length && !runActive && !liveText"
                compact
                icon="sparkles"
                :title="t('ai.emptyConversation')"
                :description="t('ai.emptyConversationHint')"
              />

              <article
                v-for="message in messages"
                :key="message.oid"
                class="msg"
                :class="`is-${message.role}`"
              >
                <template v-if="message.role === 'user'">
                  <div class="bubble user">{{ message.content }}</div>
                  <time class="msg-time">{{ formatDateTime(message.created_at) }}</time>
                </template>

                <template v-else-if="message.role === 'assistant'">
                  <div class="msg-body">
                    <ZMarkdown :source="message.content" />
                    <details v-if="message.reasoning" class="reasoning">
                      <summary>{{ t('ai.reasoning') }}</summary>
                      <p>{{ message.reasoning }}</p>
                    </details>
                    <div v-if="message.round_no !== null || message.usage" class="msg-foot">
                      <span v-if="message.round_no !== null">
                        {{ t('ai.round', { n: message.round_no }) }}
                      </span>
                      <span v-if="message.usage?.total_tokens" class="num">
                        {{ t('ai.tokenCount', { n: formatTokens(message.usage.total_tokens) }) }}
                      </span>
                    </div>
                  </div>
                </template>

                <template v-else-if="message.role === 'tool'">
                  <details class="tool-msg">
                    <summary>
                      <span class="tool-name">{{ message.tool_name || t('ai.toolFallback') }}</span>
                      <span class="tool-hint">{{ t('ai.toolResult') }}</span>
                    </summary>
                    <pre>{{ clip(message.content) }}</pre>
                  </details>
                </template>

                <p v-else class="system-msg">{{ message.content }}</p>
              </article>
            </template>

            <div v-if="runActive || liveText" class="live">
              <div class="live-head">
                <span class="pulse" aria-hidden="true" />
                <span>{{ runStatusLabel }}</span>
                <span v-if="run?.rounds" class="num">
                  {{ t('ai.roundsDone', { n: run.rounds }) }}
                </span>
                <span class="grow" />
                <ZButton
                  v-if="runActive"
                  size="sm"
                  variant="ghost"
                  :loading="cancelling"
                  @click="cancel"
                >
                  {{ t('ai.stop') }}
                </ZButton>
              </div>

              <ul v-if="liveToolList.length" class="live-tools">
                <li
                  v-for="tool in liveToolList"
                  :key="`${tool.round_no}-${tool.tool_call_id || tool.tool_name}`"
                  :class="`tool-${tool.status}`"
                >
                  <span class="tool-dot" aria-hidden="true" />
                  <span class="tool-name">{{ tool.tool_name || t('ai.toolFallback') }}</span>
                  <ZBadge v-if="tool.tool_kind === 'write'" tone="accent">
                    {{ t('ai.toolKindWrite') }}
                  </ZBadge>
                  <span class="tool-status">{{ toolStatusLabel(tool.status) }}</span>
                </li>
              </ul>

              <div v-if="liveText" class="live-answer">
                <ZMarkdown :source="liveText" />
              </div>
            </div>
          </div>

          <section v-if="proposalsLoading || proposals.length" class="proposals">
            <header class="proposals-head">
              <h3>
                {{ t('ai.proposals') }}
                <span v-if="proposals.length" class="num">({{ proposals.length }})</span>
              </h3>
              <p>{{ t('ai.proposalsHint') }}</p>
            </header>
            <div v-if="proposalsLoading && !proposals.length" class="proposal-skeleton">
              <ZSkeleton width="60%" height="0.85rem" />
              <ZSkeleton width="90%" height="0.85rem" />
            </div>
            <AgentProposalCard
              v-for="proposal in proposals"
              :key="proposal.oid"
              :proposal="proposal"
              :busy="proposalWorkingOid === proposal.oid"
              @confirm="openProposal"
              @reject="onProposalReject"
            />
          </section>

          <form class="composer" @submit.prevent="send">
            <textarea
              v-model="draft"
              class="input"
              rows="3"
              :placeholder="runActive ? t('ai.inputPlaceholderRunning') : t('ai.inputPlaceholder')"
              :disabled="runActive || sending"
              @keydown.enter.exact.prevent="send"
            />
            <div class="composer-foot">
              <span class="composer-hint">{{ t('ai.composerHint') }}</span>
              <ZButton
                v-if="runActive"
                variant="danger"
                size="sm"
                :loading="cancelling"
                @click="cancel"
              >
                {{ t('ai.stop') }}
              </ZButton>
              <ZButton
                v-else
                type="submit"
                variant="primary"
                size="sm"
                icon="arrow-up-right"
                :loading="sending"
                :disabled="!draft.trim()"
              >
                {{ t('ai.send') }}
              </ZButton>
            </div>
          </form>
        </template>

        <div v-else class="chat-blank">
          <ZEmpty
            icon="sparkles"
            :title="t('ai.selectFirst')"
            :description="t('ai.selectFirstHint')"
          >
            <ZButton variant="primary" size="sm" icon="plus" :loading="creating" @click="createNew">
              {{ t('ai.newConversation') }}
            </ZButton>
          </ZEmpty>
        </div>
      </section>
    </div>

    <ZModal v-model="ackOpen" :title="t('ai.riskTitle')" size="md" hide-close>
      <div class="risk">
        <p>{{ t('ai.riskPower') }}</p>
        <p>{{ t('ai.riskBan') }}</p>
        <p>{{ t('ai.riskAudit') }}</p>
      </div>
      <template #footer>
        <ZButton variant="ghost" size="sm" :disabled="ackBusy" @click="cancelRisk">
          {{ t('ai.riskCancel') }}
        </ZButton>
        <ZButton variant="primary" size="sm" :loading="ackBusy" @click="acknowledge">
          {{ t('ai.riskAck') }}
        </ZButton>
      </template>
    </ZModal>

    <ZModal v-model="unlockOpen" :title="t('ai.unlockTitle')" :description="t('ai.unlockDesc')" size="sm">
      <div class="modal-form">
        <ZField
          v-model="unlockPassword"
          type="password"
          allow-reveal
          :label="t('ai.unlockPassword')"
          :disabled="unlockBusy"
          autocomplete="current-password"
          @enter="submitUnlock"
        />
        <ZAlert v-if="unlockError" tone="danger" :description="unlockError" />
      </div>
      <template #footer>
        <ZButton variant="ghost" size="sm" :disabled="unlockBusy" @click="unlockOpen = false">
          {{ t('common.cancel') }}
        </ZButton>
        <ZButton variant="primary" size="sm" :loading="unlockBusy" @click="submitUnlock">
          {{ t('ai.unlockSubmit') }}
        </ZButton>
      </template>
    </ZModal>

    <ZModal v-model="renameOpen" :title="t('ai.renameTitle')" size="sm">
      <div class="modal-form">
        <ZField
          v-model="renameTitle"
          :label="t('ai.renameTitle')"
          :placeholder="t('ai.renamePlaceholder')"
          :disabled="renameBusy"
          :error="renameError"
          @enter="submitRename"
        />
      </div>
      <template #footer>
        <ZButton variant="ghost" size="sm" :disabled="renameBusy" @click="renameOpen = false">
          {{ t('common.cancel') }}
        </ZButton>
        <ZButton variant="primary" size="sm" :loading="renameBusy" @click="submitRename">
          {{ t('common.save') }}
        </ZButton>
      </template>
    </ZModal>

    <ZModal v-model="deleteOpen" :title="t('ai.deleteTitle')" :description="t('ai.deleteDesc')" size="sm">
      <ZAlert v-if="deleteError" tone="danger" :description="deleteError" />
      <template #footer>
        <ZButton variant="ghost" size="sm" :disabled="deleteBusy" @click="deleteOpen = false">
          {{ t('common.cancel') }}
        </ZButton>
        <ZButton variant="danger" size="sm" :loading="deleteBusy" @click="submitDelete">
          {{ t('ai.remove') }}
        </ZButton>
      </template>
    </ZModal>

    <ZModal
      v-model="proposalOpen"
      :title="t('ai.proposalConfirmTitle')"
      :description="t('ai.proposalConfirmDesc')"
      size="sm"
    >
      <div class="modal-form">
        <p v-if="proposalTarget?.summary" class="confirm-text">{{ proposalTarget.summary }}</p>
        <ZField
          v-if="proposalTarget?.requires_dual_auth && !proposalTarget?.dual_auth_cached"
          v-model="totp"
          :label="t('ai.proposalTotp')"
          :hint="t('ai.proposalTotpHint')"
          inputmode="numeric"
          :disabled="proposalBusy"
          @enter="submitProposal"
        />
        <ZAlert v-if="proposalError" tone="danger" :description="proposalError" />
      </div>
      <template #footer>
        <ZButton variant="ghost" size="sm" :disabled="proposalBusy" @click="proposalOpen = false">
          {{ t('common.cancel') }}
        </ZButton>
        <ZButton variant="primary" size="sm" :loading="proposalBusy" @click="submitProposal">
          {{ t('ai.proposalConfirm') }}
        </ZButton>
      </template>
    </ZModal>
  </div>
</template>

<style scoped>
.page {
  display: flex;
  flex-direction: column;
  gap: var(--space-5);
}

.head {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: var(--space-4);
  flex-wrap: wrap;
}

.title {
  font-size: var(--text-3xl);
  font-weight: 500;
}

.sub {
  margin-top: var(--space-2);
  max-width: 62ch;
  color: var(--ink-muted);
}

.workspace {
  display: grid;
  grid-template-columns: 280px minmax(0, 1fr);
  gap: var(--space-4);
  align-items: stretch;
}

.sessions,
.chat {
  min-width: 0;
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
  background: var(--surface);
  box-shadow: var(--shadow-xs);
}

.sessions {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  height: clamp(560px, calc(100dvh - 300px), 880px);
  padding: var(--space-4);
  overflow: hidden;
}

.session-skeletons,
.session-skeleton {
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
}

.session-skeleton {
  padding: 0.55rem 0.3rem;
}

.retry {
  margin-top: var(--space-2);
}

.sessions-empty {
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
  padding: var(--space-6) var(--space-3);
  color: var(--ink-muted);
  font-size: var(--text-sm);
}

.sessions-empty .hint {
  font-size: var(--text-xs);
  color: var(--ink-faint);
  line-height: 1.6;
}

.session-list {
  list-style: none;
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 0.2rem;
  margin: 0;
  padding: 0;
  overflow-y: auto;
}

.session {
  display: flex;
  flex-direction: column;
  gap: 0.18rem;
  width: 100%;
  padding: 0.58rem 0.68rem;
  border-radius: var(--radius-sm);
  color: var(--ink-soft);
  text-align: left;
  transition:
    background-color var(--dur-fast) var(--ease-out),
    color var(--dur-fast) var(--ease-out);
}

.session:hover {
  background: var(--surface-sunken);
}

.session.is-active {
  background: var(--accent-soft);
  color: var(--ink);
}

.session-title {
  overflow: hidden;
  font-size: var(--text-sm);
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.session-meta {
  display: flex;
  gap: 0.3rem;
  overflow: hidden;
  font-size: var(--text-2xs);
  color: var(--ink-faint);
  white-space: nowrap;
}

.chat {
  display: flex;
  flex-direction: column;
  height: clamp(560px, calc(100dvh - 300px), 880px);
  overflow: hidden;
}

.chat-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-4);
  flex-wrap: wrap;
  padding: var(--space-4) var(--space-5);
  border-bottom: 1px solid var(--line);
}

.chat-titles {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 0.45rem;
  min-width: 0;
}

.chat-title-line {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  flex-wrap: wrap;
}

.chat-title-line h2 {
  font-size: var(--text-xl);
  font-weight: 600;
  overflow-wrap: anywhere;
}

.chips {
  display: flex;
  flex-wrap: wrap;
  gap: 0.35rem;
}

.chip {
  padding: 0.14rem 0.5rem;
  border: 1px solid var(--line);
  border-radius: var(--radius-pill);
  background: var(--surface-sunken);
  font-size: var(--text-2xs);
  color: var(--ink-muted);
}

.chat-actions {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-1);
}

.timeline {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: var(--space-5);
  min-height: 0;
  padding: var(--space-5);
  overflow-y: auto;
  background: color-mix(in srgb, var(--surface) 88%, var(--bg));
}

.timeline-loading,
.timeline-loading-row {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.msg {
  display: flex;
  flex-direction: column;
  gap: 0.32rem;
  max-width: 100%;
}

.msg.is-user {
  align-items: flex-end;
}

.bubble.user {
  max-width: min(80%, 46rem);
  padding: 0.68rem 1rem;
  border: 1px solid color-mix(in srgb, var(--accent) 18%, var(--line));
  border-radius: var(--radius-lg) var(--radius-lg) var(--radius-xs) var(--radius-lg);
  background: var(--accent-soft);
  color: var(--ink);
  white-space: pre-wrap;
  word-break: break-word;
}

.msg-time {
  font-size: var(--text-2xs);
  color: var(--ink-faint);
}

.msg-body {
  max-width: min(92%, 54rem);
  padding: 0.85rem 1rem;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  background: var(--surface-raised);
  box-shadow: var(--shadow-xs);
}

.reasoning {
  margin-top: 0.6rem;
  font-size: var(--text-xs);
  color: var(--ink-muted);
}

.reasoning summary {
  cursor: pointer;
}

.reasoning p {
  margin-top: 0.4rem;
  line-height: 1.65;
  white-space: pre-wrap;
}

.msg-foot {
  display: flex;
  gap: 0.6rem;
  margin-top: 0.5rem;
  font-size: var(--text-2xs);
  color: var(--ink-faint);
}

.tool-msg {
  max-width: min(92%, 54rem);
  padding: 0.5rem 0.7rem;
  border: 1px dashed var(--line-strong);
  border-radius: var(--radius-sm);
  background: var(--surface-sunken);
  font-size: var(--text-xs);
  color: var(--ink-muted);
}

.tool-msg summary {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  cursor: pointer;
}

.tool-msg pre {
  max-height: 240px;
  margin: 0.5rem 0 0;
  overflow: auto;
  font-family: var(--font-mono);
  font-size: var(--text-2xs);
  white-space: pre-wrap;
  word-break: break-all;
}

.tool-name {
  font-family: var(--font-mono);
  color: var(--ink-soft);
}

.tool-hint {
  color: var(--ink-faint);
}

.system-msg {
  font-size: var(--text-xs);
  font-style: italic;
  color: var(--ink-faint);
}

.live {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding: var(--space-4);
  border: 1px solid color-mix(in srgb, var(--accent) 18%, var(--line));
  border-radius: var(--radius-md);
  background: color-mix(in srgb, var(--accent-soft) 26%, var(--surface));
}

.live-head {
  display: flex;
  align-items: center;
  gap: 0.55rem;
  flex-wrap: wrap;
  font-size: var(--text-sm);
  color: var(--ink-soft);
}

.pulse {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--accent);
  animation: pulse 1.5s var(--ease-in-out) infinite;
}

@keyframes pulse {
  0%,
  100% {
    opacity: 0.45;
    transform: scale(0.85);
  }
  50% {
    opacity: 1;
    transform: scale(1);
  }
}

.live-tools {
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
  margin: 0;
  padding: 0;
  list-style: none;
}

.live-tools li {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-size: var(--text-xs);
  color: var(--ink-muted);
}

.tool-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--ink-faint);
}

.tool-pending .tool-dot {
  background: var(--accent);
}

.tool-success .tool-dot {
  background: var(--c-success);
}

.tool-error .tool-dot {
  background: var(--c-danger);
}

.tool-status {
  color: var(--ink-faint);
}

.live-answer {
  padding: 0.6rem 0.8rem;
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  background: var(--surface-raised);
}

.proposals {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  max-height: 300px;
  padding: var(--space-4) var(--space-5);
  overflow-y: auto;
  border-top: 1px solid var(--line);
  background: var(--surface-sunken);
}

.proposals-head h3 {
  font-family: var(--font-sans);
  font-size: var(--text-sm);
  font-weight: 650;
}

.proposals-head p {
  margin-top: 0.18rem;
  font-size: var(--text-2xs);
  color: var(--ink-faint);
}

.proposal-skeleton {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  padding: var(--space-3);
}

.composer {
  display: flex;
  flex-direction: column;
  gap: 0.6rem;
  padding: var(--space-4) var(--space-5);
  border-top: 1px solid var(--line);
  background: var(--surface);
}

.input {
  width: 100%;
  min-height: 76px;
  max-height: 220px;
  padding: 0.75rem 0.95rem;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  background: var(--surface-raised);
  color: var(--ink);
  font-family: var(--font-sans);
  font-size: var(--text-md);
  line-height: 1.6;
  resize: vertical;
  box-shadow: var(--shadow-xs);
}

.input:focus {
  outline: none;
  border-color: color-mix(in srgb, var(--accent) 55%, var(--line));
  box-shadow: 0 0 0 4px color-mix(in srgb, var(--accent) 14%, transparent);
}

.input:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.composer-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
}

.composer-hint {
  font-size: var(--text-2xs);
  color: var(--ink-faint);
}

.chat-blank {
  display: grid;
  flex: 1;
  place-items: center;
  padding: var(--space-8);
}

.risk {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  font-size: var(--text-sm);
  line-height: 1.72;
  color: var(--ink-soft);
}

.risk p {
  padding-left: 0.9rem;
  border-left: 2px solid color-mix(in srgb, var(--accent) 40%, var(--line));
}

.modal-form {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.confirm-text {
  font-size: var(--text-sm);
  line-height: 1.7;
  color: var(--ink-muted);
}

@media (max-width: 980px) {
  .workspace {
    grid-template-columns: 1fr;
  }

  .sessions {
    height: auto;
    overflow: visible;
  }

  .session-list {
    flex-direction: row;
    overflow-x: auto;
    padding-bottom: 0.2rem;
  }

  .session-list li {
    flex: 0 0 auto;
  }

  .session {
    min-width: 210px;
  }

  .chat {
    height: clamp(520px, calc(100dvh - 300px), 820px);
  }
}

@media (max-width: 680px) {
  .chat {
    height: auto;
    min-height: 520px;
  }

  .timeline {
    min-height: 320px;
    max-height: 58vh;
  }

  .chat-head,
  .composer {
    padding: var(--space-4);
  }

  .chat-actions {
    width: 100%;
  }

  .bubble.user {
    max-width: 92%;
  }

  .msg-body,
  .tool-msg {
    max-width: 100%;
  }

  .composer-foot {
    flex-wrap: wrap;
  }
}
</style>
