<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  disableMaintenance,
  enableMaintenance,
  fetchBackupProgress,
  fetchDatabaseStatus,
  fetchHostSnapshot,
  fetchMaintenanceState,
  fetchRetentionPreview,
  fetchRetentionProgress
} from '@/shared/api/system'
import type {
  BackupSnapshot,
  DatabaseStatus,
  HostSnapshot,
  MaintenanceState,
  RetentionPreview,
  RetentionProgress
} from '@/types/domain'
import { ApiError } from '@/shared/http/errors'
import { formatDateTime } from '@/shared/lib/format'
import { useUiStore } from '@/shared/stores/ui'
import ZAlert from '@/shared/ui/ZAlert.vue'
import ZBadge from '@/shared/ui/ZBadge.vue'
import ZButton from '@/shared/ui/ZButton.vue'
import ZCard from '@/shared/ui/ZCard.vue'
import ZField from '@/shared/ui/ZField.vue'
import ZIcon from '@/shared/ui/ZIcon.vue'
import ZModal from '@/shared/ui/ZModal.vue'
import ZProgress from '@/shared/ui/ZProgress.vue'
import ZSkeleton from '@/shared/ui/ZSkeleton.vue'

const { t } = useI18n()
const ui = useUiStore()

const loading = ref(true)
const refreshing = ref(false)
const updatedAt = ref('')

const maintenance = ref<MaintenanceState | null>(null)
const maintenanceError = ref('')
const saving = ref(false)
const enableOpen = ref(false)
const disableOpen = ref(false)
const reason = ref('')
const announcement = ref('')
const reasonError = ref('')

const db = ref<DatabaseStatus | null>(null)
const dbStatus = ref<'ok' | 'degraded'>('ok')
const dbError = ref('')

const host = ref<HostSnapshot | null>(null)
const hostError = ref('')

const retention = ref<RetentionProgress | null>(null)
const backup = ref<BackupSnapshot | null>(null)
const jobsError = ref('')

const previewOpen = ref(false)
const preview = ref<RetentionPreview | null>(null)
const previewLoading = ref(false)
const previewError = ref('')

const RETENTION_TABLES = [
  'app_logs',
  'export_tasks',
  'ai_call_logs',
  'ai_agent_events',
  'ai_prompt_audit'
] as const

type RetentionTableKey = (typeof RETENTION_TABLES)[number]

function messageOf(cause: unknown): string {
  return cause instanceof ApiError ? cause.message : t('common.error')
}

async function loadMaintenance() {
  maintenanceError.value = ''
  try {
    maintenance.value = await fetchMaintenanceState()
  } catch (cause) {
    maintenanceError.value = messageOf(cause)
  }
}

async function loadDatabase() {
  dbError.value = ''
  try {
    const result = await fetchDatabaseStatus()
    db.value = result.data
    dbStatus.value = result.status
  } catch (cause) {
    db.value = null
    dbError.value = messageOf(cause)
  }
}

async function loadHost() {
  hostError.value = ''
  try {
    host.value = await fetchHostSnapshot()
  } catch (cause) {
    host.value = null
    hostError.value = messageOf(cause)
  }
}

async function loadJobs() {
  jobsError.value = ''
  try {
    const [retentionResult, backupResult] = await Promise.all([
      fetchRetentionProgress(),
      fetchBackupProgress()
    ])
    retention.value = retentionResult
    backup.value = backupResult
  } catch (cause) {
    jobsError.value = messageOf(cause)
  }
}

async function loadAll() {
  refreshing.value = true
  await Promise.all([loadMaintenance(), loadDatabase(), loadHost(), loadJobs()])
  updatedAt.value = new Date().toISOString()
  refreshing.value = false
  loading.value = false
}

onMounted(loadAll)

/* ---------------------------- 维护模式 ---------------------------- */

function openEnable() {
  reason.value = ''
  announcement.value = ''
  reasonError.value = ''
  enableOpen.value = true
}

function openDisable() {
  reason.value = ''
  reasonError.value = ''
  disableOpen.value = true
}

