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
    <ElResult v-if="error && !loading" icon="error" :title="error" />
    <div class="mt-6" v-else-if="!loading && hasData" style="text-align: center !important">
      <Bar v-if="barChart" :data="barChartData" />
      <Doughnut
        v-else
        :data="doughnutChartData"
        style="width: 384px; height: auto; text-align: center"
      />
    </div>
    <ElEmpty v-else-if="!loading" description="暂无数据" />
  </div>
</template>
