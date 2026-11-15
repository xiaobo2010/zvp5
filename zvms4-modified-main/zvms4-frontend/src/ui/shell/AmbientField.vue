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
    <!-- 中部焦点光：玻璃需要一层「被照亮的底面」才显厚度 -->
    <div class="yg2-ambient__blob" />
    <div class="yg2-ambient__cursor" />
  </div>
</template>

<script lang="ts">
/**
 * 鼠标环境光：rAF + 线性插值写到 CSS 变量，避免事件里直接改样式触发重排。
 * 触屏设备或 reduced-motion 时完全不启动。
 */
let mouseOwnsLight = false

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
    // 有鼠标在动就由鼠标接管，重力感应让位（混合设备：平板接键盘/触控板）
    mouseOwnsLight = true
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

/**
 * 重力感应环境光：手机上光斑随设备倾斜移动。
 * 只接管粗指针（手机/平板）设备；有鼠标的设备继续走 `installAmbientCursor`。
 * iOS 13+ 需要用户手势里申请权限，所以第一下点按才真正启动。
 */
export function installAmbientTilt() {
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return () => {}
  if (typeof window.DeviceOrientationEvent === 'undefined') return () => {}

  let targetX = 50
  let targetY = 40
  let x = targetX
  let y = targetY
  let raf = 0
  let running = true
  let started = false
  let baselineBeta: number | null = null
  let baselineGamma: number | null = null
  let lastOrientation = 0

  const clamp = (value: number, min: number, max: number) => Math.min(max, Math.max(min, value))

  const onOrientation = (event: DeviceOrientationEvent) => {
    if (event.beta === null || event.gamma === null) return
    lastOrientation = Date.now()
    // 以第一帧的姿态为「正对屏幕」基准，之后的倾角相对它计算，避免不同握持姿势跳变
    if (baselineBeta === null || baselineGamma === null) {
      baselineBeta = event.beta
      baselineGamma = event.gamma
      return
    }
    // beta 前后倾（±35° 打满），gamma 左右倾（±30° 打满）
    const nx = clamp((event.gamma - baselineGamma) / 30, -1, 1)
    const ny = clamp((event.beta - baselineBeta) / 35, -1, 1)
    targetX = 50 + nx * 26
    targetY = 40 + ny * 22
  }

  const tick = () => {
    if (!running) return
    if (mouseOwnsLight) {
      // 鼠标接管后不再写变量，但它松手前我们也不算「在跑」
      raf = requestAnimationFrame(tick)
      return
    }
    // 3 秒没有新读数就慢慢回正，避免用户把手机放平后光斑卡在角落
    if (Date.now() - lastOrientation > 3000) {
      targetX += (50 - targetX) * 0.02
      targetY += (40 - targetY) * 0.02
    }
    x += (targetX - x) * 0.07
    y += (targetY - y) * 0.07
    const root = document.documentElement
    root.style.setProperty('--yg2-mx', `${x.toFixed(2)}%`)
    root.style.setProperty('--yg2-my', `${y.toFixed(2)}%`)
    root.style.setProperty('--yg2-px', `${(((x / 100) * 2) - 1).toFixed(3)}`)
    root.style.setProperty('--yg2-py', `${(((y / 100) * 2) - 1).toFixed(3)}`)
    raf = requestAnimationFrame(tick)
  }

  const start = () => {
    if (started) return
    started = true
    window.addEventListener('deviceorientation', onOrientation, { passive: true })
    window.addEventListener('deviceorientationabsolute', onOrientation as EventListener, { passive: true })
    raf = requestAnimationFrame(tick)
  }

  const requestPermission = async () => {
    const api = window.DeviceOrientationEvent as unknown as {
      requestPermission?: () => Promise<'granted' | 'denied'>
    }
    if (typeof api.requestPermission === 'function') {
      try {
        const state = await api.requestPermission()
        if (state === 'granted') start()
      } catch {
        // 用户拒绝或非安全上下文：保持静态光斑，不报错打扰
      }
      return
    }
    start()
  }

  // iOS 必须在用户手势里申请，所以挂在第一次点按/触摸上，只跑一次
  const onFirstGesture = () => {
    window.removeEventListener('pointerdown', onFirstGesture)
    window.removeEventListener('touchstart', onFirstGesture)
    void requestPermission()
  }
  window.addEventListener('pointerdown', onFirstGesture, { passive: true })
  window.addEventListener('touchstart', onFirstGesture, { passive: true })

  // 已经有权限的浏览器（Android）不必等手势，直接跑
  const api = window.DeviceOrientationEvent as unknown as { requestPermission?: unknown }
  if (typeof api.requestPermission !== 'function') {
    start()
  }

  const onVisibility = () => {
    running = !document.hidden
    if (running && started) raf = requestAnimationFrame(tick)
    else cancelAnimationFrame(raf)
  }
  document.addEventListener('visibilitychange', onVisibility)

  return () => {
    running = false
    cancelAnimationFrame(raf)
    window.removeEventListener('pointerdown', onFirstGesture)
    window.removeEventListener('touchstart', onFirstGesture)
    window.removeEventListener('deviceorientation', onOrientation)
    window.removeEventListener('deviceorientationabsolute', onOrientation as EventListener)
    document.removeEventListener('visibilitychange', onVisibility)
  }
}
</script>
