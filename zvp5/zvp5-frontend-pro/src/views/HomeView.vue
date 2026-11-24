<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue'
import { RouterLink, useRouter } from 'vue-router'
import {
  CanaryApiError,
  fetchActivities,
  fetchGroupName,
  fetchProfile,
  fetchTime,
  isLoggedIn
} from '@/api/canary'
import type { CanaryActivity, CanaryTime, CanaryUser } from '@/api/canary'
import { fetchPendingReviews, fetchRecentAgentRuns, fetchSuspiciousLogs, LOG_TYPE_LABELS } from '@/api/home'
import { ApiError } from '@/api/http'
import BubbleFeedback from '@/components/BubbleFeedback.vue'
import VolunteerStack from '@/components/VolunteerStack.vue'
import { mapActivity, useVolunteerStackExpansion } from '@/components/volunteerStack'
import { renderMarkdown } from '@/utils/markdown'

type HourKey = keyof CanaryTime

interface HourCategory {
  key: HourKey
  title: string
  actual: number
  standard: number
  converted: number
  bonus: number
  percent: number
  barPercent: number
}

const HOUR_STANDARDS: Record<HourKey, number> = {
  'on-campus': 25,
  'off-campus': 15,
  'social-practice': 18
}

const HOUR_CATEGORIES: { key: HourKey; title: string }[] = [
  { key: 'on-campus', title: '校内时长' },
  { key: 'off-campus', title: '校外时长' },
  { key: 'social-practice', title: '社会实践' }
]

const ROLE_LABELS: Record<string, string> = {
  student: '学生',
  volunteer: '志愿者',
  admin: '管理员',
  department: '学生会',
  secretary: '秘书',
  auditor: '审计',
  teacher: '教师',
  system: '系统'
}

const WEEKDAYS = ['星期日', '星期一', '星期二', '星期三', '星期四', '星期五', '星期六']
const SPRING = 'cubic-bezier(0.34, 1.56, 0.64, 1)'
const EASE_OUT = 'cubic-bezier(0.22, 1, 0.36, 1)'

const RECENT_RECORD_LIMIT = 3
const router = useRouter()

const homeRef = ref<HTMLElement | null>(null)
const hoursPanelRef = ref<HTMLElement | null>(null)
const totalValueRef = ref<HTMLElement | null>(null)
const refreshIconRef = ref<SVGSVGElement | null>(null)

const profile = ref<CanaryUser | null>(null)
const time = ref<CanaryTime | null>(null)
const activities = ref<CanaryActivity[]>([])
const recentError = ref('')
const pendingReviews = ref<CanaryActivity[]>([])
const agentRuns = ref<Awaited<ReturnType<typeof fetchRecentAgentRuns>>>([])
const suspiciousLogs = ref<Awaited<ReturnType<typeof fetchSuspiciousLogs>>>([])
const workErrors = ref({ pending: '', agent: '', logs: '' })
const groupName = ref('')

// 首帧即确定登录状态，避免先闪骨架再跳到「需要登录」。
const initiallySignedIn = isLoggedIn()

const loading = ref(initiallySignedIn)
const unauthenticated = ref(!initiallySignedIn)
const error = ref('')
const hoursRefreshing = ref(false)
const refreshFeedback = ref<{ text: string; tone: 'success' | 'error' | 'warn' } | null>(null)

const greetingVisible = ref(false)
const sectionsVisible = ref<Record<string, boolean>>({
  identity: false,
  hours: false,
  work: false
})

const currentTime = ref(new Date())
let clockInterval: ReturnType<typeof setInterval> | null = null

const updateClock = () => {
  currentTime.value = new Date()
}

// Analog clock hand angles
const hourAngle = computed(() => {
  const hours = currentTime.value.getHours()
  const minutes = currentTime.value.getMinutes()
  return ((hours % 12) * 30) + (minutes * 0.5)
})

const minuteAngle = computed(() => {
  const minutes = currentTime.value.getMinutes()
  const seconds = currentTime.value.getSeconds()
  return (minutes * 6) + (seconds * 0.1)
})

const secondAngle = computed(() => {
  const seconds = currentTime.value.getSeconds()
  return seconds * 6
})

const timeString = computed(() => {
  const h = currentTime.value.getHours().toString().padStart(2, '0')
  const m = currentTime.value.getMinutes().toString().padStart(2, '0')
  return `${h}:${m}`
})

let observer: IntersectionObserver | null = null
let greetingTimeout: ReturnType<typeof setTimeout> | null = null
let refreshAnimations: Animation[] = []
let loadSequence = 0
let disposed = false

const displayName = computed(() => profile.value?.name?.trim() ?? '')
const isAdmin = computed(() => profile.value?.roles?.includes('admin') ?? false)
const isDepartment = computed(() => profile.value?.roles?.includes('department') ?? false)
const showPending = computed(() => isDepartment.value && !isAdmin.value)
const showAgent = computed(() => isDepartment.value || isAdmin.value)

const avatarInitial = computed(() => displayName.value.slice(0, 1) || '—')

const studentId = computed(() => {
  const id = profile.value?.id
  return id === undefined || id === null || id === '' ? '未设置' : String(id)
})

const classLabel = computed(
  () => groupName.value || profile.value?.class_id || '未设置班级'
)

const rolesLabel = computed(() => {
  const roles = (profile.value?.roles ?? []) as string[]
  if (!roles.length) return '学生'
  return Array.from(new Set(roles))
    .map((role) => ROLE_LABELS[role] ?? role)
    .join(' / ')
})

const greetingText = computed(() => {
  const hour = new Date().getHours()
  if (hour < 5) return '晚上好'
  if (hour < 9) return '早上好'
  if (hour < 12) return '上午好'
  if (hour < 18) return '下午好'
  return '晚上好'
})

const todayLabel = computed(() => {
  const now = new Date()
  return `${now.getFullYear()}年${now.getMonth() + 1}月${now.getDate()}日 ${WEEKDAYS[now.getDay()]}`
})

function rawHours(key: HourKey): number {
  const value = Number(time.value?.[key] ?? 0)
  return Number.isFinite(value) ? Math.max(0, value) : 0
}

const totalHours = computed(
  () => rawHours('on-campus') + rawHours('off-campus') + rawHours('social-practice')
)

const totalHoursLabel = computed(() => formatHours(totalHours.value))

const hourCategories = computed<HourCategory[]>(() => {
  const onCampus = rawHours('on-campus')
  const offCampus = rawHours('off-campus')
  const socialPractice = rawHours('social-practice')

  // 页面说明：超标的校外时长按 2:1 折算为校内，超标的校内时长按 3:1 折算为校外。
  const onCampusBonus = Math.max(0, offCampus - HOUR_STANDARDS['off-campus']) / 2
  const offCampusBonus = Math.max(0, onCampus - HOUR_STANDARDS['on-campus']) / 3

  const converted: Record<HourKey, number> = {
    'on-campus': onCampus + onCampusBonus,
    'off-campus': offCampus + offCampusBonus,
    'social-practice': socialPractice
  }

  const bonuses: Record<HourKey, number> = {
    'on-campus': onCampusBonus,
    'off-campus': offCampusBonus,
    'social-practice': 0
  }

  const actuals: Record<HourKey, number> = {
    'on-campus': onCampus,
    'off-campus': offCampus,
    'social-practice': socialPractice
  }

  return HOUR_CATEGORIES.map(({ key, title }) => {
    const standard = HOUR_STANDARDS[key]
    const actual = actuals[key]
    const percent = Math.max(0, Math.round((actual / standard) * 100))
    return {
      key,
      title,
      actual,
      standard,
      converted: converted[key],
      bonus: bonuses[key],
      percent,
      barPercent: Math.min(100, percent)
    }
  })
})

