<script lang="ts" setup>
import {
  ElButton,
  ElCol,
  ElEmpty,
  ElMessage,
  ElResult,
  ElRow,
  ElSegmented,
  ElStatistic
} from 'element-plus'
import { computed, ref, watch } from 'vue'
import { useWindowSize } from '@vueuse/core'
import { Download, Refresh } from '@element-plus/icons-vue'
import { Bar, Doughnut } from 'vue-chartjs'
import {
  Chart as ChartJS,
  Tooltip,
  Legend,
  BarElement,
  CategoryScale,
  LinearScale,
  ArcElement,
  Colors
} from 'chart.js'
import type { ChartData, ChartOptions } from 'chart.js'
import { useI18n } from 'vue-i18n'
import { ZButtonOrCard } from '@/components'
import UilStatistics from '@/icons/UilStatistics.vue'
import api from '@/api'
import {
  buildStatisticsCsv,
  formatBinLabel,
  mergeClassRows,
  statisticsFileName,
  type StatisticsCsvLabels
} from '@/utils/activityStatistics'
import type { ActivityStatistics, ActivityStatisticsDescription } from '@/../types'

ChartJS.register(Colors, Tooltip, Legend, BarElement, CategoryScale, LinearScale, ArcElement)

const props = withDefaults(
  defineProps<{
    aid: string
  }>(),
  {
    aid: ''
  }
)

const emits = defineEmits<{
  refresh: []
}>()

const { t } = useI18n()

const { width, height } = useWindowSize()

const STATS_CACHE_TTL_MS = 5 * 60 * 1000
const STATS_CACHE_MAX = 20
const CLASS_CHART_LIMIT = 12
const DISTRIBUTION_CHART_HEIGHT = 280

interface StatsCacheEntry {
  payload: ActivityStatistics
  cachedAt: number
}

const statsCache = new Map<string, StatsCacheEntry>()

function readCache(aid: string): ActivityStatistics | undefined {
  const entry = statsCache.get(aid)
  if (!entry) {
    return undefined
  }
  if (Date.now() - entry.cachedAt > STATS_CACHE_TTL_MS) {
    statsCache.delete(aid)
    return undefined
  }
  return entry.payload
}

function writeCache(aid: string, payload: ActivityStatistics) {
  statsCache.delete(aid)
  while (statsCache.size >= STATS_CACHE_MAX) {
    const oldest = statsCache.keys().next().value
    if (oldest === undefined) {
      break
    }
    statsCache.delete(oldest)
  }
  statsCache.set(aid, { payload, cachedAt: Date.now() })
}

const open = ref(false)
const loading = ref(false)
const error = ref(false)
const statistics = ref<ActivityStatistics | null>(null)

const hasData = computed(
  () =>
    statistics.value !== null && statistics.value.total > 0 && statistics.value.description !== null
)

type StatisticsTab = 'overview' | 'distribution' | 'groups'

const segmentOptions = computed(() => [
  { label: t('activity.statistics.tabs.overview'), value: 'overview' },
  { label: t('activity.statistics.tabs.distribution'), value: 'distribution' },
  { label: t('activity.statistics.tabs.groups'), value: 'groups' }
])
const activeTab = ref<StatisticsTab>('overview')

function descMetric(key: keyof ActivityStatisticsDescription): number {
  return statistics.value?.description?.[key] ?? 0
}

interface OverviewMetric {
  key: keyof ActivityStatisticsDescription
  titleKey: string
  unitKey: string
  precision: number
}

