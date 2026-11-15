/**
 * 数字缓动：从当前显示值过渡到目标值，只用于展示层，不参与任何真实数据计算。
 * 用 rAF + 缓出曲线，页面隐藏或 reduced-motion 时直接给终值。
 */
import { ref, watch, onBeforeUnmount, type Ref } from 'vue'

function easeOut(t: number) {
  return 1 - Math.pow(1 - t, 3)
}

export function useCountUp(target: Ref<number>, duration = 720) {
  const display = ref(0)
  let raf = 0
  let from = 0
  let start = 0

  const stop = () => {
    if (raf) cancelAnimationFrame(raf)
    raf = 0
  }

  const run = (to: number) => {
    stop()
    const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    if (reduce || document.hidden) {
      display.value = to
      return
    }
    from = display.value
    start = performance.now()
    const tick = (now: number) => {
      const t = Math.min(1, (now - start) / duration)
      display.value = from + (to - from) * easeOut(t)
      if (t < 1) raf = requestAnimationFrame(tick)
      else raf = 0
    }
    raf = requestAnimationFrame(tick)
  }

  watch(target, (to) => run(to), { immediate: true })
  onBeforeUnmount(stop)

  return display
}
