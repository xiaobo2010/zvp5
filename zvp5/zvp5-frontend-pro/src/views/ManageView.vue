<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { fetchNotices, isLoggedIn } from '@/api/canary'

const router = useRouter()

// Placeholder platform statistics
const stats = ref({
  users: 1248,
  classes: 42,
  notifications: 156,
  activities: 387
})
const moduleNotice = ref('')
let noticeTimer: ReturnType<typeof setTimeout> | null = null

const quickLinks = [
  { label: '用户管理', description: '管理用户账户和权限', icon: 'user' },
  { label: '班级管理', description: '管理班级和组织结构', icon: 'group' },
  { label: '通知管理', description: '发布和管理系统通知', icon: 'bell' },
  { label: '活动管理', description: '审核和管理志愿活动', icon: 'activity' }
]

function goBack() {
  router.back()
}

function openModule(label: string) {
  moduleNotice.value = `${label}模块将在后续版本开放。`
  if (noticeTimer) clearTimeout(noticeTimer)
  noticeTimer = setTimeout(() => {
    moduleNotice.value = ''
    noticeTimer = null
  }, 2600)
}

onMounted(async () => {
  if (!isLoggedIn()) return
  try {
    const notices = await fetchNotices()
    stats.value.notifications = notices.notifications?.length ?? 0
  } catch {
    /* the summary is a prototype; keep the representative fallback */
  }
})
</script>

<template>
  <div class="manage">
    <div class="manage__container">
      <header class="manage__header surface-enter" style="--i: 0">
        <button
          type="button"
          class="manage__back"
          aria-label="返回"
          @click="goBack"
        >
          <svg
            width="20"
            height="20"
            viewBox="0 0 20 20"
            fill="none"
            aria-hidden="true"
          >
            <path
              d="M12 4L6 10L12 16"
              stroke="currentColor"
              stroke-width="1.5"
              stroke-linecap="round"
              stroke-linejoin="round"
            />
          </svg>
        </button>
        <div>
          <h1 class="manage__title">平台管理</h1>
          <p class="manage__subtitle">查看平台概况和快速访问管理功能</p>
        </div>
      </header>

      <div class="stats-grid surface-enter" style="--i: 1">
        <div class="stat-card">
          <div class="stat-card__value">{{ stats.users }}</div>
          <div class="stat-card__label">用户</div>
        </div>
        <div class="stat-card">
          <div class="stat-card__value">{{ stats.classes }}</div>
          <div class="stat-card__label">班级</div>
        </div>
        <div class="stat-card">
          <div class="stat-card__value">{{ stats.notifications }}</div>
          <div class="stat-card__label">通知</div>
        </div>
        <div class="stat-card">
          <div class="stat-card__value">{{ stats.activities }}</div>
          <div class="stat-card__label">活动</div>
        </div>
      </div>

      <section class="quick-links surface-enter" style="--i: 2">
        <h2 class="quick-links__title">快速访问</h2>
        <div class="quick-links__grid">
          <button
            v-for="link in quickLinks"
            :key="link.label"
            type="button"
            class="link-card"
            @click="openModule(link.label)"
          >
            <div class="link-card__icon">
              <svg
                v-if="link.icon === 'user'"
                width="24"
                height="24"
                viewBox="0 0 24 24"
                fill="none"
                aria-hidden="true"
              >
                <circle
                  cx="12"
                  cy="8"
                  r="4"
                  stroke="currentColor"
                  stroke-width="1.5"
                  stroke-linecap="round"
                />
                <path
                  d="M6 20C6 16.686 8.686 14 12 14C15.314 14 18 16.686 18 20"
                  stroke="currentColor"
                  stroke-width="1.5"
                  stroke-linecap="round"
                />
              </svg>
              <svg
                v-else-if="link.icon === 'group'"
                width="24"
                height="24"
                viewBox="0 0 24 24"
                fill="none"
                aria-hidden="true"
              >
                <circle
                  cx="9"
                  cy="7"
                  r="3"
                  stroke="currentColor"
                  stroke-width="1.5"
                />
                <circle
                  cx="16"
                  cy="9"
                  r="2.5"
                  stroke="currentColor"
                  stroke-width="1.5"
                />
                <path
                  d="M4 18C4 15.791 5.791 14 8 14H10C12.209 14 14 15.791 14 18"
                  stroke="currentColor"
                  stroke-width="1.5"
                  stroke-linecap="round"
                />
                <path
                  d="M15 18C15 16.343 16.343 15 18 15H19"
                  stroke="currentColor"
                  stroke-width="1.5"
                  stroke-linecap="round"
                />
              </svg>
              <svg
                v-else-if="link.icon === 'bell'"
                width="24"
                height="24"
                viewBox="0 0 24 24"
                fill="none"
                aria-hidden="true"
              >
                <path
                  d="M9.5 19C9.5 20.381 10.619 21.5 12 21.5C13.381 21.5 14.5 20.381 14.5 19"
                  stroke="currentColor"
                  stroke-width="1.5"
                  stroke-linecap="round"
                />
                <path
                  d="M5 10C5 6.134 8.134 3 12 3C15.866 3 19 6.134 19 10C19 13.5 19.5 15.5 20.5 17H3.5C4.5 15.5 5 13.5 5 10Z"
                  stroke="currentColor"
                  stroke-width="1.5"
                  stroke-linecap="round"
                  stroke-linejoin="round"
                />
              </svg>
              <svg
                v-else-if="link.icon === 'activity'"
                width="24"
                height="24"
                viewBox="0 0 24 24"
                fill="none"
                aria-hidden="true"
              >
                <rect
                  x="3"
                  y="4"
                  width="18"
                  height="16"
                  rx="2"
                  stroke="currentColor"
                  stroke-width="1.5"
                />
                <path
                  d="M3 9H21"
                  stroke="currentColor"
                  stroke-width="1.5"
                />
                <circle
                  cx="8"
                  cy="14"
                  r="1"
                  fill="currentColor"
                />
                <circle
                  cx="12"
                  cy="14"
                  r="1"
                  fill="currentColor"
                />
                <circle
                  cx="16"
                  cy="14"
                  r="1"
                  fill="currentColor"
                />
              </svg>
            </div>
            <div class="link-card__content">
              <div class="link-card__label">{{ link.label }}</div>
              <div class="link-card__description">{{ link.description }}</div>
            </div>
            <svg
              class="link-card__arrow"
              width="20"
              height="20"
              viewBox="0 0 20 20"
              fill="none"
              aria-hidden="true"
            >
              <path
                d="M8 4L14 10L8 16"
                stroke="currentColor"
                stroke-width="1.5"
                stroke-linecap="round"
                stroke-linejoin="round"
              />
            </svg>
          </button>
        </div>
        <p v-if="moduleNotice" class="manage-notice" role="status" aria-live="polite">
          {{ moduleNotice }}
        </p>
      </section>
    </div>
  </div>
