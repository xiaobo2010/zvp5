<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import type { AiThinkingLevel } from '@/../types/ai'

/**
 * 模型 + 思考强度的受控选择器。
 *
 * 刻意不碰 Pinia：会话页的选择器绑的是会话状态（锁定、PATCH），而场景弹窗 /
 * SSH 面板只需要一次性请求参数。状态归调用方持有，这里只负责取值与上抛。
 *
 * 模型为空串 = 不指定模型（由后端取默认），所以下拉是可清空的。
 */
const props = withDefaults(
  defineProps<{
    models: string[]
    model: string
    thinking: AiThinkingLevel | ''
    /** 关掉「思考强度」这一维：入口不支持该参数时传 false。 */
    thinkingEnabled?: boolean
    disabled?: boolean
    loading?: boolean
    showThinking?: boolean
  }>(),
  { thinkingEnabled: true, disabled: false, loading: false, showThinking: true }
)

const emit = defineEmits<{
  (e: 'update:model', value: string): void
  (e: 'update:thinking', value: AiThinkingLevel | ''): void
}>()

const { t } = useI18n()

// 文案只读复用会话页那套键，避免同义不同词
const thinkingOptions = computed(() => [
  { label: t('ai.chat.thinkingDefault'), value: '' },
  { label: t('ai.chat.thinkingLow'), value: 'low' },
  { label: t('ai.chat.thinkingMedium'), value: 'medium' },
  { label: t('ai.chat.thinkingHigh'), value: 'high' }
])

const showThinkingPicker = computed(() => props.showThinking && props.thinkingEnabled)

// 清空按钮给的是 null / undefined，统一收敛成空串
function changeModel(value: string | null | undefined): void {
  emit('update:model', value ?? '')
}

function changeThinking(value: string | number | boolean | undefined): void {
  const next = typeof value === 'string' ? value : ''
  if (next === 'low' || next === 'medium' || next === 'high') {
    emit('update:thinking', next)
    return
  }
  emit('update:thinking', '')
}
</script>

<template>
  <div class="ai-picker">
    <template v-if="models.length">
      <span class="ai-picker__label">{{ t('ai.chat.model') }}</span>
      <ElSelect
        class="ai-picker__model"
        size="small"
        clearable
        :model-value="model"
        :loading="loading"
        :disabled="disabled"
        :placeholder="t('server.aiPicker.modelPlaceholder')"
        @update:model-value="changeModel"
      >
        <ElOption v-for="id in models" :key="id" :label="id" :value="id" />
      </ElSelect>
    </template>

    <template v-if="showThinkingPicker">
      <span class="ai-picker__label">{{ t('ai.chat.thinking') }}</span>
      <ElSegmented
        size="small"
        :model-value="thinking"
        :options="thinkingOptions"
        :disabled="disabled"
        @update:model-value="changeThinking"
      />
    </template>
  </div>
</template>

<style scoped>
.ai-picker {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--yg-space-2);
}

.ai-picker__label {
  font-size: var(--yg-font-size-xs);
  color: var(--yg-text-tertiary);
  white-space: nowrap;
}

.ai-picker__model {
  width: 200px;
  max-width: 100%;
}
</style>
