<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  ElButton,
  ElCard,
  ElDialog,
  ElDrawer,
  ElEmpty,
  ElIcon,
  ElInput,
  ElMessage,
  ElMessageBox,
  ElOption,
  ElSelect,
  ElTag
} from 'element-plus'
import { Menu, Promotion, VideoPause, WarningFilled } from '@element-plus/icons-vue'
import { useWindowSize } from '@vueuse/core'
import {
  AiRequestError,
  aiErrorCopy,
  aiErrorSection,
  confirmProposal,
  fetchPendingProposals,
  fetchRunDetail,
  listModels,
  rejectProposal
} from '@/api/ai'
import { useAiConversationsStore } from '@/stores/aiConversations'
import ZSystemAuthDialog from '@/components/system/ZSystemAuthDialog.vue'
import type { AiConversation, AiMessage, AiModelItem } from '@/../types/ai'
import ZAiProposalCard from './ZAiProposalCard.vue'
import ZAiSessionList from './ZAiSessionList.vue'
import { contextPercent } from './conversationSession'
import {
  eventStatusTone,
  markDualAuthCached,
  normalizeEventViews,
  normalizeProposals,
  toolLabelKey,
  type AgentEventView,
  type AgentProposalCard,
  type AgentProposalState
} from './agentSession'

const { t } = useI18n()
const { width, height } = useWindowSize()
const store = useAiConversationsStore()

/** 提案有效期的刷新间隔：后端按 10 分钟计，30 秒刷一次足够。 */
const PROPOSAL_TICK_MS = 30_000

/** 侧栏折叠成抽屉的断点：窄屏放不下固定侧栏。 */
const SIDEBAR_BREAKPOINT = 1024

const isNarrow = computed(() => width.value < SIDEBAR_BREAKPOINT)

const models = ref<AiModelItem[]>([])
const modelsLoading = ref(false)
const modelsFailed = ref(false)
const model = ref('')

const input = ref('')
const sending = ref(false)
const cooldown = ref(0)

/** 移动端抽屉：会话栏收进去。 */
const drawerOpen = ref(false)

/** 重命名弹层。 */
const renameVisible = ref(false)
const renameOid = ref('')
const renameTitle = ref('')
const renaming = ref(false)

/** 本地暂存的用户消息：服务端落库前先显示，不让界面空等。 */
const pendingUser = ref('')

/** 本轮出错的提示（提交或中止失败时展示）。 */
const chatError = ref<{ code: string; title: string; hint: string } | null>(null)

/** 待确认提案：进页面拉一次；刷新页面后仍能看到没处理的写操作。 */
const pendingProposals = ref<AgentProposalCard[]>([])
const pendingLoading = ref(false)
const pendingFailed = ref(false)
const pendingOpen = ref(false)

/** 本次 run 的事件与提案（轮询增量合并而来）。 */
const expandedEvents = ref<string[]>([])
const proposalCards = ref<AgentProposalCard[]>([])

/** 回看某次历史运行的工具留痕。 */
const replays = ref<Record<string, AgentEventView[]>>({})
const replayLoading = ref<Record<string, boolean>>({})
const replayFailed = ref<Record<string, boolean>>({})

/** 双人动态码弹窗（复用系统页那一个，运维写操作要另一位管理员放行）。 */
const authRef = ref<InstanceType<typeof ZSystemAuthDialog>>()

let cooldownTimer: ReturnType<typeof setInterval> | null = null
let proposalTimer: ReturnType<typeof setInterval> | null = null

/** 提案倒计时用的「当前时间」：只在有 pending 提案时按 30 秒推进。 */
const proposalNow = ref(Date.now())

/**
 * 输入框行数：按视口高度给书写空间。
 *
 * 高屏（竖屏平板、桌面）多给两行，输入框不再是一行小框；
 * 矮屏（横屏平板、横屏手机）少两行，输入区才挤不掉消息区。
 */
const composerRows = computed(() =>
  height.value >= 900 ? { minRows: 4, maxRows: 12 } : { minRows: 3, maxRows: 8 }
)

const hasModels = computed(() => models.value.length > 0)

/** 有活跃 run 时不能再提交：同一会话同时只允许一个后台任务。 */
const runActive = computed(
  () => store.activeRun !== null && (store.activeRun.status === 'queued' || store.activeRun.status === 'running')
)
const canSend = computed(
  () =>
    hasModels.value &&
    Boolean(model.value) &&
    !sending.value &&
    cooldown.value <= 0 &&
    Boolean(store.current) &&
    !runActive.value
)

/** 待确认提案数：会话内的提案 + 顶部待确认入口的提案。 */
const pendingCount = computed(
  () =>
    pendingProposals.value.filter((card) => card.state === 'pending').length +
    proposalCards.value.filter((card) => card.state === 'pending').length
)

/** 上下文水位。 */
const contextTokens = computed(() => store.current?.context.tokens ?? 0)
const contextLimit = computed(() => store.current?.context.limit ?? 0)
const contextPercentValue = computed(() => contextPercent(store.contextRatio))
const contextWarn = computed(() => store.contextWarning)

/** 消息行：普通消息与「压缩点」分隔线混排。 */
interface MessageRow {
  key: string
  kind: 'message' | 'compaction'
  message?: AiMessage
  compacted?: number
}

const messageRows = computed<MessageRow[]>(() => {
  const upto = store.compactedCount
  const rows: MessageRow[] = []
  let dividerPlaced = false
  for (const message of store.orderedMessages) {
    if (!dividerPlaced && upto > 0 && message.seq > upto) {
      rows.push({ key: 'compaction', kind: 'compaction', compacted: upto })
      dividerPlaced = true
    }
    rows.push({ key: message.oid, kind: 'message', message })
  }
  if (!dividerPlaced && upto > 0 && store.orderedMessages.length > 0) {
    rows.push({ key: 'compaction', kind: 'compaction', compacted: upto })
  }
  return rows
})

/** 列表空态：先说清是模型没就绪还是还没开始聊。 */
const emptyDescription = computed(() => {
  if (modelsFailed.value) {
    return t('ai.chat.modelsLoadFailed')
  }
  if (!hasModels.value) {
    return t('ai.chat.modelsEmpty')
  }
  return t('ai.agent.emptyHint')
})

/** run 的终止原因：正常完成不算提示。 */
const runStopReason = computed(() => {
  const reason = store.activeRun?.stop_reason ?? null
  if (!reason || reason === 'completed') {
    return null
  }
  return reason
})

/** run 失败时的错误码与人话说明。 */
const runErrorCopy = computed(() => {
  const code = store.activeRun?.error_code ?? null
  if (!code) {
    return null
  }
  const copy = aiErrorCopy(code, t)
  return { code, title: copy.title, hint: copy.hint }
})

/** 事件去重键：同一轮里同一个工具的同一条留痕只渲染一次。 */
function eventKey(event: {
  round_no: number
  tool_name: string | null
  result_summary: string
}): string {
  return `${event.round_no}|${event.tool_name ?? ''}|${event.result_summary}`
}

