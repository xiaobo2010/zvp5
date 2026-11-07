<script setup lang="ts">
import {
  Avatar,
  HomeFilled,
  Sunny,
  Moon,
  Management,
  Monitor,
  ChatLineRound,
  EditPen
} from '@element-plus/icons-vue'
import { ElTooltip, ElIcon } from 'element-plus'
import { ref, computed, watch, onMounted, onUnmounted } from 'vue'
import { useUserStore } from '@/stores/user'
import MdiEye from '@/icons/MdiEye.vue'
import type { Component as VueComponent } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useDark, useWindowSize } from '@vueuse/core'
import { useI18n } from 'vue-i18n'
import { pad, getTabletType } from '@/plugins/ua'

const user = useUserStore()
const router = useRouter()
const route = useRoute()
const dark = useDark()
const { t } = useI18n({
  useScope: 'global'
})

/* 高亮的唯一真源是 route.path（不含 query/hash）：judge 内部用 startsWith 比较，
   之前传 fullPath 时 `/user?tab=1`、`/me#section` 这类地址会判不中，高亮整项丢失。
   这里只保留一个「点击后、路由尚未落地」窗口期的乐观值，路由一变立刻让位给真实路径。 */
const optimisticPath = ref<string | null>(null)
const highlightPath = computed(() => optimisticPath.value ?? route.path)

watch(
  () => route.path,
  () => {
    optimisticPath.value = null
  }
)

// 与 App.vue 保持同一判定：宽 < 高 × 1.2 即竖屏
const { width, height } = useWindowSize()
const verticalMode = computed(() => width.value < height.value * 1.2)

interface NavItem {
  icon: VueComponent
  name: string
  path: string
  show: boolean
  judge: (path: string) => boolean
}

/* 用 computed 而不是静态数组：show 依赖 user.position，而 position 是登录后由
   App.vue 的 refreshUser() 异步拉回来的；静态数组会把首帧那份快照永久固化，
   导致管理/服务器/AI 项永远补不齐，指示器自然找不到对应的高亮项。 */
const navs = computed<NavItem[]>(() => [
  {
    icon: HomeFilled,
    name: 'home',
    path: '/user',
    show: true,
    judge: (path) => path === '/user'
  },
  {
    icon: MdiEye,
    name: 'activity',
    path: '/activities/mine',
    show: true,
    judge: (path) =>
      (path.startsWith('/activity') &&
        !path.startsWith('/activity/create') &&
        !path.startsWith('/activity/register')) ||
      path.startsWith('/activities')
  },
  {
    icon: EditPen,
    name: 'register',
    path: '/activity/register',
    show: user.position.includes('admin') || user.position.includes('department'),
    /* 添加/修改义工共用一个选择入口；编辑子路由也保持同一条导航高亮。 */
    judge: (path) => path.startsWith('/activity/register')
  },
  {
    icon: Management,
    name: 'manage',
    path: '/management',
    show: user.position.filter((x) => x !== 'student').length > 0,
    judge: (path) =>
      path.startsWith('/group') ||
      (path.startsWith('/user/') && path !== '/user/login') ||
      path.startsWith('/manage')
  },
  {
    icon: Avatar,
    name: 'profile',
    path: '/me',
    show: true,
    judge: (path) => path.startsWith('/me')
  },
  {
    icon: Monitor,
    name: 'server',
    path: '/server',
    show: user.position.includes('admin'),
    judge: (path) => path.startsWith('/server')
  },
  {
    icon: ChatLineRound,
    name: 'ai',
    path: '/ai/chat',
    show: user.position.includes('admin') || user.position.includes('department'),
    judge: (path) => path.startsWith('/ai')
  }
])

const visibleNavs = computed(() => navs.value.filter((x) => x.show))

function routeTo(page: string) {
  if (page === '/management') {
    if (user.position.includes('admin') || user.position.includes('department')) {
      routeTo('/manage/groups')
    } else {
      routeTo(`/group/${user.class_id}/users`)
    }
  } else {
    optimisticPath.value = page
    router.push(page)
  }
}

/* ---- 弹性滑动指示器：显式弹簧动画（WAAPI），不依赖 CSS transition ----
   高亮真源仍是 route.path（乐观路径只补「点击后、路由尚未落地」的窗口期），
   这里只负责把药丸量到激活项上并播放一段弹簧。

   为什么不用 CSS transition：实测切到 /me 时，页面挂载过程中的样式重算会把在途的
   transform transition 直接取消（transitioncancel + 瞬间落到终点），表现正是
   「第一次有动画、之后再点没有」。改为每次高亮变化都显式读当前渲染位移、新建一个
   Web Animation：动画实例独立于样式重算，只有下一次切换才会主动取消它，任何导航项
   都能稳定播放。时长/曲线仍取 --yg-dur-page / --yg-ease-spring，减少动效时不播放。
   首帧、字体就绪与尺寸校正走 instant：取消动画并直接写入位置，避免药丸从 (0,0) 飞入。 */
