<script lang="ts" setup>
import ZGroupUserList from '@/components/group/ZGroupUserList.vue'
import { useRoute, useRouter } from 'vue-router'
import { ElPageHeader, ElSegmented, ElIcon, ElText } from 'element-plus'
import { ref, watch, type Component } from 'vue'
import type { Group } from '@/../types'
import { useWindowSize } from '@vueuse/core'
import { useUserStore } from '@/stores/user.ts'
import { useGroupsStore } from '@/stores/groups'
import { ArrowLeft } from '@element-plus/icons-vue'
import { ZActivityList } from '@/components'
import ZGroupUserTimeList from '@/components/group/ZGroupUserTimeList.vue'
import { useI18n } from 'vue-i18n'
import ZUserModification from '@/components/group/ZUserModification.vue'
import { AddUser, User, ViewList } from '@icon-park/vue-next'
import { TablerSum } from '@/icons'
import UilStatistics from '@/icons/UilStatistics.vue'
import ZGroupTimeStatistics from '@/components/group/ZGroupTimeStatistics.vue'

interface SegmentedTab {
  label: string
  value: string
  icon: Component
}

const { height } = useWindowSize()
const userStore = useUserStore()
const groupsStore = useGroupsStore()
const route = useRoute()
const router = useRouter()
const { t } = useI18n()

const id = ref<string>((route.params.id as string) ?? '')

const group = ref<Group>()

const tableHeight = ref(height.value * 0.6)

watch(height, () => {
  tableHeight.value = height.value * 0.6
})

async function refresh() {
  const res = await groupsStore.fetchGroup(id.value)
  group.value = res
  if (res?.type !== 'class') {
    tab.value = 'users'
  }
}

watch(
  () => route.params.id,
  (value) => {
    id.value = value as string
  }
)

watch(id, refresh, { immediate: true })

watch(
  () => route.params.action,
  (value) => {
    tab.value = value.toString() ?? ('users' as string)
  }
)

if (
  !(
    userStore.position.includes('admin') ||
    userStore.position.includes('department') ||
    (userStore.position.includes('secretary') && userStore.class_id === id.value)
  )
) {
  router.replace('/not-found')
}

const tabs = ref<SegmentedTab[]>([
  {
    label: 'Users',
    value: 'users',
    icon: User
  },
  {
    label: 'Activities',
    value: 'activities',
    icon: ViewList
  },
  {
    label: 'Time',
    value: 'time',
    icon: TablerSum
  },
  {
    label: 'Statistics',
    value: 'statistics',
    icon: UilStatistics
  },
])

if (userStore.position.includes('admin')) {
  tabs.value[tabs.value.length] = {
    label: 'Create',
    value: 'create',
    icon: AddUser
  }
}

/*
if (group.value?.type !== 'class') {
  tabs.value = [
    {
      label: 'Users',
      value: 'users',
      icon: User
    },
  ]
}
*/

const tabs2 = ref<SegmentedTab[]>([
  {
    label: 'Users',
    value: 'users',
    icon: User
  },
])

const curPage = route.params.action?.toString()

const tab = ref(curPage && curPage !== '' ? curPage : 'users')

watch(tab, () => {
  router.replace('/group/' + id.value + '/' + (tab.value ?? 'users'))
})
</script>

<template>
  <div class="page-container px-6 py-4">
    <ElPageHeader v-if="id" :icon="ArrowLeft" @back="() => router.back()" class="py-4">
      <template #content>
        {{ group?.name }}
      </template>
      <template #extra>
        <ElSegmented v-model="tab" :options="group?.type !== 'class' ? tabs2 : tabs">
          <template #default="props">
            <div class="flex flex-col items-center gap-2 p-2">
              <ElIcon :size="18" class="mt-2">
                <Component :is="(props.item as SegmentedTab).icon" />
              </ElIcon>
              {{ t(('manage.groupDetails.tabs.' + (props.item as SegmentedTab).value) as string) }}
            </div>
          </template>
        </ElSegmented>
      </template>
    </ElPageHeader>
    <ZGroupUserList v-if="tab === 'users'" :id="id" />
    <ZActivityList v-else-if="tab === 'activities'" :class-target="id" role="class" />
    <ZGroupUserTimeList v-else-if="tab === 'time'" :id="id" />
    <ZUserModification v-else-if="tab === 'create'" :cid="id" mode="create" id="" />
    <ZGroupTimeStatistics v-else-if="tab === 'statistics'" :group-id="id" />
  </div>
</template>

<style scoped>
.page-container :deep(.el-page-header) {
  flex-wrap: wrap;
  row-gap: 8px;
}

.page-container :deep(.el-page-header__content) {
  font-weight: 600;
}
</style>
