<script setup lang="ts">
export interface SegmentOption {
  value: string
  label: string
}

withDefaults(
  defineProps<{
    modelValue: string
    options: SegmentOption[]
    size?: 'sm' | 'md'
    ariaLabel?: string
  }>(),
  { size: 'md' }
)

const emit = defineEmits<{ 'update:modelValue': [value: string] }>()
</script>

<template>
  <div class="z-segmented" :class="`size-${size}`" role="tablist" :aria-label="ariaLabel">
    <button
      v-for="option in options"
      :key="option.value"
      type="button"
      role="tab"
      class="segment"
      :class="{ 'is-active': option.value === modelValue }"
      :aria-selected="option.value === modelValue"
      @click="emit('update:modelValue', option.value)"
    >
      {{ option.label }}
    </button>
  </div>
</template>

<style scoped>
.z-segmented {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  padding: 4px;
  border-radius: var(--radius-pill);
  background: color-mix(in srgb, var(--ink) 5%, transparent);
  border: 1px solid var(--line);
}

.segment {
  position: relative;
  padding: 0.42rem 0.9rem;
  border-radius: var(--radius-pill);
  font-size: var(--text-sm);
  font-weight: 550;
  color: var(--ink-muted);
  white-space: nowrap;
  transition:
    color var(--dur-fast) var(--ease-out),
    background-color var(--dur-fast) var(--ease-out),
    box-shadow var(--dur-fast) var(--ease-out);
}

.size-sm .segment {
  padding: 0.3rem 0.7rem;
  font-size: var(--text-xs);
}

.segment:hover {
  color: var(--ink);
}

.segment.is-active {
  color: var(--ink);
  background: var(--surface-raised);
  box-shadow: var(--shadow-xs);
}
</style>
