<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useSessionStore } from '@/shared/stores/session'
import {
  fetchNotifications,
  fetchNotificationReads,
  fetchUserActivities,
  fetchUserTime,
  fetchUserTimeStatistics
} from '@/shared/api/domain'
import type {
  NotificationItem,
  UserActivityItem,
  UserTime,
  UserTimeStatistics
} from '@/types/domain'
import { greetingKey, formatDate, formatHours, TIME_TARGETS } from '@/shared/lib/format'
import ZButton from '@/shared/ui/ZButton.vue'
import ZCard from '@/shared/ui/ZCard.vue'
import ZAlert from '@/shared/ui/ZAlert.vue'
import ZBadge from '@/shared/ui/ZBadge.vue'
import ZSkeleton from '@/shared/ui/ZSkeleton.vue'
import ZEmpty from '@/shared/ui/ZEmpty.vue'
import ZIcon from '@/shared/ui/ZIcon.vue'
import ZProgress from '@/shared/ui/ZProgress.vue'

const { t, locale } = useI18n()
const session = useSessionStore()

const time = ref<UserTime | null>(null)
const stats = ref<UserTimeStatistics | null>(null)
const activities = ref<UserActivityItem[]>([])
const notifications = ref<NotificationItem[]>([])
const readMap = ref<Record<string, number>>({})
const loading = ref(true)
const failed = ref(false)

const name = computed(() => session.displayName || '—')
const greeting = computed(() => t(greetingKey(new Date().getHours())))
const today = computed(() =>
  new Intl.DateTimeFormat(locale.value, {
    month: 'long',
    day: 'numeric',
    weekday: 'long'
  }).format(new Date())
)

const total = computed<number | null>(() => {
  if (!time.value) return null
  return time.value['on-campus'] + time.value['off-campus'] + time.value['social-practice']
})

const totalTarget = TIME_TARGETS['on-campus'] + TIME_TARGETS['off-campus'] + TIME_TARGETS['social-practice']

const dialPercent = computed(() => {
  if (total.value === null) return 0
  return Math.min(100, (total.value / totalTarget) * 100)
})

const modes = computed(() => [
  {
    key: 'on-campus' as const,
    label: t('home.onCampus'),
    value: time.value ? time.value['on-campus'] : null,
    target: TIME_TARGETS['on-campus'],
    percentile: stats.value?.['on-campus']?.grade ?? null
  },
  {
    key: 'off-campus' as const,
    label: t('home.offCampus'),
    value: time.value ? time.value['off-campus'] : null,
    target: TIME_TARGETS['off-campus'],
    percentile: stats.value?.['off-campus']?.grade ?? null
  },
  {
    key: 'social-practice' as const,
    label: t('home.socialPractice'),
    value: time.value ? time.value['social-practice'] : null,
    target: TIME_TARGETS['social-practice'],
    percentile: stats.value?.['social-practice']?.grade ?? null
  }
])

const unreadCount = computed(
  () => notifications.value.filter((item) => !readMap.value[item.id]).length
)

function percentileLabel(value: number | null): string {
  if (value === null) return ''
  if (value >= 100) return locale.value === 'zh-CN' ? '达到年级基础线' : 'At grade baseline'
  return locale.value === 'zh-CN' ? `超过 ${value}% 同学` : `Ahead of ${value}%`
}

async function load() {
  loading.value = true
  failed.value = false
  const oid = session.profile?._id
  if (!oid) {
    loading.value = false
    return
  }

  const results = await Promise.allSettled([
    fetchUserTime(oid),
    fetchUserTimeStatistics(oid),
    fetchUserActivities(oid, { page: 1, perpage: 5 }),
    fetchNotifications(),
    fetchNotificationReads()
  ])

  if (results[0]?.status === 'fulfilled') time.value = results[0].value
  if (results[1]?.status === 'fulfilled') stats.value = results[1].value
  if (results[2]?.status === 'fulfilled') activities.value = results[2].value.activities.slice(0, 5)
  if (results[3]?.status === 'fulfilled') notifications.value = results[3].value.notifications.slice(0, 3)
  if (results[4]?.status === 'fulfilled') readMap.value = results[4].value

  failed.value = results.every((result) => result.status === 'rejected')
  loading.value = false
}

onMounted(load)
</script>

