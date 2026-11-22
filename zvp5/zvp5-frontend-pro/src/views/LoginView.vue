<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { login } from '@/api/canary'
import type { CanaryApiError } from '@/api/canary'
import BubbleFeedback from '@/components/BubbleFeedback.vue'

const router = useRouter()
const route = useRoute()

const studentId = ref('')
const password = ref('')
const loading = ref(false)
const errorMessage = ref('')
const studentIdTouched = ref(false)
const showContent = ref(false)

const currentTime = ref(new Date())
const timeString = computed(() => {
  const h = currentTime.value.getHours().toString().padStart(2, '0')
  const m = currentTime.value.getMinutes().toString().padStart(2, '0')
  return `${h}:${m}`
})
const dateString = computed(() => {
  return currentTime.value.toLocaleDateString('zh-CN', {
    year: 'numeric',
    month: 'long',
    day: 'numeric',
    weekday: 'long'
  })
})

const isValidStudentId = computed(() => {
  return /^\d{8}$/.test(studentId.value.trim())
})

const invalidStudentId = computed(() => studentIdTouched.value && !!studentId.value && !isValidStudentId.value)

const canSubmit = computed(() => {
  return isValidStudentId.value && password.value.length > 0 && !loading.value
})

let clockTimer: ReturnType<typeof setInterval> | null = null

async function handleSubmit() {
  if (!canSubmit.value) return

  loading.value = true
  errorMessage.value = ''

  try {
    await login(studentId.value.trim(), password.value)

    const rawRedirect = route.query.redirect
    const redirect = typeof rawRedirect === 'string' ? rawRedirect : ''
    const isLocal = redirect.startsWith('/') && !/^\/[\\/]/.test(redirect)
    await router.replace(isLocal ? redirect : '/')
  } catch (error) {
    const err = error as CanaryApiError
    errorMessage.value = err.message || '登录失败，请检查学号和密码'
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  // Staggered entrance animation
  requestAnimationFrame(() => {
    showContent.value = true
  })

  // Update clock every minute
  clockTimer = setInterval(() => {
    currentTime.value = new Date()
  }, 60000)
})

onUnmounted(() => {
  // Clean up clock timer to prevent leaks
  if (clockTimer) {
    clearInterval(clockTimer)
  }
})
</script>

<template>
  <div class="login-view">
    <!-- Left atmospheric panel -->
    <div class="aurora-panel" :class="{ visible: showContent }">
      <div class="aurora-canvas" aria-hidden="true"></div>

      <div class="time-shard" :class="{ visible: showContent }">
        <div class="clock-orb">
          <div class="orb-halo"></div>
          <div class="orb-core">
            <div class="time-display">{{ timeString }}</div>
          </div>
        </div>
        <div class="date-display">{{ dateString }}</div>
      </div>

      <div class="welcome-text" :class="{ visible: showContent }">
        <h1 class="headline">志愿管理系统</h1>
        <p class="subhead">Volunteer Management</p>
      </div>
    </div>

    <!-- Right login card -->
    <div class="login-container">
      <div class="login-card glass-material" :class="{ visible: showContent }">
        <div class="card-header">
          <h2 class="card-title">登录</h2>
          <p class="card-subtitle">使用学号和密码继续</p>
        </div>

        <form @submit.prevent="handleSubmit" class="login-form">
          <div class="form-group" :class="{ error: invalidStudentId }">
            <label for="student-id" class="form-label">学号</label>
            <input
              id="student-id"
              v-model="studentId"
              type="text"
              inputmode="numeric"
              autocomplete="username"
              placeholder="8位数字"
              maxlength="8"
              :disabled="loading"
              :aria-invalid="invalidStudentId"
              :aria-describedby="invalidStudentId ? 'student-id-error' : undefined"
              @blur="studentIdTouched = true"
              class="form-input"
              required
            />
            <BubbleFeedback v-if="invalidStudentId" id="student-id-error" class="glass-feedback--error" role="alert">
              请输入有效的8位学号
            </BubbleFeedback>
          </div>

          <div class="form-group">
            <label for="password" class="form-label">密码</label>
            <input
              id="password"
              v-model="password"
              type="password"
              autocomplete="current-password"
              placeholder="输入密码"
              :disabled="loading"
              class="form-input"
              required
            />
          </div>

          <BubbleFeedback v-if="errorMessage" class="glass-feedback--error" role="alert">
            {{ errorMessage }}
          </BubbleFeedback>

          <button
            type="submit"
            :disabled="!canSubmit"
            class="submit-button"
            :class="{ loading }"
          >
            <span v-if="!loading" class="button-label">登录</span>
            <span v-else class="button-spinner" role="status">
              <svg class="spinner-icon" viewBox="0 0 24 24" fill="none" aria-hidden="true">
                <circle class="spinner-track" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="3" />
                <path class="spinner-head" d="M12 2a10 10 0 0110 10" stroke="currentColor" stroke-width="3" stroke-linecap="round" />
              </svg>
              正在登录
            </span>
          </button>
        </form>

        <RouterLink class="login-back liquid-btn liquid-btn--secondary" to="/">
          返回首页
        </RouterLink>
      </div>
    </div>
  </div>
