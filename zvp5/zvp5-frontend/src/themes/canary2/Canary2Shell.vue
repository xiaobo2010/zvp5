<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useWindowSize } from '@vueuse/core'
import { ElMessageBox } from 'element-plus/es/components/message-box/index.mjs'
import { ElPopover } from 'element-plus/es/components/popover/index.mjs'
import {
  HomeFilled,
  Calendar,
  Avatar,
  SwitchButton,
  Lock,
  ArrowDown,
  User
} from '@element-plus/icons-vue'
import { useUserStore } from '@/stores/user'
import { useHeaderStore } from '@/stores/header'
import { modifyPasswordDialogs } from '@/views/user/modifyPasswordDialogs'
import AmbientField, { installAmbientCursor, installAmbientTilt } from './AmbientField.vue'
import AppSidebar from './AppSidebar.vue'
import AppTopbar from './AppTopbar.vue'

defineProps<{ dark?: boolean }>()
const emit = defineEmits<{ 'toggle-dark': [] }>()

const route = useRoute()
const router = useRouter()
const userStore = useUserStore()
const headerStore = useHeaderStore()

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

const mobileTabIndex = computed(() => {
  const index = mobileTabs.value.findIndex((tab) => tab.judge(route.path))
  return index < 0 ? mobileTabs.value.length : index
})

interface MobileTab {
  key: string
  label: string
  icon: unknown
  show?: () => boolean
  judge: (path: string) => boolean
  action: () => void
}

const mobileTabAll: MobileTab[] = [
  {
    key: 'home',
    label: '首页',
    icon: HomeFilled,
    judge: (path) => path === '/user',
    action: () => router.push('/user')
  },
  {
    key: 'activity',
    label: '义工',
    icon: Calendar,
    judge: (path) => path.startsWith('/activit'),
    action: () => router.push('/activities')
  },
  {
    key: 'profile',
    label: '我的',
    icon: Avatar,
    judge: (path) => path.startsWith('/me'),
    action: () => router.push('/me')
  },
  {
    key: 'create',
    label: '创建',
    icon: null,
    show: () =>
      userStore.position.includes('admin') ||
      userStore.position.includes('department') ||
      userStore.position.includes('secretary'),
    judge: (path) => path.startsWith('/activity/create'),
    action: () => router.push('/activity/create')
  }
]

const mobileTabs = computed(() => mobileTabAll.filter((tab) => !tab.show || tab.show()))

async function changePassword() {
  try {
    await modifyPasswordDialogs(String(userStore.id), 'zh-CN', userStore.resetPassword)
  } catch {
    // 取消改密不算异常
  }
}

async function logout() {
  try {
    await ElMessageBox.confirm(
      '退出登录后需要重新验证身份，确定要退出吗？',
      '退出登录',
      {
        confirmButtonText: '退出登录',
        cancelButtonText: '取消',
        type: 'warning',
        confirmButtonClass: 'el-button--danger',
        closeOnClickModal: false
      }
    )
    await userStore.removeUser()
    await router.replace('/user/login')
  } catch {
    // 用户取消
  }
}

let disposeAmbient: (() => void) | null = null
let disposeTilt: (() => void) | null = null

onMounted(() => {
  disposeAmbient = installAmbientCursor()
  disposeTilt = installAmbientTilt()
})

onUnmounted(() => {
  disposeAmbient?.()
  disposeTilt?.()
})
</script>

<template>
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
            :title="dark ? '切换到浅色模式' : '切换到深色模式'"
            :aria-label="dark ? '切换到浅色模式' : '切换到深色模式'"
            @click="emit('toggle-dark')"
          >
            <svg
              v-if="dark"
              width="17"
              height="17"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="1.6"
              stroke-linecap="round"
            >
              <circle cx="12" cy="12" r="4.2" />
              <path d="M12 3v2M12 19v2M3 12h2M19 12h2M5.6 5.6 7 7M17 17l1.4 1.4M18.4 5.6 17 7M7 17l-1.4 1.4" />
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
            width="188"
            trigger="click"
            placement="bottom-end"
            popper-class="yg2-account-pop"
            :popper-options="{ modifiers: [{ name: 'offset', options: { offset: [0, 10] } }] }"
          >
            <template #reference>
              <button type="button" class="yg2-user-chip" aria-label="账号菜单">
                <span class="yg2-user-chip__avatar">
                  <User width="14" height="14" />
                </span>
                <span class="yg2-user-chip__name">{{ userStore.name }}</span>
                <ArrowDown class="yg2-user-chip__caret" width="12" height="12" />
              </button>
            </template>
            <div class="yg2-account-menu" role="menu">
              <button type="button" class="yg2-account-menu__item" @click="changePassword">
                <Lock width="14" height="14" />
                <span>密码修改</span>
              </button>
              <button
                type="button"
                class="yg2-account-menu__item yg2-account-menu__item--danger"
                @click="logout"
              >
                <SwitchButton width="14" height="14" />
                <span>退出登录</span>
              </button>
            </div>
          </ElPopover>
        </template>
      </AppTopbar>
      <main class="yg2-app__content">
        <div class="yg2-page yg2-app__page">
          <slot />
        </div>
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
        <component :is="tab.icon" v-if="tab.icon" />
        <svg
          v-else
          width="18"
          height="18"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          stroke-width="1.8"
          stroke-linecap="round"
        >
          <path d="M12 5v14M5 12h14" />
        </svg>
        <span>{{ tab.label }}</span>
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
        <span>菜单</span>
      </button>
    </nav>
    <div v-if="drawerOpen" class="yg2-drawer-mask" @click="drawerOpen = false" />
  </div>
</template>

<style>
/* 账号菜单 Teleport 到 body，scoped 样式够不到，用全局类名收口。 */
.yg2-account-pop.el-popover.el-popper {
  padding: 6px;
  border: 1px solid var(--yg2-glass-chrome-stroke);
  border-radius: var(--yg2-r-16);
  background: var(--yg2-glass-overlay-fill);
  -webkit-backdrop-filter: blur(var(--yg2-glass-blur)) saturate(var(--yg2-glass-saturate));
  backdrop-filter: blur(var(--yg2-glass-blur)) saturate(var(--yg2-glass-saturate));
  box-shadow: var(--yg2-shadow-float);
}

.yg2-account-pop .el-popper__arrow {
  display: none;
}

.yg2-account-menu {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.yg2-account-menu__item {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  height: 34px;
  padding: 0 10px;
  border-radius: var(--yg2-r-8);
  color: var(--yg2-text);
  font-size: 13.5px;
  text-align: left;
}

.yg2-account-menu__item:hover {
  background: var(--yg2-surface-sunken);
}

.yg2-account-menu__item--danger {
  color: var(--yg2-danger);
}

.yg2-account-menu__item--danger:hover {
  background: var(--yg2-danger-soft);
}
</style>

<style scoped>
.yg2-app__page {
  min-height: 100%;
}
</style>