</template>

<style scoped>
.manage {
  min-height: 100vh;
  padding: var(--space-7) var(--content-padding) var(--space-10);
}

.manage__container {
  max-width: var(--content-max-width);
  margin: 0 auto;
}

.manage__header {
  display: flex;
  align-items: flex-start;
  gap: var(--space-4);
  margin-bottom: var(--space-7);
}

.manage__back {
  position: relative;
  width: 44px;
  height: 44px;
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

.manage__back::before {
  content: '';
  position: absolute;
  inset: 0;
  background: var(--glass-highlight);
  border-radius: var(--radius-md);
  opacity: 0.6;
  pointer-events: none;
  z-index: -1;
}

.manage__back:hover {
  color: var(--color-brand);
  transform: scale(1.05);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.98),
    inset 0 -1px 0 rgba(0, 0, 0, 0.08),
    0 4px 12px rgba(18, 48, 82, 0.12);
}

.manage__back:active {
  transform: scale(0.95);
}

.manage__back:focus-visible {
  outline: 2px solid var(--color-brand);
  outline-offset: -2px;
}

.manage__title {
  font-size: var(--font-size-title-3);
  font-weight: 600;
  line-height: var(--line-height-snug);
  letter-spacing: var(--letter-spacing-tight);
  color: var(--color-text-primary);
  margin-bottom: var(--space-2);
}

.manage__subtitle {
  font-size: var(--font-size-callout);
  color: var(--color-text-secondary);
  line-height: var(--line-height-normal);
}

/* Stats grid */
.stats-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: var(--space-5);
  margin-bottom: var(--space-8);
}

.stat-card {
  position: relative;
  background: var(--color-bg-elevated);
  border: 1px solid var(--color-border-hairline);
  border-radius: var(--radius-lg);
  padding: var(--space-6);
  text-align: center;
  box-shadow: var(--shadow-sm);
  transition:
    transform var(--duration-fast) var(--ease-spring),
    box-shadow var(--duration-fast) var(--ease-smooth);
}

.stat-card:hover {
  transform: translateY(-2px);
  box-shadow: var(--shadow-md);
}

.stat-card__value {
  font-size: var(--font-size-stats);
  font-weight: 600;
  line-height: var(--line-height-tight);
  letter-spacing: var(--letter-spacing-tight);
  color: var(--color-brand);
  margin-bottom: var(--space-2);
}

