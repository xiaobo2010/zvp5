<script setup lang="ts">
import { ref, computed, watch, onMounted, onUnmounted, nextTick } from 'vue'
import { useRouter, useRoute } from 'vue-router'

const router = useRouter()
const route = useRoute()

// Primary nav link
const primaryLink = { label: '首页', path: '/' }

// Dropdown menu items
const dropdownItems = [
  { label: '义工列表', path: '/volunteers' },
  { label: '个人主页', path: '/me' }
]

const mobileMenuOpen = ref(false)
const dropdownOpen = ref(false)
const activePath = computed(() => route.path)

// Check if current route is in dropdown
const isDropdownActive = computed(() =>
  dropdownItems.some(item => item.path === activePath.value)
)

// Spring indicator state
const linksRef = ref<HTMLElement>()
const indicatorRef = ref<HTMLElement>()
const indicator = ref({ x: 0, w: 0, visible: false })

const SPRING = 'cubic-bezier(0.34, 1.56, 0.64, 1)'
const SPRING_MS = 460

function reduceMotion() {
  return window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false
}

function renderedTranslateX(el: HTMLElement) {
  const raw = getComputedStyle(el).transform
  if (!raw || raw === 'none') return 0
  const matrix = new DOMMatrixReadOnly(raw)
  return matrix.m41
}

let indicatorAnimation: Animation | null = null

function playIndicator(from: number, target: number, width: number) {
  const pill = indicatorRef.value
  if (!pill || reduceMotion()) return
  indicatorAnimation?.cancel()
  indicatorAnimation = pill.animate(
    [
      { transform: `translateX(${from}px) translateY(-50%)`, width: `${width}px` },
      { transform: `translateX(${target}px) translateY(-50%)` }
    ],
    {
      duration: SPRING_MS,
      easing: SPRING,
      fill: 'forwards'
    }
  )
}

function updateIndicator(animate = true) {
  const root = linksRef.value
  const pill = indicatorRef.value
  if (!root || !pill) return

  // Find the active link based on current route
  let activeElement: HTMLElement | null = null

  if (activePath.value === primaryLink.path) {
    activeElement = root.querySelector<HTMLElement>('.site-nav__link[data-primary]')
  } else if (isDropdownActive.value) {
    activeElement = root.querySelector<HTMLElement>('.site-nav__dropdown-trigger')
  }

  if (!activeElement) {
    indicator.value = { ...indicator.value, visible: false }
    return
  }

  // offsetLeft is relative to the nearest positioned ancestor, which is the
  // dropdown wrapper for the trigger. Measure against the nav track instead so
  // the spring pill lands exactly under the active control.
  const rootRect = root.getBoundingClientRect()
  const activeRect = activeElement.getBoundingClientRect()
  const target = activeRect.left - rootRect.left
  if (!animate || reduceMotion()) {
    indicatorAnimation?.cancel()
    indicatorAnimation = null
    indicator.value = { x: target, w: activeRect.width, visible: true }
    return
  }

  const from = renderedTranslateX(pill)
  const size = indicator.value.w
  indicator.value = { x: target, w: activeRect.width, visible: true }
  pill.style.transform = `translateX(${target}px) translateY(-50%)`

  if (Math.abs(from - target) < 0.5) return
  playIndicator(from, target, size)
}

function pulseIcon(event: MouseEvent) {
  if (reduceMotion()) return
  const target = event.currentTarget as HTMLElement
  target.animate(
    [
      { transform: 'scale(1)' },
      { transform: 'scale(1.08)' },
      { transform: 'scale(1)' }
    ],
    { duration: 400, easing: 'cubic-bezier(0.22, 1.6, 0.36, 1)' }
  )
}

function handlePrimaryClick(event: MouseEvent) {
  event.preventDefault()
  pulseIcon(event)
  dropdownOpen.value = false
  router.push(primaryLink.path)
}

function toggleDropdown() {
  dropdownOpen.value = !dropdownOpen.value
}

function handleDropdownItemClick(path: string) {
  dropdownOpen.value = false
  router.push(path)
}

function closeDropdown() {
  dropdownOpen.value = false
}

function toggleMobileMenu() {
  mobileMenuOpen.value = !mobileMenuOpen.value
}

function closeMobileMenu() {
  mobileMenuOpen.value = false
}

function handleEscape(e: KeyboardEvent) {
  if (e.key === 'Escape') {
    if (dropdownOpen.value) {
      closeDropdown()
    } else if (mobileMenuOpen.value) {
      closeMobileMenu()
    }
  }
}

