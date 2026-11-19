<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import {
  CanaryApiError,
  fetchActivities,
  fetchProfile,
  isLoggedIn
} from '@/api/canary'
import type { ActivityScope, CanaryActivity } from '@/api/canary'

type ActivityStatus = 'draft' | 'pending' | 'refused' | 'effective'
type ActivityType = CanaryActivity['type']

interface Activity {
  id: string
  name: string
  date: string
  place: string
  type: string
  status: ActivityStatus
  duration: number
  creator: string
}

const router = useRouter()

const TYPE_LABELS: Record<ActivityType, string> = {
  'on-campus': '校内活动',
  'off-campus': '校外活动',
  'social-practice': '社会实践',
  hybrid: '混合活动'
}

const pageSize = 12

const activeTab = ref<ActivityScope>('mine')
const searchWord = ref('')
const appliedSearch = ref('')
const activePage = ref(1)
const loading = ref(true)
const activities = ref<Activity[]>([])
const totalActivities = ref(0)
const selectedActivity = ref<Activity | null>(null)
const error = ref('')
const unauthenticated = ref(false)
const classId = ref('')

let requestSeq = 0

const totalPages = computed(() => Math.ceil(totalActivities.value / pageSize))
const needsClass = computed(() => activeTab.value === 'class' && !classId.value)

function readableError(reason: unknown, fallback: string): string {
  return reason instanceof CanaryApiError ? reason.message : fallback
}

function isAuthError(reason: unknown): boolean {
  if (reason instanceof CanaryApiError && reason.status === 401) {
    unauthenticated.value = true
    activities.value = []
    totalActivities.value = 0
    return true
  }
  return false
}

function normalizeStatus(value: string | undefined): ActivityStatus {
  if (value === 'draft' || value === 'refused' || value === 'effective') {
    return value
  }
  return 'pending'
}

function mapActivity(activity: CanaryActivity): Activity {
  const mine = activity.mine
  return {
    id: activity._id,
    name: activity.name || '未命名活动',
    date: activity.date || '',
    place: activity.place || '',
    type: TYPE_LABELS[activity.type] ?? activity.type ?? '活动',
    status: normalizeStatus(mine?.status ?? activity.status),
    duration: typeof mine?.duration === 'number' ? mine.duration : 0,
    creator: activity.creator_name || ''
  }
}

async function loadProfile(): Promise<boolean> {
  try {
    const profile = await fetchProfile()
    classId.value = profile.class_id ?? ''
    return true
  } catch (reason) {
    if (isAuthError(reason)) return false
    error.value = readableError(reason, '个人资料加载失败，请稍后重试。')
    return false
  }
}

async function loadActivities() {
  if (needsClass.value) {
    requestSeq += 1
    loading.value = false
    error.value = ''
    activities.value = []
    totalActivities.value = 0
    selectedActivity.value = null
    return
  }

  const seq = ++requestSeq
  loading.value = true
  error.value = ''
  try {
    const result = await fetchActivities(
      activeTab.value,
      activePage.value,
      pageSize,
      appliedSearch.value,
      classId.value
    )
    if (seq !== requestSeq) return
    activities.value = (result.activities ?? []).map(mapActivity)
    totalActivities.value = result.total ?? activities.value.length
    const pageCount = Math.ceil(totalActivities.value / pageSize)
    if (pageCount > 0 && activePage.value > pageCount) {
      activePage.value = pageCount
      void loadActivities()
      return
    }
    if (
      selectedActivity.value &&
      !activities.value.some((activity) => activity.id === selectedActivity.value?.id)
    ) {
      selectedActivity.value = null
    }
  } catch (reason) {
    if (seq !== requestSeq) return
    if (!isAuthError(reason)) {
      activities.value = []
      totalActivities.value = 0
      error.value = readableError(reason, '活动加载失败，请稍后重试。')
    }
  } finally {
    if (seq === requestSeq) loading.value = false
  }
}

