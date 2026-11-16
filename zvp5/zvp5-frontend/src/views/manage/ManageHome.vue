<script setup lang="ts">
import { useRoute, useRouter } from 'vue-router'
import { useUserStore } from '@/stores/user.ts'
import { computed, nextTick, ref, watch, type Component } from 'vue'
import { useI18n } from 'vue-i18n'
import { useWindowSize } from '@vueuse/core'
import { ElPageHeader } from 'element-plus/es/components/page-header/index.mjs'
import { ElSegmented } from 'element-plus/es/components/segmented/index.mjs'
import { ElIcon } from 'element-plus/es/components/icon/index.mjs'
import { ArrowLeft, Avatar, Bell } from '@element-plus/icons-vue'
import { Block, Config, Group, Log, Picture, User } from '@icon-park/vue-next'
import ZGroupUserList from '@/components/group/ZGroupUserList.vue'
import ZGroupList from '@/components/group/ZGroupList.vue'
import ZLogList from '@/components/log/ZLogList.vue'
import ZBanRules from '@/components/manage/ZBanRules.vue'
import ZAttachmentAudit from '@/components/manage/ZAttachmentAudit.vue'
import ZNotificationAdmin from '@/components/manage/ZNotificationAdmin.vue'
import ZSystemSettings from '@/components/manage/ZSystemSettings.vue'
import ManageRoles from '@/views/manage/ManageRoles.vue'
import ZHexGrid from '@/components/decor/ZHexGrid.vue'

interface SegmentedTab {
  label: string
  value: string
  icon: Component
}

const route = useRoute()
const router = useRouter()
const userStore = useUserStore()
const { t } = useI18n()

const isAdmin = userStore.position.includes('admin')
const isDepartment = userStore.position.includes('department')
/** 团支书：后端临时密码等能力限同班，前端只给「用户」分区并按班级列人。 */
const isSecretary = userStore.position.includes('secretary')
const secretaryOnly = isSecretary && !isAdmin && !isDepartment

/** 只有管理员能进的分区；其他人访问一律回落到入口分区。 */
const ADMIN_ONLY = ['notifications', 'roles', 'settings', 'ban']

/** 管理员与学生会（department）共用的分区：后端 ``_is_manager`` 同样认这两个角色。 */
const MANAGER_ONLY = ['attachments']

/** 当前角色能不能进这个分区（未列出的分区对所有 manage 角色开放）。 */
function accessibleTab(tab: string): boolean {
  if (ADMIN_ONLY.includes(tab)) {
    return isAdmin
  }
  if (MANAGER_ONLY.includes(tab)) {
    return isAdmin || isDepartment
  }
  return true
}

/**
 * 旧分区 → 现分区：设备封禁已并入封禁管理，旧深链/书签自动归一，不 404。
 */
const LEGACY_TAB_ALIAS: Record<string, string> = { deviceBans: 'ban' }

function normalizeTab(value: string | undefined): string {
  if (!value) {
    return ''
  }
  return LEGACY_TAB_ALIAS[value] ?? value
}

const VALID_CURRENT = secretaryOnly
  ? ['users']
  : [
      'groups',
      'users',
      'roles',
      'ban',
      'deviceBans',
      'attachments',
      'logs',
      'notifications',
      'settings'
    ]

/** 无参数或越权访问时的回退分区。 */
const HOME_TAB = secretaryOnly ? 'users' : 'groups'

if (!(isAdmin || isDepartment || isSecretary)) {
  router.replace('/not-found')
}

const curPage = route.params.action?.toString()
const curTab = normalizeTab(curPage)

if (curTab && !accessibleTab(curTab)) {
  router.replace(`/manage/${HOME_TAB}`)
} else if (curTab && !VALID_CURRENT.includes(curTab)) {
  router.replace(`/manage/${HOME_TAB}`)
} else if (curPage && curTab !== curPage) {
  // 旧分区深链：归一到现分区（地址栏同步替换，历史书签继续可用）
  router.replace(`/manage/${curTab}`)
}

const current = ref(
  curTab && VALID_CURRENT.includes(curTab) && accessibleTab(curTab) ? curTab : HOME_TAB
)

const tabs = ref<SegmentedTab[]>(
  secretaryOnly
    ? [{ label: 'Users', value: 'users', icon: User }]
    : [
        {
          label: t('manage.manage.tabs.groups'),
          value: 'groups',
          icon: Group
        },
        {
          label: 'Users',
          value: 'users',
          icon: User
        }
      ]
)

/** 「用户」分区：团支书按本班成员展示，admin/学生会看全校。 */
const usersGroupId = secretaryOnly ? userStore.class_id || '' : ''

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
}

// 学生会上传/核查材料同样要进附件管理：与后端 _is_manager 对齐。
if (isAdmin || isDepartment) {
  tabs.value[tabs.value.length] = {
    label: 'Attachments',
    value: 'attachments',
    icon: Picture
  }
}

if (isAdmin) {
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
    const raw = route.params.action?.toString()
    const curTab = normalizeTab(raw)
    if (curTab && !accessibleTab(curTab)) {
      router.replace(`/manage/${HOME_TAB}`)
      current.value = HOME_TAB
    } else if (curTab && VALID_CURRENT.includes(curTab)) {
      current.value = curTab
      if (raw && raw !== curTab) {
        // 旧分区深链：归一到现分区，地址栏一并替换
        router.replace(`/manage/${curTab}`)
      }
    } else {
      router.replace(`/manage/${HOME_TAB}`)
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
  <div class="manage-shell" :class="compactTabs ? 'px-4 py-3' : 'px-6 py-4'">
    <!-- 蜂窝底纹：与封禁页同一套仪器感底纹，管理台用主题强调色，压在内容之下 -->
    <ZHexGrid
      :size="64"
      color="var(--phi-breathe, #1de9b6)"
      :opacity="0.12"
      drift
      sweep
      :z-index="-1"
    />
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
            <div class="flex flex-col items-center gap-2 px-2 py-1">
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
    <ZGroupUserList v-else-if="current === 'users'" :id="usersGroupId" />
    <ManageRoles v-else-if="current === 'roles'" />
    <ZLogList v-else-if="current === 'logs'" id="" user="" />
    <ZBanRules v-else-if="current === 'ban'" />
    <ZAttachmentAudit v-else-if="current === 'attachments'" />
    <ZNotificationAdmin v-else-if="current === 'notifications'" />
    <ZSystemSettings v-else-if="current === 'settings'" />
  </div>
</template>

<style scoped>
.manage-shell {
  position: relative;
  /* 建立层叠上下文：蜂窝底纹 z-index:-1 落在页面底色之上、内容之下 */
  isolation: isolate;
}

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
