<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useDark } from '@vueuse/core'
import { ElButton } from 'element-plus/es/components/button/index.mjs'
import { ElDialog } from 'element-plus/es/components/dialog/index.mjs'
import { ElIcon } from 'element-plus/es/components/icon/index.mjs'
import { ElInput } from 'element-plus/es/components/input/index.mjs'
import { ElMessage } from 'element-plus/es/components/message/index.mjs'
import { ElMessageBox } from 'element-plus/es/components/message-box/index.mjs'
import { ElSwitch } from 'element-plus/es/components/switch/index.mjs'
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
    <header class="phx-head">
      <div class="phx-head__copy">
        <p class="phx-head__eyebrow">Profile · No. {{ studentNumber }}</p>
        <h1 class="phx-head__title">{{ t('profile.pageTitle') }}</h1>
        <p class="phx-head__lead">{{ t('profile.pageSubtitle') }}</p>
      </div>
      <span class="phx-head__index" aria-hidden="true">03</span>
    </header>

    <section class="phx-panel profile-identity-card" style="--phx-i: 1">
      <div class="yg-card__header">
        <span class="phx-code" aria-hidden="true">ID</span>
        <h2 class="yg-card__title">{{ t('profile.infoTitle') }}</h2>
      </div>

      <div class="yg-card__body">
        <div class="profile-hero">
          <span class="profile-avatar" aria-hidden="true">{{ initials }}</span>
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

    <section class="phx-panel profile-device-card" style="--phx-i: 2">
      <div class="yg-card__header">
        <span class="phx-code" aria-hidden="true">DEV</span>
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

          <div
            v-if="deviceState.bound || deviceState.isXuehaiDevice"
            class="profile-device-actions"
          >
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
      class="profile-device-dialog"
    >
      <p class="profile-device-dialog-hint">{{ t('device.profile.changeHint') }}</p>
      <ElInput
        v-model="changeValue"
        class="profile-device-dialog-input"
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

    <section class="phx-panel profile-notices-card" style="--phx-i: 3">
      <div class="yg-card__header">
        <div class="profile-notices-heading">
          <span class="phx-code" aria-hidden="true">MSG</span>
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
          <li
            v-for="(notice, index) in notices"
            :key="notice.id"
            class="profile-notice"
            :style="{ '--phx-n': Math.min(index, 8) }"
          >
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

    <section class="phx-panel profile-actions-card" style="--phx-i: 4">
      <div class="yg-card__header">
        <span class="phx-code" aria-hidden="true">SYS</span>
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
/* ==========================================================================
   Phi 棱角系统 · 个人资料页（与 About / Activity 同一套 scoped 写法）
   直角 + 不对称斜切（右上大切、左下小切）、细线、offset 硬阴影。
   颜色只从 --yg-* / --phi-* 派生；浅色只改强调色与硬阴影。
   浅色覆盖写成 `html[data-theme='light'] .profile-page`：compiler-sfc 3.5
   会把 `:global(x) .a` 整条替换成 `x`，不能用。
   ========================================================================== */
