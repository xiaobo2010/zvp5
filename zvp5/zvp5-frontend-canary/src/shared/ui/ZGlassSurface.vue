<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import {
  registerGlassFilter,
  resolveGlassSpec,
  supportsGlassRefraction,
  type GlassMaterial,
  type GlassMode,
  type GlassTheme
} from '@/shared/lib/glass'

/**
 * 玻璃材质层。只负责“材质”，不承担布局与语义：
 * 用法是把 `<ZGlassSurface />` 放在一个 `.glass-host` 容器里，
 * 容器自己决定圆角、裁剪和内容层级（内容需要盖在材质之上）。
 *
 * depth:
 * - lens：真实折射（导航、浮层、重点操作）
 * - frost：只做背景模糊（大面板、Toast）
 * - flat：纯渐变与内高光（按钮、卡片、开关，成本最低）
 *
 * mode 控制折射形态；shader 需要 WebGL 逐像素着色，SVG 滤镜无法真实复现，
 * 因此降级为 polar（见 lib/glass.ts 的说明），不伪造参数。
 */
const props = withDefaults(
  defineProps<{
    material?: GlassMaterial
    depth?: 'lens' | 'frost' | 'flat'
    tone?: 'neutral' | 'accent' | 'danger'
    /** @deprecated 用 interactive 代替，保留是为了不动已有调用点 */
    track?: boolean
    /** 是否跟随指针微调高光（只建议给导航、弹窗等少量大表面开启） */
    interactive?: boolean
    mode?: GlassMode
    theme?: GlassTheme
    /** 折射强度，覆盖材质默认值 */
    displacementScale?: number
    /** 背景模糊半径（px），覆盖材质默认值 */
    blurAmount?: number
    /** 背景饱和度（百分比），覆盖材质默认值 */
    saturation?: number
    /** 边缘色散强度，覆盖材质默认值 */
    aberrationIntensity?: number
    /** 0~1，越大指针跟随越利落、回弹越明显 */
    elasticity?: number
    /** 材质层圆角（px）；不传则由宿主容器决定 */
    cornerRadius?: number
    /** 关闭高级折射与指针追踪，只保留底色与描边 */
    disabled?: boolean
  }>(),
  {
    material: 'frosted',
    depth: 'frost',
    tone: 'neutral',
    track: false,
    mode: 'polar',
    theme: 'auto',
    elasticity: 0.6,
    disabled: false
  }
)

const root = ref<HTMLElement | null>(null)

const active = computed(() => !props.disabled && (props.interactive ?? props.track))

const spec = computed(() =>
  resolveGlassSpec({
    material: props.material,
    mode: props.mode,
    displacementScale: props.displacementScale,
    aberrationIntensity: props.aberrationIntensity,
    blurAmount: props.blurAmount,
    saturation: props.saturation
  })
)

const refract = computed(
  () => !props.disabled && props.depth === 'lens' && supportsGlassRefraction()
)

/* 滤镜在 watcher 里注册而不是 computed 里，避免在渲染期写响应式状态。 */
const filterId = ref('')

watch(
  [spec, refract],
  ([value, canRefract]) => {
    filterId.value = canRefract
      ? registerGlassFilter({ kind: value.kind, scale: value.scale, aberration: value.aberration })
      : ''
  },
  { immediate: true }
)

const style = computed(() => {
  const value = spec.value
  const vars: Record<string, string> = {
    '--glass-blur': `${value.blur}px`,
    '--glass-saturate': `${value.saturation}%`,
    '--glass-contrast': `${value.contrast}%`,
    '--glass-lens-scale': String(value.scale),
    '--glass-track-dur': `${Math.round(420 - props.elasticity * 300)}ms`,
    '--glass-track-ease': props.elasticity > 0.65 ? 'var(--spring-soft)' : 'var(--ease-out)'
  }
  if (props.cornerRadius !== undefined) vars['border-radius'] = `${props.cornerRadius}px`
  return vars
})

/** 位移滤镜必须作用在同时拥有 backdrop-filter 的层上。 */
const warpStyle = computed(() =>
  filterId.value ? { filter: `url(#${filterId.value})` } : undefined
)

let host: HTMLElement | null = null
let frame = 0
let pending: { x: number; y: number } | null = null

/* 指针位置直接写 CSS 变量，不走响应式：避免每次 mousemove 触发组件重渲染。
   一帧最多写一次，且曲线交给 CSS（@property 注册后可插值）。 */
