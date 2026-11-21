<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { CanaryApiError, fetchNotices, isLoggedIn } from '@/api/canary'
import type { CanaryNotice } from '@/api/canary'
import { renderMarkdown } from '@/utils/markdown'

const router = useRouter()

const notices = ref<CanaryNotice[]>([])
const loading = ref(true)
const error = ref('')
const unauthenticated = ref(false)
const expandedId = ref('')

function readableError(reason: unknown, fallback: string): string {
  return reason instanceof CanaryApiError ? reason.message : fallback
}

function isAuthError(reason: unknown): boolean {
  if (reason instanceof CanaryApiError && reason.status === 401) {
    unauthenticated.value = true
    notices.value = []
    return true
  }
  return false
}

async function loadNotices() {
  if (!isLoggedIn()) {
    unauthenticated.value = true
    loading.value = false
    return
  }

  loading.value = true
  error.value = ''
  try {
    const result = await fetchNotices()
    notices.value = result.notifications ?? []
    if (expandedId.value && !notices.value.some((notice) => notice.id === expandedId.value)) {
      expandedId.value = ''
    }
  } catch (reason) {
    if (!isAuthError(reason)) {
      notices.value = []
      error.value = readableError(reason, '通知加载失败，请稍后重试。')
    }
  } finally {
    loading.value = false
  }
}

function formatTime(value: string | null): string {
  if (!value) return '时间未知'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

function toggleNotice(id: string) {
  expandedId.value = expandedId.value === id ? '' : id
}

function handleRefresh() {
  if (loading.value) return
  void loadNotices()
}

onMounted(() => {
  void loadNotices()
})
</script>

<template>
  <div class="notifications-page">
    <!-- Page header with frosted glass -->
    <header class="page-header glass-card surface-enter" :style="{ '--i': 0 }">
      <div class="page-header__lead">
        <button class="back-btn" type="button" aria-label="返回首页" @click="router.push('/')">
          <svg width="18" height="18" viewBox="0 0 20 20" fill="none" aria-hidden="true">
            <path d="M12 4L6 10L12 16" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
          </svg>
        </button>
        <div class="page-header__content">
          <p class="page-header__eyebrow">通知中心</p>
          <h1 class="page-header__title">通知</h1>
          <p class="page-header__subtitle">
            {{ loading && !notices.length ? '正在同步通知…' : `共 ${notices.length} 条通知` }}
          </p>
        </div>
      </div>
      <div class="page-header__actions">
        <button
          class="liquid-btn liquid-btn--secondary liquid-btn--sm"
          type="button"
          :disabled="loading"
          @click="handleRefresh"
        >
          {{ loading ? '刷新中…' : '刷新' }}
        </button>
      </div>
    </header>

    <!-- Notices list with frosted glass -->
    <div
      v-if="unauthenticated"
      class="empty-state glass-card surface-enter"
      :style="{ '--i': 1 }"
      role="alert"
    >
      <p class="empty-title">需要登录</p>
      <p class="empty-description">登录后即可查看站内通知。</p>
      <RouterLink
        class="liquid-btn liquid-btn--primary liquid-btn--sm"
        to="/login?redirect=/notifications"
      >
        去登录
      </RouterLink>
    </div>

    <div
      v-else-if="error"
      class="empty-state glass-card surface-enter"
      :style="{ '--i': 1 }"
      role="alert"
    >
      <p class="empty-title">通知加载失败</p>
      <p class="empty-description">{{ error }}</p>
      <button
        class="liquid-btn liquid-btn--secondary liquid-btn--sm"
        type="button"
        :disabled="loading"
        @click="handleRefresh"
      >
        重试
      </button>
    </div>

    <div
      v-else-if="loading && !notices.length"
      class="empty-state glass-card surface-enter"
      :style="{ '--i': 1 }"
      role="status"
      aria-live="polite"
    >
      <p class="empty-title">正在加载通知</p>
      <p class="empty-description">正在从 canary 同步最新公告…</p>
    </div>

    <div v-else-if="notices.length === 0" class="empty-state glass-card surface-enter" :style="{ '--i': 1 }">
      <svg class="empty-icon" width="64" height="64" viewBox="0 0 64 64" fill="none" aria-hidden="true">
        <circle cx="32" cy="32" r="30" stroke="currentColor" stroke-width="2" opacity="0.3"/>
        <path d="M32 20V34M32 42V44" stroke="currentColor" stroke-width="3" stroke-linecap="round"/>
      </svg>
      <p class="empty-title">暂时没有新通知</p>
      <p class="empty-description">有新的公告或提醒时会出现在这里</p>
    </div>

    <div v-else class="notices-list">
      <article
        v-for="(notice, index) in notices"
        :key="notice.id"
        class="notice-card glass-card surface-enter"
        :style="{ '--i': index }"
      >
        <button
          type="button"
          class="notice-head"
          :aria-expanded="expandedId === notice.id"
          @click="toggleNotice(notice.id)"
        >
          <h2 class="notice-title">{{ notice.title }}</h2>
          <p class="notice-meta">
            <span class="notice-author">{{ notice.author_name }}</span>
            <span class="notice-date">{{ formatTime(notice.created_at) }}</span>
          </p>
          <svg
            class="notice-caret"
            :class="{ 'is-open': expandedId === notice.id }"
            viewBox="0 0 24 24"
            width="20"
            height="20"
            aria-hidden="true"
          >
            <path
              fill="none"
              stroke="currentColor"
              stroke-width="2"
              stroke-linecap="round"
              stroke-linejoin="round"
              d="m6 9 6 6 6-6"
            />
          </svg>
        </button>
        <Transition name="notice-expand">
          <div v-if="expandedId === notice.id" class="notice-body">
            <div class="notice-content" v-html="renderMarkdown(notice.content)"></div>
          </div>
        </Transition>
      </article>
    </div>
  </div>
</template>

<style scoped>
.notifications-page {
  min-height: 100vh;
  padding: var(--space-6) var(--content-padding) var(--space-10);
  max-width: var(--content-max-width);
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
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-5);
  flex-wrap: wrap;
}

