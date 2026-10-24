<script setup lang="ts">
/**
 * ZPagination —— 统一翻页组件（自绘，不依赖 ElPagination）
 *
 * 交互设计：
 * - 页码轨道可直接左右拖动，按位移比例快速滑页（scrub），松手吸附到整数页；
 * - 拖动中实时更新「第 x / y 页」提示，页码窗口跟随当前页滚动；
 * - 选中页有一颗弹性滑块，切换页面时以弹簧曲线滑过去（回弹不僵硬）；
 * - 上一页 / 下一页、页码均为按压回弹；`prefers-reduced-motion` 时关闭弹性。
 *
 * 兼容：对外仍暴露 total / currentPage / pageSize / pageSizes / hideOnSinglePage
 * 与 `update:currentPage` / `update:pageSize` / `change`，既有调用点无需改动。
 */
import { computed, ref, watch } from 'vue'
import { useWindowSize } from '@vueuse/core'

const props = withDefaults(
  defineProps<{
    /** 总条数 */
    total: number
    /** 当前页（v-model:current-page） */
    currentPage: number
    /** 每页条数（v-model:page-size） */
    pageSize: number
    /** 可选每页条数 */
    pageSizes?: number[]
    /** 只有一页时是否隐藏 */
    hideOnSinglePage?: boolean
  }>(),
  {
    pageSizes: () => [8, 16, 32, 64],
    hideOnSinglePage: false
  }
)

const emit = defineEmits<{
  'update:currentPage': [page: number]
  'update:pageSize': [size: number]
  change: [page: number, size: number]
}>()

const { width } = useWindowSize()
const isMobile = computed(() => width.value < 768)

/** 轨道同时可见的页码个数（奇数，保证当前页居中） */
const visibleCount = computed(() => (isMobile.value ? 5 : 7))

const pageCount = computed(() => Math.max(1, Math.ceil(props.total / Math.max(1, props.pageSize))))

function clamp(page: number) {
  return Math.min(pageCount.value, Math.max(1, Math.round(page)))
}

/** 本地页：拖动过程中先行更新，避免等父组件异步刷新造成卡顿 */
const localPage = ref(clamp(props.currentPage))

watch(
  () => props.currentPage,
  (page) => {
    if (!dragging.value) {
      localPage.value = clamp(page)
    }
  }
)

watch(pageCount, () => {
  localPage.value = clamp(localPage.value)
})

/** 以当前页为中心的页码窗口，边界处对齐，不留空位 */
const visiblePages = computed(() => {
  const count = Math.min(visibleCount.value, pageCount.value)
  const current = clamp(localPage.value)
  const half = Math.floor(count / 2)
  let start = Math.max(1, current - half)
  if (start + count - 1 > pageCount.value) {
    start = Math.max(1, pageCount.value - count + 1)
  }
  return Array.from({ length: count }, (_, i) => start + i)
})

/** 选中滑块在轨道中的位置（按百分比，天然响应式） */
const thumbStyle = computed(() => {
  const count = visiblePages.value.length
  const index = visiblePages.value.indexOf(clamp(localPage.value))
  const safeIndex = index < 0 ? 0 : index
  return {
    left: `${(safeIndex / count) * 100}%`,
    width: `${100 / count}%`
  }
})

function commit(page: number) {
  const target = clamp(page)
  if (target === props.currentPage) {
    return
  }
  localPage.value = target
  emit('update:currentPage', target)
  emit('change', target, props.pageSize)
}

/* ---------- 拖动滑页 ---------- */
const trackRef = ref<HTMLElement>()
const dragging = ref(false)
const moved = ref(false)
let startX = 0
let startPage = 1
let pointerId = -1

function stepWidth(): number {
  const el = trackRef.value
  if (!el) {
    return 44
  }
  return el.clientWidth / Math.max(1, visiblePages.value.length)
}

function onPointerDown(event: PointerEvent) {
  if (pageCount.value <= 1) {
    return
  }
  // 只响应主键 / 触摸，避免右键拖动
  if (event.pointerType === 'mouse' && event.button !== 0) {
    return
  }
  dragging.value = true
  moved.value = false
  startX = event.clientX
  startPage = clamp(localPage.value)
  pointerId = event.pointerId
  // 监听 window：即使指针滑出轨道或 capture 不可用，也能持续收到移动
  window.addEventListener('pointermove', onPointerMove, { passive: false })
  window.addEventListener('pointerup', onPointerUp)
  window.addEventListener('pointercancel', onPointerUp)
  event.preventDefault()
}

function stopListening() {
  window.removeEventListener('pointermove', onPointerMove)
  window.removeEventListener('pointerup', onPointerUp)
  window.removeEventListener('pointercancel', onPointerUp)
}

