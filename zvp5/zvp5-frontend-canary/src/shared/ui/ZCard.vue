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
    transform var(--dur-base) var(--spring-snappy),
    box-shadow var(--dur-base) var(--ease-out),
    border-color var(--dur-base) var(--ease-out);
}

/* 可交互卡片的液态高光：只在 hover 时出现，不额外跑模糊 */
.is-hoverable::after {
  content: '';
  position: absolute;
  inset: 0;
  border-radius: inherit;
  background:
    radial-gradient(120% 90% at 12% 0%, color-mix(in srgb, var(--glass-hi) 38%, transparent), transparent 52%),
    linear-gradient(135deg, transparent 40%, color-mix(in srgb, var(--accent) 8%, transparent) 100%);
  opacity: 0;
  transition: opacity var(--dur-base) var(--ease-out);
  pointer-events: none;
}

.is-hoverable:hover::after {
  opacity: 1;
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
  background:
    linear-gradient(158deg, color-mix(in srgb, var(--glass-hi) 28%, transparent), transparent 52%),
    var(--glass-bg);
  border-color: var(--glass-border);
  box-shadow: var(--glass-shadow), inset 0 1px 0 var(--glass-hi);
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
