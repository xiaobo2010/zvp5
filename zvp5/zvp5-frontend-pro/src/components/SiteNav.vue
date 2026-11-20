<script setup lang="ts">
import { ref, computed, watch, onMounted, onUnmounted, nextTick } from 'vue'
import { useRoute, useRouter } from 'vue-router'

const route = useRoute()
const router = useRouter()

// Primary nav link
const primaryLink = { label: '首页', path: '/' }

// Drawer menu items
const drawerItems = [
  { label: '义工列表', path: '/volunteers' },
  { label: '个人主页', path: '/me' },
  { label: '关于', path: '/about' }
]

// Admin menu items
const adminItems = [
  { label: '添加义工', path: '/volunteers/create' },
  { label: '服务器管理', path: '/server' },
  { label: '平台管理', path: '/manage' },
  { label: 'AI 网关', path: '/ai' }
]

const drawerOpen = ref(false)
const activePath = computed(() => route.path)
const pressingItem = ref<string | null>(null)

// Thumb state tracking per nav section
const navThumbState = ref({ left: 0, top: 0, width: 0, height: 0, ready: false })
const adminThumbState = ref({ left: 0, top: 0, width: 0, height: 0, ready: false })

// Refs for nav sections
const navRef = ref<HTMLElement | null>(null)
const adminRef = ref<HTMLElement | null>(null)

let navObserver: ResizeObserver | null = null
let adminObserver: ResizeObserver | null = null
const navLinkRefs = new Map<string, HTMLElement>()
const adminLinkRefs = new Map<string, HTMLElement>()

function isNavActive(path: string): boolean {
  if (path === '/volunteers/create') return activePath.value.startsWith('/volunteers/create')
  return activePath.value === path
}

// Drag-to-pull state for the collapsed edge handle.
const dragOffset = ref(0)
const dragging = ref(false)
let dragStartX = 0
let dragMoved = false
let suppressHandleClick = false

// Focus trap refs
const drawerRef = ref<HTMLElement>()
const drawerHandleRef = ref<HTMLElement>()
let focusableElements: HTMLElement[] = []

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

function updateFocusTrap() {
  if (!drawerRef.value) return

  focusableElements = Array.from(
    drawerRef.value.querySelectorAll(
      'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
    )
  ) as HTMLElement[]
}

function handleDrawerKeydown(e: KeyboardEvent) {
  if (e.key !== 'Tab' || !drawerOpen.value) return

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
})

onUnmounted(() => {
  document.removeEventListener('keydown', handleEscape)
  window.removeEventListener('keydown', handleDrawerKeydown)
  window.removeEventListener('pointermove', moveHandleDrag)
  window.removeEventListener('pointerup', endHandleDrag)
  window.removeEventListener('pointercancel', endHandleDrag)
  setScrollLock(false)
})

watch(drawerOpen, (isOpen) => {
  setScrollLock(isOpen)

  if (isOpen) {
    nextTick(async () => {
      updateFocusTrap()
      // Wait for fonts to load for accurate measurements
      await (document as any).fonts?.ready
      measureNav()
      measureAdmin()
      // Focus first focusable element in drawer
      const firstFocusable = focusableElements[0]
      if (firstFocusable) {
        firstFocusable.focus()
      } else if (drawerRef.value) {
        drawerRef.value.focus()
      }
    })
  } else {
    // Return focus to handle when drawer closes
    if (drawerHandleRef.value) {
      drawerHandleRef.value.focus()
    }
  }
})

watch(activePath, async () => {
  // Remeasure thumb positions when active path changes
  await nextTick()
  await (document as any).fonts?.ready
  measureNav()
  measureAdmin()
})

// Keep the drawer open when a destination is chosen. It closes only from the
// explicit close button, Escape, or the overlay.

async function setNavRef(path: string, el: unknown) {
  if (!(el instanceof HTMLElement)) {
    navLinkRefs.delete(path)
  } else {
    navLinkRefs.set(path, el)
    measureNav()
  }
}

async function setAdminRef(path: string, el: unknown) {
  if (!(el instanceof HTMLElement)) {
    adminLinkRefs.delete(path)
  } else {
    adminLinkRefs.set(path, el)
    measureAdmin()
  }
}

