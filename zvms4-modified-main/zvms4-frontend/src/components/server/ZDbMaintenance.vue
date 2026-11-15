<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import {
  DB_ACTIONS,
  buildDbPlan,
  getDbOverview,
  getDbProgress,
  getDbSuggestions,
  runDbMaintenance,
  type DbMaintenanceAction,
  type DbOverview,
  type DbPlan,
  type DbSuggestion
} from '@/api/next/system/dbMaintenance'
import { errorMessage } from '@/utils/errorMessage'
import Skeleton from '@/ui/ux/Skeleton.vue'

/**
 * 数据库维护中心：体检 → 看建议 → 生成计划（只出 SQL）→ 执行 → 看进度。
 *
 * 执行前必须先在计划里审阅 SQL，再点「确认执行」；执行走后台任务，
 * 这里用 1.5s 轮询进度，作业结束后自动刷新一次体检。
 */
const overview = ref<DbOverview | null>(null)
const suggestions = ref<DbSuggestion[]>([])
const loading = ref(true)
const errorText = ref('')

const action = ref<DbMaintenanceAction>('gather_stats')
const plan = ref<DbPlan | null>(null)
const planLoading = ref(false)
const planError = ref('')

const running = ref(false)
const jobId = ref('')
const progress = ref('')
const runError = ref('')

let timer: ReturnType<typeof setTimeout> | null = null

const tablesSize = computed(() => {
  const rows = overview.value?.tables ?? []
  const total = rows.reduce((sum, item) => sum + (item.size_bytes ?? 0), 0)
  return formatBytes(total)
})

const staleCount = computed(
  () => (overview.value?.tables ?? []).filter((item) => item.stale).length
)

function formatBytes(value: number): string {
  if (!value) {
    return '—'
  }
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let size = value
  let index = 0
  while (size >= 1024 && index < units.length - 1) {
    size /= 1024
    index += 1
  }
  return `${size.toFixed(size >= 10 || index === 0 ? 0 : 1)} ${units[index]}`
}

async function load() {
  loading.value = true
  errorText.value = ''
  try {
    const [ov, sg] = await Promise.all([
      getDbOverview(),
      getDbSuggestions().catch(() => [])
    ])
    overview.value = ov
    suggestions.value = sg
  } catch (error) {
    errorText.value = errorMessage(error, '读取数据库体检失败')
  } finally {
    loading.value = false
  }
}

async function previewPlan() {
  planLoading.value = true
  planError.value = ''
  runError.value = ''
  try {
    plan.value = await buildDbPlan(action.value)
  } catch (error) {
    plan.value = null
    planError.value = errorMessage(error, '生成计划失败')
  } finally {
    planLoading.value = false
  }
}

async function execute() {
  running.value = true
  runError.value = ''
  progress.value = '已提交，等待执行…'
  try {
    const job = await runDbMaintenance(action.value)
    jobId.value = job.job_id
    poll()
  } catch (error) {
    running.value = false
    runError.value = errorMessage(error, '启动维护作业失败')
  }
}

function poll() {
  if (timer) {
    clearTimeout(timer)
  }
  timer = setTimeout(async () => {
    try {
      const data = await getDbProgress(jobId.value)
      const total = Number(data.total ?? 0)
      const finished = Number(data.finished ?? 0)
      progress.value = total
        ? `执行中 ${finished}/${total}`
        : (data.message ?? data.state ?? '执行中…')
      if (data.state && /done|finished|failed|success|error/i.test(String(data.state))) {
        running.value = false
        progress.value = data.message ?? `作业结束：${data.state}`
        void load()
        return
      }
    } catch (error) {
      running.value = false
      runError.value = errorMessage(error, '读取进度失败')
      return
    }
    poll()
  }, 1500)
}

onMounted(load)
onBeforeUnmount(() => {
  if (timer) {
    clearTimeout(timer)
  }
})
</script>

