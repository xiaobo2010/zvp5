<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { CopyDocument } from '@element-plus/icons-vue'
import type { AiMessage, AiRunStatus, AiSceneName } from '@/../types/ai'
import { fetchRunDetail, listMessages } from '@/api/ai/conversations.ts'
import { aiErrorCodeOf, aiErrorCopy, aiErrorOf } from '@/api/ai/errors.ts'
import { SCENE_CONTENT_LIMIT, SCENE_CONTEXT_LIMIT, submitSceneRun } from '@/api/ai/scenes.ts'

const props = defineProps<{
  modelValue: boolean
  scene: AiSceneName
  title: string
  /** 默认注入的参考内容（文件内容 / 终端缓冲），为空时提示取不到。 */
  context: string
}>()

const emit = defineEmits<{ (e: 'update:modelValue', value: boolean): void }>()

const { t } = useI18n()

/** 轮询节奏参照仓内既有 run 轮询，1.5 秒一档。 */
const POLL_INTERVAL_MS = 1500
/** 参考内容超限时头尾各留一半，保证上下文首尾都在。 */
const CONTEXT_HEAD_LIMIT = SCENE_CONTEXT_LIMIT / 2
const MESSAGE_PAGE_LIMIT = 100

const TERMINAL_STATUSES: AiRunStatus[] = ['succeeded', 'failed', 'cancelled', 'interrupted']

const visible = computed({
  get: () => props.modelValue,
  set: (value) => emit('update:modelValue', value)
})

const instruction = ref('')
const extra = ref('')
const running = ref(false)
const result = ref('')
const error = ref('')

let runId = ''
let conversationOid = ''
let lastSeq = 0
let assistantText = ''
let pollToken = 0

function clip(value: string): string {
  if (value.length <= SCENE_CONTEXT_LIMIT) return value
  return value.slice(0, CONTEXT_HEAD_LIMIT) + value.slice(-CONTEXT_HEAD_LIMIT)
}

const reference = computed(() => clip(props.context.trim()))
const payload = computed(() => {
  const base = props.context.trim()
  const snippet = extra.value.trim()
  if (!snippet) return base
  const marked = `${t('server.aiScene.extraMarker')}\n${snippet}`
  return base ? `${base}\n\n${marked}` : marked
})
const truncated = computed(() => payload.value.length > SCENE_CONTEXT_LIMIT)

function reset(): void {
  pollToken += 1
  instruction.value = ''
  extra.value = ''
  running.value = false
  result.value = ''
  error.value = ''
  runId = ''
  conversationOid = ''
  lastSeq = 0
  assistantText = ''
}

watch(
  () => props.modelValue,
  (open) => {
    if (!open) {
      pollToken += 1
      running.value = false
      return
    }
    reset()
  }
)

onBeforeUnmount(() => {
  pollToken += 1
})

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

function collect(messages: AiMessage[] | undefined): void {
  for (const message of messages ?? []) {
    if (typeof message.seq === 'number' && message.seq > lastSeq) lastSeq = message.seq
    if (message.role === 'assistant' && message.content) assistantText = message.content
  }
  if (assistantText) result.value = assistantText
}

function failureText(caught: unknown): string {
  const request = aiErrorOf(caught)
  if (request?.status === 403) return t('server.aiScene.permissionDenied')
  const copy = aiErrorCopy(aiErrorCodeOf(caught), t)
  return copy.title || t('server.aiScene.failed')
}

async function poll(token: number): Promise<void> {
  while (token === pollToken && runId) {
    const detail = await fetchRunDetail(runId, 0, lastSeq)
    if (token !== pollToken) return
    collect(detail.messages)
    const status = detail.run.status
    if (TERMINAL_STATUSES.includes(status)) {
      if (!assistantText && conversationOid) {
        try {
          const page = await listMessages(conversationOid, { afterSeq: 0, limit: MESSAGE_PAGE_LIMIT })
          collect(page.messages)
        } catch {
          assistantText = ''
        }
      }
      if (status !== 'succeeded') {
        error.value = detail.run.error_message || t('server.aiScene.runFailed')
      }
      return
    }
    await sleep(POLL_INTERVAL_MS)
  }
}

