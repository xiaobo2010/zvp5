<script setup lang="ts">
import ZIcon from './ZIcon.vue'

withDefaults(
  defineProps<{
    tone?: 'info' | 'success' | 'warning' | 'danger'
    title?: string
    description?: string
  }>(),
  { tone: 'info' }
)

const icons = {
  info: 'alert',
  success: 'check',
  warning: 'alert',
  danger: 'alert'
} as const
</script>

<template>
  <div class="z-alert" :class="`tone-${tone}`" role="status">
    <span class="icon"><ZIcon :name="icons[tone]" :size="18" /></span>
    <div class="body">
      <p v-if="title" class="title">{{ title }}</p>
      <p v-if="description" class="desc">{{ description }}</p>
      <slot />
    </div>
  </div>
</template>

<style scoped>
.z-alert {
  display: flex;
  gap: 0.7rem;
  padding: 0.85rem 1rem;
  border-radius: var(--radius-md);
  border: 1px solid transparent;
  font-size: var(--text-sm);
}

.icon {
  flex: none;
  margin-top: 1px;
}

.body {
  display: flex;
  flex-direction: column;
  gap: 0.15rem;
  min-width: 0;
}

.title {
  font-weight: 600;
}

.desc {
  opacity: 0.9;
  line-height: 1.55;
}

.tone-info {
  color: var(--c-info);
  background: var(--c-info-soft);
  border-color: color-mix(in srgb, var(--c-info) 20%, transparent);
}

.tone-success {
  color: var(--c-success);
  background: var(--c-success-soft);
  border-color: color-mix(in srgb, var(--c-success) 20%, transparent);
}

.tone-warning {
  color: var(--c-warning);
  background: var(--c-warning-soft);
  border-color: color-mix(in srgb, var(--c-warning) 20%, transparent);
}

.tone-danger {
  color: var(--c-danger);
  background: var(--c-danger-soft);
  border-color: color-mix(in srgb, var(--c-danger) 22%, transparent);
}
</style>
