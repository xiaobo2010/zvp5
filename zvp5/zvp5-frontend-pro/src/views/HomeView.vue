<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import {
  CanaryApiError,
  currentUserId,
  fetchProfile,
  fetchTime,
  isLoggedIn
} from '@/api/canary'
import type { CanaryTime, CanaryUser } from '@/api/canary'
import { ApiError } from '@/api/http'
import HomeProgressArtwork from '@/components/home/HomeProgressArtwork.vue'
import { assignHomeArtwork, calculateHomeProgress } from '@/utils/homeArtwork'

const initiallySignedIn = isLoggedIn()

const profile = ref<CanaryUser | null>(null)
const time = ref<CanaryTime | null>(null)
const loading = ref(initiallySignedIn)
const unauthenticated = ref(!initiallySignedIn)
const error = ref('')
const refreshing = ref(false)
const refreshFeedback = ref('')

let loadSequence = 0
let disposed = false

const displayName = computed(() => profile.value?.name?.trim() ?? '')
const assignment = computed(() => (profile.value ? assignHomeArtwork(profile.value._id) : null))
const progress = computed(() =>
  time.value
    ? calculateHomeProgress({
        onCampus: time.value['on-campus'],
        offCampus: time.value['off-campus'],
        socialPractice: time.value['social-practice']
      })
    : null
)
const categories = computed(() => progress.value?.categories ?? [])

function format(value: number): string {
  if (!Number.isFinite(value)) return '—'
  return value.toLocaleString('zh-CN', { maximumFractionDigits: 2 })
}

function clampPercent(value: number): number {
  if (!Number.isFinite(value)) return 0
  return Math.max(0, Math.min(100, Math.round(value)))
}

function progressText(effective: number, target: number): string {
  if (!Number.isFinite(effective) || !Number.isFinite(target)) return '完成情况暂不可用'
  return `${format(effective)} / ${format(target)} 小时`
}

function gapText(gap: number, bonus: number): string {
  if (!Number.isFinite(gap)) return '完成情况暂不可用'
  const suffix = bonus > 0 ? ` · 含折算 ${format(bonus)} 小时` : ''
  return `${gap >= 0 ? '已达标' : `还差 ${format(-gap)} 小时`}${suffix}`
}

function isAuthError(reason: unknown): boolean {
  return (
    (reason instanceof CanaryApiError && reason.status === 401) ||
    (reason instanceof ApiError && reason.status === 401)
  )
}

function readableError(reason: unknown, fallback: string): string {
  if (reason instanceof CanaryApiError && reason.message) return reason.message
  if (reason instanceof ApiError && reason.message) return reason.message
  return fallback
}

async function loadPage(): Promise<void> {
  if (!isLoggedIn()) {
    unauthenticated.value = true
    loading.value = false
    profile.value = null
    time.value = null
    return
  }

  const sequence = ++loadSequence
  const accountAtStart = currentUserId()
  loading.value = true
  unauthenticated.value = false
  error.value = ''
  profile.value = null
  time.value = null

  const [profileResult, timeResult] = await Promise.allSettled([
    fetchProfile(),
    fetchTime()
  ])
  if (disposed || sequence !== loadSequence || currentUserId() !== accountAtStart) return

  if (
    (profileResult.status === 'rejected' && isAuthError(profileResult.reason)) ||
    (timeResult.status === 'rejected' && isAuthError(timeResult.reason))
  ) {
    unauthenticated.value = true
    profile.value = null
    time.value = null
    loading.value = false
    return
  }

  if (profileResult.status === 'fulfilled') profile.value = profileResult.value
  if (timeResult.status === 'fulfilled') time.value = timeResult.value

  const failures: string[] = []
  if (profileResult.status === 'rejected') {
    failures.push(readableError(profileResult.reason, '账号资料加载失败。'))
  }
  if (timeResult.status === 'rejected') {
    failures.push(readableError(timeResult.reason, '义工时长加载失败。'))
  }
  error.value = failures.join(' ')

  loading.value = false
}

async function refreshTime(): Promise<void> {
  if (refreshing.value || loading.value || unauthenticated.value || !profile.value) return

  const sequence = ++loadSequence
  const accountAtStart = currentUserId()
  refreshing.value = true
  refreshFeedback.value = ''

  try {
    const result = await fetchTime()
    if (disposed || sequence !== loadSequence || currentUserId() !== accountAtStart) return
    time.value = result
    refreshFeedback.value = '义工时长已更新。'
  } catch (reason) {
    if (disposed || sequence !== loadSequence || currentUserId() !== accountAtStart) return
    if (isAuthError(reason)) {
      unauthenticated.value = true
      profile.value = null
      time.value = null
      return
    }
    // Keep the last successful values; the artwork remains a truthful snapshot.
    refreshFeedback.value = readableError(reason, '刷新失败，已保留上次的义工时长。')
  } finally {
    if (!disposed && sequence === loadSequence) refreshing.value = false
  }
}

