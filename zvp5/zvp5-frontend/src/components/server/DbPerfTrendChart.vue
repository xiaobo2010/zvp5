<script setup lang="ts">
import {
  Chart as ChartJS,
  Tooltip,
  Legend,
  LineElement,
  PointElement,
  LinearScale,
  CategoryScale
} from 'chart.js'
import type { ChartData, ChartOptions } from 'chart.js'
import { Line } from 'vue-chartjs'
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElEmpty } from 'element-plus'
import dayjs from '@/plugins/dayjs'
import { useChartTheme } from '@/utils/chartTheme'

ChartJS.register(Tooltip, Legend, LineElement, PointElement, LinearScale, CategoryScale)

export interface DbPerfTrendPoint {
  ts: string
  value: number | null
}

export interface DbPerfTrendSeries {
  key: string
  label: string
  unit: string | null
  points: DbPerfTrendPoint[]
}

const props = withDefaults(
  defineProps<{
    series: DbPerfTrendSeries[]
    loading?: boolean
    emptyText?: string
    beginAtZero?: boolean
    yMax?: number
  }>(),
  {
    loading: false,
    emptyText: '',
    beginAtZero: false,
    yMax: undefined
  }
)

const { t } = useI18n()
const chartTheme = useChartTheme()

// 与 server 段进度条配色同源(Element Plus 色板),深浅色模式下均可读
const LINE_PALETTE = [
  '#409eff',
  '#67c23a',
  '#e6a23c',
  '#f56c6c',
  '#337ecc',
  '#529b2e',
  '#b88230',
  '#909399',
  '#73767a',
  '#c45656',
  '#79bbff',
  '#95d475'
]

// 后端按统一粒度返回各序列,但容错地对齐到全序列时间轴的并集
const timeline = computed(() => {
  const set = new Set<string>()
  for (const s of props.series) {
    for (const p of s.points) {
      if (p.value !== null) set.add(p.ts)
    }
  }
  return [...set].sort()
})

const tickFormat = computed<'HH:mm' | 'MM-DD HH:mm'>(() => {
  const list = timeline.value
  if (list.length < 2) return 'HH:mm'
  const spanHours = dayjs(list[list.length - 1]).diff(dayjs(list[0]), 'hour')
  return spanHours > 48 ? 'MM-DD HH:mm' : 'HH:mm'
})

const labels = computed(() => timeline.value.map((ts) => dayjs(ts).format(tickFormat.value)))

function valueAt(series: DbPerfTrendSeries, ts: string): number | null {
  const point = series.points.find((p) => p.ts === ts)
  return point && point.value !== null ? point.value : null
}

const chartData = computed<ChartData<'line', (number | null)[], string>>(() => {
  if (props.series.length === 0 || timeline.value.length === 0) {
    return { labels: [], datasets: [] }
  }
  return {
    labels: labels.value,
    datasets: props.series.map((s, index) => ({
      label: s.label,
      data: timeline.value.map((ts) => valueAt(s, ts)),
      borderColor: LINE_PALETTE[index % LINE_PALETTE.length],
      backgroundColor: LINE_PALETTE[index % LINE_PALETTE.length],
      borderWidth: 2,
      pointRadius: 0,
      pointHitRadius: 8,
      tension: 0.3,
      spanGaps: true
    }))
  }
})

const isEmpty = computed(
  () =>
    props.series.length === 0 ||
    props.series.every((s) => s.points.every((p) => p.value === null)) ||
    timeline.value.length === 0
)

const options = computed<ChartOptions<'line'>>(() => ({
  responsive: true,
  maintainAspectRatio: false,
  interaction: { mode: 'nearest', axis: 'x', intersect: false },
  plugins: {
    legend: {
      position: 'bottom',
      labels: {
        boxWidth: 12,
        boxHeight: 12,
        padding: 16,
        color: chartTheme.value.text
      }
    },
    tooltip: {
      callbacks: {
        label: (ctx) => {
          const unit = props.series[ctx.datasetIndex]?.unit
          const value = ctx.parsed.y
          const text =
            typeof value === 'number'
              ? Number.isInteger(value)
                ? String(value)
                : value.toFixed(2)
              : '—'
          return unit ? `${ctx.dataset.label}: ${text} ${unit}` : `${ctx.dataset.label}: ${text}`
        }
      }
    }
  },
  scales: {
    x: {
      grid: { display: false },
      ticks: {
        color: chartTheme.value.muted,
        maxRotation: 0,
        autoSkip: true,
        maxTicksLimit: 9
      }
    },
    y: {
      beginAtZero: props.beginAtZero,
      suggestedMax: props.yMax,
      grid: { color: chartTheme.value.grid },
      ticks: {
        color: chartTheme.value.muted,
        callback: (value) => {
          const num = Number(value)
          return Number.isInteger(num) ? num : Number(num.toFixed(2))
        }
      }
    }
  }
}))
</script>

<template>
  <div v-loading="loading" class="relative h-64 w-full">
    <Line v-if="!isEmpty" role="img" :aria-label="t('server.database.trendTitle')" :data="chartData" :options="options" />
    <div v-else class="grid h-full place-items-center">
      <ElEmpty
        :description="emptyText || t('server.database.trendEmpty')"
        :image-size="88"
      />
    </div>
  </div>
</template>
