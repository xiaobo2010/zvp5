<script setup lang="ts">
import { onMounted, ref } from 'vue'

import {
  fetchActivities,
  fetchGroupName,
  fetchNotices,
  fetchProfile,
  fetchTime,
  isAuthError,
  isLoggedIn,
  readableError,
  serverTimeOffset,
  type CanaryActivity,
  type CanaryActivityPage,
  type CanaryNotice,
  type CanaryNoticeList,
  type CanaryTime
} from '@/api/canary'
import {
  fetchPendingReviews,
  fetchRecentAgentRuns,
  fetchSuspiciousLogs,
  type AdminLog,
  type HomeAgentRun
} from '@/api/home'
import ThreeDStage from '@/components/stage/ThreeDStage.vue'
import type { StageData } from '@/types/stage'
import { buildStageData } from './buildStageData'

type ViewState = 'loading' | 'ready' | 'auth' | 'error'

const state = ref<ViewState>('loading')
const data = ref<StageData | null>(null)
const errorMessage = ref('')
const profileName = ref('')

function settle<T>(result: PromiseSettledResult<T>, fallback: T): T {
  return result.status === 'fulfilled' ? result.value : fallback
}

async function load() {
  if (!isLoggedIn()) {
    state.value = 'auth'
    return
  }

  state.value = 'loading'
  errorMessage.value = ''

  try {
    const profile = await fetchProfile()
    profileName.value = profile.name || ''

    const roles = Array.isArray(profile.roles) ? profile.roles : []
    const isAdmin = roles.includes('admin')
    const isDepartment = roles.includes('department')

    const [timeRes, activitiesRes, pendingRes, agentsRes, logsRes, noticesRes, classRes] =
      await Promise.allSettled([
        fetchTime(),
        fetchActivities('mine', 1, 8, ''),
        isDepartment ? fetchPendingReviews() : Promise.resolve<CanaryActivity[]>([]),
        isAdmin || isDepartment ? fetchRecentAgentRuns() : Promise.resolve<HomeAgentRun[]>([]),
        isAdmin ? fetchSuspiciousLogs() : Promise.resolve<AdminLog[]>([]),
        !isAdmin && !isDepartment ? fetchNotices() : Promise.resolve(null),
        profile.class_id ? fetchGroupName(profile.class_id) : Promise.resolve('')
      ])

    const time = settle<CanaryTime | null>(timeRes, null)
    const activities = settle<CanaryActivityPage>(activitiesRes, { activities: [], total: 0 })
    const pending = settle<CanaryActivity[]>(pendingRes, [])
    const agents = settle<HomeAgentRun[]>(agentsRes, [])
    const logs = settle<AdminLog[]>(logsRes, [])
    const notices = settle<CanaryNoticeList | null>(noticesRes, null)
    const className = settle(classRes, '')

    data.value = buildStageData({
      profile,
      className,
      time,
      activities: Array.isArray(activities.activities) ? activities.activities : [],
      pending: Array.isArray(pending) ? pending : [],
      agents: Array.isArray(agents) ? agents : [],
      logs: Array.isArray(logs) ? logs : [],
      notices: Array.isArray(notices?.notifications) ? (notices.notifications as CanaryNotice[]) : [],
      serverOffsetMs: serverTimeOffset()
    })
    state.value = 'ready'
  } catch (reason) {
    if (isAuthError(reason)) {
      state.value = 'auth'
      return
    }
    errorMessage.value = readableError(reason, '空间数据加载失败，请稍后重试。')
    state.value = 'error'
  }
}

onMounted(load)
</script>

<template>
  <div class="home">
    <header class="home__bar">
      <a class="home__brand" href="./" aria-label="ZVMS Astra-Alpha 首页">
        <span class="home__logo">ZVMS</span>
        <span class="home__badge">ASTRA</span>
      </a>
      <div class="home__actions">
        <span v-if="profileName" class="home__user">{{ profileName }}</span>
        <a class="home__link" href="/">主站</a>
      </div>
    </header>

    <main class="home__main">
      <div v-if="state === 'loading'" class="home__state">
        <div class="home__orbit" aria-hidden="true">
          <span></span>
          <span></span>
          <span></span>
        </div>
        <p class="home__state-title">正在校准空间</p>
        <p class="home__state-text">读取账号、时长与最近义工记录…</p>
      </div>

      <div v-else-if="state === 'auth'" class="home__state">
        <p class="home__state-kicker">需要登录</p>
        <p class="home__state-title">登录后进入 3D 工作台</p>
        <p class="home__state-text">
          使用 ZVMS 账号登录后回到这里刷新，即可进入 Astra-Alpha 空间。
        </p>
        <div class="home__state-actions">
          <a class="home__button home__button--primary" href="/#/login">前往登录</a>
          <button class="home__button" type="button" @click="load">重新检查</button>
        </div>
      </div>

      <div v-else-if="state === 'error'" class="home__state">
        <p class="home__state-kicker">连接中断</p>
        <p class="home__state-title">空间数据没有加载出来</p>
        <p class="home__state-text">{{ errorMessage }}</p>
        <div class="home__state-actions">
          <button class="home__button home__button--primary" type="button" @click="load">
            重试
          </button>
        </div>
      </div>

      <ThreeDStage v-else-if="data" :data="data" />
    </main>
  </div>