</template>

<style scoped>
.login-view {
  min-height: 100vh;
  display: grid;
  grid-template-columns: 1fr 1fr;
  overflow-x: hidden;
}

/* Left atmospheric panel */
.aurora-panel {
  position: relative;
  display: flex;
  flex-direction: column;
  justify-content: center;
  padding: var(--space-10);
  overflow: hidden;
  opacity: 0;
  transform: translateX(-2rem);
  transition: opacity var(--duration-slow) var(--ease-settle), transform var(--duration-slow) var(--ease-settle);
}

.aurora-panel.visible {
  opacity: 1;
  transform: translateX(0);
}

.aurora-canvas {
  position: absolute;
  inset: 6% auto auto -24%;
  width: min(57vw, 830px);
  aspect-ratio: 1;
  border-radius: 50%;
  border: 1px solid #ffffff8a;
  background:
    radial-gradient(circle, #fff9ed54, transparent 67%),
    radial-gradient(circle, transparent 57%, #ffffff48 57.2%, transparent 57.5%, transparent 64%, #ffffff68 64.2%, transparent 64.5%);
  pointer-events: none;
}

@media (forced-colors: active) {
  .aurora-canvas { display: none; }
}

.time-shard {
  position: relative;
  z-index: 1;
  margin-bottom: var(--space-10);
  opacity: 0;
  transform: translateY(1rem);
  transition: opacity var(--duration-base) var(--ease-settle) 65ms, transform var(--duration-base) var(--ease-settle) 65ms;
}

.time-shard.visible {
  opacity: 1;
  transform: translateY(0);
}

.clock-orb {
  position: relative;
  width: 120px;
  height: 120px;
  margin-bottom: var(--space-4);
}

.orb-halo {
  position: absolute;
  inset: -12px;
  border-radius: 50%;
  background: conic-gradient(from 35deg, #e4ecff54, #b4d6ec9e, #ffcbb380, #e4ecff54);
  opacity: 0.62;
  border: 1px solid #ffffffa8;
}

.orb-core {
  position: relative;
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--glass-floating-bg);
  border-radius: 50%;
  border: 1px solid var(--glass-border);
  box-shadow: var(--glass-floating-shadow);
}

.time-display {
  font-size: var(--font-size-title-3);
  font-weight: 600;
  letter-spacing: -0.02em;
  color: var(--color-text-primary);
  font-variant-numeric: tabular-nums;
}

.date-display {
  font-size: var(--font-size-callout);
  color: var(--color-text-secondary);
  font-weight: 500;
  letter-spacing: 0.01em;
}

.welcome-text {
  position: relative;
  z-index: 1;
  opacity: 0;
  transform: translateY(1rem);
  transition: opacity var(--duration-base) var(--ease-settle) 110ms, transform var(--duration-base) var(--ease-settle) 110ms;
}

.welcome-text.visible {
  opacity: 1;
  transform: translateY(0);
}

.headline {
  font-size: var(--font-size-hero);
  font-weight: 700;
  letter-spacing: -0.03em;
  color: var(--color-text-primary);
  margin: 0 0 var(--space-3) 0;
  line-height: 1.1;
}

.subhead {
  font-size: var(--font-size-body-large);
  color: var(--color-text-secondary);
  font-weight: 500;
  margin: 0;
  letter-spacing: 0.02em;
  text-transform: uppercase;
}

/* Right login container */
.login-container {
  display: flex;
  align-items: center;
  justify-content: flex-start;
  min-width: 0;
  padding: clamp(32px, 6vw, 96px);
  padding-left: var(--space-7);
}

.login-card {
  position: relative;
  width: 100%;
  max-width: 440px;
  padding: clamp(28px, 4vw, 60px);
  opacity: 0;
  transform: translateX(2rem);
  transition: opacity var(--duration-slow) var(--ease-settle) 70ms, transform var(--duration-slow) var(--ease-settle) 70ms;
}

.login-card.visible {
  opacity: 1;
  transform: translateX(0);
}

.glass-material {
  position: relative;
  isolation: isolate;
  overflow: hidden;
  background: var(--glass-bg);
  -webkit-backdrop-filter: var(--glass-blur);
  backdrop-filter: var(--glass-blur);
  border-radius: var(--radius-xl);
  border: 1px solid var(--glass-border);
  box-shadow: var(--glass-floating-shadow);
}

.glass-material::before {
  content: '';
  position: absolute;
  inset: 0;
  z-index: 0;
  background: var(--glass-highlight);
  border-radius: inherit;
  pointer-events: none;
}

.glass-material::after {
  content: '';
  position: absolute;
  inset: 0;
  z-index: 0;
  background: var(--glass-sheen);
  border-radius: inherit;
  opacity: 0.42;
  transform: translateX(-38%);
  pointer-events: none;
}

.glass-material > * {
  position: relative;
  z-index: 1;
}

.card-header {
  margin-bottom: var(--space-7);
}

.card-title {
  font-size: var(--font-size-title-3);
  font-weight: 700;
  letter-spacing: -0.02em;
  color: var(--color-text-primary);
  margin: 0 0 var(--space-2) 0;
}

.card-subtitle {
  font-size: var(--font-size-callout);
  color: var(--color-text-secondary);
  font-weight: 500;
  margin: 0;
}

.login-form {
  display: flex;
  flex-direction: column;
  gap: var(--space-5);
}

.form-group {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.form-label {
  font-size: var(--font-size-callout);
  font-weight: 600;
  color: var(--color-text-primary);
  letter-spacing: 0.01em;
}

.form-input {
  width: 100%;
  padding: var(--space-4) var(--space-5);
  font-size: var(--font-size-body);
  color: var(--color-text-primary);
  background: rgba(255, 255, 255, 0.64);
  border: 1px solid var(--color-border-moderate);
  border-radius: var(--radius-md);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.9);
  transition:
    border-color var(--duration-fast) var(--ease-smooth),
    box-shadow var(--duration-fast) var(--ease-smooth),
    background-color var(--duration-fast) var(--ease-smooth);
  outline: none;
}

.form-input::placeholder {
  color: var(--color-text-tertiary);
}

.form-input:hover:not(:disabled) {
  border-color: rgba(0, 113, 227, 0.24);
  background: rgba(255, 255, 255, 0.78);
}

.form-input:focus {
  border-color: var(--color-brand);
  background: #ffffff;
  box-shadow: 0 0 0 3px rgba(0, 113, 227, 0.14);
}

.form-input:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.form-group.error .form-input {
  border-color: var(--tone-rose);
}

.submit-button {
  position: relative;
  isolation: isolate;
  overflow: hidden;
  width: 100%;
  padding: var(--space-4) var(--space-7);
  font-size: var(--font-size-body);
  font-weight: 650;
  color: #ffffff;
  background: linear-gradient(
    135deg,
    #205988f5,
    #28649aee 49%,
    #294d84f5
  );
  border: 1px solid rgba(255, 255, 255, 0.5);
  border-radius: var(--radius-md);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.58),
    inset 0 -1px 0 rgba(0, 0, 0, 0.08),
    0 8px 22px rgba(0, 113, 227, 0.2);
  cursor: pointer;
  outline: none;
  margin-top: var(--space-3);
  transition:
    transform var(--duration-fast) var(--ease-spring),
    box-shadow var(--duration-fast) var(--ease-smooth);
}

.submit-button::before {
  content: '';
  position: absolute;
  inset: 0;
  z-index: 0;
  background: var(--glass-highlight);
  border-radius: inherit;
  opacity: 0.22;
  pointer-events: none;
}

.submit-button::after {
  content: '';
  position: absolute;
  inset: 0;
  z-index: 0;
  background: var(--glass-sheen);
  border-radius: inherit;
  opacity: 0;
  transform: translateX(-120%);
  pointer-events: none;
  transition:
    opacity var(--duration-base) var(--ease-smooth),
    transform var(--duration-base) var(--ease-smooth);
}

.submit-button:hover:not(:disabled) {
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.68),
    inset 0 -1px 0 rgba(0, 0, 0, 0.08),
    0 12px 28px rgba(0, 113, 227, 0.26);
  transform: translateY(-1px);
}