function measureNav() {
  if (!navRef.value) return

  // Find the currently active link in nav section
  const activeLink = Array.from(navLinkRefs.values()).find((el) => {
    const path = Array.from(navLinkRefs.entries()).find(([, v]) => v === el)?.[0]
    return path && isNavActive(path)
  })

  if (activeLink) {
    const rect = activeLink.getBoundingClientRect()
    const navRect = navRef.value.getBoundingClientRect()
    navThumbState.value = {
      left: rect.left - navRect.left,
      top: rect.top - navRect.top,
      width: rect.width,
      height: rect.height,
      ready: true
    }
  } else {
    navThumbState.value = { ...navThumbState.value, ready: false }
  }
}

function measureAdmin() {
  if (!adminRef.value) return

  // Find the currently active link in admin section
  const activeLink = Array.from(adminLinkRefs.values()).find((el) => {
    const path = Array.from(adminLinkRefs.entries()).find(([, v]) => v === el)?.[0]
    return path && isNavActive(path)
  })

  if (activeLink) {
    const rect = activeLink.getBoundingClientRect()
    const adminRect = adminRef.value.getBoundingClientRect()
    adminThumbState.value = {
      left: rect.left - adminRect.left,
      top: rect.top - adminRect.top,
      width: rect.width,
      height: rect.height,
      ready: true
    }
  } else {
    adminThumbState.value = { ...adminThumbState.value, ready: false }
  }
}

async function handleNavClick(path: string) {
  pressingItem.value = path
  // Delay navigation to allow spring animation to start
  await new Promise((resolve) => setTimeout(resolve, 220))
  router.push(path)
  // Keep drawer open after navigation
  pressingItem.value = null
}

async function handleAdminClick(path: string) {
  pressingItem.value = path
  // Delay navigation to allow spring animation to start
  await new Promise((resolve) => setTimeout(resolve, 220))
  router.push(path)
  // Keep drawer open after navigation
  pressingItem.value = null
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

    <nav class="site-nav__drawer-nav" ref="navRef">
      <span
        class="site-nav__drawer-thumb"
        :style="{
          transform: `translate3d(${navThumbState.left}px, ${navThumbState.top}px, 0)`,
          width: `${navThumbState.width}px`,
          height: `${navThumbState.height}px`,
          opacity: navThumbState.ready ? 1 : 0
        }"
      />
      <router-link
        :to="primaryLink.path"
        class="site-nav__drawer-link"
        :class="{ 'is-active': primaryLink.path === activePath, 'is-pressing': pressingItem === primaryLink.path }"
        :aria-current="primaryLink.path === activePath ? 'page' : undefined"
        :ref="(el) => setNavRef(primaryLink.path, el)"
        @click.prevent="handleNavClick(primaryLink.path)"
      >
        <span class="site-nav__drawer-link-text">{{ primaryLink.label }}</span>
      </router-link>

      <router-link
        v-for="item in drawerItems"
        :key="item.path"
        :to="item.path"
        class="site-nav__drawer-link"
        :class="{ 'is-active': isNavActive(item.path), 'is-pressing': pressingItem === item.path }"
        :aria-current="isNavActive(item.path) ? 'page' : undefined"
        :ref="(el) => setNavRef(item.path, el)"
        @click.prevent="handleNavClick(item.path)"
      >
        <span class="site-nav__drawer-link-text">{{ item.label }}</span>
      </router-link>
    </nav>

    <div class="site-nav__drawer-divider" />

    <div class="site-nav__drawer-section">
      <router-link
        class="site-nav__drawer-action"
        :class="{ 'is-active': activePath === '/notifications' }"
        to="/notifications"
      >
        <span class="site-nav__drawer-link-text">通知</span>
      </router-link>
    </div>

    <div class="site-nav__drawer-divider" />

    <div class="site-nav__drawer-section">
      <div class="site-nav__drawer-section-title">管理</div>
      <div class="site-nav__drawer-admin-wrapper" ref="adminRef">
        <span
          class="site-nav__drawer-thumb"
          :style="{
            transform: `translate3d(${adminThumbState.left}px, ${adminThumbState.top}px, 0)`,
            width: `${adminThumbState.width}px`,
            height: `${adminThumbState.height}px`,
            opacity: adminThumbState.ready ? 1 : 0
          }"
        />
        <router-link
          v-for="item in adminItems"
          :key="item.path"
          :to="item.path"
          class="site-nav__drawer-link"
          :class="{ 'is-active': isNavActive(item.path), 'is-pressing': pressingItem === item.path }"
          :aria-current="isNavActive(item.path) ? 'page' : undefined"
          :ref="(el) => setAdminRef(item.path, el)"
          @click.prevent="handleAdminClick(item.path)"
        >
          <span class="site-nav__drawer-link-text">{{ item.label }}</span>
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
  background: linear-gradient(
    135deg,
    rgba(255, 255, 255, 0.8) 0%,
    rgba(232, 242, 252, 0.6) 100%
  );
  -webkit-backdrop-filter: blur(12px) saturate(180%);
  backdrop-filter: blur(12px) saturate(180%);
  border: 1px solid rgba(255, 255, 255, 0.86);
  border-left: none;
  border-radius: 0 var(--radius-lg) var(--radius-lg) 0;
  color: var(--color-text-primary);
  cursor: grab;
  touch-action: none;
  transition:
    transform var(--duration-fast) var(--ease-spring),
    color var(--duration-fast) var(--ease-smooth),
    box-shadow var(--duration-fast) var(--ease-smooth);
  box-shadow: var(--glass-shadow);
  overflow: hidden;
  isolation: isolate;
}

