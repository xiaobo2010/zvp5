<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElButton } from 'element-plus/es/components/button/index.mjs'
import { ElCard } from 'element-plus/es/components/card/index.mjs'
import { ElDialog } from 'element-plus/es/components/dialog/index.mjs'
import { ElDrawer } from 'element-plus/es/components/drawer/index.mjs'
import { ElEmpty } from 'element-plus/es/components/empty/index.mjs'
import { ElIcon } from 'element-plus/es/components/icon/index.mjs'
import { ElInput } from 'element-plus/es/components/input/index.mjs'
import { ElMessage } from 'element-plus/es/components/message/index.mjs'
import { ElMessageBox } from 'element-plus/es/components/message-box/index.mjs'
import { ElSwitch } from 'element-plus/es/components/switch/index.mjs'
import { ElTag } from 'element-plus/es/components/tag/index.mjs'
import { CaretLeft, CaretRight, Menu, Promotion, VideoPause, WarningFilled } from '@element-plus/icons-vue'
import { useWindowSize } from '@vueuse/core'
import {
  AiRequestError,
  ackAgentRisk,
  aiErrorCopy,
  aiErrorSection,
  confirmProposal,
  fetchPendingProposals,
  fetchRunDetail,
  getAgentRiskAck,
  listModels,
  rejectProposal,
  unlockAgent
} from '@/api/ai'
import { useAiConversationsStore } from '@/stores/aiConversations'
import { useUserStore } from '@/stores/user'
import { resolveErrorMessage } from '@/utils/errorMessage'
import ZSystemAuthDialog from '@/components/system/ZSystemAuthDialog.vue'
import ZMarkdown from '@/components/markdown/ZMarkdown.vue'
import type { AiConversation, AiMessage, AiModelItem, AiThinkingLevel } from '@/../types/ai'
import type { AiConversationCompactResult } from '@/api/ai'
import ZAiProposalCard from './ZAiProposalCard.vue'
import ZAiSessionList from './ZAiSessionList.vue'
import ZAiModelThinkingPicker from './ZAiModelThinkingPicker.vue'
import { contextPercent } from './conversationSession'
import {
  agentUnlockMinutesLeft,
  eventStatusTone,
  isAgentUnlocked,
  markDualAuthCached,
  normalizeEventViews,
  normalizeProposals,
  setAgentUnlockedUntil,
  toolLabelKey,
  type AgentEventView,
  type AgentProposalCard,
  type AgentProposalState
} from './agentSession'

const { t } = useI18n()
const { width, height } = useWindowSize()
const store = useAiConversationsStore()
const userStore = useUserStore()

/**
 * 风险警示只对 ZVAgent 弹——只有它会调用带真实写权限的工具。
 * 对话模式已下线，本组件只剩智能体一种形态。
 */

/** 提案有效期的刷新间隔：后端按 10 分钟计，30 秒刷一次足够。 */
const PROPOSAL_TICK_MS = 30_000

/** 侧栏折叠成抽屉的断点：窄屏放不下固定侧栏。 */
const SIDEBAR_BREAKPOINT = 1024

/** 短视口的判定线：低于它就算「高度紧张」（横屏平板、笔记本半屏）。 */
const SHORT_VIEWPORT_HEIGHT = 760

const isNarrow = computed(() => width.value < SIDEBAR_BREAKPOINT)

const models = ref<AiModelItem[]>([])
const modelsLoading = ref(false)
const modelsFailed = ref(false)
/** 新建会话用的模型：取 allowed 列表第一个，建完会话再改不了。 */
const model = ref('')

/** 思考强度：'' = 服务端默认档；改动即 PATCH 会话。 */
const thinkingChoice = ref<AiThinkingLevel | ''>('')
const thinkingSaving = ref(false)

/** 模型下拉的候选项（共享选择器只收字符串）。 */
const modelOptions = computed(() => models.value.map((item) => item.id))

const thinkingOptions = computed(() => [
  { label: t('ai.chat.thinkingDefault'), value: '' },
  { label: t('ai.chat.thinkingLow'), value: 'low' },
  { label: t('ai.chat.thinkingMedium'), value: 'medium' },
  { label: t('ai.chat.thinkingHigh'), value: 'high' }
])

/** 当前会话锁定的模型：建会话时确定，只有空会话还能改。 */
const currentModel = computed(() => store.current?.model ?? model.value)

/**
 * 能不能换模型。
 *
 * 没有当前会话（还没建、或停在上一次的空态）时随便选，选中的值会在建会话时带上；
 * 有当前会话时只有「一条消息都没有」的空会话能改——后端在有消息后直接 409。
 */
const canChangeModel = computed(() => !store.current || store.totalMessages === 0)
/** 有消息之后模型就锁定了：选择器禁用 + 锁定文案。 */
const modelLocked = computed(() => !canChangeModel.value)
const modelSaving = ref(false)

/** 换模型：无当前会话时只改本地预选，有当前会话时空会话走 PATCH。 */
async function changeModel(value: string) {
  if (!canChangeModel.value || !value || value === currentModel.value) {
    return
  }
  const conversation = store.current
  if (!conversation) {
    model.value = value
    return
  }
  modelSaving.value = true
  try {
    await store.setConversationModel(conversation.oid, value)
    // 记住这次选择：下一次「新建会话」沿用，不必再选一遍。
    model.value = value
  } catch (error) {
    applyError(error)
  } finally {
    modelSaving.value = false
  }
}

/**
 * 设置卡折叠：默认收成一行摘要（对话区纵向像素优先），用户按账号记住选择。
 * 短视口沿用历史行为：无论偏好如何都强制折叠，但点一下可以临时摊开。
 */
const SETTINGS_FOLD_PREF_KEY = 'zvms.ai.settingsFolded'

/** 短视口（横屏平板 / 笔记本半屏）：设置卡收成一行摘要。 */
const isShortViewport = computed(() => height.value < SHORT_VIEWPORT_HEIGHT)

function readSettingsFoldPreference(): boolean {
  try {
    const raw = localStorage.getItem(`${SETTINGS_FOLD_PREF_KEY}.${userStore._id || 'anon'}`)
    return raw === null ? true : raw === '1'
  } catch {
    return true
  }
}

/** 用户偏好：收起（true）还是摊开（false）。默认收起。 */
const settingsFoldPref = ref(readSettingsFoldPreference())
/** 短视口强制折叠时的临时摊开标记。 */
const settingsExpanded = ref(false)
const settingsFolded = computed(
  () => (settingsFoldPref.value || isShortViewport.value) && !settingsExpanded.value
)

function toggleSettings(): void {
  if (settingsFolded.value) {
    // 展开一次：短视口强制折叠下只做临时展开（沿用历史语义）；
    // 常规视口下则记住「用户喜欢摊开」，下次进来不用再点。
    if (isShortViewport.value) {
      settingsExpanded.value = true
    } else {
      settingsFoldPref.value = false
    }
  } else {
    // 收起：偏好落成「收起」，临时展开标记同时清掉
    settingsFoldPref.value = true
    settingsExpanded.value = false
  }
  try {
    localStorage.setItem(
      `${SETTINGS_FOLD_PREF_KEY}.${userStore._id || 'anon'}`,
      settingsFoldPref.value ? '1' : '0'
    )
  } catch {
    /* 隐私模式写不进去也要让本次会话生效 */
  }
}

/**
 * 会话栏折叠：桌面侧栏收成一条窄轨，宽度全让给对话列。按账号记住选择。
 * 窄屏本来就走抽屉，不适用这条偏好。
 */
const SIDEBAR_FOLD_PREF_KEY = 'zvms.ai.sessionSidebar'

function readSidebarFoldPreference(): boolean {
  try {
    const raw = localStorage.getItem(`${SIDEBAR_FOLD_PREF_KEY}.${userStore._id || 'anon'}`)
    return raw === '1'
  } catch {
    return false
  }
}

const sidebarCollapsed = ref(readSidebarFoldPreference())

function toggleSidebar(): void {
  sidebarCollapsed.value = !sidebarCollapsed.value
  try {
    localStorage.setItem(
      `${SIDEBAR_FOLD_PREF_KEY}.${userStore._id || 'anon'}`,
      sidebarCollapsed.value ? '1' : '0'
    )
  } catch {
    /* 隐私模式写不进去也要让本次会话生效 */
  }
}

