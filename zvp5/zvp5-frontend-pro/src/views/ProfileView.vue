<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import {
  CanaryApiError,
  clearSession,
  currentUserId,
  fetchGroupName,
  fetchNotices,
  fetchProfile,
  isLoggedIn
} from '@/api/canary'
import type { CanaryNotice, CanaryUser } from '@/api/canary'

const router = useRouter()

const profile = ref<CanaryUser | null>(null)
const className = ref('')
const notices = ref<CanaryNotice[]>([])
const loading = ref(true)
const unauthenticated = ref(false)
const error = ref('')
const noticesLoading = ref(false)
const noticesError = ref('')
const openNoticeId = ref('')
const actionMessage = ref('')
let actionTimer: ReturnType<typeof setTimeout> | null = null

const ROLE_LABELS: Record<string, string> = {
  student: '学生',
  volunteer: '志愿者',
  admin: '管理员',
  department: '部门',
  secretary: '秘书',
  auditor: '审计',
  teacher: '教师',
  system: '系统'
}

const displayRoles = computed(() => {
  const roles = (profile.value?.roles ?? []) as string[]
  const source = roles.length ? roles : ['student']
  return source.map((role) => ROLE_LABELS[role] ?? role)
})

const initials = computed(() => profile.value?.name?.slice(-2) || '—')

const classDisplay = computed(() => {
  if (className.value) return className.value
  if (profile.value?.class_id) return profile.value.class_id
  return '未设置班级'
})

const statusLabel = computed(() => (profile.value?.banned ? '已封禁' : '正常'))

function readableError(reason: unknown, fallback: string): string {
  return reason instanceof CanaryApiError ? reason.message : fallback
}

function isAuthError(reason: unknown): boolean {
  if (reason instanceof CanaryApiError && reason.status === 401) {
    unauthenticated.value = true
    profile.value = null
    return true
  }
  return false
}

async function loadProfile(): Promise<boolean> {
  try {
    const data = await fetchProfile()
    profile.value = data
    className.value = data.class_id ? await fetchGroupName(data.class_id) : ''
    return true
  } catch (reason) {
    if (isAuthError(reason)) return false
    error.value = readableError(reason, '个人资料加载失败，请稍后重试。')
    return false
  }
}

async function loadNotices(): Promise<boolean> {
  if (unauthenticated.value) return false
  noticesLoading.value = true
  noticesError.value = ''
  try {
    const result = await fetchNotices()
    notices.value = result.notifications ?? []
    return true
  } catch (reason) {
    if (isAuthError(reason)) return false
    noticesError.value = readableError(reason, '通知加载失败，请稍后重试。')
    return false
  } finally {
    noticesLoading.value = false
  }
}

async function loadPage() {
  loading.value = true
  error.value = ''
  const profileOk = await loadProfile()
  loading.value = false
  if (profileOk) void loadNotices()
}

function toggleNotice(id: string) {
  openNoticeId.value = openNoticeId.value === id ? '' : id
}

