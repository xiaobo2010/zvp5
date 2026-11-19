<script setup lang="ts">
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { CanaryApiError, login } from '@/api/canary'

const router = useRouter()
const route = useRoute()

const id = ref('')
const password = ref('')
const loading = ref(false)
const error = ref('')

function redirectTarget(): string {
  const target = route.query.redirect
  return typeof target === 'string' && target.startsWith('/') ? target : '/me'
}

async function submit() {
  if (loading.value) return
  error.value = ''
  if (!/^\d{8}$/.test(id.value.trim())) {
    error.value = '请输入 8 位学号。'
    return
  }
  if (!password.value) {
    error.value = '请输入密码。'
    return
  }

  loading.value = true
  try {
    await login(id.value.trim(), password.value)
    await router.replace(redirectTarget())
  } catch (reason) {
    error.value =
      reason instanceof CanaryApiError ? reason.message : '登录失败，请稍后再试。'
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="login-page">
    <section class="glass-card login-card" aria-labelledby="login-title">
      <p class="login-eyebrow">ZVMS</p>
      <h1 id="login-title" class="login-title">欢迎回来</h1>
      <p class="login-subtitle">登录后查看你的义工记录与个人资料。</p>

      <form class="login-form" novalidate @submit.prevent="submit">
        <label class="login-field">
          <span>学号</span>
          <input
            v-model="id"
            type="text"
            inputmode="numeric"
            autocomplete="username"
            placeholder="8 位学号"
            :disabled="loading"
          />
        </label>

        <label class="login-field">
          <span>密码</span>
          <input
            v-model="password"
            type="password"
            autocomplete="current-password"
            placeholder="输入密码"
            :disabled="loading"
          />
        </label>

        <p v-if="error" class="login-error" role="alert">{{ error }}</p>

        <button class="liquid-btn liquid-btn--primary" type="submit" :disabled="loading">
          {{ loading ? '正在登录…' : '登录' }}
        </button>
      </form>

      <RouterLink class="login-back liquid-btn liquid-btn--secondary" to="/">
        返回首页
      </RouterLink>
    </section>
  </div>
</template>

<style scoped>
.login-page {
  min-height: 100vh;
  padding: var(--space-8) var(--content-padding);
  display: flex;
  align-items: center;
  justify-content: center;
  background:
    radial-gradient(900px 560px at 18% -4%, rgba(0, 113, 227, 0.12), transparent 64%),
    radial-gradient(720px 460px at 88% 20%, rgba(120, 205, 255, 0.14), transparent 70%);
}

.glass-card {
  position: relative;
  background: var(--glass-bg);
  -webkit-backdrop-filter: var(--glass-blur);
  backdrop-filter: var(--glass-blur);
  border: 1px solid var(--glass-border);
  border-radius: var(--radius-xl);
  box-shadow: var(--glass-shadow);
  isolation: isolate;
}

.glass-card::before {
  content: '';
  position: absolute;
  inset: 0;
  background: var(--glass-highlight);
  border-radius: var(--radius-xl);
  pointer-events: none;
  z-index: 0;
}

.glass-card > * {
  position: relative;
  z-index: 1;
}

.login-card {
  width: min(420px, 100%);
  padding: var(--space-7);
  display: grid;
  gap: var(--space-4);
}

.login-eyebrow {
  margin: 0;
  color: var(--color-text-tertiary);
  font-size: var(--font-size-caption);
  font-weight: 600;
  letter-spacing: var(--letter-spacing-wide);
  text-transform: uppercase;
}

.login-title {
  margin: 0;
  font-size: var(--font-size-title-3);
  color: var(--color-text-primary);
}

.login-subtitle {
  margin: 0 0 var(--space-2);
  color: var(--color-text-secondary);
  font-size: var(--font-size-callout);
}

.login-form {
  display: grid;
  gap: var(--space-4);
}

.login-field {
  display: grid;
  gap: var(--space-2);
  color: var(--color-text-secondary);
  font-size: var(--font-size-caption);
  font-weight: 500;
}

.login-field input {
  min-height: 44px;
  padding: 0 var(--space-4);
  background: rgba(255, 255, 255, 0.42);
  -webkit-backdrop-filter: blur(10px) saturate(160%);
  backdrop-filter: blur(10px) saturate(160%);
  border: 1px solid rgba(255, 255, 255, 0.66);
  border-radius: var(--radius-md);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.8);
  color: var(--color-text-primary);
  font: inherit;
  font-size: var(--font-size-body);
}

.login-field input:focus-visible {
  outline: 2px solid var(--color-brand);
  outline-offset: 2px;
}

.login-error {
  margin: 0;
  padding: var(--space-3) var(--space-4);
  background: rgba(255, 69, 58, 0.1);
  border: 1px solid rgba(255, 69, 58, 0.24);
  border-radius: var(--radius-md);
  color: rgb(153, 27, 27);
  font-size: var(--font-size-callout);
}

.login-back {
  width: 100%;
}

.liquid-btn {
  position: relative;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: var(--space-2);
  min-height: 44px;
  padding: var(--space-3) var(--space-5);
  border-radius: var(--radius-pill);
  border: 1px solid;
  font-size: var(--font-size-callout);
  font-weight: 600;
  cursor: pointer;
  transition: all var(--duration-fast) var(--ease-spring);
  isolation: isolate;
  overflow: hidden;
}

.liquid-btn::before {
  content: '';
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 50%;
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.75), rgba(255, 255, 255, 0));
  border-radius: var(--radius-pill) var(--radius-pill) 0 0;
  pointer-events: none;
  opacity: 0.5;
}

.liquid-btn::after {
  content: '';
  position: absolute;
  inset: 0;
  background: var(--glass-sheen);
  border-radius: var(--radius-pill);
  opacity: 0;
  transform: translateX(-100%);
  transition: all var(--duration-base) var(--ease-smooth);
  pointer-events: none;
}

.liquid-btn:hover::after {
  opacity: 1;
  transform: translateX(100%);
}

.liquid-btn:active {
  transform: scale(0.96);
}

.liquid-btn:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}

.liquid-btn--primary {
  background: linear-gradient(135deg, rgba(0, 113, 227, 0.95), rgba(0, 113, 227, 0.85));
  -webkit-backdrop-filter: blur(12px) saturate(170%);
  backdrop-filter: blur(12px) saturate(170%);
  border-color: rgba(255, 255, 255, 0.72);
  color: white;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.45),
    0 4px 12px rgba(0, 113, 227, 0.3);
}

.liquid-btn--secondary {
  background: linear-gradient(135deg, rgba(255, 255, 255, 0.48), rgba(255, 255, 255, 0.28));
  -webkit-backdrop-filter: blur(10px) saturate(170%);
  backdrop-filter: blur(10px) saturate(170%);
  border-color: rgba(255, 255, 255, 0.72);
  color: var(--color-text-primary);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.85),
    inset 0 -1px 0 rgba(0, 0, 0, 0.08),
    0 2px 6px rgba(18, 48, 82, 0.06);
}

@media (max-width: 767px) {
  .login-page {
    padding: var(--space-6) var(--space-4);
  }

  .login-card {
    padding: var(--space-5);
  }
}

@media (prefers-reduced-motion: reduce) {
  .liquid-btn::after {
    display: none;
  }

  .liquid-btn:active {
    transform: none;
  }
}
</style>