/**
 * 流式输出偏好。
 *
 * 默认开启：逐字显示是更自然的等待反馈；多 worker 部署下 SSE 会 503，store
 * 自动回退轮询并在设置卡里给一句提示，不把开关强行拨回去。用户按账号记住选择。
 */
const STREAM_PREF_KEY = 'zvms.ai.streaming'

function readStreamingPreference(): boolean {
  try {
    const raw = localStorage.getItem(`${STREAM_PREF_KEY}.${userStore._id || 'anon'}`)
    return raw === null ? true : raw === '1'
  } catch {
    return true
  }
}

const streamingEnabled = ref(readStreamingPreference())

watch(streamingEnabled, (value) => {
  try {
    localStorage.setItem(`${STREAM_PREF_KEY}.${userStore._id || 'anon'}`, value ? '1' : '0')
  } catch {
    /* 隐私模式写不进去也要让本次会话生效 */
  }
  store.setPreferStreaming(value)
})

/** 收起态摘要里的思考强度：跟随会话当前值。 */
const thinkingLabel = computed(() => {
  const value = store.current?.thinking ?? thinkingChoice.value ?? ''
  return thinkingOptions.value.find((item) => item.value === value)?.label ?? ''
})

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

/** 中文输入法组字中：候选确认那一下不能当发送。 */
const composing = ref(false)

/** 上一次「未确认成功」的幂等键：重发时复用它，服务端不会重复落库。 */
const pendingClientMsgId = ref<string | null>(null)
/** 该幂等键对应的文本：内容改了就得换新键。 */
const pendingClientText = ref('')

/** 发送被拦时的提示（跑着的时候又按了回车）。 */
const blockedNotice = ref('')
/** 输入区被拦时的抖动标记：让用户知道这次没发出去。 */
const composerShaking = ref(false)

let blockedTimer: ReturnType<typeof setTimeout> | null = null

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

/** 解锁授权窗口：只记到期时间，到点由定时器把状态刷掉。 */
const unlockNow = ref(Date.now())
const unlocked = computed(() => isAgentUnlocked(unlockNow.value))
const unlockMinutes = computed(() => agentUnlockMinutesLeft(unlockNow.value))

let unlockTimer: ReturnType<typeof setInterval> | null = null

let cooldownTimer: ReturnType<typeof setInterval> | null = null
let proposalTimer: ReturnType<typeof setInterval> | null = null

/** 提案倒计时用的「当前时间」：只在有 pending 提案时按 30 秒推进。 */
const proposalNow = ref(Date.now())

/**
 * 输入框行数：按视口高度给书写空间。
 *
 * 高屏（竖屏平板、桌面）多给两行，输入框不再是一行小框；
 * 矮屏（横屏平板、横屏手机）少两行，输入区才挤不掉消息区；
 * 极矮（短视口）只留 2 行，把高度让给消息。
 */
const composerRows = computed(() => {
  if (height.value >= 900) {
    return { minRows: 4, maxRows: 12 }
  }
  if (height.value < SHORT_VIEWPORT_HEIGHT) {
    return { minRows: 2, maxRows: 4 }
  }
  return { minRows: 3, maxRows: 8 }
})

const hasModels = computed(() => models.value.length > 0)

/** 有活跃 run 时不能再提交：同一会话同时只允许一个后台任务。 */
const runActive = computed(
  () => store.activeRun !== null && (store.activeRun.status === 'queued' || store.activeRun.status === 'running')
)
const canSend = computed(
  () =>
    hasModels.value &&
    !sending.value &&
    cooldown.value <= 0 &&
    Boolean(store.current) &&
    !runActive.value
)

/**
 * 本轮还没有正文：先占一个位置。
 *
 * 流式开着时正文由 liveAssistantText 接管；关闭或尚未收到首帧时，消息区在
 * 思考期间是空的——用户只看到「后台执行中」，不知道模型到底动没动。
 */
const pendingReplyVisible = computed(() => {
  if (!runActive.value || store.liveAssistantText.trim()) {
    return false
  }
  const list = store.orderedMessages
  let lastUserIndex = -1
  for (let index = list.length - 1; index >= 0; index -= 1) {
    if (list[index]!.role === 'user') {
      lastUserIndex = index
      break
    }
  }
  return !list
    .slice(lastUserIndex + 1)
    .some((message) => message.role === 'assistant' && message.content.trim().length > 0)
})

/** 流式增量已经有正文：直接渲染，不再显示「正在思考」占位。 */
const liveAssistantVisible = computed(() => Boolean(store.liveAssistantText.trim()))

/** 最近一个还在跑的工具：占位文案里点名，用户知道它卡在哪一步。 */
const liveToolLabel = computed(() => {
  const pending = [...store.liveTools]
    .reverse()
    .find((item) => item.status === 'pending' && item.tool_name)
  return pending?.tool_name ? t(toolLabelKey(pending.tool_name)) : ''
})

/** 占位文案：还没跑完一轮就说「正在思考」，跑过了就带上轮次。 */
const pendingReplyText = computed(() => {
  if (liveToolLabel.value) {
    return t('ai.chat.runningTool', { tool: liveToolLabel.value })
  }
  const rounds = store.activeRun?.rounds ?? 0
  return rounds > 0
    ? t('ai.chat.thinkingPlaceholderRounds', { rounds })
    : t('ai.chat.thinkingPlaceholder')
})

/**
 * 输入框提示：跑着的时候允许继续写下一句，但说清楚这一句暂时发不出去。
 */
