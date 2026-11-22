<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import AnimatedTabs from '@/components/AnimatedTabs.vue'
import type { AnimatedTabItem } from '@/components/AnimatedTabs.vue'
import AiChatPanel from '@/components/AiChatPanel.vue'
import BubbleFeedback from '@/components/BubbleFeedback.vue'
import { ApiError, readableError } from '@/api/http'
import { currentRoles } from '@/api/permissions'
import {
  fetchAiHealth,
  fetchAiKeys,
  fetchAiLogStats,
  fetchAiLogs,
  fetchAiModels,
  fetchAiOcrEngines,
  fetchAiQuotaUsers,
  fetchAiScheduledTasks,
  type AiHealthData,
  type AiHealthProvider,
  type AiKeyRecord,
  type AiLogRecord,
  type AiLogStats,
  type AiModelRow,
  type AiOcrEngine,
  type AiQuotaUserRow,
  type AiScheduledTask
} from '@/api/admin-ai'

const router = useRouter()

const allTabs: AnimatedTabItem[] = [
  { value: 'chat', label: '对话', hint: '直接调用网关模型' },
  { value: 'overview', label: '概览', hint: '网关健康与容量' },
  { value: 'keys', label: '密钥', hint: '只读凭据清单（不含密钥）' },
  { value: 'models', label: '模型', hint: '渠道目录与覆盖行' },
  { value: 'quota', label: '额度', hint: '用户 AF 额度分布' },
  { value: 'logs', label: '日志', hint: '调用流水（只读）' },
  { value: 'scheduled', label: '定时任务', hint: '任务排程与审核状态' },
  { value: 'ocr', label: 'OCR 引擎', hint: '引擎注册表（只读）' },
  { value: 'monitor', label: '监控', hint: '近 7 天调用聚合' }
]

const isAdminUser = currentRoles().includes('admin')
const tabs = computed(() =>
  isAdminUser
    ? allTabs
    : allTabs.filter((tab) => ['chat', 'quota'].includes(tab.value))
)

const activeTab = ref('chat')
const currentTabLabel = computed(
  () => tabs.value.find((item) => item.value === activeTab.value)?.label ?? ''
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

function formatDateTime(value: string | null | undefined): string {
  if (!value) return '—'
  const raw = value.includes('T') ? value : value.replace(' ', 'T')
  const date = new Date(raw)
  if (Number.isNaN(date.getTime())) return '—'
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(
    date.getHours()
  )}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
}

function formatNumber(value: number | null | undefined): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '—'
  return value.toLocaleString('zh-CN')
}

function formatDuration(ms: number | null | undefined): string {
  if (typeof ms !== 'number' || !Number.isFinite(ms) || ms < 0) return '—'
  if (ms < 1000) return `${Math.round(ms)} ms`
  return `${(ms / 1000).toFixed(ms >= 10_000 ? 1 : 2)} s`
}

