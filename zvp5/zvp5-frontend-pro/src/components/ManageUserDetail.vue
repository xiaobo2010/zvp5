<script setup lang="ts">
// Read-only user detail for the Pro platform console (users tab).
//
// Mirrors what the main app's user page shows for the same person — profile,
// class, roles, personal hours and activity records — but stays inside the
// console as a modal so the list keeps its page/filter/search state. Every
// request is a GET; there are no write affordances here by design.
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import BubbleFeedback from '@/components/BubbleFeedback.vue'
import { ApiError } from '@/api/http'
import {
  fetchAdminGroupName,
  fetchAdminUserActivities,
  fetchAdminUserProfile,
  fetchAdminUserTime,
  type AdminUserActivity,
  type AdminUserProfile,
  type AdminUserTime
} from '@/api/admin-user-detail'

const props = defineProps<{
  userId: string
  /** List-row name, shown until the profile request lands. */
  fallbackName?: string
  /** List-row class name, used when the class lookup is unavailable. */
  fallbackClass?: string
}>()

const emit = defineEmits<{ close: [] }>()

const ACTIVITY_PAGE_SIZE = 5
const HOUR_ROWS = [
  { key: 'on-campus', label: '校内' },
  { key: 'off-campus', label: '校外' },
  { key: 'social-practice', label: '社会实践' }
] as const

const ROLE_LABELS: Record<string, string> = {
  admin: '管理员',
  department: '学生会',
  secretary: '团支书',
  student: '学生',
  auditor: '审计员',
  teacher: '教师',
  system: '系统'
}

const ACTIVITY_TYPES: Record<string, string> = {
  'on-campus': '校内',
  'off-campus': '校外',
  'social-practice': '社会实践',
  hybrid: '混合'
}

const dialog = ref<HTMLDialogElement | null>(null)

const profile = ref<AdminUserProfile | null>(null)
const className = ref('')
const time = ref<AdminUserTime | null>(null)

const profileLoading = ref(true)
const profileError = ref('')
const notFound = ref(false)

const timeLoading = ref(true)
const timeError = ref('')

const activities = ref<AdminUserActivity[]>([])
const activitiesTotal = ref(0)
const activitiesPage = ref(1)
const activitiesLoading = ref(true)
const activitiesLoaded = ref(false)
const activitiesError = ref('')

// Every async continuation checks this token; closing the modal or opening a
// different user invalidates stale responses instead of letting them paint.
let runId = 0
let closed = false

const title = computed(() => profile.value?.name || props.fallbackName || '用户资料')

const profileId = computed(() => {
  if (!profile.value) return ''
  return String(profile.value.id ?? '')
})

const classLabel = computed(
  () =>
    className.value ||
    props.fallbackClass ||
    profile.value?.class_id ||
    '未分班'
)

const displayRoles = computed(() => {
  const roles = (profile.value?.roles ?? []).filter(
    (role): role is string => typeof role === 'string' && role.length > 0
  )
  if (!roles.length) return ['学生']
  return Array.from(new Set(roles)).map((role) => ROLE_LABELS[role] ?? role)
})

const banSummary = computed(() => {
  const target = profile.value
  if (!target?.banned) return ''
  if (target.ban_permanent) return '永久封禁'
  const raw = target.ban_until
  if (!raw) return '封禁中'
  const until = new Date(raw)
  if (Number.isNaN(until.getTime())) return '封禁中'
  return `封禁至 ${formatDateTime(until)}`
})

const totalHours = computed(() =>
  HOUR_ROWS.reduce((sum, row) => sum + hoursOf(row.key), 0)
)

const hourRows = computed(() =>
  HOUR_ROWS.map((row) => ({ label: row.label, value: hoursOf(row.key) }))
)

const activityPages = computed(() =>
  Math.max(1, Math.ceil(activitiesTotal.value / ACTIVITY_PAGE_SIZE))
)

function hoursOf(key: (typeof HOUR_ROWS)[number]['key']): number {
  const value = Number(time.value?.[key] ?? 0)
  return Number.isFinite(value) ? Math.max(0, value) : 0
}

function pad(value: number): string {
  return String(value).padStart(2, '0')
}