<template>
  <div class="home">
    <ZAlert
      v-if="session.weakPassword"
      class="weak"
      tone="warning"
      :description="t('login.weakPassword')"
    />

    <section class="hero" aria-labelledby="home-title">
      <div class="hero-copy">
        <p class="eyebrow">{{ today }}</p>
        <h1 id="home-title" class="hero-title">
          {{ greeting }}，<span class="accent">{{ name }}</span>
        </h1>
        <p class="hero-lede">{{ t('home.intro') }}</p>
        <div class="hero-actions">
          <RouterLink to="/activities">
            <ZButton variant="primary" icon-right="arrow-right">{{ t('home.quickActivities') }}</ZButton>
          </RouterLink>
          <RouterLink to="/notifications" class="ghost-link">
            <ZButton variant="ghost" icon="bell">
              {{ t('home.quickNotifications') }}
              <span v-if="unreadCount" class="count">{{ unreadCount }}</span>
            </ZButton>
          </RouterLink>
        </div>
      </div>

      <div class="hero-dial" aria-hidden="true">
        <div
          class="dial-ring"
          :style="{
            background: `conic-gradient(var(--accent) ${dialPercent}%, color-mix(in srgb, var(--ink) 8%, transparent) 0)`
          }"
        >
          <div class="dial-inner">
            <span class="dial-label">{{ t('home.totalTime') }}</span>
            <strong class="dial-value num">{{ formatHours(total) }}</strong>
            <span class="dial-unit">{{ t('common.hours') }}</span>
          </div>
        </div>
      </div>
    </section>

    <section class="block" aria-labelledby="time-title">
      <div class="block-head">
        <h2 id="time-title" class="block-title">{{ t('home.totalTime') }}</h2>
        <span class="block-note">{{ formatHours(total) }} / {{ totalTarget }} {{ t('common.hours') }}</span>
      </div>

      <div v-if="loading" class="grid-3">
        <ZCard v-for="index in 3" :key="index">
          <ZSkeleton width="40%" height="0.8rem" />
          <div style="height: 1rem" />
          <ZSkeleton width="55%" height="2rem" />
          <div style="height: 1rem" />
          <ZSkeleton height="0.5rem" />
        </ZCard>
      </div>

      <div v-else class="grid-3">
        <ZCard v-for="mode in modes" :key="mode.key" class="time-card">
          <div class="time-head">
            <span class="time-label">{{ mode.label }}</span>
            <ZBadge v-if="mode.percentile !== null" tone="neutral" size="sm">
              {{ percentileLabel(mode.percentile) }}
            </ZBadge>
          </div>
          <p class="time-value">
            <strong class="num">{{ formatHours(mode.value) }}</strong>
            <span>{{ t('common.hours') }}</span>
          </p>
          <ZProgress
            :value="mode.value ?? 0"
            :max="mode.target"
            tone="accent"
            :height="6"
            :label="`${t('home.target')} ${mode.target}h`"
            :show-value="mode.value !== null"
          />
        </ZCard>
      </div>
    </section>

    <ZAlert
      v-if="failed"
      tone="danger"
      :title="t('common.error')"
      :description="locale === 'zh-CN' ? '无法读取数据，请检查网络后重试。' : 'Could not load data. Check your connection and retry.'"
    >
      <div class="retry">
        <ZButton size="sm" icon="refresh" @click="load">{{ t('common.retry') }}</ZButton>
      </div>
    </ZAlert>

    <div class="split">
      <section class="block" aria-labelledby="recent-title">
        <div class="block-head">
          <h2 id="recent-title" class="block-title">{{ t('home.recent') }}</h2>
          <RouterLink to="/activities" class="more">
            {{ t('home.viewAll') }}
            <ZIcon name="arrow-right" :size="15" />
          </RouterLink>
        </div>

        <ZCard padding="none">
          <div v-if="loading" class="list">
            <div v-for="index in 4" :key="index" class="skeleton-row">
              <ZSkeleton circle width="34px" height="34px" />
              <ZSkeleton width="60%" height="0.9rem" />
            </div>
          </div>

          <ul v-else-if="activities.length" class="list">
            <li v-for="activity in activities" :key="activity._id" class="activity-row">
              <RouterLink :to="`/activities/${activity._id}`" class="activity-link">
                <span class="date num">{{ formatDate(activity.date).slice(5) }}</span>
                <span class="activity-main">
                  <strong>{{ activity.name }}</strong>
                  <small>
                    {{ t(`activities.type.${activity.type}`) }}
                    <template v-if="activity.place"> · {{ activity.place }}</template>
                  </small>
                </span>
                <ZBadge :tone="activity.status === 'effective' ? 'success' : activity.status === 'pending' ? 'warning' : 'danger'">
                  {{ t(`activities.status.${activity.status}`) }}
                </ZBadge>
                <span class="hours num">{{ formatHours(activity.mine?.duration ?? 0) }}h</span>
              </RouterLink>
            </li>
          </ul>

          <ZEmpty v-else compact icon="compass" :title="t('home.noRecent')" />
        </ZCard>
      </section>

      <section class="block" aria-labelledby="inbox-title">
        <div class="block-head">
          <h2 id="inbox-title" class="block-title">{{ t('nav.notifications') }}</h2>
          <RouterLink to="/notifications" class="more">
            {{ t('home.viewAll') }}
            <ZIcon name="arrow-right" :size="15" />
          </RouterLink>
        </div>

        <ZCard padding="none">
          <div v-if="loading" class="list">
            <div v-for="index in 3" :key="index" class="skeleton-row">
              <ZSkeleton width="70%" height="0.9rem" />
            </div>
          </div>

          <ul v-else-if="notifications.length" class="list">
            <li v-for="item in notifications" :key="item.id" class="notice-row">
              <span class="dot" :class="{ unread: !readMap[item.id] }" aria-hidden="true" />
              <div class="notice-main">
                <strong>{{ item.title }}</strong>
                <small>{{ formatDate(item.updated_at ?? item.created_at) }}</small>
              </div>
            </li>
          </ul>

          <ZEmpty v-else compact icon="bell" :title="t('notifications.empty')" />
        </ZCard>
      </section>
    </div>
  </div>
