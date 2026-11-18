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
import { ElProgress } from 'element-plus/es/components/progress/index.mjs'
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
import { useUserStore } from '@/stores/user'
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

/* ---------------------------------------------------------------------------
   AF 口径说明的折叠记忆（纯 UI 偏好，按账号隔离，不进任何请求）
   --------------------------------------------------------------------------- */

const AF_HELP_PREF_PREFIX = 'zvms.ai.afHelp'

/** 按账号取偏好 key；读不到账号（无 Pinia 上下文等）退回匿名档。 */
function afHelpPrefKey(): string {
  let uid = 'anon'
  try {
    uid = String(useUserStore()._id || 'anon')
  } catch {
    /* 隔离环境没有 Pinia：偏好退回匿名档，功能不受影响 */
  }
  return `${AF_HELP_PREF_PREFIX}.${uid}`
}

/** 默认展开：首次进入能看到完整口径；'0' 是用户主动收起过的标记。 */
function readAfHelpPreference(): boolean {
  try {
    return localStorage.getItem(afHelpPrefKey()) !== '0'
  } catch {
    return true
  }
}

const afHelpOpen = ref(readAfHelpPreference())

/** 切换并记忆；隐私模式写不进 localStorage 也照常切换本次会话。 */
function toggleAfHelp(): void {
  afHelpOpen.value = !afHelpOpen.value
  try {
    localStorage.setItem(afHelpPrefKey(), afHelpOpen.value ? '1' : '0')
  } catch {
    /* 写不进去不影响本次切换 */
  }
}

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
        <!-- 标题行即折叠开关：整行可点，键盘 Enter/Space 原生可达 -->
        <h2 class="ai-quota-af__heading">
          <button
            type="button"
            class="ai-quota-af__toggle"
            :aria-expanded="afHelpOpen"
            aria-controls="ai-quota-af-body"
            @click="toggleAfHelp"
          >
            <span class="ai-quota-af__toggle-title">{{ t('ai.quota.ruleTitle') }}</span>
            <span class="ai-quota-af__toggle-state">
              {{ afHelpOpen ? t('ai.quota.afCollapse') : t('ai.quota.afExpand') }}
              <span class="ai-quota-af__chevron" aria-hidden="true"></span>
            </span>
          </button>
        </h2>
      </header>
      <div
        id="ai-quota-af-body"
        class="ai-quota-af__body"
        :class="{ 'is-open': afHelpOpen }"
      >
        <div class="ai-quota-af__inner">
          <div class="yg-card__body">
            <!-- AF 口径说明：术语首次出现给全称；主说明置顶、细则分行，窄屏自然折行 -->
            <div class="ai-quota-af">
              <p class="ai-quota-af__lead">{{ t('ai.quota.afMeaning') }}</p>
              <ul class="ai-quota-af__list">
                <li>{{ t('ai.quota.afChat') }}</li>
                <li>{{ t('ai.quota.ruleOcr') }}</li>
                <li>{{ t('ai.quota.rulePersonal') }}</li>
                <li>{{ t('ai.quota.ruleShared') }}</li>
                <li>{{ t('ai.quota.ruleUnified') }}</li>
                <li>{{ t('ai.quota.afMonthly') }}</li>
              </ul>
            </div>
          </div>
        </div>
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
          <!-- 消耗类型二选一：芯片一次点击即选中，不再先展开下拉 -->
          <div
            class="ai-chips ai-chips--dialog"
            role="radiogroup"
            :aria-label="t('ai.quota.request.kind')"
          >
            <button
              type="button"
              role="radio"
              class="ai-chips__item"
              :class="{ 'is-active': requestKind === 'chat' }"
              :aria-checked="requestKind === 'chat'"
              data-value="chat"
              :disabled="submitting"
              @click="requestKind = 'chat'"
            >
              {{ t('ai.quota.chatTitle') }}
            </button>
            <button
              type="button"
              role="radio"
              class="ai-chips__item"
              :class="{ 'is-active': requestKind === 'ocr' }"
              :aria-checked="requestKind === 'ocr'"
              data-value="ocr"
              :disabled="submitting"
              @click="requestKind = 'ocr'"
            >
              {{ t('ai.quota.ocrTitle') }}
            </button>
          </div>
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

<style scoped>
/* 折叠开关：标题行整体可点，展开/收起文案与小三角随状态走 */
.ai-quota-af__heading {
  margin: 0;
  min-width: 0;
}

.ai-quota-af__toggle {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: var(--yg-space-2);
  width: 100%;
  padding: 0;
  border: 0;
  background: transparent;
  font: inherit;
  color: var(--yg-text-primary);
  text-align: left;
  cursor: pointer;
}