function formatDateTime(value: Date): string {
  return `${value.getFullYear()}-${pad(value.getMonth() + 1)}-${pad(
    value.getDate()
  )} ${pad(value.getHours())}:${pad(value.getMinutes())}`
}

function formatDate(value: string | undefined): string {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
}

function formatHours(value: unknown): string {
  const number = Number(value)
  return Number.isFinite(number) ? Math.max(0, number).toFixed(1) : '0.0'
}

function messageOf(reason: unknown, fallback: string): string {
  if (reason instanceof ApiError) {
    if (reason.status === 401) return '登录状态已失效，请重新登录。'
    if (reason.status === 403) return '当前账号没有查看该用户资料的权限。'
    if (reason.status === 404) return '用户不存在或已被删除。'
    return reason.message || fallback
  }
  return fallback
}

function isNotFound(reason: unknown): boolean {
  return reason instanceof ApiError && reason.status === 404
}

function stale(run: number): boolean {
  return closed || run !== runId
}

async function loadProfile(run: number) {
  profileLoading.value = true
  profileError.value = ''
  notFound.value = false
  try {
    const data = await fetchAdminUserProfile(props.userId)
    if (stale(run)) return
    if (!data?._id) {
      notFound.value = true
      return
    }
    profile.value = data
    if (data.class_id) {
      void fetchAdminGroupName(data.class_id).then((name) => {
        if (!stale(run)) className.value = name
      })
    }
  } catch (reason) {
    if (stale(run)) return
    if (isNotFound(reason)) {
      notFound.value = true
      return
    }
    profileError.value = messageOf(reason, '用户资料加载失败，请稍后重试。')
  } finally {
    if (!stale(run)) profileLoading.value = false
  }
}

async function loadTime(run: number) {
  timeLoading.value = true
  timeError.value = ''
  try {
    const data = await fetchAdminUserTime(props.userId)
    if (stale(run)) return
    time.value = data ?? null
  } catch (reason) {
    if (stale(run)) return
    time.value = null
    timeError.value = messageOf(reason, '个人时长加载失败，请稍后重试。')
  } finally {
    if (!stale(run)) timeLoading.value = false
  }
}

async function loadActivities(run: number, page: number, revertTo?: number) {
  activitiesLoading.value = true
  activitiesError.value = ''
  try {
    const data = await fetchAdminUserActivities(props.userId, page, ACTIVITY_PAGE_SIZE)
    if (stale(run)) return
    activities.value = Array.isArray(data?.activities) ? data.activities : []
    const total = Number(data?.total)
    activitiesTotal.value = Number.isFinite(total) ? Math.max(0, total) : 0
    activitiesPage.value = page
    activitiesLoaded.value = true
  } catch (reason) {
    if (stale(run)) return
    if (revertTo !== undefined) activitiesPage.value = revertTo
    activitiesError.value = messageOf(reason, '活动记录加载失败，请稍后重试。')
  } finally {
    if (!stale(run)) activitiesLoading.value = false
  }
}

function start() {
  if (closed) return
  runId += 1
  const run = runId

  profile.value = null
  className.value = ''
  profileError.value = ''
  notFound.value = false
  profileLoading.value = true

  time.value = null
  timeError.value = ''
  timeLoading.value = true

  activities.value = []
  activitiesTotal.value = 0
  activitiesPage.value = 1
  activitiesError.value = ''
  activitiesLoaded.value = false
  activitiesLoading.value = true

  void loadProfile(run)
  void loadTime(run)
  void loadActivities(run, 1)
}

function retryTime() {
  if (timeLoading.value) return
  void loadTime(runId)
}

function goActivities(delta: number) {
  if (activitiesLoading.value) return
  const target = activitiesPage.value + delta
  if (target < 1 || target > activityPages.value) return
  void loadActivities(runId, target, activitiesPage.value)
}

function requestClose() {
  if (closed) return
  closed = true
  runId += 1
  dialog.value?.close()
  emit('close')
}

function onBackdropClick(event: MouseEvent) {
  if (event.target === dialog.value) requestClose()
}

