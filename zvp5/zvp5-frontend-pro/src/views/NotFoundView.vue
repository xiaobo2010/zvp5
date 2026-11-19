<script setup lang="ts">
import { useRouter } from 'vue-router'

const router = useRouter()
</script>

<template>
  <div class="not-found-page">
    <!-- 404 state card -->
    <article class="glass-card not-found-card surface-enter" :style="{ '--i': 0 }">
      <div class="not-found-content">
        <div class="not-found-hero">
          <svg class="not-found-icon" width="96" height="96" viewBox="0 0 96 96" fill="none" aria-hidden="true">
            <circle cx="48" cy="48" r="44" stroke="currentColor" stroke-width="2.5" opacity="0.3"/>
            <path d="M48 30V52M48 64V68" stroke="currentColor" stroke-width="4" stroke-linecap="round"/>
          </svg>
          <h1 class="not-found-title">页面不存在</h1>
          <p class="not-found-description">
            抱歉，找不到您访问的页面。可能是页面已移除，或者链接地址有误。
          </p>
        </div>

        <div class="not-found-actions">
          <button
            class="liquid-btn liquid-btn--primary"
            type="button"
            @click="router.push('/')"
          >
            <svg width="18" height="18" viewBox="0 0 18 18" fill="none" aria-hidden="true">
              <path d="M9 2L3 7V16H7V12H11V16H15V7L9 2Z" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/>
            </svg>
            返回首页
          </button>
          <button
            class="liquid-btn liquid-btn--secondary"
            type="button"
            @click="router.push('/notifications')"
          >
            <svg width="18" height="18" viewBox="0 0 18 18" fill="none" aria-hidden="true">
              <path d="M9 3V9L12 12" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
              <circle cx="9" cy="9" r="6.5" stroke="currentColor" stroke-width="1.5"/>
            </svg>
            查看通知
          </button>
        </div>
      </div>
    </article>
  </div>
</template>

<style scoped>
.not-found-page {
  min-height: 100vh;
  padding: var(--space-8) var(--content-padding);
  display: flex;
  align-items: center;
  justify-content: center;
}

/* Frosted glass card base */
.glass-card {
  position: relative;
  background: var(--glass-bg);
  -webkit-backdrop-filter: var(--glass-blur);
  backdrop-filter: var(--glass-blur);
  border: 1px solid var(--glass-border);
  border-radius: var(--radius-xl);
  box-shadow: var(--glass-shadow);
  isolation: isolate;
}

.glass-card::before {
  content: '';
  position: absolute;
  inset: 0;
  background: var(--glass-highlight);
  border-radius: var(--radius-xl);
  pointer-events: none;
  z-index: 0;
}

.glass-card > * {
  position: relative;
  z-index: 1;
}

/* Not found card */
.not-found-card {
  width: 100%;
  max-width: 560px;
  padding: var(--space-10) var(--space-6);
}

.not-found-content {
  display: flex;
  flex-direction: column;
  gap: var(--space-7);
}

.not-found-hero {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-4);
  text-align: center;
}

.not-found-icon {
  color: var(--color-text-tertiary);
  opacity: 0.6;
  margin-bottom: var(--space-2);
}

.not-found-title {
  font-size: var(--font-size-title-2);
  font-weight: 700;
  color: var(--color-text-primary);
  letter-spacing: var(--letter-spacing-tight);
  line-height: var(--line-height-tight);
  margin: 0;
}

.not-found-description {
  max-width: 42ch;
  font-size: var(--font-size-body);
  color: var(--color-text-secondary);
  line-height: var(--line-height-relaxed);
  margin: 0;
}

.not-found-actions {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

/* Liquid button */
.liquid-btn {
  position: relative;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: var(--space-2);
  padding: var(--space-3) var(--space-5);
  border: 1px solid;
  border-radius: var(--radius-md);
  font-size: var(--font-size-body);
  font-weight: 500;
  cursor: pointer;
  transition:
    transform var(--duration-fast) var(--ease-spring),
    box-shadow var(--duration-fast) var(--ease-smooth);
  overflow: hidden;
  isolation: isolate;
  min-height: 44px;
}

.liquid-btn::before {
  content: '';
  position: absolute;
  inset: 0;
  background: var(--glass-highlight);
  border-radius: var(--radius-md);
  pointer-events: none;
  opacity: 0.3;
}

.liquid-btn::after {
  content: '';
  position: absolute;
  inset: 0;
  background: var(--glass-sheen);
  border-radius: var(--radius-md);
  opacity: 0.22;
  transform: translateX(-38%);
  transition:
    opacity var(--duration-base) var(--ease-smooth),
    transform var(--duration-base) var(--ease-smooth);
  pointer-events: none;
}

.liquid-btn:hover::after {
  opacity: 1;
  transform: translateX(100%);
}

.liquid-btn:active {
  transform: scale(0.96);
}

.liquid-btn:focus-visible {
  outline: 2px solid var(--color-brand);
  outline-offset: 2px;
}

.liquid-btn svg {
  position: relative;
  z-index: 1;
}

.liquid-btn--primary {
  background: linear-gradient(135deg, rgba(0, 113, 227, 0.95) 0%, rgba(0, 113, 227, 0.85) 100%);
  -webkit-backdrop-filter: blur(12px) saturate(170%);
  backdrop-filter: blur(12px) saturate(170%);
  border-color: rgba(255, 255, 255, 0.72);
  color: white;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.45), 0 4px 12px rgba(0, 113, 227, 0.3);
}

.liquid-btn--primary:hover {
  background: linear-gradient(135deg, rgba(0, 119, 237, 0.95) 0%, rgba(0, 119, 237, 0.85) 100%);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.55), 0 6px 18px rgba(0, 113, 227, 0.4);
}

.liquid-btn--secondary {
  background: linear-gradient(135deg, rgba(255, 255, 255, 0.76) 0%, rgba(232, 242, 252, 0.56) 100%);
  -webkit-backdrop-filter: blur(12px) saturate(180%);
  backdrop-filter: blur(12px) saturate(180%);
  border-color: rgba(255, 255, 255, 0.88);
  color: var(--color-text-primary);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.96),
    inset 0 -1px 0 rgba(0, 0, 0, 0.06),
    0 3px 10px rgba(18, 48, 82, 0.08);
}

.liquid-btn--secondary:hover {
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.92), inset 0 -1px 0 rgba(0, 0, 0, 0.1), 0 4px 12px rgba(18, 48, 82, 0.1);
  transform: scale(1.02);
}

@media (max-width: 767px) {
  .not-found-page {
    padding: var(--space-6) var(--space-4);
  }

  .not-found-card {
    padding: var(--space-8) var(--space-5);
  }

  .not-found-content {
    gap: var(--space-6);
  }

  .not-found-icon {
    width: 80px;
    height: 80px;
  }
}

@media (prefers-reduced-motion: reduce) {
  .liquid-btn::after {
    display: none;
  }

  .liquid-btn:active {
    transform: none;
  }
}
</style>
