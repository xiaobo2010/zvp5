<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { fetchVersion } from '@/api/canary'

const router = useRouter()
const version = ref('ZVMS Pro')

onMounted(() => {
  void fetchVersion()
    .then((value) => {
      if (value) version.value = value
    })
    .catch(() => {
      /* version is decorative; keep the fallback */
    })
})
</script>

<template>
  <div class="about-page">
    <!-- Page header with frosted glass -->
    <header class="page-header glass-card surface-enter" :style="{ '--i': 0 }">
      <button class="back-btn" type="button" aria-label="返回首页" @click="router.push('/')">
        <svg width="18" height="18" viewBox="0 0 20 20" fill="none" aria-hidden="true">
          <path d="M12 4L6 10L12 16" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
        </svg>
      </button>
      <div class="page-header__content">
        <p class="page-header__eyebrow">信息</p>
        <h1 class="page-header__title">关于 ZVMS</h1>
      </div>
    </header>

    <!-- About content card -->
    <article class="glass-card about-card surface-enter" :style="{ '--i': 1 }">
      <div class="about-hero">
        <div class="about-icon">
          <svg width="48" height="48" viewBox="0 0 48 48" fill="none" aria-hidden="true">
            <rect x="8" y="8" width="32" height="32" rx="8" stroke="currentColor" stroke-width="2.5"/>
            <path d="M16 24L22 30L32 18" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>
          </svg>
        </div>
        <h2 class="about-title">镇海中学义工平台</h2>
        <p class="about-version">{{ version }}</p>
      </div>

      <div class="about-content">
        <p class="about-description">
          镇海中学义工管理系统（ZVMS）是一个服务于学校义工时长管理的平台，帮助学生、教师和管理员高效地记录、审核和统计义工活动。
        </p>

        <div class="about-links">
          <RouterLink class="about-link" to="/notifications">
            <svg width="20" height="20" viewBox="0 0 20 20" fill="none" aria-hidden="true">
              <path d="M10 5V10L13 13" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
              <circle cx="10" cy="10" r="7" stroke="currentColor" stroke-width="1.5"/>
            </svg>
            <span>查看通知</span>
          </RouterLink>
          <RouterLink class="about-link" to="/volunteers">
            <svg width="20" height="20" viewBox="0 0 20 20" fill="none" aria-hidden="true">
              <rect x="3" y="6" width="14" height="11" rx="1.5" stroke="currentColor" stroke-width="1.5"/>
              <path d="M6 6V4.5C6 3.67157 6.67157 3 7.5 3H12.5C13.3284 3 14 3.67157 14 4.5V6" stroke="currentColor" stroke-width="1.5"/>
            </svg>
            <span>义工列表</span>
          </RouterLink>
        </div>
      </div>

      <footer class="about-footer">
        <p class="about-copyright">© 2018–2026 镇海中学 · ZVMS</p>
      </footer>
    </article>
  </div>
</template>

<style scoped>
.about-page {
  min-height: 100vh;
  padding: var(--space-6) var(--content-padding) var(--space-10);
  max-width: 720px;
  margin: 0 auto;
  display: grid;
  gap: var(--space-5);
  align-content: start;
}

