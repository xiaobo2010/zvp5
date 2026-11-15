<script setup lang="ts">
/**
 * 我的额度。
 *
 * **只有一个额度池 AF**：对话与 OCR 都记在它上面，页面只展示一格，不做
 * 「对话 + OCR」的加法（同一个池子，加起来会翻倍）。
 *
 * 额度用尽时学生没有改额度的权限，只能提交「重置申请」等管理员批准；
 * 所以这一页除了展示用量，还要把「已申请、等待审批」这个中间态说清楚：
 * 提交后按钮立刻变成 disabled（后端也会用 409 兜底）。
 *
 * 申请里的「消耗类型」（对话 / OCR）只是告诉管理员这次是哪类消耗，
 * 批了之后动的仍然是同一个 AF 池。
 */
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElButton } from 'element-plus/es/components/button/index.mjs'
import { ElDialog } from 'element-plus/es/components/dialog/index.mjs'
import { ElForm } from 'element-plus/es/components/form/index.mjs'
import { ElFormItem } from 'element-plus/es/components/form/index.mjs'
import { ElIcon } from 'element-plus/es/components/icon/index.mjs'
import { ElInput } from 'element-plus/es/components/input/index.mjs'
import { ElInputNumber } from 'element-plus/es/components/input-number/index.mjs'
import { ElMessage } from 'element-plus/es/components/message/index.mjs'
import { ElOption } from 'element-plus/es/components/select/index.mjs'
import { ElProgress } from 'element-plus/es/components/progress/index.mjs'
import { ElSelect } from 'element-plus/es/components/select/index.mjs'
import { ElTable } from 'element-plus/es/components/table/index.mjs'
import { ElTableColumn } from 'element-plus/es/components/table/index.mjs'
import { ElTag } from 'element-plus/es/components/tag/index.mjs'
import { Refresh } from '@element-plus/icons-vue'
import {
  AI_QUOTA_REQUEST_KINDS,
  aiErrorOf,
  aiQuotaSourceLabelKey,
  createQuotaRequest,
  fetchQuotaUsage,
  myQuota,
  myQuotaRequests,
  type AiQuotaEntryView,
  type AiQuotaRequest,
  type AiQuotaSubmitInput
} from '@/api/ai'
import { formatDateTime } from '@/utils/dateTime'
import { resolveErrorMessage } from '@/utils/errorMessage'
import type { AiQuotaRequestKind, AiQuotaUsage } from '@/../types/ai'

/** 说明的字数上限：与后端字段的 200 字约束对齐。 */
const REASON_MAX = 200

const { t } = useI18n()

const loading = ref(false)
const failed = ref(false)
/** 唯一的额度池。 */
const af = ref<AiQuotaEntryView | null>(null)

const requestsFailed = ref(false)
/** 每个消耗类型最多一条待审批申请：同类型重复提交没有意义。 */
const pending = ref<Partial<Record<AiQuotaRequestKind, AiQuotaRequest>>>({})

/* 当月用量统计：与额度卡分开取数、分开报错，互不阻塞。 */
const usageLoading = ref(false)
const usageFailed = ref(false)
const usageError = ref('')
const usage = ref<AiQuotaUsage | null>(null)

const dialogVisible = ref(false)
const requestKind = ref<AiQuotaRequestKind>('chat')
const requestReason = ref('')
const requestLimit = ref<number | null>(null)
const submitting = ref(false)

function percent(entry: AiQuotaEntryView | null): number {
  if (!entry || entry.limit <= 0) {
    return 0
  }
  return Math.min(100, Math.round((entry.used / entry.limit) * 100))
}

/** limit <= 0 表示没设上限，不算用尽。 */
function isExhausted(): boolean {
  const entry = af.value
  return !!entry && entry.limit > 0 && entry.used >= entry.limit
}

/** 已有待审批申请：任何一条 pending 都让按钮进入禁用态（同一个池子，没必要再提一条）。 */
function hasPending(): boolean {
  return pendingList.value.length > 0
}

