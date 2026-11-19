<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus/es/components/message/index.mjs'
import { ElMessageBox } from 'element-plus/es/components/message-box/index.mjs'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import '@xterm/xterm/css/xterm.css'
import { useDark } from '@vueuse/core'
import {
  createSshSession,
  buildSshWsUrl,
  listSshTargets,
  suggestSshCommand
} from '@/api/system/ssh.ts'
import type { SshTarget } from '@/api/system/ssh.ts'
import { listModels } from '@/api/ai/chat.ts'
import { SCENE_CONTEXT_LIMIT } from '@/api/ai/scenes.ts'
import type { AiThinkingLevel } from '@/../types/ai'
import ZAiModelThinkingPicker from '@/components/ai/ZAiModelThinkingPicker.vue'
import ZSystemAuthDialog from '@/components/system/ZSystemAuthDialog.vue'
import ZSceneRunDialog from '@/components/server/ZSceneRunDialog.vue'
import ZHexUnit from './components/ZHexUnit.vue'
import { resolveErrorMessage } from '@/utils/errorMessage'
import { useUserStore } from '@/stores/user.ts'
import {
  ArrowDown,
  Connection,
  VideoPlay,
  SwitchButton,
  RefreshRight,
  MagicStick,
  CopyDocument,
  Download
} from '@element-plus/icons-vue'

const { t } = useI18n()
const userStore = useUserStore()

type Status = 'idle' | 'connecting' | 'connected' | 'closed'

const status = ref<Status>('idle')
const banner = ref('')
const containerRef = ref<HTMLDivElement>()
const authRef = ref<InstanceType<typeof ZSystemAuthDialog>>()

const FONT_SIZES = [12, 13, 14, 16]
const fontSize = ref(14)

let term: Terminal | null = null
let fit: FitAddon | null = null
let ws: WebSocket | null = null
let pingTimer: number | null = null
let resizeObserver: ResizeObserver | null = null
let resizeDebounce: number | null = null
let connectTimer: number | null = null
let riskResolver: ((ok: boolean) => void) | null = null
// 风险框的结论先暂存,等 @closed 再结算;null 表示当前没有待结算的结论。
let riskVerdict: boolean | null = null

const CONNECT_TIMEOUT_MS = 15000
const riskVisible = ref(false)

// 终端历史滚动:用户向上滚出底部时显示「回到底部」
const scrolledUp = ref(false)
let viewportEl: HTMLElement | null = null
let touchLastY: number | null = null

const dark = useDark()

const statusText = computed(() => {
  if (status.value === 'idle') return t('server.ssh.statusIdle')
  if (status.value === 'connecting') return t('server.ssh.statusConnecting')
  if (status.value === 'connected') return t('server.ssh.statusConnected')
  return t('server.ssh.statusClosed')
})

// —— MAGI 状态单元：会话状态是本页唯一要紧的状态,置顶并放大 ——
// 徽章值走短码（仪表盘读数），说明文字走中文状态。
const sessionTone = computed<'ok' | 'danger' | 'accent' | 'idle'>(() => {
  if (status.value === 'connected') return 'ok'
  if (status.value === 'closed') return 'danger'
  if (status.value === 'connecting') return 'accent'
  return 'idle'
})

const sessionValue = computed(() => {
  if (status.value === 'connected') return 'ONLINE'
  if (status.value === 'connecting') return 'SYNC'
  if (status.value === 'closed') return 'DOWN'
  return 'IDLE'
})

const sessionHint = computed(() => statusText.value)

/** 终端卡片头部的连接态徽章语气,跟六边形单元同一套配色。 */
const statusTagClass = computed(() => {
  if (status.value === 'connected') return 'phi-srv-tag--ok'
  if (status.value === 'closed') return 'phi-srv-tag--danger'
  if (status.value === 'connecting') return 'phi-srv-tag--warn'
  return 'phi-srv-tag--muted'
})

const mainButtonType = computed<'primary' | 'warning' | undefined>(() => {
  if (status.value === 'closed') return 'warning'
  if (status.value === 'connected') return undefined
  return 'primary'
})

const mainButtonIcon = computed(() => {
  if (status.value === 'connected') return SwitchButton
  if (status.value === 'closed') return RefreshRight
  return VideoPlay
})

const mainButtonText = computed(() => {
  if (status.value === 'connected') return t('server.ssh.disconnect')
  if (status.value === 'closed') return t('server.ssh.reconnect')
  return t('server.ssh.startButton')
})

// ---- 连接目标 ----
// 目标目录由服务端下发,前端只负责展示与透传 id;不推导任何主机地址。
const targets = ref<SshTarget[]>([])
const selectedTarget = ref('')
const targetsLoading = ref(false)
const targetsError = ref('')
// 授权框打开期间也锁住目标:双人码批的是当时展示的那个目标。
const authPending = ref(false)

const currentTarget = computed(
  () => targets.value.find((item) => item.id === selectedTarget.value) ?? null
)
// display 是服务端给的完整展示串,缺失时退回 label,再缺就用占位符。
const targetDisplay = computed(
  () => currentTarget.value?.display || currentTarget.value?.label || '—'
)
const targetLocked = computed(
  () => authPending.value || status.value === 'connecting' || status.value === 'connected'
)