async function submitEnable() {
  if (saving.value) return
  if (!reason.value.trim()) {
    reasonError.value = t('server.maintenance.reasonRequired')
    return
  }
  saving.value = true
  try {
    maintenance.value = await enableMaintenance({
      reason: reason.value.trim(),
      announcement: announcement.value.trim() || undefined
    })
    enableOpen.value = false
    ui.notify(t('server.maintenance.enabledDone'), 'success')
  } catch (cause) {
    ui.notify(messageOf(cause), 'danger')
  } finally {
    saving.value = false
  }
}

async function submitDisable() {
  if (saving.value) return
  saving.value = true
  try {
    const trimmed = reason.value.trim()
    maintenance.value = await disableMaintenance(trimmed ? { reason: trimmed } : undefined)
    disableOpen.value = false
    ui.notify(t('server.maintenance.disabledDone'), 'success')
  } catch (cause) {
    ui.notify(messageOf(cause), 'danger')
  } finally {
    saving.value = false
  }
}

/* ---------------------------- 清理预览 ---------------------------- */

const previewRows = computed(() => {
  const data = preview.value
  if (!data) return []
  return RETENTION_TABLES.map((key) => ({ key, ...data[key] }))
})

async function loadPreview() {
  previewLoading.value = true
  previewError.value = ''
  try {
    preview.value = await fetchRetentionPreview()
  } catch (cause) {
    preview.value = null
    previewError.value = messageOf(cause)
  } finally {
    previewLoading.value = false
  }
}

function openPreview() {
  previewOpen.value = true
  if (!preview.value && !previewLoading.value) void loadPreview()
}

/* ----------------------------- 派生数据 ---------------------------- */

const tables = computed(() =>
  [...(db.value?.storage.tables.items ?? [])]
    .sort((left, right) => right.total_bytes - left.total_bytes)
    .slice(0, 8)
)

const tablespaces = computed(() => db.value?.storage.tablespaces.items ?? [])
const loadMetrics = computed(() => db.value?.load.metrics ?? [])
const warnings = computed(() => db.value?.warnings ?? [])

const pool = computed(() => host.value?.pool ?? db.value?.load.connection_pool ?? null)

const poolText = computed(() => {
  const item = pool.value
  const used = item?.checked_out ?? item?.checkedout
  if (used === undefined || item?.capacity === undefined) return '—'
  return t('server.database.poolUsage', { used, capacity: item.capacity })
})

const cpuBusy = computed(() => {
  const idle = host.value?.host.cpu.idle_pct
  return idle === null || idle === undefined ? null : Math.max(0, Math.min(100, 100 - idle))
})

const memPercent = computed(() => {
  const mem = host.value?.host.mem
  if (!mem?.total_mb || mem.used_mb === null || mem.used_mb === undefined) return null
  return (mem.used_mb / mem.total_mb) * 100
})

const loadAverageText = computed(() => {
  const load = host.value?.host.load
  if (!load) return '—'
  return [load['1m'], load['5m'], load['15m']]
    .map((value) => (value === null || value === undefined ? '—' : decimal(value, 2)))
    .join(' / ')
})

const topRoutes = computed(() => (host.value?.api.items ?? []).slice(0, 8))

const retentionCells = computed(() => {
  const progress = retention.value?.progress
  if (!progress) return []
  return RETENTION_TABLES.map((key) => {
    const cell = progress[key]
    if (typeof cell !== 'object' || cell === null) return null
    return { key, deleted: cell.deleted, remaining: cell.remaining }
  }).filter(
    (item): item is { key: RetentionTableKey; deleted: number; remaining: number } =>
      item !== null
  )
})

function progressNumber(key: string): number | null {
  const value = retention.value?.progress?.[key]
  return typeof value === 'number' ? value : null
}

const batchDone = computed(() => progressNumber('batches') ?? 0)
const batchTotal = computed(() => progressNumber('max_batches'))

function jobLabel(state: string): string {
  if (state === 'ok' || state === 'done') return t('server.jobs.done')
  if (state === 'running' || state === 'unsettled') return t('server.jobs.running')
  if (state === 'failed') return t('server.jobs.failed')
  return t('server.jobs.idle')
}

function jobTone(state: string): 'success' | 'warning' | 'danger' | 'neutral' {
  if (state === 'ok' || state === 'done') return 'success'
  if (state === 'running' || state === 'unsettled') return 'warning'
  if (state === 'failed') return 'danger'
  return 'neutral'
}

