<script setup lang="ts">
import { ref } from 'vue'

/**
 * 3D 倾斜卡片（规范 11）：最多 10 度，表面反光跟着指针走。
 * 指针离开时用同一套过渡回正，避免「啪」地弹回去。
 */
const props = withDefaults(defineProps<{ max?: number }>(), { max: 10 })

const root = ref<HTMLElement>()
const tilt = ref({ rx: 0, ry: 0 })

function onMove(event: PointerEvent) {
  const el = root.value
  if (!el) {
    return
  }
  const rect = el.getBoundingClientRect()
  const px = (event.clientX - rect.left) / rect.width - 0.5
  const py = (event.clientY - rect.top) / rect.height - 0.5
  tilt.value = {
    rx: Number((-py * props.max).toFixed(2)),
    ry: Number((px * props.max).toFixed(2))
  }
}

function reset() {
  tilt.value = { rx: 0, ry: 0 }
}
</script>

<template>
  <div
    ref="root"
    class="yg2-card--tilt"
    :style="{
      transform: `perspective(900px) rotateX(${tilt.rx}deg) rotateY(${tilt.ry}deg)`
    }"
    @pointermove="onMove"
    @pointerleave="reset"
  >
    <slot />
  </div>
</template>
