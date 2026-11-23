<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import BubbleFeedback from '@/components/BubbleFeedback.vue'
import { isLoggedIn } from '@/api/canary'
import { ApiError } from '@/api/http'
import { currentRoles } from '@/api/permissions'
import {
  fetchVolunteerDetail,
  fetchVolunteerMembers,
  fetchVolunteerUser,
  type VolunteerActivity,
  type VolunteerActivityStatus,
  type VolunteerMember,
  type VolunteerMemberStatus
} from '@/api/volunteer-detail'
import { renderMarkdown } from '@/utils/markdown'

const route = useRoute()
const router = useRouter()

type ActivityStatus = VolunteerActivityStatus

const pageSize = 10
// 参与名单包含全校学生信息；普通用户只看活动本身，不请求成员接口。
const canViewRoster = currentRoles().some((role) => role === 'admin' || role === 'department')
const TYPE_LABELS: Record<string, string> = {
  'on-campus': '校内活动',
  'off-campus': '校外活动',
  'social-practice': '社会实践',
  hybrid: '混合活动'
}
const MODE_LABELS: Record<string, string> = {
  'on-campus': '校内义工',
  'off-campus': '校外义工',
  'social-practice': '社会实践'
}

const activity = ref<VolunteerActivity | null>(null)
const creatorName = ref('')
const loading = ref(true)
const error = ref('')
const unauthenticated = ref(false)

const members = ref<VolunteerMember[]>([])
const membersTotal = ref(0)
const membersLoading = ref(false)
const membersError = ref('')
const page = ref(1)
const searchWord = ref('')
const appliedSearch = ref('')

let bootSeq = 0
let memberSeq = 0

const activityId = computed(() => String(route.params.id ?? '').trim())

const totalPages = computed(() =>
  Math.max(1, Math.ceil(membersTotal.value / pageSize))
)
const visiblePages = computed(() => {
  const total = totalPages.value
  if (total <= 7) return Array.from({ length: total }, (_, index) => index + 1)
  const start = Math.max(1, Math.min(page.value - 3, total - 6))
  return Array.from({ length: 7 }, (_, index) => start + index)
})

const typeLabel = computed(() => {
  const current = activity.value
  if (!current) return ''
  return TYPE_LABELS[current.type] ?? current.type ?? '活动'
})
const statusLabel = computed(() => {
  const current = activity.value
  if (!current) return ''
  return getStatusLabel(normalizeStatus(current.status))
})
const statusClass = computed(() => {
  const current = activity.value
  if (!current) return ''
  return getStatusClass(normalizeStatus(current.status))
})

function normalizeStatus(value: string | undefined): ActivityStatus {
  if (value === 'draft' || value === 'refused' || value === 'effective') {
    return value
  }
  return 'pending'
}

function getStatusLabel(status: ActivityStatus): string {
  const labels: Record<ActivityStatus, string> = {
    draft: '草稿',
    pending: '待审核',
    refused: '已拒绝',
    effective: '已生效'
  }
  return labels[status]
}

function getStatusClass(status: ActivityStatus): string {
  const classes: Record<ActivityStatus, string> = {
    draft: 'status-draft',
    pending: 'status-pending',
    refused: 'status-refused',
    effective: 'status-effective'
  }
  return classes[status]
}

function memberStatusLabel(status: VolunteerMemberStatus): string {
  const labels: Record<VolunteerMemberStatus, string> = {
    pending: '待审核',
    effective: '已生效',
    refused: '已拒绝'
  }
  return labels[status] ?? '待审核'
}

function memberStatusClass(status: VolunteerMemberStatus): string {
  const classes: Record<VolunteerMemberStatus, string> = {
    pending: 'status-pending',
    effective: 'status-effective',
    refused: 'status-refused'
  }
  return classes[status] ?? 'status-pending'
}

function modeLabel(mode: string): string {
  return MODE_LABELS[mode] ?? mode ?? '—'
}

function memberName(member: VolunteerMember): string {
  return member.member_name || member.user?.name || '未知成员'
}

