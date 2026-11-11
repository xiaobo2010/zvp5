<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElButton } from 'element-plus/es/components/button/index.mjs'
import { ElEmpty } from 'element-plus/es/components/empty/index.mjs'
import { ElIcon } from 'element-plus/es/components/icon/index.mjs'
import { Delete, Edit, FolderChecked, Plus } from '@element-plus/icons-vue'
import type { AiConversation } from '@/../types/ai'
import { formatTokens, relativeTimeParts } from './conversationSession'

/**
 * 会话栏（桌面固定在左侧，移动端放进抽屉）。
 *
 * 这一层只负责「长得像列表」与「把意图抛出去」：重命名与删除的确认框
 * 在父级（对话页）里处理，所以这里没有弹层，也不直接调接口。
 */

const props = withDefaults(
  defineProps<{
    conversations: AiConversation[]
    currentOid: string
    archived?: AiConversation[]
    loading?: boolean
    failed?: boolean
    archivedLoading?: boolean
    archivedFailed?: boolean
    hasMore?: boolean
  }>(),
  {
    archived: () => [],
    loading: false,
    failed: false,
    archivedLoading: false,
    archivedFailed: false,
    hasMore: false
  }
)

const emit = defineEmits<{
  (event: 'select', oid: string): void
  (event: 'create'): void
  (event: 'rename', oid: string): void
  (event: 'archive', oid: string): void
  (event: 'unarchive', oid: string): void
  (event: 'remove', oid: string): void
  (event: 'loadMore'): void
  (event: 'loadArchived'): void
}>()

const { t } = useI18n()

/** 当前视图：活动会话 / 已归档。 */
const view = ref<'active' | 'archived'>('active')

const viewOptions = computed<{ label: string; value: 'active' | 'archived' }[]>(() => [
  { label: t('ai.chat.sessions.title'), value: 'active' },
  { label: t('ai.chat.sessions.archived'), value: 'archived' }
])

/**
 * 会话/归档切换器：自绘滑块。
 * Element 的分段控件切换时会有一段时间的尺寸抖动，把整条会话栏的高度带跑；
 * 这里用一个绝对定位的滑块，按钮高度固定，切换时只移动滑块，布局不会变化。
 */
const switchRef = ref<HTMLElement | null>(null)
const glider = reactive({ x: 0, width: 0 })
let switchObserver: ResizeObserver | null = null

function syncGlider() {
  const root = switchRef.value
  if (!root) {
    return
  }
  const active = root.querySelector<HTMLElement>('.ai-sessions__switch-btn.is-active')
  if (!active) {
    return
  }
  glider.x = active.offsetLeft
  glider.width = active.offsetWidth
}

function switchView(next: 'active' | 'archived') {
  if (view.value === next) {
    return
  }
  view.value = next
}

watch(view, () => {
  void nextTick(syncGlider)
})

/** 当前会话的高亮块：在列表项之间做和大分段控件同样的回弹位移。 */
const activeListRef = ref<HTMLElement | null>(null)
const indicator = reactive({ y: 0, height: 0, visible: false })
const indicatorReady = ref(false)
let indicatorTimer: number | null = null

function enableIndicatorTransition() {
  if (indicatorReady.value || indicatorTimer !== null) {
    return
  }
  indicatorTimer = window.setTimeout(() => {
    indicatorReady.value = true
    indicatorTimer = null
  }, 60)
}

function syncIndicator(reset = false) {
  const list = activeListRef.value
  const active = list?.querySelector<HTMLElement>('.ai-sessions__item.is-active')
  if (!list || !active) {
    indicator.visible = false
    return
  }
  if (reset) {
    indicatorReady.value = false
  }
  indicator.y = active.offsetTop
  indicator.height = active.offsetHeight
  indicator.visible = true
  enableIndicatorTransition()
}

/* 会话切换：保留过渡，让高亮块从旧位置滑到新位置 */
watch(
  () => props.currentOid,
  () => {
    void nextTick(() => syncIndicator())
  }
)

