<script setup lang="ts">
import { computed, ref, h } from 'vue'
import { ElMessageBox, ElMessage } from 'element-plus'
import { useUserStore } from '@/stores/user'
import { useHeaderStore } from '@/stores/header'
import { useRouter, useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { modifyPasswordDialogs } from '@/views'
import api from '@/api'
import { getMaintenanceStatus } from '@/api/system/maintenance.ts'
import { resetMaintenanceGuardCache } from '@/router/maintenanceGuard'
import { formatBanUntil, readBanInfo } from '@/utils/ban'
import dayjs from 'dayjs'

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
  const response = (
    error as { response?: { status?: number; data?: { maintenance?: boolean } } }
  )?.response
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
  const response = (
    error as { response?: { status?: number; data?: { detail?: unknown } } }
  )?.response
  return response?.status === 403 && response?.data?.detail === 'USER_BANNED'
}

function notifyUserBanned(error: unknown) {
  const { reason, until } = readBanInfo(
    (error as { response?: { headers?: unknown } })?.response?.headers
  )
  // 期限行:until=0 是永久;否则交给调用方按 locale 决定日期格式(util 不碰 i18n)。
  const untilLine =
    formatBanUntil(until, (ms) => t('nav.banned.until', { time: dayjs(ms).format('YYYY-MM-DD HH:mm') })) ||
    t('nav.banned.permanent')
  // alert 只有一个确认按钮:用户必须显式确认才能关(不允许点遮罩误关掉原因)。
  void ElMessageBox.alert(
    h('div', null, [
      h('p', { style: 'margin: 0 0 8px;' }, reason || t('nav.banned.reasonFallback')),
      h('p', { style: 'margin: 0;' }, untilLine)
    ]),
    t('nav.banned.title'),
    {
      confirmButtonText: t('nav.banned.ack'),
      closeOnClickModal: false,
      closeOnPressEscape: false,
      showClose: false
    }
  ).catch(() => {
    // 弹窗被程序化关闭时 alert 会 reject,这里吞掉即可,不影响登录流程。
  })
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
      .useLongTermAuth(id, password.value, 'short', 'login')
      .catch((error: unknown) => {
        loading.value = false
        if (isMaintenanceBlocked(error)) {
          notifyMaintenanceBlocked()
        } else if (isUserBanned(error)) {
          notifyUserBanned(error)
        }
      })) || {
      token: '',
      _id: '',
    }
    if (!token) {
      loading.value = false
      return
    }
    await ElMessageBox({
      message: h(
        'p',
        null,
        [
          h('span', null, '为确保您的账号安全，'),
          h('strong', null, '您必须修改密码后才能继续使用本系统。')
        ]
      ),
      showCancelButton: true,
      title: '您必须修改密码',
      confirmButtonText: '修改密码',
      cancelButtonText: '取消',
      showClose: false,
      closeOnClickModal: false,
      closeOnPressEscape: false,
    }).then(() => reset_and_login(_id, token)).catch(() => {
      loading.value = false
    })
    return
  }
  loading.value = true
  const id = user.value
  await userStore
    .setUser(id, password.value as string)
    .then(async () => router.replace(await postLoginTarget()))
    .catch((error: unknown) => {
      loading.value = false
      if (isMaintenanceBlocked(error)) {
        notifyMaintenanceBlocked()
      } else if (isUserBanned(error)) {
        notifyUserBanned(error)
      }
    })
}

async function reset_and_login(id: string, token: string) {
  await modifyPasswordDialogs(id, 'zh-CN', async (a: string, new_password: string) => {
    await api.user.password.put(id, new_password, token).catch(() => {
      loading.value = false
    })
    await userStore
      .setUser(id, new_password)
      .then(async () => router.replace(await postLoginTarget()))
      .catch(() => {
        loading.value = false
      })
  }, token).catch(() => {
    loading.value = false
  })
}

// 提交按钮可用性：8 位学号 + 非空密码
const canSubmit = computed(() => String(user.value).length === 8 && password.value !== '')
</script>

<template>
  <div class="yg2-login">
    <section class="yg2-login__card yg2-glass-surface yg2-glass-surface--chrome" aria-labelledby="yg2-login-title">
      <div class="yg2-login__brand">
        <span class="yg2-login__mark">
          <img src="/favicon.ico" alt="" />
        </span>
        <span class="yg2-login__brand-name">义工平台</span>
      </div>

      <h1 id="yg2-login-title" class="yg2-login__title">欢迎回来</h1>
      <p class="yg2-login__subtitle">{{ t('nav.login.motto') }}</p>

      <form class="yg2-login__form" novalidate @submit.prevent="login">
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
  </div>
</template>

<style scoped>
.yg2-login {
  display: grid;
  place-items: center;
  min-height: 100%;
  padding: 32px 20px 72px;
}

/* 登录卡：玻璃材质由 .yg2-glass-surface--chrome 提供（见 v2/glass.css），
   这里只管尺寸与排布，避免材质散落在各处。 */
.yg2-login__card {
  width: 100%;
  max-width: 420px;
  padding: 40px 36px 32px;
  border-radius: var(--yg2-r-32);
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