const overviewMetrics = computed<OverviewMetric[]>(() => [
  {
    key: 'total',
    titleKey: 'activity.statistics.metrics.total',
    unitKey: 'activity.units.person',
    precision: 0
  },
  {
    key: 'sum',
    titleKey: 'activity.statistics.metrics.sum',
    unitKey: 'activity.units.hour',
    precision: 1
  },
  {
    key: 'mean',
    titleKey: 'activity.statistics.metrics.mean',
    unitKey: 'activity.units.hour',
    precision: 1
  },
  {
    key: 'median',
    titleKey: 'activity.statistics.metrics.median',
    unitKey: 'activity.units.hour',
    precision: 1
  },
  {
    key: 'min',
    titleKey: 'activity.statistics.metrics.min',
    unitKey: 'activity.units.hour',
    precision: 1
  },
  {
    key: 'max',
    titleKey: 'activity.statistics.metrics.max',
    unitKey: 'activity.units.hour',
    precision: 1
  },
  {
    key: '25_percentile',
    titleKey: 'activity.statistics.metrics.p25',
    unitKey: 'activity.units.hour',
    precision: 1
  },
  {
    key: '75_percentile',
    titleKey: 'activity.statistics.metrics.p75',
    unitKey: 'activity.units.hour',
    precision: 1
  }
])

const modeLabels = computed<Record<string, string>>(() => ({
  'on-campus': t('activity.mode.on-campus.name'),
  'off-campus': t('activity.mode.off-campus.name'),
  'social-practice': t('activity.mode.social-practice.name')
}))

function modeName(mode: string): string {
  return modeLabels.value[mode] ?? mode
}

const csvLabels = computed<StatisticsCsvLabels>(() => ({
  overview: t('activity.statistics.csv.overview'),
  metric: t('activity.statistics.csv.metric'),
  value: t('activity.statistics.csv.value'),
  total: t('activity.statistics.metrics.total'),
  sum: t('activity.statistics.metrics.sum'),
  mean: t('activity.statistics.metrics.mean'),
  median: t('activity.statistics.metrics.median'),
  min: t('activity.statistics.metrics.min'),
  max: t('activity.statistics.metrics.max'),
  p25: t('activity.statistics.metrics.p25'),
  p75: t('activity.statistics.metrics.p75'),
  distribution: t('activity.statistics.csv.distribution'),
  range: t('activity.statistics.csv.range'),
  count: t('activity.statistics.groups.count'),
  modeGroup: t('activity.statistics.csv.modeGroup'),
  classGroup: t('activity.statistics.csv.classGroup'),
  mode: t('activity.statistics.groups.mode'),
  className: t('activity.statistics.groups.class'),
  unnamedClass: t('activity.statistics.groups.unnamedClass'),
  modeNames: modeLabels.value
}))

const durationChart = computed<ChartData<'bar'> | null>(() => {
  const stats = statistics.value
  if (!stats || stats.bins.length === 0) {
    return null
  }
  return {
    labels: stats.bins.map((bin) => formatBinLabel(bin.from, bin.to)),
    datasets: [{ data: stats.bins.map((bin) => bin.count) }]
  }
})

const durationOptions = computed<ChartOptions<'bar'>>(() => ({
  responsive: true,
  maintainAspectRatio: false,
  plugins: {
    legend: { display: false },
    tooltip: {
      callbacks: {
        label: (ctx) => `${ctx.parsed.y} ${t('activity.units.person')}`
      }
    }
  },
  scales: {
    x: {
      title: { display: true, text: t('activity.statistics.distribution.axisHours') },
      ticks: { autoSkip: true, maxRotation: 45 },
      grid: { display: false }
    },
    y: {
      title: { display: true, text: t('activity.statistics.distribution.axisPerson') },
      beginAtZero: true,
      ticks: { precision: 0 }
    }
  }
}))

const modeChart = computed<ChartData<'doughnut'> | null>(() => {
  const stats = statistics.value
  if (!stats || stats.modes.length === 0) {
    return null
  }
  return {
    labels: stats.modes.map((row) => modeName(row.mode)),
    datasets: [{ data: stats.modes.map((row) => row.count) }]
  }
})

