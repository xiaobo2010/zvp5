<script setup lang="ts">
import { computed, onMounted, ref, useId, watch } from 'vue'

/**
 * 环形进度：SVG 描边 + 金色渐变 + 柔和外发光。
 * 首次挂载时从 0 绘制到目标值，后续数值变化继续用同一条描边动画。
 */
const props = withDefaults(
  defineProps<{
    value: number
    size?: number
    stroke?: number
    tone?: 'gold' | 'sage'
    glow?: boolean
    label?: string
  }>(),
  { size: 180, stroke: 10, tone: 'gold', glow: true }
)

const uid = useId().replace(/[^a-zA-Z0-9_-]/g, '')
const gradientId = `zg-${uid}`
const glowId = `zg-glow-${uid}`

const radius = computed(() => (props.size - props.stroke) / 2 - 3)
const circumference = computed(() => 2 * Math.PI * radius.value)
const percent = computed(() => Math.min(100, Math.max(0, props.value)))
const offset = ref(circumference.value)

function applyPercent() {
  offset.value = circumference.value * (1 - percent.value / 100)
}

onMounted(() => {
  // 先绘制 0，再在下一帧写入目标值，让 CSS 过渡真正跑起来
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
    applyPercent()
    return
  }
  requestAnimationFrame(() => requestAnimationFrame(applyPercent))
})

watch([percent, circumference], applyPercent)
</script>

<template>
  <div class="z-gauge" :style="{ width: `${size}px`, height: `${size}px` }">
    <svg
      class="gauge-svg"
      :viewBox="`0 0 ${size} ${size}`"
      role="img"
      :aria-label="label ?? `${Math.round(percent)}%`"
    >
      <defs>
        <linearGradient :id="gradientId" x1="0" y1="0" x2="1" y2="1">
          <template v-if="tone === 'sage'">
            <stop offset="0" stop-color="#cfe0cf" />
            <stop offset="0.5" stop-color="#8fae94" />
            <stop offset="1" stop-color="#5d7461" />
          </template>
          <template v-else>
            <stop offset="0" stop-color="#f8e2b6" />
            <stop offset="0.45" stop-color="#d9a752" />
            <stop offset="1" stop-color="#a86e1d" />
          </template>
        </linearGradient>
        <filter :id="glowId" x="-30%" y="-30%" width="160%" height="160%">
          <feGaussianBlur stdDeviation="3.2" result="blur" />
          <feMerge>
            <feMergeNode in="blur" />
            <feMergeNode in="SourceGraphic" />
          </feMerge>
        </filter>
      </defs>

      <circle
        class="gauge-track"
        :cx="size / 2"
        :cy="size / 2"
        :r="radius"
        :stroke-width="stroke"
        fill="none"
      />
      <circle
        class="gauge-arc"
        :cx="size / 2"
        :cy="size / 2"
        :r="radius"
        :stroke-width="stroke"
        :stroke="`url(#${gradientId})`"
        :stroke-dasharray="circumference"
        :stroke-dashoffset="offset"
        :filter="glow ? `url(#${glowId})` : undefined"
        fill="none"
        stroke-linecap="round"
        :transform="`rotate(-90 ${size / 2} ${size / 2})`"
      />
    </svg>
    <div class="gauge-center">
      <slot />
    </div>
  </div>
</template>

<style scoped>
.z-gauge {
  position: relative;
  display: grid;
  place-items: center;
  flex: none;
}

.gauge-svg {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  overflow: visible;
}

.gauge-track {
  stroke: color-mix(in srgb, var(--ink) 8%, transparent);
}

.gauge-arc {
  transition: stroke-dashoffset 1200ms var(--spring-soft);
}

.gauge-center {
  position: relative;
  z-index: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  text-align: center;
}

@media (prefers-reduced-motion: reduce) {
  .gauge-arc {
    transition: none;
  }
}
</style>
