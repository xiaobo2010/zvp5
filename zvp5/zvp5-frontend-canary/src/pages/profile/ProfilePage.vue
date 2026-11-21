<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useSessionStore } from '@/shared/stores/session'
import { useUiStore, type LocaleCode, type ThemeMode } from '@/shared/stores/ui'
import { fetchGroup } from '@/shared/api/domain'
import { verifyPassword } from '@/shared/api/auth'
import {
  changeMyPassword,
  clearMyPrimaryDevice,
  fetchMyDevice,
  fetchMyTotp,
  setMyPrimaryDevice
} from '@/shared/api/profile'
import type { MyDeviceState, TotpCode } from '@/types/domain'
import { ApiError } from '@/shared/http/errors'
import { formatDateTime } from '@/shared/lib/format'
import ZAlert from '@/shared/ui/ZAlert.vue'
import ZAvatar from '@/shared/ui/ZAvatar.vue'
import ZBadge from '@/shared/ui/ZBadge.vue'
import ZButton from '@/shared/ui/ZButton.vue'
import ZCard from '@/shared/ui/ZCard.vue'
import ZField from '@/shared/ui/ZField.vue'
import ZIcon from '@/shared/ui/ZIcon.vue'
import ZModal from '@/shared/ui/ZModal.vue'
import ZProgress from '@/shared/ui/ZProgress.vue'
import ZSegmented from '@/shared/ui/ZSegmented.vue'
import ZSkeleton from '@/shared/ui/ZSkeleton.vue'

const { t, locale } = useI18n()
const router = useRouter()
const session = useSessionStore()
const ui = useUiStore()

const className = ref('')
const confirmOpen = ref(false)

const themeOptions = computed(() => [
  { value: 'system', label: t('common.system') },
  { value: 'light', label: t('common.light') },
  { value: 'dark', label: t('common.dark') }
])

const localeOptions = computed(() => [
  { value: 'zh-CN', label: '简体中文' },
  { value: 'en-US', label: 'English' }
])

const roles = computed(() => session.roles.filter((role) => role !== 'system'))

function setTheme(value: string) {
  ui.setTheme(value as ThemeMode)
}

function setLanguage(value: string) {
  const next = value as LocaleCode
  locale.value = next
  ui.setLocale(next)
}

async function signOut() {
  await session.signOut()
  confirmOpen.value = false
  router.push('/login')
}

function apiErrorText(cause: unknown): string {
  if (cause instanceof ApiError) {
    if (cause.message.includes('密码强度')) return t('profile.passwordWeak')
    return cause.message || t('common.error')
  }
  return t('common.error')
}

/* ------------------------------ 修改密码 ------------------------------ */

const pwdOpen = ref(false)
const pwdStep = ref<'form' | 'done'>('form')
const currentPassword = ref('')
const newPassword = ref('')
const confirmPassword = ref('')
const pwdBusy = ref(false)
const pwdError = ref('')

/** 与后端 `util.cert.pwd_strength` 同规则：8–14 位可打印 ASCII，至少 3 类字符。 */
function passwordStrength(value: string): number {
  if (value.length < 8 || value.length > 14) return 0
  for (const char of value) {
    const code = char.charCodeAt(0)
    if (code < 32 || code > 126) return 0
  }
  let score = 0
  if (/[a-z]/.test(value)) score += 1
  if (/[A-Z]/.test(value)) score += 1
  if (/[0-9]/.test(value)) score += 1
  if (/[^A-Za-z0-9]/.test(value)) score += 1
  return score
}

const pwdStrength = computed(() => passwordStrength(newPassword.value))
const pwdStrengthLabel = computed(() => {
  if (pwdStrength.value >= 4) return t('profile.strengthGood')
  if (pwdStrength.value >= 3) return t('profile.strengthOk')
  return t('profile.strengthLow')
})
const pwdStrengthClass = computed(() => {
  if (pwdStrength.value >= 4) return 'is-good'
  if (pwdStrength.value >= 3) return 'is-ok'
  return 'is-low'
})

