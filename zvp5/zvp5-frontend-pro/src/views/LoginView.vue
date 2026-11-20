<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { login } from '@/api/canary'
import type { CanaryApiError } from '@/api/canary'

const router = useRouter()
const route = useRoute()

const studentId = ref('')
const password = ref('')
const loading = ref(false)
const errorMessage = ref('')
const hasAttempted = ref(false)
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

const canSubmit = computed(() => {
  return isValidStudentId.value && password.value.length > 0 && !loading.value
})

let clockTimer: ReturnType<typeof setInterval> | null = null

async function handleSubmit() {
  if (!canSubmit.value) return

  loading.value = true
  errorMessage.value = ''
  hasAttempted.value = true

  try {
    await login(studentId.value.trim(), password.value)

    const rawRedirect = route.query.redirect
    const redirect = typeof rawRedirect === 'string' ? rawRedirect : ''
    if (redirect.startsWith('/')) {
      router.push(redirect)
    } else {
      router.push('/me')
    }
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
      <div class="aurora-canvas">
        <div class="aurora-orb aurora-orb-1"></div>
        <div class="aurora-orb aurora-orb-2"></div>
        <div class="aurora-orb aurora-orb-3"></div>
      </div>

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
        <div class="refraction-band"></div>

        <div class="card-header">
          <h2 class="card-title">登录</h2>
          <p class="card-subtitle">使用学号和密码继续</p>
        </div>

        <form @submit.prevent="handleSubmit" class="login-form">
          <div class="form-group" :class="{ error: hasAttempted && !isValidStudentId }">
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
              class="form-input"
              required
            />
            <span v-if="hasAttempted && !isValidStudentId" class="field-hint error">
              请输入有效的8位学号
            </span>
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

          <div v-if="errorMessage" class="error-banner" role="alert">
            <svg class="error-icon" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
              <path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM8.28 7.22a.75.75 0 00-1.06 1.06L8.94 10l-1.72 1.72a.75.75 0 101.06 1.06L10 11.06l1.72 1.72a.75.75 0 101.06-1.06L11.06 10l1.72-1.72a.75.75 0 00-1.06-1.06L10 8.94 8.28 7.22z" clip-rule="evenodd" />
            </svg>
            {{ errorMessage }}
          </div>

          <button
            type="submit"
            :disabled="!canSubmit"
            class="submit-button"
            :class="{ loading }"
          >
            <span v-if="!loading" class="button-label">登录</span>
            <span v-else class="button-spinner">
              <svg class="spinner-icon" viewBox="0 0 24 24" fill="none">
                <circle class="spinner-track" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="3" />
                <path class="spinner-head" d="M12 2a10 10 0 0110 10" stroke="currentColor" stroke-width="3" stroke-linecap="round" />
              </svg>
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
  background: linear-gradient(135deg, var(--color-bg-canvas) 0%, var(--color-bg-canvas-sky) 50%, var(--color-bg-canvas) 100%);
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
  transition: opacity 0.8s ease-out, transform 0.8s ease-out;
}

.aurora-panel.visible {
  opacity: 1;
  transform: translateX(0);
}

.aurora-canvas {
  position: absolute;
  inset: 0;
  overflow: hidden;
}

.aurora-orb {
  position: absolute;
  border-radius: 50%;
  filter: blur(80px);
  opacity: 0.4;
  animation: aurora-float 20s ease-in-out infinite;
}

.aurora-orb-1 {
  width: 600px;
  height: 600px;
  background: radial-gradient(circle, var(--color-brand) 0%, transparent 70%);
  top: -10%;
  left: -10%;
  animation-delay: 0s;
}

.aurora-orb-2 {
  width: 500px;
  height: 500px;
  background: radial-gradient(circle, var(--tone-teal) 0%, transparent 70%);
  top: 30%;
  left: 20%;
  animation-delay: -7s;
}

.aurora-orb-3 {
  width: 400px;
  height: 400px;
  background: radial-gradient(circle, var(--tone-blue) 0%, transparent 70%);
  bottom: 10%;
  left: -5%;
  animation-delay: -14s;
}

@keyframes aurora-float {
  0%, 100% {
    transform: translate(0, 0) scale(1);
  }
  33% {
    transform: translate(30px, -40px) scale(1.1);
  }
  66% {
    transform: translate(-20px, 30px) scale(0.95);
  }
}

@media (prefers-reduced-motion: reduce) {
  .aurora-orb {
    animation: none;
  }
}

.time-shard {
  position: relative;
  z-index: 1;
  margin-bottom: var(--space-10);
  opacity: 0;
  transform: translateY(1rem);
  transition: opacity 0.6s ease-out 0.1s, transform 0.6s ease-out 0.1s;
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
  background: conic-gradient(
    from 0deg,
    var(--color-brand),
    var(--tone-teal),
    var(--color-brand)
  );
  opacity: 0.3;
  filter: blur(16px);
  animation: orb-pulse 4s ease-in-out infinite;
}

@keyframes orb-pulse {
  0%, 100% {
    opacity: 0.3;
    transform: scale(1);
  }
  50% {
    opacity: 0.5;
    transform: scale(1.05);
  }
}

@media (prefers-reduced-motion: reduce) {
  .orb-halo {
    animation: none;
  }
}

.orb-core {
  position: relative;
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(255, 255, 255, 0.85);
  backdrop-filter: blur(12px);
  border-radius: 50%;
  border: 1px solid rgba(0, 0, 0, 0.08);
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.08);
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
  transition: opacity 0.6s ease-out 0.2s, transform 0.6s ease-out 0.2s;
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
  padding: var(--space-10);
  padding-left: var(--space-7);
}

