<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ElAlert } from 'element-plus/es/components/alert/index.mjs'
import { ElButton } from 'element-plus/es/components/button/index.mjs'
import { ElButtonGroup } from 'element-plus/es/components/button/index.mjs'
import { ElDialog } from 'element-plus/es/components/dialog/index.mjs'
import { ElInput } from 'element-plus/es/components/input/index.mjs'
import { ElOption } from 'element-plus/es/components/select/index.mjs'
import { ElRadioButton } from 'element-plus/es/components/radio/index.mjs'
import { ElRadioGroup } from 'element-plus/es/components/radio/index.mjs'
import { ElSelect } from 'element-plus/es/components/select/index.mjs'
import { useI18n } from 'vue-i18n'
import { listModels } from '@/api/ai/chat'
import { streamChatCompletion, type AiStreamError } from '@/api/ai/stream'
import {
  AI_ERROR_FALLBACK_SECTION,
  aiErrorCodeOf,
  aiErrorCopy,
  aiErrorSection,
  type AiErrorCopy
} from '@/api/ai/errors'
import {
  AI_WRITE_ACTIONS,
  buildAiMessages,
  stripWrappingFence,
  type AiWriteAction
} from './aiPrompts'
import ZMarkdown from './ZMarkdown.vue'

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

/** 审计场景名：后端落 AI_CALL_LOGS.PURPOSE，用来区分「markdown 助手」与对话页。 */
const AI_SCENE_MARKDOWN = 'markdown-assistant'

/**
 * 流式期间预览的刷新节奏。
 *
 * markdown 解析 + 净化不便宜，逐字重渲染会把主线程占满；每 300ms 最多一次，
 * 结束后再完整渲染一次收尾。
 */
const STREAM_PREVIEW_THROTTLE_MS = 300

const action = ref<AiWriteAction>('polish')
const instruction = ref('')
const models = ref<string[]>([])
const model = ref('')
const result = ref('')
/** 思考过程（上游 reasoning_content 边流边累计）；空串表示这次没有思考内容。 */
const reasoning = ref('')
/** 思考区块的折叠态：默认展开，用户收起后本次不再自动弹开。 */
const reasoningOpen = ref(true)
/** 预览用的文本：流式期间节流刷新，结束后与 result 完全一致。 */
const previewSource = ref('')
/** 结果区视图：原文（可改）还是渲染后的预览。 */
const resultView = ref<'text' | 'preview'>('text')
const loading = ref(false)
const interrupted = ref(false)
const error = ref<AiErrorCopy | null>(null)

let controller: AbortController | null = null
let modelsLoaded = false
let previewTimer: ReturnType<typeof setTimeout> | null = null
let lastPreviewAt = 0

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
    syncPreview(true)
    if (!modelsLoaded) {
      await loadModels()
    }
  }
)

/* ---------------------------------------------------------------------------
   预览刷新
   --------------------------------------------------------------------------- */

function cancelPreviewTimer(): void {
  if (previewTimer) {
    clearTimeout(previewTimer)
    previewTimer = null
  }
}

function commitPreview(): void {
  lastPreviewAt = Date.now()
  previewSource.value = result.value
}

/**
 * 预览刷新。
 *
 * 流式期间节流：每 300ms 最多重渲染一次；force 时立刻完整渲染一次（收尾用）。
 */
function syncPreview(force = false): void {
  if (!force && loading.value) {
    const elapsed = Date.now() - lastPreviewAt
    if (elapsed >= STREAM_PREVIEW_THROTTLE_MS) {
      cancelPreviewTimer()
      commitPreview()
      return
    }
    if (!previewTimer) {
      previewTimer = setTimeout(() => {
        previewTimer = null
        commitPreview()
      }, STREAM_PREVIEW_THROTTLE_MS - elapsed)
    }
    return
  }
  cancelPreviewTimer()
  commitPreview()
}

// 手工改了原文：预览跟着走（流式期间不打扰，流的增量自己会刷）。
watch(result, () => {
  if (!loading.value) {
    syncPreview(true)
  }
})

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

/** 内容审核拦截：用与智能体页一致的那句话，不落到通用错误文案。 */
const MODERATION_REJECTED = 'AI_AGENT_MODERATION_REJECTED'

function showError(caught: unknown): void {
  const code = aiErrorCodeOf(caught)
  if (code === MODERATION_REJECTED) {
    error.value = { title: t('ai.agent.moderation.rejected'), hint: '' }
    return
  }
  error.value = aiErrorCopy(code, t)
}