function progressTone(percent: number): string {
  if (percent >= 100) return 'green'
  if (percent >= 70) return 'blue'
  if (percent >= 40) return 'amber'
  return 'rose'
}

const recentActivities = computed(() =>
  activities.value.filter((activity) => activity.mine).slice(0, RECENT_RECORD_LIMIT).map(mapActivity)
)
const expandedActivityId = useVolunteerStackExpansion(() => recentActivities.value, () => studentId.value)

function agentResultPreview(source: string): string {
  // The renderer escapes source HTML. Keep inline formatting, flatten blocks,
  // and remove link elements so a clipped preview has no hidden tab stops.
  return renderMarkdown(source)
    .replace(/<\/?a\b[^>]*>/gi, '')
    .replace(/<\/?(?:h[1-6]|p|li|ul|ol|blockquote|pre|br|hr)\b[^>]*>/gi, ' ')
    .replace(/\s+/g, ' ')
    .trim()
}

const agentPreviews = computed(() => agentRuns.value.map((run) => ({
  ...run,
  previewHtml: run.result ? agentResultPreview(run.result) : '',
  fallback: run.error_message || (run.resultUnavailable ? '结果内容暂不可用' : '')
})))

const RUN_STATUSES: Record<string, string> = {
  queued: '排队中', running: '运行中', succeeded: '已完成', failed: '失败',
  cancelled: '已取消', interrupted: '已中断'
}

function pendingLink(id: string) {
  return { name: 'volunteer-detail', params: { id } }
}

function formatHours(value: number): string {
  return Number.isFinite(value) ? value.toFixed(1) : '0.0'
}

function formatCompactHours(value: number): string {
  if (!Number.isFinite(value) || value <= 0) return ''
  return Number.isInteger(value) ? String(value) : value.toFixed(1)
}

function formatActivityDate(value: string): string {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  const now = new Date()
  const yearPrefix = date.getFullYear() === now.getFullYear() ? '' : `${date.getFullYear()}年`
  return `${yearPrefix}${date.getMonth() + 1}月${date.getDate()}日`
}

function pendingMeta(activity: CanaryActivity): string {
  const place = (activity.place ?? '').trim()
  const date = formatActivityDate(activity.date)
  return [place, date].filter(Boolean).join(' · ')
}

function reduceMotion(): boolean {
  return window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false
}

function readableError(reason: unknown, fallback: string): string {
  return reason instanceof CanaryApiError || reason instanceof ApiError ? reason.message : fallback
}

function isAuthError(reason: unknown): boolean {
  return (reason instanceof CanaryApiError || reason instanceof ApiError) && reason.status === 401
}

function activityRows(page: { activities: CanaryActivity[] }): CanaryActivity[] {
  if (!Array.isArray(page?.activities)) throw new Error('Invalid activity page')
  return page.activities
}

function fetchRecentRecords() {
  return fetchActivities('mine', 1, RECENT_RECORD_LIMIT, '').then(activityRows)
}

function cancelRefreshAnimations() {
  refreshAnimations.forEach((animation) => {
    try {
      animation.cancel()
    } catch {
      /* already finished */
    }
  })
  refreshAnimations = []
}

function observeSections() {
  if (disposed) return
  const root = homeRef.value
  if (!root) return

  const targets = root.querySelectorAll<HTMLElement>('[data-section]')

  if (typeof IntersectionObserver === 'undefined') {
    targets.forEach((target) => {
      const id = target.dataset.section
      if (id) sectionsVisible.value[id] = true
    })
    return
  }

  if (!observer) {
    observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (!entry.isIntersecting) return
          const id = entry.target.getAttribute('data-section')
          if (id && !sectionsVisible.value[id]) sectionsVisible.value[id] = true
          // 一次进入即可，元素显示后立刻解除观察，避免持续回调。
          observer?.unobserve(entry.target)
        })
      },
      { threshold: 0, rootMargin: '-60px' }
    )
  }

  targets.forEach((target) => {
    const id = target.dataset.section
    if (id && sectionsVisible.value[id]) return
    observer?.observe(target)
  })
}

async function loadPage() {
  if (disposed) return
  const sequence = ++loadSequence
  loading.value = true
  error.value = ''
  unauthenticated.value = false
  recentError.value = ''
  workErrors.value = { pending: '', agent: '', logs: '' }

  try {
    const [profileResult, timeResult, activitiesResult] = await Promise.allSettled([
      fetchProfile(),
      fetchTime(),
      fetchRecentRecords()
    ])

    if (sequence !== loadSequence || disposed) return

    if ([profileResult, timeResult, activitiesResult].some(
      (result) => result.status === 'rejected' && isAuthError(result.reason)
    )) {
      profile.value = null
      time.value = null
      activities.value = []
      groupName.value = ''
      unauthenticated.value = true
      return
    }

    const failure = [profileResult, timeResult].find(
      (result): result is PromiseRejectedResult => result.status === 'rejected'
    )
    if (failure) {
      error.value = readableError(failure.reason, '首页数据加载失败，请稍后重试。')
      return
    }

    if (profileResult.status === 'fulfilled') profile.value = profileResult.value
    if (timeResult.status === 'fulfilled') time.value = timeResult.value
    activities.value = activitiesResult.status === 'fulfilled' ? activitiesResult.value : []
    if (activitiesResult.status === 'rejected') {
      recentError.value = readableError(activitiesResult.reason, '最近记录加载失败，请稍后重试。')
    }

    const classId = profile.value?.class_id
    const [pending, agent, logs, className] = await Promise.allSettled([
      showPending.value ? fetchPendingReviews() : Promise.resolve([]),
      showAgent.value ? fetchRecentAgentRuns() : Promise.resolve([]),
      isAdmin.value ? fetchSuspiciousLogs() : Promise.resolve([]),
      classId ? fetchGroupName(classId) : Promise.resolve('')
    ])
    if (sequence !== loadSequence || disposed) return

    if ([pending, agent, logs].some(
      (result) => result.status === 'rejected' && isAuthError(result.reason)
    )) {
      profile.value = null
      unauthenticated.value = true
      return
    }

    pendingReviews.value = pending.status === 'fulfilled' ? pending.value : []
    agentRuns.value = agent.status === 'fulfilled' ? agent.value : []
    suspiciousLogs.value = logs.status === 'fulfilled' ? logs.value : []
    if (className.status === 'fulfilled') groupName.value = className.value
    if (pending.status === 'rejected') workErrors.value.pending = readableError(pending.reason, '待审核活动加载失败。')
    if (agent.status === 'rejected') workErrors.value.agent = readableError(agent.reason, 'Agent 结果加载失败。')
    if (logs.status === 'rejected') workErrors.value.logs = readableError(logs.reason, '可疑日志加载失败。')
  } finally {
    if (sequence === loadSequence && !disposed) {
      loading.value = false
      await nextTick()
      observeSections()
    }
  }
}

function handleRetry() {
  if (loading.value) return
  void loadPage()
}

