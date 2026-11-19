<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { CanaryApiError, createActivity, isLoggedIn } from '@/api/canary'
import type { CreateActivityInput } from '@/api/canary'

const router = useRouter()

const activityName = ref('')
const activityType = ref<CreateActivityInput['type']>('on-campus')
const activityDescription = ref('')
const submitting = ref(false)
const validationError = ref('')
const successMessage = ref('')

const activityTypes: Array<{ value: CreateActivityInput['type']; label: string }> = [
  { value: 'on-campus', label: '校内' },
  { value: 'off-campus', label: '校外' },
  { value: 'social-practice', label: '社会实践' },
  { value: 'hybrid', label: '混合' }
]

function goBack() {
  router.back()
}

function validateForm(): boolean {
  validationError.value = ''

  if (!activityName.value.trim()) {
    validationError.value = '请输入活动名称'
    return false
  }

  if (activityName.value.trim().length < 2) {
    validationError.value = '活动名称至少需要 2 个字符'
    return false
  }

  if (!activityDescription.value.trim()) {
    validationError.value = '请输入活动说明'
    return false
  }

  if (activityDescription.value.trim().length < 10) {
    validationError.value = '活动说明至少需要 10 个字符'
    return false
  }

  return true
}

async function handleSubmit() {
  if (submitting.value || !validateForm()) return

  if (!isLoggedIn()) {
    validationError.value = '请先登录 canary 账号。'
    return
  }

  submitting.value = true
  validationError.value = ''
  successMessage.value = ''

  try {
    await createActivity({
      name: activityName.value.trim(),
      description: activityDescription.value.trim(),
      type: activityType.value
    })
    successMessage.value = '活动已提交，正在等待审核。'
    activityName.value = ''
    activityDescription.value = ''
  } catch (reason) {
    validationError.value =
      reason instanceof CanaryApiError ? reason.message : '提交失败，请稍后重试。'
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <div class="add-volunteer">
    <div class="add-volunteer__container">
      <header class="add-volunteer__header surface-enter" style="--i: 0">
        <button
          type="button"
          class="add-volunteer__back"
          aria-label="返回"
          @click="goBack"
        >
          <svg
            width="20"
            height="20"
            viewBox="0 0 20 20"
            fill="none"
            aria-hidden="true"
          >
            <path
              d="M12 4L6 10L12 16"
              stroke="currentColor"
              stroke-width="1.5"
              stroke-linecap="round"
              stroke-linejoin="round"
            />
          </svg>
        </button>
        <div>
          <h1 class="add-volunteer__title">添加义工</h1>
          <p class="add-volunteer__subtitle">创建新的志愿活动记录</p>
        </div>
      </header>

      <div class="add-volunteer__layout">
        <form class="add-volunteer__form surface-enter" style="--i: 1" @submit.prevent="handleSubmit">
          <div class="form-field">
            <label for="activity-name" class="form-field__label">活动名称</label>
            <input
              id="activity-name"
              v-model="activityName"
              type="text"
              class="form-field__input"
              placeholder="例如：社区环保宣传活动"
              maxlength="100"
              required
              :disabled="submitting"
            />
          </div>

          <div class="form-field">
            <label for="activity-type" class="form-field__label">活动类型</label>
            <div class="form-field__radio-group">
              <label
                v-for="type in activityTypes"
                :key="type.value"
                class="radio-option"
              >
                <input
                  v-model="activityType"
                  type="radio"
                  :value="type.value"
                  class="radio-option__input"
                  :disabled="submitting"
                />
                <span class="radio-option__label">{{ type.label }}</span>
              </label>
            </div>
          </div>

          <div class="form-field">
            <label for="activity-description" class="form-field__label">活动说明</label>
            <textarea
              id="activity-description"
              v-model="activityDescription"
              class="form-field__textarea"
              placeholder="请详细描述活动内容、目标和要求"
              rows="6"
              maxlength="500"
              required
              :disabled="submitting"
            />
            <div class="form-field__hint">
              {{ activityDescription.length }} / 500
            </div>
          </div>

          <div v-if="validationError" class="form-validation" role="alert">
            {{ validationError }}
          </div>

          <div v-if="successMessage" class="form-success" role="status">
            {{ successMessage }}
          </div>

          <div class="form-actions">
            <button
              type="submit"
              class="form-button form-button--primary"
              :disabled="submitting"
            >
              <span v-if="!submitting">提交活动</span>
              <span v-else>提交中...</span>
            </button>
            <button
              type="button"
              class="form-button form-button--secondary"
              :disabled="submitting"
              @click="goBack"
            >
              取消
            </button>
          </div>
        </form>

        <aside class="add-volunteer__hint surface-enter" style="--i: 2">
          <div class="hint-card">
            <h2 class="hint-card__title">提交后进入待审核</h2>
            <p class="hint-card__text">
              活动提交后将进入待审核状态，管理员审核通过后才会生效。请确保填写的信息准确完整。
            </p>
            <ul class="hint-card__list">
              <li>活动名称应简洁明了</li>
              <li>活动类型需符合实际情况</li>
              <li>活动说明需详细描述内容</li>
            </ul>
          </div>
        </aside>
      </div>
    </div>
  </div>
</template>

<style scoped>
.add-volunteer {
  min-height: 100vh;
  padding: var(--space-7) var(--content-padding) var(--space-10);
}

.add-volunteer__container {
  max-width: var(--content-max-width);
  margin: 0 auto;
}

.add-volunteer__header {
  display: flex;
  align-items: flex-start;
  gap: var(--space-4);
  margin-bottom: var(--space-7);
}

.add-volunteer__back {
  position: relative;
  width: 44px;
  height: 44px;
  padding: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(
    135deg,
    rgba(255, 255, 255, 0.72) 0%,
    rgba(232, 242, 252, 0.48) 100%
  );
  -webkit-backdrop-filter: blur(12px) saturate(180%);
  backdrop-filter: blur(12px) saturate(180%);
  border: 1px solid rgba(255, 255, 255, 0.82);
  border-radius: var(--radius-md);
  color: var(--color-text-primary);
  cursor: pointer;
  overflow: hidden;
  isolation: isolate;
  flex-shrink: 0;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.92),
    inset 0 -1px 0 rgba(0, 0, 0, 0.06),
    0 2px 8px rgba(18, 48, 82, 0.08);
  transition:
    transform var(--duration-fast) var(--ease-spring),
    color var(--duration-fast) var(--ease-smooth),
    box-shadow var(--duration-fast) var(--ease-smooth);
}

.add-volunteer__back::before {
  content: '';
  position: absolute;
  inset: 0;
  background: var(--glass-highlight);
  border-radius: var(--radius-md);
  opacity: 0.6;
  pointer-events: none;
  z-index: -1;
}

.add-volunteer__back:hover {
  color: var(--color-brand);
  transform: scale(1.05);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.98),
    inset 0 -1px 0 rgba(0, 0, 0, 0.08),
    0 4px 12px rgba(18, 48, 82, 0.12);
}