function memberId(member: VolunteerMember): string {
  const id = member.user?.id
  return id === undefined || id === null ? '' : String(id)
}

function formatHours(value: number): string {
  const hours = Number(value)
  if (!Number.isFinite(hours)) return '—'
  return String(Math.round(hours * 100) / 100)
}

function formatDetailDate(date: string): string {
  const match = /^(\d{4}-\d{2}-\d{2})(?:[T ](\d{2}:\d{2}))?/.exec(date)
  if (match) return match[2] ? `${match[1]} ${match[2]}` : match[1]
  return date || '—'
}

function readableError(reason: unknown, fallback: string): string {
  if (!(reason instanceof ApiError)) return fallback
  if (reason.status === 400) return '活动 ID 无效，请返回列表重新选择。'
  if (reason.status === 404) return '未找到该义工活动，可能已被删除。'
  if (reason.status === 403) return '没有查看该活动的权限。'
  return reason.message || fallback
}

function isAuthError(reason: unknown): boolean {
  if (reason instanceof ApiError && reason.status === 401) {
    unauthenticated.value = true
    activity.value = null
    members.value = []
    membersTotal.value = 0
    loading.value = false
    membersLoading.value = false
    return true
  }
  return false
}

async function loadMembers(targetPage = page.value) {
  const current = activity.value
  if (!current || !canViewRoster) return
  const seq = ++memberSeq
  membersLoading.value = true
  membersError.value = ''
  try {
    const result = await fetchVolunteerMembers(
      current._id,
      targetPage,
      pageSize,
      appliedSearch.value
    )
    if (seq !== memberSeq) return
    members.value = result.members
    membersTotal.value = result.total

    const pageCount = Math.ceil(result.total / pageSize)
    if (pageCount > 0 && targetPage > pageCount) {
      page.value = pageCount
      void loadMembers(pageCount)
      return
    }
    page.value = Math.max(1, targetPage)
  } catch (reason) {
    if (seq !== memberSeq) return
    if (isAuthError(reason)) return
    members.value = []
    membersError.value = readableError(reason, '参与名单加载失败，请稍后重试。')
  } finally {
    if (seq === memberSeq) membersLoading.value = false
  }
}

async function bootstrap() {
  if (!isLoggedIn()) {
    unauthenticated.value = true
    loading.value = false
    return
  }
  if (!activityId.value) {
    error.value = '活动 ID 无效。'
    loading.value = false
    return
  }

  const seq = ++bootSeq
  memberSeq += 1
  unauthenticated.value = false
  loading.value = true
  error.value = ''
  membersError.value = ''
  activity.value = null
  creatorName.value = ''
  members.value = []
  membersTotal.value = 0
  page.value = 1
  searchWord.value = ''
  appliedSearch.value = ''

  try {
    const detail = await fetchVolunteerDetail(activityId.value)
    if (seq !== bootSeq) return
    activity.value = detail.activity
    membersTotal.value = detail.members_count
    loading.value = false
  } catch (reason) {
    if (seq !== bootSeq) return
    if (isAuthError(reason)) return
    activity.value = null
    error.value = readableError(reason, '义工详情加载失败，请稍后重试。')
    loading.value = false
    return
  }

  const creator = activity.value?.creator
  if (creator) {
    void fetchVolunteerUser(creator)
      .then((user) => {
        if (seq === bootSeq) creatorName.value = user?.name ?? ''
      })
      .catch(() => {
        // The creator label is decorative; 404/403 must not break the page.
      })
  }

  if (canViewRoster) await loadMembers(1)
}

function handlePageChange(target: number) {
  if (membersLoading.value) return
  if (target < 1 || target > totalPages.value || target === page.value) return
  void loadMembers(target)
}

