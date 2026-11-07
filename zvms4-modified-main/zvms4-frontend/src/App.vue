<script setup lang="ts">
import { ArrowDown, User, SwitchButton, CircleCloseFilled } from '@element-plus/icons-vue'
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
import { useRouter, useRoute } from 'vue-router'
import Password from '@/icons/MaterialSymbolsPasswordRounded.vue'
import UserNav from '@/views/user-v2/UserNav.vue'
import { useHeaderStore } from './stores/header'
import { useWindowSize, useDark, useOnline, usePreferredDark } from '@vueuse/core'
import { useI18n } from 'vue-i18n'
import { computed, watch, ref, onMounted, onUnmounted, h, reactive } from 'vue'
import type { WatermarkFontType } from 'element-plus'
import { zhCn } from 'element-plus/es/locale/index.mjs'
import ZNotificationReminder from '@/components/notification/ZNotificationReminder.vue'
import ZPrimaryDeviceGate from '@/components/notification/ZPrimaryDeviceGate.vue'
import { useRegisterSW } from 'virtual:pwa-register/vue'
import { CarbonCloudOffline } from '@/icons'
// 直接从定义模块引入：绕过 @/views barrel，避免主 chunk 打包全部路由视图
import { modifyPasswordDialogs } from '@/views/user/modifyPasswordDialogs'
import {
  startPlatformGuard,
  stopPlatformGuard,
  setPlatformGuardAccount,
  clearPlatformGuardData
} from '@/utils/platformGuard'
import { startPhiEdgePhase, stopPhiEdgePhase } from '@/utils/phiEdgePhase'
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
const prefersDark = usePreferredDark()

// useDark 底层是 useColorMode，存储值有三态：'auto'（跟随系统）、'dark'、'light'。
// useDark 返回的 isDark 只在「手动深色」时为 true，'auto' 态下即使系统是深色也为 false。
// 这里还原「实际生效」的深浅态，让 --yg-* 令牌与 html.dark 始终一致。
const COLOR_SCHEME_KEY = 'vueuse-color-scheme'
const effectiveDark = computed(() => {
  // 依赖 dark：用户手动切换时重新求值
  const manualDark = dark.value
  const stored = localStorage.getItem(COLOR_SCHEME_KEY)
  if (stored === 'dark') {
    return true
  }
  if (stored === 'light') {
    return false
  }
  if (manualDark) {
    return true
  }
  return prefersDark.value
})

// 主题状态唯一来源仍是 useDark（它负责写 html.dark 与 localStorage）。
// 这里额外把同一状态映射到 data-theme，让 --yg-* 令牌跟着明暗切换
// （准则 2.1：暗色通过 <html data-theme="dark"> 手动切换）。
// 用 flush: 'sync' 让属性同步落到 DOM，后续读取令牌的代码能立刻拿到新值。
watch(
  effectiveDark,
  (value) => {
    document.documentElement.dataset.theme = value ? 'dark' : 'light'
  },
  { immediate: true, flush: 'sync' }
)

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
      !route.fullPath.endsWith('maintenance')
    ) {
      router.replace('/user/login')
    }
  }
)

locale.value = 'zh-CN'

const { width, height } = useWindowSize()

// 竖屏（窄高比）时 UserNav 自带底部悬浮药丸导航，App 外壳只需给底部 dock 让位；
// 横屏时 UserNav 走左侧玻璃栏。折叠/抽屉状态已随旧侧边栏一并移除。
const verticalMode = ref<boolean>(width.value < height.value * 1.2)

// 宽高都要看：旋转屏幕或浏览器地址栏收起时高度会变，只听 width 会漏判。
watch([width, height], ([w, h]) => {
  verticalMode.value = w < h * 1.2
})

// Phi 主题：整个应用（普通用户端 + 后台三个管理区）统一挂 .yg-phi 类
const isPhiRoute = computed(() => {
  const path = route.path
  return (
    path === '/user' ||
    path.startsWith('/user/') ||
    path.startsWith('/activities') ||
    path.startsWith('/activity/') ||
    path === '/me' ||
    path === '/about' ||
    path === '/ai' ||
    path.startsWith('/ai/') ||
    path === '/server' ||
    path.startsWith('/server/') ||
    path === '/manage' ||
    path.startsWith('/manage/') ||
    path.startsWith('/group/')
  )
})

// 页内页签（义工列表 / 平台管理 / 服务器管理 / AI 网关）切换只改路径末段。
// 这些视图由自身的本地状态驱动面板，若仍按 route.path 作为 key，每次点滑块都会整页重挂载：
// 页头动画重播、滚动位置和列表状态丢失、分段滑块的位移也要从 0 重新开始。
// 这里把同一视图的不同末段归一到同一个 key，让外壳常驻，只替换内部面板。
const VIEW_FAMILY: Record<string, string> = {
  'activity-mode': '/activities',
  'manage-action': '/manage',
  'ai-action': '/ai',
  'server-maintenance': '/server',
  'server-database': '/server',
  'server-totp': '/server',
  'server-ssh': '/server',
  'server-files': '/server',
  'server-db-content': '/server'
}

const viewKey = computed(() => {
  const name = route.name?.toString() ?? ''
  return VIEW_FAMILY[name] ?? route.path
})

