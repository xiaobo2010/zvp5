<script lang="ts" setup>
import ZGroupUserList from '@/components/group/ZGroupUserList.vue'
import { useRoute, useRouter } from 'vue-router'

import { computed, defineAsyncComponent, ref, watch } from 'vue'
import api from '@/api'
import type { Group } from '@/../types'
import { useWindowSize } from '@vueuse/core'
import { useUserStore } from '@/stores/user.ts'
import { useGroupsStore } from '@/stores/groups'
import { ZActivityList } from '@/components'
import ZGroupUserTimeList from '@/components/group/ZGroupUserTimeList.vue'
import { useI18n } from 'vue-i18n'
import Segmented from '@/ui/ux/Segmented.vue'
import ZUserModification from '@/components/group/ZUserModification.vue'
import { AddUser, User, ViewList } from '@icon-park/vue-next'
import { TablerSum } from '@/icons'
import UilStatistics from '@/icons/UilStatistics.vue'
// 统计 tab 才用 chart.js：静态导入会让 190KB 的图表块进入组页首屏，改成按需加载。
const ZGroupTimeStatistics = defineAsyncComponent(
  () => import('@/components/group/ZGroupTimeStatistics.vue')
)

interface SegmentedTab {
  label: string
  value: string
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
  },
  {
    label: 'Activities',
    value: 'activities',
  },
  {
    label: 'Time',
    value: 'time',
  },
  {
    label: 'Statistics',
    value: 'statistics',
  },
])

if (userStore.position.includes('admin')) {
  tabs.value[tabs.value.length] = {
    label: 'Create',
    value: 'create',
  }
}

/*
if (group.value?.type !== 'class') {
  tabs.value = [
    {
      label: 'Users',
      value: 'users',
    },
  ]
}
*/

const tabs2 = ref<SegmentedTab[]>([
  {
    label: 'Users',
    value: 'users',
  },
])

const curPage = route.params.action?.toString()

const tab = ref(curPage && curPage !== '' ? curPage : 'users')

const segmentItems = computed(() =>
  (group.value?.type !== 'class' ? tabs2.value : tabs.value).map((item) => ({
    value: item.value,
    label: t(('manage.groupDetails.tabs.' + item.value) as string)
  }))
)

watch(tab, () => {
  router.replace('/group/' + id.value + '/' + (tab.value ?? 'users'))
})
</script>

<template>
  <div v-if="id" class="yg2-page group-page">
    <button class="group-back" type="button" @click="router.back()">
      <span aria-hidden="true">‹</span> 返回
    </button>

    <header class="yg2-page-header">
      <div>
        <p class="yg2-page-header__eyebrow">
          {{ group?.type === 'class' ? '行政班' : '小组' }}
        </p>
        <h1 class="yg2-page-header__title">{{ group?.name || '…' }}</h1>
        <p class="yg2-page-header__subtitle">
          {{ group?.description || group?._id }}
        </p>
      </div>
    </header>

    <Segmented v-model="tab" class="group-tabs" :items="segmentItems" aria-label="小组面板" />
    <div class="yg2-panel">
      <ZGroupUserList v-if="tab === 'users'" :id="id" />
      <ZActivityList v-else-if="tab === 'activities'" :class-target="id" role="class" embed />
      <ZGroupUserTimeList v-else-if="tab === 'time'" :id="id" />
      <ZUserModification v-else-if="tab === 'create'" :cid="id" mode="create" id="" />
      <ZGroupTimeStatistics v-else-if="tab === 'statistics'" :group-id="id" />
    </div>
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

<style scoped>
.group-page {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.group-back {
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

.group-back:hover {
  background: var(--yg2-surface-sunken);
  color: var(--yg2-text);
}

.group-tabs {
  align-self: flex-start;
}
</style>
