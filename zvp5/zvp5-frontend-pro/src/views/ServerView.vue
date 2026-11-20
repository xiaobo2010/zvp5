<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import AnimatedTabs from '@/components/AnimatedTabs.vue'
import type { AnimatedTabItem } from '@/components/AnimatedTabs.vue'
import { ApiError, currentUserId, readableError } from '@/api/http'
import {
  disableMaintenance,
  enableMaintenance,
  fetchContentSchema,
  fetchDbStatus,
  fetchMaintenanceStatus,
  fetchMyTotp,
  fetchServerHealth,
  fetchServerVersion,
  fetchSshTargets,
  fetchTableRows,
  isLobValue,
  listServerFiles,
  type DbCellValue,
  type DbLoadMetric,
  type DbSectionDegrade,
  type DbStatusData,
  type FileEntry,
  type MaintenanceStatus,
  type RowsData,
  type SchemaItem,
  type SshTargetsData,
  type TotpData
} from '@/api/admin-server'

const router = useRouter()

const tabs: AnimatedTabItem[] = [
  { value: 'overview', label: '概览', hint: '服务健康与版本' },
  { value: 'maintenance', label: '维护模式', hint: '开关与公告' },
  { value: 'database', label: '数据库', hint: '存储、负载与连接池' },
  { value: 'totp', label: '动态码', hint: '当前登录动态码' },
  { value: 'ssh', label: 'SSH', hint: '只读目标目录' },
  { value: 'files', label: '文件', hint: '只读文件浏览' },
  { value: 'content', label: '数据内容', hint: '只读表数据' }
]

const activeTab = ref('overview')
const currentTabLabel = computed(
  () => tabs.find((item) => item.value === activeTab.value)?.label ?? ''
)

function goBack() {
  router.back()
}

/* -------------------------------------------------------------------------- */
/* Formatting helpers                                                          */
/* -------------------------------------------------------------------------- */

function pad(value: number): string {
  return String(value).padStart(2, '0')
}

function formatDateTime(value: string | Date | null | undefined): string {
  if (value === null || value === undefined || value === '') return '—'
  const date = value instanceof Date ? value : new Date(value)
  if (Number.isNaN(date.getTime())) return '—'
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(
    date.getHours()
  )}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
}

function formatUnixSeconds(value: number | null | undefined): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '—'
  return formatDateTime(new Date(value * 1000))
}

function formatClock(value: Date | null): string {
  if (!value) return '—'
  return value.toLocaleTimeString('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit'
  })
}

function formatBytes(value: number | null | undefined): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '—'
  if (value < 1024) return `${Math.round(value)} B`
  const units = ['KB', 'MB', 'GB', 'TB', 'PB']
  let amount = value
  let unit = ''
  for (const next of units) {
    amount /= 1024
    unit = next
    if (amount < 1024) break
  }
  return `${amount.toFixed(amount >= 100 ? 0 : 1)} ${unit}`
}

function formatCount(value: number | null | undefined): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '—'
  return value.toLocaleString('zh-CN')
}

function formatPercent(value: number | null | undefined): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '—'
  return `${Math.round(value * 10) / 10}%`
}

function clampPercent(value: number | null | undefined): number {
  if (typeof value !== 'number' || !Number.isFinite(value)) return 0
  return Math.min(100, Math.max(0, value))
}

function meterFillClass(value: number | null | undefined): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return ''
  if (value >= 90) return 'meter__fill--danger'
  if (value >= 75) return 'meter__fill--warn'
  return ''
}

/* -------------------------------------------------------------------------- */
/* Overview                                                                    */
/* -------------------------------------------------------------------------- */

const overviewLoading = ref(false)
const overviewError = ref('')
const healthOk = ref(false)
const healthMessage = ref('')
const healthLatency = ref<number | null>(null)
const serverVersion = ref('')
const lastRefreshed = ref<Date | null>(null)
const overviewMaintenance = ref<MaintenanceStatus | null>(null)

const overviewState = computed(() => {
  if (overviewLoading.value && !lastRefreshed.value) return '检查中'
  if (healthOk.value) return '在线'
  return lastRefreshed.value ? '离线' : '未检查'
})

const overviewStateClass = computed(() => {
  if (overviewLoading.value && !lastRefreshed.value) return 'is-muted'
  return healthOk.value ? 'is-good' : lastRefreshed.value ? 'is-bad' : 'is-muted'
})

function adminErrorText(reason: unknown, fallback: string): string {
  if (
    reason instanceof ApiError &&
    (reason.code === 'USER_BANNED' || reason.message === 'USER_BANNED')
  ) {
    return '当前账号已被限制，无法执行该操作。'
  }
  return readableError(reason, fallback)
}

function healthErrorText(reason: unknown): string {
  if (reason instanceof ApiError && reason.status === 503) {
    return '健康检查失败：服务或数据库不可达（503）。'
  }
  return adminErrorText(reason, '健康检查失败。')
}

async function loadOverview() {
  if (overviewLoading.value) return
  overviewLoading.value = true
  overviewError.value = ''
  healthLatency.value = null
  const started = performance.now()
  const healthPromise = fetchServerHealth().then((value) => {
    healthLatency.value = Math.round(performance.now() - started)
    return value
  })
  try {
    const [health, version, maintenance] = await Promise.allSettled([
      healthPromise,
      fetchServerVersion(),
      fetchMaintenanceStatus()
    ])
    let versionError = ''

    if (health.status === 'fulfilled') {
      healthOk.value = true
      healthMessage.value = health.value || 'OK'
    } else {
      healthOk.value = false
      healthMessage.value = healthErrorText(health.reason)
    }

    if (version.status === 'fulfilled') {
      serverVersion.value = version.value
    } else {
      serverVersion.value = ''
      versionError = adminErrorText(version.reason, '版本信息读取失败。')
    }

    if (maintenance.status === 'fulfilled') {
      overviewMaintenance.value = maintenance.value
      if (!maintenanceSubmitting.value) maintenanceStatus.value = maintenance.value
    } else {
      overviewMaintenance.value = maintenanceStatus.value
    }

    overviewError.value = versionError
    lastRefreshed.value = new Date()
  } finally {
    overviewLoading.value = false
  }
}

/* -------------------------------------------------------------------------- */
/* Maintenance                                                                 */
/* -------------------------------------------------------------------------- */

const maintenanceStatus = ref<MaintenanceStatus | null>(null)
const maintenanceLoading = ref(false)
const maintenanceError = ref('')
const maintenanceSubmitting = ref(false)
const maintenanceActionError = ref('')
const maintenanceActionNotice = ref('')
const maintenanceForm = reactive({ reason: '', announcement: '' })
const maintenanceErrors = reactive({ reason: '', announcement: '' })

const maintenanceEnabled = computed(() => maintenanceStatus.value?.enabled === true)
const maintenanceSinceText = computed(() =>
  formatUnixSeconds(maintenanceStatus.value?.since ?? null)
)
const maintenanceAnnouncement = computed(
  () => maintenanceStatus.value?.announcement?.trim() ?? ''
)

function clearMaintenanceField(field: 'reason' | 'announcement') {
  maintenanceErrors[field] = ''
  maintenanceActionError.value = ''
}

function validateMaintenance(): boolean {
  maintenanceErrors.reason = ''
  maintenanceErrors.announcement = ''
  const reason = maintenanceForm.reason.trim()
  const announcement = maintenanceForm.announcement.trim()

  if (!maintenanceEnabled.value) {
    if (!reason) maintenanceErrors.reason = '请填写开启维护模式的原因。'
    else if (reason.length > 200) maintenanceErrors.reason = '原因不能超过 200 个字符。'
  } else if (reason.length > 200) {
    maintenanceErrors.reason = '原因不能超过 200 个字符。'
  }

  if (!maintenanceEnabled.value && announcement.length > 300) {
    maintenanceErrors.announcement = '公告不能超过 300 个字符。'
  }

  return !maintenanceErrors.reason && !maintenanceErrors.announcement
}

async function loadMaintenance() {
  if (maintenanceLoading.value) return
  maintenanceLoading.value = true
  maintenanceError.value = ''
  try {
    const data = await fetchMaintenanceStatus()
    maintenanceStatus.value = data
    overviewMaintenance.value = data
  } catch (reason) {
    maintenanceError.value = adminErrorText(reason, '维护状态读取失败。')
  } finally {
    maintenanceLoading.value = false
  }
}

async function submitMaintenance() {
  maintenanceActionError.value = ''
  maintenanceActionNotice.value = ''
  if (!maintenanceStatus.value) {
    maintenanceActionError.value = '维护状态尚未加载，请先刷新。'
    return
  }
  if (!validateMaintenance()) return

  const turningOn = !maintenanceEnabled.value
  const reasonText = maintenanceForm.reason.trim()
  const announcementText = maintenanceForm.announcement.trim()
  const detail = turningOn
    ? [`原因：${reasonText}`, announcementText ? `公告：${announcementText}` : '']
        .filter(Boolean)
        .join('\n')
    : reasonText
      ? `原因：${reasonText}`
      : '未填写关闭原因。'

  const confirmed = await askConfirm(
    turningOn ? '开启维护模式？' : '关闭维护模式？',
    turningOn
      ? `${detail}\n\n开启后普通用户会被维护页拦截，管理员仍可访问本控制台。`
      : `${detail}\n\n关闭后普通用户会立即恢复正常访问。`,
    turningOn ? '确认开启' : '确认关闭',
    turningOn ? 'danger' : 'brand'
  )
  if (!confirmed) return

  maintenanceSubmitting.value = true
  try {
    const next = turningOn
      ? await enableMaintenance(reasonText, announcementText || undefined)
      : await disableMaintenance(reasonText || undefined)
    maintenanceStatus.value = next
    overviewMaintenance.value = next
    maintenanceForm.reason = ''
    maintenanceForm.announcement = ''
    maintenanceErrors.reason = ''
    maintenanceErrors.announcement = ''
    maintenanceActionNotice.value = turningOn ? '维护模式已开启。' : '维护模式已关闭。'
  } catch (reason) {
    maintenanceActionError.value = adminErrorText(reason, '操作失败，请稍后重试。')
  } finally {
    maintenanceSubmitting.value = false
  }
}

/* -------------------------------------------------------------------------- */
/* Database monitor                                                            */
/* -------------------------------------------------------------------------- */

const dbStatus = ref<DbStatusData | null>(null)
const dbLoading = ref(false)
const dbError = ref('')

const dbTables = computed(() => dbStatus.value?.storage.tables ?? null)
const dbTablespaces = computed(() => dbStatus.value?.storage.tablespaces ?? null)
const dbLoad = computed(() => dbStatus.value?.load ?? null)
const dbPool = computed(() => dbStatus.value?.load.connection_pool ?? null)
const dbWarnings = computed(() => dbStatus.value?.warnings ?? [])
const dbObservedText = computed(() => formatDateTime(dbStatus.value?.observed_at ?? null))
const dbDegraded = computed(() => {
  const status = dbStatus.value
  if (!status) return false
  return !status.storage.tables.available || !status.storage.tablespaces.available
})

const appTablespaces = computed(() =>
  (dbStatus.value?.storage.tablespaces.items ?? []).filter((item) => item.is_application)
)

const dbTopTables = computed(() =>
  [...(dbStatus.value?.storage.tables.items ?? [])]
    .sort((left, right) => right.total_bytes - left.total_bytes)
    .slice(0, 6)
)

const dbMetrics = computed(() => dbLoad.value?.metrics ?? [])

const dbPoolUtil = computed<number | null>(() => {
  const value = dbPool.value?.utilization_percent
  return typeof value === 'number' && Number.isFinite(value) ? value : null
})

const dbCacheText = computed(() => {
  const cache = dbStatus.value?.cache
  if (!cache) return ''
  const age = Number.isFinite(cache.age_seconds) ? cache.age_seconds.toFixed(1) : '0.0'
  return cache.cached
    ? `缓存结果 · ${age} 秒前生成 · TTL ${cache.ttl_seconds} 秒`
    : `实时采样 · TTL ${cache.ttl_seconds} 秒`
})

const ASSESSMENT_LABELS: Record<string, string> = {
  insufficient_capacity_context: '容量上下文不足，需结合表空间余量判断'
}

