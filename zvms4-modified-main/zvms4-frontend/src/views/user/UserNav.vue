<script setup lang="ts">
import {
  CirclePlusFilled,
  HomeFilled,
  InfoFilled,
  Sunny,
  Moon,
  Management,
  Monitor,
  ChatLineRound
} from '@element-plus/icons-vue'
import { ElIcon, ElTooltip } from 'element-plus'
import { ref } from 'vue'
import { useUserStore } from '@/stores/user'
import MdiEye from '@/icons/MdiEye.vue'
import type { Component as VueComponent } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useDark } from '@vueuse/core'
import { useI18n } from 'vue-i18n'
import { watch } from 'vue'
import { pad, getTabletType } from '@/plugins/ua'

/** 宽屏折叠成图标条；窄屏转为抽屉（由 App.vue 渲染遮罩）。 */
withDefaults(
  defineProps<{
    collapsed?: boolean
    open?: boolean
  }>(),
  {
    collapsed: false,
    open: false
  }
)

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
      path.startsWith('/group') || path.startsWith('/user/') && path !== '/user/login' || path.startsWith('/manage')
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
</script>

<template>
  <aside
    class="yg-layout__sidebar yg-sidebar yg-liquid-glass"
    :class="{ 'is-collapsed': collapsed, 'is-open': open }"
  >
    <div class="yg-sidebar__header">
      <span class="yg-sidebar__logo">
        <img src="/favicon.ico" alt="" />
      </span>
      <span class="yg-sidebar__title">义工平台</span>
    </div>

    <nav class="yg-menu">
      <template v-for="nav in navs" :key="nav.path">
        <ElTooltip
          v-if="nav.show"
          :content="t(`nav.${nav.name}`)"
          placement="right"
          :disabled="!collapsed"
          :show-after="200"
        >
          <button
            type="button"
            class="yg-menu__item"
            :class="{ 'is-active': nav.judge(path ?? '') }"
            @click="routeTo(nav.path)"
          >
            <ElIcon class="yg-menu__icon">
              <component :is="nav.icon" />
            </ElIcon>
            <span class="yg-menu__label">{{ t(`nav.${nav.name}`) }}</span>
          </button>
        </ElTooltip>
      </template>
    </nav>

    <div class="yg-sidebar__footer">
      <button
        v-if="!pad() || getTabletType() === 'p615' || getTabletType() === 'p620'"
        type="button"
        class="yg-icon-btn yg-icon-btn--ghost"
        :aria-label="t('nav.dark', '深色模式')"
        :title="t('nav.dark', '深色模式')"
        @click="dark = !dark"
      >
        <ElIcon>
          <Moon v-if="dark" />
          <Sunny v-else />
        </ElIcon>
      </button>
    </div>
  </aside>
</template>

<style scoped>
.yg-sidebar__logo img {
  width: 100%;
  height: 100%;
  border-radius: inherit;
  object-fit: cover;
}

@media print {
  .yg-sidebar {
    display: none;
  }
}
</style>
