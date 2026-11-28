<script setup lang="ts">
import { computed } from 'vue'

const props = withDefaults(
  defineProps<{
    value: number
    max?: number
    tone?: 'accent' | 'success' | 'info'
    label?: string
    showValue?: boolean
    height?: number
  }>(),
  { max: 100, tone: 'accent', showValue: false, height: 8 }
)

const percent = computed(() => {
  if (props.max <= 0) return 0
  return Math.min(100, Math.max(0, (props.value / props.max) * 100))
})
</script>

<template>
  <div class="z-progress">
    <div v-if="label || showValue" class="head">
      <span v-if="label" class="label">{{ label }}</span>
      <span v-if="showValue" class="value num">{{ Math.round(percent) }}%</span>
    </div>
    <div
      class="track"
      :style="{ height: `${height}px` }"
      role="progressbar"
      :aria-valuenow="value"
      :aria-valuemin="0"
      :aria-valuemax="max"
    >
      <span class="fill" :class="`tone-${tone}`" :style="{ width: `${percent}%` }" />
    </div>
  </div>
</template>

<style scoped>
.z-progress {
  display: flex;
  flex-direction: column;
  gap: 0.45rem;
  width: 100%;
}

.head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--space-3);
  font-size: var(--text-xs);
  color: var(--ink-muted);
}

.value {
  font-weight: 600;
  color: var(--ink-soft);
}

.track {
  width: 100%;
  overflow: hidden;
  border-radius: var(--radius-pill);
  background: color-mix(in srgb, var(--ink) 7%, transparent);
}

.fill {
  display: block;
  height: 100%;
  border-radius: inherit;
  transition: width var(--dur-slow) var(--ease-out);
}

.tone-accent {
  background: linear-gradient(90deg, #c99a45, var(--accent-strong));
}

.tone-success {
  background: linear-gradient(90deg, #7fa889, var(--c-success));
}

.tone-info {
  background: linear-gradient(90deg, #8fafc5, var(--c-info));
}
</style>