/** 消耗类型的展示文案；它是申请口径，不是额度池。 */
function kindLabel(kind: AiQuotaRequestKind): string {
  return kind === 'chat' ? t('ai.quota.chatTitle') : t('ai.quota.ocrTitle')
}

function sourceLabel(entry: AiQuotaEntryView | null): string | null {
  const key = aiQuotaSourceLabelKey(entry?.source)
  return key ? t(key) : null
}

/** 渠道 / 模型的空值占位：后端可能是 null，也可能是空串。 */
function usageLabel(value: string | null): string {
  return value ? value : t('ai.quota.usage.unlabeled')
}

/** 一行记录都没有才算空态：total.calls 为 0 但维度有行时仍要渲染表格。 */
const usageEmpty = computed(() => {
  const data = usage.value
  return (
    !!data && data.total.calls === 0 && !data.by_provider.length && !data.by_model.length
  )
})

/** 顶部待审批区：只列 pending，同一消耗类型只出现一次。 */
const pendingList = computed<AiQuotaRequest[]>(() =>
  AI_QUOTA_REQUEST_KINDS.map((kind) => pending.value[kind]).filter(
    (item): item is AiQuotaRequest => Boolean(item)
  )
)

/** 本地补一条待审批占位：409 时后端不会回请求体，但状态必须立刻切过去。 */
function pendingPlaceholder(kind: AiQuotaRequestKind): AiQuotaRequest {
  return {
    oid: '',
    period: af.value?.period ?? '',
    kind,
    status: 'pending',
    reason: null,
    requested_limit: null,
    created_at: null,
    decided_at: null,
    decision_note: null
  }
}

async function load() {
  if (loading.value) {
    return
  }
  loading.value = true
  failed.value = false
  try {
    const data = await myQuota()
    af.value = data.af
  } catch {
    af.value = null
    failed.value = true
  } finally {
    loading.value = false
  }
}

async function loadRequests() {
  requestsFailed.value = false
  try {
    const items = await myQuotaRequests()
    const next: Partial<Record<AiQuotaRequestKind, AiQuotaRequest>> = {}
    for (const item of items) {
      if (item.status === 'pending' && !next[item.kind]) {
        next[item.kind] = item
      }
    }
    pending.value = next
  } catch {
    requestsFailed.value = true
  }
}

/** 用量统计单独加载：失败只让这张卡降级，上面的额度卡照常显示。 */
async function loadUsage() {
  if (usageLoading.value) {
    return
  }
  usageLoading.value = true
  usageFailed.value = false
  try {
    usage.value = await fetchQuotaUsage()
  } catch (error) {
    usage.value = null
    usageFailed.value = true
    usageError.value = resolveErrorMessage(error, 'ai.quota.usage.loadFailed')
  } finally {
    usageLoading.value = false
  }
}

function openRequest() {
  if (hasPending() || submitting.value) {
    return
  }
  requestKind.value = 'chat'
  requestReason.value = ''
  requestLimit.value = null
  dialogVisible.value = true
}

async function submitRequest() {
  if (submitting.value) {
    return
  }
  submitting.value = true
  const payload: AiQuotaSubmitInput = { kind: requestKind.value }
  const reason = requestReason.value.trim()
  if (reason) {
    payload.reason = reason
  }
  const limit = requestLimit.value
  if (typeof limit === 'number' && Number.isFinite(limit) && limit > 0) {
    payload.requested_limit = Math.trunc(limit)
  }
  try {
    const created = await createQuotaRequest(payload)
    pending.value = {
      ...pending.value,
      [payload.kind]: created.status === 'pending' ? created : pendingPlaceholder(payload.kind)
    }
    dialogVisible.value = false
    ElMessage.success(t('ai.quota.request.submitted'))
  } catch (error) {
    if (aiErrorOf(error)?.status === 409) {
      // 已有 pending：不当作错误，直接把按钮切到「已申请」。
      pending.value = { ...pending.value, [payload.kind]: pendingPlaceholder(payload.kind) }
      dialogVisible.value = false
      return
    }
    ElMessage.error(resolveErrorMessage(error, 'ai.quota.request.submitFailed'))
  } finally {
    submitting.value = false
  }
}