.profile-page,
.profile-device-dialog-input {
  --phx-accent: var(--phi-cyan, var(--yg-color-primary));
  --phx-accent-2: var(--phi-pink, var(--yg-color-danger));
  --phx-ink: var(--yg-text-primary);
  --phx-muted: var(--yg-text-tertiary);
  --phx-surface: var(--yg-bg-surface);
  --phx-sunken: var(--yg-bg-surface-sunken);
  --phx-line: color-mix(in srgb, var(--phx-accent) 22%, transparent);
  --phx-line-soft: color-mix(in srgb, var(--phx-ink) 10%, transparent);
  --phx-hard: color-mix(in srgb, var(--phx-accent) 16%, #000);
  --phx-display: var(--phi-display, Bebas, Impact, sans-serif);
  --phx-hud: var(--phi-hud, Rajdhani, 'Arial Narrow', sans-serif);
  --phx-ease: cubic-bezier(0.19, 1, 0.22, 1);
  --phx-spring: cubic-bezier(0.34, 1.4, 0.64, 1);
  --yg-radius-xs: 0;
  --yg-radius-sm: 0;
  --yg-radius-md: 0;
  --yg-radius-lg: 0;
  --yg-radius-xl: 0;
  --el-border-radius-base: 0;
  --el-border-radius-small: 0;
  --el-border-radius-round: 0;
  --el-border-radius-circle: 0;
  --el-button-border-radius: 0;
}

html[data-theme='light'] .profile-page,
html[data-theme='light'] .profile-device-dialog-input {
  --phx-accent: #007a70;
  --phx-accent-2: #c81e63;
  --phx-hard: #101418;
  --el-color-primary: #007a70;
  --yg-color-primary: #007a70;
  --yg-focus-ring-color: #007a70;
}

/* ---------- 页面骨架 ---------- */
.profile-page {
  display: flex;
  flex-direction: column;
  gap: var(--yg-space-6);
  width: 100%;
  max-width: 760px;
  margin: 0 auto;
}

/* ---------- 页头：HUD 编号 + 细网格 + 斜线 ---------- */
.phx-head {
  position: relative;
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: end;
  gap: var(--yg-space-4);
  margin: 0;
  padding: var(--yg-space-6) var(--yg-space-6) var(--yg-space-5);
  overflow: hidden;
  border-left: 3px solid var(--phx-accent);
  background:
    linear-gradient(
        to bottom left,
        transparent calc(50% - 1px),
        var(--phx-accent) 50%,
        transparent calc(50% + 1.5px)
      )
      right top / 22px 22px no-repeat,
    linear-gradient(var(--phx-line-soft) 1px, transparent 1px) 0 0 / 24px 24px,
    linear-gradient(90deg, var(--phx-line-soft) 1px, transparent 1px) 0 0 / 24px 24px,
    linear-gradient(
      100deg,
      color-mix(in srgb, var(--phx-accent) 9%, var(--phx-surface)),
      var(--phx-surface) 58%
    );
  clip-path: polygon(0 0, calc(100% - 22px) 0, 100% 22px, 100% 100%, 0 100%);
  animation: phx-head-in 420ms var(--phx-ease) both;
}

.phx-head::after {
  content: '';
  position: absolute;
  right: 18%;
  bottom: 0;
  width: 120px;
  height: 6px;
  background: repeating-linear-gradient(-55deg, var(--phx-accent) 0 2px, transparent 2px 7px);
  opacity: 0.55;
  pointer-events: none;
}

.phx-head__copy {
  min-width: 0;
}

.phx-head__eyebrow {
  display: flex;
  align-items: center;
  gap: var(--yg-space-2);
  margin: 0 0 var(--yg-space-2);
  color: var(--phx-accent);
  font: 400 15px / 1 var(--phx-display);
  letter-spacing: 0.22em;
  text-transform: uppercase;
  overflow-wrap: anywhere;
}

.phx-head__eyebrow::before {
  content: '';
  flex: 0 0 auto;
  width: 18px;
  height: 2px;
  background: currentColor;
  transform: skewX(-40deg);
}

.phx-head__title {
  position: relative;
  display: inline-block;
  margin: 0;
  color: var(--phx-ink);
  font-size: clamp(26px, 4.2vw, 38px);
  font-weight: var(--yg-font-weight-bold);
  line-height: 1.1;
  letter-spacing: 0.02em;
  animation: phx-slide-in 420ms 60ms var(--phx-ease) both;
}

.phx-head__title::after {
  content: '';
  position: absolute;
  bottom: -6px;
  left: 0;
  width: 44px;
  height: 3px;
  background: var(--phx-accent);
  transform: skewX(-40deg);
  transform-origin: left;
}

.phx-head__lead {
  max-width: 54ch;
  margin: var(--yg-space-4) 0 0;
  color: var(--yg-text-secondary);
  font-size: var(--yg-font-size-sm);
  line-height: var(--yg-line-height-relaxed);
  animation: phx-slide-in 420ms 110ms var(--phx-ease) both;
}

.phx-head__index {
  align-self: start;
  color: transparent;
  font: 400 clamp(64px, 11vw, 112px) / 0.8 var(--phx-display);
  -webkit-text-stroke: 1px color-mix(in srgb, var(--phx-ink) 45%, transparent);
  text-shadow:
    4px 0 color-mix(in srgb, var(--phx-accent-2) 28%, transparent),
    -3px 0 color-mix(in srgb, var(--phx-accent) 32%, transparent);
  user-select: none;
  animation: phx-slide-in-r 450ms 40ms var(--phx-ease) both;
}

/* ---------- 切角面板：右上大切 + 左下小切；边框/底面用两层伪元素画 ---------- */
.phx-panel {
  --phx-cut: 18px;
  --phx-cut-s: 8px;
  position: relative;
  isolation: isolate;
  color: var(--yg-text-secondary);
  filter: drop-shadow(5px 5px 0 var(--phx-hard));
  animation: phx-panel-in 440ms calc(var(--phx-i, 0) * 55ms + 60ms) var(--phx-ease) backwards;
}

.phx-panel::before,
.phx-panel::after {
  content: '';
  position: absolute;
  z-index: -1;
  pointer-events: none;
  clip-path: polygon(
    0 0,
    calc(100% - var(--phx-cut)) 0,
    100% var(--phx-cut),
    100% 100%,
    var(--phx-cut-s) 100%,
    0 calc(100% - var(--phx-cut-s))
  );
}

.phx-panel::before {
  inset: 0;
  background:
    linear-gradient(
      225deg,
      var(--phx-accent) 0 calc(var(--phx-cut) * 0.9),
      transparent calc(var(--phx-cut) * 0.9 + 1px)
    ),
    color-mix(in srgb, var(--phx-ink) 16%, transparent);
}

.phx-panel::after {
  inset: 1px;
  background:
    linear-gradient(var(--phx-accent), transparent 70%) left top / 2px 100% no-repeat,
    linear-gradient(
      135deg,
      color-mix(in srgb, var(--phx-accent) 5%, var(--phx-surface)),
      var(--phx-surface) 46%
    );
}

.phx-panel .yg-card__header {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: flex-start;
  gap: var(--yg-space-2) var(--yg-space-3);
  min-height: 52px;
  padding: var(--yg-space-3) calc(var(--phx-cut) + var(--yg-space-3)) var(--yg-space-3)
    var(--yg-space-5);
  border-bottom: 1px solid var(--phx-line-soft);
}

.phx-panel .yg-card__title {
  min-width: 0;
  margin: 0;
  color: var(--phx-ink);
  font-size: var(--yg-font-size-lg);
  font-weight: var(--yg-font-weight-semibold);
  line-height: var(--yg-line-height-snug);
}

.phx-panel .yg-card__extra {
  margin-left: auto;
}

.phx-panel .yg-card__body {
  padding: var(--yg-space-4) var(--yg-space-5) var(--yg-space-5);
  color: var(--yg-text-secondary);
}

.phx-code {
  flex: 0 0 auto;
  padding: 2px 6px 1px;
  border: 0;
  background: color-mix(in srgb, var(--phx-accent) 12%, transparent);
  color: var(--phx-accent);
  font: 400 13px / 1.1 var(--phx-display);
  letter-spacing: 0.16em;
  clip-path: polygon(0 0, calc(100% - 5px) 0, 100% 5px, 100% 100%, 0 100%);
}

/* ---------- 标签：直角 + 右下小切；语义色走 --yg-text-* 令牌 ---------- */
.profile-page .yg-tag {
  height: 24px;
  padding: 0 10px 0 8px;
  border: 0;
  border-radius: 0;
  background: var(--phx-sunken);
  color: var(--yg-text-secondary);
  font-family: var(--phx-hud), var(--yg-font-sans);
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.06em;
  clip-path: polygon(0 0, 100% 0, 100% calc(100% - 6px), calc(100% - 6px) 100%, 0 100%);
}

.profile-page .yg-tag.yg-tag--sky {
  background: color-mix(in srgb, var(--phx-accent) 12%, transparent);
  color: var(--phx-accent);
}

.profile-page .yg-tag.yg-tag--success {
  background: var(--yg-color-success-soft);
  color: var(--yg-text-success);
}

.profile-page .yg-tag.yg-tag--warning {
  background: var(--yg-color-warning-soft);
  color: var(--yg-text-warning);
}

.profile-page .yg-tag.yg-tag--danger {
  background: var(--yg-color-danger-soft);
  color: var(--yg-text-danger);
}

/* ---------- 骨架 / 空状态：斜切条 + 菱形图标 ---------- */
.profile-page .yg-skeleton__text {
  border-radius: 0;
  background: linear-gradient(
      90deg,
      var(--phx-sunken) 0 35%,
      color-mix(in srgb, var(--phx-accent) 12%, var(--phx-sunken)) 50%,
      var(--phx-sunken) 65%
    )
    0 0 / 220% 100%;
  clip-path: polygon(0 0, 100% 0, calc(100% - 8px) 100%, 0 100%);
  animation: phx-scan 1.4s linear infinite;
}

.profile-page .yg-empty__icon {
  border-radius: 0;
  background: color-mix(in srgb, var(--phx-accent) 12%, transparent);
  color: var(--phx-accent);
  clip-path: polygon(50% 0, 100% 50%, 50% 100%, 0 50%);
}

/* ---------- 个人信息 ---------- */
.profile-hero {
  position: relative;
  display: grid;
  grid-template-columns: 72px minmax(0, 1fr);
  align-items: center;
  gap: var(--yg-space-5);
  padding: var(--yg-space-2) 0 0 var(--yg-space-1);
}

/* 头像外的角括号框 */
.profile-hero::before {
  content: '';
  position: absolute;
  top: 50%;
  left: calc(var(--yg-space-1) - 7px);
  width: 86px;
  height: 86px;
  margin-top: calc(-43px + var(--yg-space-1));
  background:
    linear-gradient(var(--phx-accent), var(--phx-accent)) left top / 14px 1px no-repeat,
    linear-gradient(var(--phx-accent), var(--phx-accent)) left top / 1px 14px no-repeat,
    linear-gradient(var(--phx-accent), var(--phx-accent)) right bottom / 14px 1px no-repeat,
    linear-gradient(var(--phx-accent), var(--phx-accent)) right bottom / 1px 14px no-repeat;
  pointer-events: none;
}

/* 右上角斜线纹，放在内容下面 */
.profile-hero::after {
  content: '';
  position: absolute;
  top: 0;
  right: 0;
  z-index: -1;
  width: 96px;
  height: 32px;
  background: repeating-linear-gradient(-55deg, var(--phx-line) 0 2px, transparent 2px 8px);
  pointer-events: none;
}

.profile-avatar {
  display: grid;
  place-items: center;
  width: 72px;
  height: 72px;
  background: linear-gradient(
    135deg,
    color-mix(in srgb, var(--phx-accent) 24%, var(--phx-surface)),
    color-mix(in srgb, var(--phx-accent) 6%, var(--phx-surface))
  );
  color: var(--phx-accent);
  font-size: 30px;
  font-weight: var(--yg-font-weight-bold);
  line-height: 1;
  clip-path: polygon(
    0 0,
    calc(100% - 14px) 0,
    100% 14px,
    100% 100%,
    14px 100%,
    0 calc(100% - 14px)
  );
}

.profile-identity {
  display: flex;
  flex-direction: column;
  gap: var(--yg-space-2);
  min-width: 0;
}

.profile-name {
  margin: 0;
  font-size: clamp(22px, 4vw, 28px);
  font-weight: var(--yg-font-weight-bold);
  line-height: var(--yg-line-height-tight);
  color: var(--phx-ink);
  overflow-wrap: anywhere;
}

.profile-roles {
  display: flex;
  flex-wrap: wrap;
  gap: var(--yg-space-2);
}

/* 描述信息：硬边网格，每格左上一段青色刻度 */
.profile-details,
.profile-device-details {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 0;
  margin: var(--yg-space-5) 0 0;
  border-top: 1px solid var(--phx-line-soft);
}

.profile-device-details {
  grid-template-columns: repeat(2, minmax(0, 1fr));
  margin-top: 0;
}

.profile-details .yg-descriptions__item,
.profile-device-details .yg-descriptions__item {
  position: relative;
  min-width: 0;
  padding: var(--yg-space-3) var(--yg-space-4) var(--yg-space-1) 0;
}

.profile-details .yg-descriptions__item + .yg-descriptions__item,
.profile-device-details .yg-descriptions__item + .yg-descriptions__item {
  padding-left: var(--yg-space-4);
  border-left: 1px solid var(--phx-line-soft);
}

.profile-details .yg-descriptions__item::before,
.profile-device-details .yg-descriptions__item::before {
  content: '';
  position: absolute;
  top: -1px;
  left: 0;
  width: 22px;
  height: 2px;
  background: var(--phx-accent);
}

.profile-details .yg-descriptions__label,
.profile-device-details .yg-descriptions__label {
  color: var(--phx-muted);
  font-size: var(--yg-font-size-xs);
  font-weight: var(--yg-font-weight-regular);
  letter-spacing: 0.08em;
}

.profile-details .yg-descriptions__value,
.profile-device-details .yg-descriptions__value {
  margin: 0;
  color: var(--phx-ink);
  font-size: var(--yg-font-size-md);
  font-weight: var(--yg-font-weight-medium);
  overflow-wrap: anywhere;
}

.profile-device-details .yg-descriptions__value {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--yg-space-2);
}

