<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'

/**
 * 数值增长：只在值变化时跑一段 rAF 缓动，文本本身仍然可选中、可读。
 * 系统开启减少动态效果时直接跳到目标值。
 */
const props = withDefaults(
  defineProps<{
    value: number | null
    decimals?: number
    duration?: number
    format?: (value: number) => string
  }>(),
  { decimals: 1, duration: 1000 }
)

const display = ref(0)
let frame = 0
let from = 0
let to = 0
let started = 0

const text = computed(() => {
  if (props.value === null) return '—'
  return props.format ? props.format(display.value) : display.value.toFixed(props.decimals)
})

function cancel() {
  if (frame) cancelAnimationFrame(frame)
  frame = 0
}

function step(now: number) {
  const progress = Math.min(1, (now - started) / props.duration)
  const eased = 1 - Math.pow(1 - progress, 3)
  display.value = from + (to - from) * eased
  if (progress < 1) {
    frame = requestAnimationFrame(step)
  } else {
    frame = 0
    display.value = to
  }
}

function animate(target: number) {
  cancel()
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
    display.value = target
    return
  }
  from = display.value
  to = target
  started = performance.now()
  frame = requestAnimationFrame(step)
}

watch(
  () => props.value,
  (value) => {
    if (value === null) {
      cancel()
      display.value = 0
      return
    }
    animate(value)
  },
  { immediate: true }
)

onBeforeUnmount(cancel)
</script>

<template>
  <span class="z-count num">{{ text }}</span>
</template>

<style scoped>
.z-count {
  display: inline-block;
  font-variant-numeric: tabular-nums;
}
</style>
