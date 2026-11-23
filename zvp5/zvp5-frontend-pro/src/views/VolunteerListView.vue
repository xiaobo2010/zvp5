<script setup lang="ts">
import { ref, computed, onMounted, onBeforeUnmount, nextTick, watch } from 'vue'
import { useRouter } from 'vue-router'
import {
  CanaryApiError,
  fetchActivities,
  fetchProfile,
  isLoggedIn
} from '@/api/canary'
import type { ActivityScope } from '@/api/canary'
import { ApiError as VolunteerApiError } from '@/api/http'
import { currentRoles } from '@/api/permissions'
import {
  updateVolunteerActivityStatus
} from '@/api/volunteer-detail'
import BubbleFeedback from '@/components/BubbleFeedback.vue'
import VolunteerStack from '@/components/VolunteerStack.vue'
import { getStatusClass, getStatusLabel, mapActivity, useVolunteerStackExpansion, type Activity, type ActivityStatus } from '@/components/volunteerStack'

type AuditableStatus = Exclude<ActivityStatus, 'draft'>

const AUDIT_TARGETS: AuditableStatus[] = ['effective', 'pending', 'refused']

const router = useRouter()

// `department` is 学生会 in this codebase (see docs/AI_GATEWAY_API.md). Only
// these two roles may audit; the backend enforces the same list.
const viewerRoles = currentRoles()
const isAdminViewer = viewerRoles.includes('admin')
const isDepartmentViewer = viewerRoles.includes('department')
// 班级/校园范围是管理视角：普通学生只能看自己的活动，不能按班号或全校
// 枚举别人的义工。后端同样按角色收紧，这里先不渲染入口、也不发请求。
const canBrowseScopes = isAdminViewer || isDepartmentViewer

const pageSize = 10

const activeTab = ref<ActivityScope>('mine')
const searchWord = ref('')
const appliedSearch = ref('')
const activePage = ref(1)
const loading = ref(true)
const listLoading = ref(false)
const activities = ref<Activity[]>([])
const activityContextKey = ref('')
const totalActivities = ref(0)
const error = ref('')
const unauthenticated = ref(false)
const classId = ref('')
const expandedActivityId = useVolunteerStackExpansion(() => activities.value, () => activityContextKey.value)

const auditOpenId = ref<string | null>(null)
const auditingId = ref<string | null>(null)
const auditErrorId = ref<string | null>(null)
const auditErrorMessage = ref('')
const auditResultId = ref<string | null>(null)
const auditResultMessage = ref('')

let requestSeq = 0

const totalPages = computed(() => Math.ceil(totalActivities.value / pageSize))
const visiblePages = computed(() => {
  const total = totalPages.value
  if (total <= 7) return Array.from({ length: total }, (_, index) => index + 1)
  const start = Math.max(1, Math.min(activePage.value - 3, total - 6))
  return Array.from({ length: 7 }, (_, index) => start + index)
})
const needsClass = computed(() => activeTab.value === 'class' && !classId.value)

const pagerTrackRef = ref<HTMLElement | null>(null)
const pagerThumbRef = ref<HTMLElement | null>(null)
const pagerThumbState = ref({ left: 0, width: 0, ready: false })
const pagerDragging = ref(false)
const pagerDragOffset = ref(0)
const pagerPages = new Map<number, HTMLElement>()
let pagerObserver: ResizeObserver | null = null
let pagerObserverHost: HTMLElement | null = null
let pagerStartX = 0
let pagerMoved = false
let pagerSuppressClick = false

function setPagerPageRef(page: number, el: unknown) {
  if (el instanceof HTMLElement) {
    pagerPages.set(page, el)
  } else {
    pagerPages.delete(page)
  }
}

function ensurePagerObserver() {
  const host = pagerTrackRef.value
  if (!host || typeof ResizeObserver === 'undefined') {
    if (pagerObserver) {
      pagerObserver.disconnect()
      pagerObserver = null
      pagerObserverHost = null
    }
    return
  }
  if (pagerObserver && pagerObserverHost === host) return
  pagerObserver?.disconnect()
  pagerObserver = new ResizeObserver(() => measurePager())
  pagerObserverHost = host
  pagerObserver.observe(host)
}

