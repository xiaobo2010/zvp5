<script setup lang="ts">
import {
  Chart as ChartJS,
  Title,
  Tooltip,
  Legend,
  BarElement,
  CategoryScale,
  LinearScale,
  ArcElement,
  Colors
} from 'chart.js'
import type { ChartData, ChartOptions } from 'chart.js'
import { Bar, Doughnut } from 'vue-chartjs'
import { ElCard, ElEmpty, ElIcon, ElResult, ElSegmented, ElTag } from 'element-plus'
import { computed, ref, toRefs, watch } from 'vue'
import { ArrowDown } from '@element-plus/icons-vue'
import api from '@/api'
import type { ActivityMember } from 'types/activity.v2'
import type { GroupComplianceMemberRosters, StatisticsMemberRef } from 'types/statistics'
import { useI18n } from 'vue-i18n'
import { useUserStore } from '@/stores/user'

const { t } = useI18n()
const userStore = useUserStore()

const props = withDefaults(
  defineProps<{
    groupId: string
  }>(),
  {
    groupId: ''
  }
)
ChartJS.register(Colors, Title, Tooltip, Legend, BarElement, CategoryScale, LinearScale, ArcElement)

type GroupMode = ActivityMember['mode']

interface BucketRow {
  rawLabel: string
  label: string
  count: number
  percent: string
  members: StatisticsMemberRef[] | null
}

interface ModeCard {
  mode: string
  name: string
  rows: BucketRow[]
  data: ChartData<'doughnut', number[], string>
  total: number
  completed: number
  rate: string | null
}

const CANONICAL_MODES: GroupMode[] = ['on-campus', 'off-campus', 'social-practice']

const MODE_PALETTES: Record<string, string[]> = {
  'on-campus': ['#c6e2ff', '#a0cfff', '#79bbff', '#409eff', '#337ecc', '#245db2'],
  'off-campus': ['#d1edc4', '#b3e19d', '#95d475', '#67c23a', '#529b2e', '#398e01'],
  'social-practice': ['#f8e3c5', '#f3d19e', '#eebe77', '#e6a23c', '#cf9236', '#b88230']
}
const FALLBACK_PALETTE = ['#e4e7ed', '#cdd0d6', '#b1b3b8', '#909399', '#73767a', '#5a5c62']

const BAR_PALETTE = ['#409eff', '#67c23a', '#e6a23c']
const ROSTER_TOOLTIP_LIMIT = 6

const { groupId } = toRefs(props)

const statistics = ref<Record<string, Record<string, number>>>({})
const roster = ref<GroupComplianceMemberRosters>({})
const loading = ref(false)
const error = ref('')

// `/v2/groups/{id}/statistics/compliance` requires secretary of the class and
// above. Pre-judge only for UX; the backend remains the source of truth.
const canView = computed(
  () =>
    userStore.position.includes('admin') ||
    userStore.position.includes('department') ||
    (userStore.position.includes('secretary') && userStore.class_id === groupId.value)
)

const barChart = ref(false)
const expandedKey = ref<string | null>(null)

const hasData = computed(() => Object.keys(statistics.value).length > 0)
const hasRoster = computed(() => Object.keys(roster.value).length > 0)

// The backend's compliance buckets are numeric ranges ("0%–20%") except the
// terminal one, which comes back as the English literal "Completed".
function formatBucketLabel(label: string) {
  return label === 'Completed' ? t('manage.groupDetails.statistics.completed') : label
}

function percentOf(count: number, total: number) {
  return total > 0 ? ((count / total) * 100).toFixed(1) : '0.0'
}

function rowKey(mode: string, rawLabel: string) {
  return `${mode}|${rawLabel}`
}

function isExpanded(mode: string, rawLabel: string) {
  return expandedKey.value === rowKey(mode, rawLabel)
}

function toggleRow(mode: string, rawLabel: string) {
  const key = rowKey(mode, rawLabel)
  expandedKey.value = expandedKey.value === key ? null : key
}

function activateRow(mode: string, row: BucketRow) {
  if (row.count > 0 && hasRoster.value) {
    toggleRow(mode, row.rawLabel)
  }
}

function paletteAt(palette: string[], index: number) {
  return palette[index % palette.length]
}