.site-nav__handle::before {
  content: '';
  position: absolute;
  inset: 0;
  background: var(--glass-highlight);
  border-radius: 0 var(--radius-lg) var(--radius-lg) 0;
  pointer-events: none;
}

.site-nav__handle:hover {
  transform: translateY(-50%) scale(1.05);
  color: var(--color-brand);
}

.site-nav__handle:active {
  transform: translateY(-50%) scale(0.95);
}

.site-nav__handle:focus-visible {
  outline: 2px solid var(--color-brand);
  outline-offset: 2px;
}

.site-nav__handle.is-dragging {
  cursor: grabbing;
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
  background: rgba(8, 20, 36, 0.24);
  /* The drawer already owns the expensive backdrop blur; a second full-screen
     blur here is pure GPU cost for no visible gain. */
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
  background: var(--glass-bg);
  -webkit-backdrop-filter: var(--glass-blur);
  backdrop-filter: var(--glass-blur);
  border-right: 1px solid var(--glass-border);
  box-shadow: var(--glass-shadow);
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  overflow-y: auto;
  overscroll-behavior: contain;
  transform: translateX(calc(-100% + var(--drawer-drag, 0px)));
  opacity: 0;
  transition: transform var(--duration-base) var(--ease-smooth),
    opacity var(--duration-base) var(--ease-smooth);
  outline: none;
}

.site-nav__drawer.is-open {
  transform: translateX(0);
  opacity: 1;
}

.site-nav__drawer.is-dragging {
  opacity: 1;
  transition: none;
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
  background: linear-gradient(
    135deg,
    rgba(255, 255, 255, 0.72) 0%,
    rgba(232, 242, 252, 0.48) 100%
  );
  -webkit-backdrop-filter: blur(12px) saturate(180%);
  backdrop-filter: blur(12px) saturate(180%);
  border: 1px solid rgba(255, 255, 255, 0.82);
  border-radius: var(--radius-md);
  color: var(--color-text-primary);
  cursor: pointer;
  overflow: hidden;
  isolation: isolate;
  flex-shrink: 0;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.92),
    inset 0 -1px 0 rgba(0, 0, 0, 0.06),
    0 2px 8px rgba(18, 48, 82, 0.08);
  transition:
    transform var(--duration-fast) var(--ease-spring),
    color var(--duration-fast) var(--ease-smooth),
    box-shadow var(--duration-fast) var(--ease-smooth);
}

.site-nav__drawer-close::before {
  content: '';
  position: absolute;
  inset: 0;
  background: var(--glass-highlight);
  border-radius: var(--radius-md);
  opacity: 0.6;
  pointer-events: none;
  z-index: -1;
}

.site-nav__drawer-close:hover {
  color: var(--color-brand);
  transform: scale(1.05);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.98),
    inset 0 -1px 0 rgba(0, 0, 0, 0.08),
    0 4px 12px rgba(18, 48, 82, 0.12);
}

