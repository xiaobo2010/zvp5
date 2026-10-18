<script setup lang="ts">
/**
 * UserNav —— 悬浮式液态玻璃导航
 *
 * 横屏（landscape）：左侧悬浮玻璃侧栏（图标 + 文字）
 * 竖屏（portrait）：底部悬浮玻璃药丸导航条（纯图标）
 *
 * 激活态：共享的液态指示器（lg-indicator）在选项间弹性滑动，
 * 模拟 iOS 26 Liquid Glass 的"高亮团块拉伸移动"效果。
 */
import { ElTooltip } from 'element-plus'
import { ref, computed, watch, nextTick, onMounted, onUnmounted } from 'vue'
import { useUserStore } from '@/stores/user'
/* 图标统一替换为 Material Symbols Rounded（与 SF Symbols 视觉风格最接近） */
import IconHomeRounded from '~icons/material-symbols/home-rounded'
import IconAddCircleRounded from '~icons/material-symbols/add-circle-rounded'
import IconVisibilityRounded from '~icons/material-symbols/visibility-rounded'
import IconInfoRounded from '~icons/material-symbols/info-rounded'
import IconSettingsRounded from '~icons/material-symbols/settings-rounded'
import IconDesktopMacRounded from '~icons/material-symbols/desktop-mac-rounded'
import IconLightModeRounded from '~icons/material-symbols/light-mode-rounded'
import IconDarkModeRounded from '~icons/material-symbols/dark-mode-rounded'
import type { Component as VueComponent } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useDark, useWindowSize } from '@vueuse/core'
import { useI18n } from 'vue-i18n'
import { pad, getTabletType } from '@/plugins/ua'

const props = defineProps<{
  /** true = 竖屏（底部导航条）；false = 横屏（左侧悬浮侧栏） */
  portrait: boolean
}>()

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

const navs: Array<{
  icon: VueComponent
  name: string
  path: string
  show: boolean
  judge: (path: string) => boolean
}> = [
  {
    icon: IconHomeRounded,
    name: 'home',
    path: '/user',
    show: true,
    judge: (path) => path === '/user'
  },
  {
    icon: IconVisibilityRounded,
    name: 'activity',
    path: '/activities/mine',
    show: true,
    judge: (path) =>
      (path.startsWith('/activity') && !path.startsWith('/activity/create')) ||
      path.startsWith('/activities')
  },
  {
    icon: IconAddCircleRounded,
    name: 'create',
    path: '/activity/create',
    show: user.position.includes('admin') || user.position.includes('department'),
    judge: (path) => path.startsWith('/activity/create')
  },
  {
    icon: IconSettingsRounded,
    name: 'manage',
    path: '/management',
    show: user.position.filter((x) => x !== 'student').length > 0,
    judge: (path) =>
      path.startsWith('/group') || path.startsWith('/user/') && path !== '/user/login' || path.startsWith('/manage')
  },
  {
    icon: IconInfoRounded,
    name: 'about',
    path: '/about',
    show: true,
    judge: (path) => path.startsWith('/about')
  },
  {
    icon: IconDesktopMacRounded,
    name: 'server',
    path: '/server',
    show: user.position.includes('admin'),
    judge: (path) => path.startsWith('/server')
  }
]

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

const showDarkToggle = !pad() || getTabletType() === 'p615' || getTabletType() === 'p620'

/* ============ 主题切换：旋转 + 渐变 + 全屏过渡 ============ */
/** 累计旋转角度：每次切换同方向再转一整圈 */
const themeRotation = ref(0)
/** 图标显示的日月状态（View Transition 期间延后切换，保证旋转动画可见） */
const iconDark = ref(dark.value)

function toggleDark() {
  themeRotation.value += 360
  const flip = () => {
    dark.value = !dark.value
  }
  if (document.startViewTransition) {
    // 整屏快照交叉淡化：所有组件同时过渡，丝滑非线性
    const vt = document.startViewTransition(flip)
    vt.finished.then(() => {
      iconDark.value = dark.value
    })
  } else {
    // 回退：临时类让全屏颜色属性平滑过渡
    document.documentElement.classList.add('theme-transition')
    flip()
    iconDark.value = dark.value
    window.setTimeout(() => {
      document.documentElement.classList.remove('theme-transition')
    }, 650)
  }
}