function playRefreshAnimation(): Promise<void> {
  cancelRefreshAnimations()
  if (reduceMotion()) return Promise.resolve()

  const animations: Animation[] = []

  const icon = refreshIconRef.value
  if (icon) {
    animations.push(
      icon.animate(
        [
          { transform: 'rotate(0deg) scale(1)' },
          { transform: 'rotate(180deg) scale(1.12)', offset: 0.5 },
          { transform: 'rotate(360deg) scale(1)' }
        ],
        { duration: 600, easing: SPRING, fill: 'none' }
      )
    )
  }

  const total = totalValueRef.value
  if (total) {
    animations.push(
      total.animate(
        [
          { transform: 'scale(1)' },
          { transform: 'scale(1.06)', offset: 0.4 },
          { transform: 'scale(0.99)', offset: 0.7 },
          { transform: 'scale(1)' }
        ],
        { duration: 640, easing: SPRING, fill: 'backwards' }
      )
    )
  }

  const panel = hoursPanelRef.value
  if (panel) {
    const widths = hourCategories.value.map((category) => category.barPercent)

    panel.querySelectorAll<HTMLElement>('.hour-category__actual').forEach((el, index) => {
      animations.push(
        el.animate(
          [
            { transform: 'scale(1)' },
            { transform: 'scale(1.12)', offset: 0.4 },
            { transform: 'scale(0.97)', offset: 0.72 },
            { transform: 'scale(1)' }
          ],
          {
            duration: 640,
            delay: 60 + index * 70,
            easing: SPRING,
            fill: 'backwards'
          }
        )
      )
    })

    // 进度条从 0 生长到新比例。用 transform 而不是 width，避免刷新时触发布局。
    panel.querySelectorAll<HTMLElement>('.hour-category__fill').forEach((el, index) => {
      const target = (widths[index] ?? 0) / 100
      animations.push(
        el.animate(
          [{ transform: 'scaleX(0)' }, { transform: `scaleX(${target})` }],
          {
            duration: 560,
            delay: index * 80,
            easing: EASE_OUT,
            fill: 'backwards'
          }
        )
      )
    })
  }

  refreshAnimations = animations
  if (!animations.length) return Promise.resolve()
  return Promise.allSettled(animations.map((animation) => animation.finished)).then(
    () => undefined
  )
}

async function handleHoursRefresh() {
  if (hoursRefreshing.value || loading.value || unauthenticated.value || !profile.value) {
    return
  }

  hoursRefreshing.value = true
  refreshFeedback.value = null

  try {
    const [timeResult, activitiesResult] = await Promise.allSettled([
      fetchTime(),
      fetchRecentRecords()
    ])

    if (disposed) return

    if ([timeResult, activitiesResult].some(
      (result) => result.status === 'rejected' && isAuthError(result.reason)
    )) {
      unauthenticated.value = true
      profile.value = null
      return
    }

    if (activitiesResult.status === 'fulfilled') {
      activities.value = activitiesResult.value
      recentError.value = ''
    }

    if (timeResult.status === 'rejected') {
      refreshFeedback.value = {
        text: readableError(timeResult.reason, '时长刷新失败，请稍后重试。'),
        tone: 'error',
      }
      return
    }

    time.value = timeResult.value
    refreshFeedback.value = activitiesResult.status === 'fulfilled'
      ? { text: '义工时长与活动记录已更新。', tone: 'success' }
      : { text: '义工时长已更新，活动记录暂未刷新。', tone: 'warn' }

    await nextTick()
    if (!disposed) await playRefreshAnimation()
  } finally {
    if (!disposed) hoursRefreshing.value = false
  }
}

onMounted(() => {
  greetingTimeout = setTimeout(() => {
    greetingVisible.value = true
    greetingTimeout = null
  }, 100)

  // The greeting clock is independent from authentication.
  updateClock()
  clockInterval = setInterval(updateClock, 1000)

  if (!initiallySignedIn) return

  void loadPage()
})

onUnmounted(() => {
  disposed = true
  loadSequence += 1
  if (observer) {
    observer.disconnect()
    observer = null
  }
  if (greetingTimeout) {
    clearTimeout(greetingTimeout)
    greetingTimeout = null
  }
  if (clockInterval) {
    clearInterval(clockInterval)
    clockInterval = null
  }
  cancelRefreshAnimations()
})
</script>

