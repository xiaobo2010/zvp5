<script setup lang="ts">
import { useRouter } from 'vue-router'
import { useUserStore } from '@/stores/user.ts'
import { ref, computed, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  ElCard, ElTag, ElAlert, ElButton, ElTooltip,
  ElTable, ElTableColumn, ElProgress
} from 'element-plus'
import type { TableColumnCtx } from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'
import dayjs from '@/plugins/dayjs'
import { isAxiosError } from 'axios'
import { getDatabaseStatus, type DbStatusData, type DbTableItem } from '@/api/system/db.ts'

const router = useRouter()
const userStore = useUserStore()
const { t } = useI18n()

if (!userStore.position.includes('admin')) {
  router.replace('/not-found')
}

const isAdmin = userStore.position.includes('admin')

const M = 'server.database.'

// —— 数据库监控:仅 admin 可见,与后端严格 admin 校验一致 ——
const dbStatus = ref<DbStatusData | null>(null)
// 整体状态在后端响应外层(status),不在 data 内
const overallState = ref<'ok' | 'degraded'>('ok')
const loading = ref(false)
const loadError = ref('')

const storage = computed(() => dbStatus.value?.storage ?? null)
const tables = computed(() => storage.value?.tables ?? null)
const tablespaces = computed(() => storage.value?.tablespaces ?? null)
const load = computed(() => dbStatus.value?.load ?? null)
const pool = computed(() => load.value?.connection_pool ?? null)

// 容量视图仅展示承载平台数据的表空间,Oracle 自有表空间(SYSTEM/SYSAUX/TEMP/UNDO 等)不渲染
const applicationTablespaces = computed(() => {
  return (tablespaces.value?.items ?? []).filter((ts) => ts.is_application === true)
})

// 从后端错误里提取 detail 用于展示
function detailOf(err: unknown): string {
  if (isAxiosError(err)) {
    const detail = (err.response?.data as { detail?: unknown } | undefined)?.detail
    return typeof detail === 'string' ? detail : ''
  }
  return ''
}

async function refresh() {
  loading.value = true
  loadError.value = ''
  try {
    const res = await getDatabaseStatus()
    dbStatus.value = res.data.data
    overallState.value = res.data.status === 'ok' ? 'ok' : 'degraded'
  } catch (err) {
    // 403:token 对应账号已无 admin 权限,与页面守卫行为一致
    if (isAxiosError(err) && err.response?.status === 403) {
      router.replace('/not-found')
      return
    }
    loadError.value = detailOf(err) || t(M + 'loadFailed')
  } finally {
    loading.value = false
  }
}

// —— 展示工具 ——
const BYTE_UNITS = ['B', 'KB', 'MB', 'GB', 'TB', 'PB']

function formatBytes(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined) return '—'
  let value = bytes
  let unit = 0
  while (value >= 1024 && unit < BYTE_UNITS.length - 1) {
    value /= 1024
    unit += 1
  }
  const text = value >= 100 || unit === 0 ? Math.round(value).toString() : value.toFixed(1)
  return `${text} ${BYTE_UNITS[unit]}`
}

function formatCount(n: number | null | undefined): string {
  return n === null || n === undefined ? '—' : n.toLocaleString('en-US')
}

function formatMetric(value: number | null): string {
  if (value === null) return '—'
  return Number.isInteger(value) ? value.toLocaleString('en-US') : value.toFixed(2)
}

function formatTime(iso: string | null | undefined): string {
  return iso ? dayjs(iso).format('YYYY-MM-DD HH:mm:ss') : '—'
}

function formatInterval(centiseconds: number | null): string {
  if (centiseconds === null) return '—'
  const seconds = centiseconds / 100
  return `${seconds % 1 === 0 ? seconds.toFixed(0) : seconds.toFixed(1)}s`
}

function pctText(percent: number | null | undefined): string {
  return percent === null || percent === undefined ? '—' : `${Math.round(percent * 10) / 10}%`
}

function clampPct(percent: number): number {
  return Math.min(100, Math.max(0, percent))
}

// 利用率配色:随用量升高从蓝到橙到红
function percentColor(percent: number): string {
  if (percent >= 90) return '#f56c6c'
  if (percent >= 75) return '#e6a23c'
  return '#409eff'
}

// 组件级降级文案:优先用后端 reason 对应的 i18n 文案,后端带 message 则直接用
function degradeText(reason: string | undefined, message: string | undefined): string {
  if (message) return message
  if (reason === 'insufficient_privileges') return t(M + 'reasonInsufficientPrivileges')
  if (reason === 'query_timeout') return t(M + 'reasonQueryTimeout')
  if (reason === 'no_samples') return t(M + 'reasonNoSamples')
  if (reason === 'pool_metrics_unavailable') return t(M + 'reasonPoolUnavailable')
  return t(M + 'reasonUnknown')
}