/* ============ 竖屏底部栏：下滑隐藏 / 上滑或点击底部显示 ============ */
const tabbarHidden = ref(false)
let lastScrollY = 0

function onScroll() {
  if (!props.portrait) return
  const y = window.scrollY
  if (y > lastScrollY && y > 80) {
    // 下滑超过阈值 → 隐藏
    tabbarHidden.value = true
  } else if (y < lastScrollY - 6) {
    // 上滑（稍快）→ 显示
    tabbarHidden.value = false
  }
  lastScrollY = y
}

function onTapBottom(e: MouseEvent | TouchEvent) {
  if (!props.portrait || !tabbarHidden.value) return
  const clientY = 'touches' in e ? e.touches[0]?.clientY ?? 0 : e.clientY
  // 点击屏幕底部 60px 区域 → 重新显示
  if (window.innerHeight - clientY <= 60) {
    tabbarHidden.value = false
  }
}

onMounted(() => {
  window.addEventListener('scroll', onScroll, { passive: true })
  window.addEventListener('click', onTapBottom, { passive: true })
})
onUnmounted(() => {
  window.removeEventListener('scroll', onScroll)
  window.removeEventListener('click', onTapBottom)
})

/* ============ 液态激活指示器 ============ */
const navRef = ref<HTMLElement>()
const itemRefs = ref<(HTMLElement | null)[]>([])

/** 当前激活的导航项索引（隐藏项不参与） */
const activeIndex = computed(() => navs.findIndex((n) => n.show && n.judge(path.value ?? '')))

const indicator = ref({ left: 0, top: 0, width: 0, height: 0, visible: false })

function setItemRef(el: Element | null, i: number) {
  itemRefs.value[i] = el as HTMLElement | null
}

async function updateIndicator() {
  await nextTick()
  const idx = activeIndex.value
  const el = idx >= 0 ? itemRefs.value[idx] : null
  if (!navRef.value || !el) {
    indicator.value.visible = false
    return
  }
  indicator.value = {
    left: el.offsetLeft,
    top: el.offsetTop,
    width: el.offsetWidth,
    height: el.offsetHeight,
    visible: true
  }
}

watch(path, updateIndicator)

// 横竖屏切换时指示器需要重新测量（先隐藏避免错位动画）
watch(
  () => props.portrait,
  async () => {
    indicator.value.visible = false
    await updateIndicator()
  }
)

// 窗口尺寸变化（旋转/分屏）时重新定位
const { width, height } = useWindowSize()
watch([width, height], updateIndicator)

onMounted(updateIndicator)
</script>

<template>
  <!-- 横屏：左侧悬浮玻璃侧栏 -->
  <nav v-if="!props.portrait" ref="navRef" class="lg-nav lg-sidebar hidden-print">
    <span
      class="lg-indicator"
      :class="{ 'is-visible': indicator.visible }"
      :style="{
        left: indicator.left + 'px',
        top: indicator.top + 'px',
        width: indicator.width + 'px',
        height: indicator.height + 'px'
      }"
    />
    <button
      v-for="(nav, i) in navs"
      :key="nav.path"
      v-show="nav.show"
      :ref="(el) => setItemRef(el as Element | null, i)"
      class="lg-nav-item lg-sidebar-item"
      :class="{ 'is-active': nav.judge(path ?? '') }"
      @click="routeTo(nav.path)"
    >
      <el-icon class="lg-nav-icon"><component :is="nav.icon" /></el-icon>
      <span class="lg-nav-label">{{ t(`nav.${nav.name}`) }}</span>
    </button>
    <div v-if="showDarkToggle" class="lg-nav-divider" />
    <button
      v-if="showDarkToggle"
      class="lg-nav-item lg-sidebar-item"
      @click="toggleDark"
    >
      <span
        class="lg-theme-icon"
        :class="{ 'is-dark': iconDark }"
        :style="{ transform: `rotate(${themeRotation}deg)` }"
      >
        <el-icon class="lg-icon-sun"><IconLightModeRounded /></el-icon>
        <el-icon class="lg-icon-moon"><IconDarkModeRounded /></el-icon>
      </span>
      <span class="lg-nav-label">{{ iconDark ? t('nav.dark') : t('nav.light') }}</span>
    </button>
  </nav>

  <!-- 竖屏：底部悬浮玻璃导航条 -->
  <nav
    v-else
    ref="navRef"
    class="lg-nav lg-tabbar hidden-print"
    :class="{ 'lg-tabbar-hidden': tabbarHidden }"
  >
    <span
      class="lg-indicator"
      :class="{ 'is-visible': indicator.visible }"
      :style="{
        left: indicator.left + 'px',
        top: indicator.top + 'px',
        width: indicator.width + 'px',
        height: indicator.height + 'px'
      }"
    />
    <ElTooltip
      v-for="(nav, i) in navs"
      :key="nav.path"
      :content="t(`nav.${nav.name}`)"
      placement="top"
      effect="light"
      :show-after="200"
      :disabled="!nav.show"
    >
      <button
        v-show="nav.show"
        :ref="(el) => setItemRef(el as Element | null, i)"
        class="lg-nav-item lg-tabbar-item"
        :class="{ 'is-active': nav.judge(path ?? '') }"
        @click="routeTo(nav.path)"
      >
        <el-icon class="lg-nav-icon"><component :is="nav.icon" /></el-icon>
      </button>
    </ElTooltip>
    <template v-if="showDarkToggle">
      <div class="lg-nav-divider lg-nav-divider-v" />
      <button class="lg-nav-item lg-tabbar-item" @click="toggleDark">
        <span
          class="lg-theme-icon"
          :class="{ 'is-dark': iconDark }"
          :style="{ transform: `rotate(${themeRotation}deg)` }"
        >
          <el-icon class="lg-icon-sun"><IconLightModeRounded /></el-icon>
          <el-icon class="lg-icon-moon"><IconDarkModeRounded /></el-icon>
        </span>
      </button>
    </template>
  </nav>