function measurePager() {
  const host = pagerTrackRef.value
  const active = pagerPages.get(activePage.value)
  if (!host || !active) {
    if (pagerThumbState.value.ready) {
      pagerThumbState.value = { ...pagerThumbState.value, ready: false }
    }
    return
  }
  const hostRect = host.getBoundingClientRect()
  const activeRect = active.getBoundingClientRect()
  const left = activeRect.left - hostRect.left + host.scrollLeft
  const width = activeRect.width
  const previous = pagerThumbState.value
  if (
    previous.ready &&
    Math.abs(previous.left - left) < 0.5 &&
    Math.abs(previous.width - width) < 0.5
  ) {
    return
  }
  pagerThumbState.value = { left, width, ready: true }
}

async function syncPager(animate = true) {
  await nextTick()
  ensurePagerObserver()
  measurePager()
  if (animate && !reduceMotion()) {
    const el = pagerThumbRef.value
    el?.animate(
      [{ scale: '1' }, { scale: '1.1' }, { scale: '1' }],
      { duration: 420, easing: 'cubic-bezier(0.22, 1.55, 0.36, 1)' }
    )
  }
}

function reduceMotion() {
  return window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false
}

function onPagerPointerDown(event: PointerEvent) {
  if (event.pointerType === 'mouse' && event.button !== 0) return
  const host = pagerTrackRef.value
  if (!host) return
  pagerStartX = event.clientX
  pagerMoved = false
  pagerSuppressClick = false
  pagerDragging.value = true
  host.setPointerCapture?.(event.pointerId)
}

function onPagerPointerMove(event: PointerEvent) {
  if (!pagerDragging.value) return
  const delta = event.clientX - pagerStartX
  if (Math.abs(delta) > 4) pagerMoved = true
  pagerDragOffset.value = delta
}

function onPagerPointerUp(event: PointerEvent) {
  if (!pagerDragging.value) return
  const host = pagerTrackRef.value
  if (host) {
    try {
      host.releasePointerCapture(event.pointerId)
    } catch {
      // already released
    }
  }

  const target = document
    .elementFromPoint(event.clientX, event.clientY)
    ?.closest<HTMLElement>('.pagination-page')
  const page = Number(target?.dataset.page ?? '')
  pagerDragging.value = false
  pagerDragOffset.value = 0
  if (Number.isInteger(page) && page > 0) {
    pagerSuppressClick = true
    handlePageChange(page)
  }
}

function handlePagerClick(page: number) {
  if (pagerSuppressClick) {
    pagerSuppressClick = false
    return
  }
  handlePageChange(page)
}

watch(activePage, () => void syncPager())
watch(expandedActivityId, () => resetAuditState())
watch(totalPages, () => void syncPager(false))
watch(
  () => visiblePages.value.join(','),
  () => void syncPager(false)
)
watch(
  () => totalPages.value > 1 && !loading.value && !error.value && !unauthenticated.value && !needsClass.value,
  () => void syncPager(false)
)

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
  if (!canBrowseScopes && activeTab.value !== 'mine') {
    activeTab.value = 'mine'
  }
  if (needsClass.value) {
    requestSeq += 1
    loading.value = false
    listLoading.value = false
    error.value = ''
    activities.value = []
    totalActivities.value = 0
    expandedActivityId.value = null
    return
  }

  const seq = ++requestSeq
  const hasExisting = activities.value.length > 0
  if (!hasExisting) loading.value = true
  listLoading.value = true
  error.value = ''
  try {
    let targetPage = activePage.value
    for (let attempt = 0; attempt < 2; attempt += 1) {
      const result = await fetchActivities(
        activeTab.value,
        targetPage,
        pageSize,
        appliedSearch.value,
        classId.value
      )
      if (seq !== requestSeq) return
      const rows = result.activities ?? []
      const total = result.total ?? rows.length
      const pageCount = Math.ceil(total / pageSize)
      if (attempt === 0 && pageCount > 0 && targetPage > pageCount) {
        targetPage = pageCount
        activePage.value = pageCount
        continue
      }
      activityContextKey.value = JSON.stringify([activeTab.value, targetPage, appliedSearch.value, classId.value])
      activities.value = rows.map(mapActivity)
      totalActivities.value = total
      break
    }
    const pageCount = Math.ceil(totalActivities.value / pageSize)
    if (pageCount > 0 && activePage.value > pageCount) {
      activePage.value = pageCount
    }
  } catch (reason) {
    if (seq !== requestSeq) return
    if (!isAuthError(reason)) {
      activities.value = []
      totalActivities.value = 0
      expandedActivityId.value = null
      error.value = readableError(reason, '活动加载失败，请稍后重试。')
    }
  } finally {
    if (seq === requestSeq) {
      loading.value = false
      listLoading.value = false
    }
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
  resetAuditState()
  void loadActivities()
}