function handleRetry(): void {
  if (loading.value) return
  void loadPage()
}

onMounted(() => {
  if (initiallySignedIn) void loadPage()
})

onUnmounted(() => {
  disposed = true
  loadSequence++
})
</script>

<template>
  <div class="home-reveal">
    <header class="home-reveal__header">
      <div class="home-reveal__heading">
        <p class="home-reveal__eyebrow">ZVMS · 画作揭示</p>
        <h1 class="home-reveal__title">每一小时，让画面显影。</h1>
        <p class="home-reveal__subtitle">
          校内、校外与社会实践分别恢复同一幅画作的红、绿、蓝通道。三项达标时，属于你的画面完整显现。
        </p>
      </div>
      <RouterLink class="home-reveal__profile" to="/me">
        <span>{{ displayName || '个人主页' }}</span>
        <span aria-hidden="true">↗</span>
      </RouterLink>
    </header>

    <section v-if="unauthenticated" class="home-reveal__state" role="alert">
      <h2>需要登录</h2>
      <p>登录后即可查看你的固定画作与义工完成进度。</p>
      <RouterLink class="home-reveal__button home-reveal__button--primary" to="/login?redirect=/">
        去登录
      </RouterLink>
    </section>

    <section v-else-if="loading" class="home-reveal__state" role="status" aria-live="polite">
      <h2>正在读取你的义工画布</h2>
      <p>作品分配只依赖你的账号，不会因为换设备而重新抽图。</p>
      <div class="home-reveal__skeleton" aria-hidden="true">
        <span></span><span></span><span></span>
      </div>
    </section>

    <template v-else>
      <section class="home-reveal__gallery" aria-label="义工完成情况画作">
        <div class="home-reveal__artwork">
          <HomeProgressArtwork
            :assignment="assignment"
            :categories="categories"
            :loading="refreshing"
          />
        </div>

        <aside v-if="progress" class="home-reveal__legend" aria-label="义工时长图例">
          <article
            v-for="category in progress.categories"
            :key="category.key"
            class="home-reveal__legend-item"
            :data-channel="category.key"
          >
            <p class="home-reveal__legend-label">
              <span class="home-reveal__channel" aria-hidden="true"></span>
              {{ category.label }}
            </p>
            <p class="home-reveal__percent">{{ format(category.percent) }}<small>%</small></p>
            <div
              class="home-reveal__track"
              role="progressbar"
              :aria-label="category.label"
              :aria-valuenow="clampPercent(category.percent)"
              aria-valuemin="0"
              aria-valuemax="100"
              :aria-valuetext="progressText(category.effective, category.target)"
            >
              <span :style="{ width: `${clampPercent(category.ratio * 100)}%` }"></span>
            </div>
            <p class="home-reveal__hours">{{ progressText(category.effective, category.target) }}</p>
            <p class="home-reveal__gap">{{ gapText(category.gap, category.bonus) }}</p>
          </article>

          <div class="home-reveal__summary">
            <p>
              累计参与
              <strong>{{ format(progress.total) }}</strong> 小时
            </p>
            <p class="home-reveal__summary-note">
              {{ progress.hasBonus ? '有效进度含互补折算；累计值只统计原始三项。' : '有效进度直接来自当前三类义工时长。' }}
            </p>
          </div>
        </aside>

        <div v-else class="home-reveal__legend home-reveal__legend--waiting" role="status">
          <p>取得时长后，画面会按完成比例逐渐显现。</p>
        </div>
      </section>

      <div class="home-reveal__actions" :aria-busy="refreshing">
        <button
          class="home-reveal__button"
          type="button"
          :disabled="refreshing"
          @click="refreshTime"
        >
          {{ refreshing ? '读取中…' : '刷新时长' }}
        </button>
        <RouterLink class="home-reveal__button" to="/volunteers">查看活动</RouterLink>
        <RouterLink
          class="home-reveal__button home-reveal__button--primary"
          to="/me"
        >
          个人档案
        </RouterLink>
      </div>

      <p v-if="error" class="home-reveal__message home-reveal__message--error" role="alert">
        {{ error }}
        <button type="button" @click="handleRetry">重试</button>
      </p>
      <p v-else-if="refreshFeedback" class="home-reveal__message" role="status" aria-live="polite">
        {{ refreshFeedback }}
      </p>

      <section class="home-reveal__explain" aria-label="画作揭示规则">
        <article>
          <h2>固定画作</h2>
          <p>同一账号始终获得同一幅作品与同一条揭示路径，刷新或换设备都不会重新抽图。</p>
        </article>
        <article>
          <h2>红绿蓝通道</h2>
          <p>三类义工各自控制一个颜色通道。完成比例精确到逻辑像素，画面覆盖率最高为 100%。</p>
        </article>
        <article>
          <h2>有效进度</h2>
          <p>校外超过 15 小时的部分按 1/2 折入校内，校内超过 25 小时的部分按 1/3 折入校外。</p>
        </article>
      </section>
    </template>
  </div>
