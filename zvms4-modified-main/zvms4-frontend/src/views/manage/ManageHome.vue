<script setup lang="ts">
import { useRoute, useRouter } from 'vue-router'
import { useUserStore } from '@/stores/user.ts'
import { ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'


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

const VALID_CURRENT = ['groups', 'users', 'roles', 'ban', 'logs', 'notifications']

const route = useRoute()
const router = useRouter()
const userStore = useUserStore()
const { t } = useI18n()
const isAdmin = userStore.position.includes('admin')

if (!(isAdmin || userStore.position.includes('department'))) {
  router.replace('/not-found')
}

const curPage = route.params.action?.toString()

if ((curPage === 'notifications' || curPage === 'roles') && !isAdmin) {
  router.replace('/manage/groups')
}

if (curPage && !VALID_CURRENT.includes(curPage)) {
  router.replace('/manage/groups')
}

const current = ref(
  curPage &&
    VALID_CURRENT.includes(curPage) &&
    (curPage !== 'notifications' || isAdmin) &&
    (curPage !== 'roles' || isAdmin)
    ? curPage
    : 'groups'
)

const tabs = ref<SegmentedTab[]>([
  {
    label: 'Groups',
    value: 'groups'
  },
  {
    label: 'Users',
    value: 'users'
  }
])

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
  tabs.value[tabs.value.length] = {
    label: 'Logs',
    value: 'logs'
  }
}

watch(current, () => {
  router.replace('/manage/' + current.value)
})

watch(
  () => route.params.action,
  () => {
    const curPage = route.params.action?.toString()
    if ((curPage === 'notifications' || curPage === 'roles') && !isAdmin) {
      router.replace('/manage/groups')
      current.value = 'groups'
    } else if (curPage && VALID_CURRENT.includes(curPage)) {
      current.value = curPage
    } else {
      router.replace('/manage/groups')
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

    <nav class="yg2-segment manage-tabs">
      <button
        v-for="item in tabs"
        :key="item.value"
        class="yg2-segment__item"
        :class="{ 'is-active': current === item.value }"
        type="button"
        @click="current = item.value"
      >
        {{ t(('manage.manage.tabs.' + item.value) as string) }}
      </button>
    </nav>
    <div class="yg2-panel">
      <ZGroupList v-if="current === 'groups'" />
      <ZGroupUserList v-else-if="current === 'users'" id="" />
      <ManageRoles v-else-if="current === 'roles'" />
      <ZLogList v-else-if="current === 'logs'" id="" user="" />
      <ZBanRules v-else-if="current === 'ban'" />
      <ZNotificationAdmin v-else-if="current === 'notifications'" />
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
