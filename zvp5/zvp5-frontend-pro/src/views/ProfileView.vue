<script setup lang="ts">
import { ref, computed, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'

const router = useRouter()

// Static profile data matching HomeView
const profile = {
  name: '林知远',
  id: '20240117',
  className: '高二(2)班',
  group: '图书馆志愿者协会',
  roles: ['志愿者', '学生'],
  status: '正常',
  banned: false
}

const initials = computed(() => profile.name.slice(-2))

// Mock notices
const notices = ref([
  {
    id: '1',
    title: '关于寒假志愿活动安排的通知',
    author: '系统管理员',
    time: '2026-11-15 14:30',
    content: '寒假期间将开展多项志愿服务活动，请各位志愿者关注活动通知并积极参与。活动详情将在近期公布，敬请关注。'
  },
  {
    id: '2',
    title: '志愿时长统计已更新',
    author: '系统管理员',
    time: '2026-11-10 09:15',
    content: '本学期志愿时长统计已完成更新，请各位同学在个人主页查看详细记录。如有疑问，请联系组织负责人。'
  }
])
const openNoticeId = ref('')
const actionMessage = ref('')
let actionTimer: ReturnType<typeof setTimeout> | null = null

function toggleNotice(id: string) {
  openNoticeId.value = openNoticeId.value === id ? '' : id
}

function announceAction(message: string) {
  actionMessage.value = message
  if (actionTimer) clearTimeout(actionTimer)
  actionTimer = setTimeout(() => {
    actionMessage.value = ''
    actionTimer = null
  }, 2800)
}

function handleChangePassword() {
  announceAction('修改密码需要在接入会话后打开安全设置。')
}

function handleLogout() {
  announceAction('退出登录将在接入会话后执行。')
}

function handleAbout() {
  announceAction('关于页将在后续版本接入。')
}

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

    <!-- Profile hero card with frosted glass -->
    <section class="glass-card profile-hero-card">
      <div class="profile-hero">
        <div class="profile-avatar">{{ initials }}</div>
        <div class="profile-identity">
          <div class="profile-name-line">
            <span class="profile-name">{{ profile.name }}</span>
            <span v-for="role in profile.roles" :key="role" class="role-badge">
              {{ role }}
            </span>
          </div>
          <span class="profile-class">{{ profile.className }}</span>
        </div>
        <span class="profile-status" :class="profile.banned ? 'is-banned' : 'is-normal'">
          {{ profile.status }}
        </span>
      </div>

      <dl class="profile-kv">
        <dt>姓名</dt>
        <dd>{{ profile.name }}</dd>
        <dt>学号</dt>
        <dd>{{ profile.id }}</dd>
        <dt>班级</dt>
        <dd>{{ profile.className }}</dd>
        <dt>组织</dt>
        <dd>{{ profile.group }}</dd>
        <dt>身份</dt>
        <dd class="profile-kv-roles">
          <span v-for="role in profile.roles" :key="role" class="role-badge">{{ role }}</span>
        </dd>
      </dl>
    </section>

    <!-- Notices section with frosted glass -->
    <section class="glass-card notices-card">
      <div class="notices-head">
        <h2 class="section-title">站内通知</h2>
        <button
          class="liquid-btn liquid-btn--secondary liquid-btn--sm"
          type="button"
          @click="announceAction('通知已是最新。')"
        >
          刷新
        </button>
      </div>

      <ul v-if="notices.length" class="notice-list">
        <li v-for="notice in notices" :key="notice.id" class="notice-item">
          <button
            type="button"
            class="notice-head"
            :aria-expanded="openNoticeId === notice.id"
            @click="toggleNotice(notice.id)"
          >
            <span class="notice-title">{{ notice.title }}</span>
            <span class="notice-meta">
              {{ notice.author }} · {{ notice.time }}
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
    <section class="glass-card actions-card">
      <button class="liquid-btn liquid-btn--primary liquid-btn--full" type="button" @click="handleChangePassword">
        修改密码
      </button>
      <button class="liquid-btn liquid-btn--secondary liquid-btn--full" type="button" @click="handleAbout">
        关于 ZVMS
      </button>
      <button class="liquid-btn liquid-btn--danger liquid-btn--full" type="button" @click="handleLogout">
        退出登录
      </button>
    </section>

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
  display: flex;
  align-items: center;
  justify-content: center;
  width: 40px;
  height: 40px;
  padding: 0;
  background: var(--glass-bg);
  -webkit-backdrop-filter: var(--glass-blur);
  backdrop-filter: var(--glass-blur);
  border: 1px solid var(--glass-border);
  border-radius: 50%;
  color: var(--color-text-secondary);
  cursor: pointer;
  transition: all var(--duration-fast) var(--ease-spring);
  box-shadow: var(--glass-shadow);
}

.back-btn:hover {
  color: var(--color-text-primary);
  transform: scale(1.05);
}

.back-btn:active {
  transform: scale(0.95);
}

.back-btn:focus-visible {
  outline: 2px solid var(--color-brand);
  outline-offset: 2px;
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
    rgba(255, 255, 255, 0.34) 0%,
    rgba(255, 255, 255, 0.18) 100%
  );
  -webkit-backdrop-filter: blur(8px) saturate(160%);
  backdrop-filter: blur(8px) saturate(160%);
  border: 1px solid rgba(255, 255, 255, 0.46);
  border-radius: var(--radius-md);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.68);
  text-align: left;
  cursor: pointer;
  display: grid;
  grid-template-columns: 1fr auto;
  grid-template-rows: auto auto;
  gap: var(--space-2) var(--space-3);
  align-items: center;
  transition: background var(--duration-fast) var(--ease-smooth);
}

.notice-head:hover {
  background: rgba(0, 113, 227, 0.05);
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
  transition: all var(--duration-fast) var(--ease-spring);
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
  transition: all var(--duration-base) var(--ease-smooth);
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
  background: linear-gradient(135deg, rgba(255, 255, 255, 0.48) 0%, rgba(255, 255, 255, 0.28) 100%);
  -webkit-backdrop-filter: blur(10px) saturate(170%);
  backdrop-filter: blur(10px) saturate(170%);
  border-color: rgba(255, 255, 255, 0.72);
  color: var(--color-text-primary);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.85), inset 0 -1px 0 rgba(0, 0, 0, 0.08), 0 2px 6px rgba(18, 48, 82, 0.06);
}

.liquid-btn--secondary:hover {
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.92), inset 0 -1px 0 rgba(0, 0, 0, 0.1), 0 4px 12px rgba(18, 48, 82, 0.1);
  transform: scale(1.02);
}

.liquid-btn--danger {
  background: linear-gradient(135deg, rgba(220, 38, 38, 0.95) 0%, rgba(220, 38, 38, 0.85) 100%);
  -webkit-backdrop-filter: blur(12px) saturate(170%);
  backdrop-filter: blur(12px) saturate(170%);
  border-color: rgba(255, 255, 255, 0.72);
  color: white;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.45), 0 4px 12px rgba(220, 38, 38, 0.3);
}

.liquid-btn--danger:hover {
  background: linear-gradient(135deg, rgba(239, 68, 68, 0.95) 0%, rgba(239, 68, 68, 0.85) 100%);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.55), 0 6px 18px rgba(220, 38, 38, 0.4);
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
}
</style>
