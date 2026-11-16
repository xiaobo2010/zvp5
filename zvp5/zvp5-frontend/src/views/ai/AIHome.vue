<script setup lang="ts">
import { useRoute, useRouter } from 'vue-router'
import { useUserStore } from '@/stores/user.ts'
import { computed, nextTick, ref, watch, type Component } from 'vue'
import { useI18n } from 'vue-i18n'
import { useWindowSize } from '@vueuse/core'
import { ElIcon } from 'element-plus/es/components/icon/index.mjs'
import {
  AlarmClock,
  ArrowLeft,
  Box,
  Coin,
  Cpu,
  DataLine,
  Key,
  Monitor,
  Odometer,
  Tickets
} from '@element-plus/icons-vue'
import ZAiChat from '@/components/ai/ZAiChat.vue'
import ZAiQuotaPanel from '@/components/ai/ZAiQuotaPanel.vue'
import ZAiKeysAdmin from '@/components/ai/ZAiKeysAdmin.vue'
import ZAiOcrEnginesAdmin from '@/components/ai/ZAiOcrEnginesAdmin.vue'
import ZAiModelsAdmin from '@/components/ai/ZAiModelsAdmin.vue'
import ZAiQuotaAdmin from '@/components/ai/ZAiQuotaAdmin.vue'
import ZAiHealthPanel from '@/components/ai/ZAiHealthPanel.vue'
import ZAiLogsAdmin from '@/components/ai/ZAiLogsAdmin.vue'
import ZAiPromptAudit from '@/components/ai/ZAiPromptAudit.vue'
import ZAiScheduledTasks from '@/components/ai/ZAiScheduledTasks.vue'

interface GatewayTab {
  label: string
  value: string
  icon: Component
}

const VALID_CURRENT = [
  'chat',
  'quota',
  'scheduled',
  'keys',
  'engines',
  'models',
  'quota-admin',
  'health',
  'logs',
  'monitor'
]
const ADMIN_ONLY = ['keys', 'engines', 'models', 'quota-admin', 'health', 'logs', 'monitor']

const route = useRoute()
const router = useRouter()
const userStore = useUserStore()
const { t } = useI18n()

const isAdmin = userStore.position.includes('admin')
const canUseAi = isAdmin || userStore.position.includes('department')

if (!canUseAi) {
  router.replace('/not-found')
}

const curPage = route.params.action?.toString()

function isVisible(value: string): boolean {
  if (ADMIN_ONLY.includes(value)) {
    return isAdmin
  }
  return canUseAi
}

if (curPage && (!VALID_CURRENT.includes(curPage) || !isVisible(curPage))) {
  router.replace('/ai/chat')
}

const current = ref(
  curPage && VALID_CURRENT.includes(curPage) && isVisible(curPage) ? curPage : 'chat'
)

const tabs = ref<GatewayTab[]>([
  {
    label: 'chat',
    value: 'chat',
    icon: Cpu
  },
  {
    label: 'quota',
    value: 'quota',
    icon: Coin
  },
  {
    label: 'scheduled',
    value: 'scheduled',
    icon: AlarmClock
  }
])

if (isAdmin) {
  tabs.value.push({ label: 'keys', value: 'keys', icon: Key })
  tabs.value.push({ label: 'engines', value: 'engines', icon: Cpu })
  tabs.value.push({ label: 'models', value: 'models', icon: Box })
  tabs.value.push({ label: 'quota-admin', value: 'quota-admin', icon: DataLine })
  tabs.value.push({ label: 'health', value: 'health', icon: Odometer })
  tabs.value.push({ label: 'logs', value: 'logs', icon: Tickets })
  tabs.value.push({ label: 'monitor', value: 'monitor', icon: Monitor })
}

watch(current, () => {
  router.replace('/ai/' + current.value)
})

watch(
  () => route.params.action,
  () => {
    const next = route.params.action?.toString()
    if (next && VALID_CURRENT.includes(next) && isVisible(next)) {
      current.value = next
    } else {
      router.replace('/ai/chat')
    }
  }
)

