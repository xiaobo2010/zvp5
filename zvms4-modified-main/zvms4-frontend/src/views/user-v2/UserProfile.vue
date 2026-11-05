<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useDark } from '@vueuse/core'
import { ElButton, ElDialog, ElIcon, ElInput, ElMessage, ElMessageBox, ElSwitch } from 'element-plus'
import { Bell, Refresh, SwitchButton, Warning } from '@element-plus/icons-vue'
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
const dark = useDark()

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

async function changePassword() {
  await modifyPasswordDialogs(String(user._id), locale.value, user.resetPassword)
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
      await withTemporaryToken(
        String(user._id),
        t('device.profile.verifyChangeReason'),
        (token) => setMyPrimaryDevice(xuehaiId, token)
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
    await withTemporaryToken(
      String(user._id),
      t('device.profile.verifyChangeReason'),
      (token) => setMyPrimaryDevice(next, token)
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
    await ElMessageBox.confirm(
      t('device.profile.unbindWarning'),
      t('device.profile.unbindTitle'),
      {
        confirmButtonText: t('device.profile.unbindConfirm'),
        cancelButtonText: t('device.profile.unbindCancel'),
        type: 'warning',
        confirmButtonClass: 'el-button--danger',
        closeOnClickModal: false
      }
    )
  } catch {
    return
  }
  deviceSaving.value = true
  try {
    await withTemporaryToken(
      String(user._id),
      t('device.profile.verifyUnbindReason'),
      (token) => clearMyPrimaryDevice(token)
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
  <div class="profile-page yg-v2-flow">
    <header class="yg-page-header">
      <div class="profile-heading">
        <h1 class="yg-page-header__title">{{ t('profile.pageTitle') }}</h1>
        <p class="yg-page-header__subtitle">{{ t('profile.pageSubtitle') }}</p>
      </div>
    </header>

    <section class="yg-card profile-identity-card">
      <div class="yg-card__header">
        <h2 class="yg-card__title">{{ t('profile.infoTitle') }}</h2>
      </div>

      <div class="yg-card__body">
        <div class="profile-hero">
          <span class="yg-avatar yg-avatar--xl" aria-hidden="true">{{ initials }}</span>
          <div class="profile-identity">
            <p class="profile-name">{{ displayName }}</p>
            <div class="profile-roles">
              <span v-for="role in visibleRoles" :key="role" class="yg-tag yg-tag--sky">
                {{ roleLabel(role) }}
              </span>
            </div>
          </div>
        </div>

        <dl class="yg-descriptions profile-details">
          <div class="yg-descriptions__item">
            <dt class="yg-descriptions__label">{{ t('profile.labels.number') }}</dt>
            <dd class="yg-descriptions__value yg-text-num">{{ studentNumber }}</dd>
          </div>
          <div class="yg-descriptions__item">
            <dt class="yg-descriptions__label">{{ t('profile.labels.class') }}</dt>
            <dd class="yg-descriptions__value" :aria-busy="classState === 'loading'">
              <span
                v-if="classState === 'loading'"
                class="yg-skeleton yg-skeleton__text"
                aria-hidden="true"
              ></span>
              <span v-else-if="classState === 'failed'" class="profile-class-error">
                <span class="yg-tag yg-tag--warning">{{ t('profile.classFailed') }}</span>
                <span class="yg-text-sm yg-text-tertiary">{{ user.class_id }}</span>
              </span>
              <span v-else-if="className || user.class_id">{{ className || user.class_id }}</span>
              <span v-else class="yg-text-tertiary">{{ t('profile.noClass') }}</span>
            </dd>
          </div>
          <div class="yg-descriptions__item">
            <dt class="yg-descriptions__label">{{ t('profile.labels.status') }}</dt>
            <dd class="yg-descriptions__value">
              <span class="yg-tag" :class="statusTagClass">{{ statusLabel }}</span>
            </dd>
          </div>
        </dl>
      </div>
    </section>

    <section class="yg-card profile-device-card">
      <div class="yg-card__header">
        <h2 class="yg-card__title">{{ t('device.profile.title') }}</h2>
        <div class="yg-card__extra">
          <ElButton
            :icon="Refresh"
            class="profile-device-refresh"
            :loading="deviceLoading"
            @click="loadDevice"
          >
            {{ t('device.profile.refresh') }}
          </ElButton>
        </div>
      </div>

      <div class="yg-card__body">
        <div
          v-if="deviceLoading && !deviceStatus"
          class="yg-skeleton profile-device-skeleton"
          role="status"
          :aria-label="t('device.profile.loading')"
        >
          <span class="yg-skeleton__text" aria-hidden="true"></span>
          <span class="yg-skeleton__text" aria-hidden="true"></span>
        </div>

        <div v-else-if="deviceFailed" class="yg-empty profile-device-empty is-warning">
          <div class="yg-empty__icon" aria-hidden="true">
            <ElIcon class="profile-device-empty-icon"><Warning /></ElIcon>
          </div>
          <p class="yg-empty__title">{{ t('device.profile.failed') }}</p>
          <p class="yg-empty__desc">{{ t('device.profile.failedDesc') }}</p>
          <div class="yg-empty__actions">
            <ElButton :loading="deviceLoading" @click="loadDevice">
              {{ t('device.profile.retry') }}
            </ElButton>
          </div>
        </div>

        <template v-else-if="deviceStatus && deviceState">
          <div v-if="deviceStatus.device_banned" class="profile-device-ban" role="alert">
            <ElIcon class="profile-device-ban-icon" aria-hidden="true"><Warning /></ElIcon>
            <div class="profile-device-ban-body">
              <p class="profile-device-ban-title">{{ t('device.profile.bannedTitle') }}</p>
              <p class="profile-device-ban-text">
                {{ t('device.profile.bannedReason', { reason: deviceBanReason }) }}
              </p>
              <p class="profile-device-ban-text">{{ deviceBanUntil }}</p>
            </div>
          </div>

          <dl class="yg-descriptions profile-device-details">
            <div class="yg-descriptions__item">
              <dt class="yg-descriptions__label">{{ t('device.profile.currentLabel') }}</dt>
              <dd class="yg-descriptions__value">
                <span v-if="deviceState.isXuehaiDevice" class="yg-text-num">
                  {{ deviceStatus.xuehai_id }}
                </span>
                <span v-else class="yg-text-tertiary">{{ t('device.profile.noDevice') }}</span>
              </dd>
            </div>
            <div class="yg-descriptions__item">
              <dt class="yg-descriptions__label">{{ t('device.profile.primaryLabel') }}</dt>
              <dd class="yg-descriptions__value">
                <template v-if="deviceState.bound">
                  <span class="yg-text-num">{{ deviceStatus.primary_xuehai_id }}</span>
                  <span
                    class="yg-tag"
                    :class="deviceState.isPrimaryDevice ? 'yg-tag--success' : 'yg-tag--gray'"
                  >
                    {{
                      deviceState.isPrimaryDevice
                        ? t('device.profile.isPrimary')
                        : t('device.profile.otherDevice')
                    }}
                  </span>
                </template>
                <span v-else class="yg-text-tertiary">{{ t('device.profile.none') }}</span>
              </dd>
            </div>
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
            <ElButton
              v-if="deviceState.canBind"
              type="primary"
              :loading="deviceBinding"
              @click="bindCurrentDevice"
            >
              {{ t('device.profile.bind') }}
            </ElButton>
            <template v-else-if="deviceState.bound">
              <ElButton :disabled="!deviceState.canChange" @click="openChangeDialog">
                {{ t('device.profile.change') }}
              </ElButton>
              <ElButton
                text
                type="danger"
                :disabled="!deviceState.canUnbind"
                :loading="deviceSaving"
                @click="unbindPrimaryDevice"
              >
                {{ t('device.profile.unbind') }}
              </ElButton>
            </template>
          </div>
        </template>
      </div>
    </section>

    <ElDialog
      v-model="changeDialogVisible"
      :title="t('device.profile.changeTitle')"
      width="min(420px, 92vw)"
      append-to-body
    >
      <p class="profile-device-dialog-hint">{{ t('device.profile.changeHint') }}</p>
      <ElInput
        v-model="changeValue"
        :maxlength="32"
        :placeholder="t('device.profile.changePlaceholder')"
        clearable
        @keydown.enter.prevent="submitChange"
      />
      <p v-if="changeError" class="profile-device-dialog-error" role="alert">
        {{ changeError }}
      </p>
      <template #footer>
        <ElButton @click="changeDialogVisible = false">
          {{ t('device.profile.changeCancel') }}
        </ElButton>
        <ElButton type="primary" :loading="deviceSaving" @click="submitChange">
          {{ t('device.profile.changeSubmit') }}
        </ElButton>
      </template>
    </ElDialog>

    <section class="yg-card">
      <div class="yg-card__header">
        <div class="profile-notices-heading">
          <h2 class="yg-card__title">{{ t('profile.notices.title') }}</h2>
          <span v-if="unreadCount > 0" class="yg-tag yg-tag--sky">
            {{ t('profile.notices.unreadCount', { count: unreadCount }) }}
          </span>
        </div>
        <div class="yg-card__extra">
          <ElButton
            :icon="Refresh"
            class="profile-notices-refresh"
            :loading="noticesLoading"
            @click="loadNotices"
          >
            {{ t('profile.notices.refresh') }}
          </ElButton>
        </div>
      </div>

      <div class="yg-card__body">
        <div
          v-if="noticesLoading && notices.length === 0"
          class="yg-skeleton profile-notice-skeleton"
          role="status"
          :aria-label="t('profile.notices.loading')"
        >
          <span class="yg-skeleton__text" aria-hidden="true"></span>
          <span class="yg-skeleton__text" aria-hidden="true"></span>
          <span class="yg-skeleton__text" aria-hidden="true"></span>
        </div>

        <div v-else-if="noticesFailed" class="yg-empty profile-notice-empty is-warning">
          <div class="yg-empty__icon" aria-hidden="true">
            <ElIcon class="profile-notice-empty-icon"><Warning /></ElIcon>
          </div>
          <p class="yg-empty__title">{{ t('profile.notices.failed') }}</p>
          <p class="yg-empty__desc">{{ t('profile.notices.failedDesc') }}</p>
          <div class="yg-empty__actions">
            <ElButton :loading="noticesLoading" @click="loadNotices">
              {{ t('profile.notices.retry') }}
            </ElButton>
          </div>
        </div>

        <div v-else-if="notices.length === 0" class="yg-empty profile-notice-empty">
          <div class="yg-empty__icon" aria-hidden="true">
            <ElIcon class="profile-notice-empty-icon"><Bell /></ElIcon>
          </div>
          <p class="yg-empty__title">{{ t('profile.notices.empty') }}</p>
          <p class="yg-empty__desc">{{ t('profile.notices.emptyDesc') }}</p>
        </div>

        <ul v-else class="profile-notice-list">
          <li v-for="notice in notices" :key="notice.id" class="profile-notice">
            <button
              type="button"
              class="profile-notice-head"
              :class="{ 'is-read': !isNoticeUnread(notice) }"
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
                {{ formatDateTime(notice.created_at) }}
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
      </div>
    </section>

    <section class="yg-card">
      <div class="yg-card__header">
        <h2 class="yg-card__title">{{ t('profile.actions.title') }}</h2>
      </div>

      <div class="yg-card__body profile-action-body">
        <ul class="profile-action-list">
          <li class="profile-action-item">
            <button type="button" class="profile-action" @click="changePassword">
              <span class="profile-action-label">{{ t('profile.actions.changePassword') }}</span>
            </button>
          </li>
          <li class="profile-action-item">
            <div
              class="profile-action profile-action--switch"
              role="switch"
              :aria-checked="dark"
              tabindex="0"
              @click="toggleDarkMode"
              @keydown.enter.prevent="toggleDarkMode"
              @keydown.space.prevent="toggleDarkMode"
            >
              <span class="profile-action-label">{{ t('profile.actions.darkMode') }}</span>
              <ElSwitch
                v-model="dark"
                class="profile-switch-control"
                :tabindex="-1"
                aria-hidden="true"
              />
            </div>
          </li>
          <li class="profile-action-item">
            <button type="button" class="profile-action" @click="router.push('/about')">
              <span class="profile-action-label">{{ t('profile.actions.about') }}</span>
            </button>
          </li>
          <li class="profile-action-item profile-action-item--danger">
            <button type="button" class="profile-action profile-action--danger" @click="logout">
              <ElIcon class="profile-logout-icon" aria-hidden="true"><SwitchButton /></ElIcon>
              <span class="profile-action-label">{{ t('profile.actions.logout') }}</span>
            </button>
          </li>
        </ul>
      </div>
    </section>
  </div>
</template>

<style scoped>
/* ---------- 页面骨架 ---------- */
.profile-page {
  display: flex;
  flex-direction: column;
  gap: var(--yg-space-6);
  width: 100%;
  max-width: 760px;
  margin: 0 auto;
}

/* 头部自带 24px 下外边距，与页面栅格间距叠加会翻倍，这里归零 */
.profile-page .yg-page-header {
  margin-bottom: 0;
}

/* ---------- 个人信息 ---------- */
.profile-identity-card {
  box-shadow: var(--yg-shadow-sm);
}

.profile-hero {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--yg-space-5);
}

.profile-identity {
  display: flex;
  flex-direction: column;
  gap: var(--yg-space-2);
  min-width: 0;
}

.profile-name {
  margin: 0;
  font-size: var(--yg-font-size-2xl);
  font-weight: var(--yg-font-weight-semibold);
  line-height: var(--yg-line-height-tight);
  color: var(--yg-text-primary);
  overflow-wrap: anywhere;
}

.profile-roles {
  display: flex;
  flex-wrap: wrap;
  gap: var(--yg-space-2);
}

.profile-details {
  margin-top: var(--yg-space-5);
  padding-top: var(--yg-space-5);
  border-top: calc(var(--yg-space-1) / 4) solid var(--yg-border-subtle);
}

.profile-details .yg-descriptions__item {
  flex: 1 1 var(--yg-space-9);
  min-width: 0;
}

.profile-details .yg-descriptions__label {
  font-weight: var(--yg-font-weight-regular);
}

.profile-details .yg-descriptions__value {
  margin: 0;
  font-size: var(--yg-font-size-md);
  font-weight: var(--yg-font-weight-medium);
  color: var(--yg-text-primary);
}

.profile-class-error {
  display: inline-flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--yg-space-2);
}

/* ---------- 快捷操作 ---------- */
/* 满宽操作行：卡片内边距归零，由行自己提供左右留白，分隔线才能贯通整卡 */
.profile-action-body {
  padding: 0;
  overflow: hidden;
  border-radius: 0 0 var(--yg-radius-lg) var(--yg-radius-lg);
}

.profile-action-list {
  margin: 0;
  padding: 0;
  list-style: none;
}

.profile-action-item + .profile-action-item {
  border-top: calc(var(--yg-space-1) / 4) solid var(--yg-border-subtle);
}

/* 危险操作单独成组：靠间距分组，红色高危态只落在这一项上 */
.profile-action-item--danger {
  margin-top: var(--yg-space-2);
}

.profile-action {
  display: flex;
  align-items: center;
  justify-content: flex-start;
  gap: var(--yg-space-3);
  width: 100%;
  padding: var(--yg-space-3) var(--yg-space-5);
  border: 0;
  background: transparent;
  color: var(--yg-text-primary);
  font-size: var(--yg-font-size-lg);
  line-height: var(--yg-line-height-snug);
  text-align: left;
  cursor: pointer;
  transition: background-color var(--yg-dur-fast) var(--yg-ease-out);
}

.profile-action:hover {
  background-color: var(--yg-bg-hover);
}

.profile-action:active {
  background-color: var(--yg-bg-active);
}

.profile-action:focus-visible {
  outline: none;
  box-shadow: var(--yg-focus-ring);
}

/* 退出登录：高危操作，红色主题（浅红底 + 红字红图标，hover 加深）
   独立成一行的按钮，语义与普通操作行拉开，故内容居中 */
.profile-action.profile-action--danger {
  justify-content: center;
  gap: var(--yg-space-2);
  color: var(--yg-text-danger);
  background: var(--yg-color-danger-soft);
  font-weight: var(--yg-font-weight-medium);
  transition:
    background-color var(--yg-dur-fast) var(--yg-ease-out),
    color var(--yg-dur-fast) var(--yg-ease-out);
}

.profile-action.profile-action--danger:hover,
.profile-action.profile-action--danger:focus {
  color: var(--yg-text-danger);
  background: color-mix(in srgb, var(--yg-color-danger-soft) 70%, var(--yg-color-danger) 30%);
}

.profile-action.profile-action--danger:active {
  color: var(--yg-color-on-danger);
  background: var(--yg-color-danger);
}

.profile-action.profile-action--danger:focus-visible {
  outline: none;
  box-shadow: inset var(--yg-focus-ring);
}

.profile-logout-icon {
  font-size: var(--yg-font-size-lg);
}

.profile-action-label {
  min-width: 0;
  overflow-wrap: anywhere;
}

/* 开关本体只作视觉呈现：焦点与交互都由外层这一层 role="switch" 承担，
   避免出现两个可聚焦的开关 */
.profile-action--switch {
  justify-content: space-between;
}

.profile-switch-control {
  pointer-events: none;
}

/* 开关 Q 弹：滑块用弹簧曲线位移，按压时轻微回缩 */
.profile-switch-control :deep(.el-switch__core) {
  transition:
    background-color var(--yg-dur-slow) var(--yg-ease-out),
    border-color var(--yg-dur-slow) var(--yg-ease-out);
}

.profile-switch-control :deep(.el-switch__action) {
  transition:
    left var(--yg-dur-slow) var(--yg-ease-spring),
    transform var(--yg-dur-slow) var(--yg-ease-spring);
}

.profile-action--switch:active :deep(.el-switch__action) {
  transform: scale(0.8);
  transition-duration: var(--yg-dur-fast);
}

/* ---------- 主设备 ---------- */
.profile-device-card {
  box-shadow: var(--yg-shadow-sm);
}

.profile-device-skeleton {
  display: flex;
  flex-direction: column;
  gap: var(--yg-space-3);
}

.profile-device-empty-icon {
  font-size: var(--yg-space-7);
}

/* 与通知卡一致：加载失败是警告，不占用红色的危险语义 */
.profile-device-empty.is-warning .yg-empty__icon {
  background-color: var(--yg-color-warning-soft);
  color: var(--yg-text-warning);
}

.profile-device-ban {
  display: flex;
  align-items: flex-start;
  gap: var(--yg-space-3);
  margin-bottom: var(--yg-space-4);
  padding: var(--yg-space-3) var(--yg-space-4);
  border: calc(var(--yg-space-1) / 4) solid var(--yg-color-warning);
  border-radius: var(--yg-radius-md);
  background: var(--yg-color-warning-soft);
  color: var(--yg-text-warning);
}

.profile-device-ban-icon {
  flex: 0 0 auto;
  font-size: var(--yg-font-size-xl);
}

.profile-device-ban-body {
  min-width: 0;
}

.profile-device-ban-title {
  margin: 0 0 var(--yg-space-1);
  font-size: var(--yg-font-size-md);
  font-weight: var(--yg-font-weight-semibold);
}

.profile-device-ban-text {
  margin: 0;
  font-size: var(--yg-font-size-sm);
  line-height: var(--yg-line-height-snug);
  overflow-wrap: anywhere;
}

.profile-device-details {
  margin: 0;
}

.profile-device-details .yg-descriptions__item {
  flex: 1 1 var(--yg-space-9);
  min-width: 0;
}

.profile-device-details .yg-descriptions__label {
  font-weight: var(--yg-font-weight-regular);
}

.profile-device-details .yg-descriptions__value {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--yg-space-2);
  margin: 0;
  font-size: var(--yg-font-size-md);
  font-weight: var(--yg-font-weight-medium);
  color: var(--yg-text-primary);
}

.profile-device-hint {
  margin: var(--yg-space-3) 0 0;
  color: var(--yg-text-tertiary);
  font-size: var(--yg-font-size-sm);
  line-height: var(--yg-line-height-relaxed);
}

.profile-device-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--yg-space-2);
  margin-top: var(--yg-space-4);
}

