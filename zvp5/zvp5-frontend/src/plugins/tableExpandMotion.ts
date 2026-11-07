/**
 * el-table「展开行」进出场动画增强。
 *
 * Element Plus 的展开行是瞬时插入 / 移除的，观感很硬。这里统一做两件事：
 * - 展开：监听文档里新增的 `td.el-table__expanded-cell`，在首次绘制前用 WAAPI
 *   从 0 高度弹性展开；内容异步渲染（骨架屏 → 正文）时也能正确接到；
 * - 收起：捕获期拦住展开图标的点击，先播完收拢动画，再触发原生收起。
 *
 * 树形表格的展开图标不产生 `el-table__expanded-cell`，因此不受影响。
 */
const OPEN_DURATION = 340
const OPEN_EASING = 'cubic-bezier(0.22, 1, 0.36, 1)'
const CLOSE_DURATION = 240
const CLOSE_EASING = 'cubic-bezier(0.4, 0, 0.2, 1)'

/** 程序化触发的收起要跳过本层拦截，避免递归 */
let suppress = false

function prefersReducedMotion(): boolean {
  return window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false
}

/** 展开内容行的单元格（td）：新增节点可能整行 <tr> 一起插入 */
function expandedCellOf(node: Node): HTMLElement | null {
  if (!(node instanceof HTMLElement)) {
    return null
  }
  if (node.classList.contains('el-table__expanded-cell')) {
    return node
  }
  return node.querySelector<HTMLElement>('td.el-table__expanded-cell')
}

/** 展开内容的根节点（各表格 slot 里的组件根元素） */
function contentOf(cell: HTMLElement): HTMLElement | null {
  return (cell.firstElementChild as HTMLElement | null) ?? null
}

function animateOpen(content: HTMLElement) {
  // 在渲染帧内启动，避免在 MutationObserver 微任务里创建的动画停在起始帧
  requestAnimationFrame(() => {
    const height = content.getBoundingClientRect().height
    if (height <= 0) {
      return
    }
    content.animate(
      [
        { height: '0px', opacity: 0, transform: 'translateY(-6px)', overflow: 'hidden' },
        { height: `${height}px`, opacity: 1, transform: 'translateY(0)', overflow: 'hidden' }
      ],
      { duration: OPEN_DURATION, easing: OPEN_EASING }
    )
  })
}

function animateClose(content: HTMLElement, done: () => void) {
  const height = content.getBoundingClientRect().height
  if (height <= 0) {
    done()
    return
  }
  const animation = content.animate(
    [
      { height: `${height}px`, opacity: 1, transform: 'translateY(0)', overflow: 'hidden' },
      { height: '0px', opacity: 0, transform: 'translateY(-4px)', overflow: 'hidden' }
    ],
    { duration: CLOSE_DURATION, easing: CLOSE_EASING }
  )
  animation.onfinish = done
  animation.oncancel = done
}

/**
 * 内容可能是异步渲染的（骨架屏带 throttle 时会延迟出现），
 * 出现第一个有高度元素子节点的瞬间再播展开动画。
 */
function playOpenWhenReady(cell: HTMLElement) {
  let observer: MutationObserver | undefined
  let frames = 0

  const stop = () => {
    observer?.disconnect()
    observer = undefined
  }

  const tryPlay = (): boolean => {
    const content = contentOf(cell)
    if (!content || content.getBoundingClientRect().height <= 0) {
      return false
    }
    stop()
    animateOpen(content)
    return true
  }

  if (tryPlay()) {
    return
  }

  observer = new MutationObserver(() => {
    tryPlay()
  })
  observer.observe(cell, { childList: true, subtree: true })

  // 兜底：内容高度在后续帧里长出来（无 childList 变化的情况）
  const tick = () => {
    if (observer === undefined) {
      return
    }
    if (tryPlay()) {
      return
    }
    if (++frames < 120) {
      requestAnimationFrame(tick)
    } else {
      stop()
    }
  }
  requestAnimationFrame(tick)
}

/** 展开图标所在行的下一行就是展开内容行 */
function findDetailRow(icon: Element): HTMLElement | null {
  const row = icon.closest('tr')
  const next = row?.nextElementSibling as HTMLElement | null
  if (!next?.querySelector('td.el-table__expanded-cell')) {
    return null
  }
  return next
}

export function installTableExpandMotion() {
  if (prefersReducedMotion()) {
    return
  }

  // 展开：捕获新增的展开单元格（可能是 td，也可能随 <tr> 整行插入）
  new MutationObserver((records) => {
    for (const record of records) {
      record.addedNodes.forEach((node) => {
        const cell = expandedCellOf(node)
        if (cell) {
          playOpenWhenReady(cell)
        }
      })
    }
  }).observe(document.body, { childList: true, subtree: true })

  // 收起：先播动画再放行原生点击
  document.addEventListener(
    'click',
    (event) => {
      if (suppress) {
        return
      }
      const icon = (event.target as Element | null)?.closest?.('.el-table__expand-icon')
      if (!icon || icon.getAttribute('aria-expanded') !== 'true') {
        return
      }
      const row = findDetailRow(icon)
      const cell = row?.querySelector('td.el-table__expanded-cell') as HTMLElement | null
      if (!cell) {
        return
      }
      event.preventDefault()
      event.stopPropagation()
      const content = contentOf(cell)
      const done = () => {
        suppress = true
        icon.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }))
        window.setTimeout(() => {
          suppress = false
        }, 0)
      }
      if (content) {
        animateClose(content, done)
      } else {
        done()
      }
    },
    true
  )
}