function formatNoticeTime(value: string | null): string {
  if (!value) return '时间未知'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  const pad = (part: number) => String(part).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

function announceAction(message: string) {
  actionMessage.value = message
  if (actionTimer) clearTimeout(actionTimer)
  actionTimer = setTimeout(() => {
    actionMessage.value = ''
    actionTimer = null
  }, 2800)
}

function handleRefreshNotices() {
  if (noticesLoading.value) return
  void (async () => {
    const ok = await loadNotices()
    if (ok) announceAction('通知已刷新。')
    else if (!unauthenticated.value) announceAction('通知刷新失败，请稍后重试。')
  })()
}

function handleRetry() {
  if (loading.value) return
  void loadPage()
}

function handleChangePassword() {
  announceAction('修改密码功能将在安全设置中提供。')
}

function handleLogout() {
  clearSession()
  void router.replace('/login')
}

function handleAbout() {
  void router.push('/about')
}

onMounted(() => {
  if (!isLoggedIn() || !currentUserId()) {
    unauthenticated.value = true
    loading.value = false
    return
  }
  void loadPage()
})

onUnmounted(() => {
  if (actionTimer) clearTimeout(actionTimer)
})
</script>

<template>
  <div class="profile-page">
    <header class="profile-header">
      <button class="back-btn" @click="router.push('/')" aria-label="返回首页">
        <svg width="20" height="20" viewBox="0 0 20 20" fill="none" aria-hidden="true">
          <path d="M12 4L6 10L12 16" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
        </svg>
      </button>
      <div class="profile-header__content">
        <p class="profile-header__eyebrow">账户</p>
        <h1 class="profile-header__title">个人主页</h1>
      </div>
    </header>

    <!-- Loading state -->
    <section v-if="loading" class="glass-card state-card surface-enter" :style="{ '--i': 0 }" role="status" aria-live="polite">
      <span class="state-spinner" aria-hidden="true"></span>
      <p class="state-title">正在加载个人资料</p>
      <p class="state-description">正在从 canary 同步账号信息…</p>
    </section>

    <!-- Unauthenticated state -->
    <section v-else-if="unauthenticated" class="glass-card state-card surface-enter" :style="{ '--i': 0 }" role="alert">
      <h2 class="state-title">需要登录</h2>
      <p class="state-description">登录后即可查看个人资料、班级与站内通知。</p>
      <RouterLink class="liquid-btn liquid-btn--primary" to="/login?redirect=/me">
        去登录
      </RouterLink>
    </section>

    <!-- API error state -->
    <section v-else-if="error" class="glass-card state-card surface-enter" :style="{ '--i': 0 }" role="alert">
      <h2 class="state-title">个人资料加载失败</h2>
      <p class="state-description">{{ error }}</p>
      <button
        class="liquid-btn liquid-btn--secondary"
        type="button"
        :disabled="loading"
        @click="handleRetry"
      >
        重新加载
      </button>
    </section>

    <template v-else-if="profile">
      <!-- Profile hero card with frosted glass -->
      <section class="glass-card profile-hero-card surface-enter" :style="{ '--i': 0 }">
        <div class="profile-hero">
          <div class="profile-avatar">{{ initials }}</div>
          <div class="profile-identity">
            <div class="profile-name-line">
              <span class="profile-name">{{ profile.name }}</span>
              <span v-for="role in displayRoles" :key="role" class="role-badge">
                {{ role }}
              </span>
            </div>
            <span class="profile-class">{{ classDisplay }}</span>
          </div>
          <span class="profile-status" :class="profile.banned ? 'is-banned' : 'is-normal'">
            {{ statusLabel }}
          </span>
        </div>

        <dl class="profile-kv">
          <dt>姓名</dt>
          <dd>{{ profile.name }}</dd>
          <dt>学号</dt>
          <dd>{{ profile.id }}</dd>
          <dt>班级</dt>
          <dd>{{ classDisplay }}</dd>
          <dt>身份</dt>
          <dd class="profile-kv-roles">
            <span v-for="role in displayRoles" :key="role" class="role-badge">{{ role }}</span>
          </dd>
        </dl>
      </section>

      <!-- Notices section with frosted glass -->
      <section class="glass-card notices-card surface-enter" :style="{ '--i': 1 }">
        <div class="notices-head">
          <h2 class="section-title">站内通知</h2>
          <div class="notices-actions">
            <RouterLink
              class="liquid-btn liquid-btn--secondary liquid-btn--sm"
              to="/notifications"
            >
              全部
            </RouterLink>
            <button
              class="liquid-btn liquid-btn--secondary liquid-btn--sm"
              type="button"
              :disabled="noticesLoading"
              @click="handleRefreshNotices"
            >
              {{ noticesLoading ? '刷新中…' : '刷新' }}
            </button>
          </div>
        </div>

        <div
          v-if="noticesLoading && !notices.length"
          class="empty-state"
          role="status"
          aria-live="polite"
        >
          <p class="empty-state__title">正在加载通知</p>
          <p class="empty-state__description">正在获取最新的站内公告…</p>
        </div>
        <div v-else-if="noticesError" class="empty-state" role="alert">
          <p class="empty-state__title">通知加载失败</p>
          <p class="empty-state__description">{{ noticesError }}</p>
          <button
            class="liquid-btn liquid-btn--secondary liquid-btn--sm"
            type="button"
            :disabled="noticesLoading"
            @click="handleRefreshNotices"
          >
            重试
          </button>
        </div>
        <ul v-else-if="notices.length" class="notice-list">
          <li v-for="notice in notices" :key="notice.id" class="notice-item">
            <button
              type="button"
              class="notice-head"
              :aria-expanded="openNoticeId === notice.id"
              @click="toggleNotice(notice.id)"
            >
              <span class="notice-title">{{ notice.title }}</span>
              <span class="notice-meta">
                {{ notice.author_name || '系统' }} · {{ formatNoticeTime(notice.created_at) }}
              </span>
              <svg
                class="notice-caret"
                :class="{ 'is-open': openNoticeId === notice.id }"
                viewBox="0 0 24 24"
                width="16"
                height="16"
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
            <div v-if="openNoticeId === notice.id" class="notice-body">
              <p>{{ notice.content }}</p>
            </div>
          </li>
        </ul>
        <div v-else class="empty-state">
          <p class="empty-state__title">暂时没有新通知</p>
          <p class="empty-state__description">有新的公告或提醒时会出现在这里</p>
        </div>
      </section>

      <!-- Account actions with frosted glass -->
      <section class="glass-card actions-card surface-enter" :style="{ '--i': 2 }">
        <button class="liquid-btn liquid-btn--primary liquid-btn--full" type="button" @click="handleChangePassword">
          修改密码
        </button>
        <button class="liquid-btn liquid-btn--neutral liquid-btn--full" type="button" @click="handleAbout">
          关于 ZVMS
        </button>
        <button class="liquid-btn liquid-btn--danger liquid-btn--full" type="button" @click="handleLogout">
          退出登录
        </button>
      </section>
    </template>

    <Transition name="action-feedback">
      <p v-if="actionMessage" class="action-feedback" role="status" aria-live="polite">
        {{ actionMessage }}
      </p>
    </Transition>
  </div>
</template>

<style scoped>
.profile-page {
  min-height: 100vh;
  padding: var(--space-6) var(--content-padding) var(--space-10);
  max-width: var(--content-max-width);
  margin: 0 auto;
  display: grid;
  gap: var(--space-6);
}

.profile-header {
  display: flex;
  align-items: center;
  gap: var(--space-4);
  margin-bottom: var(--space-2);
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
  color: var(--color-text-secondary);
  cursor: pointer;
  transition:
    transform var(--duration-fast) var(--ease-spring),
    box-shadow var(--duration-fast) var(--ease-smooth),
    color var(--duration-fast) var(--ease-smooth);
  overflow: hidden;
  isolation: isolate;
  box-shadow:
    inset 0 0.5px 0 rgba(255, 255, 255, 0.85),
    inset 0 -0.5px 0 rgba(0, 0, 0, 0.08),
    0 0 8px rgba(18, 48, 82, 0.06);
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

.profile-header__content {
  flex: 1;
}

.profile-header__eyebrow {
  font-size: var(--font-size-callout);
  color: var(--color-text-tertiary);
  font-weight: 500;
  letter-spacing: var(--letter-spacing-wide);
  text-transform: uppercase;
  margin: 0 0 var(--space-1);
}

.profile-header__title {
  font-size: var(--font-size-title-3);
  font-weight: 700;
  color: var(--color-text-primary);
  letter-spacing: var(--letter-spacing-tight);
  line-height: var(--line-height-tight);
  margin: 0;
}

/* Frosted glass card base */
.glass-card {
  position: relative;
  background: var(--glass-bg);
  -webkit-backdrop-filter: var(--glass-blur);
  backdrop-filter: var(--glass-blur);
  border: 1px solid var(--glass-border);
  border-radius: var(--radius-xl);
  padding: var(--space-6);
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

/* Profile hero */
.profile-hero {
  display: flex;
  align-items: center;
  gap: var(--space-5);
  padding-bottom: var(--space-5);
  border-bottom: 1px solid var(--color-border-subtle);
  flex-wrap: wrap;
}

.profile-avatar {
  width: 72px;
  height: 72px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, rgba(0, 113, 227, 0.92) 0%, rgba(0, 113, 227, 0.78) 100%);
  border: 2px solid var(--glass-border);
  border-radius: 50%;
  color: white;
  font-size: 28px;
  font-weight: 600;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.45), 0 4px 16px rgba(0, 113, 227, 0.25);
  position: relative;
  isolation: isolate;
}

.profile-avatar::before {
  content: '';
  position: absolute;
  inset: 0;
  background: radial-gradient(circle at 30% 30%, rgba(255, 255, 255, 0.4) 0%, transparent 60%);
  border-radius: 50%;
  pointer-events: none;
}

.profile-identity {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  min-width: 200px;
}

.profile-name-line {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-wrap: wrap;
}

.profile-name {
  font-size: var(--font-size-headline);
  font-weight: 700;
  color: var(--color-text-primary);
  letter-spacing: var(--letter-spacing-normal);
}

.profile-class {
  font-size: var(--font-size-callout);
  color: var(--color-text-secondary);
}

.profile-status {
  padding: var(--space-2) var(--space-4);
  border-radius: var(--radius-pill);
  font-size: var(--font-size-caption);
  font-weight: 600;
  letter-spacing: var(--letter-spacing-wide);
  text-transform: uppercase;
}

.profile-status.is-normal {
  background: rgba(52, 211, 153, 0.15);
  color: rgb(6, 95, 70);
}

.profile-status.is-banned {
  background: rgba(248, 113, 113, 0.15);
  color: rgb(153, 27, 27);
}

.role-badge {
  padding: 4px 10px;
  background: rgba(0, 113, 227, 0.12);
  border: 1px solid rgba(0, 113, 227, 0.24);
  border-radius: var(--radius-pill);
  font-size: var(--font-size-caption);
  font-weight: 500;
  color: var(--color-brand);
  white-space: nowrap;
}

/* Key-value list */
.profile-kv {
  display: grid;
  grid-template-columns: 80px 1fr;
  gap: var(--space-3) var(--space-5);
  margin-top: var(--space-5);
}

.profile-kv dt {
  font-size: var(--font-size-callout);
  color: var(--color-text-tertiary);
  font-weight: 500;
}

.profile-kv dd {
  font-size: var(--font-size-callout);
  color: var(--color-text-primary);
  font-weight: 400;
  margin: 0;
}

.profile-kv-roles {
  display: flex;
  gap: var(--space-2);
  flex-wrap: wrap;
}

/* Notices section */
.notices-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-4);
  margin-bottom: var(--space-5);
  flex-wrap: wrap;
}