function handleSearch() {
  const next = searchWord.value.trim()
  if (next === appliedSearch.value) return
  appliedSearch.value = next
  page.value = 1
  void loadMembers(1)
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

function goBack() {
  void router.push('/volunteers')
}

onMounted(() => {
  void bootstrap()
})

// `/volunteers/:id` reuses the component when only the param changes.
watch(activityId, () => {
  void bootstrap()
})

onBeforeUnmount(() => {
  bootSeq += 1
  memberSeq += 1
})
</script>

<template>
  <div class="volunteer-detail">
    <header class="page-header glass-card surface-enter" :style="{ '--i': 0 }">
      <div class="page-header__lead">
        <button class="back-btn" type="button" aria-label="返回义工列表" @click="goBack">
          <svg width="18" height="18" viewBox="0 0 20 20" fill="none" aria-hidden="true">
            <path d="M12 4L6 10L12 16" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
          </svg>
        </button>
        <div class="page-header__content">
          <p class="page-header__eyebrow">志愿服务</p>
          <h1 class="page-header__title">{{ activity?.name || '义工详情' }}</h1>
          <p class="page-header__subtitle">
            {{ loading && !activity ? '正在加载活动资料…' : '活动资料与参与名单' }}
          </p>
        </div>
      </div>
      <div class="page-header__actions">
        <button
          class="liquid-btn liquid-btn--secondary liquid-btn--sm"
          type="button"
          :disabled="loading || membersLoading"
          @click="handleRefresh"
        >
          刷新
        </button>
      </div>
    </header>

    <div v-if="unauthenticated" class="empty-state glass-card surface-enter" :style="{ '--i': 1 }" role="alert">
      <p class="empty-title">需要登录</p>
      <BubbleFeedback class="glass-feedback--warn">
        登录后即可查看义工活动详情与参与名单。
      </BubbleFeedback>
      <RouterLink
        class="liquid-btn liquid-btn--primary liquid-btn--sm"
        :to="`/login?redirect=${encodeURIComponent(route.fullPath)}`"
      >
        去登录
      </RouterLink>
    </div>

    <div v-else-if="error" class="empty-state glass-card surface-enter" :style="{ '--i': 1 }" role="alert">
      <p class="empty-title">无法打开活动</p>
      <BubbleFeedback class="glass-feedback--error">{{ error }}</BubbleFeedback>
      <button
        class="liquid-btn liquid-btn--secondary liquid-btn--sm"
        type="button"
        :disabled="loading"
        @click="handleRefresh"
      >
        重试
      </button>
    </div>

    <div
      v-else-if="loading && !activity"
      class="detail-skeleton glass-card"
      role="status"
      aria-live="polite"
      aria-label="正在加载活动详情"
    >
      <div class="skeleton-line skeleton-line--title"></div>
      <div class="skeleton-line"></div>
      <div class="skeleton-line skeleton-line--short"></div>
    </div>

    <template v-else-if="activity">
      <article class="detail-card glass-card surface-enter" :style="{ '--i': 1 }">
        <dl class="detail-info">
          <div class="detail-info-row">
            <dt>活动时间</dt>
            <dd>{{ formatDetailDate(activity.date) }}</dd>
          </div>
          <div class="detail-info-row">
            <dt>地点</dt>
            <dd>{{ activity.place || '—' }}</dd>
          </div>
          <div class="detail-info-row">
            <dt>类型</dt>
            <dd><span class="detail-type" :class="`activity-type--${activity.type}`">{{ typeLabel }}</span></dd>
          </div>
          <div class="detail-info-row">
            <dt>创建者</dt>
            <dd>{{ creatorName || activity.creator_name || '—' }}</dd>
          </div>
          <div class="detail-info-row">
            <dt>审核状态</dt>
            <dd><span class="detail-status" :class="statusClass">{{ statusLabel }}</span></dd>
          </div>
        </dl>

        <section v-if="activity.description" class="detail-description" aria-labelledby="activity-desc-title">
          <h2 id="activity-desc-title" class="detail-description__title">活动说明</h2>
          <div class="detail-description__body" v-html="renderMarkdown(activity.description ?? '')"></div>
        </section>
      </article>

      <section
        v-if="canViewRoster"
        class="roster glass-card surface-enter"
        :style="{ '--i': 2 }"
        :aria-busy="membersLoading"
        aria-labelledby="roster-title"
      >
        <header class="roster__head">
          <div class="roster__heading">
            <h2 id="roster-title" class="roster__title">参与名单</h2>
            <p class="roster__count" aria-live="polite">共 {{ membersTotal }} 条参与记录</p>
          </div>
          <div class="search-bar" role="search">
            <svg class="search-icon" width="18" height="18" viewBox="0 0 20 20" fill="none" aria-hidden="true">
              <circle cx="9" cy="9" r="6" stroke="currentColor" stroke-width="1.5"/>
              <path d="M13.5 13.5L17 17" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
            </svg>
            <input
              v-model="searchWord"
              class="search-input"
              type="search"
              aria-label="搜索参与成员姓名或学号"
              placeholder="搜索成员姓名或学号"
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
              <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true">
                <path d="M4 4L12 12M12 4L4 12" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
              </svg>
            </button>
          </div>
        </header>

        <div v-if="membersError" class="roster__feedback" role="alert">
          <BubbleFeedback class="glass-feedback--error">{{ membersError }}</BubbleFeedback>
          <button
            class="liquid-btn liquid-btn--secondary liquid-btn--sm"
            type="button"
            :disabled="membersLoading"
            @click="loadMembers(page)"
          >
            重新加载
          </button>
        </div>

        <div
          v-else-if="membersLoading && !members.length"
          class="roster-skeleton"
          role="status"
          aria-live="polite"
          aria-label="正在加载参与名单"
        >
          <div v-for="n in 4" :key="n" class="skeleton-line"></div>
        </div>

        <div v-else-if="!members.length" class="roster__empty">
          <p class="roster__empty-title">{{ appliedSearch ? '没有匹配的成员' : '暂无参与成员' }}</p>
          <BubbleFeedback>
            {{ appliedSearch ? '试试其他姓名或学号。' : '该活动还没有录入参与记录。' }}
          </BubbleFeedback>
        </div>

        <div v-else class="roster__table-wrap">
          <table class="roster-table">
            <caption class="sr-only">
              {{ activity.name }} 的参与名单，含成员姓名、义工类型、时长与审核状态
            </caption>
            <thead>
              <tr>
                <th scope="col">成员</th>
                <th scope="col">类型</th>
                <th scope="col">时长</th>
                <th scope="col">状态</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="member in members" :key="member._id">
                <th scope="row" class="roster-table__member">
                  <span class="roster-table__name">{{ memberName(member) }}</span>
                  <span v-if="memberId(member)" class="roster-table__id">{{ memberId(member) }}</span>
                </th>
                <td><span class="roster-table__mode" :class="`roster-table__mode--${member.mode}`">{{ modeLabel(member.mode) }}</span></td>
                <td class="roster-table__duration">{{ formatHours(member.duration) }} h</td>
                <td>
                  <span class="detail-status" :class="memberStatusClass(member.status)">
                    {{ memberStatusLabel(member.status) }}
                  </span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>

        <nav
          v-if="membersTotal > pageSize && !membersError"
          class="pagination"
          aria-label="参与名单分页"
        >
          <button
            class="liquid-btn liquid-btn--secondary liquid-btn--sm"
            type="button"
            :disabled="membersLoading || page <= 1"
            @click="handlePageChange(page - 1)"
          >
            上一页
          </button>
          <div class="pagination-pages">
            <button
              v-for="item in visiblePages"
              :key="item"
              class="pagination-page"
              :class="{ 'is-active': item === page }"
              type="button"
              :aria-current="item === page ? 'page' : undefined"
              :disabled="membersLoading"
              @click="handlePageChange(item)"
            >
              {{ item }}
            </button>
          </div>
          <button
            class="liquid-btn liquid-btn--secondary liquid-btn--sm"
            type="button"
            :disabled="membersLoading || page >= totalPages"
            @click="handlePageChange(page + 1)"
          >
            下一页
          </button>
        </nav>
      </section>

      <BubbleFeedback v-if="canViewRoster" class="detail-note">
        参与名单包含全部状态的记录；审核状态由学生会或管理员维护，每页 {{ pageSize }} 条。
      </BubbleFeedback>
    </template>
  </div>
</template>

<style scoped>
.volunteer-detail {
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
  display: flex;
  align-items: center;
  justify-content: center;
  width: 40px;
  height: 40px;
  padding: 0;
  background: var(--glass-floating-bg);
  border: 1px solid var(--glass-border);
  border-radius: 50%;
  box-shadow: var(--glass-rim), 0 2px 8px rgba(18, 48, 82, 0.08);
  color: var(--color-text-secondary);
  cursor: pointer;
  flex-shrink: 0;
  transition: transform var(--duration-fast) var(--ease-spring), color var(--duration-fast) var(--ease-smooth);
}

.back-btn:hover { color: var(--color-text-primary); }
.back-btn:active { transform: scale(0.94); }
.back-btn:focus-visible { outline: 2px solid var(--color-brand); outline-offset: 2px; }

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
  overflow-wrap: anywhere;
}

