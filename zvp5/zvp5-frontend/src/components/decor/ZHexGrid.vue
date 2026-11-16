<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, useId } from 'vue'

const props = withDefaults(
  defineProps<{
    /** 六边形外接圆半径（= 边长），单位 px；越大网格越疏 */
    size?: number
    /** 描边颜色；默认继承宿主 currentColor，也接受 var(--x) */
    color?: string
    /** 整体不透明度 0–1 */
    opacity?: number
    /** 描边宽度 px */
    stroke?: number
    /** 缓慢漂移 */
    drift?: boolean
    /** 缓慢呼吸（亮度起伏） */
    flicker?: boolean
    /**
     * 自上而下的亮度扫过：每个周期里，亮带从顶部缓慢下移，
     * 经过的网格先变亮再回落。只动 mask-position，不重排。
     */
    sweep?: boolean
    /** 层叠位置，宿主按需覆盖 */
    zIndex?: number
  }>(),
  {
    size: 56,
    color: 'currentColor',
    opacity: 0.08,
    stroke: 1,
    drift: false,
    flicker: false,
    sweep: false,
    zIndex: 0
  }
)

const patternId = `z-hex-${useId().replace(/[^a-zA-Z0-9_-]/g, '')}`
const glowPatternId = `${patternId}-glow`

const radius = computed(() => props.size)
/** 六边形对边距 = √3·r，也是相邻列的水平间距 */
const columnStep = computed(() => Math.sqrt(3) * radius.value)
/** 相邻两行的垂直间距 = 1.5·r */
const rowStep = computed(() => radius.value * 1.5)

/**
 * 平铺周期：横向一个列距（√3·r），纵向两行（3·r）。
 * 两个向量都是蜂窝点阵的平移对称轴，所以按此周期切出的图案四边能严丝合缝。
 */
const tileWidth = computed(() => columnStep.value)
const tileHeight = computed(() => radius.value * 3)

const EPS = 1e-9

/** Liang–Barsky：把线段裁到矩形内，完全落在外面返回 null */
function clipSegment(
  x1: number,
  y1: number,
  x2: number,
  y2: number,
  w: number,
  h: number
): [number, number, number, number] | null {
  let t0 = 0
  let t1 = 1
  const dx = x2 - x1
  const dy = y2 - y1
  const bounds: Array<[number, number]> = [
    [-dx, x1],
    [dx, w - x1],
    [-dy, y1],
    [dy, h - y1]
  ]

  for (const [p, q] of bounds) {
    if (Math.abs(p) < EPS) {
      if (q < 0) return null
      continue
    }
    const r = q / p
    if (p < 0) {
      if (r > t1) return null
      if (r > t0) t0 = r
    } else {
      if (r < t0) return null
      if (r < t1) t1 = r
    }
  }

  return [x1 + t0 * dx, y1 + t0 * dy, x1 + t1 * dx, y1 + t1 * dy]
}

/**
 * 蜂窝线框（等距六边形、只描边无填充）。
 * 整圈六边形会让相邻格共用的棱画两遍；这里把棱裁进一个平铺周期后按坐标去重，
 * 共用的棱只留一份。周期边界上被截断的半条棱由相邻周期接上，四边无缝。
 */
const pathData = computed(() => {
  const r = radius.value
  const colStep = columnStep.value
  const rowH = rowStep.value
  const w = tileWidth.value
  const h = tileHeight.value
  const hw = colStep / 2
  // 整体横向错开四分之一个列距：让竖棱不压在周期边界上，
  // 否则边界处那条竖棱会被裁成半根描边，横竖粗细不一。
  const shift = colStep / 4

  // 尖顶朝上的正六边形，中心 (cx, cy)
  const vertices = (cx: number, cy: number): Array<[number, number]> => [
    [cx, cy - r],
    [cx - hw, cy - r / 2],
    [cx - hw, cy + r / 2],
    [cx, cy + r],
    [cx + hw, cy + r / 2],
    [cx + hw, cy - r / 2]
  ]

  const seen = new Set<string>()
  const parts: string[] = []

  // 取两个周期范围的蜂窝，越界的那一半由相邻周期补齐
  for (let row = -2; row <= 4; row += 1) {
    const cy = row * rowH
    const offset = row % 2 === 0 ? 0 : hw
    for (let col = -2; col <= 4; col += 1) {
      const cx = col * colStep + offset + shift
      const v = vertices(cx, cy)
      for (let i = 0; i < 6; i += 1) {
        const a = v[i]
        const b = v[(i + 1) % 6]
        const clipped = clipSegment(a[0], a[1], b[0], b[1], w, h)
        if (!clipped) continue
        const rx1 = Math.round(clipped[0] * 100) / 100
        const ry1 = Math.round(clipped[1] * 100) / 100
        const rx2 = Math.round(clipped[2] * 100) / 100
        const ry2 = Math.round(clipped[3] * 100) / 100
        const key =
          rx1 <= rx2 ? `${rx1},${ry1},${rx2},${ry2}` : `${rx2},${ry2},${rx1},${ry1}`
        if (seen.has(key)) continue
        seen.add(key)
        parts.push(`M${rx1} ${ry1}L${rx2} ${ry2}`)
      }
    }
  }

  return parts.join('')
})

const rootStyle = computed(() => ({
  color: props.color,
  zIndex: props.zIndex
}))

