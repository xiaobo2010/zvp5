<script setup lang="ts">
import { RouterView, useRoute, useRouter } from 'vue-router'
import { useUserStore } from '@/stores/user.ts'
import { computed, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useWindowSize } from '@vueuse/core'
import { prefetchRoute, prefetchRoutesWhenIdle } from '@/utils/routePrefetch'
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

/* 分区 → 路由 path 的唯一真源：深链归一、点击跳转、分区预取都从这里取。 */
const TAB_PATHS: Record<string, string> = {
  maintenance: '/server/maintenance',
  database: '/server/database',
  totp: '/server/totp',
  ssh: '/server/ssh',
  files: '/server/files',
  dbContent: '/server/db/content'
}

/** 合法分区；非法深链一律回落 maintenance，不把脏值写回地址栏。 */
const VALID_TABS = new Set(Object.keys(TAB_PATHS))
const HOME_TAB = 'maintenance'

function pathOfTab(value: string): string {
  return TAB_PATHS[value] ?? TAB_PATHS[HOME_TAB]
}

function tabOfPath(path: string): string {
  const seg = path.split('/')
  if (seg[1] !== 'server' || !seg[2]) return HOME_TAB
  const value = seg[2] === 'db' && seg[3] === 'content' ? 'dbContent' : seg[2]
  return VALID_TABS.has(value) ? value : HOME_TAB
}

// 分段导航与子路由双向同步:子页 path 即 tab value
watch(
  () => route.path,
  (path) => {
    const tab = tabOfPath(path)
    if (current.value !== tab) {
      current.value = tab
      return
    }
    // 非法深链:地址栏归一到入口分区
    if (path !== pathOfTab(tab)) {
      router.replace(pathOfTab(tab))
    }
  },
  { immediate: true }
)

watch(current, () => {
  router.replace(pathOfTab(current.value))
})

/* 与 App.vue 同一判定：宽 < 高 × 1.2 即竖屏；窄屏也一律换成可横滑的药丸导航。 */
const { width, height } = useWindowSize()
const compactTabs = computed(() => width.value < height.value * 1.2 || width.value < 1024)

/* 六个分区各自是懒加载 chunk：空闲时先拉下来，点击不再白屏。 */
onMounted(() => {
  prefetchRoutesWhenIdle(
    router,
    tabs.value.map((tab) => pathOfTab(tab.value))
  )
})

function prefetchTab(value: string): void {
  prefetchRoute(router, pathOfTab(value))
}
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

    <!-- 紧凑（平板竖屏 / 窄屏）：可横滑的药丸导航 -->
    <nav v-if="compactTabs" class="server-tabs" role="tablist" aria-label="服务器管理">
      <button
        v-for="item in tabs"
        :key="item.value"
        role="tab"
        type="button"
        class="server-tabs__pill"
        :class="{ 'is-active': current === item.value }"
        :aria-selected="current === item.value"
        @click="current = item.value"
        @pointerenter="prefetchTab(item.value)"
      >
        {{ t('server.tabs.' + item.value) }}
      </button>
    </nav>
    <Segmented
      v-else
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
  display: flex;
  gap: 8px;
  overflow-x: auto;
  scrollbar-width: none;
}

.server-tabs::-webkit-scrollbar {
  display: none;
}

.server-tabs__pill {
  flex: 0 0 auto;
  height: 32px;
  padding: 0 14px;
  border: 1px solid var(--yg2-line);
  border-radius: var(--yg2-r-pill);
  background: var(--yg2-surface);
  color: var(--yg2-text-secondary);
  font: inherit;
  font-size: 14px;
  font-weight: 500;
  white-space: nowrap;
  cursor: pointer;
  transition: background var(--yg2-dur-1) var(--yg2-ease-out),
    color var(--yg2-dur-1) var(--yg2-ease-out);
}

.server-tabs__pill.is-active {
  background: var(--yg2-surface-sunken);
  color: var(--yg2-text);
}

.server-back {
  align-self: flex-start;
  max-width: 100%;
  overflow-x: auto;
  scrollbar-width: none;
}

.server-tabs::-webkit-scrollbar {
  display: none;
}
</style>
