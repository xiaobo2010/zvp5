<script setup lang="ts">
import { ref, computed, watch, onMounted, onUnmounted, nextTick } from 'vue'
import { useRoute } from 'vue-router'
import { currentRoles } from '@/api/permissions'

const route = useRoute()

// Drawer menu items
const drawerItems = [
  { label: '首页', path: '/' },
  { label: '义工列表', path: '/volunteers' },
  { label: '个人主页', path: '/me' },
  { label: '关于', path: '/about' }
]
const notificationsItem = { label: '通知', path: '/notifications' }

// Admin menu items. `currentRoles()` is read through a route tick so the drawer
// updates immediately after login without a full reload.
const allAdminItems = [
  { label: '添加义工', path: '/volunteers/create' },
  { label: '服务器管理', path: '/server' },
  { label: '平台管理', path: '/manage' },
  { label: 'AI 网关', path: '/ai' }
]
const roleTick = ref(0)
watch(
  () => route.fullPath,
  () => {
    roleTick.value += 1
  }
)
const roles = computed(() => {
  roleTick.value
  return currentRoles()
})
const adminItems = computed(() =>
  allAdminItems.filter((item) => {
    if (item.path === '/server') return roles.value.includes('admin')
    if (item.path === '/manage') {
      return (
        roles.value.includes('admin') ||
        roles.value.includes('department') ||
        roles.value.includes('secretary')
      )
    }
    return roles.value.includes('admin') || roles.value.includes('department')
  })
)

const drawerOpen = ref(false)
const activePath = computed(() => route.path)
const pendingItem = ref<string | null>(null)
let navigationSequence = 0

function isNavActive(path: string): boolean {
  if (path === '/volunteers/create') return activePath.value.startsWith('/volunteers/create')
  if (path === '/volunteers' && route.name === 'volunteer-detail') return true
  if (path === '/manage' && activePath.value.startsWith('/manage')) return true
  return activePath.value === path
}

// Every drawer group shares one selection target so a single indicator can
// travel across the section dividers. `pendingItem` leads the visual while a
// lazy route/guard is settling; aria-current always follows the committed route.
const navLinkItems = computed(() => [...drawerItems, notificationsItem, ...adminItems.value])
const activeNavPath = computed(
  () => navLinkItems.value.find((item) => isNavActive(item.path))?.path ?? ''
)
const selectedPath = computed(() => {
  const pending = pendingItem.value
  if (pending && navLinkItems.value.some((item) => item.path === pending)) return pending
  return activeNavPath.value
})

// Drag-to-pull state for the collapsed edge handle.
const dragOffset = ref(0)
const dragging = ref(false)
let dragStartX = 0
let dragMoved = false
let suppressHandleClick = false

// Focus trap refs
const drawerRef = ref<HTMLElement>()
const drawerHandleRef = ref<HTMLElement>()

// One spring indicator shared by every drawer group. It is measured in drawer
// content coordinates, so it scrolls with the panel without a scroll listener.
const indicatorState = ref({ x: 0, y: 0, width: 0, height: 0, ready: false })
const indicatorCanSlide = ref(false)
const linkElements = new Map<string, HTMLElement>()
let indicatorFrame = 0
let slideFrame = 0
let pendingSnap = false
let drawerObserver: ResizeObserver | null = null

function setLinkRef(path: string, element: Element | null) {
  if (element instanceof HTMLElement) linkElements.set(path, element)
  else linkElements.delete(path)
}

function measureIndicator(snap = false) {
  const host = drawerRef.value
  const target = selectedPath.value ? linkElements.get(selectedPath.value) : undefined
  if (!host || !target) {
    if (indicatorState.value.ready) indicatorState.value = { ...indicatorState.value, ready: false }
    return
  }

  const hostRect = host.getBoundingClientRect()
  const targetRect = target.getBoundingClientRect()
  const instant = snap || !indicatorState.value.ready
  if (instant) indicatorCanSlide.value = false
  indicatorState.value = {
    x: targetRect.left - hostRect.left - host.clientLeft + host.scrollLeft,
    y: targetRect.top - hostRect.top - host.clientTop + host.scrollTop,
    width: targetRect.width,
    height: targetRect.height,
    ready: true
  }
  if (instant) enableIndicatorSlide()
}