</template>

<style scoped>
.home-reveal {
  min-height: 100vh;
  max-width: 1160px;
  margin-inline: auto;
  padding: var(--space-8) var(--space-4) calc(var(--space-10) + env(safe-area-inset-bottom, 0px));
}

.home-reveal__header {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: var(--space-5);
  margin-bottom: var(--space-7);
}

.home-reveal__eyebrow {
  margin-bottom: var(--space-2);
  color: var(--accent-ink);
  font-size: var(--font-size-caption);
  font-weight: 700;
  letter-spacing: 0.12em;
}

.home-reveal__title {
  max-width: 620px;
  font-size: clamp(38px, 5vw, 64px);
  letter-spacing: -0.045em;
}

.home-reveal__subtitle {
  max-width: 620px;
  margin-top: var(--space-3);
  color: var(--color-text-secondary);
  font-size: var(--font-size-body-large);
}

.home-reveal__profile {
  flex-shrink: 0;
  min-height: 42px;
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  padding: 0 var(--space-4);
  border: 1px solid var(--color-border-moderate);
  border-radius: var(--radius-pill);
  color: var(--color-text-primary);
  font-weight: 600;
  background: rgb(255 255 255 / 0.68);
}

.home-reveal__profile:hover {
  border-color: var(--color-brand);
  color: var(--accent-ink);
}

.home-reveal__gallery {
  display: grid;
  grid-template-columns: minmax(0, 1.2fr) minmax(300px, 0.8fr);
  align-items: center;
  gap: var(--space-7);
}

.home-reveal__artwork {
  display: flex;
  justify-content: center;
}

.home-reveal__legend {
  display: grid;
  gap: var(--space-3);
}

.home-reveal__legend-item {
  padding: var(--space-4);
  border: 1px solid var(--color-border-subtle);
  border-radius: var(--radius-lg);
  background: rgb(255 255 255 / 0.7);
}

.home-reveal__legend-label {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  color: var(--color-text-secondary);
  font-size: var(--font-size-caption);
  font-weight: 700;
}

.home-reveal__channel {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: #8e8e93;
}

.home-reveal__legend-item[data-channel='on'] .home-reveal__channel {
  background: #e5484d;
}

.home-reveal__legend-item[data-channel='off'] .home-reveal__channel {
  background: #30a46c;
}

.home-reveal__legend-item[data-channel='social'] .home-reveal__channel {
  background: #0091ff;
}

.home-reveal__percent {
  margin-top: var(--space-2);
  font-size: clamp(34px, 3vw, 46px);
  font-weight: 700;
  letter-spacing: -0.04em;
  line-height: 1;
}

.home-reveal__percent small {
  margin-left: 3px;
  font-size: var(--font-size-body);
}

.home-reveal__track {
  height: 7px;
  margin-top: var(--space-3);
  overflow: hidden;
  border-radius: var(--radius-pill);
  background: rgb(0 0 0 / 0.09);
}

.home-reveal__track span {
  display: block;
  height: 100%;
  border-radius: inherit;
  background: #8e8e93;
  transition: width var(--duration-base) var(--ease-settle);
}

.home-reveal__legend-item[data-channel='on'] .home-reveal__track span {
  background: #e5484d;
}

.home-reveal__legend-item[data-channel='off'] .home-reveal__track span {
  background: #30a46c;
}

.home-reveal__legend-item[data-channel='social'] .home-reveal__track span {
  background: #0091ff;
}

.home-reveal__hours {
  margin-top: var(--space-2);
  font-size: var(--font-size-callout);
}