.profile-device-dialog-hint {
  margin: 0 0 var(--yg-space-3);
  color: var(--yg-text-secondary);
  font-size: var(--yg-font-size-sm);
  line-height: var(--yg-line-height-relaxed);
}

.profile-device-dialog-error {
  margin: var(--yg-space-2) 0 0;
  color: var(--yg-text-danger);
  font-size: var(--yg-font-size-sm);
}

/* ---------- 通知栏目 ---------- */
.profile-notices-heading {
  display: flex;
  align-items: center;
  gap: var(--yg-space-2);
}

.profile-notice-empty-icon {
  font-size: var(--yg-space-7);
}

/* 加载失败是「警告」而非「危险」，用橙色浅底，不占用红色的危险语义 */
.profile-notice-empty.is-warning .yg-empty__icon {
  background-color: var(--yg-color-warning-soft);
  color: var(--yg-text-warning);
}

.profile-notice-list {
  margin: 0;
  padding: 0;
  list-style: none;
}

.profile-notice + .profile-notice {
  border-top: calc(var(--yg-space-1) / 4) solid var(--yg-border-subtle);
}

.profile-notice-head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--yg-space-2) var(--yg-space-3);
  width: 100%;
  padding: var(--yg-space-3) var(--yg-space-2);
  border: 0;
  border-radius: var(--yg-radius-md);
  background: transparent;
  color: var(--yg-text-primary);
  text-align: left;
  cursor: pointer;
  transition:
    background-color var(--yg-dur-fast) var(--yg-ease-out),
    transform var(--yg-dur-slow) var(--yg-ease-spring);
}