.profile-page .yg-text-num {
  font-family: var(--phx-hud), var(--yg-font-mono);
  font-size: 17px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  letter-spacing: 0.04em;
}

.profile-class-error {
  display: inline-flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--yg-space-2);
}

/* ---------- 主设备 ---------- */
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
  padding: var(--yg-space-3) var(--yg-space-5) var(--yg-space-3) var(--yg-space-4);
  border: 0;
  border-left: 3px solid var(--yg-text-warning);
  background:
    repeating-linear-gradient(
      -45deg,
      color-mix(in srgb, var(--yg-text-warning) 10%, transparent) 0 6px,
      transparent 6px 12px
    ),
    var(--yg-color-warning-soft);
  color: var(--yg-text-warning);
  clip-path: polygon(0 0, calc(100% - 12px) 0, 100% 12px, 100% 100%, 0 100%);
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

.profile-device-hint {
  margin: var(--yg-space-4) 0 0;
  padding-left: var(--yg-space-3);
  border-left: 1px solid var(--phx-line);
  color: var(--phx-muted);
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

/* 弹窗挂在 body 上，变量在输入框自身上重新声明（见顶部） */
.profile-device-dialog-hint {
  margin: 0 0 var(--yg-space-3);
  padding-left: var(--yg-space-3);
  border-left: 2px solid var(--phi-cyan, var(--yg-color-primary));
  color: var(--yg-text-secondary);
  font-size: var(--yg-font-size-sm);
  line-height: var(--yg-line-height-relaxed);
}

.profile-device-dialog-input :deep(.el-input__wrapper) {
  border-radius: 0;
  box-shadow:
    inset 0 0 0 1px var(--phx-line-soft),
    inset 0 -2px 0 var(--phx-line);
  transition: box-shadow 200ms var(--phx-ease);
}

.profile-device-dialog-input :deep(.el-input__wrapper:hover) {
  box-shadow:
    inset 0 0 0 1px color-mix(in srgb, var(--phx-ink) 24%, transparent),
    inset 0 -2px 0 var(--phx-accent);
}

.profile-device-dialog-input :deep(.el-input__wrapper.is-focus) {
  box-shadow:
    inset 0 0 0 1px var(--phx-accent),
    inset 0 -2px 0 var(--phx-accent);
}

.profile-device-dialog-error {
  margin: var(--yg-space-2) 0 0;
  color: var(--yg-text-danger);
  font-size: var(--yg-font-size-sm);
}

/* ---------- 通知栏目 ---------- */
.profile-notices-heading {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--yg-space-2) var(--yg-space-3);
  min-width: 0;
}