.add-volunteer__back:active {
  transform: scale(0.95);
}

.add-volunteer__back:focus-visible {
  outline: 2px solid var(--color-brand);
  outline-offset: -2px;
}

.add-volunteer__title {
  font-size: var(--font-size-title-3);
  font-weight: 600;
  line-height: var(--line-height-snug);
  letter-spacing: var(--letter-spacing-tight);
  color: var(--color-text-primary);
  margin-bottom: var(--space-2);
}

.add-volunteer__subtitle {
  font-size: var(--font-size-callout);
  color: var(--color-text-secondary);
  line-height: var(--line-height-normal);
}

.add-volunteer__layout {
  display: grid;
  gap: var(--space-6);
  grid-template-columns: 1fr;
}

@media (min-width: 1024px) {
  .add-volunteer__layout {
    grid-template-columns: 1fr 360px;
  }
}

/* Form */
.add-volunteer__form {
  position: relative;
  background: var(--glass-bg);
  -webkit-backdrop-filter: var(--glass-blur);
  backdrop-filter: var(--glass-blur);
  border: 1px solid var(--glass-border);
  border-radius: var(--radius-xl);
  padding: var(--space-6);
  box-shadow: var(--glass-shadow);
  display: flex;
  flex-direction: column;
  gap: var(--space-5);
  isolation: isolate;
}

