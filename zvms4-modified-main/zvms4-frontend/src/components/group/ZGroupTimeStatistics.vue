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
import { Bar, Doughnut } from 'vue-chartjs'
import { ElEmpty, ElResult, ElSegmented, ElSwitch } from 'element-plus'
import { computed, ref, toRefs, watch } from 'vue'
import api from '@/api'
import type { ActivityMember } from 'types/activity.v2'
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

const { groupId } = toRefs(props)

const statistics = ref<Record<string, Record<string, number>>>({})
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

async function refresh() {
  error.value = ''
  if (!groupId.value) {
    statistics.value = {}
    loading.value = false
    return
  }
  if (!canView.value) {
    statistics.value = {}
    error.value = '无权限查看班级统计'
    loading.value = false
    return
  }
  loading.value = true
  try {
    statistics.value = await api.group.reads.statisticsCompliance(groupId.value)
  } catch {
    statistics.value = {}
    error.value = '统计加载失败，请稍后重试'
  } finally {
    loading.value = false
  }
}

watch(groupId, refresh, { immediate: true })

const activeMode = ref<ActivityMember['mode']>('on-campus')

const modes = ref<ActivityMember['mode'][]>(['on-campus', 'off-campus', 'social-practice'])

const barChart = ref(false)

const hasData = computed(() => Object.keys(statistics.value).length > 0)

// The backend's compliance buckets are numeric ranges ("0%–20%") except the
// terminal one, which comes back as the English literal "Completed".
function formatBucketLabel(label: string) {
  return label === 'Completed' ? '已达标' : label
}

// Chart datasets are derived from the raw payload so toggling the chart type
// never triggers another request.
const barChartData = computed(() => {
  if (Object.keys(statistics.value).length === 0) return { labels: [], datasets: [] }

  // Get all unique labels from all modes
  const allLabels = new Set<string>()
  Object.values(statistics.value).forEach((modeData) => {
    // kebab-case to Title Case
    Object.keys(modeData).forEach((label) => allLabels.add(label))
  })
  const labelKeys = Array.from(allLabels)

  const datasets = Object.keys(statistics.value).map((mode) => ({
    label: t(`activity.mode.${mode}.name`),
    data: labelKeys.map((label) => statistics.value[mode]?.[label] || 0)
  }))

  return {
    labels: labelKeys.map(formatBucketLabel),
    datasets
  }
})

const doughnutChartData = computed(() => {
  if (Object.keys(statistics.value).length === 0 || !statistics.value[activeMode.value]) {
    return { labels: [], datasets: [] }
  }

  const modeData = statistics.value[activeMode.value]
  const labels = Object.keys(modeData)
  const data = Object.values(modeData)

  return {
    labels: labels.map(formatBucketLabel),
    datasets: [{ data }]
  }
})
</script>

<template>
  <div class="statistics-panel mx-4" v-loading="loading">
    <div class="mb-2 text-center">
      <ElSegmented
        v-model="barChart"
        :options="[
          { label: '环形图', value: false },
          { label: '柱状图', value: true }
        ]"
        aria-label="图表类型切换"
      />
    </div>
    <div class="mb-2 text-center" v-if="!barChart">
      <ElSegmented :options="modes" v-model="activeMode" aria-label="义工类型切换">
        <template #default="option">
          {{ t('activity.mode.' + option.item + '.name') }}
        </template>
      </ElSegmented>
    </div>
    <ElResult v-if="error && !loading" icon="error" :title="error" />
    <Transition name="chart-fade" mode="out-in">
      <div
        class="mt-6 text-center"
        v-if="!loading && hasData"
        style="text-align: center !important"
        :key="barChart ? 'bar' : activeMode"
      >
        <Bar
          v-if="barChart"
          role="img"
          aria-label="班级达标人数柱状分布图"
          :data="{
            ...barChartData,
            datasets: barChartData.datasets.map((d, i) => ({
              ...d,
              backgroundColor: ['#409eff', '#67c23a', '#e6a23c'][i],
              borderRadius: 4
            }))
          }"
          :options="{
            responsive: true,
            plugins: {
              legend: { position: 'bottom', labels: { boxWidth: 12, boxHeight: 12 } },
              tooltip: {
                callbacks: {
                  label: (ctx) => `${ctx.dataset.label}：${ctx.parsed.y} 人`
                }
              }
            },
            scales: {
              x: { grid: { display: false } },
              y: { beginAtZero: true, ticks: { precision: 0 }, grid: { color: '#e4e7ed' } }
            }
          }"
        />
        <div v-else class="relative mx-auto max-w-96">
          <Doughnut
            role="img"
            aria-label="班级达标人数环形分布图"
            :data="{
              ...doughnutChartData,
              datasets: [
                {
                  ...doughnutChartData.datasets[0],
                  backgroundColor:
                    activeMode === 'on-campus'
                      ? ['#c6e2ff', '#a0cfff', '#79bbff', '#409eff', '#337ecc', '#245db2']
                      : activeMode === 'off-campus'
                        ? ['#d1edc4', '#b3e19d', '#95d475', '#67c23a', '#529b2e', '#398e01']
                        : ['#f8e3c5', '#f3d19e', '#eebe77', '#e6a23c', '#cf9236', '#b88230']
                }
              ]
            }"
            :options="{
              responsive: true,
              plugins: {
                legend: { position: 'bottom', labels: { boxWidth: 12, boxHeight: 12 } },
                tooltip: {
                  callbacks: {
                    label: (ctx) => {
                      const data = doughnutChartData.datasets[0]?.data ?? []
                      const total = data.reduce((a: number, b: number) => a + b, 0)
                      const pct = total > 0 ? ((ctx.parsed / total) * 100).toFixed(1) : '0.0'
                      return `${ctx.label}：${ctx.parsed} 人 (${pct}%)`
                    }
                  }
                }
              }
            }"
          />
          <div
            class="pointer-events-none absolute inset-0 grid place-items-center"
            aria-hidden="true"
          >
            <span class="text-2xl font-semibold tracking-wide text-gray-700">
              {{
                (doughnutChartData.datasets[0]?.data ?? []).reduce(
                  (a: number, b: number) => a + b,
                  0
                )
              }}
            </span>
          </div>
        </div>
      </div>
    </Transition>
    <ElEmpty v-if="!loading && !error && !hasData" description="暂无统计数据" :image-size="120" />
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
</style>