function activityStatus(activity: AdminUserActivity): string {
  const status = String(activity.mine?.status ?? activity.status ?? '')
  if (status === 'effective') return '已确认'
  if (status === 'refused') return '已拒绝'
  if (status === 'pending') return '待确认'
  return status || '未知'
}

function activityTone(activity: AdminUserActivity): string {
  const status = String(activity.mine?.status ?? activity.status ?? '')
  if (status === 'effective') return 'detail-badge--good'
  if (status === 'refused') return 'detail-badge--bad'
  if (status === 'pending') return 'detail-badge--warn'
  return 'detail-badge--info'
}

function activityMeta(activity: AdminUserActivity): string {
  const parts: string[] = []
  const date = formatDate(activity.date)
  if (date) parts.push(date)
  // A personal record's own mode is the authoritative volunteer type; the
  // activity-level `type` can be `hybrid`, which says nothing about this row.
  const mode = activity.mine?.mode || activity.type || ''
  const type = mode ? ACTIVITY_TYPES[mode] ?? mode : ''
  if (type) parts.push(type)
  const duration = Number(activity.mine?.duration)
  if (Number.isFinite(duration)) parts.push(`${formatHours(duration)}h`)
  const place = (activity.place ?? '').trim()
  if (place) parts.push(place)
  return parts.join(' · ') || '暂无活动时间记录'
}

onMounted(() => {
  dialog.value?.showModal()
  start()
})

onBeforeUnmount(() => {
  closed = true
  runId += 1
  dialog.value?.close()
})
</script>