function enableIndicatorSlide() {
  if (slideFrame) window.cancelAnimationFrame(slideFrame)
  slideFrame = window.requestAnimationFrame(() => {
    slideFrame = 0
    indicatorCanSlide.value = true
  })
}

function scheduleIndicatorMeasure(snap = false) {
  pendingSnap = pendingSnap || snap
  if (indicatorFrame) return
  indicatorFrame = window.requestAnimationFrame(() => {
    const shouldSnap = pendingSnap
    pendingSnap = false
    indicatorFrame = 0
    measureIndicator(shouldSnap)
  })
}

function toggleDrawer() {
  drawerOpen.value = !drawerOpen.value
}

function closeDrawer() {
  drawerOpen.value = false
}

function drawerWidth() {
  return drawerRef.value?.offsetWidth ?? 300
}

function startHandleDrag(event: PointerEvent) {
  if (drawerOpen.value || event.button !== 0) return
  dragStartX = event.clientX
  dragMoved = false
  dragOffset.value = 0
  dragging.value = true
  window.addEventListener('pointermove', moveHandleDrag, { passive: false })
  window.addEventListener('pointerup', endHandleDrag, { once: true })
  window.addEventListener('pointercancel', endHandleDrag, { once: true })
}

function moveHandleDrag(event: PointerEvent) {
  if (!dragging.value) return
  const delta = event.clientX - dragStartX
  if (Math.abs(delta) > 6) dragMoved = true
  dragOffset.value = Math.min(drawerWidth(), Math.max(0, delta))
  if (dragMoved) event.preventDefault()
}

function endHandleDrag() {
  window.removeEventListener('pointermove', moveHandleDrag)
  window.removeEventListener('pointerup', endHandleDrag)
  window.removeEventListener('pointercancel', endHandleDrag)
  if (!dragging.value) return

  const shouldOpen = dragMoved && dragOffset.value >= drawerWidth() * 0.28
  dragging.value = false
  dragOffset.value = 0
  if (dragMoved) {
    drawerOpen.value = shouldOpen
    suppressHandleClick = true
    window.setTimeout(() => {
      suppressHandleClick = false
    }, 0)
  }
}

function onHandleClick() {
  if (suppressHandleClick) {
    suppressHandleClick = false
    return
  }
  toggleDrawer()
}

function handleEscape(e: KeyboardEvent) {
  if (e.key === 'Escape' && drawerOpen.value) {
    e.preventDefault()
    closeDrawer()
  }
}

function handleOverlayClick(e: MouseEvent) {
  if (e.target === e.currentTarget) {
    closeDrawer()
  }
}

function drawerControls() {
  return drawerRef.value?.querySelectorAll<HTMLElement>('button, a[href]') ?? []
}

function handleDrawerKeydown(e: KeyboardEvent) {
  if (e.key !== 'Tab' || !drawerOpen.value) return

  const focusableElements = drawerControls()
  const first = focusableElements[0]
  const last = focusableElements[focusableElements.length - 1]

  if (e.shiftKey) {
    // Shift + Tab on first element: move to last
    if (document.activeElement === first) {
      e.preventDefault()
      last?.focus()
    }
  } else {
    // Tab on last element: move to first
    if (document.activeElement === last) {
      e.preventDefault()
      first?.focus()
    }
  }
}

function setScrollLock(locked: boolean) {
  if (locked) {
    const scrollbarWidth = window.innerWidth - document.documentElement.clientWidth
    document.body.style.overflow = 'hidden'
    document.body.style.paddingRight = `${scrollbarWidth}px`
  } else {
    document.body.style.overflow = ''
    document.body.style.paddingRight = ''
  }
}

onMounted(() => {
  document.addEventListener('keydown', handleEscape)
  window.addEventListener('keydown', handleDrawerKeydown)
  window.addEventListener('resize', onViewportResize)

  // Measure before the first paint so the indicator does not fly in from (0, 0).
  nextTick(() => {
    measureIndicator()
    enableIndicatorSlide()
  })

  if (typeof ResizeObserver !== 'undefined' && drawerRef.value) {
    drawerObserver = new ResizeObserver(() => scheduleIndicatorMeasure(true))
    drawerObserver.observe(drawerRef.value)
  }

  if (document.fonts?.ready) {
    void document.fonts.ready.then(() => measureIndicator(true))
  }
})

