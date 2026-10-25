<script setup lang="ts">
import {
  Avatar,
  CirclePlusFilled,
  EditPen,
  HomeFilled,
  InfoFilled,
  Sunny,
  Moon,
  Management,
  Monitor,
  ChatLineRound
} from '@element-plus/icons-vue'
import { ElTooltip, ElIcon } from 'element-plus'
import { ref, computed, watch, onMounted, onUnmounted, nextTick } from 'vue'
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

const path = ref(route.fullPath)

watch(route, () => {
  path.value = route.fullPath
})

// 与 App.vue 保持同一判定：宽 < 高 × 1.2 即竖屏
const { width, height } = useWindowSize()
const verticalMode = computed(() => width.value < height.value * 1.2)

const navs: Array<{
  icon: VueComponent
  name: string
  path: string
  show: boolean
  judge: (path: string) => boolean
}> = [
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
    icon: CirclePlusFilled,
    name: 'create',
    path: '/activity/create',
    show: user.position.includes('admin') || user.position.includes('department'),
    judge: (path) => path.startsWith('/activity/create')
  },
  {
    icon: EditPen,
    name: 'register',
    path: '/activity/register',
    show: user.position.includes('admin') || user.position.includes('department'),
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
    icon: InfoFilled,
    name: 'about',
    path: '/about',
    show: true,
    judge: (path) => path.startsWith('/about')
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
]

const visibleNavs = computed(() => navs.filter((x) => x.show))

function routeTo(page: string) {
  if (page === '/management') {
    if (user.position.includes('admin') || user.position.includes('department')) {
      routeTo('/manage/groups')
    } else {
      routeTo(`/group/${user.class_id}/users`)
    }
  } else {
    path.value = page
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

function updateIndicator(animate = true) {
  const root = itemsRef.value
  const pill = indicatorRef.value
  if (!root || !pill) return
  const active = root.querySelector<HTMLElement>('.yg-glass-nav__item.is-active')
  if (!active) {
    indicator.value = { ...indicator.value, visible: false }
    return
  }
  const target = { x: active.offsetLeft, y: active.offsetTop }
  indicator.value = {
    ...target,
    w: active.offsetWidth,
    h: active.offsetHeight,
    visible: true
  }
  const from = renderedTranslate(pill)
  pill.style.transform = `translate3d(${target.x}px, ${target.y}px, 0)`

  const same = Math.abs(from.x - target.x) < 0.5 && Math.abs(from.y - target.y) < 0.5
  if (!animate || same || reduceMotion()) return

  indicatorAnimation?.cancel()
  indicatorAnimation = pill.animate(
    [
      { transform: `translate3d(${from.x}px, ${from.y}px, 0)` },
      { transform: `translate3d(${target.x}px, ${target.y}px, 0)` }
    ],
    { duration: SPRING_MS, easing: SPRING }
  )
}

const indicatorStyle = computed(() => ({
  width: indicator.value.w + 'px',
  height: indicator.value.h + 'px',
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

function handleNavClick(page: string, event: MouseEvent) {
  pulseIcon(event)
  routeTo(page)
}

function onResize() {
  updateIndicator(true)
}

onMounted(() => {
  nextTick(() => updateIndicator(false))
  window.addEventListener('resize', onResize)
})

onUnmounted(() => {
  window.removeEventListener('resize', onResize)
  indicatorAnimation?.cancel()
  indicatorAnimation = null
})

watch(
  [path, verticalMode, () => visibleNavs.value.length],
  () => nextTick(() => updateIndicator(true))
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
            :class="{ 'is-active': nav.judge(path ?? '') }"
            :aria-label="t(`nav.${nav.name}`)"
            @click="handleNavClick(nav.path, $event)"
          >
            <ElIcon><component :is="nav.icon" /></ElIcon>
          </button>
        </ElTooltip>
        <button
          v-else
          type="button"
          class="yg-glass-nav__item"
          :class="{ 'is-active': nav.judge(path ?? '') }"
          :aria-label="t(`nav.${nav.name}`)"
          @click="handleNavClick(nav.path, $event)"
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