.stat-card__label {
  font-size: var(--font-size-body);
  font-weight: 500;
  color: var(--color-text-secondary);
}

/* Quick links */
.quick-links {
  margin-bottom: var(--space-8);
}

.quick-links__title {
  font-size: var(--font-size-headline);
  font-weight: 600;
  color: var(--color-text-primary);
  margin-bottom: var(--space-5);
  line-height: var(--line-height-snug);
}

.quick-links__grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: var(--space-5);
}

.link-card {
  position: relative;
  background: var(--glass-bg);
  -webkit-backdrop-filter: var(--glass-blur);
  backdrop-filter: var(--glass-blur);
  border: 1px solid var(--glass-border);
  border-radius: var(--radius-xl);
  padding: var(--space-5);
  display: flex;
  align-items: center;
  gap: var(--space-4);
  text-align: left;
  cursor: pointer;
  overflow: hidden;
  isolation: isolate;
  box-shadow: var(--glass-shadow);
  transition:
    transform var(--duration-fast) var(--ease-spring),
    box-shadow var(--duration-fast) var(--ease-smooth);
  min-height: 88px;
}

.link-card::before {
  content: '';
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 50%;
  background: var(--glass-highlight);
  border-radius: var(--radius-xl) var(--radius-xl) 0 0;
  opacity: 0.4;
  pointer-events: none;
}

.link-card::after {
  content: '';
  position: absolute;
  inset: 0;
  background: var(--glass-sheen);
  opacity: 0;
  transform: translateX(-42%);
  transition:
    opacity var(--duration-base) var(--ease-smooth),
    transform var(--duration-base) var(--ease-smooth);
  pointer-events: none;
}

.manage-notice {
  margin: var(--space-4) 0 0;
  padding: var(--space-3) var(--space-4);
  background: rgba(255, 255, 255, 0.48);
  border: 1px solid rgba(255, 255, 255, 0.7);
  border-radius: var(--radius-md);
  color: var(--color-text-secondary);
  font-size: var(--font-size-callout);
  text-align: center;
}

.link-card:hover {
  transform: translateX(4px);
  box-shadow:
    0 24px 60px rgba(28, 68, 120, 0.12),
    0 4px 8px rgba(28, 68, 120, 0.06),
    inset 0 1px 0 rgba(255, 255, 255, 0.98),
    inset 0 -1px 0 rgba(255, 255, 255, 0.4);
}

.link-card:hover::after {
  opacity: 0.75;
  transform: translateX(100%);
}

.link-card:active {
  transform: translateX(2px);
}

.link-card:focus-visible {
  outline: 2px solid var(--color-brand);
  outline-offset: 2px;
}

.link-card__icon {
  width: 48px;
  height: 48px;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(0, 113, 227, 0.08);
  border-radius: var(--radius-md);
  color: var(--color-brand);
  position: relative;
  z-index: 1;
}

.link-card__content {
  flex: 1;
  position: relative;
  z-index: 1;
}

.link-card__label {
  font-size: var(--font-size-body-large);
  font-weight: 600;
  color: var(--color-text-primary);
  margin-bottom: var(--space-1);
  line-height: var(--line-height-snug);
}

.link-card__description {
  font-size: var(--font-size-callout);
  color: var(--color-text-secondary);
  line-height: var(--line-height-normal);
}

.link-card__arrow {
  flex-shrink: 0;
  color: var(--color-text-tertiary);
  transition: transform var(--duration-fast) var(--ease-spring);
  position: relative;
  z-index: 1;
}

.link-card:hover .link-card__arrow {
  transform: translateX(4px);
  color: var(--color-brand);
}

@media (prefers-reduced-motion: reduce) {
  .manage__back,
  .stat-card,
  .link-card,
  .link-card__arrow {
    transition: none;
  }

  .manage__back:hover,
  .stat-card:hover,
  .manage-notice {
  margin: var(--space-4) 0 0;
  padding: var(--space-3) var(--space-4);
  background: rgba(255, 255, 255, 0.48);
  border: 1px solid rgba(255, 255, 255, 0.7);
  border-radius: var(--radius-md);
  color: var(--color-text-secondary);
  font-size: var(--font-size-callout);
  text-align: center;
}

.link-card:hover {
    transform: none;
  }

  .link-card::after {
    display: none;
  }

  .link-card:hover .link-card__arrow {
    transform: none;
  }
}

@supports not (backdrop-filter: blur(14px)) {
  .link-card {
    background: rgba(255, 255, 255, 0.92);
  }

  .manage__back {
    background: rgba(255, 255, 255, 0.92);
  }
}
</style>
