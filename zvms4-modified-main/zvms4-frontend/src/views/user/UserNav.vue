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

/* ---- 弹性滑动指示器：单段 CSS 弹簧 ----
   高亮真源仍是 route.path（乐观路径只补「点击后、路由尚未落地」的窗口期），
   这里只负责把药丸量到激活项上。位移与尺寸的弹簧交给 yg-glass-nav.css 的
   transition（--yg-dur-page + --yg-ease-spring）播放：一段动画、可被下一次
   切换直接打断，不会出现预览 + 正式两段动画来回弹。
   首帧、字体就绪与尺寸校正走 instant：同一帧内禁用过渡写入新位置、下一帧再
   交还 CSS，否则药丸会从 (0,0) 飞进来，或在缩放/重排时拖着虚影走。 */
const itemsRef = ref<HTMLElement>()
const indicator = ref({ x: 0, y: 0, w: 0, h: 0, visible: false })
const indicatorInstant = ref(false)
let instantRelease = 0

/** 首帧类校正：同一帧禁用过渡写下新位置，下一帧再交还 CSS */
function markInstant() {
  indicatorInstant.value = true
  if (instantRelease) cancelAnimationFrame(instantRelease)
  instantRelease = requestAnimationFrame(() => {
    instantRelease = 0
    indicatorInstant.value = false
  })
}

/** 真正切页：确保过渡处于开启状态，即使上一帧的校正还没解除 */
function clearInstant() {
  if (instantRelease) cancelAnimationFrame(instantRelease)
  instantRelease = 0
  indicatorInstant.value = false
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
      indicator.value = { ...indicator.value, visible: false }
    }
    return
  }

  /* 目标位置只认「已经写进 style 的那一份」：ResizeObserver 每次 observe 新目标
     都会先投递一条初始通知，而切页时正好刚 observe 了新激活的按钮——这条通知
     落在过渡起步的下一帧，量到的目标与已写入的目标完全一致。若把它当成尺寸
     变化去 request instant，就会把刚开始的弹簧掐断（药丸瞬移到终点 = 没有动画）。
     因此 instant 只在几何真的变了（字体/换行/横竖屏/缩放导致的位移或尺寸变化）
     时生效；目标没变就整段跳过，连 style 都不重写，绝不打断在途过渡。 */
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

  if (!geometryChanged) {
    /* 切页但目标没变：只需要把过渡拉回开启状态（上一帧校正可能还没解除） */
    if (!instant) clearInstant()
    return
  }

  if (instant) markInstant()
  else clearInstant()
  indicator.value = { ...next, visible: true }
}

/* transform 必须由这里输出：过渡分支与 instant 分支都写同一组 inline 值，
   高亮类挂到哪一项，药丸就落在哪一项，不存在「只改尺寸不改位移」的不同步。 */
const indicatorStyle = computed(() => ({
  width: indicator.value.w + 'px',
  height: indicator.value.h + 'px',
  transform: `translate3d(${indicator.value.x}px, ${indicator.value.y}px, 0)`,
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
     正在播放的切页过渡（判据见 updateIndicator）。 */
  resizeObserver = new ResizeObserver(() => updateIndicator(true))
  if (itemsRef.value) resizeObserver.observe(itemsRef.value)
  window.addEventListener('resize', onResize)

  /* 首帧兜底：mounted + nextTick 时字体、Vite 异步注入的 yg-glass-nav.css、ElIcon
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
  if (instantRelease) cancelAnimationFrame(instantRelease)
  instantRelease = 0
  window.clearTimeout(spinTimer)
  spinTimer = 0
})

const navSignature = computed(() =>
  visibleNavs.value.map((nav) => `${nav.name}:${nav.path}`).join('|')
)

watch(
  /* flush: 'post' 保证回调跑在 DOM 已经切到新高亮项之后：CSS transition 的起点
     取自元素当前的 computed 位移（含正在播放的中间态），因此不需要在 pre 时机
     抢读旧 .is-active。依赖里补上「内容签名」而非 length，长度不变但换了项
     （如 admin 的活动↔创建）同样要重算；user.position 变化会改变 show，
     进而补齐/移除导航项。 */
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
</script>

<template>
  <nav
    :class="['yg-glass-nav', verticalMode ? 'yg-glass-nav--portrait' : 'yg-glass-nav--landscape']"
    aria-label="主导航"
  >
    <div ref="itemsRef" class="yg-glass-nav__items">
      <span
        class="yg-glass-nav__indicator"
        :class="{ 'is-instant': indicatorInstant }"
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
            :class="{ 'is-active': nav.judge(highlightPath) }"
            :aria-label="t(`nav.${nav.name}`)"
            @click="routeTo(nav.path)"
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
          @click="routeTo(nav.path)"
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
