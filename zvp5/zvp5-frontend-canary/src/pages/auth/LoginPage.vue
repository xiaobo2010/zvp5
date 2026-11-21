<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useSessionStore } from '@/shared/stores/session'
import { useUiStore } from '@/shared/stores/ui'
import { ApiError } from '@/shared/http/errors'
import ZField from '@/shared/ui/ZField.vue'
import ZButton from '@/shared/ui/ZButton.vue'
import ZAlert from '@/shared/ui/ZAlert.vue'
import ZIcon from '@/shared/ui/ZIcon.vue'

const { t, locale } = useI18n()
const router = useRouter()
const route = useRoute()
const session = useSessionStore()
const ui = useUiStore()

const id = ref('')
const password = ref('')
const error = ref('')
const mode = ref<'password' | 'device'>('password')
const challenge = ref('')
const tempPassword = ref('')

const canSubmit = computed(() => id.value.trim().length > 0 && password.value.length > 0)

onMounted(() => {
  if (!window.crypto?.subtle) {
    error.value = t('login.unsupported')
  }
})

function safeRedirect(): string {
  const target = route.query.redirect
  return typeof target === 'string' && target.startsWith('/') && !target.startsWith('//')
    ? target
    : '/'
}

function describeError(cause: unknown): string {
  if (cause instanceof ApiError) {
    if (cause.kind === 'banned') return t('login.banned')
    if (cause.kind === 'maintenance') return t('login.maintenance')
    if (cause.status === 401 || cause.status === 403) return t('login.invalid')
    if (cause.kind === 'network') return cause.message
    return cause.message || t('common.error')
  }
  return t('common.error')
}

async function submit() {
  if (!canSubmit.value || session.busy) return
  error.value = ''
  try {
    // 6 位纯数字视为动态码：后端约定用 *code* 形式区分密码。
    const credential = /^\d{6}$/.test(password.value) ? `*${password.value}*` : password.value
    await session.signIn(id.value.trim(), credential)
    await router.replace(safeRedirect())
  } catch (cause) {
    if (cause instanceof ApiError) {
      const detail = cause.detail as { code?: string; challenge?: string } | undefined
      if (
        (detail?.code === 'DEVICE_NOT_PRIMARY' || detail?.code === 'DEVICE_REQUIRED') &&
        detail.challenge
      ) {
        challenge.value = detail.challenge
        mode.value = 'device'
        return
      }
    }
    error.value = describeError(cause)
  }
}

async function submitTemp() {
  if (!tempPassword.value || session.busy) return
  error.value = ''
  try {
    await session.signInWithTemp(challenge.value, tempPassword.value)
    await router.replace(safeRedirect())
  } catch (cause) {
    error.value = describeError(cause)
  }
}

function backToPassword() {
  mode.value = 'password'
  tempPassword.value = ''
  error.value = ''
}

function toggleLocale() {
  const next = locale.value === 'zh-CN' ? 'en-US' : 'zh-CN'
  locale.value = next
  ui.setLocale(next)
}
</script>