</template>

<style scoped>
.home {
  display: flex;
  flex-direction: column;
  gap: var(--space-10);
}

.weak {
  margin-bottom: calc(var(--space-2) * -1);
}

.hero {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-10);
  padding: clamp(1.6rem, 1.1rem + 2.4vw, 3rem);
  border-radius: var(--radius-2xl);
  border: 1px solid color-mix(in srgb, var(--accent) 20%, var(--line));
  background:
    radial-gradient(38rem 26rem at 88% 18%, color-mix(in srgb, var(--accent) 20%, transparent), transparent 62%),
    linear-gradient(145deg, color-mix(in srgb, var(--surface) 92%, var(--accent-soft)), var(--surface));
  box-shadow: var(--shadow-md);
  overflow: hidden;
}

.hero::after {
  content: '';
  position: absolute;
  inset: 0;
  pointer-events: none;
  background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='120' height='120'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='2'/%3E%3C/filter%3E%3Crect width='120' height='120' filter='url(%23n)' opacity='0.035'/%3E%3C/svg%3E");
}

.hero-copy {
  position: relative;
  z-index: 1;
  max-width: 36rem;
}

.hero-title {
  margin-top: var(--space-3);
  font-size: var(--text-3xl);
  font-weight: 500;
  line-height: 1.14;
}

.accent {
  color: var(--accent-ink);
}

.hero-lede {
  margin-top: var(--space-3);
  max-width: 38ch;
  color: var(--ink-muted);
  line-height: 1.7;
}

.hero-actions {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-3);
  margin-top: var(--space-6);
}

.ghost-link {
  text-decoration: none;
}

.count {
  display: inline-grid;
  place-items: center;
  min-width: 18px;
  height: 18px;
  margin-left: 0.3rem;
  padding: 0 0.28rem;
  border-radius: var(--radius-pill);
  background: var(--c-danger);
  color: #fff;
  font-size: 0.625rem;
  font-weight: 700;
}

.hero-dial {
  position: relative;
  z-index: 1;
  flex: none;
}

.dial-ring {
  display: grid;
  place-items: center;
  width: clamp(150px, 16vw, 196px);
  aspect-ratio: 1;
  border-radius: 50%;
  padding: 10px;
  transition: background var(--dur-slow) var(--ease-out);
}

.dial-inner {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  width: 100%;
  height: 100%;
  border-radius: 50%;
  background: var(--surface-raised);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.6);
}

.dial-label {
  font-size: var(--text-2xs);
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--ink-faint);
}

