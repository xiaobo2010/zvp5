<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  ElButton,
  ElDatePicker,
  ElEmpty,
  ElIcon,
  ElInput,
  ElMessage,
  ElOption,
  ElSelect,
  ElTable,
  ElTableColumn,
  ElTag
} from 'element-plus'
import { CopyDocument, Refresh } from '@element-plus/icons-vue'
import ZSelectPerson from '@/components/form/ZSelectPerson.vue'
import ZPagination from '@/components/utils/ZPagination.vue'
import {
  AI_LOG_STATS_DEFAULT_DAYS,
  aiErrorCodeOf,
  aiErrorCopy,
  getAiLogStats,
  listAiLogs,
  toEpochSeconds
} from '@/api/ai'
import type {
  AiLogCapability,
  AiLogKeyScope,
  AiLogRecord,
  AiLogStats,
  AiLogStatus
} from '@/../types/ai'

const { t } = useI18n()

const CAPABILITY_OPTIONS: AiLogCapability[] = ['ocr', 'chat']
const STATUS_OPTIONS: AiLogStatus[] = ['success', 'error', 'timeout', 'rejected', 'busy']
const KEY_SCOPE_OPTIONS: AiLogKeyScope[] = ['personal', 'shared', 'server']
/** 场景是开放集合：这里只列已知的三个，后端/前端新增场景不需要改这里才能筛。 */
const PURPOSE_OPTIONS = ['chat', 'markdown-assistant', 'ocr']
const STATS_DAYS_OPTIONS = [7, 30, 90]

/** 五种状态各自的配色：成功绿、错误红、超时橙、拒绝灰、繁忙橙描边；历史 warning 走橙色。 */
const STATUS_TAG_TYPE: Record<AiLogStatus, 'success' | 'danger' | 'warning' | 'info'> = {
  success: 'success',
  error: 'danger',
  timeout: 'warning',
  rejected: 'info',
  busy: 'warning',
  warning: 'warning'
}

const rows = ref<AiLogRecord[]>([])
const total = ref(0)
const page = ref(1)
const perpage = ref(20)
const loading = ref(false)
const loadFailed = ref(false)

const filters = reactive({
  userOid: '',
  capability: '' as AiLogCapability | '',
  status: '' as AiLogStatus | '',
  keyScope: '' as AiLogKeyScope | '',
  purpose: '',
  provider: '',
  model: '',
  requestId: '',
  range: undefined as [Date, Date] | undefined
})

/** 过滤条件变动后回到第一页：签名串只在取值真的变了时才变。 */
const filterSignature = computed(() =>
  [
    filters.userOid,
    filters.capability,
    filters.status,
    filters.keyScope,
    filters.purpose,
    filters.provider,
    filters.model,
    filters.requestId,
    filters.range?.[0]?.getTime() ?? 0,
    filters.range?.[1]?.getTime() ?? 0
  ].join('|')
)

/** 依次递增的请求序号：慢响应回来时若已有更新的请求，直接丢弃。 */
let loadSeq = 0
let statsSeq = 0

function onUserChange(value: string | string[]) {
  filters.userOid = typeof value === 'string' ? value : ''
}

function resetFilters() {
  filters.userOid = ''
  filters.capability = ''
  filters.status = ''
  filters.keyScope = ''
  filters.purpose = ''
  filters.provider = ''
  filters.model = ''
  filters.requestId = ''
  filters.range = undefined
}

async function load() {
  const seq = ++loadSeq
  loading.value = true
  try {
    const result = await listAiLogs({
      page: page.value,
      perpage: perpage.value,
      user_oid: filters.userOid.trim() || undefined,
      capability: filters.capability || undefined,
      status: filters.status || undefined,
      key_scope: filters.keyScope || undefined,
      purpose: filters.purpose || undefined,
      provider: filters.provider.trim() || undefined,
      model: filters.model.trim() || undefined,
      request_id: filters.requestId.trim() || undefined,
      // 时间边界折成 epoch 秒再传给后端。
      time_start: toEpochSeconds(filters.range?.[0]),
      time_end: toEpochSeconds(filters.range?.[1])
    })
    if (seq !== loadSeq) {
      return
    }
    rows.value = result.rows
    total.value = result.total
    loadFailed.value = false
  } catch (error) {
    if (seq !== loadSeq) {
      return
    }
    rows.value = []
    total.value = 0
    loadFailed.value = true
    const copy = aiErrorCopy(aiErrorCodeOf(error), t)
    ElMessage.error(`${copy.title}：${copy.hint}`)
  } finally {
    if (seq === loadSeq) {
      loading.value = false
    }
  }
}