/** run 的事件视图：展开态存在这里，轮询回来也不会被冲掉。 */
const runEventViews = computed<AgentEventView[]>(() =>
  store.runEvents.map((event) => ({
    ...event,
    expanded: expandedEvents.value.includes(eventKey(event))
  }))
)

/** 工具消息的展开态。 */
const expandedTools = ref<string[]>([])

/* ---------------------------------------------------------------------------
   基础数据
   --------------------------------------------------------------------------- */

async function loadModels() {
  modelsLoading.value = true
  modelsFailed.value = false
  try {
    const result = await listModels()
    const list = Array.isArray(result?.data) ? result.data : []
    models.value = list
    if (!model.value && list.length > 0) {
      model.value = list[0]!.id
    }
    if (list.length === 0) {
      model.value = ''
    }
  } catch {
    models.value = []
    modelsFailed.value = true
  } finally {
    modelsLoading.value = false
  }
}

function startCooldown(seconds: number) {
  cooldown.value = Math.max(1, Math.ceil(seconds))
  if (cooldownTimer) {
    clearInterval(cooldownTimer)
  }
  cooldownTimer = setInterval(() => {
    cooldown.value -= 1
    if (cooldown.value <= 0) {
      cooldown.value = 0
      if (cooldownTimer) {
        clearInterval(cooldownTimer)
        cooldownTimer = null
      }
    }
  }, 1000)
}

function stopCooldown() {
  if (cooldownTimer) {
    clearInterval(cooldownTimer)
    cooldownTimer = null
  }
  cooldown.value = 0
}

/* ---------------------------------------------------------------------------
   会话
   --------------------------------------------------------------------------- */

/**
 * 确保有一个可用的智能体会话：取最近的一个，没有就建一个。
 *
 * 对话模式已下线：历史的 chat 会话保留在库里，但不再出现在界面、也无法继续。
 */
async function ensureConversation(): Promise<void> {
  const existing = store.conversations.find(
    (item) => item.mode === 'agent' && item.status === 'active'
  )
  if (existing) {
    await store.selectConversation(existing.oid)
    return
  }
  try {
    const created = await store.createConversation('agent')
    await store.selectConversation(created.oid)
  } catch {
    ElMessage.error(t('ai.chat.sessions.createFailed'))
  }
}

async function selectSession(oid: string) {
  await store.selectConversation(oid)
  drawerOpen.value = false
  void nextTick(scrollToBottom)
}

/** 新建会话：永远新建一个，不复用已有的（复用是「切换」的事）。 */
async function createSession() {
  try {
    const created = await store.createConversation('agent')
    await store.selectConversation(created.oid)
  } catch {
    ElMessage.error(t('ai.chat.sessions.createFailed'))
  }
  drawerOpen.value = false
}

function openRename(oid: string) {
  renameOid.value = oid
  renameTitle.value = store.conversations.find((item) => item.oid === oid)?.title ?? ''
  renameVisible.value = true
}

async function submitRename() {
  const title = renameTitle.value.trim()
  if (!title || renaming.value) {
    return
  }
  renaming.value = true
  try {
    await store.renameConversation(renameOid.value, title)
    renameVisible.value = false
    ElMessage.success(t('ai.chat.sessions.renamed'))
  } catch (error) {
    applyError(error)
  } finally {
    renaming.value = false
  }
}

async function archiveSession(oid: string) {
  try {
    await store.setConversationStatus(oid, 'archived')
    ElMessage.success(t('ai.chat.sessions.archivedNotice'))
    if (store.currentOid === oid) {
      await ensureConversation()
    }
  } catch (error) {
    applyError(error)
  }
}

async function removeSession(oid: string) {
  try {
    await ElMessageBox.confirm(
      t('ai.chat.sessions.deleteConfirm'),
      t('ai.chat.sessions.deleteConfirmTitle'),
      {
        type: 'warning',
        confirmButtonText: t('ai.common.delete'),
        cancelButtonText: t('ai.common.cancel'),
        confirmButtonClass: 'el-button--danger',
        closeOnClickModal: false
      }
    )
  } catch {
    return
  }
  try {
    await store.removeConversation(oid)
    ElMessage.success(t('ai.chat.sessions.deletedNotice'))
    await ensureConversation()
  } catch (error) {
    applyError(error)
  }
}

/* ---------------------------------------------------------------------------
   提案
   --------------------------------------------------------------------------- */

/**
 * 拉本人待确认的提案。
 *
 * 页面刷新后对话记录没了，但没处理的写操作还在服务端——靠这个入口找回来。
 */
async function loadPendingProposals() {
  pendingLoading.value = true
  pendingFailed.value = false
  try {
    const result = await fetchPendingProposals('proposed')
    pendingProposals.value = normalizeProposals(result?.proposals)
  } catch {
    pendingProposals.value = []
    pendingFailed.value = true
  } finally {
    pendingLoading.value = false
    syncProposalTimer()
  }
}

function pendingProposalCount(): number {
  return pendingCount.value
}

// 有待确认提案才开定时器：没有提案时页面不该常驻一个空转的 interval。
function syncProposalTimer() {
  const active = pendingProposalCount() > 0
  if (active && !proposalTimer) {
    proposalNow.value = Date.now()
    proposalTimer = setInterval(() => {
      proposalNow.value = Date.now()
    }, PROPOSAL_TICK_MS)
  } else if (!active && proposalTimer) {
    clearInterval(proposalTimer)
    proposalTimer = null
  }
}

function stopProposalTimer() {
  if (proposalTimer) {
    clearInterval(proposalTimer)
    proposalTimer = null
  }
}

/** 提案错误的文案：已知错误码沿用全局映射，未知的按状态码给明确说法。 */
function proposalErrorCopy(code: string | null, status: number | null) {
  if (code === 'AI_AGENT_TOTP_REQUIRED') {
    return {
      title: t('ai.agent.proposalErrors.totpRequired'),
      hint: t('ai.agent.proposalErrors.totpRequiredHint')
    }
  }
  if (aiErrorSection(code) !== 'UNKNOWN') {
    return aiErrorCopy(code, t)
  }
  switch (status) {
    case 403:
      return {
        title: t('ai.agent.proposalErrors.forbidden'),
        hint: t('ai.agent.proposalErrors.forbiddenHint')
      }
    case 409:
      return {
        title: t('ai.agent.proposalErrors.conflict'),
        hint: t('ai.agent.proposalErrors.conflictHint')
      }
    case 410:
      return {
        title: t('ai.agent.proposalErrors.expired'),
        hint: t('ai.agent.proposalErrors.expiredHint')
      }
    case 422:
      return {
        title: t('ai.agent.proposalErrors.totpRequired'),
        hint: t('ai.agent.proposalErrors.totpRequiredHint')
      }
    default:
      return {
        title: t('ai.agent.proposalErrors.generic'),
        hint: t('ai.agent.proposalErrors.genericHint')
      }
  }
}