.dial-value {
  font-family: var(--font-display);
  font-size: clamp(2rem, 2.6vw, 2.8rem);
  font-weight: 500;
  line-height: 1.1;
  color: var(--ink);
}

.dial-unit {
  font-size: var(--text-xs);
  color: var(--ink-muted);
}

.block {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.block-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--space-4);
}

.block-title {
  font-size: var(--text-xl);
  font-weight: 500;
}

.block-note {
  font-size: var(--text-xs);
  color: var(--ink-faint);
}

.grid-3 {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: var(--space-4);
}

.time-card {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.time-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
}

.time-label {
  font-size: var(--text-sm);
  font-weight: 550;
  color: var(--ink-soft);
}

.time-value {
  display: flex;
  align-items: baseline;
  gap: 0.35rem;
}

.time-value strong {
  font-family: var(--font-display);
  font-size: clamp(1.8rem, 2.2vw, 2.3rem);
  font-weight: 500;
  line-height: 1;
}

.time-value span {
  font-size: var(--text-xs);
  color: var(--ink-faint);
}

.split {
  display: grid;
  grid-template-columns: minmax(0, 1.35fr) minmax(0, 0.9fr);
  gap: var(--space-8);
}

.more {
  display: inline-flex;
  align-items: center;
  gap: 0.3rem;
  font-size: var(--text-xs);
  font-weight: 550;
  color: var(--ink-muted);
  text-decoration: none;
}

.more:hover {
  color: var(--accent-ink);
}

.list {
  list-style: none;
  margin: 0;
  padding: 0;
}

.skeleton-row {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-4);
  border-bottom: 1px solid color-mix(in srgb, var(--line) 55%, transparent);
}

.skeleton-row:last-child {
  border-bottom: 0;
}

.activity-row {
  border-bottom: 1px solid color-mix(in srgb, var(--line) 55%, transparent);
}

.activity-row:last-child {
  border-bottom: 0;
}

.activity-link {
  display: flex;
  align-items: center;
  gap: var(--space-4);
  padding: var(--space-4);
  color: inherit;
  text-decoration: none;
  transition: background-color var(--dur-fast) var(--ease-out);
}

.activity-link:hover {
  background: color-mix(in srgb, var(--ink) 3%, transparent);
}

.date {
  flex: none;
  width: 3.1rem;
  font-family: var(--font-mono);
  font-size: var(--text-xs);
  color: var(--ink-faint);
}

.activity-main {
  display: flex;
  flex-direction: column;
  gap: 0.1rem;
  min-width: 0;
  flex: 1;
}

.activity-main strong {
  font-size: var(--text-sm);
  font-weight: 600;
  color: var(--ink);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.activity-main small {
  font-size: var(--text-2xs);
  color: var(--ink-faint);
}

.hours {
  flex: none;
  width: 3.2rem;
  text-align: right;
  font-size: var(--text-sm);
  font-weight: 600;
  color: var(--ink-soft);
}

.notice-row {
  display: flex;
  gap: var(--space-3);
  padding: var(--space-4);
  border-bottom: 1px solid color-mix(in srgb, var(--line) 55%, transparent);
}

.notice-row:last-child {
  border-bottom: 0;
}

.dot {
  width: 7px;
  height: 7px;
  margin-top: 0.45rem;
  flex: none;
  border-radius: 50%;
  background: color-mix(in srgb, var(--ink) 14%, transparent);
}

.dot.unread {
  background: var(--accent);
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--accent) 16%, transparent);
}

.notice-main {
  display: flex;
  flex-direction: column;
  gap: 0.15rem;
  min-width: 0;
}

.notice-main strong {
  font-size: var(--text-sm);
  font-weight: 550;
  color: var(--ink);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.notice-main small {
  font-size: var(--text-2xs);
  color: var(--ink-faint);
}

.retry {
  margin-top: var(--space-3);
}

@media (max-width: 1080px) {
  .split {
    grid-template-columns: 1fr;
    gap: var(--space-6);
  }
}

@media (max-width: 820px) {
  .hero {
    flex-direction: column;
    align-items: flex-start;
    gap: var(--space-6);
  }

  .hero-dial {
    align-self: flex-end;
  }

  .grid-3 {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 560px) {
  .activity-link {
    flex-wrap: wrap;
    gap: var(--space-2);
  }

  .activity-main {
    flex-basis: calc(100% - 4rem);
  }

  .hours {
    width: auto;
  }
}
</style>
