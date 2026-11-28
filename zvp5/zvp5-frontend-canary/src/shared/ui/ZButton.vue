<script setup lang="ts">
import ZIcon from './ZIcon.vue'

withDefaults(
  defineProps<{
    variant?: 'primary' | 'secondary' | 'ghost' | 'danger' | 'quiet'
    size?: 'sm' | 'md' | 'lg'
    type?: 'button' | 'submit' | 'reset'
    loading?: boolean
    disabled?: boolean
    block?: boolean
    icon?: string
    iconRight?: string
  }>(),
  {
    variant: 'secondary',
    size: 'md',
    type: 'button',
    loading: false,
    disabled: false,
    block: false
  }
)

/** 指针位置只写 CSS 变量，不触发组件重渲染。 */
function trackPointer(event: PointerEvent) {
  const element = event.currentTarget as HTMLElement
  const rect = element.getBoundingClientRect()
  if (rect.width === 0 || rect.height === 0) return
  const nx = ((event.clientX - rect.left) / rect.width) * 2 - 1
  const ny = ((event.clientY - rect.top) / rect.height) * 2 - 1
  element.style.setProperty('--bx', `${((event.clientX - rect.left) / rect.width) * 100}%`)
  element.style.setProperty('--by', `${((event.clientY - rect.top) / rect.height) * 100}%`)
  // 主要按钮的轻微磁吸：最多 3px，离开即回中
  element.style.setProperty('--mag-x', `${(nx * 2.6).toFixed(2)}px`)
  element.style.setProperty('--mag-y', `${(ny * 1.8).toFixed(2)}px`)
}

function resetPointer(event: PointerEvent) {
  const element = event.currentTarget as HTMLElement
  element.style.setProperty('--mag-x', '0px')
  element.style.setProperty('--mag-y', '0px')
}
</script>

<template>
  <button
    :type="type"
    class="z-button"
    :class="[`is-${variant}`, `size-${size}`, { 'is-block': block, 'is-loading': loading }]"
    :disabled="disabled || loading"
    @pointermove="trackPointer"
    @pointerleave="resetPointer"
  >
    <span v-if="loading" class="spinner" aria-hidden="true" />
    <ZIcon v-else-if="icon" :name="icon" :size="size === 'sm' ? 16 : 18" />
    <span v-if="$slots.default" class="label"><slot /></span>
    <ZIcon v-if="iconRight && !loading" :name="iconRight" :size="size === 'sm' ? 16 : 18" />
  </button>
</template>

<style scoped>
.z-button {
  --btn-height: 44px;
  --btn-pad: 1.15rem;
  --bx: 50%;
  --by: 50%;
  --mag-x: 0px;
  --mag-y: 0px;
  position: relative;
  isolation: isolate;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 0.5rem;
  min-height: var(--btn-height);
  padding: 0 var(--btn-pad);
  border-radius: var(--radius-pill);
  border: 1px solid transparent;
  overflow: hidden;
  font-family: var(--font-sans);
  font-size: var(--text-base);
  font-weight: 560;
  letter-spacing: 0.005em;
  white-space: nowrap;
  -webkit-tap-highlight-color: transparent;
  transition:
    transform var(--dur-micro) var(--spring-snappy),
    background-color var(--dur-fast) var(--ease-out),
    border-color var(--dur-fast) var(--ease-out),
    box-shadow var(--dur-fast) var(--ease-out),
    color var(--dur-fast) var(--ease-out);
}

/* 指针跟随的液态高光：一层即可，避免每个按钮都跑滤镜 */
.z-button::before {
  content: '';
  position: absolute;
  inset: -1px;
  z-index: -1;
  border-radius: inherit;
  background: radial-gradient(
    140% 120% at var(--bx) var(--by),
    rgba(255, 255, 255, 0.34),
    transparent 58%
  );
  opacity: 0;
  transition: opacity var(--dur-fast) var(--ease-out);
  pointer-events: none;
}

.z-button:not(:disabled):hover::before {
  opacity: 0.65;
}

.z-button:disabled {
  cursor: not-allowed;
  opacity: 0.5;
  filter: saturate(0.7);
}

.z-button:not(:disabled):hover {
  transform: translateY(-1px) scale(1.012);
}

.z-button:not(:disabled):active {
  transform: translateY(0) scale(0.965);
  transition-duration: 90ms;
}

.z-button:focus-visible {
  outline: none;
  box-shadow:
    var(--focus-ring),
    inset 0 1px 0 var(--glass-hi);
}

.size-sm {
  --btn-height: 36px;
  --btn-pad: 0.9rem;
  font-size: var(--text-sm);
}

