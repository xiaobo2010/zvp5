<script setup lang="ts">
import { ref, computed, watch, onMounted, onUnmounted, nextTick } from 'vue'
import { useRouter, useRoute } from 'vue-router'

const router = useRouter()
const route = useRoute()

// Primary nav link
const primaryLink = { label: '首页', path: '/' }

// Drawer menu items
const drawerItems = [
  { label: '义工列表', path: '/volunteers' },
  { label: '个人主页', path: '/me' }
]

const drawerOpen = ref(false)
const activePath = computed(() => route.path)

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

function handleDrawerItemClick() {
  closeDrawer()
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
  setScrollLock(false)
})

watch(drawerOpen, (isOpen) => {
  setScrollLock(isOpen)

  if (isOpen) {
    nextTick(() => {
      updateFocusTrap()
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

watch(
  () => route.path,
  () => {
    closeDrawer()
  }
)
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
    :class="{ 'is-open': drawerOpen }"
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
      <router-link
        :to="primaryLink.path"
        class="site-nav__drawer-link"
        :class="{ 'is-active': primaryLink.path === activePath }"
        :aria-current="primaryLink.path === activePath ? 'page' : undefined"
        @click="handleDrawerItemClick"
      >
        {{ primaryLink.label }}
      </router-link>

      <router-link
        v-for="item in drawerItems"
        :key="item.path"
        :to="item.path"
        class="site-nav__drawer-link"
        :class="{ 'is-active': item.path === activePath }"
        :aria-current="item.path === activePath ? 'page' : undefined"
        @click="handleDrawerItemClick"
      >
        {{ item.label }}
      </router-link>
    </nav>

    <div class="site-nav__drawer-divider" />

    <div class="site-nav__drawer-section">
      <button class="site-nav__drawer-action" type="button">搜索</button>
      <button class="site-nav__drawer-action" type="button">通知</button>
      <button class="site-nav__drawer-action" type="button">账户设置</button>
    </div>
  </div>

  <!-- Glass handle on left edge -->
  <button
    ref="drawerHandleRef"
    class="site-nav__handle"
    type="button"
    :aria-expanded="drawerOpen"
    aria-controls="drawer"
    :aria-label="drawerOpen ? '关闭导航菜单' : '打开导航菜单'"
    :tabindex="drawerOpen ? -1 : 0"
    @click="toggleDrawer"
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
  cursor: pointer;
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
  transform: translateX(-100%);
  opacity: 0;
  transition: transform var(--duration-base) var(--ease-smooth),
    opacity var(--duration-base) var(--ease-smooth);
  outline: none;
}

.site-nav__drawer.is-open {
  transform: translateX(0);
  opacity: 1;
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

/* Drawer navigation */
.site-nav__drawer-nav {
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

.site-nav__drawer-link.is-active {
  background: linear-gradient(
    135deg,
    rgba(0, 113, 227, 0.2) 0%,
    rgba(255, 255, 255, 0.66) 100%
  );
  border-color: rgba(255, 255, 255, 0.92);
  color: var(--color-brand);
  font-weight: 600;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.98),
    inset 0 -1px 0 rgba(0, 0, 0, 0.05),
    0 4px 14px rgba(0, 113, 227, 0.12);
}

.site-nav__drawer-link:focus-visible {
  outline: 2px solid var(--color-brand);
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
}

.site-nav__drawer-action:hover {
  border-color: rgba(255, 255, 255, 0.92);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.98),
    inset 0 -1px 0 rgba(0, 0, 0, 0.06),
    0 4px 14px rgba(18, 48, 82, 0.1);
  transform: translateX(4px);
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