async function bootstrap() {
  if (!isLoggedIn()) {
    unauthenticated.value = true
    loading.value = false
    return
  }

  requestSeq += 1
  unauthenticated.value = false
  loading.value = true
  error.value = ''
  const profileOk = await loadProfile()
  if (!profileOk) {
    loading.value = false
    return
  }
  await loadActivities()
}

function handleSearch() {
  const next = searchWord.value.trim()
  if (next === appliedSearch.value) return
  appliedSearch.value = next
  activePage.value = 1
  void loadActivities()
}

function handleSearchBlur(event: FocusEvent) {
  const next = event.relatedTarget
  if (next instanceof HTMLElement && next.closest('.search-clear')) return
  handleSearch()
}

function handleRefresh() {
  if (loading.value) return
  void bootstrap()
}

function handleTabChange(tab: ActivityScope) {
  if (activeTab.value === tab) return
  activeTab.value = tab
  activePage.value = 1
  selectedActivity.value = null
  void loadActivities()
}

function handlePageChange(page: number) {
  if (page === activePage.value || page < 1) return
  if (totalPages.value > 0 && page > totalPages.value) return
  activePage.value = page
  selectedActivity.value = null
  void loadActivities()
  window.scrollTo({ top: 0, behavior: 'smooth' })
}

function handleRetry() {
  if (loading.value) return
  void bootstrap()
}

function formatDate(date: string) {
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(date)
  const parsed = match
    ? new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]))
    : new Date(date)
  if (!date || Number.isNaN(parsed.getTime())) {
    return { day: '--', month: '--', weekday: '--' }
  }
  return {
    day: String(parsed.getDate()).padStart(2, '0'),
    month: `${parsed.getMonth() + 1}月`,
    weekday: ['周日', '周一', '周二', '周三', '周四', '周五', '周六'][parsed.getDay()]
  }
}

function formatDetailDate(date: string) {
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(date)
  if (match) return `${match[1]}-${match[2]}-${match[3]}`
  return date || '—'
}

function getStatusLabel(status: ActivityStatus) {
  const labels: Record<ActivityStatus, string> = {
    draft: '草稿',
    pending: '待审核',
    refused: '已拒绝',
    effective: '已生效'
  }
  return labels[status]
}

function getStatusClass(status: ActivityStatus) {
  const classes: Record<ActivityStatus, string> = {
    draft: 'status-draft',
    pending: 'status-pending',
    refused: 'status-refused',
    effective: 'status-effective'
  }
  return classes[status]
}

function openActivity(activity: Activity) {
  selectedActivity.value = activity
}

function closeActivity() {
  selectedActivity.value = null
}

onMounted(() => {
  void bootstrap()
})
</script>