.size-lg {
  --btn-height: 52px;
  --btn-pad: 1.5rem;
  font-size: var(--text-md);
}

.is-block {
  width: 100%;
}

/* --- 玻璃按钮材质 ------------------------------------------------------- */
.is-primary {
  color: #fffdf8;
  border-color: color-mix(in srgb, var(--accent) 62%, transparent);
  background:
    linear-gradient(
      155deg,
      color-mix(in srgb, var(--accent) 82%, white),
      var(--accent-strong) 58%,
      color-mix(in srgb, var(--accent-strong) 82%, black)
    );
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.45),
    inset 0 -2px 6px -3px rgba(60, 34, 4, 0.55),
    0 10px 24px -14px color-mix(in srgb, var(--accent-strong) 85%, transparent);
}

.is-primary:not(:disabled):hover {
  transform: translate3d(var(--mag-x), calc(var(--mag-y) - 1px), 0) scale(1.012);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.55),
    inset 0 -2px 6px -3px rgba(60, 34, 4, 0.5),
    0 16px 32px -16px color-mix(in srgb, var(--accent-strong) 95%, transparent);
}

.is-primary:not(:disabled):active {
  transform: translate3d(var(--mag-x), var(--mag-y), 0) scale(0.975);
  transition-duration: 100ms;
}

[data-theme='dark'] .is-primary {
  color: #1a1815;
  background:
    linear-gradient(
      155deg,
      color-mix(in srgb, var(--accent) 92%, white),
      var(--accent) 62%,
      color-mix(in srgb, var(--accent) 84%, #6b430b)
    );
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.55),
    inset 0 -2px 8px -4px rgba(90, 52, 4, 0.45),
    0 14px 30px -18px rgba(230, 180, 95, 0.55);
}

.is-secondary {
  color: var(--ink-soft);
  border-color: color-mix(in srgb, var(--glass-border) 85%, var(--line));
  background:
    linear-gradient(158deg, color-mix(in srgb, var(--glass-hi) 30%, transparent), transparent 58%),
    var(--glass-flat);
  box-shadow:
    inset 0 1px 0 var(--glass-hi),
    inset 0 -1px 2px color-mix(in srgb, var(--ink) 6%, transparent),
    0 6px 16px -12px rgba(40, 30, 16, 0.35);
}

.is-secondary:not(:disabled):hover {
  color: var(--ink);
  border-color: color-mix(in srgb, var(--accent) 30%, var(--glass-border));
  box-shadow:
    inset 0 1px 0 var(--glass-hi),
    0 10px 22px -14px rgba(40, 30, 16, 0.4);
}

.is-ghost {
  color: var(--ink-soft);
  border-color: color-mix(in srgb, var(--line) 72%, transparent);
  background: color-mix(in srgb, var(--glass-flat) 42%, transparent);
  box-shadow: inset 0 1px 0 color-mix(in srgb, var(--glass-hi) 60%, transparent);
}

.is-ghost:not(:disabled):hover {
  color: var(--ink);
  border-color: color-mix(in srgb, var(--accent) 28%, var(--line));
  background: color-mix(in srgb, var(--glass-flat) 76%, transparent);
}

.is-quiet {
  color: var(--ink-muted);
  background: transparent;
  padding-inline: 0.6rem;
}

.is-quiet:not(:disabled):hover {
  color: var(--ink);
  background: color-mix(in srgb, var(--glass-hi) 34%, transparent);
}

.is-danger {
  color: #fff;
  border-color: color-mix(in srgb, var(--c-danger-strong) 65%, transparent);
  background:
    linear-gradient(
      155deg,
      color-mix(in srgb, var(--c-danger) 82%, white),
      var(--c-danger-strong) 62%
    );
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.38),
    inset 0 -2px 6px -3px rgba(70, 14, 6, 0.5),
    0 10px 24px -16px rgba(140, 40, 26, 0.65);
}

.is-danger:not(:disabled):hover {
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.45),
    0 16px 30px -18px rgba(140, 40, 26, 0.7);
}

.label {
  position: relative;
}

.spinner {
  width: 1em;
  height: 1em;
  border-radius: 50%;
  border: 2px solid currentColor;
  border-top-color: transparent;
  animation: button-spin 0.75s linear infinite;
}

@keyframes button-spin {
  to {
    transform: rotate(360deg);
  }
}

@media (prefers-reduced-motion: reduce) {
  .z-button:not(:disabled):hover,
  .z-button:not(:disabled):active,
  .z-button.is-primary:not(:disabled):hover,
  .z-button.is-primary:not(:disabled):active {
    transform: none;
  }
}
</style>
