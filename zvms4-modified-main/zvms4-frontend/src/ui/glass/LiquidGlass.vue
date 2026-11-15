<script setup lang="ts">
/**
 * Liquid Glass 组件 —— 基于 `./glass.ts`（干净重写引擎，见 .liquid-glass-lab/NOTES.md）。
 *
 * 采样源由 `./backdrop.ts` 提供：优先 html2canvas 抓真实背景层，抓不到
 * （我们的 CSS 用了 color-mix，html2canvas 解析不了）就退到程序化背景，
 * 保证折射有结构可折，而不是变成一块灰板。
 *
 * 三档语义（按用户要求分级，卡片不参与）：
 * - nav：轻折射、中模糊、材质填充接近原来的 --yg2-glass-chrome-fill
 * - button：明显边缘高光 + 轻微折射，中心几乎不扭曲
 * - dialog：更强空间层次（模糊更足）+ 中等折射
 *
 * 与旧实现的差别：
 * 1) 不再走 `Container.pageSnapshot = dataUrl` 那条会 TypeError 的路径（顶栏此前一直是
 *    CSS 兜底，WebGL 分支从未生效）；
 * 2) 背景快照不再 `toDataURL()` 绕 PNG，多个实例共享同一次抓取；
 * 3) 鼠标悬停只改 spec/tint，不重抓背景；
 * 4) 滚动监听用 capture，能收到内层滚动容器（.yg2-app__content）的事件。
 */
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { LiquidGlass } from './glass'
import { getBackdrop, invalidateBackdrop } from './backdrop'

const props = withDefaults(
  defineProps<{
    radius?: number
    /** 材质填充不透明度；不给就用 preset 的默认值 */
    tint?: number
    shape?: 'rounded' | 'circle' | 'pill'
    capture?: string
    preset?: 'nav' | 'button' | 'dialog'
    refreshOnScroll?: boolean
    /** @deprecated 环境光色调由 AmbientField 决定，这里仅为兼容旧调用保留 */
    tone?: string
  }>(),
  {
    radius: 24,
    shape: 'rounded',
    capture: '.yg2-ambient',
    preset: 'nav',
    refreshOnScroll: true,
    tone: 'activities',
  },
)

interface PresetParams {
  refract: number
  edgeWidth: number
  blur: number
  spec: number
  tint: number
}

const PRESETS: Record<'nav' | 'button' | 'dialog', Record<'light' | 'dark', PresetParams>> = {
  nav: {
    light: { refract: 9, edgeWidth: 16, blur: 18, spec: 0.5, tint: 0.5 },
    dark: { refract: 10, edgeWidth: 16, blur: 20, spec: 0.55, tint: 0.62 },
  },
  button: {
    light: { refract: 16, edgeWidth: 11, blur: 12, spec: 0.75, tint: 0.06 },
    dark: { refract: 17, edgeWidth: 11, blur: 13, spec: 0.8, tint: 0.1 },
  },
  dialog: {
    light: { refract: 15, edgeWidth: 18, blur: 20, spec: 0.55, tint: 0.28 },
    dark: { refract: 16, edgeWidth: 18, blur: 22, spec: 0.6, tint: 0.34 },
  },
}

/** 材质填充色与 --yg2-glass-chrome-fill 对齐：浅色是白，深色是 #1e212a */
const FILL_LIGHT: [number, number, number] = [1, 1, 1]
const FILL_DARK: [number, number, number] = [0.118, 0.129, 0.165]

const host = ref<HTMLDivElement | null>(null)
/** 先按 CSS 兜底显示，第一次成功出图后才摘掉 —— 避免加载瞬间顶栏「透明一下」 */
const fallback = ref(true)
const captureMode = ref<'h2c' | 'procedural' | 'none'>('none')
const hovered = ref(false)

let engine: LiquidGlass | null = null
let refreshToken = 0
let scrollTimer = 0
let resizeObserver: ResizeObserver | null = null
let themeObserver: MutationObserver | null = null
let media: MediaQueryList | null = null

