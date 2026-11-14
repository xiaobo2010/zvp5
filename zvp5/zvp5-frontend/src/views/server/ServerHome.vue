<script setup lang="ts">
import { RouterView, useRoute, useRouter } from 'vue-router'
import { useUserStore } from '@/stores/user.ts'
import { computed, nextTick, onMounted, ref, watch, type Component } from 'vue'
import { useI18n } from 'vue-i18n'
import { useWindowSize } from '@vueuse/core'
import { ElPageHeader } from 'element-plus/es/components/page-header/index.mjs'
import { ElSegmented } from 'element-plus/es/components/segmented/index.mjs'
import { ElIcon } from 'element-plus/es/components/icon/index.mjs'
import { ArrowLeft, Setting, DataLine, Key, Connection, Folder, Grid } from '@element-plus/icons-vue'
import ZHexGrid from '@/components/decor/ZHexGrid.vue'
import { prefetchRoute, prefetchRoutesWhenIdle } from '@/utils/routePrefetch'

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

const tabs: SegmentedTab[] = [
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
  }
]

/** 合法分区；非法深链一律回落 maintenance，不把脏值写回地址栏。 */
const VALID_TABS = new Set(tabs.map((tab) => tab.value))
const HOME_TAB = 'maintenance'

function tabOfPath(path: string): string {
  const seg = path.split('/')
  if (seg[1] !== 'server' || !seg[2]) return HOME_TAB
  const value = seg[2] === 'db' && seg[3] === 'content' ? 'dbContent' : seg[2]
  return VALID_TABS.has(value) ? value : HOME_TAB
}