function onPointerMove(event: PointerEvent) {
  if (!dragging.value || event.pointerId !== pointerId) {
    return
  }
  const dx = event.clientX - startX
  if (!moved.value && Math.abs(dx) > 6) {
    moved.value = true
  }
  if (!moved.value) {
    return
  }
  // 向左拖 → 下一页；每滑过一个页码宽度约等于 1 页，1.15 倍灵敏度让长列表也能快速跨越
  const steps = Math.round((-dx / stepWidth()) * 1.15)
  localPage.value = clamp(startPage + steps)
  event.preventDefault()
}

function onPointerUp(event: PointerEvent) {
  if (!dragging.value || event.pointerId !== pointerId) {
    return
  }
  dragging.value = false
  stopListening()
  pointerId = -1
  if (moved.value) {
    commit(localPage.value)
    // 等 click 派发完再复位，否则拖动结束会被当作点击页码
    setTimeout(() => {
      moved.value = false
    }, 0)
  }
}

/** 拖动后拦截落在页码上的 click，避免松手瞬间误跳页 */
function onClickCapture(event: MouseEvent) {
  if (moved.value) {
    event.stopPropagation()
    event.preventDefault()
  }
}

function selectPage(page: number) {
  if (moved.value) {
    return
  }
  commit(page)
}

/* ---------- 每页条数 / 跳页 ---------- */
const sizeModel = computed({
  get: () => props.pageSize,
  set: (size: number) => {
    if (size === props.pageSize) {
      return
    }
    emit('update:pageSize', size)
    // 换每页条数后回到第 1 页，避免停留在不存在的页码
    emit('update:currentPage', 1)
    emit('change', 1, size)
    localPage.value = 1
  }
})

/** 可选每页条数：并入当前值，避免父组件默认值与预设列表不一致时下拉框显示空白 */
const sizeOptions = computed(() => {
  const list = [...props.pageSizes]
  if (!list.includes(props.pageSize)) {
    list.push(props.pageSize)
    list.sort((a, b) => a - b)
  }
  return list
})

const jumpInput = ref('')

function applyJump() {
  const page = Number(jumpInput.value)
  if (Number.isFinite(page) && page > 0) {
    commit(page)
  }
  jumpInput.value = ''
}

const hidden = computed(() => props.hideOnSinglePage && pageCount.value <= 1)
</script>

<template>
  <div v-if="!hidden" class="yg-pg" :class="{ 'is-mobile': isMobile }">
    <span v-if="!isMobile" class="yg-pg__total">共 {{ total }} 条</span>

    <label v-if="!isMobile" class="yg-pg__size">
      <select v-model.number="sizeModel" class="yg-pg__select" aria-label="每页条数">
        <option v-for="size in sizeOptions" :key="size" :value="size">{{ size }} 条/页</option>
      </select>
    </label>

    <div class="yg-pg__nav">
      <button
        type="button"
        class="yg-pg__btn"
        :disabled="localPage <= 1"
        aria-label="上一页"
        @click="commit(localPage - 1)"
      >
        <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true">
          <path
            fill="none"
            stroke="currentColor"
            stroke-width="2"
            stroke-linecap="round"
            stroke-linejoin="round"
            d="M15 5 8 12l7 7"
          />
        </svg>
      </button>

      <div
        ref="trackRef"
        class="yg-pg__track"
        :class="{ 'is-dragging': dragging }"
        role="group"
        aria-label="页码，可左右拖动快速翻页"
        @pointerdown="onPointerDown"
        @click.capture="onClickCapture"
      >
        <span class="yg-pg__thumb" :style="thumbStyle" aria-hidden="true" />
        <button
          v-for="page in visiblePages"
          :key="page"
          type="button"
          class="yg-pg__num"
          :class="{ 'is-active': page === localPage }"
          :aria-current="page === localPage ? 'page' : undefined"
          :aria-label="`第 ${page} 页`"
          @click="selectPage(page)"
        >
          {{ page }}
        </button>
      </div>

      <button
        type="button"
        class="yg-pg__btn"
        :disabled="localPage >= pageCount"
        aria-label="下一页"
        @click="commit(localPage + 1)"
      >
        <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true">
          <path
            fill="none"
            stroke="currentColor"
            stroke-width="2"
            stroke-linecap="round"
            stroke-linejoin="round"
            d="m9 5 7 7-7 7"
          />
        </svg>
      </button>
    </div>

    <span class="yg-pg__status" aria-live="polite">
      第 {{ localPage }} / {{ pageCount }} 页
    </span>

    <label v-if="!isMobile" class="yg-pg__jump">
      前往
      <input
        v-model="jumpInput"
        class="yg-pg__jump-input"
        type="number"
        min="1"
        :max="pageCount"
        inputmode="numeric"
        aria-label="跳转到指定页"
        @keydown.enter="applyJump"
        @blur="applyJump"
      />
      页
    </label>
  </div>
</template>

<style scoped>
.yg-pg {
  display: flex;
  align-items: center;
  justify-content: center;
  flex-wrap: wrap;
  gap: 12px;
  width: 100%;
  padding: 12px 0;
  color: var(--yg-text-secondary);
  font-size: 13px;
  touch-action: pan-y;
}

