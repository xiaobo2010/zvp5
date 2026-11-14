<script setup lang="ts">
import { RouterView, useRoute, useRouter } from 'vue-router'
import { useUserStore } from '@/stores/user.ts'
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import Segmented from '@/ui/ux/Segmented.vue'


interface SegmentedTab {
  label: string
  value: string
}

const route = useRoute()
const router = useRouter()
const userStore = useUserStore()
const { t } = useI18n()

if (!userStore.position.includes('admin')) {
  router.replace('/not-found')
}

const current = ref('maintenance')

const segmentItems = computed(() =>
  tabs.value.map((item) => ({
    value: item.value,
    label: t(('server.tabs.' + item.value) as string)
  }))
)

const tabs = ref<SegmentedTab[]>([
  {
    label: 'Maintenance',
    value: 'maintenance'
  },
  {
    label: 'Database',
    value: 'database'
  },
  {
    label: 'Totp',
    value: 'totp'
  },
  {
    label: 'Ssh',
    value: 'ssh'
  },
  {
    label: 'Files',
    value: 'files'
  },
  {
    label: 'Content',
    value: 'dbContent'
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
  <div class="yg2-page server-page">
    <button class="server-back" type="button" @click="router.back()">
      <span aria-hidden="true">‹</span> 返回
    </button>

    <header class="yg2-page-header">
      <div>
        <p class="yg2-page-header__eyebrow">服务器管理</p>
        <h1 class="yg2-page-header__title">{{ t('server.tabs.' + current) }}</h1>
      </div>
    </header>

    <Segmented
      v-model="current"
      class="server-tabs"
      :items="segmentItems"
      aria-label="服务器管理"
    />

    <RouterView />
  </div>
</template>

<style scoped>
.server-page {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.server-back {
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

.server-back:hover {
  background: var(--yg2-surface-sunken);
  color: var(--yg2-text);
}

.server-tabs {
  align-self: flex-start;
  max-width: 100%;
  overflow-x: auto;
  scrollbar-width: none;
}

.server-tabs::-webkit-scrollbar {
  display: none;
}
</style>