.site-nav__drawer-close:active {
  transform: scale(0.95);
}

.site-nav__drawer-close:focus-visible {
  outline: 2px solid var(--color-brand);
  outline-offset: -2px;
}

/* Drawer thumb */
.site-nav__drawer-thumb {
  position: absolute;
  top: 0;
  left: 0;
  z-index: 0;
  height: 44px;
  border-radius: var(--radius-md);
  background: linear-gradient(
    135deg,
    rgba(255, 255, 255, 0.5) 0%,
    rgba(232, 242, 252, 0.35) 100%
  );
  -webkit-backdrop-filter: blur(12px) saturate(180%);
  backdrop-filter: blur(12px) saturate(180%);
  border: 1px solid rgba(255, 255, 255, 0.86);
  transition:
    transform cubic-bezier(0.34, 1.46, 0.64, 1) 420ms,
    width cubic-bezier(0.34, 1.46, 0.64, 1) 420ms;
  pointer-events: none;
}

.site-nav__drawer-thumb::before {
  content: '';
  position: absolute;
  inset: 0;
  background: var(--glass-sheen);
  border-radius: inherit;
  opacity: 0.22;
  transform: translateX(-42%);
  pointer-events: none;
}

/* Drawer navigation */
.site-nav__drawer-nav {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}

.site-nav__drawer-link {
  padding: var(--space-3) var(--space-4);
  color: var(--color-text-primary);
  text-decoration: none;
  font-size: var(--font-size-body);
  font-weight: 500;
  position: relative;
  z-index: 1;
  border-radius: var(--radius-md);
  background: linear-gradient(
    135deg,
    rgba(255, 255, 255, 0.78) 0%,
    rgba(232, 242, 252, 0.58) 100%
  );
  -webkit-backdrop-filter: blur(12px) saturate(180%);
  backdrop-filter: blur(12px) saturate(180%);
  border: 1px solid rgba(255, 255, 255, 0.84);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.94),
    inset 0 -1px 0 rgba(0, 0, 0, 0.04),
    0 2px 8px rgba(18, 48, 82, 0.05);
  overflow: hidden;
  isolation: isolate;
  transition:
    transform var(--duration-fast) var(--ease-spring),
    background-color var(--duration-fast) var(--ease-smooth),
    border-color var(--duration-fast) var(--ease-smooth),
    box-shadow var(--duration-fast) var(--ease-smooth);
  display: flex;
  align-items: center;
  min-height: 44px;
  cursor: pointer;
}

.site-nav__drawer-link-text {
  position: relative;
  z-index: 1;
}

.site-nav__drawer-link::before,
.site-nav__drawer-action::before {
  content: '';
  position: absolute;
  inset: 0;
  background: var(--glass-sheen);
  border-radius: inherit;
  opacity: 0.18;
  transform: translateX(-42%);
  pointer-events: none;
  transition:
    opacity var(--duration-base) var(--ease-smooth),
    transform var(--duration-base) var(--ease-smooth);
  z-index: -1;
}

.site-nav__drawer-link:hover::before,
.site-nav__drawer-action:hover::before {
  opacity: 0.75;
  transform: translateX(100%);
}

.site-nav__drawer-link:hover {
  border-color: rgba(255, 255, 255, 0.92);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.98),
    inset 0 -1px 0 rgba(0, 0, 0, 0.06),
    0 4px 14px rgba(18, 48, 82, 0.1);
  transform: translateX(4px);
}

.site-nav__drawer-link.is-active,
.site-nav__drawer-action.is-active {
  animation: drawer-active-in var(--duration-base) var(--ease-spring);
}

@keyframes drawer-active-in {
  from {
    opacity: 0.55;
    transform: translateX(-5px) scale(0.985);
  }
  to {
    opacity: 1;
    transform: translateX(0) scale(1);
  }
}

.site-nav__drawer-link.is-active {
  background: rgba(255, 255, 255, 0.1);
  border-color: rgba(255, 255, 255, 0.92);
  color: var(--color-brand);
  font-weight: 600;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.98),
    inset 0 -1px 0 rgba(0, 0, 0, 0.05),
    0 4px 14px rgba(0, 113, 227, 0.12);
  z-index: 1;
  position: relative;
}

