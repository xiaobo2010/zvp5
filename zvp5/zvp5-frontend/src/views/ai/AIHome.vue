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

// 角色可能比页面晚一拍就绪（持久化恢复 / 登录后补权限）：用 computed 让管理员
// 分区跟着权限出现，而不是在 setup 时拍一张快照就再也不更新。
const isAdmin = computed(() => userStore.position.includes('admin'))
const canUseAi = computed(
  () => isAdmin.value || userStore.position.includes('department')
)

if (!canUseAi.value) {
  router.replace('/not-found')
}

const curPage = route.params.action?.toString()

function isVisible(value: string): boolean {
  if (ADMIN_ONLY.includes(value)) {
    return isAdmin.value
  }
  return canUseAi.value
}

if (curPage && (!VALID_CURRENT.includes(curPage) || !isVisible(curPage))) {
  router.replace('/ai/chat')
}

const current = ref(
  curPage && VALID_CURRENT.includes(curPage) && isVisible(curPage) ? curPage : 'chat'
)

const BASE_TABS: GatewayTab[] = [
  { label: 'chat', value: 'chat', icon: Cpu },
  { label: 'quota', value: 'quota', icon: Coin },
  { label: 'scheduled', value: 'scheduled', icon: AlarmClock }
]

const ADMIN_TABS: GatewayTab[] = [
  { label: 'keys', value: 'keys', icon: Key },
  { label: 'engines', value: 'engines', icon: Cpu },
  { label: 'models', value: 'models', icon: Box },
  { label: 'quota-admin', value: 'quota-admin', icon: DataLine },
  { label: 'health', value: 'health', icon: Odometer },
  { label: 'logs', value: 'logs', icon: Tickets },
  { label: 'monitor', value: 'monitor', icon: Monitor }
]

const tabs = computed<GatewayTab[]>(() =>
  isAdmin.value ? [...BASE_TABS, ...ADMIN_TABS] : BASE_TABS
)

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
        <p class="ai-head__eyebrow" aria-hidden="true">AI · {{ current }}</p>
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

    <!-- 桌面：phx-head（HUD 编号 + 细网格）+ 斜切底线分区导航 -->
    <template v-else>
      <header class="phx-head ai-gw-head">
        <div class="phx-head__copy">
          <div class="ai-gw-head__eyebrow-row">
            <button
              type="button"
              class="yg-icon-btn yg-icon-btn--ghost ai-gw-head__back"
              :aria-label="t('ai.tabs.back')"
              @click="router.back()"
            >
              <ElIcon :size="20">
                <ArrowLeft />
              </ElIcon>
            </button>
            <p class="phx-head__eyebrow">Gateway · {{ current }}</p>
          </div>
          <h1 class="phx-head__title">{{ t('ai.title') }}</h1>
          <p class="phx-head__lead">{{ t('ai.subtitle') }}</p>
        </div>
        <span class="phx-head__index" aria-hidden="true">07</span>
      </header>

      <nav
        class="phx-tabs ai-gw-tabs"
        role="tablist"
        :aria-label="t('ai.tabs.tabsLabel')"
      >
        <button
          v-for="tab in tabs"
          :key="tab.value"
          type="button"
          role="tab"
          class="phx-tab ai-gw-tab"
          :class="{ 'is-active': tab.value === current }"
          :aria-selected="tab.value === current"
          @click="current = tab.value"
        >
          <span class="phx-tab__fill" aria-hidden="true"></span>
          <ElIcon :size="16" class="phx-tab__icon" aria-hidden="true">
            <Component :is="tab.icon" />
          </ElIcon>
          <span class="phx-tab__text">{{ t('ai.tabs.' + tab.value) }}</span>
          <span class="phx-tab__code" aria-hidden="true">{{ tab.value }}</span>
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

  /* 大屏不要把所有东西按 14px 铺在 2K/4K 屏上：AI 页局部放大字号、控件与间距，
     小屏仍受 clamp 下限保护，不会把移动端撑爆。 */
  --yg-font-size-xs: clamp(12px, 0.25vw + 8px, 15px);
  --yg-font-size-sm: clamp(13px, 0.3vw + 8px, 16px);
  --yg-font-size-md: clamp(14px, 0.4vw + 8px, 18px);
  --yg-font-size-lg: clamp(16px, 0.5vw + 8px, 21px);
  --yg-font-size-xl: clamp(18px, 0.6vw + 8px, 24px);
  --yg-space-3: clamp(12px, 0.3vw + 8px, 16px);
  --yg-space-4: clamp(16px, 0.6vw + 8px, 26px);
  --yg-space-5: clamp(20px, 0.8vw + 10px, 32px);
  --yg-control-height-md: clamp(36px, 0.8vw + 22px, 44px);
  --yg-control-height-lg: clamp(44px, 0.9vw + 30px, 52px);

  display: flex;
  flex-direction: column;
  gap: var(--yg-space-4);
  min-height: calc(100vh - var(--yg-ai-viewport-inset));
  /* 动态视口高度：移动端浏览器收起地址栏时不留多余空白 */
  min-height: calc(100dvh - var(--yg-ai-viewport-inset));
}