<template>
  <dialog
    ref="dialog"
    class="manage-user-detail"
    aria-labelledby="manage-user-detail-title"
    aria-describedby="manage-user-detail-desc"
    @cancel.prevent="requestClose"
    @click="onBackdropClick"
  >
    <div class="manage-user-detail__panel">
      <p id="manage-user-detail-desc" class="detail-sr-only">
        只读用户资料，包含基本信息、个人时长与活动记录。
      </p>

      <header class="manage-user-detail__header">
        <div class="manage-user-detail__heading">
          <p class="manage-user-detail__eyebrow">平台管理 · 用户资料</p>
          <h2 id="manage-user-detail-title" class="manage-user-detail__title">
            {{ title }}
          </h2>
          <p v-if="profileId || classLabel" class="manage-user-detail__subtitle">
            <span v-if="profileId" class="detail-mono">{{ profileId }}</span>
            <span>{{ classLabel }}</span>
          </p>
        </div>
        <button
          type="button"
          class="manage-user-detail__close"
          autofocus
          @click="requestClose"
        >
          关闭
        </button>
      </header>

      <div v-if="profileLoading" class="detail-state" role="status">
        <span class="detail-spinner" aria-hidden="true"></span>
        正在读取用户资料…
      </div>

      <div
        v-else-if="profileError"
        class="detail-state detail-state--error"
        role="alert"
      >
        <BubbleFeedback class="glass-feedback--error">{{ profileError }}</BubbleFeedback>
        <button type="button" class="detail-button" @click="start">重试</button>
      </div>

      <div v-else-if="notFound || !profile" class="detail-state" role="alert">
        用户不存在或已被删除。
      </div>

      <template v-else>
        <section class="detail-section" aria-labelledby="manage-user-detail-profile">
          <div class="detail-section__head">
            <h3 id="manage-user-detail-profile" class="detail-section__title">基本信息</h3>
            <span
              class="detail-badge"
              :class="profile.banned ? 'detail-badge--bad' : 'detail-badge--good'"
            >
              {{ profile.banned ? '已封禁' : '正常' }}
            </span>
          </div>

          <dl class="detail-grid">
            <div>
              <dt>姓名</dt>
              <dd>{{ profile.name || '未命名用户' }}</dd>
            </div>
            <div>
              <dt>学号</dt>
              <dd class="detail-mono">{{ profile.id ?? '—' }}</dd>
            </div>
            <div>
              <dt>班级</dt>
              <dd>{{ classLabel }}</dd>
            </div>
            <div>
              <dt>角色</dt>
              <dd class="detail-chips">
                <span v-for="role in displayRoles" :key="role" class="detail-chip">
                  {{ role }}
                </span>
              </dd>
            </div>
          </dl>

          <p v-if="profile.banned" class="detail-ban" role="note">
            {{ banSummary }}
            <template v-if="profile.ban_reason"> · 原因：{{ profile.ban_reason }}</template>
          </p>
        </section>

        <section class="detail-section" aria-labelledby="manage-user-detail-hours">
          <div class="detail-section__head">
            <h3 id="manage-user-detail-hours" class="detail-section__title">个人时长</h3>
            <span v-if="timeLoading" class="detail-section__hint" role="status">读取中…</span>
          </div>

          <div v-if="timeError" class="detail-inline-error" role="alert">
            <span>{{ timeError }}</span>
            <button
              type="button"
              class="detail-button"
              :disabled="timeLoading"
              @click="retryTime"
            >
              重试
            </button>
          </div>

          <template v-else>
            <dl class="detail-grid detail-grid--hours" :aria-busy="timeLoading">
              <div v-for="row in hourRows" :key="row.label">
                <dt>{{ row.label }}</dt>
                <dd>
                  {{ timeLoading ? '—' : formatHours(row.value) }}<span class="detail-unit">h</span>
                </dd>
              </div>
              <div class="detail-total">
                <dt>总时长</dt>
                <dd>
                  {{ timeLoading ? '—' : formatHours(totalHours) }}<span class="detail-unit">h</span>
                </dd>
              </div>
            </dl>
            <p class="detail-note">
              仅统计活动与成员记录均为已确认（effective）的时长，按接口原始口径展示，不含校内/校外互补折算。
            </p>
          </template>
        </section>

        <section class="detail-section" aria-labelledby="manage-user-detail-activities">
          <div class="detail-section__head">
            <h3 id="manage-user-detail-activities" class="detail-section__title">活动记录</h3>
            <span v-if="activitiesLoaded" class="detail-badge detail-badge--info">
              共 {{ activitiesTotal }} 条
            </span>
          </div>

          <div v-if="activitiesLoading && !activitiesLoaded" class="detail-state" role="status">
            <span class="detail-spinner" aria-hidden="true"></span>
            正在读取活动记录…
          </div>

          <div
            v-else-if="activitiesError && !activitiesLoaded"
            class="detail-state detail-state--error"
            role="alert"
          >
            <BubbleFeedback class="glass-feedback--error">{{ activitiesError }}</BubbleFeedback>
            <button type="button" class="detail-button" @click="goActivities(0)">重试</button>
          </div>

          <template v-else>
            <BubbleFeedback
              v-if="activitiesError"
              class="detail-inline-feedback glass-feedback--error"
              role="alert"
            >
              {{ activitiesError }}
            </BubbleFeedback>

            <p v-if="!activities.length" class="detail-empty">暂无活动记录。</p>
            <ul v-else class="detail-activity-list" :class="{ 'is-loading': activitiesLoading }">
              <li
                v-for="activity in activities"
                :key="activity.mine?._id || activity._id"
                class="detail-activity"
              >
                <div class="detail-activity__main">
                  <span class="detail-activity__name">{{ activity.name || '未命名活动' }}</span>
                  <span class="detail-activity__meta">{{ activityMeta(activity) }}</span>
                </div>
                <span class="detail-badge" :class="activityTone(activity)">
                  {{ activityStatus(activity) }}
                </span>
              </li>
            </ul>

            <nav
              v-if="activitiesTotal > ACTIVITY_PAGE_SIZE"
              class="detail-pager"
              aria-label="活动记录分页"
            >
              <span class="detail-pager__info" aria-live="polite">
                第 {{ activitiesPage }} / {{ activityPages }} 页 · 每页
                {{ ACTIVITY_PAGE_SIZE }} 条
              </span>
              <div class="detail-pager__actions">
                <button
                  type="button"
                  class="detail-button"
                  :disabled="activitiesLoading || activitiesPage <= 1"
                  @click="goActivities(-1)"
                >
                  上一页
                </button>
                <button
                  type="button"
                  class="detail-button"
                  :disabled="activitiesLoading || activitiesPage >= activityPages"
                  @click="goActivities(1)"
                >
                  下一页
                </button>
              </div>
            </nav>
          </template>
        </section>
      </template>
    </div>
  </dialog>
</template>

