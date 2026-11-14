/**
 * 卡片指针坐标：把指针在卡片内的位置写成 --mx / --my（0–100%），
 * 供 card.css 里的聚光灯、全息彩虹、液态高光跟随使用。
 * 事件委托 + rAF 节流，避免每张卡各挂一个监听。
 */
const SELECTOR = [
  '.yg2-card--spotlight',
  '.yg2-card--holo',
  '.yg2-card--liquid',
  '.yg2-card--tilt'
].join(',')

let installed = false
let frame = 0
let pending: { el: HTMLElement; x: number; y: number } | null = null

function flush() {
  frame = 0
  if (!pending) {
    return
  }
  const { el, x, y } = pending
  pending = null
  const rect = el.getBoundingClientRect()
  if (!rect.width || !rect.height) {
    return
  }
  el.style.setProperty('--mx', `${((x - rect.left) / rect.width) * 100}%`)
  el.style.setProperty('--my', `${((y - rect.top) / rect.height) * 100}%`)
}

export function installCardPointer() {
  if (installed || typeof window === 'undefined') {
    return
  }
  installed = true
  window.addEventListener(
    'pointermove',
    (event) => {
      const el = (event.target as HTMLElement | null)?.closest<HTMLElement>(SELECTOR)
      if (!el) {
        return
      }
      pending = { el, x: event.clientX, y: event.clientY }
      if (!frame) {
        frame = requestAnimationFrame(flush)
      }
    },
    { passive: true }
  )
}