</template>

<style scoped>
.home {
  position: relative;
  z-index: 1;
  min-height: 100vh;
  min-height: 100dvh;
  display: flex;
  flex-direction: column;
}

.home__bar {
  position: absolute;
  inset: 0 0 auto 0;
  z-index: 30;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: clamp(16px, 2.6vw, 30px) clamp(18px, 4vw, 52px);
  pointer-events: none;
}

.home__brand,
.home__link,
.home__user {
  pointer-events: auto;
}

.home__brand {
  display: inline-flex;
  align-items: center;
  gap: 10px;
  padding: 8px 12px 8px 8px;
  border-radius: 999px;
  border: 1px solid rgb(142 178 255 / 0.16);
  background: rgb(10 15 26 / 0.5);
  backdrop-filter: blur(14px);
}

.home__logo {
  padding: 3px 9px;
  border-radius: 999px;
  background: linear-gradient(140deg, #3d8dff, #7b5cff);
  color: #fff;
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.14em;
}

.home__badge {
  font-family: var(--font-mono);
  font-size: 11px;
  letter-spacing: 0.22em;
  color: var(--color-accent);
}

.home__actions {
  display: inline-flex;
  align-items: center;
  gap: 14px;
}

.home__user {
  font-size: 13px;
  color: var(--color-ink-soft);
  max-width: 40vw;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.home__link {
  padding: 8px 14px;
  border-radius: 999px;
  border: 1px solid rgb(142 178 255 / 0.2);
  background: rgb(10 15 26 / 0.46);
  color: var(--color-ink-soft);
  font-size: 12px;
  letter-spacing: 0.06em;
  transition: border-color 200ms var(--ease-out), color 200ms var(--ease-out);
}

.home__link:hover {
  border-color: var(--color-line-strong);
  color: var(--color-ink);
}

.home__main {
  position: relative;
  flex: 1;
  min-height: 100vh;
  min-height: 100dvh;
}

.home__state {
  position: relative;
  z-index: 10;
  min-height: 100vh;
  min-height: 100dvh;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  padding: 120px clamp(22px, 6vw, 72px) 96px;
  text-align: center;
}

.home__state-kicker {
  font-family: var(--font-mono);
  font-size: 11px;
  letter-spacing: 0.28em;
  text-transform: uppercase;
  color: var(--color-accent);
}

.home__state-title {
  font-size: clamp(24px, 3.4vw, 40px);
  font-weight: 600;
  letter-spacing: -0.04em;
}

.home__state-text {
  max-width: 46ch;
  color: var(--color-ink-muted);
  font-size: 15px;
}

.home__state-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  margin-top: 14px;
}

.home__button {
  padding: 11px 20px;
  border-radius: 999px;
  border: 1px solid rgb(142 178 255 / 0.24);
  background: rgb(14 20 34 / 0.7);
  color: var(--color-ink-soft);
  font-size: 14px;
  cursor: pointer;
  transition: border-color 200ms var(--ease-out), color 200ms var(--ease-out),
    transform 200ms var(--ease-out);
}

.home__button:hover {
  border-color: var(--color-line-strong);
  color: var(--color-ink);
  transform: translateY(-1px);
}

.home__button--primary {
  border-color: transparent;
  background: linear-gradient(140deg, #3d8dff, #7b5cff);
  color: #fff;
}

.home__orbit {
  position: relative;
  width: 74px;
  height: 74px;
  margin-bottom: 8px;
  transform-style: preserve-3d;
  animation: orbit-spin 6s linear infinite;
}

.home__orbit span {
  position: absolute;
  inset: 0;
  border-radius: 50%;
  border: 1px solid rgb(109 180 255 / 0.4);
}

.home__orbit span:nth-child(2) {
  transform: rotateY(62deg);
  border-color: rgb(169 139 255 / 0.45);
}

.home__orbit span:nth-child(3) {
  transform: rotateX(62deg);
  border-color: rgb(79 214 196 / 0.4);
}

@keyframes orbit-spin {
  to {
    transform: rotate3d(1, 1, 0.4, 360deg);
  }
}

@media (prefers-reduced-motion: reduce) {
  .home__orbit {
    animation: none;
  }

  .home__button {
    transition: none;
  }
}
</style>
