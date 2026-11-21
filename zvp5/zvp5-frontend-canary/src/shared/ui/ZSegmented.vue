<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'

export interface SegmentOption {
  value: string
  label: string
}

const props = withDefaults(
  defineProps<{
    modelValue: string
    options: SegmentOption[]
    size?: 'sm' | 'md'
    ariaLabel?: string
  }>(),
  { size: 'md' }
)

const emit = defineEmits<{ 'update:modelValue': [value: string] }>()

/* 选中背景是一个独立滑块，切换时用弹簧过渡，而不是让文字背景硬切。 */
const root = ref<HTMLElement | null>(null)
const pill = reactive({ x: 0, width: 0, height: 0, ready: false })

function measure() {
  const container = root.value
  if (!container) return
  const active = container.querySelector<HTMLElement>('.segment.is-active')
  if (!active) {
    pill.ready = false
    return
  }
  pill.x = active.offsetLeft
  pill.width = active.offsetWidth
  pill.height = active.offsetHeight
  pill.ready = true
}

let ro: ResizeObserver | null = null

onMounted(() => {
  measure()
  if (root.value) {
    ro = new ResizeObserver(() => measure())
    ro.observe(root.value)
  }
})

onBeforeUnmount(() => ro?.disconnect())

watch(
  () => [props.modelValue, props.options.map((option) => option.value).join('|')],
  async () => {
    await nextTick()
    measure()
  }
)
</script>

<template>
  <div ref="root" class="z-segmented" :class="`size-${size}`" role="tablist" :aria-label="ariaLabel">
    <span
      class="seg-pill"
      :class="{ 'is-ready': pill.ready }"
      aria-hidden="true"
      :style="{
        transform: `translateX(${pill.x}px)`,
        width: `${pill.width}px`,
        height: `${pill.height}px`
      }"
    />
    <button
      v-for="option in options"
      :key="option.value"
      type="button"
      role="tab"
      class="segment"
      :class="{ 'is-active': option.value === modelValue }"
      :aria-selected="option.value === modelValue"
      @click="emit('update:modelValue', option.value)"
    >
      {{ option.label }}
    </button>
  </div>
</template>

<style scoped>
.z-segmented {
  position: relative;
  isolation: isolate;
  display: inline-flex;
  align-items: center;
  gap: 2px;
  padding: 4px;
  border-radius: var(--radius-pill);
  border: 1px solid color-mix(in srgb, var(--line) 78%, transparent);
  background:
    linear-gradient(180deg, color-mix(in srgb, var(--ink) 3%, transparent), transparent),
    color-mix(in srgb, var(--surface-sunken) 62%, transparent);
  box-shadow: inset 0 1px 2px color-mix(in srgb, var(--ink) 7%, transparent);
}

.seg-pill {
  position: absolute;
  left: 0;
  top: 0;
  z-index: 0;
  border-radius: var(--radius-pill);
  border: 1px solid color-mix(in srgb, var(--glass-border) 90%, var(--line));
  background:
    linear-gradient(155deg, color-mix(in srgb, var(--glass-hi) 34%, transparent), transparent 58%),
    var(--surface-raised);
  box-shadow:
    inset 0 1px 0 var(--glass-hi),
    0 6px 16px -10px rgba(40, 30, 16, 0.42);
  opacity: 0;
  pointer-events: none;
  transition:
    transform 420ms var(--spring-snappy),
    width 300ms var(--spring-snappy),
    height 300ms var(--spring-snappy),
    opacity 160ms var(--ease-out);
}

.seg-pill.is-ready {
  opacity: 1;
}

.segment {
  position: relative;
  z-index: 1;
  padding: 0.42rem 0.9rem;
  border-radius: var(--radius-pill);
  font-size: var(--text-sm);
  font-weight: 550;
  color: var(--ink-muted);
  white-space: nowrap;
  transition:
    color var(--dur-fast) var(--ease-out),
    transform var(--dur-micro) var(--spring-snappy);
}

.segment:active {
  transform: scale(0.96);
}

.size-sm .segment {
  padding: 0.3rem 0.7rem;
  font-size: var(--text-xs);
}

.segment:hover {
  color: var(--ink);
}

.segment.is-active {
  color: var(--ink);
}
</style>