/* 竖屏或窄屏用紧凑页头 + 胶囊导航；≥1024 横屏用扁平页头 + 下划线分区导航。 */
const { width, height } = useWindowSize()
const verticalMode = computed(() => width.value < height.value * 1.2)
const compactTabs = computed(() => verticalMode.value || width.value < 1024)

const tabsNavRef = ref<HTMLElement | null>(null)

function scrollActiveTabIntoView() {
  const nav = tabsNavRef.value
  if (!nav) {
    return
  }
  const active = nav.querySelector<HTMLElement>('.ai-tabs__pill.is-active')
  if (!active) {
    return
  }
  const target = active.offsetLeft - (nav.clientWidth - active.offsetWidth) / 2
  const reduced = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false
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
</script>

<template>
  <div
    class="yg-ai-home"
    :class="[
      compactTabs ? 'px-4 py-3' : 'px-6 py-4',
      { 'yg-ai-home--chat': current === 'chat', 'is-compact': compactTabs }
    ]"
  >
    <!-- 竖屏/窄屏：紧凑页头 + 胶囊导航，与服务器/平台管理同一套外壳 -->
    <template v-if="compactTabs">
      <header class="ai-head">
        <button
          type="button"
          class="yg-icon-btn ai-head__back"
          :aria-label="t('ai.tabs.back')"
          @click="router.back()"
        >
          <ElIcon :size="20">
            <ArrowLeft />
          </ElIcon>
        </button>
        <h1 class="ai-head__title">{{ t('ai.tabs.' + current) }}</h1>
      </header>

      <nav
        ref="tabsNavRef"
        class="ai-tabs yg-liquid-glass"
        role="tablist"
        :aria-label="t('ai.tabs.tabsLabel')"
      >
        <button
          v-for="tab in tabs"
          :key="tab.value"
          type="button"
          role="tab"
          class="ai-tabs__pill"
          :class="{ 'is-active': tab.value === current }"
          :aria-selected="tab.value === current"
          @click="current = tab.value"
        >
          <ElIcon :size="18">
            <Component :is="tab.icon" />
          </ElIcon>
          <span class="ai-tabs__label">{{ t('ai.tabs.' + tab.value) }}</span>
        </button>
      </nav>
    </template>

    <!-- 桌面：扁平页头 + 底部主色下划线的分区导航 -->
    <template v-else>
      <header class="yg-ai-home__header ai-gateway-head">
        <div class="ai-gateway-head__intro">
          <button
            type="button"
            class="yg-icon-btn yg-icon-btn--ghost ai-gateway-head__back"
            :aria-label="t('ai.tabs.back')"
            @click="router.back()"
          >
            <ElIcon :size="20">
              <ArrowLeft />
            </ElIcon>
          </button>
          <div class="ai-gateway-head__titles">
            <h1 class="ai-gateway-head__title">{{ t('ai.title') }}</h1>
            <p class="ai-gateway-head__subtitle">{{ t('ai.subtitle') }}</p>
          </div>
        </div>
      </header>

      <nav
        class="ai-gateway-tabs"
        role="tablist"
        :aria-label="t('ai.tabs.tabsLabel')"
      >
        <button
          v-for="tab in tabs"
          :key="tab.value"
          type="button"
          role="tab"
          class="ai-gateway-tab"
          :class="{ 'is-active': tab.value === current }"
          :aria-selected="tab.value === current"
          @click="current = tab.value"
        >
          <ElIcon :size="17">
            <Component :is="tab.icon" />
          </ElIcon>
          <span>{{ t('ai.tabs.' + tab.value) }}</span>
        </button>
      </nav>
    </template>

    <div class="yg-ai-home__panel">
      <ZAiChat v-if="current === 'chat'" />
      <ZAiQuotaPanel v-else-if="current === 'quota'" />
      <ZAiScheduledTasks v-else-if="current === 'scheduled'" />
      <ZAiKeysAdmin v-else-if="current === 'keys'" />
      <ZAiOcrEnginesAdmin v-else-if="current === 'engines'" />
      <ZAiModelsAdmin v-else-if="current === 'models'" />
      <ZAiQuotaAdmin v-else-if="current === 'quota-admin'" />
      <ZAiHealthPanel v-else-if="current === 'health'" />
      <ZAiLogsAdmin v-else-if="current === 'logs'" />
      <ZAiPromptAudit v-else-if="current === 'monitor'" />
    </div>
  </div>
