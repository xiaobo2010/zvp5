<script setup lang="ts">
import { ref, computed, watch, onMounted, onUnmounted, nextTick } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { currentRoles } from '@/api/permissions'

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
    return roles.value.includes('admin') || roles.value.includes('department')
  })
)

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

function setNavRef(path: string, el: unknown) {
  if (!(el instanceof HTMLElement)) {
    navLinkRefs.delete(path)
  } else {
    navLinkRefs.set(path, el)
  }
}

function setAdminRef(path: string, el: unknown) {
  if (!(el instanceof HTMLElement)) {
    adminLinkRefs.delete(path)
  } else {
    adminLinkRefs.set(path, el)
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

    <div v-if="adminItems.length" class="site-nav__drawer-divider" />

    <div v-if="adminItems.length" class="site-nav__drawer-section">
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
    transform 280ms cubic-bezier(0.34, 1.08, 0.64, 1),
    color 180ms cubic-bezier(0.4, 0, 0.2, 1),
    box-shadow 280ms cubic-bezier(0.4, 0, 0.2, 1);
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
  transform: translateY(-50%) scale(1.04);
  color: var(--color-brand);
  box-shadow: var(--glass-floating-shadow), 0 0 0 1px rgb(0 113 227 / 0.12);
}

.site-nav__handle:active {
  transform: translateY(-50%) scale(0.96);
  transition-duration: 120ms;
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
  background: linear-gradient(135deg, rgba(255, 255, 255, 0.68) 0%, rgba(248, 250, 252, 0.52) 100%);
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
  color: var(--color-brand);
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

/* Drawer thumb */
.site-nav__drawer-thumb {
  position: absolute;
  top: 0;
  left: 0;
  z-index: 0;
  height: 44px;
  border-radius: var(--radius-md);
  background: linear-gradient(135deg, rgba(255, 255, 255, 0.5) 0%, rgba(240, 248, 255, 0.38) 100%);
  border: 1px solid var(--glass-border, rgba(255, 255, 255, 0.88));
  box-shadow:
    0 2px 6px rgba(0, 113, 227, 0.08),
    0 0 0 0.5px rgba(255, 255, 255, 0.4),
    var(--glass-rim, inset 0 0.5px 0 rgba(255, 255, 255, 0.92));
  transition:
    transform 460ms cubic-bezier(0.34, 1.32, 0.64, 1),
    width 460ms cubic-bezier(0.34, 1.32, 0.64, 1);
  pointer-events: none;
}

.site-nav__drawer-thumb::before {
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
  background: linear-gradient(135deg, rgba(255, 255, 255, 0.72) 0%, rgba(248, 250, 252, 0.56) 100%);
  border: 1px solid var(--glass-border, rgba(255, 255, 255, 0.86));
  box-shadow:
    0 1px 3px rgba(18, 48, 82, 0.04),
    0 0 0 0.5px rgba(255, 255, 255, 0.5),
    var(--glass-rim, inset 0 0.5px 0 rgba(255, 255, 255, 0.92));
  overflow: hidden;
  isolation: isolate;
  transition:
    transform 280ms cubic-bezier(0.34, 1.08, 0.64, 1),
    background-color 180ms cubic-bezier(0.4, 0, 0.2, 1),
    border-color 180ms cubic-bezier(0.4, 0, 0.2, 1),
    box-shadow 280ms cubic-bezier(0.4, 0, 0.2, 1);
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
  top: 0;
  left: -100%;
  width: 100%;
  height: 100%;
  background: var(--glass-sheen);
  transform: translateX(0);
  transition:
    transform 680ms cubic-bezier(0.4, 0, 0.2, 1),
    opacity 180ms cubic-bezier(0.4, 0, 0.2, 1);
  pointer-events: none;
  opacity: 0;
}

.site-nav__drawer-link:hover::before,
.site-nav__drawer-action:hover::before {
  opacity: 1;
  transform: translateX(200%);
}

.site-nav__drawer-link:hover {
  border-color: rgba(255, 255, 255, 0.92);
  box-shadow:
    0 2px 6px rgba(18, 48, 82, 0.06),
    0 0 0 0.5px rgba(255, 255, 255, 0.6),
    var(--glass-rim, inset 0 0.5px 0 rgba(255, 255, 255, 0.96));
  transform: translateX(3px);
}

.site-nav__drawer-link.is-active,
.site-nav__drawer-action.is-active {
  animation: drawer-active-in 420ms cubic-bezier(0.34, 1.08, 0.64, 1);
}

@keyframes drawer-active-in {
  from {
    opacity: 0.6;
    transform: translateX(-4px) scale(0.988);
  }
  to {
    opacity: 1;
    transform: translateX(0) scale(1);
  }
}

.site-nav__drawer-link.is-active {
  background: linear-gradient(135deg, rgba(255, 255, 255, 0.88) 0%, rgba(240, 248, 255, 0.72) 100%);
  border-color: rgba(0, 113, 227, 0.24);
  color: var(--color-brand);
  font-weight: 600;
  box-shadow:
    0 2px 8px rgba(0, 113, 227, 0.12),
    0 0 0 0.5px rgba(0, 113, 227, 0.15),
    var(--glass-rim, inset 0 0.5px 0 rgba(255, 255, 255, 0.96));
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
  transform: scale(0.97);
  transition: transform 200ms cubic-bezier(0.34, 1.32, 0.64, 1);
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
  background: linear-gradient(135deg, rgba(255, 255, 255, 0.72) 0%, rgba(248, 250, 252, 0.56) 100%);
  border: 1px solid var(--glass-border, rgba(255, 255, 255, 0.86));
  box-shadow:
    0 1px 3px rgba(18, 48, 82, 0.04),
    0 0 0 0.5px rgba(255, 255, 255, 0.5),
    var(--glass-rim, inset 0 0.5px 0 rgba(255, 255, 255, 0.92));
  text-align: left;
  cursor: pointer;
  overflow: hidden;
  isolation: isolate;
  transition:
    transform 280ms cubic-bezier(0.34, 1.08, 0.64, 1),
    border-color 180ms cubic-bezier(0.4, 0, 0.2, 1),
    box-shadow 280ms cubic-bezier(0.4, 0, 0.2, 1);
  min-height: 44px;
  display: flex;
  align-items: center;
  text-decoration: none;
}

.site-nav__drawer-action:hover {
  border-color: rgba(255, 255, 255, 0.92);
  box-shadow:
    0 2px 6px rgba(18, 48, 82, 0.06),
    0 0 0 0.5px rgba(255, 255, 255, 0.6),
    var(--glass-rim, inset 0 0.5px 0 rgba(255, 255, 255, 0.96));
  transform: translateX(3px);
}

.site-nav__drawer-action.is-active {
  background: linear-gradient(135deg, rgba(255, 255, 255, 0.88) 0%, rgba(240, 248, 255, 0.72) 100%);
  border-color: rgba(0, 113, 227, 0.24);
  color: var(--color-brand);
  box-shadow:
    0 2px 8px rgba(0, 113, 227, 0.12),
    0 0 0 0.5px rgba(0, 113, 227, 0.15),
    var(--glass-rim, inset 0 0.5px 0 rgba(255, 255, 255, 0.96));
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
    height 320ms cubic-bezier(0.34, 1.08, 0.64, 1),
    opacity 180ms cubic-bezier(0.4, 0, 0.2, 1);
  z-index: 2;
}

.site-nav__drawer-action:active {
  transform: translateX(2px);
  transition-duration: 120ms;
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
    transition: color 180ms cubic-bezier(0.4, 0, 0.2, 1);
  }

  .site-nav__handle:hover {
    transform: translateY(-50%);
  }

  .site-nav__handle:active {
    transform: translateY(-50%);
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

  .site-nav__drawer-link {
    transition:
      background-color 180ms cubic-bezier(0.4, 0, 0.2, 1),
      border-color 180ms cubic-bezier(0.4, 0, 0.2, 1);
  }

  .site-nav__drawer-action {
    transition:
      border-color 180ms cubic-bezier(0.4, 0, 0.2, 1);
  }

  .site-nav__drawer-link::before,
  .site-nav__drawer-action::before {
    display: none;
  }

  .site-nav__drawer-link.is-active,
  .site-nav__drawer-action.is-active {
    animation: none;
  }

  .site-nav__drawer-link:hover,
  .site-nav__drawer-action:hover {
    transform: none;
  }

  .site-nav__drawer-action:active {
    transform: none;
  }

  .site-nav__drawer-thumb {
    transition: none;
  }
}

@media (prefers-reduced-transparency: reduce), (prefers-contrast: more) {
  .site-nav__drawer-close,
  .site-nav__drawer-link,
  .site-nav__drawer-action {
    background: var(--surface-matte);
    border-color: var(--glass-border);
  }

  .site-nav__drawer-thumb,
  .site-nav__drawer-link.is-active,
  .site-nav__drawer-action.is-active {
    background: #e3eef8;
    border-color: var(--accent-ink);
  }
}
</style>