<template>
  <div class="volunteer-page">
    <!-- Page header with frosted glass -->
    <header class="page-header glass-card">
      <div class="page-header__lead">
        <button class="back-btn" type="button" aria-label="返回首页" @click="router.push('/')">
          <svg width="18" height="18" viewBox="0 0 20 20" fill="none" aria-hidden="true">
            <path d="M12 4L6 10L12 16" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
          </svg>
        </button>
        <div class="page-header__content">
          <p class="page-header__eyebrow">志愿服务</p>
          <h1 class="page-header__title">义工列表</h1>
          <p class="page-header__subtitle">共 {{ totalActivities }} 个项目</p>
        </div>
      </div>
      <div class="page-header__actions">
        <button
          class="liquid-btn liquid-btn--secondary liquid-btn--sm"
          type="button"
          :disabled="loading"
          @click="handleRefresh"
        >
          刷新
        </button>
      </div>
    </header>

    <!-- Scope tabs with frosted glass -->
    <nav class="scope-tabs glass-card" role="tablist" aria-label="活动范围">
      <button
        class="scope-tab"
        :class="{ 'is-active': activeTab === 'mine' }"
        role="tab"
        :aria-selected="activeTab === 'mine'"
        @click="handleTabChange('mine')"
      >
        我的活动
      </button>
      <button
        class="scope-tab"
        :class="{ 'is-active': activeTab === 'class' }"
        role="tab"
        :aria-selected="activeTab === 'class'"
        @click="handleTabChange('class')"
      >
        班级活动
      </button>
      <button
        class="scope-tab"
        :class="{ 'is-active': activeTab === 'campus' }"
        role="tab"
        :aria-selected="activeTab === 'campus'"
        @click="handleTabChange('campus')"
      >
        校园活动
      </button>
      <span class="scope-indicator" aria-hidden="true" />
    </nav>

    <!-- Search bar with frosted glass -->
    <div class="search-bar glass-card">
      <svg class="search-icon" width="20" height="20" viewBox="0 0 20 20" fill="none" aria-hidden="true">
        <circle cx="9" cy="9" r="6" stroke="currentColor" stroke-width="1.5"/>
        <path d="M13.5 13.5L17 17" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
      </svg>
      <input
        v-model="searchWord"
        class="search-input"
        type="search"
        placeholder="搜索项目名称或地点"
        @keydown.enter="handleSearch"
        @blur="handleSearchBlur"
      />
      <button
        v-if="searchWord"
        class="search-clear"
        type="button"
        aria-label="清除搜索"
        @click="searchWord = ''; handleSearch()"
      >
        <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
          <path d="M4 4L12 12M12 4L4 12" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
        </svg>
      </button>
    </div>

    <!-- Activities list with frosted glass cards -->
    <div v-if="unauthenticated" class="empty-state glass-card" role="alert">
      <p class="empty-title">需要登录</p>
      <p class="empty-description">登录后即可查看你的义工记录。</p>
      <RouterLink
        class="liquid-btn liquid-btn--primary liquid-btn--sm"
        to="/login?redirect=/volunteers"
      >
        去登录
      </RouterLink>
    </div>

    <div v-else-if="error" class="empty-state glass-card" role="alert">
      <p class="empty-title">活动加载失败</p>
      <p class="empty-description">{{ error }}</p>
      <button
        class="liquid-btn liquid-btn--secondary liquid-btn--sm"
        type="button"
        :disabled="loading"
        @click="handleRetry"
      >
        重试
      </button>
    </div>

    <div v-else-if="needsClass" class="empty-state glass-card" role="status">
      <svg class="empty-icon" width="64" height="64" viewBox="0 0 64 64" fill="none" aria-hidden="true">
        <circle cx="32" cy="32" r="30" stroke="currentColor" stroke-width="2" opacity="0.3"/>
        <path d="M32 20V34M32 42V44" stroke="currentColor" stroke-width="3" stroke-linecap="round"/>
      </svg>
      <p class="empty-title">尚未绑定班级</p>
      <p class="empty-description">
        绑定班级后即可查看班级活动；你也可以先浏览“我的活动”或“校园活动”。
      </p>
    </div>

    <div
      v-else-if="loading"
      class="activities-skeleton"
      role="status"
      aria-live="polite"
      aria-label="正在加载活动"
    >
      <div v-for="n in 6" :key="n" class="activity-skeleton glass-card">
        <div class="skeleton-date"></div>
        <div class="skeleton-content">
          <div class="skeleton-line skeleton-line--sm"></div>
          <div class="skeleton-line skeleton-line--lg"></div>
          <div class="skeleton-line skeleton-line--xs"></div>
        </div>
      </div>
    </div>

    <div v-else-if="activities.length === 0" class="empty-state glass-card">
      <svg class="empty-icon" width="64" height="64" viewBox="0 0 64 64" fill="none" aria-hidden="true">
        <circle cx="32" cy="32" r="30" stroke="currentColor" stroke-width="2" opacity="0.3"/>
        <path d="M32 20V34M32 42V44" stroke="currentColor" stroke-width="3" stroke-linecap="round"/>
      </svg>
      <p class="empty-title">没有找到相关项目</p>
      <p class="empty-description">
        {{ appliedSearch ? '尝试使用其他关键词搜索' : '暂时没有活动记录' }}
      </p>
    </div>

    <div v-else class="activities-list">
      <button
        v-for="activity in activities"
        :key="activity.id"
        class="activity-card glass-card"
        type="button"
        @click="openActivity(activity)"
      >
        <div class="activity-date">
          <span class="activity-day">{{ formatDate(activity.date).day }}</span>
          <span class="activity-month">{{ formatDate(activity.date).month }}</span>
          <span class="activity-weekday">{{ formatDate(activity.date).weekday }}</span>
        </div>

        <div class="activity-content">
          <div class="activity-kicker">
            <span class="activity-type">{{ activity.type }}</span>
            <span v-if="activity.place" class="activity-place">{{ activity.place }}</span>
          </div>
          <h3 class="activity-name">{{ activity.name }}</h3>
          <div class="activity-meta">
            <span class="activity-status" :class="getStatusClass(activity.status)">
              {{ getStatusLabel(activity.status) }}
            </span>
            <span class="activity-duration">{{ activity.duration }}h</span>
          </div>
        </div>
      </button>
    </div>

    <Transition name="activity-detail">
      <section v-if="selectedActivity" class="glass-card activity-detail" aria-live="polite">
        <header class="activity-detail__head">
          <div>
            <p class="activity-detail__eyebrow">活动详情</p>
            <h2 class="activity-detail__title">{{ selectedActivity.name }}</h2>
          </div>
          <button
            class="liquid-btn liquid-btn--secondary liquid-btn--sm"
            type="button"
            @click="closeActivity"
          >
            关闭
          </button>
        </header>

        <dl class="activity-detail__kv">
          <dt>日期</dt>
          <dd>{{ formatDetailDate(selectedActivity.date) }}</dd>
          <dt>地点</dt>
          <dd>{{ selectedActivity.place || '—' }}</dd>
          <dt>类型</dt>
          <dd>{{ selectedActivity.type }}</dd>
          <template v-if="selectedActivity.creator">
            <dt>创建者</dt>
            <dd>{{ selectedActivity.creator }}</dd>
          </template>
          <dt>状态</dt>
          <dd>
            <span class="activity-status" :class="getStatusClass(selectedActivity.status)">
              {{ getStatusLabel(selectedActivity.status) }}
            </span>
          </dd>
          <dt>时长</dt>
          <dd>{{ selectedActivity.duration }}h</dd>
        </dl>
      </section>
    </Transition>

    <!-- Pagination with liquid glass buttons -->
    <nav
      v-if="totalPages > 1 && !loading && !error && !unauthenticated && !needsClass"
      class="pagination glass-card"
      aria-label="分页导航"
    >
      <button
        class="liquid-btn liquid-btn--secondary liquid-btn--sm"
        type="button"
        :disabled="activePage === 1"
        @click="handlePageChange(activePage - 1)"
      >
        上一页
      </button>
      <div class="pagination-pages">
        <button
          v-for="page in totalPages"
          :key="page"
          class="pagination-page"
          :class="{ 'is-active': page === activePage }"
          type="button"
          :aria-current="page === activePage ? 'page' : undefined"
          @click="handlePageChange(page)"
        >
          {{ page }}
        </button>
      </div>
      <button
        class="liquid-btn liquid-btn--secondary liquid-btn--sm"
        type="button"
        :disabled="activePage === totalPages"
        @click="handlePageChange(activePage + 1)"
      >
        下一页
      </button>
    </nav>
  </div>