.profile-notice-skeleton {
  display: flex;
  flex-direction: column;
  gap: var(--yg-space-3);
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

/* 逐条斜向擦入 */
.profile-notice {
  animation: phx-row-in 360ms calc(var(--phx-n, 0) * 40ms + 200ms) var(--phx-ease) backwards;
}

.profile-notice + .profile-notice {
  border-top: 1px solid var(--phx-line-soft);
}

.profile-notice-head {
  position: relative;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--yg-space-2) var(--yg-space-3);
  width: 100%;
  padding: var(--yg-space-3) var(--yg-space-2) var(--yg-space-3) var(--yg-space-4);
  border: 0;
  border-radius: 0;
  background: transparent;
  color: var(--phx-ink);
  text-align: left;
  cursor: pointer;
  transition:
    background-color 180ms var(--phx-ease),
    transform 260ms var(--phx-spring);
}

.profile-notice-head::before {
  content: '';
  position: absolute;
  top: var(--yg-space-2);
  bottom: var(--yg-space-2);
  left: 0;
  width: 2px;
  background: var(--phx-accent);
  transform: scaleY(0);
  transition: transform 260ms var(--phx-spring);
}

.profile-notice-head:hover {
  background: linear-gradient(
    100deg,
    color-mix(in srgb, var(--phx-accent) 10%, transparent) 0 45%,
    transparent 80%
  );
  transform: translateX(3px);
}