function handleClickOutside(e: MouseEvent) {
  const target = e.target as HTMLElement
  if (dropdownOpen.value && !target.closest('.site-nav__dropdown')) {
    closeDropdown()
  }
}

function onResize() {
  updateIndicator(true)
}

onMounted(() => {
  document.addEventListener('keydown', handleEscape)
  document.addEventListener('click', handleClickOutside)
  window.addEventListener('resize', onResize)
  nextTick(() => updateIndicator(false))
})

onUnmounted(() => {
  document.removeEventListener('keydown', handleEscape)
  document.removeEventListener('click', handleClickOutside)
  window.removeEventListener('resize', onResize)
  indicatorAnimation?.cancel()
  indicatorAnimation = null
})

watch(activePath, () => {
  closeDropdown()
  updateIndicator(true)
})
</script>

<template>
  <header class="site-nav">
    <div class="site-nav__inner">
      <router-link class="site-nav__brand" to="/" aria-label="ZVMS 首页">
        <span class="site-nav__wordmark">ZVMS</span>
      </router-link>

      <!-- Desktop navigation with liquid glass indicator -->
      <nav ref="linksRef" class="site-nav__links" aria-label="主导航">
        <span ref="indicatorRef" class="site-nav__indicator" :style="{
          width: indicator.w + 'px',
          opacity: indicator.visible ? 1 : 0,
          transform: `translateX(${indicator.x}px) translateY(-50%)`
        }" aria-hidden="true" />

        <router-link
          :to="primaryLink.path"
          class="site-nav__link"
          :class="{ 'is-active': primaryLink.path === activePath }"
          :aria-current="primaryLink.path === activePath ? 'page' : undefined"
          data-primary
          @click="handlePrimaryClick"
        >
          {{ primaryLink.label }}
        </router-link>

        <div class="site-nav__dropdown">
          <button
            class="site-nav__dropdown-trigger"
            :class="{ 'is-active': isDropdownActive }"
            :aria-expanded="dropdownOpen"
            aria-haspopup="true"
            @click="toggleDropdown"
          >
            更多
            <svg
              class="dropdown-chevron"
              :class="{ 'is-open': dropdownOpen }"
              width="12"
              height="12"
              viewBox="0 0 12 12"
              fill="none"
              aria-hidden="true"
            >
              <path d="M3 4.5L6 7.5L9 4.5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
            </svg>
          </button>

          <Transition name="dropdown">
            <div v-if="dropdownOpen" class="site-nav__dropdown-menu">
              <router-link
                v-for="item in dropdownItems"
                :key="item.path"
                :to="item.path"
                class="site-nav__dropdown-item"
                :class="{ 'is-active': item.path === activePath }"
                :aria-current="item.path === activePath ? 'page' : undefined"
                @click="handleDropdownItemClick(item.path)"
              >
                {{ item.label }}
              </router-link>
            </div>
          </Transition>
        </div>
      </nav>

      <!-- Right controls -->
      <div class="site-nav__controls">
        <button class="site-nav__icon-btn" aria-label="搜索">
          <svg width="20" height="20" viewBox="0 0 20 20" fill="none" aria-hidden="true">
            <circle cx="9" cy="9" r="6" stroke="currentColor" stroke-width="1.5"/>
            <path d="M13.5 13.5L17 17" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
          </svg>
        </button>
        <button class="site-nav__icon-btn" aria-label="通知">
          <svg width="20" height="20" viewBox="0 0 20 20" fill="none" aria-hidden="true">
            <path d="M6 8C6 5.79086 7.79086 4 10 4C12.2091 4 14 5.79086 14 8V11.382C14 11.7607 14.214 12.107 14.5528 12.2764L15.5 12.75V14H4.5V12.75L5.44721 12.2764C5.78598 12.107 6 11.7607 6 11.382V8Z" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/>
            <path d="M8.5 14V14.5C8.5 15.3284 9.17157 16 10 16C10.8284 16 11.5 15.3284 11.5 14.5V14" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
          </svg>
        </button>
        <button class="site-nav__avatar" aria-label="账户菜单">
          <span class="site-nav__avatar-text">知</span>
        </button>
      </div>

      <!-- Mobile menu toggle -->
      <button
        class="site-nav__toggle"
        :aria-expanded="mobileMenuOpen"
        aria-controls="mobile-menu"
        aria-label="切换菜单"
        @click="toggleMobileMenu"
      >
        <span class="site-nav__toggle-line" :class="{ open: mobileMenuOpen }" />
        <span class="site-nav__toggle-line" :class="{ open: mobileMenuOpen }" />
      </button>
    </div>

    <!-- Mobile menu -->
    <Transition name="mobile-menu">
      <nav
        v-if="mobileMenuOpen"
        id="mobile-menu"
        class="site-nav__mobile"
        aria-label="移动导航"
      >
        <router-link
          :to="primaryLink.path"
          class="site-nav__mobile-link"
          :class="{ 'is-active': primaryLink.path === activePath }"
          :aria-current="primaryLink.path === activePath ? 'page' : undefined"
          @click="closeMobileMenu"
        >
          {{ primaryLink.label }}
        </router-link>
        <router-link
          v-for="item in dropdownItems"
          :key="item.path"
          :to="item.path"
          class="site-nav__mobile-link"
          :class="{ 'is-active': item.path === activePath }"
          :aria-current="item.path === activePath ? 'page' : undefined"
          @click="closeMobileMenu"
        >
          {{ item.label }}
        </router-link>
        <div class="site-nav__mobile-divider" />
        <button class="site-nav__mobile-link" @click="closeMobileMenu">
          搜索
        </button>
        <button class="site-nav__mobile-link" @click="closeMobileMenu">
          通知
        </button>
        <button class="site-nav__mobile-link" @click="closeMobileMenu">
          账户设置
        </button>
      </nav>
    </Transition>
  </header>