/* ----------------------------- 格式化 ----------------------------- */

function decimal(value: number, digits = 1): string {
  const factor = 10 ** digits
  return String(Math.round(value * factor) / factor)
}

function numberText(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—'
  return new Intl.NumberFormat().format(Math.round(value))
}

function metricText(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—'
  return new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 }).format(value)
}

function percentText(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—'
  return `${decimal(value, 1)}%`
}

function bytes(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let size = value
  let index = 0
  while (size >= 1024 && index < units.length - 1) {
    size /= 1024
    index += 1
  }
  const rounded = index === 0 || size >= 100 ? Math.round(size) : decimal(size, 1)
  return `${rounded} ${units[index]}`
}

function megabytes(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—'
  return value >= 1024 ? `${decimal(value / 1024, 1)} GB` : `${decimal(value, 0)} MB`
}

function rateText(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—'
  return `${decimal(value, 1)} KB/s`
}

function timeText(value: string | null | undefined): string {
  return formatDateTime(value ?? undefined)
}

function epochText(value: number | null | undefined): string {
  if (!value) return '—'
  return formatDateTime(new Date(value * 1000).toISOString())
}
</script>

<template>
  <div class="page">
    <header class="head">
      <div>
        <p class="eyebrow">{{ t('server.eyebrow') }}</p>
        <h1 class="title">{{ t('server.title') }}</h1>
        <p class="sub">{{ t('server.subtitle') }}</p>
      </div>
      <div class="head-side">
        <span v-if="updatedAt" class="stamp num">
          {{ t('server.updatedAt', { time: timeText(updatedAt) }) }}
        </span>
        <ZButton
          size="sm"
          variant="secondary"
          icon="refresh"
          :disabled="refreshing"
          @click="loadAll"
        >
          {{ t('server.refresh') }}
        </ZButton>
      </div>
    </header>

    <p class="note">{{ t('server.readOnly') }}</p>

    <ZCard padding="lg">
      <div class="card-head">
        <div class="card-title">
          <span class="glyph"><ZIcon name="shield" :size="18" /></span>
          <div>
            <h2>{{ t('server.maintenance.title') }}</h2>
            <p class="card-desc">{{ t('server.maintenance.desc') }}</p>
          </div>
        </div>
        <ZBadge :tone="maintenance?.enabled ? 'warning' : 'success'" dot>
          {{
            maintenance?.enabled
              ? t('server.maintenance.enabled')
              : t('server.maintenance.disabled')
          }}
        </ZBadge>
      </div>

      <ZAlert v-if="maintenanceError" tone="danger" :description="maintenanceError" />

      <dl v-else-if="maintenance" class="facts">
        <div>
          <dt>{{ t('server.maintenance.since') }}</dt>
          <dd class="num">{{ epochText(maintenance.since) }}</dd>
        </div>
        <div>
          <dt>{{ t('server.maintenance.announcement') }}</dt>
          <dd>{{ maintenance.announcement || t('server.maintenance.noAnnouncement') }}</dd>
        </div>
      </dl>

      <div class="actions">
        <ZButton
          v-if="!maintenance?.enabled"
          variant="danger"
          icon="alert"
          :disabled="!maintenance"
          @click="openEnable"
        >
          {{ t('server.maintenance.enable') }}
        </ZButton>
        <ZButton v-else variant="primary" icon="check" :disabled="saving" @click="openDisable">
          {{ t('server.maintenance.disable') }}
        </ZButton>
      </div>
    </ZCard>

    <ZCard padding="lg">
        <div class="card-head">
          <div class="card-title">
            <span class="glyph"><ZIcon name="server" :size="18" /></span>
            <div>
              <h2>{{ t('server.database.title') }}</h2>
              <p class="card-desc">{{ t('server.database.desc') }}</p>
            </div>
          </div>
          <div class="badges">
            <ZBadge v-if="db?.cache" tone="neutral">
              {{ t('server.database.cached', { n: Math.round(db.cache.age_seconds) }) }}
            </ZBadge>
            <ZBadge v-if="db" :tone="dbStatus === 'ok' ? 'success' : 'warning'" dot>
              {{
                dbStatus === 'ok'
                  ? t('server.database.ok')
                  : t('server.database.degraded')
              }}
            </ZBadge>
          </div>
        </div>

        <ZAlert v-if="dbError" tone="danger" :description="dbError" />

        <div v-else-if="loading && !db" class="skeletons">
          <ZSkeleton height="4.5rem" />
          <ZSkeleton height="7rem" />
        </div>

        <template v-else-if="db">
          <div class="stats">
            <div class="stat">
              <span class="stat-label">{{ t('server.database.tables') }}</span>
              <strong class="num">{{ numberText(db.storage.tables.count) }}</strong>
            </div>
            <div class="stat">
              <span class="stat-label">{{ t('server.database.totalSize') }}</span>
              <strong class="num">{{ bytes(db.storage.tables.total_bytes) }}</strong>
            </div>
            <div class="stat">
              <span class="stat-label">{{ t('server.database.pool') }}</span>
              <strong class="num">{{ poolText }}</strong>
            </div>
          </div>

          <section class="block">
            <h3 class="block-title">{{ t('server.database.tablespaces') }}</h3>
            <ul v-if="tablespaces.length" class="spaces">
              <li v-for="space in tablespaces" :key="space.name" class="space">
                <div class="space-head">
                  <span class="space-name">{{ space.name }}</span>
                  <span class="num muted">{{ percentText(space.used_percent) }}</span>
                </div>
                <ZProgress
                  :value="space.used_percent ?? 0"
                  :tone="(space.used_percent ?? 0) >= 85 ? 'accent' : 'info'"
                  :height="6"
                />
                <span class="space-meta num">
                  {{ bytes(space.used_bytes) }} / {{ bytes(space.max_bytes) }}
                </span>
              </li>
            </ul>
            <p v-else class="muted small">{{ t('server.database.noTablespaces') }}</p>
          </section>

          <section class="block">
            <h3 class="block-title">{{ t('server.database.tables') }}</h3>
            <div v-if="tables.length" class="scroll">
              <table class="table">
                <thead>
                  <tr>
                    <th>{{ t('server.database.tableName') }}</th>
                    <th class="right">{{ t('server.database.estimatedRows') }}</th>
                    <th class="right">{{ t('server.database.size') }}</th>
                    <th class="right">{{ t('server.database.analyzed') }}</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="row in tables" :key="row.name">
                    <td class="mono">{{ row.name }}</td>
                    <td class="right num">{{ numberText(row.estimated_rows) }}</td>
                    <td class="right num">{{ bytes(row.total_bytes) }}</td>
                    <td class="right num">{{ timeText(row.last_analyzed) }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
            <p v-else class="muted small">{{ t('server.database.noTables') }}</p>
          </section>

          <section class="block">
            <h3 class="block-title">{{ t('server.database.load') }}</h3>
            <ul v-if="loadMetrics.length" class="chips">
              <li v-for="item in loadMetrics" :key="item.name" class="chip">
                <span class="chip-label">{{ item.name }}</span>
                <strong class="num">{{ metricText(item.value) }}</strong>
                <span class="chip-unit">{{ item.unit }}</span>
              </li>
            </ul>
            <p v-else class="muted small">{{ t('server.database.noLoad') }}</p>
          </section>

          <details v-if="warnings.length" class="notes">
            <summary>{{ t('server.database.warnings') }}</summary>
            <ul>
              <li v-for="warning in warnings" :key="warning">{{ warning }}</li>
            </ul>
          </details>
        </template>
      </ZCard>

    <div class="grid">
      <ZCard padding="lg">
        <div class="card-head">
          <div class="card-title">
            <span class="glyph"><ZIcon name="activity" :size="18" /></span>
            <div>
              <h2>{{ t('server.host.title') }}</h2>
              <p class="card-desc">{{ t('server.host.desc') }}</p>
            </div>
          </div>
          <ZBadge v-if="host" tone="neutral">
            {{ t('server.host.apiWindow', { n: host.api.window_seconds }) }}
          </ZBadge>
        </div>

        <ZAlert v-if="hostError" tone="danger" :description="hostError" />

        <div v-else-if="loading && !host" class="skeletons">
          <ZSkeleton height="4.5rem" />
          <ZSkeleton height="4.5rem" />
        </div>

        <template v-else-if="host">
          <div class="gauges">
            <div class="gauge">
              <div class="gauge-head">
                <span>{{ t('server.host.cpu') }}</span>
                <strong class="num">{{ percentText(cpuBusy) }}</strong>
              </div>
              <ZProgress :value="cpuBusy ?? 0" tone="accent" :height="6" />
              <small class="num muted">
                user {{ percentText(host.host.cpu.user_pct) }} · sys
                {{ percentText(host.host.cpu.system_pct) }} · iowait
                {{ percentText(host.host.cpu.iowait_pct) }}
              </small>
            </div>

            <div class="gauge">
              <div class="gauge-head">
                <span>{{ t('server.host.memory') }}</span>
                <strong class="num">{{ percentText(memPercent) }}</strong>
              </div>
              <ZProgress :value="memPercent ?? 0" tone="info" :height="6" />
              <small class="num muted">
                {{
                  t('server.host.used', {
                    used: megabytes(host.host.mem.used_mb),
                    total: megabytes(host.host.mem.total_mb)
                  })
                }}
              </small>
            </div>
          </div>

          <dl class="facts facts-wide">
            <div>
              <dt>{{ t('server.host.loadAvg') }}</dt>
              <dd class="num">{{ loadAverageText }}</dd>
            </div>
            <div>
              <dt>{{ t('server.host.disk') }}</dt>
              <dd class="num">
                {{ percentText(host.host.disk.used_pct) }} ·
                {{ t('server.host.free', { n: numberText(host.host.disk.free_gb) }) }}
              </dd>
            </div>
            <div>
              <dt>{{ t('server.host.net') }}</dt>
              <dd class="num">
                ↓ {{ rateText(host.host.net.rx_kbps) }} · ↑
                {{ rateText(host.host.net.tx_kbps) }}
              </dd>
            </div>
            <div>
              <dt>{{ t('server.host.diskio') }}</dt>
              <dd class="num">
                R {{ rateText(host.host.diskio.read_kbps) }} · W
                {{ rateText(host.host.diskio.write_kbps) }}
              </dd>
            </div>
            <div>
              <dt>{{ t('server.host.process') }}</dt>
              <dd class="num">
                {{ t('server.host.rss', { n: numberText(host.host.proc.rss_mb) }) }} ·
                {{ t('server.host.uptime', { n: numberText(host.host.proc.uptime_hours) }) }}
              </dd>
            </div>
          </dl>

          <section class="block">
            <h3 class="block-title">{{ t('server.host.api') }}</h3>
            <div v-if="topRoutes.length" class="scroll">
              <table class="table">
                <thead>
                  <tr>
                    <th>{{ t('server.host.route') }}</th>
                    <th class="right">{{ t('server.host.count') }}</th>
                    <th class="right">{{ t('server.host.avg') }}</th>
                    <th class="right">{{ t('server.host.p95') }}</th>
                    <th class="right">{{ t('server.host.errors') }}</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="row in topRoutes" :key="`${row.method}-${row.route}`">
                    <td class="route">
                      <span class="method">{{ row.method }}</span>
                      <span class="mono">{{ row.route }}</span>
                    </td>
                    <td class="right num">{{ numberText(row.count) }}</td>
                    <td class="right num">{{ numberText(row.avg_ms) }} ms</td>
                    <td class="right num">{{ numberText(row.p95_ms) }} ms</td>
                    <td class="right num">{{ numberText(row.err_count) }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
            <p v-else class="muted small">{{ t('server.host.noApi') }}</p>
          </section>
        </template>
      </ZCard>

      <ZCard padding="lg">
        <div class="card-head">
          <div class="card-title">
            <span class="glyph"><ZIcon name="clock" :size="18" /></span>
          <div>
            <h2>{{ t('server.jobs.title') }}</h2>
            <p class="card-desc">{{ t('server.jobs.desc') }}</p>
          </div>
        </div>
        <ZButton
          size="sm"
          variant="secondary"
          icon="eye"
          :disabled="previewLoading"
          @click="openPreview"
        >
          {{ t('server.jobs.preview') }}
        </ZButton>
      </div>

      <ZAlert v-if="jobsError" tone="danger" :description="jobsError" />

      <div v-else class="jobs">
        <section class="job">
          <div class="job-head">
            <h3>{{ t('server.jobs.retention') }}</h3>
            <ZBadge :tone="retention?.running ? 'warning' : jobTone(retention?.phase ?? '')" dot>
              {{
                retention?.running
                  ? t('server.jobs.running')
                  : jobLabel(retention?.phase ?? '')
              }}
            </ZBadge>
          </div>

          <template v-if="retention && (retention.job_id || retention.phase)">
            <dl class="facts facts-wide">
              <div v-if="retention.phase">
                <dt>{{ t('server.jobs.phase') }}</dt>
                <dd class="mono">{{ retention.phase }}</dd>
              </div>
              <div v-if="retention.started_at">
                <dt>{{ t('server.jobs.startedAt') }}</dt>
                <dd class="num">{{ timeText(retention.started_at) }}</dd>
              </div>
              <div v-if="retention.finished_at">
                <dt>{{ t('server.jobs.finishedAt') }}</dt>
                <dd class="num">{{ timeText(retention.finished_at) }}</dd>
              </div>
            </dl>

            <p v-if="batchTotal !== null" class="muted small">
              {{ t('server.jobs.batches', { done: batchDone, max: batchTotal }) }}
            </p>

            <ul v-if="retentionCells.length" class="cells">
              <li v-for="cell in retentionCells" :key="cell.key">
                <span class="mono">{{ cell.key }}</span>
                <span class="num">{{ t('server.jobs.deleted', { n: numberText(cell.deleted) }) }}</span>
              </li>
            </ul>

            <p v-if="retention.error" class="error-text">{{ retention.error }}</p>
          </template>
          <p v-else class="muted small">{{ t('server.jobs.noJob') }}</p>
        </section>

        <section class="job">
          <div class="job-head">
            <h3>{{ t('server.jobs.backup') }}</h3>
            <ZBadge :tone="jobTone(backup?.state ?? 'idle')" dot>
              {{ jobLabel(backup?.state ?? 'idle') }}
            </ZBadge>
          </div>

          <template v-if="backup">
            <p class="job-message">{{ backup.message }}</p>
            <dl v-if="backup.state === 'running' || backup.state === 'ok'" class="facts facts-wide">
              <div v-if="backup.tables_total !== null">
                <dt>{{ t('server.jobs.tables', { done: backup.tables_done, total: backup.tables_total }) }}</dt>
                <dd class="mono">{{ backup.current_table || '—' }}</dd>
              </div>
              <div>
                <dt>{{ t('server.jobs.rowsLoaded', { n: numberText(backup.rows_loaded) }) }}</dt>
                <dd v-if="backup.elapsed_seconds !== null" class="num">
                  {{ t('server.jobs.elapsed', { n: numberText(backup.elapsed_seconds) }) }}
                </dd>
              </div>
            </dl>
          </template>
          <p v-else class="muted small">{{ t('server.jobs.noJob') }}</p>
        </section>
      </div>
      </ZCard>
    </div>

    <ZModal v-model="enableOpen" :title="t('server.maintenance.enableTitle')" size="md">
      <div class="sheet">
        <p class="sheet-desc">{{ t('server.maintenance.enableDesc') }}</p>
        <ZField
          v-model="reason"
          :label="t('server.maintenance.reason')"
          :placeholder="t('server.maintenance.reasonPlaceholder')"
          :error="reasonError"
        />
        <ZField
          v-model="announcement"
          :label="t('server.maintenance.announcementLabel')"
          :placeholder="t('server.maintenance.announcementPlaceholder')"
        />
        <ZAlert tone="warning" :description="t('server.maintenance.desc')" />
      </div>
      <template #footer>
        <ZButton variant="ghost" @click="enableOpen = false">{{ t('common.cancel') }}</ZButton>
        <ZButton variant="danger" :disabled="saving" @click="submitEnable">
          {{ t('server.maintenance.confirmEnable') }}
        </ZButton>
      </template>
    </ZModal>

    <ZModal v-model="disableOpen" :title="t('server.maintenance.disableTitle')" size="md">
      <div class="sheet">
        <ZField
          v-model="reason"
          :label="t('server.maintenance.reason')"
          :placeholder="t('server.maintenance.reasonPlaceholder')"
        />
      </div>
      <template #footer>
        <ZButton variant="ghost" @click="disableOpen = false">{{ t('common.cancel') }}</ZButton>
        <ZButton variant="primary" :disabled="saving" @click="submitDisable">
          {{ t('server.maintenance.confirmDisable') }}
        </ZButton>
      </template>
    </ZModal>

    <ZModal v-model="previewOpen" :title="t('server.jobs.previewTitle')" size="md">
      <div class="preview">
        <p class="sheet-desc">{{ t('server.jobs.previewHint') }}</p>

        <p v-if="previewError" class="error-text">{{ previewError }}</p>

        <div v-else-if="preview" class="scroll">
          <table class="table">
            <thead>
              <tr>
                <th>{{ t('server.database.tableName') }}</th>
                <th>{{ t('server.jobs.cutoff') }}</th>
                <th class="right">{{ t('server.jobs.deletable') }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in previewRows" :key="row.key">
                <td class="mono">{{ row.key }}</td>
                <td class="num">{{ timeText(row.cutoff) }}</td>
                <td class="right num">
                  {{
                    row.deletable === null
                      ? t('server.jobs.unavailable')
                      : numberText(row.deletable)
                  }}
                </td>
              </tr>
            </tbody>
          </table>
        </div>

        <p v-else class="muted small">{{ t('common.loading') }}</p>
      </div>
      <template #footer>
        <ZButton variant="ghost" @click="previewOpen = false">{{ t('common.close') }}</ZButton>
        <ZButton
          variant="secondary"
          icon="refresh"
          :disabled="previewLoading"
          @click="loadPreview"
        >
          {{ t('server.refresh') }}
        </ZButton>
      </template>
    </ZModal>
  </div>
</template>

<style scoped>
.page {
  display: flex;
  flex-direction: column;
  gap: var(--space-5);
  min-width: 0;
}

.head {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: var(--space-4);
  flex-wrap: wrap;
}

.eyebrow {
  margin-bottom: var(--space-2);
  font-size: var(--text-2xs);
  letter-spacing: 0.18em;
  color: var(--ink-faint);
}

.title {
  font-size: var(--text-3xl);
  font-weight: 500;
}

.sub {
  margin-top: var(--space-2);
  color: var(--ink-muted);
}

.head-side {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  flex-wrap: wrap;
}

.stamp {
  font-size: var(--text-xs);
  color: var(--ink-faint);
}

.note {
  font-size: var(--text-xs);
  color: var(--ink-faint);
  line-height: 1.6;
  padding-bottom: var(--space-3);
  border-bottom: 1px solid color-mix(in srgb, var(--line) 55%, transparent);
}

.grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--space-5);
  align-items: stretch;
}

.card-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-4);
  flex-wrap: wrap;
  margin-bottom: var(--space-4);
}

