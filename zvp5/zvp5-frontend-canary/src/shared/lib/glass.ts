/**
 * Liquid Glass 基础能力。
 *
 * 参考 `liquid-glass-react`（MIT）的实现思路：用 SVG 位移滤镜对
 * backdrop-filter 之后的背景做边缘折射，再叠加高光与色散。
 * 该库是 React 组件，本项目是 Vue，因此这里只移植底层技术：
 *
 * 1. 位移图：参考实现内置一张 JPEG，这里改为运行时用 canvas 生成
 *    超椭圆（squircle）径向位移图，避免往仓库里塞 20KB 二进制；
 * 2. 色散：R/G/B 三个通道用略有差异的位移量分开渲染再 screen 叠加；
 * 3. 兼容：Safari / Firefox 对「filter 作用于 backdrop-filter 结果」
 *    支持不完整（与参考库 README 的说明一致），这类浏览器只保留
 *    毛玻璃与高光，不做折射。
 */

export type GlassMaterial = 'nav' | 'prominent' | 'frosted' | 'subtle'

interface GlassMaterialSpec {
  scale: number
  aberration: number
}

export const GLASS_MATERIALS: Record<GlassMaterial, GlassMaterialSpec> = {
  nav: { scale: 38, aberration: 1.6 },
  prominent: { scale: 56, aberration: 2.4 },
  frosted: { scale: 30, aberration: 1.2 },
  subtle: { scale: 18, aberration: 0.8 }
}

const MAP_SIZE = 256

let mapCache: string | null = null

function smoothstep(value: number): number {
  const t = Math.min(1, Math.max(0, value))
  return t * t * (3 - 2 * t)
}

/**
 * 生成径向位移图：中心为中性灰（不位移），边缘按方向推开。
 * R 通道表示 x 位移，B 通道表示 y 位移，与 `feDisplacementMap` 的
 * `xChannelSelector="R" yChannelSelector="B"` 对应。
 */
export function glassDisplacementMap(): string {
  if (mapCache !== null) return mapCache
  if (typeof document === 'undefined') return ''
  const canvas = document.createElement('canvas')
  canvas.width = MAP_SIZE
  canvas.height = MAP_SIZE
  const context = canvas.getContext('2d')
  if (!context) {
    mapCache = ''
    return mapCache
  }
  const image = context.createImageData(MAP_SIZE, MAP_SIZE)
  const inner = 0.46
  for (let y = 0; y < MAP_SIZE; y += 1) {
    for (let x = 0; x < MAP_SIZE; x += 1) {
      const nx = ((x + 0.5) / MAP_SIZE) * 2 - 1
      const ny = ((y + 0.5) / MAP_SIZE) * 2 - 1
      // 超椭圆距离，让折射带贴合圆角矩形的边而不是圆的边。
      const radius = Math.pow(
        Math.pow(Math.abs(nx), 4) + Math.pow(Math.abs(ny), 4),
        1 / 4
      )
      let dx = 0
      let dy = 0
      if (radius > inner) {
        const strength = smoothstep((radius - inner) / (1 - inner))
        dx = (nx / radius) * strength
        dy = (ny / radius) * strength
      }
      const offset = (y * MAP_SIZE + x) * 4
      image.data[offset] = Math.round(128 + dx * 127)
      image.data[offset + 1] = 128
      image.data[offset + 2] = Math.round(128 + dy * 127)
      image.data[offset + 3] = 255
    }
  }
  context.putImageData(image, 0, 0)
  mapCache = canvas.toDataURL('image/png')
  return mapCache
}

let refractionSupport: boolean | null = null

/**
 * 折射支持判断。参考库明确说明 Safari / Firefox 只能部分呈现，
 * 这里在能力不足时直接降级为「毛玻璃 + 高光」，避免出现灰边或错位。
 */
export function supportsGlassRefraction(): boolean {
  if (typeof window === 'undefined') return false
  if (refractionSupport !== null) return refractionSupport
  const ua = navigator.userAgent
  const isSafari = /Safari\//.test(ua) && !/Chrome|Chromium|Edg\//.test(ua)
  const isFirefox = /Firefox\//.test(ua)
  const hasBackdrop =
    CSS.supports('backdrop-filter', 'blur(1px)') ||
    CSS.supports('-webkit-backdrop-filter', 'blur(1px)')
  refractionSupport =
    !isSafari && !isFirefox && hasBackdrop && CSS.supports('filter', 'url(#zvp5-glass)')
  return refractionSupport
}

export function glassFilterId(material: GlassMaterial): string {
  return `zvp5-glass-${material}`
}