function resetPasswordForm() {
  pwdStep.value = 'form'
  currentPassword.value = ''
  newPassword.value = ''
  confirmPassword.value = ''
  pwdError.value = ''
  pwdBusy.value = false
}

function openPassword() {
  resetPasswordForm()
  pwdOpen.value = true
}

async function submitPassword() {
  if (pwdBusy.value) return
  pwdError.value = ''
  if (!currentPassword.value) {
    pwdError.value = t('profile.passwordCurrentRequired')
    return
  }
  if (pwdStrength.value < 3) {
    pwdError.value = t('profile.passwordWeak')
    return
  }
  if (newPassword.value !== confirmPassword.value) {
    pwdError.value = t('profile.passwordMismatch')
    return
  }
  const profile = session.profile
  if (!profile) return

  pwdBusy.value = true
  let token = ''
  try {
    token = await verifyPassword(profile._id, currentPassword.value)
  } catch (cause) {
    pwdError.value =
      cause instanceof ApiError && cause.kind === 'network'
        ? cause.message
        : t('profile.passwordCurrentWrong')
    pwdBusy.value = false
    return
  }

  try {
    await changeMyPassword(profile._id, newPassword.value, token)
    currentPassword.value = ''
    newPassword.value = ''
    confirmPassword.value = ''
    pwdStep.value = 'done'
  } catch (cause) {
    pwdError.value = apiErrorText(cause)
  } finally {
    pwdBusy.value = false
  }
}

async function relogin() {
  pwdOpen.value = false
  await session.signOut()
  await router.push('/login')
}

watch(pwdOpen, (open) => {
  if (!open) resetPasswordForm()
})

/* ------------------------------ 动态码 ------------------------------ */

const totpOpen = ref(false)
const totpLoading = ref(false)
const totpError = ref('')
const totp = ref<TotpCode | null>(null)
const totpRemaining = ref(0)
const totpPeriod = ref(30)
const totpCopied = ref(false)
let totpTimer: number | null = null
let totpExpiry = 0
let totpCopyTimer: number | null = null

const formattedTotp = computed(() => {
  const value = totp.value?.otp ?? ''
  return value.length === 6 ? `${value.slice(0, 3)} ${value.slice(3)}` : value
})
const totpPercent = computed(() =>
  totpPeriod.value > 0 ? Math.max(0, Math.min(100, (totpRemaining.value / totpPeriod.value) * 100)) : 0
)

function stopTotpTimer() {
  if (totpTimer !== null) {
    window.clearInterval(totpTimer)
    totpTimer = null
  }
}

function updateTotpRemaining() {
  totpRemaining.value = Math.max(0, Math.ceil(totpExpiry - Date.now() / 1000))
  if (totpRemaining.value <= 0) stopTotpTimer()
}

async function loadTotp() {
  const profile = session.profile
  if (!profile || totpLoading.value) return
  totpLoading.value = true
  totpError.value = ''
  totpCopied.value = false
  try {
    const result = await fetchMyTotp(profile._id)
    totp.value = result
    totpExpiry = result.exp
    totpPeriod.value = Math.max(1, Math.round(result.exp - Date.now() / 1000))
    updateTotpRemaining()
    stopTotpTimer()
    totpTimer = window.setInterval(updateTotpRemaining, 1000)
  } catch (cause) {
    totp.value = null
    totpError.value = apiErrorText(cause)
  } finally {
    totpLoading.value = false
  }
}

async function copyTotp() {
  if (!totp.value || totpRemaining.value <= 0) return
  try {
    await navigator.clipboard.writeText(totp.value.otp)
    totpCopied.value = true
    if (totpCopyTimer !== null) window.clearTimeout(totpCopyTimer)
    totpCopyTimer = window.setTimeout(() => {
      totpCopied.value = false
      totpCopyTimer = null
    }, 1600)
  } catch {
    ui.notify(t('common.error'), 'danger')
  }
}