function applyProposalError(card: AgentProposalCard, error: unknown) {
  const aiError = error instanceof AiRequestError ? error : null
  const code = aiError?.code ?? null
  const copy = proposalErrorCopy(code, aiError?.status ?? null)
  card.errorCode = code ?? ''
  card.errorTitle = copy.title
  card.errorHint = copy.hint
  card.requestId = aiError?.requestId ?? null
}

/** 提案失败的终态：过期单独落一个，其余算处理失败。 */
function proposalErrorState(error: unknown): AgentProposalState {
  const aiError = error instanceof AiRequestError ? error : null
  const code = aiError?.code ?? null
  const status = aiError?.status ?? null
  if (code === 'AI_AGENT_PROPOSAL_EXPIRED' || status === 410) {
    return 'expired'
  }
  return 'error'
}

/** 动态码类的失败可以原样重试：留在待确认态，让用户换个码再来一次。 */
function isAuthFailure(error: unknown): boolean {
  const aiError = error instanceof AiRequestError ? error : null
  const code = aiError?.code ?? null
  const status = aiError?.status ?? null
  return code === 'AI_AGENT_TOTP_REQUIRED' || status === 403 || status === 422
}

/**
 * 仍需二次验证（422 AI_AGENT_TOTP_REQUIRED）。
 *
 * 30 分钟授权窗口刚好过期时会出现：这不是失败，只是要再要一次码。
 */
function isTotpRequired(error: unknown): boolean {
  const aiError = error instanceof AiRequestError ? error : null
  return aiError?.code === 'AI_AGENT_TOTP_REQUIRED'
}

/** 打开动态码弹窗；用户取消返回 null。 */
async function requestTotp(card: AgentProposalCard): Promise<string | null> {
  let dialog = authRef.value
  if (!dialog) {
    await nextTick()
    dialog = authRef.value
  }
  if (!dialog) {
    ElMessage.error(t('ai.agent.dualAuth.unavailable'))
    return null
  }
  const code = await dialog.open(
    t('ai.agent.dualAuth.scene', { tool: t(toolLabelKey(card.toolName)) })
  )
  if (!code) {
    ElMessage.warning(t('ai.agent.dualAuth.cancelled'))
  }
  return code
}

/** 确认最多试三次：错的码不该把人锁在弹层里。 */
const MAX_CONFIRM_ATTEMPTS = 3

/**
 * 确认一条提案。
 *
 * 运维写操作（文件 / 数据库 / SSH）要另一位管理员放行：第一次弹动态码框，拿到码再
 * 带码确认。授权在 30 分钟窗口内有效——命中缓存（dual_auth_cached）时直接确认、不再
 * 弹框；窗口刚好过期时服务端回 422 AI_AGENT_TOTP_REQUIRED，这时重新弹码，不当失败。
 */
async function confirmCard(card: AgentProposalCard) {
  if (card.state !== 'pending') {
    return
  }
  let needCode = card.requiresDualAuth && !card.dualAuthCached
  for (let attempt = 0; attempt < MAX_CONFIRM_ATTEMPTS; attempt += 1) {
    let totpCode: string | undefined
    if (needCode) {
      // 弹层开着时按钮禁用，避免重复打开同一个弹层。
      card.state = 'confirming'
      const code = await requestTotp(card)
      if (!code) {
        card.state = 'pending'
        return
      }
      totpCode = code
    }
    card.state = 'confirming'
    try {
      const result =
        totpCode === undefined
          ? await confirmProposal(card.oid)
          : await confirmProposal(card.oid, totpCode)
      authRef.value?.succeed()
      card.state = 'confirmed'
      card.resultSummary = typeof result?.result_summary === 'string' ? result.result_summary : ''
      if (card.requiresDualAuth) {
        // 这一次授权已生效：同列表里其余待确认的运维写卡片在窗口内都免码。
        applyDualAuthCache()
      }
      ElMessage.success(t('ai.agent.proposal.confirmedNotice'))
      return
    } catch (error) {
      if (isTotpRequired(error)) {
        // 授权窗口刚好过期：重新弹码要一次，别把它显示成失败。
        needCode = true
        card.state = 'pending'
        card.errorCode = ''
        card.errorTitle = ''
        card.errorHint = ''
        continue
      }
      applyProposalError(card, error)
      if (totpCode !== undefined && isAuthFailure(error)) {
        const message = `${card.errorTitle}：${card.errorHint}`
        authRef.value?.fail(message)
        ElMessage.error(message)
        card.state = 'pending'
        continue
      }
      card.state = proposalErrorState(error)
      return
    } finally {
      syncProposalTimer()
      void nextTick(scrollToBottom)
    }
  }
}

/**
 * 首次双人授权成功后，把当前列表里其余待确认的运维写卡片标记为免码。
 *
 * 覆盖会话内的提案卡与顶部待确认入口两处。
 */
function applyDualAuthCache() {
  markDualAuthCached(proposalCards.value)
  markDualAuthCached(pendingProposals.value)
}

async function rejectCard(card: AgentProposalCard) {
  if (card.state !== 'pending') {
    return
  }
  card.state = 'rejecting'
  try {
    await rejectProposal(card.oid)
    card.state = 'rejected'
    ElMessage.success(t('ai.agent.proposal.rejectedNotice'))
  } catch (error) {
    applyProposalError(card, error)
    card.state = proposalErrorState(error)
  } finally {
    syncProposalTimer()
  }
}

/* ---------------------------------------------------------------------------
   消息区
   --------------------------------------------------------------------------- */

const listRef = ref<HTMLElement>()

let autoScroll = true

function listElement(): HTMLElement | undefined {
  return listRef.value
}

// 用户手动上翻时暂停自动跟随，滚回底部附近（64px 容差）恢复
function handleListScroll() {
  const element = listElement()
  if (!element) return
  autoScroll = element.scrollTop + element.clientHeight >= element.scrollHeight - 64
}

function scrollToBottom() {
  const element = listElement()
  if (!element || !autoScroll) return
  element.scrollTop = element.scrollHeight
}

/** 工具消息的展开态。 */
function toggleTool(oid: string) {
  expandedTools.value = expandedTools.value.includes(oid)
    ? expandedTools.value.filter((item) => item !== oid)
    : [...expandedTools.value, oid]
}

function toolExpanded(oid: string): boolean {
  return expandedTools.value.includes(oid)
}

/** 工具事件（run 留痕）的展开态。 */
function toggleEvent(event: AgentEventView) {
  const key = eventKey(event)
  expandedEvents.value = expandedEvents.value.includes(key)
    ? expandedEvents.value.filter((item) => item !== key)
    : [...expandedEvents.value, key]
}

/**
 * 回看某次历史运行的工具留痕。
 *
 * 消息上的 request_id 就是 run id，所以回看不需要另存一份映射。
 */
