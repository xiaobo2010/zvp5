<script setup lang="ts">
import { useRoute, useRouter } from 'vue-router'
import { useUserStore } from '@/stores/user.ts'
import { computed, nextTick, ref, watch, type Component } from 'vue'
import { useI18n } from 'vue-i18n'
import { useWindowSize } from '@vueuse/core'
import { ElPageHeader, ElSegmented, ElIcon } from 'element-plus'
import { ArrowLeft, Avatar, Bell } from '@element-plus/icons-vue'
import { Block, Config, Group, Log, User } from '@icon-park/vue-next'
import ZGroupUserList from '@/components/group/ZGroupUserList.vue'
import ZGroupList from '@/components/group/ZGroupList.vue'
import ZLogList from '@/components/log/ZLogList.vue'
import ZBanRules from '@/components/manage/ZBanRules.vue'
import ZNotificationAdmin from '@/components/manage/ZNotificationAdmin.vue'
import ZSystemSettings from '@/components/manage/ZSystemSettings.vue'
import ManageRoles from '@/views/manage/ManageRoles.vue'

interface SegmentedTab {
  label: string
  value: string
  icon: Component
}

const VALID_CURRENT = ['groups', 'users', 'roles', 'ban', 'logs', 'notifications', 'settings']

/** 只有管理员能进的分区；其他人访问一律回落到群组列表。 */
const ADMIN_ONLY = ['notifications', 'roles', 'settings']

const route = useRoute()
const router = useRouter()
const userStore = useUserStore()
const { t } = useI18n()
const isAdmin = userStore.position.includes('admin')

if (!(isAdmin || userStore.position.includes('department'))) {
  router.replace('/not-found')
}

const curPage = route.params.action?.toString()

if (curPage && ADMIN_ONLY.includes(curPage) && !isAdmin) {
  router.replace('/manage/groups')
}

if (curPage && !VALID_CURRENT.includes(curPage)) {
  router.replace('/manage/groups')
}

const current = ref(
  curPage &&
    VALID_CURRENT.includes(curPage) &&
    (!ADMIN_ONLY.includes(curPage) || isAdmin)
    ? curPage
    : 'groups'
)

const tabs = ref<SegmentedTab[]>([
  {
    label: 'Groups',
    value: 'groups',
    icon: Group
  },
  {
    label: 'Users',
    value: 'users',
    icon: User
  }
])

if (isAdmin) {
  tabs.value[tabs.value.length] = {
    label: 'Roles',
    value: 'roles',
    icon: Avatar
  }
  tabs.value[tabs.value.length] = {
    label: 'Notifications',
    value: 'notifications',
    icon: Bell
  }
  tabs.value[tabs.value.length] = {
    label: 'Ban',
    value: 'ban',
    icon: Block
  }
  tabs.value[tabs.value.length] = {
    label: 'Logs',
    value: 'logs',
    icon: Log
  }
  tabs.value[tabs.value.length] = {
    label: 'Settings',
    value: 'settings',
    icon: Config
  }
}

watch(current, () => {
  router.replace('/manage/' + current.value)
})

watch(
  () => route.params.action,
  () => {
    const curPage = route.params.action?.toString()
    if (curPage && ADMIN_ONLY.includes(curPage) && !isAdmin) {
      router.replace('/manage/groups')
      current.value = 'groups'
    } else if (curPage && VALID_CURRENT.includes(curPage)) {
      current.value = curPage
    } else {
      router.replace('/manage/groups')
    }
  }
)

/* 与 App.vue / UserNav 同一判定：宽 < 高 × 1.2 即竖屏 */
const { width, height } = useWindowSize()
const verticalMode = computed(() => width.value < height.value * 1.2)

/** 竖屏或窄屏一律改用胶囊导航；≥1024 的横屏保留原分段控件 */
const compactTabs = computed(() => verticalMode.value || width.value < 1024)

const tabsNavRef = ref<HTMLElement | null>(null)

/** 当前项自动滚入可视区（横向居中，不影响页面纵向滚动） */
function scrollActiveTabIntoView() {
  const nav = tabsNavRef.value
  if (!nav) {
    return
  }
  const active = nav.querySelector<HTMLElement>('.manage-tabs__item.is-active')
  if (!active) {
    return
  }
  const target = active.offsetLeft - (nav.clientWidth - active.offsetWidth) / 2
  const reduced = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false
  nav.scrollTo({ left: Math.max(0, target), behavior: reduced ? 'auto' : 'smooth' })
}

watch([current, compactTabs], () => {
  void nextTick(() => {
    scrollActiveTabIntoView()
  })
})
</script>

