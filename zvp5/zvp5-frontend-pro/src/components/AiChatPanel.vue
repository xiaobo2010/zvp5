<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue'
import {
  createChatCompletion,
  listModels,
  readCompletionText,
  streamChatCompletion,
  type AiChatError,
  type AiChatMessage,
  type AiModel
} from '@/api/ai-chat'

/* -------------------------------------------------------------------------- */
/* State                                                                      */
/* -------------------------------------------------------------------------- */

interface ChatTurn {
  id: number
  role: 'user' | 'assistant'
  content: string
  status: 'pending' | 'done' | 'error' | 'stopped' | 'interrupted'
  errorMessage?: string
  errorCode?: string
  requestId?: string | null
}

const models = ref<AiModel[]>([])
const modelsLoading = ref(false)
const modelsError = ref('')

const selectedModel = ref('')
const streamEnabled = ref(true)

const turns = ref<ChatTurn[]>([])
const input = ref('')
const sending = ref(false)

let controller: AbortController | null = null
let turnSeq = 0

const listRef = ref<HTMLElement>()

/* -------------------------------------------------------------------------- */
/* Computed                                                                   */
/* -------------------------------------------------------------------------- */

const hasModels = computed(() => models.value.length > 0)
const canSend = computed(
  () => hasModels.value && selectedModel.value && input.value.trim() && !sending.value
)
const isEmpty = computed(() => turns.value.length === 0)

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
    if (!selectedModel.value && list.length > 0) {
      selectedModel.value = list[0]!.id
    }
    if (list.length === 0) {
      selectedModel.value = ''
      modelsError.value = '当前没有可用的模型。'
    }
  } catch (error) {
    models.value = []
    const message =
      error && typeof error === 'object' && 'message' in error
        ? String((error as { message?: unknown }).message ?? '')
        : ''
    modelsError.value = message || '模型列表加载失败，请重试。'
  } finally {
    modelsLoading.value = false
  }
}

/* -------------------------------------------------------------------------- */
/* Chat                                                                       */
/* -------------------------------------------------------------------------- */

function nextTurnId(): number {
  turnSeq += 1
  return turnSeq
}

function turnsToMessages(turns: ChatTurn[]): AiChatMessage[] {
  return turns
    .filter((turn) => turn.status === 'done' && turn.content.trim())
    .map((turn) => ({ role: turn.role, content: turn.content }))
}

async function scrollToBottom() {
  await nextTick()
  const element = listRef.value
  if (element) {
    element.scrollTop = element.scrollHeight
  }
}

async function send() {
  if (!canSend.value) return

  const text = input.value.trim()
  if (!text) return

  const userTurn: ChatTurn = {
    id: nextTurnId(),
    role: 'user',
    content: text,
    status: 'done'
  }
  turns.value.push(userTurn)

  const messages = turnsToMessages(turns.value)
  turns.value.push({
    id: nextTurnId(),
    role: 'assistant',
    content: '',
    status: 'pending'
  })
  // Mutate the reactive proxy stored in the array, not the raw object.
  const assistantTurn = turns.value[turns.value.length - 1]!

  input.value = ''
  sending.value = true
  void scrollToBottom()

  if (streamEnabled.value) {
    controller = new AbortController()
    try {
      const result = await streamChatCompletion({
        model: selectedModel.value,
        messages,
        signal: controller.signal,
        onDelta: (delta) => {
          assistantTurn.content += delta
          void scrollToBottom()
        }
      })
      assistantTurn.content = result.text || assistantTurn.content
      assistantTurn.requestId = result.requestId
      assistantTurn.status = result.interrupted ? 'interrupted' : 'done'
    } catch (error) {
      if ((error as { name?: string })?.name === 'AbortError') {
        assistantTurn.status = 'stopped'
      } else {
        const aiError = error as AiChatError
        assistantTurn.status = 'error'
        assistantTurn.errorMessage = aiError.message || '请求失败'
        assistantTurn.errorCode = aiError.code || ''
        assistantTurn.requestId = aiError.requestId
      }
    } finally {
      controller = null
    }
  } else {
    try {
      const result = await createChatCompletion(selectedModel.value, messages)
      assistantTurn.content = readCompletionText(result)
      assistantTurn.status = 'done'
    } catch (error) {
      const aiError = error as AiChatError
      assistantTurn.status = 'error'
      assistantTurn.errorMessage = aiError.message || '请求失败'
      assistantTurn.errorCode = aiError.code || ''
      assistantTurn.requestId = aiError.requestId
    }
  }

  sending.value = false
  void scrollToBottom()
}