<template>
  <div class="auth">
    <section class="story">
      <div class="story-top">
        <div class="brand">
          <span class="mark" aria-hidden="true">Z</span>
          <span class="brand-text">
            <strong>ZVP5</strong>
            <small>{{ t('common.appTagline') }}</small>
          </span>
        </div>
        <span class="canary">CANARY</span>
      </div>

      <div class="story-body">
        <p class="eyebrow">{{ t('login.eyebrow') }}</p>
        <h1 class="headline">{{ t('login.title') }}</h1>
        <p class="lede">{{ t('login.subtitle') }}</p>
      </div>

      <div class="story-foot">
        <span class="foot-note">Zhenhai · Volunteer Platform</span>
        <span class="orb" aria-hidden="true" />
      </div>
    </section>

    <section class="panel">
      <div class="form-wrap">
        <header class="form-head">
          <p class="eyebrow">{{ mode === 'device' ? t('login.deviceTitle') : t('login.formEyebrow') }}</p>
          <h2>{{ mode === 'device' ? t('login.deviceTitle') : t('login.formTitle') }}</h2>
        </header>

        <form v-if="mode === 'password'" class="form" @submit.prevent="submit">
          <ZField
            id="student-id"
            v-model="id"
            :label="t('login.idLabel')"
            :placeholder="t('login.idPlaceholder')"
            autocomplete="username"
            inputmode="text"
            icon="user"
          />
          <ZField
            id="password"
            v-model="password"
            :label="t('login.passwordLabel')"
            :placeholder="t('login.passwordPlaceholder')"
            type="password"
            autocomplete="current-password"
            allow-reveal
            icon="shield"
            :error="error"
            :hint="t('login.totpHint')"
            @enter="submit"
          />
          <ZButton
            type="submit"
            variant="primary"
            size="lg"
            block
            :loading="session.busy"
            :disabled="!canSubmit"
          >
            {{ session.busy ? t('login.submitting') : t('login.submit') }}
          </ZButton>
        </form>

        <form v-else class="form" @submit.prevent="submitTemp">
          <ZAlert tone="info" :title="t('login.deviceTitle')" :description="t('login.deviceDesc')" />
          <ZField
            id="challenge"
            v-model="challenge"
            :label="t('login.challengeLabel')"
            readonly
            disabled
          />
          <ZField
            id="temp-password"
            v-model="tempPassword"
            :label="t('login.tempPasswordLabel')"
            type="password"
            allow-reveal
            :error="error"
            autocomplete="one-time-code"
            @enter="submitTemp"
          />
          <ZButton
            type="submit"
            variant="primary"
            size="lg"
            block
            :loading="session.busy"
            :disabled="!tempPassword"
          >
            {{ t('login.tempSubmit') }}
          </ZButton>
          <ZButton variant="quiet" block @click="backToPassword">
            {{ t('login.backToPassword') }}
          </ZButton>
        </form>

        <footer class="form-foot">
          <span class="badge-line">
            <ZIcon name="shield" :size="15" />
            RSA · TOTP · Device Guard
          </span>
          <div class="switches">
            <button type="button" class="switch" @click="toggleLocale">
              {{ locale === 'zh-CN' ? 'EN' : '中' }}
            </button>
            <button type="button" class="switch" @click="ui.toggleTheme()">
              <ZIcon :name="ui.isDark ? 'sun' : 'moon'" :size="16" />
            </button>
          </div>
        </footer>
      </div>
    </section>
  </div>
</template>

<style scoped>
.auth {
  display: grid;
  grid-template-columns: minmax(0, 1.12fr) minmax(0, 0.88fr);
  min-height: 100dvh;
}

.story {
  position: relative;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  padding: clamp(2rem, 1.4rem + 3vw, 4.5rem);
  overflow: hidden;
  background:
    radial-gradient(48rem 36rem at 18% 8%, color-mix(in srgb, var(--accent) 26%, transparent), transparent 64%),
    radial-gradient(40rem 32rem at 92% 92%, color-mix(in srgb, var(--c-sage) 22%, transparent), transparent 62%),
    linear-gradient(160deg, color-mix(in srgb, var(--surface) 92%, var(--accent-soft)), var(--surface-sunken));
  border-right: 1px solid var(--line);
}

.story::after {
  content: '';
  position: absolute;
  inset: 0;
  pointer-events: none;
  background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='120' height='120'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.85' numOctaves='2'/%3E%3C/filter%3E%3Crect width='120' height='120' filter='url(%23n)' opacity='0.05'/%3E%3C/svg%3E");
}