<template>
  <section class="dbm">
    <header class="dbm__head">
      <div>
        <h2 class="dbm__title">数据库维护</h2>
        <p class="dbm__desc">
          体检只读、计划只出 SQL；真正执行会开后台作业，必须在计划里审阅后再点确认。
        </p>
      </div>
      <button class="yg2-btn yg2-btn--secondary yg2-btn--sm" type="button" :disabled="loading" @click="load">
        重新体检
      </button>
    </header>

    <p v-if="errorText" class="dbm__error">{{ errorText }}</p>

    <div v-if="loading" class="dbm__cards">
      <Skeleton v-for="i in 4" :key="i" height="72px" radius="var(--yg2-r-12)" />
    </div>
    <div v-else class="dbm__cards">
      <div class="dbm__card">
        <span class="dbm__card-label">表数量</span>
        <strong class="dbm__card-value">{{ overview?.table_count ?? '—' }}</strong>
      </div>
      <div class="dbm__card">
        <span class="dbm__card-label">总占用</span>
        <strong class="dbm__card-value">{{ tablesSize }}</strong>
      </div>
      <div class="dbm__card">
        <span class="dbm__card-label">统计过期表</span>
        <strong class="dbm__card-value" :class="{ 'is-warn': staleCount > 0 }">{{ staleCount }}</strong>
      </div>
      <div class="dbm__card">
        <span class="dbm__card-label">优化建议</span>
        <strong class="dbm__card-value">{{ suggestions.length }}</strong>
      </div>
    </div>

    <div v-if="overview?.warnings?.length" class="dbm__warn">
      <span v-for="(warn, index) in overview.warnings" :key="index">{{ warn }}</span>
    </div>

    <div class="dbm__grid">
      <div class="dbm__panel">
        <h3 class="dbm__panel-title">优化建议</h3>
        <p v-if="!suggestions.length" class="dbm__empty">暂时没有需要处理的建议。</p>
        <ul v-else class="dbm__list">
          <li v-for="(item, index) in suggestions.slice(0, 8)" :key="index" class="dbm__item">
            <span class="dbm__severity" :class="`is-${item.severity ?? 'low'}`">{{ item.severity ?? 'low' }}</span>
            <span class="dbm__item-text">{{ item.message ?? item.kind ?? '未命名建议' }}</span>
            <span v-if="item.table" class="dbm__item-table">{{ item.table }}</span>
          </li>
        </ul>
      </div>

      <div class="dbm__panel">
        <h3 class="dbm__panel-title">维护作业</h3>
        <div class="dbm__form">
          <select v-model="action" class="dbm__select">
            <option v-for="item in DB_ACTIONS" :key="item.value" :value="item.value">
              {{ item.label }}
            </option>
          </select>
          <button class="yg2-btn yg2-btn--secondary yg2-btn--sm" type="button" :disabled="planLoading" @click="previewPlan">
            {{ planLoading ? '生成中…' : '生成计划' }}
          </button>
        </div>
        <p class="dbm__hint">
          {{ DB_ACTIONS.find((item) => item.value === action)?.hint }}
        </p>

        <p v-if="planError" class="dbm__error">{{ planError }}</p>

        <div v-if="plan" class="dbm__plan">
          <p v-if="plan.note" class="dbm__note">{{ plan.note }}</p>
          <ol v-if="plan.items.length" class="dbm__sql">
            <li v-for="(item, index) in plan.items" :key="index">
              <span class="dbm__sql-table">{{ item.table }}</span>
              <code>{{ item.sql }}</code>
            </li>
          </ol>
          <p v-else class="dbm__empty">这次计划没有需要执行的 SQL。</p>

          <div class="dbm__actions">
            <button
              class="yg2-btn yg2-btn--danger yg2-btn--sm"
              type="button"
              :disabled="running || !plan.items.length"
              @click="execute"
            >
              {{ running ? '执行中…' : '确认执行' }}
            </button>
            <span v-if="progress" class="dbm__progress">{{ progress }}</span>
          </div>
          <p v-if="runError" class="dbm__error">{{ runError }}</p>
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.dbm {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.dbm__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}