.login-card {
  position: relative;
  width: 100%;
  max-width: 440px;
  padding: var(--space-10);
  opacity: 0;
  transform: translateX(2rem);
  transition: opacity 0.8s ease-out 0.15s, transform 0.8s ease-out 0.15s;
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
  box-shadow: var(--glass-shadow);
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

.refraction-band {
  position: absolute;
  top: 0;
  right: 0;
  bottom: 0;
  width: 120px;
  background: linear-gradient(
    115deg,
    transparent 0%,
    rgba(255, 255, 255, 0.05) 50%,
    transparent 100%
  );
  border-radius: var(--radius-xl);
  pointer-events: none;
  opacity: 0.38;
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

.field-hint {
  font-size: var(--font-size-caption);
  color: var(--color-text-tertiary);
}

.field-hint.error {
  color: var(--tone-rose);
}

.error-banner {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-4) var(--space-5);
  background: rgba(244, 63, 94, 0.1);
  border: 1px solid rgba(244, 63, 94, 0.3);
  border-radius: var(--radius-md);
  color: var(--tone-rose);
  font-size: var(--font-size-callout);
  font-weight: 500;
}

.error-icon {
  width: 20px;
  height: 20px;
  flex-shrink: 0;
}

.submit-button {
  position: relative;
  width: 100%;
  padding: var(--space-4) var(--space-7);
  font-size: var(--font-size-body);
  font-weight: 600;
  color: #ffffff;
  background: var(--color-brand);
  border: none;
  border-radius: var(--radius-md);
  cursor: pointer;
  transition: all 0.2s ease-out;
  outline: none;
  margin-top: var(--space-3);
}

.submit-button:hover:not(:disabled) {
  background: #0077ed;
  box-shadow: 0 8px 24px rgba(0, 113, 227, 0.3);
  transform: translateY(-1px);
}

.submit-button:active:not(:disabled) {
  transform: translateY(0);
  box-shadow: 0 4px 12px rgba(0, 113, 227, 0.2);
}

.submit-button:focus-visible {
  box-shadow: 0 0 0 3px rgba(0, 113, 227, 0.4);
}

.submit-button:disabled {
  opacity: 0.5;
  cursor: not-allowed;
  transform: none;
}

.submit-button.loading {
  cursor: wait;
}

.button-label,
.button-spinner {
  display: flex;
  align-items: center;
  justify-content: center;
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
  border: 1px solid var(--color-border-moderate);
  border-radius: var(--radius-md);
  background: rgba(255, 255, 255, 0.5);
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
    padding: var(--space-7);
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
</style>