</template>

<style scoped>
.site-nav {
  position: sticky;
  top: 0;
  z-index: 1000;
  background: var(--glass-bg);
  -webkit-backdrop-filter: var(--glass-blur);
  backdrop-filter: var(--glass-blur);
  border-bottom: 1px solid var(--glass-border);
  box-shadow: var(--glass-shadow);
}

.site-nav__inner {
  max-width: 1200px;
  margin: 0 auto;
  padding: 0 clamp(20px, 4vw, 40px);
  height: 56px;
  display: flex;
  align-items: center;
  gap: var(--space-6);
}

.site-nav__brand {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  text-decoration: none;
  color: var(--color-text-primary);
  font-weight: 600;
  font-size: var(--font-size-body);
  letter-spacing: -0.01em;
  transition: opacity var(--duration-fast) var(--ease-smooth);
}

.site-nav__brand:hover {
  opacity: 0.7;
}

.site-nav__brand:focus-visible {
  outline: 2px solid var(--color-brand);
  outline-offset: 4px;
  border-radius: var(--radius-xs);
}

.site-nav__wordmark {
  font-weight: 600;
}

/* Liquid glass navigation with spring indicator */
.site-nav__links {
  position: relative;
  display: none;
  align-items: center;
  gap: 2px;
  margin-left: var(--space-4);
  padding: 4px;
  background: linear-gradient(
    135deg,
    rgba(255, 255, 255, 0.42) 0%,
    rgba(255, 255, 255, 0.25) 100%
  );
  -webkit-backdrop-filter: blur(12px) saturate(170%);
  backdrop-filter: blur(12px) saturate(170%);
  border: 1px solid rgba(255, 255, 255, 0.68);
  border-radius: var(--radius-pill);
  box-shadow:
    inset 0 0.5px 0 rgba(255, 255, 255, 0.75),
    0 0 14px rgba(18, 48, 82, 0.08);
}

.site-nav__indicator {
  position: absolute;
  left: 0;
  top: 50%;
  transform: translateY(-50%);
  height: calc(100% - 8px);
  border-radius: var(--radius-pill);
  background: linear-gradient(
    135deg,
    rgba(0, 113, 227, 0.15) 0%,
    rgba(0, 113, 227, 0.08) 100%
  );
  box-shadow:
    inset 0 0.5px 0 rgba(255, 255, 255, 0.55),
    0 0 10px rgba(14, 165, 233, 0.25);
  pointer-events: none;
  transition: opacity 0.2s ease-out;
}

.site-nav__link {
  position: relative;
  z-index: 1;
  padding: var(--space-2) var(--space-4);
  color: var(--color-text-secondary);
  text-decoration: none;
  font-size: var(--font-size-callout);
  font-weight: 500;
  border-radius: var(--radius-pill);
  transition:
    color var(--duration-fast) var(--ease-smooth),
    transform var(--duration-fast) var(--ease-spring);
  min-height: 36px;
  display: flex;
  align-items: center;
}

.site-nav__link:hover {
  color: var(--color-text-primary);
}

.site-nav__link:active {
  transform: scale(0.94);
}