<template>
  <div id="home" ref="homeRef" class="home">
    <!-- Greeting -->
    <section class="greeting" :class="{ visible: greetingVisible }">
      <div class="greeting__copy">
        <p class="greeting__eyebrow">义工工作台</p>
        <h1 class="greeting__title">
          <template v-if="displayName">{{ greetingText }}，{{ displayName }}。</template>
          <template v-else-if="loading">
            {{ greetingText }}，<span class="greeting__skeleton" aria-hidden="true"></span>
          </template>
          <template v-else>{{ greetingText }}。</template>
        </h1>
      </div>
      <div class="greeting__panel">
        <div class="greeting__clock-orb">
          <div class="greeting__clock-light"></div>
          <time
            class="greeting__clock"
            :datetime="timeString"
            :aria-label="timeString"
            :style="{
              '--hour-angle': `${hourAngle}deg`,
              '--minute-angle': `${minuteAngle}deg`,
              '--second-angle': `${secondAngle}deg`
            }"
          >
            <svg class="clock-face" viewBox="0 0 100 100" aria-hidden="true">
              <!-- Hour tick marks -->
              <line v-for="i in 12" :key="`tick-${i}`"
                :x1="50" :y1="8"
                :x2="50" :y2="14"
                :transform="`rotate(${i * 30} 50 50)`"
                class="clock-tick"
              />

              <!-- Hour hand -->
              <line
                x1="50" y1="50"
                x2="50" y2="28"
                class="clock-hand clock-hand--hour"
                :style="{ transform: `rotate(var(--hour-angle))` }"
              />

              <!-- Minute hand -->
              <line
                x1="50" y1="50"
                x2="50" y2="18"
                class="clock-hand clock-hand--minute"
                :style="{ transform: `rotate(var(--minute-angle))` }"
              />

              <!-- Second hand -->
              <line
                x1="50" y1="50"
                x2="50" y2="15"
                class="clock-hand clock-hand--second"
                :style="{ transform: `rotate(var(--second-angle))` }"
              />

              <!-- Center cap -->
              <circle cx="50" cy="50" r="3" class="clock-center" />
            </svg>
          </time>
        </div>
        <div class="greeting__info">
          <p class="greeting__date">{{ todayLabel }}</p>
          <p class="greeting__now-label">现在</p>
        </div>
      </div>
    </section>

    <!-- Signed out -->
    <section v-if="unauthenticated" class="state-panel glass-panel" role="alert">
      <h2 class="state-panel__title">需要登录</h2>
      <BubbleFeedback class="glass-feedback--warn">
        登录后即可查看你的义工时长与最近记录。
      </BubbleFeedback>
      <RouterLink class="glass-button state-panel__action" to="/login?redirect=/">
        去登录
      </RouterLink>
    </section>

    <!-- First fetch -->
    <div v-else-if="loading" class="skeleton-layout" role="status" aria-live="polite">
      <span class="sr-only">正在加载首页数据…</span>

      <section class="identity-row visible" aria-hidden="true">
        <article class="identity-card">
          <div class="identity-card__header">
            <div class="skeleton-block skeleton-avatar"></div>
            <div class="identity-card__info">
              <div class="skeleton-block skeleton-line skeleton-line--title"></div>
              <div class="skeleton-block skeleton-line skeleton-line--caption"></div>
            </div>
          </div>
          <div class="identity-card__meta">
            <div class="skeleton-block skeleton-line skeleton-line--wide"></div>
            <div class="skeleton-block skeleton-line skeleton-line--medium"></div>
          </div>
          <div class="skeleton-block skeleton-button"></div>
        </article>

        <article class="total-hours">
          <div class="skeleton-stack skeleton-stack--center">
            <div class="skeleton-block skeleton-line skeleton-line--caption"></div>
            <div class="skeleton-block skeleton-line--stat"></div>
            <div class="skeleton-block skeleton-line skeleton-line--caption"></div>
          </div>
        </article>
      </section>

      <section class="hours-panel glass-panel visible" aria-hidden="true">
        <div class="hours-panel__header">
          <div class="skeleton-block skeleton-line--heading"></div>
          <div class="hours-panel__actions">
            <div class="skeleton-block skeleton-button skeleton-button--compact"></div>
            <div class="skeleton-block skeleton-button skeleton-button--compact"></div>
          </div>
        </div>
        <div class="hours-grid">
          <div v-for="n in 3" :key="n" class="hour-category">
            <div class="hour-category__header">
              <div class="skeleton-block skeleton-line--category"></div>
              <div class="skeleton-block skeleton-badge"></div>
            </div>
            <div class="skeleton-stack">
              <div class="skeleton-block skeleton-line--stat"></div>
              <div class="skeleton-block skeleton-line skeleton-line--wide"></div>
              <div class="skeleton-block skeleton-line skeleton-line--medium"></div>
            </div>
            <div class="skeleton-block skeleton-bar"></div>
          </div>
        </div>
      </section>

      <section class="work-lists visible" aria-hidden="true">
        <article class="work-list glass-panel work-list--wide">
          <div class="skeleton-block skeleton-line--heading skeleton-heading-gap"></div>
          <div v-for="row in 3" :key="row" class="skeleton-row">
            <div class="skeleton-row__content">
              <div class="skeleton-block skeleton-line skeleton-row__name"></div>
              <div class="skeleton-block skeleton-line skeleton-row__meta"></div>
            </div>
            <div class="skeleton-block skeleton-badge"></div>
          </div>
        </article>
      </section>
    </div>

    <!-- Load failure -->
    <section v-else-if="error" class="state-panel glass-panel" role="alert">
      <h2 class="state-panel__title">加载失败</h2>
      <BubbleFeedback class="glass-feedback--error">{{ error }}</BubbleFeedback>
      <button class="glass-button state-panel__action" type="button" @click="handleRetry">
        重试
      </button>
    </section>

    <template v-else>
      <!-- Identity + Total Hours -->
      <section
        id="profile"
        class="identity-row"
        data-section="identity"
        :class="{ visible: sectionsVisible.identity }"
      >
        <!-- Personal Identity Card -->
        <article class="identity-card">
          <div class="identity-card__header">
            <div class="identity-card__avatar">
              <span class="identity-card__avatar-text">{{ avatarInitial }}</span>
            </div>
            <div class="identity-card__info">
              <h2 class="identity-card__name">{{ displayName || '未命名用户' }}</h2>
              <p class="identity-card__number">学号 {{ studentId }}</p>
            </div>
          </div>
          <div class="identity-card__meta">
            <div class="identity-card__meta-item">
              <svg width="18" height="18" viewBox="0 0 18 18" fill="none" aria-hidden="true">
                <path d="M4 15V5.5C4 4.67157 4.67157 4 5.5 4H10C10.8284 4 11.5 4.67157 11.5 5.5V15" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/>
                <path d="M11.5 8H13.5C14.3284 8 15 8.67157 15 9.5V15" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/>
                <path d="M2.5 15H15.5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
                <path d="M6.5 7H9M6.5 10H9M6.5 13H9" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
              </svg>
              <span>{{ classLabel }}</span>
            </div>
            <div class="identity-card__meta-item">
              <svg width="18" height="18" viewBox="0 0 18 18" fill="none" aria-hidden="true">
                <circle cx="9" cy="6" r="2.5" stroke="currentColor" stroke-width="1.5"/>
                <path d="M4 14C4 11.7909 5.79086 10 8 10H10C12.2091 10 14 11.7909 14 14" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
              </svg>
              <span>{{ rolesLabel }}</span>
            </div>
          </div>
          <RouterLink class="identity-card__action glass-button" to="/me">
            编辑资料
          </RouterLink>
        </article>

        <!-- Total Hours Anchor -->
        <article class="total-hours">
          <p class="total-hours__label">累计记录</p>
          <p ref="totalValueRef" class="total-hours__value">
            {{ totalHoursLabel }}<span class="total-hours__unit">h</span>
          </p>
          <div class="total-hours__breakdown">
            <div
              v-for="category in hourCategories"
              :key="category.key"
              class="breakdown-item"
            >
              <span
                class="breakdown-dot"
                :class="`breakdown-dot--${category.key}`"
                aria-hidden="true"
              ></span>
              <span class="breakdown-label">{{ category.title.slice(0, 2) }}</span>
              <span class="breakdown-value">{{ formatHours(category.actual) }}</span>
            </div>
          </div>
        </article>
      </section>

      <!-- Hours Panel -->
      <section
        id="hours"
        ref="hoursPanelRef"
        class="hours-panel glass-panel"
        data-section="hours"
        :class="{ visible: sectionsVisible.hours }"
      >
        <div class="hours-panel__header">
          <h2 class="hours-panel__title">义工时长</h2>
          <div class="hours-panel__actions">
            <RouterLink class="glass-button glass-button--compact" to="/volunteers">查看记录</RouterLink>
            <button
              class="glass-button glass-button--compact hours-refresh-btn"
              type="button"
              :disabled="hoursRefreshing"
              :aria-busy="hoursRefreshing"
              @click="handleHoursRefresh"
            >
              <svg
                ref="refreshIconRef"
                width="16"
                height="16"
                viewBox="0 0 16 16"
                fill="none"
                aria-hidden="true"
              >
                <path d="M14 8C14 11.3137 11.3137 14 8 14C4.68629 14 2 11.3137 2 8C2 4.68629 4.68629 2 8 2" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
                <path d="M14 2V6H10" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
              </svg>
              刷新
            </button>
          </div>
        </div>

        <div aria-live="polite">
          <BubbleFeedback
            v-if="refreshFeedback"
            class="hours-feedback"
            :class="`glass-feedback--${refreshFeedback.tone}`"
          >{{ refreshFeedback.text }}</BubbleFeedback>
        </div>

        <div class="hours-grid">
          <div v-for="(category, index) in hourCategories" :key="category.key" :class="['hour-category', `hour-category--type-${index}`]">
            <div class="hour-category__header">
              <h3 class="hour-category__title">{{ category.title }}</h3>
              <span
                class="hour-category__progress"
                :class="`hour-category__progress--${progressTone(category.percent)}`"
              >
                {{ category.percent }}%
              </span>
            </div>
            <div class="hour-category__values">
              <div class="hour-category__main">
                <span
                  class="hour-category__actual"
                  :class="`hour-category__actual--${progressTone(category.percent)}`"
                >
                  {{ formatHours(category.actual) }}
                </span>
                <span class="hour-category__unit">h</span>
              </div>
              <div class="hour-category__detail">
                <span class="hour-category__label">标准要求</span>
                <span class="hour-category__standard">{{ category.standard }}h</span>
              </div>
              <div class="hour-category__detail">
                <span class="hour-category__label">折算后</span>
                <span class="hour-category__converted">
                  {{ formatHours(category.converted) }}h
                  <span v-if="category.bonus > 0" class="hour-category__bonus">
                    +{{ formatCompactHours(category.bonus) }}
                  </span>
                </span>
              </div>
            </div>
            <div class="hour-category__bar">
              <div
                class="hour-category__fill"
                :class="`hour-category__fill--${progressTone(category.percent)}`"
                :style="{ transform: `scaleX(${category.barPercent / 100})` }"
              ></div>
            </div>
          </div>
        </div>

        <p class="hours-panel__note">
          超出标准的校外时长可折算为校内时长（每2小时折算1小时），超出标准的校内时长可折算为校外时长（每3小时折算1小时）
        </p>
      </section>

      <!-- Work Lists -->
      <section
        id="activities"
        class="work-lists"
        data-section="work"
        :class="{ visible: sectionsVisible.work }"
      >
        <!-- Pending activities -->
        <article v-if="showPending" class="work-list glass-panel">
          <h2 class="work-list__title">待审核活动</h2>
          <div v-if="workErrors.pending" role="alert">
            <BubbleFeedback class="glass-feedback--error">{{ workErrors.pending }}</BubbleFeedback>
            <button class="glass-button glass-button--compact" type="button" @click="handleRetry">重试</button>
          </div>
          <div v-else-if="pendingReviews.length" class="work-list__items">
            <RouterLink
              v-for="activity in pendingReviews"
              :key="activity._id"
              class="work-item"
              :to="pendingLink(activity._id)"
            >
              <div class="work-item__content">
                <p class="work-item__name">{{ activity.name }}</p>
                <p class="work-item__meta">{{ pendingMeta(activity) }}</p>
              </div>
              <span class="work-item__status work-item__status--pending">待审核</span>
            </RouterLink>
          </div>
          <BubbleFeedback v-else>暂无待审核活动</BubbleFeedback>
        </article>

        <article v-if="showAgent" class="work-list glass-panel">
          <h2 class="work-list__title">最近 Agent 结果</h2>
          <div v-if="workErrors.agent" role="alert">
            <BubbleFeedback class="glass-feedback--error">{{ workErrors.agent }}</BubbleFeedback>
            <button class="glass-button glass-button--compact" type="button" @click="handleRetry">重试</button>
          </div>
          <ul v-else-if="agentRuns.length" class="work-list__items work-list__entries">
            <li v-for="run in agentPreviews" :key="run.oid" class="work-item">
              <div class="work-item__content">
                <p class="work-item__name">{{ RUN_STATUSES[run.status] ?? run.status }}</p>
                <p class="work-item__meta">{{ [run.model, formatActivityDate(run.created_at ?? '')].filter(Boolean).join(' · ') }}</p>
                <p v-if="run.previewHtml" class="work-item__result" v-html="run.previewHtml"></p>
                <p v-else-if="run.fallback" class="work-item__result">{{ run.fallback }}</p>
              </div>
            </li>
          </ul>
          <BubbleFeedback v-else>暂无 Agent 运行记录</BubbleFeedback>
        </article>

        <article v-if="isAdmin" class="work-list glass-panel">
          <h2 class="work-list__title">可疑日志</h2>
          <div v-if="workErrors.logs" role="alert">
            <BubbleFeedback class="glass-feedback--error">{{ workErrors.logs }}</BubbleFeedback>
            <button class="glass-button glass-button--compact" type="button" @click="handleRetry">重试</button>
          </div>
          <ul v-else-if="suspiciousLogs.length" class="work-list__items work-list__entries">
            <li v-for="log in suspiciousLogs" :key="log._id" class="work-item">
              <div class="work-item__content">
                <p class="work-item__name">{{ LOG_TYPE_LABELS[log.type ?? ''] ?? '可疑事件' }}</p>
                <p class="work-item__meta">{{ [log.user_name || log.user_id, log.level].filter(Boolean).join(' · ') }}</p>
                <p v-if="log.data" class="work-item__result">{{ log.data }}</p>
              </div>
            </li>
          </ul>
          <BubbleFeedback v-else>暂无可疑日志</BubbleFeedback>
        </article>

        <article class="work-list glass-panel work-list--wide">
          <h2 class="work-list__title">最近记录</h2>
          <div v-if="recentError" role="alert">
            <BubbleFeedback class="glass-feedback--error">{{ recentError }}</BubbleFeedback>
            <button class="glass-button glass-button--compact" type="button" @click="handleRetry">重试</button>
          </div>
          <VolunteerStack
            v-else-if="recentActivities.length"
            v-model="expandedActivityId"
            :activities="recentActivities"
          />
          <BubbleFeedback v-else>暂无个人活动记录</BubbleFeedback>
        </article>
      </section>
    </template>
  </div>