.page-header__lead {
  display: flex;
  align-items: flex-start;
  gap: var(--space-4);
  flex: 1;
  min-width: 200px;
}

.page-header__content {
  flex: 1;
  min-width: 200px;
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
  margin: 0 0 var(--space-1);
}

.page-header__subtitle {
  font-size: var(--font-size-callout);
  color: var(--color-text-secondary);
  margin: 0;
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
  font-weight: 500;
  cursor: pointer;
  transition:
    transform var(--duration-fast) var(--ease-spring),
    box-shadow var(--duration-fast) var(--ease-smooth);
  overflow: hidden;
  isolation: isolate;
  min-height: 44px;
}

.liquid-btn--primary {
  background: linear-gradient(135deg, rgba(0, 113, 227, 0.96) 0%, rgba(0, 113, 227, 0.86) 100%);
  -webkit-backdrop-filter: blur(12px) saturate(180%);
  backdrop-filter: blur(12px) saturate(180%);
  border-color: rgba(255, 255, 255, 0.82);
  color: white;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.55),
    0 4px 14px rgba(0, 113, 227, 0.28);
}

.liquid-btn--primary:hover {
  background: linear-gradient(135deg, rgba(0, 119, 237, 0.98) 0%, rgba(0, 119, 237, 0.88) 100%);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.62),
    0 6px 18px rgba(0, 113, 227, 0.36);
  transform: scale(1.02);
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

.liquid-btn--sm {
  padding: var(--space-2) var(--space-4);
  font-size: var(--font-size-caption);
  min-height: 36px;
}

.liquid-btn:disabled {
  opacity: 0.6;
  cursor: default;
  transform: none;
}

/* Empty state */
.empty-state {
  padding: var(--space-10) var(--space-6);
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-4);
  text-align: center;
}

.empty-icon {
  color: var(--color-text-tertiary);
  opacity: 0.6;
}

.empty-title {
  font-size: var(--font-size-headline);
  font-weight: 600;
  color: var(--color-text-primary);
  margin: 0;
}

.empty-description {
  max-width: 44ch;
  font-size: var(--font-size-body);
  color: var(--color-text-secondary);
  line-height: var(--line-height-relaxed);
  margin: 0;
}

/* Notices list */
.notices-list {
  display: grid;
  gap: var(--space-4);
}

.notice-card {
  padding: 0;
  overflow: hidden;
}

.notice-head {
  width: 100%;
  padding: var(--space-5);
  background: transparent;
  border: none;
  text-align: left;
  cursor: pointer;
  display: grid;
  grid-template-columns: 1fr auto;
  grid-template-rows: auto auto;
  gap: var(--space-2) var(--space-3);
  align-items: start;
  transition: background-color var(--duration-fast) var(--ease-smooth);
  position: relative;
  z-index: 1;
}

.notice-head:hover {
  background: rgba(255, 255, 255, 0.2);
}

