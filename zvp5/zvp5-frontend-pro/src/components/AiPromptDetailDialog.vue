<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ApiError, readableError } from '@/api/http'
import { fetchAiPromptDetail } from '@/api/admin-ai'
import type { AiPromptDetail, AiPromptRecord } from '@/api/admin-ai'

/**
 * Single retained prompt, opened from the AI-gateway 审阅 tab.
 *
 * Reading the body is itself an audited action: `GET /ai/prompts/{oid}` writes
 * an `APP_LOGS` admin-access row on every call. The dialog therefore says so
 * before showing content, and never re-fetches on incidental re-renders — only
 * opening a row or pressing retry triggers a read.
 *
 * Nothing from the previously opened record survives a switch: the body is
 * cleared the moment the target oid changes, so a slow response for row A can
 * never paint over row B (sequence guard) and stale text is never visible.
 */

const props = defineProps<{
  open: boolean
  record: AiPromptRecord | null
}>()

const emit = defineEmits<{ close: [] }>()

const dialog = ref<HTMLDialogElement | null>(null)
const detail = ref<AiPromptDetail | null>(null)
const detailLoading = ref(false)
const detailError = ref('')

let detailSeq = 0
let loadedOid = ''
let restoreOverflow = ''
let scrollLocked = false
let backdropPressed = false

const meta = computed(() => ({
  userName: detail.value?.userName || props.record?.userName || '未知用户',
  userOid: detail.value ? detail.value.userOid : (props.record?.userOid ?? null),
  purpose: detail.value?.purpose || props.record?.purpose || '',
  roles: detail.value?.rolesSnapshot || props.record?.rolesSnapshot || '',
  createdAt: detail.value?.createdAt || props.record?.createdAt || '',
  requestId: detail.value?.requestId ?? '',
  promptChars: props.record?.promptChars ?? 0
}))

const promptText = computed(() => detail.value?.promptText ?? '')
const answerText = computed(() => detail.value?.answerText ?? '')
const toolCallsText = computed(() => detail.value?.toolCalls ?? '')
const isRefused = computed(() => detail.value?.refused === true)
const isWarned = computed(() => detail.value?.warned === true)

const promptCharsText = computed(() =>
  meta.value.promptChars > 0 ? `${meta.value.promptChars.toLocaleString('zh-CN')} 字` : ''
)

const PURPOSE_LABELS: Record<string, string> = {
  chat: '对话',
  ocr: 'OCR',
  'markdown-assistant': 'Markdown 助手',
  agent: '智能体',
  scheduled: '定时任务'
}

function purposeLabel(value: string): string {
  if (!value) return '未标注场景'
  return PURPOSE_LABELS[value] ?? value
}

function pad(value: number): string {
  return String(value).padStart(2, '0')
}

function formatDateTime(value: string | null | undefined): string {
  if (!value) return '—'
  const raw = value.includes('T') ? value : value.replace(' ', 'T')
  const date = new Date(raw)
  if (Number.isNaN(date.getTime())) return '—'
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(
    date.getHours()
  )}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
}

function shortOid(value: string | null | undefined): string {
  if (!value) return ''
  return value.length > 14 ? `…${value.slice(-10)}` : value
}

function setScrollLock(locked: boolean) {
  if (typeof document === 'undefined') return
  if (locked && !scrollLocked) {
    restoreOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    scrollLocked = true
  } else if (!locked && scrollLocked) {
    document.body.style.overflow = restoreOverflow
    restoreOverflow = ''
    scrollLocked = false
  }
}

function resetDetail() {
  detailSeq += 1
  detail.value = null
  detailError.value = ''
  detailLoading.value = false
}

function closeElement() {
  const element = dialog.value
  if (element?.open) element.close()
}

/** Escape, backdrop and the close button all funnel here. */
function requestClose() {
  resetDetail()
  setScrollLock(false)
  closeElement()
  emit('close')
}

async function load(oid: string) {
  const seq = ++detailSeq
  detail.value = null
  detailError.value = ''
  if (!oid) {
    detailLoading.value = false
    detailError.value = '缺少记录编号，无法读取正文。'
    return
  }
  detailLoading.value = true
  try {
    const data = await fetchAiPromptDetail(oid)
    if (seq !== detailSeq) return
    detail.value = data
  } catch (reason) {
    if (seq !== detailSeq) return
    if (reason instanceof ApiError && reason.status === 404) {
      detailError.value = '记录不存在，或已过保留期被清理。'
    } else if (reason instanceof ApiError && reason.status === 403) {
      detailError.value = '当前账号没有读取提示词正文的权限。'
    } else {
      detailError.value = readableError(reason, '提示词正文读取失败。')
    }
  } finally {
    if (seq === detailSeq) detailLoading.value = false
  }
}