function onViewportResize() {
  scheduleIndicatorMeasure(true)
}

onUnmounted(() => {
  document.removeEventListener('keydown', handleEscape)
  window.removeEventListener('keydown', handleDrawerKeydown)
  window.removeEventListener('resize', onViewportResize)
  window.removeEventListener('pointermove', moveHandleDrag)
  window.removeEventListener('pointerup', endHandleDrag)
  window.removeEventListener('pointercancel', endHandleDrag)
  drawerObserver?.disconnect()
  drawerObserver = null
  if (indicatorFrame) window.cancelAnimationFrame(indicatorFrame)
  if (slideFrame) window.cancelAnimationFrame(slideFrame)
  indicatorFrame = 0
  slideFrame = 0
  pendingSnap = false
  setScrollLock(false)
})

watch(drawerOpen, (isOpen) => {
  setScrollLock(isOpen)

  if (isOpen) {
    nextTick(() => {
      if (drawerOpen.value) (drawerControls()[0] ?? drawerRef.value)?.focus()
      scheduleIndicatorMeasure(true)
    })
  } else {
    // Return focus to handle when drawer closes
    if (drawerHandleRef.value) {
      drawerHandleRef.value.focus()
    }
  }
})

// A committed route, pending navigation or role change can move the target;
// remeasure after Vue has patched the DOM.
watch(selectedPath, () => {
  void nextTick(scheduleIndicatorMeasure)
})

watch(
  () => adminItems.value.map((item) => item.path).join('|'),
  () => {
    void nextTick(() => scheduleIndicatorMeasure(true))
  }
)

async function handleNavClick(event: MouseEvent, path: string, navigate: (event: MouseEvent) => Promise<unknown>) {
  if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return

  // Let the indicator lead through lazy route loading; aria-current still
  // follows the committed route and falls back here if a guard rejects.
  const sequence = ++navigationSequence
  pendingItem.value = path
  try {
    await navigate(event)
  } finally {
    if (sequence === navigationSequence) pendingItem.value = null
  }
}

</script>