function pathOfTab(value: string): string {
  return value === 'dbContent' ? '/server/db/content' : '/server/' + value
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

watch(current, (value) => {
  const target = pathOfTab(value)
  if (route.path !== target) {
    router.replace(target)
  }
})

/* 与 App.vue / UserNav 同一判定：宽 < 高 × 1.2 即竖屏 */
const { width, height } = useWindowSize()
const verticalMode = computed(() => width.value < height.value * 1.2)

/** 竖屏或窄屏一律改用胶囊导航；≥1024 的横屏保留原分段控件 */
const compactTabs = computed(() => verticalMode.value || width.value < 1024)

// 只有终端与文件两页在紧凑布局下需要吃满可用高度：
// 高度交给 flex 按实际可用空间分配,不再拍脑袋算 100vh - 固定 rem。
const FILL_ROUTES = new Set(['ssh', 'files'])
const isFill = computed(() => compactTabs.value && FILL_ROUTES.has(current.value))

const tabsNavRef = ref<HTMLElement | null>(null)

/** 当前项自动滚入可视区（横向居中，不影响页面纵向滚动） */
function scrollActiveTabIntoView() {
  const nav = tabsNavRef.value
  if (!nav) {
    return
  }
  const active = nav.querySelector<HTMLElement>('.server-tabs__pill.is-active')
  if (!active) {
    return
  }
  const target = active.offsetLeft - (nav.clientWidth - active.offsetWidth) / 2
  const reduced = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false
  // 老浏览器的 Element.scrollTo 缺失时直接跳过,不影响 tab 切换本身
  nav.scrollTo?.({ left: Math.max(0, target), behavior: reduced ? 'auto' : 'smooth' })
}

watch(
  [current, compactTabs],
  () => {
    void nextTick(() => {
      scrollActiveTabIntoView()
    })
  },
  { immediate: true }
)

/* ---------- 响应速度：分区 chunk 预取 ----------
   六个分区各自是懒加载 chunk，点下去才开始下载，等待期间页面是空的。
   空闲时把六个 chunk 先拉下来（预取失败静默吞掉，不影响导航），
   指针悬停时再即时预取一次——tap 到 click 的百毫秒足够命中本地缓存。 */
const tabPaths = tabs.map((tab) => pathOfTab(tab.value))

onMounted(() => {
  prefetchRoutesWhenIdle(router, tabPaths)
})

function prefetchTab(value: string): void {
  prefetchRoute(router, pathOfTab(value))
}
</script>

<template>
  <div
    class="server-shell phi-srv"
    :class="[compactTabs ? 'px-4 py-3' : 'px-6 py-4', { 'server-shell--fill': isFill }]"
  >
    <!-- 蜂窝底纹：与封禁页同一套仪器感底纹，管理台用主题强调色，压在内容之下 -->
    <ZHexGrid
      :size="64"
      color="var(--yg-text-primary)"
      :opacity="0.055"
      drift
      sweep
      :z-index="-1"
    />
    <!-- 紧凑布局：页头（返回 + 标题）与胶囊导航分两行 -->
    <template v-if="compactTabs">
      <header class="server-head">
        <button
          type="button"
          class="yg-icon-btn server-head__back"
          :aria-label="t('server.tabs.back')"
          @click="router.back()"
        >
          <ElIcon :size="20">
            <ArrowLeft />
          </ElIcon>
        </button>
        <h1 class="server-head__title">
          {{ t(('server.tabs.' + current) as string) }}
        </h1>
      </header>

      <nav
        ref="tabsNavRef"
        class="server-tabs yg-liquid-glass"
        role="tablist"
        :aria-label="t('server.tabs.tabsLabel')"
      >
        <button
          v-for="tab in tabs"
          :key="tab.value"
          type="button"
          role="tab"
          class="server-tabs__pill"
          :class="{ 'is-active': tab.value === current }"
          :aria-selected="tab.value === current"
          @click="current = tab.value"
          @pointerenter="prefetchTab(tab.value)"
        >
          <ElIcon :size="18">
            <Component :is="tab.icon" />
          </ElIcon>
          <span class="server-tabs__label">{{ t(('server.tabs.' + tab.value) as string) }}</span>
        </button>
      </nav>
    </template>

    <!-- 桌面：页头标题 + 右侧分段控件 -->
    <ElPageHeader v-else :icon="ArrowLeft" class="py-4" @back="() => router.back()">
      <template #content>
        {{ t(('server.tabs.' + current) as string) }}
      </template>
      <template #extra>
        <ElSegmented v-model="current" :options="tabs">
          <template #default="props">
            <div class="server-tabs__seg-item">
              <ElIcon :size="18" class="server-tabs__seg-icon">
                <Component :is="(props.item as SegmentedTab).icon" />
              </ElIcon>
              <span>{{ t(('server.tabs.' + (props.item as SegmentedTab).value) as string) }}</span>
            </div>
          </template>
        </ElSegmented>
      </template>
    </ElPageHeader>

    <!-- HUD 读数条：把「现在在哪个分区」用仪表盘的方式标出来 -->
    <div class="phi-srv-rail server-rail">
      <span class="phi-srv-rail__item">{{ t('server.hud.rail') }}</span>
      <span class="phi-srv-rail__item phi-srv-rail__item--muted">
        {{ t(('server.tabs.' + current) as string) }}
      </span>
    </div>

    <div class="server-body">
      <RouterView />
    </div>
  </div>
</template>

<style scoped>
/* ---------- 服务器页外壳 ---------- */
.server-shell {
  position: relative;
  /* 建立层叠上下文：蜂窝底纹 z-index:-1 落在页面底色之上、内容之下 */
  isolation: isolate;
  display: flex;
  flex-direction: column;
  min-height: 0;
  /* 底部让位不在这儿另算一份:外壳(App.vue)已经把药丸占位算进
     --yg-content-bottom-inset 并挂在 .yg-layout__content 上,
     该变量随 DOM 继承,本页(及子页)直接 var(--yg-content-bottom-inset) 即可。 */
}

/* 填满模式:紧凑布局下终端/文件页吃满内容区,高度由 flex 分配。
   外壳的 padding-bottom 已经把药丸占位让出来,页面只要填满剩下的盒子,
   底边必然在药丸上方。 */
.server-shell--fill {
  height: 100%;
  min-height: calc(
    100vh
    - var(--yg-header-height)
    - env(safe-area-inset-top, 0px)
    - var(--yg-space-6)
    - var(--yg-content-bottom-inset, 96px)
    - var(--yg-space-2)
    - var(--yg-space-2)
  );
}

@supports (height: 100dvh) {
  .server-shell--fill {
    min-height: calc(
      100dvh
      - var(--yg-header-height)
      - env(safe-area-inset-top, 0px)
      - var(--yg-space-6)
      - var(--yg-content-bottom-inset, 96px)
      - var(--yg-space-2)
      - var(--yg-space-2)
    );
  }
}

/* 紧凑页头：返回按钮与标题同行 */
.server-head {
  display: flex;
  align-items: center;
  gap: var(--yg-space-3);
  margin-bottom: var(--yg-space-3);
}

.server-head__back {
  flex: 0 0 auto;
}

.server-head__title {
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
.server-tabs {
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
  -webkit-backdrop-filter: blur(var(--yg-glass-blur-sm)) saturate(180%);
  backdrop-filter: blur(var(--yg-glass-blur-sm)) saturate(180%);
}

.server-tabs::-webkit-scrollbar {
  display: none;
}

.server-tabs__pill {
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

.server-tabs__pill:hover {
  color: var(--yg-text-primary);
  background-color: var(--yg-bg-hover);
}

.server-tabs__pill:active {
  background-color: var(--yg-bg-active);
}

.server-tabs__pill:focus-visible {
  outline: none;
  box-shadow: var(--yg-focus-ring);
}

.server-tabs__pill.is-active {
  color: var(--yg-color-primary);
  background-color: var(--yg-color-primary-soft);
  box-shadow: var(--yg-shadow-sm);
  font-weight: var(--yg-font-weight-medium);
}

/* 桌面分段控件里的单项：图标在上、文字在下 */
.server-tabs__seg-item {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--yg-space-2);
  padding: var(--yg-space-1) var(--yg-space-2);
}

.server-tabs__seg-icon {
  margin-top: var(--yg-space-2);
}

/* HUD 读数条：紧贴页头下方，给出仪表盘读数的存在感 */
.server-rail {
  margin-bottom: var(--yg-space-3);
}

.server-body {
  display: flex;
  flex-direction: column;
  flex: 1;
  min-height: 0;
}
</style>