/*
 * Phi 棱角语汇（与「义工列表 / 我的 / 通知」同一套 HUD 视觉）：
 * 强调色 / 墨色线 / offset 硬阴影 / HUD 字体全部落到页面根上，
 * 子面板与 Element 控件通过 CSS 变量继承自动跟随，无需逐组件改样式。
 * 弹窗与抽屉挂 body，不受这些覆盖影响，保持准则第 6 章的弹层规范。
 */
.yg-ai-home {
  --phx-accent: var(--phi-cyan, var(--yg-color-primary));
  --phx-accent-2: var(--phi-pink, var(--yg-color-danger));
  --phx-ink: var(--yg-text-primary);
  --phx-muted: var(--yg-text-tertiary);
  --phx-surface: var(--yg-bg-surface);
  --phx-sunken: var(--yg-bg-surface-sunken);
  --phx-line: color-mix(in srgb, var(--phx-accent) 22%, transparent);
  --phx-line-soft: color-mix(in srgb, var(--phx-ink) 10%, transparent);
  --phx-hard: color-mix(in srgb, var(--phx-accent) 16%, #000);
  --phx-display: var(--phi-display, Bebas, Impact, sans-serif);
  --phx-hud: var(--phi-hud, Rajdhani, 'Arial Narrow', sans-serif);
  --phx-ease: cubic-bezier(0.19, 1, 0.22, 1);
  --phx-spring: cubic-bezier(0.34, 1.4, 0.64, 1);

  /* 站内面板与控件一律直角（胶囊导航除外），对齐 phi 页面的硬边语言 */
  --yg-radius-xs: 0;
  --yg-radius-sm: 0;
  --yg-radius-md: 0;
  --yg-radius-lg: 0;
  --yg-radius-xl: 0;
  --yg-radius-2xl: 0;
  --el-border-radius-base: 0;
  --el-border-radius-small: 0;
  --el-border-radius-round: 0;
  --el-card-border-radius: 0;
  --el-tag-border-radius: 0;
  --el-button-border-radius: 0;
}

/* 浅色：强调色与硬阴影换成 phi 浅色配方（与其他 v5 页面同一套） */
html[data-theme='light'] .yg-ai-home {
  --phx-accent: #007a70;
  --phx-accent-2: #c81e63;
  --phx-hard: #101418;
  --el-color-primary: #007a70;
  --yg-color-primary: #007a70;
  --yg-focus-ring-color: #007a70;
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
 * 页签容器在 flex 里自动最小尺寸是 0；对话页一旦拿到长内容（长消息、代码块），
 * 就会把页签压成半截甚至整条消失，表现为「顶部选项卡不见了、点不到」。
 * 这里对所有方向锁死页头与页签的高度，压缩全部让给对话面板（它自己 overflow: hidden）。
 */
.yg-ai-home__header,
.ai-head,
.ai-tabs,
.ai-gw-head,
.ai-gw-tabs {
  flex: none;
}

/* ==========================================================================
   桌面页头：phx-head（HUD 编号 + 细网格 + 斜线），与 user-v2 各页同一套
   ========================================================================== */
.phx-head {
  position: relative;
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: end;
  gap: var(--yg-space-4);
  margin: 0;
  padding: var(--yg-space-6) var(--yg-space-6) var(--yg-space-5);
  overflow: hidden;
  border-left: 3px solid var(--phx-accent);
  background:
    linear-gradient(
        to bottom left,
        transparent calc(50% - 1px),
        var(--phx-accent) 50%,
        transparent calc(50% + 1.5px)
      )
      right top / 22px 22px no-repeat,
    linear-gradient(var(--phx-line-soft) 1px, transparent 1px) 0 0 / 24px 24px,
    linear-gradient(90deg, var(--phx-line-soft) 1px, transparent 1px) 0 0 / 24px 24px,
    linear-gradient(
      100deg,
      color-mix(in srgb, var(--phx-accent) 9%, var(--phx-surface)),
      var(--phx-surface) 58%
    );
  clip-path: polygon(0 0, calc(100% - 22px) 0, 100% 22px, 100% 100%, 0 100%);
  animation: phx-head-in 420ms var(--phx-ease) both;
}

.phx-head::after {
  content: '';
  position: absolute;
  right: 18%;
  bottom: 0;
  width: 120px;
  height: 6px;
  background: repeating-linear-gradient(-55deg, var(--phx-accent) 0 2px, transparent 2px 7px);
  opacity: 0.55;
  pointer-events: none;
}

.phx-head__copy {
  min-width: 0;
}

.phx-head__eyebrow {
  display: flex;
  align-items: center;
  gap: var(--yg-space-2);
  margin: 0;
  color: var(--phx-accent);
  font: 400 15px / 1 var(--phx-display);
  letter-spacing: 0.22em;
  text-transform: uppercase;
  overflow-wrap: anywhere;
}

.phx-head__eyebrow::before {
  content: '';
  flex: 0 0 auto;
  width: 18px;
  height: 2px;
  background: currentColor;
  transform: skewX(-40deg);
}

.phx-head__title {
  position: relative;
  display: inline-block;
  margin: 0;
  color: var(--phx-ink);
  font-size: clamp(26px, 3.4vw, 38px);
  font-weight: var(--yg-font-weight-bold);
  line-height: 1.1;
  letter-spacing: 0.02em;
  animation: phx-slide-in 420ms 60ms var(--phx-ease) both;
}

.phx-head__title::after {
  content: '';
  position: absolute;
  bottom: -6px;
  left: 0;
  width: 44px;
  height: 3px;
  background: var(--phx-accent);
  transform: skewX(-40deg);
  transform-origin: left;
}

.phx-head__lead {
  max-width: 54ch;
  margin: var(--yg-space-4) 0 0;
  color: var(--yg-text-secondary);
  font-size: var(--yg-font-size-sm);
  line-height: var(--yg-line-height-relaxed);
  animation: phx-slide-in 420ms 110ms var(--phx-ease) both;
}

.phx-head__index {
  align-self: start;
  color: transparent;
  font: 400 clamp(64px, 8vw, 112px) / 0.8 var(--phx-display);
  -webkit-text-stroke: 1px color-mix(in srgb, var(--phx-ink) 45%, transparent);
  text-shadow:
    4px 0 color-mix(in srgb, var(--phx-accent-2) 28%, transparent),
    -3px 0 color-mix(in srgb, var(--phx-accent) 32%, transparent);
  user-select: none;
  animation: phx-slide-in-r 450ms 40ms var(--phx-ease) both;
}

/* eyebrow 一行：返回按钮 + HUD 眉题并排，按钮不抢眉题的节奏 */
.ai-gw-head__eyebrow-row {
  display: flex;
  align-items: center;
  gap: var(--yg-space-2);
  margin-bottom: var(--yg-space-2);
}

.ai-gw-head__back {
  flex: 0 0 auto;
}

/* ==========================================================================
   桌面分区导航：底部斜切强调线，从中间展开（填充动效），不抢内容区重量
   ========================================================================== */
.phx-tabs {
  position: relative;
  display: flex;
  flex-wrap: wrap;
  align-items: stretch;
  gap: 4px;
  margin-bottom: var(--yg-space-4);
  border-bottom: 1px solid var(--phx-line);
  animation: phx-slide-in 400ms 90ms var(--phx-ease) both;
}

.phx-tab {
  position: relative;
  isolation: isolate;
  display: inline-flex;
  align-items: center;
  gap: var(--yg-space-2);
  min-height: 44px;
  padding: 0 18px;
  border: 0;
  background: transparent;
  color: var(--phx-muted);
  cursor: pointer;
  font: inherit;
  font-size: var(--yg-font-size-md);
  font-weight: var(--yg-font-weight-medium);
  transition: color 200ms var(--phx-ease);
}

/* 激活标记：3px 倾斜底线，从中间展开 */
.phx-tab__fill {
  position: absolute;
  right: 18px;
  bottom: -1px;
  left: 18px;
  z-index: 0;
  height: 3px;
  background: var(--phx-accent);
  transform: scaleX(0) skewX(-28deg);
  transform-origin: right center;
  transition: transform 340ms var(--phx-spring);
  pointer-events: none;
}

/* hover 只给一层很淡的斜向洗色 */
.phx-tab::before {
  content: '';
  position: absolute;
  inset: 4px 2px;
  z-index: -1;
  background: linear-gradient(
    100deg,
    color-mix(in srgb, var(--phx-accent) 10%, transparent) 0 46%,
    transparent 78%
  );
  opacity: 0;
  transition: opacity 180ms var(--phx-ease);
  pointer-events: none;
}

.phx-tab:hover {
  color: var(--phx-ink);
}

.phx-tab:hover::before {
  opacity: 1;
}

.phx-tab.is-active {
  color: var(--phx-accent);
  font-weight: var(--yg-font-weight-semibold);
}

.phx-tab.is-active .phx-tab__fill {
  transform: scaleX(1) skewX(-28deg);
  transform-origin: left center;
}

.phx-tab:focus-visible {
  outline: 2px solid var(--phx-accent);
  outline-offset: -4px;
}

.phx-tab__icon {
  font-size: 16px;
}

.phx-tab__code {
  color: currentColor;
  font: 400 12px / 1 var(--phx-display);
  letter-spacing: 0.18em;
  text-transform: uppercase;
  opacity: 0.55;
}

/* ==========================================================================
   紧凑页头：返回按钮 + HUD 眉题 + 标题（竖屏空间最稀缺，保持一行）
   ========================================================================== */
.ai-head {
  display: flex;
  align-items: center;
  gap: var(--yg-space-3);
  margin-bottom: var(--yg-space-3);
  padding-left: var(--yg-space-3);
  border-left: 3px solid var(--phx-accent);
}

.ai-head__back {
  flex: 0 0 auto;
}

.ai-head__eyebrow {
  margin: 0;
  color: var(--phx-accent);
  font: 400 13px / 1 var(--phx-display);
  letter-spacing: 0.22em;
  text-transform: uppercase;
  white-space: nowrap;
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

/* 胶囊导航：换行铺开，任何宽度下都不许把入口裁掉半截。
   之前 <600px 仍是横向滚动，手机/平板上「定时任务」这类长标签只能看到
   前半截（滚动条又被隐藏，看起来就是「显示不完整」）。
   这里沿用 7bd02c3d 在 ≥600px 用过的做法，直接对所有宽度生效；
   窄屏同时收紧胶囊内边距与字号，10 个入口落在 3 行内，不给对话区多抢高度。 */
.ai-tabs {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--yg-space-1);
  row-gap: var(--yg-space-1);
  margin-bottom: var(--yg-space-4);
  padding: var(--yg-space-1);
  border: none;
  border-radius: var(--yg-radius-pill);
  -webkit-backdrop-filter: blur(var(--yg-glass-blur-sm)) saturate(180%);
  backdrop-filter: blur(var(--yg-glass-blur-sm)) saturate(180%);
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
  color: var(--phx-accent);
  background-color: color-mix(in srgb, var(--phx-accent) 12%, transparent);
  box-shadow: var(--yg-shadow-sm);
  font-weight: var(--yg-font-weight-medium);
}

/* 手机宽度：收紧胶囊，让 10 个分区尽量落在 3~4 行内，
   不给对话区多抢高度（窄屏最稀缺的就是纵向像素）。
   ⚠️ 必须放在 `.ai-tabs__pill` 基础规则**之后**：同特异性下后写的胜出，
   放在前面会被基础尺寸（min-height / 字号 / 内边距）覆盖掉。 */
@media (max-width: 599px) {
  .ai-tabs {
    gap: 4px;
    row-gap: 4px;
    padding: 4px;
    border-radius: var(--yg-radius-lg);
  }

  .ai-tabs__pill {
    gap: 4px;
    min-height: 36px;
    padding: 0 10px;
    font-size: var(--yg-font-size-xs);
  }
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

/* ==========================================================================
   面板皮肤：全部分区统一「墨色细边 + 左侧强调条 + offset 硬阴影」。
   经 :deep 作用于子面板，弹层（挂 body）不受影响、维持实色圆角规范。
   ========================================================================== */
.yg-ai-home :deep(.yg-card),
.yg-ai-home :deep(.el-card) {
  position: relative;
  border-color: color-mix(in srgb, var(--phx-ink) 16%, transparent);
  border-left: 2px solid var(--phx-accent);
  box-shadow: 4px 4px 0 var(--phx-hard);
}

.yg-ai-home :deep(.yg-card__header) {
  border-bottom: 1px solid var(--phx-line-soft);
}

/* 卡片标题前的斜切短线：与页头眉题同源的 HUD 记号 */
.yg-ai-home :deep(.yg-card__title)::before {
  content: '';
  display: inline-block;
  width: 14px;
  height: 3px;
  margin-right: var(--yg-space-2);
  vertical-align: middle;
  background: var(--phx-accent);
  transform: skewX(-40deg);
}

/* 对话页根容器 overflow: hidden 会裁掉右侧硬阴影，这里直接关掉，不再留残影 */
.yg-ai-home--chat :deep(.yg-card),
.yg-ai-home--chat :deep(.el-card) {
  box-shadow: none;
}

/* 表格：HUD 表头 + 细线；行 hover 走强调色淡底 */
.yg-ai-home :deep(.el-table) {
  --el-table-header-bg-color: var(--phx-sunken);
  --el-table-row-hover-bg-color: color-mix(
    in srgb,
    var(--phx-accent) 9%,
    var(--phx-surface)
  );
  --el-table-border-color: var(--phx-line-soft);
}

.yg-ai-home :deep(.el-table thead th.el-table__cell) {
  background-color: var(--phx-sunken);
  border-bottom: 1px solid var(--phx-line);
  color: var(--phx-muted);
  font-family: var(--phx-hud), var(--yg-font-sans);
  font-size: var(--yg-font-size-xs);
  font-weight: 700;
  letter-spacing: 0.08em;
}

.yg-ai-home :deep(.el-table thead th.el-table__cell:first-child) {
  background-image: linear-gradient(var(--phx-accent), var(--phx-accent));
  background-position: left center;
  background-size: 2px 40%;
  background-repeat: no-repeat;
}

/* 输入槽：直角 + 底部强调细线，聚焦时整框强调 */
.yg-ai-home :deep(.el-input__wrapper),
.yg-ai-home :deep(.el-select__wrapper) {
  border-radius: 0;
  box-shadow:
    inset 0 0 0 1px var(--phx-line-soft),
    inset 0 -2px 0 var(--phx-line);
}

.yg-ai-home :deep(.el-input__wrapper:hover),
.yg-ai-home :deep(.el-select__wrapper:hover) {
  box-shadow:
    inset 0 0 0 1px color-mix(in srgb, var(--phx-ink) 22%, transparent),
    inset 0 -2px 0 var(--phx-accent);
}

.yg-ai-home :deep(.el-input__wrapper.is-focus),
.yg-ai-home :deep(.el-select__wrapper.is-focused) {
  box-shadow:
    inset 0 0 0 1px var(--phx-accent),
    inset 0 -2px 0 var(--phx-accent);
}

.yg-ai-home :deep(.el-textarea__inner) {
  border-radius: 0;
}

/* 统计块（yg-tile）：直角 + 右上小切角，与日志页既有的 HUD 数字块同族 */
.yg-ai-home :deep(.yg-tile) {
  border-radius: 0;
  clip-path: polygon(0 0, calc(100% - 8px) 0, 100% 8px, 100% 100%, 0 100%);
}

/* 分段控件（子面板筛选/表单芯片）：直角描边 + 选中斜切底线，统一视觉 */
.yg-ai-home :deep(.ai-chips) {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--yg-space-1);
}

.yg-ai-home :deep(.ai-chips__item) {
  position: relative;
  min-height: calc(var(--yg-control-height-sm) + var(--yg-space-1));
  padding: 0 var(--yg-space-3);
  border: 1px solid var(--phx-line-soft);
  border-radius: 0;
  background: transparent;
  color: var(--yg-text-secondary);
  font: inherit;
  font-size: var(--yg-font-size-sm);
  cursor: pointer;
  transition:
    color var(--yg-dur-fast) var(--yg-ease-out),
    border-color var(--yg-dur-fast) var(--yg-ease-out),
    background-color var(--yg-dur-fast) var(--yg-ease-out);
}

.yg-ai-home :deep(.ai-chips__item::after) {
  content: '';
  position: absolute;
  right: var(--yg-space-2);
  bottom: -1px;
  left: var(--yg-space-2);
  height: 2px;
  background: var(--phx-accent);
  transform: scaleX(0) skewX(-28deg);
  transform-origin: right center;
  transition: transform 260ms var(--phx-spring);
  pointer-events: none;
}

.yg-ai-home :deep(.ai-chips__item:hover) {
  color: var(--phx-ink);
  border-color: color-mix(in srgb, var(--phx-accent) 40%, transparent);
  background: linear-gradient(
    100deg,
    color-mix(in srgb, var(--phx-accent) 8%, transparent) 0 60%,
    transparent 85%
  );
}

.yg-ai-home :deep(.ai-chips__item:active) {
  color: var(--phx-ink);
}

.yg-ai-home :deep(.ai-chips__item:focus-visible) {
  outline: none;
  box-shadow: var(--yg-focus-ring);
}

.yg-ai-home :deep(.ai-chips__item.is-active) {
  color: var(--phx-accent);
  border-color: color-mix(in srgb, var(--phx-accent) 45%, transparent);
  font-weight: var(--yg-font-weight-medium);
}

.yg-ai-home :deep(.ai-chips__item.is-active::after) {
  transform: scaleX(1) skewX(-28deg);
  transform-origin: left center;
}

.yg-ai-home :deep(.ai-chips__item:disabled) {
  color: var(--yg-text-disabled);
  border-color: var(--phx-line-soft);
  background: none;
  cursor: not-allowed;
}

/* ==========================================================================
   动效
   ========================================================================== */
@keyframes phx-head-in {
  from {
    opacity: 0;
    clip-path: polygon(0 0, 0 0, 0 22px, 0 100%, 0 100%);
  }
  to {
    opacity: 1;
    clip-path: polygon(0 0, calc(100% - 22px) 0, 100% 22px, 100% 100%, 0 100%);
  }
}

@keyframes phx-slide-in {
  from {
    opacity: 0;
    transform: translateX(-14px);
  }
  to {
    opacity: 1;
    transform: none;
  }
}

@keyframes phx-slide-in-r {
  from {
    opacity: 0;
    transform: translateX(18px) skewX(-8deg);
  }
  to {
    opacity: 1;
    transform: none;
  }
}

/*
 * 极矮横屏（横放手机、横屏小窗）：纵向像素是最稀缺的资源。
 * 页头压扁、编号缩小，间距全部让给对话区。
 */
@media (orientation: landscape) and (max-height: 600px) {
  .yg-ai-home {
    gap: var(--yg-space-2);
  }

  .ai-gw-head {
    padding: var(--yg-space-4) var(--yg-space-5) var(--yg-space-3);
  }

  .ai-gw-head .phx-head__index {
    font-size: clamp(40px, 5vw, 64px);
  }

  .ai-gw-head .phx-head__lead {
    margin-top: var(--yg-space-2);
  }

  .ai-gw-head::after {
    right: 12px;
    width: 72px;
  }

  .ai-gw-tabs {
    margin-bottom: var(--yg-space-2);
  }

  .phx-tab {
    min-height: 36px;
    padding: 0 var(--yg-space-3);
  }
}

/* 减少动态：页头/页签/芯片直接出现，过渡与位移全部关闭 */
@media (prefers-reduced-motion: reduce) {
  .phx-head,
  .phx-head__title,
  .phx-head__lead,
  .phx-head__index,
  .phx-tabs {
    animation: none;
    opacity: 1;
    transform: none;
  }

  .phx-tab,
  .phx-tab__fill,
  .phx-tab::before,
  .yg-ai-home :deep(.ai-chips__item),
  .yg-ai-home :deep(.ai-chips__item::after) {
    transition: none;
  }
}
</style>