<template>
  <!-- Drawer overlay -->
  <Transition name="drawer-overlay">
    <div
      v-if="drawerOpen"
      class="site-nav__overlay"
      @click="handleOverlayClick"
      aria-hidden="true"
    />
  </Transition>

  <!-- Hidden left drawer -->
  <div
    id="drawer"
    ref="drawerRef"
    class="site-nav__drawer"
    :class="{ 'is-open': drawerOpen, 'is-dragging': dragging }"
    :style="{ '--drawer-drag': `${dragOffset}px` }"
    :aria-hidden="!drawerOpen"
    :inert="!drawerOpen"
    aria-labelledby="drawer-title"
    aria-modal="true"
    role="dialog"
  >
    <span
      class="site-nav__indicator"
      :class="{ 'is-ready': indicatorState.ready, 'can-slide': indicatorCanSlide }"
      aria-hidden="true"
      :style="{
        transform: `translate3d(${indicatorState.x}px, ${indicatorState.y}px, 0)`,
        width: `${indicatorState.width}px`,
        height: `${indicatorState.height}px`
      }"
    />

    <!-- Drawer header with close button -->
    <div class="site-nav__drawer-header">
      <h2 id="drawer-title" class="site-nav__drawer-title">ZVMS</h2>
      <button
        class="site-nav__drawer-close"
        type="button"
        aria-label="关闭菜单"
        @click="closeDrawer"
      >
        <svg
          width="20"
          height="20"
          viewBox="0 0 20 20"
          fill="none"
          aria-hidden="true"
        >
          <!-- X icon -->
          <path
            d="M15 5L5 15M5 5L15 15"
            stroke="currentColor"
            stroke-width="1.5"
            stroke-linecap="round"
            stroke-linejoin="round"
          />
        </svg>
      </button>
    </div>

    <nav class="site-nav__drawer-nav">
      <router-link v-for="item in drawerItems" :key="item.path" :to="item.path" custom v-slot="{ href, navigate }">
        <a
          :href="href"
          :ref="(element) => setLinkRef(item.path, element as Element | null)"
          class="site-nav__drawer-link"
          :class="{ 'is-selected': selectedPath === item.path }"
          :aria-current="isNavActive(item.path) ? 'page' : undefined"
          @click="handleNavClick($event, item.path, navigate)"
        >
          <span class="site-nav__drawer-link-text">{{ item.label }}</span>
        </a>
      </router-link>
    </nav>

    <div class="site-nav__drawer-divider" />

    <div class="site-nav__drawer-section">
      <router-link :to="notificationsItem.path" custom v-slot="{ href, navigate }">
        <a
          :href="href"
          :ref="(element) => setLinkRef(notificationsItem.path, element as Element | null)"
          class="site-nav__drawer-link"
          :class="{ 'is-selected': selectedPath === notificationsItem.path }"
          :aria-current="isNavActive(notificationsItem.path) ? 'page' : undefined"
          @click="handleNavClick($event, notificationsItem.path, navigate)"
        >
          <span class="site-nav__drawer-link-text">{{ notificationsItem.label }}</span>
        </a>
      </router-link>
    </div>

    <div v-if="adminItems.length" class="site-nav__drawer-divider" />

    <div v-if="adminItems.length" class="site-nav__drawer-section">
      <div class="site-nav__drawer-section-title">管理</div>
      <div class="site-nav__drawer-admin-wrapper">
        <router-link v-for="item in adminItems" :key="item.path" :to="item.path" custom v-slot="{ href, navigate }">
          <a
            :href="href"
            :ref="(element) => setLinkRef(item.path, element as Element | null)"
            class="site-nav__drawer-link"
            :class="{ 'is-selected': selectedPath === item.path }"
            :aria-current="isNavActive(item.path) ? 'page' : undefined"
            @click="handleNavClick($event, item.path, navigate)"
          >
            <span class="site-nav__drawer-link-text">{{ item.label }}</span>
          </a>
        </router-link>
      </div>
    </div>
  </div>

  <!-- Glass handle on left edge -->
  <button
    ref="drawerHandleRef"
    class="site-nav__handle"
    :class="{ 'is-dragging': dragging }"
    type="button"
    :aria-expanded="drawerOpen"
    aria-controls="drawer"
    :aria-label="drawerOpen ? '关闭导航菜单' : '打开导航菜单'"
    :tabindex="drawerOpen ? -1 : 0"
    @pointerdown="startHandleDrag"
    @click="onHandleClick"
  >
    <svg
      class="site-nav__handle-icon"
      width="16"
      height="16"
      viewBox="0 0 16 16"
      fill="none"
      aria-hidden="true"
    >
      <!-- Three horizontal lines (hamburger) -->
      <line x1="2" y1="4" x2="14" y2="4" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" />
      <line x1="2" y1="8" x2="14" y2="8" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" />
      <line x1="2" y1="12" x2="14" y2="12" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" />
    </svg>
  </button>
</template>

<style scoped>
/* Glass handle on left edge */
.site-nav__handle {
  --handle-time: 200ms;
  position: fixed;
  left: 0;
  top: 50%;
  transform: translateY(-50%);
  z-index: 999;
  width: 44px;
  height: 44px;
  padding: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--glass-floating-bg, linear-gradient(135deg, rgba(255, 255, 255, 0.76) 0%, rgba(248, 250, 252, 0.68) 100%));
  -webkit-backdrop-filter: var(--glass-blur, blur(10px) saturate(165%));
  backdrop-filter: var(--glass-blur, blur(10px) saturate(165%));
  border: 1px solid var(--glass-border, rgba(255, 255, 255, 0.88));
  border-left: none;
  border-radius: 0 var(--radius-lg) var(--radius-lg) 0;
  color: var(--color-text-primary);
  cursor: grab;
  touch-action: none;
  transition:
    transform var(--handle-time) var(--ease-settle),
    scale var(--handle-time) var(--ease-settle),
    color var(--handle-time) var(--ease-settle),
    box-shadow var(--handle-time) var(--ease-settle);
  box-shadow: var(--glass-floating-shadow);
  overflow: hidden;
  isolation: isolate;
}

.site-nav__handle::before {
  content: '';
  position: absolute;
  top: -1px;
  right: -1px;
  bottom: -1px;
  width: 2px;
  background: linear-gradient(
    180deg,
    rgba(255, 255, 255, 0) 0%,
    rgba(255, 255, 255, 0.6) 10%,
    rgba(255, 255, 255, 0.9) 50%,
    rgba(255, 255, 255, 0.6) 90%,
    rgba(255, 255, 255, 0) 100%
  );
  border-radius: 0 var(--radius-lg) var(--radius-lg) 0;
  pointer-events: none;
  opacity: 0.5;
}