.site-nav__link.is-active {
  background: transparent;
  border-color: transparent;
  box-shadow: none;
  color: var(--color-brand);
}

.site-nav__link:focus-visible {
  outline: 2px solid var(--color-brand);
  outline-offset: 2px;
}

/* Dropdown */
.site-nav__dropdown {
  position: relative;
}

.site-nav__dropdown-trigger {
  position: relative;
  z-index: 1;
  padding: var(--space-2) var(--space-4);
  display: flex;
  align-items: center;
  gap: var(--space-1);
  color: var(--color-text-secondary);
  font-size: var(--font-size-callout);
  font-weight: 500;
  border-radius: var(--radius-pill);
  background: linear-gradient(
    135deg,
    rgba(255, 255, 255, 0.34) 0%,
    rgba(255, 255, 255, 0.18) 100%
  );
  -webkit-backdrop-filter: blur(10px) saturate(160%);
  backdrop-filter: blur(10px) saturate(160%);
  border: 1px solid rgba(255, 255, 255, 0.48);
  box-shadow: inset 0 0.5px 0 rgba(255, 255, 255, 0.72);
  cursor: pointer;
  transition:
    color var(--duration-fast) var(--ease-smooth),
    background var(--duration-fast) var(--ease-smooth),
    border-color var(--duration-fast) var(--ease-smooth),
    transform var(--duration-fast) var(--ease-spring);
  min-height: 36px;
}

.site-nav__dropdown-trigger:hover {
  color: var(--color-text-primary);
}

.site-nav__dropdown-trigger:active {
  transform: scale(0.94);
}

.site-nav__dropdown-trigger.is-active {
  background: transparent;
  border-color: transparent;
  box-shadow: none;
  color: var(--color-brand);
}

.site-nav__dropdown-trigger:focus-visible {
  outline: 2px solid var(--color-brand);
  outline-offset: 2px;
}

.dropdown-chevron {
  transition: transform var(--duration-fast) var(--ease-spring);
}

.dropdown-chevron.is-open {
  transform: rotate(180deg);
}

.site-nav__dropdown-menu {
  position: absolute;
  z-index: 30;
  top: calc(100% + 8px);
  right: 0;
  min-width: 160px;
  padding: 6px;
  background: linear-gradient(
    135deg,
    rgba(255, 255, 255, 0.85) 0%,
    rgba(248, 251, 255, 0.78) 100%
  );
  -webkit-backdrop-filter: blur(24px) saturate(180%);
  backdrop-filter: blur(24px) saturate(180%);
  border: 1px solid rgba(255, 255, 255, 0.72);
  border-radius: var(--radius-lg);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.85),
    0 8px 24px rgba(18, 48, 82, 0.12),
    0 2px 8px rgba(0, 113, 227, 0.08);
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.site-nav__dropdown-item {
  padding: var(--space-3) var(--space-4);
  color: var(--color-text-primary);
  text-decoration: none;
  font-size: var(--font-size-callout);
  font-weight: 500;
  border-radius: var(--radius-md);
  transition:
    background var(--duration-fast) var(--ease-smooth),
    color var(--duration-fast) var(--ease-smooth);
  display: flex;
  align-items: center;
  min-height: 36px;
}

.site-nav__dropdown-item:hover {
  background: linear-gradient(
    135deg,
    rgba(0, 113, 227, 0.12) 0%,
    rgba(0, 113, 227, 0.08) 100%
  );
  color: var(--color-brand);
}

.site-nav__dropdown-item.is-active {
  background: linear-gradient(
    135deg,
    rgba(0, 113, 227, 0.15) 0%,
    rgba(0, 113, 227, 0.1) 100%
  );
  color: var(--color-brand);
  font-weight: 600;
}

.site-nav__dropdown-item:focus-visible {
  outline: 2px solid var(--color-brand);
  outline-offset: -2px;
}

.dropdown-enter-active,
.dropdown-leave-active {
  transition: opacity var(--duration-fast) var(--ease-smooth),
    transform var(--duration-fast) var(--ease-spring);
}

.dropdown-enter-from,
.dropdown-leave-to {
  opacity: 0;
  transform: translateY(-8px);
}

.site-nav__controls {
  display: none;
  align-items: center;
  gap: var(--space-2);
  margin-left: auto;
}