/* Frosted glass card base */
.glass-card {
  position: relative;
  background: var(--surface-matte);
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

/* Page header */
.page-header {
  padding: var(--space-6);
  display: flex;
  align-items: center;
  gap: var(--space-4);
}

.back-btn {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 40px;
  height: 40px;
  padding: 0;
  background: linear-gradient(
    135deg,
    rgba(255, 255, 255, 0.74) 0%,
    rgba(232, 242, 252, 0.54) 100%
  );
  -webkit-backdrop-filter: blur(12px) saturate(180%);
  backdrop-filter: blur(12px) saturate(180%);
  border: 1px solid rgba(255, 255, 255, 0.86);
  border-radius: 50%;
  box-shadow:
    inset 0 0.5px 0 rgba(255, 255, 255, 0.85),
    inset 0 -0.5px 0 rgba(0, 0, 0, 0.08),
    0 0 8px rgba(18, 48, 82, 0.06);
  color: var(--color-text-secondary);
  cursor: pointer;
  overflow: hidden;
  isolation: isolate;
  flex-shrink: 0;
  transition:
    transform var(--duration-fast) var(--ease-spring),
    box-shadow var(--duration-fast) var(--ease-smooth),
    color var(--duration-fast) var(--ease-smooth);
}

.back-btn::before {
  content: '';
  position: absolute;
  inset: 0;
  background: radial-gradient(
    circle at 50% 40%,
    rgba(255, 255, 255, 0.35) 0%,
    transparent 70%
  );
  border-radius: 50%;
  pointer-events: none;
}

.back-btn::after {
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
  border-radius: 50%;
  opacity: 0;
  transform: translateX(-100%);
  transition:
    opacity var(--duration-base) var(--ease-smooth),
    transform var(--duration-base) var(--ease-smooth);
  pointer-events: none;
}

.back-btn:hover {
  color: var(--color-text-primary);
  transform: scale(1.05);
  box-shadow:
    inset 0 0.5px 0 rgba(255, 255, 255, 0.92),
    inset 0 -0.5px 0 rgba(0, 0, 0, 0.1),
    0 0 14px rgba(18, 48, 82, 0.1);
}

.back-btn:hover::after {
  opacity: 1;
  transform: translateX(100%);
}

.back-btn:active {
  transform: scale(0.92);
}

.back-btn:focus-visible {
  outline: 2px solid var(--color-brand);
  outline-offset: 2px;
}

.back-btn svg {
  position: relative;
  z-index: 1;
}

.page-header__content {
  flex: 1;
}

.page-header__eyebrow {
  font-size: var(--font-size-callout);
  color: var(--color-text-tertiary);
  font-weight: 500;
  letter-spacing: var(--letter-spacing-wide);
  text-transform: uppercase;
  margin: 0 0 var(--space-1);
}

.page-header__title {
  font-size: var(--font-size-title-3);
  font-weight: 700;
  color: var(--color-text-primary);
  letter-spacing: var(--letter-spacing-tight);
  line-height: var(--line-height-tight);
  margin: 0;
}

/* About card */
.about-card {
  padding: var(--space-8) var(--space-6);
  display: flex;
  flex-direction: column;
  gap: var(--space-6);
}

.about-hero {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-3);
  text-align: center;
  padding-bottom: var(--space-5);
  border-bottom: 1px solid var(--color-border-subtle);
}

.about-icon {
  width: 80px;
  height: 80px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, rgba(0, 113, 227, 0.12) 0%, rgba(0, 113, 227, 0.06) 100%);
  border: 1px solid rgba(0, 113, 227, 0.2);
  border-radius: var(--radius-lg);
  color: var(--color-brand);
}

.about-title {
  font-size: var(--font-size-headline);
  font-weight: 700;
  color: var(--color-text-primary);
  letter-spacing: var(--letter-spacing-normal);
  margin: 0;
}

.about-version {
  font-size: var(--font-size-callout);
  color: var(--color-text-tertiary);
  font-weight: 500;
  padding: 4px var(--space-3);
  background: rgba(0, 0, 0, 0.04);
  border-radius: var(--radius-pill);
  margin: 0;
}

.about-content {
  display: flex;
  flex-direction: column;
  gap: var(--space-5);
}

.about-description {
  font-size: var(--font-size-body);
  color: var(--color-text-secondary);
  line-height: var(--line-height-relaxed);
  margin: 0;
  text-align: center;
}

.about-links {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.about-link {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-4);
  background: linear-gradient(
    135deg,
    rgba(255, 255, 255, 0.66) 0%,
    rgba(232, 242, 252, 0.46) 100%
  );
  -webkit-backdrop-filter: blur(10px) saturate(170%);
  backdrop-filter: blur(10px) saturate(170%);
  border: 1px solid rgba(255, 255, 255, 0.8);
  border-radius: var(--radius-lg);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.9),
    0 2px 6px rgba(18, 48, 82, 0.04);
  color: var(--color-text-primary);
  font-size: var(--font-size-body);
  font-weight: 500;
  text-decoration: none;
  transition:
    background-color var(--duration-fast) var(--ease-smooth),
    border-color var(--duration-fast) var(--ease-smooth),
    transform var(--duration-fast) var(--ease-spring);
}

.about-link:hover {
  background: rgba(255, 255, 255, 0.82);
  border-color: rgba(255, 255, 255, 0.95);
  transform: translateX(2px);
}

.about-link:active {
  transform: scale(0.98);
}

.about-link:focus-visible {
  outline: 2px solid var(--color-brand);
  outline-offset: 2px;
}

.about-link svg {
  flex-shrink: 0;
  color: var(--color-brand);
}

.about-footer {
  padding-top: var(--space-5);
  border-top: 1px solid var(--color-border-subtle);
  text-align: center;
}

.about-copyright {
  font-size: var(--font-size-caption);
  color: var(--color-text-tertiary);
  margin: 0;
}

@media (max-width: 767px) {
  .about-page {
    padding: var(--space-5) var(--space-4) var(--space-9);
    gap: var(--space-4);
  }

  .page-header {
    padding: var(--space-5);
  }

  .about-card {
    padding: var(--space-6) var(--space-5);
    gap: var(--space-5);
  }
}

@media (prefers-reduced-motion: reduce) {
  .back-btn:active,
  .about-link:active {
    transform: none;
  }

  .about-link:hover {
    transform: none;
  }
}
</style>