function openTotp() {
  totpOpen.value = true
  void loadTotp()
}

watch(totpOpen, (open) => {
  if (!open) {
    stopTotpTimer()
    totp.value = null
    totpError.value = ''
    totpRemaining.value = 0
    totpCopied.value = false
  }
})

/* ------------------------------ 主设备 ------------------------------ */

const device = ref<MyDeviceState | null>(null)
const deviceLoading = ref(false)
const deviceError = ref('')
const deviceOpen = ref(false)
const deviceActionBusy = ref(false)
const devicePassword = ref('')
const deviceNewId = ref('')
const deviceActionError = ref('')

const deviceSummary = computed(() => {
  if (deviceLoading.value) return t('profile.deviceLoading')
  if (deviceError.value) return t('profile.deviceLoadFailed')
  if (device.value?.primary_xuehai_id) {
    return t('profile.deviceBound', { id: device.value.primary_xuehai_id })
  }
  return t('profile.deviceUnbound')
})

const isPrimaryRequest = computed(
  () =>
    Boolean(device.value?.xuehai_id) &&
    Boolean(device.value?.primary_xuehai_id) &&
    device.value?.xuehai_id === device.value?.primary_xuehai_id
)

const deviceBanText = computed(() => {
  if (!device.value?.device_banned) return ''
  const until = device.value.device_ban_until
  const reason = device.value.device_ban_reason || t('profile.deviceBanReasonFallback')
  return until
    ? `${reason} · ${t('profile.deviceBanUntil', { time: formatDateTime(until) })}`
    : reason
})

async function loadDevice() {
  if (deviceLoading.value) return
  deviceLoading.value = true
  deviceError.value = ''
  try {
    device.value = await fetchMyDevice()
  } catch (cause) {
    device.value = null
    deviceError.value = apiErrorText(cause)
  } finally {
    deviceLoading.value = false
  }
}

function resetDeviceForm() {
  devicePassword.value = ''
  deviceNewId.value = ''
  deviceActionError.value = ''
}

function openDevice() {
  resetDeviceForm()
  deviceOpen.value = true
  void loadDevice()
}

async function bindCurrentDevice() {
  const current = device.value
  if (!current?.xuehai_id || deviceActionBusy.value) return
  deviceActionBusy.value = true
  deviceActionError.value = ''
  try {
    await setMyPrimaryDevice(current.xuehai_id)
    await loadDevice()
    ui.notify(t('profile.deviceBindDone'), 'success')
  } catch (cause) {
    deviceActionError.value = apiErrorText(cause)
  } finally {
    deviceActionBusy.value = false
  }
}

async function verifyDevicePassword(): Promise<string | null> {
  const profile = session.profile
  if (!profile) return null
  if (!devicePassword.value) {
    deviceActionError.value = t('profile.deviceNeedPassword')
    return null
  }
  try {
    return await verifyPassword(profile._id, devicePassword.value)
  } catch (cause) {
    deviceActionError.value =
      cause instanceof ApiError && cause.kind === 'network'
        ? cause.message
        : t('profile.passwordCurrentWrong')
    return null
  }
}

async function rebindDevice() {
  if (deviceActionBusy.value) return
  const next = deviceNewId.value.trim()
  if (!/^\d{1,32}$/.test(next)) {
    deviceActionError.value = t('profile.deviceIdInvalid')
    return
  }
  deviceActionBusy.value = true
  deviceActionError.value = ''
  const token = await verifyDevicePassword()
  if (!token) {
    deviceActionBusy.value = false
    return
  }
  try {
    await setMyPrimaryDevice(next, token)
    await loadDevice()
    resetDeviceForm()
    ui.notify(t('profile.deviceBindDone'), 'success')
  } catch (cause) {
    deviceActionError.value = apiErrorText(cause)
  } finally {
    deviceActionBusy.value = false
  }
}