async function loadTargets() {
  targetsLoading.value = true
  targetsError.value = ''
  try {
    const res = await listSshTargets()
    targets.value = (res.data?.data?.targets ?? []).filter((item) => Boolean(item?.id))
    const preferred = res.data?.data?.default
    if (!targets.value.some((item) => item.id === selectedTarget.value)) {
      selectedTarget.value =
        (preferred && targets.value.find((item) => item.id === preferred)?.id) ||
        targets.value[0]?.id ||
        ''
    }
    if (!targets.value.length) {
      targetsError.value = t('server.ssh.noTargets')
    }
  } catch {
    targets.value = []
    selectedTarget.value = ''
    targetsError.value = t('server.ssh.targetLoadFailed')
  } finally {
    targetsLoading.value = false
  }
}

// 切换目标只换目标,不自动连接;已有会话必须先干净断开并清屏。
function resetForTargetChange() {
  cancelConnectTimeout()
  teardown()
  term?.reset()
  status.value = 'idle'
  banner.value = ''
  scrolledUp.value = false
}

watch(selectedTarget, (next, previous) => {
  if (next === previous) return
  if (status.value === 'idle' && !ws) return
  resetForTargetChange()
})

function warnedKey(): string {
  return `zv.ssh.warned.${String(userStore._id || 'unknown')}`
}

function confirmRootRisk(): Promise<boolean> {
  return new Promise((resolve) => {
    riskResolver = resolve
    riskVerdict = null
    riskVisible.value = true
  })
}

// 这里只决定结论并让框离场,不 resolve:
// 真正的结算必须等 @closed(离场动画结束、DOM 已移除)。
// 否则风险框与紧随其后的授权框会同批渲染,ElDialog 的 overlay 刚建好就被拆掉,
// 表现为"点了我已知悉却什么也没发生",服务端根本收不到授权请求。
function closeRisk(ok: boolean) {
  riskVerdict = ok
  riskVisible.value = false
}

// ElDialog 关闭动画结束后触发。resolver 只消费一次,重复 closed 直接忽略(防重入)。
function onRiskClosed() {
  if (!riskResolver) return
  const verdict = riskVerdict ?? false // 右上角 X / ESC 不会走 ack/cancel,按取消结算
  const resolve = riskResolver
  riskResolver = null
  riskVerdict = null
  resolve(verdict)
}

function cancelRisk() {
  closeRisk(false)
}

function ackRisk() {
  localStorage.setItem(warnedKey(), '1')
  closeRisk(true)
}

const xtermTheme = computed(() => ({
  background: dark.value ? '#0f0f0f' : '#f2f2f2',
  foreground: dark.value ? '#f2f2f2' : '#111111',
  cursor: dark.value ? '#f2f2f2' : '#111111',
  cursorAccent: dark.value ? '#0f0f0f' : '#f2f2f2',
  selectionBackground: dark.value ? '#3a3a3a' : '#d4d4d4',
  black: '#111111',
  red: '#b42318',
  green: '#1a7f37',
  yellow: '#9a6700',
  blue: '#0969da',
  magenta: '#8250df',
  cyan: '#1b7c83',
  white: '#d4d4d4',
  brightBlack: '#666666',
  brightWhite: '#ffffff'
}))

watch(xtermTheme, (value) => {
  if (term) {
    term.options.theme = value
  }
})

watch(fontSize, (value) => {
  if (term) {
    term.options.fontSize = value
    fitTerminal()
  }
})

function ensureTerminal() {
  if (!containerRef.value) return
  if (!term) {
    term = new Terminal({
      fontFamily:
        'ui-monospace, "JetBrains Mono", SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace',
      fontSize: fontSize.value,
      lineHeight: 1.3,
      cursorBlink: false,
      convertEol: false,
      scrollback: 5000,
      theme: xtermTheme.value
    })
    fit = new FitAddon()
    term.loadAddon(fit)
    term.open(containerRef.value)
    term.attachCustomKeyEventHandler(handleTerminalKey)
    term.onData((data) => {
      sendFrame({ type: 'input', data })
    })
    attachScrollHelpers(containerRef.value)
  }
  // 尺寸观察必须每次都挂:teardown() 会断开观察者,
  // 而终端实例是复用的,不重挂的话重连后高度变化就不会再 fit。
  // 竖屏下终端高度由 flex 分配,容器内容盒尺寸一变这里就会收到通知。
  resizeObserver?.disconnect()
  resizeObserver = new ResizeObserver(() => fitTerminal())
  resizeObserver.observe(containerRef.value)
}

function fitTerminal() {
  if (!fit || !term) return
  try {
    fit.fit()
  } catch {
    return
  }
  updateScrollState()
  if (ws && ws.readyState === WebSocket.OPEN) {
    sendFrame({ type: 'resize', cols: term.cols, rows: term.rows })
  }
}

// ---- 历史滚动辅助 ----
// xterm 默认滚轮就能翻 scrollback;但远端程序开启鼠标上报后,滚轮会被转成
// 应用按键而不是翻历史,备用缓冲(如 vim/htop)必须继续交给应用,所以只在
// 「鼠标上报开启 + 普通缓冲」的组合下拦截滚轮自己翻历史。
function updateScrollState() {
  if (!term) {
    scrolledUp.value = false
    return
  }
  try {
    const buffer = term.buffer.active
    scrolledUp.value =
      buffer.type === 'normal' && buffer.viewportY < buffer.length - term.rows - 1
  } catch {
    return
  }
}