function flush() {
  frame = 0
  const element = root.value
  if (!element || !pending) return
  element.style.setProperty('--glass-x', pending.x.toFixed(3))
  element.style.setProperty('--glass-y', pending.y.toFixed(3))
  pending = null
}

function clamp01(value: number) {
  return Math.max(-1, Math.min(1, value))
}

function onPointerMove(event: PointerEvent) {
  if (!host) return
  const rect = host.getBoundingClientRect()
  if (rect.width === 0 || rect.height === 0) return
  pending = {
    x: clamp01(((event.clientX - rect.left) / rect.width) * 2 - 1),
    y: clamp01(((event.clientY - rect.top) / rect.height) * 2 - 1)
  }
  if (!frame) frame = requestAnimationFrame(flush)
}

function onPointerLeave() {
  pending = { x: 0, y: 0 }
  if (!frame) frame = requestAnimationFrame(flush)
}

function bind(value: boolean) {
  const next = root.value?.parentElement ?? null
  if (host === next && value) return
  if (host) {
    host.removeEventListener('pointermove', onPointerMove)
    host.removeEventListener('pointerleave', onPointerLeave)
  }
  host = value ? next : null
  if (host) {
    host.addEventListener('pointermove', onPointerMove, { passive: true })
    host.addEventListener('pointerleave', onPointerLeave, { passive: true })
  }
}

watch([active, root], ([value]) => bind(value), { immediate: true, flush: 'post' })

onBeforeUnmount(() => {
  if (frame) cancelAnimationFrame(frame)
  frame = 0
  if (!host) return
  host.removeEventListener('pointermove', onPointerMove)
  host.removeEventListener('pointerleave', onPointerLeave)
  host = null
})
</script>

<template>
  <span
    ref="root"
    class="glass-surface"
    :class="[`depth-${depth}`, `tone-${tone}`, { 'is-refracting': refract, 'is-interactive': active, 'is-static': disabled }]"
    :style="style"
    :data-glass-theme="theme === 'auto' ? undefined : theme"
    aria-hidden="true"
  >
    <span class="surface-warp" :style="warpStyle" />
    <span class="surface-tint" />
    <span class="surface-sheen" />
    <span class="surface-ring" />
  </span>
</template>

<style scoped>
.glass-surface {
  position: absolute;
  inset: 0;
  z-index: -1;
  display: block;
  border-radius: inherit;
  overflow: hidden;
  pointer-events: none;
  contain: paint;
}

/* 指针高光的位置用 CSS 变量表达：注册成 <number> 后可以平滑插值，
   弹性越高插值越快，松开指针时自然回中。 */
.glass-surface.is-interactive {
  transition:
    --glass-x var(--glass-track-dur, 260ms) var(--glass-track-ease, var(--ease-out)),
    --glass-y var(--glass-track-dur, 260ms) var(--glass-track-ease, var(--ease-out));
}

.glass-surface.is-static {
  opacity: 0.82;
}

/* --- 背景折射/模糊层 ---------------------------------------------------- */
.surface-warp {
  position: absolute;
  inset: 0;
  border-radius: inherit;
  transform: translateZ(0);
}

.depth-frost .surface-warp,
.depth-lens .surface-warp {
  -webkit-backdrop-filter: blur(var(--glass-blur)) saturate(var(--glass-saturate))
    contrast(var(--glass-contrast));
  backdrop-filter: blur(var(--glass-blur)) saturate(var(--glass-saturate))
    contrast(var(--glass-contrast));
}

.is-refracting .surface-warp {
  /* 位移滤镜作用在 backdrop-filter 的结果上：只有 Chromium 能做到 */
  will-change: filter;
}

/* --- 底色 ---------------------------------------------------------------- */
.surface-tint {
  position: absolute;
  inset: 0;
  border-radius: inherit;
  background:
    linear-gradient(
      165deg,
      color-mix(in srgb, var(--glass-hi) 32%, transparent),
      transparent 46%
    ),
    var(--glass-bg);
}

.depth-frost .surface-tint {
  background:
    linear-gradient(
      168deg,
      color-mix(in srgb, var(--glass-hi) 26%, transparent),
      transparent 40%
    ),
    var(--glass-bg-strong);
}

.depth-flat .surface-tint {
  background:
    linear-gradient(
      150deg,
      color-mix(in srgb, var(--glass-hi) 30%, transparent),
      transparent 42%
    ),
    var(--glass-flat);
}

.tone-accent .surface-tint {
  background:
    linear-gradient(
      150deg,
      color-mix(in srgb, var(--accent) 34%, var(--glass-flat)),
      var(--glass-flat) 62%
    );
}

