import { nextTick, onBeforeUnmount, onMounted, ref, type Ref } from 'vue'
import { AmbientLightEngine } from '@/shared/lib/ambientLight'

/**
 * 把 AmbientLightEngine 接到一个固定层上：
 * - 指针事件在 window 上被动监听，用 rAF 合并后交给引擎；
 * - 触摸设备不启用指针视差；小屏裁剪光团数量；
 * - 组件卸载时移除监听并停止帧循环。
 */
export function useAmbientLight(host: Ref<HTMLElement | null>) {
  const compact = ref(false)
  let engine: AmbientLightEngine | null = null
  let frame = 0
  let lastX = 0
  let lastY = 0
  let lastT = 0
  let pending: { x: number; y: number; speed: number } | null = null
  let mediaCompact: MediaQueryList | null = null
  let mediaQuiet: MediaQueryList | null = null
  let onCompactChange: (() => void) | null = null
  let onQuietChange: (() => void) | null = null

  function flush() {
    frame = 0
    if (!pending || !engine) return
    engine.pushPointer(pending.x, pending.y, pending.speed)
    pending = null
  }

  function onPointerMove(event: PointerEvent) {
    const now = event.timeStamp || performance.now()
    const dt = Math.max(1, now - (lastT || now))
    const speed = Math.hypot(event.clientX - lastX, event.clientY - lastY) / dt
    lastX = event.clientX
    lastY = event.clientY
    lastT = now
    pending = {
      x: (event.clientX / window.innerWidth) * 2 - 1,
      y: (event.clientY / window.innerHeight) * 2 - 1,
      speed
    }
    if (!frame) frame = requestAnimationFrame(flush)
  }

  function onPointerLeave() {
    engine?.releasePointer()
  }

  function bindPointer(enabled: boolean) {
    unbindPointer()
    if (!enabled) return
    window.addEventListener('pointermove', onPointerMove, { passive: true })
    window.addEventListener('blur', onPointerLeave)
    document.addEventListener('pointerleave', onPointerLeave)
  }

  function unbindPointer() {
    window.removeEventListener('pointermove', onPointerMove)
    window.removeEventListener('blur', onPointerLeave)
    document.removeEventListener('pointerleave', onPointerLeave)
  }

  function build() {
    if (!host.value) return
    const reduced = mediaQuiet?.matches ?? false
    const elements = Array.from(host.value.querySelectorAll<HTMLElement>('.ambient-blob'))
    engine = new AmbientLightEngine(host.value, elements, {
      reducedMotion: reduced,
      pointerEnabled: !reduced && !mediaCompact?.matches,
      compact: compact.value
    })
    engine.start()
    bindPointer(!reduced && !window.matchMedia('(pointer: coarse)').matches)
  }

  function rebuild() {
    engine?.destroy()
    engine = null
    build()
  }

  onMounted(async () => {
    mediaCompact = window.matchMedia('(max-width: 760px), (pointer: coarse)')
    mediaQuiet = window.matchMedia('(prefers-reduced-motion: reduce)')
    compact.value = mediaCompact.matches
    // 光团数量由 compact 决定，先等 DOM 收敛再绑定，避免引擎持有被移除的节点
    await nextTick()
    build()

    const handleCompactChange = () => {
      compact.value = mediaCompact?.matches ?? false
      void nextTick().then(rebuild)
    }
    const handleQuietChange = () => rebuild()
    onCompactChange = handleCompactChange
    onQuietChange = handleQuietChange
    mediaCompact.addEventListener('change', handleCompactChange)
    mediaQuiet.addEventListener('change', handleQuietChange)
  })

  onBeforeUnmount(() => {
    unbindPointer()
    if (frame) cancelAnimationFrame(frame)
    frame = 0
    if (mediaCompact && onCompactChange) mediaCompact.removeEventListener('change', onCompactChange)
    if (mediaQuiet && onQuietChange) mediaQuiet.removeEventListener('change', onQuietChange)
    engine?.destroy()
    engine = null
  })

  return { compact }
}
