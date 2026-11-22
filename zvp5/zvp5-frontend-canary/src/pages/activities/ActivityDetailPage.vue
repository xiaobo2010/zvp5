<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { fetchActivity, fetchActivityMembers, fetchUserBrief } from '@/shared/api/domain'
import {
  updateActivityStatus,
  updateMemberRecord,
  updateMemberStatus
} from '@/shared/api/manage'
import type { Activity, ActivityMemberRow, MemberStatus, TimeMode } from '@/types/domain'
import { ApiError } from '@/shared/http/errors'
import { formatDate, formatDateTime, formatHours } from '@/shared/lib/format'
import { categoryOf, categoryTone } from '@/shared/lib/category'
import CategoryIcon from '@/shared/ui/CategoryIcon.vue'
import { useSessionStore } from '@/shared/stores/session'
import { useUiStore } from '@/shared/stores/ui'
import ZBadge from '@/shared/ui/ZBadge.vue'
import ZButton from '@/shared/ui/ZButton.vue'
import ZCard from '@/shared/ui/ZCard.vue'
import ZSkeleton from '@/shared/ui/ZSkeleton.vue'
import ZEmpty from '@/shared/ui/ZEmpty.vue'
import ZAlert from '@/shared/ui/ZAlert.vue'
import ZIcon from '@/shared/ui/ZIcon.vue'
import ZModal from '@/shared/ui/ZModal.vue'
import ZField from '@/shared/ui/ZField.vue'
import UserLink from '@/shared/ui/UserLink.vue'
import ZSegmented from '@/shared/ui/ZSegmented.vue'
import AdaptivePagination from '@/shared/ui/pagination/AdaptivePagination.vue'
import { useAdaptivePagination } from '@/shared/composables/useAdaptivePagination'
import { loadStudentIndex, type StudentRecord } from '@/shared/lib/studentIndex'

const { t, locale } = useI18n()
const route = useRoute()
const session = useSessionStore()
const ui = useUiStore()

const activity = ref<Activity | null>(null)
const members = ref<ActivityMemberRow[]>([])
const memberTotal = ref(0)
const memberPage = ref(1)
const loading = ref(true)
const memberLoading = ref(false)
const error = ref('')
const memberQuery = ref('')
const memberSort = ref<'' | 'business_id' | 'duration'>('duration')
const memberAsc = ref(true)
let memberSearchTimer = 0
let memberRequest = 0

const sortOptions = [
  { field: 'business_id' as const, label: locale.value === 'zh-CN' ? '账号' : 'ID' },
  { field: 'duration' as const, label: locale.value === 'zh-CN' ? '时长' : 'Hours' }
]

function sortState(field: 'business_id' | 'duration') {
  const active = memberSort.value === field
  const zh = locale.value === 'zh-CN'
  const hint = active
    ? memberAsc.value
      ? zh
        ? '当前升序，点击切换降序'
        : 'Ascending, click for descending'
      : zh
        ? '当前降序，点击恢复原始顺序'
        : 'Descending, click to clear'
    : zh
      ? '点击按此列升序排序'
      : 'Click to sort ascending'
  return {
    active,
    order: active ? (memberAsc.value ? 'asc' : 'desc') : '',
    title: hint,
    aria: active
      ? memberAsc.value
        ? 'ascending'
        : 'descending'
      : 'none'
  }
}

const {
  size: memberSize,
  pageSizeOptions: memberSizeOptions,
  setPageSize: setMemberPageSize
} = useAdaptivePagination({
  listId: 'activity-members',
  allowAuto: false,
  allowColumns: false,
  defaultPageSize: 20,
  min: 5,
  max: 100
})

const id = computed(() => String(route.params.id ?? ''))

const names = ref<Record<string, string>>({})
const isOid = (value: string) => /^[0-9a-f]{24}$/i.test(value)
const personName = (value: string) => (isOid(value) ? names.value[value] || '' : value)

