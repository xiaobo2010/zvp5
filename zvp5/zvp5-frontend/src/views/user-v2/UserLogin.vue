<script setup lang="ts">
import { ref, watch, h, reactive, nextTick } from 'vue'
import { ElButton } from 'element-plus/es/components/button/index.mjs'
import { ElInput } from 'element-plus/es/components/input/index.mjs'
import { ElMessageBox } from 'element-plus/es/components/message-box/index.mjs'
import { ElMessage } from 'element-plus/es/components/message/index.mjs'
import { ArrowRight } from '@element-plus/icons-vue'
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
import dayjs from 'dayjs'
import type { User, UserProfile } from '@/../types'

const header = useHeaderStore()

const { t } = useI18n()

const user = ref('')
const password = ref<string>('')
const passwordInput = ref<InstanceType<typeof ElInput> | null>(null)
const loginfield = ref()
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

watch(user, async () => {
  await loginfield.value.validate()
})
</script>

<template>
  <div class="yg-login-v2">
    <aside class="yg-login-v2__brand" aria-label="平台介绍">
      <span class="yg-phi-login-mark" aria-hidden="true">ZVMS</span>
      <div class="yg-phi-login-rail" aria-hidden="true">
        <span>SYS // LOGIN</span>
        <span>ZH-HS · VOLUNTEER</span>
      </div>
      <div class="yg-login-v2__brand-inner">
        <span class="yg-login-v2__logo">
          <img src="/pwa-192x192.png" alt="" />
        </span>
        <p class="yg-login-v2__eyebrow">镇海中学 · 义工管理</p>
        <h1 class="yg-login-v2__headline">把时间，<br />留给有意义的事。</h1>
        <p class="yg-login-v2__lead">记录义工时长，参与校园与社区活动，查看自己的参与轨迹。</p>
        <ul class="yg-login-v2__points">
          <li>义工时长与折算规则</li>
          <li>活动状态与参与记录</li>
          <li>个人资料与设备安全</li>
        </ul>
      </div>
    </aside>

    <main class="yg-login-v2__main">
      <section class="yg-login__card" aria-labelledby="yg-login-title">
        <span class="yg-phi-login-tag" aria-hidden="true">
          {{ mode === 'normal' ? 'AUTH // 01' : 'AUTH // 02' }}
        </span>
        <div class="yg-login__brand">
          <span class="yg-login__logo">
            <img src="/favicon.ico" alt="" />
          </span>
          <span class="yg-login__brand-name">义工平台</span>
        </div>

        <h1 id="yg-login-title" class="yg-login__title">{{ t('nav.login.pageTitle') }}</h1>
        <p class="yg-login__subtitle">{{ t('nav.login.motto') }}</p>

        <ElForm
          v-if="mode === 'normal'"
          ref="loginfield"
          label-position="top"
          class="yg-login__form"
        >
          <ElFormItem :label="t('nav.login.form.account')" prop="id">
            <ElInput
              v-model.number="user"
              :minlength="8"
              :maxlength="8"
              clearable
              type="number"
              class="yg-login__input w-full"
            />
          </ElFormItem>
          <ElFormItem :label="t('nav.login.form.password')" prop="password">
            <ElInput
              ref="passwordInput"
              type="password"
              v-model="password"
              clearable
              show-password
              class="yg-login__input w-full"
              @keyup.enter="login"
            />
          </ElFormItem>
        </ElForm>

        <div v-else class="yg-login__temp">
          <p class="yg-login__temp-title">{{ t('nav.login.device.tempTitle') }}</p>
          <p class="yg-login__temp-hint">{{ t('nav.login.device.tempHint') }}</p>
          <ElInput
            v-model="tempPassword"
            :maxlength="8"
            inputmode="numeric"
            autocomplete="one-time-code"
            :placeholder="t('nav.login.device.tempPlaceholder')"
            class="yg-login__input yg-login__temp-input w-full"
            @keyup.enter="submitTempPassword"
          />
          <p class="yg-login__temp-note">{{ t('nav.login.device.tempNote') }}</p>
          <p v-if="tempError" class="yg-login__error" role="alert">{{ tempError }}</p>
        </div>

        <div class="yg-login__actions">
          <template v-if="mode === 'normal'">
            <ElButton
              type="primary"
              size="large"
              class="w-full"
              @click="login"
              :icon="ArrowRight"
              :disabled="password === '' || String(user).length !== 8"
              :loading="loading"
            >
              {{ t('nav.login.actions.login') }}
            </ElButton>
          </template>
          <template v-else>
            <ElButton
              type="primary"
              size="large"
              class="w-full"
              @click="submitTempPassword"
              :disabled="tempPassword.length !== 8"
              :loading="tempSubmitting"
            >
              {{ t('nav.login.device.tempSubmit') }}
            </ElButton>
            <ElButton size="large" text class="w-full yg-login__back" @click="backToNormal">
              {{ t('nav.login.device.tempBack') }}
            </ElButton>
          </template>
        </div>
      </section>
    </main>

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

    <ZBanScreen
      v-if="banScreen.visible"
      :title="banScreen.title"
      :reason="banScreen.reason"
      :until="banScreen.until"
      :appeal="banScreen.appeal"
      :variant="banScreen.variant"
      @ack="closeBanScreen"
    />
  </div>
</template>

<style scoped>
.yg-login {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 100%;
  padding: var(--yg-space-6) 0;
}

/* 登录卡：Phi 方角；切角、硬阴影、明暗配色由 phi-theme.css 接管 */
.yg-login__card {
  width: 100%;
  max-width: 400px;
  padding: var(--yg-space-8);
  background-color: var(--yg-bg-surface);
  border: calc(var(--yg-space-1) / 4) solid var(--yg-border-subtle);
  border-radius: 0;
  box-shadow: var(--yg-shadow-lg);
}