<style scoped>
.manage-user-detail {
  width: min(760px, calc(100% - 32px));
  max-width: 100%;
  max-height: min(86vh, 900px);
  margin: auto;
  padding: 0;
  overflow: hidden;
  border: 1px solid var(--glass-border);
  border-radius: var(--radius-xl);
  background: var(--surface-matte);
  color: var(--color-text-primary);
  box-shadow: var(--glass-floating-shadow);
}

.manage-user-detail::backdrop {
  background: rgb(16 27 50 / 42%);
}

.manage-user-detail__panel {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  max-height: min(86vh, 900px);
  padding: var(--space-5);
  overflow-y: auto;
  overscroll-behavior: contain;
}

.detail-sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}

.manage-user-detail__header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-3);
}

.manage-user-detail__heading {
  min-width: 0;
}

.manage-user-detail__eyebrow {
  font-size: var(--font-size-caption);
  font-weight: 600;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--color-text-tertiary);
}

.manage-user-detail__title {
  margin-top: 2px;
  font-size: var(--font-size-headline);
  font-weight: 600;
  line-height: var(--line-height-snug);
  color: var(--color-text-primary);
  overflow-wrap: anywhere;
}

.manage-user-detail__subtitle {
  display: flex;
  flex-wrap: wrap;
  gap: 4px var(--space-2);
  margin-top: var(--space-1);
  font-size: var(--font-size-caption);
  color: var(--color-text-secondary);
}

.manage-user-detail__close {
  flex: 0 0 auto;
  min-width: 44px;
  min-height: 44px;
  padding: 0 var(--space-3);
  border: 1px solid var(--color-border-moderate);
  border-radius: var(--radius-pill);
  background: rgba(255, 255, 255, 0.72);
  color: var(--color-text-primary);
  font: inherit;
  font-size: var(--font-size-caption);
  font-weight: 600;
  cursor: pointer;
}

.manage-user-detail__close:focus-visible,
.detail-pager__actions button:focus-visible,
.detail-inline-error button:focus-visible,
.detail-state button:focus-visible {
  outline: 2px solid var(--color-brand);
  outline-offset: 2px;
}

.manage-user-detail__close:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.detail-state {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-wrap: wrap;
  padding: var(--space-4) 0;
  font-size: var(--font-size-caption);
  color: var(--color-text-secondary);
}

.detail-state--error {
  flex-direction: column;
  align-items: flex-start;
  color: var(--color-text-primary);
}

.detail-button {
  min-height: 44px;
  padding: 0 var(--space-4);
  border: 1px solid var(--color-border-moderate);
  border-radius: var(--radius-pill);
  background: rgba(255, 255, 255, 0.72);
  color: var(--color-text-primary);
  font: inherit;
  font-size: var(--font-size-caption);
  font-weight: 600;
  cursor: pointer;
}

.detail-button:hover:not(:disabled) {
  border-color: var(--color-brand);
  color: var(--color-brand);
}

.detail-button:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.detail-inline-feedback {
  margin-top: var(--space-2);
}

.detail-spinner {
  width: 14px;
  height: 14px;
  border-radius: 50%;
  border: 2px solid var(--color-border-moderate);
  border-top-color: var(--color-brand);
  animation: detail-spin 720ms linear infinite;
}

@keyframes detail-spin {
  to {
    transform: rotate(360deg);
  }
}

.detail-section {
  padding-top: var(--space-4);
  border-top: 1px solid var(--color-border-hairline);
}

.detail-section__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
  flex-wrap: wrap;
  margin-bottom: var(--space-3);
}

.detail-section__title {
  font-size: var(--font-size-callout);
  font-weight: 600;
  color: var(--color-text-primary);
}

.detail-section__hint {
  font-size: var(--font-size-caption);
  color: var(--color-text-tertiary);
}

.detail-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: var(--space-2);
  margin: 0;
}

.detail-grid > div {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
  padding: var(--space-2) var(--space-3);
  border: 1px solid var(--color-border-hairline);
  border-radius: var(--radius-md);
  background: rgba(255, 255, 255, 0.5);
}

.detail-grid dt {
  font-size: var(--font-size-caption);
  color: var(--color-text-tertiary);
}

