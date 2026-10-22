<script setup lang="ts">
import { ArrowDown, User, SwitchButton, CircleCloseFilled } from '@element-plus/icons-vue'
import {
  ElContainer,
  ElHeader,
  ElFooter,
  ElIcon,
  ElRow,
  ElCol,
  ElButton,
  ElButtonGroup,
  ElPopover,
  ElConfigProvider,
  ElDivider,
  ElMessageBox,
  ElWatermark
} from 'element-plus'
import { RouterView } from 'vue-router'
import { useUserStore } from '@/stores/user'
import { useRouter, useRoute } from 'vue-router'
import Password from '@/icons/MaterialSymbolsPasswordRounded.vue'
import UserNav from '@/views/user/UserNav.vue'
import { useHeaderStore } from './stores/header'
import { useWindowSize, useDark, useOnline } from '@vueuse/core'
import { useI18n } from 'vue-i18n'
import { computed, watch, ref, onMounted, onUnmounted, h, reactive } from 'vue'
import type { WatermarkFontType } from 'element-plus'
import { zhCn } from 'element-plus/es/locale/index.mjs'
import ZVerticalNav from '@/components/form/ZVerticalNav.vue'
import ZNotificationReminder from '@/components/notification/ZNotificationReminder.vue'
import { useRegisterSW } from 'virtual:pwa-register/vue'
import { CarbonCloudOffline } from '@/icons'
import { modifyPasswordDialogs } from '@/views'
import {
  startPlatformGuard,
  stopPlatformGuard,
  setPlatformGuardAccount,
  clearPlatformGuardData
} from '@/utils/platformGuard'
import dayjs from '@/plugins/dayjs'

const { needRefresh, offlineReady, updateServiceWorker } = useRegisterSW()

const online = useOnline()
const cached = ref(localStorage.getItem('cached'))

// 新 SW 接管(controllerchange)时刷新一次。skipWaiting + clientsClaim 会立即接管,
// 所以部署后无需等下一次导航。首次安装时页面本来就没有 controller(hadController
// 为 false),不刷新;reloaded 保证只 reload 一次,避免重复刷新。
if ('serviceWorker' in navigator) {
  const hadController = !!navigator.serviceWorker.controller
  let reloaded = false
  navigator.serviceWorker.addEventListener('controllerchange', () => {
    if (!hadController || reloaded) return
    reloaded = true
    window.location.reload()
  })
}

window.onbeforeunload = (e) => {
  if (location.pathname.startsWith('/activity/create/') || location.pathname.endsWith('/modify')) {
    e.preventDefault()
    return false
  }
}

window.oncontextmenu = (e) => {
  e.preventDefault()
  return false
}

const { t, locale } = useI18n()

const langPack = ref(zhCn)

const route = useRoute()
const router = useRouter()
const userStore = useUserStore()
const headerStore = useHeaderStore()
const dark = useDark()

const watermark = computed<WatermarkFontType>(() => ({
  color: dark.value ? 'rgba(255,255,255,.10)' : 'rgba(0,0,0,.15)',
  fontSize: 16,
  fontWeight: 'normal',
  fontStyle: 'normal',
  fontFamily: 'sans-serif',
  textAlign: 'center',
  textBaseline: 'hanging'
}))

async function resetPassword() {
  if (userStore.shouldResetPassword) {
    const advice = {
      'zh-CN': '您必须修改密码'
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
      title: advice['zh-CN'] as string,
      confirmButtonText: '修改密码',
      cancelButtonText: '退出登录',
      showClose: false,
      closeOnClickModal: false,
      closeOnPressEscape: false,
    })
      .then(async () => {
        try {
          await modifyPasswordDialogs(String(userStore.id), 'zh-CN', userStore.resetPassword)
          .catch(async () => {
            userStore.shouldResetPassword = false
            await userStore.removeUser()
            router.replace('/user/login')
          })
        } catch {
          userStore.shouldResetPassword = false
          await userStore.removeUser()
          router.replace('/user/login')
        }
      })
      .catch(async () => {
        userStore.shouldResetPassword = false
        await userStore.removeUser()
        router.replace('/user/login')
      })
  }
}

watch(
  () => userStore.isLogin,
  (v) => {
    v && resetPassword()
  }
)

// force insecure sessions to log out
const security = ref(Number(localStorage.getItem('security') || '0') - 0)

if (security.value < 4) {
  security.value = 4
  localStorage.setItem('security', '' + security.value)
  userStore.removeUser()
  router.replace('/user/login')
}

