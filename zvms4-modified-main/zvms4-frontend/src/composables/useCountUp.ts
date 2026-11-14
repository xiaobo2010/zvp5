import { onBeforeUnmount, ref, watch } from 'vue'

/**
 * 数字滚动：从 0（或上一次的值）缓动到目标值。
 *
 * 用 setTimeout + performance.now 驱动而不是 requestAnimationFrame：
 * rAF 在后台标签页/无头环境里会被限流甚至完全不触发，数字会直接跳到终值；
 * 计数器只跑 1 秒左右，16ms 的定时器开销可以忽略，换来的是「一定会动」。
 * 曲线用 easeOutExpo —— 前段快、后段收得慢，比线性更像计数器归位。
 */
export function useCountUp(target: () => number, duration = 900, fps = 60) {
  const value = ref(0)
  let timer: ReturnType<typeof setTimeout> | undefined
  let startedAt = 0
  let from = 0
  let to = 0

  function easeOutExpo(t: number) {
    return t === 1 ? 1 : 1 - Math.pow(2, -10 * t)
  }

  function step() {
    const now = performance.now()
    if (!startedAt) {
      startedAt = now
    }
    const progress = Math.min(1, (now - startedAt) / duration)
    value.value = from + (to - from) * easeOutExpo(progress)
    if (progress < 1) {
      timer = setTimeout(step, 1000 / fps)
    }
  }

  function run(next: number) {
    clearTimeout(timer)
    from = value.value
    to = next
    startedAt = 0
    // 目标相同就不重跑，避免折算开关一开一关时来回抖
    if (from === to) {
      value.value = to
      return
    }
    step()
  }

  watch(
    target,
    (next) => {
      if (!Number.isFinite(next)) {
        return
      }
      run(next)
    },
    { immediate: true }
  )

  onBeforeUnmount(() => clearTimeout(timer))

  return value
}