async function refresh() {
  error.value = ''
  expandedKey.value = null
  roster.value = {}
  if (!groupId.value) {
    statistics.value = {}
    loading.value = false
    return
  }
  if (!canView.value) {
    statistics.value = {}
    error.value = t('manage.groupDetails.statistics.noPermission')
    loading.value = false
    return
  }
  const current = groupId.value
  loading.value = true
  try {
    const data = await api.group.reads.statisticsCompliance(current)
    if (current === groupId.value) {
      // The per-bucket rosters ride along with the buckets in the same response
      // (no second request). Peel the additive field off so the bucket maps stay
      // the only modes `orderedModes()` ever sees; a backend without the field
      // degrades to the same empty roster as before.
      const { member_rosters: memberRosters, ...buckets } = data
      statistics.value = buckets
      roster.value = memberRosters ?? {}
    }
  } catch {
    if (current === groupId.value) {
      statistics.value = {}
      error.value = t('manage.groupDetails.statistics.loadFailed')
    }
  } finally {
    if (current === groupId.value) {
      loading.value = false
    }
  }
}

watch(groupId, refresh, { immediate: true })

function orderedModes(): string[] {
  const present = Object.keys(statistics.value)
  if (present.length === 0) {
    return [...CANONICAL_MODES]
  }
  const canonical = CANONICAL_MODES.filter((mode) => present.includes(mode))
  const extra = present.filter((mode) => !CANONICAL_MODES.includes(mode as GroupMode))
  return [...canonical, ...extra]
}

const barChartData = computed<ChartData<'bar', number[], string>>(() => {
  if (!hasData.value) {
    return { labels: [], datasets: [] }
  }

  const totals = new Map<string, number>()
  const firstSeen = new Map<string, number>()
  Object.values(statistics.value).forEach((modeData) => {
    Object.entries(modeData).forEach(([label, count]) => {
      if (!firstSeen.has(label)) {
        firstSeen.set(label, firstSeen.size)
      }
      totals.set(label, (totals.get(label) ?? 0) + (Number(count) || 0))
    })
  })

  const labelKeys = Array.from(totals.keys()).sort(
    (a, b) =>
      (totals.get(a) ?? 0) - (totals.get(b) ?? 0) ||
      (firstSeen.get(a) ?? 0) - (firstSeen.get(b) ?? 0)
  )

  const datasets = orderedModes().map((mode, index) => ({
    label: t(`activity.mode.${mode}.name`),
    data: labelKeys.map((label) => Number(statistics.value[mode]?.[label]) || 0),
    backgroundColor: BAR_PALETTE[index % BAR_PALETTE.length],
    borderRadius: 4
  }))

  return {
    labels: labelKeys.map(formatBucketLabel),
    datasets
  }
})

const barOptions = computed<ChartOptions<'bar'>>(() => ({
  responsive: true,
  plugins: {
    legend: { position: 'bottom', labels: { boxWidth: 12, boxHeight: 12 } },
    tooltip: {
      callbacks: {
        label: (ctx) =>
          t('manage.groupDetails.statistics.barPersonLine', {
            mode: ctx.dataset.label ?? '',
            count: ctx.parsed.y
          })
      }
    }
  },
  scales: {
    x: { grid: { display: false } },
    y: { beginAtZero: true, ticks: { precision: 0 }, grid: { color: '#e4e7ed' } }
  }
}))

const modeCards = computed<ModeCard[]>(() => {
  if (!hasData.value) {
    return []
  }
  const stats = statistics.value
  const rosterData = roster.value
  return orderedModes().map((mode) => {
    const buckets = stats[mode] ?? {}
    const entries = Object.entries(buckets)
    const total = entries.reduce((acc, [, count]) => acc + (Number(count) || 0), 0)
    const sorted = entries
      .map(([rawLabel, count], index) => ({ rawLabel, count: Number(count) || 0, index }))
      .sort((a, b) => a.count - b.count || a.index - b.index)
    const completed = Number(buckets['Completed']) || 0
    const modeRosterData = rosterData[mode] ?? null
    const rows: BucketRow[] = sorted.map((bucket) => ({
      rawLabel: bucket.rawLabel,
      label: formatBucketLabel(bucket.rawLabel),
      count: bucket.count,
      percent: percentOf(bucket.count, total),
      members: modeRosterData ? (modeRosterData[bucket.rawLabel] ?? []) : null
    }))
    return {
      mode,
      name: t(`activity.mode.${mode}.name`),
      rows,
      data: {
        labels: rows.map((row) => row.label),
        datasets: [
          {
            data: rows.map((row) => row.count),
            backgroundColor: rows.map((row, index) =>
              paletteAt(MODE_PALETTES[mode] ?? FALLBACK_PALETTE, index)
            )
          }
        ]
      },
      total,
      completed,
      rate: total > 0 ? ((completed / total) * 100).toFixed(1) : null
    }
  })
})