// 封禁全屏锁:由 axios 拦截器识别 403 + USER_BANNED 后写入的首次检测时间戳驱动
// (userStore.bannedNoticeAt, unix ms, 0=无)。时间戳随 store 持久化,刷新页面
// 恢复剩余倒计时而非重新计时;到点前不可关闭、不可跳过,到点自动登出。
const BAN_NOTICE_SECONDS = 30
const banCountdown = ref(0)
const banLocked = computed(() => userStore.bannedNoticeAt > 0)
const banFinalizing = ref(false)

// 原因来自 403 响应头(X-Ban-Reason);后端偶发不带时用兜底原因,不让文案出现空洞。
// 期限:banUntil === 0(含空串头)按永久呈现,否则按本地时区格式化到分钟。
const banReasonText = computed(() => userStore.banReason || t('nav.banned.reasonFallback'))
const banUntilText = computed(() =>
  userStore.banUntil > 0
    ? t('nav.banned.until', { time: dayjs(userStore.banUntil).format('YYYY-MM-DD HH:mm') })
    : t('nav.banned.permanent')
)

let banTimer: ReturnType<typeof setInterval> | undefined

async function finalizeBan() {
  if (banFinalizing.value) {
    return
  }
  banFinalizing.value = true
  await userStore.removeUser().catch(() => {})
  try {
    await router.replace('/user/login')
    userStore.bannedNoticeAt = 0
  } catch {
    // 跳转失败保持遮罩(fail-closed),由倒计时 tick 自动重试
  }
  banFinalizing.value = false
}

function updateBanCountdown() {
  if (userStore.bannedNoticeAt <= 0) {
    return
  }
  const left = BAN_NOTICE_SECONDS - (Date.now() - userStore.bannedNoticeAt) / 1000
  banCountdown.value = Math.max(0, Math.ceil(left))
  if (left <= 0) {
    void finalizeBan()
  }
}

watch(
  () => userStore.bannedNoticeAt,
  (at) => {
    if (at > 0) {
      updateBanCountdown()
      if (!banTimer) {
        banTimer = setInterval(updateBanCountdown, 250)
      }
    } else if (banTimer) {
      clearInterval(banTimer)
      banTimer = undefined
      banCountdown.value = 0
      banFinalizing.value = false
    }
  },
  { immediate: true }
)

watch(
  banLocked,
  (locked) => {
    document.body.style.overflow = locked ? 'hidden' : ''
    if (locked) {
      // 失焦当前元素,防止键盘焦点残留在下层可滚动区域继续翻页
      ;(document.activeElement as HTMLElement | null)?.blur()
    }
  },
  { immediate: true }
)

// 客户端违规检测(iframe / 学海桥缺失 / devtools)只在已登录且不在登录页时巡检,
// 登录页自身没有 token 也没有业务页面,巡检只会白跑;登出或组件卸载立即停机。
// 所有检测键按账号 oid 隔离:登录时注入账号,登出(由登录态切回否)时清掉
// 当前账号的键,同机切换账号不会把上一人的节流状态带过来。
function syncPlatformGuard(_newState: [boolean, string], oldState?: [boolean, string]) {
  const [wasLogin, wasPath] = oldState ?? [undefined, '']
  if (!userStore.isLogin && wasLogin && !wasPath?.endsWith('login')) {
    clearPlatformGuardData()
  }
  if (userStore.isLogin && !route.path.endsWith('login')) {
    setPlatformGuardAccount(String(userStore._id))
    startPlatformGuard()
  } else {
    stopPlatformGuard()
  }
}

watch(
  () => [userStore.isLogin, route.path] as [boolean, string],
  (state, oldState) => syncPlatformGuard(state, oldState),
  { immediate: true }
)

onUnmounted(() => {
  if (banTimer) {
    clearInterval(banTimer)
    banTimer = undefined
  }
  stopPlatformGuard()
  document.body.style.overflow = ''
})

watch(
  () => route.path,
  () => {
    if (!location.pathname.startsWith('/activities/')) {
      headerStore.resetHeader()
    }
    if (
      !userStore.isLogin &&
      !route.fullPath.endsWith('login') &&
      !route.fullPath.endsWith('about') &&
      !route.fullPath.endsWith('changelog') &&
      !route.fullPath.endsWith('maintenance')
    ) {
      router.replace('/user/login')
    }
  }
)

locale.value = 'zh-CN'

const { width, height } = useWindowSize()

const verticalMode = ref<boolean>(width.value < height.value * 1.2)

watch(
  () => width.value,
  (w) => {
    verticalMode.value = w < height.value * 1.2
  }
)