<template>
  <div :class="compactTabs ? 'px-4 py-3' : 'px-6 py-4'">
    <!-- 竖屏/窄屏：紧凑页头（图标优先），返回与标题同行 -->
    <header v-if="compactTabs" class="manage-head">
      <button
        type="button"
        class="yg-icon-btn manage-head__back"
        :aria-label="t('manage.manage.back')"
        @click="router.back()"
      >
        <ElIcon :size="20">
          <ArrowLeft />
        </ElIcon>
      </button>
      <h1 class="manage-head__title">{{ t('manage.manage.tabs.' + current) }}</h1>
    </header>

    <!-- 横屏（≥1024）：保留原分段控件 -->
    <ElPageHeader
      v-else
      :icon="ArrowLeft"
      @back="() => router.back()"
      class="py-4"
    >
      <template #content>
        {{ t('manage.manage.tabs.' + current) }}
      </template>
      <template #extra>
        <ElSegmented v-model="current" :options="tabs">
          <template #default="props">
            <div class="flex flex-col items-center gap-2 p-2">
              <ElIcon :size="18" class="mt-2">
                <Component :is="(props.item as SegmentedTab).icon" />
              </ElIcon>
              {{ t(('manage.manage.tabs.' + (props.item as SegmentedTab).value) as string) }}
            </div>
          </template>
        </ElSegmented>
      </template>
    </ElPageHeader>

    <!-- 竖屏/窄屏：横向可滚动的胶囊导航 -->
    <nav
      v-if="compactTabs"
      ref="tabsNavRef"
      class="manage-tabs yg-liquid-glass"
      role="tablist"
      :aria-label="t('manage.manage.tabsLabel')"
    >
      <button
        v-for="tab in tabs"
        :key="tab.value"
        type="button"
        role="tab"
        class="manage-tabs__item"
        :class="{ 'is-active': tab.value === current }"
        :aria-selected="tab.value === current"
        @click="current = tab.value"
      >
        <ElIcon :size="18">
          <Component :is="tab.icon" />
        </ElIcon>
        <span class="manage-tabs__label">{{ t('manage.manage.tabs.' + tab.value) }}</span>
      </button>
    </nav>

    <ZGroupList v-if="current === 'groups'" />
    <ZGroupUserList v-else-if="current === 'users'" id="" />
    <ManageRoles v-else-if="current === 'roles'" />
    <ZLogList v-else-if="current === 'logs'" id="" user="" />
    <ZBanRules v-else-if="current === 'ban'" />
    <ZNotificationAdmin v-else-if="current === 'notifications'" />
    <ZSystemSettings v-else-if="current === 'settings'" />
  </div>
</template>

<style scoped>
/* 紧凑页头：返回按钮与标题同行，克制、图标优先 */
.manage-head {
  display: flex;
  align-items: center;
  gap: var(--yg-space-3);
  margin-bottom: var(--yg-space-3);
}

.manage-head__title {
  margin: 0;
  font-size: var(--yg-font-size-xl);
  font-weight: var(--yg-font-weight-semibold);
  line-height: var(--yg-line-height-tight);
  color: var(--yg-text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 胶囊导航：横向可滚动，当前项自动滚入可视区 */
.manage-tabs {
  display: flex;
  align-items: center;
  gap: var(--yg-space-2);
  margin-bottom: var(--yg-space-4);
  padding: var(--yg-space-1);
  overflow-x: auto;
  overscroll-behavior-x: contain;
  scrollbar-width: none;
  -webkit-overflow-scrolling: touch;
  border: none;
  border-radius: var(--yg-radius-pill);
  /* 液态玻璃观感：复用标准库配方，模糊半径压到 sm —— 横向滑动时每帧都要重采样 */
  -webkit-backdrop-filter: blur(var(--yg-glass-blur-sm)) saturate(180%);
  backdrop-filter: blur(var(--yg-glass-blur-sm)) saturate(180%);
}

.manage-tabs::-webkit-scrollbar {
  display: none;
}

/* 导航项：44px 触控目标 */
.manage-tabs__item {
  display: inline-flex;
  align-items: center;
  gap: var(--yg-space-2);
  flex: 0 0 auto;
  min-height: var(--yg-control-height-lg);
  padding: 0 var(--yg-space-4);
  border: none;
  border-radius: var(--yg-radius-pill);
  background-color: transparent;
  color: var(--yg-text-tertiary);
  font-family: inherit;
  font-size: var(--yg-font-size-md);
  line-height: var(--yg-line-height-snug);
  white-space: nowrap;
  cursor: pointer;
  transition:
    color var(--yg-dur-base) var(--yg-ease-out),
    background-color var(--yg-dur-base) var(--yg-ease-out),
    box-shadow var(--yg-dur-base) var(--yg-ease-out);
}

.manage-tabs__item:hover {
  color: var(--yg-text-primary);
  background-color: var(--yg-bg-hover);
}

.manage-tabs__item:active {
  background-color: var(--yg-bg-active);
}

.manage-tabs__item:focus-visible {
  outline: none;
  box-shadow: var(--yg-focus-ring);
}

.manage-tabs__item.is-active {
  color: var(--yg-color-primary);
  background-color: var(--yg-color-primary-soft);
  box-shadow: var(--yg-shadow-sm);
  font-weight: var(--yg-font-weight-medium);
}
</style>