// last_analyzed 距今超过 30 天视为统计信息较旧(优化器估算可能已过期)
const ANALYZED_STALE_DAYS = 30
function analyzedStale(iso: string | null): boolean {
  if (!iso) return false
  return dayjs().diff(dayjs(iso), 'day') > ANALYZED_STALE_DAYS
}

const cacheText = computed(() => {
  const cache = dbStatus.value?.cache
  if (!cache) return ''
  return cache.cached
    ? t(M + 'cacheCached', { age: cache.age_seconds.toFixed(1) })
    : t(M + 'cacheFresh')
})

// 表按合计占用降序展示
const sortedTableItems = computed(() => {
  return [...(tables.value?.items ?? [])].sort((a, b) => b.total_bytes - a.total_bytes)
})

const tablespaceTotals = computed(() => {
  // 仅统计承载平台数据的表空间(is_application),Oracle 系统表空间不参与容量视图与合计
  const items = applicationTablespaces.value
  let used = 0
  let max = 0
  let anyNull = false
  for (const item of items) {
    if (item.used_bytes === null) anyNull = true
    else used += item.used_bytes
    if (item.max_bytes === null) anyNull = true
    else max += item.max_bytes
  }
  const percent = items.length > 0 && !anyNull && max > 0 ? (used / max) * 100 : null
  return { used, max, percent, anyNull }
})

const SUMMARY_BYTE_PROPS = new Set([
  'table_bytes', 'index_bytes', 'lob_bytes', 'lob_index_bytes', 'total_bytes'
])

// 表格合计行:字节列与估算行数做纵向求和
function tableSummaries(param: { columns: TableColumnCtx<DbTableItem>[]; data: DbTableItem[] }) {
  const { columns, data } = param
  const sums: string[] = []
  columns.forEach((column, i) => {
    const prop = column.property
    if (i === 0) {
      sums[i] = t(M + 'summary')
    } else if (SUMMARY_BYTE_PROPS.has(prop ?? '')) {
      const key = prop as 'total_bytes'
      sums[i] = formatBytes(data.reduce((acc, row) => acc + row[key], 0))
    } else if (prop === 'estimated_rows') {
      sums[i] = formatCount(data.reduce((acc, row) => acc + (row.estimated_rows ?? 0), 0))
    } else {
      sums[i] = ''
    }
  })
  return sums
}

if (isAdmin) {
  onMounted(() => {
    void refresh()
  })
}
</script>

