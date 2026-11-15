<script setup lang="ts">
/**
 * 全站统一的「返回」按钮。
 *
 * 只保留一个圆形玻璃 + 返回箭头：不要文字、不要「‹ 返回」那种组合。
 * 玻璃层用 LiquidGlass（WebGL 真实折射）；WebGL 不可用时它自己会退回 CSS 玻璃，
 * 按钮仍然可见可点。
 */
import { ArrowLeft } from 'lucide-vue-next'
import LiquidGlass from './LiquidGlass.vue'

const props = withDefaults(
  defineProps<{
    /** 无障碍标签，默认「返回」 */
    label?: string
    /** 圆形直径，默认 40px */
    size?: number
  }>(),
  { label: '返回', size: 40 },
)

const emit = defineEmits<{ (e: 'click', event: MouseEvent): void }>()

function onClick(event: MouseEvent) {
  emit('click', event)
}
</script>

<template>
  <button
    class="yg2-back"
    type="button"
    :aria-label="props.label"
    :style="{ '--yg2-back-size': `${props.size}px` }"
    @click="onClick"
  >
    <LiquidGlass
      class="yg2-back__glass"
      shape="circle"
      preset="button"
      :radius="props.size / 2"
      :tint="0.10"
      capture=".yg2-ambient"
    />
    <ArrowLeft class="yg2-back__icon" :size="18" :stroke-width="2" aria-hidden="true" />
  </button>
</template>

<style scoped>
.yg2-back {
  position: relative;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: var(--yg2-back-size, 40px);
  height: var(--yg2-back-size, 40px);
  padding: 0;
  border: 0;
  border-radius: 999px;
  background: transparent;
  color: var(--yg2-text-secondary);
  cursor: pointer;
  isolation: isolate;
  transition:
    transform 180ms var(--yg2-ease-out),
    color 180ms var(--yg2-ease-out);
  -webkit-tap-highlight-color: transparent;
}

/* 没有 WebGL 时也要有「玻璃」的形：一圈描边 + 一点内高光 */
.yg2-back::before {
  content: '';
  position: absolute;
  inset: 0;
  z-index: 1;
  border-radius: inherit;
  border: 1px solid color-mix(in srgb, var(--yg2-border) 70%, transparent);
  box-shadow:
    inset 0 1px 0 color-mix(in srgb, #ffffff 55%, transparent),
    0 6px 16px color-mix(in srgb, #1f2d3d 12%, transparent);
  pointer-events: none;
}

.yg2-back__glass {
  z-index: 0;
}

/* 玻璃的「高光面」：左上一点反射光，整体压一层极淡的竖向渐变 */
.yg2-back::after {
  content: '';
  position: absolute;
  inset: 0;
  z-index: 1;
  border-radius: inherit;
  background:
    radial-gradient(120% 90% at 30% 16%, color-mix(in srgb, #ffffff 62%, transparent), transparent 62%),
    linear-gradient(180deg, color-mix(in srgb, #ffffff 20%, transparent), transparent 70%);
  pointer-events: none;
}

.yg2-back__icon {
  position: relative;
  z-index: 2;
  margin-left: -1px;
}

.yg2-back:hover {
  color: var(--yg2-text-primary);
  transform: translate3d(0, -1px, 0);
}

.yg2-back:active {
  transform: translate3d(0, 0, 0) scale(0.96);
}

.yg2-back:focus-visible {
  outline: 3px solid var(--yg2-accent);
  outline-offset: 2px;
}

.dark .yg2-back::before {
  border-color: color-mix(in srgb, #ffffff 16%, transparent);
  box-shadow:
    inset 0 1px 0 color-mix(in srgb, #ffffff 14%, transparent),
    0 6px 16px rgba(0, 0, 0, 0.4);
}

@media (prefers-reduced-motion: reduce) {
  .yg2-back,
  .yg2-back:hover,
  .yg2-back:active {
    transition: none;
    transform: none;
  }
}
</style>