.card-title {
  display: flex;
  gap: var(--space-3);
  align-items: flex-start;
}

.glyph {
  display: grid;
  place-items: center;
  width: 38px;
  height: 38px;
  flex: none;
  border-radius: var(--radius-md);
  color: var(--accent-ink);
  background: var(--accent-soft);
  border: 1px solid color-mix(in srgb, var(--accent) 20%, transparent);
}

.card-title h2 {
  font-size: var(--text-lg);
  font-weight: 500;
}

.card-desc {
  margin-top: 0.2rem;
  font-size: var(--text-xs);
  color: var(--ink-muted);
  line-height: 1.6;
  max-width: 42ch;
}

.badges {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-wrap: wrap;
}

.stats {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: var(--space-3);
  padding: var(--space-3) 0 var(--space-4);
}

.stat {
  display: flex;
  flex-direction: column;
  gap: 0.3rem;
  padding: var(--space-3) var(--space-4);
  border-radius: var(--radius-sm);
  background: var(--surface-sunken);
}

.stat-label {
  font-size: var(--text-2xs);
  color: var(--ink-faint);
}

.stat strong {
  font-size: var(--text-lg);
  font-weight: 600;
  color: var(--ink);
}

.block {
  padding-top: var(--space-4);
  border-top: 1px solid color-mix(in srgb, var(--line) 55%, transparent);
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  min-width: 0;
}