</template>

<style scoped>
.home {
  min-height: 100vh;
  padding: var(--space-8) clamp(20px, 4vw, 40px) var(--space-10);
  max-width: 1200px;
  margin: 0 auto;
}

/* Greeting */
.greeting {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-4);
  padding: clamp(15px, 4vw, 32px);
  margin-bottom: var(--space-4);
  border: 1px solid var(--glass-border);
  border-radius: var(--radius-xl);
  background: linear-gradient(115deg, var(--palette-main) 0%, transparent 70%), var(--surface-matte);
  box-shadow: var(--glass-shadow);
  opacity: 0;
  transform: translateY(12px);
  transition:
    opacity var(--duration-slow) var(--ease-smooth),
    transform var(--duration-slow) var(--ease-smooth);
}

.greeting.visible {
  opacity: 1;
  transform: translateY(0);
}

.greeting__copy { min-width: 0; }

.greeting__eyebrow {
  margin-bottom: var(--space-1);
  color: var(--accent-ink);
  font-size: var(--font-size-callout);
  font-weight: 650;
}

.greeting__title {
  font-size: clamp(1.65rem, 4.4vw, 3.2rem);
  font-weight: 600;
  line-height: var(--line-height-tight);
  letter-spacing: -0.02em;
  color: var(--color-text-primary);
  overflow-wrap: anywhere;
  text-wrap: balance;
}

.hours-feedback { margin-bottom: var(--space-4); }

/* Apple-style "now" panel: simple translucent glass, no gradient/grain. */
.greeting__panel {
  position: relative;
  isolation: isolate;
  display: inline-flex;
  align-items: center;
  gap: var(--space-3);
  padding: 10px 18px 10px 10px;
  border-radius: var(--radius-pill);
  background: var(--glass-btn-bg);
  -webkit-backdrop-filter: var(--glass-btn-blur);
  backdrop-filter: var(--glass-btn-blur);
  border: 1px solid var(--glass-btn-border);
  box-shadow: var(--glass-btn-shadow),
    0 20px 48px rgb(51 79 108 / 0.12);
  overflow: hidden;
}

.greeting__panel::before {
  content: '';
  position: absolute;
  inset: 0;
  z-index: 0;
  background: rgb(255 255 255 / 0.1);
  pointer-events: none;
}

/* A single soft refraction band, kept inside the pill. */
.greeting__panel::after {
  content: '';
  position: absolute;
  inset: 1px;
  z-index: 0;
  border-radius: inherit;
  background: linear-gradient(
    115deg,
    transparent 16%,
    rgba(255, 255, 255, 0.42) 40%,
    rgba(255, 255, 255, 0.04) 52%,
    transparent 64%
  );
  opacity: 0.62;
  pointer-events: none;
}

.greeting__clock-orb {
  position: relative;
  isolation: isolate;
  z-index: 2;
  width: 76px;
  height: 76px;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 50%;
  background: var(--clock-glass-bg);
  -webkit-backdrop-filter: var(--clock-glass-blur);
  backdrop-filter: var(--clock-glass-blur);
  border: 1px solid var(--clock-glass-border);
  box-shadow: var(--clock-glass-shadow);
  overflow: hidden;
}

.greeting__clock-light {
  position: absolute;
  inset: -6px;
  z-index: 0;
  border-radius: 50%;
  background: rgb(255 255 255 / 0.16);
  border: 1px solid rgb(255 255 255 / 0.48);
  opacity: 0.62;
  pointer-events: none;
}

