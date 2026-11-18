<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue'

const links = [
  { label: '首页', href: '#home' },
  { label: '活动', href: '#activities' },
  { label: '时长', href: '#hours' },
  { label: '组织', href: '#profile' }
]

const mobileMenuOpen = ref(false)

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

onMounted(() => {
  document.addEventListener('keydown', handleEscape)
})

onUnmounted(() => {
  document.removeEventListener('keydown', handleEscape)
})
</script>

<template>
  <header class="site-nav">
    <div class="site-nav__inner">
      <a class="site-nav__brand" href="#home" aria-label="ZVMS 首页">
        <span class="site-nav__wordmark">ZVMS</span>
      </a>

      <!-- Desktop navigation -->
      <nav class="site-nav__links" aria-label="主导航">
        <a
          v-for="link in links"
          :key="link.label"
          :href="link.href"
          class="site-nav__link"
          :class="{ 'is-active': link.href === '#home' }"
          :aria-current="link.href === '#home' ? 'page' : undefined"
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

.site-nav__links {
  display: none;
  gap: var(--space-2);
  margin-left: var(--space-4);
}

.site-nav__link {
  padding: var(--space-2) var(--space-3);
  color: var(--color-text-secondary);
  text-decoration: none;
  font-size: var(--font-size-callout);
  font-weight: 400;
  border-radius: var(--radius-sm);
  transition: all var(--duration-fast) var(--ease-smooth);
}

.site-nav__link:hover {
  color: var(--color-text-primary);
  background: rgba(0, 0, 0, 0.04);
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

/* Glass Icon Button */
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
    rgba(255, 255, 255, 0.35) 0%,
    rgba(255, 255, 255, 0.15) 100%
  );
  -webkit-backdrop-filter: blur(8px) saturate(180%);
  backdrop-filter: blur(8px) saturate(180%);
  border: 1px solid rgba(255, 255, 255, 0.6);
  border-radius: var(--radius-sm);
  color: var(--color-text-secondary);
  cursor: pointer;
  transition: all var(--duration-fast) var(--ease-spring);
  overflow: hidden;
  isolation: isolate;
}

.site-nav__icon-btn::before {
  content: '';
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 50%;
  background: var(--glass-highlight);
  border-radius: var(--radius-sm) var(--radius-sm) 0 0;
  pointer-events: none;
  opacity: 0.3;
}

.site-nav__icon-btn::after {
  content: '';
  position: absolute;
  inset: 0;
  background: var(--glass-sheen);
  border-radius: var(--radius-sm);
  opacity: 0;
  transform: translateX(-100%);
  transition: all var(--duration-base) var(--ease-smooth);
  pointer-events: none;
}

.site-nav__icon-btn:hover {
  color: var(--color-text-primary);
  transform: scale(1.05);
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.08);
}

.site-nav__icon-btn:hover::after {
  opacity: 1;
  transform: translateX(100%);
}

.site-nav__icon-btn:active {
  transform: scale(0.95);
}

.site-nav__icon-btn:focus-visible {
  outline: 2px solid var(--color-brand);
  outline-offset: 2px;
}

.site-nav__icon-btn svg {
  position: relative;
  z-index: 1;
}

/* Avatar Button (also glass) */
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
    rgba(0, 113, 227, 0.9) 0%,
    rgba(0, 113, 227, 0.75) 100%
  );
  -webkit-backdrop-filter: blur(8px) saturate(180%);
  backdrop-filter: blur(8px) saturate(180%);
  border: 1px solid rgba(255, 255, 255, 0.6);
  border-radius: 50%;
  color: white;
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  transition: all var(--duration-fast) var(--ease-spring);
  overflow: hidden;
  isolation: isolate;
}

.site-nav__avatar::before {
  content: '';
  position: absolute;
  inset: 0;
  background: radial-gradient(
    circle at 30% 30%,
    rgba(255, 255, 255, 0.3) 0%,
    transparent 60%
  );
  border-radius: 50%;
  pointer-events: none;
}

.site-nav__avatar:hover {
  transform: scale(1.08);
  box-shadow: 0 4px 12px rgba(0, 113, 227, 0.3);
}

.site-nav__avatar:active {
  transform: scale(0.95);
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

.site-nav__mobile {
  position: absolute;
  top: 100%;
  left: 0;
  right: 0;
  padding: var(--space-4) clamp(20px, 4vw, 40px);
  /* The overlay sits directly on page copy: keep the frost, but raise opacity so text stays legible. */
  background: #f8fbff;
  -webkit-backdrop-filter: blur(24px) saturate(180%);
  backdrop-filter: blur(24px) saturate(180%);
  border-bottom: 1px solid var(--glass-border);
  box-shadow: var(--shadow-lg);
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
  transition: background var(--duration-fast) var(--ease-smooth);
  min-height: 44px;
  display: flex;
  align-items: center;
}

.site-nav__mobile-link:hover {
  background: rgba(0, 0, 0, 0.04);
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
}
</style>
