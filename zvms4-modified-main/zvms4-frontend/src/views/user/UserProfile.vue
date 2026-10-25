<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useDark } from '@vueuse/core'
import {
  ElAvatar,
  ElButton,
  ElCard,
  ElDescriptions,
  ElDescriptionsItem,
  ElIcon,
  ElMessageBox,
  ElPageHeader,
  ElSwitch,
  ElTag
} from 'element-plus'
import { ArrowLeft, SwitchButton } from '@element-plus/icons-vue'
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
const dark = useDark()

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
  const next = !dark.value
  const doc = document as Document & {
    startViewTransition?: (callback: () => void) => unknown
  }
  // 整屏快照交叉淡化，避免逐个组件生硬切换
  if (typeof doc.startViewTransition === 'function' && !reduceMotion()) {
    doc.startViewTransition(() => {
      dark.value = next
    })
  } else {
    dark.value = next
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
  <div class="profile-page">
    <ElPageHeader :icon="ArrowLeft" class="profile-header" @back="router.back()">
      <template #content>{{ t('profile.pageTitle') }}</template>
    </ElPageHeader>

    <ElCard shadow="never" class="profile-card">
      <div class="profile-hero">
        <ElAvatar :size="80" class="profile-avatar">{{ initials }}</ElAvatar>
        <div class="profile-identity">
          <div class="profile-name-line">
            <span class="profile-name">{{ user.name }}</span>
            <ElTag
              v-for="role in visibleRoles"
              :key="role"
              size="small"
              effect="plain"
              class="profile-role"
            >
              {{ roleLabel(role) }}
            </ElTag>
          </div>
          <span class="profile-class">
            {{ className || user.class_id || t('profile.noClass') }}
          </span>
        </div>
      </div>

      <ElDescriptions :column="1" border class="profile-details">
        <ElDescriptionsItem :label="t('profile.labels.name')">
          {{ user.name }}
        </ElDescriptionsItem>
        <ElDescriptionsItem :label="t('profile.labels.number')">
          {{ user.id }}
        </ElDescriptionsItem>
        <ElDescriptionsItem :label="t('profile.labels.class')">
          {{ className || user.class_id || t('profile.noClass') }}
        </ElDescriptionsItem>
        <ElDescriptionsItem :label="t('profile.labels.identity')">
          <ElTag
            v-for="role in visibleRoles"
            :key="role"
            size="small"
            effect="plain"
            class="profile-role"
          >
            {{ roleLabel(role) }}
          </ElTag>
        </ElDescriptionsItem>
        <ElDescriptionsItem :label="t('profile.labels.status')">
          <span :class="user.banned ? 'profile-banned' : 'profile-normal'">
            {{ user.banned ? t('profile.status.banned') : t('profile.status.normal') }}
          </span>
        </ElDescriptionsItem>
      </ElDescriptions>
    </ElCard>

    <ElCard shadow="never" class="profile-card profile-notices">
      <div class="profile-notices-head">
        <span class="profile-section-title">{{ t('profile.notices.title') }}</span>
        <ElButton text size="small" :loading="noticesLoading" @click="loadNotices">
          {{ t('profile.notices.refresh') }}
        </ElButton>
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
    </ElCard>

    <ElCard shadow="never" class="profile-card profile-actions">
      <ElButton text size="large" class="profile-action" @click="changePassword">
        {{ t('profile.actions.changePassword') }}
      </ElButton>
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
        <ElSwitch v-model="dark" />
      </div>
      <ElButton text size="large" class="profile-action" @click="router.push('/about')">
        {{ t('profile.actions.about') }}
      </ElButton>
      <ElButton text size="large" class="profile-action" @click="router.push('/changelog')">
        {{ t('profile.actions.changelog') }}
      </ElButton>
      <ElButton class="profile-action profile-logout" @click="logout">
        <ElIcon class="profile-logout__icon"><SwitchButton /></ElIcon>
        {{ t('profile.actions.logout') }}
      </ElButton>
    </ElCard>
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
</style>
