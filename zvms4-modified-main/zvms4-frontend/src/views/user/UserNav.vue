<script setup lang="ts">
import {
  Avatar,
  CirclePlusFilled,
  HomeFilled,
  Sunny,
  Moon,
  Management,
  Monitor,
  ChatLineRound,
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
    path:
      user.position.includes('admin') || user.position.includes('department')
        ? '/activity/register'
        : '/activities/mine',
    show: true,
    judge: (path) =>
      (path.startsWith('/activity') && !path.startsWith('/activity/create')) ||
      path.startsWith('/activities')
  },
  {
    icon: CirclePlusFilled,
    name: 'create',
    path: '/activity/create',
    show: user.position.includes('admin') || user.position.includes('department'),
    judge: (path) => path.startsWith('/activity/create')
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

/* ---- 弹性滑动指示器：WAAPI 弹簧，每次导航都重放 ----
   旧实现依赖 CSS transition：目标位置若与上一次写入的值相同、或测量时机早于
   导航项重渲染，位移就不会发生，表现为「切到某一项时没有弹性」。
   改成显式读当前渲染位置 → 写目标位置 → 播放 WAAPI 弹簧，动画实例每次新建，
   不受上一次状态影响，任何一项都能弹。 */
const itemsRef = ref<HTMLElement>()
const indicatorRef = ref<HTMLElement>()
const indicator = ref({ x: 0, y: 0, w: 0, h: 0, visible: false })

const SPRING = 'cubic-bezier(0.3, 1.6, 0.4, 1)'
const SPRING_MS = 460

function reduceMotion() {
  return window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false
}

/** 读取当前实际渲染出的位移（含正在播放动画的中间态），作为弹簧起点 */
function renderedTranslate(el: HTMLElement) {
  const raw = getComputedStyle(el).transform
  if (!raw || raw === 'none') {
    return { x: 0, y: 0 }
  }
  const matrix = new DOMMatrixReadOnly(raw)
  return { x: matrix.m41, y: matrix.m42 }
}

let indicatorAnimation: Animation | null = null

function playIndicator(
  from: { x: number; y: number },
  target: { x: number; y: number },
  size: { w: number; h: number }
) {
  const pill = indicatorRef.value
  if (!pill || reduceMotion()) return
  indicatorAnimation?.cancel()
  const start = {
    transform: `translate3d(${from.x}px, ${from.y}px, 0)`,
    width: `${size.w}px`,
    height: `${size.h}px`
  }
  const end = { transform: `translate3d(${target.x}px, ${target.y}px, 0)` }
  const animation = pill.animate([start, end], {
    duration: SPRING_MS,
    easing: SPRING,
    fill: 'forwards'
  })
  indicatorAnimation = animation
  /* 弹簧有过冲，动画收尾的落点并不等于目标项的真实 offset；结束后无动画校正一次，
     否则小圈会长期偏在目标项旁边几像素。cancel() 会让 finished reject，一并吞掉。 */
  animation.finished
    .then(() => {
      if (indicatorAnimation !== animation) return // 已被更晚的一次弹簧取代
      indicatorAnimation = null
      animation.cancel()
      updateIndicator(false)
    })
    .catch(() => {
      /* 被 cancel（连点、卸载）属预期，不处理 */
    })
}

/** 点击瞬间：活动项还没切换，先把指示器弹到被点的那一项 */
function previewIndicator(index: number) {
  const root = itemsRef.value
  const pill = indicatorRef.value
  const item = root?.querySelectorAll<HTMLElement>('.yg-glass-nav__item')?.[index]
  if (!pill || !item) return
  playIndicator(renderedTranslate(pill), { x: item.offsetLeft, y: item.offsetTop }, indicator.value)
}

/* 指示器只关心两个盒子的尺寸/位置：导航容器（换行、横竖屏重排）与当前高亮项
   （字体/svg 加载后尺寸变化）。window.resize 覆盖不到这些，所以用 ResizeObserver。 */
let resizeObserver: ResizeObserver | null = null
let observedActive: HTMLElement | null = null

function syncObservedActive(active?: HTMLElement | null) {
  if (!resizeObserver) return
  const next =
    active ??
    itemsRef.value?.querySelector<HTMLElement>('.yg-glass-nav__item.is-active') ??
    null
  if (next === observedActive) return
  if (observedActive) resizeObserver.unobserve(observedActive)
  if (next) resizeObserver.observe(next)
  observedActive = next
}

function updateIndicator(animate = true) {
  const root = itemsRef.value
  const pill = indicatorRef.value
  if (!root || !pill) return
  const active = root.querySelector<HTMLElement>('.yg-glass-nav__item.is-active')
  syncObservedActive(active)

  /* 无激活项（路径不在任何 nav 的 judge 内）或是隐藏项
     （宽度为 0，常见于首帧样式还没注入）时统一隐藏，避免残留上一次的位置。 */
  if (!active || active.offsetWidth === 0) {
    indicatorAnimation?.cancel()
    indicatorAnimation = null
    indicator.value = { ...indicator.value, visible: false }
    return
  }
  const target = { x: active.offsetLeft, y: active.offsetTop }
  if (!animate || reduceMotion()) {
    indicatorAnimation?.cancel()
    indicatorAnimation = null
    indicator.value = {
      ...target,
      w: active.offsetWidth,
      h: active.offsetHeight,
      visible: true
    }
    return
  }
  const from = renderedTranslate(pill)
  const size = { w: indicator.value.w, h: indicator.value.h }
  // 先落位（含尺寸），随后用 fill: forwards 的弹簧动画覆盖 transform
  indicator.value = {
    ...target,
    w: active.offsetWidth,
    h: active.offsetHeight,
    visible: true
  }

  const same = Math.abs(from.x - target.x) < 0.5 && Math.abs(from.y - target.y) < 0.5
  if (same) return
  playIndicator(from, target, size)
}

/* transform 必须由这里输出：旧实现只在弹簧分支里写 inline transform，
   非动画分支（首帧、ResizeObserver 校正）只改了 width/height/opacity，
   于是小圈留在 CSS 的 left:0/top:0，而高亮类已经挂到正确项上——即「不同步」。
   动画播放期间 WAAPI 的动画优先级高于 inline style，不会互相抢值。 */
const indicatorStyle = computed(() => ({
  width: indicator.value.w + 'px',
  height: indicator.value.h + 'px',
  transform: `translate3d(${indicator.value.x}px, ${indicator.value.y}px, 0)`,
  opacity: indicator.value.visible ? 1 : 0
}))

/** 点击即给图标一次挤压回弹，连点同一项也有反馈 */
function pulseIcon(event: MouseEvent) {
  if (reduceMotion()) return
  const icon = (event.currentTarget as HTMLElement | null)?.querySelector<HTMLElement>('.el-icon')
  if (!icon) return
  icon.animate(
    [{ transform: 'scale(1)' }, { transform: 'scale(1.16)' }, { transform: 'scale(1)' }],
    { duration: 400, easing: 'cubic-bezier(0.22, 1.6, 0.36, 1)' }
  )
}

function handleNavClick(page: string, event: MouseEvent, index: number) {
  pulseIcon(event)
  previewIndicator(index)
  routeTo(page)
}

function onResize() {
  updateIndicator(false)
}

let rafHandles: number[] = []
let disposed = false

onMounted(() => {
  resizeObserver = new ResizeObserver(() => updateIndicator(false))
  if (itemsRef.value) resizeObserver.observe(itemsRef.value)
  window.addEventListener('resize', onResize)

  /* 首帧兜底：mounted + nextTick 时字体、Vite 异步注入的 yg-glass-nav.css、ElIcon
     里的 svg 都可能还没参与布局，offset* 会读到 0，小圈就贴在左上角。这里连续两帧、
     以及字体就绪后再各校一次（字体变化会改变图标/项宽，位置会整体平移）。 */
  rafHandles = [
    requestAnimationFrame(() => {
      rafHandles.push(requestAnimationFrame(() => updateIndicator(false)))
    })
  ]
  document.fonts?.ready
    .then(() => {
      if (!disposed) updateIndicator(false)
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
  indicatorAnimation?.cancel()
  indicatorAnimation = null
})

const navSignature = computed(() =>
  visibleNavs.value.map((nav) => `${nav.name}:${nav.path}`).join('|')
)

watch(
  /* flush: 'post' 保证回调跑在 DOM 已经切到新高亮项之后：
     弹簧起点来自 indicator 自身正在渲染的 transform（renderedTranslate），不依赖
     旧 .is-active 还挂在哪个元素上，所以不需要 pre 时机的取巧。
     依赖里补上「内容签名」而非 length，长度不变但换了项（如 admin 的活动↔创建）
     同样要重算；user.position 变化会改变 show，进而补齐/移除导航项。 */
  [highlightPath, verticalMode, navSignature, () => user.position.join('|')],
  () => updateIndicator(true),
  { flush: 'post' }
)

/* ---- 深浅色切换：图标旋转一圈 + View Transitions 整屏丝滑过渡 ---- */
const spinning = ref(false)

function toggleDark() {
  if (spinning.value) return
  spinning.value = true
  setTimeout(() => (spinning.value = false), 650)
  const apply = () => {
    dark.value = !dark.value
  }
  if (document.startViewTransition) {
    document.startViewTransition(apply)
  } else {
    apply()
  }
}

const showThemeToggle = !pad() || getTabletType() === 'p615' || getTabletType() === 'p620'
</script>

<template>
  <nav
    :class="['yg-glass-nav', verticalMode ? 'yg-glass-nav--portrait' : 'yg-glass-nav--landscape']"
    aria-label="主导航"
  >
    <div ref="itemsRef" class="yg-glass-nav__items">
      <span ref="indicatorRef" class="yg-glass-nav__indicator" :style="indicatorStyle" aria-hidden="true" />
      <template v-for="(nav, index) in visibleNavs" :key="nav.path">
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
            :class="{ 'is-active': nav.judge(highlightPath) }"
            :aria-label="t(`nav.${nav.name}`)"
            @click="handleNavClick(nav.path, $event, index)"
          >
            <ElIcon><component :is="nav.icon" /></ElIcon>
          </button>
        </ElTooltip>
        <button
          v-else
          type="button"
          class="yg-glass-nav__item"
          :class="{ 'is-active': nav.judge(highlightPath) }"
          :aria-label="t(`nav.${nav.name}`)"
          @click="handleNavClick(nav.path, $event, index)"
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
        <span class="yg-glass-nav__theme-icon" :class="{ spinning }">
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
