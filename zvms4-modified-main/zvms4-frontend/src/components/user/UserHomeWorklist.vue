<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElButton, ElCard, ElEmpty, ElIcon, ElPagination, ElSkeleton, ElTag } from 'element-plus'
import { Refresh, WarningFilled, Clock, CircleCheck, CircleClose } from '@element-plus/icons-vue'
import dayjs from '@/plugins/dayjs'
import api from '@/api'
import { useRouter } from 'vue-router'
import type { Activity } from '@/../types/v2'
import type { AiRun } from '@/../types/ai'
import type { Log } from '@/../types/log'

const props = defineProps<{ isAdmin: boolean }>()
const { t } = useI18n()
const router = useRouter()
const LOG_TYPE_LABELS: Record<string, string> = {
  risk_alert: '风险告警',
  risk_ban: '风险封禁',
  security_alert: '安全告警',
  platform_misuse_violation: '平台违规告警',
  agent_jailbreak_warning: 'ZVAgent 越狱告警'
}

const page = ref(1)
const perPage = 10
const activities = ref<(Activity & { creator_name?: string })[]>([])
const activityTotal = ref(0)
const activityLoading = ref(false)
const activityError = ref(false)

const runs = ref<(AiRun & { result?: string })[]>([])
const runsLoading = ref(false)
const runsError = ref(false)

const suspiciousTypes = [
  'risk_alert',
  'risk_ban',
  'security_alert',
  'platform_misuse_violation',
  'agent_jailbreak_warning'
]
const logs = ref<Log[]>([])
const logsLoading = ref(false)
const logsError = ref(false)

const statusIcon = computed(() => ({
  queued: Clock,
  running: Refresh,
  succeeded: CircleCheck,
  failed: CircleClose,
  cancelled: CircleClose,
  interrupted: WarningFilled
}))

async function loadActivities() {
  activityLoading.value = true
  activityError.value = false
  try {
    const result = await api.activity.read.pending(page.value, perPage)
    activities.value = result.activities
    activityTotal.value = result.total
  } catch {
    activityError.value = true
  } finally {
    activityLoading.value = false
  }
}

async function loadRuns() {
  runsLoading.value = true
  runsError.value = false
  try {
    const recent = await api.ai.listRecentRuns(5)
    runs.value = await Promise.all(
      recent.map(async (run) => {
        if (!run.result_message_oid) return { ...run, result: '' }
        try {
          const conversation = await api.ai.getConversation(run.conversation_oid)
          const afterSeq = Math.max(0, conversation.message_count - 100)
          const messagePage = await api.ai.listMessages(run.conversation_oid, {
            afterSeq,
            limit: 100
          })
          const resultMessage = messagePage.messages.find(
            (message) => message.oid === run.result_message_oid
          )
          return { ...run, result: resultMessage?.content ?? '' }
        } catch {
          return { ...run, result: '' }
        }
      })
    )
  } catch {
    runsError.value = true
  } finally {
    runsLoading.value = false
  }
}

async function loadLogs() {
  logsLoading.value = true
  logsError.value = false
  try {
    const result = await api.logs.read(1, 5, '', '', '', { types: suspiciousTypes })
    logs.value = result?.data ?? []
  } catch {
    logsError.value = true
  } finally {
    logsLoading.value = false
  }
}

function openActivity(activity: Activity) {
  void router.push(`/activity/details/${encodeURIComponent(activity._id)}`)
}

function openAi() {
  void router.push('/ai')
}

function openLogs() {
  void router.push('/manage/logs')
}

function logTypeLabel(type?: string): string {
  return type ? (LOG_TYPE_LABELS[type] ?? type) : '可疑事件'
}

onMounted(() => {
  if (!props.isAdmin) void loadActivities()
  void loadRuns()
  if (props.isAdmin) void loadLogs()
})
</script>