async function toggleReplay(message: AiMessage) {
  const runId = message.request_id
  if (!runId) {
    return
  }
  if (replays.value[runId]) {
    const next = { ...replays.value }
    delete next[runId]
    replays.value = next
    return
  }
  replayLoading.value = { ...replayLoading.value, [runId]: true }
  try {
    const detail = await fetchRunDetail(runId, 0)
    replays.value = { ...replays.value, [runId]: normalizeEventViews(detail.events) }
  } catch {
    replayFailed.value = { ...replayFailed.value, [runId]: true }
  } finally {
    replayLoading.value = { ...replayLoading.value, [runId]: false }
  }
}

/* ---------------------------------------------------------------------------
   发送
   --------------------------------------------------------------------------- */

/** 通用错误处理：错误码 + 人话说明。 */
function applyError(error: unknown) {
  const aiError = error instanceof AiRequestError ? error : null
  const code = aiError?.code ?? null
  const copy = aiErrorCopy(code, t)
  chatError.value = { code: code ?? '', title: copy.title, hint: copy.hint }
  if (code === 'AI_RATE_LIMITED' && aiError?.retryAfter) {
    startCooldown(aiError.retryAfter)
  }
}

/** 幂等键：同一次提交重试时不会重复写消息、重复计费。 */
function clientMsgId(): string {
  const cryptoRef = globalThis.crypto as Crypto | undefined
  if (cryptoRef && typeof cryptoRef.randomUUID === 'function') {
    return cryptoRef.randomUUID().replace(/-/g, '')
  }
  return `${Date.now().toString(16)}${Math.random().toString(16).slice(2, 10)}`
}

/** 服务端是否已经落库了这条用户消息。 */
function userMessagePersisted(text: string): boolean {
  const target = text.trim()
  return store.orderedMessages.some(
    (message) => message.role === 'user' && message.content.trim() === target
  )
}

async function send() {
  if (!canSend.value) {
    return
  }
  const text = input.value.trim()
  if (!text) {
    return
  }
  const conversation = store.current
  if (!conversation) {
    return
  }

  input.value = ''
  sending.value = true
  autoScroll = true
  chatError.value = null
  void nextTick(scrollToBottom)

  await sendAgentTurn(conversation, text)

  sending.value = false
  void nextTick(scrollToBottom)
}

/**
 * 智能体：提交一次后台执行。
 *
 * 服务端写完用户消息就回 202，任务在 worker 里继续跑。页面不等它跑完：
 * 提交成功后立刻显示「后台执行中」与轮次进度，由轮询推进。
 */
async function sendAgentTurn(conversation: AiConversation, text: string) {
  pendingUser.value = text
  try {
    await store.submitRun(conversation.oid, text, {
      model: model.value || undefined,
      clientMsgId: clientMsgId()
    })
    await store.refreshMessages()
    if (userMessagePersisted(text)) {
      pendingUser.value = ''
    }
  } catch (error) {
    applyError(error)
  }
}

/** 停止：请求中止当前后台任务。 */
async function cancelRun() {
  try {
    await store.cancelCurrentRun()
    ElMessage.success(t('ai.agent.run.cancelNotice'))
  } catch (error) {
    applyError(error)
  }
}

/* ---------------------------------------------------------------------------
   生命周期
   --------------------------------------------------------------------------- */

// 换会话时把模型选择对齐到该会话最近用的模型。
watch(
  () => store.currentOid,
  () => {
    const current = store.current
    if (current?.model && models.value.some((item) => item.id === current.model)) {
      model.value = current.model
    }
  }
)

// run 的提案增量合并：已处理过的卡片状态不能被轮询冲掉。
watch(
  () => store.runProposals,
  (list) => {
    const known = new Map(proposalCards.value.map((card) => [card.oid, card]))
    const next: AgentProposalCard[] = []
    for (const raw of list) {
      const existing = known.get(raw.oid)
      if (existing) {
        next.push(existing)
        continue
      }
      const created = normalizeProposals([raw])[0]
      if (created) {
        next.push(created)
      }
    }
    proposalCards.value = next
    syncProposalTimer()
  },
  { immediate: true }
)

onMounted(async () => {
  void loadModels()
  // 刷新页面后没处理的写操作还在服务端：进页面先找回来。
  void loadPendingProposals()
  await store.loadConversations()
  // 先问一次有没有在跑的任务：有就接管进度，不另起一轮。
  const runs = await store.loadActiveRuns()
  const active = runs.find((item) => item.status === 'queued' || item.status === 'running')
  if (active) {
    await store.adoptRun(active)
    return
  }
  // 接着上一次那个会话：列表按更新时间倒序，第一个就是最近聊的。
  const latest = store.conversations.find((item) => item.status === 'active')
  if (!latest) {
    // 一个会话都没有时不偷偷建：侧栏给空态引导，用户点了「新建会话」再建。
    return
  }
  await store.selectConversation(latest.oid)
})

onUnmounted(() => {
  stopCooldown()
  stopProposalTimer()
  store.stopPolling()
})
</script>

