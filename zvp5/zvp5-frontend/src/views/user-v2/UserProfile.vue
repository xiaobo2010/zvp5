<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import dayjs from '@/plugins/dayjs'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useTheme } from '@/utils/theme'
import Skeleton from '@/ui/ux/Skeleton.vue'
import StateBlock from '@/ui/ux/StateBlock.vue'
import { ElIcon, ElMessage, ElMessageBox, ElSwitch, ElDialog, ElInput } from 'element-plus'
import { SwitchButton } from '@element-plus/icons-vue'
import { useUserStore } from '@/stores/user'
import { useGroupsStore } from '@/stores/groups'
import { useHeaderStore } from '@/stores/header'
import { modifyPasswordDialogs } from '@/views/user/modifyPasswordDialogs'
import { listActive, type NotificationRecord } from '@/api/notifications'
import {
  clearMyPrimaryDevice,
  readMyDevice,
  setMyPrimaryDevice,
  type MyDeviceStatus
} from '@/api/user/device'
import { isTemporaryTokenCancelled, withTemporaryToken } from '@/plugins/short-token'
import {
  isValidXuehaiId,
  normalizeXuehaiId,
  primaryDeviceBanReason,
  primaryDeviceCardState,
  primaryDeviceErrorMessage
} from '@/components/notification/primaryDeviceLogic'
import ZMarkdown from '@/components/markdown/ZMarkdown.vue'
import { formatDateTime } from '@/utils/dateTime'
import {
  emptyNotificationState,
  isNotificationUnread,
  loadNotificationState,
  markNotificationRead,
  saveNotificationState,
  type NotificationState
} from '@/utils/notificationState'

const user = useUserStore()
const groupsStore = useGroupsStore()
const header = useHeaderStore()
const router = useRouter()
const { t, locale } = useI18n()
const { dark, toggleTheme: toggleThemePreference } = useTheme()

header.setHeader(t('nav.profile'))

const className = ref('')
const classState = ref<'idle' | 'loading' | 'ready' | 'failed'>('idle')

if (!user.isLogin) {
  router.replace('/user/login')
}

/* ---------- 个人信息 ---------- */
const displayName = computed(() => user.name.trim() || t('profile.nameMissing'))

const initials = computed(() => user.name.trim().slice(0, 1) || '—')

const studentNumber = computed(() => (user.id ? String(user.id) : '—'))

const statusLabel = computed(() =>
  user.banned ? t('profile.status.banned') : t('profile.status.normal')
)

const statusTagClass = computed(() => (user.banned ? 'yg-tag--danger' : 'yg-tag--gray'))

const visibleRoles = computed(() => {
  const roles = user.position.filter((role) => role !== 'student')
  return roles.length > 0 ? roles : (['student'] as typeof user.position)
})

function roleLabel(role: string): string {
  const key = `manage.personalPanel.roles.${role}`
  const label = t(key)
  return label === key ? role : label
}

function formatNoticeTime(value: string | null): string {
  if (!value) {
    return ''
  }
  const parsed = dayjs(value)
  return parsed.isValid() ? parsed.format('YYYY-MM-DD HH:mm') : ''
}

async function changePassword() {
  await modifyPasswordDialogs(String(user._id), locale.value, user.resetPassword)
}