</template>

<style scoped>
.volunteer-page {
  min-height: 100vh;
  padding: var(--space-6) var(--content-padding) var(--space-10);
  max-width: var(--content-max-width);
  margin: 0 auto;
  display: grid;
  gap: var(--space-5);
}

/* Frosted glass card base */
.glass-card {
  position: relative;
  background: var(--glass-bg);
  -webkit-backdrop-filter: var(--glass-blur);
  backdrop-filter: var(--glass-blur);
  border: 1px solid var(--glass-border);
  border-radius: var(--radius-xl);
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

/* Page header */
.page-header {
  padding: var(--space-6);
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-5);
  flex-wrap: wrap;
}

.page-header__lead {
  display: flex;
  align-items: flex-start;
  gap: var(--space-4);
  flex: 1;
  min-width: 200px;
}

.page-header__content {
  flex: 1;
  min-width: 200px;
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
    rgba(255, 255, 255, 0.48) 0%,
    rgba(255, 255, 255, 0.28) 100%
  );
  -webkit-backdrop-filter: blur(10px) saturate(170%);
  backdrop-filter: blur(10px) saturate(170%);
  border: 1px solid rgba(255, 255, 255, 0.72);
  border-radius: 50%;
  box-shadow:
    inset 0 0.5px 0 rgba(255, 255, 255, 0.85),
    inset 0 -0.5px 0 rgba(0, 0, 0, 0.08),
    0 0 8px rgba(18, 48, 82, 0.06);
  color: var(--color-text-secondary);
  cursor: pointer;
  overflow: hidden;
  isolation: isolate;
  flex-shrink: 0;
  transition: all var(--duration-fast) var(--ease-spring);
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
  transition: all var(--duration-base) var(--ease-smooth);
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

