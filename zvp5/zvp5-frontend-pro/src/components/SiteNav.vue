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
const pendingItem = ref<string | null>(null)
let navigationSequence = 0

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
    nextTick(() => {
      if (drawerOpen.value) (drawerControls()[0] ?? drawerRef.value)?.focus()
    })
  } else {
    // Return focus to handle when drawer closes
    if (drawerHandleRef.value) {
      drawerHandleRef.value.focus()
    }
  }
})

async function handleNavClick(event: MouseEvent, path: string, navigate: (event: MouseEvent) => Promise<unknown>) {
  if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return

  // Keep the press tint through lazy route loading. aria-current still follows the committed route.
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
          class="site-nav__drawer-link"
          :class="{ 'is-selected': pendingItem ? pendingItem === item.path : isNavActive(item.path) }"
          :aria-current="isNavActive(item.path) ? 'page' : undefined"
          @click="handleNavClick($event, item.path, navigate)"
        >{{ item.label }}</a>
      </router-link>
    </nav>

    <div class="site-nav__drawer-divider" />

    <div class="site-nav__drawer-section">
      <router-link to="/notifications" custom v-slot="{ href, navigate }">
        <a
          :href="href"
          class="site-nav__drawer-link"
          :class="{ 'is-selected': pendingItem ? pendingItem === '/notifications' : isNavActive('/notifications') }"
          :aria-current="isNavActive('/notifications') ? 'page' : undefined"
          @click="handleNavClick($event, '/notifications', navigate)"
        >通知</a>
      </router-link>
    </div>

    <div v-if="adminItems.length" class="site-nav__drawer-divider" />

    <div v-if="adminItems.length" class="site-nav__drawer-section">
      <div class="site-nav__drawer-section-title">管理</div>
      <div class="site-nav__drawer-admin-wrapper">
        <router-link v-for="item in adminItems" :key="item.path" :to="item.path" custom v-slot="{ href, navigate }">
          <a
            :href="href"
            class="site-nav__drawer-link"
            :class="{ 'is-selected': pendingItem ? pendingItem === item.path : isNavActive(item.path) }"
            :aria-current="isNavActive(item.path) ? 'page' : undefined"
            @click="handleNavClick($event, item.path, navigate)"
          >{{ item.label }}</a>
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
  transform: translateY(-50%) scale(0.96);
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

.site-nav__drawer-nav {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}

.site-nav__drawer-link {
  --nav-press-time: 200ms;
  position: relative;
  isolation: isolate;
  display: flex;
  align-items: center;
  min-height: 44px;
  padding: var(--space-3) var(--space-4);
  border: 1px solid var(--glass-border);
  border-radius: var(--radius-md);
  background: linear-gradient(135deg, #ffffffb8, #f8fafc8f);
  box-shadow: var(--glass-rim), 0 1px 3px #1230520a;
  color: var(--color-text-primary);
  text-decoration: none;
  font-size: var(--font-size-body);
  font-weight: 500;
  transition: scale var(--nav-press-time) var(--ease-settle),
    color var(--nav-press-time) var(--ease-settle),
    border-color var(--nav-press-time) var(--ease-settle);
}

.site-nav__drawer-link::before {
  content: '';
  position: absolute;
  inset: 0;
  z-index: -1;
  border-radius: inherit;
  background: linear-gradient(150deg, #ffffffb3, transparent 60%), var(--palette-main, #d9eafd);
  box-shadow: var(--glass-rim);
  opacity: 0;
  pointer-events: none;
  transition: opacity var(--nav-press-time) var(--ease-settle);
}

@media (hover: hover) {
  .site-nav__drawer-link:hover::before { opacity: 0.36; }
}

.site-nav__drawer-link.is-selected,
.site-nav__drawer-link:active {
  color: var(--accent-ink);
  border-color: color-mix(in srgb, var(--accent-ink) 42%, var(--glass-border));
}

.site-nav__drawer-link.is-selected::before,
.site-nav__drawer-link:active::before { opacity: 1; }

.site-nav__drawer-link:active { scale: 0.97; }

.site-nav__drawer-link:focus-visible {
  outline: 2px solid var(--accent-ink);
  outline-offset: -2px;
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
  .site-nav__drawer-link::before {
    transition: none;
  }

  .site-nav__drawer-link:active { scale: none; }
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
</style>