const locales: Record<
  (typeof locale)['value'],
  {
    disconnected: {
      title: string
      message: string
    }
  }
> = {
  'zh-CN': {
    disconnected: {
      title: '连接已断开',
      message: '已加载备用缓存数据但无法进行操作。'
    }
  }
}

const panelButtons = [
  {
    icon: Password,
    async click() {
      await modifyPasswordDialogs(String(userStore.id), locale.value, userStore.resetPassword)
    },
    text: 'reset'
  },
  {
    icon: SwitchButton,
    async click() {
      try {
        await ElMessageBox.confirm(
          t('nav.logoutConfirm', '退出登录后需要重新验证身份，确定要退出吗？'),
          t('nav.logout', '退出登录'),
          {
            confirmButtonText: t('nav.logout', '退出登录'),
            cancelButtonText: t('action.cancel', '取消'),
            type: 'warning',
            confirmButtonClass: 'el-button--danger'
          }
        )
        await userStore.removeUser()
        router.replace('/user/login')
      } catch {
        /* 用户取消 */
      }
    },
    text: 'logout'
  }
]

watch(needRefresh, () => {
  if (!needRefresh.value) {
    return
  }
  updateServiceWorker()
})

watch(offlineReady, () => {
  if (!offlineReady.value) {
    return
  }
  localStorage.setItem('cached','true')
})

onMounted(async () => {
  // 维护模式的跳转统一交给 router 全局守卫(见 src/router/maintenanceGuard.ts),
  // 这里只负责登录态刷新;维护页自身不再发起注定 503 的业务请求。
  try {
    if (userStore.isLogin && route.path !== '/maintenance') {
      await userStore.refreshUser().catch(() => {})
      resetPassword()
    }
  } catch {
    // 刷新失败时静默,不阻塞正常启动
  }
})
</script>

<template>
  <ElConfigProvider :locale="langPack" class="z-bg full">
    <ElWatermark
      :font="watermark"
      content=""
      :z-index="999"
    >
      <ElAlert
        type="error"
        center
        :closable="true"
        v-if="(cached || offlineReady) && !online"
        class="hidden-print"
      >
        <template #title>
          <ElIcon class="disconnected">
            <CarbonCloudOffline />
          </ElIcon>
          <span class="text-lg px-1">{{ locales[locale].disconnected.title }}</span>
          <span class="text-sm px-1">{{ locales[locale].disconnected.message }}</span>
        </template>
      </ElAlert>
      <div
        v-if="banLocked"
        class="banned-shield hidden-print fixed inset-0 flex items-center justify-center"
        style="z-index: 9999"
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="banned-title"
        @wheel.prevent
        @touchmove.prevent
        @contextmenu.prevent
      >
        <div class="banned-card flex flex-col items-center text-center px-6">
          <ElIcon class="banned-icon" :size="88">
            <CircleCloseFilled />
          </ElIcon>
          <h2 id="banned-title" class="banned-title">{{ t('nav.banned.title') }}</h2>
          <p class="banned-message">{{ t('nav.banned.message', { reason: banReasonText }) }}</p>
          <p class="banned-until">{{ banUntilText }}</p>
          <p class="banned-appeal">{{ t('nav.banned.appeal') }}</p>
          <p v-show="banCountdown > 0" class="banned-countdown">
            {{ t('nav.banned.autoLogoutIn', { seconds: banCountdown }) }}
          </p>
        </div>
      </div>
      <ElContainer
        @contextmenu.prevent
        :inert="banLocked"
        class="z-bg"
        direction="vertical"
        :style="{ width: width + 'px', height: height + 'px' }"
      >
        <ElHeader>
          <ElRow :class="['pt-2', verticalMode && userStore.isLogin ? 'px-1' : 'px-4']">
            <ElCol :span="16">
              <div
                :class="[
                  'text-[22px]',
                  'font-semibold',
                  'tracking-tight',
                  'w-full',
                  verticalMode && userStore.isLogin ? 'pl-1' : 'pl-10',
                  'pt-1.5',
                  'flex',
                  'items-center'
                ]"
                @dblclick="router.push(userStore.isLogin ? '/user' : '/user/login')"
              >
                <ZVerticalNav v-if="verticalMode && userStore.isLogin" class="pl-6" />
                <ElDivider v-if="verticalMode && userStore.isLogin" direction="vertical" />
                <ElIcon style="width: 28px; height: 28px; font-size: 28px; flex-shrink: 0; border-radius: .25rem; overflow: hidden;"><img src="/favicon.ico" style="width: 28px; height: 28px; border-radius: 20%;" alt="favicon" /></ElIcon>
                <span class="lh-100% ml-2">{{ headerStore.header }}</span>
              </div>
            </ElCol>
            <ElCol :span="8">
              <div class="user" v-if="userStore.isLogin">
                <ElButtonGroup>
                  <ElButton text bg :icon="User" type="primary">
                    {{ userStore.name }}
                  </ElButton>
                  <ElPopover width="216px">
                    <template #reference>
                      <ElButton
                        text
                        bg
                        :icon="ArrowDown"
                        type="primary"
                      />
                    </template>
                    <ElButtonGroup class="w-full" v-for="button in panelButtons" :key="button.text">
                      <ElButton
                        text
                        :icon="button.icon"
                        class="action-btn p-1 w-full"
                        @click="button.click"
                      >
                        {{ t(`nav.${button.text}`) }}
                      </ElButton>
                    </ElButtonGroup>
                  </ElPopover>
                </ElButtonGroup>
              </div>
            </ElCol>
          </ElRow>
        </ElHeader>
        <ElContainer class="full">
          <UserNav style="height: 100%; width: 3.2rem" v-if="userStore.isLogin && !verticalMode" />
          <RouterView
            class="z-surface dark:text-gray-100 view fragment-container"
            style="width: 100%; height: 100%; box-sizing: border-box;"
          />
        </ElContainer>
      </ElContainer>
    </ElWatermark>
    <ZNotificationReminder />
  </ElConfigProvider>