async function unbindDevice() {
  if (deviceActionBusy.value) return
  deviceActionBusy.value = true
  deviceActionError.value = ''
  const token = await verifyDevicePassword()
  if (!token) {
    deviceActionBusy.value = false
    return
  }
  try {
    await clearMyPrimaryDevice(token)
    await loadDevice()
    resetDeviceForm()
    ui.notify(t('profile.deviceUnbindDone'), 'success')
  } catch (cause) {
    deviceActionError.value = apiErrorText(cause)
  } finally {
    deviceActionBusy.value = false
  }
}

watch(deviceOpen, (open) => {
  if (!open) resetDeviceForm()
})

onBeforeUnmount(() => {
  stopTotpTimer()
  if (totpCopyTimer !== null) window.clearTimeout(totpCopyTimer)
})

onMounted(async () => {
  void loadDevice()
  const classId = session.profile?.class_id
  if (!classId) return
  try {
    const group = await fetchGroup(classId)
    className.value = group.name
  } catch {
    className.value = ''
  }
})
</script>

<template>
  <div class="page">
    <section class="identity">
      <ZAvatar :name="session.displayName" :size="76" />
      <div class="identity-body">
        <h1 class="name">{{ session.displayName || '—' }}</h1>
        <p class="meta">
          <span class="num">{{ session.profile?.id || '—' }}</span>
          <template v-if="className">
            <span class="sep">·</span>
            <span>{{ className }}</span>
          </template>
        </p>
        <div class="roles">
          <ZBadge v-for="role in roles" :key="role" tone="accent">{{ t(`role.${role}`) }}</ZBadge>
        </div>
      </div>
      <div class="identity-action">
        <ZButton variant="ghost" size="sm" icon="logout" @click="confirmOpen = true">
          {{ t('common.logout') }}
        </ZButton>
      </div>
    </section>

    <div class="grid">
      <ZCard class="setting">
        <div class="setting-head">
          <h2 class="setting-title">{{ t('profile.appearance') }}</h2>
          <ZIcon :name="ui.isDark ? 'moon' : 'sun'" :size="18" />
        </div>
        <p class="setting-desc">
          {{ locale === 'zh-CN' ? '浅色与深色分别调校，不只是颜色反转。' : 'Light and dark are tuned separately — not a color inversion.' }}
        </p>
        <ZSegmented :model-value="ui.themeMode" :options="themeOptions" @update:model-value="setTheme" />
      </ZCard>

      <ZCard class="setting">
        <div class="setting-head">
          <h2 class="setting-title">{{ t('profile.language') }}</h2>
          <ZIcon name="book" :size="18" />
        </div>
        <p class="setting-desc">
          {{ locale === 'zh-CN' ? '界面文案即时切换，无需刷新。' : 'Interface copy switches instantly, no reload.' }}
        </p>
        <ZSegmented :model-value="locale" :options="localeOptions" @update:model-value="setLanguage" />
      </ZCard>

      <ZCard class="setting wide">
        <div class="setting-head">
          <h2 class="setting-title">{{ t('profile.security') }}</h2>
          <ZIcon name="shield" :size="18" />
        </div>
        <ul class="security">
          <li class="security-row">
            <div class="security-copy">
              <strong>{{ t('profile.password') }}</strong>
              <small>{{ t('profile.passwordDesc') }}</small>
            </div>
            <ZButton size="sm" variant="ghost" @click="openPassword">
              {{ t('profile.passwordAction') }}
            </ZButton>
          </li>
          <li class="security-row">
            <div class="security-copy">
              <strong>{{ t('profile.totp') }}</strong>
              <small>{{ t('profile.totpDesc') }}</small>
            </div>
            <ZButton size="sm" variant="ghost" @click="openTotp">
              {{ t('profile.totpAction') }}
            </ZButton>
          </li>
          <li class="security-row">
            <div class="security-copy">
              <strong>{{ t('profile.device') }}</strong>
              <small>{{ t('profile.deviceDesc') }}</small>
            </div>
            <div class="security-actions">
              <ZBadge :tone="device?.primary_xuehai_id ? 'success' : 'neutral'">
                {{ deviceSummary }}
              </ZBadge>
              <ZButton size="sm" variant="ghost" @click="openDevice">
                {{ t('profile.deviceAction') }}
              </ZButton>
            </div>
          </li>
        </ul>
        <p class="note">{{ t('profile.securityNote') }}</p>
      </ZCard>

      <ZCard class="setting wide">
        <div class="setting-head">
          <h2 class="setting-title">{{ locale === 'zh-CN' ? '关于' : 'About' }}</h2>
          <ZIcon name="sparkles" :size="18" />
        </div>
        <dl class="about">
          <div>
            <dt>{{ t('profile.version') }}</dt>
            <dd>Canary 0.1.0</dd>
          </div>
          <div>
            <dt>{{ locale === 'zh-CN' ? '构建' : 'Build' }}</dt>
            <dd>Vue 3 · Vite</dd>
          </div>
          <div>
            <dt>{{ locale === 'zh-CN' ? '设计语言' : 'Design' }}</dt>
            <dd>Warm · Editorial · Glass</dd>
          </div>
        </dl>
      </ZCard>
    </div>

    <ZModal v-model="confirmOpen" :title="t('common.logout')" size="sm">
      <p class="confirm-text">
        {{ locale === 'zh-CN' ? '退出后需要重新登录才能继续使用。' : 'You will need to sign in again to continue.' }}
      </p>
      <template #footer>
        <ZButton variant="ghost" @click="confirmOpen = false">{{ t('common.cancel') }}</ZButton>
        <ZButton variant="primary" @click="signOut">{{ t('common.logout') }}</ZButton>
      </template>
    </ZModal>

    <ZModal
      v-model="pwdOpen"
      :title="pwdStep === 'done' ? t('profile.passwordChanged') : t('profile.passwordTitle')"
      :description="pwdStep === 'form' ? t('profile.passwordDescLong') : undefined"
      size="sm"
    >
      <div v-if="pwdStep === 'form'" class="form">
        <ZField
          v-model="currentPassword"
          type="password"
          allow-reveal
          autocomplete="current-password"
          :label="t('profile.currentPassword')"
          :disabled="pwdBusy"
        />
        <ZField
          v-model="newPassword"
          type="password"
          allow-reveal
          autocomplete="new-password"
          :label="t('profile.newPassword')"
          :disabled="pwdBusy"
        />
        <div v-if="newPassword" class="strength" :class="pwdStrengthClass">
          <span class="strength-bars" aria-hidden="true">
            <i v-for="index in 4" :key="index" :class="{ on: pwdStrength >= index }" />
          </span>
          <span class="strength-label">
            {{ t('profile.passwordStrength') }} · {{ pwdStrengthLabel }}
          </span>
        </div>
        <ZField
          v-model="confirmPassword"
          type="password"
          allow-reveal
          autocomplete="new-password"
          :label="t('profile.confirmPassword')"
          :disabled="pwdBusy"
          @enter="submitPassword"
        />
        <p class="form-hint">{{ t('profile.passwordHint') }}</p>
        <ZAlert v-if="pwdError" tone="danger" :description="pwdError" />
      </div>
      <p v-else class="form-hint">{{ t('profile.passwordChangedDesc') }}</p>
      <template #footer>
        <template v-if="pwdStep === 'form'">
          <ZButton variant="ghost" size="sm" :disabled="pwdBusy" @click="pwdOpen = false">
            {{ t('common.cancel') }}
          </ZButton>
          <ZButton variant="primary" size="sm" :loading="pwdBusy" @click="submitPassword">
            {{ t('common.save') }}
          </ZButton>
        </template>
        <ZButton v-else variant="primary" size="sm" @click="relogin">
          {{ t('profile.passwordRelogin') }}
        </ZButton>
      </template>
    </ZModal>

    <ZModal v-model="totpOpen" :title="t('profile.totpTitle')" size="sm">
      <div class="totp-body">
        <div v-if="totpLoading" class="totp-loading">
          <ZSkeleton width="58%" height="2.4rem" />
          <ZSkeleton width="36%" height="0.8rem" />
        </div>
        <ZAlert v-else-if="totpError" tone="danger" :description="totpError">
          <div class="retry">
            <ZButton size="sm" icon="refresh" @click="loadTotp">
              {{ t('profile.totpRefresh') }}
            </ZButton>
          </div>
        </ZAlert>
        <template v-else-if="totp">
          <div class="totp-display">
            <span class="totp-code" :class="{ 'is-expired': totpRemaining <= 0 }">
              {{ formattedTotp }}
            </span>
            <ZButton
              size="sm"
              variant="ghost"
              icon="copy"
              :disabled="totpRemaining <= 0"
              @click="copyTotp"
            >
              {{ totpCopied ? t('profile.totpCopied') : t('profile.copyCode') }}
            </ZButton>
          </div>
          <ZProgress :value="totpPercent" :height="4" />
          <p class="totp-meta">
            {{
              totpRemaining > 0
                ? t('profile.totpRemaining', { n: totpRemaining })
                : t('profile.totpExpired')
            }}
          </p>
          <ZAlert tone="info" :description="t('profile.totpHint')" />
        </template>
      </div>
      <template #footer>
        <ZButton variant="ghost" size="sm" @click="totpOpen = false">
          {{ t('common.close') }}
        </ZButton>
        <ZButton
          variant="secondary"
          size="sm"
          icon="refresh"
          :loading="totpLoading"
          @click="loadTotp"
        >
          {{ t('profile.totpRefresh') }}
        </ZButton>
      </template>
    </ZModal>

    <ZModal
      v-model="deviceOpen"
      :title="t('profile.deviceTitle')"
      :description="t('profile.deviceDesc')"
      size="md"
    >
      <div v-if="deviceLoading" class="device-loading">
        <ZSkeleton width="100%" height="3.6rem" />
        <ZSkeleton width="100%" height="3.6rem" />
      </div>
      <ZAlert v-else-if="deviceError" tone="danger" :description="deviceError">
        <div class="retry">
          <ZButton size="sm" icon="refresh" @click="loadDevice">
            {{ t('profile.deviceRefresh') }}
          </ZButton>
        </div>
      </ZAlert>
      <div v-else-if="device" class="device-body">
        <div class="device-grid">
          <div class="device-item">
            <span class="device-label">{{ t('profile.deviceCurrent') }}</span>
            <strong v-if="device.xuehai_id" class="num">{{ device.xuehai_id }}</strong>
            <strong v-else class="device-missing">{{ t('profile.deviceHeaderMissing') }}</strong>
          </div>
          <div class="device-item">
            <span class="device-label">{{ t('profile.devicePrimary') }}</span>
            <strong v-if="device.primary_xuehai_id" class="num">
              {{ device.primary_xuehai_id }}
            </strong>
            <strong v-else class="device-missing">{{ t('profile.deviceUnboundText') }}</strong>
          </div>
        </div>

        <ZAlert
          v-if="device.device_banned"
          tone="danger"
          :title="t('profile.deviceBanned')"
          :description="deviceBanText"
        />
        <ZAlert
          v-if="!device.xuehai_id"
          tone="info"
          :description="t('profile.deviceNoHeaderHint')"
        />

        <template v-else-if="!device.primary_xuehai_id">
          <div class="form-actions">
            <ZButton
              variant="primary"
              size="sm"
              :loading="deviceActionBusy"
              @click="bindCurrentDevice"
            >
              {{ t('profile.deviceBindCurrent') }}
            </ZButton>
          </div>
        </template>

        <template v-else-if="isPrimaryRequest">
          <div class="form">
            <ZField
              v-model="deviceNewId"
              :label="t('profile.deviceNewId')"
              :hint="t('profile.deviceIdHint')"
              inputmode="numeric"
              :disabled="deviceActionBusy"
            />
            <ZField
              v-model="devicePassword"
              type="password"
              allow-reveal
              autocomplete="current-password"
              :label="t('profile.devicePassword')"
              :hint="t('profile.devicePasswordHint')"
              :disabled="deviceActionBusy"
            />
            <ZAlert v-if="deviceActionError" tone="danger" :description="deviceActionError" />
            <div class="form-actions">
              <ZButton
                variant="secondary"
                size="sm"
                :loading="deviceActionBusy"
                @click="rebindDevice"
              >
                {{ t('profile.deviceRebind') }}
              </ZButton>
              <ZButton
                variant="danger"
                size="sm"
                :loading="deviceActionBusy"
                @click="unbindDevice"
              >
                {{ t('profile.deviceUnbind') }}
              </ZButton>
            </div>
          </div>
        </template>

        <ZAlert v-else tone="warning" :description="t('profile.deviceOnlyPrimary')" />
      </div>
      <template #footer>
        <ZButton variant="ghost" size="sm" @click="deviceOpen = false">
          {{ t('common.close') }}
        </ZButton>
        <ZButton
          variant="secondary"
          size="sm"
          icon="refresh"
          :loading="deviceLoading"
          @click="loadDevice"
        >
          {{ t('profile.deviceRefresh') }}
        </ZButton>
      </template>
    </ZModal>
  </div>