const modeOptions = computed<ChartOptions<'doughnut'>>(() => ({
  responsive: true,
  plugins: {
    legend: { position: 'bottom', labels: { boxWidth: 12, boxHeight: 12 } },
    tooltip: {
      callbacks: {
        label: (ctx) => `${ctx.parsed} ${t('activity.units.person')}`,
        afterLabel: (ctx) => {
          const row = statistics.value?.modes[ctx.dataIndex]
          return row
            ? `${t('activity.statistics.groups.sum')}：${row.sum.toFixed(1)} ${t('activity.units.hour')}`
            : ''
        }
      }
    }
  }
}))

const classRows = computed(() =>
  mergeClassRows(
    statistics.value?.classes ?? [],
    CLASS_CHART_LIMIT,
    t('activity.statistics.groups.other')
  )
)

const classChartHeight = computed(() => Math.max(classRows.value.length, 1) * 32 + 48)

const classChart = computed<ChartData<'bar'> | null>(() => {
  if (classRows.value.length === 0) {
    return null
  }
  return {
    labels: classRows.value.map((row) =>
      row.group_name === '' ? t('activity.statistics.groups.unnamedClass') : row.group_name
    ),
    datasets: [{ data: classRows.value.map((row) => row.sum) }]
  }
})

const classOptions = computed<ChartOptions<'bar'>>(() => ({
  responsive: true,
  maintainAspectRatio: false,
  indexAxis: 'y',
  plugins: {
    legend: { display: false },
    tooltip: {
      callbacks: {
        label: (ctx) =>
          `${classRows.value[ctx.dataIndex]?.count ?? 0} ${t('activity.units.person')}`,
        afterLabel: (ctx) => {
          const row = classRows.value[ctx.dataIndex]
          return `${t('activity.statistics.groups.sum')}：${(row?.sum ?? 0).toFixed(1)} ${t('activity.units.hour')}`
        }
      }
    }
  },
  scales: {
    x: {
      title: {
        display: true,
        text: t('activity.statistics.groups.axisSum')
      },
      beginAtZero: true
    },
    y: { ticks: { autoSkip: false } }
  }
}))

async function load(aid: string, force = false) {
  if (!aid) {
    return
  }
  if (!force) {
    const cached = readCache(aid)
    if (cached) {
      statistics.value = cached
      return
    }
  }
  loading.value = true
  error.value = false
  try {
    statistics.value = await api.activity.stats.analysis(aid)
    writeCache(aid, statistics.value)
  } catch {
    statistics.value = null
    error.value = true
  } finally {
    loading.value = false
  }
}

function reload() {
  void load(props.aid, true)
  emits('refresh')
}

function exportCsv() {
  const stats = statistics.value
  if (!stats || !hasData.value) {
    return
  }
  const blob = new Blob([buildStatisticsCsv(stats, csvLabels.value)], {
    type: 'text/csv;charset=utf-8'
  })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = statisticsFileName(props.aid)
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  setTimeout(() => URL.revokeObjectURL(url), 0)
  ElMessage.success(t('activity.statistics.exportDone'))
}

watch(open, () => {
  if (open.value) {
    void load(props.aid)
  }
})

watch(
  () => props.aid,
  (value) => {
    statistics.value = null
    error.value = false
    if (value && open.value) {
      void load(value)
    }
  }
)
</script>

