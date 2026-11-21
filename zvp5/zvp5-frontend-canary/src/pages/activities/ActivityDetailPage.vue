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
import type { Activity, ActivityMemberRow, TimeMode } from '@/types/domain'
import { ApiError } from '@/shared/http/errors'
import { formatDate, formatDateTime, formatHours } from '@/shared/lib/format'
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
import ZSegmented from '@/shared/ui/ZSegmented.vue'

const { t, locale } = useI18n()
const route = useRoute()
const session = useSessionStore()
const ui = useUiStore()

const activity = ref<Activity | null>(null)
const members = ref<ActivityMemberRow[]>([])
const memberTotal = ref(0)
const memberPage = ref(1)
const memberSize = 20
const loading = ref(true)
const memberLoading = ref(false)
const error = ref('')

const id = computed(() => String(route.params.id ?? ''))
const totalPages = computed(() => Math.max(1, Math.ceil(memberTotal.value / memberSize)))

const names = ref<Record<string, string>>({})
const isOid = (value: string) => /^[0-9a-f]{24}$/i.test(value)
const personName = (value: string) => (isOid(value) ? names.value[value] || '' : value)

const meta = computed(() => {
  if (!activity.value) return []
  const { appointee, approver } = activity.value
  return [
    { label: t('activities.date'), value: formatDate(activity.value.date), icon: 'clock' },
    { label: t('activities.place'), value: activity.value.place || '—', icon: 'pin' },
    { label: t('activities.organizer'), value: personName(appointee) || '—', icon: 'user' },
    {
      label: locale.value === 'zh-CN' ? '审核方' : 'Approver',
      value:
        approver === 'authority'
          ? locale.value === 'zh-CN'
            ? '学校团委'
            : 'School Youth League'
          : personName(approver) || '—',
      icon: 'shield'
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
  { value: 'on-campus', label: t('activities.type.on-campus') },
  { value: 'off-campus', label: t('activities.type.off-campus') },
  { value: 'social-practice', label: t('activities.type.social-practice') }
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

async function reviewActivity(next: 'effective' | 'refused') {
  if (!activity.value || activitySaving.value) return
  activitySaving.value = true
  try {
    await updateActivityStatus(activity.value._id, next)
    activity.value.status = next
    ui.notify(t('manage.done'), 'success')
  } catch (cause) {
    report(cause)
  } finally {
    activitySaving.value = false
  }
}

async function reviewMember(member: ActivityMemberRow, next: 'effective' | 'refused') {
  if (memberSaving.value) return
  memberSaving.value = member._id
  try {
    await updateMemberStatus(id.value, member._id, next)
    member.status = next
    ui.notify(t('manage.done'), 'success')
  } catch (cause) {
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
  try {
    const result = await fetchActivityMembers(id.value, memberPage.value, memberSize)
    members.value = result.members
    memberTotal.value = result.total
  } catch {
    members.value = []
    memberTotal.value = 0
  } finally {
    memberLoading.value = false
  }
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
onMounted(load)
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
      <header class="hero">
        <div class="hero-badges">
          <ZBadge tone="accent">{{ t(`activities.type.${activity.type}`) }}</ZBadge>
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
            <dd>{{ item.value }}</dd>
          </div>
        </dl>

        <div v-if="canManage" class="review-bar">
          <span class="review-label">{{ locale === 'zh-CN' ? '审核活动' : 'Review' }}</span>
          <ZButton
            size="sm"
            variant="secondary"
            :disabled="activitySaving"
            @click="reviewActivity('refused')"
          >
            {{ t('manage.refuse') }}
          </ZButton>
          <ZButton
            size="sm"
            variant="primary"
            :disabled="activitySaving"
            @click="reviewActivity('effective')"
          >
            {{ t('manage.approve') }}
          </ZButton>
        </div>
      </header>

      <section class="members" aria-labelledby="members-title">
        <div class="section-head">
          <h2 id="members-title" class="section-title">{{ t('activities.memberList') }}</h2>
          <span class="section-count num">{{ memberTotal }} {{ t('activities.members') }}</span>
        </div>

        <ZCard padding="none">
          <div v-if="memberLoading" class="rows">
            <div v-for="index in 5" :key="index" class="row">
              <ZSkeleton circle width="32px" height="32px" />
              <ZSkeleton width="35%" height="0.85rem" />
            </div>
          </div>

          <ul v-else-if="members.length" class="rows">
            <li v-for="member in members" :key="member._id" class="row">
              <span class="member-name">
                <span class="avatar" aria-hidden="true">{{ Array.from(member.member_name || '·')[0] }}</span>
                <span class="member-text">
                  <strong>{{ member.member_name || '—' }}</strong>
                  <small>{{ t(`activities.type.${member.mode}`) }}</small>
                </span>
              </span>
              <span class="duration num">{{ formatHours(member.duration) }}h</span>
              <ZBadge
                :tone="member.status === 'effective' ? 'success' : member.status === 'pending' ? 'warning' : 'danger'"
              >
                {{ t(`activities.status.${member.status}`) }}
              </ZBadge>
              <div v-if="canManage" class="member-actions">
                <button
                  v-if="member.status !== 'effective'"
                  type="button"
                  class="mini"
                  :disabled="memberSaving === member._id"
                  :aria-label="t('manage.approve')"
                  @click="reviewMember(member, 'effective')"
                >
                  <ZIcon name="check" :size="14" />
                </button>
                <button
                  v-if="member.status === 'pending'"
                  type="button"
                  class="mini"
                  :disabled="memberSaving === member._id"
                  :aria-label="t('manage.refuse')"
                  @click="reviewMember(member, 'refused')"
                >
                  <ZIcon name="x" :size="14" />
                </button>
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

        <nav v-if="totalPages > 1" class="pager" aria-label="分页">
          <ZButton
            variant="ghost"
            size="sm"
            icon="chevron-left"
            :disabled="memberPage <= 1 || memberLoading"
            @click="memberPage -= 1"
          >
            {{ t('common.back') }}
          </ZButton>
          <span class="page-num num">{{ memberPage }} / {{ totalPages }}</span>
          <ZButton
            variant="ghost"
            size="sm"
            icon-right="chevron-right"
            :disabled="memberPage >= totalPages || memberLoading"
            @click="memberPage += 1"
          >
            {{ t('home.viewAll') }}
          </ZButton>
        </nav>
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

.hero {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  padding-bottom: var(--space-6);
  border-bottom: 1px solid var(--line);
}

.hero-badges {
  display: flex;
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
  align-items: baseline;
  justify-content: space-between;
  gap: var(--space-4);
  margin-bottom: var(--space-4);
}

.section-title {
  font-size: var(--text-xl);
  font-weight: 500;
}

.section-count {
  font-size: var(--text-xs);
  color: var(--ink-faint);
}

.rows {
  list-style: none;
  margin: 0;
  padding: 0;
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

.member-name {
  display: flex;
  align-items: center;
  gap: 0.7rem;
  flex: 1;
  min-width: 0;
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

.duration {
  width: 4rem;
  text-align: right;
  font-size: var(--text-sm);
  font-weight: 600;
  color: var(--ink-soft);
}

.pager {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: var(--space-4);
  margin-top: var(--space-4);
}

.review-bar {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  margin-top: var(--space-6);
  padding-top: var(--space-5);
  border-top: 1px solid color-mix(in srgb, var(--line) 55%, transparent);
}

.review-label {
  flex: 1;
  font-size: var(--text-xs);
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--ink-faint);
}

.member-actions {
  display: flex;
  align-items: center;
  gap: 0.3rem;
  flex: none;
}

.mini {
  display: grid;
  place-items: center;
  width: 26px;
  height: 26px;
  border-radius: 50%;
  color: var(--ink-faint);
  border: 1px solid color-mix(in srgb, var(--glass-border) 70%, transparent);
  background:
    linear-gradient(160deg, color-mix(in srgb, var(--glass-hi) 30%, transparent), transparent 55%),
    var(--glass-flat);
  box-shadow: inset 0 1px 0 var(--glass-hi);
  transition:
    background-color var(--dur-fast) var(--ease-out),
    color var(--dur-fast) var(--ease-out),
    transform var(--dur-micro) var(--spring-snappy);
}

.mini:hover {
  background-color: color-mix(in srgb, var(--accent) 16%, transparent);
  color: var(--accent-ink);
  transform: translateY(-1px) scale(1.06);
}

.mini:active {
  transform: scale(0.9);
}

.mini:disabled {
  opacity: 0.45;
}

.edit-form {
  display: flex;
  flex-direction: column;
  gap: var(--space-5);
}

.edit-mode {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.edit-label {
  font-size: var(--text-xs);
  font-weight: 550;
  color: var(--ink-soft);
}

.page-num {
  font-size: var(--text-xs);
  color: var(--ink-muted);
}

.stamp {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-size: var(--text-2xs);
  color: var(--ink-faint);
}

.dot {
  opacity: 0.6;
}

.retry {
  margin-top: var(--space-3);
}

@media (max-width: 820px) {
  .meta {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 520px) {
  .row {
    gap: var(--space-2);
    padding-inline: var(--space-3);
  }

  .duration {
    width: auto;
  }
}
</style>
