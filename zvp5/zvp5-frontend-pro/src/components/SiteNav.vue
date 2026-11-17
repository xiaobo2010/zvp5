<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue'

const links = [
  { label: '首页', href: '#top' },
  { label: '志愿活动', href: '#activities' },
  { label: '志愿时长', href: '#impact' },
  { label: '组织', href: '#organizations' },
  { label: '关于', href: '#about' }
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
      <a class="site-nav__brand" href="#top" aria-label="ZVMS 首页">
        <span class="site-nav__mark" aria-hidden="true">Z</span>
        <span class="site-nav__wordmark">ZVMS</span>
      </a>

      <!-- Desktop navigation -->
      <nav class="site-nav__links" aria-label="主导航">
        <a
          v-for="link in links"
          :key="link.label"
          :href="link.href"
          class="site-nav__link"
        >
          {{ link.label }}
        </a>
      </nav>

      <a class="site-nav__cta" href="#start">开始使用</a>

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
        <a href="#start" class="site-nav__mobile-cta" @click="closeMobileMenu">
          开始使用
        </a>
      </nav>
    </Transition>
  </header>
</template>

<style scoped>
.site-nav {
  position: sticky;
  top: 0;
  z-index: 100;
  width: 100%;
}

.site-nav__inner {
  display: flex;
  align-items: center;
  gap: var(--space-5);
  padding: var(--space-4) var(--content-padding);
  background: var(--glass-bg);
  backdrop-filter: var(--glass-blur);
  border-bottom: 1px solid var(--color-border-subtle);
  box-shadow: var(--glass-shadow);
  /* Edge highlight for glass depth */
  position: relative;
}

.site-nav__inner::before {
  content: '';
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 1px;
  background: linear-gradient(
    90deg,
    transparent,
    var(--glass-border) 20%,
    var(--glass-border) 80%,
    transparent
  );
  pointer-events: none;
}

.site-nav__brand {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  color: var(--color-text-primary);
  font-weight: 600;
  font-size: var(--font-size-body);
  transition: transform var(--duration-fast) var(--ease-smooth);
}

.site-nav__brand:hover {
  transform: scale(1.02);
}

.site-nav__brand:active {
  transform: scale(0.98);
}

.site-nav__mark {
  display: grid;
  place-items: center;
  width: 32px;
  height: 32px;
  border-radius: var(--radius-sm);
  background: var(--color-brand);
  color: white;
  font-size: var(--font-size-callout);
  font-weight: 700;
  box-shadow: 0 2px 8px rgb(0 113 227 / 0.3);
  transition: all var(--duration-base) var(--ease-spring);
}

.site-nav__brand:hover .site-nav__mark {
  background: var(--color-brand-hover);
  transform: translateY(-1px);
  box-shadow: 0 4px 12px rgb(0 113 227 / 0.4);
}

.site-nav__brand:active .site-nav__mark {
  transform: translateY(0) scale(0.96);
}

.site-nav__wordmark {
  font-size: var(--font-size-body);
  letter-spacing: var(--letter-spacing-normal);
}

.site-nav__links {
  display: none;
  gap: var(--space-6);
  margin-left: auto;
}

.site-nav__link {
  position: relative;
  color: var(--color-text-secondary);
  font-size: var(--font-size-callout);
  font-weight: 500;
  transition: color var(--duration-fast) var(--ease-smooth);
}

.site-nav__link::after {
  content: '';
  position: absolute;
  bottom: -4px;
  left: 0;
  width: 0;
  height: 2px;
  background: var(--color-brand);
  border-radius: var(--radius-pill);
  transition: width var(--duration-base) var(--ease-spring);
}

.site-nav__link:hover {
  color: var(--color-text-primary);
}

.site-nav__link:hover::after {
  width: 100%;
}

.site-nav__cta {
  display: none;
  padding: var(--space-2) var(--space-5);
  border-radius: var(--radius-pill);
  background: var(--color-brand);
  color: white;
  font-size: var(--font-size-callout);
  font-weight: 500;
  box-shadow: 0 2px 8px rgb(0 113 227 / 0.3);
  transition: all var(--duration-base) var(--ease-spring);
}

.site-nav__cta:hover {
  background: var(--color-brand-hover);
  transform: translateY(-1px);
  box-shadow: 0 4px 16px rgb(0 113 227 / 0.4);
}

.site-nav__cta:active {
  transform: translateY(0) scale(0.98);
}

/* Mobile toggle */
.site-nav__toggle {
  display: flex;
  flex-direction: column;
  justify-content: center;
  align-items: center;
  gap: 5px;
  width: 44px;
  height: 44px;
  margin-left: auto;
  cursor: pointer;
  transition: transform var(--duration-fast) var(--ease-smooth);
}

.site-nav__toggle:hover {
  transform: scale(1.05);
}

.site-nav__toggle:active {
  transform: scale(0.95);
}

.site-nav__toggle-line {
  width: 20px;
  height: 2px;
  background: var(--color-text-primary);
  border-radius: var(--radius-pill);
  transition: all var(--duration-base) var(--ease-spring);
}

.site-nav__toggle-line:first-child.open {
  transform: translateY(7px) rotate(45deg);
}

.site-nav__toggle-line:last-child.open {
  transform: translateY(-7px) rotate(-45deg);
}

/* Mobile menu */
.site-nav__mobile {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  padding: var(--space-5) var(--content-padding);
  background: var(--glass-bg);
  backdrop-filter: var(--glass-blur);
  border-bottom: 1px solid var(--color-border-subtle);
  box-shadow: var(--shadow-lg);
}

.site-nav__mobile-link {
  padding: var(--space-4);
  color: var(--color-text-primary);
  font-size: var(--font-size-body);
  font-weight: 500;
  border-radius: var(--radius-sm);
  transition: all var(--duration-fast) var(--ease-smooth);
}

.site-nav__mobile-link:hover {
  background: rgb(0 0 0 / 0.04);
  transform: translateX(4px);
}

.site-nav__mobile-link:active {
  transform: translateX(2px) scale(0.98);
}

.site-nav__mobile-cta {
  margin-top: var(--space-3);
  padding: var(--space-4);
  border-radius: var(--radius-md);
  background: var(--color-brand);
  color: white;
  font-size: var(--font-size-body);
  font-weight: 600;
  text-align: center;
  box-shadow: 0 2px 8px rgb(0 113 227 / 0.3);
  transition: all var(--duration-base) var(--ease-spring);
}

.site-nav__mobile-cta:hover {
  background: var(--color-brand-hover);
  transform: translateY(-2px);
  box-shadow: 0 4px 16px rgb(0 113 227 / 0.4);
}

.site-nav__mobile-cta:active {
  transform: translateY(0) scale(0.98);
}

/* Mobile menu transitions */
.mobile-menu-enter-active,
.mobile-menu-leave-active {
  transition: all var(--duration-base) var(--ease-smooth);
  transform-origin: top;
}

.mobile-menu-enter-from,
.mobile-menu-leave-to {
  opacity: 0;
  transform: translateY(-8px);
}

/* Desktop breakpoint */
@media (min-width: 768px) {
  .site-nav__links {
    display: flex;
  }

  .site-nav__cta {
    display: block;
  }

  .site-nav__toggle {
    display: none;
  }

  .site-nav__mobile {
    display: none;
  }
}

/* Large screen refinements */
@media (min-width: 1024px) {
  .site-nav__links {
    gap: var(--space-7);
  }

  .site-nav__link {
    font-size: var(--font-size-body);
  }
}
</style>