/* Liquid glass icon buttons with specular highlight and inset edge */
.site-nav__icon-btn {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  padding: 0;
  background: linear-gradient(
    135deg,
    rgba(255, 255, 255, 0.48) 0%,
    rgba(255, 255, 255, 0.28) 100%
  );
  -webkit-backdrop-filter: blur(10px) saturate(170%);
  backdrop-filter: blur(10px) saturate(170%);
  border: 1px solid rgba(255, 255, 255, 0.72);
  border-radius: var(--radius-sm);
  color: var(--color-text-secondary);
  cursor: pointer;
  transition: all var(--duration-fast) var(--ease-spring);
  overflow: hidden;
  isolation: isolate;
  box-shadow:
    inset 0 0.5px 0 rgba(255, 255, 255, 0.85),
    inset 0 -0.5px 0 rgba(0, 0, 0, 0.08),
    0 0 8px rgba(18, 48, 82, 0.06);
}

.site-nav__icon-btn::before {
  content: '';
  position: absolute;
  inset: 0;
  background: radial-gradient(
    circle at 50% 40%,
    rgba(255, 255, 255, 0.35) 0%,
    transparent 70%
  );
  border-radius: var(--radius-sm);
  pointer-events: none;
}

.site-nav__icon-btn::after {
  content: '';
  position: absolute;
  inset: 0;
  background: linear-gradient(
    115deg,
    rgba(255, 255, 255, 0) 22%,
    rgba(255, 255, 255, 0.65) 46%,
    rgba(255, 255, 255, 0.08) 56%,
    rgba(255, 255, 255, 0) 72%
  );
  border-radius: var(--radius-sm);
  opacity: 0;
  transform: translateX(-100%);
  transition: all var(--duration-base) var(--ease-smooth);
  pointer-events: none;
}

.site-nav__icon-btn:hover {
  color: var(--color-text-primary);
  transform: scale(1.05);
  box-shadow:
    inset 0 0.5px 0 rgba(255, 255, 255, 0.92),
    inset 0 -0.5px 0 rgba(0, 0, 0, 0.1),
    0 0 14px rgba(18, 48, 82, 0.1);
}

.site-nav__icon-btn:hover::after {
  opacity: 1;
  transform: translateX(100%);
}

.site-nav__icon-btn:active {
  transform: scale(0.92);
}

.site-nav__icon-btn:focus-visible {
  outline: 2px solid var(--color-brand);
  outline-offset: 2px;
}

.site-nav__icon-btn svg {
  position: relative;
  z-index: 1;
}

/* Avatar button with liquid glass treatment */
.site-nav__avatar {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  padding: 0;
  background: linear-gradient(
    135deg,
    rgba(0, 113, 227, 0.92) 0%,
    rgba(0, 113, 227, 0.78) 100%
  );
  -webkit-backdrop-filter: blur(10px) saturate(170%);
  backdrop-filter: blur(10px) saturate(170%);
  border: 1px solid rgba(255, 255, 255, 0.72);
  border-radius: 50%;
  color: white;
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  transition: all var(--duration-fast) var(--ease-spring);
  overflow: hidden;
  isolation: isolate;
  box-shadow:
    inset 0 0.5px 0 rgba(255, 255, 255, 0.45),
    0 0 10px rgba(0, 113, 227, 0.2);
}

.site-nav__avatar::before {
  content: '';
  position: absolute;
  inset: 0;
  background: radial-gradient(
    circle at 50% 35%,
    rgba(255, 255, 255, 0.4) 0%,
    transparent 65%
  );
  border-radius: 50%;
  pointer-events: none;
}

.site-nav__avatar:hover {
  transform: scale(1.08);
  box-shadow:
    inset 0 0.5px 0 rgba(255, 255, 255, 0.55),
    0 0 16px rgba(0, 113, 227, 0.35);
}

.site-nav__avatar:active {
  transform: scale(0.94);
}

.site-nav__avatar:focus-visible {
  outline: 2px solid var(--color-brand);
  outline-offset: 3px;
}

.site-nav__avatar-text {
  margin-top: 1px;
  position: relative;
  z-index: 1;
}

.site-nav__toggle {
  display: flex;
  flex-direction: column;
  justify-content: center;
  align-items: center;
  gap: 5px;
  width: 40px;
  height: 40px;
  margin-left: auto;
  padding: 0;
  background: var(--glass-bg);
  -webkit-backdrop-filter: var(--glass-blur);
  backdrop-filter: var(--glass-blur);
  border: 1px solid var(--glass-border);
  border-radius: 50%;
  box-shadow:
    inset 0 0.5px 0 rgba(255, 255, 255, 0.78),
    inset 0 -0.5px 0 rgba(0, 0, 0, 0.06),
    0 0 12px rgba(18, 48, 82, 0.1);
  cursor: pointer;
  transition: transform var(--duration-fast) var(--ease-spring);
}