function handleTerminalKey(event: KeyboardEvent): boolean {
  if (event.type !== 'keydown' || !term) {
    return true
  }
  if (event.shiftKey && !event.ctrlKey && event.code === 'PageUp') {
    term.scrollPages(-1)
    updateScrollState()
    return false
  }
  if (event.shiftKey && !event.ctrlKey && event.code === 'PageDown') {
    term.scrollPages(1)
    updateScrollState()
    return false
  }
  if (event.ctrlKey && event.shiftKey && (event.code === 'ArrowUp' || event.code === 'ArrowDown')) {
    term.scrollLines(event.code === 'ArrowUp' ? -1 : 1)
    updateScrollState()
    return false
  }
  return true
}

function handleTerminalWheel(event: WheelEvent) {
  if (!term) return
  try {
    const tracking = term.modes.mouseTrackingMode
    if (!tracking || tracking === 'none' || term.buffer.active.type !== 'normal') return
    event.stopPropagation()
    event.preventDefault()
    term.scrollLines(event.deltaY > 0 ? 3 : -3)
    updateScrollState()
  } catch {
    return
  }
}

function handleTerminalTouchStart(event: TouchEvent) {
  try {
    touchLastY = event.touches.length === 1 ? event.touches[0].clientY : null
  } catch {
    touchLastY = null
  }
}

function handleTerminalTouchMove(event: TouchEvent) {
  if (touchLastY === null || !term) return
  try {
    if (term.buffer.active.type !== 'normal') {
      touchLastY = null
      return
    }
    const y = event.touches[0]?.clientY
    if (y === undefined) return
    const lines = Math.round((touchLastY - y) / 14)
    if (lines !== 0) {
      event.preventDefault()
      term.scrollLines(lines)
      touchLastY = y
      updateScrollState()
    }
  } catch {
    touchLastY = null
  }
}

function handleTerminalTouchEnd() {
  touchLastY = null
}

function scrollToTermBottom() {
  if (!term) return
  term.scrollToBottom()
  term.focus()
  updateScrollState()
}

function attachScrollHelpers(container: HTMLDivElement) {
  container.addEventListener('wheel', handleTerminalWheel, { capture: true, passive: false })
  container.addEventListener('touchstart', handleTerminalTouchStart, { passive: true })
  container.addEventListener('touchmove', handleTerminalTouchMove, { passive: false })
  container.addEventListener('touchend', handleTerminalTouchEnd, { passive: true })
  viewportEl = container.querySelector('.xterm-viewport')
  // scroll 只读滚动位置刷新 UI 状态、绝不 preventDefault，passive 让
  // 浏览器不必等主线程应答即可平滑滚动（终端长输出滚动更顺）。
  viewportEl?.addEventListener('scroll', updateScrollState, { passive: true })
}

function detachScrollHelpers() {
  const container = containerRef.value
  if (container) {
    container.removeEventListener('wheel', handleTerminalWheel, { capture: true } as EventListenerOptions)
    container.removeEventListener('touchstart', handleTerminalTouchStart)
    container.removeEventListener('touchmove', handleTerminalTouchMove)
    container.removeEventListener('touchend', handleTerminalTouchEnd)
  }
  viewportEl?.removeEventListener('scroll', updateScrollState)
  viewportEl = null
  touchLastY = null
  scrolledUp.value = false
}

function sendFrame(payload: Record<string, unknown>) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify(payload))
  }
}

function startPing() {
  stopPing()
  pingTimer = window.setInterval(() => {
    sendFrame({ type: 'ping' })
  }, 25000)
}

function stopPing() {
  if (pingTimer !== null) {
    window.clearInterval(pingTimer)
    pingTimer = null
  }
}

interface OutFrame {
  type: string
  data?: string
  code?: number
  detail?: string
}

function handleFrame(event: MessageEvent) {
  let frame: OutFrame | null = null
  try {
    frame = JSON.parse(event.data as string) as OutFrame
  } catch {
    return
  }
  if (!frame) return
  if (frame.type === 'output' && typeof frame.data === 'string') {
    term?.write(frame.data)
    return
  }
  if (frame.type === 'exit') {
    banner.value = t('server.ssh.exitCode', { code: frame.code ?? 0 })
    status.value = 'closed'
    return
  }
  if (frame.type === 'error') {
    banner.value = t('server.ssh.outputError', { detail: frame.detail || '' })
    return
  }
  // pong / 其它帧静默
}

// 关闭码映射(后端裁定 ssh_bridge.py WS_CLOSE_* 常量):呈现层用
function closeReason(event: CloseEvent): string | null {
  switch (event.code) {
    case 4401:
      return t('server.ssh.wsErrorTicket')
    case 4429:
      return t('server.ssh.wsErrorLimit')
    case 4408:
      return t('server.ssh.wsErrorIdle')
    case 4500:
      return t('server.ssh.wsErrorFailure')
    default:
      return null
  }
}

function cancelConnectTimeout() {
  if (connectTimer !== null) {
    window.clearTimeout(connectTimer)
    connectTimer = null
  }
}

function armConnectTimeout() {
  cancelConnectTimeout()
  connectTimer = window.setTimeout(() => {
    connectTimer = null
    if (status.value === 'connecting') {
      teardown()
      status.value = 'closed'
      banner.value = t('server.ssh.connectTimeout')
      ElMessage.error(t('server.ssh.connectTimeout'))
    }
  }, CONNECT_TIMEOUT_MS)
}

