<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useTheme } from '@/utils/theme'
import { ElIcon, ElMessageBox, ElSwitch } from 'element-plus'
import { SwitchButton } from '@element-plus/icons-vue'
import { useUserStore } from '@/stores/user'
import { useGroupsStore } from '@/stores/groups'
import { useHeaderStore } from '@/stores/header'
import { modifyPasswordDialogs } from '@/views/user'
import { listActive, type NotificationRecord } from '@/api/notifications'
import ZMarkdown from '@/components/markdown/ZMarkdown.vue'
import dayjs from '@/plugins/dayjs'

const user = useUserStore()
const groupsStore = useGroupsStore()
const header = useHeaderStore()
const router = useRouter()
const { t } = useI18n()
const { dark, toggleTheme: toggleThemePreference } = useTheme()

header.setHeader(t('nav.profile'))

const className = ref('')

if (!user.isLogin) {
  router.replace('/user/login')
}

const initials = computed(() => user.name.slice(Math.max(user.name.length - 2, 0)) || '—')

const visibleRoles = computed(() => {
  const roles = user.position.filter((role) => role !== 'student')
  return roles.length > 0 ? roles : (['student'] as typeof user.position)
})

function roleLabel(role: string): string {
  return t(`manage.personalPanel.roles.${role}`)
}

async function changePassword() {
  await modifyPasswordDialogs(String(user._id), 'zh-CN', user.resetPassword)
}

async function logout() {
  try {
    await ElMessageBox.confirm(
      t('profile.logoutConfirm'),
      t('profile.logoutConfirmTitle'),
      {
        confirmButtonText: t('profile.logoutConfirmOk'),
        cancelButtonText: t('profile.logoutConfirmCancel'),
        type: 'warning',
        // 退出登录属高危确认，主按钮走红色语义色（深浅色一致）
        confirmButtonClass: 'el-button--danger',
        closeOnClickModal: false
      }
    )
  } catch {
    return
  }
  await user.removeUser()
  await router.replace('/user/login')
}

/* ---------- 站内通知 ---------- */
const notices = ref<NotificationRecord[]>([])
const noticesLoading = ref(false)
const noticesFailed = ref(false)
const openNoticeId = ref('')

async function loadNotices() {
  noticesLoading.value = true
  noticesFailed.value = false
  try {
    const data = await listActive()
    notices.value = data.notifications
  } catch {
    noticesFailed.value = true
  } finally {
    noticesLoading.value = false
  }
}

function toggleNotice(id: string) {
  openNoticeId.value = openNoticeId.value === id ? '' : id
}

function formatNoticeTime(value: string | null): string {
  if (!value) {
    return ''
  }
  const parsed = dayjs(value)
  return parsed.isValid() ? parsed.format('YYYY-MM-DD HH:mm') : ''
}

/* ---------- 深浅色切换 ---------- */
const reduceMotion = () =>
  window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false

function toggleDarkMode() {
  const doc = document as Document & {
    startViewTransition?: (callback: () => void) => unknown
  }
  // 整屏快照交叉淡化，避免逐个组件生硬切换
  if (typeof doc.startViewTransition === 'function' && !reduceMotion()) {
    doc.startViewTransition(() => {
      toggleThemePreference()
    })
  } else {
    toggleThemePreference()
  }
}

onMounted(async () => {
  void loadNotices()
  if (!user.class_id) {
    return
  }
  const group = await groupsStore.fetchGroup(user.class_id).catch(() => undefined)
  className.value = group?.name ?? ''
})
</script>

