<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch, h } from 'vue'
import { ElMessageBox, ElMessage } from 'element-plus'
import { useUserStore } from '@/stores/user'
import { useHeaderStore } from '@/stores/header'
import { useRouter, useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { modifyPasswordDialogs } from '@/views/user/modifyPasswordDialogs'
import api from '@/api'
import { getMaintenanceStatus } from '@/api/system/maintenance.ts'
import { resetMaintenanceGuardCache } from '@/router/maintenanceGuard'
import { formatBanUntil, readBanInfo } from '@/utils/ban'
import ZBanScreen from '@/components/system/ZBanScreen.vue'
import ZAuthAlert from '@/components/system/ZAuthAlert.vue'
import ZLoginBackdrop from '@/components/system/ZLoginBackdrop.vue'
import ZLoginReveal from '@/components/system/ZLoginReveal.vue'
import dayjs from 'dayjs'
import { staticAssetUrl } from '@/utils/staticAssets'
import type { User, UserProfile } from '@/../types'

const header = useHeaderStore()

const { t } = useI18n()

const user = ref('')
const password = ref<string>('')
const userStore = useUserStore()
const router = useRouter()
const route = useRoute()
const loading = ref(false)

header.setHeader(t('nav.login.actions.login'))

// 站内回跳目标,仅接受以 / 开头的 query,避免外链注入
function redirectTarget(): string {
  const target = route.query.redirect
  return typeof target === 'string' && target.startsWith('/') ? target : ''
}

// 登录后按角色分流:维护中只有 admin 能继续(普通用户回维护暂停页);redirect
// 参数仅在站内路径生效,缺省 /user。
// admin 判定优先用维护状态接口的 data.admin:它由服务端用 token 复核,而 position
// 由小组权限推导,刚登录/缓存未就绪时可能为空 —— 只看 position 会把真管理员弹回
// 维护页,导致「登录后进不去 /server」。
async function postLoginTarget(): Promise<string> {
  const target = redirectTarget() || '/user'
  const positionalAdmin = userStore.position.includes('admin')
  try {
    const res = await getMaintenanceStatus()
    const data = res.data.data
    if (data.enabled) {
      if (data.admin === true || positionalAdmin) {
        // 守卫缓存的是「登录前」的判定(无 token ⇒ admin=false)。登录后必须失效,
        // 否则真管理员会被 30s 内的旧缓存重新挡回维护页,落不到 /server。
        resetMaintenanceGuardCache()
        return target
      }
      return '/maintenance'
    }
  } catch {
    // 状态接口不可达时按未维护处理
  }
  return target
}

// 维护模式 503(v4.2.4-2 R4):维护期间后端只给 admin 下发 token,非 admin 的老
// 登录请求会被拒。该响应只做提示 —— 绝不清 token、绝不跳登录页,清 token 只属
// 于 axios 的 401 分支(plugins/axios.ts:168-186)。
function isMaintenanceBlocked(error: unknown): boolean {
  const response = (error as { response?: { status?: number; data?: { maintenance?: boolean } } })
    ?.response
  return response?.status === 503 && response?.data?.maintenance === true
}

function notifyMaintenanceBlocked() {
  ElMessage({
    message: t('maintenance.loginBlocked'),
    type: 'warning',
    grouping: true,
    plain: true
  })
}

// 封禁信号:后端登录被拒时返回 403 + detail USER_BANNED,原因/期限在 X-Ban-* 头里。
// 只提示、不清 token、不跳转 —— 与维护模式 503 各自独立,避免两套分支互相污染。
function isUserBanned(error: unknown): boolean {
  const response = (error as { response?: { status?: number; data?: { detail?: unknown } } })
    ?.response
  if (response?.status !== 403) {
    return false
  }
  const detail = response.data?.detail
  if (detail === 'USER_BANNED') {
    return true
  }
  // 设备闸/结构化错误用对象承载 code,账号封禁也可能以该形式下发。
  return (
    !!detail && typeof detail === 'object' && (detail as { code?: unknown }).code === 'USER_BANNED'
  )
}

// 全屏封禁屏:登录页没有 token,不写 store、不自动登出,只显示原因/期限；
// 15 秒后由 ZBanScreen 播放粒子消散并 emit ack，关闭后仍停留在登录页。
type BanScreenState = {
  visible: boolean
  title: string
  reason: string
  until: string
  appeal: string
  variant: 'account' | 'device'
}

// 弹层关掉后焦点要落回密码框：分支模板用原生 input，这里直接持有元素引用。
const passwordInput = ref<HTMLInputElement | null>(null)

const banScreen = reactive<BanScreenState>({
  visible: false,
  title: '',
  reason: '',
  until: '',
  appeal: '',
  variant: 'account'
})

function showBanScreen(payload: Omit<BanScreenState, 'visible'>) {
  Object.assign(banScreen, payload, { visible: true })
}

function closeBanScreen() {
  banScreen.visible = false
}

// 封禁屏预热：封禁信息只能从登录 403 的响应头拿到，首帧要在低端平板上一次性
// 完成样式解析/字体度量/合成层创建，冷启动会掉帧。空闲时先挂一个不可见实例
// （ZBanScreen 的 prewarm 模式）把这一笔成本提前付掉，真正封禁时直接显示。
const banWarm = ref(false)
let warmTimer: ReturnType<typeof setTimeout> | null = null

// 登录揭示动画预热：登录成功后的 2.8s 幕布在低端平板上第一次挂载时，
// 蜂窝底纹/字体阴影/合成层的样式解析会掉帧。登录页空闲时先挂一个 prewarm
// 实例，把这一笔成本提前付掉；真正揭示时直接复用同一套样式与合成路径。
const revealWarm = ref(false)
let revealWarmTimer: ReturnType<typeof setTimeout> | null = null
let warmAlive = true

function warmBanScreen() {
  if (!warmAlive || banScreen.visible) {
    return
  }
  // 静态化用户（reduced-motion）没有入场动画，预热无收益。
  if (window.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches === true) {
    return
  }
  banWarm.value = true
  warmTimer = setTimeout(() => {
    warmTimer = null
    banWarm.value = false
  }, 1600)
}

function warmLoginReveal() {
  if (!warmAlive || revealWarm.value) {
    return
  }
  if (window.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches === true) {
    return
  }
  revealWarm.value = true
  revealWarmTimer = setTimeout(() => {
    revealWarmTimer = null
    revealWarm.value = false
  }, 1600)
}

onMounted(() => {
  if (typeof window.requestIdleCallback === 'function') {
    window.requestIdleCallback(
      () => {
        warmBanScreen()
        warmLoginReveal()
      },
      { timeout: 2500 }
    )
  } else {
    setTimeout(() => {
      warmBanScreen()
      warmLoginReveal()
    }, 900)
  }
})

onBeforeUnmount(() => {
  warmAlive = false
  if (warmTimer !== null) {
    clearTimeout(warmTimer)
    warmTimer = null
  }
  if (revealWarmTimer !== null) {
    clearTimeout(revealWarmTimer)
    revealWarmTimer = null
  }
})

// 登录失败对话框：密码错误走红色（danger），设备闸拦截走琥珀色（warning）。
// 与封禁屏分开——封禁是终态不可重试，这几种都还能改输入再来一次。
type AuthAlertState = {
  visible: boolean
  variant: 'danger' | 'warning'
  title: string
  message: string
  hint: string
  ackText: string
  actionText: string
}

const authAlert = reactive<AuthAlertState>({
  visible: false,
  variant: 'danger',
  title: '',
  message: '',
  hint: '',
  ackText: '',
  actionText: ''
})

type AuthAlertPayload = Pick<AuthAlertState, 'title' | 'message'> &
  Partial<Pick<AuthAlertState, 'variant' | 'hint' | 'ackText' | 'actionText'>>

// 按钮回调放在响应式状态之外：它们不需要参与 diff，而且弹层关闭后还要再消费一次。
let alertAckHandler: (() => void) | null = null
let alertActionHandler: (() => void) | null = null

function showAuthAlert(
  payload: AuthAlertPayload,
  handlers: { ack?: () => void; action?: () => void } = {}
) {
  Object.assign(
    authAlert,
    { variant: 'danger', hint: '', ackText: '', actionText: '' },
    payload,
    { visible: true }
  )
  alertAckHandler = handlers.ack ?? null
  alertActionHandler = handlers.action ?? null
}

function closeAuthAlert() {
  authAlert.visible = false
}

function onAuthAlertAck() {
  const handler = alertAckHandler
  alertAckHandler = null
  alertActionHandler = null
  closeAuthAlert()
  handler?.()
}

function onAuthAlertAction() {
  const handler = alertActionHandler
  alertAckHandler = null
  alertActionHandler = null
  closeAuthAlert()
  handler?.()
}

// 弹层关掉后焦点必须落回密码框，否则键盘/读屏用户会停在 body 上。
function focusPassword() {
  void nextTick(() => {
    passwordInput.value?.focus()
  })
}

function notifyUserBanned(error: unknown) {
  const { reason, until } = readBanInfo(
    (error as { response?: { headers?: unknown } })?.response?.headers
  )
  // 期限行:until=0 是永久;否则交给调用方按 locale 决定日期格式(util 不碰 i18n)。
  const untilLine =
    formatBanUntil(until, (ms) =>
      t('nav.banned.until', { time: dayjs(ms).format('YYYY-MM-DD HH:mm') })
    ) || t('nav.banned.permanent')
  showBanScreen({
    title: t('nav.banned.title'),
    reason: reason || t('nav.banned.reasonFallback'),
    until: untilLine,
    appeal: t('nav.banned.appeal'),
    variant: 'account'
  })
}

// 设备闸的结构化 403:detail 是对象 {code,message,challenge?}。只认 403,字符串
// detail(USER_BANNED / 密码错误)仍走各自的旧分支。
interface DeviceLoginDetail {
  code?: string
  message?: string
  challenge?: string
}

function deviceDetailOf(error: unknown): DeviceLoginDetail | null {
  const response = (error as { response?: { status?: number; data?: { detail?: unknown } } })
    ?.response
  if (response?.status !== 403) {
    return null
  }
  const detail = response.data?.detail
  if (!detail || typeof detail !== 'object') {
    return null
  }
  return detail as DeviceLoginDetail
}

// 本机不是主设备:拿 403 里的挑战令牌切到临时密码模式。挑战 5 分钟过期,
// 所以进入页面后要重新输账号密码换新挑战——「返回」就是干这个的。
const mode = ref<'normal' | 'temp'>('normal')
const challenge = ref('')
const tempPassword = ref('')
const tempSubmitting = ref(false)
const tempError = ref('')

function enterTempMode(detail: DeviceLoginDetail) {
  challenge.value = detail.challenge ?? ''
  tempPassword.value = ''
  tempError.value = ''
  mode.value = 'temp'
}

function backToNormal() {
  mode.value = 'normal'
  challenge.value = ''
  tempPassword.value = ''
  tempError.value = ''
}

// 纯数字 8 位:输入法/粘贴可能带空格或全角,直接滤掉不打断输入。
watch(tempPassword, (value) => {
  const digits = value.replace(/\D/g, '').slice(0, 8)
  if (digits !== value) {
    tempPassword.value = digits
  }
})

// 设备封禁:原因/期限同样在 X-Ban-* 头里(后端复用 util.ban 的头口径),
// 沿用账号封禁的全屏封禁屏,只把标题/兜底原因/确认文案换成设备口径。
function notifyDeviceBanned(error: unknown) {
  const { reason, until } = readBanInfo(
    (error as { response?: { headers?: unknown } })?.response?.headers
  )
  const untilLine =
    formatBanUntil(until, (ms) =>
      t('nav.banned.until', { time: dayjs(ms).format('YYYY-MM-DD HH:mm') })
    ) || t('nav.banned.permanent')
  showBanScreen({
    title: t('nav.login.device.bannedTitle'),
    reason: reason || t('nav.login.device.bannedFallback'),
    until: untilLine,
    appeal: t('nav.banned.appeal'),
    variant: 'device'
  })
}

function notifyDeviceRequired(detail: DeviceLoginDetail) {
  // 后端 message 更贴近当前策略（平板绑定/临时密码），i18n 只做兜底。
  showAuthAlert(
    {
      variant: 'warning',
      title: t('nav.login.device.requiredTitle'),
      message: detail.message || t('nav.login.device.required'),
      ackText: t('nav.login.device.requiredAck')
    },
    { ack: focusPassword }
  )
}

// 本机不是主设备：挑战令牌 5 分钟过期且只能用一次，切到临时密码表单后没有退路，
// 所以先让用户确认「要用临时密码」，不再像以前那样静默换表单。
function notifyDeviceNotPrimary(detail: DeviceLoginDetail) {
  showAuthAlert(
    {
      variant: 'warning',
      title: t('nav.login.device.notPrimaryTitle'),
      message: detail.message || t('nav.login.device.tempHint'),
      ackText: t('nav.login.device.notPrimaryAck'),
      actionText: t('nav.login.device.notPrimaryAction')
    },
    {
      ack: () => {
        backToNormal()
        focusPassword()
      },
      action: () => enterTempMode(detail)
    }
  )
}

// 没拿到 challenge 就没法换临时密码，只能告知并回到登录表单。
function notifyDeviceNotPrimaryNoChallenge() {
  showAuthAlert(
    {
      variant: 'warning',
      title: t('nav.login.device.notPrimaryTitle'),
      message: t('nav.login.device.challengeMissing'),
      ackText: t('nav.login.device.notPrimaryAck')
    },
    { ack: focusPassword }
  )
}

// 后端把「密码错误」与「用户不存在」归一成同一条 403 字符串 detail，
// 页面侧统一按「账号或密码不正确」提示，不区分具体哪一种。
function isPasswordRejected(error: unknown): boolean {
  const response = (error as { response?: { status?: number; data?: { detail?: unknown } } })
    ?.response
  return response?.status === 403 && typeof response.data?.detail === 'string'
}

function notifyPasswordError() {
  showAuthAlert(
    {
      variant: 'danger',
      title: t('nav.login.passwordErrorTitle'),
      message: t('nav.login.passwordErrorMessage'),
      ackText: t('nav.login.passwordErrorAck')
    },
    {
      ack: () => {
        // 留下的密码一定是错的，清空后直接把焦点还给密码框。
        password.value = ''
        focusPassword()
      }
    }
  )
}

// 登录失败的统一出口:设备闸三类错误优先,其余保持原有维护/封禁分支。
function handleLoginFailure(error: unknown) {
  const detail = deviceDetailOf(error)
  if (detail?.code === 'DEVICE_NOT_PRIMARY') {
    if (detail.challenge) {
      notifyDeviceNotPrimary(detail)
    } else {
      notifyDeviceNotPrimaryNoChallenge()
    }
    return
  }
  if (detail?.code === 'DEVICE_REQUIRED') {
    notifyDeviceRequired(detail)
    return
  }
  if (detail?.code === 'DEVICE_BANNED') {
    notifyDeviceBanned(error)
    return
  }
  if (isMaintenanceBlocked(error)) {
    notifyMaintenanceBlocked()
  } else if (isUserBanned(error)) {
    notifyUserBanned(error)
  } else if (isPasswordRejected(error)) {
    // 顺序不能反：USER_BANNED 也是 403 字符串 detail，先由封禁分支收口，
    // 剩下的 403 字符串才归到密码错误。
    notifyPasswordError()
  }
}

// 临时密码兑换失败:后端给结构化 detail(人话 message),挑战过期/一次性用掉
// 都没有第二次机会,只能返回重新登录拿新挑战,所以提示里必须写清退路。
function tempErrorText(detail: DeviceLoginDetail | null): string {
  if (detail?.code === 'TEMP_PASSWORD_INVALID') {
    return t('nav.login.device.tempInvalid')
  }
  if (detail?.code === 'DEVICE_CHALLENGE_MISMATCH') {
    return t('nav.login.device.tempMismatch')
  }
  return detail?.message || t('nav.login.device.tempFailed')
}

async function submitTempPassword() {
  if (tempSubmitting.value || !/^\d{8}$/.test(tempPassword.value)) {
    return
  }
  tempSubmitting.value = true
  tempError.value = ''
  try {
    const result = await api.user.auth.useTempPasswordAuth(challenge.value, tempPassword.value)
    const information: User | UserProfile =
      result.profile ?? ((await api.user.readOne(result._id)) as User)
    // 临时会话不能复用 setUser:那条路会拿明文密码重新登录,而这里只有一次性
    // 密码。安装方式与 setUser 一致,但 weak_password=false 不置位。
    await userStore.setUserInformation(information)
    userStore.shouldResetPassword = false
    userStore.isLogin = true
    ElMessage({
      message: t('nav.login.device.tempSession'),
      type: 'success',
      grouping: true,
      plain: true
    })
    await router.replace(await postLoginTarget())
  } catch (error) {
    const detail = deviceDetailOf(error)
    if (detail?.code === 'DEVICE_BANNED') {
      notifyDeviceBanned(error)
      return
    }
    if (isUserBanned(error)) {
      notifyUserBanned(error)
      return
    }
    tempError.value = tempErrorText(detail)
  } finally {
    tempSubmitting.value = false
  }
}

if (userStore.isLogin) {
  void postLoginTarget().then((target) => router.replace(target))
} else {
  localStorage.removeItem('token')
}

async function login() {
  if (loading.value || password.value === '' || String(user.value).length !== 8) {
    return
  }
  if (!userStore.validatePasswordStrength(password.value)) {
    loading.value = true

    const id = user.value
    const { token, _id } = (await api.user.auth
      .useLongTermAuth(id, password.value, 'short', 'login', { suppressErrorToast: true })
      .catch((error: unknown) => {
        loading.value = false
        handleLoginFailure(error)
      })) || {
      token: '',
      _id: ''
    }
    if (!token) {
      loading.value = false
      return
    }
    await ElMessageBox({
      message: h('p', null, [
        h('span', null, '为确保您的账号安全，'),
        h('strong', null, '您必须修改密码后才能继续使用本系统。')
      ]),
      showCancelButton: true,
      title: '您必须修改密码',
      confirmButtonText: '修改密码',
      cancelButtonText: '取消',
      showClose: false,
      closeOnClickModal: false,
      closeOnPressEscape: false
    })
      .then(() => reset_and_login(_id, token))
      .catch(() => {
        loading.value = false
      })
    return
  }
  loading.value = true
  const id = user.value
  await userStore
    .setUser(id, password.value as string, { suppressErrorToast: true })
    .then(async () => router.replace(await postLoginTarget()))
    .catch((error: unknown) => {
      loading.value = false
      handleLoginFailure(error)
    })
}

async function reset_and_login(id: string, token: string) {
  await modifyPasswordDialogs(
    id,
    'zh-CN',
    async (a: string, new_password: string) => {
      await api.user.password.put(id, new_password, token).catch(() => {
        loading.value = false
      })
      await userStore
        .setUser(id, new_password)
        .then(async () => router.replace(await postLoginTarget()))
        .catch((error: unknown) => {
          loading.value = false
          handleLoginFailure(error)
        })
    },
    token
  ).catch(() => {
    loading.value = false
  })
}

// 提交按钮可用性：8 位学号 + 非空密码
const canSubmit = computed(() => String(user.value).length === 8 && password.value !== '')
</script>

<template>
  <div class="yg2-login">
    <section class="yg2-login__card" aria-labelledby="yg2-login-title">
      <div class="yg2-login__brand">
        <span class="yg2-login__mark">
          <img src="/favicon.png" alt="" />
        </span>
        <span class="yg2-login__brand-name">义工平台</span>
      </div>

      <h1 id="yg2-login-title" class="yg2-login__title">欢迎回来</h1>
      <p class="yg2-login__subtitle">{{ t('nav.login.motto') }}</p>

      <!-- 临时密码兑换表单：DEVICE_NOT_PRIMARY 且拿到 challenge 后切换到这里 -->
      <form
        v-if="mode === 'temp'"
        class="yg2-login__form"
        novalidate
        @submit.prevent="submitTempPassword"
      >
        <p class="yg2-login__subtitle">{{ t('nav.login.device.tempTitle') }}</p>
        <label class="yg2-field">
          <span class="yg2-field__label">{{ t('nav.login.device.tempHint') }}</span>
          <input
            v-model="tempPassword"
            class="yg2-input"
            type="text"
            inputmode="numeric"
            maxlength="8"
            autocomplete="one-time-code"
            placeholder="8 位数字临时密码"
          />
        </label>
        <p v-if="tempError" class="yg2-login__hint yg2-login__hint--danger">
          {{ tempError }}
        </p>
        <button
          class="yg2-btn yg2-btn--primary yg2-btn--lg yg2-login__submit"
          type="submit"
          :disabled="tempSubmitting"
        >
          {{ t('nav.login.device.tempSubmit') }}
        </button>
      </form>

      <form v-else class="yg2-login__form" novalidate @submit.prevent="login">
        <label class="yg2-field">
          <span class="yg2-field__label">{{ t('nav.login.form.account') }}</span>
          <input
            v-model="user"
            class="yg2-input"
            type="text"
            inputmode="numeric"
            autocomplete="username"
            maxlength="8"
            placeholder="8 位学号"
          />
        </label>

        <label class="yg2-field">
          <span class="yg2-field__label">{{ t('nav.login.form.password') }}</span>
          <input
            ref="passwordInput"
            v-model="password"
            class="yg2-input"
            type="password"
            autocomplete="current-password"
            placeholder="密码"
          />
        </label>

        <button
          class="yg2-btn yg2-btn--primary yg2-btn--lg yg2-login__submit"
          type="submit"
          :disabled="!canSubmit || loading"
        >
          <span v-if="!loading">{{ t('nav.login.actions.login') }}</span>
          <span v-else class="yg2-login__spinner" aria-hidden="true" />
        </button>
      </form>

      <p class="yg2-login__hint">首次登录密码为学号，登录后请立即修改</p>
    </section>

    <!-- 登录失败告警：密码错误 / 弱密码 / 设备闸三类，都走仓库统一的 ZAuthAlert -->
    <ZAuthAlert
      v-if="authAlert.visible"
      :variant="authAlert.variant"
      :title="authAlert.title"
      :message="authAlert.message"
      :hint="authAlert.hint"
      :ack-text="authAlert.ackText"
      :action-text="authAlert.actionText"
      @ack="onAuthAlertAck"
      @action="onAuthAlertAction"
    />
  </div>
</template>

<style scoped>
.yg2-login {
  display: grid;
  place-items: center;
  min-height: 100%;
  padding: 32px 20px 72px;
}

/* 登录卡：实色哑光面 + 28px 圆角 + 单层柔和投影 */
.yg2-login__card {
  width: 100%;
  max-width: 420px;
  padding: 40px 36px 32px;
  border: 1px solid var(--yg2-line);
  border-radius: var(--yg2-r-32);
  background: var(--yg2-surface);
  box-shadow: var(--yg2-shadow-raised);
}

.yg2-login__brand {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 28px;
}

.yg2-login__mark {
  display: grid;
  place-items: center;
  width: 36px;
  height: 36px;
  overflow: hidden;
  border-radius: 11px;
  background: var(--yg2-surface-sunken);
}

.yg2-login__mark img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.yg2-login__brand-name {
  font-size: 13px;
  font-weight: 600;
  letter-spacing: 0.08em;
  color: var(--yg2-text-secondary);
}

.yg2-login__title {
  margin: 0;
  font-size: 30px;
  font-weight: 700;
  letter-spacing: -0.02em;
  line-height: 1.15;
  color: var(--yg2-text);
}

.yg2-login__subtitle {
  margin: 8px 0 0;
  font-size: 14px;
  letter-spacing: 0.04em;
  color: var(--yg2-text-tertiary);
}

.yg2-login__form {
  display: grid;
  gap: 18px;
  margin-top: 28px;
}

.yg2-login__submit {
  width: 100%;
  margin-top: 6px;
}

.yg2-login__hint {
  margin: 22px 0 0;
  font-size: 12px;
  text-align: center;
  color: var(--yg2-text-tertiary);
}

.yg2-login__spinner {
  width: 18px;
  height: 18px;
  border: 2px solid rgba(255, 255, 255, 0.35);
  border-top-color: #fff;
  border-radius: 50%;
  animation: yg2-login-spin 0.8s linear infinite;
}

@keyframes yg2-login-spin {
  to {
    transform: rotate(360deg);
  }
}

@media (prefers-reduced-motion: reduce) {
  .yg2-login__spinner {
    animation-duration: 2s;
  }
}

@media (max-width: 640px) {
  .yg2-login {
    padding: 16px 14px 48px;
  }

  .yg2-login__card {
    padding: 30px 22px 24px;
    border-radius: var(--yg2-r-26);
  }

  .yg2-login__title {
    font-size: 26px;
  }
}
</style>