/** 流内 error 帧 → 展示文案；码表不认识的码优先用后端给的中文 message。 */
function streamErrorCopy(payload: AiStreamError): AiErrorCopy {
  if (payload.message && aiErrorSection(payload.code) === AI_ERROR_FALLBACK_SECTION) {
    return { title: payload.message, hint: '' }
  }
  return aiErrorCopy(payload.code, t)
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
  previewSource.value = ''
  reasoning.value = ''
  reasoningOpen.value = true
  lastPreviewAt = 0
  error.value = null
  interrupted.value = false
  loading.value = true
  // 流式期间直接看渲染结果：边生成边看排版，比盯纯文本舒服。
  resultView.value = 'preview'
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
      scene: AI_SCENE_MARKDOWN,
      onDelta: (delta) => {
        result.value += delta
        syncPreview()
      },
      onReasoningDelta: (delta) => {
        reasoning.value += delta
      }
    })
    const final = stripWrappingFence(out.text || result.value).trim()
    result.value = final
    // 收尾：完整渲染一次，最终文本一字不差。
    syncPreview(true)
    if (out.error) {
      // 流内错误帧是明确失败，不是截断：正文（若有）照旧展示，警告换成错误。
      error.value = streamErrorCopy(out.error)
      interrupted.value = false
    } else {
      interrupted.value = out.interrupted
    }
  } catch (caught) {
    // 中断或出错也把已经收到的部分完整渲染出来。
    syncPreview(true)
    if ((caught as Error)?.name === 'AbortError') {
      return
    }
    showError(caught)
  } finally {
    loading.value = false
    cancelPreviewTimer()
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
        <div class="yg-markdown-ai__section-head">
          <span class="yg-markdown-ai__label">{{ t('markdown.editor.ai.result') }}</span>
          <ElButtonGroup size="small">
            <ElButton
              :type="resultView === 'text' ? 'primary' : 'default'"
              @click="resultView = 'text'"
            >
              {{ t('markdown.editor.edit') }}
            </ElButton>
            <ElButton
              :type="resultView === 'preview' ? 'primary' : 'default'"
              @click="resultView = 'preview'"
            >
              {{ t('markdown.editor.preview') }}
            </ElButton>
          </ElButtonGroup>
        </div>

        <!-- 思考过程：弱化配色、默认展开，与智能体页同一套观感 -->
        <div v-if="reasoning" class="yg-markdown-ai__reasoning">
          <button
            type="button"
            class="yg-markdown-ai__reasoning-head"
            :aria-expanded="reasoningOpen"
            @click="reasoningOpen = !reasoningOpen"
          >
            <span class="yg-markdown-ai__reasoning-chevron" aria-hidden="true" />
            <span>{{ t('ai.chat.thinkingSection') }}</span>
            <span v-if="loading" class="yg-markdown-ai__reasoning-live" aria-hidden="true" />
          </button>
          <p v-if="reasoningOpen" class="yg-markdown-ai__reasoning-text">{{ reasoning }}</p>
        </div>

        <!-- 原文：可以直接改，改完再应用 -->
        <ElInput
          v-if="resultView === 'text'"
          v-model="result"
          type="textarea"
          :rows="10"
          resize="none"
          :readonly="loading"
          :placeholder="t('markdown.editor.ai.source')"
        />

        <!-- 预览：流式期间按节流刷新，结束后是完整渲染 -->
        <div v-else class="yg-markdown-ai__preview">
          <ZMarkdown :source="previewSource" immediate />
        </div>
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

.yg-markdown-ai__section-head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
}

.yg-markdown-ai__preview {
  min-height: 220px;
  max-height: 360px;
  padding: 0.75rem;
  border: 1px solid var(--el-border-color);
  border-radius: var(--el-border-radius-base);
  background: var(--yg-bg-surface-sunken);
  overflow-y: auto;
}

/* 思考过程：弱化的旁注，不与正文抢注意力 */
.yg-markdown-ai__reasoning {
  padding: 0.5rem 0.625rem;
  border: 1px solid var(--el-border-color-lighter);
  border-left: 2px solid var(--el-border-color);
  border-radius: var(--el-border-radius-base);
  background: var(--yg-bg-surface-sunken);
}

.yg-markdown-ai__reasoning-head {
  display: flex;
  align-items: center;
  gap: 0.375rem;
  width: 100%;
  padding: 0;
  border: 0;
  background: transparent;
  font-size: 0.75rem;
  color: var(--el-text-color-secondary);
  text-align: left;
  cursor: pointer;
}

/* 折叠指示：一个小三角，展开时朝下 */
.yg-markdown-ai__reasoning-chevron {
  width: 0;
  height: 0;
  border-top: 4px solid currentColor;
  border-right: 4px solid transparent;
  border-left: 4px solid transparent;
  transform: rotate(-90deg);
  transition: transform 0.15s ease;
}

.yg-markdown-ai__reasoning-head[aria-expanded='true'] .yg-markdown-ai__reasoning-chevron {
  transform: rotate(0deg);
}

/* 还在生成：一个小圆点，暗示后面还有内容 */
.yg-markdown-ai__reasoning-live {
  width: 4px;
  height: 4px;
  border-radius: 50%;
  background: currentColor;
  animation: yg-markdown-ai-pulse 1.2s ease-in-out infinite;
}

.yg-markdown-ai__reasoning-text {
  margin: 0.375rem 0 0;
  font-size: 0.75rem;
  line-height: 1.6;
  color: var(--el-text-color-secondary);
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 200px;
  overflow-y: auto;
}

@keyframes yg-markdown-ai-pulse {
  0%,
  100% {
    opacity: 0.35;
  }

  50% {
    opacity: 1;
  }
}

@media (prefers-reduced-motion: reduce) {
  .yg-markdown-ai__reasoning-live {
    animation: none;
    opacity: 1;
  }
}
</style>