.notices-actions {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

.section-title {
  font-size: var(--font-size-headline);
  font-weight: 700;
  color: var(--color-text-primary);
  letter-spacing: var(--letter-spacing-normal);
  margin: 0;
}

.notice-list {
  list-style: none;
  padding: 0;
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.notice-item {
  border-radius: var(--radius-md);
  background: rgba(255, 255, 255, 0.12);
  border: 1px solid var(--color-border-subtle);
  overflow: hidden;
}

.notice-head {
  width: 100%;
  padding: var(--space-4);
  background: linear-gradient(
    135deg,
    rgba(255, 255, 255, 0.66) 0%,
    rgba(232, 242, 252, 0.46) 100%
  );
  -webkit-backdrop-filter: blur(10px) saturate(170%);
  backdrop-filter: blur(10px) saturate(170%);
  border: 1px solid rgba(255, 255, 255, 0.8);
  border-radius: var(--radius-md);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.9),
    0 2px 6px rgba(18, 48, 82, 0.04);
  text-align: left;
  cursor: pointer;
  display: grid;
  grid-template-columns: 1fr auto;
  grid-template-rows: auto auto;
  gap: var(--space-2) var(--space-3);
  align-items: center;
  transition:
    background-color var(--duration-fast) var(--ease-smooth),
    border-color var(--duration-fast) var(--ease-smooth);
}

.notice-head:hover {
  background: rgba(255, 255, 255, 0.82);
  border-color: rgba(255, 255, 255, 0.95);
}

.notice-head:focus-visible {
  outline: 2px solid var(--color-brand);
  outline-offset: -2px;
}

.notice-title {
  grid-column: 1;
  grid-row: 1;
  font-size: var(--font-size-body);
  font-weight: 600;
  color: var(--color-text-primary);
}

.notice-meta {
  grid-column: 1;
  grid-row: 2;
  font-size: var(--font-size-caption);
  color: var(--color-text-tertiary);
}

.notice-caret {
  grid-column: 2;
  grid-row: 1 / -1;
  color: var(--color-text-tertiary);
  transition: transform var(--duration-base) var(--ease-spring);
}

.notice-caret.is-open {
  transform: rotate(180deg);
}

.notice-body {
  padding: 0 var(--space-4) var(--space-4);
  font-size: var(--font-size-callout);
  line-height: var(--line-height-relaxed);
  color: var(--color-text-secondary);
}

.notice-body p {
  margin: 0;
}

.empty-state {
  text-align: center;
  padding: var(--space-8) var(--space-4);
}

.empty-state__title {
  font-size: var(--font-size-body);
  font-weight: 600;
  color: var(--color-text-primary);
  margin: 0 0 var(--space-2);
}

.empty-state__description {
  font-size: var(--font-size-callout);
  color: var(--color-text-tertiary);
  margin: 0;
}

.empty-state .liquid-btn {
  margin-top: var(--space-2);
}

/* Loading / error states */
.state-card {
  padding: var(--space-9) var(--space-6);
  text-align: center;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-3);
}