const isPaused = ref(false)

function syncVisibility(): void {
  isPaused.value = document.hidden
}

onMounted(() => {
  syncVisibility()
  document.addEventListener('visibilitychange', syncVisibility)
})

onBeforeUnmount(() => {
  document.removeEventListener('visibilitychange', syncVisibility)
})
</script>

<template>
  <div
    class="z-hex-grid"
    :class="{
      'z-hex-grid--drift': drift,
      'z-hex-grid--flicker': flicker,
      'z-hex-grid--sweep': sweep,
      'is-paused': isPaused
    }"
    aria-hidden="true"
    :style="rootStyle"
  >
    <div class="z-hex-grid__layer" :style="{ opacity }">
      <svg class="z-hex-grid__svg" xmlns="http://www.w3.org/2000/svg">
        <defs>
          <pattern
            :id="patternId"
            patternUnits="userSpaceOnUse"
            :width="tileWidth"
            :height="tileHeight"
          >
            <path :d="pathData" fill="none" stroke="currentColor" :stroke-width="stroke" />
          </pattern>
        </defs>
        <rect width="100%" height="100%" :fill="`url(#${patternId})`" />
      </svg>
    </div>
    <div v-if="sweep" class="z-hex-grid__glow-layer">
      <svg class="z-hex-grid__svg" xmlns="http://www.w3.org/2000/svg">
        <defs>
          <pattern
            :id="glowPatternId"
            patternUnits="userSpaceOnUse"
            :width="tileWidth"
            :height="tileHeight"
          >
            <path :d="pathData" fill="none" stroke="currentColor" :stroke-width="stroke" />
          </pattern>
        </defs>
        <rect width="100%" height="100%" :fill="`url(#${glowPatternId})`" />
      </svg>
    </div>
  </div>
</template>

<style scoped>
.z-hex-grid {
  position: absolute;
  inset: 0;
  overflow: hidden;
  pointer-events: none;
}

/* 漂移层比容器大一圈，平移时不会露出空白边缘 */
.z-hex-grid__layer {
  position: absolute;
  inset: -80px;
  will-change: transform;
}

.z-hex-grid__svg {
  display: block;
  width: 100%;
  height: 100%;
}

.z-hex-grid--drift .z-hex-grid__layer {
  animation: z-hex-drift 96s linear infinite;
}

/* 导航/页面背景的呼吸光只画在六边形边上：glow 层是一条窄亮带，自上而下扫过。 */
.z-hex-grid__glow-layer {
  position: absolute;
  inset: -80px;
  opacity: 0.9;
  pointer-events: none;
  filter:
    drop-shadow(0 0 3px var(--phi-breathe, currentColor))
    drop-shadow(0 0 9px var(--phi-breathe, currentColor));
  -webkit-mask-image: linear-gradient(
    to bottom,
    transparent 0%,
    transparent 38%,
    rgba(0, 0, 0, 0.18) 44%,
    #000 50%,
    rgba(0, 0, 0, 0.18) 56%,
    transparent 62%,
    transparent 100%
  );
  mask-image: linear-gradient(
    to bottom,
    transparent 0%,
    transparent 38%,
    rgba(0, 0, 0, 0.18) 44%,
    #000 50%,
    rgba(0, 0, 0, 0.18) 56%,
    transparent 62%,
    transparent 100%
  );
  -webkit-mask-size: 100% 100%;
  mask-size: 100% 100%;
  -webkit-mask-repeat: no-repeat;
  mask-repeat: no-repeat;
  -webkit-mask-position: 50% -50%;
  mask-position: 50% -50%;
  will-change: transform, mask-position;
  animation: z-hex-glow-sweep 10s cubic-bezier(0.4, 0, 0.6, 1) infinite;
}

.z-hex-grid--drift .z-hex-grid__glow-layer {
  animation:
    z-hex-drift 96s linear infinite,
    z-hex-glow-sweep 10s cubic-bezier(0.4, 0, 0.6, 1) infinite;
}

.z-hex-grid--flicker .z-hex-grid__svg {
  will-change: opacity;
  animation: z-hex-flicker 7s ease-in-out infinite;
}

@keyframes z-hex-glow-sweep {
  0% {
    -webkit-mask-position: 50% -50%;
    mask-position: 50% -50%;
  }
  100% {
    -webkit-mask-position: 50% 50%;
    mask-position: 50% 50%;
  }
}

@keyframes z-hex-drift {
  from {
    transform: translate3d(0, 0, 0);
  }
  to {
    transform: translate3d(-42px, -26px, 0);
  }
}

@keyframes z-hex-flicker {
  0%,
  100% {
    opacity: 0.62;
  }
  50% {
    opacity: 1;
  }
}

/* 页面不可见时冻结合成层 */
.z-hex-grid.is-paused .z-hex-grid__layer,
.z-hex-grid.is-paused .z-hex-grid__glow-layer,
.z-hex-grid.is-paused .z-hex-grid__svg {
  animation-play-state: paused;
}

@media (prefers-reduced-motion: reduce) {
  .z-hex-grid__layer,
  .z-hex-grid__glow-layer,
  .z-hex-grid__svg {
    animation: none !important;
  }

  .z-hex-grid__glow-layer {
    display: none;
  }
}

@media print {
  .z-hex-grid {
    display: none;
  }
}
</style>
