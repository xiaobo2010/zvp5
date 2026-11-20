<script lang="ts">
export interface AnimatedTabItem {
  value: string
  label: string
  hint?: string
}
</script>

<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

const props = defineProps<{
  items: AnimatedTabItem[]
  modelValue: string
  ariaLabel?: string
}>()

const emit = defineEmits<{ 'update:modelValue': [value: string] }>()

const track = ref<HTMLElement | null>(null)
const tabElements = new Map<string, HTMLElement>()
const indicator = ref({ left: 0, width: 0, ready: false })
let observer: ResizeObserver | null = null

function setTabRef(value: string, element: Element | null) {
  if (element instanceof HTMLElement) tabElements.set(value, element)
  else tabElements.delete(value)
}

function measure() {
  const active = tabElements.get(props.modelValue)
  const host = track.value
  if (!active || !host) {
    indicator.value.ready = false
    return
  }
  indicator.value = {
    left: active.offsetLeft,
    width: active.offsetWidth,
    ready: true
  }
}

function select(value: string) {
  if (value !== props.modelValue) emit('update:modelValue', value)
}

function onKeydown(event: KeyboardEvent) {
  if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return
  const index = props.items.findIndex((item) => item.value === props.modelValue)
  if (index < 0) return
  event.preventDefault()
  const next =
    event.key === 'ArrowRight'
      ? (index + 1) % props.items.length
      : (index - 1 + props.items.length) % props.items.length
  const item = props.items[next]
  if (item) select(item.value)
}

watch(
  () => props.modelValue,
  () => {
    void nextTick(measure)
  }
)

watch(
  () => props.items,
  () => {
    void nextTick(measure)
  },
  { deep: true }
)

onMounted(() => {
  void nextTick(measure)
  if (typeof ResizeObserver !== 'undefined' && track.value) {
    observer = new ResizeObserver(() => measure())
    observer.observe(track.value)
  }
  window.addEventListener('resize', measure)
})

onBeforeUnmount(() => {
  observer?.disconnect()
  observer = null
  window.removeEventListener('resize', measure)
})
</script>

<template>
  <nav
    ref="track"
    class="animated-tabs"
    role="tablist"
    :aria-label="ariaLabel"
    @keydown="onKeydown"
  >
    <span
      class="animated-tabs__indicator"
      :class="{ 'is-ready': indicator.ready }"
      :style="{
        width: `${indicator.width}px`,
        transform: `translate3d(${indicator.left}px, 0, 0)`
      }"
      aria-hidden="true"
    />
    <button
      v-for="item in items"
      :key="item.value"
      :ref="(element) => setTabRef(item.value, element as Element | null)"
      type="button"
      role="tab"
      class="animated-tabs__tab"
      :class="{ 'is-active': item.value === modelValue }"
      :aria-selected="item.value === modelValue"
      :tabindex="item.value === modelValue ? 0 : -1"
      :title="item.hint"
      @click="select(item.value)"
    >
      <span class="animated-tabs__label">{{ item.label }}</span>
    </button>
  </nav>
</template>

<style scoped>
.animated-tabs {
  position: relative;
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 5px;
  overflow-x: auto;
  overscroll-behavior-x: contain;
  scrollbar-width: none;
  background: linear-gradient(
    135deg,
    rgba(255, 255, 255, 0.62) 0%,
    rgba(238, 246, 254, 0.42) 100%
  );
  -webkit-backdrop-filter: blur(16px) saturate(180%);
  backdrop-filter: blur(16px) saturate(180%);
  border: 1px solid rgba(255, 255, 255, 0.78);
  border-radius: var(--radius-pill);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.82),
    0 2px 10px rgba(18, 48, 82, 0.06);
  isolation: isolate;
}

.animated-tabs::-webkit-scrollbar {
  display: none;
}

.animated-tabs__indicator {
  position: absolute;
  top: 5px;
  bottom: 5px;
  left: 0;
  z-index: 0;
  border-radius: var(--radius-pill);
  background: linear-gradient(
    135deg,
    rgba(0, 113, 227, 0.19) 0%,
    rgba(64, 156, 255, 0.12) 100%
  );
  border: 1px solid rgba(0, 113, 227, 0.16);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.58),
    0 3px 12px rgba(14, 165, 233, 0.22);
  opacity: 0;
  pointer-events: none;
  transition:
    transform 440ms var(--ease-spring),
    width 440ms var(--ease-spring),
    opacity 180ms var(--ease-smooth);
}

.animated-tabs__indicator.is-ready {
  opacity: 1;
}

.animated-tabs__tab {
  position: relative;
  z-index: 1;
  flex: 0 0 auto;
  min-height: 38px;
  padding: 0 var(--space-4);
  border: 0;
  border-radius: var(--radius-pill);
  background: transparent;
  color: var(--color-text-secondary);
  font-family: inherit;
  font-size: var(--font-size-callout);
  font-weight: 600;
  line-height: 1;
  white-space: nowrap;
  cursor: pointer;
  transition:
    color var(--duration-fast) var(--ease-smooth),
    transform var(--duration-fast) var(--ease-spring);
}

.animated-tabs__tab:hover {
  color: var(--color-text-primary);
}

.animated-tabs__tab:active {
  transform: scale(0.97);
}

.animated-tabs__tab:focus-visible {
  outline: 2px solid var(--color-brand);
  outline-offset: 1px;
}

.animated-tabs__tab.is-active {
  color: var(--color-brand);
}

@media (prefers-reduced-motion: reduce) {
  .animated-tabs__indicator {
    transition: none;
  }

  .animated-tabs__tab {
    transition: color var(--duration-fast) var(--ease-smooth);
  }
}
</style>
