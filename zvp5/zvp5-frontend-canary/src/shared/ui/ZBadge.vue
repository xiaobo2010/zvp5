<script setup lang="ts">
withDefaults(
  defineProps<{
    tone?: 'neutral' | 'accent' | 'success' | 'warning' | 'danger' | 'info'
    size?: 'sm' | 'md'
    dot?: boolean
  }>(),
  { tone: 'neutral', size: 'sm', dot: false }
)
</script>

<template>
  <span class="z-badge" :class="[`tone-${tone}`, `size-${size}`]">
    <span v-if="dot" class="dot" aria-hidden="true" />
    <slot />
  </span>
</template>

<style scoped>
.z-badge {
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
  padding: 0.22rem 0.6rem;
  border-radius: var(--radius-pill);
  border: 1px solid transparent;
  font-size: var(--text-2xs);
  font-weight: 600;
  letter-spacing: 0.02em;
  line-height: 1.6;
  white-space: nowrap;
}

.size-md {
  font-size: var(--text-xs);
  padding: 0.3rem 0.7rem;
}

.dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: currentColor;
}

.tone-neutral {
  color: var(--ink-muted);
  background: color-mix(in srgb, var(--ink) 5%, transparent);
  border-color: var(--line);
}

.tone-accent {
  color: var(--accent-ink);
  background: var(--accent-soft);
  border-color: color-mix(in srgb, var(--accent) 26%, transparent);
}

.tone-success {
  color: var(--c-success);
  background: var(--c-success-soft);
  border-color: color-mix(in srgb, var(--c-success) 24%, transparent);
}

.tone-warning {
  color: var(--c-warning);
  background: var(--c-warning-soft);
  border-color: color-mix(in srgb, var(--c-warning) 24%, transparent);
}

.tone-danger {
  color: var(--c-danger);
  background: var(--c-danger-soft);
  border-color: color-mix(in srgb, var(--c-danger) 24%, transparent);
}

.tone-info {
  color: var(--c-info);
  background: var(--c-info-soft);
  border-color: color-mix(in srgb, var(--c-info) 22%, transparent);
}
</style>