.site-nav__drawer-link.is-active::before {
  display: none;
}

.site-nav__drawer-link:focus-visible {
  outline: 2px solid var(--color-brand);
  outline-offset: -2px;
}

.site-nav__drawer-link.is-pressing {
  transform: scale(0.96);
  transition: transform 180ms cubic-bezier(0.34, 1.46, 0.64, 1);
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
  padding: var(--space-2) var(--space-4);
  font-size: var(--font-size-caption);
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: var(--letter-spacing-wide);
  color: var(--color-text-tertiary);
}

.site-nav__drawer-action {
  position: relative;
  padding: var(--space-3) var(--space-4);
  color: var(--color-text-primary);
  font-size: var(--font-size-body);
  font-weight: 500;
  border-radius: var(--radius-md);
  background: linear-gradient(
    135deg,
    rgba(255, 255, 255, 0.78) 0%,
    rgba(232, 242, 252, 0.58) 100%
  );
  -webkit-backdrop-filter: blur(12px) saturate(180%);
  backdrop-filter: blur(12px) saturate(180%);
  border: 1px solid rgba(255, 255, 255, 0.84);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.94),
    inset 0 -1px 0 rgba(0, 0, 0, 0.04),
    0 2px 8px rgba(18, 48, 82, 0.05);
  text-align: left;
  cursor: pointer;
  overflow: hidden;
  isolation: isolate;
  transition:
    transform var(--duration-fast) var(--ease-spring),
    border-color var(--duration-fast) var(--ease-smooth),
    box-shadow var(--duration-fast) var(--ease-smooth);
  min-height: 44px;
  display: flex;
  align-items: center;
  text-decoration: none;
}

.site-nav__drawer-action:hover {
  border-color: rgba(255, 255, 255, 0.92);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.98),
    inset 0 -1px 0 rgba(0, 0, 0, 0.06),
    0 4px 14px rgba(18, 48, 82, 0.1);
  transform: translateX(4px);
}

.site-nav__drawer-action.is-active {
  background: linear-gradient(
    135deg,
    rgba(0, 113, 227, 0.2) 0%,
    rgba(255, 255, 255, 0.66) 100%
  );
  border-color: rgba(255, 255, 255, 0.92);
  color: var(--color-brand);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.98),
    0 4px 14px rgba(0, 113, 227, 0.12);
}

.site-nav__drawer-action.is-active::before {
  content: '';
  position: absolute;
  left: 0;
  top: 50%;
  transform: translateY(-50%);
  width: 3px;
  height: 60%;
  background: var(--color-brand);
  border-radius: 0 var(--radius-xs) var(--radius-xs) 0;
  opacity: 1;
  transition:
    height var(--duration-base) var(--ease-spring),
    opacity var(--duration-base) var(--ease-smooth);
  z-index: 2;
}

.site-nav__drawer-action:active {
  transform: translateX(2px);
}

.site-nav__drawer-action:focus-visible {
  outline: 2px solid var(--color-brand);
  outline-offset: -2px;
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

  .site-nav__drawer {
    transition: none;
  }

  .site-nav__drawer-close,
  .site-nav__drawer-link,
  .site-nav__drawer-action {
    transition: none;
  }

  .site-nav__drawer-link::before,
  .site-nav__drawer-action::before {
    display: none;
  }

  .site-nav__drawer-link.is-active,
  .site-nav__drawer-action.is-active {
    animation: none;
  }

  .site-nav__handle:hover,
  .site-nav__drawer-link:hover,
  .site-nav__drawer-action:hover {
    transform: none;
  }

  .site-nav__handle:active,
  .site-nav__drawer-action:active {
    transform: none;
  }
}

/* Support for browsers without backdrop-filter */
@supports not (backdrop-filter: blur(1px)) {
  .site-nav__handle {
    background: rgba(255, 255, 255, 0.92);
  }

  .site-nav__drawer {
    background: rgba(255, 255, 255, 0.92);
  }

  .site-nav__drawer-close,
  .site-nav__drawer-link,
  .site-nav__drawer-action {
    background: rgba(255, 255, 255, 0.9);
  }
}
</style>
