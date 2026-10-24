<script setup lang="ts">
import { useRouter } from 'vue-router'
import { useUserStore } from '@/stores/user.ts'
import { ref, computed, watch, onMounted, onBeforeUnmount } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  ElCard, ElTag, ElAlert, ElButton, ElTooltip, ElMessageBox, ElMessage,
  ElTable, ElTableColumn, ElProgress, ElSelect, ElOption, ElOptionGroup, ElSegmented
} from 'element-plus'
import type { TableColumnCtx } from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'
import dayjs from '@/plugins/dayjs'
import { isAxiosError } from 'axios'
import {
  getDatabaseStatus, previewRetention, runRetention, getRetentionProgress,
  runBackup, getBackupProgress,
  getMetricsRealtime, getMetricsHost, getMetricsHistory, getMetricsTopSql, getMetricsEndpoints,
  isRetentionJobStart,
  type DbStatusData, type DbTableItem, type RetentionPreviewData, type RetentionRunData,
  type RetentionProgressData, type DbMetricsRealtimeData, type DbMetricsHistoryData,
  type DbMetricsRange, type DbMetricsTopSqlData, type DbMetricsEndpointsData,
  type DbMetricsHostData, type BackupProgressData
} from '@/api/system/db.ts'
import DbPerfTrendChart from '@/components/server/DbPerfTrendChart.vue'

const router = useRouter()
const userStore = useUserStore()
const { t, te } = useI18n()

if (!userStore.position.includes('admin')) {
  router.replace('/not-found')
}

const isAdmin = userStore.position.includes('admin')

const M = 'server.database.'

// R6:页内两分区 —— 数据库(远端 Oracle) / 本地服务器(主机资源 + 进程池,独立端点)
const activeView = ref<'database' | 'host'>('database')
const viewOptions = computed(() => [
  { label: t(M + 'viewDatabase'), value: 'database' },
  { label: t(M + 'viewHost'), value: 'host' }
])

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