.ai-quota-af__toggle-title {
  font-size: var(--yg-font-size-sm);
  font-weight: var(--yg-font-weight-medium);
  color: var(--yg-text-primary);
}

.ai-quota-af__toggle-state {
  display: inline-flex;
  align-items: center;
  gap: var(--yg-space-1);
  font-size: var(--yg-font-size-xs);
  color: var(--yg-text-tertiary);
  white-space: nowrap;
}

.ai-quota-af__toggle:hover .ai-quota-af__toggle-state {
  color: var(--yg-text-secondary);
}

.ai-quota-af__toggle:focus-visible {
  outline: none;
  box-shadow: var(--yg-focus-ring);
}

.ai-quota-af__chevron {
  width: 0;
  height: 0;
  border-top: 4px solid currentColor;
  border-right: 4px solid transparent;
  border-left: 4px solid transparent;
  transform: rotate(-90deg);
  transition: transform var(--yg-dur-base) var(--yg-ease-out);
}

.ai-quota-af__toggle[aria-expanded='true'] .ai-quota-af__chevron {
  transform: rotate(0deg);
}

/* 折叠动画：grid 0fr→1fr 插值高度 + 透明度，收起不留空白 */
.ai-quota-af__body {
  display: grid;
  grid-template-rows: 0fr;
  opacity: 0;
  transition:
    grid-template-rows 200ms var(--yg-ease-out),
    opacity 200ms var(--yg-ease-out);
}

.ai-quota-af__body.is-open {
  grid-template-rows: 1fr;
  opacity: 1;
}

.ai-quota-af__inner {
  overflow: hidden;
  min-height: 0;
}

/* AF 计费口径：主说明置顶、细则分行；斜切短线沿用页面的 HUD 记号 */
.ai-quota-af__lead {
  margin: 0 0 var(--yg-space-3);
  color: var(--yg-text-primary);
  font-size: var(--yg-font-size-md);
  line-height: var(--yg-line-height-normal);
  overflow-wrap: anywhere;
}

.ai-quota-af__list {
  display: flex;
  flex-direction: column;
  gap: var(--yg-space-2);
  margin: 0;
  padding: 0;
  list-style: none;
}

.ai-quota-af__list li {
  position: relative;
  padding-left: var(--yg-space-4);
  color: var(--yg-text-secondary);
  font-size: var(--yg-font-size-sm);
  line-height: var(--yg-line-height-normal);
  overflow-wrap: anywhere;
}

.ai-quota-af__list li::before {
  content: '';
  position: absolute;
  left: 0;
  top: 0.62em;
  width: 12px;
  height: 2px;
  background: var(--phx-accent, var(--yg-color-primary));
  transform: skewX(-40deg);
}

/* 折叠与三角动画在减少动效下瞬切 */
@media (prefers-reduced-motion: reduce) {
  .ai-quota-af__body,
  .ai-quota-af__chevron {
    transition: none;
  }
}

/* 消耗类型芯片：弹层挂在 body 下、不吃页面级皮肤，这里自带一份直角描边版式 */
.ai-chips--dialog {
  display: flex;
  flex-wrap: wrap;
  gap: var(--yg-space-1);
  width: 100%;
}

.ai-chips--dialog .ai-chips__item {
  min-height: calc(var(--yg-control-height-sm) + var(--yg-space-1));
  padding: 0 var(--yg-space-3);
  border: 1px solid var(--yg-border-default);
  border-radius: var(--yg-radius-sm);
  background: var(--yg-bg-surface);
  color: var(--yg-text-secondary);
  font: inherit;
  font-size: var(--yg-font-size-sm);
  cursor: pointer;
  transition:
    color var(--yg-dur-fast) var(--yg-ease-out),
    border-color var(--yg-dur-fast) var(--yg-ease-out),
    background-color var(--yg-dur-fast) var(--yg-ease-out);
}

.ai-chips--dialog .ai-chips__item:hover {
  color: var(--yg-text-primary);
  border-color: var(--yg-border-strong);
}

.ai-chips--dialog .ai-chips__item:active {
  background: var(--yg-bg-surface-sunken);
}

.ai-chips--dialog .ai-chips__item:focus-visible {
  outline: none;
  box-shadow: var(--yg-focus-ring);
}

.ai-chips--dialog .ai-chips__item.is-active {
  color: var(--yg-text-link);
  border-color: var(--yg-color-primary);
  background: var(--yg-color-primary-soft);
  box-shadow: inset 0 0 0 1px var(--yg-color-primary);
  font-weight: var(--yg-font-weight-medium);
}

.ai-chips--dialog .ai-chips__item:disabled {
  color: var(--yg-text-disabled);
  border-color: var(--yg-border-default);
  background: var(--yg-bg-surface-sunken);
  cursor: not-allowed;
}
</style>