<template>
  <div class="yg2-page profile-page">
    <button class="profile-back" type="button" @click="router.back()">
      <span aria-hidden="true">‹</span> 返回
    </button>

    <header class="yg2-page-header">
      <div>
        <p class="yg2-page-header__eyebrow">账户</p>
        <h1 class="yg2-page-header__title">{{ t('profile.pageTitle') }}</h1>
      </div>
    </header>

    <section class="yg2-card profile-hero-card">
      <div class="profile-hero">
        <div class="profile-avatar">{{ initials }}</div>
        <div class="profile-identity">
          <div class="profile-name-line">
            <span class="profile-name">{{ user.name }}</span>
            <span v-for="role in visibleRoles" :key="role" class="yg2-badge yg2-badge--accent">
              {{ roleLabel(role) }}
            </span>
          </div>
          <span class="profile-class">
            {{ className || user.class_id || t('profile.noClass') }}
          </span>
        </div>
        <span class="profile-status" :class="user.banned ? 'is-banned' : 'is-normal'">
          {{ user.banned ? t('profile.status.banned') : t('profile.status.normal') }}
        </span>
      </div>

      <dl class="yg2-kv profile-kv">
        <dt>{{ t('profile.labels.name') }}</dt>
        <dd>{{ user.name }}</dd>
        <dt>{{ t('profile.labels.number') }}</dt>
        <dd>{{ user.id }}</dd>
        <dt>{{ t('profile.labels.class') }}</dt>
        <dd>{{ className || user.class_id || t('profile.noClass') }}</dd>
        <dt>{{ t('profile.labels.identity') }}</dt>
        <dd class="profile-kv-roles">
          <span v-for="role in visibleRoles" :key="role" class="yg2-badge">{{ roleLabel(role) }}</span>
        </dd>
      </dl>
    </section>

    <section class="yg2-card">
      <div class="profile-notices-head">
        <h2 class="yg2-card__title">{{ t('profile.notices.title') }}</h2>
        <button
          class="yg2-btn yg2-btn--quiet yg2-btn--sm"
          type="button"
          :disabled="noticesLoading"
          @click="loadNotices"
        >
          {{ t('profile.notices.refresh') }}
        </button>
      </div>

      <p v-if="noticesLoading && notices.length === 0" class="profile-notices-hint">
        {{ t('profile.notices.loading') }}
      </p>
      <p v-else-if="noticesFailed" class="profile-notices-hint">
        {{ t('profile.notices.failed') }}
      </p>
      <p v-else-if="notices.length === 0" class="profile-notices-hint">
        {{ t('profile.notices.empty') }}
      </p>
      <ul v-else class="profile-notice-list">
        <li v-for="notice in notices" :key="notice.id" class="profile-notice">
          <button
            type="button"
            class="profile-notice-head"
            :aria-expanded="openNoticeId === notice.id"
            @click="toggleNotice(notice.id)"
          >
            <span class="profile-notice-title">{{ notice.title }}</span>
            <span class="profile-notice-meta">
              {{ notice.author_name }} · {{ formatNoticeTime(notice.created_at) }}
            </span>
            <svg
              class="profile-notice-caret"
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
          <div v-if="openNoticeId === notice.id" class="profile-notice-body">
            <ZMarkdown :source="notice.content" />
          </div>
        </li>
      </ul>
    </section>

    <section class="yg2-card profile-actions">
      <button class="profile-action" type="button" @click="changePassword">
        {{ t('profile.actions.changePassword') }}
      </button>
      <div
        class="profile-action profile-switch"
        role="switch"
        :aria-checked="dark"
        tabindex="0"
        @click="toggleDarkMode"
        @keydown.enter.prevent="toggleDarkMode"
        @keydown.space.prevent="toggleDarkMode"
      >
        <span>{{ t('profile.actions.darkMode') }}</span>
        <ElSwitch :model-value="dark" @update:model-value="toggleDarkMode" />
      </div>
      <button class="profile-action" type="button" @click="router.push('/about')">
        {{ t('profile.actions.about') }}
      </button>
      <button class="profile-action" type="button" @click="router.push('/changelog')">
        {{ t('profile.actions.changelog') }}
      </button>
      <button class="profile-action profile-logout" type="button" @click="logout">
        <ElIcon class="profile-logout__icon"><SwitchButton /></ElIcon>
        {{ t('profile.actions.logout') }}
      </button>
    </section>
  </div>
</template>

<style scoped>
.profile-page {
  display: flex;
  flex-direction: column;
  gap: 16px;
  width: 100%;
  max-width: 760px;
  margin: 0 auto;
  padding: 16px 24px 32px;
}

.profile-header {
  padding: 4px 0;
}

.profile-card {
  border-radius: 8px;
}

.profile-hero {
  display: flex;
  align-items: center;
  gap: 20px;
  padding-bottom: 16px;
  border-bottom: 1px solid var(--yg-border-default);
}

.profile-avatar {
  flex-shrink: 0;
  font-size: 24px;
  background: var(--yg-color-primary);
  color: #fff;
}

.profile-identity {
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-width: 0;
}

.profile-name-line {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}

.profile-name {
  font-size: 22px;
  font-weight: 600;
  line-height: 1.3;
  color: var(--yg-text-primary);
}

.profile-role {
  margin: 0;
}

.profile-class {
  color: var(--yg-text-secondary);
  font-size: 14px;
}

.profile-details {
  margin-top: 16px;
}

.profile-actions {
  display: flex;
  flex-direction: column;
  padding: 0;
  overflow: hidden;
}

.profile-action {
  justify-content: flex-start;
  width: 100%;
  height: 52px;
  margin: 0;
  padding: 0 16px;
  border-radius: 0;
  font-size: 15px;
}

.profile-action + .profile-action {
  border-top: 1px solid var(--yg-border-default);
}

