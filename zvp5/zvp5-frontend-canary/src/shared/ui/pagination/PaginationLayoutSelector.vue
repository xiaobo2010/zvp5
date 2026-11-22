<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { motion } from 'motion-v'
import { useI18n } from 'vue-i18n'
import { springs } from '@/motion/presets'
import type { PageColumns } from '@/shared/composables/useAdaptivePagination'

const props = defineProps<{ modelValue: PageColumns }>()
const emit = defineEmits<{ 'update:modelValue': [value: PageColumns] }>()

const { t } = useI18n()
const root = ref<HTMLElement | null>(null)
const pill = ref({ x: 0, width: 0, ready: false })
let observer: ResizeObserver | null = null

const options = computed(() => [
  { value: 1 as PageColumns, label: t('pagination.single') },
  { value: 2 as PageColumns, label: t('pagination.double') }
])

async function measure() {
  await nextTick()
  const active = root.value?.querySelector<HTMLElement>(`[data-cols="${props.modelValue}"]`)
  if (!root.value || !active) return
  pill.value = { x: active.offsetLeft, width: active.offsetWidth, ready: true }
}

function choose(value: PageColumns) {
  if (value !== props.modelValue) emit('update:modelValue', value)
}

onMounted(() => {
  void measure()
  if (root.value) {
    observer = new ResizeObserver(() => void measure())
    observer.observe(root.value)
  }
})

onBeforeUnmount(() => {
  observer?.disconnect()
  observer = null
})

watch(() => props.modelValue, () => void measure())
</script>

<template>
  <div ref="root" class="layout" role="group" :aria-label="t('pagination.layout')">
    <motion.span
      class="pill"
      aria-hidden="true"
      :initial="false"
      :animate="{ x: pill.x, width: pill.width, opacity: pill.ready ? 1 : 0 }"
      :transition="springs.snappy"
    />
    <button
      v-for="option in options"
      :key="option.value"
      type="button"
      class="btn"
      :class="{ 'is-active': option.value === modelValue }"
      :data-cols="option.value"
      :aria-label="option.label"
      :aria-pressed="option.value === modelValue"
      @click="choose(option.value)"
    >
      <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true">
        <template v-if="option.value === 1">
          <rect x="1.6" y="2.4" width="12.8" height="11.2" rx="2.2" stroke="currentColor" stroke-width="1.4" />
        </template>
        <template v-else>
          <rect x="1.6" y="2.4" width="5.4" height="11.2" rx="1.8" stroke="currentColor" stroke-width="1.4" />
          <rect x="9" y="2.4" width="5.4" height="11.2" rx="1.8" stroke="currentColor" stroke-width="1.4" />
        </template>
      </svg>
    </button>
  </div>
</template>

<style scoped>
.layout {
  position: relative;
  display: flex;
  align-items: center;
  gap: 2px;
  padding: 3px;
  border-radius: var(--radius-pill);
  background: color-mix(in srgb, var(--ink) 4%, transparent);
}

.pill {
  position: absolute;
  top: 3px;
  left: 0;
  height: calc(100% - 6px);
  border-radius: var(--radius-pill);
  background: rgba(255, 252, 244, 0.9);
  border: 1px solid color-mix(in srgb, var(--glass-border) 80%, transparent);
  box-shadow: inset 0 1px 0 var(--glass-hi);
  pointer-events: none;
}

[data-theme='dark'] .pill {
  background: rgba(74, 63, 48, 0.92);
  border-color: rgba(255, 235, 200, 0.16);
  box-shadow: inset 0 1px 0 rgba(255, 244, 224, 0.12);
}

.btn {
  position: relative;
  z-index: 1;
  display: grid;
  place-items: center;
  width: 30px;
  height: 26px;
  border: 0;
  border-radius: var(--radius-pill);
  background: transparent;
  color: var(--ink-faint);
  transition: color var(--dur-fast) var(--ease-out);
}

.btn:hover {
  color: var(--ink-soft);
}

.btn.is-active {
  color: var(--accent-ink);
}

.btn:active {
  transform: scale(0.92);
}
</style>