<template>
  <ZButtonOrCard
    class="px-2"
    v-model:open="open"
    mode="button"
    pop-type="dialog"
    :width="width < height ? '80%' : '50%'"
    type="warning"
    :icon="UilStatistics"
  >
    <template #default>
      <div v-loading="loading" class="min-h-40">
        <div class="mb-3 flex items-center justify-between gap-2">
          <ElSegmented v-model="activeTab" :options="segmentOptions" aria-label="统计维度切换" />
          <div class="flex items-center">
            <ElButton
              class="px-2"
              type="primary"
              :icon="Download"
              :disabled="loading || !hasData"
              text
              bg
              circle
              :title="t('activity.statistics.export')"
              :aria-label="t('activity.statistics.export')"
              @click="exportCsv"
            />
            <ElButton
              class="px-2"
              type="success"
              :icon="Refresh"
              :disabled="loading"
              text
              bg
              circle
              :title="t('activity.statistics.refresh')"
              :aria-label="t('activity.statistics.refresh')"
              @click="reload"
            />
          </div>
        </div>
        <ElResult
          v-if="error && !loading"
          icon="error"
          :title="t('activity.statistics.loadFailed')"
        >
          <template #extra>
            <ElButton
              type="warning"
              text
              bg
              round
              :icon="Refresh"
              :title="t('activity.statistics.retry')"
              :aria-label="t('activity.statistics.retry')"
              @click="reload"
            >
              {{ t('activity.statistics.retry') }}
            </ElButton>
          </template>
        </ElResult>
        <ElEmpty
          v-else-if="!loading && !hasData"
          :description="t('activity.statistics.empty')"
          :image-size="120"
        />
        <template v-else>
          <ElRow v-if="activeTab === 'overview'" class="stats-grid">
            <ElCol v-for="metric in overviewMetrics" :key="metric.key" :span="4" :sm="6" :xs="8">
              <ElStatistic
                :title="t(metric.titleKey)"
                :value="descMetric(metric.key)"
                :precision="metric.precision"
                :suffix="t(metric.unitKey)"
              />
            </ElCol>
          </ElRow>
          <div v-else-if="activeTab === 'distribution'">
            <div
              class="relative mx-auto"
              :style="{ height: DISTRIBUTION_CHART_HEIGHT + 'px', maxWidth: '640px' }"
            >
              <Bar
                v-if="durationChart"
                role="img"
                aria-label="活动参与时长分布柱状图"
                :data="durationChart"
                :options="durationOptions"
              />
            </div>
            <p class="mt-2 text-center text-xs text-gray-500">
              {{
                t('activity.statistics.distribution.percentile', {
                  p25: descMetric('25_percentile').toFixed(1),
                  p50: descMetric('median').toFixed(1),
                  p75: descMetric('75_percentile').toFixed(1)
                })
              }}
            </p>
          </div>
          <ElRow v-else :gutter="16">
            <ElCol :span="12" :xs="24">
              <p class="mb-2 text-center text-sm text-gray-500">
                {{ t('activity.statistics.groups.mode') }}
              </p>
              <div class="relative mx-auto" style="max-width: 320px">
                <Doughnut
                  v-if="modeChart"
                  role="img"
                  aria-label="活动参与模式占比环形图"
                  :data="modeChart"
                  :options="modeOptions"
                />
                <div
                  v-if="statistics"
                  class="pointer-events-none absolute inset-0 grid place-items-center"
                  aria-hidden="true"
                >
                  <span class="text-center">
                    <span class="block text-2xl font-semibold text-gray-700">
                      {{ statistics.total }}
                    </span>
                    <span class="block text-xs text-gray-400">
                      {{ t('activity.statistics.metrics.total') }}
                    </span>
                  </span>
                </div>
              </div>
            </ElCol>
            <ElCol :span="12" :xs="24">
              <p class="mb-2 text-center text-sm text-gray-500">
                {{ t('activity.statistics.groups.class') }}
              </p>
              <div class="relative" :style="{ height: classChartHeight + 'px' }">
                <Bar
                  v-if="classChart"
                  role="img"
                  aria-label="活动班级总时长对比横向柱状图（按后端统计顺序，未分班垫底）"
                  :data="classChart"
                  :options="classOptions"
                />
              </div>
            </ElCol>
          </ElRow>
        </template>
      </div>
    </template>
    <template #text> {{ t('activity.statistics.name') }} </template>
  </ZButtonOrCard>
</template>

<style scoped>
.stats-grid :deep(.el-statistic__number) {
  font-variant-numeric: tabular-nums;
}

.stats-grid :deep(.el-statistic__head) {
  color: var(--el-text-color-secondary);
}

.stats-grid .el-col {
  margin-bottom: 8px;
}
</style>