.state-title {
  font-size: var(--font-size-headline);
  font-weight: 600;
  color: var(--color-text-primary);
  margin: 0;
}

.state-description {
  font-size: var(--font-size-callout);
  color: var(--color-text-secondary);
  margin: 0;
  max-width: 46ch;
}

.state-card .liquid-btn {
  margin-top: var(--space-2);
}

.state-spinner {
  width: 32px;
  height: 32px;
  border: 3px solid rgba(0, 113, 227, 0.18);
  border-top-color: var(--color-brand);
  border-radius: 50%;
  animation: state-spin 0.9s linear infinite;
}

@keyframes state-spin {
  to {
    transform: rotate(360deg);
  }
}

/* Action buttons section */
.actions-card {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

/* Liquid glass buttons */
.liquid-btn {
  position: relative;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: var(--space-2);
  padding: var(--space-3) var(--space-5);
  font-size: var(--font-size-callout);
  font-weight: 600;
  border-radius: var(--radius-pill);
  cursor: pointer;
  transition:
    transform var(--duration-fast) var(--ease-spring),
    box-shadow var(--duration-fast) var(--ease-smooth);
  isolation: isolate;
  overflow: hidden;
  border: 1px solid;
  min-height: 44px;
}

.liquid-btn::before {
  content: '';
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 50%;
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.75) 0%, rgba(255, 255, 255, 0) 100%);
  border-radius: var(--radius-pill) var(--radius-pill) 0 0;
  pointer-events: none;
  opacity: 0.5;
}