const DEGRADE_LABELS: Record<string, string> = {
  insufficient_privileges: '数据库权限不足',
  query_timeout: '查询超时',
  no_samples: '暂无负载采样',
  pool_metrics_unavailable: '连接池指标不可用'
}

function assessmentText(value: string | undefined): string {
  if (!value) return '—'
  return ASSESSMENT_LABELS[value] ?? value
}

function sectionUnavailableText(section: DbSectionDegrade | null | undefined): string {
  if (!section) return '该部分当前不可用。'
  if (section.message) return section.message
  if (section.reason) return DEGRADE_LABELS[section.reason] ?? `不可用（${section.reason}）`
  return '该部分当前不可用。'
}

function metricValueText(metric: DbLoadMetric): string {
  if (typeof metric.value !== 'number' || !Number.isFinite(metric.value)) return '—'
  const rounded =
    Math.abs(metric.value) >= 100
      ? Math.round(metric.value).toLocaleString('zh-CN')
      : (Math.round(metric.value * 100) / 100).toLocaleString('zh-CN')
  return metric.unit ? `${rounded} ${metric.unit}` : rounded
}

async function loadDbStatus() {
  if (dbLoading.value) return
  dbLoading.value = true
  dbError.value = ''
  try {
    dbStatus.value = await fetchDbStatus()
  } catch (reason) {
    dbError.value = adminErrorText(reason, '数据库状态读取失败。')
  } finally {
    dbLoading.value = false
  }
}

/* -------------------------------------------------------------------------- */
/* TOTP                                                                        */
/* -------------------------------------------------------------------------- */

const OTP_STEP_SECONDS = 60
const totpData = ref<TotpData | null>(null)
const totpLoading = ref(false)
const totpError = ref('')
const totpShow = ref(false)
const totpNow = ref(Date.now())
let totpTimer: ReturnType<typeof setInterval> | null = null
let disposed = false

const totpRemainingMs = computed(() =>
  totpData.value ? totpData.value.exp * 1000 - totpNow.value : 0
)
const totpRemaining = computed(() =>
  Math.min(OTP_STEP_SECONDS, Math.max(0, Math.ceil(totpRemainingMs.value / 1000)))
)
const totpExpired = computed(() => !!totpData.value && totpRemainingMs.value <= 0)
const totpProgress = computed(() => {
  if (!totpData.value) return 0
  return Math.min(100, Math.max(0, (totpRemainingMs.value / (OTP_STEP_SECONDS * 1000)) * 100))
})
const totpStatusText = computed(() => {
  if (!totpData.value) return ''
  if (totpExpired.value) return '动态码已过期'
  return `还剩 ${totpRemaining.value} 秒`
})
const totpDisplay = computed(() => {
  if (!totpData.value || !totpShow.value || totpExpired.value) return '••• •••'
  const otp = totpData.value.otp
  return otp.length === 6 ? `${otp.slice(0, 3)} ${otp.slice(3)}` : otp
})

function getTotpDigits(): (string | null)[] {
  if (!totpData.value) return Array(6).fill(null)
  if (!totpShow.value || totpExpired.value) return Array(6).fill('•')
  const otp = totpData.value.otp.padEnd(6, '0')
  return Array.from(otp.slice(0, 6))
}

function countdownClass(seconds: number): string {
  if (seconds <= 10) return 'meter__fill--danger'
  if (seconds <= 24) return 'meter__fill--warn'
  return ''
}

function stopTotpTimer() {
  if (totpTimer) {
    clearInterval(totpTimer)
    totpTimer = null
  }
}

function tickTotp() {
  totpNow.value = Date.now()
  if (totpExpired.value) {
    if (totpShow.value) totpShow.value = false
    stopTotpTimer()
  }
}

function startTotpTimer() {
  stopTotpTimer()
  totpNow.value = Date.now()
  totpTimer = setInterval(tickTotp, 250)
}

async function loadTotp() {
  if (totpLoading.value) return
  const userId = currentUserId()
  if (!userId) {
    totpData.value = null
    totpError.value = '登录状态不可用，请重新登录后再查看动态码。'
    stopTotpTimer()
    return
  }

  totpLoading.value = true
  totpError.value = ''
  try {
    const data = await fetchMyTotp(userId)
    totpData.value = data
    totpShow.value = false
    totpNow.value = Date.now()
    if (!disposed && activeTab.value === 'totp') startTotpTimer()
  } catch (reason) {
    totpData.value = null
    stopTotpTimer()
    if (reason instanceof ApiError && reason.status === 403) {
      totpError.value =
        reason.code === 'USER_BANNED'
          ? '当前账号已被限制，无法查看动态码。'
          : '只能查看自己的动态码，当前请求被拒绝。'
    } else if (reason instanceof ApiError && reason.status === 404) {
      totpError.value = '用户不存在，无法读取动态码。'
    } else {
      totpError.value = adminErrorText(reason, '动态码读取失败。')
    }
  } finally {
    totpLoading.value = false
  }
}

function toggleTotpShow() {
  if (!totpData.value || totpExpired.value) return
  totpShow.value = !totpShow.value
}

/* -------------------------------------------------------------------------- */
/* SSH targets                                                                 */
/* -------------------------------------------------------------------------- */

const sshData = ref<SshTargetsData | null>(null)
const sshLoading = ref(false)
const sshError = ref('')

const sshTargets = computed(() =>
  (sshData.value?.targets ?? []).filter((item) => Boolean(item.id))
)
const sshDefault = computed(() => sshData.value?.default ?? '')

async function loadSshTargets() {
  if (sshLoading.value) return
  sshLoading.value = true
  sshError.value = ''
  try {
    sshData.value = await fetchSshTargets()
  } catch (reason) {
    sshError.value = adminErrorText(reason, 'SSH 目标目录读取失败。')
  } finally {
    sshLoading.value = false
  }
}

/* -------------------------------------------------------------------------- */
/* Files                                                                       */
/* -------------------------------------------------------------------------- */

const FILE_ROOT = '/www/zvms4'
const FILE_PAGE_SIZE = 200
const filesPath = ref(FILE_ROOT)
const filesPathInput = ref(FILE_ROOT)
const filesEntries = ref<FileEntry[]>([])
const filesTotal = ref(0)
const filesLoading = ref(false)
const filesError = ref('')
const filesLoaded = ref(false)

interface FileCrumb {
  name: string
  path: string
}

function normalizeFilesPath(raw: string): string {
  const absolute = raw.startsWith('/') ? raw : `/${raw}`
  const collapsed = absolute.replace(/\/{2,}/g, '/')
  return collapsed.length > 1 ? collapsed.replace(/\/+$/, '') : collapsed
}

function isWithinFileRoot(path: string): boolean {
  return path === FILE_ROOT || path.startsWith(`${FILE_ROOT}/`)
}

const fileCrumbs = computed<FileCrumb[]>(() => {
  const path = filesPath.value || FILE_ROOT
  const root = isWithinFileRoot(path) ? FILE_ROOT : '/'
  const crumbs: FileCrumb[] = [{ name: root === '/' ? '根目录' : root, path: root }]
  if (root === '/') return crumbs
  const rest = path.slice(root.length).split('/').filter(Boolean)
  let prefix = root
  for (const part of rest) {
    prefix += `/${part}`
    crumbs.push({ name: part, path: prefix })
  }
  return crumbs
})

const fileCanGoUp = computed(() => {
  const path = filesPath.value
  if (!isWithinFileRoot(path) || path === FILE_ROOT) return false
  const parent = path.slice(0, path.lastIndexOf('/')) || '/'
  return isWithinFileRoot(parent)
})

const filesOverflow = computed(
  () => filesLoaded.value && filesTotal.value > filesEntries.value.length
)

function entryPath(name: string): string {
  return filesPath.value === '/' ? `/${name}` : `${filesPath.value}/${name}`
}

function fileTypeLabel(entry: FileEntry): string {
  if (entry.type === 'dir') return '目录'
  if (entry.type === 'file') return '文件'
  if (entry.type === 'symlink') return '符号链接'
  return '其它'
}

function fileModeText(mode: number): string {
  if (!Number.isFinite(mode)) return ''
  return mode.toString(8).padStart(3, '0')
}

function fileSizeText(entry: FileEntry): string {
  return entry.type === 'dir' ? '—' : formatBytes(entry.size)
}

function fileTimeText(mtime: number): string {
  if (!Number.isFinite(mtime)) return '—'
  return formatDateTime(new Date(mtime * 1000))
}

async function loadFiles() {
  if (filesLoading.value) return
  filesLoading.value = true
  filesError.value = ''
  filesEntries.value = []
  filesTotal.value = 0
  filesLoaded.value = false
  try {
    const data = await listServerFiles(filesPath.value, 0, FILE_PAGE_SIZE)
    filesPath.value = data.path || filesPath.value
    filesPathInput.value = filesPath.value
    filesEntries.value = data.entries ?? []
    filesTotal.value = typeof data.total === 'number' ? data.total : filesEntries.value.length
    filesLoaded.value = true
  } catch (reason) {
    filesEntries.value = []
    filesTotal.value = 0
    filesError.value = adminErrorText(reason, '文件列表读取失败。')
  } finally {
    filesLoading.value = false
  }
}

function jumpToFilesPath(raw: string) {
  if (filesLoading.value) return
  const target = normalizeFilesPath(raw.trim() || FILE_ROOT)
  if (!isWithinFileRoot(target)) {
    filesError.value = `只能访问 ${FILE_ROOT} 下的文件。`
    filesPathInput.value = filesPath.value
    return
  }
  filesPath.value = target
  filesPathInput.value = target
  void loadFiles()
}

function goUpFiles() {
  if (!fileCanGoUp.value) return
  const parent = filesPath.value.slice(0, filesPath.value.lastIndexOf('/')) || '/'
  jumpToFilesPath(parent)
}

function openFileEntry(entry: FileEntry) {
  if (entry.type !== 'dir') return
  jumpToFilesPath(entryPath(entry.name))
}

/* -------------------------------------------------------------------------- */
/* Database content                                                            */
/* -------------------------------------------------------------------------- */

const ROW_LIMIT = 20
const schema = ref<SchemaItem[]>([])
const schemaLoaded = ref(false)
const schemaLoading = ref(false)
const schemaError = ref('')
const schemaFilter = ref('')
const selectedTable = ref('')
const rowsData = ref<RowsData | null>(null)
const rowsLoading = ref(false)
const rowsError = ref('')
const rowOffset = ref(0)

const filteredSchema = computed(() => {
  const keyword = schemaFilter.value.trim().toUpperCase()
  if (!keyword) return schema.value
  return schema.value.filter((item) => item.name.toUpperCase().includes(keyword))
})

const selectedSchemaInfo = computed(
  () => schema.value.find((item) => item.name === selectedTable.value) ?? null
)

const rowRangeText = computed(() => {
  const data = rowsData.value
  if (!data) return ''
  if (!data.rows.length) return '当前页无数据'
  return `${formatCount(data.offset + 1)}–${formatCount(data.offset + data.rows.length)}`
})

const rowTotalText = computed(() => {
  const total = rowsData.value?.total_estimate
  if (typeof total !== 'number' || !Number.isFinite(total)) return '总行数未知'
  return `约 ${formatCount(total)} 行`
})

async function loadSchema() {
  if (schemaLoading.value) return
  schemaLoading.value = true
  schemaError.value = ''
  try {
    const data = await fetchContentSchema()
    schema.value = data.tables ?? []
    schemaLoaded.value = true
    if (selectedTable.value && !schema.value.some((item) => item.name === selectedTable.value)) {
      selectedTable.value = ''
      rowsData.value = null
    }
  } catch (reason) {
    schemaError.value = adminErrorText(reason, '数据表清单读取失败。')
  } finally {
    schemaLoading.value = false
  }
}

async function loadRows() {
  if (!selectedTable.value || rowsLoading.value) return
  rowsLoading.value = true
  rowsError.value = ''
  try {
    rowsData.value = await fetchTableRows(selectedTable.value, rowOffset.value, ROW_LIMIT)
  } catch (reason) {
    rowsData.value = null
    rowsError.value = adminErrorText(reason, '表数据读取失败。')
  } finally {
    rowsLoading.value = false
  }
}