function retry() {
  const target = props.record?.oid ?? loadedOid
  if (target) void load(target)
}

function onDialogPointerDown(event: PointerEvent) {
  // Only a press that starts on the backdrop may close: dragging a text
  // selection out of a <pre> must not dismiss the record mid-read.
  backdropPressed = event.target === dialog.value
}

function onDialogClick(event: MouseEvent) {
  const fromBackdrop = backdropPressed
  backdropPressed = false
  // Backdrop clicks target the <dialog> itself; the panel covers the rest.
  if (fromBackdrop && event.target === dialog.value) requestClose()
}

watch(
  () => [props.open, props.record?.oid] as const,
  ([open, oid]) => {
    if (!open) {
      loadedOid = ''
      resetDetail()
      setScrollLock(false)
      closeElement()
      return
    }
    const element = dialog.value
    if (element && !element.open) element.showModal()
    setScrollLock(true)
    const target = oid ?? ''
    if (target && target === loadedOid) return
    loadedOid = target
    void load(target)
  },
  { flush: 'post' }
)

onMounted(() => {
  if (props.open) {
    const element = dialog.value
    if (element && !element.open) element.showModal()
  }
})

onBeforeUnmount(() => {
  detailSeq += 1
  setScrollLock(false)
  closeElement()
})
</script>

<template>
  <dialog
    ref="dialog"
    class="prompt-detail"
    aria-labelledby="prompt-detail-title"
    @cancel.prevent="requestClose"
    @pointerdown="onDialogPointerDown"
    @click="onDialogClick"
  >
    <div class="prompt-detail__panel">
      <header class="prompt-detail__header">
        <div class="prompt-detail__heading">
          <h2 id="prompt-detail-title" class="prompt-detail__title">提示词审阅</h2>
          <p class="prompt-detail__subtitle">
            {{ meta.userName }}
            <span v-if="meta.userOid" class="prompt-detail__mono">
              {{ shortOid(meta.userOid) }}
            </span>
          </p>
        </div>
        <button
          type="button"
          class="prompt-detail__close"
          aria-label="关闭"
          autofocus
          @click="requestClose"
        >
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
            <path
              d="M4 4L12 12M12 4L4 12"
              stroke="currentColor"
              stroke-width="1.6"
              stroke-linecap="round"
            />
          </svg>
        </button>
      </header>

      <div class="prompt-detail__body">
        <p class="prompt-detail__notice" role="note">
          <strong>访问留痕：</strong>
          本次查看会在服务端写入一条管理员访问审计，记录你的账号与这条记录编号；正文本身不会复制到审计日志。
        </p>

        <div class="prompt-detail__meta">
          <span class="prompt-detail__meta-label">用户</span>
          <span class="prompt-detail__meta-value">
            {{ meta.userName }}
            <span class="prompt-detail__mono">{{ meta.userOid || '账号已移除' }}</span>
          </span>
          <span class="prompt-detail__meta-label">场景</span>
          <span class="prompt-detail__meta-value">{{ purposeLabel(meta.purpose) }}</span>
          <template v-if="meta.roles">
            <span class="prompt-detail__meta-label">角色快照</span>
            <span class="prompt-detail__meta-value">{{ meta.roles }}</span>
          </template>
          <span class="prompt-detail__meta-label">记录时间</span>
          <span class="prompt-detail__meta-value">{{ formatDateTime(meta.createdAt) }}</span>
          <span class="prompt-detail__meta-label">请求编号</span>
          <span class="prompt-detail__meta-value prompt-detail__mono">
            {{ meta.requestId || '—' }}
          </span>
        </div>

        <div v-if="detailLoading" class="prompt-detail__state" role="status">
          <span class="prompt-detail__spinner" aria-hidden="true"></span>
          正在读取正文（本次读取会写入访问审计）…
        </div>

        <div
          v-else-if="detailError"
          class="prompt-detail__state prompt-detail__state--error"
          role="alert"
        >
          <span>{{ detailError }}</span>
          <div class="prompt-detail__state-actions">
            <button type="button" class="prompt-detail__retry" @click="retry">重试</button>
            <span class="prompt-detail__hint">重试会再次读取正文，也会再写一条审计。</span>
          </div>
        </div>

        <template v-else>
          <section class="prompt-detail__block">
            <div class="prompt-detail__block-head">
              <h3 class="prompt-detail__block-title">提示词正文</h3>
              <span v-if="promptCharsText" class="prompt-detail__count">
                {{ promptCharsText }}
              </span>
            </div>
            <pre v-if="promptText" class="prompt-detail__text">{{ promptText }}</pre>
            <p v-else class="prompt-detail__empty">未留存提示词正文。</p>
          </section>

          <section class="prompt-detail__block">
            <div class="prompt-detail__block-head">
              <h3 class="prompt-detail__block-title">回答</h3>
              <div class="prompt-detail__flags">
                <span v-if="isRefused" class="prompt-detail__flag prompt-detail__flag--bad">
                  被拒答
                </span>
                <span v-if="isWarned" class="prompt-detail__flag prompt-detail__flag--warn">
                  触发警告
                </span>
              </div>
            </div>
            <pre v-if="answerText" class="prompt-detail__text">{{ answerText }}</pre>
            <p v-else class="prompt-detail__empty">未留存回答文本（可能被拒答或尚未回写）。</p>
          </section>

          <section v-if="toolCallsText" class="prompt-detail__block">
            <div class="prompt-detail__block-head">
              <h3 class="prompt-detail__block-title">工具调用</h3>
            </div>
            <pre class="prompt-detail__text">{{ toolCallsText }}</pre>
          </section>
        </template>
      </div>

      <footer class="prompt-detail__footer">
        <button type="button" class="prompt-detail__done" @click="requestClose">关闭</button>
      </footer>
    </div>
  </dialog>
