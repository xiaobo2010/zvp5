<script setup lang="ts">
import { ref, computed, watch, onMounted, onUnmounted, nextTick } from 'vue'

const links = [
  { label: '首页', href: '#home' },
  { label: '活动', href: '#activities' },
  { label: '时长', href: '#hours' },
  { label: '组织', href: '#profile' }
]

const mobileMenuOpen = ref(false)
const activeHref = ref('#home')

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
      { transform: `translateX(${from}px)`, width: `${width}px` },
      { transform: `translateX(${target}px)` }
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

  const active = root.querySelector<HTMLElement>('.site-nav__link.is-active')
  if (!active) {
    indicator.value = { ...indicator.value, visible: false }
    return
  }

  const target = active.offsetLeft
  if (!animate || reduceMotion()) {
    indicatorAnimation?.cancel()
    indicatorAnimation = null
    indicator.value = { x: target, w: active.offsetWidth, visible: true }
    return
  }

  const from = renderedTranslateX(pill)
  const size = indicator.value.w
  indicator.value = { x: target, w: active.offsetWidth, visible: true }
  pill.style.transform = `translateX(${target}px)`

  if (Math.abs(from - target) < 0.5) return
  playIndicator(from, target, size)
}

function previewIndicator(index: number) {
  const root = linksRef.value
  const pill = indicatorRef.value
  const link = root?.querySelectorAll<HTMLElement>('.site-nav__link')?.[index]
  if (!pill || !link) return
  playIndicator(renderedTranslateX(pill), link.offsetLeft, indicator.value.w)
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

function handleNavClick(href: string, event: MouseEvent, index: number) {
  pulseIcon(event)
  const root = linksRef.value
  if (root) {
    previewIndicator(index)
  }
  activeHref.value = href
}

const indicatorStyle = computed(() => ({
  width: indicator.value.w + 'px',
  opacity: indicator.value.visible ? 1 : 0,
  transform: `translateX(${indicator.value.x}px)`
}))

function toggleMobileMenu() {
  mobileMenuOpen.value = !mobileMenuOpen.value
}

function closeMobileMenu() {
  mobileMenuOpen.value = false
}

function handleEscape(e: KeyboardEvent) {
  if (e.key === 'Escape' && mobileMenuOpen.value) {
    closeMobileMenu()
  }
}

function onResize() {
  updateIndicator(true)
}

onMounted(() => {
  document.addEventListener('keydown', handleEscape)
  window.addEventListener('resize', onResize)
  nextTick(() => updateIndicator(false))
})

onUnmounted(() => {
  document.removeEventListener('keydown', handleEscape)
  window.removeEventListener('resize', onResize)
  indicatorAnimation?.cancel()
  indicatorAnimation = null
})

watch(activeHref, () => updateIndicator(true))
</script>

<template>
  <header class="site-nav">
    <div class="site-nav__inner">
      <a class="site-nav__brand" href="#home" aria-label="ZVMS 首页">
        <span class="site-nav__wordmark">ZVMS</span>
      </a>

      <!-- Desktop navigation with liquid glass indicator -->
      <nav ref="linksRef" class="site-nav__links" aria-label="主导航">
        <span ref="indicatorRef" class="site-nav__indicator" :style="indicatorStyle" aria-hidden="true" />
        <a
          v-for="(link, index) in links"
          :key="link.label"
          :href="link.href"
          class="site-nav__link"
          :class="{ 'is-active': link.href === activeHref }"
          :aria-current="link.href === activeHref ? 'page' : undefined"
          @click="handleNavClick(link.href, $event, index)"
        >
          {{ link.label }}
        </a>
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
        <a
          v-for="link in links"
          :key="link.label"
          :href="link.href"
          class="site-nav__mobile-link"
          @click="closeMobileMenu"
        >
          {{ link.label }}
        </a>
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
    inset 0 1px 0 rgba(255, 255, 255, 0.75),
    0 2px 12px rgba(18, 48, 82, 0.08);
}

.site-nav__indicator {
  position: absolute;
  left: 4px;
  top: 4px;
  height: calc(100% - 8px);
  border-radius: var(--radius-pill);
  background: linear-gradient(
    135deg,
    rgba(0, 113, 227, 0.15) 0%,
    rgba(0, 113, 227, 0.08) 100%
  );
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.55),
    0 2px 8px rgba(14, 165, 233, 0.25);
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
  color: var(--color-brand);
}

.site-nav__link:focus-visible {
  outline: 2px solid var(--color-brand);
  outline-offset: 2px;
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
    inset 0 1px 0 rgba(255, 255, 255, 0.85),
    inset 0 -1px 0 rgba(0, 0, 0, 0.08),
    0 2px 6px rgba(18, 48, 82, 0.06);
}

.site-nav__icon-btn::before {
  content: '';
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 50%;
  background: linear-gradient(
    180deg,
    rgba(255, 255, 255, 0.75) 0%,
    rgba(255, 255, 255, 0) 100%
  );
  border-radius: var(--radius-sm) var(--radius-sm) 0 0;
  pointer-events: none;
  opacity: 0.5;
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
    inset 0 1px 0 rgba(255, 255, 255, 0.92),
    inset 0 -1px 0 rgba(0, 0, 0, 0.1),
    0 4px 12px rgba(18, 48, 82, 0.1);
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
    inset 0 1px 0 rgba(255, 255, 255, 0.45),
    0 2px 8px rgba(0, 113, 227, 0.2);
}

.site-nav__avatar::before {
  content: '';
  position: absolute;
  inset: 0;
  background: radial-gradient(
    circle at 30% 30%,
    rgba(255, 255, 255, 0.5) 0%,
    transparent 60%
  );
  border-radius: 50%;
  pointer-events: none;
}

.site-nav__avatar:hover {
  transform: scale(1.08);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.55),
    0 4px 14px rgba(0, 113, 227, 0.35);
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
  box-shadow: var(--glass-shadow);
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
    rgba(248, 251, 255, 0.95) 0%,
    rgba(245, 248, 252, 0.92) 100%
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
  font-weight: 400;
  border-radius: var(--radius-md);
  background: transparent;
  border: none;
  text-align: left;
  cursor: pointer;
  transition:
    background var(--duration-fast) var(--ease-smooth),
    transform var(--duration-fast) var(--ease-spring);
  min-height: 44px;
  display: flex;
  align-items: center;
}

.site-nav__mobile-link:hover {
  background: rgba(0, 113, 227, 0.08);
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
  .site-nav__mobile-link:active {
    transform: none;
  }
}
</style>
