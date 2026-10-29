<script setup lang="ts">
/**
 * 我的额度。
 *
 * 额度用尽时学生没有改额度的权限，只能提交「重置申请」等管理员批准；
 * 所以这一页除了展示用量，还要把「已申请、等待审批」这个中间态说清楚：
 * 提交后按钮立刻变成 disabled，同一能力不会重复提交（后端也会用 409 兜底）。
 */
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  ElButton,
  ElDialog,
  ElForm,
  ElFormItem,
  ElIcon,
  ElInput,
  ElInputNumber,
  ElMessage,
  ElOption,
  ElProgress,
  ElSelect,
  ElTag
} from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'
import {
  aiErrorOf,
  aiQuotaSourceLabelKey,
  createQuotaRequest,
  myQuota,
  myQuotaRequests,
  type AiQuotaEntryView,
  type AiQuotaRequest,
  type AiQuotaSubmitInput
} from '@/api/ai'
import { formatDateTime } from '@/utils/dateTime'
import { resolveErrorMessage } from '@/utils/errorMessage'
import type { AiQuotaKind } from '@/../types/ai'

/** 说明的字数上限：与后端字段的 200 字约束对齐。 */
const REASON_MAX = 200

const { t } = useI18n()

const loading = ref(false)
const failed = ref(false)
const ocr = ref<AiQuotaEntryView | null>(null)
const chat = ref<AiQuotaEntryView | null>(null)

const requestsFailed = ref(false)
/** 每个能力最多一条待审批申请：同能力重复提交没有意义。 */
const pending = ref<Partial<Record<AiQuotaKind, AiQuotaRequest>>>({})

const dialogVisible = ref(false)
const requestKind = ref<AiQuotaKind>('chat')
const requestReason = ref('')
const requestLimit = ref<number | null>(null)
const submitting = ref(false)

function percent(entry: AiQuotaEntryView | null): number {
  if (!entry || entry.limit <= 0) {
    return 0
  }
  return Math.min(100, Math.round((entry.used / entry.limit) * 100))
}

function entryOf(kind: AiQuotaKind): AiQuotaEntryView | null {
  return kind === 'chat' ? chat.value : ocr.value
}

/** limit <= 0 表示没设上限，不算用尽。 */
function isExhausted(kind: AiQuotaKind): boolean {
  const entry = entryOf(kind)
  return !!entry && entry.limit > 0 && entry.used >= entry.limit
}

function hasPending(kind: AiQuotaKind): boolean {
  return Boolean(pending.value[kind])
}

function kindLabel(kind: AiQuotaKind): string {
  return kind === 'chat' ? t('ai.quota.chatTitle') : t('ai.quota.ocrTitle')
}

function sourceLabel(entry: AiQuotaEntryView | null): string | null {
  const key = aiQuotaSourceLabelKey(entry?.source)
  return key ? t(key) : null
}

const tiles = computed(() => [
  {
    kind: 'ocr' as AiQuotaKind,
    title: t('ai.quota.ocrTitle'),
    unit: t('ai.quota.ocrUnit'),
    entry: ocr.value,
    percent: percent(ocr.value)
  },
  {
    kind: 'chat' as AiQuotaKind,
    title: t('ai.quota.chatTitle'),
    unit: t('ai.quota.chatUnit'),
    entry: chat.value,
    percent: percent(chat.value)
  }
])

/** 顶部待审批区：只列 pending，且每个能力只出现一次。 */
const pendingList = computed<AiQuotaRequest[]>(() =>
  (['chat', 'ocr'] as AiQuotaKind[])
    .map((kind) => pending.value[kind])
    .filter((item): item is AiQuotaRequest => Boolean(item))
)

/** 本地补一条待审批占位：409 时后端不会回请求体，但状态必须立刻切过去。 */
function pendingPlaceholder(kind: AiQuotaKind): AiQuotaRequest {
  return {
    oid: '',
    period: entryOf(kind)?.period ?? '',
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
    ocr.value = data.ocr
    chat.value = data.chat
  } catch {
    ocr.value = null
    chat.value = null
    failed.value = true
  } finally {
    loading.value = false
  }
}

async function loadRequests() {
  requestsFailed.value = false
  try {
    const items = await myQuotaRequests()
    const next: Partial<Record<AiQuotaKind, AiQuotaRequest>> = {}
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

function openRequest(kind: AiQuotaKind) {
  if (hasPending(kind) || submitting.value) {
    return
  }
  requestKind.value = kind
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

        <div class="grid gap-4 sm:grid-cols-2" :class="{ 'mt-4': pendingList.length }">
          <div v-for="tile in tiles" :key="tile.kind" class="yg-tile p-4">
            <div class="flex items-baseline justify-between gap-2">
              <span class="yg-statistic__title">{{ tile.title }}</span>
              <span class="yg-text-xs yg-text-tertiary">{{ tile.unit }}</span>
            </div>
            <div v-if="sourceLabel(tile.entry)" class="flex items-center gap-2 mt-1">
              <ElTag type="info" size="small" effect="plain">{{ sourceLabel(tile.entry) }}</ElTag>
            </div>
            <p class="yg-statistic__value">
              {{ tile.entry?.used ?? 0 }}
              <span class="yg-statistic__suffix">/ {{ tile.entry?.limit ?? 0 }}</span>
            </p>
            <ElProgress :percentage="tile.percent" :stroke-width="6" class="mt-2" />
            <p class="yg-text-xs yg-text-tertiary mt-2 tabular-nums">
              {{ t('ai.common.period') }}：{{ tile.entry?.period ?? '—' }}
            </p>
            <div v-if="isExhausted(tile.kind)" class="flex justify-end mt-3">
              <ElButton
                size="small"
                type="primary"
                plain
                :disabled="hasPending(tile.kind) || submitting"
                @click="openRequest(tile.kind)"
              >
                {{
                  hasPending(tile.kind)
                    ? t('ai.quota.request.applied')
                    : t('ai.quota.request.apply')
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

    <section class="yg-card">
      <header class="yg-card__header">
        <h2 class="yg-statistic__title">{{ t('ai.quota.ruleTitle') }}</h2>
      </header>
      <div class="yg-card__body">
        <ul class="yg-text-sm yg-text-secondary flex flex-col gap-1">
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
            <ElOption :label="t('ai.quota.ocrTitle')" value="ocr" />
            <ElOption :label="t('ai.quota.chatTitle')" value="chat" />
          </ElSelect>
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