function handleSearchBlur(event: FocusEvent) {
  const next = event.relatedTarget
  if (next instanceof HTMLElement && next.closest('.search-clear')) return
  handleSearch()
}

function handleRefresh() {
  if (loading.value || listLoading.value) return
  resetAuditState()
  void bootstrap()
}

function handleTabChange(tab: ActivityScope) {
  if (activeTab.value === tab) return
  if (tab !== 'mine' && !canBrowseScopes) return
  activeTab.value = tab
  activePage.value = 1
  resetAuditState()
  void loadActivities()
}

function handlePageChange(page: number) {
  if (page === activePage.value || page < 1) return
  if (totalPages.value > 0 && page > totalPages.value) return
  activePage.value = page
  resetAuditState()
  void loadActivities()
}

function handleRetry() {
  if (loading.value) return
  void bootstrap()
}

function canAudit(activity: Activity): boolean {
  if (isAdminViewer) return true
  return isDepartmentViewer && activity.status === 'pending'
}

function auditTargets(activity: Activity): AuditableStatus[] {
  return AUDIT_TARGETS.filter((status) => status !== activity.status)
}

function auditErrorText(reason: unknown): string {
  if (reason instanceof VolunteerApiError) {
    if (reason.status === 401) return '登录状态已失效，请重新登录。'
    if (reason.status === 403) return '没有审核权限：仅学生会或管理员可修改审核状态。'
    if (reason.status === 409) return reason.message || '状态已被其他操作修改，请刷新后重试。'
    if (reason.status === 400) return reason.message || '当前状态不支持该操作。'
    return reason.message || '审核状态更新失败，请稍后重试。'
  }
  return '审核状态更新失败，请稍后重试。'
}

function resetAuditState() {
  auditOpenId.value = null
  auditErrorId.value = null
  auditErrorMessage.value = ''
  auditResultId.value = null
  auditResultMessage.value = ''
}

function toggleAudit(activity: Activity) {
  if (auditingId.value) return
  auditOpenId.value = auditOpenId.value === activity.rowKey ? null : activity.rowKey
  auditErrorId.value = null
  auditErrorMessage.value = ''
  auditResultId.value = null
  auditResultMessage.value = ''
}

async function applyAudit(activity: Activity, target: AuditableStatus) {
  if (auditingId.value || !canAudit(activity)) return
  auditingId.value = activity.rowKey
  auditErrorId.value = null
  auditErrorMessage.value = ''
  auditResultId.value = null
  auditResultMessage.value = ''
  try {
    await updateVolunteerActivityStatus(activity.id, target)
    for (const row of activities.value) {
      if (row.id !== activity.id) continue
      row.status = target
      row.statusLabel = getStatusLabel(target)
      row.statusClass = getStatusClass(target)
    }
    if (expandedActivityId.value === activity.rowKey) {
      auditOpenId.value = null
      auditResultId.value = activity.rowKey
      auditResultMessage.value = '活动审核状态已更新。'
    }
  } catch (reason) {
    if (reason instanceof VolunteerApiError && reason.status === 401) {
      unauthenticated.value = true
      activities.value = []
      totalActivities.value = 0
      expandedActivityId.value = null
      return
    }
    auditErrorId.value = activity.rowKey
    auditErrorMessage.value = auditErrorText(reason)
  } finally {
    auditingId.value = null
  }
}