const meta = computed(() => {
  if (!activity.value) return []
  const { appointee, approver } = activity.value
  return [
    { label: t('activities.date'), value: formatDate(activity.value.date), icon: 'clock' },
    { label: t('activities.place'), value: activity.value.place || '—', icon: 'pin' },
    {
      label: t('activities.organizer'),
      value: personName(appointee) || '—',
      icon: 'user',
      oid: isOid(appointee) ? appointee : ''
    },
    {
      label: locale.value === 'zh-CN' ? '审核方' : 'Approver',
      value:
        approver === 'authority'
          ? locale.value === 'zh-CN'
            ? '学校团委'
            : 'School Youth League'
          : personName(approver) || '—',
      icon: 'shield',
      oid: approver !== 'authority' && isOid(approver) ? approver : ''
    }
  ]
})

function resolveNames(activity: Activity) {
  const oids = [activity.appointee, activity.approver].filter(isOid)
  void Promise.allSettled(
    oids.map(async (oid) => {
      const user = await fetchUserBrief(oid)
      names.value = { ...names.value, [oid]: user.name }
    })
  )
}

const canManage = computed(() => session.isManager)
const activitySaving = ref(false)
const memberSaving = ref<string | null>(null)

const editOpen = ref(false)
const editTarget = ref<ActivityMemberRow | null>(null)
const editDuration = ref('')
const editMode = ref<TimeMode>('on-campus')
const editError = ref('')

const modeOptions = computed(() => [
  { value: 'on-campus', label: t('activities.type.on-campus'), tone: 'campus' as const },
  { value: 'off-campus', label: t('activities.type.off-campus'), tone: 'outdoor' as const },
  { value: 'social-practice', label: t('activities.type.social-practice'), tone: 'social' as const }
])

function report(cause: unknown) {
  if (cause instanceof ApiError) {
    if (cause.status === 409) {
      ui.notify(t('manage.conflict'), 'neutral')
      void load()
      return
    }
    if (cause.status === 403) {
      ui.notify(t('manage.reviewerOnly'), 'danger')
      return
    }
    ui.notify(cause.message, 'danger')
    return
  }
  ui.notify(t('common.error'), 'danger')
}

async function reviewActivity(next: MemberStatus) {
  if (!activity.value || activitySaving.value) return
  const previous = activity.value.status
  activitySaving.value = true
  try {
    await updateActivityStatus(activity.value._id, next)
    activity.value.status = next
    ui.notify(t('manage.done'), 'success')
  } catch (cause) {
    activity.value.status = previous
    report(cause)
  } finally {
    activitySaving.value = false
  }
}

async function reviewMember(member: ActivityMemberRow, next: MemberStatus) {
  if (memberSaving.value || member.status === next) return
  const previous = member.status
  memberSaving.value = member._id
  try {
    await updateMemberStatus(id.value, member._id, next)
    member.status = next
    ui.notify(t('manage.done'), 'success')
  } catch (cause) {
    member.status = previous
    report(cause)
  } finally {
    memberSaving.value = null
  }
}

function openEdit(member: ActivityMemberRow) {
  editTarget.value = member
  editDuration.value = String(member.duration)
  editMode.value = member.mode
  editError.value = ''
  editOpen.value = true
}

async function saveRecord() {
  const target = editTarget.value
  if (!target) return
  const duration = Number(editDuration.value)
  if (!Number.isFinite(duration) || duration <= 0) {
    editError.value = locale.value === 'zh-CN' ? '时长必须大于 0' : 'Duration must be greater than 0'
    return
  }
  memberSaving.value = target._id
  try {
    await updateMemberRecord(id.value, target._id, { duration, mode: editMode.value })
    target.duration = duration
    target.mode = editMode.value
    editOpen.value = false
    ui.notify(t('manage.done'), 'success')
  } catch (cause) {
    report(cause)
  } finally {
    memberSaving.value = null
  }
}

async function loadMembers() {
  if (!id.value) return
  memberLoading.value = true
  // 连续输入/翻页时只采用最后一次请求的结果，避免旧响应覆盖新结果。
  const ticket = ++memberRequest
  try {
    const result = await fetchActivityMembers(
      id.value,
      memberPage.value,
      memberSize.value,
      memberQuery.value.trim(),
      memberSort.value,
      memberAsc.value
    )
    if (ticket !== memberRequest) return
    members.value = result.members
    memberTotal.value = result.total
    const maxPage = Math.max(1, Math.ceil(result.total / memberSize.value))
    if (memberPage.value > maxPage) memberPage.value = maxPage
  } catch {
    if (ticket !== memberRequest) return
    members.value = []
    memberTotal.value = 0
  } finally {
    if (ticket === memberRequest) memberLoading.value = false
  }
}

