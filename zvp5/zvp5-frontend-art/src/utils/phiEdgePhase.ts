/**
 * 大框流光的随机相位 + 屏外暂停。
 *
 * 所有框共用同一条 7.6s 的巡边动画，如果都从 0deg 起步，整页会像同步闪烁。
 * 这里给每个新建的大框写一个随机的负 animation-delay；相位只写一次，
 * 后续重渲染不再变化，避免同一块框每次闪到不同位置。
 *
 * 第二职责：流光只在框进入视口附近时才值得跑。用 IntersectionObserver
 * 给屏外的框打 `data-phi-edge-idle`，由 phi-theme.css 把 `::after` 的
 * animation-play-state 暂停（该 CSS 属另一条 lane）。rootMargin 留 200px，
 * 让框滚入前已经重启，不会看到“刚亮起来”的突兀感。
 *
 * 低 GPU 档（`html[data-low-gpu]`）下流光在 CSS 里是静态的（不做 animation），
 * 相位与可见性都失去意义，因此整个模块直接不启动：不建 MutationObserver、
 * 不建 IntersectionObserver，也不写属性，省掉低端设备上的常驻观察器开销。
 */

const CARD_SELECTOR = '.yg-phi .el-card, .yg-phi .yg-card, .yg-phi .phi-time__panel'
const FLOW_DURATION_MS = 7600
const PHASE_ATTR = 'data-phi-edge-phase'
const IDLE_ATTR = 'data-phi-edge-idle'
const MUTATION_DEBOUNCE_MS = 120
/** 视口上下各预留 200px：框接近视口边缘时恢复，滚入时已经是常态相位。 */
const IDLE_ROOT_MARGIN = '200px 0px'

let observer: MutationObserver | null = null
let idleObserver: IntersectionObserver | null = null
let pendingTimer: number | null = null

function isLowGpu(): boolean {
  return typeof document !== 'undefined' && document.documentElement.hasAttribute('data-low-gpu')
}

function getIntersectionObserverCtor(): typeof IntersectionObserver | undefined {
  const scope = globalThis as typeof globalThis & {
    IntersectionObserver?: typeof IntersectionObserver
  }
  return typeof scope.IntersectionObserver === 'function' ? scope.IntersectionObserver : undefined
}

function createIdleObserver(): IntersectionObserver | null {
  const Ctor = getIntersectionObserverCtor()
  if (!Ctor) {
    // 老 WebView 没有 IO：保留相位逻辑，只是不做屏外暂停
    return null
  }
  return new Ctor(
    (entries) => {
      entries.forEach((entry) => {
        const el = entry.target as HTMLElement
        if (entry.isIntersecting) {
          el.removeAttribute(IDLE_ATTR)
        } else {
          el.setAttribute(IDLE_ATTR, '')
        }
      })
    },
    { rootMargin: IDLE_ROOT_MARGIN }
  )
}

function stamp(root: ParentNode): void {
  root.querySelectorAll<HTMLElement>(CARD_SELECTOR).forEach((el) => {
    if (el.hasAttribute(PHASE_ATTR)) {
      return
    }
    el.setAttribute(PHASE_ATTR, '')
    const phase = -Math.round(Math.random() * FLOW_DURATION_MS)
    el.style.setProperty('--phi-edge-delay', `${phase}ms`)
    // 新插入的框（弹层、路由切换、表格分页）同样要纳入屏外暂停
    idleObserver?.observe(el)
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
  // 低 GPU 档：CSS 侧流光本就是静态的，观察器与相位都不必存在
  if (isLowGpu()) {
    return
  }
  idleObserver = createIdleObserver()
  stamp(document)
  observer = new MutationObserver(scheduleStamp)
  observer.observe(document.body, { childList: true, subtree: true })
}

export function stopPhiEdgePhase(): void {
  observer?.disconnect()
  observer = null
  idleObserver?.disconnect()
  idleObserver = null
  if (pendingTimer !== null) {
    window.clearTimeout(pendingTimer)
    pendingTimer = null
  }
}