</template>

<style scoped>
/* ============ 悬浮玻璃容器 ============ */
.lg-nav {
  position: fixed;
  z-index: 2000;
  background: var(--lg-bg-strong);
  -webkit-backdrop-filter: blur(var(--lg-blur-strong)) saturate(var(--lg-saturate));
  backdrop-filter: blur(var(--lg-blur-strong)) saturate(var(--lg-saturate));
  box-shadow: var(--lg-rim), var(--lg-rim-bottom), var(--lg-shadow-float);
}

/* 顶部镜面高光 */
.lg-nav::before {
  content: '';
  position: absolute;
  inset: 0;
  border-radius: inherit;
  pointer-events: none;
  background: radial-gradient(140% 60% at 50% 0%, var(--lg-specular) 0%, transparent 55%);
  opacity: 0.4;
}

/* ---- 横屏：贴左边缘、垂直居中的玻璃侧栏（高度自适应内容，上下留白） ---- */
.lg-sidebar {
  left: 0;
  top: 50%;
  bottom: auto;
  height: auto;
  max-height: calc(100dvh - 96px);
  transform: translateY(-50%);
  display: flex;
  flex-direction: column;
  justify-content: flex-start;
  gap: 2px;
  padding: 14px 12px;
  width: 8.5rem;
  border-radius: 0 24px 24px 0;
  overflow-y: auto;
  /* 加强版液态玻璃：更高不透明度 + 更强模糊/饱和 */
  background: linear-gradient(135deg, rgba(255, 255, 255, 0.85), rgba(255, 255, 255, 0.62));
  -webkit-backdrop-filter: blur(24px) saturate(160%);
  backdrop-filter: blur(24px) saturate(160%);
  box-shadow:
    inset 0 0 0 1px rgba(255, 255, 255, 0.45),
    inset 0 1px 0 var(--lg-specular),
    inset -1px 0 0 rgba(255, 255, 255, 0.35),
    var(--lg-shadow-float);
}

html.dark .lg-sidebar {
  background: linear-gradient(135deg, rgba(44, 46, 58, 0.9), rgba(28, 30, 38, 0.72));
  box-shadow:
    inset 0 0 0 1px rgba(255, 255, 255, 0.14),
    inset 0 1px 0 rgba(255, 255, 255, 0.18),
    var(--lg-shadow-float);
}

/* 侧栏镜面高光加强 */
.lg-sidebar::before {
  opacity: 0.6;
}