</template>

<style scoped>
.page {
  display: flex;
  flex-direction: column;
  gap: var(--space-8);
  max-width: 960px;
}

.identity {
  display: flex;
  align-items: center;
  gap: var(--space-5);
  padding: clamp(1.4rem, 1rem + 2vw, 2.2rem);
  border-radius: var(--radius-xl);
  border: 1px solid var(--line);
  background:
    radial-gradient(32rem 20rem at 90% 0%, color-mix(in srgb, var(--accent) 14%, transparent), transparent 62%),
    var(--surface);
  box-shadow: var(--shadow-sm);
}

.identity-body {
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
  flex: 1;
  min-width: 0;
}

.name {
  font-size: var(--text-2xl);
  font-weight: 500;
}

.meta {
  display: flex;
  align-items: center;
  gap: 0.45rem;
  font-size: var(--text-sm);
  color: var(--ink-muted);
  white-space: nowrap;
}

.identity-action {
  flex: none;
}

.sep {
  opacity: 0.6;
}

.roles {
  display: flex;
  flex-wrap: wrap;
  gap: 0.35rem;
  margin-top: 0.2rem;
}

.grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--space-4);
}

.setting {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.setting.wide {
  grid-column: 1 / -1;
}

.setting-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  color: var(--ink-muted);
}