function handleAuditEscape(event: KeyboardEvent, id: string) {
  if (auditOpenId.value !== id) return
  event.preventDefault()
  event.stopPropagation()
  auditOpenId.value = null
  const card = event.currentTarget as HTMLElement
  card.querySelector<HTMLElement>('[data-audit-toggle]')?.focus()
}

onMounted(() => {
  void bootstrap()
  window.addEventListener('resize', measurePager)
  if (document.fonts?.ready) void document.fonts.ready.then(() => syncPager(false))
  void syncPager(false)
})

onBeforeUnmount(() => {
  pagerObserver?.disconnect()
  pagerObserver = null
  pagerObserverHost = null
  pagerPages.clear()
  window.removeEventListener('resize', measurePager)
})
</script>

<template>
  <div class="volunteer-page">
    <header class="page-header glass-card surface-enter" :style="{ '--i': 0 }">
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
          :disabled="loading || listLoading"
          @click="handleRefresh"
        >
          刷新
        </button>
      </div>
    </header>

    <nav
      v-if="canBrowseScopes"
      class="scope-tabs glass-card surface-enter"
      :style="{ '--i': 1 }"
      role="tablist"
      aria-label="活动范围"
    >
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

    <div class="search-bar glass-card surface-enter" :style="{ '--i': 2 }">
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

    <div v-if="unauthenticated" class="empty-state glass-card surface-enter" :style="{ '--i': 0 }" role="alert">
      <p class="empty-title">需要登录</p>
      <BubbleFeedback class="glass-feedback--warn">
        登录后即可查看你的义工记录。
      </BubbleFeedback>
      <RouterLink
        class="liquid-btn liquid-btn--primary liquid-btn--sm"
        to="/login?redirect=/volunteers"
      >
        去登录
      </RouterLink>
    </div>

    <div v-else-if="error" class="empty-state glass-card surface-enter" :style="{ '--i': 0 }" role="alert">
      <p class="empty-title">活动加载失败</p>
      <BubbleFeedback class="glass-feedback--error">
        {{ error }}
      </BubbleFeedback>
      <button
        class="liquid-btn liquid-btn--secondary liquid-btn--sm"
        type="button"
        :disabled="loading"
        @click="handleRetry"
      >
        重试
      </button>
    </div>

    <div v-else-if="needsClass" class="empty-state glass-card surface-enter" :style="{ '--i': 0 }" role="status">
      <svg class="empty-icon" width="64" height="64" viewBox="0 0 64 64" fill="none" aria-hidden="true">
        <circle cx="32" cy="32" r="30" stroke="currentColor" stroke-width="2" opacity="0.3"/>
        <path d="M32 20V34M32 42V44" stroke="currentColor" stroke-width="3" stroke-linecap="round"/>
      </svg>
      <p class="empty-title">尚未绑定班级</p>
      <BubbleFeedback>
        绑定班级后即可查看班级活动；你也可以先浏览"我的活动"或"校园活动"。
      </BubbleFeedback>
    </div>

    <div
      v-else-if="loading && !activities.length"
      class="activities-skeleton"
      role="status"
      aria-live="polite"
      aria-label="正在加载活动"
    >
      <div v-for="n in 3" :key="n" class="activity-skeleton glass-card">
        <div class="skeleton-line skeleton-line--title"></div>
      </div>
    </div>

    <div v-else-if="activities.length === 0" class="empty-state glass-card surface-enter" :style="{ '--i': 0 }">
      <svg class="empty-icon" width="64" height="64" viewBox="0 0 64 64" fill="none" aria-hidden="true">
        <circle cx="32" cy="32" r="30" stroke="currentColor" stroke-width="2" opacity="0.3"/>
        <path d="M32 20V34M32 42V44" stroke="currentColor" stroke-width="3" stroke-linecap="round"/>
      </svg>
      <p class="empty-title">没有找到相关项目</p>
      <BubbleFeedback>
        {{ appliedSearch ? '尝试使用其他关键词搜索' : '暂时没有活动记录' }}
      </BubbleFeedback>
    </div>

    <VolunteerStack
      v-else
      v-model="expandedActivityId"
      :activities="activities"
      :list-loading="listLoading"
      @card-escape="handleAuditEscape"
    >
      <template #status="{ activity }">
          <button
            v-if="canAudit(activity)"
             :data-audit-toggle="activity.rowKey"
            class="detail-status detail-status--action"
            :class="activity.statusClass"
            type="button"
            title="点击修改审核状态"
             :aria-expanded="auditOpenId === activity.rowKey"
             :aria-controls="auditOpenId === activity.rowKey ? `audit-actions-${activity.rowKey}` : undefined"
            @click="toggleAudit(activity)"
          >
            {{ activity.statusLabel }}
          </button>
          <span v-else class="detail-status" :class="activity.statusClass">
            {{ activity.statusLabel }}
          </span>

          <div
             v-if="auditOpenId === activity.rowKey"
             :id="`audit-actions-${activity.rowKey}`"
            class="audit-actions"
            role="group"
             :aria-busy="auditingId === activity.rowKey"
            :aria-label="`将审核状态改为（当前：${activity.statusLabel}）`"
          >
            <button
              v-for="target in auditTargets(activity)"
              :key="target"
              class="audit-action"
              :class="getStatusClass(target)"
              type="button"
               :disabled="auditingId === activity.rowKey"
              @click="applyAudit(activity, target)"
            >
              改为{{ getStatusLabel(target) }}
            </button>
            <button
              class="audit-action audit-action--cancel"
              type="button"
               :disabled="auditingId === activity.rowKey"
              @click="auditOpenId = null"
            >
              取消
            </button>
          </div>

           <div v-if="auditErrorId === activity.rowKey" class="audit-feedback" role="alert">
            <BubbleFeedback class="glass-feedback--error">{{ auditErrorMessage }}</BubbleFeedback>
          </div>
           <div v-else-if="auditResultId === activity.rowKey" class="audit-feedback" aria-live="polite">
            <BubbleFeedback class="glass-feedback--success">{{ auditResultMessage }}</BubbleFeedback>
          </div>
      </template>
    </VolunteerStack>

    <nav
      v-if="totalPages > 1 && !loading && !error && !unauthenticated && !needsClass"
      class="pagination glass-card surface-enter"
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
      <div
        ref="pagerTrackRef"
        class="pagination-pages"
        :class="{ 'is-dragging': pagerDragging }"
        @pointerdown="onPagerPointerDown"
        @pointermove="onPagerPointerMove"
        @pointerup="onPagerPointerUp"
        @pointercancel="onPagerPointerUp"
      >
        <span
          ref="pagerThumbRef"
          class="pagination-thumb"
          :class="{ 'is-ready': pagerThumbState.ready }"
          :style="{
            transform: `translate3d(${pagerThumbState.left + pagerDragOffset}px, 0, 0)`,
            width: `${pagerThumbState.width}px`
          }"
          aria-hidden="true"
        />
        <button
          v-for="page in visiblePages"
          :key="page"
          :ref="(el) => setPagerPageRef(page, el)"
          :data-page="page"
          class="pagination-page"
          :class="{ 'is-active': page === activePage }"
          type="button"
          :aria-current="page === activePage ? 'page' : undefined"
          @click="handlePagerClick(page)"
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