<template>
  <div class="ai-chat">
    <!-- 桌面：会话栏固定在左侧 -->
    <aside v-if="!isNarrow" class="ai-chat__sidebar">
      <ZAiSessionList
        :conversations="store.conversations"
        :current-oid="store.currentOid"
        :loading="store.listLoading"
        :failed="store.listFailed"
        :has-more="Boolean(store.nextCursor)"
        @select="selectSession"
        @create="createSession"
        @rename="openRename"
        @archive="archiveSession"
        @remove="removeSession"
        @load-more="store.loadMoreConversations()"
      />
    </aside>

    <!-- 窄屏：会话栏收进抽屉 -->
    <ElDrawer
      v-if="isNarrow"
      v-model="drawerOpen"
      direction="ltr"
      :size="Math.min(320, Math.max(240, width - 48)) + 'px'"
      :title="t('ai.chat.sessions.title')"
    >
      <ZAiSessionList
        :conversations="store.conversations"
        :current-oid="store.currentOid"
        :loading="store.listLoading"
        :failed="store.listFailed"
        :has-more="Boolean(store.nextCursor)"
        @select="selectSession"
        @create="createSession"
        @rename="openRename"
        @archive="archiveSession"
        @remove="removeSession"
        @load-more="store.loadMoreConversations()"
      />
    </ElDrawer>

    <div class="ai-chat__main">
      <ElCard shadow="never" class="ai-chat__settings" v-loading="modelsLoading">
        <template #header>
          <div class="flex flex-wrap items-center justify-between gap-3">
            <div class="flex flex-col gap-1">
              <span>{{ t('ai.agent.title') }}</span>
              <span class="text-xs font-normal" style="color: var(--yg-text-tertiary)">
                {{ t('ai.agent.subtitle') }}
              </span>
            </div>
            <div class="flex flex-wrap items-center gap-3">
              <ElButton v-if="isNarrow" text @click="drawerOpen = true">
                <ElIcon class="mr-1"><Menu /></ElIcon>
                {{ t('ai.chat.sessions.openDrawer') }}
              </ElButton>
            </div>
          </div>
        </template>

        <div class="flex flex-wrap items-center gap-3">
          <span class="yg-text-xs yg-text-tertiary">{{ t('ai.chat.model') }}</span>
          <ElSelect
            v-model="model"
            class="w-64"
            :placeholder="t('ai.chat.modelPlaceholder')"
            :disabled="!hasModels"
          >
            <ElOption v-for="item in models" :key="item.id" :label="item.id" :value="item.id" />
          </ElSelect>
          <ElButton text @click="loadModels">{{ t('ai.common.refresh') }}</ElButton>
        </div>

        <!-- 上下文水位：接近上限时换警示色 -->
        <div v-if="contextLimit > 0" class="ai-chat__context">
          <div class="ai-chat__context-head">
            <span>{{ t('ai.chat.sessions.context.label') }}</span>
            <span class="tabular-nums">
              {{ t('ai.chat.sessions.context.usage', { used: contextTokens, limit: contextLimit }) }}
            </span>
          </div>
          <div
            class="yg-progress yg-progress--sm"
            :class="{ 'yg-progress--warning': contextWarn }"
            role="progressbar"
            :aria-valuenow="contextPercentValue"
            aria-valuemin="0"
            aria-valuemax="100"
          >
            <div
              class="yg-progress__rail"
              :style="{ '--yg-progress-value': contextPercentValue }"
            >
              <div class="yg-progress__bar" />
            </div>
          </div>
          <p v-if="contextWarn" class="ai-chat__context-warn">
            {{ t('ai.chat.sessions.context.warning') }}
          </p>
        </div>
      </ElCard>

      <ElCard shadow="never" class="ai-chat__conversation">
        <!-- 待确认提案入口：刷新页面后仍在这里等你处理 -->
        <div class="yg-card yg-card--narrow ai-agent__pending">
          <div class="yg-card__header ai-agent__pending-head">
            <div class="ai-agent__pending-title-group">
              <span class="yg-card__title ai-agent__pending-title">{{
                t('ai.agent.pending.title')
              }}</span>
              <span v-if="pendingLoading" class="ai-agent__hint">{{
                t('ai.agent.run.replayLoading')
              }}</span>
              <span v-else-if="pendingFailed" class="ai-agent__hint">{{
                t('ai.agent.pending.loadFailed')
              }}</span>
              <span v-else-if="pendingCount > 0" class="ai-agent__pending-count">{{
                t('ai.agent.pending.count', { count: pendingCount })
              }}</span>
              <span v-else class="ai-agent__hint">{{ t('ai.agent.pending.empty') }}</span>
            </div>
            <div class="yg-card__extra ai-agent__pending-actions">
              <ElButton text size="small" @click="loadPendingProposals">{{
                t('ai.agent.pending.refresh')
              }}</ElButton>
              <ElButton text size="small" @click="pendingOpen = !pendingOpen">{{
                pendingOpen ? t('ai.agent.pending.collapse') : t('ai.agent.pending.entry')
              }}</ElButton>
            </div>
          </div>
          <div v-if="pendingOpen" class="yg-card__body ai-agent__pending-body">
            <p v-if="pendingLoading" class="ai-agent__hint">
              {{ t('ai.agent.run.replayLoading') }}
            </p>
            <p v-else-if="pendingFailed" class="ai-agent__hint">
              {{ t('ai.agent.pending.loadFailed') }}
            </p>
            <p v-else-if="pendingProposals.length === 0" class="ai-agent__hint">
              {{ t('ai.agent.pending.empty') }}
            </p>
            <ZAiProposalCard
              v-for="card in pendingProposals"
              :key="card.oid"
              :card="card"
              :now="proposalNow"
              @confirm="confirmCard(card)"
              @reject="rejectCard(card)"
            />
          </div>
        </div>

        <div
          ref="listRef"
          class="ai-chat__list"
          @scroll.passive="handleListScroll"
        >
          <div v-if="messageRows.length === 0 && !pendingUser" class="py-10">
            <ElEmpty :description="emptyDescription" :image-size="90" />
          </div>

          <!-- 往上翻：把窗口前面那一段也加载进来 -->
          <div v-if="store.earlierCount > 0" class="ai-chat__earlier">
            <ElButton text size="small" :loading="store.messagesLoading" @click="store.loadEarlierMessages()">
              {{ t('ai.chat.sessions.loadEarlier') }}（{{ store.earlierCount }}）
            </ElButton>
          </div>

          <template v-for="row in messageRows" :key="row.key">
            <!-- 压缩点：更早的消息已被摘要带走，原文仍在库里 -->
            <div v-if="row.kind === 'compaction'" class="ai-chat__compaction">
              <span class="ai-chat__compaction-line" aria-hidden="true" />
              <span class="ai-chat__compaction-text">
                {{ t('ai.chat.sessions.context.compacted', { count: row.compacted }) }}
              </span>
              <span class="ai-chat__compaction-line" aria-hidden="true" />
            </div>

            <div v-else class="ai-chat__row" :class="row.message!.role">
              <div class="ai-chat__who yg-text-xs yg-text-tertiary">
                {{
                  row.message!.role === 'user'
                    ? t('ai.chat.you')
                    : row.message!.role === 'tool'
                      ? t('ai.chat.sessions.toolRole')
                      : t('ai.chat.assistant')
                }}
              </div>
              <div class="ai-chat__bubble" :class="row.message!.role">
                <!-- 工具消息：折叠成一行，点开才看结果全文 -->
                <div v-if="row.message!.role === 'tool'" class="ai-chat__tool">
                  <button
                    type="button"
                    class="ai-agent__event-row"
                    :aria-expanded="toolExpanded(row.message!.oid)"
                    @click="toggleTool(row.message!.oid)"
                  >
                    <span class="ai-agent__dot is-read" aria-hidden="true" />
                    <span class="ai-agent__tool">{{
                      t(toolLabelKey(row.message!.tool_name))
                    }}</span>
                    <span class="ai-agent__badge is-success">
                      {{ t('ai.agent.status.executed') }}
                    </span>
                  </button>
                  <div v-if="toolExpanded(row.message!.oid)" class="ai-agent__event-result">
                    <span class="ai-agent__hint">{{ t('ai.agent.result') }}</span>
                    <code class="ai-agent__result-text">{{ row.message!.content }}</code>
                  </div>
                </div>

                <p v-else-if="row.message!.content" class="ai-chat__text">
                  {{ row.message!.content }}
                </p>

                <!-- 历史运行：回看这次运行的工具留痕 -->
                <div v-if="row.message!.request_id" class="ai-agent__meta">
                  <ElButton
                    text
                    size="small"
                    :loading="replayLoading[row.message!.request_id]"
                    @click="toggleReplay(row.message!)"
                  >
                    {{
                      replays[row.message!.request_id]
                        ? t('ai.agent.run.replayClose')
                        : t('ai.agent.run.replay')
                    }}
                  </ElButton>
                </div>
                <p
                  v-if="row.message!.request_id && replayFailed[row.message!.request_id]"
                  class="ai-agent__hint"
                >
                  {{ t('ai.agent.run.replayFailed') }}
                </p>
                <div
                  v-if="row.message!.request_id && replays[row.message!.request_id]"
                  class="ai-agent__replay"
                >
                  <p
                    v-if="replays[row.message!.request_id]!.length === 0"
                    class="ai-agent__hint"
                  >
                    {{ t('ai.agent.run.replayEmpty') }}
                  </p>
                  <ul v-else class="ai-agent__replay-list">
                    <li
                      v-for="(event, index) in replays[row.message!.request_id]!"
                      :key="index"
                    >
                      <span class="ai-agent__replay-round tabular-nums">{{
                        event.round_no
                      }}</span>
                      <span>{{ t(toolLabelKey(event.tool_name)) }}</span>
                      <span class="ai-agent__badge" :class="eventStatusTone(event.status)">{{
                        t(`ai.agent.status.${event.status}`)
                      }}</span>
                    </li>
                  </ul>
                </div>
              </div>
            </div>
          </template>

          <!-- 本地暂存的用户消息：服务端落库前先显示 -->
          <div v-if="pendingUser" class="ai-chat__row user">
            <div class="ai-chat__who yg-text-xs yg-text-tertiary">{{ t('ai.chat.you') }}</div>
            <div class="ai-chat__bubble user">
              <p class="ai-chat__text">{{ pendingUser }}</p>
            </div>
          </div>

          <!-- 本轮出错 -->
          <div v-if="chatError" class="ai-chat__error">
            <div class="flex flex-wrap items-center gap-2">
              <span class="font-medium">{{ chatError.title }}</span>
              <ElTag v-if="chatError.code" type="danger" size="small">{{ chatError.code }}</ElTag>
            </div>
            <p class="text-xs">{{ chatError.hint }}</p>
          </div>

          <!-- 后台执行：轮次进度 + 工具留痕 + 提案 -->
          <div v-if="store.activeRun" class="ai-agent__run">
            <div v-if="runActive" class="ai-agent__running" role="status" aria-live="polite">
              <span class="ai-agent__pulse" aria-hidden="true" />
              <span>{{ t('ai.agent.run.background') }}</span>
              <span class="ai-agent__hint tabular-nums">
                {{ t('ai.agent.run.roundsDone', { rounds: store.activeRun.rounds }) }}
              </span>
              <ElButton text type="danger" size="small" @click="cancelRun">
                {{ t('ai.agent.run.cancel') }}
              </ElButton>
            </div>

            <!-- 触顶 / 超时 / 出错是提示，不是弹窗 -->
            <div
              v-if="runStopReason && !runActive"
              class="yg-alert yg-alert--warning ai-agent__stop"
              role="status"
            >
              <ElIcon class="yg-alert__icon"><WarningFilled /></ElIcon>
              <span class="yg-alert__title">{{
                t(`ai.agent.stopReason.${runStopReason}`)
              }}</span>
              <span class="yg-alert__desc">{{
                t(`ai.agent.stopReason.${runStopReason}Hint`)
              }}</span>
            </div>

            <div v-if="runErrorCopy" class="ai-chat__error">
              <div class="flex flex-wrap items-center gap-2">
                <span class="font-medium">{{ runErrorCopy.title }}</span>
                <ElTag type="danger" size="small">{{ runErrorCopy.code }}</ElTag>
              </div>
              <p class="text-xs">{{ runErrorCopy.hint }}</p>
              <p v-if="store.activeRun.error_message" class="text-xs">
                {{ store.activeRun.error_message }}
              </p>
            </div>

            <!-- 工具调用时间线（一条调用一行，点开看结果） -->
            <div v-if="runEventViews.length > 0" class="ai-agent__timeline">
              <div class="ai-agent__timeline-head">
                <span>{{ t('ai.agent.events') }}</span>
                <span>{{ t('ai.agent.eventCount', { count: runEventViews.length }) }}</span>
              </div>
              <ul class="ai-agent__events">
                <li v-for="(event, index) in runEventViews" :key="index" class="ai-agent__event">
                  <button
                    type="button"
                    class="ai-agent__event-row"
                    :aria-expanded="event.expanded"
                    @click="toggleEvent(event)"
                  >
                    <span
                      class="ai-agent__dot"
                      :class="event.tool_kind === 'write' ? 'is-write' : 'is-read'"
                      aria-hidden="true"
                    />
                    <span class="ai-agent__tool">{{ t(toolLabelKey(event.tool_name)) }}</span>
                    <!-- 写操作不只靠色点区分：文字标签保证色觉障碍下也能读出来 -->
                    <span v-if="event.tool_kind === 'write'" class="ai-agent__kind">
                      {{ t('ai.agent.kindWrite') }}
                    </span>
                    <code v-if="event.args_summary" class="ai-agent__args">
                      {{ event.args_summary }}
                    </code>
                    <span class="ai-agent__badge" :class="eventStatusTone(event.status)">
                      {{ t(`ai.agent.status.${event.status}`) }}
                    </span>
                  </button>
                  <div v-if="event.expanded" class="ai-agent__event-result">
                    <span class="ai-agent__hint">{{ t('ai.agent.result') }}</span>
                    <code class="ai-agent__result-text">{{ event.result_summary }}</code>
                  </div>
                </li>
              </ul>
            </div>

            <!-- 写操作提案卡（确认才执行） -->
            <ZAiProposalCard
              v-for="card in proposalCards"
              :key="card.oid"
              :card="card"
              :now="proposalNow"
              @confirm="confirmCard(card)"
              @reject="rejectCard(card)"
            />

            <div class="ai-agent__meta">
              <span v-if="store.activeRun.rounds">{{
                t('ai.agent.run.rounds', { rounds: store.activeRun.rounds })
              }}</span>
              <span v-if="store.activeRun.total_tokens > 0">{{
                t('ai.agent.run.usage', { total: store.activeRun.total_tokens })
              }}</span>
              <span class="ai-agent__badge" :class="runActive ? 'is-info' : 'is-success'">
                {{ t(`ai.agent.run.status.${store.activeRun.status}`) }}
              </span>
            </div>
          </div>
        </div>

        <div class="ai-chat__composer">
          <ElInput
            v-model="input"
            type="textarea"
            :autosize="composerRows"
            resize="none"
            :placeholder="t('ai.chat.inputPlaceholder')"
            :disabled="!hasModels || !store.current"
            @keydown.enter.exact.prevent="send"
          />
          <div class="flex flex-wrap items-center justify-between gap-2 mt-2">
            <span class="text-xs" style="color: var(--yg-text-tertiary)">
              <template v-if="cooldown > 0">
                {{ t('ai.chat.rateLimited', { seconds: cooldown }) }}
              </template>
              <template v-else-if="!hasModels">
                {{ modelsFailed ? t('ai.chat.modelsLoadFailed') : t('ai.chat.modelsEmpty') }}
              </template>
              <template v-else-if="!store.current">
                {{ t('ai.chat.sessions.pickFirst') }}
              </template>
              <template v-else-if="runActive">
                {{ t('ai.agent.run.background') }}
              </template>
            </span>
            <div class="flex items-center gap-2">
              <ElButton v-if="sending || runActive" type="danger" plain @click="cancelRun">
                <ElIcon class="mr-1"><VideoPause /></ElIcon>
                {{ t('ai.chat.stop') }}
              </ElButton>
              <ElButton
                type="primary"
                :disabled="!canSend || input.trim().length === 0"
                :loading="sending"
                @click="send"
              >
                <ElIcon class="mr-1"><Promotion /></ElIcon>
                {{ t('ai.chat.send') }}
              </ElButton>
            </div>
          </div>
        </div>
      </ElCard>
    </div>

    <!-- 重命名会话 -->
    <ElDialog
      v-model="renameVisible"
      :title="t('ai.chat.sessions.renameTitle')"
      width="min(420px, 92vw)"
      :close-on-click-modal="false"
      append-to-body
    >
      <ElInput
        v-model="renameTitle"
        :placeholder="t('ai.chat.sessions.renamePlaceholder')"
        @keydown.enter.exact.prevent="submitRename"
      />
      <template #footer>
        <div class="flex items-center justify-end gap-2">
          <ElButton :disabled="renaming" @click="renameVisible = false">
            {{ t('ai.common.cancel') }}
          </ElButton>
          <ElButton type="primary" :loading="renaming" @click="submitRename">
            {{ t('ai.common.save') }}
          </ElButton>
        </div>
      </template>
    </ElDialog>

    <!-- 双人动态码：运维写操作确认时弹出（复用系统页同一个组件） -->
    <ZSystemAuthDialog ref="authRef" />
  </div>
