<script setup lang="ts">
import {
  ArrowDown,
  User,
  SwitchButton,
  CircleCloseFilled,
  HomeFilled,
  Calendar,
  Avatar
} from '@element-plus/icons-vue'
import {
  ElIcon,
  ElButton,
  ElPopover,
  ElConfigProvider,
  ElMessageBox,
  ElWatermark
} from 'element-plus'
import { RouterView } from 'vue-router'
import { useUserStore } from '@/stores/user'
import { useTheme } from '@/utils/theme'
import lensNormal from '@/assets/lens/lens-normal.png'
import { useRouter, useRoute } from 'vue-router'
import Password from '@/icons/MaterialSymbolsPasswordRounded.vue'
import AmbientField from '@/ui/shell/AmbientField.vue'
import AppSidebar from '@/ui/shell/AppSidebar.vue'
import OfflineBanner from '@/ui/ux/OfflineBanner.vue'
import AppTopbar from '@/ui/shell/AppTopbar.vue'
import { useHeaderStore } from './stores/header'
import { useWindowSize, useOnline } from '@vueuse/core'
import { useI18n } from 'vue-i18n'
import { computed, watch, ref, onMounted, onUnmounted, h, reactive } from 'vue'
import type { WatermarkFontType } from 'element-plus'
import { zhCn } from 'element-plus/es/locale/index.mjs'
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
const { dark, toggleTheme: toggleThemePreference } = useTheme()

// 深浅色统一由 utils/theme.ts 管理：它同时维护 html.dark 与 data-theme，
// 并保留 auto / light / dark 三态，见 useTheme()。
const effectiveDark = computed(() => dark.value)

/** 把 #RGB / #RRGGBB 令牌值转成带透明度的 rgba，避免在脚本里写死色值。 */
function tokenToRgba(token: string, alpha: number): string {
  const hex = token.trim()
  const short = /^#([0-9a-f])([0-9a-f])([0-9a-f])$/i.exec(hex)
  const full = /^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(hex)
  const match = short ?? full
  if (!match) {
    return hex
  }
  const channels = match.slice(1).map((part) =>
    short ? parseInt(part + part, 16) : parseInt(part, 16)
  )
  return `rgba(${channels.join(', ')}, ${alpha})`
}

// 水印颜色取自 --yg-text-primary 令牌（运行时读取，明暗自适应）
const watermarkColor = computed(() => {
  const isDark = effectiveDark.value
  const raw = getComputedStyle(document.documentElement).getPropertyValue('--yg-text-primary')
  return tokenToRgba(raw, isDark ? 0.12 : 0.15)
})

// 新版设计令牌通过 data-theme 切换深浅色；唯一写入点见上方 effectiveDark 监听。
// （合并分支曾重复挂一个 watch(dark)，会把 auto 态首屏强制成浅色，已移除。）

