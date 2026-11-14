<script lang="ts" setup>
import {
  ElButton,
  ElCol,
  ElEmpty,
  ElIcon,
  ElMessage,
  ElResult,
  ElRow,
  ElStatistic,
  ElTag
} from 'element-plus'
import { computed, ref, watch } from 'vue'
import { useWindowSize } from '@vueuse/core'
import { ArrowDown, Download, Refresh } from '@element-plus/icons-vue'
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
import { categoryColor, categoryHover } from '@/utils/categoryColors'
import { useTheme } from '@/utils/theme'
import { ZButtonOrCard } from '@/components'
import UilStatistics from '@/icons/UilStatistics.vue'
import api from '@/api'
import { useChartTheme } from '@/utils/chartTheme'
import {
  buildStatisticsCsv,
  formatBinLabel,
  orderClassRowsForChart,
  statisticsFileName,
  type StatisticsCsvLabels
} from '@/utils/activityStatistics'
import type {
  ActivityStatistics,
  ActivityStatisticsClassGroup,
  ActivityStatisticsDescription,
  StatisticsMemberRef
} from '@/../types'

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
const chartTheme = useChartTheme()

const STATS_CACHE_TTL_MS = 5 * 60 * 1000
const STATS_CACHE_MAX = 20
const DISTRIBUTION_CHART_HEIGHT = 280
const ROSTER_TOOLTIP_LIMIT = 6

// 三类义工统一走语义令牌；依赖主题，切换时重新取色
const { dark: themeDark } = useTheme()

const MODE_COLORS = computed<Record<string, { base: string; hover: string }>>(() => {
  void themeDark.value
  return {
    'on-campus': { base: categoryColor('on-campus'), hover: categoryHover('on-campus') },
    'off-campus': { base: categoryColor('off-campus'), hover: categoryHover('off-campus') },
    'social-practice': {
      base: categoryColor('social-practice'),
      hover: categoryHover('social-practice')
    }
  }
})
const FALLBACK_MODE_COLOR = computed(() => {
  void themeDark.value
  return { base: categoryColor('on-campus'), hover: categoryHover('on-campus') }
})

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
    key: 'mode',
    titleKey: 'activity.statistics.metrics.mode',
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
  },
  {
    key: 'std',
    titleKey: 'activity.statistics.metrics.std',
    unitKey: 'activity.units.hour',
    precision: 1
  }
])

const modeLabels = computed<Record<string, string>>(() => ({
  'on-campus': t('activity.mode.on-campus.name'),
  'off-campus': t('activity.mode.off-campus.name'),
  'social-practice': t('activity.mode.social-practice.name'),
  hybrid: t('activity.mode.hybrid.name')
}))

function modeName(mode: string): string {
  return modeLabels.value[mode] ?? mode
}

function formatHours(value: number): string {
  return value.toFixed(1)
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
  unknownClass: t('activity.statistics.groups.unknownClass'),
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

const peakBin = computed(() => {
  const stats = statistics.value
  if (!stats || stats.bins.length === 0) {
    return null
  }
  return stats.bins.reduce((best, bin) => (bin.count > best.count ? bin : best), stats.bins[0])
})

const binWidthLabel = computed(() => {
  const binWidth = statistics.value?.bin_width ?? 0
  return String(Math.round(binWidth * 1e6) / 1e6)
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
      title: {
        display: true,
        text: t('activity.statistics.distribution.axisHours'),
        color: chartTheme.value.text
      },
      ticks: {
        autoSkip: true,
        maxRotation: 45,
        color: chartTheme.value.muted
      },
      grid: { display: false }
    },
    y: {
      title: {
        display: true,
        text: t('activity.statistics.distribution.axisPerson'),
        color: chartTheme.value.text
      },
      beginAtZero: true,
      ticks: { precision: 0, color: chartTheme.value.muted },
      grid: { color: chartTheme.value.grid }
    }
  }
}))

interface ModeRow {
  mode: string
  count: number
  sum: number
  mean: number
  color: string
  hoverColor: string
  members: StatisticsMemberRef[] | null
}

const hasModeRoster = computed(() => {
  const modeMembers = statistics.value?.mode_members
  return modeMembers !== undefined && Object.keys(modeMembers).length > 0
})

