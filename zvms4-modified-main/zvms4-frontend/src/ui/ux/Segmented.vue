<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

/**
 * 液态玻璃分段控件。
 *
 * 旧实现是「点一下换一个按钮的背景」，切换生硬。这里按 AndroidLiquidGlass
 * 的做法重做：一枚会变形的玻璃滑块（thumb）在选项之间弹动，按下时跟随指针
 * 位移并轻微变宽，松手用带回弹的弹簧曲线归位；滑块本身是玻璃材质，
 * 叠了镜面高光与内描边。
 */
interface SegmentItem {
  value: string
  label: string
}

const props = withDefaults(
  defineProps<{
    modelValue: string
    items: SegmentItem[]
    /** 无障碍名称 */
    ariaLabel?: string
  }>(),
  { ariaLabel: '' }
)

const emit = defineEmits<{ 'update:modelValue': [value: string] }>()

const root = ref<HTMLElement>()
const buttons = ref<HTMLElement[]>([])
const thumb = ref({ x: 0, width: 0 })
const dragging = ref(false)
const dragOffset = ref(0)

const activeIndex = computed(() =>
  Math.max(0, props.items.findIndex((item) => item.value === props.modelValue))
)

function measure(index: number) {
  const el = buttons.value[index]
  const host = root.value
  if (!el || !host) {
    return
  }
  const a = el.getBoundingClientRect()
  const b = host.getBoundingClientRect()
  thumb.value = { x: a.left - b.left, width: a.width }
}

async function sync(index = activeIndex.value) {
  await nextTick()
  measure(index)
}

watch(() => [props.items.length, props.modelValue], () => void sync())
onMounted(() => {
  void sync()
  window.addEventListener('resize', onResize)
})

function onResize() {
  void sync()
}

onBeforeUnmount(() => window.removeEventListener('resize', onResize))

/* ---------- 指针拖动：滑块跟着手走，松手吸附 ---------- */
let startX = 0
let pointerId: number | null = null

function onPointerDown(event: PointerEvent) {
  if (event.button !== 0) {
    return
  }
  dragging.value = true
  startX = event.clientX
  pointerId = event.pointerId
  ;(event.currentTarget as HTMLElement).setPointerCapture?.(event.pointerId)
}

function onPointerMove(event: PointerEvent) {
  if (!dragging.value) {
    return
  }
  // 拖拽位移带上阻尼，避免滑块比手指跑得快
  dragOffset.value = (event.clientX - startX) * 0.85
}

function onPointerUp(event: PointerEvent) {
  if (!dragging.value) {
    return
  }
  dragging.value = false
  dragOffset.value = 0
  if (pointerId !== null) {
    pointerId = null
  }
  // 用松手位置找最近的按钮，实现「拖到哪个就停在哪个」
  const host = root.value
  if (!host) {
    return
  }
  const hit = buttons.value.find((el, index) => {
    const r = el.getBoundingClientRect()
    const inside =
      event.clientX >= r.left - 8 &&
      event.clientX <= r.right + 8
    return inside && index !== activeIndex.value
  })
  if (hit) {
    const index = buttons.value.indexOf(hit)
    const item = props.items[index]
    if (item) {
      emit('update:modelValue', item.value)
    }
  }
}

function select(value: string) {
  if (value !== props.modelValue) {
    emit('update:modelValue', value)
  }
}
</script>

<template>
  <div
    ref="root"
    class="yg2-seg"
    role="tablist"
    :aria-label="ariaLabel || undefined"
    :class="{ 'is-dragging': dragging }"
    @pointerdown="onPointerDown"
    @pointermove="onPointerMove"
    @pointerup="onPointerUp"
    @pointercancel="onPointerUp"
  >
    <span
      class="yg2-seg__thumb"
      aria-hidden="true"
      :style="{
        transform: `translate3d(${thumb.x + dragOffset}px, 0, 0)`,
        width: `${thumb.width}px`
      }"
    />
    <button
      v-for="(item, index) in items"
      :key="item.value"
      ref="buttons"
      class="yg2-seg__item"
      :class="{ 'is-active': item.value === modelValue }"
      type="button"
      role="tab"
      :aria-selected="item.value === modelValue"
      :tabindex="index === activeIndex ? 0 : -1"
      @click="select(item.value)"
    >
      {{ item.label }}
    </button>
  </div>
</template>
