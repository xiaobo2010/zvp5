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
import { Connection, VideoPlay, SwitchButton } from '@element-plus/icons-vue'

const { t } = useI18n()
// 本批 i18n 冻结,新增文案暂用英文正文之外的简短中文硬编码(报告中注明,待放开后迁入 server.ts)
const i18nFontSizeLabel = '字号'
const closedAdvice = '请重新建立双人授权后再接入；若反复失败,检查服务器 sshd 状态或联系另一位管理员。'

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

const dark = useDark()

const statusText = computed(() => {
  if (status.value === 'idle') return t('server.ssh.statusIdle')
  if (status.value === 'connecting') return t('server.ssh.statusConnecting')
  if (status.value === 'connected') return t('server.ssh.statusConnected')
  return t('server.ssh.statusClosed')
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
    status.value = 'connected'
    banner.value = ''
    startPing()
    fitTerminal()
  }
  ws.onmessage = (event) => handleFrame(event)
  ws.onclose = (event) => {
    const reason = closeReason(event)
    if (status.value !== 'closed') {
      status.value = 'closed'
    }
    stopPing()
    if (event.code !== 1000 && reason) {
      banner.value = reason
    }
    ws = null
  }
}

async function openTerminal() {
  banner.value = ''
  const dialog = authRef.value
  if (!dialog) return
  const code = await dialog.open(t('server.ssh.authScene'))
  if (!code) return
  try {
    const res = await createSshSession(code)
    const ticket = res.data?.data?.ticket
    if (!ticket) {
      dialog.fail(t('server.ssh.wsErrorTicket'))
      return
    }
    dialog.succeed()
    startTerminal(ticket)
  } catch (error) {
    dialog.fail(errorMessageOf(error))
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
  <div class="flex flex-col gap-3 pb-2">
    <!-- 页面标题行 -->
    <div class="flex flex-wrap items-end justify-between gap-2 px-1">
      <div class="flex flex-col gap-0.5">
        <h2 class="m-0 text-[22px] font-semibold leading-7 text-[var(--zv-text)]">{{ t('server.ssh.title') }}</h2>
        <span class="text-[12px] text-[var(--zv-label)]">{{ t('server.ssh.limitNote') }}</span>
      </div>
    </div>

    <!-- 工具条:状态 + 目标 + 字号 + 动作(图标+文字,≥34px 高) -->
    <div class="flex flex-wrap items-center gap-x-5 gap-y-2 border border-[var(--zv-hairline)] bg-[var(--zv-surface)] px-4 py-2">
      <div class="flex items-center gap-2">
        <span
          class="inline-block h-2.5 w-2.5 rounded-[2px]"
          :class="{
            'bg-[#1a7f37]': status === 'connected',
            'bg-[#9a6700]': status === 'connecting',
            'bg-[#b42318]': status === 'closed',
            'bg-[var(--zv-hairline-strong)]': status === 'idle'
          }"
        ></span>
        <span class="min-w-[64px] text-[14px] font-medium text-[var(--zv-text)]">{{ statusText }}</span>
      </div>
      <span class="h-5 w-px bg-[var(--zv-hairline)]"></span>
      <div class="flex items-center gap-2 text-[13px] text-[var(--zv-text-sub)]">
        <ElIcon :size="18"><Connection /></ElIcon>
        <span class="font-mono text-[13px]">root@127.0.0.1</span>
      </div>
      <span class="h-5 w-px bg-[var(--zv-hairline)]"></span>
      <div class="flex items-center gap-1.5 text-[12px] text-[var(--zv-label)]">
        <span>{{ i18nFontSizeLabel }}</span>
        <div class="flex overflow-hidden rounded-[2px] border border-[var(--zv-hairline-strong)]">
          <button
            v-for="size in FONT_SIZES"
            :key="size"
            class="border-0 bg-[var(--zv-surface)] px-2.5 text-[13px] tabular-nums transition-colors first:border-r last:border-l border-[var(--zv-hairline)]"
            :class="fontSize === size ? 'bg-[var(--zv-text)] text-[var(--zv-surface)]' : 'text-[var(--zv-text)] hover:bg-[var(--zv-surface-alt)]'"
            style="height: 34px"
            @click="fontSize = size"
          >
            {{ size }}
          </button>
        </div>
      </div>
      <div class="ml-auto flex items-center gap-2">
        <button
          class="flex items-center gap-1.5 border border-[var(--zv-hairline-strong)] bg-[var(--zv-surface)] px-3 text-[14px] text-[var(--zv-text)] transition-colors hover:bg-[var(--zv-surface-alt)] disabled:opacity-50"
          style="height: 34px"
          :disabled="status === 'connecting'"
          @click="status === 'connected' ? ws?.close(1000) : openTerminal()"
        >
          <ElIcon :size="18"><VideoPlay /></ElIcon>
          <template v-if="status === 'connected'">{{ t('server.ssh.disconnect') }}</template>
          <template v-else-if="status === 'closed'">{{ t('server.ssh.reconnect') }}</template>
          <template v-else>{{ t('server.ssh.startButton') }}</template>
        </button>
        <button
          v-if="status === 'connected'"
          class="flex items-center gap-1.5 border border-[#b42318] bg-[var(--zv-surface)] px-3 text-[14px] text-[#b42318] transition-colors hover:bg-[var(--zv-surface-alt)] dark:text-[#ff8a7a]"
          style="height: 34px"
          @click="ws?.close(1000)"
        >
          <ElIcon :size="18"><SwitchButton /></ElIcon>
          {{ t('server.ssh.disconnect') }}
        </button>
      </div>
    </div>

    <!-- 断线 / 出错横幅:显眼的功能性呈现,不再一行小字 -->
    <div
      v-if="status === 'closed' && (banner || statusText)"
      class="flex items-center justify-between gap-3 border border-[#b42318] border-l-4 bg-[var(--zv-surface)] px-4 py-3 dark:border-[#ff8a7a]"
    >
      <div class="flex flex-col gap-0.5">
        <span class="text-[14px] font-medium text-[#b42318] dark:text-[#ff8a7a]">
          {{ banner || t('server.ssh.statusClosed') }}
        </span>
        <span class="text-[12px] text-[var(--zv-label)]">{{ closedAdvice }}</span>
      </div>
      <button
        class="flex shrink-0 items-center gap-1.5 border border-[var(--zv-hairline-strong)] bg-[var(--zv-text)] px-3 text-[14px] text-[var(--zv-surface)] transition-colors hover:opacity-80"
        style="height: 34px"
        @click="openTerminal"
      >
        <ElIcon :size="18"><VideoPlay /></ElIcon>
        {{ t('server.ssh.reconnect') }}
      </button>
    </div>
    <div
      v-else-if="banner && status === 'connected'"
      class="border-l-4 border-[#9a6700] bg-[var(--zv-surface)] px-4 py-2 text-[13px] text-[var(--zv-text-sub)]"
    >
      {{ banner }}
    </div>

    <!-- 终端区:connecting 骨架 / 空态说明 / 终端本体 -->
    <div
      ref="containerRef"
      class="border border-[var(--zv-hairline)] bg-[var(--zv-bg)] p-2"
      style="height: calc(100vh - 300px); min-height: 360px"
    >
      <!-- 连接中骨架 -->
      <div v-if="status === 'connecting'" class="flex h-full flex-col gap-2 px-2 py-2">
        <div class="h-4 w-1/3 animate-pulse bg-[var(--zv-surface-alt)]"></div>
        <div class="h-4 w-1/2 animate-pulse bg-[var(--zv-surface-alt)]"></div>
        <div class="h-4 w-2/5 animate-pulse bg-[var(--zv-surface-alt)]"></div>
        <div class="h-4 w-1/4 animate-pulse bg-[var(--zv-surface-alt)]"></div>
      </div>
      <!-- 未连接空态 -->
      <div v-else-if="status === 'idle'" class="flex h-full flex-col items-center justify-center gap-3 px-6 text-center">
        <span class="text-[16px] font-medium text-[var(--zv-text)]">root 终端</span>
        <span class="max-w-md text-[14px] leading-6 text-[var(--zv-text-sub)]">
          这是连接到服务器 root 的全交互终端；发起需另一位管理员的动态码双人授权,票据 120 秒内单次有效。
        </span>
        <span class="max-w-md text-[13px] leading-5 text-[#b42318] dark:text-[#ff8a7a]">
          风险提示:root 下运行的命令不可撤销,请确认操作内容后再开启。
        </span>
        <button
          class="mt-1 flex items-center gap-1.5 border border-[var(--zv-hairline-strong)] bg-[var(--zv-text)] px-4 text-[14px] text-[var(--zv-surface)] transition-colors hover:opacity-80"
          style="height: 34px"
          @click="openTerminal"
        >
          <ElIcon :size="18"><Connection /></ElIcon>
          {{ t('server.ssh.startButton') }}
        </button>
      </div>
    </div>
    <p class="text-[12px] text-[var(--zv-label)]">{{ t('server.ssh.typeToStart') }}</p>
    <ZSystemAuthDialog ref="authRef" />
  </div>
</template>

<style scoped>
/* 本页局部 token:建议后续由 flat.css 统一收口(另一车道) */
textarea,
input {
  font-family: inherit;
}
</style>