<template>
  <div class="home-worklist">
    <ElCard v-if="!isAdmin" shadow="never" class="home-worklist__card">
      <header class="home-worklist__header">
        <div>
          <h2>{{ t('home.panels.review.title') }}</h2>
          <p>{{ t('home.panels.review.description') }}</p>
        </div>
        <ElButton
          text
          bg
          circle
          :icon="Refresh"
          :disabled="activityLoading"
          :aria-label="t('home.panels.review.retry')"
          @click="loadActivities"
        />
      </header>
      <ElSkeleton v-if="activityLoading && !activities.length" :rows="3" animated />
      <div v-else-if="activityError" class="home-worklist__error" role="alert">
        <span>{{ t('home.panels.review.loadFailed') }}</span>
        <ElButton text type="primary" @click="loadActivities">{{
          t('home.panels.review.retry')
        }}</ElButton>
      </div>
      <ElEmpty
        v-else-if="!activities.length"
        :description="t('home.panels.review.emptyDescription')"
        :image-size="56"
      >
        <template #default
          ><span>{{ t('home.panels.review.empty') }}</span></template
        >
      </ElEmpty>
      <ul v-else class="home-worklist__items" :aria-busy="activityLoading">
        <li v-for="activity in activities" :key="activity._id">
          <button class="home-worklist__row" type="button" @click="openActivity(activity)">
            <span class="home-worklist__main">
              <strong>{{ activity.name }}</strong>
              <small
                >{{ t('home.panels.review.creator') }}：{{
                  activity.creator_name || activity.creator || '—'
                }}</small
              >
            </span>
            <span class="home-worklist__meta">
              <ElTag type="warning" effect="plain">{{ t('activity.status.pending') }}</ElTag>
              <small>{{ dayjs(activity.date).format('YYYY-MM-DD') }}</small>
            </span>
          </button>
        </li>
      </ul>
      <ElPagination
        v-if="activityTotal > perPage"
        class="home-worklist__pagination"
        small
        background
        layout="prev, pager, next"
        :current-page="page"
        :page-size="perPage"
        :total="activityTotal"
        @current-change="
          (next: number) => {
            page = next
            loadActivities()
          }
        "
      />
    </ElCard>

    <ElCard shadow="never" class="home-worklist__card">
      <header class="home-worklist__header">
        <div>
          <h2>{{ t('home.panels.agent.title') }}</h2>
          <p>{{ t('home.panels.agent.description') }}</p>
        </div>
        <ElButton
          text
          bg
          circle
          :icon="Refresh"
          :disabled="runsLoading"
          :aria-label="t('home.panels.agent.retry')"
          @click="loadRuns"
        />
      </header>
      <ElSkeleton v-if="runsLoading && !runs.length" :rows="3" animated />
      <div v-else-if="runsError" class="home-worklist__error" role="alert">
        <span>{{ t('home.panels.agent.loadFailed') }}</span>
        <ElButton text type="primary" @click="loadRuns">{{
          t('home.panels.agent.retry')
        }}</ElButton>
      </div>
      <ElEmpty
        v-else-if="!runs.length"
        :description="t('home.panels.agent.emptyDescription')"
        :image-size="56"
      >
        <template #default
          ><span>{{ t('home.panels.agent.empty') }}</span></template
        >
      </ElEmpty>
      <ul v-else class="home-worklist__items">
        <li v-for="run in runs" :key="run.oid">
          <button class="home-worklist__row" type="button" @click="openAi">
            <span class="home-worklist__main">
              <strong>{{ run.result || run.error_message || run.stop_reason || run.oid }}</strong>
              <small
                >{{ dayjs(run.created_at).format('YYYY-MM-DD HH:mm') }} ·
                {{ t('home.panels.agent.rounds', { count: run.rounds }) }}</small
              >
            </span>
            <ElTag
              :type="
                run.status === 'succeeded' ? 'success' : run.status === 'failed' ? 'danger' : 'info'
              "
              effect="plain"
            >
              <ElIcon><component :is="statusIcon[run.status]" /></ElIcon>
              {{ t(`home.panels.agent.statuses.${run.status}`) }}
            </ElTag>
          </button>
        </li>
      </ul>
    </ElCard>

    <ElCard v-if="isAdmin" shadow="never" class="home-worklist__card">
      <header class="home-worklist__header">
        <div>
          <h2>{{ t('home.panels.suspiciousLogs.title') }}</h2>
          <p>{{ t('home.panels.suspiciousLogs.description') }}</p>
        </div>
        <ElButton
          text
          bg
          circle
          :icon="Refresh"
          :disabled="logsLoading"
          :aria-label="t('home.panels.suspiciousLogs.retry')"
          @click="loadLogs"
        />
      </header>
      <ElSkeleton v-if="logsLoading && !logs.length" :rows="3" animated />
      <div v-else-if="logsError" class="home-worklist__error" role="alert">
        <span>{{ t('home.panels.suspiciousLogs.loadFailed') }}</span>
        <ElButton text type="primary" @click="loadLogs">{{
          t('home.panels.suspiciousLogs.retry')
        }}</ElButton>
      </div>
      <ElEmpty
        v-else-if="!logs.length"
        :description="t('home.panels.suspiciousLogs.emptyDescription')"
        :image-size="56"
      >
        <template #default
          ><span>{{ t('home.panels.suspiciousLogs.empty') }}</span></template
        >
      </ElEmpty>
      <ul v-else class="home-worklist__items">
        <li v-for="log in logs" :key="log._id">
          <button class="home-worklist__row" type="button" @click="openLogs">
            <span class="home-worklist__main">
              <strong>{{ logTypeLabel(log.type) }}</strong>
              <small
                >{{ log.user_name || log.user_id || log.user || '—' }} ·
                {{ dayjs.unix(log.timestamp).format('YYYY-MM-DD HH:mm') }}</small
              >
              <span class="home-worklist__detail">{{ log.data }}</span>
            </span>
            <ElTag type="warning" effect="plain">{{ log.level || 'warning' }}</ElTag>
          </button>
        </li>
      </ul>
    </ElCard>
  </div>