const watermark = computed<WatermarkFontType>(() => ({
  color: watermarkColor.value,
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

const { width } = useWindowSize()

// 侧边栏形态：>1024 可手动收起；769–1024 自动收为图标栏；≤768 变成抽屉。
const rail = ref(false)
const drawerOpen = ref(false)

watch(
  () => width.value,
  (w) => {
    rail.value = w > 768 && w <= 1024
    if (w <= 768) {
      drawerOpen.value = false
    }
  },
  { immediate: true }
)

function toggleRail() {
  rail.value = !rail.value
}

// headerStore.header 形如「首页 - 义工平台」，顶栏只显示前半段。
/** 底部栏当前高亮项的下标，用来驱动滑块平移 */
const mobileTabIndex = computed(() => {
  const index = mobileTabs.findIndex((tab) => tab.judge(route.path))
  // 既不是首页、项目、我的（例如「菜单」里打开的关于/更新日志/管理页），
  // 高亮落到最后一个格子「菜单」上，而不是硬回退到首页。
  return index < 0 ? mobileTabs.length : index
})

const pageTitle = computed(() => {
  const full = headerStore.header
  const suffix = ` - ${headerStore.base}`
  return full.endsWith(suffix) ? full.slice(0, -suffix.length) : full
})

// 光场按板块换色相，结构不变。
const ambientTone = computed(() => {
  const path = route.path
  if (path.startsWith('/ai')) return 'ai'
  if (path.startsWith('/group')) return 'groups'
  if (path.startsWith('/manage') || path.startsWith('/server')) return 'manage'
  return 'activities'
})

function toggleTheme() {
  toggleThemePreference()
}

// 移动端底栏前三个入口，第四个是抽屉。
const mobileTabs = [
  {
    key: 'home',
    labelKey: 'nav.home',
    icon: HomeFilled,
    judge: (path: string) => path === '/user',
    action: () => router.push('/user')
  },
  {
    key: 'activity',
    labelKey: 'nav.activityShort',
    icon: Calendar,
    judge: (path: string) => path.startsWith('/activit'),
    action: () => router.push('/activities/mine')
  },
  {
    key: 'profile',
    labelKey: 'nav.profile',
    icon: Avatar,
    judge: (path: string) => path.startsWith('/me'),
    action: () => router.push('/me')
  }
]

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
  localStorage.setItem('cached', 'true')
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
  <svg class="yg2-lens-defs" aria-hidden="true" width="0" height="0">
    <defs>
      <filter id="yg2-lens-lg" x="-25%" y="-25%" width="150%" height="150%" color-interpolation-filters="sRGB">
        <feImage :href="lensNormal" preserveAspectRatio="none" result="map" />
        <feDisplacementMap in="SourceGraphic" in2="map" scale="-16" xChannelSelector="R" yChannelSelector="G" />
      </filter>
      <filter id="yg2-lens-sm" x="-25%" y="-25%" width="150%" height="150%" color-interpolation-filters="sRGB">
        <feImage :href="lensNormal" preserveAspectRatio="none" result="map" />
        <feDisplacementMap in="SourceGraphic" in2="map" scale="-9" xChannelSelector="R" yChannelSelector="G" />
      </filter>
    </defs>
  </svg>
  <ElConfigProvider :locale="langPack" class="yg-bg-page full">
    <ElWatermark
      :font="watermark"
      content=""
      :z-index="999"
    >
      <div class="yg-app-shell">
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
        <div
          class="yg2-app"
          :class="{
            'is-rail': rail,
            'is-drawer-open': drawerOpen,
            'is-plain': !userStore.isLogin
          }"
        >
          <AmbientField :tone="ambientTone" />
          <AppSidebar
            v-if="userStore.isLogin"
            :open="drawerOpen"
            :rail="rail"
            @toggle-rail="toggleRail"
            @close="drawerOpen = false"
          />
          <div class="yg2-app__main">
            <AppTopbar
              v-if="userStore.isLogin"
              :title="pageTitle"
              @open-drawer="drawerOpen = true"
            >
              <template #actions>
                <button
                  type="button"
                  class="yg2-icon-btn"
                  :title="
                    effectiveDark ? t('nav.theme.light', '切换到浅色模式') : t('nav.theme.dark', '切换到深色模式')
                  "
                  :aria-label="
                    effectiveDark ? t('nav.theme.light', '切换到浅色模式') : t('nav.theme.dark', '切换到深色模式')
                  "
                  @click="toggleTheme"
                >
                  <svg
                    v-if="effectiveDark"
                    width="17"
                    height="17"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    stroke-width="1.6"
                    stroke-linecap="round"
                  >
                    <circle cx="12" cy="12" r="4.2" />
                    <path
                      d="M12 3v2M12 19v2M3 12h2M19 12h2M5.6 5.6 7 7M17 17l1.4 1.4M18.4 5.6 17 7M7 17l-1.4 1.4"
                    />
                  </svg>
                  <svg
                    v-else
                    width="17"
                    height="17"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    stroke-width="1.6"
                    stroke-linecap="round"
                    stroke-linejoin="round"
                  >
                    <path d="M20 14.5A8.5 8.5 0 0 1 9.5 4a8.5 8.5 0 1 0 10.5 10.5Z" />
                  </svg>
                </button>
                <ElPopover
                  width="200px"
                  trigger="click"
                  placement="bottom-end"
                  :popper-options="{ modifiers: [{ name: 'offset', options: { offset: [0, 10] } }] }"
                  popper-class="yg-user-menu"
                >
                  <template #reference>
                    <button
                      type="button"
                      class="yg2-user-chip"
                      :aria-label="t('nav.accountMenu', '账号菜单')"
                    >
                      <span class="yg2-user-chip__avatar">
                        <ElIcon><User /></ElIcon>
                      </span>
                      <span class="yg2-user-chip__name">{{ userStore.name }}</span>
                      <ElIcon class="yg2-user-chip__caret"><ArrowDown /></ElIcon>
                    </button>
                  </template>
                  <div class="yg-user-menu__list">
                    <ElButton
                      v-for="button in panelButtons"
                      :key="button.text"
                      text
                      :icon="button.icon"
                      :class="['yg-user-menu__item', `yg-user-menu__item--${button.text}`]"
                      @click="button.click"
                    >
                      {{ t(`nav.${button.text}`) }}
                    </ElButton>
                  </div>
                </ElPopover>
              </template>
            </AppTopbar>
            <main class="yg2-app__content">
              <RouterView class="yg-view" />
            </main>
          </div>
          <nav
            v-if="userStore.isLogin"
            class="yg2-mobile-tabs"
            aria-label="快捷导航"
            :style="{
              '--yg2-tabs-count': mobileTabs.length + 1,
              '--yg2-tabs-index': mobileTabIndex
            }"
          >
            <span class="yg2-mobile-tabs__thumb" aria-hidden="true" />
            <button
              v-for="tab in mobileTabs"
              :key="tab.key"
              type="button"
              class="yg2-mobile-tabs__item"
              :class="{ 'is-active': tab.judge(route.path) }"
              @click="tab.action()"
            >
              <component :is="tab.icon" />
              <span>{{ t(tab.labelKey, tab.key) }}</span>
            </button>
            <button
              type="button"
              class="yg2-mobile-tabs__item"
              :class="{ 'is-active': mobileTabIndex === mobileTabs.length }"
              @click="drawerOpen = true"
            >
              <svg
                width="18"
                height="18"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                stroke-width="1.8"
                stroke-linecap="round"
              >
                <path d="M4 7h16M4 12h16M4 17h16" />
              </svg>
              <span>{{ t('nav.menu', '菜单') }}</span>
            </button>
          </nav>
          <div v-if="drawerOpen" class="yg2-drawer-mask" @click="drawerOpen = false" />
        </div>
      </div>
    </ElWatermark>
    <ZNotificationReminder />
    <OfflineBanner />
  </ElConfigProvider>
  </template>

<style scoped>
.full {
  width: 100%;
  height: 100%;
}

.disconnected {
  position: relative;
  top: 40%;
  transform: translateY(16%);
  font-size: 1.2rem;
}

/* 顶栏外观由标准样式库的 .yg-topbar 提供，这里只补项目自有元素 */

.yg-topbar__title {
  font-size: var(--yg-font-size-lg);
  font-weight: var(--yg-font-weight-semibold);
  line-height: var(--yg-line-height-tight);
  color: var(--yg-text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.yg-topbar__logo {
  width: var(--yg-space-7);
  height: var(--yg-space-7);
  border-radius: var(--yg-radius-md);
  object-fit: cover;
}

.yg-topbar__right {
  display: flex;
  align-items: center;
  gap: var(--yg-space-2);
  flex-shrink: 0;
}

/* 账号胶囊：头像 + 用户名 + 展开箭头合成一个整体，替代原先两个 is-text 主色按钮 */
.yg-user-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 36px;
  padding: 0 4px 0 4px;
  border: 1px solid var(--yg-border-default);
  border-radius: var(--yg-radius-pill);
  background: var(--yg-bg-surface);
  transition:
    border-color var(--yg-dur-fast) var(--yg-ease-out),
    background-color var(--yg-dur-fast) var(--yg-ease-out);
}

.yg-user-chip:hover {
  border-color: var(--yg-color-primary);
  background: var(--yg-bg-hover);
}

.yg-user-chip__avatar {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  border-radius: 50%;
  background: var(--yg-color-primary-soft);
  color: var(--yg-color-primary);
  font-size: 15px;
  line-height: 1;
}

.yg-user-chip__name {
  max-width: 12rem;
  overflow: hidden;
  color: var(--yg-text-primary);
  font-size: var(--yg-font-size-sm);
  font-weight: var(--yg-font-weight-medium);
  /* 行盒压到 26px 与头像同高，三者共用 flex 中线，避免基线把文字顶偏 */
  display: inline-flex;
  align-items: center;
  height: 26px;
  line-height: 1;
  white-space: nowrap;
  text-overflow: ellipsis;
}

/* 箭头按钮保留 24px 触控区，不参与主色填充；图标自身对齐中线 */
.yg-user-chip__toggle {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  padding: 0;
  border: 0;
  border-radius: 50%;
  background: transparent;
  color: var(--yg-text-secondary);
  font-size: 13px;
  line-height: 1;
  cursor: pointer;
  transition:
    color var(--yg-dur-fast) var(--yg-ease-out),
    background-color var(--yg-dur-fast) var(--yg-ease-out);
}

.yg-user-chip__toggle > .el-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  line-height: 1;
}

.yg-user-chip__toggle:hover {
  background: var(--yg-bg-active);
  color: var(--yg-text-primary);
}

.yg-user-chip__toggle:focus-visible {
  outline: none;
  box-shadow: var(--yg-focus-ring);
}

/* 账号菜单：中性项 + 危险项（退出登录属高危，走 --yg-color-danger）
   注：弹层挂在 body 下，scoped 选择器到不了，这里用 :global 显式声明
   （面板色另在 popper-class 同规则里补，避免白字白底） */
:global(.yg-user-menu__list) {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

/* 菜单只有两项，压到 32px 行高 + 8px 内边距，整体更紧凑。
   注意：Element Plus 有 `.el-button + .el-button { margin-left: 12px }`，
   在纵向列表里会把第二项顶偏 12px —— 必须显式清零，否则两项左边缘对不齐。 */
:global(.yg-user-menu__list .yg-user-menu__item.el-button) {
  justify-content: flex-start;
  width: 100%;
  height: 34px;
  margin: 0;
  padding: 0 10px;
  border-radius: var(--yg2-r-8);
  color: var(--yg2-text);
  font-size: 13.5px;
  transition:
    background-color 160ms var(--yg2-ease-out),
    color 160ms var(--yg2-ease-out);
}

:global(.yg-user-menu__list .yg-user-menu__item.el-button:hover),
:global(.yg-user-menu__list .yg-user-menu__item.el-button:focus) {
  background: var(--yg2-surface-sunken);
  color: var(--yg2-text);
}

:global(.yg-user-menu__list .yg-user-menu__item.el-button.yg-user-menu__item--logout) {
  color: var(--yg2-danger);
}

:global(.yg-user-menu__list .yg-user-menu__item.el-button.yg-user-menu__item--logout:hover) {
  background: var(--yg2-danger-soft);
  color: var(--yg2-danger);
}

/* 图标与文字基线对齐，两项的起始位置一致 */
:global(.yg-user-menu__list .yg-user-menu__item.el-button > .el-icon) {
  margin-right: 8px;
  font-size: 15px;
}

:global(.yg-user-menu__list .yg-user-menu__item.el-button > span) {
  display: inline-flex;
  align-items: center;
}

:global(.yg-user-menu) {
  padding: 6px;
}

/* 极简风去掉指向箭头，弹层直接以右边缘对齐胶囊 */
:global(.yg-user-menu .el-popper__arrow),
:global(.yg-user-menu > .el-popper__arrow) {
  display: none;
}

/* 封禁全屏：危险态用红色系（准则第 5 章）。深红底不在语义调色板内，
   这里取 --yg-red-800 色阶令牌；文字用 --yg-color-on-danger。 */
.banned-shield {
  background: var(--yg-red-800);
  color: var(--yg-color-on-danger);
  animation: banned-shield-in 0.2s ease-out;
}

.banned-card {
  max-width: 32rem;
  padding: 3rem 2rem;
}

.banned-icon {
  color: var(--yg-red-100);
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
  font-size: 1rem;
  font-variant-numeric: tabular-nums;
  opacity: 0.9;
  margin: 0.5rem 0 0;
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

.yg-form-wrap .el-form-item--default .el-form-item__content {
  display: block;
  overflow-wrap: break-word;
}

@media print {
  body {
    font-size: 12px;
  }
}
</style>
