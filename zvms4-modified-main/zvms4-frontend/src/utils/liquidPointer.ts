/**
 * 液态玻璃的指针跟随：给按钮 / 图标键 / 导航项写入 --lx / --ly，
 * 由 liquid.css 用它渲染「镜面高光」。用事件委托实现，组件零改动。
 *
 * 同时维护按下态 .is-pressed（CSS 里做轻微内凹与缩小），
 * 触摸端在 pointerup/cancel 时复位，避免状态卡住。
 */
const SELECTOR = [
  '.yg2-btn',
  '.yg2-icon-btn',
  '.yg2-nav-item',
  '.yg2-seg__item',
  '.yg2-mobile-tabs__item'
].join(',')

let installed = false

export function installLiquidPointer() {
  if (installed || typeof window === 'undefined') {
    return
  }
  installed = true

  function paint(target: HTMLElement, clientX: number, clientY: number) {
    const rect = target.getBoundingClientRect()
    if (rect.width === 0 || rect.height === 0) {
      return
    }
    const x = ((clientX - rect.left) / rect.width) * 100
    const y = ((clientY - rect.top) / rect.height) * 100
    target.style.setProperty('--lx', `${Math.max(0, Math.min(100, x))}%`)
    target.style.setProperty('--ly', `${Math.max(0, Math.min(100, y))}%`)
  }

  window.addEventListener(
    'pointermove',
    (event) => {
      const target = (event.target as HTMLElement | null)?.closest<HTMLElement>(SELECTOR)
      if (target) {
        paint(target, event.clientX, event.clientY)
      }
    },
    { passive: true }
  )

  // 按下 / 抬起：给当前元素加 is-pressed，抬手（可能在别处）统一清掉
  window.addEventListener(
    'pointerdown',
    (event) => {
      const target = (event.target as HTMLElement | null)?.closest<HTMLElement>(SELECTOR)
      if (!target) {
        return
      }
      paint(target, event.clientX, event.clientY)
      target.classList.add('is-pressed')
    },
    { passive: true }
  )

  const release = () => {
    document.querySelectorAll<HTMLElement>('.is-pressed').forEach((el) => {
      if (el.matches(SELECTOR)) {
        el.classList.remove('is-pressed')
      }
    })
  }

  window.addEventListener('pointerup', release, { passive: true })
  window.addEventListener('pointercancel', release, { passive: true })
  window.addEventListener('blur', release)
}