const itemsRef = ref<HTMLElement>()
const pillRef = ref<HTMLElement>()
const indicator = ref({ x: 0, y: 0, w: 0, h: 0, visible: false })
let indicatorAnimation: Animation | null = null

/** 与 --yg-* 令牌同源；读不到就退回内置默认值，不能让动画因令牌缺失而消失 */
function tokenMs(name: string, fallback: number): number {
  const raw = getComputedStyle(document.documentElement).getPropertyValue(name).trim()
  const value = Number.parseFloat(raw)
  if (!Number.isFinite(value) || value <= 0) return fallback
  if (raw.endsWith('ms')) return value
  if (raw.endsWith('s')) return value * 1000
  return fallback
}

function tokenEasing(name: string, fallback: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback
}

/** 读取当前「已经渲染出来的」位移：动画进行中也能拿到中间态，作为下一次弹簧的起点 */
function renderedTranslate(el: HTMLElement): { x: number; y: number } {
  const raw = getComputedStyle(el).transform
  if (!raw || raw === 'none') return { x: 0, y: 0 }
  /* 真实浏览器给的是 matrix(...)；测试/降级环境可能原样返回 translate3d(...) */
  const translateBody = /^translate3?d?\(([^)]+)\)/.exec(raw)?.[1]
  if (translateBody) {
    const [x, y] = translateBody.split(',').map((part) => Number.parseFloat(part))
    return { x: Number.isFinite(x) ? x : 0, y: Number.isFinite(y) ? y : 0 }
  }
  const body = /matrix3?d?\(([^)]+)\)/.exec(raw)?.[1]
  if (!body) return { x: 0, y: 0 }
  const parts = body.split(',').map((part) => Number.parseFloat(part))
  const [x, y] = raw.startsWith('matrix3d')
    ? [parts[12] ?? 0, parts[13] ?? 0]
    : [parts[4] ?? 0, parts[5] ?? 0]
  return { x: Number.isFinite(x) ? x : 0, y: Number.isFinite(y) ? y : 0 }
}

function translate(x: number, y: number): string {
  return `translate3d(${x}px, ${y}px, 0)`
}

function stopIndicatorAnimation() {
  indicatorAnimation?.cancel()
  indicatorAnimation = null
}

/** 从当前渲染位置弹簧到目标项；目标没变就不打扰在途动画 */
function playIndicator(target: { x: number; y: number }) {
  const pill = pillRef.value
  /* jsdom / 老浏览器没有 WAAPI，或用户在系统里关掉了动效：直接落到终点 */
  if (!pill) return
  if (reduceMotion() || typeof pill.animate !== 'function') {
    stopIndicatorAnimation()
    return
  }

  const from = renderedTranslate(pill)
  if (Math.abs(from.x - target.x) < 0.5 && Math.abs(from.y - target.y) < 0.5) return

  stopIndicatorAnimation()
  /* 先以左端为锚点收缩，再弹到目标项；目标项同时由 CSS 向外突出并带阴影。 */
  const midX = from.x + (target.x - from.x) * 0.34
  const midY = from.y + (target.y - from.y) * 0.34
  const animation = pill.animate(
    [
      {
        transform: `${translate(from.x, from.y)} scaleX(1)`,
        transformOrigin: '0 50%',
        offset: 0
      },
      {
        transform: `${translate(midX, midY)} scaleX(0.58)`,
        transformOrigin: '0 50%',
        offset: 0.3
      },
      {
        transform: `${translate(target.x, target.y)} scaleX(1)`,
        transformOrigin: '0 50%',
        offset: 1
      }
    ],
    {
      duration: tokenMs('--yg-dur-page', 450),
      easing: tokenEasing('--yg-ease-spring', 'cubic-bezier(0.34, 1.56, 0.64, 1)'),
      fill: 'forwards'
    }
  )
  indicatorAnimation = animation
  animation.finished.then(
    () => {
      /* 已被更晚的一次切换取代时不要动它 */
      if (indicatorAnimation !== animation) return
      indicatorAnimation = null
      /* 交还给行内样式（此时已等于终点），避免动画长期驻留 */
      animation.cancel()
    },
    () => {
      /* 被下一次切页取消属预期 */
    }
  )
}

/* 指示器只关心两个盒子的尺寸/位置：导航容器（换行、横竖屏重排）与当前高亮项
   （字体/svg 加载后尺寸变化）。window.resize 覆盖不到这些，所以用 ResizeObserver。 */
