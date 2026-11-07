<script setup lang="ts">
/**
 * ZPagination —— 统一翻页组件（自绘，不依赖 ElPagination）
 *
 * 交互设计：
 * - 页码轨道可左右拖动：滑块 1:1 跟随手指（不做整数吸附），拖得越快横向拉伸越明显；
 * - 松手时滑块以强回弹弹簧滑到整数页，同时做一次缩放脉冲；
 * - 点击页码 / 上一页 / 下一页 / 跳页，每次都会重新播放弹簧动画与脉冲，
 *   动画由 WAAPI 驱动，不依赖 CSS 过渡状态，所以不会被上一次性状吞掉；
 * - `prefers-reduced-motion` 时关闭全部弹性。
 *
 * 兼容：对外仍暴露 total / currentPage / pageSize / pageSizes / hideOnSinglePage
 * 与 `update:currentPage` / `update:pageSize` / `change`，既有调用点无需改动。
 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
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

/** 每个页码格子的宽度：轨道宽度完全由它决定，滑块与格子因此严格对齐 */
const slotPx = computed(() => (isMobile.value ? 36 : 42))

/** 轨道内边距，滑块起点与之对齐 */
const TRACK_PAD = 3

const SPRING = 'cubic-bezier(0.16, 1.72, 0.3, 1)'

const pageCount = computed(() => Math.max(1, Math.ceil(props.total / Math.max(1, props.pageSize))))

function clamp(page: number) {
  return Math.min(pageCount.value, Math.max(1, Math.round(page)))
}

function reduceMotion() {
  return window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false
}

/** 本地页：拖动过程中先行更新，避免等父组件异步刷新造成卡顿 */
const localPage = ref(clamp(props.currentPage))

/** 拖动期间冻结页码窗口，避免数字在手指下跳动 */
const windowAnchor = ref<number | null>(null)

const dragging = ref(false)

watch(
  () => props.currentPage,
  (page) => {
    if (!dragging.value) {
      localPage.value = clamp(page)
      placeThumb(true)
    }
  }
)

watch(pageCount, () => {
  localPage.value = clamp(localPage.value)
  windowAnchor.value = null
})

/** 以当前页为中心的页码窗口，边界处对齐，不留空位 */
const visiblePages = computed(() => {
  const count = Math.min(visibleCount.value, pageCount.value)
  const current = clamp(windowAnchor.value ?? localPage.value)
  const half = Math.floor(count / 2)
  let start = Math.max(1, current - half)
  if (start + count - 1 > pageCount.value) {
    start = Math.max(1, pageCount.value - count + 1)
  }
  return Array.from({ length: count }, (_, i) => start + i)
})

/* ---------- 滑块：位置全部由 JS 驱动，动画走 WAAPI ---------- */
const trackRef = ref<HTMLElement>()
const thumbRef = ref<HTMLElement>()

/** 最近一次写入的滑块位移，作为下一次弹簧动画的起点 */
let lastX = TRACK_PAD

function slotX(index: number): number {
  return TRACK_PAD + index * slotPx.value
}

function pageIndexOf(page: number): number {
  const index = visiblePages.value.indexOf(clamp(page))
  return index < 0 ? 0 : index
}

function paintThumb(indexFloat: number) {
  const el = thumbRef.value
  if (!el) {
    return
  }
  const x = slotX(indexFloat)
  lastX = x
  el.style.transform = `translate3d(${x}px, 0, 0)`
}

/**
 * 把滑块放到当前页的位置。
 * animate 时从 lastX 弹到目标位置（WAAPI 每次都是新的动画实例，
 * 所以同一页连点、或目标与上次相同时也能重新弹一次）。
 */
function placeThumb(animate: boolean) {
  const el = thumbRef.value
  if (!el || dragging.value) {
    return
  }
  const x = slotX(pageIndexOf(localPage.value))
  const from = lastX
  paintThumb(pageIndexOf(localPage.value))
  if (!animate || reduceMotion() || Math.abs(from - x) < 0.5) {
    return
  }
  el.animate(
    [{ transform: `translate3d(${from}px, 0, 0)` }, { transform: `translate3d(${x}px, 0, 0)` }],
    { duration: 470, easing: SPRING }
  )
}

/** 缩放脉冲：让每一次翻页都有可见的弹性反馈 */
function pulse() {
  const el = thumbRef.value
  if (!el || reduceMotion()) {
    return
  }
  el.animate([{ scale: '1' }, { scale: '1.12' }, { scale: '1' }], {
    duration: 430,
    easing: 'cubic-bezier(0.22, 1.6, 0.36, 1)'
  })
}

/* ---------- 拖动滑页 ---------- */
const moved = ref(false)
let startX = 0
let startIndex = 0
let dragIndex = 0
let pointerId = -1
let lastMoveX = 0