/**
 * 姓名旁的班级 / 学号：优先用列表内嵌的用户摘要（class_id、id），
 * 缺失时回退到本地学生名单缓存。没有班级也没有学号时不占位。
 */
const studentIndex = ref<Map<string, StudentRecord>>(new Map())

function gradeOf(studentId: string) {
  const grade = studentId.slice(0, 4)
  return /^\d{4}$/.test(grade) ? Number(grade.slice(2)) : 0
}

function memberInfo(member: ActivityMemberRow) {
  const cached = studentIndex.value.get(member.member)
  const studentId = member.user?.id || cached?.studentId || ''
  const raw = member.user?.class_id || cached?.classId || ''
  const className = (raw ? cached?.className : '') || ''
  const grade = gradeOf(studentId)
  const klass = className
    ? grade
      ? `${grade} 年级 ${className}`
      : className
    : grade
      ? `${grade} 年级${raw && !className ? ` ${raw}` : ''}`
      : raw
  if (!klass && !studentId) return null
  return { klass: klass || '—', studentId }
}

function loadMemberIndex() {
  void loadStudentIndex()
    .then((state) => {
      const index = new Map<string, StudentRecord>()
      for (const record of state.students) {
        if (record.oid) index.set(record.oid, record)
      }
      studentIndex.value = index
    })
    .catch(() => undefined)
}

function resetMemberPage() {
  if (memberPage.value === 1) {
    void loadMembers()
    return
  }
  memberPage.value = 1
}

watch(memberQuery, () => {
  window.clearTimeout(memberSearchTimer)
  memberSearchTimer = window.setTimeout(resetMemberPage, 260)
})

function toggleMemberSort(field: 'business_id' | 'duration') {
  if (memberSort.value !== field) {
    memberSort.value = field
    memberAsc.value = true
  } else if (memberAsc.value) {
    memberAsc.value = false
  } else {
    // 第三次点击：清除排序，回到后端原始顺序。
    memberSort.value = ''
    memberAsc.value = true
  }
  resetMemberPage()
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const result = await fetchActivity(id.value)
    activity.value = result.activity
    memberTotal.value = result.members_count
    resolveNames(result.activity)
    // 成员列表自己有骨架屏，不参与首屏等待（远程 ADB 一次分页查询可达数秒）。
    void loadMembers()
  } catch (cause) {
    activity.value = null
    error.value = cause instanceof ApiError ? cause.message : t('common.error')
  } finally {
    loading.value = false
  }
}

watch(memberPage, () => void loadMembers())
watch(memberSize, () => {
  if (memberPage.value !== 1) {
    memberPage.value = 1
    return
  }
  void loadMembers()
})
onMounted(() => {
  void load()
  if (canManage.value) loadMemberIndex()
})
</script>