</template>

<style scoped>
.home-worklist {
  display: grid;
  gap: var(--yg-space-6, 24px);
}

/* 与「义工时间」面板同一套外框：不对称切角 + 扫描线底 + 硬阴影包进 clip 多边形，
   再用 ::before 补切角斜线、左侧强调条与右下角括号。 */
.home-worklist__card {
  --hw-cut: 18px;
  --hw-d: 6px;
  position: relative;
  overflow: hidden;
  border: 1px solid var(--phi-frame, var(--yg-border-default, #e5e7eb));
  border-radius: 0;
  background:
    linear-gradient(var(--phi-scan, rgba(73, 244, 226, 0.035)) 1px, transparent 1px) 0 0 / 100% 3px,
    linear-gradient(115deg, var(--phi-panel-hi, #12171b), var(--phi-panel, #0b0e12) 58%);
  box-shadow: var(--hw-d) var(--hw-d) 0 var(--phi-shadow, rgba(73, 244, 226, 0.16));
  clip-path: polygon(
    0 0,
    calc(100% - var(--hw-cut)) 0,
    calc(100% + var(--hw-d)) calc(var(--hw-cut) + var(--hw-d)),
    calc(100% + var(--hw-d)) calc(100% + var(--hw-d)),
    calc(var(--hw-cut) + var(--hw-d)) calc(100% + var(--hw-d)),
    0 calc(100% - var(--hw-cut))
  );
  animation: home-card-reveal 560ms cubic-bezier(0.16, 1, 0.3, 1) 120ms backwards;
}

.home-worklist__card::before {
  content: '';
  position: absolute;
  inset: 0;
  pointer-events: none;
  background:
    linear-gradient(
        to bottom left,
        transparent calc(50% - 1px),
        var(--phi-frame, rgba(244, 246, 248, 0.2)) calc(50% - 1px),
        var(--phi-frame, rgba(244, 246, 248, 0.2)) calc(50% + 1px),
        transparent calc(50% + 1px)
      )
      100% 0 / var(--hw-cut) var(--hw-cut) no-repeat,
    linear-gradient(
        to top right,
        transparent calc(50% - 1px),
        var(--phi-frame, rgba(244, 246, 248, 0.2)) calc(50% - 1px),
        var(--phi-frame, rgba(244, 246, 248, 0.2)) calc(50% + 1px),
        transparent calc(50% + 1px)
      )
      0 100% / var(--hw-cut) var(--hw-cut) no-repeat,
    linear-gradient(var(--phi-accent, var(--yg-color-primary, #0b9f93)), var(--phi-accent, var(--yg-color-primary, #0b9f93)))
      0 0 / 3px 46% no-repeat,
    linear-gradient(var(--phi-accent, var(--yg-color-primary, #0b9f93)), var(--phi-accent, var(--yg-color-primary, #0b9f93)))
      calc(100% - 10px) calc(100% - 10px) / 18px 2px no-repeat,
    linear-gradient(var(--phi-accent, var(--yg-color-primary, #0b9f93)), var(--phi-accent, var(--yg-color-primary, #0b9f93)))
      calc(100% - 10px) calc(100% - 10px) / 2px 18px no-repeat;
}

@keyframes home-card-reveal {
  from {
    clip-path: polygon(0 0, 0 0, 0 0, 0 100%, 0 100%, 0 100%);
  }
}

.home-worklist__header,
.home-worklist__row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--yg-space-4, 16px);
}

.home-worklist__header {
  margin-bottom: var(--yg-space-4, 16px);
}

.home-worklist__header h2 {
  margin: 0;
  color: var(--yg-text-primary, #111827);
  font-size: var(--yg-font-size-2xl, 1.25rem);
  font-weight: 650;
}

.home-worklist__header p,
.home-worklist__main small {
  margin: 4px 0 0;
  color: var(--yg-text-tertiary, #64748b);
  font-size: 0.875rem;
}

.home-worklist__items {
  margin: 0;
  padding: 0;
  list-style: none;
}

/* 行数据出现：与表格列表同一套逐行错峰擦入 */
.home-worklist__items li {
  animation: phi-home-row-in 360ms cubic-bezier(0.19, 1, 0.22, 1) backwards;
  animation-delay: var(--home-row-delay, 0ms);
}

.home-worklist__items li:nth-of-type(2) {
  --home-row-delay: 30ms;
}

.home-worklist__items li:nth-of-type(3) {
  --home-row-delay: 60ms;
}

.home-worklist__items li:nth-of-type(4) {
  --home-row-delay: 90ms;
}

.home-worklist__items li:nth-of-type(5) {
  --home-row-delay: 120ms;
}

.home-worklist__items li:nth-of-type(n + 6) {
  --home-row-delay: 160ms;
}

.home-worklist__items li + li {
  border-top: 1px solid var(--phi-line, var(--yg-border-subtle, #e5e7eb));
}

.home-worklist__row {
  width: 100%;
  min-height: 52px;
  padding: 10px 4px;
  border: 0;
  border-radius: 0;
  background: transparent;
  color: inherit;
  text-align: left;
  cursor: pointer;
  transition:
    background-color 180ms var(--yg-ease-out, cubic-bezier(0.25, 0.1, 0.25, 1)),
    box-shadow 180ms var(--yg-ease-out, cubic-bezier(0.25, 0.1, 0.25, 1));
}

/* 与标准列表行一致：青色淡底 + 左缘强调线 */
.home-worklist__row:hover {
  background: color-mix(
    in srgb,
    var(--phi-accent, var(--yg-color-primary, #0b9f93)) 9%,
    transparent
  );
  box-shadow: inset 3px 0 0 var(--phi-accent, var(--yg-color-primary, #0b9f93));
}

@keyframes phi-home-row-in {
  from {
    opacity: 0;
    clip-path: inset(0 100% 0 0);
  }
  to {
    opacity: 1;
    clip-path: inset(0 0 0 0);
  }
}

.home-worklist__row:focus-visible {
  outline: 3px solid var(--yg-focus-ring, #38bdf8);
  outline-offset: 2px;
}

.home-worklist__main {
  display: grid;
  min-width: 0;
  gap: 3px;
}

.home-worklist__main strong,
.home-worklist__detail {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.home-worklist__meta {
  display: grid;
  justify-items: end;
  gap: 4px;
  flex: none;
}

.home-worklist__meta small {
  color: var(--yg-text-tertiary, #64748b);
}

.home-worklist__detail {
  max-width: 100%;
  color: var(--yg-text-secondary, #475569);
  font-size: 0.875rem;
}

.home-worklist__error {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  color: var(--yg-text-danger, #b91c1c);
}

.home-worklist__pagination {
  --el-pagination-bg-color: transparent;
  --el-pagination-button-bg-color: transparent;
  --el-pagination-hover-color: var(--yg-color-primary, #0b9f93);
  justify-content: center;
  margin-top: var(--yg-space-4, 16px);
}

/* 无框页码：普通页透明，当前页只留文字强调 + 底部细标记 */
.home-worklist__pagination.home-worklist__pagination :deep(.btn-prev),
.home-worklist__pagination.home-worklist__pagination :deep(.btn-next),
.home-worklist__pagination.home-worklist__pagination :deep(.el-pager li) {
  border: 0;
  border-radius: 0;
  background: transparent;
  color: var(--yg-text-secondary, #475569);
}

.home-worklist__pagination.home-worklist__pagination :deep(.el-pager li:hover) {
  color: var(--yg-color-primary, #0b9f93);
}

.home-worklist__pagination.home-worklist__pagination :deep(.el-pager li.is-active) {
  color: var(--yg-color-primary, #0b9f93);
  background: transparent;
  box-shadow: inset 0 -2px 0 var(--yg-color-primary, #0b9f93);
  clip-path: none;
}

@media (max-width: 768px) {
  .home-worklist__row {
    align-items: flex-start;
  }

  .home-worklist__detail {
    white-space: normal;
  }
}

@media (prefers-reduced-motion: reduce) {
  .home-worklist__card,
  .home-worklist__items li {
    animation: none;
  }

  .home-worklist__row {
    transition: none;
  }
}
</style>