function isDark(): boolean {
  return document.documentElement.classList.contains('dark')
}

function params() {
  const dark = isDark()
  const tier = PRESETS[props.preset][dark ? 'dark' : 'light']
  return {
    shape: props.shape,
    radius: props.radius,
    refract: tier.refract,
    edgeWidth: tier.edgeWidth,
    blur: tier.blur,
    spec: tier.spec,
    tint: props.tint ?? tier.tint,
    fill: dark ? FILL_DARK : FILL_LIGHT,
  }
}

/** 悬停只加强边缘高光与填充，不改折射，避免边缘跳动 */
function applyOptions() {
  if (!engine) return
  const base = params()
  engine.update({
    ...base,
    spec: base.spec * (hovered.value ? 1.35 : 1),
    tint: base.tint + (hovered.value ? 0.04 : 0),
  })
}

async function refresh(force = false) {
  const instance = engine
  if (!instance) return
  const token = ++refreshToken
  try {
    const backdrop = await getBackdrop(props.capture, force)
    // 有更新的请求在跑，丢弃这一次（避免旧的快照盖掉新的）
    if (token !== refreshToken || !engine) return
    captureMode.value = backdrop.mode
    engine.setSource(backdrop.canvas, { origin: backdrop.origin, width: backdrop.width })
    applyOptions()
    engine.render()
    fallback.value = false
  } catch {
    fallback.value = true
  }
}

function mount() {
  const el = host.value
  if (!el) return
  try {
    engine = new LiquidGlass(el, params())
  } catch {
    fallback.value = true
    engine = null
    return
  }
  void refresh()
}

function onScroll() {
  if (!props.refreshOnScroll) return
  window.clearTimeout(scrollTimer)
  scrollTimer = window.setTimeout(() => {
    invalidateBackdrop(props.capture)
    void refresh(true)
  }, 240)
}

function onResize() {
  window.clearTimeout(scrollTimer)
  scrollTimer = window.setTimeout(() => {
    invalidateBackdrop(props.capture)
    void refresh(true)
  }, 240)
}

function onThemeChange() {
  applyOptions()
  invalidateBackdrop()
  void refresh(true)
}

function bindHover() {
  const target = host.value?.parentElement
  if (!target) return
  target.addEventListener('pointerenter', onEnter)
  target.addEventListener('pointerleave', onLeave)
}

function onEnter() {
  hovered.value = true
  applyOptions()
  engine?.render()
}

function onLeave() {
  hovered.value = false
  applyOptions()
  engine?.render()
}

onMounted(() => {
  mount()
  bindHover()
  // capture 才能收到内层滚动容器（.yg2-app__content）的 scroll
  document.addEventListener('scroll', onScroll, { capture: true, passive: true })
  window.addEventListener('resize', onResize)
  media = window.matchMedia('(prefers-color-scheme: dark)')
  media.addEventListener('change', onThemeChange)
  themeObserver = new MutationObserver(onThemeChange)
  themeObserver.observe(document.documentElement, {
    attributes: true,
    attributeFilter: ['class', 'data-theme'],
  })
  if (host.value) {
    resizeObserver = new ResizeObserver(() => {
      engine?.resize()
      engine?.render()
    })
    resizeObserver.observe(host.value)
  }
})

watch(
  () => [props.tint, props.shape, props.radius, props.preset],
  () => {
    applyOptions()
    engine?.render()
  },
)

onBeforeUnmount(() => {
  window.clearTimeout(scrollTimer)
  document.removeEventListener('scroll', onScroll, { capture: true })
  window.removeEventListener('resize', onResize)
  media?.removeEventListener('change', onThemeChange)
  themeObserver?.disconnect()
  resizeObserver?.disconnect()
  const target = host.value?.parentElement
  target?.removeEventListener('pointerenter', onEnter)
  target?.removeEventListener('pointerleave', onLeave)
  engine?.destroy()
  engine = null
})
</script>

<template>
  <div
    ref="host"
    class="yg2-glass"
    :class="{ 'is-fallback': fallback }"
    :data-capture="captureMode"
  >
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