.profile-switch {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  color: var(--yg-text-primary);
  cursor: pointer;
  user-select: none;
  transition: background-color 160ms ease;
}

/* 整行可点：开关本身不再吃掉点击（由外层统一处理，切换才带整屏过渡） */
.profile-switch:hover {
  background: var(--yg-bg-surface-sunken);
}

.profile-switch:focus-visible {
  outline: none;
  box-shadow: var(--yg-focus-ring);
}

.profile-switch :deep(.el-switch) {
  pointer-events: none;
}

/* 开关 Q 弹：滑块用弹簧曲线位移，按压时轻微回缩 */
.profile-switch :deep(.el-switch__core) {
  transition:
    background-color 260ms ease,
    border-color 260ms ease;
}

.profile-switch :deep(.el-switch__action) {
  transition:
    left 420ms cubic-bezier(0.34, 1.56, 0.64, 1),
    transform 380ms cubic-bezier(0.34, 1.56, 0.64, 1);
}

.profile-switch:active :deep(.el-switch__action) {
  transform: scale(0.82);
  transition-duration: 90ms;
}

/* ---------- 通知栏目 ---------- */
.profile-section-title {
  font-size: 16px;
  font-weight: 600;
  color: var(--yg-text-primary);
}

.profile-notices-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding-bottom: 10px;
  border-bottom: 1px solid var(--yg-border-default);
}

.profile-notices-hint {
  margin: 14px 0 2px;
  color: var(--yg-text-secondary);
  font-size: 14px;
}

.profile-notice-list {
  margin: 0;
  padding: 0;
  list-style: none;
}

.profile-notice + .profile-notice {
  border-top: 1px solid var(--yg-border-default);
}

.profile-notice-head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px 10px;
  width: 100%;
  padding: 14px 6px;
  border: 0;
  border-radius: 10px;
  background: transparent;
  color: var(--yg-text-primary);
  text-align: left;
  cursor: pointer;
  transition:
    background-color 180ms ease,
    transform 320ms cubic-bezier(0.34, 1.56, 0.64, 1);
}

.profile-notice-head:hover {
  background: var(--yg-bg-surface-sunken);
}

.profile-notice-head:active {
  transform: scale(0.99);
  transition-duration: 90ms;
}

.profile-notice-head:focus-visible {
  outline: none;
  box-shadow: var(--yg-focus-ring);
}

.profile-notice-title {
  flex: 1 1 auto;
  min-width: 0;
  font-size: 15px;
  font-weight: 600;
  overflow-wrap: anywhere;
}

.profile-notice-meta {
  flex: 0 0 auto;
  color: var(--yg-text-secondary);
  font-size: 12.5px;
  white-space: nowrap;
  font-variant-numeric: tabular-nums;
}

.profile-notice-caret {
  flex: 0 0 auto;
  color: var(--yg-text-secondary);
  transition: transform 380ms cubic-bezier(0.34, 1.56, 0.64, 1);
}

.profile-notice-caret.is-open {
  transform: rotate(180deg);
}

.profile-notice-body {
  padding: 0 6px 16px;
  color: var(--yg-text-primary);
  font-size: 14px;
  line-height: 1.7;
  animation: yg-notice-open 320ms cubic-bezier(0.22, 1, 0.36, 1);
}

