<script setup lang="ts">
/**
 * 环境光场：背后缓慢漂移的低饱和光斑，只负责氛围，不参与交互。
 * tone 由外壳按板块传入（activities / groups / ai / manage），换色相不换结构。
 */
withDefaults(defineProps<{ tone?: string }>(), { tone: 'activities' })
</script>

<template>
  <div class="yg2-ambient" :data-ambient="tone" aria-hidden="true">
    <div class="yg2-ambient__blob" />
    <div class="yg2-ambient__blob" />
    <div class="yg2-ambient__blob" />
    <div class="yg2-ambient__cursor" />
  </div>
</template>

<script lang="ts">
/**
 * 鼠标环境光：rAF + 线性插值写到 CSS 变量，避免事件里直接改样式触发重排。
 * 触屏设备或 reduced-motion 时完全不启动。
 */
export function installAmbientCursor() {
  if (window.matchMedia('(pointer: coarse)').matches) return () => {}
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return () => {}
  let targetX = window.innerWidth * 0.5
  let targetY = window.innerHeight * 0.4
  let x = targetX
  let y = targetY
  let raf = 0
  let running = true

  const onMove = (event: PointerEvent) => {
    targetX = event.clientX
    targetY = event.clientY
  }

  const tick = () => {
    if (!running) return
    x += (targetX - x) * 0.06
    y += (targetY - y) * 0.06
    const root = document.documentElement
    root.style.setProperty('--yg2-cx', `${x.toFixed(1)}px`)
    root.style.setProperty('--yg2-cy', `${y.toFixed(1)}px`)
    root.style.setProperty('--yg2-mx', `${((x / window.innerWidth) * 100).toFixed(2)}%`)
    root.style.setProperty('--yg2-my', `${((y / window.innerHeight) * 100).toFixed(2)}%`)
    root.style.setProperty('--yg2-px', `${((x / window.innerWidth) * 2 - 1).toFixed(3)}`)
    root.style.setProperty('--yg2-py', `${((y / window.innerHeight) * 2 - 1).toFixed(3)}`)
    raf = requestAnimationFrame(tick)
  }

  window.addEventListener('pointermove', onMove, { passive: true })
  const onVisibility = () => {
    running = !document.hidden
    if (running) raf = requestAnimationFrame(tick)
    else cancelAnimationFrame(raf)
  }
  document.addEventListener('visibilitychange', onVisibility)
  raf = requestAnimationFrame(tick)

  return () => {
    running = false
    cancelAnimationFrame(raf)
    window.removeEventListener('pointermove', onMove)
    document.removeEventListener('visibilitychange', onVisibility)
  }
}
</script>
