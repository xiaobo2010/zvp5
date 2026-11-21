<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import {
  GLASS_MATERIALS,
  glassFilterId,
  supportsGlassRefraction,
  type GlassMaterial
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
 */
const props = withDefaults(
  defineProps<{
    material?: GlassMaterial
    depth?: 'lens' | 'frost' | 'flat'
    tone?: 'neutral' | 'accent' | 'danger'
    /** 跟随指针微调高光角度（只建议给导航、弹窗等少量大表面开启） */
    track?: boolean
  }>(),
  { material: 'frosted', depth: 'frost', tone: 'neutral', track: false }
)

const root = ref<HTMLElement | null>(null)
const pointerX = ref(0)
const pointerY = ref(0)

const refract = computed(
  () => props.depth === 'lens' && supportsGlassRefraction() && Boolean(glassFilterId(props.material))
)

const style = computed(() => {
  const vars: Record<string, string> = {
    '--glass-x': pointerX.value.toFixed(3),
    '--glass-y': pointerY.value.toFixed(3),
    '--glass-lens-scale': String(GLASS_MATERIALS[props.material].scale)
  }
  return vars
})

/** 位移滤镜必须作用在同时拥有 backdrop-filter 的层上。 */
const warpStyle = computed(() =>
  refract.value ? { filter: `url(#${glassFilterId(props.material)})` } : undefined
)

let host: HTMLElement | null = null

function onPointerMove(event: PointerEvent) {
  if (!host) return
  const rect = host.getBoundingClientRect()
  if (rect.width === 0 || rect.height === 0) return
  pointerX.value = Math.max(-1, Math.min(1, ((event.clientX - rect.left) / rect.width) * 2 - 1))
  pointerY.value = Math.max(-1, Math.min(1, ((event.clientY - rect.top) / rect.height) * 2 - 1))
}

function onPointerLeave() {
  pointerX.value = 0
  pointerY.value = 0
}

onMounted(() => {
  if (!props.track) return
  host = root.value?.parentElement ?? null
  if (!host) return
  host.addEventListener('pointermove', onPointerMove, { passive: true })
  host.addEventListener('pointerleave', onPointerLeave, { passive: true })
})

onBeforeUnmount(() => {
  if (!host) return
  host.removeEventListener('pointermove', onPointerMove)
  host.removeEventListener('pointerleave', onPointerLeave)
})
</script>

<template>
  <span
    ref="root"
    class="glass-surface"
    :class="[`depth-${depth}`, `tone-${tone}`, { 'is-refracting': refract }]"
    :style="style"
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

/* --- 降级：不支持 backdrop-filter 时用更实的底色 ----------------------- */
@supports not ((backdrop-filter: blur(1px)) or (-webkit-backdrop-filter: blur(1px))) {
  .depth-frost .surface-tint,
  .depth-lens .surface-tint {
    background: var(--surface);
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