const modeRows = computed<ModeRow[]>(() => {
  const stats = statistics.value
  if (!stats) {
    return []
  }
  const modeMembers = stats.mode_members ?? null
  return stats.modes
    .map((row) => ({
      mode: row.mode,
      count: row.count,
      sum: row.sum,
      mean: row.mean,
      color: MODE_COLORS.value[row.mode]?.base ?? FALLBACK_MODE_COLOR.value.base,
      hoverColor: MODE_COLORS.value[row.mode]?.hover ?? FALLBACK_MODE_COLOR.value.hover,
      members: modeMembers ? (modeMembers[row.mode] ?? []) : null
    }))
    .sort((a, b) => a.count - b.count)
})

const expandedMode = ref<string | null>(null)

function toggleModeRow(mode: string) {
  expandedMode.value = expandedMode.value === mode ? null : mode
}

function activateModeRow(row: ModeRow) {
  if (row.count > 0 && hasModeRoster.value) {
    toggleModeRow(row.mode)
  }
}

const modeChart = computed<ChartData<'doughnut', number[], string> | null>(() => {
  if (modeRows.value.length === 0) {
    return null
  }
  return {
    labels: modeRows.value.map((row) => modeName(row.mode)),
    datasets: [
      {
        data: modeRows.value.map((row) => row.count),
        backgroundColor: modeRows.value.map((row) => row.color),
        hoverBackgroundColor: modeRows.value.map((row) => row.hoverColor)
      }
    ]
  }
})

const modeOptions = computed<ChartOptions<'doughnut'>>(() => ({
  responsive: true,
  plugins: {
    legend: { display: false },
    tooltip: {
      callbacks: {
        label: (ctx) => `${ctx.parsed} ${t('activity.units.person')}`,
        afterLabel: (ctx) => {
          const row = modeRows.value[ctx.dataIndex]
          if (!row) {
            return []
          }
          const lines = [
            `${t('activity.statistics.groups.sum')}：${formatHours(row.sum)} ${t('activity.units.hour')}`,
            `${t('activity.statistics.groups.mean')}：${formatHours(row.mean)} ${t('activity.units.hour')}`
          ]
          if (row.members && row.members.length > 0) {
            const shown = row.members.slice(0, ROSTER_TOOLTIP_LIMIT).map((member) => member.name)
            if (row.members.length > ROSTER_TOOLTIP_LIMIT) {
              shown.push(`+${row.members.length - ROSTER_TOOLTIP_LIMIT}`)
            }
            return [...lines, ...shown]
          }
          return lines
        }
      }
    }
  },
  onClick: (_event, elements) => {
    if (elements.length === 0) {
      expandedMode.value = null
      return
    }
    const row = modeRows.value[elements[0].index]
    if (row && row.count > 0 && hasModeRoster.value) {
      toggleModeRow(row.mode)
    }
  }
}))

const classRows = computed(() => {
  return orderClassRowsForChart(statistics.value?.classes ?? [])
})

const classChartHeight = computed(() => Math.max(classRows.value.length, 1) * 32 + 48)

function classLabel(row: ActivityStatisticsClassGroup): string {
  if (row.group_oid === '') {
    return t('activity.statistics.groups.unnamedClass')
  }
  return row.group_name || t('activity.statistics.groups.unknownClassWithId', {
    id: row.group_oid.slice(-6)
  })
}