.site-nav__handle::after {
  content: '';
  position: absolute;
  inset: 0;
  background: radial-gradient(
    circle at 80% 50%,
    rgba(255, 255, 255, 0.24) 0%,
    rgba(255, 255, 255, 0) 60%
  );
  border-radius: 0 var(--radius-lg) var(--radius-lg) 0;
  pointer-events: none;
}

.site-nav__handle:hover {
  box-shadow: var(--glass-floating-shadow), 0 0 0 1px rgb(0 113 227 / 0.12);
}

.site-nav__handle:active,
.site-nav__handle[aria-expanded='true'] {
  color: var(--accent-ink);
}

.site-nav__handle:active {
  transform: translateY(-50%);
  scale: 0.96;
}

.site-nav__handle:focus-visible {
  outline: 2px solid var(--color-brand);
  outline-offset: 2px;
}

.site-nav__handle.is-dragging {
  cursor: grabbing;
  transition: none;
}

.site-nav__handle-icon {
  position: relative;
  z-index: 1;
}

/* Drawer overlay */
.site-nav__overlay {
  position: fixed;
  inset: 0;
  z-index: 998;
  background: rgba(8, 20, 36, 0.32);
}

/* Hidden left drawer */
.site-nav__drawer {
  position: fixed;
  left: 0;
  top: 0;
  z-index: 998;
  width: min(300px, calc(100vw - 48px));
  height: 100vh;
  height: 100dvh;
  padding: var(--space-5);
  padding-bottom: max(var(--space-5), env(safe-area-inset-bottom));
  background: var(--glass-floating-bg, linear-gradient(135deg, rgba(255, 255, 255, 0.82) 0%, rgba(248, 250, 252, 0.78) 100%));
  -webkit-backdrop-filter: var(--glass-blur, blur(20px) saturate(165%));
  backdrop-filter: var(--glass-blur, blur(20px) saturate(165%));
  border-right: 1px solid var(--glass-border, rgba(255, 255, 255, 0.92));
  box-shadow: var(--glass-floating-shadow);
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  overflow-y: auto;
  overscroll-behavior: contain;
  transform: translateX(calc(-100% + var(--drawer-drag, 0px)));
  opacity: 0;
  transition:
    transform 420ms cubic-bezier(0.34, 1.12, 0.64, 1),
    opacity 420ms cubic-bezier(0.4, 0, 0.2, 1);
  outline: none;
}

.site-nav__drawer::before {
  content: '';
  position: absolute;
  top: 0;
  right: -1px;
  bottom: 0;
  width: 1.5px;
  background: linear-gradient(
    180deg,
    rgba(255, 255, 255, 0) 0%,
    rgba(255, 255, 255, 0.7) 10%,
    rgba(255, 255, 255, 0.95) 50%,
    rgba(255, 255, 255, 0.7) 90%,
    rgba(255, 255, 255, 0) 100%
  );
  pointer-events: none;
  opacity: 0.6;
}

.site-nav__drawer.is-open {
  transform: translateX(0);
  opacity: 1;
}

.site-nav__drawer.is-dragging {
  opacity: 1;
  transition: none;
}

/* Single sliding selection backdrop shared by every drawer group. It lives in
   drawer content coordinates (measured with scrollTop/scrollLeft) and animates
   only transform/width/height, so retargeting mid-flight is handled by CSS. */
