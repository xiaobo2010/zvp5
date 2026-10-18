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
import { useRegisterSW } from 'virtual:pwa-register/vue'
import { CarbonCloudOffline } from '@/icons'
import { modifyPasswordDialogs } from '@/views'
import { getMaintenanceStatus } from '@/api/system/maintenance.ts'

const { needRefresh, offlineReady, updateServiceWorker } = useRegisterSW()

const online = useOnline()
const cached = ref(localStorage.getItem('cached'))

// reload once when a NEW service worker takes control (skip first install)
if ('serviceWorker' in navigator) {
  const hadController = !!navigator.serviceWorker.controller
  navigator.serviceWorker.addEventListener('controllerchange', () => {
    if (hadController) {
      window.location.reload()
    }
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

onUnmounted(() => {
  if (banTimer) {
    clearInterval(banTimer)
    banTimer = undefined
  }
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
      await userStore.removeUser()
      router.replace('/user/login')
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
  // 启动时检查一次维护状态(公开接口):维护中跳公告页并略过注定 503 的用户刷新;admin 放行
  try {
    const res = await getMaintenanceStatus()
    if (res.data.data.enabled) {
      if (!userStore.position.includes('admin') && route.path !== '/maintenance') {
        router.replace('/maintenance')
      }
      return
    }
  } catch {
    // 状态接口不可达时静默,不阻塞正常启动
  }
  if (userStore.isLogin) {
    await userStore.refreshUser().catch(() => {})
    resetPassword()
  }
})
</script>

<template>
  <ElConfigProvider :locale="langPack" class="bg-slate-100 dark:bg-gray-900 full">
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
          <p class="banned-message">{{ t('nav.banned.message') }}</p>
          <p class="banned-appeal">{{ t('nav.banned.appeal') }}</p>
          <p v-show="banCountdown > 0" class="banned-countdown">
            {{ t('nav.banned.autoLogoutIn', { seconds: banCountdown }) }}
          </p>
        </div>
      </div>
      <ElContainer
        @contextmenu.prevent
        :inert="banLocked"
        class="bg-slate-100 dark:bg-gray-900"
        direction="vertical"
        :style="{ width: width + 'px', height: height + 'px' }"
      >
        <ElHeader>
          <ElRow :class="['pt-4', verticalMode && userStore.isLogin ? 'px-1' : 'px-4']">
            <ElCol :span="16">
              <div
                :class="[
                  'text-2xl',
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
                <ElIcon style="border-radius: .25rem;"><img src="/favicon.ico" class="scale-50" style="border-radius: 20%;" alt="favicon" /></ElIcon>
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
            class="bg-slate-50 dark:bg-gray-950 view fragment-container"
            style="width: 100%; height: 100%; box-sizing: border-box;"
          />
        </ElContainer>
      </ElContainer>
    </ElWatermark>
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
  border-radius: 1rem;
  width: 100%;
}

.user {
  position: absolute;
  right: 0;
}

.banned-shield {
  background:
    radial-gradient(900px 600px at 50% 30%, rgba(255, 255, 255, 0.08), transparent 70%),
    #7f1d1d;
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
