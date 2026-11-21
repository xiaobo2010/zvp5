import { reactive } from 'vue'

/**
 * Liquid Glass 基础能力。
 *
 * 参考 `liquid-glass-react`（MIT）的实现思路：用 SVG 位移滤镜对
 * backdrop-filter 之后的背景做边缘折射，再叠加高光与色散。
 * 该库是 React 组件，本项目是 Vue，因此这里只移植底层技术：
 *
 * 1. 位移图：参考实现内置一张 JPEG，这里改为运行时用 canvas 生成
 *    超椭圆（squircle）位移图，避免往仓库里塞 20KB 二进制；
 * 2. 色散：R/G/B 三个通道用略有差异的位移量分开渲染再 screen 叠加；
 * 3. 兼容：Safari / Firefox 对「filter 作用于 backdrop-filter 结果」
 *    支持不完整（与参考库 README 的说明一致），这类浏览器只保留
 *    毛玻璃与高光，不做折射。
 */

export type GlassMaterial = 'nav' | 'prominent' | 'frosted' | 'subtle'

/**
 * 折射形态：
 * - polar：宽折射带，像一整块凸透镜（导航、Dock）
 * - standard：只在边缘一圈折射，中间保持通透（工具栏、卡片）
 * - prominent：比 polar 更厚的镜片（重点操作、浮层）
 * - shader：需要 WebGL 逐像素着色，SVG 滤镜无法真实复现，
 *   这里按参考库的做法降级为 polar，只抬高饱和与色散，
 *   不伪造一个不存在的 shader 参数。
 */
export type GlassMode = 'standard' | 'polar' | 'prominent' | 'shader'

export type GlassTheme = 'light' | 'dark' | 'auto'

export type GlassMapKind = 'polar' | 'standard'

interface GlassMaterialSpec {
  scale: number
  aberration: number
  blur: number
  saturation: number
  contrast: number
}

export const GLASS_MATERIALS: Record<GlassMaterial, GlassMaterialSpec> = {
  nav: { scale: 38, aberration: 1.6, blur: 20, saturation: 175, contrast: 108 },
  prominent: { scale: 56, aberration: 2.4, blur: 18, saturation: 185, contrast: 110 },
  frosted: { scale: 30, aberration: 1.2, blur: 26, saturation: 160, contrast: 106 },
  subtle: { scale: 18, aberration: 0.8, blur: 14, saturation: 150, contrast: 104 }
}

/** 每种形态对基础材质的放大倍率；shader 走 polar 的降级路径。 */
const MODE_GAIN: Record<GlassMode, { scale: number; aberration: number }> = {
  standard: { scale: 0.82, aberration: 1 },
  polar: { scale: 1, aberration: 1 },
  prominent: { scale: 1.3, aberration: 1.3 },
  shader: { scale: 1.08, aberration: 1.35 }
}

export interface GlassSpec {
  kind: GlassMapKind
  scale: number
  aberration: number
  blur: number
  saturation: number
  contrast: number
}

const MAP_SIZE = 256

const mapCache: Record<GlassMapKind, string | null> = { polar: null, standard: null }

/** 折射带的内边界：越小折射区越宽（透镜感越强）。 */
const MAP_INNER: Record<GlassMapKind, number> = { polar: 0.46, standard: 0.74 }

function smoothstep(value: number): number {
  const t = Math.min(1, Math.max(0, value))
  return t * t * (3 - 2 * t)
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value))
}

/**
 * 生成径向位移图：中心为中性灰（不位移），边缘按方向推开。
 * R 通道表示 x 位移，B 通道表示 y 位移，与 `feDisplacementMap` 的
 * `xChannelSelector="R" yChannelSelector="B"` 对应。
 */
export function glassDisplacementMap(kind: GlassMapKind = 'polar'): string {
  const cached = mapCache[kind]
  if (cached !== null) return cached
  if (typeof document === 'undefined') return ''
  const canvas = document.createElement('canvas')
  canvas.width = MAP_SIZE
  canvas.height = MAP_SIZE
  const context = canvas.getContext('2d')
  if (!context) {
    mapCache[kind] = ''
    return mapCache[kind]
  }
  const image = context.createImageData(MAP_SIZE, MAP_SIZE)
  const inner = MAP_INNER[kind]
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
  mapCache[kind] = canvas.toDataURL('image/png')
  return mapCache[kind]
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

/** 把组件 props 解析成一份具体的玻璃参数。 */
export function resolveGlassSpec(options: {
  material: GlassMaterial
  mode: GlassMode
  displacementScale?: number
  aberrationIntensity?: number
  blurAmount?: number
  saturation?: number
}): GlassSpec {
  const base = GLASS_MATERIALS[options.material]
  const gain = MODE_GAIN[options.mode]
  return {
    kind: options.mode === 'standard' ? 'standard' : 'polar',
    scale: clamp(options.displacementScale ?? base.scale * gain.scale, 0, 90),
    aberration: clamp(options.aberrationIntensity ?? base.aberration * gain.aberration, 0, 6),
    blur: clamp(options.blurAmount ?? base.blur, 0, 64),
    saturation: clamp(options.saturation ?? base.saturation, 100, 240),
    contrast: base.contrast
  }
}

export interface GlassFilterSpec {
  kind: GlassMapKind
  scale: number
  aberration: number
}

/**
 * 滤镜注册表：全站共用一份 <defs>，相同参数的组件复用同一个滤镜，
 * 避免每个玻璃实例各建一套 SVG 节点。
 */
const filterRegistry = reactive(new Map<string, GlassFilterSpec>())

function keyPart(value: number): string {
  return String(value).replace('.', '_')
}

export function registerGlassFilter(spec: GlassFilterSpec): string {
  // 量化到 0.5px / 0.1 的精度，让相近的实例命中同一个滤镜。
  const scale = Math.round(spec.scale * 2) / 2
  const aberration = Math.round(spec.aberration * 10) / 10
  const id = `zvp5-glass-${spec.kind}-${keyPart(scale)}-${keyPart(aberration)}`
  if (!filterRegistry.has(id)) {
    filterRegistry.set(id, { kind: spec.kind, scale, aberration })
  }
  return id
}

export function glassFilterEntries(): [string, GlassFilterSpec][] {
  return [...filterRegistry.entries()]
}
