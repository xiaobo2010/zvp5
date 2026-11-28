<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import ZIcon from '@/shared/ui/ZIcon.vue'

const props = withDefaults(
  defineProps<{
    modelValue: number
    options: number[]
    auto?: boolean
    disabled?: boolean
  }>(),
  { auto: false, disabled: false }
)

const emit = defineEmits<{
  'update:modelValue': [value: number]
  'update:auto': [value: boolean]
}>()

const { t } = useI18n()
const open = ref(false)
const root = ref<HTMLElement | null>(null)

function choose(value: number) {
  emit('update:modelValue', value)
  open.value = false
}

function toggleAuto() {
  emit('update:auto', !props.auto)
  open.value = false
}

function onDocumentPointer(event: PointerEvent) {
  if (!root.value?.contains(event.target as Node)) open.value = false
}

function onKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape') open.value = false
}

watch(open, (value) => {
  if (value) {
    document.addEventListener('pointerdown', onDocumentPointer)
    document.addEventListener('keydown', onKeydown)
  } else {
    document.removeEventListener('pointerdown', onDocumentPointer)
    document.removeEventListener('keydown', onKeydown)
  }
})

onBeforeUnmount(() => {
  document.removeEventListener('pointerdown', onDocumentPointer)
  document.removeEventListener('keydown', onKeydown)
})
</script>

<template>
  <div ref="root" class="size">
    <button
      type="button"
      class="trigger"
      :class="{ 'is-auto': auto }"
      :disabled="disabled"
      aria-haspopup="listbox"
      :aria-expanded="open"
      @click="open = !open"
    >
      <span class="trigger-label">{{ t('pagination.perPage') }}</span>
      <strong class="num">{{ modelValue }}</strong>
      <span v-if="auto" class="auto-dot" aria-hidden="true" />
      <ZIcon name="chevron-down" :size="14" class="chevron" :class="{ rotated: open }" />
    </button>

    <Transition name="menu">
      <div v-if="open" class="menu" role="listbox" :aria-label="t('pagination.perPage')">
        <button
          type="button"
          class="option auto-option"
          :class="{ 'is-active': auto }"
          role="option"
          :aria-selected="auto"
          @click="toggleAuto"
        >
          <span>{{ t('pagination.auto') }}</span>
          <ZIcon v-if="auto" name="check" :size="14" />
        </button>
        <span class="menu-sep" aria-hidden="true" />
        <button
          v-for="option in options"
          :key="option"
          type="button"
          class="option"
          :class="{ 'is-active': !auto && option === modelValue }"
          role="option"
          :aria-selected="!auto && option === modelValue"
          @click="choose(option)"
        >
          <span class="num">{{ option }} {{ t('pagination.rows') }}</span>
          <ZIcon v-if="!auto && option === modelValue" name="check" :size="14" />
        </button>
      </div>
    </Transition>
  </div>
</template>

<style scoped>
.size {
  position: relative;
}

.trigger {
  display: inline-flex;
  align-items: center;
  gap: 0.32rem;
  height: 30px;
  padding: 0 0.6rem;
  border: 1px solid color-mix(in srgb, var(--glass-border) 82%, transparent);
  border-radius: var(--radius-pill);
  background: color-mix(in srgb, var(--glass-raised) 48%, transparent);
  color: var(--ink-soft);
  font-size: var(--text-2xs);
  transition:
    border-color var(--dur-fast) var(--ease-out),
    background-color var(--dur-fast) var(--ease-out),
    transform var(--dur-fast) var(--spring-snappy);
}

.trigger:hover {
  background: color-mix(in srgb, var(--glass-raised) 76%, transparent);
}

.trigger:active {
  transform: scale(0.97);
}

.trigger:disabled {
  opacity: 0.5;
}

.trigger-label {
  color: var(--ink-faint);
}

.trigger strong {
  font-size: var(--text-xs);
  color: var(--ink);
}

.auto-dot {
  width: 4px;
  height: 4px;
  border-radius: 50%;
  background: var(--accent);
}

.chevron {
  color: var(--ink-faint);
  transition: transform var(--dur-fast) var(--ease-out);
}

.chevron.rotated {
  transform: rotate(180deg);
}

.menu {
  position: absolute;
  right: 0;
  bottom: calc(100% + 8px);
  z-index: 40;
  min-width: 9.5rem;
  padding: 0.35rem;
  border: 1px solid color-mix(in srgb, var(--glass-border) 88%, transparent);
  border-radius: var(--radius-lg);
  background: color-mix(in srgb, var(--surface-raised) 92%, transparent);
  box-shadow: var(--shadow-lg, 0 20px 48px -28px rgba(60, 40, 12, 0.45));
  -webkit-backdrop-filter: blur(18px) saturate(150%);
  backdrop-filter: blur(18px) saturate(150%);
  transform-origin: bottom right;
}

.option {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  width: 100%;
  padding: 0.42rem 0.5rem;
  border: 0;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--ink-soft);
  font-size: var(--text-xs);
  text-align: left;
  transition: background-color var(--dur-fast) var(--ease-out);
}

.option:hover {
  background: color-mix(in srgb, var(--glass-hi) 30%, transparent);
}

.option.is-active {
  color: var(--accent-ink);
  background: color-mix(in srgb, var(--accent) 10%, transparent);
}

.auto-option {
  color: var(--ink);
}

.menu-sep {
  display: block;
  height: 1px;
  margin: 0.25rem 0.3rem;
  background: color-mix(in srgb, var(--line) 70%, transparent);
}

.menu-enter-active {
  transition:
    opacity 160ms var(--ease-out),
    transform 260ms var(--spring-soft);
}

.menu-leave-active {
  transition:
    opacity 120ms var(--ease-out),
    transform 140ms var(--ease-out);
}

.menu-enter-from,
.menu-leave-to {
  opacity: 0;
  transform: translateY(6px) scale(0.96);
}
</style>