.story-top {
  position: relative;
  z-index: 1;
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.brand {
  display: flex;
  align-items: center;
  gap: 0.7rem;
}

.mark {
  display: grid;
  place-items: center;
  width: 40px;
  height: 40px;
  border-radius: 13px;
  font-family: var(--font-display);
  font-size: 1.25rem;
  font-weight: 600;
  color: #fffdf8;
  background: linear-gradient(145deg, #c9973f, #8c5a12);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.4), var(--shadow-sm);
}

.brand-text {
  display: flex;
  flex-direction: column;
  line-height: 1.2;
}

.brand-text strong {
  font-family: var(--font-display);
  font-size: 1.1rem;
  letter-spacing: 0.02em;
}

.brand-text small {
  font-size: var(--text-2xs);
  letter-spacing: 0.12em;
  text-transform: uppercase;
  color: var(--ink-faint);
}

.canary {
  font-family: var(--font-mono);
  font-size: var(--text-2xs);
  letter-spacing: 0.24em;
  color: var(--ink-faint);
  padding: 0.3rem 0.55rem;
  border: 1px solid color-mix(in srgb, var(--ink) 12%, transparent);
  border-radius: var(--radius-pill);
}

.story-body {
  position: relative;
  z-index: 1;
  max-width: 34rem;
  padding-block: clamp(3rem, 8vh, 7rem);
  animation: rise var(--dur-slow) var(--ease-out) both;
}

.headline {
  margin-top: var(--space-4);
  font-size: var(--text-hero);
  font-weight: 500;
  line-height: 1.08;
  letter-spacing: -0.03em;
  white-space: pre-line;
  color: var(--ink);
}

.lede {
  margin-top: var(--space-5);
  max-width: 30ch;
  font-size: var(--text-md);
  line-height: 1.7;
  color: var(--ink-muted);
}

.story-foot {
  position: relative;
  z-index: 1;
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: var(--space-4);
}

.foot-note {
  font-size: var(--text-2xs);
  letter-spacing: 0.14em;
  text-transform: uppercase;
  color: var(--ink-muted);
  opacity: 0.85;
}

.orb {
  width: clamp(90px, 10vw, 150px);
  height: clamp(90px, 10vw, 150px);
  border-radius: 50%;
  background:
    radial-gradient(circle at 34% 30%, rgba(255, 255, 255, 0.85), transparent 46%),
    radial-gradient(circle at 62% 70%, color-mix(in srgb, var(--c-sage) 55%, transparent), transparent 60%),
    linear-gradient(150deg, #e8bd6e, #a96a1c 65%, #6f4210);
  box-shadow: 0 30px 70px -30px rgba(120, 80, 20, 0.6);
  opacity: 0.9;
  animation: float 9s var(--ease-in-out) infinite;
}

.panel {
  display: grid;
  place-items: center;
  padding: clamp(1.5rem, 1rem + 3vw, 4rem);
  background: var(--bg);
}

.form-wrap {
  width: 100%;
  max-width: 25rem;
  animation: rise var(--dur-slow) var(--ease-out) 80ms both;
}

.form-head {
  margin-bottom: var(--space-6);
}

.form-head h2 {
  margin-top: var(--space-2);
  font-size: var(--text-2xl);
  font-weight: 500;
}

.form {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.form-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  margin-top: var(--space-6);
  padding-top: var(--space-4);
  border-top: 1px solid color-mix(in srgb, var(--line) 70%, transparent);
}

.badge-line {
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
  font-family: var(--font-mono);
  font-size: var(--text-2xs);
  letter-spacing: 0.04em;
  color: var(--ink-faint);
}

.switches {
  display: flex;
  gap: 0.3rem;
}

.switch {
  display: grid;
  place-items: center;
  min-width: 30px;
  height: 30px;
  padding: 0 0.4rem;
  border-radius: var(--radius-pill);
  font-size: var(--text-xs);
  font-weight: 600;
  color: var(--ink-muted);
  transition: background-color var(--dur-fast) var(--ease-out), color var(--dur-fast) var(--ease-out);
}

.switch:hover {
  background: color-mix(in srgb, var(--ink) 6%, transparent);
  color: var(--ink);
}

@keyframes rise {
  from {
    opacity: 0;
    transform: translateY(16px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

@keyframes float {
  0%,
  100% {
    transform: translateY(0);
  }
  50% {
    transform: translateY(-10px);
  }
}

@media (max-width: 900px) {
  .auth {
    grid-template-columns: 1fr;
  }

  .story {
    min-height: auto;
    padding: var(--space-6) var(--space-6) var(--space-8);
    border-right: 0;
    border-bottom: 1px solid var(--line);
  }

  .story-body {
    padding-block: var(--space-8) 0;
  }

  .headline {
    font-size: clamp(1.9rem, 1.3rem + 4vw, 2.6rem);
  }

  .story-foot {
    display: none;
  }

  .panel {
    place-items: start center;
    padding: var(--space-8) var(--space-5) var(--space-12);
  }
}
</style>
