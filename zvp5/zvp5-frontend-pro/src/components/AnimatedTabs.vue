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
const indicator = ref<HTMLElement | null>(null)
const tabElements = new Map<string, HTMLElement>()
const indicatorState = ref({ left: 0, width: 0, ready: false })
const isDragging = ref(false)
const dragOffset = ref(0)

let observer: ResizeObserver | null = null
let dragStartX = 0
// Sticky until the next pointerdown/keydown: the browser may dispatch the
// retargeted click after pointerup, possibly in a later task.
let suppressClick = false

function setTabRef(value: string, element: Element | null) {
  if (element instanceof HTMLElement) tabElements.set(value, element)
  else tabElements.delete(value)
}

function measure() {
  const active = tabElements.get(props.modelValue)
  const host = track.value
  if (!active || !host) {
    indicatorState.value.ready = false
    return
  }

  // Use getBoundingClientRect for robust measurement
  const activeRect = active.getBoundingClientRect()
  const hostRect = host.getBoundingClientRect()
  // The track itself scrolls: add scrollLeft so the thumb lives in content
  // coordinates instead of being offset twice when the strip is scrolled.
  const relativeLeft = activeRect.left - hostRect.left + host.scrollLeft

  indicatorState.value = {
    left: relativeLeft,
    width: activeRect.width,
    ready: true
  }
}

function select(value: string) {
  if (value !== props.modelValue) {
    dragOffset.value = 0
    emit('update:modelValue', value)
  }
}

