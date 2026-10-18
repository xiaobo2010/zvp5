<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import '@xterm/xterm/css/xterm.css'
import { useDark } from '@vueuse/core'
import { createSshSession, buildSshWsUrl } from '@/api/system/ssh.ts'
import ZSystemAuthDialog from '@/components/system/ZSystemAuthDialog.vue'

const { t } = useI18n()

type Status = 'idle' | 'connecting' | 'connected' | 'closed'

const status = ref<Status>('idle')
const banner = ref('')
const containerRef = ref<HTMLDivElement>()
const authRef = ref<InstanceType<typeof ZSystemAuthDialog>>()

let term: Terminal | null = null
let fit: FitAddon | null = null
let ws: WebSocket | null = null
let pingTimer: number | null = null
let resizeObserver: ResizeObserver | null = null
let resizeDebounce: number | null = null

const dark = useDark()
const statusText = computed(() => {
  if (status.value === 'idle') return t('server.ssh.statusIdle')
  if (status.value === 'connecting') return t('server.ssh.statusConnecting')
  if (status.value === 'connected') return t('server.ssh.statusConnected')
  return t('server.ssh.statusClosed')
})

const statusColor = computed(() => {
  if (status.value === 'connected') return 'var(--zv-text)'
  if (status.value === 'connecting') return 'var(--zv-label)'
  return 'var(--zv-label)'
})

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

function ensureTerminal() {
  if (term || !containerRef.value) return
  term = new Terminal({
    fontFamily:
      "ui-monospace, SFMono-Regular, Menlo, Consolas, 'Liberation Mono', monospace",
    fontSize: 13,
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
    return
  }
  if (frame.type === 'error') {
    banner.value = t('server.ssh.outputError', { detail: frame.detail || '' })
    return
  }
  // pong / 其它帧静默
}

// 关闭码映射:后端裁定 services/ssh_bridge.py 常量(system_ssh_router.py 引用)
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
  banner.value = t('server.ssh.connecting')
  ensureTerminal()
  term?.clear()
  term?.focus()

  const url = buildSshWsUrl(voirTicket)
  ws = new WebSocket(url)
  ws.onopen = () => {
    status.value = 'connected'
    banner.value = ''
    startPing()
    fitTerminal()
  }
  ws.onmessage = (event) => handleFrame(event)
  ws.onclose = (event) => {
    const port = closeReason(event)
    status.value = 'closed'
    stopPing()
    if (event.code !== 1000) {
      banner.value = port || t('server.ssh.wsCloseNoun', { code: event.code })
      ElMessage({
        message: banner.value,
        type: 'error',
        grouping: true,
        plain: true
      })
    }
    ws = null
  }
}

async function openAuth(scene: string): Promise<string | null> {
  const dialog = authRef.value
  if (!dialog) return null
  return dialog.open(scene)
}

async function openTerminal() {
  banner.value = ''
  const code = await openAuth(t('server.ssh.authScene'))
  if (!code) return
  try {
    const res = await createSshSession(code)
    const ticket = res.data?.data?.ticket
    if (!ticket) {
      authRef.value?.fail(t('server.ssh.wsErrorTicket'))
      return
    }
    authRef.value?.succeed()
    startTerminal(ticket)
  } catch (error) {
    authRef.value?.fail(errorMessageOf(error))
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
  teardown()
})
</script>

<template>
  <div class="flex flex-col gap-2">
    <div class="flex flex-wrap items-center justify-between gap-2 border border-[var(--zv-hairline)] bg-[var(--zv-surface)] px-3 py-2">
      <div class="flex items-center gap-4">
        <span class="text-[13px] font-medium text-[var(--zv-text)]">{{ statusText }}</span>
        <span class="h-3 w-px bg-[var(--zv-hairline)]"></span>
        <span class="text-[11px] uppercase tracking-wide text-[var(--zv-label)]" :style="{ color: statusColor }">
          {{ statusText }}
        </span>
        <span class="h-3 w-px bg-[var(--zv-hairline)]"></span>
        <span class="text-[12px] text-[var(--zv-label)]">{{ t('server.ssh.limitNote') }}</span>
      </div>
      <div class="flex items-center gap-2">
        <button
          class="border border-[var(--zv-hairline-strong)] bg-[var(--zv-text)] px-3 py-1.5 text-[13px] text-[var(--zv-surface)] transition-colors hover:opacity-80"
          @click="openTerminal"
        >
          {{ t('server.ssh.startButton') }}
        </button>
        <button
          v-if="status === 'closed'"
          class="border border-[var(--zv-hairline-strong)] bg-[var(--zv-surface)] px-3 py-1.5 text-[13px] text-[var(--zv-text)] transition-colors hover:bg-[var(--zv-surface-alt)]"
          @click="openTerminal"
        >
          {{ t('server.ssh.reconnect') }}
        </button>
      </div>
    </div>
    <div v-if="banner" class="border border-[var(--zv-hairline)] bg-[var(--zv-surface-alt)] px-3 py-2 text-[12px] text-[var(--zv-text-sub)]">
      {{ banner }}
    </div>
    <div
      ref="containerRef"
      class="border border-[var(--zv-hairline)] bg-[var(--zv-bg)] p-1"
      style="height: calc(100vh - 220px); min-height: 320px"
    ></div>
    <p class="text-[12px] text-[var(--zv-label)]">{{ t('server.ssh.typeToStart') }}</p>
    <ZSystemAuthDialog ref="authRef" />
  </div>
</template>