.block:first-of-type {
  border-top: none;
}

.block-title {
  font-size: var(--text-xs);
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--ink-faint);
}

.spaces {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: var(--space-3) var(--space-5);
}

.space {
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
}

.space-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--space-3);
  font-size: var(--text-sm);
}

.space-name {
  font-weight: 500;
}

.space-meta {
  font-size: var(--text-2xs);
  color: var(--ink-faint);
}

.scroll {
  overflow-x: auto;
  -webkit-overflow-scrolling: touch;
  min-width: 0;
  max-width: 100%;
}

.table {
  width: 100%;
  min-width: 460px;
  border-collapse: collapse;
  font-size: var(--text-sm);
}

.table th {
  text-align: left;
  padding: 0.45rem 0.6rem;
  font-size: var(--text-2xs);
  font-weight: 600;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--ink-faint);
  border-bottom: 1px solid var(--line);
  white-space: nowrap;
}

.table td {
  padding: 0.55rem 0.6rem;
  border-bottom: 1px solid color-mix(in srgb, var(--line) 45%, transparent);
  color: var(--ink-soft);
  white-space: nowrap;
}

.table tbody tr:last-child td {
  border-bottom: none;
}

.table .right {
  text-align: right;
}

.route {
  display: flex;
  align-items: center;
  gap: 0.45rem;
}

