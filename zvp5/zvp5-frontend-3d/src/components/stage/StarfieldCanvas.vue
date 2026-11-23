<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'

const props = defineProps<{
  warp?: boolean
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

interface Star {
  x: number
  y: number
  z: number
  pz: number
}

function init() {
  if (!canvas.value) return
  
  const dpr = Math.min(window.devicePixelRatio || 1, 2)
  const w = window.innerWidth
  const h = window.innerHeight
  
  canvas.value.width = w * dpr
  canvas.value.height = h * dpr
  canvas.value.style.width = `${w}px`
  canvas.value.style.height = `${h}px`
  
  ctx = canvas.value.getContext('2d', { alpha: true })
  if (!ctx) return
  
  ctx.scale(dpr, dpr)
  
  const count = w < 720 ? 400 : 700
  stars = Array.from({ length: count }, () => ({
    x: Math.random() * w - w / 2,
    y: Math.random() * h - h / 2,
    z: Math.random() * 1000,
    pz: 0
  }))
  
  stars.forEach(s => { s.pz = s.z })
}

function render() {
  if (!ctx || !canvas.value || !visible || document.hidden || props.reduced) return
  
  const w = canvas.value.width / Math.min(window.devicePixelRatio || 1, 2)
  const h = canvas.value.height / Math.min(window.devicePixelRatio || 1, 2)
  const cx = w / 2
  const cy = h / 2
  const speed = props.warp ? 18 : 1.2
  const warpStretch = props.warp ? 8 : 1
  
  ctx.fillStyle = 'rgba(0, 0, 0, 0.25)'
  ctx.globalCompositeOperation = 'destination-out'
  ctx.fillRect(0, 0, w, h)
  ctx.globalCompositeOperation = 'source-over'
  
  for (const star of stars) {
    star.z -= speed
    if (star.z <= 1) {
      star.z = 1000
      star.x = Math.random() * w - cx
      star.y = Math.random() * h - cy
      star.pz = star.z
    }
    
    const sx = (star.x / star.z) * 400 + cx
    const sy = (star.y / star.z) * 400 + cy
    const r = Math.max(0, (1 - star.z / 1000) * 2)
    
    if (sx < 0 || sx > w || sy < 0 || sy > h) {
      star.pz = star.z
      continue
    }
    
    const opacity = Math.max(0.1, 1 - star.z / 1000)
    
    if (props.warp && star.pz !== star.z) {
      const px = (star.x / star.pz) * 400 + cx
      const py = (star.y / star.pz) * 400 + cy
      const dx = sx - px
      const dy = sy - py
      const len = Math.sqrt(dx * dx + dy * dy) * warpStretch
      
      ctx.strokeStyle = `rgba(169, 211, 255, ${opacity * 0.6})`
      ctx.lineWidth = r
      ctx.beginPath()
      ctx.moveTo(px, py)
      ctx.lineTo(sx, sy)
      ctx.stroke()
    } else {
      ctx.fillStyle = `rgba(238, 246, 255, ${opacity})`
      ctx.beginPath()
      ctx.arc(sx, sy, r, 0, Math.PI * 2)
      ctx.fill()
    }
    
    star.pz = star.z
  }
}

function renderStatic() {
  if (!ctx || !canvas.value) return
  
  const w = canvas.value.width / Math.min(window.devicePixelRatio || 1, 2)
  const h = canvas.value.height / Math.min(window.devicePixelRatio || 1, 2)
  const cx = w / 2
  const cy = h / 2
  
  ctx.clearRect(0, 0, w, h)
  
  for (const star of stars) {
    const sx = (star.x / star.z) * 400 + cx
    const sy = (star.y / star.z) * 400 + cy
    const r = Math.max(0, (1 - star.z / 1000) * 1.5)
    
    if (sx < 0 || sx > w || sy < 0 || sy > h) continue
    
    const opacity = Math.max(0.1, 1 - star.z / 1000) * 0.6
    
    ctx.fillStyle = `rgba(238, 246, 255, ${opacity})`
    ctx.beginPath()
    ctx.arc(sx, sy, r, 0, Math.PI * 2)
    ctx.fill()
  }
}

function shouldRun() {
  return mounted && !props.reduced && visible && !document.hidden
}

function loop() {
  if (!mounted || !shouldRun()) {
    running = false
    return
  }
  render()
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
  if (!props.warp && stars.length) {
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