async function selectTable(name: string) {
  if (rowsLoading.value) return
  selectedTable.value = name
  rowOffset.value = 0
  rowsData.value = null
  rowsError.value = ''
  await loadRows()
}

function nextRows() {
  if (!rowsData.value?.has_more || rowsLoading.value) return
  rowOffset.value += ROW_LIMIT
  void loadRows()
}

function prevRows() {
  if (rowOffset.value <= 0 || rowsLoading.value) return
  rowOffset.value = Math.max(0, rowOffset.value - ROW_LIMIT)
  void loadRows()
}

function refreshContent() {
  void loadSchema()
  if (selectedTable.value) void loadRows()
}

function cellText(value: DbCellValue | undefined): string {
  if (value === null || value === undefined) return '—'
  if (typeof value === 'boolean') return value ? 'TRUE' : 'FALSE'
  if (typeof value === 'object') {
    if (isLobValue(value)) {
      const unit = value.$lob === 'BLOB' ? '字节' : '字符'
      if (value.$lob === 'CLOB' && value.preview) return value.preview
      return `[${value.$lob} ${formatCount(value.length)} ${unit}]`
    }
    return '—'
  }
  return String(value)
}

function schemaMeta(item: SchemaItem): string {
  if (item.type === 'VIEW') return '视图'
  return item.num_rows === null ? '表 · 行数未知' : `表 · 约 ${formatCount(item.num_rows)} 行`
}

/* -------------------------------------------------------------------------- */
/* Confirmation dialog                                                         */
/* -------------------------------------------------------------------------- */

const confirmDialog = ref<HTMLDialogElement | null>(null)
const confirmButton = ref<HTMLButtonElement | null>(null)
const confirmState = reactive({
  title: '',
  message: '',
  confirmLabel: '',
  tone: 'danger' as 'danger' | 'brand'
})
let confirmResolver: ((confirmed: boolean) => void) | null = null

function askConfirm(
  title: string,
  message: string,
  confirmLabel: string,
  tone: 'danger' | 'brand'
): Promise<boolean> {
  if (confirmResolver) {
    const previous = confirmResolver
    confirmResolver = null
    previous(false)
  }
  confirmState.title = title
  confirmState.message = message
  confirmState.confirmLabel = confirmLabel
  confirmState.tone = tone

  const dialog = confirmDialog.value
  if (!dialog) return Promise.resolve(false)
  if (!dialog.open) dialog.showModal()
  void nextTick(() => confirmButton.value?.focus())

  return new Promise<boolean>((resolve) => {
    confirmResolver = resolve
  })
}

function settleConfirm(confirmed: boolean) {
  const resolver = confirmResolver
  confirmResolver = null
  if (confirmDialog.value?.open) confirmDialog.value.close()
  resolver?.(confirmed)
}

function onConfirmDialogClose() {
  if (!confirmResolver) return
  const resolver = confirmResolver
  confirmResolver = null
  resolver(false)
}

function onConfirmBackdropClick(event: MouseEvent) {
  if (event.target === confirmDialog.value) settleConfirm(false)
}

/* -------------------------------------------------------------------------- */
/* Tab wiring                                                                  */
/* -------------------------------------------------------------------------- */

function ensureTab(tab: string) {
  if (tab === 'overview') {
    if (!lastRefreshed.value && !overviewLoading.value) void loadOverview()
    return
  }
  if (tab === 'maintenance') {
    if (!maintenanceStatus.value && !maintenanceLoading.value) void loadMaintenance()
    return
  }
  if (tab === 'database') {
    if (!dbStatus.value && !dbLoading.value) void loadDbStatus()
    return
  }
  if (tab === 'ssh') {
    if (!sshData.value && !sshLoading.value) void loadSshTargets()
    return
  }
  if (tab === 'files') {
    if (!filesLoaded.value && !filesLoading.value && !filesError.value) void loadFiles()
    return
  }
  if (tab === 'content') {
    if (!schemaLoaded.value && !schemaLoading.value && !schemaError.value) void loadSchema()
  }
}

const panelBusy = computed(() => {
  if (activeTab.value === 'overview') return overviewLoading.value
  if (activeTab.value === 'maintenance') return maintenanceLoading.value || maintenanceSubmitting.value
  if (activeTab.value === 'database') return dbLoading.value
  if (activeTab.value === 'totp') return totpLoading.value
  if (activeTab.value === 'ssh') return sshLoading.value
  if (activeTab.value === 'files') return filesLoading.value
  if (activeTab.value === 'content') return schemaLoading.value || rowsLoading.value
  return false
})

watch(
  activeTab,
  (tab) => {
    if (tab === 'totp') {
      const expired = !!totpData.value && totpData.value.exp * 1000 <= Date.now()
      if (!totpData.value) {
        if (!totpError.value && !totpLoading.value) void loadTotp()
      } else if (expired) {
        // Returning to the panel after the window closed: fetch a fresh code
        // instead of leaving the user stuck on an expired one.
        if (!totpLoading.value) void loadTotp()
      } else {
        startTotpTimer()
      }
    } else {
      stopTotpTimer()
    }
    ensureTab(tab)
  },
  { immediate: true }
)

onBeforeUnmount(() => {
  disposed = true
  stopTotpTimer()
})
</script>