</template>

<style scoped>
.prompt-detail {
  width: min(880px, calc(100% - 32px));
  max-width: 100%;
  margin: auto;
  padding: 0;
  border: 1px solid var(--glass-border);
  border-radius: var(--radius-xl);
  /* Pro dialogs are opaque: the audit body must not let the table behind
     bleed through the prompt text. */
  background: var(--color-bg-elevated);
  color: var(--color-text-primary);
  box-shadow: var(--glass-floating-shadow);
  overflow: hidden;
}

.prompt-detail::backdrop {
  background: rgb(16 27 50 / 42%);
}

.prompt-detail__panel {
  display: flex;
  flex-direction: column;
  max-height: min(86vh, 900px);
}

.prompt-detail__header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-3);
  padding: var(--space-5) var(--space-5) var(--space-3);
  border-bottom: 1px solid var(--color-border-hairline);
}

.prompt-detail__heading {
  min-width: 0;
}

.prompt-detail__title {
  font-size: var(--font-size-headline);
  font-weight: 600;
  line-height: var(--line-height-snug);
  color: var(--color-text-primary);
}

.prompt-detail__subtitle {
  margin-top: 2px;
  font-size: var(--font-size-caption);
  color: var(--color-text-secondary);
  line-height: var(--line-height-normal);
  overflow-wrap: anywhere;
}

.prompt-detail__close {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  width: 36px;
  height: 36px;
  padding: 0;
  border: 1px solid var(--color-border-hairline);
  border-radius: 50%;
  background: rgba(110, 110, 115, 0.08);
  color: var(--color-text-secondary);
  cursor: pointer;
  transition:
    background-color var(--duration-fast) var(--ease-smooth),
    color var(--duration-fast) var(--ease-smooth);
}

.prompt-detail__close:hover {
  background: rgba(110, 110, 115, 0.14);
  color: var(--color-text-primary);
}

.prompt-detail__close:focus-visible,
.prompt-detail__retry:focus-visible,
.prompt-detail__done:focus-visible {
  outline: 2px solid var(--color-brand);
  outline-offset: 1px;
}

.prompt-detail__body {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  padding: var(--space-4) var(--space-5);
  overflow-y: auto;
  overscroll-behavior: contain;
}

.prompt-detail__notice {
  margin: 0;
  padding: var(--space-3) var(--space-4);
  border-radius: var(--radius-md);
  background: var(--tone-blue-bg);
  color: var(--color-text-secondary);
  font-size: var(--font-size-caption);
  line-height: var(--line-height-normal);
}

.prompt-detail__notice strong {
  color: var(--color-brand);
  font-weight: 600;
}

.prompt-detail__meta {
  display: grid;
  grid-template-columns: max-content minmax(0, 1fr);
  gap: var(--space-2) var(--space-4);
  font-size: var(--font-size-caption);
  line-height: var(--line-height-normal);
}

.prompt-detail__meta-label {
  color: var(--color-text-tertiary);
}

.prompt-detail__meta-value {
  display: flex;
  align-items: baseline;
  gap: var(--space-2);
  flex-wrap: wrap;
  min-width: 0;
  color: var(--color-text-primary);
  overflow-wrap: anywhere;
}

.prompt-detail__mono {
  font-family: 'SF Mono', 'Menlo', 'Monaco', 'Courier New', monospace;
  font-size: 11px;
  color: var(--color-text-tertiary);
  overflow-wrap: anywhere;
}