function teardown() {
  stopPing()
  if (resizeObserver) {
    resizeObserver.disconnect()
    resizeObserver = null
  }
  if (ws) {
    ws.onopen = null
    ws.onmessage = null
    ws.onclose = null
    if (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING) {
      ws.close(1000)
    }
    ws = null
  }
}

function startTerminal(voirTicket: string) {
  teardown()
  status.value = 'connecting'
  banner.value = ''
  ensureTerminal()
  term?.reset()
  scrolledUp.value = false
  term?.focus()

  const url = buildSshWsUrl(voirTicket)
  ws = new WebSocket(url)
  ws.onopen = () => {
    cancelConnectTimeout()
    status.value = 'connected'
    banner.value = ''
    startPing()
    fitTerminal()
  }
  ws.onmessage = (event) => handleFrame(event)
  ws.onclose = (event) => {
    cancelConnectTimeout()
    const reason = closeReason(event)
    if (status.value !== 'closed') {
      status.value = 'closed'
    }
    stopPing()
    if (event.code !== 1000 && reason) {
      banner.value = reason
      ElMessage.warning(reason)
    }
    ws = null
  }
}

function disconnect() {
  // 未连接时没有可断的会话,重复点击不应再动一次状态机
  if (status.value !== 'connected') return
  cancelConnectTimeout()
  if (ws) {
    ws.close(1000)
  }
  status.value = 'closed'
}

async function openTerminal() {
  // 连接中 / 授权码待确认时忽略二次点击：
  // 双击会同时开出两个授权框、拿到两张票据,后建立的会话会把前一个顶掉。
  if (status.value === 'connecting' || authPending.value) return
  banner.value = ''
  const targetId = selectedTarget.value
  const target = targets.value.find((item) => item.id === targetId)
  if (!targetId) {
    ElMessage.warning(t('server.ssh.targetRequired'))
    return
  }
  // 从点下到 ticket 落地全程锁目标：双人码批的是这里展示的那台，
  // 不能在授权框关闭后、POST 返回前被切走。
  authPending.value = true
  try {
    let dialog = authRef.value
    if (!dialog) {
      await nextTick()
      dialog = authRef.value
    }
    if (!dialog) {
      ElMessage.error(t('server.ssh.authDialogNotReady'))
      console.error(
        '[ssh] ZSystemAuthDialog ref unavailable (authRef.value is empty after nextTick)'
      )
      return
    }
    if (!localStorage.getItem(warnedKey())) {
      const ok = await confirmRootRisk()
      if (!ok) return
    }
    const code = await dialog.open(
      t('server.ssh.authScene', {
        target: target?.display || target?.label || targetId
      })
    )
    if (!code) {
      status.value = 'idle'
      return
    }
    try {
      const res = await createSshSession(code, targetId)
      const ticket = res.data?.data?.ticket
      if (!ticket) {
        dialog.fail(t('server.ssh.wsErrorTicket'))
        ElMessage.error(t('server.ssh.ticketMissing'))
        return
      }
      dialog.succeed()
      startTerminal(ticket)
      armConnectTimeout()
    } catch (error) {
      const message = resolveErrorMessage(error, 'server.auth.failed')
      dialog.fail(message)
      ElMessage.error(message)
    }
  } finally {
    authPending.value = false
  }
}

// ---- AI 命令生成面板 ----
// 后端只返回命令文本,一句都不执行;真正的「写进终端」必须由人点按钮触发。
const suggestVisible = ref(false)
const suggestIntent = ref('')
const suggestContext = ref('')
const suggestModel = ref('')
const suggestThinking = ref<AiThinkingLevel | ''>('')
const aiModels = ref<string[]>([])
const aiModelsLoaded = ref(false)
const suggestLoading = ref(false)
const suggestError = ref('')
const suggestCommand = ref('')
const suggestExplanation = ref('')
const insertAndRun = ref(false)

const AI_ERROR_KEYS: Record<string, string> = {
  AI_NO_PROVIDER: 'server.ssh.suggest.error.noProvider',
  model_not_found: 'server.ssh.suggest.error.modelNotFound',
  AI_BUSY: 'server.ssh.suggest.error.busy',
  insufficient_quota: 'server.ssh.suggest.error.quota',
  AI_RATE_LIMITED: 'server.ssh.suggest.error.rateLimited',
  AI_TIMEOUT: 'server.ssh.suggest.error.timeout',
  AI_AGENT_BAD_ARGUMENT: 'server.ssh.suggest.error.badArgument'
}