.page-header__eyebrow {
  font-size: var(--font-size-callout);
  color: var(--color-text-tertiary);
  font-weight: 500;
  letter-spacing: var(--letter-spacing-wide);
  text-transform: uppercase;
  margin: 0 0 var(--space-1);
}

.page-header__title {
  font-size: var(--font-size-title-3);
  font-weight: 700;
  color: var(--color-text-primary);
  letter-spacing: var(--letter-spacing-tight);
  line-height: var(--line-height-tight);
  margin: 0 0 var(--space-2);
}

.page-header__subtitle {
  font-size: var(--font-size-callout);
  color: var(--color-text-secondary);
  margin: 0;
}

.page-header__actions {
  display: flex;
  gap: var(--space-3);
  align-items: center;
}

/* Scope tabs */
.scope-tabs {
  padding: 6px;
  display: flex;
  gap: 4px;
  position: relative;
}

.scope-tab {
  flex: 1;
  padding: var(--space-3) var(--space-4);
  background: linear-gradient(
    135deg,
    rgba(255, 255, 255, 0.34) 0%,
    rgba(255, 255, 255, 0.18) 100%
  );
  -webkit-backdrop-filter: blur(10px) saturate(160%);
  backdrop-filter: blur(10px) saturate(160%);
  border: 1px solid rgba(255, 255, 255, 0.48);
  border-radius: var(--radius-pill);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.72);
  font-size: var(--font-size-callout);
  font-weight: 600;
  color: var(--color-text-secondary);
  cursor: pointer;
  transition:
    color var(--duration-fast) var(--ease-smooth),
    background var(--duration-fast) var(--ease-smooth),
    border-color var(--duration-fast) var(--ease-smooth);
  position: relative;
  z-index: 1;
  min-height: 40px;
}

.scope-tab:hover {
  color: var(--color-text-primary);
}

.scope-tab.is-active {
  background: transparent;
  border-color: transparent;
  box-shadow: none;
  color: var(--color-brand);
}

.scope-tab:focus-visible {
  outline: 2px solid var(--color-brand);
  outline-offset: 2px;
}

.scope-tabs > .scope-indicator {
  z-index: 0;
}

.scope-indicator {
  position: absolute;
  left: 6px;
  top: 6px;
  height: calc(100% - 12px);
  width: calc(33.333% - 8px);
  background: linear-gradient(135deg, rgba(0, 113, 227, 0.15) 0%, rgba(0, 113, 227, 0.08) 100%);
  border-radius: var(--radius-pill);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.55), 0 2px 8px rgba(14, 165, 233, 0.25);
  pointer-events: none;
  transition: transform var(--duration-base) var(--ease-spring);
}

.scope-tab:nth-child(2).is-active ~ .scope-indicator {
  transform: translateX(calc(100% + 4px));
}

.scope-tab:nth-child(3).is-active ~ .scope-indicator {
  transform: translateX(calc(200% + 8px));
}