.yg-pg__total {
  margin-right: 2px;
  white-space: nowrap;
}

.yg-pg__size {
  display: inline-flex;
}

.yg-pg__select {
  height: 32px;
  padding: 0 26px 0 10px;
  border: 1px solid var(--yg-border-default);
  border-radius: var(--yg-radius-md);
  background-color: var(--yg-bg-surface-raised);
  background-image: linear-gradient(45deg, transparent 50%, currentColor 50%),
    linear-gradient(135deg, currentColor 50%, transparent 50%);
  background-position:
    calc(100% - 15px) 14px,
    calc(100% - 10px) 14px;
  background-size: 5px 5px;
  background-repeat: no-repeat;
  color: var(--yg-text-primary);
  font-size: 13px;
  appearance: none;
  cursor: pointer;
  transition:
    border-color 160ms ease,
    box-shadow 160ms ease;
}

.yg-pg__select:hover {
  border-color: var(--yg-color-primary);
}

.yg-pg__select:focus-visible {
  outline: none;
  border-color: var(--yg-color-primary);
  box-shadow: var(--yg-focus-ring);
}

.yg-pg__nav {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}

/* 圆形玻璃导航钮：按压回弹 */
.yg-pg__btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 34px;
  padding: 0;
  border: 1px solid var(--yg-border-default);
  border-radius: 999px;
  background: var(--yg-bg-surface-raised);
  color: var(--yg-text-primary);
  cursor: pointer;
  transition:
    transform 380ms cubic-bezier(0.34, 1.56, 0.64, 1),
    background-color 160ms ease,
    border-color 160ms ease,
    opacity 160ms ease;
}

.yg-pg__btn:hover:not(:disabled) {
  border-color: var(--yg-color-primary);
  color: var(--yg-color-primary);
}

.yg-pg__btn:active:not(:disabled) {
  transform: scale(0.86);
  transition-duration: 90ms;
}

.yg-pg__btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.yg-pg__btn:focus-visible {
  outline: none;
  box-shadow: var(--yg-focus-ring);
}

/* 页码轨道：可拖拽 */
.yg-pg__track {
  position: relative;
  display: inline-flex;
  align-items: stretch;
  min-width: 0;
  padding: 3px;
  border: 1px solid var(--yg-border-default);
  border-radius: 999px;
  background: var(--yg-bg-surface-sunken);
  cursor: grab;
  user-select: none;
  touch-action: none;
}

.yg-pg__track.is-dragging {
  cursor: grabbing;
}

/* 选中滑块：弹性位移，拖动时跟手（无过渡） */
.yg-pg__thumb {
  position: absolute;
  top: 3px;
  bottom: 3px;
  border-radius: 999px;
  background: var(--yg-color-primary);
  box-shadow: 0 2px 8px rgba(14, 165, 233, 0.35);
  transition: left 420ms cubic-bezier(0.34, 1.56, 0.64, 1), width 240ms ease;
  will-change: left;
  pointer-events: none;
}

.yg-pg__track.is-dragging .yg-pg__thumb {
  transition: none;
}

.yg-pg__num {
  position: relative;
  z-index: 1;
  min-width: 38px;
  height: 32px;
  padding: 0 6px;
  border: 0;
  border-radius: 999px;
  background: transparent;
  color: var(--yg-text-secondary);
  font-size: 13px;
  font-variant-numeric: tabular-nums;
  cursor: pointer;
  transition:
    color 200ms ease,
    transform 380ms cubic-bezier(0.34, 1.56, 0.64, 1);
}

.yg-pg__num:hover {
  color: var(--yg-text-primary);
}

.yg-pg__num:active {
  transform: scale(0.9);
  transition-duration: 90ms;
}

.yg-pg__num.is-active {
  color: #fff;
  font-weight: 600;
}

.yg-pg__num:focus-visible {
  outline: none;
  box-shadow: var(--yg-focus-ring);
}

.yg-pg__status {
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

.yg-pg__jump {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  white-space: nowrap;
}

.yg-pg__jump-input {
  width: 52px;
  height: 32px;
  padding: 0 6px;
  border: 1px solid var(--yg-border-default);
  border-radius: var(--yg-radius-md);
  background: var(--yg-bg-surface-raised);
  color: var(--yg-text-primary);
  font-size: 13px;
  text-align: center;
  font-variant-numeric: tabular-nums;
}

.yg-pg__jump-input:focus-visible {
  outline: none;
  border-color: var(--yg-color-primary);
  box-shadow: var(--yg-focus-ring);
}

/* 窄屏：只留 上一页 / 页码轨道 / 下一页，纵向排一行 */
.is-mobile {
  gap: 10px;
  padding: 8px 0;
}

.is-mobile .yg-pg__num {
  min-width: 34px;
}

@media (prefers-reduced-motion: reduce) {
  .yg-pg__thumb,
  .yg-pg__btn,
  .yg-pg__num {
    transition: none;
  }
}
</style>