/* 列表重建/数据首次到位：重新定位且不播放过渡，避免从顶部或旧列表滑入 */
watch(
  () => [props.conversations.length, view.value],
  () => {
    void nextTick(() => syncIndicator(true))
  }
)

function onWindowResize() {
  syncIndicator()
}

onMounted(() => {
  void nextTick(syncGlider)
  if (typeof ResizeObserver !== 'undefined' && switchRef.value) {
    switchObserver = new ResizeObserver(() => syncGlider())
    switchObserver.observe(switchRef.value)
  }
  void nextTick(() => syncIndicator(true))
  window.addEventListener('resize', onWindowResize)
})

onBeforeUnmount(() => {
  switchObserver?.disconnect()
  switchObserver = null
  window.removeEventListener('resize', onWindowResize)
  if (indicatorTimer !== null) {
    window.clearTimeout(indicatorTimer)
    indicatorTimer = null
  }
})

/** 切到归档视图时才去拉归档列表。 */
watch(view, (next) => {
  if (next === 'archived') {
    emit('loadArchived')
  }
})

/** 空态文案：列表空是因为没有会话，还是因为拉失败。 */
const emptyDescription = computed(() => {
  if (props.failed) {
    return t('ai.chat.sessions.loadFailed')
  }
  return t('ai.chat.sessions.empty')
})

const archivedEmptyDescription = computed(() => {
  if (props.archivedFailed) {
    return t('ai.chat.sessions.loadFailed')
  }
  return t('ai.chat.sessions.archivedEmpty')
})

/** 相对时间：刚刚 / N 分钟前 / N 小时前 / N 天前 / 具体日期。 */
function relativeTime(value: string | null | undefined): string {
  const parts = relativeTimeParts(value, Date.now())
  if (parts.kind === 'date') {
    return parts.date || '—'
  }
  if (parts.kind === 'justNow') {
    return t('ai.chat.sessions.justNow')
  }
  return t(`ai.chat.sessions.${parts.kind}Ago`, { count: parts.count })
}
</script>