.liquid-btn::after {
  content: '';
  position: absolute;
  inset: 0;
  background: var(--glass-sheen);
  border-radius: var(--radius-pill);
  opacity: 0;
  transform: translateX(-100%);
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

.liquid-btn--neutral {
  background: linear-gradient(135deg, rgba(110, 110, 115, 0.12) 0%, rgba(156, 163, 175, 0.08) 100%);
  -webkit-backdrop-filter: blur(12px) saturate(160%);
  backdrop-filter: blur(12px) saturate(160%);
  border-color: rgba(110, 110, 115, 0.22);
  color: var(--tone-ink);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.7),
    inset 0 -1px 0 rgba(0, 0, 0, 0.05),
    0 3px 10px rgba(18, 48, 82, 0.06);
}

.liquid-btn--neutral:hover {
  background: linear-gradient(135deg, rgba(110, 110, 115, 0.16) 0%, rgba(156, 163, 175, 0.12) 100%);
  border-color: rgba(110, 110, 115, 0.28);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.75), inset 0 -1px 0 rgba(0, 0, 0, 0.08), 0 4px 12px rgba(18, 48, 82, 0.08);
  transform: scale(1.02);
}

.liquid-btn--danger {
  background: linear-gradient(135deg, rgba(255, 59, 48, 0.92) 0%, rgba(239, 68, 68, 0.86) 100%);
  -webkit-backdrop-filter: blur(12px) saturate(170%);
  backdrop-filter: blur(12px) saturate(170%);
  border-color: rgba(255, 255, 255, 0.7);
  color: white;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.4), 0 4px 12px rgba(255, 59, 48, 0.28);
}