.setting-title {
  font-family: var(--font-sans);
  font-size: var(--text-base);
  font-weight: 600;
  color: var(--ink);
}

.setting-desc {
  font-size: var(--text-sm);
  color: var(--ink-muted);
  line-height: 1.6;
}

.security {
  list-style: none;
  margin: 0;
  padding: 0;
  border-top: 1px solid color-mix(in srgb, var(--line) 55%, transparent);
}

.security-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-4);
  padding: var(--space-4) 0;
  border-bottom: 1px solid color-mix(in srgb, var(--line) 55%, transparent);
}

.security-copy {
  display: flex;
  flex-direction: column;
  gap: 0.2rem;
  min-width: 0;
}

.security-row strong {
  font-size: var(--text-sm);
  font-weight: 550;
}

.security-row small {
  font-size: var(--text-xs);
  color: var(--ink-faint);
}

.security-actions {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-wrap: wrap;
  justify-content: flex-end;
}

.note {
  font-size: var(--text-xs);
  color: var(--ink-faint);
  line-height: 1.6;
}

.form {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.form-hint {
  font-size: var(--text-xs);
  line-height: 1.65;
  color: var(--ink-faint);
}

.form-actions {
  display: flex;
  justify-content: flex-end;
  gap: var(--space-2);
  flex-wrap: wrap;
}

.strength {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin-top: calc(var(--space-2) * -1);
}

.strength-bars {
  display: inline-flex;
  gap: 3px;
}

.strength-bars i {
  width: 26px;
  height: 3px;
  border-radius: var(--radius-pill);
  background: var(--line);
}

.strength.is-low .strength-bars i.on {
  background: var(--c-danger);
}

.strength.is-ok .strength-bars i.on {
  background: var(--c-warning);
}

.strength.is-good .strength-bars i.on {
  background: var(--c-success);
}

.strength-label {
  font-size: var(--text-2xs);
  color: var(--ink-faint);
}

.totp-body {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.totp-loading,
.device-loading {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.totp-display {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-4);
  padding: var(--space-5);
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  background: var(--surface-sunken);
}

.totp-code {
  font-family: var(--font-mono);
  font-size: clamp(1.8rem, 1.4rem + 2vw, 2.5rem);
  letter-spacing: 0.16em;
  color: var(--ink);
  transition: opacity var(--dur-base) var(--ease-out);
}

.totp-code.is-expired {
  opacity: 0.35;
}

.totp-meta {
  font-size: var(--text-xs);
  color: var(--ink-faint);
  text-align: right;
}

.retry {
  margin-top: var(--space-2);
}

.device-body {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.device-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--space-3);
}

.device-item {
  display: flex;
  flex-direction: column;
  gap: 0.3rem;
  min-width: 0;
  padding: var(--space-3) var(--space-4);
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  background: var(--surface-sunken);
}

.device-label {
  font-size: var(--text-2xs);
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--ink-faint);
}