.method {
  font-size: var(--text-2xs);
  font-weight: 600;
  letter-spacing: 0.04em;
  color: var(--accent-ink);
  background: var(--accent-soft);
  padding: 0.05rem 0.35rem;
  border-radius: var(--radius-xs, 4px);
}

.chips {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
}

.chip {
  display: inline-flex;
  align-items: baseline;
  gap: 0.4rem;
  padding: 0.45rem 0.7rem;
  border-radius: var(--radius-sm);
  background: var(--surface-sunken);
  font-size: var(--text-sm);
}

.chip-label {
  color: var(--ink-muted);
}

.chip-unit {
  font-size: var(--text-2xs);
  color: var(--ink-faint);
}

.notes {
  margin-top: var(--space-4);
  padding-top: var(--space-3);
  border-top: 1px solid color-mix(in srgb, var(--line) 55%, transparent);
  font-size: var(--text-xs);
  color: var(--ink-muted);
}

.notes summary {
  cursor: pointer;
  color: var(--ink-soft);
}

.notes ul {
  margin: var(--space-2) 0 0;
  padding-left: 1.1rem;
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
  line-height: 1.6;
}

.gauges {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--space-4);
}

.gauge {
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
}

.gauge-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--space-3);
  font-size: var(--text-sm);
}