function onPageChange(nextPage: number, nextSize: number) {
  page.value = nextPage
  perpage.value = nextSize
  void load()
}

watch(filterSignature, () => {
  page.value = 1
  void load()
})

// ---------------------------------------------------------------------------
// 用量概览（/ai/logs/stats）
// ---------------------------------------------------------------------------

const stats = ref<AiLogStats | null>(null)
const statsDays = ref<number>(AI_LOG_STATS_DEFAULT_DAYS)
const statsLoading = ref(false)
const statsFailed = ref(false)

async function loadStats() {
  const seq = ++statsSeq
  statsLoading.value = true
  try {
    const result = await getAiLogStats(statsDays.value)
    if (seq !== statsSeq) {
      return
    }
    stats.value = result
    statsFailed.value = false
  } catch (error) {
    if (seq !== statsSeq) {
      return
    }
    stats.value = null
    statsFailed.value = true
    const copy = aiErrorCopy(aiErrorCodeOf(error), t)
    ElMessage.error(`${copy.title}：${copy.hint}`)
  } finally {
    if (seq === statsSeq) {
      statsLoading.value = false
    }
  }
}

/**
 * 概览卡片上的四个总数。
 *
 * 全部从 ``daily`` 累加而不是再发一次查询：四个分项（按日/服务商/模型/场景）
 * 来自同一个窗口，累加其中一份就不会出现「卡片总数 ≠ 明细合计」。
 */
const statsSummary = computed(() => {
  const daily = stats.value?.daily ?? []
  const calls = daily.reduce((sum, item) => sum + item.calls, 0)
  const errors = daily.reduce((sum, item) => sum + item.errors, 0)
  const totalTokens = daily.reduce((sum, item) => sum + item.total_tokens, 0)
  const latencyWeighted = daily.reduce(
    (sum, item) => sum + (item.avg_latency_ms ?? 0) * item.calls,
    0
  )
  return {
    calls,
    errors,
    totalTokens,
    // 加权平均：按调用数加权，否则「只有一次调用的那天」会和「一千次的那天」等价。
    avgLatency: calls > 0 ? Math.round(latencyWeighted / calls) : null,
    errorRate: calls > 0 ? `${Math.round((errors / calls) * 1000) / 10}%` : '—'
  }
})

const statsDaily = computed(() => stats.value?.daily ?? [])
const statsByProvider = computed(() => stats.value?.by_provider ?? [])
const statsByPurpose = computed(() => stats.value?.by_purpose ?? [])
const statsByUser = computed(() => stats.value?.by_user ?? [])
const statsByStatus = computed(() => stats.value?.by_status ?? [])

watch(statsDays, () => {
  void loadStats()
})

function formatTime(value?: string | null): string {
  if (!value) {
    return '—'
  }
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) {
    return value
  }
  const fill = (n: number) => String(n).padStart(2, '0')
  return `${date.getFullYear()}-${fill(date.getMonth() + 1)}-${fill(date.getDate())} ${fill(date.getHours())}:${fill(date.getMinutes())}:${fill(date.getSeconds())}`
}

function formatMs(value: number | null | undefined, unitKey: string): string {
  return value === null || value === undefined ? '—' : `${value} ${t(unitKey)}`
}

function statusTagType(status: AiLogStatus): 'success' | 'danger' | 'warning' | 'info' {
  return STATUS_TAG_TYPE[status] ?? 'info'
}

function statusTagClass(status: AiLogStatus): string {
  return status === 'busy' ? 'ai-log-status--busy' : ''
}