<template>
  <div class="server">
    <div class="server__container">
      <header class="server__header surface-enter" style="--i: 0">
        <button type="button" class="server__back" aria-label="返回" @click="goBack">
          <svg width="20" height="20" viewBox="0 0 20 20" fill="none" aria-hidden="true">
            <path
              d="M12 4L6 10L12 16"
              stroke="currentColor"
              stroke-width="1.5"
              stroke-linecap="round"
              stroke-linejoin="round"
            />
          </svg>
        </button>
        <div class="server__heading">
          <h1 class="server__title">服务器管理</h1>
          <p class="server__subtitle">实时状态、维护模式与受控运维数据</p>
        </div>
        <div class="server__header-status" aria-live="polite">
          <span
            class="status-dot"
            :class="{
              'is-good': healthOk,
              'is-bad': !healthOk && lastRefreshed,
              'is-muted': !lastRefreshed
            }"
            aria-hidden="true"
          ></span>
          <span>{{ overviewState }}</span>
        </div>
      </header>

      <div class="server__tabs surface-enter" style="--i: 1">
        <AnimatedTabs
          v-model="activeTab"
          :items="tabs"
          aria-label="服务器管理分区"
        />
      </div>

      <div
        class="server__content"
        role="tabpanel"
        :aria-label="currentTabLabel"
        :aria-busy="panelBusy"
      >
        <Transition name="tab-fade">
          <div :key="activeTab" class="server__panel">

          <!-- 概览 -->
          <template v-if="activeTab === 'overview'">
            <section class="panel">
              <div class="panel__header">
                <div>
                  <h2 class="panel__title">服务概览</h2>
                  <p class="panel__subtitle">来自 /health 与 /version 的实时结果</p>
                </div>
                <div class="panel__actions">
                  <span class="badge" :class="healthOk ? 'badge--good' : lastRefreshed ? 'badge--bad' : 'badge--info'">
                    {{ overviewState }}
                  </span>
                  <button
                    type="button"
                    class="ghost-button"
                    :disabled="overviewLoading"
                    @click="loadOverview"
                  >
                    {{ overviewLoading ? '刷新中…' : '刷新' }}
                  </button>
                </div>
              </div>

              <div v-if="overviewLoading && !lastRefreshed" class="state-block" role="status">
                <span class="spinner" aria-hidden="true"></span>
                正在读取服务状态…
              </div>

              <template v-else>
                <p v-if="overviewError" class="inline-note inline-note--error" role="alert">
                  {{ overviewError }}
                </p>

                <div class="summary-grid">
                  <div class="summary-card surface-enter" style="--i: 0">
                    <span class="summary-card__label">服务状态</span>
                    <span class="summary-card__value" :class="overviewStateClass">{{ overviewState }}</span>
                    <span class="summary-card__hint">{{ healthMessage || '等待健康检查结果' }}</span>
                  </div>
                  <div class="summary-card surface-enter" style="--i: 1">
                    <span class="summary-card__label">API 版本</span>
                    <span class="summary-card__value summary-card__value--mono">
                      {{ serverVersion || '—' }}
                    </span>
                    <span class="summary-card__hint">读取 CHANGELOG 最新记录</span>
                  </div>
                  <div class="summary-card surface-enter" style="--i: 2">
                    <span class="summary-card__label">维护模式</span>
                    <span
                      class="summary-card__value"
                      :class="overviewMaintenance ? (overviewMaintenance.enabled ? 'is-warn' : 'is-good') : 'is-muted'"
                    >
                      {{ overviewMaintenance ? (overviewMaintenance.enabled ? '已启用' : '未启用') : '未获取' }}
                    </span>
                    <span class="summary-card__hint">
                      {{ overviewMaintenance ? (overviewMaintenance.enabled ? formatUnixSeconds(overviewMaintenance.since) : '系统正常运行') : '维护状态未能读取' }}
                    </span>
                  </div>
                  <div class="summary-card surface-enter" style="--i: 3">
                    <span class="summary-card__label">最后刷新</span>
                    <span class="summary-card__value summary-card__value--mono">
                      {{ formatClock(lastRefreshed) }}
                    </span>
                    <span class="summary-card__hint">
                      {{ healthLatency !== null ? `健康检查 ${healthLatency} ms` : '尚未完成健康检查' }}
                    </span>
                  </div>
                </div>

                <div class="detail-list">
                  <div class="detail-row">
                    <span class="detail-row__label">健康检查响应</span>
                    <span class="detail-row__value">{{ healthMessage || '—' }}</span>
                  </div>
                  <div class="detail-row">
                    <span class="detail-row__label">检查耗时</span>
                    <span class="detail-row__value">{{ healthLatency !== null ? `${healthLatency} ms` : '—' }}</span>
                  </div>
                  <div class="detail-row">
                    <span class="detail-row__label">版本来源</span>
                    <span class="detail-row__value">CHANGELOG 表</span>
                  </div>
                </div>

                <p
                  v-if="overviewMaintenance?.enabled"
                  class="inline-note inline-note--warn"
                >
                  系统当前处于维护模式{{ overviewMaintenance?.announcement ? `：${overviewMaintenance.announcement}` : '。' }}
                </p>
              </template>
            </section>
          </template>

          <!-- 维护模式 -->
          <template v-else-if="activeTab === 'maintenance'">
            <section class="panel">
              <div class="panel__header">
                <div>
                  <h2 class="panel__title">维护模式</h2>
                  <p class="panel__subtitle">开启后普通用户会被维护页拦截，管理员仍可访问控制台</p>
                </div>
                <div class="panel__actions">
                  <button
                    type="button"
                    class="ghost-button"
                    :disabled="maintenanceLoading || maintenanceSubmitting"
                    @click="loadMaintenance"
                  >
                    {{ maintenanceLoading ? '刷新中…' : '刷新' }}
                  </button>
                </div>
              </div>

              <div v-if="maintenanceLoading && !maintenanceStatus" class="state-block" role="status">
                <span class="spinner" aria-hidden="true"></span>
                正在读取维护状态…
              </div>

              <div
                v-else-if="!maintenanceStatus"
                class="state-block state-block--error"
                role="alert"
              >
                <span>{{ maintenanceError || '维护状态不可用。' }}</span>
                <button type="button" class="ghost-button" @click="loadMaintenance">重试</button>
              </div>

              <template v-else>
                <p v-if="maintenanceError" class="inline-note inline-note--error" role="alert">
                  {{ maintenanceError }}
                </p>

                <div class="status-line surface-enter" style="--i: 0">
                  <span class="badge" :class="maintenanceEnabled ? 'badge--warn' : 'badge--good'">
                    {{ maintenanceEnabled ? '维护中' : '正常运行' }}
                  </span>
                  <span class="status-line__text">
                    {{ maintenanceEnabled ? '普通用户访问已被拦截' : '所有用户均可正常访问' }}
                  </span>
                </div>

                <p
                  v-if="maintenanceEnabled && maintenanceAnnouncement"
                  class="inline-note inline-note--warn announcement-note"
                >
                  {{ maintenanceAnnouncement }}
                </p>

                <div class="detail-list">
                  <div class="detail-row">
                    <span class="detail-row__label">当前状态</span>
                    <span class="detail-row__value">{{ maintenanceEnabled ? '已启用' : '未启用' }}</span>
                  </div>
                  <div class="detail-row">
                    <span class="detail-row__label">开始时间</span>
                    <span class="detail-row__value">{{ maintenanceSinceText }}</span>
                  </div>
                  <div class="detail-row">
                    <span class="detail-row__label">公告</span>
                    <span class="detail-row__value detail-row__value--pre">
                      {{ maintenanceAnnouncement || '未设置' }}
                    </span>
                  </div>
                </div>

                <p v-if="maintenanceActionNotice" class="inline-note inline-note--success" role="status">
                  {{ maintenanceActionNotice }}
                </p>
                <p v-if="maintenanceActionError" class="inline-note inline-note--error" role="alert">
                  {{ maintenanceActionError }}
                </p>

                <form class="form surface-enter" style="--i: 1" @submit.prevent="submitMaintenance">
                  <div class="field">
                    <label class="field__label" for="maintenance-reason">原因</label>
                    <textarea
                      id="maintenance-reason"
                      v-model="maintenanceForm.reason"
                      class="textarea"
                      maxlength="200"
                      :placeholder="maintenanceEnabled ? '可选：记录关闭原因' : '必填：说明本次维护的原因'"
                      :aria-invalid="!!maintenanceErrors.reason"
                      :aria-required="!maintenanceEnabled"
                      aria-describedby="maintenance-reason-hint"
                      @input="clearMaintenanceField('reason')"
                    ></textarea>
                    <p id="maintenance-reason-hint" class="field__hint">
                      {{ maintenanceForm.reason.trim().length }}/200
                    </p>
                    <p v-if="maintenanceErrors.reason" class="field__error" role="alert">
                      {{ maintenanceErrors.reason }}
                    </p>
                  </div>

                  <div v-if="!maintenanceEnabled" class="field">
                    <label class="field__label" for="maintenance-announcement">公告（可选）</label>
                    <textarea
                      id="maintenance-announcement"
                      v-model="maintenanceForm.announcement"
                      class="textarea"
                      maxlength="300"
                      placeholder="显示给用户的维护说明，最多 300 字"
                      :aria-invalid="!!maintenanceErrors.announcement"
                      aria-describedby="maintenance-announcement-hint"
                      @input="clearMaintenanceField('announcement')"
                    ></textarea>
                    <p id="maintenance-announcement-hint" class="field__hint">
                      {{ maintenanceForm.announcement.trim().length }}/300
                    </p>
                    <p v-if="maintenanceErrors.announcement" class="field__error" role="alert">
                      {{ maintenanceErrors.announcement }}
                    </p>
                  </div>

                  <div class="form__actions">
                    <button
                      type="submit"
                      class="primary-button"
                      :class="{ 'danger-button': !maintenanceEnabled }"
                      :disabled="maintenanceSubmitting || !maintenanceStatus"
                    >
                      <span v-if="maintenanceSubmitting" class="spinner" aria-hidden="true"></span>
                      {{ maintenanceSubmitting ? '处理中…' : maintenanceEnabled ? '关闭维护模式' : '开启维护模式' }}
                    </button>
                    <button
                      type="button"
                      class="ghost-button"
                      :disabled="maintenanceSubmitting"
                      @click="loadMaintenance"
                    >
                      重新读取状态
                    </button>
                  </div>
                </form>
              </template>
            </section>
          </template>

          <!-- 数据库 -->
          <template v-else-if="activeTab === 'database'">
            <section class="panel">
              <div class="panel__header">
                <div>
                  <h2 class="panel__title">数据库</h2>
                  <p class="panel__subtitle">
                    只读监控 · {{ dbObservedText }}
                    <span v-if="dbCacheText"> · {{ dbCacheText }}</span>
                  </p>
                </div>
                <div class="panel__actions">
                  <span v-if="dbStatus" class="badge" :class="dbDegraded ? 'badge--warn' : 'badge--good'">
                    {{ dbDegraded ? '部分降级' : '正常' }}
                  </span>
                  <button
                    type="button"
                    class="ghost-button"
                    :disabled="dbLoading"
                    @click="loadDbStatus"
                  >
                    {{ dbLoading ? '刷新中…' : '刷新' }}
                  </button>
                </div>
              </div>

              <div v-if="dbLoading && !dbStatus" class="state-block" role="status">
                <span class="spinner" aria-hidden="true"></span>
                正在读取数据库状态…
              </div>

              <div v-else-if="!dbStatus" class="state-block state-block--error" role="alert">
                <span>{{ dbError || '数据库状态不可用。' }}</span>
                <button type="button" class="ghost-button" @click="loadDbStatus">重试</button>
              </div>

              <template v-else>
                <p v-if="dbError" class="inline-note inline-note--error" role="alert">
                  {{ dbError }}
                </p>

                <div class="summary-grid">
                  <div class="summary-card surface-enter" style="--i: 0">
                    <span class="summary-card__label">观测时间</span>
                    <span class="summary-card__value summary-card__value--mono">{{ dbObservedText }}</span>
                    <span class="summary-card__hint">后端只读采样</span>
                  </div>
                  <div class="summary-card surface-enter" style="--i: 1">
                    <span class="summary-card__label">数据表数量</span>
                    <span class="summary-card__value">{{ formatCount(dbTables?.count ?? null) }}</span>
                    <span class="summary-card__hint">不含 Oracle 运维表</span>
                  </div>
                  <div class="summary-card surface-enter" style="--i: 2">
                    <span class="summary-card__label">数据总量</span>
                    <span class="summary-card__value summary-card__value--mono">{{ formatBytes(dbTables?.total_bytes ?? null) }}</span>
                    <span class="summary-card__hint">表 + 索引 + LOB</span>
                  </div>
                  <div class="summary-card surface-enter" style="--i: 3">
                    <span class="summary-card__label">连接池利用率</span>
                    <span class="summary-card__value">{{ dbPoolUtil !== null ? formatPercent(dbPoolUtil) : '—' }}</span>
                    <span class="summary-card__hint">仅代表当前 API 进程</span>
                  </div>
                </div>

                <div class="section-stack">
                  <section class="sub-panel surface-enter" style="--i: 1">
                    <div class="sub-panel__head">
                      <h3 class="sub-panel__title">应用表空间</h3>
                      <span class="sub-panel__meta">仅业务表所在表空间</span>
                    </div>
                    <p v-if="dbTablespaces && !dbTablespaces.available" class="inline-note inline-note--warn">
                      {{ sectionUnavailableText(dbTablespaces) }}
                    </p>
                    <div v-else-if="appTablespaces.length" class="meter-list">
                      <div v-for="space in appTablespaces" :key="space.name" class="meter-row">
                        <div class="meter-row__head">
                          <span class="meter-row__name">{{ space.name }}</span>
                          <span class="meter-row__value">
                            {{ formatBytes(space.used_bytes) }} / {{ formatBytes(space.max_bytes) }}
                            · {{ formatPercent(space.used_percent) }}
                          </span>
                        </div>
                        <div
                          v-if="space.used_percent !== null"
                          class="meter"
                          role="progressbar"
                          :aria-label="`${space.name} 已用空间`"
                          :aria-valuenow="Math.round(clampPercent(space.used_percent))"
                          aria-valuemin="0"
                          aria-valuemax="100"
                        >
                          <span
                            class="meter__fill"
                            :class="meterFillClass(space.used_percent)"
                            :style="{ width: `${clampPercent(space.used_percent)}%` }"
                          ></span>
                        </div>
                      </div>
                    </div>
                    <p v-else class="empty-inline">暂无业务表空间信息。</p>
                  </section>

                  <section class="sub-panel surface-enter" style="--i: 2">
                    <div class="sub-panel__head">
                      <h3 class="sub-panel__title">负载与连接池</h3>
                      <span class="sub-panel__meta">{{ dbLoad?.source || '—' }}</span>
                    </div>
                    <p v-if="dbLoad && !dbLoad.available" class="inline-note inline-note--warn">
                      {{ sectionUnavailableText(dbLoad) }}
                    </p>
                    <template v-else>
                      <div v-if="dbMetrics.length" class="metric-grid">
                        <div v-for="metric in dbMetrics" :key="metric.name" class="metric">
                          <span class="metric__label">{{ metric.name }}</span>
                          <span class="metric__value">{{ metricValueText(metric) }}</span>
                        </div>
                      </div>
                      <p v-else class="empty-inline">暂无负载采样。</p>
                    </template>

                    <div class="assessment-row">
                      <span class="assessment-row__label">负载评估</span>
                      <span class="assessment-row__value">{{ assessmentText(dbLoad?.assessment) }}</span>
                    </div>

                    <div v-if="dbPool" class="pool-block">
                      <h4 class="pool-block__title">连接池</h4>
                      <p v-if="!dbPool.available" class="inline-note inline-note--warn">
                        {{ sectionUnavailableText(dbPool) }}
                      </p>
                      <template v-else>
                        <div class="metric-grid metric-grid--compact">
                          <div class="metric">
                            <span class="metric__label">池大小</span>
                            <span class="metric__value">{{ dbPool.size ?? '—' }}</span>
                          </div>
                          <div class="metric">
                            <span class="metric__label">已借出</span>
                            <span class="metric__value">{{ dbPool.checked_out ?? '—' }}</span>
                          </div>
                          <div class="metric">
                            <span class="metric__label">溢出</span>
                            <span class="metric__value">{{ dbPool.overflow ?? '—' }}</span>
                          </div>
                          <div class="metric">
                            <span class="metric__label">容量</span>
                            <span class="metric__value">{{ dbPool.capacity ?? '—' }}</span>
                          </div>
                          <div class="metric">
                            <span class="metric__label">利用率</span>
                            <span class="metric__value">{{ dbPoolUtil !== null ? formatPercent(dbPoolUtil) : '—' }}</span>
                          </div>
                        </div>
                        <div
                          v-if="dbPoolUtil !== null"
                          class="meter"
                          role="progressbar"
                          aria-label="连接池利用率"
                          :aria-valuenow="Math.round(clampPercent(dbPoolUtil))"
                          aria-valuemin="0"
                          aria-valuemax="100"
                        >
                          <span
                            class="meter__fill"
                            :class="meterFillClass(dbPoolUtil)"
                            :style="{ width: `${clampPercent(dbPoolUtil)}%` }"
                          ></span>
                        </div>
                        <p class="field__hint">仅代表当前 API 进程，不代表数据库全局。</p>
                      </template>
                    </div>
                  </section>

                  <section class="sub-panel surface-enter" style="--i: 3">
                    <div class="sub-panel__head">
                      <h3 class="sub-panel__title">最大数据表</h3>
                      <span class="sub-panel__meta">按总占用降序 · 仅显示前 6 项</span>
                    </div>
                    <p v-if="dbTables && !dbTables.available" class="inline-note inline-note--warn">
                      {{ sectionUnavailableText(dbTables) }}
                    </p>
                    <div v-else-if="dbTopTables.length" class="compact-list">
                      <div v-for="table in dbTopTables" :key="table.name" class="compact-row">
                        <span class="compact-row__name">{{ table.name }}</span>
                        <span class="compact-row__meta">
                          {{ table.estimated_rows === null ? '行数未知' : `约 ${formatCount(table.estimated_rows)} 行` }}
                        </span>
                        <span class="compact-row__value">{{ formatBytes(table.total_bytes) }}</span>
                      </div>
                    </div>
                    <p v-else class="empty-inline">暂无表占用数据。</p>
                  </section>
                </div>

                <div v-if="dbWarnings.length" class="note-block">
                  <h3 class="note-block__title">提示与口径</h3>
                  <ul class="note-list">
                    <li v-for="(warning, index) in dbWarnings" :key="index">{{ warning }}</li>
                  </ul>
                </div>
              </template>
            </section>
          </template>

          <!-- 动态码 -->
          <template v-else-if="activeTab === 'totp'">
            <section class="panel">
              <div class="panel__header">
                <div>
                  <h2 class="panel__title">登录动态码</h2>
                  <p class="panel__subtitle">仅显示本人的当前 TOTP；每次查看都会写入审计日志</p>
                </div>
                <div class="panel__actions">
                  <button
                    type="button"
                    class="ghost-button"
                    :disabled="totpLoading"
                    @click="loadTotp"
                  >
                    {{ totpLoading ? '刷新中…' : '刷新' }}
                  </button>
                </div>
              </div>

              <div v-if="totpLoading && !totpData" class="state-block" role="status">
                <span class="spinner" aria-hidden="true"></span>
                正在读取动态码…
              </div>

              <div v-else-if="!totpData" class="state-block state-block--error" role="alert">
                <span>{{ totpError || '动态码不可用。' }}</span>
                <button type="button" class="ghost-button" @click="loadTotp">重试</button>
              </div>

              <template v-else>
                <p v-if="totpError" class="inline-note inline-note--error" role="alert">
                  {{ totpError }}
                </p>

                <div class="totp-stage surface-enter" style="--i: 0">
                  <div class="totp-display" :class="{ 'totp-expired': totpExpired }">
                    <div
                      v-for="(digit, idx) in getTotpDigits()"
                      :key="idx"
                      class="totp-digit"
                      :style="{ '--delay': `${idx * 30}ms` }"
                    >
                      {{ digit }}
                    </div>
                    <div
                      v-if="totpData"
                      class="totp-countdown"
                      role="progressbar"
                      aria-label="动态码剩余时间"
                      :aria-valuenow="Math.round(totpProgress)"
                      aria-valuemin="0"
                      aria-valuemax="100"
                    >
                      <svg viewBox="0 0 100 100" class="totp-countdown__svg">
                        <circle
                          cx="50"
                          cy="50"
                          r="45"
                          fill="none"
                          stroke="currentColor"
                          stroke-width="3"
                          opacity="0.1"
                        />
                        <circle
                          cx="50"
                          cy="50"
                          r="45"
                          fill="none"
                          stroke="currentColor"
                          stroke-width="3"
                          stroke-dasharray="282.74"
                          :stroke-dashoffset="`${282.74 * (1 - totpProgress / 100)}`"
                          class="totp-countdown__ring"
                          :class="{
                            'totp-countdown__ring--critical': totpRemaining <= 10,
                            'totp-countdown__ring--warning': totpRemaining > 10 && totpRemaining <= 24
                          }"
                          stroke-linecap="round"
                        />
                      </svg>
                      <div class="totp-countdown__text">
                        <span class="totp-countdown__value">{{ totpRemaining }}</span>
                        <span class="totp-countdown__unit">s</span>
                      </div>
                    </div>
                  </div>

                  <div class="totp-actions">
                    <button
                      type="button"
                      class="ghost-button"
                      :disabled="totpExpired || !totpData"
                      @click="toggleTotpShow"
                    >
                      {{ totpShow ? '隐藏' : '显示' }}
                    </button>
                    <button
                      type="button"
                      class="primary-button"
                      :disabled="totpLoading"
                      @click="loadTotp"
                    >
                      {{ totpLoading ? '刷新中…' : '刷新' }}
                    </button>
                  </div>
                </div>

                <div class="totp-info">
                  <p class="totp-info__item">仅本人可见</p>
                  <p class="totp-info__item">用于高危操作确认</p>
                  <p class="totp-info__item">查看会写入审计日志</p>
                </div>

                <p v-if="totpExpired" class="inline-note inline-note--warn" role="status">
                  动态码已过期，请刷新后再查看。
                </p>
              </template>
            </section>
          </template>

          <!-- SSH -->
          <template v-else-if="activeTab === 'ssh'">
            <section class="panel">
              <div class="panel__header">
                <div>
                  <h2 class="panel__title">SSH 目标</h2>
                  <p class="panel__subtitle">服务端下发的只读目标目录</p>
                </div>
                <div class="panel__actions">
                  <button
                    type="button"
                    class="ghost-button"
                    :disabled="sshLoading"
                    @click="loadSshTargets"
                  >
                    {{ sshLoading ? '刷新中…' : '刷新' }}
                  </button>
                </div>
              </div>

              <div v-if="sshLoading && !sshData" class="state-block" role="status">
                <span class="spinner" aria-hidden="true"></span>
                正在读取 SSH 目标…
              </div>

              <div v-else-if="!sshData" class="state-block state-block--error" role="alert">
                <span>{{ sshError || 'SSH 目标目录不可用。' }}</span>
                <button type="button" class="ghost-button" @click="loadSshTargets">重试</button>
              </div>

              <template v-else>
                <p v-if="sshError" class="inline-note inline-note--error" role="alert">
                  {{ sshError }}
                </p>

                <div class="summary-grid">
                  <div class="summary-card surface-enter" style="--i: 0">
                    <span class="summary-card__label">目标数量</span>
                    <span class="summary-card__value">{{ sshTargets.length }}</span>
                    <span class="summary-card__hint">由服务端配置决定</span>
                  </div>
                  <div class="summary-card surface-enter" style="--i: 1">
                    <span class="summary-card__label">默认目标</span>
                    <span class="summary-card__value summary-card__value--mono">
                      {{ sshDefault || '—' }}
                    </span>
                    <span class="summary-card__hint">未选择目标时使用</span>
                  </div>
                </div>

                <div v-if="sshTargets.length" class="target-list">
                  <div
                    v-for="target in sshTargets"
                    :key="target.id"
                    class="target-row surface-enter"
                    style="--i: 2"
                  >
                    <div class="target-row__main">
                      <span class="target-row__label">{{ target.label }}</span>
                      <span class="target-row__display">{{ target.display || target.label || '—' }}</span>
                    </div>
                    <span v-if="target.id === sshDefault" class="badge badge--info">默认</span>
                  </div>
                </div>
                <div v-else class="state-block">当前没有配置 SSH 目标。</div>

                <p class="inline-note inline-note--info">
                  本页面仅展示目标目录；不提供终端连接、票据创建或命令执行入口。
                </p>
              </template>
            </section>
          </template>

          <!-- 文件 -->
          <template v-else-if="activeTab === 'files'">
            <section class="panel">
              <div class="panel__header">
                <div>
                  <h2 class="panel__title">文件浏览</h2>
                  <p class="panel__subtitle">只读目录列表 · 仅允许访问 {{ FILE_ROOT }}</p>
                </div>
                <div class="panel__actions">
                  <button
                    type="button"
                    class="ghost-button"
                    :disabled="filesLoading"
                    @click="loadFiles"
                  >
                    {{ filesLoading ? '刷新中…' : '刷新' }}
                  </button>
                </div>
              </div>

              <form class="path-bar" @submit.prevent="jumpToFilesPath(filesPathInput)">
                <button
                  type="button"
                  class="ghost-button ghost-button--icon"
                  :disabled="!fileCanGoUp || filesLoading"
                  aria-label="上一级目录"
                  @click="goUpFiles"
                >
                  ↑
                </button>
                <input
                  v-model="filesPathInput"
                  class="input path-input"
                  type="text"
                  aria-label="文件路径"
                  placeholder="输入绝对路径，例如 /www/zvms4"
                  spellcheck="false"
                  autocomplete="off"
                />
                <button type="submit" class="primary-button" :disabled="filesLoading">前往</button>
              </form>

              <nav class="crumbs" aria-label="文件路径">
                <template v-for="(crumb, index) in fileCrumbs" :key="crumb.path">
                  <span v-if="index > 0" class="crumbs__sep" aria-hidden="true">/</span>
                  <button
                    type="button"
                    class="crumb"
                    :class="{ 'crumb--current': index === fileCrumbs.length - 1 }"
                    :disabled="filesLoading || index === fileCrumbs.length - 1"
                    @click="jumpToFilesPath(crumb.path)"
                  >
                    {{ crumb.name }}
                  </button>
                </template>
              </nav>

              <div v-if="filesLoading && !filesLoaded" class="state-block" role="status">
                <span class="spinner" aria-hidden="true"></span>
                正在读取目录…
              </div>

              <div v-else-if="filesError && !filesLoaded" class="state-block state-block--error" role="alert">
                <span>{{ filesError }}</span>
                <button type="button" class="ghost-button" @click="loadFiles">重试</button>
              </div>

              <template v-else>
                <p v-if="filesError" class="inline-note inline-note--error" role="alert">
                  {{ filesError }}
                </p>

                <ul v-if="filesEntries.length" class="file-list">
                  <li v-for="entry in filesEntries" :key="entry.name" class="file-row">
                    <component
                      :is="entry.type === 'dir' ? 'button' : 'div'"
                      :type="entry.type === 'dir' ? 'button' : undefined"
                      class="file-row__main"
                      :class="{ 'is-clickable': entry.type === 'dir' }"
                      @click="openFileEntry(entry)"
                    >
                      <span class="file-icon" :class="`file-icon--${entry.type}`" aria-hidden="true">
                        <svg v-if="entry.type === 'dir'" width="18" height="18" viewBox="0 0 18 18" fill="none">
                          <path d="M2.5 5.5C2.5 4.948 2.948 4.5 3.5 4.5H7L8.5 6H14.5C15.052 6 15.5 6.448 15.5 7V13C15.5 13.552 15.052 14 14.5 14H3.5C2.948 14 2.5 13.552 2.5 13V5.5Z" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round" />
                        </svg>
                        <svg v-else width="18" height="18" viewBox="0 0 18 18" fill="none">
                          <path d="M5 2.5H10.5L14 6V15.5H5V2.5Z" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round" />
                          <path d="M10.5 2.5V6H14" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round" />
                        </svg>
                      </span>
                      <span class="file-row__name-wrap">
                        <span class="file-row__name">{{ entry.name }}</span>
                        <span class="file-row__sub">
                          {{ fileTypeLabel(entry) }}
                          <template v-if="fileModeText(entry.mode)"> · {{ fileModeText(entry.mode) }}</template>
                          <template v-if="entry.type === 'symlink' && entry.target"> → {{ entry.target }}</template>
                        </span>
                      </span>
                      <span class="file-row__meta file-row__meta--size">{{ fileSizeText(entry) }}</span>
                      <span class="file-row__meta file-row__meta--time">{{ fileTimeText(entry.mtime) }}</span>
                    </component>
                  </li>
                </ul>

                <div v-else-if="!filesLoading" class="state-block">
                  当前目录为空，或没有可显示的条目。
                </div>

                <p v-if="filesOverflow" class="field__hint">
                  共 {{ formatCount(filesTotal) }} 项，当前仅显示前 {{ FILE_PAGE_SIZE }} 项。
                </p>
                <p v-else-if="filesLoaded" class="field__hint">
                  共 {{ formatCount(filesTotal) }} 项。
                </p>
              </template>
            </section>
          </template>

          <!-- 数据内容 -->
          <template v-else-if="activeTab === 'content'">
            <section class="panel">
              <div class="panel__header">
                <div>
                  <h2 class="panel__title">数据内容</h2>
                  <p class="panel__subtitle">只读表结构浏览 · 前 {{ ROW_LIMIT }} 行 · 不支持 SQL 执行或写入</p>
                </div>
                <div class="panel__actions">
                  <button
                    type="button"
                    class="ghost-button"
                    :disabled="schemaLoading || rowsLoading"
                    @click="refreshContent"
                  >
                    {{ schemaLoading || rowsLoading ? '刷新中…' : '刷新' }}
                  </button>
                </div>
              </div>

              <div class="content-layout">
                <aside class="schema-panel" aria-label="数据表清单">
                  <div class="field">
                    <label class="field__label" for="schema-filter">筛选表或视图</label>
                    <input
                      id="schema-filter"
                      v-model="schemaFilter"
                      class="input"
                      type="search"
                      placeholder="输入表名…"
                      autocomplete="off"
                    />
                  </div>

                  <div v-if="schemaLoading && !schemaLoaded" class="state-block state-block--compact" role="status">
                    <span class="spinner" aria-hidden="true"></span>
                    正在读取表清单…
                  </div>

                  <div
                    v-else-if="schemaError && !schema.length"
                    class="state-block state-block--error state-block--compact"
                    role="alert"
                  >
                    <span>{{ schemaError }}</span>
                    <button type="button" class="ghost-button" @click="loadSchema">重试</button>
                  </div>

                  <template v-else>
                    <p v-if="schemaError" class="inline-note inline-note--error" role="alert">
                      {{ schemaError }}
                    </p>
                    <div v-if="filteredSchema.length" class="schema-list" role="group" aria-label="表或视图">
                      <button
                        v-for="item in filteredSchema"
                        :key="item.name"
                        type="button"
                        class="schema-item"
                        :class="{ 'is-active': item.name === selectedTable }"
                        :aria-pressed="item.name === selectedTable"
                        :disabled="rowsLoading"
                        @click="selectTable(item.name)"
                      >
                        <span class="schema-item__name">{{ item.name }}</span>
                        <span class="schema-item__type">{{ item.type === 'VIEW' ? '视图' : '表' }}</span>
                      </button>
                    </div>
                    <div v-else class="state-block state-block--compact">
                      {{ schemaFilter.trim() ? '没有匹配的表或视图。' : '当前 schema 没有可用的表或视图。' }}
                    </div>
                  </template>
                </aside>

                <div class="content-detail">
                  <div v-if="!selectedTable" class="state-block">
                    从左侧选择一张表或视图，查看只读列与行数据。
                  </div>

                  <template v-else>
                    <div class="content-detail__head">
                      <div>
                        <h3 class="content-detail__title">{{ selectedTable }}</h3>
                        <p class="panel__subtitle">
                          {{ selectedSchemaInfo ? schemaMeta(selectedSchemaInfo) : '表信息读取中' }}
                          <template v-if="rowsData"> · {{ rowsData.columns.length }} 列</template>
                        </p>
                      </div>
                      <span class="badge badge--info">
                        {{ selectedSchemaInfo?.type === 'VIEW' ? '视图' : '表' }}
                      </span>
                    </div>

                    <div v-if="rowsLoading && !rowsData" class="state-block" role="status">
                      <span class="spinner" aria-hidden="true"></span>
                      正在读取表数据…
                    </div>

                    <div v-else-if="rowsError" class="state-block state-block--error" role="alert">
                      <span>{{ rowsError }}</span>
                      <button type="button" class="ghost-button" @click="loadRows">重试</button>
                    </div>

                    <template v-else-if="rowsData">
                      <div class="data-table-wrap" :class="{ 'is-loading': rowsLoading }">
                        <table v-if="rowsData.columns.length" class="data-table">
                          <thead>
                            <tr>
                              <th v-for="column in rowsData.columns" :key="column" scope="col">
                                {{ column }}
                              </th>
                            </tr>
                          </thead>
                          <tbody>
                            <tr v-if="!rowsData.rows.length">
                              <td :colspan="rowsData.columns.length" class="data-table__empty">
                                当前页没有数据
                              </td>
                            </tr>
                            <tr v-for="(row, rowIndex) in rowsData.rows" :key="rowIndex">
                              <td v-for="column in rowsData.columns" :key="column">
                                <span class="cell-text" :title="cellText(row[column])">
                                  {{ cellText(row[column]) }}
                                </span>
                              </td>
                            </tr>
                          </tbody>
                        </table>
                        <div v-else class="state-block state-block--compact">
                          这张表没有可读列。
                        </div>
                      </div>

                      <div class="pager" :key="rowOffset">
                        <span class="pager__info">
                          {{ rowRangeText }} · {{ rowTotalText }}
                          <template v-if="rowsLoading"> · 加载中…</template>
                        </span>
                        <div class="pager__actions">
                          <button
                            type="button"
                            class="ghost-button"
                            :disabled="rowOffset === 0 || rowsLoading"
                            @click="prevRows"
                          >
                            上一页
                          </button>
                          <button
                            type="button"
                            class="ghost-button"
                            :disabled="!rowsData.has_more || rowsLoading"
                            @click="nextRows"
                          >
                            下一页
                          </button>
                        </div>
                      </div>
                    </template>

                    <div v-else class="state-block">
                      选择数据表后显示前 {{ ROW_LIMIT }} 行。
                    </div>
                  </template>
                </div>
              </div>
            </section>
          </template>
          </div>
        </Transition>
      </div>
    </div>

    <dialog
      ref="confirmDialog"
      class="confirm-dialog"
      aria-labelledby="confirm-title"
      aria-describedby="confirm-message"
      @cancel.prevent="settleConfirm(false)"
      @close="onConfirmDialogClose"
      @click="onConfirmBackdropClick"
    >
      <div class="confirm-dialog__body">
        <h2 id="confirm-title" class="confirm-dialog__title">{{ confirmState.title }}</h2>
        <p id="confirm-message" class="confirm-dialog__message">{{ confirmState.message }}</p>
      </div>
      <div class="confirm-dialog__actions">
        <button type="button" class="ghost-button" @click="settleConfirm(false)">取消</button>
        <button
          ref="confirmButton"
          type="button"
          class="primary-button"
          :class="{ 'danger-button': confirmState.tone === 'danger' }"
          @click="settleConfirm(true)"
        >
          {{ confirmState.confirmLabel }}
        </button>
      </div>
    </dialog>
  </div>
