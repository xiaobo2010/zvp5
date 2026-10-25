<script setup lang="ts">
import { RouterView, useRoute, useRouter } from 'vue-router'
import { useUserStore } from '@/stores/user.ts'
import { computed, nextTick, onMounted, ref, watch, type Component } from 'vue'
import { useI18n } from 'vue-i18n'
import { useMediaQuery } from '@vueuse/core'
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

// 竖屏判定与 App.vue / UserNav.vue 同阈值(宽高比 ≤ 6/5):
// 竖屏时底部换成悬浮药丸导航,页面必须给它让位;
// 横屏是左侧玻璃栏,底部没有遮挡,留白按桌面观感走。
const isPortrait = useMediaQuery('(max-aspect-ratio: 6/5)')

// 只有终端与文件两页在竖屏下需要吃满可用高度:
// 高度交给 flex 按实际可用空间分配,不再拍脑袋算 100vh - 固定 rem。
const FILL_ROUTES = new Set(['ssh', 'files'])
const isFill = computed(() => isPortrait.value && FILL_ROUTES.has(current.value))

const shellRef = ref<HTMLElement>()

// 竖屏下分段导航是一条横向可滚动的胶囊带,选中项可能在可视区外,滚进来
function revealActiveTab() {
  const el = shellRef.value?.querySelector<HTMLElement>('.el-segmented__item.is-selected')
  el?.scrollIntoView({ block: 'nearest', inline: 'nearest' })
}

onMounted(() => {
  revealActiveTab()
})

watch(isPortrait, () => {
  nextTick(revealActiveTab)
})

watch(current, () => {
  nextTick(revealActiveTab)
})
</script>

<template>
  <div
    ref="shellRef"
    class="server-shell px-4 py-4"
    :class="{ 'server-shell--fill': isFill }"
  >
    <div class="server-tabs mb-4 flex items-center gap-3">
      <ElButton text bg :icon="ArrowLeft" class="server-tabs__back" @click="() => router.back()" />
      <ElSegmented v-model="current" :options="tabs" class="server-tabs__seg min-w-0 flex-1">
        <template #default="props">
          <div class="server-tabs__item">
            <ElIcon :size="18" class="server-tabs__icon">
              <Component :is="(props.item as SegmentedTab).icon" />
            </ElIcon>
            <span class="server-tabs__label">
              {{ t(('server.tabs.' + (props.item as SegmentedTab).value) as string) }}
            </span>
          </div>
        </template>
      </ElSegmented>
    </div>
    <div class="server-body">
      <RouterView />
    </div>
  </div>
</template>

<style scoped>
/* ---------- 服务器页外壳 ---------- */
.server-shell {
  display: flex;
  flex-direction: column;
  min-height: 0;
  /* 底部让位不在这儿另算一份:外壳(App.vue)已经把药丸占位算进
     --yg-content-bottom-inset 并挂在 .yg-layout__content 上,
     该变量随 DOM 继承,本页(及子页)直接 var(--yg-content-bottom-inset) 即可。 */
}

/* 竖屏:底部是悬浮药丸,页面整体收紧留白 */
@media (max-aspect-ratio: 6/5) {
  .server-shell {
    /* 外壳内容区已经有 24px 内边距,这里只留 8px,避免双份留白 */
    padding: var(--yg-space-2);
  }
}

/* 填满模式:竖屏下终端/文件页吃满内容区,高度由 flex 分配。
   外壳的 padding-bottom 已经把药丸占位让出来,页面只要填满剩下的盒子,
   底边必然在药丸上方。

   高度来源有两条,互为冗余:
   1) height:100% —— 外壳 .yg-layout__content 是 flex:1 的弹性项,
      高度确定(不是靠 min-height 撑出来的),所以百分比能解析成确定高度。
   2) min-height —— 万一百分比链断了(外壳改成 height:auto、或换布局),
      这一行仍然给出确定高度,终端/表格不会塌成 min-height 下限。
      视口基准:100vh 起,支持 dvh 的浏览器改用 100dvh(移动端地址栏收起时更准)。 */
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

.server-tabs {
  flex: 0 0 auto;
}

.server-body {
  display: flex;
  flex-direction: column;
  flex: 1 1 auto;
  min-height: 0;
}

/* ---------- 分段导航 ---------- */
.server-tabs__item {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--yg-space-2);
  padding: var(--yg-space-2);
}

.server-tabs__icon {
  margin-top: var(--yg-space-2);
}

/* 竖屏:六项导航压成一条横向可滚动的胶囊带,不换行、不溢出 */
@media (max-aspect-ratio: 6/5) {
  .server-tabs {
    gap: var(--yg-space-2);
    margin-bottom: var(--yg-space-2);
  }

  .server-tabs__back {
    flex: 0 0 auto;
    width: 44px;
    height: 44px;
  }

  .server-tabs__seg {
    overflow-x: auto;
    overflow-y: hidden;
    -webkit-overflow-scrolling: touch;
    scrollbar-width: none;
  }

  .server-tabs__seg::-webkit-scrollbar {
    display: none;
  }

  .server-tabs__seg :deep(.el-segmented__group) {
    width: max-content;
    min-width: 100%;
  }

  .server-tabs__seg :deep(.el-segmented__item) {
    flex: 0 0 auto;
    min-height: 44px;
  }

  .server-tabs__seg :deep(.el-segmented__item-label) {
    flex: 0 0 auto;
    overflow: visible;
  }

  .server-tabs__item {
    flex-direction: row;
    align-items: center;
    gap: var(--yg-space-1);
    padding: 0 var(--yg-space-1);
    white-space: nowrap;
  }

  .server-tabs__icon {
    margin-top: 0;
  }
}
</style>