// 各新端点的 403 处理与页面守卫口径一致:token 对应账号已无 admin 权限
function redirectIfForbidden(err: unknown): boolean {
  if (isAxiosError(err) && err.response?.status === 403) {
    router.replace('/not-found')
    return true
  }
  return false
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

// —— 数据保留期清理:预览只统计,执行需二次确认;新版为后台任务 + 进度轮询(§5.5) ——
const retentionPreview = ref<RetentionPreviewData | null>(null)
const retentionRun = ref<RetentionRunData | null>(null)
const previewLoading = ref(false)
const runLoading = ref(false)
const retentionError = ref<{ type: 'warning' | 'error'; text: string } | null>(null)
const retentionProgress = ref<RetentionProgressData | null>(null)
// 进度端点尚未部署(L2 未上线)时退回旧同步形态,不影响既有清理流程
const progressSupported = ref(true)
let progressTimer: number | undefined

const PROGRESS_INTERVAL_MS = 3000
const REALTIME_INTERVAL_MS = 15000
const SLOW_INTERVAL_MS = 60000
const PROBE_RETRY_MS = 300000
const DEFAULT_TOP_SQL_LIMIT = 10
const DEFAULT_ENDPOINTS_LIMIT = 20

const progressRunning = computed(() => retentionProgress.value?.running === true)
const maintenanceActive = computed(() => retentionProgress.value?.maintenance?.enabled === true)

const retentionRows = computed(() => {
  const preview = retentionPreview.value
  if (!preview) return []
  return [
    {
      key: 'app_logs',
      label: t(M + 'appLogsLabel'),
      cutoff: preview.app_logs.cutoff,
      deletable: preview.app_logs.deletable
    },
    {
      key: 'export_tasks',
      label: t(M + 'exportTasksLabel'),
      cutoff: preview.export_tasks.cutoff,
      deletable: preview.export_tasks.deletable
    }
  ]
})

// 维护横幅文案:reason 后端写"数据库清理",缺省时给出同义默认
const maintenanceBannerText = computed(() => {
  const reason = retentionProgress.value?.maintenance?.reason
  return t(M + 'maintenanceBannerBody', {
    reason: reason || t(M + 'maintenanceReasonDefault')
  })
})

const batchPercent = computed(() => {
  const progress = retentionProgress.value?.progress
  if (!progress || progress.max_batches <= 0) return 0
  return clampPct((progress.batches / progress.max_batches) * 100)
})

function phaseLabel(phase: string | null | undefined): string {
  switch (phase) {
    case 'entering_maintenance':
      return t(M + 'phaseEnteringMaintenance')
    case 'purging_app_logs':
      return t(M + 'phasePurgingAppLogs')
    case 'purging_export_tasks':
      return t(M + 'phasePurgingExportTasks')
    case 'leaving_maintenance':
      return t(M + 'phaseLeavingMaintenance')
    case 'done':
      return t(M + 'phaseDone')
    case 'failed':
      return t(M + 'phaseFailed')
    default:
      return phase ?? '—'
  }
}

async function previewRetentionNow() {
  previewLoading.value = true
  retentionError.value = null
  try {
    const res = await previewRetention()
    retentionPreview.value = res.data.data
  } catch (err) {
    if (redirectIfForbidden(err)) return
    retentionError.value = { type: 'error', text: detailOf(err) || t(M + 'retentionPreviewFailed') }
  } finally {
    previewLoading.value = false
  }
}

async function onRunRetention() {
  if (!retentionPreview.value || progressRunning.value) return
  try {
    await ElMessageBox.confirm(
      t(M + 'retentionConfirmBody'),
      t(M + 'retentionConfirmTitle'),
      {
        type: 'warning',
        confirmButtonText: t(M + 'retentionConfirmOk'),
        cancelButtonText: t(M + 'retentionConfirmCancel')
      }
    )
  } catch {
    return
  }
  runLoading.value = true
  retentionError.value = null
  try {
    const res = await runRetention()
    const data = res.data.data
    if (isRetentionJobStart(data)) {
      // 后台任务形态(L2):立即拉一次进度并开启 3 秒轮询
      ElMessage.success(t(M + 'backgroundJobStarted'))
      void refreshProgress()
    } else {
      // 旧后端(同步结果):保持既有行为
      retentionRun.value = data
      void previewRetentionNow()
    }
  } catch (err) {
    if (redirectIfForbidden(err)) return
    if (isAxiosError(err) && err.response?.status === 409) {
      // 409:已有清理在跑,转进度轮询向用户展示当前阶段
      retentionError.value = { type: 'warning', text: detailOf(err) || t(M + 'retentionBusy') }
      void refreshProgress()
    } else {
      retentionError.value = { type: 'error', text: detailOf(err) || t(M + 'retentionRunFailed') }
    }
  } finally {
    runLoading.value = false
  }
}

async function refreshProgress() {
  try {
    const res = await getRetentionProgress()
    const data = res.data.data
    const wasRunning = retentionProgress.value?.running === true
    retentionProgress.value = data
    progressSupported.value = true
    // 页面已知上次运行结果时,先回填"上次执行结果"块
    if (!retentionRun.value && data.last_run) {
      retentionRun.value = data.last_run.result
    }
    if (data.running) {
      ensureProgressPolling()
    } else {
      stopProgressPolling()
      if (wasRunning) {
        // 从 running 收敛到终态:收尾提示并刷新受影响数据
        if (data.phase === 'failed') {
          retentionError.value = { type: 'error', text: data.error || t(M + 'retentionRunFailed') }
        } else {
          ElMessage.success(t(M + 'progressDoneSuccess'))
        }
        if (data.result) retentionRun.value = data.result
        void refresh()
        void previewRetentionNow()
      }
    }
  } catch (err) {
    if (redirectIfForbidden(err)) return
    if (isAxiosError(err) && err.response?.status === 404) {
      // L2 未上线:退回旧同步形态,不再轮询
      progressSupported.value = false
      stopProgressPolling()
      return
    }
    // 503 等瞬时故障:保留旧数据,轮询由 interval 继续
  }
}

function ensureProgressPolling() {
  if (progressTimer === undefined) {
    progressTimer = window.setInterval(() => void refreshProgress(), PROGRESS_INTERVAL_MS)
  }
}

function stopProgressPolling() {
  if (progressTimer !== undefined) {
    window.clearInterval(progressTimer)
    progressTimer = undefined
  }
}

// —— 手动备份:prod → adb2 全量备份,后台任务 + 进度轮询 ——
const backupProgress = ref<BackupProgressData | null>(null)
const backupRunLoading = ref(false)
const backupError = ref<string | null>(null)
let backupTimer: number | undefined

// POST /backup/run 成功后的瞬间,进度端点读到的仍可能是上一次 run 的终态快照:
// 有界启动窗口内(fake running)保持轮询,等新 run 写出 running 行或窗口超时
const BACKUP_START_GRACE_MS = 120000
const backupStarting = ref(false)
let backupStartDeadline = 0

const backupState = computed(() => backupProgress.value?.state ?? 'idle')
const backupRunning = computed(() => backupState.value === 'running')

// 启动窗口内统一按 running 呈现,防止"点完按钮立刻显示上次的成功"
const displayedBackupState = computed<'idle' | 'running' | 'ok' | 'failed'>(() => {
  if (backupStarting.value && !backupRunning.value) return 'running'
  return backupState.value
})

const backupTotalText = computed(() => {
  const total = backupProgress.value?.tables_total
  return total === null || total === undefined ? '—' : total
})

const backupPercent = computed(() => {
  const p = backupProgress.value
  if (!p || p.tables_total === null || p.tables_total <= 0) return 0
  return clampPct((p.tables_done / p.tables_total) * 100)
})

// 启动窗口内的快照明细仍是上一次 run 的数字,不作为当前进度展示
const backupShowDetail = computed(() => {
  if (!backupProgress.value) return false
  return !backupStarting.value || backupRunning.value
})

// 状态语义与 progressStateLabel 同型:未知值回显原文
function backupStateLabel(state: string): string {
  switch (state) {
    case 'running':
      return t(M + 'backupRunning')
    case 'ok':
      return t(M + 'backupOk')
    case 'failed':
      return t(M + 'backupFailed')
    case 'idle':
      return t(M + 'backupIdle')
    default:
      return state
  }
}

function formatElapsedSeconds(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return '—'
  if (seconds < 90) return `${Math.round(seconds)}s`
  return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`
}

async function refreshBackupProgress() {
  try {
    const res = await getBackupProgress()
    const data = res.data.data
    const wasRunning = backupRunning.value
    backupProgress.value = data
    if (data.state === 'running') {
      // 新 run 的进度行已写出,退出启动窗口
      backupStarting.value = false
      ensureBackupPolling()
    } else if (backupStarting.value && Date.now() < backupStartDeadline) {
      // 启动窗口内:快照仍是上一次 run 的终态,保持轮询等新 run 上线
      ensureBackupPolling()
    } else {
      backupStarting.value = false
      stopBackupPolling()
      // 终态:failed 落页内错误(不用 ElMessage),ok 收尾清错误
      if (data.state === 'failed') {
        backupError.value = data.message || t(M + 'backupRunFailed')
      } else if (wasRunning) {
        backupError.value = null
      }
    }
  } catch (err) {
    if (redirectIfForbidden(err)) return
    // 503 等瞬时故障:保留旧数据,轮询由 interval 继续
  }
}

function ensureBackupPolling() {
  if (backupTimer === undefined) {
    backupTimer = window.setInterval(() => void refreshBackupProgress(), PROGRESS_INTERVAL_MS)
  }
}

function stopBackupPolling() {
  if (backupTimer !== undefined) {
    window.clearInterval(backupTimer)
    backupTimer = undefined
  }
}

async function onRunBackup() {
  try {
    await ElMessageBox.confirm(
      t(M + 'backupConfirmBody'),
      t(M + 'backupConfirmTitle'),
      {
        type: 'warning',
        confirmButtonText: t(M + 'backupConfirmOk'),
        cancelButtonText: t(M + 'backupConfirmCancel')
      }
    )
  } catch {
    return
  }
  backupRunLoading.value = true
  backupError.value = null
  try {
    await runBackup()
    // 进度端点此刻可能仍读到上一次 run 的终态:进入有界启动窗口,
    // 立即拉一次进度并开启 3 秒轮询,窗口内按 running 呈现
    backupStarting.value = true
    backupStartDeadline = Date.now() + BACKUP_START_GRACE_MS
    ensureBackupPolling()
    void refreshBackupProgress()
  } catch (err) {
    if (redirectIfForbidden(err)) return
    backupError.value = detailOf(err) || t(M + 'backupRunFailed')
  } finally {
    backupRunLoading.value = false
  }
}

// —— 性能采集实时面板(§5.1):15 秒轮询,维护/清理期间暂停 ——
const metricsAvailable = ref<boolean | null>(null)
const metricsRealtime = ref<DbMetricsRealtimeData | null>(null)
const realtimeLoading = ref(false)
const realtimeError = ref(false)

// —— 趋势历史(§5.2) ——
const historyData = ref<DbMetricsHistoryData | null>(null)
const historyLoading = ref(false)
const historyError = ref(false)
const historySupported = ref(true)
let historySeq = 0
const trendRange = ref<DbMetricsRange>('24h')
const trendFamily = ref('cpu')

// —— Top 慢 SQL(§5.3) / 慢端点(§5.4) ——
const topSql = ref<DbMetricsTopSqlData | null>(null)
const topSqlLoading = ref(false)
const topSqlError = ref(false)
const topSqlSupported = ref(true)
const endpoints = ref<DbMetricsEndpointsData | null>(null)
const endpointsLoading = ref(false)
const endpointsError = ref(false)
const endpointsSupported = ref(true)

let realtimeTimer: number | undefined
let slowTimer: number | undefined
let probeTimer: number | undefined

const rtLoad = computed(() => metricsRealtime.value?.load ?? null)
const rtPool = computed(() => metricsRealtime.value?.pool ?? null)
const rtSessions = computed(() => metricsRealtime.value?.sessions ?? null)
const rtObservedText = computed(() => formatTime(metricsRealtime.value?.observed_at))

// —— 本地服务器快照:独立端点 /system/db/metrics/host(host/pool 字段与 /realtime 一致,零 Oracle 往返) ——
const hostSnapshot = ref<DbMetricsHostData | null>(null)
const hostRes = computed(() => hostSnapshot.value?.host ?? null)
const hostPool = computed(() => hostSnapshot.value?.pool ?? null)
const hostObservedText = computed(() => formatTime(hostSnapshot.value?.observed_at))
const hostAvailable = ref<boolean | null>(null)
const hostLoading = ref(false)
const hostError = ref(false)
let hostTimer: number | undefined
let hostProbeTimer: number | undefined

const hostMemUsedPct = computed(() => {
  const mem = hostRes.value?.mem
  if (!mem || mem.total_mb <= 0) return null
  return (mem.used_mb / mem.total_mb) * 100
})

// 等待类别分布:由会话明细按 wait_class 就地聚合,无需等慢档采样
const WAIT_COLORS = ['#409eff', '#67c23a', '#e6a23c', '#f56c6c', '#909399', '#337ecc', '#529b2e', '#b88230']

const waitDistribution = computed(() => {
  const sessions = rtSessions.value
  if (!sessions || sessions.items.length === 0) return []
  const counts = new Map<string, number>()
  for (const item of sessions.items) {
    if (!item.wait_class) continue
    counts.set(item.wait_class, (counts.get(item.wait_class) ?? 0) + 1)
  }
  return [...counts.entries()]
    .map(([cls, count]) => ({ cls, count, pct: (count / sessions.total) * 100 }))
    .sort((a, b) => b.count - a.count)
    .map((row, index) => ({ ...row, color: WAIT_COLORS[index % WAIT_COLORS.length] }))
})

const waitClasses = computed(() => {
  const seen: string[] = []
  for (const item of rtSessions.value?.items ?? []) {
    if (item.wait_class && !seen.includes(item.wait_class)) seen.push(item.wait_class)
  }
  return seen
})

// —— 趋势分组(§4.1 冻结键,前端只做分组切换) ——
interface TrendFamily {
  key: string
  groupKey: string
  labelKey: string
  series: string[]
  beginAtZero: boolean
  yMax?: number
}

const STATIC_TREND_FAMILIES: TrendFamily[] = [
  {
    key: 'cpu', groupKey: 'trendGroupHost', labelKey: 'trendFamilyCpu',
    series: ['host.cpu.user_pct', 'host.cpu.system_pct', 'host.cpu.idle_pct', 'host.cpu.iowait_pct'],
    beginAtZero: true, yMax: 100
  },
  {
    key: 'mem', groupKey: 'trendGroupHost', labelKey: 'trendFamilyMem',
    series: ['host.mem.used_mb', 'host.mem.available_mb', 'host.mem.total_mb'],
    beginAtZero: false
  },
  {
    key: 'load', groupKey: 'trendGroupHost', labelKey: 'trendFamilyLoad',
    series: ['host.load.1m', 'host.load.5m', 'host.load.15m'],
    beginAtZero: true
  },
  {
    key: 'disk', groupKey: 'trendGroupHost', labelKey: 'trendFamilyDisk',
    series: ['host.disk.used_pct'],
    beginAtZero: true, yMax: 100
  },
  {
    key: 'net', groupKey: 'trendGroupHost', labelKey: 'trendFamilyNet',
    series: ['host.net.rx_kbps', 'host.net.tx_kbps'],
    beginAtZero: false
  },
  {
    key: 'diskio', groupKey: 'trendGroupHost', labelKey: 'trendFamilyDiskIo',
    series: ['host.diskio.read_kbps', 'host.diskio.write_kbps'],
    beginAtZero: false
  },
  {
    key: 'oracle-load', groupKey: 'trendGroupOracle', labelKey: 'trendFamilyOracleLoad',
    series: [
      'oracle.load.exec_per_sec', 'oracle.load.commits_per_sec', 'oracle.load.cpu_cs_per_sec',
      'oracle.load.logical_reads_per_sec', 'oracle.load.physical_reads_per_sec', 'oracle.load.parse_per_sec'
    ],
    beginAtZero: false
  },
  {
    key: 'sessions', groupKey: 'trendGroupOracle', labelKey: 'trendFamilySessions',
    series: ['oracle.session.total', 'oracle.session.active', 'oracle.session.inactive', 'oracle.aas.5m'],
    beginAtZero: true
  },
  {
    key: 'pool', groupKey: 'trendGroupPool', labelKey: 'trendFamilyPool',
    series: ['pool.size', 'pool.checkedout', 'pool.overflow', 'pool.capacity', 'pool.waited'],
    beginAtZero: true
  }
]

// 动态键(表空间名 / 等待类别)从既有实时数据推导,单组最多 12 个 key(§5.2 上限)
const trendFamilies = computed<TrendFamily[]>(() => {
  const dynamic: TrendFamily[] = []
  const tablespaceNames = applicationTablespaces.value.map((ts) => ts.name).slice(0, 12)
  if (tablespaceNames.length > 0) {
    dynamic.push({
      key: 'tablespace', groupKey: 'trendGroupOther', labelKey: 'trendFamilyTablespace',
      series: tablespaceNames.map((name) => `oracle.tablespace.${name}.used_pct`),
      beginAtZero: true, yMax: 100
    })
  }
  const classes = waitClasses.value.slice(0, 12)
  if (classes.length > 0) {
    dynamic.push({
      key: 'wait', groupKey: 'trendGroupOther', labelKey: 'trendFamilyWait',
      series: classes.map((cls) => `oracle.wait.${cls}.sessions`),
      beginAtZero: true
    })
  }
  return [...STATIC_TREND_FAMILIES, ...dynamic]
})

// 当前分组被移除(会话明细里等待类别消失等)时回落到 CPU
watch(trendFamilies, (families) => {
  if (!families.some((family) => family.key === trendFamily.value)) {
    trendFamily.value = 'cpu'
  }
})

const trendFamilyGroups = computed(() => {
  const groups: { key: string; items: TrendFamily[] }[] = []
  for (const family of trendFamilies.value) {
    let group = groups.find((g) => g.key === family.groupKey)
    if (!group) {
      group = { key: family.groupKey, items: [] }
      groups.push(group)
    }
    group.items.push(family)
  }
  return groups
})

const rangeOptions = computed(() => [
  { label: t(M + 'range1h'), value: '1h' },
  { label: t(M + 'range6h'), value: '6h' },
  { label: t(M + 'range24h'), value: '24h' },
  { label: t(M + 'range7d'), value: '7d' }
])

const currentTrendFamily = computed(() =>
  trendFamilies.value.find((family) => family.key === trendFamily.value) ?? null
)

// §4.1 冻结键的展示名;动态键用参数模板,未登记的键回显原文
function seriesLabel(key: string): string {
  if (key.startsWith('oracle.tablespace.') && key.endsWith('.used_pct')) {
    const name = key.slice('oracle.tablespace.'.length, -'.used_pct'.length)
    return t(M + 'perfSeries.tablespaceUsedPct', { name })
  }
  if (key.startsWith('oracle.wait.') && key.endsWith('.sessions')) {
    const cls = key.slice('oracle.wait.'.length, -'.sessions'.length)
    return t(M + 'perfSeries.waitSessions', { cls })
  }
  const path = M + 'perfSeries.' + key
  if (te(path)) return t(path)
  return key
}

interface TrendSeriesInput {
  key: string
  label: string
  unit: string | null
  points: { ts: string; value: number | null }[]
}

const trendSeries = computed<TrendSeriesInput[]>(() => {
  const data = historyData.value
  if (!data) return []
  const unknown = new Set(data.unknown_series ?? [])
  return data.series
    .filter((s) => !unknown.has(s.key))
    .map((s) => ({ key: s.key, label: seriesLabel(s.key), unit: s.unit ?? null, points: s.points }))
})

const unknownTrendKeys = computed(() => (historyData.value?.unknown_series ?? []).join(', '))

const trendEmptyText = computed(() => {
  if (historyError.value) return t(M + 'trendLoadFailed')
  if (historyData.value && historyData.value.series.length === 0) return t(M + 'trendAllUnknown')
  return t(M + 'trendEmpty')
})

watch(trendRange, () => {
  void refreshHistory()
})

watch(trendFamily, () => {
  void refreshHistory()
})

// 慢端点按 P95 降序,"最慢的排最前"
const endpointRows = computed(() => {
  return [...(endpoints.value?.items ?? [])].sort((a, b) => b.p95_ms - a.p95_ms)
})

// —— 拉取与轮询调度 ——
async function refreshRealtimeData() {
  const res = await getMetricsRealtime()
  metricsRealtime.value = res.data.data
  realtimeError.value = false
}

async function probeMetrics() {
  stopMetricsPolling()
  if (probeTimer !== undefined) {
    window.clearTimeout(probeTimer)
    probeTimer = undefined
  }
  realtimeLoading.value = true
  try {
    await refreshRealtimeData()
  } catch (err) {
    if (redirectIfForbidden(err)) return
    // 新端点尚不可用:整区降级为空态,低频重试等待后端上线
    metricsAvailable.value = false
    metricsRealtime.value = null
    scheduleProbeRetry()
    return
  } finally {
    realtimeLoading.value = false
  }
  metricsAvailable.value = true
  await Promise.all([refreshHistory(), refreshTopSql(), refreshEndpoints()])
  startMetricsPolling()
}

function scheduleProbeRetry() {
  if (probeTimer !== undefined) return
  probeTimer = window.setTimeout(() => {
    probeTimer = undefined
    if (metricsAvailable.value !== true) void probeMetrics()
  }, PROBE_RETRY_MS)
}

async function realtimeTick() {
  if (realtimeLoading.value) return
  realtimeLoading.value = true
  try {
    await refreshRealtimeData()
  } catch (err) {
    if (redirectIfForbidden(err)) return
    if (isAxiosError(err) && err.response?.status === 404) {
      metricsAvailable.value = false
      metricsRealtime.value = null
      stopMetricsPolling()
      scheduleProbeRetry()
      return
    }
    // 503 等瞬时故障:保留上一次成功数据,只提示不中断
    realtimeError.value = true
  } finally {
    realtimeLoading.value = false
  }
}

async function refreshHistory() {
  if (metricsAvailable.value !== true || !historySupported.value) return
  const family = trendFamilies.value.find((f) => f.key === trendFamily.value)
  if (!family) return
  const seq = ++historySeq
  historyLoading.value = true
  try {
    const res = await getMetricsHistory({ series: family.series, range: trendRange.value })
    if (seq === historySeq) {
      historyData.value = res.data.data
      historyError.value = false
    }
  } catch (err) {
    if (redirectIfForbidden(err)) return
    if (isAxiosError(err) && err.response?.status === 404) {
      historySupported.value = false
      if (seq === historySeq) historyError.value = true
      return
    }
    if (seq === historySeq) historyError.value = true
  } finally {
    if (seq === historySeq) historyLoading.value = false
  }
}

async function refreshTopSql() {
  if (metricsAvailable.value !== true || !topSqlSupported.value) return
  topSqlLoading.value = true
  try {
    const res = await getMetricsTopSql(DEFAULT_TOP_SQL_LIMIT)
    topSql.value = res.data.data
    topSqlError.value = false
  } catch (err) {
    if (redirectIfForbidden(err)) return
    if (isAxiosError(err) && err.response?.status === 404) topSqlSupported.value = false
    topSqlError.value = true
  } finally {
    topSqlLoading.value = false
  }
}

async function refreshEndpoints() {
  if (metricsAvailable.value !== true || !endpointsSupported.value) return
  endpointsLoading.value = true
  try {
    const res = await getMetricsEndpoints('1h', DEFAULT_ENDPOINTS_LIMIT)
    endpoints.value = res.data.data
    endpointsError.value = false
  } catch (err) {
    if (redirectIfForbidden(err)) return
    if (isAxiosError(err) && err.response?.status === 404) endpointsSupported.value = false
    endpointsError.value = true
  } finally {
    endpointsLoading.value = false
  }
}

async function slowTick() {
  await Promise.all([refreshHistory(), refreshTopSql(), refreshEndpoints()])
}

function startMetricsPolling() {
  stopMetricsPolling()
  // 维护/清理期间暂停对自身 API 与 Oracle 的额外压力,结束后由 watcher 恢复
  if (maintenanceActive.value) return
  realtimeTimer = window.setInterval(() => void realtimeTick(), REALTIME_INTERVAL_MS)
  slowTimer = window.setInterval(() => void slowTick(), SLOW_INTERVAL_MS)
}

function stopMetricsPolling() {
  if (realtimeTimer !== undefined) {
    window.clearInterval(realtimeTimer)
    realtimeTimer = undefined
  }
  if (slowTimer !== undefined) {
    window.clearInterval(slowTimer)
    slowTimer = undefined
  }
}

// —— 本地服务器端点(零 Oracle):仅本地面板激活时轮询,节奏与实时面板一致(15 秒) ——

function startHostPolling() {
  stopHostPolling()
  // 维护/清理期间与实时面板同口径暂停,结束后由 watcher 恢复
  if (maintenanceActive.value) return
  hostTimer = window.setInterval(() => void hostTick(), REALTIME_INTERVAL_MS)
}

function stopHostPolling() {
  if (hostTimer !== undefined) {
    window.clearInterval(hostTimer)
    hostTimer = undefined
  }
}

async function refreshHostSnapshot() {
  if (hostProbeTimer !== undefined) {
    window.clearTimeout(hostProbeTimer)
    hostProbeTimer = undefined
  }
  hostLoading.value = true
  try {
    const res = await getMetricsHost()
    hostSnapshot.value = res.data.data
    hostError.value = false
    const wasUnavailable = hostAvailable.value !== true
    hostAvailable.value = true
    if (wasUnavailable && activeView.value === 'host') startHostPolling()
  } catch (err) {
    if (redirectIfForbidden(err)) return
    if (isAxiosError(err) && err.response?.status === 404) {
      // 后端尚未上线:整区降级,低频重试等待上线
      hostAvailable.value = false
      stopHostPolling()
      scheduleHostProbeRetry()
      return
    }
    // 503 等瞬时故障:保留上一次成功数据,轮询继续
    hostError.value = true
  } finally {
    hostLoading.value = false
  }
}

function hostTick() {
  if (hostLoading.value) return
  void refreshHostSnapshot()
}

function scheduleHostProbeRetry() {
  if (hostProbeTimer !== undefined) return
  hostProbeTimer = window.setTimeout(() => {
    void refreshHostSnapshot()
  }, PROBE_RETRY_MS)
}

// 分区切换:L4 事实 —— /realtime 与 /host 的速率计算共享 host_metrics 模块级 _previous 状态,
// 两个端点同时轮询会互相扰动速率窗口。因此轮询互斥:只允许激活 tab 轮询自己的端点。
watch(activeView, (view) => {
  if (view === 'host') {
    // 停掉数据库侧 /realtime + 趋势/慢档轮询,再启动本地轮询
    stopMetricsPolling()
    void refreshHostSnapshot()
    if (hostAvailable.value === true) startHostPolling()
  } else {
    stopHostPolling()
    if (metricsAvailable.value === true) {
      // 回到数据库分区:先即时刷一轮实时面板(轮询暂停期间数据已过期),再恢复既有轮询节奏
      void realtimeTick()
      startMetricsPolling()
    }
  }
})

// 维护开启/解除时停启轮询;解除后只恢复当前激活分区的轮询(两侧端点互斥,L4 事实)
watch(maintenanceActive, (active) => {
  if (active) {
    stopMetricsPolling()
    stopHostPolling()
  } else if (activeView.value === 'host') {
    if (hostAvailable.value === true) startHostPolling()
  } else if (metricsAvailable.value === true) {
    startMetricsPolling()
  }
})

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

function formatMb(mb: number | null | undefined): string {
  if (mb === null || mb === undefined) return '—'
  return mb >= 1024 ? `${(mb / 1024).toFixed(2)} GB` : `${mb.toFixed(1)} MB`
}

function formatGb(gb: number | null | undefined): string {
  return gb === null || gb === undefined ? '—' : `${gb.toFixed(1)} GB`
}

function formatKbps(kbps: number | null | undefined): string {
  return kbps === null || kbps === undefined ? '—' : `${kbps.toFixed(1)} KB/s`
}

function formatHours(hours: number | null | undefined): string {
  return hours === null || hours === undefined ? '—' : `${hours.toFixed(1)} h`
}

function formatWaitSeconds(seconds: number | null | undefined): string {
  return seconds === null || seconds === undefined ? '—' : `${Math.round(seconds)}s`
}

function formatDurationMs(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return '—'
  if (ms < 1000) return `${Math.round(ms)} ms`
  if (ms < 60000) return `${(ms / 1000).toFixed(2)} s`
  if (ms < 3600000) return `${Math.floor(ms / 60000)}m ${Math.round((ms % 60000) / 1000)}s`
  return `${Math.floor(ms / 3600000)}h ${Math.round((ms % 3600000) / 60000)}m`
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

// —— 会话状态标签配色 ——
function sessionStatusTag(status: string | null): 'success' | 'info' | 'warning' {
  if (status === 'ACTIVE') return 'success'
  if (status === 'INACTIVE') return 'info'
  return 'warning'
}

const METHOD_TAG_TYPES: Record<string, 'success' | 'primary' | 'warning' | 'danger'> = {
  GET: 'success',
  POST: 'primary',
  PUT: 'warning',
  PATCH: 'warning',
  DELETE: 'danger'
}

function methodTagType(method: string): 'success' | 'warning' | 'danger' | 'info' | 'primary' {
  return METHOD_TAG_TYPES[method.toUpperCase()] ?? 'info'
}

if (isAdmin) {
  onMounted(() => {
    void refresh()
    void refreshProgress()
    void refreshBackupProgress()
    void probeMetrics()
    // 注意:初始分区是「数据库」,不预拉 /metrics/host —— 该请求同样推进 host_metrics
    // 的模块级 _previous 状态,会扰动 /realtime 的速率计算(L4 事实)
  })
}

onBeforeUnmount(() => {
  stopProgressPolling()
  stopBackupPolling()
  stopMetricsPolling()
  stopHostPolling()
  if (probeTimer !== undefined) {
    window.clearTimeout(probeTimer)
    probeTimer = undefined
  }
  if (hostProbeTimer !== undefined) {
    window.clearTimeout(hostProbeTimer)
    hostProbeTimer = undefined
  }
})
</script>

<template>
  <!-- 数据库监控:仅 admin 可见;页内 ElSegmented 分区(数据库 / 本地服务器) -->
  <div class="flex flex-col gap-3">
    <div>
      <ElSegmented v-model="activeView" :options="viewOptions" />
    </div>

    <!-- 本地服务器:仅依赖独立 host 端点,首屏不等待 Oracle -->
    <template v-if="activeView === 'host'">
      <ElCard shadow="never">
        <template #header>
          <div class="flex flex-wrap items-center justify-between gap-x-3 gap-y-2">
            <span class="font-medium">{{ t(M + 'hostCardTitle') }}</span>
            <div class="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-gray-500 dark:text-gray-400">
              <span v-if="hostSnapshot && hostObservedText !== '—'">
                {{ t(M + 'perfRefreshedAt', { time: hostObservedText }) }}
              </span>
              <ElButton text bg size="small" :icon="Refresh" :loading="hostLoading" @click="refreshHostSnapshot">
                {{ t(M + 'refresh') }}
              </ElButton>
            </div>
          </div>
        </template>
        <div class="flex flex-col gap-4">
          <ElAlert
            v-if="hostAvailable === false"
            type="info"
            show-icon
            :closable="false"
            :title="t(M + 'hostUnavailable')"
          />
          <template v-else-if="hostRes">
            <div v-if="hostError" class="text-sm text-gray-500 dark:text-gray-400">
              {{ t(M + 'hostRefreshFailed') }}
            </div>

            <!-- 主机资源:/proc 即时读,按指标域分瓦片 -->
            <section class="flex flex-col gap-2">
              <div class="text-sm font-medium">{{ t(M + 'hostTitle') }}</div>
              <div class="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
                <!-- CPU -->
                <div class="flex flex-col gap-1.5 yg-tile">
                  <span class="yg-text-xs yg-text-tertiary">{{ t(M + 'hostCpu') }}</span>
                  <div class="flex h-2 w-full overflow-hidden bg-gray-200 dark:bg-gray-700">
                    <div class="h-full" :style="{ width: `${Math.max(0, hostRes.cpu.user_pct)}%`, background: '#409eff' }"></div>
                    <div class="h-full" :style="{ width: `${Math.max(0, hostRes.cpu.system_pct)}%`, background: '#e6a23c' }"></div>
                    <div class="h-full" :style="{ width: `${Math.max(0, hostRes.cpu.iowait_pct)}%`, background: '#f56c6c' }"></div>
                    <div class="h-full" :style="{ width: `${Math.max(0, hostRes.cpu.idle_pct)}%`, background: '#9ca3af' }"></div>
                  </div>
                  <div class="flex flex-wrap gap-x-2.5 gap-y-0.5 yg-text-xs yg-text-tertiary">
                    <span>{{ t(M + 'hostCpuUser') }} {{ pctText(hostRes.cpu.user_pct) }}</span>
                    <span>{{ t(M + 'hostCpuSystem') }} {{ pctText(hostRes.cpu.system_pct) }}</span>
                    <span>{{ t(M + 'hostCpuIowait') }} {{ pctText(hostRes.cpu.iowait_pct) }}</span>
                    <span>{{ t(M + 'hostCpuIdle') }} {{ pctText(hostRes.cpu.idle_pct) }}</span>
                  </div>
                </div>
                <!-- 内存 -->
                <div class="flex flex-col gap-1.5 yg-tile">
                  <span class="yg-text-xs yg-text-tertiary">{{ t(M + 'hostMem') }}</span>
                  <span class="font-medium tabular-nums">
                    {{ formatMb(hostRes.mem.used_mb) }}
                    <span class="yg-text-xs yg-text-tertiary">/ {{ formatMb(hostRes.mem.total_mb) }}</span>
                  </span>
                  <ElProgress
                    v-if="hostMemUsedPct !== null"
                    :percentage="clampPct(hostMemUsedPct)"
                    :color="percentColor"
                    :show-text="false"
                  />
                  <span class="yg-text-xs yg-text-tertiary">
                    {{ t(M + 'hostMemAvailable') }} {{ formatMb(hostRes.mem.available_mb) }}
                  </span>
                </div>
                <!-- 系统负载 -->
                <div class="flex flex-col gap-1.5 yg-tile">
                  <span class="yg-text-xs yg-text-tertiary">{{ t(M + 'hostLoad') }}</span>
                  <div class="flex flex-col gap-0.5 text-sm tabular-nums">
                    <span>{{ t(M + 'hostLoad1m') }} <span class="font-medium">{{ formatMetric(hostRes.load['1m']) }}</span></span>
                    <span>{{ t(M + 'hostLoad5m') }} <span class="font-medium">{{ formatMetric(hostRes.load['5m']) }}</span></span>
                    <span>{{ t(M + 'hostLoad15m') }} <span class="font-medium">{{ formatMetric(hostRes.load['15m']) }}</span></span>
                  </div>
                </div>
                <!-- 磁盘 -->
                <div class="flex flex-col gap-1.5 yg-tile">
                  <span class="yg-text-xs yg-text-tertiary">
                    {{ t(M + 'hostDiskMount', { mount: hostRes.disk.mount }) }}
                  </span>
                  <span class="font-medium tabular-nums">{{ pctText(hostRes.disk.used_pct) }}</span>
                  <ElProgress
                    :percentage="clampPct(hostRes.disk.used_pct)"
                    :color="percentColor"
                    :show-text="false"
                  />
                  <span class="yg-text-xs yg-text-tertiary">
                    {{ t(M + 'hostDiskFree') }} {{ formatGb(hostRes.disk.free_gb) }}
                  </span>
                </div>
                <!-- 网络 -->
                <div class="flex flex-col gap-1.5 yg-tile">
                  <span class="yg-text-xs yg-text-tertiary">{{ t(M + 'hostNet') }}</span>
                  <div class="flex flex-col gap-0.5 text-sm tabular-nums">
                    <span>
                      <span class="text-gray-500 dark:text-gray-400">{{ t(M + 'hostNetRx') }}</span>
                      {{ formatKbps(hostRes.net.rx_kbps) }}
                    </span>
                    <span>
                      <span class="text-gray-500 dark:text-gray-400">{{ t(M + 'hostNetTx') }}</span>
                      {{ formatKbps(hostRes.net.tx_kbps) }}
                    </span>
                  </div>
                </div>
                <!-- 磁盘 IO -->
                <div class="flex flex-col gap-1.5 yg-tile">
                  <span class="yg-text-xs yg-text-tertiary">{{ t(M + 'hostDiskIo') }}</span>
                  <div class="flex flex-col gap-0.5 text-sm tabular-nums">
                    <span>
                      <span class="text-gray-500 dark:text-gray-400">{{ t(M + 'hostDiskIoRead') }}</span>
                      {{ formatKbps(hostRes.diskio.read_kbps) }}
                    </span>
                    <span>
                      <span class="text-gray-500 dark:text-gray-400">{{ t(M + 'hostDiskIoWrite') }}</span>
                      {{ formatKbps(hostRes.diskio.write_kbps) }}
                    </span>
                  </div>
                </div>
                <!-- 后端进程 -->
                <div class="flex flex-col gap-1.5 yg-tile">
                  <span class="yg-text-xs yg-text-tertiary">{{ t(M + 'hostProc') }}</span>
                  <div class="flex flex-col gap-0.5 text-sm tabular-nums">
                    <span>
                      <span class="text-gray-500 dark:text-gray-400">{{ t(M + 'hostProcRss') }}</span>
                      {{ formatMb(hostRes.proc.rss_mb) }}
                    </span>
                    <span>
                      <span class="text-gray-500 dark:text-gray-400">{{ t(M + 'hostProcUptime') }}</span>
                      {{ formatHours(hostRes.proc.uptime_hours) }}
                    </span>
                  </div>
                </div>
              </div>
            </section>

            <!-- 进程池(host/pool 口径与实时面板一致) -->
            <section v-if="hostPool" class="flex flex-col gap-2">
              <div class="text-sm font-medium">{{ t(M + 'poolTitle') }}</div>
              <div class="flex flex-wrap gap-x-8 gap-y-2 text-sm tabular-nums">
                <span>
                  <span class="text-gray-500 dark:text-gray-400">{{ t(M + 'poolSize') }}</span>
                  {{ hostPool.size }}
                </span>
                <span>
                  <span class="text-gray-500 dark:text-gray-400">{{ t(M + 'poolCheckedOut') }}</span>
                  {{ hostPool.checkedout }}
                </span>
                <span>
                  <span class="text-gray-500 dark:text-gray-400">{{ t(M + 'poolOverflow') }}</span>
                  {{ hostPool.overflow }}
                </span>
                <span>
                  <span class="text-gray-500 dark:text-gray-400">{{ t(M + 'poolCapacity') }}</span>
                  {{ hostPool.capacity }}
                </span>
                <span>
                  <span class="text-gray-500 dark:text-gray-400">{{ t(M + 'poolWaited') }}</span>
                  {{ hostPool.waited }}
                </span>
                <span>
                  <span class="text-gray-500 dark:text-gray-400">{{ t(M + 'poolUtilization') }}</span>
                  {{ pctText(hostPool.utilization_percent) }}
                </span>
              </div>
              <ElProgress :percentage="clampPct(hostPool.utilization_percent)" :color="percentColor" :show-text="false" />
              <div class="text-sm text-gray-500 dark:text-gray-400">{{ t(M + 'poolScopeNote') }}</div>
            </section>
          </template>
          <div v-else class="flex min-h-24 items-center text-sm text-gray-500 dark:text-gray-400">
            {{ t(M + 'loading') }}
          </div>
        </div>
      </ElCard>
    </template>

    <template v-else-if="dbStatus">
    <!-- 概览 + 容量并排(Swiss 栅格):宽屏左右分栏,窄屏纵向 -->
    <div class="grid grid-cols-1 gap-3 xl:grid-cols-2 xl:items-start">
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
          <div v-if="applicationTablespaces.length > 1" class="flex flex-wrap items-baseline justify-between gap-x-3 border-t border-gray-200 pt-2 text-sm dark:border-gray-700">
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
    </div>

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
          <ElTable :data="sortedTableItems" show-summary :summary-method="tableSummaries" :max-height="440">
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
          <ElTable :data="load.metrics" :max-height="360">
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

    <!-- 性能监控:实时面板(新端点 §5.1,后端未上线时整区降级) -->
    <ElCard shadow="never">
      <template #header>
        <div class="flex flex-wrap items-center justify-between gap-x-3 gap-y-2">
          <div class="flex flex-wrap items-center gap-2">
            <span class="font-medium">{{ t(M + 'perfTitle') }}</span>
            <ElTag v-if="metricsAvailable && !maintenanceActive" size="small" type="success" effect="plain" round>
              <span class="perf-live" aria-hidden="true"></span>
              {{ t(M + 'perfLiveTag') }}
            </ElTag>
            <ElTag v-else-if="metricsAvailable && maintenanceActive" size="small" type="warning" effect="plain" round>
              {{ t(M + 'perfPausedTag') }}
            </ElTag>
          </div>
          <div class="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-gray-500 dark:text-gray-400">
            <span v-if="metricsRealtime">{{ t(M + 'perfRefreshedAt', { time: rtObservedText }) }}</span>
            <ElButton text bg size="small" :icon="Refresh" :loading="realtimeLoading" @click="probeMetrics">
              {{ t(M + 'refresh') }}
            </ElButton>
          </div>
        </div>
      </template>
      <div class="flex flex-col gap-4">
        <ElAlert
          v-if="metricsAvailable === false"
          type="info"
          show-icon
          :closable="false"
          :title="t(M + 'perfUnavailable')"
        />
        <template v-else-if="metricsRealtime">
          <div v-if="realtimeError" class="text-sm text-gray-500 dark:text-gray-400">
            {{ t(M + 'perfRefreshFailed') }}
          </div>
          <div v-if="maintenanceActive" class="text-sm text-gray-500 dark:text-gray-400">
            {{ t(M + 'perfPausedMaintenance') }}
          </div>

          <!-- Oracle 负载 + 连接池 -->
          <div class="grid grid-cols-1 gap-4 xl:grid-cols-2">
            <section class="flex flex-col gap-2">
              <div class="text-sm font-medium">{{ t(M + 'oracleLoadTitle') }}</div>
              <ElAlert
                v-if="rtLoad && !rtLoad.available"
                type="info"
                show-icon
                :closable="false"
                :title="t(M + 'degraded')"
                :description="degradeText(rtLoad.reason, undefined)"
              />
              <div v-else class="grid grid-cols-2 gap-x-6 gap-y-1.5 text-sm tabular-nums sm:grid-cols-3">
                <div v-for="(metric, i) in rtLoad?.metrics ?? []" :key="i">
                  <span class="text-gray-500 dark:text-gray-400">{{ metric.name }}</span>
                  <span class="ml-1 font-medium">
                    {{ formatMetric(metric.value) }}<span v-if="metric.unit"> {{ metric.unit }}</span>
                  </span>
                </div>
              </div>
              <div v-if="rtSessions" class="flex flex-wrap gap-x-6 gap-y-1 text-sm tabular-nums">
                <span>
                  <span class="text-gray-500 dark:text-gray-400">{{ t(M + 'sessionsTotal') }}</span>
                  {{ formatCount(rtSessions.total) }}
                </span>
                <span class="text-emerald-600 dark:text-emerald-400">
                  {{ t(M + 'sessionsActive') }} {{ formatCount(rtSessions.active) }}
                </span>
                <span class="text-gray-500 dark:text-gray-400">
                  {{ t(M + 'sessionsInactive') }} {{ formatCount(rtSessions.inactive) }}
                </span>
              </div>
            </section>

            <!-- 连接池(新键,口径同既有卡,额外含因满等待次数) -->
            <section v-if="rtPool" class="flex flex-col gap-2">
              <div class="text-sm font-medium">{{ t(M + 'poolTitle') }}</div>
              <div class="flex flex-wrap gap-x-8 gap-y-2 text-sm tabular-nums">
                <span>
                  <span class="text-gray-500 dark:text-gray-400">{{ t(M + 'poolSize') }}</span>
                  {{ rtPool.size }}
                </span>
                <span>
                  <span class="text-gray-500 dark:text-gray-400">{{ t(M + 'poolCheckedOut') }}</span>
                  {{ rtPool.checkedout }}
                </span>
                <span>
                  <span class="text-gray-500 dark:text-gray-400">{{ t(M + 'poolOverflow') }}</span>
                  {{ rtPool.overflow }}
                </span>
                <span>
                  <span class="text-gray-500 dark:text-gray-400">{{ t(M + 'poolCapacity') }}</span>
                  {{ rtPool.capacity }}
                </span>
                <span>
                  <span class="text-gray-500 dark:text-gray-400">{{ t(M + 'poolWaited') }}</span>
                  {{ rtPool.waited }}
                </span>
                <span>
                  <span class="text-gray-500 dark:text-gray-400">{{ t(M + 'poolUtilization') }}</span>
                  {{ pctText(rtPool.utilization_percent) }}
                </span>
              </div>
              <ElProgress :percentage="clampPct(rtPool.utilization_percent)" :color="percentColor" :show-text="false" />
              <div class="text-sm text-gray-500 dark:text-gray-400">{{ t(M + 'poolScopeNote') }}</div>
            </section>
          </div>

          <!-- 会话明细 + 等待类别分布 -->
          <section v-if="rtSessions" class="flex flex-col gap-3">
            <div class="text-sm font-medium">{{ t(M + 'sessionsTitle') }}</div>
            <div class="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1fr)_15rem]">
              <ElTable
                :data="rtSessions.items"
                size="small"
                :max-height="320"
                :empty-text="t(M + 'sessionsEmpty')"
              >
                <ElTableColumn prop="sid" :label="t(M + 'colSid')" min-width="70" />
                <ElTableColumn prop="username" :label="t(M + 'colUser')" min-width="84" show-overflow-tooltip />
                <ElTableColumn prop="program" :label="t(M + 'colProgram')" min-width="120" show-overflow-tooltip />
                <ElTableColumn prop="machine" :label="t(M + 'colMachine')" min-width="140" show-overflow-tooltip />
                <ElTableColumn prop="status" :label="t(M + 'colStatus')" min-width="96">
                  <template #default="{ row }">
                    <ElTag size="small" :type="sessionStatusTag(row.status)">{{ row.status ?? '—' }}</ElTag>
                  </template>
                </ElTableColumn>
                <ElTableColumn prop="event" :label="t(M + 'colEvent')" min-width="180" show-overflow-tooltip />
                <ElTableColumn prop="wait_class" :label="t(M + 'colWaitClass')" min-width="100">
                  <template #default="{ row }">{{ row.wait_class ?? '—' }}</template>
                </ElTableColumn>
                <ElTableColumn prop="last_call_et" :label="t(M + 'colLastCall')" min-width="84" sortable>
                  <template #default="{ row }">{{ formatWaitSeconds(row.last_call_et) }}</template>
                </ElTableColumn>
                <ElTableColumn prop="sql_id" :label="t(M + 'colSqlId')" min-width="120">
                  <template #default="{ row }">
                    <span class="font-mono text-xs">{{ row.sql_id ?? '—' }}</span>
                  </template>
                </ElTableColumn>
                <ElTableColumn prop="logon_time" :label="t(M + 'colLogon')" min-width="158">
                  <template #default="{ row }">{{ formatTime(row.logon_time) }}</template>
                </ElTableColumn>
              </ElTable>
              <div class="flex flex-col gap-2">
                <div class="text-sm font-medium">{{ t(M + 'waitClassTitle') }}</div>
                <template v-if="waitDistribution.length > 0">
                  <div
                    v-for="row in waitDistribution"
                    :key="row.cls"
                    class="flex items-center gap-2"
                    :title="`${row.count} / ${rtSessions.total}`"
                  >
                    <span class="h-2 w-2 shrink-0 rounded-full" :style="{ background: row.color }"></span>
                    <span class="min-w-0 flex-1 truncate text-xs">{{ row.cls }}</span>
                    <span class="text-xs tabular-nums text-gray-500 dark:text-gray-400">{{ row.count }}</span>
                  </div>
                </template>
                <span v-else class="text-sm text-gray-500 dark:text-gray-400">—</span>
              </div>
            </div>
          </section>
        </template>
        <div v-else class="flex min-h-24 items-center text-sm text-gray-500 dark:text-gray-400">
          {{ t(M + 'loading') }}
        </div>
      </div>
    </ElCard>

    <!-- 历史趋势:分组切换 + 时间范围,60 秒低频轮询 -->
    <ElCard shadow="never">
      <template #header>
        <div class="flex flex-wrap items-center justify-between gap-x-3 gap-y-2">
          <div class="flex flex-wrap items-center gap-x-3 gap-y-2">
            <span class="font-medium">{{ t(M + 'trendTitle') }}</span>
            <ElSegmented v-model="trendRange" :options="rangeOptions" />
            <ElSelect v-model="trendFamily" class="w-44" :aria-label="t(M + 'trendFamilyLabel')">
              <ElOptionGroup
                v-for="group in trendFamilyGroups"
                :key="group.key"
                :label="t(M + group.key)"
              >
                <ElOption
                  v-for="family in group.items"
                  :key="family.key"
                  :label="t(M + family.labelKey)"
                  :value="family.key"
                />
              </ElOptionGroup>
            </ElSelect>
          </div>
          <ElButton text bg size="small" :icon="Refresh" :loading="historyLoading" @click="refreshHistory">
            {{ t(M + 'refresh') }}
          </ElButton>
        </div>
      </template>
      <div class="flex flex-col gap-2">
        <DbPerfTrendChart
          :series="trendSeries"
          :loading="historyLoading"
          :empty-text="trendEmptyText"
          :begin-at-zero="currentTrendFamily?.beginAtZero ?? false"
          :y-max="currentTrendFamily?.yMax"
        />
        <div class="flex flex-col gap-1 yg-text-xs yg-text-tertiary">
          <span v-if="unknownTrendKeys">{{ t(M + 'trendUnknownHint', { keys: unknownTrendKeys }) }}</span>
          <span>{{ t(M + 'trendNote') }}</span>
        </div>
      </div>
    </ElCard>

    <!-- Top 慢 SQL:行内展开 sql_text,10 分钟级低频轮询 -->
    <ElCard shadow="never">
      <template #header>
        <div class="flex flex-wrap items-center justify-between gap-x-3 gap-y-2">
          <span class="font-medium">{{ t(M + 'topSqlTitle') }}</span>
          <ElButton text bg size="small" :icon="Refresh" :loading="topSqlLoading" @click="refreshTopSql">
            {{ t(M + 'refresh') }}
          </ElButton>
        </div>
      </template>
      <div class="flex flex-col gap-3">
        <ElAlert
          v-if="topSqlError"
          type="info"
          show-icon
          :closable="false"
          :title="t(M + 'degraded')"
        />
        <ElTable
          v-else-if="topSql && topSql.items.length > 0"
          :data="topSql.items"
          size="small"
          :max-height="420"
        >
          <ElTableColumn type="expand">
            <template #default="{ row }">
              <pre class="whitespace-pre-wrap break-all rounded bg-gray-50 p-2 font-mono text-xs leading-relaxed dark:bg-gray-800">{{ row.sql_text || t(M + 'sqlTextUnavailable') }}</pre>
            </template>
          </ElTableColumn>
          <ElTableColumn prop="sql_id" :label="t(M + 'colSqlId')" min-width="130">
            <template #default="{ row }">
              <span class="font-mono text-xs">{{ row.sql_id }}</span>
            </template>
          </ElTableColumn>
          <ElTableColumn prop="executions" sortable :label="t(M + 'colExecutions')" min-width="100">
            <template #default="{ row }">{{ formatCount(row.executions) }}</template>
          </ElTableColumn>
          <ElTableColumn prop="elapsed_ms" sortable :label="t(M + 'colElapsed')" min-width="110">
            <template #default="{ row }">{{ formatDurationMs(row.elapsed_ms) }}</template>
          </ElTableColumn>
          <ElTableColumn prop="avg_elapsed_ms" sortable :label="t(M + 'colAvgElapsed')" min-width="104">
            <template #default="{ row }">{{ formatDurationMs(row.avg_elapsed_ms) }}</template>
          </ElTableColumn>
          <ElTableColumn prop="disk_reads" sortable :label="t(M + 'colDiskReads')" min-width="100">
            <template #default="{ row }">{{ formatCount(row.disk_reads) }}</template>
          </ElTableColumn>
        </ElTable>
        <div v-else class="text-sm text-gray-500 dark:text-gray-400">{{ t(M + 'topSqlEmpty') }}</div>
        <div class="text-sm text-gray-500 dark:text-gray-400">{{ t(M + 'topSqlNote') }}</div>
      </div>
    </ElCard>

    <!-- 应用侧慢端点:按 P95 降序 -->
    <ElCard shadow="never">
      <template #header>
        <div class="flex flex-wrap items-center justify-between gap-x-3 gap-y-2">
          <span class="font-medium">{{ t(M + 'endpointsTitle') }}</span>
          <ElButton text bg size="small" :icon="Refresh" :loading="endpointsLoading" @click="refreshEndpoints">
            {{ t(M + 'refresh') }}
          </ElButton>
        </div>
      </template>
      <div class="flex flex-col gap-3">
        <ElAlert
          v-if="endpointsError"
          type="info"
          show-icon
          :closable="false"
          :title="t(M + 'degraded')"
        />
        <ElTable
          v-else-if="endpoints && endpointRows.length > 0"
          :data="endpointRows"
          size="small"
          :max-height="420"
        >
          <ElTableColumn prop="method" :label="t(M + 'colMethod')" min-width="88">
            <template #default="{ row }">
              <ElTag size="small" :type="methodTagType(row.method)">{{ row.method }}</ElTag>
            </template>
          </ElTableColumn>
          <ElTableColumn prop="route" :label="t(M + 'colRoute')" min-width="220" show-overflow-tooltip>
            <template #default="{ row }">
              <span class="font-mono text-xs">{{ row.route }}</span>
            </template>
          </ElTableColumn>
          <ElTableColumn prop="count" sortable :label="t(M + 'colCount')" min-width="90">
            <template #default="{ row }">{{ formatCount(row.count) }}</template>
          </ElTableColumn>
          <ElTableColumn prop="err_count" sortable :label="t(M + 'colErrCount')" min-width="90">
            <template #default="{ row }">
              <span :class="row.err_count > 0 ? 'font-medium text-red-500' : ''">
                {{ formatCount(row.err_count) }}
              </span>
            </template>
          </ElTableColumn>
          <ElTableColumn prop="avg_ms" sortable :label="t(M + 'colAvg')" min-width="90">
            <template #default="{ row }">{{ formatDurationMs(row.avg_ms) }}</template>
          </ElTableColumn>
          <ElTableColumn prop="p95_ms" sortable :label="t(M + 'colP95')" min-width="90">
            <template #default="{ row }">{{ formatDurationMs(row.p95_ms) }}</template>
          </ElTableColumn>
          <ElTableColumn prop="max_ms" sortable :label="t(M + 'colMax')" min-width="90">
            <template #default="{ row }">{{ formatDurationMs(row.max_ms) }}</template>
          </ElTableColumn>
        </ElTable>
        <div v-else class="text-sm text-gray-500 dark:text-gray-400">{{ t(M + 'endpointsEmpty') }}</div>
        <div class="text-sm text-gray-500 dark:text-gray-400">{{ t(M + 'endpointsNote') }}</div>
      </div>
    </ElCard>

    <!-- 数据保留期清理:预览只统计,执行为后台任务 + 进度轮询 -->
    <ElCard shadow="never">
      <template #header>
        <div class="flex flex-wrap items-center justify-between gap-x-3 gap-y-2">
          <span class="font-medium">{{ t(M + 'retentionTitle') }}</span>
          <div class="flex flex-wrap items-center gap-2">
            <ElButton
              text bg :icon="Refresh" :loading="previewLoading" :disabled="progressRunning"
              @click="previewRetentionNow"
            >
              {{ t(M + 'retentionPreview') }}
            </ElButton>
            <ElButton
              text bg :loading="runLoading" :disabled="!retentionPreview || progressRunning"
              @click="onRunRetention"
            >
              {{ t(M + 'retentionRun') }}
            </ElButton>
          </div>
        </div>
      </template>
      <div class="flex flex-col gap-4">
        <ElAlert
          v-if="maintenanceActive"
          type="warning"
          show-icon
          :closable="false"
          :title="t(M + 'maintenanceBannerTitle')"
          :description="maintenanceBannerText"
        />
        <ElAlert
          v-if="retentionError"
          :type="retentionError.type"
          show-icon
          :closable="false"
          :title="retentionError.text"
        />

        <!-- 后台任务进度(仅 L2 新端点在位时出现) -->
        <ElAlert
          v-if="progressSupported && progressRunning && retentionProgress"
          type="warning"
          show-icon
          :closable="false"
          :title="`${t(M + 'progressRunningTitle')} · ${phaseLabel(retentionProgress.phase)}`"
        >
          <template #default>
            <div class="flex flex-col gap-2">
              <div class="flex flex-wrap gap-x-6 gap-y-1 text-sm tabular-nums">
                <span v-if="retentionProgress.started_at">
                  {{ t(M + 'progressStartedAt', { time: formatTime(retentionProgress.started_at) }) }}
                </span>
                <span v-if="retentionProgress.progress">
                  {{ t(M + 'progressBatchesLine', {
                    done: retentionProgress.progress.batches,
                    max: retentionProgress.progress.max_batches
                  }) }}
                </span>
              </div>
              <div
                v-if="retentionProgress.progress"
                class="flex flex-wrap gap-x-6 gap-y-1 text-sm tabular-nums"
              >
                <span>
                  <span class="text-gray-500 dark:text-gray-400">{{ t(M + 'lastRunAppLogs') }}</span>
                  {{ t(M + 'progressTableLine', retentionProgress.progress.app_logs) }}
                </span>
                <span>
                  <span class="text-gray-500 dark:text-gray-400">{{ t(M + 'lastRunExportTasks') }}</span>
                  {{ t(M + 'progressTableLine', retentionProgress.progress.export_tasks) }}
                </span>
              </div>
              <ElProgress
                v-if="retentionProgress.progress && retentionProgress.progress.max_batches > 0"
                :percentage="batchPercent"
                :show-text="false"
              />
            </div>
          </template>
        </ElAlert>

        <template v-if="retentionPreview">
          <div v-for="row in retentionRows" :key="row.key" class="flex flex-col gap-1">
            <div class="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
              <span class="font-medium">{{ row.label }}</span>
              <span class="text-sm text-gray-600 dark:text-gray-300 tabular-nums">
                {{ t(M + 'retentionCutoffLine', { time: formatTime(row.cutoff), count: formatCount(row.deletable) }) }}
              </span>
            </div>
          </div>
        </template>
        <div v-else class="text-sm text-gray-500 dark:text-gray-400">
          {{ t(M + 'retentionPreviewEmpty') }}
        </div>
        <template v-if="retentionRun">
          <div class="flex flex-col gap-2">
            <div class="flex items-center gap-2">
              <div class="text-sm font-medium">{{ t(M + 'lastRunTitle') }}</div>
              <ElTag v-if="retentionProgress?.last_run?.state === 'failed'" type="danger" size="small">
                {{ t(M + 'phaseFailed') }}
              </ElTag>
            </div>
            <div class="flex flex-wrap gap-x-8 gap-y-2 text-sm tabular-nums">
              <span>
                <span class="text-gray-500 dark:text-gray-400">{{ t(M + 'lastRunAppLogs') }}</span>
                {{ t(M + 'lastRunCountLine', {
                  count: formatCount(retentionRun.app_logs.deleted),
                  remaining: formatCount(retentionRun.app_logs.remaining)
                }) }}
              </span>
              <span>
                <span class="text-gray-500 dark:text-gray-400">{{ t(M + 'lastRunExportTasks') }}</span>
                {{ t(M + 'lastRunCountLine', {
                  count: formatCount(retentionRun.export_tasks.deleted),
                  remaining: formatCount(retentionRun.export_tasks.remaining)
                }) }}
              </span>
              <span>
                <span class="text-gray-500 dark:text-gray-400">{{ t(M + 'lastRunBatches') }}</span>
                {{ retentionRun.batches }}
              </span>
            </div>
          </div>
        </template>
        <div class="text-sm text-gray-500 dark:text-gray-400">{{ t(M + 'retentionNote') }}</div>
      </div>
    </ElCard>

    <!-- 手动备份:prod → adb2 全量备份,后台任务 + 进度轮询 -->
    <ElCard shadow="never">
      <template #header>
        <div class="flex flex-wrap items-center justify-between gap-x-3 gap-y-2">
          <span class="font-medium">{{ t(M + 'backupTitle') }}</span>
          <ElButton
            text bg
            :loading="backupRunLoading || displayedBackupState === 'running'"
            :disabled="displayedBackupState === 'running'"
            @click="onRunBackup"
          >
            {{ t(M + 'backupRun') }}
          </ElButton>
        </div>
      </template>
      <div class="flex flex-col gap-4">
        <ElAlert
          v-if="backupError"
          type="error"
          show-icon
          :closable="false"
          :title="backupError"
        />
        <ElAlert
          v-if="backupProgress"
          :type="displayedBackupState === 'running' ? 'warning' : displayedBackupState === 'ok' ? 'success' : displayedBackupState === 'failed' ? 'error' : 'info'"
          show-icon
          :closable="false"
          :title="backupStateLabel(displayedBackupState)"
        >
          <template #default v-if="backupShowDetail">
            <div class="flex flex-col gap-2">
              <div class="flex flex-wrap gap-x-6 gap-y-1 text-sm tabular-nums">
                <span>{{ t(M + 'backupTables', { done: backupProgress.tables_done, total: backupTotalText }) }}</span>
                <span>{{ t(M + 'backupRows', { rows: formatCount(backupProgress.rows_loaded) }) }}</span>
              </div>
              <div class="flex flex-wrap gap-x-6 gap-y-1 text-sm tabular-nums">
                <span v-if="backupProgress.current_table">
                  {{ t(M + 'backupCurrent', { table: backupProgress.current_table }) }}
                </span>
                <span>{{ t(M + 'backupElapsed', { time: formatElapsedSeconds(backupProgress.elapsed_seconds) }) }}</span>
              </div>
              <ElProgress
                v-if="backupRunning && backupProgress.tables_total !== null && backupProgress.tables_total > 0"
                :percentage="backupPercent"
                :show-text="false"
              />
            </div>
          </template>
        </ElAlert>
        <div v-else class="text-sm text-gray-500 dark:text-gray-400">{{ t(M + 'backupIdleEmpty') }}</div>
        <div class="text-sm text-gray-500 dark:text-gray-400">{{ t(M + 'backupNote') }}</div>
      </div>
    </ElCard>
    </template>

    <!-- 首次加载 / 读取失败(数据库分区) -->
    <template v-else>
      <ElCard shadow="never">
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
  </div>
</template>

<style scoped>
.perf-live {
  display: inline-block;
  width: 6px;
  height: 6px;
  margin-right: 6px;
  border-radius: 9999px;
  background: var(--el-color-success);
  animation: perf-pulse 2s ease-in-out infinite;
}

@keyframes perf-pulse {
  0%,
  100% {
    opacity: 1;
  }
  50% {
    opacity: 0.35;
  }
}

@media (prefers-reduced-motion: reduce) {
  .perf-live {
    animation: none;
  }
}
</style>
