<script setup lang="ts">
import { RouterView, useRoute, useRouter } from 'vue-router'
import { useUserStore } from '@/stores/user.ts'
import { ref, watch, type Component } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElButton, ElSegmented, ElIcon } from 'element-plus'
import { ArrowLeft, Setting, DataLine, Key, Connection, Folder, Grid } from '@element-plus/icons-vue'

interface SegmentedTab {
  label: string
  value: string
  icon: Component
}

const route = useRoute()
const router = useRouter()
const userStore = useUserStore()
const { t } = useI18n()

if (!userStore.position.includes('admin')) {
  router.replace('/not-found')
}

const current = ref('maintenance')

const tabs = ref<SegmentedTab[]>([
  {
    label: 'Maintenance',
    value: 'maintenance',
    icon: Setting
  },
  {
    label: 'Database',
    value: 'database',
    icon: DataLine
  },
  {
    label: 'Totp',
    value: 'totp',
    icon: Key
  },
  {
    label: 'Ssh',
    value: 'ssh',
    icon: Connection
  },
  {
    label: 'Files',
    value: 'files',
    icon: Folder
  },
  {
    label: 'Content',
    value: 'dbContent',
    icon: Grid
  },
])

// 分段导航与子路由双向同步:子页 path 即 tab value
watch(
  () => route.path,
  () => {
    const seg = route.path.split('/')
    const isDbContent = seg[1] === 'server' && seg[2] === 'db' && seg[3] === 'content'
    current.value = seg[1] === 'server' && seg[2] ? (isDbContent ? 'dbContent' : seg[2]) : 'maintenance'
  },
  { immediate: true }
)

watch(current, () => {
  if (current.value === 'dbContent') {
    router.replace('/server/db/content')
    return
  }
  router.replace('/server/' + current.value)
})
</script>

<template>
  <div class="px-4 py-4">
    <div class="flex items-center gap-3 mb-4">
      <ElButton text bg :icon="ArrowLeft" @click="() => router.back()" />
      <ElSegmented v-model="current" :options="tabs" class="flex-1 min-w-0 w-full">
        <template #default="props">
          <div class="flex flex-col items-center gap-2 p-2">
            <ElIcon :size="18" class="mt-2">
              <Component :is="(props.item as SegmentedTab).icon" />
            </ElIcon>
            {{ t(('server.tabs.' + (props.item as SegmentedTab).value) as string) }}
          </div>
        </template>
      </ElSegmented>
    </div>
    <RouterView />
  </div>
</template>