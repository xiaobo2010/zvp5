<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import {
  ElAlert,
  ElButton,
  ElDialog,
  ElInput,
  ElOption,
  ElRadioButton,
  ElRadioGroup,
  ElSelect
} from 'element-plus'
import { useI18n } from 'vue-i18n'
import { listModels } from '@/api/ai/chat'
import { streamChatCompletion } from '@/api/ai/stream'
import { aiErrorCodeOf, aiErrorCopy, type AiErrorCopy } from '@/api/ai/errors'
import {
  AI_WRITE_ACTIONS,
  buildAiMessages,
  stripWrappingFence,
  type AiWriteAction
} from './aiPrompts'

const props = defineProps<{
  visible: boolean
  /** 选中的文本；没有选中时是全文（父组件决定）。 */
  source: string
  hasSelection: boolean
}>()

const emit = defineEmits<{
  (e: 'update:visible', value: boolean): void
  (e: 'apply', text: string): void
}>()

const { t } = useI18n()

const action = ref<AiWriteAction>('polish')
const instruction = ref('')
const models = ref<string[]>([])
const model = ref('')
const result = ref('')
const loading = ref(false)
const interrupted = ref(false)
const error = ref<AiErrorCopy | null>(null)

let controller: AbortController | null = null
let modelsLoaded = false

const visible = computed({
  get: () => props.visible,
  set: (value) => emit('update:visible', value)
})

const canRun = computed(() => !loading.value && model.value !== '')

watch(
  () => props.visible,
  async (open) => {
    if (!open) {
      stop()
      return
    }
    error.value = null
    interrupted.value = false
    if (!modelsLoaded) {
      await loadModels()
    }
  }
)

async function loadModels(): Promise<void> {
  try {
    const list = await listModels()
    models.value = (list.data ?? []).map((item) => item.id)
    modelsLoaded = true
    if (!model.value && models.value.length > 0) {
      model.value = models.value[0]
    }
  } catch (caught) {
    showError(caught)
  }
}

function showError(caught: unknown): void {
  error.value = aiErrorCopy(aiErrorCodeOf(caught), t)
}

function stop(): void {
  controller?.abort()
  controller = null
  loading.value = false
}

async function run(): Promise<void> {
  if (loading.value) {
    return
  }
  if (!model.value) {
    error.value = { title: t('markdown.editor.ai.noModels'), hint: '' }
    return
  }
  result.value = ''
  error.value = null
  interrupted.value = false
  loading.value = true
  controller = new AbortController()
  try {
    const out = await streamChatCompletion({
      model: model.value,
      messages: buildAiMessages({
        action: action.value,
        source: props.source,
        instruction: instruction.value
      }),
      signal: controller.signal,
      onDelta: (delta) => {
        result.value += delta
      }
    })
    const final = stripWrappingFence(out.text || result.value).trim()
    result.value = final
    interrupted.value = out.interrupted
  } catch (caught) {
    if ((caught as Error)?.name === 'AbortError') {
      return
    }
    showError(caught)
  } finally {
    loading.value = false
    controller = null
  }
}

function apply(): void {
  const text = result.value.trim()
  if (!text) {
    error.value = { title: t('markdown.editor.ai.empty'), hint: '' }
    return
  }
  emit('apply', text)
  visible.value = false
}
</script>

<template>
  <ElDialog
    v-model="visible"
    :title="t('markdown.editor.ai.title')"
    width="min(680px, 92vw)"
    append-to-body
    destroy-on-close
    class="yg-markdown-ai"
  >
    <div class="yg-markdown-ai__body">
      <div class="yg-markdown-ai__row">
        <span class="yg-markdown-ai__label">{{ t('markdown.editor.ai.action') }}</span>
        <ElRadioGroup v-model="action" size="small">
          <ElRadioButton v-for="item in AI_WRITE_ACTIONS" :key="item" :value="item">
            {{ t(`markdown.editor.ai.actions.${item}`) }}
          </ElRadioButton>
        </ElRadioGroup>
      </div>

      <div class="yg-markdown-ai__row">
        <span class="yg-markdown-ai__label">{{ t('markdown.editor.ai.model') }}</span>
        <ElSelect
          v-model="model"
          size="small"
          class="yg-markdown-ai__model"
          :placeholder="t('markdown.editor.ai.noModels')"
        >
          <ElOption v-for="name in models" :key="name" :label="name" :value="name" />
        </ElSelect>
        <span v-if="!hasSelection" class="yg-markdown-ai__hint">
          {{ t('markdown.editor.ai.wholeDocument') }}
        </span>
      </div>

      <ElInput
        v-model="instruction"
        type="textarea"
        :rows="2"
        resize="none"
        :placeholder="t('markdown.editor.ai.instructionPlaceholder')"
      />

      <div class="yg-markdown-ai__section">
        <div class="yg-markdown-ai__label">{{ t('markdown.editor.ai.result') }}</div>
        <ElInput
          :model-value="result"
          type="textarea"
          :rows="10"
          resize="none"
          readonly
          :placeholder="t('markdown.editor.ai.source')"
        />
      </div>

      <ElAlert
        v-if="error"
        type="error"
        :closable="false"
        show-icon
        :title="error.title"
        :description="error.hint"
      />
      <ElAlert
        v-else-if="interrupted"
        type="warning"
        :closable="false"
        show-icon
        :title="t('markdown.editor.ai.truncated')"
      />
    </div>

    <template #footer>
      <ElButton @click="visible = false">{{ t('markdown.editor.ai.cancel') }}</ElButton>
      <ElButton :disabled="!canRun" @click="run">
        {{ result ? t('markdown.editor.ai.rerun') : t('markdown.editor.ai.run') }}
      </ElButton>
      <ElButton type="primary" :disabled="loading || !result.trim()" @click="apply">
        {{ t('markdown.editor.ai.apply') }}
      </ElButton>
    </template>
  </ElDialog>
</template>

<style scoped>
.yg-markdown-ai__body {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}

.yg-markdown-ai__row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.5rem;
}

.yg-markdown-ai__label {
  font-size: 0.8125rem;
  color: var(--el-text-color-secondary);
}

.yg-markdown-ai__model {
  width: 260px;
}

.yg-markdown-ai__hint {
  font-size: 0.75rem;
  color: var(--el-text-color-secondary);
}

.yg-markdown-ai__section {
  display: flex;
  flex-direction: column;
  gap: 0.375rem;
}
</style>