.notice-head:focus-visible {
  outline: 2px solid var(--color-brand);
  outline-offset: -2px;
}

.notice-title {
  grid-column: 1;
  grid-row: 1;
  font-size: var(--font-size-body-large);
  font-weight: 600;
  color: var(--color-text-primary);
  margin: 0;
  text-align: left;
}

.notice-meta {
  grid-column: 1;
  grid-row: 2;
  display: flex;
  align-items: center;
  gap: var(--space-2);
  font-size: var(--font-size-caption);
  color: var(--color-text-tertiary);
  margin: 0;
}

.notice-author {
  font-weight: 500;
}

.notice-date::before {
  content: '·';
  margin-right: var(--space-2);
}

.notice-caret {
  grid-column: 2;
  grid-row: 1 / span 2;
  align-self: center;
  color: var(--color-text-tertiary);
  flex-shrink: 0;
  transition: transform var(--duration-base) var(--ease-smooth);
}

.notice-caret.is-open {
  transform: rotate(180deg);
}

.notice-body {
  padding: 0 var(--space-5) var(--space-5);
  position: relative;
  z-index: 1;
}

.notice-content {
  font-size: var(--font-size-body);
  color: var(--color-text-secondary);
  line-height: var(--line-height-relaxed);
  margin: 0;
  overflow-wrap: anywhere;
}

.notice-content :deep(p) {
  margin: 0 0 var(--space-3);
}

.notice-content :deep(p:last-child) {
  margin-bottom: 0;
}

.notice-content :deep(h1),
.notice-content :deep(h2),
.notice-content :deep(h3),
.notice-content :deep(h4) {
  color: var(--color-text-primary);
  margin: var(--space-4) 0 var(--space-2);
  line-height: var(--line-height-snug);
}

.notice-content :deep(h1) { font-size: var(--font-size-headline); }
.notice-content :deep(h2) { font-size: var(--font-size-body-large); }
.notice-content :deep(h3),
.notice-content :deep(h4) { font-size: var(--font-size-body); }

.notice-content :deep(ul),
.notice-content :deep(ol) {
  margin: 0 0 var(--space-3);
  padding-left: 1.4em;
}

.notice-content :deep(li) {
  margin: 0.2em 0;
}

.notice-content :deep(blockquote) {
  margin: var(--space-3) 0;
  padding: var(--space-3) var(--space-4);
  background: rgba(255, 255, 255, 0.34);
  border-left: 2px solid var(--color-brand);
  border-radius: 0 var(--radius-sm) var(--radius-sm) 0;
  color: var(--color-text-secondary);
}

.notice-content :deep(code) {
  padding: 2px 5px;
  background: rgba(0, 0, 0, 0.06);
  border-radius: 5px;
  font-size: 0.92em;
}

.notice-content :deep(pre) {
  margin: var(--space-3) 0;
  padding: var(--space-4);
  overflow-x: auto;
  background: rgba(18, 32, 48, 0.06);
  border: 1px solid rgba(255, 255, 255, 0.5);
  border-radius: var(--radius-md);
}

.notice-content :deep(pre code) {
  padding: 0;
  background: transparent;
}

.notice-content :deep(a) {
  color: var(--color-brand);
  text-decoration: underline;
  text-underline-offset: 2px;
}

.notice-content :deep(hr) {
  margin: var(--space-4) 0;
  border: 0;
  border-top: 1px solid var(--color-border-subtle);
}

.notice-content :deep(strong) {
  color: var(--color-text-primary);
}

.notice-expand-enter-active,
.notice-expand-leave-active {
  transition:
    opacity var(--duration-base) var(--ease-smooth),
    max-height var(--duration-base) var(--ease-smooth);
  overflow: hidden;
}

.notice-expand-enter-from,
.notice-expand-leave-to {
  opacity: 0;
  max-height: 0;
}

.notice-expand-enter-to,
.notice-expand-leave-from {
  opacity: 1;
  max-height: 500px;
}

@media (max-width: 767px) {
  .notifications-page {
    padding: var(--space-5) var(--space-4) var(--space-9);
    gap: var(--space-4);
  }

  .page-header {
    padding: var(--space-5);
  }

  .notice-head {
    padding: var(--space-4);
  }

  .notice-body {
    padding: 0 var(--space-4) var(--space-4);
  }
}

@media (prefers-reduced-motion: reduce) {
  .notice-caret {
    transition: none;
  }

  .back-btn:active,
  .liquid-btn:active {
    transform: none;
  }
}
</style>
