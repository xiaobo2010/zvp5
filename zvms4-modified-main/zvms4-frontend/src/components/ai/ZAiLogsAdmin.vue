<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  ElButton,
  ElDatePicker,
  ElEmpty,
  ElIcon,
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
import { aiErrorCodeOf, aiErrorCopy, listAiLogs, toEpochSeconds } from '@/api/ai'
import type { AiLogCapability, AiLogKeyScope, AiLogRecord, AiLogStatus } from '@/../types/ai'

const { t } = useI18n()

const CAPABILITY_OPTIONS: AiLogCapability[] = ['ocr', 'chat']
const STATUS_OPTIONS: AiLogStatus[] = ['success', 'error', 'timeout', 'rejected', 'busy']
const KEY_SCOPE_OPTIONS: AiLogKeyScope[] = ['personal', 'shared', 'server']

/** 五种状态各自的配色：成功绿、错误红、超时橙、拒绝灰、繁忙橙描边。 */
const STATUS_TAG_TYPE: Record<AiLogStatus, 'success' | 'danger' | 'warning' | 'info'> = {
  success: 'success',
  error: 'danger',
  timeout: 'warning',
  rejected: 'info',
  busy: 'warning'
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
  range: undefined as [Date, Date] | undefined
})

/** 过滤条件变动后回到第一页：签名串只在取值真的变了时才变。 */
const filterSignature = computed(() =>
  [
    filters.userOid,
    filters.capability,
    filters.status,
    filters.keyScope,
    filters.range?.[0]?.getTime() ?? 0,
    filters.range?.[1]?.getTime() ?? 0
  ].join('|')
)

/** 依次递增的请求序号：慢响应回来时若已有更新的请求，直接丢弃。 */
let loadSeq = 0

function onUserChange(value: string | string[]) {
  filters.userOid = typeof value === 'string' ? value : ''
}

function resetFilters() {
  filters.userOid = ''
  filters.capability = ''
  filters.status = ''
  filters.keyScope = ''
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
    busy: t('ai.logs.statusBusy')
  }
  return map[status] ?? status
}

function keyScopeLabel(scope: AiLogKeyScope): string {
  const map: Record<AiLogKeyScope, string> = {
    personal: t('ai.logs.keyScopePersonal'),
    shared: t('ai.logs.keyScopeShared'),
    server: t('ai.logs.keyScopeServer')
  }
  return map[scope] ?? scope
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

    <section class="yg-card" v-loading="loading">
      <div class="yg-card__body">
        <ElTable :data="rows" row-key="oid" stripe>
          <ElTableColumn prop="created_at" :label="t('ai.logs.columns.createdAt')" min-width="160">
            <template #default="{ row }">
              <span class="tabular-nums yg-text-xs">{{ formatTime(row.created_at) }}</span>
            </template>
          </ElTableColumn>
          <ElTableColumn :label="t('ai.logs.columns.user')" min-width="140">
            <template #default="{ row }">
              <div class="flex flex-col">
                <span class="yg-text-sm">{{ row.user_name || '—' }}</span>
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
/* 繁忙态用描边橙，与超时的实心橙区分开：tag 的配色走同名 CSS 变量，这里只改变量。 */
.ai-log-status--busy.el-tag {
  --el-tag-bg-color: var(--yg-bg-surface);
  --el-tag-border-color: var(--yg-color-warning);
  --el-tag-text-color: var(--yg-text-warning);
}
</style>
