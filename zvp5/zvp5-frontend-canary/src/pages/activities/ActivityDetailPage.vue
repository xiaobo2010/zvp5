<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { fetchActivity, fetchActivityMembers } from '@/shared/api/domain'
import type { Activity, ActivityMemberRow } from '@/types/domain'
import { ApiError } from '@/shared/http/errors'
import { formatDate, formatDateTime, formatHours } from '@/shared/lib/format'
import ZBadge from '@/shared/ui/ZBadge.vue'
import ZButton from '@/shared/ui/ZButton.vue'
import ZCard from '@/shared/ui/ZCard.vue'
import ZSkeleton from '@/shared/ui/ZSkeleton.vue'
import ZEmpty from '@/shared/ui/ZEmpty.vue'
import ZAlert from '@/shared/ui/ZAlert.vue'
import ZIcon from '@/shared/ui/ZIcon.vue'

const { t, locale } = useI18n()
const route = useRoute()

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

const meta = computed(() => {
  if (!activity.value) return []
  return [
    { label: t('activities.date'), value: formatDate(activity.value.date), icon: 'clock' },
    { label: t('activities.place'), value: activity.value.place || '—', icon: 'pin' },
    { label: t('activities.organizer'), value: activity.value.appointee || '—', icon: 'user' },
    {
      label: locale.value === 'zh-CN' ? '审核方' : 'Approver',
      value:
        activity.value.approver === 'authority'
          ? locale.value === 'zh-CN'
            ? '学校团委'
            : 'School Youth League'
          : activity.value.approver || '—',
      icon: 'shield'
    }
  ]
})

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
    await loadMembers()
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
        <p class="description">{{ activity.description || '—' }}</p>
        <dl class="meta">
          <div v-for="item in meta" :key="item.label" class="meta-item">
            <dt><ZIcon :name="item.icon" :size="14" />{{ item.label }}</dt>
            <dd>{{ item.value }}</dd>
          </div>
        </dl>
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
                  <strong>{{ member.member_name || member.member }}</strong>
                  <small>{{ t(`activities.type.${member.mode}`) }}</small>
                </span>
              </span>
              <span class="duration num">{{ formatHours(member.duration) }}h</span>
              <ZBadge
                :tone="member.status === 'effective' ? 'success' : member.status === 'pending' ? 'warning' : 'danger'"
              >
                {{ t(`activities.status.${member.status}`) }}
              </ZBadge>
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
