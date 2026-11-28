<script setup lang="ts">
import { onBeforeUnmount, ref } from 'vue'

/**
 * 3D 倾斜卡片：指针位置写 CSS 变量，倾斜与高光都交给合成层。
 * 离开后由 CSS 弹簧曲线回位，不做瞬移。
 */
const props = withDefaults(
  defineProps<{
    max?: number
    lift?: number
    disabled?: boolean
  }>(),
  { max: 4, lift: 3, disabled: false }
)

const root = ref<HTMLElement | null>(null)
let frame = 0
let pending: { x: number; y: number; px: number; py: number } | null = null

function flush() {
  frame = 0
  const el = root.value
  if (!el || !pending) return
  el.style.setProperty('--tilt-x', `${pending.x.toFixed(2)}deg`)
  el.style.setProperty('--tilt-y', `${pending.y.toFixed(2)}deg`)
  el.style.setProperty('--tilt-mx', `${pending.px.toFixed(1)}%`)
  el.style.setProperty('--tilt-my', `${pending.py.toFixed(1)}%`)
  pending = null
}

function onPointerMove(event: PointerEvent) {
  if (props.disabled) return
  const el = root.value
  if (!el) return
  const rect = el.getBoundingClientRect()
  if (!rect.width || !rect.height) return
  const px = (event.clientX - rect.left) / rect.width
  const py = (event.clientY - rect.top) / rect.height
  pending = {
    x: (0.5 - py) * 2 * props.max,
    y: (px - 0.5) * 2 * props.max,
    px: px * 100,
    py: py * 100
  }
  if (!frame) frame = requestAnimationFrame(flush)
}

function reset() {
  const el = root.value
  if (!el) return
  el.style.setProperty('--tilt-x', '0deg')
  el.style.setProperty('--tilt-y', '0deg')
  el.style.setProperty('--tilt-mx', '50%')
  el.style.setProperty('--tilt-my', '0%')
}

onBeforeUnmount(() => {
  if (frame) cancelAnimationFrame(frame)
  frame = 0
})
</script>

<template>
  <div
    ref="root"
    class="z-tilt"
    :style="{ '--tilt-lift': `${lift}px` }"
    @pointermove="onPointerMove"
    @pointerleave="reset"
  >
    <span class="tilt-sheen" aria-hidden="true" />
    <span class="tilt-body"><slot /></span>
  </div>
</template>

<style scoped>
.z-tilt {
  position: relative;
  border-radius: var(--radius-lg);
  transform: perspective(1100px) rotateX(var(--tilt-x, 0deg)) rotateY(var(--tilt-y, 0deg));
  translate: 0 0;
  transition:
    transform 420ms var(--spring-soft),
    translate 420ms var(--spring-soft),
    box-shadow 320ms var(--ease-out),
    border-color 320ms var(--ease-out);
  transform-style: preserve-3d;
}

.z-tilt:hover {
  translate: 0 calc(var(--tilt-lift, 0px) * -1);
}

.tilt-sheen {
  position: absolute;
  inset: 0;
  z-index: 2;
  border-radius: inherit;
  pointer-events: none;
  opacity: 0;
  background:
    radial-gradient(
      220px circle at var(--tilt-mx, 50%) var(--tilt-my, 0%),
      color-mix(in srgb, var(--glass-sheen) 40%, transparent),
      transparent 68%
    ),
    linear-gradient(126deg, transparent 42%, color-mix(in srgb, var(--accent) 9%, transparent) 100%);
  transition: opacity 340ms var(--ease-out);
}

.z-tilt:hover .tilt-sheen {
  opacity: 1;
}

.tilt-body {
  display: contents;
}

[data-theme='dark'] .tilt-sheen {
  background:
    radial-gradient(
      220px circle at var(--tilt-mx, 50%) var(--tilt-my, 0%),
      color-mix(in srgb, var(--glass-sheen) 70%, transparent),
      transparent 68%
    ),
    linear-gradient(126deg, transparent 46%, color-mix(in srgb, var(--accent) 7%, transparent) 100%);
}

@media (prefers-reduced-motion: reduce) {
  .z-tilt {
    transform: none;
    translate: 0 0;
    transition: none;
  }

  .z-tilt:hover {
    translate: 0 0;
  }

  .tilt-sheen {
    display: none;
  }
}
</style>