<template>
  <!-- 数据库监控:仅 admin 可见 -->
  <div v-if="dbStatus" class="mt-8 flex flex-col gap-6">
    <!-- 总览:状态 / 观测时间 / 缓存口径 / 合计 / warnings -->
    <ElCard shadow="never">
      <template #header>
        <div class="flex flex-wrap items-center justify-between gap-x-3 gap-y-2">
          <span class="font-medium">{{ t(M + 'title') }}</span>
          <ElTooltip :content="t(M + 'cacheHint')" placement="top">
            <ElButton text bg :icon="Refresh" :loading="loading" @click="refresh">
              {{ t(M + 'refresh') }}
            </ElButton>
          </ElTooltip>
        </div>
      </template>
      <div class="flex flex-col gap-3">
        <div class="flex flex-wrap items-center gap-x-6 gap-y-2">
          <ElTag :type="overallState === 'ok' ? 'success' : 'warning'">
            {{ overallState === 'ok' ? t(M + 'statusOk') : t(M + 'statusDegraded') }}
          </ElTag>
          <span class="text-sm">
            {{ t(M + 'observedAt') }}：<span class="font-medium">{{ formatTime(dbStatus.observed_at) }}</span>
          </span>
          <span class="text-sm text-gray-500 dark:text-gray-400">{{ cacheText }}</span>
        </div>
        <div class="flex flex-wrap gap-x-8 gap-y-2 text-sm">
          <span>
            {{ t(M + 'totalTables') }}：
            <span class="font-medium">{{ formatCount(tables?.count ?? null) }}</span>
          </span>
          <span>
            {{ t(M + 'totalBytes') }}：
            <span class="font-medium">{{ formatBytes(tables?.total_bytes ?? null) }}</span>
          </span>
        </div>
        <div class="mt-1">
          <div class="text-sm font-medium">{{ t(M + 'warningsTitle') }}</div>
          <ul class="mt-1 flex list-disc flex-col gap-1 pl-5 text-sm text-gray-600 dark:text-gray-300">
            <li v-for="(warning, i) in dbStatus.warnings" :key="i">{{ warning }}</li>
          </ul>
        </div>
      </div>
    </ElCard>

    <!-- 容量配额:仅展示承载平台数据的表空间,每项 used/max + 进度条,合计行 -->
    <ElCard shadow="never">
      <template #header>
        <span class="font-medium">{{ t(M + 'tablespacesTitle') }}</span>
      </template>
      <div class="flex flex-col gap-4">
        <ElAlert
          v-if="tablespaces && !tablespaces.available"
          type="info"
          show-icon
          :closable="false"
          :title="t(M + 'degraded')"
          :description="degradeText(tablespaces.reason, tablespaces.message)"
        />
        <template v-else-if="tablespaces">
          <div
            v-if="applicationTablespaces.length === 0"
            class="text-sm text-gray-500 dark:text-gray-400"
          >
            {{ t(M + 'tablespacesEmpty') }}
          </div>
          <div v-for="ts in applicationTablespaces" :key="ts.name" class="flex flex-col gap-1">
            <div class="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
              <span class="font-medium">{{ ts.name }}</span>
              <span class="text-sm text-gray-600 dark:text-gray-300">
                {{ formatBytes(ts.used_bytes) }} / {{ formatBytes(ts.max_bytes) }}
                · {{ pctText(ts.used_percent) }}
              </span>
            </div>
            <ElProgress
              v-if="ts.used_percent !== null"
              :percentage="clampPct(ts.used_percent)"
              :color="percentColor"
              :show-text="false"
            />
          </div>
          <div v-if="applicationTablespaces.length > 1" class="flex flex-wrap items-baseline justify-between gap-x-3 border-t border-gray-200/70 pt-2 text-sm dark:border-gray-700/70">
            <span class="text-gray-500 dark:text-gray-400">{{ t(M + 'tablespacesTotal') }}</span>
            <span>
              {{ formatBytes(tablespaceTotals.used) }} / {{ formatBytes(tablespaceTotals.max) }}
              · <span class="font-medium">{{ pctText(tablespaceTotals.percent) }}</span>
              <span v-if="tablespaceTotals.anyNull" class="text-gray-500 dark:text-gray-400">
                · {{ t(M + 'totalsPartial') }}
              </span>
            </span>
          </div>
          <div class="text-sm text-gray-500 dark:text-gray-400">{{ t(M + 'maxBytesNote') }}</div>
          <div class="text-sm text-gray-500 dark:text-gray-400">{{ t(M + 'tablespacesScopeNote') }}</div>
        </template>
      </div>
    </ElCard>

    <!-- 每表占用与数据量:字节拆分 + 优化器估算行数 + 统计新鲜度 -->
    <ElCard shadow="never">
      <template #header>
        <span class="font-medium">{{ t(M + 'tablesTitle') }}</span>
      </template>
      <div class="flex flex-col gap-4">
        <ElAlert
          v-if="tables && !tables.available"
          type="info"
          show-icon
          :closable="false"
          :title="t(M + 'degraded')"
          :description="degradeText(tables.reason, tables.message)"
        />
        <template v-else-if="tables">
          <ElTable :data="sortedTableItems" show-summary :summary-method="tableSummaries">
            <ElTableColumn prop="name" :label="t(M + 'colTable')" min-width="140" show-overflow-tooltip />
            <ElTableColumn prop="total_bytes" sortable :label="t(M + 'colTotal')" min-width="96">
              <template #default="{ row }">{{ formatBytes(row.total_bytes) }}</template>
            </ElTableColumn>
            <ElTableColumn prop="table_bytes" sortable :label="t(M + 'colTableBytes')" min-width="96">
              <template #default="{ row }">{{ formatBytes(row.table_bytes) }}</template>
            </ElTableColumn>
            <ElTableColumn prop="index_bytes" sortable :label="t(M + 'colIndexBytes')" min-width="88">
              <template #default="{ row }">{{ formatBytes(row.index_bytes) }}</template>
            </ElTableColumn>
            <ElTableColumn prop="lob_bytes" sortable :label="t(M + 'colLobBytes')" min-width="88">
              <template #default="{ row }">{{ formatBytes(row.lob_bytes) }}</template>
            </ElTableColumn>
            <ElTableColumn prop="lob_index_bytes" sortable :label="t(M + 'colLobIndexBytes')" min-width="108">
              <template #default="{ row }">{{ formatBytes(row.lob_index_bytes) }}</template>
            </ElTableColumn>
            <ElTableColumn prop="estimated_rows" sortable :label="t(M + 'colRows')" min-width="104">
              <template #default="{ row }">{{ formatCount(row.estimated_rows) }}</template>
            </ElTableColumn>
            <ElTableColumn prop="last_analyzed" sortable :label="t(M + 'colAnalyzed')" min-width="172">
              <template #default="{ row }">
                <span v-if="row.last_analyzed" :class="analyzedStale(row.last_analyzed) ? 'opacity-60' : ''">
                  {{ formatTime(row.last_analyzed) }}
                </span>
                <ElTag v-else type="warning" size="small">{{ t(M + 'neverAnalyzed') }}</ElTag>
              </template>
            </ElTableColumn>
          </ElTable>
          <div class="text-sm text-gray-500 dark:text-gray-400">{{ t(M + 'tablesCaption') }}</div>
        </template>
      </div>
    </ElCard>

    <!-- DB 负载与连接池 -->
    <ElCard shadow="never">
      <template #header>
        <div class="flex flex-wrap items-center justify-between gap-x-3 gap-y-2">
          <span class="font-medium">{{ t(M + 'loadTitle') }}</span>
          <span class="text-sm text-gray-500 dark:text-gray-400">
            {{ t(M + 'source') }}：{{ load?.source }}
          </span>
        </div>
      </template>
      <div class="flex flex-col gap-4">
        <!-- V$SYSMETRIC 在部分 ADB 实例不产生采样:属已知口径而非故障,措辞用中性说明 -->
        <ElAlert
          v-if="load && !load.available"
          type="info"
          show-icon
          :closable="false"
          :title="t(M + 'loadNoSamplesTitle')"
          :description="degradeText(load.reason, undefined)"
        />
        <template v-else-if="load">
          <ElTable :data="load.metrics">
            <ElTableColumn prop="name" :label="t(M + 'colMetric')" min-width="160" show-overflow-tooltip />
            <ElTableColumn :label="t(M + 'colValue')" min-width="110">
              <template #default="{ row }">
                {{ formatMetric(row.value) }}<span v-if="row.unit"> {{ row.unit }}</span>
              </template>
            </ElTableColumn>
            <ElTableColumn :label="t(M + 'colWindow')" min-width="250">
              <template #default="{ row }">
                {{ formatTime(row.begin_time) }} ～ {{ formatTime(row.end_time) }}
                <span class="text-gray-500 dark:text-gray-400">（{{ formatInterval(row.interval_centiseconds) }}）</span>
              </template>
            </ElTableColumn>
          </ElTable>
          <div class="text-sm text-gray-500 dark:text-gray-400">{{ t(M + 'assessmentNote') }}</div>

          <!-- 连接池:仅代表当前 API 进程 -->
          <ElAlert
            v-if="pool && !pool.available"
            type="info"
            show-icon
            :closable="false"
            :title="t(M + 'degraded')"
            :description="degradeText(pool.reason, undefined)"
          />
          <div v-else-if="pool" class="flex flex-col gap-2">
            <div class="text-sm font-medium">{{ t(M + 'poolTitle') }}</div>
            <div class="flex flex-wrap gap-x-8 gap-y-2 text-sm">
              <span>
                <span class="text-gray-500 dark:text-gray-400">{{ t(M + 'poolSize') }}</span>
                {{ pool.size ?? '—' }}
              </span>
              <span>
                <span class="text-gray-500 dark:text-gray-400">{{ t(M + 'poolCheckedOut') }}</span>
                {{ pool.checked_out ?? '—' }}
              </span>
              <span>
                <span class="text-gray-500 dark:text-gray-400">{{ t(M + 'poolOverflow') }}</span>
                {{ pool.overflow ?? '—' }}
              </span>
              <span>
                <span class="text-gray-500 dark:text-gray-400">{{ t(M + 'poolCapacity') }}</span>
                {{ pool.capacity ?? '—' }}
              </span>
              <span>
                <span class="text-gray-500 dark:text-gray-400">{{ t(M + 'poolUtilization') }}</span>
                {{ pctText(pool.utilization_percent) }}
              </span>
            </div>
            <ElProgress
              v-if="typeof pool.utilization_percent === 'number'"
              :percentage="clampPct(pool.utilization_percent)"
              :color="percentColor"
              :show-text="false"
            />
            <div class="text-sm text-gray-500 dark:text-gray-400">{{ t(M + 'poolScopeNote') }}</div>
          </div>
        </template>
      </div>
    </ElCard>
  </div>

  <!-- 首次加载 / 读取失败 -->
  <ElCard v-else shadow="never" class="mt-8">
    <template #header>
      <span class="font-medium">{{ t(M + 'title') }}</span>
    </template>
    <div class="flex min-h-24 flex-wrap items-center gap-3">
      <template v-if="loadError && !loading">
        <span class="text-sm text-gray-500 dark:text-gray-400">{{ loadError }}</span>
        <ElButton text bg :icon="Refresh" @click="refresh">
          {{ t('maintenance.recheck') }}
        </ElButton>
      </template>
      <span v-else class="text-sm text-gray-500 dark:text-gray-400">{{ t(M + 'loading') }}</span>
    </div>
  </ElCard>
</template>