function capabilityLabel(capability: AiLogCapability): string {
  return capability === 'chat' ? t('ai.logs.capabilityChat') : t('ai.logs.capabilityOcr')
}

function statusLabel(status: AiLogStatus): string {
  const map: Record<AiLogStatus, string> = {
    success: t('ai.logs.statusSuccess'),
    error: t('ai.logs.statusError'),
    timeout: t('ai.logs.statusTimeout'),
    rejected: t('ai.logs.statusRejected'),
    busy: t('ai.logs.statusBusy'),
    // 历史行：管理员旁路内容审核留的痕（不是「调用成功」）
    warning: t('ai.logs.statusAdminBypass')
  }
  return map[status] ?? status
}

/**
 * 角色快照里有没有 admin。
 *
 * 后端 audit.roles_snapshot 存的是排序去重、逗号连接的字符串（`admin,department`）；
 * 兼容早期工具留痕里的 JSON 数组形态（`["admin"]`），顺手把括号引号去掉。
 */
function isAdminSnapshot(snapshot: string | null | undefined): boolean {
  if (!snapshot) {
    return false
  }
  return snapshot
    .replace(/[[\]"]/g, '')
    .split(',')
    .some((role) => role.trim() === 'admin')
}

function keyScopeLabel(scope: AiLogKeyScope): string {
  const map: Record<AiLogKeyScope, string> = {
    personal: t('ai.logs.keyScopePersonal'),
    shared: t('ai.logs.keyScopeShared'),
    server: t('ai.logs.keyScopeServer')
  }
  return map[scope] ?? scope
}

/** 「入 / 出 / 合计」三段；全空说明这次上游没报 usage（计费单位是估算值）。 */
function tokensLabel(row: AiLogRecord): string {
  if (
    row.prompt_tokens === null &&
    row.completion_tokens === null &&
    row.total_tokens === null
  ) {
    return '—'
  }
  return `${row.prompt_tokens ?? '—'} / ${row.completion_tokens ?? '—'} / ${row.total_tokens ?? '—'}`
}

async function copyRequestId(requestId: string) {
  if (!requestId) {
    return
  }
  try {
    await navigator.clipboard.writeText(requestId)
    ElMessage.success(t('ai.common.copied'))
  } catch {
    ElMessage.warning(requestId)
  }
}

onMounted(() => {
  void load()
  void loadStats()
})
</script>

<template>
  <div class="flex flex-col gap-4">
    <section class="yg-card">
      <header class="yg-card__header">
        <div class="flex flex-col gap-1 min-w-0">
          <h2 class="yg-card__title">{{ t('ai.logs.title') }}</h2>
          <p class="yg-text-xs yg-text-tertiary">{{ t('ai.logs.subtitle') }}</p>
        </div>
        <div class="yg-card__extra flex flex-wrap items-center gap-2">
          <ElButton @click="resetFilters">
            {{ t('ai.logs.resetFilters') }}
          </ElButton>
          <ElButton :loading="loading" @click="load">
            <ElIcon class="mr-1"><Refresh /></ElIcon>
            {{ t('ai.common.refresh') }}
          </ElButton>
        </div>
      </header>

      <div class="yg-card__body">
        <div class="grid gap-3 md:grid-cols-2 xl:grid-cols-5">
          <div class="flex flex-col gap-1">
            <span class="yg-text-xs yg-text-tertiary">{{ t('ai.logs.user') }}</span>
            <ZSelectPerson
              :model-value="filters.userOid"
              :filter-start="6"
              full-width
              clearable
              :placeholder="t('ai.logs.userAll')"
              @update:model-value="onUserChange"
            />
          </div>
          <div class="flex flex-col gap-1">
            <span class="yg-text-xs yg-text-tertiary">{{ t('ai.logs.capability') }}</span>
            <ElSelect v-model="filters.capability" class="w-full">
              <ElOption :label="t('ai.logs.capabilityAll')" value="" />
              <ElOption
                v-for="item in CAPABILITY_OPTIONS"
                :key="item"
                :label="capabilityLabel(item)"
                :value="item"
              />
            </ElSelect>
          </div>
          <div class="flex flex-col gap-1">
            <span class="yg-text-xs yg-text-tertiary">{{ t('ai.logs.status') }}</span>
            <ElSelect v-model="filters.status" class="w-full">
              <ElOption :label="t('ai.logs.statusAll')" value="" />
              <ElOption
                v-for="item in STATUS_OPTIONS"
                :key="item"
                :label="statusLabel(item)"
                :value="item"
              />
            </ElSelect>
          </div>
          <div class="flex flex-col gap-1">
            <span class="yg-text-xs yg-text-tertiary">{{ t('ai.logs.keyScope') }}</span>
            <ElSelect v-model="filters.keyScope" class="w-full">
              <ElOption :label="t('ai.logs.keyScopeAll')" value="" />
              <ElOption
                v-for="item in KEY_SCOPE_OPTIONS"
                :key="item"
                :label="keyScopeLabel(item)"
                :value="item"
              />
            </ElSelect>
          </div>
          <div class="flex flex-col gap-1">
            <span class="yg-text-xs yg-text-tertiary">{{ t('ai.logs.purpose') }}</span>
            <ElSelect v-model="filters.purpose" class="w-full">
              <ElOption :label="t('ai.logs.purposeAll')" value="" />
              <ElOption
                v-for="item in PURPOSE_OPTIONS"
                :key="item"
                :label="item"
                :value="item"
              />
            </ElSelect>
          </div>
          <div class="flex flex-col gap-1">
            <span class="yg-text-xs yg-text-tertiary">{{ t('ai.logs.provider') }}</span>
            <ElInput
              v-model="filters.provider"
              clearable
              :placeholder="t('ai.logs.providerPlaceholder')"
            />
          </div>
          <div class="flex flex-col gap-1">
            <span class="yg-text-xs yg-text-tertiary">{{ t('ai.logs.model') }}</span>
            <ElInput
              v-model="filters.model"
              clearable
              :placeholder="t('ai.logs.modelPlaceholder')"
            />
          </div>
          <div class="flex flex-col gap-1">
            <span class="yg-text-xs yg-text-tertiary">{{ t('ai.logs.requestId') }}</span>
            <ElInput
              v-model="filters.requestId"
              clearable
              :placeholder="t('ai.logs.requestIdPlaceholder')"
            />
          </div>
          <div class="flex flex-col gap-1">
            <span class="yg-text-xs yg-text-tertiary">{{ t('ai.logs.timeRange') }}</span>
            <ElDatePicker
              v-model="filters.range"
              type="datetimerange"
              class="w-full"
              :range-separator="t('ai.logs.timeRangeTo')"
              :start-placeholder="t('ai.logs.timeRangeStart')"
              :end-placeholder="t('ai.logs.timeRangeEnd')"
            />
          </div>
        </div>
        <p class="yg-text-xs yg-text-tertiary mt-3">{{ t('ai.logs.timeRangeHint') }}</p>
      </div>
    </section>

    <section class="yg-card" v-loading="statsLoading">
      <header class="yg-card__header">
        <div class="flex flex-col gap-1 min-w-0">
          <h2 class="yg-card__title">{{ t('ai.logs.stats.title') }}</h2>
          <p class="yg-text-xs yg-text-tertiary">{{ t('ai.logs.stats.daysHint') }}</p>
        </div>
        <div class="yg-card__extra flex items-center gap-2">
          <span class="yg-text-xs yg-text-tertiary">{{ t('ai.logs.stats.days') }}</span>
          <ElSelect v-model="statsDays" class="ai-log-stats__days">
            <ElOption
              v-for="item in STATS_DAYS_OPTIONS"
              :key="item"
              :label="`${item} ${t('ai.logs.stats.daysUnit')}`"
              :value="item"
            />
          </ElSelect>
        </div>
      </header>

      <div class="yg-card__body flex flex-col gap-4">
        <div class="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          <div class="ai-log-stats__tile">
            <span class="yg-text-xs yg-text-tertiary">{{ t('ai.logs.stats.totalCalls') }}</span>
            <span class="ai-log-stats__value tabular-nums">{{ statsSummary.calls }}</span>
            <span class="yg-text-xs yg-text-tertiary">
              {{ t('ai.logs.stats.errorRate') }} {{ statsSummary.errorRate }}
            </span>
          </div>
          <div class="ai-log-stats__tile">
            <span class="yg-text-xs yg-text-tertiary">{{ t('ai.logs.stats.totalErrors') }}</span>
            <span class="ai-log-stats__value tabular-nums">{{ statsSummary.errors }}</span>
            <span class="yg-text-xs yg-text-tertiary">
              {{ t('ai.logs.statusError') }} + {{ t('ai.logs.statusRejected') }}
            </span>
          </div>
          <div class="ai-log-stats__tile">
            <span class="yg-text-xs yg-text-tertiary">{{ t('ai.logs.stats.totalTokens') }}</span>
            <span class="ai-log-stats__value tabular-nums">
              {{ statsSummary.totalTokens }}
            </span>
            <span class="yg-text-xs yg-text-tertiary">
              {{ t('ai.logs.stats.tokenEstimatedHint') }}
            </span>
          </div>
          <div class="ai-log-stats__tile">
            <span class="yg-text-xs yg-text-tertiary">{{ t('ai.logs.stats.avgLatency') }}</span>
            <span class="ai-log-stats__value tabular-nums">
              {{ formatMs(statsSummary.avgLatency, 'ai.logs.latencyUnit') }}
            </span>
            <span class="yg-text-xs yg-text-tertiary">{{ t('ai.logs.stats.daily') }}</span>
          </div>
        </div>

        <div v-if="statsFailed" class="yg-text-xs yg-text-tertiary">
          {{ t('ai.logs.stats.refreshFailed') }}
        </div>
        <div
          v-else-if="statsDaily.length === 0"
          class="yg-text-xs yg-text-tertiary"
        >
          {{ t('ai.logs.stats.noData') }}
        </div>
        <template v-else>
          <div class="overflow-x-auto">
            <table class="ai-log-stats__grid">
              <thead>
                <tr>
                  <th>{{ t('ai.logs.stats.date') }}</th>
                  <th class="text-right">{{ t('ai.logs.stats.calls') }}</th>
                  <th class="text-right">{{ t('ai.logs.stats.errors') }}</th>
                  <th class="text-right">{{ t('ai.logs.stats.tokens') }}</th>
                  <th class="text-right">{{ t('ai.logs.columns.latency') }}</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="item in statsDaily" :key="item.date">
                  <td class="tabular-nums">{{ item.date }}</td>
                  <td class="text-right tabular-nums">{{ item.calls }}</td>
                  <td class="text-right tabular-nums">{{ item.errors }}</td>
                  <td class="text-right tabular-nums">{{ item.total_tokens }}</td>
                  <td class="text-right tabular-nums">
                    {{ formatMs(item.avg_latency_ms, 'ai.logs.latencyUnit') }}
                  </td>
                </tr>
              </tbody>
            </table>
          </div>

          <div class="grid gap-3 lg:grid-cols-2">
            <div class="flex flex-col gap-2">
              <h3 class="yg-text-sm">{{ t('ai.logs.stats.byProvider') }}</h3>
              <table class="ai-log-stats__grid">
                <thead>
                  <tr>
                    <th>{{ t('ai.logs.columns.provider') }}</th>
                    <th class="text-right">{{ t('ai.logs.stats.calls') }}</th>
                    <th class="text-right">{{ t('ai.logs.stats.errors') }}</th>
                    <th class="text-right">{{ t('ai.logs.stats.units') }}</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="item in statsByProvider" :key="item.provider ?? 'unknown'">
                    <td>{{ item.provider ?? '—' }}</td>
                    <td class="text-right tabular-nums">{{ item.calls }}</td>
                    <td class="text-right tabular-nums">{{ item.errors }}</td>
                    <td class="text-right tabular-nums">{{ item.units }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
            <div class="flex flex-col gap-2">
              <h3 class="yg-text-sm">{{ t('ai.logs.stats.byPurpose') }}</h3>
              <table class="ai-log-stats__grid">
                <thead>
                  <tr>
                    <th>{{ t('ai.logs.purpose') }}</th>
                    <th class="text-right">{{ t('ai.logs.stats.calls') }}</th>
                    <th class="text-right">{{ t('ai.logs.stats.errors') }}</th>
                    <th class="text-right">{{ t('ai.logs.stats.units') }}</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="item in statsByPurpose" :key="item.purpose ?? 'unknown'">
                    <td>{{ item.purpose ?? '—' }}</td>
                    <td class="text-right tabular-nums">{{ item.calls }}</td>
                    <td class="text-right tabular-nums">{{ item.errors }}</td>
                    <td class="text-right tabular-nums">{{ item.units }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>

          <div class="grid gap-3 lg:grid-cols-3">
            <div class="flex flex-col gap-2 lg:col-span-2">
              <h3 class="yg-text-sm">{{ t('ai.logs.stats.byUser') }}</h3>
              <div v-if="statsByUser.length === 0" class="yg-text-xs yg-text-tertiary">
                {{ t('ai.logs.stats.noData') }}
              </div>
              <div v-else class="overflow-x-auto">
                <table class="ai-log-stats__grid">
                  <thead>
                    <tr>
                      <th>{{ t('ai.logs.user') }}</th>
                      <th class="text-right">{{ t('ai.logs.stats.calls') }}</th>
                      <th class="text-right">{{ t('ai.logs.stats.errors') }}</th>
                      <th class="text-right">{{ t('ai.logs.stats.tokens') }}</th>
                      <th class="text-right">{{ t('ai.logs.stats.units') }}</th>
                      <th class="text-right">{{ t('ai.logs.stats.avgLatency') }}</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="item in statsByUser" :key="item.user_oid">
                      <td>
                        <div class="flex flex-col">
                          <span>{{ item.name || item.user_oid }}</span>
                          <span class="yg-text-xs yg-text-tertiary">{{ item.user_oid }}</span>
                        </div>
                      </td>
                      <td class="text-right tabular-nums">{{ item.calls }}</td>
                      <td class="text-right tabular-nums">{{ item.errors }}</td>
                      <td class="text-right tabular-nums">{{ item.total_tokens }}</td>
                      <td class="text-right tabular-nums">{{ item.units }}</td>
                      <td class="text-right tabular-nums">
                        {{ formatMs(item.avg_latency_ms, 'ai.logs.latencyUnit') }}
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </div>
            <div class="flex flex-col gap-2">
              <h3 class="yg-text-sm">{{ t('ai.logs.stats.byStatus') }}</h3>
              <div v-if="statsByStatus.length === 0" class="yg-text-xs yg-text-tertiary">
                {{ t('ai.logs.stats.noData') }}
              </div>
              <table v-else class="ai-log-stats__grid">
                <thead>
                  <tr>
                    <th>{{ t('ai.logs.status') }}</th>
                    <th class="text-right">{{ t('ai.logs.stats.calls') }}</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="item in statsByStatus" :key="item.status ?? 'unknown'">
                    <td>{{ item.status ? statusLabel(item.status) : '—' }}</td>
                    <td class="text-right tabular-nums">{{ item.calls }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
        </template>
      </div>
    </section>

    <section class="yg-card">
      <div class="yg-card__body">
        <ElTable v-loading="loading" :data="rows" row-key="oid" stripe>
          <ElTableColumn prop="created_at" :label="t('ai.logs.columns.createdAt')" min-width="160">
            <template #default="{ row }">
              <span class="tabular-nums yg-text-xs">{{ formatTime(row.created_at) }}</span>
            </template>
          </ElTableColumn>
          <ElTableColumn :label="t('ai.logs.columns.user')" min-width="140">
            <template #default="{ row }">
              <div class="flex flex-col">
                <div class="ai-log-user">
                  <span class="yg-text-sm">{{ row.user_name || '—' }}</span>
                  <ElTag
                    v-if="isAdminSnapshot(row.roles_snapshot)"
                    type="info"
                    size="small"
                    class="ai-log-user__admin"
                  >
                    {{ t('ai.logs.columns.adminBadge') }}
                  </ElTag>
                </div>
                <span class="yg-text-xs yg-text-tertiary">{{ row.user_oid }}</span>
              </div>
            </template>
          </ElTableColumn>
          <ElTableColumn :label="t('ai.logs.columns.capability')" width="100" align="center">
            <template #default="{ row }">
              <ElTag :type="row.capability === 'chat' ? 'success' : 'primary'" size="small">
                {{ capabilityLabel(row.capability) }}
              </ElTag>
            </template>
          </ElTableColumn>
          <ElTableColumn
            prop="endpoint"
            :label="t('ai.logs.columns.endpoint')"
            min-width="180"
            show-overflow-tooltip
          >
            <template #default="{ row }">
              <span class="yg-text-xs">{{ row.endpoint || '—' }}</span>
            </template>
          </ElTableColumn>
          <ElTableColumn
            prop="provider"
            :label="t('ai.logs.columns.provider')"
            min-width="110"
            show-overflow-tooltip
          >
            <template #default="{ row }">
              <span class="yg-text-xs">{{ row.provider ?? '—' }}</span>
            </template>
          </ElTableColumn>
          <ElTableColumn
            prop="model"
            :label="t('ai.logs.columns.model')"
            min-width="140"
            show-overflow-tooltip
          >
            <template #default="{ row }">
              <span class="yg-text-xs">{{ row.model ?? '—' }}</span>
            </template>
          </ElTableColumn>
          <ElTableColumn :label="t('ai.logs.columns.purpose')" min-width="140">
            <template #default="{ row }">
              <span v-if="row.purpose" class="yg-text-xs">{{ row.purpose }}</span>
              <span v-else class="yg-text-tertiary">—</span>
            </template>
          </ElTableColumn>
          <ElTableColumn :label="t('ai.logs.columns.engineKind')" min-width="100">
            <template #default="{ row }">
              <span v-if="row.engine_kind" class="yg-text-xs">{{ row.engine_kind }}</span>
              <span v-else class="yg-text-tertiary">—</span>
            </template>
          </ElTableColumn>
          <ElTableColumn :label="t('ai.logs.columns.status')" width="100" align="center">
            <template #default="{ row }">
              <ElTag
                :type="statusTagType(row.status)"
                :class="statusTagClass(row.status)"
                :data-status="row.status"
                size="small"
              >
                {{ statusLabel(row.status) }}
              </ElTag>
            </template>
          </ElTableColumn>
          <ElTableColumn
            prop="http_status"
            :label="t('ai.logs.columns.httpStatus')"
            width="90"
            align="right"
          >
            <template #default="{ row }">
              <span class="tabular-nums">{{ row.http_status ?? '—' }}</span>
            </template>
          </ElTableColumn>
          <ElTableColumn
            prop="error_code"
            :label="t('ai.logs.columns.errorCode')"
            min-width="150"
            show-overflow-tooltip
          >
            <template #default="{ row }">
              <span v-if="row.error_code" class="tabular-nums yg-text-xs">{{ row.error_code }}</span>
              <span v-else class="yg-text-tertiary">—</span>
            </template>
          </ElTableColumn>
          <ElTableColumn
            prop="latency_ms"
            :label="t('ai.logs.columns.latency')"
            width="110"
            align="right"
          >
            <template #default="{ row }">
              <span v-if="row.latency_ms !== null" class="tabular-nums">
                {{ row.latency_ms }} {{ t('ai.logs.latencyUnit') }}
              </span>
              <span v-else class="yg-text-tertiary">—</span>
            </template>
          </ElTableColumn>
          <ElTableColumn
            prop="first_token_ms"
            :label="t('ai.logs.columns.firstToken')"
            width="110"
            align="right"
          >
            <template #default="{ row }">
              <span v-if="row.first_token_ms !== null" class="tabular-nums">
                {{ row.first_token_ms }} {{ t('ai.logs.latencyUnit') }}
              </span>
              <span v-else class="yg-text-tertiary">—</span>
            </template>
          </ElTableColumn>
          <ElTableColumn :label="t('ai.logs.columns.tokens')" min-width="180">
            <template #default="{ row }">
              <span class="tabular-nums yg-text-xs">{{ tokensLabel(row) }}</span>
            </template>
          </ElTableColumn>
          <ElTableColumn
            prop="attempt_no"
            :label="t('ai.logs.columns.attempt')"
            width="80"
            align="right"
          >
            <template #default="{ row }">
              <span v-if="row.attempt_no !== null" class="tabular-nums">
                {{ row.attempt_no }}
              </span>
              <span v-else class="yg-text-tertiary">—</span>
            </template>
          </ElTableColumn>
          <ElTableColumn prop="units" :label="t('ai.logs.columns.units')" width="100" align="right">
            <template #default="{ row }">
              <span class="tabular-nums">{{ row.units }}</span>
            </template>
          </ElTableColumn>
          <ElTableColumn :label="t('ai.logs.columns.keyScope')" width="100" align="center">
            <template #default="{ row }">
              <ElTag
                v-if="row.key_scope"
                :type="
                  row.key_scope === 'server'
                    ? 'warning'
                    : row.key_scope === 'shared'
                      ? 'primary'
                      : 'info'
                "
                size="small"
              >
                {{ keyScopeLabel(row.key_scope) }}
              </ElTag>
              <span v-else class="yg-text-tertiary">—</span>
            </template>
          </ElTableColumn>
          <ElTableColumn :label="t('ai.logs.columns.requestId')" min-width="180">
            <template #default="{ row }">
              <div v-if="row.request_id" class="flex items-center gap-1">
                <code class="tabular-nums yg-text-xs font-mono">{{ row.request_id }}</code>
                <ElButton text size="small" @click="copyRequestId(row.request_id)">
                  <ElIcon><CopyDocument /></ElIcon>
                </ElButton>
              </div>
              <span v-else class="yg-text-tertiary">—</span>
            </template>
          </ElTableColumn>
          <template #empty>
            <ElEmpty
              :description="loadFailed ? t('ai.logs.loadFailed') : t('ai.logs.empty')"
              :image-size="90"
            />
          </template>
        </ElTable>

        <ZPagination
          v-model:current-page="page"
          v-model:page-size="perpage"
          :total="total"
          @change="onPageChange"
        />
      </div>
    </section>
  </div>
</template>

<style scoped>
/* 用户列：名字与「管理员」标识同排，标识弱化不抢行 */
.ai-log-user {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--yg-space-1);
}

.ai-log-user__admin {
  flex: none;
}

/* 繁忙态用描边橙，与超时的实心橙区分开：tag 的配色走同名 CSS 变量，这里只改变量。 */
.ai-log-status--busy.el-tag {
  --el-tag-bg-color: var(--yg-bg-surface);
  --el-tag-border-color: var(--yg-color-warning);
  --el-tag-text-color: var(--yg-text-warning);
}

/* 概览数字块：不加边框/阴影，靠间距与字号分层，避免和表格抢注意力。 */
.ai-log-stats__tile {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: var(--yg-space-4);
  border-radius: 0;
  background: var(--yg-bg-surface-sunken);
  clip-path: polygon(0 0, calc(100% - 8px) 0, 100% 8px, 100% 100%, 0 100%);
}

.ai-log-stats__value {
  font-size: var(--yg-font-size-2xl);
  font-weight: 600;
  line-height: 1.2;
}

.ai-log-stats__days {
  width: 110px;
}

/* 概览表格：轻分隔线 + 首列左对齐，数字列右对齐（可比较）。 */
.ai-log-stats__grid {
  width: 100%;
  border-collapse: collapse;
  font-size: var(--yg-font-size-xs);
}

.ai-log-stats__grid th,
.ai-log-stats__grid td {
  padding: var(--yg-space-1) var(--yg-space-2);
  border-bottom: 1px solid var(--yg-border-subtle);
  text-align: left;
  white-space: nowrap;
}

.ai-log-stats__grid th {
  color: var(--yg-text-tertiary);
  font-weight: 500;
}

.ai-log-stats__grid tr:last-child td {
  border-bottom: none;
}
</style>