<template>
  <div class="page">
    <RouterLink to="/activities" class="back">
      <ZIcon name="chevron-left" :size="16" />
      {{ t('activities.backToList') }}
    </RouterLink>

    <div v-if="loading" class="detail">
      <ZSkeleton width="42%" height="2.4rem" />
      <div style="height: 1rem" />
      <ZSkeleton width="70%" height="0.9rem" />
      <div style="height: 2rem" />
      <ZSkeleton height="8rem" />
    </div>

    <ZAlert v-else-if="error" tone="danger" :title="t('common.error')" :description="error">
      <div class="retry">
        <ZButton size="sm" icon="refresh" @click="load">{{ t('common.retry') }}</ZButton>
      </div>
    </ZAlert>

    <template v-else-if="activity">
      <header class="hero" :data-category="categoryOf(activity.type)">
        <div class="hero-badges">
          <CategoryIcon :type="activity.type" />
          <ZBadge :tone="categoryTone(activity.type)">{{ t(`activities.type.${activity.type}`) }}</ZBadge>
          <ZBadge
            :tone="activity.status === 'effective' ? 'success' : activity.status === 'pending' ? 'warning' : 'danger'"
          >
            {{ t(`activities.status.${activity.status}`) }}
          </ZBadge>
        </div>
        <h1 class="title">{{ activity.name }}</h1>
        <p v-if="activity.description" class="description">{{ activity.description }}</p>
        <dl class="meta">
          <div v-for="item in meta" :key="item.label" class="meta-item">
            <dt><ZIcon :name="item.icon" :size="14" />{{ item.label }}</dt>
            <dd>
              <UserLink v-if="item.oid" :oid="item.oid" :name="item.value" :size="24" />
              <template v-else>{{ item.value }}</template>
            </dd>
          </div>
        </dl>

        <div v-if="canManage" class="review-bar">
          <span class="review-label">{{ locale === 'zh-CN' ? '审核活动' : 'Review' }}</span>
          <div class="status-switch">
            <select
              class="status-select activity-status"
              :value="activity.status"
              :data-tone="activity.status === 'effective' ? 'success' : activity.status === 'pending' ? 'warning' : 'danger'"
              :disabled="activitySaving"
              :aria-label="locale === 'zh-CN' ? '修改活动状态' : 'Change activity status'"
              @change="reviewActivity(($event.target as HTMLSelectElement).value as MemberStatus)"
            >
              <option value="pending">{{ t('activities.status.pending') }}</option>
              <option value="effective">{{ t('activities.status.effective') }}</option>
              <option value="refused">{{ t('activities.status.refused') }}</option>
            </select>
            <ZIcon name="chevron-down" :size="12" class="status-caret" />
          </div>
        </div>
      </header>

      <section class="members" aria-labelledby="members-title">
        <div class="section-head">
          <h2 id="members-title" class="section-title">{{ t('activities.memberList') }}</h2>
          <span class="section-count num">{{ memberTotal }} {{ t('activities.members') }}</span>
          <div class="member-tools">
            <div class="member-search" :data-filled="memberQuery ? '' : undefined">
              <ZIcon name="search" :size="14" />
              <input
                v-model="memberQuery"
                type="search"
                :placeholder="locale === 'zh-CN' ? '搜索姓名 / 账号' : 'Search name / ID'"
                :aria-label="locale === 'zh-CN' ? '按姓名或账号搜索义工' : 'Search members'"
                @keydown.esc="memberQuery = ''"
              />
            </div>
            <div class="member-sort" role="group" :aria-label="locale === 'zh-CN' ? '排序' : 'Sort'">
              <button
                v-for="item in sortOptions"
                :key="item.field"
                type="button"
                class="sort-btn"
                :data-active="sortState(item.field).active || undefined"
                :data-order="sortState(item.field).order || undefined"
                :aria-pressed="sortState(item.field).active"
                :aria-label="`${item.label} ${sortState(item.field).aria}`"
                :title="sortState(item.field).title"
                @click="toggleMemberSort(item.field)"
              >
                {{ item.label }}
                <ZIcon
                  name="chevron-down"
                  :size="12"
                  class="sort-arrow"
                  :data-order="sortState(item.field).order || 'idle'"
                />
              </button>
            </div>
          </div>
        </div>

        <ZCard padding="none">
          <div v-if="memberLoading" class="rows">
            <div v-for="index in 5" :key="index" class="row">
              <ZSkeleton circle width="32px" height="32px" />
              <ZSkeleton width="35%" height="0.85rem" />
            </div>
          </div>

          <ul v-else-if="members.length" class="rows">
            <li
              v-for="member in members"
              :key="member._id"
              class="row"
              :data-category="categoryOf(member.mode)"
            >
              <div class="member-main">
                <UserLink
                  :oid="member.member"
                  :name="member.member_name"
                  :size="32"
                  :meta="t(`activities.type.${member.mode}`)"
                  class="member-name"
                />
                <span v-if="memberInfo(member)" class="member-info num">
                  <span class="member-class">{{ memberInfo(member)?.klass }}</span>
                  <span v-if="memberInfo(member)?.studentId" class="member-sid">{{ memberInfo(member)?.studentId }}</span>
                </span>
              </div>
              <span class="duration num">{{ formatHours(member.duration) }}h</span>
              <div v-if="canManage" class="status-switch">
                <select
                  class="status-select"
                  :data-tone="member.status === 'effective' ? 'success' : member.status === 'pending' ? 'warning' : 'danger'"
                  :value="member.status"
                  :disabled="memberSaving === member._id"
                  :aria-label="locale === 'zh-CN' ? '修改义工状态' : 'Change member status'"
                  @change="reviewMember(member, ($event.target as HTMLSelectElement).value as MemberStatus)"
                >
                  <option value="pending">{{ t('activities.status.pending') }}</option>
                  <option value="effective">{{ t('activities.status.effective') }}</option>
                  <option value="refused">{{ t('activities.status.refused') }}</option>
                </select>
                <ZIcon name="chevron-down" :size="12" class="status-caret" />
              </div>
              <ZBadge
                v-else
                :tone="member.status === 'effective' ? 'success' : member.status === 'pending' ? 'warning' : 'danger'"
              >
                {{ t(`activities.status.${member.status}`) }}
              </ZBadge>
              <div v-if="canManage" class="member-actions">
                <button
                  type="button"
                  class="mini"
                  :aria-label="locale === 'zh-CN' ? '修改时长' : 'Edit hours'"
                  @click="openEdit(member)"
                >
                  <ZIcon name="pencil" :size="14" />
                </button>
              </div>
            </li>
          </ul>

          <ZEmpty v-else compact icon="users" :title="t('activities.noMembers')" />
        </ZCard>

        <AdaptivePagination
          :page="memberPage"
          :page-size="memberSize"
          :total="memberTotal"
          :auto="false"
          :allow-auto="false"
          :page-size-options="memberSizeOptions"
          :disabled="memberLoading"
          @update:page="memberPage = $event"
          @update:page-size="setMemberPageSize"
        />
      </section>

      <footer class="stamp">
        <span>{{ locale === 'zh-CN' ? '创建于' : 'Created' }} {{ formatDateTime(activity.createdAt) }}</span>
        <span class="dot">·</span>
        <span>{{ locale === 'zh-CN' ? '更新于' : 'Updated' }} {{ formatDateTime(activity.updatedAt) }}</span>
      </footer>
    </template>

    <ZModal
      v-model="editOpen"
      size="sm"
      :title="locale === 'zh-CN' ? '修改时长' : 'Edit hours'"
      :description="editTarget?.member_name"
    >
      <div class="edit-form">
        <ZField
          v-model="editDuration"
          :label="locale === 'zh-CN' ? '时长（小时）' : 'Hours'"
          inputmode="decimal"
          :error="editError"
        />
        <div class="edit-mode">
          <span class="edit-label">{{ locale === 'zh-CN' ? '时长类型' : 'Mode' }}</span>
          <ZSegmented v-model="editMode" :options="modeOptions" size="sm" :aria-label="t('activities.title')" />
        </div>
      </div>
      <template #footer>
        <ZButton variant="ghost" size="sm" @click="editOpen = false">{{ t('common.cancel') }}</ZButton>
        <ZButton
          variant="primary"
          size="sm"
          :disabled="memberSaving === editTarget?._id"
          @click="saveRecord"
        >
          {{ t('common.save') }}
        </ZButton>
      </template>
    </ZModal>
  </div>