</template>

<style scoped>
.ai-chat {
  display: flex;
  align-items: stretch;
  gap: var(--yg-space-4);
  width: 100%;
  min-width: 0;
  /* 吃掉页面面板剩下的高度：对话列自己再分「消息区 + 输入区」 */
  flex: 1 1 auto;
}

/* 桌面侧栏：固定宽度，跟主区等高 */
.ai-chat__sidebar {
  flex: none;
  width: 260px;
  /* 高度跟着对话列走，不再按视口猜一个数 */
  max-height: 100%;
  padding: var(--yg-space-3);
  border: 1px solid var(--yg-border-default);
  border-radius: var(--yg-radius-lg);
  background: var(--yg-bg-surface);
}

.ai-chat__main {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: var(--yg-space-4);
  min-width: 0;
}

/* 模型选择与上下文水位：高度固定，不参与对话区的伸缩 */
.ai-chat__settings {
  flex: none;
}

/* 对话卡片：整列撑满剩余高度，消息区滚动、输入区常驻底部 */
.ai-chat__conversation {
  display: flex;
  flex-direction: column;
  flex: 1 1 auto;
  /* 视口再矮也有一个能用的对话区，不让输入区被压没 */
  min-height: 380px;
}

/* 卡片内容区跟卡片等高，消息区与输入区才能在里面分高度 */
.ai-chat__conversation :deep(.el-card__body) {
  display: flex;
  flex-direction: column;
  flex: 1 1 auto;
  min-height: 0;
}