.submit-button:hover:not(:disabled)::after {
  opacity: 0.18;
  transform: translateX(120%);
}

.submit-button:active:not(:disabled) {
  transform: translateY(0) scale(0.99);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.45),
    0 4px 12px rgba(0, 113, 227, 0.18);
}

.submit-button:focus-visible {
  outline: 2px solid var(--color-brand);
  outline-offset: 2px;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.6),
    0 0 0 3px rgba(0, 113, 227, 0.18);
}

.submit-button:disabled {
  background: #dce8f3;
  color: #3d5a76;
  cursor: not-allowed;
  transform: none;
}

.submit-button.loading {
  cursor: wait;
}

.button-label,
.button-spinner {
  position: relative;
  z-index: 2;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: var(--space-2);
}

.spinner-icon {
  width: 20px;
  height: 20px;
  animation: spin 0.8s linear infinite;
}

.login-back {
  width: 100%;
  min-height: 44px;
  margin-top: var(--space-4);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: 1px solid var(--glass-border);
  border-radius: var(--radius-md);
  background: var(--glass-floating-bg);
  box-shadow: var(--glass-rim);
  color: var(--color-text-secondary);
  font-size: var(--font-size-callout);
  font-weight: 600;
  text-decoration: none;
  transition:
    background-color var(--duration-fast) var(--ease-smooth),
    color var(--duration-fast) var(--ease-smooth);
}

