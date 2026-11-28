<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import MarkdownEditor from '@/shared/ui/markdown/MarkdownEditor.vue'

const props = withDefaults(
  defineProps<{
    /** header：卡片内联（通知页）；modal：弹窗内（管理页） */
    variant?: 'header' | 'modal'
    initialTitle?: string
    initialContent?: string
    submitting?: boolean
    serverError?: string
    /** 后端要求管理员发布时复核密码；为 true 时表单带密码字段 */
    requirePassword?: boolean
  }>(),
  {
    variant: 'header',
    initialTitle: '',
    initialContent: '',
    submitting: false,
    serverError: '',
    requirePassword: false
  }
)

const emit = defineEmits<{
  submit: [payload: { title: string; content: string; password: string }]
}>()

const { t } = useI18n()

const TITLE_MAX = 200
const CONTENT_MAX = 4000

const title = ref(props.initialTitle)
const content = ref(props.initialContent)
const password = ref('')
const error = ref('')

const canSubmit = computed(
  () =>
    title.value.trim().length > 0 &&
    content.value.trim().length > 0 &&
    (!props.requirePassword || password.value.length > 0) &&
    !props.submitting
)

function reset() {
  title.value = ''
  content.value = ''
  password.value = ''
  error.value = ''
}

function submit() {
  if (props.submitting) return
  if (!title.value.trim() || !content.value.trim()) {
    error.value = t('notifications.required')
    return
  }
  if (props.requirePassword && !password.value) {
    error.value = t('manage.notify.required')
    return
  }
  error.value = ''
  emit('submit', {
    title: title.value.trim(),
    content: content.value.trim(),
    password: password.value
  })
}

watch(
  () => props.initialTitle,
  (value) => {
    title.value = value
  }
)
watch(
  () => props.initialContent,
  (value) => {
    content.value = value
  }
)

defineExpose({ reset })
</script>

<template>
  <form class="notify-compose" :class="`variant-${variant}`" @submit.prevent="submit">
    <div class="compose-head">
      <div class="compose-title">
        <h2>{{ t('notifications.compose') }}</h2>
        <p class="compose-hint">{{ t('notifications.composeHint') }}</p>
      </div>
    </div>

    <label class="field">
      <span class="field-label">{{ t('notifications.composeTitle') }}</span>
      <input
        v-model="title"
        class="input"
        type="text"
        :maxlength="TITLE_MAX"
        :placeholder="t('notifications.composeTitlePlaceholder')"
        :disabled="submitting"
      />
      <span class="field-count num">{{ title.length }} / {{ TITLE_MAX }}</span>
    </label>

    <MarkdownEditor
      v-model="content"
      :placeholder="t('notifications.composeContentPlaceholder')"
      :maxlength="CONTENT_MAX"
      :disabled="submitting"
      min-height="180px"
    />

    <label v-if="requirePassword" class="field">
      <span class="field-label">{{ t('manage.notify.password') }}</span>
      <input
        v-model="password"
        class="input"
        type="password"
        autocomplete="current-password"
        :placeholder="t('manage.notify.passwordHint')"
        :disabled="submitting"
      />
    </label>

    <p v-if="error || serverError" class="compose-error">{{ error || serverError }}</p>

    <div class="compose-foot">
      <button type="button" class="ghost-btn" :disabled="submitting" @click="reset">
        {{ t('common.cancel') }}
      </button>
      <button type="submit" class="primary-btn" :disabled="!canSubmit">
        <span v-if="submitting" class="spinner" aria-hidden="true" />
        {{ t('notifications.compose') }}
      </button>
    </div>
  </form>
</template>

<style scoped>
.notify-compose {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.variant-modal {
  gap: var(--space-5);
}

.compose-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-4);
}

.compose-title h2 {
  font-size: var(--text-base);
  font-weight: 600;
  color: var(--ink);
}

.compose-hint {
  margin-top: 0.2rem;
  font-size: var(--text-2xs);
  color: var(--ink-faint);
}

.field {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
}

.field-label {
  font-size: var(--text-sm);
  font-weight: 550;
  color: var(--ink-soft);
}

.field-count {
  position: absolute;
  right: 0;
  top: 0;
  font-size: var(--text-2xs);
  color: var(--ink-faint);
  font-variant-numeric: tabular-nums;
}

.input {
  width: 100%;
  padding: 0.7rem 0.95rem;
  border-radius: var(--radius-md);
  border: 1px solid var(--line);
  background: var(--surface-raised);
  color: var(--ink);
  font-family: var(--font-sans);
  font-size: var(--text-md);
  box-shadow: var(--shadow-xs);
  transition:
    border-color var(--dur-fast) var(--ease-out),
    box-shadow var(--dur-fast) var(--ease-out);
}

.input:focus {
  outline: none;
  border-color: color-mix(in srgb, var(--accent) 55%, var(--line));
  box-shadow: 0 0 0 4px color-mix(in srgb, var(--accent) 14%, transparent);
}

.input:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.compose-error {
  font-size: var(--text-xs);
  color: var(--c-danger);
}

.compose-foot {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: var(--space-3);
}

.ghost-btn,
.primary-btn {
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
  padding: 0.5rem 1.05rem;
  border-radius: var(--radius-pill);
  font-size: var(--text-sm);
  font-weight: 550;
  cursor: pointer;
  transition:
    transform var(--dur-fast) var(--ease-out),
    filter var(--dur-fast) var(--ease-out),
    opacity var(--dur-fast) var(--ease-out);
}

.ghost-btn {
  border: 1px solid var(--line);
  background: transparent;
  color: var(--ink-muted);
}

.ghost-btn:hover:not(:disabled) {
  color: var(--ink);
  border-color: var(--line-strong, var(--line));
}

.primary-btn {
  border: 0;
  background: var(--accent);
  color: var(--accent-contrast, #fff);
  box-shadow: 0 8px 20px -12px color-mix(in srgb, var(--accent) 70%, transparent);
}

.primary-btn:hover:not(:disabled) {
  filter: brightness(1.04);
  transform: translateY(-1px);
}

.ghost-btn:disabled,
.primary-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
  transform: none;
}

.spinner {
  width: 12px;
  height: 12px;
  border-radius: 50%;
  border: 2px solid color-mix(in srgb, currentColor 40%, transparent);
  border-top-color: currentColor;
  animation: spin 0.7s linear infinite;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}

@media (prefers-reduced-motion: reduce) {
  .spinner {
    animation-duration: 1.6s;
  }
}
</style>