.profile-notice-head:hover::before,
.profile-notice-head[aria-expanded='true']::before {
  transform: scaleY(1);
}

.profile-notice-head:active {
  transform: translateX(3px) scale(0.99);
  transition-duration: 120ms;
}

.profile-notice-head:focus-visible {
  outline: 2px solid var(--phx-accent);
  outline-offset: -2px;
}

/* 未读：青色菱形 + 外圈；已读退成灰菱形，位置始终保留 */
.profile-notice-dot {
  flex: 0 0 auto;
  width: 8px;
  height: 8px;
  border-radius: 0;
  background-color: var(--yg-border-default);
  transform: rotate(45deg);
  transition:
    background-color 200ms var(--phx-ease),
    box-shadow 200ms var(--phx-ease);
}

.profile-notice-dot.is-unread {
  background-color: var(--phx-accent);
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--phx-accent) 22%, transparent);
}

.profile-notice-title {
  flex: 1 1 auto;
  min-width: 0;
  font-size: var(--yg-font-size-lg);
  font-weight: var(--yg-font-weight-semibold);
  line-height: var(--yg-line-height-snug);
  color: var(--phx-ink);
  overflow-wrap: anywhere;
}

.profile-notice-head.is-read .profile-notice-title {
  font-weight: var(--yg-font-weight-regular);
  color: var(--yg-text-secondary);
}