function onKeydown(event: KeyboardEvent) {
  if (event.key === 'Enter' || event.key === ' ' || event.key === 'Spacebar') {
    // Keyboard activation fires a click without a preceding pointerdown, so a
    // stale pointer-click suppression must not swallow it.
    suppressClick = false
    return
  }
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

function onPointerDown(event: PointerEvent) {
  if (event.button !== 0) return
  const element = event.currentTarget as HTMLElement | null
  if (!element) return
  dragStartX = event.clientX
  suppressClick = false
  isDragging.value = true
  try {
    element.setPointerCapture?.(event.pointerId)
  } catch {
    // Capture is best-effort; pointerup still works without it.
  }
}

function onPointerMove(event: PointerEvent) {
  if (!isDragging.value) return
  const delta = event.clientX - dragStartX
  // Apply damping factor of 0.85
  dragOffset.value = delta * 0.85
}

function releaseCapture(element: HTMLElement | null, pointerId: number) {
  if (!element) return
  try {
    if (typeof element.hasPointerCapture === 'function' && element.hasPointerCapture(pointerId)) {
      element.releasePointerCapture(pointerId)
    } else {
      element.releasePointerCapture?.(pointerId)
    }
  } catch {
    // Silently ignore if pointer was already released
  }
}

/**
 * Resolve the tab sitting under the given viewport coordinates.
 *
 * Pointer capture retargets the native `click` to the capturing `<nav>`, so a
 * plain tap never reaches the button's `@click`. Selection therefore has to be
 * resolved from the pointer position instead of relying on the click event.
 */
function tabValueAtPoint(x: number, y: number): string {
  if (typeof document === 'undefined' || typeof document.elementFromPoint !== 'function') return ''
  const hit = document.elementFromPoint(x, y)
  const tab = hit instanceof Element ? hit.closest<HTMLElement>('.animated-tabs__tab') : null
  if (!tab) return ''
  // Guard against other tab strips rendered on the same page.
  if (track.value && !track.value.contains(tab)) return ''
  return tab.dataset.value ?? ''
}

function resetDrag() {
  isDragging.value = false
  dragOffset.value = 0
}

function onPointerUp(event: PointerEvent) {
  if (!isDragging.value) return
  const element = event.currentTarget as HTMLElement | null
  releaseCapture(element, event.pointerId)

  // Select the tab under the pointer whether or not the drag moved: a normal
  // click must always switch tabs even though capture ate the native click.
  const value = tabValueAtPoint(event.clientX, event.clientY)
  if (value) {
    // Suppress the follow-up click: capture can retarget it to <nav> or back to
    // the previously-active tab, which would undo the selection above.
    suppressClick = true
    select(value)
  }

  resetDrag()
}

function onPointerCancel(event: PointerEvent) {
  if (!isDragging.value) return
  releaseCapture(event.currentTarget as HTMLElement | null, event.pointerId)
  resetDrag()
}

function onPointerLeave(event: PointerEvent) {
  if (!isDragging.value) return
  const element = event.currentTarget as HTMLElement | null
  // With capture active the pointer is still logically inside; leave only
  // matters as a safety net when capture is unavailable.
  if (element?.hasPointerCapture?.(event.pointerId)) return
  resetDrag()
}

function onTabClick(value: string) {
  if (suppressClick) {
    suppressClick = false
    return
  }
  select(value)
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
  // Wait for fonts to load for accurate measurement
  if (document.fonts && typeof document.fonts.ready !== 'undefined') {
    void document.fonts.ready.then(() => measure())
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
    :class="{ 'is-dragging': isDragging }"
    role="tablist"
    :aria-label="ariaLabel"
    @keydown="onKeydown"
    @pointerdown="onPointerDown"
    @pointermove="onPointerMove"
    @pointerup="onPointerUp"
    @pointercancel="onPointerCancel"
    @pointerleave="onPointerLeave"
  >
    <span
      class="animated-tabs__indicator"
      :class="{ 'is-ready': indicatorState.ready, 'is-dragging': isDragging }"
      :style="{
        width: `${indicatorState.width}px`,
        transform: `translate3d(${indicatorState.left + dragOffset}px, 0, 0)`
      }"
      aria-hidden="true"
    />
    <button
      v-for="item in items"
      :key="item.value"
      :ref="(element) => setTabRef(item.value, element as Element | null)"
      :data-value="item.value"
      type="button"
      role="tab"
      class="animated-tabs__tab"
      :class="{ 'is-active': item.value === modelValue }"
      :aria-selected="item.value === modelValue"
      :tabindex="item.value === modelValue ? 0 : -1"
      :title="item.hint"
      @click="onTabClick(item.value)"
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
  background: var(--glass-floating-bg, linear-gradient(135deg, rgba(255, 255, 255, 0.68) 0%, rgba(248, 250, 252, 0.52) 100%));
  -webkit-backdrop-filter: var(--glass-blur, blur(12px) saturate(165%));
  backdrop-filter: var(--glass-blur, blur(12px) saturate(165%));
  border: 1px solid var(--glass-border, rgba(255, 255, 255, 0.82));
  border-radius: var(--radius-pill);
  box-shadow: var(--glass-floating-shadow);
  isolation: isolate;
}

.animated-tabs::before {
  content: '';
  position: absolute;
  top: 0;
  left: 50%;
  transform: translateX(-50%);
  width: 80%;
  height: 1px;
  background: linear-gradient(
    90deg,
    rgba(255, 255, 255, 0) 0%,
    rgba(255, 255, 255, 0.7) 50%,
    rgba(255, 255, 255, 0) 100%
  );
  pointer-events: none;
  opacity: 0.6;
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
  background: linear-gradient(135deg, rgba(255, 255, 255, 0.52) 0%, rgba(240, 248, 255, 0.38) 100%);
  border: 1px solid var(--glass-border, rgba(255, 255, 255, 0.72));
  box-shadow:
    0 2px 6px rgba(0, 113, 227, 0.1),
    0 0 0 0.5px rgba(255, 255, 255, 0.4),
    var(--glass-rim, inset 0 0.5px 0 rgba(255, 255, 255, 0.92));
  opacity: 0;
  pointer-events: none;
  transition:
    transform 440ms cubic-bezier(0.34, 1.28, 0.64, 1),
    width 440ms cubic-bezier(0.34, 1.28, 0.64, 1),
    opacity 200ms cubic-bezier(0.4, 0, 0.2, 1);
}

.animated-tabs__indicator::before {
  content: '';
  position: absolute;
  top: 0;
  right: 0;
  width: 60%;
  height: 100%;
  background: linear-gradient(
    135deg,
    rgba(255, 255, 255, 0) 0%,
    rgba(255, 255, 255, 0.5) 100%
  );
  border-radius: inherit;
  pointer-events: none;
}

.animated-tabs__indicator.is-dragging {
  transition:
    transform 260ms cubic-bezier(0.4, 0, 0.2, 1),
    width 260ms cubic-bezier(0.4, 0, 0.2, 1),
    opacity 200ms cubic-bezier(0.4, 0, 0.2, 1);
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
    color 220ms cubic-bezier(0.4, 0, 0.2, 1),
    transform 260ms cubic-bezier(0.34, 1.08, 0.64, 1);
}

.animated-tabs__tab:hover {
  color: var(--color-text-primary);
}

.animated-tabs__tab:active {
  transform: scale(0.97);
  transition-duration: 140ms;
}

.animated-tabs__tab:focus-visible {
  outline: 2px solid var(--color-brand);
  outline-offset: 1px;
}

.animated-tabs__tab.is-active {
  color: var(--accent-ink);
}

@media (prefers-reduced-motion: reduce) {
  .animated-tabs__indicator {
    transition: opacity 200ms cubic-bezier(0.4, 0, 0.2, 1);
  }

  .animated-tabs__tab {
    transition: color 220ms cubic-bezier(0.4, 0, 0.2, 1);
  }

  .animated-tabs__tab:active {
    transform: none;
  }
}

@media (prefers-reduced-transparency: reduce), (prefers-contrast: more) {
  .animated-tabs__indicator {
    background: #e3eef8;
    border-color: var(--accent-ink);
  }

  .animated-tabs__indicator::before {
    display: none;
  }
}
</style>