onMounted(() => {
  void load()
  void loadRequests()
  void loadUsage()
})
</script>

<template>
  <div class="flex flex-col gap-4">
    <section class="yg-card" v-loading="loading">
      <header class="yg-card__header">
        <div class="flex flex-col gap-1 min-w-0">
          <h2 class="yg-card__title">{{ t('ai.quota.title') }}</h2>
          <p class="yg-text-xs yg-text-tertiary">{{ t('ai.quota.subtitle') }}</p>
        </div>
        <div class="yg-card__extra">
          <ElButton @click="load">
            <ElIcon class="mr-1"><Refresh /></ElIcon>
            {{ t('ai.common.refresh') }}
          </ElButton>
        </div>
      </header>

      <div class="yg-card__body">
        <div v-if="pendingList.length" class="ai-quota-panel__pending">
          <h3 class="yg-statistic__title">{{ t('ai.quota.request.pendingTitle') }}</h3>
          <ul class="flex flex-col gap-1 mt-2">
            <li
              v-for="item in pendingList"
              :key="item.kind"
              class="flex flex-wrap items-center gap-2 yg-text-xs yg-text-secondary"
            >
              <ElTag type="warning" size="small" effect="plain">
                {{ kindLabel(item.kind) }}
              </ElTag>
              <span>{{ t('ai.quota.request.applied') }}</span>
              <span v-if="item.reason" class="yg-text-tertiary">{{ item.reason }}</span>
              <span v-if="item.requested_limit !== null" class="yg-text-tertiary tabular-nums">
                {{ t('ai.quota.request.requestedLimit') }}：{{ item.requested_limit }}
              </span>
              <span class="yg-text-tertiary tabular-nums">
                {{ formatDateTime(item.created_at) }}
              </span>
            </li>
          </ul>
        </div>

        <div class="grid gap-4" :class="{ 'mt-4': pendingList.length }">
          <div class="yg-tile p-4">
            <div class="flex items-baseline justify-between gap-2">
              <span class="yg-statistic__title">{{ t('ai.quota.afTitle') }}</span>
              <span class="yg-text-xs yg-text-tertiary">{{ t('ai.quota.afUnit') }}</span>
            </div>
            <div v-if="sourceLabel(af)" class="flex items-center gap-2 mt-1">
              <ElTag type="info" size="small" effect="plain">{{ sourceLabel(af) }}</ElTag>
            </div>
            <p class="yg-statistic__value">
              {{ af?.used ?? 0 }}
              <span class="yg-statistic__suffix">/ {{ af?.limit ?? 0 }}</span>
            </p>
            <ElProgress :percentage="percent(af)" :stroke-width="6" class="mt-2" />
            <p class="yg-text-xs yg-text-tertiary mt-2 tabular-nums">
              {{ t('ai.common.period') }}：{{ af?.period ?? '—' }}
            </p>
            <div v-if="isExhausted()" class="flex justify-end mt-3">
              <ElButton
                size="small"
                type="primary"
                plain
                :disabled="hasPending() || submitting"
                @click="openRequest"
              >
                {{
                  hasPending() ? t('ai.quota.request.applied') : t('ai.quota.request.apply')
                }}
              </ElButton>
            </div>
          </div>
        </div>

        <p v-if="failed" class="yg-text-xs yg-text-danger mt-3">
          {{ t('ai.quota.loadFailed') }}
        </p>
        <p v-if="requestsFailed" class="yg-text-xs yg-text-danger mt-1">
          {{ t('ai.quota.request.loadFailed') }}
        </p>
      </div>
    </section>

    <section class="yg-card" v-loading="usageLoading">
      <header class="yg-card__header">
        <div class="flex flex-col gap-1 min-w-0">
          <h2 class="yg-card__title">{{ t('ai.quota.usage.title') }}</h2>
          <p class="yg-text-xs yg-text-tertiary">{{ t('ai.quota.usage.subtitle') }}</p>
        </div>
        <div class="yg-card__extra">
          <ElButton :disabled="usageLoading" @click="loadUsage">
            <ElIcon class="mr-1"><Refresh /></ElIcon>
            {{ t('ai.common.refresh') }}
          </ElButton>
        </div>
      </header>

      <div class="yg-card__body">
        <div v-if="usageFailed" class="py-6 flex flex-col items-center gap-3">
          <p class="yg-text-sm yg-text-danger">{{ usageError }}</p>
          <ElButton size="small" :loading="usageLoading" @click="loadUsage">
            {{ t('ai.quota.usage.retry') }}
          </ElButton>
        </div>

        <template v-else-if="usage && !usageEmpty">
          <p class="yg-text-xs yg-text-tertiary tabular-nums">
            {{ t('ai.common.period') }}：{{ usage.period || '—' }}
          </p>

          <div class="grid gap-4 sm:grid-cols-3 mt-3">
            <div class="yg-tile p-4">
              <span class="yg-statistic__title">{{ t('ai.quota.usage.calls') }}</span>
              <p class="yg-statistic__value">{{ usage.total.calls }}</p>
            </div>
            <div class="yg-tile p-4">
              <span class="yg-statistic__title">{{ t('ai.quota.usage.tokens') }}</span>
              <p class="yg-statistic__value">{{ usage.total.total_tokens }}</p>
            </div>
            <div class="yg-tile p-4">
              <span class="yg-statistic__title">{{ t('ai.quota.usage.units') }}</span>
              <p class="yg-statistic__value">{{ usage.total.units }}</p>
            </div>
          </div>

          <div class="mt-4 flex flex-col gap-1">
            <h3 class="yg-statistic__title">{{ t('ai.quota.usage.byProvider') }}</h3>
            <p
              v-if="!usage.by_provider.length"
              class="py-4 text-center yg-text-sm yg-text-tertiary"
            >
              {{ t('ai.quota.usage.empty') }}
            </p>
            <ElTable v-else :data="usage.by_provider" class="w-full">
              <ElTableColumn :label="t('ai.quota.usage.provider')" min-width="160">
                <template #default="{ row }">
                  <span class="yg-text-sm">{{ usageLabel(row.provider) }}</span>
                </template>
              </ElTableColumn>
              <ElTableColumn :label="t('ai.quota.usage.calls')" min-width="100" align="right">
                <template #default="{ row }">
                  <span class="tabular-nums">{{ row.calls }}</span>
                </template>
              </ElTableColumn>
              <ElTableColumn :label="t('ai.quota.usage.tokens')" min-width="120" align="right">
                <template #default="{ row }">
                  <span class="tabular-nums">{{ row.total_tokens }}</span>
                </template>
              </ElTableColumn>
              <ElTableColumn :label="t('ai.quota.usage.units')" min-width="100" align="right">
                <template #default="{ row }">
                  <span class="tabular-nums">{{ row.units }}</span>
                </template>
              </ElTableColumn>
            </ElTable>
          </div>

          <div class="mt-4 flex flex-col gap-1">
            <h3 class="yg-statistic__title">{{ t('ai.quota.usage.byModel') }}</h3>
            <p v-if="!usage.by_model.length" class="py-4 text-center yg-text-sm yg-text-tertiary">
              {{ t('ai.quota.usage.empty') }}
            </p>
            <ElTable v-else :data="usage.by_model" class="w-full">
              <ElTableColumn :label="t('ai.quota.usage.provider')" min-width="160">
                <template #default="{ row }">
                  <span class="yg-text-sm">{{ usageLabel(row.provider) }}</span>
                </template>
              </ElTableColumn>
              <ElTableColumn :label="t('ai.quota.usage.model')" min-width="180">
                <template #default="{ row }">
                  <span class="yg-text-sm">{{ usageLabel(row.model) }}</span>
                </template>
              </ElTableColumn>
              <ElTableColumn :label="t('ai.quota.usage.calls')" min-width="100" align="right">
                <template #default="{ row }">
                  <span class="tabular-nums">{{ row.calls }}</span>
                </template>
              </ElTableColumn>
              <ElTableColumn :label="t('ai.quota.usage.tokens')" min-width="120" align="right">
                <template #default="{ row }">
                  <span class="tabular-nums">{{ row.total_tokens }}</span>
                </template>
              </ElTableColumn>
              <ElTableColumn :label="t('ai.quota.usage.units')" min-width="100" align="right">
                <template #default="{ row }">
                  <span class="tabular-nums">{{ row.units }}</span>
                </template>
              </ElTableColumn>
            </ElTable>
          </div>
        </template>

        <p v-else-if="usageEmpty" class="py-8 text-center yg-text-sm yg-text-tertiary">
          {{ t('ai.quota.usage.empty') }}
        </p>
      </div>
    </section>

    <section class="yg-card">
      <header class="yg-card__header">
        <h2 class="yg-statistic__title">{{ t('ai.quota.ruleTitle') }}</h2>
      </header>
      <div class="yg-card__body">
        <ul class="yg-text-sm yg-text-secondary flex flex-col gap-1">
          <li>{{ t('ai.quota.ruleUnified') }}</li>
          <li>{{ t('ai.quota.ruleOcr') }}</li>
          <li>{{ t('ai.quota.rulePersonal') }}</li>
          <li>{{ t('ai.quota.ruleShared') }}</li>
        </ul>
      </div>
    </section>

    <ElDialog
      v-model="dialogVisible"
      :title="t('ai.quota.request.dialogTitle')"
      width="min(460px, 92vw)"
      :close-on-click-modal="false"
      :close-on-press-escape="!submitting"
      :show-close="!submitting"
      append-to-body
    >
      <ElForm label-position="top">
        <ElFormItem :label="t('ai.quota.request.kind')">
          <ElSelect v-model="requestKind" class="w-full" :disabled="submitting">
            <ElOption :label="t('ai.quota.chatTitle')" value="chat" />
            <ElOption :label="t('ai.quota.ocrTitle')" value="ocr" />
          </ElSelect>
          <p class="yg-text-xs yg-text-tertiary mt-1">
            {{ t('ai.quota.request.kindHint') }}
          </p>
        </ElFormItem>
        <ElFormItem :label="t('ai.quota.request.reason')">
          <ElInput
            v-model="requestReason"
            type="textarea"
            :rows="3"
            :maxlength="REASON_MAX"
            show-word-limit
            :placeholder="t('ai.quota.request.reasonPlaceholder')"
            :disabled="submitting"
          />
        </ElFormItem>
        <ElFormItem :label="t('ai.quota.request.requestedLimit')">
          <ElInputNumber
            v-model="requestLimit"
            :min="0"
            :step="10"
            class="w-full"
            :disabled="submitting"
          />
          <p class="yg-text-xs yg-text-tertiary mt-1">
            {{ t('ai.quota.request.requestedLimitHint') }}
          </p>
        </ElFormItem>
      </ElForm>
      <template #footer>
        <div class="flex items-center justify-end gap-2">
          <ElButton :disabled="submitting" @click="dialogVisible = false">
            {{ t('ai.common.cancel') }}
          </ElButton>
          <ElButton type="primary" :loading="submitting" @click="submitRequest">
            {{ t('ai.quota.request.submit') }}
          </ElButton>
        </div>
      </template>
    </ElDialog>
  </div>
</template>
