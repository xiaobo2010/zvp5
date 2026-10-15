<script lang="ts" setup>
import {
  ElButton,
  ElCol,
  ElEmpty,
  ElResult,
  ElRow,
  ElSegmented,
  ElStatistic
} from 'element-plus'
import { computed, ref, watch } from 'vue'
import { useWindowSize } from '@vueuse/core'
import { Refresh } from '@element-plus/icons-vue'
import { Doughnut } from 'vue-chartjs'
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
import { ZButtonOrCard } from '@/components'
import UilStatistics from '@/icons/UilStatistics.vue'
import api from '@/api'
import type { ActivityStatisticsDescription, ActivityStatisticsLayer } from '@/../types'

ChartJS.register(Colors, Title, Tooltip, Legend, BarElement, CategoryScale, LinearScale, ArcElement)

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

const { width, height } = useWindowSize()

interface StatisticsPayload {
  description: ActivityStatisticsDescription
  layers: ActivityStatisticsLayer[]
  layersChart: {
    labels: string[]
    datasets: {
      label?: string
      data: number[]
      backgroundColor?: string[]
    }[]
  }
}

// Cache the last result per activity so reopening the dialog is instant.
const statsCache = new Map<string, StatisticsPayload>()

function emptyPayload(): StatisticsPayload {
  return {
    description: {},
    layers: [],
    layersChart: { labels: [], datasets: [] }
  }
}

function buildPayload(
  description: ActivityStatisticsDescription,
  layers: ActivityStatisticsLayer[]
): StatisticsPayload {
  const payload = emptyPayload()
  payload.description = description
  payload.layers = layers
  if (layers && layers.length > 0) {
    const total = description.total ?? 0
    payload.layersChart.labels = layers.map((layer) => {
      const percent = total > 0 ? ((layer.count / total) * 100).toFixed(1) : '0.0'
      return `${layer.value.toFixed(1)} 小时 (${percent}%)`
    })
    payload.layersChart.datasets = [
      {
        data: layers.map((layer) => layer.count)
      }
    ]
  }
  return payload
}

const open = ref(false)
const loading = ref(false)
const error = ref('')
const statistics = ref<StatisticsPayload>(emptyPayload())

const hasData = computed(
  () => Object.keys(statistics.value.description).length > 0 || statistics.value.layers.length > 0
)

const statTable = ['概览', '时长分布']
const activeStatistic = ref(statTable[0])

async function load(aid: string, force = false) {
  if (!aid) {
    return
  }
  if (!force) {
    const cached = statsCache.get(aid)
    if (cached) {
      statistics.value = cached
      return
    }
  }
  loading.value = true
  error.value = ''
  try {
    const description = await api.activity.stats.description(aid)
    const layers = await api.activity.stats.layers(aid)
    const payload = buildPayload(description, layers)
    statistics.value = payload
    statsCache.set(aid, payload)
  } catch {
    error.value = '统计数据加载失败，请稍后重试'
  } finally {
    loading.value = false
  }
}

function reload() {
  void load(props.aid, true)
  emits('refresh')
}

watch(
  open,
  () => {
    if (open.value) {
      void load(props.aid)
    }
  }
)

watch(
  () => props.aid,
  (value) => {
    if (value && !statsCache.has(value)) {
      statistics.value = emptyPayload()
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
    :width="width < height ? '60%' : '30%'"
    type="warning"
    :icon="UilStatistics"
  >
    <template #default>
      <div v-loading="loading">
        <div style="text-align: center">
          <ElSegmented v-model="activeStatistic" :options="statTable" aria-label="统计维度切换" />
          <ElButton
            class="px-2"
            type="success"
            :icon="Refresh"
            :disabled="loading"
            text
            bg
            circle
            title="强制刷新统计"
            aria-label="强制刷新统计"
            @click="reload"
          />
        </div>
        <ElResult v-if="error && !loading" icon="error" :title="error" />
        <ElEmpty v-else-if="!loading && !hasData" description="暂无统计数据" :image-size="120" />
        <template v-else>
          <ElRow v-if="activeStatistic === '概览'" class="stats-grid">
            <ElCol :span="4" :sm="6" :xs="8">
              <ElStatistic title="平均值" :value="statistics.description.mean" :precision="1" />
            </ElCol>
            <ElCol :span="4" :sm="6" :xs="8">
              <ElStatistic title="中位数" :value="statistics.description.median" :precision="1" />
            </ElCol>
            <ElCol :span="4" :sm="6" :xs="8">
              <ElStatistic title="众数" :value="statistics.description.mode" :precision="1" />
            </ElCol>
            <ElCol :span="4" :sm="6" :xs="8">
              <ElStatistic title="最小值" :value="statistics.description.min" :precision="1" />
            </ElCol>
            <ElCol :span="4" :sm="6" :xs="8">
              <ElStatistic title="最大值" :value="statistics.description.max" :precision="1" />
            </ElCol>
            <ElCol :span="4" :sm="6" :xs="8">
              <ElStatistic
                title="标准差"
                :value="statistics.description.std"
                :precision="1"
              />
            </ElCol>
            <ElCol :span="4" :sm="6" :xs="8">
              <ElStatistic title="方差" :value="statistics.description.var" :precision="1" />
            </ElCol>
            <ElCol :span="4" :sm="6" :xs="8">
              <ElStatistic
                title="25% 分位数"
                :value="statistics.description['25_percentile']"
                :precision="1"
              />
            </ElCol>
            <ElCol :span="4" :sm="6" :xs="8">
              <ElStatistic
                title="75% 分位数"
                :value="statistics.description['75_percentile']"
                :precision="1"
              />
            </ElCol>
          </ElRow>
          <div v-else-if="activeStatistic === '时长分布'" class="relative mx-auto max-w-96">
            <Doughnut
              role="img"
              aria-label="活动参与时长分布环形图"
              :data="statistics.layersChart"
              :options="{
                responsive: true,
                plugins: {
                  legend: { position: 'bottom', labels: { boxWidth: 12, boxHeight: 12 } },
                  tooltip: {
                    callbacks: {
                      label: (ctx) => `${ctx.label}：${ctx.parsed} 人`
                    },
                    padding: 10
                  }
                }
              }"
            />
            <div
              v-if="statistics.description.total"
              class="pointer-events-none absolute inset-0 grid place-items-center"
              aria-hidden="true"
            >
              <span class="text-center">
                <span class="block text-2xl font-semibold text-gray-700">
                  {{ statistics.description.total }}
                </span>
                <span class="block text-xs text-gray-400">参与人数</span>
              </span>
            </div>
          </div>
        </template>
      </div>
    </template>
    <template #text> 统计 </template>
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
