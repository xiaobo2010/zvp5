<script setup lang="ts">
/**
 * Liquid Glass 包装层。
 *
 * 库的着色器按「整个视口的页面快照」采样，所以这里给它一个可控的采样源：
 * 1) 优先用 html2canvas 抓真实背景层（结构最真，能看到背景里的光斑/内容被折射）；
 * 2) 抓取失败（我们的 CSS 用了 color-mix，html2canvas 解析不了）时，
 *    退到程序化背景：按当前主题把底色 + 三团环境光 + 细颗粒噪点画进 2D canvas，
 *    保证折射有结构可折，而不是变成一块灰板。
 *
 * 主题切换 / 尺寸变化 / 页面滚动都会重抓并重建 WebGL 纹理（库本身只抓一次）。
 */
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Container } from '@/vendor/liquid-glass/container.js'

const props = withDefaults(
  defineProps<{
    radius?: number
    tint?: number
    shape?: 'rounded' | 'circle' | 'pill'
    capture?: string
    preset?: 'nav' | 'button' | 'dialog'
    refreshOnScroll?: boolean
    tone?: string
  }>(),
  {
    radius: 24,
    tint: 0.06,
    shape: 'rounded',
    capture: '.yg2-ambient',
    preset: 'nav',
    refreshOnScroll: true,
    tone: 'activities',
  },
)

const host = ref<HTMLDivElement | null>(null)
const fallback = ref(false)
const hasWebGL = ref(false)
let instance: any = null
let scrollTimer = 0
let resizeObserver: ResizeObserver | null = null
let media: MediaQueryList | null = null

/**
 * 三档参数（按用户要求分级，卡片不参与）：
 * - nav：轻折射、中模糊，只给一层薄边缘光
 * - button：明显边缘高光 + 轻微折射，中心几乎不扭曲
 * - dialog：更强空间层次（模糊/内压更足）+ 中等折射
 */
const PRESETS = {
  nav: {
    light: { edge: 0.012, rim: 0.05, base: 0.0, edgeD: 0.2, rimD: 0.9, baseD: 0.12, corner: 0.018, ripple: 0.0, blur: 7 },
    dark: { edge: 0.014, rim: 0.06, base: 0.0, edgeD: 0.19, rimD: 1.0, baseD: 0.12, corner: 0.02, ripple: 0.0, blur: 8 },
  },
  button: {
    light: { edge: 0.02, rim: 0.11, base: 0.0, edgeD: 0.14, rimD: 0.7, baseD: 0.1, corner: 0.03, ripple: 0.0, blur: 5 },
    dark: { edge: 0.024, rim: 0.13, base: 0.0, edgeD: 0.13, rimD: 0.75, baseD: 0.1, corner: 0.034, ripple: 0.0, blur: 6 },
  },
  dialog: {
    light: { edge: 0.02, rim: 0.07, base: 0.006, edgeD: 0.16, rimD: 0.85, baseD: 0.15, corner: 0.026, ripple: 0.0, blur: 10 },
    dark: { edge: 0.024, rim: 0.085, base: 0.008, edgeD: 0.15, rimD: 0.9, baseD: 0.16, corner: 0.03, ripple: 0.0, blur: 12 },
  },
} as const

function params(dark: boolean) {
  const tier = PRESETS[props.preset][dark ? 'dark' : 'light']
  return {
    edgeIntensity: tier.edge,
    rimIntensity: tier.rim,
    baseIntensity: tier.base,
    edgeDistance: tier.edgeD,
    rimDistance: tier.rimD,
    baseDistance: tier.baseD,
    cornerBoost: tier.corner,
    rippleEffect: tier.ripple,
    blurRadius: tier.blur,
    tintOpacity: props.tint,
  }
}

function isDark() {
  return document.documentElement.classList.contains('dark')
}

/** 程序化背景：与外壳同一套环境光，但补上细颗粒，让折射有东西可折 */
function proceduralBackdrop(): string {
  const w = window.innerWidth
  const h = window.innerHeight
  const c = document.createElement('canvas')
  c.width = w
  c.height = h
  const ctx = c.getContext('2d')!
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
  const d = grain.data
  const amount = dark ? 10 : 7
  for (let i = 0; i < d.length; i += 4) {
    const n = (Math.random() - 0.5) * amount
    d[i] = 128 + n
    d[i + 1] = 128 + n
    d[i + 2] = 128 + n
    d[i + 3] = 16
  }
  const grainCanvas = document.createElement('canvas')
  grainCanvas.width = w
  grainCanvas.height = h
  grainCanvas.getContext('2d')!.putImageData(grain, 0, 0)
  ctx.globalAlpha = 0.5
  ctx.drawImage(grainCanvas, 0, 0)
  ctx.globalAlpha = 1

  return c.toDataURL('image/png')
}

