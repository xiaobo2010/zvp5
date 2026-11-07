<script setup lang="ts">
import { computed, defineAsyncComponent, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElButton, ElDialog } from 'element-plus'
import { useI18n } from 'vue-i18n'
import dayjs from '@/plugins/dayjs'
import { useUserStore } from '@/stores/user'
import { listActive, type NotificationRecord } from '@/api/notifications'
// 通知正文的 markdown 渲染(markdown-it + dompurify)不在首屏路径上：
// 提醒弹窗打开时才加载，避免这两个库随 App 壳进入入口 chunk。
const ZMarkdown = defineAsyncComponent(() => import('@/components/markdown/ZMarkdown.vue'))
import {
  collectUnreadNotifications,
  emptyNotificationState,
  loadNotificationState,
  markNotificationRead,
  saveNotificationState,
  suppressNotification,
  withLastGlobalVersion,
  type NotificationState
} from '@/utils/notificationState'

const MIN_CHECK_INTERVAL_MS = 60_000

const { t } = useI18n()
const route = useRoute()
const userStore = useUserStore()

/**
 * 账号唯一 id 优先使用 oid。切换账号时 watcher 会立即丢弃旧队列、旧响应和
 * 旧本地状态；key 本身也按账号隔离，绝不把已阅状态带到下一个人。
 */
const accountKey = computed(() => {
  if (!userStore.isLogin) {
    return ''
  }
  return String(userStore._id || userStore.id || '')
})

const queue = ref<NotificationRecord[]>([])
const current = computed(() => queue.value[0] ?? null)
const state = ref<NotificationState>(emptyNotificationState())
const advancing = ref(false)
const bodyRef = ref<HTMLElement | null>(null)

let generation = 0
let inFlight: AbortController | null = null
let lastCheckedAt = 0
let advanceTimer: ReturnType<typeof setTimeout> | null = null

const progressText = computed(() => {
  if (!current.value) {
    return ''
  }
  return t('notificationCenter.progress', {
    current: 1,
    total: queue.value.length
  })
})

function formatTime(value: string | null | undefined): string {
  if (!value) {
    return '—'
  }
  const parsed = dayjs(value)
  return parsed.isValid() ? parsed.format('YYYY-MM-DD HH:mm') : value
}

function resetForAccount() {
  generation += 1
  inFlight?.abort()
  inFlight = null
  queue.value = []
  state.value = emptyNotificationState()
  lastCheckedAt = 0
  advancing.value = false
  if (advanceTimer) {
    clearTimeout(advanceTimer)
    advanceTimer = null
  }
}

async function checkNotifications(force = false) {
  const key = accountKey.value
  if (!key || !userStore.isLogin) {
    return
  }
  // 同一时刻只允许一个请求；弹窗未处理完时不叠加新请求或新弹窗。
  if (inFlight || queue.value.length > 0) {
    return
  }
  const now = Date.now()
  if (!force && lastCheckedAt > 0 && now - lastCheckedAt < MIN_CHECK_INTERVAL_MS) {
    return
  }

  const requestGeneration = generation
  const controller = new AbortController()
  inFlight = controller
  lastCheckedAt = now

  try {
    const data = await listActive(controller.signal)
    if (requestGeneration !== generation || key !== accountKey.value) {
      return
    }

    // 先按当前账号的本地状态判定，再写回服务端全局版本。全局版本只用于刷新
    // 优化/记录，不能代替 read 判定，也不能让新用户误以为缓存通知已处理。
    const loaded = loadNotificationState(key)
    const next = withLastGlobalVersion(loaded, data.version)
    state.value = next
    saveNotificationState(key, next)

    const pending = collectUnreadNotifications(data.notifications, next)
    if (pending.length > 0 && requestGeneration === generation) {
      queue.value = pending
    }
  } catch {
    // 获取失败静默处理：不改本地状态，不让陈旧通知被误标已阅，也不阻塞主页面。
  } finally {
    if (inFlight === controller) {
      inFlight = null
    }
  }
}

function finishNotice(next: NotificationState, notice: NotificationRecord) {
  state.value = next
  saveNotificationState(accountKey.value, next)
  queue.value = queue.value.filter((item) => item.id !== notice.id)
  // 快速双击时第二下不应作用到下一条通知；短暂禁用操作，完成视觉切换。
  advancing.value = true
  if (advanceTimer) {
    clearTimeout(advanceTimer)
  }
  advanceTimer = setTimeout(() => {
    advancing.value = false
    advanceTimer = null
  }, 300)
}

function acknowledge() {
  const notice = current.value
  if (!notice || advancing.value) {
    return
  }
  finishNotice(markNotificationRead(state.value, notice), notice)
}

function suppress() {
  const notice = current.value
  if (!notice || advancing.value) {
    return
  }
  finishNotice(suppressNotification(state.value, notice), notice)
}

watch(
  accountKey,
  () => {
    resetForAccount()
    if (accountKey.value) {
      void checkNotifications(true)
    }
  },
  { immediate: true }
)

watch(
  () => current.value?.id,
  async () => {
    await nextTick()
    if (bodyRef.value) {
      bodyRef.value.scrollTop = 0
    }
  }
)

