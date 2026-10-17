<script setup lang="ts">
import { RouterView, useRoute, useRouter } from 'vue-router'
import { useUserStore } from '@/stores/user.ts'
import { ref, watch, type Component } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElPageHeader, ElSegmented, ElIcon } from 'element-plus'
import { ArrowLeft, Setting, DataLine } from '@element-plus/icons-vue'

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
])

// 分段导航与子路由双向同步:子页 path 即 tab value
watch(
  () => route.path,
  () => {
    const seg = route.path.split('/')
    current.value = seg[1] === 'server' && seg[2] ? seg[2] : 'maintenance'
  },
  { immediate: true }
)

watch(current, () => {
  router.replace('/server/' + current.value)
})
</script>

<template>
  <div class="px-16 py-8">
    <ElPageHeader :icon="ArrowLeft" @back="() => router.back()" class="py-4">
      <template #content>
        {{ t('server.tabs.' + current) }}
      </template>
      <template #extra>
        <ElSegmented v-model="current" :options="tabs">
          <template #default="props">
            <div class="flex flex-col items-center gap-2 p-2">
              <ElIcon :size="18" class="mt-2">
                <Component :is="(props.item as SegmentedTab).icon" />
              </ElIcon>
              {{ t(('server.tabs.' + (props.item as SegmentedTab).value) as string) }}
            </div>
          </template>
        </ElSegmented>
      </template>
    </ElPageHeader>
    <RouterView />
  </div>
</template>