const captureMode = ref<'h2c' | 'procedural' | 'none'>('none')
const hovered = ref(false)

/** 悬停只加强边缘高光与环境光，不改 edge/base，避免折射剧烈跳动 */
function applyHover(on: boolean) {
  const base = params(isDark())
  ;(window as any).glassControls = {
    ...(window as any).glassControls,
    ...base,
    rimIntensity: base.rimIntensity * (on ? 1.35 : 1),
    tintOpacity: base.tintOpacity + (on ? 0.03 : 0),
  }
  instance?.render?.()
}

async function buildBackdrop() {
  const target = props.capture ? document.querySelector(props.capture) : null
  if (target) {
    try {
      const canvas = await (await import('html2canvas')).default(target as HTMLElement, {
        scale: 1,
        useCORS: true,
        allowTaint: true,
        backgroundColor: null,
        ignoreElements: (node: Element) =>
          node.classList?.contains('glass-container') ||
          node.classList?.contains('glass-button') ||
          node.classList?.contains('yg2-glass-layer'),
      })
      captureMode.value = 'h2c'
      return canvas.toDataURL('image/png')
    } catch {
      /* 落到程序化背景 */
    }
  }
  captureMode.value = 'procedural'
  return proceduralBackdrop()
}

async function refresh() {
  if (!instance) return
  try {
    const dataUrl = await buildBackdrop()
    ;(Container as any).pageSnapshot = dataUrl
    instance.initWebGL?.()
  } catch {
    fallback.value = true
  }
}

function applyParams() {
  ;(window as any).glassControls = { ...(window as any).glassControls, ...params(isDark()) }
}

function mount() {
  if (!host.value) return
  applyParams()
  try {
    instance = new Container({ borderRadius: props.radius, type: props.shape, tintOpacity: props.tint })
  } catch {
    fallback.value = true
    return
  }

  const canvas = instance.canvas as HTMLCanvasElement | null
  hasWebGL.value = !!canvas && !!instance.gl_refs?.gl
  if (!hasWebGL.value) {
    fallback.value = true
    instance = null
    return
  }

  // 库自建 wrapper 会带 padding/flex，这里只留 canvas，塞进宿主
  canvas!.className = 'yg2-glass-layer'
  canvas!.style.cssText =
    'position:absolute;inset:0;width:100%;height:100%;z-index:0;pointer-events:none;border-radius:inherit;box-shadow:none'
  instance.element = host.value
  host.value.appendChild(canvas!)
  instance.updateSizeFromDOM?.()
  void refresh()
}

function scheduleRefresh() {
  window.clearTimeout(scrollTimer)
  scrollTimer = window.setTimeout(() => void refresh(), 220)
}

function bindHover() {
  const target = host.value?.parentElement
  if (!target) return
  target.addEventListener('pointerenter', () => { hovered.value = true; applyHover(true) })
  target.addEventListener('pointerleave', () => { hovered.value = false; applyHover(false) })
}

onMounted(() => {
  mount()
  bindHover()
  if (props.refreshOnScroll) window.addEventListener('scroll', scheduleRefresh, { passive: true })
  window.addEventListener('resize', scheduleRefresh)
  media = window.matchMedia('(prefers-color-scheme: dark)')
  media.addEventListener('change', onThemeChange)
  new MutationObserver(onThemeChange).observe(document.documentElement, {
    attributes: true,
    attributeFilter: ['class', 'data-theme'],
  })
  if (host.value) {
    resizeObserver = new ResizeObserver(() => {
      instance?.updateSizeFromDOM?.()
      scheduleRefresh()
    })
    resizeObserver.observe(host.value)
  }
})

function onThemeChange() {
  if (!instance) return
  applyParams()
  scheduleRefresh()
}

watch(() => props.tint, () => {
  applyParams()
  void refresh()
})

onBeforeUnmount(() => {
  window.clearTimeout(scrollTimer)
  window.removeEventListener('scroll', scheduleRefresh)
  window.removeEventListener('resize', scheduleRefresh)
  media?.removeEventListener('change', onThemeChange)
  resizeObserver?.disconnect()
  try {
    const index = (Container as any).instances?.indexOf(instance)
    if (index >= 0) (Container as any).instances.splice(index, 1)
    instance?.gl_refs?.gl?.getExtension('WEBGL_lose_context')?.loseContext()
  } catch {
    /* ignore */
  }
  instance = null
})
</script>

<template>
  <div ref="host" class="yg2-glass" :class="{ 'is-fallback': fallback }" :data-capture="captureMode">
    <slot />
  </div>
</template>

<style scoped>
.yg2-glass {
  position: absolute;
  inset: 0;
  border-radius: inherit;
  overflow: hidden;
  pointer-events: none;
  z-index: 0;
}
</style>