.profile-notice-head[aria-expanded='true'] .profile-notice-title {
  color: var(--phx-accent);
}

.profile-notice-meta {
  flex: 0 0 auto;
  color: var(--phx-muted);
  font-family: var(--phx-hud), var(--yg-font-sans);
  font-size: var(--yg-font-size-sm);
  font-weight: 500;
  white-space: nowrap;
  font-variant-numeric: tabular-nums;
}

.profile-notice-caret {
  flex: 0 0 auto;
  color: var(--phx-muted);
  transition: transform 300ms var(--phx-spring);
}

.profile-notice-caret.is-open {
  color: var(--phx-accent);
  transform: rotate(180deg);
}

.profile-notice-body {
  margin: 0 0 var(--yg-space-4) var(--yg-space-1);
  padding: var(--yg-space-1) var(--yg-space-2) 0 var(--yg-space-4);
  border-left: 1px solid var(--phx-line);
  color: var(--phx-ink);
  font-size: var(--yg-font-size-md);
  line-height: var(--yg-line-height-relaxed);
  animation: phx-notice-open 300ms var(--phx-ease);
}

/* ---------- 快捷操作 ---------- */
.profile-action-body {
  padding: 0;
}

.profile-action-list {
  margin: 0;
  padding: 0;
  list-style: none;
  counter-reset: phx-action;
}

.profile-action-item {
  counter-increment: phx-action;
}

.profile-action-item + .profile-action-item {
  border-top: 1px solid var(--phx-line-soft);
}

.profile-action {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: flex-start;
  gap: var(--yg-space-3);
  width: 100%;
  min-height: 52px;
  padding: var(--yg-space-3) var(--yg-space-5);
  border: 0;
  background: transparent;
  color: var(--phx-ink);
  font-size: var(--yg-font-size-lg);
  line-height: var(--yg-line-height-snug);
  text-align: left;
  cursor: pointer;
  transition:
    background-color 180ms var(--phx-ease),
    box-shadow 180ms var(--phx-ease);
}

/* 行号 01/02/03：纯装饰 */
.profile-action::before {
  content: counter(phx-action, decimal-leading-zero);
  flex: 0 0 auto;
  width: 22px;
  color: var(--phx-muted);
  font: 400 15px / 1 var(--phx-display);
  letter-spacing: 0.08em;
  transition: color 180ms var(--phx-ease);
}

/* 右侧斜角箭头（开关与退出不显示） */
.profile-action:not(.profile-action--switch, .profile-action--danger)::after {
  content: '';
  flex: 0 0 auto;
  width: 8px;
  height: 8px;
  margin-left: auto;
  border-top: 1.5px solid currentColor;
  border-right: 1.5px solid currentColor;
  color: var(--phx-muted);
  transform: rotate(45deg);
  transition:
    transform 260ms var(--phx-spring),
    color 180ms var(--phx-ease);
}

.profile-action-label {
  min-width: 0;
  overflow-wrap: anywhere;
  transition: transform 260ms var(--phx-spring);
}

.profile-action:hover {
  background: linear-gradient(
    100deg,
    color-mix(in srgb, var(--phx-accent) 10%, transparent) 0 40%,
    transparent 78%
  );
  box-shadow: inset 3px 0 0 var(--phx-accent);
}

