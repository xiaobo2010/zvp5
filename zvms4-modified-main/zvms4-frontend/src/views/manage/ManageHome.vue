<script setup lang="ts">
import { useRoute, useRouter } from 'vue-router'
import { useUserStore } from '@/stores/user.ts'
import { ref, watch, type Component } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElPageHeader, ElSegmented, ElIcon } from 'element-plus'
import { ArrowLeft, Avatar, Bell } from '@element-plus/icons-vue'
import { Block, Group, Log, User } from '@icon-park/vue-next'
import ZGroupUserList from '@/components/group/ZGroupUserList.vue'
import ZGroupList from '@/components/group/ZGroupList.vue'
import ZLogList from '@/components/log/ZLogList.vue'
import ZBanRules from '@/components/manage/ZBanRules.vue'
import ZNotificationAdmin from '@/components/manage/ZNotificationAdmin.vue'
import ManageRoles from '@/views/manage/ManageRoles.vue'

interface SegmentedTab {
  label: string
  value: string
  icon: Component
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
  <div class="px-6 py-4">
    <ElPageHeader :icon="ArrowLeft" @back="() => router.back()" class="py-4">
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
    <ZGroupList v-if="current === 'groups'" />
    <ZGroupUserList v-else-if="current === 'users'" id="" />
    <ManageRoles v-else-if="current === 'roles'" />
    <ZLogList v-else-if="current === 'logs'" id="" user="" />
    <ZBanRules v-else-if="current === 'ban'" />
    <ZNotificationAdmin v-else-if="current === 'notifications'" />
  </div>
</template>