.yg-login__brand {
  display: flex;
  align-items: center;
  gap: var(--yg-space-3);
  margin-bottom: var(--yg-space-6);
}

.yg-login__logo {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: var(--yg-space-9);
  height: var(--yg-space-9);
  flex-shrink: 0;
  border-radius: 0;
  overflow: hidden;
}

.yg-login__logo img {
  width: 100%;
  height: 100%;
  border-radius: inherit;
  object-fit: cover;
}

.yg-login__brand-name {
  font-size: var(--yg-font-size-sm);
  font-weight: var(--yg-font-weight-medium);
  color: var(--yg-text-tertiary);
  letter-spacing: 0.08em;
}

.yg-login__title {
  margin: 0;
  font-size: var(--yg-font-size-3xl);
  font-weight: var(--yg-font-weight-semibold);
  line-height: var(--yg-line-height-tight);
  color: var(--yg-text-primary);
}

.yg-login__subtitle {
  margin: var(--yg-space-2) 0 0;
  font-size: var(--yg-font-size-sm);
  color: var(--yg-text-tertiary);
}

.yg-login__form {
  margin-top: var(--yg-space-6);
}

.yg-login__form :deep(.el-form-item) {
  margin-bottom: var(--yg-space-5);
}

/* 登录控件用大号档（44px），移动端触控目标达标 */
.yg-login__input {
  --el-input-height: var(--yg-control-height-lg);
}

.yg-login__actions {
  margin-top: var(--yg-space-6);
}

/* 临时密码模式：说明 + 8 位数字输入 + 行内错误（准则 3.5：错误用红字，不用绿框） */
.yg-login__temp {
  margin-top: var(--yg-space-6);
}

.yg-login__temp-title {
  margin: 0;
  font-size: var(--yg-font-size-sm);
  font-weight: var(--yg-font-weight-semibold);
  color: var(--yg-text-primary);
}

.yg-login__temp-hint,
.yg-login__temp-note {
  margin: var(--yg-space-2) 0 0;
  font-size: var(--yg-font-size-sm);
  line-height: var(--yg-line-height-normal);
  color: var(--yg-text-secondary);
}

.yg-login__temp-note {
  color: var(--yg-text-tertiary);
}

.yg-login__temp-input {
  margin-top: var(--yg-space-4);
}

.yg-login__temp-input :deep(.el-input__inner) {
  font-family: var(--yg-font-mono);
  letter-spacing: 0.16em;
}

.yg-login__error {
  margin: var(--yg-space-2) 0 0;
  font-size: var(--yg-font-size-sm);
  line-height: var(--yg-line-height-normal);
  color: var(--yg-text-danger);
}

.yg-login__back {
  margin-top: var(--yg-space-2);
}

/* ---- v2 登录页：品牌区 + 表单区（桌面分栏，移动单列） ---- */
.yg-login-v2 {
  display: grid;
  grid-template-columns: minmax(0, 0.92fr) minmax(0, 1.08fr);
  min-height: 100%;
  /* 透出 .yg-phi 的网格/扫描线底 */
  background: transparent;
}

.yg-login-v2__brand {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: var(--yg-space-9);
  border-right: 1px solid var(--yg-border-subtle);
  background: var(--yg-color-primary-soft);
}

.yg-login-v2__brand-inner {
  width: 100%;
  max-width: 380px;
}

.yg-login-v2__logo {
  display: inline-flex;
  width: 64px;
  height: 64px;
  border-radius: 0;
  overflow: hidden;
  box-shadow: var(--yg-shadow-sm);
}

.yg-login-v2__logo img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.yg-login-v2__eyebrow {
  margin: var(--yg-space-6) 0 var(--yg-space-3);
  color: var(--yg-text-link);
  font-size: var(--yg-font-size-sm);
  font-weight: var(--yg-font-weight-medium);
  letter-spacing: 0.08em;
}

.yg-login-v2__headline {
  margin: 0;
  color: var(--yg-text-primary);
  font-size: var(--yg-font-size-3xl);
  font-weight: var(--yg-font-weight-semibold);
  line-height: var(--yg-line-height-tight);
}

.yg-login-v2__lead {
  margin: var(--yg-space-4) 0 0;
  color: var(--yg-text-secondary);
  font-size: var(--yg-font-size-md);
  line-height: var(--yg-line-height-relaxed);
}

.yg-login-v2__points {
  display: flex;
  flex-direction: column;
  gap: var(--yg-space-2);
  margin: var(--yg-space-6) 0 0;
  padding: 0;
  list-style: none;
  color: var(--yg-text-secondary);
  font-size: var(--yg-font-size-sm);
}

.yg-login-v2__points li {
  display: flex;
  align-items: center;
  gap: var(--yg-space-2);
}

.yg-login-v2__points li::before {
  content: '';
  width: 5px;
  height: 5px;
  border-radius: 0;
  background: var(--yg-color-primary);
  transform: rotate(45deg);
}

.yg-login-v2__main {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: var(--yg-space-6);
}

.yg-login-v2__main .yg-login__card {
  max-width: 400px;
}

@media (max-width: 959px) {
  .yg-login-v2 {
    display: block;
  }

  .yg-login-v2__brand {
    display: none;
  }

  .yg-login-v2__main {
    min-height: 100%;
    padding: var(--yg-space-5) var(--yg-space-4);
  }
}

@media (min-width: 960px) {
  .yg-login-v2__main .yg-login__brand {
    display: none;
  }
}
</style>