.facts {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
  gap: var(--space-3);
  margin: 0;
}

.facts-wide {
  margin-top: var(--space-4);
  padding-top: var(--space-4);
  border-top: 1px solid color-mix(in srgb, var(--line) 55%, transparent);
}

.facts dt {
  font-size: var(--text-2xs);
  color: var(--ink-faint);
  letter-spacing: 0.04em;
}

.facts dd {
  margin: 0.25rem 0 0;
  font-size: var(--text-sm);
  color: var(--ink-soft);
  overflow-wrap: anywhere;
}

.actions {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  flex-wrap: wrap;
  margin-top: var(--space-4);
}

.jobs {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.job {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding: var(--space-4);
  border-radius: var(--radius-md);
  background: var(--surface-sunken);
}

.job-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
}

.job-head h3 {
  font-size: var(--text-sm);
  font-weight: 600;
}

.job-message {
  font-size: var(--text-sm);
  color: var(--ink-soft);
  line-height: 1.6;
}

.cells {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
  font-size: var(--text-xs);
}

.cells li {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--space-3);
  color: var(--ink-muted);
}

.sheet {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.sheet-desc {
  font-size: var(--text-sm);
  color: var(--ink-muted);
  line-height: 1.6;
}

.preview {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.skeletons {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.muted {
  color: var(--ink-muted);
}

.small {
  font-size: var(--text-xs);
}

.error-text {
  font-size: var(--text-sm);
  color: var(--c-danger);
  line-height: 1.6;
}

@media (max-width: 980px) {
  .grid {
    grid-template-columns: minmax(0, 1fr);
  }
}

@media (max-width: 720px) {
  .stats {
    grid-template-columns: 1fr;
  }

  .gauges,
  .jobs {
    grid-template-columns: 1fr;
  }

  .head {
    align-items: flex-start;
  }
}
</style>