.site-nav__indicator {
  position: absolute;
  top: 0;
  left: 0;
  z-index: 1;
  width: 0;
  height: 0;
  border-radius: var(--radius-md);
  background:
    radial-gradient(ellipse at 18% 0%, #ffffffd9, transparent 62%),
    var(--nav-highlight);
  border: 1px solid var(--palette-main, #cfe5f8);
  border: 1px solid color-mix(in srgb, var(--accent-ink) 32%, transparent);
  box-shadow:
    0 6px 16px var(--glass-tint, rgb(42 106 170 / 0.08)),
    0 1px 3px #1230521f,
    inset 0 1px 0 #ffffffe6;
  box-shadow:
    0 6px 16px color-mix(in srgb, var(--accent-ink) 16%, transparent),
    0 1px 3px #1230521f,
    inset 0 1px 0 #ffffffe6;
  opacity: 0;
  pointer-events: none;
  transform: translate3d(0, 0, 0);
  transition: opacity 180ms var(--ease-smooth);
}

.site-nav__indicator.is-ready {
  opacity: 1;
}

.site-nav__indicator.can-slide {
  transition:
    transform 420ms cubic-bezier(0.34, 1.28, 0.64, 1),
    width 420ms cubic-bezier(0.34, 1.28, 0.64, 1),
    height 420ms cubic-bezier(0.34, 1.28, 0.64, 1),
    opacity 180ms var(--ease-smooth);
}

.site-nav__indicator::before {
  content: '';
  position: absolute;
  inset: 0;
  border-radius: inherit;
  background: linear-gradient(135deg, #ffffff8c, transparent 58%);
  pointer-events: none;
}

/* Drawer navigation */
.site-nav__drawer-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding-bottom: var(--space-3);
  border-bottom: 1px solid var(--color-border-hairline);
}

.site-nav__drawer-title {
  font-size: 18px;
  font-weight: 700;
  color: var(--color-text-primary);
  margin: 0;
  letter-spacing: -0.5px;
}

.site-nav__drawer-close {
  position: relative;
  width: 40px;
  height: 40px;
  padding: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--glass-floating-bg);
  border: 1px solid var(--glass-border, rgba(255, 255, 255, 0.84));
  border-radius: var(--radius-md);
  color: var(--color-text-primary);
  cursor: pointer;
  overflow: hidden;
  isolation: isolate;
  flex-shrink: 0;
  box-shadow:
    0 1px 3px rgba(18, 48, 82, 0.06),
    0 0 0 0.5px rgba(255, 255, 255, 0.5),
    var(--glass-rim, inset 0 0.5px 0 rgba(255, 255, 255, 0.92));
  transition:
    transform 240ms cubic-bezier(0.34, 1.08, 0.64, 1),
    color 180ms cubic-bezier(0.4, 0, 0.2, 1),
    box-shadow 240ms cubic-bezier(0.4, 0, 0.2, 1);
}

.site-nav__drawer-close::before {
  content: '';
  position: absolute;
  top: 0;
  right: 0;
  width: 50%;
  height: 100%;
  background: linear-gradient(
    135deg,
    rgba(255, 255, 255, 0) 0%,
    rgba(255, 255, 255, 0.4) 100%
  );
  border-radius: var(--radius-md);
  pointer-events: none;
}

.site-nav__drawer-close:hover {
  color: var(--accent-ink);
  transform: scale(1.04);
  box-shadow:
    0 2px 6px rgba(18, 48, 82, 0.08),
    0 0 0 0.5px rgba(0, 113, 227, 0.2),
    var(--glass-rim, inset 0 0.5px 0 rgba(255, 255, 255, 0.96));
}

.site-nav__drawer-close:active {
  transform: scale(0.96);
  transition-duration: 120ms;
}

.site-nav__drawer-close:focus-visible {
  outline: 2px solid var(--color-brand);
  outline-offset: -2px;
}

.site-nav__drawer-nav {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}

.site-nav__drawer-link {
  --nav-press-time: 200ms;
  position: relative;
  display: flex;
  align-items: center;
  min-height: 44px;
  padding: var(--space-3) var(--space-4);
  border: 1px solid var(--glass-border);
  border-radius: var(--radius-md);
  background: linear-gradient(135deg, #ffffffb8, var(--palette-edge, #d8eee6));
  background: linear-gradient(135deg, #ffffffb8, color-mix(in srgb, var(--palette-edge, #d8eee6) 32%, #ffffff8f));
  box-shadow: var(--glass-rim), 0 1px 3px #1230520a;
  color: var(--color-text-primary);
  text-decoration: none;
  font-size: var(--font-size-body);
  font-weight: 500;
  transition: box-shadow var(--nav-press-time) var(--ease-settle),
    color var(--nav-press-time) var(--ease-settle),
    border-color var(--nav-press-time) var(--ease-settle);
}

.site-nav__drawer-link::before {
  content: '';
  position: absolute;
  inset: 0;
  z-index: 0;
  border-radius: inherit;
  background: linear-gradient(150deg, #ffffffb3, transparent 60%), var(--palette-main, #d9eafd);
  box-shadow: var(--glass-rim);
  opacity: 0;
  pointer-events: none;
  transition: opacity var(--nav-press-time) var(--ease-settle);
}

.site-nav__drawer-link-text {
  position: relative;
  z-index: 2;
  transition: transform 120ms var(--ease-settle);
}

/* Focus ring is painted above the shared indicator (the link itself is not a
   stacking context, so the ring can escape it). */
.site-nav__drawer-link::after {
  content: '';
  position: absolute;
  inset: 0;
  z-index: 3;
  border-radius: inherit;
  pointer-events: none;
}

@media (hover: hover) {
  .site-nav__drawer-link:hover::before { opacity: 0.36; }
}

.site-nav__drawer-link.is-selected {
  color: var(--accent-ink);
  border-color: var(--accent-ink);
  border-color: color-mix(in srgb, var(--color-brand) 38%, var(--glass-border));
}

.site-nav__drawer-link:active {
  border-color: var(--color-brand);
  border-color: color-mix(in srgb, var(--color-brand) 34%, var(--glass-border));
  box-shadow: inset 0 1px 3px #12305229, var(--glass-rim);
}

/* Press feedback stays off the measured box: the label nudges and the card
   shadows in, so the shared indicator never measures a shifted target. */
.site-nav__drawer-link:active .site-nav__drawer-link-text {
  transform: translateY(1px);
}

.site-nav__drawer-link:focus-visible {
  outline: none;
}

.site-nav__drawer-link:focus-visible::after {
  border: 2px solid var(--accent-ink);
}

/* Drawer sections */
.site-nav__drawer-divider {
  height: 1px;
  background: var(--color-border-hairline);
  margin: var(--space-2) 0;
}

.site-nav__drawer-section {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}

.site-nav__drawer-admin-wrapper {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}

.site-nav__drawer-section-title {
  position: relative;
  z-index: 2;
  padding: var(--space-2) var(--space-4);
  font-size: var(--font-size-caption);
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: var(--letter-spacing-wide);
  color: var(--color-text-tertiary);
}

/* Transitions */
.drawer-overlay-enter-active,
.drawer-overlay-leave-active {
  transition: opacity var(--duration-base) var(--ease-smooth);
}

.drawer-overlay-enter-from,
.drawer-overlay-leave-to {
  opacity: 0;
}

/* Reduced motion */
@media (prefers-reduced-motion: reduce) {
  .site-nav__handle {
    transition: none;
  }

  .site-nav__handle:active {
    transform: translateY(-50%);
    scale: 1;
  }

  .site-nav__drawer {
    transition: opacity 280ms cubic-bezier(0.4, 0, 0.2, 1);
  }

  .site-nav__drawer-close {
    transition: color 180ms cubic-bezier(0.4, 0, 0.2, 1);
  }

  .site-nav__drawer-close:hover,
  .site-nav__drawer-close:active {
    transform: none;
  }

  .site-nav__drawer-link,
  .site-nav__drawer-link::before,
  .site-nav__drawer-link-text {
    transition: none;
  }

  .site-nav__drawer-link:active .site-nav__drawer-link-text { transform: none; }

  .site-nav__indicator.can-slide { transition: opacity 160ms var(--ease-smooth); }
}

@media (prefers-reduced-transparency: reduce), (prefers-contrast: more), (forced-colors: active) {
  .site-nav__drawer-close,
  .site-nav__drawer-link {
    background: var(--surface-matte);
    border-color: var(--glass-border);
  }

  .site-nav__drawer-link::before { background: var(--palette-main, #d9eafd); }

  .site-nav__drawer-link.is-selected,
  .site-nav__drawer-link:active {
    border-color: currentColor;
  }
}

@media (prefers-reduced-transparency: reduce), (prefers-contrast: more) {
  .site-nav__indicator {
    background: var(--palette-main, #cfe5f8);
    background: color-mix(in srgb, white 38%, var(--palette-main, #cfe5f8));
    border-color: var(--accent-ink);
    box-shadow: none;
  }

  .site-nav__indicator::before { display: none; }
}

@media (forced-colors: active) {
  .site-nav__indicator {
    background: Canvas;
    border: 2px solid Highlight;
    box-shadow: none;
  }

  .site-nav__indicator::before { display: none; }

  .site-nav__drawer-link:focus-visible::after { border-color: Highlight; }
}
</style>