function onPointerDown(event: PointerEvent) {
  if (pageCount.value <= 1) {
    return
  }
  // 只响应主键 / 触摸，避免右键拖动
  if (event.pointerType === 'mouse' && event.button !== 0) {
    return
  }
  const el = thumbRef.value
  // 手指按下立刻接管滑块，取消还没播完的弹簧，避免和手动定位打架
  el?.getAnimations().forEach((animation) => animation.cancel())
  if (el) {
    el.style.scale = '1'
  }

  dragging.value = true
  moved.value = false
  startX = event.clientX
  lastMoveX = event.clientX
  startIndex = pageIndexOf(localPage.value)
  dragIndex = startIndex
  windowAnchor.value = clamp(localPage.value)
  pointerId = event.pointerId
  // 监听 window：即使指针滑出轨道，也能持续收到移动
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
  if (!moved.value && Math.abs(dx) > 5) {
    moved.value = true
  }
  if (!moved.value) {
    return
  }

  const count = Math.max(1, visiblePages.value.length)
  // 1:1 跟手：手指移动一个格子宽度就换一页，不做整数吸附
  const raw = startIndex - dx / Math.max(1, slotPx.value)
  dragIndex = Math.min(count - 1, Math.max(0, raw))
  paintThumb(dragIndex)

  // 拖动速度决定横向拉伸幅度，松手后回弹到原形
  const speed = Math.abs(event.clientX - lastMoveX)
  lastMoveX = event.clientX
  const el = thumbRef.value
  if (el) {
    const stretch = Math.min(0.16, speed / 260)
    el.style.scale = `${(1 + stretch).toFixed(3)} ${(1 - stretch * 0.45).toFixed(3)}`
  }

  const page = visiblePages.value[Math.round(dragIndex)]
  if (page !== undefined && page !== localPage.value) {
    localPage.value = page
  }
  event.preventDefault()
}

function onPointerUp(event: PointerEvent) {
  if (!dragging.value || event.pointerId !== pointerId) {
    return
  }
  stopListening()
  pointerId = -1

  const el = thumbRef.value
  if (el) {
    el.style.scale = '1'
  }

  const count = Math.max(1, visiblePages.value.length)
  const index = Math.min(count - 1, Math.max(0, Math.round(dragIndex)))
  const page = visiblePages.value[index] ?? localPage.value

  // 先解除冻结、再落页，让「重排窗口 + 弹簧回位」合并成一次动画
  dragging.value = false
  windowAnchor.value = null

  if (moved.value) {
    localPage.value = page
    commit(page)
    // 等 click 派发完再复位，否则拖动结束会被当作点击页码
    setTimeout(() => {
      moved.value = false
    }, 0)
  } else {
    placeThumb(true)
  }
}

/** 拖动后拦截落在页码上的 click，避免松手瞬间误跳页 */
function onClickCapture(event: MouseEvent) {
  if (moved.value) {
    event.stopPropagation()
    event.preventDefault()
  }
}

/* ---------- 翻页 ---------- */
function commit(page: number) {
  const target = clamp(page)
  localPage.value = target
  placeThumb(true)
  pulse()
  if (target !== props.currentPage) {
    emit('update:currentPage', target)
    emit('change', target, props.pageSize)
  }
}

function selectPage(page: number, event: MouseEvent) {
  if (moved.value) {
    return
  }
  // 每点一次都给被点的数字一次完整的弹簧挤压，不受上一次动画状态影响
  const target = event.currentTarget as HTMLElement | null
  if (target && !reduceMotion()) {
    target.animate(
      [{ transform: 'scale(0.86)' }, { transform: 'scale(1.18)' }, { transform: 'scale(1)' }],
      { duration: 420, easing: 'cubic-bezier(0.22, 1.55, 0.36, 1)' }
    )
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
    placeThumb(true)
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

onMounted(() => {
  nextTick(() => {
    placeThumb(false)
    // 首帧后再同步一次，保证父级卡片布局稳定后位置依然准确
    requestAnimationFrame(() => placeThumb(false))
  })
})

onBeforeUnmount(() => {
  stopListening()
})

watch([visiblePages, slotPx], () => {
  if (!dragging.value) {
    placeThumb(true)
  }
})
</script>

<template>
  <div
    v-if="!hidden"
    class="yg-pg"
    :class="{ 'is-mobile': isMobile }"
    :style="{ '--yg-pg-slot': `${slotPx}px` }"
  >
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
        <span ref="thumbRef" class="yg-pg__thumb" aria-hidden="true" />
        <button
          v-for="page in visiblePages"
          :key="page"
          type="button"
          class="yg-pg__num"
          :class="{ 'is-active': page === localPage }"
          :aria-current="page === localPage ? 'page' : undefined"
          :aria-label="`第 ${page} 页`"
          @click="selectPage(page, $event)"
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

    <span class="yg-pg__status" aria-live="polite"> 第 {{ localPage }} / {{ pageCount }} 页 </span>

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
  padding: 0 26px 0 4px;
  border: 0;
  border-bottom: 1px solid var(--yg-border-default);
  border-radius: 0;
  background-color: transparent;
  background-image:
    linear-gradient(45deg, transparent 50%, currentColor 50%),
    linear-gradient(135deg, currentColor 50%, transparent 50%);
  background-position:
    calc(100% - 12px) 14px,
    calc(100% - 7px) 14px;
  background-size: 5px 5px;
  background-repeat: no-repeat;
  color: var(--yg-text-primary);
  font-size: 13px;
  appearance: none;
  cursor: pointer;
  transition: border-color 160ms ease;
}

.yg-pg__select:hover {
  border-bottom-color: var(--yg-color-primary);
}

.yg-pg__select:focus-visible {
  outline: none;
  border-bottom-color: var(--yg-color-primary);
  box-shadow: inset 0 -2px 0 var(--yg-color-primary);
}

.yg-pg__nav {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}

/* 翻页钮：无框，只保留箭头与颜色反馈 */
.yg-pg__btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 34px;
  padding: 0;
  border: 0;
  border-radius: 0;
  background: transparent;
  color: var(--yg-text-secondary);
  cursor: pointer;
  transition:
    transform 460ms cubic-bezier(0.34, 1.9, 0.42, 1),
    color 160ms ease,
    opacity 160ms ease;
}