.home-reveal__gap {
  margin-top: var(--space-1);
  color: var(--color-text-secondary);
  font-size: var(--font-size-caption);
}

.home-reveal__summary {
  padding: var(--space-3) var(--space-4);
  border: 1px dashed var(--color-border-subtle);
  border-radius: var(--radius-lg);
  color: var(--color-text-secondary);
  font-size: var(--font-size-callout);
}

.home-reveal__summary strong {
  color: var(--color-text-primary);
  font-size: var(--font-size-headline);
}

.home-reveal__summary-note {
  margin-top: var(--space-1);
  font-size: var(--font-size-caption);
}

.home-reveal__legend--waiting {
  align-self: center;
  padding: var(--space-5);
  border: 1px dashed var(--color-border-subtle);
  border-radius: var(--radius-lg);
  color: var(--color-text-secondary);
  text-align: center;
}

.home-reveal__actions {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
  margin-top: var(--space-5);
}

.home-reveal__button {
  min-height: 42px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding-inline: var(--space-4);
  border: 1px solid var(--color-border-moderate);
  border-radius: var(--radius-pill);
  color: var(--color-text-primary);
  font-weight: 600;
  background: rgb(255 255 255 / 0.74);
}

.home-reveal__button:hover {
  border-color: var(--color-brand);
}

.home-reveal__button--primary {
  border-color: transparent;
  color: white;
  background: var(--color-brand);
}

.home-reveal__button--primary:hover {
  color: white;
  background: var(--color-brand-hover);
}

button.home-reveal__button:disabled {
  cursor: wait;
  opacity: 0.62;
}

.home-reveal__message {
  margin-top: var(--space-3);
  color: var(--color-text-secondary);
  font-size: var(--font-size-callout);
}

.home-reveal__message--error {
  color: #b42318;
}

.home-reveal__message button {
  margin-left: var(--space-2);
  color: var(--accent-ink);
  font-weight: 700;
}

.home-reveal__explain {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: var(--space-3);
  margin-top: var(--space-7);
  padding-top: var(--space-5);
  border-top: 1px solid var(--color-border-subtle);
}

.home-reveal__explain article {
  padding: var(--space-3);
  border-radius: var(--radius-md);
  background: rgb(255 255 255 / 0.45);
}

.home-reveal__explain h2 {
  font-size: var(--font-size-body);
}

.home-reveal__explain p {
  margin-top: var(--space-2);
  color: var(--color-text-secondary);
  font-size: var(--font-size-caption);
  line-height: var(--line-height-relaxed);
}

.home-reveal__state {
  max-width: 560px;
  margin-inline: auto;
  padding: var(--space-7);
  border: 1px solid var(--color-border-subtle);
  border-radius: var(--radius-xl);
  background: rgb(255 255 255 / 0.76);
  text-align: center;
}

.home-reveal__state h2 {
  font-size: var(--font-size-headline);
}

.home-reveal__state > p {
  margin-top: var(--space-2);
  color: var(--color-text-secondary);
}

.home-reveal__state > .home-reveal__button {
  margin-top: var(--space-4);
}

.home-reveal__skeleton {
  display: flex;
  gap: var(--space-2);
  justify-content: center;
  margin-top: var(--space-5);
}

.home-reveal__skeleton span {
  width: 54px;
  height: 8px;
  border-radius: var(--radius-pill);
  background: rgb(0 0 0 / 0.12);
  animation: reveal-pulse 1.2s ease-in-out infinite;
}

.home-reveal__skeleton span:nth-child(2) {
  animation-delay: 0.15s;
}

.home-reveal__skeleton span:nth-child(3) {
  animation-delay: 0.3s;
}

@keyframes reveal-pulse {
  0%,
  100% {
    opacity: 0.35;
  }
  50% {
    opacity: 0.8;
  }
}

@media (max-width: 900px) {
  .home-reveal {
    padding-top: var(--space-6);
  }

  .home-reveal__header {
    align-items: flex-start;
    flex-direction: column;
  }

  .home-reveal__gallery {
    grid-template-columns: 1fr;
    gap: var(--space-5);
  }

  .home-reveal__explain {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 520px) {
  .home-reveal__title {
    font-size: 34px;
  }

  .home-reveal__legend-item {
    padding: var(--space-3);
  }

  .home-reveal__actions {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .home-reveal__actions > :last-child {
    grid-column: 1 / -1;
  }
}

@media (prefers-reduced-motion: reduce) {
  .home-reveal__track span,
  .home-reveal__skeleton span {
    transition: none;
    animation: none;
  }
}
</style>