/* Search bar */
.search-bar {
  padding: var(--space-4);
  display: flex;
  align-items: center;
  gap: var(--space-3);
}

.search-icon {
  color: var(--color-text-tertiary);
  flex-shrink: 0;
}

.search-input {
  flex: 1;
  background: transparent;
  border: none;
  font-size: var(--font-size-body);
  color: var(--color-text-primary);
  outline: none;
}

.search-input::placeholder {
  color: var(--color-text-tertiary);
}

.search-clear {
  width: 28px;
  height: 28px;
  padding: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(
    135deg,
    rgba(255, 255, 255, 0.48) 0%,
    rgba(255, 255, 255, 0.24) 100%
  );
  -webkit-backdrop-filter: blur(8px) saturate(160%);
  backdrop-filter: blur(8px) saturate(160%);
  border: 1px solid rgba(255, 255, 255, 0.58);
  border-radius: 50%;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.8);
  color: var(--color-text-secondary);
  cursor: pointer;
  transition: all var(--duration-fast) var(--ease-smooth);
  flex-shrink: 0;
}

.search-clear:hover {
  background: rgba(0, 0, 0, 0.12);
  color: var(--color-text-primary);
}

.search-clear:active {
  transform: scale(0.9);
}

/* Activities list */
.activities-list {
  display: grid;
  gap: var(--space-4);
}

.activity-card {
  padding: var(--space-5);
  display: flex;
  gap: var(--space-5);
  background: var(--glass-bg);
  border: 1px solid var(--glass-border);
  cursor: pointer;
  transition: all var(--duration-fast) var(--ease-spring);
  text-align: left;
}

.activity-card:hover {
  transform: translateY(-2px);
  box-shadow:
    var(--glass-shadow),
    0 8px 24px rgba(0, 113, 227, 0.12);
}

.activity-card:active {
  transform: translateY(0);
}

.activity-card:focus-visible {
  outline: 2px solid var(--color-brand);
  outline-offset: 2px;
}

.activity-date {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 2px;
  min-width: 64px;
  padding: var(--space-3);
  background: linear-gradient(135deg, rgba(255, 255, 255, 0.25) 0%, rgba(255, 255, 255, 0.15) 100%);
  border: 1px solid rgba(255, 255, 255, 0.3);
  border-radius: var(--radius-md);
  -webkit-backdrop-filter: blur(12px) saturate(170%) brightness(1.04);
  backdrop-filter: blur(12px) saturate(170%) brightness(1.04);
  flex-shrink: 0;
  position: relative;
  box-shadow: inset 0 1px 2px rgba(255, 255, 255, 0.4), inset 0 -1px 2px rgba(0, 0, 0, 0.1), 0 8px 32px rgba(0, 113, 227, 0.1);
  transition: transform 0.2s cubic-bezier(0.34, 1.56, 0.64, 1), box-shadow 0.2s ease;
}

.activity-date::before {
  content: '';
  position: absolute;
  top: 2px;
  left: 50%;
  transform: translateX(-50%);
  width: 80%;
  height: 1px;
  background: radial-gradient(ellipse 100% 100%, rgba(255, 255, 255, 0.8) 0%, transparent 70%);
  pointer-events: none;
}

.activity-date:hover {
  transform: scale(1.02);
  box-shadow: inset 0 1px 2px rgba(255, 255, 255, 0.4), inset 0 -1px 2px rgba(0, 0, 0, 0.1), 0 12px 40px rgba(0, 113, 227, 0.15);
}

@supports not (backdrop-filter: blur(12px)) {
  .activity-date {
    background: rgba(255, 255, 255, 0.95);
    border-color: rgba(0, 113, 227, 0.2);
  }
}

.activity-day {
  font-size: 32px;
  font-weight: 700;
  color: var(--color-brand);
  line-height: 1;
  letter-spacing: var(--letter-spacing-tight);
}

.activity-month {
  font-size: var(--font-size-caption);
  font-weight: 600;
  color: var(--color-text-secondary);
}

.activity-weekday {
  font-size: var(--font-size-caption);
  color: var(--color-text-tertiary);
}

