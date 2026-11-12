<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElIcon } from 'element-plus/es/components/icon/index.mjs'
import { ElOption } from 'element-plus/es/components/select/index.mjs'
import { ElSegmented } from 'element-plus/es/components/segmented/index.mjs'
import { ElSelect } from 'element-plus/es/components/select/index.mjs'
import { Lock } from '@element-plus/icons-vue'
import type { AiThinkingLevel } from '@/../types/ai'

/**
 * 模型 + 思考强度的受控选择器。
 *
 * 刻意不碰 Pinia：会话页的选择器绑的是会话状态（锁定、PATCH），而场景弹窗 /
 * SSH 面板只需要一次性请求参数。状态归调用方持有，这里只负责取值与上抛。
 *
 * 模型为空串 = 不指定模型（由后端取默认），所以下拉默认可清空；
 * 会话页不允许清空（建会话时模型就定死了），传 clearable=false 关掉。
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
    /** 模型已锁定（建会话时定死）：显示锁图标并禁用下拉。 */
    modelLocked?: boolean
    /** 只禁用模型下拉。 */
    modelDisabled?: boolean
    /** 只禁用思考强度。 */
    thinkingDisabled?: boolean
    /** 模型下拉是否可清空。 */
    clearable?: boolean
    /** 模型下拉宽度：'auto' 表示按内容自适应，其余按给定值。 */
    modelWidth?: string
  }>(),
  {
    thinkingEnabled: true,
    disabled: false,
    loading: false,
    showThinking: true,
    modelLocked: false,
    modelDisabled: false,
    thinkingDisabled: false,
    clearable: true,
    modelWidth: '200px'
  }
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

const modelOff = computed(() => props.disabled || props.modelLocked || props.modelDisabled)
const thinkingOff = computed(() => props.disabled || props.thinkingDisabled)

/** 宽度自适应内容：下拉按当前模型名长短伸缩，不再写死一整块横向空间。 */
const modelAutoWidth = computed(() => props.modelWidth === 'auto')

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
      <ElIcon v-if="modelLocked" class="ai-picker__lock">
        <Lock />
      </ElIcon>
      <ElSelect
        class="ai-picker__model"
        :class="{ 'ai-picker__model--auto': modelAutoWidth }"
        size="small"
        :clearable="clearable"
        :model-value="model"
        :loading="loading"
        :disabled="modelOff"
        :placeholder="t('server.aiPicker.modelPlaceholder')"
        @update:model-value="changeModel"
      >
        <ElOption v-for="id in models" :key="id" :label="id" :value="id" />
      </ElSelect>
    </template>

    <!-- 附加控件：模型列表为空时也要留着（刷新入口不能跟着一起消失） -->
    <slot name="model-extra" />

    <template v-if="showThinkingPicker">
      <span class="ai-picker__label">{{ t('ai.chat.thinking') }}</span>
      <ElSegmented
        size="small"
        :model-value="thinking"
        :options="thinkingOptions"
        :disabled="thinkingOff"
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

/* 模型已锁定：锁图标点明「建会话时定的」 */
.ai-picker__lock {
  color: var(--yg-text-tertiary);
}

.ai-picker__model {
  width: 200px;
  max-width: 100%;
}

/* 自适应宽度：按内容伸缩，长模型名到上限就截断 */
.ai-picker__model--auto {
  width: max-content;
  min-width: 7rem;
  max-width: 15rem;
}
</style>