</template>

<style scoped>
.yg-ai-home {
  /* 撑满内容区：对话页要在视口内把「消息区 + 输入区」分完，
     不让输入区被长消息顶到屏幕外。
     扣掉的量 = 顶栏 + 内容区上内边距 + 内容区下内边距；
     竖屏底部有悬浮药丸导航，--yg-content-bottom-inset 由外壳给值（横屏为 0），
     所以下内边距取两者的较大值。 */
  --yg-ai-viewport-inset: calc(
    var(--yg-header-height) + env(safe-area-inset-top, 0px) + var(--yg-space-6) +
      max(var(--yg-space-6), var(--yg-content-bottom-inset, 0px))
  );

  display: flex;
  flex-direction: column;
  gap: var(--yg-space-4);
  min-height: calc(100vh - var(--yg-ai-viewport-inset));
  /* 动态视口高度：移动端浏览器收起地址栏时不留多余空白 */
  min-height: calc(100dvh - var(--yg-ai-viewport-inset));
}

/*
 * 对话页：高度锁在内容区里。
 *
 * 只给 min-height 时，消息一长整页就会滚起来，输入区被顶到屏幕外；
 * 这里把高度定死、外层不滚，滚动交给消息区自己。
 *
 * max-height: 100% 以真实容器（.yg-layout__content 的内容盒）为准：
 * 离线横幅一出现，内容区就比视口矮，此时对话区必须跟着容器缩，
 * 而不是继续按视口算的高度把外层撑出滚动条。
 */
.yg-ai-home--chat {
  height: calc(100vh - var(--yg-ai-viewport-inset));
  height: calc(100dvh - var(--yg-ai-viewport-inset));
  max-height: 100%;
  min-height: 0;
  overflow: hidden;
}

/*
 * 页头与页签是固定件：不参与纵向压缩。
 *
 * 页签容器是横向滚动容器（overflow-x: auto），在 flex 里它的自动最小尺寸是 0，
 * 对话页一旦拿到长内容（长消息、代码块），就会把页签压成半截甚至整条消失，
 * 表现为「顶部选项卡不见了、点不到」。这里对所有方向锁死它们的高度，
 * 压缩全部让给对话面板（它自己 overflow: hidden）。
 */
.yg-ai-home__header,
.ai-head,
.ai-tabs {
  flex: none;
}

/* 桌面页头：标题 + 副标题，左侧返回；导航单独一行，视觉更轻 */
.ai-gateway-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--yg-space-4);
  min-width: 0;
}

.ai-gateway-head__intro {
  display: flex;
  align-items: center;
  gap: var(--yg-space-3);
  min-width: 0;
}

.ai-gateway-head__back {
  flex: 0 0 auto;
}

.ai-gateway-head__titles {
  min-width: 0;
}