.prompt-detail__state {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: var(--space-2);
  padding: var(--space-4);
  border-radius: var(--radius-lg);
  background: rgba(255, 255, 255, 0.5);
  border: 1px solid var(--color-border-hairline);
  color: var(--color-text-secondary);
  font-size: var(--font-size-callout);
  line-height: var(--line-height-normal);
}

.prompt-detail__state--error {
  border-color: rgba(255, 59, 48, 0.22);
  background: rgba(255, 59, 48, 0.06);
  color: var(--tone-rose);
}

.prompt-detail__state-actions {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-wrap: wrap;
}

.prompt-detail__spinner {
  width: 15px;
  height: 15px;
  border-radius: 50%;
  border: 2px solid rgba(0, 113, 227, 0.2);
  border-top-color: var(--color-brand);
  animation: prompt-detail-spin 700ms linear infinite;
}

@keyframes prompt-detail-spin {
  to {
    transform: rotate(360deg);
  }
}

.prompt-detail__retry {
  min-height: 32px;
  padding: 0 var(--space-3);
  border: 1px solid var(--color-border-moderate);
  border-radius: var(--radius-pill);
  background: #ffffff;
  color: var(--color-text-primary);
  font: inherit;
  font-size: var(--font-size-caption);
  font-weight: 600;
  cursor: pointer;
}

.prompt-detail__hint {
  font-size: var(--font-size-caption);
  color: var(--tone-rose);
}

.prompt-detail__block {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  min-width: 0;
}

.prompt-detail__block-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--space-3);
  flex-wrap: wrap;
}

.prompt-detail__block-title {
  font-size: var(--font-size-callout);
  font-weight: 600;
  color: var(--color-text-primary);
}

.prompt-detail__count {
  font-size: var(--font-size-caption);
  color: var(--color-text-tertiary);
  font-variant-numeric: tabular-nums;
}

.prompt-detail__flags {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  flex-wrap: wrap;
}

.prompt-detail__flag {
  display: inline-flex;
  align-items: center;
  padding: 3px 9px;
  border-radius: var(--radius-pill);
  font-size: var(--font-size-caption);
  font-weight: 600;
  line-height: 1.3;
}

.prompt-detail__flag--bad {
  background: var(--tone-rose-bg);
  color: var(--tone-rose);
}

.prompt-detail__flag--warn {
  background: var(--tone-amber-bg);
  color: #a86400;
}

/* pre-wrap keeps the model input's own line breaks while still wrapping. */
.prompt-detail__text {
  margin: 0;
  padding: var(--space-3) var(--space-4);
  border: 1px solid var(--color-border-hairline);
  border-radius: var(--radius-md);
  background: #f5f7fa;
  color: var(--color-text-primary);
  font-family: 'SF Mono', 'Menlo', 'Monaco', 'Courier New', monospace;
  font-size: var(--font-size-caption);
  line-height: var(--line-height-relaxed);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.prompt-detail__empty {
  margin: 0;
  font-size: var(--font-size-caption);
  color: var(--color-text-tertiary);
}

.prompt-detail__footer {
  display: flex;
  justify-content: flex-end;
  padding: var(--space-3) var(--space-5) var(--space-5);
  border-top: 1px solid var(--color-border-hairline);
}

.prompt-detail__done {
  min-height: 38px;
  padding: 0 var(--space-5);
  border: 1px solid rgba(0, 113, 227, 0.22);
  border-radius: var(--radius-pill);
  background: linear-gradient(135deg, #409cff 0%, #0071e3 100%);
  color: #ffffff;
  font: inherit;
  font-size: var(--font-size-callout);
  font-weight: 600;
  cursor: pointer;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.34),
    0 3px 12px rgba(0, 113, 227, 0.24);
}

.prompt-detail__done:hover {
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.4),
    0 6px 18px rgba(0, 113, 227, 0.3);
}

@media (max-width: 640px) {
  .prompt-detail {
    width: calc(100% - 16px);
    border-radius: var(--radius-lg);
  }

  .prompt-detail__panel {
    max-height: 88vh;
  }

  .prompt-detail__header,
  .prompt-detail__footer {
    padding-left: var(--space-4);
    padding-right: var(--space-4);
  }

  .prompt-detail__body {
    padding: var(--space-4);
  }

  .prompt-detail__meta {
    grid-template-columns: 1fr;
    gap: 2px;
  }

  .prompt-detail__meta-value {
    margin-bottom: var(--space-2);
  }
}

@media (prefers-reduced-motion: reduce) {
  .prompt-detail__close,
  .prompt-detail__done {
    transition: none;
  }

  .prompt-detail__spinner {
    animation: none;
  }
}
</style>