</template>

<style scoped>
.server {
  min-height: 100vh;
  padding: var(--space-7) var(--content-padding) var(--space-10);
}

.server__container {
  max-width: var(--content-max-width);
  margin: 0 auto;
}

.server__header {
  display: flex;
  align-items: flex-start;
  gap: var(--space-4);
  margin-bottom: var(--space-6);
  flex-wrap: wrap;
}

.server__back {
  position: relative;
  width: 44px;
  height: 44px;
  padding: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(
    135deg,
    rgba(255, 255, 255, 0.72) 0%,
    rgba(232, 242, 252, 0.48) 100%
  );
  -webkit-backdrop-filter: blur(12px) saturate(180%);
  backdrop-filter: blur(12px) saturate(180%);
  border: 1px solid rgba(255, 255, 255, 0.82);
  border-radius: var(--radius-md);
  color: var(--color-text-primary);
  cursor: pointer;
  overflow: hidden;
  isolation: isolate;
  flex-shrink: 0;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.92),
    inset 0 -1px 0 rgba(0, 0, 0, 0.06),
    0 2px 8px rgba(18, 48, 82, 0.08);
  transition:
    transform var(--duration-fast) var(--ease-spring),
    color var(--duration-fast) var(--ease-smooth),
    box-shadow var(--duration-fast) var(--ease-smooth);
}

