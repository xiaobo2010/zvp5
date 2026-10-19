<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import '@xterm/xterm/css/xterm.css'
import { useDark } from '@vueuse/core'
import { createSshSession, buildSshWsUrl } from '@/api/system/ssh.ts'
import ZSystemAuthDialog from '@/components/system/ZSystemAuthDialog.vue'
import { useUserStore } from '@/stores/user.ts'
import { Connection, VideoPlay, SwitchButton, RefreshRight } from '@element-plus/icons-vue'

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

const CONNECT_TIMEOUT_MS = 15000
const riskVisible = ref(false)

const dark = useDark()

const statusText = computed(() => {
  if (status.value === 'idle') return t('server.ssh.statusIdle')
  if (status.value === 'connecting') return t('server.ssh.statusConnecting')
  if (status.value === 'connected') return t('server.ssh.statusConnected')
  return t('server.ssh.statusClosed')
})

const statusTagType = computed<'success' | 'info' | 'danger'>(() => {
  if (status.value === 'connected') return 'success'
  if (status.value === 'closed') return 'danger'
  return 'info'
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

function warnedKey(): string {
  return `zv.ssh.warned.${String(userStore._id || 'unknown')}`
}

function confirmRootRisk(): Promise<boolean> {
  return new Promise((resolve) => {
    riskResolver = resolve
    riskVisible.value = true
  })
}

function cancelRisk() {
  riskVisible.value = false
  riskResolver?.(false)
  riskResolver = null
}

function ackRisk() {
  localStorage.setItem(warnedKey(), '1')
  riskVisible.value = false
  riskResolver?.(true)
  riskResolver = null
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
  if (term || !containerRef.value) return
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
  term.onData((data) => {
    sendFrame({ type: 'input', data })
  })
  if (containerRef.value) {
    resizeObserver?.disconnect()
    resizeObserver = new ResizeObserver(() => fitTerminal())
    resizeObserver.observe(containerRef.value)
  }
}

function fitTerminal() {
  if (!fit || !term) return
  try {
    fit.fit()
  } catch {
    return
  }
  if (ws && ws.readyState === WebSocket.OPEN) {
    sendFrame({ type: 'resize', cols: term.cols, rows: term.rows })
  }
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
  cancelConnectTimeout()
  if (ws) {
    ws.close(1000)
  }
  status.value = 'closed'
}

async function openTerminal() {
  banner.value = ''
  let dialog = authRef.value
  if (!dialog) {
    await nextTick()
    dialog = authRef.value
  }
  if (!dialog) {
    ElMessage.error(t('server.ssh.authDialogNotReady'))
    console.error('[ssh] ZSystemAuthDialog ref unavailable (authRef.value is empty after nextTick)')
    return
  }
  if (!localStorage.getItem(warnedKey())) {
    const ok = await confirmRootRisk()
    if (!ok) return
  }
  const code = await dialog.open(t('server.ssh.authScene'))
  if (!code) {
    status.value = 'idle'
    return
  }
  try {
    const res = await createSshSession(code)
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
    const message = errorMessageOf(error)
    dialog.fail(message)
    ElMessage.error(message)
  }
}

function errorMessageOf(error: unknown): string {
  const anyErr = error as { response?: { data?: { detail?: unknown } } }
  const detail = anyErr?.response?.data?.detail
  if (typeof detail === 'string' && detail) return detail
  return t('server.auth.failed')
}

function onWindowResize() {
  if (resizeDebounce !== null) {
    window.clearTimeout(resizeDebounce)
  }
  resizeDebounce = window.setTimeout(() => fitTerminal(), 150)
}

onMounted(() => {
  window.addEventListener('resize', onWindowResize)
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', onWindowResize)
  if (resizeDebounce !== null) {
    window.clearTimeout(resizeDebounce)
  }
  cancelConnectTimeout()
  teardown()
})
</script>

<template>
  <div class="flex flex-col gap-4 pb-2">
    <!-- 终端卡片:header 放状态 + 目标 + 字号 + 动作,body 放终端 -->
    <ElCard shadow="never">
      <template #header>
        <div class="flex flex-wrap items-center gap-x-4 gap-y-2">
          <ElTag :type="statusTagType" effect="light" size="large">{{ statusText }}</ElTag>
          <span class="flex items-center gap-1.5 font-mono text-sm">
            <ElIcon :size="18"><Connection /></ElIcon>
            root@127.0.0.1
          </span>
          <div class="ml-auto flex flex-wrap items-center gap-2">
            <ElSelect v-model="fontSize" class="w-24" :aria-label="t('server.ssh.fontLabel')">
              <ElOption v-for="size in FONT_SIZES" :key="size" :label="String(size)" :value="size" />
            </ElSelect>
            <ElButton
              size="large"
              :type="mainButtonType"
              :icon="mainButtonIcon"
              :disabled="status === 'connecting'"
              @click="status === 'connected' ? disconnect() : openTerminal()"
            >
              {{ mainButtonText }}
            </ElButton>
          </div>
        </div>
      </template>

      <div class="flex flex-col gap-4">
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
        <div
          ref="containerRef"
          class="h-[calc(100vh_-_20rem)] min-h-[360px] bg-[var(--zv-bg)] p-2"
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
            <p class="max-w-md text-xs leading-5 text-red-600 dark:text-red-400">
              {{ t('server.ssh.riskHint') }}
            </p>
            <ElButton
              size="large"
              type="primary"
              :icon="Connection"
              class="mt-2"
              @click="openTerminal"
            >
              {{ t('server.ssh.startButton') }}
            </ElButton>
          </div>
        </div>
      </div>
    </ElCard>
    <p class="text-xs opacity-60">{{ t('server.ssh.limitNote') }}</p>
    <ZSystemAuthDialog ref="authRef" />

    <!-- 管理员首次使用警告:确认后记入 localStorage,之后不再出现 -->
    <ElDialog
      v-model="riskVisible"
      :title="t('server.ssh.riskTitle')"
      width="500px"
      :close-on-click-modal="false"
      append-to-body
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
  </div>
</template>

<style scoped>
/* 全局 flat.css 已收口样式,本页只保留等宽字体继承 */
textarea,
input {
  font-family: inherit;
}
</style>