function rosterTooltipLines(members: StatisticsMemberRef[] | null): string[] {
  if (!members || members.length === 0) {
    return []
  }
  const shown = members.slice(0, ROSTER_TOOLTIP_LIMIT).map((member) => member.name)
  if (members.length > ROSTER_TOOLTIP_LIMIT) {
    shown.push(`+${members.length - ROSTER_TOOLTIP_LIMIT}`)
  }
  return shown
}

function doughnutOptions(card: ModeCard): ChartOptions<'doughnut'> {
  return {
    responsive: true,
    plugins: {
      legend: { display: false },
      tooltip: {
        callbacks: {
          label: (ctx) => {
            const row = card.rows[ctx.dataIndex]
            const percent = row?.percent ?? '0.0'
            return t('manage.groupDetails.statistics.bucketLine', {
              count: ctx.parsed,
              percent
            })
          },
          afterLabel: (ctx) => rosterTooltipLines(card.rows[ctx.dataIndex]?.members ?? null)
        }
      }
    },
    onClick: (_event, elements) => {
      if (elements.length === 0) {
        expandedKey.value = null
        return
      }
      const row = card.rows[elements[0].index]
      if (row && row.count > 0) {
        toggleRow(card.mode, row.rawLabel)
      }
    }
  }
}

function memberChipTitle(member: StatisticsMemberRef) {
  const base = `${member.name}（${member.id}）`
  const duration =
    member.duration !== undefined
      ? ` · ${member.duration.toFixed(1)} ${t('activity.units.hour')}`
      : ''
  return `${base}${duration}`
}

function memberChipLabel(member: StatisticsMemberRef) {
  const duration = member.duration !== undefined ? ` · ${member.duration.toFixed(1)}h` : ''
  return `${member.name}${duration}`
}
</script>

