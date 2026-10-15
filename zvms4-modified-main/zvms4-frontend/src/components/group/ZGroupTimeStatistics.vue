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
import { ElSegmented, ElSwitch } from 'element-plus'
import { ref, toRefs, watch } from 'vue'
import api from '@/api'
import type { ActivityMember } from 'types/activity.v2'
import { useI18n } from 'vue-i18n'

const { t } = useI18n()

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
const loading = ref(true)

const barChartData = ref()
const doughnutChartData = ref()

async function refresh() {
  loading.value = true
  if (!groupId.value) return
  const res = await api.group.reads.statisticsCompliance(groupId.value)
  statistics.value = res
  barChartData.value = computeBarChartData()
  doughnutChartData.value = computeDoughnutChartData()
  loading.value = false
}

refresh()

const activeMode = ref<ActivityMember['mode']>('on-campus')

const modes = ref<ActivityMember['mode'][]>(['on-campus', 'off-campus', 'social-practice'])

const barChart = ref(false)

// The backend's compliance buckets are numeric ranges ("0%–20%") except the
// terminal one, which comes back as the English literal "Completed".
function formatBucketLabel(label: string) {
  return label === 'Completed' ? '已达标' : label
}

// Chart data computed properties
function computeBarChartData() {
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
}

function computeDoughnutChartData() {
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
}

watch(
  () => activeMode.value,
  () => {
    doughnutChartData.value = computeDoughnutChartData()
  }
)
</script>

<template>
  <div class="mx-4 text-center" v-loading="loading">
    <div class="text-center">
      <ElSwitch v-model="barChart" active-text="柱状图" inactive-text="环形图" />
    </div>
    <div class="text-center">
      <ElSegmented :options="modes" v-model="activeMode" v-if="!barChart">
        <template #default="option">
          {{ t('activity.mode.' + option.item + '.name') }}
        </template>
      </ElSegmented>
    </div>
    <div class="mt-6" v-if="!loading" style="text-align: center !important">
      <Bar v-if="barChart" :data="barChartData" />
      <Doughnut
        v-else
        :data="doughnutChartData"
        style="width: 384px; height: auto; text-align: center"
      />
    </div>
  </div>
</template>