</template>

<style scoped>
.full {
  width: 100%;
  height: 100%;
}

.footer-container {
  height: 3rem;
}

.disconnected {
  position: relative;
  top: 40%;
  transform: translateY(16%);
  font-size: 1.2rem;
}

.footer {
  font-size: 0.8rem;
  position: fixed;
  width: 100%;
  bottom: 0;
  display: flex;
  justify-content: center;
  align-items: center;
}

.fragment-container {
  height: 100%;
  overflow-y: scroll;
  max-height: calc(100vh - 3.75rem);
}

.action-btn {
  width: 100%;
}

.view {
  border-radius: 0;
  width: 100%;
}

.user {
  position: absolute;
  right: 0;
}

.banned-shield {
  background: #7f1d1d;
  color: #fff;
  animation: banned-shield-in 0.2s ease-out;
}

.banned-card {
  max-width: 32rem;
  padding: 3rem 2rem;
}

.banned-icon {
  color: #fecaca;
  margin-bottom: 1.5rem;
}

.banned-title {
  font-size: 2.25rem;
  font-weight: 700;
  letter-spacing: 0.05em;
  margin: 0 0 1rem;
}

.banned-message {
  font-size: 1.125rem;
  opacity: 0.95;
  margin: 0;
}

.banned-appeal {
  font-size: 1rem;
  opacity: 0.75;
  margin: 0.5rem 0 0;
}

.banned-until {
  font-size: 1rem;
  opacity: 0.85;
  margin: 0.5rem 0 0;
  font-variant-numeric: tabular-nums;
}

.banned-countdown {
  margin: 2.5rem 0 0;
  font-size: 1rem;
  font-variant-numeric: tabular-nums;
  opacity: 0.9;
}

@keyframes banned-shield-in {
  from {
    opacity: 0;
  }
  to {
    opacity: 1;
  }
}

@media (prefers-reduced-motion: reduce) {
  .banned-shield {
    animation: none;
  }
}
</style>

<style>
@media print {
  .hidden-print {
    display: none !important;
  }
  .__vue-devtools-container__ {
    display: none !important;
  }
}

body {
  transition-property: color, background-color !important;
  transition-duration: 1s !important;
}

/* 翻页样式统一由 ZPagination 组件负责（响应式换行，无需横向滚动） */

input::-webkit-outer-spin-button,
input::-webkit-inner-spin-button {
  -webkit-appearance: none;
}

input[type='number'] {
  -moz-appearance: textfield;
}

div[data-netlify-site-id] {
  display: none !important;
}

*,
*:active,
*:focus {
  -webkit-tap-highlight-color: rgba(0, 0, 0, 0) !important;
  -webkit-focus-ring-color: rgba(0, 0, 0, 0) !important;
  outline: none !important;
  -ms-overflow-style: none !important;
  scrollbar-width: none !important;
}

.z-wrap .el-form-item--default .el-form-item__content {
  display: block;
  overflow-wrap: break-word;
}

@media print {
  body {
    font-size: 12px;
  }
}
</style>