<template>
  <div class="statistics-panel mx-4" v-loading="loading">
    <div class="mb-2 text-center">
      <ElSegmented
        v-model="barChart"
        :options="[
          { label: t('manage.groupDetails.statistics.chartType.doughnut'), value: false },
          { label: t('manage.groupDetails.statistics.chartType.bar'), value: true }
        ]"
        :aria-label="t('manage.groupDetails.statistics.chartSwitch')"
      />
    </div>
    <ElResult v-if="error && !loading" icon="error" :title="error" />
    <Transition name="chart-fade" mode="out-in">
      <div v-if="!loading && hasData" class="mt-4" :key="barChart ? 'bar' : 'doughnuts'">
        <template v-if="barChart">
          <Bar
            role="img"
            :aria-label="t('manage.groupDetails.statistics.ariaBar')"
            :data="barChartData"
            :options="barOptions"
          />
          <p class="mt-2 text-center text-xs text-gray-500">
            {{ t('manage.groupDetails.statistics.sortedBarNote') }}
          </p>
        </template>
        <div v-else class="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
          <ElCard v-for="card in modeCards" :key="card.mode" shadow="hover" class="mode-card">
            <header class="mb-3 text-center">
              <p class="text-base font-medium text-gray-800">{{ card.name }}</p>
              <p class="mt-1 text-xs text-gray-500">
                {{ t('manage.groupDetails.statistics.total') }} {{ card.total }}
                {{ t('activity.units.person') }}
                <template v-if="card.total > 0">
                  ·
                  {{ t('manage.groupDetails.statistics.completedOf', { count: card.completed }) }}
                  ({{ t('manage.groupDetails.statistics.rateOf', { rate: card.rate }) }})
                </template>
              </p>
            </header>
            <div v-if="card.total === 0" class="grid h-64 place-items-center">
              <ElEmpty
                :description="t('manage.groupDetails.statistics.modeEmpty')"
                :image-size="72"
              />
            </div>
            <template v-else>
              <div class="relative mx-auto max-w-72">
                <Doughnut
                  role="img"
                  :aria-label="
                    t('manage.groupDetails.statistics.ariaDoughnut', { mode: card.name })
                  "
                  :data="card.data"
                  :options="doughnutOptions(card)"
                />
                <div
                  class="pointer-events-none absolute inset-0 grid place-items-center"
                  aria-hidden="true"
                >
                  <span class="text-center">
                    <span class="block text-2xl font-semibold tracking-wide text-gray-700">
                      {{ card.total }}
                    </span>
                    <span class="block text-xs text-gray-400">
                      {{ t('activity.units.person') }}
                    </span>
                  </span>
                </div>
              </div>
              <ul class="bucket-list mt-3">
                <template v-for="(row, index) in card.rows" :key="row.rawLabel">
                  <li
                    class="bucket-row"
                    :class="{
                      expandable: row.count > 0 && hasRoster,
                      expanded: isExpanded(card.mode, row.rawLabel)
                    }"
                    :title="
                      row.count > 0 && hasRoster
                        ? t('manage.groupDetails.statistics.viewRoster')
                        : undefined
                    "
                    role="button"
                    tabindex="0"
                    :aria-expanded="isExpanded(card.mode, row.rawLabel)"
                    @click="activateRow(card.mode, row)"
                    @keydown.enter.prevent="activateRow(card.mode, row)"
                    @keydown.space.prevent="activateRow(card.mode, row)"
                  >
                    <span
                      class="bucket-dot"
                      :style="{
                        backgroundColor: paletteAt(
                          MODE_PALETTES[card.mode] ?? FALLBACK_PALETTE,
                          index
                        )
                      }"
                      aria-hidden="true"
                    ></span>
                    <span class="bucket-label">{{ row.label }}</span>
                    <span class="bucket-count">
                      {{ row.count }} {{ t('activity.units.person') }} · {{ row.percent }}%
                    </span>
                    <ElIcon
                      v-if="row.count > 0 && hasRoster"
                      class="bucket-caret"
                      :size="12"
                      aria-hidden="true"
                    >
                      <ArrowDown />
                    </ElIcon>
                  </li>
                  <li
                    v-if="isExpanded(card.mode, row.rawLabel) && row.count > 0"
                    class="member-row"
                  >
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
                    <span v-else class="text-xs text-gray-400">
                      {{ t('manage.groupDetails.statistics.rosterUnavailable') }}
                    </span>
                  </li>
                </template>
              </ul>
            </template>
          </ElCard>
        </div>
      </div>
    </Transition>
    <ElEmpty
      v-if="!loading && !error && !hasData"
      :description="t('manage.groupDetails.statistics.empty')"
      :image-size="120"
    />
  </div>
</template>

<style scoped>
.chart-fade-enter-active,
.chart-fade-leave-active {
  transition: opacity 0.18s ease;
}

.chart-fade-enter-from,
.chart-fade-leave-to {
  opacity: 0;
}

.statistics-panel :deep(.el-segmented__item) {
  padding: 6px 14px;
}

.mode-card :deep(.el-card__body) {
  padding: 14px 14px 10px;
}

.bucket-list {
  list-style: none;
  margin: 0;
  padding: 0;
}

.bucket-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 5px 6px;
  border-radius: 4px;
  font-size: 12px;
  line-height: 1.4;
  color: var(--el-text-color-regular);
  transition: background-color 0.15s ease;
}

.bucket-row.expandable {
  cursor: pointer;
}

.bucket-row.expandable:hover {
  background-color: var(--el-fill-color-light);
}

.bucket-row.expanded {
  background-color: var(--el-fill-color-light);
}

.bucket-dot {
  flex: none;
  width: 10px;
  height: 10px;
  border-radius: 50%;
}

.bucket-label {
  flex: 1 1 auto;
  text-align: left;
}

.bucket-count {
  flex: none;
  font-variant-numeric: tabular-nums;
  color: var(--el-text-color-secondary);
}

.bucket-caret {
  flex: none;
  color: var(--el-text-color-secondary);
  transition: transform 0.18s ease;
}

.bucket-row.expanded .bucket-caret {
  transform: rotate(180deg);
}

.bucket-row.expandable:focus-visible {
  outline: 2px solid var(--el-color-primary);
  outline-offset: 1px;
}

.member-row {
  padding: 4px 8px 6px 24px;
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