.glass-card {
  position: relative;
  background: var(--surface-matte);
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
  background: var(--glass-floating-bg, linear-gradient(135deg, rgba(255, 255, 255, 0.74) 0%, rgba(232, 242, 252, 0.54) 100%));
  -webkit-backdrop-filter: var(--glass-blur, blur(12px) saturate(180%));
  backdrop-filter: var(--glass-blur, blur(12px) saturate(180%));
  border: 1px solid var(--glass-border, rgba(255, 255, 255, 0.86));
  border-radius: 50%;
  box-shadow:
    0 0 8px rgba(18, 48, 82, 0.06),
    var(--glass-rim, inset 0 0.5px 0 rgba(255, 255, 255, 0.85));
  color: var(--color-text-secondary);
  cursor: pointer;
  overflow: hidden;
  isolation: isolate;
  flex-shrink: 0;
  transition:
    transform 280ms cubic-bezier(0.34, 1.08, 0.64, 1),
    box-shadow 280ms cubic-bezier(0.4, 0, 0.2, 1),
    color 180ms cubic-bezier(0.4, 0, 0.2, 1);
}

.back-btn::before {
  content: '';
  position: absolute;
  inset: 0;
  background: radial-gradient(circle at 50% 40%, rgba(255, 255, 255, 0.35) 0%, transparent 70%);
  border-radius: 50%;
  pointer-events: none;
}