.ai-gateway-head__title {
  margin: 0;
  font-size: var(--yg-font-size-xl);
  font-weight: var(--yg-font-weight-semibold);
  line-height: var(--yg-line-height-tight);
  color: var(--yg-text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ai-gateway-head__subtitle {
  margin: var(--yg-space-1) 0 0;
  font-size: var(--yg-font-size-sm);
  line-height: var(--yg-line-height-snug);
  color: var(--yg-text-tertiary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 桌面分区导航：扁平下划线，不抢内容区的视觉重量 */
.ai-gateway-tabs {
  display: flex;
  align-items: stretch;
  gap: var(--yg-space-1);
  margin-bottom: var(--yg-space-4);
  overflow-x: auto;
  overscroll-behavior-x: contain;
  scrollbar-width: none;
  border-bottom: 1px solid var(--yg-border-subtle);
}

.ai-gateway-tabs::-webkit-scrollbar {
  display: none;
}

.ai-gateway-tab {
  display: inline-flex;
  align-items: center;
  gap: var(--yg-space-2);
  flex: 0 0 auto;
  min-height: var(--yg-control-height-lg);
  padding: 0 var(--yg-space-4);
  border: none;
  border-bottom: 2px solid transparent;
  background: transparent;
  color: var(--yg-text-tertiary);
  font-family: inherit;
  font-size: var(--yg-font-size-md);
  font-weight: var(--yg-font-weight-medium);
  line-height: var(--yg-line-height-snug);
  white-space: nowrap;
  cursor: pointer;
  transition:
    color var(--yg-dur-fast) var(--yg-ease-out),
    background-color var(--yg-dur-fast) var(--yg-ease-out),
    border-color var(--yg-dur-fast) var(--yg-ease-out);
}

.ai-gateway-tab:hover {
  color: var(--yg-text-primary);
  background-color: var(--yg-bg-hover);
}

.ai-gateway-tab:active {
  background-color: var(--yg-bg-active);
}

.ai-gateway-tab:focus-visible {
  outline: none;
  box-shadow: var(--yg-focus-ring);
}

.ai-gateway-tab.is-active {
  color: var(--yg-color-primary-strong);
  border-bottom-color: var(--yg-color-primary);
}

/* 桌面多了一行分区导航：对话页高度把这一行扣掉，避免外层多出滚动条 */
.yg-ai-home--chat:not(.is-compact) {
  height: calc(
    100vh - var(--yg-ai-viewport-inset) - var(--yg-control-height-lg) - var(--yg-space-4)
  );
  height: calc(
    100dvh - var(--yg-ai-viewport-inset) - var(--yg-control-height-lg) - var(--yg-space-4)
  );
}

/* 紧凑页头：返回按钮与标题同行（与服务器/平台管理一致） */
.ai-head {
  display: flex;
  align-items: center;
  gap: var(--yg-space-3);
  margin-bottom: var(--yg-space-3);
}

.ai-head__back {
  flex: 0 0 auto;
}

.ai-head__title {
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
.ai-tabs {
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

.ai-tabs::-webkit-scrollbar {
  display: none;
}

.ai-tabs__pill {
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

.ai-tabs__pill:hover {
  color: var(--yg-text-primary);
  background-color: var(--yg-bg-hover);
}

.ai-tabs__pill:active {
  background-color: var(--yg-bg-active);
}

.ai-tabs__pill:focus-visible {
  outline: none;
  box-shadow: var(--yg-focus-ring);
}

.ai-tabs__pill.is-active {
  color: var(--yg-color-primary);
  background-color: var(--yg-color-primary-soft);
  box-shadow: var(--yg-shadow-sm);
  font-weight: var(--yg-font-weight-medium);
}

.yg-ai-home__panel {
  display: flex;
  flex-direction: column;
  /* 吃掉页面头部与页签剩下的高度，交给对话区自己分配 */
  flex: 1 1 auto;
  min-width: 0;
}

/* 对话页的面板不参与整页滚动：溢出只能发生在消息区里 */
.yg-ai-home--chat .yg-ai-home__panel {
  min-height: 0;
  overflow: hidden;
}

/*
 * 极矮横屏（横放手机、横屏小窗）：纵向像素是最稀缺的资源。
 * 页头与页签之间的间距先让出来，剩下的全部留给对话区。
 */
@media (orientation: landscape) and (max-height: 600px) {
  .yg-ai-home {
    gap: var(--yg-space-2);
  }
}
</style>
