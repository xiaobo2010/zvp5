<script setup lang="ts">
// 深路径 import：entry 静态链一旦触碰 element-plus barrel，全部已用组件
//（含懒加载路由独占的重组件）都会被提升进首屏 chunk；深路径只拉本组件闭包。
import { ElIcon } from 'element-plus/es/components/icon/index.mjs'
import { ElConfigProvider } from 'element-plus/es/components/config-provider/index.mjs'
import { ElMessageBox } from 'element-plus/es/components/message-box/index.mjs'
import { ElWatermark } from 'element-plus/es/components/watermark/index.mjs'

import { RouterView } from 'vue-router'
import { useUserStore } from '@/stores/user'
import { useRouter, useRoute } from 'vue-router'
import UserNav from '@/views/user-v2/UserNav.vue'
import { useHeaderStore } from './stores/header'
import { useWindowSize, useDark, useOnline, usePreferredDark } from '@vueuse/core'
import { useI18n } from 'vue-i18n'
import { computed, watch, ref, onMounted, onUnmounted, h, reactive } from 'vue'
import type { WatermarkFontType } from 'element-plus'
import { zhCn } from 'element-plus/es/locale/index.mjs'
import ZNotificationReminder from '@/components/notification/ZNotificationReminder.vue'
import ZPrimaryDeviceGate from '@/components/notification/ZPrimaryDeviceGate.vue'
import ZLoginReveal from '@/components/system/ZLoginReveal.vue'
import ZBanScreen from '@/components/system/ZBanScreen.vue'
import ZHexGrid from '@/components/decor/ZHexGrid.vue'
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
import { loginRoleKey } from '@/utils/loginRole'
import { installScrollActivityFlag, uninstallScrollActivityFlag } from '@/utils/scrollActivity'
import { warmupFontsWhenIdle } from '@/utils/routePrefetch'
import { prefetchLoginHome, resetLoginHomePrefetch } from '@/utils/homePrefetch'
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

// 退出/右键都走 addEventListener：直接占用 window.onbeforeunload / oncontextmenu
// 会顶掉挂在同一槽位上的其它监听，且卸载时无痕可清。
function handleBeforeUnload(e: BeforeUnloadEvent) {
  if (location.pathname.startsWith('/activity/create/') || location.pathname.endsWith('/modify')) {
    e.preventDefault()
    // 兼容旧浏览器：与原先 return false 的语义一致
    e.returnValue = ''
  }
}

function handleContextMenu(e: MouseEvent) {
  e.preventDefault()
}

window.addEventListener('beforeunload', handleBeforeUnload)
window.addEventListener('contextmenu', handleContextMenu)

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

// 登录揭示：登录成功瞬间就挂一层不透明覆盖，先挡住 nav/topbar；
// 等路由真正离开登录页后再启动 2.8s 时间线。这样慢接口只表现为“幕布等待”，
// 不会先闪一下导航。等待超过 6s 兜底撤掉，避免卡死。
const loginRevealPending = ref(false)
const loginRevealVisible = ref(false)
const loginRevealStarted = ref(false)
const loginRevealRun = ref(0)
const loginRevealName = ref('')
const loginRevealRole = ref('')
let loginCoverTimer: number | undefined

function clearLoginCoverTimer() {
  if (loginCoverTimer !== undefined) {
    window.clearTimeout(loginCoverTimer)
    loginCoverTimer = undefined
  }
}

function finishLoginReveal() {
  clearLoginCoverTimer()
  loginRevealPending.value = false
  loginRevealVisible.value = false
  loginRevealStarted.value = false
  delete document.documentElement.dataset.loginReveal
}

function beginLoginCover() {
  if (loginRevealVisible.value) {
    return
  }
  loginRevealName.value = userStore.name
  loginRevealRole.value = t(`home.positions.${loginRoleKey(userStore.position)}`)
  loginRevealRun.value += 1
  loginRevealStarted.value = false
  loginRevealVisible.value = true
  document.documentElement.dataset.loginReveal = '1'
  // 首页首屏依赖立即发；不要把时长/群组/字体排到 idle，揭示一结束就要能看见。
  prefetchLoginHome(router)
  clearLoginCoverTimer()
  loginCoverTimer = window.setTimeout(() => {
    if (!loginRevealStarted.value) {
      finishLoginReveal()
    }
  }, 6000)
}

function startLoginReveal() {
  if (!loginRevealVisible.value) {
    return
  }
  clearLoginCoverTimer()
  loginRevealStarted.value = true
}

watch(
  () => userStore.isLogin,
  (logged) => {
    if (!logged) {
      resetLoginHomePrefetch()
      finishLoginReveal()
      return
    }
    loginRevealPending.value = true
    beginLoginCover()
    // 极端时序下路由 watch 可能先于登录 watch 触发（同一 tick 的 replace）。
    if (!route.path.endsWith('login') && !route.path.endsWith('maintenance')) {
      startLoginReveal()
    }
  }
)

watch(
  () => route.path,
  () => {
    if (!userStore.isLogin) {
      return
    }
    if (route.path.endsWith('login')) {
      return
    }
    if (route.path.endsWith('maintenance')) {
      finishLoginReveal()
      return
    }
    if (!loginRevealPending.value && !loginRevealVisible.value) {
      return
    }
    if (!loginRevealVisible.value) {
      beginLoginCover()
    }
    loginRevealPending.value = false
    startLoginReveal()
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
const BAN_NOTICE_SECONDS = 15
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
    // 跳转失败保持状态(fail-closed)，短延迟重试，避免粒子已消散后留下空遮罩。
    window.setTimeout(() => {
      if (userStore.bannedNoticeAt > 0) {
        void finalizeBan()
      }
    }, 600)
  }
  banFinalizing.value = false
}