async function logout() {
  try {
    await ElMessageBox.confirm(t('profile.logoutConfirm'), t('profile.logoutConfirmTitle'), {
      confirmButtonText: t('profile.logoutConfirmOk'),
      cancelButtonText: t('profile.logoutConfirmCancel'),
      type: 'warning',
      // 退出登录属高危确认，主按钮走红色语义色（深浅色一致）
      confirmButtonClass: 'el-button--danger',
      closeOnClickModal: false
    })
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
/** 已阅状态存在本地（按账号隔离），与全局提醒弹窗共用同一份读写口径。 */
const noticeState = ref<NotificationState>(emptyNotificationState())

const accountKey = computed(() => String(user._id || user.id || ''))

const unreadCount = computed(
  () => notices.value.filter((notice) => isNotificationUnread(notice, noticeState.value)).length
)

async function loadNotices() {
  noticesLoading.value = true
  noticesFailed.value = false
  try {
    const data = await listActive()
    notices.value = data.notifications
    noticeState.value = loadNotificationState(accountKey.value)
  } catch {
    noticesFailed.value = true
  } finally {
    noticesLoading.value = false
  }
}

function isNoticeUnread(notice: NotificationRecord): boolean {
  return isNotificationUnread(notice, noticeState.value)
}

function noticeReadLabel(notice: NotificationRecord): string {
  return isNoticeUnread(notice) ? t('profile.notices.unread') : t('profile.notices.read')
}

function toggleNotice(id: string) {
  if (openNoticeId.value === id) {
    openNoticeId.value = ''
    return
  }
  openNoticeId.value = id
  markNoticeRead(id)
}

/**
 * 展开即视为已读：写本地 read 标记后，全局提醒弹窗下次检查不会再重复弹出。
 * 读-改-写本地状态，避免覆盖其它组件（提醒弹窗）写入的标记。
 */
function markNoticeRead(id: string) {
  const notice = notices.value.find((item) => item.id === id)
  const key = accountKey.value
  if (!notice || !key) {
    return
  }
  const current = loadNotificationState(key)
  if (!isNotificationUnread(notice, current)) {
    return
  }
  const next = markNotificationRead(current, notice)
  noticeState.value = next
  saveNotificationState(key, next)
}

/* ---------- 主设备 ---------- */
const deviceStatus = ref<MyDeviceStatus | null>(null)
const deviceLoading = ref(false)
const deviceFailed = ref(false)
const deviceBinding = ref(false)
const deviceSaving = ref(false)
const changeDialogVisible = ref(false)
const changeValue = ref('')
const changeError = ref('')

const deviceState = computed(() =>
  deviceStatus.value ? primaryDeviceCardState(deviceStatus.value) : null
)

const deviceBanReason = computed(() =>
  deviceStatus.value?.device_banned
    ? primaryDeviceBanReason(deviceStatus.value, t('device.profile.bannedReasonFallback'))
    : ''
)

const deviceBanUntil = computed(() => {
  const until = deviceStatus.value?.device_ban_until
  return until
    ? t('device.profile.bannedUntil', { time: formatDateTime(until) })
    : t('device.profile.bannedPermanent')
})

async function loadDevice() {
  deviceLoading.value = true
  deviceFailed.value = false
  try {
    deviceStatus.value = await readMyDevice()
  } catch {
    deviceStatus.value = null
    deviceFailed.value = true
  } finally {
    deviceLoading.value = false
  }
}

async function bindCurrentDevice() {
  const xuehaiId = deviceStatus.value?.xuehai_id ?? ''
  if (!xuehaiId || deviceBinding.value) {
    return
  }
  deviceBinding.value = true
  try {
    if (deviceStatus.value?.primary_xuehai_id) {
      await withTemporaryToken(String(user._id), t('device.profile.verifyChangeReason'), (token) =>
        setMyPrimaryDevice(xuehaiId, token)
      )
    } else {
      await setMyPrimaryDevice(xuehaiId)
    }
    ElMessage.success(t('device.profile.bindSuccess'))
    await loadDevice()
  } catch (error) {
    if (isTemporaryTokenCancelled(error)) {
      return
    }
    ElMessage.error(primaryDeviceErrorMessage(error, t('device.profile.bindFailed')))
  } finally {
    deviceBinding.value = false
  }
}

function openChangeDialog() {
  changeValue.value = ''
  changeError.value = ''
  changeDialogVisible.value = true
}

async function submitChange() {
  const next = normalizeXuehaiId(changeValue.value)
  if (!isValidXuehaiId(next)) {
    changeError.value = t('device.profile.changeInvalid')
    return
  }
  deviceSaving.value = true
  try {
    await withTemporaryToken(String(user._id), t('device.profile.verifyChangeReason'), (token) =>
      setMyPrimaryDevice(next, token)
    )
    changeDialogVisible.value = false
    ElMessage.success(t('device.profile.changeSuccess'))
    await loadDevice()
  } catch (error) {
    if (isTemporaryTokenCancelled(error)) {
      return
    }
    changeError.value = primaryDeviceErrorMessage(error, t('device.profile.changeFailed'))
  } finally {
    deviceSaving.value = false
  }
}

async function unbindPrimaryDevice() {
  try {
    await ElMessageBox.confirm(t('device.profile.unbindWarning'), t('device.profile.unbindTitle'), {
      confirmButtonText: t('device.profile.unbindConfirm'),
      cancelButtonText: t('device.profile.unbindCancel'),
      type: 'warning',
      confirmButtonClass: 'el-button--danger',
      closeOnClickModal: false
    })
  } catch {
    return
  }
  deviceSaving.value = true
  try {
    await withTemporaryToken(String(user._id), t('device.profile.verifyUnbindReason'), (token) =>
      clearMyPrimaryDevice(token)
    )
    ElMessage.success(t('device.profile.unbindSuccess'))
    await loadDevice()
  } catch (error) {
    if (isTemporaryTokenCancelled(error)) {
      return
    }
    ElMessage.error(primaryDeviceErrorMessage(error, t('device.profile.unbindFailed')))
  } finally {
    deviceSaving.value = false
  }
}

/* ---------- 深浅色切换 ---------- */
const reduceMotion = () => window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false

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
  void loadDevice()
  if (!user.class_id) {
    classState.value = 'idle'
    return
  }
  classState.value = 'loading'
  const group = await groupsStore.fetchGroup(user.class_id)
  if (group) {
    className.value = group.name ?? ''
    classState.value = 'ready'
  } else {
    className.value = ''
    classState.value = 'failed'
  }
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

    <section class="yg2-card profile-device-card">
      <div class="profile-card-head">
        <h2 class="yg2-card__title">{{ t('device.profile.title') }}</h2>
        <button
          class="yg2-btn yg2-btn--quiet yg2-btn--sm"
          type="button"
          :disabled="deviceLoading"
          @click="loadDevice"
        >
          {{ t('device.profile.refresh') }}
        </button>
      </div>

      <div v-if="deviceLoading && !deviceStatus" class="profile-device-skeleton" role="status">
        <Skeleton :lines="2" height="13px" />
        <span class="profile-device-loading">{{ t('device.profile.loading') }}</span>
      </div>

      <StateBlock
        v-else-if="deviceFailed"
        tone="error"
        :title="t('device.profile.failed')"
        :description="t('device.profile.failedDesc')"
      >
        <template #actions>
          <button class="yg2-btn yg2-btn--primary" type="button" @click="loadDevice">
            {{ t('device.profile.retry') }}
          </button>
        </template>
      </StateBlock>

      <template v-else-if="deviceStatus && deviceState">
        <div v-if="deviceStatus.device_banned" class="profile-device-ban" role="alert">
          <span class="profile-device-ban-title">{{ t('device.profile.bannedTitle') }}</span>
          <span class="profile-device-ban-text">
            {{ t('device.profile.bannedReason', { reason: deviceBanReason }) }}
          </span>
          <span class="profile-device-ban-text">{{ deviceBanUntil }}</span>
        </div>

        <dl class="yg2-kv profile-device-kv">
          <dt>{{ t('device.profile.currentLabel') }}</dt>
          <dd>
            <span v-if="deviceState.isXuehaiDevice" class="profile-device-num">
              {{ deviceStatus.xuehai_id }}
            </span>
            <span v-else class="profile-device-muted">{{ t('device.profile.noDevice') }}</span>
          </dd>
          <dt>{{ t('device.profile.primaryLabel') }}</dt>
          <dd>
            <template v-if="deviceState.bound">
              <span class="profile-device-num">{{ deviceStatus.primary_xuehai_id }}</span>
              <span
                class="yg2-badge"
                :class="deviceState.isPrimaryDevice ? 'yg2-badge--accent' : ''"
              >
                {{
                  deviceState.isPrimaryDevice
                    ? t('device.profile.isPrimary')
                    : t('device.profile.otherDevice')
                }}
              </span>
            </template>
            <span v-else class="profile-device-muted">{{ t('device.profile.none') }}</span>
          </dd>
        </dl>

        <p v-if="!deviceState.bound" class="profile-device-hint">
          {{
            deviceState.isXuehaiDevice
              ? t('device.profile.unboundHint')
              : t('device.profile.bindNonDeviceHint')
          }}
        </p>
        <p v-else-if="deviceState.readOnlyReason !== 'none'" class="profile-device-hint">
          {{
            deviceState.readOnlyReason === 'notXuehai'
              ? t('device.profile.notDeviceHint')
              : t('device.profile.notPrimaryHint')
          }}
        </p>

        <div v-if="deviceState.bound || deviceState.isXuehaiDevice" class="profile-device-actions">
          <button
            v-if="deviceState.canBind"
            class="yg2-btn yg2-btn--primary yg2-btn--sm"
            type="button"
            :disabled="deviceBinding"
            @click="bindCurrentDevice"
          >
            {{ t('device.profile.bind') }}
          </button>
          <template v-else-if="deviceState.bound">
            <button
              class="yg2-btn yg2-btn--secondary yg2-btn--sm"
              type="button"
              :disabled="!deviceState.canChange || deviceSaving"
              @click="openChangeDialog"
            >
              {{ t('device.profile.change') }}
            </button>
            <button
              class="yg2-btn yg2-btn--danger yg2-btn--sm"
              type="button"
              :disabled="!deviceState.canUnbind || deviceSaving"
              @click="unbindPrimaryDevice"
            >
              {{ t('device.profile.unbind') }}
            </button>
          </template>
        </div>
      </template>
    </section>

    <ElDialog
      v-model="changeDialogVisible"
      :title="t('device.profile.changeTitle')"
      width="min(420px, 92vw)"
      append-to-body
      class="profile-device-dialog"
    >
      <p class="profile-device-dialog-hint">{{ t('device.profile.changeHint') }}</p>
      <ElInput
        v-model="changeValue"
        :maxlength="32"
        :placeholder="t('device.profile.changePlaceholder')"
        clearable
        @keydown.enter.prevent="submitChange"
      />
      <p v-if="changeError" class="profile-device-dialog-error" role="alert">{{ changeError }}</p>
      <template #footer>
        <button class="yg2-btn yg2-btn--quiet yg2-btn--sm" type="button" @click="changeDialogVisible = false">
          {{ t('device.profile.changeCancel') }}
        </button>
        <button
          class="yg2-btn yg2-btn--primary yg2-btn--sm"
          type="button"
          :disabled="deviceSaving"
          @click="submitChange"
        >
          {{ t('device.profile.changeSubmit') }}
        </button>
      </template>
    </ElDialog>

    <section class="yg2-card">
      <div class="profile-notices-head">
        <h2 class="yg2-card__title">{{ t('profile.notices.title') }}</h2>
        <span v-if="unreadCount > 0" class="yg2-badge yg2-badge--accent">
          {{ t('profile.notices.unreadCount', { count: unreadCount }) }}
        </span>
        <button
          class="yg2-btn yg2-btn--quiet yg2-btn--sm"
          type="button"
          :disabled="noticesLoading"
          @click="loadNotices"
        >
          {{ t('profile.notices.refresh') }}
        </button>
      </div>

      <div v-if="noticesLoading && notices.length === 0" class="profile-notices-skeleton">
        <Skeleton :lines="3" height="13px" />
      </div>
      <StateBlock
        v-else-if="noticesFailed"
        tone="error"
        title="通知没能加载出来"
        description="服务端暂时没有响应，不是你的操作问题，重试一次通常就好了。"
      >
        <template #actions>
          <button class="yg2-btn yg2-btn--primary" type="button" @click="loadNotices">
            重新试试
          </button>
        </template>
      </StateBlock>
      <StateBlock
        v-else-if="notices.length === 0"
        title="暂时没有新通知"
        description="有新的公告或提醒时会出现在这里。"
      />
      <ul v-else class="profile-notice-list">
        <li v-for="notice in notices" :key="notice.id" class="profile-notice">
          <button
            type="button"
            class="profile-notice-head"
            :aria-expanded="openNoticeId === notice.id"
            @click="toggleNotice(notice.id)"
          >
            <span
              class="profile-notice-dot"
              :class="{ 'is-unread': isNoticeUnread(notice) }"
              role="img"
              :aria-label="noticeReadLabel(notice)"
            ></span>
            <span class="profile-notice-title">{{ notice.title }}</span>
            <span class="profile-notice-meta">
              {{ notice.author_name || t('profile.notices.sender') }} ·
              {{ formatNoticeTime(notice.created_at) }}
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
      <button class="profile-action" type="button" @click="router.push('/notifications')">
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
  color: var(--yg2-text);
  background: transparent;
  transition:
    background-color 200ms var(--yg2-ease-out),
    color 200ms var(--yg2-ease-out),
    padding-left 200ms var(--yg2-ease-out);
}

.profile-action:hover {
  background: var(--yg2-surface-sunken);
}

.profile-action:active {
  background: color-mix(in srgb, var(--yg2-accent) 10%, transparent);
}

.profile-action + .profile-action {
  border-top: 1px solid var(--yg2-sep);
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
  background: var(--yg2-cat-community-soft);
  color: var(--yg2-cat-community);
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
  grid-template-columns: 8px minmax(0, 1fr) auto 16px;
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

/* ---------- 通知未读点 ---------- */
.profile-notice-dot {
  width: 8px;
  height: 8px;
  border-radius: 999px;
  background: transparent;
  box-shadow: inset 0 0 0 1.5px var(--yg2-line-strong);
  transition:
    background-color 200ms var(--yg2-ease-out),
    box-shadow 200ms var(--yg2-ease-out);
}

.profile-notice-dot.is-unread {
  background: var(--yg2-accent);
  box-shadow: 0 0 0 3px var(--yg2-accent-soft);
}

/* ---------- 主设备 ---------- */
.profile-card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 12px;
}

.profile-card-head .yg2-card__title {
  margin: 0;
}

.profile-device-skeleton {
  display: grid;
  gap: 10px;
  padding: 4px 0;
}

.profile-device-loading {
  font-size: 13px;
  color: var(--yg2-text-tertiary);
}

.profile-device-ban {
  display: grid;
  gap: 3px;
  padding: 12px 14px;
  margin-bottom: 14px;
  border-radius: var(--yg2-r-12);
  background: var(--yg2-danger-soft);
  color: var(--yg2-danger);
}

.profile-device-ban-title {
  font-size: 14px;
  font-weight: 600;
}

.profile-device-ban-text {
  font-size: 12.5px;
  line-height: 1.5;
}

.profile-device-kv dt {
  font-size: 13px;
  color: var(--yg2-text-secondary);
}

.profile-device-num {
  font-variant-numeric: tabular-nums;
  font-weight: 600;
}

.profile-device-muted {
  color: var(--yg2-text-tertiary);
  font-weight: 400;
}

.profile-device-hint {
  margin: 12px 0 0;
  font-size: 13px;
  line-height: 1.6;
  color: var(--yg2-text-secondary);
}

.profile-device-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 14px;
}

.profile-device-dialog-hint {
  margin: 0 0 12px;
  font-size: 13px;
  line-height: 1.6;
  color: var(--yg2-text-secondary);
}

.profile-device-dialog-error {
  margin: 8px 0 0;
  font-size: 13px;
  color: var(--yg2-danger);
}
</style>