.back-btn:hover {
  color: var(--color-text-primary);
  transform: scale(1.04);
  box-shadow:
    0 0 14px rgba(18, 48, 82, 0.1),
    var(--glass-rim, inset 0 0.5px 0 rgba(255, 255, 255, 0.92));
}

.back-btn:active {
  transform: scale(0.96);
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

.scope-tabs {
  padding: 6px;
  display: flex;
  gap: 4px;
  position: relative;
}

.scope-tab {
  flex: 1;
  padding: var(--space-3) var(--space-4);
  background: var(--glass-floating-bg, linear-gradient(135deg, rgba(255, 255, 255, 0.68) 0%, rgba(232, 242, 252, 0.46) 100%));
  -webkit-backdrop-filter: var(--glass-blur, blur(10px) saturate(170%));
  backdrop-filter: var(--glass-blur, blur(10px) saturate(170%));
  border: 1px solid var(--glass-border, rgba(255, 255, 255, 0.82));
  border-radius: var(--radius-pill);
  box-shadow:
    0 2px 6px rgba(18, 48, 82, 0.04),
    var(--glass-rim, inset 0 1px 0 rgba(255, 255, 255, 0.92));
  font-size: var(--font-size-callout);
  font-weight: 600;
  color: var(--color-text-secondary);
  cursor: pointer;
  transition:
    color 180ms cubic-bezier(0.4, 0, 0.2, 1),
    background 180ms cubic-bezier(0.4, 0, 0.2, 1),
    border-color 180ms cubic-bezier(0.4, 0, 0.2, 1);
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
  transition: transform 420ms cubic-bezier(0.34, 1.12, 0.64, 1);
}

.scope-tab:nth-child(2).is-active ~ .scope-indicator {
  transform: translateX(calc(100% + 4px));
}

.scope-tab:nth-child(3).is-active ~ .scope-indicator {
  transform: translateX(calc(200% + 8px));
}

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

.search-input::-webkit-search-cancel-button,
.search-input::-webkit-search-decoration {
  display: none;
  -webkit-appearance: none;
  appearance: none;
}

.search-clear {
  width: 28px;
  height: 28px;
  padding: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--glass-floating-bg, linear-gradient(135deg, rgba(255, 255, 255, 0.74) 0%, rgba(232, 242, 252, 0.5) 100%));
  -webkit-backdrop-filter: var(--glass-blur, blur(10px) saturate(170%));
  backdrop-filter: var(--glass-blur, blur(10px) saturate(170%));
  border: 1px solid var(--glass-border, rgba(255, 255, 255, 0.84));
  border-radius: 50%;
  box-shadow:
    0 1px 4px rgba(18, 48, 82, 0.05),
    var(--glass-rim, inset 0 1px 0 rgba(255, 255, 255, 0.94));
  color: var(--color-text-secondary);
  cursor: pointer;
  transition:
    transform 280ms cubic-bezier(0.34, 1.08, 0.64, 1),
    background-color 180ms cubic-bezier(0.4, 0, 0.2, 1);
  flex-shrink: 0;
}

.search-clear:hover {
  background: rgba(0, 0, 0, 0.12);
  color: var(--color-text-primary);
}

.search-clear:active {
  transform: scale(0.9);
}

/* The status slot is rendered inside the shared volunteer card. */
.detail-status {
  display: inline-block;
  padding: 0.25rem 0.75rem;
  border-radius: 999px;
  font-size: 0.875rem;
  font-weight: 500;
}

.status-draft { background: rgba(148, 163, 184, 0.1); color: rgb(71, 85, 105); }
.status-pending { background: rgba(251, 191, 36, 0.1); color: rgb(180, 83, 9); }
.status-refused { background: rgba(239, 68, 68, 0.1); color: rgb(185, 28, 28); }
.status-effective { background: rgba(34, 197, 94, 0.1); color: rgb(21, 128, 61); }