@keyframes yg-notice-open {
  from {
    opacity: 0;
    transform: translateY(-6px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

@media (prefers-reduced-motion: reduce) {
  .profile-switch :deep(.el-switch__action),
  .profile-notice-caret,
  .profile-notice-head,
  .profile-notice-body {
    transition: none;
    animation: none;
  }
}

/* 退出登录：高危操作，红色主题（浅红底 + 红字红图标，hover 加深） */
.profile-logout.el-button {
  justify-content: center;
  gap: 8px;
  color: var(--yg-text-danger);
  background: var(--yg-color-danger-soft);
  border: 0;
  border-radius: 0;
  font-weight: var(--yg-font-weight-medium);
  transition:
    background-color var(--yg-dur-fast) var(--yg-ease-out),
    color var(--yg-dur-fast) var(--yg-ease-out);
}

.profile-logout.el-button:hover,
.profile-logout.el-button:focus {
  color: var(--yg-text-danger);
  background: color-mix(in srgb, var(--yg-color-danger-soft) 70%, var(--yg-color-danger) 30%);
}

.profile-logout.el-button:active {
  color: var(--yg-color-on-danger);
  background: var(--yg-color-danger);
}

.profile-logout.el-button:focus-visible {
  outline: none;
  box-shadow: inset var(--yg-focus-ring);
}

.profile-logout__icon {
  font-size: 16px;
}

.profile-normal {
  color: var(--el-color-success);
}

.profile-banned {
  color: var(--el-color-danger);
}

@media (max-width: 480px) {
  .profile-page {
    padding: 12px 16px 24px;
  }

  .profile-hero {
    align-items: flex-start;
    gap: 14px;
  }

  .profile-avatar {
    width: 64px !important;
    height: 64px !important;
    font-size: 20px;
  }

  .profile-name {
    font-size: 20px;
  }
}

/* v2 个人页 */
.profile-page {
  display: flex;
  flex-direction: column;
  gap: 16px;
  max-width: 1080px;
  margin: 0 auto;
}

.profile-page .yg2-card {
  padding: 20px;
}

.profile-page .profile-actions {
  padding: 8px;
}

.profile-back {
  align-self: flex-start;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  height: 32px;
  padding: 0 12px 0 8px;
  border: 0;
  border-radius: var(--yg2-r-pill);
  background: transparent;
  color: var(--yg2-text-secondary);
  font: inherit;
  font-size: 14px;
  font-weight: 500;
  cursor: pointer;
}

.profile-back:hover {
  background: var(--yg2-surface-sunken);
  color: var(--yg2-text);
}

.profile-hero-card {
  padding: 24px;
}

.profile-hero {
  display: flex;
  align-items: center;
  gap: 16px;
}

.profile-avatar {
  display: grid;
  place-items: center;
  width: 72px;
  height: 72px;
  border-radius: var(--yg2-r-pill);
  background: linear-gradient(160deg, rgba(0, 113, 227, 0.16), rgba(0, 113, 227, 0.04));
  color: var(--yg2-accent);
  font-size: 24px;
  font-weight: 600;
  letter-spacing: 0.02em;
}

.profile-name-line {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.profile-name {
  font-size: 24px;
  font-weight: 650;
  letter-spacing: -0.02em;
  color: var(--yg2-text);
}

.profile-class {
  display: block;
  margin-top: 4px;
  font-size: 13px;
  color: var(--yg2-text-secondary);
}

.profile-status {
  margin-left: auto;
  padding: 4px 10px;
  border-radius: var(--yg2-r-pill);
  font-size: 12px;
  font-weight: 500;
}

.profile-status.is-normal {
  background: rgba(52, 199, 89, 0.12);
  color: #248a3d;
}

.profile-status.is-banned {
  background: rgba(255, 59, 48, 0.12);
  color: var(--yg2-danger);
}

.profile-kv {
  margin-top: 20px;
  padding-top: 16px;
  border-top: 1px solid var(--yg2-line);
}

.profile-kv dt {
  font-size: 13px;
  color: var(--yg2-text-secondary);
}

.profile-kv dd {
  font-size: 14px;
  color: var(--yg2-text);
}

.profile-kv-roles {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}

.profile-notices-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 8px;
}

.profile-notices-hint {
  margin: 4px 0;
  font-size: 13px;
  color: var(--yg2-text-tertiary);
}

.profile-notice-list {
  margin: 0;
  padding: 0;
  list-style: none;
}

.profile-notice + .profile-notice {
  border-top: 1px solid var(--yg2-line);
}

.profile-notice-head {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto 16px;
  align-items: center;
  gap: 12px;
  width: 100%;
  padding: 14px 4px;
  border: 0;
  background: transparent;
  font: inherit;
  text-align: left;
  cursor: pointer;
  color: var(--yg2-text);
}

.profile-notice-title {
  font-size: 14px;
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.profile-notice-meta {
  font-size: 12px;
  color: var(--yg2-text-tertiary);
}

.profile-notice-caret {
  color: var(--yg2-text-tertiary);
  transition: transform var(--yg2-dur-1) var(--yg2-ease-out);
}

.profile-notice-caret.is-open {
  transform: rotate(180deg);
}

.profile-notice-body {
  padding: 4px 4px 16px;
}

.profile-actions {
  display: flex;
  flex-direction: column;
  padding: 8px;
}

.profile-action {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  width: 100%;
  min-height: 48px;
  padding: 0 12px;
  border: 0;
  border-radius: var(--yg2-r-12);
  background: transparent;
  color: var(--yg2-text);
  font: inherit;
  font-size: 15px;
  text-align: left;
  cursor: pointer;
}

.profile-action:hover {
  background: var(--yg2-surface-sunken);
}

.profile-switch {
  cursor: default;
}

.profile-logout {
  color: var(--yg2-danger);
  gap: 8px;
  justify-content: flex-start;
}

.profile-logout__icon {
  font-size: 16px;
}

/* 覆盖旧样式遗留 */
.profile-card {
  border: 0;
  box-shadow: none;
  background: transparent;
}
</style>