// 路由变化本身不是高频刷新点：用 60s 节流覆盖「新页面 / 再次进入」，
// 弹窗进行中或请求在飞时直接跳过，避免重复请求和重叠弹窗。
watch(
  () => route.path,
  () => {
    void checkNotifications()
  }
)

function onVisibilityChange() {
  if (document.visibilityState === 'visible') {
    void checkNotifications()
  }
}

onMounted(() => {
  document.addEventListener('visibilitychange', onVisibilityChange)
})

onBeforeUnmount(() => {
  document.removeEventListener('visibilitychange', onVisibilityChange)
  resetForAccount()
})
</script>

<template>
  <ElDialog
    :model-value="current !== null"
    :title="t('notificationCenter.dialogTitle')"
    :show-close="false"
    :close-on-click-modal="false"
    :close-on-press-escape="false"
    :destroy-on-close="true"
    append-to-body
    top="6vh"
    width="min(640px, 92vw)"
    class="notification-dialog"
  >
    <div v-if="current" class="notification-card">
      <div class="notification-meta">
        <span>{{ t('notificationCenter.from', { name: current.author_name || '—' }) }}</span>
        <span class="notification-dot" aria-hidden="true">·</span>
        <span class="tabular-nums">{{ formatTime(current.created_at) }}</span>
        <span class="notification-progress">{{ progressText }}</span>
      </div>

      <h2 class="notification-title">{{ current.title }}</h2>

      <div ref="bodyRef" class="notification-body" tabindex="0">
        <ZMarkdown :source="current.content" />
      </div>
    </div>

    <template #footer>
      <div class="notification-footer">
        <div class="notification-hints">
          <p>{{ t('notificationCenter.readHint') }}</p>
          <p>{{ t('notificationCenter.suppressHint') }}</p>
        </div>
        <div class="notification-actions">
          <ElButton
            size="large"
            class="notification-action"
            :disabled="advancing"
            @click="suppress"
          >
            {{ t('notificationCenter.suppress') }}
          </ElButton>
          <ElButton
            size="large"
            type="primary"
            class="notification-action"
            :disabled="advancing"
            @click="acknowledge"
          >
            {{ t('notificationCenter.read') }}
          </ElButton>
        </div>
      </div>
    </template>
  </ElDialog>
</template>

<style scoped>
.notification-card {
  display: flex;
  flex-direction: column;
  gap: 12px;
  min-width: 0;
}

.notification-meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  color: var(--yg-text-tertiary);
}

.notification-dot {
  color: var(--yg-border-strong);
}

.notification-progress {
  margin-left: auto;
  padding: 2px 8px;
  border: 1px solid var(--yg-border-default);
  border-radius: 4px;
  color: var(--yg-text-secondary);
  font-variant-numeric: tabular-nums;
}

.notification-title {
  margin: 0;
  font-size: 22px;
  line-height: 1.35;
  font-weight: 600;
  color: var(--yg-text-primary);
  overflow-wrap: anywhere;
  word-break: break-word;
}

.notification-body {
  max-height: min(48vh, 420px);
  overflow-y: auto;
  padding: 14px 16px;
  border: 1px solid var(--yg-border-default);
  border-radius: 6px;
  background: var(--yg-bg-surface-sunken);
  color: var(--yg-text-primary);
  font-size: 15px;
  line-height: 1.7;
  overflow-wrap: anywhere;
  word-break: break-word;
  -webkit-overflow-scrolling: touch;
}

.notification-body :deep(p) {
  margin: 0 0 0.75em;
}

.notification-body :deep(p:last-child) {
  margin-bottom: 0;
}

.notification-body :deep(img) {
  max-width: 100%;
  height: auto;
}

.notification-body :deep(pre) {
  max-width: 100%;
  overflow-x: auto;
}

.notification-body :deep(a) {
  color: var(--el-color-primary);
}

.notification-footer {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.notification-hints {
  display: flex;
  flex-direction: column;
  gap: 2px;
  text-align: left;
  font-size: 12px;
  line-height: 1.5;
  color: var(--yg-text-tertiary);
}

.notification-hints p {
  margin: 0;
}

.notification-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
}

.notification-action {
  min-width: 112px;
}

@media (max-width: 560px) {
  .notification-title {
    font-size: 19px;
  }

  .notification-actions {
    flex-direction: column-reverse;
  }

  .notification-action {
    width: 100%;
    min-width: 0;
    margin-left: 0;
  }
}
</style>

<style>
/* 弹窗内部必须是不透明实色；低圆角沿用全局 flat 基线。 */
.notification-dialog.el-dialog {
  background: var(--yg-bg-surface);
  border: 1px solid var(--yg-border-default);
  border-radius: 8px;
}

.notification-dialog .el-dialog__header {
  border-bottom: 1px solid var(--yg-border-default);
  padding-bottom: 12px;
}

.notification-dialog .el-dialog__title {
  color: var(--yg-text-primary);
  font-size: 16px;
  font-weight: 600;
}

.notification-dialog .el-dialog__body {
  padding-top: 16px;
  padding-bottom: 8px;
}

.notification-dialog .el-dialog__footer {
  border-top: 1px solid var(--yg-border-default);
  padding-top: 14px;
}
</style>