let resizeObserver: ResizeObserver | null = null
let observedActive: HTMLElement | null = null

function syncObservedActive(active?: HTMLElement | null) {
  if (!resizeObserver) return
  const next =
    active ?? itemsRef.value?.querySelector<HTMLElement>('.yg-glass-nav__item.is-active') ?? null
  if (next === observedActive) return
  if (observedActive) resizeObserver.unobserve(observedActive)
  if (next) resizeObserver.observe(next)
  observedActive = next
}

function updateIndicator(instant = false) {
  const root = itemsRef.value
  if (!root) return
  const active = root.querySelector<HTMLElement>('.yg-glass-nav__item.is-active')
  syncObservedActive(active)

  /* 无激活项（路径不在任何 nav 的 judge 内）或是隐藏项
     （宽度为 0，常见于首帧样式还没注入）时统一隐藏，避免残留上一次的位置。 */
  if (!active || active.offsetWidth === 0) {
    if (indicator.value.visible) {
      stopIndicatorAnimation()
      indicator.value = { ...indicator.value, visible: false }
    }
    return
  }

  /* ResizeObserver 每次 observe 新目标都会先投递一条初始通知，量到的位置与刚写入的
     目标完全一致。目标没变就整段跳过，绝不取消在途弹簧。 */
  const prev = indicator.value
  const next = {
    x: active.offsetLeft,
    y: active.offsetTop,
    w: active.offsetWidth,
    h: active.offsetHeight
  }
  const geometryChanged =
    !prev.visible ||
    prev.x !== next.x ||
    prev.y !== next.y ||
    prev.w !== next.w ||
    prev.h !== next.h

  if (!geometryChanged) return

  indicator.value = { ...next, visible: true }
  if (instant) {
    stopIndicatorAnimation()
    return
  }
  playIndicator(next)
}

/* 尺寸/位置始终由这里输出；动画只覆写 transform 的中间帧，终点与行内样式一致，
   高亮类挂到哪一项，药丸就落在哪一项。 */
const indicatorStyle = computed(() => ({
  width: indicator.value.w + 'px',
  height: indicator.value.h + 'px',
  transform: translate(indicator.value.x, indicator.value.y),
  opacity: indicator.value.visible ? 1 : 0
}))

function onResize() {
  updateIndicator(true)
}

let rafHandles: number[] = []
let disposed = false

onMounted(() => {
  /* 这里统一传 instant：真正的尺寸变化（换行、横竖屏、字体、缩放）即时校正；
     observe 新激活项带来的那条初始通知会因为「几何没变」被判据吸收，不会打断
     正在播放的弹簧（判据见 updateIndicator）。 */
  resizeObserver = new ResizeObserver(() => updateIndicator(true))
  if (itemsRef.value) resizeObserver.observe(itemsRef.value)
  window.addEventListener('resize', onResize)

  /* 首帧兜底：mounted 后连续两帧时字体、Vite 异步注入的 yg-glass-nav.css、ElIcon
     里的 svg 都可能还没参与布局，offset* 会读到 0，小圈就贴在左上角。这里连续两帧、
     以及字体就绪后再各校一次（字体变化会改变图标/项宽，位置会整体平移）。 */
  rafHandles = [
    requestAnimationFrame(() => {
      rafHandles.push(requestAnimationFrame(() => updateIndicator(true)))
    })
  ]
  document.fonts?.ready
    .then(() => {
      if (!disposed) updateIndicator(true)
    })
    .catch(() => {
      /* 字体加载失败不影响导航，忽略 */
    })
})

onUnmounted(() => {
  disposed = true
  rafHandles.forEach((handle) => cancelAnimationFrame(handle))
  rafHandles = []
  resizeObserver?.disconnect()
  resizeObserver = null
  observedActive = null
  window.removeEventListener('resize', onResize)
  stopIndicatorAnimation()
  window.clearTimeout(spinTimer)
  spinTimer = 0
  window.clearTimeout(protrudeTimer)
  protrudeTimer = 0
})

const navSignature = computed(() =>
  visibleNavs.value.map((nav) => `${nav.name}:${nav.path}`).join('|')
)

watch(
  /* flush: 'post' 保证回调跑在 DOM 已经切到新高亮项之后：弹簧的起点取自元素当前
     的 computed 位移（含正在播放的中间态），因此不需要在 pre 时机抢读旧 .is-active。
     依赖里补上「内容签名」而非 length，长度不变但换了项（如 admin 的活动↔创建）
     同样要重算；user.position 变化会改变 show，进而补齐/移除导航项。 */
  [highlightPath, verticalMode, navSignature, () => user.position.join('|')],
  () => updateIndicator(),
  { flush: 'post' }
)

