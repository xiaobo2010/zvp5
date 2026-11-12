/**
 * 大框流光的随机相位。
 *
 * 所有框共用同一条 7.6s 的巡边动画，如果都从 0deg 起步，整页会像同步闪烁。
 * 这里给每个新建的大框写一个随机的负 animation-delay；相位只写一次，
 * 后续重渲染不再变化，避免同一块框每次闪到不同位置。
 */

const CARD_SELECTOR = '.yg-phi .el-card, .yg-phi .yg-card, .yg-phi .phi-time__panel'
const FLOW_DURATION_MS = 7600
const PHASE_ATTR = 'data-phi-edge-phase'
const MUTATION_DEBOUNCE_MS = 120

let observer: MutationObserver | null = null
let pendingTimer: number | null = null

function stamp(root: ParentNode): void {
  root.querySelectorAll<HTMLElement>(CARD_SELECTOR).forEach((el) => {
    if (el.hasAttribute(PHASE_ATTR)) {
      return
    }
    el.setAttribute(PHASE_ATTR, '')
    const phase = -Math.round(Math.random() * FLOW_DURATION_MS)
    el.style.setProperty('--phi-edge-delay', `${phase}ms`)
  })
}

function scheduleStamp(): void {
  if (pendingTimer !== null) {
    return
  }
  pendingTimer = window.setTimeout(() => {
    pendingTimer = null
    stamp(document)
  }, MUTATION_DEBOUNCE_MS)
}

export function startPhiEdgePhase(): void {
  if (typeof window === 'undefined' || observer) {
    return
  }
  stamp(document)
  observer = new MutationObserver(scheduleStamp)
  observer.observe(document.body, { childList: true, subtree: true })
}

export function stopPhiEdgePhase(): void {
  observer?.disconnect()
  observer = null
  if (pendingTimer !== null) {
    window.clearTimeout(pendingTimer)
    pendingTimer = null
  }
}
