import { onBeforeUnmount, onMounted, ref } from 'vue'

/**
 * 触屏下拉刷新：只在滚动到顶时接管手势，松手触发 onRefresh。
 * 阈值 64px，回弹用 CSS transition，桌面端不注册。
 */
export function usePullToRefresh(onRefresh: () => Promise<unknown> | void, enabled = true) {
  const pulling = ref(false)
  const distance = ref(0)
  const refreshing = ref(false)
  let startY = 0
  let tracking = false

  const threshold = 64

  function onTouchStart(event: TouchEvent) {
    if (!enabled || refreshing.value) {
      return
    }
    const scroller = document.scrollingElement || document.documentElement
    if (scroller.scrollTop > 0) {
      return
    }
    startY = event.touches[0]?.clientY ?? 0
    tracking = true
  }

  function onTouchMove(event: TouchEvent) {
    if (!tracking) {
      return
    }
    const current = event.touches[0]?.clientY ?? startY
    const delta = Math.max(0, current - startY)
    if (delta > 0) {
      pulling.value = true
      // 阻尼，越拉越沉
      distance.value = Math.min(96, delta * 0.55)
    }
  }

  async function onTouchEnd() {
    if (!tracking) {
      return
    }
    tracking = false
    const shouldRefresh = distance.value >= threshold * 0.55
    distance.value = 0
    pulling.value = false
    if (!shouldRefresh) {
      return
    }
    refreshing.value = true
    try {
      await onRefresh()
    } finally {
      refreshing.value = false
    }
  }

  onMounted(() => {
    if (!enabled || typeof window === 'undefined' || !('ontouchstart' in window)) {
      return
    }
    window.addEventListener('touchstart', onTouchStart, { passive: true })
    window.addEventListener('touchmove', onTouchMove, { passive: true })
    window.addEventListener('touchend', onTouchEnd)
  })

  onBeforeUnmount(() => {
    window.removeEventListener('touchstart', onTouchStart)
    window.removeEventListener('touchmove', onTouchMove)
    window.removeEventListener('touchend', onTouchEnd)
  })

  return { pulling, distance, refreshing }
}