.tone-danger .surface-tint {
  background:
    linear-gradient(
      150deg,
      color-mix(in srgb, var(--c-danger) 26%, var(--glass-flat)),
      var(--glass-flat) 68%
    );
}

/* --- 高光：随指针角度偏移，悬停时增强 ---------------------------------- */
.surface-sheen {
  position: absolute;
  inset: 0;
  border-radius: inherit;
  background:
    radial-gradient(
      circle at calc(50% + var(--glass-x, 0) * 34%) calc(0% + var(--glass-y, 0) * 10%),
      color-mix(in srgb, var(--glass-sheen) 62%, transparent),
      transparent 58%
    ),
    linear-gradient(
      calc(118deg + var(--glass-x, 0) * 16deg),
      transparent 18%,
      color-mix(in srgb, var(--glass-sheen) 26%, transparent) 42%,
      transparent 64%
    );
  opacity: 0.55;
  mix-blend-mode: screen;
  transition: opacity var(--dur-base) var(--ease-out);
}

.depth-flat .surface-sheen {
  opacity: 0.32;
}

.glass-host:hover .surface-sheen {
  opacity: 0.88;
}

[data-theme='dark'] .surface-sheen {
  opacity: 0.42;
  mix-blend-mode: screen;
}

[data-theme='dark'] .glass-host:hover .surface-sheen {
  opacity: 0.66;
}

/* --- 边缘：1.5px 渐变描边 + 内阴影 ------------------------------------ */
.surface-ring {
  position: absolute;
  inset: 0;
  border-radius: inherit;
  padding: 1.5px;
  background: linear-gradient(
    calc(135deg + var(--glass-x, 0) * 22deg),
    transparent 4%,
    color-mix(in srgb, var(--glass-edge) 42%, transparent) 24%,
    var(--glass-edge) 52%,
    color-mix(in srgb, var(--glass-edge) 30%, transparent) 78%,
    transparent 96%
  );
  -webkit-mask:
    linear-gradient(#000 0 0) content-box,
    linear-gradient(#000 0 0);
  -webkit-mask-composite: xor;
  mask:
    linear-gradient(#000 0 0) content-box,
    linear-gradient(#000 0 0);
  mask-composite: exclude;
  opacity: 0.8;
  box-shadow:
    inset 0 1px 1px var(--glass-hi),
    inset 0 -1px 2px var(--glass-lo);
}

[data-theme='dark'] .surface-ring {
  opacity: 0.55;
}

/* --- 单实例主题覆盖 ----------------------------------------------------- */
.glass-surface[data-glass-theme='light'] {
  --glass-bg: rgba(251, 248, 241, 0.58);
  --glass-bg-strong: rgba(251, 248, 241, 0.82);
  --glass-flat: rgba(255, 253, 248, 0.72);
  --glass-border: rgba(255, 255, 255, 0.62);
  --glass-edge: rgba(255, 255, 255, 0.92);
  --glass-sheen: rgba(255, 255, 255, 0.5);
  --glass-hi: rgba(255, 255, 255, 0.75);
  --glass-lo: rgba(64, 50, 30, 0.12);
}

.glass-surface[data-glass-theme='dark'] {
  --glass-bg: rgba(28, 25, 21, 0.52);
  --glass-bg-strong: rgba(28, 25, 21, 0.78);
  --glass-flat: rgba(38, 34, 29, 0.66);
  --glass-border: rgba(255, 255, 255, 0.09);
  --glass-edge: rgba(255, 255, 255, 0.16);
  --glass-sheen: rgba(255, 255, 255, 0.1);
  --glass-hi: rgba(255, 255, 255, 0.12);
  --glass-lo: rgba(0, 0, 0, 0.42);
}

/* --- 降级：不支持 backdrop-filter 时用更实的底色 ----------------------- */
@supports not ((backdrop-filter: blur(1px)) or (-webkit-backdrop-filter: blur(1px))) {
  .depth-frost .surface-tint,
  .depth-lens .surface-tint {
    background: var(--surface);
  }
}

@media (prefers-reduced-motion: reduce) {
  .glass-surface.is-interactive {
    transition: none;
  }
}

@media (prefers-reduced-transparency: reduce) {
  .depth-frost .surface-tint,
  .depth-lens .surface-tint {
    background: var(--surface);
  }

  .surface-warp {
    -webkit-backdrop-filter: none;
    backdrop-filter: none;
  }
}
</style>