.server__back::before {
  content: '';
  position: absolute;
  inset: 0;
  background: var(--glass-highlight);
  border-radius: var(--radius-md);
  opacity: 0.6;
  pointer-events: none;
  z-index: -1;
}

.server__back:hover {
  color: var(--color-brand);
  transform: scale(1.05);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.98),
    inset 0 -1px 0 rgba(0, 0, 0, 0.08),
    0 4px 12px rgba(18, 48, 82, 0.12);
}

.server__back:active {
  transform: scale(0.95);
}

.server__back:focus-visible {
  outline: 2px solid var(--color-brand);
  outline-offset: -2px;
}

.server__heading {
  flex: 1 1 220px;
  min-width: 0;
}

.server__title {
  font-size: var(--font-size-title-3);
  font-weight: 600;
  line-height: var(--line-height-snug);
  letter-spacing: var(--letter-spacing-tight);
  color: var(--color-text-primary);
  margin-bottom: var(--space-2);
}

.server__subtitle {
  font-size: var(--font-size-callout);
  color: var(--color-text-secondary);
  line-height: var(--line-height-normal);
}

.server__header-status {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  align-self: center;
  padding: var(--space-2) var(--space-4);
  border-radius: var(--radius-pill);
  background: rgba(255, 255, 255, 0.5);
  border: 1px solid rgba(255, 255, 255, 0.78);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.86);
  font-size: var(--font-size-caption);
  font-weight: 600;
  color: var(--color-text-secondary);
  backdrop-filter: blur(12px) saturate(180%);
  -webkit-backdrop-filter: blur(12px) saturate(180%);
}

.status-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--tone-ink);
  box-shadow: 0 0 0 3px var(--tone-ink-bg);
}

.status-dot.is-good {
  background: var(--tone-green);
  box-shadow: 0 0 0 3px var(--tone-green-bg);
}

.status-dot.is-bad {
  background: var(--tone-rose);
  box-shadow: 0 0 0 3px var(--tone-rose-bg);
}

.status-dot.is-muted {
  background: var(--tone-ink);
  box-shadow: 0 0 0 3px var(--tone-ink-bg);
}

.server__tabs {
  margin-bottom: var(--space-4);
  min-width: 0;
}

.server__content {
  position: relative;
  min-height: 420px;
}

.server__panel {
  position: relative;
}

/* -------------------------------------------------------------------------- */
/* Panels                                                                      */
/* -------------------------------------------------------------------------- */

.panel {
  position: relative;
  background: var(--glass-bg);
  -webkit-backdrop-filter: var(--glass-blur);
  backdrop-filter: var(--glass-blur);
  border: 1px solid var(--glass-border);
  border-radius: var(--radius-xl);
  padding: var(--space-5);
  box-shadow: var(--glass-shadow);
  isolation: isolate;
  overflow: hidden;
}

.panel::before {
  content: '';
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 50%;
  background: var(--glass-highlight);
  border-radius: var(--radius-xl) var(--radius-xl) 0 0;
  opacity: 0.38;
  pointer-events: none;
}

.panel > * {
  position: relative;
  z-index: 1;
}

.panel__header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-3);
  margin-bottom: var(--space-5);
  flex-wrap: wrap;
}

.panel__title {
  font-size: var(--font-size-headline);
  font-weight: 600;
  color: var(--color-text-primary);
  line-height: var(--line-height-snug);
}

.panel__subtitle {
  margin-top: var(--space-1);
  font-size: var(--font-size-caption);
  color: var(--color-text-secondary);
  line-height: var(--line-height-normal);
}

.panel__actions {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-wrap: wrap;
}

/* -------------------------------------------------------------------------- */
/* Buttons and fields                                                         */
/* -------------------------------------------------------------------------- */

.ghost-button,
.primary-button,
.danger-button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  min-height: 38px;
  padding: 0 var(--space-4);
  border-radius: var(--radius-pill);
  border: 1px solid rgba(255, 255, 255, 0.82);
  background: linear-gradient(
    135deg,
    rgba(255, 255, 255, 0.72) 0%,
    rgba(232, 242, 252, 0.5) 100%
  );
  color: var(--color-text-primary);
  font-family: inherit;
  font-size: var(--font-size-callout);
  font-weight: 600;
  line-height: 1;
  white-space: nowrap;
  cursor: pointer;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.86),
    0 2px 8px rgba(18, 48, 82, 0.07);
  transition:
    transform var(--duration-fast) var(--ease-spring),
    color var(--duration-fast) var(--ease-smooth),
    box-shadow var(--duration-fast) var(--ease-smooth);
}

.ghost-button:hover:not(:disabled),
.primary-button:hover:not(:disabled),
.danger-button:hover:not(:disabled) {
  transform: translateY(-1px);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.94),
    0 4px 12px rgba(18, 48, 82, 0.11);
}

.ghost-button:active:not(:disabled),
.primary-button:active:not(:disabled),
.danger-button:active:not(:disabled) {
  transform: scale(0.97);
}

.ghost-button:disabled,
.primary-button:disabled,
.danger-button:disabled {
  opacity: 0.55;
  cursor: not-allowed;
  transform: none;
}

.ghost-button--icon {
  width: 38px;
  padding: 0;
  font-size: 17px;
}

.primary-button {
  border-color: rgba(0, 113, 227, 0.22);
  background: linear-gradient(135deg, #409cff 0%, #0071e3 100%);
  color: #ffffff;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.34),
    0 3px 12px rgba(0, 113, 227, 0.24);
}

.primary-button:hover:not(:disabled) {
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.4),
    0 6px 18px rgba(0, 113, 227, 0.3);
}

.danger-button {
  border-color: rgba(255, 59, 48, 0.24);
  background: linear-gradient(135deg, #ff6b61 0%, #ff3b30 100%);
  color: #ffffff;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.32),
    0 3px 12px rgba(255, 59, 48, 0.22);
}

.danger-button:hover:not(:disabled) {
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.38),
    0 6px 18px rgba(255, 59, 48, 0.28);
}