// Teleport 到 body 的弹层/下拉不在 .yg-phi 内，用 body 标记把 Phi 令牌和样式带出去。
// 仅普通用户路由生效，后台页面保持原样。
watch(
  isPhiRoute,
  (value) => {
    document.body.classList.toggle('yg-phi-portals', value)
  },
  { immediate: true }
)

onUnmounted(() => {
  document.body.classList.remove('yg-phi-portals')
  stopPhiEdgePhase()
})

onMounted(startPhiEdgePhase)

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
        <div :class="['yg-layout', 'yg-layout--app', { 'yg-phi': isPhiRoute }]">
          <div v-if="isPhiRoute" class="yg-phi-velocity" aria-hidden="true"></div>
          <div v-if="isPhiRoute" class="yg-phi-velocity" aria-hidden="true"></div>
          <div v-if="isPhiRoute" class="yg-phi-velocity" aria-hidden="true"></div>
          <UserNav v-if="userStore.isLogin" />
          <div class="yg-layout__main">
            <header :class="['yg-layout__topbar', 'yg-topbar', { 'is-portrait': verticalMode }]">
              <div class="yg-topbar__left">
                <img class="yg-topbar__logo" src="/pwa-192x192.png" alt="义工平台 Logo" />
                <span
                  class="yg-topbar__title"
                  @dblclick="router.push(userStore.isLogin ? '/user' : '/user/login')"
                >
                  {{ headerStore.header }}
                </span>
              </div>
              <div class="yg-topbar__right" v-if="userStore.isLogin">
                <div class="yg-user-chip">
                  <span class="yg-user-chip__avatar">
                    <ElIcon><User /></ElIcon>
                  </span>
                  <span class="yg-user-chip__name">{{ userStore.name }}</span>
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
                        class="yg-user-chip__toggle"
                        :aria-label="t('nav.accountMenu', '账号菜单')"
                      >
                        <ElIcon><ArrowDown /></ElIcon>
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
                </div>
              </div>
            </header>
            <main :class="['yg-layout__content', { 'is-portrait': verticalMode && userStore.isLogin }]">
              <RouterView v-slot="{ Component }">
                <Transition name="yg-route" mode="out-in">
                  <component :is="Component" :key="viewKey" class="yg-view" />
                </Transition>
              </RouterView>
            </main>
          </div>
        </div>
      </div>
    </ElWatermark>
    <ZNotificationReminder />
    <ZPrimaryDeviceGate />
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

/* 竖屏底部 dock 让位：药丸 56px(--yg-header-height) + 离底 12px(--yg-space-3)
   + 手势/刘海安全区 + 内容自身 16px 留白(--yg-space-4)。
   抽成 --yg-content-bottom-inset 是为了让页面（如服务器页）算自身高度时引用同一份
   数值，不必各自硬编码 96px；横屏不挂 is-portrait，padding 仍是 base.css 的 24px。 */
.yg-layout__content {
  --yg-content-bottom-inset: 0px;
}

.yg-layout__content.is-portrait {
  --yg-content-bottom-inset: calc(
    var(--yg-header-height) + var(--yg-space-3) + env(safe-area-inset-bottom, 0px) +
      var(--yg-space-4)
  );
  padding-bottom: var(--yg-content-bottom-inset);
}

/* 竖屏顶栏：视觉高度仍是 56px，但用 min-height 承载顶部安全区（刘海屏），
   左右留白收紧到 12px 把宽度让给标题；用户名在窄屏会挤掉标题，故只留头像。 */
.yg-topbar.is-portrait {
  height: auto;
  min-height: calc(var(--yg-header-height) + env(safe-area-inset-top, 0px));
  padding-top: env(safe-area-inset-top, 0px);
  padding-inline: var(--yg-space-3);
  gap: var(--yg-space-2);
}

.yg-topbar.is-portrait .yg-topbar__left {
  flex: 1;
  min-width: 0;
}

/* 标题占满剩余宽度并被 ellipsis 截断；双击回首页的绑定在模板上，与样式无关 */
.yg-topbar.is-portrait .yg-topbar__title {
  max-width: 100%;
  min-width: 0;
}

.yg-topbar.is-portrait .yg-user-chip {
  gap: var(--yg-space-1);
  padding: 0 var(--yg-space-1);
}

.yg-topbar.is-portrait .yg-user-chip__name {
  display: none;
}

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
  flex-shrink: 0;
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

/* 菜单只有两项，压到 32px 行高 + 4px 内边距，整体更紧凑 */
:global(.yg-user-menu__item.el-button) {
  justify-content: flex-start;
  width: 100%;
  height: 32px;
  margin: 0;
  padding: 0 8px;
  border-radius: var(--yg-radius-sm);
  color: var(--yg-text-primary);
  font-size: var(--yg-font-size-sm);
}

:global(.yg-user-menu__item.el-button:hover),
:global(.yg-user-menu__item.el-button:focus) {
  background: var(--yg-bg-hover);
  color: var(--yg-text-primary);
}

:global(.yg-user-menu__item.el-button.yg-user-menu__item--logout),
:global(.yg-user-menu__item.el-button.yg-user-menu__item--logout:hover) {
  color: var(--yg-text-danger);
}

:global(.yg-user-menu__item.el-button.yg-user-menu__item--logout:hover) {
  background: var(--yg-color-danger-soft);
}

:global(.yg-user-menu) {
  padding: 4px;
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