.activity-content {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  min-width: 0;
}

.activity-kicker {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  font-size: var(--font-size-caption);
  color: var(--color-text-tertiary);
}

.activity-type {
  padding: 4px 8px;
  background: rgba(0, 113, 227, 0.12);
  border: 1px solid rgba(0, 113, 227, 0.24);
  border-radius: var(--radius-xs);
  font-weight: 500;
  color: var(--color-brand);
}

.activity-place {
  font-weight: 500;
}

.activity-name {
  font-size: var(--font-size-body);
  font-weight: 700;
  color: var(--color-text-primary);
  margin: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.activity-meta {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  font-size: var(--font-size-caption);
}

.activity-status {
  padding: 4px 10px;
  border-radius: var(--radius-pill);
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

.status-effective {
  background: rgba(52, 211, 153, 0.15);
  color: rgb(6, 95, 70);
}

.status-pending {
  background: rgba(251, 191, 36, 0.15);
  color: rgb(120, 53, 15);
}

.status-draft {
  background: rgba(156, 163, 175, 0.15);
  color: rgb(55, 65, 81);
}

.status-refused {
  background: rgba(248, 113, 113, 0.15);
  color: rgb(153, 27, 27);
}

.activity-duration {
  font-weight: 600;
  color: var(--color-text-secondary);
}

/* Skeleton states */
.activities-skeleton {
  display: grid;
  gap: var(--space-4);
}

.activity-skeleton {
  padding: var(--space-5);
  display: flex;
  gap: var(--space-5);
  opacity: 0.6;
}

.skeleton-date {
  min-width: 64px;
  height: 72px;
  background: linear-gradient(90deg, rgba(0, 0, 0, 0.08) 0%, rgba(0, 0, 0, 0.12) 50%, rgba(0, 0, 0, 0.08) 100%);
  background-size: 200% 100%;
  border-radius: var(--radius-md);
  animation: skeleton-pulse 2s ease-in-out infinite;
}

.skeleton-content {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.skeleton-line {
  height: 12px;
  background: linear-gradient(90deg, rgba(0, 0, 0, 0.08) 0%, rgba(0, 0, 0, 0.12) 50%, rgba(0, 0, 0, 0.08) 100%);
  background-size: 200% 100%;
  border-radius: var(--radius-xs);
  animation: skeleton-pulse 2s ease-in-out infinite;
}

.skeleton-line--xs {
  width: 30%;
}

.skeleton-line--sm {
  width: 42%;
}

.skeleton-line--lg {
  width: 72%;
  height: 16px;
}

@keyframes skeleton-pulse {
  0% {
    background-position: 200% 0;
  }
  100% {
    background-position: -200% 0;
  }
}

/* Empty state */
.empty-state {
  padding: var(--space-10) var(--space-6);
  text-align: center;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-4);
}

.empty-icon {
  color: var(--color-text-tertiary);
  opacity: 0.5;
}

.empty-title {
  font-size: var(--font-size-headline);
  font-weight: 600;
  color: var(--color-text-primary);
  margin: 0;
}

.empty-description {
  font-size: var(--font-size-callout);
  color: var(--color-text-tertiary);
  margin: 0;
}

/* Pagination */
.pagination {
  padding: var(--space-4);
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-4);
}

.pagination-pages {
  display: flex;
  gap: var(--space-2);
  flex-wrap: wrap;
  justify-content: center;
}

.pagination-page {
  width: 36px;
  height: 36px;
  padding: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(
    135deg,
    rgba(255, 255, 255, 0.38) 0%,
    rgba(255, 255, 255, 0.2) 100%
  );
  -webkit-backdrop-filter: blur(8px) saturate(160%);
  backdrop-filter: blur(8px) saturate(160%);
  border: 1px solid rgba(255, 255, 255, 0.5);
  border-radius: var(--radius-sm);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.72);
  font-size: var(--font-size-callout);
  font-weight: 600;
  color: var(--color-text-secondary);
  cursor: pointer;
  transition: all var(--duration-fast) var(--ease-smooth);
}