.greeting__clock {
  position: relative;
  z-index: 1;
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
}

.clock-face {
  width: 100%;
  height: 100%;
}

.clock-tick {
  stroke: var(--color-text-primary);
  stroke-width: 1.5;
  stroke-linecap: round;
  opacity: 0.6;
}

.clock-hand {
  stroke-linecap: round;
  transform-origin: 50px 50px;
  transition: transform 0.3s cubic-bezier(0.4, 0, 0.2, 1);
}

.clock-hand--hour {
  stroke: var(--color-text-primary);
  stroke-width: 3;
}

.clock-hand--minute {
  stroke: var(--color-text-primary);
  stroke-width: 2;
}

.clock-hand--second {
  stroke: var(--color-brand);
  stroke-width: 1.5;
}

.clock-center {
  fill: var(--color-brand);
}

@media (prefers-reduced-motion: reduce) {
  .clock-hand {
    transition: none;
  }
}

.greeting__info {
  position: relative;
  z-index: 1;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 3px;
  padding-right: var(--space-2);
}

.greeting__date {
  font-size: var(--font-size-callout);
  font-weight: 560;
  color: var(--color-text-secondary);
  margin: 0;
  line-height: 1.3;
}

.greeting__now-label {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  margin: 0;
  padding: 2px 8px;
  border-radius: var(--radius-pill);
  background: rgba(0, 122, 255, 0.09);
  color: var(--tone-blue);
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.greeting__now-label::before {
  content: '';
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: currentColor;
  box-shadow: 0 0 0 3px rgba(0, 122, 255, 0.12);
}

/* Identity Row */
.identity-row {
  display: grid;
  grid-template-columns: 1fr;
  gap: var(--space-5);
  margin-bottom: var(--space-8);
  opacity: 0;
  transform: translateY(20px);
  transition:
    opacity var(--duration-slow) var(--ease-smooth),
    transform var(--duration-slow) var(--ease-smooth);
  transition-delay: 100ms;
}

.identity-row.visible {
  opacity: 1;
  transform: translateY(0);
}

/* Identity Card - Frosted Glass */
.identity-card {
  position: relative;
  background: var(--surface-matte);
  border: 1px solid var(--glass-border);
  border-radius: var(--radius-lg);
  padding: var(--space-6);
  box-shadow: var(--glass-shadow);
  isolation: isolate;
  overflow: hidden;
}

.identity-card::before {
  content: '';
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 50%;
  background: var(--glass-highlight);
  border-radius: var(--radius-lg) var(--radius-lg) 0 0;
  pointer-events: none;
  opacity: 0.55;
  z-index: 0;
}

.identity-card::after {
  content: '';
  position: absolute;
  inset: 0;
  border-radius: inherit;
  background: linear-gradient(
    115deg,
    rgba(255, 255, 255, 0) 18%,
    rgba(255, 255, 255, 0.38) 38%,
    rgba(255, 255, 255, 0.04) 50%,
    rgba(255, 255, 255, 0) 64%
  );
  pointer-events: none;
  opacity: 0.55;
  z-index: 0;
}

.identity-card > * {
  position: relative;
  z-index: 1;
}

.identity-card__header {
  display: flex;
  align-items: center;
  gap: var(--space-4);
  margin-bottom: var(--space-5);
}

.identity-card__avatar {
  width: 64px;
  height: 64px;
  border-radius: 50%;
  background: var(--color-brand);
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.identity-card__avatar-text {
  font-size: 28px;
  font-weight: 500;
  color: white;
}

.identity-card__info {
  flex: 1;
}

.identity-card__name {
  font-size: var(--font-size-title-3);
  font-weight: 600;
  line-height: var(--line-height-snug);
  color: var(--color-text-primary);
  margin: 0 0 var(--space-1) 0;
}

.identity-card__number {
  font-size: var(--font-size-callout);
  color: var(--color-text-secondary);
  margin: 0;
}

.identity-card__meta {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  margin-bottom: var(--space-5);
}

.identity-card__meta-item {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  color: var(--color-text-secondary);
  font-size: var(--font-size-body);
}

.identity-card__meta-item svg {
  flex-shrink: 0;
  color: var(--color-text-tertiary);
}

.identity-card__action {
  align-self: flex-start;
  width: auto;
  min-width: 120px;
}

/* Total Hours - Frosted Glass */
.total-hours {
  position: relative;
  background: var(--surface-matte);
  background:
    radial-gradient(ellipse at 12% 0%, color-mix(in srgb, var(--palette-main) 54%, transparent), transparent 55%),
    radial-gradient(ellipse at 98% 100%, color-mix(in srgb, var(--palette-low) 58%, transparent), transparent 48%),
    var(--surface-matte);
  border: 1px solid var(--glass-border);
  border-radius: var(--radius-lg);
  padding: var(--space-6);
  text-align: center;
  display: flex;
  flex-direction: column;
  justify-content: center;
  box-shadow:
    var(--glass-shadow),
    0 0 24px rgba(59, 130, 246, 0.1);
  isolation: isolate;
  overflow: hidden;
}

.total-hours::before {
  content: '';
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 50%;
  background: var(--glass-highlight);
  border-radius: var(--radius-lg) var(--radius-lg) 0 0;
  pointer-events: none;
  opacity: 0.55;
  z-index: 0;
}

.total-hours::after {
  content: '';
  position: absolute;
  inset: 0;
  border-radius: inherit;
  background: linear-gradient(
    115deg,
    rgba(255, 255, 255, 0) 18%,
    rgba(255, 255, 255, 0.38) 38%,
    rgba(255, 255, 255, 0.04) 50%,
    rgba(255, 255, 255, 0) 64%
  );
  pointer-events: none;
  opacity: 0.55;
  z-index: 0;
}

.total-hours > * {
  position: relative;
  z-index: 1;
}

.total-hours__label {
  font-size: var(--font-size-callout);
  color: var(--color-text-tertiary);
  margin: 0 0 var(--space-3) 0;
}

.total-hours__value {
  font-size: var(--font-size-stats);
  font-weight: 600;
  line-height: var(--line-height-tight);
  letter-spacing: -0.02em;
  color: var(--color-text-primary);
  margin: 0 0 var(--space-3) 0;
}

.total-hours__unit {
  font-size: 0.5em;
  font-weight: 400;
  color: var(--color-text-secondary);
  margin-left: var(--space-1);
}

.total-hours__breakdown {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.breakdown-item {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  font-size: var(--font-size-caption);
}

.breakdown-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
}

.breakdown-dot--on-campus {
  background: var(--tone-blue);
}

.breakdown-dot--off-campus {
  background: var(--tone-teal);
}

.breakdown-dot--social-practice {
  background: var(--tone-amber);
}

.breakdown-label {
  color: var(--color-text-tertiary);
  font-weight: 500;
  min-width: 28px;
}

.breakdown-value {
  color: var(--color-text-secondary);
  font-weight: 600;
  margin-left: auto;
}

.total-hours__note {
  font-size: var(--font-size-caption);
  color: var(--color-text-tertiary);
  margin: 0;
}

/* Glass Panel Base */
.glass-panel {
  position: relative;
  background: var(--surface-matte);
  border: 1px solid var(--glass-border);
  border-radius: var(--radius-xl);
  box-shadow: var(--glass-shadow);
  isolation: isolate;
  padding: var(--space-6);
}

.glass-panel::before {
  content: '';
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 50%;
  background: var(--glass-highlight);
  border-radius: var(--radius-xl) var(--radius-xl) 0 0;
  pointer-events: none;
  opacity: 0.55;
  z-index: 0;
}

/* A single diagonal refraction band makes the glass legible on a light canvas. */
.glass-panel::after {
  content: '';
  position: absolute;
  inset: 0;
  border-radius: inherit;
  background: linear-gradient(
    115deg,
    rgba(255, 255, 255, 0) 18%,
    rgba(255, 255, 255, 0.38) 38%,
    rgba(255, 255, 255, 0.04) 50%,
    rgba(255, 255, 255, 0) 64%
  );
  pointer-events: none;
  opacity: 0.55;
  z-index: 0;
}

.glass-panel > * {
  position: relative;
  z-index: 1;
}

/* Glass Button */
.glass-button {
  position: relative;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: var(--space-2);
  padding: var(--space-3) var(--space-5);
  background: var(--glass-floating-bg);
  border: 1px solid var(--glass-border);
  border-radius: var(--radius-md);
  color: var(--accent-ink);
  font-size: var(--font-size-body);
  font-weight: 500;
  text-decoration: none;
  cursor: pointer;
  transition:
    transform var(--duration-fast) var(--ease-spring),
    box-shadow var(--duration-fast) var(--ease-smooth);
  overflow: hidden;
  isolation: isolate;
  min-height: 44px;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.98),
    inset 0 -1px 0 rgba(255, 255, 255, 0.38),
    0 3px 10px rgba(18, 48, 82, 0.08);
}

.glass-button::before {
  content: '';
  position: absolute;
  inset: 0;
  background: var(--glass-highlight);
  border-radius: var(--radius-md);
  pointer-events: none;
  opacity: 0.3;
}

.glass-button::after {
  content: '';
  position: absolute;
  inset: 0;
  background: var(--glass-sheen);
  border-radius: var(--radius-md);
  opacity: 0.22;
  transform: translateX(-38%);
  transition:
    opacity var(--duration-base) var(--ease-smooth),
    transform var(--duration-base) var(--ease-smooth);
  pointer-events: none;
}

.glass-button:hover {
  transform: scale(1.01);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.96),
    inset 0 -1px 0 rgba(255, 255, 255, 0.36),
    0 6px 18px rgba(18, 48, 82, 0.09);
}