.liquid-btn--danger:hover {
  background: linear-gradient(135deg, rgba(255, 59, 48, 0.95) 0%, rgba(239, 68, 68, 0.9) 100%);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.5), 0 6px 18px rgba(255, 59, 48, 0.35);
}

.liquid-btn--full {
  width: 100%;
}

.liquid-btn--sm {
  padding: var(--space-2) var(--space-4);
  font-size: var(--font-size-caption);
  min-height: 36px;
}

.action-feedback {
  position: fixed;
  left: 50%;
  bottom: calc(env(safe-area-inset-bottom, 0px) + 24px);
  z-index: 1200;
  max-width: min(520px, calc(100vw - 32px));
  padding: var(--space-3) var(--space-5);
  transform: translateX(-50%);
  background: var(--glass-bg);
  -webkit-backdrop-filter: var(--glass-blur);
  backdrop-filter: var(--glass-blur);
  border: 1px solid var(--glass-border);
  border-radius: var(--radius-pill);
  box-shadow: var(--glass-shadow);
  color: var(--color-text-primary);
  font-size: var(--font-size-callout);
  text-align: center;
}

.action-feedback-enter-active,
.action-feedback-leave-active {
  transition: opacity var(--duration-base) var(--ease-smooth),
    transform var(--duration-base) var(--ease-spring);
}

.action-feedback-enter-from,
.action-feedback-leave-to {
  opacity: 0;
  transform: translateX(-50%) translateY(8px);
}

@media (max-width: 767px) {
  .profile-page {
    padding: var(--space-5) var(--space-4) var(--space-9);
    gap: var(--space-5);
  }

  .glass-card {
    padding: var(--space-5);
  }

  .profile-hero {
    gap: var(--space-4);
  }

  .profile-avatar {
    width: 64px;
    height: 64px;
    font-size: 24px;
  }

  .profile-kv {
    grid-template-columns: 70px 1fr;
    gap: var(--space-2) var(--space-4);
  }

  .notice-head {
    padding: var(--space-3);
  }

  .notice-body {
    padding: 0 var(--space-3) var(--space-3);
  }
}

@media (prefers-reduced-motion: reduce) {
  .liquid-btn::after,
  .profile-avatar::before {
    display: none;
  }

  .liquid-btn:active,
  .back-btn:active {
    transform: none;
  }

  .notice-caret {
    transition: none;
  }

  .state-spinner {
    animation: none;
  }
}
</style>
