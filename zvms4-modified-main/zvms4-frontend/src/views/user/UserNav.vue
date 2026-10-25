<script setup lang="ts">
import {
  Avatar,
  CirclePlusFilled,
  HomeFilled,
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

/* ---- 弹性滑动指示器：测量激活项位置，弹簧缓动滑动 ---- */
const itemsRef = ref<HTMLElement>()
const indicator = ref({ x: 0, y: 0, w: 0, h: 0, visible: false })

function updateIndicator() {
  const root = itemsRef.value
  if (!root) return
  const active = root.querySelector<HTMLElement>('.yg-glass-nav__item.is-active')
  if (!active) {
    indicator.value.visible = false
    return
  }
  indicator.value = {
    x: active.offsetLeft,
    y: active.offsetTop,
    w: active.offsetWidth,
    h: active.offsetHeight,
    visible: true
  }
}

const indicatorStyle = computed(() => ({
  transform: `translate(${indicator.value.x}px, ${indicator.value.y}px)`,
  width: indicator.value.w + 'px',
  height: indicator.value.h + 'px',
  opacity: indicator.value.visible ? 1 : 0
}))

onMounted(() => {
  nextTick(updateIndicator)
  window.addEventListener('resize', updateIndicator)
})

onUnmounted(() => {
  window.removeEventListener('resize', updateIndicator)
})

watch([path, verticalMode], () => nextTick(updateIndicator))

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
      <span class="yg-glass-nav__indicator" :style="indicatorStyle" aria-hidden="true" />
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
            @click="routeTo(nav.path)"
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