.device-item strong {
  font-size: var(--text-sm);
  font-weight: 550;
  color: var(--ink-soft);
  overflow-wrap: anywhere;
}

.device-item strong.device-missing {
  font-weight: 400;
  font-size: var(--text-xs);
  color: var(--ink-faint);
}

.about {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: var(--space-5);
  margin: 0;
}

.about div {
  display: flex;
  flex-direction: column;
  gap: 0.3rem;
  padding-right: var(--space-4);
  border-right: 1px solid color-mix(in srgb, var(--line) 45%, transparent);
}

.about div:last-child {
  border-right: 0;
  padding-right: 0;
}

.about dt {
  font-size: var(--text-2xs);
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--ink-faint);
}

.about dd {
  margin: 0;
  font-size: var(--text-sm);
  font-weight: 550;
  color: var(--ink-soft);
}

.confirm-text {
  font-size: var(--text-sm);
  color: var(--ink-muted);
}

@media (max-width: 760px) {
  .grid {
    grid-template-columns: 1fr;
  }

  .security-row {
    flex-wrap: wrap;
  }

  .security-actions {
    width: 100%;
    justify-content: flex-start;
  }

  .device-grid {
    grid-template-columns: 1fr;
  }

  .identity {
    flex-wrap: wrap;
  }

  .identity-body {
    flex-basis: calc(100% - 76px - var(--space-5));
  }

  .about {
    grid-template-columns: 1fr;
    gap: var(--space-3);
  }

  .about div {
    border-right: 0;
    padding-right: 0;
    padding-bottom: var(--space-2);
    border-bottom: 1px solid color-mix(in srgb, var(--line) 45%, transparent);
  }

  .about div:last-child {
    border-bottom: 0;
    padding-bottom: 0;
  }
}

@media (max-width: 520px) {
  .totp-display {
    align-items: flex-start;
    flex-direction: column;
  }

  .totp-meta {
    text-align: left;
  }
}
</style>
