/**
 * 工作台「本地预览」点选桥接（仅开发环境 + 被 iframe 嵌入时生效）
 *
 * 工作台页面与应用页面跨源，父窗口读不到这里的 DOM，所以由应用侧负责
 * hover 高亮、点击选中、生成选择器与 Vue 组件文件，再通过 postMessage
 * 把结果回传给工作台的预览面板。生产构建不会启用，也不会进入交互流程。
 */

const SOURCE = 'dsh-live-preview'
const HIGHLIGHT_ID = '__dsh_preview_highlight__'
const STYLE_ID = '__dsh_preview_style__'
const PICKING_CLASS = '__dsh-preview-picking'

interface VueComponentLike {
  type?: { __file?: unknown; name?: unknown }
  parent?: VueComponentLike | null
}

interface ElementWithVue {
  __vueParentComponent?: VueComponentLike | null
}

type PickedElement = {
  selector: string
  tag: string
  id: string
  classes: string[]
  text: string
  route: string
  componentFile: string
  componentName: string
  attrs: Record<string, string>
  rect: { x: number; y: number; width: number; height: number }
  pickedAt: number
}

let active = false
let currentTarget: Element | null = null
let highlight: HTMLDivElement | null = null

export function installPreviewPicker(): void {
  if (!import.meta.env.DEV) return
  if (typeof window === 'undefined') return
  if (window.parent === window) return

  window.addEventListener('message', (event) => {
    if (event.source !== window.parent) return
    const data = event.data as { source?: unknown; type?: unknown } | null
    if (!data || data.source !== SOURCE) return
    if (data.type === 'picker:start') {
      startPicking()
    } else if (data.type === 'picker:stop') {
      stopPicking()
    }
  })

  announce()
}

function announce(): void {
  try {
    window.parent.postMessage({ source: SOURCE, type: 'ready' }, '*')
  } catch {
    /* ignore */
  }
}

function startPicking(): void {
  if (active) return
  active = true
  currentTarget = null
  ensureStyle()
  document.documentElement.classList.add(PICKING_CLASS)
  ensureHighlight()
  window.addEventListener('mousemove', onPointerMove, true)
  window.addEventListener('scroll', onViewportChange, true)
  window.addEventListener('resize', onViewportChange, true)
  window.addEventListener('click', onClick, true)
  window.addEventListener('keydown', onKeyDown, true)
  document.documentElement.style.cursor = 'crosshair'
}

function stopPicking(): void {
  if (!active) return
  active = false
  currentTarget = null
  window.removeEventListener('mousemove', onPointerMove, true)
  window.removeEventListener('scroll', onViewportChange, true)
  window.removeEventListener('resize', onViewportChange, true)
  window.removeEventListener('click', onClick, true)
  window.removeEventListener('keydown', onKeyDown, true)
  document.documentElement.classList.remove(PICKING_CLASS)
  document.documentElement.style.cursor = ''
  if (highlight) {
    highlight.style.display = 'none'
  }
}

function onKeyDown(event: KeyboardEvent): void {
  if (event.key !== 'Escape') return
  event.preventDefault()
  event.stopPropagation()
  stopPicking()
  try {
    window.parent.postMessage({ source: SOURCE, type: 'cancelled' }, '*')
  } catch {
    /* ignore */
  }
}

function onPointerMove(event: MouseEvent): void {
  const target = resolveTarget(event)
  if (!target || target === currentTarget) return
  currentTarget = target
  paintHighlight(target)
}

function onViewportChange(): void {
  if (currentTarget) paintHighlight(currentTarget)
}

function onClick(event: MouseEvent): void {
  if (!active) return
  const target = resolveTarget(event)
  if (!target) return
  event.preventDefault()
  event.stopPropagation()
  event.stopImmediatePropagation()
  const payload = buildPayload(target)
  stopPicking()
  try {
    window.parent.postMessage({ source: SOURCE, type: 'picked', payload }, '*')
  } catch {
    /* ignore */
  }
}

function resolveTarget(event: MouseEvent): Element | null {
  const fromPoint = document.elementFromPoint(event.clientX, event.clientY)
  const raw = fromPoint ?? (event.target instanceof Element ? event.target : null)
  if (!raw) return null
  const host = raw.closest(`#${HIGHLIGHT_ID}`)
  if (host) return null
  return raw
}