.page-header__subtitle {
  font-size: var(--font-size-callout);
  color: var(--color-text-secondary);
  margin: 0;
}

.page-header__actions { display: flex; gap: var(--space-3); align-items: center; }

.detail-card { padding: var(--space-6); display: grid; gap: var(--space-5); }

.detail-info {
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.detail-info-row {
  display: grid;
  grid-template-columns: min(5rem, 30%) minmax(0, 1fr);
  gap: var(--space-3);
  align-items: baseline;
}

.detail-info-row dt {
  font-size: 0.875rem;
  font-weight: 500;
  color: var(--color-text-tertiary);
}

.detail-info-row dd {
  margin: 0;
  font-size: 0.9375rem;
  color: var(--color-text-primary);
  overflow-wrap: anywhere;
}

.detail-type,
.detail-status {
  display: inline-block;
  padding: 0.25rem 0.75rem;
  border-radius: 999px;
  font-size: 0.875rem;
  font-weight: 500;
}

.activity-type--on-campus { background: rgba(56, 189, 248, 0.1); color: var(--accent-ink); }
.activity-type--off-campus { background: rgba(110, 231, 183, 0.1); color: rgb(5, 150, 105); }
.activity-type--social-practice { background: rgba(251, 191, 36, 0.1); color: rgb(180, 83, 9); }
.activity-type--hybrid { background: rgba(167, 139, 250, 0.1); color: rgb(109, 40, 217); }

.status-draft { background: rgba(148, 163, 184, 0.1); color: rgb(71, 85, 105); }
.status-pending { background: rgba(251, 191, 36, 0.1); color: rgb(180, 83, 9); }
.status-refused { background: rgba(239, 68, 68, 0.1); color: rgb(185, 28, 28); }
.status-effective { background: rgba(34, 197, 94, 0.1); color: rgb(21, 128, 61); }

.detail-description {
  padding-block-start: var(--space-4);
  border-block-start: 1px solid var(--color-border-hairline);
}

.detail-description__title {
  font-size: var(--font-size-callout);
  font-weight: 600;
  color: var(--color-text-secondary);
  margin: 0 0 var(--space-3);
}

.detail-description__body {
  font-size: 0.9375rem;
  line-height: var(--line-height-normal);
  color: var(--color-text-primary);
  overflow-wrap: anywhere;
}

.detail-description__body :deep(p) { margin: 0 0 var(--space-3); }
.detail-description__body :deep(p:last-child) { margin-bottom: 0; }
.detail-description__body :deep(a) { color: var(--color-brand); text-decoration: underline; }
.detail-description__body :deep(code) {
  padding: 0.1em 0.35em;
  border-radius: var(--radius-xs);
  background: rgba(0, 0, 0, 0.06);
  font-size: 0.9em;
}
.detail-description__body :deep(pre) {
  overflow-x: auto;
  padding: var(--space-3);
  border-radius: var(--radius-sm);
  background: rgba(0, 0, 0, 0.06);
  margin: 0 0 var(--space-3);
}

.roster { padding: var(--space-6); display: grid; gap: var(--space-4); }

.roster__head {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: var(--space-4);
  flex-wrap: wrap;
}

.roster__heading { display: grid; gap: var(--space-1); }

.roster__title {
  font-size: var(--font-size-headline);
  font-weight: 600;
  color: var(--color-text-primary);
  margin: 0;
}

.roster__count {
  font-size: var(--font-size-callout);
  color: var(--color-text-tertiary);
  font-variant-numeric: tabular-nums;
  margin: 0;
}

.search-bar {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: 0 var(--space-3);
  min-height: 40px;
  min-width: min(100%, 260px);
  border: 1px solid var(--glass-border);
  border-radius: var(--radius-pill);
  background: var(--glass-floating-bg);
  box-shadow: var(--glass-rim);
}

.search-icon { color: var(--color-text-tertiary); flex-shrink: 0; }

.search-input {
  flex: 1;
  min-width: 0;
  background: transparent;
  border: none;
  font-size: var(--font-size-callout);
  color: var(--color-text-primary);
  outline: none;
}

.search-input::placeholder { color: var(--color-text-tertiary); }
.search-input::-webkit-search-cancel-button,
.search-input::-webkit-search-decoration { display: none; -webkit-appearance: none; appearance: none; }

.search-clear {
  width: 24px;
  height: 24px;
  padding: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 50%;
  color: var(--color-text-secondary);
  background: rgba(0, 0, 0, 0.06);
  flex-shrink: 0;
}

.search-clear:focus-visible { outline: 2px solid var(--color-brand); outline-offset: 2px; }

.roster__feedback { display: grid; justify-items: start; gap: var(--space-3); }

.roster__empty { padding: var(--space-6) 0; display: grid; justify-items: start; gap: var(--space-3); }

.roster__empty-title {
  font-size: var(--font-size-headline);
  font-weight: 600;
  color: var(--color-text-primary);
  margin: 0;
}

.roster__table-wrap { overflow-x: auto; }

.roster-table {
  width: 100%;
  border-collapse: collapse;
  text-align: start;
}

.roster-table th,
.roster-table td {
  padding: var(--space-3) var(--space-2);
  border-block-end: 1px solid var(--color-border-hairline);
  font-size: 0.9375rem;
  text-align: start;
  vertical-align: middle;
}

.roster-table thead th {
  font-size: 0.8125rem;
  font-weight: 600;
  color: var(--color-text-tertiary);
  white-space: nowrap;
}

.roster-table tbody tr:last-child th,
.roster-table tbody tr:last-child td { border-block-end: none; }

.roster-table__member { font-weight: 600; }
.roster-table__name { display: block; color: var(--color-text-primary); overflow-wrap: anywhere; }
.roster-table__id {
  display: block;
  font-size: 0.8125rem;
  font-weight: 400;
  color: var(--color-text-tertiary);
  font-variant-numeric: tabular-nums;
}

.roster-table__mode {
  display: inline-block;
  padding: 0.15rem 0.6rem;
  border-radius: 999px;
  background: rgba(56, 189, 248, 0.1);
  color: var(--accent-ink);
  font-size: 0.8125rem;
  white-space: nowrap;
}

.roster-table__mode--on-campus { background: rgba(56, 189, 248, 0.1); color: var(--accent-ink); }
.roster-table__mode--off-campus { background: rgba(110, 231, 183, 0.1); color: rgb(5, 150, 105); }
.roster-table__mode--social-practice { background: rgba(251, 191, 36, 0.1); color: rgb(180, 83, 9); }

.roster-table__duration {
  font-variant-numeric: tabular-nums;
  color: var(--accent-ink);
  font-weight: 600;
  white-space: nowrap;
}

.pagination {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  flex-wrap: wrap;
  padding-block-start: var(--space-2);
}

.pagination-pages {
  display: flex;
  gap: 4px;
  padding: 3px;
  border-radius: var(--radius-pill);
  background: rgba(255, 255, 255, 0.28);
  border: 1px solid rgba(255, 255, 255, 0.48);
}

.pagination-page {
  width: 34px;
  height: 34px;
  display: flex;
  align-items: center;
  justify-content: center;
  border: 1px solid transparent;
  border-radius: var(--radius-pill);
  font-size: var(--font-size-callout);
  font-weight: 600;
  color: var(--color-text-secondary);
  transition: background-color var(--duration-fast) var(--ease-smooth), color var(--duration-fast) var(--ease-smooth);
}

.pagination-page:hover:not(:disabled) { background: rgba(0, 113, 227, 0.08); color: var(--color-text-primary); }
.pagination-page.is-active { color: var(--color-brand); font-weight: 700; }
.pagination-page:disabled { opacity: 0.5; cursor: not-allowed; }
.pagination-page:focus-visible { outline: 2px solid var(--color-brand); outline-offset: 2px; }

.detail-skeleton {
  padding: var(--space-6);
  display: grid;
  gap: var(--space-3);
}

.skeleton-line {
  height: 16px;
  border-radius: var(--radius-xs);
  background: linear-gradient(90deg, rgba(0, 0, 0, 0.08) 0%, rgba(0, 0, 0, 0.12) 50%, rgba(0, 0, 0, 0.08) 100%);
  background-size: 200% 100%;
  animation: skeleton-pulse 2s ease-in-out infinite;
}

.skeleton-line--title { width: 62%; }
.skeleton-line--short { width: 40%; }

.roster-skeleton { display: grid; gap: var(--space-3); padding-block: var(--space-2); }

@keyframes skeleton-pulse {
  0% { background-position: 200% 0; }
  100% { background-position: -200% 0; }
}

.empty-state {
  padding: var(--space-10) var(--space-6);
  text-align: center;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-4);
}