.dbm__title {
  margin: 0 0 4px;
  font-size: 16px;
  font-weight: 600;
}

.dbm__desc {
  margin: 0;
  font-size: 12.5px;
  color: var(--yg2-text-secondary);
}

.dbm__error {
  margin: 0;
  color: var(--yg2-danger);
  font-size: 13px;
}

.dbm__cards {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  gap: 10px;
}

.dbm__card {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 12px 14px;
  border-radius: var(--yg2-r-12);
  background: var(--yg2-surface-sunken);
}

.dbm__card-label {
  font-size: 12px;
  color: var(--yg2-text-secondary);
}

.dbm__card-value {
  font-size: 20px;
  font-weight: 600;
  letter-spacing: -0.01em;
}

.dbm__card-value.is-warn {
  color: var(--yg2-cat-school);
}

.dbm__warn {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 10px 12px;
  border-radius: var(--yg2-r-12);
  background: var(--yg2-act-bg);
  color: var(--yg2-act-text);
  font-size: 12.5px;
}

.dbm__grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: 12px;
}

@media (max-width: 900px) {
  .dbm__grid {
    grid-template-columns: minmax(0, 1fr);
  }
}

.dbm__panel {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 14px;
  border: 1px solid var(--yg2-line);
  border-radius: var(--yg2-r-16);
}

.dbm__panel-title {
  margin: 0;
  font-size: 14px;
  font-weight: 600;
}

.dbm__list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.dbm__item {
  display: flex;
  align-items: baseline;
  gap: 8px;
  font-size: 12.5px;
}

.dbm__severity {
  flex: none;
  padding: 1px 7px;
  border-radius: 999px;
  background: var(--yg2-surface-sunken);
  color: var(--yg2-text-secondary);
  font-size: 11px;
  text-transform: uppercase;
}

.dbm__severity.is-high {
  background: var(--yg2-act-danger-bg);
  color: var(--yg2-act-danger-text);
}

.dbm__severity.is-medium {
  background: var(--yg2-act-info-bg);
  color: var(--yg2-act-info-text);
}

.dbm__item-text {
  flex: 1 1 auto;
  min-width: 0;
}

.dbm__item-table {
  flex: none;
  color: var(--yg2-text-tertiary);
  font-family: var(--yg2-font-mono, ui-monospace, monospace);
  font-size: 11.5px;
}

.dbm__form {
  display: flex;
  gap: 8px;
}

.dbm__select {
  flex: 1 1 auto;
  height: 32px;
  padding: 0 10px;
  border: 1px solid var(--yg2-line);
  border-radius: 8px;
  background: var(--yg2-surface-sunken);
  color: var(--yg2-text);
  font: inherit;
  font-size: 13px;
}

.dbm__hint,
.dbm__note {
  margin: 0;
  font-size: 12.5px;
  color: var(--yg2-text-secondary);
}

.dbm__plan {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.dbm__sql {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin: 0;
  padding-left: 18px;
  max-height: 220px;
  overflow: auto;
  font-size: 12px;
}

.dbm__sql code {
  display: block;
  margin-top: 2px;
  padding: 6px 8px;
  border-radius: 8px;
  background: var(--yg2-surface-sunken);
  font-family: var(--yg2-font-mono, ui-monospace, monospace);
  font-size: 11.5px;
  white-space: pre-wrap;
  word-break: break-all;
}

.dbm__sql-table {
  color: var(--yg2-text-secondary);
}

.dbm__actions {
  display: flex;
  align-items: center;
  gap: 10px;
}

.dbm__progress {
  font-size: 12.5px;
  color: var(--yg2-text-secondary);
}

.dbm__empty {
  margin: 0;
  font-size: 12.5px;
  color: var(--yg2-text-tertiary);
}
</style>
