<script setup lang="ts">
withDefaults(
  defineProps<{
    tone?: 'surface' | 'sunken' | 'glass' | 'plain' | 'accent'
    padding?: 'none' | 'sm' | 'md' | 'lg'
    hover?: boolean
    as?: string
  }>(),
  { tone: 'surface', padding: 'md', hover: false, as: 'section' }
)
</script>

<template>
  <component
    :is="as"
    class="z-card"
    :class="[`tone-${tone}`, `pad-${padding}`, { 'is-hoverable': hover }]"
  >
    <slot />
  </component>
</template>

<style scoped>
.z-card {
  position: relative;
  border-radius: var(--radius-lg);
  border: 1px solid var(--line);
  background: var(--surface);
  box-shadow: var(--shadow-xs);
  transition:
    transform var(--dur-base) var(--ease-out),
    box-shadow var(--dur-base) var(--ease-out),
    border-color var(--dur-base) var(--ease-out);
}

.tone-surface {
  background: var(--surface);
}

.tone-sunken {
  background: var(--surface-sunken);
  border-color: transparent;
  box-shadow: none;
}

.tone-plain {
  background: transparent;
  border-color: transparent;
  box-shadow: none;
}

.tone-accent {
  background: linear-gradient(
    145deg,
    color-mix(in srgb, var(--accent-soft) 78%, var(--surface)),
    var(--surface)
  );
  border-color: color-mix(in srgb, var(--accent) 22%, var(--line));
}

.tone-glass {
  background: var(--glass-bg);
  border-color: var(--glass-border);
  box-shadow: var(--shadow-md), var(--shadow-inset);
  -webkit-backdrop-filter: blur(var(--glass-blur)) saturate(var(--glass-saturate));
  backdrop-filter: blur(var(--glass-blur)) saturate(var(--glass-saturate));
}

.pad-none {
  padding: 0;
}

.pad-sm {
  padding: var(--space-4);
}

.pad-md {
  padding: clamp(1.15rem, 0.9rem + 0.7vw, 1.6rem);
}

.pad-lg {
  padding: clamp(1.5rem, 1.1rem + 1.4vw, 2.25rem);
}

.is-hoverable:hover {
  transform: translateY(-2px);
  border-color: var(--line-strong);
  box-shadow: var(--shadow-md);
}
</style>