</template>

<style scoped>
.page {
  display: flex;
  flex-direction: column;
  gap: var(--space-8);
  max-width: 960px;
  margin-inline: auto;
}

.back {
  display: inline-flex;
  align-items: center;
  gap: 0.25rem;
  align-self: flex-start;
  font-size: var(--text-sm);
  color: var(--ink-muted);
  text-decoration: none;
}

.back:hover {
  color: var(--accent-ink);
}

.detail {
  display: flex;
  flex-direction: column;
}

.hero[data-category='campus'] {
  --hero-category: var(--category-campus);
  --hero-border: var(--category-campus-border);
}

.hero[data-category='outdoor'] {
  --hero-category: var(--category-outdoor);
  --hero-border: var(--category-outdoor-border);
}

.hero[data-category='social'] {
  --hero-category: var(--category-social);
  --hero-border: var(--category-social-border);
}

.hero {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  padding-bottom: var(--space-6);
  border-bottom: 1px solid var(--hero-border, var(--line));
}

.hero-badges {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

.title {
  font-size: clamp(1.9rem, 1.4rem + 2vw, 3rem);
  font-weight: 500;
  line-height: 1.14;
}

.description {
  max-width: 68ch;
  color: var(--ink-soft);
  line-height: 1.75;
  white-space: pre-line;
}

.meta {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: var(--space-4);
  margin: var(--space-2) 0 0;
}

.meta-item {
  display: flex;
  flex-direction: column;
  gap: 0.3rem;
  padding-top: var(--space-3);
  border-top: 1px solid color-mix(in srgb, var(--line) 60%, transparent);
}

.meta-item dt {
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
  font-size: var(--text-2xs);
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--ink-faint);
}