.profile-action:hover::before {
  color: var(--phx-accent);
}

.profile-action:hover .profile-action-label {
  transform: translateX(3px);
}

.profile-action:not(.profile-action--switch, .profile-action--danger):hover::after {
  color: var(--phx-accent);
  transform: translateX(3px) rotate(45deg);
}

.profile-action:active {
  background-color: var(--yg-bg-active);
}

.profile-action:focus-visible {
  outline: 2px solid var(--phx-accent);
  outline-offset: -3px;
}

/* 开关本体只作视觉呈现：焦点与交互都由外层这一层 role="switch" 承担，
   避免出现两个可聚焦的开关 */
.profile-action--switch {
  justify-content: flex-start;
}

.profile-switch-control {
  --el-switch-on-color: var(--phx-accent);
  --el-switch-off-color: color-mix(in srgb, var(--phx-ink) 26%, var(--phx-surface));
  margin-left: auto;
  pointer-events: none;
}

/* 无框开关：软填充切角轨道 + 切角滑块；滑块仍走 left 弹簧位移，按压回缩 */
.profile-switch-control :deep(.el-switch__core) {
  border: 0;
  border-radius: 0;
  clip-path: polygon(5px 0, 100% 0, 100% calc(100% - 5px), calc(100% - 5px) 100%, 0 100%, 0 5px);
  box-shadow: none;
  transition:
    background-color 300ms var(--phx-ease),
    box-shadow 300ms var(--phx-ease);
}

.profile-switch-control :deep(.el-switch__action) {
  border: 0;
  border-radius: 0;
  clip-path: polygon(3px 0, 100% 0, 100% calc(100% - 3px), calc(100% - 3px) 100%, 0 100%, 0 3px);
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.26);
  transition:
    left 300ms var(--phx-spring),
    transform 300ms var(--phx-spring);
}

.profile-action--switch:active :deep(.el-switch__action) {
  transform: scale(0.8);
  transition-duration: 120ms;
}

/* 退出登录：单独成块的高危按钮，红色斜纹 + 双切角 */
.profile-action-item.profile-action-item--danger {
  margin: var(--yg-space-2) var(--yg-space-4) var(--yg-space-4);
  border-top: 0;
}

.profile-action.profile-action--danger {
  justify-content: center;
  gap: var(--yg-space-2);
  min-height: 48px;
  border: 0;
  background:
    repeating-linear-gradient(
      -45deg,
      color-mix(in srgb, var(--yg-text-danger) 9%, transparent) 0 6px,
      transparent 6px 12px
    ),
    var(--yg-color-danger-soft);
  box-shadow: none;
  color: var(--yg-text-danger);
  font-weight: var(--yg-font-weight-semibold);
  clip-path: polygon(
    0 0,
    calc(100% - 12px) 0,
    100% 12px,
    100% 100%,
    12px 100%,
    0 calc(100% - 12px)
  );
  transition:
    background-color 180ms var(--phx-ease),
    color 180ms var(--phx-ease);
}

.profile-action.profile-action--danger::before {
  content: none;
}

.profile-action.profile-action--danger:hover,
.profile-action.profile-action--danger:focus {
  color: var(--yg-text-danger);
  background:
    repeating-linear-gradient(
      -45deg,
      color-mix(in srgb, var(--yg-text-danger) 14%, transparent) 0 6px,
      transparent 6px 12px
    ),
    color-mix(in srgb, var(--yg-color-danger-soft) 70%, var(--yg-color-danger) 30%);
  box-shadow: none;
}

.profile-action.profile-action--danger:hover .profile-action-label {
  transform: none;
}

.profile-action.profile-action--danger:active {
  color: var(--yg-color-on-danger);
  background: var(--yg-color-danger);
}

.profile-action.profile-action--danger:focus-visible {
  outline: 2px solid var(--yg-text-danger);
  outline-offset: -5px;
}

.profile-logout-icon {
  font-size: var(--yg-font-size-lg);
}

/* ---------- 动效 ---------- */
@keyframes phx-head-in {
  from {
    opacity: 0;
    clip-path: polygon(0 0, 0 0, 0 22px, 0 100%, 0 100%);
  }
  to {
    opacity: 1;
    clip-path: polygon(0 0, calc(100% - 22px) 0, 100% 22px, 100% 100%, 0 100%);
  }
}