.pagination-page:hover {
  background: rgba(0, 113, 227, 0.08);
  color: var(--color-text-primary);
}

.pagination-page.is-active {
  background: linear-gradient(
    135deg,
    rgba(0, 113, 227, 0.2) 0%,
    rgba(0, 113, 227, 0.1) 100%
  );
  border-color: rgba(255, 255, 255, 0.65);
  color: var(--color-brand);
}

.pagination-page:focus-visible {
  outline: 2px solid var(--color-brand);
  outline-offset: 2px;
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

.liquid-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.liquid-btn:disabled:hover::after {
  opacity: 0;
}

.liquid-btn:focus-visible {
  outline: 2px solid var(--color-brand);
  outline-offset: 2px;
}

.liquid-btn--secondary {
  background: linear-gradient(135deg, rgba(255, 255, 255, 0.48) 0%, rgba(255, 255, 255, 0.28) 100%);
  -webkit-backdrop-filter: blur(10px) saturate(170%);
  backdrop-filter: blur(10px) saturate(170%);
  border-color: rgba(255, 255, 255, 0.72);
  color: var(--color-text-primary);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.85), inset 0 -1px 0 rgba(0, 0, 0, 0.08), 0 2px 6px rgba(18, 48, 82, 0.06);
}

.liquid-btn--secondary:hover:not(:disabled) {
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.92), inset 0 -1px 0 rgba(0, 0, 0, 0.1), 0 4px 12px rgba(18, 48, 82, 0.1);
  transform: scale(1.02);
}

.liquid-btn--sm {
  padding: var(--space-2) var(--space-4);
  font-size: var(--font-size-caption);
  min-height: 36px;
}

.activity-detail {
  padding: var(--space-5);
  display: grid;
  gap: var(--space-5);
}

.activity-detail__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-4);
}

.activity-detail__eyebrow {
  margin: 0 0 var(--space-1);
  color: var(--color-text-tertiary);
  font-size: var(--font-size-caption);
  font-weight: 600;
  letter-spacing: var(--letter-spacing-wide);
  text-transform: uppercase;
}

.activity-detail__title {
  margin: 0;
  font-size: var(--font-size-headline);
  color: var(--color-text-primary);
}

.activity-detail__kv {
  display: grid;
  grid-template-columns: 72px 1fr;
  gap: var(--space-2) var(--space-4);
  margin: 0;
}

.activity-detail__kv dt {
  color: var(--color-text-tertiary);
  font-size: var(--font-size-callout);
}

.activity-detail__kv dd {
  margin: 0;
  color: var(--color-text-primary);
  font-size: var(--font-size-callout);
}

.activity-detail-enter-active,
.activity-detail-leave-active {
  transition: opacity var(--duration-base) var(--ease-smooth),
    transform var(--duration-base) var(--ease-spring);
}

.activity-detail-enter-from,
.activity-detail-leave-to {
  opacity: 0;
  transform: translateY(8px);
}

@media (max-width: 767px) {
  .volunteer-page {
    padding: var(--space-5) var(--space-4) var(--space-9);
    gap: var(--space-4);
  }

  .page-header {
    padding: var(--space-5);
  }

  .activity-card {
    flex-direction: column;
    gap: var(--space-4);
  }

  .activity-date {
    flex-direction: row;
    width: 100%;
    min-width: unset;
    justify-content: flex-start;
  }

  .activity-day {
    font-size: 28px;
  }

  .pagination {
    flex-wrap: wrap;
  }

  .pagination-pages {
    width: 100%;
    order: -1;
  }
}

@media (prefers-reduced-motion: reduce) {
  .liquid-btn::after {
    display: none;
  }

  .liquid-btn:active,
  .search-clear:active {
    transform: none;
  }

  .activity-card:hover {
    transform: none;
  }

  .activity-card:active {
    transform: none;
  }

  .activity-date:hover {
    transform: none;
  }

  .skeleton-line,
  .skeleton-date {
    animation: none;
  }
}
</style>