async function submit(): Promise<void> {
  if (running.value) return
  const content = instruction.value.trim()
  if (!content) {
    error.value = t('server.aiScene.instructionRequired')
    return
  }
  if (content.length > SCENE_CONTENT_LIMIT) {
    error.value = t('server.aiScene.instructionTooLong')
    return
  }
  const token = pollToken + 1
  pollToken = token
  running.value = true
  error.value = ''
  result.value = ''
  assistantText = ''
  lastSeq = 0
  try {
    const submitted = await submitSceneRun(props.scene, content, payload.value || undefined)
    if (token !== pollToken) return
    runId = submitted.run_id
    conversationOid = submitted.conversation_oid
    await poll(token)
  } catch (caught) {
    if (token === pollToken) error.value = failureText(caught)
  } finally {
    if (token === pollToken) running.value = false
  }
}

async function copyResult(): Promise<void> {
  if (!result.value) return
  try {
    await navigator.clipboard.writeText(result.value)
    ElMessage.success(t('server.aiScene.copied'))
  } catch {
    ElMessage.error(t('server.aiScene.copyFailed'))
  }
}
</script>

<template>
  <ElDialog
    v-model="visible"
    :title="title"
    width="min(680px, 92vw)"
    append-to-body
    destroy-on-close
    class="scene-run"
  >
    <div class="scene-run__body">
      <span class="scene-run__label">{{ t('server.aiScene.instruction') }}</span>
      <ElInput
        v-model="instruction"
        type="textarea"
        :rows="3"
        resize="none"
        :maxlength="SCENE_CONTENT_LIMIT"
        show-word-limit
        :disabled="running"
        :placeholder="t('server.aiScene.instructionPlaceholder')"
      />

      <span class="scene-run__label">{{ t('server.aiScene.reference') }}</span>
      <pre v-if="reference" class="scene-run__reference">{{ reference }}</pre>
      <p v-else class="scene-run__hint">{{ t('server.aiScene.referenceEmpty') }}</p>
      <ElAlert
        v-if="truncated"
        type="warning"
        :closable="false"
        :title="t('server.aiScene.referenceTruncated')"
      />

      <span class="scene-run__label">{{ t('server.aiScene.extra') }}</span>
      <ElInput
        v-model="extra"
        type="textarea"
        :rows="2"
        resize="none"
        :disabled="running"
        :placeholder="t('server.aiScene.extraPlaceholder')"
      />

      <div class="scene-run__section">
        <div class="scene-run__section-head">
          <span class="scene-run__label">{{ t('server.aiScene.result') }}</span>
          <ElButton v-if="result" text bg size="small" :icon="CopyDocument" @click="copyResult">
            {{ t('server.aiScene.copy') }}
          </ElButton>
        </div>
        <pre v-if="result" class="scene-run__result">{{ result }}</pre>
        <p v-else class="scene-run__empty">
          {{ running ? t('server.aiScene.running') : t('server.aiScene.empty') }}
        </p>
      </div>

      <ElAlert v-if="error" type="error" :closable="false" show-icon :title="error" />

      <p class="scene-run__hint">{{ t('server.aiScene.readonlyNote') }}</p>
    </div>

    <template #footer>
      <ElButton @click="visible = false">{{ t('server.aiScene.close') }}</ElButton>
      <ElButton
        type="primary"
        :loading="running"
        :disabled="running || !instruction.trim()"
        @click="submit"
      >
        {{ result ? t('server.aiScene.rerun') : t('server.aiScene.submit') }}
      </ElButton>
    </template>
  </ElDialog>
</template>

<style scoped>
.scene-run__body {
  display: flex;
  flex-direction: column;
  gap: var(--yg-space-2);
}

.scene-run__label {
  font-size: var(--yg-font-size-xs);
  color: var(--yg-text-tertiary);
}

.scene-run__section {
  display: flex;
  flex-direction: column;
  gap: var(--yg-space-2);
  margin-top: var(--yg-space-2);
}

.scene-run__section-head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: var(--yg-space-2);
}

.scene-run__reference,
.scene-run__result {
  margin: 0;
  max-height: 200px;
  padding: var(--yg-space-2) var(--yg-space-3);
  border: 1px solid var(--yg-border-subtle);
  border-radius: var(--yg-radius-md);
  background: var(--yg-bg-surface-sunken);
  color: var(--yg-text-primary);
  font-family: var(--yg-font-mono);
  font-size: var(--yg-font-size-sm);
  line-height: var(--yg-line-height-normal);
  white-space: pre-wrap;
  word-break: break-word;
  overflow: auto;
}

.scene-run__result {
  max-height: 320px;
}

.scene-run__hint,
.scene-run__empty {
  margin: 0;
  font-size: var(--yg-font-size-xs);
  line-height: var(--yg-line-height-normal);
  color: var(--yg-text-tertiary);
}
</style>