function formatContext(value: number | null | undefined): string {
  if (typeof value !== 'number' || !Number.isFinite(value) || value <= 0) return '未标注'
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`
  if (value >= 1000) return `${Math.round(value / 1000)}K`
  return formatNumber(value)
}

function formatRate(value: number | null | undefined): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '默认'
  const rounded = Math.round(value * 100) / 100
  return `×${Number.isInteger(rounded) ? rounded : rounded.toFixed(2)}`
}

function shortOid(value: string | null | undefined): string {
  if (!value) return '—'
  return value.length > 12 ? `…${value.slice(-8)}` : value
}

function hostOf(value: string): string {
  if (!value) return ''
  try {
    return new URL(value).host
  } catch {
    return value.replace(/^https?:\/\//, '').replace(/\/.*$/, '')
  }
}

function tabError(reason: unknown, fallback: string): string {
  if (reason instanceof ApiError) {
    if (reason.status === 401) return '登录状态已失效，请重新登录。'
    if (reason.status === 403) return '当前账号没有访问该分区的权限。'
    if (reason.status === 503) return '服务暂不可用，请稍后重试。'
  }
  return readableError(reason, fallback)
}

function totalPages(total: number, size: number): number {
  if (!Number.isFinite(total) || total <= 0 || size <= 0) return 1
  return Math.max(1, Math.ceil(total / size))
}

/* -------------------------------------------------------------------------- */
/* Overview                                                                    */
/* -------------------------------------------------------------------------- */

const health = ref<AiHealthData | null>(null)
const healthLoading = ref(false)
const healthError = ref('')
const healthLoaded = ref(false)

async function loadHealth() {
  if (healthLoading.value) return
  healthLoading.value = true
  healthError.value = ''
  try {
    health.value = await fetchAiHealth()
    healthLoaded.value = true
  } catch (reason) {
    healthError.value = tabError(reason, '网关健康信息读取失败。')
  } finally {
    healthLoading.value = false
  }
}

const overviewEnabledText = computed(() => {
  if (!health.value) return '未知'
  return health.value.enabled ? '已启用' : '已停用'
})

const localEngineText = computed(() => {
  const ready = health.value?.localEngineReady
  if (ready === null || ready === undefined) return '本地引擎状态未上报'
  return ready ? '本地引擎已就绪' : '本地引擎未就绪'
})

const credentialTotal = computed(() => {
  const credentials = health.value?.credentials
  if (!credentials) return 0
  return credentials.personal + credentials.shared + credentials.server
})

function providerModelsText(provider: AiHealthProvider): string {
  if (!provider.models.length) return '未声明模型'
  if (provider.models.length <= 2) return provider.models.join('、')
  return `${provider.models.slice(0, 2).join('、')} 等 ${provider.models.length} 个`
}

/* -------------------------------------------------------------------------- */
/* Keys (read-only)                                                            */
/* -------------------------------------------------------------------------- */

const keys = ref<AiKeyRecord[]>([])
const keysLoading = ref(false)
const keysError = ref('')
const keysLoaded = ref(false)

async function loadKeys() {
  if (keysLoading.value) return
  keysLoading.value = true
  keysError.value = ''
  try {
    keys.value = await fetchAiKeys()
    keysLoaded.value = true
  } catch (reason) {
    keysError.value = tabError(reason, '凭据清单读取失败。')
  } finally {
    keysLoading.value = false
  }
}

const KEY_SCOPE_LABELS: Record<string, string> = {
  personal: '个人',
  shared: '共享',
  server: '站点'
}

function keyScopeText(scope: string): string {
  return KEY_SCOPE_LABELS[scope] ?? (scope || '未知')
}

function keyScopeClass(scope: string): string {
  if (scope === 'shared') return 'badge--good'
  if (scope === 'server') return 'badge--warn'
  return 'badge--info'
}

function keyProviderText(key: AiKeyRecord): string {
  const parts = [key.kind, hostOf(key.baseUrl)].filter((item) => item.length > 0)
  return parts.length ? parts.join(' · ') : '未标注渠道'
}

function keyHintText(key: AiKeyRecord): string {
  return key.keyHint ? `尾号 ${key.keyHint}` : '未返回提示'
}

/* -------------------------------------------------------------------------- */
/* Models (read-only)                                                          */
/* -------------------------------------------------------------------------- */

const models = ref<AiModelRow[]>([])
const modelsLoading = ref(false)
const modelsError = ref('')
const modelsLoaded = ref(false)

async function loadModels() {
  if (modelsLoading.value) return
  modelsLoading.value = true
  modelsError.value = ''
  try {
    models.value = await fetchAiModels()
    modelsLoaded.value = true
  } catch (reason) {
    modelsError.value = tabError(reason, '模型清单读取失败。')
  } finally {
    modelsLoading.value = false
  }
}

function modelProvidersText(model: AiModelRow): string {
  if (model.providers.length) return model.providers.join('、')
  return model.provider || '未标注渠道'
}

/* -------------------------------------------------------------------------- */
/* Quota users (read-only)                                                     */
/* -------------------------------------------------------------------------- */

const QUOTA_PAGE_SIZE = 20
const quotaUsers = ref<AiQuotaUserRow[]>([])
const quotaTotal = ref(0)
const quotaPage = ref(1)
const quotaQuery = ref('')
const quotaDraft = ref('')
const quotaLoading = ref(false)
const quotaError = ref('')
const quotaLoaded = ref(false)
let quotaSeq = 0

const quotaPages = computed(() => totalPages(quotaTotal.value, QUOTA_PAGE_SIZE))

async function loadQuota() {
  const seq = ++quotaSeq
  quotaLoading.value = true
  quotaError.value = ''
  try {
    const data = await fetchAiQuotaUsers(quotaQuery.value, quotaPage.value, QUOTA_PAGE_SIZE)
    if (seq !== quotaSeq) return
    quotaUsers.value = data.items
    quotaTotal.value = data.total
    quotaLoaded.value = true
  } catch (reason) {
    if (seq !== quotaSeq) return
    if (!quotaLoaded.value) {
      quotaUsers.value = []
      quotaTotal.value = 0
    }
    quotaError.value = tabError(reason, '用户额度读取失败。')
  } finally {
    if (seq === quotaSeq) quotaLoading.value = false
  }
}

function searchQuota() {
  quotaQuery.value = quotaDraft.value.trim()
  quotaPage.value = 1
  quotaLoaded.value = false
  void loadQuota()
}

function quotaPrev() {
  if (quotaPage.value <= 1 || quotaLoading.value) return
  quotaPage.value -= 1
  quotaLoaded.value = false
  void loadQuota()
}

function quotaNext() {
  if (quotaPage.value >= quotaPages.value || quotaLoading.value) return
  quotaPage.value += 1
  quotaLoaded.value = false
  void loadQuota()
}

const QUOTA_SOURCE_LABELS: Record<string, string> = {
  explicit: '库内已固化',
  role_default: '角色默认',
  global_default: '全局默认'
}

function quotaSourceText(user: AiQuotaUserRow): string {
  return QUOTA_SOURCE_LABELS[user.source] ?? '来源未知'
}

function quotaPercent(user: AiQuotaUserRow): number {
  if (!Number.isFinite(user.limit) || user.limit <= 0) return 0
  return Math.min(100, Math.max(0, (user.used / user.limit) * 100))
}

function quotaFillClass(user: AiQuotaUserRow): string {
  const percent = quotaPercent(user)
  if (percent >= 95) return 'meter__fill--danger'
  if (percent >= 75) return 'meter__fill--warn'
  return ''
}

function quotaUserLabel(user: AiQuotaUserRow): string {
  return user.businessId || shortOid(user.oid)
}

/* -------------------------------------------------------------------------- */
/* Call logs (read-only)                                                       */
/* -------------------------------------------------------------------------- */

const LOG_PAGE_SIZE = 20
const logs = ref<AiLogRecord[]>([])
const logsTotal = ref(0)
const logsPage = ref(1)
const logsLoading = ref(false)
const logsError = ref('')
const logsLoaded = ref(false)
let logsSeq = 0

const logsPages = computed(() => totalPages(logsTotal.value, LOG_PAGE_SIZE))

async function loadLogs() {
  const seq = ++logsSeq
  logsLoading.value = true
  logsError.value = ''
  try {
    const data = await fetchAiLogs(logsPage.value, LOG_PAGE_SIZE)
    if (seq !== logsSeq) return
    logs.value = data.rows
    logsTotal.value = data.total
    logsLoaded.value = true
  } catch (reason) {
    if (seq !== logsSeq) return
    if (!logsLoaded.value) {
      logs.value = []
      logsTotal.value = 0
    }
    logsError.value = tabError(reason, '调用日志读取失败。')
  } finally {
    if (seq === logsSeq) logsLoading.value = false
  }
}

function logsPrev() {
  if (logsPage.value <= 1 || logsLoading.value) return
  logsPage.value -= 1
  logsLoaded.value = false
  void loadLogs()
}

function logsNext() {
  if (logsPage.value >= logsPages.value || logsLoading.value) return
  logsPage.value += 1
  logsLoaded.value = false
  void loadLogs()
}

const LOG_STATUS_LABELS: Record<string, string> = {
  success: '成功',
  error: '错误',
  timeout: '超时',
  rejected: '拒绝',
  busy: '忙碌',
  warning: '警告'
}

function logStatusText(status: string): string {
  return LOG_STATUS_LABELS[status] ?? (status || '未知')
}

function logStatusClass(status: string): string {
  if (status === 'success') return 'badge--good'
  if (status === 'error' || status === 'rejected') return 'badge--bad'
  if (status === 'timeout' || status === 'busy') return 'badge--warn'
  return 'badge--info'
}

function capabilityText(capability: string): string {
  if (capability === 'chat') return '对话'
  if (capability === 'ocr') return 'OCR'
  return capability || '未知能力'
}

function purposeText(purpose: string): string {
  const labels: Record<string, string> = {
    chat: '对话',
    ocr: 'OCR',
    'markdown-assistant': 'Markdown 助手',
    agent: '智能体',
    scheduled: '定时任务'
  }
  return labels[purpose] ?? purpose
}

function logUserText(log: AiLogRecord): string {
  if (log.userName) return log.userName
  if (log.userOid) return shortOid(log.userOid)
  return '未知用户'
}

function logTokenText(log: AiLogRecord): string {
  if (log.totalTokens !== null && log.totalTokens > 0) {
    return `${formatNumber(log.totalTokens)} tokens`
  }
  if (log.units > 0) return `${formatNumber(log.units)} 单位`
  return '用量未上报'
}

function logModelText(log: AiLogRecord): string {
  const provider = log.provider || '未知渠道'
  return log.model ? `${provider} / ${log.model}` : provider
}

/* -------------------------------------------------------------------------- */
/* Scheduled tasks (read-only)                                                 */
/* -------------------------------------------------------------------------- */

const scheduledTasks = ref<AiScheduledTask[]>([])
const scheduledLoading = ref(false)
const scheduledError = ref('')
const scheduledLoaded = ref(false)

async function loadScheduled() {
  if (scheduledLoading.value) return
  scheduledLoading.value = true
  scheduledError.value = ''
  try {
    scheduledTasks.value = await fetchAiScheduledTasks(50)
    scheduledLoaded.value = true
  } catch (reason) {
    scheduledError.value = tabError(reason, '定时任务读取失败。')
  } finally {
    scheduledLoading.value = false
  }
}

const REVIEW_STATUS_LABELS: Record<string, string> = {
  draft: '草稿',
  pending: '待审核',
  approved: '已通过',
  rejected: '已驳回'
}

function reviewStatusText(status: string): string {
  return REVIEW_STATUS_LABELS[status] ?? (status || '未知')
}

function reviewStatusClass(status: string): string {
  if (status === 'approved') return 'badge--good'
  if (status === 'rejected') return 'badge--bad'
  if (status === 'pending') return 'badge--warn'
  return 'badge--info'
}

const LAST_STATUS_LABELS: Record<string, string> = {
  queued: '已入队',
  skipped_review: '未审核跳过',
  skipped_busy: '忙碌跳过',
  skipped_no_key: '无可用凭据',
  skipped_quota: '额度不足',
  skipped_owner: '所有者不可用',
  skipped_flagged: '内容被拦截',
  failed: '执行失败'
}

function lastStatusText(status: string): string {
  if (!status) return '尚未运行'
  return LAST_STATUS_LABELS[status] ?? status
}

function lastStatusClass(status: string): string {
  if (status === 'queued') return 'badge--info'
  if (status === 'failed') return 'badge--bad'
  if (status.startsWith('skipped_')) return 'badge--warn'
  return ''
}

function ownerText(task: AiScheduledTask): string {
  return task.ownerName || shortOid(task.ownerOid)
}

function scheduleText(task: AiScheduledTask): string {
  if (task.scheduleKind === 'cron' && task.cronExpr) return `cron ${task.cronExpr}`
  if (task.intervalMinutes > 0) return `每 ${formattedMinutes(task.intervalMinutes)}`
  return '排程未知'
}

function formattedMinutes(minutes: number): string {
  if (minutes >= 1440 && minutes % 1440 === 0) return `${minutes / 1440} 天`
  if (minutes >= 60 && minutes % 60 === 0) return `${minutes / 60} 小时`
  return `${minutes} 分钟`
}

/* -------------------------------------------------------------------------- */
/* OCR engines (read-only)                                                     */
/* -------------------------------------------------------------------------- */

const ocrEngines = ref<AiOcrEngine[]>([])
const ocrLoading = ref(false)
const ocrError = ref('')
const ocrLoaded = ref(false)

async function loadOcr() {
  if (ocrLoading.value) return
  ocrLoading.value = true
  ocrError.value = ''
  try {
    ocrEngines.value = await fetchAiOcrEngines()
    ocrLoaded.value = true
  } catch (reason) {
    ocrError.value = tabError(reason, 'OCR 引擎清单读取失败。')
  } finally {
    ocrLoading.value = false
  }
}

const OCR_KIND_LABELS: Record<string, string> = {
  tencent: '腾讯云',
  aliyun: '阿里云',
  baidu: '百度',
  rapidocr: '本地 RapidOCR',
  groq: 'Groq',
  zhipu: '智谱'
}

function ocrKindText(kind: string): string {
  return OCR_KIND_LABELS[kind] ?? (kind || '未知类型')
}

function ocrSecretText(engine: AiOcrEngine): string {
  if (engine.kind === 'rapidocr' && !engine.hasSecret) return '本地引擎无需凭证'
  if (!engine.hasSecret) return '未配置凭证'
  return engine.keyHint ? `已配置 · 尾号 ${engine.keyHint}` : '已配置'
}

function ocrSecretBadgeText(engine: AiOcrEngine): string {
  if (engine.hasSecret) return '凭证就绪'
  return engine.kind === 'rapidocr' ? '无需凭证' : '缺少凭证'
}

function ocrSecretBadgeClass(engine: AiOcrEngine): string {
  if (engine.hasSecret) return 'badge--good'
  return engine.kind === 'rapidocr' ? 'badge--info' : 'badge--warn'
}

/* -------------------------------------------------------------------------- */
/* Monitoring                                                                  */
/* -------------------------------------------------------------------------- */

const STATS_DAY_OPTIONS = [7, 14, 30]
const statsDays = ref(7)
const stats = ref<AiLogStats | null>(null)
const statsLoading = ref(false)
const statsError = ref('')
const statsLoaded = ref(false)
let statsSeq = 0

const statsTotals = computed(() => {
  const daily = stats.value?.daily ?? []
  return daily.reduce(
    (acc, row) => {
      acc.calls += row.calls
      acc.errors += row.errors
      acc.tokens += row.totalTokens
      return acc
    },
    { calls: 0, errors: 0, tokens: 0 }
  )
})

const statusStats = computed(() => stats.value?.byStatus ?? [])
const topProviders = computed(() => (stats.value?.byProvider ?? []).slice(0, 5))
const topModels = computed(() => (stats.value?.byModel ?? []).slice(0, 5))
const maxProviderCalls = computed(() =>
  topProviders.value.reduce((max, row) => Math.max(max, row.calls), 0)
)
const maxModelCalls = computed(() =>
  topModels.value.reduce((max, row) => Math.max(max, row.calls), 0)
)

async function loadStats() {
  const seq = ++statsSeq
  statsLoading.value = true
  statsError.value = ''
  try {
    const data = await fetchAiLogStats(statsDays.value)
    if (seq !== statsSeq) return
    stats.value = data
    statsLoaded.value = true
  } catch (reason) {
    if (seq !== statsSeq) return
    if (!statsLoaded.value) stats.value = null
    statsError.value = tabError(reason, '调用统计读取失败。')
  } finally {
    if (seq === statsSeq) statsLoading.value = false
  }
}

function changeStatsDays(days: number) {
  if (statsDays.value === days || statsLoading.value) return
  statsDays.value = days
  statsLoaded.value = false
  void loadStats()
}

function dailyDateText(date: string): string {
  return /^\d{4}-\d{2}-\d{2}$/.test(date) ? date.slice(5).replace('-', '/') : date || '—'
}

function groupShare(calls: number, max: number): number {
  if (!max || calls <= 0) return 0
  return Math.min(100, Math.max(2, (calls / max) * 100))
}

function statusStatClass(status: string): string {
  return logStatusClass(status)
}

function statusStatText(status: string): string {
  return logStatusText(status)
}

/* -------------------------------------------------------------------------- */
/* Tab wiring                                                                  */
/* -------------------------------------------------------------------------- */

function ensureTab(tab: string) {
  if (tab === 'chat') {
    // Chat panel manages its own loading state
    return
  }
  if (tab === 'overview') {
    if (!healthLoaded.value && !healthLoading.value && !healthError.value) void loadHealth()
    return
  }
  if (tab === 'keys') {
    if (!keysLoaded.value && !keysLoading.value && !keysError.value) void loadKeys()
    return
  }
  if (tab === 'models') {
    if (!modelsLoaded.value && !modelsLoading.value && !modelsError.value) void loadModels()
    return
  }
  if (tab === 'quota') {
    if (!quotaLoaded.value && !quotaLoading.value && !quotaError.value) void loadQuota()
    return
  }
  if (tab === 'logs') {
    if (!logsLoaded.value && !logsLoading.value && !logsError.value) void loadLogs()
    return
  }
  if (tab === 'scheduled') {
    if (!scheduledLoaded.value && !scheduledLoading.value && !scheduledError.value) {
      void loadScheduled()
    }
    return
  }
  if (tab === 'ocr') {
    if (!ocrLoaded.value && !ocrLoading.value && !ocrError.value) void loadOcr()
    return
  }
  if (tab === 'monitor') {
    if (!statsLoaded.value && !statsLoading.value && !statsError.value) void loadStats()
  }
}

const panelBusy = computed(() => {
  if (activeTab.value === 'chat') return false
  if (activeTab.value === 'overview') return healthLoading.value
  if (activeTab.value === 'keys') return keysLoading.value
  if (activeTab.value === 'models') return modelsLoading.value
  if (activeTab.value === 'quota') return quotaLoading.value
  if (activeTab.value === 'logs') return logsLoading.value
  if (activeTab.value === 'scheduled') return scheduledLoading.value
  if (activeTab.value === 'ocr') return ocrLoading.value
  if (activeTab.value === 'monitor') return statsLoading.value
  return false
})

watch(activeTab, ensureTab, { immediate: true })
</script>

<template>
  <div class="ai-gateway">
    <div class="ai-gateway__container">
      <header class="ai-gateway__header surface-enter" style="--i: 0">
        <button type="button" class="ai-gateway__back" aria-label="返回" @click="goBack">
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
        <div class="ai-gateway__heading">
          <h1 class="ai-gateway__title">AI 网关</h1>
          <p class="ai-gateway__subtitle">只读管理控制台 · 密钥、模型、额度与调用观测</p>
        </div>
        <div class="ai-gateway__header-status" aria-live="polite">
          <span class="status-dot" aria-hidden="true"></span>
          <span>{{ currentTabLabel }}</span>
        </div>
      </header>

      <div class="ai-gateway__tabs surface-enter" style="--i: 1">
        <AnimatedTabs v-model="activeTab" :items="tabs" aria-label="AI 网关分区" />
      </div>

      <div
        class="ai-gateway__content"
        role="tabpanel"
        :aria-label="currentTabLabel"
        :aria-busy="panelBusy"
      >
        <Transition name="tab-fade" mode="out-in">
          <div :key="activeTab" class="ai-gateway__panel">
            <!-- 对话 -->
            <template v-if="activeTab === 'chat'">
              <section class="panel">
                <div class="panel__header">
                  <div>
                    <h2 class="panel__title">AI 对话</h2>
                    <p class="panel__subtitle">直接调用网关模型进行对话测试</p>
                  </div>
                </div>
                <AiChatPanel />
              </section>
            </template>

            <!-- 概览 -->
            <template v-else-if="activeTab === 'overview'">
              <section class="panel">
                <div class="panel__header">
                  <div>
                    <h2 class="panel__title">网关概览</h2>
                    <p class="panel__subtitle">来自 /ai/health 的实时容量与凭据统计</p>
                  </div>
                  <div class="panel__actions">
                    <span
                      v-if="health"
                      class="badge"
                      :class="health.enabled ? 'badge--good' : 'badge--bad'"
                    >
                      {{ overviewEnabledText }}
                    </span>
                    <button
                      type="button"
                      class="ghost-button"
                      :disabled="healthLoading"
                      @click="loadHealth"
                    >
                      {{ healthLoading ? '刷新中…' : '刷新' }}
                    </button>
                  </div>
                </div>

                <div v-if="healthLoading && !healthLoaded" class="state-block" role="status">
                  <span class="spinner" aria-hidden="true"></span>
                  正在读取网关健康信息…
                </div>

                <div
                  v-else-if="healthError && !healthLoaded"
                  class="state-block state-block--error"
                  role="alert"
                >
                  <BubbleFeedback class="glass-feedback--error">{{ healthError }}</BubbleFeedback>
                  <button type="button" class="ghost-button" @click="loadHealth">重试</button>
                </div>

                <template v-else-if="health">
                  <BubbleFeedback v-if="healthError" class="inline-note glass-feedback--error" role="alert">
                    {{ healthError }}
                  </BubbleFeedback>

                  <div class="summary-grid">
                    <div class="summary-card surface-enter" style="--i: 0">
                      <span class="summary-card__label">网关状态</span>
                      <span
                        class="summary-card__value"
                        :class="health.enabled ? 'is-good' : 'is-bad'"
                      >
                        {{ overviewEnabledText }}
                      </span>
                      <span class="summary-card__hint">{{ localEngineText }}</span>
                    </div>
                    <div class="summary-card surface-enter" style="--i: 1">
                      <span class="summary-card__label">工作进程</span>
                      <span class="summary-card__value">{{ formatNumber(health.workerCount) }}</span>
                      <span class="summary-card__hint">部署的进程数</span>
                    </div>
                    <div class="summary-card surface-enter" style="--i: 2">
                      <span class="summary-card__label">并发上限</span>
                      <span class="summary-card__value">{{ formatNumber(health.maxConcurrency) }}</span>
                      <span class="summary-card__hint">AI_MAX_CONCURRENCY 配置值</span>
                    </div>
                    <div class="summary-card surface-enter" style="--i: 3">
                      <span class="summary-card__label">模型 / 渠道</span>
                      <span class="summary-card__value">
                        {{ formatNumber(health.models.length) }} /
                        {{ formatNumber(health.providers.length) }}
                      </span>
                      <span class="summary-card__hint">目录模型数与已登记渠道数</span>
                    </div>
                    <div class="summary-card surface-enter" style="--i: 4">
                      <span class="summary-card__label">凭据总数</span>
                      <span class="summary-card__value">{{ formatNumber(credentialTotal) }}</span>
                      <span class="summary-card__hint">
                        个人 {{ formatNumber(health.credentials.personal) }} ·
                        共享 {{ formatNumber(health.credentials.shared) }} ·
                        站点 {{ formatNumber(health.credentials.server) }}
                      </span>
                    </div>
                  </div>

                  <div class="section-stack">
                    <section class="sub-panel surface-enter" style="--i: 1">
                      <div class="sub-panel__head">
                        <h3 class="sub-panel__title">速率限制</h3>
                        <span class="sub-panel__meta">
                          每进程实际阈值
                          <template v-if="health.promptCacheMode">
                            · 缓存 {{ health.promptCacheMode }}
                          </template>
                        </span>
                      </div>
                      <div v-if="health.rateLimitPerMinute.length" class="metric-grid">
                        <div
                          v-for="item in health.rateLimitPerMinute"
                          :key="item.capability"
                          class="metric"
                        >
                          <span class="metric__label">{{ capabilityText(item.capability) }}</span>
                          <span class="metric__value">
                            {{ formatNumber(item.limit) }} 次/分钟
                          </span>
                        </div>
                      </div>
                      <p v-else class="empty-inline">接口未返回频控配置。</p>
                      <p v-if="health.quotaDefaults.length" class="panel__footnote">
                        默认额度：
                        <template
                          v-for="(item, index) in health.quotaDefaults"
                          :key="item.kind"
                        >
                          {{ index > 0 ? '，' : '' }}{{ item.kind.toUpperCase() }}
                          {{ formatNumber(item.limit) }}
                        </template>
                      </p>
                    </section>

                    <section class="sub-panel surface-enter" style="--i: 2">
                      <div class="sub-panel__head">
                        <h3 class="sub-panel__title">渠道</h3>
                        <span class="sub-panel__meta">
                          {{ formatNumber(health.providers.length) }} 个已登记渠道
                        </span>
                      </div>
                      <ul v-if="health.providers.length" class="row-list">
                        <li
                          v-for="provider in health.providers"
                          :key="provider.name"
                          class="row row--compact"
                        >
                          <div class="row__main">
                            <span class="row__title">
                              {{ provider.name }}
                              <span v-if="provider.kind" class="row__id">{{ provider.kind }}</span>
                            </span>
                            <span class="row__meta">
                              {{ hostOf(provider.baseUrl) || '未标注地址' }} ·
                              {{ providerModelsText(provider) }}
                            </span>
                          </div>
                          <span
                            class="badge"
                            :class="
                              provider.configured === true
                                ? 'badge--good'
                                : provider.configured === false
                                  ? 'badge--warn'
                                  : 'badge--info'
                            "
                          >
                            {{
                              provider.configured === true
                                ? '已配置'
                                : provider.configured === false
                                  ? '未配置'
                                  : '状态未知'
                            }}
                          </span>
                        </li>
                      </ul>
                      <p v-else class="empty-inline">接口未返回渠道清单。</p>
                    </section>

                    <section class="sub-panel surface-enter" style="--i: 3">
                      <div class="sub-panel__head">
                        <h3 class="sub-panel__title">共享凭据</h3>
                        <span class="sub-panel__meta">平台出资、全员可用</span>
                      </div>
                      <ul v-if="health.credentials.sharedEntries.length" class="row-list">
                        <li
                          v-for="entry in health.credentials.sharedEntries"
                          :key="entry.name"
                          class="row row--compact"
                        >
                          <div class="row__main">
                            <span class="row__title">{{ entry.name }}</span>
                            <span class="row__meta">
                              {{ hostOf(entry.baseUrl) || '未标注地址' }} ·
                              {{
                                entry.models.length
                                  ? `${entry.models.length} 个模型`
                                  : '未声明模型'
                              }}
                            </span>
                          </div>
                          <span class="badge" :class="entry.enabled ? 'badge--good' : 'badge--info'">
                            {{ entry.enabled ? '启用' : '停用' }}
                          </span>
                        </li>
                      </ul>
                      <p v-else class="empty-inline">暂无共享凭据。</p>
                    </section>

                    <section
                      v-if="health.cooldownSeconds.length"
                      class="sub-panel surface-enter"
                      style="--i: 4"
                    >
                      <div class="sub-panel__head">
                        <h3 class="sub-panel__title">路由冷却</h3>
                        <span class="sub-panel__meta">
                          {{ health.cooldownScope || '作用域未知' }}
                        </span>
                      </div>
                      <ul class="row-list">
                        <li
                          v-for="item in health.cooldownSeconds"
                          :key="item.provider"
                          class="row row--compact"
                        >
                          <div class="row__main">
                            <span class="row__title">{{ item.provider }}</span>
                          </div>
                          <span class="badge badge--warn">剩余 {{ item.seconds }} 秒</span>
                        </li>
                      </ul>
                    </section>
                  </div>
                </template>
              </section>
            </template>

            <!-- 密钥 -->
            <template v-else-if="activeTab === 'keys'">
              <section class="panel">
                <div class="panel__header">
                  <div>
                    <h2 class="panel__title">密钥与凭据</h2>
                    <p class="panel__subtitle">只读清单 · 服务端只返回末四位提示，不含任何密钥</p>
                  </div>
                  <div class="panel__actions">
                    <span class="badge badge--info">
                      共 {{ formatNumber(keys.length) }} 条
                    </span>
                    <button
                      type="button"
                      class="ghost-button"
                      :disabled="keysLoading"
                      @click="loadKeys"
                    >
                      {{ keysLoading ? '刷新中…' : '刷新' }}
                    </button>
                  </div>
                </div>

                <div v-if="keysLoading && !keysLoaded" class="state-block" role="status">
                  <span class="spinner" aria-hidden="true"></span>
                  正在读取凭据清单…
                </div>

                <div
                  v-else-if="keysError && !keysLoaded"
                  class="state-block state-block--error"
                  role="alert"
                >
                  <BubbleFeedback class="glass-feedback--error">{{ keysError }}</BubbleFeedback>
                  <button type="button" class="ghost-button" @click="loadKeys">重试</button>
                </div>

                <template v-else>
                  <BubbleFeedback v-if="keysError" class="inline-note glass-feedback--error" role="alert">
                    {{ keysError }}
                  </BubbleFeedback>
                  <p v-if="!keys.length" class="empty-inline">暂无已登记的凭据。</p>
                  <ul v-else class="row-list" :class="{ 'is-loading': keysLoading }">
                    <li v-for="key in keys" :key="key.oid || key.name" class="row">
                      <div class="row__main">
                        <span class="row__title">
                          {{ key.name }}
                          <span class="badge" :class="keyScopeClass(key.scope)">
                            {{ keyScopeText(key.scope) }}
                          </span>
                          <span class="badge" :class="key.enabled ? 'badge--good' : 'badge--info'">
                            {{ key.enabled ? '启用' : '停用' }}
                          </span>
                        </span>
                        <span class="row__meta">
                          {{ keyProviderText(key) }} · {{ keyHintText(key) }} ·
                          更新 {{ formatDateTime(key.updatedAt) }}
                        </span>
                        <span class="row__detail">
                          <template v-if="key.models.length">
                            模型：
                            <template v-for="(model, index) in key.models.slice(0, 4)" :key="model.id">
                              {{ index > 0 ? '、' : '' }}{{ model.id }}
                            </template>
                            <template v-if="key.models.length > 4">
                              … 等 {{ key.models.length }} 个
                            </template>
                          </template>
                          <template v-else>未声明模型</template>
                        </span>
                      </div>
                      <span v-if="key.expiresAt" class="badge badge--warn">
                        到期 {{ formatDateTime(key.expiresAt) }}
                      </span>
                      <span v-else class="badge badge--info">永不过期</span>
                    </li>
                  </ul>
                  <p class="panel__footnote">
                    出于安全考虑，本页不提供新增、轮换或删除操作，也不会展示凭据内容。
                  </p>
                </template>
              </section>
            </template>

            <!-- 模型 -->
            <template v-else-if="activeTab === 'models'">
              <section class="panel">
                <div class="panel__header">
                  <div>
                    <h2 class="panel__title">模型目录</h2>
                    <p class="panel__subtitle">渠道目录行与手工覆盖行合并展示 · 只读</p>
                  </div>
                  <div class="panel__actions">
                    <span class="badge badge--info">
                      共 {{ formatNumber(models.length) }} 个
                    </span>
                    <button
                      type="button"
                      class="ghost-button"
                      :disabled="modelsLoading"
                      @click="loadModels"
                    >
                      {{ modelsLoading ? '刷新中…' : '刷新' }}
                    </button>
                  </div>
                </div>

                <div v-if="modelsLoading && !modelsLoaded" class="state-block" role="status">
                  <span class="spinner" aria-hidden="true"></span>
                  正在读取模型清单…
                </div>

                <div
                  v-else-if="modelsError && !modelsLoaded"
                  class="state-block state-block--error"
                  role="alert"
                >
                  <BubbleFeedback class="glass-feedback--error">{{ modelsError }}</BubbleFeedback>
                  <button type="button" class="ghost-button" @click="loadModels">重试</button>
                </div>

                <template v-else>
                  <BubbleFeedback v-if="modelsError" class="inline-note glass-feedback--error" role="alert">
                    {{ modelsError }}
                  </BubbleFeedback>
                  <p v-if="!models.length" class="empty-inline">暂无模型记录。</p>
                  <ul v-else class="row-list" :class="{ 'is-loading': modelsLoading }">
                    <li v-for="model in models" :key="model.model" class="row">
                      <div class="row__main">
                        <span class="row__title">
                          <span class="row__mono">{{ model.model }}</span>
                          <span class="badge" :class="model.enabled ? 'badge--good' : 'badge--info'">
                            {{ model.enabled ? '启用' : '停用' }}
                          </span>
                          <span class="badge" :class="model.inCatalog ? 'badge--info' : ''">
                            {{ model.inCatalog ? '目录模型' : '手工登记' }}
                          </span>
                        </span>
                        <span class="row__meta">
                          {{ modelProvidersText(model) }} ·
                          上下文 {{ formatContext(model.contextLength) }} ·
                          倍率 {{ formatRate(model.rate) }}
                        </span>
                        <span v-if="model.notes" class="row__detail">{{ model.notes }}</span>
                      </div>
                      <span v-if="model.displayName" class="badge badge--info">
                        {{ model.displayName }}
                      </span>
                    </li>
                  </ul>
                  <p class="panel__footnote">
                    倍率乘在每次调用的 AF 消耗上；「手工登记」表示该模型未出现在任何渠道目录中。
                  </p>
                </template>
              </section>
            </template>

            <!-- 额度 -->
            <template v-else-if="activeTab === 'quota'">
              <section class="panel">
                <div class="panel__header">
                  <div>
                    <h2 class="panel__title">用户额度</h2>
                    <p class="panel__subtitle">
                      统一 AF 池 · 对话与 OCR 共用同一份额度 · 只读
                    </p>
                  </div>
                  <div class="panel__actions">
                    <span class="badge badge--info">共 {{ formatNumber(quotaTotal) }} 人</span>
                    <button
                      type="button"
                      class="ghost-button"
                      :disabled="quotaLoading"
                      @click="loadQuota"
                    >
                      {{ quotaLoading ? '刷新中…' : '刷新' }}
                    </button>
                  </div>
                </div>

                <form class="toolbar" role="search" @submit.prevent="searchQuota">
                  <label class="sr-only" for="ai-quota-search">搜索用户</label>
                  <input
                    id="ai-quota-search"
                    v-model="quotaDraft"
                    class="input toolbar__search"
                    type="search"
                    placeholder="按姓名或学号搜索"
                    autocomplete="off"
                  />
                  <button type="submit" class="primary-button" :disabled="quotaLoading">
                    搜索
                  </button>
                </form>

                <div v-if="quotaLoading && !quotaLoaded" class="state-block" role="status">
                  <span class="spinner" aria-hidden="true"></span>
                  正在读取用户额度…
                </div>

                <div
                  v-else-if="quotaError && !quotaLoaded"
                  class="state-block state-block--error"
                  role="alert"
                >
                  <BubbleFeedback class="glass-feedback--error">{{ quotaError }}</BubbleFeedback>
                  <button type="button" class="ghost-button" @click="loadQuota">重试</button>
                </div>

                <template v-else>
                  <BubbleFeedback v-if="quotaError" class="inline-note glass-feedback--error" role="alert">
                    {{ quotaError }}
                  </BubbleFeedback>
                  <p v-if="!quotaUsers.length" class="empty-inline">没有匹配的用户。</p>
                  <ul v-else class="row-list" :class="{ 'is-loading': quotaLoading }">
                    <li v-for="user in quotaUsers" :key="user.oid || user.name" class="row">
                      <div class="row__main">
                        <span class="row__title">
                          {{ user.name }}
                          <span class="row__id">{{ quotaUserLabel(user) }}</span>
                          <span v-if="user.pendingRequest" class="badge badge--warn">
                            有待审申请
                          </span>
                        </span>
                        <span class="row__meta">
                          AF 已用 {{ formatNumber(user.used) }} /
                          {{ formatNumber(user.limit) }} ·
                          来源 {{ quotaSourceText(user) }}
                          <template v-if="user.period"> · 周期 {{ user.period }}</template>
                        </span>
                        <div
                          class="meter"
                          role="progressbar"
                          :aria-label="`${user.name} 的 AF 额度使用率`"
                          :aria-valuenow="Math.round(quotaPercent(user))"
                          aria-valuemin="0"
                          aria-valuemax="100"
                        >
                          <span
                            class="meter__fill"
                            :class="quotaFillClass(user)"
                            :style="{ width: `${quotaPercent(user)}%` }"
                          ></span>
                        </div>
                      </div>
                      <span class="badge badge--info">
                        {{ Math.round(quotaPercent(user)) }}%
                      </span>
                    </li>
                  </ul>
                  <div class="pager" :key="quotaPage">
                    <span class="pager__info">
                      第 {{ quotaPage }} / {{ quotaPages }} 页 · 每页 {{ QUOTA_PAGE_SIZE }} 条
                    </span>
                    <div class="pager__actions">
                      <button
                        type="button"
                        class="ghost-button"
                        :disabled="quotaPage <= 1 || quotaLoading"
                        @click="quotaPrev"
                      >
                        上一页
                      </button>
                      <button
                        type="button"
                        class="ghost-button"
                        :disabled="quotaPage >= quotaPages || quotaLoading"
                        @click="quotaNext"
                      >
                        下一页
                      </button>
                    </div>
                  </div>
                </template>
              </section>
            </template>

            <!-- 日志 -->
            <template v-else-if="activeTab === 'logs'">
              <section class="panel">
                <div class="panel__header">
                  <div>
                    <h2 class="panel__title">调用日志</h2>
                    <p class="panel__subtitle">新调用在前 · 仅管理员可读 · 只读</p>
                  </div>
                  <div class="panel__actions">
                    <span class="badge badge--info">共 {{ formatNumber(logsTotal) }} 条</span>
                    <button
                      type="button"
                      class="ghost-button"
                      :disabled="logsLoading"
                      @click="loadLogs"
                    >
                      {{ logsLoading ? '刷新中…' : '刷新' }}
                    </button>
                  </div>
                </div>

                <div v-if="logsLoading && !logsLoaded" class="state-block" role="status">
                  <span class="spinner" aria-hidden="true"></span>
                  正在读取调用日志…
                </div>

                <div
                  v-else-if="logsError && !logsLoaded"
                  class="state-block state-block--error"
                  role="alert"
                >
                  <BubbleFeedback class="glass-feedback--error">{{ logsError }}</BubbleFeedback>
                  <button type="button" class="ghost-button" @click="loadLogs">重试</button>
                </div>

                <template v-else>
                  <BubbleFeedback v-if="logsError" class="inline-note glass-feedback--error" role="alert">
                    {{ logsError }}
                  </BubbleFeedback>
                  <p v-if="!logs.length" class="empty-inline">当前页没有调用记录。</p>
                  <ul v-else class="row-list" :class="{ 'is-loading': logsLoading }">
                    <li v-for="log in logs" :key="log.oid" class="row">
                      <div class="row__main">
                        <span class="row__title">
                          <span class="badge" :class="logStatusClass(log.status)">
                            {{ logStatusText(log.status) }}
                          </span>
                          <span>{{ capabilityText(log.capability) }}</span>
                          <span class="row__mono">{{ logModelText(log) }}</span>
                        </span>
                        <span class="row__meta">
                          {{ formatDateTime(log.createdAt) }} ·
                          {{ log.purpose ? purposeText(log.purpose) : '来源未知' }} ·
                          {{ logUserText(log) }}
                        </span>
                        <span class="row__detail">
                          {{ logTokenText(log) }} · 耗时 {{ formatDuration(log.latencyMs) }}
                          <template v-if="log.errorCode"> · 错误码 {{ log.errorCode }}</template>
                          <template v-else-if="log.httpStatus"> · HTTP {{ log.httpStatus }}</template>
                        </span>
                      </div>
                      <span class="row__id">{{ shortOid(log.oid) }}</span>
                    </li>
                  </ul>
                  <div class="pager" :key="logsPage">
                    <span class="pager__info">
                      第 {{ logsPage }} / {{ logsPages }} 页 · 每页 {{ LOG_PAGE_SIZE }} 条
                    </span>
                    <div class="pager__actions">
                      <button
                        type="button"
                        class="ghost-button"
                        :disabled="logsPage <= 1 || logsLoading"
                        @click="logsPrev"
                      >
                        上一页
                      </button>
                      <button
                        type="button"
                        class="ghost-button"
                        :disabled="logsPage >= logsPages || logsLoading"
                        @click="logsNext"
                      >
                        下一页
                      </button>
                    </div>
                  </div>
                </template>
              </section>
            </template>

            <!-- 定时任务 -->
            <template v-else-if="activeTab === 'scheduled'">
              <section class="panel">
                <div class="panel__header">
                  <div>
                    <h2 class="panel__title">定时任务</h2>
                    <p class="panel__subtitle">跨全部用户的管理视图 · 最多 50 条 · 只读</p>
                  </div>
                  <div class="panel__actions">
                    <span class="badge badge--info">
                      本页 {{ formatNumber(scheduledTasks.length) }} 条
                    </span>
                    <button
                      type="button"
                      class="ghost-button"
                      :disabled="scheduledLoading"
                      @click="loadScheduled"
                    >
                      {{ scheduledLoading ? '刷新中…' : '刷新' }}
                    </button>
                  </div>
                </div>

                <div
                  v-if="scheduledLoading && !scheduledLoaded"
                  class="state-block"
                  role="status"
                >
                  <span class="spinner" aria-hidden="true"></span>
                  正在读取定时任务…
                </div>

                <div
                  v-else-if="scheduledError && !scheduledLoaded"
                  class="state-block state-block--error"
                  role="alert"
                >
                  <BubbleFeedback class="glass-feedback--error">{{ scheduledError }}</BubbleFeedback>
                  <button type="button" class="ghost-button" @click="loadScheduled">重试</button>
                </div>

                <template v-else>
                  <BubbleFeedback v-if="scheduledError" class="inline-note glass-feedback--error" role="alert">
                    {{ scheduledError }}
                  </BubbleFeedback>
                  <p v-if="!scheduledTasks.length" class="empty-inline">暂无定时任务。</p>
                  <ul v-else class="row-list" :class="{ 'is-loading': scheduledLoading }">
                    <li v-for="task in scheduledTasks" :key="task.oid || task.name" class="row">
                      <div class="row__main">
                        <span class="row__title">
                          {{ task.name }}
                          <span class="badge" :class="reviewStatusClass(task.reviewStatus)">
                            {{ reviewStatusText(task.reviewStatus) }}
                          </span>
                          <span class="badge" :class="task.enabled ? 'badge--good' : 'badge--info'">
                            {{ task.enabled ? '启用' : '停用' }}
                          </span>
                        </span>
                        <span class="row__meta">
                          所有者 {{ ownerText(task) }} · {{ scheduleText(task) }} ·
                          已运行 {{ formatNumber(task.runCount) }} 次
                        </span>
                        <span class="row__detail">
                          最近运行 {{ formatDateTime(task.lastRunAt) }} ·
                          {{ lastStatusText(task.lastStatus) }}
                          <template v-if="task.lastError"> · {{ task.lastError }}</template>
                          <template v-else-if="task.nextRunAt">
                            · 下次 {{ formatDateTime(task.nextRunAt) }}
                          </template>
                        </span>
                      </div>
                      <span
                        v-if="task.lastStatus"
                        class="badge"
                        :class="lastStatusClass(task.lastStatus)"
                      >
                        {{ lastStatusText(task.lastStatus) }}
                      </span>
                    </li>
                  </ul>
                  <p class="panel__footnote">
                    管理视图只显示最新一页；审核与执行操作请前往主站，Pro 控制台保持只读。
                  </p>
                </template>
              </section>
            </template>

            <!-- OCR 引擎 -->
            <template v-else-if="activeTab === 'ocr'">
              <section class="panel">
                <div class="panel__header">
                  <div>
                    <h2 class="panel__title">OCR 引擎</h2>
                    <p class="panel__subtitle">全站生效的引擎注册表 · 只返回凭证有无与末四位</p>
                  </div>
                  <div class="panel__actions">
                    <span class="badge badge--info">
                      共 {{ formatNumber(ocrEngines.length) }} 个
                    </span>
                    <button
                      type="button"
                      class="ghost-button"
                      :disabled="ocrLoading"
                      @click="loadOcr"
                    >
                      {{ ocrLoading ? '刷新中…' : '刷新' }}
                    </button>
                  </div>
                </div>

                <div v-if="ocrLoading && !ocrLoaded" class="state-block" role="status">
                  <span class="spinner" aria-hidden="true"></span>
                  正在读取 OCR 引擎…
                </div>

                <div
                  v-else-if="ocrError && !ocrLoaded"
                  class="state-block state-block--error"
                  role="alert"
                >
                  <BubbleFeedback class="glass-feedback--error">{{ ocrError }}</BubbleFeedback>
                  <button type="button" class="ghost-button" @click="loadOcr">重试</button>
                </div>

                <template v-else>
                  <BubbleFeedback v-if="ocrError" class="inline-note glass-feedback--error" role="alert">
                    {{ ocrError }}
                  </BubbleFeedback>
                  <p v-if="!ocrEngines.length" class="empty-inline">暂无 OCR 引擎。</p>
                  <ul v-else class="row-list" :class="{ 'is-loading': ocrLoading }">
                    <li v-for="engine in ocrEngines" :key="engine.oid || engine.name" class="row">
                      <div class="row__main">
                        <span class="row__title">
                          {{ engine.name }}
                          <span class="row__id">{{ ocrKindText(engine.kind) }}</span>
                          <span class="badge" :class="engine.enabled ? 'badge--good' : 'badge--info'">
                            {{ engine.enabled ? '启用' : '停用' }}
                          </span>
                        </span>
                        <span class="row__meta">
                          凭证 {{ ocrSecretText(engine) }} · 优先级 {{ formatNumber(engine.priority) }}
                          <template v-if="engine.updatedAt">
                            · 更新 {{ formatDateTime(engine.updatedAt) }}
                          </template>
                        </span>
                        <span class="row__detail">
                          {{ engine.models.length ? `模型：${engine.models.join('、')}` : '未标注模型' }}
                        </span>
                      </div>
                      <span
                        class="badge"
                        :class="ocrSecretBadgeClass(engine)"
                      >
                        {{ ocrSecretBadgeText(engine) }}
                      </span>
                    </li>
                  </ul>
                  <p class="panel__footnote">
                    Pro 控制台不展示引擎配置与任何凭证内容，也不提供新增或修改入口。
                  </p>
                </template>
              </section>
            </template>

            <!-- 监控 -->
            <template v-else-if="activeTab === 'monitor'">
              <section class="panel">
                <div class="panel__header">
                  <div>
                    <h2 class="panel__title">调用监控</h2>
                    <p class="panel__subtitle">
                      按自然日聚合 ·
                      {{ stats?.since ? `窗口自 ${formatDateTime(stats?.since)}` : '近 7 天' }}
                    </p>
                  </div>
                  <div class="panel__actions">
                    <div class="day-switch" role="group" aria-label="统计窗口">
                      <button
                        v-for="days in STATS_DAY_OPTIONS"
                        :key="days"
                        type="button"
                        class="ghost-button ghost-button--small"
                        :class="{ 'is-active': statsDays === days }"
                        :aria-pressed="statsDays === days"
                        :disabled="statsLoading"
                        @click="changeStatsDays(days)"
                      >
                        {{ days }} 天
                      </button>
                    </div>
                    <button
                      type="button"
                      class="ghost-button"
                      :disabled="statsLoading"
                      @click="loadStats"
                    >
                      {{ statsLoading ? '刷新中…' : '刷新' }}
                    </button>
                  </div>
                </div>

                <div v-if="statsLoading && !statsLoaded" class="state-block" role="status">
                  <span class="spinner" aria-hidden="true"></span>
                  正在读取调用统计…
                </div>

                <div
                  v-else-if="statsError && !statsLoaded"
                  class="state-block state-block--error"
                  role="alert"
                >
                  <BubbleFeedback class="glass-feedback--error">{{ statsError }}</BubbleFeedback>
                  <button type="button" class="ghost-button" @click="loadStats">重试</button>
                </div>

                <template v-else-if="stats">
                  <BubbleFeedback v-if="statsError" class="inline-note glass-feedback--error" role="alert">
                    {{ statsError }}
                  </BubbleFeedback>

                  <div class="summary-grid">
                    <div class="summary-card surface-enter" style="--i: 0">
                      <span class="summary-card__label">调用总量</span>
                      <span class="summary-card__value">{{ formatNumber(statsTotals.calls) }}</span>
                      <span class="summary-card__hint">{{ stats.days }} 天窗口</span>
                    </div>
                    <div class="summary-card surface-enter" style="--i: 1">
                      <span class="summary-card__label">错误数</span>
                      <span class="summary-card__value">{{ formatNumber(statsTotals.errors) }}</span>
                      <span class="summary-card__hint">
                        {{
                          statsTotals.calls > 0
                            ? `错误率 ${((statsTotals.errors / statsTotals.calls) * 100).toFixed(1)}%`
                            : '暂无调用'
                        }}
                      </span>
                    </div>
                    <div class="summary-card surface-enter" style="--i: 2">
                      <span class="summary-card__label">Token 总数</span>
                      <span class="summary-card__value">{{ formatNumber(statsTotals.tokens) }}</span>
                      <span class="summary-card__hint">按日汇总的 total_tokens</span>
                    </div>
                    <div class="summary-card surface-enter" style="--i: 3">
                      <span class="summary-card__label">活跃渠道 / 模型</span>
                      <span class="summary-card__value">
                        {{ formatNumber(stats.byProvider.length) }} /
                        {{ formatNumber(stats.byModel.length) }}
                      </span>
                      <span class="summary-card__hint">窗口内产生过流量的维度</span>
                    </div>
                  </div>

                  <div class="section-stack">
                    <section class="sub-panel surface-enter" style="--i: 1">
                      <div class="sub-panel__head">
                        <h3 class="sub-panel__title">按日汇总</h3>
                        <span class="sub-panel__meta">{{ formatNumber(stats.daily.length) }} 天</span>
                      </div>
                      <p v-if="!stats.daily.length" class="empty-inline">暂无按日数据。</p>
                      <ul v-else class="row-list">
                        <li v-for="row in stats.daily" :key="row.date" class="row row--compact">
                          <div class="row__main">
                            <span class="row__title">
                              <span class="row__mono">{{ dailyDateText(row.date) }}</span>
                              <span class="badge badge--info">{{ formatNumber(row.calls) }} 次</span>
                              <span
                                v-if="row.errors > 0"
                                class="badge badge--bad"
                              >
                                {{ formatNumber(row.errors) }} 错误
                              </span>
                            </span>
                            <span class="row__meta">
                              {{ formatNumber(row.totalTokens) }} tokens ·
                              平均 {{ formatDuration(row.avgLatencyMs) }}
                              <template v-if="row.promptTokens + row.completionTokens > 0">
                                · 输入 {{ formatNumber(row.promptTokens) }} /
                                输出 {{ formatNumber(row.completionTokens) }}
                              </template>
                            </span>
                          </div>
                        </li>
                      </ul>
                    </section>

                    <section class="sub-panel surface-enter" style="--i: 2">
                      <div class="sub-panel__head">
                        <h3 class="sub-panel__title">按状态</h3>
                        <span class="sub-panel__meta">窗口内合计</span>
                      </div>
                      <p v-if="!statusStats.length" class="empty-inline">暂无状态数据。</p>
                      <ul v-else class="row-list">
                        <li
                          v-for="row in statusStats"
                          :key="row.status"
                          class="row row--compact"
                        >
                          <div class="row__main">
                            <span class="row__title">
                              <span class="badge" :class="statusStatClass(row.status)">
                                {{ statusStatText(row.status) }}
                              </span>
                            </span>
                          </div>
                          <span class="row__id">{{ formatNumber(row.calls) }} 次</span>
                        </li>
                      </ul>
                    </section>

                    <section class="sub-panel surface-enter" style="--i: 3">
                      <div class="sub-panel__head">
                        <h3 class="sub-panel__title">渠道 Top 5</h3>
                        <span class="sub-panel__meta">按调用次数排序</span>
                      </div>
                      <p v-if="!topProviders.length" class="empty-inline">暂无渠道数据。</p>
                      <ul v-else class="row-list">
                        <li
                          v-for="row in topProviders"
                          :key="row.key"
                          class="row row--compact"
                        >
                          <div class="row__main">
                            <span class="row__title">{{ row.key }}</span>
                            <div class="bar" aria-hidden="true">
                              <span
                                class="bar__fill"
                                :style="{ width: `${groupShare(row.calls, maxProviderCalls)}%` }"
                              ></span>
                            </div>
                            <span class="row__meta">
                              {{ formatNumber(row.calls) }} 次 ·
                              {{ formatNumber(row.errors) }} 错误 ·
                              {{ formatNumber(row.totalTokens) }} tokens
                            </span>
                          </div>
                        </li>
                      </ul>
                    </section>

                    <section class="sub-panel surface-enter" style="--i: 4">
                      <div class="sub-panel__head">
                        <h3 class="sub-panel__title">模型 Top 5</h3>
                        <span class="sub-panel__meta">按调用次数排序</span>
                      </div>
                      <p v-if="!topModels.length" class="empty-inline">暂无模型数据。</p>
                      <ul v-else class="row-list">
                        <li v-for="row in topModels" :key="row.key" class="row row--compact">
                          <div class="row__main">
                            <span class="row__title row__mono">{{ row.key }}</span>
                            <div class="bar" aria-hidden="true">
                              <span
                                class="bar__fill"
                                :style="{ width: `${groupShare(row.calls, maxModelCalls)}%` }"
                              ></span>
                            </div>
                            <span class="row__meta">
                              {{ formatNumber(row.calls) }} 次 ·
                              {{ formatNumber(row.errors) }} 错误 ·
                              {{ formatNumber(row.totalTokens) }} tokens
                            </span>
                          </div>
                        </li>
                      </ul>
                    </section>
                  </div>
                </template>
              </section>
            </template>
          </div>
        </Transition>
      </div>
    </div>
  </div>
</template>

<style scoped>
.ai-gateway {
  min-height: 100vh;
  padding: var(--space-7) var(--content-padding) var(--space-10);
}

.ai-gateway__container {
  max-width: var(--content-max-width);
  margin: 0 auto;
}

.ai-gateway__header {
  display: flex;
  align-items: flex-start;
  gap: var(--space-4);
  margin-bottom: var(--space-6);
  flex-wrap: wrap;
}

.ai-gateway__back {
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

.ai-gateway__back::before {
  content: '';
  position: absolute;
  inset: 0;
  background: var(--glass-highlight);
  border-radius: var(--radius-md);
  opacity: 0.6;
  pointer-events: none;
  z-index: -1;
}

.ai-gateway__back:hover {
  color: var(--color-brand);
  transform: scale(1.05);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.98),
    inset 0 -1px 0 rgba(0, 0, 0, 0.08),
    0 4px 12px rgba(18, 48, 82, 0.12);
}

.ai-gateway__back:active {
  transform: scale(0.95);
}

.ai-gateway__back:focus-visible {
  outline: 2px solid var(--color-brand);
  outline-offset: -2px;
}

.ai-gateway__heading {
  flex: 1 1 220px;
  min-width: 0;
}

.ai-gateway__title {
  font-size: var(--font-size-title-3);
  font-weight: 600;
  line-height: var(--line-height-snug);
  letter-spacing: var(--letter-spacing-tight);
  color: var(--color-text-primary);
  margin-bottom: var(--space-2);
}

.ai-gateway__subtitle {
  font-size: var(--font-size-callout);
  color: var(--color-text-secondary);
  line-height: var(--line-height-normal);
}

.ai-gateway__header-status {
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
  -webkit-backdrop-filter: blur(12px) saturate(180%);
  backdrop-filter: blur(12px) saturate(180%);
}

.status-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--tone-blue);
  box-shadow: 0 0 0 3px var(--tone-blue-bg);
}

.ai-gateway__tabs {
  margin-bottom: var(--space-4);
  min-width: 0;
}

.ai-gateway__content {
  min-height: 420px;
}

/* -------------------------------------------------------------------------- */
/* Panels                                                                      */
/* -------------------------------------------------------------------------- */

.panel {
  position: relative;
  background: var(--surface-matte);
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

.panel__footnote {
  margin-top: var(--space-3);
  font-size: var(--font-size-caption);
  color: var(--color-text-tertiary);
  line-height: var(--line-height-normal);
}

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

/* -------------------------------------------------------------------------- */
/* Buttons and fields                                                          */
/* -------------------------------------------------------------------------- */

.ghost-button,
.primary-button {
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
.primary-button:hover:not(:disabled) {
  transform: translateY(-1px);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.94),
    0 4px 12px rgba(18, 48, 82, 0.11);
}

.ghost-button:active:not(:disabled),
.primary-button:active:not(:disabled) {
  transform: scale(0.97);
}

.ghost-button:disabled,
.primary-button:disabled {
  opacity: 0.55;
  cursor: not-allowed;
  transform: none;
}

.ghost-button:focus-visible,
.primary-button:focus-visible {
  outline: 2px solid var(--color-brand);
  outline-offset: 2px;
}

.ghost-button--small {
  min-height: 32px;
  padding: 0 var(--space-3);
  font-size: var(--font-size-caption);
}

.ghost-button--small.is-active {
  border-color: rgba(0, 113, 227, 0.24);
  background: var(--tone-blue-bg);
  color: var(--color-brand);
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

.day-switch {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px;
  border-radius: var(--radius-pill);
  background: rgba(255, 255, 255, 0.42);
  border: 1px solid rgba(255, 255, 255, 0.68);
}

.toolbar {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-wrap: wrap;
  margin-bottom: var(--space-4);
}

.toolbar__search {
  flex: 1 1 240px;
  min-width: 0;
}

.input {
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

.input:focus-visible {
  outline: 2px solid var(--color-brand);
  outline-offset: 1px;
  border-color: transparent;
}

/* -------------------------------------------------------------------------- */
/* Badges and states                                                           */
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

.state-block--error {
  border-color: rgba(255, 59, 48, 0.22);
  background: rgba(255, 59, 48, 0.06);
  color: var(--tone-rose);
}

.inline-note {
  margin-top: var(--space-4);
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

.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}

/* -------------------------------------------------------------------------- */
/* Summary cards                                                               */
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
  font-variant-numeric: tabular-nums;
}

.summary-card__value.is-good {
  color: #1d7a34;
}

.summary-card__value.is-bad {
  color: var(--tone-rose);
}

.summary-card__hint {
  font-size: var(--font-size-caption);
  color: var(--color-text-tertiary);
  line-height: var(--line-height-normal);
  word-break: break-word;
}

/* -------------------------------------------------------------------------- */
/* Metric grid                                                                 */
/* -------------------------------------------------------------------------- */

.metric-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: var(--space-3);
}

.metric {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: var(--space-2) var(--space-3);
  border-radius: var(--radius-md);
  background: rgba(255, 255, 255, 0.5);
  border: 1px solid var(--color-border-hairline);
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

/* -------------------------------------------------------------------------- */
/* Compact rows                                                                */
/* -------------------------------------------------------------------------- */

.row-list {
  list-style: none;
  display: flex;
  flex-direction: column;
  margin: 0;
  padding: 0;
  transition: opacity var(--duration-fast) var(--ease-smooth);
}

.row-list.is-loading {
  opacity: 0.55;
}

.row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-4);
  padding: var(--space-3) var(--space-2);
  border-bottom: 1px solid var(--color-border-hairline);
  min-width: 0;
}

.row:last-child {
  border-bottom: none;
}

.row--compact {
  padding: var(--space-2);
}

.row__main {
  display: flex;
  flex-direction: column;
  gap: 3px;
  min-width: 0;
  flex: 1;
}

.row__title {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-wrap: wrap;
  font-size: var(--font-size-callout);
  font-weight: 600;
  color: var(--color-text-primary);
  line-height: var(--line-height-snug);
  overflow-wrap: anywhere;
}

.row__id {
  font-family: 'SF Mono', 'Menlo', 'Monaco', 'Courier New', monospace;
  font-size: 11px;
  font-weight: 500;
  color: var(--color-text-tertiary);
}

.row__mono {
  font-family: 'SF Mono', 'Menlo', 'Monaco', 'Courier New', monospace;
  font-size: var(--font-size-caption);
  letter-spacing: -0.01em;
}

.row__meta {
  font-size: var(--font-size-caption);
  color: var(--color-text-secondary);
  line-height: var(--line-height-normal);
  overflow-wrap: anywhere;
}

.row__detail {
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
  overflow: hidden;
  font-size: var(--font-size-caption);
  color: var(--color-text-tertiary);
  line-height: var(--line-height-normal);
  overflow-wrap: anywhere;
}

/* -------------------------------------------------------------------------- */
/* Meters and bars                                                             */
/* -------------------------------------------------------------------------- */

.meter {
  width: 100%;
  height: 6px;
  margin-top: 4px;
  border-radius: var(--radius-pill);
  background: var(--tone-ink-bg);
  overflow: hidden;
}

.meter__fill {
  display: block;
  height: 100%;
  border-radius: var(--radius-pill);
  background: linear-gradient(90deg, #409cff 0%, #0071e3 100%);
  transition: width var(--duration-base) var(--ease-smooth);
}

.meter__fill--warn {
  background: linear-gradient(90deg, #ffb340 0%, #ff9f0a 100%);
}

.meter__fill--danger {
  background: linear-gradient(90deg, #ff6961 0%, #ff3b30 100%);
}

.bar {
  width: 100%;
  height: 4px;
  margin-top: 2px;
  border-radius: var(--radius-pill);
  background: var(--tone-ink-bg);
  overflow: hidden;
}

.bar__fill {
  display: block;
  height: 100%;
  border-radius: var(--radius-pill);
  background: linear-gradient(90deg, rgba(64, 156, 255, 0.72) 0%, #0071e3 100%);
}

/* -------------------------------------------------------------------------- */
/* Pager                                                                       */
/* -------------------------------------------------------------------------- */

.pager {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  flex-wrap: wrap;
  margin-top: var(--space-3);
  --surface-start-opacity: 0.55;
  --surface-travel: 3px;
  animation: surface-enter-keyframes 230ms var(--ease-settle) backwards;
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
/* Transitions and responsive                                                  */
/* -------------------------------------------------------------------------- */

.tab-fade-enter-active,
.tab-fade-leave-active {
  transition:
    opacity 160ms var(--ease-smooth),
    transform 160ms var(--ease-smooth);
}

.tab-fade-enter-from {
  opacity: 0;
  transform: translateY(6px);
}

.tab-fade-leave-to {
  opacity: 0;
  transform: translateY(-4px);
}

@media (max-width: 640px) {
  .ai-gateway {
    padding: var(--space-5) var(--space-4) var(--space-9);
  }

  .ai-gateway__title {
    font-size: 26px;
  }

  .panel {
    padding: var(--space-4);
    border-radius: var(--radius-lg);
  }

  .summary-grid {
    grid-template-columns: 1fr;
  }

  .metric-grid {
    grid-template-columns: 1fr;
  }

  .row {
    flex-direction: column;
    gap: var(--space-2);
  }

  .row > .badge {
    align-self: flex-start;
  }

  .pager {
    flex-direction: column;
    align-items: flex-start;
  }

  .day-switch {
    width: 100%;
    justify-content: space-between;
  }
}

@media (prefers-reduced-motion: reduce) {
  .ai-gateway__back,
  .ghost-button,
  .primary-button,
  .meter__fill,
  .row-list,
  .tab-fade-enter-active,
  .tab-fade-leave-active {
    transition: none;
  }

  .spinner,
  .pager {
    animation: none;
  }

  .ai-gateway__back:hover,
  .ghost-button:hover:not(:disabled),
  .primary-button:hover:not(:disabled) {
    transform: none;
  }
}

@supports not (backdrop-filter: blur(14px)) {
  .panel,
  .ai-gateway__back,
  .ai-gateway__header-status,
  .summary-card,
  .sub-panel,
  .state-block {
    background: rgba(255, 255, 255, 0.92);
  }

  .ghost-button {
    background: rgba(255, 255, 255, 0.94);
  }

  .primary-button {
    background: #0071e3;
  }

  .day-switch {
    background: rgba(255, 255, 255, 0.92);
  }
}
</style>