.detail-status--action {
  border: 1px solid color-mix(in srgb, currentColor 28%, transparent);
  cursor: pointer;
  transition:
    transform var(--duration-fast) var(--ease-spring),
    box-shadow var(--duration-fast) var(--ease-smooth);
}

.detail-status--action:hover {
  box-shadow: 0 2px 8px rgba(16, 44, 71, 0.14);
}

.detail-status--action:active { transform: scale(0.96); }

.detail-status--action:focus-visible {
  outline: 2px solid var(--accent-ink);
  outline-offset: 2px;
}

.audit-actions {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
  width: 100%;
  margin-block-start: var(--space-1);
}

.audit-action {
  padding: 0.2rem 0.7rem;
  border: 1px solid transparent;
  border-radius: 999px;
  font-size: 0.8125rem;
  font-weight: 500;
  cursor: pointer;
  transition:
    transform var(--duration-fast) var(--ease-spring),
    opacity var(--duration-fast) var(--ease-smooth);
}

.audit-action:hover:not(:disabled) { transform: translateY(-1px); }
.audit-action:active:not(:disabled) { transform: scale(0.96); }
.audit-action:disabled { opacity: 0.55; cursor: progress; }

.audit-action:focus-visible {
  outline: 2px solid var(--accent-ink);
  outline-offset: 2px;
}

.audit-action--cancel {
  background: rgba(0, 0, 0, 0.06);
  color: var(--color-text-secondary);
}

.audit-feedback { width: 100%; margin-block-start: var(--space-2); }

.activities-skeleton {
  display: grid;
  gap: var(--space-3);
}

.activity-skeleton {
  padding: var(--space-4) var(--space-5);
  opacity: 0.6;
}

.skeleton-line {
  height: 16px;
  background: linear-gradient(90deg, rgba(0, 0, 0, 0.08) 0%, rgba(0, 0, 0, 0.12) 50%, rgba(0, 0, 0, 0.08) 100%);
  background-size: 200% 100%;
  border-radius: var(--radius-xs);
  animation: skeleton-pulse 2s ease-in-out infinite;
}

.skeleton-line--title {
  width: 60%;
}

@keyframes skeleton-pulse {
  0% {
    background-position: 200% 0;
  }
  100% {
    background-position: -200% 0;
  }
}

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

.pagination {
  padding: var(--space-4);
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-4);
}

.pagination-pages {
  position: relative;
  display: flex;
  gap: 6px;
  padding: 3px;
  flex-wrap: nowrap;
  justify-content: center;
  border-radius: var(--radius-pill);
  background: rgba(255, 255, 255, 0.28);
  border: 1px solid rgba(255, 255, 255, 0.48);
  touch-action: pan-y;
  isolation: isolate;
}

.pagination-thumb {
  position: absolute;
  top: 3px;
  left: 0;
  height: calc(100% - 6px);
  border-radius: var(--radius-pill);
  background: rgba(255, 255, 255, 0.72);
  border: 1px solid var(--glass-border, rgba(255, 255, 255, 0.9));
  box-shadow:
    0 2px 8px rgba(18, 48, 82, 0.08),
    var(--glass-rim, inset 0 1px 0 rgba(255, 255, 255, 0.95));
  opacity: 0;
  pointer-events: none;
  transition:
    transform 470ms cubic-bezier(0.16, 1.72, 0.3, 1),
    width 470ms cubic-bezier(0.16, 1.72, 0.3, 1),
    opacity 180ms cubic-bezier(0.4, 0, 0.2, 1);
  z-index: 0;
}

.pagination-thumb.is-ready {
  opacity: 1;
}

.pagination-pages.is-dragging .pagination-thumb {
  transition:
    transform 120ms linear,
    width 180ms cubic-bezier(0.4, 0, 0.6, 1),
    opacity 180ms cubic-bezier(0.4, 0, 0.2, 1);
}

.pagination-page {
  position: relative;
  z-index: 1;
  width: 36px;
  height: 36px;
  padding: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  background: transparent;
  border: 1px solid transparent;
  border-radius: var(--radius-pill);
  box-shadow: none;
  font-size: var(--font-size-callout);
  font-weight: 600;
  color: var(--color-text-secondary);
  cursor: pointer;
  transition:
    transform 280ms cubic-bezier(0.34, 1.08, 0.64, 1),
    background-color 180ms cubic-bezier(0.4, 0, 0.2, 1),
    border-color 180ms cubic-bezier(0.4, 0, 0.2, 1);
}

