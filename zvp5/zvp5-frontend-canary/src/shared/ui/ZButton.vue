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
</script>

<template>
  <button
    :type="type"
    class="z-button"
    :class="[`is-${variant}`, `size-${size}`, { 'is-block': block, 'is-loading': loading }]"
    :disabled="disabled || loading"
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
  position: relative;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 0.5rem;
  min-height: var(--btn-height);
  padding: 0 var(--btn-pad);
  border-radius: var(--radius-pill);
  border: 1px solid transparent;
  font-family: var(--font-sans);
  font-size: var(--text-base);
  font-weight: 560;
  letter-spacing: 0.005em;
  white-space: nowrap;
  transition:
    transform var(--dur-fast) var(--ease-out),
    background-color var(--dur-fast) var(--ease-out),
    border-color var(--dur-fast) var(--ease-out),
    box-shadow var(--dur-fast) var(--ease-out),
    color var(--dur-fast) var(--ease-out);
}

.z-button:disabled {
  cursor: not-allowed;
  opacity: 0.55;
}

.z-button:not(:disabled):hover {
  transform: translateY(-1px);
}

.z-button:not(:disabled):active {
  transform: translateY(0) scale(0.985);
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

.is-primary {
  background: var(--accent-strong);
  color: #fffdf8;
  box-shadow: var(--shadow-sm);
}

[data-theme='dark'] .is-primary {
  color: #1a1815;
}

.is-primary:not(:disabled):hover {
  box-shadow: var(--shadow-md);
  background: color-mix(in srgb, var(--accent-strong) 90%, black);
}

[data-theme='dark'] .is-primary:not(:disabled):hover {
  background: color-mix(in srgb, var(--accent-strong) 92%, white);
}

.is-secondary {
  background: var(--surface-raised);
  border-color: var(--line);
  color: var(--ink-soft);
  box-shadow: var(--shadow-xs);
}

.is-secondary:not(:disabled):hover {
  border-color: var(--line-strong);
  color: var(--ink);
}

.is-ghost {
  background: transparent;
  border-color: var(--line);
  color: var(--ink-soft);
}

.is-ghost:not(:disabled):hover {
  background: color-mix(in srgb, var(--ink) 5%, transparent);
  color: var(--ink);
}

.is-quiet {
  background: transparent;
  color: var(--ink-muted);
  padding-inline: 0.6rem;
}

.is-quiet:not(:disabled):hover {
  background: color-mix(in srgb, var(--ink) 6%, transparent);
  color: var(--ink);
}

.is-danger {
  background: var(--c-danger-strong);
  color: #fff;
  box-shadow: var(--shadow-sm);
}

.is-danger:not(:disabled):hover {
  background: color-mix(in srgb, var(--c-danger-strong) 88%, black);
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
</style>