.ai-chat__list {
  /* 消息区吃掉所有剩余高度并自己滚动，输入区不参与伸缩。
     min-height 保底：视口再矮也要露出几行消息，不能整个塌掉。 */
  flex: 1 1 0;
  min-height: 120px;
  overflow-y: auto;
  overscroll-behavior: contain;
  border: 1px solid var(--yg-border-default);
  border-radius: 6px;
  padding: 12px;
  background: var(--yg-bg-surface-sunken);
}

.ai-chat__row {
  display: flex;
  flex-direction: column;
  gap: 2px;
  margin-bottom: 12px;
}

.ai-chat__row.user {
  align-items: flex-end;
}

.ai-chat__who {
  font-size: 11px;
}

.ai-chat__bubble {
  max-width: min(680px, 88%);
  padding: 8px 10px;
  border-radius: 6px;
  border: 1px solid var(--yg-border-default);
  background: var(--yg-bg-surface);
}

.ai-chat__bubble.user {
  background: var(--yg-color-primary);
  border-color: var(--yg-color-primary);
  color: var(--yg-color-on-primary);
}

/* 工具消息不是一句话：它是一条工具留痕，去掉气泡壳，直接落在时间线里。 */
.ai-chat__bubble.tool {
  max-width: min(680px, 92%);
  padding: 0;
  border: 0;
  background: transparent;
}

.ai-chat__text {
  white-space: pre-wrap;
  word-break: break-word;
  line-height: 1.55;
}

.ai-chat__error {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 8px;
  border-left: 2px solid var(--yg-color-danger);
  background: var(--yg-bg-surface);
}

/* 输入区：常驻对话卡片底部，消息再多也不压缩它 */
.ai-chat__composer {
  flex: none;
  margin-top: var(--yg-space-3);
}

/* 书写区：内边距与行高走令牌，跟正文同一套字号层级 */
.ai-chat__composer :deep(.el-textarea__inner) {
  padding: var(--yg-space-3);
  font-size: var(--yg-font-size-md);
  line-height: var(--yg-line-height-normal);
}

/* 压缩点：一条虚线加一句说明，把被摘要的历史隔在下面 */
.ai-chat__compaction {
  display: flex;
  align-items: center;
  gap: var(--yg-space-2);
  margin: var(--yg-space-3) 0;
  font-size: var(--yg-font-size-xs);
  color: var(--yg-text-tertiary);
}

.ai-chat__compaction-line {
  flex: 1;
  height: 1px;
  background: var(--yg-border-subtle);
}

.ai-chat__compaction-text {
  flex: none;
  font-variant-numeric: tabular-nums;
}

/* 往上翻的入口：贴在列表顶部，不打断阅读 */
.ai-chat__earlier {
  display: flex;
  justify-content: center;
  padding-bottom: var(--yg-space-2);
}

/* 上下文水位：贴着模型选择器下面，像一条细刻度 */
.ai-chat__context {
  margin-top: var(--yg-space-3);
  padding-top: var(--yg-space-3);
  border-top: 1px solid var(--yg-border-subtle);
}

.ai-chat__context-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--yg-space-2);
  margin-bottom: var(--yg-space-1);
  font-size: var(--yg-font-size-xs);
  color: var(--yg-text-tertiary);
}

.ai-chat__context-warn {
  margin: var(--yg-space-1) 0 0;
  font-size: var(--yg-font-size-xs);
  color: var(--yg-text-warning);
}

/* ---------------------------------------------------------------------------
   智能体：运行态 + 工具时间线 + 待确认入口 + 回看
   颜色/间距/圆角/动效全部走令牌（准则 2.2 / 2.7）；红色只给失败与写操作警示。
   --------------------------------------------------------------------------- */

.ai-agent__run {
  display: flex;
  flex-direction: column;
  gap: var(--yg-space-2);
  margin-top: var(--yg-space-2);
}

.ai-agent__running {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--yg-space-2);
  padding: var(--yg-space-1) 0;
  font-size: var(--yg-font-size-sm);
  color: var(--yg-text-secondary);
}

