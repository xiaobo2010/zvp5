<script setup lang="ts">
import { useRoute, useRouter } from 'vue-router'
import { useUserStore } from '@/stores/user.ts'
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import Segmented from '@/ui/ux/Segmented.vue'


import ZGroupUserList from '@/components/group/ZGroupUserList.vue'
import ZGroupList from '@/components/group/ZGroupList.vue'
import ZLogList from '@/components/log/ZLogList.vue'
import ZBanRules from '@/components/manage/ZBanRules.vue'
import ZNotificationAdmin from '@/components/manage/ZNotificationAdmin.vue'
import ManageRoles from '@/views/manage/ManageRoles.vue'

interface SegmentedTab {
  label: string
  value: string
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

const segmentItems = computed(() =>
  tabs.value.map((item) => ({
    value: item.value,
    label: t(('manage.manage.tabs.' + item.value) as string)
  }))
)

const tabs = ref<SegmentedTab[]>(
  secretaryOnly
    ? [{ label: 'Users', value: 'users' }]
    : [
        {
          label: 'Groups',
          value: 'groups'
        },
        {
          label: 'Users',
          value: 'users'
        }
      ]
)

/** 「用户」分区：团支书按本班成员展示，admin/学生会看全校。 */
const usersGroupId = secretaryOnly ? userStore.class_id || '' : ''

if (isAdmin) {
  tabs.value[tabs.value.length] = {
    label: 'Roles',
    value: 'roles'
  }
  tabs.value[tabs.value.length] = {
    label: 'Notifications',
    value: 'notifications'
  }
  tabs.value[tabs.value.length] = {
    label: 'Ban',
    value: 'ban'
  }
}

// 学生会上传/核查材料同样要进附件管理：与后端 _is_manager 对齐。
if (isAdmin || isDepartment) {
  tabs.value[tabs.value.length] = {
    label: 'Attachments',
    value: 'attachments'
  }
}

if (isAdmin) {
  tabs.value[tabs.value.length] = {
    label: 'Logs',
    value: 'logs'
  }
  tabs.value[tabs.value.length] = {
    label: 'Settings',
    value: 'settings'
  }
}

watch(current, () => {
  router.replace('/manage/' + current.value)
})

watch(
  () => route.params.action,
  () => {
    const raw = route.params.action?.toString()
    const next = normalizeTab(raw)
    if (next && !accessibleTab(next)) {
      router.replace(`/manage/${HOME_TAB}`)
      current.value = HOME_TAB
    } else if (next && VALID_CURRENT.includes(next)) {
      current.value = next
      if (raw && raw !== next) {
        // 旧分区深链：归一到现分区，地址栏一并替换
        router.replace(`/manage/${next}`)
      }
    } else {
      router.replace(`/manage/${HOME_TAB}`)
    }
  }
)
</script>

<template>
  <div class="yg2-page manage-page">
    <button class="manage-back" type="button" @click="router.back()">
      <span aria-hidden="true">‹</span> 返回
    </button>

    <header class="yg2-page-header">
      <div>
        <p class="yg2-page-header__eyebrow">平台管理</p>
        <h1 class="yg2-page-header__title">{{ t('manage.manage.tabs.' + current) }}</h1>
      </div>
    </header>

    <Segmented
      v-model="current"
      class="manage-tabs"
      :items="segmentItems"
      aria-label="平台管理"
    />
    <div class="yg2-panel">
      <ZGroupList v-if="current === 'groups'" />
      <ZGroupUserList v-else-if="current === 'users'" :id="usersGroupId" />
      <ManageRoles v-else-if="current === 'roles'" />
      <ZLogList v-else-if="current === 'logs'" id="" user="" />
      <ZBanRules v-else-if="current === 'ban'" />
      <ZNotificationAdmin v-else-if="current === 'notifications'" />
      <ZAttachmentAudit v-else-if="current === 'attachments'" />
      <ZSystemSettings v-else-if="current === 'settings'" />
    </div>
  </div>
</template>

<style scoped>
.manage-page {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.manage-back {
  align-self: flex-start;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  height: 32px;
  padding: 0 12px 0 8px;
  border: 0;
  border-radius: var(--yg2-r-pill);
  background: transparent;
  color: var(--yg2-text-secondary);
  font: inherit;
  font-size: 14px;
  font-weight: 500;
  cursor: pointer;
}

.manage-back:hover {
  background: var(--yg2-surface-sunken);
  color: var(--yg2-text);
}

.manage-tabs {
  align-self: flex-start;
  max-width: 100%;
  overflow-x: auto;
  scrollbar-width: none;
}

.manage-tabs::-webkit-scrollbar {
  display: none;
}
</style>