.detail-grid dd {
  margin: 0;
  font-size: var(--font-size-callout);
  font-weight: 600;
  color: var(--color-text-primary);
  overflow-wrap: anywhere;
}

.detail-grid--hours {
  grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
}

.detail-total {
  border-color: var(--tone-blue-bg);
  background: var(--tone-blue-bg);
}

.detail-note {
  margin-top: var(--space-2);
  font-size: var(--font-size-caption);
  color: var(--color-text-tertiary);
  line-height: var(--line-height-normal);
}

.detail-unit {
  margin-left: 2px;
  font-size: var(--font-size-caption);
  font-weight: 500;
  color: var(--color-text-secondary);
}

.detail-mono {
  font-family: 'SF Mono', 'Menlo', 'Monaco', 'Courier New', monospace;
  font-size: var(--font-size-caption);
  font-weight: 500;
}

.detail-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  font-weight: 500;
}

.detail-chip {
  padding: 2px 8px;
  border-radius: var(--radius-pill);
  background: var(--tone-blue-bg);
  color: var(--accent-ink);
  font-size: 11px;
  font-weight: 600;
  line-height: 1.5;
}

.detail-badge {
  display: inline-flex;
  align-items: center;
  padding: 3px 9px;
  border-radius: var(--radius-pill);
  font-size: 11px;
  font-weight: 600;
  line-height: 1.4;
  white-space: nowrap;
}

.detail-badge--good {
  background: var(--tone-green-bg);
  color: #1d7a34;
}

.detail-badge--warn {
  background: var(--tone-amber-bg);
  color: #8a5a00;
}

.detail-badge--bad {
  background: rgba(255, 59, 48, 0.12);
  color: #b3261e;
}

.detail-badge--info {
  background: var(--tone-blue-bg);
  color: var(--accent-ink);
}

.detail-ban {
  margin-top: var(--space-3);
  padding: var(--space-2) var(--space-3);
  border-radius: var(--radius-md);
  background: rgba(255, 59, 48, 0.08);
  color: #b3261e;
  font-size: var(--font-size-caption);
  overflow-wrap: anywhere;
}

.detail-inline-error {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  flex-wrap: wrap;
  padding: var(--space-3);
  border-radius: var(--radius-md);
  background: rgba(255, 59, 48, 0.08);
  color: var(--color-text-primary);
  font-size: var(--font-size-caption);
}

.detail-empty {
  padding: var(--space-3) 0;
  font-size: var(--font-size-caption);
  color: var(--color-text-tertiary);
}

.detail-activity-list {
  list-style: none;
  display: flex;
  flex-direction: column;
  margin: 0;
  padding: 0;
  transition: opacity var(--duration-fast) var(--ease-smooth);
}

.detail-activity-list.is-loading {
  opacity: 0.55;
}

.detail-activity {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-3);
  padding: var(--space-2) 0;
  border-bottom: 1px solid var(--color-border-hairline);
  min-width: 0;
}

.detail-activity:last-child {
  border-bottom: none;
}

.detail-activity__main {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
  flex: 1;
}

.detail-activity__name {
  font-size: var(--font-size-callout);
  font-weight: 600;
  color: var(--color-text-primary);
  overflow-wrap: anywhere;
}

.detail-activity__meta {
  font-size: var(--font-size-caption);
  color: var(--color-text-secondary);
  overflow-wrap: anywhere;
}

.detail-pager {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  flex-wrap: wrap;
  margin-top: var(--space-3);
}

.detail-pager__info {
  font-size: var(--font-size-caption);
  color: var(--color-text-secondary);
}

.detail-pager__actions {
  display: flex;
  gap: var(--space-2);
}

@media (max-width: 599px) {
  .manage-user-detail {
    width: 100%;
    max-height: calc(100% - 16px);
    border-radius: var(--radius-lg);
  }

  .manage-user-detail__panel {
    max-height: calc(100vh - 16px);
    padding: var(--space-4);
  }

  .manage-user-detail__header {
    gap: var(--space-2);
  }
}

@media (prefers-reduced-motion: reduce) {
  .detail-spinner {
    animation: none;
  }

  .detail-activity-list {
    transition: none;
  }
}
</style>