.pagination-page:hover {
  background: rgba(0, 113, 227, 0.08);
  color: var(--color-text-primary);
}

.pagination-page.is-active {
  background: transparent;
  border-color: transparent;
  color: var(--color-brand);
  font-weight: 700;
}

.pagination-page:focus-visible {
  outline: 2px solid var(--color-brand);
  outline-offset: 2px;
}

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
  transition:
    transform 280ms cubic-bezier(0.34, 1.08, 0.64, 1),
    box-shadow 280ms cubic-bezier(0.4, 0, 0.2, 1);
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

.liquid-btn:active {
  transform: scale(0.96);
}

.liquid-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.liquid-btn:focus-visible {
  outline: 2px solid var(--color-brand);
  outline-offset: 2px;
}

.liquid-btn--primary {
  background: linear-gradient(135deg, rgba(0, 113, 227, 0.96) 0%, rgba(0, 113, 227, 0.86) 100%);
  -webkit-backdrop-filter: blur(12px) saturate(180%);
  backdrop-filter: blur(12px) saturate(180%);
  border-color: rgba(255, 255, 255, 0.82);
  color: white;
  box-shadow:
    0 4px 14px rgba(0, 113, 227, 0.28),
    var(--glass-rim, inset 0 1px 0 rgba(255, 255, 255, 0.55));
}

.liquid-btn--primary:hover:not(:disabled) {
  background: linear-gradient(135deg, rgba(0, 119, 237, 0.98) 0%, rgba(0, 119, 237, 0.88) 100%);
  box-shadow:
    0 6px 18px rgba(0, 113, 227, 0.36),
    var(--glass-rim, inset 0 1px 0 rgba(255, 255, 255, 0.62));
  transform: scale(1.02);
}

.liquid-btn--secondary {
  background: var(--glass-floating-bg, linear-gradient(135deg, rgba(255, 255, 255, 0.76) 0%, rgba(232, 242, 252, 0.56) 100%));
  -webkit-backdrop-filter: var(--glass-blur, blur(12px) saturate(180%));
  backdrop-filter: var(--glass-blur, blur(12px) saturate(180%));
  border-color: var(--glass-border, rgba(255, 255, 255, 0.88));
  color: var(--color-text-primary);
  box-shadow:
    0 3px 10px rgba(18, 48, 82, 0.08),
    var(--glass-rim, inset 0 1px 0 rgba(255, 255, 255, 0.96));
}

.liquid-btn--secondary:hover:not(:disabled) {
  box-shadow:
    0 4px 12px rgba(18, 48, 82, 0.1),
    var(--glass-rim, inset 0 1px 0 rgba(255, 255, 255, 0.92));
  transform: scale(1.02);
}

.liquid-btn--sm {
  padding: var(--space-2) var(--space-4);
  font-size: var(--font-size-caption);
  min-height: 36px;
}

@media (max-width: 767px) {
  .volunteer-page {
    grid-template-columns: minmax(0, 1fr);
    padding: var(--space-5) var(--space-4) var(--space-9);
    gap: var(--space-4);
  }

  .page-header {
    padding: var(--space-5);
  }

  .page-header__content {
    min-width: 0;
  }

  .activities-grid {
    grid-template-columns: 1fr;
    gap: var(--space-4);
  }

  .pagination {
    flex-wrap: wrap;
  }

  .pagination-pages {
    width: 100%;
    order: -1;
    gap: 4px;
    padding: 2px;
  }

  .pagination-page {
    width: 32px;
    height: 32px;
  }

  .pagination-thumb {
    top: 2px;
    height: calc(100% - 4px);
  }
}

@media (prefers-reduced-motion: reduce) {
  .pagination-thumb,
  .audit-action,
  .detail-status--action {
    transition: none;
    animation: none;
  }

  .liquid-btn:active,
  .search-clear:active,
  .back-btn:active,
  .audit-action:active,
  .detail-status--action:active {
    transform: none;
  }

  .skeleton-line { animation: none; }
}
</style>