.meta-item dd {
  margin: 0;
  font-size: var(--text-sm);
  font-weight: 550;
  color: var(--ink);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.section-head {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--space-3) var(--space-4);
  margin-bottom: var(--space-4);
}

.section-title {
  font-size: var(--text-xl);
  font-weight: 500;
}

.section-count {
  font-size: var(--text-xs);
  color: var(--ink-faint);
  margin-right: auto;
}

.member-tools {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-wrap: wrap;
}

.member-search {
  display: inline-flex;
  align-items: center;
  gap: 0.45rem;
  height: 2rem;
  padding-inline: 0.7rem;
  min-width: 12rem;
  border-radius: var(--radius-pill);
  color: var(--ink-faint);
  background: var(--glass-hi);
  border: 1px solid color-mix(in srgb, var(--line) 60%, transparent);
  transition: border-color var(--dur-fast) var(--ease-out),
    box-shadow var(--dur-fast) var(--ease-out);
}

.member-search:focus-within {
  border-color: color-mix(in srgb, var(--accent) 45%, transparent);
  box-shadow: 0 0 0 2px color-mix(in srgb, var(--accent) 22%, transparent);
}

/* 焦点环只画在外层胶囊上：输入框自身不再叠加全局 focus-visible 阴影，
   避免出现内外两圈强调色。 */
.member-search input,
.member-search input:focus,
.member-search input:focus-visible {
  outline: none;
  box-shadow: none;
}

.member-search input {
  flex: 1;
  min-width: 0;
  height: 100%;
  border: 0;
  outline: 0;
  background: transparent;
  color: var(--ink);
  font: inherit;
  font-size: var(--text-xs);
}

.member-search input::placeholder {
  color: var(--ink-faint);
}

.member-search input::-webkit-search-cancel-button {
  cursor: pointer;
  opacity: 0.55;
}

.member-sort {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  padding: 2px;
  border-radius: var(--radius-pill);
  background: var(--glass-hi);
  border: 1px solid color-mix(in srgb, var(--line) 60%, transparent);
}

.sort-btn {
  display: inline-flex;
  align-items: center;
  gap: 0.2rem;
  height: 1.6rem;
  padding-inline: 0.6rem;
  border: 0;
  border-radius: var(--radius-pill);
  background: transparent;
  color: var(--ink-muted);
  font: inherit;
  font-size: var(--text-2xs);
  cursor: pointer;
  transition: background var(--dur-fast) var(--ease-out), color var(--dur-fast) var(--ease-out);
}

.sort-btn:hover {
  color: var(--ink);
  background: color-mix(in srgb, var(--accent) 10%, transparent);
}

.sort-btn[data-active] {
  color: var(--accent-strong, var(--accent));
  background: color-mix(in srgb, var(--accent) 16%, transparent);
  font-weight: 600;
}

.sort-arrow {
  opacity: 0.35;
  transition: transform var(--dur-fast) var(--ease-out), opacity var(--dur-fast) var(--ease-out);
}

.sort-btn[data-active] .sort-arrow {
  opacity: 1;
}

.sort-arrow[data-order='asc'] {
  transform: rotate(180deg);
}

.status-switch {
  position: relative;
  display: inline-flex;
  align-items: center;
  flex: none;
}

.review-bar {
  position: relative;
  display: flex;
  align-items: center;
  gap: 0.55rem;
  margin-top: 0.9rem;
}