/* ---- 深浅色切换：图标旋转一圈 + View Transitions 整屏丝滑过渡 ---- */
const spinning = ref(false)
let spinTimer = 0

function reduceMotion() {
  return window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false
}

/** 与 --yg-dur-page 同源；读不到令牌就返回 0（宁可少拦一次点击，也不能把开关锁死） */
function themeSpinMs() {
  const raw = getComputedStyle(document.documentElement).getPropertyValue('--yg-dur-page').trim()
  const value = Number.parseFloat(raw)
  if (!Number.isFinite(value)) return 0
  if (raw.endsWith('ms')) return value
  if (raw.endsWith('s')) return value * 1000
  return 0
}

/** 解锁旋转态：animationend、快照过渡结束、令牌兜底三路都汇到这里，重复调用无副作用 */
function endThemeSpin() {
  window.clearTimeout(spinTimer)
  spinTimer = 0
  spinning.value = false
}

function toggleDark() {
  if (spinning.value) return
  const apply = () => {
    dark.value = !dark.value
  }

  /* 减少动效：base.css 把 animation-duration 压到 0.01ms，animationend 不再保证派发，
     此时挂上 spinning 会等不到解锁、把主题开关永久锁死。这里干脆不挂旋转态，直接
     换肤（整屏快照过渡已由 yg-glass-nav.css 的 reduce 块降级为瞬时）。 */
  if (reduceMotion()) {
    apply()
    return
  }

  spinning.value = true
  if (document.startViewTransition) {
    /* 第二路解锁：快照过渡结束（含被跳过/中止）时也释放，与 animationend 谁先到都行 */
    document.startViewTransition(apply).finished.then(endThemeSpin, endThemeSpin)
  } else {
    apply()
  }
  /* 兜底：动画事件可能被系统策略吞掉，这里按同一令牌时长无条件解锁 */
  spinTimer = window.setTimeout(endThemeSpin, themeSpinMs())
}

const showThemeToggle = !pad() || getTabletType() === 'p615' || getTabletType() === 'p620'

/* 点击态：目标项向外突出；指示器同时向左收缩再展开。 */
const protrudingPath = ref('')
let protrudeTimer = 0

function handleNavClick(path: string) {
  protrudingPath.value = path
  window.clearTimeout(protrudeTimer)
  protrudeTimer = window.setTimeout(() => {
    protrudingPath.value = ''
    protrudeTimer = 0
  }, 520)
  routeTo(path)
}
</script>

<template>
  <nav
    :class="['yg-glass-nav', 'yg-glass-nav--v2', verticalMode ? 'yg-glass-nav--portrait' : 'yg-glass-nav--landscape']"
    aria-label="主导航"
  >
    <div ref="itemsRef" class="yg-glass-nav__items">
      <span
        ref="pillRef"
        class="yg-glass-nav__indicator"
        :style="indicatorStyle"
        aria-hidden="true"
      />
      <template v-for="nav in visibleNavs" :key="nav.path">
        <ElTooltip
          v-if="!verticalMode"
          :content="t(`nav.${nav.name}`)"
          placement="right"
          effect="light"
          :show-after="200"
        >
          <button
            type="button"
            class="yg-glass-nav__item"
            :class="{
              'is-active': nav.judge(highlightPath),
              'is-protruding': protrudingPath === nav.path
            }"
            :aria-label="t(`nav.${nav.name}`)"
            @click="handleNavClick(nav.path)"
          >
            <ElIcon><component :is="nav.icon" /></ElIcon>
          </button>
        </ElTooltip>
        <button
          v-else
          type="button"
          class="yg-glass-nav__item"
          :class="{
            'is-active': nav.judge(highlightPath),
            'is-protruding': protrudingPath === nav.path
          }"
          :aria-label="t(`nav.${nav.name}`)"
          @click="handleNavClick(nav.path)"
        >
          <ElIcon><component :is="nav.icon" /></ElIcon>
        </button>
      </template>
      <span v-if="showThemeToggle" class="yg-glass-nav__divider" aria-hidden="true" />
      <button
        v-if="showThemeToggle"
        type="button"
        class="yg-glass-nav__item yg-glass-nav__theme"
        :aria-label="t('nav.toggleDark', '切换深浅色模式')"
        @click="toggleDark"
      >
        <span
          class="yg-glass-nav__theme-icon"
          :class="{ spinning }"
          @animationend="endThemeSpin"
          @animationcancel="endThemeSpin"
        >
          <ElIcon><component :is="dark ? Moon : Sunny" /></ElIcon>
        </span>
      </button>
    </div>
  </nav>
</template>

<style scoped>
@media print {
  .yg-glass-nav {
    display: none;
  }
}
</style>