.profile-notice-head:hover {
  background: var(--yg-bg-hover);
}

.profile-notice-head:active {
  transform: scale(0.99);
  transition-duration: var(--yg-dur-fast);
}

.profile-notice-head:focus-visible {
  outline: none;
  box-shadow: var(--yg-focus-ring);
}

/* 未读圆点：主色实心；已读退成浅灰底，位置始终保留，避免行宽跳动 */
.profile-notice-dot {
  flex: 0 0 auto;
  width: var(--yg-space-2);
  height: var(--yg-space-2);
  border-radius: 50%;
  background-color: var(--yg-border-default);
  transition: background-color var(--yg-dur-base) var(--yg-ease-out);
}

.profile-notice-dot.is-unread {
  background-color: var(--yg-color-primary);
}

.profile-notice-title {
  flex: 1 1 auto;
  min-width: 0;
  font-size: var(--yg-font-size-lg);
  font-weight: var(--yg-font-weight-semibold);
  line-height: var(--yg-line-height-snug);
  color: var(--yg-text-primary);
  overflow-wrap: anywhere;
}

.profile-notice-head.is-read .profile-notice-title {
  font-weight: var(--yg-font-weight-regular);
  color: var(--yg-text-secondary);
}

.profile-notice-meta {
  flex: 0 0 auto;
  color: var(--yg-text-tertiary);
  font-size: var(--yg-font-size-sm);
  white-space: nowrap;
  font-variant-numeric: tabular-nums;
}