.login-back:hover {
  background: rgba(0, 113, 227, 0.08);
  color: var(--color-brand);
}

.login-back:focus-visible {
  outline: 2px solid var(--color-brand);
  outline-offset: 2px;
}

.spinner-track {
  opacity: 0.25;
}

.spinner-head {
  opacity: 0.75;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}

@media (prefers-reduced-motion: reduce) {
  .spinner-icon {
    animation-duration: 1.6s;
  }

  .submit-button,
  .submit-button::after {
    transition: none;
  }
}

/* Responsive: tablet breakpoint */
@media (max-width: 1024px) {
  .login-view {
    grid-template-columns: 1fr;
  }

  .aurora-panel {
    display: none;
  }

  .login-container {
    justify-content: center;
    padding: var(--space-7);
  }

  .login-card {
    max-width: 480px;
  }
}

/* Responsive: mobile breakpoint */
@media (max-width: 640px) {
  .login-container {
    padding: var(--space-5);
  }

  .login-card {
    padding: clamp(22px, 7vw, 42px);
  }

  .card-title {
    font-size: var(--font-size-headline);
  }

  .headline {
    font-size: var(--font-size-title-2);
  }

  .time-display {
    font-size: var(--font-size-headline);
  }

  .clock-orb {
    width: 96px;
    height: 96px;
  }
}

/* Reduced motion overrides */
@media (prefers-reduced-motion: reduce) {
  .aurora-panel,
  .login-card,
  .time-shard,
  .welcome-text {
    transition: opacity 0.15s linear;
    transform: none;
  }

  .submit-button:hover:not(:disabled) {
    transform: none;
  }

  .submit-button:active:not(:disabled) {
    transform: none;
  }
}
@media (prefers-reduced-transparency: reduce), (prefers-contrast: more) {
  .submit-button:not(:disabled) { background: #205988; }
  .submit-button::before,
  .submit-button::after { display: none; }
}
</style>