.review-label {
  font-size: var(--text-xs);
  color: var(--ink-muted);
}

.activity-status {
  height: 1.9rem;
  font-size: var(--text-xs);
}

.status-select {
  appearance: none;
  height: 1.65rem;
  padding: 0 1.5rem 0 0.6rem;
  border-radius: var(--radius-pill);
  border: 1px solid currentColor;
  font: inherit;
  font-size: var(--text-2xs);
  font-weight: 600;
  cursor: pointer;
  color: var(--ink-muted);
  background: color-mix(in srgb, currentColor 10%, transparent);
  transition: background var(--dur-fast) var(--ease-out);
}

.status-select[data-tone='success'] {
  color: var(--c-success);
  background: color-mix(in srgb, var(--c-success) 12%, transparent);
}

.status-select[data-tone='warning'] {
  color: var(--c-warning);
  background: color-mix(in srgb, var(--c-warning) 12%, transparent);
}

.status-select[data-tone='danger'] {
  color: var(--c-danger);
  background: color-mix(in srgb, var(--c-danger) 12%, transparent);
}

.status-select:hover:not(:disabled) {
  background: color-mix(in srgb, currentColor 18%, transparent);
}

.status-select:focus-visible {
  outline: 2px solid color-mix(in srgb, var(--accent) 60%, transparent);
  outline-offset: 2px;
}

.status-select:disabled {
  opacity: 0.5;
  cursor: progress;
}

.status-select option {
  color: var(--ink);
  background: var(--surface, var(--bg));
}

.status-caret {
  position: absolute;
  right: 0.5rem;
  pointer-events: none;
  opacity: 0.7;
}

.rows {
  list-style: none;
  margin: 0;
  padding: 0;
}

.row[data-category='campus'] {
  --member-color: var(--category-campus);
}

.row[data-category='outdoor'] {
  --member-color: var(--category-outdoor);
}

.row[data-category='social'] {
  --member-color: var(--category-social);
}

.row {
  display: flex;
  align-items: center;
  gap: var(--space-4);
  padding: var(--space-3) var(--space-4);
  border-bottom: 1px solid color-mix(in srgb, var(--line) 55%, transparent);
}

.row:last-child {
  border-bottom: 0;
}

.member-main {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  flex: 1;
  min-width: 0;
}

.member-info {
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
  flex: none;
  padding: 0.15rem 0.5rem;
  border-radius: var(--radius-pill);
  border: 1px solid color-mix(in srgb, var(--line) 55%, transparent);
  background: color-mix(in srgb, var(--accent) 6%, transparent);
  color: var(--ink-muted);
  font-size: var(--text-2xs);
  line-height: 1.5;
  white-space: nowrap;
}

.member-class {
  color: var(--ink-muted);
}

.member-sid {
  color: var(--ink-faint);
  letter-spacing: 0.02em;
}

.member-name {
  display: flex;
  align-items: center;
  gap: 0.7rem;
  min-width: 0;
  flex: 0 1 auto;
}

.avatar {
  display: grid;
  place-items: center;
  width: 32px;
  height: 32px;
  flex: none;
  border-radius: 50%;
  font-size: var(--text-xs);
  font-weight: 600;
  color: #fffdf8;
  background: linear-gradient(140deg, #c58a33, #8c5a12);
}

.member-text {
  display: flex;
  flex-direction: column;
  min-width: 0;
  line-height: 1.3;
}

.member-text strong {
  font-size: var(--text-sm);
  font-weight: 550;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.member-text small {
  font-size: var(--text-2xs);
  color: var(--ink-faint);
}

.member-name :deep(small) {
  color: var(--member-color, var(--ink-faint));
}

.duration {
  width: 4rem;
  text-align: right;
  font-size: var(--text-sm);
  font-weight: 600;
  color: var(--member-color, var(--ink-soft));
}

@media (max-width: 520px) {
  .row {
    gap: var(--space-2);
    padding-inline: var(--space-3);
  }

  .duration {
    width: auto;
  }

  .member-tools,
  .member-search {
    width: 100%;
  }

  .member-search {
    min-width: 0;
  }
}
</style>
