/**
 * 玻璃的「背后画面」来源。
 *
 * 上游库里玻璃采样的是 html2canvas 给整页拍的快照，而快照是静态的 ——
 * 这里把这件事拆成独立模块，并修掉上游的两个浪费：
 * 1) 不再 `toDataURL()` 绕一圈 PNG：html2canvas 直接给 canvas，直接当纹理上传；
 * 2) 多个玻璃实例共享同一次抓取（顶栏 + 返回按钮以前各抓一遍整页）。
 *
 * 抓取失败时退到程序化背景：按主题画底色 + 三团环境光 + 细颗粒，
 * 保证折射有结构可折，而不是变成一块灰板。
 */

export type BackdropMode = 'h2c' | 'procedural'

export interface Backdrop {
  canvas: HTMLCanvasElement
  /** 纹理左上角在页面坐标里的位置 */
  origin: { x: number; y: number }
  /** 纹理覆盖的 CSS 宽高 */
  width: number
  height: number
  mode: BackdropMode
  at: number
}

interface Entry {
  promise: Promise<Backdrop>
  backdrop: Backdrop | null
}

const cache = new Map<string, Entry>()

function isDark(): boolean {
  return document.documentElement.classList.contains('dark')
}

function drawProcedural(): Backdrop {
  const w = Math.max(1, Math.round(window.innerWidth))
  const h = Math.max(1, Math.round(window.innerHeight))
  const canvas = document.createElement('canvas')
  canvas.width = w
  canvas.height = h
  const ctx = canvas.getContext('2d')
  if (!ctx) throw new Error('backdrop: 无法创建 2D context')
  const dark = isDark()

  const base = ctx.createLinearGradient(0, 0, w, h)
  if (dark) {
    base.addColorStop(0, '#15171c')
    base.addColorStop(0.5, '#1b1d22')
    base.addColorStop(1, '#101216')
  } else {
    base.addColorStop(0, '#eef3fb')
    base.addColorStop(0.52, '#f7f4ee')
    base.addColorStop(1, '#eef1f7')
  }
  ctx.fillStyle = base
  ctx.fillRect(0, 0, w, h)

  const blobs: Array<[number, number, number, string]> = dark
    ? [
        [0.18 * w, 0.24 * h, 0.42 * Math.max(w, h), 'rgba(38, 74, 122, .55)'],
        [0.82 * w, 0.3 * h, 0.4 * Math.max(w, h), 'rgba(96, 66, 34, .45)'],
        [0.55 * w, 0.9 * h, 0.46 * Math.max(w, h), 'rgba(58, 52, 96, .5)'],
      ]
    : [
        [0.16 * w, 0.2 * h, 0.44 * Math.max(w, h), 'rgba(120, 170, 245, .5)'],
        [0.84 * w, 0.28 * h, 0.42 * Math.max(w, h), 'rgba(255, 190, 130, .42)'],
        [0.58 * w, 0.9 * h, 0.48 * Math.max(w, h), 'rgba(170, 140, 240, .3)'],
      ]
  for (const [x, y, r, color] of blobs) {
    const g = ctx.createRadialGradient(x, y, 0, x, y, r)
    g.addColorStop(0, color)
    g.addColorStop(1, 'rgba(0,0,0,0)')
    ctx.fillStyle = g
    ctx.fillRect(0, 0, w, h)
  }

  // 细颗粒：给折射一点高频结构，纯渐变折出来会像塑料
  const grain = ctx.createImageData(w, h)
  const data = grain.data
  const amount = dark ? 10 : 7
  for (let i = 0; i < data.length; i += 4) {
    const n = (Math.random() - 0.5) * amount
    data[i] = 128 + n
    data[i + 1] = 128 + n
    data[i + 2] = 128 + n
    data[i + 3] = 16
  }
  const grainCanvas = document.createElement('canvas')
  grainCanvas.width = w
  grainCanvas.height = h
  grainCanvas.getContext('2d')?.putImageData(grain, 0, 0)
  ctx.globalAlpha = 0.5
  ctx.drawImage(grainCanvas, 0, 0)
  ctx.globalAlpha = 1

  return {
    canvas,
    origin: { x: 0, y: 0 },
    width: w,
    height: h,
    mode: 'procedural',
    at: performance.now(),
  }
}

async function capture(selector: string): Promise<Backdrop> {
  const target = selector ? document.querySelector<HTMLElement>(selector) : null
  if (target) {
    try {
      const { default: html2canvas } = await import('html2canvas')
      const rect = target.getBoundingClientRect()
      const canvas = await html2canvas(target, {
        scale: 1,
        useCORS: true,
        allowTaint: true,
        backgroundColor: null,
        logging: false,
        // 玻璃自己的 canvas 不能再被拍进快照
        ignoreElements: (node: Element) =>
          node instanceof HTMLCanvasElement && node.classList.contains('lg-canvas'),
      })
      return {
        canvas,
        origin: { x: rect.left + window.scrollX, y: rect.top + window.scrollY },
        width: Math.max(1, Math.round(rect.width)),
        height: Math.max(1, Math.round(rect.height)),
        mode: 'h2c',
        at: performance.now(),
      }
    } catch {
      /* 落回程序化背景（常见原因：CSS 里用了 color-mix / oklch，html2canvas 解析不了） */
    }
  }
  return drawProcedural()
}

/**
 * 取一份背景；同一 selector 并发调用共享同一次抓取。
 * @param force 忽略已有结果，强制重抓
 */
export function getBackdrop(selector: string, force = false): Promise<Backdrop> {
  const key = selector || 'procedural'
  const hit = cache.get(key)
  if (hit && !force) return hit.promise

  const promise = capture(selector).then((backdrop) => {
    cache.set(key, { promise, backdrop })
    return backdrop
  })
  cache.set(key, { promise, backdrop: null })
  return promise
}

/** 已缓存的结果（同步取，用于尺寸变化后立即重绘而不用等抓取） */
export function peekBackdrop(selector: string): Backdrop | null {
  return cache.get(selector || 'procedural')?.backdrop ?? null
}

/** 下一次抓取重新来（滚动、主题切换、窗口尺寸变化时调用） */
export function invalidateBackdrop(selector?: string): void {
  if (selector === undefined) cache.clear()
  else cache.delete(selector || 'procedural')
}