.form {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.form__actions {
  display: flex;
  gap: var(--space-2);
  flex-wrap: wrap;
}

.field {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.field__label {
  font-size: var(--font-size-caption);
  font-weight: 600;
  color: var(--color-text-secondary);
}

.input,
.textarea {
  width: 100%;
  padding: 10px var(--space-3);
  border: 1px solid var(--color-border-moderate);
  border-radius: var(--radius-md);
  background: rgba(255, 255, 255, 0.62);
  color: var(--color-text-primary);
  font: inherit;
  font-size: var(--font-size-callout);
  line-height: var(--line-height-normal);
  box-shadow: inset 0 1px 2px rgba(18, 48, 82, 0.04);
  transition:
    border-color var(--duration-fast) var(--ease-smooth),
    box-shadow var(--duration-fast) var(--ease-smooth);
}

.textarea {
  min-height: 86px;
  resize: vertical;
}

.input:focus-visible,
.textarea:focus-visible {
  outline: 2px solid var(--color-brand);
  outline-offset: 1px;
  border-color: transparent;
}

.field__hint {
  font-size: var(--font-size-caption);
  color: var(--color-text-tertiary);
  line-height: var(--line-height-normal);
}

.field__error {
  font-size: var(--font-size-caption);
  color: var(--tone-rose);
  line-height: var(--line-height-normal);
}

/* -------------------------------------------------------------------------- */
/* Badges and state blocks                                                    */
/* -------------------------------------------------------------------------- */

.badge {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 4px 10px;
  border-radius: var(--radius-pill);
  background: var(--tone-ink-bg);
  color: var(--tone-ink);
  font-size: var(--font-size-caption);
  font-weight: 600;
  line-height: 1.3;
  white-space: nowrap;
}

.badge--good {
  background: var(--tone-green-bg);
  color: #1d7a34;
}

.badge--warn {
  background: var(--tone-amber-bg);
  color: #a86400;
}

.badge--bad {
  background: var(--tone-rose-bg);
  color: var(--tone-rose);
}

.badge--info {
  background: var(--tone-blue-bg);
  color: var(--color-brand);
}

.state-block {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: var(--space-3);
  padding: var(--space-5);
  border-radius: var(--radius-lg);
  background: rgba(255, 255, 255, 0.44);
  border: 1px solid rgba(255, 255, 255, 0.68);
  color: var(--color-text-secondary);
  font-size: var(--font-size-callout);
  line-height: var(--line-height-normal);
}

.state-block--compact {
  padding: var(--space-3) var(--space-4);
  font-size: var(--font-size-caption);
}

.state-block--error {
  border-color: rgba(255, 59, 48, 0.22);
  background: rgba(255, 59, 48, 0.06);
  color: var(--tone-rose);
}

.inline-note {
  padding: var(--space-3) var(--space-4);
  border-radius: var(--radius-md);
  font-size: var(--font-size-callout);
  line-height: var(--line-height-normal);
  margin-top: var(--space-4);
}

.inline-note--error {
  background: var(--tone-rose-bg);
  color: var(--tone-rose);
}

.inline-note--warn {
  background: var(--tone-amber-bg);
  color: #8a5600;
}

.inline-note--success {
  background: var(--tone-green-bg);
  color: #1d7a34;
}

.inline-note--info {
  background: var(--tone-blue-bg);
  color: #0a5fb8;
}

.announcement-note {
  white-space: pre-line;
}

.empty-inline {
  font-size: var(--font-size-callout);
  color: var(--color-text-tertiary);
}

.spinner {
  width: 15px;
  height: 15px;
  border-radius: 50%;
  border: 2px solid rgba(0, 113, 227, 0.2);
  border-top-color: var(--color-brand);
  animation: spin 700ms linear infinite;
  flex-shrink: 0;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}

/* -------------------------------------------------------------------------- */
/* Summary and detail                                                         */
/* -------------------------------------------------------------------------- */

.summary-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(185px, 1fr));
  gap: var(--space-3);
  margin-top: var(--space-4);
}

.summary-card {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-height: 108px;
  padding: var(--space-4);
  border-radius: var(--radius-lg);
  background: rgba(255, 255, 255, 0.48);
  border: 1px solid rgba(255, 255, 255, 0.72);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.86),
    0 2px 8px rgba(18, 48, 82, 0.04);
}

.summary-card__label {
  font-size: var(--font-size-caption);
  font-weight: 600;
  color: var(--color-text-secondary);
}

.summary-card__value {
  font-size: 28px;
  font-weight: 600;
  letter-spacing: -0.03em;
  line-height: 1.1;
  color: var(--color-text-primary);
  word-break: break-word;
}

.summary-card__value.is-good {
  color: #1d7a34;
}

.summary-card__value.is-bad {
  color: var(--tone-rose);
}

.summary-card__value.is-warn {
  color: #a86400;
}

.summary-card__value.is-muted {
  color: var(--color-text-tertiary);
}

.summary-card__value--mono {
  font-family: 'SF Mono', 'Menlo', 'Monaco', 'Courier New', monospace;
  font-size: 18px;
  letter-spacing: -0.02em;
}

.summary-card__hint {
  font-size: var(--font-size-caption);
  color: var(--color-text-tertiary);
  line-height: var(--line-height-normal);
  word-break: break-word;
}

.detail-list {
  display: flex;
  flex-direction: column;
  margin-top: var(--space-4);
}

.detail-row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-4);
  padding: var(--space-3) 0;
  border-bottom: 1px solid var(--color-border-hairline);
  font-size: var(--font-size-callout);
}

.detail-row:last-child {
  border-bottom: none;
}

.detail-row__label {
  color: var(--color-text-secondary);
  flex-shrink: 0;
}

.detail-row__value {
  color: var(--color-text-primary);
  font-weight: 600;
  text-align: right;
  word-break: break-word;
}

.detail-row__value--pre {
  white-space: pre-line;
}

.status-line {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  flex-wrap: wrap;
  margin-top: var(--space-4);
}

.status-line__text {
  font-size: var(--font-size-callout);
  color: var(--color-text-secondary);
}

/* -------------------------------------------------------------------------- */
/* Sub panels, meters, metrics                                                */
/* -------------------------------------------------------------------------- */

.section-stack {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  margin-top: var(--space-4);
}

.sub-panel {
  padding: var(--space-4);
  border-radius: var(--radius-lg);
  background: rgba(255, 255, 255, 0.42);
  border: 1px solid rgba(255, 255, 255, 0.66);
}

.sub-panel__head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--space-3);
  flex-wrap: wrap;
  margin-bottom: var(--space-3);
}

.sub-panel__title {
  font-size: var(--font-size-body-large);
  font-weight: 600;
  color: var(--color-text-primary);
}

.sub-panel__meta {
  font-size: var(--font-size-caption);
  color: var(--color-text-tertiary);
}

.meter-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.meter-row {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.meter-row__head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--space-3);
  flex-wrap: wrap;
}

.meter-row__name {
  font-size: var(--font-size-callout);
  font-weight: 600;
  color: var(--color-text-primary);
  font-family: 'SF Mono', 'Menlo', 'Monaco', 'Courier New', monospace;
}

.meter-row__value {
  font-size: var(--font-size-caption);
  color: var(--color-text-secondary);
}

.meter {
  height: 8px;
  border-radius: var(--radius-pill);
  background: rgba(0, 0, 0, 0.07);
  overflow: hidden;
}

.meter__fill {
  display: block;
  height: 100%;
  border-radius: inherit;
  background: linear-gradient(90deg, #409cff, #0071e3);
  transition: width var(--duration-base) var(--ease-smooth);
}

.meter__fill--warn {
  background: linear-gradient(90deg, #ffc04d, #ff9f0a);
}

.meter__fill--danger {
  background: linear-gradient(90deg, #ff7a70, #ff3b30);
}

.metric-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: var(--space-2) var(--space-4);
}

.metric-grid--compact {
  grid-template-columns: repeat(auto-fit, minmax(90px, 1fr));
}

.metric {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: var(--space-2) 0;
}

.metric__label {
  font-size: var(--font-size-caption);
  color: var(--color-text-tertiary);
}

.metric__value {
  font-size: var(--font-size-callout);
  font-weight: 600;
  color: var(--color-text-primary);
  font-variant-numeric: tabular-nums;
}

.assessment-row {
  display: flex;
  justify-content: space-between;
  gap: var(--space-3);
  flex-wrap: wrap;
  margin-top: var(--space-3);
  padding-top: var(--space-3);
  border-top: 1px solid var(--color-border-hairline);
  font-size: var(--font-size-callout);
}

.assessment-row__label {
  color: var(--color-text-secondary);
}

.assessment-row__value {
  color: var(--color-text-primary);
  font-weight: 600;
  text-align: right;
}

.pool-block {
  margin-top: var(--space-4);
  padding-top: var(--space-4);
  border-top: 1px solid var(--color-border-hairline);
}

.pool-block__title {
  font-size: var(--font-size-callout);
  font-weight: 600;
  color: var(--color-text-primary);
  margin-bottom: var(--space-2);
}

.compact-list {
  display: flex;
  flex-direction: column;
}

.compact-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto auto;
  gap: var(--space-3);
  align-items: baseline;
  padding: var(--space-3) 0;
  border-bottom: 1px solid var(--color-border-hairline);
  font-size: var(--font-size-callout);
}

.compact-row:last-child {
  border-bottom: none;
}

.compact-row__name {
  font-family: 'SF Mono', 'Menlo', 'Monaco', 'Courier New', monospace;
  font-size: var(--font-size-caption);
  color: var(--color-text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.compact-row__meta {
  font-size: var(--font-size-caption);
  color: var(--color-text-tertiary);
  white-space: nowrap;
}

.compact-row__value {
  font-weight: 600;
  color: var(--color-text-primary);
  white-space: nowrap;
}

.note-block {
  margin-top: var(--space-4);
  padding: var(--space-4);
  border-radius: var(--radius-lg);
  background: rgba(255, 255, 255, 0.38);
  border: 1px solid rgba(255, 255, 255, 0.62);
}

.note-block__title {
  font-size: var(--font-size-callout);
  font-weight: 600;
  color: var(--color-text-primary);
  margin-bottom: var(--space-2);
}

.note-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding-left: 1.2em;
  margin-top: var(--space-3);
  list-style: disc;
  font-size: var(--font-size-caption);
  color: var(--color-text-secondary);
  line-height: var(--line-height-relaxed);
}

.note-block .note-list {
  margin-top: 0;
}

/* -------------------------------------------------------------------------- */
/* TOTP                                                                        */
/* -------------------------------------------------------------------------- */

.totp-stage {
  display: flex;
  align-items: flex-start;
  gap: var(--space-5);
  flex-wrap: wrap;
  margin-top: var(--space-5);
}

.totp-display {
  position: relative;
  display: grid;
  grid-template-columns: repeat(6, 1fr);
  gap: 8px;
  width: 320px;
  max-width: 100%;
}

.totp-digit {
  aspect-ratio: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: calc(var(--radius-lg) * 0.8);
  background: rgba(255, 255, 255, 0.56);
  backdrop-filter: blur(var(--glass-blur));
  border: 1px solid rgba(255, 255, 255, 0.8);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.9),
    0 8px 32px rgba(0, 0, 0, 0.1);
  font-family: 'SF Mono', 'Menlo', 'Monaco', 'Courier New', monospace;
  font-size: clamp(20px, 5vw, 32px);
  font-weight: 700;
  color: var(--color-text-primary);
  user-select: none;
  animation: surface-enter 0.5s ease-out both;
  animation-delay: var(--delay, 0ms);
}

@media (prefers-reduced-motion: reduce) {
  .totp-digit {
    animation: none;
  }
}

.totp-display.totp-expired .totp-digit {
  opacity: 0.4;
  color: var(--color-text-tertiary);
}

.totp-countdown {
  position: absolute;
  top: -12px;
  right: -12px;
  width: 80px;
  height: 80px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.totp-countdown__svg {
  position: absolute;
  inset: 0;
  color: var(--tone-blue);
  transform: rotate(-90deg);
}

.totp-countdown__ring {
  transition: stroke 0.3s ease, stroke-dashoffset 0.25s linear;
}

.totp-countdown__ring--critical {
  color: var(--tone-rose);
}

.totp-countdown__ring--warning {
  color: var(--tone-amber);
}

.totp-countdown__text {
  position: relative;
  z-index: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  background: rgba(255, 255, 255, 0.72);
  backdrop-filter: blur(12px);
  border-radius: 50%;
  width: 56px;
  height: 56px;
  border: 1px solid rgba(255, 255, 255, 0.8);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.9);
}

.totp-countdown__value {
  font-size: 18px;
  font-weight: 700;
  font-family: 'SF Mono', 'Menlo', 'Monaco', 'Courier New', monospace;
  color: var(--color-text-primary);
  line-height: 1;
}

.totp-countdown__unit {
  font-size: 10px;
  font-weight: 600;
  color: var(--color-text-secondary);
  margin-top: 2px;
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

.totp-actions {
  display: flex;
  gap: var(--space-2);
  flex-wrap: wrap;
  align-self: flex-start;
}

.totp-info {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-top: var(--space-5);
  padding: var(--space-3) var(--space-4);
  border-radius: var(--radius-md);
  background: rgba(0, 113, 227, 0.06);
  border-left: 3px solid var(--tone-blue);
}

.totp-info__item {
  font-size: 13px;
  color: var(--color-text-secondary);
  line-height: 1.4;
  margin: 0;
}

/* -------------------------------------------------------------------------- */
/* SSH                                                                         */
/* -------------------------------------------------------------------------- */

.target-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  margin-top: var(--space-4);
}

.target-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-4);
  padding: var(--space-4);
  border-radius: var(--radius-lg);
  background: rgba(255, 255, 255, 0.46);
  border: 1px solid rgba(255, 255, 255, 0.68);
}

