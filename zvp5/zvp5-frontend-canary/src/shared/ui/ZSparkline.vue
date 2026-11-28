<script setup lang="ts">
import { computed, onMounted, ref, useId, watch } from 'vue'

/**
 * 微型趋势线：把真实的月度累计值画成平滑曲线。
 * 只有描边在绘制，面积填充与端点做淡入，成本远低于逐点粒子。
 */
const props = withDefaults(
  defineProps<{
    values: number[]
    width?: number
    height?: number
    tone?: 'gold' | 'sage'
    area?: boolean
  }>(),
  { width: 132, height: 42, tone: 'gold', area: true }
)

const uid = useId().replace(/[^a-zA-Z0-9_-]/g, '')
const strokeId = `zs-${uid}`
const areaId = `zs-area-${uid}`

const PAD = 3

const points = computed(() => {
  const list = props.values.length ? props.values : [0]
  const max = Math.max(...list, 0.0001)
  const stepX = list.length > 1 ? (props.width - PAD * 2) / (list.length - 1) : 0
  return list.map((value, index) => ({
    x: PAD + index * stepX,
    y: props.height - PAD - (value / max) * (props.height - PAD * 2)
  }))
})

/** Catmull-Rom 转三次贝塞尔：曲线经过所有真实数据点，不做额外假设 */
const linePath = computed(() => {
  const list = points.value
  const first = list[0]
  if (!first) return ''
  if (list.length === 1) return `M ${first.x} ${first.y}`

  let path = `M ${first.x.toFixed(2)} ${first.y.toFixed(2)}`
  for (let index = 0; index < list.length - 1; index += 1) {
    const p1 = list[index]
    const p2 = list[index + 1]
    if (!p1 || !p2) continue
    const p0 = list[index - 1] ?? p1
    const p3 = list[index + 2] ?? p2
    const c1x = p1.x + (p2.x - p0.x) / 6
    const c1y = p1.y + (p2.y - p0.y) / 6
    const c2x = p2.x - (p3.x - p1.x) / 6
    const c2y = p2.y - (p3.y - p1.y) / 6
    path += ` C ${c1x.toFixed(2)} ${c1y.toFixed(2)}, ${c2x.toFixed(2)} ${c2y.toFixed(2)}, ${p2.x.toFixed(2)} ${p2.y.toFixed(2)}`
  }
  return path
})

const areaPath = computed(() => {
  const list = points.value
  const first = list[0]
  const last = list[list.length - 1]
  if (list.length < 2 || !first || !last) return ''
  return `${linePath.value} L ${last.x.toFixed(2)} ${props.height - PAD} L ${first.x.toFixed(2)} ${props.height - PAD} Z`
})

const endPoint = computed(() => points.value[points.value.length - 1] ?? { x: 0, y: 0 })

const line = ref<SVGPathElement | null>(null)
const drawn = ref(false)

function draw() {
  const el = line.value
  if (!el) return
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
    drawn.value = true
    return
  }
  const length = el.getTotalLength()
  el.style.strokeDasharray = String(length)
  el.style.strokeDashoffset = String(length)
  requestAnimationFrame(() => {
    el.style.strokeDashoffset = '0'
    drawn.value = true
  })
}

onMounted(draw)
watch(linePath, () => {
  if (!drawn.value) return
  // 数据更新后重画，但不再从 0 开始，只做一次很短的重新描边
  requestAnimationFrame(draw)
})
</script>

<template>
  <svg
    class="z-spark"
    :viewBox="`0 0 ${width} ${height}`"
    preserveAspectRatio="none"
    aria-hidden="true"
  >
    <defs>
      <linearGradient :id="strokeId" x1="0" y1="0" x2="1" y2="0">
        <template v-if="tone === 'sage'">
          <stop offset="0" stop-color="#a9c2ad" />
          <stop offset="1" stop-color="#5d7461" />
        </template>
        <template v-else>
          <stop offset="0" stop-color="#eed9ab" />
          <stop offset="0.55" stop-color="#cfa04b" />
          <stop offset="1" stop-color="#a86e1d" />
        </template>
      </linearGradient>
      <linearGradient :id="areaId" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0" stop-color="var(--spark-fill, rgba(196, 148, 66, 0.26))" />
        <stop offset="1" stop-color="transparent" />
      </linearGradient>
    </defs>

    <path
      v-if="area && areaPath"
      class="spark-area"
      :class="{ 'is-drawn': drawn }"
      :d="areaPath"
      :fill="`url(#${areaId})`"
    />
    <path
      ref="line"
      class="spark-line"
      :d="linePath"
      :stroke="`url(#${strokeId})`"
      fill="none"
      stroke-width="1.8"
      stroke-linecap="round"
      stroke-linejoin="round"
    />
    <circle
      class="spark-dot"
      :class="{ 'is-drawn': drawn }"
      :cx="endPoint.x"
      :cy="endPoint.y"
      r="2.6"
    />
  </svg>
</template>

<style scoped>
.z-spark {
  display: block;
  width: 100%;
  height: 100%;
  overflow: visible;
}

.spark-line {
  transition: stroke-dashoffset 1400ms var(--spring-soft);
}

.spark-area {
  opacity: 0;
  transition: opacity 700ms var(--ease-out) 260ms;
}

.spark-area.is-drawn {
  opacity: 1;
}

.spark-dot {
  fill: var(--accent-strong, #8c5a12);
  opacity: 0;
  transition: opacity 320ms var(--ease-out) 900ms;
}

.spark-dot.is-drawn {
  opacity: 1;
}

[data-theme='dark'] .z-spark {
  --spark-fill: rgba(226, 178, 96, 0.2);
}

@media (prefers-reduced-motion: reduce) {
  .spark-line,
  .spark-area,
  .spark-dot {
    transition: none;
  }
}
</style>
