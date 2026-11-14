/**
 * 三类义工的语义色（《深浅色自适应配色规范》13.2）。
 *
 * 颜色以 CSS 令牌为唯一来源，图表这类需要具体色值的场景在运行时读取，
 * 主题切换后重新获取即可跟着变，不在组件里写死 hex。
 */

export type CategoryKey = 'on-campus' | 'off-campus' | 'social-practice'

const TOKEN: Record<CategoryKey, string> = {
  'on-campus': '--yg2-cat-school',
  'off-campus': '--yg2-cat-community',
  'social-practice': '--yg2-cat-time'
}

const FALLBACK: Record<CategoryKey, string> = {
  'on-campus': '#b94f65',
  'off-campus': '#34865c',
  'social-practice': '#416fa9'
}

function parseHex(value: string): [number, number, number] | null {
  const hex = value.trim()
  if (!/^#([0-9a-f]{3}|[0-9a-f]{6})$/i.test(hex)) {
    return null
  }
  const body = hex.length === 4 ? hex.slice(1).replace(/./g, (c) => c + c) : hex.slice(1)
  return [
    parseInt(body.slice(0, 2), 16),
    parseInt(body.slice(2, 4), 16),
    parseInt(body.slice(4, 6), 16)
  ]
}

function toHex(rgb: [number, number, number]): string {
  return `#${rgb.map((n) => Math.max(0, Math.min(255, Math.round(n))).toString(16).padStart(2, '0')).join('')}`
}

function mix(color: string, target: [number, number, number], ratio: number): string {
  const rgb = parseHex(color)
  if (!rgb) {
    return color
  }
  return toHex([
    rgb[0] + (target[0] - rgb[0]) * ratio,
    rgb[1] + (target[1] - rgb[1]) * ratio,
    rgb[2] + (target[2] - rgb[2]) * ratio
  ])
}

/** 读取当前主题下的类别色（深浅色由 CSS 令牌决定） */
export function categoryColor(mode: CategoryKey): string {
  if (typeof window === 'undefined') {
    return FALLBACK[mode]
  }
  const value = getComputedStyle(document.documentElement).getPropertyValue(TOKEN[mode]).trim()
  return value || FALLBACK[mode]
}

/**
 * 由类别色生成同色系梯度（分组堆积图的不同年级）。
 * 浅色：从「浅一档」渐变到「深一档」，保证白底上每档都能区分；
 * 深色：从「深一档」渐变到「浅一档」，避免亮色糊成一片。
 */
export function categoryRamp(mode: CategoryKey, steps = 6): string[] {
  const base = categoryColor(mode)
  const dark = document.documentElement.dataset.theme === 'dark'
  return Array.from({ length: steps }, (_, i) => {
    const t = steps === 1 ? 0 : i / (steps - 1)
    // t: 0 → 最浅/最暗的一端，1 → 另一端
    if (dark) {
      return t < 0.5
        ? mix(base, [0, 0, 0], 0.45 * (1 - t * 2))
        : mix(base, [255, 255, 255], 0.35 * ((t - 0.5) * 2))
    }
    return t < 0.5
      ? mix(base, [255, 255, 255], 0.62 * (1 - t * 2))
      : mix(base, [0, 0, 0], 0.3 * ((t - 0.5) * 2))
  })
}

/** 悬停态：比基色更沉一点 */
export function categoryHover(mode: CategoryKey): string {
  return mix(categoryColor(mode), [0, 0, 0], 0.14)
}