<template>
  <div class="ai-sessions">
    <div class="ai-sessions__head">
      <div
        ref="switchRef"
        class="ai-sessions__switch"
        role="tablist"
        :aria-label="t('ai.chat.sessions.title')"
      >
        <span
          class="ai-sessions__switch-glider"
          aria-hidden="true"
          :style="{ width: glider.width + 'px', transform: `translateX(${glider.x}px)` }"
        ></span>
        <button
          v-for="option in viewOptions"
          :key="option.value"
          type="button"
          role="tab"
          class="ai-sessions__switch-btn"
          :class="{ 'is-active': view === option.value }"
          :aria-selected="view === option.value"
          @click="switchView(option.value)"
        >
          {{ option.label }}
        </button>
      </div>
      <ElButton size="small" text type="primary" @click="emit('create')">
        <ElIcon class="mr-1"><Plus /></ElIcon>
        {{ t('ai.chat.sessions.new') }}
      </ElButton>
    </div>

    <p class="ai-sessions__hint">{{ t('ai.chat.sessions.subtitle') }}</p>

    <div
      v-if="view === 'active'"
      :key="view"
      ref="activeListRef"
      class="ai-sessions__list is-from-left"
    >
      <span
        v-if="indicator.visible"
        class="ai-sessions__indicator"
        :class="{ 'is-ready': indicatorReady }"
        :style="{ transform: `translateY(${indicator.y}px)`, height: `${indicator.height}px` }"
        aria-hidden="true"
      ></span>
      <p v-if="loading && conversations.length === 0" class="ai-sessions__hint">
        {{ t('ai.common.refresh') }}…
      </p>

      <div
        v-for="(item, index) in conversations"
        :key="item.oid"
        class="ai-sessions__item"
        :class="{ 'is-active': item.oid === currentOid }"
        :style="{ '--session-index': index }"
        :aria-current="item.oid === currentOid ? 'true' : undefined"
      >
        <button
          type="button"
          class="ai-sessions__main"
          :aria-label="t('ai.chat.sessions.switchTo')"
          @click="emit('select', item.oid)"
        >
          <span class="ai-sessions__name" :title="item.title || t('ai.chat.sessions.untitled')">
            {{ item.title || t('ai.chat.sessions.untitled') }}
          </span>
          <span class="ai-sessions__meta">
            <span class="tabular-nums">{{ relativeTime(item.updated_at) }}</span>
            <span class="tabular-nums">
              {{ t('ai.chat.sessions.tokens', { total: formatTokens(item.tokens.total) }) }}
            </span>
          </span>
        </button>
        <div class="ai-sessions__actions">
          <ElButton
            text
            size="small"
            :title="t('ai.chat.sessions.rename')"
            @click="emit('rename', item.oid)"
          >
            <ElIcon><Edit /></ElIcon>
          </ElButton>
          <ElButton
            text
            size="small"
            :title="t('ai.chat.sessions.archive')"
            @click="emit('archive', item.oid)"
          >
            <ElIcon><FolderChecked /></ElIcon>
          </ElButton>
          <ElButton
            text
            type="danger"
            size="small"
            :title="t('ai.chat.sessions.remove')"
            @click="emit('remove', item.oid)"
          >
            <ElIcon><Delete /></ElIcon>
          </ElButton>
        </div>
      </div>

      <ElButton
        v-if="hasMore"
        text
        size="small"
        class="ai-sessions__more"
        :loading="loading"
        @click="emit('loadMore')"
      >
        {{ t('ai.chat.sessions.loadMore') }}
      </ElButton>

      <ElEmpty
        v-if="!loading && conversations.length === 0"
        :description="emptyDescription"
        :image-size="72"
      >
        <ElButton size="small" type="primary" @click="emit('create')">
          {{ t('ai.chat.sessions.new') }}
        </ElButton>
      </ElEmpty>
    </div>

    <div v-else key="archived" class="ai-sessions__list is-from-right">
      <p v-if="archivedLoading && archived.length === 0" class="ai-sessions__hint">
        {{ t('ai.common.refresh') }}…
      </p>

      <div
        v-for="(item, index) in archived"
        :key="item.oid"
        class="ai-sessions__item ai-sessions__item--archived"
        :style="{ '--session-index': index }"
      >
        <div class="ai-sessions__main ai-sessions__main--static">
          <span class="ai-sessions__name" :title="item.title || t('ai.chat.sessions.untitled')">
            {{ item.title || t('ai.chat.sessions.untitled') }}
          </span>
          <span class="ai-sessions__meta">
            <span class="tabular-nums">
              {{ t('ai.chat.sessions.archived') }} · {{ relativeTime(item.updated_at) }}
            </span>
          </span>
        </div>
        <div class="ai-sessions__actions">
          <ElButton
            text
            type="primary"
            size="small"
            :title="t('ai.chat.sessions.unarchive')"
            @click="emit('unarchive', item.oid)"
          >
            {{ t('ai.chat.sessions.restore') }}
          </ElButton>
        </div>
      </div>

      <ElEmpty
        v-if="!archivedLoading && archived.length === 0"
        :description="archivedEmptyDescription"
        :image-size="72"
      />
    </div>
  </div>
</template>

<style scoped>
.ai-sessions {
  display: flex;
  flex-direction: column;
  gap: var(--yg-space-2);
  height: 100%;
  min-height: 0;
}

.ai-sessions__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--yg-space-2);
  /* 切换器高度固定：切换只移动滑块，不参与内容撑高 */
  min-height: 29px;
}

/* 会话/归档切换器：和大分段控件同一套切角、渐变与回弹位移 */
.ai-sessions__switch {
  position: relative;
  display: inline-flex;
  align-items: center;
  flex: none;
  height: 29px;
  padding: 3px;
  border: 1px solid var(--yg-border-default);
  background: var(--yg-bg-surface-sunken);
  clip-path: polygon(
    3px 0,
    calc(100% - 8px) 0,
    100% 8px,
    100% calc(100% - 3px),
    calc(100% - 3px) 100%,
    8px 100%,
    0 calc(100% - 8px),
    0 3px
  );
}