.profile-notice-caret {
  flex: 0 0 auto;
  color: var(--yg-text-tertiary);
  transition: transform var(--yg-dur-slow) var(--yg-ease-spring);
}

.profile-notice-caret.is-open {
  transform: rotate(180deg);
}

.profile-notice-body {
  padding: 0 var(--yg-space-2) var(--yg-space-4);
  color: var(--yg-text-primary);
  font-size: var(--yg-font-size-md);
  line-height: var(--yg-line-height-relaxed);
  animation: yg-notice-open var(--yg-dur-slow) var(--yg-ease-spring);
}

@keyframes yg-notice-open {
  from {
    opacity: 0;
    transform: translateY(calc(var(--yg-space-1) * -1));
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

@media (prefers-reduced-motion: reduce) {
  .profile-switch-control :deep(.el-switch__action),
  .profile-notice-caret,
  .profile-notice-dot,
  .profile-notice-head,
  .profile-notice-body {
    transition: none;
    animation: none;
  }
}

@media (max-width: 640px) {
  .profile-details .yg-descriptions__item,
  .profile-device-details .yg-descriptions__item {
    flex-basis: 100%;
  }

  /* 触控目标：窄屏下刷新按钮不小于 44px */
  .profile-notices-refresh :deep(.el-button),
  .profile-device-refresh :deep(.el-button) {
    min-height: var(--yg-control-height-lg);
  }
}
</style>