const classChart = computed<ChartData<'bar', number[], string> | null>(() => {
  if (classRows.value.length === 0) {
    return null
  }
  return {
    labels: classRows.value.map(classLabel),
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
          if (!row) {
            return []
          }
          return [
            `${t('activity.statistics.groups.sum')}：${formatHours(row.sum)} ${t('activity.units.hour')}`,
            `${t('activity.statistics.groups.mean')}：${formatHours(row.mean)} ${t('activity.units.hour')}`
          ]
        }
      }
    }
  },
  scales: {
    x: {
      title: {
        display: true,
        text: t('activity.statistics.groups.axisSum'),
        color: chartTheme.value.text
      },
      beginAtZero: true,
      ticks: { color: chartTheme.value.muted },
      grid: { color: chartTheme.value.grid }
    },
    y: {
      reverse: true,
      ticks: { autoSkip: false, color: chartTheme.value.muted },
      grid: { display: false }
    }
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

function memberChipTitle(member: StatisticsMemberRef) {
  const base = `${member.name}（${member.id}）`
  const duration =
    member.duration !== undefined
      ? ` · ${formatHours(member.duration)} ${t('activity.units.hour')}`
      : ''
  return `${base}${duration}`
}

function memberChipLabel(member: StatisticsMemberRef) {
  const duration = member.duration !== undefined ? ` · ${formatHours(member.duration)}h` : ''
  return `${member.name}${duration}`
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
        <div class="stats-body">
          <div class="mb-3 flex items-center justify-end gap-2">
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
            <section class="stats-section">
              <h4 class="stats-section-title">
                {{ t('activity.statistics.sections.overview') }}
              </h4>
              <ElRow class="stats-grid -mb-2">
                <ElCol
                  v-for="metric in overviewMetrics"
                  :key="metric.key"
                  :span="4"
                  :sm="6"
                  :xs="8"
                >
                  <ElStatistic
                    :title="t(metric.titleKey)"
                    :value="descMetric(metric.key)"
                    :precision="metric.precision"
                    :suffix="t(metric.unitKey)"
                  />
                </ElCol>
              </ElRow>
            </section>
            <section v-if="durationChart" class="stats-section">
              <h4 class="stats-section-title">
                {{ t('activity.statistics.sections.distribution') }}
              </h4>
              <div
                class="relative mx-auto"
                :style="{ height: DISTRIBUTION_CHART_HEIGHT + 'px', maxWidth: '640px' }"
              >
                <Bar
                  role="img"
                  :aria-label="t('activity.statistics.distribution.aria')"
                  :data="durationChart"
                  :options="durationOptions"
                />
              </div>
              <p class="mt-2 text-center text-xs text-gray-500 dark:text-gray-400">
                {{
                  t('activity.statistics.distribution.percentile', {
                    p25: formatHours(descMetric('25_percentile')),
                    p50: formatHours(descMetric('median')),
                    p75: formatHours(descMetric('75_percentile'))
                  })
                }}
              </p>
              <p v-if="peakBin && peakBin.count > 0" class="text-center text-xs text-gray-500 dark:text-gray-400">
                {{
                  t('activity.statistics.distribution.binLine', {
                    width: binWidthLabel,
                    range: formatBinLabel(peakBin.from, peakBin.to),
                    count: peakBin.count
                  })
                }}
              </p>
            </section>
            <section v-if="modeChart" class="stats-section">
              <h4 class="stats-section-title">
                {{ t('activity.statistics.sections.mode') }}
              </h4>
              <ElRow :gutter="16">
                <ElCol :span="10" :xs="24">
                  <div class="relative mx-auto" style="max-width: 300px">
                    <Doughnut
                      role="img"
                      :aria-label="t('activity.statistics.groups.ariaMode')"
                      :data="modeChart"
                      :options="modeOptions"
                    />
                    <div
                      v-if="statistics"
                      class="pointer-events-none absolute inset-0 grid place-items-center"
                      aria-hidden="true"
                    >
                      <span class="text-center">
                        <span class="block text-2xl font-semibold text-gray-700 dark:text-gray-100">
                          {{ statistics.total }}
                        </span>
                        <span class="block text-xs text-gray-400 dark:text-gray-500">
                          {{ t('activity.statistics.metrics.total') }}
                        </span>
                      </span>
                    </div>
                  </div>
                </ElCol>
                <ElCol :span="14" :xs="24">
                  <ul class="mode-list">
                    <template v-for="row in modeRows" :key="row.mode">
                      <li
                        class="mode-row"
                        :class="{
                          expandable: row.count > 0 && hasModeRoster,
                          expanded: expandedMode === row.mode
                        }"
                        :title="
                          row.count > 0 && hasModeRoster
                            ? t('activity.statistics.groups.rosterToggle')
                            : undefined
                        "
                        role="button"
                        tabindex="0"
                        :aria-expanded="expandedMode === row.mode"
                        @click="activateModeRow(row)"
                        @keydown.enter.prevent="activateModeRow(row)"
                        @keydown.space.prevent="activateModeRow(row)"
                      >
                        <span
                          class="mode-dot"
                          :style="{ backgroundColor: row.color }"
                          aria-hidden="true"
                        ></span>
                        <span class="mode-label">{{ modeName(row.mode) }}</span>
                        <span class="mode-nums">
                          {{ row.count }} {{ t('activity.units.person') }} ·
                          {{ formatHours(row.sum) }} {{ t('activity.units.hour') }} ·
                          {{ t('activity.statistics.groups.mean') }}
                          {{ formatHours(row.mean) }}
                        </span>
                        <ElIcon
                          v-if="row.count > 0 && hasModeRoster"
                          class="mode-caret"
                          :size="12"
                          aria-hidden="true"
                        >
                          <ArrowDown />
                        </ElIcon>
                      </li>
                      <li v-if="expandedMode === row.mode && row.count > 0" class="roster-row">
                        <div v-if="row.members && row.members.length > 0" class="member-chips">
                          <ElTag
                            v-for="(member, memberIndex) in row.members"
                            :key="member._id || member.id || memberIndex"
                            size="small"
                            type="info"
                            effect="plain"
                            class="member-chip"
                            :title="memberChipTitle(member)"
                          >
                            {{ memberChipLabel(member) }}
                          </ElTag>
                        </div>
                        <span v-else class="text-xs text-gray-400 dark:text-gray-500">
                          {{ t('activity.statistics.groups.rosterUnavailable') }}
                        </span>
                      </li>
                    </template>
                  </ul>
                </ElCol>
              </ElRow>
            </section>
            <section v-if="classChart" class="stats-section">
              <h4 class="stats-section-title">
                {{ t('activity.statistics.sections.classes') }}
              </h4>
              <div class="relative" :style="{ height: classChartHeight + 'px' }">
                <Bar
                  role="img"
                  :aria-label="t('activity.statistics.groups.ariaClass')"
                  :data="classChart"
                  :options="classOptions"
                />
              </div>
              <p class="mt-1 text-center text-xs text-gray-500 dark:text-gray-400">
                {{ t('activity.statistics.groups.sortedAsc') }}
              </p>
            </section>
          </template>
        </div>
      </div>
    </template>
    <template #text> {{ t('activity.statistics.name') }} </template>
  </ZButtonOrCard>
</template>

<style scoped>
.stats-body {
  max-height: min(76vh, 920px);
  overflow-y: auto;
}

.stats-section {
  margin-bottom: 20px;
}

.stats-section-title {
  margin: 0 0 10px;
  padding-left: 8px;
  border-left: 3px solid var(--el-color-primary);
  font-size: 14px;
  font-weight: 600;
  line-height: 1.3;
  color: var(--el-text-color-primary);
}

.stats-grid :deep(.el-statistic__number) {
  font-variant-numeric: tabular-nums;
}

.stats-grid :deep(.el-statistic__head) {
  color: var(--el-text-color-secondary);
}

.stats-grid .el-col {
  margin-bottom: 8px;
}

.mode-list {
  list-style: none;
  margin: 0;
  padding: 0;
}

.mode-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 7px 8px;
  border-radius: 6px;
  font-size: 13px;
  color: var(--el-text-color-regular);
  transition: background-color 0.15s ease;
}

.mode-row.expandable {
  cursor: pointer;
}

.mode-row.expandable:hover {
  background-color: var(--el-fill-color-light);
}

.mode-row.expanded {
  background-color: var(--el-fill-color-light);
}

.mode-dot {
  flex: none;
  width: 10px;
  height: 10px;
  border-radius: 50%;
}

.mode-label {
  flex: none;
  max-width: 40%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.mode-nums {
  flex: 1 1 auto;
  text-align: right;
  font-variant-numeric: tabular-nums;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.mode-caret {
  flex: none;
  color: var(--el-text-color-secondary);
  transition: transform 0.18s ease;
}

.mode-row.expanded .mode-caret {
  transform: rotate(180deg);
}

.mode-row.expandable:focus-visible {
  outline: 2px solid var(--el-color-primary);
  outline-offset: 1px;
}

.roster-row {
  padding: 4px 8px 6px 18px;
}

.member-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}

.member-chip {
  cursor: default;
}
</style>
