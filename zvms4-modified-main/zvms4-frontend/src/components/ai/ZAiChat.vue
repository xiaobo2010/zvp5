<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  ElButton,
  ElCard,
  ElEmpty,
  ElIcon,
  ElInput,
  ElMessage,
  ElOption,
  ElSelect,
  ElSwitch,
  ElTag,
  ElTooltip
} from 'element-plus'
import { CopyDocument, Delete, Promotion, VideoPause } from '@element-plus/icons-vue'
import { useWindowSize } from '@vueuse/core'
import {
  AiRequestError,
  aiErrorCopy,
  createChatCompletion,
  listModels,
  readCompletionText,
  streamChatCompletion
} from '@/api/ai'
import type { AiModelItem } from '@/../types/ai'
import { buildChatRequest, turnsToMessages, type ChatTurn } from './chatSession'

const { t } = useI18n()
const { height } = useWindowSize()

const models = ref<AiModelItem[]>([])
const modelsLoading = ref(false)
const modelsFailed = ref(false)
const model = ref('')
const streamEnabled = ref(true)

const turns = ref<ChatTurn[]>([])
const input = ref('')
const sending = ref(false)
const cooldown = ref(0)

let controller: AbortController | null = null
let cooldownTimer: ReturnType<typeof setInterval> | null = null
let turnSeq = 0

const listHeight = computed(() => Math.max(220, Math.round(height.value * 0.52)))
const hasModels = computed(() => models.value.length > 0)
const canSend = computed(
  () => hasModels.value && Boolean(model.value) && !sending.value && cooldown.value <= 0
)

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