function ensureHighlight(): HTMLDivElement {
  if (highlight && highlight.isConnected) return highlight
  const node = document.createElement('div')
  node.id = HIGHLIGHT_ID
  const style = node.style
  style.position = 'fixed'
  style.pointerEvents = 'none'
  style.zIndex = '2147483647'
  style.border = '2px solid #0EA5E9'
  style.borderRadius = '4px'
  style.background = 'rgba(14, 165, 233, 0.12)'
  style.boxShadow = '0 0 0 1px rgba(14, 165, 233, 0.35)'
  style.display = 'none'
  style.transition = 'top 40ms linear, left 40ms linear, width 40ms linear, height 40ms linear'
  ;(document.body ?? document.documentElement).appendChild(node)
  highlight = node
  return node
}

function paintHighlight(target: Element): void {
  const node = ensureHighlight()
  const rect = target.getBoundingClientRect()
  node.style.display = 'block'
  node.style.top = `${Math.round(rect.top)}px`
  node.style.left = `${Math.round(rect.left)}px`
  node.style.width = `${Math.round(rect.width)}px`
  node.style.height = `${Math.round(rect.height)}px`
}

function ensureStyle(): void {
  if (document.getElementById(STYLE_ID)) return
  const style = document.createElement('style')
  style.id = STYLE_ID
  style.textContent = `.${PICKING_CLASS}, .${PICKING_CLASS} * { cursor: crosshair !important; }`
  document.head.appendChild(style)
}

function buildPayload(element: Element): PickedElement {
  const rect = element.getBoundingClientRect()
  const component = findVueComponent(element)
  return {
    selector: cssSelector(element),
    tag: element.tagName.toLowerCase(),
    id: element.id || '',
    classes: Array.from(element.classList).slice(0, 8),
    text: normalizeText(element.textContent || '').slice(0, 120),
    route: `${window.location.pathname}${window.location.search}${window.location.hash}`,
    componentFile: component.file,
    componentName: component.name,
    attrs: collectAttributes(element),
    rect: {
      x: Math.round(rect.left),
      y: Math.round(rect.top),
      width: Math.round(rect.width),
      height: Math.round(rect.height),
    },
    pickedAt: Date.now(),
  }
}

function normalizeText(value: string): string {
  return value.replace(/\s+/g, ' ').trim()
}

function cssSelector(element: Element): string {
  const segments: string[] = []
  let node: Element | null = element
  while (node && node.nodeType === Node.ELEMENT_NODE && node !== document.body && node !== document.documentElement) {
    segments.unshift(describeSegment(node))
    node = node.parentElement
  }
  return segments.join(' > ')
}

function describeSegment(element: Element): string {
  const tag = element.tagName.toLowerCase()
  if (element.id) return `${tag}#${cssSafe(element.id)}`
  const classes = Array.from(element.classList)
    .filter((name) => !name.startsWith('__dsh') && !name.startsWith('dlp-'))
    .slice(0, 3)
  let segment = tag + classes.map((name) => `.${cssSafe(name)}`).join('')
  const parent = element.parentElement
  if (parent) {
    const sameTag = Array.from(parent.children).filter((child) => child.tagName === element.tagName)
    if (sameTag.length > 1) segment += `:nth-of-type(${sameTag.indexOf(element) + 1})`
  }
  return segment
}

function cssSafe(value: string): string {
  return typeof CSS !== 'undefined' && typeof CSS.escape === 'function' ? CSS.escape(value) : value.replace(/[^\w-]/g, '\\$&')
}

function findVueComponent(element: Element): { file: string; name: string } {
  let instance = (element as unknown as ElementWithVue).__vueParentComponent ?? null
  while (instance) {
    const type = instance.type
    const file = typeof type?.__file === 'string' ? type.__file : ''
    const name = typeof type?.name === 'string' ? type.name : ''
    if (file) return { file, name }
    instance = instance.parent ?? null
  }
  return { file: '', name: '' }
}

const ATTR_KEYS = ['type', 'name', 'placeholder', 'href', 'role', 'aria-label', 'title', 'data-testid', 'for', 'value']

function collectAttributes(element: Element): Record<string, string> {
  const attrs: Record<string, string> = {}
  for (const key of ATTR_KEYS) {
    const value = element.getAttribute(key)
    if (value) attrs[key] = value.slice(0, 120)
  }
  return attrs
}