.add-volunteer__form::before {
  content: '';
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 50%;
  background: var(--glass-highlight);
  border-radius: var(--radius-xl) var(--radius-xl) 0 0;
  opacity: 0.4;
  pointer-events: none;
}

.form-field {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  position: relative;
  z-index: 1;
}

.form-field__label {
  font-size: var(--font-size-body);
  font-weight: 600;
  color: var(--color-text-primary);
}

.form-field__input,
.form-field__textarea {
  padding: var(--space-3) var(--space-4);
  font-size: var(--font-size-body);
  font-family: var(--font-system);
  color: var(--color-text-primary);
  background: rgba(255, 255, 255, 0.92);
  border: 1px solid var(--color-border-subtle);
  border-radius: var(--radius-md);
  transition:
    border-color var(--duration-fast) var(--ease-smooth),
    box-shadow var(--duration-fast) var(--ease-smooth);
}

.form-field__input {
  height: 48px;
}

.form-field__textarea {
  resize: vertical;
  min-height: 120px;
  line-height: var(--line-height-relaxed);
}

.form-field__input:focus,
.form-field__textarea:focus {
  outline: none;
  border-color: var(--color-brand);
  box-shadow: 0 0 0 3px rgba(0, 113, 227, 0.12);
}

.form-field__input:disabled,
.form-field__textarea:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.form-field__input::placeholder,
.form-field__textarea::placeholder {
  color: var(--color-text-tertiary);
}

.form-field__hint {
  font-size: var(--font-size-caption);
  color: var(--color-text-tertiary);
  text-align: right;
}

.form-field__radio-group {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(100px, 1fr));
  gap: var(--space-3);
}

.radio-option {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: var(--space-3) var(--space-4);
  background: rgba(255, 255, 255, 0.92);
  border: 1px solid var(--color-border-subtle);
  border-radius: var(--radius-md);
  cursor: pointer;
  transition:
    border-color var(--duration-fast) var(--ease-smooth),
    background-color var(--duration-fast) var(--ease-smooth),
    box-shadow var(--duration-fast) var(--ease-smooth);
  min-height: 48px;
}

.radio-option:has(.radio-option__input:checked) {
  background: rgba(0, 113, 227, 0.08);
  border-color: var(--color-brand);
  box-shadow: 0 0 0 3px rgba(0, 113, 227, 0.08);
}

.radio-option:has(.radio-option__input:focus-visible) {
  outline: 2px solid var(--color-brand);
  outline-offset: 2px;
}

.radio-option__input {
  position: absolute;
  opacity: 0;
  pointer-events: none;
}

.radio-option__label {
  font-size: var(--font-size-body);
  font-weight: 500;
  color: var(--color-text-primary);
  user-select: none;
}

.radio-option:has(.radio-option__input:checked) .radio-option__label {
  color: var(--color-brand);
  font-weight: 600;
}

.form-validation {
  padding: var(--space-3) var(--space-4);
  background: var(--tone-rose-bg);
  border: 1px solid var(--tone-rose);
  border-radius: var(--radius-md);
  color: var(--tone-rose);
  font-size: var(--font-size-callout);
  font-weight: 500;
  position: relative;
  z-index: 1;
}

.form-success {
  padding: var(--space-3) var(--space-4);
  background: var(--tone-green-bg);
  border: 1px solid rgb(52 199 89 / 0.34);
  border-radius: var(--radius-md);
  color: rgb(6, 95, 70);
  font-size: var(--font-size-callout);
  font-weight: 500;
  position: relative;
  z-index: 1;
}

.form-actions {
  display: flex;
  gap: var(--space-3);
  position: relative;
  z-index: 1;
}

.form-button {
  flex: 1;
  padding: var(--space-3) var(--space-5);
  font-size: var(--font-size-body);
  font-weight: 600;
  border-radius: var(--radius-md);
  min-height: 48px;
  cursor: pointer;
  position: relative;
  overflow: hidden;
  isolation: isolate;
  transition:
    transform var(--duration-fast) var(--ease-spring),
    box-shadow var(--duration-fast) var(--ease-smooth);
}