.site-nav__toggle:hover {
  transform: scale(1.05);
}

.site-nav__toggle:active {
  transform: scale(0.95);
}

.site-nav__toggle:focus-visible {
  outline: 2px solid var(--color-brand);
  outline-offset: 2px;
  border-radius: var(--radius-xs);
}

.site-nav__toggle-line {
  width: 20px;
  height: 2px;
  margin: 0 auto;
  background: var(--color-text-primary);
  border-radius: 2px;
  transition: all var(--duration-base) var(--ease-spring);
}

.site-nav__toggle-line:first-child.open {
  transform: translateY(7px) rotate(45deg);
}

.site-nav__toggle-line:last-child.open {
  transform: translateY(-7px) rotate(-45deg);
}

/* Mobile menu with frosted glass */
.site-nav__mobile {
  position: absolute;
  top: 100%;
  left: 0;
  right: 0;
  padding: var(--space-4) clamp(20px, 4vw, 40px);
  background: linear-gradient(
    135deg,
    rgba(248, 251, 255, 0.82) 0%,
    rgba(245, 248, 252, 0.7) 100%
  );
  -webkit-backdrop-filter: blur(24px) saturate(180%);
  backdrop-filter: blur(24px) saturate(180%);
  border-bottom: 1px solid var(--glass-border);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.65),
    0 8px 24px rgba(18, 48, 82, 0.12);
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}

.site-nav__mobile-link {
  padding: var(--space-3) var(--space-4);
  color: var(--color-text-primary);
  text-decoration: none;
  font-size: var(--font-size-body);
  font-weight: 500;
  border-radius: var(--radius-md);
  background: linear-gradient(
    135deg,
    rgba(255, 255, 255, 0.42) 0%,
    rgba(255, 255, 255, 0.22) 100%
  );
  -webkit-backdrop-filter: blur(10px) saturate(160%);
  backdrop-filter: blur(10px) saturate(160%);
  border: 1px solid rgba(255, 255, 255, 0.58);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.72);
  text-align: left;
  cursor: pointer;
  transition:
    background var(--duration-fast) var(--ease-smooth),
    border-color var(--duration-fast) var(--ease-smooth),
    transform var(--duration-fast) var(--ease-spring);
  min-height: 44px;
  display: flex;
  align-items: center;
}

.site-nav__mobile-link.is-active {
  background: linear-gradient(
    135deg,
    rgba(0, 113, 227, 0.16) 0%,
    rgba(255, 255, 255, 0.26) 100%
  );
  border-color: rgba(255, 255, 255, 0.7);
  color: var(--color-brand);
}

.site-nav__mobile-link:hover {
  background: linear-gradient(
    135deg,
    rgba(0, 113, 227, 0.12) 0%,
    rgba(255, 255, 255, 0.24) 100%
  );
  border-color: rgba(255, 255, 255, 0.72);
}

.site-nav__mobile-link:active {
  transform: scale(0.98);
}

.site-nav__mobile-link:focus-visible {
  outline: 2px solid var(--color-brand);
  outline-offset: 2px;
}

.site-nav__mobile-divider {
  height: 1px;
  background: var(--color-border-hairline);
  margin: var(--space-2) 0;
}

.mobile-menu-enter-active,
.mobile-menu-leave-active {
  transition: all var(--duration-base) var(--ease-smooth);
}

.mobile-menu-enter-from {
  opacity: 0;
  transform: translateY(-12px);
}

.mobile-menu-leave-to {
  opacity: 0;
  transform: translateY(-8px);
}

@media (min-width: 768px) {
  .site-nav__links {
    display: flex;
  }

  .site-nav__controls {
    display: flex;
  }

  .site-nav__toggle {
    display: none;
  }
}

/* Reduced motion */
@media (prefers-reduced-motion: reduce) {
  .site-nav__icon-btn::after,
  .site-nav__avatar::before {
    display: none;
  }

  .site-nav__indicator {
    transition: opacity 0.01ms, width 0.01ms, transform 0.01ms;
  }

  .site-nav__link:active,
  .site-nav__icon-btn:active,
  .site-nav__avatar:active,
  .site-nav__toggle:active,
  .site-nav__mobile-link:active,
  .site-nav__dropdown-trigger:active {
    transform: none;
  }

  .dropdown-chevron {
    transition: none;
  }
}
</style>
