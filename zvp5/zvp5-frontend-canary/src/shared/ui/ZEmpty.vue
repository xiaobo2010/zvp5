<script setup lang="ts">
import ZIcon from './ZIcon.vue'

withDefaults(
  defineProps<{
    title?: string
    description?: string
    icon?: string
    compact?: boolean
  }>(),
  { icon: 'sparkles', compact: false }
)
</script>

<template>
  <div class="z-empty" :class="{ 'is-compact': compact }">
    <span class="glyph"><ZIcon :name="icon" :size="compact ? 20 : 26" /></span>
    <p v-if="title" class="title">{{ title }}</p>
    <p v-if="description" class="desc">{{ description }}</p>
    <slot />
  </div>
</template>

<style scoped>
.z-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 0.5rem;
  padding: var(--space-12) var(--space-6);
  text-align: center;
}

.is-compact {
  padding: var(--space-8) var(--space-4);
}

.glyph {
  display: grid;
  place-items: center;
  width: 52px;
  height: 52px;
  margin-bottom: var(--space-2);
  border-radius: 50%;
  color: var(--accent-ink);
  background: var(--accent-soft);
  border: 1px solid color-mix(in srgb, var(--accent) 18%, transparent);
}

.is-compact .glyph {
  width: 40px;
  height: 40px;
}

.title {
  font-family: var(--font-display);
  font-size: var(--text-lg);
  font-weight: 600;
  color: var(--ink);
}

.desc {
  max-width: 34ch;
  font-size: var(--text-sm);
  color: var(--ink-muted);
  line-height: 1.6;
}
</style>