.ai-agent__pulse {
  flex: none;
  width: var(--yg-space-2);
  height: var(--yg-space-2);
  border-radius: var(--yg-radius-pill);
  background: var(--yg-color-primary);
  animation: ai-agent-pulse var(--yg-dur-page) var(--yg-ease-in-out) infinite;
}

@keyframes ai-agent-pulse {
  0%,
  100% {
    opacity: 0.35;
    transform: scale(0.8);
  }
  50% {
    opacity: 1;
    transform: scale(1);
  }
}

/* 待确认提案入口：常驻在对话区顶部，刷新页面后也能找回未处理的写操作 */
.ai-agent__pending {
  /* 高度固定：提案多的时候也不该把消息区与输入区挤没 */
  flex: none;
  margin-bottom: var(--yg-space-3);
  border-left: 2px solid var(--yg-color-primary);
}

.ai-agent__pending-head {
  flex-wrap: wrap;
  gap: var(--yg-space-2);
}

.ai-agent__pending-title-group {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: var(--yg-space-2);
  min-width: 0;
}

.ai-agent__pending-title {
  font-size: var(--yg-font-size-sm);
  font-weight: var(--yg-font-weight-medium);
}

.ai-agent__pending-count {
  font-size: var(--yg-font-size-xs);
  font-variant-numeric: tabular-nums;
  color: var(--yg-text-warning);
}

.ai-agent__pending-actions {
  display: flex;
  align-items: center;
  gap: var(--yg-space-2);
}

.ai-agent__pending-body {
  display: flex;
  flex-direction: column;
  gap: var(--yg-space-2);
  padding-top: 0;
}

.ai-agent__stop {
  margin-bottom: var(--yg-space-2);
}

.ai-agent__timeline {
  display: flex;
  flex-direction: column;
  gap: var(--yg-space-2);
  margin-bottom: var(--yg-space-3);
  padding-bottom: var(--yg-space-2);
  border-bottom: 1px solid var(--yg-border-subtle);
}

.ai-agent__timeline-head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--yg-space-2);
  font-size: var(--yg-font-size-xs);
  color: var(--yg-text-tertiary);
}

.ai-agent__events {
  display: flex;
  flex-direction: column;
  gap: var(--yg-space-1);
  margin: 0;
  padding: 0;
  list-style: none;
}

.ai-agent__event {
  display: flex;
  flex-direction: column;
  gap: var(--yg-space-1);
}

/* 整行是按钮：键盘可聚焦、触控高度 ≥44px（准则 3.7 / 3.8） */
.ai-agent__event-row {
  display: flex;
  align-items: center;
  gap: var(--yg-space-2);
  width: 100%;
  min-height: 44px;
  padding: var(--yg-space-1) var(--yg-space-2);
  border: 0;
  border-radius: var(--yg-radius-sm);
  background: transparent;
  color: inherit;
  font: inherit;
  text-align: left;
  cursor: pointer;
  transition: background-color var(--yg-dur-fast) var(--yg-ease-out);
}

.ai-agent__event-row:hover {
  background: var(--yg-bg-hover);
}

.ai-agent__event-row:focus-visible {
  outline: none;
  box-shadow: var(--yg-focus-ring);
}

.ai-agent__dot {
  flex: none;
  width: var(--yg-space-2);
  height: var(--yg-space-2);
  border-radius: var(--yg-radius-pill);
  background: var(--yg-text-tertiary);
}

/* 写工具用警示色点：这一行不是查询，而是会产生提案的写操作。 */
.ai-agent__dot.is-write {
  background: var(--yg-color-warning);
}

.ai-agent__tool {
  flex: none;
  font-size: var(--yg-font-size-sm);
  font-weight: var(--yg-font-weight-medium);
  color: var(--yg-text-primary);
}

/* 写操作的文字标签：与警示色点成对出现，不让颜色成为唯一信息载体 */
.ai-agent__kind {
  flex: none;
  font-size: var(--yg-font-size-xs);
  color: var(--yg-text-warning);
}

.ai-agent__args {
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-family: var(--yg-font-mono);
  font-size: var(--yg-font-size-xs);
  color: var(--yg-text-tertiary);
}

.ai-agent__badge {
  flex: none;
  padding: 0 var(--yg-space-1);
  border-radius: var(--yg-radius-xs);
  background: var(--yg-bg-surface-sunken);
  color: var(--yg-text-secondary);
  font-size: var(--yg-font-size-xs);
  line-height: 1.7;
}

.ai-agent__badge.is-success {
  background: var(--yg-color-success-soft);
  color: var(--yg-text-success);
}

.ai-agent__badge.is-warning {
  background: var(--yg-color-warning-soft);
  color: var(--yg-text-warning);
}

.ai-agent__badge.is-danger {
  background: var(--yg-color-danger-soft);
  color: var(--yg-text-danger);
}

.ai-agent__badge.is-info {
  background: var(--yg-color-info-soft);
  color: var(--yg-text-link);
}

.ai-agent__event-result {
  display: flex;
  flex-direction: column;
  gap: var(--yg-space-1);
  padding: var(--yg-space-2);
  border-radius: var(--yg-radius-sm);
  background: var(--yg-bg-surface-sunken);
}

.ai-agent__result-text {
  font-family: var(--yg-font-mono);
  font-size: var(--yg-font-size-xs);
  white-space: pre-wrap;
  word-break: break-word;
  color: var(--yg-text-secondary);
}

.ai-agent__hint {
  margin: 0;
  font-size: var(--yg-font-size-xs);
  color: var(--yg-text-tertiary);
}

.ai-agent__meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--yg-space-3);
  margin-top: var(--yg-space-2);
  font-size: var(--yg-font-size-xs);
  font-variant-numeric: tabular-nums;
  color: var(--yg-text-tertiary);
}

.ai-agent__replay {
  margin-top: var(--yg-space-2);
  padding: var(--yg-space-2);
  border-radius: var(--yg-radius-sm);
  background: var(--yg-bg-surface-sunken);
}

.ai-agent__replay-list {
  display: flex;
  flex-direction: column;
  gap: var(--yg-space-1);
  margin: 0;
  padding: 0;
  list-style: none;
  font-size: var(--yg-font-size-xs);
  color: var(--yg-text-secondary);
}

.ai-agent__replay-list li {
  display: flex;
  align-items: center;
  gap: var(--yg-space-2);
  min-width: 0;
}

.ai-agent__replay-round {
  flex: none;
  min-width: var(--yg-space-6);
  color: var(--yg-text-tertiary);
}

/* 窄屏：待确认入口的操作按钮换行，不撑破页面 */
@media (max-width: 640px) {
  .ai-agent__pending-actions {
    width: 100%;
  }

  .ai-agent__replay-round {
    min-width: var(--yg-space-5);
  }
}

@media (prefers-reduced-motion: reduce) {
  .ai-agent__pulse {
    animation: none;
    opacity: 1;
  }

  .ai-agent__event-row {
    transition: none;
  }
}
</style>