.glass-button:hover::after {
  opacity: 1;
  transform: translateX(100%);
}

.glass-button:active {
  transform: scale(0.98);
}

.glass-button:focus-visible {
  outline: 2px solid var(--color-brand);
  outline-offset: 3px;
}

.glass-button:disabled {
  opacity: 0.6;
  cursor: default;
  transform: none;
}

.glass-button--compact {
  padding: var(--space-2) var(--space-4);
  font-size: var(--font-size-callout);
  min-height: 36px;
}

.glass-button--compact svg {
  width: 14px;
  height: 14px;
}

/* Hours Panel */
.hours-panel {
  margin-bottom: var(--space-8);
  opacity: 0;
  transform: translateY(20px);
  transition:
    opacity var(--duration-slow) var(--ease-smooth),
    transform var(--duration-slow) var(--ease-smooth);
  transition-delay: 200ms;
}

.hours-panel.visible {
  opacity: 1;
  transform: translateY(0);
}

.hours-panel__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-4);
  margin-bottom: var(--space-6);
  position: relative;
  z-index: 1;
}

.hours-panel__title {
  font-size: var(--font-size-headline);
  font-weight: 600;
  color: var(--color-text-primary);
  margin: 0;
}

.hours-panel__actions {
  display: flex;
  gap: var(--space-2);
}

.hours-grid {
  display: grid;
  grid-template-columns: 1fr;
  gap: var(--space-5);
  margin-bottom: var(--space-5);
  position: relative;
  z-index: 1;
}

.hour-category {
  position: relative;
  padding: var(--space-5);
  background: var(--surface-matte);
  border: 1px solid rgba(255, 255, 255, 0.65);
  border-radius: var(--radius-lg);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.8),
    0 2px 8px rgba(18, 48, 82, 0.05);
  isolation: isolate;
  overflow: hidden;
}

.hour-category::before {
  content: '';
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 40%;
  background: linear-gradient(
    180deg,
    rgba(255, 255, 255, 0.7) 0%,
    rgba(255, 255, 255, 0) 100%
  );
  pointer-events: none;
  z-index: 0;
}

.hour-category > * {
  position: relative;
  z-index: 1;
}

/* Type-specific color accents */
.hour-category--type-0 {
  --hour-accent: 0, 122, 255;
  border-color: rgba(0, 122, 255, 0.2);
}

.hour-category--type-1 {
  --hour-accent: 13, 148, 136;
  border-color: rgba(20, 184, 166, 0.2);
}

.hour-category--type-2 {
  --hour-accent: 245, 158, 11;
  border-color: rgba(245, 158, 11, 0.22);
}

.hour-category--type-0,
.hour-category--type-1,
.hour-category--type-2 {
  box-shadow:
    inset 3px 0 0 rgba(var(--hour-accent), 0.52),
    inset 0 1px 0 rgba(255, 255, 255, 0.8),
    0 3px 12px rgba(18, 48, 82, 0.06);
}

.hour-category__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: var(--space-4);
}

.hour-category__title {
  font-size: var(--font-size-body-large);
  font-weight: 600;
  color: var(--color-text-primary);
  margin: 0;
}

.hour-category__progress {
  font-size: var(--font-size-caption);
  font-weight: 600;
  padding: 4px var(--space-2);
  border-radius: var(--radius-xs);
}

.hour-category__progress--green {
  background: var(--tone-green-bg);
  color: var(--tone-green);
}

.hour-category__progress--blue {
  background: var(--tone-blue-bg);
  color: var(--tone-blue);
}

.hour-category__progress--amber {
  background: var(--tone-amber-bg);
  color: var(--tone-amber);
}

.hour-category__progress--rose {
  background: var(--tone-rose-bg);
  color: var(--tone-rose);
}

.hour-category__progress--good {
  background: rgba(52, 199, 89, 0.15);
  color: #34c759;
}

.hour-category__progress--warn {
  background: rgba(255, 159, 10, 0.15);
  color: #ff9f0a;
}

.hour-category__values {
  margin-bottom: var(--space-4);
}

.hour-category__main {
  display: flex;
  align-items: baseline;
  gap: var(--space-1);
  margin-bottom: var(--space-3);
}

.hour-category__actual {
  font-size: clamp(40px, 5vw, 48px);
  font-weight: 600;
  line-height: 1;
  letter-spacing: -0.02em;
}

.hour-category__actual--green {
  color: var(--tone-green);
}

.hour-category__actual--blue {
  color: var(--tone-blue);
}

.hour-category__actual--amber {
  color: var(--tone-amber);
}

.hour-category__actual--rose {
  color: var(--tone-rose);
}

.hour-category__unit {
  font-size: var(--font-size-body);
  font-weight: 400;
  color: var(--color-text-secondary);
}

.hour-category__detail {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: var(--font-size-callout);
  margin-bottom: var(--space-1);
}

.hour-category__label {
  color: var(--color-text-tertiary);
}

.hour-category__standard,
.hour-category__converted {
  color: var(--color-text-secondary);
  font-weight: 500;
}

.hour-category__bonus {
  color: #34c759;
  font-weight: 600;
  margin-left: var(--space-1);
}

.hour-category__bar {
  height: 6px;
  background: rgba(0, 0, 0, 0.08);
  border-radius: var(--radius-pill);
  overflow: hidden;
}