.form-button--primary {
  background: linear-gradient(
    135deg,
    rgba(0, 113, 227, 0.92) 0%,
    rgba(0, 113, 227, 1) 100%
  );
  -webkit-backdrop-filter: blur(8px) saturate(180%);
  backdrop-filter: blur(8px) saturate(180%);
  border: 1px solid rgba(255, 255, 255, 0.82);
  color: var(--color-text-inverse);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.3),
    0 4px 14px rgba(0, 113, 227, 0.24);
}

.form-button--primary::before {
  content: '';
  position: absolute;
  inset: 0;
  background: var(--glass-highlight);
  opacity: 0.3;
  pointer-events: none;
}

.form-button--primary::after {
  content: '';
  position: absolute;
  inset: 0;
  background: var(--glass-sheen);
  opacity: 0;
  transform: translateX(-42%);
  transition:
    opacity var(--duration-base) var(--ease-smooth),
    transform var(--duration-base) var(--ease-smooth);
  pointer-events: none;
}

.form-button--primary:hover {
  transform: scale(1.01);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.4),
    0 6px 18px rgba(0, 113, 227, 0.32);
}

.form-button--primary:hover::after {
  opacity: 0.75;
  transform: translateX(100%);
}

.form-button--primary:active {
  transform: scale(0.98);
}

.form-button--primary:disabled {
  opacity: 0.5;
  cursor: not-allowed;
  transform: none;
}

.form-button--secondary {
  background: linear-gradient(
    135deg,
    rgba(255, 255, 255, 0.82) 0%,
    rgba(232, 242, 252, 0.58) 100%
  );
  -webkit-backdrop-filter: blur(8px) saturate(180%);
  backdrop-filter: blur(8px) saturate(180%);
  border: 1px solid rgba(255, 255, 255, 0.82);
  color: var(--color-text-primary);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.94),
    0 2px 8px rgba(18, 48, 82, 0.08);
}

.form-button--secondary:hover {
  transform: scale(1.01);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.98),
    0 4px 12px rgba(18, 48, 82, 0.12);
}

.form-button--secondary:active {
  transform: scale(0.98);
}

.form-button--secondary:disabled {
  opacity: 0.5;
  cursor: not-allowed;
  transform: none;
}

/* Hint card */
.add-volunteer__hint {
  position: sticky;
  top: calc(var(--space-7));
  align-self: start;
}

.hint-card {
  position: relative;
  background: var(--color-bg-elevated);
  border: 1px solid var(--color-border-hairline);
  border-radius: var(--radius-lg);
  padding: var(--space-6);
  box-shadow: var(--shadow-sm);
}

.hint-card__title {
  font-size: var(--font-size-headline);
  font-weight: 600;
  color: var(--color-text-primary);
  margin-bottom: var(--space-3);
  line-height: var(--line-height-snug);
}

.hint-card__text {
  font-size: var(--font-size-body);
  color: var(--color-text-secondary);
  line-height: var(--line-height-relaxed);
  margin-bottom: var(--space-4);
}

.hint-card__list {
  list-style: none;
  padding: 0;
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.hint-card__list li {
  position: relative;
  padding-left: var(--space-5);
  font-size: var(--font-size-callout);
  color: var(--color-text-secondary);
  line-height: var(--line-height-normal);
}

.hint-card__list li::before {
  content: '';
  position: absolute;
  left: 0;
  top: 0.5em;
  width: 6px;
  height: 6px;
  background: var(--color-brand);
  border-radius: 50%;
}

@media (max-width: 1023px) {
  .add-volunteer__hint {
    position: static;
  }
}

@media (prefers-reduced-motion: reduce) {
  .add-volunteer__back,
  .form-button {
    transition: none;
  }

  .add-volunteer__back:hover,
  .form-button:hover {
    transform: none;
  }

  .form-button--primary::after {
    display: none;
  }
}

@supports not (backdrop-filter: blur(14px)) {
  .add-volunteer__form {
    background: rgba(255, 255, 255, 0.92);
  }

  .add-volunteer__back,
  .form-button--primary,
  .form-button--secondary {
    background: rgba(255, 255, 255, 0.92);
  }
}
</style>