/* 面板斜向 clip reveal；终点多留余量，结束时硬阴影不会“跳出来” */
@keyframes phx-panel-in {
  from {
    opacity: 0;
    transform: translateX(-10px);
    clip-path: polygon(-12px -12px, -12px -12px, -52px calc(100% + 12px), -52px calc(100% + 12px));
  }
  to {
    opacity: 1;
    transform: none;
    clip-path: polygon(
      -12px -12px,
      calc(100% + 52px) -12px,
      calc(100% + 12px) calc(100% + 12px),
      -52px calc(100% + 12px)
    );
  }
}

@keyframes phx-row-in {
  from {
    opacity: 0;
    transform: translateX(-10px);
    clip-path: inset(-4px 100% -4px -12px);
  }
  to {
    opacity: 1;
    transform: none;
    clip-path: inset(-4px -4px -4px -12px);
  }
}

@keyframes phx-notice-open {
  from {
    opacity: 0;
    clip-path: inset(0 0 100% 0);
  }
  to {
    opacity: 1;
    clip-path: inset(0 0 0 0);
  }
}

@keyframes phx-slide-in {
  from {
    opacity: 0;
    transform: translateX(-14px);
  }
  to {
    opacity: 1;
    transform: none;
  }
}

@keyframes phx-slide-in-r {
  from {
    opacity: 0;
    transform: translateX(18px) skewX(-8deg);
  }
  to {
    opacity: 1;
    transform: none;
  }
}

@keyframes phx-scan {
  from {
    background-position: 100% 0;
  }
  to {
    background-position: -120% 0;
  }
}

/* ---------- 窄屏（≤640 / 390） ---------- */
@media (max-width: 640px) {
  .phx-head {
    padding: var(--yg-space-5) var(--yg-space-4) var(--yg-space-4);
  }

  .phx-head__index {
    font-size: 56px;
  }

  .phx-head::after {
    right: 12px;
    width: 72px;
  }

  .phx-panel {
    filter: drop-shadow(3px 3px 0 var(--phx-hard));
  }

  .phx-panel .yg-card__header {
    padding-left: var(--yg-space-4);
  }

  .phx-panel .yg-card__body {
    padding: var(--yg-space-4);
  }

  .profile-hero {
    grid-template-columns: 60px minmax(0, 1fr);
    gap: var(--yg-space-4);
  }

  .profile-hero::before {
    width: 74px;
    height: 74px;
    margin-top: calc(-37px + var(--yg-space-1));
  }

  .profile-avatar {
    width: 60px;
    height: 60px;
    font-size: 26px;
  }

  .profile-details,
  .profile-device-details {
    grid-template-columns: minmax(0, 1fr);
  }

  .profile-details .yg-descriptions__item + .yg-descriptions__item,
  .profile-device-details .yg-descriptions__item + .yg-descriptions__item {
    padding-left: 0;
    border-top: 1px solid var(--phx-line-soft);
    border-left: 0;
  }

  /* 通知行：标题与箭头同排，作者/时间换到下一行 */
  .profile-notice-caret {
    order: 2;
  }

  .profile-notice-meta {
    order: 3;
    flex-basis: calc(100% - 20px);
    margin-left: 20px;
    white-space: normal;
  }

  .profile-action {
    padding-inline: var(--yg-space-4);
  }

  /* 触控目标：窄屏下刷新按钮不小于 44px */
  .profile-notices-refresh,
  .profile-device-refresh {
    min-height: var(--yg-control-height-lg);
  }
}

@media (prefers-reduced-motion: reduce) {
  .phx-head,
  .phx-head__title,
  .phx-head__lead,
  .phx-head__index,
  .phx-panel,
  .profile-notice,
  .profile-notice-body,
  .profile-page .yg-skeleton__text {
    animation: none;
  }

  .profile-switch-control :deep(.el-switch__core),
  .profile-switch-control :deep(.el-switch__action),
  .profile-notice-caret,
  .profile-notice-dot,
  .profile-notice-head,
  .profile-notice-head::before,
  .profile-action,
  .profile-action::before,
  .profile-action::after,
  .profile-action-label {
    transition: none;
  }

  .profile-notice-head:hover,
  .profile-notice-head:active,
  .profile-action:hover .profile-action-label {
    transform: none;
  }

  .profile-action:not(.profile-action--switch, .profile-action--danger):hover::after {
    transform: rotate(45deg);
  }
}
</style>