.hour-category__fill {
  height: 100%;
  width: 100%;
  border-radius: var(--radius-pill);
  transform-origin: left center;
  /* 比例由 :style 以 scaleX 绑定，刷新时用 WAAPI 从 0 生长，不触发布局。 */
}

.hour-category__fill--green {
  background: var(--tone-green);
}

.hour-category__fill--blue {
  background: var(--tone-blue);
}

.hour-category__fill--amber {
  background: var(--tone-amber);
}

.hour-category__fill--rose {
  background: var(--tone-rose);
}

.hours-panel__note {
  font-size: var(--font-size-caption);
  color: var(--color-text-tertiary);
  margin: 0;
  line-height: var(--line-height-relaxed);
  position: relative;
  z-index: 1;
}

/* Work Lists */
.work-lists {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: var(--space-5);
  margin-bottom: var(--space-10);
  opacity: 0;
  transform: translateY(20px);
  transition:
    opacity var(--duration-slow) var(--ease-smooth),
    transform var(--duration-slow) var(--ease-smooth);
  transition-delay: 300ms;
}

.work-lists.visible {
  opacity: 1;
  transform: translateY(0);
}

.work-list__title {
  font-size: var(--font-size-headline);
  font-weight: 600;
  color: var(--color-text-primary);
  margin: 0 0 var(--space-5) 0;
  position: relative;
  z-index: 1;
}

.work-list__items {
  display: flex;
  flex-direction: column;
  position: relative;
  z-index: 1;
}

.work-list--wide { grid-column: 1 / -1; }
.work-list__entries { list-style: none; margin: 0; padding: 0; }
.work-item__result {
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
  overflow: hidden;
  overflow-wrap: anywhere;
  white-space: normal;
  line-height: 1.5;
  max-block-size: 3em;
}

.work-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-4);
  padding: var(--space-4);
  border-bottom: 1px solid rgba(0, 0, 0, 0.06);
  text-decoration: none;
  transition:
    background-color var(--duration-fast) var(--ease-smooth),
    transform var(--duration-fast) var(--ease-smooth);
  min-height: 68px;
}

.work-item:last-child {
  border-bottom: none;
}

.work-item:hover {
  background: rgba(255, 255, 255, 0.4);
  transform: translateX(2px);
}

.work-item:active {
  transform: scale(0.99);
}

.work-item:focus-visible {
  outline: 2px solid var(--color-brand);
  outline-offset: -2px;
  border-radius: var(--radius-sm);
}

.work-item__content {
  flex: 1;
  min-width: 0;
}

.work-item__name {
  font-size: var(--font-size-body);
  font-weight: 500;
  color: var(--color-text-primary);
  margin: 0 0 var(--space-1) 0;
  overflow-wrap: anywhere;
}

.work-item__meta {
  overflow: hidden;
  font-size: var(--font-size-callout);
  color: var(--color-text-secondary);
  margin: 0;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.work-item__status {
  font-size: var(--font-size-caption);
  font-weight: 500;
  padding: 4px var(--space-3);
  border-radius: var(--radius-pill);
  white-space: nowrap;
  flex-shrink: 0;
}

.work-item__status--pending {
  background: rgba(255, 159, 10, 0.15);
  color: #ff9f0a;
}

/* Login / error state */
.state-panel {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-4);
  padding: var(--space-8) var(--space-6);
  margin-bottom: var(--space-8);
  text-align: center;
  animation: surface-enter-keyframes var(--duration-base) var(--ease-settle) backwards;
}

.state-panel__title {
  font-size: var(--font-size-headline);
  font-weight: 600;
  color: var(--color-text-primary);
  margin: 0;
}

.state-panel > .glass-feedback {
  max-width: 44ch;
}

.state-panel__action {
  min-width: 132px;
  margin-top: var(--space-2);
}

/* Skeleton loading */
.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0 0 0 0);
  white-space: nowrap;
  border: 0;
}

.greeting__skeleton,
.skeleton-block {
  background: linear-gradient(
    100deg,
    rgba(255, 255, 255, 0.42) 18%,
    rgba(255, 255, 255, 0.92) 42%,
    rgba(255, 255, 255, 0.42) 66%
  );
  background-size: 220% 100%;
  border-radius: var(--radius-xs);
  animation: skeleton-sweep 1.5s var(--ease-smooth) infinite;
}

.greeting__skeleton {
  display: inline-block;
  vertical-align: middle;
  width: clamp(96px, 12vw, 168px);
  height: 0.56em;
  border-radius: var(--radius-sm);
}

@keyframes skeleton-sweep {
  from {
    background-position: 130% 0;
  }
  to {
    background-position: -70% 0;
  }
}

.skeleton-line {
  height: 14px;
}

.skeleton-line--title {
  width: 42%;
  height: 26px;
  margin-bottom: var(--space-2);
}

.skeleton-line--caption {
  width: 28%;
  height: 12px;
}

.skeleton-line--wide {
  width: 62%;
}

.skeleton-line--medium {
  width: 44%;
}

.skeleton-line--heading {
  width: 120px;
  height: 22px;
}

.skeleton-line--category {
  width: 88px;
  height: 19px;
}

.skeleton-line--stat {
  width: 96px;
  height: 48px;
}

.skeleton-avatar {
  width: 64px;
  height: 64px;
  flex-shrink: 0;
  border-radius: 50%;
}

.skeleton-button {
  width: 128px;
  height: 44px;
  border-radius: var(--radius-md);
}

.skeleton-button--compact {
  width: 84px;
  height: 36px;
}

.skeleton-badge {
  width: 58px;
  height: 24px;
  border-radius: var(--radius-pill);
}

.skeleton-bar {
  height: 6px;
  margin-top: var(--space-4);
  border-radius: var(--radius-pill);
}

.skeleton-stack {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.skeleton-stack--center {
  align-items: center;
}

.skeleton-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-4);
  min-height: 68px;
  padding: var(--space-4);
  border-bottom: 1px solid rgba(0, 0, 0, 0.06);
}

.skeleton-row:last-child {
  border-bottom: none;
}

.skeleton-row__content {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.skeleton-row__name {
  width: 56%;
}

.skeleton-row__meta {
  width: 38%;
  height: 12px;
}

.skeleton-heading-gap {
  margin-bottom: var(--space-5);
}

@media (max-width: 767px) {
  .work-list {
    padding: var(--space-4);
  }

  .work-item {
    gap: var(--space-2);
    padding-inline: var(--space-2);
  }
}

/* Tablet */
@media (min-width: 768px) {
  .identity-row {
    grid-template-columns: 7fr 5fr;
  }

  .hours-grid {
    grid-template-columns: repeat(2, 1fr);
  }

  .work-lists {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

/* Desktop */
@media (min-width: 1024px) {
  .home {
    padding-top: var(--space-10);
  }


  .identity-row {
    margin-bottom: var(--space-10);
  }

  .hours-panel {
    margin-bottom: var(--space-10);
  }

  .hours-grid {
    grid-template-columns: repeat(3, 1fr);
  }

  .work-lists {
    grid-template-columns: minmax(0, 7fr) minmax(0, 5fr);
  }
}

/* Reduced motion */
@media (prefers-reduced-motion: reduce) {
  .work-item,
  .work-item:hover,
  .work-item:active {
    transition: none;
    transform: none;
  }

  .greeting,
  .identity-row,
  .hours-panel,
  .work-lists {
    opacity: 1;
    transform: none;
  }

  .glass-button::after {
    display: none;
  }

  .skeleton-block,
  .greeting__skeleton,
  .state-panel {
    animation: none;
  }

}
</style>