.ai-sessions__switch-glider {
  position: absolute;
  top: 3px;
  left: 0;
  height: calc(100% - 6px);
  background: linear-gradient(
    118deg,
    color-mix(in srgb, var(--yg-color-primary) 70%, #ffffff) 0%,
    var(--yg-color-primary) 36%,
    color-mix(in srgb, var(--yg-color-primary) 88%, #04110f) 72%,
    color-mix(in srgb, var(--yg-color-primary) 90%, #ffffff) 100%
  );
  clip-path: polygon(
    0 0,
    calc(100% - 6px) 0,
    100% 6px,
    100% 100%,
    6px 100%,
    0 calc(100% - 6px)
  );
  box-shadow:
    inset 0 1px 0 color-mix(in srgb, #ffffff 52%, transparent),
    inset 0 -1px 0 color-mix(in srgb, #000000 22%, transparent);
  transition:
    transform 440ms cubic-bezier(0.22, 1.22, 0.3, 1),
    width 440ms cubic-bezier(0.22, 1.22, 0.3, 1);
  pointer-events: none;
}

.ai-sessions__switch-btn {
  position: relative;
  z-index: 1;
  height: 100%;
  min-width: 56px;
  padding: 0 12px;
  border: 0;
  background: transparent;
  color: var(--yg-text-tertiary);
  font: inherit;
  font-size: var(--yg-font-size-sm);
  line-height: 1;
  white-space: nowrap;
  cursor: pointer;
  transition: color 280ms var(--yg-ease-out);
}

.ai-sessions__switch-btn:hover {
  color: var(--yg-text-primary);
}

.ai-sessions__switch-btn.is-active {
  color: #041311;
  font-weight: 600;
}

html[data-theme='light'] .ai-sessions__switch-btn.is-active {
  color: #ffffff;
}

.ai-sessions__switch-btn:focus-visible {
  outline: none;
  box-shadow: var(--yg-focus-ring);
}

.ai-sessions__hint {
  margin: 0;
  font-size: var(--yg-font-size-xs);
  line-height: 1.5;
  color: var(--yg-text-tertiary);
}

.ai-sessions__list {
  position: relative;
  display: flex;
  flex-direction: column;
  flex: 1 1 auto;
  gap: var(--yg-space-1);
  min-height: 0;
  overflow-y: auto;
  overscroll-behavior: contain;
  /* 会话栏不需要可见滚动条：滚轮/触控板照常滚，出现/消失都不再压缩列表和按钮 */
  scrollbar-width: none;
  animation: ai-sessions-switch-in-left 260ms cubic-bezier(0.19, 1, 0.22, 1) both;
}

/* 从归档切回来时从左侧滑入，和滑块的方向一致 */
.ai-sessions__list.is-from-right {
  animation-name: ai-sessions-switch-in-right;
}

.ai-sessions__list::-webkit-scrollbar {
  width: 0;
  height: 0;
}

/* 当前会话的高亮块：和大分段控件的滑块同一套回弹位移 */
.ai-sessions__indicator {
  position: absolute;
  top: 0;
  right: 0;
  left: 0;
  z-index: 0;
  border: 1px solid var(--yg-color-primary);
  background: color-mix(in srgb, var(--yg-color-primary) 12%, var(--yg-bg-surface));
  clip-path: polygon(0 0, calc(100% - 8px) 0, 100% 8px, 100% 100%, 0 100%);
  box-shadow: inset 3px 0 0 var(--yg-color-primary);
  filter: drop-shadow(2px 2px 0 color-mix(in srgb, var(--yg-color-primary) 28%, transparent));
  pointer-events: none;
}

.ai-sessions__indicator.is-ready {
  transition:
    transform 440ms cubic-bezier(0.22, 1.22, 0.3, 1),
    height 440ms cubic-bezier(0.22, 1.22, 0.3, 1);
}

html[data-theme='light'] .ai-sessions__indicator {
  filter: drop-shadow(2px 2px 0 rgba(5, 6, 8, 0.28));
}

/* 会话项：主按钮占满宽度，操作按钮浮在右侧。 */
.ai-sessions__item {
  position: relative;
  z-index: 1;
  display: flex;
  align-items: stretch;
  gap: var(--yg-space-1);
  border: 1px solid var(--yg-border-default);
  border-radius: 0;
  background: transparent;
  clip-path: polygon(0 0, calc(100% - 8px) 0, 100% 8px, 100% 100%, 0 100%);
  box-shadow: inset 0 1px 0 color-mix(in srgb, #ffffff 7%, transparent);
  /* 切换会话/归档时逐项擦入，和标准列表同一套节奏 */
  animation: ai-sessions-item-in 300ms cubic-bezier(0.19, 1, 0.22, 1) backwards;
  animation-delay: calc(min(var(--session-index, 0), 8) * 26ms);
  transition:
    background-color var(--yg-dur-fast) var(--yg-ease-out),
    border-color var(--yg-dur-fast) var(--yg-ease-out),
    box-shadow var(--yg-dur-fast) var(--yg-ease-out);
}

.ai-sessions__item:hover {
  background: var(--yg-bg-hover);
  box-shadow:
    inset 2px 0 0 var(--yg-color-primary),
    inset 0 1px 0 color-mix(in srgb, #ffffff 7%, transparent);
}

.ai-sessions__item.is-active {
  border-color: transparent;
  background: transparent;
  box-shadow: none;
}

.ai-sessions__item.is-active .ai-sessions__name {
  color: var(--yg-color-primary);
}

.ai-sessions__main {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 3px;
  min-width: 0;
  padding: 10px 12px;
  border: 0;
  border-radius: 0;
  background: transparent;
  color: inherit;
  font: inherit;
  text-align: left;
  cursor: pointer;
}

.ai-sessions__main:focus-visible {
  outline: none;
  box-shadow: var(--yg-focus-ring);
}

/* 归档行不可切换会话，只留「恢复」。 */
.ai-sessions__main--static {
  cursor: default;
}

.ai-sessions__name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: var(--yg-font-size-sm);
  color: var(--yg-text-primary);
}

.ai-sessions__meta {
  display: flex;
  align-items: center;
  gap: var(--yg-space-2);
  font-size: var(--yg-font-size-xs);
  color: var(--yg-text-tertiary);
}

.ai-sessions__actions {
  display: flex;
  flex: none;
  align-items: center;
  gap: 0;
  padding-right: 8px;
}

/* 操作按钮默认淡出，聚焦或悬停时现身：列表本身保持安静。 */
.ai-sessions__actions :deep(.el-button) {
  opacity: 0;
  transition: opacity var(--yg-dur-fast) var(--yg-ease-out);
}

.ai-sessions__item:hover .ai-sessions__actions :deep(.el-button),
.ai-sessions__item:focus-within .ai-sessions__actions :deep(.el-button) {
  opacity: 1;
}

/* 归档行只有「恢复」这一个动作，常显。 */
.ai-sessions__item--archived .ai-sessions__actions :deep(.el-button) {
  opacity: 1;
}

.ai-sessions__more {
  align-self: flex-start;
}

/* 会话/归档切换：列表按滑块方向滑入，条目再逐项擦入 */
@keyframes ai-sessions-switch-in-left {
  from {
    opacity: 0;
    transform: translateX(-14px);
  }
  to {
    opacity: 1;
    transform: none;
  }
}

@keyframes ai-sessions-switch-in-right {
  from {
    opacity: 0;
    transform: translateX(14px);
  }
  to {
    opacity: 1;
    transform: none;
  }
}

@keyframes ai-sessions-item-in {
  from {
    opacity: 0;
    transform: translateX(-8px);
  }
  to {
    opacity: 1;
    transform: none;
  }
}

@media (prefers-reduced-motion: reduce) {
  .ai-sessions__list,
  .ai-sessions__item {
    animation: none;
  }

  .ai-sessions__switch-glider,
  .ai-sessions__indicator.is-ready {
    transition: none;
  }
}
</style>