const composerPlaceholder = computed(() =>
  runActive.value ? t('ai.chat.inputPlaceholderRunning') : t('ai.chat.inputPlaceholder')
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

/** 手动压缩中：按钮转圈，连点不再发请求。 */
const compacting = ref(false)

/** 409 / AI_RUN_CONFLICT：有活跃 run，压缩被挡下。 */
function isRunConflict(error: unknown): boolean {
  return (
    error instanceof AiRequestError &&
    (error.status === 409 || error.code === 'AI_RUN_CONFLICT')
  )
}

/** 压缩成功的提示：两端 token 都拿到时附上「约 X → Y」。 */
function compactNotice(result: AiConversationCompactResult): string {
  const { tokens_before: before, tokens_after: after } = result
  if (before > 0 && after >= 0 && after < before) {
    return t('ai.chat.sessions.context.compactOkTokens', { before, after })
  }
  return t('ai.chat.sessions.context.compactOk')
}

/**
 * 手动压缩当前会话的上下文。
 *
 * 成功由 store 重拉会话详情（进度条按后端口径回落），这里只负责提示；
 * 没有可压的内容不是错误，单独给一句说明。
 */
async function compactContext() {
  const conversation = store.current
  if (!conversation || compacting.value) {
    return
  }
  compacting.value = true
  try {
    const result = await store.compactConversation(conversation.oid)
    if (!result.compacted) {
      ElMessage.warning(t('ai.chat.sessions.context.nothingToCompact'))
      return
    }
    ElMessage.success(compactNotice(result))
  } catch (error) {
    if (isRunConflict(error)) {
      ElMessage.warning(t('ai.chat.sessions.context.compactBusy'))
      return
    }
    ElMessage.error(resolveErrorMessage(error, 'ai.chat.sessions.context.compactFailed'))
  } finally {
    compacting.value = false
  }
}

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

/** 内容安全拦截的统一错误码：只给一句固定文案，不再展开处罚梯度。 */
const SAFETY_REJECTED = 'AI_SAFETY_REJECTED'

/** run 失败时的错误码与人话说明；安全拦截不展开解释，也不显示错误码。 */
const runErrorCopy = computed(() => {
  const code = store.activeRun?.error_code ?? null
  if (!code) {
    return null
  }
  if (code === SAFETY_REJECTED) {
    return { code: '', title: t('ai.chat.safetyRejected'), hint: '' }
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

/**
 * 思考过程的折叠态：记的是「被用户收起」的消息。
 *
 * 默认展开——思考内容是新信息，默认藏起来等于没做；收起一次就够，
 * 轮询刷新同一条消息不会把它重新弹开（按 oid 记）。
 */
const collapsedReasoning = ref<string[]>([])

function reasoningOpen(oid: string): boolean {
  return !collapsedReasoning.value.includes(oid)
}

function toggleReasoning(oid: string) {
  collapsedReasoning.value = reasoningOpen(oid)
    ? [...collapsedReasoning.value, oid]
    : collapsedReasoning.value.filter((item) => item !== oid)
}

/* ---------------------------------------------------------------------------
   首次使用风险警示
   --------------------------------------------------------------------------- */

/**
 * 按账号隔离：同一台机器换账号时，旧账号的确认不会替新账号放行。
 */
function riskAckKey(): string {
  return `zvms.agent.riskAck.${String(userStore._id || 'unknown')}`
}

/** 隐私模式/配额满时 localStorage 会抛异常：读不到就按「未确认」处理。 */
function riskAcked(): boolean {
  try {
    return Boolean(localStorage.getItem(riskAckKey()))
  } catch {
    return false
  }
}

/** 写本地快速缓存：写不进去也要让本次会话生效，不能让隐私模式把对话卡死。 */
function markRiskAckedLocally() {
  try {
    localStorage.setItem(riskAckKey(), '1')
  } catch {
    /* 隐私模式写不进去：内存态照常放行本次，下次再查服务端 */
  }
}

const riskVisible = ref(false)
/** 风险框的结论先暂存，等 @closed 再结算；null 表示当前没有待结算的结论。 */
let riskVerdict: boolean | null = null
/** 门禁 promise：建会话、切换、提交共用同一次弹框，不叠罗汉。 */
let riskGate: Promise<boolean> | null = null
let riskResolver: ((ok: boolean) => void) | null = null

function openRisk(): Promise<boolean> {
  if (riskGate) {
    return riskGate
  }
  riskGate = new Promise<boolean>((resolve) => {
    riskResolver = resolve
  })
  riskVerdict = null
  riskVisible.value = true
  return riskGate
}

// 只决定结论并让框离场，不 resolve：
// 真正的结算必须等 @closed（离场动画结束、DOM 已移除）。否则风险框会和紧随其后的
// 密码解锁框 / 动态码框同批渲染，overlay 刚建好就被拆掉，表现为「点了却什么也没发生」。
function closeRisk(ok: boolean) {
  riskVerdict = ok
  riskVisible.value = false
}

// ElDialog 关闭动画结束后触发。resolver 只消费一次，重复 closed 直接忽略（防重入）；
// 取消后门禁清空，下次进入/下次动作还能再弹。
function onRiskClosed() {
  if (!riskResolver) {
    return
  }
  const verdict = riskVerdict ?? false // 右上角 X / ESC 不会走 ack/cancel，按取消结算
  const resolve = riskResolver
  riskResolver = null
  riskVerdict = null
  riskGate = null
  resolve(verdict)
}

function cancelRisk() {
  closeRisk(false)
}

function ackRisk() {
  // 先写本地缓存并放行：确认是跟账号走的，本机不该再被同一道门拦一次。
  markRiskAckedLocally()
  closeRisk(true)
  // 后台落库到服务端：失败静默——本设备已有缓存，换设备进入时 GET 会补上，
  // 不重复打扰本设备。
  void ackAgentRisk().catch(() => {})
}

/**
 * 智能体门禁：本地缓存命中直接放行（首屏零请求）；未命中以服务端为准
 * （确认跟账号走，换设备同样生效）；服务端未确认或查询失败一律弹框兜底，
 * 网络错误不外抛、不卡死门禁。
 */
async function passRiskGate(): Promise<boolean> {
  if (riskAcked()) {
    return true
  }
  try {
    const status = await getAgentRiskAck()
    if (status.acked) {
      markRiskAckedLocally()
      return true
    }
  } catch {
    /* 查询失败按未确认处理：弹原有风险框，不把错误暴露成页面报错 */
  }
  return await openRisk()
}

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
  if (!(await passRiskGate())) {
    return
  }
  const existing = store.conversations.find(
    (item) => item.mode === 'agent' && item.status === 'active'
  )
  if (existing) {
    await store.selectConversation(existing.oid)
    return
  }
  try {
    const created = await store.createConversation('agent', model.value || undefined)
    await store.selectConversation(created.oid)
  } catch {
    ElMessage.error(t('ai.chat.sessions.createFailed'))
  }
}

async function selectSession(oid: string) {
  if (!(await passRiskGate())) {
    return
  }
  await store.selectConversation(oid)
  drawerOpen.value = false
  void nextTick(scrollToBottom)
}

/** 新建会话：永远新建一个，不复用已有的（复用是「切换」的事）。 */
async function createSession() {
  if (!(await passRiskGate())) {
    return
  }
  try {
    const created = await store.createConversation('agent', model.value || undefined)
    await store.selectConversation(created.oid)
  } catch {
    ElMessage.error(t('ai.chat.sessions.createFailed'))
  }
  drawerOpen.value = false
}

/** 改思考强度：默认档传 null，服务端按自家默认走。 */
async function saveThinking(value: AiThinkingLevel | '') {
  const conversation = store.current
  if (!conversation) {
    return
  }
  thinkingSaving.value = true
  try {
    await store.setConversationThinking(conversation.oid, value === '' ? null : value)
  } catch (error) {
    thinkingChoice.value = conversation.thinking ?? ''
    applyError(error)
  } finally {
    thinkingSaving.value = false
  }
}

/** 共享选择器是受控的：只把新值接住，落库交给 thinkingChoice 的 watcher。 */
function onThinkingChange(value: AiThinkingLevel | '') {
  thinkingChoice.value = value
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

async function unarchiveSession(oid: string) {
  try {
    await store.unarchiveConversation(oid)
    ElMessage.success(t('ai.chat.sessions.unarchivedNotice'))
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
  if (code === SAFETY_REJECTED) {
    return { title: t('ai.chat.safetyRejected'), hint: '' }
  }
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
 *
 * 动态码弹层提交后只有父级回话（succeed/fail）才会退出 loading，所以这里的每个出口
 * 都必须收口：成功 → succeed；鉴权失败 → fail（换个码重试）；非鉴权失败（如 500）
 * → fail + 卡片错误态；重试次数耗尽 → 最后一次 fail 之后把弹层关掉。finally 兜一层，
 * 保证任何异常/提前 return 都不会把弹层留在转圈状态。
 */
async function confirmCard(card: AgentProposalCard) {
  if (card.state !== 'pending') {
    return
  }
  let needCode = card.requiresDualAuth && !card.dualAuthCached
  /** 本轮是否真的向弹层要过码：只有拿过码才需要关它（没码时它自己就关着）。 */
  let codeTaken = false
  try {
    for (let attempt = 0; attempt < MAX_CONFIRM_ATTEMPTS; attempt += 1) {
      let totpCode: string | undefined
      if (needCode) {
        // 弹层开着时按钮禁用，避免重复打开同一个弹层。
        card.state = 'confirming'
        const code = await requestTotp(card)
        if (!code) {
          // 用户取消 / 弹层超时收口：都不算失败，卡片退回待确认
          card.state = 'pending'
          return
        }
        codeTaken = true
        totpCode = code
      }
      card.state = 'confirming'
      try {
        const result = await withUnlockRetry(() =>
          totpCode === undefined
            ? confirmProposal(card.oid)
            : confirmProposal(card.oid, totpCode)
        )
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
        if (error === UNLOCK_CANCELLED) {
          // 取消解锁：卡片退回待确认，不是失败。
          // 动态码已被取走、弹层还开着（submit 后不自动关），这里顺手收掉。
          if (totpCode !== undefined) {
            authRef.value?.succeed()
          }
          card.state = 'pending'
          return
        }
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
        if (totpCode !== undefined) {
          const message = `${card.errorTitle}：${card.errorHint}`
          // 拿过码就必须回话：鉴权失败留在待确认让用户换码，其它错误落卡片终态。
          authRef.value?.fail(message)
          if (isAuthFailure(error)) {
            ElMessage.error(message)
            card.state = 'pending'
            continue
          }
        }
        card.state = proposalErrorState(error)
        return
      } finally {
        syncProposalTimer()
        void nextTick(scrollToBottom)
      }
    }
    // 三次都用到头：还有可重试的余地（卡片回到待确认），弹层由下面的 finally 收掉
    card.state = 'pending'
  } finally {
    // 拿过码就走完了这一轮：弹层不能再留在打开/loading 状态
    if (codeTaken) {
      authRef.value?.cancel()
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
    await withUnlockRetry(() => rejectProposal(card.oid))
    card.state = 'rejected'
    ElMessage.success(t('ai.agent.proposal.rejectedNotice'))
  } catch (error) {
    if (error === UNLOCK_CANCELLED) {
      card.state = 'pending'
      return
    }
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

/**
 * 消息内容容器。
 *
 * 单独一层是为了盯它的高度：markdown 渲染是异步的（防抖 + v-html 替换），
 * 高度变了才补一次贴底，否则回复渲染完会停在半空。
 */
const contentRef = ref<HTMLElement>()

let autoScroll = true
let resizeObserver: ResizeObserver | null = null

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

/**
 * 盯住内容高度：内容一变高就补一次贴底。
 *
 * 用户手动上滑后 autoScroll 为 false，这里不再强拉回底部。
 */
function observeContent() {
  const content = contentRef.value
  if (!content || typeof ResizeObserver === 'undefined') return
  resizeObserver?.disconnect()
  resizeObserver = new ResizeObserver(() => {
    scrollToBottom()
  })
  resizeObserver.observe(content)
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
   解锁与内容审核
   --------------------------------------------------------------------------- */

/** 服务端要求先解锁的错误码：提交 run、确认 / 驳回提案都可能碰到。 */
const UNLOCK_REQUIRED = 'AI_AGENT_UNLOCK_REQUIRED'

/** 用户在解锁弹窗里取消：放弃当前动作，不落成一条错误。 */
const UNLOCK_CANCELLED = Symbol('agent-unlock-cancelled')

function isUnlockRequired(error: unknown): boolean {
  return error instanceof AiRequestError && error.code === UNLOCK_REQUIRED
}

/** 解锁窗口的倒计时：只在有效期内转，过期自己停。 */
function syncUnlockTimer() {
  const active = isAgentUnlocked()
  if (active && !unlockTimer) {
    unlockNow.value = Date.now()
    unlockTimer = setInterval(() => {
      unlockNow.value = Date.now()
      if (!isAgentUnlocked(unlockNow.value)) {
        stopUnlockTimer()
      }
    }, PROPOSAL_TICK_MS)
  } else if (!active && unlockTimer) {
    stopUnlockTimer()
  }
}

function stopUnlockTimer() {
  if (unlockTimer) {
    clearInterval(unlockTimer)
    unlockTimer = null
  }
}

/**
 * 弹一次密码框并解锁；取消返回 false。
 *
 * 密码错时提示后重新弹，让用户原地重输；连错锁定直接收手——继续弹只会一直吃 429。
 */
async function promptUnlock(): Promise<boolean> {
  for (;;) {
    let password = ''
    try {
      const result = await ElMessageBox.prompt(
        t('ai.agent.unlock.prompt'),
        t('ai.agent.unlock.title'),
        {
          confirmButtonText: t('ai.common.confirm'),
          cancelButtonText: t('ai.common.cancel'),
          inputType: 'password',
          closeOnClickModal: false
        }
      )
      password = result.value ?? ''
    } catch {
      return false
    }
    if (!password) {
      return false
    }
    try {
      setAgentUnlockedUntil(await unlockAgent(password))
      unlockNow.value = Date.now()
      syncUnlockTimer()
      return true
    } catch (error) {
      const aiError = error instanceof AiRequestError ? error : null
      if (aiError?.code === 'AI_AGENT_UNLOCK_LOCKED' || aiError?.status === 429) {
        ElMessage.error(t('ai.agent.unlock.locked'))
        return false
      }
      if (aiError?.code === 'AI_AGENT_UNLOCK_FAILED' || aiError?.status === 403) {
        ElMessage.error(t('ai.agent.unlock.wrong'))
        continue
      }
      applyError(error)
      return false
    }
  }
}

/**
 * 遇到「需要解锁」就验证一次，然后原动作自动重试一次。
 *
 * 只重试一次：再失败说明授权没生效（或后端又改了契约），继续循环只会把用户困在弹窗里。
 */
async function withUnlockRetry<T>(action: () => Promise<T>): Promise<T> {
  try {
    return await action()
  } catch (error) {
    if (!isUnlockRequired(error)) {
      throw error
    }
    if (!(await promptUnlock())) {
      throw UNLOCK_CANCELLED
    }
    return await action()
  }
}

/* ---------------------------------------------------------------------------
   发送
   --------------------------------------------------------------------------- */

/** 通用错误处理：错误码 + 人话说明；安全拦截只留那句固定文案。 */
function applyError(error: unknown) {
  const aiError = error instanceof AiRequestError ? error : null
  const code = aiError?.code ?? null
  if (code === SAFETY_REJECTED) {
    chatError.value = { code: '', title: t('ai.chat.safetyRejected'), hint: '' }
    return
  }
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

/** 发送被拦：给一句明确的话，别让这次按回车石沉大海。 */
function notifySendBlocked(): void {
  blockedNotice.value = t('ai.chat.sendingBlocked')
  composerShaking.value = true
  if (blockedTimer) {
    clearTimeout(blockedTimer)
  }
  blockedTimer = setTimeout(() => {
    composerShaking.value = false
    blockedNotice.value = ''
    blockedTimer = null
  }, 2400)
}

/** 输入法组字开始：这时候的回车是选词，不是发送。 */
function onCompositionStart(): void {
  composing.value = true
}

/** 输入法组字结束：回到正常输入。 */
function onCompositionEnd(): void {
  composing.value = false
}

/**
 * 回车发送：中文输入法组字中的那一下不算。
 *
 * 组字期间（compositionstart → compositionend）按回车是「选词」；不挡住的话，
 * 刚打完一句中文，候选词一确认消息就飞出去了。
 */
function onComposerKeydown(event: Event | KeyboardEvent): void {
  const keyEvent = event as KeyboardEvent
  if (
    keyEvent.key !== 'Enter' ||
    keyEvent.shiftKey ||
    keyEvent.ctrlKey ||
    keyEvent.metaKey ||
    keyEvent.altKey
  ) {
    return
  }
  if (composing.value || keyEvent.isComposing) {
    return
  }
  keyEvent.preventDefault()
  void send()
}

async function send() {
  if (!(await passRiskGate())) {
    return
  }
  if (!canSend.value) {
    // 跑着的时候又按回车：说清楚为什么没发出去。
    if (runActive.value || sending.value) {
      notifySendBlocked()
    }
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

  // 幂等键：上一次没确认成功且内容没改，就沿用同一个键；
  // 确认成功（或内容改了）才重新生成。
  const reuseId =
    pendingClientMsgId.value && pendingClientText.value === text ? pendingClientMsgId.value : null
  const msgId = reuseId ?? clientMsgId()

  input.value = ''
  sending.value = true
  autoScroll = true
  chatError.value = null
  void nextTick(scrollToBottom)

  await sendAgentTurn(conversation, text, msgId)

  sending.value = false
  void nextTick(scrollToBottom)
}

/**
 * 智能体：提交一次后台执行。
 *
 * 服务端写完用户消息就回 202，任务在 worker 里继续跑。页面不等它跑完：
 * 提交成功后立刻显示「后台执行中」与轮次进度，由轮询推进。
 */
async function sendAgentTurn(conversation: AiConversation, text: string, msgId: string) {
  pendingUser.value = text
  try {
    await withUnlockRetry(() =>
      store.submitRun(conversation.oid, text, {
        clientMsgId: msgId,
        streaming: streamingEnabled.value
      })
    )
    // 提交成功：这条已经落库，下一条消息用新键。
    pendingClientMsgId.value = null
    pendingClientText.value = ''
    await store.refreshMessages()
    if (userMessagePersisted(text)) {
      pendingUser.value = ''
    }
  } catch (error) {
    if (error === UNLOCK_CANCELLED) {
      // 取消解锁：把没发出去的内容还回输入框，别让用户白打一遍。
      pendingUser.value = ''
      if (!input.value) {
        input.value = text
      }
      return
    }
    // 提交失败：store 侧已在 catch 里停轮询并复位 run。这里必须清掉那条
    // 还没落库的乐观气泡，否则它会和下一轮的回答并列显示（封禁/拒绝时尤其明显）；
    // 内容还回输入框，避免用户重打。
    pendingUser.value = ''
    if (!input.value) {
      input.value = text
    }
    // 这次没确认成功：幂等键留着，重发时服务端认得同一条消息，不会重复落库。
    pendingClientMsgId.value = msgId
    pendingClientText.value = text
    applyError(error)
  }
}

/** 停止：请求中止当前后台任务。没有在跑的任务就不假装中止成功。 */
async function cancelRun() {
  if (!runActive.value) {
    return
  }
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

// 换会话时把思考强度对齐到该会话当前值。
watch(
  () => store.currentOid,
  () => {
    thinkingChoice.value = store.current?.thinking ?? ''
    // 幂等键跟着会话走：上一条会话里没确认成功的那次提交，不该带到新会话。
    pendingClientMsgId.value = null
    pendingClientText.value = ''
  }
)

// 思考强度改动即落库；值没变（例如切会话时的对齐）不发请求。
watch(thinkingChoice, (value) => {
  const conversation = store.current
  if (!conversation) {
    return
  }
  if ((conversation.thinking ?? '') === value) {
    return
  }
  void saveThinking(value)
})

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

// 消息换了就补一次贴底：markdown 渲染完会再由高度监听兜一次。
watch(
  () => {
    const list = store.orderedMessages
    const last = list[list.length - 1]
    return `${list.length}:${last?.oid ?? ''}:${last?.content.length ?? 0}`
  },
  () => {
    void nextTick(scrollToBottom)
  }
)

onMounted(async () => {
  // 首次进入智能体：先把风险提示摆出来，确认前不建会话、不切换、不提交。
  void passRiskGate()
  void loadModels()
  // 偏好要在接管活跃 run 之前落到 store：首次进入就按用户设置决定链路。
  store.setPreferStreaming(streamingEnabled.value)
  // 解锁窗口可能还在（同一次访问里切过页面）：先让倒计时接上。
  syncUnlockTimer()
  observeContent()
  // 刷新页面后没处理的写操作还在服务端：进页面先找回来。
  void loadPendingProposals()
  // 会话列表与活跃 run 互不依赖：并行拉，首屏少等一个来回。
  const [, runs] = await Promise.all([store.loadConversations(), store.loadActiveRuns()])
  // 先问一次有没有在跑的任务：有就接管进度，不另起一轮。
  const active = runs.find((item) => item.status === 'queued' || item.status === 'running')
  if (active) {
    await store.adoptRun(active, { streaming: streamingEnabled.value })
    return
  }
  // 接着上一次那个会话：列表按更新时间倒序，第一个就是最近聊的。
  const latest = store.conversations.find((item) => item.status === 'active')
  if (!latest) {
    // 一个会话都没有时不偷偷建：侧栏给空态引导，用户点了「新建会话」再建。
    return
  }
  if (!(await passRiskGate())) {
    // 没确认风险提示：停在空态，什么都没动
    return
  }
  await store.selectConversation(latest.oid)
})

onUnmounted(() => {
  stopCooldown()
  stopProposalTimer()
  stopUnlockTimer()
  if (blockedTimer) {
    clearTimeout(blockedTimer)
    blockedTimer = null
  }
  resizeObserver?.disconnect()
  resizeObserver = null
  store.stopStreaming()
  store.stopPolling()
})
</script>

<template>
  <div class="ai-chat">
    <!-- 桌面：会话栏固定在左侧，可收成窄轨把宽度全让给对话列 -->
    <aside
      v-if="!isNarrow"
      class="ai-chat__sidebar"
      :class="{ 'is-collapsed': sidebarCollapsed }"
    >
      <!-- 收起态：一条窄轨，展开按钮 + 竖排标签，宽度全还给主列 -->
      <button
        v-if="sidebarCollapsed"
        type="button"
        class="ai-chat__sidebar-rail"
        :aria-expanded="false"
        :aria-label="t('ai.chat.sessions.list')"
        @click="toggleSidebar"
      >
        <ElIcon :size="16" class="ai-chat__sidebar-rail-icon"><CaretRight /></ElIcon>
        <span class="ai-chat__sidebar-rail-label">{{ t('ai.chat.sessions.list') }}</span>
      </button>
      <template v-else>
        <ZAiSessionList
          :conversations="store.conversations"
          :current-oid="store.currentOid"
          :loading="store.listLoading"
          :failed="store.listFailed"
          :has-more="Boolean(store.nextCursor)"
          :archived="store.archivedConversations"
          :archived-loading="store.archivedLoading"
          :archived-failed="store.archivedFailed"
          @select="selectSession"
          @create="createSession"
          @rename="openRename"
          @archive="archiveSession"
          @unarchive="unarchiveSession"
          @remove="removeSession"
          @load-more="store.loadMoreConversations()"
          @load-archived="store.loadArchivedConversations()"
        />
      </template>
    </aside>

    <!-- 折叠把手：跨在侧栏与对话列的缝上，点了把会话栏收成窄轨。
         挂在 .ai-chat 根上（aside 有 overflow:hidden，放里面会被裁掉） -->
    <button
      v-if="!isNarrow && !sidebarCollapsed"
      type="button"
      class="ai-chat__sidebar-handle"
      :aria-expanded="true"
      :aria-label="t('ai.chat.sessions.list')"
      @click="toggleSidebar"
    >
      <ElIcon :size="14"><CaretLeft /></ElIcon>
    </button>

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
        :archived="store.archivedConversations"
        :archived-loading="store.archivedLoading"
        :archived-failed="store.archivedFailed"
        @select="selectSession"
        @create="createSession"
        @rename="openRename"
        @archive="archiveSession"
        @unarchive="unarchiveSession"
        @remove="removeSession"
        @load-more="store.loadMoreConversations()"
        @load-archived="store.loadArchivedConversations()"
      />
    </ElDrawer>

    <div class="ai-chat__main">
      <ElCard
        shadow="never"
        class="ai-chat__settings"
        :class="{ 'is-folded': settingsFolded }"
        v-loading="modelsLoading"
      >
        <template #header>
          <div class="flex flex-wrap items-center justify-between gap-2">
            <div class="flex items-baseline gap-2 min-w-0">
              <span>{{ t('ai.agent.title') }}</span>
              <span class="text-xs font-normal ai-chat__settings-subtitle">
                {{ t('ai.agent.subtitle') }}
              </span>
            </div>
            <div class="flex flex-wrap items-center gap-2">
              <ElButton v-if="isNarrow" text class="yg-btn yg-btn--text yg-btn--sm" @click="drawerOpen = true">
                <ElIcon class="mr-1"><Menu /></ElIcon>
                {{ t('ai.chat.sessions.openDrawer') }}
              </ElButton>
              <!-- 设置卡折叠：任何视口都能收成一行摘要，把纵向像素还给消息区 -->
              <ElButton
                text
                size="small"
                class="yg-btn yg-btn--text yg-btn--sm"
                :aria-expanded="!settingsFolded"
                @click="toggleSettings"
              >
                {{ settingsFolded ? t('ai.chat.expandSettings') : t('ai.chat.foldSettings') }}
              </ElButton>
            </div>
          </div>
        </template>

        <!-- 收起态：一行摘要，够看清现在跑哪个模型、哪档思考 -->
        <p v-if="settingsFolded" class="ai-chat__settings-summary">
          <span>{{ t('ai.chat.model') }}：{{ currentModel || '—' }}</span>
          <span class="ai-chat__settings-dot" aria-hidden="true">·</span>
          <span>{{ t('ai.chat.thinking') }}：{{ thinkingLabel }}</span>
          <span class="ai-chat__settings-dot" aria-hidden="true">·</span>
          <span>{{ t('ai.chat.streaming') }}：{{ streamingEnabled ? t('ai.chat.streamingOn') : t('ai.chat.streamingOff') }}</span>
        </p>

        <!-- 摊开态：模型、思考强度、上下文水位挤在一条横线上 -->
        <div v-show="!settingsFolded" class="ai-chat__settings-body">
          <div class="ai-chat__settings-row">
            <ZAiModelThinkingPicker
              class="ai-chat__settings-models"
              :models="modelOptions"
              :model="currentModel"
              :thinking="thinkingChoice"
              :model-locked="modelLocked"
              :model-disabled="modelSaving"
              :thinking-disabled="!store.current || thinkingSaving"
              :clearable="false"
              model-width="auto"
              @update:model="changeModel"
              @update:thinking="onThinkingChange"
            >
              <template #model-extra>
                <span
                  class="ai-chat__settings-hint"
                  :title="modelLocked ? t('ai.chat.modelLocked') : t('ai.chat.modelChangeable')"
                >
                  {{
                    modelLocked ? t('ai.chat.modelLockedShort') : t('ai.chat.modelChangeableShort')
                  }}
                </span>
                <ElButton text size="small" class="yg-btn yg-btn--text yg-btn--sm" :loading="modelsLoading" @click="loadModels">
                  {{ t('ai.common.refresh') }}
                </ElButton>
              </template>
            </ZAiModelThinkingPicker>

            <!-- 流式输出：开着时逐字显示，关闭时按轮次刷新；运行中不允许切换 -->
            <span class="ai-chat__settings-sep" aria-hidden="true" />
            <div class="ai-chat__stream">
              <span class="ai-chat__settings-label">{{ t('ai.chat.streaming') }}</span>
              <ElSwitch
                v-model="streamingEnabled"
                size="small"
                :disabled="runActive"
                :aria-label="t('ai.chat.streaming')"
              />
              <span
                class="ai-chat__settings-hint"
                :title="
                  runActive
                    ? t('ai.chat.streamingBusyHint')
                    : streamingEnabled
                      ? t('ai.chat.streamingOnHint')
                      : t('ai.chat.streamingOffHint')
                "
              >
                {{
                  runActive
                    ? t('ai.chat.streamingBusyHint')
                    : streamingEnabled
                      ? t('ai.chat.streamingOnHint')
                      : t('ai.chat.streamingOffHint')
                }}
              </span>
            </div>

            <!-- 分隔：把「选什么」和「用得怎么样」分开 -->
            <span class="ai-chat__settings-sep" aria-hidden="true" />

            <!-- 上下文水位：接近上限时换警示色 -->
            <div v-if="contextLimit > 0" class="ai-chat__context">
              <span class="ai-chat__context-label">{{
                t('ai.chat.sessions.context.label')
              }}</span>
              <span class="tabular-nums ai-chat__context-usage">
                {{ t('ai.chat.sessions.context.usage', { used: contextTokens, limit: contextLimit }) }}
              </span>
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
              <span v-if="contextWarn" class="ai-chat__context-warn">
                {{ t('ai.chat.sessions.context.warning') }}
              </span>
              <span
                v-else
                class="ai-chat__context-hint"
                :title="t('ai.chat.sessions.context.autoCompactHint')"
              >
                {{ t('ai.chat.sessions.context.autoCompactHintShort') }}
              </span>
              <!-- 手动压缩：自动压缩在 80% 触发，这里给一个随时可用的入口 -->
              <ElButton
                text
                size="small"
                class="yg-btn yg-btn--text yg-btn--sm"
                :loading="compacting"
                :disabled="!store.current || compacting"
                @click="compactContext"
              >
                {{
                  compacting
                    ? t('ai.chat.sessions.context.compacting')
                    : t('ai.chat.sessions.context.compact')
                }}
              </ElButton>
            </div>
          </div>
          <p v-if="store.streamFallback" class="ai-chat__stream-fallback" role="status">
            {{ t('ai.chat.streamFallback') }}
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
          <!-- 内容单独一层：高度变化可观测，markdown 渲染完才知道该贴底 -->
          <div ref="contentRef" class="ai-chat__content">
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

                  <!-- 思考过程：只有 assistant 且模型真给了 reasoning 才渲染整块 -->
                  <div
                    v-if="row.message!.role === 'assistant' && row.message!.reasoning"
                    class="ai-chat__reasoning"
                    :class="{ 'is-collapsed': !reasoningOpen(row.message!.oid) }"
                  >
                    <button
                      type="button"
                      class="ai-chat__reasoning-head"
                      :aria-expanded="reasoningOpen(row.message!.oid)"
                      @click="toggleReasoning(row.message!.oid)"
                    >
                      <span class="ai-chat__reasoning-chevron" aria-hidden="true" />
                      <span>{{ t('ai.chat.thinkingSection') }}</span>
                    </button>
                    <p v-if="reasoningOpen(row.message!.oid)" class="ai-chat__reasoning-text">
                      {{ row.message!.reasoning }}
                    </p>
                  </div>

                  <!-- 助手回复：走 Markdown（表格、列表、代码块都在里面） -->
                  <ZMarkdown
                    v-if="row.message!.role === 'assistant' && row.message!.content"
                    :source="row.message!.content"
                    class="ai-chat__markdown"
                  />

                  <!-- 用户气泡保持纯文本：用户写的内容不该被当成 Markdown 执行 -->
                  <p
                    v-else-if="row.message!.role === 'user' && row.message!.content"
                    class="ai-chat__text"
                  >
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

            <!-- 流式增量：累计文本直接渲染，轮提交后由落库消息接管 -->
            <div v-if="liveAssistantVisible" class="ai-chat__row assistant">
              <div class="ai-chat__who yg-text-xs yg-text-tertiary">
                {{ t('ai.chat.assistant') }}
              </div>
              <div class="ai-chat__bubble assistant ai-chat__streaming">
                <ZMarkdown :source="store.liveAssistantText" class="ai-chat__markdown" />
                <span class="ai-chat__streaming-dot" aria-hidden="true" />
              </div>
            </div>

            <!-- 占位：这一轮还没出正文，先占个位置，别让消息区空着 -->
            <div v-if="pendingReplyVisible" class="ai-chat__row assistant">
              <div class="ai-chat__who yg-text-xs yg-text-tertiary">{{ t('ai.chat.assistant') }}</div>
              <div
                class="ai-chat__bubble assistant ai-chat__pending-reply"
                role="status"
                aria-live="polite"
              >
                <span class="ai-agent__pulse" aria-hidden="true" />
                <span>{{ pendingReplyText }}</span>
              </div>
            </div>

            <!-- 本轮出错：安全拦截只有一句固定文案，不展开解释 -->
            <div v-if="chatError" class="ai-chat__error">
              <div class="flex flex-wrap items-center gap-2">
                <span class="font-medium">{{ chatError.title }}</span>
                <ElTag v-if="chatError.code" type="danger" size="small">{{ chatError.code }}</ElTag>
              </div>
              <p v-if="chatError.hint" class="text-xs">{{ chatError.hint }}</p>
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
                  <ElTag v-if="runErrorCopy.code" type="danger" size="small">{{
                    runErrorCopy.code
                  }}</ElTag>
                </div>
                <p v-if="runErrorCopy.hint" class="text-xs">{{ runErrorCopy.hint }}</p>
                <p v-if="store.activeRun.error_message && runErrorCopy.code" class="text-xs">
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
        </div>

        <div class="ai-chat__composer" :class="{ 'is-shaking': composerShaking }">
          <ElInput
            v-model="input"
            type="textarea"
            :autosize="composerRows"
            resize="none"
            :placeholder="composerPlaceholder"
            :disabled="!hasModels || !store.current"
            @compositionstart="onCompositionStart"
            @compositionend="onCompositionEnd"
            @keydown="onComposerKeydown"
          />
          <div class="flex flex-wrap items-center justify-between gap-2 mt-2">
            <div class="flex flex-wrap items-center gap-2">
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
              <!-- 发送被拦：把「为什么没发出去」摆在明面上 -->
              <span v-if="blockedNotice" class="ai-chat__blocked">{{ blockedNotice }}</span>
              <span v-if="unlocked" class="ai-chat__unlock">
                {{ t('ai.agent.unlock.status', { min: unlockMinutes }) }}
              </span>
            </div>
            <div class="flex items-center gap-2">
              <ElButton v-if="runActive" type="danger" class="yg-btn yg-btn--danger" @click="cancelRun">
                <ElIcon class="mr-1"><VideoPause /></ElIcon>
                {{ t('ai.chat.stop') }}
              </ElButton>
              <ElButton
                type="primary"
                class="yg-btn yg-btn--primary"
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

    <!-- 智能体首次使用警示：确认后按账号记入 localStorage，之后不再出现 -->
    <ElDialog
      v-model="riskVisible"
      class="ai-agent__risk-dialog"
      :title="t('ai.agent.risk.title')"
      width="min(520px, calc(100vw - 32px))"
      :close-on-click-modal="false"
      append-to-body
      @closed="onRiskClosed"
    >
      <div class="ai-agent__risk-body">
        <p>{{ t('ai.agent.risk.power') }}</p>
        <p class="ai-agent__risk-ban">{{ t('ai.agent.risk.ban') }}</p>
        <p>{{ t('ai.agent.risk.audit') }}</p>
      </div>
      <template #footer>
        <ElButton size="large" @click="cancelRisk">{{ t('ai.agent.risk.cancel') }}</ElButton>
        <ElButton size="large" type="primary" @click="ackRisk">
          {{ t('ai.agent.risk.ack') }}
        </ElButton>
      </template>
    </ElDialog>

    <!-- 双人动态码：运维写操作确认时弹出（复用系统页同一个组件） -->
    <ZSystemAuthDialog ref="authRef" />
  </div>
</template>

<style scoped>
.ai-chat {
  position: relative;
  display: flex;
  align-items: stretch;
  gap: var(--yg-space-4);
  width: 100%;
  min-width: 0;
  /* 父级高度锁死时允许被压缩：滚动只发生在消息区里 */
  min-height: 0;
  /* 吃掉页面面板剩下的高度：对话列自己再分「消息区 + 输入区」 */
  flex: 1 1 auto;
}

/* 桌面侧栏：固定宽度，跟主区等高；大屏按视口略微加宽，别让会话名挤成一条。
   收起时换成 40px 窄轨（内部渲染折叠按钮），宽度全部让给对话列。 */
.ai-chat__sidebar {
  position: relative;
  display: flex;
  flex-direction: column;
  flex: none;
  width: clamp(260px, 18vw, 340px);
  /* 高度跟着对话列走，不再按视口猜一个数 */
  max-height: 100%;
  min-height: 0;
  padding: var(--yg-space-3);
  border: 1px solid var(--yg-border-default);
  border-radius: var(--yg-radius-lg);
  background: var(--yg-bg-surface);
  /* 会话多时只让内部列表滚，侧栏本身不被内容顶高 */
  overflow: hidden;
  transition: width var(--yg-dur-base) var(--yg-ease-out);
}

.ai-chat__sidebar.is-collapsed {
  width: 40px;
  padding: var(--yg-space-2) 0;
}

/* 收起态窄轨：竖排「会话列表」标签 + 展开箭头，键盘可达（原生 button） */
.ai-chat__sidebar-rail {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--yg-space-3);
  flex: 1 1 auto;
  min-height: 0;
  padding: var(--yg-space-2) 0;
  border: 0;
  background: transparent;
  color: var(--yg-text-tertiary);
  font: inherit;
  cursor: pointer;
  transition: color var(--yg-dur-fast) var(--yg-ease-out);
}

.ai-chat__sidebar-rail:hover {
  color: var(--yg-color-primary);
}

.ai-chat__sidebar-rail:focus-visible {
  outline: none;
  box-shadow: var(--yg-focus-ring);
}

.ai-chat__sidebar-rail-icon {
  flex: none;
}

.ai-chat__sidebar-rail-label {
  /* 竖排标签顺着窄轨走：writing-mode 不占横向空间 */
  writing-mode: vertical-rl;
  font-size: var(--yg-font-size-xs);
  letter-spacing: 0.12em;
  white-space: nowrap;
}

/* 折叠把手：跨在侧栏与对话列的缝上（挂在 .ai-chat 根上，不受侧栏
   overflow:hidden 裁剪），一眼能看出会话栏可以收起。 */
.ai-chat__sidebar-handle {
  position: absolute;
  top: 50%;
  /* 侧栏外缘 = clamp 宽度（border-box）；把手圆心落在侧栏与主列的缝中间 */
  inset-inline-start: calc(clamp(260px, 18vw, 340px) + (var(--yg-space-4) / 2) - 13px);
  transform: translateY(-50%);
  z-index: 2;
  display: grid;
  place-items: center;
  width: 26px;
  height: 26px;
  padding: 0;
  border: 1px solid var(--yg-border-default);
  border-radius: 50%;
  background: var(--yg-bg-surface-raised);
  color: var(--yg-text-tertiary);
  cursor: pointer;
  box-shadow: var(--yg-shadow-sm);
  transition:
    color var(--yg-dur-fast) var(--yg-ease-out),
    border-color var(--yg-dur-fast) var(--yg-ease-out);
}

.ai-chat__sidebar-handle:hover {
  color: var(--yg-color-primary);
  border-color: var(--yg-color-primary);
}

.ai-chat__sidebar-handle:focus-visible {
  outline: none;
  box-shadow: var(--yg-focus-ring);
}

.ai-chat__main {
  display: flex;
  flex: 1;
  flex-direction: column;
  /* 设置卡与对话卡之间的缝收紧一档：纵向像素优先给消息区 */
  gap: var(--yg-space-3);
  min-width: 0;
  /* 这一列的高度完全交给面板分配：里面的两张卡有各自的下限，
     断掉这层最小尺寸链，压不进面板时才会溢出到面板外。 */
  min-height: 0;
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
  /* 不设硬下限：空间不够时消息区先让位，输入区必须始终留在容器里。
     之前的 min(380px, 46dvh) 会把整列撑出面板，底部输入区被裁掉。 */
  min-height: 0;
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
     min-height 只保一个很小的底：再矮也是消息区先让位，输入区不能被顶出去。 */
  flex: 1 1 0;
  min-height: 56px;
  overflow-y: auto;
  overscroll-behavior: contain;
  border: 1px solid var(--yg-border-default);
  border-radius: var(--yg-radius-sm);
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
  max-width: min(820px, 86%);
  padding: 8px 10px;
  border-radius: var(--yg-radius-sm);
  border: 1px solid var(--yg-border-default);
  background: var(--yg-bg-surface);
}

.ai-chat__bubble.user {
  /* 实底承白字走 strong 系列：浅色 5.93:1、深色同档，正文气泡达标（准则 2.1） */
  background: var(--yg-color-primary-strong);
  border-color: var(--yg-color-primary-strong);
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

/* 消息内容层：单独一层是为了观测高度，markdown 渲染完才知道要不要贴底 */
.ai-chat__content {
  min-width: 0;
}

/* 占位气泡：这一轮还没出正文，先占住助手的位置 */
.ai-chat__pending-reply {
  display: flex;
  align-items: center;
  gap: var(--yg-space-2);
  font-size: var(--yg-font-size-sm);
  color: var(--yg-text-secondary);
}

/* 助手回复走 Markdown：排版交给 ZMarkdown，这里只保证长表格不撑破气泡 */
.ai-chat__markdown {
  min-width: 0;
  max-width: 100%;
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

/* 设置卡内容：一行横排，窄了再折行 */
.ai-chat__settings-body {
  display: flex;
  flex-direction: column;
}

.ai-chat__settings-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--yg-space-2) var(--yg-space-3);
}

/* 模型与思考强度那一组 */
.ai-chat__settings-models {
  min-width: 0;
}

/* 分隔线：把「选什么」和「用得怎么样」在视觉上分开 */
.ai-chat__settings-sep {
  flex: none;
  width: 1px;
  height: 16px;
  background: var(--yg-border-subtle);
}

/* 锁定提示：短句挂在选择器后面，长解释走 title */
.ai-chat__settings-hint {
  font-size: var(--yg-font-size-xs);
  color: var(--yg-text-tertiary);
  white-space: nowrap;
}

.ai-chat__settings-subtitle {
  color: var(--yg-text-tertiary);
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 流式开关：与模型/思考同一行，开关本身不抢视觉重心 */
.ai-chat__settings-label {
  font-size: var(--yg-font-size-xs);
  color: var(--yg-text-tertiary);
  white-space: nowrap;
}

.ai-chat__stream {
  display: inline-flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--yg-space-2);
}

.ai-chat__stream-fallback {
  margin: var(--yg-space-2) 0 0;
  font-size: var(--yg-font-size-xs);
  color: var(--yg-text-tertiary);
}

/* 流式气泡：正文照常走 Markdown，末尾只加一个轻量光标，不改变气泡尺寸 */
.ai-chat__streaming {
  position: relative;
}

.ai-chat__streaming-dot {
  display: inline-block;
  width: 6px;
  height: 6px;
  margin-left: 2px;
  vertical-align: 0.12em;
  border-radius: 50%;
  background: var(--yg-color-primary);
  animation: ai-stream-blink 1s steps(2, start) infinite;
}

@keyframes ai-stream-blink {
  0%,
  45% {
    opacity: 1;
  }
  50%,
  100% {
    opacity: 0.2;
  }
}

/* 短视口收起态：一行摘要，替代摊开的模型 / 思考 / 水位三行 */
.ai-chat__settings-summary {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--yg-space-1);
  margin: 0;
  font-size: var(--yg-font-size-xs);
  color: var(--yg-text-secondary);
}

.ai-chat__settings-dot {
  color: var(--yg-text-tertiary);
}

.ai-chat__settings-body {
  display: flex;
  flex-direction: column;
}

/* 思考过程：弱于正文的旁注；等宽字号略小，默认展开、点标题收起 */
.ai-chat__reasoning {
  margin-bottom: var(--yg-space-2);
  padding: var(--yg-space-2);
  border-left: 2px solid var(--yg-border-default);
  border-radius: var(--yg-radius-sm);
  background: var(--yg-bg-surface-sunken);
}

.ai-chat__reasoning-head {
  display: flex;
  align-items: center;
  gap: var(--yg-space-1);
  width: 100%;
  padding: 0;
  border: 0;
  background: transparent;
  font-size: var(--yg-font-size-xs);
  color: var(--yg-text-tertiary);
  text-align: left;
  cursor: pointer;
}

/* 折叠指示：小三角，展开时朝下 */
.ai-chat__reasoning-chevron {
  width: 0;
  height: 0;
  border-top: 4px solid currentColor;
  border-right: 4px solid transparent;
  border-left: 4px solid transparent;
  transform: rotate(-90deg);
  transition: transform 0.15s ease;
}

.ai-chat__reasoning-head[aria-expanded='true'] .ai-chat__reasoning-chevron {
  transform: rotate(0deg);
}

.ai-chat__reasoning-text {
  margin: var(--yg-space-1) 0 0;
  font-size: var(--yg-font-size-xs);
  line-height: var(--yg-line-height-normal);
  color: var(--yg-text-secondary);
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 240px;
  overflow-y: auto;
}

/* 解锁窗口提示：细弱小字，过期自动消失 */
.ai-chat__unlock {
  font-size: var(--yg-font-size-xs);
  font-variant-numeric: tabular-nums;
  color: var(--yg-text-tertiary);
}

/* 发送被拦的提示：跟在状态行后面，说清楚这次没发出去 */
.ai-chat__blocked {
  font-size: var(--yg-font-size-xs);
  color: var(--yg-text-warning);
}

/* 输入区被拦：抖一下，让用户看见「刚才那下没生效」 */
.ai-chat__composer.is-shaking {
  animation: ai-chat-shake 0.4s var(--yg-ease-out);
}

@keyframes ai-chat-shake {
  0%,
  100% {
    transform: translateX(0);
  }
  25% {
    transform: translateX(-4px);
  }
  75% {
    transform: translateX(4px);
  }
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

/* 上下文水位：跟模型、思考强度并排，像一条细刻度 */
.ai-chat__context {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--yg-space-2);
  min-width: 0;
}

.ai-chat__context-label,
.ai-chat__context-usage {
  font-size: var(--yg-font-size-xs);
  color: var(--yg-text-tertiary);
  white-space: nowrap;
}

/* 水位条压成一小段：横排里只占一个身位 */
.ai-chat__context .yg-progress {
  flex: none;
  width: 88px;
}

.ai-chat__context-warn {
  font-size: var(--yg-font-size-xs);
  color: var(--yg-text-warning);
}

.ai-chat__context-hint {
  font-size: var(--yg-font-size-xs);
  color: var(--yg-text-tertiary);
  white-space: nowrap;
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
  /* 提案多的时候自己滚：这一块不能把输入区顶出容器 */
  max-height: 40vh;
  overflow-y: auto;
}

.ai-agent__stop {
  margin-bottom: var(--yg-space-2);
}

/* 首次使用风险警示：三条要点逐行读，封号那条单独用警示色 */
.ai-agent__risk-body {
  display: flex;
  flex-direction: column;
  gap: var(--yg-space-3);
  font-size: var(--yg-font-size-sm);
  line-height: var(--yg-line-height-normal);
}

.ai-agent__risk-body p {
  margin: 0;
}

.ai-agent__risk-ban {
  color: var(--yg-text-danger);
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

/* 中等宽度（1024–1280）：侧栏收窄但仍要放得下「会话 / 已归档 / 新建会话」 */
@media (min-width: 1024px) and (max-width: 1280px) {
  .ai-chat__sidebar {
    width: 250px;
  }
}

/*
 * 横屏且高度紧张（笔记本半屏、横屏平板 / 手机）：
 * 高度是稀缺资源——间距压小、输入区让路。
 * 设置卡折叠与输入框行数由脚本按视口高度决定（settingsFolded / composerRows）。
 */
@media (orientation: landscape) and (max-height: 820px) {
  .ai-chat,
  .ai-chat__main {
    gap: var(--yg-space-2);
  }

  .ai-chat__conversation {
    /* 不留硬下限：横屏高度紧张时消息区先让位，输入区必须完整可见 */
    min-height: 0;
  }

  .ai-chat__list {
    min-height: 48px;
    padding: var(--yg-space-2);
  }

  .ai-chat__composer {
    margin-top: var(--yg-space-2);
  }

  .ai-chat__composer :deep(.el-textarea__inner) {
    padding: var(--yg-space-2);
  }

  .ai-chat__settings-row {
    gap: var(--yg-space-1) var(--yg-space-2);
  }

  .ai-chat__reasoning-text {
    max-height: 140px;
  }
}

/*
 * 极矮横屏（手机横放、375–600px 高）：
 * 纵向只够「页头 + 页签 + 一条设置 + 输入区」，消息区是最后让位的那一块。
 * 设置卡压成横排的一条，消息区不再保底高度——输入区必须完整可见、可点到。
 */
@media (orientation: landscape) and (max-height: 600px) {
  .ai-chat__settings {
    /* 标题与摘要并排成一条，不再上下叠两行 */
    display: flex;
    flex-direction: row;
    align-items: center;
    gap: var(--yg-space-2);
  }

  .ai-chat__settings :deep(.el-card__header) {
    flex: 0 1 auto;
    min-width: 0;
    padding: var(--yg-space-1) var(--yg-space-2);
    border-bottom: none;
  }

  .ai-chat__settings :deep(.el-card__body) {
    flex: 1 1 auto;
    min-width: 0;
    padding: var(--yg-space-1) var(--yg-space-2);
  }

  /* 标题与副标题并排，卡片高度再降一档 */
  .ai-chat__settings :deep(.el-card__header > div > div:first-child) {
    flex-direction: row;
    align-items: baseline;
    gap: var(--yg-space-2);
  }

  .ai-chat__conversation {
    /* 会话卡让位：高度全部由消息区与输入区分配 */
    min-height: 0;
  }

  /* 卡片内边距收紧：flat.css 固定了 .el-card__body 的 padding，这里直接压掉 */
  .ai-chat__conversation :deep(.el-card__body) {
    padding: var(--yg-space-2);
  }

  .ai-chat__list {
    /* 消息区让位：输入区必须完整可见，消息区高度由剩余空间决定 */
    min-height: 0;
    padding: var(--yg-space-1);
  }

  .ai-chat__composer {
    margin-top: var(--yg-space-1);
  }

  .ai-chat__composer :deep(.el-textarea__inner) {
    padding: var(--yg-space-1) var(--yg-space-2);
  }
}

@media (prefers-reduced-motion: reduce) {
  .ai-agent__pulse {
    animation: none;
    opacity: 1;
  }

  .ai-chat__composer.is-shaking {
    animation: none;
  }

  .ai-chat__streaming-dot {
    animation: none;
    opacity: 1;
  }

  .ai-agent__event-row {
    transition: none;
  }

  .ai-chat__sidebar,
  .ai-chat__sidebar-handle {
    transition: none;
  }
}
</style>
