import type { Directive } from 'vue'

/**
 * v-reveal：元素进入视口时淡入上浮。
 * 只在首次可见时触发一次，然后断开观察；系统减少动态效果时直接跳过。
 */
const observers = new WeakMap<HTMLElement, IntersectionObserver>()

export const reveal: Directive<HTMLElement> = {
  mounted(el) {
    if (
      typeof IntersectionObserver === 'undefined' ||
      window.matchMedia('(prefers-reduced-motion: reduce)').matches
    ) {
      return
    }

    el.classList.add('reveal')
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (!entry.isIntersecting) continue
          el.classList.add('is-revealed')
          observer.disconnect()
          observers.delete(el)
        }
      },
      { threshold: 0.08, rootMargin: '0px 0px -6% 0px' }
    )
    observer.observe(el)
    observers.set(el, observer)
  },
  unmounted(el) {
    observers.get(el)?.disconnect()
    observers.delete(el)
  }
}