/* ---- 竖屏：底部悬浮药丸（参考 iOS 26） ---- */
.lg-tabbar {
  left: 50%;
  bottom: calc(14px + env(safe-area-inset-bottom, 0px));
  transform: translateX(-50%);
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 8px 12px;
  border-radius: 999px;
  max-width: calc(100vw - 28px);
  /* 隐藏/显示的滑入滑出动画 */
  transition: transform 0.45s var(--lg-ease), opacity 0.35s ease;
}

/* 下滑隐藏：沉入屏幕底部外 + 淡出 */
.lg-tabbar-hidden {
  transform: translateX(-50%) translateY(calc(100% + 30px)) scale(0.92);
  opacity: 0;
  pointer-events: none;
}

/* ============ 主题切换图标：旋转 + 太阳/月亮交叉渐变 ============ */
.lg-theme-icon {
  position: relative;
  display: inline-block;
  width: 20px;
  height: 20px;
  transition: transform 0.65s var(--lg-ease);
}

.lg-theme-icon .el-icon {
  position: absolute;
  inset: 0;
  font-size: 20px;
  transition: opacity 0.3s ease, transform 0.65s var(--lg-ease);
}

/* 太阳 → 月亮：旋转中缩小淡出，月亮旋转放大淡入 */
.lg-icon-sun {
  opacity: 1;
  transform: scale(1) rotate(0deg);
}

.lg-icon-moon {
  opacity: 0;
  transform: scale(0.4) rotate(-90deg);
}

.lg-theme-icon.is-dark .lg-icon-sun {
  opacity: 0;
  transform: scale(0.4) rotate(90deg);
}

.lg-theme-icon.is-dark .lg-icon-moon {
  opacity: 1;
  transform: scale(1) rotate(0deg);
}

/* ============ 液态激活指示器（共享团块，弹性滑动） ============ */
.lg-indicator {
  position: absolute;
  pointer-events: none;
  border-radius: 999px;
  background: color-mix(in srgb, var(--el-color-primary) 16%, transparent);
  box-shadow:
    inset 0 0 0 1px color-mix(in srgb, var(--el-color-primary) 28%, transparent),
    inset 0 1px 0 var(--lg-specular);
  opacity: 0;
  /* 位置与尺寸同曲线回弹：团块滑动时先拉伸、到位后回弹，形成液态感 */
  transition:
    left 0.55s cubic-bezier(0.3, 1.6, 0.4, 1),
    top 0.55s cubic-bezier(0.3, 1.6, 0.4, 1),
    width 0.55s cubic-bezier(0.3, 1.6, 0.4, 1),
    height 0.55s cubic-bezier(0.3, 1.6, 0.4, 1),
    opacity 0.2s ease;
}

.lg-sidebar .lg-indicator {
  border-radius: 16px;
}

.lg-indicator.is-visible {
  opacity: 1;
}

html.dark .lg-indicator {
  background: color-mix(in srgb, var(--el-color-primary) 26%, transparent);
}

/* ============ 导航项 ============ */
.lg-nav-item {
  position: relative;
  z-index: 1;
  display: flex;
  align-items: center;
  border: none;
  background: transparent;
  color: var(--el-text-color-regular);
  cursor: pointer;
  -webkit-tap-highlight-color: transparent;
  transition: transform 0.25s var(--lg-ease), color 0.25s ease;
}

.lg-nav-item:active {
  transform: scale(0.92);
}

.lg-sidebar-item {
  gap: 10px;
  padding: 10px 14px;
  border-radius: 16px;
  font-size: 14px;
  white-space: nowrap;
}

.lg-tabbar-item {
  justify-content: center;
  width: 44px;
  height: 44px;
  border-radius: 999px;
}

.lg-nav-icon {
  font-size: 20px;
}

.lg-nav-label {
  line-height: 1;
}

/* 激活项：仅文字/图标变色，高亮团块由共享指示器承担 */
.lg-nav-item.is-active {
  color: var(--el-color-primary);
}

html.dark .lg-nav-item.is-active {
  color: var(--el-color-primary-light-3);
}

/* 分隔线 */
.lg-nav-divider {
  height: 1px;
  margin: 6px 10px;
  background: currentColor;
  opacity: 0.12;
}

.lg-nav-divider-v {
  width: 1px;
  height: 22px;
  margin: 0 4px;
}

/* 打印时隐藏 */
@media print {
  .lg-nav {
    display: none;
  }
}
</style>