function suggestErrorText(error: unknown): string {
  const detail = (error as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
  const code = typeof detail === 'string' ? detail.trim() : ''
  const key = AI_ERROR_KEYS[code]
  // 机器可读的 AI_* 码不直接示人,一律翻成中文说明
  return t(key ?? 'server.ssh.suggest.error.unknown')
}

// 模型列表只是加分项:取不到就隐藏下拉,生成仍走后端默认模型
async function loadAiModels() {
  if (aiModelsLoaded.value) return
  aiModelsLoaded.value = true
  try {
    const list = await listModels()
    aiModels.value = (list.data ?? []).map((item) => item.id).filter(Boolean)
  } catch {
    aiModels.value = []
  }
}

watch(suggestVisible, (visible) => {
  if (visible) void loadAiModels()
})

async function generateSuggestions() {
  const intent = suggestIntent.value.trim()
  if (!intent) {
    suggestError.value = t('server.ssh.suggest.intentRequired')
    return
  }
  if (intent.length > 1000) {
    suggestError.value = t('server.ssh.suggest.intentTooLong')
    return
  }
  suggestLoading.value = true
  suggestError.value = ''
  suggestCommand.value = ''
  suggestExplanation.value = ''
  try {
    const res = await suggestSshCommand(
      intent,
      suggestContext.value.trim() || undefined,
      suggestModel.value || undefined,
      suggestThinking.value || undefined
    )
    suggestCommand.value = res.data?.data?.command ?? ''
    suggestExplanation.value = res.data?.data?.explanation ?? ''
  } catch (error) {
    suggestError.value = suggestErrorText(error)
  } finally {
    suggestLoading.value = false
  }
}

async function copySuggested() {
  if (!suggestCommand.value) return
  try {
    await navigator.clipboard.writeText(suggestCommand.value)
    ElMessage.success(t('server.ssh.suggest.copied'))
  } catch {
    ElMessage.error(t('server.ssh.suggest.copyFailed'))
  }
}

/** 本地时间戳，只用于文件名（YYYYMMDD-HHmmss）。 */
function suggestStamp(date = new Date()): string {
  const pad = (value: number) => String(value).padStart(2, '0')
  return (
    `${date.getFullYear()}${pad(date.getMonth() + 1)}${pad(date.getDate())}` +
    `-${pad(date.getHours())}${pad(date.getMinutes())}${pad(date.getSeconds())}`
  )
}

// 把建议拼成一个可直接审阅的 .sh：注释写清来源与说明，正文只有那一条命令。
// 纯前端生成，不经过后端，也不需要终端已连接。
function downloadSuggestedSh() {
  const command = suggestCommand.value.trim()
  if (!command) return
  try {
    const notes = [
      'AI 生成的 SSH 命令建议；执行前请逐字核对，脚本不会自动执行。',
      ...(suggestExplanation.value.trim() ? suggestExplanation.value.trim().split('\n') : [])
    ]
    const body = [
      '#!/usr/bin/env bash',
      ...notes.map((line) => `# ${line}`),
      '',
      command,
      ''
    ].join('\n')
    const blob = new Blob([body], { type: 'text/x-shellscript;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = `ssh-suggest-${suggestStamp()}.sh`
    anchor.click()
    setTimeout(() => URL.revokeObjectURL(url), 3000)
    ElMessage.success(t('server.ssh.suggest.downloadShDone'))
  } catch {
    ElMessage.error(t('server.ssh.suggest.downloadShFailed'))
  }
}

// 写进终端的两条路径都只发一帧 input,且都不关面板、不动会话状态:
// 默认 command(不回车);只有勾了「插入并回车」并二次确认才追加 \r。
async function insertSuggested() {
  if (status.value !== 'connected') {
    suggestError.value = t('server.ssh.suggest.insertNotConnected')
    return
  }
  const command = suggestCommand.value.trim()
  if (!command) return
  let runImmediately = false
  if (insertAndRun.value) {
    try {
      await ElMessageBox.confirm(
        t('server.ssh.suggest.confirmRun'),
        t('server.ssh.suggest.confirmRunTitle'),
        { type: 'warning' }
      )
      runImmediately = true
    } catch {
      return
    }
  }
  sendFrame({ type: 'input', data: runImmediately ? command + '\r' : command })
  term?.focus()
  ElMessage.success(
    t(runImmediately ? 'server.ssh.suggest.insertRunDone' : 'server.ssh.suggest.insertDone')
  )
}

// ---- 场景智能分析 ----
// 只读语义:结果展示在弹窗里,既不自动执行命令,也不写回任何文件。
const analysisVisible = ref(false)
const analysisContext = ref('')

interface BufferLineLike {
  translateToString?: (trimRight?: boolean) => string
}

interface BufferLike {
  length: number
  getLine?: (index: number) => BufferLineLike | undefined
}

// 取终端缓冲最近的内容作参考;取不到(未连接 / stub)就给空,弹窗会提示补一段
function terminalContext(limit = SCENE_CONTEXT_LIMIT): string {
  if (!term) return ''
  try {
    const buffer = term.buffer.active as unknown as BufferLike
    if (!buffer || typeof buffer.getLine !== 'function') return ''
    const getLine = buffer.getLine.bind(buffer)
    const collected: string[] = []
    let total = 0
    for (let index = buffer.length - 1; index >= 0 && total < limit; index -= 1) {
      const line = getLine(index)
      const raw = line && typeof line.translateToString === 'function'
        ? line.translateToString(true)
        : ''
      const text = raw.replace(/\s+$/, '')
      if (!text) continue
      collected.unshift(text)
      total += text.length + 1
    }
    return collected.join('\n').slice(-limit)
  } catch {
    return ''
  }
}

function openAnalysis() {
  analysisContext.value = terminalContext()
  analysisVisible.value = true
}

function onWindowResize() {
  if (resizeDebounce !== null) {
    window.clearTimeout(resizeDebounce)
  }
  resizeDebounce = window.setTimeout(() => fitTerminal(), 150)
}

onMounted(() => {
  window.addEventListener('resize', onWindowResize)
  void loadTargets()
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', onWindowResize)
  if (resizeDebounce !== null) {
    window.clearTimeout(resizeDebounce)
  }
  cancelConnectTimeout()
  teardown()
  detachScrollHelpers()
})
</script>

<template>
  <div class="ssh-page phi-srv-page">
    <!-- 状态单元:会话状态是这一页唯一要紧的状态,置顶放大 -->
    <div class="phi-srv-strip" :class="{ 'phi-srv-strip--danger': status === 'closed' }">
      <ZHexUnit
        :label="t('server.ssh.unitLabel')"
        :value="sessionValue"
        :tone="sessionTone"
        size="lg"
        :hint="sessionHint"
      />
      <div class="phi-srv-strip__cell">
        <div class="phi-srv-unit__body">
          <span class="phi-srv-unit__label">{{ t('server.ssh.unitTargetNow') }}</span>
          <span class="phi-srv-unit__hint phi-srv-unit__hint--muted font-mono">{{
            targetDisplay
          }}</span>
        </div>
      </div>
      <div class="phi-srv-strip__cell">
        <div class="phi-srv-unit__body">
          <span class="phi-srv-unit__label">{{ t('server.ssh.unitLimit') }}</span>
          <span class="phi-srv-unit__hint phi-srv-unit__hint--muted">{{
            t('server.ssh.limitNote')
          }}</span>
        </div>
      </div>
    </div>

    <!-- 终端卡片:header 放状态 + 目标 + 字号 + 动作,body 放终端 -->
    <ElCard shadow="never" class="ssh-card">
      <template #header>
        <div class="flex flex-wrap items-center gap-x-4 gap-y-2">
          <span class="phi-srv-tag" :class="statusTagClass">{{ statusText }}</span>
          <span class="phi-srv-tag phi-srv-tag--muted">{{ t('server.ssh.tagTerminal') }}</span>
          <span class="flex items-center gap-1.5 font-mono text-sm">
            <ElIcon :size="18"><Connection /></ElIcon>
            {{ targetDisplay }}
          </span>
          <ElSelect
            v-model="selectedTarget"
            class="target-select w-44"
            :placeholder="t('server.ssh.targetPlaceholder')"
            :aria-label="t('server.ssh.targetLabel')"
            :disabled="targetLocked || targetsLoading"
            :loading="targetsLoading"
          >
            <ElOption
              v-for="target in targets"
              :key="target.id"
              :label="target.label"
              :value="target.id"
            />
          </ElSelect>
          <div class="ml-auto flex flex-wrap items-center gap-2">
            <ElButton size="large" :icon="MagicStick" @click="suggestVisible = true">
              {{ t('server.ssh.suggest.open') }}
            </ElButton>
            <ElButton size="large" :icon="MagicStick" @click="openAnalysis">
              {{ t('server.ssh.analysis.open') }}
            </ElButton>
            <ElSelect v-model="fontSize" class="w-24" :aria-label="t('server.ssh.fontLabel')">
              <ElOption v-for="size in FONT_SIZES" :key="size" :label="String(size)" :value="size" />
            </ElSelect>
            <ElButton
              size="large"
              :type="mainButtonType"
              :icon="mainButtonIcon"
              :disabled="status === 'connecting' || authPending || (!selectedTarget && status !== 'connected')"
              @click="status === 'connected' ? disconnect() : openTerminal()"
            >
              {{ mainButtonText }}
            </ElButton>
          </div>
        </div>
      </template>

      <div class="ssh-body flex flex-col gap-4">
        <!-- 目标目录异常:加载失败或为空,必须显式说明,不能静默禁用 -->
        <ElAlert
          v-if="targetsError"
          type="warning"
          show-icon
          :closable="false"
          :title="targetsError"
        />
        <!-- 断线 / 出错提示 -->
        <ElAlert
          v-if="status === 'closed' && (banner || statusText)"
          type="error"
          show-icon
          :closable="false"
          :title="banner || t('server.ssh.statusClosed')"
          :description="t('server.ssh.closedAdvice')"
        />
        <ElAlert
          v-else-if="status === 'connected' && banner"
          type="warning"
          show-icon
          :closable="false"
          :title="banner"
        />

        <!-- 终端区:connecting 骨架 / 空态说明 / 终端本体 -->
        <div class="terminal-stage relative">
          <!-- 连接中:终端框顶部跑一条 HUD 加载条,状态一眼可见 -->
          <div v-if="status === 'connecting'" class="phi-srv-loading"></div>
          <div
            ref="containerRef"
            class="terminal-box p-2"
          >
            <!-- 连接中骨架 -->
            <ElSkeleton v-if="status === 'connecting'" :rows="4" animated />
            <!-- 未连接空态 -->
            <div
              v-else-if="status === 'idle'"
              class="flex h-full flex-col items-center justify-center gap-2 px-6 text-center"
            >
              <span class="text-base font-semibold">{{ t('server.ssh.emptyTitle') }}</span>
              <p class="max-w-md text-sm leading-6 opacity-75">
                {{ t('server.ssh.emptyDesc') }}
              </p>
              <p class="max-w-md text-xs leading-5 ssh-danger">
                {{ t('server.ssh.riskHint') }}
              </p>
              <ElButton
                size="large"
                type="primary"
                :icon="Connection"
                class="mt-2"
                :disabled="!selectedTarget || targetsLoading || authPending"
                @click="openTerminal"
              >
                {{ t('server.ssh.startButton') }}
              </ElButton>
            </div>
          </div>
          <!-- 向上翻过历史后出现,点击回到底部并聚焦终端 -->
          <button
            v-if="scrolledUp"
            type="button"
            class="back-to-bottom absolute z-10 flex items-center gap-1 rounded-full border border-current/15 bg-[var(--yg-bg-page)] px-3 py-1.5 text-xs opacity-80 shadow-md transition hover:opacity-100"
            style="color: var(--yg-text-tertiary)"
            :title="t('server.ssh.scrollToBottom')"
            @click="scrollToTermBottom"
          >
            <ElIcon :size="12"><ArrowDown /></ElIcon>
            {{ t('server.ssh.scrollToBottom') }}
          </button>
        </div>
      </div>
    </ElCard>
    <ZSystemAuthDialog ref="authRef" />

    <!-- 管理员首次使用警告:确认后记入 localStorage,之后不再出现 -->
    <ElDialog
      v-model="riskVisible"
      :title="t('server.ssh.riskTitle')"
      width="500px"
      :close-on-click-modal="false"
      append-to-body
      @closed="onRiskClosed"
    >
      <div class="flex flex-col gap-3 text-sm leading-6">
        <p>{{ t('server.ssh.riskPower') }}</p>
        <p>{{ t('server.ssh.riskScope') }}</p>
        <p class="text-red-600 dark:text-red-400">{{ t('server.ssh.riskConsequence') }}</p>
      </div>
      <template #footer>
        <ElButton size="large" @click="cancelRisk">{{ t('server.ssh.riskCancel') }}</ElButton>
        <ElButton size="large" type="primary" @click="ackRisk">
          {{ t('server.ssh.riskAck') }}
        </ElButton>
      </template>
    </ElDialog>

    <!-- AI 生成命令:只生成文本,写不写进终端由人点按钮决定 -->
    <ElDialog
      v-model="suggestVisible"
      :title="t('server.ssh.suggest.title')"
      :width="'min(520px, calc(100vw - 32px))'"
      append-to-body
    >
      <div class="suggest">
        <label class="suggest__label" for="ssh-suggest-intent">
          {{ t('server.ssh.suggest.intent') }}
        </label>
        <ElInput
          id="ssh-suggest-intent"
          v-model="suggestIntent"
          type="textarea"
          :rows="3"
          maxlength="1000"
          show-word-limit
          :placeholder="t('server.ssh.suggest.intentPlaceholder')"
        />

        <label class="suggest__label" for="ssh-suggest-context">
          {{ t('server.ssh.suggest.context') }}
        </label>
        <ElInput
          id="ssh-suggest-context"
          v-model="suggestContext"
          maxlength="2000"
          show-word-limit
          :placeholder="t('server.ssh.suggest.contextPlaceholder')"
        />

        <ZAiModelThinkingPicker
          :models="aiModels"
          :model="suggestModel"
          :thinking="suggestThinking"
          :disabled="suggestLoading"
          @update:model="suggestModel = $event"
          @update:thinking="suggestThinking = $event"
        />

        <div class="suggest__actions">
          <ElButton
            type="primary"
            :loading="suggestLoading"
            :disabled="!suggestIntent.trim()"
            @click="generateSuggestions"
          >
            {{ t('server.ssh.suggest.generate') }}
          </ElButton>
        </div>

        <p v-if="suggestError" class="suggest__error" role="alert">{{ suggestError }}</p>

        <div v-if="suggestCommand" class="suggest__result">
          <span class="suggest__label">{{ t('server.ssh.suggest.result') }}</span>
          <textarea
            v-model="suggestCommand"
            class="suggest__command"
            rows="4"
            spellcheck="false"
          ></textarea>
          <p class="suggest__explanation">{{ suggestExplanation }}</p>
          <div class="suggest__actions">
            <ElButton :icon="CopyDocument" @click="copySuggested">
              {{ t('server.ssh.suggest.copy') }}
            </ElButton>
            <ElButton :icon="Download" @click="downloadSuggestedSh">
              {{ t('server.ssh.suggest.downloadSh') }}
            </ElButton>
            <ElButton
              type="primary"
              :disabled="status !== 'connected'"
              @click="insertSuggested"
            >
              {{ t('server.ssh.suggest.insert') }}
            </ElButton>
          </div>
          <ElCheckbox v-model="insertAndRun" :disabled="status !== 'connected'">
            {{ t('server.ssh.suggest.insertAndRun') }}
          </ElCheckbox>
          <span class="suggest__hint">{{ t('server.ssh.suggest.insertHint') }}</span>
        </div>
        <p v-else class="suggest__empty">
          {{ suggestLoading ? t('server.ssh.statusConnecting') : t('server.ssh.suggest.empty') }}
        </p>

        <p class="suggest__hint">{{ t('server.ssh.suggest.note') }}</p>
      </div>
    </ElDialog>

    <ZSceneRunDialog
      v-model="analysisVisible"
      scene="terminal"
      :title="t('server.aiScene.title')"
      :context="analysisContext"
    />
  </div>
</template>

<style scoped>
/* 全局 flat.css 已收口样式,本页只保留等宽字体继承 */
textarea,
input {
  font-family: inherit;
}

/* ---------- AI 生成命令面板 ---------- */
.suggest {
  display: flex;
  flex-direction: column;
  gap: var(--yg-space-2);
}

.suggest__label {
  font-size: var(--yg-font-size-xs);
  color: var(--yg-text-tertiary);
}

.suggest__select {
  width: 100%;
}

.suggest__actions {
  display: flex;
  flex-wrap: wrap;
  gap: var(--yg-space-2);
}

.suggest__error {
  margin: 0;
  font-size: var(--yg-font-size-sm);
  line-height: var(--yg-line-height-normal);
  color: var(--yg-text-danger);
}

.suggest__result {
  display: flex;
  flex-direction: column;
  gap: var(--yg-space-2);
  margin-top: var(--yg-space-2);
}

/* 命令文本：等宽 + 可编辑，执行前逐字核对 */
.suggest__command {
  width: 100%;
  padding: var(--yg-space-2) var(--yg-space-3);
  border: 1px solid var(--yg-border-subtle);
  border-radius: var(--yg-radius-md);
  background: var(--yg-bg-surface-sunken);
  color: var(--yg-text-primary);
  font-family: var(--yg-font-mono);
  font-size: var(--yg-font-size-sm);
  line-height: var(--yg-line-height-normal);
  resize: vertical;
}

.suggest__explanation {
  margin: 0;
  font-size: var(--yg-font-size-sm);
  line-height: var(--yg-line-height-normal);
  color: var(--yg-text-secondary);
  white-space: pre-wrap;
}

.suggest__hint,
.suggest__empty {
  margin: 0;
  font-size: var(--yg-font-size-xs);
  line-height: var(--yg-line-height-normal);
  color: var(--yg-text-tertiary);
}

/* 危险提示(root 权限):用主题的强调色,与状态条同一套危险色 */
.ssh-danger {
  color: var(--phi-srv-danger);
}

/* ---------- 终端卡片:高度跟着实际可用空间走 ----------
   竖屏时底部是悬浮药丸导航,外壳(App.vue)已经把路由视图的底距让出来,
   终端只要填满剩下的高度,底边就一定在药丸上方,不再靠 100vh - 固定 rem 估算。 */
.ssh-page {
  flex: 1 1 auto;
  min-height: 0;
}

.ssh-card {
  display: flex;
  flex-direction: column;
  flex: 1 1 auto;
  min-height: 0;
}

.ssh-card :deep(.el-card__body) {
  display: flex;
  flex-direction: column;
  flex: 1 1 auto;
  min-height: 0;
}

.ssh-body {
  flex: 1 1 auto;
  min-height: 0;
}

.terminal-stage {
  position: relative;
  display: flex;
  flex-direction: column;
  flex: 1 1 auto;
  min-height: 0;
}

/* 横屏沿用既有高度算法,桌面观感不变。
   顶部多了一条 MAGI 状态条(六边形徽章 + 读数格),扣减量相应加大。 */
.terminal-box {
  height: calc(100vh - 26rem);
  min-height: 360px;
}

/* 终端框:切角 + 硬阴影,与服务器台其余面板同一套语言。
   只动容器的视觉,xterm 本体与滚动逻辑一律不碰。
   裁剪区向右下各多留 5px:clip-path 会裁掉自身阴影,把阴影带包进裁剪区才留得住。 */
.terminal-box {
  border: 1px solid var(--phi-frame);
  background: var(--yg-bg-page);
  clip-path: polygon(
    0 0,
    calc(100% - 14px) 0,
    100% 14px,
    calc(100% + 5px) calc(14px + 5px),
    calc(100% + 5px) calc(100% + 5px),
    0 calc(100% + 5px)
  );
  box-shadow: 4px 4px 0 var(--phi-shadow);
}

/* 竖屏:高度由 flex 分配,不再用 100vh 减固定 rem 猜高度 */
@media (max-aspect-ratio: 6/5) {
  .terminal-box {
    height: auto;
    min-height: 180px;
    flex: 1 1 auto;
    /* 兜底:即便容器高度算错,也不许高过「视口减去底部让位」,
       保证终端底边永远在底部悬浮药丸上方。
       底部让位直接引用外壳的 --yg-content-bottom-inset(App.vue 在
       .yg-layout__content 上下发,随 DOM 继承),本页不再写第二份数值。
       视口基准跟外壳一致用 100vh,不混用 100dvh,免得两套基准打架。 */
    max-height: calc(100vh - var(--yg-content-bottom-inset, 96px));
  }
}

/* 浮动「回到底部」按钮:贴终端右下角,
   右侧多留 8px 避开 xterm 自己的滚动条。
   底部让位由外壳的 --yg-content-bottom-inset 统一承担(整页已被抬离药丸),
   这里的 env() 只是按钮自己的额外余量,不是第二份让位数值。 */
.back-to-bottom {
  right: calc(var(--yg-space-6) + 8px);
  bottom: var(--yg-space-4);
}

@media (max-aspect-ratio: 6/5) {
  .back-to-bottom {
    bottom: calc(var(--yg-space-4) + env(safe-area-inset-bottom, 0px));
  }
}

/* 终端历史滚动条:xterm 用 .xterm-viewport 内滚,收窄宽度并压低对比度 */
.terminal-box :deep(.xterm) {
  height: 100%;
}

.terminal-box :deep(.xterm-viewport) {
  scrollbar-width: thin;
  scrollbar-color: rgba(128, 128, 128, 0.4) transparent;
}

.terminal-box :deep(.xterm-viewport::-webkit-scrollbar) {
  width: 7px;
  height: 7px;
}

.terminal-box :deep(.xterm-viewport::-webkit-scrollbar-track) {
  background: transparent;
}

.terminal-box :deep(.xterm-viewport::-webkit-scrollbar-thumb) {
  background: rgba(128, 128, 128, 0.4);
  border-radius: 4px;
}

.terminal-box :deep(.xterm-viewport::-webkit-scrollbar-thumb:hover) {
  background: rgba(128, 128, 128, 0.65);
}
</style>