function stop() {
  controller?.abort()
  controller = null
}

function clear() {
  controller?.abort()
  controller = null
  turns.value = []
}

function handleKeydown(event: KeyboardEvent) {
  if (event.isComposing) return
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault()
    void send()
  }
}

/* -------------------------------------------------------------------------- */
/* Lifecycle                                                                  */
/* -------------------------------------------------------------------------- */

onMounted(() => {
  void loadModels()
})

onUnmounted(() => {
  controller?.abort()
})
</script>

<template>
  <div class="ai-chat-panel">
    <!-- Controls -->
    <div class="chat-controls">
      <div class="control-row">
        <label for="chat-model-select" class="control-label">模型</label>
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
      </div>

      <div class="control-row">
        <label for="stream-toggle" class="control-label">流式输出</label>
        <label class="toggle-switch">
          <input id="stream-toggle" v-model="streamEnabled" type="checkbox" />
          <span class="toggle-slider" aria-hidden="true"></span>
        </label>
        <span class="control-hint">{{ streamEnabled ? '开启' : '关闭' }}</span>
        <button
          type="button"
          class="ghost-button ghost-button--small"
          :disabled="isEmpty"
          @click="clear"
        >
          清空对话
        </button>
      </div>
    </div>

    <!-- Error state -->
    <div v-if="modelsError && !modelsLoading" class="error-banner glass-feedback glass-feedback--error" role="alert">
      <span>{{ modelsError }}</span>
      <button type="button" class="ghost-button ghost-button--small" @click="loadModels">
        重试
      </button>
    </div>

    <!-- Messages -->
    <div ref="listRef" class="chat-list" role="log" aria-live="polite" aria-atomic="false">
      <div v-if="isEmpty && !modelsLoading" class="empty-state">
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
          {{ hasModels ? '选择模型并输入消息开始对话' : '请先加载可用的模型' }}
        </p>
      </div>

      <div
        v-for="turn in turns"
        :key="turn.id"
        class="chat-turn"
        :class="[turn.role, turn.status]"
      >
        <div class="turn-header">
          <span class="turn-role">{{ turn.role === 'user' ? '我' : '助手' }}</span>
          <span
            v-if="turn.status === 'pending'"
            class="turn-status"
            role="status"
            aria-label="正在生成"
          >
            <span class="spinner" aria-hidden="true"></span>
            正在生成…
          </span>
          <span v-else-if="turn.status === 'stopped'" class="turn-status turn-status--warn">
            已停止
          </span>
          <span v-else-if="turn.status === 'interrupted'" class="turn-status turn-status--warn">
            回答被截断
          </span>
          <span v-else-if="turn.status === 'error'" class="turn-status turn-status--error">
            发生错误
          </span>
        </div>
        <div class="turn-content">
          <p v-if="!turn.content && turn.status === 'pending'" class="turn-placeholder">…</p>
          <p v-else class="turn-text">{{ turn.content }}</p>
          <div v-if="turn.status === 'error' && turn.errorMessage" class="turn-error glass-feedback glass-feedback--error">
            {{ turn.errorMessage }}
            <span v-if="turn.errorCode" class="error-code">({{ turn.errorCode }})</span>
          </div>
        </div>
      </div>
    </div>

    <!-- Input -->
    <div class="chat-input-area">
      <textarea
        v-model="input"
        class="chat-input"
        placeholder="输入消息…按 Enter 发送，Shift+Enter 换行"
        rows="3"
        :disabled="sending || !hasModels"
        @keydown="handleKeydown"
      ></textarea>
      <div class="input-actions">
        <button
          v-if="sending"
          type="button"
          class="ghost-button"
          @click="stop"
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
          :disabled="!canSend"
          @click="send"
        >
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
            <path
              d="M2 2L14 8L2 14V9L10 8L2 7V2Z"
              stroke="currentColor"
              stroke-width="1.5"
              stroke-linecap="round"
              stroke-linejoin="round"
              fill="currentColor"
            />
          </svg>
          发送
        </button>
      </div>
    </div>
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
  justify-content: flex-end;
  gap: var(--space-2);
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