.yg-pg__btn:hover:not(:disabled) {
  color: var(--yg-color-primary);
}

.yg-pg__btn:active:not(:disabled) {
  transform: scale(0.84);
  transition-duration: 70ms;
}

.yg-pg__btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.yg-pg__btn:focus-visible {
  outline: 2px solid var(--yg-color-primary);
  outline-offset: 2px;
}

/* 页码轨道：无胶囊外框，只留一条底规；宽度由 --yg-pg-slot 决定，滑块严格对齐 */
.yg-pg__track {
  position: relative;
  display: inline-flex;
  align-items: stretch;
  padding: 0 3px;
  border: 0;
  border-bottom: 1px solid var(--yg-border-subtle);
  border-radius: 0;
  background: transparent;
  cursor: grab;
  user-select: none;
  touch-action: none;
  overflow: visible;
}

.yg-pg__track.is-dragging {
  cursor: grabbing;
}

/* 选中标记：低高度底部强调条，不再是整块滑块/平行四边形。
   位移、弹簧与缩放脉冲仍由 JS/WAAPI 驱动。 */
.yg-pg__thumb {
  position: absolute;
  right: auto;
  bottom: -1px;
  left: 0;
  width: var(--yg-pg-slot, 42px);
  height: 3px;
  border-radius: 0;
  background: var(--yg-color-primary);
  box-shadow: none;
  clip-path: polygon(0 0, 100% 0, calc(100% - 6px) 100%, 0 100%);
  transform: translate3d(3px, 0, 0);
  transform-origin: 50% 100%;
  will-change: transform, scale;
  pointer-events: none;
}

.yg-pg__num {
  position: relative;
  z-index: 1;
  width: var(--yg-pg-slot, 42px);
  height: 34px;
  padding: 0;
  border: 0;
  border-radius: 0;
  background: transparent;
  color: var(--yg-text-secondary);
  font-size: 13px;
  font-variant-numeric: tabular-nums;
  cursor: pointer;
  transition: color 200ms ease;
}

.yg-pg__num:hover {
  color: var(--yg-text-primary);
}

.yg-pg__num:active {
  color: var(--yg-text-primary);
  opacity: 0.72;
}

.yg-pg__num.is-active {
  color: var(--yg-color-primary);
  font-weight: 600;
}

.yg-pg__num:focus-visible {
  outline: 2px solid var(--yg-color-primary);
  outline-offset: -2px;
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
  border: 0;
  border-bottom: 1px solid var(--yg-border-default);
  border-radius: 0;
  background: transparent;
  color: var(--yg-text-primary);
  font-size: 13px;
  text-align: center;
  font-variant-numeric: tabular-nums;
}

.yg-pg__jump-input:focus-visible {
  outline: none;
  border-bottom-color: var(--yg-color-primary);
  box-shadow: inset 0 -2px 0 var(--yg-color-primary);
}

/* 窄屏：只留 上一页 / 页码轨道 / 下一页，纵向排一行 */
.is-mobile {
  gap: 10px;
  padding: 8px 0;
}

@media (prefers-reduced-motion: reduce) {
  .yg-pg__btn,
  .yg-pg__num {
    transition: none;
  }

  .yg-pg__btn:active:not(:disabled) {
    transform: none;
  }
}
</style>