.target-row__main {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}

.target-row__label {
  font-size: var(--font-size-callout);
  font-weight: 600;
  color: var(--color-text-primary);
}

.target-row__display {
  font-family: 'SF Mono', 'Menlo', 'Monaco', 'Courier New', monospace;
  font-size: var(--font-size-caption);
  color: var(--color-text-secondary);
  word-break: break-all;
}

/* -------------------------------------------------------------------------- */
/* Files                                                                       */
/* -------------------------------------------------------------------------- */

.path-bar {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-wrap: wrap;
  margin-top: var(--space-4);
}

.path-input {
  flex: 1 1 260px;
  min-width: 0;
}

.crumbs {
  display: flex;
  align-items: center;
  gap: 2px;
  flex-wrap: wrap;
  margin-top: var(--space-3);
  font-size: var(--font-size-caption);
}

.crumbs__sep {
  color: var(--color-text-tertiary);
}

.crumb {
  padding: 3px 7px;
  border-radius: var(--radius-xs);
  color: var(--color-brand);
  font-size: var(--font-size-caption);
  font-weight: 600;
}

.crumb:hover:not(:disabled) {
  background: var(--tone-blue-bg);
}

.crumb--current {
  color: var(--color-text-secondary);
  cursor: default;
}

.crumb:disabled {
  cursor: default;
}

.file-list {
  list-style: none;
  display: flex;
  flex-direction: column;
  margin-top: var(--space-4);
}

.file-row {
  border-bottom: 1px solid var(--color-border-hairline);
}

.file-row:last-child {
  border-bottom: none;
}

.file-row__main {
  display: grid;
  grid-template-columns: 26px minmax(0, 1fr) 90px 150px;
  gap: var(--space-3);
  align-items: center;
  width: 100%;
  padding: var(--space-3) var(--space-2);
  border-radius: var(--radius-sm);
  text-align: left;
  font: inherit;
  color: inherit;
  transition: background var(--duration-fast) var(--ease-smooth);
}

.file-row__main.is-clickable {
  cursor: pointer;
}

.file-row__main.is-clickable:hover {
  background: rgba(0, 113, 227, 0.06);
}

.file-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--color-text-tertiary);
}

.file-icon--dir {
  color: var(--color-brand);
}

.file-row__name-wrap {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.file-row__name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: var(--font-size-callout);
  font-weight: 500;
  color: var(--color-text-primary);
}

.file-row__sub {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: var(--font-size-caption);
  color: var(--color-text-tertiary);
}

.file-row__meta {
  font-size: var(--font-size-caption);
  color: var(--color-text-secondary);
  text-align: right;
  white-space: nowrap;
  font-variant-numeric: tabular-nums;
}

/* -------------------------------------------------------------------------- */
/* DB content                                                                  */
/* -------------------------------------------------------------------------- */

.content-layout {
  display: grid;
  grid-template-columns: minmax(0, 240px) minmax(0, 1fr);
  gap: var(--space-4);
  align-items: start;
  margin-top: var(--space-4);
}

.schema-panel {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  min-width: 0;
  padding: var(--space-4);
  border-radius: var(--radius-lg);
  background: rgba(255, 255, 255, 0.4);
  border: 1px solid rgba(255, 255, 255, 0.64);
}

.schema-list {
  display: flex;
  flex-direction: column;
  gap: 2px;
  max-height: 540px;
  overflow-y: auto;
  padding-right: 2px;
}

.schema-item {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  width: 100%;
  padding: 8px 10px;
  border-radius: var(--radius-sm);
  text-align: left;
  color: var(--color-text-secondary);
  transition:
    background var(--duration-fast) var(--ease-smooth),
    color var(--duration-fast) var(--ease-smooth);
}

.schema-item:hover {
  background: rgba(0, 113, 227, 0.06);
  color: var(--color-text-primary);
}

.schema-item:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}

.schema-item.is-active {
  background: var(--tone-blue-bg);
  color: var(--color-brand);
}

.schema-item__name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-family: 'SF Mono', 'Menlo', 'Monaco', 'Courier New', monospace;
  font-size: 12px;
  font-weight: 600;
}

.schema-item__type {
  font-size: 11px;
  color: var(--color-text-tertiary);
  flex-shrink: 0;
}

.content-detail {
  min-width: 0;
}

.content-detail__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-3);
  flex-wrap: wrap;
  margin-bottom: var(--space-3);
}

.content-detail__title {
  font-size: var(--font-size-body-large);
  font-weight: 600;
  color: var(--color-text-primary);
  font-family: 'SF Mono', 'Menlo', 'Monaco', 'Courier New', monospace;
  word-break: break-all;
}

.data-table-wrap {
  overflow: auto;
  max-width: 100%;
  border-radius: var(--radius-lg);
  border: 1px solid var(--color-border-hairline);
  background: rgba(255, 255, 255, 0.36);
  transition: opacity var(--duration-fast) var(--ease-smooth);
}

.data-table-wrap.is-loading {
  opacity: 0.5;
}

.data-table {
  border-collapse: collapse;
  min-width: 100%;
  font-size: var(--font-size-caption);
}

.data-table th {
  position: sticky;
  top: 0;
  z-index: 1;
  padding: 10px 12px;
  text-align: left;
  font-weight: 600;
  color: var(--color-text-secondary);
  white-space: nowrap;
  background: rgba(246, 250, 255, 0.95);
  border-bottom: 1px solid var(--color-border-hairline);
}

.data-table td {
  padding: 10px 12px;
  vertical-align: top;
  border-bottom: 1px solid var(--color-border-hairline);
  max-width: 280px;
}

.data-table tbody tr:last-child td {
  border-bottom: none;
}

.data-table tbody tr:hover {
  background: rgba(0, 113, 227, 0.04);
}

.data-table td.data-table__empty {
  padding: var(--space-5) var(--space-4);
  text-align: center;
  color: var(--color-text-tertiary);
}

.cell-text {
  display: block;
  max-width: 280px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--color-text-primary);
  font-variant-numeric: tabular-nums;
}

.pager {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  flex-wrap: wrap;
  margin-top: var(--space-3);
  animation: pager-in 300ms var(--ease-spring);
}

@keyframes pager-in {
  from {
    opacity: 0.55;
    transform: translateY(3px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

.pager__info {
  font-size: var(--font-size-caption);
  color: var(--color-text-secondary);
}

.pager__actions {
  display: flex;
  gap: var(--space-2);
}

/* -------------------------------------------------------------------------- */
/* Confirmation dialog                                                         */
/* -------------------------------------------------------------------------- */

.confirm-dialog {
  width: min(440px, calc(100vw - 32px));
  margin: auto;
  padding: 0;
  border: none;
  border-radius: var(--radius-xl);
  background: var(--color-bg-elevated);
  color: var(--color-text-primary);
  box-shadow: 0 24px 80px rgba(18, 48, 82, 0.28);
}

.confirm-dialog::backdrop {
  background: rgba(15, 30, 50, 0.32);
  -webkit-backdrop-filter: blur(4px);
  backdrop-filter: blur(4px);
}

.confirm-dialog[open] {
  animation: dialog-in 180ms var(--ease-out);
}

@keyframes dialog-in {
  from {
    opacity: 0;
    transform: translateY(8px) scale(0.98);
  }
  to {
    opacity: 1;
    transform: translateY(0) scale(1);
  }
}

.confirm-dialog__body {
  padding: var(--space-6) var(--space-6) var(--space-4);
}

.confirm-dialog__title {
  font-size: var(--font-size-headline);
  font-weight: 600;
  margin-bottom: var(--space-3);
}

.confirm-dialog__message {
  font-size: var(--font-size-callout);
  color: var(--color-text-secondary);
  line-height: var(--line-height-relaxed);
  white-space: pre-line;
}

.confirm-dialog__actions {
  display: flex;
  justify-content: flex-end;
  gap: var(--space-2);
  padding: 0 var(--space-6) var(--space-6);
}

/* -------------------------------------------------------------------------- */
/* Transitions and responsive                                                 */
/* -------------------------------------------------------------------------- */

.tab-fade-enter-active,
.tab-fade-leave-active {
  transition:
    opacity 160ms var(--ease-smooth),
    transform 160ms var(--ease-smooth);
}

.tab-fade-leave-active {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  pointer-events: none;
}

.tab-fade-enter-from {
  opacity: 0;
  transform: translateY(6px);
}

.tab-fade-leave-to {
  opacity: 0;
  transform: translateY(-4px);
}

@media (max-width: 900px) {
  .content-layout {
    grid-template-columns: 1fr;
  }

  .schema-list {
    max-height: 260px;
  }
}

@media (max-width: 640px) {
  .server {
    padding: var(--space-5) var(--space-4) var(--space-9);
  }

  .server__title {
    font-size: 26px;
  }

  .panel {
    padding: var(--space-4);
    border-radius: var(--radius-lg);
  }

  .summary-grid {
    grid-template-columns: 1fr;
  }

  .detail-row {
    flex-direction: column;
    gap: 2px;
  }

  .detail-row__value {
    text-align: left;
  }

  .file-row__main {
    grid-template-columns: 24px minmax(0, 1fr) auto;
  }

  .file-row__meta--size {
    display: none;
  }

  .totp-code {
    width: 100%;
    text-align: center;
  }

  .confirm-dialog__body {
    padding: var(--space-5) var(--space-5) var(--space-3);
  }

  .confirm-dialog__actions {
    padding: 0 var(--space-5) var(--space-5);
  }
}

@media (prefers-reduced-motion: reduce) {
  .server__back,
  .ghost-button,
  .primary-button,
  .danger-button,
  .meter__fill,
  .tab-fade-enter-active,
  .tab-fade-leave-active {
    transition: none;
  }

  .spinner,
  .confirm-dialog[open],
  .pager {
    animation: none;
  }

  .server__back:hover,
  .ghost-button:hover:not(:disabled),
  .primary-button:hover:not(:disabled),
  .danger-button:hover:not(:disabled) {
    transform: none;
  }
}

@supports not (backdrop-filter: blur(14px)) {
  .panel,
  .server__back,
  .server__header-status,
  .summary-card,
  .sub-panel,
  .target-row,
  .schema-panel,
  .data-table-wrap,
  .state-block,
  .note-block,
  .confirm-dialog {
    background: rgba(255, 255, 255, 0.92);
  }

  .ghost-button,
  .primary-button,
  .danger-button {
    background: rgba(255, 255, 255, 0.94);
  }

  .primary-button {
    background: #0071e3;
  }

  .danger-button {
    background: #ff3b30;
  }
}
</style>