.empty-title {
  font-size: var(--font-size-headline);
  font-weight: 600;
  color: var(--color-text-primary);
  margin: 0;
}

.detail-note { justify-self: start; }

.sr-only {
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

.liquid-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: var(--space-2);
  padding: var(--space-3) var(--space-5);
  font-size: var(--font-size-callout);
  font-weight: 600;
  border-radius: var(--radius-pill);
  border: 1px solid;
  min-height: 44px;
  cursor: pointer;
  transition: transform var(--duration-fast) var(--ease-spring), box-shadow var(--duration-fast) var(--ease-smooth);
}

.liquid-btn:active { transform: scale(0.96); }
.liquid-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.liquid-btn:focus-visible { outline: 2px solid var(--color-brand); outline-offset: 2px; }

.liquid-btn--primary {
  background: linear-gradient(135deg, rgba(0, 113, 227, 0.96) 0%, rgba(0, 113, 227, 0.86) 100%);
  border-color: rgba(255, 255, 255, 0.82);
  color: white;
  box-shadow: 0 4px 14px rgba(0, 113, 227, 0.28), var(--glass-rim);
}

.liquid-btn--secondary {
  background: var(--glass-floating-bg);
  border-color: var(--glass-border);
  color: var(--color-text-primary);
  box-shadow: 0 3px 10px rgba(18, 48, 82, 0.08), var(--glass-rim);
}

.liquid-btn--sm {
  padding: var(--space-2) var(--space-4);
  font-size: var(--font-size-caption);
  min-height: 36px;
}

@media (max-width: 767px) {
  .volunteer-detail {
    padding: var(--space-5) var(--space-4) var(--space-9);
    gap: var(--space-4);
  }

  .page-header,
  .detail-card,
  .roster { padding: var(--space-5); }

  .roster__head { align-items: stretch; }
  .search-bar { width: 100%; }

  .detail-info-row { grid-template-columns: min(4.5rem, 32%) minmax(0, 1fr); gap: var(--space-2); }

  .roster-table th,
  .roster-table td { padding: var(--space-3) var(--space-1); font-size: 0.875rem; }
  .roster-table thead th { font-size: 0.75rem; }
}

@media (prefers-reduced-motion: reduce) {
  .skeleton-line { animation: none; }
  .back-btn:active,
  .liquid-btn:active { transform: none; }
}
</style>