function updateBanCountdown() {
  if (userStore.bannedNoticeAt <= 0) {
    return
  }
  const left = BAN_NOTICE_SECONDS - (Date.now() - userStore.bannedNoticeAt) / 1000
  banCountdown.value = Math.max(0, Math.ceil(left))
  // 到点后由 ZBanScreen 先播完粒子消散，再 emit ack 调 finalizeBan；
  // 这里不能提前卸载遮罩，否则粒子动画会被硬切掉。
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
  clearLoginCoverTimer()
  delete document.documentElement.dataset.loginReveal
  window.removeEventListener('beforeunload', handleBeforeUnload)
  window.removeEventListener('contextmenu', handleContextMenu)
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

// 服务端/管理台已在各自外壳内挂蜂窝底纹；其余 Phi 页面与独立通知页由 App 统一补一层。
// 登录页有自己的缓存背景，不重复叠。
const hasLocalHexGrid = computed(() => {
  const path = route.path
  return (
    path === '/server' ||
    path.startsWith('/server/') ||
    path === '/manage' ||
    path.startsWith('/manage/')
  )
})

const showPageHex = computed(
  () =>
    (isPhiRoute.value || route.path === '/notifications') &&
    !hasLocalHexGrid.value &&
    route.path !== '/user/login'
)

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
  // 学生详情头部滑块（info/activity/modify/logs）只切路由末段，外壳必须常驻；
  // 按学生 id 归族，既避免每次点滑块整页重挂载，也不会和 `/user` 首页撞 key。
  if (name === 'user-page' || name === 'user-action') {
    return `/user/${route.params.id ?? ''}`
  }
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
  // 平板滚动流畅性：滚动进行中给 <html> 打 data-scrolling，
  // perf-tablet.css 据此暂停纯装饰动画（详见 utils/scrollActivity.ts）。
  installScrollActivityFlag()
  // phi 显示字体（Bebas/Rajdhani，font-display: swap）空闲预热：
  // 提前于登录揭示动画与首页大标题首次绘制，消掉 FOUT 文本跳动。
  warmupFontsWhenIdle(['400 1em Bebas', '500 1em Rajdhani', '700 1em Rajdhani'])
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

onUnmounted(() => {
  uninstallScrollActivityFlag()
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
        <ZBanScreen
          v-if="banLocked"
          variant="account"
          :title="t('nav.banned.title')"
          :reason="banReasonText"
          :until="banUntilText"
          :appeal="t('nav.banned.appeal')"
          :countdown="banCountdown"
          :countdown-total="BAN_NOTICE_SECONDS"
          :countdown-text="banCountdown > 0 ? t('nav.banned.autoLogoutIn', { seconds: banCountdown }) : ''"
          :auto-dismiss="banCountdown"
          :lock-scroll="false"
          @ack="finalizeBan"
        />
        <div :class="['yg-layout', 'yg-layout--app', { 'yg-phi': isPhiRoute }]">
          <UserNav v-if="userStore.isLogin" />
          <div class="yg-layout__main">
            <main :class="['yg-layout__content', { 'is-portrait': verticalMode && userStore.isLogin }]">
              <!-- 全局蜂窝底纹：服务端/管理台已有自己的底纹，其余页面统一补上 -->
              <div v-if="showPageHex" class="yg-page-hex" aria-hidden="true">
                <ZHexGrid
                  :size="64"
                  color="var(--yg-text-primary)"
                  :opacity="0.07"
                  drift
                  sweep
                />
              </div>
              <!-- 速度线挂在滚动内容里，随页面上下滚动；不再 fixed 浮在视口上。 -->
              <div v-if="isPhiRoute" class="yg-phi-velocity" aria-hidden="true"></div>
              <div v-if="isPhiRoute" class="yg-phi-velocity" aria-hidden="true"></div>
              <div v-if="isPhiRoute" class="yg-phi-velocity" aria-hidden="true"></div>
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
    <ZLoginReveal
      v-if="loginRevealVisible"
      :key="loginRevealRun"
      :name="loginRevealName"
      :role="loginRevealRole"
      :active="loginRevealStarted"
      @done="finishLoginReveal"
    />
  </ElConfigProvider>
</template>

<style scoped>
.full {
  width: 100%;
  height: 100%;
}

/* 全局蜂窝底纹：固定在视口上，负 z-index 落在内容区底色之上、卡片之下。
   内容区自身有 isolation + overflow，这里必须 fixed 才不会被滚动内容带跑。 */
.yg-page-hex {
  position: fixed;
  inset: 0;
  z-index: -1;
  overflow: hidden;
  pointer-events: none;
  contain: layout paint style;
}

.disconnected {
  position: relative;
  top: 40%;
  transform: translateY(16%);
  font-size: 1.2rem;
}

/* 竖屏底部 dock（UserNav 悬浮药丸）让位：药丸 56px(--yg-header-height 令牌同值)
   + 离底 12px(--yg-space-3) + 手势/刘海安全区 + 内容自身 16px 留白(--yg-space-4)。
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
