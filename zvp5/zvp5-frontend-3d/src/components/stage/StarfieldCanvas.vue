<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'

const props = defineProps<{
  warp?: boolean
  reverse?: boolean
  reduced?: boolean
}>()

const canvas = ref<HTMLCanvasElement>()
let ctx: CanvasRenderingContext2D | null = null
let stars: Star[] = []
let frame = 0
let observer: IntersectionObserver | null = null
let visible = true
let mounted = false
let running = false
let viewW = 0
let viewH = 0
let lastRender = 0
const MAX_DPR = 1.5
const WARP_STRETCH = 3.2

interface Star {
  x: number
  y: number
  z: number
  pz: number
}

function init() {
  if (!canvas.value) return
  
  const dpr = Math.min(window.devicePixelRatio || 1, MAX_DPR)
  const w = window.innerWidth
  const h = window.innerHeight
  viewW = w
  viewH = h
  
  canvas.value.width = Math.round(w * dpr)
  canvas.value.height = Math.round(h * dpr)
  canvas.value.style.width = `${w}px`
  canvas.value.style.height = `${h}px`
  
  ctx = canvas.value.getContext('2d', { alpha: true })
  if (!ctx) return
  
  // Replacing the transform on every resize keeps the canvas from scaling twice.
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
  
  const count = w < 720 ? 105 : 180
  stars = Array.from({ length: count }, () => ({
    x: Math.random() * w - w / 2,
    y: Math.random() * h - h / 2,
    z: Math.random() * 1000,
    pz: 0
  }))
  
  stars.forEach(s => { s.pz = s.z })
}

function resetStar(star: Star, cx: number, cy: number, z: number) {
  star.z = z
  star.x = Math.random() * viewW - cx
  star.y = Math.random() * viewH - cy
  star.pz = z
}

function advance(star: Star, speed: number, cx: number, cy: number) {
  if (props.reverse) {
    star.z += speed
    if (star.z >= 1000) resetStar(star, cx, cy, 1)
  } else {
    star.z -= speed
    if (star.z <= 1) resetStar(star, cx, cy, 1000)
  }
}

function render() {
  if (!ctx || !canvas.value || !visible || document.hidden || props.reduced) return

  const w = viewW
  const h = viewH
  const cx = w / 2
  const cy = h / 2
  const warp = props.warp
  const speed = warp ? 16 : 0.9

  ctx.fillStyle = 'rgba(0, 0, 0, 0.5)'
  ctx.globalCompositeOperation = 'destination-out'
  ctx.fillRect(0, 0, w, h)
  ctx.globalCompositeOperation = 'source-over'

  if (warp) {
    // One batched path for every warp streak. Drawing 180 individual strokes
    // with per-star opacity was the main cause of transition jank.
    ctx.beginPath()
    let drew = false

    for (const star of stars) {
      advance(star, speed, cx, cy)

      const sx = (star.x / star.z) * 400 + cx
      const sy = (star.y / star.z) * 400 + cy
      const pz = Math.max(1, star.pz)
      const px = (star.x / pz) * 400 + cx
      const py = (star.y / pz) * 400 + cy

      if (sx < -120 || sx > w + 120 || sy < -120 || sy > h + 120) {
        star.pz = star.z
        continue
      }

      ctx.moveTo(px, py)
      ctx.lineTo(px + (sx - px) * WARP_STRETCH, py + (sy - py) * WARP_STRETCH)
      drew = true
      star.pz = star.z
    }

    if (drew) {
      ctx.strokeStyle = 'rgba(169, 211, 255, 0.5)'
      ctx.lineWidth = 1.1
      ctx.stroke()
    }
    return
  }

  for (const star of stars) {
    advance(star, speed, cx, cy)

    const sx = (star.x / star.z) * 400 + cx
    const sy = (star.y / star.z) * 400 + cy

    if (sx < 0 || sx > w || sy < 0 || sy > h) {
      star.pz = star.z
      continue
    }

    const r = Math.max(0.3, (1 - star.z / 1000) * 1.8)
    const opacity = Math.max(0.12, 1 - star.z / 1000)

    ctx.fillStyle = `rgba(238, 246, 255, ${opacity})`
    ctx.beginPath()
    ctx.arc(sx, sy, r, 0, Math.PI * 2)
    ctx.fill()

    star.pz = star.z
  }
}

function renderStatic() {
  if (!ctx || !canvas.value) return
  
  const w = viewW
  const h = viewH
  const cx = w / 2
  const cy = h / 2
  
  ctx.clearRect(0, 0, w, h)
  
  for (const star of stars) {
    const sx = (star.x / star.z) * 400 + cx
    const sy = (star.y / star.z) * 400 + cy
    const r = Math.max(0, (1 - star.z / 1000) * 1.3)
    
    if (sx < 0 || sx > w || sy < 0 || sy > h) continue
    
    const opacity = Math.max(0.1, 1 - star.z / 1000) * 0.55
    
    ctx.fillStyle = `rgba(238, 246, 255, ${opacity})`
    ctx.beginPath()
    ctx.arc(sx, sy, r, 0, Math.PI * 2)
    ctx.fill()
  }
}

function shouldRun() {
  return mounted && !props.reduced && visible && !document.hidden
}

function loop(now: number) {
  if (!mounted || !shouldRun()) {
    running = false
    return
  }
  // The starfield is ambient: 30fps keeps it smooth enough and leaves the main
  // thread for the 3D card transition. Warp streaks use the same cadence.
  if (now - lastRender >= 32) {
    render()
    lastRender = now
  }
  frame = requestAnimationFrame(loop)
}

function start() {
  if (!mounted || running || !shouldRun()) return
  running = true
  frame = requestAnimationFrame(loop)
}

function stop() {
  running = false
  cancelAnimationFrame(frame)
}

function handleVisibility(entries: IntersectionObserverEntry[]) {
  visible = entries[0]?.isIntersecting ?? false
  if (visible) start()
  else stop()
}

function handleVisibilityChange() {
  if (document.hidden) stop()
  else start()
}

function handleResize() {
  init()
  if (props.reduced) renderStatic()
  else start()
}

onMounted(() => {
  mounted = true
  init()

  if (props.reduced) {
    renderStatic()
  } else {
    start()
  }

  observer = new IntersectionObserver(handleVisibility, { threshold: 0 })
  if (canvas.value) observer.observe(canvas.value)

  document.addEventListener('visibilitychange', handleVisibilityChange)
  window.addEventListener('resize', handleResize)
})

onBeforeUnmount(() => {
  mounted = false
  stop()
  observer?.disconnect()
  document.removeEventListener('visibilitychange', handleVisibilityChange)
  window.removeEventListener('resize', handleResize)
})

watch(() => props.warp, () => {
  lastRender = 0
  if (!props.warp && stars.length) {
    stars.forEach(s => { s.pz = s.z })
  }
})

watch(() => props.reverse, () => {
  lastRender = 0
  if (stars.length) {
    stars.forEach(s => { s.pz = s.z })
  }
})

watch(() => props.reduced, (val) => {
  if (val) {
    stop()
    renderStatic()
  } else {
    start()
  }
})
</script>

<template>
  <canvas ref="canvas" class="starfield-canvas"></canvas>
</template>

<style scoped>
.starfield-canvas {
  position: fixed;
  inset: 0;
  width: 100%;
  height: 100%;
  pointer-events: none;
  z-index: 0;
}
</style>
