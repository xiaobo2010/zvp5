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
import ZCountUp from '@/shared/ui/ZCountUp.vue'
import ZRingGauge from '@/shared/ui/ZRingGauge.vue'
import ZSparkline from '@/shared/ui/ZSparkline.vue'
import ZTiltCard from '@/shared/ui/ZTiltCard.vue'
import ZGlassSurface from '@/shared/ui/ZGlassSurface.vue'

type ModeKey = 'on-campus' | 'off-campus' | 'social-practice'

const TREND_MONTHS = 6

const { t, locale } = useI18n()
const session = useSessionStore()

const time = ref<UserTime | null>(null)
const stats = ref<UserTimeStatistics | null>(null)
const activities = ref<UserActivityItem[]>([])
const notifications = ref<NotificationItem[]>([])
const readMap = ref<Record<string, number>>({})
const loadingTime = ref(true)
const loadingActivities = ref(true)
const loadingNotifications = ref(true)
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
].map((mode) => ({
  ...mode,
  percent: mode.value === null ? 0 : Math.min(100, (mode.value / mode.target) * 100)
})))

/** 近 6 个月各分类的有效时长，用于卡片里的真实趋势线。 */
const trend = computed<Record<ModeKey, number[]>>(() => {
  const buckets: Record<ModeKey, number[]> = {
    'on-campus': Array.from({ length: TREND_MONTHS }, () => 0),
    'off-campus': Array.from({ length: TREND_MONTHS }, () => 0),
    'social-practice': Array.from({ length: TREND_MONTHS }, () => 0)
  }
  const now = new Date()
  const monthKeys = Array.from({ length: TREND_MONTHS }, (_, index) => {
    const date = new Date(now.getFullYear(), now.getMonth() - (TREND_MONTHS - 1 - index), 1)
    return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}`
  })

  for (const activity of activities.value) {
    const mine = activity.mine
    if (!mine || mine.status !== 'effective') continue
    const date = new Date(activity.date)
    if (Number.isNaN(date.getTime())) continue
    const key = `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}`
    const index = monthKeys.indexOf(key)
    if (index < 0) continue
    const mode = mine.mode as ModeKey
    if (!(mode in buckets)) continue
    const bucket = buckets[mode]
    bucket[index] = (bucket[index] ?? 0) + mine.duration
  }

  // 累计曲线：每个点是从窗口起点到该月的总时长，比例尺统一由最后一项归一
  for (const key of Object.keys(buckets) as ModeKey[]) {
    let running = 0
    buckets[key] = buckets[key].map((value) => {
      running += value
      return Math.round(running * 10) / 10
    })
  }
  return buckets
})

const recentActivities = computed(() => activities.value.slice(0, 5))

/** 社会实践卡片里沿进度轨道分布的粒子，位置固定、数量克制。 */
const PARTICLE_OFFSETS = [14, 38, 62, 86]

const unreadCount = computed(
  () => notifications.value.filter((item) => !readMap.value[item.id]).length
)

function percentileLabel(value: number | null): string {
  if (value === null) return ''
  if (value >= 100) return locale.value === 'zh-CN' ? '达到年级基础线' : 'At grade baseline'
  return locale.value === 'zh-CN' ? `超过 ${value}% 同学` : `Ahead of ${value}%`
}

async function load() {
  const oid = session.profile?._id
  if (!oid) {
    loadingTime.value = false
    loadingActivities.value = false
    loadingNotifications.value = false
    return
  }

  failed.value = false
  loadingTime.value = true
  loadingActivities.value = true
  loadingNotifications.value = true

  // 各区块独立加载：时长统计（含年级百分位）在 ADB 上可能耗时数秒，
  // 不能让它拖住首屏。
  void fetchUserTime(oid)
    .then((value) => {
      time.value = value
    })
    .catch(() => {
      failed.value = true
    })
    .finally(() => {
      loadingTime.value = false
    })

  void fetchUserTimeStatistics(oid)
    .then((value) => {
      stats.value = value
    })
    .catch(() => {
      stats.value = null
    })

  void fetchUserActivities(oid, { page: 1, perpage: 40 })
    .then((value) => {
      activities.value = value.activities ?? []
    })
    .catch(() => {
      activities.value = []
    })
    .finally(() => {
      loadingActivities.value = false
    })

  void Promise.allSettled([fetchNotifications(), fetchNotificationReads()])
    .then(([noticeResult, readResult]) => {
      if (noticeResult.status === 'fulfilled') {
        notifications.value = noticeResult.value.notifications.slice(0, 3)
      }
      if (readResult.status === 'fulfilled') readMap.value = readResult.value
    })
    .finally(() => {
      loadingNotifications.value = false
    })
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

    <section class="hero glass-host" aria-labelledby="home-title">
      <ZGlassSurface material="prominent" depth="lens" interactive :elasticity="0.55" />
      <span class="hero-aura" aria-hidden="true" />

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

      <ZTiltCard class="hero-panel" :max="6" :lift="2">
        <ZRingGauge
          class="hero-gauge"
          :value="dialPercent"
          :size="212"
          :stroke="11"
          :label="`${t('home.totalTime')} ${formatHours(total)} / ${totalTarget} ${t('common.hours')}`"
        >
          <span class="dial-label">{{ t('home.totalTime') }}</span>
          <strong class="dial-value num"><ZCountUp :value="total" :format="formatHours" /></strong>
          <span class="dial-unit">{{ t('common.hours') }}</span>
          <span class="dial-percent num">{{ Math.round(dialPercent) }}%</span>
        </ZRingGauge>
      </ZTiltCard>
    </section>

    <section class="block" aria-labelledby="time-title">
      <div class="block-head">
        <h2 id="time-title" class="block-title">{{ t('home.totalTime') }}</h2>
        <span class="block-note">{{ formatHours(total) }} / {{ totalTarget }} {{ t('common.hours') }}</span>
      </div>

      <div v-if="loadingTime" class="grid-3">
        <ZCard v-for="index in 3" :key="index">
          <ZSkeleton width="40%" height="0.8rem" />
          <div style="height: 1rem" />
          <ZSkeleton width="55%" height="2rem" />
          <div style="height: 1rem" />
          <ZSkeleton height="0.5rem" />
        </ZCard>
      </div>

      <div v-else class="grid-3">
        <ZTiltCard
          v-for="mode in modes"
          :key="mode.key"
          class="stat-card glass-host"
          :max="3.2"
          :lift="2"
        >
          <ZGlassSurface material="subtle" depth="flat" />

          <div class="time-head">
            <span class="time-label">{{ mode.label }}</span>
            <ZBadge v-if="mode.percentile !== null" tone="neutral" size="sm">
              {{ percentileLabel(mode.percentile) }}
            </ZBadge>
          </div>

          <p class="time-value">
            <strong><ZCountUp :value="mode.value" :format="formatHours" /></strong>
            <span>{{ t('common.hours') }}</span>
          </p>

          <div class="stat-visual">
            <ZSparkline
              v-if="mode.key === 'on-campus'"
              class="stat-spark"
              :values="trend[mode.key]"
              :height="46"
            />

            <div v-else-if="mode.key === 'off-campus'" class="stat-ring">
              <ZRingGauge :value="mode.percent" :size="82" :stroke="6">
                <span class="mini-value num">{{ Math.round(mode.percent) }}%</span>
              </ZRingGauge>
              <span class="stat-caption">{{ formatHours(mode.value) }} / {{ mode.target }}h</span>
            </div>

            <div v-else class="stat-trail">
              <span class="trail-base" />
              <span class="trail-fill" :style="{ width: `${mode.percent}%` }">
                <i
                  v-for="(offset, index) in PARTICLE_OFFSETS"
                  :key="offset"
                  :style="{ '--p': `${offset}%`, '--i': index }"
                />
              </span>
            </div>
          </div>

          <footer class="stat-foot">
            <span class="stat-caption">{{ t('home.target') }} {{ mode.target }}h</span>
            <span class="num">{{ Math.round(mode.percent) }}%</span>
          </footer>
        </ZTiltCard>
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
      <section v-reveal class="block" aria-labelledby="recent-title">
        <div class="block-head">
          <h2 id="recent-title" class="block-title">{{ t('home.recent') }}</h2>
          <RouterLink to="/activities" class="more">
            {{ t('home.viewAll') }}
            <ZIcon name="arrow-right" :size="15" />
          </RouterLink>
        </div>

        <ZCard padding="none">
          <div v-if="loadingActivities" class="list">
            <div v-for="index in 4" :key="index" class="skeleton-row">
              <ZSkeleton circle width="34px" height="34px" />
              <ZSkeleton width="60%" height="0.9rem" />
            </div>
          </div>

          <ol v-else-if="recentActivities.length" class="timeline">
            <li
              v-for="(activity, index) in recentActivities"
              :key="activity._id"
              class="tl-item"
              :style="{ '--i': index }"
            >
              <span class="tl-axis" aria-hidden="true"><span class="tl-dot" /></span>
              <RouterLink :to="`/activities/${activity._id}`" class="tl-card">
                <div class="tl-top">
                  <time class="num">{{ formatDate(activity.date) }}</time>
                  <ZBadge
                    :tone="activity.status === 'effective' ? 'success' : activity.status === 'pending' ? 'warning' : 'danger'"
                  >
                    {{ t(`activities.status.${activity.status}`) }}
                  </ZBadge>
                </div>
                <strong class="tl-name">{{ activity.name }}</strong>
                <div class="tl-meta">
                  <small>
                    {{ t(`activities.type.${activity.type}`) }}
                    <template v-if="activity.place"> · {{ activity.place }}</template>
                  </small>
                  <span class="hours num">{{ formatHours(activity.mine?.duration ?? 0) }}h</span>
                </div>
              </RouterLink>
            </li>
          </ol>

          <ZEmpty v-else compact icon="compass" :title="t('home.noRecent')" />
        </ZCard>
      </section>

      <section v-reveal class="block" aria-labelledby="inbox-title">
        <div class="block-head">
          <h2 id="inbox-title" class="block-title">{{ t('nav.notifications') }}</h2>
          <RouterLink to="/notifications" class="more">
            {{ t('home.viewAll') }}
            <ZIcon name="arrow-right" :size="15" />
          </RouterLink>
        </div>

        <ZCard padding="none" tone="glass">
          <div v-if="loadingNotifications" class="list">
            <div v-for="index in 3" :key="index" class="skeleton-row">
              <ZSkeleton width="70%" height="0.9rem" />
            </div>
          </div>

          <ul v-else-if="notifications.length" class="notices">
            <li
              v-for="(item, index) in notifications"
              :key="item.id"
              class="notice"
              :class="{ 'is-unread': !readMap[item.id] }"
              :style="{ '--i': index }"
            >
              <span class="notice-rail" aria-hidden="true" />
              <div class="notice-main">
                <strong>{{ item.title }}</strong>
                <small>{{ formatDate(item.updated_at ?? item.created_at) }}</small>
              </div>
              <span v-if="!readMap[item.id]" class="notice-tag">
                {{ t('notifications.unread') }}
              </span>
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
  border: 1px solid color-mix(in srgb, var(--glass-border) 74%, var(--accent));
  box-shadow: var(--glass-shadow), inset 0 1px 0 var(--glass-hi);
  overflow: hidden;
}

/* 环境光在卡片内部缓慢移动，随指针产生轻微位移，不做旋转 */
.hero-aura {
  position: absolute;
  inset: -18%;
  z-index: 0;
  pointer-events: none;
  background:
    radial-gradient(40% 44% at 74% 28%, color-mix(in srgb, var(--accent) 26%, transparent), transparent 68%),
    radial-gradient(36% 40% at 16% 84%, color-mix(in srgb, var(--c-sage) 16%, transparent), transparent 70%);
  animation: hero-aura 24s var(--ease-in-out) infinite alternate;
  will-change: transform;
}

@keyframes hero-aura {
  from {
    transform: translate3d(-2%, 1%, 0) scale(1);
  }

  to {
    transform: translate3d(3%, -2%, 0) scale(1.07);
  }
}

.hero::after {
  content: '';
  position: absolute;
  inset: 0;
  z-index: 0;
  pointer-events: none;
  background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='120' height='120'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='2'/%3E%3C/filter%3E%3Crect width='120' height='120' filter='url(%23n)' opacity='0.03'/%3E%3C/svg%3E");
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

/* 累计时长：立体玻璃面板 + 环形进度，悬停有轻微视差 */
.hero-panel {
  position: relative;
  z-index: 1;
  flex: none;
  display: grid;
  place-items: center;
  padding: 0.4rem;
  border-radius: 50%;
}

.hero-gauge {
  filter: drop-shadow(0 20px 44px color-mix(in srgb, var(--accent) 22%, transparent));
}

.dial-label {
  font-size: var(--text-2xs);
  letter-spacing: 0.12em;
  text-transform: uppercase;
  color: var(--ink-faint);
}

.dial-value {
  font-family: var(--font-display);
  font-size: clamp(2.3rem, 3vw, 3rem);
  font-weight: 500;
  line-height: 1.05;
  color: var(--ink);
}

.dial-unit {
  font-size: var(--text-xs);
  color: var(--ink-muted);
}

.dial-percent {
  margin-top: 0.3rem;
  font-size: var(--text-2xs);
  letter-spacing: 0.1em;
  color: var(--accent-ink);
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

.stat-card {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding: clamp(1.05rem, 0.85rem + 0.6vw, 1.45rem);
  border: 1px solid color-mix(in srgb, var(--glass-border) 82%, transparent);
  box-shadow: var(--shadow-sm), inset 0 1px 0 var(--glass-hi);
  overflow: hidden;
}

.stat-card:hover {
  border-color: color-mix(in srgb, var(--accent) 26%, var(--glass-border));
  box-shadow: var(--shadow-md), inset 0 1px 0 var(--glass-hi);
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

.stat-visual {
  position: relative;
  display: flex;
  align-items: center;
  min-height: 84px;
}

.stat-spark {
  width: 100%;
  height: 46px;
}

.stat-ring {
  display: flex;
  align-items: center;
  gap: var(--space-4);
}

.mini-value {
  font-family: var(--font-display);
  font-size: 1.05rem;
  font-weight: 600;
  color: var(--ink-soft);
}

.stat-caption {
  font-size: var(--text-2xs);
  letter-spacing: 0.04em;
  color: var(--ink-faint);
}

.stat-foot {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--space-3);
  padding-top: var(--space-2);
  border-top: 1px solid color-mix(in srgb, var(--line) 55%, transparent);
  font-size: var(--text-xs);
  font-weight: 600;
  color: var(--ink-soft);
}

/* 社会实践：粒子沿已完成进度缓慢漂移 */
.stat-trail {
  position: relative;
  width: 100%;
  height: 10px;
}

.trail-base {
  position: absolute;
  inset: 0;
  border-radius: var(--radius-pill);
  background: color-mix(in srgb, var(--ink) 7%, transparent);
}

.trail-fill {
  position: absolute;
  top: 0;
  left: 0;
  height: 100%;
  border-radius: inherit;
  overflow: hidden;
  background: linear-gradient(90deg, #c99a45, var(--accent-strong));
  box-shadow: 0 4px 14px -8px color-mix(in srgb, var(--accent-strong) 90%, transparent);
  transition: width 1100ms var(--spring-soft);
}

.trail-fill::after {
  content: '';
  position: absolute;
  inset: 0;
  background: linear-gradient(90deg, transparent, rgba(255, 247, 230, 0.5), transparent);
  transform: translateX(-100%);
  animation: trail-sheen 5.4s var(--ease-in-out) infinite;
}

.trail-fill i {
  position: absolute;
  top: 50%;
  left: var(--p, 0%);
  width: 4px;
  height: 4px;
  margin-top: -2px;
  border-radius: 50%;
  background: #fff6e2;
  opacity: 0;
  animation: particle-drift 6s var(--ease-in-out) infinite;
  animation-delay: calc(var(--i, 0) * -1.4s);
}

@keyframes trail-sheen {
  0%,
  55% {
    transform: translateX(-100%);
  }

  100% {
    transform: translateX(100%);
  }
}

@keyframes particle-drift {
  0%,
  100% {
    opacity: 0;
    transform: translate3d(-3px, 0, 0);
  }

  30% {
    opacity: 0.85;
  }

  70% {
    opacity: 0.55;
    transform: translate3d(3px, 0, 0);
  }
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

/* --- 最近活动：动态时间轴 ------------------------------------------------ */
.timeline {
  position: relative;
  list-style: none;
  margin: 0;
  padding: var(--space-4) var(--space-4) var(--space-2);
}

.tl-item {
  position: relative;
  display: flex;
  gap: var(--space-2);
  animation: tl-in 560ms var(--spring-soft) both;
  animation-delay: calc(var(--i, 0) * 60ms + 80ms);
}

@keyframes tl-in {
  from {
    opacity: 0;
    transform: translate3d(0, 8px, 0);
  }

  to {
    opacity: 1;
    transform: none;
  }
}

.tl-axis {
  position: relative;
  flex: none;
  display: flex;
  justify-content: center;
  width: 18px;
  padding-top: 0.85rem;
}

.tl-dot {
  position: relative;
  z-index: 1;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--accent);
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--accent) 14%, transparent);
  transition: box-shadow var(--dur-base) var(--ease-out);
}

.tl-axis::after {
  content: '';
  position: absolute;
  top: 1.35rem;
  bottom: 0.15rem;
  width: 1.5px;
  border-radius: var(--radius-pill);
  background: linear-gradient(
    color-mix(in srgb, var(--accent) 34%, var(--line)),
    color-mix(in srgb, var(--line) 72%, transparent)
  );
}

.tl-item:last-child .tl-axis::after {
  display: none;
}

.tl-card {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 0.15rem;
  margin-bottom: 0.35rem;
  padding: 0.55rem 0.7rem;
  border: 1px solid transparent;
  border-radius: var(--radius-md);
  color: inherit;
  text-decoration: none;
  transition:
    background-color var(--dur-fast) var(--ease-out),
    border-color var(--dur-fast) var(--ease-out),
    transform var(--dur-fast) var(--spring-snappy);
}

.tl-card:hover {
  background: color-mix(in srgb, var(--glass-hi) 26%, transparent);
  border-color: color-mix(in srgb, var(--glass-border) 66%, transparent);
  transform: translate3d(2px, 0, 0);
}

.tl-item:hover .tl-dot {
  box-shadow: 0 0 0 4px color-mix(in srgb, var(--accent) 20%, transparent);
}

.tl-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
}

.tl-top time {
  font-family: var(--font-mono);
  font-size: var(--text-2xs);
  letter-spacing: 0.04em;
  color: var(--ink-faint);
}

.tl-name {
  font-size: var(--text-sm);
  font-weight: 600;
  color: var(--ink);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tl-meta {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--space-2);
}

.tl-meta small {
  font-size: var(--text-2xs);
  color: var(--ink-faint);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.hours {
  flex: none;
  font-size: var(--text-xs);
  font-weight: 600;
  color: var(--ink-soft);
}

/* --- 通知：分层玻璃信息面板 ---------------------------------------------- */
.notices {
  display: flex;
  flex-direction: column;
  gap: 0.45rem;
  list-style: none;
  margin: 0;
  padding: var(--space-3);
}

.notice {
  position: relative;
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: 0.7rem 0.85rem;
  border: 1px solid color-mix(in srgb, var(--glass-border) 58%, transparent);
  border-radius: var(--radius-md);
  background:
    linear-gradient(155deg, color-mix(in srgb, var(--glass-hi) 20%, transparent), transparent 62%);
  animation: tl-in 560ms var(--spring-soft) both;
  animation-delay: calc(var(--i, 0) * 70ms + 120ms);
  transition:
    background-color var(--dur-fast) var(--ease-out),
    border-color var(--dur-fast) var(--ease-out),
    transform var(--dur-fast) var(--spring-snappy);
}

.notice:hover {
  border-color: color-mix(in srgb, var(--glass-border) 88%, transparent);
  transform: translate3d(2px, 0, 0);
}

.notice.is-unread {
  border-color: color-mix(in srgb, var(--accent) 28%, var(--glass-border));
  background:
    linear-gradient(155deg, color-mix(in srgb, var(--accent) 11%, transparent), transparent 66%);
}

.notice-rail {
  position: absolute;
  left: 0;
  top: 22%;
  bottom: 22%;
  width: 2px;
  border-radius: var(--radius-pill);
  background: transparent;
  transition: background-color var(--dur-fast) var(--ease-out);
}

.notice.is-unread .notice-rail {
  background: linear-gradient(var(--accent), transparent);
}

.notice-tag {
  flex: none;
  padding: 0.12rem 0.42rem;
  border-radius: var(--radius-pill);
  background: color-mix(in srgb, var(--accent) 13%, transparent);
  font-size: 0.625rem;
  font-weight: 600;
  letter-spacing: 0.04em;
  color: var(--accent-ink);
}

.notice-main {
  display: flex;
  flex-direction: column;
  gap: 0.15rem;
  flex: 1;
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

  .hero-panel {
    align-self: flex-end;
  }

  .grid-3 {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 560px) {
  .tl-card {
    padding-inline: 0.5rem;
  }

  .hours {
    width: auto;
  }
}
</style>