function nextTurnId(): number {
  turnSeq += 1
  return turnSeq
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

function markError(turn: ChatTurn, error: unknown) {
  const aiError = error instanceof AiRequestError ? error : null
  const code = aiError?.code ?? null
  const copy = aiErrorCopy(code, t)
  turn.status = 'error'
  turn.errorCode = code ?? ''
  turn.errorTitle = copy.title
  turn.errorHint = copy.hint
  turn.requestId = aiError?.requestId ?? null
  turn.quotaUsed = aiError?.quotaUsed ?? null
  turn.quotaLimit = aiError?.quotaLimit ?? null
  if (code === 'AI_RATE_LIMITED' && aiError?.retryAfter) {
    startCooldown(aiError.retryAfter)
  }
}

const listRef = ref<HTMLElement>()

async function scrollToBottom() {
  await nextTick()
  const element = listRef.value
  if (element) {
    element.scrollTop = element.scrollHeight
  }
}

async function send() {
  if (!canSend.value) {
    return
  }
  const text = input.value.trim()
  if (!text) {
    return
  }

  const userTurn: ChatTurn = {
    id: nextTurnId(),
    role: 'user',
    content: text,
    status: 'done'
  }
  turns.value.push(userTurn)

  const request = buildChatRequest(turnsToMessages(turns.value), model.value, streamEnabled.value)
  const assistantTurn: ChatTurn = {
    id: nextTurnId(),
    role: 'assistant',
    content: '',
    status: 'pending'
  }
  turns.value.push(assistantTurn)

  input.value = ''
  sending.value = true
  void scrollToBottom()

  if (streamEnabled.value) {
    controller = new AbortController()
    try {
      const result = await streamChatCompletion({
        model: request.model,
        messages: request.messages,
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
        markError(assistantTurn, error)
      }
    } finally {
      controller = null
    }
  } else {
    try {
      const result = await createChatCompletion(request.model, request.messages)
      assistantTurn.content = readCompletionText(result)
      assistantTurn.status = 'done'
    } catch (error) {
      markError(assistantTurn, error)
    }
  }

  sending.value = false
  void scrollToBottom()
}

function stop() {
  controller?.abort()
  controller = null
}

function clearSession() {
  controller?.abort()
  controller = null
  turns.value = []
  stopCooldown()
  ElMessage.success(t('ai.chat.cleared'))
}

async function copyRequestId(turn: ChatTurn) {
  if (!turn.requestId) {
    return
  }
  try {
    await navigator.clipboard.writeText(turn.requestId)
    ElMessage.success(t('ai.common.copied'))
  } catch {
    ElMessage.warning(turn.requestId)
  }
}

onMounted(() => {
  void loadModels()
})

onUnmounted(() => {
  controller?.abort()
  stopCooldown()
})
</script>

<template>
  <div class="flex flex-col gap-4">
    <ElCard shadow="never" v-loading="modelsLoading">
      <template #header>
        <div class="flex flex-wrap items-center justify-between gap-3">
          <div class="flex flex-col gap-1">
            <span>{{ t('ai.chat.title') }}</span>
            <span class="text-xs font-normal" style="color: var(--yg-text-tertiary)">
              {{ t('ai.chat.subtitle') }}
            </span>
          </div>
          <div class="flex flex-wrap items-center gap-3">
            <div class="flex items-center gap-2">
              <span class="yg-text-xs yg-text-tertiary">{{ t('ai.chat.stream') }}</span>
              <ElSwitch v-model="streamEnabled" />
              <span class="text-xs tabular-nums" style="color: var(--yg-text-tertiary)">
                {{ streamEnabled ? t('ai.chat.streamOn') : t('ai.chat.streamOff') }}
              </span>
            </div>
            <ElButton :disabled="turns.length === 0" @click="clearSession">
              <ElIcon class="mr-1"><Delete /></ElIcon>
              {{ t('ai.chat.clear') }}
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
    </ElCard>

    <ElCard shadow="never" class="ai-chat">
      <div
        ref="listRef"
        class="ai-chat__list"
        :style="{ maxHeight: `${listHeight}px` }"
      >
        <div v-if="turns.length === 0" class="py-10">
          <ElEmpty
            :description="
              modelsFailed
                ? t('ai.chat.modelsLoadFailed')
                : hasModels
                  ? t('ai.chat.emptyHint')
                  : t('ai.chat.modelsEmpty')
            "
            :image-size="90"
          />
        </div>

        <div v-for="turn in turns" :key="turn.id" class="ai-chat__row" :class="turn.role">
          <div class="ai-chat__who yg-text-xs yg-text-tertiary">
            {{ turn.role === 'user' ? t('ai.chat.you') : t('ai.chat.assistant') }}
          </div>
          <div class="ai-chat__bubble" :class="turn.role">
            <p v-if="turn.content" class="ai-chat__text">{{ turn.content }}</p>
            <p v-else-if="turn.status === 'pending'" class="text-xs" style="color: var(--yg-text-tertiary)">
              {{ t('ai.chat.thinking') }}
            </p>

            <div v-if="turn.status === 'error'" class="ai-chat__error">
              <div class="flex flex-wrap items-center gap-2">
                <span class="font-medium">{{ turn.errorTitle }}</span>
                <ElTag v-if="turn.errorCode" type="danger" size="small">
                  {{ turn.errorCode }}
                </ElTag>
              </div>
              <p class="text-xs">{{ turn.errorHint }}</p>
              <p
                v-if="turn.quotaUsed !== null && turn.quotaLimit !== null"
                class="text-xs tabular-nums"
              >
                {{
                  t('ai.chat.quotaUsed', {
                    used: turn.quotaUsed,
                    limit: turn.quotaLimit
                  })
                }}
              </p>
              <div v-if="turn.requestId" class="flex flex-wrap items-center gap-2 text-xs">
                <span style="color: var(--yg-text-tertiary)">{{ t('ai.chat.requestId') }}</span>
                <code class="tabular-nums">{{ turn.requestId }}</code>
                <ElButton text size="small" @click="copyRequestId(turn)">
                  <ElIcon class="mr-1"><CopyDocument /></ElIcon>
                  {{ t('ai.chat.copyRequestId') }}
                </ElButton>
              </div>
            </div>

            <p v-else-if="turn.status === 'interrupted'" class="ai-chat__notice">
              {{ t('ai.chat.interrupted') }}
              <span class="text-xs">{{ t('ai.chat.interruptedHint') }}</span>
            </p>
            <p v-else-if="turn.status === 'stopped'" class="ai-chat__notice">
              {{ t('ai.chat.stopped') }}
            </p>
          </div>
        </div>
      </div>

      <div class="ai-chat__composer">
        <ElInput
          v-model="input"
          type="textarea"
          :rows="3"
          resize="none"
          :placeholder="t('ai.chat.inputPlaceholder')"
          :disabled="!hasModels"
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
          </span>
          <div class="flex items-center gap-2">
            <ElButton v-if="sending" type="danger" plain @click="stop">
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
</template>

<style scoped>
.ai-chat__list {
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
  color: #ffffff;
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
  border-left: 2px solid var(--el-color-danger);
  background: var(--yg-bg-surface);
}

.ai-chat__notice {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding-left: 8px;
  border-left: 2px solid var(--yg-border-strong);
  color: var(--yg-text-tertiary);
}

.ai-chat__composer {
  margin-top: 12px;
}
</style>
